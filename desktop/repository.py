from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from metric.core import MetricSession
from parser.models import DatasetArtifact


@dataclass
class DatasetRecord:
    artifact: DatasetArtifact
    metric_session: MetricSession
    activation_id: str = ""
    background_sidecar_prebuild: dict[str, Any] = field(default_factory=dict)
    task_state_window_index: dict[str, Any] | None = None


@dataclass
class QueryCacheEntry:
    dataset_id: str
    namespace: str
    cache_key: tuple[Any, ...]
    payload: Any
    byte_size_estimate: int
    last_access_at: float
    source: str


class QueryCacheManager:
    def __init__(self, cache_budget_mb: float = 8.0) -> None:
        self.cache_budget_bytes = max(int(float(cache_budget_mb) * 1024 * 1024), 0)
        self._entries: dict[tuple[str, str, tuple[Any, ...]], QueryCacheEntry] = {}
        self.eviction_count = 0

    def _entry_id(self, dataset_id: str, namespace: str, cache_key: tuple[Any, ...]) -> tuple[str, str, tuple[Any, ...]]:
        return (dataset_id, namespace, cache_key)

    def configure(self, cache_budget_mb: float) -> list[QueryCacheEntry]:
        self.cache_budget_bytes = max(int(float(cache_budget_mb) * 1024 * 1024), 0)
        evicted = self._evict_if_needed()
        self.eviction_count += len(evicted)
        return evicted

    def clear_dataset(self, dataset_id: str) -> None:
        doomed = [entry_id for entry_id, entry in self._entries.items() if entry.dataset_id == dataset_id]
        for entry_id in doomed:
            self._entries.pop(entry_id, None)

    def get(self, dataset_id: str, namespace: str, cache_key: tuple[Any, ...]) -> QueryCacheEntry | None:
        entry = self._entries.get(self._entry_id(dataset_id, namespace, cache_key))
        if entry is None:
            return None
        entry.last_access_at = time.time()
        return entry

    def put(
        self,
        dataset_id: str,
        namespace: str,
        cache_key: tuple[Any, ...],
        payload: Any,
        *,
        byte_size_estimate: int,
        source: str,
    ) -> list[QueryCacheEntry]:
        self._entries[self._entry_id(dataset_id, namespace, cache_key)] = QueryCacheEntry(
            dataset_id=dataset_id,
            namespace=namespace,
            cache_key=cache_key,
            payload=payload,
            byte_size_estimate=max(int(byte_size_estimate), 0),
            last_access_at=time.time(),
            source=source,
        )
        evicted = self._evict_if_needed()
        self.eviction_count += len(evicted)
        return evicted

    def keys(self, dataset_id: str, namespace: str | None = None) -> list[tuple[Any, ...]]:
        keys: list[tuple[Any, ...]] = []
        for entry in self._entries.values():
            if entry.dataset_id != dataset_id:
                continue
            if namespace is not None and entry.namespace != namespace:
                continue
            keys.append(entry.cache_key)
        return keys

    def stats(self, dataset_id: str | None = None) -> dict[str, Any]:
        entries = [
            entry
            for entry in self._entries.values()
            if dataset_id is None or entry.dataset_id == dataset_id
        ]
        namespaces: dict[str, dict[str, int]] = {}
        total_bytes = 0
        for entry in entries:
            total_bytes += entry.byte_size_estimate
            bucket = namespaces.setdefault(entry.namespace, {"entry_count": 0, "byte_size_estimate": 0})
            bucket["entry_count"] += 1
            bucket["byte_size_estimate"] += entry.byte_size_estimate
        return {
            "budget_bytes": self.cache_budget_bytes,
            "entry_count": len(entries),
            "total_bytes": total_bytes,
            "eviction_count": self.eviction_count,
            "namespaces": namespaces,
        }

    def _evict_if_needed(self) -> list[QueryCacheEntry]:
        evicted: list[QueryCacheEntry] = []
        total_bytes = sum(entry.byte_size_estimate for entry in self._entries.values())
        if total_bytes <= self.cache_budget_bytes:
            return evicted
        for entry_id, entry in sorted(self._entries.items(), key=lambda item: item[1].last_access_at):
            if total_bytes <= self.cache_budget_bytes:
                break
            total_bytes -= entry.byte_size_estimate
            evicted.append(entry)
            self._entries.pop(entry_id, None)
        return evicted


class DatasetRepository:
    def __init__(self, cache_budget_mb: float = 8.0) -> None:
        self._records: dict[str, DatasetRecord] = {}
        self.query_cache = QueryCacheManager(cache_budget_mb)

    def add(self, record: DatasetRecord) -> str:
        self.query_cache.clear_dataset(record.artifact.dataset_id)
        self._records[record.artifact.dataset_id] = record
        return record.artifact.dataset_id

    def get(self, dataset_id: str) -> DatasetRecord:
        return self._records[dataset_id]

    def has(self, dataset_id: str) -> bool:
        return dataset_id in self._records

    def list_ids(self) -> list[str]:
        return list(self._records)

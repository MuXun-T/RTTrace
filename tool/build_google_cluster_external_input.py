from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.codec import (  # noqa: E402
    CHUNK_HEADER_STRUCT,
    EVENT_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_CHUNK_MAGIC,
    TRACE_FORMAT_MAGIC,
    TRACE_SEGMENT_META_MAGIC,
    _encode_payload,
)
from spec.events import event_id_for  # noqa: E402
from spec.schema_loader import load_dictionary  # noqa: E402

DEFAULT_TARGET_SIZE_BYTES = 1024 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 2048
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_CELL = "a"
DEFAULT_MAX_SHARDS = 32
DEFAULT_OPAQUE_FILLER_CHUNK_BYTES = 64 * 1024 * 1024
DEFAULT_PRODUCER_VER = "google-clusterdata-v3-instance-events"
INT64_MAX = (1 << 63) - 1
MAX_CORE_ID = 0xFFFF
MAX_TASK_ID = 0xFFFF_FFFF
RAW_EXTERNAL_RECORD_EVENT_ID = 0x9001

# Source enum values are documented by the upstream schema:
# https://protodoc.io/google/cluster-data/google.cluster_data#google.cluster_data.InstanceEvent.InstanceEventType
INSTANCE_EVENT_TYPE_NAMES = {
    0: "SUBMIT",
    1: "QUEUE",
    2: "ENABLE",
    3: "SCHEDULE",
    4: "EVICT",
    5: "FAIL",
    6: "FINISH",
    7: "KILL",
    8: "LOST",
    9: "UPDATE_PENDING",
    10: "UPDATE_RUNNING",
}

SOURCE_URL_ROOTS = [
    "https://research.google/resources/datasets/cluster-workload-traces/",
    "https://raw.githubusercontent.com/google/cluster-data/master/README.md",
    "https://groups.google.com/d/msgid/googleclusterdata-discuss/73710d9a-0ad4-48ee-a094-be51d2cb9f1cn%40googlegroups.com",
    "https://protodoc.io/google/cluster-data/google.cluster_data",
]


@dataclass
class PendingChunk:
    core_id: int
    record_count: int = 0
    seq_start: int = 0
    seq_end: int = 0
    ts_start: int = 0
    ts_end: int = 0
    payload: bytearray | None = None

    def append(self, *, seq: int, timestamp: int, event_bytes: bytes) -> None:
        if self.payload is None:
            self.payload = bytearray()
        if self.record_count == 0:
            self.seq_start = int(seq)
            self.ts_start = int(timestamp)
        self.record_count += 1
        self.seq_end = int(seq)
        self.ts_end = int(timestamp)
        self.payload.extend(event_bytes)

    @property
    def chunk_bytes(self) -> bytes:
        if self.payload is None:
            return b""
        return bytes(self.payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_http_source(url: str, cache_dir: Path, timeout_s: float) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    basename = Path(urllib.parse.urlparse(url).path).name or "download.json.gz"
    destination = cache_dir / basename
    if destination.exists() and destination.is_file() and destination.stat().st_size > 0:
        return destination
    with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=basename + ".", suffix=".tmp", delete=False) as handle:
        temp_path = Path(handle.name)
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            shutil.copyfileobj(response, handle)
    temp_path.replace(destination)
    return destination


def _resolve_source(url: str, cache_dir: Path, timeout_s: float) -> Path:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {"", "file"}:
        if parsed.scheme == "file":
            resolved = Path(urllib.request.url2pathname(parsed.path)).expanduser().resolve()
        else:
            resolved = Path(url).expanduser().resolve()
        if not resolved.exists():
            raise SystemExit(f"source file not found: {resolved}")
        return resolved
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit(f"unsupported source URL scheme: {url}")
    return _download_http_source(url, cache_dir=cache_dir, timeout_s=timeout_s)


def _generated_source_urls(*, cell: str, shard_start: int, max_shards: int) -> list[str]:
    urls = []
    for shard_index in range(int(shard_start), int(shard_start) + int(max_shards)):
        urls.append(
            f"https://storage.googleapis.com/clusterdata_2019_{cell}/instance_events-{shard_index:012d}.json.gz"
        )
    return urls


def _queue_obj_id(collection_id: int) -> int:
    return int(collection_id) & 0x7FFF_FFFF_FFFF_FFFF


def _clamp_prio(value: object) -> int:
    priority = int(value or 0)
    return max(min(priority, 0x7FFF), -0x8000)


def _stable_task_key(record: dict[str, Any]) -> tuple[int, int]:
    return (int(record["collection_id"]), int(record["instance_index"]))


class GoogleClusterTraceBuilder:
    def __init__(
        self,
        *,
        output: Path,
        producer_ver: str,
        chunk_size: int,
        report: Path | None,
        target_size_bytes: int,
        emit_raw_record_event: bool,
        core_bucket_count: int | None,
    ) -> None:
        self.output = output
        self.producer_ver = producer_ver
        self.chunk_size = int(chunk_size)
        self.report = report
        self.target_size_bytes = int(target_size_bytes)
        self.emit_raw_record_event = bool(emit_raw_record_event)
        self.core_bucket_count = int(core_bucket_count) if core_bucket_count is not None else None
        dictionary = load_dictionary()
        self.dict_ver = int(dictionary["dict_ver"])

        self.seq_by_core: dict[int, int] = defaultdict(int)
        self.pending_chunks: dict[int, PendingChunk] = {}
        self.task_id_map: dict[tuple[int, int], int] = {}
        self.next_task_id = 1
        self.core_id_map: dict[int, int] = {}
        self.next_core_id = 1
        self.running_by_core: dict[int, int] = {}
        self.task_running_core: dict[int, int] = {}
        self.task_seen: set[int] = set()
        self.observed_core_ids: set[int] = set()

        self.source_record_count = 0
        self.source_type_counts: Counter[str] = Counter()
        self.missing_type_counts: Counter[str] = Counter()
        self.emitted_event_counts: Counter[str] = Counter()
        self.ignored_source_types: Counter[str] = Counter()
        self.latest_timestamp = 0
        self.opaque_filler_chunk_count = 0
        self.opaque_filler_bytes = 0
        self.timestamp_sanitized_count = 0

        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.output.open("wb")
        self.handle.write(
            GLOBAL_HEADER_STRUCT.pack(
                TRACE_FORMAT_MAGIC,
                1,
                1,
                2,
                self.dict_ver,
                2,
                1,
                b"steady_clock".ljust(16, b"\0"),
                self.producer_ver.encode("utf-8")[:32].ljust(32, b"\0"),
                self.output.stem.encode("utf-8")[:32].ljust(32, b"\0"),
            )
        )
        self.handle.write(
            SEGMENT_META_STRUCT.pack(
                TRACE_SEGMENT_META_MAGIC,
                2,
                SEGMENT_META_STRUCT.size,
                1,
                0,
                self.dict_ver,
                0,
                0,
            )
        )

    def _task_id(self, record: dict[str, Any]) -> int:
        key = _stable_task_key(record)
        mapped = self.task_id_map.get(key)
        if mapped is not None:
            return mapped
        if self.next_task_id > MAX_TASK_ID:
            raise SystemExit("task id space exhausted while converting external input")
        mapped = self.next_task_id
        self.next_task_id += 1
        self.task_id_map[key] = mapped
        return mapped

    def _core_id(self, machine_id: object | None) -> int:
        if machine_id in {None, "", "0", 0}:
            return 0
        machine_key = int(machine_id)
        if self.core_bucket_count is not None and self.core_bucket_count > 0:
            bucketed = 1 + (abs(machine_key) % self.core_bucket_count)
            self.observed_core_ids.add(int(bucketed))
            return bucketed
        mapped = self.core_id_map.get(machine_key)
        if mapped is not None:
            return mapped
        if self.next_core_id > MAX_CORE_ID:
            raise SystemExit("core id space exhausted while converting external input")
        mapped = self.next_core_id
        self.next_core_id += 1
        self.core_id_map[machine_key] = mapped
        self.observed_core_ids.add(int(mapped))
        return mapped

    def _flush_core(self, core_id: int) -> None:
        chunk = self.pending_chunks.get(core_id)
        if chunk is None or chunk.record_count <= 0:
            return
        payload = chunk.chunk_bytes
        self.handle.write(
            CHUNK_HEADER_STRUCT.pack(
                TRACE_CHUNK_MAGIC,
                2,
                int(core_id),
                int(chunk.record_count),
                len(payload),
                int(chunk.ts_start),
                int(chunk.ts_end),
                int(chunk.seq_start),
                int(chunk.seq_end),
                self.dict_ver,
                zlib.crc32(payload) & 0xFFFFFFFF,
            )
        )
        self.handle.write(payload)
        self.pending_chunks.pop(core_id, None)

    def flush_pending(self) -> None:
        for core_id in sorted(self.pending_chunks):
            self._flush_core(core_id)
        self.handle.flush()

    def estimated_output_size(self) -> int:
        pending_bytes = 0
        for chunk in self.pending_chunks.values():
            if chunk.record_count <= 0:
                continue
            pending_bytes += CHUNK_HEADER_STRUCT.size + len(chunk.chunk_bytes)
        return int(self.output.stat().st_size) + pending_bytes

    def append_opaque_chunk(self, payload: bytes, *, core_id: int = 0) -> None:
        if not payload:
            return
        self.flush_pending()
        ts_start = int(self.latest_timestamp)
        ts_end = int(ts_start + 1)
        self.handle.write(
            CHUNK_HEADER_STRUCT.pack(
                TRACE_CHUNK_MAGIC,
                2,
                int(core_id),
                0,
                len(payload),
                ts_start,
                ts_end,
                1,
                1,
                self.dict_ver,
                zlib.crc32(payload) & 0xFFFFFFFF,
            )
        )
        self.handle.write(payload)
        self.handle.flush()
        self.latest_timestamp = ts_end
        self.opaque_filler_chunk_count += 1
        self.opaque_filler_bytes += len(payload)

    def _sanitize_timestamp(self, raw_timestamp: object) -> int:
        timestamp = int(raw_timestamp)
        if timestamp >= INT64_MAX:
            fallback = int(self.latest_timestamp) + 1 if self.latest_timestamp < INT64_MAX else INT64_MAX - 1
            self.timestamp_sanitized_count += 1
            return fallback
        return timestamp

    def _emit(self, *, event_name: str, timestamp: int, core_id: int, payload: dict[str, Any]) -> None:
        event_id = int(event_id_for(event_name))
        self._emit_by_id(
            event_id=event_id,
            event_label=event_name,
            timestamp=timestamp,
            core_id=core_id,
            payload=payload,
        )

    def _emit_by_id(
        self,
        *,
        event_id: int,
        event_label: str,
        timestamp: int,
        core_id: int,
        payload: dict[str, Any],
    ) -> None:
        sequence = int(self.seq_by_core[core_id]) + 1
        self.seq_by_core[core_id] = sequence
        pending = self.pending_chunks.get(core_id)
        if pending is not None and pending.record_count >= self.chunk_size:
            self._flush_core(core_id)
            pending = None
        if pending is None:
            pending = PendingChunk(core_id=int(core_id))
            self.pending_chunks[core_id] = pending
        payload_bytes = _encode_payload(event_id, payload)
        event_bytes = EVENT_HEADER_STRUCT.pack(
            1,
            0,
            int(core_id),
            event_id,
            sequence,
            int(timestamp),
            len(payload_bytes),
        ) + payload_bytes
        pending.append(seq=sequence, timestamp=int(timestamp), event_bytes=event_bytes)
        self.emitted_event_counts[event_label] += 1

    def _switch_out(self, *, task_id: int, timestamp: int, reason: int) -> None:
        active_core = self.task_running_core.pop(task_id, None)
        if active_core is None:
            return
        current_task = self.running_by_core.get(active_core)
        if current_task == task_id:
            self._emit(
                event_name="CTX_SWITCH",
                timestamp=timestamp,
                core_id=active_core,
                payload={
                    "core_id": active_core,
                    "prev_task_id": task_id,
                    "next_task_id": 0,
                    "reason": reason,
                },
            )
            self.running_by_core.pop(active_core, None)

    def process_record(self, record: dict[str, Any]) -> None:
        source_type = int(record["type"])
        source_name = INSTANCE_EVENT_TYPE_NAMES.get(source_type, f"UNKNOWN_{source_type}")
        self.source_record_count += 1
        self.source_type_counts[source_name] += 1
        if "missing_type" in record:
            self.missing_type_counts[str(record["missing_type"])] += 1

        task_id = self._task_id(record)
        timestamp = self._sanitize_timestamp(record["time"])
        self.latest_timestamp = max(self.latest_timestamp, timestamp)
        machine_core = self._core_id(record.get("machine_id"))
        priority = _clamp_prio(record.get("priority"))
        queue_obj_id = _queue_obj_id(int(record["collection_id"]))
        ready_reason = 1 if task_id not in self.task_seen else 2
        self.task_seen.add(task_id)

        if self.emit_raw_record_event:
            self._emit_by_id(
                event_id=RAW_EXTERNAL_RECORD_EVENT_ID,
                event_label="RAW_EXTERNAL_RECORD",
                timestamp=timestamp,
                core_id=machine_core,
                payload=dict(record),
            )

        if source_type == 0:  # SUBMIT
            self._emit(
                event_name="TASK_READY",
                timestamp=timestamp,
                core_id=0,
                payload={
                    "task_id": task_id,
                    "prio": priority,
                    "core_hint": machine_core,
                    "reason": ready_reason,
                },
            )
            return

        if source_type == 1:  # QUEUE
            self._switch_out(task_id=task_id, timestamp=timestamp, reason=3)
            self._emit(
                event_name="SYNC_TRY",
                timestamp=timestamp,
                core_id=0,
                payload={
                    "task_id": task_id,
                    "obj_id": queue_obj_id,
                    "obj_type": 3,
                    "timeout_ns": 0,
                },
            )
            self._emit(
                event_name="TASK_BLOCK",
                timestamp=timestamp,
                core_id=0,
                payload={
                    "task_id": task_id,
                    "wait_obj_id": queue_obj_id,
                    "reason": 5,
                    "owner_task_id": 0,
                },
            )
            return

        if source_type == 2:  # ENABLE
            self._emit(
                event_name="TASK_WAKEUP",
                timestamp=timestamp,
                core_id=0,
                payload={
                    "task_id": task_id,
                    "wake_src": 3,
                    "obj_id": queue_obj_id,
                },
            )
            return

        if source_type == 3:  # SCHEDULE
            target_core = machine_core
            if target_core == 0:
                target_core = self.task_running_core.get(task_id, 0)
            if target_core == 0:
                target_core = 1
            previous_core = self.task_running_core.get(task_id)
            if previous_core is not None and previous_core != target_core:
                self._switch_out(task_id=task_id, timestamp=timestamp, reason=2)
            previous_task = self.running_by_core.get(target_core, 0)
            self._emit(
                event_name="TASK_DISPATCH",
                timestamp=timestamp,
                core_id=target_core,
                payload={
                    "task_id": task_id,
                    "core_id": target_core,
                    "prio": priority,
                    "reason": 1,
                },
            )
            if previous_task != task_id:
                self._emit(
                    event_name="CTX_SWITCH",
                    timestamp=timestamp,
                    core_id=target_core,
                    payload={
                        "core_id": target_core,
                        "prev_task_id": previous_task,
                        "next_task_id": task_id,
                        "reason": 1 if previous_task == 0 else 2,
                    },
                )
            self.running_by_core[target_core] = task_id
            self.task_running_core[task_id] = target_core
            return

        if source_type == 4:  # EVICT
            self._switch_out(task_id=task_id, timestamp=timestamp, reason=3)
            self._emit(
                event_name="TASK_BLOCK",
                timestamp=timestamp,
                core_id=0,
                payload={
                    "task_id": task_id,
                    "wait_obj_id": queue_obj_id,
                    "reason": 2,
                    "owner_task_id": 0,
                },
            )
            return

        if source_type in {5, 6, 7, 8}:  # FAIL, FINISH, KILL, LOST
            self._switch_out(task_id=task_id, timestamp=timestamp, reason=6)
            exit_code = {5: 1, 6: 0, 7: 137, 8: 2}[source_type]
            self._emit(
                event_name="TASK_EXIT",
                timestamp=timestamp,
                core_id=machine_core,
                payload={
                    "task_id": task_id,
                    "exit_code": exit_code,
                },
            )
            return

        if source_type == 9:  # UPDATE_PENDING
            self._emit(
                event_name="TASK_READY",
                timestamp=timestamp,
                core_id=0,
                payload={
                    "task_id": task_id,
                    "prio": priority,
                    "core_hint": machine_core,
                    "reason": 2,
                },
            )
            return

        if source_type == 10:  # UPDATE_RUNNING
            running_core = self.task_running_core.get(task_id, machine_core)
            if running_core == 0:
                running_core = 1
            self._emit(
                event_name="TASK_DISPATCH",
                timestamp=timestamp,
                core_id=running_core,
                payload={
                    "task_id": task_id,
                    "core_id": running_core,
                    "prio": priority,
                    "reason": 1,
                },
            )
            return

        self.ignored_source_types[source_name] += 1

    def finalize(self) -> None:
        self.flush_pending()
        self.handle.close()

    def manifest(
        self,
        *,
        source_shards: list[dict[str, Any]],
    ) -> dict[str, Any]:
        output_size_bytes = int(self.output.stat().st_size)
        payload = {
            "sample_profile": "google_cluster_real_external_formal_input",
            "source": {
                "dataset": "Google cluster workload traces v3 / clusterdata_2019 / instance_events",
                "schema_package": "google.cluster_data.InstanceEvent",
                "official_urls": SOURCE_URL_ROOTS,
                "event_type_map": {str(key): value for key, value in INSTANCE_EVENT_TYPE_NAMES.items()},
            },
            "source_shards": source_shards,
            "conversion": {
                "producer_ver": self.producer_ver,
                "chunk_size": self.chunk_size,
                "emit_raw_record_event": self.emit_raw_record_event,
                "raw_record_event_id": RAW_EXTERNAL_RECORD_EVENT_ID if self.emit_raw_record_event else None,
                "source_record_count": int(self.source_record_count),
                "source_type_counts": dict(self.source_type_counts),
                "missing_type_counts": dict(self.missing_type_counts),
                "emitted_event_counts": dict(self.emitted_event_counts),
                "ignored_source_types": dict(self.ignored_source_types),
                "task_count": len(self.task_id_map),
                "core_count": len(self.observed_core_ids),
                "core_bucket_count": self.core_bucket_count,
                "machine_core_policy": (
                    "bucketed_modulo"
                    if self.core_bucket_count is not None and self.core_bucket_count > 0
                    else "one_to_one_stable_mapping"
                ),
                "opaque_filler_chunk_count": int(self.opaque_filler_chunk_count),
                "opaque_filler_bytes": int(self.opaque_filler_bytes),
                "timestamp_sanitized_count": int(self.timestamp_sanitized_count),
            },
            "output": {
                "path": str(self.output),
                "output_size_bytes": output_size_bytes,
                "sha256": _sha256(self.output),
                "formal_size_ready": output_size_bytes >= self.target_size_bytes,
                "expected_input_provenance": "real_external",
                "baseline_candidate_recommendation": "use_same_file_for_perf_only",
            },
        }
        return payload


def build_google_cluster_external_input(
    *,
    output: Path,
    cache_dir: Path,
    report: Path | None,
    source_urls: Iterable[str],
    target_size_bytes: int,
    chunk_size: int,
    producer_ver: str,
    timeout_s: float,
    allow_short_output: bool,
    max_source_records: int | None,
    emit_raw_record_event: bool,
    progress_record_interval: int,
    opaque_filler_from_source: bool,
    opaque_filler_chunk_bytes: int,
    allow_repeat_opaque_source: bool,
    stop_on_target_size_reached: bool,
    core_bucket_count: int | None,
) -> dict[str, Any]:
    builder = GoogleClusterTraceBuilder(
        output=output,
        producer_ver=producer_ver,
        chunk_size=chunk_size,
        report=report,
        target_size_bytes=target_size_bytes,
        emit_raw_record_event=emit_raw_record_event,
        core_bucket_count=core_bucket_count,
    )
    source_shards: list[dict[str, Any]] = []
    resolved_sources: list[Path] = []
    semantic_limit_reached = False
    target_size_reached = False
    opaque_filler_meta = {
        "enabled": bool(opaque_filler_from_source),
        "chunk_bytes": int(opaque_filler_chunk_bytes),
        "repeat_source_bytes": bool(allow_repeat_opaque_source),
        "source_cycles": 0,
        "source_bytes_consumed": 0,
        "chunks_appended": 0,
    }
    try:
        for source_index, source_url in enumerate(source_urls):
            resolved_source = _resolve_source(source_url, cache_dir=cache_dir, timeout_s=timeout_s)
            resolved_sources.append(resolved_source)
            shard_meta = {
                "source_index": int(source_index),
                "url": source_url,
                "local_path": str(resolved_source),
                "download_size_bytes": int(resolved_source.stat().st_size),
                "sha256": _sha256(resolved_source),
            }
            lines_in_shard = 0
            if not semantic_limit_reached:
                with gzip.open(resolved_source, "rt", encoding="utf-8") as handle:
                    for raw_line in handle:
                        text = raw_line.strip()
                        if not text:
                            continue
                        builder.process_record(json.loads(text))
                        lines_in_shard += 1
                        if progress_record_interval > 0 and builder.source_record_count % int(progress_record_interval) == 0:
                            print(
                                json.dumps(
                                    {
                                        "stage": "processing",
                                        "source_url": source_url,
                                        "cumulative_source_records": builder.source_record_count,
                                        "output_size_bytes": builder.estimated_output_size(),
                                    },
                                    ensure_ascii=False,
                                )
                            )
                        if (
                            stop_on_target_size_reached
                            and not opaque_filler_from_source
                            and builder.estimated_output_size() >= int(target_size_bytes)
                        ):
                            target_size_reached = True
                            break
                        if max_source_records is not None and builder.source_record_count >= int(max_source_records):
                            semantic_limit_reached = True
                            break
            shard_meta["record_count"] = int(lines_in_shard)
            shard_meta["semantic_limit_reached"] = bool(semantic_limit_reached and lines_in_shard > 0)
            shard_meta["semantic_processing_skipped"] = bool(semantic_limit_reached and lines_in_shard == 0)
            shard_meta["target_size_reached"] = bool(target_size_reached and lines_in_shard > 0)
            source_shards.append(shard_meta)
            current_size = builder.estimated_output_size()
            print(
                json.dumps(
                    {
                        "stage": "processed_shard",
                        "source_url": source_url,
                        "record_count": lines_in_shard,
                        "cumulative_source_records": builder.source_record_count,
                        "output_size_bytes": current_size,
                        "target_size_reached": bool(target_size_reached),
                    },
                    ensure_ascii=False,
                )
            )
            if target_size_reached and not opaque_filler_from_source:
                break
            if not opaque_filler_from_source and current_size >= int(target_size_bytes):
                break
            if semantic_limit_reached and not opaque_filler_from_source:
                break
        if opaque_filler_from_source:
            builder.flush_pending()
            current_size = int(builder.output.stat().st_size)
            while current_size < int(target_size_bytes):
                if not resolved_sources:
                    break
                opaque_filler_meta["source_cycles"] = int(opaque_filler_meta["source_cycles"]) + 1
                appended_this_cycle = False
                for resolved_source in resolved_sources:
                    with resolved_source.open("rb") as handle:
                        while current_size < int(target_size_bytes):
                            remaining = int(target_size_bytes) - current_size
                            read_size = min(int(opaque_filler_chunk_bytes), remaining)
                            payload = handle.read(read_size)
                            if not payload:
                                break
                            builder.append_opaque_chunk(payload)
                            opaque_filler_meta["source_bytes_consumed"] = (
                                int(opaque_filler_meta["source_bytes_consumed"]) + len(payload)
                            )
                            opaque_filler_meta["chunks_appended"] = int(opaque_filler_meta["chunks_appended"]) + 1
                            current_size = int(builder.output.stat().st_size)
                            appended_this_cycle = True
                            print(
                                json.dumps(
                                    {
                                        "stage": "opaque_filler_progress",
                                        "source_path": str(resolved_source),
                                        "output_size_bytes": current_size,
                                        "chunks_appended": int(opaque_filler_meta["chunks_appended"]),
                                    },
                                    ensure_ascii=False,
                                )
                            )
                            if current_size >= int(target_size_bytes):
                                break
                if current_size >= int(target_size_bytes):
                    break
                if not appended_this_cycle or not allow_repeat_opaque_source:
                    break
    finally:
        builder.finalize()

    manifest = builder.manifest(source_shards=source_shards)
    manifest["conversion"]["opaque_external_filler"] = dict(opaque_filler_meta)
    manifest["conversion"]["stop_on_target_size_reached"] = bool(stop_on_target_size_reached)
    manifest["conversion"]["target_size_reached_during_semantic_conversion"] = bool(target_size_reached)
    manifest["output"]["stop_reason"] = "target_size_reached" if target_size_reached else None
    output_size_bytes = int(manifest["output"]["output_size_bytes"])
    if output_size_bytes < int(target_size_bytes) and not allow_short_output:
        raise SystemExit(
            f"converted trace is smaller than target_size_bytes: {output_size_bytes} < {int(target_size_bytes)}"
        )
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_google_cluster_external_input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--report")
    parser.add_argument("--source-url", action="append", default=[])
    parser.add_argument("--cell", default=DEFAULT_CELL)
    parser.add_argument("--shard-start", type=int, default=0)
    parser.add_argument("--max-shards", type=int, default=DEFAULT_MAX_SHARDS)
    parser.add_argument("--target-size-bytes", type=int, default=DEFAULT_TARGET_SIZE_BYTES)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--producer-ver", default=DEFAULT_PRODUCER_VER)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--allow-short-output", action="store_true")
    parser.add_argument("--max-source-records", type=int)
    parser.add_argument("--no-raw-record-event", action="store_true")
    parser.add_argument("--opaque-filler-from-source", action="store_true")
    parser.add_argument("--opaque-filler-chunk-bytes", type=int, default=DEFAULT_OPAQUE_FILLER_CHUNK_BYTES)
    parser.add_argument("--allow-repeat-opaque-source", action="store_true")
    parser.add_argument("--progress-record-interval", type=int, default=100000)
    parser.add_argument("--stop-on-target-size-reached", action="store_true")
    parser.add_argument("--core-bucket-count", type=int)
    args = parser.parse_args(argv)

    explicit_urls = [str(item) for item in args.source_url if str(item).strip()]
    source_urls = (
        explicit_urls
        if explicit_urls
        else _generated_source_urls(cell=str(args.cell), shard_start=int(args.shard_start), max_shards=int(args.max_shards))
    )
    manifest = build_google_cluster_external_input(
        output=Path(args.output).expanduser().resolve(),
        cache_dir=Path(args.cache_dir).expanduser().resolve(),
        report=Path(args.report).expanduser().resolve() if args.report else None,
        source_urls=source_urls,
        target_size_bytes=int(args.target_size_bytes),
        chunk_size=int(args.chunk_size),
        producer_ver=str(args.producer_ver),
        timeout_s=float(args.timeout_s),
        allow_short_output=bool(args.allow_short_output),
        max_source_records=int(args.max_source_records) if args.max_source_records is not None else None,
        emit_raw_record_event=not bool(args.no_raw_record_event),
        progress_record_interval=int(args.progress_record_interval),
        opaque_filler_from_source=bool(args.opaque_filler_from_source),
        opaque_filler_chunk_bytes=int(args.opaque_filler_chunk_bytes),
        allow_repeat_opaque_source=bool(args.allow_repeat_opaque_source),
        stop_on_target_size_reached=bool(args.stop_on_target_size_reached),
        core_bucket_count=int(args.core_bucket_count) if args.core_bucket_count is not None else None,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

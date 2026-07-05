from __future__ import annotations

import copy
import concurrent.futures
import csv
import heapq
import json
import math
import shutil
import struct
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from collector.channel import ChannelError, FileChunkSource, SerialChunkSource, SocketChunkSource
from metric.core import (
    MetricSession,
    alert_Evaluate,
    diag_Generate,
    diag_PayloadFromAlert,
    metric_Compare,
    metric_Compute,
    metric_Ingest,
    metric_Init,
)
from parser.align import ALIGNMENT_EVENT_NAMES
from parser import (
    encode_trace,
    load_dataset,
    load_dataset_from_chunks,
    prs_Prescan,
    query_events,
    query_timeline_buckets,
)
from parser.codec import (
    CHUNK_HEADER_STRUCT,
    EVENT_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_CHUNK_MAGIC,
    TRACE_FORMAT_MAGIC,
    TraceDecodeSession,
    _chunk_header,
    _encode_payload,
)
from parser.index import build_task_state_window_index, query_events_source_backed, query_task_state_segments
from parser.evidence_models import EvidenceExportRequest, evd_DeriveStableSnapshotId
from parser.evidence_sidecar import build_dependency_sidecar, materialize_dependency_sidecar, validate_sidecar_payload
from parser.parser_process_agent import ParserProcessAgent, build_parser_cache_bindings
from parser.runtime_advisor import RUNTIME_LOAD_PLAN_VERSION, RuntimeLoadPlan, RuntimeOptimizationAdvisor
from parser.runtime_optimization_gate import gate_ValidateRuntimeLoadPlan
from parser.sidecar_index_agent import SidecarIndexAgent
from parser.models import (
    Alert,
    AlignmentSummary,
    AnalysisContext,
    Bookmark,
    CompareScope,
    DecodedEvent,
    DatasetArtifact,
    DiffBundle,
    DiffDetail,
    EvidenceRef,
    EventCursor,
    EventPage,
    EventTableQuery,
    ExecSlice,
    GlobalHeader,
    IrqSpan,
    LoadPreview,
    MetricResult,
    PlaybackState,
    ReadinessState,
    RebuildBundle,
    ResourceGraph,
    SegmentMeta,
    SwitchPoint,
    TaskStateQuery,
    TaskStateSeg,
    TaskStateViewRow,
    TaskStateViewSegment,
    TaskStateViewModel,
    TimelinePayload,
    UnifiedEvent,
    UntrustedWindow,
    dataclass_to_dict,
    event_ref_key_for,
)
from parser.result import Result, err_result, ok_result
from spec.io import checksum_file, json_dump, json_load, jsonl_dump, serialize
from spec.schema_loader import DICTIONARY_PATH, SCHEMA_DIR, load_specs

from .context import ContextStore
from .evidence_export import (
    EVIDENCE_PACKAGE_VERSION,
    EVIDENCE_PARSER_VERSION,
    _build_analysis_context,
    _source_backed_ref_index_rows,
    write_evidence_package,
)
from .repro_evidence import load_evidence_control, rpr_BuildResultValidityIndex
from .repository import DatasetRecord, DatasetRepository


PACKAGE_VERSION = "rttrace-package-1"
SUPPORTED_PACKAGE_VERSIONS = {PACKAGE_VERSION, EVIDENCE_PACKAGE_VERSION}
PARSER_VERSION = "parser-mvp-1"
PARSER_CACHE_SCHEMA_VERSION = "parser-process-artifact-v1"
TIME_UNIT_LABELS = {1: "ns"}
CLOCK_SOURCE_LABELS = {1: "steady_clock"}
REFERENCE_SCHEMA_NAMES = [
    "package.schema.json",
    "meta.schema.json",
    "manifest.schema.json",
    "analysis_context.schema.json",
    "compare_scope.schema.json",
]
VALID_DATASET_ROLES = {"single", "baseline", "candidate"}
_ALIGNED_TS_STRUCT = struct.Struct("<q")
_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
SIDECAR_SCHEMA_NAMES = {
    "dependency_sidecar_schema": "dependency_sidecar.schema.json",
    "frontier_snapshot_schema": "frontier_snapshot.schema.json",
    "frontier_refs_schema": "frontier_refs.schema.json",
    "proof_digest_schema": "proof_digest.schema.json",
    "sidecar_manifest_schema": "sidecar_manifest.schema.json",
    "blocker_artifact_schema": "blocker_artifact.schema.json",
}
PATENT_JOB_CONTRACT_VERSION = "patent-job-v1"
PATENT_JOB_KIND_LANES = {
    "sidecar_build": "sidecar_build",
    "export_evidence": "evidence_export",
    "evidence_query_proof": "evidence_query",
    "evidence_query_validity": "evidence_query",
}
_PATENT_JOB_HISTORY_LIMIT = 16


def _clamp_aligned_timestamp(value: float) -> int:
    integer = int(value)
    if integer < _INT64_MIN:
        return _INT64_MIN
    if integer > _INT64_MAX:
        return _INT64_MAX
    return integer


def _resolve_evidence_trace_source_path(source: str) -> Result[Path]:
    path = Path(source)
    if path.is_file():
        return ok_result(path)
    if path.is_dir():
        package_trace = path / "event" / "events.trace"
        if package_trace.exists():
            return ok_result(package_trace)
    return err_result("INVALID_ARG", f"unable to resolve trace source path for evidence export: {source}")


def _resolve_evidence_dictionary_source_path(source: str) -> Path:
    path = Path(source)
    if path.is_dir():
        package_dictionary = path / "reference" / "dictionary.json"
        if package_dictionary.exists():
            return package_dictionary
    return DICTIONARY_PATH


@dataclass(frozen=True)
class _NormalizedTaskStateQuery:
    dataset_id: str | None
    time_window: tuple[float, float]
    lane_group: str
    state_mask: tuple[str, ...]
    task_filter: tuple[int, ...]
    anchor_ref: dict[str, Any] | None
    include_summary: bool
    runtime_filter: dict[str, Any]


@dataclass
class _ReplayRuntimeState:
    current_index: int = 0
    mode: str = "stopped"
    rate: float = 1.0


@dataclass
class _SourceBackedEventSummary:
    count: int = 0
    first_ref_key: str | None = None
    last_ref_key: str | None = None
    write_mode: str = "source_backed_streaming"
    context_switch_count: int = 0
    priority_by_task: dict[int, int] = field(default_factory=dict)
    owner_task_id_by_cause_event_ref: dict[str, int] = field(default_factory=dict)
    deadline_records: list[dict[str, Any]] = field(default_factory=list)
    deadline_candidate_count: int = 0
    deadline_incomplete_count: int = 0
    deadline_conflict_count: int = 0
    deadline_semantics_status: str = "unsupported"
    deadline_precise_record_count: int = 0
    deadline_degraded_record_count: int = 0
    deadline_supported: bool = False
    deadline_issue_samples: list[dict[str, Any]] = field(default_factory=list)
    required_event_ref_count: int = 0
    closure_forced_event_count: int = 0
    exported_ref_keys: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _CompactDeadlineEvent:
    sort_key: tuple[float, int, int]
    event_name: str
    task_id: int
    job_id: int | None
    instance_id: int | None
    timestamp_aligned: float
    release_ts: float | None
    deadline_ts: float | None
    finish_ts: float | None
    ref_key: str


class _StreamingTraceWriter:
    def __init__(
        self,
        path: Path,
        *,
        producer_ver: str,
        run_id: str,
        dict_ver: int,
        core_count: int,
        chunk_size: int = 8,
    ) -> None:
        self._path = path
        self._handle = path.open("wb")
        self._dict_ver = int(dict_ver)
        self._chunk_size = max(int(chunk_size), 1)
        self._buffers: dict[int, list[tuple[int, int, int, int, bytes]]] = {}
        self._handle.write(
            GLOBAL_HEADER_STRUCT.pack(
                TRACE_FORMAT_MAGIC,
                1,
                1,
                1,
                self._dict_ver,
                1,
                max(int(core_count), 1),
                b"steady_clock".ljust(16, b"\0"),
                producer_ver.encode("utf-8")[:32].ljust(32, b"\0"),
                run_id.encode("utf-8")[:32].ljust(32, b"\0"),
            )
        )

    def append(
        self,
        *,
        core_id: int,
        event_id: int,
        seq: int,
        timestamp_raw: int,
        flags: int,
        payload_bytes: bytes,
    ) -> None:
        buffer = self._buffers.setdefault(int(core_id), [])
        buffer.append(
            (
                int(timestamp_raw),
                int(seq),
                int(event_id),
                int(flags),
                payload_bytes if isinstance(payload_bytes, bytes) else bytes(payload_bytes),
            )
        )
        if len(buffer) >= self._chunk_size:
            self._flush_core(int(core_id))

    def _flush_core(self, core_id: int) -> None:
        buffered = self._buffers.get(core_id) or []
        if not buffered:
            return
        chunk_rows = [
            {
                "timestamp": timestamp_raw,
                "seq": seq,
            }
            for timestamp_raw, seq, _event_id, _flags, _payload_bytes in buffered
        ]
        chunk_bytes = bytearray()
        for timestamp_raw, seq, event_id, flags, payload_bytes in buffered:
            chunk_bytes.extend(
                EVENT_HEADER_STRUCT.pack(
                    1,
                    flags,
                    core_id,
                    event_id,
                    seq,
                    timestamp_raw,
                    len(payload_bytes),
                )
            )
            chunk_bytes.extend(payload_bytes)
        chunk_payload = bytes(chunk_bytes)
        self._handle.write(_chunk_header(chunk_rows, core_id=core_id, dict_ver=self._dict_ver, chunk_bytes=chunk_payload))
        self._handle.write(chunk_payload)
        self._buffers[core_id] = []

    def finalize(self) -> None:
        for core_id in sorted(self._buffers):
            self._flush_core(core_id)
        self._handle.close()


class _SourceBackedAnalysisAccumulator:
    def __init__(self, *, dataset_id: str, cause_event_refs: set[str]) -> None:
        self._dataset_id = dataset_id
        self._cause_event_refs = cause_event_refs
        self.context_switch_count = 0
        self._priority_events_by_task: dict[int, tuple[tuple[float, int, int], int]] = {}
        self.owner_task_id_by_cause_event_ref: dict[str, int] = {}
        self._deadline_events: list[_CompactDeadlineEvent] = []

    def observe(self, event: DecodedEvent, *, aligned_ts: float) -> None:
        sort_key = (float(aligned_ts), int(event.core_id), int(event.seq))
        payload = event.payload or {}
        event_ref_key = event_ref_key_for(event, self._dataset_id)

        if event.event_name == "CTX_SWITCH":
            self.context_switch_count += 1

        if payload.get("task_id") is not None and "prio" in payload:
            task_id = int(payload["task_id"])
            priority = int(payload["prio"])
            previous = self._priority_events_by_task.get(task_id)
            if previous is None or sort_key > previous[0]:
                self._priority_events_by_task[task_id] = (sort_key, priority)

        if event_ref_key in self._cause_event_refs and payload.get("owner_task_id") is not None:
            self.owner_task_id_by_cause_event_ref[event_ref_key] = int(payload["owner_task_id"])

        if event.task_id is None:
            return
        if event.event_name == "TASK_READY":
            deadline_ts = payload.get("deadline_ts", payload.get("deadline"))
            if deadline_ts is None and "release_ts" not in payload:
                return
            self._deadline_events.append(
                _CompactDeadlineEvent(
                    sort_key=sort_key,
                    event_name=event.event_name,
                    task_id=int(event.task_id),
                    job_id=event.job_id,
                    instance_id=event.instance_id,
                    timestamp_aligned=float(aligned_ts),
                    release_ts=float(payload.get("release_ts", aligned_ts)),
                    deadline_ts=float(deadline_ts) if deadline_ts is not None else None,
                    finish_ts=None,
                    ref_key=event_ref_key,
                )
            )
            return
        if event.event_name == "TASK_EXIT":
            self._deadline_events.append(
                _CompactDeadlineEvent(
                    sort_key=sort_key,
                    event_name=event.event_name,
                    task_id=int(event.task_id),
                    job_id=event.job_id,
                    instance_id=event.instance_id,
                    timestamp_aligned=float(aligned_ts),
                    release_ts=None,
                    deadline_ts=None,
                    finish_ts=float(payload.get("finish_ts", aligned_ts)),
                    ref_key=event_ref_key,
                )
            )

    def populate_summary(
        self,
        summary: _SourceBackedEventSummary,
        *,
        capability_flags: dict[str, bool] | None = None,
    ) -> None:
        deadline_analysis = _source_backed_deadline_analysis(self._deadline_events, capability_flags)
        summary.context_switch_count = int(self.context_switch_count)
        summary.priority_by_task = {
            task_id: int(priority)
            for task_id, (_sort_key, priority) in sorted(self._priority_events_by_task.items())
        }
        summary.owner_task_id_by_cause_event_ref = {
            ref_key: int(task_id)
            for ref_key, task_id in sorted(self.owner_task_id_by_cause_event_ref.items())
        }
        summary.deadline_records = list(deadline_analysis["records"])
        summary.deadline_candidate_count = int(deadline_analysis["candidate_count"])
        summary.deadline_incomplete_count = int(deadline_analysis["incomplete_count"])
        summary.deadline_conflict_count = int(deadline_analysis["conflict_count"])
        summary.deadline_semantics_status = str(deadline_analysis["semantics_status"])
        summary.deadline_precise_record_count = int(deadline_analysis["precise_record_count"])
        summary.deadline_degraded_record_count = int(deadline_analysis["degraded_record_count"])
        summary.deadline_supported = bool(deadline_analysis["supported"])
        summary.deadline_issue_samples = list(deadline_analysis["issue_samples"])


def _quantile_value(samples: list[float], ratio: float) -> float:
    ordered = sorted(float(item) for item in samples)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * float(ratio)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _distribution(samples: list[float]) -> list[dict[str, Any]]:
    if not samples:
        return []
    points = [("p50", 0.50), ("p90", 0.90), ("p95", 0.95), ("p99", 0.99)]
    return [{"quantile": label, "value": round(_quantile_value(samples, ratio), 6)} for label, ratio in points]


def _distribution_summary(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    return {
        "p50": round(_quantile_value(samples, 0.50), 6),
        "p90": round(_quantile_value(samples, 0.90), 6),
        "p95": round(_quantile_value(samples, 0.95), 6),
        "p99": round(_quantile_value(samples, 0.99), 6),
    }


def _source_backed_deadline_analysis(
    events: list[_CompactDeadlineEvent],
    capability_flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    precise_records: dict[tuple[Any, ...], dict[str, Any]] = {}
    degraded_records: dict[tuple[Any, ...], dict[str, Any]] = {}
    conflict_count = 0
    issue_samples: list[dict[str, Any]] = []

    for event in sorted(events, key=lambda item: item.sort_key):
        task_id = int(event.task_id)
        precise_key = ("instance", task_id, event.job_id, event.instance_id)
        degraded_key = ("task", task_id)

        if event.event_name == "TASK_READY":
            record = {
                "task_id": task_id,
                "job_id": event.job_id,
                "instance_id": event.instance_id,
                "release_ts": float(event.release_ts if event.release_ts is not None else event.timestamp_aligned),
                "deadline_ts": float(event.deadline_ts) if event.deadline_ts is not None else None,
                "finish_ts": None,
                "release_ref_key": event.ref_key,
                "finish_ref_key": None,
                "semantics": "precise" if (event.job_id is not None or event.instance_id is not None) else "degraded",
            }
            if record["semantics"] == "precise":
                precise_records[precise_key] = record
            else:
                existing = degraded_records.get(degraded_key)
                if existing is not None and existing["finish_ts"] is None:
                    conflict_count += 1
                    issue_samples.append(
                        {
                            "issue_type": "conflict",
                            **record,
                        }
                    )
                    continue
                degraded_records[degraded_key] = record
            continue

        if event.event_name != "TASK_EXIT":
            continue

        if event.job_id is not None or event.instance_id is not None:
            record = precise_records.get(precise_key)
        else:
            record = degraded_records.get(degraded_key)
        if record is None or record["finish_ts"] is not None:
            continue
        record["finish_ts"] = float(event.finish_ts if event.finish_ts is not None else event.timestamp_aligned)
        record["finish_ref_key"] = event.ref_key

    candidates = list(precise_records.values()) + list(degraded_records.values())
    completed: list[dict[str, Any]] = []
    incomplete_count = 0
    for record in candidates:
        if record["deadline_ts"] is None or record["finish_ts"] is None:
            incomplete_count += 1
            issue_samples.append(
                {
                    "issue_type": "incomplete",
                    **record,
                }
            )
            continue
        response_time = max(0.0, float(record["finish_ts"]) - float(record["release_ts"]))
        deadline_miss = max(0.0, float(record["finish_ts"]) - float(record["deadline_ts"]))
        completed.append(
            {
                **record,
                "response_time": response_time,
                "deadline_miss": deadline_miss,
            }
        )

    if completed:
        semantics_status = "precise"
        if incomplete_count or conflict_count or any(item["semantics"] != "precise" for item in completed):
            semantics_status = "degraded"
    else:
        semantics_status = "unsupported"
        if incomplete_count or conflict_count or (capability_flags or {}).get("deadline_semantics"):
            semantics_status = "degraded"

    return {
        "records": completed,
        "candidate_count": len(candidates),
        "incomplete_count": incomplete_count,
        "conflict_count": conflict_count,
        "semantics_status": semantics_status,
        "precise_record_count": len([item for item in completed if item["semantics"] == "precise"]),
        "degraded_record_count": len([item for item in completed if item["semantics"] == "degraded"]),
        "supported": bool(completed),
        "issue_samples": issue_samples[:5],
    }


def _bundle_trusted(bundle: RebuildBundle, t_begin: float, t_end: float) -> bool:
    return not any(window for window in bundle.untrusted_windows if window.t_begin < t_end and window.t_end > t_begin)


def _support_level(trusted: bool, semantics_status: str | None = None) -> str:
    if semantics_status == "unsupported":
        return "unsupported"
    if semantics_status == "degraded" or not trusted:
        return "degraded"
    return "exact"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_dataset_role(role: Any) -> str:
    role_text = str(role or "").strip()
    return role_text if role_text in VALID_DATASET_ROLES else "single"


def _base_dataset_id(dataset_id: str) -> str:
    if ":" not in dataset_id:
        return dataset_id
    prefix, suffix = dataset_id.rsplit(":", 1)
    return prefix if suffix in VALID_DATASET_ROLES else dataset_id


def _role_dataset_id(dataset_id: str, role: Any) -> str:
    normalized_role = _normalize_dataset_role(role)
    base_id = _base_dataset_id(dataset_id)
    if normalized_role == "single":
        return base_id
    return f"{base_id}:{normalized_role}"


def _infer_dataset_role(dataset_id: str | None, default: Any = "single") -> str:
    if dataset_id and ":" in dataset_id:
        suffix = dataset_id.rsplit(":", 1)[1]
        if suffix in VALID_DATASET_ROLES:
            return suffix
    return _normalize_dataset_role(default)


def _serialize_diff_summary(summary: Any) -> dict[str, Any]:
    payload = dataclass_to_dict(summary)
    scope = dict(payload.get("scope") or {})
    trust_summary = dict(payload.get("trust_summary") or {})
    payload["scope"] = scope
    payload["trust_summary"] = trust_summary
    payload["scope_id"] = scope.get("scope_id")
    payload["baseline_id"] = scope.get("baseline_id")
    payload["candidate_id"] = scope.get("candidate_id")
    payload["dimensions"] = list(scope.get("dimensions") or [])
    payload["metrics"] = list(payload.get("metric_changes") or [])
    payload["untrusted"] = not bool(trust_summary.get("trusted", True))
    return payload


def _time_unit_label(header: GlobalHeader | None) -> str:
    if header is None:
        return "unknown"
    return TIME_UNIT_LABELS.get(header.time_unit, str(header.time_unit))


def _clock_source_label(header: GlobalHeader | None) -> str:
    if header is None:
        return "unknown"
    return CLOCK_SOURCE_LABELS.get(header.clock_source, str(header.clock_source))


def _event_record_from_unified_event(event: UnifiedEvent) -> dict[str, Any]:
    return {
        "core_id": event.core_id,
        "event_id": event.event_id,
        "seq": event.seq,
        "timestamp": int(event.timestamp_raw),
        "payload": dict(event.payload),
        "flags": 1 if event.trust_tags else 0,
        "ver": 1,
    }


def _csv_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return _json_dumps_compact(value)


def _normalize_export_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_export_payload(value[key])
            for key in sorted(value)
            if key != "chunk_id"
        }
    if isinstance(value, list):
        return [_normalize_export_payload(item) for item in value]
    return value


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_scalar(row.get(field)) for field in fieldnames})


def _write_json_array_streaming(path: Path, rows: Iterable[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
    with path.open("w", encoding="utf-8") as handle:
        handle.write("[")
        for row in rows:
            if count:
                handle.write(",")
            for chunk in encoder.iterencode(row):
                handle.write(chunk)
            count += 1
        handle.write("]\n")
    return count


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return {
            field_info.name: getattr(value, field_info.name)
            for field_info in fields(value)
        }
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps_compact(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_dump_without_reserialize(path: Path, data: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        )
    else:
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
    with path.open("w", encoding="utf-8") as handle:
        for chunk in encoder.iterencode(data):
            handle.write(chunk)
        handle.write("\n")


def _record_duration(bucket: dict[str, float] | None, key: str, seconds: float) -> None:
    if bucket is None:
        return
    bucket[key] = float(bucket.get(key, 0.0)) + float(seconds)


def _measure_duration(
    bucket: dict[str, float] | None,
    key: str,
    callback: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    started = time.perf_counter()
    try:
        return callback(*args, **kwargs)
    finally:
        _record_duration(bucket, key, time.perf_counter() - started)


def _ref_summary(ref_index_path: str, ref_keys: list[str]) -> dict[str, Any]:
    return {
        "path": ref_index_path,
        "count": len(ref_keys),
        "first": ref_keys[0] if ref_keys else None,
        "last": ref_keys[-1] if ref_keys else None,
    }


def _ref_summary_from_bounds(ref_index_path: str, *, count: int, first: str | None, last: str | None) -> dict[str, Any]:
    return {
        "path": ref_index_path,
        "count": int(count),
        "first": first,
        "last": last,
    }


def _normalize_ref_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _looks_like_event_ref_key(ref_key: str | None) -> bool:
    return bool(ref_key and str(ref_key).startswith("evt:"))


def _event_ref_key_from_evidence_payload(payload: Any, *, allow_untyped_event_ref: bool = True) -> str | None:
    if isinstance(payload, EvidenceRef):
        ref_type = payload.ref_type
        ref_key = payload.ref_key
    elif isinstance(payload, dict):
        ref_type = payload.get("ref_type")
        ref_key = payload.get("ref_key")
    else:
        return None
    normalized_ref_key = _normalize_ref_key(ref_key)
    if normalized_ref_key is None:
        return None
    normalized_ref_type = str(ref_type or "").strip().lower()
    if normalized_ref_type == "event":
        return normalized_ref_key
    if allow_untyped_event_ref and normalized_ref_type in {"", "index"} and _looks_like_event_ref_key(normalized_ref_key):
        return normalized_ref_key
    return None


def _collect_direct_event_ref_keys(bundle: RebuildBundle) -> set[str]:
    refs: set[str] = set()
    for segment in bundle.task_states:
        ref_key = _normalize_ref_key(segment.cause_event)
        if ref_key is not None:
            refs.add(ref_key)
    for segment in bundle.exec_slices:
        start_ref = _normalize_ref_key(segment.start_event)
        end_ref = _normalize_ref_key(segment.end_event)
        if start_ref is not None:
            refs.add(start_ref)
        if end_ref is not None:
            refs.add(end_ref)
    for edge in list(bundle.resource_graph.hold_edges) + list(bundle.resource_graph.wait_edges):
        ref_key = _normalize_ref_key(edge.get("evidence_ref"))
        if ref_key is not None:
            refs.add(ref_key)
    return refs


def _collect_event_ref_keys_from_alerts(alerts: list[Alert] | None) -> set[str]:
    refs: set[str] = set()
    for alert in alerts or []:
        for evidence_ref in alert.evidence_refs:
            ref_key = _event_ref_key_from_evidence_payload(evidence_ref)
            if ref_key is not None:
                refs.add(ref_key)
    return refs


def _collect_event_ref_keys_from_diagnosis_rows(diagnoses: list[dict[str, Any]] | None) -> set[str]:
    refs: set[str] = set()
    for diagnosis in diagnoses or []:
        for evidence_ref in list((diagnosis or {}).get("evidence_refs") or []):
            ref_key = _event_ref_key_from_evidence_payload(evidence_ref)
            if ref_key is not None:
                refs.add(ref_key)
    return refs


def _collect_event_ref_keys_from_anchor_rows(anchors: list[dict[str, Any]] | None) -> set[str]:
    refs: set[str] = set()
    for anchor in anchors or []:
        ref_key = _event_ref_key_from_evidence_payload((anchor or {}).get("evidence_anchor"))
        if ref_key is not None:
            refs.add(ref_key)
    return refs


def _collect_context_event_ref_keys(context: dict[str, Any] | None) -> set[str]:
    if not isinstance(context, dict):
        return set()
    ref_key = _event_ref_key_from_evidence_payload(context.get("evidence_anchor"))
    return {ref_key} if ref_key is not None else set()


def _collect_required_event_ref_keys(
    export_bundle: RebuildBundle,
    *,
    context: dict[str, Any] | None = None,
    alerts: list[Alert] | None = None,
    diagnoses: list[dict[str, Any]] | None = None,
    anchors: list[dict[str, Any]] | None = None,
) -> set[str]:
    refs = _collect_direct_event_ref_keys(export_bundle)
    refs.update(_collect_context_event_ref_keys(context))
    refs.update(_collect_event_ref_keys_from_alerts(alerts))
    refs.update(_collect_event_ref_keys_from_diagnosis_rows(diagnoses))
    refs.update(_collect_event_ref_keys_from_anchor_rows(anchors))
    return refs


def _materialized_event_stream_with_required_refs(
    export_events: list[UnifiedEvent],
    *,
    source_events: list[UnifiedEvent],
    required_event_ref_keys: set[str],
) -> tuple[list[UnifiedEvent], set[str], int]:
    source_by_ref: dict[str, UnifiedEvent] = {}
    for event in source_events:
        ref_key = _normalize_ref_key(event.ref_key)
        if ref_key is not None and ref_key not in source_by_ref:
            source_by_ref[ref_key] = event
    selected_by_ref: dict[str, UnifiedEvent] = {}
    for event in export_events:
        ref_key = _normalize_ref_key(event.ref_key)
        if ref_key is not None:
            selected_by_ref[ref_key] = event
    in_scope_ref_keys = set(selected_by_ref)
    for ref_key in sorted(required_event_ref_keys):
        event = source_by_ref.get(ref_key)
        if event is not None:
            selected_by_ref.setdefault(ref_key, event)
    selected_events = sorted(selected_by_ref.values(), key=lambda item: item.sort_key)
    exported_ref_keys = set(selected_by_ref)
    closure_forced_event_count = len(exported_ref_keys - in_scope_ref_keys)
    return selected_events, exported_ref_keys, closure_forced_event_count


def _materialized_ref_index_rows(events: list[UnifiedEvent]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": index,
            "event_uid": event.event_uid,
            "ref_key": event.ref_key,
            "core_id": event.core_id,
            "seq": event.seq,
            "timestamp_raw": event.timestamp_raw,
            "timestamp_aligned": event.timestamp_aligned,
        }
        for index, event in enumerate(events)
    ]


def _missing_required_event_refs(required_event_ref_keys: set[str], exported_event_ref_keys: set[str]) -> list[str]:
    return sorted(ref_key for ref_key in required_event_ref_keys if ref_key not in exported_event_ref_keys)


def _bundle_time_window(bundle: RebuildBundle) -> tuple[float, float]:
    if bundle.event_stream:
        return (
            float(bundle.event_stream[0].timestamp_aligned),
            float(bundle.event_stream[-1].timestamp_aligned),
        )
    index_bundle = bundle.index_bundle
    if index_bundle is not None:
        time_origin = float(index_bundle.summary.get("time_origin", 0.0))
        time_end = float(index_bundle.summary.get("time_end", 0.0))
        if time_end > time_origin:
            return (time_origin, time_end)
    starts: list[float] = []
    ends: list[float] = []
    for item in bundle.task_states:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    for item in bundle.exec_slices:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    for item in bundle.irq_spans:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    for item in bundle.untrusted_windows:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    if not starts or not ends:
        return (0.0, 0.0)
    return (min(starts), max(ends))


def _trace_runtime_load_features(source_path: Path) -> dict[str, Any]:
    try:
        input_bytes = int(source_path.stat().st_size)
    except OSError:
        input_bytes = 0
    try:
        bindings = build_parser_cache_bindings(
            source_path,
            dictionary_path=DICTIONARY_PATH,
            parser_version=PARSER_VERSION,
            schema_version=PARSER_CACHE_SCHEMA_VERSION,
            index_build_mode="full",
            materialize_event_stream=True,
        )
    except (OSError, ValueError):
        bindings = {
            "trace_checksum": "",
            "dictionary_checksum": "",
        }
    return {
        "input_bytes": input_bytes,
        "trace_checksum": str(bindings.get("trace_checksum") or ""),
        "dictionary_checksum": str(bindings.get("dictionary_checksum") or ""),
    }


def _runtime_load_plan_payload(value: RuntimeLoadPlan | dict[str, Any]) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    return dict(value)


def _full_safe_runtime_load_plan(reason: str) -> RuntimeLoadPlan:
    return RuntimeLoadPlan(
        plan_version=RUNTIME_LOAD_PLAN_VERSION,
        load_mode="full",
        index_build_mode="full",
        materialize_event_stream=True,
        try_parser_artifact_reuse=False,
        try_sidecar_index_reuse=False,
        background_sidecar_prebuild=False,
        reasons=[str(reason)],
    )


def _source_trace_paths(source_handle: str | Path) -> Result[list[Path]]:
    source_path = Path(source_handle)
    if source_path.is_dir():
        package_trace = source_path / "event" / "events.trace"
        if (source_path / "manifest.json").exists() and package_trace.exists():
            return ok_result([package_trace])
        segments = sorted(item for item in source_path.iterdir() if item.is_file() and item.suffix == ".trace")
        if segments:
            return ok_result(segments)
        return err_result("INVALID_ARG", f"trace directory has no .trace segments: {source_path}")
    if not source_path.exists():
        return err_result("INVALID_ARG", f"trace source not found: {source_path}")
    if not source_path.is_file():
        return err_result("INVALID_ARG", f"trace source is not a file: {source_path}")
    return ok_result([source_path])


def _scan_trace_source(
    trace_paths: list[Path],
    *,
    dataset_id: str,
    dictionary: dict[str, Any] | str | Path | None,
    on_events: Callable[[list[DecodedEvent]], None],
) -> Result[None]:
    session = TraceDecodeSession(
        dataset_id=dataset_id,
        dictionary=dictionary,
        materialize_events=False,
    )
    for path in trace_paths:
        with path.open("rb") as handle:
            header_bytes = handle.read(GLOBAL_HEADER_STRUCT.size)
            if len(header_bytes) < GLOBAL_HEADER_STRUCT.size:
                return err_result("INVALID_ARG", f"incomplete trace header: {path}")
            header_result = session.feed(header_bytes)
            if not header_result.ok:
                return Result(
                    code=header_result.code,
                    message=header_result.message,
                    warnings=header_result.warnings,
                    untrusted_windows=header_result.untrusted_windows,
                )
            header_tuple = GLOBAL_HEADER_STRUCT.unpack(header_bytes)
            format_ver = int(header_tuple[3])
            if format_ver >= 2:
                meta_bytes = handle.read(SEGMENT_META_STRUCT.size)
                if len(meta_bytes) < SEGMENT_META_STRUCT.size:
                    return err_result("INVALID_ARG", f"incomplete segment meta: {path}")
                meta_result = session.feed(meta_bytes)
                if not meta_result.ok:
                    return Result(
                        code=meta_result.code,
                        message=meta_result.message,
                        warnings=meta_result.warnings,
                        untrusted_windows=meta_result.untrusted_windows,
                    )
            while True:
                chunk_header_bytes = handle.read(CHUNK_HEADER_STRUCT.size)
                if not chunk_header_bytes:
                    break
                if len(chunk_header_bytes) < CHUNK_HEADER_STRUCT.size:
                    return err_result("INVALID_ARG", f"incomplete chunk header: {path}")
                chunk_tuple = CHUNK_HEADER_STRUCT.unpack(chunk_header_bytes)
                if int(chunk_tuple[0]) != TRACE_CHUNK_MAGIC:
                    return err_result("INVALID_ARG", f"invalid chunk magic in {path}")
                payload_size = int(chunk_tuple[4])
                chunk_payload = handle.read(payload_size)
                if len(chunk_payload) < payload_size:
                    return err_result("INVALID_ARG", f"incomplete chunk payload: {path}")
                header_result = session.feed(chunk_header_bytes)
                if not header_result.ok:
                    return Result(
                        code=header_result.code,
                        message=header_result.message,
                        warnings=header_result.warnings,
                        untrusted_windows=header_result.untrusted_windows,
                    )
                chunk_result = session.feed(chunk_payload)
                if not chunk_result.ok:
                    return Result(
                        code=chunk_result.code,
                        message=chunk_result.message,
                        warnings=chunk_result.warnings,
                        untrusted_windows=chunk_result.untrusted_windows,
                    )
                if session.events:
                    on_events([item for item in session.events if isinstance(item, DecodedEvent)])
                    session.events.clear()
    finalized = session.finalize()
    if not finalized.ok:
        return Result(
            code=finalized.code,
            message=finalized.message,
            warnings=finalized.warnings,
            untrusted_windows=finalized.untrusted_windows,
        )
    retained = [item for item in finalized.data.get("events") or [] if isinstance(item, DecodedEvent)]
    if retained:
        on_events(retained)
    return ok_result(None, warnings=finalized.warnings, untrusted_windows=finalized.untrusted_windows)


def _read_spooled_event(handle) -> dict[str, Any] | None:
    aligned_bytes = handle.read(_ALIGNED_TS_STRUCT.size)
    if not aligned_bytes:
        return None
    if len(aligned_bytes) < _ALIGNED_TS_STRUCT.size:
        raise ValueError("spooled event is truncated (aligned timestamp)")
    header_bytes = handle.read(EVENT_HEADER_STRUCT.size)
    if len(header_bytes) < EVENT_HEADER_STRUCT.size:
        raise ValueError("spooled event is truncated (event header)")
    ver, flags, core_id, event_id, seq, timestamp_raw, payload_len = EVENT_HEADER_STRUCT.unpack(header_bytes)
    payload_bytes = handle.read(payload_len)
    if len(payload_bytes) < payload_len:
        raise ValueError("spooled event is truncated (payload)")
    return {
        "aligned_ts": int(_ALIGNED_TS_STRUCT.unpack(aligned_bytes)[0]),
        "ver": int(ver),
        "flags": int(flags),
        "core_id": int(core_id),
        "event_id": int(event_id),
        "seq": int(seq),
        "timestamp_raw": int(timestamp_raw),
        "payload_bytes": payload_bytes,
    }


def _schema_missing_fields(schema: dict[str, Any], payload: Any, label: str) -> list[str]:
    if not isinstance(payload, dict):
        return [label]
    missing: list[str] = []
    for key in schema.get("required", []):
        if key not in payload:
            missing.append(f"{label}.{key}")
    properties = schema.get("properties", {})
    for key, child_schema in properties.items():
        if key in payload and isinstance(payload[key], dict) and child_schema.get("type") == "object":
            missing.extend(_schema_missing_fields(child_schema, payload[key], f"{label}.{key}"))
    return missing


def _normalize_compare_scope_payload(value: Any, *, label: str = "compare_scope") -> Result[dict[str, Any] | None]:
    if value is None:
        return ok_result(None)
    if not isinstance(value, dict):
        return err_result("INVALID_ARG", f"{label} must be an object")
    if not value:
        return ok_result({})
    missing = _schema_missing_fields(load_specs()["compare_scope"], value, label)
    if missing:
        return err_result("INVALID_ARG", f"compare_scope schema missing fields: {', '.join(missing)}")
    normalized = dict(value)
    normalized["aligned_time_window"] = [float(item) for item in normalized.get("aligned_time_window") or []]
    normalized["filter"] = dict(normalized.get("filter") or {})
    normalized["dimensions"] = list(normalized.get("dimensions") or [])
    normalized["metric_ids"] = list(normalized.get("metric_ids") or [])
    if "bucket_size" in normalized and normalized["bucket_size"] is not None:
        normalized["bucket_size"] = float(normalized["bucket_size"])
    return ok_result(normalized)


def _event_from_dict(raw: dict[str, Any]) -> UnifiedEvent:
    payload = dict(raw)
    payload["sort_key"] = tuple(raw.get("sort_key", (raw["timestamp_aligned"], raw["core_id"], raw["seq"])))
    return UnifiedEvent(**payload)


def _task_state_from_dict(raw: dict[str, Any]) -> TaskStateSeg:
    return TaskStateSeg(**raw)


def _exec_slice_from_dict(raw: dict[str, Any]) -> ExecSlice:
    return ExecSlice(**raw)


def _irq_span_from_dict(raw: dict[str, Any]) -> IrqSpan:
    return IrqSpan(**raw)


def _window_from_dict(raw: dict[str, Any]) -> UntrustedWindow:
    return UntrustedWindow(**raw)


def _header_from_dict(raw: dict[str, Any] | None, default_run_id: str) -> GlobalHeader | None:
    if not raw:
        return None
    payload = dict(raw)
    payload.setdefault("run_id", default_run_id)
    return GlobalHeader(**payload)


def _segment_meta_from_dict(raw: dict[str, Any]) -> SegmentMeta:
    return SegmentMeta(**raw)


def _segment_chain_payload(segment_metas: list[SegmentMeta]) -> dict[str, Any]:
    segments = [dataclass_to_dict(item) for item in segment_metas]
    contiguous = True
    prev_seq = 0
    checksum_set: set[int] = set()
    for index, meta in enumerate(segment_metas):
        expected_prev = 0 if index == 0 else prev_seq
        if meta.prev_segment_seq != expected_prev:
            contiguous = False
        prev_seq = meta.segment_seq
        if meta.dict_ref_checksum:
            checksum_set.add(int(meta.dict_ref_checksum))
    return {
        "count": len(segments),
        "segments": segments,
        "contiguous": contiguous,
        "dict_ref_checksums": sorted(checksum_set),
    }


def _bundle_from_dict(
    raw: dict[str, Any],
    *,
    event_stream_override: list[UnifiedEvent] | None = None,
    header_override: GlobalHeader | None = None,
) -> RebuildBundle:
    header = header_override if header_override is not None else _header_from_dict(raw.get("header"), raw["dataset_id"])
    event_stream = (
        list(event_stream_override)
        if event_stream_override is not None
        else [_event_from_dict(item) for item in raw["event_stream"]]
    )
    capability_flags = raw["capability_flags"]
    alignment = _alignment_from_dict(raw.get("alignment"))
    if alignment is None:
        alignment = _alignment_from_events(event_stream, capability_flags=capability_flags)
    return RebuildBundle(
        bundle_id=raw["bundle_id"],
        dataset_id=raw["dataset_id"],
        event_stream=event_stream,
        task_states=[_task_state_from_dict(item) for item in raw["task_states"]],
        exec_slices=[_exec_slice_from_dict(item) for item in raw["exec_slices"]],
        resource_graph=ResourceGraph(**raw["resource_graph"]),
        irq_spans=[_irq_span_from_dict(item) for item in raw["irq_spans"]],
        untrusted_windows=[_window_from_dict(item) for item in raw["untrusted_windows"]],
        rebuild_rev=raw["rebuild_rev"],
        capability_flags=capability_flags,
        alignment=alignment,
        segment_metas=[_segment_meta_from_dict(item) for item in raw.get("segment_metas", [])],
        header=header,
        index_bundle=None,
    )


def _alignment_from_dict(raw: Any) -> AlignmentSummary | None:
    if not isinstance(raw, dict):
        return None
    raw_offsets = raw.get("offsets")
    offsets: dict[int, float] = {}
    if isinstance(raw_offsets, dict):
        for core_id, offset in raw_offsets.items():
            try:
                offsets[int(core_id)] = float(offset)
            except (TypeError, ValueError):
                continue
    raw_core_ids = raw.get("core_ids")
    core_ids: list[int] = []
    if isinstance(raw_core_ids, list):
        for item in raw_core_ids:
            try:
                core_ids.append(int(item))
            except (TypeError, ValueError):
                continue
    if not core_ids and offsets:
        core_ids = sorted(offsets)
    elif core_ids:
        core_ids = sorted(dict.fromkeys(core_ids))
    if not core_ids and not offsets:
        return None
    for core_id in core_ids:
        offsets.setdefault(core_id, 0.0)
    reference_core_id_raw = raw.get("reference_core_id")
    try:
        reference_core_id = int(reference_core_id_raw) if reference_core_id_raw is not None else None
    except (TypeError, ValueError):
        reference_core_id = None
    return AlignmentSummary(
        core_ids=core_ids,
        offsets={core_id: float(offsets.get(core_id, 0.0)) for core_id in core_ids},
        anchors_seen=bool(raw.get("anchors_seen")),
        calibrated=bool(raw.get("calibrated")),
        reference_core_id=reference_core_id,
    )


def _alignment_from_events(
    events: list[UnifiedEvent],
    *,
    capability_flags: dict[str, Any] | None = None,
) -> AlignmentSummary | None:
    if not events:
        return None
    offsets: dict[int, float] = {}
    for event in events:
        core_id = int(event.core_id)
        offsets.setdefault(core_id, float(event.timestamp_aligned) - float(event.timestamp_raw))
    core_ids = sorted(offsets)
    flags = capability_flags or {}
    inferred_calibrated = any(abs(value) > 0.0 for value in offsets.values())
    return AlignmentSummary(
        core_ids=core_ids,
        offsets={core_id: float(offsets.get(core_id, 0.0)) for core_id in core_ids},
        anchors_seen=bool(flags.get("align_anchor_seen", inferred_calibrated)),
        calibrated=bool(flags.get("align_calibrated", inferred_calibrated)),
        reference_core_id=core_ids[0] if core_ids else None,
    )


def _is_evidence_package(meta: dict[str, Any], manifest: dict[str, Any]) -> bool:
    return str(meta.get("export_family") or "").strip().lower() == "evidence" or manifest.get("package_version") == EVIDENCE_PACKAGE_VERSION


def _bundle_has_full_contract(raw: dict[str, Any]) -> bool:
    required = {
        "bundle_id",
        "dataset_id",
        "event_stream",
        "task_states",
        "exec_slices",
        "resource_graph",
        "irq_spans",
        "untrusted_windows",
        "rebuild_rev",
        "capability_flags",
    }
    return required.issubset(raw)


def _isolated_load_dataset_artifact(
    trace_path: Path,
    *,
    dictionary_path: Path | None,
    job_id_prefix: str,
    materialize_event_stream: bool = True,
    index_build_mode: str = "full",
) -> Result[DatasetArtifact]:
    with tempfile.TemporaryDirectory(prefix="rttrace-package-parse-") as artifact_dir:
        parser_agent = ParserProcessAgent(job_id=f"{job_id_prefix}-{uuid.uuid4().hex}")
        parsed = parser_agent.parse_rebuild(
            trace_path,
            artifact_policy={
                "artifact_dir": artifact_dir,
                "dictionary_path": str(dictionary_path) if dictionary_path is not None else None,
                "materialize_event_stream": bool(materialize_event_stream),
                "index_build_mode": str(index_build_mode),
                "load_artifact": True,
                "retain_artifact": False,
            },
        )
    if not parsed.ok:
        return Result(
            code=parsed.code,
            message=parsed.message,
            data=parsed.data,
            warnings=parsed.warnings,
            untrusted_windows=parsed.untrusted_windows,
        )
    return ok_result(
        parsed.data["artifact"],
        warnings=parsed.warnings,
        untrusted_windows=parsed.untrusted_windows,
    )


def _minimal_evidence_bundle_from_artifacts(
    package_path: Path,
    meta: dict[str, Any],
    raw_bundle: dict[str, Any],
    dictionary_info: dict[str, Any],
) -> Result[RebuildBundle]:
    trace_path = package_path / "event" / "events.trace"
    event_stream: list[UnifiedEvent] = []
    header: GlobalHeader | None = None
    if trace_path.exists() and trace_path.stat().st_size > 0:
        dict_ref = meta.get("dict_ref") or {}
        dictionary_path = package_path / str(dict_ref.get("path") or "reference/dictionary.json")
        reconstructed = _isolated_load_dataset_artifact(
            trace_path,
            dictionary_path=dictionary_path,
            job_id_prefix="evidence-minimal-load",
        )
        if reconstructed.ok:
            event_stream = list(reconstructed.data.bundle.event_stream)
            header = reconstructed.data.header
    if header is None:
        header = GlobalHeader(
            magic="0x0",
            endian=1,
            time_unit=1,
            clock_source=1,
            format_ver=1,
            dict_ver=int(meta.get("dict_ver", 1) or 1),
            producer_ver=str(meta.get("parser_ver") or "repro-evidence"),
            run_id=str(meta.get("run_id") or meta.get("dataset_id") or "evidence"),
        )
    capability_flags = dict(meta.get("capability_flags") or {})
    alignment = _alignment_from_events(event_stream, capability_flags=capability_flags)
    return ok_result(
        RebuildBundle(
            bundle_id=str(raw_bundle.get("bundle_id") or f"rebuild:{meta.get('snapshot_id') or meta.get('dataset_id') or 'evidence'}"),
            dataset_id=str(meta.get("dataset_id") or raw_bundle.get("dataset_id") or "evidence"),
            event_stream=event_stream,
            task_states=[],
            exec_slices=[],
            resource_graph=ResourceGraph(),
            irq_spans=[],
            untrusted_windows=[],
            rebuild_rev=int(raw_bundle.get("rebuild_rev", 1) or 1),
            capability_flags=capability_flags,
            alignment=alignment,
            segment_metas=[],
            header=header,
            index_bundle=None,
        )
    )


def _load_cached_rebuild_bundle(package_path: Path, bundle_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    if bundle_cache is not None:
        cached_bundle = bundle_cache.get("raw_bundle")
        if isinstance(cached_bundle, dict):
            return cached_bundle
    raw_bundle = json_load(package_path / "rebuild" / "rebuild_bundle.json")
    if bundle_cache is not None:
        bundle_cache["raw_bundle"] = raw_bundle
    return raw_bundle


def _validate_package_refs(
    package_path: Path,
    meta: dict[str, Any],
    manifest: dict[str, Any],
    *,
    bundle_cache: dict[str, Any] | None = None,
) -> Result[None]:
    specs = load_specs()
    if manifest.get("package_version") not in SUPPORTED_PACKAGE_VERSIONS:
        return err_result("UNSUPPORTED_VERSION", f"unsupported package version: {manifest.get('package_version')}")
    meta_missing = _schema_missing_fields(specs["meta"], meta, "meta")
    manifest_missing = _schema_missing_fields(specs["manifest"], manifest, "manifest")
    if meta_missing:
        return err_result("INVALID_ARG", f"meta schema missing fields: {', '.join(meta_missing)}")
    if manifest_missing:
        return err_result("INVALID_ARG", f"manifest schema missing fields: {', '.join(manifest_missing)}")

    dict_ref = meta["dict_ref"]
    dict_path = package_path / dict_ref["path"]
    if not dict_path.exists():
        return err_result("INVALID_ARG", f"missing dictionary reference: {dict_ref['path']}")
    if checksum_file(dict_path) != dict_ref["checksum"]:
        return err_result("INVALID_ARG", "dictionary checksum mismatch")

    for ref_payload in meta["schema_ref"].values():
        schema_path = package_path / ref_payload["path"]
        if not schema_path.exists():
            return err_result("INVALID_ARG", f"missing schema reference: {ref_payload['path']}")
        if checksum_file(schema_path) != ref_payload["checksum"]:
            return err_result("INVALID_ARG", f"schema checksum mismatch: {ref_payload['path']}")

    for entry in manifest["entries"]:
        target = package_path / entry["path"]
        if not target.exists():
            return err_result("INVALID_ARG", f"manifest entry missing: {entry['path']}")
        if checksum_file(target) != entry["checksum"]:
            return err_result("INVALID_ARG", f"manifest checksum mismatch: {entry['path']}")

    meta_segment_chain = meta.get("segment_chain") or {}
    manifest_segment_chain = manifest.get("segment_chain") or {}
    if bool(meta_segment_chain) != bool(manifest_segment_chain):
        return err_result("INVALID_ARG", "segment_chain metadata missing or inconsistent")
    if meta_segment_chain and meta_segment_chain != manifest_segment_chain:
        return err_result("INVALID_ARG", "segment_chain metadata mismatch")
    if meta_segment_chain:
        raw_bundle = _load_cached_rebuild_bundle(package_path, bundle_cache)
        rebuild_segment_chain = _segment_chain_payload(
            [_segment_meta_from_dict(item) for item in raw_bundle.get("segment_metas", [])]
        )
        if rebuild_segment_chain != meta_segment_chain:
            return err_result("INVALID_ARG", "segment_chain metadata mismatch with rebuild bundle")
    if _is_evidence_package(meta, manifest):
        evidence_validation = load_evidence_control(package_path, meta, manifest)
        if not evidence_validation.ok:
            return evidence_validation
    return ok_result(None)


def _load_validated_package(package_path: str | Path) -> Result[dict[str, Any]]:
    target = Path(package_path)
    manifest_path = target / "manifest.json"
    meta_path = target / "meta.json"
    if not manifest_path.exists() or not meta_path.exists():
        return err_result("INVALID_ARG", "package missing manifest or meta")
    meta = json_load(meta_path)
    manifest = json_load(manifest_path)
    bundle_cache: dict[str, Any] = {}
    validation = _validate_package_refs(target, meta, manifest, bundle_cache=bundle_cache)
    if not validation.ok:
        return validation
    rebuild_path = target / "rebuild" / "rebuild_bundle.json"
    if not rebuild_path.exists():
        return err_result("INVALID_ARG", "package missing rebuild bundle")
    raw_bundle = _load_cached_rebuild_bundle(target, bundle_cache)
    dict_ref = meta.get("dict_ref") or {}
    dict_path = target / dict_ref.get("path", "reference/dictionary.json")
    dictionary_info = dict(meta.get("dictionary_status") or {})
    if dict_path.exists():
        dictionary_info["resolved_dictionary"] = json_load(dict_path)
        dictionary_info.setdefault("resolved_dict_ver", int(dictionary_info["resolved_dictionary"].get("dict_ver", 0) or 0))
        dictionary_info.setdefault("resolved_source", "package")
    dictionary_info.setdefault("reference_path", dict_ref.get("path"))
    is_evidence = _is_evidence_package(meta, manifest)
    if is_evidence and not _bundle_has_full_contract(raw_bundle):
        minimal_bundle = _minimal_evidence_bundle_from_artifacts(target, meta, raw_bundle, dictionary_info)
        if not minimal_bundle.ok:
            return minimal_bundle
        return ok_result(
            {
                "package_path": str(target),
                "meta": meta,
                "manifest": manifest,
                "bundle": minimal_bundle.data,
                "dataset_role": _normalize_dataset_role(meta.get("compare_role")),
                "dictionary_info": dictionary_info,
            }
        )
    header_override = _header_from_dict(raw_bundle.get("header"), raw_bundle["dataset_id"])
    event_stream_override: list[UnifiedEvent] | None = None
    if "event_stream" not in raw_bundle or raw_bundle.get("event_stream") is None or (not is_evidence and not raw_bundle.get("event_stream")):
        event_trace_path = target / "event" / "events.trace"
        if event_trace_path.exists():
            reconstructed = _isolated_load_dataset_artifact(
                event_trace_path,
                dictionary_path=dict_path,
                job_id_prefix="package-rebuild-load",
            )
            if not reconstructed.ok:
                return Result(
                    code=reconstructed.code,
                    message=reconstructed.message,
                    warnings=reconstructed.warnings,
                    untrusted_windows=reconstructed.untrusted_windows,
                )
            event_stream_override = list(reconstructed.data.bundle.event_stream)
            if header_override is None:
                header_override = reconstructed.data.header
    bundle = _bundle_from_dict(
        raw_bundle,
        event_stream_override=event_stream_override,
        header_override=header_override,
    )
    return ok_result(
        {
            "package_path": str(target),
            "meta": meta,
            "manifest": manifest,
            "bundle": bundle,
            "dataset_role": _normalize_dataset_role(meta.get("compare_role")),
            "dictionary_info": dictionary_info,
        }
    )


def _segment_matches_filter(segment: TaskStateSeg, filter_spec: dict[str, Any]) -> bool:
    if not filter_spec:
        return True
    task_id = filter_spec.get("task_id")
    task_ids = filter_spec.get("task_ids")
    resource_id = _normalize_resource_id(filter_spec.get("resource_id") or filter_spec.get("obj_id"))
    if task_id is not None and segment.task_id != task_id:
        return False
    if task_ids and segment.task_id not in set(task_ids):
        return False
    if resource_id is not None and segment.related_obj != resource_id:
        return False
    return True


def _task_state_lane_group(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"core", "按核", "by_core"}:
        return "core"
    return "task"


def _window_intersects(window: UntrustedWindow, time_window: tuple[float, float]) -> bool:
    return window.t_begin < time_window[1] and window.t_end > time_window[0]


def _task_state_window_trusted(
    time_window: tuple[float, float],
    untrusted_windows: list[UntrustedWindow],
) -> bool:
    return not any(_window_intersects(window, time_window) for window in untrusted_windows)


def _task_state_lane_label(
    segment: TaskStateSeg,
    lane_group: str,
    event_by_uid: dict[str, UnifiedEvent],
) -> tuple[str, int | None]:
    if lane_group == "core":
        cause_event = event_by_uid.get(segment.cause_event)
        if cause_event is not None:
            return (f"Core {cause_event.core_id}", cause_event.core_id)
    return (f"Task {segment.task_id}", None)


def _task_state_cursor_hint(
    context: AnalysisContext,
    anchor_ref: dict[str, Any] | None = None,
) -> str | None:
    evidence_anchor = dict(anchor_ref or context.evidence_anchor or {})
    if evidence_anchor.get("ref_key"):
        return f"ref:{evidence_anchor['ref_key']}"
    if evidence_anchor.get("event_uid"):
        return f"event:{evidence_anchor['event_uid']}"
    selection = context.selection or {}
    if selection.get("task_id") is not None:
        return f"task:{selection['task_id']}"
    if selection.get("resource_id") is not None:
        return f"resource:{selection['resource_id']}"
    playback_cursor = context.playback_cursor or {}
    if playback_cursor.get("event_uid"):
        return f"event:{playback_cursor['event_uid']}"
    return None


def _task_state_display_label(state: str) -> str:
    return state.replace("_", " ").title()


def _normalize_task_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _normalize_task_ids(values: Any) -> tuple[int, ...]:
    raw_values = values if isinstance(values, (list, tuple, set)) else [values]
    normalized: list[int] = []
    for item in raw_values:
        task_id = _normalize_task_id(item)
        if task_id is not None and task_id not in normalized:
            normalized.append(task_id)
    return tuple(sorted(normalized))


def _task_state_runtime_overrides(query: TaskStateQuery) -> dict[str, Any]:
    runtime_filter = getattr(query, "_runtime_filter", None)
    if isinstance(runtime_filter, dict):
        return dict(runtime_filter)
    return {}


def _task_state_legacy_filter(query: TaskStateQuery) -> dict[str, Any]:
    legacy_filter = getattr(query, "filter", None)
    if isinstance(legacy_filter, dict):
        return dict(legacy_filter)
    return {}


def _normalize_task_state_query(query: TaskStateQuery) -> _NormalizedTaskStateQuery:
    legacy_filter = _task_state_legacy_filter(query)
    runtime_overrides = _task_state_runtime_overrides(query)
    dataset_id = legacy_filter.get("dataset_id")
    normalized_task_filter = _normalize_task_ids(getattr(query, "task_filter", ()))
    if not normalized_task_filter:
        normalized_task_filter = _normalize_task_ids(
            legacy_filter.get("task_ids")
            if legacy_filter.get("task_ids")
            else legacy_filter.get("task_id")
        )
    runtime_filter: dict[str, Any] = {}
    if len(normalized_task_filter) == 1:
        runtime_filter["task_id"] = normalized_task_filter[0]
    elif normalized_task_filter:
        runtime_filter["task_ids"] = list(normalized_task_filter)
    resource_id = _normalize_resource_id(
        runtime_overrides.get("resource_id")
        or runtime_overrides.get("obj_id")
        or legacy_filter.get("resource_id")
        or legacy_filter.get("obj_id")
    )
    if resource_id is not None:
        runtime_filter["resource_id"] = resource_id
    anchor_ref = getattr(query, "anchor_ref", None)
    normalized_anchor_ref = dict(anchor_ref) if isinstance(anchor_ref, dict) and anchor_ref else None
    include_summary = bool(getattr(query, "include_summary", True))
    state_mask = tuple(sorted(str(item) for item in getattr(query, "state_mask", ()) if item))
    return _NormalizedTaskStateQuery(
        dataset_id=str(dataset_id) if dataset_id is not None else None,
        time_window=(float(query.time_window[0]), float(query.time_window[1])),
        lane_group=_task_state_lane_group(getattr(query, "lane_group", None)),
        state_mask=state_mask,
        task_filter=normalized_task_filter,
        anchor_ref=normalized_anchor_ref,
        include_summary=include_summary,
        runtime_filter=runtime_filter,
    )


def _normalize_cache_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(key), _normalize_cache_value(item)) for key, item in sorted(value.items(), key=lambda pair: str(pair[0])))
    if isinstance(value, (list, tuple, set)):
        return tuple(_normalize_cache_value(item) for item in value)
    return value


def _task_state_cache_key(normalized: _NormalizedTaskStateQuery) -> tuple[Any, ...]:
    return (
        "task_states",
        normalized.time_window,
        normalized.lane_group,
        normalized.state_mask,
        normalized.task_filter,
        _normalize_cache_value(normalized.anchor_ref),
        normalized.include_summary,
        _normalize_cache_value(normalized.runtime_filter),
    )


def _event_cursor_cache_key(cursor: EventCursor | None) -> tuple[Any, ...] | None:
    if cursor is None:
        return None
    return (
        float(cursor.ts),
        int(cursor.core_id),
        int(cursor.seq),
        tuple(cursor.sort_key),
    )


def _event_page_cache_key(
    filter_spec: dict[str, Any],
    cursor: EventCursor | None,
    limit: int,
    time_window: tuple[float, float] | None,
    direction: str,
) -> tuple[Any, ...]:
    return (
        "event_page",
        _normalize_cache_value(filter_spec),
        _event_cursor_cache_key(cursor),
        int(limit),
        None if time_window is None else (float(time_window[0]), float(time_window[1])),
        direction,
    )


def _cache_source_label(namespace: str) -> str:
    return f"query_cache:{namespace}"


def _cache_eviction_summary(evicted: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "namespace": entry.namespace,
            "source": entry.source,
        }
        for entry in evicted
    ]


def _estimate_task_state_view_bytes(view: TaskStateViewModel) -> int:
    row_count = len(view.rows)
    segment_count = sum(len(row.segments) for row in view.rows)
    return 512 + row_count * 160 + segment_count * 128


def _estimate_event_page_bytes(page: EventPage) -> int:
    return 512 + len(page.items) * 256


def _edge_task_id(edge: dict[str, Any]) -> Any:
    return edge.get("from_task", edge.get("task_id", edge.get("owner_task")))


def _edge_obj_id(edge: dict[str, Any]) -> Any:
    return edge.get("to_obj", edge.get("obj_id"))


def _normalize_resource_id(value: Any) -> Any:
    if value is None or isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return value


def _event_summary(event: UnifiedEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "event_uid": event.event_uid,
        "ref_key": event.ref_key,
        "event_name": event.event_name,
        "timestamp": event.timestamp_aligned,
        "core_id": event.core_id,
        "task_id": event.task_id,
        "obj_id": event.obj_id,
        "payload": dict(event.payload),
    }


def _metric_summary_value(summary: dict[str, Any]) -> float:
    if "avg_utilization" in summary:
        return float(summary["avg_utilization"]) * 100.0
    if "total_blocked_time" in summary:
        return float(summary["total_blocked_time"])
    if "total_ready_wait_time" in summary:
        return float(summary["total_ready_wait_time"])
    if "avg_response_time" in summary:
        return float(summary["avg_response_time"])
    if "max_jitter" in summary:
        return float(summary["max_jitter"])
    if "max_deadline_miss" in summary:
        return float(summary["max_deadline_miss"])
    if "count" in summary:
        return float(summary["count"])
    if "irq_count" in summary:
        return float(summary["irq_count"])
    if "total_irq_latency" in summary:
        return float(summary["total_irq_latency"])
    return 0.0


def _metric_bucket_series(
    session: MetricSession,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
    bucket_size: float,
) -> tuple[dict[str, list[dict[str, Any]]], list[UntrustedWindow]]:
    if bucket_size <= 0.0 or t_end <= t_begin:
        return {}, []
    cursor = float(t_begin)
    by_metric: dict[str, list[dict[str, Any]]] = {}
    bucket_windows: list[UntrustedWindow] = []
    while cursor < float(t_end):
        bucket_end = min(float(t_end), cursor + bucket_size)
        bucket_metrics = metric_Compute(session, cursor, bucket_end, filter_spec)
        if bucket_metrics.ok:
            for metric in bucket_metrics.data or []:
                by_metric.setdefault(metric.metric_id, []).append(
                    {
                        "t_begin": cursor,
                        "t_end": bucket_end,
                        "midpoint": round((cursor + bucket_end) / 2.0, 6),
                        "value": round(_metric_summary_value(metric.summary), 6),
                        "trusted": metric.trusted,
                    }
                )
            bucket_windows.extend(bucket_metrics.untrusted_windows)
        cursor = bucket_end
    return by_metric, bucket_windows


def _resource_wait_chains(
    hold_edges: list[dict[str, Any]],
    wait_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    holds_by_obj: dict[Any, list[dict[str, Any]]] = {}
    for edge in hold_edges:
        holds_by_obj.setdefault(_edge_obj_id(edge), []).append(edge)
    chains: list[dict[str, Any]] = []
    for wait_edge in wait_edges:
        obj_id = _edge_obj_id(wait_edge)
        matching_holds = holds_by_obj.get(obj_id, [])
        if matching_holds:
            for hold_edge in matching_holds:
                owner_task_id = _edge_task_id(hold_edge)
                chains.append(
                    {
                        "path": [
                            f"task:{_edge_task_id(wait_edge)}",
                            f"obj:{obj_id}",
                            f"task:{owner_task_id}",
                        ],
                        "task_id": _edge_task_id(wait_edge),
                        "obj_id": obj_id,
                        "owner_task_id": owner_task_id,
                        "wait_evidence": wait_edge.get("evidence_ref"),
                        "hold_evidence": hold_edge.get("evidence_ref"),
                        "trusted": bool(wait_edge.get("trusted", True) and hold_edge.get("trusted", True)),
                    }
                )
            continue
        owner_task_id = wait_edge.get("owner_task_id")
        if owner_task_id is not None:
            chains.append(
                {
                    "path": [
                        f"task:{_edge_task_id(wait_edge)}",
                        f"obj:{obj_id}",
                        f"task:{owner_task_id}",
                    ],
                    "task_id": _edge_task_id(wait_edge),
                    "obj_id": obj_id,
                    "owner_task_id": owner_task_id,
                    "wait_evidence": wait_edge.get("evidence_ref"),
                    "hold_evidence": None,
                    "trusted": bool(wait_edge.get("trusted", True)),
                }
            )
    return chains


def _lod_ready_state(
    *,
    lod0: bool = False,
    lod1: bool = False,
    lod2: bool = False,
) -> dict[str, bool]:
    return {
        "lod0": bool(lod0),
        "lod1": bool(lod1),
        "lod2": bool(lod2),
    }


def _view_ready_state(**overrides: bool) -> dict[str, bool]:
    ready = {
        "timeline": False,
        "task_states": False,
        "event_table": False,
        "metric_series": False,
        "resource_graph": False,
        "alerts": False,
    }
    ready.update({key: bool(value) for key, value in overrides.items()})
    return ready


def _readiness_state(
    *,
    stage: str,
    source: str,
    preview: bool,
    lod_ready: dict[str, bool] | None = None,
    view_ready: dict[str, bool] | None = None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    readiness = ReadinessState(
        stage=stage,
        source=source,
        preview=preview,
        lod_ready=dict(lod_ready or _lod_ready_state()),
        view_ready=dict(view_ready or _view_ready_state()),
        fallback_reason=fallback_reason,
    )
    return dataclass_to_dict(readiness)


def _preview_readiness(source: str) -> dict[str, Any]:
    return _readiness_state(
        stage="preview_ready",
        source=source,
        preview=True,
        lod_ready=_lod_ready_state(lod0=True),
        view_ready=_view_ready_state(timeline=True),
    )


def _bundle_has_lod0_index(bundle: RebuildBundle) -> bool:
    index_bundle = bundle.index_bundle
    if index_bundle is None:
        return False
    return str(index_bundle.summary.get("index_build_mode") or "full") != "deferred"


def _query_ready_state(
    *,
    source: str,
    bundle: RebuildBundle | None = None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    if bundle is None:
        lod_ready = _lod_ready_state()
        view_ready = _view_ready_state()
    else:
        lod_ready = _lod_ready_state(
            lod0=_bundle_has_lod0_index(bundle) or bool(bundle.event_stream),
            lod1=bool(bundle.exec_slices or bundle.irq_spans),
            lod2=bool(bundle.event_stream),
        )
        view_ready = _view_ready_state(
            timeline=lod_ready["lod0"],
            task_states=True,
            event_table=True,
            metric_series=True,
            resource_graph=True,
            alerts=True,
        )
    return _readiness_state(
        stage="query_ready",
        source=source,
        preview=False,
        lod_ready=lod_ready,
        view_ready=view_ready,
        fallback_reason=fallback_reason,
    )


def _preview_buckets_from_timestamps(
    timestamps: list[float],
    time_window: tuple[float, float],
    bucket_count: int = 24,
    fallback_count: int = 0,
) -> list[dict[str, Any]]:
    t_begin, t_end = float(time_window[0]), float(time_window[1])
    if t_end <= t_begin:
        return []
    requested = max(int(bucket_count), 1)
    bucket_size = max((t_end - t_begin) / requested, 1.0)
    rows: list[dict[str, Any]] = []
    cursor = t_begin
    while cursor < t_end:
        bucket_end = min(t_end, cursor + bucket_size)
        rows.append(
            {
                "t_begin": cursor,
                "t_end": bucket_end,
                "event_count": 0.0,
                "chunk_count": 0.0,
            }
        )
        cursor = bucket_end
    if timestamps:
        for timestamp in timestamps:
            for row in rows:
                if float(row["t_begin"]) <= float(timestamp) <= float(row["t_end"]):
                    row["event_count"] = round(float(row["event_count"]) + 1.0, 6)
                    break
    elif rows:
        rows[0]["event_count"] = float(fallback_count)
    return rows


def _preview_package(package_path: Path) -> Result[dict[str, Any]]:
    meta_path = package_path / "meta.json"
    manifest_path = package_path / "manifest.json"
    if not meta_path.exists() or not manifest_path.exists():
        return err_result("INVALID_ARG", "package missing manifest or meta")
    meta = json_load(meta_path)
    manifest = json_load(manifest_path)
    trace_preview: dict[str, Any] | None = None
    event_trace_path = package_path / "event" / "events.trace"
    if event_trace_path.exists():
        prescan = prs_Prescan(event_trace_path)
        if prescan.ok:
            trace_preview = prescan.data
    time_window = tuple(
        meta.get("time_window")
        or meta.get("analysis_context", {}).get("time_window")
        or (trace_preview or {}).get("time_window")
        or (0.0, 0.0)
    )
    ref_index_path = package_path / "event" / "ref_index.json"
    timestamps: list[float] = []
    if ref_index_path.exists():
        timestamps = [
            float(item.get("timestamp_aligned", item.get("timestamp_raw", 0.0)))
            for item in json_load(ref_index_path)
        ]
    event_entry = next((entry for entry in manifest.get("entries", []) if entry.get("path") == "event/events.trace"), {})
    if trace_preview is not None:
        header_info = dict(trace_preview.get("header") or {})
        event_count = int(trace_preview.get("record_count", len(timestamps)))
        chunk_count = int(trace_preview.get("chunk_count", 0))
        core_ids = list(trace_preview.get("core_ids") or [])
        lod0_buckets = list(trace_preview.get("lod0_buckets") or [])
    else:
        header_info = {}
        event_count = int(event_entry.get("count", len(timestamps)))
        chunk_count = 0
        core_ids = []
        lod0_buckets = _preview_buckets_from_timestamps(timestamps, time_window, fallback_count=event_count)
    readiness = _preview_readiness("package_preview")
    preview = LoadPreview(
        dataset_id=str(meta.get("dataset_id") or package_path.name),
        header={
            "producer_ver": header_info.get("producer_ver", meta.get("parser_ver")),
            "run_id": header_info.get("run_id", meta.get("run_id")),
            "dict_ver": header_info.get("dict_ver", meta.get("dict_ver")),
        },
        time_window=time_window,
        chunk_count=chunk_count,
        record_count=event_count,
        core_ids=core_ids,
        lod0_buckets=lod0_buckets,
        source="package_preview",
        stage=str(readiness["stage"]),
        readiness=ReadinessState(**readiness),
        task_state_preview=(trace_preview or {}).get("task_state_preview"),
    )
    return ok_result(dataclass_to_dict(preview))


def _resolve_event_query_time_window(
    context: AnalysisContext,
    filter_spec: dict[str, Any] | None,
    explicit_time_window: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    if explicit_time_window is not None:
        return (float(explicit_time_window[0]), float(explicit_time_window[1]))
    active_filter = dict(filter_spec or {})
    filter_window = active_filter.get("time_window")
    if isinstance(filter_window, (list, tuple)) and len(filter_window) == 2:
        return (float(filter_window[0]), float(filter_window[1]))
    t_begin = active_filter.get("t_begin")
    t_end = active_filter.get("t_end")
    if t_begin is not None or t_end is not None:
        begin = float(t_begin) if t_begin is not None else 0.0
        end = float(t_end) if t_end is not None else begin
        return (begin, end)
    if context.time_window != (0.0, 0.0):
        return (float(context.time_window[0]), float(context.time_window[1]))
    return None


def _event_query_ready_state(bundle: RebuildBundle, source: str) -> dict[str, Any]:
    readiness = _query_ready_state(source=source, bundle=bundle)
    if source in {"trace_window_scan", "package_index", "event_page_cache"}:
        readiness["lod_ready"]["lod2"] = True
        readiness["view_ready"]["timeline"] = True
        readiness["view_ready"]["event_table"] = True
    return readiness


def _task_state_query_ready_state(bundle: RebuildBundle, source: str) -> dict[str, Any]:
    readiness = _query_ready_state(source=source, bundle=bundle)
    readiness["view_ready"]["task_states"] = True
    return readiness


class BackgroundJobManager:
    def __init__(self, on_job_state_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self._executors = {
            "input": concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rttrace-input"),
            "parse_rebuild": concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="rttrace-parse",
            ),
            "query": concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="rttrace-query"),
            "sidecar": concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rttrace-sidecar"),
            "evidence_query": concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="rttrace-evidence-query",
            ),
            "export": concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rttrace-export"),
            "default": concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="rttrace-job"),
        }
        self._on_job_state_change = on_job_state_change

    def _default_patent_payload(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        lane = str(normalized.get("lane") or PATENT_JOB_KIND_LANES.get(kind) or "").strip()
        if not lane:
            return normalized
        normalized.setdefault("lane", lane)
        normalized.setdefault("job_kind", kind)
        normalized.setdefault("state_contract_version", PATENT_JOB_CONTRACT_VERSION)
        normalized.setdefault("patent_job_state", "")
        normalized.setdefault("patent_state_seq", 0)
        normalized.setdefault("patent_state_updated_at", None)
        normalized.setdefault("patent_state_history", [])
        normalized.setdefault("finalized_mode", "n/a")
        normalized.setdefault("halt_reason", "")
        normalized.setdefault("error_code", "")
        normalized.setdefault("failed_at_state", "")
        normalized.setdefault("budget_rejected", False)
        normalized.setdefault("projected_next_events", None)
        normalized.setdefault("projected_next_bytes", None)
        normalized.setdefault("timeout_s", normalized.get("timeout_s"))
        normalized.setdefault("timed_out", False)
        normalized.setdefault("cancel_requested", False)
        normalized.setdefault("cancelled", False)
        normalized.setdefault("last_substage", "")
        normalized.setdefault("last_substage_status", "")
        return normalized

    def _apply_patent_payload_locked(
        self,
        job: dict[str, Any],
        *,
        patent_job_state: str | None = None,
        **fields: Any,
    ) -> None:
        payload = self._default_patent_payload(str(job["kind"]), dict(job["payload"]))
        if payload.get("state_contract_version") != PATENT_JOB_CONTRACT_VERSION:
            job["payload"] = payload
            return
        now = _iso_now()
        current_state = str(payload.get("patent_job_state") or "")
        next_state = str(patent_job_state or "").strip()
        if next_state and next_state != current_state:
            next_seq = int(payload.get("patent_state_seq") or 0) + 1
            payload["patent_job_state"] = next_state
            payload["patent_state_seq"] = next_seq
            payload["patent_state_updated_at"] = now
            history = list(payload.get("patent_state_history") or [])
            history.append(
                {
                    "seq": next_seq,
                    "state": next_state,
                    "updated_at": now,
                }
            )
            payload["patent_state_history"] = history[-_PATENT_JOB_HISTORY_LIMIT:]
        for key, value in fields.items():
            payload[key] = value
        job["payload"] = payload

    def _executor_group(self, kind: str) -> str:
        if kind == "input_prescan":
            return "input"
        if kind.startswith("sidecar_"):
            return "sidecar"
        if kind.startswith("evidence_query_"):
            return "evidence_query"
        if kind.startswith("query_"):
            return "query"
        if kind.startswith("export_"):
            return "export"
        if kind in {"load_dataset", "parse_rebuild"}:
            return "parse_rebuild"
        return "default"

    def _snapshot_locked(self, job_id: str) -> dict[str, Any]:
        job = self._jobs[job_id]
        return {
            "job_id": job["job_id"],
            "kind": job["kind"],
            "payload": dict(job["payload"]),
            "status": job["status"],
            "submitted_at": job["submitted_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "error": job["error"],
        }

    def _emit_state_change(self, snapshot: dict[str, Any]) -> None:
        if self._on_job_state_change is not None:
            self._on_job_state_change(snapshot)

    def create(self, kind: str, payload: dict[str, Any]) -> str:
        with self._lock:
            self._counter += 1
            job_id = f"{kind}-{self._counter}"
            self._jobs[job_id] = {
                "job_id": job_id,
                "kind": kind,
                "payload": self._default_patent_payload(kind, dict(payload)),
                "status": "created",
                "submitted_at": None,
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "future": None,
            }
        return job_id

    def update_patent_contract(
        self,
        job_id: str,
        *,
        patent_job_state: str | None = None,
        **fields: Any,
    ) -> Result[dict[str, Any]]:
        with self._lock:
            if job_id not in self._jobs:
                return err_result("INVALID_ARG", f"unknown background job: {job_id}")
            job = self._jobs[job_id]
            self._apply_patent_payload_locked(job, patent_job_state=patent_job_state, **fields)
            snapshot = self._snapshot_locked(job_id)
        self._emit_state_change(snapshot)
        return ok_result(snapshot)

    def update_payload(self, job_id: str, payload_delta: dict[str, Any]) -> Result[dict[str, Any]]:
        with self._lock:
            if job_id not in self._jobs:
                return err_result("INVALID_ARG", f"unknown background job: {job_id}")
            job = self._jobs[job_id]
            job["payload"] = {
                **job["payload"],
                **dict(payload_delta),
            }
            snapshot = self._snapshot_locked(job_id)
        self._emit_state_change(snapshot)
        return ok_result(snapshot)

    def request_cancel(self, job_id: str) -> Result[dict[str, Any]]:
        with self._lock:
            if job_id not in self._jobs:
                return err_result("INVALID_ARG", f"unknown background job: {job_id}")
            job = self._jobs[job_id]
            self._apply_patent_payload_locked(job, cancel_requested=True)
            job["payload"]["cancel_requested"] = True
            cancel_marker = None
            for key in ("parser_cancel_path", "parser_process_cancel_path", "cancel_path"):
                value = job["payload"].get(key)
                if value:
                    cancel_marker = Path(str(value)).expanduser().resolve()
                    break
            if cancel_marker is not None:
                marker_error = None
                try:
                    cancel_marker.parent.mkdir(parents=True, exist_ok=True)
                    cancel_marker.write_text(
                        json.dumps({"job_id": job_id, "cancel_requested_at": _iso_now()}, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                except OSError as exc:
                    marker_error = str(exc)
                marker_fields = {
                    "parser_cancel_marker_path": str(cancel_marker),
                    "parser_cancel_marker_written": marker_error is None,
                    "parser_cancel_marker_error": marker_error,
                }
                self._apply_patent_payload_locked(
                    job,
                    **marker_fields,
                )
                job["payload"].update(marker_fields)
            outcome: Result[Any] | None = None
            future = job.get("future")
            if job["status"] == "created":
                outcome = err_result("CANCELLED", f"background job cancelled before submit: {job_id}")
            elif future is not None and future.cancel():
                outcome = err_result("CANCELLED", f"background job cancelled before execution: {job_id}")
            if outcome is not None:
                current_state = str(job["payload"].get("patent_job_state") or "JOB-queued")
                cancel_fields = {
                    "cancelled": True,
                    "error_code": "CANCELLED",
                    "failed_at_state": current_state,
                }
                self._apply_patent_payload_locked(
                    job,
                    **cancel_fields,
                )
                job["payload"].update(cancel_fields)
                job["result"] = outcome
                job["error"] = outcome.message
                job["status"] = "failed"
                job["finished_at"] = _iso_now()
            snapshot = self._snapshot_locked(job_id)
        self._emit_state_change(snapshot)
        return ok_result(snapshot)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._jobs[job_id]["payload"])

    def submit(self, job_id: str, task, *args, **kwargs) -> Result[dict[str, Any]]:
        with self._lock:
            if job_id not in self._jobs:
                return err_result("INVALID_ARG", f"unknown background job: {job_id}")
            job = self._jobs[job_id]
            if job["status"] not in {"created", "failed"}:
                return err_result("INVALID_ARG", f"background job already submitted: {job_id}")
            job["status"] = "queued"
            job["submitted_at"] = _iso_now()
            job["error"] = None
            job["result"] = None
            self._apply_patent_payload_locked(job, patent_job_state="JOB-queued")
            executor = self._executors[self._executor_group(job["kind"])]
            job["future"] = executor.submit(self._run_job, job_id, task, args, kwargs)
            snapshot = self._snapshot_locked(job_id)
        self._emit_state_change(snapshot)
        return ok_result({"job_id": job_id, "status": "queued"})

    def _run_job(self, job_id: str, task, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Result[Any]:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = _iso_now()
            cancel_requested = bool(job["payload"].get("cancel_requested"))
            running_snapshot = self._snapshot_locked(job_id)
        self._emit_state_change(running_snapshot)
        if cancel_requested:
            outcome = err_result("CANCELLED", f"background job cancelled before run: {job_id}")
        else:
            start_monotonic = time.monotonic()
            try:
                outcome = task(*args, **kwargs)
                if not isinstance(outcome, Result):
                    outcome = ok_result(outcome)
            except Exception as exc:  # pragma: no cover - defensive path
                outcome = err_result("INTERNAL_ERROR", str(exc))
            elapsed_s = time.monotonic() - start_monotonic
            with self._lock:
                timeout_s_value = self._jobs[job_id]["payload"].get("timeout_s")
                cancel_requested = bool(self._jobs[job_id]["payload"].get("cancel_requested"))
            timeout_s = float(timeout_s_value) if timeout_s_value is not None else None
            if cancel_requested and outcome.ok:
                outcome = err_result("CANCELLED", f"background job cancelled during execution: {job_id}")
            elif timeout_s is not None and elapsed_s > timeout_s:
                outcome = err_result("TIMEOUT", f"background job exceeded timeout_s={timeout_s}: {job_id}")
        with self._lock:
            job = self._jobs[job_id]
            current_state = str(job["payload"].get("patent_job_state") or "JOB-queued")
            if outcome.ok:
                self._apply_patent_payload_locked(job, patent_job_state="JOB-completed")
            else:
                error_code = str(job["payload"].get("error_code") or outcome.code or "JOB_FAILED")
                fields: dict[str, Any] = {
                    "error_code": error_code,
                    "failed_at_state": current_state,
                }
                if error_code == "TIMEOUT":
                    fields["timed_out"] = True
                if error_code == "CANCELLED":
                    fields["cancelled"] = True
                self._apply_patent_payload_locked(job, **fields)
            job["result"] = outcome
            job["error"] = None if outcome.ok else outcome.message
            job["status"] = "succeeded" if outcome.ok else "failed"
            job["finished_at"] = _iso_now()
            finished_snapshot = self._snapshot_locked(job_id)
        self._emit_state_change(finished_snapshot)
        return outcome

    def status(self, job_id: str) -> Result[dict[str, Any]]:
        with self._lock:
            if job_id not in self._jobs:
                return err_result("INVALID_ARG", f"unknown background job: {job_id}")
            return ok_result(self._snapshot_locked(job_id))

    def result(self, job_id: str) -> Result[Any]:
        with self._lock:
            if job_id not in self._jobs:
                return err_result("INVALID_ARG", f"unknown background job: {job_id}")
            job = self._jobs[job_id]
            outcome = job["result"]
            status = job["status"]
        if status not in {"succeeded", "failed"} or outcome is None:
            return err_result("NOT_READY", f"background job not finished: {job_id}")
        return outcome


class BookmarkService:
    def __init__(self) -> None:
        self._bookmarks: dict[str, Bookmark] = {}
        self._counter = 0

    def create(self, label: str, context: AnalysisContext, evidence_anchor: dict[str, Any] | None) -> Bookmark:
        self._counter += 1
        bookmark = Bookmark(
            bookmark_id=f"bookmark-{self._counter}",
            label=label,
            time_window=context.time_window,
            filter=dict(context.filter),
            selection=dict(context.selection),
            focused_view=context.focused_view,
            evidence_anchor=evidence_anchor,
        )
        self._bookmarks[bookmark.bookmark_id] = bookmark
        return bookmark

    def list(self) -> list[Bookmark]:
        return list(self._bookmarks.values())

    def get(self, bookmark_id: str) -> Bookmark:
        return self._bookmarks[bookmark_id]

    def delete(self, bookmark_id: str) -> None:
        self._bookmarks.pop(bookmark_id, None)


class WorkspaceController:
    def __init__(self, cache_budget_mb: float = 8.0) -> None:
        self.repository = DatasetRepository(cache_budget_mb=cache_budget_mb)
        self.context_store = ContextStore()
        self.bookmarks = BookmarkService()
        self.jobs = BackgroundJobManager(self._sync_pending_jobs)
        self.workspace_id = "workspace-1"
        self.active_dataset_id: str | None = None
        self._resolved_load_jobs: dict[str, str] = {}

    def _sync_pending_jobs(self, snapshot: dict[str, Any]) -> None:
        current = self.context_store.get()
        pending_jobs = [job_id for job_id in current.pending_jobs if job_id != snapshot["job_id"]]
        if snapshot["status"] in {"queued", "running"}:
            pending_jobs.append(snapshot["job_id"])
        self.context_store.commit({"pending_jobs": pending_jobs})
        self._sync_background_sidecar_prebuild(snapshot)

    def _sync_background_sidecar_prebuild(self, snapshot: dict[str, Any]) -> None:
        payload = dict(snapshot.get("payload") or {})
        if not bool(payload.get("background_sidecar_prebuild")):
            return
        dataset_id = str(payload.get("dataset_id") or "").strip()
        activation_id = str(payload.get("activation_id") or "").strip()
        if not dataset_id or not activation_id or not self.repository.has(dataset_id):
            return
        record = self.repository.get(dataset_id)
        if record.activation_id != activation_id:
            return
        metadata = {
            "job_id": snapshot["job_id"],
            "dataset_id": dataset_id,
            "activation_id": activation_id,
            "output_path": payload.get("output_path"),
            "runtime_load_effective_plan": payload.get("runtime_load_effective_plan"),
        }
        if snapshot["status"] in {"queued", "running"}:
            metadata["state"] = snapshot["status"]
            record.background_sidecar_prebuild = metadata
            return
        result = self.jobs.result(snapshot["job_id"])
        if result.ok and isinstance(result.data, dict):
            result_payload = dict(result.data)
            metadata.update(
                {
                    "state": "completed",
                    "snapshot_id": result_payload.get("snapshot_id"),
                    "dependency_sidecar_path": result_payload.get("dependency_sidecar_path"),
                    "sidecar_manifest_path": result_payload.get("sidecar_manifest_path"),
                    "sidecar_index_path": result_payload.get("sidecar_index_path"),
                    "sidecar_index_ticket_path": result_payload.get("sidecar_index_ticket_path"),
                    "prebuild_stage_breakdown": result_payload.get("prebuild_stage_breakdown"),
                    "prebuild_path_features": result_payload.get("prebuild_path_features"),
                    "prebuild_diagnostics": result_payload.get("prebuild_diagnostics"),
                }
            )
        else:
            metadata.update(
                {
                    "state": "cancelled" if result.code == "CANCELLED" else "failed",
                    "error_code": result.code,
                }
            )
        record.background_sidecar_prebuild = metadata

    def _active_record(self, dataset_id: str | None = None) -> DatasetRecord:
        target_id = dataset_id or self.active_dataset_id
        if target_id is None or not self.repository.has(target_id):
            raise KeyError("no active dataset")
        return self.repository.get(target_id)

    def viz_InitWorkspace(self) -> Result[str]:
        return ok_result(self.workspace_id)

    def _register_artifact(
        self,
        artifact: DatasetArtifact,
        *,
        dataset_role: str | None = None,
        runtime_load_effective_plan: dict[str, Any] | None = None,
    ) -> Result[str]:
        session = metric_Init().data
        ingest = metric_Ingest(session, artifact.bundle)
        if not ingest.ok:
            return Result(
                code=ingest.code,
                message=ingest.message,
                warnings=ingest.warnings,
                untrusted_windows=ingest.untrusted_windows,
            )
        record = DatasetRecord(
            artifact=artifact,
            metric_session=session,
            activation_id=uuid.uuid4().hex,
        )
        dataset_id = self.repository.add(record)
        self.active_dataset_id = dataset_id
        bundle = artifact.bundle
        role = _infer_dataset_role(dataset_id, dataset_role)
        context_payload = {"dataset_role": role}
        time_window = _bundle_time_window(bundle)
        if time_window != (0.0, 0.0):
            context_payload["time_window"] = time_window
        self.context_store.commit(context_payload)
        self._submit_background_sidecar_prebuild(record, runtime_load_effective_plan)
        return ok_result(dataset_id)

    def _submit_background_sidecar_prebuild(
        self,
        record: DatasetRecord,
        runtime_load_effective_plan: dict[str, Any] | None,
    ) -> None:
        plan = dict(runtime_load_effective_plan or {})
        if not bool(plan.get("background_sidecar_prebuild")):
            return
        if str(record.background_sidecar_prebuild.get("state") or "") in {"created", "queued", "running", "completed"}:
            return
        output_path = Path(tempfile.gettempdir()) / "rttrace-sidecar-prebuild" / record.activation_id
        context = dict(self.context_store.get().persisted_dict())
        payload = {
            "dataset_id": record.artifact.dataset_id,
            "context": context,
            "lane": "sidecar_build",
            "background_sidecar_prebuild": True,
            "activation_id": record.activation_id,
            "output_path": str(output_path),
            "runtime_load_effective_plan": plan,
        }
        job_id = self.jobs.create("sidecar_build", payload)
        record.background_sidecar_prebuild = {
            "state": "created",
            "job_id": job_id,
            "dataset_id": record.artifact.dataset_id,
            "activation_id": record.activation_id,
            "output_path": str(output_path),
            "runtime_load_effective_plan": plan,
        }
        submitted = self.jobs.submit(
            job_id,
            ExportService(self.repository, self.context_store, self.jobs).sidecar_WriteArtifacts,
            job_id,
            str(output_path),
        )
        if not submitted.ok:
            record.background_sidecar_prebuild = {
                **record.background_sidecar_prebuild,
                "state": "failed",
                "error_code": submitted.code,
            }

    def _load_artifact_from_source(
        self,
        source: str,
        *,
        job_id: str | None = None,
        load_artifact: bool = True,
        parser_artifact_dir: str | Path | None = None,
        parser_cancel_path: str | Path | None = None,
        parser_process_timeout_s: float | None = None,
        payload_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Result[dict[str, Any]]:
        path = Path(source)
        if path.is_dir() and (path / "manifest.json").exists() and (path / "meta.json").exists():
            loaded_package = _load_validated_package(path)
            if not loaded_package.ok:
                return loaded_package
            bundle = loaded_package.data["bundle"]
            dataset_role = loaded_package.data["dataset_role"]
            header = bundle.header or GlobalHeader(
                magic="0x0",
                endian=1,
                time_unit=1,
                clock_source=1,
                format_ver=1,
                dict_ver=1,
                producer_ver="repro",
                run_id=bundle.dataset_id,
            )
            artifact = DatasetArtifact(
                dataset_id=_role_dataset_id(bundle.dataset_id, dataset_role),
                source=str(path),
                header=header,
                bundle=bundle,
                dictionary_info=dict(loaded_package.data.get("dictionary_info") or {}),
            )
            return ok_result(
                {
                    "artifact": artifact,
                    "dataset_role": dataset_role,
                    "runtime_load_plan": None,
                    "runtime_load_gate_result": None,
                    "runtime_load_effective_plan": None,
                    "parser_process_agent": None,
                    "artifact_handle": None,
                    "parser_process_artifact": None,
                    "child_agent_contract": None,
                },
                warnings=loaded_package.warnings,
                untrusted_windows=loaded_package.untrusted_windows,
            )

        return self._parse_trace_source_isolated(
            path,
            job_id=job_id or f"sync-load-{uuid.uuid4().hex}",
            load_artifact=load_artifact,
            artifact_dir=parser_artifact_dir,
            cancel_path=parser_cancel_path,
            timeout_s=parser_process_timeout_s,
            payload_callback=payload_callback,
        )

    def _parse_trace_source_isolated(
        self,
        source: str | Path,
        *,
        job_id: str,
        load_artifact: bool = True,
        artifact_dir: str | Path | None = None,
        cancel_path: str | Path | None = None,
        timeout_s: float | None = None,
        payload_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Result[dict[str, Any]]:
        source_path = Path(source)
        dictionary_path = _resolve_evidence_dictionary_source_path(str(source_path))
        parser_cache_root = Path(tempfile.gettempdir()) / "rttrace-parser-agent"
        runtime_load_plan = RuntimeOptimizationAdvisor().plan_load(
            current_request_features=_trace_runtime_load_features(source_path)
        )
        runtime_load_plan_payload = _runtime_load_plan_payload(runtime_load_plan)
        runtime_load_gate = gate_ValidateRuntimeLoadPlan(runtime_load_plan=runtime_load_plan)
        runtime_load_gate_payload = (
            runtime_load_gate.data.to_dict()
            if runtime_load_gate.data is not None and hasattr(runtime_load_gate.data, "to_dict")
            else runtime_load_gate.data
        )
        runtime_load_effective_plan_payload = _runtime_load_plan_payload(
            runtime_load_plan
            if runtime_load_gate.ok
            else _full_safe_runtime_load_plan(f"gate rejected runtime load plan: {runtime_load_gate.code}")
        )
        parser_artifact_present = False
        try:
            parser_probe = build_parser_cache_bindings(
                source_path,
                dictionary_path=dictionary_path,
                parser_version=PARSER_VERSION,
                schema_version=PARSER_CACHE_SCHEMA_VERSION,
                index_build_mode=str(runtime_load_effective_plan_payload.get("index_build_mode") or "full"),
                materialize_event_stream=bool(runtime_load_effective_plan_payload.get("materialize_event_stream", True)),
            )
        except (OSError, ValueError):
            parser_probe = None
        if parser_probe is not None:
            probe_root = parser_cache_root / str(parser_probe["cache_key"])
            parser_artifact_present = (probe_root / "parse_rebuild_artifact.pickle").exists() and (
                probe_root / "parse_rebuild_result.json"
            ).exists()
            if parser_artifact_present:
                runtime_load_gate = gate_ValidateRuntimeLoadPlan(
                    runtime_load_plan=runtime_load_plan,
                    parser_artifact_present=True,
                )
                runtime_load_gate_payload = (
                    runtime_load_gate.data.to_dict()
                    if runtime_load_gate.data is not None and hasattr(runtime_load_gate.data, "to_dict")
                    else runtime_load_gate.data
                )
                runtime_load_effective_plan_payload = _runtime_load_plan_payload(
                    runtime_load_plan
                    if runtime_load_gate.ok
                    else _full_safe_runtime_load_plan(f"gate rejected runtime load plan: {runtime_load_gate.code}")
                )
        if payload_callback is not None:
            payload_callback(
                {
                    "runtime_load_plan": runtime_load_plan_payload,
                    "runtime_load_gate_result": runtime_load_gate_payload,
                    "runtime_load_effective_plan": runtime_load_effective_plan_payload,
                }
            )

        parser_agent = ParserProcessAgent(job_id=job_id)
        parsed = parser_agent.parse_rebuild(
            source_path,
            artifact_policy={
                "artifact_dir": str(artifact_dir or (Path(tempfile.gettempdir()) / "rttrace-parser-agent" / job_id)),
                "cancel_path": str(cancel_path) if cancel_path is not None else None,
                "timeout_s": timeout_s,
                "materialize_event_stream": bool(runtime_load_effective_plan_payload.get("materialize_event_stream", True)),
                "index_build_mode": str(runtime_load_effective_plan_payload.get("index_build_mode") or "full"),
                "dictionary_path": str(dictionary_path),
                "parser_version": PARSER_VERSION,
                "schema_version": PARSER_CACHE_SCHEMA_VERSION,
                "cache_enabled": True,
                "cache_root": str(parser_cache_root),
                "load_artifact": load_artifact,
                "retain_artifact": True,
            },
        )
        parser_artifact_payload = (parsed.data or {}).get("parser_process_artifact") if parsed.data else None
        if (
            parsed.ok
            and runtime_load_gate.ok
            and isinstance(parser_artifact_payload, dict)
            and bool(parser_artifact_payload.get("cache_hit"))
        ):
            runtime_load_effective_plan_payload = {
                **runtime_load_effective_plan_payload,
                "load_mode": "hot_reuse",
                "try_parser_artifact_reuse": True,
            }
        result_payload = {
            "artifact": (parsed.data or {}).get("artifact") if parsed.data else None,
            "dataset_role": None,
            "runtime_load_plan": runtime_load_plan_payload,
            "runtime_load_gate_result": runtime_load_gate_payload,
            "runtime_load_effective_plan": runtime_load_effective_plan_payload,
            "parser_process_agent": parser_agent.contract.to_dict(),
            "artifact_handle": (parsed.data or {}).get("artifact_handle") if parsed.data else None,
            "parser_process_artifact": parser_artifact_payload,
            "child_agent_contract": (parsed.data or {}).get("child_agent_contract") if parsed.data else None,
        }
        if payload_callback is not None:
            payload_callback(
                {
                    "parser_process_agent": result_payload["parser_process_agent"],
                    "artifact_handle": result_payload["artifact_handle"],
                    "parser_process_artifact": result_payload["parser_process_artifact"],
                    "child_agent_contract": result_payload["child_agent_contract"],
                }
            )
        if not parsed.ok:
            return Result(
                code=parsed.code,
                message=parsed.message,
                data=result_payload,
                warnings=parsed.warnings,
                untrusted_windows=parsed.untrusted_windows,
            )
        return ok_result(
            result_payload,
            warnings=parsed.warnings,
            untrusted_windows=parsed.untrusted_windows,
        )

    def _build_load_preview(self, source: str) -> Result[dict[str, Any]]:
        path = Path(source)
        if path.is_dir() and (path / "manifest.json").exists() and (path / "meta.json").exists():
            return _preview_package(path)
        prescan = prs_Prescan(source)
        if not prescan.ok:
            return Result(
                code=prescan.code,
                message=prescan.message,
                warnings=prescan.warnings,
                untrusted_windows=prescan.untrusted_windows,
            )
        return prescan

    def _load_stage_payload(
        self,
        stage: str,
        source: str,
        preview: dict[str, Any] | None,
        *,
        job_kind: str,
        readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective_readiness = dict(
            readiness
            or _readiness_state(
                stage=stage,
                source=source,
                preview=preview is not None,
            )
        )
        return {
            "source": source,
            "preview": preview,
            "stage": stage,
            "job_kind": job_kind,
            "readiness": effective_readiness,
            "view_ready": dict(effective_readiness.get("view_ready") or {}),
        }

    def _submit_query_job(
        self,
        kind: str,
        payload: dict[str, Any],
        query_callable: Callable[[], Result[Any]],
    ) -> Result[dict[str, Any]]:
        job_id = self.jobs.create(
            kind,
            {
                **payload,
                "stage": "queued",
                "job_kind": kind,
                "readiness": _readiness_state(stage="queued", source=kind, preview=False),
                "view_ready": _view_ready_state(),
            },
        )

        def run_query() -> Result[Any]:
            self.jobs.update_payload(
                job_id,
                {
                    "stage": "running",
                    "job_kind": kind,
                    "readiness": _readiness_state(stage="running", source=kind, preview=False),
                    "view_ready": _view_ready_state(),
                },
            )
            result = query_callable()
            if result.ok:
                readiness = _readiness_state(stage="query_ready", source=kind, preview=False)
                view_ready = dict(readiness.get("view_ready") or {})
                summary = getattr(result.data, "summary", None)
                if isinstance(summary, dict) and isinstance(summary.get("readiness"), dict):
                    readiness = dict(summary["readiness"])
                    readiness["stage"] = "query_ready"
                    view_ready = dict(readiness.get("view_ready") or {})
                self.jobs.update_payload(
                    job_id,
                    {
                        "stage": "query_ready",
                        "job_kind": kind,
                        "readiness": readiness,
                        "view_ready": view_ready,
                    },
                )
            return result

        submitted = self.jobs.submit(job_id, run_query)
        if not submitted.ok:
            return submitted
        return ok_result(
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "job_kind": kind,
                "readiness": _readiness_state(stage="queued", source=kind, preview=False),
                "view_ready": _view_ready_state(),
            }
        )

    def viz_LoadDataset(self, source: str) -> Result[str]:
        loaded = self._load_artifact_from_source(source)
        if not loaded.ok:
            return loaded
        registered = self._register_artifact(
            loaded.data["artifact"],
            dataset_role=loaded.data["dataset_role"],
            runtime_load_effective_plan=loaded.data.get("runtime_load_effective_plan"),
        )
        if not registered.ok:
            return registered
        return ok_result(registered.data, warnings=loaded.warnings, untrusted_windows=loaded.untrusted_windows)

    def viz_LoadDatasetAsync(self, source: str) -> Result[dict[str, Any]]:
        source_path = str(Path(source))
        preview_result = self._build_load_preview(source_path)
        if not preview_result.ok:
            return preview_result
        preview = preview_result.data
        readiness = dict(preview.get("readiness") or _preview_readiness(str(preview.get("source") or "prescan")))
        stage = str(preview.get("stage") or readiness.get("stage") or "preview_ready")
        job_id = self.jobs.create(
            "load_dataset",
            self._load_stage_payload(
                stage,
                source_path,
                preview,
                job_kind="input_prescan",
                readiness=readiness,
            ),
        )
        parser_artifact_dir = Path(tempfile.gettempdir()) / "rttrace-parser-agent" / job_id
        parser_cancel_path = parser_artifact_dir / "cancel.flag"
        self.jobs.update_payload(
            job_id,
            {
                "parser_artifact_dir": str(parser_artifact_dir),
                "parser_cancel_path": str(parser_cancel_path),
                "runtime_load_plan": None,
                "runtime_load_gate_result": None,
                "runtime_load_effective_plan": None,
                "parser_process_agent": None,
                "artifact_handle": None,
                "parser_process_artifact": None,
                "child_agent_contract": None,
            },
        )

        def run_load() -> Result[dict[str, Any]]:
            parse_readiness = _readiness_state(
                stage="parse_rebuild",
                source=str(preview.get("source") or "prescan"),
                preview=preview is not None,
                lod_ready=dict(readiness.get("lod_ready") or _lod_ready_state()),
                view_ready=dict(readiness.get("view_ready") or _view_ready_state()),
            )
            self.jobs.update_payload(
                job_id,
                self._load_stage_payload(
                    "parse_rebuild",
                    source_path,
                    preview,
                    job_kind="parse_rebuild",
                    readiness=parse_readiness,
                ),
            )
            job_payload = self.jobs.get(job_id)
            loaded = self._load_artifact_from_source(
                source_path,
                job_id=job_id,
                parser_artifact_dir=job_payload.get("parser_artifact_dir"),
                parser_cancel_path=job_payload.get("parser_cancel_path"),
                parser_process_timeout_s=job_payload.get("parser_process_timeout_s"),
                payload_callback=lambda payload: self.jobs.update_payload(job_id, payload),
            )
            if isinstance(loaded.data, dict):
                self.jobs.update_payload(
                    job_id,
                    {
                        "runtime_load_plan": loaded.data.get("runtime_load_plan"),
                        "runtime_load_gate_result": loaded.data.get("runtime_load_gate_result"),
                        "runtime_load_effective_plan": loaded.data.get("runtime_load_effective_plan"),
                        "parser_process_agent": loaded.data.get("parser_process_agent"),
                        "artifact_handle": loaded.data.get("artifact_handle"),
                        "parser_process_artifact": loaded.data.get("parser_process_artifact"),
                        "child_agent_contract": loaded.data.get("child_agent_contract"),
                    },
                )
            if loaded.ok:
                query_readiness = _query_ready_state(source="materialized_bundle", bundle=loaded.data["artifact"].bundle)
                self.jobs.update_payload(
                    job_id,
                    self._load_stage_payload(
                        "query_ready",
                        source_path,
                        preview,
                        job_kind="parse_rebuild",
                        readiness=query_readiness,
                    ),
                )
            return loaded

        submitted = self.jobs.submit(job_id, run_load)
        if not submitted.ok:
            return submitted
        return ok_result(
            {
                "job_id": job_id,
                "status": "queued",
                "source": source_path,
                "preview": preview,
                "readiness": readiness,
                "stage": stage,
                "job_kind": "input_prescan",
                "view_ready": dict(readiness.get("view_ready") or {}),
                "parser_artifact_dir": str(parser_artifact_dir),
                "parser_cancel_path": str(parser_cancel_path),
            }
        )

    def viz_ResolveLoadDatasetJob(self, job_id: str) -> Result[str]:
        if job_id in self._resolved_load_jobs:
            return ok_result(self._resolved_load_jobs[job_id])
        loaded = self.jobs.result(job_id)
        if not loaded.ok:
            return loaded
        registered = self._register_artifact(
            loaded.data["artifact"],
            dataset_role=loaded.data["dataset_role"],
            runtime_load_effective_plan=loaded.data.get("runtime_load_effective_plan") if isinstance(loaded.data, dict) else None,
        )
        if not registered.ok:
            return registered
        self._resolved_load_jobs[job_id] = registered.data
        return ok_result(
            registered.data,
            warnings=loaded.warnings,
            untrusted_windows=loaded.untrusted_windows,
        )

    def viz_LoadDatasetFromChannel(self, channel_type: str, config: dict[str, Any]) -> Result[str]:
        try:
            if channel_type == "file":
                source = FileChunkSource(
                    config["path"],
                    read_size=int(config.get("read_size", 4096)),
                    core_id=config.get("core_id"),
                )
            elif channel_type == "socket":
                source = SocketChunkSource(
                    str(config.get("host", "127.0.0.1")),
                    int(config["port"]),
                    recv_size=int(config.get("recv_size", 4096)),
                    timeout_s=float(config["timeout_s"]) if "timeout_s" in config and config["timeout_s"] is not None else 5.0,
                    core_id=config.get("core_id"),
                )
            elif channel_type == "serial":
                source = SerialChunkSource(
                    str(config["device"]),
                    read_size=int(config.get("read_size", 4096)),
                    timeout_s=float(config["timeout_s"]) if "timeout_s" in config and config["timeout_s"] is not None else 1.0,
                    baudrate=int(config.get("baudrate", 115200)),
                    core_id=config.get("core_id"),
                )
            else:
                return err_result("INVALID_ARG", f"unsupported channel type: {channel_type}")
        except (KeyError, TypeError, ValueError, ChannelError) as exc:
            return err_result("INVALID_ARG", str(exc))

        loaded = load_dataset_from_chunks(
            source,
            source=source.source_label,
            cfg={
                "dataset_id": config.get("dataset_id"),
                "online_mode": True,
            },
        )
        if not loaded.ok:
            return Result(
                code=loaded.code,
                message=loaded.message,
                warnings=loaded.warnings,
                untrusted_windows=loaded.untrusted_windows,
            )
        return self._register_artifact(loaded.data)

    def viz_SetContext(self, context_delta: dict[str, Any]) -> Result[AnalysisContext]:
        return ok_result(self.context_store.commit(context_delta))

    def viz_ApplySelection(self, selection: dict[str, Any]) -> Result[AnalysisContext]:
        return ok_result(self.context_store.commit({"selection": selection}))

    def viz_SetHoverTarget(
        self,
        hover_target: dict[str, Any] | None,
        *,
        transient_selection: dict[str, Any] | None = None,
    ) -> Result[AnalysisContext]:
        return ok_result(
            self.context_store.set_hover_target(
                hover_target,
                transient_selection=transient_selection,
            )
        )

    def viz_SetTransientSelection(
        self,
        transient_selection: dict[str, Any] | None,
        *,
        hover_target: dict[str, Any] | None = None,
    ) -> Result[AnalysisContext]:
        return ok_result(
            self.context_store.set_transient_selection(
                transient_selection,
                hover_target=hover_target,
            )
        )

    def viz_CommitTransientSelection(self) -> Result[AnalysisContext]:
        return ok_result(self.context_store.commit_transient_selection())

    def viz_CancelTransientSelection(self, *, clear_hover_target: bool = True) -> Result[AnalysisContext]:
        return ok_result(self.context_store.clear_transient_selection(clear_hover_target=clear_hover_target))

    def viz_QueryTimelineLOD(self, scope: dict[str, Any]) -> Result[TimelinePayload]:
        record = self._active_record(scope.get("dataset_id"))
        t_begin, t_end = scope["time_window"]
        filter_spec = scope.get("filter", {})
        lod = int(scope.get("lod", 1))
        slices = [
            item for item in record.artifact.bundle.exec_slices if item.t_begin < t_end and item.t_end > t_begin
        ]
        irqs = [
            item for item in record.artifact.bundle.irq_spans if item.t_begin < t_end and item.t_end > t_begin
        ]
        switch_points = [
            SwitchPoint(
                switch_id=f"switch:{event.event_uid}",
                core_id=event.core_id,
                timestamp=event.timestamp_aligned,
                prev_task_id=event.payload.get("prev_task_id"),
                next_task_id=event.payload.get("next_task_id"),
                reason=event.payload.get("reason"),
                event_ref=event.ref_key,
                trusted=not bool(event.trust_tags),
            )
            for event in record.artifact.bundle.event_stream
            if event.event_name == "CTX_SWITCH" and t_begin <= event.timestamp_aligned <= t_end
        ]
        if "task_id" in filter_spec:
            slices = [item for item in slices if item.task_id == filter_spec["task_id"]]
            switch_points = [
                item
                for item in switch_points
                if item.prev_task_id == filter_spec["task_id"] or item.next_task_id == filter_spec["task_id"]
            ]
        if "core_id" in filter_spec:
            slices = [item for item in slices if item.core_id == filter_spec["core_id"]]
            irqs = [item for item in irqs if item.core_id == filter_spec["core_id"]]
            switch_points = [item for item in switch_points if item.core_id == filter_spec["core_id"]]
        if "irq_id" in filter_spec:
            irqs = [item for item in irqs if item.irq_id == filter_spec["irq_id"]]
        if lod == 0:
            bucket_result = query_timeline_buckets(
                record.artifact.bundle,
                (float(t_begin), float(t_end)),
                bucket_count=max(8, min(48, int(scope.get("bucket_count", 16)))),
                filter_spec=filter_spec,
            )
            fallback_reason = None
            if bucket_result["summary"]["fallback"]:
                fallback_reason = "filter_requires_bundle_scan"
            readiness = _query_ready_state(
                source=bucket_result["summary"]["source"],
                bundle=record.artifact.bundle,
                fallback_reason=fallback_reason,
            )
            return ok_result(
                TimelinePayload(
                    lod=lod,
                    window=(t_begin, t_end),
                    buckets=bucket_result["buckets"],
                    summary={
                        "render_mode": "bucket",
                        "bucket_count": len(bucket_result["buckets"]),
                        "bucket_size": bucket_result["summary"]["bucket_size"],
                        "source": bucket_result["summary"]["source"],
                        "fallback": bucket_result["summary"]["fallback"],
                        "readiness": readiness,
                    },
                )
            )
        if lod == 2:
            event_page = self._query_event_page(
                record,
                filter_spec,
                None,
                int(scope.get("event_limit", 200)),
                (float(t_begin), float(t_end)),
                namespace="timeline",
            )
            if not event_page.ok:
                return event_page
            event_rows = [
                {
                    "event_uid": event.event_uid,
                    "ref_key": event.ref_key,
                    "timestamp": event.timestamp_aligned,
                    "event_name": event.event_name,
                    "task_id": event.task_id,
                    "core_id": event.core_id,
                    "obj_id": event.obj_id,
                    "irq_id": event.irq_id,
                    "trusted": not bool(event.trust_tags),
                }
                for event in event_page.data.items
            ]
            page_source = str(event_page.data.summary.get("source", "trace_window_scan"))
            readiness = _event_query_ready_state(record.artifact.bundle, page_source)
            return ok_result(
                TimelinePayload(
                    lod=lod,
                    window=(t_begin, t_end),
                    events=event_rows,
                    summary={
                        "render_mode": "event",
                        "event_count": len(event_rows),
                        "truncated": event_page.data.has_more,
                        "source": page_source,
                        "cache_hit": bool(event_page.data.summary.get("cache_hit")),
                        "cache_source": event_page.data.summary.get("cache_source"),
                        "evicted": list(event_page.data.summary.get("evicted") or []),
                        "fallback_reason": event_page.data.summary.get("fallback_reason"),
                        "readiness": readiness,
                    },
                )
            )
        readiness = _query_ready_state(source="materialized_bundle", bundle=record.artifact.bundle)
        return ok_result(
            TimelinePayload(
                lod=lod,
                window=(t_begin, t_end),
                slices=slices,
                switch_points=switch_points,
                irq_spans=irqs,
                summary={
                    "render_mode": "slice",
                    "slice_count": len(slices),
                    "switch_count": len(switch_points),
                    "irq_count": len(irqs),
                    "source": "materialized_bundle",
                    "readiness": readiness,
                },
            )
        )

    def viz_QueryTaskStates(self, query: TaskStateQuery) -> Result[TaskStateViewModel]:
        normalized = _normalize_task_state_query(query)
        record = self._active_record(normalized.dataset_id)
        bundle = record.artifact.bundle
        cache_key = _task_state_cache_key(normalized)
        dataset_id = record.artifact.dataset_id
        cached = self.repository.query_cache.get(dataset_id, "task_states", cache_key)
        if cached is not None:
            cached_payload = copy.deepcopy(cached.payload)
            cached_payload.summary["cache_hit"] = True
            cached_payload.summary["cache_source"] = _cache_source_label("task_states")
            cached_payload.summary["evicted"] = []
            return ok_result(cached_payload)
        if record.task_state_window_index is None:
            if not bundle.task_states:
                return err_result("NOT_READY", "task state query data is not ready")
            record.task_state_window_index = build_task_state_window_index(bundle)
        segments = query_task_state_segments(
            record.task_state_window_index,
            normalized.time_window,
            list(normalized.state_mask),
            normalized.runtime_filter,
        )
        event_by_uid = {event.event_uid: event for event in bundle.event_stream}
        lane_group = normalized.lane_group
        relevant_windows = [
            window for window in bundle.untrusted_windows if _window_intersects(window, normalized.time_window)
        ]
        lane_order: list[str] = []
        row_order: list[tuple[int, str]] = []
        row_segments: dict[tuple[int, str], list[TaskStateViewSegment]] = {}
        row_state_counts: dict[tuple[int, str], dict[str, int]] = {}
        state_legend: dict[str, str] = {}
        for segment in segments:
            lane_label, core_id = _task_state_lane_label(segment, lane_group, event_by_uid)
            if lane_label not in lane_order:
                lane_order.append(lane_label)
            row_key = (segment.task_id, lane_label)
            if row_key not in row_segments:
                row_order.append(row_key)
                row_segments[row_key] = []
                row_state_counts[row_key] = {}
            segment_window = (segment.t_begin, segment.t_end)
            view_segment = TaskStateViewSegment(
                seg_id=segment.seg_id,
                state=segment.state,
                t_begin=segment.t_begin,
                t_end=segment.t_end,
                evidence_ref=EvidenceRef(
                    ref_type="slice",
                    ref_key=segment.cause_event,
                    t_begin=segment.t_begin,
                    t_end=segment.t_end,
                ),
                trusted=bool(segment.trusted and _task_state_window_trusted(segment_window, relevant_windows)),
                task_id=segment.task_id,
                core_id=core_id,
                related_obj=segment.related_obj,
                cause_event=segment.cause_event,
            )
            row_segments[row_key].append(view_segment)
            row_state_counts[row_key][segment.state] = row_state_counts[row_key].get(segment.state, 0) + 1
            state_legend.setdefault(segment.state, _task_state_display_label(segment.state))
        rows: list[TaskStateViewRow] = []
        for task_id, lane_label in row_order:
            key = (task_id, lane_label)
            segments_for_row = sorted(
                row_segments[key],
                key=lambda item: (item.t_begin, item.t_end, item.seg_id),
            )
            if segments_for_row:
                row_window = (
                    min(item.t_begin for item in segments_for_row),
                    max(item.t_end for item in segments_for_row),
                )
            else:
                row_window = normalized.time_window
            row_trusted = bool(
                segments_for_row
                and all(item.trusted for item in segments_for_row)
                and _task_state_window_trusted(row_window, relevant_windows)
            )
            rows.append(
                TaskStateViewRow(
                    task_id=task_id,
                    lane_label=lane_label,
                    segments=segments_for_row,
                    state_counts=dict(sorted(row_state_counts[key].items())),
                    time_window=row_window,
                    trusted=row_trusted,
                )
            )
        trusted_rows = sum(1 for row in rows if row.trusted)
        trusted_segments = sum(1 for row in rows for item in row.segments if item.trusted)
        query_trusted = bool(
            _task_state_window_trusted(normalized.time_window, relevant_windows)
            and all(row.trusted for row in rows)
        )
        readiness = _task_state_query_ready_state(bundle, "task_state_window_index")
        result = TaskStateViewModel(
            time_window=normalized.time_window,
            lane_order=lane_order,
            rows=rows,
            state_legend=state_legend,
            summary={
                "segment_count": len(segments),
                "row_count": len(rows),
                "source": "task_state_window_index",
                "cache_hit": False,
                "cache_source": None,
                "evicted": [],
                "readiness": readiness,
                "trusted_summary": {
                    "trusted_rows": trusted_rows,
                    "untrusted_rows": len(rows) - trusted_rows,
                    "trusted_segments": trusted_segments,
                    "untrusted_segments": len(segments) - trusted_segments,
                    "untrusted_window_count": len(relevant_windows),
                },
            },
            cursor_hint=_task_state_cursor_hint(self.context_store.get(), normalized.anchor_ref),
            trusted=query_trusted,
        )
        evicted = self.repository.query_cache.put(
            dataset_id,
            "task_states",
            cache_key,
            copy.deepcopy(result),
            byte_size_estimate=_estimate_task_state_view_bytes(result),
            source="task_state_window_index",
        )
        result.summary["evicted"] = _cache_eviction_summary(evicted)
        return ok_result(result)

    def _query_event_page(
        self,
        record: DatasetRecord,
        filter_spec: dict[str, Any] | None,
        cursor: EventCursor | None,
        limit: int,
        time_window: tuple[float, float] | None = None,
        *,
        namespace: str = "event_table",
        direction: str = "forward",
    ) -> Result[EventPage]:
        normalized_direction = "backward" if direction == "backward" else "forward"
        active_filter = dict(filter_spec or {})
        resolved_window = _resolve_event_query_time_window(self.context_store.get(), active_filter, time_window)
        if resolved_window is not None:
            active_filter["t_begin"] = float(resolved_window[0])
            active_filter["t_end"] = float(resolved_window[1])
        dataset_id = record.artifact.dataset_id
        cache_key = _event_page_cache_key(active_filter, cursor, limit, resolved_window, normalized_direction)
        cached = self.repository.query_cache.get(dataset_id, namespace, cache_key)
        if cached is not None:
            cached_payload = copy.deepcopy(cached.payload)
            cached_payload.summary["cache_hit"] = True
            cached_payload.summary["cache_source"] = _cache_source_label(namespace)
            cached_payload.summary["evicted"] = []
            return ok_result(cached_payload)
        if record.artifact.bundle.event_stream:
            page = query_events(record.artifact.bundle, active_filter, cursor, limit, normalized_direction)
            page.summary["source"] = "materialized_bundle"
            page.summary["fallback_reason"] = "bundle_scan"
        else:
            source_page = query_events_source_backed(
                record.artifact.source,
                active_filter,
                cursor,
                limit,
                resolved_window,
                normalized_direction,
            )
            if not source_page.ok:
                return err_result("NOT_READY", "event table query data is not ready")
            page = source_page.data
            page.summary["fallback_reason"] = None
            source_page = ok_result(page, warnings=source_page.warnings, untrusted_windows=source_page.untrusted_windows)
            result = source_page
        if record.artifact.bundle.event_stream:
            result = ok_result(page)
        evicted = self.repository.query_cache.put(
            dataset_id,
            namespace,
            cache_key,
            copy.deepcopy(page),
            byte_size_estimate=_estimate_event_page_bytes(page),
            source=str(page.summary.get("source") or "materialized_bundle"),
        )
        page.summary["cache_hit"] = False
        page.summary["cache_source"] = None
        page.summary["evicted"] = _cache_eviction_summary(evicted)
        if result.ok:
            return ok_result(page, warnings=result.warnings, untrusted_windows=result.untrusted_windows)
        return result

    def viz_QueryMetricSeries(self, scope: dict[str, Any]) -> Result[list[dict[str, Any]]]:
        record = self._active_record(scope.get("dataset_id"))
        t_begin, t_end = scope["time_window"]
        metrics = metric_Compute(record.metric_session, t_begin, t_end, scope.get("filter", {}))
        metric_rows = [dataclass_to_dict(item) for item in (metrics.data or [])]
        bucket_count = max(6, min(24, int(scope.get("lod", 1)) * 6))
        bucket_size = float(scope.get("bucket_size") or max((float(t_end) - float(t_begin)) / bucket_count, 1.0))
        bucket_series, bucket_windows = _metric_bucket_series(
            record.metric_session,
            float(t_begin),
            float(t_end),
            scope.get("filter", {}),
            bucket_size,
        )
        for metric in metric_rows:
            metric["bucket_size"] = bucket_size
            metric["bucket_series"] = bucket_series.get(metric["metric_id"], [])
        return ok_result(
            metric_rows,
            untrusted_windows=metrics.untrusted_windows + bucket_windows,
        )

    def viz_QueryEventTable(self, query: EventTableQuery) -> Result[EventPage]:
        record = self._active_record(query.filter.get("dataset_id"))
        page = self._query_event_page(
            record,
            query.filter,
            query.cursor,
            query.limit,
            namespace="event_table",
            direction=query.direction,
        )
        if not page.ok:
            return page
        page_source = str(page.data.summary.get("source", "trace_window_scan"))
        page.data.summary = {
            "source": page_source,
            "cache_hit": bool(page.data.summary.get("cache_hit")),
            "cache_source": page.data.summary.get("cache_source"),
            "evicted": list(page.data.summary.get("evicted") or []),
            "fallback_reason": page.data.summary.get("fallback_reason"),
            "readiness": _event_query_ready_state(record.artifact.bundle, page_source),
        }
        return ok_result(page.data, warnings=page.warnings, untrusted_windows=page.untrusted_windows)

    def viz_QueryTimelineLODAsync(self, scope: dict[str, Any]) -> Result[dict[str, Any]]:
        return self._submit_query_job(
            "query_timeline",
            {
                "scope": dict(scope),
            },
            lambda: self.viz_QueryTimelineLOD(scope),
        )

    def viz_QueryTaskStatesAsync(self, query: TaskStateQuery) -> Result[dict[str, Any]]:
        return self._submit_query_job(
            "query_task_states",
            {
                "query": dataclass_to_dict(query),
            },
            lambda: self.viz_QueryTaskStates(query),
        )

    def viz_QueryEventTableAsync(self, query: EventTableQuery) -> Result[dict[str, Any]]:
        return self._submit_query_job(
            "query_event_page",
            {
                "query": dataclass_to_dict(query),
            },
            lambda: self.viz_QueryEventTable(query),
        )

    def viz_QueryResourceGraph(self, scope: dict[str, Any]) -> Result[dict[str, Any]]:
        record = self._active_record(scope.get("dataset_id"))
        graph = record.artifact.bundle.resource_graph
        filter_spec = scope.get("filter", {})
        nodes = list(graph.nodes)
        hold_edges = list(graph.hold_edges)
        wait_edges = list(graph.wait_edges)
        hotspots = list(graph.hotspot_stats)
        resource_id = _normalize_resource_id(filter_spec.get("resource_id") or filter_spec.get("obj_id"))
        task_id = filter_spec.get("task_id")
        if resource_id is not None:
            target = f"obj:{resource_id}"
            nodes = [item for item in nodes if item.get("node_id") == target]
            hotspots = [item for item in hotspots if item.get("node_id") == target]
            wait_edges = [item for item in wait_edges if _edge_obj_id(item) == resource_id]
            hold_edges = [item for item in hold_edges if _edge_obj_id(item) == resource_id]
        if task_id is not None:
            task_node = f"task:{task_id}"
            nodes = [item for item in nodes if item.get("node_id") == task_node or item.get("node_id", "").startswith("obj:")]
            wait_edges = [item for item in wait_edges if _edge_task_id(item) == task_id]
            hold_edges = [item for item in hold_edges if _edge_task_id(item) == task_id]
        wait_chains = _resource_wait_chains(hold_edges, wait_edges)
        return ok_result(
            {
                "nodes": nodes,
                "hold_edges": hold_edges,
                "wait_edges": wait_edges,
                "hotspots": hotspots,
                "wait_chains": wait_chains,
                "summary": {
                    "node_count": len(nodes),
                    "hold_edge_count": len(hold_edges),
                    "wait_edge_count": len(wait_edges),
                    "wait_chain_count": len(wait_chains),
                },
            }
        )

    def viz_QueryResourceDrilldown(self, scope: dict[str, Any]) -> Result[dict[str, Any]]:
        graph_result = self.viz_QueryResourceGraph(scope)
        if not graph_result.ok:
            return graph_result
        filter_spec = scope.get("filter", {})
        resource_id = _normalize_resource_id(filter_spec.get("resource_id") or filter_spec.get("obj_id"))
        if resource_id is None:
            return err_result("INVALID_ARG", "resource drilldown requires resource_id")

        record = self._active_record(scope.get("dataset_id"))
        event_by_ref = {event.ref_key: event for event in record.artifact.bundle.event_stream}
        event_by_uid = {event.event_uid: event for event in record.artifact.bundle.event_stream}
        task_segments = [
            segment
            for segment in record.artifact.bundle.task_states
            if segment.related_obj == resource_id and _segment_matches_filter(segment, filter_spec)
        ]

        def segment_payload(segment: TaskStateSeg) -> dict[str, Any]:
            cause_event = event_by_uid.get(segment.cause_event)
            return {
                "seg_id": segment.seg_id,
                "task_id": segment.task_id,
                "state": segment.state,
                "t_begin": segment.t_begin,
                "t_end": segment.t_end,
                "related_obj": segment.related_obj,
                "trusted": segment.trusted,
                "cause_event": _event_summary(cause_event),
            }

        segment_rows = [segment_payload(segment) for segment in task_segments]
        graph_payload = graph_result.data
        chain_rows: list[dict[str, Any]] = []
        related_refs: set[str] = set()

        for chain in graph_payload.get("wait_chains", []):
            wait_ref = chain.get("wait_evidence")
            hold_ref = chain.get("hold_evidence")
            if wait_ref:
                related_refs.add(str(wait_ref))
            if hold_ref:
                related_refs.add(str(hold_ref))
            blocked_segments = [
                item
                for item in segment_rows
                if item["task_id"] == chain.get("task_id") and item["state"] == "BLOCKED"
            ][:3]
            owner_segments = [
                item
                for item in segment_rows
                if item["task_id"] == chain.get("owner_task_id")
            ][:3]
            time_points = [
                value
                for value in (
                    *[item["t_begin"] for item in blocked_segments],
                    *[item["t_end"] for item in blocked_segments],
                    *[item["t_begin"] for item in owner_segments],
                    *[item["t_end"] for item in owner_segments],
                    event_by_ref.get(wait_ref).timestamp_aligned if wait_ref in event_by_ref else None,
                    event_by_ref.get(hold_ref).timestamp_aligned if hold_ref in event_by_ref else None,
                )
                if value is not None
            ]
            window = None
            if time_points:
                window = (max(0.0, min(time_points) - 20.0), max(time_points) + 20.0)
            chain_rows.append(
                {
                    "task_id": chain.get("task_id"),
                    "owner_task_id": chain.get("owner_task_id"),
                    "obj_id": chain.get("obj_id"),
                    "path": list(chain.get("path", [])),
                    "trusted": chain.get("trusted", True),
                    "wait_event": _event_summary(event_by_ref.get(wait_ref)) if wait_ref else None,
                    "hold_event": _event_summary(event_by_ref.get(hold_ref)) if hold_ref else None,
                    "blocked_segments": blocked_segments,
                    "owner_segments": owner_segments,
                    "jump_target": (
                        {
                            "time_window": list(window),
                            "selection": {
                                "resource_id": resource_id,
                                **({"task_id": chain.get("task_id")} if chain.get("task_id") is not None else {}),
                            },
                            "evidence_anchor": {"ref_key": wait_ref} if wait_ref else None,
                            "focused_view": "resource",
                        }
                        if window is not None
                        else None
                    ),
                }
            )

        for segment in task_segments:
            cause_event = event_by_uid.get(segment.cause_event)
            if cause_event is not None:
                related_refs.add(cause_event.ref_key)

        related_events = [
            _event_summary(event_by_ref[ref_key])
            for ref_key in sorted(
                related_refs,
                key=lambda ref: event_by_ref[ref].timestamp_aligned if ref in event_by_ref else 0.0,
            )
            if ref_key in event_by_ref
        ]

        jump_target = None
        time_points = [
            value
            for value in (
                *[item["t_begin"] for item in segment_rows],
                *[item["t_end"] for item in segment_rows],
                *[item["timestamp"] for item in related_events if item is not None],
            )
            if value is not None
        ]
        if time_points:
            jump_target = {
                "time_window": [max(0.0, min(time_points) - 20.0), max(time_points) + 20.0],
                "selection": {"resource_id": resource_id},
                "evidence_anchor": {"ref_key": related_events[0]["ref_key"]} if related_events else None,
                "focused_view": "resource",
            }

        return ok_result(
            {
                "resource_id": resource_id,
                "resource_node": next(
                    (item for item in graph_payload.get("nodes", []) if item.get("node_id") == f"obj:{resource_id}"),
                    None,
                ),
                "hotspot": next(
                    (item for item in graph_payload.get("hotspots", []) if item.get("node_id") == f"obj:{resource_id}"),
                    None,
                ),
                "summary": graph_payload.get("summary", {}),
                "wait_chains": chain_rows,
                "task_segments": segment_rows[:10],
                "related_events": related_events[:10],
                "jump_target": jump_target,
            }
        )

    def viz_QueryAlerts(self, scope: dict[str, Any]) -> Result[list[Alert]]:
        record = self._active_record(scope.get("dataset_id"))
        t_begin, t_end = scope["time_window"]
        alerts = alert_Evaluate(record.metric_session, t_begin, t_end, scope.get("filter", {}))
        return ok_result(alerts.data or [], untrusted_windows=alerts.untrusted_windows)

    def viz_CreateBookmark(self, label: str, context: AnalysisContext | None = None, evidence_anchor: dict[str, Any] | None = None) -> Result[Bookmark]:
        current = context or self.context_store.get()
        return ok_result(self.bookmarks.create(label, current, evidence_anchor))

    def viz_ListBookmarks(self) -> Result[list[Bookmark]]:
        return ok_result(self.bookmarks.list())

    def viz_ApplyBookmark(self, bookmark_id: str) -> Result[AnalysisContext]:
        bookmark = self.bookmarks.get(bookmark_id)
        context = self.context_store.commit(
            {
                "time_window": bookmark.time_window,
                "filter": bookmark.filter,
                "selection": bookmark.selection,
                "focused_view": bookmark.focused_view,
                "evidence_anchor": bookmark.evidence_anchor,
            }
        )
        return ok_result(context)

    def viz_DeleteBookmark(self, bookmark_id: str) -> Result[None]:
        self.bookmarks.delete(bookmark_id)
        return ok_result(None)


class ReplayService:
    def __init__(self, context_store: ContextStore) -> None:
        self.context_store = context_store
        self.events: list[UnifiedEvent] = []
        self._runtime = _ReplayRuntimeState()
        self.replay_id = "replay-1"
        self.window_span = 200.0

    def replay_Init(self, unified_stream: list[UnifiedEvent], exec_slices: list[ExecSlice], context: AnalysisContext | None = None) -> Result[str]:
        self.events = list(unified_stream)
        self._runtime = _ReplayRuntimeState(
            current_index=0,
            mode="paused" if self.events else "stopped",
            rate=1.0,
        )
        if context is not None and context.time_window != (0.0, 0.0):
            requested_span = float(context.time_window[1]) - float(context.time_window[0])
            if requested_span > 0.0:
                self.window_span = requested_span
        if self.events:
            if context is not None:
                target = context.playback_cursor or context.evidence_anchor
                index = self._resolve_index(target) if target is not None else None
                if index is None and context.time_window != (0.0, 0.0):
                    index = self._resolve_index({"timestamp_aligned": context.time_window[0]})
                if index is not None:
                    self._runtime.current_index = index
            self._commit_cursor()
        else:
            self.context_store.commit({"playback_runtime": self._runtime_payload()})
        return ok_result(self.replay_id)

    def _current_event(self) -> UnifiedEvent | None:
        if not self.events:
            return None
        return self.events[self._runtime.current_index]

    def _cursor_payload(self, event: UnifiedEvent | None = None) -> dict[str, Any] | None:
        active_event = event or self._current_event()
        if active_event is None:
            return None
        return {
            "event_uid": active_event.event_uid,
            "ref_key": active_event.ref_key,
            "sort_key": list(active_event.sort_key),
            "timestamp": active_event.timestamp_aligned,
            "step_index": self._runtime.current_index,
        }

    def _anchor_ref_payload(self, event: UnifiedEvent | None = None) -> dict[str, Any] | None:
        active_event = event or self._current_event()
        if active_event is None:
            return None
        return {"ref_key": active_event.ref_key, "event_uid": active_event.event_uid}

    def _formal_status(self) -> str:
        mode = self._runtime.mode
        if mode == "stopped":
            return "idle"
        if mode == "playing" and self.events and self._runtime.current_index >= len(self.events) - 1:
            return "ended"
        if mode in {"idle", "playing", "paused", "ended", "degraded"}:
            return mode
        return "degraded"

    def _state(self) -> PlaybackState:
        event = self._current_event()
        visible_window = None
        trusted = True
        if event is not None:
            visible_window = self._visible_window_for_event(event)
            trusted = not event.trust_tags
        return PlaybackState(
            replay_id=self.replay_id,
            status=self._formal_status(),
            rate=self._runtime.rate,
            cursor=self._cursor_payload(event),
            anchor_ref=self._anchor_ref_payload(event),
            visible_window=visible_window,
            linked_views=["timeline", "task_states", "event_table", "metrics"],
            trusted=trusted,
        )

    def _runtime_payload(self) -> dict[str, Any]:
        event = self._current_event()
        cursor = self._cursor_payload(event)
        payload = {
            "current_index": self._runtime.current_index,
            "mode": self._runtime.mode,
            "status": self._formal_status(),
            "rate": self._runtime.rate,
            "cursor_ts": float(event.timestamp_aligned) if event is not None else 0.0,
            "cursor": cursor,
            "anchor_ref": self._anchor_ref_payload(event),
            "visible_window": self._visible_window_for_event(event) if event is not None else None,
            "linked_views": ["timeline", "task_states", "event_table", "metrics"],
            "trusted": bool(event is None or not event.trust_tags),
            "replay_id": self.replay_id,
        }
        if payload.get("visible_window") is not None:
            payload["visible_window"] = list(payload["visible_window"])
        return payload

    def _find_index_by_ref(self, ref_key: str) -> int | None:
        for index, event in enumerate(self.events):
            if event.ref_key == ref_key or event.event_uid == ref_key:
                return index
        return None

    def _nearest_index_for_timestamp(self, timestamp: float) -> int | None:
        if not self.events or not math.isfinite(timestamp):
            return None
        nearest_index: int | None = None
        nearest_distance: float | None = None
        for index, event in enumerate(self.events):
            distance = abs(event.timestamp_aligned - timestamp)
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest_index = index
        return nearest_index

    def _fallback_timestamp_from_evidence_payload(self, payload: dict[str, Any]) -> float | None:
        for field in ("t_begin", "t_end"):
            if field not in payload:
                continue
            try:
                timestamp = float(payload[field])
            except (TypeError, ValueError):
                continue
            if math.isfinite(timestamp):
                return timestamp
        return None

    def _resolve_index(self, target: Any) -> int | None:
        if not self.events or target is None:
            return None
        if isinstance(target, EvidenceRef):
            if target.ref_key:
                index = self._find_index_by_ref(str(target.ref_key))
                if index is not None:
                    return index
            fallback_timestamp = self._fallback_timestamp_from_evidence_payload(
                {"t_begin": target.t_begin, "t_end": target.t_end}
            )
            if fallback_timestamp is None:
                return None
            return self._nearest_index_for_timestamp(fallback_timestamp)
        if isinstance(target, dict) and "ref_key" in target:
            ref_key = str(target["ref_key"])
            index = self._find_index_by_ref(ref_key)
            if index is not None:
                return index
            if any(field in target for field in ("ref_type", "t_begin", "t_end")):
                fallback_timestamp = self._fallback_timestamp_from_evidence_payload(target)
                if fallback_timestamp is not None:
                    return self._nearest_index_for_timestamp(fallback_timestamp)
            return None
        if isinstance(target, dict) and "event_uid" in target:
            event_uid = str(target["event_uid"])
            return self._find_index_by_ref(event_uid)
        if isinstance(target, dict) and "timestamp_aligned" in target:
            ts = float(target["timestamp_aligned"])
        elif isinstance(target, dict) and "t_begin" in target:
            ts = float(target["t_begin"])
        elif isinstance(target, str):
            index = self._find_index_by_ref(target)
            if index is not None:
                return index
            try:
                ts = float(target)
            except ValueError:
                return None
        else:
            try:
                ts = float(target)
            except (TypeError, ValueError):
                return None
        return self._nearest_index_for_timestamp(ts)

    def _visible_window_for_event(self, event: UnifiedEvent) -> tuple[float, float]:
        span = max(self.window_span, 1.0)
        half_span = span / 2.0
        return (max(0.0, event.timestamp_aligned - half_span), event.timestamp_aligned + half_span)

    def _commit_cursor(self) -> None:
        event = self._current_event()
        runtime_payload = self._runtime_payload()
        if event is not None:
            visible_window = self._visible_window_for_event(event)
            selection = {
                "event_uid": event.event_uid,
                "core_id": event.core_id,
            }
            if event.task_id is not None:
                selection["task_id"] = event.task_id
            if event.obj_id is not None:
                selection["resource_id"] = event.obj_id
            self.context_store.commit(
                {
                    "time_window": visible_window,
                    "playback_cursor": dataclass_to_dict(event),
                    "playback_runtime": runtime_payload,
                    "evidence_anchor": {"ref_key": event.ref_key, "event_uid": event.event_uid},
                    "focused_view": "timeline",
                    "selection": selection,
                }
            )
            return
        self.context_store.commit({"playback_runtime": runtime_payload})

    def replay_Play(self, rate: float) -> Result[PlaybackState]:
        self._runtime.rate = rate
        self._runtime.mode = "playing"
        self.context_store.commit({"playback_runtime": self._runtime_payload()})
        return ok_result(self._state())

    def replay_Pause(self) -> Result[PlaybackState]:
        self._runtime.mode = "paused"
        self.context_store.commit({"playback_runtime": self._runtime_payload()})
        return ok_result(self._state())

    def replay_StepForward(self, count: int) -> Result[PlaybackState]:
        if self.events:
            self._runtime.current_index = min(len(self.events) - 1, self._runtime.current_index + count)
            self._commit_cursor()
        return ok_result(self._state())

    def replay_StepBackward(self, count: int) -> Result[PlaybackState]:
        if self.events:
            self._runtime.current_index = max(0, self._runtime.current_index - count)
            self._commit_cursor()
        return ok_result(self._state())

    def replay_Seek(self, target: Any) -> Result[PlaybackState]:
        if not self.events:
            return err_result("NOT_READY", "replay not initialized")
        index = self._resolve_index(target)
        if index is None:
            return err_result("INVALID_ARG", f"replay target not found: {target}")
        self._runtime.current_index = index
        self._commit_cursor()
        return ok_result(self._state())

    def replay_GetState(self) -> Result[PlaybackState]:
        return ok_result(self._state())


class CompareService:
    def __init__(self, repository: DatasetRepository, context_store: ContextStore) -> None:
        self.repository = repository
        self.context_store = context_store
        self.scope: CompareScope | None = None
        self.diff_bundle: DiffBundle | None = None

    def _detail_dimension(self, detail: DiffDetail) -> str:
        return str((detail.target or {}).get("dimension") or "")

    def _detail_matches_string_target(self, detail: DiffDetail, target: str) -> bool:
        if detail.diff_id == target:
            return True
        detail_target = detail.target or {}
        return any(str(value) == target for key, value in detail_target.items() if key != "dimension")

    def _detail_matches_structured_target(self, detail: DiffDetail, target: dict[str, Any]) -> bool:
        if target.get("diff_id") == detail.diff_id:
            return True
        detail_target = detail.target or {}
        return all(detail_target.get(key) == value for key, value in target.items())

    def _register_bundle(
        self,
        bundle: RebuildBundle,
        source: str,
        role: str,
        dictionary_info: dict[str, Any] | None = None,
    ) -> Result[str]:
        dataset_id = _role_dataset_id(bundle.dataset_id, role)
        artifact = DatasetArtifact(
            dataset_id=dataset_id,
            source=source,
            header=bundle.header or GlobalHeader(
                magic="0x0",
                endian=1,
                time_unit=1,
                clock_source=1,
                format_ver=1,
                dict_ver=1,
                producer_ver="compare",
                run_id=bundle.dataset_id,
            ),
            bundle=bundle,
            dictionary_info=dict(dictionary_info or {}),
        )
        session = metric_Init().data
        ingest = metric_Ingest(session, bundle)
        if not ingest.ok:
            return Result(
                code=ingest.code,
                message=ingest.message,
                warnings=ingest.warnings,
                untrusted_windows=ingest.untrusted_windows,
            )
        dataset_id = self.repository.add(DatasetRecord(artifact=artifact, metric_session=session))
        return ok_result(dataset_id)

    def _parse_trace_source_isolated(self, source: str | Path, *, job_id: str) -> Result[dict[str, Any]]:
        parser_agent = ParserProcessAgent(job_id=job_id)
        parsed = parser_agent.parse_rebuild(
            source,
            artifact_policy={
                "artifact_dir": str(Path(tempfile.gettempdir()) / "rttrace-parser-agent" / job_id),
                "load_artifact": True,
                "retain_artifact": True,
            },
        )
        if not parsed.ok:
            return Result(
                code=parsed.code,
                message=parsed.message,
                data=parsed.data,
                warnings=parsed.warnings,
                untrusted_windows=parsed.untrusted_windows,
            )
        return ok_result(
            {
                "artifact": parsed.data["artifact"],
                "parser_process_agent": parser_agent.contract.to_dict(),
                "artifact_handle": parsed.data.get("artifact_handle"),
                "parser_process_artifact": parsed.data.get("parser_process_artifact"),
            },
            warnings=parsed.warnings,
            untrusted_windows=parsed.untrusted_windows,
        )

    def _resolve_dataset_ref(self, ref: str, role: str) -> Result[str]:
        if self.repository.has(ref):
            target_id = _role_dataset_id(self.repository.get(ref).artifact.dataset_id, role)
            if self.repository.has(target_id):
                return ok_result(target_id)
            return self._register_bundle(
                self.repository.get(ref).artifact.bundle,
                self.repository.get(ref).artifact.source,
                role,
                dictionary_info=self.repository.get(ref).artifact.dictionary_info,
            )
        path = Path(ref)
        if path.is_file():
            loaded = self._parse_trace_source_isolated(path, job_id=f"resolve-{role}-{uuid.uuid4().hex}")
            if not loaded.ok:
                return Result(
                    code=loaded.code,
                    message=loaded.message,
                    data=loaded.data,
                    warnings=loaded.warnings,
                    untrusted_windows=loaded.untrusted_windows,
                )
            artifact = loaded.data["artifact"]
            target_id = _role_dataset_id(artifact.dataset_id, role)
            if self.repository.has(target_id):
                return ok_result(target_id)
            return self._register_bundle(
                artifact.bundle,
                artifact.source,
                role,
                dictionary_info=artifact.dictionary_info,
            )
        if path.is_dir() and (path / "manifest.json").exists() and (path / "meta.json").exists():
            loaded_package = _load_validated_package(path)
            if not loaded_package.ok:
                return loaded_package
            bundle = loaded_package.data["bundle"]
            dataset_id = _role_dataset_id(bundle.dataset_id, role)
            if self.repository.has(dataset_id):
                return ok_result(dataset_id)
            return self._register_bundle(
                bundle,
                str(path),
                role,
                dictionary_info=loaded_package.data.get("dictionary_info"),
            )
        return err_result("INVALID_ARG", f"compare dataset not found: {ref}")

    def cmp_LoadPair(self, baseline_id: str, candidate_id: str) -> Result[str]:
        baseline_ref = self._resolve_dataset_ref(baseline_id, "baseline")
        candidate_ref = self._resolve_dataset_ref(candidate_id, "candidate")
        if not baseline_ref.ok:
            return baseline_ref
        if not candidate_ref.ok:
            return candidate_ref
        return ok_result("compare-1")

    def cmp_SetScope(self, scope: dict[str, Any]) -> Result[CompareScope]:
        baseline_ref = self._resolve_dataset_ref(scope["baseline_id"], "baseline")
        candidate_ref = self._resolve_dataset_ref(scope["candidate_id"], "candidate")
        if not baseline_ref.ok:
            return baseline_ref
        if not candidate_ref.ok:
            return candidate_ref
        baseline_id = baseline_ref.data
        candidate_id = candidate_ref.data
        baseline = self.repository.get(baseline_id).artifact.bundle
        candidate = self.repository.get(candidate_id).artifact.bundle
        base_window = (
            baseline.event_stream[0].timestamp_aligned,
            baseline.event_stream[-1].timestamp_aligned,
        )
        cand_window = (
            candidate.event_stream[0].timestamp_aligned,
            candidate.event_stream[-1].timestamp_aligned,
        )
        aligned_time_window = tuple(
            scope.get("aligned_time_window")
            or (
                max(base_window[0], cand_window[0]),
                min(base_window[1], cand_window[1]),
            )
        )
        if len(aligned_time_window) != 2 or float(aligned_time_window[0]) >= float(aligned_time_window[1]):
            return err_result("INVALID_ARG", "compare aligned_time_window has no overlap")
        normalized = CompareScope(
            baseline_id=baseline_id,
            candidate_id=candidate_id,
            aligned_time_window=aligned_time_window,
            filter=scope.get("filter", {}),
            dimensions=list(
                scope.get(
                    "dimensions",
                    ["metric", "alert", "hotspot", "interval", "task", "core", "resource", "irq"],
                )
            ),
            metric_ids=list(
                scope.get(
                    "metric_ids",
                    [
                        "cpu_utilization",
                        "context_switch_count",
                        "blocked_time",
                        "ready_wait_time",
                        "response_time",
                        "response_jitter",
                        "irq_latency",
                    ],
                )
            ),
            bucket_size=scope.get("bucket_size", max(1.0, (aligned_time_window[1] - aligned_time_window[0]) / 10.0)),
            evidence_policy=scope.get("evidence_policy", "inherit"),
            scope_id=scope.get("scope_id", f"scope:{baseline_id}:{candidate_id}"),
        )
        self.scope = normalized
        self.context_store.commit({"compare_scope": dataclass_to_dict(normalized)})
        diff = metric_Compare(baseline, candidate, dataclass_to_dict(normalized))
        if not diff.ok:
            return Result(
                code=diff.code,
                message=diff.message,
                warnings=diff.warnings,
                untrusted_windows=diff.untrusted_windows,
            )
        self.diff_bundle = diff.data
        return ok_result(normalized, untrusted_windows=diff.untrusted_windows)

    def cmp_QueryDiffSummary(self) -> Result[dict[str, Any]]:
        if self.diff_bundle is None:
            return err_result("NOT_READY", "compare scope not initialized")
        return ok_result(_serialize_diff_summary(self.diff_bundle.summary))

    def cmp_ListDiffDetails(self, dimension: str | None = None) -> Result[list[dict[str, Any]]]:
        if self.diff_bundle is None:
            return err_result("NOT_READY", "compare scope not initialized")
        details = self.diff_bundle.details
        if dimension is not None:
            details = [detail for detail in details if self._detail_dimension(detail) == dimension]
        return ok_result([dataclass_to_dict(item) for item in details])

    def cmp_QueryDiffDetail(self, target: str | dict[str, Any] | None) -> Result[dict[str, Any]]:
        if self.diff_bundle is None:
            return err_result("NOT_READY", "compare scope not initialized")
        if target is None:
            return err_result("INVALID_ARG", "target is required")
        if isinstance(target, str):
            exact = next((detail for detail in self.diff_bundle.details if detail.diff_id == target), None)
            if exact is not None:
                return ok_result(dataclass_to_dict(exact))
            matches = [detail for detail in self.diff_bundle.details if self._detail_matches_string_target(detail, target)]
        else:
            matches = [
                detail for detail in self.diff_bundle.details if self._detail_matches_structured_target(detail, target)
            ]
        if not matches:
            return err_result("NOT_FOUND", "diff detail not found")
        if len(matches) > 1:
            return err_result("INVALID_ARG", "diff detail target is ambiguous")
        return ok_result(dataclass_to_dict(matches[0]))

    def cmp_LinkAuxView(self, dataset_id: str, context: dict[str, Any]) -> Result[None]:
        if not self.repository.has(dataset_id):
            return err_result("INVALID_ARG", "dataset not loaded")
        self.context_store.commit({"selection": context.get("selection", {})})
        return ok_result(None)


class ExportService:
    def __init__(
        self,
        repository: DatasetRepository,
        context_store: ContextStore,
        jobs: BackgroundJobManager,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.repository = repository
        self.context_store = context_store
        self.jobs = jobs
        self.on_progress = on_progress

    def _emit_progress(self, **payload: Any) -> None:
        if self.on_progress is None:
            return
        self.on_progress(dict(payload))

    def _job_lineage(self, scope: dict[str, Any]) -> dict[str, Any]:
        lineage: dict[str, Any] = {}
        for key in ("run_id", "run_batch_id", "version_id", "experiment_params"):
            if key in scope:
                lineage[key] = scope[key]
        return lineage

    def _resolve_dataset_role(self, dataset_id: str | None, context: dict[str, Any] | None = None) -> str:
        if context and "dataset_role" in context:
            context_role = _normalize_dataset_role(context["dataset_role"])
            if context_role != "single":
                return context_role
        if dataset_id is not None and self.repository.has(dataset_id):
            return _infer_dataset_role(self.repository.get(dataset_id).artifact.dataset_id)
        return _infer_dataset_role(dataset_id, context.get("dataset_role") if context else "single")

    def export_Full(self, scope: dict[str, Any]) -> Result[dict[str, Any]]:
        dataset_id = scope.get("dataset_id")
        context = self.context_store.get().persisted_dict()
        context["dataset_role"] = self._resolve_dataset_role(dataset_id, context)
        job_id = self.jobs.create(
            "export_full",
            {
                "dataset_id": dataset_id,
                "mode": "full",
                "context": context,
                **self._job_lineage(scope),
            },
        )
        return ok_result({"job_id": job_id})

    def export_Clipped(self, scope: dict[str, Any]) -> Result[dict[str, Any]]:
        payload = dict(scope or {})
        payload.setdefault("dataset_id", scope.get("dataset_id") if scope else None)
        payload["mode"] = "clipped"
        context = dict(self.context_store.get().persisted_dict())
        context["dataset_role"] = self._resolve_dataset_role(payload.get("dataset_id"), context)
        for key in (
            "time_window",
            "filter",
            "selection",
            "zoom_level",
            "focused_view",
            "evidence_anchor",
            "playback_cursor",
            "compare_scope",
            "dataset_role",
        ):
            if key in payload:
                context[key] = payload[key]
        payload["context"] = context
        payload.update(self._job_lineage(scope or {}))
        job_id = self.jobs.create("export_clipped", payload)
        return ok_result({"job_id": job_id})

    def export_Evidence(self, request: dict[str, Any]) -> Result[dict[str, Any]]:
        payload = dict(request or {})
        payload.setdefault("dataset_id", payload.get("dataset_id"))
        payload["mode"] = "evidence"
        context = dict(self.context_store.get().persisted_dict())
        context["dataset_role"] = self._resolve_dataset_role(payload.get("dataset_id"), context)
        for key in (
            "time_window",
            "filter",
            "selection",
            "zoom_level",
            "focused_view",
            "evidence_anchor",
            "playback_cursor",
            "compare_scope",
            "dataset_role",
        ):
            if key in payload:
                context[key] = payload[key]
        payload["context"] = context
        payload["lane"] = "evidence_export"
        payload.update(self._job_lineage(request or {}))
        job_id = self.jobs.create("export_evidence", payload)
        self.jobs.update_patent_contract(job_id, patent_job_state="JOB-queued")
        return ok_result({"job_id": job_id})

    def _update_evidence_job_contract(self, job_id: str, progress_payload: dict[str, Any]) -> None:
        if str(progress_payload.get("category") or "") != "evidence_export":
            return
        substage = str(progress_payload.get("substage") or "")
        status = str(progress_payload.get("status") or "")
        fields: dict[str, Any] = {
            "last_substage": substage,
            "last_substage_status": status,
        }
        for key in ("round_id", "frontier_count", "emitted_events", "emitted_bytes"):
            if key in progress_payload:
                fields[key] = progress_payload.get(key)
        patent_job_state: str | None = None
        if substage == "prepare/context_freeze":
            patent_job_state = "JOB-context_frozen"
        elif substage == "sidecar/build_or_load" and status == "completed":
            patent_job_state = "JOB-sidecar_ready"
        elif substage == "sidecar/validate" and status == "completed":
            patent_job_state = "JOB-sidecar_validated"
        elif substage == "seed/resolve" and status == "completed":
            patent_job_state = "JOB-seeds_ready"
        elif substage.startswith("round/"):
            patent_job_state = "JOB-closing"
            if substage == "round/budget" and status == "rejected":
                fields["budget_rejected"] = True
                fields["projected_next_events"] = progress_payload.get("projected_next_events")
                fields["projected_next_bytes"] = progress_payload.get("projected_next_bytes")
        elif substage == "finalize/proof" and status == "completed":
            patent_job_state = "JOB-finalized"
        elif substage == "write/manifest" and status == "completed":
            patent_job_state = "JOB-package_written"
        self.jobs.update_patent_contract(job_id, patent_job_state=patent_job_state, **fields)

    def _copy_sidecar_reference_assets(
        self,
        root_path: Path,
        dictionary_source_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reference_dir = root_path / "reference"
        schema_dir = reference_dir / "schema"
        reference_dir.mkdir(parents=True, exist_ok=True)
        schema_dir.mkdir(parents=True, exist_ok=True)

        dict_target = reference_dir / "dictionary.json"
        shutil.copy2(dictionary_source_path, dict_target)
        dict_payload = json_load(dict_target)
        dict_ref = {
            "path": "reference/dictionary.json",
            "algo": "sha256",
            "checksum": checksum_file(dict_target),
            "dict_ver": int(dict_payload["dict_ver"]),
        }

        schema_ref: dict[str, Any] = {}
        for schema_key, filename in SIDECAR_SCHEMA_NAMES.items():
            target = schema_dir / filename
            shutil.copy2(SCHEMA_DIR / filename, target)
            schema_ref[schema_key] = {
                "path": f"reference/schema/{filename}",
                "algo": "sha256",
                "checksum": checksum_file(target),
            }
        return dict_ref, schema_ref

    def sidecar_Build(self, request: dict[str, Any]) -> Result[dict[str, Any]]:
        payload = dict(request or {})
        payload.setdefault("dataset_id", payload.get("dataset_id"))
        context = dict(self.context_store.get().persisted_dict())
        context["dataset_role"] = self._resolve_dataset_role(payload.get("dataset_id"), context)
        for key in (
            "time_window",
            "filter",
            "selection",
            "zoom_level",
            "focused_view",
            "evidence_anchor",
            "playback_cursor",
            "compare_scope",
            "dataset_role",
        ):
            if key in payload:
                context[key] = payload[key]
        payload["context"] = context
        payload["lane"] = "sidecar_build"
        payload.update(self._job_lineage(request or {}))
        job_id = self.jobs.create("sidecar_build", payload)
        self.jobs.update_patent_contract(job_id, patent_job_state="JOB-queued")
        return ok_result({"job_id": job_id})

    def sidecar_WriteArtifacts(self, job_id: str, output_path: str) -> Result[dict[str, Any]]:
        def _round_duration(value: float) -> float:
            return round(max(float(value), 0.0), 6)

        job_wall_started = time.perf_counter()
        job = self.jobs.get(job_id)
        dataset_id = job.get("dataset_id")
        if dataset_id is None:
            dataset_id = next(iter(self.repository.list_ids()), None)
        if dataset_id is None:
            self.jobs.update_patent_contract(job_id, error_code="NOT_READY", failed_at_state="JOB-queued")
            return err_result("NOT_READY", "no dataset available for sidecar build")
        record = self.repository.get(dataset_id)
        context = dict(job.get("context") or self.context_store.get().persisted_dict())
        runtime_load_effective_plan = dict(job.get("runtime_load_effective_plan") or {})
        load_materialize_event_stream = bool(runtime_load_effective_plan.get("materialize_event_stream", True))
        prepare_context_started = time.perf_counter()
        self.jobs.update_patent_contract(
            job_id,
            patent_job_state="JOB-context_frozen",
            last_substage="prepare/context_freeze",
            last_substage_status="completed",
        )
        default_window = _bundle_time_window(record.artifact.bundle)
        try:
            request = EvidenceExportRequest.from_payload(
                job,
                default_dataset_id=dataset_id,
                default_time_window=default_window,
                default_filter=dict(context.get("filter") or {}),
            )
        except ValueError as exc:
            self.jobs.update_patent_contract(job_id, error_code="INVALID_ARG", failed_at_state="JOB-context_frozen")
            return err_result("INVALID_ARG", str(exc))

        trace_path_result = _resolve_evidence_trace_source_path(str(record.artifact.source))
        if not trace_path_result.ok:
            self.jobs.update_patent_contract(
                job_id,
                error_code=str(trace_path_result.code or "INVALID_ARG"),
                failed_at_state="JOB-context_frozen",
            )
            return trace_path_result
        trace_checksum = checksum_file(trace_path_result.data)
        dictionary_path = _resolve_evidence_dictionary_source_path(str(record.artifact.source))
        dictionary_checksum = checksum_file(dictionary_path)

        bundle = record.artifact.bundle
        if bundle.event_stream:
            export_window = (
                float(bundle.event_stream[0].timestamp_aligned),
                float(bundle.event_stream[-1].timestamp_aligned),
            )
        else:
            export_window = request.time_window or default_window
        export_filter = dict(context.get("filter") or {})
        analysis_context, _ = _build_analysis_context(
            context,
            dataset_id=dataset_id,
            export_window=export_window,
            export_filter=export_filter,
        )
        alerts = alert_Evaluate(record.metric_session, export_window[0], export_window[1], export_filter).data or []
        diagnoses = list(self._iter_diagnosis_rows(alerts))
        anchor_rows = list(self._iter_anchor_rows(analysis_context, alerts))
        bundle_event_stream_available = bool(bundle.event_stream)
        ref_index_rows_started = time.perf_counter()
        if bundle_event_stream_available:
            ref_index_rows_source = "bundle_event_stream"
            ref_index_rows = [
                {
                    "ref_key": event.ref_key,
                    "timestamp_aligned": float(event.timestamp_aligned),
                    "core_id": int(event.core_id),
                    "seq": int(event.seq),
                }
                for event in sorted(
                    bundle.event_stream,
                    key=lambda item: (float(item.timestamp_aligned), int(item.core_id), int(item.seq)),
                )
            ]
        else:
            ref_index_rows_source = "trace_source_scan"
            source_ref_index_rows = _source_backed_ref_index_rows(
                trace_path_result.data,
                bundle,
                dictionary=(record.artifact.dictionary_info or {}).get("resolved_dictionary") or dictionary_path,
            )
            if not source_ref_index_rows.ok:
                self.jobs.update_patent_contract(
                    job_id,
                    error_code=str(source_ref_index_rows.code or "INVALID_ARG"),
                    failed_at_state="JOB-context_frozen",
                )
                return source_ref_index_rows
            ref_index_rows = list(source_ref_index_rows.data)
        ref_index_rows_seconds = time.perf_counter() - ref_index_rows_started
        prepare_context_seconds = time.perf_counter() - prepare_context_started
        snapshot_id = evd_DeriveStableSnapshotId(
            dataset_id=dataset_id,
            embodiment_mode=request.embodiment_mode,
            trace_checksum=trace_checksum,
            dictionary_checksum=dictionary_checksum,
            request=request,
            analysis_context=analysis_context,
        )
        sidecar_build_started = time.perf_counter()
        sidecar_rows = materialize_dependency_sidecar(
            build_dependency_sidecar(
                bundle,
                snapshot_id=snapshot_id,
                rule_families=request.rule_family,
                alerts=alerts,
                diagnoses=diagnoses,
                context=analysis_context,
                anchors=anchor_rows,
                ref_index_rows=ref_index_rows,
            ),
            trace_checksum=trace_checksum,
        )
        sidecar_build_seconds = time.perf_counter() - sidecar_build_started
        self.jobs.update_patent_contract(
            job_id,
            patent_job_state="JOB-sidecar_ready",
            last_substage="sidecar/build_or_load",
            last_substage_status="completed",
        )

        sidecar_write_started = time.perf_counter()
        package_path = Path(output_path)
        control_dir = package_path / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        dict_ref, schema_ref = self._copy_sidecar_reference_assets(package_path, dictionary_path)
        dependency_sidecar_path = control_dir / "dependency_sidecar.jsonl"
        jsonl_dump(dependency_sidecar_path, sidecar_rows)
        sidecar_write_seconds = time.perf_counter() - sidecar_write_started
        sidecar_manifest = {
            "sidecar_version": "evidence-sidecar-1",
            "generator_version": EVIDENCE_PARSER_VERSION,
            "trace_checksum": trace_checksum,
            "dictionary_checksum": dictionary_checksum,
            "schema_checksums": schema_ref,
            "relation_families": list(request.rule_family),
            "entry_paths": ["control/dependency_sidecar.jsonl"],
            "entry_checksums": {
                "control/dependency_sidecar.jsonl": checksum_file(dependency_sidecar_path),
            },
            "created_at": _iso_now(),
            "snapshot_id": snapshot_id,
        }
        sidecar_validate_started = time.perf_counter()
        validated = validate_sidecar_payload(
            sidecar_rows,
            sidecar_manifest,
            expected_snapshot_id=snapshot_id,
            expected_trace_checksum=trace_checksum,
            expected_dictionary_checksum=dictionary_checksum,
            require_non_empty=True,
        )
        sidecar_validate_seconds = time.perf_counter() - sidecar_validate_started
        if not validated.ok:
            self.jobs.update_patent_contract(
                job_id,
                error_code=str(validated.code or "SIDECAR_INVALID"),
                failed_at_state="JOB-sidecar_ready",
            )
            return validated
        self.jobs.update_patent_contract(
            job_id,
            patent_job_state="JOB-sidecar_validated",
            last_substage="sidecar/validate",
            last_substage_status="completed",
        )
        index_agent = SidecarIndexAgent(job_id=job_id)
        sidecar_index_started = time.perf_counter()
        indexed = index_agent.build_or_open(
            sidecar_path=dependency_sidecar_path,
            sidecar_manifest=sidecar_manifest,
            expected_snapshot_id=snapshot_id,
            expected_trace_checksum=trace_checksum,
            expected_dictionary_checksum=dictionary_checksum,
            validate_stream=False,
            rebuild_on_mismatch=True,
        )
        sidecar_index_total_seconds = time.perf_counter() - sidecar_index_started
        self.jobs.update_patent_contract(
            job_id,
            patent_job_state="JOB-sidecar_indexed" if indexed.ok else None,
            last_substage="sidecar/index",
            last_substage_status="completed" if indexed.ok else "failed",
            sidecar_index_agent=index_agent.contract.to_dict(),
        )
        if not indexed.ok:
            self.jobs.update_patent_contract(
                job_id,
                error_code=str(indexed.code or "ERR-SIDECAR_INDEX_MISMATCH"),
                failed_at_state="JOB-sidecar_validated",
            )
            return Result(
                code=str(indexed.code or "ERR-SIDECAR_INDEX_MISMATCH"),
                message=indexed.message,
                data=indexed.data,
                warnings=indexed.warnings,
                untrusted_windows=indexed.untrusted_windows,
            )
        handle = indexed.data["handle"]
        sqlite_index_build_seconds = max(float(handle.build_seconds), 0.0)
        ticket_write_seconds = max(float(getattr(handle, "ticket_write_seconds", 0.0)), 0.0)
        ticket_write_seconds_estimated = False
        ticket_write_seconds_basis = "measured:write_sidecar_index_ticket"
        manifest_write_started = time.perf_counter()
        index_path = Path(str(indexed.data["index_path"]))
        ticket_path = Path(str(indexed.data["ticket_path"]))
        agent_contract_path = control_dir / "sidecar_index_agent_contract.json"
        json_dump(agent_contract_path, index_agent.contract.to_dict())
        for rel_path, abs_path in (
            ("control/dependency_sidecar.jsonl.sqlite3", index_path),
            ("control/dependency_sidecar.jsonl.sqlite3.ticket.json", ticket_path),
            ("control/sidecar_index_agent_contract.json", agent_contract_path),
        ):
            if rel_path not in sidecar_manifest["entry_paths"]:
                sidecar_manifest["entry_paths"].append(rel_path)
            sidecar_manifest["entry_checksums"][rel_path] = checksum_file(abs_path)
        sidecar_manifest_path = control_dir / "sidecar_manifest.json"
        json_dump(sidecar_manifest_path, sidecar_manifest)
        manifest_write_seconds = time.perf_counter() - manifest_write_started
        self.jobs.update_patent_contract(
            job_id,
            patent_job_state="JOB-package_written",
            last_substage="write/manifest",
            last_substage_status="completed",
        )
        job_wall_seconds = time.perf_counter() - job_wall_started
        prebuild_path_features = {
            "bundle_event_stream_available": bundle_event_stream_available,
            "full_trace_reparse": False,
            "source_ref_index_scan": not bundle_event_stream_available,
            "load_materialize_event_stream": load_materialize_event_stream,
            "full_sidecar_materialize": True,
            "full_sidecar_jsonl_write": True,
            "sqlite_index_full_scan": bool(handle.built),
        }
        duplicate_parse_detected = (not load_materialize_event_stream) and not bundle_event_stream_available
        prebuild_diagnostics = {
            "prebuild_build_dependency_sidecar_calls": 1,
            "prebuild_materialize_dependency_sidecar_calls": 1,
            "prebuild_full_sidecar_jsonl_write_count": 1,
            "duplicate_parse_detected": bool(duplicate_parse_detected),
            "load_materialize_event_stream": bool(load_materialize_event_stream),
            "ref_index_rows_source": ref_index_rows_source,
            "ref_index_rows_seconds": _round_duration(ref_index_rows_seconds),
            "sqlite_index_rebuilt": bool(handle.built),
            "ticket_reused": bool(getattr(handle, "ticket_reused", False)),
            "sidecar_index_total_seconds": _round_duration(sidecar_index_total_seconds),
        }
        accounted_seconds = (
            prepare_context_seconds
            + sidecar_build_seconds
            + sidecar_write_seconds
            + sidecar_validate_seconds
            + sqlite_index_build_seconds
            + ticket_write_seconds
            + manifest_write_seconds
        )
        prebuild_stage_breakdown = {
            "prepare_context_seconds": _round_duration(prepare_context_seconds),
            "sidecar_build_seconds": _round_duration(sidecar_build_seconds),
            "sidecar_write_seconds": _round_duration(sidecar_write_seconds),
            "sidecar_validate_seconds": _round_duration(sidecar_validate_seconds),
            "sqlite_index_build_seconds": _round_duration(sqlite_index_build_seconds),
            "ticket_write_seconds": _round_duration(ticket_write_seconds),
            "ticket_write_seconds_estimated": ticket_write_seconds_estimated,
            "ticket_write_seconds_basis": ticket_write_seconds_basis,
            "manifest_write_seconds": _round_duration(manifest_write_seconds),
            "ref_index_rows_seconds": _round_duration(ref_index_rows_seconds),
            "ref_index_rows_source": ref_index_rows_source,
            "job_wall_seconds": _round_duration(job_wall_seconds),
            "unaccounted_seconds": _round_duration(job_wall_seconds - accounted_seconds),
        }
        return ok_result(
            {
                "job_id": job_id,
                "output_path": str(package_path),
                "snapshot_id": snapshot_id,
                "dependency_sidecar_path": str(dependency_sidecar_path),
                "sidecar_manifest_path": str(sidecar_manifest_path),
                "sidecar_index_path": str(index_path),
                "sidecar_index_ticket_path": str(ticket_path),
                "sidecar_index_agent_contract_path": str(agent_contract_path),
                "sidecar_index_agent": index_agent.contract.to_dict(),
                "edge_count": len(sidecar_rows),
                "prebuild_stage_breakdown": prebuild_stage_breakdown,
                "prebuild_path_features": prebuild_path_features,
                "prebuild_diagnostics": prebuild_diagnostics,
            }
        )

    def _iter_flat_dataclass_rows(self, rows: Iterable[Any]) -> Iterator[dict[str, Any]]:
        for row in rows:
            yield _json_default(row)

    def _iter_alert_rows(self, alerts: list[Alert]) -> Iterator[dict[str, Any]]:
        for alert in alerts:
            yield {
                "alert_id": alert.alert_id,
                "type": alert.type,
                "severity": alert.severity,
                "time_window": alert.time_window,
                "object_scope": alert.object_scope,
                "threshold": alert.threshold,
                "actual": alert.actual,
                "evidence_refs": alert.evidence_refs,
                "trusted": alert.trusted,
                "support_level": alert.support_level,
            }

    def _iter_diagnosis_rows(self, alerts: list[Alert]) -> Iterator[dict[str, Any]]:
        for alert in alerts:
            yield diag_PayloadFromAlert(alert)

    def _iter_anchor_rows(
        self,
        context: dict[str, Any],
        alerts: list[Alert],
    ) -> Iterator[dict[str, Any]]:
        evidence_anchor = context.get("evidence_anchor")
        if evidence_anchor:
            yield {
                "anchor_id": "context:current",
                "anchor_type": "context",
                "evidence_anchor": evidence_anchor,
                "time_window": context.get("time_window", []),
            }
        for alert in alerts:
            for index, evidence_ref in enumerate(alert.evidence_refs):
                yield {
                    "anchor_id": f"{alert.alert_id}:{index}",
                    "anchor_type": "alert",
                    "owner_id": alert.alert_id,
                    "evidence_anchor": evidence_ref,
                    "time_window": alert.time_window,
                }
        for alert in alerts:
            diag_id = f"diag:{alert.alert_id}"
            for index, evidence_ref in enumerate(alert.evidence_refs):
                yield {
                    "anchor_id": f"{diag_id}:{index}",
                    "anchor_type": "diagnosis",
                    "owner_id": diag_id,
                    "evidence_anchor": evidence_ref,
                    "time_window": alert.time_window,
                }

    def _anchor_rows(
        self,
        context: dict[str, Any],
        alerts: list[Alert],
        diagnoses: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [serialize(row) for row in self._iter_anchor_rows(context, alerts)]

    def _copy_reference_assets(
        self,
        package_path: Path,
        dictionary_info: dict[str, Any] | None = None,
        *,
        write_timings: dict[str, float] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reference_dir = package_path / "reference"
        schema_dir = reference_dir / "schema"
        reference_dir.mkdir(parents=True, exist_ok=True)
        schema_dir.mkdir(parents=True, exist_ok=True)

        dict_target = reference_dir / "dictionary.json"
        resolved_dictionary = (dictionary_info or {}).get("resolved_dictionary")
        if isinstance(resolved_dictionary, dict) and resolved_dictionary:
            _measure_duration(write_timings, "json_dump_seconds", json_dump, dict_target, resolved_dictionary)
            dict_payload = resolved_dictionary
        else:
            _measure_duration(write_timings, "reference_copy_seconds", shutil.copy2, DICTIONARY_PATH, dict_target)
            dict_payload = json_load(dict_target)
        dict_ref = {
            "path": "reference/dictionary.json",
            "algo": "sha256",
            "checksum": _measure_duration(write_timings, "checksum_seconds", checksum_file, dict_target),
            "dict_ver": int(dict_payload["dict_ver"]),
        }

        schema_key_map = {
            "package.schema.json": "package_schema",
            "meta.schema.json": "meta_schema",
            "manifest.schema.json": "manifest_schema",
            "analysis_context.schema.json": "analysis_context_schema",
            "compare_scope.schema.json": "compare_scope_schema",
        }
        schema_ref: dict[str, Any] = {}
        for name in REFERENCE_SCHEMA_NAMES:
            src = SCHEMA_DIR / name
            target = schema_dir / name
            _measure_duration(write_timings, "reference_copy_seconds", shutil.copy2, src, target)
            schema_ref[schema_key_map[name]] = {
                "path": f"reference/schema/{name}",
                "algo": "sha256",
                "checksum": _measure_duration(write_timings, "checksum_seconds", checksum_file, target),
            }
        return dict_ref, schema_ref

    def _dictionary_status(self, artifact: DatasetArtifact, dict_ref: dict[str, Any]) -> dict[str, Any]:
        info = dict(artifact.dictionary_info or {})
        resolved_dict = info.get("resolved_dictionary")
        return {
            "requested_source": info.get("requested_source", "default"),
            "requested_path": info.get("requested_path"),
            "requested_dict_ver": info.get("requested_dict_ver"),
            "resolved_source": info.get("resolved_source", "default"),
            "resolved_dict_ver": info.get("resolved_dict_ver", dict_ref.get("dict_ver")),
            "expected_dict_ver": info.get("expected_dict_ver"),
            "header_dict_ver": artifact.header.dict_ver,
            "fallback_used": bool(info.get("fallback_used")),
            "reason_codes": list(info.get("reason_codes", [])),
            "version_mismatch": bool(info.get("version_mismatch")),
            "warning_count": len(info.get("warnings", [])),
            "warnings": list(info.get("warnings", [])),
            "reference_path": dict_ref["path"],
            "reference_dict_ver": dict_ref["dict_ver"],
            "has_embedded_dictionary": isinstance(resolved_dict, dict) and bool(resolved_dict),
        }

    def _manifest_entry(
        self,
        package_path: Path,
        relative_path: str,
        *,
        category: str,
        count: int,
        format_name: str,
        schema_ref: str | None = None,
        producer: str | None = None,
        ref_keys: dict[str, Any] | None = None,
        write_timings: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        path = package_path / relative_path
        entry = {
            "path": relative_path,
            "category": category,
            "count": count,
            "checksum": _measure_duration(write_timings, "checksum_seconds", checksum_file, path),
            "format": format_name,
            "schema_ref": schema_ref,
            "producer": producer,
        }
        if ref_keys is not None:
            entry["ref_keys"] = ref_keys
        return entry

    def _matches_export_filter(self, item: Any, filter_spec: dict[str, Any]) -> bool:
        if not filter_spec:
            return True
        if "task_id" in filter_spec:
            if getattr(item, "task_id", None) != filter_spec["task_id"] and getattr(item, "delayed_task", None) != filter_spec["task_id"]:
                return False
        if "core_id" in filter_spec and getattr(item, "core_id", None) != filter_spec["core_id"]:
            return False
        if "event_name" in filter_spec and getattr(item, "event_name", None) != filter_spec["event_name"]:
            return False
        resource_id = _normalize_resource_id(filter_spec.get("resource_id") or filter_spec.get("obj_id"))
        if resource_id is not None:
            if getattr(item, "obj_id", None) != resource_id and getattr(item, "related_obj", None) != resource_id:
                return False
        if "irq_id" in filter_spec and getattr(item, "irq_id", None) != filter_spec["irq_id"]:
            return False
        return True

    def _filter_graph(self, graph: ResourceGraph, filter_spec: dict[str, Any]) -> ResourceGraph:
        nodes = list(graph.nodes)
        hold_edges = list(graph.hold_edges)
        wait_edges = list(graph.wait_edges)
        hotspots = list(graph.hotspot_stats)
        resource_id = _normalize_resource_id(filter_spec.get("resource_id") or filter_spec.get("obj_id"))
        task_id = filter_spec.get("task_id")
        if resource_id is not None:
            node_id = f"obj:{resource_id}"
            nodes = [item for item in nodes if item.get("node_id") == node_id]
            hotspots = [item for item in hotspots if item.get("node_id") == node_id]
            hold_edges = [item for item in hold_edges if _edge_obj_id(item) == resource_id]
            wait_edges = [item for item in wait_edges if _edge_obj_id(item) == resource_id]
        if task_id is not None:
            nodes = [
                item
                for item in nodes
                if item.get("node_id") == f"task:{task_id}" or item.get("node_id", "").startswith("obj:")
            ]
            hold_edges = [item for item in hold_edges if _edge_task_id(item) == task_id]
            wait_edges = [item for item in wait_edges if _edge_task_id(item) == task_id]
        return ResourceGraph(nodes=nodes, hold_edges=hold_edges, wait_edges=wait_edges, hotspot_stats=hotspots)

    def _build_export_bundle(
        self,
        record: DatasetRecord,
        mode: str,
        time_window: tuple[float, float],
        filter_spec: dict[str, Any],
    ) -> RebuildBundle:
        bundle = record.artifact.bundle
        if mode != "clipped":
            return bundle
        events: list[UnifiedEvent]
        if bundle.event_stream:
            events = list(
                query_events(
                    bundle,
                    {
                        **filter_spec,
                        "t_begin": time_window[0],
                        "t_end": time_window[1],
                    },
                    None,
                    len(bundle.event_stream),
                ).items
            )
        else:
            events = []
        task_states = [
            item
            for item in bundle.task_states
            if item.t_begin < time_window[1]
            and item.t_end > time_window[0]
            and self._matches_export_filter(item, filter_spec)
        ]
        exec_slices = [
            item
            for item in bundle.exec_slices
            if item.t_begin < time_window[1]
            and item.t_end > time_window[0]
            and self._matches_export_filter(item, filter_spec)
        ]
        irq_spans = [
            item
            for item in bundle.irq_spans
            if item.t_begin < time_window[1]
            and item.t_end > time_window[0]
            and self._matches_export_filter(item, filter_spec)
        ]
        untrusted_windows = [
            item for item in bundle.untrusted_windows if item.t_begin < time_window[1] and item.t_end > time_window[0]
        ]
        return RebuildBundle(
            bundle_id=bundle.bundle_id,
            dataset_id=bundle.dataset_id,
            event_stream=list(events),
            task_states=task_states,
            exec_slices=exec_slices,
            resource_graph=self._filter_graph(bundle.resource_graph, filter_spec),
            irq_spans=irq_spans,
            untrusted_windows=untrusted_windows,
            rebuild_rev=bundle.rebuild_rev,
            capability_flags=bundle.capability_flags,
            alignment=bundle.alignment,
            segment_metas=list(bundle.segment_metas),
            header=bundle.header,
            index_bundle=None,
        )

    def _source_alignment_offsets_from_bundle(self, bundle: RebuildBundle) -> Result[dict[str, Any]] | None:
        alignment = bundle.alignment
        if alignment is None:
            return None
        core_ids = sorted({int(item) for item in alignment.core_ids} | {int(item) for item in alignment.offsets})
        if not core_ids:
            return ok_result({"offsets": {}, "core_ids": [], "segments": {}})
        segments_by_core: dict[int, list[dict[str, float]]] = {}
        for segment in alignment.segments:
            segments_by_core.setdefault(int(segment.core_id), []).append(
                {
                    "t_begin": float(segment.t_begin),
                    "t_end": float(segment.t_end),
                    "offset_ns": float(segment.offset_ns),
                }
            )
        for core_id in core_ids:
            segments_by_core.setdefault(core_id, [])
        return ok_result(
            {
                "offsets": {
                    core_id: float(alignment.offsets.get(core_id, 0.0))
                    for core_id in core_ids
                },
                "core_ids": core_ids,
                "segments": segments_by_core,
            }
        )

    def _source_alignment_offsets(
        self,
        trace_paths: list[Path],
        *,
        dataset_id: str,
        dictionary: dict[str, Any] | str | Path | None,
    ) -> Result[dict[str, Any]]:
        anchors: dict[int, float] = {}
        core_ids: set[int] = set()
        max_timestamp_raw: float | None = None

        def _capture(decoded_events: list[DecodedEvent]) -> None:
            nonlocal max_timestamp_raw
            for event in decoded_events:
                core_ids.add(int(event.core_id))
                timestamp = float(event.timestamp_raw)
                if max_timestamp_raw is None or timestamp > max_timestamp_raw:
                    max_timestamp_raw = timestamp
                if event.event_name not in ALIGNMENT_EVENT_NAMES:
                    continue
                prior = anchors.get(int(event.core_id))
                if prior is None or timestamp < prior:
                    anchors[int(event.core_id)] = timestamp

        scanned = _scan_trace_source(
            trace_paths,
            dataset_id=dataset_id,
            dictionary=dictionary,
            on_events=_capture,
        )
        if not scanned.ok:
            return scanned
        if not core_ids:
            return ok_result({"offsets": {}, "core_ids": [], "segments": {}})
        if not anchors or len(anchors) != len(core_ids):
            return ok_result(
                {
                    "offsets": {core_id: 0.0 for core_id in sorted(core_ids)},
                    "core_ids": sorted(core_ids),
                    "segments": {core_id: [] for core_id in sorted(core_ids)},
                }
            )
        reference_core = min(anchors)
        reference_ts = float(anchors[reference_core])
        segment_end = float(max_timestamp_raw if max_timestamp_raw is not None else reference_ts)
        segments_by_core = {
            core_id: [
                {
                    "t_begin": float(anchors[core_id]),
                    "t_end": segment_end,
                    "offset_ns": float(reference_ts - anchors[core_id]),
                }
            ]
            for core_id in sorted(core_ids)
        }
        return ok_result(
            {
                "offsets": {
                    core_id: float(reference_ts - anchors[core_id])
                    for core_id in sorted(core_ids)
                },
                "core_ids": sorted(core_ids),
                "segments": segments_by_core,
            }
        )

    def _source_backed_event_in_scope(
        self,
        event: DecodedEvent,
        *,
        timestamp_aligned: float,
        time_window: tuple[float, float],
        filter_spec: dict[str, Any],
    ) -> bool:
        if timestamp_aligned < float(time_window[0]) or timestamp_aligned > float(time_window[1]):
            return False
        return self._matches_export_filter(event, filter_spec)

    def _source_backed_event_summary(
        self,
        record: DatasetRecord,
        export_bundle: RebuildBundle,
        *,
        export_window: tuple[float, float],
        export_filter: dict[str, Any],
        required_event_ref_keys: set[str],
        event_trace_path: Path,
        ref_index_path: Path,
        write_timings: dict[str, float] | None,
    ) -> Result[_SourceBackedEventSummary]:
        trace_paths = _source_trace_paths(record.artifact.source)
        if not trace_paths.ok:
            return trace_paths
        dictionary = (record.artifact.dictionary_info or {}).get("resolved_dictionary")
        dataset_id = record.artifact.bundle.dataset_id
        offset_result = self._source_alignment_offsets_from_bundle(record.artifact.bundle)
        if offset_result is None:
            offset_result = self._source_alignment_offsets(
                trace_paths.data,
                dataset_id=dataset_id,
                dictionary=dictionary,
            )
        if not offset_result.ok:
            return offset_result
        offsets = {
            int(core_id): float(offset)
            for core_id, offset in (offset_result.data.get("offsets") or {}).items()
        }
        raw_segments = offset_result.data.get("segments") or {}
        segments_by_core: dict[int, list[dict[str, float]]] = {
            int(core_id): [
                {
                    "t_begin": float(segment.get("t_begin", float("-inf"))),
                    "t_end": float(segment.get("t_end", float("inf"))),
                    "offset_ns": float(segment.get("offset_ns", 0.0)),
                }
                for segment in list(segment_rows or [])
            ]
            for core_id, segment_rows in dict(raw_segments).items()
        }
        core_ids = [int(item) for item in (offset_result.data.get("core_ids") or [])]
        has_segment_coverage = any(segments for segments in segments_by_core.values())
        cause_event_refs = {
            str(item.cause_event)
            for item in export_bundle.task_states
            if item.cause_event
        }
        required_refs = {
            ref_key
            for ref_key in {_normalize_ref_key(item) for item in required_event_ref_keys}
            if ref_key is not None
        }
        producer_ver = str(record.artifact.header.producer_ver or "python-sim-0.1")
        run_id = str(record.artifact.header.run_id or dataset_id)

        with tempfile.TemporaryDirectory(prefix="source-backed-export-", dir=str(event_trace_path.parent)) as temp_dir:
            spool_dir = Path(temp_dir)
            spool_handles: dict[int, Any] = {}
            analysis_accumulator = _SourceBackedAnalysisAccumulator(
                dataset_id=dataset_id,
                cause_event_refs=cause_event_refs,
            )
            forced_required_ref_keys: set[str] = set()

            def _spool_events(decoded_events: list[DecodedEvent]) -> None:
                for event in decoded_events:
                    core_id = int(event.core_id)
                    timestamp_raw = float(event.timestamp_raw)
                    aligned_ts = timestamp_raw + offsets.get(core_id, 0.0)
                    if has_segment_coverage:
                        aligned_ts = timestamp_raw
                        for segment in segments_by_core.get(core_id, []):
                            if segment["t_begin"] <= timestamp_raw <= segment["t_end"]:
                                aligned_ts = timestamp_raw + segment["offset_ns"]
                                break
                    aligned_ts_i64 = _clamp_aligned_timestamp(aligned_ts)
                    event_ref_key = event_ref_key_for(event, dataset_id)
                    in_scope = self._source_backed_event_in_scope(
                        event,
                        timestamp_aligned=float(aligned_ts_i64),
                        time_window=export_window,
                        filter_spec=export_filter,
                    )
                    include_due_to_required_ref = event_ref_key in required_refs
                    if not in_scope and not include_due_to_required_ref:
                        continue
                    if include_due_to_required_ref and not in_scope:
                        forced_required_ref_keys.add(event_ref_key)
                    handle = spool_handles.get(core_id)
                    if handle is None:
                        handle = (spool_dir / f"core_{core_id:04d}.bin").open("wb")
                        spool_handles[core_id] = handle
                    payload_bytes = _encode_payload(int(event.event_id), dict(event.payload))
                    handle.write(_ALIGNED_TS_STRUCT.pack(int(aligned_ts_i64)))
                    handle.write(
                        EVENT_HEADER_STRUCT.pack(
                            1,
                            1 if event.trust_tags else 0,
                            core_id,
                            int(event.event_id),
                            int(event.seq),
                            int(event.timestamp_raw),
                            len(payload_bytes),
                        )
                    )
                    handle.write(payload_bytes)
                    if in_scope:
                        analysis_accumulator.observe(event, aligned_ts=float(aligned_ts_i64))

            spool_started = time.perf_counter()
            scanned = _scan_trace_source(
                trace_paths.data,
                dataset_id=dataset_id,
                dictionary=dictionary,
                on_events=_spool_events,
            )
            _record_duration(write_timings, "encode_trace_seconds", time.perf_counter() - spool_started)
            for handle in spool_handles.values():
                handle.close()
            if not scanned.ok:
                return scanned

            writer = _StreamingTraceWriter(
                event_trace_path,
                producer_ver=producer_ver,
                run_id=run_id,
                dict_ver=int(record.artifact.header.dict_ver),
                core_count=max(core_ids, default=0) + 1,
            )
            summary = _SourceBackedEventSummary()
            with ref_index_path.open("w", encoding="utf-8") as ref_handle, ExitStack() as stack:
                ref_handle.write("[\n")
                heap: list[tuple[int, int, int, Any, dict[str, Any]]] = []
                for spool_path in sorted(spool_dir.glob("core_*.bin")):
                    handle = stack.enter_context(spool_path.open("rb"))
                    record_payload = _read_spooled_event(handle)
                    if record_payload is None:
                        continue
                    heapq.heappush(
                        heap,
                        (
                            int(record_payload["aligned_ts"]),
                            int(record_payload["core_id"]),
                            int(record_payload["seq"]),
                            handle,
                            record_payload,
                        ),
                    )
                first_row = True
                while heap:
                    _aligned_ts, _core_id, _seq, handle, record_payload = heapq.heappop(heap)
                    write_started = time.perf_counter()
                    writer.append(
                        core_id=int(record_payload["core_id"]),
                        event_id=int(record_payload["event_id"]),
                        seq=int(record_payload["seq"]),
                        timestamp_raw=int(record_payload["timestamp_raw"]),
                        flags=int(record_payload["flags"]),
                        payload_bytes=record_payload["payload_bytes"],
                    )
                    _record_duration(write_timings, "encode_trace_seconds", time.perf_counter() - write_started)
                    event_uid = f"evt:{dataset_id}:{int(record_payload['core_id'])}:{int(record_payload['seq'])}"
                    ref_key = event_uid
                    ref_row = {
                        "ordinal": summary.count,
                        "event_uid": event_uid,
                        "ref_key": ref_key,
                        "core_id": int(record_payload["core_id"]),
                        "seq": int(record_payload["seq"]),
                        "timestamp_raw": float(record_payload["timestamp_raw"]),
                        "timestamp_aligned": float(record_payload["aligned_ts"]),
                    }
                    json_started = time.perf_counter()
                    if not first_row:
                        ref_handle.write(",\n")
                    ref_handle.write(_json_dumps_compact(ref_row))
                    _record_duration(write_timings, "json_dump_seconds", time.perf_counter() - json_started)
                    first_row = False
                    if summary.first_ref_key is None:
                        summary.first_ref_key = ref_key
                    summary.last_ref_key = ref_key
                    summary.count += 1
                    summary.exported_ref_keys.add(ref_key)
                    next_payload = _read_spooled_event(handle)
                    if next_payload is not None:
                        heapq.heappush(
                            heap,
                            (
                                int(next_payload["aligned_ts"]),
                                int(next_payload["core_id"]),
                                int(next_payload["seq"]),
                                handle,
                                next_payload,
                            ),
                        )
                json_started = time.perf_counter()
                ref_handle.write("\n]\n")
                _record_duration(write_timings, "json_dump_seconds", time.perf_counter() - json_started)
            writer.finalize()
            analysis_accumulator.populate_summary(summary, capability_flags=export_bundle.capability_flags)
            summary.required_event_ref_count = len(required_refs)
            summary.closure_forced_event_count = len(forced_required_ref_keys)
        return ok_result(summary, warnings=scanned.warnings, untrusted_windows=scanned.untrusted_windows)

    def _export_analysis_session(
        self,
        record: DatasetRecord,
        export_bundle: RebuildBundle,
        *,
        source_backed_write: bool,
    ) -> Result[MetricSession]:
        if not source_backed_write:
            return ok_result(record.metric_session)
        metric_bundle = RebuildBundle(
            bundle_id=export_bundle.bundle_id,
            dataset_id=export_bundle.dataset_id,
            event_stream=[],
            task_states=list(export_bundle.task_states),
            exec_slices=list(export_bundle.exec_slices),
            resource_graph=export_bundle.resource_graph,
            irq_spans=list(export_bundle.irq_spans),
            untrusted_windows=list(export_bundle.untrusted_windows),
            rebuild_rev=export_bundle.rebuild_rev,
            capability_flags=export_bundle.capability_flags,
            alignment=export_bundle.alignment,
            segment_metas=list(export_bundle.segment_metas),
            header=export_bundle.header or record.artifact.header,
            index_bundle=None,
        )
        session_result = metric_Init(record.metric_session.cfg)
        if not session_result.ok:
            return session_result
        session = session_result.data
        ingested = metric_Ingest(session, metric_bundle)
        if not ingested.ok:
            return Result(
                code=ingested.code,
                message=ingested.message,
                warnings=ingested.warnings,
                untrusted_windows=ingested.untrusted_windows,
            )
        return ok_result(session)

    def _source_backed_deadline_metric(
        self,
        export_bundle: RebuildBundle,
        event_summary: _SourceBackedEventSummary,
        *,
        export_window: tuple[float, float],
        export_filter: dict[str, Any],
    ) -> MetricResult:
        trusted = _bundle_trusted(export_bundle, export_window[0], export_window[1])
        deadline_records = list(event_summary.deadline_records)
        deadline_miss_values = [float(item["deadline_miss"]) for item in deadline_records]
        ranked_records = sorted(
            deadline_records,
            key=lambda record: (-float(record["deadline_miss"]), int(record["task_id"]), float(record["release_ts"])),
        )
        return MetricResult(
            metric_id="deadline_miss",
            scope={"t_begin": export_window[0], "t_end": export_window[1], "filter": export_filter},
            series=[
                {
                    "task_id": int(item["task_id"]),
                    "job_id": item["job_id"],
                    "instance_id": item["instance_id"],
                    "response_time": round(float(item["response_time"]), 6),
                    "deadline_ts": round(float(item["deadline_ts"]), 6),
                    "deadline_miss": round(float(item["deadline_miss"]), 6),
                    "semantics": item["semantics"],
                }
                for item in ranked_records
            ],
            distribution=_distribution(deadline_miss_values),
            topn=[
                {
                    "task_id": int(item["task_id"]),
                    "job_id": item["job_id"],
                    "instance_id": item["instance_id"],
                    "deadline_miss": round(float(item["deadline_miss"]), 6),
                    "semantics": item["semantics"],
                }
                for item in ranked_records[:10]
            ],
            summary={
                "record_count": len(deadline_records),
                "candidate_count": int(event_summary.deadline_candidate_count),
                "miss_count": len([item for item in deadline_records if float(item["deadline_miss"]) > 0.0]),
                "max_deadline_miss": max(deadline_miss_values, default=0.0),
                "semantics_status": event_summary.deadline_semantics_status,
                "precise_record_count": int(event_summary.deadline_precise_record_count),
                "degraded_record_count": int(event_summary.deadline_degraded_record_count),
                "incomplete_count": int(event_summary.deadline_incomplete_count),
                "conflict_count": int(event_summary.deadline_conflict_count),
                "issue_count": len(event_summary.deadline_issue_samples),
                "issue_types": sorted({item["issue_type"] for item in event_summary.deadline_issue_samples}),
                **_distribution_summary(deadline_miss_values),
            },
            trusted=trusted and event_summary.deadline_semantics_status != "unsupported",
        )

    def _source_backed_context_switch_metric(
        self,
        export_bundle: RebuildBundle,
        event_summary: _SourceBackedEventSummary,
        *,
        export_window: tuple[float, float],
        export_filter: dict[str, Any],
    ) -> MetricResult:
        trusted = _bundle_trusted(export_bundle, export_window[0], export_window[1])
        return MetricResult(
            metric_id="context_switch_count",
            scope={"t_begin": export_window[0], "t_end": export_window[1], "filter": export_filter},
            series=[{"count": int(event_summary.context_switch_count)}],
            distribution=[],
            topn=[],
            summary={"count": int(event_summary.context_switch_count)},
            trusted=trusted,
        )

    def _merge_source_backed_metrics(
        self,
        metrics: list[MetricResult],
        export_bundle: RebuildBundle,
        event_summary: _SourceBackedEventSummary,
        *,
        export_window: tuple[float, float],
        export_filter: dict[str, Any],
    ) -> list[MetricResult]:
        replacements = {
            "deadline_miss": self._source_backed_deadline_metric(
                export_bundle,
                event_summary,
                export_window=export_window,
                export_filter=export_filter,
            ),
            "context_switch_count": self._source_backed_context_switch_metric(
                export_bundle,
                event_summary,
                export_window=export_window,
                export_filter=export_filter,
            ),
        }
        ordered_metrics = [replacements.get(metric.metric_id, metric) for metric in metrics]
        existing_ids = {metric.metric_id for metric in ordered_metrics}
        for metric_id in ("deadline_miss", "context_switch_count"):
            if metric_id not in existing_ids:
                ordered_metrics.append(replacements[metric_id])
        return ordered_metrics

    def _source_backed_deadline_alerts(
        self,
        export_bundle: RebuildBundle,
        event_summary: _SourceBackedEventSummary,
        *,
        export_window: tuple[float, float],
    ) -> list[Alert]:
        trusted = _bundle_trusted(export_bundle, export_window[0], export_window[1])
        alerts: list[Alert] = []
        for item in event_summary.deadline_records:
            if float(item["deadline_miss"]) <= 0.0:
                continue
            evidence_refs = [
                EvidenceRef(
                    ref_type="event",
                    ref_key=str(item["release_ref_key"]),
                    t_begin=float(item["release_ts"]),
                    t_end=float(item["release_ts"]),
                )
            ]
            if item.get("finish_ref_key") is not None:
                evidence_refs.append(
                    EvidenceRef(
                        ref_type="event",
                        ref_key=str(item["finish_ref_key"]),
                        t_begin=float(item["finish_ts"]),
                        t_end=float(item["finish_ts"]),
                    )
                )
            alerts.append(
                Alert(
                    alert_id=(
                        f"alert:deadline:{int(item['task_id'])}:"
                        f"{item['job_id'] if item['job_id'] is not None else 'na'}:"
                        f"{item['instance_id'] if item['instance_id'] is not None else 'na'}"
                    ),
                    type="deadline_miss",
                    severity="warning",
                    time_window=(float(item["release_ts"]), float(item["finish_ts"])),
                    object_scope={
                        "task_id": int(item["task_id"]),
                        "job_id": item["job_id"],
                        "instance_id": item["instance_id"],
                        "semantics": item["semantics"],
                    },
                    threshold=max(0.0, float(item["deadline_ts"]) - float(item["release_ts"])),
                    actual=float(item["response_time"]),
                    evidence_refs=evidence_refs,
                    support_level=_support_level(trusted and item["semantics"] == "precise"),
                    trusted=trusted and item["semantics"] == "precise",
                )
            )
        if event_summary.deadline_issue_samples:
            issue_window_begin = min(float(item["release_ts"]) for item in event_summary.deadline_issue_samples)
            issue_window_end = max(
                float(item["finish_ts"] if item["finish_ts"] is not None else item["release_ts"])
                for item in event_summary.deadline_issue_samples
            )
            alerts.append(
                Alert(
                    alert_id=f"alert:deadline_gap:{int(issue_window_begin)}:{int(issue_window_end)}",
                    type="deadline_semantics_gap",
                    severity="warning",
                    time_window=(issue_window_begin, issue_window_end),
                    object_scope={
                        "issue_count": len(event_summary.deadline_issue_samples),
                        "issue_types": sorted({item["issue_type"] for item in event_summary.deadline_issue_samples}),
                        "candidate_count": int(event_summary.deadline_candidate_count),
                        "incomplete_count": int(event_summary.deadline_incomplete_count),
                        "conflict_count": int(event_summary.deadline_conflict_count),
                        "semantics_status": event_summary.deadline_semantics_status,
                    },
                    threshold=0,
                    actual=float(event_summary.deadline_incomplete_count + event_summary.deadline_conflict_count),
                    evidence_refs=[
                        EvidenceRef(
                            ref_type="event",
                            ref_key=str(item["release_ref_key"]),
                            t_begin=float(item["release_ts"]),
                            t_end=float(item["release_ts"]),
                        )
                        for item in event_summary.deadline_issue_samples[:3]
                        if item.get("release_ref_key") is not None
                    ],
                    support_level=_support_level(False, event_summary.deadline_semantics_status),
                    trusted=False,
                )
            )
        return alerts

    def _source_backed_priority_inversion_alerts(
        self,
        export_bundle: RebuildBundle,
        event_summary: _SourceBackedEventSummary,
        *,
        export_window: tuple[float, float],
        export_filter: dict[str, Any],
    ) -> list[Alert]:
        window_states = [
            item
            for item in export_bundle.task_states
            if item.t_begin < export_window[1]
            and item.t_end > export_window[0]
            and self._matches_export_filter(item, export_filter)
        ]
        window_slices = [
            item
            for item in export_bundle.exec_slices
            if item.t_begin < export_window[1]
            and item.t_end > export_window[0]
            and self._matches_export_filter(item, export_filter)
        ]
        blocked_time = 0.0
        for segment in window_states:
            blocked_time = min(segment.t_end, export_window[1]) - max(segment.t_begin, export_window[0])

        alerts: list[Alert] = []
        for segment in window_states:
            if segment.state != "BLOCKED" or segment.related_obj is None:
                continue
            owner_task_id = event_summary.owner_task_id_by_cause_event_ref.get(segment.cause_event)
            if owner_task_id is None:
                continue
            blocked_prio = event_summary.priority_by_task.get(int(segment.task_id))
            owner_prio = event_summary.priority_by_task.get(int(owner_task_id))
            if blocked_prio is None or owner_prio is None or blocked_prio >= owner_prio:
                continue
            medium_slices = [
                item
                for item in window_slices
                if item.task_id not in {int(segment.task_id), int(owner_task_id)}
                and item.t_begin < segment.t_end
                and item.t_end > segment.t_begin
                and blocked_prio < event_summary.priority_by_task.get(int(item.task_id), owner_prio + 1) < owner_prio
            ]
            if not medium_slices:
                continue
            trusted = bool(segment.trusted and all(item.trusted for item in medium_slices))
            alerts.append(
                Alert(
                    alert_id=f"alert:inversion:{segment.seg_id}",
                    type="priority_inversion",
                    severity="warning",
                    time_window=(segment.t_begin, segment.t_end),
                    object_scope={
                        "task_id": int(segment.task_id),
                        "owner_task_id": int(owner_task_id),
                        "resource_id": segment.related_obj,
                        "medium_task_ids": sorted({int(item.task_id) for item in medium_slices}),
                    },
                    threshold=0,
                    actual=blocked_time,
                    evidence_refs=[
                        EvidenceRef(
                            ref_type="event",
                            ref_key=segment.cause_event,
                            t_begin=segment.t_begin,
                            t_end=segment.t_end,
                        )
                    ]
                    + [
                        EvidenceRef(
                            ref_type="slice",
                            ref_key=item.slice_id,
                            t_begin=item.t_begin,
                            t_end=item.t_end,
                        )
                        for item in medium_slices[:3]
                    ],
                    support_level=_support_level(trusted),
                    trusted=trusted,
                )
            )
        return alerts

    def _merge_source_backed_alerts(
        self,
        alerts: list[Alert],
        export_bundle: RebuildBundle,
        event_summary: _SourceBackedEventSummary,
        *,
        export_window: tuple[float, float],
        export_filter: dict[str, Any],
    ) -> list[Alert]:
        event_alerts = self._source_backed_deadline_alerts(
            export_bundle,
            event_summary,
            export_window=export_window,
        ) + self._source_backed_priority_inversion_alerts(
            export_bundle,
            event_summary,
            export_window=export_window,
            export_filter=export_filter,
        )
        if not event_alerts:
            return alerts
        merged_alerts: list[Alert] = []
        inserted = False
        for alert in alerts:
            if alert.type != "long_block" and not inserted:
                merged_alerts.extend(event_alerts)
                inserted = True
            merged_alerts.append(alert)
        if not inserted:
            merged_alerts.extend(event_alerts)
        return merged_alerts

    def _normalized_meta(self, meta: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(meta)
        normalized.pop("export_time", None)
        normalized.pop("snapshot_id", None)
        normalized.pop("run_batch_id", None)
        normalized.pop("version_id", None)
        normalized.pop("experiment_params", None)
        normalized.pop("selection", None)
        normalized.pop("zoom_level", None)
        normalized.pop("focused_view", None)
        normalized.pop("evidence_anchor", None)
        normalized.pop("context_padding_rule", None)
        normalized.pop("dictionary_status", None)
        dict_ref = dict(normalized.get("dict_ref") or {})
        dict_ref.pop("checksum", None)
        normalized["dict_ref"] = dict_ref
        export_source = dict(normalized.get("export_source") or {})
        export_source.pop("job_id", None)
        export_source.pop("write_mode", None)
        export_source.pop("source", None)
        normalized["export_source"] = export_source
        export_scope = dict(normalized.get("export_scope") or {})
        export_scope.pop("write_mode", None)
        normalized["export_scope"] = export_scope
        analysis_context = dict(normalized.get("analysis_context") or {})
        analysis_context.pop("selection", None)
        analysis_context.pop("zoom_level", None)
        analysis_context.pop("focused_view", None)
        analysis_context.pop("playback_cursor", None)
        analysis_context.pop("evidence_anchor", None)
        normalized["analysis_context"] = analysis_context
        return _normalize_export_payload(normalized)

    def _normalized_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            key: value
            for key, value in manifest.items()
            if key not in {"created_at", "snapshot_id"}
        }
        dict_ref = dict(normalized.get("dict_ref") or {})
        dict_ref.pop("checksum", None)
        normalized["dict_ref"] = dict_ref
        entries = []
        for entry in manifest.get("entries", []):
            entries.append(
                {
                    key: value
                    for key, value in entry.items()
                    if key != "checksum"
                }
            )
        normalized["entries"] = sorted(entries, key=lambda item: item["path"])
        return _normalize_export_payload(normalized)

    def export_NormalizePackage(self, path_or_stream: str) -> Result[dict[str, Any]]:
        loaded = _load_validated_package(path_or_stream)
        if not loaded.ok:
            return loaded
        package_path = Path(loaded.data["package_path"])
        analysis_context = dict(json_load(package_path / "context" / "analysis_context.json"))
        analysis_context.pop("selection", None)
        analysis_context.pop("zoom_level", None)
        analysis_context.pop("focused_view", None)
        analysis_context.pop("playback_cursor", None)
        analysis_context.pop("evidence_anchor", None)
        normalized = {
            "meta": self._normalized_meta(loaded.data["meta"]),
            "manifest": self._normalized_manifest(loaded.data["manifest"]),
            "analysis_context": _normalize_export_payload(analysis_context),
            "compare_scope": _normalize_export_payload(json_load(package_path / "context" / "compare_scope.json")),
            "anchors": _normalize_export_payload(json_load(package_path / "context" / "anchors.json")),
            "bookmarks": _normalize_export_payload(json_load(package_path / "context" / "bookmarks.json")),
            "ref_index": _normalize_export_payload(json_load(package_path / "event" / "ref_index.json")),
            "rebuild_bundle": _normalize_export_payload(loaded.data["bundle"].to_dict()),
            "dictionary": _normalize_export_payload(
                (loaded.data.get("dictionary_info") or {}).get("resolved_dictionary") or {}
            ),
        }
        metrics_path = package_path / "result" / "metrics.json"
        alerts_path = package_path / "result" / "alerts.json"
        diagnoses_path = package_path / "result" / "diagnoses.json"
        normalized["metrics"] = _normalize_export_payload(json_load(metrics_path) if metrics_path.exists() else [])
        normalized["alerts"] = _normalize_export_payload(json_load(alerts_path) if alerts_path.exists() else [])
        normalized["diagnoses"] = _normalize_export_payload(json_load(diagnoses_path) if diagnoses_path.exists() else [])
        evidence_control = load_evidence_control(package_path, loaded.data["meta"], loaded.data["manifest"])
        if not evidence_control.ok:
            return evidence_control
        if evidence_control.data:
            for key, value in evidence_control.data.items():
                normalized[key] = _normalize_export_payload(value)
        return ok_result(normalized)

    def export_NormalizePackagePerfOnly(
        self,
        path_or_stream: str,
        *,
        meta: dict[str, Any] | None,
        manifest: dict[str, Any] | None,
    ) -> Result[dict[str, Any]]:
        package_path = Path(path_or_stream)
        if not package_path.exists():
            return err_result("INVALID_ARG", f"package not found: {path_or_stream}")
        if not (package_path / "manifest.json").exists() or not (package_path / "meta.json").exists():
            return err_result("INVALID_ARG", "package missing manifest or meta")
        if not isinstance(meta, dict) or not isinstance(manifest, dict):
            return err_result("INVALID_ARG", "perf-only normalize fast path requires in-memory meta and manifest")
        return ok_result(
            {
                "meta": self._normalized_meta(meta),
                "manifest": self._normalized_manifest(manifest),
            }
        )

    def export_WritePackage(self, job_id: str, output_path: str) -> Result[dict[str, Any]]:
        job = self.jobs.get(job_id)
        if job.get("mode") == "evidence":
            return self.export_WriteEvidencePackage(job_id, output_path)
        dataset_id = job.get("dataset_id")
        if dataset_id is None:
            dataset_id = next(iter(self.repository.list_ids()), None)
        if dataset_id is None:
            return err_result("NOT_READY", "no dataset available for export")
        record = self.repository.get(dataset_id)
        package_path = Path(output_path)
        for relative in ["event", "rebuild", "result", "context", "reference/schema"]:
            (package_path / relative).mkdir(parents=True, exist_ok=True)

        snapshot_context = dict(job.get("context") or self.context_store.get().persisted_dict())
        mode = job.get("mode", "full")
        bundle = record.artifact.bundle
        compare_scope_result = _normalize_compare_scope_payload(snapshot_context.get("compare_scope"))
        if not compare_scope_result.ok:
            return Result(
                code=compare_scope_result.code,
                message=compare_scope_result.message,
                warnings=compare_scope_result.warnings,
                untrusted_windows=compare_scope_result.untrusted_windows,
            )
        snapshot_context["compare_scope"] = compare_scope_result.data
        full_window = _bundle_time_window(bundle)
        context_window = tuple(snapshot_context.get("time_window", full_window))
        context_filter = dict(snapshot_context.get("filter", {}))
        if mode == "full":
            export_window = full_window
            export_filter: dict[str, Any] = {}
        else:
            export_window = context_window
            export_filter = context_filter
        write_timings: dict[str, float] = {
            "prepare_seconds": 0.0,
            "encode_trace_seconds": 0.0,
            "json_dump_seconds": 0.0,
            "csv_write_seconds": 0.0,
            "checksum_seconds": 0.0,
            "reference_copy_seconds": 0.0,
        }

        def _progress(substage: str, *, status: str, count: int | None = None) -> None:
            payload: dict[str, Any] = {
                "category": "export_write",
                "substage": substage,
                "status": status,
            }
            if count is not None:
                payload["count"] = int(count)
            self._emit_progress(**payload)

        def _measure_export_write(
            substage: str,
            bucket_key: str,
            callback: Callable[..., Any],
            *args: Any,
            count: int | None = None,
            **kwargs: Any,
        ) -> Any:
            _progress(substage, status="started")
            result = _measure_duration(write_timings, bucket_key, callback, *args, **kwargs)
            completed_count = count if count is not None else (int(result) if isinstance(result, int) else None)
            _progress(substage, status="completed", count=completed_count)
            return result

        _progress("prepare/build_export_bundle", status="started")
        prepare_started = time.perf_counter()
        export_bundle = self._build_export_bundle(record, mode, export_window, export_filter)

        event_trace_path = package_path / "event" / "events.trace"
        ref_index_path = package_path / "event" / "ref_index.json"
        rebuild_path = package_path / "rebuild" / "rebuild_bundle.json"
        exec_csv_path = package_path / "rebuild" / "exec_slices.csv"
        exec_json_path = package_path / "rebuild" / "exec_slices.json"
        task_csv_path = package_path / "rebuild" / "task_states.csv"
        task_json_path = package_path / "rebuild" / "task_states.json"
        irq_csv_path = package_path / "rebuild" / "irq_spans.csv"
        irq_json_path = package_path / "rebuild" / "irq_spans.json"
        graph_path = package_path / "rebuild" / "resource_graph.json"
        windows_path = package_path / "rebuild" / "untrusted_windows.json"
        metric_csv_path = package_path / "result" / "metrics.csv"
        metric_path = package_path / "result" / "metrics.json"
        hotspot_csv_path = package_path / "result" / "hotspots.csv"
        alert_csv_path = package_path / "result" / "alerts.csv"
        alert_path = package_path / "result" / "alerts.json"
        diag_path = package_path / "result" / "diagnoses.json"
        context_path = package_path / "context" / "analysis_context.json"
        compare_path = package_path / "context" / "compare_scope.json"
        anchors_path = package_path / "context" / "anchors.json"
        bookmarks_path = package_path / "context" / "bookmarks.json"
        meta_path = package_path / "meta.json"
        manifest_path = package_path / "manifest.json"

        exec_count = len(export_bundle.exec_slices)
        task_count = len(export_bundle.task_states)
        irq_count = len(export_bundle.irq_spans)
        hotspot_rows = list(export_bundle.resource_graph.hotspot_stats)
        window_rows = [_json_default(item) for item in export_bundle.untrusted_windows]
        window_count = len(window_rows)
        _record_duration(write_timings, "prepare_seconds", time.perf_counter() - prepare_started)
        _progress("prepare/build_export_bundle", status="completed")
        source_backed_write = not bool(bundle.event_stream)
        required_event_ref_keys = _collect_required_event_ref_keys(
            export_bundle,
            context=snapshot_context,
        )
        event_summary = _SourceBackedEventSummary(write_mode="materialized_bundle")
        materialized_export_events = list(export_bundle.event_stream)
        exported_event_ref_keys: set[str] = set()
        if source_backed_write:
            _progress("event/source_backed", status="started")
            source_written = self._source_backed_event_summary(
                record,
                export_bundle,
                export_window=export_window,
                export_filter=export_filter,
                required_event_ref_keys=required_event_ref_keys,
                event_trace_path=event_trace_path,
                ref_index_path=ref_index_path,
                write_timings=write_timings,
            )
            if not source_written.ok:
                return source_written
            event_summary = source_written.data
            exported_event_ref_keys = set(event_summary.exported_ref_keys)
            _progress("event/source_backed", status="completed", count=event_summary.count)
        analysis_prepare_started = time.perf_counter()
        _progress("prepare/analysis_session", status="started")
        analysis_session_result = self._export_analysis_session(
            record,
            export_bundle,
            source_backed_write=source_backed_write,
        )
        if not analysis_session_result.ok:
            return analysis_session_result
        analysis_session = analysis_session_result.data
        _progress("prepare/analysis_session", status="completed")
        _progress("prepare/metrics_and_alerts", status="started")
        metrics = metric_Compute(
            analysis_session,
            export_window[0],
            export_window[1],
            export_filter,
        ).data or []
        if source_backed_write:
            metrics = self._merge_source_backed_metrics(
                metrics,
                export_bundle,
                event_summary,
                export_window=export_window,
                export_filter=export_filter,
            )
        analysis_session.latest_metrics = metrics
        alerts = alert_Evaluate(
            analysis_session,
            export_window[0],
            export_window[1],
            export_filter,
        ).data or []
        if source_backed_write:
            alerts = self._merge_source_backed_alerts(
                alerts,
                export_bundle,
                event_summary,
                export_window=export_window,
                export_filter=export_filter,
            )
        analysis_session.latest_alerts = alerts
        metric_rows = [_json_default(item) for item in metrics]
        metric_count = len(metric_rows)
        diagnosis_rows = list(self._iter_diagnosis_rows(alerts))
        anchor_rows = list(self._iter_anchor_rows(snapshot_context, alerts))
        required_event_ref_keys = _collect_required_event_ref_keys(
            export_bundle,
            context=snapshot_context,
            alerts=alerts,
            diagnoses=diagnosis_rows,
            anchors=anchor_rows,
        )
        if source_backed_write:
            event_summary.required_event_ref_count = len(required_event_ref_keys)
        else:
            materialized_export_events, exported_event_ref_keys, closure_forced_event_count = (
                _materialized_event_stream_with_required_refs(
                    materialized_export_events,
                    source_events=list(bundle.event_stream),
                    required_event_ref_keys=required_event_ref_keys,
                )
            )
            export_bundle.event_stream = materialized_export_events
            ref_index = _materialized_ref_index_rows(materialized_export_events)
            event_summary.count = len(ref_index)
            if ref_index:
                event_summary.first_ref_key = str(ref_index[0]["ref_key"])
                event_summary.last_ref_key = str(ref_index[-1]["ref_key"])
            event_summary.required_event_ref_count = len(required_event_ref_keys)
            event_summary.closure_forced_event_count = int(closure_forced_event_count)
            event_summary.exported_ref_keys = set(exported_event_ref_keys)
            trace_events = [_event_record_from_unified_event(event) for event in materialized_export_events]
            _measure_export_write(
                "event/events.trace",
                "encode_trace_seconds",
                encode_trace,
                event_trace_path,
                trace_events,
                count=len(trace_events),
                producer_ver=record.artifact.header.producer_ver,
                run_id=record.artifact.header.run_id or export_bundle.dataset_id,
            )
            _measure_export_write(
                "event/ref_index.json",
                "json_dump_seconds",
                _json_dump_without_reserialize,
                ref_index_path,
                ref_index,
                count=len(ref_index),
                compact=True,
            )
        if mode == "clipped":
            missing_event_refs = _missing_required_event_refs(required_event_ref_keys, exported_event_ref_keys)
            if missing_event_refs:
                sample = ", ".join(missing_event_refs[:5])
                suffix = "..." if len(missing_event_refs) > 5 else ""
                return err_result(
                    "INVALID_ARG",
                    (
                        "clipped export event reference closure incomplete: "
                        f"{len(missing_event_refs)} ref(s) missing in event/ref_index.json "
                        f"(sample: {sample}{suffix})"
                    ),
                )
        _record_duration(write_timings, "prepare_seconds", time.perf_counter() - analysis_prepare_started)
        _progress("prepare/metrics_and_alerts", status="completed")
        rebuild_payload = {
            "alignment": export_bundle.alignment,
            "bundle_id": export_bundle.bundle_id,
            "capability_flags": export_bundle.capability_flags,
            "dataset_id": export_bundle.dataset_id,
            "event_stream": export_bundle.event_stream,
            "exec_slices": export_bundle.exec_slices,
            "header": export_bundle.header,
            "index_bundle": export_bundle.index_bundle,
            "irq_spans": export_bundle.irq_spans,
            "rebuild_rev": export_bundle.rebuild_rev,
            "resource_graph": export_bundle.resource_graph,
            "segment_metas": export_bundle.segment_metas,
            "task_states": export_bundle.task_states,
            "untrusted_windows": window_rows,
        }
        _measure_export_write(
            "rebuild/rebuild_bundle.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            rebuild_path,
            rebuild_payload,
            count=event_summary.count,
            compact=True,
        )
        _measure_export_write(
            "rebuild/exec_slices.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            exec_json_path,
            export_bundle.exec_slices,
            count=exec_count,
            compact=True,
        )
        _measure_export_write(
            "rebuild/task_states.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            task_json_path,
            export_bundle.task_states,
            count=task_count,
            compact=True,
        )
        _measure_export_write(
            "rebuild/irq_spans.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            irq_json_path,
            export_bundle.irq_spans,
            count=irq_count,
            compact=True,
        )
        _measure_export_write(
            "rebuild/resource_graph.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            graph_path,
            export_bundle.resource_graph,
            compact=True,
        )
        _measure_export_write(
            "rebuild/untrusted_windows.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            windows_path,
            window_rows,
            count=window_count,
            compact=True,
        )
        _measure_export_write(
            "result/metrics.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            metric_path,
            metric_rows,
            count=metric_count,
            compact=True,
        )
        alert_count = _measure_export_write(
            "result/alerts.json",
            "json_dump_seconds",
            _write_json_array_streaming,
            alert_path,
            self._iter_alert_rows(alerts),
        )
        diagnosis_count = _measure_export_write(
            "result/diagnoses.json",
            "json_dump_seconds",
            _write_json_array_streaming,
            diag_path,
            diagnosis_rows,
        )
        _measure_export_write(
            "context/analysis_context.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            context_path,
            snapshot_context,
        )
        compare_scope = snapshot_context.get("compare_scope")
        _measure_export_write(
            "context/compare_scope.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            compare_path,
            compare_scope or {},
        )
        anchor_count = _measure_export_write(
            "context/anchors.json",
            "json_dump_seconds",
            _write_json_array_streaming,
            anchors_path,
            anchor_rows,
        )
        _measure_export_write(
            "context/bookmarks.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            bookmarks_path,
            [],
        )

        _measure_export_write(
            "rebuild/exec_slices.csv",
            "csv_write_seconds",
            _write_csv,
            exec_csv_path,
            ["slice_id", "task_id", "core_id", "t_begin", "t_end", "start_event", "end_event", "preempted_by", "run_reason", "trusted"],
            self._iter_flat_dataclass_rows(export_bundle.exec_slices),
            count=exec_count,
        )
        _measure_export_write(
            "rebuild/task_states.csv",
            "csv_write_seconds",
            _write_csv,
            task_csv_path,
            ["seg_id", "task_id", "state", "t_begin", "t_end", "cause_event", "related_obj", "trusted"],
            self._iter_flat_dataclass_rows(export_bundle.task_states),
            count=task_count,
        )
        _measure_export_write(
            "rebuild/irq_spans.csv",
            "csv_write_seconds",
            _write_csv,
            irq_csv_path,
            ["irq_span_id", "irq_id", "core_id", "nesting_depth", "t_begin", "t_end", "delayed_task", "trusted"],
            self._iter_flat_dataclass_rows(export_bundle.irq_spans),
            count=irq_count,
        )
        _measure_export_write(
            "result/metrics.csv",
            "csv_write_seconds",
            _write_csv,
            metric_csv_path,
            ["metric_id", "scope", "series", "distribution", "topn", "summary", "trusted"],
            metric_rows,
            count=metric_count,
        )
        _measure_export_write(
            "result/hotspots.csv",
            "csv_write_seconds",
            _write_csv,
            hotspot_csv_path,
            ["node_id", "count"],
            hotspot_rows,
            count=len(hotspot_rows),
        )
        _measure_export_write(
            "result/alerts.csv",
            "csv_write_seconds",
            _write_csv,
            alert_csv_path,
            ["alert_id", "type", "severity", "time_window", "object_scope", "threshold", "actual", "evidence_refs", "trusted"],
            self._iter_alert_rows(alerts),
            count=alert_count,
        )

        _progress("reference/assets", status="started")
        dict_ref, schema_ref = self._copy_reference_assets(
            package_path,
            record.artifact.dictionary_info,
            write_timings=write_timings,
        )
        _progress("reference/assets", status="completed")
        dictionary_status = self._dictionary_status(record.artifact, dict_ref)
        segment_chain = _segment_chain_payload(export_bundle.segment_metas)
        export_time = _iso_now()
        snapshot_id = f"snapshot:{dataset_id}:{job_id}:{int(datetime.now(timezone.utc).timestamp())}"
        run_id = str(job.get("run_id") or snapshot_context.get("run_id") or record.artifact.header.run_id or export_bundle.dataset_id)
        run_batch_id = str(job.get("run_batch_id") or snapshot_context.get("run_batch_id") or run_id)
        version_id = str(job.get("version_id") or snapshot_context.get("version_id") or record.artifact.header.producer_ver)
        experiment_params = dict(
            job.get("experiment_params")
            or snapshot_context.get("experiment_params")
            or {
                "export_mode": mode,
                "filter": export_filter,
                "time_window": list(export_window),
            }
        )
        context_padding_rule = None
        if mode == "clipped":
            context_padding_rule = {
                "segment_policy": "include_intersecting",
                "event_policy": "window_intersection",
                "event_closure_policy": "window_intersection_plus_required_refs",
                "required_event_ref_count": int(event_summary.required_event_ref_count),
                "closure_forced_event_count": int(event_summary.closure_forced_event_count),
                "untrusted_policy": "include_intersecting",
            }
        meta = {
            "dataset_id": dataset_id,
            "time_unit": _time_unit_label(record.artifact.header),
            "clock_source": _clock_source_label(record.artifact.header),
            "align_policy": "sync_anchor_or_stable_sort",
            "dict_ver": record.artifact.header.dict_ver,
            "parser_ver": PARSER_VERSION,
            "dict_ref": dict_ref,
            "dictionary_status": dictionary_status,
            "schema_ref": schema_ref,
            "export_scope": {
                "mode": mode,
                "dataset_id": dataset_id,
                "window": list(export_window),
                "filter": export_filter,
                "write_mode": event_summary.write_mode,
            },
            "export_time": export_time,
            "snapshot_id": snapshot_id,
            "export_mode": mode,
            "time_window": list(export_window),
            "filter": export_filter,
            "selection": snapshot_context.get("selection", {}),
            "zoom_level": snapshot_context.get("zoom_level", 1.0),
            "run_id": run_id,
            "run_batch_id": run_batch_id,
            "version_id": version_id,
            "experiment_params": experiment_params,
            "analysis_context": snapshot_context,
            "compare_scope": compare_scope,
            "evidence_anchor": snapshot_context.get("evidence_anchor"),
            "compare_role": snapshot_context.get("dataset_role") or "single",
            "export_source": {
                "dataset_id": dataset_id,
                "source": record.artifact.source,
                "job_id": job_id,
                "job_kind": job_id.rsplit("-", 1)[0],
                "run_id": run_id,
                "write_mode": event_summary.write_mode,
            },
            "context_padding_rule": context_padding_rule,
            "capability_flags": export_bundle.capability_flags,
            "untrusted_windows": window_rows,
            "segment_chain": segment_chain,
        }

        specs = load_specs()
        meta_missing = _schema_missing_fields(specs["meta"], meta, "meta")
        if meta_missing:
            return err_result("INVALID_ARG", f"meta schema missing fields: {', '.join(meta_missing)}")
        _measure_export_write(
            "meta.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            meta_path,
            meta,
        )

        entries = [
            self._manifest_entry(
                package_path,
                "meta.json",
                category="meta",
                count=1,
                format_name="json",
                schema_ref="meta_schema",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "event/events.trace",
                category="event",
                count=event_summary.count,
                format_name="trace",
                producer=record.artifact.header.producer_ver,
                ref_keys=_ref_summary_from_bounds(
                    "event/ref_index.json",
                    count=event_summary.count,
                    first=event_summary.first_ref_key,
                    last=event_summary.last_ref_key,
                ),
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "event/ref_index.json",
                category="event_index",
                count=event_summary.count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/rebuild_bundle.json",
                category="rebuild",
                count=event_summary.count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/exec_slices.csv",
                category="rebuild",
                count=exec_count,
                format_name="csv",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/exec_slices.json",
                category="rebuild",
                count=exec_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/task_states.csv",
                category="rebuild",
                count=task_count,
                format_name="csv",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/task_states.json",
                category="rebuild",
                count=task_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/irq_spans.csv",
                category="rebuild",
                count=irq_count,
                format_name="csv",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/irq_spans.json",
                category="rebuild",
                count=irq_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/resource_graph.json",
                category="rebuild",
                count=len(export_bundle.resource_graph.nodes),
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "rebuild/untrusted_windows.json",
                category="rebuild",
                count=window_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "result/metrics.csv",
                category="result",
                count=metric_count,
                format_name="csv",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "result/metrics.json",
                category="result",
                count=metric_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "result/hotspots.csv",
                category="result",
                count=len(hotspot_rows),
                format_name="csv",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "result/alerts.csv",
                category="result",
                count=alert_count,
                format_name="csv",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "result/alerts.json",
                category="result",
                count=alert_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "result/diagnoses.json",
                category="result",
                count=diagnosis_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "context/analysis_context.json",
                category="context",
                count=1,
                format_name="json",
                schema_ref="analysis_context_schema",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "context/compare_scope.json",
                category="context",
                count=1 if compare_scope else 0,
                format_name="json",
                schema_ref="compare_scope_schema",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "context/anchors.json",
                category="context",
                count=anchor_count,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "context/bookmarks.json",
                category="context",
                count=0,
                format_name="json",
                write_timings=write_timings,
            ),
            self._manifest_entry(
                package_path,
                "reference/dictionary.json",
                category="reference",
                count=1,
                format_name="json",
                write_timings=write_timings,
            ),
        ]
        for name in REFERENCE_SCHEMA_NAMES:
            entries.append(
                self._manifest_entry(
                    package_path,
                    f"reference/schema/{name}",
                    category="reference",
                    count=1,
                    format_name="json",
                    write_timings=write_timings,
                )
            )
        manifest = {
            "package_version": PACKAGE_VERSION,
            "created_at": export_time,
            "snapshot_id": snapshot_id,
            "dict_ref": dict_ref,
            "schema_ref": schema_ref,
            "segment_chain": segment_chain,
            "entries": entries,
        }
        manifest_missing = _schema_missing_fields(specs["manifest"], manifest, "manifest")
        if manifest_missing:
            return err_result("INVALID_ARG", f"manifest schema missing fields: {', '.join(manifest_missing)}")
        _measure_export_write(
            "manifest.json",
            "json_dump_seconds",
            _json_dump_without_reserialize,
            manifest_path,
            manifest,
        )
        return ok_result(
            {
                "package_path": str(package_path),
                "entry_count": len(entries),
                "snapshot_id": snapshot_id,
                "event_count": event_summary.count,
                "write_mode": event_summary.write_mode,
                "write_timings": {
                    key: round(float(value), 6)
                    for key, value in write_timings.items()
                },
                "meta": copy.deepcopy(meta),
                "manifest": copy.deepcopy(manifest),
            }
        )

    def export_WriteEvidencePackage(self, job_id: str, output_path: str) -> Result[dict[str, Any]]:
        job = self.jobs.get(job_id)
        dataset_id = job.get("dataset_id")
        if dataset_id is None:
            dataset_id = next(iter(self.repository.list_ids()), None)
        if dataset_id is None:
            self.jobs.update_patent_contract(job_id, error_code="NOT_READY", failed_at_state="JOB-queued")
            return err_result("NOT_READY", "no dataset available for evidence export")
        record = self.repository.get(dataset_id)
        snapshot_context = dict(job.get("context") or self.context_store.get().persisted_dict())
        result = write_evidence_package(
            record,
            snapshot_context,
            job,
            job_id=job_id,
            output_path=output_path,
            emit_progress=lambda payload: (
                self._emit_progress(**payload),
                self._update_evidence_job_contract(job_id, payload),
            )[-1],
        )
        if result.ok:
            proof_digest_path = Path(str(result.data["package_path"])) / "control" / "proof_digest.json"
            proof_digest = json_load(proof_digest_path) if proof_digest_path.exists() else {}
            halt_reason = str(proof_digest.get("frontier_halt_reason") or "")
            self.jobs.update_patent_contract(
                job_id,
                patent_job_state="JOB-package_written",
                finalized_mode=str(result.data.get("closure_mode") or "n/a"),
                halt_reason=halt_reason,
                budget_rejected=halt_reason in {"DEPTH_LIMIT", "EVENT_LIMIT", "BYTE_LIMIT", "RHO_LIMIT"},
                projected_next_events=proof_digest.get("projected_next_events"),
                projected_next_bytes=proof_digest.get("projected_next_bytes"),
            )
            self.jobs.update_patent_contract(job_id, patent_job_state="JOB-completed")
        else:
            self.jobs.update_patent_contract(
                job_id,
                error_code=str(result.code or "EXPORT_FAILED"),
                failed_at_state=str(job.get("patent_job_state") or "JOB-queued"),
            )
        return result


class ReproService:
    def __init__(
        self,
        repository: DatasetRepository,
        context_store: ContextStore,
        jobs: BackgroundJobManager | None = None,
    ) -> None:
        self.repository = repository
        self.context_store = context_store
        self.jobs = jobs
        self._opened_package: Path | None = None
        self._meta: dict[str, Any] | None = None
        self._manifest: dict[str, Any] | None = None
        self._evidence_control: dict[str, Any] | None = None
        self._result_validity_index: dict[str, Any] | None = None

    def _validate_refs(self, package_path: Path, meta: dict[str, Any], manifest: dict[str, Any]) -> Result[None]:
        return _validate_package_refs(package_path, meta, manifest)

    def repro_OpenPackage(self, path_or_stream: str) -> Result[dict[str, Any]]:
        loaded_package = _load_validated_package(path_or_stream)
        if not loaded_package.ok:
            self._evidence_control = None
            self._result_validity_index = None
            return loaded_package
        self._opened_package = Path(loaded_package.data["package_path"])
        self._meta = loaded_package.data["meta"]
        self._manifest = loaded_package.data["manifest"]
        self._evidence_control = None
        self._result_validity_index = None
        evidence_control = load_evidence_control(self._opened_package, self._meta, self._manifest)
        if not evidence_control.ok:
            self._evidence_control = None
            self._result_validity_index = None
            return evidence_control
        self._evidence_control = evidence_control.data or None
        if self._evidence_control:
            self._result_validity_index = rpr_BuildResultValidityIndex(self._evidence_control.get("result_validity"))
        payload = {"package_path": str(self._opened_package), "meta": self._meta, "manifest": self._manifest}
        if self._evidence_control:
            payload.update(self._evidence_control)
        return ok_result(payload)

    def _ensure_opened_package(self, path_or_stream: str | None = None) -> Result[None]:
        if path_or_stream is None:
            if self._opened_package is None:
                return err_result("NOT_READY", "package not opened")
            return ok_result(None)
        opened = self.repro_OpenPackage(path_or_stream)
        if not opened.ok:
            return err_result(opened.code, opened.message, warnings=opened.warnings, untrusted_windows=opened.untrusted_windows)
        return ok_result(None)

    def repro_QueryProof(self, path_or_stream: str | None = None) -> Result[dict[str, Any]]:
        opened = self._ensure_opened_package(path_or_stream)
        if not opened.ok:
            return opened
        if self._evidence_control is None:
            return err_result("NOT_READY", "evidence control not available")
        payload = {
            "package_path": str(self._opened_package) if self._opened_package is not None else None,
            "proof_digest": self._evidence_control.get("proof_digest"),
            "proof_verification": self._evidence_control.get("proof_verification"),
            "consumer_mode": self._evidence_control.get("consumer_mode"),
            "consumer_mode_explain": self._evidence_control.get("consumer_mode_explain"),
            "frontier_snapshot": self._evidence_control.get("frontier_snapshot"),
            "frontier_refs": self._evidence_control.get("frontier_refs"),
            "blocker_artifact": self._evidence_control.get("blocker_artifact"),
            "sidecar_manifest": self._evidence_control.get("sidecar_manifest"),
            "result_validity": self._evidence_control.get("result_validity"),
        }
        return ok_result(copy.deepcopy(payload))

    def _submit_evidence_query_job(
        self,
        kind: str,
        payload: dict[str, Any],
        query_callable: Callable[[], Result[Any]],
    ) -> Result[dict[str, Any]]:
        if self.jobs is None:
            return err_result("NOT_READY", "background jobs not configured for evidence query")
        job_id = self.jobs.create(
            kind,
            {
                **payload,
                "lane": "evidence_query",
                "stage": "queued",
                "job_kind": kind,
            },
        )

        def run_query() -> Result[Any]:
            assert self.jobs is not None
            self.jobs.update_payload(
                job_id,
                {
                    "lane": "evidence_query",
                    "stage": "running",
                    "job_kind": kind,
                },
            )
            self.jobs.update_patent_contract(
                job_id,
                patent_job_state="JOB-closing",
                last_substage="query/execute",
                last_substage_status="running",
            )
            result = query_callable()
            if result.ok:
                self.jobs.update_payload(
                    job_id,
                    {
                        "lane": "evidence_query",
                        "stage": "query_ready",
                        "job_kind": kind,
                    },
                )
                self.jobs.update_patent_contract(
                    job_id,
                    patent_job_state="JOB-finalized",
                    finalized_mode="n/a",
                    last_substage="query/execute",
                    last_substage_status="completed",
                )
            else:
                self.jobs.update_patent_contract(
                    job_id,
                    error_code=str(result.code or "QUERY_FAILED"),
                    failed_at_state="JOB-closing",
                    last_substage="query/execute",
                    last_substage_status="failed",
                )
            return result

        submitted = self.jobs.submit(job_id, run_query)
        if not submitted.ok:
            return submitted
        return ok_result(
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "job_kind": kind,
                "lane": "evidence_query",
            }
        )

    def repro_QueryProofAsync(self, path_or_stream: str | None = None) -> Result[dict[str, Any]]:
        return self._submit_evidence_query_job(
            "evidence_query_proof",
            {"package_path": path_or_stream},
            lambda: self.repro_QueryProof(path_or_stream),
        )

    def _query_after_open(
        self,
        path_or_stream: str | None,
        query_callable: Callable[[], Result[dict[str, Any]]],
    ) -> Result[dict[str, Any]]:
        opened = self._ensure_opened_package(path_or_stream)
        if not opened.ok:
            return opened
        return query_callable()

    def repro_QueryValidityByAlertIdAsync(
        self,
        alert_id: str,
        path_or_stream: str | None = None,
    ) -> Result[dict[str, Any]]:
        return self._submit_evidence_query_job(
            "evidence_query_validity",
            {"package_path": path_or_stream, "query": {"type": "alert_id", "value": alert_id}},
            lambda: self._query_after_open(path_or_stream, lambda: self.repro_QueryValidityByAlertId(alert_id)),
        )

    def repro_QueryValidityByDiagIdAsync(
        self,
        diag_id: str,
        path_or_stream: str | None = None,
    ) -> Result[dict[str, Any]]:
        return self._submit_evidence_query_job(
            "evidence_query_validity",
            {"package_path": path_or_stream, "query": {"type": "diag_id", "value": diag_id}},
            lambda: self._query_after_open(path_or_stream, lambda: self.repro_QueryValidityByDiagId(diag_id)),
        )

    def repro_QueryValidityByObjectAsync(
        self,
        object_kind: str,
        object_id: str,
        path_or_stream: str | None = None,
    ) -> Result[dict[str, Any]]:
        return self._submit_evidence_query_job(
            "evidence_query_validity",
            {
                "package_path": path_or_stream,
                "query": {
                    "type": "object",
                    "value": {"object_kind": object_kind, "object_id": object_id},
                },
            },
            lambda: self._query_after_open(
                path_or_stream,
                lambda: self.repro_QueryValidityByObject(object_kind, object_id),
            ),
        )

    def _result_validity_index_or_error(self) -> Result[dict[str, Any]]:
        if self._opened_package is None:
            return err_result("NOT_READY", "package not opened")
        if self._evidence_control is None:
            return err_result("NOT_READY", "evidence control not available")
        if self._result_validity_index is None:
            self._result_validity_index = rpr_BuildResultValidityIndex(self._evidence_control.get("result_validity"))
        return ok_result(self._result_validity_index)

    def _query_result_validity(
        self,
        *,
        query: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> Result[dict[str, Any]]:
        variants = sorted(
            {
                (str(item.get("validity_scope") or ""), str(item.get("derivation_mode") or ""))
                for item in rows
            }
        )
        conflict_present = len(rows) > 1 and len(variants) > 1
        conflict = {
            "present": conflict_present,
            "reason": "inconsistent_validity_semantics" if conflict_present else "",
            "variants": [
                {"validity_scope": scope, "derivation_mode": derivation}
                for scope, derivation in variants
            ] if conflict_present else [],
            "row_indices": sorted({int(item.get("row_index", -1)) for item in rows}) if conflict_present else [],
        }
        status = "MISS"
        if rows:
            status = "CONFLICT" if conflict_present else "HIT"
        payload = {
            "query": copy.deepcopy(query),
            "status": status,
            "rows": copy.deepcopy(rows),
            "conflict": conflict,
        }
        warnings: list[str] = []
        if conflict_present:
            warnings.append(
                f"result_validity conflict for query {query.get('type')}: "
                f"{query.get('value')}"
            )
        return ok_result(payload, warnings=warnings)

    def repro_QueryValidityByAlertId(self, alert_id: str) -> Result[dict[str, Any]]:
        normalized_alert_id = str(alert_id or "").strip()
        if not normalized_alert_id:
            return err_result("INVALID_ARG", "alert_id must be non-empty")
        index_result = self._result_validity_index_or_error()
        if not index_result.ok:
            return index_result
        rows = list(dict(index_result.data.get("by_alert_id") or {}).get(normalized_alert_id) or [])
        return self._query_result_validity(
            query={"type": "alert_id", "value": normalized_alert_id},
            rows=rows,
        )

    def repro_QueryValidityByDiagId(self, diag_id: str) -> Result[dict[str, Any]]:
        normalized_diag_id = str(diag_id or "").strip()
        if not normalized_diag_id:
            return err_result("INVALID_ARG", "diag_id must be non-empty")
        index_result = self._result_validity_index_or_error()
        if not index_result.ok:
            return index_result
        rows = list(dict(index_result.data.get("by_diag_id") or {}).get(normalized_diag_id) or [])
        return self._query_result_validity(
            query={"type": "diag_id", "value": normalized_diag_id},
            rows=rows,
        )

    def repro_QueryValidityByObject(self, object_kind: str, object_id: str) -> Result[dict[str, Any]]:
        normalized_object_kind = str(object_kind or "").strip().lower()
        normalized_object_id = str(object_id or "").strip()
        if normalized_object_kind not in {"alert", "diagnosis"}:
            return err_result("INVALID_ARG", f"unsupported object_kind: {object_kind}")
        if not normalized_object_id:
            return err_result("INVALID_ARG", "object_id must be non-empty")
        index_result = self._result_validity_index_or_error()
        if not index_result.ok:
            return index_result
        rows = list(dict(index_result.data.get("by_object") or {}).get((normalized_object_kind, normalized_object_id)) or [])
        return self._query_result_validity(
            query={
                "type": "object",
                "value": {
                    "object_kind": normalized_object_kind,
                    "object_id": normalized_object_id,
                },
            },
            rows=rows,
        )

    def repro_RestoreContext(self, snapshot_id: str | None = None) -> Result[AnalysisContext]:
        if self._opened_package is None:
            return err_result("NOT_READY", "package not opened")
        if snapshot_id is not None and self._meta is not None and self._meta.get("snapshot_id") != snapshot_id:
            return err_result("INVALID_ARG", f"snapshot mismatch: {snapshot_id}")
        context_path = self._opened_package / "context" / "analysis_context.json"
        compare_path = self._opened_package / "context" / "compare_scope.json"
        current = json_load(context_path)
        specs = load_specs()
        context_missing = _schema_missing_fields(specs["analysis_context"], current, "analysis_context")
        if context_missing:
            return err_result("INVALID_ARG", f"context schema missing fields: {', '.join(context_missing)}")
        context_compare_result = _normalize_compare_scope_payload(
            current.get("compare_scope"),
            label="analysis_context.compare_scope",
        )
        if not context_compare_result.ok:
            return Result(
                code=context_compare_result.code,
                message=context_compare_result.message,
                warnings=context_compare_result.warnings,
                untrusted_windows=context_compare_result.untrusted_windows,
            )
        current_compare_result = _normalize_compare_scope_payload(
            json_load(compare_path) if compare_path.exists() else {},
            label="context.compare_scope",
        )
        if not current_compare_result.ok:
            return Result(
                code=current_compare_result.code,
                message=current_compare_result.message,
                warnings=current_compare_result.warnings,
                untrusted_windows=current_compare_result.untrusted_windows,
            )
        context_compare_scope = context_compare_result.data or {}
        current_compare_scope = current_compare_result.data or {}
        if current_compare_scope != context_compare_scope:
            return err_result("INVALID_ARG", "compare_scope mismatch between analysis_context and context")
        if self._meta is not None:
            meta_compare_result = _normalize_compare_scope_payload(
                self._meta.get("compare_scope"),
                label="meta.compare_scope",
            )
            if not meta_compare_result.ok:
                return Result(
                    code=meta_compare_result.code,
                    message=meta_compare_result.message,
                    warnings=meta_compare_result.warnings,
                    untrusted_windows=meta_compare_result.untrusted_windows,
                )
            meta_compare_scope = meta_compare_result.data or {}
            if current_compare_scope != meta_compare_scope:
                return err_result("INVALID_ARG", "compare_scope mismatch between meta and context")
        restored_compare_scope = current_compare_result.data
        if restored_compare_scope in (None, {}):
            restored_compare_scope = context_compare_result.data
        context = AnalysisContext(
            time_window=tuple(current["time_window"]),
            filter=current["filter"],
            selection=current["selection"],
            zoom_level=current["zoom_level"],
            focused_view=current["focused_view"],
            evidence_anchor=current["evidence_anchor"],
            playback_cursor=current["playback_cursor"],
            compare_scope=restored_compare_scope,
            dataset_role=current["dataset_role"],
        )
        return ok_result(self.context_store.replace(context))

    def repro_LoadAsDataset(self, role: str) -> Result[str]:
        if self._opened_package is None:
            return err_result("NOT_READY", "package not opened")
        loaded_package = _load_validated_package(self._opened_package)
        if not loaded_package.ok:
            return loaded_package
        bundle = loaded_package.data["bundle"]
        dictionary_info = dict(loaded_package.data.get("dictionary_info") or {})
        artifact = DatasetArtifact(
            dataset_id=_role_dataset_id(bundle.dataset_id, role),
            source=str(self._opened_package),
            header=bundle.header
            or GlobalHeader(
                magic="0x0",
                endian=1,
                time_unit=1,
                clock_source=1,
                format_ver=1,
                dict_ver=1,
                producer_ver="repro",
                run_id=bundle.dataset_id,
            ),
            bundle=bundle,
            dictionary_info=dictionary_info,
        )
        session = metric_Init().data
        ingest = metric_Ingest(session, bundle)
        if not ingest.ok:
            return Result(
                code=ingest.code,
                message=ingest.message,
                warnings=ingest.warnings,
                untrusted_windows=ingest.untrusted_windows,
            )
        dataset_id = self.repository.add(DatasetRecord(artifact=artifact, metric_session=session))
        return ok_result(dataset_id)

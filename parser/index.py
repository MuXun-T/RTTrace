from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .align import align_events
from .codec import (
    CHUNK_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_CHUNK_MAGIC,
    TraceDecodeSession,
)
from .models import EventCursor, EventPage, IndexBundle, RebuildBundle, TaskStateSeg, UnifiedEvent, materialize_unified_event
from .result import Result, err_result, ok_result


DEFAULT_TIME_INDEX_BUCKETS = 128
DEFAULT_INDEX_BUILD_MODE = "full"
VALID_INDEX_BUILD_MODES = {"full", "minimal", "deferred"}


@dataclass(frozen=True)
class _TraceChunkSpan:
    path: Path
    header_offset: int
    payload_offset: int
    payload_size: int
    t_begin: float
    t_end: float
    core_id: int
    seq_begin: int
    seq_end: int
    segment_seq: int | None
    has_segment_meta: bool


TraceChunkCatalog = tuple[_TraceChunkSpan, ...]


def _bucket_window(origin: float, bucket_size: float, bucket_id: int) -> tuple[float, float]:
    start = origin + (bucket_id * bucket_size)
    return start, start + bucket_size


def _bucket_id(timestamp: float, origin: float, bucket_size: float) -> int:
    if bucket_size <= 0.0:
        return 0
    return int(max((float(timestamp) - origin) // bucket_size, 0))


def _ensure_bucket(
    buckets: dict[int, dict[str, Any]],
    bucket_id: int,
    origin: float,
    bucket_size: float,
) -> dict[str, Any]:
    if bucket_id not in buckets:
        t_begin, t_end = _bucket_window(origin, bucket_size, bucket_id)
        buckets[bucket_id] = {
            "bucket_id": bucket_id,
            "t_begin": t_begin,
            "t_end": t_end,
            "event_count": 0.0,
            "slice_count": 0.0,
            "irq_count": 0.0,
            "core_event_counts": {},
            "core_slice_counts": {},
            "core_irq_counts": {},
        }
    return buckets[bucket_id]


def _increment_core(bucket: dict[str, Any], field: str, core_id: int, value: float) -> None:
    counts = dict(bucket.get(field) or {})
    key = str(core_id)
    counts[key] = round(float(counts.get(key, 0.0)) + float(value), 6)
    bucket[field] = counts


def _accumulate_interval(
    buckets: dict[int, dict[str, Any]],
    t_begin: float,
    t_end: float,
    origin: float,
    bucket_size: float,
    total_value: float,
    value_field: str,
    core_field: str,
    core_id: int,
) -> None:
    begin = float(t_begin)
    end = float(t_end)
    if end < begin:
        begin, end = end, begin
    if end <= begin:
        bucket = _ensure_bucket(buckets, _bucket_id(begin, origin, bucket_size), origin, bucket_size)
        bucket[value_field] = round(float(bucket[value_field]) + float(total_value), 6)
        _increment_core(bucket, core_field, core_id, float(total_value))
        return
    start_bucket = _bucket_id(begin, origin, bucket_size)
    end_bucket = _bucket_id(max(end - 1e-9, begin), origin, bucket_size)
    duration = max(end - begin, 1e-9)
    for bucket_id in range(start_bucket, end_bucket + 1):
        bucket = _ensure_bucket(buckets, bucket_id, origin, bucket_size)
        overlap_begin = max(begin, float(bucket["t_begin"]))
        overlap_end = min(end, float(bucket["t_end"]))
        overlap = max(overlap_end - overlap_begin, 0.0)
        if overlap <= 0.0:
            continue
        portion = float(total_value) * (overlap / duration)
        bucket[value_field] = round(float(bucket[value_field]) + portion, 6)
        _increment_core(bucket, core_field, core_id, portion)


def _lower_bound_ts(events: list[UnifiedEvent], timestamp: float) -> int:
    lo = 0
    hi = len(events)
    target = float(timestamp)
    while lo < hi:
        mid = (lo + hi) // 2
        if float(events[mid].timestamp_aligned) < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _upper_bound_ts(events: list[UnifiedEvent], timestamp: float) -> int:
    lo = 0
    hi = len(events)
    target = float(timestamp)
    while lo < hi:
        mid = (lo + hi) // 2
        if float(events[mid].timestamp_aligned) <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _supports_index_filter(filter_spec: dict[str, object]) -> bool:
    unsupported_keys = {"task_id", "task_ids", "event_name", "obj_id", "resource_id", "irq_id"}
    return not any(filter_spec.get(key) is not None for key in unsupported_keys)


def _build_bucket_rows(time_window: tuple[float, float], bucket_count: int) -> tuple[list[dict[str, Any]], float]:
    t_begin, t_end = float(time_window[0]), float(time_window[1])
    if t_end <= t_begin:
        return [], 0.0
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
                "slice_count": 0.0,
                "irq_count": 0.0,
            }
        )
        cursor = bucket_end
    return rows, bucket_size


def _accumulate_rows(
    rows: list[dict[str, Any]],
    t_begin: float,
    t_end: float,
    total_value: float,
    field: str,
) -> None:
    if not rows:
        return
    begin = float(t_begin)
    end = float(t_end)
    if end < begin:
        begin, end = end, begin
    if end <= begin:
        for row in rows:
            if begin < float(row["t_end"]):
                row[field] = round(float(row[field]) + float(total_value), 6)
                return
        rows[-1][field] = round(float(rows[-1][field]) + float(total_value), 6)
        return
    duration = max(end - begin, 1e-9)
    for row in rows:
        overlap_begin = max(begin, float(row["t_begin"]))
        overlap_end = min(end, float(row["t_end"]))
        overlap = max(overlap_end - overlap_begin, 0.0)
        if overlap <= 0.0:
            continue
        portion = float(total_value) * (overlap / duration)
        row[field] = round(float(row[field]) + portion, 6)


def _bucket_value(bucket: dict[str, Any], total_field: str, core_field: str, core_id: int | None) -> float:
    if core_id is None:
        return float(bucket.get(total_field, 0.0))
    return float((bucket.get(core_field) or {}).get(str(core_id), 0.0))


def _normalize_index_build_mode(mode: str | None) -> Result[str]:
    normalized = str(mode or DEFAULT_INDEX_BUILD_MODE).strip().lower()
    if normalized not in VALID_INDEX_BUILD_MODES:
        return err_result(
            "INVALID_ARG",
            f"index_build_mode must be one of {', '.join(sorted(VALID_INDEX_BUILD_MODES))}",
        )
    return ok_result(normalized)


def _bundle_time_bounds(results: RebuildBundle) -> tuple[float, float]:
    if results.event_stream:
        return (
            float(results.event_stream[0].timestamp_aligned),
            float(results.event_stream[-1].timestamp_aligned),
        )
    start_candidates: list[float] = []
    end_candidates: list[float] = []
    for item in results.exec_slices:
        start_candidates.append(float(item.t_begin))
        end_candidates.append(float(item.t_end))
    for item in results.irq_spans:
        start_candidates.append(float(item.t_begin))
        end_candidates.append(float(item.t_end))
    for item in results.task_states:
        start_candidates.append(float(item.t_begin))
        end_candidates.append(float(item.t_end))
    if not start_candidates or not end_candidates:
        return 0.0, 0.0
    return min(start_candidates), max(end_candidates)


def default_on_demand_index_build_mode(results: RebuildBundle) -> str:
    return "full" if results.event_stream else "minimal"


def idx_Build(results: RebuildBundle, *, mode: str = DEFAULT_INDEX_BUILD_MODE) -> Result[IndexBundle]:
    normalized_mode_result = _normalize_index_build_mode(mode)
    if not normalized_mode_result.ok:
        return normalized_mode_result
    normalized_mode = normalized_mode_result.data
    time_index: list[dict[str, Any]] = []
    task_ids: set[int] = set()
    core_ids: set[int] = set()
    event_types: set[str] = set()
    build_uid_indexes = normalized_mode == "full"

    origin, end = _bundle_time_bounds(results)
    if origin != end:
        span = max(end - origin, 1.0)
        bucket_size = max(span / DEFAULT_TIME_INDEX_BUCKETS, 1.0)
    else:
        bucket_size = 1.0
    if normalized_mode == "deferred":
        bundle = IndexBundle(
            time_index=[],
            task_index={},
            core_index={},
            event_type_index={},
            summary={
                "event_count": len(results.event_stream),
                "task_count": len({int(item.task_id) for item in results.task_states if int(item.task_id) > 0}),
                "core_count": len({int(item.core_id) for item in results.exec_slices + results.irq_spans}),
                "event_type_count": 0,
                "time_origin": origin,
                "time_end": end,
                "bucket_size": bucket_size,
                "bucket_count": 0,
                "index_build_mode": normalized_mode,
                "uid_indexes_built": False,
            },
        )
        results.index_bundle = bundle
        return ok_result(bundle)

    task_index: dict[int, list[str]] = defaultdict(list) if build_uid_indexes else {}
    core_index: dict[int, list[str]] = defaultdict(list) if build_uid_indexes else {}
    event_type_index: dict[str, list[str]] = defaultdict(list) if build_uid_indexes else {}
    buckets: dict[int, dict[str, Any]] = {}
    for event in results.event_stream:
        bucket = _ensure_bucket(
            buckets,
            _bucket_id(float(event.timestamp_aligned), origin, bucket_size),
            origin,
            bucket_size,
        )
        bucket["event_count"] = round(float(bucket["event_count"]) + 1.0, 6)
        _increment_core(bucket, "core_event_counts", int(event.core_id), 1.0)
        core_ids.add(int(event.core_id))
        event_types.add(str(event.event_name))
        if event.task_id is not None:
            task_ids.add(int(event.task_id))
            if build_uid_indexes:
                task_index[event.task_id].append(event.event_uid)
        if build_uid_indexes:
            core_index[event.core_id].append(event.event_uid)
            event_type_index[event.event_name].append(event.event_uid)
    for item in results.exec_slices:
        task_ids.add(int(item.task_id))
        core_ids.add(int(item.core_id))
        _accumulate_interval(
            buckets,
            float(item.t_begin),
            float(item.t_end),
            origin,
            bucket_size,
            1.0,
            "slice_count",
            "core_slice_counts",
            int(item.core_id),
        )
    for item in results.irq_spans:
        core_ids.add(int(item.core_id))
        _accumulate_interval(
            buckets,
            float(item.t_begin),
            float(item.t_end),
            origin,
            bucket_size,
            1.0,
            "irq_count",
            "core_irq_counts",
            int(item.core_id),
        )
    for item in results.task_states:
        task_ids.add(int(item.task_id))
    time_index = [buckets[key] for key in sorted(buckets)]
    bundle = IndexBundle(
        time_index=time_index,
        task_index=dict(task_index),
        core_index=dict(core_index),
        event_type_index=dict(event_type_index),
        summary={
            "event_count": len(results.event_stream),
            "task_count": len(task_ids),
            "core_count": len(core_ids),
            "event_type_count": len(event_types),
            "time_origin": origin,
            "time_end": end,
            "bucket_size": bucket_size,
            "bucket_count": len(time_index),
            "index_build_mode": normalized_mode,
            "uid_indexes_built": build_uid_indexes,
        },
    )
    results.index_bundle = bundle
    return ok_result(bundle)


def _apply_filter(events: Iterable[UnifiedEvent], filter_spec: dict[str, object]) -> list[UnifiedEvent]:
    event_list = events if isinstance(events, list) else list(events)
    t_begin = filter_spec.get("t_begin")
    t_end = filter_spec.get("t_end")
    if t_begin is not None or t_end is not None:
        begin = float(t_begin) if t_begin is not None else float(event_list[0].timestamp_aligned) if event_list else 0.0
        end = float(t_end) if t_end is not None else float(event_list[-1].timestamp_aligned) if event_list else 0.0
        filtered = event_list[_lower_bound_ts(event_list, begin) : _upper_bound_ts(event_list, end)]
    else:
        filtered = list(event_list)
    task_id = filter_spec.get("task_id")
    core_id = filter_spec.get("core_id")
    event_name = filter_spec.get("event_name")
    obj_id = filter_spec.get("obj_id") or filter_spec.get("resource_id")
    irq_id = filter_spec.get("irq_id")
    if task_id is not None:
        filtered = [event for event in filtered if event.task_id == task_id]
    if core_id is not None:
        filtered = [event for event in filtered if event.core_id == core_id]
    if event_name is not None:
        filtered = [event for event in filtered if event.event_name == event_name]
    if obj_id is not None:
        filtered = [event for event in filtered if event.obj_id == obj_id]
    if irq_id is not None:
        filtered = [event for event in filtered if event.irq_id == irq_id]
    return filtered


def _event_page_from_events(
    events: list[UnifiedEvent],
    *,
    cursor: EventCursor | None,
    limit: int,
    source: str,
    direction: str = "forward",
) -> EventPage:
    normalized_direction = "backward" if direction == "backward" else "forward"
    page_limit = max(int(limit), 0)
    filtered = list(events)
    if cursor is not None:
        if normalized_direction == "backward":
            filtered = [event for event in filtered if event.sort_key < cursor.sort_key]
        else:
            filtered = [event for event in filtered if event.sort_key > cursor.sort_key]
    filtered.sort(key=lambda event: event.sort_key)
    if page_limit == 0:
        page: list[UnifiedEvent] = []
    elif normalized_direction == "backward":
        page = filtered[-page_limit:]
    else:
        page = filtered[:page_limit]
    next_cursor = None
    has_more = len(filtered) > page_limit
    prev_cursor = cursor
    if page:
        head = page[0]
        tail = page[-1]
        if normalized_direction == "backward":
            if has_more:
                next_cursor = EventCursor(
                    ts=head.timestamp_aligned,
                    core_id=head.core_id,
                    seq=head.seq,
                    sort_key=head.sort_key,
                )
            prev_cursor = EventCursor(
                ts=tail.timestamp_aligned,
                core_id=tail.core_id,
                seq=tail.seq,
                sort_key=tail.sort_key,
            )
        else:
            if has_more:
                next_cursor = EventCursor(
                    ts=tail.timestamp_aligned,
                    core_id=tail.core_id,
                    seq=tail.seq,
                    sort_key=tail.sort_key,
                )
            prev_cursor = EventCursor(
                ts=head.timestamp_aligned,
                core_id=head.core_id,
                seq=head.seq,
                sort_key=head.sort_key,
            )
    return EventPage(
        items=page,
        order_by="sort_key asc",
        cursor_in=cursor,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        has_more=has_more,
        total_hint=len(filtered),
        trusted=all(not event.trust_tags for event in page),
        summary={"source": source},
    )


def _merge_time_window(
    filter_spec: dict[str, object] | None,
    time_window: tuple[float, float] | None,
) -> dict[str, object]:
    active_filter = dict(filter_spec or {})
    if time_window is None:
        return active_filter
    begin = float(time_window[0])
    end = float(time_window[1])
    existing_begin = active_filter.get("t_begin")
    existing_end = active_filter.get("t_end")
    if existing_begin is not None:
        begin = max(begin, float(existing_begin))
    if existing_end is not None:
        end = min(end, float(existing_end))
    active_filter["t_begin"] = begin
    active_filter["t_end"] = end
    return active_filter


def _coerce_trace_paths(source_handle: str | Path) -> Result[list[Path]]:
    source_path = Path(source_handle)
    if not source_path.exists():
        return err_result("INVALID_ARG", f"trace source not found: {source_path}")
    if source_path.is_dir():
        segments = sorted(item for item in source_path.iterdir() if item.is_file() and item.suffix == ".trace")
        if segments:
            return ok_result(segments)
        return err_result("INVALID_ARG", f"trace directory has no .trace segments: {source_path}")
    if not source_path.is_file():
        return err_result("INVALID_ARG", f"trace source is not a file: {source_path}")
    return ok_result([source_path])


def _prescan_trace_chunk_catalog(trace_paths: list[Path]) -> Result[list[_TraceChunkSpan]]:
    catalog: list[_TraceChunkSpan] = []
    for path in trace_paths:
        try:
            with path.open("rb") as handle:
                header_bytes = handle.read(GLOBAL_HEADER_STRUCT.size)
                if len(header_bytes) < GLOBAL_HEADER_STRUCT.size:
                    return err_result("INVALID_ARG", f"incomplete trace header: {path}")
                header_tuple = GLOBAL_HEADER_STRUCT.unpack(header_bytes)
                format_ver = int(header_tuple[3])
                segment_seq: int | None = None
                has_segment_meta = format_ver >= 2
                if has_segment_meta:
                    meta_bytes = handle.read(SEGMENT_META_STRUCT.size)
                    if len(meta_bytes) < SEGMENT_META_STRUCT.size:
                        return err_result("INVALID_ARG", f"incomplete segment meta: {path}")
                    meta_tuple = SEGMENT_META_STRUCT.unpack(meta_bytes)
                    segment_seq = int(meta_tuple[0])
                while True:
                    header_offset = handle.tell()
                    chunk_header_bytes = handle.read(CHUNK_HEADER_STRUCT.size)
                    if not chunk_header_bytes:
                        break
                    if len(chunk_header_bytes) < CHUNK_HEADER_STRUCT.size:
                        return err_result("INVALID_ARG", f"incomplete chunk header: {path}")
                    chunk_tuple = CHUNK_HEADER_STRUCT.unpack(chunk_header_bytes)
                    if chunk_tuple[0] != TRACE_CHUNK_MAGIC:
                        return err_result("INVALID_ARG", f"invalid chunk magic in {path}")
                    payload_size = int(chunk_tuple[4])
                    payload_offset = handle.tell()
                    catalog.append(
                        _TraceChunkSpan(
                            path=path,
                            header_offset=header_offset,
                            payload_offset=payload_offset,
                            payload_size=payload_size,
                            t_begin=float(chunk_tuple[5]),
                            t_end=float(chunk_tuple[6]),
                            core_id=int(chunk_tuple[2]),
                            seq_begin=int(chunk_tuple[7]),
                            seq_end=int(chunk_tuple[8]),
                            segment_seq=segment_seq,
                            has_segment_meta=has_segment_meta,
                        )
                    )
                    handle.seek(payload_size, 1)
        except OSError as exc:
            return err_result("TRACE_IO_GUARD", f"trace chunk catalog build failed for {path}: {exc}")
    return ok_result(catalog)


def build_trace_chunk_catalog(source_handle: str | Path) -> Result[TraceChunkCatalog]:
    trace_paths = _coerce_trace_paths(source_handle)
    if not trace_paths.ok:
        return Result(
            code=trace_paths.code,
            message=trace_paths.message,
            warnings=trace_paths.warnings,
            untrusted_windows=trace_paths.untrusted_windows,
        )
    catalog_result = _prescan_trace_chunk_catalog(trace_paths.data)
    if not catalog_result.ok:
        return Result(
            code=catalog_result.code,
            message=catalog_result.message,
            warnings=catalog_result.warnings,
            untrusted_windows=catalog_result.untrusted_windows,
        )
    return ok_result(
        tuple(catalog_result.data),
        warnings=catalog_result.warnings,
        untrusted_windows=catalog_result.untrusted_windows,
    )


def _event_matches_any_span(event: UnifiedEvent, spans: list[dict[str, Any]]) -> bool:
    event_ref = str(event.ref_key)
    event_ts = float(event.timestamp_aligned)
    event_core = int(event.core_id)
    event_seq = int(event.seq)
    for span in spans:
        if event_ref not in set(str(item) for item in list(span.get("target_refs") or [])):
            continue
        if event_ts < float(span.get("t_begin", event_ts)) or event_ts > float(span.get("t_end", event_ts)):
            continue
        core_id = span.get("core_id")
        if core_id is not None and event_core != int(core_id):
            continue
        seq_begin = span.get("seq_begin")
        if seq_begin is not None and event_seq < int(seq_begin):
            continue
        seq_end = span.get("seq_end")
        if seq_end is not None and event_seq > int(seq_end):
            continue
        return True
    return False


def read_trace_window_spans(
    source_handle: str | Path,
    spans: list[dict[str, Any]],
    *,
    prescanned_catalog: TraceChunkCatalog | None = None,
) -> Result[dict[str, Any]]:
    trace_paths = _coerce_trace_paths(source_handle)
    if not trace_paths.ok:
        return trace_paths
    if not spans:
        return ok_result(
            {
                "matched_events": [],
                "bytes_read": 0,
                "scan_count": 0,
                "seek_count": 0,
                "span_total": 0,
                "corrupt_segments": [],
                "io_guard_triggered": False,
                "telemetry": {
                    "catalog_chunk_count": 0,
                    "selected_chunk_count": 0,
                    "planned_span_count": 0,
                    "selected_span_count": 0,
                    "target_ref_count": 0,
                    "matched_ref_count": 0,
                    "missed_ref_count": 0,
                    "window_hit_rate": 1.0,
                },
            }
        )

    if prescanned_catalog is None:
        catalog_result = _prescan_trace_chunk_catalog(trace_paths.data)
        if not catalog_result.ok:
            return catalog_result
        catalog = tuple(catalog_result.data)
    else:
        catalog = tuple(prescanned_catalog)
    target_refs = {
        str(item).strip()
        for span in spans
        for item in list(span.get("target_refs") or [])
        if str(item).strip()
    }
    planned_span_count = int(len(spans))

    selected_by_chunk: dict[tuple[str, int], list[dict[str, Any]]] = {}
    selected_span_ids: set[str] = set()
    for chunk in catalog:
        for index, span in enumerate(spans, start=1):
            span_begin = float(span.get("t_begin", 0.0))
            span_end = float(span.get("t_end", span_begin))
            if chunk.t_end < span_begin or chunk.t_begin > span_end:
                continue
            span_core = span.get("core_id")
            if span_core is not None and int(span_core) != int(chunk.core_id):
                continue
            span_segment_seq = span.get("segment_seq")
            if span_segment_seq is not None and chunk.segment_seq is not None and int(span_segment_seq) != int(chunk.segment_seq):
                continue
            span_seq_begin = span.get("seq_begin")
            if span_seq_begin is not None and int(chunk.seq_end) < int(span_seq_begin):
                continue
            span_seq_end = span.get("seq_end")
            if span_seq_end is not None and int(chunk.seq_begin) > int(span_seq_end):
                continue
            key = (str(chunk.path), int(chunk.header_offset))
            selected_by_chunk.setdefault(key, []).append(span)
            selected_span_ids.add(str(span.get("span_id") or f"span:{index}"))

    if not selected_by_chunk:
        span_total = sum(max(0, int(span.get("t_end", 0)) - int(span.get("t_begin", 0))) for span in spans)
        target_ref_count = int(len(target_refs))
        missed_ref_count = int(target_ref_count)
        window_hit_rate = 1.0 if target_ref_count == 0 else 0.0
        return ok_result(
            {
                "matched_events": [],
                "bytes_read": 0,
                "scan_count": 0,
                "seek_count": 0,
                "span_total": int(span_total),
                "corrupt_segments": [],
                "io_guard_triggered": False,
                "telemetry": {
                    "catalog_chunk_count": len(catalog),
                    "selected_chunk_count": 0,
                    "planned_span_count": int(planned_span_count),
                    "selected_span_count": 0,
                    "target_ref_count": int(target_ref_count),
                    "matched_ref_count": 0,
                    "missed_ref_count": int(missed_ref_count),
                    "window_hit_rate": float(window_hit_rate),
                },
            }
        )

    dataset_id = Path(source_handle).stem or "trace"
    matched_by_ref: dict[str, UnifiedEvent] = {}
    bytes_read = 0
    scan_count = 0
    seek_count = 0
    span_total = sum(max(0, int(span.get("t_end", 0)) - int(span.get("t_begin", 0))) for span in spans)

    for chunk in catalog:
        key = (str(chunk.path), int(chunk.header_offset))
        relevant_spans = selected_by_chunk.get(key)
        if not relevant_spans:
            continue
        try:
            with chunk.path.open("rb") as handle:
                session = TraceDecodeSession(dataset_id=dataset_id)
                header_bytes = handle.read(GLOBAL_HEADER_STRUCT.size)
                if len(header_bytes) < GLOBAL_HEADER_STRUCT.size:
                    return err_result("TRACE_IO_GUARD", f"incomplete trace header: {chunk.path}")
                fed = session.feed(header_bytes)
                if not fed.ok:
                    return Result(
                        code="TRACE_IO_GUARD",
                        message=fed.message,
                        warnings=fed.warnings,
                        untrusted_windows=fed.untrusted_windows,
                    )
                if chunk.has_segment_meta:
                    meta_bytes = handle.read(SEGMENT_META_STRUCT.size)
                    if len(meta_bytes) < SEGMENT_META_STRUCT.size:
                        return err_result("TRACE_IO_GUARD", f"incomplete segment meta: {chunk.path}")
                    meta_result = session.feed(meta_bytes)
                    if not meta_result.ok:
                        return Result(
                            code="TRACE_IO_GUARD",
                            message=meta_result.message,
                            warnings=meta_result.warnings,
                            untrusted_windows=meta_result.untrusted_windows,
                        )
                handle.seek(chunk.header_offset)
                chunk_header_bytes = handle.read(CHUNK_HEADER_STRUCT.size)
                chunk_payload = handle.read(chunk.payload_size)
                if len(chunk_header_bytes) < CHUNK_HEADER_STRUCT.size or len(chunk_payload) < chunk.payload_size:
                    return err_result("TRACE_IO_GUARD", f"incomplete chunk payload: {chunk.path}")
                header_result = session.feed(chunk_header_bytes)
                if not header_result.ok:
                    return Result(
                        code="CORRUPT_SEGMENT",
                        message=header_result.message,
                        warnings=header_result.warnings,
                        untrusted_windows=header_result.untrusted_windows,
                    )
                chunk_result = session.feed(chunk_payload)
                if not chunk_result.ok:
                    return Result(
                        code="CORRUPT_SEGMENT",
                        message=chunk_result.message,
                        warnings=chunk_result.warnings,
                        untrusted_windows=chunk_result.untrusted_windows,
                    )
        except OSError as exc:
            return err_result("TRACE_IO_GUARD", f"trace span read failed for {chunk.path}: {exc}")

        if session.windows:
            return Result(
                code="CORRUPT_SEGMENT",
                message=f"trace span read produced untrusted windows for {chunk.path}",
                warnings=session.warnings,
                untrusted_windows=session.windows,
            )
        aligned = align_events(
            session.events,
            dataset_id=dataset_id,
            existing_windows=session.windows,
        )
        if not aligned.ok:
            return Result(
                code=aligned.code,
                message=aligned.message,
                warnings=aligned.warnings,
                untrusted_windows=aligned.untrusted_windows,
            )
        materialized_events = [materialize_unified_event(event, dataset_id) for event in aligned.data["events"]]
        for event in materialized_events:
            if _event_matches_any_span(event, relevant_spans):
                matched_by_ref[str(event.ref_key)] = event
        scan_count += 1
        seek_count += 1
        bytes_read += int(CHUNK_HEADER_STRUCT.size + chunk.payload_size)

    ordered_events = sorted(matched_by_ref.values(), key=lambda event: event.sort_key)
    target_ref_count = int(len(target_refs))
    matched_ref_count = int(len({str(event.ref_key) for event in ordered_events}))
    missed_ref_count = max(0, int(target_ref_count - matched_ref_count))
    window_hit_rate = 1.0 if target_ref_count == 0 else float(matched_ref_count / target_ref_count)
    return ok_result(
        {
            "matched_events": ordered_events,
            "bytes_read": int(bytes_read),
            "scan_count": int(scan_count),
            "seek_count": int(seek_count),
            "span_total": int(span_total),
            "corrupt_segments": [],
            "io_guard_triggered": False,
            "telemetry": {
                "catalog_chunk_count": len(catalog),
                "selected_chunk_count": int(scan_count),
                "planned_span_count": int(planned_span_count),
                "selected_span_count": int(len(selected_span_ids)),
                "target_ref_count": int(target_ref_count),
                "matched_ref_count": int(matched_ref_count),
                "missed_ref_count": int(missed_ref_count),
                "window_hit_rate": float(window_hit_rate),
            },
        }
    )


def _scan_trace_events(
    trace_paths: list[Path],
    *,
    dataset_id: str,
    cursor: EventCursor | None,
    time_window: tuple[float, float] | None,
    direction: str = "forward",
) -> Result[list[UnifiedEvent]]:
    session = TraceDecodeSession(dataset_id=dataset_id)
    normalized_direction = "backward" if direction == "backward" else "forward"
    scan_begin = None
    scan_end = None
    if normalized_direction == "backward":
        scan_end = float(cursor.ts) if cursor is not None else None
    else:
        scan_begin = float(cursor.ts) if cursor is not None else None
    if time_window is not None:
        window_begin = float(time_window[0])
        window_end = float(time_window[1])
        if normalized_direction == "backward":
            scan_begin = max(scan_begin, window_begin) if scan_begin is not None else window_begin
            if scan_end is None:
                scan_end = window_end
            else:
                scan_end = min(scan_end, window_end)
        else:
            scan_begin = max(scan_begin, window_begin) if scan_begin is not None else window_begin
            scan_end = window_end
    for path in trace_paths:
        with path.open("rb") as handle:
            header_bytes = handle.read(GLOBAL_HEADER_STRUCT.size)
            if len(header_bytes) < GLOBAL_HEADER_STRUCT.size:
                return err_result("INVALID_ARG", f"incomplete trace header: {path}")
            fed = session.feed(header_bytes)
            if not fed.ok:
                return Result(
                    code=fed.code,
                    message=fed.message,
                    warnings=fed.warnings,
                    untrusted_windows=fed.untrusted_windows,
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
                if chunk_tuple[0] != TRACE_CHUNK_MAGIC:
                    return err_result("INVALID_ARG", f"invalid chunk magic in {path}")
                payload_size = int(chunk_tuple[4])
                chunk_start = float(chunk_tuple[5])
                chunk_end = float(chunk_tuple[6])
                if scan_begin is not None and chunk_end < scan_begin:
                    handle.seek(payload_size, 1)
                    continue
                if scan_end is not None and chunk_start > scan_end:
                    handle.seek(payload_size, 1)
                    continue
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
    finalized = session.finalize()
    if not finalized.ok:
        return Result(
            code=finalized.code,
            message=finalized.message,
            warnings=finalized.warnings,
            untrusted_windows=finalized.untrusted_windows,
        )
    aligned = align_events(
        finalized.data["events"],
        dataset_id=dataset_id,
        existing_windows=finalized.data["untrusted_windows"],
    )
    if not aligned.ok:
        return Result(
            code=aligned.code,
            message=aligned.message,
            warnings=finalized.warnings + aligned.warnings,
            untrusted_windows=aligned.untrusted_windows,
        )
    return ok_result(
        aligned.data["events"],
        warnings=finalized.warnings + aligned.warnings,
        untrusted_windows=aligned.untrusted_windows,
    )


def _align_trace_page_candidate(
    *,
    dataset_id: str,
    decoder: TraceDecodeSession,
    filter_spec: dict[str, object],
    cursor: EventCursor | None,
    limit: int,
) -> Result[EventPage]:
    aligned = align_events(
        decoder.events,
        dataset_id=dataset_id,
        existing_windows=decoder.windows,
    )
    if not aligned.ok:
        return Result(
            code=aligned.code,
            message=aligned.message,
            warnings=list(decoder.warnings) + aligned.warnings,
            untrusted_windows=aligned.untrusted_windows,
        )
    materialized_events = [materialize_unified_event(event, dataset_id) for event in aligned.data["events"]]
    page = _event_page_from_events(
        _apply_filter(materialized_events, filter_spec),
        cursor=cursor,
        limit=limit,
        source="trace_window_scan",
        direction="forward",
    )
    return ok_result(
        page,
        warnings=list(decoder.warnings) + aligned.warnings,
        untrusted_windows=aligned.untrusted_windows,
    )


def _query_events_from_trace_forward_page(
    trace_paths: list[Path],
    *,
    dataset_id: str,
    filter_spec: dict[str, object],
    cursor: EventCursor | None,
    limit: int,
    time_window: tuple[float, float] | None,
) -> Result[EventPage]:
    session = TraceDecodeSession(dataset_id=dataset_id)
    scan_begin = float(cursor.ts) if cursor is not None else None
    scan_end = None
    if time_window is not None:
        window_begin = float(time_window[0])
        window_end = float(time_window[1])
        scan_begin = max(scan_begin, window_begin) if scan_begin is not None else window_begin
        scan_end = window_end

    active_filter = _merge_time_window(filter_spec, time_window)
    probe_threshold = max(int(limit), 64)
    candidate_page: Result[EventPage] | None = None
    candidate_tail_ts: float | None = None

    for path in trace_paths:
        with path.open("rb") as handle:
            header_bytes = handle.read(GLOBAL_HEADER_STRUCT.size)
            if len(header_bytes) < GLOBAL_HEADER_STRUCT.size:
                return err_result("INVALID_ARG", f"incomplete trace header: {path}")
            fed = session.feed(header_bytes)
            if not fed.ok:
                return Result(
                    code=fed.code,
                    message=fed.message,
                    warnings=fed.warnings,
                    untrusted_windows=fed.untrusted_windows,
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
                if chunk_tuple[0] != TRACE_CHUNK_MAGIC:
                    return err_result("INVALID_ARG", f"invalid chunk magic in {path}")
                payload_size = int(chunk_tuple[4])
                chunk_start = float(chunk_tuple[5])
                chunk_end = float(chunk_tuple[6])
                if scan_begin is not None and chunk_end < scan_begin:
                    handle.seek(payload_size, 1)
                    continue
                if scan_end is not None and chunk_start > scan_end:
                    handle.seek(payload_size, 1)
                    continue
                if (
                    candidate_page is not None
                    and candidate_page.ok
                    and len(candidate_page.data.items) >= max(int(limit), 1)
                    and candidate_tail_ts is not None
                    and chunk_start > candidate_tail_ts
                ):
                    return candidate_page
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
                if not session.events or len(session.events) < probe_threshold:
                    continue
                candidate_page = _align_trace_page_candidate(
                    dataset_id=dataset_id,
                    decoder=session,
                    filter_spec=active_filter,
                    cursor=cursor,
                    limit=limit,
                )
                if not candidate_page.ok:
                    return candidate_page
                if candidate_page.data.items:
                    candidate_tail_ts = float(candidate_page.data.items[-1].timestamp_aligned)
                probe_threshold = max(len(session.events) * 2, probe_threshold * 2)

    final_page = _align_trace_page_candidate(
        dataset_id=dataset_id,
        decoder=session,
        filter_spec=active_filter,
        cursor=cursor,
        limit=limit,
    )
    return final_page


def query_events_from_bundle(
    bundle: RebuildBundle,
    filter_spec: dict[str, object] | None = None,
    cursor: EventCursor | None = None,
    limit: int = 100,
    direction: str = "forward",
) -> EventPage:
    events = _apply_filter(bundle.event_stream, filter_spec or {})
    return _event_page_from_events(
        events,
        cursor=cursor,
        limit=limit,
        source="materialized_bundle",
        direction=direction,
    )


def query_events_from_trace(
    source_handle: str | Path,
    filter_spec: dict[str, object] | None = None,
    cursor: EventCursor | None = None,
    limit: int = 100,
    time_window: tuple[float, float] | None = None,
    direction: str = "forward",
) -> Result[EventPage]:
    trace_paths = _coerce_trace_paths(source_handle)
    if not trace_paths.ok:
        return trace_paths
    dataset_id = Path(source_handle).stem or "trace"
    if direction != "backward":
        return _query_events_from_trace_forward_page(
            trace_paths.data,
            dataset_id=dataset_id,
            filter_spec=filter_spec or {},
            cursor=cursor,
            limit=limit,
            time_window=time_window,
        )
    scanned = _scan_trace_events(
        trace_paths.data,
        dataset_id=dataset_id,
        cursor=cursor,
        time_window=time_window,
        direction=direction,
    )
    if not scanned.ok:
        return scanned
    active_filter = _merge_time_window(filter_spec, time_window)
    page = _event_page_from_events(
        _apply_filter(scanned.data, active_filter),
        cursor=cursor,
        limit=limit,
        source="trace_window_scan",
        direction=direction,
    )
    return ok_result(page, warnings=scanned.warnings, untrusted_windows=scanned.untrusted_windows)


def query_events_from_package(
    package_path: str | Path,
    filter_spec: dict[str, object] | None = None,
    cursor: EventCursor | None = None,
    limit: int = 100,
    time_window: tuple[float, float] | None = None,
    direction: str = "forward",
) -> Result[EventPage]:
    package_dir = Path(package_path)
    ref_index_path = package_dir / "event" / "ref_index.json"
    if ref_index_path.exists():
        ref_rows = json.loads(ref_index_path.read_text(encoding="utf-8"))
        normalized_direction = "backward" if direction == "backward" else "forward"
        scan_begin = None
        scan_end = None
        if normalized_direction == "backward":
            scan_end = float(cursor.ts) if cursor is not None else None
        else:
            scan_begin = float(cursor.ts) if cursor is not None else None
        if time_window is not None:
            window_begin = float(time_window[0])
            window_end = float(time_window[1])
            if normalized_direction == "backward":
                scan_begin = max(scan_begin, window_begin) if scan_begin is not None else window_begin
                if scan_end is None:
                    scan_end = window_end
                else:
                    scan_end = min(scan_end, window_end)
            else:
                scan_begin = max(scan_begin, window_begin) if scan_begin is not None else window_begin
                scan_end = window_end
        candidate_rows = [
            row
            for row in ref_rows
            if (scan_begin is None or float(row.get("timestamp_aligned", row.get("timestamp_raw", 0.0))) >= scan_begin)
            and (scan_end is None or float(row.get("timestamp_aligned", row.get("timestamp_raw", 0.0))) <= scan_end)
        ]
        if not candidate_rows:
            return ok_result(
                EventPage(
                    items=[],
                    order_by="sort_key asc",
                    cursor_in=cursor,
                    next_cursor=None,
                    prev_cursor=cursor,
                    has_more=False,
                    total_hint=0,
                    trusted=True,
                    summary={"source": "package_index"},
                )
            )
    trace_path = package_dir / "event" / "events.trace"
    traced = query_events_from_trace(
        trace_path,
        filter_spec,
        cursor,
        limit,
        time_window,
        direction,
    )
    if not traced.ok:
        return traced
    traced.data.summary["source"] = "package_index"
    return traced


def query_events_source_backed(
    source_handle: str | Path,
    filter_spec: dict[str, object] | None = None,
    cursor: EventCursor | None = None,
    limit: int = 100,
    time_window: tuple[float, float] | None = None,
    direction: str = "forward",
) -> Result[EventPage]:
    source_path = Path(source_handle)
    if source_path.is_dir() and (source_path / "manifest.json").exists() and (source_path / "event" / "events.trace").exists():
        return query_events_from_package(source_path, filter_spec, cursor, limit, time_window, direction)
    return query_events_from_trace(source_path, filter_spec, cursor, limit, time_window, direction)


def _normalize_task_state_filter_value(value: object) -> object:
    if value is None or isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return value


def build_task_state_window_index(
    bundle: RebuildBundle,
    bucket_count: int = DEFAULT_TIME_INDEX_BUCKETS,
) -> dict[str, Any]:
    segments = sorted(bundle.task_states, key=lambda item: (item.t_begin, item.t_end, item.task_id, item.seg_id))
    if not segments:
        return {
            "origin": 0.0,
            "bucket_size": 1.0,
            "bucket_to_segment_indexes": {},
            "segments": [],
        }
    origin = min(float(item.t_begin) for item in segments)
    end = max(float(item.t_end) for item in segments)
    span = max(end - origin, 1.0)
    bucket_size = max(span / max(int(bucket_count), 1), 1.0)
    bucket_to_segment_indexes: dict[int, list[int]] = defaultdict(list)
    for index, segment in enumerate(segments):
        start_bucket = _bucket_id(float(segment.t_begin), origin, bucket_size)
        end_bucket = _bucket_id(max(float(segment.t_end) - 1e-9, float(segment.t_begin)), origin, bucket_size)
        for bucket_id in range(start_bucket, end_bucket + 1):
            bucket_to_segment_indexes[bucket_id].append(index)
    return {
        "origin": origin,
        "bucket_size": bucket_size,
        "bucket_to_segment_indexes": dict(bucket_to_segment_indexes),
        "segments": segments,
    }


def query_task_state_segments(
    index_state: dict[str, Any],
    time_window: tuple[float, float],
    state_mask: list[str] | None = None,
    filter_spec: dict[str, object] | None = None,
) -> list[TaskStateSeg]:
    segments: list[TaskStateSeg] = list(index_state.get("segments") or [])
    if not segments:
        return []
    begin = float(time_window[0])
    end = float(time_window[1])
    if end <= begin:
        return []
    origin = float(index_state.get("origin", 0.0))
    bucket_size = float(index_state.get("bucket_size", 1.0)) or 1.0
    bucket_map: dict[int, list[int]] = dict(index_state.get("bucket_to_segment_indexes") or {})
    start_bucket = _bucket_id(begin, origin, bucket_size)
    end_bucket = _bucket_id(max(end - 1e-9, begin), origin, bucket_size)
    candidate_indexes: set[int] = set()
    for bucket_id in range(start_bucket, end_bucket + 1):
        candidate_indexes.update(bucket_map.get(bucket_id, []))
    active_filter = dict(filter_spec or {})
    task_id = active_filter.get("task_id")
    task_ids = set(active_filter.get("task_ids") or [])
    resource_id = _normalize_task_state_filter_value(active_filter.get("resource_id") or active_filter.get("obj_id"))
    visible_states = set(state_mask or [])
    results: list[TaskStateSeg] = []
    for index in sorted(candidate_indexes):
        segment = segments[index]
        if float(segment.t_begin) >= end or float(segment.t_end) <= begin:
            continue
        if visible_states and segment.state not in visible_states:
            continue
        if task_id is not None and segment.task_id != task_id:
            continue
        if task_ids and segment.task_id not in task_ids:
            continue
        if resource_id is not None and segment.related_obj != resource_id:
            continue
        results.append(segment)
    results.sort(key=lambda item: (item.t_begin, item.t_end, item.task_id, item.seg_id))
    return results


def query_timeline_buckets(
    bundle: RebuildBundle,
    time_window: tuple[float, float],
    bucket_count: int = 16,
    filter_spec: dict[str, object] | None = None,
) -> dict[str, Any]:
    rows, bucket_size = _build_bucket_rows(time_window, bucket_count)
    if not rows:
        return {
            "buckets": [],
            "summary": {
                "source": "index_bundle",
                "fallback": False,
                "bucket_count": 0,
                "bucket_size": 0.0,
            },
        }

    active_filter = dict(filter_spec or {})
    core_id = int(active_filter["core_id"]) if active_filter.get("core_id") is not None else None
    if bundle.index_bundle is None or bundle.index_bundle.summary.get("index_build_mode") == "deferred":
        idx_Build(bundle, mode=default_on_demand_index_build_mode(bundle))

    if (
        bundle.index_bundle is not None
        and bundle.index_bundle.summary.get("index_build_mode") != "deferred"
        and _supports_index_filter(active_filter)
    ):
        for bucket in bundle.index_bundle.time_index:
            if float(bucket.get("t_end", 0.0)) <= float(time_window[0]) or float(bucket.get("t_begin", 0.0)) >= float(time_window[1]):
                continue
            _accumulate_rows(
                rows,
                float(bucket["t_begin"]),
                float(bucket["t_end"]),
                _bucket_value(bucket, "event_count", "core_event_counts", core_id),
                "event_count",
            )
            _accumulate_rows(
                rows,
                float(bucket["t_begin"]),
                float(bucket["t_end"]),
                _bucket_value(bucket, "slice_count", "core_slice_counts", core_id),
                "slice_count",
            )
            _accumulate_rows(
                rows,
                float(bucket["t_begin"]),
                float(bucket["t_end"]),
                _bucket_value(bucket, "irq_count", "core_irq_counts", core_id),
                "irq_count",
            )
        return {
            "buckets": rows,
            "summary": {
                "source": "index_bundle",
                "fallback": False,
                "bucket_count": len(rows),
                "bucket_size": bucket_size,
            },
        }

    fallback_filter = {
        **active_filter,
        "t_begin": float(time_window[0]),
        "t_end": float(time_window[1]),
    }
    events = _apply_filter(bundle.event_stream, fallback_filter)
    for event in events:
        _accumulate_rows(
            rows,
            float(event.timestamp_aligned),
            float(event.timestamp_aligned),
            1.0,
            "event_count",
        )

    slices = [
        item
        for item in bundle.exec_slices
        if item.t_begin < float(time_window[1]) and item.t_end > float(time_window[0])
    ]
    irqs = [
        item
        for item in bundle.irq_spans
        if item.t_begin < float(time_window[1]) and item.t_end > float(time_window[0])
    ]
    if active_filter.get("task_id") is not None:
        slices = [item for item in slices if item.task_id == active_filter["task_id"]]
    if core_id is not None:
        slices = [item for item in slices if item.core_id == core_id]
        irqs = [item for item in irqs if item.core_id == core_id]
    if active_filter.get("irq_id") is not None:
        irqs = [item for item in irqs if item.irq_id == active_filter["irq_id"]]
    for item in slices:
        _accumulate_rows(rows, float(item.t_begin), float(item.t_end), 1.0, "slice_count")
    for item in irqs:
        _accumulate_rows(rows, float(item.t_begin), float(item.t_end), 1.0, "irq_count")

    return {
        "buckets": rows,
        "summary": {
            "source": "scan_fallback",
            "fallback": True,
            "bucket_count": len(rows),
            "bucket_size": bucket_size,
        },
    }


def query_events(
    bundle: RebuildBundle,
    filter_spec: dict[str, object] | None = None,
    cursor: EventCursor | None = None,
    limit: int = 100,
    direction: str = "forward",
) -> EventPage:
    return query_events_from_bundle(bundle, filter_spec, cursor, limit, direction)

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .align import align_events
from .codec import (
    CHUNK_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_FORMAT_MAGIC,
    TRACE_SEGMENT_META_MAGIC,
    TraceDecodeSession,
)
from .index import idx_Build
from .models import DatasetArtifact, GlobalHeader, LoadPreview, ReadinessState, TaskStatePreview, dataclass_to_dict
from .rebuild import rb_Rebuild
from .result import Result, err_result, ok_result
from .rtd_lineage import CaptureLineageContext, LineageValidationError, validate_capture_lineage_context
from .runtime_cost_graph import build_pipeline_runtime_cost_graph
from .telemetry import StageObserver, add_stage_timing, emit_stage_update, memory_snapshot


@dataclass
class ParserSession:
    dataset_id: str | None = None
    cfg: dict[str, Any] = field(default_factory=dict)
    dictionary: dict[str, Any] | str | Path | None = None
    stage_observer: StageObserver | None = None
    stage_timings: dict[str, float] | None = None
    materialize_events: bool = True
    expected_capture_id: str | None = None
    decoder: TraceDecodeSession = field(init=False)

    def __post_init__(self) -> None:
        self.decoder = TraceDecodeSession(
            dataset_id=self.dataset_id,
            dictionary=self.dictionary,
            stage_observer=self.stage_observer,
            stage_timings=self.stage_timings,
            materialize_events=self.materialize_events,
            expected_capture_id=self.expected_capture_id,
        )


def prs_Init(
    cfg: dict[str, Any] | None = None,
    dictionary: dict[str, Any] | str | Path | None = None,
    *,
    stage_observer: StageObserver | None = None,
    stage_timings: dict[str, float] | None = None,
    materialize_events: bool = True,
    expected_capture_id: str | None = None,
) -> Result[ParserSession]:
    config = dict(cfg or {})
    dataset_id = config.get("dataset_id")
    if dataset_id is not None:
        dataset_id = str(dataset_id)
    return ok_result(
        ParserSession(
            dataset_id=dataset_id,
            cfg=config,
            dictionary=dictionary,
            stage_observer=stage_observer,
            stage_timings=stage_timings,
            materialize_events=materialize_events,
            expected_capture_id=expected_capture_id,
        )
    )


def prs_FeedChunk(session: ParserSession, core_id: int | None, payload: bytes | bytearray) -> Result[dict[str, Any]]:
    if not isinstance(payload, (bytes, bytearray)):
        return err_result("INVALID_ARG", "payload must be bytes-like")
    feed_started = time.perf_counter()
    fed = session.decoder.feed(payload, source_core_id=core_id)
    feed_seconds = time.perf_counter() - feed_started
    add_stage_timing(session.stage_timings, "prs_FeedChunk_seconds", feed_seconds)
    if not fed.ok:
        emit_stage_update(
            session.stage_observer,
            "prs_FeedChunk",
            status="failed",
            seconds=feed_seconds,
            stage_timings=session.stage_timings,
        )
        return Result(
            code=fed.code,
            message=fed.message,
            warnings=fed.warnings,
            untrusted_windows=fed.untrusted_windows,
        )
    if fed.data.decoded_records > 0:
        emit_stage_update(
            session.stage_observer,
            "prs_FeedChunk",
            status="progress",
            seconds=feed_seconds,
            stage_timings=session.stage_timings,
            chunk_progress=session.decoder.latest_chunk_progress(),
            hotspot_summary=session.decoder.decode_hotspot_summary(),
        )
    return ok_result(
        {
            "decoded_records": fed.data.decoded_records,
            "gaps": [window for window in fed.data.gaps],
            "warnings": list(fed.data.warnings),
            "online_cursor": fed.data.online_cursor,
        },
        warnings=fed.warnings,
        untrusted_windows=fed.untrusted_windows,
    )


def prs_Finalize(session: ParserSession) -> Result[dict[str, object]]:
    return session.decoder.finalize()


def _artifact_from_parsed_details(
    parsed: Result[dict[str, object]],
    source: str | Path,
    *,
    stage_timings: dict[str, float] | None = None,
    stage_observer: StageObserver | None = None,
    memory_snapshots: list[dict[str, object]] | None = None,
    materialize_event_stream: bool = True,
    index_build_mode: str = "full",
    experimental_parallel_rebuild: bool = False,
    rebuild_morsel_size: int | None = None,
    rebuild_parallel_workers: int | None = None,
    lineage_context: CaptureLineageContext | None = None,
) -> Result[dict[str, object]]:
    if not parsed.ok:
        return Result(
            code=parsed.code,
            message=parsed.message,
            warnings=parsed.warnings,
            untrusted_windows=parsed.untrusted_windows,
        )
    if lineage_context is not None:
        header = parsed.data.get("header")
        trace_capture_id = getattr(header, "run_id", None)
        if trace_capture_id != lineage_context.capture_id:
            return err_result(
                "INVALID_ARG",
                "capture lineage context does not match trace header capture identity",
            )
    summary = parsed.data.get("summary")
    dataset_id = getattr(summary, "dataset_id", None) or Path(str(source)).stem or "stream"
    timings = dict(stage_timings or {})

    align_start_snapshot = memory_snapshot("align_start")
    if memory_snapshots is not None:
        memory_snapshots.append(align_start_snapshot)
    emit_stage_update(
        stage_observer,
        "align_events",
        status="start",
        memory_snapshot_payload=align_start_snapshot,
        stage_timings=timings,
    )
    align_started = time.perf_counter()
    aligned = align_events(
        parsed.data["events"],
        dataset_id=dataset_id,
        existing_windows=parsed.data["untrusted_windows"],
    )
    align_seconds = round(time.perf_counter() - align_started, 6)
    timings["align_events_seconds"] = align_seconds
    align_end_snapshot = memory_snapshot("align_end")
    if memory_snapshots is not None:
        memory_snapshots.append(align_end_snapshot)
    emit_stage_update(
        stage_observer,
        "align_events",
        status="completed",
        seconds=align_seconds,
        memory_snapshot_payload=align_end_snapshot,
        stage_timings=timings,
    )
    if not aligned.ok:
        return Result(
            code=aligned.code,
            message=aligned.message,
            warnings=aligned.warnings,
            untrusted_windows=aligned.untrusted_windows,
        )
    parsed.data.pop("events", None)

    rebuild_start_snapshot = memory_snapshot("rebuild_start")
    if memory_snapshots is not None:
        memory_snapshots.append(rebuild_start_snapshot)
    emit_stage_update(
        stage_observer,
        "rb_Rebuild",
        status="start",
        memory_snapshot_payload=rebuild_start_snapshot,
        stage_timings=timings,
    )
    rebuild_started = time.perf_counter()
    rebuild = rb_Rebuild(
        aligned.data["events"],
        dataset_id=dataset_id,
        header=parsed.data["header"],
        untrusted_windows=aligned.data["untrusted_windows"],
        capability_flags=aligned.data["capability_flags"],
        materialize_event_stream=materialize_event_stream,
        release_source_events=not materialize_event_stream,
        experimental_parallel_rebuild=experimental_parallel_rebuild,
        morsel_size=rebuild_morsel_size,
        parallel_workers=rebuild_parallel_workers,
        lineage_context=lineage_context,
    )
    rebuild_stage_seconds = round(time.perf_counter() - rebuild_started, 6)
    timings["rb_Rebuild_seconds"] = rebuild_stage_seconds
    rebuild_end_snapshot = memory_snapshot("rebuild_end")
    if memory_snapshots is not None:
        memory_snapshots.append(rebuild_end_snapshot)
    emit_stage_update(
        stage_observer,
        "rb_Rebuild",
        status="completed",
        seconds=rebuild_stage_seconds,
        memory_snapshot_payload=rebuild_end_snapshot,
        stage_timings=timings,
    )
    if not rebuild.ok:
        return Result(
            code=rebuild.code,
            message=rebuild.message,
            warnings=rebuild.warnings,
            untrusted_windows=rebuild.untrusted_windows,
        )
    aligned_events = aligned.data.pop("events", None)
    if isinstance(aligned_events, list):
        aligned_events.clear()
    rebuild.data.alignment = aligned.data.get("alignment")
    rebuild.data.segment_metas = list(parsed.data.get("segment_metas") or [])

    index_start_snapshot = memory_snapshot("index_start")
    if memory_snapshots is not None:
        memory_snapshots.append(index_start_snapshot)
    emit_stage_update(
        stage_observer,
        "idx_Build",
        status="start",
        memory_snapshot_payload=index_start_snapshot,
        stage_timings=timings,
    )
    index_started = time.perf_counter()
    indexed = idx_Build(rebuild.data, mode=index_build_mode)
    index_seconds = round(time.perf_counter() - index_started, 6)
    timings["idx_Build_seconds"] = index_seconds
    index_end_snapshot = memory_snapshot("index_end")
    if memory_snapshots is not None:
        memory_snapshots.append(index_end_snapshot)
    emit_stage_update(
        stage_observer,
        "idx_Build",
        status="completed",
        seconds=index_seconds,
        memory_snapshot_payload=index_end_snapshot,
        stage_timings=timings,
    )
    if not indexed.ok:
        return Result(
            code=indexed.code,
            message=indexed.message,
            warnings=indexed.warnings,
            untrusted_windows=indexed.untrusted_windows,
        )
    artifact = DatasetArtifact(
        dataset_id=dataset_id,
        source=str(source),
        header=parsed.data["header"],
        bundle=rebuild.data,
        dictionary_info=dict(parsed.data.get("dictionary_info") or {}),
    )
    return ok_result(
        {
            "artifact": artifact,
            "stage_timings": timings,
        },
        warnings=parsed.warnings + aligned.warnings + rebuild.warnings,
        untrusted_windows=rebuild.untrusted_windows,
    )


def _artifact_from_parsed(
    parsed: Result[dict[str, object]],
    source: str | Path,
    *,
    lineage_context: CaptureLineageContext | None = None,
) -> Result[DatasetArtifact]:
    detailed = _artifact_from_parsed_details(
        parsed,
        source,
        materialize_event_stream=True,
        lineage_context=lineage_context,
    )
    if not detailed.ok:
        return Result(
            code=detailed.code,
            message=detailed.message,
            warnings=detailed.warnings,
            untrusted_windows=detailed.untrusted_windows,
        )
    return ok_result(
        detailed.data["artifact"],
        warnings=detailed.warnings,
        untrusted_windows=detailed.untrusted_windows,
    )


def _chunk_payload(chunk: Any) -> tuple[int | None, bytes | bytearray]:
    if isinstance(chunk, tuple) and len(chunk) == 2:
        return chunk[0], chunk[1]
    if hasattr(chunk, "payload"):
        return getattr(chunk, "core_id", None), getattr(chunk, "payload")
    raise TypeError("chunk must be a (core_id, payload) tuple or expose payload/core_id attributes")


def _snapshot_rss_mb(snapshot: object) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    for key in ("rss_mb", "peak_rss_mb", "peak_rss", "rss"):
        value = snapshot.get(key)
        if value is None:
            continue
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            continue
    return None


def _peak_rss_mb_from_snapshots(memory_snapshots: Iterable[object]) -> float | None:
    peak_rss_values = [
        rss_mb
        for rss_mb in (_snapshot_rss_mb(snapshot) for snapshot in memory_snapshots)
        if rss_mb is not None
    ]
    if not peak_rss_values:
        return None
    return round(max(peak_rss_values), 6)


def _coerce_trace_paths(source: str | Path | Iterable[str | Path]) -> Result[list[Path]]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            segments = sorted(item for item in path.iterdir() if item.is_file() and item.suffix == ".trace")
            if not segments:
                return err_result("INVALID_ARG", f"trace directory has no .trace segments: {path}")
            return ok_result(segments)
        return ok_result([path])
    try:
        paths = [Path(item) for item in source]
    except TypeError:
        return err_result("INVALID_ARG", "trace source must be path-like or iterable of paths")
    if not paths:
        return err_result("INVALID_ARG", "trace source is empty")
    return ok_result(paths)


def _iter_bytes(payload: bytes, read_size: int) -> Iterable[bytes]:
    cursor = 0
    while cursor < len(payload):
        yield payload[cursor : cursor + read_size]
        cursor += read_size


def _read_prefix(handle: Any, byte_count: int, read_size: int) -> bytes:
    prefix = bytearray()
    while len(prefix) < byte_count:
        chunk = handle.read(min(read_size, byte_count - len(prefix)))
        if not chunk:
            break
        prefix.extend(chunk)
    return bytes(prefix)


def _iter_trace_file_chunks(path: Path, read_size: int, segment_index: int) -> Iterable[bytes]:
    with path.open("rb") as handle:
        while True:
            payload = handle.read(read_size)
            if not payload:
                break
            yield payload


def _validate_capture_bound_segment_header(
    path: Path,
    expected_capture_id: str,
    read_size: int,
) -> Result[None]:
    try:
        with path.open("rb") as handle:
            raw_header = _read_prefix(handle, GLOBAL_HEADER_STRUCT.size, read_size)
    except OSError as exc:
        return err_result("INVALID_ARG", f"trace segment unavailable: {path}: {exc}")
    if len(raw_header) != GLOBAL_HEADER_STRUCT.size:
        return err_result("INVALID_ARG", f"capture lineage context requires a trace header for segment: {path}")
    header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(raw_header, 0)
    if header_tuple[0] != TRACE_FORMAT_MAGIC:
        return err_result("INVALID_ARG", f"capture lineage context requires a trace header for segment: {path}")
    try:
        header = _global_header_from_tuple(header_tuple)
    except (UnicodeDecodeError, ValueError):
        return err_result("INVALID_ARG", f"capture lineage context requires a valid UTF-8 identity for segment: {path}")
    if header.run_id != expected_capture_id:
        return err_result("INVALID_ARG", "capture lineage context does not match trace header capture identity")
    return ok_result(None)


def _global_header_from_tuple(header_tuple: tuple[int, ...]) -> GlobalHeader:
    (
        magic,
        endian,
        time_unit,
        format_ver,
        dict_ver,
        _header_ver,
        _core_count,
        _clock_source,
        producer_ver,
        run_id,
    ) = header_tuple
    return GlobalHeader(
        magic=hex(magic),
        endian=endian,
        time_unit=time_unit,
        clock_source=1,
        format_ver=format_ver,
        dict_ver=dict_ver,
        producer_ver=producer_ver.decode("utf-8").rstrip("\0"),
        run_id=run_id.decode("utf-8").rstrip("\0") or None,
    )


def _validate_trace_paths(paths: list[Path]) -> Result[list[Path]]:
    for path in paths:
        if not path.exists():
            return err_result("INVALID_ARG", f"trace file not found: {path}")
        if not path.is_file():
            return err_result("INVALID_ARG", f"trace source is not a file: {path}")
    return ok_result(paths)


def _build_prescan_buckets(
    chunks: list[dict[str, Any]],
    time_window: tuple[float, float],
    bucket_count: int,
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
    for chunk in chunks:
        begin = float(chunk["t_begin"])
        end = float(chunk["t_end"])
        if end < begin:
            begin, end = end, begin
        if end <= begin:
            for row in rows:
                if begin < float(row["t_end"]):
                    row["event_count"] = round(float(row["event_count"]) + float(chunk["record_count"]), 6)
                    row["chunk_count"] = round(float(row["chunk_count"]) + 1.0, 6)
                    break
            continue
        duration = max(end - begin, 1e-9)
        for row in rows:
            overlap_begin = max(begin, float(row["t_begin"]))
            overlap_end = min(end, float(row["t_end"]))
            overlap = max(overlap_end - overlap_begin, 0.0)
            if overlap <= 0.0:
                continue
            portion = float(chunk["record_count"]) * (overlap / duration)
            row["event_count"] = round(float(row["event_count"]) + portion, 6)
            row["chunk_count"] = round(float(row["chunk_count"]) + (overlap / duration), 6)
    return rows


def _preview_view_ready(**overrides: bool) -> dict[str, bool]:
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


def _preview_readiness(source: str) -> ReadinessState:
    return ReadinessState(
        stage="preview_ready",
        source=source,
        preview=True,
        lod_ready={
            "lod0": True,
            "lod1": False,
            "lod2": False,
        },
        view_ready=_preview_view_ready(timeline=True),
    )


def _empty_task_state_preview_buckets(
    time_window: tuple[float, float],
    bucket_count: int,
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
                "state_counts": {},
            }
        )
        cursor = bucket_end
    return rows


def _task_state_preview_bucket_index(
    timestamp: float,
    time_window: tuple[float, float],
    bucket_count: int,
) -> int:
    t_begin, t_end = float(time_window[0]), float(time_window[1])
    if bucket_count <= 0 or t_end <= t_begin:
        return 0
    clamped = min(max(float(timestamp), t_begin), t_end)
    if clamped >= t_end:
        return bucket_count - 1
    relative = (clamped - t_begin) / max(t_end - t_begin, 1.0)
    return min(max(int(relative * bucket_count), 0), bucket_count - 1)


def _task_state_preview_transition(
    *,
    task_id: int,
    state: str,
    timestamp: float,
    task_ids: set[int],
    state_totals: dict[str, int],
    bucket_summary: list[dict[str, Any]],
    time_window: tuple[float, float],
) -> None:
    if task_id <= 0:
        return
    task_ids.add(task_id)
    state_totals[state] = state_totals.get(state, 0) + 1
    if bucket_summary:
        bucket_index = _task_state_preview_bucket_index(timestamp, time_window, len(bucket_summary))
        state_counts = bucket_summary[bucket_index]["state_counts"]
        state_counts[state] = int(state_counts.get(state, 0)) + 1


def _build_task_state_preview(
    paths: list[Path],
    *,
    read_size: int,
    time_window: tuple[float, float],
    bucket_count: int,
    source: str,
) -> Result[TaskStatePreview]:
    decoder = TraceDecodeSession(dataset_id=Path(paths[0]).stem if paths else "stream")
    task_ids: set[int] = set()
    state_totals: dict[str, int] = {}
    bucket_summary = _empty_task_state_preview_buckets(time_window, bucket_count)

    for segment_index, path in enumerate(paths):
        for chunk in _iter_trace_file_chunks(path, read_size, segment_index):
            fed = decoder.feed(chunk)
            if not fed.ok:
                return Result(
                    code=fed.code,
                    message=fed.message,
                    warnings=fed.warnings,
                    untrusted_windows=fed.untrusted_windows,
                )
            for event in decoder.events:
                payload = event.payload
                if event.event_name == "TASK_READY":
                    _task_state_preview_transition(
                        task_id=int(payload.get("task_id", 0)),
                        state="READY",
                        timestamp=event.timestamp_aligned,
                        task_ids=task_ids,
                        state_totals=state_totals,
                        bucket_summary=bucket_summary,
                        time_window=time_window,
                    )
                elif event.event_name == "TASK_BLOCK":
                    _task_state_preview_transition(
                        task_id=int(payload.get("task_id", 0)),
                        state="BLOCKED",
                        timestamp=event.timestamp_aligned,
                        task_ids=task_ids,
                        state_totals=state_totals,
                        bucket_summary=bucket_summary,
                        time_window=time_window,
                    )
                elif event.event_name == "TASK_WAKEUP":
                    _task_state_preview_transition(
                        task_id=int(payload.get("task_id", 0)),
                        state="READY",
                        timestamp=event.timestamp_aligned,
                        task_ids=task_ids,
                        state_totals=state_totals,
                        bucket_summary=bucket_summary,
                        time_window=time_window,
                    )
                elif event.event_name == "TASK_EXIT":
                    _task_state_preview_transition(
                        task_id=int(payload.get("task_id", 0)),
                        state="EXIT",
                        timestamp=event.timestamp_aligned,
                        task_ids=task_ids,
                        state_totals=state_totals,
                        bucket_summary=bucket_summary,
                        time_window=time_window,
                    )
                elif event.event_name == "CTX_SWITCH":
                    prev_task_id = int(payload.get("prev_task_id", 0))
                    next_task_id = int(payload.get("next_task_id", 0))
                    if prev_task_id:
                        _task_state_preview_transition(
                            task_id=prev_task_id,
                            state="READY",
                            timestamp=event.timestamp_aligned,
                            task_ids=task_ids,
                            state_totals=state_totals,
                            bucket_summary=bucket_summary,
                            time_window=time_window,
                        )
                    if next_task_id:
                        _task_state_preview_transition(
                            task_id=next_task_id,
                            state="RUNNING",
                            timestamp=event.timestamp_aligned,
                            task_ids=task_ids,
                            state_totals=state_totals,
                            bucket_summary=bucket_summary,
                            time_window=time_window,
                        )
            decoder.events.clear()

    readiness = _preview_readiness(source)
    return ok_result(
        TaskStatePreview(
            time_window=time_window,
            lane_count=len(task_ids),
            task_ids=sorted(task_ids),
            state_totals=dict(sorted(state_totals.items())),
            bucket_summary=bucket_summary,
            readiness=readiness,
            trusted=not decoder.windows,
        ),
        warnings=list(decoder.warnings),
        untrusted_windows=list(decoder.windows),
    )


def prs_Prescan(
    source: str | Path | Iterable[str | Path],
    read_size: int = 4096,
    bucket_count: int = 24,
    *,
    include_task_state_preview: bool = True,
) -> Result[dict[str, Any]]:
    if read_size <= 0:
        return err_result("INVALID_ARG", "read_size must be positive")
    paths_result = _coerce_trace_paths(source)
    if not paths_result.ok:
        return paths_result
    validation = _validate_trace_paths(paths_result.data)
    if not validation.ok:
        return validation
    paths = validation.data

    header: GlobalHeader | None = None
    dataset_id = Path(str(source)).stem if isinstance(source, (str, Path)) else "stream"
    chunk_rows: list[dict[str, Any]] = []
    core_ids: set[int] = set()
    record_count = 0

    for segment_index, path in enumerate(paths):
        with path.open("rb") as handle:
            if segment_index == 0:
                raw_header = _read_prefix(handle, GLOBAL_HEADER_STRUCT.size, read_size)
                if len(raw_header) != GLOBAL_HEADER_STRUCT.size:
                    return err_result("INVALID_ARG", f"trace header truncated: {path}")
                header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(raw_header, 0)
                if header_tuple[0] != TRACE_FORMAT_MAGIC:
                    return err_result("INVALID_ARG", f"invalid trace magic: {path}")
                header = _global_header_from_tuple(header_tuple)
                dataset_id = header.run_id or path.stem or dataset_id
                if header.format_ver >= 2:
                    raw_segment_meta = _read_prefix(handle, SEGMENT_META_STRUCT.size, read_size)
                    if len(raw_segment_meta) != SEGMENT_META_STRUCT.size:
                        return err_result("INVALID_ARG", f"segment meta truncated: {path}")
                    meta_tuple = SEGMENT_META_STRUCT.unpack_from(raw_segment_meta, 0)
                    if meta_tuple[0] != TRACE_SEGMENT_META_MAGIC:
                        return err_result("INVALID_ARG", f"invalid segment meta magic: {path}")
                    if int(meta_tuple[2]) != SEGMENT_META_STRUCT.size:
                        return err_result(
                            "INVALID_ARG",
                            (
                                f"unsupported segment meta size: {path}: "
                                f"expected {SEGMENT_META_STRUCT.size}, got {int(meta_tuple[2])}"
                            ),
                        )
            else:
                prefix = _read_prefix(handle, GLOBAL_HEADER_STRUCT.size, read_size)
                if len(prefix) == GLOBAL_HEADER_STRUCT.size:
                    header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(prefix, 0)
                    if header_tuple[0] != TRACE_FORMAT_MAGIC:
                        handle.seek(0)
                    elif int(header_tuple[3]) >= 2:
                        raw_segment_meta = _read_prefix(handle, SEGMENT_META_STRUCT.size, read_size)
                        if len(raw_segment_meta) != SEGMENT_META_STRUCT.size:
                            return err_result("INVALID_ARG", f"segment meta truncated: {path}")
                        meta_tuple = SEGMENT_META_STRUCT.unpack_from(raw_segment_meta, 0)
                        if meta_tuple[0] != TRACE_SEGMENT_META_MAGIC:
                            return err_result("INVALID_ARG", f"invalid segment meta magic: {path}")
                        if int(meta_tuple[2]) != SEGMENT_META_STRUCT.size:
                            return err_result(
                                "INVALID_ARG",
                                (
                                    f"unsupported segment meta size: {path}: "
                                    f"expected {SEGMENT_META_STRUCT.size}, got {int(meta_tuple[2])}"
                                ),
                            )
                else:
                    handle.seek(0)

            while True:
                chunk_header = _read_prefix(handle, CHUNK_HEADER_STRUCT.size, read_size)
                if not chunk_header:
                    break
                if len(chunk_header) != CHUNK_HEADER_STRUCT.size:
                    return err_result("INVALID_ARG", f"chunk header truncated: {path}")
                chunk_tuple = CHUNK_HEADER_STRUCT.unpack_from(chunk_header, 0)
                if chunk_tuple[0] != 0x43484B31:
                    return err_result("INVALID_ARG", f"invalid chunk magic during prescan: {path}")
                payload_bytes = int(chunk_tuple[4])
                skipped = handle.seek(payload_bytes, 1)
                if skipped is None:
                    payload = handle.read(payload_bytes)
                    if len(payload) != payload_bytes:
                        return err_result("INVALID_ARG", f"chunk payload truncated: {path}")
                chunk_rows.append(
                    {
                        "t_begin": float(chunk_tuple[5]),
                        "t_end": float(chunk_tuple[6]),
                        "record_count": int(chunk_tuple[3]),
                        "core_id": int(chunk_tuple[2]),
                    }
                )
                core_ids.add(int(chunk_tuple[2]))
                record_count += int(chunk_tuple[3])

    if header is None:
        return err_result("INVALID_ARG", "trace header missing")

    if chunk_rows:
        time_window = (
            min(float(item["t_begin"]) for item in chunk_rows),
            max(float(item["t_end"]) for item in chunk_rows),
        )
    else:
        time_window = (0.0, 0.0)
    task_state_preview: Result[TaskStatePreview] | None = None
    if include_task_state_preview:
        task_state_preview = _build_task_state_preview(
            paths,
            read_size=read_size,
            time_window=time_window,
            bucket_count=bucket_count,
            source="prescan",
        )
        if not task_state_preview.ok:
            return task_state_preview

    readiness = _preview_readiness("prescan")
    preview = LoadPreview(
        dataset_id=dataset_id,
        header=header.to_dict(),
        time_window=time_window,
        chunk_count=len(chunk_rows),
        record_count=record_count,
        core_ids=sorted(core_ids),
        lod0_buckets=_build_prescan_buckets(chunk_rows, time_window, bucket_count),
        source="prescan",
        stage=readiness.stage,
        readiness=readiness,
        task_state_preview=task_state_preview.data if task_state_preview is not None else None,
    )
    return ok_result(
        dataclass_to_dict(preview),
        warnings=list(task_state_preview.warnings) if task_state_preview is not None else [],
        untrusted_windows=list(task_state_preview.untrusted_windows) if task_state_preview is not None else [],
    )


def prs_Load(
    source: str | Path | Iterable[str | Path],
    read_size: int = 4096,
    dictionary: dict[str, Any] | str | Path | None = None,
    *,
    stage_observer: StageObserver | None = None,
    stage_timings: dict[str, float] | None = None,
    materialize_events: bool = True,
    expected_capture_id: str | None = None,
) -> Result[dict[str, object]]:
    if read_size <= 0:
        return err_result("INVALID_ARG", "read_size must be positive")
    paths_result = _coerce_trace_paths(source)
    if not paths_result.ok:
        return paths_result
    validation = _validate_trace_paths(paths_result.data)
    if not validation.ok:
        return validation
    paths = validation.data
    init = prs_Init(
        dictionary=dictionary,
        stage_observer=stage_observer,
        stage_timings=stage_timings,
        materialize_events=materialize_events,
        expected_capture_id=expected_capture_id,
    )
    session = init.data
    load_start_snapshot = memory_snapshot("prs_Load_start")
    emit_stage_update(
        stage_observer,
        "prs_Load",
        status="start",
        memory_snapshot_payload=load_start_snapshot,
        stage_timings=stage_timings,
    )
    load_started = time.perf_counter()
    for index, path in enumerate(paths):
        if expected_capture_id is not None:
            header_validation = _validate_capture_bound_segment_header(path, expected_capture_id, read_size)
            if not header_validation.ok:
                load_seconds = time.perf_counter() - load_started
                add_stage_timing(stage_timings, "prs_Load_seconds", load_seconds)
                emit_stage_update(
                    stage_observer,
                    "prs_Load",
                    status="failed",
                    seconds=load_seconds,
                    stage_timings=stage_timings,
                )
                return Result(
                    code=header_validation.code,
                    message=header_validation.message,
                    warnings=header_validation.warnings,
                    untrusted_windows=header_validation.untrusted_windows,
                )
        for chunk in _iter_trace_file_chunks(path, read_size, index):
            fed = prs_FeedChunk(session, None, chunk)
            if not fed.ok:
                load_seconds = time.perf_counter() - load_started
                add_stage_timing(stage_timings, "prs_Load_seconds", load_seconds)
                emit_stage_update(
                    stage_observer,
                    "prs_Load",
                    status="failed",
                    seconds=load_seconds,
                    stage_timings=stage_timings,
                )
                return Result(
                    code=fed.code,
                    message=fed.message,
                    warnings=fed.warnings,
                    untrusted_windows=fed.untrusted_windows,
                )
    finalized = prs_Finalize(session)
    load_seconds = time.perf_counter() - load_started
    add_stage_timing(stage_timings, "prs_Load_seconds", load_seconds)
    load_status = "completed" if finalized.ok else "failed"
    emit_stage_update(
        stage_observer,
        "prs_Load",
        status=load_status,
        seconds=load_seconds,
        memory_snapshot_payload=memory_snapshot(
            "prs_Load_completed" if finalized.ok else "prs_Load_failed"
        ),
        stage_timings=stage_timings,
        chunk_progress=session.decoder.latest_chunk_progress(),
        hotspot_summary=session.decoder.decode_hotspot_summary(),
    )
    return finalized


def prs_LoadChunks(
    chunks: Iterable[Any],
    cfg: dict[str, Any] | None = None,
    dictionary: dict[str, Any] | str | Path | None = None,
    *,
    stage_observer: StageObserver | None = None,
    stage_timings: dict[str, float] | None = None,
    expected_capture_id: str | None = None,
) -> Result[dict[str, object]]:
    init = prs_Init(
        cfg,
        dictionary,
        stage_observer=stage_observer,
        stage_timings=stage_timings,
        expected_capture_id=expected_capture_id,
    )
    if not init.ok:
        return init
    session = init.data
    try:
        for chunk in chunks:
            core_id, payload = _chunk_payload(chunk)
            fed = prs_FeedChunk(session, core_id, payload)
            if not fed.ok:
                return Result(
                    code=fed.code,
                    message=fed.message,
                    warnings=fed.warnings,
                    untrusted_windows=fed.untrusted_windows,
                )
    except TypeError as exc:
        return err_result("INVALID_ARG", str(exc))
    return prs_Finalize(session)


def prs_Verify(
    source: str | Path | Iterable[str | Path],
    dictionary: dict[str, Any] | str | Path | None = None,
    *,
    stage_observer: StageObserver | None = None,
    stage_timings: dict[str, float] | None = None,
    materialize_events: bool = True,
    expected_capture_id: str | None = None,
) -> Result[dict[str, object]]:
    loaded = prs_Load(
        source,
        dictionary=dictionary,
        stage_observer=stage_observer,
        stage_timings=stage_timings,
        materialize_events=materialize_events,
        expected_capture_id=expected_capture_id,
    )
    if not loaded.ok:
        return loaded
    header = loaded.data["header"]
    if header.format_ver not in {1, 2}:
        return err_result("INVALID_ARG", f"unsupported format version: {header.format_ver}")
    return loaded


def prs_Align(
    source: str | Path | Iterable[str | Path],
    dictionary: dict[str, Any] | str | Path | None = None,
) -> Result[dict[str, object]]:
    loaded = prs_Verify(source, dictionary=dictionary)
    if not loaded.ok:
        return loaded
    summary = loaded.data.get("summary")
    dataset_id = getattr(summary, "dataset_id", None) or "stream"
    aligned = align_events(
        loaded.data["events"],
        dataset_id=dataset_id,
        existing_windows=loaded.data["untrusted_windows"],
    )
    if not aligned.ok:
        return Result(
            code=aligned.code,
            message=aligned.message,
            warnings=aligned.warnings,
            untrusted_windows=aligned.untrusted_windows,
        )
    data = dict(aligned.data)
    data["header"] = loaded.data["header"]
    data["warnings"] = loaded.warnings + aligned.warnings
    data["summary"] = loaded.data.get("summary")
    return ok_result(data, warnings=loaded.warnings + aligned.warnings, untrusted_windows=aligned.untrusted_windows)


def load_dataset(
    source: str | Path | Iterable[str | Path],
    dictionary: dict[str, Any] | str | Path | None = None,
    *,
    lineage_context: CaptureLineageContext | None = None,
) -> Result[DatasetArtifact]:
    if lineage_context is not None:
        try:
            validate_capture_lineage_context(lineage_context)
        except LineageValidationError as exc:
            return err_result("INVALID_ARG", str(exc))
    verified = prs_Verify(
        source,
        dictionary=dictionary,
        expected_capture_id=None if lineage_context is None else lineage_context.capture_id,
    )
    return _artifact_from_parsed(verified, source, lineage_context=lineage_context)


def load_dataset_with_timings(
    source: str | Path | Iterable[str | Path],
    dictionary: dict[str, Any] | str | Path | None = None,
    stage_observer: StageObserver | None = None,
    materialize_event_stream: bool = True,
    index_build_mode: str = "full",
    experimental_parallel_rebuild: bool = False,
    rebuild_morsel_size: int | None = None,
    rebuild_parallel_workers: int | None = None,
    lineage_context: CaptureLineageContext | None = None,
) -> Result[dict[str, object]]:
    if lineage_context is not None:
        try:
            validate_capture_lineage_context(lineage_context)
        except LineageValidationError as exc:
            return err_result("INVALID_ARG", str(exc))
    memory_snapshots = [memory_snapshot("parse_start")]
    stage_timings: dict[str, float] = {}
    emit_stage_update(
        stage_observer,
        "prs_Verify",
        status="start",
        memory_snapshot_payload=memory_snapshots[-1],
        stage_timings=stage_timings,
    )
    parse_started = time.perf_counter()
    verified = prs_Verify(
        source,
        dictionary=dictionary,
        stage_observer=stage_observer,
        stage_timings=stage_timings,
        materialize_events=False,
        expected_capture_id=None if lineage_context is None else lineage_context.capture_id,
    )
    parse_seconds = round(time.perf_counter() - parse_started, 6)
    stage_timings["prs_Verify_seconds"] = parse_seconds
    if not verified.ok:
        emit_stage_update(
            stage_observer,
            "prs_Verify",
            status="failed",
            seconds=parse_seconds,
            stage_timings=stage_timings,
            hotspot_summary=verified.data.get("decode_hotspot_summary") if verified.data else None,
        )
        return Result(
            code=verified.code,
            message=verified.message,
            warnings=verified.warnings,
            untrusted_windows=verified.untrusted_windows,
        )
    memory_snapshots.append(memory_snapshot("parse_end"))
    emit_stage_update(
        stage_observer,
        "prs_Verify",
        status="completed",
        seconds=parse_seconds,
        memory_snapshot_payload=memory_snapshots[-1],
        stage_timings=stage_timings,
        hotspot_summary=verified.data.get("decode_hotspot_summary"),
    )

    detailed = _artifact_from_parsed_details(
        verified,
        source,
        stage_timings=stage_timings,
        stage_observer=stage_observer,
        memory_snapshots=memory_snapshots,
        materialize_event_stream=materialize_event_stream,
        index_build_mode=index_build_mode,
        experimental_parallel_rebuild=experimental_parallel_rebuild,
        rebuild_morsel_size=rebuild_morsel_size,
        rebuild_parallel_workers=rebuild_parallel_workers,
        lineage_context=lineage_context,
    )
    if not detailed.ok:
        return Result(
            code=detailed.code,
            message=detailed.message,
            warnings=detailed.warnings,
            untrusted_windows=detailed.untrusted_windows,
        )
    stage_timings = dict(detailed.data.get("stage_timings") or {})
    align_events_seconds = round(float(stage_timings.get("align_events_seconds", 0.0)), 6)
    rebuild_stage_seconds = round(float(stage_timings.get("rb_Rebuild_seconds", 0.0)), 6)
    idx_build_seconds = round(float(stage_timings.get("idx_Build_seconds", 0.0)), 6)
    stage_timings["rebuild_stage_seconds"] = rebuild_stage_seconds
    pipeline_rebuild_seconds = round(
        align_events_seconds + rebuild_stage_seconds + idx_build_seconds,
        6,
    )
    rebuild_seconds = rebuild_stage_seconds
    load_seconds = round(parse_seconds + pipeline_rebuild_seconds, 6)
    stage_timings["load_seconds"] = load_seconds
    peak_rss_mb = _peak_rss_mb_from_snapshots(memory_snapshots)
    experimental_rebuild_requested = bool(
        experimental_parallel_rebuild or rebuild_morsel_size is not None or rebuild_parallel_workers is not None
    )
    if experimental_rebuild_requested:
        rebuild_mode = "morsel_rebuild" if rebuild_morsel_size is not None else "parallel_rebuild"
    else:
        rebuild_mode = "serial_rebuild"

    return ok_result(
        {
            "artifact": detailed.data["artifact"],
            "parse_seconds": parse_seconds,
            "align_events_seconds": align_events_seconds,
            "rebuild_seconds": rebuild_seconds,
            "pipeline_rebuild_seconds": pipeline_rebuild_seconds,
            "idx_build_seconds": idx_build_seconds,
            "load_seconds": load_seconds,
            "load_stage_timings": stage_timings,
            "memory_snapshots": memory_snapshots,
            "load_hotspot_summary": dict(verified.data.get("decode_hotspot_summary") or {}),
            "index_build_mode": index_build_mode,
            "materialize_event_stream": bool(materialize_event_stream),
            "experimental_parallel_rebuild": experimental_rebuild_requested,
            "rebuild_morsel_size": rebuild_morsel_size,
            "rebuild_parallel_workers": rebuild_parallel_workers,
            "rebuild_mode": rebuild_mode,
            "peak_rss_mb": peak_rss_mb,
            "runtime_cost_graph": build_pipeline_runtime_cost_graph(
                dataset_id=detailed.data["artifact"].dataset_id,
                stage_timings=stage_timings,
                index_build_mode=index_build_mode,
                materialize_event_stream=materialize_event_stream,
            ),
        },
        warnings=detailed.warnings,
        untrusted_windows=detailed.untrusted_windows,
    )


def load_dataset_from_chunks(
    chunks: Iterable[Any],
    *,
    source: str = "channel",
    cfg: dict[str, Any] | None = None,
    dictionary: dict[str, Any] | None = None,
    lineage_context: CaptureLineageContext | None = None,
) -> Result[DatasetArtifact]:
    if lineage_context is not None:
        try:
            validate_capture_lineage_context(lineage_context)
        except LineageValidationError as exc:
            return err_result("INVALID_ARG", str(exc))
    config = dict(cfg or {})
    config.setdefault("online_mode", True)
    parsed = prs_LoadChunks(
        chunks,
        config,
        dictionary,
        expected_capture_id=None if lineage_context is None else lineage_context.capture_id,
    )
    return _artifact_from_parsed(parsed, source, lineage_context=lineage_context)

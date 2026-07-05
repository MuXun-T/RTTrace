from __future__ import annotations

from .models import AlignmentSummary, CoreAlignSegment, DecodedEvent, UnifiedEvent, UntrustedWindow, event_sort_key
from .result import Result, ok_result


ALIGNMENT_EVENT_NAMES = {"SYNC_CALIB", "TS_CALIB"}


def _is_sorted_by_key(events: list[UnifiedEvent | DecodedEvent], key_name: str) -> bool:
    if len(events) < 2:
        return True
    if key_name == "raw":
        key = lambda event: (event.timestamp_raw, event.core_id, event.seq)
    else:
        key = event_sort_key
    previous = key(events[0])
    for event in events[1:]:
        current = key(event)
        if current < previous:
            return False
        previous = current
    return True


def _anchor_offsets(
    events: list[UnifiedEvent | DecodedEvent],
) -> tuple[dict[int, float], list[CoreAlignSegment], bool, bool, int | None]:
    cores = sorted({event.core_id for event in events})
    if len(cores) <= 1:
        return {core_id: 0.0 for core_id in cores}, [], False, False, cores[0] if cores else None
    anchors: dict[int, list[float]] = {}
    for event in events:
        if event.event_name not in ALIGNMENT_EVENT_NAMES:
            continue
        anchors.setdefault(event.core_id, []).append(float(event.timestamp_raw))
    for core_id in list(anchors):
        anchors[core_id] = sorted(anchors[core_id])
    if not anchors:
        return {core_id: 0.0 for core_id in cores}, [], False, False, cores[0] if cores else None
    if len(anchors) != len(cores):
        return {core_id: 0.0 for core_id in cores}, [], True, False, min(anchors)
    reference_core = min(anchors)
    reference_ts = anchors[reference_core][0]
    offsets: dict[int, float] = {}
    segments: list[CoreAlignSegment] = []
    for core_id in cores:
        first_anchor_ts = anchors[core_id][0]
        offset = reference_ts - first_anchor_ts
        offsets[core_id] = offset
        # P0 segment model: calibration applies only from the first known anchor onward.
        segments.append(
            CoreAlignSegment(
                core_id=core_id,
                t_begin=first_anchor_ts,
                t_end=float("inf"),
                offset_ns=offset,
            )
        )
    return offsets, segments, True, True, reference_core


def _segment_for_event(
    event: UnifiedEvent | DecodedEvent,
    segments_by_core: dict[int, list[CoreAlignSegment]],
) -> CoreAlignSegment | None:
    segments = segments_by_core.get(event.core_id) or []
    ts = float(event.timestamp_raw)
    for segment in segments:
        if segment.t_begin <= ts <= segment.t_end:
            return segment
    return None


def align_events(
    events: list[UnifiedEvent | DecodedEvent],
    dataset_id: str,
    existing_windows: list[UntrustedWindow] | None = None,
) -> Result[dict[str, object]]:
    windows = list(existing_windows or [])
    offsets, segments, anchors_seen, calibrated, reference_core_id = _anchor_offsets(events)
    core_ids: set[int] = set()
    job_semantics = False
    instance_semantics = False
    uncovered_timestamps: list[float] = []
    has_applied_offset = False
    t_begin = 0.0
    t_end = 0.0
    if events:
        t_begin = float(events[0].timestamp_raw)
        t_end = float(events[0].timestamp_raw)
    for event in events:
        core_ids.add(event.core_id)
        if not job_semantics and event.job_id is not None:
            job_semantics = True
        if not instance_semantics and event.instance_id is not None:
            instance_semantics = True
        timestamp = float(event.timestamp_raw)
        if timestamp < t_begin:
            t_begin = timestamp
        if timestamp > t_end:
            t_end = timestamp
    capability_flags = {
        "job_semantics": job_semantics,
        "instance_semantics": instance_semantics,
        "align_anchor_seen": anchors_seen,
        "align_calibrated": calibrated,
        "resource_closed": True,
        "irq_closed": True,
    }
    for segment in segments:
        if segment.t_end == float("inf"):
            segment.t_end = t_end
    segments_by_core: dict[int, list[CoreAlignSegment]] = {}
    for segment in segments:
        segments_by_core.setdefault(segment.core_id, []).append(segment)

    if not _is_sorted_by_key(events, "raw"):
        events.sort(key=lambda item: (item.timestamp_raw, item.core_id, item.seq))
    for event in events:
        segment = _segment_for_event(event, segments_by_core)
        if len(core_ids) <= 1:
            event.timestamp_aligned = event.timestamp_raw
        elif calibrated and segment is not None:
            event.timestamp_aligned = event.timestamp_raw + segment.offset_ns
            has_applied_offset = has_applied_offset or bool(segment.offset_ns != 0.0)
        else:
            event.timestamp_aligned = event.timestamp_raw
            uncovered_timestamps.append(float(event.timestamp_raw))
        if isinstance(event, UnifiedEvent):
            event.sort_key = (event.timestamp_aligned, event.core_id, event.seq)

    if len(core_ids) > 1 and (not capability_flags["align_calibrated"] or uncovered_timestamps):
        degraded_begin = min(uncovered_timestamps) if uncovered_timestamps else t_begin
        degraded_end = max(uncovered_timestamps) if uncovered_timestamps else t_end
        windows.append(
            UntrustedWindow(
                window_id=f"uw:{dataset_id}:align_degraded",
                source="align_fail",
                scope="rebuild",
                t_begin=degraded_begin,
                t_end=degraded_end,
                reason_code="ALIGN_DEGRADED",
                severity="warning",
            )
        )
    if has_applied_offset and not _is_sorted_by_key(events, "aligned"):
        events.sort(key=event_sort_key)
    sorted_core_ids = sorted(core_ids)
    alignment = AlignmentSummary(
        core_ids=sorted_core_ids,
        offsets={core_id: float(offsets.get(core_id, 0.0)) for core_id in sorted_core_ids},
        anchors_seen=anchors_seen,
        calibrated=calibrated,
        reference_core_id=reference_core_id,
        segments=segments,
    )
    return ok_result(
        {
            "events": events,
            "untrusted_windows": windows,
            "capability_flags": capability_flags,
            "alignment": alignment,
        }
    )

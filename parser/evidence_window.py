from __future__ import annotations

from pathlib import Path
from typing import Any

from parser.index import TraceChunkCatalog, read_trace_window_spans
from parser.result import Result, err_result, ok_result

from .evidence_models import DependencySidecarEdge, ReadWindowResult, WindowPlan, WindowSpan, evd_StableEventSortKey


def _normalize_ref_index_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in list(rows or []):
        ref_key = str(row.get("ref_key") or "").strip()
        if not ref_key:
            continue
        normalized.append(
            {
                "ref_key": ref_key,
                "timestamp_aligned": float(row.get("timestamp_aligned", row.get("timestamp_raw", 0.0))),
                "core_id": int(row.get("core_id", 0)),
                "seq": int(row.get("seq", 0)),
            }
        )
    return normalized


def _segment_seq_from_hint(hint: Any) -> int | None:
    text = str(hint or "").strip()
    if not text:
        return None
    for prefix in ("segment:", "segment_seq:", "segment#"):
        if text.startswith(prefix):
            suffix = text[len(prefix) :]
            if suffix.isdigit():
                return int(suffix)
    return None


def _median_gap_ns(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    widths = sorted(max(0, int(row["time_hint_end_ns"]) - int(row["time_hint_begin_ns"])) for row in rows)
    middle = len(widths) // 2
    if len(widths) % 2 == 1:
        median = widths[middle]
    else:
        median = (widths[middle - 1] + widths[middle]) // 2
    return max(0, min(int(median), 50000))


def _normalize_window_plan(window_plan: WindowPlan | dict[str, Any] | list[dict[str, Any]] | None) -> WindowPlan:
    if isinstance(window_plan, WindowPlan):
        return window_plan
    if isinstance(window_plan, dict):
        return WindowPlan.from_payload(window_plan)
    spans = list(window_plan or [])
    normalized_spans: list[WindowSpan] = []
    for index, row in enumerate(spans, start=1):
        normalized_spans.append(
            WindowSpan(
                span_id=str(row.get("window_id") or f"span:{index}"),
                t_begin=int(row.get("time_hint_begin_ns", 0)),
                t_end=int(row.get("time_hint_end_ns", row.get("time_hint_begin_ns", 0))),
                core_id=row.get("core_hint"),
                seq_begin=row.get("seq_hint_begin"),
                seq_end=row.get("seq_hint_end"),
                segment_seq=_segment_seq_from_hint(row.get("segment_hint")),
                source_edge_count=int(row.get("source_edge_count", 1)),
                target_refs=sorted(set(str(item) for item in list(row.get("target_refs") or []))),
            )
        )
    return WindowPlan(
        round_id=0,
        spans=normalized_spans,
        planned_seek_count=len(normalized_spans),
        planned_span_total=sum(max(0, int(span.t_end) - int(span.t_begin)) for span in normalized_spans),
        planned_ref_count=len({str(ref_key) for span in normalized_spans for ref_key in list(span.target_refs)}),
        unplanned_refs=[],
    )


def build_window_plan(
    candidate_edges: list[DependencySidecarEdge],
    delta_refs: list[str],
    segment_metas: list[Any] | None = None,
    ref_index_rows: list[dict[str, Any]] | None = None,
) -> WindowPlan:
    del segment_metas
    delta_set = {str(ref_key).strip() for ref_key in list(delta_refs or []) if str(ref_key).strip()}
    if not delta_set:
        return WindowPlan(round_id=0)
    ref_index = {row["ref_key"]: row for row in _normalize_ref_index_rows(ref_index_rows)}
    grouped: dict[tuple[str | None, int | None], list[dict[str, Any]]] = {}
    covered_refs: set[str] = set()

    for edge in list(candidate_edges or []):
        dst_ref = str(edge.dst_ref).strip()
        if not dst_ref or dst_ref not in delta_set:
            continue
        begin = int(min(edge.time_hint_begin_ns, edge.time_hint_end_ns))
        end = int(max(edge.time_hint_begin_ns, edge.time_hint_end_ns))
        key = (edge.segment_hint, edge.core_hint)
        grouped.setdefault(key, []).append(
            {
                "time_hint_begin_ns": begin,
                "time_hint_end_ns": end,
                "segment_hint": edge.segment_hint,
                "core_hint": edge.core_hint,
                "seq_hint_begin": edge.seq_hint_begin,
                "seq_hint_end": edge.seq_hint_end,
                "source_edge_count": 1,
                "target_refs": {dst_ref},
            }
        )
        covered_refs.add(dst_ref)

    for ref_key in sorted(delta_set.difference(covered_refs)):
        row = ref_index.get(ref_key)
        if row is None:
            continue
        begin = int(row["timestamp_aligned"])
        key = (f"core:{int(row['core_id'])}", int(row["core_id"]))
        grouped.setdefault(key, []).append(
            {
                "time_hint_begin_ns": begin,
                "time_hint_end_ns": begin,
                "segment_hint": key[0],
                "core_hint": key[1],
                "seq_hint_begin": int(row["seq"]),
                "seq_hint_end": int(row["seq"]),
                "source_edge_count": 0,
                "target_refs": {ref_key},
            }
        )
        covered_refs.add(ref_key)
        covered_refs.add(ref_key)

    merge_gap_ns = _median_gap_ns([row for rows in grouped.values() for row in rows])
    merged: list[dict[str, Any]] = []
    for group_key in sorted(grouped, key=lambda item: (str(item[0]), -1 if item[1] is None else int(item[1]))):
        rows = sorted(grouped[group_key], key=lambda item: (int(item["time_hint_begin_ns"]), int(item["time_hint_end_ns"])))
        current: dict[str, Any] | None = None
        for row in rows:
            if current is None:
                current = dict(row)
                continue
            if int(row["time_hint_begin_ns"]) <= int(current["time_hint_end_ns"]) + merge_gap_ns:
                current["time_hint_end_ns"] = max(int(current["time_hint_end_ns"]), int(row["time_hint_end_ns"]))
                current["target_refs"] = set(current["target_refs"]).union(set(row["target_refs"]))
                current["source_edge_count"] = int(current.get("source_edge_count", 0)) + int(row.get("source_edge_count", 0))
                if current.get("seq_hint_begin") is not None and row.get("seq_hint_begin") is not None:
                    current["seq_hint_begin"] = min(int(current["seq_hint_begin"]), int(row["seq_hint_begin"]))
                if current.get("seq_hint_end") is not None and row.get("seq_hint_end") is not None:
                    current["seq_hint_end"] = max(int(current["seq_hint_end"]), int(row["seq_hint_end"]))
                continue
            merged.append(current)
            current = dict(row)
        if current is not None:
            merged.append(current)

    spans: list[WindowSpan] = []
    for index, row in enumerate(
        sorted(
            merged,
            key=lambda item: (
                "" if item.get("segment_hint") is None else str(item.get("segment_hint")),
                -1 if item.get("core_hint") is None else int(item.get("core_hint")),
                int(item["time_hint_begin_ns"]),
                int(item["time_hint_end_ns"]),
            ),
        ),
        start=1,
    ):
        spans.append(
            WindowSpan(
                span_id=f"span:{index}",
                t_begin=int(row["time_hint_begin_ns"]),
                t_end=int(row["time_hint_end_ns"]),
                segment_seq=_segment_seq_from_hint(row.get("segment_hint")),
                core_id=row.get("core_hint"),
                seq_begin=row.get("seq_hint_begin"),
                seq_end=row.get("seq_hint_end"),
                source_edge_count=int(row.get("source_edge_count", 0)),
                target_refs=sorted(set(str(item) for item in list(row.get("target_refs") or []))),
            )
        )
    return WindowPlan(
        round_id=0,
        spans=spans,
        planned_seek_count=int(len(spans)),
        planned_span_total=int(sum(max(0, int(span.t_end) - int(span.t_begin)) for span in spans)),
        planned_ref_count=int(len(delta_set)),
        unplanned_refs=sorted(delta_set.difference(covered_refs)),
    )


def summarize_window_plan(window_plan: WindowPlan | dict[str, Any] | list[dict[str, Any]]) -> dict[str, int]:
    normalized = _normalize_window_plan(window_plan)
    return {
        "window_count": int(len(normalized.spans)),
        "scan_count": int(normalized.planned_seek_count),
        "seek_count": int(normalized.planned_seek_count),
        "window_span_total": int(normalized.planned_span_total),
        "planned_ref_count": int(normalized.planned_ref_count),
    }


def read_window_plan(
    trace_source: str | Path,
    window_plan: WindowPlan | dict[str, Any] | list[dict[str, Any]],
    delta_refs: list[str],
    *,
    prescanned_catalog: TraceChunkCatalog | None = None,
) -> Result[ReadWindowResult]:
    normalized_plan = _normalize_window_plan(window_plan)
    spans = list(normalized_plan.spans)
    target_refs = {str(ref_key).strip() for ref_key in list(delta_refs or []) if str(ref_key).strip()}
    ordered_target_refs = sorted(target_refs)
    unplanned_refs = [str(ref_key) for ref_key in list(normalized_plan.unplanned_refs or []) if str(ref_key).strip()]
    if unplanned_refs:
        return err_result("SIDECAR_MISMATCH", f"window plan missing refs: {', '.join(sorted(unplanned_refs))}")
    if not spans or not target_refs:
        target_ref_count = int(len(ordered_target_refs))
        matched_ref_count = 0
        missed_ref_count = int(target_ref_count)
        window_hit_rate = 1.0 if target_ref_count == 0 else 0.0
        return ok_result(
            ReadWindowResult(
                round_id=int(normalized_plan.round_id),
                matched_events=[],
                matched_refs=[],
                missed_refs=list(ordered_target_refs),
                bytes_read=0,
                scan_count=0,
                seek_count=0,
                span_total=0,
                window_span_total=0,
                window_count=0,
                corrupt_segments=[],
                io_guard_triggered=False,
                telemetry={
                    "target_refs": list(ordered_target_refs),
                    "target_ref_count": int(target_ref_count),
                    "matched_ref_count": int(matched_ref_count),
                    "missed_ref_count": int(missed_ref_count),
                    "window_hit_rate": float(window_hit_rate),
                    "planned_seek_count": int(normalized_plan.planned_seek_count),
                    "planned_ref_count": int(normalized_plan.planned_ref_count),
                    "unplanned_refs": list(normalized_plan.unplanned_refs),
                },
            )
        )

    if normalized_plan.unplanned_refs:
        return err_result(
            "SIDECAR_MISMATCH",
            f"window plan missing hints for refs: {', '.join(normalized_plan.unplanned_refs)}",
        )

    for span in spans:
        if not span.target_refs:
            return err_result("SIDECAR_MISMATCH", f"window span missing target refs: {span.span_id}")
        if span.t_begin is None or span.t_end is None:
            return err_result("SIDECAR_MISMATCH", f"window span missing time hints: {span.span_id}")

    read_result = read_trace_window_spans(
        trace_source,
        [span.to_dict() for span in spans],
        prescanned_catalog=prescanned_catalog,
    )
    if not read_result.ok:
        return read_result
    matched_events = [
        event
        for event in list(read_result.data.get("matched_events") or [])
        if str(event.ref_key) in target_refs
    ]
    ordered_events = sorted(matched_events, key=evd_StableEventSortKey)
    matched_refs = [str(event.ref_key) for event in ordered_events]
    missed_refs = sorted(target_refs.difference(matched_refs))
    matched_ref_count = int(len(matched_refs))
    target_ref_count = int(len(ordered_target_refs))
    missed_ref_count = int(len(missed_refs))
    window_hit_rate = 1.0 if target_ref_count == 0 else float(matched_ref_count / target_ref_count)
    return ok_result(
        ReadWindowResult(
            round_id=int(normalized_plan.round_id),
            matched_events=ordered_events,
            matched_refs=matched_refs,
            missed_refs=missed_refs,
            bytes_read=int(read_result.data.get("bytes_read", 0)),
            scan_count=int(read_result.data.get("scan_count", 0)),
            seek_count=int(read_result.data.get("seek_count", 0)),
            span_total=int(read_result.data.get("span_total", normalized_plan.planned_span_total)),
            window_span_total=int(read_result.data.get("span_total", normalized_plan.planned_span_total)),
            window_count=int(len(spans)),
            corrupt_segments=list(read_result.data.get("corrupt_segments") or []),
            io_guard_triggered=bool(read_result.data.get("io_guard_triggered", False)),
            telemetry={
                **dict(read_result.data.get("telemetry") or {}),
                "target_refs": list(ordered_target_refs),
                "target_ref_count": int(target_ref_count),
                "matched_ref_count": int(matched_ref_count),
                "missed_ref_count": int(missed_ref_count),
                "window_hit_rate": float(window_hit_rate),
                "planned_seek_count": int(normalized_plan.planned_seek_count),
                "planned_ref_count": int(normalized_plan.planned_ref_count),
                "unplanned_refs": list(normalized_plan.unplanned_refs),
            },
        )
    )

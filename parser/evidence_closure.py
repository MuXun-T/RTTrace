from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from parser.index import TraceChunkCatalog, build_trace_chunk_catalog
from parser.models import RebuildBundle, UnifiedEvent
from parser.result import Result
from spec.io import serialize

from .evidence_models import (
    DependencySidecarEdge,
    EvidenceClosureOutcome,
    EvidenceClosureState,
    EvidenceExportRequest,
    FrontierRefRow,
    ReadWindowResult,
    RoundProjection,
    SeedResolution,
    evd_StableEdgeSortKey,
    evd_StableEventSortKey,
)
from .evidence_sidecar import select_candidate_edges
from .evidence_window import build_window_plan, read_window_plan


_BUDGET_HALT_REASONS = {"DEPTH_LIMIT", "EVENT_LIMIT", "BYTE_LIMIT", "RHO_LIMIT"}
SidecarCandidateSelector = Callable[[list[str], tuple[str, ...]], Result[list[DependencySidecarEdge]]]


def _emit_progress(
    emit_progress: Callable[[dict[str, Any]], None] | None,
    *,
    substage: str,
    status: str,
    round_id: int | None = None,
    frontier_count: int | None = None,
    emitted_events: int | None = None,
    emitted_bytes: int | None = None,
) -> None:
    if emit_progress is None:
        return
    payload: dict[str, Any] = {
        "category": "evidence_export",
        "substage": str(substage),
        "status": str(status),
    }
    if round_id is not None:
        payload["round_id"] = int(round_id)
    if frontier_count is not None:
        payload["frontier_count"] = int(frontier_count)
    if emitted_events is not None:
        payload["emitted_events"] = int(emitted_events)
    if emitted_bytes is not None:
        payload["emitted_bytes"] = int(emitted_bytes)
    emit_progress(payload)


def _estimate_event_bytes(event: UnifiedEvent | None) -> int:
    if event is None:
        return 64
    payload_bytes = len(json.dumps(dict(event.payload or {}), ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return max(64, 48 + payload_bytes)


def _projected_halt_reason(
    request: EvidenceExportRequest,
    *,
    next_depth: int,
    consumed_events: int,
    consumed_bytes: int,
    projected_next_events: int,
    projected_next_bytes: int,
    expansion_ratio: float,
) -> str | None:
    budget = request.budget_vector
    for rule in request.closure_policy.halt_priority:
        if rule == "DEPTH_LIMIT" and next_depth > budget.D_max:
            return "DEPTH_LIMIT"
        if rule == "EVENT_LIMIT" and consumed_events + projected_next_events > budget.C_events:
            return "EVENT_LIMIT"
        if rule == "BYTE_LIMIT" and consumed_bytes + projected_next_bytes > budget.S_bytes:
            return "BYTE_LIMIT"
        if rule == "RHO_LIMIT" and expansion_ratio > budget.rho_max:
            return "RHO_LIMIT"
    return None


def _dedupe_refs(rows: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for row in rows:
        ref_key = str(row or "").strip()
        if not ref_key or ref_key in seen:
            continue
        seen.add(ref_key)
        normalized.append(ref_key)
    return normalized


def _build_round_read_summary(
    projection_bundle: dict[str, Any],
    read_payload: ReadWindowResult,
    *,
    read_ok: bool,
    read_error_code: str | None = None,
    read_error_message: str | None = None,
) -> dict[str, Any]:
    telemetry = dict(read_payload.telemetry or {})
    target_refs = _dedupe_refs(
        [str(ref_key) for ref_key in list(telemetry.get("target_refs") or projection_bundle.get("window_delta_refs") or [])]
    )
    matched_refs = _dedupe_refs([str(ref_key) for ref_key in list(read_payload.matched_refs)])
    missed_refs = _dedupe_refs([str(ref_key) for ref_key in list(read_payload.missed_refs)])
    if not read_ok and not missed_refs:
        missed_refs = list(target_refs)
    target_ref_count = int(len(target_refs))
    matched_ref_count = int(len(matched_refs))
    missed_ref_count = int(len(missed_refs))
    window_hit_rate = (
        float(telemetry["window_hit_rate"])
        if telemetry.get("window_hit_rate") is not None
        else (1.0 if target_ref_count == 0 else float(matched_ref_count / target_ref_count))
    )
    return {
        "round_id": int(read_payload.round_id or projection_bundle["projection"].round_id),
        "read_ok": bool(read_ok),
        "read_error_code": read_error_code,
        "read_error_message": read_error_message,
        "target_refs": list(target_refs),
        "target_ref_count": int(target_ref_count),
        "matched_refs": list(matched_refs),
        "matched_ref_count": int(matched_ref_count),
        "missed_refs": list(missed_refs),
        "missed_ref_count": int(missed_ref_count),
        "window_hit_rate": float(window_hit_rate),
        "scan_count": int(read_payload.scan_count),
        "seek_count": int(read_payload.seek_count),
        "span_total": int(read_payload.span_total),
        "window_span_total": int(read_payload.window_span_total),
        "window_count": int(read_payload.window_count),
        "catalog_chunk_count": int(telemetry.get("catalog_chunk_count", 0)),
        "selected_chunk_count": int(telemetry.get("selected_chunk_count", read_payload.scan_count)),
        "planned_span_count": int(telemetry.get("planned_span_count", 0)),
        "selected_span_count": int(telemetry.get("selected_span_count", 0)),
        "telemetry": telemetry,
    }


def _merge_workset_edges(
    existing: list[DependencySidecarEdge],
    additions: list[DependencySidecarEdge],
) -> list[DependencySidecarEdge]:
    merged: dict[str, DependencySidecarEdge] = {}
    for edge in list(existing) + list(additions):
        edge_key = str(edge.edge_hash or f"{edge.src_ref}|{edge.dst_ref}|{edge.relation_kind}|{edge.rule_family}")
        merged[edge_key] = edge
    return sorted(merged.values(), key=evd_StableEdgeSortKey)


def _frontier_rows_from_edges(
    rows: list[DependencySidecarEdge],
    *,
    limit: int,
) -> tuple[list[FrontierRefRow], int]:
    frontier_rows: list[FrontierRefRow] = []
    seen_refs: set[str] = set()
    total = 0
    for row in rows:
        if row.dst_ref in seen_refs:
            continue
        seen_refs.add(row.dst_ref)
        total += 1
        if len(frontier_rows) >= limit:
            continue
        frontier_rows.append(
            FrontierRefRow(
                ref_key=row.dst_ref,
                ref_kind=row.dst_kind,
                priority=int(row.priority),
                frontier_origin_rule=row.relation_kind,
                estimate_events=int(row.estimate_events),
                estimate_bytes=int(row.estimate_bytes),
                time_hint_begin_ns=int(row.time_hint_begin_ns),
                time_hint_end_ns=int(row.time_hint_end_ns),
                core_hint=row.core_hint,
                seq_hint_begin=row.seq_hint_begin,
                seq_hint_end=row.seq_hint_end,
                segment_hint=row.segment_hint,
            )
        )
    return frontier_rows, max(0, total - len(frontier_rows))


def evd_InitState(
    seed_refs: list[str],
    scope_events: list[str],
    *,
    missing_required_refs: list[str] | None = None,
) -> EvidenceClosureState:
    normalized_seed_refs = _dedupe_refs(seed_refs)
    normalized_scope_events = _dedupe_refs(scope_events)
    return EvidenceClosureState(
        seed_refs=normalized_seed_refs,
        scope_events=normalized_scope_events,
        closed_refs=list(normalized_seed_refs),
        frontier_refs=list(normalized_seed_refs),
        emitted_event_refs=list(normalized_scope_events),
        emitted_events_count=len(normalized_scope_events),
        missing_required_refs=_dedupe_refs(list(missing_required_refs or [])),
    )


def evd_ProjectRound(
    state: EvidenceClosureState,
    request: EvidenceExportRequest,
    sidecar_rows: list[DependencySidecarEdge],
    sidecar_candidate_selector: SidecarCandidateSelector | None = None,
) -> dict[str, Any]:
    frontier = list(state.frontier_refs)
    selector_error_code: str | None = None
    selector_error_message: str | None = None
    if sidecar_candidate_selector is None:
        candidate_edges = select_candidate_edges(sidecar_rows, frontier, request.rule_family)
    else:
        selected = sidecar_candidate_selector(frontier, request.rule_family)
        if not selected.ok:
            candidate_edges = []
            selector_error_code = selected.code
            selector_error_message = selected.message
        else:
            candidate_edges = list(selected.data or [])
    state.sidecar_lookup_count += len(frontier)

    cycle_guard_threshold = max(8, len(frontier))
    token_hits: dict[str, int] = {}
    seen_next_refs: set[str] = set()
    closed_ref_set = set(state.closed_refs)
    next_edges: list[DependencySidecarEdge] = []
    error_code: str | None = selector_error_code
    error_message: str | None = selector_error_message

    if error_code is None:
        for edge in candidate_edges:
            token = str(edge.cycle_guard_token or "").strip()
            if token:
                token_hits[token] = token_hits.get(token, 0) + 1
                if token_hits[token] > cycle_guard_threshold:
                    error_code = "CYCLE_INFLATION"
                    error_message = f"cycle_guard_token exceeded threshold: {token}"
                    break
            if edge.dst_ref in closed_ref_set or edge.dst_ref in seen_next_refs:
                continue
            if int(edge.estimate_events) <= 0 or int(edge.estimate_bytes) <= 0:
                error_code = "SIDECAR_MISMATCH"
                error_message = f"candidate edge missing usable estimate_*: {edge.dst_ref}"
                break
            seen_next_refs.add(edge.dst_ref)
            next_edges.append(edge)

    projected_next_events = sum(max(int(edge.estimate_events), 0) for edge in next_edges)
    projected_next_bytes = sum(max(int(edge.estimate_bytes), 0) for edge in next_edges)
    state.workset_edges = _merge_workset_edges(state.workset_edges, next_edges)
    expansion_ratio = len(next_edges) / max(1, len(frontier))
    frontier_rows, truncated_frontier_count = _frontier_rows_from_edges(
        next_edges,
        limit=request.closure_policy.frontier_ref_limit,
    )
    projection = RoundProjection(
        round_id=len(state.rounds) + 1,
        frontier_in_count=len(frontier),
        candidate_edge_count=len(next_edges),
        delta_ref_count=len(next_edges),
        estimate_events=projected_next_events,
        estimate_bytes=projected_next_bytes,
        expansion_ratio=float(expansion_ratio),
        would_halt=False,
        halt_reason=error_code,
        candidate_frontier_refs=frontier_rows,
    )
    if error_code is not None:
        projection = RoundProjection(
            **{**projection.__dict__, "would_halt": True}
        )
        window_candidate_edges = [edge for edge in next_edges if edge.dst_kind == "ref"]
        return {
            "projection": projection,
            "candidate_edges": next_edges,
            "delta_refs": [edge.dst_ref for edge in next_edges],
            "window_candidate_edges": window_candidate_edges,
            "window_delta_refs": [edge.dst_ref for edge in window_candidate_edges],
            "truncated_frontier_count": truncated_frontier_count,
            "candidate_edge_sample": [serialize(row) for row in next_edges[:5]],
            "error_code": error_code,
            "error_message": error_message,
        }

    halt_reason = _projected_halt_reason(
        request,
        next_depth=len(state.rounds) + 1,
        consumed_events=state.emitted_events_count,
        consumed_bytes=state.emitted_bytes,
        projected_next_events=projected_next_events,
        projected_next_bytes=projected_next_bytes,
        expansion_ratio=expansion_ratio,
    )
    if halt_reason is not None:
        projection = RoundProjection(
            **{**projection.__dict__, "would_halt": True, "halt_reason": halt_reason}
        )
    window_candidate_edges = [edge for edge in next_edges if edge.dst_kind == "ref"]
    return {
        "projection": projection,
        "candidate_edges": next_edges,
        "delta_refs": [edge.dst_ref for edge in next_edges],
        "window_candidate_edges": window_candidate_edges,
        "window_delta_refs": [edge.dst_ref for edge in window_candidate_edges],
        "truncated_frontier_count": truncated_frontier_count,
        "candidate_edge_sample": [serialize(row) for row in next_edges[:5]],
        "error_code": None,
        "error_message": None,
    }


def evd_ApplyRoundRead(
    state: EvidenceClosureState,
    projection_bundle: dict[str, Any],
    read_result: ReadWindowResult | dict[str, Any],
    *,
    event_by_ref: dict[str, UnifiedEvent],
) -> None:
    normalized_read = ReadWindowResult.from_payload(read_result)
    delta_refs = _dedupe_refs(list(projection_bundle.get("delta_refs") or []))
    state.closed_refs = _dedupe_refs(list(state.closed_refs) + delta_refs)
    state.frontier_refs = list(delta_refs)

    emitted_refs = list(state.emitted_event_refs)
    emitted_ref_set = set(emitted_refs)
    for event in list(normalized_read.matched_events):
        ref_key = str(event.ref_key)
        if ref_key in emitted_ref_set:
            continue
        emitted_ref_set.add(ref_key)
        emitted_refs.append(ref_key)
        state.emitted_bytes += _estimate_event_bytes(event)
    state.emitted_event_refs = emitted_refs
    state.emitted_events_count = len(emitted_refs)
    state.missing_required_refs = _dedupe_refs(
        list(state.missing_required_refs) + [str(ref_key) for ref_key in list(normalized_read.missed_refs)]
    )
    state.rounds.append(
        {
            "projection": serialize(projection_bundle["projection"]),
            "read_result": _build_round_read_summary(projection_bundle, normalized_read, read_ok=True),
        }
    )
    if state.emitted_event_refs:
        state.emitted_event_refs = [
            ref_key
            for ref_key in sorted(
                {ref_key for ref_key in state.emitted_event_refs if ref_key in event_by_ref},
                key=lambda ref_key: evd_StableEventSortKey(event_by_ref[ref_key]),
            )
        ]


def evd_Finalize(
    state: EvidenceClosureState,
    request: EvidenceExportRequest,
    *,
    event_by_ref: dict[str, UnifiedEvent],
    frontier_rows: list[FrontierRefRow] | None = None,
    frontier_count: int = 0,
    truncated_frontier_count: int = 0,
    projected_next_events: int = 0,
    projected_next_bytes: int = 0,
    expansion_ratio: float = 0.0,
    candidate_edge_sample: list[dict[str, Any]] | None = None,
    degraded_code: str | None = None,
    degraded_message: str | None = None,
    scan_count: int = 0,
    seek_count: int = 0,
    window_span_total: int = 0,
) -> EvidenceClosureOutcome:
    selected_events = [
        event_by_ref[ref_key]
        for ref_key in list(state.emitted_event_refs)
        if ref_key in event_by_ref
    ]
    selected_events = sorted(selected_events, key=evd_StableEventSortKey)
    selected_refs = [event.ref_key for event in selected_events]
    consumed_bytes = sum(_estimate_event_bytes(event) for event in selected_events)

    closure_mode = "exact"
    halt_reason = state.halt_reason
    if degraded_code is not None:
        closure_mode = "degraded"
        halt_reason = degraded_code
    elif halt_reason in _BUDGET_HALT_REASONS and frontier_count > 0:
        closure_mode = "bounded" if request.closure_policy.allow_bounded else "degraded"
    elif state.frontier_refs or state.missing_required_refs:
        closure_mode = "degraded"
        if not halt_reason or halt_reason == "FRONTIER_EMPTY":
            halt_reason = "SIDECAR_MISMATCH"

    return EvidenceClosureOutcome(
        closure_mode=closure_mode,
        halt_reason=halt_reason or "FRONTIER_EMPTY",
        round_id=len(state.rounds),
        seed_refs=list(state.seed_refs),
        closed_refs=list(state.closed_refs),
        selected_refs=selected_refs,
        missing_required_refs=list(state.missing_required_refs),
        rounds=list(state.rounds),
        frontier_rows=list(frontier_rows or []),
        frontier_count=int(frontier_count),
        truncated_frontier_count=int(truncated_frontier_count),
        projected_next_events=int(projected_next_events),
        projected_next_bytes=int(projected_next_bytes),
        expansion_ratio=float(expansion_ratio),
        sidecar_lookup_count=int(state.sidecar_lookup_count),
        scan_count=int(scan_count),
        seek_count=int(seek_count),
        window_span_total=int(window_span_total),
        candidate_edge_sample=list(candidate_edge_sample or []),
        consumed_events=len(selected_events),
        consumed_bytes=consumed_bytes,
        degraded_code=degraded_code,
        degraded_message=degraded_message,
        workset_edges=list(state.workset_edges),
    )


def execute_evidence_closure(
    bundle: RebuildBundle,
    request: EvidenceExportRequest,
    seed_result: SeedResolution | dict[str, Any],
    sidecar_rows: list[DependencySidecarEdge],
    *,
    trace_source: str | Path,
    ref_index_rows: list[dict[str, Any]] | None = None,
    emit_progress: Callable[[dict[str, Any]], None] | None = None,
    sidecar_candidate_selector: SidecarCandidateSelector | None = None,
) -> EvidenceClosureOutcome:
    event_by_ref = {event.ref_key: event for event in list(bundle.event_stream)}
    normalized_seed_result = SeedResolution.from_payload(seed_result)
    seed_refs = list(normalized_seed_result.seed_refs)
    scope_events = list(normalized_seed_result.scope_events)
    state = evd_InitState(
        seed_refs,
        scope_events,
        missing_required_refs=list(normalized_seed_result.missing_required_refs),
    )
    state.emitted_bytes = sum(
        _estimate_event_bytes(event_by_ref[ref_key])
        for ref_key in list(state.emitted_event_refs)
        if ref_key in event_by_ref
    )
    _emit_progress(
        emit_progress,
        substage="seed/materialize",
        status="started",
        round_id=0,
        frontier_count=len(state.frontier_refs),
        emitted_events=state.emitted_events_count,
        emitted_bytes=state.emitted_bytes,
    )
    if not state.frontier_refs:
        state.halt_reason = "SEED_EMPTY"
        _emit_progress(
            emit_progress,
            substage="seed/materialize",
            status="failed",
            round_id=0,
            frontier_count=0,
            emitted_events=state.emitted_events_count,
            emitted_bytes=state.emitted_bytes,
        )
        return evd_Finalize(
            state,
            request,
            event_by_ref=event_by_ref,
            degraded_code="SEED_EMPTY",
            degraded_message="no seed refs resolved",
        )
    _emit_progress(
        emit_progress,
        substage="seed/materialize",
        status="completed",
        round_id=0,
        frontier_count=len(state.frontier_refs),
        emitted_events=state.emitted_events_count,
        emitted_bytes=state.emitted_bytes,
    )

    total_scan_count = 0
    total_seek_count = 0
    total_window_span_total = 0
    prescanned_catalog: TraceChunkCatalog | None = None

    while state.frontier_refs:
        projected_round_id = len(state.rounds) + 1
        _emit_progress(
            emit_progress,
            substage="round/project",
            status="started",
            round_id=projected_round_id,
            frontier_count=len(state.frontier_refs),
            emitted_events=state.emitted_events_count,
            emitted_bytes=state.emitted_bytes,
        )
        projected = evd_ProjectRound(
            state,
            request,
            sidecar_rows,
            sidecar_candidate_selector=sidecar_candidate_selector,
        )
        projection: RoundProjection = projected["projection"]
        state.halt_reason = projection.halt_reason or "FRONTIER_EMPTY"
        if projected["error_code"] is not None:
            _emit_progress(
                emit_progress,
                substage="round/project",
                status="failed",
                round_id=projection.round_id,
                frontier_count=projection.delta_ref_count,
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            return evd_Finalize(
                state,
                request,
                event_by_ref=event_by_ref,
                frontier_rows=projection.candidate_frontier_refs,
                frontier_count=projection.delta_ref_count,
                truncated_frontier_count=int(projected["truncated_frontier_count"]),
                projected_next_events=projection.estimate_events,
                projected_next_bytes=projection.estimate_bytes,
                expansion_ratio=projection.expansion_ratio,
                candidate_edge_sample=list(projected["candidate_edge_sample"]),
                degraded_code=str(projected["error_code"]),
                degraded_message=str(projected["error_message"] or ""),
                scan_count=total_scan_count,
                seek_count=total_seek_count,
                window_span_total=total_window_span_total,
            )
        _emit_progress(
            emit_progress,
            substage="round/project",
            status="completed",
            round_id=projection.round_id,
            frontier_count=projection.delta_ref_count,
            emitted_events=state.emitted_events_count,
            emitted_bytes=state.emitted_bytes,
        )
        _emit_progress(
            emit_progress,
            substage="round/budget",
            status="started",
            round_id=projection.round_id,
            frontier_count=projection.delta_ref_count,
            emitted_events=state.emitted_events_count,
            emitted_bytes=state.emitted_bytes,
        )
        if projection.delta_ref_count == 0:
            _emit_progress(
                emit_progress,
                substage="round/budget",
                status="completed",
                round_id=projection.round_id,
                frontier_count=projection.delta_ref_count,
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            state.frontier_refs = []
            state.halt_reason = "FRONTIER_EMPTY"
            break
        if projection.would_halt:
            _emit_progress(
                emit_progress,
                substage="round/budget",
                status="rejected",
                round_id=projection.round_id,
                frontier_count=projection.delta_ref_count,
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            return evd_Finalize(
                state,
                request,
                event_by_ref=event_by_ref,
                frontier_rows=projection.candidate_frontier_refs,
                frontier_count=projection.delta_ref_count,
                truncated_frontier_count=int(projected["truncated_frontier_count"]),
                projected_next_events=projection.estimate_events,
                projected_next_bytes=projection.estimate_bytes,
                expansion_ratio=projection.expansion_ratio,
                candidate_edge_sample=list(projected["candidate_edge_sample"]),
                scan_count=total_scan_count,
                seek_count=total_seek_count,
                window_span_total=total_window_span_total,
            )
        _emit_progress(
            emit_progress,
            substage="round/budget",
            status="completed",
            round_id=projection.round_id,
            frontier_count=projection.delta_ref_count,
            emitted_events=state.emitted_events_count,
            emitted_bytes=state.emitted_bytes,
        )

        read_payload = ReadWindowResult(round_id=projection.round_id)
        window_delta_refs = list(projected["window_delta_refs"])
        if window_delta_refs:
            _emit_progress(
                emit_progress,
                substage="round/window_plan",
                status="started",
                round_id=projection.round_id,
                frontier_count=len(window_delta_refs),
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            window_plan = build_window_plan(
                list(projected["window_candidate_edges"]),
                window_delta_refs,
                bundle.segment_metas,
                ref_index_rows,
            )
            if list(window_plan.get("unplanned_refs") or []):
                _emit_progress(
                    emit_progress,
                    substage="round/window_plan",
                    status="failed",
                    round_id=projection.round_id,
                    frontier_count=len(window_delta_refs),
                    emitted_events=state.emitted_events_count,
                    emitted_bytes=state.emitted_bytes,
                )
                return evd_Finalize(
                    state,
                    request,
                    event_by_ref=event_by_ref,
                    frontier_rows=projection.candidate_frontier_refs,
                    frontier_count=projection.delta_ref_count,
                    truncated_frontier_count=int(projected["truncated_frontier_count"]),
                    projected_next_events=projection.estimate_events,
                    projected_next_bytes=projection.estimate_bytes,
                    expansion_ratio=projection.expansion_ratio,
                    candidate_edge_sample=list(projected["candidate_edge_sample"]),
                    degraded_code="SIDECAR_MISMATCH",
                    degraded_message=f"window plan missing refs: {', '.join(window_plan['unplanned_refs'])}",
                    scan_count=total_scan_count,
                    seek_count=total_seek_count,
                    window_span_total=total_window_span_total,
                )
            _emit_progress(
                emit_progress,
                substage="round/window_plan",
                status="completed",
                round_id=projection.round_id,
                frontier_count=len(window_delta_refs),
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            _emit_progress(
                emit_progress,
                substage="round/read",
                status="started",
                round_id=projection.round_id,
                frontier_count=len(window_delta_refs),
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            if prescanned_catalog is None:
                catalog_result = build_trace_chunk_catalog(trace_source)
                if not catalog_result.ok:
                    read_result = Result(code=catalog_result.code, message=catalog_result.message)
                else:
                    prescanned_catalog = catalog_result.data
                    read_result = read_window_plan(
                        trace_source,
                        window_plan,
                        window_delta_refs,
                        prescanned_catalog=prescanned_catalog,
                    )
            else:
                read_result = read_window_plan(
                    trace_source,
                    window_plan,
                    window_delta_refs,
                    prescanned_catalog=prescanned_catalog,
                )
            if not read_result.ok:
                failed_target_refs = _dedupe_refs([str(ref_key) for ref_key in list(window_delta_refs)])
                failed_target_ref_count = int(len(failed_target_refs))
                failed_data = dict(read_result.data or {})
                failed_telemetry = dict(failed_data.get("telemetry") or {})
                failed_telemetry.update(
                    {
                        "target_refs": list(failed_target_refs),
                        "target_ref_count": int(failed_target_ref_count),
                        "matched_ref_count": int(failed_telemetry.get("matched_ref_count", 0)),
                        "missed_ref_count": int(failed_telemetry.get("missed_ref_count", failed_target_ref_count)),
                        "window_hit_rate": float(
                            failed_telemetry.get(
                                "window_hit_rate",
                                1.0 if failed_target_ref_count == 0 else 0.0,
                            )
                        ),
                        "planned_span_count": int(len(list(window_plan.get("spans") or []))),
                        "planned_seek_count": int(window_plan.get("planned_seek_count", 0)),
                        "planned_span_total": int(window_plan.get("planned_span_total", 0)),
                    }
                )
                failed_read_payload = ReadWindowResult(
                    round_id=int(projection.round_id),
                    matched_events=[],
                    matched_refs=[str(ref_key) for ref_key in list(failed_data.get("matched_refs") or [])],
                    missed_refs=[str(ref_key) for ref_key in list(failed_data.get("missed_refs") or failed_target_refs)],
                    bytes_read=int(failed_data.get("bytes_read", 0)),
                    scan_count=int(failed_data.get("scan_count", 0)),
                    seek_count=int(failed_data.get("seek_count", 0)),
                    span_total=int(failed_data.get("span_total", 0)),
                    window_span_total=int(failed_data.get("window_span_total", failed_data.get("span_total", 0))),
                    window_count=int(failed_data.get("window_count", 0)),
                    corrupt_segments=list(failed_data.get("corrupt_segments") or []),
                    io_guard_triggered=bool(failed_data.get("io_guard_triggered", False)),
                    telemetry=failed_telemetry,
                )
                state.rounds.append(
                    {
                        "projection": serialize(projection),
                        "read_result": _build_round_read_summary(
                            projected,
                            failed_read_payload,
                            read_ok=False,
                            read_error_code=read_result.code,
                            read_error_message=read_result.message,
                        ),
                    }
                )
                _emit_progress(
                    emit_progress,
                    substage="round/read",
                    status="failed",
                    round_id=projection.round_id,
                    frontier_count=len(window_delta_refs),
                    emitted_events=state.emitted_events_count,
                    emitted_bytes=state.emitted_bytes,
                )
                return evd_Finalize(
                    state,
                    request,
                    event_by_ref=event_by_ref,
                    frontier_rows=projection.candidate_frontier_refs,
                    frontier_count=projection.delta_ref_count,
                    truncated_frontier_count=int(projected["truncated_frontier_count"]),
                    projected_next_events=projection.estimate_events,
                    projected_next_bytes=projection.estimate_bytes,
                    expansion_ratio=projection.expansion_ratio,
                    candidate_edge_sample=list(projected["candidate_edge_sample"]),
                    degraded_code=read_result.code,
                    degraded_message=read_result.message,
                    scan_count=total_scan_count,
                    seek_count=total_seek_count,
                    window_span_total=total_window_span_total,
                )
            read_payload = ReadWindowResult.from_payload(read_result.data)
            total_scan_count += int(read_payload.scan_count)
            total_seek_count += int(read_payload.seek_count)
            total_window_span_total += int(read_payload.window_span_total)
            _emit_progress(
                emit_progress,
                substage="round/read",
                status="completed",
                round_id=projection.round_id,
                frontier_count=len(window_delta_refs),
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
        else:
            _emit_progress(
                emit_progress,
                substage="round/window_plan",
                status="started",
                round_id=projection.round_id,
                frontier_count=0,
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            _emit_progress(
                emit_progress,
                substage="round/window_plan",
                status="completed",
                round_id=projection.round_id,
                frontier_count=0,
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            _emit_progress(
                emit_progress,
                substage="round/read",
                status="started",
                round_id=projection.round_id,
                frontier_count=0,
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )
            _emit_progress(
                emit_progress,
                substage="round/read",
                status="completed",
                round_id=projection.round_id,
                frontier_count=0,
                emitted_events=state.emitted_events_count,
                emitted_bytes=state.emitted_bytes,
            )

        _emit_progress(
            emit_progress,
            substage="round/merge",
            status="started",
            round_id=projection.round_id,
            frontier_count=projection.delta_ref_count,
            emitted_events=state.emitted_events_count,
            emitted_bytes=state.emitted_bytes,
        )
        evd_ApplyRoundRead(state, projected, read_payload, event_by_ref=event_by_ref)
        _emit_progress(
            emit_progress,
            substage="round/merge",
            status="completed",
            round_id=projection.round_id,
            frontier_count=len(state.frontier_refs),
            emitted_events=state.emitted_events_count,
            emitted_bytes=state.emitted_bytes,
        )

    return evd_Finalize(
        state,
        request,
        event_by_ref=event_by_ref,
        scan_count=total_scan_count,
        seek_count=total_seek_count,
        window_span_total=total_window_span_total,
    )

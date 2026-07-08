from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RUNTIME_COST_GRAPH_VERSION = "runtime-cost-graph-v1"
RUNTIME_COST_GRAPH_NODE_ORDER = (
    "decode",
    "verify",
    "align",
    "rebuild",
    "index_full",
    "index_minimal",
    "index_deferred",
    "sidecar_validate",
    "sidecar_index_build",
    "sidecar_index_open",
    "closure_project",
    "window_read",
    "package_write",
    "proof_validate",
)
RUNTIME_COST_GRAPH_EDGE_TEMPLATES = (
    ("decode_to_verify", "decode", "verify", "normal", None),
    ("verify_to_align", "verify", "align", "normal", None),
    ("align_to_rebuild", "align", "rebuild", "normal", None),
    ("rebuild_to_index_full", "rebuild", "index_full", "normal", "index_build_mode=full"),
    ("rebuild_to_index_minimal", "rebuild", "index_minimal", "normal", "index_build_mode=minimal"),
    ("rebuild_to_index_deferred", "rebuild", "index_deferred", "deferred", "index_build_mode=deferred"),
    ("index_full_to_sidecar_validate", "index_full", "sidecar_validate", "normal", None),
    ("index_minimal_to_sidecar_validate", "index_minimal", "sidecar_validate", "normal", None),
    ("index_deferred_to_sidecar_validate", "index_deferred", "sidecar_validate", "deferred", None),
    ("sidecar_validate_to_index_build", "sidecar_validate", "sidecar_index_build", "normal", "build_index"),
    ("sidecar_validate_to_index_open", "sidecar_validate", "sidecar_index_open", "cache_hit", "open_index"),
    ("index_build_to_closure", "sidecar_index_build", "closure_project", "normal", None),
    ("index_open_to_closure", "sidecar_index_open", "closure_project", "cache_hit", None),
    ("closure_to_window", "closure_project", "window_read", "normal", None),
    ("window_to_package", "window_read", "package_write", "normal", None),
    ("package_to_proof", "package_write", "proof_validate", "normal", None),
)
_NODE_ORDINALS = {node_id: index for index, node_id in enumerate(RUNTIME_COST_GRAPH_NODE_ORDER)}
RUNTIME_ACTION_NODE_BINDINGS = {
    "baseline_full_load": ("decode", "verify", "align", "rebuild", "index_full"),
    "cold_preview": ("decode", "verify", "align", "rebuild", "index_minimal"),
    "sidecar_index_prebuild": ("sidecar_validate", "sidecar_index_build"),
    "sidecar_index_reuse": ("sidecar_validate", "sidecar_index_open"),
    "streaming_package_write": ("package_write",),
    "deferred_index_build": ("rebuild", "index_deferred"),
    "abstain": (),
}

_NODE_LABELS = {
    "decode": "decode",
    "verify": "verify",
    "align": "align",
    "rebuild": "rebuild",
    "index_full": "index_full",
    "index_minimal": "index_minimal",
    "index_deferred": "index_deferred",
    "sidecar_validate": "sidecar_validate",
    "sidecar_index_build": "sidecar_index_build",
    "sidecar_index_open": "sidecar_index_open",
    "closure_project": "closure_project",
    "window_read": "window_read",
    "package_write": "package_write",
    "proof_validate": "proof_validate",
}
_SHARED_NODES = {"decode", "verify", "align", "rebuild", "index_full", "index_minimal", "index_deferred"}
_TERMINAL_PROGRESS_STATUSES = {"completed", "failed", "rejected", "fallback", "missing"}
_EXPORT_NODE_HINTS = {
    "sidecar/preflight": "sidecar_validate",
    "sidecar/validate": "sidecar_validate",
    "sidecar/ticket": "sidecar_index_open",
    "seed/resolve": "closure_project",
    "seed/materialize": "closure_project",
    "round/project": "closure_project",
    "round/budget": "closure_project",
    "round/merge": "closure_project",
    "round/window_plan": "window_read",
    "round/read": "window_read",
    "write/event": "package_write",
    "write/rebuild": "package_write",
    "write/result": "package_write",
    "write/control": "package_write",
    "write/meta": "package_write",
    "write/manifest": "package_write",
    "write/package": "package_write",
    "finalize/proof": "proof_validate",
}
_REUSE_CACHE_KEY_FIELDS = (
    "snapshot_id",
    "trace_checksum",
    "dictionary_checksum",
    "sidecar_checksum",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ms_from_seconds(value: Any) -> float | None:
    seconds = _optional_float(value)
    if seconds is None:
        return None
    return round(seconds * 1000.0, 6)


def _ns_from_counter(value: Any) -> int | None:
    seconds = _optional_float(value)
    if seconds is None:
        return None
    return int(seconds * 1_000_000_000)


def _normalized_strings(values: Iterable[Any] | None) -> list[str]:
    normalized: list[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if text:
            normalized.append(text)
    return list(dict.fromkeys(normalized))


def _safe_ref(value: Any, *, root: str | Path | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        path = Path(text)
    except (TypeError, ValueError):
        return text
    if root is not None:
        try:
            return str(path.resolve().relative_to(Path(root).resolve()))
        except Exception:
            pass
    if path.parts:
        return str(Path(path.name))
    return text


def _allowlist_action_kinds() -> list[str]:
    try:
        from .runtime_optimization_gate import RUNTIME_ACTION_GATE_ALLOWLIST
    except Exception:
        return sorted(RUNTIME_ACTION_NODE_BINDINGS)
    return sorted(str(item) for item in RUNTIME_ACTION_GATE_ALLOWLIST)


def graph_node_ids_for_action(action_kind: str) -> tuple[str, ...]:
    return tuple(RUNTIME_ACTION_NODE_BINDINGS.get(str(action_kind or "").strip(), ()))


def _default_node(node_id: str, *, graph_path_type: str) -> dict[str, Any]:
    node_path_type = "shared" if node_id in _SHARED_NODES else graph_path_type
    return {
        "node_id": node_id,
        "stage_name": _NODE_LABELS[node_id],
        "ordinal": _NODE_ORDINALS[node_id],
        "path_type": node_path_type,
        "status": "planned",
        "started_at_ns": None,
        "ended_at_ns": None,
        "duration_ms": None,
        "predicted_ms": None,
        "observed_ms": None,
        "prediction_source": "none",
        "preconditions": {},
        "cache_key": None,
        "fallback_edge": None,
        "telemetry_refs": [],
        "artifact_refs": [],
        "error_reason": None,
        "reject_reason": None,
    }


def _default_graph(*, graph_id: str, scenario_id: str | None, path_type: str) -> dict[str, Any]:
    return {
        "graph_id": graph_id,
        "scenario_id": None if scenario_id is None else str(scenario_id),
        "path_type": path_type,
        "node_order": list(RUNTIME_COST_GRAPH_NODE_ORDER),
        "nodes": [_default_node(node_id, graph_path_type=path_type) for node_id in RUNTIME_COST_GRAPH_NODE_ORDER],
        "edges": [
            {
                "edge_id": edge_id,
                "from_node": from_node,
                "to_node": to_node,
                "edge_kind": edge_kind,
                "condition": condition,
            }
            for edge_id, from_node, to_node, edge_kind, condition in RUNTIME_COST_GRAPH_EDGE_TEMPLATES
        ],
        "action_bindings": [],
        "stage_coverage": {
            "covered_nodes": 0,
            "expected_nodes": len(RUNTIME_COST_GRAPH_NODE_ORDER),
            "coverage_ratio": 0.0,
            "observed_nodes": 0,
        },
        "predicted_vs_observed": [],
        "proof_boundary": {
            "cost_graph_fields_in_proof_digest": 0,
            "advisor_fields_in_proof_digest": 0,
        },
        "metadata": {},
    }


def _graph_nodes(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node["node_id"]): node for node in list(graph.get("nodes") or [])}


def _set_node_status(node: dict[str, Any], status: str) -> None:
    current = str(node.get("status") or "skipped")
    priority = {
        "failed": 6,
        "degraded": 5,
        "rejected": 4,
        "running": 3,
        "completed": 2,
        "skipped": 1,
        "planned": 0,
    }
    if priority.get(status, 0) >= priority.get(current, 0):
        node["status"] = status


def _apply_node_measurement(
    node: dict[str, Any],
    *,
    status: str,
    observed_ms: float | None = None,
    started_at_ns: int | None = None,
    ended_at_ns: int | None = None,
    predicted_ms: float | None = None,
    prediction_source: str | None = None,
    preconditions: dict[str, Any] | None = None,
    cache_key: str | None = None,
    fallback_edge: str | None = None,
    telemetry_refs: Iterable[Any] | None = None,
    artifact_refs: Iterable[Any] | None = None,
    error_reason: str | None = None,
    reject_reason: str | None = None,
) -> None:
    _set_node_status(node, status)
    if observed_ms is not None:
        node["observed_ms"] = observed_ms if node["observed_ms"] is None else round(float(node["observed_ms"]) + observed_ms, 6)
        node["duration_ms"] = node["observed_ms"]
    if started_at_ns is not None:
        node["started_at_ns"] = started_at_ns if node["started_at_ns"] is None else min(int(node["started_at_ns"]), started_at_ns)
    if ended_at_ns is not None:
        node["ended_at_ns"] = ended_at_ns if node["ended_at_ns"] is None else max(int(node["ended_at_ns"]), ended_at_ns)
    if predicted_ms is not None:
        node["predicted_ms"] = predicted_ms
    if prediction_source is not None:
        node["prediction_source"] = prediction_source
    if preconditions:
        node["preconditions"] = {**dict(node.get("preconditions") or {}), **dict(preconditions)}
    if cache_key:
        node["cache_key"] = str(cache_key)
    if fallback_edge:
        node["fallback_edge"] = str(fallback_edge)
    if telemetry_refs:
        node["telemetry_refs"] = _normalized_strings(list(node.get("telemetry_refs") or []) + list(telemetry_refs))
    if artifact_refs:
        node["artifact_refs"] = _normalized_strings(list(node.get("artifact_refs") or []) + list(artifact_refs))
    if error_reason:
        node["error_reason"] = str(error_reason)
    if reject_reason:
        node["reject_reason"] = str(reject_reason)


def _finalize_graph(
    graph: dict[str, Any],
    *,
    action_kinds: Iterable[Any] | None = None,
    action_context: dict[str, Any] | None = None,
    action_gate_result: dict[str, Any] | None = None,
    proof_digest: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = list(graph.get("nodes") or [])
    selected_actions = set(_normalized_strings(action_kinds))
    gate_result = dict(action_gate_result or {})
    gate_execution_plan = _normalized_strings(gate_result.get("execution_plan"))
    context = dict(action_context or {})
    cache_key = build_runtime_cost_cache_key(context)
    action_bindings: list[dict[str, Any]] = []
    for action_kind in _allowlist_action_kinds():
        node_ids = list(graph_node_ids_for_action(action_kind))
        available = True
        unavailable_reason: str | None = None
        if action_kind == "sidecar_index_reuse" and not cache_key:
            available = False
            missing = [field for field in _REUSE_CACHE_KEY_FIELDS if not str(context.get(field) or "").strip()]
            unavailable_reason = f"missing_cache_key:{','.join(missing)}" if missing else "missing_cache_key"
        action_bindings.append(
            {
                "action_kind": action_kind,
                "node_ids": node_ids,
                "selected": action_kind in selected_actions,
                "available": available,
                "unavailable_reason": unavailable_reason,
                "gate_execution_plan": gate_execution_plan,
            }
        )
    predicted_vs_observed = []
    covered_nodes = 0
    observed_nodes = 0
    for node in nodes:
        status = str(node.get("status") or "skipped")
        if status != "planned":
            covered_nodes += 1
        if node.get("observed_ms") is not None:
            observed_nodes += 1
        predicted_vs_observed.append(
            {
                "node_id": str(node["node_id"]),
                "predicted_ms": node.get("predicted_ms"),
                "observed_ms": node.get("observed_ms"),
                "prediction_source": str(node.get("prediction_source") or "none"),
                "status": status,
            }
        )
    graph["action_bindings"] = action_bindings
    graph["stage_coverage"] = {
        "covered_nodes": covered_nodes,
        "expected_nodes": len(RUNTIME_COST_GRAPH_NODE_ORDER),
        "coverage_ratio": round(float(covered_nodes) / float(len(RUNTIME_COST_GRAPH_NODE_ORDER)), 6),
        "observed_nodes": observed_nodes,
    }
    graph["predicted_vs_observed"] = predicted_vs_observed
    graph["proof_boundary"] = proof_boundary_summary(proof_digest or {})
    graph["metadata"] = {**dict(graph.get("metadata") or {}), **dict(metadata or {})}
    return graph


def proof_boundary_summary(proof_digest: dict[str, Any] | None) -> dict[str, int]:
    payload = dict(proof_digest or {})
    cost_graph_fields = 0
    advisor_fields = 0
    for key in payload:
        lowered = str(key).lower()
        if lowered.startswith(("runtime_cost_graph", "cost_graph", "graph_node", "graph_path")):
            cost_graph_fields += 1
        if lowered.startswith(("advisor", "action_binding", "action_graph")):
            advisor_fields += 1
    return {
        "cost_graph_fields_in_proof_digest": cost_graph_fields,
        "advisor_fields_in_proof_digest": advisor_fields,
    }


def build_runtime_cost_cache_key(context: dict[str, Any] | None) -> str | None:
    payload = dict(context or {})
    parts = [str(payload.get(field) or "").strip() for field in _REUSE_CACHE_KEY_FIELDS]
    if any(not part for part in parts):
        return None
    return "|".join(parts)


def _graph_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        raise ValueError("runtime cost graph payload is empty")
    graphs = list(dict(payload).get("graphs") or [])
    if not graphs:
        raise ValueError("runtime cost graph payload has no graphs")
    return deepcopy(dict(graphs[0]))


def build_pipeline_runtime_cost_graph(
    *,
    dataset_id: str | None,
    stage_timings: dict[str, Any] | None,
    index_build_mode: str,
    materialize_event_stream: bool,
    path_type: str = "product",
) -> dict[str, Any]:
    graph = _default_graph(
        graph_id=f"pipeline:{dataset_id or 'dataset'}",
        scenario_id=dataset_id,
        path_type="formal" if str(path_type).strip() == "formal" else "product",
    )
    nodes = _graph_nodes(graph)
    timings = dict(stage_timings or {})
    mode = str(index_build_mode or "full").strip()
    index_node_id = f"index_{mode}" if f"index_{mode}" in RUNTIME_COST_GRAPH_NODE_ORDER else "index_full"
    _apply_node_measurement(
        nodes["decode"],
        status="completed",
        observed_ms=_ms_from_seconds(timings.get("_decode_chunk_seconds") or timings.get("TraceDecodeSession.feed_seconds")),
        telemetry_refs=["_decode_chunk_seconds", "TraceDecodeSession.feed_seconds"],
    )
    _apply_node_measurement(
        nodes["verify"],
        status="completed",
        observed_ms=_ms_from_seconds(timings.get("prs_Verify_seconds")),
        preconditions={"materialize_event_stream": bool(materialize_event_stream)},
        telemetry_refs=["prs_Verify_seconds"],
    )
    _apply_node_measurement(
        nodes["align"],
        status="completed",
        observed_ms=_ms_from_seconds(timings.get("align_events_seconds")),
        telemetry_refs=["align_events_seconds"],
    )
    _apply_node_measurement(
        nodes["rebuild"],
        status="completed",
        observed_ms=_ms_from_seconds(timings.get("rb_Rebuild_seconds")),
        preconditions={"materialize_event_stream": bool(materialize_event_stream)},
        telemetry_refs=["rb_Rebuild_seconds"],
    )
    _apply_node_measurement(
        nodes[index_node_id],
        status="completed",
        observed_ms=_ms_from_seconds(timings.get("idx_Build_seconds")),
        preconditions={"index_build_mode": mode},
        telemetry_refs=["idx_Build_seconds"],
    )
    for node_id in ("index_full", "index_minimal", "index_deferred"):
        if node_id == index_node_id:
            continue
        _apply_node_measurement(
            nodes[node_id],
            status="skipped",
            preconditions={"index_build_mode": mode},
            reject_reason=f"index_build_mode={mode}",
        )
    return {
        "graph_version": RUNTIME_COST_GRAPH_VERSION,
        "generated_at": _iso_now(),
        "graphs": [
            _finalize_graph(
                graph,
                metadata={
                    "dataset_id": dataset_id,
                    "index_build_mode": mode,
                    "materialize_event_stream": bool(materialize_event_stream),
                    "graph_scope": "pipeline",
                },
            )
        ],
    }


def annotate_evidence_export_progress(payload: dict[str, Any], *, path_type: str = "product") -> dict[str, Any]:
    annotated = dict(payload)
    substage = str(annotated.get("substage") or "").strip()
    node_id = str(annotated.get("graph_node_id") or "").strip()
    if not node_id:
        node_id = _EXPORT_NODE_HINTS.get(substage, "")
    if substage == "sidecar/index":
        if bool(annotated.get("sidecar_index_rebuilt")):
            node_id = "sidecar_index_build"
        elif substage and annotated.get("status") in {"completed", "fallback", "failed"}:
            node_id = "sidecar_index_open"
    if node_id:
        annotated["graph_node_id"] = node_id
        annotated["graph_path_type"] = "formal" if str(path_type).strip() == "formal" else "product"
    return annotated


def build_benchmark_runtime_cost_graph(
    *,
    base_graph_payload: dict[str, Any],
    scenario_id: str,
    progress: list[dict[str, Any]],
    row: dict[str, Any],
    proof_digest: dict[str, Any],
    advisor_report: dict[str, Any] | None = None,
    action_context: dict[str, Any] | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    graph = _graph_from_payload(base_graph_payload)
    graph["graph_id"] = f"benchmark:{scenario_id}"
    graph["scenario_id"] = str(scenario_id)
    graph["path_type"] = "product"
    nodes = _graph_nodes(graph)
    telemetry_record = dict((advisor_report or {}).get("telemetry_record") or {})
    if not telemetry_record:
        telemetry_updates = list(row.get("telemetry_records") or [])
        if telemetry_updates:
            telemetry_record = dict(telemetry_updates[-1].get("metrics") or {})
    sidecar_selector_mode = str(proof_digest.get("sidecar_selector_mode") or "")
    index_rebuilt = bool(telemetry_record.get("index_rebuilt"))
    index_reused = bool(telemetry_record.get("index_reused"))
    ticket_fast_path = bool(dict(row.get("summary_metrics") or {}).get("sidecar_ticket_fast_path"))
    cache_key = build_runtime_cost_cache_key(action_context)

    sidecar_validate_ms = _ms_from_seconds(row.get("sidecar_validate_seconds"))
    _apply_node_measurement(
        nodes["sidecar_validate"],
        status="completed" if sidecar_validate_ms is not None else "planned",
        observed_ms=sidecar_validate_ms,
        telemetry_refs=["sidecar/validate", "sidecar/preflight"],
        artifact_refs=[
            _safe_ref(dict(row.get("artifact_refs") or {}).get("sidecar_manifest"), root=output_root),
            _safe_ref(dict(row.get("artifact_refs") or {}).get("sidecar_source"), root=output_root),
        ],
        preconditions={
            "ticket_fast_path_enabled": ticket_fast_path,
            "selector_mode": sidecar_selector_mode or None,
        },
        cache_key=cache_key,
    )
    if index_rebuilt:
        _apply_node_measurement(
            nodes["sidecar_index_build"],
            status="completed",
            observed_ms=_ms_from_seconds(row.get("index_build_open_seconds")),
            telemetry_refs=["sidecar/index"],
            artifact_refs=[
                _safe_ref(dict(row.get("artifact_refs") or {}).get("sidecar_index"), root=output_root),
                _safe_ref(dict(row.get("artifact_refs") or {}).get("sidecar_index_ticket"), root=output_root),
            ],
            preconditions={"selector_mode": sidecar_selector_mode or None},
            cache_key=cache_key,
        )
        _apply_node_measurement(
            nodes["sidecar_index_open"],
            status="skipped",
            preconditions={"index_rebuilt": True},
            cache_key=cache_key,
            reject_reason="index_rebuilt",
        )
    elif index_reused or ticket_fast_path:
        _apply_node_measurement(
            nodes["sidecar_index_open"],
            status="completed",
            observed_ms=_ms_from_seconds(row.get("index_build_open_seconds")),
            telemetry_refs=["sidecar/ticket", "sidecar/index"],
            artifact_refs=[
                _safe_ref(dict(row.get("artifact_refs") or {}).get("sidecar_index"), root=output_root),
                _safe_ref(dict(row.get("artifact_refs") or {}).get("sidecar_index_ticket"), root=output_root),
            ],
            preconditions={"ticket_fast_path": ticket_fast_path, "selector_mode": sidecar_selector_mode or None},
            cache_key=cache_key,
        )
        _apply_node_measurement(
            nodes["sidecar_index_build"],
            status="skipped",
            preconditions={"ticket_fast_path": ticket_fast_path},
            cache_key=cache_key,
            reject_reason="ticket_fast_path_or_reuse",
        )
    else:
        fallback_edge = "sidecar_validate_to_window_read:stream_scan" if sidecar_selector_mode == "stream_scan" else None
        _apply_node_measurement(
            nodes["sidecar_index_build"],
            status="skipped",
            preconditions={"selector_mode": sidecar_selector_mode or None},
            cache_key=cache_key,
            fallback_edge=fallback_edge,
            reject_reason="index_not_built",
        )
        _apply_node_measurement(
            nodes["sidecar_index_open"],
            status="skipped",
            preconditions={"selector_mode": sidecar_selector_mode or None},
            cache_key=cache_key,
            fallback_edge=fallback_edge,
            reject_reason="index_not_opened",
        )

    progress_windows: dict[tuple[str, str, int | None], float] = {}
    for item in list(progress or []):
        annotated = annotate_evidence_export_progress(item)
        node_id = str(annotated.get("graph_node_id") or "").strip()
        if not node_id or node_id not in nodes:
            continue
        node = nodes[node_id]
        observed_at = _optional_float(annotated.get("observed_at"))
        observed_at_ns = _ns_from_counter(observed_at)
        substage = str(annotated.get("substage") or node_id)
        round_id = _optional_int(annotated.get("round_id"))
        key = (node_id, substage, round_id)
        status = str(annotated.get("status") or "").strip()
        refs = []
        for field in (
            "sidecar_index_ticket_path",
            "sidecar_index_path",
            "path",
            "artifact_path",
        ):
            ref = _safe_ref(annotated.get(field), root=output_root)
            if ref:
                refs.append(ref)
        error_reason = (
            str(annotated.get("sidecar_index_error_message") or "").strip()
            or str(annotated.get("error_message") or "").strip()
            or None
        )
        reject_reason = str(annotated.get("rejected_reason") or "").strip() or None
        if status == "started":
            if observed_at is not None:
                progress_windows[key] = observed_at
            _apply_node_measurement(
                node,
                status="running",
                started_at_ns=observed_at_ns,
                telemetry_refs=[substage],
                artifact_refs=refs,
                error_reason=error_reason,
                reject_reason=reject_reason,
            )
            continue
        duration_ms = None
        started = progress_windows.pop(key, None)
        if observed_at is not None and started is not None:
            duration_ms = round(max(0.0, observed_at - started) * 1000.0, 6)
        mapped_status = {
            "completed": "completed",
            "fallback": "degraded",
            "failed": "failed",
            "rejected": "rejected",
            "missing": "skipped",
        }.get(status, "skipped")
        _apply_node_measurement(
            node,
            status=mapped_status,
            observed_ms=duration_ms,
            started_at_ns=_ns_from_counter(started),
            ended_at_ns=observed_at_ns,
            telemetry_refs=[substage],
            artifact_refs=refs,
            error_reason=error_reason,
            reject_reason=reject_reason,
            fallback_edge=(
                "sidecar_validate_to_window_read:stream_scan"
                if node_id in {"sidecar_index_build", "sidecar_index_open"} and status == "fallback"
                else None
            ),
        )

    _apply_node_measurement(
        nodes["package_write"],
        status="completed" if _ms_from_seconds(row.get("package_write_seconds")) is not None else "planned",
        observed_ms=_ms_from_seconds(row.get("package_write_seconds")),
        artifact_refs=[
            _safe_ref(dict(row.get("artifact_refs") or {}).get("package_path"), root=output_root),
            _safe_ref(dict(row.get("artifact_refs") or {}).get("manifest"), root=output_root),
        ],
    )
    proof_status = "completed" if str(row.get("status") or "").startswith("completed") else "failed"
    _apply_node_measurement(
        nodes["proof_validate"],
        status=proof_status,
        artifact_refs=[_safe_ref(dict(row.get("artifact_refs") or {}).get("proof_digest"), root=output_root)],
        error_reason=None if proof_status == "completed" else str(dict(row.get("summary_metrics") or {}).get("error_message") or "") or None,
    )
    selected_actions = [
        str(action.get("action_kind") or "")
        for action in list(dict((advisor_report or {}).get("decision") or {}).get("proposed_actions") or [])
        if str(action.get("action_kind") or "").strip()
    ]
    return {
        "graph_version": RUNTIME_COST_GRAPH_VERSION,
        "generated_at": _iso_now(),
        "graphs": [
            _finalize_graph(
                graph,
                action_kinds=selected_actions,
                action_context=action_context,
                action_gate_result=dict((advisor_report or {}).get("gate_result") or {}),
                proof_digest=proof_digest,
                metadata={
                    "graph_scope": "benchmark_scenario",
                    "scenario_status": row.get("status"),
                    "metric_scope": dict(row.get("summary_metrics") or {}).get("metric_scope"),
                    "formal_product_separation": True,
                },
            )
        ],
    }


def build_formal_runtime_cost_graph(
    *,
    scenario_id: str,
    active_nodes: Iterable[str] | None = None,
    observed_seconds: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph = _default_graph(
        graph_id=f"formal:{scenario_id}",
        scenario_id=scenario_id,
        path_type="formal",
    )
    nodes = _graph_nodes(graph)
    for node_id in _normalized_strings(active_nodes):
        if node_id not in nodes:
            continue
        _apply_node_measurement(
            nodes[node_id],
            status="completed",
            observed_ms=_ms_from_seconds(dict(observed_seconds or {}).get(node_id)),
        )
    return {
        "graph_version": RUNTIME_COST_GRAPH_VERSION,
        "generated_at": _iso_now(),
        "graphs": [_finalize_graph(graph, metadata={**dict(metadata or {}), "graph_scope": "formal"})],
    }


def merge_runtime_cost_graphs(graph_payloads: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    graphs: list[dict[str, Any]] = []
    for payload in list(graph_payloads or []):
        graphs.extend(list(dict(payload).get("graphs") or []))
    return {
        "graph_version": RUNTIME_COST_GRAPH_VERSION,
        "generated_at": _iso_now(),
        "graphs": graphs,
    }

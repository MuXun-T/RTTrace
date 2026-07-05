from __future__ import annotations

from typing import Any

from parser.models import Alert, Diagnosis, EvidenceRef, RebuildBundle
from parser.result import Result, err_result, ok_result

from .evidence_models import EvidenceExportRequest, SeedResolution


def _ref_key_from_payload(value: Any) -> str | None:
    if isinstance(value, EvidenceRef):
        return str(value.ref_key).strip() or None
    if isinstance(value, dict):
        ref_key = value.get("ref_key")
        if ref_key is None:
            return None
        text = str(ref_key).strip()
        return text or None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe_refs(rows: list[str]) -> list[str]:
    normalized_rows = sorted(str(row or "").strip() for row in rows if str(row or "").strip())
    normalized: list[str] = []
    seen: set[str] = set()
    for ref_key in normalized_rows:
        if ref_key in seen:
            continue
        seen.add(ref_key)
        normalized.append(ref_key)
    return normalized


def _refs_from_alert(alert: Alert) -> list[str]:
    return _dedupe_refs([_ref_key_from_payload(item) or "" for item in list(alert.evidence_refs or [])])


def _refs_from_diagnosis(diagnosis: Diagnosis | dict[str, Any]) -> list[str]:
    evidence_refs = diagnosis.evidence_refs if isinstance(diagnosis, Diagnosis) else list((diagnosis or {}).get("evidence_refs") or [])
    return _dedupe_refs([_ref_key_from_payload(item) or "" for item in evidence_refs])


def _normalize_ref_index_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized = [
        {
            "ref_key": str(row.get("ref_key") or "").strip(),
            "timestamp_aligned": float(row.get("timestamp_aligned", row.get("timestamp_raw", 0.0))),
        }
        for row in list(rows or [])
        if str(row.get("ref_key") or "").strip()
    ]
    normalized.sort(key=lambda row: (float(row["timestamp_aligned"]), str(row["ref_key"])))
    return normalized


def _resolve_time_window(
    source_kind: str,
    payload: Any,
    request: EvidenceExportRequest,
    context: dict[str, Any],
) -> tuple[float, float] | None:
    if source_kind == "analysis_context":
        return request.time_window
    raw_window = payload.get("time_window") if isinstance(payload, dict) else payload
    if isinstance(raw_window, (list, tuple)) and len(raw_window) >= 2:
        return (float(raw_window[0]), float(raw_window[1]))
    if request.time_window is not None:
        return request.time_window
    context_window = context.get("time_window")
    if isinstance(context_window, (list, tuple)) and len(context_window) >= 2:
        return (float(context_window[0]), float(context_window[1]))
    return None


def _scope_events_from_window(
    time_window: tuple[float, float] | None,
    *,
    ref_index_rows: list[dict[str, Any]],
    bundle: RebuildBundle,
) -> Result[list[str]]:
    if time_window is None:
        return ok_result([])
    if not ref_index_rows:
        if bundle.index_bundle is not None and list(bundle.index_bundle.time_index or []):
            return err_result(
                "SEED_EMPTY",
                "unable to legally materialize time_window seeds via bundle.index_bundle.time_index: coarse buckets lack ref_key; provide ref_index_rows",
            )
        return err_result(
            "SEED_EMPTY",
            "unable to legally materialize time_window seeds: missing ref_index_rows",
        )
    t_begin, t_end = float(time_window[0]), float(time_window[1])
    scope_events = [
        str(row["ref_key"])
        for row in ref_index_rows
        if float(t_begin) <= float(row["timestamp_aligned"]) <= float(t_end)
    ]
    return ok_result(_dedupe_refs(scope_events))


def resolve_seed_refs(
    request: EvidenceExportRequest,
    context: dict[str, Any],
    bundle: RebuildBundle,
    alerts: list[Alert],
    diagnoses: list[Diagnosis] | list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    ref_index_rows: list[dict[str, Any]] | None = None,
) -> Result[SeedResolution]:
    source_kind = str(request.seed_spec.source_kind or "analysis_context").strip().lower()
    payload = request.seed_spec.source_payload
    normalized_ref_index_rows = _normalize_ref_index_rows(ref_index_rows)
    indexed_event_ref_set = {str(row["ref_key"]) for row in normalized_ref_index_rows}
    event_ref_set: set[str] | None = None

    def _materializable_event_ref_set() -> set[str]:
        nonlocal event_ref_set
        if indexed_event_ref_set:
            return indexed_event_ref_set
        if event_ref_set is None:
            event_ref_set = {str(event.ref_key) for event in bundle.event_stream}
        return event_ref_set

    resolved: list[str] = []
    scope_events: list[str] = []

    if source_kind == "manual_refs":
        if isinstance(payload, dict):
            rows = payload.get("refs") or payload.get("ref_keys") or []
        else:
            rows = payload or []
        if isinstance(rows, (str, EvidenceRef)):
            rows = [rows]
        resolved = _dedupe_refs([_ref_key_from_payload(item) or "" for item in list(rows)])
        materializable_ref_set = _materializable_event_ref_set()
        scope_events = [ref_key for ref_key in resolved if ref_key in materializable_ref_set]
    elif source_kind == "anchor":
        anchor_payload = payload if isinstance(payload, dict) else {}
        anchor_ids = [str(item).strip() for item in list(anchor_payload.get("anchor_ids") or []) if str(item).strip()]
        if anchor_ids:
            anchor_refs: list[str] = []
            for anchor_id in anchor_ids:
                for row in anchors:
                    if str((row or {}).get("anchor_id") or "").strip() != anchor_id:
                        continue
                    ref_key = _ref_key_from_payload((row or {}).get("evidence_anchor"))
                    if ref_key is not None:
                        anchor_refs.append(ref_key)
            resolved = _dedupe_refs(anchor_refs)
        else:
            ref_key = _ref_key_from_payload(anchor_payload) or _ref_key_from_payload(context.get("evidence_anchor"))
            if ref_key is None:
                for row in anchors:
                    ref_key = _ref_key_from_payload((row or {}).get("evidence_anchor"))
                    if ref_key is not None:
                        break
            resolved = [ref_key] if ref_key is not None else []
        materializable_ref_set = _materializable_event_ref_set()
        scope_events = [ref_key for ref_key in resolved if ref_key in materializable_ref_set]
    elif source_kind == "analysis_context":
        ref_key = _ref_key_from_payload(context.get("evidence_anchor"))
        if ref_key is None:
            ref_key = _ref_key_from_payload((context.get("selection") or {}).get("seed_ref"))
        if ref_key is not None:
            resolved = [ref_key]
            scope_events = [ref_key] if ref_key in _materializable_event_ref_set() else []
        else:
            scoped = _scope_events_from_window(
                _resolve_time_window(source_kind, payload, request, context),
                ref_index_rows=normalized_ref_index_rows,
                bundle=bundle,
            )
            if not scoped.ok:
                return scoped
            scope_events = list(scoped.data)
            resolved = list(scope_events)
    elif source_kind == "time_window":
        scoped = _scope_events_from_window(
            _resolve_time_window(source_kind, payload, request, context),
            ref_index_rows=normalized_ref_index_rows,
            bundle=bundle,
        )
        if not scoped.ok:
            return scoped
        scope_events = list(scoped.data)
        resolved = list(scope_events)
    elif source_kind == "alert":
        alert_payload = payload if isinstance(payload, dict) else {}
        alert_ids = [str(item).strip() for item in list(alert_payload.get("alert_ids") or []) if str(item).strip()]
        if not alert_ids and alert_payload.get("alert_id") is not None:
            alert_ids = [str(alert_payload.get("alert_id")).strip()]
        targets: list[Alert] = []
        if alert_ids:
            by_id = {str(alert.alert_id): alert for alert in alerts}
            targets = [by_id[item] for item in alert_ids if item in by_id]
        elif alerts:
            targets = [alerts[0]]
        resolved = _dedupe_refs([ref_key for alert in targets for ref_key in _refs_from_alert(alert)])
        materializable_ref_set = _materializable_event_ref_set()
        scope_events = [ref_key for ref_key in resolved if ref_key in materializable_ref_set]
    elif source_kind == "diagnosis":
        diagnosis_payload = payload if isinstance(payload, dict) else {}
        diag_ids = [str(item).strip() for item in list(diagnosis_payload.get("diag_ids") or []) if str(item).strip()]
        if not diag_ids and diagnosis_payload.get("diag_id") is not None:
            diag_ids = [str(diagnosis_payload.get("diag_id")).strip()]
        targets: list[Diagnosis | dict[str, Any]] = []
        if diag_ids:
            by_id: dict[str, Diagnosis | dict[str, Any]] = {}
            for diagnosis in diagnoses:
                diag_key = diagnosis.diag_id if isinstance(diagnosis, Diagnosis) else diagnosis.get("diag_id")
                by_id[str(diag_key)] = diagnosis
            targets = [by_id[item] for item in diag_ids if item in by_id]
        elif diagnoses:
            targets = [diagnoses[0]]
        resolved = _dedupe_refs([ref_key for diagnosis in targets for ref_key in _refs_from_diagnosis(diagnosis)])
        materializable_ref_set = _materializable_event_ref_set()
        scope_events = [ref_key for ref_key in resolved if ref_key in materializable_ref_set]
    else:
        return err_result("INVALID_ARG", f"unsupported seed source_kind: {source_kind}")

    normalized = _dedupe_refs(resolved)
    if not normalized:
        return err_result("SEED_EMPTY", f"no seed refs resolved for source_kind={source_kind}")
    materialized_scope_events = _dedupe_refs(scope_events)
    missing_required_refs = [ref_key for ref_key in normalized if ref_key not in materialized_scope_events]
    if missing_required_refs and request.closure_policy.seed_materialization != "best_effort":
        return err_result(
            "SEED_EMPTY",
            f"unable to legally materialize seed refs via ref_index_rows: {', '.join(missing_required_refs)}",
        )
    return ok_result(
        SeedResolution(
            seed_refs=normalized,
            scope_events=materialized_scope_events,
            missing_required_refs=missing_required_refs,
        )
    )

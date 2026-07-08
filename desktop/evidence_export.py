from __future__ import annotations

from datetime import datetime, timezone
import json
try:
    import resource
except ImportError:  # pragma: no cover - non-Unix fallback
    resource = None
import time
from pathlib import Path
from typing import Any, Callable

from desktop.package_writer import (
    EvidencePackageWriteError,
    consume_export_write_metrics,
    export_write_metrics_snapshot,
    write_evidence_advisor_artifacts,
    write_evidence_context_payload,
    write_evidence_control_plane,
    write_evidence_event_payload,
    write_evidence_manifest,
    write_evidence_meta,
    write_evidence_reference_assets,
    write_evidence_rebuild_payload,
    write_evidence_result_payload,
)
from desktop.repository import DatasetRecord
from metric.core import alert_Evaluate, diag_Generate, metric_Init, metric_Ingest
from parser import encode_trace
from parser.codec import (
    CHUNK_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_CHUNK_MAGIC,
    TraceDecodeSession,
)
from parser.evidence_closure import execute_evidence_closure
from parser.evidence_models import (
    BlockerArtifact,
    EvidenceClosureOutcome,
    EvidenceExportRequest,
    FrontierSnapshot,
    ProofDigest,
    evd_DeriveStableSnapshotId,
    evd_RecomputeProofHash,
    evd_StableEventSortKey,
)
from parser.evidence_seed import resolve_seed_refs
from parser.evidence_sidecar import (
    build_dependency_sidecar,
    load_dependency_sidecar,
    materialize_dependency_sidecar,
    select_candidate_edges_from_sidecar,
    validate_dependency_sidecar_stream,
    validate_sidecar_segment_manifest_metadata,
    validate_sidecar_manifest_metadata,
    validate_sidecar_payload,
    validate_sidecar_manifest,
)
from parser.evidence_sidecar_index import (
    DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES,
    build_or_open_sidecar_index,
    read_sidecar_index_ticket,
    select_candidate_edges_from_segment_manifest,
    select_candidate_edges_from_index,
    sidecar_index_path_for_source,
    sidecar_index_ticket_path_for_source,
    sidecar_stream_scan_fallback_allowed,
)
from parser.models import (
    Alert,
    DecodedEvent,
    Diagnosis,
    EvidenceRef,
    RebuildBundle,
    ResourceGraph,
    UnifiedEvent,
    event_ref_key_for,
    materialize_unified_event,
)
from parser.result import Result, err_result, ok_result
from parser.runtime_advisor import RuntimeOptimizationAdvisor, build_advisor_trace
from parser.runtime_cost_graph import annotate_evidence_export_progress
from parser.runtime_optimization_gate import DeterministicValidationGate
from parser.telemetry import TelemetryHistoryStore, TelemetryReportAgent
from spec.io import checksum_file, json_load, serialize
from spec.schema_loader import DICTIONARY_PATH, SCHEMA_DIR


EVIDENCE_PACKAGE_VERSION = "rttrace-package-2"
EVIDENCE_PARSER_VERSION = "parser-evidence-v1"
TIME_UNIT_LABELS = {1: "ns"}
CLOCK_SOURCE_LABELS = {1: "steady_clock"}
SCHEMA_KEY_MAP = {
    "package.schema.json": "package_schema",
    "meta.schema.json": "meta_schema",
    "manifest.schema.json": "manifest_schema",
    "analysis_context.schema.json": "analysis_context_schema",
    "compare_scope.schema.json": "compare_scope_schema",
    "dependency_sidecar.schema.json": "dependency_sidecar_schema",
    "frontier_snapshot.schema.json": "frontier_snapshot_schema",
    "frontier_refs.schema.json": "frontier_refs_schema",
    "proof_digest.schema.json": "proof_digest_schema",
    "sidecar_manifest.schema.json": "sidecar_manifest_schema",
    "sidecar_segment_manifest.schema.json": "sidecar_segment_manifest_schema",
    "blocker_artifact.schema.json": "blocker_artifact_schema",
    "result_validity.schema.json": "result_validity_schema",
    "sidecar_index_ticket.schema.json": "sidecar_index_ticket_schema",
    "telemetry_record.schema.json": "telemetry_record_schema",
    "telemetry_record_update.schema.json": "telemetry_record_update_schema",
    "advisor_decision.schema.json": "advisor_decision_schema",
    "advisor_trace.schema.json": "advisor_trace_schema",
    "advisor_report.schema.json": "advisor_report_schema",
    "runtime_action.schema.json": "runtime_action_schema",
    "runtime_action_set.schema.json": "runtime_action_set_schema",
    "advisor_grounding_report.schema.json": "advisor_grounding_report_schema",
    "validation_gate_result.schema.json": "validation_gate_result_schema",
    "agent_job_contract.schema.json": "agent_job_contract_schema",
    "export_write_metric.schema.json": "export_write_metric_schema",
    "write_failure_blocker.schema.json": "write_failure_blocker_schema",
    "parser_process_artifact.schema.json": "parser_process_artifact_schema",
    "formal_schedule_plan.schema.json": "formal_schedule_plan_schema",
    "benchmark_scenario.schema.json": "benchmark_scenario_schema",
    "benchmark_report.schema.json": "benchmark_report_schema",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _current_process_peak_rss_mb() -> float | None:
    if resource is None:
        return None
    peak_rss_kb = getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0)
    if peak_rss_kb <= 0:
        return None
    return round(float(peak_rss_kb) / 1024.0, 3)


def _dependency_sidecar_checksum_from_manifest(manifest: dict[str, Any]) -> str:
    entry_checksums = {
        str(rel_path).strip(): str(checksum).strip()
        for rel_path, checksum in dict(manifest.get("entry_checksums") or {}).items()
        if str(rel_path).strip()
    }
    for rel_path in list(manifest.get("entry_paths") or []) + list(entry_checksums):
        text = str(rel_path).strip()
        if Path(text).name == "dependency_sidecar.jsonl" and entry_checksums.get(text):
            return str(entry_checksums[text])
    return ""


def _time_unit_label(header: Any) -> str:
    if header is None:
        return "unknown"
    return TIME_UNIT_LABELS.get(int(header.time_unit), str(header.time_unit))


def _clock_source_label(header: Any) -> str:
    if header is None:
        return "unknown"
    return CLOCK_SOURCE_LABELS.get(int(header.clock_source), str(header.clock_source))


def _event_record_from_unified_event(event: UnifiedEvent) -> dict[str, Any]:
    return {
        "core_id": int(event.core_id),
        "event_id": int(event.event_id),
        "seq": int(event.seq),
        "timestamp": int(event.timestamp_raw),
        "payload": dict(event.payload),
        "flags": 1 if event.trust_tags else 0,
        "ver": 1,
    }


def _materialized_ref_index_rows(events: list[UnifiedEvent]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": index,
            "event_uid": event.event_uid,
            "ref_key": event.ref_key,
            "core_id": int(event.core_id),
            "seq": int(event.seq),
            "timestamp_raw": float(event.timestamp_raw),
            "timestamp_aligned": float(event.timestamp_aligned),
        }
        for index, event in enumerate(events)
    ]


def _source_alignment_segments(bundle: RebuildBundle) -> dict[int, list[dict[str, float]]]:
    alignment = bundle.alignment
    if alignment is None:
        return {}
    segments_by_core: dict[int, list[dict[str, float]]] = {}
    for segment in list(alignment.segments or []):
        segments_by_core.setdefault(int(segment.core_id), []).append(
            {
                "t_begin": float(segment.t_begin),
                "t_end": float(segment.t_end),
                "offset_ns": float(segment.offset_ns),
            }
        )
    return segments_by_core


def _source_alignment_offsets(bundle: RebuildBundle) -> dict[int, float]:
    alignment = bundle.alignment
    if alignment is None:
        return {}
    return {int(core_id): float(offset) for core_id, offset in dict(alignment.offsets or {}).items()}


def _source_aligned_timestamp(bundle: RebuildBundle, event: DecodedEvent) -> float:
    timestamp_raw = float(event.timestamp_raw)
    for segment in _source_alignment_segments(bundle).get(int(event.core_id), []):
        if segment["t_begin"] <= timestamp_raw <= segment["t_end"]:
            return timestamp_raw + float(segment["offset_ns"])
    return timestamp_raw + float(_source_alignment_offsets(bundle).get(int(event.core_id), 0.0))


def _iter_source_decoded_events(
    trace_source: Path,
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
    with trace_source.open("rb") as handle:
        header_bytes = handle.read(GLOBAL_HEADER_STRUCT.size)
        if len(header_bytes) < GLOBAL_HEADER_STRUCT.size:
            return err_result("INVALID_ARG", f"incomplete trace header: {trace_source}")
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
                return err_result("INVALID_ARG", f"incomplete segment meta: {trace_source}")
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
                return err_result("INVALID_ARG", f"incomplete chunk header: {trace_source}")
            chunk_tuple = CHUNK_HEADER_STRUCT.unpack(chunk_header_bytes)
            if int(chunk_tuple[0]) != TRACE_CHUNK_MAGIC:
                return err_result("INVALID_ARG", f"invalid chunk magic in {trace_source}")
            payload_size = int(chunk_tuple[4])
            chunk_payload = handle.read(payload_size)
            if len(chunk_payload) < payload_size:
                return err_result("INVALID_ARG", f"incomplete chunk payload: {trace_source}")
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
    retained = [item for item in list(finalized.data.get("events") or []) if isinstance(item, DecodedEvent)]
    if retained:
        on_events(retained)
    return ok_result(None, warnings=finalized.warnings, untrusted_windows=finalized.untrusted_windows)


def _source_backed_ref_index_rows(
    trace_source: Path,
    bundle: RebuildBundle,
    *,
    dictionary: dict[str, Any] | str | Path | None,
) -> Result[list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []

    def _append_rows(events: list[DecodedEvent]) -> None:
        for event in events:
            timestamp_aligned = _source_aligned_timestamp(bundle, event)
            ref_key = event_ref_key_for(event, bundle.dataset_id)
            rows.append(
                {
                    "event_uid": ref_key,
                    "ref_key": ref_key,
                    "core_id": int(event.core_id),
                    "seq": int(event.seq),
                    "timestamp_raw": float(event.timestamp_raw),
                    "timestamp_aligned": float(timestamp_aligned),
                    "segment_hint": f"core:{int(event.core_id)}",
                }
            )

    scanned = _iter_source_decoded_events(
        trace_source,
        dataset_id=bundle.dataset_id,
        dictionary=dictionary,
        on_events=_append_rows,
    )
    if not scanned.ok:
        return Result(
            code=scanned.code,
            message=scanned.message,
            warnings=scanned.warnings,
            untrusted_windows=scanned.untrusted_windows,
        )
    rows.sort(
        key=lambda row: (
            float(row["timestamp_aligned"]),
            int(row["core_id"]),
            int(row["seq"]),
        )
    )
    for index, row in enumerate(rows):
        row["ordinal"] = index
    return ok_result(rows, warnings=scanned.warnings, untrusted_windows=scanned.untrusted_windows)


def _source_backed_selected_events(
    trace_source: Path,
    bundle: RebuildBundle,
    selected_refs: list[str],
    *,
    dictionary: dict[str, Any] | str | Path | None,
) -> Result[list[UnifiedEvent]]:
    selected_ref_set = {str(ref_key).strip() for ref_key in selected_refs if str(ref_key).strip()}
    if not selected_ref_set:
        return ok_result([])
    event_by_ref: dict[str, UnifiedEvent] = {}

    def _capture_selected(events: list[DecodedEvent]) -> None:
        for event in events:
            ref_key = event_ref_key_for(event, bundle.dataset_id)
            if ref_key not in selected_ref_set or ref_key in event_by_ref:
                continue
            event.timestamp_aligned = _source_aligned_timestamp(bundle, event)
            event_by_ref[ref_key] = materialize_unified_event(event, bundle.dataset_id)

    scanned = _iter_source_decoded_events(
        trace_source,
        dataset_id=bundle.dataset_id,
        dictionary=dictionary,
        on_events=_capture_selected,
    )
    if not scanned.ok:
        return Result(
            code=scanned.code,
            message=scanned.message,
            warnings=scanned.warnings,
            untrusted_windows=scanned.untrusted_windows,
        )
    return ok_result(
        sorted(event_by_ref.values(), key=evd_StableEventSortKey),
        warnings=scanned.warnings,
        untrusted_windows=scanned.untrusted_windows,
    )


def _background_sidecar_prebuild_sources(
    record: DatasetRecord,
    *,
    snapshot_id: str,
    trace_checksum: str,
    dictionary_checksum: str,
) -> dict[str, Any] | None:
    metadata = dict(record.background_sidecar_prebuild or {})
    if str(metadata.get("state") or "") != "completed":
        return None
    if str(metadata.get("activation_id") or "") != str(record.activation_id or ""):
        return None
    sidecar_path = Path(str(metadata.get("dependency_sidecar_path") or "")).expanduser()
    manifest_path = Path(str(metadata.get("sidecar_manifest_path") or "")).expanduser()
    index_path = Path(str(metadata.get("sidecar_index_path") or "")).expanduser()
    ticket_path = Path(str(metadata.get("sidecar_index_ticket_path") or "")).expanduser()
    if not sidecar_path.exists() or not manifest_path.exists() or not index_path.exists() or not ticket_path.exists():
        return None
    try:
        manifest = json_load(manifest_path)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if (
        str(manifest.get("snapshot_id") or "") != str(snapshot_id)
        or str(manifest.get("trace_checksum") or "") != str(trace_checksum)
        or str(manifest.get("dictionary_checksum") or "") != str(dictionary_checksum)
    ):
        return None
    return {
        "sidecar_source": str(sidecar_path),
        "sidecar_manifest_source": str(manifest_path),
        "sidecar_index_path": str(index_path),
        "sidecar_index_ticket_path": str(ticket_path),
        "manifest": manifest,
    }


def _bundle_ref_index_rows(bundle: RebuildBundle) -> list[dict[str, Any]]:
    ordered = sorted(list(bundle.event_stream), key=evd_StableEventSortKey)
    return _materialized_ref_index_rows(ordered)


def _ordered_selected_refs(
    ref_index_rows: list[dict[str, Any]],
    *,
    selected_refs: list[str],
    fallback_refs: list[str] | None = None,
) -> list[str]:
    desired = {
        str(ref_key).strip()
        for ref_key in list(selected_refs) + list(fallback_refs or [])
        if str(ref_key).strip()
    }
    if not desired:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for row in ref_index_rows:
        ref_key = str(row.get("ref_key") or "").strip()
        if ref_key in desired and ref_key not in seen:
            seen.add(ref_key)
            ordered.append(ref_key)
    for ref_key in sorted(desired - seen):
        ordered.append(ref_key)
    return ordered


def _bundle_time_window(bundle: RebuildBundle) -> tuple[float, float]:
    if bundle.event_stream:
        ordered = sorted(bundle.event_stream, key=evd_StableEventSortKey)
        return (float(ordered[0].timestamp_aligned), float(ordered[-1].timestamp_aligned))
    spans: list[tuple[float, float]] = []
    for row in list(bundle.exec_slices) + list(bundle.task_states) + list(bundle.irq_spans):
        spans.append((float(row.t_begin), float(row.t_end)))
    if not spans:
        return (0.0, 0.0)
    return (min(begin for begin, _ in spans), max(end for _, end in spans))


def _normalize_compare_scope(
    compare_scope: Any,
    *,
    dataset_id: str,
    export_window: tuple[float, float],
    export_filter: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(compare_scope, dict) and compare_scope:
        return {
            "scope_id": compare_scope.get("scope_id"),
            "baseline_id": str(compare_scope.get("baseline_id")),
            "candidate_id": str(compare_scope.get("candidate_id")),
            "aligned_time_window": [float(item) for item in list(compare_scope.get("aligned_time_window") or export_window)],
            "filter": dict(compare_scope.get("filter") or export_filter),
            "dimensions": list(compare_scope.get("dimensions") or []),
            "metric_ids": list(compare_scope.get("metric_ids") or []),
            "bucket_size": compare_scope.get("bucket_size"),
            "evidence_policy": compare_scope.get("evidence_policy"),
        }
    return {
        "scope_id": f"scope:{dataset_id}:evidence",
        "baseline_id": dataset_id,
        "candidate_id": dataset_id,
        "aligned_time_window": [float(export_window[0]), float(export_window[1])],
        "filter": dict(export_filter),
        "dimensions": [],
        "metric_ids": [],
        "bucket_size": None,
        "evidence_policy": "retain",
    }


def _build_analysis_context(
    snapshot_context: dict[str, Any],
    *,
    dataset_id: str,
    export_window: tuple[float, float],
    export_filter: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    compare_scope = _normalize_compare_scope(
        snapshot_context.get("compare_scope"),
        dataset_id=dataset_id,
        export_window=export_window,
        export_filter=export_filter,
    )
    context = {
        "time_window": [float(export_window[0]), float(export_window[1])],
        "filter": dict(export_filter),
        "selection": dict(snapshot_context.get("selection") or {}),
        "zoom_level": float(snapshot_context.get("zoom_level", 1.0)),
        "focused_view": snapshot_context.get("focused_view"),
        "evidence_anchor": snapshot_context.get("evidence_anchor"),
        "playback_cursor": snapshot_context.get("playback_cursor"),
        "compare_scope": compare_scope,
        "dataset_role": snapshot_context.get("dataset_role") or "single",
    }
    return context, compare_scope


def _iter_anchor_rows(
    context: dict[str, Any],
    alerts: list[Alert],
    diagnoses: list[Diagnosis],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    evidence_anchor = context.get("evidence_anchor")
    if evidence_anchor:
        rows.append(
            {
                "anchor_id": "context:current",
                "anchor_type": "context",
                "evidence_anchor": evidence_anchor,
                "time_window": list(context.get("time_window") or []),
            }
        )
    for alert in alerts:
        for index, evidence_ref in enumerate(list(alert.evidence_refs or [])):
            rows.append(
                {
                    "anchor_id": f"{alert.alert_id}:{index}",
                    "anchor_type": "alert",
                    "owner_id": alert.alert_id,
                    "evidence_anchor": serialize(evidence_ref),
                    "time_window": list(alert.time_window),
                }
            )
    for diagnosis in diagnoses:
        for index, evidence_ref in enumerate(list(diagnosis.evidence_refs or [])):
            rows.append(
                {
                    "anchor_id": f"{diagnosis.diag_id}:{index}",
                    "anchor_type": "diagnosis",
                    "owner_id": diagnosis.diag_id,
                    "evidence_anchor": serialize(evidence_ref),
                    "time_window": list(diagnosis.time_window),
                }
            )
    return rows


def _evidence_ref_from_payload(payload: Any) -> EvidenceRef:
    row = dict(payload or {})
    return EvidenceRef(
        ref_type=str(row.get("ref_type") or "event"),
        ref_key=str(row.get("ref_key") or ""),
        t_begin=float(row.get("t_begin", 0.0)),
        t_end=float(row.get("t_end", row.get("t_begin", 0.0))),
    )


def _alert_from_payload(payload: Any) -> Alert:
    row = dict(payload or {})
    time_window = list(row.get("time_window") or [0.0, 0.0])
    while len(time_window) < 2:
        time_window.append(time_window[0] if time_window else 0.0)
    return Alert(
        alert_id=str(row.get("alert_id") or ""),
        type=str(row.get("type") or ""),
        severity=str(row.get("severity") or ""),
        time_window=(float(time_window[0]), float(time_window[1])),
        object_scope=dict(row.get("object_scope") or {}),
        threshold=float(row.get("threshold", 0.0)),
        actual=float(row.get("actual", 0.0)),
        evidence_refs=[_evidence_ref_from_payload(item) for item in list(row.get("evidence_refs") or [])],
        trusted=bool(row.get("trusted", True)),
        support_level=str(row.get("support_level") or "exact"),
    )


def _diagnosis_from_payload(payload: Any) -> Diagnosis:
    row = dict(payload or {})
    time_window = list(row.get("time_window") or [0.0, 0.0])
    while len(time_window) < 2:
        time_window.append(time_window[0] if time_window else 0.0)
    return Diagnosis(
        diag_id=str(row.get("diag_id") or ""),
        title=str(row.get("title") or ""),
        diagnosis_type=str(row.get("diagnosis_type") or ""),
        time_window=(float(time_window[0]), float(time_window[1])),
        object_scope=dict(row.get("object_scope") or {}),
        conclusion=str(row.get("conclusion") or ""),
        evidence_refs=[_evidence_ref_from_payload(item) for item in list(row.get("evidence_refs") or [])],
        related_alerts=[str(item) for item in list(row.get("related_alerts") or [])],
        confidence=str(row.get("confidence") or ""),
        support_level=str(row.get("support_level") or "exact"),
    )


def _candidate_mode_b_roots(sidecar_manifest_root: Path | None, source: str | Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved in seen or not path.exists() or not path.is_dir():
            return
        seen.add(resolved)
        roots.append(path)

    add(sidecar_manifest_root)
    source_path = Path(source)
    if source_path.is_dir():
        add(source_path)
    return roots


def _load_mode_b_frozen_results(
    sidecar_manifest_root: Path | None,
    source: str | Path,
) -> Result[tuple[list[Alert], list[Diagnosis]]]:
    missing_candidates: list[str] = []
    for root in _candidate_mode_b_roots(sidecar_manifest_root, source):
        alerts_path = root / "result" / "alerts.json"
        diagnoses_path = root / "result" / "diagnoses.json"
        if not alerts_path.exists() or not diagnoses_path.exists():
            missing_candidates.append(str(root))
            continue
        try:
            alerts_payload = json_load(alerts_path)
            diagnoses_payload = json_load(diagnoses_path)
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            return err_result("SIDECAR_MISMATCH", f"mode_b frozen result load failed under {root}: {exc}")
        return ok_result(
            (
                [_alert_from_payload(item) for item in list(alerts_payload or [])],
                [_diagnosis_from_payload(item) for item in list(diagnoses_payload or [])],
            )
        )
    detail = ", ".join(missing_candidates) if missing_candidates else "no candidate roots"
    return err_result("SIDECAR_MISMATCH", f"mode_b frozen result whitelist missing under: {detail}")


def _seed_source_kind(request: EvidenceExportRequest) -> str:
    return str(request.seed_spec.source_kind or "analysis_context").strip().lower()


def _mode_b_seed_requires_frozen_results(request: EvidenceExportRequest) -> bool:
    # P1-1: only alert/diagnosis seeds require frozen result whitelist as a hard contract.
    return _seed_source_kind(request) in {"alert", "diagnosis"}


def _anchor_seed_requires_preanalysis(request: EvidenceExportRequest) -> bool:
    if _seed_source_kind(request) != "anchor":
        return False
    payload = request.seed_spec.source_payload if isinstance(request.seed_spec.source_payload, dict) else {}
    anchor_ids = [str(item).strip() for item in list(payload.get("anchor_ids") or []) if str(item).strip()]
    return any(anchor_id != "context:current" for anchor_id in anchor_ids)


def _mode_a_can_delay_analysis(request: EvidenceExportRequest) -> bool:
    if request.embodiment_mode == "mode_b":
        return False
    if _seed_source_kind(request) in {"alert", "diagnosis"}:
        return False
    if _anchor_seed_requires_preanalysis(request):
        return False
    risky_rule_families = {"ref_alert", "ref_diagnosis"}
    if any(str(rule_family).strip() in risky_rule_families for rule_family in request.rule_family):
        return False
    return True


def _run_analysis_on_bundle(
    metric_cfg: Any,
    bundle: RebuildBundle,
    *,
    export_window: tuple[float, float],
    export_filter: dict[str, Any],
) -> Result[tuple[list[Alert], list[Diagnosis]]]:
    session_result = metric_Init(metric_cfg)
    if not session_result.ok:
        return session_result
    session = session_result.data
    ingested = metric_Ingest(session, bundle)
    if not ingested.ok:
        return Result(
            code=ingested.code,
            message=ingested.message,
            warnings=ingested.warnings,
            untrusted_windows=ingested.untrusted_windows,
        )
    alerts = alert_Evaluate(session, export_window[0], export_window[1], export_filter).data or []
    diagnoses = diag_Generate(session, export_window[0], export_window[1], alert_list=alerts).data or []
    return ok_result((list(alerts), list(diagnoses)))


def _edge_int(edge: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = edge.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _subset_resource_graph_for_selected_scope(
    graph: ResourceGraph | None,
    selected_events: list[UnifiedEvent],
) -> ResourceGraph:
    if graph is None or not selected_events:
        return ResourceGraph()

    selected_refs: set[str] = set()
    selected_task_ids: set[int] = set()
    selected_obj_ids: set[int] = set()
    for event in selected_events:
        if event.ref_key:
            selected_refs.add(str(event.ref_key))
        if event.event_uid:
            selected_refs.add(str(event.event_uid))
        if event.task_id is not None:
            selected_task_ids.add(int(event.task_id))
        if event.obj_id is not None:
            selected_obj_ids.add(int(event.obj_id))
        payload = dict(event.payload or {})
        for key in ("task_id", "prev_task_id", "next_task_id", "owner_task_id"):
            value = payload.get(key)
            if value is not None:
                try:
                    selected_task_ids.add(int(value))
                except (TypeError, ValueError):
                    pass
        for key in ("obj_id", "wait_obj_id"):
            value = payload.get(key)
            if value is not None:
                try:
                    selected_obj_ids.add(int(value))
                except (TypeError, ValueError):
                    pass

    def keep_edge(edge: dict[str, Any]) -> bool:
        evidence_ref = edge.get("evidence_ref")
        if evidence_ref:
            return str(evidence_ref) in selected_refs
        task_id = _edge_int(edge, "task_id", "from_task")
        owner_task_id = _edge_int(edge, "owner_task_id", "owner_task", "to_task")
        obj_id = _edge_int(edge, "obj_id", "to_obj", "from_obj")
        return (
            (task_id is not None and task_id in selected_task_ids)
            or (owner_task_id is not None and owner_task_id in selected_task_ids)
            or (obj_id is not None and obj_id in selected_obj_ids)
        )

    hold_edges = [dict(edge) for edge in list(graph.hold_edges or []) if keep_edge(dict(edge))]
    wait_edges = [dict(edge) for edge in list(graph.wait_edges or []) if keep_edge(dict(edge))]

    node_counts: dict[str, int] = {}

    def bump_node(node_id: str | None) -> None:
        if not node_id:
            return
        node_counts[node_id] = node_counts.get(node_id, 0) + 1

    for event in selected_events:
        if event.task_id is not None:
            bump_node(f"task:{int(event.task_id)}")
        if event.obj_id is not None:
            bump_node(f"obj:{int(event.obj_id)}")
    for edge in hold_edges + wait_edges:
        task_id = _edge_int(edge, "task_id", "from_task")
        owner_task_id = _edge_int(edge, "owner_task_id", "owner_task", "to_task")
        obj_id = _edge_int(edge, "obj_id", "to_obj", "from_obj")
        if task_id is not None:
            bump_node(f"task:{task_id}")
        if owner_task_id is not None:
            bump_node(f"task:{owner_task_id}")
        if obj_id is not None:
            bump_node(f"obj:{obj_id}")

    nodes = [
        {"node_id": node_id, "count": count}
        for node_id, count in sorted(node_counts.items(), key=lambda item: item[0])
    ]
    hotspot_stats = sorted(
        [{"node_id": node_id, "count": count} for node_id, count in node_counts.items()],
        key=lambda item: (-item["count"], item["node_id"]),
    )[:10]
    return ResourceGraph(nodes=nodes, hold_edges=hold_edges, wait_edges=wait_edges, hotspot_stats=hotspot_stats)


def _subset_rebuild_bundle(
    bundle: RebuildBundle,
    selected_events: list[UnifiedEvent],
    export_window: tuple[float, float],
) -> RebuildBundle:
    selected_ref_set = {event.ref_key for event in selected_events}
    selected_task_states = [
        row
        for row in bundle.task_states
        if row.cause_event in selected_ref_set
    ]
    selected_exec_slices = [
        row
        for row in bundle.exec_slices
        if row.start_event in selected_ref_set or row.end_event in selected_ref_set
    ]
    selected_cores = {int(event.core_id) for event in selected_events}
    selected_irqs = [
        row
        for row in bundle.irq_spans
        if (
            int(row.core_id) in selected_cores
            and float(row.t_begin) < float(export_window[1])
            and float(row.t_end) > float(export_window[0])
        )
    ]
    selected_windows = [
        row
        for row in bundle.untrusted_windows
        if float(row.t_begin) < float(export_window[1]) and float(row.t_end) > float(export_window[0])
    ]
    return RebuildBundle(
        bundle_id=bundle.bundle_id,
        dataset_id=bundle.dataset_id,
        event_stream=list(selected_events),
        task_states=selected_task_states,
        exec_slices=selected_exec_slices,
        resource_graph=_subset_resource_graph_for_selected_scope(bundle.resource_graph, selected_events),
        irq_spans=selected_irqs,
        untrusted_windows=selected_windows,
        rebuild_rev=bundle.rebuild_rev,
        capability_flags=dict(bundle.capability_flags or {}),
        alignment=bundle.alignment,
        segment_metas=list(bundle.segment_metas),
        header=bundle.header,
        index_bundle=None,
    )


def _resolve_source_trace_path(source: str) -> Result[Path]:
    path = Path(source)
    if path.is_file():
        return ok_result(path)
    if path.is_dir():
        package_trace = path / "event" / "events.trace"
        if package_trace.exists():
            return ok_result(package_trace)
    return err_result("INVALID_ARG", f"unable to resolve trace source path for evidence export: {source}")


def _resolve_source_dictionary_path(source: str) -> Path:
    path = Path(source)
    if path.is_dir():
        package_dictionary = path / "reference" / "dictionary.json"
        if package_dictionary.exists():
            return package_dictionary
    return DICTIONARY_PATH


def _repo_sidecar_schema_checksums() -> dict[str, Any]:
    schema_checksums: dict[str, Any] = {}
    for schema_name, schema_key in SCHEMA_KEY_MAP.items():
        if schema_key not in {
            "dependency_sidecar_schema",
            "frontier_snapshot_schema",
            "frontier_refs_schema",
            "proof_digest_schema",
            "sidecar_manifest_schema",
            "blocker_artifact_schema",
        }:
            continue
        source_path = SCHEMA_DIR / schema_name
        schema_checksums[schema_key] = {
            "path": f"reference/schema/{schema_name}",
            "algo": "sha256",
            "checksum": checksum_file(source_path),
        }
    return schema_checksums


def _build_sidecar_manifest_draft(
    *,
    snapshot_id: str,
    trace_checksum: str,
    dictionary_checksum: str,
    rule_family: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "sidecar_version": "evidence-sidecar-1",
        "generator_version": EVIDENCE_PARSER_VERSION,
        "trace_checksum": trace_checksum,
        "dictionary_checksum": dictionary_checksum,
        "schema_checksums": _repo_sidecar_schema_checksums(),
        "relation_families": list(rule_family),
        "created_at": _iso_now(),
        "snapshot_id": snapshot_id,
    }


def write_evidence_package(
    record: DatasetRecord,
    snapshot_context: dict[str, Any],
    job: dict[str, Any],
    *,
    job_id: str,
    output_path: str | Path,
    emit_progress: Callable[[dict[str, Any]], None] | None = None,
) -> Result[dict[str, Any]]:
    try:
        return _write_evidence_package_impl(
            record,
            snapshot_context,
            job,
            job_id=job_id,
            output_path=output_path,
            emit_progress=emit_progress,
        )
    except EvidencePackageWriteError as exc:
        if emit_progress is not None:
            emit_progress(
                {
                    "category": "evidence_export",
                    "substage": "write/package",
                    "status": "failed",
                }
            )
        data = dict(exc.result.data or {})
        package_result = dict(data.get("package_result") or {})
        package_result["write_metrics"] = export_write_metrics_snapshot()
        data["package_result"] = package_result
        return Result(exc.result.code, exc.result.message, data=data)


def _write_evidence_package_impl(
    record: DatasetRecord,
    snapshot_context: dict[str, Any],
    job: dict[str, Any],
    *,
    job_id: str,
    output_path: str | Path,
    emit_progress: Callable[[dict[str, Any]], None] | None = None,
) -> Result[dict[str, Any]]:
    def _progress(
        substage: str,
        status: str,
        *,
        round_id: int | None = None,
        frontier_count: int | None = None,
        emitted_events: int | None = None,
        emitted_bytes: int | None = None,
    ) -> None:
        if emit_progress is None:
            return
        payload: dict[str, Any] = {
            "category": "evidence_export",
            "substage": substage,
            "status": status,
        }
        if round_id is not None:
            payload["round_id"] = int(round_id)
        if frontier_count is not None:
            payload["frontier_count"] = int(frontier_count)
        if emitted_events is not None:
            payload["emitted_events"] = int(emitted_events)
        if emitted_bytes is not None:
            payload["emitted_bytes"] = int(emitted_bytes)
        emit_progress(annotate_evidence_export_progress(payload))

    def _emit_progress_payload(payload: dict[str, Any]) -> None:
        if emit_progress is None:
            return
        emit_progress(annotate_evidence_export_progress(dict(payload)))

    export_started_at = time.perf_counter()
    advisor_enabled = bool(job.get("advisor_enabled", False))
    advisor_mode = str(job.get("advisor_mode") or ("heuristic" if advisor_enabled else "disabled"))
    advisor_config = dict(job.get("advisor_config") or {})
    advisor_config.setdefault("advisor_mode", advisor_mode)
    advisor_gate = DeterministicValidationGate()
    advisor_decision = None
    advisor_gate_result = None
    pre_execution_advisor_decision = None
    pre_execution_gate_result = None
    pre_execution_advisor_overhead_seconds = 0.0
    pre_execution_advisor_trace = None
    pre_execution_agent_contract: dict[str, Any] | None = None
    pre_execution_agent_contract_ref: dict[str, Any] | None = None
    pre_execution_advisor_trace_ref: dict[str, Any] | None = None
    pre_execution_advisor_metadata: dict[str, Any] | None = None
    advisor_execution_plan: list[str] = []
    advisor_feature_snapshot: dict[str, Any] = {}
    advisor_started_at: float | None = None
    sidecar_ticket_for_advisor = None

    dataset_id = str(job.get("dataset_id") or record.artifact.dataset_id)
    bundle = record.artifact.bundle
    default_window = _bundle_time_window(bundle)
    _progress("prepare/context_freeze", "started")
    request_result: EvidenceExportRequest
    try:
        request_result = EvidenceExportRequest.from_payload(
            job,
            default_dataset_id=dataset_id,
            default_time_window=default_window,
            default_filter=dict(snapshot_context.get("filter") or {}),
        )
    except ValueError as exc:
        _progress("prepare/context_freeze", "failed")
        return err_result("INVALID_ARG", str(exc))
    request = request_result
    export_window = request.time_window or default_window
    export_filter = dict(request.filter or snapshot_context.get("filter") or {})
    analysis_context, compare_scope = _build_analysis_context(
        snapshot_context,
        dataset_id=dataset_id,
        export_window=export_window,
        export_filter=export_filter,
    )

    package_path = Path(output_path)
    telemetry_history_path = None
    raw_telemetry_history_path = job.get("telemetry_history_path")
    if raw_telemetry_history_path is not None and str(raw_telemetry_history_path).strip():
        telemetry_history_path = Path(str(raw_telemetry_history_path)).expanduser().resolve()
    elif advisor_enabled:
        telemetry_history_path = package_path.resolve().parent / "telemetry_history.jsonl"
    advisor_telemetry_history: list[dict[str, Any]] = []
    if advisor_enabled and telemetry_history_path is not None:
        try:
            advisor_telemetry_history = TelemetryHistoryStore(telemetry_history_path).read_advisor_history(
                dataset_id=dataset_id,
                embodiment_mode=request.embodiment_mode,
                export_family="evidence",
            )
        except Exception:
            advisor_telemetry_history = []
    consume_export_write_metrics()

    delay_analysis_until_selected = _mode_a_can_delay_analysis(request)
    alerts: list[Alert] = []
    diagnoses: list[Diagnosis] = []
    anchor_rows: list[dict[str, Any]] = _iter_anchor_rows(analysis_context, alerts, diagnoses)
    if request.embodiment_mode != "mode_b" and not delay_analysis_until_selected:
        analysis_result = _run_analysis_on_bundle(
            record.metric_session.cfg,
            bundle,
            export_window=export_window,
            export_filter=export_filter,
        )
        if not analysis_result.ok:
            _progress("prepare/context_freeze", "failed")
            return analysis_result
        alerts, diagnoses = analysis_result.data
        anchor_rows = _iter_anchor_rows(analysis_context, alerts, diagnoses)

    trace_source_result = _resolve_source_trace_path(str(record.artifact.source))
    if not trace_source_result.ok:
        _progress("prepare/context_freeze", "failed")
        return trace_source_result
    trace_source = trace_source_result.data
    dictionary_source: dict[str, Any] | str | Path | None = (
        (record.artifact.dictionary_info or {}).get("resolved_dictionary")
        or _resolve_source_dictionary_path(str(record.artifact.source))
    )
    if bundle.event_stream:
        ref_index_rows = _bundle_ref_index_rows(bundle)
    else:
        source_ref_index_rows = _source_backed_ref_index_rows(
            trace_source,
            bundle,
            dictionary=dictionary_source,
        )
        if not source_ref_index_rows.ok:
            _progress("prepare/context_freeze", "failed")
            return source_ref_index_rows
        ref_index_rows = list(source_ref_index_rows.data)
    source_trace_checksum = checksum_file(trace_source)
    source_dictionary_checksum = checksum_file(_resolve_source_dictionary_path(str(record.artifact.source)))
    _progress("prepare/context_freeze", "completed")

    snapshot_id = evd_DeriveStableSnapshotId(
        dataset_id=dataset_id,
        embodiment_mode=request.embodiment_mode,
        trace_checksum=source_trace_checksum,
        dictionary_checksum=source_dictionary_checksum,
        request=request,
        analysis_context=analysis_context,
    )
    explicit_sidecar_requested = request.sidecar_source is not None or request.sidecar_manifest_source is not None
    background_prebuild_candidate = None
    effective_sidecar_source = request.sidecar_source
    effective_sidecar_manifest_source = request.sidecar_manifest_source
    if request.embodiment_mode != "mode_b" and not explicit_sidecar_requested:
        background_prebuild_candidate = _background_sidecar_prebuild_sources(
            record,
            snapshot_id=snapshot_id,
            trace_checksum=source_trace_checksum,
            dictionary_checksum=source_dictionary_checksum,
        )
        if background_prebuild_candidate is not None:
            effective_sidecar_source = str(background_prebuild_candidate["sidecar_source"])
            effective_sidecar_manifest_source = str(background_prebuild_candidate["sidecar_manifest_source"])

    candidate_consumption_diagnostics: dict[str, Any] = {
        "candidate_local_sidecar_build_calls": 0,
        "candidate_local_sidecar_materialize_calls": 0,
        "candidate_package_sidecar_write_count": 0,
        "candidate_used_background_prebuild": False,
        "candidate_ticket_fast_path_used": False,
        "candidate_stream_validate_used": False,
        "candidate_sqlite_index_rebuilt": False,
        "candidate_stream_scan_fallback_used": False,
        "candidate_full_sidecar_duplicate_write_detected": False,
        "candidate_full_sidecar_duplicate_write_heuristic_only": False,
        "candidate_package_sidecar_checksum": None,
        "background_prebuild_sidecar_checksum": (
            _dependency_sidecar_checksum_from_manifest(dict(background_prebuild_candidate["manifest"]))
            if background_prebuild_candidate is not None
            else None
        ),
    }

    def _build_local_sidecar_rows() -> list[Any]:
        candidate_consumption_diagnostics["candidate_local_sidecar_build_calls"] = int(
            candidate_consumption_diagnostics["candidate_local_sidecar_build_calls"]
        ) + 1
        candidate_consumption_diagnostics["candidate_local_sidecar_materialize_calls"] = int(
            candidate_consumption_diagnostics["candidate_local_sidecar_materialize_calls"]
        ) + 1
        return materialize_dependency_sidecar(
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
            trace_checksum=source_trace_checksum,
        )

    sidecar_seed_rows: list[Any] = []
    sidecar_candidate_selector: Callable[[list[str], tuple[str, ...]], Result[list[Any]]] | None = None
    outcome: EvidenceClosureOutcome | None = None
    sidecar_manifest_source_payload: dict[str, Any] | None = None
    sidecar_segment_manifest_source_payload: dict[str, Any] | None = None
    sidecar_manifest_root: Path | None = None
    if background_prebuild_candidate is not None:
        sidecar_manifest_source_payload = dict(background_prebuild_candidate["manifest"])
        sidecar_manifest_path = Path(effective_sidecar_manifest_source)
        manifest_parent = sidecar_manifest_path.parent
        sidecar_manifest_root = manifest_parent.parent if manifest_parent.name == "control" else manifest_parent
    sidecar_selector_telemetry: dict[str, Any] = {
        "sidecar_bytes": None,
        "sidecar_row_count": None,
        "sidecar_selector_mode": None,
        "sidecar_selector_calls": 0,
        "sidecar_bytes_scanned": 0,
        "sidecar_validate_seconds": 0.0,
        "sidecar_index_build_seconds": 0.0,
        "sidecar_index_reused": False,
        "sidecar_index_rebuilt": False,
        "sidecar_ticket_fast_path": False,
        "background_prebuild_reused": False,
    }
    external_sidecar_requested = effective_sidecar_source is not None and effective_sidecar_manifest_source is not None

    def _fallback_to_local_sidecar() -> None:
        nonlocal background_prebuild_candidate
        nonlocal external_sidecar_requested
        nonlocal sidecar_seed_rows
        nonlocal sidecar_manifest_source_payload
        nonlocal sidecar_segment_manifest_source_payload
        nonlocal sidecar_manifest_root

        background_prebuild_candidate = None
        external_sidecar_requested = False
        sidecar_manifest_source_payload = None
        sidecar_segment_manifest_source_payload = None
        sidecar_manifest_root = None
        sidecar_seed_rows = _build_local_sidecar_rows()

    _progress("sidecar/build_or_load", "started")
    if request.embodiment_mode == "mode_b":
        if not external_sidecar_requested:
            if request.closure_policy.allow_degraded:
                sidecar_seed_rows = []
                outcome = EvidenceClosureOutcome(
                    closure_mode="degraded",
                    halt_reason="SIDECAR_MISMATCH",
                    round_id=0,
                    seed_refs=[],
                    closed_refs=[],
                    selected_refs=[],
                    degraded_code="SIDECAR_MISMATCH",
                    degraded_message="mode_b requires sidecar_source and sidecar_manifest_source",
                )
            else:
                _progress("sidecar/build_or_load", "failed")
                return err_result("INVALID_ARG", "mode_b requires sidecar_source and sidecar_manifest_source")
        else:
            try:
                sidecar_manifest_source_payload = json_load(effective_sidecar_manifest_source)
            except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
                if request.closure_policy.allow_degraded:
                    sidecar_seed_rows = []
                    outcome = EvidenceClosureOutcome(
                        closure_mode="degraded",
                        halt_reason="SIDECAR_MISMATCH",
                        round_id=0,
                        seed_refs=[],
                        closed_refs=[],
                        selected_refs=[],
                        degraded_code="SIDECAR_MISMATCH",
                        degraded_message=f"sidecar manifest load failed: {exc}",
                    )
                else:
                    _progress("sidecar/build_or_load", "failed")
                    return err_result("INVALID_ARG", f"sidecar manifest load failed: {exc}")
            else:
                sidecar_manifest_path = Path(effective_sidecar_manifest_source)
                manifest_parent = sidecar_manifest_path.parent
                sidecar_manifest_root = manifest_parent.parent if manifest_parent.name == "control" else manifest_parent
                manifest_snapshot_id = str(sidecar_manifest_source_payload.get("snapshot_id") or "").strip()
                if not manifest_snapshot_id:
                    if request.closure_policy.allow_degraded:
                        sidecar_seed_rows = []
                        outcome = EvidenceClosureOutcome(
                            closure_mode="degraded",
                            halt_reason="SIDECAR_MISMATCH",
                            round_id=0,
                            seed_refs=[],
                            closed_refs=[],
                            selected_refs=[],
                            degraded_code="SIDECAR_MISMATCH",
                            degraded_message="sidecar manifest missing snapshot_id",
                        )
                    else:
                        _progress("sidecar/build_or_load", "failed")
                        return err_result("SIDECAR_MISMATCH", "sidecar manifest missing snapshot_id")
                else:
                    snapshot_id = manifest_snapshot_id
        if outcome is None:
            if _mode_b_seed_requires_frozen_results(request):
                frozen_results = _load_mode_b_frozen_results(sidecar_manifest_root, record.artifact.source)
                if not frozen_results.ok:
                    if request.closure_policy.allow_degraded:
                        sidecar_seed_rows = []
                        outcome = EvidenceClosureOutcome(
                            closure_mode="degraded",
                            halt_reason="SIDECAR_MISMATCH",
                            round_id=0,
                            seed_refs=[],
                            closed_refs=[],
                            selected_refs=[],
                            degraded_code="SIDECAR_MISMATCH",
                            degraded_message=frozen_results.message,
                        )
                    else:
                        _progress("sidecar/build_or_load", "failed")
                        return frozen_results
                else:
                    alerts, diagnoses = frozen_results.data
            else:
                optional_frozen_results = _load_mode_b_frozen_results(sidecar_manifest_root, record.artifact.source)
                if optional_frozen_results.ok:
                    alerts, diagnoses = optional_frozen_results.data
                else:
                    alerts, diagnoses = [], []
            anchor_rows = _iter_anchor_rows(analysis_context, alerts, diagnoses)
    if not sidecar_seed_rows and request.embodiment_mode != "mode_b" and not external_sidecar_requested:
        sidecar_seed_rows = _build_local_sidecar_rows()
    _progress("sidecar/build_or_load", "completed" if outcome is None else "failed")

    _progress("sidecar/validate", "started")
    sidecar_validate_completed = False
    if outcome is None and external_sidecar_requested:
        if sidecar_manifest_source_payload is None:
            if background_prebuild_candidate is not None and not explicit_sidecar_requested:
                _fallback_to_local_sidecar()
            elif request.closure_policy.allow_degraded:
                sidecar_seed_rows = []
                outcome = EvidenceClosureOutcome(
                    closure_mode="degraded",
                    halt_reason="SIDECAR_MISMATCH",
                    round_id=0,
                    seed_refs=[],
                    closed_refs=[],
                    selected_refs=[],
                    degraded_code="SIDECAR_MISMATCH",
                    degraded_message="sidecar manifest missing payload",
                )
                _progress("sidecar/validate", "failed")
            else:
                _progress("sidecar/validate", "failed")
                return err_result("SIDECAR_MISMATCH", "sidecar manifest missing payload")
        else:
            validated = validate_sidecar_manifest_metadata(
                sidecar_manifest_source_payload,
                expected_snapshot_id=snapshot_id,
                expected_trace_checksum=source_trace_checksum,
                expected_dictionary_checksum=source_dictionary_checksum,
                manifest_root=sidecar_manifest_root,
            )
            if not validated.ok:
                if background_prebuild_candidate is not None and not explicit_sidecar_requested:
                    _fallback_to_local_sidecar()
                elif request.closure_policy.allow_degraded:
                    sidecar_seed_rows = []
                    outcome = EvidenceClosureOutcome(
                        closure_mode="degraded",
                        halt_reason="SIDECAR_MISMATCH",
                        round_id=0,
                        seed_refs=[],
                        closed_refs=[],
                        selected_refs=[],
                        degraded_code="SIDECAR_MISMATCH",
                        degraded_message=validated.message,
                    )
                    _progress("sidecar/validate", "failed")
                else:
                    _progress("sidecar/validate", "failed")
                    return validated
            else:
                segment_manifest_rel = str(sidecar_manifest_source_payload.get("segment_manifest_path") or "").strip()
                sidecar_segment_manifest_source_payload = None
                if segment_manifest_rel:
                    if sidecar_manifest_root is None:
                        if request.closure_policy.allow_degraded:
                            sidecar_seed_rows = []
                            outcome = EvidenceClosureOutcome(
                                closure_mode="degraded",
                                halt_reason="SIDECAR_MISMATCH",
                                round_id=0,
                                seed_refs=[],
                                closed_refs=[],
                                selected_refs=[],
                                degraded_code="SIDECAR_MISMATCH",
                                degraded_message="sidecar segment manifest root missing",
                            )
                            _progress("sidecar/validate", "failed")
                        else:
                            _progress("sidecar/validate", "failed")
                            return err_result("SIDECAR_MISMATCH", "sidecar segment manifest root missing")
                    elif outcome is None:
                        segment_manifest_path = sidecar_manifest_root / segment_manifest_rel
                        try:
                            sidecar_segment_manifest_source_payload = json_load(segment_manifest_path)
                        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
                            if request.closure_policy.allow_degraded:
                                sidecar_seed_rows = []
                                outcome = EvidenceClosureOutcome(
                                    closure_mode="degraded",
                                    halt_reason="SIDECAR_MISMATCH",
                                    round_id=0,
                                    seed_refs=[],
                                    closed_refs=[],
                                    selected_refs=[],
                                    degraded_code="SIDECAR_MISMATCH",
                                    degraded_message=f"sidecar segment manifest load failed: {exc}",
                                )
                                _progress("sidecar/validate", "failed")
                            else:
                                _progress("sidecar/validate", "failed")
                                return err_result("INVALID_ARG", f"sidecar segment manifest load failed: {exc}")
                        else:
                            segmented_validated = validate_sidecar_segment_manifest_metadata(
                                sidecar_segment_manifest_source_payload,
                                expected_snapshot_id=snapshot_id,
                                expected_trace_checksum=source_trace_checksum,
                                expected_dictionary_checksum=source_dictionary_checksum,
                                manifest_root=sidecar_manifest_root,
                            )
                            if not segmented_validated.ok:
                                if request.closure_policy.allow_degraded:
                                    sidecar_seed_rows = []
                                    outcome = EvidenceClosureOutcome(
                                        closure_mode="degraded",
                                        halt_reason="SIDECAR_MISMATCH",
                                        round_id=0,
                                        seed_refs=[],
                                        closed_refs=[],
                                        selected_refs=[],
                                        degraded_code="SIDECAR_MISMATCH",
                                        degraded_message=segmented_validated.message,
                                    )
                                    _progress("sidecar/validate", "failed")
                                else:
                                    _progress("sidecar/validate", "failed")
                                    return segmented_validated
                sidecar_source_path = Path(effective_sidecar_source)
                sidecar_checksum = _dependency_sidecar_checksum_from_manifest(sidecar_manifest_source_payload)
                sidecar_bytes = int(sidecar_source_path.stat().st_size)
                sidecar_index_path = sidecar_index_path_for_source(
                    sidecar_source_path,
                    sidecar_checksum=sidecar_checksum,
                )
                ticket_path = sidecar_index_ticket_path_for_source(
                    sidecar_source_path,
                    index_path=sidecar_index_path,
                )
                fallback_allowed = sidecar_stream_scan_fallback_allowed(sidecar_bytes)
                sidecar_selector_telemetry["sidecar_bytes"] = int(sidecar_bytes)
                _emit_progress_payload(
                    {
                        "category": "evidence_export",
                        "substage": "sidecar/preflight",
                        "status": "completed",
                        "sidecar_bytes": int(sidecar_bytes),
                        "sidecar_stream_scan_threshold_bytes": int(DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES),
                        "sidecar_requires_index": bool(not fallback_allowed),
                        "sidecar_index_candidate": str(sidecar_index_path),
                        "sidecar_index_ticket_candidate": str(ticket_path),
                        "background_prebuild_reused": bool(background_prebuild_candidate is not None),
                    }
                )

                validated_fingerprint: tuple[int, int, int, int] | None = None
                ticket_fast_path = False
                ticket = None
                loaded_ticket = read_sidecar_index_ticket(ticket_path)
                if advisor_enabled:
                    _progress("advisor/evaluate", "started")
                    pre_execution_advisor_started_at = time.perf_counter()
                    pre_execution_advisor_job_id = f"{job_id}:pre_execution"
                    pre_execution_agent_contract_rel = "control/pre_execution_runtime_advisor_agent_contract.json"
                    pre_execution_agent_contract_ref = {
                        "path": pre_execution_agent_contract_rel,
                        "job_id": pre_execution_advisor_job_id,
                    }
                    pre_ticket_payload = loaded_ticket.data if loaded_ticket.ok else None
                    pre_execution_features = {
                        "advisor_phase": "pre_execution",
                        "dataset_id": dataset_id,
                        "run_id": str(job.get("run_id") or record.artifact.header.run_id or dataset_id),
                        "embodiment_mode": request.embodiment_mode,
                        "export_family": "evidence",
                        "input_bytes": int(trace_source.stat().st_size) if trace_source.exists() else None,
                        "sidecar_bytes": int(sidecar_bytes),
                        "sidecar_row_count": int(pre_ticket_payload.row_count) if pre_ticket_payload is not None else None,
                        "ticket_present": bool(pre_ticket_payload is not None),
                        "ticket_validated": bool(loaded_ticket.ok),
                        "trace_checksum": source_trace_checksum,
                        "dictionary_checksum": source_dictionary_checksum,
                        "gate_policy_summary": {
                            "advisor_enabled": advisor_enabled,
                            "ticket_fast_path_enabled": True,
                        },
                        "background_prebuild_reused": bool(background_prebuild_candidate is not None),
                    }
                    pre_advisor_result = RuntimeOptimizationAdvisor(advisor_config).evaluate_result(
                        telemetry_history=advisor_telemetry_history,
                        current_request_features=pre_execution_features,
                        sidecar_ticket=pre_ticket_payload,
                        job_id=pre_execution_advisor_job_id,
                    )
                    pre_advisor_payload = dict(pre_advisor_result.data or {})
                    pre_execution_agent_contract = dict(pre_advisor_payload.get("agent_contract") or {})
                    pre_execution_advisor_metadata = dict(pre_advisor_payload.get("advisor_metadata") or {})
                    pre_execution_evidence_context = dict(pre_advisor_payload.get("advisor_evidence_context") or {})
                    if not pre_advisor_result.ok:
                        try:
                            write_evidence_advisor_artifacts(
                                package_path,
                                pre_execution_agent_contract=pre_execution_agent_contract,
                                snapshot_id=snapshot_id,
                            )
                        except Exception:
                            pass
                        _progress("advisor/evaluate", "failed")
                        return Result(
                            code=pre_advisor_result.code,
                            message=pre_advisor_result.message,
                            data=pre_advisor_result.data,
                        )
                    pre_execution_advisor_decision = pre_advisor_payload["advisor_decision"]
                    pre_gate = advisor_gate.validate_advisor_decision(
                        advisor_decision=pre_execution_advisor_decision,
                        sidecar_ticket=pre_ticket_payload,
                        sidecar_manifest=sidecar_manifest_source_payload,
                        request_context={
                            "sidecar_path": str(sidecar_source_path),
                            "index_path": str(sidecar_index_path),
                            "ticket_path": str(ticket_path),
                            "snapshot_id": snapshot_id,
                            "trace_checksum": source_trace_checksum,
                            "dictionary_checksum": source_dictionary_checksum,
                            "sidecar_checksum": sidecar_checksum,
                        },
                        policy={"advisor_enabled": advisor_enabled, "ticket_fast_path_enabled": True},
                    )
                    pre_execution_gate_result = pre_gate.to_dict()
                    advisor_execution_plan = list(pre_gate.execution_plan) if pre_gate.accepted else []
                    advisor_gate_result = pre_execution_gate_result
                    pre_execution_advisor_trace = build_advisor_trace(
                        request_id=pre_execution_advisor_job_id,
                        feature_snapshot={
                            "advisor_phase": "pre_execution",
                            "pre_execution_features": dict(pre_execution_features),
                            "evidence_context": pre_execution_evidence_context,
                        },
                        decision=pre_execution_advisor_decision,
                        gate_result=pre_execution_gate_result,
                        started_at=pre_execution_advisor_started_at,
                        agent_contract_ref=pre_execution_agent_contract_ref,
                    )
                    pre_execution_advisor_overhead_seconds = pre_execution_advisor_trace.advisor_overhead_seconds
                    pre_execution_advisor_trace_ref = {
                        "path": "control/pre_execution_advisor_trace.json",
                        "job_id": pre_execution_advisor_job_id,
                    }
                    _emit_progress_payload(
                        {
                            "category": "evidence_export",
                            "substage": "advisor/evaluate",
                            "status": "completed" if pre_gate.accepted else "rejected",
                            "advisor_phase": "pre_execution",
                            "advisor_mode": pre_execution_advisor_decision.advisor_mode,
                            "advisor_execution_plan": list(advisor_execution_plan),
                            "rejected_reason": pre_gate.rejected_reason,
                        }
                    )
                if loaded_ticket.ok:
                    gate_result = advisor_gate.validate_ticket_fast_path(
                        sidecar_manifest=sidecar_manifest_source_payload,
                        request_context={
                            "sidecar_path": str(sidecar_source_path),
                            "index_path": str(sidecar_index_path),
                            "ticket_path": str(ticket_path),
                            "snapshot_id": snapshot_id,
                            "trace_checksum": source_trace_checksum,
                            "dictionary_checksum": source_dictionary_checksum,
                            "sidecar_checksum": sidecar_checksum,
                        },
                        policy={"ticket_fast_path_enabled": True},
                        sidecar_ticket=loaded_ticket.data,
                    )
                    advisor_gate_result = gate_result.to_dict()
                    if gate_result.accepted:
                        ticket = loaded_ticket.data
                        sidecar_ticket_for_advisor = ticket
                        validated_fingerprint = tuple(ticket.file_fingerprint)
                        sidecar_selector_telemetry["sidecar_row_count"] = int(ticket.row_count)
                        sidecar_selector_telemetry["sidecar_ticket_fast_path"] = True
                        ticket_fast_path = True
                        _emit_progress_payload(
                            {
                                "category": "evidence_export",
                                "substage": "sidecar/ticket",
                                "status": "completed",
                                "sidecar_index_ticket_path": str(ticket_path),
                                "sidecar_row_count": int(ticket.row_count),
                            }
                        )
                    else:
                        _emit_progress_payload(
                            {
                                "category": "evidence_export",
                                "substage": "sidecar/ticket",
                                "status": "rejected",
                                "sidecar_index_ticket_path": str(ticket_path),
                                "rejected_reason": gate_result.rejected_reason,
                            }
                        )
                else:
                    _emit_progress_payload(
                        {
                            "category": "evidence_export",
                            "substage": "sidecar/ticket",
                            "status": "missing",
                            "sidecar_index_ticket_path": str(ticket_path),
                        }
                    )

                if validated_fingerprint is None:
                    candidate_consumption_diagnostics["candidate_stream_validate_used"] = True
                    validation_started = time.perf_counter()
                    streamed = validate_dependency_sidecar_stream(
                        effective_sidecar_source,
                        sidecar_manifest_source_payload,
                        expected_snapshot_id=snapshot_id,
                        expected_trace_checksum=source_trace_checksum,
                    )
                    sidecar_selector_telemetry["sidecar_validate_seconds"] = round(
                        time.perf_counter() - validation_started,
                        6,
                    )
                    if not streamed.ok:
                        if background_prebuild_candidate is not None and not explicit_sidecar_requested:
                            _fallback_to_local_sidecar()
                        elif request.closure_policy.allow_degraded:
                            sidecar_seed_rows = []
                            outcome = EvidenceClosureOutcome(
                                closure_mode="degraded",
                                halt_reason="SIDECAR_MISMATCH",
                                round_id=0,
                                seed_refs=[],
                                closed_refs=[],
                                selected_refs=[],
                                degraded_code="SIDECAR_MISMATCH",
                                degraded_message=streamed.message,
                            )
                            _progress("sidecar/validate", "failed")
                        else:
                            _progress("sidecar/validate", "failed")
                            return streamed
                    else:
                        validated_fingerprint = tuple(streamed.data["file_fingerprint"])
                        sidecar_checksum = str(streamed.data["checksum"])
                        sidecar_selector_telemetry["sidecar_row_count"] = int(streamed.data.get("row_count") or 0)
                        sidecar_selector_telemetry["sidecar_bytes_scanned"] = int(sidecar_bytes)

                if outcome is None and validated_fingerprint is not None:
                    effective_fallback_allowed = bool(fallback_allowed and not ticket_fast_path)
                    _emit_progress_payload(
                        {
                            "category": "evidence_export",
                            "substage": "sidecar/index",
                            "status": "started",
                            "sidecar_bytes": int(sidecar_bytes),
                        }
                    )
                    if sidecar_segment_manifest_source_payload is not None and sidecar_manifest_root is not None:
                        sidecar_selector_telemetry["sidecar_selector_mode"] = "segmented_sqlite"
                        _emit_progress_payload(
                            {
                                "category": "evidence_export",
                                "substage": "sidecar/index",
                                "status": "completed",
                                "sidecar_selector_mode": "segmented_sqlite",
                                "sidecar_segment_count": int(
                                    sidecar_segment_manifest_source_payload.get("segment_count", 0) or 0
                                ),
                            }
                        )

                        def _select_sidecar_candidates(
                            frontier_refs: list[str],
                            rule_families: tuple[str, ...],
                            *,
                            _segment_manifest: dict[str, Any] = sidecar_segment_manifest_source_payload,
                            _segment_root: Path = sidecar_manifest_root,
                            _snapshot_id: str = snapshot_id,
                            _trace_checksum: str = source_trace_checksum,
                            _dictionary_checksum: str = source_dictionary_checksum,
                        ) -> Result[list[Any]]:
                            sidecar_selector_telemetry["sidecar_selector_calls"] = int(
                                sidecar_selector_telemetry["sidecar_selector_calls"]
                            ) + 1
                            return select_candidate_edges_from_segment_manifest(
                                _segment_manifest,
                                manifest_root=_segment_root,
                                frontier_refs=frontier_refs,
                                rule_families=rule_families,
                                expected_snapshot_id=_snapshot_id,
                                expected_trace_checksum=_trace_checksum,
                                expected_dictionary_checksum=_dictionary_checksum,
                            )

                        sidecar_candidate_selector = _select_sidecar_candidates
                        sidecar_selector_telemetry["background_prebuild_reused"] = bool(
                            background_prebuild_candidate is not None
                        )
                        _progress("sidecar/validate", "completed")
                        sidecar_validate_completed = True
                    else:
                        indexed = build_or_open_sidecar_index(
                            sidecar_source_path,
                            expected_snapshot_id=snapshot_id,
                            expected_trace_checksum=source_trace_checksum,
                            sidecar_checksum=sidecar_checksum,
                            file_fingerprint=validated_fingerprint,
                            dictionary_checksum=source_dictionary_checksum,
                            index_path=sidecar_index_path,
                            ticket_path=ticket_path,
                            rebuild_on_mismatch=not ticket_fast_path,
                        )
                        if indexed.ok:
                            index_handle = indexed.data
                            sidecar_selector_telemetry["sidecar_selector_mode"] = index_handle.selector_mode
                            sidecar_selector_telemetry["sidecar_index_build_seconds"] = float(index_handle.build_seconds)
                            sidecar_selector_telemetry["sidecar_index_rebuilt"] = bool(index_handle.built)
                            sidecar_selector_telemetry["sidecar_index_reused"] = not bool(index_handle.built)
                            sidecar_selector_telemetry["sidecar_row_count"] = int(index_handle.row_count)
                            if sidecar_ticket_for_advisor is None:
                                refreshed_ticket = read_sidecar_index_ticket(ticket_path)
                                if refreshed_ticket.ok:
                                    sidecar_ticket_for_advisor = refreshed_ticket.data
                            sidecar_selector_telemetry["sidecar_bytes_scanned"] = int(
                                sidecar_selector_telemetry["sidecar_bytes_scanned"]
                            ) + int(index_handle.bytes_scanned)
                            _emit_progress_payload(
                                {
                                    "category": "evidence_export",
                                    "substage": "sidecar/index",
                                    "status": "completed",
                                    "sidecar_index_path": str(index_handle.path),
                                    "sidecar_selector_mode": str(index_handle.selector_mode),
                                    "sidecar_index_rebuilt": bool(index_handle.built),
                                    "sidecar_index_build_seconds": float(index_handle.build_seconds),
                                }
                            )

                            def _select_sidecar_candidates(
                                frontier_refs: list[str],
                                rule_families: tuple[str, ...],
                                *,
                                _index_handle=index_handle,
                                _sidecar_source: Path = sidecar_source_path,
                                _file_fingerprint: tuple[int, int, int, int] = validated_fingerprint,
                                _sidecar_checksum: str = sidecar_checksum,
                                _sidecar_bytes: int = int(sidecar_bytes),
                                _fallback_allowed: bool = bool(effective_fallback_allowed),
                            ) -> Result[list[Any]]:
                                sidecar_selector_telemetry["sidecar_selector_calls"] = int(
                                    sidecar_selector_telemetry["sidecar_selector_calls"]
                                ) + 1
                                selected = select_candidate_edges_from_index(
                                    _index_handle,
                                    frontier_refs,
                                    rule_families,
                                    expected_sidecar_checksum=_sidecar_checksum,
                                    expected_file_fingerprint=_file_fingerprint,
                                )
                                if selected.ok or not _fallback_allowed:
                                    return selected
                                sidecar_selector_telemetry["sidecar_selector_mode"] = "stream_scan"
                                sidecar_selector_telemetry["sidecar_bytes_scanned"] = int(
                                    sidecar_selector_telemetry["sidecar_bytes_scanned"]
                                ) + int(_sidecar_bytes)
                                return select_candidate_edges_from_sidecar(
                                    _sidecar_source,
                                    frontier_refs,
                                    rule_families,
                                    file_fingerprint=_file_fingerprint,
                                )

                            sidecar_candidate_selector = _select_sidecar_candidates
                            sidecar_selector_telemetry["background_prebuild_reused"] = bool(
                                background_prebuild_candidate is not None
                            )
                            _progress("sidecar/validate", "completed")
                            sidecar_validate_completed = True
                        else:
                            _emit_progress_payload(
                                {
                                    "category": "evidence_export",
                                    "substage": "sidecar/index",
                                    "status": "failed" if not fallback_allowed else "fallback",
                                    "sidecar_index_error_code": indexed.code,
                                    "sidecar_index_error_message": indexed.message,
                                }
                            )
                            if fallback_allowed:
                                sidecar_selector_telemetry["sidecar_selector_mode"] = "stream_scan"

                                def _select_sidecar_candidates(
                                    frontier_refs: list[str],
                                    rule_families: tuple[str, ...],
                                    *,
                                    _sidecar_source: Path = sidecar_source_path,
                                    _file_fingerprint: tuple[int, int, int, int] = validated_fingerprint,
                                    _sidecar_bytes: int = int(sidecar_bytes),
                                ) -> Result[list[Any]]:
                                    sidecar_selector_telemetry["sidecar_selector_calls"] = int(
                                        sidecar_selector_telemetry["sidecar_selector_calls"]
                                    ) + 1
                                    sidecar_selector_telemetry["sidecar_bytes_scanned"] = int(
                                        sidecar_selector_telemetry["sidecar_bytes_scanned"]
                                    ) + int(_sidecar_bytes)
                                    return select_candidate_edges_from_sidecar(
                                        _sidecar_source,
                                        frontier_refs,
                                        rule_families,
                                        file_fingerprint=_file_fingerprint,
                                    )

                                sidecar_candidate_selector = _select_sidecar_candidates
                                sidecar_selector_telemetry["background_prebuild_reused"] = bool(
                                    background_prebuild_candidate is not None
                                )
                                _progress("sidecar/validate", "completed")
                                sidecar_validate_completed = True
                            else:
                                indexed_message = (
                                    "large dependency sidecar requires sqlite index before mode_b closure: "
                                    f"{indexed.message}"
                                )
                                if background_prebuild_candidate is not None and not explicit_sidecar_requested:
                                    _fallback_to_local_sidecar()
                                elif request.closure_policy.allow_degraded:
                                    sidecar_seed_rows = []
                                    outcome = EvidenceClosureOutcome(
                                        closure_mode="degraded",
                                        halt_reason="SIDECAR_MISMATCH",
                                        round_id=0,
                                        seed_refs=[],
                                        closed_refs=[],
                                        selected_refs=[],
                                        degraded_code="SIDECAR_MISMATCH",
                                        degraded_message=indexed_message,
                                    )
                                    _progress("sidecar/validate", "failed")
                                else:
                                    _progress("sidecar/validate", "failed")
                                    return err_result(indexed.code, indexed_message)
    if outcome is None and request.embodiment_mode != "mode_b" and not external_sidecar_requested:
        validated = validate_sidecar_payload(
            sidecar_seed_rows,
            _build_sidecar_manifest_draft(
                snapshot_id=snapshot_id,
                trace_checksum=source_trace_checksum,
                dictionary_checksum=source_dictionary_checksum,
                rule_family=request.rule_family,
            ),
            expected_snapshot_id=snapshot_id,
            expected_trace_checksum=source_trace_checksum,
            expected_dictionary_checksum=source_dictionary_checksum,
            require_non_empty=True,
        )
        if not validated.ok:
            if request.closure_policy.allow_degraded:
                sidecar_seed_rows = []
                outcome = EvidenceClosureOutcome(
                    closure_mode="degraded",
                    halt_reason="SIDECAR_MISMATCH",
                    round_id=0,
                    seed_refs=[],
                    closed_refs=[],
                    selected_refs=[],
                    degraded_code="SIDECAR_MISMATCH",
                    degraded_message=validated.message,
                )
                _progress("sidecar/validate", "failed")
            else:
                _progress("sidecar/validate", "failed")
                return validated
        else:
            _progress("sidecar/validate", "completed")
            sidecar_validate_completed = True
    elif outcome is not None and not sidecar_validate_completed:
        _progress("sidecar/validate", "failed")

    seed_resolution = None
    if outcome is None:
        _progress("seed/resolve", "started")
        seed_result = resolve_seed_refs(
            request,
            analysis_context,
            bundle,
            alerts,
            diagnoses,
            anchor_rows,
            ref_index_rows,
        )
        if seed_result.ok:
            seed_resolution = seed_result.data
            _progress("seed/resolve", "completed")
            outcome = execute_evidence_closure(
                bundle,
                request,
                seed_result.data,
                sidecar_seed_rows,
                trace_source=trace_source,
                ref_index_rows=ref_index_rows,
                emit_progress=_emit_progress_payload,
                sidecar_candidate_selector=sidecar_candidate_selector,
            )
        elif request.closure_policy.allow_degraded:
            _progress("seed/resolve", "failed")
            _progress("seed/materialize", "failed")
            outcome = EvidenceClosureOutcome(
                closure_mode="degraded",
                halt_reason="SEED_EMPTY",
                round_id=0,
                seed_refs=[],
                closed_refs=[],
                selected_refs=[],
                degraded_code=seed_result.code,
                degraded_message=seed_result.message,
            )
        else:
            _progress("seed/resolve", "failed")
            _progress("seed/materialize", "failed")
            return seed_result
    assert outcome is not None

    round_read_results = [dict(round_payload.get("read_result") or {}) for round_payload in list(outcome.rounds)]
    event_by_ref = {event.ref_key: event for event in bundle.event_stream}
    selected_events: list[UnifiedEvent]
    if bundle.event_stream:
        selected_events = [
            event_by_ref[ref_key]
            for ref_key in outcome.selected_refs
            if ref_key in event_by_ref
        ]
        selected_events = sorted(selected_events, key=evd_StableEventSortKey)
    else:
        source_selected_refs = _ordered_selected_refs(
            ref_index_rows,
            selected_refs=outcome.selected_refs,
            fallback_refs=[
                ref_key
                for read_result in round_read_results
                for ref_key in list(read_result.get("matched_refs") or [])
            ],
        )
        if seed_resolution is not None:
            source_selected_refs = _ordered_selected_refs(
                ref_index_rows,
                selected_refs=source_selected_refs,
                fallback_refs=list(seed_resolution.scope_events),
            )
        source_selected = _source_backed_selected_events(
            trace_source,
            bundle,
            source_selected_refs,
            dictionary=dictionary_source,
        )
        if not source_selected.ok:
            return source_selected
        selected_events = list(source_selected.data)
        event_by_ref = {event.ref_key: event for event in selected_events}
    selected_ref_set = {str(event.ref_key) for event in selected_events}
    read_target_ref_set = {
        str(ref_key)
        for read_result in round_read_results
        for ref_key in list(read_result.get("target_refs") or [])
        if str(ref_key).strip()
    }
    read_matched_ref_set = {
        str(ref_key)
        for read_result in round_read_results
        for ref_key in list(read_result.get("matched_refs") or [])
        if str(ref_key).strip()
    }
    read_missed_ref_set = {
        str(ref_key)
        for read_result in round_read_results
        for ref_key in list(read_result.get("missed_refs") or [])
        if str(ref_key).strip()
    }
    read_target_ref_count = int(len(read_target_ref_set))
    read_matched_ref_count = int(len(read_matched_ref_set))
    read_missed_ref_count = int(len(read_missed_ref_set))
    read_window_hit_rate = 1.0 if read_target_ref_count == 0 else float(read_matched_ref_count / read_target_ref_count)
    selected_ref_count_from_window_read = int(len(selected_ref_set.intersection(read_matched_ref_set)))
    selected_ref_count_from_seed_materialization = int(len(selected_ref_set.difference(read_matched_ref_set)))
    selected_ref_overlap_count = int(len(selected_ref_set.intersection(read_matched_ref_set)))
    blocker_window_count = sum(int(read_result.get("window_count", 0)) for read_result in round_read_results)
    blocker_selected_chunk_count = sum(int(read_result.get("selected_chunk_count", 0)) for read_result in round_read_results)
    blocker_planned_span_count = sum(int(read_result.get("planned_span_count", 0)) for read_result in round_read_results)
    blocker_selected_span_count = sum(int(read_result.get("selected_span_count", 0)) for read_result in round_read_results)
    blocker_failed_read_count = sum(1 for read_result in round_read_results if read_result.get("read_ok") is False)
    read_metrics = {
        "scan_count": int(outcome.scan_count),
        "seek_count": int(outcome.seek_count),
        "window_span_total": int(outcome.window_span_total),
    }
    analysis_bundle: RebuildBundle | None = None
    if request.embodiment_mode != "mode_b" and delay_analysis_until_selected:
        analysis_bundle = _subset_rebuild_bundle(bundle, selected_events, export_window)
        if analysis_bundle.event_stream:
            analysis_result = _run_analysis_on_bundle(
                record.metric_session.cfg,
                analysis_bundle,
                export_window=export_window,
                export_filter=export_filter,
            )
            if not analysis_result.ok:
                return analysis_result
            alerts, diagnoses = analysis_result.data
        else:
            alerts, diagnoses = [], []
        anchor_rows = _iter_anchor_rows(analysis_context, alerts, diagnoses)
    rebuild_bundle = _subset_rebuild_bundle(bundle, selected_events, export_window)
    if not bundle.event_stream:
        rebuild_bundle.event_stream = []

    event_trace_path = package_path / "event" / "events.trace"
    package_write_started_at = time.perf_counter()
    _progress("write/event", "started")
    ref_index = _materialized_ref_index_rows(selected_events)
    event_records = [_event_record_from_unified_event(event) for event in selected_events]
    write_evidence_event_payload(
        package_path,
        trace_callback=lambda target: encode_trace(
            target,
            event_records,
            producer_ver=record.artifact.header.producer_ver,
            run_id=record.artifact.header.run_id or dataset_id,
        ),
        ref_index_rows=ref_index,
        event_count=len(selected_events),
        snapshot_id=snapshot_id,
    )
    _progress("write/event", "completed")

    _progress("write/rebuild", "started")
    write_evidence_rebuild_payload(package_path, rebuild_bundle=rebuild_bundle, snapshot_id=snapshot_id)
    _progress("write/rebuild", "completed")

    _progress("write/result", "started")
    result_validity_rows = write_evidence_result_payload(
        package_path,
        alerts=alerts,
        diagnoses=diagnoses,
        snapshot_id=snapshot_id,
    )
    _progress("write/result", "completed")
    write_evidence_context_payload(
        package_path,
        analysis_context=analysis_context,
        compare_scope=compare_scope,
        anchor_rows=anchor_rows,
        snapshot_id=snapshot_id,
    )

    dict_ref, schema_ref = write_evidence_reference_assets(package_path, schema_key_map=SCHEMA_KEY_MAP, snapshot_id=snapshot_id)
    trace_checksum = checksum_file(event_trace_path)
    dictionary_checksum = checksum_file(package_path / dict_ref["path"])
    sidecar_rows = materialize_dependency_sidecar(list(outcome.workset_edges), trace_checksum=trace_checksum)

    bytes_emitted = int(event_trace_path.stat().st_size) if event_trace_path.exists() else 0
    _progress(
        "finalize/proof",
        "started",
        round_id=int(outcome.round_id),
        frontier_count=int(outcome.frontier_count),
        emitted_events=len(selected_events),
        emitted_bytes=int(bytes_emitted),
    )
    missing_required_refs = len(list(outcome.missing_required_refs))
    budget_halt_reasons = {"DEPTH_LIMIT", "EVENT_LIMIT", "BYTE_LIMIT", "RHO_LIMIT"}
    pre_read_reject = bool(
        str(outcome.halt_reason) in budget_halt_reasons
        and int(outcome.frontier_count) > 0
        and str(outcome.closure_mode) in {"bounded", "degraded"}
    )
    freeze_round_id = int(outcome.round_id) + (1 if pre_read_reject else 0)
    pre_read_reject_round_id = freeze_round_id if pre_read_reject else None
    frontier_refs_rel = "control/frontier_refs.jsonl" if outcome.frontier_rows else None
    frontier_snapshot = FrontierSnapshot(
        round_id=int(outcome.round_id),
        closure_mode=outcome.closure_mode,
        frontier_count=int(outcome.frontier_count),
        consumed_depth=int(outcome.round_id),
        consumed_events=int(len(selected_events)),
        consumed_bytes=int(bytes_emitted),
        projected_next_events=int(outcome.projected_next_events),
        projected_next_bytes=int(outcome.projected_next_bytes),
        expansion_ratio=float(outcome.expansion_ratio),
        halt_reason=outcome.halt_reason,
        frontier_refs_path=frontier_refs_rel,
        truncated_frontier_count=int(outcome.truncated_frontier_count),
        freeze_round_id=int(freeze_round_id),
        pre_read_reject=bool(pre_read_reject),
        pre_read_reject_round_id=(int(pre_read_reject_round_id) if pre_read_reject_round_id is not None else None),
    )
    proof_digest_payload = {
        "snapshot_id": snapshot_id,
        "closure_mode": outcome.closure_mode,
        "complete_wrt_rule_family": outcome.closure_mode == "exact",
        "rule_family": list(request.rule_family),
        "budget_vector": request.budget_vector.to_dict(),
        "closure_depth_reached": int(outcome.round_id),
        "seed_ref_count": len(outcome.seed_refs),
        "closed_ref_count": len(outcome.closed_refs),
        "missing_required_refs": int(missing_required_refs),
        "truncated_frontier_count": int(outcome.truncated_frontier_count),
        "frontier_halt_reason": outcome.halt_reason,
        "events_emitted": len(selected_events),
        "bytes_emitted": int(bytes_emitted),
        "scan_count": int(read_metrics["scan_count"]),
        "seek_count": int(read_metrics["seek_count"]),
        "window_span_total": int(read_metrics["window_span_total"]),
        "sidecar_lookup_count": int(outcome.sidecar_lookup_count),
        "round_count": int(len(outcome.rounds)),
        "window_hit_rate": float(read_window_hit_rate),
        "peak_rss_mb": _current_process_peak_rss_mb(),
    }
    if request.embodiment_mode == "mode_b":
        proof_digest_payload.update(
            {
                "sidecar_bytes": (
                    None
                    if sidecar_selector_telemetry["sidecar_bytes"] is None
                    else int(sidecar_selector_telemetry["sidecar_bytes"])
                ),
                "sidecar_selector_mode": (
                    None
                    if sidecar_selector_telemetry["sidecar_selector_mode"] is None
                    else str(sidecar_selector_telemetry["sidecar_selector_mode"])
                ),
                "sidecar_selector_calls": int(sidecar_selector_telemetry["sidecar_selector_calls"]),
                "sidecar_bytes_scanned": int(sidecar_selector_telemetry["sidecar_bytes_scanned"]),
                "sidecar_index_build_seconds": float(sidecar_selector_telemetry["sidecar_index_build_seconds"]),
            }
        )
    proof_digest = ProofDigest(
        **proof_digest_payload,
        proof_hash=evd_RecomputeProofHash(proof_digest_payload),
    )

    blocker_artifact = None
    if outcome.closure_mode == "degraded":
        blocker_artifact = BlockerArtifact(
            snapshot_id=snapshot_id,
            round_id=int(outcome.round_id),
            closure_mode=outcome.closure_mode,
            halt_reason=outcome.halt_reason,
            exception_code=outcome.degraded_code,
            exception_message=outcome.degraded_message,
            candidate_edge_sample=list(outcome.candidate_edge_sample),
            window_plan_summary={
                "window_count": int(blocker_window_count),
                "window_span_total": int(read_metrics["window_span_total"]),
                "target_refs": sorted(read_target_ref_set),
                "target_ref_count": int(read_target_ref_count),
                "matched_refs": sorted(read_matched_ref_set),
                "matched_ref_count": int(read_matched_ref_count),
                "missed_refs": sorted(read_missed_ref_set),
                "missed_ref_count": int(read_missed_ref_count),
                "window_hit_rate": float(read_window_hit_rate),
                "selected_chunk_count": int(blocker_selected_chunk_count),
                "planned_span_count": int(blocker_planned_span_count),
                "selected_span_count": int(blocker_selected_span_count),
                "failed_read_count": int(blocker_failed_read_count),
            },
            partial_write_status={
                "events_emitted": len(selected_events),
                "bytes_emitted": int(bytes_emitted),
            },
            emitted_metrics={
                "scan_count": int(proof_digest.scan_count),
                "seek_count": int(proof_digest.seek_count),
                "window_span_total": int(proof_digest.window_span_total),
                "sidecar_lookup_count": int(proof_digest.sidecar_lookup_count),
                "selected_ref_count_from_window_read": int(selected_ref_count_from_window_read),
                "selected_ref_count_from_seed_materialization": int(selected_ref_count_from_seed_materialization),
                "selected_ref_overlap_count": int(selected_ref_overlap_count),
                "read_target_ref_count": int(read_target_ref_count),
                "read_matched_ref_count": int(read_matched_ref_count),
                "read_missed_ref_count": int(read_missed_ref_count),
                "read_window_hit_rate": float(read_window_hit_rate),
            },
        )
    _progress(
        "finalize/proof",
        "completed",
        round_id=int(outcome.round_id),
        frontier_count=int(outcome.frontier_count),
        emitted_events=len(selected_events),
        emitted_bytes=int(bytes_emitted),
    )
    export_time = _iso_now()
    _progress("write/control", "started")
    control_plane = write_evidence_control_plane(
        package_path,
        sidecar_rows=sidecar_rows,
        frontier_rows=list(outcome.frontier_rows),
        frontier_snapshot=frontier_snapshot,
        proof_digest=proof_digest,
        schema_ref=schema_ref,
        request_rule_family=request.rule_family,
        snapshot_id=snapshot_id,
        trace_checksum=trace_checksum,
        dictionary_checksum=dictionary_checksum,
        created_at=export_time,
        generator_version=EVIDENCE_PARSER_VERSION,
        blocker_artifact=blocker_artifact,
    )
    _progress("write/control", "completed")
    candidate_package_sidecar_checksum = str(
        dict(control_plane["sidecar_manifest"].get("entry_checksums") or {}).get("control/dependency_sidecar.jsonl")
        or ""
    )
    background_prebuild_sidecar_checksum = str(candidate_consumption_diagnostics.get("background_prebuild_sidecar_checksum") or "")
    candidate_consumption_diagnostics.update(
        {
            "candidate_package_sidecar_write_count": 1,
            "candidate_used_background_prebuild": bool(sidecar_selector_telemetry["background_prebuild_reused"]),
            "candidate_ticket_fast_path_used": bool(sidecar_selector_telemetry["sidecar_ticket_fast_path"]),
            "candidate_sqlite_index_rebuilt": bool(sidecar_selector_telemetry["sidecar_index_rebuilt"]),
            "candidate_stream_scan_fallback_used": str(sidecar_selector_telemetry["sidecar_selector_mode"] or "")
            == "stream_scan",
            "candidate_package_sidecar_checksum": candidate_package_sidecar_checksum or None,
            "candidate_full_sidecar_duplicate_write_detected": bool(
                candidate_package_sidecar_checksum
                and background_prebuild_sidecar_checksum
                and candidate_package_sidecar_checksum == background_prebuild_sidecar_checksum
            ),
            "candidate_full_sidecar_duplicate_write_heuristic_only": False,
        }
    )
    frontier_refs_rel = control_plane["frontier_refs_rel"]
    sidecar_segment_manifest_rel = control_plane["sidecar_segment_manifest_rel"]
    sidecar_segments = control_plane["sidecar_segments"]
    control_refs = control_plane["control_refs"]
    advisor_trace_rel = None
    advisor_contract_rel = None
    advisor_report_rel = None
    pre_execution_advisor_trace_rel = None
    pre_execution_advisor_contract_rel = None
    if pre_execution_advisor_trace is not None or pre_execution_agent_contract is not None:
        pre_execution_advisor_trace_rel = (
            "control/pre_execution_advisor_trace.json"
            if pre_execution_advisor_trace is not None
            else None
        )
        pre_execution_advisor_contract_rel = (
            "control/pre_execution_runtime_advisor_agent_contract.json"
            if pre_execution_agent_contract is not None
            else None
        )
        write_evidence_advisor_artifacts(
            package_path,
            pre_execution_advisor_trace=pre_execution_advisor_trace,
            pre_execution_agent_contract=pre_execution_agent_contract,
            snapshot_id=snapshot_id,
        )
        if pre_execution_advisor_trace_rel is not None:
            control_refs["pre_execution_advisor_trace"] = pre_execution_advisor_trace_rel
        if pre_execution_advisor_contract_rel is not None:
            control_refs["pre_execution_runtime_advisor_agent_contract"] = pre_execution_advisor_contract_rel
    if advisor_enabled:
        _progress("advisor/evaluate", "started")
        advisor_started_at = time.perf_counter()
        advisor_feature_snapshot = {
            "dataset_id": dataset_id,
            "run_id": str(job.get("run_id") or record.artifact.header.run_id or dataset_id),
            "embodiment_mode": request.embodiment_mode,
            "export_family": "evidence",
            "advisor_phase": "post_execution_report",
            "pre_execution_decision": (
                pre_execution_advisor_decision.to_dict() if pre_execution_advisor_decision is not None else None
            ),
            "pre_execution_gate_result": pre_execution_gate_result,
            "pre_execution_advisor_overhead_seconds": pre_execution_advisor_overhead_seconds,
            "advisor_execution_plan": list(advisor_execution_plan),
            "input_bytes": int(trace_source.stat().st_size) if trace_source.exists() else None,
            "sidecar_bytes": sidecar_selector_telemetry["sidecar_bytes"],
            "sidecar_row_count": sidecar_selector_telemetry["sidecar_row_count"],
            "sidecar_bytes_scanned": sidecar_selector_telemetry["sidecar_bytes_scanned"],
            "sidecar_validate_seconds": sidecar_selector_telemetry["sidecar_validate_seconds"],
            "index_build_open_seconds": sidecar_selector_telemetry["sidecar_index_build_seconds"],
            "index_reused": sidecar_selector_telemetry["sidecar_index_reused"],
            "index_rebuilt": sidecar_selector_telemetry["sidecar_index_rebuilt"],
            "sidecar_ticket_fast_path": sidecar_selector_telemetry["sidecar_ticket_fast_path"],
            "ticket_present": bool(sidecar_ticket_for_advisor is not None),
            "ticket_validated": bool(sidecar_selector_telemetry["sidecar_ticket_fast_path"]),
            "background_prebuild_reused": sidecar_selector_telemetry["background_prebuild_reused"],
            "trace_checksum": source_trace_checksum,
            "dictionary_checksum": source_dictionary_checksum,
            "gate_policy_summary": {
                "advisor_enabled": advisor_enabled,
                "ticket_fast_path_enabled": True,
            },
            "candidate_consumption_diagnostics": dict(candidate_consumption_diagnostics),
            "package_write_seconds": round(time.perf_counter() - package_write_started_at, 6),
            "runtime_seconds": round(time.perf_counter() - export_started_at, 6),
            "peak_rss_mb": proof_digest.peak_rss_mb,
            "closure_mode": proof_digest.closure_mode,
            "proof_hash": proof_digest.proof_hash,
            "write_metrics": export_write_metrics_snapshot(),
        }
        advisor_result = RuntimeOptimizationAdvisor(advisor_config).evaluate_result(
            telemetry_history=advisor_telemetry_history,
            current_request_features=advisor_feature_snapshot,
            sidecar_ticket=sidecar_ticket_for_advisor,
            job_id=job_id,
        )
        if not advisor_result.ok:
            _progress("advisor/evaluate", "failed")
            return Result(code=advisor_result.code, message=advisor_result.message, data=advisor_result.data)
        advisor_payload = dict(advisor_result.data or {})
        advisor_decision = advisor_payload["advisor_decision"]
        advisor_contract = dict(advisor_payload["agent_contract"])
        advisor_metadata = dict(advisor_payload.get("advisor_metadata") or {})
        advisor_evidence_context = dict(advisor_payload.get("advisor_evidence_context") or {})
        if advisor_evidence_context:
            advisor_feature_snapshot["evidence_context"] = advisor_evidence_context
        advisor_gate_result = advisor_gate.validate_advisor_decision(
            advisor_decision=advisor_decision,
            sidecar_ticket=sidecar_ticket_for_advisor,
            sidecar_manifest=sidecar_manifest_source_payload,
            request_context={
                "sidecar_path": effective_sidecar_source,
                "snapshot_id": snapshot_id,
                "trace_checksum": source_trace_checksum,
                "dictionary_checksum": source_dictionary_checksum,
            },
            policy={"advisor_enabled": advisor_enabled, "ticket_fast_path_enabled": True},
        ).to_dict()
        advisor_contract_rel = "control/runtime_advisor_agent_contract.json"
        advisor_contract_ref = {
            "path": advisor_contract_rel,
            "job_id": advisor_contract.get("job_id"),
        }
        advisor_trace = build_advisor_trace(
            request_id=job_id,
            feature_snapshot=advisor_feature_snapshot,
            decision=advisor_decision,
            gate_result=advisor_gate_result,
            telemetry_refs=[proof_digest.proof_hash],
            started_at=advisor_started_at,
            agent_contract_ref=advisor_contract_ref,
        )
        advisor_trace_rel = "control/advisor_trace.json"
        advisor_report_rel = "control/advisor_report.json"
        telemetry_record = TelemetryReportAgent().build_record(
            run_id=str(job.get("run_id") or record.artifact.header.run_id or dataset_id),
            dataset_id=dataset_id,
            embodiment_mode=request.embodiment_mode,
            input_bytes=advisor_feature_snapshot["input_bytes"],
            sidecar_bytes=sidecar_selector_telemetry["sidecar_bytes"],
            sidecar_row_count=sidecar_selector_telemetry["sidecar_row_count"],
            sidecar_bytes_scanned=sidecar_selector_telemetry["sidecar_bytes_scanned"],
            sidecar_validate_seconds=sidecar_selector_telemetry["sidecar_validate_seconds"],
            index_build_open_seconds=sidecar_selector_telemetry["sidecar_index_build_seconds"],
            index_reused=sidecar_selector_telemetry["sidecar_index_reused"],
            index_rebuilt=sidecar_selector_telemetry["sidecar_index_rebuilt"],
            package_write_seconds=advisor_feature_snapshot["package_write_seconds"],
            runtime_seconds=advisor_feature_snapshot["runtime_seconds"],
            peak_rss_mb=proof_digest.peak_rss_mb,
            proof_hash=proof_digest.proof_hash,
            closure_mode=proof_digest.closure_mode,
            advisor_overhead_seconds=advisor_trace.advisor_overhead_seconds,
            sidecar_lookup_count=outcome.sidecar_lookup_count,
            write_mode="streaming_agent" if export_write_metrics_snapshot() else "standard_json",
            stage_timings={
                "package_write_seconds": advisor_feature_snapshot["package_write_seconds"],
                "sidecar_validate_seconds": sidecar_selector_telemetry["sidecar_validate_seconds"],
                "index_build_open_seconds": sidecar_selector_telemetry["sidecar_index_build_seconds"],
            },
            rss_snapshots=[{"stage": "finalize/proof", "rss_mb": proof_digest.peak_rss_mb}],
        )
        if telemetry_history_path is not None:
            try:
                TelemetryHistoryStore(telemetry_history_path).append_record(telemetry_record, job_id=job_id)
            except Exception:
                pass
        advisor_report = {
            "report_version": "runtime-advisor-report-v1",
            "generated_at": advisor_trace.generated_at,
            "request_id": job_id,
            "decision": advisor_decision.to_dict(),
            "gate_result": advisor_gate_result,
            "agent_contract_ref": advisor_contract_ref,
            "pre_execution_decision": (
                pre_execution_advisor_decision.to_dict() if pre_execution_advisor_decision is not None else None
            ),
            "pre_execution_gate_result": pre_execution_gate_result,
            "pre_execution_advisor_overhead_seconds": pre_execution_advisor_overhead_seconds,
            "effective_execution_plan": list(
                dict.fromkeys(
                    list(advisor_execution_plan)
                    + list((advisor_gate_result or {}).get("execution_plan") or [])
                )
            ),
            "telemetry_record": telemetry_record.to_dict(),
            "write_metrics": export_write_metrics_snapshot(),
            "proof_boundary": {
                "proof_hash": proof_digest.proof_hash,
                "advisor_fields_in_proof_digest": False,
            },
        }
        if advisor_metadata:
            advisor_report["advisor_metadata"] = advisor_metadata
        if pre_execution_advisor_metadata:
            advisor_report["pre_execution_advisor_metadata"] = pre_execution_advisor_metadata
        if pre_execution_agent_contract_ref is not None:
            advisor_report["pre_execution_agent_contract_ref"] = pre_execution_agent_contract_ref
        if pre_execution_advisor_trace_ref is not None:
            advisor_report["pre_execution_advisor_trace_ref"] = pre_execution_advisor_trace_ref
        write_evidence_advisor_artifacts(
            package_path,
            advisor_trace=advisor_trace,
            agent_contract=advisor_contract,
            advisor_report=advisor_report,
            snapshot_id=snapshot_id,
        )
        control_refs["advisor_trace"] = advisor_trace_rel
        control_refs["runtime_advisor_agent_contract"] = advisor_contract_rel
        control_refs["advisor_report"] = advisor_report_rel
        _progress("advisor/evaluate", "completed")
    _progress("write/meta", "started")
    try:
        meta = write_evidence_meta(
            package_path,
            dataset_id=dataset_id,
            time_unit=_time_unit_label(record.artifact.header),
            clock_source=_clock_source_label(record.artifact.header),
            dict_ver=int(record.artifact.header.dict_ver),
            parser_ver=EVIDENCE_PARSER_VERSION,
            dict_ref=dict_ref,
            schema_ref=schema_ref,
            export_window=export_window,
            rule_family=request.rule_family,
            embodiment_mode=request.embodiment_mode,
            export_time=export_time,
            snapshot_id=snapshot_id,
            run_id=str(job.get("run_id") or record.artifact.header.run_id or dataset_id),
            run_batch_id=str(job.get("run_batch_id") or f"{job_id}:batch"),
            version_id=str(job.get("version_id") or EVIDENCE_PARSER_VERSION),
            experiment_params=dict(job.get("experiment_params") or {"export_mode": "evidence"}),
            closure_mode=outcome.closure_mode,
            budget_vector=request.budget_vector.to_dict(),
            export_filter=export_filter,
            analysis_context=analysis_context,
            compare_scope=compare_scope,
            control_refs=control_refs,
            export_source={
                "dataset_id": dataset_id,
                "source": record.artifact.source,
                "job_id": job_id,
                "job_kind": "export_evidence",
                "write_mode": "evidence_controlled",
            },
            capability_flags=dict(bundle.capability_flags or {}),
            untrusted_windows=[serialize(item) for item in list(rebuild_bundle.untrusted_windows)],
        )
    except ValueError as exc:
        return err_result("INVALID_ARG", str(exc))
    _progress("write/meta", "completed")

    _progress("write/manifest", "started")
    try:
        manifest = write_evidence_manifest(
            package_path,
            package_version=EVIDENCE_PACKAGE_VERSION,
            created_at=export_time,
            snapshot_id=snapshot_id,
            dict_ref=dict_ref,
            schema_ref=schema_ref,
            producer_ver=record.artifact.header.producer_ver,
            selected_events=selected_events,
            ref_index_count=len(ref_index),
            alerts=alerts,
            diagnoses=diagnoses,
            result_validity_rows=result_validity_rows,
            anchor_rows=anchor_rows,
            dependency_sidecar_count=len(sidecar_rows),
            frontier_rows_count=len(outcome.frontier_rows),
            frontier_refs_rel=frontier_refs_rel,
            sidecar_segment_manifest_rel=sidecar_segment_manifest_rel,
            sidecar_segments=sidecar_segments,
            blocker_artifact=blocker_artifact,
            advisor_trace_rel=advisor_trace_rel,
            advisor_contract_rel=advisor_contract_rel,
            advisor_report_rel=advisor_report_rel,
            pre_execution_advisor_trace_rel=pre_execution_advisor_trace_rel,
            pre_execution_advisor_contract_rel=pre_execution_advisor_contract_rel,
            schema_names=list(SCHEMA_KEY_MAP),
        )
    except ValueError as exc:
        return err_result("INVALID_ARG", str(exc))
    _progress("write/manifest", "completed")

    return ok_result(
        {
            "package_path": str(package_path),
            "entry_count": len(list(manifest.get("entries") or [])),
            "snapshot_id": snapshot_id,
            "closure_mode": outcome.closure_mode,
            "event_count": len(selected_events),
            "alert_count": len(alerts),
            "diagnosis_count": len(diagnoses),
            "meta": meta,
            "manifest": manifest,
        }
    )

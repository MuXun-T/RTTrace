from __future__ import annotations

import importlib.util
import json
from pathlib import Path
try:
    import resource
except ImportError:  # pragma: no cover - non-Unix fallback
    resource = None
import shutil
import sys
import time
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.sample_data import write_scenario
from desktop.services import ExportService, WorkspaceController
from parser import load_dataset_with_timings
from parser.benchmark_matrix import BenchmarkReportBuilder, BenchmarkScenario, default_benchmark_scenarios
from parser.evidence_models import evd_ProofHashInput
from parser.evidence_sidecar import build_dependency_sidecar, dependency_sidecar_file_fingerprint, materialize_dependency_sidecar
from parser.evidence_sidecar_index import (
    build_or_open_sidecar_index,
    sidecar_index_path_for_source,
    sidecar_index_ticket_path_for_source,
)
from parser.result import Result, err_result, ok_result
from parser.runtime_cost_graph import build_benchmark_runtime_cost_graph, merge_runtime_cost_graphs
from parser.runtime_optimization_gate import RUNTIME_ACTION_GATE_ALLOWLIST
from parser.telemetry import TelemetryHistoryStore, TelemetryReportAgent
from spec.io import checksum_file, json_dump, json_load, jsonl_dump
from spec.schema_loader import DICTIONARY_PATH, SCHEMA_DIR, load_specs
from spec.schema_validator import validate_schema
from tool.train_runtime_advisor import train_runtime_advisor_coefficients


BENCHMARK_MANDATORY_FIELDS = sorted(evd_ProofHashInput({}).keys())


def _rss_mb() -> float | None:
    if resource is None:
        return None
    rss_kb = getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0)
    if rss_kb <= 0:
        return None
    return round(float(rss_kb) / 1024.0, 3)


def _write_external_sidecar_artifacts(
    root: Path,
    trace_path: Path,
    bundle: Any,
    *,
    snapshot_id: str,
    rule_family: tuple[str, ...],
) -> tuple[Path, Path]:
    control_dir = root / "control"
    schema_dir = root / "reference" / "schema"
    control_dir.mkdir(parents=True, exist_ok=True)
    schema_dir.mkdir(parents=True, exist_ok=True)
    ref_index_rows = [
        {
            "ref_key": event.ref_key,
            "timestamp_aligned": float(event.timestamp_aligned),
            "core_id": int(event.core_id),
            "seq": int(event.seq),
        }
        for event in sorted(bundle.event_stream, key=lambda item: (item.timestamp_aligned, item.core_id, item.seq))
    ]
    source_trace_checksum = checksum_file(trace_path)
    sidecar_rows = materialize_dependency_sidecar(
        build_dependency_sidecar(
            bundle,
            snapshot_id=snapshot_id,
            rule_families=rule_family,
            ref_index_rows=ref_index_rows,
        ),
        trace_checksum=source_trace_checksum,
    )
    sidecar_path = control_dir / "dependency_sidecar.jsonl"
    jsonl_dump(sidecar_path, sidecar_rows)

    dictionary_target = root / "reference" / "dictionary.json"
    shutil.copy2(DICTIONARY_PATH, dictionary_target)
    schema_names = {
        "dependency_sidecar_schema": "dependency_sidecar.schema.json",
        "frontier_snapshot_schema": "frontier_snapshot.schema.json",
        "frontier_refs_schema": "frontier_refs.schema.json",
        "proof_digest_schema": "proof_digest.schema.json",
        "sidecar_manifest_schema": "sidecar_manifest.schema.json",
        "blocker_artifact_schema": "blocker_artifact.schema.json",
    }
    schema_checksums: dict[str, Any] = {}
    for schema_key, filename in schema_names.items():
        target = schema_dir / filename
        shutil.copy2(SCHEMA_DIR / filename, target)
        schema_checksums[schema_key] = {
            "path": f"reference/schema/{filename}",
            "algo": "sha256",
            "checksum": checksum_file(target),
        }

    manifest_path = control_dir / "sidecar_manifest.json"
    json_dump(
        manifest_path,
        {
            "sidecar_version": "external-sidecar-1",
            "generator_version": "runtime-benchmark-runner-v1",
            "trace_checksum": source_trace_checksum,
            "dictionary_checksum": checksum_file(dictionary_target),
            "schema_checksums": schema_checksums,
            "relation_families": list(rule_family),
            "entry_paths": ["control/dependency_sidecar.jsonl"],
            "entry_checksums": {"control/dependency_sidecar.jsonl": checksum_file(sidecar_path)},
            "created_at": "2026-05-04T00:00:00+00:00",
            "snapshot_id": snapshot_id,
        },
    )
    return sidecar_path, manifest_path


def _clear_sidecar_index_artifacts(sidecar_path: Path) -> None:
    index_path = sidecar_index_path_for_source(sidecar_path)
    ticket_path = sidecar_index_ticket_path_for_source(sidecar_path, index_path=index_path)
    for path in (index_path, ticket_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _progress_duration(progress: list[dict[str, Any]], substage: str) -> float:
    started = None
    finished = None
    for item in progress:
        if item.get("substage") != substage:
            continue
        if item.get("status") == "started" and started is None:
            started = float(item["observed_at"])
        elif item.get("status") in {"completed", "failed", "rejected", "fallback"}:
            finished = float(item["observed_at"])
    if started is None or finished is None or finished < started:
        return 0.0
    return round(finished - started, 6)


def _progress_duration_optional(progress: list[dict[str, Any]], *substages: str) -> float | None:
    for substage in substages:
        if not any(item.get("substage") == substage for item in progress):
            continue
        return _progress_duration(progress, substage)
    return None


def _write_phase_duration(progress: list[dict[str, Any]]) -> float:
    starts = [
        float(item["observed_at"])
        for item in progress
        if str(item.get("substage") or "").startswith("write/") and item.get("status") == "started"
    ]
    finishes = [
        float(item["observed_at"])
        for item in progress
        if str(item.get("substage") or "").startswith("write/") and item.get("status") in {"completed", "failed"}
    ]
    if not starts or not finishes:
        return 0.0
    return round(max(finishes) - min(starts), 6)


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json_load(path)
    return dict(payload) if isinstance(payload, dict) else {}


def _scenario_payload(
    scenario: BenchmarkScenario,
    *,
    dataset_id: str,
    sidecar_path: Path,
    sidecar_manifest_path: Path,
    telemetry_history_path: Path,
    coefficients_path: Path | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_id": dataset_id,
        "embodiment_mode": "mode_b",
        "sidecar_source": str(sidecar_path),
        "sidecar_manifest_source": str(sidecar_manifest_path),
        "telemetry_history_path": str(telemetry_history_path),
        "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
        "rule_family": ["ref_ref"],
        "budget_vector": {"D_max": 16, "C_events": 64, "S_bytes": 65536, "rho_max": 8.0},
        "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 64},
        "advisor_enabled": bool(scenario.advisor_enabled),
        "advisor_mode": scenario.advisor_mode,
    }
    if scenario.optional_dependency:
        advisor_config = {"advisor_mode": scenario.advisor_mode}
        if coefficients_path is not None:
            advisor_config["coefficients_path"] = str(coefficients_path)
        payload["advisor_config"] = advisor_config
    elif scenario.advisor_mode == "openai_structured":
        payload["advisor_config"] = {
            "advisor_mode": scenario.advisor_mode,
            "llm_enabled": True,
        }
    return payload


def _artifact_refs(package_dir: Path, sidecar_path: Path, sidecar_manifest_path: Path, telemetry_history_path: Path) -> dict[str, str]:
    refs = {
        "package_path": str(package_dir),
        "manifest": str(package_dir / "manifest.json"),
        "proof_digest": str(package_dir / "control" / "proof_digest.json"),
        "sidecar_source": str(sidecar_path),
        "sidecar_manifest": str(sidecar_manifest_path),
        "telemetry_history_path": str(telemetry_history_path),
    }
    for key, path in {
        "advisor_trace": package_dir / "control" / "advisor_trace.json",
        "advisor_report": package_dir / "control" / "advisor_report.json",
        "sidecar_index": sidecar_index_path_for_source(sidecar_path),
        "sidecar_index_ticket": sidecar_index_ticket_path_for_source(sidecar_path),
    }.items():
        if path.exists():
            refs[key] = str(path)
    return refs


def _jsonl_row_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_timing_metrics(load_payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "parse_seconds",
        "align_events_seconds",
        "rebuild_seconds",
        "idx_build_seconds",
        "pipeline_rebuild_seconds",
        "load_seconds",
        "index_build_mode",
        "materialize_event_stream",
        "peak_rss_mb",
    )
    return {key: load_payload.get(key) for key in keys}


def _runtime_cost_graph_action_context(
    *,
    sidecar_manifest: dict[str, Any],
    sidecar_path: Path,
) -> dict[str, Any]:
    return {
        "snapshot_id": sidecar_manifest.get("snapshot_id"),
        "trace_checksum": sidecar_manifest.get("trace_checksum"),
        "dictionary_checksum": sidecar_manifest.get("dictionary_checksum"),
        "sidecar_checksum": checksum_file(sidecar_path) if sidecar_path.exists() else None,
    }


def _runtime_cost_graph_summary(graph_payload: dict[str, Any]) -> dict[str, Any]:
    scenario_graph = dict(list(graph_payload.get("graphs") or [{}])[0])
    action_bindings = list(scenario_graph.get("action_bindings") or [])
    observed_bindings = {
        str(binding.get("action_kind"))
        for binding in action_bindings
        if str(binding.get("action_kind") or "").strip()
    }
    return {
        "runtime_cost_graph_path_type": scenario_graph.get("path_type"),
        "runtime_cost_graph_stage_coverage": dict(scenario_graph.get("stage_coverage") or {}),
        "predicted_vs_observed": list(scenario_graph.get("predicted_vs_observed") or []),
        "formal_product_separation": bool(dict(scenario_graph.get("metadata") or {}).get("formal_product_separation")),
        "proof_contamination_check": dict(scenario_graph.get("proof_boundary") or {}),
        "advisor_action_graph_binding_complete": observed_bindings == set(RUNTIME_ACTION_GATE_ALLOWLIST),
    }


def _product_runtime_breakdown(
    *,
    load_metrics: dict[str, Any],
    runtime_seconds: float,
    sidecar_build_seconds: float | None,
    sidecar_validate_seconds: float | None,
    index_build_open_seconds: float | None,
    advisor_overhead_seconds: float | None,
    package_write_seconds: float | None,
    peak_rss_mb: float | None,
) -> dict[str, Any]:
    return {
        "product_runtime_seconds": float(runtime_seconds),
        "parse_seconds": _optional_number(load_metrics.get("parse_seconds")),
        "align_events_seconds": _optional_number(load_metrics.get("align_events_seconds")),
        "rebuild_seconds": _optional_number(load_metrics.get("rebuild_seconds")),
        "idx_build_seconds": _optional_number(load_metrics.get("idx_build_seconds")),
        "pipeline_rebuild_seconds": _optional_number(load_metrics.get("pipeline_rebuild_seconds")),
        "load_seconds": _optional_number(load_metrics.get("load_seconds")),
        "index_build_mode": load_metrics.get("index_build_mode"),
        "materialize_event_stream": load_metrics.get("materialize_event_stream"),
        "sidecar_build_seconds": sidecar_build_seconds,
        "ticket_validate_seconds": sidecar_validate_seconds,
        "index_build_open_seconds": index_build_open_seconds,
        "advisor_overhead_seconds": advisor_overhead_seconds,
        "package_write_seconds": package_write_seconds,
        "peak_rss_mb": peak_rss_mb,
    }


def _append_row_telemetry_if_advisor_report_absent(
    row: dict[str, Any],
    scenario: BenchmarkScenario,
    *,
    telemetry_history_path: Path,
    trace_path: Path,
    sidecar_path: Path,
    dataset_id: str,
) -> None:
    if row.get("status") not in {"completed", "completed_with_fallback"}:
        return
    if dict(row.get("summary_metrics") or {}).get("advisor_report_present"):
        return
    runtime_seconds = _optional_number(row.get("runtime_seconds"))
    if runtime_seconds is None:
        return
    sidecar_bytes = sidecar_path.stat().st_size if sidecar_path.exists() else None
    sidecar_bytes_scanned = row.get("sidecar_bytes_scanned")
    index_reused = bool(scenario.ticket_fast_path_enabled and sidecar_bytes_scanned == 0)
    telemetry_record = TelemetryReportAgent().build_record(
        run_id=f"runtime-benchmark-{scenario.scenario_id}",
        dataset_id=dataset_id,
        embodiment_mode="mode_b",
        input_bytes=trace_path.stat().st_size if trace_path.exists() else None,
        sidecar_bytes=sidecar_bytes,
        sidecar_row_count=_jsonl_row_count(sidecar_path),
        sidecar_bytes_scanned=sidecar_bytes_scanned,
        sidecar_validate_seconds=row.get("sidecar_validate_seconds"),
        index_build_open_seconds=row.get("index_build_open_seconds"),
        index_reused=index_reused,
        index_rebuilt=not index_reused,
        package_write_seconds=row.get("package_write_seconds"),
        runtime_seconds=runtime_seconds,
        peak_rss_mb=row.get("peak_rss_mb"),
        proof_hash=row.get("proof_hash"),
        closure_mode=dict(row.get("summary_metrics") or {}).get("closure_mode"),
        advisor_overhead_seconds=row.get("advisor_overhead_seconds") or 0.0,
        write_mode="standard_json",
        stage_timings={
            "sidecar_validate_seconds": row.get("sidecar_validate_seconds") or 0.0,
            "index_build_open_seconds": row.get("index_build_open_seconds") or 0.0,
            "package_write_seconds": row.get("package_write_seconds") or 0.0,
        },
        rss_snapshots=[{"stage": "runtime_benchmark_row", "rss_mb": row.get("peak_rss_mb")}],
    )
    telemetry_update = TelemetryHistoryStore(telemetry_history_path).append_record(
        telemetry_record,
        job_id=f"runtime-benchmark-{scenario.scenario_id}:row_telemetry",
    )
    row.setdefault("telemetry_records", []).append(telemetry_update.to_dict())


def _build_row(
    scenario: BenchmarkScenario,
    *,
    package_dir: Path,
    sidecar_path: Path,
    sidecar_manifest_path: Path,
    telemetry_history_path: Path,
    load_metrics: dict[str, Any],
    progress: list[dict[str, Any]],
    runtime_seconds: float,
    baseline_parity: dict[str, Any] | None,
    advisor_coefficients_path: Path | None = None,
    advisor_training_report: dict[str, Any] | None = None,
    advisor_training_report_path: Path | None = None,
) -> dict[str, Any]:
    proof_digest = _load_json_if_exists(package_dir / "control" / "proof_digest.json")
    advisor_report = _load_json_if_exists(package_dir / "control" / "advisor_report.json")
    advisor_metadata = dict(advisor_report.get("advisor_metadata") or {})
    telemetry_record = dict(advisor_report.get("telemetry_record") or {})
    decision = dict(advisor_report.get("decision") or {})
    gate_result = dict(advisor_report.get("gate_result") or {})
    proof_hash = str(proof_digest.get("proof_hash") or "")
    mandatory_field_set = sorted(evd_ProofHashInput(proof_digest).keys()) if proof_digest else list(BENCHMARK_MANDATORY_FIELDS)
    baseline_hash = str((baseline_parity or {}).get("proof_hash") or proof_hash)
    baseline_fields = list((baseline_parity or {}).get("mandatory_field_set") or mandatory_field_set)
    metric_diff_count = 0
    if proof_hash != baseline_hash:
        metric_diff_count += 1
    if mandatory_field_set != baseline_fields:
        metric_diff_count += 1

    sidecar_bytes_scanned = int(proof_digest.get("sidecar_bytes_scanned") or 0)
    ticket_path = sidecar_index_ticket_path_for_source(sidecar_path)
    sidecar_ticket_fast_path = bool(
        scenario.ticket_fast_path_enabled
        and ticket_path.exists()
        and sidecar_bytes_scanned == 0
        and str(proof_digest.get("sidecar_selector_mode") or "")
    )
    optional_available = (
        importlib.util.find_spec(str(scenario.optional_dependency)) is not None
        if scenario.optional_dependency
        else None
    )
    effective_advisor_mode = str(decision.get("advisor_mode") or ("disabled" if not scenario.advisor_enabled else "heuristic"))
    advisor_decision_model_ref = decision.get("model_ref")
    advisor_decision_model_checksum = decision.get("model_checksum")
    advisor_action_kinds = [
        str(action.get("action_kind") or "")
        for action in list(decision.get("proposed_actions") or [])
        if str(action.get("action_kind") or "").strip()
    ]
    training_status = None
    training_skip_reason = None
    training_rows = None
    training_model_checksum = None
    if scenario.optional_dependency:
        training_payload = dict(advisor_training_report or {})
        training_status = str(training_payload.get("status") or "not_requested")
        training_skip_reason = training_payload.get("skip_reason")
        training_rows = training_payload.get("training_rows")
        training_model_checksum = training_payload.get("model_checksum")
    advisor_coefficients_attached = bool(
        scenario.optional_dependency
        and advisor_coefficients_path is not None
        and effective_advisor_mode == "offline_coefficients"
    )
    fallback_reasons: list[str] = []
    if scenario.ticket_fast_path_enabled and not sidecar_ticket_fast_path:
        fallback_reasons.append("ticket_fast_path_not_accepted")
    if scenario.optional_dependency:
        if not optional_available:
            fallback_reasons.append(f"optional_dependency_unavailable:{scenario.optional_dependency}")
        if effective_advisor_mode != scenario.advisor_mode:
            fallback_reasons.append(f"advisor_mode_fallback:{effective_advisor_mode}")
        if advisor_training_report is not None and training_status != "trained":
            fallback_reasons.append(f"advisor_training_skipped:{training_skip_reason or training_status}")
        if not advisor_coefficients_attached:
            fallback_reasons.append("advisor_coefficients_not_attached")
    if scenario.advisor_mode == "openai_structured":
        if effective_advisor_mode != scenario.advisor_mode:
            fallback_reasons.append(f"advisor_mode_fallback:{effective_advisor_mode}")
        fallback_reason = advisor_metadata.get("fallback_reason")
        if fallback_reason:
            fallback_reasons.append(str(fallback_reason))
    fallback_reasons = list(dict.fromkeys(fallback_reasons))
    status = "completed_with_fallback" if fallback_reasons else "completed"
    artifact_refs = _artifact_refs(package_dir, sidecar_path, sidecar_manifest_path, telemetry_history_path)
    if advisor_coefficients_path is not None:
        artifact_refs["advisor_coefficients"] = str(advisor_coefficients_path)
    if advisor_training_report_path is not None:
        artifact_refs["advisor_training_report"] = str(advisor_training_report_path)
    sidecar_build_seconds = _progress_duration_optional(
        progress,
        "sidecar/build_or_load",
        "sidecar/build",
        "sidecar/write",
    )
    sidecar_validate_seconds = float(
        telemetry_record.get("sidecar_validate_seconds") or _progress_duration(progress, "sidecar/validate")
    )
    index_build_open_seconds = float(
        telemetry_record.get("index_build_open_seconds")
        or proof_digest.get("sidecar_index_build_seconds")
        or 0.0
    )
    package_write_seconds = float(telemetry_record.get("package_write_seconds") or _write_phase_duration(progress))
    advisor_overhead_seconds = float(telemetry_record.get("advisor_overhead_seconds") or 0.0)
    peak_rss_mb = proof_digest.get("peak_rss_mb") if proof_digest.get("peak_rss_mb") is not None else _rss_mb()
    expected_checksum = training_model_checksum
    observed_checksum = advisor_decision_model_checksum
    checksum_validation_result = {
        "status": "missing",
        "expected_checksum": expected_checksum,
        "observed_checksum": observed_checksum,
        "reason": "model_checksum_missing",
    }
    if scenario.advisor_mode != "offline_coefficients":
        checksum_validation_result.update({"status": "not_applicable", "reason": "advisor_mode_not_offline"})
    elif effective_advisor_mode != "offline_coefficients":
        checksum_validation_result.update(
            {
                "status": "fallback",
                "reason": advisor_metadata.get("fallback_reason") or f"advisor_mode_fallback:{effective_advisor_mode}",
            }
        )
    elif expected_checksum and observed_checksum:
        checksum_validation_result.update(
            {
                "status": "passed" if str(expected_checksum) == str(observed_checksum) else "failed",
                "reason": None if str(expected_checksum) == str(observed_checksum) else "checksum_mismatch",
            }
        )
    proof_drift = {
        "status": "clean" if metric_diff_count == 0 else "drift_detected",
        "metric_diff_count": int(metric_diff_count),
        "proof_hash_changed": bool(baseline_hash and proof_hash and proof_hash != baseline_hash),
        "mandatory_field_set_changed": bool(metric_diff_count > 0 and baseline_hash == proof_hash),
    }
    return {
        "scenario_id": scenario.scenario_id,
        "status": status,
        "runtime_seconds": float(runtime_seconds),
        "peak_rss_mb": peak_rss_mb,
        "sidecar_bytes_scanned": sidecar_bytes_scanned,
        "sidecar_validate_seconds": sidecar_validate_seconds,
        "index_build_open_seconds": index_build_open_seconds,
        "package_write_seconds": package_write_seconds,
        "advisor_overhead_seconds": advisor_overhead_seconds,
        "proof_hash": proof_hash,
        "parity_result": {
            "proof_hash": proof_hash,
            "metric_diff_count": int(metric_diff_count),
            "mandatory_field_set": mandatory_field_set,
            "baseline_proof_hash": baseline_hash,
        },
        "artifact_refs": artifact_refs,
        "summary_metrics": {
            "closure_mode": proof_digest.get("closure_mode"),
            "events_emitted": proof_digest.get("events_emitted"),
            "bytes_emitted": proof_digest.get("bytes_emitted"),
            "sidecar_selector_mode": proof_digest.get("sidecar_selector_mode"),
            "sidecar_ticket_fast_path": sidecar_ticket_fast_path,
            "fallback_reasons": fallback_reasons,
            "optional_dependency": scenario.optional_dependency,
            "optional_dependency_available": optional_available,
            "advisor_mode_requested": scenario.advisor_mode,
            "advisor_mode_effective": effective_advisor_mode,
            "advisor_action_kinds": advisor_action_kinds,
            "advisor_decision_model_ref": advisor_decision_model_ref,
            "advisor_decision_model_checksum": advisor_decision_model_checksum,
            "advisor_training_model_checksum": training_model_checksum,
            "checksum_validation_result": checksum_validation_result,
            "advisor_latency_seconds": advisor_metadata.get("openai_latency_seconds", advisor_overhead_seconds if scenario.advisor_enabled else 0.0),
            "gate_accept": gate_result.get("accepted"),
            "gate_reject_reason": gate_result.get("rejected_reason"),
            "advisor_report_present": bool(advisor_report),
            "advisor_training_status": training_status,
            "advisor_training_skip_reason": training_skip_reason,
            "advisor_training_rows": training_rows,
            "advisor_coefficients_attached": advisor_coefficients_attached,
            "advisor_coefficients_path": str(advisor_coefficients_path) if advisor_coefficients_path is not None else None,
            "openai_latency_seconds": advisor_metadata.get("openai_latency_seconds"),
            "openai_tokens": advisor_metadata.get("openai_tokens"),
            "fallback_reason": advisor_metadata.get("fallback_reason"),
            "openai_response_id": advisor_metadata.get("openai_response_id"),
            "openai_model": advisor_metadata.get("openai_model"),
            "llm_backend": advisor_metadata.get("llm_backend"),
            "proof_drift": proof_drift,
            "plan_regret": {
                "status": "not_applicable" if scenario.advisor_mode == "disabled" else "not_measured",
                "requested_action_kinds": advisor_action_kinds,
                "oracle_scenario_id": None,
                "oracle_action_kind": None,
                "runtime_delta_seconds": None,
                "normalized_regret": None,
                "reason": "advisor_mode_disabled" if scenario.advisor_mode == "disabled" else "no_oracle_safe_action_runtime_available",
                "claim_strength": "report_only",
                "source": "product_runtime_matrix_summary_only",
                "notes": ["report_only_no_counterfactual_runtime_source"],
            },
            "counterfactual_replay": {
                "status": "not_applicable" if scenario.advisor_mode == "disabled" else "not_measured",
                "baseline_scenario_id": "current_baseline",
                "replayed_scenario_id": None,
                "replay_runtime_seconds": None,
                "oracle_runtime_seconds": None,
                "reason": "advisor_mode_disabled" if scenario.advisor_mode == "disabled" else "no_counterfactual_fixture_or_external_benchmark_not_run",
                "claim_strength": "report_only",
                "source": "product_runtime_matrix_summary_only",
                "notes": ["report_only_no_counterfactual_fixture"],
            },
            "sidecar_build_seconds": sidecar_build_seconds,
            "synthetic_metric_fields": [],
            "runtime_breakdown": _product_runtime_breakdown(
                load_metrics=load_metrics,
                runtime_seconds=runtime_seconds,
                sidecar_build_seconds=sidecar_build_seconds,
                sidecar_validate_seconds=sidecar_validate_seconds,
                index_build_open_seconds=index_build_open_seconds,
                advisor_overhead_seconds=advisor_overhead_seconds,
                package_write_seconds=package_write_seconds,
                peak_rss_mb=peak_rss_mb,
            ),
        },
    }


def run_runtime_optimization_benchmark_matrix(
    *,
    trace: str | Path | None = None,
    output_root: str | Path | None = None,
    input_contract: dict[str, Any] | None = None,
    train_optional_sklearn_coefficients: bool = False,
    advisor_coefficients_path: str | Path | None = None,
    scenarios: Iterable[BenchmarkScenario] | None = None,
    prebuild_ticket_fast_path: bool = False,
) -> Result[dict[str, Any]]:
    root = Path(output_root).expanduser().resolve() if output_root is not None else Path.cwd() / "runtime_benchmark_matrix"
    root.mkdir(parents=True, exist_ok=True)
    trace_path = Path(trace).expanduser().resolve() if trace is not None else root / "input" / "runtime-benchmark.trace"
    if trace is None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        write_scenario(trace_path, name="basic", repeat=3)
    if not trace_path.exists():
        return err_result("INVALID_ARG", f"benchmark trace does not exist: {trace_path}")

    controller = WorkspaceController()
    loaded = load_dataset_with_timings(trace_path)
    if not loaded.ok:
        return Result(loaded.code, loaded.message, warnings=loaded.warnings, untrusted_windows=loaded.untrusted_windows)
    load_metrics = _load_timing_metrics(dict(loaded.data or {}))
    registered = controller._register_artifact(loaded.data["artifact"])
    if not registered.ok:
        return Result(registered.code, registered.message, warnings=registered.warnings, untrusted_windows=registered.untrusted_windows)
    record = controller.repository.get(registered.data)
    bundle = record.artifact.bundle
    if not bundle.event_stream:
        return err_result("INVALID_ARG", "benchmark trace contains no events")
    anchor_ref = bundle.event_stream[0].ref_key
    controller.viz_SetContext({"evidence_anchor": {"ref_key": anchor_ref}, "selection": {"seed_ref": anchor_ref}})

    sidecar_root = root / "shared_sidecar"
    sidecar_path, sidecar_manifest_path = _write_external_sidecar_artifacts(
        sidecar_root,
        trace_path,
        bundle,
        snapshot_id="snapshot:runtime-benchmark:mode-b",
        rule_family=("ref_ref",),
        )
    shared_sidecar_manifest = _load_json_if_exists(sidecar_manifest_path)
    base_runtime_cost_graph = dict(loaded.data.get("runtime_cost_graph") or {})
    if not base_runtime_cost_graph:
        return err_result("SCHEMA_INVALID", "pipeline runtime cost graph missing from load_dataset_with_timings output")
    _clear_sidecar_index_artifacts(sidecar_path)
    if prebuild_ticket_fast_path:
        sidecar_manifest = json_load(sidecar_manifest_path)
        sidecar_checksum = checksum_file(sidecar_path)
        prebuilt = build_or_open_sidecar_index(
            sidecar_path,
            expected_snapshot_id=str(sidecar_manifest.get("snapshot_id") or ""),
            expected_trace_checksum=str(sidecar_manifest.get("trace_checksum") or ""),
            dictionary_checksum=str(sidecar_manifest.get("dictionary_checksum") or ""),
            sidecar_checksum=sidecar_checksum,
            file_fingerprint=dependency_sidecar_file_fingerprint(sidecar_path),
            rebuild_on_mismatch=True,
        )
        if not prebuilt.ok:
            return Result(prebuilt.code, prebuilt.message, warnings=prebuilt.warnings, untrusted_windows=prebuilt.untrusted_windows)
    telemetry_history_path = root / "telemetry_history.jsonl"
    raw_advisor_coefficients_path = advisor_coefficients_path
    if raw_advisor_coefficients_path is None:
        raw_advisor_coefficients_path = dict(input_contract or {}).get("advisor_coefficients_path")
    effective_advisor_coefficients_path = (
        Path(raw_advisor_coefficients_path).expanduser().resolve()
        if raw_advisor_coefficients_path is not None
        else None
    )
    advisor_training_report: dict[str, Any] | None = None
    advisor_training_report_path: Path | None = None

    rows: list[dict[str, Any]] = []
    runtime_cost_graph_artifact = root / "runtime_cost_graph.json"
    runtime_cost_graph_payloads: list[dict[str, Any]] = []
    baseline_parity: dict[str, Any] | None = None
    scenario_result_dir = root / "scenario_results"
    scenario_result_dir.mkdir(parents=True, exist_ok=True)
    scenario_list = list(scenarios or default_benchmark_scenarios())
    for scenario in scenario_list:
        package_dir = root / "packages" / scenario.scenario_id
        if package_dir.exists():
            shutil.rmtree(package_dir)
        progress: list[dict[str, Any]] = []
        scenario_coefficients_path: Path | None = None
        if scenario.scenario_id == "advisor_optional_sklearn":
            if train_optional_sklearn_coefficients:
                if effective_advisor_coefficients_path is None:
                    effective_advisor_coefficients_path = root / "advisor_coefficients.json"
                advisor_training_report_path = effective_advisor_coefficients_path
                advisor_training_report = train_runtime_advisor_coefficients(
                    telemetry_history_path,
                    advisor_training_report_path,
                )
            scenario_coefficients_path = effective_advisor_coefficients_path

        def _record_progress(payload: dict[str, Any]) -> None:
            progress.append({**dict(payload), "observed_at": time.perf_counter()})

        export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=_record_progress)
        started = time.perf_counter()
        job = export.export_Evidence(
            _scenario_payload(
                scenario,
                dataset_id=registered.data,
                sidecar_path=sidecar_path,
                sidecar_manifest_path=sidecar_manifest_path,
                telemetry_history_path=telemetry_history_path,
                coefficients_path=scenario_coefficients_path,
            )
        )
        if not job.ok:
            return Result(job.code, job.message, warnings=job.warnings, untrusted_windows=job.untrusted_windows)
        written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
        runtime_seconds = round(time.perf_counter() - started, 6)
        if not written.ok:
            sidecar_build_seconds = _progress_duration_optional(
                progress,
                "sidecar/build_or_load",
                "sidecar/build",
                "sidecar/write",
            )
            row = {
                "scenario_id": scenario.scenario_id,
                "status": "failed",
                "runtime_seconds": runtime_seconds,
                "peak_rss_mb": _rss_mb(),
                "sidecar_bytes_scanned": None,
                "sidecar_validate_seconds": _progress_duration(progress, "sidecar/validate"),
                "index_build_open_seconds": None,
                "package_write_seconds": _write_phase_duration(progress),
                "advisor_overhead_seconds": 0.0,
                "proof_hash": None,
                "parity_result": {"proof_hash": None, "metric_diff_count": 1, "mandatory_field_set": []},
                "artifact_refs": {
                    "package_path": str(package_dir),
                    "telemetry_history_path": str(telemetry_history_path),
                },
                "summary_metrics": {
                    "error_code": written.code,
                    "error_message": written.message,
                    "sidecar_build_seconds": sidecar_build_seconds,
                    "synthetic_metric_fields": [],
                    "runtime_breakdown": _product_runtime_breakdown(
                        load_metrics=load_metrics,
                        runtime_seconds=runtime_seconds,
                        sidecar_build_seconds=sidecar_build_seconds,
                        sidecar_validate_seconds=_progress_duration(progress, "sidecar/validate"),
                        index_build_open_seconds=None,
                        advisor_overhead_seconds=0.0,
                        package_write_seconds=_write_phase_duration(progress),
                        peak_rss_mb=_rss_mb(),
                    ),
                },
            }
        else:
            row = _build_row(
                scenario,
                package_dir=package_dir,
                sidecar_path=sidecar_path,
                sidecar_manifest_path=sidecar_manifest_path,
                telemetry_history_path=telemetry_history_path,
                load_metrics=load_metrics,
                progress=progress,
                runtime_seconds=runtime_seconds,
                baseline_parity=baseline_parity,
                advisor_coefficients_path=scenario_coefficients_path,
                advisor_training_report=advisor_training_report,
                advisor_training_report_path=advisor_training_report_path,
            )
        _append_row_telemetry_if_advisor_report_absent(
            row,
            scenario,
            telemetry_history_path=telemetry_history_path,
            trace_path=trace_path,
            sidecar_path=sidecar_path,
            dataset_id=registered.data,
        )
        scenario_runtime_cost_graph = build_benchmark_runtime_cost_graph(
            base_graph_payload=base_runtime_cost_graph,
            scenario_id=scenario.scenario_id,
            progress=progress,
            row=row,
            proof_digest=_load_json_if_exists(package_dir / "control" / "proof_digest.json"),
            advisor_report=_load_json_if_exists(package_dir / "control" / "advisor_report.json") or None,
            action_context=_runtime_cost_graph_action_context(
                sidecar_manifest=shared_sidecar_manifest,
                sidecar_path=sidecar_path,
            ),
            output_root=root,
        )
        row["artifact_refs"]["runtime_cost_graph"] = str(runtime_cost_graph_artifact)
        row["summary_metrics"] = {
            **dict(row.get("summary_metrics") or {}),
            **_runtime_cost_graph_summary(scenario_runtime_cost_graph),
        }
        runtime_cost_graph_payloads.append(scenario_runtime_cost_graph)
        result_path = scenario_result_dir / f"{scenario.scenario_id}.json"
        row["artifact_refs"]["scenario_result"] = str(result_path)
        json_dump(result_path, row)
        if baseline_parity is None and row.get("parity_result"):
            baseline_parity = dict(row["parity_result"])
        rows.append(row)

    merged_runtime_cost_graph = merge_runtime_cost_graphs(runtime_cost_graph_payloads)
    runtime_cost_graph_schema_error = validate_schema(load_specs()["runtime_cost_graph"], merged_runtime_cost_graph)
    if runtime_cost_graph_schema_error is not None:
        return err_result(
            "SCHEMA_INVALID",
            f"runtime cost graph schema invalid: {runtime_cost_graph_schema_error}",
        )
    json_dump(runtime_cost_graph_artifact, merged_runtime_cost_graph)

    report_input_contract = {
        **dict(input_contract or {}),
        "trace_path": str(trace_path),
        "sidecar_source": str(sidecar_path),
        "sidecar_manifest_source": str(sidecar_manifest_path),
        "telemetry_history_path": str(telemetry_history_path),
    }
    if effective_advisor_coefficients_path is not None:
        report_input_contract["advisor_coefficients_path"] = str(effective_advisor_coefficients_path)
    return ok_result(
        BenchmarkReportBuilder().build_report(
            rows,
            input_contract=report_input_contract,
            scenarios=scenario_list,
        )
    )

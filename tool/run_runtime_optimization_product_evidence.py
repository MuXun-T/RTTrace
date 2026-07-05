from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any
from unittest import mock
from dataclasses import replace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.services import (
    ExportService,
    PARSER_CACHE_SCHEMA_VERSION,
    PARSER_VERSION,
    WorkspaceController,
    _full_safe_runtime_load_plan,
    _resolve_evidence_dictionary_source_path,
    _trace_runtime_load_features,
)
from parser.parser_process_agent import build_parser_cache_bindings
from parser.evidence_models import evd_ProofHashInput
from parser.evidence_sidecar import (
    build_dependency_sidecar,
    build_sidecar_segment_manifest,
    materialize_dependency_sidecar,
    partition_dependency_sidecar_segments,
)
from parser.evidence_sidecar_index import sidecar_index_path_for_source, sidecar_index_ticket_path_for_source
from parser.runtime_advisor import RuntimeLoadPlan, RuntimeOptimizationAdvisor
from parser.runtime_optimization_gate import gate_ValidateRuntimeLoadPlan
from spec.io import checksum_file, json_dump, jsonl_dump
from spec.schema_loader import DICTIONARY_PATH, SCHEMA_DIR

REPORT_VERSION = "runtime-optimization-product-evidence-v1"
DEFAULT_OUTPUT_ROOT = ROOT_DIR / "docs" / "runtime_optimization_product_evidence_20260702"
DEFAULT_TMP_ROOT = ROOT_DIR / "tmp" / "runtime_optimization_product_evidence_20260702"
SUITE_CHOICES = ("load_cache", "background_prebuild", "segmented_sidecar")
LOAD_CACHE_MODE_CHOICES = ("per_repeat_cold", "cold_once_warm_repeat")
DEFAULT_LOAD_CACHE_MODE = LOAD_CACHE_MODE_CHOICES[0]
DEFAULT_PREBUILD_TIMEOUT_S = 3600.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _selected_suites(requested: list[str]) -> list[str]:
    if not requested:
        return list(SUITE_CHOICES)
    selected: list[str] = []
    for suite in requested:
        text = str(suite).strip()
        if text and text not in selected:
            selected.append(text)
    return selected


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_distribution(values: list[Any]) -> dict[str, Any]:
    samples = [value for item in values for value in [_optional_float(item)] if value is not None]
    if not samples:
        return {
            "count": 0,
            "samples": [],
            "median": None,
            "p95": None,
            "min": None,
            "max": None,
        }
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    median_index = len(ordered) // 2
    if len(ordered) % 2 == 0:
        median = (ordered[median_index - 1] + ordered[median_index]) / 2.0
    else:
        median = ordered[median_index]
    return {
        "count": len(ordered),
        "samples": [round(float(value), 6) for value in ordered],
        "median": round(float(median), 6),
        "p95": round(float(ordered[p95_index]), 6),
        "min": round(float(ordered[0]), 6),
        "max": round(float(ordered[-1]), 6),
    }


def _planned_suite_payload(suite_name: str) -> dict[str, Any]:
    return {
        "suite_id": suite_name,
        "status": "planned",
        "repeat_count": None,
        "notes": ["dry_run_only"],
    }


def _suite_metric_summary(rows: list[dict[str, Any]], *, group_key: str, metric_keys: tuple[str, ...]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(group_key) or "unknown"), []).append(row)
    return {
        group_name: {
            metric_key: _metric_distribution([row.get(metric_key) for row in group_rows])
            for metric_key in metric_keys
        }
        for group_name, group_rows in sorted(grouped.items())
    }


def _load_cache_bindings(trace: Path) -> dict[str, Any]:
    trace_path = trace.expanduser().resolve()
    dictionary_path = Path(_resolve_evidence_dictionary_source_path(str(trace_path))).expanduser().resolve()
    runtime_load_plan = RuntimeOptimizationAdvisor().plan_load(
        current_request_features=_trace_runtime_load_features(trace_path)
    )
    runtime_load_gate = gate_ValidateRuntimeLoadPlan(runtime_load_plan=runtime_load_plan)
    effective_plan = (
        runtime_load_plan
        if runtime_load_gate.ok
        else _full_safe_runtime_load_plan(f"gate rejected runtime load plan: {runtime_load_gate.code}")
    )
    bindings = build_parser_cache_bindings(
        trace_path,
        dictionary_path=dictionary_path,
        parser_version=PARSER_VERSION,
        schema_version=PARSER_CACHE_SCHEMA_VERSION,
        index_build_mode=str(effective_plan.index_build_mode),
        materialize_event_stream=bool(effective_plan.materialize_event_stream),
    )
    return {
        **bindings,
        "dictionary_path": str(dictionary_path),
        "runtime_load_plan": runtime_load_plan.to_dict(),
        "runtime_load_gate_result": (
            runtime_load_gate.data.to_dict()
            if runtime_load_gate.data is not None and hasattr(runtime_load_gate.data, "to_dict")
            else runtime_load_gate.data
        ),
        "runtime_load_effective_plan": effective_plan.to_dict(),
    }


def _run_load_cache_phase(
    *,
    trace: Path,
    repeat_index: int,
    phase: str,
    load_artifact: bool,
) -> dict[str, Any]:
    controller = WorkspaceController()
    started = time.perf_counter()
    loaded = controller._load_artifact_from_source(
        str(trace),
        job_id=f"product-evidence-load-cache-{repeat_index}-{phase}",
        load_artifact=load_artifact,
    )
    wall_seconds = round(time.perf_counter() - started, 6)
    payload = dict(loaded.data or {}) if isinstance(loaded.data, dict) else {}
    parser_artifact = dict(payload.get("parser_process_artifact") or {})
    return {
        "repeat_index": int(repeat_index),
        "phase": phase,
        "status": "completed" if loaded.ok else "failed",
        "code": loaded.code,
        "message": loaded.message,
        "metadata_only": not bool(load_artifact),
        "load_artifact": bool(load_artifact),
        "open_load_seconds": wall_seconds,
        "wall_seconds": wall_seconds,
        "load_seconds": _optional_float(parser_artifact.get("load_seconds")),
        "cache_hit": bool(parser_artifact.get("cache_hit")),
        "runtime_load_plan": payload.get("runtime_load_plan"),
        "runtime_load_gate_result": payload.get("runtime_load_gate_result"),
        "runtime_load_effective_plan": payload.get("runtime_load_effective_plan"),
        "index_build_mode": parser_artifact.get("index_build_mode"),
        "materialize_event_stream": bool(parser_artifact.get("materialize_event_stream", True)),
        "trace_checksum": parser_artifact.get("trace_checksum"),
        "dictionary_checksum": parser_artifact.get("dictionary_checksum"),
        "parser_version": parser_artifact.get("parser_version"),
        "schema_version": parser_artifact.get("schema_version"),
    }


def _run_load_cache_suite(
    trace: Path,
    *,
    repeat: int,
    load_cache_mode: str = DEFAULT_LOAD_CACHE_MODE,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if load_cache_mode not in LOAD_CACHE_MODE_CHOICES:
        raise ValueError(f"unsupported load cache mode: {load_cache_mode}")
    rows: list[dict[str, Any]] = []
    phase_order = ["cold", "warm", "hot_metadata_only"]
    bindings = _load_cache_bindings(trace)
    cache_root = Path(tempfile.gettempdir()) / "rttrace-parser-agent" / str(bindings["cache_key"])
    if load_cache_mode == "cold_once_warm_repeat":
        shutil.rmtree(cache_root, ignore_errors=True)
    for repeat_index in range(1, repeat + 1):
        if load_cache_mode == "per_repeat_cold":
            shutil.rmtree(cache_root, ignore_errors=True)
            rows.append(_run_load_cache_phase(trace=trace, repeat_index=repeat_index, phase="cold", load_artifact=True))
        elif repeat_index == 1:
            rows.append(_run_load_cache_phase(trace=trace, repeat_index=repeat_index, phase="cold", load_artifact=True))
        rows.append(_run_load_cache_phase(trace=trace, repeat_index=repeat_index, phase="warm", load_artifact=True))
        rows.append(_run_load_cache_phase(trace=trace, repeat_index=repeat_index, phase="hot_metadata_only", load_artifact=False))

    phase_statistics = _suite_metric_summary(
        rows,
        group_key="phase",
        metric_keys=("open_load_seconds", "load_seconds"),
    )
    repeat_rows = [
        [row for row in rows if int(row.get("repeat_index") or 0) == repeat_index]
        for repeat_index in range(1, repeat + 1)
    ]
    phase_sequences = [[str(row.get("phase") or "") for row in group] for group in repeat_rows]
    cache_hit_sequences = [[bool(row.get("cache_hit")) for row in group] for group in repeat_rows]
    expected_phase_sequences = [
        phase_order if load_cache_mode == "per_repeat_cold" or repeat_index == 1 else phase_order[1:]
        for repeat_index in range(1, repeat + 1)
    ]
    phase_sequence_valid = phase_sequences == expected_phase_sequences
    phase_checksums = {
        key: {
            str(row.get(key) or "")
            for row in rows
            if str(row.get(key) or "").strip()
        }
        for key in ("trace_checksum", "dictionary_checksum", "parser_version", "schema_version")
    }
    warm_median = dict(phase_statistics.get("warm") or {}).get("open_load_seconds", {}).get("median")
    cold_median = dict(phase_statistics.get("cold") or {}).get("open_load_seconds", {}).get("median")
    cold_rows = [row for row in rows if str(row.get("phase") or "") == "cold"]
    warm_rows = [row for row in rows if str(row.get("phase") or "") == "warm"]
    hot_rows = [row for row in rows if str(row.get("phase") or "") == "hot_metadata_only"]
    phase_counts = {
        "cold": len(cold_rows),
        "warm": len(warm_rows),
        "hot_metadata_only": len(hot_rows),
    }
    cold_miss_observed = bool(cold_rows) and any(not bool(row.get("cache_hit")) for row in cold_rows)
    warm_hits_all = bool(warm_rows) and all(bool(row.get("cache_hit")) for row in warm_rows)
    hot_hits_all = bool(hot_rows) and all(bool(row.get("cache_hit")) for row in hot_rows)
    cache_sequence_valid = phase_sequence_valid and cold_miss_observed and warm_hits_all and hot_hits_all
    version_consistent = all(len(values) == 1 for values in phase_checksums.values())
    claimable = (
        cache_sequence_valid
        and version_consistent
        and warm_median is not None
        and cold_median is not None
        and float(warm_median) < float(cold_median)
    )
    claimable_speedups: list[dict[str, Any]] = []
    not_claimable: list[dict[str, Any]] = [
        {
            "suite": "load_cache",
            "phase": "hot_metadata_only",
            "reason": "metadata_only_not_full_ui_open",
        }
    ]
    if claimable:
        claimable_speedups.append(
            {
                "suite": "load_cache",
                "claim": "cached_desktop_open_load_median_reduced",
                "metric": "open_load_seconds",
                "baseline_phase": "cold",
                "candidate_phase": "warm",
                "baseline_median": cold_median,
                "candidate_median": warm_median,
            }
        )
    else:
        not_claimable.append(
            {
                "suite": "load_cache",
                "reason": "claim_conditions_not_met",
                "load_cache_mode": load_cache_mode,
                "phase_sequence_valid": phase_sequence_valid,
                "cold_miss_observed": cold_miss_observed,
                "warm_hits_all": warm_hits_all,
                "hot_hits_all": hot_hits_all,
                "phase_counts": phase_counts,
                "cache_sequence_valid": cache_sequence_valid,
                "version_consistent": version_consistent,
                "cold_open_load_median": cold_median,
                "warm_open_load_median": warm_median,
            }
        )
    suite = {
        "suite_id": "load_cache",
        "status": "completed" if all(str(row.get("status")) == "completed" for row in rows) else "failed",
        "repeat_count": repeat,
        "load_cache_mode": load_cache_mode,
        "phase_order": phase_order,
        "rows": rows,
        "phase_statistics": phase_statistics,
        "claim_evaluation": {
            "load_cache_mode": load_cache_mode,
            "phase_sequences": phase_sequences,
            "expected_phase_sequences": expected_phase_sequences,
            "cache_hit_sequences": cache_hit_sequences,
            "phase_counts": phase_counts,
            "phase_sequence_valid": phase_sequence_valid,
            "cold_miss_observed": cold_miss_observed,
            "warm_hits_all": warm_hits_all,
            "hot_hits_all": hot_hits_all,
            "cache_sequence_valid": cache_sequence_valid,
            "version_consistent": version_consistent,
            "checksum_consistency": {
                key: sorted(values)
                for key, values in phase_checksums.items()
            },
            "claimable": claimable,
        },
    }
    if load_cache_mode == "per_repeat_cold":
        suite["claim_evaluation"]["expected_cache_hit_sequence"] = [False, True, True]
    return suite, claimable_speedups, not_claimable


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, dict) else {}


def _progress_duration(progress: list[dict[str, Any]], substage: str) -> float:
    started = None
    finished = None
    for item in progress:
        if item.get("substage") != substage:
            continue
        if "observed_at" not in item:
            continue
        if item.get("status") == "started" and started is None:
            started = float(item["observed_at"])
        elif item.get("status") in {"completed", "failed", "rejected", "fallback"}:
            finished = float(item["observed_at"])
    if started is None or finished is None or finished < started:
        return 0.0
    return round(finished - started, 6)


def _write_phase_duration(progress: list[dict[str, Any]]) -> float:
    starts = [
        float(item["observed_at"])
        for item in progress
        if (
            "observed_at" in item
            and str(item.get("substage") or "").startswith("write/")
            and item.get("status") == "started"
        )
    ]
    finishes = [
        float(item["observed_at"])
        for item in progress
        if (
            "observed_at" in item
            and str(item.get("substage") or "").startswith("write/")
            and item.get("status") in {"completed", "failed"}
        )
    ]
    if not starts or not finishes:
        return 0.0
    return round(max(finishes) - min(starts), 6)


def _wait_for_job_result(
    controller: WorkspaceController,
    job_id: str,
    *,
    timeout_s: float = DEFAULT_PREBUILD_TIMEOUT_S,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        snapshot = controller.jobs.status(job_id)
        if snapshot.ok and snapshot.data["status"] in {"succeeded", "failed"}:
            result = controller.jobs.result(job_id)
            if not result.ok:
                raise RuntimeError(result.message)
            return dict(result.data or {}) if isinstance(result.data, dict) else {"result": result.data}
        time.sleep(0.01)
    raise RuntimeError(f"background job timed out: {job_id}")


def _default_anchor_ref(controller: WorkspaceController, dataset_id: str) -> str | None:
    bundle = controller.repository.get(dataset_id).artifact.bundle
    if not bundle.event_stream:
        return None
    return str(bundle.event_stream[0].ref_key)


def _manual_ref_seed_spec(anchor_ref: str) -> dict[str, Any]:
    return {
        "source_kind": "manual_refs",
        "source_payload": {
            "refs": [str(anchor_ref)],
        },
    }


def _configure_default_evidence_context(controller: WorkspaceController, dataset_id: str) -> None:
    anchor_ref = _default_anchor_ref(controller, dataset_id)
    if anchor_ref is None:
        return
    configured = controller.viz_SetContext(
        {
            "evidence_anchor": {"ref_key": anchor_ref},
            "selection": {"seed_ref": anchor_ref},
        }
    )
    if not configured.ok:
        raise RuntimeError(configured.message)


def _background_prebuild_plan(enabled: bool) -> RuntimeLoadPlan:
    return RuntimeLoadPlan(
        plan_version="runtime-load-plan-v1",
        load_mode="full",
        index_build_mode="full",
        materialize_event_stream=True,
        try_parser_artifact_reuse=False,
        try_sidecar_index_reuse=False,
        background_sidecar_prebuild=bool(enabled),
        reasons=["product evidence background prebuild suite"],
    )


def _background_prebuild_row(
    *,
    trace: Path,
    repeat_index: int,
    scenario: str,
    output_root: Path,
    prebuild_timeout_s: float,
) -> dict[str, Any]:
    controller = WorkspaceController()
    progress: list[dict[str, Any]] = []
    plan = _background_prebuild_plan(scenario == "candidate")
    with mock.patch("desktop.services.RuntimeOptimizationAdvisor.plan_load", return_value=plan):
        open_started = time.perf_counter()
        loaded = controller.viz_LoadDataset(str(trace))
        open_load_seconds = round(time.perf_counter() - open_started, 6)
    if not loaded.ok:
        raise RuntimeError(loaded.message)
    dataset_id = str(loaded.data)
    anchor_ref = _default_anchor_ref(controller, dataset_id)
    if anchor_ref is None:
        raise RuntimeError("background prebuild suite requires non-empty event_stream")
    seed_spec = _manual_ref_seed_spec(anchor_ref)

    prebuild_seconds = 0.0
    background_prebuild_state = {}
    if scenario == "candidate":
        background_prebuild = dict(controller.repository.get(dataset_id).background_sidecar_prebuild)
        job_id = str(background_prebuild.get("job_id") or "")
        if not job_id:
            raise RuntimeError("background prebuild job_id missing")
        updated = controller.jobs.update_payload(job_id, {"seed_spec": seed_spec})
        if not updated.ok:
            raise RuntimeError(updated.message)
        prebuild_started = time.perf_counter()
        background_prebuild_state = _wait_for_job_result(controller, job_id, timeout_s=prebuild_timeout_s)
        prebuild_seconds = round(time.perf_counter() - prebuild_started, 6)

    export = ExportService(controller.repository, controller.context_store, controller.jobs, on_progress=progress.append)
    click_started = time.perf_counter()
    job = export.export_Evidence(
        {
            "dataset_id": dataset_id,
            "advisor_enabled": True,
            "advisor_mode": "heuristic",
            "seed_spec": seed_spec,
        }
    )
    if not job.ok:
        raise RuntimeError(job.message)
    package_dir = output_root / f"repeat_{repeat_index:03d}" / scenario
    shutil.rmtree(package_dir, ignore_errors=True)
    written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
    click_to_export_seconds = round(time.perf_counter() - click_started, 6)
    if not written.ok:
        raise RuntimeError(written.message)

    advisor_trace = _load_json_if_exists(package_dir / "control" / "advisor_trace.json")
    feature_snapshot = dict(advisor_trace.get("feature_snapshot") or {})
    package_write_seconds = _optional_float(feature_snapshot.get("package_write_seconds"))
    sidecar_validate_seconds = _optional_float(feature_snapshot.get("sidecar_validate_seconds"))
    return {
        "repeat_index": int(repeat_index),
        "scenario": scenario,
        "status": "completed",
        "seed_ref": anchor_ref,
        "open_load_seconds": open_load_seconds,
        "prebuild_seconds": prebuild_seconds,
        "click_to_export_seconds": click_to_export_seconds,
        "total_elapsed_seconds": round(open_load_seconds + prebuild_seconds + click_to_export_seconds, 6),
        "package_write_seconds": package_write_seconds if package_write_seconds is not None else _write_phase_duration(progress),
        "sidecar_validate_seconds": (
            sidecar_validate_seconds if sidecar_validate_seconds is not None else _progress_duration(progress, "sidecar/validate")
        ),
        "advisor_overhead_seconds": _optional_float(advisor_trace.get("advisor_overhead_seconds")) or 0.0,
        "background_prebuild_reused": bool(feature_snapshot.get("background_prebuild_reused")),
        "sidecar_ticket_fast_path": bool(feature_snapshot.get("sidecar_ticket_fast_path")),
        "sidecar_bytes_scanned": int(feature_snapshot.get("sidecar_bytes_scanned") or 0),
        "proof_hash": _load_json_if_exists(package_dir / "control" / "proof_digest.json").get("proof_hash"),
        "prebuild_stage_breakdown": dict(background_prebuild_state.get("prebuild_stage_breakdown") or {}),
        "prebuild_path_features": dict(background_prebuild_state.get("prebuild_path_features") or {}),
        "prebuild_diagnostics": dict(background_prebuild_state.get("prebuild_diagnostics") or {}),
        "candidate_consumption_diagnostics": dict(feature_snapshot.get("candidate_consumption_diagnostics") or {}),
        "artifact_refs": {
            "package_path": str(package_dir),
            "advisor_trace": str(package_dir / "control" / "advisor_trace.json"),
        },
        "background_prebuild_state": background_prebuild_state,
    }


def _row_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "repeat_index": int(row.get("repeat_index") or 0),
        "scenario": str(row.get("scenario") or ""),
    }


def _background_prebuild_stage_breakdown_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = next((row for row in rows if row.get("scenario") == "baseline"), {})
    candidate = next((row for row in rows if row.get("scenario") == "candidate"), {})
    prebuild_diagnostics = dict(candidate.get("prebuild_diagnostics") or {})
    candidate_diagnostics = dict(candidate.get("candidate_consumption_diagnostics") or {})
    duplicate_findings = {
        "duplicate_parse_detected": bool(prebuild_diagnostics.get("duplicate_parse_detected")),
        "duplicate_materialize_detected": bool(
            int(prebuild_diagnostics.get("prebuild_materialize_dependency_sidecar_calls") or 0) > 0
            and int(candidate_diagnostics.get("candidate_local_sidecar_materialize_calls") or 0) > 0
        ),
        "duplicate_sidecar_full_write_detected": bool(
            candidate_diagnostics.get("candidate_full_sidecar_duplicate_write_detected")
        ),
        "duplicate_sidecar_full_write_heuristic_only": bool(
            candidate_diagnostics.get("candidate_full_sidecar_duplicate_write_heuristic_only")
        ),
    }
    background_state = dict(candidate.get("background_prebuild_state") or {})
    return {
        "baseline_row_ref": _row_ref(baseline),
        "candidate_row_ref": _row_ref(candidate),
        "prebuild_stage_breakdown": dict(candidate.get("prebuild_stage_breakdown") or {}),
        "prebuild_path_features": dict(candidate.get("prebuild_path_features") or {}),
        "prebuild_diagnostics": prebuild_diagnostics,
        "candidate_consumption_diagnostics": candidate_diagnostics,
        "duplicate_findings": duplicate_findings,
        "artifact_refs": {
            **dict(candidate.get("artifact_refs") or {}),
            "prebuild_dependency_sidecar": background_state.get("dependency_sidecar_path"),
            "prebuild_sidecar_manifest": background_state.get("sidecar_manifest_path"),
            "prebuild_sqlite_index": background_state.get("sidecar_index_path"),
            "prebuild_sidecar_index_ticket": background_state.get("sidecar_index_ticket_path"),
        },
    }


def _run_background_prebuild_suite(
    trace: Path,
    *,
    repeat: int,
    output_root: Path,
    prebuild_timeout_s: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    suite_root = output_root / "artifacts" / "background_prebuild"
    rows: list[dict[str, Any]] = []
    for repeat_index in range(1, repeat + 1):
        rows.append(
            _background_prebuild_row(
                trace=trace,
                repeat_index=repeat_index,
                scenario="baseline",
                output_root=suite_root,
                prebuild_timeout_s=prebuild_timeout_s,
            )
        )
        rows.append(
            _background_prebuild_row(
                trace=trace,
                repeat_index=repeat_index,
                scenario="candidate",
                output_root=suite_root,
                prebuild_timeout_s=prebuild_timeout_s,
            )
        )

    scenario_statistics = _suite_metric_summary(
        rows,
        group_key="scenario",
        metric_keys=(
            "open_load_seconds",
            "prebuild_seconds",
            "click_to_export_seconds",
            "total_elapsed_seconds",
            "package_write_seconds",
            "sidecar_validate_seconds",
            "advisor_overhead_seconds",
        ),
    )
    baseline_stats = dict(scenario_statistics.get("baseline") or {})
    candidate_stats = dict(scenario_statistics.get("candidate") or {})
    baseline_click = dict(baseline_stats.get("click_to_export_seconds") or {})
    candidate_click = dict(candidate_stats.get("click_to_export_seconds") or {})
    baseline_total = dict(baseline_stats.get("total_elapsed_seconds") or {})
    candidate_total = dict(candidate_stats.get("total_elapsed_seconds") or {})
    click_claimable = (
        baseline_click.get("median") is not None
        and candidate_click.get("median") is not None
        and baseline_click.get("p95") is not None
        and candidate_click.get("p95") is not None
        and float(candidate_click["median"]) < float(baseline_click["median"])
        and float(candidate_click["p95"]) < float(baseline_click["p95"])
    )
    total_claimable = (
        click_claimable
        and baseline_total.get("median") is not None
        and candidate_total.get("median") is not None
        and baseline_total.get("p95") is not None
        and candidate_total.get("p95") is not None
        and float(candidate_total["median"]) < float(baseline_total["median"])
        and float(candidate_total["p95"]) < float(baseline_total["p95"])
    )
    claimable_speedups: list[dict[str, Any]] = []
    not_claimable: list[dict[str, Any]] = []
    if click_claimable:
        claimable_speedups.append(
            {
                "suite": "background_prebuild",
                "claim": "click_to_export_wait_reduced",
                "metric": "click_to_export_seconds",
                "baseline_median": baseline_click.get("median"),
                "candidate_median": candidate_click.get("median"),
                "baseline_p95": baseline_click.get("p95"),
                "candidate_p95": candidate_click.get("p95"),
            }
        )
    else:
        not_claimable.append(
            {
                "suite": "background_prebuild",
                "reason": "click_to_export_not_lower_on_median_and_p95",
                "baseline": baseline_click,
                "candidate": candidate_click,
            }
        )
    if total_claimable:
        claimable_speedups.append(
            {
                "suite": "background_prebuild",
                "claim": "total_elapsed_reduced",
                "metric": "total_elapsed_seconds",
                "baseline_median": baseline_total.get("median"),
                "candidate_median": candidate_total.get("median"),
                "baseline_p95": baseline_total.get("p95"),
                "candidate_p95": candidate_total.get("p95"),
            }
        )
    else:
        not_claimable.append(
            {
                "suite": "background_prebuild",
                "reason": "total_elapsed_not_lower_with_click_claim",
                "baseline": baseline_total,
                "candidate": candidate_total,
            }
        )
    suite = {
        "suite_id": "background_prebuild",
        "status": "completed" if all(str(row.get("status")) == "completed" for row in rows) else "failed",
        "repeat_count": repeat,
        "prebuild_timeout_s": float(prebuild_timeout_s),
        "rows": rows,
        "scenario_statistics": scenario_statistics,
        "claim_evaluation": {
            "click_to_export_claimable": click_claimable,
            "total_elapsed_claimable": total_claimable,
        },
        "stage_breakdown_report": _background_prebuild_stage_breakdown_report(rows),
    }
    return suite, claimable_speedups, not_claimable


def _copy_sidecar_reference_assets(root: Path, *, include_segment_schema: bool) -> tuple[Path, dict[str, Any]]:
    dictionary_target = root / "reference" / "dictionary.json"
    schema_dir = root / "reference" / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DICTIONARY_PATH, dictionary_target)
    schema_names = {
        "dependency_sidecar_schema": "dependency_sidecar.schema.json",
        "frontier_snapshot_schema": "frontier_snapshot.schema.json",
        "frontier_refs_schema": "frontier_refs.schema.json",
        "proof_digest_schema": "proof_digest.schema.json",
        "sidecar_manifest_schema": "sidecar_manifest.schema.json",
        "blocker_artifact_schema": "blocker_artifact.schema.json",
    }
    if include_segment_schema:
        schema_names["sidecar_segment_manifest_schema"] = "sidecar_segment_manifest.schema.json"
    schema_checksums: dict[str, Any] = {}
    for schema_key, filename in schema_names.items():
        target = schema_dir / filename
        shutil.copy2(SCHEMA_DIR / filename, target)
        schema_checksums[schema_key] = {
            "path": f"reference/schema/{filename}",
            "algo": "sha256",
            "checksum": checksum_file(target),
        }
    return dictionary_target, schema_checksums


def _select_focus_seed_ref(sidecar_rows: list[Any]) -> str:
    counts: dict[str, int] = {}
    for row in sidecar_rows:
        counts[str(row.src_ref)] = counts.get(str(row.src_ref), 0) + 1
    if not counts:
        raise RuntimeError("segmented sidecar suite requires non-empty sidecar rows")
    return min(sorted(counts.keys()), key=lambda item: (counts[item], item))


def _tag_segmented_rows(rows: list[Any], *, focus_seed_ref: str) -> list[Any]:
    tagged: list[Any] = []
    for row in rows:
        if str(row.src_ref) == focus_seed_ref:
            segment_hint = "segment:focus"
        elif row.core_hint is not None:
            segment_hint = f"segment:core-{int(row.core_hint)}"
        else:
            segment_hint = "segment:other"
        tagged.append(replace(row, segment_hint=segment_hint))
    return tagged


def _write_sidecar_root(
    *,
    root: Path,
    trace: Path,
    sidecar_rows: list[Any],
    snapshot_id: str,
    include_segment_manifest: bool,
) -> tuple[Path, Path, dict[str, Any]]:
    shutil.rmtree(root, ignore_errors=True)
    control_dir = root / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    dictionary_target, schema_checksums = _copy_sidecar_reference_assets(
        root,
        include_segment_schema=include_segment_manifest,
    )
    sidecar_path = control_dir / "dependency_sidecar.jsonl"
    jsonl_dump(sidecar_path, sidecar_rows)
    manifest_payload = {
        "sidecar_version": "external-sidecar-1",
        "generator_version": REPORT_VERSION,
        "trace_checksum": checksum_file(trace),
        "dictionary_checksum": checksum_file(dictionary_target),
        "schema_checksums": schema_checksums,
        "relation_families": ["ref_ref"],
        "entry_paths": ["control/dependency_sidecar.jsonl"],
        "entry_checksums": {
            "control/dependency_sidecar.jsonl": checksum_file(sidecar_path),
        },
        "created_at": _iso_now(),
        "snapshot_id": snapshot_id,
    }
    segment_manifest = {}
    if include_segment_manifest:
        segments = partition_dependency_sidecar_segments(sidecar_rows)
        segment_sidecar_rel_by_id: dict[str, str] = {}
        segment_index_rel_by_id: dict[str, str] = {}
        segment_ticket_rel_by_id: dict[str, str] = {}
        segment_checksum_by_id: dict[str, str] = {}
        for segment in segments:
            token = segment.segment_id.replace(":", "_")
            segment_rel = f"control/segments/{token}/dependency_sidecar.jsonl"
            segment_path = root / segment_rel
            jsonl_dump(segment_path, list(segment.rows))
            segment_sidecar_rel_by_id[segment.segment_id] = segment_rel
            segment_index_rel_by_id[segment.segment_id] = f"{segment_rel}.sqlite3"
            segment_ticket_rel_by_id[segment.segment_id] = f"{segment_rel}.sqlite3.ticket.json"
            segment_checksum_by_id[segment.segment_id] = checksum_file(segment_path)
            manifest_payload["entry_paths"].append(segment_rel)
            manifest_payload["entry_checksums"][segment_rel] = segment_checksum_by_id[segment.segment_id]
        segment_manifest = build_sidecar_segment_manifest(
            sidecar_rows,
            segment_dir="control/segments",
            snapshot_id=snapshot_id,
            trace_checksum=manifest_payload["trace_checksum"],
            dictionary_checksum=manifest_payload["dictionary_checksum"],
            created_at=_iso_now(),
            sidecar_path_by_segment=segment_sidecar_rel_by_id,
            index_path_by_segment=segment_index_rel_by_id,
            checksum_by_segment=segment_checksum_by_id,
            ticket_path_by_segment=segment_ticket_rel_by_id,
        )
        json_dump(control_dir / "sidecar_segment_manifest.json", segment_manifest)
        manifest_payload["layout_mode"] = "segmented"
        manifest_payload["segment_manifest_path"] = "control/sidecar_segment_manifest.json"
        manifest_payload["segment_count"] = len(segment_manifest["segments"])
        manifest_payload["entry_paths"].append("control/sidecar_segment_manifest.json")
        manifest_payload["entry_checksums"]["control/sidecar_segment_manifest.json"] = checksum_file(
            control_dir / "sidecar_segment_manifest.json"
        )
    manifest_path = control_dir / "sidecar_manifest.json"
    json_dump(manifest_path, manifest_payload)
    return sidecar_path, manifest_path, segment_manifest


def _clear_sidecar_index_artifacts(sidecar_path: Path) -> None:
    index_path = sidecar_index_path_for_source(sidecar_path)
    ticket_path = sidecar_index_ticket_path_for_source(sidecar_path, index_path=index_path)
    for path in (index_path, ticket_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _prepare_segmented_sidecar_suite(
    *,
    trace: Path,
    output_root: Path,
    explicit_segmented_root: Path | None,
) -> dict[str, Any]:
    controller = WorkspaceController()
    loaded = controller.viz_LoadDataset(str(trace))
    if not loaded.ok:
        raise RuntimeError(loaded.message)
    dataset_id = str(loaded.data)
    _configure_default_evidence_context(controller, dataset_id)
    bundle = controller.repository.get(dataset_id).artifact.bundle
    ref_index_rows = [
        {
            "ref_key": event.ref_key,
            "timestamp_aligned": float(event.timestamp_aligned),
            "core_id": int(event.core_id),
            "seq": int(event.seq),
        }
        for event in sorted(bundle.event_stream, key=lambda item: (item.timestamp_aligned, item.core_id, item.seq))
    ]
    analysis_context = dict(controller.context_store.get().persisted_dict())
    snapshot_id = f"snapshot:product-evidence:segmented:{checksum_file(trace)[:12]}"
    base_rows = materialize_dependency_sidecar(
        build_dependency_sidecar(
            bundle,
            snapshot_id=snapshot_id,
            rule_families=("ref_ref",),
            context=analysis_context,
            ref_index_rows=ref_index_rows,
        ),
        trace_checksum=checksum_file(trace),
    )
    focus_seed_ref = _select_focus_seed_ref(base_rows)
    _configure_default_evidence_context(controller, dataset_id)
    controller.viz_SetContext(
        {
            "evidence_anchor": {"ref_key": focus_seed_ref},
            "selection": {"seed_ref": focus_seed_ref},
        }
    )
    tagged_rows = _tag_segmented_rows(base_rows, focus_seed_ref=focus_seed_ref)
    aggregate_root = output_root / "artifacts" / "segmented_sidecar" / "aggregate"
    segmented_root = (
        explicit_segmented_root
        if explicit_segmented_root is not None
        else output_root / "artifacts" / "segmented_sidecar" / "segmented"
    )
    aggregate_sidecar_path, aggregate_manifest_path, _ = _write_sidecar_root(
        root=aggregate_root,
        trace=trace,
        sidecar_rows=tagged_rows,
        snapshot_id=snapshot_id,
        include_segment_manifest=False,
    )
    segmented_sidecar_path, segmented_manifest_path, segment_manifest = _write_sidecar_root(
        root=segmented_root,
        trace=trace,
        sidecar_rows=tagged_rows,
        snapshot_id=snapshot_id,
        include_segment_manifest=True,
    )
    total_segment_count = int(segment_manifest.get("segment_count") or 0)
    hit_segment_ids = sorted(
        {
            str(row.segment_hint)
            for row in tagged_rows
            if str(row.src_ref) == focus_seed_ref and str(row.segment_hint or "").strip()
        }
    )
    return {
        "controller": controller,
        "dataset_id": dataset_id,
        "focus_seed_ref": focus_seed_ref,
        "snapshot_id": snapshot_id,
        "aggregate": {
            "root": aggregate_root,
            "sidecar_path": aggregate_sidecar_path,
            "manifest_path": aggregate_manifest_path,
        },
        "segmented": {
            "root": segmented_root,
            "sidecar_path": segmented_sidecar_path,
            "manifest_path": segmented_manifest_path,
            "segment_manifest": segment_manifest,
        },
        "segment_summary": {
            "total_segment_count": total_segment_count,
            "hit_segment_count": len(hit_segment_ids),
            "hit_segment_ids": hit_segment_ids,
            "hit_segment_count_inferred": True,
        },
    }


def _run_segmented_sidecar_row(
    *,
    controller: WorkspaceController,
    dataset_id: str,
    repeat_index: int,
    scenario: str,
    package_root: Path,
    sidecar_path: Path,
    manifest_path: Path,
    focus_seed_ref: str,
) -> dict[str, Any]:
    _clear_sidecar_index_artifacts(sidecar_path)
    if scenario == "segmented":
        segment_manifest_payload = _load_json_if_exists(manifest_path.parent / "sidecar_segment_manifest.json")
        for segment in list(segment_manifest_payload.get("segments") or []):
            _clear_sidecar_index_artifacts((manifest_path.parents[1] / str(segment.get("sidecar_path") or "")).resolve())
    export = ExportService(controller.repository, controller.context_store, controller.jobs)
    payload = {
        "dataset_id": dataset_id,
        "embodiment_mode": "mode_b",
        "sidecar_source": str(sidecar_path),
        "sidecar_manifest_source": str(manifest_path),
        "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
        "rule_family": ["ref_ref"],
        "budget_vector": {"D_max": 1, "C_events": 16, "S_bytes": 8192, "rho_max": 4.0},
        "closure_policy": {"allow_bounded": True, "allow_degraded": False, "frontier_ref_limit": 16},
    }
    started = time.perf_counter()
    job = export.export_Evidence(payload)
    if not job.ok:
        raise RuntimeError(job.message)
    package_dir = package_root / f"repeat_{repeat_index:03d}" / scenario
    shutil.rmtree(package_dir, ignore_errors=True)
    written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
    runtime_seconds = round(time.perf_counter() - started, 6)
    if not written.ok:
        raise RuntimeError(written.message)
    proof_digest = _load_json_if_exists(package_dir / "control" / "proof_digest.json")
    mandatory_field_set = sorted(evd_ProofHashInput(proof_digest).keys()) if proof_digest else []
    return {
        "repeat_index": int(repeat_index),
        "scenario": scenario,
        "status": "completed",
        "runtime_seconds": runtime_seconds,
        "selector_mode": proof_digest.get("sidecar_selector_mode"),
        "sidecar_selector_calls": int(proof_digest.get("sidecar_selector_calls") or 0),
        "sidecar_bytes_scanned": int(proof_digest.get("sidecar_bytes_scanned") or 0),
        "sidecar_bytes": proof_digest.get("sidecar_bytes"),
        "proof_hash": proof_digest.get("proof_hash"),
        "mandatory_field_set": mandatory_field_set,
        "focus_seed_ref": focus_seed_ref,
        "artifact_refs": {
            "package_path": str(package_dir),
            "proof_digest": str(package_dir / "control" / "proof_digest.json"),
        },
    }


def _run_segmented_sidecar_suite(
    trace: Path,
    *,
    repeat: int,
    output_root: Path,
    explicit_segmented_root: Path | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    prepared = _prepare_segmented_sidecar_suite(
        trace=trace,
        output_root=output_root,
        explicit_segmented_root=explicit_segmented_root,
    )
    controller = prepared["controller"]
    dataset_id = str(prepared["dataset_id"])
    focus_seed_ref = str(prepared["focus_seed_ref"])
    rows: list[dict[str, Any]] = []
    package_root = output_root / "artifacts" / "segmented_sidecar" / "packages"
    for repeat_index in range(1, repeat + 1):
        rows.append(
            _run_segmented_sidecar_row(
                controller=controller,
                dataset_id=dataset_id,
                repeat_index=repeat_index,
                scenario="aggregate",
                package_root=package_root,
                sidecar_path=prepared["aggregate"]["sidecar_path"],
                manifest_path=prepared["aggregate"]["manifest_path"],
                focus_seed_ref=focus_seed_ref,
            )
        )
        rows.append(
            _run_segmented_sidecar_row(
                controller=controller,
                dataset_id=dataset_id,
                repeat_index=repeat_index,
                scenario="segmented",
                package_root=package_root,
                sidecar_path=prepared["segmented"]["sidecar_path"],
                manifest_path=prepared["segmented"]["manifest_path"],
                focus_seed_ref=focus_seed_ref,
            )
        )
    scenario_statistics = _suite_metric_summary(
        rows,
        group_key="scenario",
        metric_keys=("runtime_seconds",),
    )
    aggregate_stats = dict(scenario_statistics.get("aggregate") or {}).get("runtime_seconds", {})
    segmented_stats = dict(scenario_statistics.get("segmented") or {}).get("runtime_seconds", {})
    aggregate_bytes = _metric_distribution([row.get("sidecar_bytes_scanned") for row in rows if row.get("scenario") == "aggregate"])
    segmented_bytes = _metric_distribution([row.get("sidecar_bytes_scanned") for row in rows if row.get("scenario") == "segmented"])
    aggregate_hashes = {str(row.get("proof_hash") or "") for row in rows if row.get("scenario") == "aggregate"}
    segmented_hashes = {str(row.get("proof_hash") or "") for row in rows if row.get("scenario") == "segmented"}
    aggregate_fields = {
        tuple(row.get("mandatory_field_set") or [])
        for row in rows
        if row.get("scenario") == "aggregate"
    }
    segmented_fields = {
        tuple(row.get("mandatory_field_set") or [])
        for row in rows
        if row.get("scenario") == "segmented"
    }
    segment_summary = dict(prepared["segment_summary"] or {})
    claimable = (
        all(str(row.get("selector_mode") or "") == "segmented_sqlite" for row in rows if row.get("scenario") == "segmented")
        and int(segment_summary.get("hit_segment_count") or 0) < int(segment_summary.get("total_segment_count") or 0)
        and aggregate_bytes.get("median") is not None
        and segmented_bytes.get("median") is not None
        and float(segmented_bytes["median"]) < float(aggregate_bytes["median"])
        and aggregate_stats.get("median") is not None
        and segmented_stats.get("median") is not None
        and aggregate_stats.get("p95") is not None
        and segmented_stats.get("p95") is not None
        and float(segmented_stats["median"]) < float(aggregate_stats["median"])
        and float(segmented_stats["p95"]) < float(aggregate_stats["p95"])
        and aggregate_hashes == segmented_hashes
        and aggregate_fields == segmented_fields
    )
    claimable_speedups: list[dict[str, Any]] = []
    not_claimable: list[dict[str, Any]] = []
    if claimable:
        claimable_speedups.append(
            {
                "suite": "segmented_sidecar",
                "claim": "segmented_sidecar_reduced_runtime_and_bytes_scanned",
                "aggregate_runtime": aggregate_stats,
                "segmented_runtime": segmented_stats,
                "aggregate_bytes_scanned": aggregate_bytes,
                "segmented_bytes_scanned": segmented_bytes,
            }
        )
    else:
        not_claimable.append(
            {
                "suite": "segmented_sidecar",
                "reason": "claim_conditions_not_met",
                "aggregate_runtime": aggregate_stats,
                "segmented_runtime": segmented_stats,
                "aggregate_bytes_scanned": aggregate_bytes,
                "segmented_bytes_scanned": segmented_bytes,
                "segment_summary": segment_summary,
                "aggregate_hashes": sorted(aggregate_hashes),
                "segmented_hashes": sorted(segmented_hashes),
            }
        )
    suite = {
        "suite_id": "segmented_sidecar",
        "status": "completed" if all(str(row.get("status")) == "completed" for row in rows) else "failed",
        "repeat_count": repeat,
        "rows": rows,
        "scenario_statistics": {
            **scenario_statistics,
            "bytes_scanned": {
                "aggregate": aggregate_bytes,
                "segmented": segmented_bytes,
            },
        },
        "segment_summary": segment_summary,
        "claim_evaluation": {
            "claimable": claimable,
            "proof_hashes_match": aggregate_hashes == segmented_hashes,
            "mandatory_field_sets_match": aggregate_fields == segmented_fields,
        },
    }
    return suite, claimable_speedups, not_claimable


def _execution_status(*, suites: dict[str, dict[str, Any]], selected_suites: list[str], dry_run: bool) -> str:
    if dry_run:
        return "planned"
    selected_statuses = [str(dict(suites.get(name) or {}).get("status") or "missing") for name in selected_suites]
    if any(status == "failed" for status in selected_statuses):
        return "failed"
    if any(status in {"pending_implementation", "missing"} for status in selected_statuses):
        return "partial"
    return "completed"


def _evidence_status(*, claimable_speedups: list[dict[str, Any]]) -> str:
    if claimable_speedups:
        return "pass_with_product_speedup_evidence"
    return "pass_with_noted_limits"


def _run_selected_suites(
    *,
    trace: Path,
    repeat: int,
    selected_suites: list[str],
    tmp_root: Path,
    output_segmented_sidecar: Path | None,
    load_cache_mode: str,
    prebuild_timeout_s: float,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    suites = {
        suite_name: {
            "suite_id": suite_name,
            "status": "skipped",
            "repeat_count": repeat,
        }
        for suite_name in SUITE_CHOICES
    }
    claimable_speedups: list[dict[str, Any]] = []
    not_claimable: list[dict[str, Any]] = []
    for suite_name in selected_suites:
        if suite_name == "load_cache":
            suite_payload, suite_claimable, suite_not_claimable = _run_load_cache_suite(
                trace,
                repeat=repeat,
                load_cache_mode=load_cache_mode,
            )
        elif suite_name == "background_prebuild":
            suite_payload, suite_claimable, suite_not_claimable = _run_background_prebuild_suite(
                trace,
                repeat=repeat,
                output_root=tmp_root,
                prebuild_timeout_s=prebuild_timeout_s,
            )
        elif suite_name == "segmented_sidecar":
            suite_payload, suite_claimable, suite_not_claimable = _run_segmented_sidecar_suite(
                trace,
                repeat=repeat,
                output_root=tmp_root,
                explicit_segmented_root=output_segmented_sidecar,
            )
        else:
            suite_payload = {
                "suite_id": suite_name,
                "status": "pending_implementation",
                "repeat_count": repeat,
            }
            suite_claimable = []
            suite_not_claimable = [
                {
                    "suite": suite_name,
                    "reason": "pending_implementation",
                }
            ]
        suites[suite_name] = suite_payload
        claimable_speedups.extend(suite_claimable)
        not_claimable.extend(suite_not_claimable)
    return suites, claimable_speedups, not_claimable


def _render_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Runtime Optimization Product Evidence",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- status: `{report['status']}`",
        f"- repeat_count: `{report['repeat_count']}`",
        f"- selected_suites: `{', '.join(report['selected_suites'])}`",
        "",
        "## Suites",
    ]
    suites = dict(report.get("suites") or {})
    for suite_name in SUITE_CHOICES:
        suite = dict(suites.get(suite_name) or {})
        lines.append(f"- `{suite_name}`: `{suite.get('status', 'missing')}`")
    lines.append("")
    lines.append("## Claims")
    claims = list(report.get("claimable_speedups") or [])
    if claims:
        for item in claims:
            lines.append(f"- `{item.get('suite', 'unknown')}`: {item.get('claim', item.get('reason', 'claimable'))}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_runtime_optimization_product_evidence")
    parser.add_argument("--trace", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--suite", action="append", choices=SUITE_CHOICES, default=[])
    parser.add_argument("--load-cache-mode", choices=LOAD_CACHE_MODE_CHOICES, default=DEFAULT_LOAD_CACHE_MODE)
    parser.add_argument("--prebuild-timeout-s", type=float, default=DEFAULT_PREBUILD_TIMEOUT_S)
    parser.add_argument(
        "--output-segmented-sidecar",
        nargs="?",
        const=DEFAULT_TMP_ROOT / "segmented_sidecar_external",
        type=Path,
        default=None,
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    if float(args.prebuild_timeout_s) <= 0.0:
        parser.error("--prebuild-timeout-s must be > 0")
    if not args.dry_run and args.trace is None:
        parser.error("--trace is required unless --dry-run")

    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    trace = args.trace.expanduser().resolve() if args.trace is not None else None
    selected_suites = _selected_suites(args.suite)
    output_segmented_sidecar = (
        args.output_segmented_sidecar.expanduser().resolve()
        if args.output_segmented_sidecar is not None
        else None
    )

    if args.dry_run:
        suites = {suite_name: _planned_suite_payload(suite_name) for suite_name in SUITE_CHOICES}
        claimable_speedups: list[dict[str, Any]] = []
        not_claimable = [{"suite": "all", "reason": "dry_run_only"}]
    else:
        tmp_root = DEFAULT_TMP_ROOT.expanduser().resolve()
        tmp_root.mkdir(parents=True, exist_ok=True)
        suites, claimable_speedups, not_claimable = _run_selected_suites(
            trace=trace,
            repeat=args.repeat,
            selected_suites=selected_suites,
            tmp_root=tmp_root,
            output_segmented_sidecar=output_segmented_sidecar,
            load_cache_mode=str(args.load_cache_mode),
            prebuild_timeout_s=float(args.prebuild_timeout_s),
        )
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": _iso_now(),
        "status": _evidence_status(claimable_speedups=claimable_speedups),
        "execution_status": _execution_status(suites=suites, selected_suites=selected_suites, dry_run=bool(args.dry_run)),
        "trace": None if trace is None else str(trace),
        "output_root": str(output_root),
        "repeat_count": int(args.repeat),
        "load_cache_mode": str(args.load_cache_mode),
        "prebuild_timeout_s": float(args.prebuild_timeout_s),
        "selected_suites": list(selected_suites),
        "output_segmented_sidecar": None if output_segmented_sidecar is None else str(output_segmented_sidecar),
        "claimable_speedups": claimable_speedups,
        "not_claimable": not_claimable,
        "suites": suites,
    }
    report_path = output_root / "product_evidence_report.json"
    summary_path = output_root / "product_evidence_summary.md"
    p4_breakdown_path = output_root / "p4_prebuild_stage_breakdown_report.json"
    background_prebuild_suite = dict(suites.get("background_prebuild") or {})
    if dict(background_prebuild_suite.get("stage_breakdown_report") or {}):
        report["p4_prebuild_stage_breakdown_report"] = str(p4_breakdown_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if dict(background_prebuild_suite.get("stage_breakdown_report") or {}):
        p4_breakdown_report = {
            "report_version": "p4-prebuild-stage-breakdown-v1",
            "generated_at": report["generated_at"],
            "source_product_evidence_report": str(report_path),
            **dict(background_prebuild_suite["stage_breakdown_report"]),
        }
        p4_breakdown_path.write_text(
            json.dumps(p4_breakdown_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    summary_path.write_text(_render_summary(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "summary_path": str(summary_path),
                "status": report["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.dry_run:
        return 0
    return 0 if report["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

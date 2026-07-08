from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.benchmark_matrix import BENCHMARK_SCENARIO_IDS, BenchmarkReportBuilder, BenchmarkScenario, default_benchmark_scenarios
from parser.evidence_models import evd_ProofHashInput, evd_RecomputeProofHash
from parser.runtime_advisor import RuntimeOptimizationAdvisor, build_advisor_trace, write_advisor_trace
from tool.run_formal_windows_suite import main as run_formal_windows_suite_main


FORMAL_MATRIX_SUMMARY_VERSION = "runtime-optimization-formal-matrix-v1"
FORMAL_PARITY_REPORT_VERSION = "runtime-optimization-formal-parity-v1"
FORMAL_READINESS_REPORT_VERSION = "runtime-optimization-formal-readiness-v1"
FORMAL_INPUT_MANIFEST_VERSION = "runtime-optimization-formal-input-manifest-v1"
FORMAL_GROUPS = {
    "A": "A_control_plane_first",
    "B": "B_budget_pre_freeze",
    "C": "C_degraded_audit",
}
PROOF_DIGEST_POLLUTION_KEYWORDS = ("advisor", "openai", "llm", "token", "fallback")
FORMAL_SYNTHETIC_METRIC_FIELDS = (
    "sidecar_validate_seconds",
    "index_build_open_seconds",
    "peak_rss_mb",
    "package_write_seconds",
    "advisor_overhead_seconds",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _first_existing(root: Path, candidates: Iterable[str]) -> Path | None:
    for rel_path in candidates:
        path = root / rel_path
        if path.exists():
            return path
    return None


def _load_optional_json(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if path is None:
        return {}, None
    try:
        return _read_json(path), None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, str(exc)


def _select_scenarios(requested: list[str]) -> list[BenchmarkScenario]:
    scenarios = default_benchmark_scenarios()
    if not requested:
        return scenarios
    requested_ids = set(requested)
    return [scenario for scenario in scenarios if scenario.scenario_id in requested_ids]


def _is_optional_scenario(scenario: BenchmarkScenario) -> bool:
    return scenario.scenario_id == "advisor_openai_structured"


def _deterministic_scenarios(scenarios: list[BenchmarkScenario]) -> list[BenchmarkScenario]:
    return [scenario for scenario in scenarios if not _is_optional_scenario(scenario)]


def _optional_scenarios(scenarios: list[BenchmarkScenario]) -> list[BenchmarkScenario]:
    return [scenario for scenario in scenarios if _is_optional_scenario(scenario)]


def _root_proof_digest_path(scenario_root: Path) -> Path | None:
    return _first_existing(
        scenario_root,
        (
            "control/proof_digest.json",
            "packages/run_001/control/proof_digest.json",
            "package/control/proof_digest.json",
            "proof_digest.json",
        ),
    )


def _group_proof_digest_path(scenario_root: Path, group_dir: str) -> Path | None:
    direct = _first_existing(
        scenario_root,
        (
            f"{group_dir}/control/proof_digest.json",
            f"{group_dir}/packages/run_001/control/proof_digest.json",
            f"{group_dir}/package/control/proof_digest.json",
            f"formal/{group_dir}/control/proof_digest.json",
            f"formal/{group_dir}/packages/run_001/control/proof_digest.json",
            f"formal/{group_dir}/package/control/proof_digest.json",
        ),
    )
    if direct is not None:
        return direct
    for root in (scenario_root / group_dir, scenario_root / "formal" / group_dir):
        matches = sorted(root.glob("**/control/proof_digest.json"))
        if matches:
            return matches[0]
    return None


def _proof_digest_statuses(scenario_root: Path) -> dict[str, dict[str, Any]]:
    root_path = _root_proof_digest_path(scenario_root)
    group_paths = {
        group: _group_proof_digest_path(scenario_root, group_dir)
        for group, group_dir in FORMAL_GROUPS.items()
    }
    use_root_fallback = root_path is not None and not any(group_paths.values())
    statuses: dict[str, dict[str, Any]] = {}
    for group, group_dir in FORMAL_GROUPS.items():
        path = group_paths[group] or (root_path if use_root_fallback else None)
        payload, load_error = _load_optional_json(path)
        proof_hash = str(payload.get("proof_hash") or "")
        recomputed_proof_hash: str | None = None
        mandatory_field_set: list[str] = []
        proof_input_error: str | None = None
        if payload:
            try:
                recomputed_proof_hash = evd_RecomputeProofHash(payload)
                mandatory_field_set = sorted(evd_ProofHashInput(payload).keys())
            except (TypeError, ValueError) as exc:
                proof_input_error = str(exc)
        statuses[group] = {
            "group_dir": group_dir,
            "path": str(path.resolve()) if path is not None else None,
            "source": "group" if group_paths[group] is not None else ("root" if path is not None else None),
            "exists": bool(path is not None and path.exists()),
            "loaded": bool(payload) and proof_input_error is None,
            "load_error": load_error or proof_input_error,
            "proof_hash": proof_hash or None,
            "proof_hash_recomputed": recomputed_proof_hash,
            "proof_hash_recomputed_matches": bool(proof_hash and recomputed_proof_hash == proof_hash),
            "mandatory_field_set": mandatory_field_set,
            "sidecar_bytes_scanned": _optional_int(payload.get("sidecar_bytes_scanned"), default=None) if payload else None,
            "proof_digest_pollution_fields": _polluted_key_paths(payload),
        }
    return statuses


def _formal_summary_path(scenario_root: Path, group_dir: str) -> Path | None:
    return _first_existing(
        scenario_root,
        (
            f"{group_dir}/formal_summary.json",
            f"{group_dir}/formal_summary_windows.json",
            f"{group_dir}/formal_summary_linux.json",
            f"formal/{group_dir}/formal_summary.json",
            f"formal/{group_dir}/formal_summary_windows.json",
            f"formal/{group_dir}/formal_summary_linux.json",
        ),
    )


def _scheduler_report_path(scenario_root: Path) -> Path | None:
    return _first_existing(
        scenario_root,
        (
            "formal_scheduler_plan.json",
            "formal_scheduler_plan_windows.json",
            "formal_scheduler_report.json",
            "schedule_report.json",
        ),
    )


def _advisor_report_path(scenario_root: Path) -> Path | None:
    return _first_existing(
        scenario_root,
        (
            "control/advisor_report.json",
            "advisor_report.json",
        ),
    )


def _formal_parity_report_path(scenario_root: Path) -> Path | None:
    return _first_existing(
        scenario_root,
        (
            "formal_parity_report.json",
            "parity_report.json",
        ),
    )


def _readiness_report_path(scenario_root: Path) -> Path | None:
    return _first_existing(
        scenario_root,
        (
            "windows_final_readiness_report.json",
            "readiness_report.json",
        ),
    )


def _scenario_result_path(scenario_root: Path) -> Path | None:
    return _first_existing(
        scenario_root,
        (
            "scenario_result.json",
            "scenario_summary.json",
            "result/scenario_result.json",
        ),
    )


def _polluted_key_paths(value: Any, *, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}"
            lowered = key_text.lower()
            if any(keyword in lowered for keyword in PROOF_DIGEST_POLLUTION_KEYWORDS):
                paths.append(path)
            paths.extend(_polluted_key_paths(child, prefix=path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_polluted_key_paths(child, prefix=f"{prefix}[{index}]"))
    return paths


def _optional_int(*values: Any, default: int | None = 0) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _optional_float(*values: Any, default: float | None = 0.0) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _normalized_metric_fields(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized = [str(value) for value in values if str(value).strip()]
    return list(dict.fromkeys(normalized))


def _group_artifact_status(scenario_root: Path) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for group, group_dir in FORMAL_GROUPS.items():
        path = _formal_summary_path(scenario_root, group_dir)
        payload, load_error = _load_optional_json(path)
        verdict = str(payload.get("verdict") or payload.get("status") or "")
        groups[group] = {
            "group_dir": group_dir,
            "path": str(path.resolve()) if path is not None else None,
            "exists": bool(path is not None and path.exists()),
            "loaded": bool(payload),
            "load_error": load_error,
            "verdict": verdict or None,
            "pass": verdict == "pass",
        }
    return groups


def _scheduler_execution_failures(scheduler_report: dict[str, Any]) -> list[str]:
    execution = dict(scheduler_report.get("execution") or {})
    results = list(execution.get("task_results") or [])
    failures: list[str] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        exit_code = _optional_int(row.get("exit_code"), default=0)
        error_code = row.get("error_code")
        task_id = str(row.get("task_id") or "unknown")
        if exit_code != 0 or error_code is not None:
            failures.append(task_id)
    return failures


def _scenario_failure_codes(
    *,
    proof_digests: dict[str, dict[str, Any]],
    group_artifacts: dict[str, dict[str, Any]],
    scenario: BenchmarkScenario,
    sidecar_bytes_scanned: int | None,
    scheduler_report_present: bool,
    advisor_report_present: bool,
    scheduler_report: dict[str, Any],
    formal_parity_report: dict[str, Any],
    readiness_report: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    for group, status in group_artifacts.items():
        if not status["exists"]:
            failures.append(f"formal_group_{group}_missing")
        elif not status["loaded"]:
            failures.append(f"formal_group_{group}_load_failed")
        elif not status["pass"]:
            failures.append(f"formal_group_{group}_verdict_not_pass")
    for group, status in proof_digests.items():
        if not status["exists"]:
            failures.append(f"proof_digest_group_{group}_missing")
        elif not status["loaded"]:
            failures.append(f"proof_digest_group_{group}_load_failed")
        elif not status["proof_hash"]:
            failures.append(f"proof_digest_group_{group}_missing_proof_hash")
        elif not status["proof_hash_recomputed_matches"]:
            failures.append(f"proof_digest_group_{group}_hash_recompute_mismatch")
        if status["proof_digest_pollution_fields"]:
            failures.append(f"proof_digest_group_{group}_boundary_polluted")
    if scenario.ticket_fast_path_enabled:
        nonzero_fast_path_groups = [
            group
            for group, status in proof_digests.items()
            if status.get("sidecar_bytes_scanned") != 0
        ]
        if sidecar_bytes_scanned != 0 or nonzero_fast_path_groups:
            failures.append("ticket_fast_path_sidecar_bytes_scanned_not_zero")
            failures.extend(
                f"ticket_fast_path_group_{group}_sidecar_bytes_scanned_not_zero"
                for group in nonzero_fast_path_groups
            )
    if any(status["proof_digest_pollution_fields"] for status in proof_digests.values()):
        failures.append("proof_digest_boundary_polluted")
    if not scheduler_report_present:
        failures.append("missing_scheduler_report")
    scheduler_failures = _scheduler_execution_failures(scheduler_report)
    if scheduler_failures:
        failures.append("scheduler_task_failure")
        failures.extend(f"scheduler_task_failed:{task_id}" for task_id in scheduler_failures)
    if scenario.advisor_enabled and not advisor_report_present:
        failures.append("missing_advisor_report")
    if not formal_parity_report:
        failures.append("missing_formal_parity_report")
    else:
        if str(formal_parity_report.get("status") or "") != "pass":
            failures.append("formal_parity_status_not_pass")
        if not bool(formal_parity_report.get("ready_for_gate")):
            failures.append("formal_parity_not_ready_for_gate")
        if int(formal_parity_report.get("metric_diff_count") or 0) != 0:
            failures.append("formal_parity_metric_diff_count_nonzero")
        if formal_parity_report.get("mandatory_field_set_same") is not True:
            failures.append("formal_parity_mandatory_field_set_not_same")
    readiness_verdict = str(readiness_report.get("verdict") or readiness_report.get("status") or "")
    if not readiness_report:
        failures.append("missing_readiness_report")
    elif readiness_verdict not in {"ready_for_gate", "ready_for_linux_parity", "ready"}:
        failures.append("readiness_verdict_not_ready")
    return failures


def _build_scenario_summary(
    scenario: BenchmarkScenario,
    *,
    input_matrix_root: Path,
) -> dict[str, Any]:
    scenario_root = input_matrix_root / scenario.scenario_id
    proof_digests = _proof_digest_statuses(scenario_root)
    group_artifacts = _group_artifact_status(scenario_root)
    scheduler_path = _scheduler_report_path(scenario_root)
    scheduler_report, scheduler_load_error = _load_optional_json(scheduler_path)
    advisor_path = _advisor_report_path(scenario_root)
    advisor_report, advisor_load_error = _load_optional_json(advisor_path)
    formal_parity_path = _formal_parity_report_path(scenario_root)
    formal_parity_report, formal_parity_load_error = _load_optional_json(formal_parity_path)
    readiness_path = _readiness_report_path(scenario_root)
    readiness_report, readiness_load_error = _load_optional_json(readiness_path)
    scenario_result_path = _scenario_result_path(scenario_root)
    scenario_result, scenario_result_load_error = _load_optional_json(scenario_result_path)

    proof_hashes = [
        str(status["proof_hash"])
        for status in proof_digests.values()
        if status.get("proof_hash")
    ]
    recomputed_proof_hashes = [
        str(status["proof_hash_recomputed"])
        for status in proof_digests.values()
        if status.get("proof_hash_recomputed")
    ]
    mandatory_field_sets = [
        list(status.get("mandatory_field_set") or [])
        for status in proof_digests.values()
        if status.get("mandatory_field_set")
    ]
    proof_hash = proof_hashes[0] if proof_hashes else ""
    recomputed_proof_hash = recomputed_proof_hashes[0] if recomputed_proof_hashes else None
    mandatory_field_set = mandatory_field_sets[0] if mandatory_field_sets else []
    sidecar_bytes_scanned = _optional_int(
        *[status.get("sidecar_bytes_scanned") for status in proof_digests.values()],
        scenario_result.get("sidecar_bytes_scanned"),
        default=None,
    )
    proof_digest_pollution_fields = sorted({
        str(field)
        for status in proof_digests.values()
        for field in list(status.get("proof_digest_pollution_fields") or [])
    })
    scheduler_report_present = bool(scheduler_path is not None and scheduler_path.exists() and scheduler_report)
    advisor_report_present = bool(advisor_path is not None and advisor_path.exists() and advisor_report)
    failures = _scenario_failure_codes(
        proof_digests=proof_digests,
        group_artifacts=group_artifacts,
        scenario=scenario,
        sidecar_bytes_scanned=sidecar_bytes_scanned,
        scheduler_report_present=scheduler_report_present,
        advisor_report_present=advisor_report_present,
        scheduler_report=scheduler_report,
        formal_parity_report=formal_parity_report,
        readiness_report=readiness_report,
    )

    artifact_refs = {"scenario_root": str(scenario_root.resolve())}
    for group, status in proof_digests.items():
        if status["path"]:
            artifact_refs[f"proof_digest_{group}"] = str(status["path"])
    for group, status in group_artifacts.items():
        if status["path"]:
            artifact_refs[f"formal_{group}_summary"] = str(status["path"])
    if scheduler_path is not None:
        artifact_refs["scheduler_report"] = str(scheduler_path.resolve())
    if advisor_path is not None:
        artifact_refs["advisor_report"] = str(advisor_path.resolve())
    if formal_parity_path is not None:
        artifact_refs["formal_parity_report"] = str(formal_parity_path.resolve())
    if readiness_path is not None:
        artifact_refs["readiness_report"] = str(readiness_path.resolve())
    if scenario_result_path is not None:
        artifact_refs["scenario_result"] = str(scenario_result_path.resolve())
    scenario_result_artifact_refs = dict(scenario_result.get("artifact_refs") or {})
    formal_input_manifest_ref = scenario_result_artifact_refs.get("formal_input_manifest")
    if isinstance(formal_input_manifest_ref, str) and formal_input_manifest_ref:
        artifact_refs["formal_input_manifest"] = formal_input_manifest_ref

    decision = dict(advisor_report.get("decision") or {})
    advisor_status = "present" if advisor_report_present else ("not_required" if not scenario.advisor_enabled else "missing")
    advisor_effective_mode = (
        "disabled"
        if not scenario.advisor_enabled
        else str(decision.get("advisor_mode") or "missing")
    )
    scheduler_execution = dict(scheduler_report.get("execution") or {})
    scheduler_parity = dict(scheduler_execution.get("parity_summary") or {})
    readiness_verdict = str(readiness_report.get("verdict") or readiness_report.get("status") or "")
    parity_status = str(formal_parity_report.get("status") or "")
    return {
        "scenario_id": scenario.scenario_id,
        "scenario": scenario.to_dict(),
        "deterministic_scenario": not _is_optional_scenario(scenario),
        "optional_scenario": _is_optional_scenario(scenario),
        "scenario_root": str(scenario_root.resolve()),
        "artifact_refs": artifact_refs,
        "formal_groups": group_artifacts,
        "proof_digests": proof_digests,
        "required_artifacts_present": all(
            status["exists"] and status["loaded"]
            for status in group_artifacts.values()
        ) and all(
            status["exists"] and status["loaded"]
            for status in proof_digests.values()
        ) and bool(formal_parity_report) and bool(readiness_report),
        "scheduler_report_present": scheduler_report_present,
        "abc_summary_verdicts_pass": all(status["pass"] for status in group_artifacts.values()),
        "proof_digest_load_error": next(
            (
                str(status["load_error"])
                for status in proof_digests.values()
                if status.get("load_error")
            ),
            None,
        ),
        "proof_hash": proof_hash or None,
        "proof_hash_recomputed": recomputed_proof_hash,
        "proof_hash_recomputed_matches": all(
            bool(status.get("proof_hash_recomputed_matches"))
            for status in proof_digests.values()
        ),
        "mandatory_field_set": mandatory_field_set,
        "proof_hashes_by_group": {
            group: status.get("proof_hash")
            for group, status in proof_digests.items()
        },
        "mandatory_field_sets_by_group": {
            group: list(status.get("mandatory_field_set") or [])
            for group, status in proof_digests.items()
        },
        "sidecar_bytes_scanned": sidecar_bytes_scanned,
        "ticket_fast_path_required": bool(scenario.ticket_fast_path_enabled),
        "ticket_fast_path_sidecar_bytes_scanned_zero": bool(
            not scenario.ticket_fast_path_enabled
            or all(status.get("sidecar_bytes_scanned") == 0 for status in proof_digests.values())
        ),
        "proof_digest_pollution_fields": proof_digest_pollution_fields,
        "proof_digest_pollution_fields_by_group": {
            group: list(status.get("proof_digest_pollution_fields") or [])
            for group, status in proof_digests.items()
        },
        "proof_digest_boundary_clean": not proof_digest_pollution_fields,
        "advisor": {
            "enabled": bool(scenario.advisor_enabled),
            "requested_mode": scenario.advisor_mode,
            "effective_mode": advisor_effective_mode,
            "status": advisor_status,
            "load_error": advisor_load_error,
        },
        "formal_parity": {
            "path": str(formal_parity_path.resolve()) if formal_parity_path is not None else None,
            "loaded": bool(formal_parity_report),
            "load_error": formal_parity_load_error,
            "status": parity_status or None,
            "ready_for_gate": bool(formal_parity_report.get("ready_for_gate")) if formal_parity_report else False,
            "metric_diff_count": int(formal_parity_report.get("metric_diff_count") or 0) if formal_parity_report else None,
            "mandatory_field_set_same": formal_parity_report.get("mandatory_field_set_same") if formal_parity_report else None,
        },
        "readiness": {
            "path": str(readiness_path.resolve()) if readiness_path is not None else None,
            "loaded": bool(readiness_report),
            "load_error": readiness_load_error,
            "verdict": readiness_verdict or None,
        },
        "scheduler": {
            "report_path": str(scheduler_path.resolve()) if scheduler_path is not None else None,
            "plan_ref": str(scheduler_path.resolve()) if scheduler_report_present and scheduler_report.get("plan") is not None else None,
            "execution_ref": str(scheduler_path.resolve()) if scheduler_report_present and scheduler_report.get("execution") is not None else None,
            "loaded": bool(scheduler_report),
            "load_error": scheduler_load_error,
            "ready_for_gate": scheduler_parity.get("ready_for_gate"),
            "task_result_count": len(list(scheduler_execution.get("task_results") or [])),
            "failed_task_count": len(_scheduler_execution_failures(scheduler_report)),
            "failed_tasks": _scheduler_execution_failures(scheduler_report),
        },
        "scenario_result": {
            "path": str(scenario_result_path.resolve()) if scenario_result_path is not None else None,
            "loaded": bool(scenario_result),
            "load_error": scenario_result_load_error,
        },
        "ready_for_gate": not failures,
        "ready_for_main_gate": (not failures) if not _is_optional_scenario(scenario) else None,
        "failures": failures,
    }


def _build_benchmark_report(
    scenario_summaries: list[dict[str, Any]],
    *,
    scenarios: list[BenchmarkScenario],
    input_contract: dict[str, Any],
) -> dict[str, Any]:
    deterministic_summaries = [summary for summary in scenario_summaries if not summary.get("optional_scenario")]
    baseline = deterministic_summaries[0] if deterministic_summaries else (scenario_summaries[0] if scenario_summaries else {})
    baseline_proof_hash = baseline.get("proof_hash")
    baseline_fields = list(baseline.get("mandatory_field_set") or [])
    rows: list[dict[str, Any]] = []
    for summary in scenario_summaries:
        scenario = next(item for item in scenarios if item.scenario_id == summary["scenario_id"])
        proof_hash = summary.get("proof_hash")
        mandatory_field_set = list(summary.get("mandatory_field_set") or [])
        metric_diff_count = 0
        if proof_hash != baseline_proof_hash:
            metric_diff_count += 1
        if mandatory_field_set != baseline_fields:
            metric_diff_count += 1
        sidecar_bytes_scanned = summary.get("sidecar_bytes_scanned")
        scenario_result_payload = _read_json(Path(summary["scenario_result"]["path"])) if summary.get("scenario_result", {}).get("loaded") and summary.get("scenario_result", {}).get("path") else {}
        benchmark_row_path = Path(summary["scenario_root"]) / "benchmark_row.json"
        runtime_seconds = _optional_float(scenario_result_payload.get("runtime_seconds"), default=0.0)
        sidecar_validate_seconds = _optional_float(
            scenario_result_payload.get("sidecar_validate_seconds"),
            0.0 if sidecar_bytes_scanned == 0 else 0.001,
            default=0.0 if sidecar_bytes_scanned == 0 else 0.001,
        )
        index_build_open_seconds = _optional_float(scenario_result_payload.get("index_build_open_seconds"), default=0.0)
        peak_rss_mb = _optional_float(scenario_result_payload.get("peak_rss_mb"), default=0.0)
        package_write_seconds = _optional_float(scenario_result_payload.get("package_write_seconds"), default=0.0)
        advisor_overhead_seconds = _optional_float(
            scenario_result_payload.get("advisor_overhead_seconds"),
            0.0 if not scenario.advisor_enabled else 0.001,
            default=0.0 if not scenario.advisor_enabled else 0.001,
        )
        synthetic_metric_fields = _normalized_metric_fields(
            dict(scenario_result_payload.get("summary_metrics") or {}).get("synthetic_metric_fields")
        )
        for field in FORMAL_SYNTHETIC_METRIC_FIELDS:
            if scenario_result_payload.get(field) is None:
                synthetic_metric_fields.append(field)
        synthetic_metric_fields = list(dict.fromkeys(synthetic_metric_fields))
        summary_metrics = _formal_metric_summary(
            {
                **dict(scenario_result_payload.get("summary_metrics") or {}),
                "ready_for_gate": bool(summary["ready_for_gate"]),
                "ready_for_main_gate": bool(summary.get("ready_for_main_gate")),
                "failures": list(summary.get("failures") or []),
                "advisor_mode_requested": scenario.advisor_mode,
                "advisor_mode_effective": dict(summary.get("advisor") or {}).get("effective_mode"),
                "ticket_fast_path_required": bool(summary.get("ticket_fast_path_required")),
                "ticket_fast_path_sidecar_bytes_scanned_zero": bool(
                    summary.get("ticket_fast_path_sidecar_bytes_scanned_zero")
                ),
                "proof_digest_boundary_clean": bool(summary.get("proof_digest_boundary_clean")),
                "scheduler_failed_task_count": int(dict(summary.get("scheduler") or {}).get("failed_task_count") or 0),
                "deterministic_scenario": bool(summary.get("deterministic_scenario")),
                "optional_scenario": bool(summary.get("optional_scenario")),
                "gate_accept": bool(summary.get("ready_for_gate")),
                "gate_reject_reason": None if bool(summary.get("ready_for_gate")) else "ready_for_gate_false",
                "proof_drift": {
                    "status": "clean" if metric_diff_count == 0 else "drift_detected",
                    "metric_diff_count": int(metric_diff_count),
                    "proof_hash_changed": bool(baseline_proof_hash and proof_hash and proof_hash != baseline_proof_hash),
                    "mandatory_field_set_changed": bool(metric_diff_count > 0 and proof_hash == baseline_proof_hash),
                },
            },
            runtime_seconds=runtime_seconds or 0.0,
            sidecar_validate_seconds=sidecar_validate_seconds,
            index_build_open_seconds=index_build_open_seconds,
            advisor_overhead_seconds=advisor_overhead_seconds,
            synthetic_metric_fields=synthetic_metric_fields,
        )
        rows.append(
            {
                "scenario_id": summary["scenario_id"],
                "status": str(scenario_result_payload.get("status") or ("completed" if summary["ready_for_gate"] else "failed")),
                "runtime_seconds": runtime_seconds,
                "sidecar_bytes_scanned": sidecar_bytes_scanned,
                "sidecar_validate_seconds": sidecar_validate_seconds,
                "index_build_open_seconds": index_build_open_seconds,
                "peak_rss_mb": peak_rss_mb,
                "package_write_seconds": package_write_seconds,
                "advisor_overhead_seconds": advisor_overhead_seconds,
                "proof_hash": proof_hash,
                "parity_result": {
                    "proof_hash": proof_hash,
                    "metric_diff_count": int(metric_diff_count),
                    "mandatory_field_set": mandatory_field_set,
                    "baseline_proof_hash": baseline_proof_hash,
                },
                "artifact_refs": {
                    **dict(summary.get("artifact_refs") or {}),
                    "benchmark_row": str(benchmark_row_path.resolve()),
                },
                "summary_metrics": summary_metrics,
                "telemetry_records": list(scenario_result_payload.get("telemetry_records") or []),
            }
        )
    report = BenchmarkReportBuilder().build_report(rows, input_contract=input_contract, scenarios=scenarios)
    deterministic_rows = [row for row in rows if not bool(dict(row.get("summary_metrics") or {}).get("optional_scenario"))]
    deterministic_proof_hashes = {
        str(row.get("proof_hash") or (dict(row.get("parity_result") or {}).get("proof_hash")) or "")
        for row in deterministic_rows
        if str(row.get("proof_hash") or (dict(row.get("parity_result") or {}).get("proof_hash")) or "").strip()
    }
    deterministic_parity_rows = [dict(row.get("parity_result") or {}) for row in deterministic_rows if row.get("parity_result") is not None]
    deterministic_mandatory_field_sets = {
        tuple(sorted(str(item) for item in list(parity.get("mandatory_field_set") or [])))
        for parity in deterministic_parity_rows
    }
    report["summary"]["parity"] = {
        "proof_hash_consistent": len(deterministic_proof_hashes) <= 1,
        "metric_diff_count": sum(int(parity.get("metric_diff_count") or 0) for parity in deterministic_parity_rows),
        "mandatory_field_set_consistent": len(deterministic_mandatory_field_sets) <= 1,
        "mandatory_field_sets": [list(items) for items in sorted(deterministic_mandatory_field_sets)],
        "proof_hashes": sorted(deterministic_proof_hashes),
        "parity_row_count": len(deterministic_parity_rows),
        "scenario_ids": [str(row.get("scenario_id")) for row in deterministic_rows],
    }
    return report


def _load_formal_input_manifest(input_matrix_root: Path) -> tuple[dict[str, Any], Path | None]:
    manifest_path = input_matrix_root / "formal_input_manifest.json"
    if not manifest_path.exists():
        return {}, None
    try:
        return _read_json(manifest_path), manifest_path
    except (OSError, json.JSONDecodeError, ValueError):
        return {}, manifest_path


def _validate_execute_contract(
    *,
    trace_path: Path | None,
    source_report: Path | None,
    expected_trace_sha256: str | None,
    expected_trace_size_bytes: int | None,
) -> tuple[Path, Path]:
    if trace_path is None:
        raise SystemExit("--execute requires --trace\n")
    if source_report is None:
        raise SystemExit("--execute requires --source-report\n")
    resolved_trace = trace_path.expanduser().resolve()
    resolved_source = source_report.expanduser().resolve()
    if not resolved_trace.exists() or not resolved_trace.is_file():
        raise SystemExit(f"--execute trace not found: {resolved_trace}\n")
    if not resolved_source.exists() or not resolved_source.is_file():
        raise SystemExit(f"--execute source report not found: {resolved_source}\n")
    if not expected_trace_sha256 or expected_trace_size_bytes is None:
        raise SystemExit("--execute requires explicit --expected-trace-sha256 and --expected-trace-size-bytes\n")
    return resolved_trace, resolved_source


def _scenario_output_root(output_root: Path, scenario: BenchmarkScenario) -> Path:
    return output_root / scenario.scenario_id


def _scenario_scheduler_strategy(scenario: BenchmarkScenario, requested_strategy: str) -> str:
    if requested_strategy != "serial_safe":
        return requested_strategy
    if scenario.advisor_enabled:
        return "sidecar_first"
    return "serial_safe"


def _scenario_feature_snapshot(
    scenario: BenchmarkScenario,
    *,
    trace_path: Path,
    expected_trace_sha256: str,
    expected_trace_size_bytes: int,
    scheduler_strategy: str,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario.scenario_id,
        "advisor_enabled": bool(scenario.advisor_enabled),
        "advisor_mode": scenario.advisor_mode,
        "ticket_fast_path_enabled": bool(scenario.ticket_fast_path_enabled),
        "trace_path": str(trace_path),
        "trace_sha256": expected_trace_sha256,
        "trace_size_bytes": int(expected_trace_size_bytes),
        "scheduler_strategy": scheduler_strategy,
        "sidecar_bytes": 0 if scenario.ticket_fast_path_enabled else 4096,
        "ticket_present": bool(scenario.ticket_fast_path_enabled),
    }


def _build_formal_input_manifest(
    *,
    output_root: Path,
    trace_path: Path,
    source_report_path: Path,
    expected_trace_sha256: str,
    expected_trace_size_bytes: int,
    scenarios: list[BenchmarkScenario],
) -> dict[str, Any]:
    return {
        "contract_version": FORMAL_INPUT_MANIFEST_VERSION,
        "generated_at": _iso_now(),
        "trace": {
            "path": str(trace_path.resolve()),
            "name": trace_path.name,
            "sha256": expected_trace_sha256,
            "size_bytes": int(expected_trace_size_bytes),
        },
        "source_report_path": str(source_report_path.resolve()),
        "scenario_order": [scenario.scenario_id for scenario in scenarios],
        "deterministic_scenario_order": [scenario.scenario_id for scenario in _deterministic_scenarios(scenarios)],
        "optional_scenario_order": [scenario.scenario_id for scenario in _optional_scenarios(scenarios)],
        "artifact_refs": {
            "output_root": str(output_root.resolve()),
            "trace": str(trace_path.resolve()),
            "source_report": str(source_report_path.resolve()),
        },
    }


def _write_formal_input_manifest(
    output_root: Path,
    *,
    trace_path: Path,
    source_report_path: Path,
    expected_trace_sha256: str,
    expected_trace_size_bytes: int,
    scenarios: list[BenchmarkScenario],
) -> Path:
    manifest_path = output_root / "formal_input_manifest.json"
    _write_json(
        manifest_path,
        _build_formal_input_manifest(
            output_root=output_root,
            trace_path=trace_path,
            source_report_path=source_report_path,
            expected_trace_sha256=expected_trace_sha256,
            expected_trace_size_bytes=expected_trace_size_bytes,
            scenarios=scenarios,
        ),
    )
    return manifest_path


def _write_scenario_execution_contract(
    scenario_root: Path,
    scenario: BenchmarkScenario,
    *,
    trace_path: Path,
    source_report_path: Path,
    formal_input_manifest_path: Path,
    expected_trace_sha256: str,
    expected_trace_size_bytes: int,
    scheduler_strategy: str,
) -> Path:
    contract_path = scenario_root / "scenario_execution_contract.json"
    _write_json(
        contract_path,
        {
            "contract_version": "runtime-optimization-formal-scenario-execution-v1",
            "generated_at": _iso_now(),
            "scenario": scenario.to_dict(),
            "trace": {
                "path": str(trace_path),
                "sha256": expected_trace_sha256,
                "size_bytes": int(expected_trace_size_bytes),
            },
            "source_report_path": str(source_report_path),
            "input_contract": {
                "formal_input_manifest_path": str(formal_input_manifest_path.resolve()),
                "trace_path": str(trace_path.resolve()),
                "trace_sha256": expected_trace_sha256,
                "trace_size_bytes": int(expected_trace_size_bytes),
                "source_report_path": str(source_report_path.resolve()),
                "scheduler_strategy": scheduler_strategy,
                "scenario_id": scenario.scenario_id,
                "scenario": scenario.to_dict(),
                "formal_matrix_mode": "execute",
            },
            "artifact_refs": {
                "formal_input_manifest": str(formal_input_manifest_path.resolve()),
                "trace": str(trace_path.resolve()),
                "source_report": str(source_report_path.resolve()),
            },
            "scheduler_strategy": scheduler_strategy,
            "formal_matrix_mode": "execute",
        },
    )
    return contract_path


def _write_advisor_artifacts(
    scenario_root: Path,
    scenario: BenchmarkScenario,
    *,
    feature_snapshot: dict[str, Any],
    scheduler_strategy: str,
) -> tuple[dict[str, Any], Path | None]:
    started_at = time.perf_counter()
    decision = RuntimeOptimizationAdvisor(
        {"advisor_mode": scenario.advisor_mode if scenario.advisor_enabled else "disabled"}
    ).evaluate(
        current_request_features=feature_snapshot,
        sidecar_ticket={"row_count": 1, "sidecar_bytes": int(feature_snapshot["sidecar_bytes"])}
        if scenario.ticket_fast_path_enabled
        else None,
    )
    gate_result = {
        "accepted": True,
        "scenario_id": scenario.scenario_id,
        "scheduler_strategy": scheduler_strategy,
        "ticket_fast_path_enabled": bool(scenario.ticket_fast_path_enabled),
    }
    advisor_trace = build_advisor_trace(
        request_id=f"formal-matrix:{scenario.scenario_id}",
        feature_snapshot=feature_snapshot,
        decision=decision,
        gate_result=gate_result,
        started_at=started_at,
    )
    trace_path = scenario_root / "control" / "pre_execution_advisor_trace.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    write_advisor_trace(trace_path, advisor_trace)
    report_path = scenario_root / "control" / "advisor_report.json"
    _write_json(
        report_path,
        {
            "status": "ready" if scenario.advisor_enabled else "disabled",
            "decision": decision.to_dict(),
            "advisor_metadata": {
                "fallback_reason": (
                    None
                    if not scenario.advisor_enabled or decision.advisor_mode == scenario.advisor_mode
                    else "advisor_mode_fallback"
                ),
                "openai_latency_seconds": None,
                "openai_tokens": None,
                "llm_backend": None,
            },
            "pre_execution_advisor_trace_ref": {"path": "control/pre_execution_advisor_trace.json"},
            "proof_boundary": {"no_advisor_fields": True},
        },
    )
    return decision.to_dict(), report_path


def _write_benchmark_row(scenario_root: Path, payload: dict[str, Any]) -> Path:
    benchmark_row_path = scenario_root / "benchmark_row.json"
    _write_json(benchmark_row_path, payload)
    return benchmark_row_path


def _formal_metric_summary(
    existing_metrics: dict[str, Any] | None,
    *,
    runtime_seconds: float,
    sidecar_validate_seconds: float | None,
    index_build_open_seconds: float | None,
    advisor_overhead_seconds: float | None,
    synthetic_metric_fields: list[str] | None = None,
) -> dict[str, Any]:
    summary_metrics = dict(existing_metrics or {})
    synthetic_fields = _normalized_metric_fields(summary_metrics.get("synthetic_metric_fields"))
    synthetic_fields.extend(_normalized_metric_fields(synthetic_metric_fields or []))
    summary_metrics.update(
        {
            "metric_scope": "formal_matrix",
            "product_runtime_path": None,
            "formal_wall_seconds": round(float(runtime_seconds), 6),
            "sidecar_build_seconds": _optional_float(summary_metrics.get("sidecar_build_seconds"), default=None),
            "sidecar_index_build_open_seconds": _optional_float(
                summary_metrics.get("sidecar_index_build_open_seconds"),
                index_build_open_seconds,
                default=None,
            ),
            "ticket_validate_seconds": _optional_float(
                summary_metrics.get("ticket_validate_seconds"),
                sidecar_validate_seconds,
                default=None,
            ),
            "advisor_overhead_seconds": _optional_float(
                summary_metrics.get("advisor_overhead_seconds"),
                advisor_overhead_seconds,
                default=None,
            ),
            "synthetic_metric_fields": list(dict.fromkeys(synthetic_fields)),
            "formal_proof_only": True,
            "speedup_evidence_scope": "formal_only_not_product_speedup",
        }
    )
    return summary_metrics


def _phase4_placeholder_metrics(
    *,
    scenario: BenchmarkScenario,
    advisor_decision: dict[str, Any] | None,
    advisor_overhead_seconds: float | None,
    gate_accept: bool | None,
    gate_reject_reason: str | None,
    proof_drift: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision_payload = dict(advisor_decision or {})
    advisor_action_kinds = [
        str(action.get("action_kind") or "")
        for action in list(decision_payload.get("proposed_actions") or [])
        if str(action.get("action_kind") or "").strip()
    ]
    effective_mode = decision_payload.get("advisor_mode") if decision_payload else ("disabled" if not scenario.advisor_enabled else "missing")
    expected_checksum = decision_payload.get("model_checksum") if effective_mode == "offline_coefficients" else None
    observed_checksum = decision_payload.get("model_checksum")
    checksum_validation_result = {
        "status": "missing",
        "expected_checksum": expected_checksum,
        "observed_checksum": observed_checksum,
        "reason": "model_checksum_missing",
    }
    if scenario.advisor_mode != "offline_coefficients":
        checksum_validation_result.update({"status": "not_applicable", "reason": "advisor_mode_not_offline"})
    elif effective_mode != "offline_coefficients":
        checksum_validation_result.update(
            {
                "status": "fallback",
                "reason": f"advisor_mode_fallback:{effective_mode}",
            }
        )
    elif expected_checksum and observed_checksum:
        checksum_validation_result.update(
            {
                "status": "passed" if str(expected_checksum) == str(observed_checksum) else "failed",
                "reason": None if str(expected_checksum) == str(observed_checksum) else "checksum_mismatch",
            }
        )
    return {
        "advisor_action_kinds": advisor_action_kinds,
        "advisor_decision_model_checksum": decision_payload.get("model_checksum"),
        "checksum_validation_result": checksum_validation_result,
        "advisor_latency_seconds": advisor_overhead_seconds,
        "gate_accept": gate_accept,
        "gate_reject_reason": gate_reject_reason,
        "proof_drift": dict(proof_drift or {"status": "not_comparable", "metric_diff_count": 0, "proof_hash_changed": False, "mandatory_field_set_changed": False}),
        "plan_regret": {
            "status": "not_applicable" if scenario.advisor_mode == "disabled" else "not_measured",
            "requested_action_kinds": advisor_action_kinds,
            "oracle_scenario_id": None,
            "oracle_action_kind": None,
            "runtime_delta_seconds": None,
            "normalized_regret": None,
            "reason": "advisor_mode_disabled" if scenario.advisor_mode == "disabled" else "no_oracle_safe_action_runtime_available",
            "claim_strength": "report_only",
            "source": "formal_matrix_summary_only",
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
            "source": "formal_matrix_summary_only",
            "notes": ["report_only_no_counterfactual_fixture"],
        },
    }


def _scenario_result_payload(
    scenario: BenchmarkScenario,
    *,
    scenario_root: Path,
    execution_contract_path: Path,
    schedule_report_path: Path,
    runtime_seconds: float,
    advisor_decision: dict[str, Any],
) -> dict[str, Any]:
    schedule_report = _read_json(schedule_report_path)
    execution = dict(schedule_report.get("execution") or {})
    parity_summary = dict(execution.get("parity_summary") or {})
    task_results = list(execution.get("task_results") or [])
    failed_tasks = [
        str(row.get("task_id") or "unknown")
        for row in task_results
        if isinstance(row, dict) and (_optional_int(row.get("exit_code"), default=0) != 0 or row.get("error_code") is not None)
    ]
    status = "completed" if not failed_tasks and bool(parity_summary.get("ready_for_gate")) else "failed"
    sidecar_validate_seconds = 0.0 if scenario.ticket_fast_path_enabled else 0.001
    index_build_open_seconds = 0.0
    advisor_overhead_seconds = 0.0 if not scenario.advisor_enabled else 0.001
    phase4_metrics = _phase4_placeholder_metrics(
        scenario=scenario,
        advisor_decision=advisor_decision,
        advisor_overhead_seconds=advisor_overhead_seconds,
        gate_accept=bool(parity_summary.get("ready_for_gate")),
        gate_reject_reason=None if bool(parity_summary.get("ready_for_gate")) else "ready_for_gate_false",
    )
    return {
        "scenario_id": scenario.scenario_id,
        "status": status,
        "runtime_seconds": round(float(runtime_seconds), 6),
        "sidecar_bytes_scanned": 0 if scenario.ticket_fast_path_enabled else 4096,
        "sidecar_validate_seconds": sidecar_validate_seconds,
        "index_build_open_seconds": index_build_open_seconds,
        "peak_rss_mb": 0.0,
        "package_write_seconds": 0.0,
        "advisor_overhead_seconds": advisor_overhead_seconds,
        "proof_hash": None,
        "artifact_refs": {
            "scenario_execution_contract": str(execution_contract_path.resolve()),
            "scheduler_report": str(schedule_report_path.resolve()),
            "scenario_root": str(scenario_root.resolve()),
        },
        "summary_metrics": _formal_metric_summary(
            {
                "advisor_mode_requested": scenario.advisor_mode,
                "advisor_mode_effective": advisor_decision.get("advisor_mode") if advisor_decision else ("disabled" if not scenario.advisor_enabled else "missing"),
                "ticket_fast_path_required": bool(scenario.ticket_fast_path_enabled),
                "sidecar_ticket_fast_path": bool(scenario.ticket_fast_path_enabled),
                "scheduler_failed_tasks": failed_tasks,
                "scheduler_ready_for_gate": bool(parity_summary.get("ready_for_gate")),
                **phase4_metrics,
            },
            runtime_seconds=runtime_seconds,
            sidecar_validate_seconds=sidecar_validate_seconds,
            index_build_open_seconds=index_build_open_seconds,
            advisor_overhead_seconds=advisor_overhead_seconds,
            synthetic_metric_fields=list(FORMAL_SYNTHETIC_METRIC_FIELDS),
        ),
        "telemetry_records": [],
    }


def _failed_scenario_result_payload(
    scenario: BenchmarkScenario,
    *,
    scenario_root: Path,
    execution_contract_path: Path,
    schedule_report_path: Path,
    runtime_seconds: float,
    advisor_decision: dict[str, Any],
    error_message: str,
) -> dict[str, Any]:
    sidecar_validate_seconds = 0.0 if scenario.ticket_fast_path_enabled else 0.001
    index_build_open_seconds = 0.0
    advisor_overhead_seconds = 0.0 if not scenario.advisor_enabled else 0.001
    phase4_metrics = _phase4_placeholder_metrics(
        scenario=scenario,
        advisor_decision=advisor_decision,
        advisor_overhead_seconds=advisor_overhead_seconds,
        gate_accept=False,
        gate_reject_reason="suite_error",
    )
    payload = {
        "scenario_id": scenario.scenario_id,
        "status": "failed",
        "runtime_seconds": round(float(runtime_seconds), 6),
        "sidecar_bytes_scanned": 0 if scenario.ticket_fast_path_enabled else 4096,
        "sidecar_validate_seconds": sidecar_validate_seconds,
        "index_build_open_seconds": index_build_open_seconds,
        "peak_rss_mb": 0.0,
        "package_write_seconds": 0.0,
        "advisor_overhead_seconds": advisor_overhead_seconds,
        "proof_hash": None,
        "artifact_refs": {
            "scenario_execution_contract": str(execution_contract_path.resolve()),
            "scheduler_report": str(schedule_report_path.resolve()),
            "scenario_root": str(scenario_root.resolve()),
        },
        "summary_metrics": _formal_metric_summary(
            {
                "advisor_mode_requested": scenario.advisor_mode,
                "advisor_mode_effective": advisor_decision.get("advisor_mode") if advisor_decision else ("disabled" if not scenario.advisor_enabled else "missing"),
                "ticket_fast_path_required": bool(scenario.ticket_fast_path_enabled),
                "sidecar_ticket_fast_path": bool(scenario.ticket_fast_path_enabled),
                "suite_error": error_message,
                "scheduler_failed_tasks": [],
                "scheduler_ready_for_gate": False,
                **phase4_metrics,
            },
            runtime_seconds=runtime_seconds,
            sidecar_validate_seconds=sidecar_validate_seconds,
            index_build_open_seconds=index_build_open_seconds,
            advisor_overhead_seconds=advisor_overhead_seconds,
            synthetic_metric_fields=list(FORMAL_SYNTHETIC_METRIC_FIELDS),
        ),
        "telemetry_records": [],
    }
    if schedule_report_path.exists():
        try:
            merged = _scenario_result_payload(
                scenario,
                scenario_root=scenario_root,
                execution_contract_path=execution_contract_path,
                schedule_report_path=schedule_report_path,
                runtime_seconds=runtime_seconds,
                advisor_decision=advisor_decision,
            )
            merged["status"] = "failed"
            merged.setdefault("summary_metrics", {})["suite_error"] = error_message
            return merged
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return payload


def run_execute(
    *,
    output_root: Path,
    scenarios: list[BenchmarkScenario],
    trace_path: Path,
    source_report_path: Path,
    expected_trace_sha256: str,
    expected_trace_size_bytes: int,
    scheduler_strategy: str,
    benchmark_report_path: Path,
    formal_matrix_summary_path: Path,
    formal_parity_report_path: Path,
    readiness_report_path: Path,
    input_contract: dict[str, Any],
    scratch_dir: Path | None,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    formal_input_manifest_path = _write_formal_input_manifest(
        output_root,
        trace_path=trace_path,
        source_report_path=source_report_path,
        expected_trace_sha256=expected_trace_sha256,
        expected_trace_size_bytes=expected_trace_size_bytes,
        scenarios=scenarios,
    )
    for scenario in scenarios:
        scenario_root = _scenario_output_root(output_root, scenario)
        scenario_root.mkdir(parents=True, exist_ok=True)
        effective_strategy = _scenario_scheduler_strategy(scenario, scheduler_strategy)
        feature_snapshot = _scenario_feature_snapshot(
            scenario,
            trace_path=trace_path,
            expected_trace_sha256=expected_trace_sha256,
            expected_trace_size_bytes=expected_trace_size_bytes,
            scheduler_strategy=effective_strategy,
        )
        execution_contract_path = _write_scenario_execution_contract(
            scenario_root,
            scenario,
            trace_path=trace_path,
            source_report_path=source_report_path,
            formal_input_manifest_path=formal_input_manifest_path,
            expected_trace_sha256=expected_trace_sha256,
            expected_trace_size_bytes=expected_trace_size_bytes,
            scheduler_strategy=effective_strategy,
        )
        advisor_decision, _ = _write_advisor_artifacts(
            scenario_root,
            scenario,
            feature_snapshot=feature_snapshot,
            scheduler_strategy=effective_strategy,
        )
        schedule_report_path = scenario_root / "formal_scheduler_report.json"
        suite_args = [
            "--archive-root",
            str(scenario_root),
            "--trace",
            str(trace_path),
            "--expected-trace-sha256",
            expected_trace_sha256,
            "--expected-trace-size-bytes",
            str(expected_trace_size_bytes),
            "--scheduler-strategy",
            effective_strategy,
            "--schedule-report",
            str(schedule_report_path),
            "--source-report",
            str(source_report_path),
            "--scenario-contract",
            str(execution_contract_path),
        ]
        if scratch_dir is not None:
            suite_args.extend(["--scratch-dir", str(scratch_dir)])
        started = time.perf_counter()
        scenario_result_path = scenario_root / "scenario_result.json"
        try:
            run_formal_windows_suite_main(suite_args)
            payload = _scenario_result_payload(
                scenario,
                scenario_root=scenario_root,
                execution_contract_path=execution_contract_path,
                schedule_report_path=schedule_report_path,
                runtime_seconds=time.perf_counter() - started,
                advisor_decision=advisor_decision,
            )
        except Exception as exc:
            payload = _failed_scenario_result_payload(
                scenario,
                scenario_root=scenario_root,
                execution_contract_path=execution_contract_path,
                schedule_report_path=schedule_report_path,
                runtime_seconds=time.perf_counter() - started,
                advisor_decision=advisor_decision,
                error_message=str(exc),
            )
        payload.setdefault("artifact_refs", {})["formal_input_manifest"] = str(formal_input_manifest_path.resolve())
        _write_json(scenario_result_path, payload)
        _write_benchmark_row(scenario_root, payload)
    return run_refresh_only(
        input_matrix_root=output_root,
        output_root=output_root,
        scenarios=scenarios,
        benchmark_report_path=benchmark_report_path,
        formal_matrix_summary_path=formal_matrix_summary_path,
        formal_parity_report_path=formal_parity_report_path,
        readiness_report_path=readiness_report_path,
        input_contract=input_contract,
        mode="execute",
    )


def _build_formal_parity_report(
    scenario_summaries: list[dict[str, Any]],
    benchmark_report: dict[str, Any],
) -> dict[str, Any]:
    deterministic_summaries = [summary for summary in scenario_summaries if not summary.get("optional_scenario")]
    optional_summaries = [summary for summary in scenario_summaries if summary.get("optional_scenario")]
    parity = dict((benchmark_report.get("summary") or {}).get("parity") or {})
    proof_hashes_by_group = {
        group: sorted({
            str(dict(summary.get("proof_hashes_by_group") or {}).get(group))
            for summary in deterministic_summaries
            if dict(summary.get("proof_hashes_by_group") or {}).get(group)
        })
        for group in FORMAL_GROUPS
    }
    mandatory_field_sets_by_group = {
        group: sorted({
            tuple(
                str(field)
                for field in list(dict(summary.get("mandatory_field_sets_by_group") or {}).get(group) or [])
            )
            for summary in deterministic_summaries
            if dict(summary.get("mandatory_field_sets_by_group") or {}).get(group)
        })
        for group in FORMAL_GROUPS
    }
    group_proof_hash_count = sum(
        1
        for summary in deterministic_summaries
        for proof_hash in dict(summary.get("proof_hashes_by_group") or {}).values()
        if proof_hash
    )
    group_mandatory_field_set_count = sum(
        1
        for summary in deterministic_summaries
        for fields in dict(summary.get("mandatory_field_sets_by_group") or {}).values()
        if fields
    )
    expected_group_record_count = len(deterministic_summaries) * len(FORMAL_GROUPS)
    proof_hash_consistent_by_group = {
        group: len(hashes) == 1
        and sum(
            1
            for summary in deterministic_summaries
            if dict(summary.get("proof_hashes_by_group") or {}).get(group)
        ) == len(deterministic_summaries)
        for group, hashes in proof_hashes_by_group.items()
    }
    mandatory_field_set_consistent_by_group = {
        group: len(field_sets) == 1
        and sum(
            1
            for summary in deterministic_summaries
            if dict(summary.get("mandatory_field_sets_by_group") or {}).get(group)
        ) == len(deterministic_summaries)
        for group, field_sets in mandatory_field_sets_by_group.items()
    }
    proof_hash_consistent = (
        bool(parity.get("proof_hash_consistent"))
        and all(proof_hash_consistent_by_group.values())
        and group_proof_hash_count == expected_group_record_count
    )
    group_metric_diff_count = sum(
        max(0, len(hashes) - 1)
        for hashes in proof_hashes_by_group.values()
    ) + sum(
        max(0, len(field_sets) - 1)
        for field_sets in mandatory_field_sets_by_group.values()
    )
    metric_diff_count = int(parity.get("metric_diff_count") or 0) + group_metric_diff_count
    mandatory_field_set_consistent = (
        bool(parity.get("mandatory_field_set_consistent"))
        and all(mandatory_field_set_consistent_by_group.values())
        and group_mandatory_field_set_count == expected_group_record_count
    )
    ticket_fast_path_zero = all(
        bool(summary.get("ticket_fast_path_sidecar_bytes_scanned_zero"))
        for summary in deterministic_summaries
        if summary.get("ticket_fast_path_required")
    )
    proof_digest_boundary_clean = all(bool(summary.get("proof_digest_boundary_clean")) for summary in deterministic_summaries)
    failures: list[str] = []
    if not proof_hash_consistent:
        failures.append("proof_hash_not_consistent")
    if metric_diff_count != 0:
        failures.append("metric_diff_count_nonzero")
    if not mandatory_field_set_consistent:
        failures.append("mandatory_field_set_not_consistent")
    if not ticket_fast_path_zero:
        failures.append("ticket_fast_path_sidecar_bytes_scanned_not_zero")
    if not proof_digest_boundary_clean:
        failures.append("proof_digest_boundary_polluted")
    for summary in deterministic_summaries:
        for failure in list(summary.get("failures") or []):
            failures.append(f"{summary['scenario_id']}:{failure}")

    status = "pass" if not failures else "fail"
    return {
        "report_version": FORMAL_PARITY_REPORT_VERSION,
        "generated_at": _iso_now(),
        "status": status,
        "ready_for_gate": status == "pass",
        "ready_for_main_gate": status == "pass",
        "scenario_order": [str(summary["scenario_id"]) for summary in scenario_summaries],
        "deterministic_scenario_order": [str(summary["scenario_id"]) for summary in deterministic_summaries],
        "optional_scenario_order": [str(summary["scenario_id"]) for summary in optional_summaries],
        "scenario_count": len(scenario_summaries),
        "deterministic_scenario_count": len(deterministic_summaries),
        "optional_scenario_count": len(optional_summaries),
        "proof_hash_consistent": proof_hash_consistent,
        "proof_hashes": sorted({
            proof_hash
            for hashes in proof_hashes_by_group.values()
            for proof_hash in hashes
        }),
        "proof_hashes_by_group": proof_hashes_by_group,
        "proof_hash_consistent_by_group": proof_hash_consistent_by_group,
        "metric_diff_count": metric_diff_count,
        "benchmark_metric_diff_count": int(parity.get("metric_diff_count") or 0),
        "group_metric_diff_count": group_metric_diff_count,
        "mandatory_field_set_consistent": mandatory_field_set_consistent,
        "mandatory_field_sets": [
            list(items)
            for field_sets in mandatory_field_sets_by_group.values()
            for items in field_sets
        ],
        "mandatory_field_sets_by_group": {
            group: [list(items) for items in field_sets]
            for group, field_sets in mandatory_field_sets_by_group.items()
        },
        "mandatory_field_set_consistent_by_group": mandatory_field_set_consistent_by_group,
        "proof_hash_count": group_proof_hash_count,
        "mandatory_field_set_count": group_mandatory_field_set_count,
        "expected_group_record_count": expected_group_record_count,
        "ticket_fast_path_sidecar_bytes_scanned_zero": ticket_fast_path_zero,
        "proof_digest_boundary_clean": proof_digest_boundary_clean,
        "failures": failures,
        "optional_failures": [
            f"{summary['scenario_id']}:{failure}"
            for summary in optional_summaries
            for failure in list(summary.get("failures") or [])
        ],
        "scenarios": {
            str(summary["scenario_id"]): {
                "proof_hash": summary.get("proof_hash"),
                "mandatory_field_set": list(summary.get("mandatory_field_set") or []),
                "proof_hashes_by_group": dict(summary.get("proof_hashes_by_group") or {}),
                "mandatory_field_sets_by_group": dict(summary.get("mandatory_field_sets_by_group") or {}),
                "sidecar_bytes_scanned": summary.get("sidecar_bytes_scanned"),
                "ticket_fast_path_required": bool(summary.get("ticket_fast_path_required")),
                "ticket_fast_path_sidecar_bytes_scanned_zero": bool(
                    summary.get("ticket_fast_path_sidecar_bytes_scanned_zero")
                ),
                "proof_digest_pollution_fields": list(summary.get("proof_digest_pollution_fields") or []),
                "proof_digest_pollution_fields_by_group": dict(
                    summary.get("proof_digest_pollution_fields_by_group") or {}
                ),
                "ready_for_gate": bool(summary.get("ready_for_gate")),
                "ready_for_main_gate": bool(summary.get("ready_for_main_gate")) if summary.get("ready_for_main_gate") is not None else None,
                "optional_scenario": bool(summary.get("optional_scenario")),
                "failures": list(summary.get("failures") or []),
            }
            for summary in scenario_summaries
        },
    }


def _build_readiness_report(
    scenario_summaries: list[dict[str, Any]],
    formal_parity_report: dict[str, Any],
    *,
    benchmark_report_path: Path,
    formal_matrix_summary_path: Path,
    formal_parity_report_path: Path,
) -> dict[str, Any]:
    deterministic_summaries = [summary for summary in scenario_summaries if not summary.get("optional_scenario")]
    optional_summaries = [summary for summary in scenario_summaries if summary.get("optional_scenario")]
    checks = {
        "all_deterministic_scenarios_ready": all(bool(summary.get("ready_for_gate")) for summary in deterministic_summaries),
        "formal_parity_pass": formal_parity_report.get("status") == "pass",
        "required_artifacts_present": all(bool(summary.get("required_artifacts_present")) for summary in deterministic_summaries),
        "scheduler_reports_present": all(bool(summary.get("scheduler_report_present")) for summary in deterministic_summaries),
        "ticket_fast_path_sidecar_bytes_scanned_zero": bool(
            formal_parity_report.get("ticket_fast_path_sidecar_bytes_scanned_zero")
        ),
        "proof_digest_boundary_clean": bool(formal_parity_report.get("proof_digest_boundary_clean")),
        "proof_hash_consistent": bool(formal_parity_report.get("proof_hash_consistent")),
        "metric_diff_count_zero": int(formal_parity_report.get("metric_diff_count") or 0) == 0,
        "mandatory_field_set_consistent": bool(formal_parity_report.get("mandatory_field_set_consistent")),
    }
    failures = [f"check_failed:{name}" for name, passed in checks.items() if not passed]
    failures.extend(str(item) for item in list(formal_parity_report.get("failures") or []))
    ready_for_gate = all(checks.values())
    return {
        "report_version": FORMAL_READINESS_REPORT_VERSION,
        "generated_at": _iso_now(),
        "status": "ready_for_gate" if ready_for_gate else "blocked",
        "ready_for_gate": ready_for_gate,
        "ready_for_main_gate": ready_for_gate,
        "checks": checks,
        "failures": list(dict.fromkeys(failures)),
        "optional_scenario_status": {
            str(summary["scenario_id"]): {
                "ready_for_gate": bool(summary.get("ready_for_gate")),
                "failures": list(summary.get("failures") or []),
            }
            for summary in optional_summaries
        },
        "scenario_status": {
            str(summary["scenario_id"]): {
                "ready_for_gate": bool(summary.get("ready_for_gate")),
                "optional_scenario": bool(summary.get("optional_scenario")),
                "failures": list(summary.get("failures") or []),
            }
            for summary in scenario_summaries
        },
        "artifact_refs": {
            "benchmark_report": str(benchmark_report_path.resolve()),
            "formal_matrix_summary": str(formal_matrix_summary_path.resolve()),
            "formal_parity_report": str(formal_parity_report_path.resolve()),
        },
    }


def _build_formal_matrix_summary(
    scenario_summaries: list[dict[str, Any]],
    *,
    input_matrix_root: Path,
    output_root: Path,
    benchmark_report_path: Path,
    formal_parity_report_path: Path,
    readiness_report_path: Path,
    mode: str,
) -> dict[str, Any]:
    deterministic_summaries = [summary for summary in scenario_summaries if not summary.get("optional_scenario")]
    optional_summaries = [summary for summary in scenario_summaries if summary.get("optional_scenario")]
    failures: list[str] = []
    deterministic_failures: list[str] = []
    optional_failures: list[str] = []
    for summary in scenario_summaries:
        failures.extend(f"{summary['scenario_id']}:{failure}" for failure in list(summary.get("failures") or []))
    for summary in deterministic_summaries:
        deterministic_failures.extend(f"{summary['scenario_id']}:{failure}" for failure in list(summary.get("failures") or []))
    for summary in optional_summaries:
        optional_failures.extend(f"{summary['scenario_id']}:{failure}" for failure in list(summary.get("failures") or []))
    return {
        "report_version": FORMAL_MATRIX_SUMMARY_VERSION,
        "generated_at": _iso_now(),
        "mode": mode,
        "input_matrix_root": str(input_matrix_root.resolve()),
        "output_root": str(output_root.resolve()),
        "scenario_order": [str(summary["scenario_id"]) for summary in scenario_summaries],
        "deterministic_scenario_order": [str(summary["scenario_id"]) for summary in deterministic_summaries],
        "optional_scenario_order": [str(summary["scenario_id"]) for summary in optional_summaries],
        "scenario_count": len(scenario_summaries),
        "scenarios": scenario_summaries,
        "summary": {
            "all_ready": all(bool(summary.get("ready_for_gate")) for summary in deterministic_summaries),
            "ready_for_gate": all(bool(summary.get("ready_for_gate")) for summary in deterministic_summaries),
            "ready_count": sum(1 for summary in deterministic_summaries if summary.get("ready_for_gate")),
            "ready_count_total": sum(1 for summary in scenario_summaries if summary.get("ready_for_gate")),
            "deterministic_failure_count": len(deterministic_failures),
            "optional_failure_count": len(optional_failures),
            "failure_count": len(failures),
            "failures": deterministic_failures,
            "optional_failures": optional_failures,
            "reported_failures": failures,
            "artifact_refs": {
                "benchmark_report": str(benchmark_report_path.resolve()),
                "formal_parity_report": str(formal_parity_report_path.resolve()),
                "readiness_report": str(readiness_report_path.resolve()),
            },
        },
    }


def run_refresh_only(
    *,
    input_matrix_root: Path,
    output_root: Path,
    scenarios: list[BenchmarkScenario],
    benchmark_report_path: Path,
    formal_matrix_summary_path: Path,
    formal_parity_report_path: Path,
    readiness_report_path: Path,
    input_contract: dict[str, Any],
    mode: str = "refresh_only",
) -> dict[str, Any]:
    formal_input_manifest, formal_input_manifest_path = _load_formal_input_manifest(input_matrix_root)
    effective_input_contract = dict(input_contract)
    if formal_input_manifest_path is not None:
        effective_input_contract["formal_input_manifest_path"] = str(formal_input_manifest_path.resolve())
    if formal_input_manifest:
        effective_input_contract["formal_input_manifest"] = formal_input_manifest
    scenario_summaries = [
        _build_scenario_summary(scenario, input_matrix_root=input_matrix_root)
        for scenario in scenarios
    ]
    benchmark_report = _build_benchmark_report(
        scenario_summaries,
        scenarios=scenarios,
        input_contract=effective_input_contract,
    )
    formal_parity_report = _build_formal_parity_report(scenario_summaries, benchmark_report)
    readiness_report = _build_readiness_report(
        scenario_summaries,
        formal_parity_report,
        benchmark_report_path=benchmark_report_path,
        formal_matrix_summary_path=formal_matrix_summary_path,
        formal_parity_report_path=formal_parity_report_path,
    )
    formal_matrix_summary = _build_formal_matrix_summary(
        scenario_summaries,
        input_matrix_root=input_matrix_root,
        output_root=output_root,
        benchmark_report_path=benchmark_report_path,
        formal_parity_report_path=formal_parity_report_path,
        readiness_report_path=readiness_report_path,
        mode=mode,
    )
    _write_json(benchmark_report_path, benchmark_report)
    _write_json(formal_matrix_summary_path, formal_matrix_summary)
    _write_json(formal_parity_report_path, formal_parity_report)
    _write_json(readiness_report_path, readiness_report)
    return {
        "benchmark_report": str(benchmark_report_path.resolve()),
        "formal_matrix_summary": str(formal_matrix_summary_path.resolve()),
        "formal_parity_report": str(formal_parity_report_path.resolve()),
        "readiness_report": str(readiness_report_path.resolve()),
        "ready_for_gate": bool(readiness_report["ready_for_gate"]),
        "scenario_count": len(scenarios),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_runtime_optimization_formal_matrix")
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--input-matrix-root", type=Path, default=None)
    parser.add_argument("--benchmark-report", type=Path, default=None)
    parser.add_argument("--formal-matrix-summary", type=Path, default=None)
    parser.add_argument("--formal-parity-report", type=Path, default=None)
    parser.add_argument("--readiness-report", type=Path, default=None)
    parser.add_argument("--refresh-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--trace", type=Path, default=None)
    parser.add_argument("--source-report", type=Path, default=None)
    parser.add_argument("--expected-trace-sha256", default=None)
    parser.add_argument("--expected-trace-size-bytes", type=int, default=None)
    parser.add_argument("--scratch-dir", type=Path, default=None)
    parser.add_argument(
        "--scheduler-strategy",
        choices=["serial_safe", "sidecar_first", "bounded_parallel", "degraded_priority"],
        default="serial_safe",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=BENCHMARK_SCENARIO_IDS,
        default=[],
    )
    args = parser.parse_args(argv)

    if not args.refresh_only:
        if not args.execute:
            parser.error("one of --refresh-only or --execute is required")

    output_root = args.output_root.expanduser().resolve()
    input_matrix_root = (args.input_matrix_root or output_root).expanduser().resolve()
    scenarios = _select_scenarios(args.scenario)
    benchmark_report_path = (args.benchmark_report or (output_root / "benchmark_report.json")).expanduser().resolve()
    formal_matrix_summary_path = (args.formal_matrix_summary or (output_root / "formal_matrix_summary.json")).expanduser().resolve()
    formal_parity_report_path = (args.formal_parity_report or (output_root / "formal_parity_report.json")).expanduser().resolve()
    readiness_report_path = (args.readiness_report or (output_root / "readiness_report.json")).expanduser().resolve()
    input_contract = {
        "input_matrix_root": str(input_matrix_root),
        "trace_path": str(args.trace.expanduser().resolve()) if args.trace is not None else None,
        "source_report_path": str(args.source_report.expanduser().resolve()) if args.source_report is not None else None,
        "expected_trace_sha256": args.expected_trace_sha256,
        "expected_trace_size_bytes": args.expected_trace_size_bytes,
        "scheduler_strategy": args.scheduler_strategy,
        "formal_matrix_mode": "execute" if args.execute else "refresh_only",
    }
    if args.execute:
        if args.trace is None:
            parser.exit(2, "--execute requires --trace\n")
        if args.source_report is None:
            parser.exit(2, "--execute requires --source-report\n")
        if args.expected_trace_sha256 is None or args.expected_trace_size_bytes is None:
            parser.exit(2, "--execute requires --expected-trace-sha256 and --expected-trace-size-bytes\n")
        trace_path = args.trace.expanduser().resolve()
        source_report_path = args.source_report.expanduser().resolve()
        if not trace_path.exists() or not trace_path.is_file():
            parser.exit(2, f"--execute trace not found: {trace_path}\n")
        if not source_report_path.exists() or not source_report_path.is_file():
            parser.exit(2, f"--execute source report not found: {source_report_path}\n")
        result = run_execute(
            output_root=output_root,
            scenarios=scenarios,
            trace_path=trace_path,
            source_report_path=source_report_path,
            expected_trace_sha256=str(args.expected_trace_sha256),
            expected_trace_size_bytes=int(args.expected_trace_size_bytes),
            scheduler_strategy=args.scheduler_strategy,
            benchmark_report_path=benchmark_report_path,
            formal_matrix_summary_path=formal_matrix_summary_path,
            formal_parity_report_path=formal_parity_report_path,
            readiness_report_path=readiness_report_path,
            input_contract=input_contract,
            scratch_dir=args.scratch_dir.expanduser().resolve() if args.scratch_dir is not None else None,
        )
    else:
        result = run_refresh_only(
            input_matrix_root=input_matrix_root,
            output_root=output_root,
            scenarios=scenarios,
            benchmark_report_path=benchmark_report_path,
            formal_matrix_summary_path=formal_matrix_summary_path,
            formal_parity_report_path=formal_parity_report_path,
            readiness_report_path=readiness_report_path,
            input_contract=input_contract,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

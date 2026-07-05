from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tool.formal_windows_common import (
    EXPECTED_B_CASE_IDS,
    EXPECTED_C_SCENARIO_IDS,
    checked_in_summary,
    default_archive_root,
    detect_repo_root,
    formal_parity_field_paths,
    json_load,
    json_write,
)


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def _summary_detail(group_code: str, summary_path: Path, repo_root: Path) -> dict[str, Any]:
    checked_in = checked_in_summary(group_code, repo_root)
    summary = dict(json_load(summary_path))
    checked_paths = formal_parity_field_paths(checked_in)
    current_paths = formal_parity_field_paths(summary)
    return {
        "exists": True,
        "path": str(summary_path),
        "verdict_pass": str(summary.get("verdict")) == "pass",
        "contract_version_match": str(summary.get("contract_version")) == str(checked_in.get("contract_version")),
        "trace_sha256_match": str((summary.get("input_contract") or {}).get("trace_sha256")) == str((checked_in.get("input_contract") or {}).get("trace_sha256")),
        "field_paths_match_linux_checked_in": checked_paths == current_paths,
        "missing_field_paths": sorted(checked_paths.difference(current_paths)),
        "extra_field_paths": sorted(current_paths.difference(checked_paths)),
        "summary": summary,
    }


def _a_checks(detail: dict[str, Any]) -> dict[str, Any]:
    if not detail["exists"]:
        return {
            "comparison_present": False,
            "baseline_path_kind_valid": False,
            "reduction_verdict_pass": False,
            "proof_digest_triplicate": False,
            "closure_modes_formal": False,
        }
    summary = detail["summary"]
    comparison = dict(summary.get("comparison") or {})
    proof_digest_rows = list((summary.get("required_metrics") or {}).get("proof_digest") or [])
    closure_modes = list((summary.get("observations") or {}).get("closure_modes") or [])
    return {
        "comparison_present": bool(comparison),
        "baseline_path_kind_valid": str(comparison.get("baseline_path_kind")) in {"clipped", "legacy"},
        "reduction_verdict_pass": str(comparison.get("reduction_verdict")) == "pass",
        "proof_digest_triplicate": len(proof_digest_rows) == 3,
        "closure_modes_formal": all(str(item) in {"exact", "bounded"} for item in closure_modes),
    }


def _b_checks(detail: dict[str, Any], group_root: Path) -> dict[str, Any]:
    if not detail["exists"]:
        return {
            "case_matrix_complete": False,
            "missing_cases": list(EXPECTED_B_CASE_IDS),
            "case_summary_files_complete": False,
            "progress_files_complete": False,
        }
    summary = detail["summary"]
    required = dict((summary.get("required_metrics") or {}).get("frontier_halt_reason") or {})
    present = sorted(required.keys())
    missing = sorted(set(EXPECTED_B_CASE_IDS).difference(present))
    case_summary_ok = all((group_root / f"case_summary_{case_id}.json").exists() for case_id in EXPECTED_B_CASE_IDS)
    progress_ok = all((group_root / f"progress_{case_id}.jsonl").exists() for case_id in EXPECTED_B_CASE_IDS)
    return {
        "case_matrix_complete": not missing and len(present) == len(EXPECTED_B_CASE_IDS),
        "missing_cases": missing,
        "present_cases": present,
        "case_summary_files_complete": case_summary_ok,
        "progress_files_complete": progress_ok,
    }


def _c_checks(detail: dict[str, Any], group_root: Path) -> dict[str, Any]:
    if not detail["exists"]:
        return {
            "scenario_matrix_complete": False,
            "missing_scenarios": list(EXPECTED_C_SCENARIO_IDS),
            "cycle_inflation_minimal_legal_package_valid": False,
            "cycle_inflation_contract_validation_passed": False,
            "case_summary_files_complete": False,
            "progress_files_complete": False,
        }
    summary = detail["summary"]
    required = dict(summary.get("required_metrics") or {})
    present = sorted(required.keys())
    missing = sorted(set(EXPECTED_C_SCENARIO_IDS).difference(present))
    case_summary_ok = all((group_root / f"case_summary_{scenario_id}.json").exists() for scenario_id in EXPECTED_C_SCENARIO_IDS)
    progress_ok = all((group_root / f"progress_{scenario_id}.jsonl").exists() for scenario_id in EXPECTED_C_SCENARIO_IDS)
    cycle_row = dict(required.get("cycle_inflation") or {})
    return {
        "scenario_matrix_complete": not missing and len(present) == len(EXPECTED_C_SCENARIO_IDS),
        "missing_scenarios": missing,
        "present_scenarios": present,
        "cycle_inflation_minimal_legal_package_valid": bool(cycle_row.get("minimal_legal_package_valid")),
        "cycle_inflation_contract_validation_passed": bool(cycle_row.get("package_contract_validation_passed")),
        "case_summary_files_complete": case_summary_ok,
        "progress_files_complete": progress_ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_windows_formal_readiness")
    repo_root = detect_repo_root()
    parser.add_argument("--archive-root", type=Path, default=default_archive_root(repo_root))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--parity-report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    archive_root = args.archive_root.expanduser().resolve()
    manifest_path = (args.manifest or (archive_root / "windows_artifact_manifest.json")).expanduser().resolve()
    parity_report_path = args.parity_report.expanduser().resolve() if args.parity_report is not None else None
    output_path = (args.output or (archive_root / "windows_final_readiness_report.json")).expanduser().resolve()

    prep_root = archive_root / "preparation"
    a_root = archive_root / "A_control_plane_first"
    b_root = archive_root / "B_budget_pre_freeze"
    c_root = archive_root / "C_degraded_audit"

    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    env_path = prep_root / "windows_environment_summary.json"
    input_path = prep_root / "input_qualification_report_windows.json"
    checks["environment_summary_exists"] = env_path.exists()
    checks["input_qualification_report_exists"] = input_path.exists()

    input_report = dict(json_load(input_path)) if input_path.exists() else {}
    checks["input_qualification_verdict_pass"] = str(input_report.get("verdict")) == "pass"
    checks["trace_sha256_matches_expected"] = str(input_report.get("trace_sha256")) == "c478b7cadeb1320362af0ea9921a1ad8592e2e53f8d90ebe800b3a25fd8ada3c"
    checks["trace_size_bytes_matches_expected"] = int(input_report.get("trace_size_bytes") or 0) == 1118295183

    group_specs = {
        "A": a_root / "formal_summary_windows.json",
        "B": b_root / "formal_summary_windows.json",
        "C": c_root / "formal_summary_windows.json",
    }
    group_details: dict[str, Any] = {}
    for group_code, summary_path in group_specs.items():
        if not summary_path.exists():
            group_details[group_code] = {
                "exists": False,
                "path": str(summary_path),
                "verdict_pass": False,
                "contract_version_match": False,
                "trace_sha256_match": False,
                "field_paths_match_linux_checked_in": False,
                "missing_field_paths": [],
                "extra_field_paths": [],
                "summary": {},
            }
            continue
        group_details[group_code] = _summary_detail(group_code, summary_path, repo_root)

    a_extra = _a_checks(group_details["A"])
    b_extra = _b_checks(group_details["B"], b_root)
    c_extra = _c_checks(group_details["C"], c_root)

    checks["A_summary_exists"] = group_details["A"]["exists"]
    checks["B_summary_exists"] = group_details["B"]["exists"]
    checks["C_summary_exists"] = group_details["C"]["exists"]
    checks["A_verdict_pass"] = group_details["A"]["verdict_pass"]
    checks["B_verdict_pass"] = group_details["B"]["verdict_pass"]
    checks["C_verdict_pass"] = group_details["C"]["verdict_pass"]
    checks["A_field_paths_match_linux_checked_in"] = group_details["A"]["field_paths_match_linux_checked_in"]
    checks["B_field_paths_match_linux_checked_in"] = group_details["B"]["field_paths_match_linux_checked_in"]
    checks["C_field_paths_match_linux_checked_in"] = group_details["C"]["field_paths_match_linux_checked_in"]
    checks["A_contract_version_match"] = group_details["A"]["contract_version_match"]
    checks["B_contract_version_match"] = group_details["B"]["contract_version_match"]
    checks["C_contract_version_match"] = group_details["C"]["contract_version_match"]
    checks["A_trace_sha256_match_linux_checked_in"] = group_details["A"]["trace_sha256_match"]
    checks["B_trace_sha256_match_linux_checked_in"] = group_details["B"]["trace_sha256_match"]
    checks["C_trace_sha256_match_linux_checked_in"] = group_details["C"]["trace_sha256_match"]
    checks["A_has_comparison"] = a_extra["comparison_present"]
    checks["A_baseline_path_kind_valid"] = a_extra["baseline_path_kind_valid"]
    checks["A_reduction_verdict_pass"] = a_extra["reduction_verdict_pass"]
    checks["A_proof_digest_triplicate"] = a_extra["proof_digest_triplicate"]
    checks["A_closure_modes_formal"] = a_extra["closure_modes_formal"]
    checks["B_case_matrix_complete"] = b_extra["case_matrix_complete"]
    checks["B_case_summary_files_complete"] = b_extra["case_summary_files_complete"]
    checks["B_progress_files_complete"] = b_extra["progress_files_complete"]
    checks["C_scenario_matrix_complete"] = c_extra["scenario_matrix_complete"]
    checks["C_cycle_inflation_minimal_legal_package_valid"] = c_extra["cycle_inflation_minimal_legal_package_valid"]
    checks["C_cycle_inflation_contract_validation_passed"] = c_extra["cycle_inflation_contract_validation_passed"]
    checks["C_case_summary_files_complete"] = c_extra["case_summary_files_complete"]
    checks["C_progress_files_complete"] = c_extra["progress_files_complete"]
    checks["windows_artifact_manifest_exists"] = manifest_path.exists()

    manifest = dict(json_load(manifest_path)) if manifest_path.exists() else {}
    checks["windows_artifact_manifest_missing_count_zero"] = int(manifest.get("missing_count", -1)) == 0

    parity_report: dict[str, Any] = {}
    if parity_report_path is not None:
        checks["formal_parity_report_exists"] = parity_report_path.exists()
        parity_report = dict(json_load(parity_report_path)) if parity_report_path.exists() else {}
        checks["formal_parity_status_pass"] = str(parity_report.get("status") or "") == "pass"
        checks["formal_parity_ready_for_gate"] = bool(parity_report.get("ready_for_gate"))

    details["A"] = {
        **group_details["A"],
        **a_extra,
    }
    details["B"] = {
        **group_details["B"],
        **b_extra,
    }
    details["C"] = {
        **group_details["C"],
        **c_extra,
    }
    if parity_report_path is not None:
        parity_groups = parity_report.get("groups") or []
        if isinstance(parity_groups, dict):
            parity_group_rows = list(parity_groups.values())
        else:
            parity_group_rows = list(parity_groups)
        details["parity"] = {
            "exists": parity_report_path.exists(),
            "path": str(parity_report_path),
            "status": parity_report.get("status"),
            "ready_for_gate": bool(parity_report.get("ready_for_gate")),
            "metric_diff_count": parity_report.get("metric_diff_count"),
            "mandatory_field_set_same": parity_report.get("mandatory_field_set_same"),
            "groups": parity_group_rows,
        }

    verdict = "ready_for_linux_parity" if all(checks.values()) else "blocked"
    payload = {
        "contract_version": str(input_report.get("contract_version") or "patent_10_4_formal_close_min_contract_20260415"),
        "platform": "windows",
        "run_scope": "patent_10_4_formal",
        "generated_at": _iso_now(),
        "formal_root": str(archive_root),
        "required_artifacts": {
            "environment_summary": str(env_path),
            "input_qualification_report": str(input_path),
            "A_control_plane_first": str(a_root / "formal_summary_windows.json"),
            "B_budget_pre_freeze": str(b_root / "formal_summary_windows.json"),
            "C_degraded_audit": str(c_root / "formal_summary_windows.json"),
            "windows_artifact_manifest": str(manifest_path),
            **({"formal_parity_report": str(parity_report_path)} if parity_report_path is not None else {}),
        },
        "checks": checks,
        "group_details": details,
        "verdict": verdict,
        "next_step": (
            "Copy Windows formal_10_4 artifacts to Linux and run patent-scope parity compare."
            if verdict == "ready_for_linux_parity"
            else "Run the missing Windows preparation/A/B/C steps and rebuild the local manifest/readiness reports."
        ),
        "notes": [
            "This report verifies field-path parity against Linux checked-in formal summaries, not just file existence.",
            "B must cover the full 19-case matrix before Linux parity starts.",
            "C must include a contract-valid cycle_inflation package before Linux parity starts.",
        ],
    }
    json_write(output_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

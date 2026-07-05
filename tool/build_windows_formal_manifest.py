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
    artifact_record,
    default_archive_root,
    detect_repo_root,
    json_load,
    json_write,
)


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def _artifact(role: str, path: Path, *, required: bool = True) -> dict[str, Any]:
    record = artifact_record(role, path)
    record["required"] = bool(required)
    return record


def _case_roles(group_root: Path, case_id: str) -> list[dict[str, Any]]:
    return [
        _artifact(f"B.case_summary.{case_id}", group_root / f"case_summary_{case_id}.json"),
        _artifact(f"B.progress.{case_id}", group_root / f"progress_{case_id}.jsonl"),
        _artifact(f"B.package.{case_id}.proof_digest", group_root / "packages" / case_id / "control" / "proof_digest.json"),
        _artifact(f"B.package.{case_id}.frontier_snapshot", group_root / "packages" / case_id / "control" / "frontier_snapshot.json"),
        _artifact(f"B.package.{case_id}.result_validity", group_root / "packages" / case_id / "result" / "result_validity.json"),
    ]


def _scenario_roles(group_root: Path, scenario_id: str) -> list[dict[str, Any]]:
    return [
        _artifact(f"C.case_summary.{scenario_id}", group_root / f"case_summary_{scenario_id}.json"),
        _artifact(f"C.progress.{scenario_id}", group_root / f"progress_{scenario_id}.jsonl"),
        _artifact(f"C.proof_query.{scenario_id}", group_root / "raw" / f"{scenario_id}_proof_query_windows.json"),
        _artifact(f"C.repro.{scenario_id}", group_root / "raw" / f"{scenario_id}_repro_windows.json"),
        _artifact(f"C.package.{scenario_id}.proof_digest", group_root / "packages" / scenario_id / "control" / "proof_digest.json"),
        _artifact(f"C.package.{scenario_id}.frontier_snapshot", group_root / "packages" / scenario_id / "control" / "frontier_snapshot.json"),
        _artifact(f"C.package.{scenario_id}.blocker_artifact", group_root / "packages" / scenario_id / "control" / "blocker_artifact.json"),
        _artifact(f"C.package.{scenario_id}.result_validity", group_root / "packages" / scenario_id / "result" / "result_validity.json"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_windows_formal_manifest")
    repo_root = detect_repo_root()
    parser.add_argument("--archive-root", type=Path, default=default_archive_root(repo_root))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    archive_root = args.archive_root.expanduser().resolve()
    output_path = (args.output or (archive_root / "windows_artifact_manifest.json")).expanduser().resolve()

    prep_root = archive_root / "preparation"
    a_root = archive_root / "A_control_plane_first"
    b_root = archive_root / "B_budget_pre_freeze"
    c_root = archive_root / "C_degraded_audit"

    input_report = {}
    input_report_path = prep_root / "input_qualification_report_windows.json"
    if input_report_path.exists():
        input_report = dict(json_load(input_report_path))

    artifacts = [
        _artifact("preparation.environment_summary", prep_root / "windows_environment_summary.json"),
        _artifact("preparation.input_qualification_report", input_report_path),
        _artifact("preparation.desktop_preflight", prep_root / "desktop_preflight_windows_formal_10_4.json", required=False),
        _artifact("preparation.desktop_env_check", prep_root / "desktop_env_windows_formal_10_4.json", required=False),
        _artifact("A.formal_summary_windows", a_root / "formal_summary_windows.json"),
        _artifact("A.seed_selection_windows", a_root / "seed_selection_windows.json"),
        _artifact("A.sidecar_build_windows", a_root / "sidecar_build_windows.json"),
        _artifact("A.baseline_clipped_windows", a_root / "baseline_clipped_windows.json"),
        _artifact("A.baseline_legacy_windows", a_root / "baseline_legacy_windows.json", required=False),
        _artifact("A.repro_run_001_windows", a_root / "repro_run_001_windows.json"),
        _artifact("A.proof_query_run_001_windows", a_root / "proof_query_run_001_windows.json"),
        _artifact("A.sidecar_manifest", a_root / "sidecar" / "control" / "sidecar_manifest.json"),
        _artifact("A.sidecar_payload", a_root / "sidecar" / "control" / "dependency_sidecar.jsonl"),
        _artifact("A.run_001.proof_digest", a_root / "packages" / "run_001" / "control" / "proof_digest.json"),
        _artifact("A.run_001.frontier_snapshot", a_root / "packages" / "run_001" / "control" / "frontier_snapshot.json"),
        _artifact("A.run_001.result_validity", a_root / "packages" / "run_001" / "result" / "result_validity.json"),
        _artifact("A.run_002.proof_digest", a_root / "packages" / "run_002" / "control" / "proof_digest.json"),
        _artifact("A.run_002.frontier_snapshot", a_root / "packages" / "run_002" / "control" / "frontier_snapshot.json"),
        _artifact("A.run_002.result_validity", a_root / "packages" / "run_002" / "result" / "result_validity.json"),
        _artifact("A.run_003.proof_digest", a_root / "packages" / "run_003" / "control" / "proof_digest.json"),
        _artifact("A.run_003.frontier_snapshot", a_root / "packages" / "run_003" / "control" / "frontier_snapshot.json"),
        _artifact("A.run_003.result_validity", a_root / "packages" / "run_003" / "result" / "result_validity.json"),
        _artifact("B.formal_summary_windows", b_root / "formal_summary_windows.json"),
        _artifact("B.seed_profiles_windows", b_root / "seed_profiles_windows.json"),
        _artifact("B.budget_sweep_windows", b_root / "budget_sweep_windows.json"),
        _artifact("B.pareto_table_windows", b_root / "pareto_table_windows.json"),
        _artifact("B.scenario_matrix_windows", b_root / "scenario_matrix_windows.json"),
        _artifact("C.formal_summary_windows", c_root / "formal_summary_windows.json"),
        _artifact("C.scenario_matrix_windows", c_root / "scenario_matrix_windows.json"),
    ]
    for case_id in EXPECTED_B_CASE_IDS:
        artifacts.extend(_case_roles(b_root, case_id))
    for scenario_id in EXPECTED_C_SCENARIO_IDS:
        artifacts.extend(_scenario_roles(c_root, scenario_id))

    missing_count = sum(1 for row in artifacts if bool(row.get("required", True)) and not row["exists"])
    optional_missing_count = sum(1 for row in artifacts if not bool(row.get("required", True)) and not row["exists"])
    payload = {
        "contract_version": str(input_report.get("contract_version") or "patent_10_4_formal_close_min_contract_20260415"),
        "platform": "windows",
        "run_scope": "patent_10_4_formal",
        "generated_at": _iso_now(),
        "formal_root": str(archive_root),
        "environment_summary": str(prep_root / "windows_environment_summary.json"),
        "summary_paths": {
            "A_control_plane_first": str(a_root / "formal_summary_windows.json"),
            "B_budget_pre_freeze": str(b_root / "formal_summary_windows.json"),
            "C_degraded_audit": str(c_root / "formal_summary_windows.json"),
        },
        "trace_sha256": input_report.get("trace_sha256"),
        "trace_size_bytes": input_report.get("trace_size_bytes"),
        "groups": {
            "A_control_plane_first": str(a_root / "formal_summary_windows.json"),
            "B_budget_pre_freeze": str(b_root / "formal_summary_windows.json"),
            "C_degraded_audit": str(c_root / "formal_summary_windows.json"),
        },
        "artifact_count": len(artifacts),
        "missing_count": missing_count,
        "optional_missing_count": optional_missing_count,
        "artifacts": artifacts,
    }
    json_write(output_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

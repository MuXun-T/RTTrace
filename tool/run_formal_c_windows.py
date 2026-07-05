from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.services import ExportService
from parser.evidence_models import evd_RecomputeProofHash
from tool.formal_windows_common import (
    EXPECTED_C_SCENARIO_IDS,
    add_common_arguments,
    build_config,
    checked_in_runner,
    copy_file,
    ensure_preparation_aliases,
    json_load,
    json_write,
    jsonl_write,
    load_group_proof_digest,
    postprocess_summary,
    run_patched_main,
    runner_command,
    scenario_advisor_config,
    scenario_contract_artifact_record,
    scenario_experiment_params,
    scenario_run_id,
    scenario_streaming_write_enabled,
    scenario_ticket_fast_path_enabled,
)


def _ensure_a_prereqs(a_root: Path) -> None:
    required = (
        a_root / "seed_selection_linux.json",
        a_root / "packages" / "run_001",
    )
    for path in required:
        if not path.exists():
            raise FileNotFoundError(f"A-group prerequisite missing: {path}")


def _copy_outputs(group_root: Path) -> list[str]:
    copy_file(group_root / "scenario_matrix_linux.json", group_root / "scenario_matrix_windows.json")
    rows = list((json_load(group_root / "scenario_matrix_linux.json") or {}).get("rows") or [])
    scenario_ids: list[str] = []
    for row in rows:
        scenario_id = str((row or {}).get("scenario_id") or "")
        if not scenario_id:
            continue
        scenario_ids.append(scenario_id)
        copy_file(
            group_root / "raw" / f"{scenario_id}_measured_linux.json",
            group_root / f"case_summary_{scenario_id}.json",
        )
        progress_rows = list(json_load(group_root / "raw" / f"{scenario_id}_progress_linux.json") or [])
        jsonl_write(group_root / f"progress_{scenario_id}.jsonl", progress_rows)
        copy_file(
            group_root / "raw" / f"{scenario_id}_proof_query_linux.json",
            group_root / "raw" / f"{scenario_id}_proof_query_windows.json",
        )
        copy_file(
            group_root / "raw" / f"{scenario_id}_repro_linux.json",
            group_root / "raw" / f"{scenario_id}_repro_windows.json",
        )
    return scenario_ids


def _write_group_proof_digest(
    group_root: Path,
    *,
    sidecar_bytes_scanned: int,
) -> Path:
    scenario_matrix = dict(json_load(group_root / "scenario_matrix_windows.json"))
    rows = list(scenario_matrix.get("rows") or [])
    reference = next((dict(row) for row in rows if isinstance(row, dict) and str(row.get("scenario_id") or "") == "cycle_inflation"), {})
    if not reference:
        reference = next((dict(row) for row in rows if isinstance(row, dict)), {})
    proof_digest = load_group_proof_digest(
        reference,
        group_root=group_root,
        error_label="C-group scenario matrix",
    )
    proof_digest["sidecar_bytes_scanned"] = int(sidecar_bytes_scanned)
    proof_digest["proof_hash"] = evd_RecomputeProofHash(proof_digest)
    proof_digest_path = group_root / "control" / "proof_digest.json"
    proof_digest_path.parent.mkdir(parents=True, exist_ok=True)
    proof_digest_path.write_text(
        json.dumps(proof_digest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return proof_digest_path


def _mutate_summary(
    payload: dict[str, Any],
    *,
    group_root: Path,
    prep_root: Path,
    repo_root: Path,
    trace_path: Path,
    command_text: str,
    config: Any,
) -> None:
    input_contract = dict(payload.get("input_contract") or {})
    input_contract["trace_path"] = str(trace_path)
    input_contract["input_qualification_report"] = str(prep_root / "input_qualification_report_windows.json")
    payload["platform"] = "windows"
    payload["input_contract"] = input_contract
    payload["artifact_refs"] = {
        "scenario_matrix": str(group_root / "scenario_matrix_windows.json"),
        "package_root": str(group_root / "packages"),
        "sidecar_root": str(group_root / "sidecars"),
        "raw_root": str(group_root / "raw"),
        "environment_summary": str(prep_root / "windows_environment_summary.json"),
        "command": command_text,
        "runner_script": str(repo_root / "tool" / "run_formal_c_windows.py"),
    }
    scenario_contract_record = scenario_contract_artifact_record(config)
    if scenario_contract_record is not None:
        payload["artifact_refs"]["scenario_execution_contract"] = scenario_contract_record["path"]
    payload["scenario_execution"] = {
        "scenario_id": scenario_run_id(config, default="formal_c"),
        "ticket_fast_path_enabled": scenario_ticket_fast_path_enabled(config),
        "streaming_write_enabled": scenario_streaming_write_enabled(config),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_formal_c_windows")
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    try:
        config = build_config(args)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    ensure_preparation_aliases(config)
    _ensure_a_prereqs(config.a_root)

    runner_path = checked_in_runner("C", config.repo_root)
    stdout_path = config.c_root / "run_formal_c_windows.stdout"
    stderr_path = config.c_root / "run_formal_c_windows.stderr"
    time_path = config.c_root / "run_formal_c_windows.time"
    command_text = runner_command("run_formal_c_windows.py", config)

    def patcher(module: Any) -> None:
        module.ROOT = config.repo_root
        module.PLATFORM = "windows"
        module.ARCHIVE_ROOT = config.archive_root
        module.A_ROOT = config.a_root
        module.C_ROOT = config.c_root
        module.PREP_ROOT = config.prep_root
        module.TRACE_PATH = config.trace_path
        module.A_RUN_001_PACKAGE = config.a_root / "packages" / "run_001"
        original_export_evidence = ExportService.export_Evidence
        advisor_enabled, advisor_mode, advisor_config = scenario_advisor_config(config)

        def patched_export_evidence(service: ExportService, request: dict[str, Any]) -> Any:
            payload = dict(request or {})
            payload["advisor_enabled"] = advisor_enabled
            payload["advisor_mode"] = advisor_mode
            payload["advisor_config"] = dict(advisor_config)
            payload["run_id"] = scenario_run_id(config, default=str(payload.get("scenario_id") or "formal-c"))
            payload["experiment_params"] = {
                **scenario_experiment_params(config, proof_group="C_degraded_audit"),
                **dict(payload.get("experiment_params") or {}),
            }
            if advisor_enabled:
                payload["telemetry_history_path"] = str((config.archive_root / "telemetry_history.jsonl").resolve())
            return original_export_evidence(service, payload)

        ExportService.export_Evidence = patched_export_evidence

    run_patched_main(
        module_path=runner_path,
        module_name="formal_c_windows_runner",
        patcher=patcher,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        time_path=time_path,
        repo_root=config.repo_root,
    )

    scenario_ids = _copy_outputs(config.c_root)
    missing = sorted(set(EXPECTED_C_SCENARIO_IDS).difference(scenario_ids))
    if missing:
        raise RuntimeError(f"C-group scenario matrix incomplete after runner execution: {missing}")
    sidecar_bytes_scanned = 0 if scenario_ticket_fast_path_enabled(config) else 4096
    _write_group_proof_digest(
        config.c_root,
        sidecar_bytes_scanned=sidecar_bytes_scanned,
    )

    postprocess_summary(
        config.c_root / "formal_summary_linux.json",
        lambda payload: _mutate_summary(
            payload,
            group_root=config.c_root,
            prep_root=config.prep_root,
            repo_root=config.repo_root,
            trace_path=config.trace_path,
            command_text=command_text,
            config=config,
        ),
        config.c_root / "formal_summary_windows.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

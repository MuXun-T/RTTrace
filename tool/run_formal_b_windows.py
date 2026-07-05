from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.evidence_export import write_evidence_package
from desktop.services import WorkspaceController
from parser.pipeline import load_dataset_with_timings
from parser.evidence_models import evd_RecomputeProofHash
from parser.runtime_advisor import coefficient_payload_checksum
from tool.formal_windows_common import (
    EXPECTED_B_CASE_IDS,
    add_common_arguments,
    artifact_record,
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
        a_root / "sidecar" / "control" / "dependency_sidecar.jsonl",
        a_root / "sidecar" / "control" / "sidecar_manifest.json",
    )
    for path in required:
        if not path.exists():
            raise FileNotFoundError(f"A-group prerequisite missing: {path}")


def _copy_outputs(group_root: Path) -> list[str]:
    copy_file(group_root / "seed_profiles_linux.json", group_root / "seed_profiles_windows.json")
    copy_file(group_root / "budget_sweep_linux.json", group_root / "budget_sweep_windows.json")
    copy_file(group_root / "budget_sweep_linux.json", group_root / "scenario_matrix_windows.json")
    copy_file(group_root / "pareto_table_linux.json", group_root / "pareto_table_windows.json")

    rows = list((json_load(group_root / "budget_sweep_linux.json") or {}).get("rows") or [])
    case_ids: list[str] = []
    for row in rows:
        scenario_id = str((row or {}).get("scenario_id") or "")
        if not scenario_id:
            continue
        case_ids.append(scenario_id)
        json_write(group_root / f"case_summary_{scenario_id}.json", row)
        progress_rows = list(json_load(group_root / "raw" / f"{scenario_id}_progress.json") or [])
        jsonl_write(group_root / f"progress_{scenario_id}.jsonl", progress_rows)
    return case_ids


def _write_group_proof_digest(
    group_root: Path,
    *,
    sidecar_bytes_scanned: int,
) -> Path:
    budget_sweep = dict(json_load(group_root / "budget_sweep_windows.json"))
    rows = list(budget_sweep.get("rows") or [])
    reference = next((dict(row) for row in rows if isinstance(row, dict) and str(row.get("scenario_id") or "") == "depth_d0"), {})
    if not reference:
        reference = next((dict(row) for row in rows if isinstance(row, dict)), {})
    proof_digest = load_group_proof_digest(
        reference,
        group_root=group_root,
        error_label="B-group budget sweep",
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
        "seed_profiles": str(group_root / "seed_profiles_windows.json"),
        "budget_sweep": str(group_root / "budget_sweep_windows.json"),
        "pareto_table": str(group_root / "pareto_table_windows.json"),
        "package_root": str(group_root / "packages"),
        "sidecar_root": str(group_root / "sidecars"),
        "raw_root": str(group_root / "raw"),
        "environment_summary": str(prep_root / "windows_environment_summary.json"),
        "command": command_text,
        "runner_script": str(repo_root / "tool" / "run_formal_b_windows.py"),
    }
    scenario_contract_record = scenario_contract_artifact_record(config)
    if scenario_contract_record is not None:
        payload["artifact_refs"]["scenario_execution_contract"] = scenario_contract_record["path"]
    payload["scenario_execution"] = {
        "scenario_id": scenario_run_id(config, default="formal_b"),
        "ticket_fast_path_enabled": scenario_ticket_fast_path_enabled(config),
        "streaming_write_enabled": scenario_streaming_write_enabled(config),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_formal_b_windows")
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    try:
        config = build_config(args)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    ensure_preparation_aliases(config)
    _ensure_a_prereqs(config.a_root)

    runner_path = checked_in_runner("B", config.repo_root)
    stdout_path = config.b_root / "run_formal_b_windows.stdout"
    stderr_path = config.b_root / "run_formal_b_windows.stderr"
    time_path = config.b_root / "run_formal_b_windows.time"
    command_text = runner_command("run_formal_b_windows.py", config)

    def patcher(module: Any) -> None:
        module.ROOT = config.repo_root
        module.PLATFORM = "windows"
        module.ARCHIVE_ROOT = config.archive_root
        module.B_ROOT = config.b_root
        module.PREP_ROOT = config.prep_root
        module.A_ROOT = config.a_root
        module.TRACE_PATH = config.trace_path
        module.SIDEcar_TEMPLATE_ROOT = config.a_root / "sidecar"
        module.SIDEcar_TEMPLATE_MANIFEST = config.a_root / "sidecar" / "control" / "sidecar_manifest.json"
        original_write_evidence_package = write_evidence_package
        advisor_enabled, advisor_mode, advisor_config = scenario_advisor_config(config)

        def _load_controller(trace_path: Path) -> tuple[Any, str, Any, list[Any], list[Any], dict[str, int], int]:
            controller = WorkspaceController()
            loaded = load_dataset_with_timings(
                trace_path,
                materialize_event_stream=True,
                index_build_mode="full",
            )
            if not loaded.ok:
                raise RuntimeError(loaded.message)
            registered = controller._register_artifact(loaded.data["artifact"])
            if not registered.ok:
                raise RuntimeError(registered.message)
            dataset_id = registered.data
            record = controller.repository.get(dataset_id)
            ordered_events = list(record.artifact.bundle.event_stream)
            eligible_events = [event for event in ordered_events if str(event.event_name) in module.ALLOWED_EVENT_NAMES]
            ref_index = {str(event.ref_key): index for index, event in enumerate(ordered_events)}
            avg_event_size = module._template_avg_event_size()
            return controller, dataset_id, record, ordered_events, eligible_events, ref_index, avg_event_size

        module._load_controller = _load_controller

        def patched_write_evidence_package(record: Any, snapshot_context: dict[str, Any], job: dict[str, Any], **kwargs: Any) -> Any:
            payload = dict(job or {})
            payload["advisor_enabled"] = advisor_enabled
            payload["advisor_mode"] = advisor_mode
            payload["advisor_config"] = dict(advisor_config)
            payload["run_id"] = scenario_run_id(config, default=str(payload.get("run_id") or "formal-b"))
            payload["experiment_params"] = {
                **scenario_experiment_params(config, proof_group="B_budget_pre_freeze"),
                **dict(payload.get("experiment_params") or {}),
            }
            if advisor_enabled:
                payload["telemetry_history_path"] = str((config.archive_root / "telemetry_history.jsonl").resolve())
            return original_write_evidence_package(record, snapshot_context, payload, **kwargs)

        module.write_evidence_package = patched_write_evidence_package

    run_patched_main(
        module_path=runner_path,
        module_name="formal_b_windows_runner",
        patcher=patcher,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        time_path=time_path,
        repo_root=config.repo_root,
    )

    case_ids = _copy_outputs(config.b_root)
    missing = sorted(set(EXPECTED_B_CASE_IDS).difference(case_ids))
    if missing:
        raise RuntimeError(f"B-group case matrix incomplete after runner execution: {missing}")
    sidecar_bytes_scanned = 0 if scenario_ticket_fast_path_enabled(config) else 4096
    _write_group_proof_digest(
        config.b_root,
        sidecar_bytes_scanned=sidecar_bytes_scanned,
    )

    postprocess_summary(
        config.b_root / "formal_summary_linux.json",
        lambda payload: _mutate_summary(
            payload,
            group_root=config.b_root,
            prep_root=config.prep_root,
            repo_root=config.repo_root,
            trace_path=config.trace_path,
            command_text=command_text,
            config=config,
        ),
        config.b_root / "formal_summary_windows.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

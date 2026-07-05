from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.services import ExportService, ReproService, WorkspaceController
from parser.evidence_sidecar import dependency_sidecar_file_fingerprint
from parser.evidence_sidecar_index import build_or_open_sidecar_index
from parser.parser_process_agent import ParserProcessAgent
from tool.formal_windows_common import (
    add_common_arguments,
    build_config,
    checked_in_runner,
    copy_file,
    copy_if_exists,
    ensure_preparation_aliases,
    json_load,
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


RUNNER_NOTE = "sync ReproService proof query is used by the Windows wrapper to avoid background-job dependency during formal packaging."


def _patched_repro_queries(controller: Any, package_dir: Path) -> dict[str, Any]:
    repro = ReproService(controller.repository, controller.context_store)
    repro_open = repro.repro_OpenPackage(str(package_dir))
    if not repro_open.ok:
        raise RuntimeError(repro_open.message)
    restored = repro.repro_RestoreContext(None)
    if not restored.ok:
        raise RuntimeError(restored.message)
    loaded_dataset = repro.repro_LoadAsDataset("single")
    if not loaded_dataset.ok:
        raise RuntimeError(loaded_dataset.message)
    proof_query = repro.repro_QueryProof()
    if not proof_query.ok:
        raise RuntimeError(proof_query.message)
    return {
        "repro_open": {
            "context": restored.data.persisted_dict(),
            "dataset_id": loaded_dataset.data,
            "consumer_mode": repro_open.data.get("consumer_mode"),
            "proof_digest": repro_open.data.get("proof_digest"),
        },
        "proof_query": proof_query.data,
    }


def _replay_parser_progress(progress_path: Path, stage_observer: Any) -> None:
    if stage_observer is None or not progress_path.exists():
        return
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if not isinstance(payload, dict):
            continue
        stage = str(payload.get("stage") or "")
        stage_payload = payload.get("payload")
        if stage and isinstance(stage_payload, dict):
            stage_observer(stage, dict(stage_payload))


def _copy_outputs(group_root: Path) -> dict[str, Path | None]:
    outputs: dict[str, Path | None] = {}
    outputs["seed_selection"] = copy_file(
        group_root / "seed_selection_linux.json",
        group_root / "seed_selection_windows.json",
    )
    outputs["sidecar_build"] = copy_file(
        group_root / "sidecar_build_linux.json",
        group_root / "sidecar_build_windows.json",
    )
    outputs["baseline_clipped"] = copy_file(
        group_root / "baseline_clipped_linux.json",
        group_root / "baseline_clipped_windows.json",
    )
    outputs["baseline_legacy"] = copy_if_exists(
        group_root / "baseline_legacy_linux.json",
        group_root / "baseline_legacy_windows.json",
    )
    outputs["repro"] = copy_file(
        group_root / "repro_run_001_linux.json",
        group_root / "repro_run_001_windows.json",
    )
    outputs["proof_query"] = copy_file(
        group_root / "proof_query_run_001_linux.json",
        group_root / "proof_query_run_001_windows.json",
    )
    return outputs


def _prebuild_sidecar_ticket_if_needed(
    *,
    config: Any,
    sidecar_dir: Path,
) -> None:
    if not scenario_ticket_fast_path_enabled(config):
        return
    sidecar_path = sidecar_dir / "control" / "dependency_sidecar.jsonl"
    manifest_path = sidecar_dir / "control" / "sidecar_manifest.json"
    if not sidecar_path.exists() or not manifest_path.exists():
        return
    manifest = dict(json_load(manifest_path))
    checksum = str((manifest.get("entry_checksums") or {}).get("control/dependency_sidecar.jsonl") or "")
    if not checksum:
        return
    prebuilt = build_or_open_sidecar_index(
        sidecar_path,
        expected_snapshot_id=str(manifest.get("snapshot_id") or ""),
        expected_trace_checksum=str(manifest.get("trace_checksum") or ""),
        dictionary_checksum=str(manifest.get("dictionary_checksum") or ""),
        sidecar_checksum=checksum,
        file_fingerprint=dependency_sidecar_file_fingerprint(sidecar_path),
        rebuild_on_mismatch=True,
    )
    if not prebuilt.ok:
        raise RuntimeError(prebuilt.message)


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
    proof_digests = list((payload.get("required_metrics") or {}).get("proof_digest") or [])
    proof_hashes = [str((row or {}).get("proof_hash") or "") for row in proof_digests]
    proof_hashes_nonempty = [item for item in proof_hashes if item]
    proof_query = dict(json_load(group_root / "proof_query_run_001_windows.json"))
    comparison = dict(payload.get("comparison") or {})
    baseline_kind = str(comparison.get("baseline_path_kind") or "clipped")
    baseline_report_path = (
        group_root / "baseline_legacy_windows.json"
        if baseline_kind == "legacy"
        else group_root / "baseline_clipped_windows.json"
    )
    runtime_seconds = list((payload.get("observations") or {}).get("runtime_seconds") or [])
    closure_modes = list((payload.get("observations") or {}).get("closure_modes") or [])
    proof_consumer_modes = list((payload.get("observations") or {}).get("proof_consumer_modes") or [])
    catalog_chunk_count = int((payload.get("observations") or {}).get("catalog_chunk_count") or 0)
    full_global_rescan = bool(
        (payload.get("observations") or {}).get("full_global_rescan_fallback_inferred")
    )
    minimal_legal_all = bool(
        (payload.get("observations") or {}).get("minimal_legal_package_valid_all")
    )

    input_contract = dict(payload.get("input_contract") or {})
    input_contract["trace_path"] = str(trace_path)
    input_contract["input_qualification_report"] = str(prep_root / "input_qualification_report_windows.json")
    input_contract.pop("execution_trace_path", None)

    payload["platform"] = "windows"
    payload["input_contract"] = input_contract
    payload.pop("runtime_seconds_total", None)
    payload["artifact_refs"] = {
        "baseline_path_kind": baseline_kind,
        "baseline_report_path": str(baseline_report_path),
        "command": command_text,
        "environment_summary": str(prep_root / "windows_environment_summary.json"),
        "package_paths": [str(group_root / "packages" / f"run_{index:03d}") for index in range(1, 4)],
        "progress_reports": [str(group_root / "raw" / f"run_{index:03d}_progress.json") for index in range(1, 4)],
        "proof_query_report_path": str(group_root / "proof_query_run_001_windows.json"),
        "repro_open_report_path": str(group_root / "repro_run_001_windows.json"),
        "run_reports": [str(group_root / "raw" / f"run_{index:03d}_measured.json") for index in range(1, 4)],
        "runner_script": str(repo_root / "tool" / "run_formal_a_windows.py"),
        "runner_stderr": str(group_root / "run_formal_a_windows.stderr"),
        "runner_time_report": str(group_root / "run_formal_a_windows.time"),
        "seed_selection": str(group_root / "seed_selection_windows.json"),
        "sidecar_build_report": str(group_root / "sidecar_build_windows.json"),
        "sidecar_manifest_path": str(group_root / "sidecar" / "control" / "sidecar_manifest.json"),
        "sidecar_path": str(group_root / "sidecar" / "control" / "dependency_sidecar.jsonl"),
        "tail_recovery_note": RUNNER_NOTE,
    }
    scenario_contract_record = scenario_contract_artifact_record(config)
    if scenario_contract_record is not None:
        payload["artifact_refs"]["scenario_execution_contract"] = scenario_contract_record["path"]
    payload["scenario_execution"] = {
        "scenario_id": scenario_run_id(config, default="formal_a"),
        "ticket_fast_path_enabled": scenario_ticket_fast_path_enabled(config),
        "streaming_write_enabled": scenario_streaming_write_enabled(config),
    }
    payload["observations"] = {
        "catalog_chunk_count": catalog_chunk_count,
        "closure_modes": closure_modes,
        "full_global_rescan_fallback_inferred": full_global_rescan,
        "minimal_legal_package_valid_all": minimal_legal_all,
        "no_global_rescan_inference_basis": "evidence scan_count < total catalog chunk count",
        "proof_consumer_modes": proof_consumer_modes,
        "proof_hash_consistent": len(set(proof_hashes_nonempty)) <= 1,
        "proof_hash_matches": bool(proof_query.get("proof_verification", {}).get("proof_hash_matches")),
        "proof_hashes": proof_hashes,
        "proof_verification_ok": bool(proof_query.get("proof_verification", {}).get("ok")),
        "runner_tail_failure": RUNNER_NOTE,
        "runtime_seconds": runtime_seconds,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_formal_a_windows")
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    try:
        config = build_config(args)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    ensure_preparation_aliases(config)

    runner_path = checked_in_runner("A", config.repo_root)
    stdout_path = config.a_root / "run_formal_a_windows.stdout"
    stderr_path = config.a_root / "run_formal_a_windows.stderr"
    time_path = config.a_root / "run_formal_a_windows.time"
    command_text = runner_command("run_formal_a_windows.py", config)

    def patcher(module: Any) -> None:
        module.ROOT = config.repo_root
        module.PLATFORM = "windows"
        module.ARCHIVE_ROOT = config.archive_root
        module.A_ROOT = config.a_root
        module.PREP_ROOT = config.prep_root
        module.TRACE_PATH = config.trace_path
        module.FROZEN_TRACE_PATH = config.trace_path
        module._run_repro_queries = _patched_repro_queries
        original_export_evidence = ExportService.export_Evidence
        advisor_enabled, advisor_mode, advisor_config = scenario_advisor_config(config)

        def _load_full_controller(trace_path: Path) -> tuple[Any, str, Any, dict[str, Any]]:
            load_started = time.perf_counter()
            controller = WorkspaceController()
            loaded = module.load_dataset_with_timings(
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
            bundle = controller.repository.get(dataset_id).artifact.bundle
            load_seconds = round(time.perf_counter() - load_started, 6)
            meta = {
                "dataset_id": dataset_id,
                "event_count": len(bundle.event_stream),
                "task_state_count": len(bundle.task_states),
                "exec_slice_count": len(bundle.exec_slices),
                "load_seconds": load_seconds,
                "time_window": [
                    float(bundle.event_stream[0].timestamp_aligned),
                    float(bundle.event_stream[-1].timestamp_aligned),
                ],
            }
            return controller, dataset_id, bundle, meta

        module._load_full_controller = _load_full_controller

        def patched_export_evidence(service: ExportService, request: dict[str, Any]) -> Any:
            payload = dict(request or {})
            payload["advisor_enabled"] = advisor_enabled
            payload["advisor_mode"] = advisor_mode
            payload["advisor_config"] = dict(advisor_config)
            payload["run_id"] = scenario_run_id(config, default="formal-a")
            payload["experiment_params"] = scenario_experiment_params(config, proof_group="A_control_plane_first")
            if advisor_enabled:
                payload["telemetry_history_path"] = str((config.archive_root / "telemetry_history.jsonl").resolve())
            return original_export_evidence(service, payload)

        ExportService.export_Evidence = patched_export_evidence

        def isolated_load_dataset_with_timings(
            source: Any,
            dictionary: Any = None,
            stage_observer: Any = None,
            materialize_event_stream: bool = True,
            index_build_mode: str = "full",
        ) -> Any:
            if isinstance(dictionary, dict):
                raise RuntimeError("isolated formal parser wrapper requires dictionary path, not dictionary payload")
            artifact_dir = config.a_root / "parser_process" / "formal_a_windows_load"
            progress_path = artifact_dir / "progress.jsonl"
            parsed = ParserProcessAgent(job_id="formal-a-windows-load").parse_rebuild(
                source,
                artifact_policy={
                    "artifact_dir": str(artifact_dir),
                    "dictionary_path": str(dictionary) if dictionary is not None else None,
                    "materialize_event_stream": bool(materialize_event_stream),
                    "index_build_mode": str(index_build_mode or "full"),
                    "load_artifact": True,
                    "retain_artifact": False,
                    "progress_path": str(progress_path),
                },
            )
            _replay_parser_progress(progress_path, stage_observer)
            return parsed

        module.load_dataset_with_timings = isolated_load_dataset_with_timings

    run_patched_main(
        module_path=runner_path,
        module_name="formal_a_windows_runner",
        patcher=patcher,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        time_path=time_path,
        repo_root=config.repo_root,
    )

    _prebuild_sidecar_ticket_if_needed(config=config, sidecar_dir=config.a_root / "sidecar")
    _copy_outputs(config.a_root)
    postprocess_summary(
        config.a_root / "formal_summary_linux.json",
        lambda payload: _mutate_summary(
            payload,
            group_root=config.a_root,
            prep_root=config.prep_root,
            repo_root=config.repo_root,
            trace_path=config.trace_path,
            command_text=command_text,
            config=config,
        ),
        config.a_root / "formal_summary_windows.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

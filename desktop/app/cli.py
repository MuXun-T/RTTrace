from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from collector.channel import SocketTraceMockServer
from parser.models import EventTableQuery, TaskStateQuery, dataclass_to_dict

from ..sample_data import write_scenario
from ..services import CompareService, ExportService, ReplayService, ReproService, WorkspaceController


def build_controller() -> WorkspaceController:
    return WorkspaceController()


def _print(data) -> None:
    print(json.dumps(dataclass_to_dict(data), indent=2, ensure_ascii=False))


def _print_dataset(controller: WorkspaceController, dataset_id: str) -> None:
    dataset = controller.repository.get(dataset_id).artifact.bundle
    _print(
        {
            "dataset_id": dataset_id,
            "event_count": len(dataset.event_stream),
            "task_state_count": len(dataset.task_states),
            "exec_slice_count": len(dataset.exec_slices),
            "untrusted_windows": [dataclass_to_dict(item) for item in dataset.untrusted_windows],
        }
    )


def _parse_json_object(raw: str | None, *, arg_name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if raw is None:
        return dict(default or {})
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid {arg_name}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"invalid {arg_name}: expected JSON object")
    return dict(parsed)


def _parse_time_window_json(raw: str, *, arg_name: str) -> tuple[float, float]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid {arg_name}: {exc}") from exc
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise SystemExit(f"invalid {arg_name}: expected JSON array with two numbers")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in parsed):
        raise SystemExit(f"invalid {arg_name}: expected JSON array with two numbers")
    return (float(parsed[0]), float(parsed[1]))


def _wait_for_job_result(controller: WorkspaceController, job_id: str, *, timeout_s: float = 30.0):
    deadline = time.time() + timeout_s
    last_status = "created"
    while time.time() < deadline:
        snapshot = controller.jobs.status(job_id)
        if not snapshot.ok:
            raise SystemExit(snapshot.message)
        last_status = str(snapshot.data["status"])
        if last_status in {"succeeded", "failed"}:
            return controller.jobs.result(job_id)
        time.sleep(0.01)
    raise SystemExit(f"background job timed out: {job_id} status={last_status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trace-mvp")
    sub = parser.add_subparsers(dest="command", required=True)

    simulate = sub.add_parser("simulate")
    simulate.add_argument("--output", required=True)
    simulate.add_argument("--scenario", default="basic", choices=["basic", "gap", "multi_core"])
    simulate.add_argument("--candidate-variant", action="store_true")

    parse_cmd = sub.add_parser("parse")
    parse_cmd.add_argument("--input", required=True)

    online_file_cmd = sub.add_parser("online-file")
    online_file_cmd.add_argument("--input", required=True)
    online_file_cmd.add_argument("--read-size", type=int, default=128)
    online_file_cmd.add_argument("--dataset-id")

    online_socket_cmd = sub.add_parser("online-socket")
    online_socket_cmd.add_argument("--host", default="127.0.0.1")
    online_socket_cmd.add_argument("--port", type=int, required=True)
    online_socket_cmd.add_argument("--recv-size", type=int, default=128)
    online_socket_cmd.add_argument("--timeout-s", type=float, default=5.0)
    online_socket_cmd.add_argument("--dataset-id")

    online_serial_cmd = sub.add_parser("online-serial")
    online_serial_cmd.add_argument("--device", required=True)
    online_serial_cmd.add_argument("--read-size", type=int, default=128)
    online_serial_cmd.add_argument("--timeout-s", type=float, default=1.0)
    online_serial_cmd.add_argument("--baudrate", type=int, default=115200)
    online_serial_cmd.add_argument("--dataset-id")

    mock_socket_cmd = sub.add_parser("mock-socket")
    mock_socket_cmd.add_argument("--input", required=True)
    mock_socket_cmd.add_argument("--host", default="127.0.0.1")
    mock_socket_cmd.add_argument("--port", type=int, default=0)
    mock_socket_cmd.add_argument("--send-size", type=int, default=128)
    mock_socket_cmd.add_argument("--delay-ms", type=float, default=0.0)

    metric_cmd = sub.add_parser("metric")
    metric_cmd.add_argument("--input", required=True)
    metric_cmd.add_argument("--t-begin", type=float)
    metric_cmd.add_argument("--t-end", type=float)

    compare_cmd = sub.add_parser("compare")
    compare_cmd.add_argument("--baseline", required=True)
    compare_cmd.add_argument("--candidate", required=True)

    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("--input", required=True)
    export_cmd.add_argument("--output-dir", required=True)

    export_evidence_cmd = sub.add_parser("export-evidence")
    export_evidence_cmd.add_argument("--input", required=True)
    export_evidence_cmd.add_argument("--output-dir", required=True)
    export_evidence_cmd.add_argument("--job-timeout-s", type=float, default=30.0)
    export_evidence_cmd.add_argument("--seed-spec-json")
    export_evidence_cmd.add_argument("--rule-family", action="append")
    export_evidence_cmd.add_argument("--budget-vector-json")
    export_evidence_cmd.add_argument("--closure-policy-json")
    export_evidence_cmd.add_argument("--embodiment-mode", default="mode_a", choices=["mode_a", "mode_b"])
    export_evidence_cmd.add_argument("--sidecar-source")
    export_evidence_cmd.add_argument("--sidecar-manifest-source")
    export_evidence_cmd.add_argument("--time-window-json")
    export_evidence_cmd.add_argument("--advisor-enabled", action="store_true")
    export_evidence_cmd.add_argument(
        "--advisor-mode",
        choices=["disabled", "heuristic", "offline_coefficients", "openai_structured"],
        default="disabled",
    )
    export_evidence_cmd.add_argument("--openai-advisor-model")
    export_evidence_cmd.add_argument("--openai-advisor-timeout-s", type=float)
    export_evidence_cmd.add_argument("--openai-advisor-reasoning-effort")
    export_evidence_cmd.add_argument("--openai-advisor-max-history-rows", type=int)
    export_evidence_cmd.add_argument("--llm-advisor-provider", choices=["deepseek"])
    export_evidence_cmd.add_argument("--llm-advisor-backend", choices=["openai_responses", "openai_compatible_chat"])
    export_evidence_cmd.add_argument("--llm-advisor-base-url")
    export_evidence_cmd.add_argument("--llm-advisor-api-key-env")
    export_evidence_cmd.add_argument("--llm-advisor-model")
    export_evidence_cmd.add_argument("--llm-advisor-max-output-tokens", type=int)
    export_evidence_cmd.add_argument("--llm-advisor-max-retries", type=int)
    export_evidence_cmd.add_argument("--llm-advisor-chat-completions-path")

    sidecar_build_cmd = sub.add_parser("sidecar-build")
    sidecar_build_cmd.add_argument("--input", required=True)
    sidecar_build_cmd.add_argument("--output-dir", required=True)
    sidecar_build_cmd.add_argument("--rule-family", action="append")

    evidence_query_cmd = sub.add_parser("evidence-query")
    evidence_query_cmd.add_argument("--package", required=True)
    evidence_query_cmd.add_argument(
        "--kind",
        required=True,
        choices=["proof", "validity-alert", "validity-diag", "validity-object"],
    )
    evidence_query_cmd.add_argument("--alert-id")
    evidence_query_cmd.add_argument("--diag-id")
    evidence_query_cmd.add_argument("--object-kind")
    evidence_query_cmd.add_argument("--object-id")

    repro_cmd = sub.add_parser("repro")
    repro_cmd.add_argument("--package", required=True)

    shell_cmd = sub.add_parser("shell")
    shell_cmd.add_argument("--input")
    shell_cmd.add_argument("--offscreen", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "simulate":
        path = write_scenario(args.output, name=args.scenario, candidate_variant=args.candidate_variant)
        print(path)
        return 0

    if args.command == "mock-socket":
        delay_s = max(0.0, args.delay_ms) / 1000.0
        with SocketTraceMockServer(
            args.input,
            host=args.host,
            port=args.port,
            send_size=args.send_size,
            delay_s=delay_s,
        ) as server:
            _print({"host": server.host, "port": server.port, "source": str(Path(args.input))})
            server.wait()
        return 0

    controller = build_controller()
    controller.viz_InitWorkspace()

    if args.command == "parse":
        result = controller.viz_LoadDataset(args.input)
        if not result.ok:
            raise SystemExit(result.message)
        _print_dataset(controller, result.data)
        return 0

    if args.command == "online-file":
        result = controller.viz_LoadDatasetFromChannel(
            "file",
            {"path": args.input, "read_size": args.read_size, "dataset_id": args.dataset_id},
        )
        if not result.ok:
            raise SystemExit(result.message)
        _print_dataset(controller, result.data)
        return 0

    if args.command == "online-socket":
        result = controller.viz_LoadDatasetFromChannel(
            "socket",
            {
                "host": args.host,
                "port": args.port,
                "recv_size": args.recv_size,
                "timeout_s": args.timeout_s,
                "dataset_id": args.dataset_id,
            },
        )
        if not result.ok:
            raise SystemExit(result.message)
        _print_dataset(controller, result.data)
        return 0

    if args.command == "online-serial":
        result = controller.viz_LoadDatasetFromChannel(
            "serial",
            {
                "device": args.device,
                "read_size": args.read_size,
                "timeout_s": args.timeout_s,
                "baudrate": args.baudrate,
                "dataset_id": args.dataset_id,
            },
        )
        if not result.ok:
            raise SystemExit(result.message)
        _print_dataset(controller, result.data)
        return 0

    if args.command == "metric":
        result = controller.viz_LoadDataset(args.input)
        if not result.ok:
            raise SystemExit(result.message)
        bundle = controller.repository.get(result.data).artifact.bundle
        t_begin = args.t_begin if args.t_begin is not None else bundle.event_stream[0].timestamp_aligned
        t_end = args.t_end if args.t_end is not None else bundle.event_stream[-1].timestamp_aligned
        metrics = controller.viz_QueryMetricSeries({"time_window": (t_begin, t_end), "filter": {}})
        _print(metrics.data)
        return 0

    if args.command == "compare":
        baseline = controller.viz_LoadDataset(args.baseline)
        candidate = controller.viz_LoadDataset(args.candidate)
        compare = CompareService(controller.repository, controller.context_store)
        compare.cmp_LoadPair(baseline.data, candidate.data)
        scope = compare.cmp_SetScope({"baseline_id": baseline.data, "candidate_id": candidate.data, "filter": {}})
        _print(compare.cmp_QueryDiffSummary().data)
        return 0

    if args.command == "export":
        result = controller.viz_LoadDataset(args.input)
        if not result.ok:
            raise SystemExit(result.message)
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": result.data})
        written = export.export_WritePackage(job.data["job_id"], args.output_dir)
        _print(written.data)
        return 0

    if args.command == "export-evidence":
        result = controller.viz_LoadDataset(args.input)
        if not result.ok:
            raise SystemExit(result.message)
        payload: dict[str, Any] = {
            "dataset_id": result.data,
            "seed_spec": _parse_json_object(
                args.seed_spec_json,
                arg_name="--seed-spec-json",
                default={"source_kind": "analysis_context", "source_payload": {}},
            ),
            "embodiment_mode": str(args.embodiment_mode),
        }
        if args.rule_family:
            payload["rule_family"] = [str(item) for item in args.rule_family if str(item).strip()]
        if args.budget_vector_json is not None:
            payload["budget_vector"] = _parse_json_object(args.budget_vector_json, arg_name="--budget-vector-json")
        if args.closure_policy_json is not None:
            payload["closure_policy"] = _parse_json_object(args.closure_policy_json, arg_name="--closure-policy-json")
        if args.time_window_json is not None:
            payload["time_window"] = list(_parse_time_window_json(args.time_window_json, arg_name="--time-window-json"))
        if args.sidecar_source is not None:
            payload["sidecar_source"] = str(args.sidecar_source)
        if args.sidecar_manifest_source is not None:
            payload["sidecar_manifest_source"] = str(args.sidecar_manifest_source)
        if args.advisor_enabled:
            payload["advisor_enabled"] = True
            payload["advisor_mode"] = str(args.advisor_mode)
            advisor_config: dict[str, Any] = {"advisor_mode": str(args.advisor_mode)}
            if args.advisor_mode == "openai_structured":
                advisor_config["llm_enabled"] = True
            if args.openai_advisor_model is not None:
                advisor_config["openai_model"] = str(args.openai_advisor_model)
            if args.openai_advisor_timeout_s is not None:
                advisor_config["openai_timeout_s"] = float(args.openai_advisor_timeout_s)
            if args.openai_advisor_reasoning_effort is not None:
                advisor_config["openai_reasoning_effort"] = str(args.openai_advisor_reasoning_effort)
            if args.openai_advisor_max_history_rows is not None:
                advisor_config["openai_max_history_rows"] = int(args.openai_advisor_max_history_rows)
            if args.llm_advisor_provider is not None:
                advisor_config["llm_provider"] = str(args.llm_advisor_provider)
            if args.llm_advisor_backend is not None:
                advisor_config["llm_backend"] = str(args.llm_advisor_backend)
            if args.llm_advisor_base_url is not None:
                advisor_config["llm_base_url"] = str(args.llm_advisor_base_url)
            if args.llm_advisor_api_key_env is not None:
                advisor_config["llm_api_key_env"] = str(args.llm_advisor_api_key_env)
            if args.llm_advisor_model is not None:
                advisor_config["llm_model"] = str(args.llm_advisor_model)
            if args.llm_advisor_max_output_tokens is not None:
                advisor_config["llm_max_output_tokens"] = int(args.llm_advisor_max_output_tokens)
            if args.llm_advisor_max_retries is not None:
                advisor_config["llm_max_retries"] = int(args.llm_advisor_max_retries)
            if args.llm_advisor_chat_completions_path is not None:
                advisor_config["llm_chat_completions_path"] = str(args.llm_advisor_chat_completions_path)
            if len(advisor_config) > 1:
                payload["advisor_config"] = advisor_config
        if payload["embodiment_mode"] == "mode_b":
            if not payload.get("sidecar_source") or not payload.get("sidecar_manifest_source"):
                raise SystemExit("mode_b requires --sidecar-source and --sidecar-manifest-source")

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Evidence(payload)
        if not job.ok:
            raise SystemExit(job.message)
        submitted = controller.jobs.submit(
            job.data["job_id"],
            export.export_WriteEvidencePackage,
            job.data["job_id"],
            args.output_dir,
        )
        if not submitted.ok:
            raise SystemExit(submitted.message)
        written = _wait_for_job_result(controller, job.data["job_id"], timeout_s=float(args.job_timeout_s))
        if not written.ok:
            raise SystemExit(written.message)

        proof_digest_path = Path(str(written.data["package_path"])) / "control" / "proof_digest.json"
        proof_digest = json.loads(proof_digest_path.read_text(encoding="utf-8")) if proof_digest_path.exists() else {}
        _print(
            {
                "job_id": job.data["job_id"],
                "package_path": written.data["package_path"],
                "closure_mode": written.data.get("closure_mode"),
                "proof_digest": proof_digest,
            }
        )
        return 0

    if args.command == "sidecar-build":
        result = controller.viz_LoadDataset(args.input)
        if not result.ok:
            raise SystemExit(result.message)
        payload: dict[str, Any] = {"dataset_id": result.data}
        if args.rule_family:
            payload["rule_family"] = [str(item) for item in args.rule_family if str(item).strip()]
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.sidecar_Build(payload)
        if not job.ok:
            raise SystemExit(job.message)
        submitted = controller.jobs.submit(
            job.data["job_id"],
            export.sidecar_WriteArtifacts,
            job.data["job_id"],
            args.output_dir,
        )
        if not submitted.ok:
            raise SystemExit(submitted.message)
        written = _wait_for_job_result(controller, job.data["job_id"])
        if not written.ok:
            raise SystemExit(written.message)
        _print(written.data)
        return 0

    if args.command == "evidence-query":
        repro = ReproService(controller.repository, controller.context_store, controller.jobs)
        if args.kind == "proof":
            job = repro.repro_QueryProofAsync(args.package)
        elif args.kind == "validity-alert":
            if not args.alert_id:
                raise SystemExit("--alert-id is required for validity-alert")
            job = repro.repro_QueryValidityByAlertIdAsync(args.alert_id, args.package)
        elif args.kind == "validity-diag":
            if not args.diag_id:
                raise SystemExit("--diag-id is required for validity-diag")
            job = repro.repro_QueryValidityByDiagIdAsync(args.diag_id, args.package)
        else:
            if not args.object_kind or not args.object_id:
                raise SystemExit("--object-kind and --object-id are required for validity-object")
            job = repro.repro_QueryValidityByObjectAsync(args.object_kind, args.object_id, args.package)
        if not job.ok:
            raise SystemExit(job.message)
        queried = _wait_for_job_result(controller, job.data["job_id"])
        if not queried.ok:
            raise SystemExit(queried.message)
        _print({"job_id": job.data["job_id"], "kind": args.kind, "result": queried.data})
        return 0

    if args.command == "repro":
        repro = ReproService(controller.repository, controller.context_store, controller.jobs)
        opened = repro.repro_OpenPackage(args.package)
        if not opened.ok:
            raise SystemExit(opened.message)
        context = repro.repro_RestoreContext(None)
        dataset_id = repro.repro_LoadAsDataset("single")
        _print({"context": context.data.persisted_dict(), "dataset_id": dataset_id.data})
        return 0

    if args.command == "shell":
        from .gui import main as gui_main

        gui_argv: list[str] = []
        if args.input:
            gui_argv.extend(["--input", args.input])
        if args.offscreen:
            gui_argv.append("--offscreen")
        return gui_main(gui_argv)

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tool.formal_windows_common import add_common_arguments, build_config, validate_scenario_contract_binding
from parser.agent_contract import AGENT_ARTIFACT_WRITTEN, AGENT_RUNNING, AGENT_VALIDATING_INPUT
from parser.formal_scheduler import FormalSchedulePlan, FormalSuiteSchedulerAgent, FormalTask, FormalTaskResult


def _script_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def _summary_artifacts(archive_root: Path, *, include_parity: bool = True) -> dict[str, Path]:
    parity_report = archive_root / "formal_parity_report.json"
    return {
        "A": archive_root / "A_control_plane_first" / "formal_summary_windows.json",
        "B": archive_root / "B_budget_pre_freeze" / "formal_summary_windows.json",
        "C": archive_root / "C_degraded_audit" / "formal_summary_windows.json",
        "manifest": archive_root / "windows_artifact_manifest.json",
        **({"parity": parity_report} if include_parity else {}),
        "readiness": archive_root / "windows_final_readiness_report.json",
    }


def _plan_from_payload(payload: dict[str, object]) -> FormalSchedulePlan:
    waves = [
        [
            FormalTask(
                task_id=str(task["task_id"]),
                group=str(task.get("group") or ""),
                command=[str(item) for item in list(task.get("command") or [])],
                dependencies=[str(item) for item in list(task.get("dependencies") or [])],
                priority=int(task.get("priority") or 100),
                resource_class=str(task.get("resource_class") or "default"),
            )
            for task in list(wave)
            if isinstance(task, dict)
        ]
        for wave in list(payload.get("waves") or [])
    ]
    return FormalSchedulePlan(
        schedule_version=str(payload.get("schedule_version") or "formal-suite-schedule-v1"),
        strategy=str(payload.get("strategy") or "serial_safe"),
        max_parallel=int(payload.get("max_parallel") or 1),
        waves=waves,
        reasons=[str(item) for item in list(payload.get("reasons") or [])],
    )


def _task_results_from_payload(rows: list[object]) -> list[FormalTaskResult]:
    results: list[FormalTaskResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        results.append(
            FormalTaskResult(
                task_id=str(row.get("task_id") or ""),
                group=str(row.get("group") or ""),
                command=[str(item) for item in list(row.get("command") or [])],
                exit_code=int(row.get("exit_code") or 0),
                runtime_seconds=float(row.get("runtime_seconds") or 0.0),
                started_at=str(row.get("started_at") or ""),
                finished_at=str(row.get("finished_at") or ""),
                error_code=str(row.get("error_code")) if row.get("error_code") is not None else None,
                error_message=str(row.get("error_message")) if row.get("error_message") is not None else None,
            )
        )
    return results


def _refresh_report_only(*, source_report: Path, archive_root: Path, output_report: Path) -> None:
    source = source_report.expanduser().resolve()
    output = output_report.expanduser().resolve()
    if source == output:
        raise ValueError("refresh-only output report must differ from source report")
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    plan = _plan_from_payload(dict(payload.get("plan") or {}))
    execution = dict(payload.get("execution") or {})
    results = _task_results_from_payload(list(execution.get("task_results") or []))
    scheduler = FormalSuiteSchedulerAgent(job_id="formal-windows-suite-refresh")
    scheduler.contract.transition(AGENT_VALIDATING_INPUT)
    scheduler.contract.add_input_ref(source, kind="json", role="source_schedule_report")
    scheduler.contract.transition(AGENT_RUNNING, refresh_only=True)
    refreshed_execution = scheduler._execution_summary(
        plan,
        results,
        resource_guard=dict(execution.get("resource_guard") or {}),
        resource_observation=dict(execution.get("resource_observation") or {}),
        summary_artifacts=_summary_artifacts(archive_root),
    )
    refreshed_execution["refresh_only"] = True
    refreshed_execution["source_report"] = str(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    scheduler.contract.transition(AGENT_ARTIFACT_WRITTEN)
    scheduler.contract.add_artifact(output, kind="json", role="refresh_only_schedule_report")
    scheduler.contract.complete(refresh_only=True)
    output.write_text(
        json.dumps(
            {
                "plan": plan.to_dict(),
                "execution": refreshed_execution,
                "agent_contract": scheduler.contract.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_formal_windows_suite")
    add_common_arguments(parser, include_scratch_dir=True, require_trace=False)
    parser.add_argument("--source-report", type=Path, default=None)
    parser.add_argument("--refresh-only-from-report", type=Path, default=None)
    parser.add_argument("--skip-desktop-preflight", action="store_true")
    parser.add_argument("--skip-env-check", action="store_true")
    parser.add_argument("--skip-preparation", action="store_true")
    parser.add_argument("--skip-a", action="store_true")
    parser.add_argument("--skip-b", action="store_true")
    parser.add_argument("--skip-c", action="store_true")
    parser.add_argument("--skip-manifest", action="store_true")
    parser.add_argument("--skip-parity", action="store_true")
    parser.add_argument("--skip-readiness", action="store_true")
    parser.add_argument(
        "--scheduler-strategy",
        choices=["serial_safe", "sidecar_first", "bounded_parallel", "degraded_priority"],
        default="serial_safe",
    )
    parser.add_argument("--schedule-report", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.refresh_only_from_report is not None:
        archive_root = args.archive_root.expanduser().resolve()
        output_report = args.schedule_report or (archive_root / "formal_scheduler_plan_windows_refresh.json")
        _refresh_report_only(
            source_report=args.refresh_only_from_report,
            archive_root=archive_root,
            output_report=output_report,
        )
        return 0
    if args.trace is None:
        parser.error("--trace is required unless --refresh-only-from-report is used")
    try:
        config = build_config(args)
        if config.scenario_contract_path is not None and args.source_report is not None:
            validate_scenario_contract_binding(
                config,
                source_report_path=args.source_report.expanduser().resolve(),
            )
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")

    common = [
        "--archive-root",
        str(config.archive_root),
        "--trace",
        str(config.trace_path),
        "--expected-trace-sha256",
        str(config.expected_trace_sha256),
        "--expected-trace-size-bytes",
        str(config.expected_trace_size_bytes),
    ]
    if config.scenario_contract_path is not None:
        common.extend(["--scenario-contract", str(config.scenario_contract_path)])

    scheduled_tasks: list[FormalTask] = []
    if not args.skip_preparation:
        prep_cmd = [
            sys.executable,
            str(_script_path("run_formal_windows_preparation.py")),
            *common,
            "--scratch-dir",
            str(config.scratch_dir),
        ]
        if args.source_report is not None:
            prep_cmd.extend(["--source-report", str(args.source_report.expanduser().resolve())])
        if args.skip_desktop_preflight:
            prep_cmd.append("--skip-desktop-preflight")
        if args.skip_env_check:
            prep_cmd.append("--skip-env-check")
        scheduled_tasks.append(FormalTask("preparation", "prep", prep_cmd, priority=0, resource_class="io"))
    if not args.skip_a:
        cmd = [sys.executable, str(_script_path("run_formal_a_windows.py")), *common]
        scheduled_tasks.append(FormalTask("formal_a", "A", cmd, dependencies=["preparation"], priority=10, resource_class="sidecar"))
    if not args.skip_b:
        cmd = [sys.executable, str(_script_path("run_formal_b_windows.py")), *common]
        scheduled_tasks.append(FormalTask("formal_b", "B", cmd, dependencies=["formal_a"], priority=20, resource_class="cpu"))
    if not args.skip_c:
        cmd = [sys.executable, str(_script_path("run_formal_c_windows.py")), *common]
        scheduled_tasks.append(FormalTask("formal_c", "C", cmd, dependencies=["formal_a"], priority=30, resource_class="cpu"))
    if not args.skip_manifest:
        cmd = [
            sys.executable,
            str(_script_path("build_windows_formal_manifest.py")),
            "--archive-root",
            str(config.archive_root),
        ]
        scheduled_tasks.append(FormalTask("manifest", "summary", cmd, dependencies=["formal_b", "formal_c"], priority=90, resource_class="io"))
    if not args.skip_parity:
        cmd = [
            sys.executable,
            str(_script_path("build_windows_formal_parity.py")),
            "--archive-root",
            str(config.archive_root),
            "--windows-manifest",
            str(config.archive_root / "windows_artifact_manifest.json"),
            "--output",
            str(config.archive_root / "formal_parity_report.json"),
        ]
        parity_dependencies = [] if args.skip_manifest else ["manifest"]
        scheduled_tasks.append(FormalTask("parity", "summary", cmd, dependencies=parity_dependencies, priority=95, resource_class="io"))
    if not args.skip_readiness:
        cmd = [
            sys.executable,
            str(_script_path("build_windows_formal_readiness.py")),
            "--archive-root",
            str(config.archive_root),
        ]
        if not args.skip_parity:
            cmd.extend(["--parity-report", str(config.archive_root / "formal_parity_report.json")])
        readiness_dependencies = []
        if not args.skip_parity:
            readiness_dependencies.append("parity")
        elif not args.skip_manifest:
            readiness_dependencies.append("manifest")
        scheduled_tasks.append(FormalTask("readiness", "summary", cmd, dependencies=readiness_dependencies, priority=100, resource_class="io"))
    scheduler = FormalSuiteSchedulerAgent(job_id="formal-windows-suite")
    if config.scenario_contract_path is not None:
        scheduler.contract.add_input_ref(
            config.scenario_contract_path,
            kind="json",
            role="scenario_execution_contract",
        )
    planned = scheduler.plan(scheduled_tasks, strategy=args.scheduler_strategy)
    if not planned.ok:
        raise RuntimeError(planned.message)
    executed = scheduler.execute_plan(
        planned.data,
        cwd=config.repo_root,
        resource_policy={"min_cpu_count": 1},
        summary_artifacts=_summary_artifacts(config.archive_root, include_parity=not args.skip_parity),
    )
    schedule_report = args.schedule_report or (config.archive_root / "formal_scheduler_plan_windows.json")
    schedule_report.parent.mkdir(parents=True, exist_ok=True)
    schedule_report.write_text(
        json.dumps(
            {
                "plan": planned.data.to_dict(),
                "execution": executed.data,
                "agent_contract": scheduler.contract.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if not executed.ok:
        raise RuntimeError(executed.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from parser.agent_contract import (
    ERR_AGENT_TIMEOUT,
    ERR_FORMAL_TASK_FAILED,
    ERR_FORMAL_SCHEDULER_RESOURCE_GUARD,
    AgentJobContract,
    AGENT_RUNNING,
    AGENT_VALIDATING_INPUT,
)
from parser.result import Result, ok_result
from parser.runtime_advisor import SCHEDULES


FORMAL_SUITE_SCHEDULER_AGENT_NAME = "FormalSuiteSchedulerAgent"
FORMAL_SUBPROCESS_ENV = {"PYTHONDONTWRITEBYTECODE": "1"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class FormalTask:
    task_id: str
    group: str
    command: list[str]
    dependencies: list[str] = field(default_factory=list)
    priority: int = 100
    resource_class: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "group": self.group,
            "command": list(self.command),
            "dependencies": list(self.dependencies),
            "priority": int(self.priority),
            "resource_class": self.resource_class,
        }


@dataclass(frozen=True)
class FormalSchedulePlan:
    schedule_version: str
    strategy: str
    max_parallel: int
    waves: list[list[FormalTask]]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_version": self.schedule_version,
            "strategy": self.strategy,
            "max_parallel": int(self.max_parallel),
            "waves": [[task.to_dict() for task in wave] for wave in self.waves],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class FormalTaskResult:
    task_id: str
    group: str
    command: list[str]
    exit_code: int
    runtime_seconds: float
    started_at: str
    finished_at: str
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error_code is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "group": self.group,
            "command": list(self.command),
            "exit_code": int(self.exit_code),
            "runtime_seconds": float(self.runtime_seconds),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "ok": bool(self.ok),
        }


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def default_formal_tasks(*, python_executable: str = "python") -> list[FormalTask]:
    return [
        FormalTask(
            task_id="preparation",
            group="prep",
            command=[python_executable, "tool/run_formal_windows_preparation.py"],
            priority=0,
            resource_class="io",
        ),
        FormalTask(
            task_id="formal_a",
            group="A",
            command=[python_executable, "tool/run_formal_a_windows.py"],
            dependencies=["preparation"],
            priority=10,
            resource_class="sidecar",
        ),
        FormalTask(
            task_id="formal_b",
            group="B",
            command=[python_executable, "tool/run_formal_b_windows.py"],
            dependencies=["formal_a"],
            priority=20,
            resource_class="cpu",
        ),
        FormalTask(
            task_id="formal_c",
            group="C",
            command=[python_executable, "tool/run_formal_c_windows.py"],
            dependencies=["formal_a"],
            priority=30,
            resource_class="cpu",
        ),
        FormalTask(
            task_id="manifest",
            group="summary",
            command=[python_executable, "tool/build_windows_formal_manifest.py"],
            dependencies=["formal_b", "formal_c"],
            priority=90,
            resource_class="io",
        ),
        FormalTask(
            task_id="readiness",
            group="summary",
            command=[python_executable, "tool/build_windows_formal_readiness.py"],
            dependencies=["manifest"],
            priority=100,
            resource_class="io",
        ),
    ]


class FormalSuiteSchedulerAgent:
    def __init__(self, *, job_id: str | None = None) -> None:
        self.contract = AgentJobContract(agent_name=FORMAL_SUITE_SCHEDULER_AGENT_NAME, job_id=job_id)

    def plan(
        self,
        tasks: Iterable[FormalTask | dict[str, Any]],
        *,
        strategy: str | None = None,
        advisor_decision: Any = None,
        gate_result: Any = None,
        max_parallel: int = 2,
    ) -> Result[FormalSchedulePlan]:
        self.contract.transition(AGENT_VALIDATING_INPUT)
        normalized = [self._task_from_payload(task) for task in tasks]
        gate = _mapping(gate_result)
        decision = _mapping(advisor_decision)
        resolved_strategy = str(strategy or decision.get("recommended_schedule") or "serial_safe")
        if resolved_strategy not in SCHEDULES:
            resolved_strategy = "serial_safe"
        if gate and not bool(gate.get("accepted", False)):
            resolved_strategy = "serial_safe"
        max_parallel = max(1, int(max_parallel))
        if resolved_strategy in {"serial_safe", "sidecar_first", "degraded_priority"}:
            max_parallel = 1
        self.contract.transition(AGENT_RUNNING, strategy=resolved_strategy, task_count=len(normalized))
        if self._has_dependency_cycle(normalized):
            message = "formal schedule dependencies contain a cycle"
            self.contract.fail(ERR_FORMAL_SCHEDULER_RESOURCE_GUARD, message)
            return Result(ERR_FORMAL_SCHEDULER_RESOURCE_GUARD, message, data={"agent_contract": self.contract.to_dict()})
        waves = self._build_waves(normalized, strategy=resolved_strategy, max_parallel=max_parallel)
        reasons = [f"strategy={resolved_strategy}"]
        if gate:
            reasons.append("advisor gate accepted" if gate.get("accepted") else "advisor gate rejected; serial safe fallback")
        plan = FormalSchedulePlan(
            schedule_version="formal-suite-schedule-v1",
            strategy=resolved_strategy,
            max_parallel=max_parallel,
            waves=waves,
            reasons=reasons,
        )
        self.contract.complete(wave_count=len(waves), task_count=len(normalized), strategy=resolved_strategy)
        return ok_result(plan)

    def run_suite(
        self,
        tasks: Iterable[FormalTask | dict[str, Any]],
        *,
        strategy: str | None = None,
        advisor_decision: Any = None,
        gate_result: Any = None,
        max_parallel: int = 2,
        cwd: str | Path | None = None,
        resource_policy: dict[str, Any] | None = None,
        summary_artifacts: dict[str, str | Path] | None = None,
        fail_fast: bool = True,
    ) -> Result[dict[str, Any]]:
        planned = self.plan(
            tasks,
            strategy=strategy,
            advisor_decision=advisor_decision,
            gate_result=gate_result,
            max_parallel=max_parallel,
        )
        if not planned.ok:
            return Result(planned.code, planned.message, data=planned.data)
        executed = self.execute_plan(
            planned.data,
            cwd=cwd,
            resource_policy=resource_policy,
            summary_artifacts=summary_artifacts,
            fail_fast=fail_fast,
        )
        if not executed.ok:
            return executed
        return ok_result({"plan": planned.data.to_dict(), **dict(executed.data or {})})

    def execute_plan(
        self,
        plan: FormalSchedulePlan,
        *,
        cwd: str | Path | None = None,
        resource_policy: dict[str, Any] | None = None,
        summary_artifacts: dict[str, str | Path] | None = None,
        fail_fast: bool = True,
    ) -> Result[dict[str, Any]]:
        workdir = Path(cwd or ".").expanduser().resolve()
        policy = dict(resource_policy or {})
        guard = self._resource_guard(workdir, policy, max_parallel=plan.max_parallel)
        if not guard["accepted"]:
            message = str(guard["rejected_reason"])
            self.contract.fail(ERR_FORMAL_SCHEDULER_RESOURCE_GUARD, message)
            return Result(
                ERR_FORMAL_SCHEDULER_RESOURCE_GUARD,
                message,
                data={"resource_guard": guard, "agent_contract": self.contract.to_dict()},
            )

        self.contract.transition(AGENT_RUNNING, execution="formal_suite", max_parallel=plan.max_parallel)
        results: list[FormalTaskResult] = []
        active_count = 0
        observed_max_parallel = 0
        active_lock = threading.Lock()

        def _run_observed(task: FormalTask) -> FormalTaskResult:
            nonlocal active_count, observed_max_parallel
            with active_lock:
                active_count += 1
                observed_max_parallel = max(observed_max_parallel, active_count)
            try:
                return self._run_task(task, cwd=workdir, timeout_s=policy.get("task_timeout_s"))
            finally:
                with active_lock:
                    active_count -= 1

        for wave_index, wave in enumerate(plan.waves):
            max_workers = max(1, min(int(plan.max_parallel), len(wave)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_run_observed, task): task
                    for task in wave
                }
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    results.append(result)
                    if not result.ok and fail_fast:
                        summary = self._execution_summary(
                            plan,
                            results,
                            resource_guard=guard,
                            resource_observation=self._resource_observation(
                                workdir,
                                guard,
                                plan=plan,
                                observed_max_parallel=observed_max_parallel,
                            ),
                            summary_artifacts=summary_artifacts,
                        )
                        self.contract.fail(result.error_code or ERR_FORMAL_TASK_FAILED, result.error_message or "formal task failed")
                        return Result(
                            result.error_code or ERR_FORMAL_TASK_FAILED,
                            result.error_message or f"formal task failed: {result.task_id}",
                            data={**summary, "failed_wave_index": wave_index, "agent_contract": self.contract.to_dict()},
                        )
        summary = self._execution_summary(
            plan,
            results,
            resource_guard=guard,
            resource_observation=self._resource_observation(
                workdir,
                guard,
                plan=plan,
                observed_max_parallel=observed_max_parallel,
            ),
            summary_artifacts=summary_artifacts,
        )
        self.contract.complete(
            executed_task_count=len(results),
            failed_task_count=sum(1 for result in results if not result.ok),
            max_parallel=plan.max_parallel,
            observed_max_parallel=observed_max_parallel,
        )
        return ok_result({**summary, "agent_contract": self.contract.to_dict()})

    def _resource_guard(self, cwd: Path, policy: dict[str, Any], *, max_parallel: int) -> dict[str, Any]:
        snapshot = self._resource_snapshot(cwd, max_parallel=max_parallel)
        cpu_count = int(snapshot["cpu_count"])
        min_free_disk_bytes = int(policy.get("min_free_disk_bytes") or 0)
        min_cpu_count = int(policy.get("min_cpu_count") or 1)
        reasons: list[str] = []
        if int(snapshot["free_disk_bytes"]) < min_free_disk_bytes:
            reasons.append(f"free disk {snapshot['free_disk_bytes']} < required {min_free_disk_bytes}")
        if cpu_count < min_cpu_count:
            reasons.append(f"cpu count {cpu_count} < required {min_cpu_count}")
        if int(max_parallel) > cpu_count:
            reasons.append(f"max_parallel {max_parallel} exceeds cpu count {cpu_count}")
        return {
            "accepted": not reasons,
            "rejected_reason": "; ".join(reasons) if reasons else None,
            "resource_snapshot": snapshot,
            "resource_policy": {
                "min_free_disk_bytes": int(min_free_disk_bytes),
                "min_cpu_count": int(min_cpu_count),
                "task_timeout_s": policy.get("task_timeout_s"),
            },
        }

    def _resource_snapshot(self, cwd: Path, *, max_parallel: int) -> dict[str, Any]:
        disk_root = cwd if cwd.exists() else cwd.parent
        disk = shutil.disk_usage(disk_root)
        return {
            "captured_at": _iso_now(),
            "cwd": str(cwd),
            "free_disk_bytes": int(disk.free),
            "total_disk_bytes": int(disk.total),
            "cpu_count": int(os.cpu_count() or 1),
            "max_parallel": int(max_parallel),
        }

    def _resource_observation(
        self,
        cwd: Path,
        resource_guard: dict[str, Any],
        *,
        plan: FormalSchedulePlan,
        observed_max_parallel: int,
    ) -> dict[str, Any]:
        pre_snapshot = dict(resource_guard.get("resource_snapshot") or {})
        post_snapshot = self._resource_snapshot(cwd, max_parallel=plan.max_parallel)
        pre_free = int(pre_snapshot.get("free_disk_bytes") or 0)
        post_free = int(post_snapshot.get("free_disk_bytes") or 0)
        wave_parallel_cap = max((min(int(plan.max_parallel), len(wave)) for wave in plan.waves), default=1)
        return {
            "pre_resource_snapshot": pre_snapshot,
            "post_resource_snapshot": post_snapshot,
            "disk_free_delta_bytes": int(post_free - pre_free),
            "observed_max_parallel": int(observed_max_parallel),
            "bounded_parallel_observed": bool(
                plan.strategy == "bounded_parallel"
                and int(observed_max_parallel) >= min(int(plan.max_parallel), int(wave_parallel_cap))
            ),
        }

    def _run_task(self, task: FormalTask, *, cwd: Path, timeout_s: Any = None) -> FormalTaskResult:
        started_at = _iso_now()
        started = time.perf_counter()
        try:
            env = os.environ.copy()
            env.update(FORMAL_SUBPROCESS_ENV)
            completed = subprocess.run(
                task.command,
                cwd=str(cwd),
                check=False,
                env=env,
                timeout=None if timeout_s is None else float(timeout_s),
            )
            exit_code = int(completed.returncode)
            error_code = None if exit_code == 0 else ERR_FORMAL_TASK_FAILED
            error_message = None if exit_code == 0 else f"formal task {task.task_id} failed with exit_code={exit_code}"
        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            error_code = ERR_AGENT_TIMEOUT
            error_message = f"formal task {task.task_id} exceeded timeout_s={timeout_s}: {exc}"
        return FormalTaskResult(
            task_id=task.task_id,
            group=task.group,
            command=list(task.command),
            exit_code=exit_code,
            runtime_seconds=round(time.perf_counter() - started, 6),
            started_at=started_at,
            finished_at=_iso_now(),
            error_code=error_code,
            error_message=error_message,
        )

    def _execution_summary(
        self,
        plan: FormalSchedulePlan,
        results: list[FormalTaskResult],
        *,
        resource_guard: dict[str, Any],
        resource_observation: dict[str, Any],
        summary_artifacts: dict[str, str | Path] | None,
    ) -> dict[str, Any]:
        by_group: dict[str, dict[str, Any]] = {}
        for result in results:
            group = str(result.group or "default")
            row = by_group.setdefault(group, {"task_count": 0, "failed_task_count": 0, "runtime_seconds": 0.0})
            row["task_count"] = int(row["task_count"]) + 1
            row["failed_task_count"] = int(row["failed_task_count"]) + (0 if result.ok else 1)
            row["runtime_seconds"] = round(float(row["runtime_seconds"]) + float(result.runtime_seconds), 6)
        artifact_status = self._summary_artifact_status(summary_artifacts)
        parity = self._parity_summary(by_group, artifact_status, results)
        return {
            "execution_version": "formal-suite-execution-v1",
            "plan": plan.to_dict(),
            "resource_guard": resource_guard,
            "resource_observation": resource_observation,
            "task_results": [result.to_dict() for result in results],
            "group_summary": by_group,
            "summary_artifacts": artifact_status,
            "parity_summary": parity,
        }

    def _summary_artifact_status(self, summary_artifacts: dict[str, str | Path] | None) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        for group, path in dict(summary_artifacts or {}).items():
            resolved = Path(path).expanduser().resolve()
            status: dict[str, Any] = {
                "path": str(resolved),
                "exists": resolved.exists(),
                "loaded": False,
                "load_error": None,
            }
            if resolved.exists() and resolved.is_file():
                try:
                    status["payload"] = json.loads(resolved.read_text(encoding="utf-8-sig"))
                    status["loaded"] = True
                except (OSError, json.JSONDecodeError) as exc:
                    status["load_error"] = str(exc)
            statuses[str(group)] = status
        return statuses

    def _artifact_payload(self, artifact_status: dict[str, dict[str, Any]], *keys: str) -> dict[str, Any]:
        for key in keys:
            payload = artifact_status.get(key, {}).get("payload")
            if isinstance(payload, dict):
                return dict(payload)
        return {}

    def _artifact_exists(self, artifact_status: dict[str, dict[str, Any]], *keys: str) -> bool:
        return any(bool(artifact_status.get(key, {}).get("exists")) for key in keys)

    def _parity_metric_summary(self, parity_payload: dict[str, Any]) -> tuple[int | None, bool | None]:
        if not parity_payload:
            return None, None
        if "metric_diff_count" in parity_payload:
            metric_diff_count = int(parity_payload.get("metric_diff_count") or 0)
        else:
            groups = parity_payload.get("groups") or []
            if isinstance(groups, dict):
                group_rows = list(groups.values())
            else:
                group_rows = list(groups)
            metric_diff_count = sum(int(dict(row).get("metric_diff_count") or 0) for row in group_rows if isinstance(row, dict))
        if "mandatory_field_set_same" in parity_payload:
            mandatory_field_set_same = bool(parity_payload.get("mandatory_field_set_same"))
        else:
            groups = parity_payload.get("groups") or []
            if isinstance(groups, dict):
                group_rows = list(groups.values())
            else:
                group_rows = list(groups)
            mandatory_values = [
                bool(dict(dict(row).get("mandatory_field_set") or {}).get("same"))
                for row in group_rows
                if isinstance(row, dict) and isinstance(dict(row).get("mandatory_field_set"), dict)
            ]
            mandatory_field_set_same = bool(mandatory_values) and all(mandatory_values)
        return int(metric_diff_count), bool(mandatory_field_set_same)

    def _parity_summary(
        self,
        by_group: dict[str, dict[str, Any]],
        artifact_status: dict[str, dict[str, Any]],
        results: list[FormalTaskResult],
    ) -> dict[str, Any]:
        a_payload = self._artifact_payload(artifact_status, "A", "A_control_plane_first")
        b_payload = self._artifact_payload(artifact_status, "B", "B_budget_pre_freeze")
        c_payload = self._artifact_payload(artifact_status, "C", "C_degraded_audit")
        readiness_payload = self._artifact_payload(artifact_status, "readiness")
        parity_payload = self._artifact_payload(artifact_status, "parity", "parity_report")
        readiness_verdict = str(readiness_payload.get("verdict") or "")
        parity_report_status = str(parity_payload.get("status") or "")
        metric_diff_count, mandatory_field_set_same = self._parity_metric_summary(parity_payload)
        abc_summary_files_present = all(
            self._artifact_exists(artifact_status, *keys)
            for keys in (("A", "A_control_plane_first"), ("B", "B_budget_pre_freeze"), ("C", "C_degraded_audit"))
        )
        abc_summary_verdicts_pass = all(str(payload.get("verdict")) == "pass" for payload in (a_payload, b_payload, c_payload))
        readiness_ready = readiness_verdict in {"ready_for_gate", "ready_for_linux_parity", "ready"}
        parity_ready = bool(parity_payload.get("ready_for_gate")) or parity_report_status == "pass"
        failed_task_count = sum(1 for result in results if not result.ok)
        return {
            "abc_groups_present": all(group in by_group for group in ("A", "B", "C")),
            "abc_summary_files_present": bool(abc_summary_files_present),
            "abc_summary_verdicts_pass": bool(abc_summary_verdicts_pass),
            "readiness_verdict": readiness_verdict or None,
            "parity_report_status": parity_report_status or None,
            "metric_diff_count": metric_diff_count,
            "mandatory_field_set_same": mandatory_field_set_same,
            "ready_for_gate": bool(
                all(group in by_group for group in ("A", "B", "C"))
                and abc_summary_files_present
                and abc_summary_verdicts_pass
                and readiness_ready
                and parity_ready
                and metric_diff_count == 0
                and mandatory_field_set_same is True
                and failed_task_count == 0
            ),
            "failed_task_count": failed_task_count,
        }

    def _task_from_payload(self, payload: FormalTask | dict[str, Any]) -> FormalTask:
        if isinstance(payload, FormalTask):
            return payload
        data = dict(payload)
        return FormalTask(
            task_id=str(data["task_id"]),
            group=str(data.get("group") or ""),
            command=[str(item) for item in list(data.get("command") or [])],
            dependencies=[str(item) for item in list(data.get("dependencies") or [])],
            priority=int(data.get("priority") or 100),
            resource_class=str(data.get("resource_class") or "default"),
        )

    def _sort_ready(self, tasks: list[FormalTask], *, strategy: str) -> list[FormalTask]:
        if strategy == "degraded_priority":
            return sorted(tasks, key=lambda task: (0 if task.group == "C" else 1, task.priority, task.task_id))
        if strategy == "sidecar_first":
            return sorted(tasks, key=lambda task: (0 if task.resource_class == "sidecar" else 1, task.priority, task.task_id))
        return sorted(tasks, key=lambda task: (task.priority, task.task_id))

    def _build_waves(self, tasks: list[FormalTask], *, strategy: str, max_parallel: int) -> list[list[FormalTask]]:
        pending = {task.task_id: task for task in tasks}
        known_ids = set(pending)
        completed: set[str] = set()
        waves: list[list[FormalTask]] = []
        while pending:
            ready = [
                task
                for task in pending.values()
                if all(dep not in known_ids or dep in completed for dep in task.dependencies)
            ]
            if not ready:
                return waves
            wave = self._sort_ready(ready, strategy=strategy)[:max_parallel]
            waves.append(wave)
            for task in wave:
                completed.add(task.task_id)
                pending.pop(task.task_id, None)
        return waves

    def _has_dependency_cycle(self, tasks: list[FormalTask]) -> bool:
        by_id = {task.task_id: task for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> bool:
            if task_id in visited:
                return False
            if task_id in visiting:
                return True
            visiting.add(task_id)
            for dep in by_id.get(task_id, FormalTask(task_id, "", [])).dependencies:
                if dep in by_id and visit(dep):
                    return True
            visiting.remove(task_id)
            visited.add(task_id)
            return False

        return any(visit(task.task_id) for task in tasks)

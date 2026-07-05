from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from pathlib import Path
from typing import Any


AGENT_JOB_CONTRACT_VERSION = "agent-job-contract-v1"

AGENT_QUEUED = "AGENT-queued"
AGENT_VALIDATING_INPUT = "AGENT-validating_input"
AGENT_RUNNING = "AGENT-running"
AGENT_ARTIFACT_WRITTEN = "AGENT-artifact_written"
AGENT_COMPLETED = "AGENT-completed"
AGENT_FAILED = "AGENT-failed"
AGENT_CANCELLED = "AGENT-cancelled"
AGENT_TIMEOUT = "AGENT-timeout"

ERR_PACKAGE_WRITE_FAILED = "ERR-PACKAGE_WRITE_FAILED"
ERR_PARSER_PROCESS_FAILED = "ERR-PARSER_PROCESS_FAILED"
ERR_FORMAL_SCHEDULER_RESOURCE_GUARD = "ERR-FORMAL_SCHEDULER_RESOURCE_GUARD"
ERR_FORMAL_TASK_FAILED = "ERR-FORMAL_TASK_FAILED"
ERR_AGENT_TIMEOUT = "ERR-AGENT_TIMEOUT"
ERR_AGENT_CANCELLED = "ERR-AGENT_CANCELLED"


AGENT_KIND_BY_NAME = {
    "SidecarIndexAgent": "sidecar_index",
    "ExportWriteAgent": "export_write",
    "ParserProcessAgent": "parser_process",
    "FormalSuiteSchedulerAgent": "formal_scheduler",
    "TelemetryReportAgent": "telemetry_report",
    "RuntimeOptimizationAdvisor": "runtime_advisor",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def artifact_handle(path: str | Path, *, kind: str, role: str | None = None) -> dict[str, Any]:
    payload = {
        "kind": str(kind),
        "path": str(Path(path).expanduser().resolve()),
    }
    if role is not None:
        payload["role"] = str(role)
    return payload


@dataclass
class AgentJobContract:
    agent_name: str
    job_id: str | None = None
    contract_version: str = AGENT_JOB_CONTRACT_VERSION
    created_at: str = field(default_factory=iso_now)
    status: str = AGENT_QUEUED
    started_at: str | None = None
    completed_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    failed_at_state: str | None = None
    input_refs: list[dict[str, Any]] = field(default_factory=list)
    artifact_handles: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    state_history: list[dict[str, Any]] = field(default_factory=list)
    _started_perf_counter: float | None = field(default=None, init=False, repr=False)

    def transition(self, status: str, **telemetry: Any) -> "AgentJobContract":
        now = iso_now()
        if self.started_at is None and status != AGENT_QUEUED:
            self.started_at = now
            self._started_perf_counter = time.perf_counter()
        self.status = str(status)
        if telemetry:
            self.telemetry.update(telemetry)
        self.state_history.append({"status": self.status, "updated_at": now})
        return self

    def add_artifact(self, path: str | Path, *, kind: str, role: str | None = None) -> "AgentJobContract":
        self.artifact_handles.append(artifact_handle(path, kind=kind, role=role))
        return self

    def add_input_ref(self, path: str | Path, *, kind: str, role: str | None = None) -> "AgentJobContract":
        self.input_refs.append(artifact_handle(path, kind=kind, role=role))
        return self

    def complete(self, **telemetry: Any) -> "AgentJobContract":
        if telemetry:
            self.telemetry.update(telemetry)
        self.status = AGENT_COMPLETED
        self.completed_at = iso_now()
        if self._started_perf_counter is not None:
            self.telemetry.setdefault(
                "duration_seconds",
                round(time.perf_counter() - self._started_perf_counter, 6),
            )
        self.state_history.append({"status": self.status, "updated_at": self.completed_at})
        return self

    def fail(self, error_code: str, error_message: str, *, timeout: bool = False, cancelled: bool = False) -> "AgentJobContract":
        self.failed_at_state = self.status
        self.status = AGENT_TIMEOUT if timeout else (AGENT_CANCELLED if cancelled else AGENT_FAILED)
        self.error_code = str(error_code)
        self.error_message = str(error_message)
        self.completed_at = iso_now()
        if self._started_perf_counter is not None:
            self.telemetry.setdefault(
                "duration_seconds",
                round(time.perf_counter() - self._started_perf_counter, 6),
            )
        self.state_history.append({"status": self.status, "updated_at": self.completed_at})
        return self

    def to_dict(self) -> dict[str, Any]:
        agent_kind = AGENT_KIND_BY_NAME.get(self.agent_name, self.agent_name)
        return {
            "contract_version": self.contract_version,
            "state_contract_version": self.contract_version,
            "agent_name": self.agent_name,
            "agent_kind": agent_kind,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "status": self.status,
            "agent_state": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "finished_at": self.completed_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "failed_at_state": self.failed_at_state,
            "input_refs": list(self.input_refs),
            "output_refs": list(self.artifact_handles),
            "artifact_handles": list(self.artifact_handles),
            "telemetry": dict(self.telemetry),
            "state_history": list(self.state_history),
        }

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from parser.evidence_sidecar_index import (
    SIDECAR_INDEX_SCHEMA_VERSION,
    SIDECAR_INDEX_TICKET_VERSION,
    SidecarIndexTicket,
    read_sidecar_index_ticket,
    sidecar_index_path_for_source,
    sidecar_index_ticket_path_for_source,
    validate_sidecar_index_ticket,
)
from parser.result import Result, ok_result
from parser.runtime_advisor import RUNTIME_LOAD_INDEX_BUILD_MODES, RUNTIME_LOAD_MODES


GATE_VERSION = "runtime-optimization-gate-v1"
RUNTIME_ACTION_GATE_ALLOWLIST = {
    "cold_preview",
    "sidecar_index_prebuild",
    "sidecar_index_reuse",
    "streaming_package_write",
    "need_more_telemetry",
}
_ACTION_POLICY_KEYS = {
    "cold_preview": "cold_preview_enabled",
    "sidecar_index_prebuild": "sidecar_index_prebuild_enabled",
    "sidecar_index_reuse": "sidecar_index_reuse_enabled",
    "streaming_package_write": "streaming_package_write_enabled",
    "need_more_telemetry": "need_more_telemetry_enabled",
}
_UNSAFE_ACTION_TEXT_PATTERNS = (
    re.compile(r"(^|[^a-z0-9])(shell|bash|powershell|cmd(?:\.exe)?|script|python3?|node|perl|ruby|curl|wget)([^a-z0-9]|$)"),
    re.compile(r"schema[-_\s]?migration|migrate[-_\s]?schema"),
    re.compile(r"proof[-_\s]?(path|digest|hash)"),
    re.compile(r"truth[-_\s]?path"),
    re.compile(r"(?:^|[\\/])[^\\/\s]+\.(?:sh|ps1|bat|cmd)\b"),
)


def _decision_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _load_plan_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _dependency_sidecar_checksum_from_manifest(manifest: dict[str, Any] | None) -> str:
    payload = dict(manifest or {})
    entry_checksums = {
        str(path).strip(): str(checksum).strip()
        for path, checksum in dict(payload.get("entry_checksums") or {}).items()
        if str(path).strip()
    }
    for rel_path in list(payload.get("entry_paths") or []) + list(entry_checksums):
        if Path(str(rel_path)).name == "dependency_sidecar.jsonl":
            checksum = entry_checksums.get(str(rel_path).strip())
            if checksum:
                return checksum
    return ""


def _action_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _iter_string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        items: list[str] = []
        for nested in value.values():
            items.extend(_iter_string_values(nested))
        return items
    if isinstance(value, (list, tuple, set)):
        items = []
        for nested in value:
            items.extend(_iter_string_values(nested))
        return items
    return []


def _action_contains_unsafe_output(action: dict[str, Any]) -> bool:
    for key, value in action.items():
        if key == "proof_scope_impact":
            continue
        for text in _iter_string_values(value):
            lowered = str(text or "").strip().lower()
            if not lowered:
                continue
            if any(pattern.search(lowered) for pattern in _UNSAFE_ACTION_TEXT_PATTERNS):
                return True
    return False


def _action_policy_allowed(action_kind: str, policy: dict[str, Any]) -> bool:
    policy_key = _ACTION_POLICY_KEYS.get(action_kind)
    if policy_key and not bool(policy.get(policy_key, True)):
        return False
    if action_kind == "sidecar_index_reuse" and not bool(policy.get("ticket_fast_path_enabled", True)):
        return False
    return True


def _gate_result_to_result(gate_result: "ValidationGateResult", *, action: str) -> Result["ValidationGateResult"]:
    if gate_result.accepted:
        return ok_result(gate_result)
    reason = gate_result.rejected_reason or "ERR-ADVISOR_REJECTED_BY_GATE"
    return Result(
        code=reason,
        message=f"{action} rejected by deterministic validation gate: {reason}",
        data=gate_result,
    )


@dataclass(frozen=True)
class ValidationGateResult:
    gate_version: str
    accepted: bool
    rejected_reason: str | None
    checked_ticket: bool
    checked_schema: bool
    checked_checksum: bool
    checked_fingerprint: bool
    checked_policy: bool
    execution_plan: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_version": self.gate_version,
            "accepted": bool(self.accepted),
            "rejected_reason": self.rejected_reason,
            "checked_ticket": bool(self.checked_ticket),
            "checked_schema": bool(self.checked_schema),
            "checked_checksum": bool(self.checked_checksum),
            "checked_fingerprint": bool(self.checked_fingerprint),
            "checked_policy": bool(self.checked_policy),
            "execution_plan": list(self.execution_plan),
        }


class DeterministicValidationGate:
    def validate_runtime_load_plan(
        self,
        *,
        runtime_load_plan: Any,
        parser_artifact_present: bool = False,
        sidecar_index_ticket_present: bool = False,
    ) -> ValidationGateResult:
        plan = _load_plan_mapping(runtime_load_plan)
        load_mode = str(plan.get("load_mode") or "")
        index_build_mode = str(plan.get("index_build_mode") or "")
        materialize_event_stream = bool(plan.get("materialize_event_stream"))
        try_parser_artifact_reuse = bool(plan.get("try_parser_artifact_reuse"))
        try_sidecar_index_reuse = bool(plan.get("try_sidecar_index_reuse"))
        background_sidecar_prebuild = bool(plan.get("background_sidecar_prebuild"))

        if load_mode not in RUNTIME_LOAD_MODES or index_build_mode not in RUNTIME_LOAD_INDEX_BUILD_MODES:
            return self._reject("ERR-RUNTIME_LOAD_PLAN_INVALID", checked_policy=True)
        if try_parser_artifact_reuse and not parser_artifact_present:
            return self._reject("ERR-PARSER_ARTIFACT_MISSING", checked_policy=True)
        if try_sidecar_index_reuse and not sidecar_index_ticket_present:
            return self._reject("ERR-SIDECAR_INDEX_MISSING", checked_ticket=True, checked_policy=True)

        if load_mode == "full":
            if (
                index_build_mode != "full"
                or not materialize_event_stream
                or try_parser_artifact_reuse
                or try_sidecar_index_reuse
            ):
                return self._reject("ERR-RUNTIME_LOAD_PLAN_INVALID", checked_policy=True)
        elif load_mode == "cold_preview":
            if (
                materialize_event_stream
                or index_build_mode not in {"minimal", "deferred"}
                or try_parser_artifact_reuse
                or try_sidecar_index_reuse
            ):
                return self._reject("ERR-RUNTIME_LOAD_PLAN_INVALID", checked_policy=True)
        elif load_mode in {"warm_reuse", "hot_reuse"}:
            if background_sidecar_prebuild or not (try_parser_artifact_reuse or try_sidecar_index_reuse):
                return self._reject("ERR-RUNTIME_LOAD_PLAN_INVALID", checked_policy=True)

        execution_plan = [f"load:{load_mode}", f"index:{index_build_mode}"]
        if materialize_event_stream:
            execution_plan.append("materialize_event_stream")
        if background_sidecar_prebuild:
            execution_plan.append("background_sidecar_prebuild")
        if try_parser_artifact_reuse:
            execution_plan.append("parser_artifact_reuse")
        if try_sidecar_index_reuse:
            execution_plan.append("sidecar_index_reuse")
        return ValidationGateResult(
            gate_version=GATE_VERSION,
            accepted=True,
            rejected_reason=None,
            checked_ticket=bool(try_sidecar_index_reuse),
            checked_schema=False,
            checked_checksum=False,
            checked_fingerprint=False,
            checked_policy=True,
            execution_plan=execution_plan,
        )

    def validate_runtime_load_plan_result(
        self,
        *,
        runtime_load_plan: Any,
        parser_artifact_present: bool = False,
        sidecar_index_ticket_present: bool = False,
    ) -> Result[ValidationGateResult]:
        return _gate_result_to_result(
            self.validate_runtime_load_plan(
                runtime_load_plan=runtime_load_plan,
                parser_artifact_present=parser_artifact_present,
                sidecar_index_ticket_present=sidecar_index_ticket_present,
            ),
            action="runtime load plan",
        )

    def validate_ticket_fast_path(
        self,
        *,
        sidecar_manifest: dict[str, Any],
        request_context: dict[str, Any],
        policy: dict[str, Any] | None = None,
        sidecar_ticket: SidecarIndexTicket | None = None,
    ) -> ValidationGateResult:
        policy_payload = dict(policy or {})
        if not bool(policy_payload.get("ticket_fast_path_enabled", True)):
            return self._reject("ERR-ADVISOR_REJECTED_BY_GATE", checked_policy=True)

        sidecar_path = request_context.get("sidecar_path")
        if not sidecar_path:
            return self._reject("ERR-SIDECAR_INDEX_MISSING", checked_policy=True)
        index_path = request_context.get("index_path") or sidecar_index_path_for_source(sidecar_path)
        ticket_path = request_context.get("ticket_path") or sidecar_index_ticket_path_for_source(
            sidecar_path,
            index_path=index_path,
        )
        ticket = sidecar_ticket
        if ticket is None:
            loaded = read_sidecar_index_ticket(ticket_path)
            if not loaded.ok:
                return self._reject(loaded.code, checked_policy=True)
            ticket = loaded.data

        checksum = str(request_context.get("sidecar_checksum") or _dependency_sidecar_checksum_from_manifest(sidecar_manifest))
        expected_snapshot_id = str(request_context.get("snapshot_id") or sidecar_manifest.get("snapshot_id") or "")
        expected_trace_checksum = str(request_context.get("trace_checksum") or sidecar_manifest.get("trace_checksum") or "")
        expected_dictionary_checksum = str(
            request_context.get("dictionary_checksum") or sidecar_manifest.get("dictionary_checksum") or ""
        )
        if not checksum or not expected_snapshot_id or not expected_trace_checksum or not expected_dictionary_checksum:
            return self._reject("ERR-SIDECAR_INDEX_MISMATCH", checked_ticket=True, checked_policy=True)
        validated = validate_sidecar_index_ticket(
            ticket,
            sidecar_path=sidecar_path,
            expected_snapshot_id=expected_snapshot_id,
            expected_trace_checksum=expected_trace_checksum,
            expected_dictionary_checksum=expected_dictionary_checksum,
            sidecar_checksum=checksum,
            index_path=index_path,
        )
        if not validated.ok:
            return self._reject(
                validated.code,
                checked_ticket=True,
                checked_schema=True,
                checked_checksum=True,
                checked_policy=True,
            )
        return ValidationGateResult(
            gate_version=GATE_VERSION,
            accepted=True,
            rejected_reason=None,
            checked_ticket=True,
            checked_schema=True,
            checked_checksum=True,
            checked_fingerprint=True,
            checked_policy=True,
            execution_plan=["open_index", "ticket_fast_path"],
        )

    def validate_ticket_fast_path_result(
        self,
        *,
        sidecar_manifest: dict[str, Any],
        request_context: dict[str, Any],
        policy: dict[str, Any] | None = None,
        sidecar_ticket: SidecarIndexTicket | None = None,
    ) -> Result[ValidationGateResult]:
        return _gate_result_to_result(
            self.validate_ticket_fast_path(
                sidecar_manifest=sidecar_manifest,
                request_context=request_context,
                policy=policy,
                sidecar_ticket=sidecar_ticket,
            ),
            action="ticket fast path",
        )

    def validate_runtime_action(
        self,
        *,
        runtime_action: Any,
        sidecar_ticket: SidecarIndexTicket | None = None,
        sidecar_manifest: dict[str, Any] | None = None,
        request_context: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> ValidationGateResult:
        action = _action_mapping(runtime_action)
        policy_payload = dict(policy or {})
        action_kind = str(action.get("action_kind") or "").strip()
        proof_scope_impact = str(action.get("proof_scope_impact") or "none").strip()

        if proof_scope_impact != "none":
            return self._reject("ERR-ACTION_PROOF_SCOPE_IMPACT", checked_policy=True)
        if _action_contains_unsafe_output(action):
            return self._reject("ERR-ACTION_UNSAFE_LLM_OUTPUT", checked_policy=True)
        if action_kind not in RUNTIME_ACTION_GATE_ALLOWLIST:
            return self._reject("ERR-ACTION_UNKNOWN", checked_policy=True)
        if not _action_policy_allowed(action_kind, policy_payload):
            return self._reject("ERR-ACTION_POLICY_DENIED", checked_policy=True)
        if action_kind == "sidecar_index_reuse":
            manifest_payload = dict(sidecar_manifest or {})
            request_payload = dict(request_context or {})
            resolved_sidecar_path = str(request_payload.get("sidecar_path") or "").strip()
            resolved_snapshot_id = str(request_payload.get("snapshot_id") or manifest_payload.get("snapshot_id") or "").strip()
            resolved_trace_checksum = str(
                request_payload.get("trace_checksum") or manifest_payload.get("trace_checksum") or ""
            ).strip()
            resolved_dictionary_checksum = str(
                request_payload.get("dictionary_checksum") or manifest_payload.get("dictionary_checksum") or ""
            ).strip()
            resolved_sidecar_checksum = str(
                request_payload.get("sidecar_checksum") or _dependency_sidecar_checksum_from_manifest(manifest_payload) or ""
            ).strip()
            if (
                sidecar_manifest is None
                or request_context is None
                or not resolved_sidecar_path
                or not resolved_snapshot_id
                or not resolved_trace_checksum
                or not resolved_dictionary_checksum
                or not resolved_sidecar_checksum
            ):
                return self._reject("ERR-ACTION_MISSING_ARTIFACT", checked_ticket=True, checked_policy=True)
            ticket_result = self.validate_ticket_fast_path(
                sidecar_manifest=manifest_payload,
                request_context=request_payload,
                policy=policy_payload,
                sidecar_ticket=sidecar_ticket,
            )
            if not ticket_result.accepted:
                rejected_reason = str(ticket_result.rejected_reason or "")
                if rejected_reason == "ERR-ADVISOR_REJECTED_BY_GATE":
                    return self._reject(
                        "ERR-ACTION_POLICY_DENIED",
                        checked_ticket=ticket_result.checked_ticket,
                        checked_schema=ticket_result.checked_schema,
                        checked_checksum=ticket_result.checked_checksum,
                        checked_fingerprint=ticket_result.checked_fingerprint,
                        checked_policy=True,
                    )
                if rejected_reason == "ERR-SIDECAR_INDEX_MISSING":
                    return self._reject(
                        "ERR-ACTION_MISSING_ARTIFACT",
                        checked_ticket=True,
                        checked_schema=ticket_result.checked_schema,
                        checked_checksum=ticket_result.checked_checksum,
                        checked_fingerprint=ticket_result.checked_fingerprint,
                        checked_policy=True,
                    )
                return self._reject(
                    "ERR-ACTION_CHECKSUM_MISMATCH",
                    checked_ticket=True,
                    checked_schema=True,
                    checked_checksum=True,
                    checked_fingerprint=bool(ticket_result.checked_fingerprint or rejected_reason == "ERR-SIDECAR_INDEX_STALE"),
                    checked_policy=True,
                )
            return ValidationGateResult(
                gate_version=GATE_VERSION,
                accepted=True,
                rejected_reason=None,
                checked_ticket=True,
                checked_schema=bool(ticket_result.checked_schema),
                checked_checksum=bool(ticket_result.checked_checksum),
                checked_fingerprint=bool(ticket_result.checked_fingerprint),
                checked_policy=True,
                execution_plan=list(ticket_result.execution_plan),
            )
        action_execution_plan = {
            "cold_preview": ["cold_preview"],
            "sidecar_index_prebuild": ["build_index"],
            "streaming_package_write": ["stream_write"],
            "need_more_telemetry": ["need_more_telemetry"],
        }.get(action_kind, [])
        return ValidationGateResult(
            gate_version=GATE_VERSION,
            accepted=True,
            rejected_reason=None,
            checked_ticket=False,
            checked_schema=False,
            checked_checksum=False,
            checked_fingerprint=False,
            checked_policy=True,
            execution_plan=action_execution_plan,
        )

    def validate_advisor_decision(
        self,
        *,
        advisor_decision: Any,
        sidecar_ticket: SidecarIndexTicket | None = None,
        sidecar_manifest: dict[str, Any] | None = None,
        request_context: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> ValidationGateResult:
        decision = _decision_mapping(advisor_decision)
        policy_payload = dict(policy or {})
        if not bool(policy_payload.get("advisor_enabled", True)):
            return ValidationGateResult(
                gate_version=GATE_VERSION,
                accepted=True,
                rejected_reason=None,
                checked_ticket=False,
                checked_schema=False,
                checked_checksum=False,
                checked_fingerprint=False,
                checked_policy=True,
                execution_plan=["baseline"],
            )
        proposed_actions = [_action_mapping(item) for item in list(decision.get("proposed_actions") or [])]
        if proposed_actions:
            checked_ticket = False
            checked_schema = False
            checked_checksum = False
            checked_fingerprint = False
            checked_policy = False
            execution_plan: list[str] = []
            for action in proposed_actions:
                action_result = self.validate_runtime_action(
                    runtime_action=action,
                    sidecar_ticket=sidecar_ticket,
                    sidecar_manifest=sidecar_manifest,
                    request_context=request_context,
                    policy=policy_payload,
                )
                if not action_result.accepted:
                    return action_result
                checked_ticket = checked_ticket or bool(action_result.checked_ticket)
                checked_schema = checked_schema or bool(action_result.checked_schema)
                checked_checksum = checked_checksum or bool(action_result.checked_checksum)
                checked_fingerprint = checked_fingerprint or bool(action_result.checked_fingerprint)
                checked_policy = checked_policy or bool(action_result.checked_policy)
                execution_plan.extend(list(action_result.execution_plan))
            schedule = str(decision.get("recommended_schedule") or "")
            if schedule in {"serial_safe", "sidecar_first", "bounded_parallel", "degraded_priority"}:
                execution_plan.append(schedule)
            if not execution_plan:
                execution_plan.append("baseline")
            return ValidationGateResult(
                gate_version=GATE_VERSION,
                accepted=True,
                rejected_reason=None,
                checked_ticket=checked_ticket,
                checked_schema=checked_schema,
                checked_checksum=checked_checksum,
                checked_fingerprint=checked_fingerprint,
                checked_policy=checked_policy,
                execution_plan=list(dict.fromkeys(execution_plan)),
            )
        execution_plan: list[str] = []
        if bool(decision.get("recommend_streaming_write")):
            execution_plan.append("stream_write")
        if str(decision.get("recommended_schedule") or "") in {"serial_safe", "sidecar_first", "bounded_parallel", "degraded_priority"}:
            execution_plan.append(str(decision.get("recommended_schedule")))
        if bool(decision.get("recommend_index_reuse_attempt")):
            if sidecar_manifest is None or request_context is None:
                return self._reject("ERR-SIDECAR_INDEX_MISSING", checked_policy=True)
            ticket_result = self.validate_ticket_fast_path(
                sidecar_manifest=sidecar_manifest,
                request_context=request_context,
                policy=policy_payload,
                sidecar_ticket=sidecar_ticket,
            )
            if not ticket_result.accepted:
                return ticket_result
            execution_plan.extend(ticket_result.execution_plan)
        if bool(decision.get("recommend_index_prebuild")):
            execution_plan.append("build_index")
        if not execution_plan:
            execution_plan.append("baseline")
        return ValidationGateResult(
            gate_version=GATE_VERSION,
            accepted=True,
            rejected_reason=None,
            checked_ticket=bool(sidecar_ticket),
            checked_schema=bool(sidecar_ticket),
            checked_checksum=bool(sidecar_ticket),
            checked_fingerprint=bool(sidecar_ticket),
            checked_policy=True,
            execution_plan=list(dict.fromkeys(execution_plan)),
        )

    def validate_advisor_decision_result(
        self,
        *,
        advisor_decision: Any,
        sidecar_ticket: SidecarIndexTicket | None = None,
        sidecar_manifest: dict[str, Any] | None = None,
        request_context: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> Result[ValidationGateResult]:
        return _gate_result_to_result(
            self.validate_advisor_decision(
                advisor_decision=advisor_decision,
                sidecar_ticket=sidecar_ticket,
                sidecar_manifest=sidecar_manifest,
                request_context=request_context,
                policy=policy,
            ),
            action="advisor decision",
        )

    def _reject(
        self,
        reason: str,
        *,
        checked_ticket: bool = False,
        checked_schema: bool = False,
        checked_checksum: bool = False,
        checked_fingerprint: bool = False,
        checked_policy: bool = False,
    ) -> ValidationGateResult:
        return ValidationGateResult(
            gate_version=GATE_VERSION,
            accepted=False,
            rejected_reason=reason,
            checked_ticket=checked_ticket,
            checked_schema=checked_schema,
            checked_checksum=checked_checksum,
            checked_fingerprint=checked_fingerprint,
            checked_policy=checked_policy,
            execution_plan=[],
        )


def gate_ValidateAdvisorDecision(
    *,
    advisor_decision: Any,
    sidecar_ticket: SidecarIndexTicket | None = None,
    sidecar_manifest: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> Result[ValidationGateResult]:
    return DeterministicValidationGate().validate_advisor_decision_result(
        advisor_decision=advisor_decision,
        sidecar_ticket=sidecar_ticket,
        sidecar_manifest=sidecar_manifest,
        request_context=request_context,
        policy=policy,
    )


def gate_ValidateTicketFastPath(
    *,
    sidecar_manifest: dict[str, Any],
    request_context: dict[str, Any],
    policy: dict[str, Any] | None = None,
    sidecar_ticket: SidecarIndexTicket | None = None,
) -> Result[ValidationGateResult]:
    return DeterministicValidationGate().validate_ticket_fast_path_result(
        sidecar_manifest=sidecar_manifest,
        request_context=request_context,
        policy=policy,
        sidecar_ticket=sidecar_ticket,
    )


def gate_ValidateRuntimeLoadPlan(
    *,
    runtime_load_plan: Any,
    parser_artifact_present: bool = False,
    sidecar_index_ticket_present: bool = False,
) -> Result[ValidationGateResult]:
    return DeterministicValidationGate().validate_runtime_load_plan_result(
        runtime_load_plan=runtime_load_plan,
        parser_artifact_present=parser_artifact_present,
        sidecar_index_ticket_present=sidecar_index_ticket_present,
    )

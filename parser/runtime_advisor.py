from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable

from parser.agent_contract import (
    AGENT_QUEUED,
    AGENT_RUNNING,
    AGENT_VALIDATING_INPUT,
    AgentJobContract,
)
from parser.advisor_retrieval import (
    default_case_bank_root,
    build_case_similarity_features,
    retrieve_runtime_cases,
)
from parser.evidence_sidecar_index import DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES, SidecarIndexTicket
from parser.openai_advisor_client import (
    LLMAdvisorClient,
    LLMAdvisorClientError,
)
from parser.result import Result, ok_result
from parser.telemetry import TelemetryRecord, TelemetryRecordUpdate, normalize_telemetry_history_item
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema
from spec.io import json_dump


ADVISOR_DECISION_VERSION = "runtime-advisor-decision-v1"
ADVISOR_TRACE_VERSION = "runtime-advisor-trace-v1"
ADVISOR_EVIDENCE_CONTEXT_VERSION = "advisor-evidence-context-v1"
RUNTIME_LOAD_PLAN_VERSION = "runtime-load-plan-v1"
RUNTIME_ACTION_SET_VERSION = "runtime-action-set-v1"
HEURISTIC_MODEL_REF = "heuristic-runtime-advisor-v1"
OFFLINE_ADVISOR_PRIOR_VERSION = "runtime-advisor-safe-prior-v1"
OFFLINE_ADVISOR_MODEL_REF = "offline-safe-prior-v1"
ERR_ADVISOR_UNAVAILABLE = "ERR-ADVISOR_UNAVAILABLE"
RISK_LEVELS = {"low", "medium", "high", "critical"}
HIGH_RISK_ACTION_LEVELS = {"high", "critical"}
SCHEDULES = {"serial_safe", "sidecar_first", "bounded_parallel", "degraded_priority"}
ADVISOR_MODES = {"disabled", "heuristic", "offline_coefficients", "openai_structured"}
RUNTIME_LOAD_MODES = {"full", "cold_preview", "warm_reuse", "hot_reuse"}
RUNTIME_LOAD_INDEX_BUILD_MODES = {"full", "minimal", "deferred"}
SAFE_RUNTIME_ACTION_KINDS = (
    "baseline_full_load",
    "cold_preview",
    "sidecar_index_prebuild",
    "sidecar_index_reuse",
    "streaming_package_write",
    "deferred_index_build",
    "abstain",
)
RUNTIME_ACTION_KINDS = set(SAFE_RUNTIME_ACTION_KINDS)
RUNTIME_ACTION_PROOF_SCOPE_IMPACTS = {"none"}
DEFAULT_RUNTIME_LOAD_LARGE_INPUT_THRESHOLD_BYTES = 256 * 1024 * 1024
_ADVISOR_UNSAFE_TEXT_PATTERNS = (
    re.compile(r"(^|[^a-z0-9])(shell|bash|powershell|cmd(?:\.exe)?|script|python3?|node|perl|ruby|curl|wget)([^a-z0-9]|$)"),
    re.compile(r"schema[-_\s]?migration|migrate[-_\s]?schema"),
    re.compile(r"proof[-_\s]?(path|digest|hash)"),
    re.compile(r"truth[-_\s]?path"),
    re.compile(r"(?:(?:[a-z]:)?[\\/]|\.{1,2}[\\/])\S+"),
    re.compile(r"\bbearer\s+[^\s,;]+"),
    re.compile(r"\bsk-[a-z0-9_-]+\b"),
    re.compile(r"\b[a-z0-9_]*(?:secret|token|api[_-]?key)[a-z0-9_]*\b"),
)
_ADVISOR_OVERCLAIM_TEXT_PATTERNS = (
    re.compile(r"p4 total elapsed reduction"),
    re.compile(r"llm improves parsing correctness"),
    re.compile(r"llm participates in proof digest generation"),
    re.compile(r"system is formally secure"),
)
_ADVISOR_FALLBACK_PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?[\\/]|\.{1,2}[\\/])\S+")
_ADVISOR_FALLBACK_PROOF_HASH_RE = re.compile(r"sha256:[^\s,;]+")


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _advisor_output_validation_error(payload: dict[str, Any]) -> str | None:
    for text in _iter_string_values(payload):
        lowered = str(text or "").strip().lower()
        if not lowered:
            continue
        if any(pattern.search(lowered) for pattern in _ADVISOR_OVERCLAIM_TEXT_PATTERNS):
            return "structured output contains forbidden claim text"
        if any(pattern.search(lowered) for pattern in _ADVISOR_UNSAFE_TEXT_PATTERNS):
            return "structured output contains unsafe text"
    return None


def _feature_snapshot_hash(features: dict[str, Any]) -> str:
    payload = json.dumps(features, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def coefficient_payload_checksum(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in dict(payload).items() if key != "model_checksum"}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def safe_runtime_action_space() -> list[str]:
    return list(SAFE_RUNTIME_ACTION_KINDS)


def _runtime_load_large_input_threshold_bytes(config: dict[str, Any] | None = None) -> int:
    value = _optional_int(dict(config or {}).get("runtime_load_large_input_threshold_bytes"))
    if value is None:
        return DEFAULT_RUNTIME_LOAD_LARGE_INPUT_THRESHOLD_BYTES
    return max(0, int(value))


def runtime_advisor_prior_bucket(features: dict[str, Any], *, config: dict[str, Any] | None = None) -> str:
    payload = dict(features or {})
    input_bytes = _optional_int(payload.get("input_bytes") or payload.get("source_bytes")) or 0
    sidecar_bytes = _optional_int(payload.get("sidecar_bytes") or payload.get("ticket_sidecar_bytes")) or 0
    peak_rss_mb = _optional_float(payload.get("peak_rss_mb"))
    ticket_present = bool(
        payload.get("ticket_present")
        or payload.get("ticket_validated")
        or payload.get("index_reused")
        or payload.get("sidecar_ticket_fast_path")
    )
    large_input = input_bytes >= _runtime_load_large_input_threshold_bytes(config)
    large_sidecar = sidecar_bytes >= DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES
    high_rss = peak_rss_mb is not None and peak_rss_mb >= 2048.0
    return "|".join(
        (
            f"ticket:{int(ticket_present)}",
            f"large_input:{int(large_input)}",
            f"large_sidecar:{int(large_sidecar)}",
            f"high_rss:{int(high_rss)}",
        )
    )


def safe_runtime_action_candidates(
    features: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    include_abstain: bool = True,
    telemetry_missing: bool = False,
) -> list[str]:
    payload = dict(features or {})
    input_bytes = _optional_int(payload.get("input_bytes") or payload.get("source_bytes")) or 0
    sidecar_bytes = _optional_int(payload.get("sidecar_bytes") or payload.get("ticket_sidecar_bytes")) or 0
    peak_rss_mb = _optional_float(payload.get("peak_rss_mb"))
    ticket_present = bool(
        payload.get("ticket_present")
        or payload.get("ticket_validated")
        or payload.get("index_reused")
        or payload.get("sidecar_ticket_fast_path")
    )
    large_input = input_bytes >= _runtime_load_large_input_threshold_bytes(config)
    large_sidecar = sidecar_bytes >= DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES
    high_rss = peak_rss_mb is not None and peak_rss_mb >= 2048.0
    action_kinds = ["baseline_full_load"]
    if large_input and not ticket_present:
        action_kinds.append("cold_preview")
    if ticket_present:
        action_kinds.append("sidecar_index_reuse")
    elif large_sidecar or large_input:
        action_kinds.append("sidecar_index_prebuild")
    if large_input and not ticket_present:
        action_kinds.append("deferred_index_build")
    if large_input or high_rss:
        action_kinds.append("streaming_package_write")
    if include_abstain and telemetry_missing and high_rss:
        action_kinds.insert(1, "abstain")
    elif include_abstain:
        action_kinds.append("abstain")
    return [action_kind for action_kind in dict.fromkeys(action_kinds) if action_kind in RUNTIME_ACTION_KINDS]


def _telemetry_missing(features: dict[str, Any]) -> bool:
    telemetry_summary = _as_mapping(dict(features or {}).get("risk_history_summary"))
    return (_optional_int(telemetry_summary.get("row_count")) or 0) <= 0


def _latest_telemetry(
    telemetry_history: Iterable[TelemetryRecord | TelemetryRecordUpdate | dict[str, Any]] | None,
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for item in list(telemetry_history or []):
        normalized = normalize_telemetry_history_item(item)
        if normalized is not None:
            latest = normalized
    return latest


def _risk_history_summary(
    telemetry_history: Iterable[TelemetryRecord | TelemetryRecordUpdate | dict[str, Any]] | None,
    *,
    max_rows: int,
) -> dict[str, Any]:
    normalized_rows = [
        normalized
        for item in list(telemetry_history or [])
        for normalized in [normalize_telemetry_history_item(item)]
        if normalized is not None
    ]
    recent_rows = normalized_rows[-max(1, int(max_rows or 1)) :]
    runtime_values = [
        value
        for row in recent_rows
        for value in [_optional_float(row.get("runtime_seconds"))]
        if value is not None
    ]
    rss_values = [
        value
        for row in recent_rows
        for value in [_optional_float(row.get("peak_rss_mb"))]
        if value is not None
    ]
    return {
        "row_count": len(normalized_rows),
        "recent_row_count": len(recent_rows),
        "max_runtime_seconds": max(runtime_values) if runtime_values else None,
        "max_peak_rss_mb": max(rss_values) if rss_values else None,
        "high_rss_rows": sum(1 for value in rss_values if value >= 2048.0),
        "index_reused_rows": sum(1 for row in recent_rows if bool(row.get("index_reused"))),
    }


@dataclass(frozen=True)
# ponytail: Phase 1 only defines the typed action payload shape; decision flow stays on legacy fields for now.
class RuntimeAction:
    action_id: str
    action_kind: str
    required_artifacts: list[str]
    expected_benefit: dict[str, Any]
    risk_level: str
    proof_scope_impact: str = "none"
    fallback_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_kind": self.action_kind,
            "required_artifacts": list(self.required_artifacts),
            "expected_benefit": dict(self.expected_benefit),
            "risk_level": self.risk_level,
            "proof_scope_impact": self.proof_scope_impact,
            "fallback_action": self.fallback_action,
        }


def _serialize_runtime_action(value: RuntimeAction | dict[str, Any]) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    return dict(value)


def _runtime_action_from_payload(value: RuntimeAction | dict[str, Any]) -> RuntimeAction:
    if isinstance(value, RuntimeAction):
        return value
    payload = dict(value)
    return RuntimeAction(
        action_id=str(payload["action_id"]),
        action_kind=str(payload["action_kind"]),
        required_artifacts=[str(item) for item in list(payload.get("required_artifacts") or [])],
        expected_benefit=dict(payload.get("expected_benefit") or {}),
        risk_level=str(payload["risk_level"]),
        proof_scope_impact=str(payload.get("proof_scope_impact") or "none"),
        fallback_action=None if payload.get("fallback_action") is None else str(payload["fallback_action"]),
    )


def _runtime_actions_from_payload(value: Any) -> list[RuntimeAction]:
    return [_runtime_action_from_payload(item) for item in list(value or [])]


def _action_kinds(actions: Iterable[RuntimeAction]) -> list[str]:
    return [str(action.action_kind) for action in list(actions)]


def _derive_legacy_advisor_fields(actions: Iterable[RuntimeAction]) -> dict[str, Any]:
    kinds = set(_action_kinds(actions))
    recommend_index_prebuild = "sidecar_index_prebuild" in kinds
    recommend_index_reuse_attempt = "sidecar_index_reuse" in kinds
    recommend_streaming_write = "streaming_package_write" in kinds
    recommended_schedule = (
        "sidecar_first"
        if recommend_index_prebuild or recommend_index_reuse_attempt
        else "serial_safe"
    )
    return {
        "recommend_index_prebuild": recommend_index_prebuild,
        "recommend_index_reuse_attempt": recommend_index_reuse_attempt,
        "recommend_streaming_write": recommend_streaming_write,
        "recommended_schedule": recommended_schedule,
    }


def _append_runtime_action(
    actions: list[RuntimeAction],
    *,
    action_kind: str,
    risk_level: str,
    required_artifacts: list[str] | None = None,
    notes: list[str] | None = None,
    fallback_action: str | None = None,
) -> None:
    if any(item.action_kind == action_kind for item in actions):
        return
    actions.append(
        RuntimeAction(
            action_id=f"runtime-action:{action_kind}",
            action_kind=action_kind,
            required_artifacts=list(required_artifacts or []),
            expected_benefit={
                "runtime_seconds_delta": None,
                "peak_rss_mb_delta": None,
                "notes": list(notes or []),
            },
            risk_level=risk_level if risk_level in RISK_LEVELS else "low",
            fallback_action=fallback_action,
        )
    )


def _ensure_abstain_action(
    actions: list[RuntimeAction],
    *,
    notes: list[str] | None = None,
    required_artifacts: list[str] | None = None,
) -> list[RuntimeAction]:
    updated = list(actions)
    _append_runtime_action(
        updated,
        action_kind="abstain",
        risk_level="high",
        required_artifacts=required_artifacts,
        notes=notes,
    )
    return updated


@dataclass(frozen=True)
class AdvisorDecision:
    decision_version: str
    advisor_mode: str
    model_ref: str
    model_checksum: str | None
    feature_snapshot_hash: str
    recommend_index_prebuild: bool
    recommend_index_reuse_attempt: bool
    recommend_streaming_write: bool
    recommended_schedule: str
    predicted_runtime_seconds: float | None
    predicted_peak_rss_mb: float | None
    risk_level: str
    reasons: list[str]
    proposed_actions: list[RuntimeAction] = field(default_factory=list)
    abstained: bool = False
    abstain_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_version": self.decision_version,
            "advisor_mode": self.advisor_mode,
            "model_ref": self.model_ref,
            "model_checksum": self.model_checksum,
            "feature_snapshot_hash": self.feature_snapshot_hash,
            "recommend_index_prebuild": bool(self.recommend_index_prebuild),
            "recommend_index_reuse_attempt": bool(self.recommend_index_reuse_attempt),
            "recommend_streaming_write": bool(self.recommend_streaming_write),
            "recommended_schedule": self.recommended_schedule,
            "predicted_runtime_seconds": self.predicted_runtime_seconds,
            "predicted_peak_rss_mb": self.predicted_peak_rss_mb,
            "risk_level": self.risk_level,
            "reasons": list(self.reasons),
            "proposed_actions": [_serialize_runtime_action(item) for item in list(self.proposed_actions)],
            "abstained": bool(self.abstained),
            "abstain_reason": self.abstain_reason,
        }


@dataclass(frozen=True)
class AdvisorTrace:
    trace_version: str
    generated_at: str
    request_id: str
    telemetry_refs: list[str]
    feature_snapshot: dict[str, Any]
    decision: AdvisorDecision
    gate_result: dict[str, Any] | None
    advisor_overhead_seconds: float
    agent_contract_ref: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_version": self.trace_version,
            "generated_at": self.generated_at,
            "request_id": self.request_id,
            "telemetry_refs": list(self.telemetry_refs),
            "feature_snapshot": dict(self.feature_snapshot),
            "decision": self.decision.to_dict(),
            "gate_result": self.gate_result,
            "advisor_overhead_seconds": float(self.advisor_overhead_seconds),
            "agent_contract_ref": self.agent_contract_ref,
        }


@dataclass(frozen=True)
class AdvisorEvidenceContext:
    context_version: str
    advisor_phase: str
    input_bytes: int | None
    sidecar_bytes: int | None
    sidecar_row_count: int | None
    platform: str | None
    export_family: str | None
    embodiment_mode: str | None
    ticket_present: bool
    ticket_validated: bool | None
    trace_checksum_prefix: str | None = None
    dictionary_checksum_prefix: str | None = None
    gate_policy_summary: dict[str, Any] = field(default_factory=dict)
    retrieved_case_refs: list[str] = field(default_factory=list)
    case_similarity_features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_version": self.context_version,
            "advisor_phase": self.advisor_phase,
            "input_bytes": self.input_bytes,
            "sidecar_bytes": self.sidecar_bytes,
            "sidecar_row_count": self.sidecar_row_count,
            "platform": self.platform,
            "export_family": self.export_family,
            "embodiment_mode": self.embodiment_mode,
            "ticket_present": bool(self.ticket_present),
            "ticket_validated": self.ticket_validated,
            "trace_checksum_prefix": self.trace_checksum_prefix,
            "dictionary_checksum_prefix": self.dictionary_checksum_prefix,
            "gate_policy_summary": dict(self.gate_policy_summary),
            "retrieved_case_refs": list(self.retrieved_case_refs),
            "case_similarity_features": dict(self.case_similarity_features),
        }


def _checksum_prefix(value: Any, *, keep: int = 16) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if ":" in text:
        _prefix, _sep, suffix = text.partition(":")
        text = suffix or text
    return text[: max(8, int(keep or 8))]


def _advisor_evidence_context_from_features(
    features: dict[str, Any],
    *,
    retrieved_case_refs: list[str] | None = None,
    case_similarity_features: dict[str, Any] | None = None,
) -> AdvisorEvidenceContext:
    payload = dict(features or {})
    similarity_features = dict(case_similarity_features or build_case_similarity_features(payload))
    ticket_validated = payload.get("ticket_validated")
    sidecar_bytes = _optional_int(payload.get("sidecar_bytes"))
    if sidecar_bytes is None:
        sidecar_bytes = _optional_int(payload.get("ticket_sidecar_bytes"))
    sidecar_row_count = _optional_int(payload.get("sidecar_row_count"))
    if sidecar_row_count is None:
        sidecar_row_count = _optional_int(payload.get("ticket_row_count"))
    return AdvisorEvidenceContext(
        context_version=ADVISOR_EVIDENCE_CONTEXT_VERSION,
        advisor_phase=str(payload.get("advisor_phase") or ""),
        input_bytes=_optional_int(payload.get("input_bytes")),
        sidecar_bytes=sidecar_bytes,
        sidecar_row_count=sidecar_row_count,
        platform=None if payload.get("platform") is None else str(payload.get("platform")),
        export_family=None if payload.get("export_family") is None else str(payload.get("export_family")),
        embodiment_mode=None if payload.get("embodiment_mode") is None else str(payload.get("embodiment_mode")),
        ticket_present=bool(payload.get("ticket_present")),
        ticket_validated=None if ticket_validated is None else bool(ticket_validated),
        trace_checksum_prefix=_checksum_prefix(payload.get("trace_checksum")),
        dictionary_checksum_prefix=_checksum_prefix(payload.get("dictionary_checksum")),
        gate_policy_summary=dict(payload.get("gate_policy_summary") or {}),
        retrieved_case_refs=list(retrieved_case_refs or []),
        case_similarity_features=similarity_features,
    )


def _retrieval_metadata(
    context: AdvisorEvidenceContext,
    retrieval_summary: dict[str, Any],
    *,
    applied_abstain_reason: str | None,
) -> dict[str, Any]:
    return {
        "retrieved_case_refs": list(context.retrieved_case_refs),
        "case_similarity_features": dict(context.case_similarity_features),
        "matched_case_count": int(retrieval_summary.get("matched_case_count") or 0),
        "similar_case_count": int(retrieval_summary.get("similar_case_count") or 0),
        "has_sufficient_similarity": bool(retrieval_summary.get("has_sufficient_similarity")),
        "conflicting_actions": list(retrieval_summary.get("conflicting_actions") or []),
        "unsafe_case_match": bool(retrieval_summary.get("unsafe_case_match")),
        "top_score": int(retrieval_summary.get("top_score") or 0),
        "reject_taxonomy_coverage": float(retrieval_summary.get("reject_taxonomy_coverage") or 0.0),
        "missing_telemetry_fields": list(context.case_similarity_features.get("missing_telemetry_fields") or []),
        "applied_abstain_reason": applied_abstain_reason,
    }


@dataclass(frozen=True)
class RuntimeLoadPlan:
    plan_version: str
    load_mode: str
    index_build_mode: str
    materialize_event_stream: bool
    try_parser_artifact_reuse: bool
    try_sidecar_index_reuse: bool
    background_sidecar_prebuild: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "load_mode": self.load_mode,
            "index_build_mode": self.index_build_mode,
            "materialize_event_stream": bool(self.materialize_event_stream),
            "try_parser_artifact_reuse": bool(self.try_parser_artifact_reuse),
            "try_sidecar_index_reuse": bool(self.try_sidecar_index_reuse),
            "background_sidecar_prebuild": bool(self.background_sidecar_prebuild),
            "reasons": list(self.reasons),
        }


class RuntimeOptimizationAdvisor:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.last_advisor_metadata: dict[str, Any] = {}
        self.last_evidence_context: dict[str, Any] = {}

    def plan_load(
        self,
        *,
        current_request_features: dict[str, Any] | None = None,
    ) -> RuntimeLoadPlan:
        features = dict(current_request_features or {})
        input_bytes = _optional_int(features.get("input_bytes") or features.get("source_bytes")) or 0
        large_input_threshold_bytes = _optional_int(self.config.get("runtime_load_large_input_threshold_bytes"))
        if large_input_threshold_bytes is None:
            large_input_threshold_bytes = 256 * 1024 * 1024

        if input_bytes >= max(0, large_input_threshold_bytes):
            return RuntimeLoadPlan(
                plan_version=RUNTIME_LOAD_PLAN_VERSION,
                load_mode="cold_preview",
                index_build_mode="minimal",
                materialize_event_stream=False,
                try_parser_artifact_reuse=False,
                try_sidecar_index_reuse=False,
                background_sidecar_prebuild=True,
                reasons=["large input prefers cold preview load"],
            )

        return RuntimeLoadPlan(
            plan_version=RUNTIME_LOAD_PLAN_VERSION,
            load_mode="full",
            index_build_mode="full",
            materialize_event_stream=True,
            try_parser_artifact_reuse=False,
            try_sidecar_index_reuse=False,
            background_sidecar_prebuild=False,
            reasons=["small input keeps full materialized load"],
        )

    def plan_load_result(
        self,
        *,
        current_request_features: dict[str, Any] | None = None,
        job_id: str | None = None,
        request_id: str | None = None,
    ) -> Result[dict[str, Any]]:
        effective_job_id = (
            job_id
            or request_id
            or self.config.get("job_id")
            or self.config.get("request_id")
            or "runtime-advisor:plan_load"
        )
        contract = AgentJobContract(
            agent_name="RuntimeOptimizationAdvisor",
            job_id=str(effective_job_id),
        )
        contract.transition(AGENT_QUEUED)
        try:
            contract.transition(AGENT_VALIDATING_INPUT)
            contract.transition(AGENT_RUNNING)
            plan = self.plan_load(current_request_features=current_request_features)
            contract.complete(
                load_mode=plan.load_mode,
                index_build_mode=plan.index_build_mode,
                materialize_event_stream=plan.materialize_event_stream,
            )
            return ok_result(
                {
                    "runtime_load_plan": plan,
                    "agent_contract": contract.to_dict(),
                    "advisor_metadata": dict(self.last_advisor_metadata),
                }
            )
        except Exception as exc:
            contract.fail(ERR_ADVISOR_UNAVAILABLE, str(exc))
            return Result(
                code=ERR_ADVISOR_UNAVAILABLE,
                message=str(exc),
                data={
                    "runtime_load_plan": None,
                    "agent_contract": contract.to_dict(),
                    "advisor_metadata": dict(self.last_advisor_metadata),
                },
            )

    def evaluate(
        self,
        *,
        telemetry_history: Iterable[TelemetryRecord | TelemetryRecordUpdate | dict[str, Any]] | None = None,
        current_request_features: dict[str, Any] | None = None,
        sidecar_ticket: SidecarIndexTicket | dict[str, Any] | None = None,
    ) -> AdvisorDecision:
        self.last_advisor_metadata = {}
        self.last_evidence_context = {}
        mode = str(self.config.get("advisor_mode") or self.config.get("mode") or "disabled").strip() or "disabled"
        if mode not in ADVISOR_MODES:
            mode = "disabled"
        history_items = list(telemetry_history or [])
        latest = _latest_telemetry(history_items)
        ticket_payload = _as_mapping(sidecar_ticket)
        max_history_rows = _optional_int(self.config.get("openai_max_history_rows")) or 20
        request_features = {**latest, **dict(current_request_features or {})}
        ticket_validated = request_features.get("ticket_validated")
        if ticket_validated is None and ticket_payload:
            ticket_validated = True
        features = {
            **request_features,
            "ticket_present": bool(ticket_payload),
            "ticket_validated": ticket_validated,
            "ticket_row_count": ticket_payload.get("row_count"),
            "ticket_sidecar_bytes": ticket_payload.get("sidecar_bytes"),
            "risk_history_summary": _risk_history_summary(history_items, max_rows=max_history_rows),
        }
        snapshot_hash = _feature_snapshot_hash(features)
        if mode == "disabled":
            decision = AdvisorDecision(
                decision_version=ADVISOR_DECISION_VERSION,
                advisor_mode="disabled",
                model_ref="disabled",
                model_checksum=None,
                feature_snapshot_hash=snapshot_hash,
                recommend_index_prebuild=False,
                recommend_index_reuse_attempt=False,
                recommend_streaming_write=False,
                recommended_schedule="serial_safe",
                predicted_runtime_seconds=None,
                predicted_peak_rss_mb=None,
                risk_level="low",
                reasons=["advisor disabled"],
                proposed_actions=[],
                abstained=False,
                abstain_reason=None,
            )
        elif mode == "offline_coefficients":
            decision = self._evaluate_offline_coefficients(features, snapshot_hash)
        elif mode == "openai_structured":
            decision = self._evaluate_openai_structured(features, snapshot_hash)
        else:
            decision = self._evaluate_heuristic(features, snapshot_hash)
        decision, evidence_context, retrieval_metadata = self._apply_retrieval_fail_closed_policy(
            decision=decision,
            features=features,
        )
        final_snapshot_hash = _feature_snapshot_hash(
            {
                **features,
                "evidence_context": evidence_context.to_dict(),
            }
        )
        if decision.feature_snapshot_hash != final_snapshot_hash:
            decision = replace(decision, feature_snapshot_hash=final_snapshot_hash)
        self.last_evidence_context = evidence_context.to_dict()
        self.last_advisor_metadata = {
            **dict(self.last_advisor_metadata),
            "retrieval": retrieval_metadata,
        }
        return decision

    def evaluate_result(
        self,
        *,
        telemetry_history: Iterable[TelemetryRecord | TelemetryRecordUpdate | dict[str, Any]] | None = None,
        current_request_features: dict[str, Any] | None = None,
        sidecar_ticket: SidecarIndexTicket | dict[str, Any] | None = None,
        job_id: str | None = None,
        request_id: str | None = None,
    ) -> Result[dict[str, Any]]:
        effective_job_id = (
            job_id
            or request_id
            or self.config.get("job_id")
            or self.config.get("request_id")
            or "runtime-advisor:evaluate"
        )
        contract = AgentJobContract(
            agent_name="RuntimeOptimizationAdvisor",
            job_id=str(effective_job_id),
        )
        contract.transition(AGENT_QUEUED)
        try:
            contract.transition(AGENT_VALIDATING_INPUT)
            contract.transition(AGENT_RUNNING)
            decision = self.evaluate(
                telemetry_history=telemetry_history,
                current_request_features=current_request_features,
                sidecar_ticket=sidecar_ticket,
            )
            contract.complete(
                advisor_mode=decision.advisor_mode,
                recommended_schedule=decision.recommended_schedule,
                risk_level=decision.risk_level,
            )
            return ok_result(
                {
                    "advisor_decision": decision,
                    "agent_contract": contract.to_dict(),
                    "advisor_metadata": dict(self.last_advisor_metadata),
                    "advisor_evidence_context": dict(self.last_evidence_context),
                }
            )
        except Exception as exc:
            contract.fail(ERR_ADVISOR_UNAVAILABLE, str(exc))
            return Result(
                code=ERR_ADVISOR_UNAVAILABLE,
                message=str(exc),
                data={
                    "advisor_decision": None,
                    "agent_contract": contract.to_dict(),
                    "advisor_metadata": dict(self.last_advisor_metadata),
                    "advisor_evidence_context": dict(self.last_evidence_context),
                },
            )

    def _case_bank_root(self) -> Path:
        root = self.config.get("advisor_case_bank_root")
        if root:
            return Path(str(root)).expanduser().resolve()
        return default_case_bank_root().resolve()

    def _retrieval_summary_for_decision(
        self,
        *,
        features: dict[str, Any],
        decision: AdvisorDecision,
    ) -> tuple[AdvisorEvidenceContext, dict[str, Any]]:
        summary = retrieve_runtime_cases(
            current_features=features,
            proposed_actions=decision.proposed_actions,
            case_bank_root=self._case_bank_root(),
            top_k=_optional_int(self.config.get("advisor_retrieval_top_k")) or 3,
            similarity_threshold=_optional_int(self.config.get("advisor_similarity_threshold")) or 14,
        ).to_dict()
        context = _advisor_evidence_context_from_features(
            features,
            retrieved_case_refs=list(summary.get("retrieved_case_refs") or []),
            case_similarity_features=dict(summary.get("case_similarity_features") or {}),
        )
        return context, summary

    def _apply_retrieval_fail_closed_policy(
        self,
        *,
        decision: AdvisorDecision,
        features: dict[str, Any],
    ) -> tuple[AdvisorDecision, AdvisorEvidenceContext, dict[str, Any]]:
        evidence_context, retrieval_summary = self._retrieval_summary_for_decision(
            features=features,
            decision=decision,
        )
        actions = list(decision.proposed_actions)
        high_risk_actions = [
            action
            for action in actions
            if action.risk_level in HIGH_RISK_ACTION_LEVELS and action.action_kind in RUNTIME_ACTION_KINDS
        ]
        concrete_confident_action_present = any(
            action.action_kind in {"cold_preview", "sidecar_index_prebuild", "sidecar_index_reuse"}
            for action in actions
        )
        missing_telemetry_fields = list(evidence_context.case_similarity_features.get("missing_telemetry_fields") or [])
        matched_labels = {str(label) for label in list(retrieval_summary.get("matched_labels") or []) if str(label)}
        needs_more_data_signal = bool(matched_labels) and matched_labels.issubset({"abstain", "needs_more_data"})
        applied_abstain_reason: str | None = None
        filtered_actions = list(actions)
        if high_risk_actions and missing_telemetry_fields and needs_more_data_signal and not concrete_confident_action_present:
            filtered_actions = _ensure_abstain_action(
                [],
                required_artifacts=["telemetry_history"],
                notes=["retrieval grounding requested more telemetry before a high-risk action"],
            )
            applied_abstain_reason = "need_more_telemetry"
        elif high_risk_actions and missing_telemetry_fields and not concrete_confident_action_present:
            filtered_actions = _ensure_abstain_action(
                [],
                required_artifacts=["telemetry_history"],
                notes=["retrieval fail-closed: high-risk recommendation lacks telemetry evidence"],
            )
            applied_abstain_reason = "need_more_telemetry"
        elif high_risk_actions and bool(retrieval_summary.get("unsafe_case_match")):
            filtered_actions = [action for action in filtered_actions if action.risk_level not in HIGH_RISK_ACTION_LEVELS]
            applied_abstain_reason = "unsafe_case_match"
        elif high_risk_actions and list(retrieval_summary.get("conflicting_actions") or []):
            filtered_actions = [action for action in filtered_actions if action.risk_level not in HIGH_RISK_ACTION_LEVELS]
            applied_abstain_reason = "conflicting_evidence"
        elif high_risk_actions and not bool(retrieval_summary.get("has_sufficient_similarity")):
            filtered_actions = [action for action in filtered_actions if action.risk_level not in HIGH_RISK_ACTION_LEVELS]
            applied_abstain_reason = "insufficient_similar_case"
        if applied_abstain_reason is None:
            return decision, evidence_context, _retrieval_metadata(
                evidence_context,
                retrieval_summary,
                applied_abstain_reason=None,
            )
        if not filtered_actions:
            filtered_actions = _ensure_abstain_action(
                [],
                required_artifacts=["telemetry_history"] if applied_abstain_reason == "need_more_telemetry" else None,
                notes=["retrieval fail-closed: telemetry is insufficient"] if applied_abstain_reason == "need_more_telemetry" else [f"retrieval fail-closed: {applied_abstain_reason}"],
            )
        legacy_fields = _derive_legacy_advisor_fields(filtered_actions)
        reasons = [reason for reason in list(decision.reasons) if reason != decision.abstain_reason]
        if applied_abstain_reason not in reasons:
            reasons.append(applied_abstain_reason)
        updated = replace(
            decision,
            recommend_index_prebuild=bool(legacy_fields["recommend_index_prebuild"]),
            recommend_index_reuse_attempt=bool(legacy_fields["recommend_index_reuse_attempt"]),
            recommend_streaming_write=bool(legacy_fields["recommend_streaming_write"]),
            recommended_schedule=str(legacy_fields["recommended_schedule"]),
            reasons=reasons,
            proposed_actions=filtered_actions,
            abstained=True,
            abstain_reason=applied_abstain_reason,
        )
        return updated, evidence_context, _retrieval_metadata(
            evidence_context,
            retrieval_summary,
            applied_abstain_reason=applied_abstain_reason,
        )

    def _evaluate_heuristic(self, features: dict[str, Any], snapshot_hash: str) -> AdvisorDecision:
        sidecar_bytes = _optional_int(features.get("sidecar_bytes") or features.get("ticket_sidecar_bytes")) or 0
        input_bytes = _optional_int(features.get("input_bytes")) or 0
        sidecar_row_count = _optional_int(features.get("sidecar_row_count") or features.get("ticket_row_count")) or 0
        previous_runtime = _optional_float(features.get("runtime_seconds"))
        previous_peak_rss = _optional_float(features.get("peak_rss_mb"))
        ticket_present = bool(features.get("ticket_present"))
        telemetry_missing = _telemetry_missing(features)

        large_input_threshold_bytes = _runtime_load_large_input_threshold_bytes(self.config)
        large_input = input_bytes >= max(0, large_input_threshold_bytes)
        large_sidecar = sidecar_bytes >= DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES
        recommend_index_reuse = ticket_present
        recommend_index_prebuild = (not ticket_present) and (large_sidecar or large_input)
        recommend_streaming_write = large_input or (previous_peak_rss is not None and previous_peak_rss >= 2048.0)
        recommended_schedule = "sidecar_first" if recommend_index_prebuild or large_sidecar else "serial_safe"

        predicted_runtime = previous_runtime
        if predicted_runtime is None:
            predicted_runtime = 0.0
            if input_bytes:
                predicted_runtime += max(1.0, input_bytes / (64 * 1024 * 1024))
            if sidecar_bytes:
                predicted_runtime += max(1.0, sidecar_bytes / (128 * 1024 * 1024))
            if sidecar_row_count:
                predicted_runtime += min(600.0, max(1.0, sidecar_row_count / 50000.0))

        predicted_peak_rss = previous_peak_rss
        if predicted_peak_rss is None:
            predicted_peak_rss = 256.0
            if input_bytes:
                predicted_peak_rss += input_bytes / (8 * 1024 * 1024)
            if sidecar_bytes:
                predicted_peak_rss += min(4096.0, sidecar_bytes / (16 * 1024 * 1024))

        risk_level = "low"
        reasons: list[str] = []
        if large_sidecar:
            risk_level = "high"
            reasons.append("large sidecar should avoid repeated stream validation")
        elif sidecar_bytes > 0:
            risk_level = "medium"
            reasons.append("sidecar cost is present")
        if recommend_streaming_write:
            if risk_level in {"low", "medium"}:
                risk_level = "high"
            reasons.append("input or RSS suggests streaming write")
        if ticket_present:
            reasons.append("sidecar index ticket is available")
        if not reasons:
            reasons.append("small deterministic baseline path")

        proposed_actions: list[RuntimeAction] = []
        if large_input and not ticket_present:
            _append_runtime_action(
                proposed_actions,
                action_kind="cold_preview",
                risk_level=risk_level,
                notes=["large input without ticket prefers cold preview load"],
            )
        if recommend_index_prebuild:
            _append_runtime_action(
                proposed_actions,
                action_kind="sidecar_index_prebuild",
                risk_level=risk_level,
                notes=["missing reusable ticket keeps sidecar prebuild on the critical path"],
            )
        if recommend_index_reuse:
            _append_runtime_action(
                proposed_actions,
                action_kind="sidecar_index_reuse",
                risk_level=risk_level,
                required_artifacts=["sidecar_index_ticket"],
                notes=["valid ticket can skip repeated sidecar validation work"],
            )
        if recommend_streaming_write:
            _append_runtime_action(
                proposed_actions,
                action_kind="streaming_package_write",
                risk_level=risk_level,
                notes=["RSS pressure or large output suggests streaming package writes"],
            )

        concrete_confident_action_present = any(
            item.action_kind in {"cold_preview", "sidecar_index_prebuild", "sidecar_index_reuse"}
            for item in proposed_actions
        )
        abstained = False
        abstain_reason: str | None = None
        if telemetry_missing and (previous_peak_rss is not None and previous_peak_rss >= 2048.0) and not concrete_confident_action_present:
            proposed_actions = _ensure_abstain_action(
                [],
                required_artifacts=["telemetry_history"],
                notes=["high-risk RSS path lacks telemetry history confidence"],
            )
            abstained = True
            abstain_reason = "need_more_telemetry"
            reasons.append("telemetry history is missing for the high-risk RSS path")
            recommend_index_prebuild = False
            recommend_index_reuse = False
            recommend_streaming_write = False
            recommended_schedule = "serial_safe"

        return AdvisorDecision(
            decision_version=ADVISOR_DECISION_VERSION,
            advisor_mode="heuristic",
            model_ref=HEURISTIC_MODEL_REF,
            model_checksum=None,
            feature_snapshot_hash=snapshot_hash,
            recommend_index_prebuild=bool(recommend_index_prebuild),
            recommend_index_reuse_attempt=bool(recommend_index_reuse),
            recommend_streaming_write=bool(recommend_streaming_write),
            recommended_schedule=recommended_schedule,
            predicted_runtime_seconds=round(float(predicted_runtime), 6),
            predicted_peak_rss_mb=round(float(predicted_peak_rss), 3),
            risk_level=risk_level if risk_level in RISK_LEVELS else "low",
            reasons=reasons,
            proposed_actions=proposed_actions,
            abstained=abstained,
            abstain_reason=abstain_reason,
        )

    def _evaluate_heuristic_fallback(self, features: dict[str, Any], snapshot_hash: str, reason: str) -> AdvisorDecision:
        base = self._evaluate_heuristic(features, snapshot_hash)
        return replace(base, reasons=[*base.reasons, reason])

    def _evaluate_offline_coefficients(self, features: dict[str, Any], snapshot_hash: str) -> AdvisorDecision:
        coefficients_path = self.config.get("coefficients_path")
        if not coefficients_path:
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients path is not configured",
            )
        try:
            with Path(str(coefficients_path)).expanduser().open("r", encoding="utf-8") as handle:
                coefficients = json.load(handle)
            if not isinstance(coefficients, dict):
                raise ValueError("coefficients payload must be an object")
        except (OSError, ValueError, json.JSONDecodeError):
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients payload is unavailable",
            )
        if "artifact_version" not in coefficients and any(key in coefficients for key in ("runtime_weights", "runtime_intercept")):
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients payload is legacy",
            )
        if coefficients.get("status") != "trained":
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients payload is not trained",
            )
        if str(coefficients.get("artifact_version") or "") != OFFLINE_ADVISOR_PRIOR_VERSION:
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients artifact version mismatch",
            )
        if not str(coefficients.get("model_ref") or "").strip():
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients model_ref is missing",
            )
        if not str(coefficients.get("created_at") or "").strip() or not str(coefficients.get("telemetry_source_digest") or "").strip():
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients payload is legacy",
            )
        if not isinstance(coefficients.get("training_input_summary"), dict):
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients payload is legacy",
            )
        expected_safe_actions = safe_runtime_action_space()
        observed_safe_actions = [str(item) for item in list(coefficients.get("safe_action_space") or [])]
        if observed_safe_actions != expected_safe_actions:
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients safe action mismatch",
            )
        model_checksum = coefficient_payload_checksum(coefficients)
        expected_checksum = str(coefficients.get("model_checksum") or "").strip()
        if not expected_checksum or expected_checksum != model_checksum:
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients checksum mismatch",
            )
        base = self._evaluate_heuristic(features, snapshot_hash)
        telemetry_missing = _telemetry_missing(features)
        candidate_kinds = safe_runtime_action_candidates(
            features,
            config=self.config,
            telemetry_missing=telemetry_missing,
        )
        global_scores = dict(coefficients.get("global_action_scores") or {})
        bucket_key = runtime_advisor_prior_bucket(features, config=self.config)
        bucket_payload = dict(dict(coefficients.get("buckets") or {}).get(bucket_key) or {})
        bucket_scores = dict(bucket_payload.get("action_scores") or {})
        unknown_actions = sorted(
            {
                str(action_kind)
                for action_kind in [*global_scores.keys(), *bucket_scores.keys()]
                if str(action_kind) not in expected_safe_actions
            }
        )
        if unknown_actions:
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients unknown action",
            )
        scored_candidates = sorted(
            (
                action_kind
                for action_kind in candidate_kinds
                if action_kind not in {"baseline_full_load", "abstain"}
            ),
            key=lambda action_kind: (
                -(float(bucket_scores.get(action_kind) or 0.0)),
                -(float(global_scores.get(action_kind) or 0.0)),
                candidate_kinds.index(action_kind),
            ),
        )
        action_by_kind = {action.action_kind: action for action in list(base.proposed_actions)}
        ranked_actions: list[RuntimeAction] = []
        if base.abstained:
            ranked_actions = _ensure_abstain_action(
                [],
                required_artifacts=["telemetry_history"] if base.abstain_reason == "need_more_telemetry" else None,
                notes=["offline safe prior preserved heuristic abstain"],
            )
        else:
            for action_kind in scored_candidates:
                existing = action_by_kind.get(action_kind)
                if existing is not None:
                    ranked_actions.append(existing)
                    continue
                required_artifacts = ["sidecar_index_ticket"] if action_kind == "sidecar_index_reuse" else []
                _append_runtime_action(
                    ranked_actions,
                    action_kind=action_kind,
                    risk_level=base.risk_level,
                    required_artifacts=required_artifacts,
                    notes=["offline safe prior ranked this action inside the deterministic candidate set"],
                )
        runtime = _optional_float(bucket_payload.get("mean_runtime_seconds")) or base.predicted_runtime_seconds
        model_ref = str(coefficients.get("model_ref") or OFFLINE_ADVISOR_MODEL_REF)
        return replace(
            base,
            advisor_mode="offline_coefficients",
            model_ref=model_ref,
            model_checksum=model_checksum,
            reasons=[*list(base.reasons), f"offline safe prior bucket={bucket_key}"],
            predicted_runtime_seconds=None if runtime is None else round(float(runtime), 6),
            proposed_actions=ranked_actions,
        )

    def _evaluate_openai_structured(self, features: dict[str, Any], snapshot_hash: str) -> AdvisorDecision:
        schema = dict(
            self.config.get("openai_action_set_schema")
            or self.config.get("openai_decision_schema")
            or load_schema("runtime_action_set.schema.json")
        )
        client_for_metadata = None
        try:
            heuristic_base = self._evaluate_heuristic(features, snapshot_hash)
            preliminary_context, _preliminary_summary = self._retrieval_summary_for_decision(
                features=features,
                decision=heuristic_base,
            )
            features_with_context = {
                **features,
                "evidence_context": preliminary_context.to_dict(),
            }
            snapshot_hash = _feature_snapshot_hash(features_with_context)
            heuristic_base = replace(heuristic_base, feature_snapshot_hash=snapshot_hash)
            client = self.config.get("llm_client") or self.config.get("openai_client")
            if client is None:
                client = LLMAdvisorClient.from_config(self.config)
            client_for_metadata = client
            result = client.request_advisor_decision(
                feature_payload=features_with_context,
                decision_schema=schema,
                feature_snapshot_hash=snapshot_hash,
                decision_version=RUNTIME_ACTION_SET_VERSION,
            )
            payload = dict(result.decision_payload)
            schema_reason = validate_schema(schema, payload)
            if schema_reason is not None:
                raise LLMAdvisorClientError("openai_schema_invalid", schema_reason)
            unsafe_reason = _advisor_output_validation_error(payload)
            if unsafe_reason is not None:
                raise LLMAdvisorClientError("openai_unsafe_output", unsafe_reason)
            proposed_actions = _runtime_actions_from_payload(payload.get("proposed_actions"))
            abstained = bool(payload.get("abstained"))
            abstain_reason = None if payload.get("abstain_reason") is None else str(payload["abstain_reason"])
            safe_candidates = set(
                safe_runtime_action_candidates(
                    features,
                    config=self.config,
                    telemetry_missing=_telemetry_missing(features),
                )
            )
            for action in proposed_actions:
                if action.action_kind not in RUNTIME_ACTION_KINDS:
                    raise LLMAdvisorClientError("openai_unknown_action", f"unknown action {action.action_kind}")
                if action.action_kind not in safe_candidates:
                    raise LLMAdvisorClientError(
                        "openai_safe_action_mismatch",
                        f"action {action.action_kind} is outside safe candidate set",
                    )
            if abstained:
                proposed_actions = _ensure_abstain_action(
                    [],
                    required_artifacts=["telemetry_history"] if abstain_reason == "need_more_telemetry" else None,
                    notes=["online advisor abstained inside the deterministic candidate set"],
                )
            legacy_fields = _derive_legacy_advisor_fields(proposed_actions)
            reasons = list(heuristic_base.reasons)
            if abstained and abstain_reason and abstain_reason not in reasons:
                reasons.append(abstain_reason)
            self.last_advisor_metadata = {
                "openai_latency_seconds": result.latency_seconds,
                "openai_tokens": _openai_usage_token_count(result.usage),
                "fallback_reason": None,
                "openai_response_id": result.response_id,
                "openai_model": result.model,
                "llm_backend": getattr(result, "backend", None),
                "llm_provider": getattr(client_for_metadata, "provider", None) or self.config.get("llm_provider"),
            }
            return replace(
                heuristic_base,
                advisor_mode="openai_structured",
                model_ref=str(result.model or self.config.get("llm_model") or self.config.get("openai_model") or "openai_structured"),
                model_checksum=None,
                recommend_index_prebuild=bool(legacy_fields["recommend_index_prebuild"]),
                recommend_index_reuse_attempt=bool(legacy_fields["recommend_index_reuse_attempt"]),
                recommend_streaming_write=bool(legacy_fields["recommend_streaming_write"]),
                recommended_schedule=str(legacy_fields["recommended_schedule"]),
                reasons=reasons,
                proposed_actions=proposed_actions,
                abstained=abstained,
                abstain_reason=abstain_reason,
            )
        except LLMAdvisorClientError as exc:
            self.last_advisor_metadata = _fallback_llm_metadata(
                self.config,
                client_for_metadata,
                fallback_reason=exc.reason,
                fallback_message=str(exc),
                fallback_exception_type=type(exc).__name__,
            )
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                f"openai fallback: {exc.reason}",
            )
        except Exception as exc:
            self.last_advisor_metadata = _fallback_llm_metadata(
                self.config,
                client_for_metadata,
                fallback_reason="openai_refused_or_incomplete",
                fallback_message=str(exc),
                fallback_exception_type=type(exc).__name__,
            )
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "openai fallback: openai_refused_or_incomplete",
            )


def _openai_usage_token_count(usage: dict[str, Any]) -> int | None:
    for key in ("total_tokens", "total_token_count"):
        value = _optional_int(usage.get(key))
        if value is not None:
            return value
    input_tokens = _optional_int(usage.get("input_tokens"))
    output_tokens = _optional_int(usage.get("output_tokens"))
    if input_tokens is None and output_tokens is None:
        return None
    return int(input_tokens or 0) + int(output_tokens or 0)


def _fallback_llm_metadata(
    config: dict[str, Any],
    client: Any,
    *,
    fallback_reason: str,
    fallback_message: str | None = None,
    fallback_exception_type: str | None = None,
) -> dict[str, Any]:
    return {
        "openai_latency_seconds": None,
        "openai_tokens": None,
        "fallback_reason": fallback_reason,
        "fallback_message": _redact_llm_fallback_message(fallback_message, config),
        "fallback_exception_type": fallback_exception_type,
        "openai_response_id": None,
        "openai_model": (
            getattr(client, "model", None)
            or config.get("llm_model")
            or config.get("openai_model")
        ),
        "llm_backend": (
            getattr(client, "backend", None)
            or config.get("llm_backend")
            or config.get("openai_backend")
            or "openai_responses"
        ),
        "llm_provider": getattr(client, "provider", None) or config.get("llm_provider"),
    }


def _redact_llm_fallback_message(message: str | None, config: dict[str, Any]) -> str | None:
    if message is None:
        return None
    redacted = str(message)
    for key in ("llm_api_key", "openai_api_key"):
        value = str(config.get(key) or "")
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    redacted = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]+", "sk-[REDACTED]", redacted)
    redacted = re.sub(r"\b[A-Z0-9_]*(?:SECRET|TOKEN|API[_-]?KEY)[A-Z0-9_]*\b", "[REDACTED]", redacted)
    redacted = _ADVISOR_FALLBACK_PATH_RE.sub("[REDACTED]", redacted)
    redacted = _ADVISOR_FALLBACK_PROOF_HASH_RE.sub("[REDACTED]", redacted)
    return redacted


def write_advisor_trace(path: str | Path, trace: AdvisorTrace) -> None:
    json_dump(path, trace.to_dict())


def advisor_Evaluate(
    *,
    telemetry_history: Iterable[TelemetryRecord | TelemetryRecordUpdate | dict[str, Any]] | None = None,
    current_request_features: dict[str, Any] | None = None,
    sidecar_ticket: SidecarIndexTicket | dict[str, Any] | None = None,
    advisor_config: dict[str, Any] | None = None,
    job_id: str | None = None,
    request_id: str | None = None,
) -> Result[dict[str, Any]]:
    return RuntimeOptimizationAdvisor(advisor_config).evaluate_result(
        telemetry_history=telemetry_history,
        current_request_features=current_request_features,
        sidecar_ticket=sidecar_ticket,
        job_id=job_id,
        request_id=request_id,
    )


def build_advisor_trace(
    *,
    request_id: str,
    feature_snapshot: dict[str, Any],
    decision: AdvisorDecision,
    gate_result: dict[str, Any] | None,
    telemetry_refs: list[str] | None = None,
    started_at: float | None = None,
    agent_contract_ref: dict[str, Any] | None = None,
) -> AdvisorTrace:
    overhead = 0.0 if started_at is None else max(0.0, time.perf_counter() - float(started_at))
    return AdvisorTrace(
        trace_version=ADVISOR_TRACE_VERSION,
        generated_at=_iso_now(),
        request_id=str(request_id),
        telemetry_refs=list(telemetry_refs or []),
        feature_snapshot=dict(feature_snapshot),
        decision=decision,
        gate_result=gate_result,
        advisor_overhead_seconds=round(overhead, 6),
        agent_contract_ref=agent_contract_ref,
    )

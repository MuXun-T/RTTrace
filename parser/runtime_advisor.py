from __future__ import annotations

from dataclasses import dataclass
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
RUNTIME_LOAD_PLAN_VERSION = "runtime-load-plan-v1"
HEURISTIC_MODEL_REF = "heuristic-runtime-advisor-v1"
ERR_ADVISOR_UNAVAILABLE = "ERR-ADVISOR_UNAVAILABLE"
RISK_LEVELS = {"low", "medium", "high", "critical"}
SCHEDULES = {"serial_safe", "sidecar_first", "bounded_parallel", "degraded_priority"}
ADVISOR_MODES = {"disabled", "heuristic", "offline_coefficients", "openai_structured"}
RUNTIME_LOAD_MODES = {"full", "cold_preview", "warm_reuse", "hot_reuse"}
RUNTIME_LOAD_INDEX_BUILD_MODES = {"full", "minimal", "deferred"}


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


def _feature_snapshot_hash(features: dict[str, Any]) -> str:
    payload = json.dumps(features, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def coefficient_payload_checksum(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in dict(payload).items() if key != "model_checksum"}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


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
        mode = str(self.config.get("advisor_mode") or self.config.get("mode") or "disabled").strip() or "disabled"
        if mode not in ADVISOR_MODES:
            mode = "disabled"
        history_items = list(telemetry_history or [])
        latest = _latest_telemetry(history_items)
        ticket_payload = _as_mapping(sidecar_ticket)
        max_history_rows = _optional_int(self.config.get("openai_max_history_rows")) or 20
        features = {
            **latest,
            **dict(current_request_features or {}),
            "ticket_present": bool(ticket_payload),
            "ticket_row_count": ticket_payload.get("row_count"),
            "ticket_sidecar_bytes": ticket_payload.get("sidecar_bytes"),
            "risk_history_summary": _risk_history_summary(history_items, max_rows=max_history_rows),
        }
        snapshot_hash = _feature_snapshot_hash(features)
        if mode == "disabled":
            return AdvisorDecision(
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
            )
        if mode == "offline_coefficients":
            return self._evaluate_offline_coefficients(features, snapshot_hash)
        if mode == "openai_structured":
            return self._evaluate_openai_structured(features, snapshot_hash)
        return self._evaluate_heuristic(features, snapshot_hash)

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
                },
            )

    def _evaluate_heuristic(self, features: dict[str, Any], snapshot_hash: str) -> AdvisorDecision:
        sidecar_bytes = _optional_int(features.get("sidecar_bytes") or features.get("ticket_sidecar_bytes")) or 0
        input_bytes = _optional_int(features.get("input_bytes")) or 0
        sidecar_row_count = _optional_int(features.get("sidecar_row_count") or features.get("ticket_row_count")) or 0
        previous_runtime = _optional_float(features.get("runtime_seconds"))
        previous_peak_rss = _optional_float(features.get("peak_rss_mb"))
        ticket_present = bool(features.get("ticket_present"))

        large_sidecar = sidecar_bytes >= DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES
        recommend_index_reuse = ticket_present
        recommend_index_prebuild = (not ticket_present) and large_sidecar
        recommend_streaming_write = input_bytes >= 256 * 1024 * 1024 or (previous_peak_rss is not None and previous_peak_rss >= 2048.0)
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
            risk_level = "high" if risk_level == "medium" else risk_level
            reasons.append("input or RSS suggests streaming write")
        if ticket_present:
            reasons.append("sidecar index ticket is available")
        if not reasons:
            reasons.append("small deterministic baseline path")

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
        )

    def _evaluate_heuristic_fallback(self, features: dict[str, Any], snapshot_hash: str, reason: str) -> AdvisorDecision:
        base = self._evaluate_heuristic(features, snapshot_hash)
        return AdvisorDecision(
            **{
                **base.to_dict(),
                "reasons": [*base.reasons, reason],
            }
        )

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
        if coefficients.get("status") != "trained":
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients payload is not trained",
            )
        weights_payload = coefficients.get("runtime_weights")
        if not isinstance(weights_payload, dict):
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients runtime weights are missing",
            )
        model_checksum = coefficient_payload_checksum(coefficients)
        expected_checksum = str(coefficients.get("model_checksum") or "").strip()
        if expected_checksum and expected_checksum != model_checksum:
            return self._evaluate_heuristic_fallback(
                features,
                snapshot_hash,
                "offline coefficients checksum mismatch",
            )

        base = self._evaluate_heuristic(features, snapshot_hash)
        weights = dict(weights_payload)
        intercept = _optional_float(coefficients.get("runtime_intercept")) or 0.0
        runtime = intercept
        for name, weight in weights.items():
            runtime += (_optional_float(features.get(name)) or 0.0) * (_optional_float(weight) or 0.0)
        runtime = runtime if runtime > 0 else base.predicted_runtime_seconds
        model_ref = str(coefficients.get("model_ref") or "offline-coefficients")
        return AdvisorDecision(
            **{
                **base.to_dict(),
                "advisor_mode": "offline_coefficients",
                "model_ref": model_ref,
                "model_checksum": model_checksum,
                "predicted_runtime_seconds": None if runtime is None else round(float(runtime), 6),
            }
        )

    def _evaluate_openai_structured(self, features: dict[str, Any], snapshot_hash: str) -> AdvisorDecision:
        schema = dict(self.config.get("openai_decision_schema") or load_schema("advisor_decision.schema.json"))
        client_for_metadata = None
        try:
            client = self.config.get("llm_client") or self.config.get("openai_client")
            if client is None:
                client = LLMAdvisorClient.from_config(self.config)
            client_for_metadata = client
            result = client.request_advisor_decision(
                feature_payload=features,
                decision_schema=schema,
                feature_snapshot_hash=snapshot_hash,
                decision_version=ADVISOR_DECISION_VERSION,
            )
            payload = dict(result.decision_payload)
            payload.setdefault("model_checksum", None)
            schema_reason = validate_schema(schema, payload)
            if schema_reason is not None:
                raise LLMAdvisorClientError("openai_schema_invalid", schema_reason)
            self.last_advisor_metadata = {
                "openai_latency_seconds": result.latency_seconds,
                "openai_tokens": _openai_usage_token_count(result.usage),
                "fallback_reason": None,
                "openai_response_id": result.response_id,
                "openai_model": result.model,
                "llm_backend": getattr(result, "backend", None),
                "llm_provider": getattr(client_for_metadata, "provider", None) or self.config.get("llm_provider"),
            }
            return AdvisorDecision(
                decision_version=str(payload["decision_version"]),
                advisor_mode=str(payload["advisor_mode"]),
                model_ref=str(payload["model_ref"]),
                model_checksum=payload.get("model_checksum"),
                feature_snapshot_hash=str(payload["feature_snapshot_hash"]),
                recommend_index_prebuild=bool(payload["recommend_index_prebuild"]),
                recommend_index_reuse_attempt=bool(payload["recommend_index_reuse_attempt"]),
                recommend_streaming_write=bool(payload["recommend_streaming_write"]),
                recommended_schedule=str(payload["recommended_schedule"]),
                predicted_runtime_seconds=(
                    None
                    if payload.get("predicted_runtime_seconds") is None
                    else round(float(payload["predicted_runtime_seconds"]), 6)
                ),
                predicted_peak_rss_mb=(
                    None
                    if payload.get("predicted_peak_rss_mb") is None
                    else round(float(payload["predicted_peak_rss_mb"]), 3)
                ),
                risk_level=str(payload["risk_level"]),
                reasons=[str(item) for item in list(payload.get("reasons") or [])],
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

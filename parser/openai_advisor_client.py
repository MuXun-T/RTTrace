from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import socket
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import urllib.parse

from spec.schema_validator import validate_schema


OPENAI_ADVISOR_DEFAULT_MODEL = "gpt-5.4-mini"
OPENAI_ADVISOR_DEFAULT_TIMEOUT_S = 8.0
OPENAI_ADVISOR_DEFAULT_REASONING_EFFORT = "low"
OPENAI_ADVISOR_DEFAULT_MAX_HISTORY_ROWS = 20
OPENAI_RESPONSES_BASE_URL = "https://api.openai.com"
LLM_ADVISOR_DEFAULT_BACKEND = "openai_responses"
LLM_ADVISOR_BACKENDS = {"openai_responses", "openai_compatible_chat"}
DEEPSEEK_ADVISOR_DEFAULT_MODEL = "deepseek-v4-pro"
LLM_ADVISOR_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "backend": "openai_compatible_chat",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": DEEPSEEK_ADVISOR_DEFAULT_MODEL,
        "chat_completions_path": "/chat/completions",
        "max_retries": 2,
    },
}

OPENAI_FALLBACK_UNCONFIGURED = "openai_unconfigured"
OPENAI_FALLBACK_TIMEOUT = "openai_timeout"
OPENAI_FALLBACK_RATE_LIMITED = "openai_rate_limited"
OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE = "openai_refused_or_incomplete"
OPENAI_FALLBACK_JSON_INVALID = "openai_json_invalid"
OPENAI_FALLBACK_SCHEMA_INVALID = "openai_schema_invalid"

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]

_NUMERIC_FEATURES = {
    "input_bytes",
    "sidecar_bytes",
    "sidecar_row_count",
    "sidecar_bytes_scanned",
    "sidecar_validate_seconds",
    "index_build_open_seconds",
    "package_write_seconds",
    "runtime_seconds",
    "peak_rss_mb",
    "advisor_overhead_seconds",
    "ticket_row_count",
}
_BOOLEAN_FEATURES = {
    "index_reused",
    "index_rebuilt",
    "ticket_present",
    "background_prebuild_reused",
    "sidecar_ticket_fast_path",
}
_STRING_FEATURES = {
    "write_mode",
}
_ADVISORY_SUMMARY_FIELDS = {
    "benchmark_delta",
    "ticket_status",
}
_SENSITIVE_SUMMARY_KEY_TOKENS = {
    "event_content",
    "event_payload",
    "event_stream",
    "index_path",
    "package_path",
    "proof_digest",
    "proof_hash",
    "raw_sidecar",
    "raw_trace",
    "sidecar_path",
    "sidecar_row",
    "sidecar_rows",
    "source_path",
    "trace_path",
}
_SUMMARY_PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?[\\/]|\.{1,2}[\\/])\S+")
_SUMMARY_PROOF_HASH_RE = re.compile(r"sha256:[^\s,;]+")


class LLMAdvisorClientError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = str(reason)
        super().__init__(message or self.reason)


@dataclass(frozen=True)
class LLMAdvisorClientResult:
    decision_payload: dict[str, Any]
    response_id: str | None
    model: str
    usage: dict[str, Any]
    latency_seconds: float
    request_payload: dict[str, Any]
    backend: str = LLM_ADVISOR_DEFAULT_BACKEND


class LLMAdvisorClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = OPENAI_ADVISOR_DEFAULT_MODEL,
        timeout_s: float = OPENAI_ADVISOR_DEFAULT_TIMEOUT_S,
        reasoning_effort: str | None = OPENAI_ADVISOR_DEFAULT_REASONING_EFFORT,
        max_history_rows: int = OPENAI_ADVISOR_DEFAULT_MAX_HISTORY_ROWS,
        base_url: str = OPENAI_RESPONSES_BASE_URL,
        enabled: bool = False,
        transport: Transport | None = None,
        max_output_tokens: int = 900,
        backend: str = LLM_ADVISOR_DEFAULT_BACKEND,
        chat_response_format: str | None = "json_object",
        provider: str | None = None,
        chat_completions_path: str | None = None,
        max_retries: int = 0,
        retry_delay_s: float = 0.5,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or OPENAI_ADVISOR_DEFAULT_MODEL).strip() or OPENAI_ADVISOR_DEFAULT_MODEL
        self.timeout_s = max(0.001, float(timeout_s or OPENAI_ADVISOR_DEFAULT_TIMEOUT_S))
        self.reasoning_effort = str(reasoning_effort or "").strip() or None
        self.max_history_rows = max(1, int(max_history_rows or OPENAI_ADVISOR_DEFAULT_MAX_HISTORY_ROWS))
        self.base_url = str(base_url or OPENAI_RESPONSES_BASE_URL).strip().rstrip("/")
        self.enabled = bool(enabled)
        self.transport = transport or _urllib_transport
        self.max_output_tokens = max(1, int(max_output_tokens or 900))
        requested_backend = str(backend or LLM_ADVISOR_DEFAULT_BACKEND).strip()
        self.backend = requested_backend if requested_backend in LLM_ADVISOR_BACKENDS else LLM_ADVISOR_DEFAULT_BACKEND
        self.chat_response_format = str(chat_response_format or "").strip() or None
        self.provider = str(provider or "").strip() or None
        self.chat_completions_path = _normalize_path(chat_completions_path)
        self.max_retries = max(0, int(max_retries or 0))
        self.retry_delay_s = max(0.0, float(retry_delay_s or 0.0))

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "LLMAdvisorClient":
        payload = dict(config or {})
        provider = str(payload.get("llm_provider") or os.environ.get("LLM_ADVISOR_PROVIDER") or "").strip()
        provider_defaults = _provider_defaults(provider)
        enabled = _as_bool(
            payload.get(
                "llm_enabled",
                payload.get(
                    "openai_enabled",
                    payload.get("enabled", os.environ.get("LLM_ADVISOR_ENABLED", os.environ.get("OPENAI_ADVISOR_ENABLED"))),
                ),
            ),
            default=False,
        )
        api_key_env = (
            payload.get("llm_api_key_env")
            or payload.get("openai_api_key_env")
            or os.environ.get("LLM_ADVISOR_API_KEY_ENV")
            or provider_defaults.get("api_key_env")
        )
        return cls(
            api_key=(
                payload.get("llm_api_key")
                or payload.get("openai_api_key")
                or (os.environ.get(str(api_key_env)) if api_key_env else None)
                or os.environ.get("LLM_ADVISOR_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
            ),
            model=(
                payload.get("llm_model")
                or payload.get("openai_model")
                or os.environ.get("LLM_ADVISOR_MODEL")
                or os.environ.get("OPENAI_ADVISOR_MODEL")
                or provider_defaults.get("model")
                or OPENAI_ADVISOR_DEFAULT_MODEL
            ),
            timeout_s=_as_float(
                payload.get("llm_timeout_s")
                or payload.get("openai_timeout_s")
                or os.environ.get("LLM_ADVISOR_TIMEOUT_S")
                or os.environ.get("OPENAI_ADVISOR_TIMEOUT_S"),
                OPENAI_ADVISOR_DEFAULT_TIMEOUT_S,
            ),
            reasoning_effort=(
                payload.get("llm_reasoning_effort")
                or payload.get("openai_reasoning_effort")
                or os.environ.get("LLM_ADVISOR_REASONING_EFFORT")
                or os.environ.get("OPENAI_ADVISOR_REASONING_EFFORT")
                or OPENAI_ADVISOR_DEFAULT_REASONING_EFFORT
            ),
            max_history_rows=_as_int(
                payload.get("llm_max_history_rows")
                or payload.get("openai_max_history_rows")
                or os.environ.get("LLM_ADVISOR_MAX_HISTORY_ROWS")
                or os.environ.get("OPENAI_ADVISOR_MAX_HISTORY_ROWS"),
                OPENAI_ADVISOR_DEFAULT_MAX_HISTORY_ROWS,
            ),
            base_url=(
                payload.get("llm_base_url")
                or payload.get("openai_base_url")
                or os.environ.get("LLM_ADVISOR_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or provider_defaults.get("base_url")
                or OPENAI_RESPONSES_BASE_URL
            ),
            enabled=enabled,
            transport=payload.get("llm_transport") or payload.get("openai_transport"),
            max_output_tokens=_as_int(
                payload.get("llm_max_output_tokens")
                or payload.get("openai_max_output_tokens")
                or os.environ.get("LLM_ADVISOR_MAX_OUTPUT_TOKENS")
                or os.environ.get("OPENAI_ADVISOR_MAX_OUTPUT_TOKENS"),
                900,
            ),
            backend=(
                payload.get("llm_backend")
                or payload.get("openai_backend")
                or os.environ.get("LLM_ADVISOR_BACKEND")
                or os.environ.get("OPENAI_ADVISOR_BACKEND")
                or provider_defaults.get("backend")
                or LLM_ADVISOR_DEFAULT_BACKEND
            ),
            chat_response_format=payload.get("llm_chat_response_format", "json_object"),
            provider=provider or None,
            chat_completions_path=(
                payload.get("llm_chat_completions_path")
                or os.environ.get("LLM_ADVISOR_CHAT_COMPLETIONS_PATH")
                or provider_defaults.get("chat_completions_path")
            ),
            max_retries=_as_int(
                payload.get("llm_max_retries")
                or os.environ.get("LLM_ADVISOR_MAX_RETRIES")
                or provider_defaults.get("max_retries"),
                0,
            ),
            retry_delay_s=_as_float(
                payload.get("llm_retry_delay_s")
                or os.environ.get("LLM_ADVISOR_RETRY_DELAY_S")
                or provider_defaults.get("retry_delay_s"),
                0.5,
            ),
        )

    def request_advisor_decision(
        self,
        *,
        feature_payload: dict[str, Any],
        decision_schema: dict[str, Any],
        feature_snapshot_hash: str,
        decision_version: str,
    ) -> LLMAdvisorClientResult:
        if not self.enabled or not self.api_key:
            raise LLMAdvisorClientError(OPENAI_FALLBACK_UNCONFIGURED)

        sanitized_features = sanitize_advisor_features(
            feature_payload,
            max_history_rows=self.max_history_rows,
        )
        if self.backend == "openai_compatible_chat":
            request_payload = self._build_chat_request_payload(
                sanitized_features=sanitized_features,
                decision_schema=decision_schema,
                feature_snapshot_hash=feature_snapshot_hash,
                decision_version=decision_version,
            )
            endpoint_url = _chat_completions_url(self.base_url, path=self.chat_completions_path)
        else:
            request_payload = self._build_responses_request_payload(
                sanitized_features=sanitized_features,
                decision_schema=decision_schema,
                feature_snapshot_hash=feature_snapshot_hash,
                decision_version=decision_version,
            )
            endpoint_url = _responses_url(self.base_url)
        encoded = json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        started_at = time.perf_counter()
        attempt = 0
        while True:
            try:
                status_code, _response_headers, response_body = self.transport(
                    endpoint_url,
                    headers,
                    encoded,
                    self.timeout_s,
                )
            except TimeoutError as exc:
                if self._retry_after_transient_failure(attempt):
                    attempt += 1
                    continue
                raise LLMAdvisorClientError(OPENAI_FALLBACK_TIMEOUT, str(exc)) from exc
            except (socket.timeout, urllib.error.URLError) as exc:
                is_timeout = isinstance(getattr(exc, "reason", None), TimeoutError) or isinstance(exc, socket.timeout)
                if self._retry_after_transient_failure(attempt):
                    attempt += 1
                    continue
                reason = OPENAI_FALLBACK_TIMEOUT if is_timeout else OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE
                raise LLMAdvisorClientError(reason, str(exc)) from exc

            if status_code in {408, 504} and self._retry_after_transient_failure(attempt):
                attempt += 1
                continue
            break

        latency_seconds = round(time.perf_counter() - started_at, 6)
        if status_code == 429:
            raise LLMAdvisorClientError(OPENAI_FALLBACK_RATE_LIMITED, "LLM API rate limit")
        if status_code in {408, 504}:
            raise LLMAdvisorClientError(OPENAI_FALLBACK_TIMEOUT, f"LLM API HTTP {status_code}")
        if status_code < 200 or status_code >= 300:
            raise LLMAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, f"LLM API HTTP {status_code}")

        response_payload = _loads_response(response_body)
        if self.backend == "openai_compatible_chat":
            decision_payload = parse_openai_compatible_chat_response(
                response_payload,
                decision_schema=decision_schema,
                feature_snapshot_hash=feature_snapshot_hash,
                decision_version=decision_version,
            )
        else:
            decision_payload = parse_openai_advisor_response(
                response_payload,
                decision_schema=decision_schema,
                feature_snapshot_hash=feature_snapshot_hash,
                decision_version=decision_version,
            )
        return LLMAdvisorClientResult(
            decision_payload=decision_payload,
            response_id=str(response_payload.get("id")) if response_payload.get("id") is not None else None,
            model=str(response_payload.get("model") or self.model),
            usage=dict(response_payload.get("usage") or {}),
            latency_seconds=latency_seconds,
            request_payload=request_payload,
            backend=self.backend,
        )

    def _retry_after_transient_failure(self, attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        if self.retry_delay_s > 0:
            time.sleep(self.retry_delay_s)
        return True

    def _build_responses_request_payload(
        self,
        *,
        sanitized_features: dict[str, Any],
        decision_schema: dict[str, Any],
        feature_snapshot_hash: str,
        decision_version: str,
    ) -> dict[str, Any]:
        user_payload = _advisor_user_payload(
            sanitized_features=sanitized_features,
            feature_snapshot_hash=feature_snapshot_hash,
            decision_version=decision_version,
            model=self.model,
            include_schema=False,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": _advisor_system_prompt()}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                        }
                    ],
                },
            ],
            "text": {
                "format": advisor_decision_text_format(decision_schema),
            },
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        return payload

    def _build_chat_request_payload(
        self,
        *,
        sanitized_features: dict[str, Any],
        decision_schema: dict[str, Any],
        feature_snapshot_hash: str,
        decision_version: str,
    ) -> dict[str, Any]:
        user_payload = _advisor_user_payload(
            sanitized_features=sanitized_features,
            feature_snapshot_hash=feature_snapshot_hash,
            decision_version=decision_version,
            model=self.model,
            include_schema=True,
            decision_schema=decision_schema,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _advisor_system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        if self.chat_response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload


class OpenAIAdvisorClient(LLMAdvisorClient):
    pass


OpenAIAdvisorClientError = LLMAdvisorClientError
OpenAIAdvisorClientResult = LLMAdvisorClientResult


def _advisor_system_prompt() -> str:
    return (
        "You are the RuntimeOptimizationAdvisor for an RTOS trace evidence exporter. "
        "Return JSON only. The decision is advisory, must not create proof facts, must not "
        "change closure/frontier/proof digest semantics, and must choose conservative values "
        "when information is insufficient."
    )


def _advisor_user_payload(
    *,
    sanitized_features: dict[str, Any],
    feature_snapshot_hash: str,
    decision_version: str,
    model: str,
    include_schema: bool,
    decision_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision_version": decision_version,
        "advisor_mode": "openai_structured",
        "model_ref": model,
        "feature_snapshot_hash": feature_snapshot_hash,
        "allowed_schedules": ["serial_safe", "sidecar_first", "bounded_parallel", "degraded_priority"],
        "allowed_risk_levels": ["low", "medium", "high", "critical"],
        "features": sanitized_features,
        "constraints": [
            "Do not include raw trace, sidecar rows, proof digest contents, filesystem paths, usernames, or secrets.",
            "If ticket_present is false, recommend_index_reuse_attempt must normally be false.",
            "If risk is unclear, prefer serial_safe and low or medium risk.",
            "Return exactly one JSON object and no markdown.",
        ],
    }
    if include_schema and decision_schema is not None:
        payload["json_schema"] = advisor_decision_text_format(decision_schema)["schema"]
    return payload


def advisor_decision_text_format(decision_schema: dict[str, Any]) -> dict[str, Any]:
    schema = json.loads(json.dumps(decision_schema))
    properties = dict(schema.get("properties") or {})
    required = list(schema.get("required") or [])
    if "model_checksum" in properties and "model_checksum" not in required:
        required.append("model_checksum")
    schema["required"] = required
    return {
        "type": "json_schema",
        "name": "AdvisorDecision",
        "strict": True,
        "schema": schema,
    }


def sanitize_advisor_features(features: dict[str, Any], *, max_history_rows: int = OPENAI_ADVISOR_DEFAULT_MAX_HISTORY_ROWS) -> dict[str, Any]:
    payload = dict(features or {})
    sanitized: dict[str, Any] = {}
    dataset_id = str(payload.get("dataset_id") or "").strip()
    if dataset_id:
        sanitized["dataset_id_hash"] = _hash_identifier(dataset_id)
    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        sanitized["run_id_hash"] = _hash_identifier(run_id)

    for key in sorted(_NUMERIC_FEATURES):
        if key in payload:
            sanitized[key] = _clean_number(payload.get(key))
    for key in sorted(_BOOLEAN_FEATURES):
        if key in payload:
            sanitized[key] = bool(payload.get(key))
    for key in sorted(_STRING_FEATURES):
        if key in payload and payload.get(key) is not None:
            sanitized[key] = str(payload.get(key))[:80]
    stage_timings = _sanitize_advisory_summary(payload.get("stage_timings"), max_items=max_history_rows, numeric_only=True)
    if isinstance(stage_timings, dict) and stage_timings:
        sanitized["stage_timings"] = stage_timings
    for key in sorted(_ADVISORY_SUMMARY_FIELDS):
        summary = _sanitize_advisory_summary(payload.get(key), max_items=max_history_rows)
        if summary not in (None, {}, []):
            sanitized[key] = summary
    failure_reason = _sanitize_summary_text(payload.get("failure_reason"))
    if failure_reason:
        sanitized["failure_reason"] = failure_reason
    if "risk_history_summary" in payload:
        sanitized["risk_history_summary"] = _json_safe(payload.get("risk_history_summary"), max_items=max_history_rows)
    return sanitized


def parse_openai_advisor_response(
    response_payload: dict[str, Any],
    *,
    decision_schema: dict[str, Any],
    feature_snapshot_hash: str,
    decision_version: str,
) -> dict[str, Any]:
    status = str(response_payload.get("status") or "completed")
    if status not in {"completed", "succeeded"}:
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, f"OpenAI status {status}")
    if response_payload.get("error"):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, str(response_payload.get("error")))
    if _contains_refusal(response_payload):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, "OpenAI refusal")
    output_text = _extract_output_text(response_payload)
    if not output_text:
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, "OpenAI response has no output_text")
    return _parse_decision_payload_from_text(
        output_text,
        decision_schema=decision_schema,
        feature_snapshot_hash=feature_snapshot_hash,
        decision_version=decision_version,
    )


def parse_openai_compatible_chat_response(
    response_payload: dict[str, Any],
    *,
    decision_schema: dict[str, Any],
    feature_snapshot_hash: str,
    decision_version: str,
) -> dict[str, Any]:
    if response_payload.get("error"):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, str(response_payload.get("error")))
    output_text = _extract_chat_output_text(response_payload)
    if not output_text:
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, "chat response has no message content")
    return _parse_decision_payload_from_text(
        output_text,
        decision_schema=decision_schema,
        feature_snapshot_hash=feature_snapshot_hash,
        decision_version=decision_version,
    )


def _parse_decision_payload_from_text(
    output_text: str,
    *,
    decision_schema: dict[str, Any],
    feature_snapshot_hash: str,
    decision_version: str,
) -> dict[str, Any]:
    decision_payload = _load_json_value_from_text(output_text)
    if not isinstance(decision_payload, dict):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_SCHEMA_INVALID, "AdvisorDecision output must be an object")
    decision_payload.setdefault("model_checksum", None)
    schema_reason = validate_schema(decision_schema, decision_payload)
    if schema_reason is not None:
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_SCHEMA_INVALID, schema_reason)
    if str(decision_payload.get("decision_version")) != str(decision_version):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_SCHEMA_INVALID, "decision_version mismatch")
    if str(decision_payload.get("advisor_mode")) != "openai_structured":
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_SCHEMA_INVALID, "advisor_mode mismatch")
    if str(decision_payload.get("feature_snapshot_hash")) != str(feature_snapshot_hash):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_SCHEMA_INVALID, "feature_snapshot_hash mismatch")
    for key in ("predicted_runtime_seconds", "predicted_peak_rss_mb"):
        value = decision_payload.get(key)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))):
            raise OpenAIAdvisorClientError(OPENAI_FALLBACK_SCHEMA_INVALID, f"{key} must be finite")
    return decision_payload


def _load_json_value_from_text(value: str) -> Any:
    text = _strip_json_text(value)
    try:
        return json.loads(text)
    except json.JSONDecodeError as direct_exc:
        last_exc = direct_exc

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
            return parsed
        except json.JSONDecodeError as exc:
            last_exc = exc
    raise OpenAIAdvisorClientError(OPENAI_FALLBACK_JSON_INVALID, last_exc.msg) from last_exc


def _responses_url(base_url: str) -> str:
    base = str(base_url or OPENAI_RESPONSES_BASE_URL).rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def _chat_completions_url(base_url: str, *, path: str | None = None) -> str:
    base = str(base_url or OPENAI_RESPONSES_BASE_URL).rstrip("/")
    if path:
        return f"{base}{_normalize_path(path)}"
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    if _is_deepseek_official_base(base):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _provider_defaults(provider: str | None) -> dict[str, Any]:
    normalized = str(provider or "").strip().lower()
    if not normalized:
        return {}
    return dict(LLM_ADVISOR_PROVIDER_DEFAULTS.get(normalized) or {})


def _normalize_path(path: Any) -> str | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    return raw if raw.startswith("/") else f"/{raw}"


def _is_deepseek_official_base(base_url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(base_url or ""))
    except ValueError:
        return False
    return parsed.netloc.lower() == "api.deepseek.com" and parsed.path.rstrip("/") == ""


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()), exc.read()


def _loads_response(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_JSON_INVALID, str(exc)) from exc
    if not isinstance(payload, dict):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_JSON_INVALID, "OpenAI response must be an object")
    return payload


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    for item in list(payload.get("output") or []):
        if not isinstance(item, dict):
            continue
        for content in list(item.get("content") or []):
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                return str(content.get("text") or "")
    return ""


def _extract_chat_output_text(payload: dict[str, Any]) -> str:
    choices = list(payload.get("choices") or [])
    if not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    finish_reason = first.get("finish_reason")
    if finish_reason not in {None, "stop"}:
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, f"chat finish_reason {finish_reason}")
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    if message.get("refusal"):
        raise OpenAIAdvisorClientError(OPENAI_FALLBACK_REFUSED_OR_INCOMPLETE, "chat refusal")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "".join(parts)
    return ""


def _strip_json_text(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _contains_refusal(payload: dict[str, Any]) -> bool:
    for item in list(payload.get("output") or []):
        if not isinstance(item, dict):
            continue
        for content in list(item.get("content") or []):
            if isinstance(content, dict) and (content.get("type") == "refusal" or content.get("refusal")):
                return True
    return False


def _hash_identifier(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _clean_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    if numeric.is_integer():
        return int(numeric)
    return round(numeric, 6)


def _json_safe(value: Any, *, max_items: int) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for index, key in enumerate(sorted(value)):
            if index >= max_items:
                break
            safe[str(key)[:80]] = _json_safe(value[key], max_items=max_items)
        return safe
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, max_items=max_items) for item in list(value)[:max_items]]
    return str(value)[:120]


def _sanitize_advisory_summary(
    value: Any,
    *,
    max_items: int,
    numeric_only: bool = False,
    depth: int = 0,
) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return None if numeric_only else value
    if isinstance(value, (int, float)):
        return _clean_number(value)
    if isinstance(value, str):
        return None if numeric_only else _sanitize_summary_text(value)
    if depth >= 2:
        return None
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for index, key in enumerate(sorted(value)):
            if index >= max_items:
                break
            clean_key = _sanitize_summary_key(key)
            if clean_key is None:
                continue
            clean_value = _sanitize_advisory_summary(
                value[key],
                max_items=max_items,
                numeric_only=numeric_only,
                depth=depth + 1,
            )
            if clean_value in (None, {}, []):
                continue
            safe[clean_key] = clean_value
        return safe
    if numeric_only or not isinstance(value, (list, tuple)):
        return None
    safe_items = [
        clean_value
        for item in list(value)[:max_items]
        for clean_value in [_sanitize_advisory_summary(item, max_items=max_items, depth=depth + 1)]
        if clean_value not in (None, {}, [])
    ]
    return safe_items


def _sanitize_summary_key(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    key_token = normalized.lower().replace("-", "_").replace(".", "_")
    if key_token.endswith("_path") or key_token in _SENSITIVE_SUMMARY_KEY_TOKENS:
        return None
    if "proof_hash" in key_token:
        return None
    return normalized[:80]


def _sanitize_summary_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = _SUMMARY_PATH_RE.sub("[REDACTED]", text)
    text = _SUMMARY_PROOF_HASH_RE.sub("[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:160] or None


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)

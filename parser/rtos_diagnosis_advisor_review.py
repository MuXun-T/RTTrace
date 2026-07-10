"""P6.5 review-only overlay for an immutable P6.4 diagnosis report."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Iterable

from parser.rtos_diagnosis_report import canonical_report_json, validate_report
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


REVIEW_SCHEMA_VERSION = "rtos-diagnosis-advisor-review-v1"
MOCK_SCHEMA_VERSION = "rtos-diagnosis-advisor-mock-v1"
VARIANT_IDS = frozenset(
    {"advisor_disabled", "deterministic_template", "retrieval_grounded_review", "mock_llm_explanation"}
)
CASE_KINDS = frozenset(
    {
        "priority_inversion",
        "irq_latency_spike",
        "mutex_hold_inflation",
        "queue_wait_backlog",
        "task_starvation",
        "corrupt_segment",
        "stale_sidecar",
        "missing_calibration",
    }
)
_MAX_REVIEW_BYTES = 64 * 1024
_MOCK_SUMMARY = "Mock explanation is review-only and does not create source facts."
_FORBIDDEN_MOCK_KEYS = frozenset(
    {
        "expected_root_cause",
        "root_cause",
        "root_cause_truth",
        "affected_entity",
        "replay_truth",
        "replay_status",
        "claim_class",
        "proof_truth",
        "proof_hash_input",
        "proof_digest_write_path",
        "llm_truth",
        "advisor_truth",
        "executable_command",
        "shell_command",
        "tool_call",
        "api_key",
        "token",
        "secret",
        "password",
    }
)
_FORBIDDEN_MOCK_TEXT = (
    "root cause",
    "root_cause",
    "root-cause",
    "affected entity",
    "affected_entity",
    "replay status",
    "replay_status",
    "replay truth",
    "replay_truth",
    "claim class",
    "claim_class",
    "proof hash",
    "proof digest",
    "proof_hash_input",
    "proof_digest_write_path",
    "execute",
    "action",
    "shell command",
    "shell",
    "tool call",
    "tool invocation",
    "tool",
    "api key",
    "access key",
    "key=",
    "token",
    "secret",
    "password",
    "ignore previous",
    "system prompt",
    "developer message",
    "jailbreak",
)
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)/(?:\S*)")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?:^|\s)[a-zA-Z]:[\\/](?:\S*)")
_MOCK_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "case_reviews"],
    "properties": {
        "schema_version": {"type": "string", "enum": [MOCK_SCHEMA_VERSION]},
        "case_reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["case_id", "summary", "limitations"],
                "properties": {
                    "case_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "limitations": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def build_diagnosis_advisor_review(
    report: dict[str, Any],
    *,
    variant_id: str,
    review_id: str = "phase6-p6-5-advisor-review",
    retrieval_references: Iterable[str] | None = None,
    mock_response: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, review-only result without mutating ``report``.

    Optional retrieval and mock inputs are in-memory, bounded test inputs.  This
    module intentionally has no file, network, environment, tool, or action API.
    """
    _require_non_empty_string(variant_id, "variant_id")
    _require_non_empty_string(review_id, "review_id")
    if variant_id not in VARIANT_IDS:
        raise ValueError(f"unsupported variant_id {variant_id!r}")

    validate_report(report)
    source_canonical_before = canonical_report_json(report)
    source_hash_before = _sha256(source_canonical_before)
    source_snapshot = json.loads(source_canonical_before)
    cases = _allowlisted_cases(source_snapshot)
    _assert_safe_source_identifiers(source_snapshot["report_id"], cases)
    event = _empty_event()

    if variant_id == "advisor_disabled":
        status, rows, deterministic, retrieval_used, llm_used = "disabled", _disabled_rows(cases), True, False, False
    elif variant_id == "deterministic_template":
        status, rows, deterministic, retrieval_used, llm_used = "completed", _template_rows(cases), True, False, False
    elif variant_id == "retrieval_grounded_review":
        try:
            _validate_retrieval_references(retrieval_references, cases)
            status, rows, deterministic, retrieval_used, llm_used = (
                "completed",
                _retrieval_rows(cases),
                True,
                True,
                False,
            )
        except ValueError:
            event["schema_invalid_count"] = 1
            event["rejected_invalid_count"] = 1
            event["fallback_count"] = 1
            status, rows, deterministic, retrieval_used, llm_used = (
                "fallback",
                _fallback_rows(cases, variant_id),
                True,
                False,
                False,
            )
    else:
        try:
            _validate_mock_response(_normalize_mock_response(mock_response, cases), cases)
            status, rows, deterministic, retrieval_used, llm_used = "completed", _mock_rows(cases), True, False, False
        except _MockInputError as error:
            event["schema_invalid_count"] = 1
            event["rejected_invalid_count"] = 1
            event["fallback_count"] = 1
            event["prompt_injection_blocked_count"] = int(error.prompt_injection)
            event["unsupported_statement_count"] = int(error.unsupported_statement)
            status, rows, deterministic, retrieval_used, llm_used = (
                "fallback",
                _fallback_rows(cases, variant_id),
                True,
                False,
                False,
            )

    source_canonical_after = canonical_report_json(report)
    source_hash_after = _sha256(source_canonical_after)
    if source_canonical_after != source_canonical_before:
        raise ValueError("P6.4 source report mutated during advisor review")

    review = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_id": review_id,
        "source_report_id": source_snapshot["report_id"],
        "source_report_canonical_hash": source_hash_before,
        "variant_id": variant_id,
        "status": status,
        "deterministic": deterministic,
        "retrieval_used": retrieval_used,
        "llm_used": llm_used,
        "input_integrity": {
            "input_report_mutated": False,
            "input_report_mutation_count": 0,
            "source_hash_before": source_hash_before,
            "source_hash_after": source_hash_after,
            "source_hash_unchanged": True,
        },
        "truth_invariance": {
            "root_cause_mutation_count": 0,
            "affected_entity_mutation_count": 0,
            "replay_status_mutation_count": 0,
            "claim_class_mutation_count": 0,
            "report_metric_mutation_count": 0,
            "proof_drift_count": 0,
            "proof_contamination_count": 0,
        },
        "safety_summary": _safety_summary(event),
        "review_summary": _review_summary(rows, event),
        "cases": rows,
        "limitations": _top_limitations(),
        "claim_class": "report_only",
        "notes": ["Advisor overlay is review-only and cannot alter the P6.4 source report."],
    }
    validate_review(review)
    return review


def canonical_review_json(review: dict[str, Any]) -> str:
    """Render canonical JSON for a validated P6.5 review overlay."""
    validate_review(review)
    text = json.dumps(review, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if len(text.encode("utf-8")) > _MAX_REVIEW_BYTES:
        raise ValueError("canonical advisor review JSON exceeds the 64 KiB size cap")
    return text


def validate_review(review: dict[str, Any]) -> None:
    """Validate strict schema plus P6.5 review-only invariants."""
    if not isinstance(review, dict):
        raise ValueError("review must be an object")
    reason = validate_schema(load_schema("rtos_diagnosis_advisor_review.schema.json"), review)
    if reason is not None:
        raise ValueError(f"review does not satisfy rtos_diagnosis_advisor_review.schema.json: {reason}")
    _assert_safe_review_strings(review)
    _validate_review_invariants(review)


def build_variant_comparison(reviews: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return non-correctness comparison rows for validated review overlays."""
    rows = []
    for review in reviews:
        validate_review(review)
        summary = review["review_summary"]
        safety = review["safety_summary"]
        rows.append(
            {
                "variant_id": review["variant_id"],
                "status": review["status"],
                "schema_valid": summary["schema_valid"],
                "deterministic_reproducible": summary["deterministic_reproducible"],
                "review_coverage_ratio": summary["review_coverage_ratio"],
                "limitation_coverage_count": summary["limitation_coverage_count"],
                "citation_presence_count": summary["citation_presence_count"],
                "abstention_count": safety["abstention_count"],
                "fallback_count": safety["fallback_count"],
                "adversarial_rejection_count": summary["adversarial_rejection_count"],
            }
        )
    return {"comparison_type": "review_overlay_variant_comparison_v1", "variants": sorted(rows, key=lambda row: row["variant_id"])}


def _allowlisted_cases(report: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for source in report["cases"]:
        cases.append(
            {
                "case_id": source["case_id"],
                "case_kind": source["case_kind"],
                "reference_only": source["reference_only"],
                "replay_pass": source["replay_pass"],
                "evidence_refs_retained_count": source["evidence_refs_retained_count"],
                "evidence_retention_ratio": source["evidence_retention_ratio"],
            }
        )
    return sorted(cases, key=lambda row: row["case_id"])


def _assert_safe_source_identifiers(report_id: str, cases: list[dict[str, Any]]) -> None:
    _assert_safe_text(report_id, "source report_id")
    for case in cases:
        _assert_safe_text(case["case_id"], "source case_id")


def _disabled_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_case_row(case, "advisor_disabled", "disabled", "", [], [], None) for case in cases]


def _template_rows(cases: list[dict[str, Any]], *, status: str = "completed") -> list[dict[str, Any]]:
    return [
        _case_row(
            case,
            "deterministic_template" if status == "completed" else "retrieval_grounded_review",
            status,
            f"Review-only metadata summary for {case['case_kind']}.",
            _case_limitations(case),
            [_p6_4_citation(case["case_id"])],
            None,
        )
        for case in cases
    ]


def _fallback_rows(cases: list[dict[str, Any]], variant_id: str) -> list[dict[str, Any]]:
    return [
        _case_row(
            case,
            variant_id,
            "fallback",
            f"Review-only metadata summary for {case['case_kind']}.",
            _case_limitations(case),
            [_p6_4_citation(case["case_id"])],
            None,
        )
        for case in cases
    ]


def _retrieval_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        case_id = case["case_id"]
        rows.append(
            _case_row(
                case,
                "retrieval_grounded_review",
                "completed",
                f"Review-only metadata summary for {case['case_kind']}.",
                _case_limitations(case),
                [_p6_4_citation(case_id), _retrieval_citation(case_id)],
                None,
            )
        )
    return rows


def _mock_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _case_row(
            case,
            "mock_llm_explanation",
            "completed",
            _MOCK_SUMMARY,
            _case_limitations(case),
            [_p6_4_citation(case["case_id"])],
            None,
        )
        for case in cases
    ]


def _case_row(
    case: dict[str, Any],
    variant_id: str,
    review_status: str,
    summary: str,
    limitations: list[str],
    citations: list[dict[str, Any]],
    abstention_reason: str | None,
) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "case_kind": case["case_kind"],
        "variant_id": variant_id,
        "review_status": review_status,
        "summary": summary,
        "limitations": limitations,
        "evidence_citations": citations,
        "unsupported_statements": [],
        "abstention_reason": abstention_reason,
        "claim_class": "report_only",
    }


def _case_limitations(case: dict[str, Any]) -> list[str]:
    limitations = ["Synthetic-only and metadata-only review overlay."]
    if case["reference_only"] and not case["replay_pass"]:
        limitations.append("Reference-only does not mean replay pass.")
    return sorted(limitations)


def _p6_4_citation(case_id: str) -> dict[str, Any]:
    return {"source": "p6_4_case", "case_id": case_id, "reference": f"p6_4_case:{case_id}", "not_proof": True}


def _retrieval_citation(case_id: str) -> dict[str, Any]:
    return {
        "source": "retrieved_review_reference",
        "case_id": case_id,
        "reference": f"retrieved_review_reference:{case_id}",
        "not_proof": True,
    }


def _validate_retrieval_references(references: Iterable[str] | None, cases: list[dict[str, Any]]) -> None:
    expected = [case["case_id"] for case in cases]
    actual = expected if references is None else list(references)
    if any(not isinstance(value, str) for value in actual) or sorted(actual) != expected:
        raise ValueError("retrieval references must exactly allowlist every P6.4 case_id")


def _normalize_mock_response(
    mock_response: dict[str, Any] | str | None, cases: list[dict[str, Any]]
) -> dict[str, Any]:
    if mock_response is None:
        return {
            "schema_version": MOCK_SCHEMA_VERSION,
            "case_reviews": [
                {"case_id": case["case_id"], "summary": _MOCK_SUMMARY, "limitations": []} for case in cases
            ],
        }
    if isinstance(mock_response, str):
        try:
            parsed = json.loads(mock_response)
        except json.JSONDecodeError as error:
            raise _MockInputError("malformed mock JSON") from error
        if not isinstance(parsed, dict):
            raise _MockInputError("mock JSON must be an object")
        return parsed
    if isinstance(mock_response, dict):
        return mock_response
    raise _MockInputError("mock response must be an object or JSON string")


def _validate_mock_response(payload: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    _assert_safe_mock_content(payload)
    reason = validate_schema(_MOCK_RESPONSE_SCHEMA, payload)
    if reason is not None:
        raise _MockInputError(f"mock response schema invalid: {reason}")
    expected_ids = [case["case_id"] for case in cases]
    actual_ids = [row["case_id"] for row in payload["case_reviews"]]
    if sorted(actual_ids) != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise _MockInputError("mock response case binding invalid")
    for row in payload["case_reviews"]:
        if row["summary"] != _MOCK_SUMMARY or row["limitations"] != []:
            raise _MockInputError("mock response contains unsupported statement", unsupported_statement=True)


def _assert_safe_mock_content(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered_key = str(key).lower()
            if lowered_key in _FORBIDDEN_MOCK_KEYS:
                raise _MockInputError(f"forbidden mock field {key!r}")
            if any(marker in lowered_key for marker in _FORBIDDEN_MOCK_TEXT):
                raise _MockInputError(
                    f"unsafe mock field {key!r}",
                    prompt_injection="ignore previous" in lowered_key or "jailbreak" in lowered_key,
                    unsupported_statement=True,
                )
            if _text_is_unsafe(str(key)):
                raise _MockInputError(f"unsafe mock field {key!r}", unsupported_statement=True)
            _assert_safe_mock_content(child)
    elif isinstance(value, list):
        for child in value:
            _assert_safe_mock_content(child)
    elif isinstance(value, str):
        lower = value.lower()
        if _text_is_unsafe(value):
            raise _MockInputError(
                "unsafe mock content",
                prompt_injection="ignore previous" in lower or "jailbreak" in lower,
                unsupported_statement=True,
            )


def _assert_safe_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or _text_is_unsafe(value):
        raise ValueError(f"{label} is unsafe for review output")


def _assert_safe_review_strings(review: dict[str, Any]) -> None:
    _assert_safe_output_text(review["review_id"], "review_id")
    _assert_safe_output_text(review["source_report_id"], "source_report_id")
    for value in [*review["limitations"], *review["notes"]]:
        _assert_safe_output_text(value, "top-level review text")
    for row in review["cases"]:
        for value in [
            row["case_id"],
            row["case_kind"],
            row["summary"],
            *row["limitations"],
            *row["unsupported_statements"],
        ]:
            _assert_safe_output_text(value, "case review text")
        if row["abstention_reason"] is not None:
            _assert_safe_output_text(row["abstention_reason"], "abstention reason")
        for citation in row["evidence_citations"]:
            for value in (citation["source"], citation["case_id"], citation["reference"]):
                _assert_safe_output_text(value, "citation text")


def _assert_safe_output_text(value: str, label: str) -> None:
    if _text_is_unsafe(value):
        raise ValueError(f"{label} contains unsafe output text")


def _text_is_unsafe(value: str) -> bool:
    lower = value.lower()
    return bool(
        any(marker in lower for marker in _FORBIDDEN_MOCK_TEXT)
        or "sk-" in lower
        or "bearer " in lower
        or "bearer:" in lower
        or _ABSOLUTE_PATH.search(value)
        or _WINDOWS_ABSOLUTE_PATH.search(value)
    )


def _review_summary(rows: list[dict[str, Any]], event: dict[str, int]) -> dict[str, Any]:
    total = len(rows)
    reviewed = sum(row["review_status"] in {"completed", "fallback"} for row in rows)
    abstained = sum(row["review_status"] == "abstained" for row in rows)
    citations = sum(len(row["evidence_citations"]) for row in rows)
    return {
        "cases_total": total,
        "cases_reviewed_count": reviewed,
        "cases_abstained_count": abstained,
        "review_coverage_ratio": 0.0 if total == 0 else reviewed / total,
        "limitation_coverage_count": sum(bool(row["limitations"]) for row in rows),
        "evidence_citation_count": citations,
        "citation_presence_count": sum(bool(row["evidence_citations"]) for row in rows),
        "unsupported_statement_count": event["unsupported_statement_count"],
        "schema_valid": True,
        "deterministic_reproducible": True,
        "adversarial_rejection_count": event["rejected_invalid_count"],
    }


def _empty_event() -> dict[str, int]:
    return {
        "schema_invalid_count": 0,
        "rejected_invalid_count": 0,
        "fallback_count": 0,
        "abstention_count": 0,
        "prompt_injection_blocked_count": 0,
        "unsupported_statement_count": 0,
    }


def _safety_summary(event: dict[str, int]) -> dict[str, int]:
    return {
        "unauthorized_action_accepted_count": 0,
        "executable_command_count": 0,
        "tool_invocation_count": 0,
        "secret_leak_count": 0,
        "absolute_path_leak_count": 0,
        "forbidden_truth_field_count": 0,
        "schema_invalid_count": event["schema_invalid_count"],
        "rejected_invalid_count": event["rejected_invalid_count"],
        "fallback_count": event["fallback_count"],
        "abstention_count": event["abstention_count"],
        "prompt_injection_blocked_count": event["prompt_injection_blocked_count"],
        "unsupported_statement_count": event["unsupported_statement_count"],
    }


def _top_limitations() -> list[str]:
    return [
        "Advisor output is review-only and does not create source facts.",
        "Reference-only review coverage is not replay success.",
        "Synthetic-only metadata remains the sole source scope.",
    ]


def _validate_review_invariants(review: dict[str, Any]) -> None:
    cases = review["cases"]
    _require_non_empty_string(review["review_id"], "review.review_id")
    _require_non_empty_string(review["source_report_id"], "review.source_report_id")
    source_hash = review["source_report_canonical_hash"]
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
        raise ValueError("review source report hash must be lowercase SHA-256")
    case_ids = [row["case_id"] for row in cases]
    if case_ids != sorted(case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("review cases must be unique and sorted by case_id")
    for row in cases:
        if row["case_kind"] not in CASE_KINDS or row["claim_class"] not in {"report_only", "not_claimable"}:
            raise ValueError("review case has an unsupported boundary value")
        if row["variant_id"] != review["variant_id"]:
            raise ValueError("review case variant_id mismatch")
        if row["unsupported_statements"]:
            raise ValueError("review output cannot contain unsupported statements")
        if row["review_status"] in {"completed", "fallback"} and not row["evidence_citations"]:
            raise ValueError("completed or fallback review case requires an allowlisted citation")
        for citation in row["evidence_citations"]:
            expected = (
                f"p6_4_case:{row['case_id']}"
                if citation["source"] == "p6_4_case"
                else f"retrieved_review_reference:{row['case_id']}"
            )
            if citation["case_id"] != row["case_id"] or citation["reference"] != expected or not citation["not_proof"]:
                raise ValueError("review citation is not an allowlisted non-proof case reference")
    if review["input_integrity"]["input_report_mutated"] or review["input_integrity"]["input_report_mutation_count"] != 0:
        raise ValueError("review input mutation invariant violated")
    integrity = review["input_integrity"]
    if not integrity["source_hash_unchanged"] or integrity["source_hash_before"] != integrity["source_hash_after"]:
        raise ValueError("review source hash invariant violated")
    if review["source_report_canonical_hash"] != integrity["source_hash_before"]:
        raise ValueError("review source report hash and input integrity hash differ")
    if not review["deterministic"] or review["llm_used"]:
        raise ValueError("P6.5 review outputs must remain deterministic and non-LLM")
    if any(value != 0 for value in review["truth_invariance"].values()):
        raise ValueError("review truth invariance invariant violated")
    safety = review["safety_summary"]
    for key in (
        "unauthorized_action_accepted_count",
        "executable_command_count",
        "tool_invocation_count",
        "secret_leak_count",
        "absolute_path_leak_count",
        "forbidden_truth_field_count",
    ):
        if safety[key] != 0:
            raise ValueError(f"review safety invariant violated: {key}")
    summary = review["review_summary"]
    reviewed = sum(row["review_status"] in {"completed", "fallback"} for row in cases)
    abstained = sum(row["review_status"] == "abstained" for row in cases)
    citations = sum(len(row["evidence_citations"]) for row in cases)
    if summary["cases_total"] != len(cases) or summary["cases_reviewed_count"] != reviewed:
        raise ValueError("review coverage invariant violated")
    if summary["cases_abstained_count"] != abstained or summary["evidence_citation_count"] != citations:
        raise ValueError("review summary count invariant violated")
    expected_coverage = 0.0 if not cases else reviewed / len(cases)
    if float(summary["review_coverage_ratio"]) != expected_coverage:
        raise ValueError("review coverage ratio invariant violated")
    if summary["limitation_coverage_count"] != sum(bool(row["limitations"]) for row in cases):
        raise ValueError("review limitation coverage invariant violated")
    if summary["citation_presence_count"] != sum(bool(row["evidence_citations"]) for row in cases):
        raise ValueError("review citation presence invariant violated")
    if summary["unsupported_statement_count"] != safety["unsupported_statement_count"]:
        raise ValueError("review unsupported statement invariant violated")
    _validate_variant_state(review, cases, safety, reviewed)


def _validate_variant_state(review: dict[str, Any], cases: list[dict[str, Any]], safety: dict[str, Any], reviewed: int) -> None:
    variant_id = review["variant_id"]
    status = review["status"]
    row_statuses = {row["review_status"] for row in cases}
    if variant_id == "advisor_disabled":
        valid = status == "disabled" and not review["retrieval_used"] and reviewed == 0 and row_statuses == {"disabled"}
    elif variant_id == "deterministic_template":
        valid = status == "completed" and not review["retrieval_used"] and row_statuses == {"completed"}
    elif variant_id == "retrieval_grounded_review":
        valid = (status == "completed" and review["retrieval_used"] and row_statuses == {"completed"}) or (
            status == "fallback" and not review["retrieval_used"] and row_statuses == {"fallback"}
        )
    else:
        valid = (status == "completed" and not review["retrieval_used"] and row_statuses == {"completed"}) or (
            status == "fallback" and not review["retrieval_used"] and row_statuses == {"fallback"}
        )
    if not valid:
        raise ValueError("review variant/status/row-state invariant violated")
    if status == "fallback" and safety["fallback_count"] != 1:
        raise ValueError("fallback invariant violated")


def _require_non_empty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class _MockInputError(ValueError):
    def __init__(
        self, message: str, *, prompt_injection: bool = False, unsupported_statement: bool = False
    ) -> None:
        super().__init__(message)
        self.prompt_injection = prompt_injection
        self.unsupported_statement = unsupported_statement

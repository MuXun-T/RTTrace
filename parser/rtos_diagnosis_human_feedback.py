"""P6.6 synthetic-only, privacy-minimized feedback contracts and aggregation."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Iterable

from parser.rtos_diagnosis_advisor_review import (
    VARIANT_IDS,
    canonical_review_json,
    validate_review,
)
from parser.rtos_diagnosis_report import canonical_report_json, validate_report
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


RECORD_SCHEMA_VERSION = "rtos-diagnosis-human-feedback-record-v1"
SUMMARY_SCHEMA_VERSION = "rtos-diagnosis-human-feedback-summary-v1"
RATING_NAMES = (
    "explanation_clarity",
    "limitation_visibility",
    "citation_usefulness",
    "abstention_appropriateness",
    "review_usefulness",
)
_MAX_BYTES = 64 * 1024
_FORBIDDEN_KEYS = frozenset(
    {
        "name", "full_name", "first_name", "last_name", "email", "phone", "address", "location",
        "precise_location", "ip", "ip_address", "mac_address", "device_id", "advertising_id",
        "username", "student_id", "employee_id", "organization", "employer", "age", "gender",
        "ethnicity", "race", "religion", "disability", "health", "diagnosis", "political_affiliation",
        "union_membership", "raw_comment", "free_text", "transcript", "audio", "image", "video",
        "biometric", "api_key", "token", "secret", "password", "proof_hash_input",
        "proof_digest_write_path", "llm_truth", "advisor_truth", "executable_command", "shell_command",
        "tool_call",
    }
)
_UNSAFE_KEY_PARTS = ("root_cause", "affected_entity", "replay_status", "proof_", "truth", "tool", "command")
_SUMMARY_SAFE_KEYS = frozenset(
    {
        "root_cause_mutation_count", "replay_status_mutation_count", "proof_drift_count",
        "root_cause_correctness_claimable", "replay_correctness_claimable", "proof_correctness_claimable",
    }
)
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)/(?:\S*)|(?:^|\s)[A-Za-z]:[\\/](?:\S*)")
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"\+?\d[\d .()\-]{6,}\d")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UNSAFE_TEXT = ("ignore previous", "system prompt", "developer message", "jailbreak", "bearer ", "sk-")
_SUMMARY_LIMITATIONS = [
    "Synthetic feedback validates this pipeline only.",
    "Descriptive ratings do not establish usability, human effect, or correctness.",
]
_SUMMARY_NOTES = ["No real participant data, free text, demographics, or inferential statistics are included."]


def canonical_feedback_record_json(record: dict[str, Any]) -> str:
    """Render one validated feedback record in canonical JSON."""
    validate_feedback_record(record)
    return _canonical_json(record, label="feedback record")


def canonical_feedback_summary_json(summary: dict[str, Any]) -> str:
    """Render one validated descriptive-only summary in canonical JSON."""
    validate_feedback_summary(summary)
    return _canonical_json(summary, label="feedback summary")


def validate_feedback_record(
    record: dict[str, Any], *, report: dict[str, Any] | None = None, reviews: Iterable[dict[str, Any]] | None = None
) -> None:
    """Fail closed for anything other than a strictly synthetic P6.6 record."""
    if not isinstance(record, dict):
        raise ValueError("feedback record must be an object")
    _assert_safe_content(record, label="feedback record")
    _validate_schema("rtos_diagnosis_human_feedback_record.schema.json", record, label="feedback record")
    for key in ("response_id", "source_report_id", "source_review_id", "case_id"):
        _require_safe_id(record[key], f"feedback record {key}")
    for key in ("source_report_canonical_hash", "source_review_canonical_hash"):
        _require_sha256(record[key], f"feedback record {key}")
    if record["data_source"] != "synthetic" or record["synthetic"] is not True:
        raise ValueError("only synthetic feedback records are accepted in P6.6")
    if record["collection_scope"] != {"real_participant": False, "pipeline_validation_only": True}:
        raise ValueError("feedback must be synthetic pipeline validation only")
    if record["consent"] != {
        "required": False,
        "granted": False,
        "form_version": "not_applicable",
        "withdrawal_supported": False,
        "consent_not_applicable": True,
    }:
        raise ValueError("synthetic feedback must use the fixed consent-not-applicable contract")
    if record["privacy"] != {
        "contains_direct_identifiers": False,
        "contains_free_text": False,
        "contains_demographics": False,
        "pii_scan_passed": True,
    }:
        raise ValueError("feedback privacy declaration is invalid")
    if record["retention"] != {
        "retention_class": "synthetic_fixture_only",
        "deletion_supported": True,
        "repository_storage_allowed": True,
    }:
        raise ValueError("synthetic feedback retention contract is invalid")
    if record["claim_class"] not in {"report_only", "not_claimable"}:
        raise ValueError("feedback claim class is invalid")
    if sorted(set(record["coded_comment_labels"])) != sorted(record["coded_comment_labels"]):
        raise ValueError("coded_comment_labels must be unique and sorted")
    if report is not None or reviews is not None:
        if report is None or reviews is None:
            raise ValueError("report and reviews must be supplied together for source verification")
        context = _source_context(report, reviews)
        _assert_record_source(record, context)


def build_feedback_summary(
    records: Iterable[dict[str, Any]],
    *,
    report: dict[str, Any],
    reviews: Iterable[dict[str, Any]],
    summary_id: str = "phase6-p6-6-human-feedback-summary",
) -> dict[str, Any]:
    """Aggregate valid synthetic feedback without modifying report, reviews, or records."""
    if not isinstance(summary_id, str) or not summary_id:
        raise ValueError("summary_id must be a non-empty string")
    raw_records = list(records)
    records_snapshot = _snapshot(raw_records)
    report_snapshot = _snapshot(report)
    review_rows = list(reviews)
    reviews_snapshot = _snapshot(review_rows)
    context = _source_context(report, review_rows)

    response_ids: set[str] = set()
    for record in raw_records:
        validate_feedback_record(record, report=report, reviews=review_rows)
        response_id = record["response_id"]
        if response_id in response_ids:
            raise ValueError(f"duplicate response_id {response_id!r}")
        response_ids.add(response_id)

    _assert_unchanged("records", records_snapshot, raw_records)
    _assert_unchanged("report", report_snapshot, report)
    _assert_unchanged("reviews", reviews_snapshot, review_rows)
    ordered_records = sorted(raw_records, key=lambda row: row["response_id"])
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "summary_id": summary_id,
        "source_feedback_set_hash": _sha256(_canonical_json(ordered_records, label="feedback set")),
        "source_report_hash": context["report_hash"],
        "source_review_hashes": context["review_hashes"],
        "pipeline_validation_only": True,
        "contains_real_participant_data": False,
        "consent_summary": {
            "missing_consent_count": 0,
            "denied_consent_count": 0,
            "accepted_consent_count": len(ordered_records),
            "consent_violation_count": 0,
        },
        "privacy_summary": {key: 0 for key in ("direct_identifier_count", "free_text_count", "demographic_field_count", "secret_leak_count", "absolute_path_leak_count", "privacy_rejection_count")},
        "record_counts": {
            "records_total": len(ordered_records), "records_accepted": len(ordered_records), "records_rejected": 0,
            "synthetic_records_count": len(ordered_records), "consented_manual_records_count": 0,
            "real_participant_records_count": 0,
        },
        "variant_metrics": [
            _variant_metric(variant_id, ordered_records)
            for variant_id in sorted(VARIANT_IDS)
        ],
        "rating_metrics": {name: _rating_metric([row["ratings"][name] for row in ordered_records]) for name in RATING_NAMES},
        "structured_flag_metrics": _flag_metrics(ordered_records),
        "integrity_metrics": {
            "source_hash_mismatch_count": 0, "source_mutation_count": 0, "root_cause_mutation_count": 0,
            "replay_status_mutation_count": 0, "claim_class_mutation_count": 0, "proof_drift_count": 0,
            "invalid_record_count": 0, "duplicate_response_id_count": 0,
        },
        "claim_boundary": {
            "human_effect_claimable": False, "usability_improvement_claimable": False,
            "diagnosis_correctness_claimable": False, "root_cause_correctness_claimable": False,
            "replay_correctness_claimable": False, "proof_correctness_claimable": False,
            "descriptive_pipeline_only": True,
        },
        "limitations": _SUMMARY_LIMITATIONS,
        "notes": _SUMMARY_NOTES,
    }
    validate_feedback_summary(summary)
    return summary


def validate_feedback_summary(summary: dict[str, Any]) -> None:
    """Validate the output-only summary and its non-claimable invariants."""
    if not isinstance(summary, dict):
        raise ValueError("feedback summary must be an object")
    _assert_safe_content(summary, label="feedback summary", allow_summary_keys=True)
    _validate_schema("rtos_diagnosis_human_feedback_summary.schema.json", summary, label="feedback summary")
    _require_safe_id(summary["summary_id"], "feedback summary summary_id")
    for key in ("source_feedback_set_hash", "source_report_hash"):
        _require_sha256(summary[key], f"feedback summary {key}")
    if summary["limitations"] != _SUMMARY_LIMITATIONS or summary["notes"] != _SUMMARY_NOTES:
        raise ValueError("feedback summary must use the fixed P6.6 descriptive-only text")
    counts = summary["record_counts"]
    if counts["records_total"] != counts["records_accepted"] or counts["records_rejected"] != 0:
        raise ValueError("P6.6 summary must be fail-closed with accepted synthetic records only")
    if counts["synthetic_records_count"] != counts["records_accepted"] or any(
        counts[key] != 0 for key in ("consented_manual_records_count", "real_participant_records_count")
    ):
        raise ValueError("P6.6 summary cannot include manual or real participant records")
    if summary["pipeline_validation_only"] is not True or summary["contains_real_participant_data"] is not False:
        raise ValueError("P6.6 summary must remain synthetic pipeline validation only")
    if any(value != 0 for section in (summary["privacy_summary"], summary["integrity_metrics"]) for value in section.values()):
        raise ValueError("P6.6 summary privacy and integrity counters must remain zero")
    if any(value is not False for key, value in summary["claim_boundary"].items() if key != "descriptive_pipeline_only") or summary["claim_boundary"]["descriptive_pipeline_only"] is not True:
        raise ValueError("P6.6 claim boundary is invalid")
    if len(summary["source_review_hashes"]) != len(VARIANT_IDS):
        raise ValueError("summary must reference exactly four P6.5 variants")
    for row in summary["source_review_hashes"]:
        _require_safe_id(row["review_id"], "source review_id")
        _require_sha256(row["canonical_hash"], "source review canonical_hash")
    if [row["variant_id"] for row in summary["variant_metrics"]] != sorted(VARIANT_IDS):
        raise ValueError("variant metrics must include the four variants in canonical order")
    for row in summary["variant_metrics"]:
        if set(row["rating_metrics"]) != set(RATING_NAMES):
            raise ValueError("each variant must contain exactly the five approved rating metrics")
        for name in RATING_NAMES:
            metric = row["rating_metrics"][name]
            if metric["count"] != row["accepted_record_count"]:
                raise ValueError(f"variant rating metric {name!r} count must equal accepted_record_count")
            _validate_rating_metric(metric, f"{row['variant_id']}.{name}")
    if set(summary["rating_metrics"]) != set(RATING_NAMES):
        raise ValueError("summary must contain exactly the five approved rating metrics")
    for name in RATING_NAMES:
        metric = summary["rating_metrics"].get(name)
        if metric is None:
            raise ValueError(f"missing rating metric {name!r}")
        _validate_rating_metric(metric, name)


def _source_context(report: dict[str, Any], reviews: Iterable[dict[str, Any]]) -> dict[str, Any]:
    validate_report(report)
    report_before = canonical_report_json(report)
    report_hash = _sha256(report_before)
    report_snapshot = json.loads(report_before)
    review_rows = list(reviews)
    if len(review_rows) != len(VARIANT_IDS):
        raise ValueError("exactly four P6.5 review variants are required")
    review_hashes = []
    review_by_variant: dict[str, dict[str, Any]] = {}
    for review in review_rows:
        validate_review(review)
        canonical = canonical_review_json(review)
        if review["source_report_id"] != report_snapshot["report_id"] or review["source_report_canonical_hash"] != report_hash:
            raise ValueError("review source report reference does not match P6.4 report")
        if review["variant_id"] in review_by_variant:
            raise ValueError("reviews must have unique variant_id values")
        review_by_variant[review["variant_id"]] = review
        review_hashes.append({"review_id": review["review_id"], "variant_id": review["variant_id"], "canonical_hash": _sha256(canonical)})
    if set(review_by_variant) != set(VARIANT_IDS):
        raise ValueError("reviews must cover the four P6.5 variants")
    if canonical_report_json(report) != report_before:
        raise ValueError("P6.4 report mutated during feedback source validation")
    return {
        "report_id": report_snapshot["report_id"], "report_hash": report_hash,
        "case_ids": {row["case_id"] for row in report_snapshot["cases"]},
        "review_by_variant": review_by_variant,
        "review_hashes": sorted(review_hashes, key=lambda row: row["variant_id"]),
    }


def _assert_record_source(record: dict[str, Any], context: dict[str, Any]) -> None:
    if record["source_report_id"] != context["report_id"] or record["source_report_canonical_hash"] != context["report_hash"]:
        raise ValueError("feedback source report hash mismatch")
    review = context["review_by_variant"].get(record["variant_id"])
    if review is None or record["source_review_id"] != review["review_id"]:
        raise ValueError("feedback source review reference mismatch")
    review_hash = _sha256(canonical_review_json(review))
    if record["source_review_canonical_hash"] != review_hash:
        raise ValueError("feedback source review hash mismatch")
    if record["case_id"] not in context["case_ids"]:
        raise ValueError("feedback case_id is not in the P6.4 report")
    if record["case_id"] not in {row["case_id"] for row in review["cases"]}:
        raise ValueError("feedback case_id is not in the P6.5 review")


def _rating_metric(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"count": 0, "mean": None, "minimum": None, "maximum": None}
    return {"count": len(values), "mean": sum(values) / len(values), "minimum": min(values), "maximum": max(values)}


def _variant_metric(variant_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in records if row["variant_id"] == variant_id]
    return {
        "variant_id": variant_id,
        "accepted_record_count": len(rows),
        "rating_metrics": {name: _rating_metric([row["ratings"][name] for row in rows]) for name in RATING_NAMES},
    }


def _validate_rating_metric(metric: dict[str, Any], name: str) -> None:
    count = metric["count"]
    if count == 0:
        if any(metric[key] is not None for key in ("mean", "minimum", "maximum")):
            raise ValueError(f"empty rating metric {name!r} must use null values")
    elif metric["mean"] is None or metric["minimum"] is None or metric["maximum"] is None:
        raise ValueError(f"non-empty rating metric {name!r} cannot use null values")


def _flag_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    flags = [record["structured_flags"] for record in records]
    return {
        "claim_boundary_understood": {value: sum(row["claim_boundary_understood"] == value for row in flags) for value in ("yes", "no", "unsure")},
        "unsupported_statement_observed_count": sum(row["unsupported_statement_observed"] for row in flags),
        "explanation_too_verbose_count": sum(row["explanation_too_verbose"] for row in flags),
        "explanation_too_brief_count": sum(row["explanation_too_brief"] for row in flags),
        "limitation_unclear_count": sum(row["limitation_unclear"] for row in flags),
        "citation_unclear_count": sum(row["citation_unclear"] for row in flags),
    }


def _assert_safe_content(value: Any, *, label: str, allow_summary_keys: bool = False) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_KEYS or (
                any(part in lowered for part in _UNSAFE_KEY_PARTS)
                and not (allow_summary_keys and lowered in _SUMMARY_SAFE_KEYS)
            ):
                raise ValueError(f"{label} contains forbidden field {key!r}")
            _assert_safe_content(child, label=label, allow_summary_keys=allow_summary_keys)
    elif isinstance(value, list):
        for child in value:
            _assert_safe_content(child, label=label, allow_summary_keys=allow_summary_keys)
    elif isinstance(value, str) and _unsafe_string(value):
        raise ValueError(f"{label} contains unsafe text")


def _unsafe_string(value: str) -> bool:
    lowered = value.lower()
    is_sha256 = bool(re.fullmatch(r"[0-9a-f]{64}", lowered))
    return bool(_ABSOLUTE_PATH.search(value) or _EMAIL.search(value) or (not is_sha256 and _PHONE.search(value)) or any(marker in lowered for marker in _UNSAFE_TEXT))


def _require_safe_id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a non-empty structured identifier")


def _require_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def _validate_schema(name: str, value: Any, *, label: str) -> None:
    reason = validate_schema(load_schema(name), value)
    if reason is not None:
        raise ValueError(f"{label} does not satisfy {name}: {reason}")


def _canonical_json(value: Any, *, label: str) -> str:
    text = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise ValueError(f"canonical {label} JSON exceeds the 64 KiB size cap")
    return text


def _snapshot(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _assert_unchanged(label: str, before: str, after: Any) -> None:
    if _snapshot(after) != before:
        raise ValueError(f"{label} mutated during feedback aggregation")


def _sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()

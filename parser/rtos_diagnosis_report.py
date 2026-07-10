"""Deterministic, synthetic-only P6.4 diagnosis report builder."""

from __future__ import annotations

import json
from typing import Any, Iterable

from parser.rtos_diagnosis_replay import (
    EQUIVALENCE_SCOPE,
    FORBIDDEN_FIELD_NAMES,
    REPLAY_STATUSES,
    validate_suite,
)
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


REPORT_SCHEMA_VERSION = "rtos-diagnosis-report-v1"
SOURCE_REPLAY_MODE = "synthetic_replay"
VALIDATION_SCOPE = "synthetic_only"
REPORT_CLAIM_CLASS = "report_only"
MAX_CANONICAL_REPORT_BYTES = 64 * 1024
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
CLAIM_CLASSES = frozenset({"claimable", "report_only", "not_claimable"})
CLAIM_TOPICS = frozenset(
    {
        "top_k_root_cause",
        "human_helpfulness",
        "llm_explanation",
        "synthetic_benchmark",
        "p6_2_plus_approval",
        "stub_suite_status",
        "schema_validity",
        "phase6_scope_lock",
    }
)
OMITTED_OUTPUT_FIELD_NAMES = frozenset(
    {
        "full_diagnosis",
        "replay_diagnosis",
        "source_records",
        "full_proof_facts",
        "replay_proof_facts",
        "proof_facts",
    }
)
CLAIM_CLASS_RANK = {"not_claimable": 0, "report_only": 1, "claimable": 2}
TOP_LEVEL_LIMITATIONS = tuple(
    sorted(
        {
            "Metadata-only replay equivalence; no full trace reconstruction or evidence package replay.",
            "Reference-only closure never counts as replay_pass even with retention ratio 1.0.",
            "Synthetic-only frozen P6.2 fixtures; not real RTOS traces or generalization evidence.",
            "Zero proof drift here means no supplied deterministic facts diverged; it does not prove proof artifact parity.",
            "No advisor, LLM, hardware, proof correctness, or top-k accuracy claim is established.",
        }
    )
)
TOP_LEVEL_NOTES = tuple(
    sorted(
        {
            "Cases are sorted by case_id and canonical JSON uses sorted keys.",
            "If generated_at is omitted, the report copies suite.generated_at and never reads current time.",
            "Raw full_diagnosis, replay_diagnosis, source_records, and proof facts are intentionally omitted.",
        }
    )
)


def build_diagnosis_report(
    suite: dict[str, Any],
    replay_results: Iterable[dict[str, Any]],
    *,
    report_id: str = "phase6-p6-4-diagnosis-report",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic synthetic replay report without mutating inputs."""
    _require_non_empty_string(report_id, "report_id")
    if generated_at is not None:
        _require_non_empty_string(generated_at, "generated_at")

    raw_results = list(replay_results)
    suite_snapshot = _json_snapshot(suite, label="suite")
    results_snapshot = _json_snapshot(raw_results, label="replay_results")

    _validate_suite_payload(suite)
    source_case_claim_counts = _case_claim_counts(suite["cases"])
    result_by_case_id = _normalize_results(raw_results, suite["cases"])

    report_cases = [
        _build_case_entry(case, result_by_case_id[str(case["case_id"])])
        for case in sorted(suite["cases"], key=lambda row: str(row["case_id"]))
    ]

    metrics = _build_metrics(suite, report_cases)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "generated_at": generated_at if generated_at is not None else suite.get("generated_at"),
        "source_suite": {
            "suite_id": str(suite["suite_id"]),
            "schema_version": str(suite["schema_version"]),
            "generated_at": suite.get("generated_at"),
            "case_count": len(suite["cases"]),
            "case_claimable_count": source_case_claim_counts["claimable"],
            "case_report_only_count": source_case_claim_counts["report_only"],
            "case_not_claimable_count": source_case_claim_counts["not_claimable"],
        },
        "source_replay_mode": SOURCE_REPLAY_MODE,
        "scope": {
            "validation_scope": VALIDATION_SCOPE,
            "equivalence_scope": EQUIVALENCE_SCOPE,
            "claim_class": REPORT_CLAIM_CLASS,
            "description": "Deterministic synthetic metadata replay report for the frozen P6.2 diagnosis suite.",
        },
        "summary": metrics["summary"],
        "case_coverage": metrics["case_coverage"],
        "preservation_metrics": metrics["preservation_metrics"],
        "replay_status_metrics": metrics["replay_status_metrics"],
        "integrity_metrics": metrics["integrity_metrics"],
        "claim_boundary": metrics["claim_boundary"],
        "limitations": list(TOP_LEVEL_LIMITATIONS),
        "cases": report_cases,
        "notes": list(TOP_LEVEL_NOTES),
    }

    _assert_snapshot_unchanged("suite", suite_snapshot, suite)
    _assert_snapshot_unchanged("replay_results", results_snapshot, raw_results)
    validate_report(report)
    return report


def canonical_report_json(report: dict[str, Any]) -> str:
    """Render canonical JSON for a validated report."""
    validate_report(report)
    text = _render_canonical_json(report)
    _assert_canonical_size(text)
    return text


def validate_report(report: dict[str, Any]) -> None:
    """Validate report schema, invariants, ordering, and size cap."""
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    _assert_no_forbidden_fields(report, label="report")
    _assert_no_omitted_output_fields(report, label="report")
    _validate_against_schema("rtos_diagnosis_report.schema.json", report, label="report")
    _assert_report_invariants(report)
    _assert_canonical_size(_render_canonical_json(report))


def _validate_suite_payload(suite: dict[str, Any]) -> None:
    if not isinstance(suite, dict):
        raise ValueError("suite must be an object")
    _assert_no_forbidden_fields(suite, label="suite")
    _validate_against_schema("rtos_diagnosis_suite.schema.json", suite, label="suite")
    validate_suite(suite)


def _normalize_results(
    replay_results: list[dict[str, Any]],
    suite_cases: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    case_by_id = {str(case["case_id"]): case for case in suite_cases}
    result_by_case_id: dict[str, dict[str, Any]] = {}
    for row in replay_results:
        normalized = _normalize_result(row, case_by_id)
        case_id = normalized["case_id"]
        if case_id in result_by_case_id:
            raise ValueError(f"duplicate replay result for case_id {case_id!r}")
        result_by_case_id[case_id] = normalized

    missing = sorted(case_id for case_id in case_by_id if case_id not in result_by_case_id)
    if missing:
        raise ValueError(f"missing replay results for case_ids {missing!r}")
    extras = sorted(case_id for case_id in result_by_case_id if case_id not in case_by_id)
    if extras:
        raise ValueError(f"unexpected replay results for case_ids {extras!r}")
    return result_by_case_id


def _normalize_result(row: dict[str, Any], case_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("each replay result must be an object")
    _assert_no_forbidden_fields(row, label="replay_result")

    case_id = _require_non_empty_string(row.get("case_id"), "replay_result.case_id")
    case = case_by_id.get(case_id)
    if case is None:
        raise ValueError(f"unexpected replay result case_id {case_id!r}")

    case_kind = _require_enum(row.get("case_kind"), CASE_KINDS, "replay_result.case_kind")
    if case_kind != str(case["case_kind"]):
        raise ValueError(f"case_kind mismatch for case_id {case_id!r}")
    validation_mode = _require_non_empty_string(row.get("validation_mode"), "replay_result.validation_mode")
    if validation_mode != SOURCE_REPLAY_MODE:
        raise ValueError(f"unsupported validation_mode for case_id {case_id!r}")
    equivalence_scope = _require_non_empty_string(row.get("equivalence_scope"), "replay_result.equivalence_scope")
    if equivalence_scope != EQUIVALENCE_SCOPE:
        raise ValueError(f"unsupported equivalence_scope for case_id {case_id!r}")

    replay_status = _require_enum(row.get("replay_status"), REPLAY_STATUSES, "replay_result.replay_status")
    replay_pass = _require_bool(row.get("replay_pass"), "replay_result.replay_pass")
    reference_only = _require_bool(row.get("reference_only"), "replay_result.reference_only")
    diagnosis_preserved = _require_bool(row.get("diagnosis_preserved"), "replay_result.diagnosis_preserved")
    case_id_retained = _require_bool(row.get("case_id_retained"), "replay_result.case_id_retained")
    root_cause_retained = _require_bool(row.get("root_cause_retained"), "replay_result.root_cause_retained")
    affected_entity_retained = _require_bool(
        row.get("affected_entity_retained"), "replay_result.affected_entity_retained"
    )
    closure_retained = _require_bool(row.get("closure_retained"), "replay_result.closure_retained")
    deterministic_signal_retained = _require_bool(
        row.get("deterministic_signal_retained"), "replay_result.deterministic_signal_retained"
    )
    proof_drift_count = _require_non_negative_int(row.get("proof_drift_count"), "replay_result.proof_drift_count")
    failure_reasons = _sorted_unique_strings(row.get("failure_reasons"), "replay_result.failure_reasons")
    evidence_expected_count = _count_refs(row.get("evidence_refs_expected"), "replay_result.evidence_refs_expected")
    evidence_retained_count = _count_refs(row.get("evidence_refs_retained"), "replay_result.evidence_refs_retained")
    if evidence_retained_count > evidence_expected_count:
        raise ValueError(f"retained evidence exceeds expected evidence for case_id {case_id!r}")
    evidence_retention_ratio = _require_ratio(
        row.get("evidence_retention_ratio"), "replay_result.evidence_retention_ratio"
    )
    expected_ratio = (
        0.0
        if replay_status == "not_evaluated"
        else 1.0
        if evidence_expected_count == 0
        else evidence_retained_count / evidence_expected_count
    )
    if not _float_equal(evidence_retention_ratio, expected_ratio):
        raise ValueError(f"evidence retention ratio mismatch for case_id {case_id!r}")

    closure_mode = row.get("closure_mode")
    if closure_mode is not None:
        _require_non_empty_string(closure_mode, "replay_result.closure_mode")
    result_claim_class = _require_enum(row.get("claim_class"), CLAIM_CLASSES, "replay_result.claim_class")
    diagnosis_expected = bool(
        case_id_retained and root_cause_retained and affected_entity_retained and deterministic_signal_retained
    )
    if diagnosis_preserved != diagnosis_expected:
        raise ValueError(f"diagnosis_preserved mismatch for case_id {case_id!r}")

    _assert_result_status_consistency(
        case_id=case_id,
        replay_status=replay_status,
        replay_pass=replay_pass,
        reference_only=reference_only,
        closure_mode=closure_mode,
        diagnosis_preserved=diagnosis_preserved,
        closure_retained=closure_retained,
        evidence_retention_ratio=evidence_retention_ratio,
        proof_drift_count=proof_drift_count,
    )
    if replay_status in {"pass", "reference_only"} and failure_reasons:
        raise ValueError(f"preserved replay status cannot retain failure reasons for case_id {case_id!r}")
    if replay_status == "not_evaluated":
        if (
            diagnosis_preserved
            or case_id_retained
            or root_cause_retained
            or affected_entity_retained
            or closure_retained
            or deterministic_signal_retained
            or evidence_expected_count != 0
            or evidence_retained_count != 0
            or not _float_equal(evidence_retention_ratio, 0.0)
        ):
            raise ValueError(f"not_evaluated result must remain empty for case_id {case_id!r}")

    claim_class = _resolved_claim_class(str(case["claim_class"]), result_claim_class, replay_status)
    tamper_detected = any(reason == "representation_invalid:source_record_checksum" for reason in failure_reasons)
    fail_closed = replay_status in {"fail", "not_evaluated"} or proof_drift_count > 0 or tamper_detected
    return {
        "case_id": case_id,
        "case_kind": case_kind,
        "replay_status": replay_status,
        "replay_pass": replay_pass,
        "reference_only": reference_only,
        "diagnosis_metadata_retained": diagnosis_preserved,
        "root_cause_retained": root_cause_retained,
        "affected_entity_retained": affected_entity_retained,
        "evidence_refs_expected_count": evidence_expected_count,
        "evidence_refs_retained_count": evidence_retained_count,
        "evidence_retention_ratio": evidence_retention_ratio,
        "closure_metadata_retained": closure_retained,
        "deterministic_signal_retained": deterministic_signal_retained,
        "proof_drift_count": proof_drift_count,
        "tamper_detected": tamper_detected,
        "fail_closed": fail_closed,
        "claim_class": claim_class,
        "failure_reasons": failure_reasons,
    }


def _assert_result_status_consistency(
    *,
    case_id: str,
    replay_status: str,
    replay_pass: bool,
    reference_only: bool,
    closure_mode: str | None,
    diagnosis_preserved: bool,
    closure_retained: bool,
    evidence_retention_ratio: float,
    proof_drift_count: int,
) -> None:
    if replay_status == "pass":
        if not replay_pass or reference_only:
            raise ValueError(f"pass status flags inconsistent for case_id {case_id!r}")
        if closure_mode is None:
            raise ValueError(f"pass status requires closure metadata for case_id {case_id!r}")
        if closure_mode == "reference_only":
            raise ValueError(f"pass status cannot use reference_only closure for case_id {case_id!r}")
    elif replay_status == "reference_only":
        if replay_pass or not reference_only:
            raise ValueError(f"reference_only status flags inconsistent for case_id {case_id!r}")
        if closure_mode != "reference_only":
            raise ValueError(f"reference_only status requires reference_only closure for case_id {case_id!r}")
    elif replay_status == "not_evaluated":
        if replay_pass or reference_only:
            raise ValueError(f"not_evaluated status flags inconsistent for case_id {case_id!r}")
    else:
        if replay_pass or reference_only:
            raise ValueError(f"fail status flags inconsistent for case_id {case_id!r}")

    if replay_status in {"pass", "reference_only"}:
        if not diagnosis_preserved or not closure_retained:
            raise ValueError(f"preserved replay status lacks retained metadata for case_id {case_id!r}")
        if not _float_equal(evidence_retention_ratio, 1.0):
            raise ValueError(f"preserved replay status lacks full evidence retention for case_id {case_id!r}")
    if proof_drift_count != 0 and replay_pass:
        raise ValueError(f"proof drift cannot coexist with replay_pass for case_id {case_id!r}")
    if replay_status == "reference_only" and replay_pass:
        raise ValueError(f"reference_only cannot be replay_pass for case_id {case_id!r}")
    if replay_status == "not_evaluated" and replay_pass:
        raise ValueError(f"not_evaluated cannot be replay_pass for case_id {case_id!r}")


def _resolved_claim_class(source_claim_class: str, result_claim_class: str, replay_status: str) -> str:
    _require_enum(source_claim_class, CLAIM_CLASSES, "suite.case.claim_class")
    _require_enum(result_claim_class, CLAIM_CLASSES, "replay_result.claim_class")
    if replay_status == "not_evaluated":
        return "not_claimable"
    if CLAIM_CLASS_RANK[result_claim_class] < CLAIM_CLASS_RANK[source_claim_class]:
        return result_claim_class
    return source_claim_class


def _build_case_entry(case: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": str(case["case_id"]),
        "case_kind": str(case["case_kind"]),
        "replay_status": normalized["replay_status"],
        "replay_pass": normalized["replay_pass"],
        "reference_only": normalized["reference_only"],
        "diagnosis_metadata_retained": normalized["diagnosis_metadata_retained"],
        "root_cause_retained": normalized["root_cause_retained"],
        "affected_entity_retained": normalized["affected_entity_retained"],
        "evidence_refs_expected_count": normalized["evidence_refs_expected_count"],
        "evidence_refs_retained_count": normalized["evidence_refs_retained_count"],
        "evidence_retention_ratio": normalized["evidence_retention_ratio"],
        "closure_metadata_retained": normalized["closure_metadata_retained"],
        "deterministic_signal_retained": normalized["deterministic_signal_retained"],
        "proof_drift_count": normalized["proof_drift_count"],
        "tamper_detected": normalized["tamper_detected"],
        "fail_closed": normalized["fail_closed"],
        "claim_class": normalized["claim_class"],
        "failure_reasons": list(normalized["failure_reasons"]),
        "limitations": _case_limitations(normalized["replay_status"]),
    }


def _build_metrics(suite: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    total = len(cases)
    case_status_counts = {status: sum(case["replay_status"] == status for case in cases) for status in sorted(REPLAY_STATUSES)}
    case_claim_counts = _case_claim_counts(cases)
    evidence_expected_total = sum(int(case["evidence_refs_expected_count"]) for case in cases)
    evidence_retained_total = sum(int(case["evidence_refs_retained_count"]) for case in cases)
    proof_drift_total = sum(int(case["proof_drift_count"]) for case in cases)
    preservation_complete_count = sum(1 for case in cases if _case_preservation_complete(case))
    preservation_complete = bool(total > 0 and preservation_complete_count == total)
    all_required_cases_evaluated = case_status_counts["not_evaluated"] == 0
    no_replay_failures = case_status_counts["fail"] == 0
    evaluated_count = total - case_status_counts["not_evaluated"]
    invalid_result_count = 0
    inconsistent_status_count = 0
    forbidden_field_count = 0
    input_mutation_count = 0
    all_replay_passed = bool(
        total > 0
        and case_status_counts["pass"] == total
        and case_status_counts["fail"] == 0
        and case_status_counts["reference_only"] == 0
        and case_status_counts["not_evaluated"] == 0
        and proof_drift_total == 0
        and invalid_result_count == 0
        and inconsistent_status_count == 0
    )
    evidence_retention_ratio = 1.0 if evidence_expected_total == 0 else evidence_retained_total / evidence_expected_total
    claim_boundary = dict(suite["claim_boundary"])
    claimable_topics = _boundary_topics(claim_boundary, "claimable")
    report_only_topics = _boundary_topics(claim_boundary, "report_only")
    not_claimable_topics = _boundary_topics(claim_boundary, "not_claimable")
    required_case_kinds = sorted(set(str(kind) for kind in suite["minimum_case_kinds"]))
    observed_case_kinds = sorted({str(case["case_kind"]) for case in cases})
    missing_case_kinds = sorted(set(required_case_kinds) - set(observed_case_kinds))
    case_kind_coverage_count = len(set(required_case_kinds) & set(observed_case_kinds))
    case_kind_coverage_ratio = 1.0 if not required_case_kinds else case_kind_coverage_count / len(required_case_kinds)
    synthetic_case_count = total
    real_hardware_case_count = 0
    return {
        "summary": {
            "cases_total": total,
            "replay_pass_count": case_status_counts["pass"],
            "replay_fail_count": case_status_counts["fail"],
            "reference_only_count": case_status_counts["reference_only"],
            "not_evaluated_count": case_status_counts["not_evaluated"],
            "all_required_cases_evaluated": all_required_cases_evaluated,
            "all_replay_passed": all_replay_passed,
            "no_replay_failures": no_replay_failures,
            "preservation_complete": preservation_complete,
            "evidence_retention_ratio": evidence_retention_ratio,
            "proof_drift_count": proof_drift_total,
            "synthetic_case_count": synthetic_case_count,
            "real_hardware_case_count": real_hardware_case_count,
            "claimable_count": case_claim_counts["claimable"],
            "report_only_count": case_claim_counts["report_only"],
            "not_claimable_count": case_claim_counts["not_claimable"],
        },
        "case_coverage": {
            "cases_total": total,
            "required_case_kinds": required_case_kinds,
            "observed_case_kinds": observed_case_kinds,
            "case_kind_coverage_count": case_kind_coverage_count,
            "case_kind_coverage_ratio": case_kind_coverage_ratio,
            "missing_case_kinds": missing_case_kinds,
            "case_kinds_expected_count": len(set(suite["minimum_case_kinds"])),
            "case_kinds_present_count": len({str(case["case_kind"]) for case in cases}),
            "evaluated_count": evaluated_count,
            "evaluated_ratio": 1.0 if total == 0 else evaluated_count / total,
            "reference_only_count": case_status_counts["reference_only"],
            "not_evaluated_count": case_status_counts["not_evaluated"],
            "all_required_cases_evaluated": all_required_cases_evaluated,
        },
        "preservation_metrics": {
            "diagnosis_metadata_retained_count": sum(1 for case in cases if case["diagnosis_metadata_retained"]),
            "root_cause_retained_count": sum(1 for case in cases if case["root_cause_retained"]),
            "affected_entity_retained_count": sum(1 for case in cases if case["affected_entity_retained"]),
            "closure_metadata_retained_count": sum(1 for case in cases if case["closure_metadata_retained"]),
            "deterministic_signal_retained_count": sum(1 for case in cases if case["deterministic_signal_retained"]),
            "evidence_refs_expected_count": evidence_expected_total,
            "evidence_refs_retained_count": evidence_retained_total,
            "evidence_retention_ratio": evidence_retention_ratio,
            "preservation_complete_count": preservation_complete_count,
            "preservation_complete_ratio": 1.0 if total == 0 else preservation_complete_count / total,
            "preservation_complete": preservation_complete,
        },
        "replay_status_metrics": {
            "replay_pass_count": case_status_counts["pass"],
            "replay_fail_count": case_status_counts["fail"],
            "reference_only_count": case_status_counts["reference_only"],
            "not_evaluated_count": case_status_counts["not_evaluated"],
            "evaluated_count": evaluated_count,
            "all_required_cases_evaluated": all_required_cases_evaluated,
            "no_replay_failures": no_replay_failures,
            "all_replay_passed": all_replay_passed,
        },
        "integrity_metrics": {
            "proof_drift_count": proof_drift_total,
            "tamper_detected_count": sum(1 for case in cases if case["tamper_detected"]),
            "fail_closed_count": sum(1 for case in cases if case["fail_closed"]),
            "invalid_result_count": invalid_result_count,
            "inconsistent_status_count": inconsistent_status_count,
            "forbidden_field_count": forbidden_field_count,
            "input_mutation_count": input_mutation_count,
        },
        "claim_boundary": {
            "claim_class": REPORT_CLAIM_CLASS,
            "synthetic_case_count": synthetic_case_count,
            "real_hardware_case_count": real_hardware_case_count,
            "claimable_count": case_claim_counts["claimable"],
            "report_only_count": case_claim_counts["report_only"],
            "not_claimable_count": case_claim_counts["not_claimable"],
            "case_claimable_count": case_claim_counts["claimable"],
            "case_report_only_count": case_claim_counts["report_only"],
            "case_not_claimable_count": case_claim_counts["not_claimable"],
            "suite_claimable_boundary_count": len(claim_boundary["claimable"]),
            "suite_report_only_boundary_count": len(claim_boundary["report_only"]),
            "suite_not_claimable_boundary_count": len(claim_boundary["not_claimable"]),
            "claimable_topics": claimable_topics,
            "report_only_topics": report_only_topics,
            "not_claimable_topics": not_claimable_topics,
        },
    }


def _boundary_topics(claim_boundary: dict[str, Any], bucket: str) -> list[str]:
    entries = claim_boundary.get(bucket)
    if not isinstance(entries, list):
        raise ValueError(f"claim_boundary.{bucket} must be a list")
    topics = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"claim_boundary.{bucket}[{index}] must be an object")
        topic = _require_enum(entry.get("topic"), CLAIM_TOPICS, f"claim_boundary.{bucket}[{index}].topic")
        topics.append(topic)
    return sorted(set(topics))


def _case_claim_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts = {claim_class: 0 for claim_class in sorted(CLAIM_CLASSES)}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case[{index}] must be an object")
        claim_class = _require_enum(case.get("claim_class"), CLAIM_CLASSES, f"case[{index}].claim_class")
        counts[claim_class] += 1
    return counts


def _case_preservation_complete(case: dict[str, Any]) -> bool:
    return bool(
        case["diagnosis_metadata_retained"]
        and case["closure_metadata_retained"]
        and case["deterministic_signal_retained"]
        and _float_equal(float(case["evidence_retention_ratio"]), 1.0)
        and int(case["proof_drift_count"]) == 0
    )


def _case_limitations(replay_status: str) -> list[str]:
    limitations = {
        "Synthetic metadata replay only.",
        "No raw proof facts or full traces are embedded.",
    }
    if replay_status == "reference_only":
        limitations.add("Reference-only closure never yields replay_pass.")
    elif replay_status == "not_evaluated":
        limitations.add("Missing inputs or predicate absence prevented evaluation.")
    elif replay_status == "fail":
        limitations.add("Replay fails closed on metadata loss or deterministic drift.")
    return sorted(limitations)


def _assert_report_invariants(report: dict[str, Any]) -> None:
    cases = report["cases"]
    case_ids = [str(case["case_id"]) for case in cases]
    if case_ids != sorted(case_ids):
        raise ValueError("cases must be sorted by case_id")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("case_id values must be unique")

    for key in ("limitations", "notes"):
        _assert_sorted_unique_strings(report[key], f"report.{key}")
    for key in ("claimable_topics", "report_only_topics", "not_claimable_topics"):
        _assert_sorted_unique_strings(report["claim_boundary"][key], f"report.claim_boundary.{key}")
    for index, case in enumerate(cases):
        _assert_sorted_unique_strings(case["failure_reasons"], f"report.cases[{index}].failure_reasons")
        _assert_sorted_unique_strings(case["limitations"], f"report.cases[{index}].limitations")
        _assert_case_invariants(case, index)

    status_counts = {status: sum(case["replay_status"] == status for case in cases) for status in sorted(REPLAY_STATUSES)}
    claim_counts = _case_claim_counts(cases)
    evidence_expected_total = sum(int(case["evidence_refs_expected_count"]) for case in cases)
    evidence_retained_total = sum(int(case["evidence_refs_retained_count"]) for case in cases)
    evidence_retention_ratio = 1.0 if evidence_expected_total == 0 else evidence_retained_total / evidence_expected_total
    proof_drift_total = sum(int(case["proof_drift_count"]) for case in cases)
    preservation_complete_count = sum(1 for case in cases if _case_preservation_complete(case))
    preservation_complete = bool(cases and preservation_complete_count == len(cases))
    all_required_cases_evaluated = status_counts["not_evaluated"] == 0
    no_replay_failures = status_counts["fail"] == 0
    invalid_result_count = int(report["integrity_metrics"]["invalid_result_count"])
    inconsistent_status_count = int(report["integrity_metrics"]["inconsistent_status_count"])
    forbidden_field_count = int(report["integrity_metrics"]["forbidden_field_count"])
    input_mutation_count = int(report["integrity_metrics"]["input_mutation_count"])
    all_replay_passed = bool(
        len(cases) > 0
        and status_counts["pass"] == len(cases)
        and status_counts["fail"] == 0
        and status_counts["reference_only"] == 0
        and status_counts["not_evaluated"] == 0
        and proof_drift_total == 0
        and invalid_result_count == 0
        and inconsistent_status_count == 0
    )

    if report["summary"]["cases_total"] != (
        report["summary"]["replay_pass_count"]
        + report["summary"]["replay_fail_count"]
        + report["summary"]["reference_only_count"]
        + report["summary"]["not_evaluated_count"]
    ):
        raise ValueError("summary cases_total invariant violated")
    if len(cases) != int(report["summary"]["cases_total"]):
        raise ValueError("summary cases_total does not match cases length")
    if status_counts["pass"] != int(report["summary"]["replay_pass_count"]):
        raise ValueError("summary replay_pass_count mismatch")
    if status_counts["fail"] != int(report["summary"]["replay_fail_count"]):
        raise ValueError("summary replay_fail_count mismatch")
    if status_counts["reference_only"] != int(report["summary"]["reference_only_count"]):
        raise ValueError("summary reference_only_count mismatch")
    if status_counts["not_evaluated"] != int(report["summary"]["not_evaluated_count"]):
        raise ValueError("summary not_evaluated_count mismatch")
    if bool(report["summary"]["all_required_cases_evaluated"]) != all_required_cases_evaluated:
        raise ValueError("summary all_required_cases_evaluated mismatch")
    if bool(report["summary"]["no_replay_failures"]) != no_replay_failures:
        raise ValueError("summary no_replay_failures mismatch")
    if bool(report["summary"]["preservation_complete"]) != preservation_complete:
        raise ValueError("summary preservation_complete mismatch")
    if not _float_equal(float(report["summary"]["evidence_retention_ratio"]), evidence_retention_ratio):
        raise ValueError("summary evidence_retention_ratio mismatch")
    if int(report["summary"]["proof_drift_count"]) != proof_drift_total:
        raise ValueError("summary proof_drift_count mismatch")
    if int(report["summary"]["synthetic_case_count"]) != len(cases):
        raise ValueError("summary synthetic_case_count mismatch")
    if int(report["summary"]["real_hardware_case_count"]) != 0:
        raise ValueError("summary real_hardware_case_count must remain zero")
    if int(report["summary"]["claimable_count"]) != claim_counts["claimable"]:
        raise ValueError("summary claimable_count mismatch")
    if int(report["summary"]["report_only_count"]) != claim_counts["report_only"]:
        raise ValueError("summary report_only_count mismatch")
    if int(report["summary"]["not_claimable_count"]) != claim_counts["not_claimable"]:
        raise ValueError("summary not_claimable_count mismatch")
    if bool(report["summary"]["all_replay_passed"]) != all_replay_passed:
        raise ValueError("summary all_replay_passed mismatch")

    if report["case_coverage"]["cases_total"] != len(cases):
        raise ValueError("case_coverage cases_total mismatch")
    expected_required_kinds = sorted(report["case_coverage"]["required_case_kinds"])
    expected_observed_kinds = sorted(report["case_coverage"]["observed_case_kinds"])
    expected_missing_kinds = sorted(set(expected_required_kinds) - set(expected_observed_kinds))
    if report["case_coverage"]["required_case_kinds"] != expected_required_kinds:
        raise ValueError("case_coverage required_case_kinds must be sorted")
    if report["case_coverage"]["observed_case_kinds"] != expected_observed_kinds:
        raise ValueError("case_coverage observed_case_kinds must be sorted")
    if report["case_coverage"]["missing_case_kinds"] != expected_missing_kinds:
        raise ValueError("case_coverage missing_case_kinds mismatch")
    coverage_count = len(set(expected_required_kinds) & set(expected_observed_kinds))
    coverage_ratio = 1.0 if not expected_required_kinds else coverage_count / len(expected_required_kinds)
    if int(report["case_coverage"]["case_kind_coverage_count"]) != coverage_count:
        raise ValueError("case_coverage case_kind_coverage_count mismatch")
    if not _float_equal(float(report["case_coverage"]["case_kind_coverage_ratio"]), coverage_ratio):
        raise ValueError("case_coverage case_kind_coverage_ratio mismatch")
    if int(report["case_coverage"]["case_kinds_present_count"]) != len({case["case_kind"] for case in cases}):
        raise ValueError("case_coverage case_kinds_present_count mismatch")
    if int(report["case_coverage"]["evaluated_count"]) != len(cases) - status_counts["not_evaluated"]:
        raise ValueError("case_coverage evaluated_count mismatch")
    evaluated_ratio = 1.0 if not cases else (len(cases) - status_counts["not_evaluated"]) / len(cases)
    if not _float_equal(float(report["case_coverage"]["evaluated_ratio"]), evaluated_ratio):
        raise ValueError("case_coverage evaluated_ratio mismatch")
    if int(report["case_coverage"]["reference_only_count"]) != status_counts["reference_only"]:
        raise ValueError("case_coverage reference_only_count mismatch")
    if int(report["case_coverage"]["not_evaluated_count"]) != status_counts["not_evaluated"]:
        raise ValueError("case_coverage not_evaluated_count mismatch")
    if bool(report["case_coverage"]["all_required_cases_evaluated"]) != all_required_cases_evaluated:
        raise ValueError("case_coverage all_required_cases_evaluated mismatch")

    preservation = report["preservation_metrics"]
    if int(preservation["diagnosis_metadata_retained_count"]) != sum(1 for case in cases if case["diagnosis_metadata_retained"]):
        raise ValueError("preservation diagnosis_metadata_retained_count mismatch")
    if int(preservation["root_cause_retained_count"]) != sum(1 for case in cases if case["root_cause_retained"]):
        raise ValueError("preservation root_cause_retained_count mismatch")
    if int(preservation["affected_entity_retained_count"]) != sum(1 for case in cases if case["affected_entity_retained"]):
        raise ValueError("preservation affected_entity_retained_count mismatch")
    if int(preservation["closure_metadata_retained_count"]) != sum(1 for case in cases if case["closure_metadata_retained"]):
        raise ValueError("preservation closure_metadata_retained_count mismatch")
    if int(preservation["deterministic_signal_retained_count"]) != sum(1 for case in cases if case["deterministic_signal_retained"]):
        raise ValueError("preservation deterministic_signal_retained_count mismatch")
    if int(preservation["evidence_refs_expected_count"]) != evidence_expected_total:
        raise ValueError("preservation evidence_refs_expected_count mismatch")
    if int(preservation["evidence_refs_retained_count"]) != evidence_retained_total:
        raise ValueError("preservation evidence_refs_retained_count mismatch")
    if not _float_equal(float(preservation["evidence_retention_ratio"]), evidence_retention_ratio):
        raise ValueError("preservation evidence_retention_ratio mismatch")
    if int(preservation["preservation_complete_count"]) != preservation_complete_count:
        raise ValueError("preservation preservation_complete_count mismatch")
    preservation_ratio = 1.0 if not cases else preservation_complete_count / len(cases)
    if not _float_equal(float(preservation["preservation_complete_ratio"]), preservation_ratio):
        raise ValueError("preservation preservation_complete_ratio mismatch")
    if bool(preservation["preservation_complete"]) != preservation_complete:
        raise ValueError("preservation preservation_complete mismatch")

    replay_status_metrics = report["replay_status_metrics"]
    if int(replay_status_metrics["replay_pass_count"]) != status_counts["pass"]:
        raise ValueError("replay_status replay_pass_count mismatch")
    if int(replay_status_metrics["replay_fail_count"]) != status_counts["fail"]:
        raise ValueError("replay_status replay_fail_count mismatch")
    if int(replay_status_metrics["reference_only_count"]) != status_counts["reference_only"]:
        raise ValueError("replay_status reference_only_count mismatch")
    if int(replay_status_metrics["not_evaluated_count"]) != status_counts["not_evaluated"]:
        raise ValueError("replay_status not_evaluated_count mismatch")
    if int(replay_status_metrics["evaluated_count"]) != len(cases) - status_counts["not_evaluated"]:
        raise ValueError("replay_status evaluated_count mismatch")
    if bool(replay_status_metrics["all_required_cases_evaluated"]) != all_required_cases_evaluated:
        raise ValueError("replay_status all_required_cases_evaluated mismatch")
    if bool(replay_status_metrics["no_replay_failures"]) != no_replay_failures:
        raise ValueError("replay_status no_replay_failures mismatch")
    if bool(replay_status_metrics["all_replay_passed"]) != all_replay_passed:
        raise ValueError("replay_status all_replay_passed mismatch")

    integrity = report["integrity_metrics"]
    if int(integrity["proof_drift_count"]) != proof_drift_total:
        raise ValueError("integrity proof_drift_count mismatch")
    if int(integrity["tamper_detected_count"]) != sum(1 for case in cases if case["tamper_detected"]):
        raise ValueError("integrity tamper_detected_count mismatch")
    if int(integrity["fail_closed_count"]) != sum(1 for case in cases if case["fail_closed"]):
        raise ValueError("integrity fail_closed_count mismatch")
    if forbidden_field_count != 0:
        raise ValueError("forbidden_field_count must remain zero")
    if input_mutation_count != 0:
        raise ValueError("input_mutation_count must remain zero")

    source_suite = report["source_suite"]
    if int(source_suite["case_count"]) != len(cases):
        raise ValueError("source_suite case_count mismatch")
    if (
        int(source_suite["case_claimable_count"])
        + int(source_suite["case_report_only_count"])
        + int(source_suite["case_not_claimable_count"])
    ) != int(source_suite["case_count"]):
        raise ValueError("source_suite claim class counts do not sum to case_count")
    if claim_counts["claimable"] > int(source_suite["case_claimable_count"]):
        raise ValueError("claimable_count cannot increase beyond the source suite")

    claim_boundary = report["claim_boundary"]
    if claim_boundary["claim_class"] != REPORT_CLAIM_CLASS:
        raise ValueError("claim_boundary claim_class must remain report_only")
    if int(claim_boundary["synthetic_case_count"]) != len(cases):
        raise ValueError("claim_boundary synthetic_case_count mismatch")
    if int(claim_boundary["real_hardware_case_count"]) != 0:
        raise ValueError("claim_boundary real_hardware_case_count must remain zero")
    if int(claim_boundary["claimable_count"]) != claim_counts["claimable"]:
        raise ValueError("claim_boundary claimable_count mismatch")
    if int(claim_boundary["report_only_count"]) != claim_counts["report_only"]:
        raise ValueError("claim_boundary report_only_count mismatch")
    if int(claim_boundary["not_claimable_count"]) != claim_counts["not_claimable"]:
        raise ValueError("claim_boundary not_claimable_count mismatch")
    if int(claim_boundary["case_claimable_count"]) != claim_counts["claimable"]:
        raise ValueError("claim_boundary case_claimable_count mismatch")
    if int(claim_boundary["case_report_only_count"]) != claim_counts["report_only"]:
        raise ValueError("claim_boundary case_report_only_count mismatch")
    if int(claim_boundary["case_not_claimable_count"]) != claim_counts["not_claimable"]:
        raise ValueError("claim_boundary case_not_claimable_count mismatch")

    if bool(report["summary"]["all_replay_passed"]) and (
        len(cases) == 0
        or status_counts["pass"] != len(cases)
        or status_counts["fail"] != 0
        or status_counts["reference_only"] != 0
        or status_counts["not_evaluated"] != 0
        or proof_drift_total != 0
        or invalid_result_count != 0
        or inconsistent_status_count != 0
    ):
        raise ValueError("all_replay_passed invariant violated")
    if no_replay_failures and (status_counts["reference_only"] > 0 or status_counts["not_evaluated"] > 0) and all_replay_passed:
        raise ValueError("no_replay_failures cannot imply all_replay_passed")
    if all_required_cases_evaluated and status_counts["reference_only"] > 0 and all_replay_passed:
        raise ValueError("all_required_cases_evaluated cannot imply all_replay_passed")


def _assert_case_invariants(case: dict[str, Any], index: int) -> None:
    case_id = _require_non_empty_string(case.get("case_id"), f"report.cases[{index}].case_id")
    _require_enum(case.get("case_kind"), CASE_KINDS, f"report.cases[{index}].case_kind")
    replay_status = _require_enum(case.get("replay_status"), REPLAY_STATUSES, f"report.cases[{index}].replay_status")
    replay_pass = _require_bool(case.get("replay_pass"), f"report.cases[{index}].replay_pass")
    reference_only = _require_bool(case.get("reference_only"), f"report.cases[{index}].reference_only")
    diagnosis_metadata_retained = _require_bool(
        case.get("diagnosis_metadata_retained"), f"report.cases[{index}].diagnosis_metadata_retained"
    )
    root_cause_retained = _require_bool(case.get("root_cause_retained"), f"report.cases[{index}].root_cause_retained")
    affected_entity_retained = _require_bool(
        case.get("affected_entity_retained"), f"report.cases[{index}].affected_entity_retained"
    )
    expected_count = _require_non_negative_int(
        case.get("evidence_refs_expected_count"), f"report.cases[{index}].evidence_refs_expected_count"
    )
    retained_count = _require_non_negative_int(
        case.get("evidence_refs_retained_count"), f"report.cases[{index}].evidence_refs_retained_count"
    )
    if retained_count > expected_count:
        raise ValueError(f"retained evidence exceeds expected evidence for case_id {case_id!r}")
    ratio = _require_ratio(case.get("evidence_retention_ratio"), f"report.cases[{index}].evidence_retention_ratio")
    expected_ratio = (
        0.0
        if replay_status == "not_evaluated"
        else 1.0
        if expected_count == 0
        else retained_count / expected_count
    )
    if not _float_equal(ratio, expected_ratio):
        raise ValueError(f"evidence retention ratio mismatch for case_id {case_id!r}")
    closure_metadata_retained = _require_bool(
        case.get("closure_metadata_retained"), f"report.cases[{index}].closure_metadata_retained"
    )
    deterministic_signal_retained = _require_bool(
        case.get("deterministic_signal_retained"), f"report.cases[{index}].deterministic_signal_retained"
    )
    proof_drift_count = _require_non_negative_int(
        case.get("proof_drift_count"), f"report.cases[{index}].proof_drift_count"
    )
    tamper_detected = _require_bool(case.get("tamper_detected"), f"report.cases[{index}].tamper_detected")
    fail_closed = _require_bool(case.get("fail_closed"), f"report.cases[{index}].fail_closed")
    _require_enum(case.get("claim_class"), CLAIM_CLASSES, f"report.cases[{index}].claim_class")
    failure_reasons = _sorted_unique_strings(case.get("failure_reasons"), f"report.cases[{index}].failure_reasons")
    if replay_pass and replay_status in {"reference_only", "not_evaluated"}:
        raise ValueError(f"replay_pass must exclude reference_only/not_evaluated for case_id {case_id!r}")
    if tamper_detected and replay_status != "fail":
        raise ValueError(f"tamper_detected must stay separate from non-fail statuses for case_id {case_id!r}")
    if replay_status == "pass":
        if not replay_pass or reference_only or fail_closed:
            raise ValueError(f"pass status invariants violated for case_id {case_id!r}")
        if (
            not diagnosis_metadata_retained
            or not root_cause_retained
            or not affected_entity_retained
            or not closure_metadata_retained
            or not deterministic_signal_retained
            or not _float_equal(ratio, 1.0)
            or proof_drift_count != 0
            or failure_reasons
        ):
            raise ValueError(f"proof drift cannot coexist with pass status for case_id {case_id!r}")
    elif replay_status == "reference_only":
        if replay_pass or not reference_only or fail_closed:
            raise ValueError(f"reference_only status invariants violated for case_id {case_id!r}")
        if (
            not diagnosis_metadata_retained
            or not root_cause_retained
            or not affected_entity_retained
            or not closure_metadata_retained
            or not deterministic_signal_retained
            or not _float_equal(ratio, 1.0)
            or proof_drift_count != 0
            or failure_reasons
        ):
            raise ValueError(f"reference_only preservation invariants violated for case_id {case_id!r}")
    elif replay_status == "not_evaluated":
        if replay_pass or reference_only or not fail_closed:
            raise ValueError(f"not_evaluated status invariants violated for case_id {case_id!r}")
        if (
            diagnosis_metadata_retained
            or root_cause_retained
            or affected_entity_retained
            or closure_metadata_retained
            or deterministic_signal_retained
            or expected_count != 0
            or retained_count != 0
            or not _float_equal(ratio, 0.0)
            or proof_drift_count != 0
        ):
            raise ValueError(f"not_evaluated must remain empty for case_id {case_id!r}")
    else:
        if replay_pass or reference_only or not fail_closed:
            raise ValueError(f"fail status invariants violated for case_id {case_id!r}")


def _validate_against_schema(name: str, value: Any, *, label: str) -> None:
    schema = load_schema(name)
    reason = validate_schema(schema, value)
    if reason is not None:
        raise ValueError(f"{label} does not satisfy {name}: {reason}")


def _json_snapshot(value: Any, *, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise ValueError(f"{label} must be JSON-serializable") from exc


def _assert_snapshot_unchanged(label: str, before: str, after_value: Any) -> None:
    after = _json_snapshot(after_value, label=label)
    if before != after:
        raise ValueError(f"{label} was mutated during report construction")


def _render_canonical_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _assert_canonical_size(text: str) -> None:
    if len(text.encode("utf-8")) > MAX_CANONICAL_REPORT_BYTES:
        raise ValueError("canonical report JSON exceeds the 64 KiB size cap")


def _assert_no_forbidden_fields(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_FIELD_NAMES:
                raise ValueError(f"{label} contains forbidden field {key!r}")
            _assert_no_forbidden_fields(child, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_forbidden_fields(child, label=f"{label}[{index}]")


def _assert_no_omitted_output_fields(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in OMITTED_OUTPUT_FIELD_NAMES:
                raise ValueError(f"{label} contains omitted raw output field {key!r}")
            _assert_no_omitted_output_fields(child, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_omitted_output_fields(child, label=f"{label}[{index}]")


def _assert_sorted_unique_strings(value: Any, label: str) -> None:
    if value != _sorted_unique_strings(value, label):
        raise ValueError(f"{label} must be sorted with unique strings")


def _sorted_unique_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    items: list[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry:
            raise ValueError(f"{label}[{index}] must be a non-empty string")
        items.append(entry)
    return sorted(set(items))


def _count_refs(value: Any, label: str) -> int:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    keys: list[str] = []
    for index, ref in enumerate(value):
        if isinstance(ref, str):
            keys.append(_require_non_empty_string(ref, f"{label}[{index}]"))
            continue
        if not isinstance(ref, dict):
            raise ValueError(f"{label}[{index}] must be a string or object")
        ref_kind = _require_non_empty_string(ref.get("ref_kind"), f"{label}[{index}].ref_kind")
        ref_id = _require_non_empty_string(ref.get("ref_id"), f"{label}[{index}].ref_id")
        keys.append(f"{ref_kind}:{ref_id}")
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} contains duplicate evidence references")
    return len(keys)


def _require_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_enum(value: Any, options: set[str] | frozenset[str], label: str) -> str:
    normalized = _require_non_empty_string(value, label)
    if normalized not in options:
        raise ValueError(f"{label} must be one of {sorted(options)!r}")
    return normalized


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _require_non_negative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _require_ratio(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    ratio = float(value)
    if ratio < 0.0 or ratio > 1.0:
        raise ValueError(f"{label} must be between 0.0 and 1.0")
    return ratio


def _float_equal(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9

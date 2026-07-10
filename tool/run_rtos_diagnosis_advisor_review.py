#!/usr/bin/env python3
"""Build a review-only P6.5 advisor overlay from an immutable P6.4 report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.rtos_diagnosis_advisor_review import (
    VARIANT_IDS,
    build_diagnosis_advisor_review,
    canonical_review_json,
)


ALLOWLISTED_MOCK_RESPONSE = (
    ROOT / "tests/python/fixtures/rtos_diagnosis/advisor_reviews/mock_responses.json"
).resolve()


def _load_report(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("report must be readable JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("report must be a JSON object")
    return payload


def _load_allowlisted_mock_response(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    if path.resolve() != ALLOWLISTED_MOCK_RESPONSE:
        raise ValueError("mock response must be the allowlisted repository fixture")
    try:
        payload = json.loads(ALLOWLISTED_MOCK_RESPONSE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("allowlisted mock response is unreadable") from error
    if not isinstance(payload, dict):
        raise ValueError("allowlisted mock response must be a JSON object")
    return payload


def _summary_payload(review: dict[str, object]) -> dict[str, object]:
    summary = dict(review["review_summary"])
    integrity = dict(review["input_integrity"])
    truth = dict(review["truth_invariance"])
    safety = dict(review["safety_summary"])
    return {
        "variant_id": review["variant_id"],
        "status": review["status"],
        "cases_reviewed_count": summary["cases_reviewed_count"],
        "cases_abstained_count": summary["cases_abstained_count"],
        "evidence_citation_count": summary["evidence_citation_count"],
        "limitation_coverage_count": summary["limitation_coverage_count"],
        "unsupported_statement_count": summary["unsupported_statement_count"],
        "input_report_mutation_count": integrity["input_report_mutation_count"],
        "root_cause_mutation_count": truth["root_cause_mutation_count"],
        "affected_entity_mutation_count": truth["affected_entity_mutation_count"],
        "replay_status_mutation_count": truth["replay_status_mutation_count"],
        "claim_class_mutation_count": truth["claim_class_mutation_count"],
        "report_metric_mutation_count": truth["report_metric_mutation_count"],
        "proof_drift_count": truth["proof_drift_count"],
        "unauthorized_action_accepted_count": safety["unauthorized_action_accepted_count"],
        "executable_command_count": safety["executable_command_count"],
        "tool_invocation_count": safety["tool_invocation_count"],
        "secret_leak_count": safety["secret_leak_count"],
        "absolute_path_leak_count": safety["absolute_path_leak_count"],
        "forbidden_truth_field_count": safety["forbidden_truth_field_count"],
        "schema_invalid_count": safety["schema_invalid_count"],
        "rejected_invalid_count": safety["rejected_invalid_count"],
        "fallback_count": safety["fallback_count"],
        "abstention_count": safety["abstention_count"],
        "prompt_injection_blocked_count": safety["prompt_injection_blocked_count"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_rtos_diagnosis_advisor_review", description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="Read-only P6.4 report JSON.")
    parser.add_argument("--variant", choices=sorted(VARIANT_IDS), required=True, help="Review-only variant.")
    parser.add_argument("--output", type=Path, required=True, help="Separate review overlay JSON output path.")
    parser.add_argument("--summary", action="store_true", help="Write the compact review summary to stdout.")
    parser.add_argument("--overwrite", action="store_true", help="Permit replacing an existing review output.")
    parser.add_argument("--review-id", required=True, help="Deterministic review identifier.")
    parser.add_argument(
        "--mock-response",
        type=Path,
        help="Optional allowlisted repository mock response fixture for mock_llm_explanation only.",
    )
    args = parser.parse_args(argv)

    if args.output.resolve() == args.report.resolve():
        parser.error("output must be separate from the read-only report")
    if args.output.exists() and not args.overwrite:
        parser.error(f"output exists: {args.output}; pass --overwrite to replace it")
    if args.mock_response is not None and args.variant != "mock_llm_explanation":
        parser.error("--mock-response is only valid for mock_llm_explanation")
    try:
        report = _load_report(args.report)
        mock_response = _load_allowlisted_mock_response(args.mock_response)
        review = build_diagnosis_advisor_review(
            report,
            variant_id=args.variant,
            review_id=args.review_id,
            mock_response=mock_response,
        )
    except ValueError as error:
        parser.error(str(error))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_review_json(review), encoding="utf-8")
    if args.summary:
        print(json.dumps(_summary_payload(review), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

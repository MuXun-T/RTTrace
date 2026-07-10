#!/usr/bin/env python3
"""Build a deterministic P6.6 synthetic human-feedback summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.rtos_diagnosis_advisor_review import VARIANT_IDS, build_diagnosis_advisor_review
from parser.rtos_diagnosis_human_feedback import (
    build_feedback_summary,
    canonical_feedback_summary_json,
)


DEFAULT_REPORT = ROOT / "tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json"


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be readable JSON") from error


def _load_records(path: Path) -> list[dict[str, object]]:
    payload = _load_json(path, "feedback")
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ValueError("feedback must be a JSON array of feedback records")
    return payload


def _load_reviews(directory: Path | None, report: dict[str, object]) -> list[dict[str, object]]:
    if directory is None:
        return [build_diagnosis_advisor_review(report, variant_id=variant_id) for variant_id in sorted(VARIANT_IDS)]
    if not directory.is_dir():
        raise ValueError("source-review-dir must be a readable directory")
    paths = sorted(directory.glob("*.json"))
    if len(paths) != len(VARIANT_IDS):
        raise ValueError("source-review-dir must contain exactly four P6.5 review JSON files")
    reviews = [_load_json(path, "source review") for path in paths]
    if any(not isinstance(row, dict) for row in reviews):
        raise ValueError("source reviews must be JSON objects")
    return reviews


def _summary_payload(summary: dict[str, object]) -> dict[str, object]:
    return {
        "pipeline_validation_only": summary["pipeline_validation_only"],
        "contains_real_participant_data": summary["contains_real_participant_data"],
        "record_counts": summary["record_counts"],
        "privacy_summary": summary["privacy_summary"],
        "integrity_metrics": summary["integrity_metrics"],
        "claim_boundary": summary["claim_boundary"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_rtos_diagnosis_human_feedback_summary", description=__doc__)
    parser.add_argument("--feedback", type=Path, required=True, help="Synthetic P6.6 feedback JSON array.")
    parser.add_argument("--output", type=Path, required=True, help="Separate feedback summary JSON output path.")
    parser.add_argument("--summary", action="store_true", help="Write the compact summary to stdout.")
    parser.add_argument("--overwrite", action="store_true", help="Permit replacing an existing output file.")
    parser.add_argument("--summary-id", required=True, help="Deterministic feedback summary identifier.")
    parser.add_argument("--source-report", type=Path, default=DEFAULT_REPORT, help="Read-only P6.4 report JSON.")
    parser.add_argument("--source-review-dir", type=Path, help="Optional read-only directory of exactly four P6.5 reviews.")
    parser.add_argument("--synthetic-only", action="store_true", help="Required P6.6 gate; no participant records are accepted.")
    args = parser.parse_args(argv)

    if not args.synthetic_only:
        parser.error("--synthetic-only is required for P6.6")
    if args.output.resolve() in {args.feedback.resolve(), args.source_report.resolve()}:
        parser.error("output must be separate from read-only feedback and source report")
    if args.source_review_dir is not None and args.output.resolve().is_relative_to(args.source_review_dir.resolve()):
        parser.error("output must not be written inside source-review-dir")
    if args.output.exists() and not args.overwrite:
        parser.error(f"output exists: {args.output}; pass --overwrite to replace it")

    try:
        report = _load_json(args.source_report, "source report")
        if not isinstance(report, dict):
            raise ValueError("source report must be a JSON object")
        records = _load_records(args.feedback)
        reviews = _load_reviews(args.source_review_dir, report)
        summary = build_feedback_summary(records, report=report, reviews=reviews, summary_id=args.summary_id)
    except ValueError as error:
        parser.error(str(error))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_feedback_summary_json(summary), encoding="utf-8")
    if args.summary:
        print(json.dumps(_summary_payload(summary), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

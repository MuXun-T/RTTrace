#!/usr/bin/env python3
"""Build the deterministic P6.4 synthetic diagnosis report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.rtos_diagnosis_replay import companion_root_for_suite, load_suite, run_replay_suite
from parser.rtos_diagnosis_report import build_diagnosis_report, canonical_report_json


def _summary_payload(report: dict[str, object]) -> dict[str, object]:
    summary = dict(report["summary"])
    case_coverage = dict(report["case_coverage"])
    preservation = dict(report["preservation_metrics"])
    return {
        "all_replay_passed": summary["all_replay_passed"],
        "all_required_cases_evaluated": summary["all_required_cases_evaluated"],
        "case_kind_coverage_ratio": case_coverage["case_kind_coverage_ratio"],
        "cases_total": summary["cases_total"],
        "claimable_count": summary["claimable_count"],
        "not_evaluated_count": summary["not_evaluated_count"],
        "preservation_complete_count": preservation["preservation_complete_count"],
        "proof_drift_count": summary["proof_drift_count"],
        "real_hardware_case_count": summary["real_hardware_case_count"],
        "reference_only_count": summary["reference_only_count"],
        "replay_fail_count": summary["replay_fail_count"],
        "replay_pass_count": summary["replay_pass_count"],
        "report_only_count": summary["report_only_count"],
        "synthetic_case_count": summary["synthetic_case_count"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_rtos_diagnosis_report")
    parser.add_argument("--suite", type=Path, required=True, help="Frozen P6.2 synthetic suite JSON.")
    parser.add_argument("--output", type=Path, required=True, help="Report JSON output path.")
    parser.add_argument("--summary", action="store_true", help="Write the compact summary to stdout.")
    parser.add_argument("--overwrite", action="store_true", help="Permit replacing an existing output file.")
    parser.add_argument("--report-id", required=True, help="Deterministic report identifier.")
    parser.add_argument(
        "--generated-at",
        help="Optional generated_at override. If omitted, suite.generated_at is copied into the report.",
    )
    args = parser.parse_args(argv)

    if args.output.exists() and not args.overwrite:
        parser.error(f"output exists: {args.output}; pass --overwrite to replace it")

    suite = load_suite(args.suite)
    replay_results, _ = run_replay_suite(suite, companion_root_for_suite(args.suite))
    report = build_diagnosis_report(
        suite,
        replay_results,
        report_id=args.report_id,
        generated_at=args.generated_at,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_report_json(report), encoding="utf-8")
    if args.summary:
        print(json.dumps(_summary_payload(report), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

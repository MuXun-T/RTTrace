#!/usr/bin/env python3
"""Run deterministic P6.3 synthetic diagnosis replay validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.rtos_diagnosis_replay import companion_root_for_suite, load_suite, run_replay_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="Frozen P6.2 synthetic suite JSON.")
    parser.add_argument("--output", required=True, help="Replay validation JSON output path.")
    parser.add_argument("--summary", action="store_true", help="Write the compact summary to stdout.")
    parser.add_argument("--fail-on-drift", action="store_true", help="Return nonzero when deterministic fact drift is nonzero.")
    parser.add_argument("--overwrite", action="store_true", help="Permit replacing an existing output file.")
    args = parser.parse_args()

    output = Path(args.output)
    if output.exists() and not args.overwrite:
        parser.error(f"output exists: {output}; pass --overwrite to replace it")
    suite_path = Path(args.suite)
    suite = load_suite(suite_path)
    results, summary = run_replay_suite(suite, companion_root_for_suite(suite_path))
    payload = {
        "schema_version": "rtos-diagnosis-replay-output-v1",
        "suite_id": suite.get("suite_id"),
        "results": results,
        "summary": summary,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.summary:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 1 if args.fail_on_drift and int(summary["proof_drift_count"]) != 0 else 0


if __name__ == "__main__":
    sys.exit(main())

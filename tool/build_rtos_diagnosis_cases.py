from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.rtos_diagnosis_fixtures import DEFAULT_OUTPUT_PATH, SIZE_POLICY, write_generated_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_rtos_diagnosis_cases")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--max-events-per-case",
        type=int,
        default=SIZE_POLICY["default_max_events_per_case"],
    )
    args = parser.parse_args(argv)

    summary = write_generated_suite(
        args.output,
        overwrite=args.overwrite,
        max_events_per_case=args.max_events_per_case,
    )
    if args.summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

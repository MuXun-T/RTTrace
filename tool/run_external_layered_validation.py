#!/usr/bin/env python3
"""Run one frozen P7.5 layered validation case."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser.external_layered_validation import validate_case
from parser.external_layered_validation_report import canonical_report


EXIT_CODES = {"validation_pass": 0, "reference_only": 2, "not_evaluated": 3, "validation_fail": 4}
CASES = ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k", "zephyr", "zephelin")
ROOT = Path(__file__).resolve().parents[1]


def _outside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--output")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        report = validate_case(args.case)
        data = canonical_report(report)
        if args.output:
            output = Path(args.output)
            if not _outside_repository(output):
                return 70
            output.open("xb").write(data)
        else:
            print(data.decode("utf-8"), end="")
    except (OSError, ValueError, KeyError):
        return 70
    return EXIT_CODES[report.validation_state.value]


if __name__ == "__main__":
    raise SystemExit(main())

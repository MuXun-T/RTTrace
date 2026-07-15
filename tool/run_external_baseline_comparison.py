#!/usr/bin/env python3
"""Validate and write a supplied P7.7 comparison record without tool execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser.external_baseline_runner import canonical_report_bytes


ROOT = Path(__file__).resolve().parents[1]


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(64, f"{self.prog}: {message}\n")


def _outside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(prog="run_external_baseline_comparison")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        output = Path(args.output)
        if not _outside_repository(output):
            raise ValueError("output must be outside the repository")
        record = json.loads(Path(args.input).read_bytes())
        data = canonical_report_bytes(record)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(data)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return 70
    print("canonical_report_written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

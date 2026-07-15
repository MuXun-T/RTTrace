#!/usr/bin/env python3
"""Validate a P7.8 manifest and write canonical audit bytes outside the repo."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser.phase7_closeout_runner import canonical_audit

ROOT = Path(__file__).resolve().parents[1]


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(64, f"{self.prog}: {message}\n")


def _outside(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = Parser(prog="run_phase7_closeout_audit")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    try:
        args = parser.parse_args(argv)
        output = Path(args.output)
        if not _outside(output):
            return 2
        value = json.loads(Path(args.manifest).read_bytes())
        data = canonical_audit(value, ROOT)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(data)
        return 0
    except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

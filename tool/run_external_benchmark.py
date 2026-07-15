#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser.external_benchmark_runner import run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--case", action="append", dest="cases")
    try:
        args = parser.parse_args(argv)
        evidence = run_benchmark(repeats=args.repeats, warmups=args.warmups, cases=tuple(args.cases) if args.cases else None)
        output = Path(args.output)
        if output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError("benchmark output must be outside the repository")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.open("xb").write(evidence.canonical_bytes())
        print(evidence.canonical_sha256())
        return 0
    except (OSError, ValueError):
        return 70


if __name__ == "__main__":
    raise SystemExit(main())

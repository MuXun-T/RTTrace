"""Append one Phase 2 Injection Ledger JSONL record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spec.rtd_pilot_contracts import ContractError, append_ledger_record, canonical_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--seal", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = json.loads(args.record.read_text(encoding="utf-8"))
        result = append_ledger_record(args.ledger, value, seal_path=args.seal)
    except (OSError, json.JSONDecodeError, ContractError) as error:
        parser.error(str(error))
    print(canonical_json(result).decode("ascii"), end="")


if __name__ == "__main__":
    main()

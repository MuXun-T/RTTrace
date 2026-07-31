"""Read and verify a Phase 2 Ledger and optional session seal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spec.rtd_pilot_contracts import ContractError, load_jsonl, validate_ledger_stream


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--seal", type=Path)
    args = parser.parse_args()
    try:
        rows, raw = load_jsonl(args.ledger)
        seal = json.loads(args.seal.read_text(encoding="utf-8")) if args.seal else None
        validate_ledger_stream(rows, seal, raw)
    except (OSError, json.JSONDecodeError, ContractError) as error:
        parser.error(str(error))
    print(json.dumps({"valid": True, "records": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()

"""Create an immutable Phase 2 Ledger session seal."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spec.rtd_pilot_contracts import ContractError, canonical_json, seal_ledger_session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--seal", type=Path, required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--seal-record-id", required=True)
    args = parser.parse_args()
    try:
        result = seal_ledger_session(args.ledger, args.seal, timestamp=args.timestamp, seal_record_id=args.seal_record_id)
    except (OSError, ContractError) as error:
        parser.error(str(error))
    print(canonical_json(result).decode("ascii"), end="")


if __name__ == "__main__":
    main()

"""Fail closed on incomplete or mismatched Phase 2 capture bindings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spec.rtd_pilot_contracts import ContractError, validate_capture_bundle, validate_scoring_eligibility


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--scoring-eligible", action="store_true")
    args = parser.parse_args()
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        result = validate_scoring_eligibility(bundle) if args.scoring_eligible else validate_capture_bundle(bundle)
    except (OSError, json.JSONDecodeError, ContractError) as error:
        parser.error(str(error))
    print(json.dumps({"valid": True, "capture_status": result}, sort_keys=True))


if __name__ == "__main__":
    main()

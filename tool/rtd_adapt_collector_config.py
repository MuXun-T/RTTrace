"""Normalize a collector configuration into a schema-validated snapshot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spec.rtd_pilot_contracts import ContractError, adapt_collector_config_snapshot, canonical_json, validate_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        source = json.loads(args.input.read_text(encoding="utf-8"))
        snapshot = adapt_collector_config_snapshot(source)
        validate_snapshot(snapshot)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, canonical_json(snapshot))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, json.JSONDecodeError, ContractError) as error:
        parser.error(str(error))
    print(canonical_json(snapshot).decode("ascii"), end="")


if __name__ == "__main__":
    main()

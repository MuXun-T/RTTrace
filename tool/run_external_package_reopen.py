#!/usr/bin/env python3
"""Open a P7.3 directory package offline and emit its canonical report."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from parser.external_package_models import CONTRACT_VERSION, PackageOpenReason, PackageOpenReport, PackageOpenResult, canonical_json
from parser.external_package_reader import PackageReadError, read_directory_package
from parser.external_package_validator import validate_package


EXIT_CODES = {PackageOpenResult.OPENED: 0, PackageOpenResult.OPENED_REFERENCE_ONLY: 2, PackageOpenResult.INVALID: 3, PackageOpenResult.BLOCKED: 4, PackageOpenResult.UNSUPPORTED: 5}


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None: self.exit(64, f"{self.prog}: {message}\n")


def _invalid(reason: PackageOpenReason) -> PackageOpenReport:
    return PackageOpenReport(None, None, PackageOpenResult.INVALID, (reason,), False, False, 0, 0, 0, 0, 0, 0, 0, 0, 0, False, 0, 0, 0, 0, 0, ("package_open_failed",))


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(add_help=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-contract-version", default=CONTRACT_VERSION)
    parser.add_argument("--offline", action="store_true", default=True)
    args = parser.parse_args(argv)
    try:
        report = validate_package(read_directory_package(args.package, expected_contract_version=args.expected_contract_version))
        code = EXIT_CODES[report.open_result]
    except PackageReadError as error:
        report = _invalid(error.reason); code = EXIT_CODES[PackageOpenResult.INVALID]
    except Exception:
        report = _invalid(PackageOpenReason.MANIFEST_UNREADABLE); code = 70
    try:
        Path(args.output).write_bytes(canonical_json(report.to_dict()))
    except OSError:
        return 70
    return code


if __name__ == "__main__": sys.exit(main())

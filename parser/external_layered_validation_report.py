"""Canonical P7.5 layered-validation report serialization."""

from __future__ import annotations

import hashlib

from parser.external_layered_validation_models import LayeredValidationReport, canonical_json


def canonical_report(report: LayeredValidationReport) -> bytes:
    return canonical_json(report.to_dict())


def report_sha256(report: LayeredValidationReport) -> str:
    return hashlib.sha256(canonical_report(report)).hexdigest()

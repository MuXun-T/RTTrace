"""Pure P7.7 report canonicalization; this module never runs external tools."""

from __future__ import annotations

from parser.external_baseline_models import BaselineComparisonReport


def canonical_report_bytes(record: object) -> bytes:
    """Validate one explicit record and return its canonical report bytes."""
    return BaselineComparisonReport.from_dict(record).canonical_bytes()

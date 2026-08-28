"""Offline-only Phase 4 capture preparation surfaces.

The package deliberately does not open a hardware transport or create an
authoritative Case, Ledger, OAR, or CVR.  Its artifacts are preparation inputs
for a later, separately authorized hardware operation.
"""

from .contracts import (
    P4_SCHEMA_VERSION,
    P4ContractError,
    finalize_record,
    make_collector_config_export,
    make_session_manifest,
    p2_snapshot_from_export,
    validate_collector_config_export,
    validate_session_manifest,
)
from .cli import build_parser
from .integrity import reconcile_collector_facts

__all__ = [
    "P4_SCHEMA_VERSION",
    "P4ContractError",
    "finalize_record",
    "make_collector_config_export",
    "make_session_manifest",
    "p2_snapshot_from_export",
    "validate_collector_config_export",
    "validate_session_manifest",
    "build_parser",
    "reconcile_collector_facts",
]

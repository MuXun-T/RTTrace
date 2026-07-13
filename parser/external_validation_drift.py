"""P7.5 deterministic replay-fact drift over the frozen profile domain."""

from __future__ import annotations

from typing import Mapping

from parser.external_layered_validation_models import FactDrift, ReplayFactDrift, ValidationProfile, canonical_json, sha256_identity


SUPPORTED_REPLAY_FACT_FIELDS = (
    "actual_identity",
    "comparison_attempted",
    "comparison_matched",
    "comparison_profile_id",
    "comparison_profile_identity",
    "comparison_profile_version",
    "expected_identity",
    "hardware_validation",
    "package_identity",
    "primary_reason",
    "raw_trace_mutation_count",
    "reason_codes",
    "replay_attempted",
    "replay_state",
    "source_identity",
    "source_mutation_count",
    "trace_identity",
)
_MISSING = {"p7_5_missing_replay_fact": True}


def _fact_value(report: Mapping[str, object], field: str) -> object:
    return report[field] if field in report else _MISSING


def _digest(value: object) -> str:
    return sha256_identity({"value": value})


def analyze_replay_fact_drift(frozen_report: Mapping[str, object], reproduced_report: Mapping[str, object], profile: ValidationProfile) -> ReplayFactDrift:
    if profile.replay_fact_fields != SUPPORTED_REPLAY_FACT_FIELDS:
        raise ValueError("validation profile replay drift domain is unsupported")
    items = tuple(
        FactDrift(field, _digest(_fact_value(frozen_report, field)), _digest(_fact_value(reproduced_report, field)))
        for field in profile.replay_fact_fields
        if _fact_value(frozen_report, field) != _fact_value(reproduced_report, field)
    )
    return ReplayFactDrift(len(profile.replay_fact_fields), len(items), items)


def replay_fact_digest(report: Mapping[str, object], profile: ValidationProfile) -> str:
    """Stable digest of exactly the P7.5 replay drift domain."""
    if profile.replay_fact_fields != SUPPORTED_REPLAY_FACT_FIELDS:
        raise ValueError("validation profile replay drift domain is unsupported")
    return sha256_identity({field: _fact_value(report, field) for field in profile.replay_fact_fields})

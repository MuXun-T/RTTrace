"""Strict, offline-only contracts for RTD-Pilot Phase 4 preparation.

P2 remains the authority for its snapshot, CCM, CIR, inventory, Ledger, OAR,
and CVR records.  This module adds P4 sidecars around those frozen records so
that full collector configuration and provenance can be prepared without
changing a P2 or P3 contract.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from spec.rtd_pilot_contracts import (
    ContractError,
    adapt_collector_config_snapshot,
    canonical_json as p2_canonical_json,
    validate_snapshot,
)


P4_SCHEMA_VERSION = "rtd-phase4-prehardware-v1.0"
P4_CONFIG_EXPORT_VERSION = "rtd-p4-config-export-v1"
P4_SESSION_MANIFEST_VERSION = "rtd-p4-session-manifest-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_CAPTURE_ID = re.compile(r"^capture:[A-Za-z0-9][A-Za-z0-9._-]*$")
_CASE_ID = re.compile(r"^case:[A-Za-z0-9][A-Za-z0-9._-]*$")

_SUPPORTED_EVENT_TYPES = frozenset(
    {
        "TASK_READY",
        "TASK_BLOCK",
        "TASK_WAKEUP",
        "TASK_DISPATCH",
        "TASK_EXIT",
        "CTX_SWITCH",
        "SCHED_DECISION",
        "SYNC_TRY",
        "SYNC_LOCK",
        "SYNC_UNLOCK",
        "IRQ_ENTER",
        "IRQ_EXIT",
        "LOSS",
        "OVERFLOW",
        "SYNC_CALIB",
        "TS_CALIB",
    }
)


class P4ContractError(ValueError):
    """A P4 pre-hardware input is malformed, stale, or not admissible."""


def canonical_json(value: Any) -> bytes:
    """Return the canonical ASCII JSON encoding used by P4 sidecars."""

    return p2_canonical_json(value)


def record_digest(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("record_digest", None)
    return sha256(canonical_json(payload)).hexdigest()


def finalize_record(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result["record_digest"] = record_digest(result)
    return result


def file_digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise P4ContractError(message)


def _required_fields(value: Mapping[str, Any], required: set[str], label: str) -> None:
    _require(set(value) == required, f"{label} has unsupported or missing fields")


def _identifier(value: Any, label: str) -> str:
    _require(isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value)), f"{label} must be an ASCII identifier")
    return value


def _sha256(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    _require(isinstance(value, str) and bool(_SHA256.fullmatch(value)), f"{label} must be a lowercase SHA-256 digest")
    return str(value)


def _text(value: Any, label: str) -> str:
    _require(isinstance(value, str) and value and value.isascii(), f"{label} must be non-empty ASCII text")
    return value


def validate_capture_identity(
    capture_id: Any,
    session_id: Any,
    case_id: Any,
    capture_mode: Any,
    test_only: Any,
) -> None:
    """Validate P4 identity without permitting a non-test formal Case."""

    _require(isinstance(capture_id, str) and bool(_CAPTURE_ID.fullmatch(capture_id)), "capture_id has an invalid format")
    _require(len(capture_id.encode("ascii")) <= 32, "capture_id exceeds the trace header run_id capacity")
    _identifier(session_id, "session_id")
    _require(isinstance(test_only, bool), "test_only must be boolean")
    _require(capture_mode in {"test_only", "hardware_smoke_noncase"}, "unsupported capture_mode")
    if test_only:
        _require(capture_mode == "test_only", "test-only material requires capture_mode=test_only")
        if case_id is not None:
            _require(isinstance(case_id, str) and bool(_CASE_ID.fullmatch(case_id)), "case_id has an invalid format")
    else:
        _require(capture_mode == "hardware_smoke_noncase", "non-test material must be hardware_smoke_noncase")
        _require(case_id is None, "P4 hardware smoke must not create a Case")


def _validate_unique_text_list(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    _require(isinstance(value, list), f"{label} must be an array")
    _require(allow_empty or bool(value), f"{label} must not be empty")
    _require(all(isinstance(item, str) and item and item.isascii() for item in value), f"{label} must contain non-empty ASCII strings")
    _require(len(value) == len(set(value)), f"{label} contains duplicate values")
    return list(value)


def _validate_sampling(value: Any) -> None:
    _require(isinstance(value, Mapping), "sampling_configuration must be an object")
    enabled = value.get("enabled")
    mode = value.get("mode")
    _require(isinstance(enabled, bool), "sampling_configuration.enabled must be boolean")
    _require(isinstance(mode, str), "sampling_configuration.mode must be text")
    if not enabled:
        _required_fields(value, {"enabled", "mode"}, "disabled sampling_configuration")
        _require(mode == "disabled", "disabled sampling_configuration requires mode=disabled")
        return
    if mode == "every_n":
        _required_fields(value, {"enabled", "mode", "every_n", "mark_sampled"}, "every_n sampling_configuration")
        _require(isinstance(value["every_n"], int) and value["every_n"] >= 1, "sampling every_n must be positive")
    elif mode == "ratio":
        _required_fields(value, {"enabled", "mode", "ratio_numerator", "ratio_denominator", "mark_sampled"}, "ratio sampling_configuration")
        numerator = value["ratio_numerator"]
        denominator = value["ratio_denominator"]
        _require(isinstance(numerator, int) and isinstance(denominator, int) and 0 < numerator <= denominator, "sampling ratio is invalid")
    else:
        raise P4ContractError("unsupported sampling mode")
    _require(isinstance(value["mark_sampled"], bool), "sampling mark_sampled must be boolean")


def _validate_filter_configuration(value: Any) -> None:
    _require(isinstance(value, Mapping), "filter_configuration must be an object")
    _required_fields(value, {"event_domain_mask", "drop_unknown", "drop_integrity"}, "filter_configuration")
    _require(isinstance(value["event_domain_mask"], int) and value["event_domain_mask"] >= 0, "event_domain_mask must be non-negative")
    _require(isinstance(value["drop_unknown"], bool), "drop_unknown must be boolean")
    _require(isinstance(value["drop_integrity"], bool), "drop_integrity must be boolean")
    _require(not value["drop_integrity"], "P4 capture configuration must retain integrity events")


def _validate_high_watermark_policy(value: Any, buffer_capacity: int) -> None:
    _require(isinstance(value, Mapping), "high_watermark_policy must be an object")
    _required_fields(value, {"report_on_stop", "warning_threshold"}, "high_watermark_policy")
    _require(isinstance(value["report_on_stop"], bool) and value["report_on_stop"], "high watermark reporting must be enabled")
    threshold = value["warning_threshold"]
    _require(isinstance(threshold, int) and 1 <= threshold <= buffer_capacity, "high watermark threshold is invalid")


def _validate_flush_configuration(value: Any) -> None:
    _require(isinstance(value, Mapping), "flush_configuration must be an object")
    _required_fields(
        value,
        {"flush_threshold_bytes", "flush_interval_ms", "segment_size_limit", "segment_duration_ns", "auto_flush"},
        "flush_configuration",
    )
    for key in ("flush_threshold_bytes", "flush_interval_ms", "segment_size_limit", "segment_duration_ns"):
        _require(isinstance(value[key], int) and value[key] >= 0, f"{key} must be non-negative")
    _require(isinstance(value["auto_flush"], bool), "auto_flush must be boolean")


def _validate_transport(value: Any, *, test_only: bool) -> None:
    _require(isinstance(value, Mapping), "transport_configuration must be an object")
    _required_fields(
        value,
        {"type", "endpoint", "baud_rate", "framing", "retry_limit", "retry_backoff_ms"},
        "transport_configuration",
    )
    transport_type = value["type"]
    _require(transport_type in {"offline_import", "file", "serial", "network"}, "unsupported transport type")
    _text(value["endpoint"], "transport endpoint")
    _require(isinstance(value["baud_rate"], int) and value["baud_rate"] >= 0, "baud_rate must be non-negative")
    _text(value["framing"], "transport framing")
    _require(isinstance(value["retry_limit"], int) and value["retry_limit"] >= 0, "retry_limit must be non-negative")
    _require(isinstance(value["retry_backoff_ms"], int) and value["retry_backoff_ms"] >= 0, "retry_backoff_ms must be non-negative")
    if test_only:
        _require(transport_type == "offline_import", "test-only configuration must use offline_import transport")
        _require(str(value["endpoint"]).startswith("test://"), "test-only transport endpoint must use test://")


def _p2_config_source(export: Mapping[str, Any]) -> dict[str, Any]:
    """Return only fields the frozen P2 adapter is allowed to normalize."""

    return {
        "event_types_enabled": list(export["event_types_enabled"]),
        "task_filter": list(export["task_filter"]),
        "resource_filter": list(export["resource_filter"]),
        "irq_filter": list(export["irq_filter"]),
        "core_filter": list(export["core_filter"]),
        "sampling_configuration": deepcopy(dict(export["sampling_configuration"])),
        "buffer_capacity": export["buffer_capacity"],
        "flush_policy": export["flush_policy"],
        "timestamp_source": export["timestamp_source"],
        "clock_resolution": export["clock_resolution"],
        "payload_fields": list(export["payload_fields"]),
        "dictionary_version": export["dictionary_version"],
        "mapping_version": export["mapping_version"],
        "collector_version": export["collector_version"],
        "trace_start_boundary": export["trace_start_boundary"],
        "trace_end_boundary": export["trace_end_boundary"],
    }


def p2_snapshot_source(export: Mapping[str, Any]) -> dict[str, Any]:
    """Return P2 adapter provenance without P4-only buffer unit annotations.

    The frozen adapter normalizes only its established configuration fields.
    Keeping the remaining prepared-export provenance in its source digest
    preserves the existing cross-capture stale-snapshot guard while units do
    not alter the P2 snapshot or its digest.
    """

    source = deepcopy(dict(export))
    source.pop("buffer_capacity_unit", None)
    source.pop("buffer_high_watermark_unit", None)
    # The export record digest necessarily changes when the P4-only fields are
    # added.  Re-finalize the source view so a frozen P2 snapshot sees exactly
    # the same provenance bytes as the pre-unit export.
    source["record_digest"] = record_digest(source)
    return source


def _p2_config_hash(export: Mapping[str, Any]) -> str:
    snapshot = adapt_collector_config_snapshot(_p2_config_source(export))
    return str(snapshot["config_hash"])


_CONFIG_EXPORT_FIELDS = {
    "schema_version",
    "collector_config_export_id",
    "capture_id",
    "session_id",
    "case_id",
    "capture_mode",
    "test_only",
    "board_ref",
    "platform_ref",
    "pinmap_ref",
    "firmware_hash",
    "elf_hash",
    "build_config_hash",
    "source_commit",
    "event_types_enabled",
    "task_filter",
    "resource_filter",
    "irq_filter",
    "core_filter",
    "filter_configuration",
    "sampling_configuration",
    "buffer_capacity",
    "buffer_capacity_unit",
    "buffer_high_watermark_unit",
    "high_watermark_policy",
    "flush_policy",
    "flush_configuration",
    "transport_configuration",
    "timestamp_source",
    "clock_resolution",
    "payload_fields",
    "dictionary_version",
    "mapping_version",
    "collector_version",
    "trace_start_boundary",
    "trace_end_boundary",
    "p2_config_hash",
    "prior_config_export_ref",
    "record_digest",
}


def validate_collector_config_export(value: Mapping[str, Any]) -> None:
    """Validate full P4 configuration/provenance and its P2 projection."""

    _require(isinstance(value, Mapping), "collector config export must be an object")
    legacy_fields = _CONFIG_EXPORT_FIELDS - {"buffer_capacity_unit", "buffer_high_watermark_unit"}
    _require(
        set(value) == _CONFIG_EXPORT_FIELDS or (value.get("test_only") is True and set(value) == legacy_fields),
        "collector config export has unsupported or missing fields",
    )
    _require(value["schema_version"] == P4_SCHEMA_VERSION, "unsupported P4 schema version")
    _identifier(value["collector_config_export_id"], "collector_config_export_id")
    validate_capture_identity(
        value["capture_id"], value["session_id"], value["case_id"], value["capture_mode"], value["test_only"]
    )
    for key in ("board_ref", "platform_ref", "pinmap_ref", "flush_policy", "timestamp_source", "dictionary_version", "mapping_version", "collector_version", "trace_start_boundary", "trace_end_boundary"):
        _text(value[key], key)
    _sha256(value["firmware_hash"], "firmware_hash")
    _sha256(value["elf_hash"], "elf_hash")
    _sha256(value["build_config_hash"], "build_config_hash")
    _require(isinstance(value["source_commit"], str) and bool(_SOURCE_COMMIT.fullmatch(value["source_commit"])), "source_commit must be a lowercase commit hash")
    event_types = _validate_unique_text_list(value["event_types_enabled"], "event_types_enabled", allow_empty=False)
    unsupported = sorted(set(event_types).difference(_SUPPORTED_EVENT_TYPES))
    if unsupported:
        raise P4ContractError(f"unsupported event type {unsupported[0]}")
    for key in ("task_filter", "resource_filter", "irq_filter", "core_filter", "payload_fields"):
        _validate_unique_text_list(value[key], key)
    _validate_filter_configuration(value["filter_configuration"])
    _validate_sampling(value["sampling_configuration"])
    _require(isinstance(value["buffer_capacity"], int) and value["buffer_capacity"] >= 1, "buffer_capacity must be positive")
    if "buffer_capacity_unit" in value or "buffer_high_watermark_unit" in value:
        _require(
            value.get("buffer_capacity_unit") == value.get("buffer_high_watermark_unit") == "records",
            "buffer capacity/high-watermark units must both be records",
        )
    else:
        _require(value["test_only"] is True, "hardware_smoke_noncase requires records buffer units")
    _validate_high_watermark_policy(value["high_watermark_policy"], value["buffer_capacity"])
    _validate_flush_configuration(value["flush_configuration"])
    _validate_transport(value["transport_configuration"], test_only=bool(value["test_only"]))
    _require(isinstance(value["clock_resolution"], int) and value["clock_resolution"] >= 1, "clock_resolution must be positive")
    _sha256(value["p2_config_hash"], "p2_config_hash")
    _require(value["p2_config_hash"] == _p2_config_hash(value), "p2_config_hash does not match normalized P2 configuration")
    _require(value["prior_config_export_ref"] is None or bool(_SHA256.fullmatch(str(value["prior_config_export_ref"]))), "prior_config_export_ref must be a digest or null")
    _sha256(value["record_digest"], "record_digest")
    _require(value["record_digest"] == record_digest(value), "collector config export record_digest mismatch")


def make_collector_config_export(
    *,
    capture_id: str,
    session_id: str,
    test_only: bool,
    board_ref: str,
    platform_ref: str,
    pinmap_ref: str,
    firmware_hash: str,
    elf_hash: str,
    build_config_hash: str,
    source_commit: str,
    event_types_enabled: list[str],
    task_filter: list[str],
    resource_filter: list[str],
    irq_filter: list[str],
    core_filter: list[str],
    filter_configuration: Mapping[str, Any],
    sampling_configuration: Mapping[str, Any],
    buffer_capacity: int,
    high_watermark_policy: Mapping[str, Any],
    flush_policy: str,
    flush_configuration: Mapping[str, Any],
    transport_configuration: Mapping[str, Any],
    timestamp_source: str,
    clock_resolution: int,
    payload_fields: list[str],
    dictionary_version: str,
    mapping_version: str,
    collector_version: str,
    trace_start_boundary: str,
    trace_end_boundary: str,
    case_id: str | None = None,
    prior_config_export_ref: str | None = None,
) -> dict[str, Any]:
    """Create a canonical P4 config export with an explicit P2 hash bridge."""

    provisional = {
        "schema_version": P4_SCHEMA_VERSION,
        "collector_config_export_id": f"p4cfg:{capture_id.split(':', 1)[1]}",
        "capture_id": capture_id,
        "session_id": session_id,
        "case_id": case_id,
        "capture_mode": "test_only" if test_only else "hardware_smoke_noncase",
        "test_only": test_only,
        "board_ref": board_ref,
        "platform_ref": platform_ref,
        "pinmap_ref": pinmap_ref,
        "firmware_hash": firmware_hash,
        "elf_hash": elf_hash,
        "build_config_hash": build_config_hash,
        "source_commit": source_commit,
        "event_types_enabled": list(event_types_enabled),
        "task_filter": list(task_filter),
        "resource_filter": list(resource_filter),
        "irq_filter": list(irq_filter),
        "core_filter": list(core_filter),
        "filter_configuration": deepcopy(dict(filter_configuration)),
        "sampling_configuration": deepcopy(dict(sampling_configuration)),
        "buffer_capacity": buffer_capacity,
        "buffer_capacity_unit": "records",
        "buffer_high_watermark_unit": "records",
        "high_watermark_policy": deepcopy(dict(high_watermark_policy)),
        "flush_policy": flush_policy,
        "flush_configuration": deepcopy(dict(flush_configuration)),
        "transport_configuration": deepcopy(dict(transport_configuration)),
        "timestamp_source": timestamp_source,
        "clock_resolution": clock_resolution,
        "payload_fields": list(payload_fields),
        "dictionary_version": dictionary_version,
        "mapping_version": mapping_version,
        "collector_version": collector_version,
        "trace_start_boundary": trace_start_boundary,
        "trace_end_boundary": trace_end_boundary,
        "p2_config_hash": "0" * 64,
        "prior_config_export_ref": prior_config_export_ref,
        "record_digest": "0" * 64,
    }
    provisional["p2_config_hash"] = _p2_config_hash(provisional)
    result = finalize_record(provisional)
    validate_collector_config_export(result)
    return result


def p2_snapshot_from_export(export: Mapping[str, Any]) -> dict[str, Any]:
    """Build the frozen P2 snapshot from the P4 export without adding identity."""

    validate_collector_config_export(export)
    source = p2_snapshot_source(export)
    try:
        snapshot = adapt_collector_config_snapshot(source)
        validate_snapshot(snapshot)
    except ContractError as exc:
        raise P4ContractError(f"P2 snapshot projection failed: {exc}") from exc
    _require(snapshot["config_hash"] == export["p2_config_hash"], "P2 snapshot config_hash mismatch")
    _require(snapshot["source_metadata_digest"] == file_digest(canonical_json(source)), "P2 snapshot source metadata mismatch")
    return snapshot


_SESSION_MANIFEST_FIELDS = {
    "schema_version",
    "session_manifest_id",
    "capture_id",
    "session_id",
    "case_id",
    "capture_mode",
    "test_only",
    "buffer_capacity_unit",
    "buffer_high_watermark_unit",
    "collector_config_export_ref",
    "collector_config_export_digest",
    "board_ref",
    "platform_ref",
    "pinmap_ref",
    "firmware_hash",
    "elf_hash",
    "build_config_hash",
    "source_commit",
    "prepared_at",
    "prior_session_manifest_ref",
    "record_digest",
}


def validate_session_manifest(value: Mapping[str, Any], *, export: Mapping[str, Any] | None = None) -> None:
    """Validate a non-authoritative session preparation manifest."""

    _require(isinstance(value, Mapping), "session manifest must be an object")
    legacy_fields = _SESSION_MANIFEST_FIELDS - {"buffer_capacity_unit", "buffer_high_watermark_unit"}
    _require(
        set(value) == _SESSION_MANIFEST_FIELDS or (value.get("test_only") is True and set(value) == legacy_fields),
        "session manifest has unsupported or missing fields",
    )
    _require(value["schema_version"] == P4_SCHEMA_VERSION, "unsupported P4 schema version")
    _identifier(value["session_manifest_id"], "session_manifest_id")
    validate_capture_identity(
        value["capture_id"], value["session_id"], value["case_id"], value["capture_mode"], value["test_only"]
    )
    if "buffer_capacity_unit" in value or "buffer_high_watermark_unit" in value:
        _require(
            value.get("buffer_capacity_unit") == value.get("buffer_high_watermark_unit") == "records",
            "session manifest buffer units must both be records",
        )
    else:
        _require(value["test_only"] is True, "hardware_smoke_noncase session manifest requires records buffer units")
    _identifier(value["collector_config_export_ref"], "collector_config_export_ref")
    _sha256(value["collector_config_export_digest"], "collector_config_export_digest")
    for key in ("board_ref", "platform_ref", "pinmap_ref", "prepared_at"):
        _text(value[key], key)
    for key in ("firmware_hash", "elf_hash", "build_config_hash"):
        _sha256(value[key], key)
    _require(isinstance(value["source_commit"], str) and bool(_SOURCE_COMMIT.fullmatch(value["source_commit"])), "source_commit must be a lowercase commit hash")
    _require(value["prior_session_manifest_ref"] is None or bool(_SHA256.fullmatch(str(value["prior_session_manifest_ref"]))), "prior_session_manifest_ref must be a digest or null")
    _sha256(value["record_digest"], "record_digest")
    _require(value["record_digest"] == record_digest(value), "session manifest record_digest mismatch")
    if export is not None:
        validate_collector_config_export(export)
        for field in (
            "capture_id",
            "session_id",
            "case_id",
            "capture_mode",
            "test_only",
            "buffer_capacity_unit",
            "buffer_high_watermark_unit",
            "board_ref",
            "platform_ref",
            "pinmap_ref",
            "firmware_hash",
            "elf_hash",
            "build_config_hash",
            "source_commit",
        ):
            if field in {"buffer_capacity_unit", "buffer_high_watermark_unit"}:
                if field not in value and field not in export:
                    continue
                _require(value.get(field, "records") == export.get(field, "records"), f"session manifest {field} mismatch")
                continue
            _require(value[field] == export[field], f"session manifest {field} mismatch")
        _require(value["collector_config_export_ref"] == export["collector_config_export_id"], "session manifest config export reference mismatch")
        _require(value["collector_config_export_digest"] == export["record_digest"], "session manifest config export digest mismatch")


def make_session_manifest(export: Mapping[str, Any], *, prepared_at: str) -> dict[str, Any]:
    """Build an immutable P4 preparation manifest from a validated export."""

    validate_collector_config_export(export)
    result = finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "session_manifest_id": f"p4manifest:{export['capture_id'].split(':', 1)[1]}",
            "capture_id": export["capture_id"],
            "session_id": export["session_id"],
            "case_id": export["case_id"],
            "capture_mode": export["capture_mode"],
            "test_only": export["test_only"],
            "buffer_capacity_unit": export.get("buffer_capacity_unit", "records"),
            "buffer_high_watermark_unit": export.get("buffer_high_watermark_unit", "records"),
            "collector_config_export_ref": export["collector_config_export_id"],
            "collector_config_export_digest": export["record_digest"],
            "board_ref": export["board_ref"],
            "platform_ref": export["platform_ref"],
            "pinmap_ref": export["pinmap_ref"],
            "firmware_hash": export["firmware_hash"],
            "elf_hash": export["elf_hash"],
            "build_config_hash": export["build_config_hash"],
            "source_commit": export["source_commit"],
            "prepared_at": prepared_at,
            "prior_session_manifest_ref": None,
        }
    )
    validate_session_manifest(result, export=export)
    return result


def make_test_only_envelope(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap ephemeral synthetic/P2 fixture material so it cannot be hardware evidence."""

    _text(kind, "test-only envelope kind")
    _require(isinstance(payload, Mapping), "test-only envelope payload must be an object")
    return {"test_only": True, "kind": kind, "payload": deepcopy(dict(payload))}


def validate_test_only_envelope(value: Mapping[str, Any], *, expected_kind: str | None = None) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "test-only envelope must be an object")
    _required_fields(value, {"test_only", "kind", "payload"}, "test-only envelope")
    _require(value["test_only"] is True, "synthetic fixture must be marked test_only=true")
    _text(value["kind"], "test-only envelope kind")
    if expected_kind is not None:
        _require(value["kind"] == expected_kind, "test-only envelope kind mismatch")
    _require(isinstance(value["payload"], Mapping), "test-only envelope payload must be an object")
    return deepcopy(dict(value["payload"]))

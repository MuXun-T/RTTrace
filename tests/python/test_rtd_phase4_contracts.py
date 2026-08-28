from __future__ import annotations

from copy import deepcopy

import pytest

from p4_capture.contracts import (
    P4ContractError,
    canonical_json,
    finalize_record,
    make_session_manifest,
    p2_snapshot_from_export,
    p2_snapshot_source,
    validate_collector_config_export,
    validate_session_manifest,
    validate_test_only_envelope,
)
from p4_phase4_support import COMMIT, config_export


def test_config_export_projects_to_a_capture_free_p2_snapshot() -> None:
    export = config_export()
    snapshot = p2_snapshot_from_export(export)

    assert snapshot["config_hash"] == export["p2_config_hash"]
    assert "capture_id" not in snapshot
    assert snapshot["source_metadata_digest"] == __import__("hashlib").sha256(canonical_json(p2_snapshot_source(export))).hexdigest()
    assert snapshot["event_types_enabled"] == export["event_types_enabled"]
    assert "buffer_capacity_unit" not in snapshot
    assert "buffer_high_watermark_unit" not in snapshot


def test_buffer_units_are_records_for_hardware_and_legacy_test_only_is_readable() -> None:
    hardware = config_export(test_only=False)
    assert hardware["buffer_capacity_unit"] == "records"
    assert hardware["buffer_high_watermark_unit"] == "records"
    validate_collector_config_export(hardware)
    hardware_manifest = make_session_manifest(hardware, prepared_at="2026-08-24T00:00:00Z")
    assert hardware_manifest["buffer_capacity_unit"] == "records"
    assert hardware_manifest["buffer_high_watermark_unit"] == "records"
    validate_session_manifest(hardware_manifest, export=hardware)

    for field, invalid in (("buffer_capacity_unit", "bytes"), ("buffer_high_watermark_unit", "events")):
        broken = deepcopy(hardware)
        broken[field] = invalid
        broken = finalize_record(broken)
        with pytest.raises(P4ContractError, match="units"):
            validate_collector_config_export(broken)

    legacy = deepcopy(config_export())
    legacy.pop("buffer_capacity_unit")
    legacy.pop("buffer_high_watermark_unit")
    legacy = finalize_record(legacy)
    validate_collector_config_export(legacy)
    assert make_session_manifest(legacy, prepared_at="2026-08-24T00:00:00Z")["buffer_capacity_unit"] == "records"

    # P4-only annotations do not alter frozen P2 source provenance.
    assert p2_snapshot_source(config_export()) == legacy


def test_capture_identity_isolated_and_manifest_binds_complete_provenance() -> None:
    first = config_export(capture_id="capture:p4-a", session_id="session:p4-a")
    second = config_export(capture_id="capture:p4-b", session_id="session:p4-b")
    manifest = make_session_manifest(first, prepared_at="2026-08-16T00:00:00Z")

    validate_session_manifest(manifest, export=first)
    assert manifest["capture_id"] != second["capture_id"]
    assert manifest["collector_config_export_digest"] == first["record_digest"]
    assert manifest["firmware_hash"] == first["firmware_hash"]
    assert manifest["elf_hash"] == first["elf_hash"]

    crossed = deepcopy(manifest)
    crossed["capture_id"] = second["capture_id"]
    crossed = finalize_record(crossed)
    with pytest.raises(P4ContractError, match="capture_id mismatch"):
        validate_session_manifest(crossed, export=first)


@pytest.mark.parametrize(
    "field, replacement, message",
    [
        ("event_types_enabled", ["NOT_A_TRACE_EVENT"], "unsupported event type"),
        ("sampling_configuration", {"enabled": True, "mode": "unknown", "mark_sampled": False}, "unsupported sampling mode"),
        ("filter_configuration", {"event_domain_mask": 1, "drop_unknown": False, "drop_integrity": True}, "retain integrity"),
    ],
)
def test_unsupported_config_semantics_fail_closed(field: str, replacement: object, message: str) -> None:
    export = config_export()
    broken = deepcopy(export)
    broken[field] = replacement
    broken = finalize_record(broken)

    with pytest.raises(P4ContractError, match=message):
        validate_collector_config_export(broken)


def test_stale_config_hash_and_non_test_case_mode_are_rejected() -> None:
    export = config_export()
    stale = deepcopy(export)
    stale["buffer_capacity"] = 128
    stale = finalize_record(stale)
    with pytest.raises(P4ContractError, match="p2_config_hash"):
        validate_collector_config_export(stale)

    improper = deepcopy(export)
    improper["test_only"] = False
    improper["capture_mode"] = "hardware_smoke_noncase"
    improper["case_id"] = "case:forbidden"
    improper["transport_configuration"] = {
        "type": "serial",
        "endpoint": "future://uart",
        "baud_rate": 115200,
        "framing": "8N1",
        "retry_limit": 1,
        "retry_backoff_ms": 5,
    }
    improper = finalize_record(improper)
    with pytest.raises(P4ContractError, match="must not create a Case"):
        validate_collector_config_export(improper)


def test_synthetic_or_p2_fixture_requires_explicit_test_only_envelope() -> None:
    payload = {"capture_id": "capture:p4-a"}
    wrapped = {"test_only": True, "kind": "p2-join-fixture", "payload": payload}

    assert validate_test_only_envelope(wrapped, expected_kind="p2-join-fixture") == payload
    with pytest.raises(P4ContractError, match="test_only=true"):
        validate_test_only_envelope({"test_only": False, "kind": "p2-join-fixture", "payload": payload})

from __future__ import annotations

import pytest

from p4_capture.contracts import P4ContractError
from p4_capture.integrity import reconcile_collector_facts


def _facts() -> dict[str, int | str]:
    return {"observation_state": "observed", "attempted_records": 12, "accepted_records": 8, "dropped_records": 4,
            "buffer_overflow_dropped_records": 3, "rejected_payload_records": 1, "integrity_markers": 4,
            "loss_delta_total": 3, "overflow_delta_total": 3, "flushed_records": 12, "decoded_records": 12,
            "wire_frames": 2, "wire_bytes": 100, "crc_failures": 0, "truncations": 0, "backpressure_total": 0,
            "buffer_high_watermark": 64, "buffer_capacity_records": 64,
            "buffer_capacity_unit": "records", "buffer_high_watermark_unit": "records"}


def test_exact_counter_reconciliation_and_wire_inventory() -> None:
    result = reconcile_collector_facts(_facts(), wire_inventory={"frames": 2, "bytes": 100}, hardware_smoke_noncase=True)
    assert result["pass"] and result["unit"] == "records"
    broken = _facts(); broken["decoded_records"] = 11
    assert "DECODED_EQUALS_FLUSHED" in reconcile_collector_facts(broken, wire_inventory={"frames": 2, "bytes": 100})["reasons"]


def test_missing_wire_or_hardware_nulls_fail_closed() -> None:
    assert not reconcile_collector_facts(_facts())["pass"]
    with pytest.raises(P4ContractError, match="complete observed"):
        reconcile_collector_facts({"observation_state": "not_observed"}, hardware_smoke_noncase=True)


@pytest.mark.parametrize(
    "field, invalid",
    [
        ("buffer_capacity_unit", "bytes"),
        ("buffer_high_watermark_unit", "events"),
        ("buffer_capacity_unit", None),
        ("buffer_high_watermark_unit", "not_observed"),
    ],
)
def test_hardware_reconciliation_rejects_missing_or_mismatched_units(field: str, invalid: object) -> None:
    facts = _facts()
    facts[field] = invalid  # type: ignore[assignment]
    with pytest.raises(P4ContractError, match="complete observed"):
        reconcile_collector_facts(facts, wire_inventory={"frames": 2, "bytes": 100}, hardware_smoke_noncase=True)

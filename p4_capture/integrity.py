"""Pure P4 collector/transport integer reconciliation (no hardware I/O)."""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import P4ContractError


_FIELDS = (
    "attempted_records", "accepted_records", "dropped_records",
    "buffer_overflow_dropped_records", "rejected_payload_records",
    "integrity_markers", "loss_delta_total", "overflow_delta_total",
    "flushed_records", "decoded_records", "wire_frames", "wire_bytes",
    "crc_failures", "truncations", "backpressure_total",
    "buffer_high_watermark", "buffer_capacity_records",
)


def reconcile_collector_facts(
    facts: Mapping[str, Any], *, wire_inventory: Mapping[str, Any] | None = None, hardware_smoke_noncase: bool = False
) -> dict[str, Any]:
    """Return every P4 integer equation and fail closed for incomplete hardware facts.

    ``wire_inventory`` is the sealed wire-side observation: ``frames`` and
    ``bytes``.  Absence is represented as not observed, never as zero.
    """
    if not isinstance(facts, Mapping):
        raise P4ContractError("collector facts must be an object")
    observed = facts.get("observation_state") == "observed"
    missing = [name for name in _FIELDS if not isinstance(facts.get(name), int) or int(facts[name]) < 0]
    capacity_unit = facts.get("buffer_capacity_unit")
    watermark_unit = facts.get("buffer_high_watermark_unit")
    units_valid = capacity_unit == watermark_unit == "records"
    if hardware_smoke_noncase and (not observed or missing or not units_valid):
        raise P4ContractError("hardware_smoke_noncase requires complete observed collector facts")
    if not observed:
        return {"observation_state": "not_observed", "pass": False, "reasons": ["COUNTERS_NOT_OBSERVED"], "equations": {}}
    if missing:
        raise P4ContractError("observed collector facts are incomplete")
    if capacity_unit is not None or watermark_unit is not None:
        if not units_valid:
            raise P4ContractError("buffer capacity/high-watermark units must both be records")
    f = {name: int(facts[name]) for name in _FIELDS}
    equations = {
        "attempted_equals_accepted_plus_dropped": f["attempted_records"] == f["accepted_records"] + f["dropped_records"],
        "dropped_classification_closed": f["dropped_records"] == f["buffer_overflow_dropped_records"] + f["rejected_payload_records"],
        "drained_flush_closed": f["flushed_records"] == f["accepted_records"] + f["integrity_markers"],
        "decoded_equals_flushed": f["decoded_records"] == f["flushed_records"],
        "loss_delta_equals_overflow_drops": f["loss_delta_total"] == f["buffer_overflow_dropped_records"],
        "overflow_delta_equals_overflow_drops": f["overflow_delta_total"] == f["buffer_overflow_dropped_records"],
        "hwm_within_capacity_records": f["buffer_high_watermark"] <= f["buffer_capacity_records"],
    }
    if wire_inventory is None:
        equations["wire_inventory_observed"] = False
    else:
        equations["wire_inventory_observed"] = (
            isinstance(wire_inventory.get("frames"), int) and isinstance(wire_inventory.get("bytes"), int)
            and int(wire_inventory["frames"]) == f["wire_frames"] and int(wire_inventory["bytes"]) == f["wire_bytes"]
        )
    reasons = [name.upper() for name, passed in equations.items() if not passed]
    return {"observation_state": "observed", "pass": not reasons, "reasons": reasons, "equations": equations, "unit": "records"}

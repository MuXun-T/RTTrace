"""P4 non-Case capture-mode assessment; no verdict/Case material."""
from __future__ import annotations
from typing import Any, Mapping


def assess_capture_mode(mode: str, facts: Mapping[str, Any], t2: Mapping[str, Any], reconciliation: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if not t2.get("threshold_hash") or facts.get("threshold_hash") != t2.get("threshold_hash"): reasons.append("T2_THRESHOLD_MISMATCH")
    if facts.get("observation_state") != "observed" or not reconciliation.get("pass"): reasons.append("OBSERVATION_OR_RECONCILIATION_FAILED")
    if any(bool(facts.get(k)) for k in ("force", "mask", "offline_deletion", "synthetic_marker")): reasons.append("FORBIDDEN_PRESSURE_MECHANISM")
    loss, overflow = int(facts.get("lost", -1)), int(facts.get("overflow", -1))
    hwm, capacity = int(facts.get("hwm", -1)), int(facts.get("capacity", -1))
    clean_integrity = all(int(facts.get(k, -1)) == 0 for k in ("lost", "overflow", "backpressure", "crc", "truncation")) and facts.get("seq") == "continuous"
    if mode == "clean":
        if t2.get("clean_hwm") is None or not clean_integrity or hwm > int(t2.get("clean_hwm", -1)): reasons.append("CLEAN_GATE_FAILED")
        status = "PASS/admitted_clean"
    elif mode == "pressure_without_overflow":
        low, high = t2.get("pressure_low"), t2.get("pressure_high")
        if low is None or high is None or not (0 <= int(low) <= int(high) < capacity) or not clean_integrity or not int(low) <= hwm <= int(high): reasons.append("PRESSURE_BAND_FAILED")
        status = "PASS/pressure_without_overflow"
    elif mode == "natural_overflow":
        if facts.get("workload_config_hash") != t2.get("workload_config_hash") or not facts.get("natural_attestation") or loss <= 0 or overflow <= 0 or hwm != capacity or facts.get("delta_total") != overflow or facts.get("gap_total") != loss: reasons.append("NATURAL_OVERFLOW_GATE_FAILED")
        status = "expected_degraded/admitted_degraded"
    else: reasons.append("UNSUPPORTED_MODE"); status = "denied"
    return {"mode": mode, "status": status if not reasons else "denied", "reasons": reasons}

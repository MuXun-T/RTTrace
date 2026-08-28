"""P4-only admission boundary before any public parser/P3 rebuild call."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class AdmissionResult:
    status: str
    reasons: tuple[str, ...]


def admit_before_rebuild(*, ccm: Mapping[str, Any], cir: Mapping[str, Any], context: Mapping[str, Any], counter_reconciliation: Mapping[str, Any], decoder: Mapping[str, Any], alignment: Mapping[str, Any], threshold_hash: str | None, rebuild: Callable[[], Any] | None = None) -> AdmissionResult:
    """Validate retained P4 inputs before invoking ``rebuild``; never makes Case/OAR/CVR."""
    reasons: list[str] = []
    identities = {ccm.get("capture_id"), cir.get("capture_id"), context.get("capture_id"), decoder.get("capture_id"), alignment.get("capture_id")}
    sessions = {ccm.get("session_id"), cir.get("session_id"), context.get("session_id"), decoder.get("session_id"), alignment.get("session_id")}
    if len(identities) != 1 or None in identities or len(sessions) != 1 or None in sessions: reasons.append("IDENTITY_CROSSING")
    if cir.get("capability_manifest_id") != ccm.get("capability_manifest_id") or cir.get("capability_manifest_digest") != ccm.get("record_digest"): reasons.append("CCM_CIR_MISMATCH")
    if decoder.get("raw_state") != "available" or decoder.get("decoder_state") not in {"complete", "degraded"}: reasons.append("RAW_OR_DECODER_INVALID")
    if not counter_reconciliation.get("pass"): reasons.append("COUNTER_RECONCILIATION_FAILED")
    bound = alignment.get("alignment_error_bound")
    if alignment.get("state") != "aligned" or not isinstance(bound, (int, float)) or not isfinite(bound) or not threshold_hash or alignment.get("threshold_hash") != threshold_hash: reasons.append("ALIGNMENT_INADMISSIBLE")
    if reasons: return AdmissionResult("denied", tuple(reasons))
    degraded = cir.get("integrity_status") == "degraded" and bool(cir.get("natural_overflow"))
    clean = cir.get("integrity_status") == "complete" and not any(bool(cir.get(k)) for k in ("loss", "overflow", "truncation", "corruption", "mapping_mismatch"))
    if not clean and not degraded: return AdmissionResult("denied", ("CIR_NOT_ADMISSIBLE",))
    if rebuild is not None: rebuild()
    return AdmissionResult("admitted_degraded" if degraded else "admitted_clean", ())

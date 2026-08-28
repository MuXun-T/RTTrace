"""P4 observer and alignment input contracts, without observer adjudication."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .contracts import P4ContractError, P4_SCHEMA_VERSION, _sha256, _text, finalize_record, record_digest, validate_capture_identity


_STATES = frozenset({"pending", "missing", "partial", "corrupt", "unalignable", "test_only", "aligned"})
_ALIGNMENT_INPUT_FIELDS = {
    "schema_version",
    "alignment_input_id",
    "capture_id",
    "session_id",
    "observer_inventory_ref",
    "observer_inventory_digest",
    "shared_epoch_ref",
    "shared_epoch_digest",
    "raw_trace_id",
    "raw_trace_hash",
    "algorithm_id",
    "algorithm_version",
    "test_only",
    "record_digest",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise P4ContractError(message)


def make_observer_artifact_inventory(
    *,
    capture_id: str,
    session_id: str,
    observer_artifact_hash: str | None,
    state: str,
    test_only: bool,
    logical_name: str,
) -> dict[str, Any]:
    validate_capture_identity(capture_id, session_id, None, "test_only" if test_only else "hardware_smoke_noncase", test_only)
    _require(state in _STATES, "unsupported observer state")
    _text(logical_name, "observer logical_name")
    if state in {"test_only", "aligned"}:
        _sha256(observer_artifact_hash, "observer_artifact_hash")
    else:
        _require(observer_artifact_hash is None, "unavailable observer must not claim a hash")
    return finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "observer_inventory_id": f"p4observer:{capture_id.split(':', 1)[1]}",
            "capture_id": capture_id,
            "session_id": session_id,
            "observer_artifact_hash": observer_artifact_hash,
            "logical_name": logical_name,
            "state": state,
            "test_only": test_only,
        }
    )


def make_shared_epoch_record(
    *,
    capture_id: str,
    session_id: str,
    epoch_id: str,
    trace_sequence: int | None,
    trace_timestamp: int | None,
    observer_timestamp: float | None,
    test_only: bool,
) -> dict[str, Any]:
    validate_capture_identity(capture_id, session_id, None, "test_only" if test_only else "hardware_smoke_noncase", test_only)
    _text(epoch_id, "epoch_id")
    for value, label in ((trace_sequence, "trace_sequence"), (trace_timestamp, "trace_timestamp")):
        _require(value is None or isinstance(value, int) and value >= 0, f"{label} is invalid")
    _require(observer_timestamp is None or isinstance(observer_timestamp, (int, float)), "observer_timestamp is invalid")
    return finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "shared_epoch_id": f"p4epoch:{epoch_id}",
            "capture_id": capture_id,
            "session_id": session_id,
            "epoch_id": epoch_id,
            "trace_sequence": trace_sequence,
            "trace_timestamp": trace_timestamp,
            "observer_timestamp": observer_timestamp,
            "test_only": test_only,
        }
    )


def make_alignment_report(
    observer: Mapping[str, Any], epoch: Mapping[str, Any], *, state: str, alignment_error_bound: float | None, algorithm: str, test_only: bool
) -> dict[str, Any]:
    _require(state in _STATES, "unsupported alignment state")
    _require(observer["capture_id"] == epoch["capture_id"] and observer["session_id"] == epoch["session_id"], "observer/epoch identity mismatch")
    _require(observer.get("test_only") is test_only and epoch.get("test_only") is test_only, "alignment test_only mismatch")
    _text(algorithm, "alignment algorithm")
    if test_only:
        _require(state != "aligned", "test-only alignment must use state=test_only, never aligned")
    if state in {"aligned", "test_only"}:
        _require(isinstance(alignment_error_bound, (int, float)) and alignment_error_bound >= 0, "alignment_error_bound is required")
    else:
        _require(alignment_error_bound is None, "unavailable alignment must have null alignment_error_bound")
    return finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "alignment_report_id": f"p4alignment:{epoch['epoch_id']}",
            "capture_id": observer["capture_id"],
            "session_id": observer["session_id"],
            "observer_inventory_ref": observer["observer_inventory_id"],
            "observer_inventory_digest": observer["record_digest"],
            "shared_epoch_ref": epoch["shared_epoch_id"],
            "shared_epoch_digest": epoch["record_digest"],
            "state": state,
            "alignment_error_bound": alignment_error_bound,
            "algorithm": algorithm,
            "test_only": test_only,
        }
    )


def make_alignment_input(
    observer: Mapping[str, Any],
    epoch: Mapping[str, Any],
    *,
    raw_trace_id: str,
    raw_trace_hash: str | None,
    algorithm_id: str,
    algorithm_version: str,
    test_only: bool,
) -> dict[str, Any]:
    """Bind observer and trace epoch material before an alignment is attempted.

    The input is deliberately not an OAR and carries no manifestation or
    capture-validity conclusion.
    """

    _require(observer.get("capture_id") == epoch.get("capture_id"), "observer/epoch capture_id mismatch")
    _require(observer.get("session_id") == epoch.get("session_id"), "observer/epoch session_id mismatch")
    _require(observer.get("test_only") is test_only and epoch.get("test_only") is test_only, "alignment input test_only mismatch")
    _text(raw_trace_id, "raw_trace_id")
    _sha256(raw_trace_hash, "raw_trace_hash", nullable=True)
    _text(algorithm_id, "alignment algorithm_id")
    _text(algorithm_version, "alignment algorithm_version")
    result = finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "alignment_input_id": f"p4alignment-input:{epoch['epoch_id']}",
            "capture_id": observer["capture_id"],
            "session_id": observer["session_id"],
            "observer_inventory_ref": observer["observer_inventory_id"],
            "observer_inventory_digest": observer["record_digest"],
            "shared_epoch_ref": epoch["shared_epoch_id"],
            "shared_epoch_digest": epoch["record_digest"],
            "raw_trace_id": raw_trace_id,
            "raw_trace_hash": raw_trace_hash,
            "algorithm_id": algorithm_id,
            "algorithm_version": algorithm_version,
            "test_only": test_only,
        }
    )
    validate_alignment_input(result, observer=observer, epoch=epoch)
    return result


def validate_alignment_input(value: Mapping[str, Any], *, observer: Mapping[str, Any], epoch: Mapping[str, Any]) -> None:
    _require(isinstance(value, Mapping) and set(value) == _ALIGNMENT_INPUT_FIELDS, "alignment input has unsupported or missing fields")
    _require(value.get("schema_version") == P4_SCHEMA_VERSION, "unsupported alignment input schema version")
    _require(value.get("record_digest") == record_digest(value), "alignment input digest mismatch")
    _require(value.get("capture_id") == observer.get("capture_id") == epoch.get("capture_id"), "alignment input capture_id mismatch")
    _require(value.get("session_id") == observer.get("session_id") == epoch.get("session_id"), "alignment input session_id mismatch")
    _require(value.get("observer_inventory_ref") == observer.get("observer_inventory_id"), "alignment input observer reference mismatch")
    _require(value.get("observer_inventory_digest") == observer.get("record_digest"), "alignment input observer digest mismatch")
    _require(value.get("shared_epoch_ref") == epoch.get("shared_epoch_id"), "alignment input epoch reference mismatch")
    _require(value.get("shared_epoch_digest") == epoch.get("record_digest"), "alignment input epoch digest mismatch")
    _text(value.get("raw_trace_id"), "raw_trace_id")
    _sha256(value.get("raw_trace_hash"), "raw_trace_hash", nullable=True)
    _text(value.get("algorithm_id"), "alignment algorithm_id")
    _text(value.get("algorithm_version"), "alignment algorithm_version")
    _require(value.get("test_only") is observer.get("test_only") is epoch.get("test_only"), "alignment input test_only mismatch")


def bind_alignment_input(
    report: Mapping[str, Any],
    alignment_input: Mapping[str, Any],
    *,
    observer: Mapping[str, Any],
    epoch: Mapping[str, Any],
) -> dict[str, Any]:
    """Add a verified alignment-input reference to an immutable P4 report."""

    validate_alignment_report(report, observer=observer, epoch=epoch)
    validate_alignment_input(alignment_input, observer=observer, epoch=epoch)
    _require(report.get("capture_id") == alignment_input.get("capture_id"), "alignment report/input capture_id mismatch")
    _require(report.get("session_id") == alignment_input.get("session_id"), "alignment report/input session_id mismatch")
    result = dict(report)
    result["alignment_input_ref"] = alignment_input["alignment_input_id"]
    result["alignment_input_digest"] = alignment_input["record_digest"]
    return finalize_record(result)


def validate_alignment_report(value: Mapping[str, Any], *, observer: Mapping[str, Any], epoch: Mapping[str, Any]) -> None:
    _require(value.get("schema_version") == P4_SCHEMA_VERSION, "unsupported alignment schema version")
    for field in ("capture_id", "session_id"):
        _require(value.get(field) == observer.get(field) == epoch.get(field), f"alignment {field} mismatch")
    _require(value.get("observer_inventory_ref") == observer.get("observer_inventory_id"), "alignment observer reference mismatch")
    _require(value.get("observer_inventory_digest") == observer.get("record_digest"), "alignment observer digest mismatch")
    _require(value.get("shared_epoch_ref") == epoch.get("shared_epoch_id"), "alignment epoch reference mismatch")
    _require(value.get("shared_epoch_digest") == epoch.get("record_digest"), "alignment epoch digest mismatch")
    _require(value.get("record_digest") == record_digest(value), "alignment report digest mismatch")
    _require(value.get("state") in _STATES, "unsupported alignment state")
    if value["state"] in {"aligned", "test_only"}:
        _require(isinstance(value.get("alignment_error_bound"), (int, float)) and value["alignment_error_bound"] >= 0, "alignment error bound is invalid")
    else:
        _require(value.get("alignment_error_bound") is None, "unalignable alignment must have null error bound")
    if value.get("test_only") is True:
        _require(value.get("state") != "aligned", "test-only alignment must not claim aligned")

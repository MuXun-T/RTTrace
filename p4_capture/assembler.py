"""Offline P4 integrity inputs and the sole CCM/CIR assembly boundary.

The collector reports bytes/counters, the decoder reports parse integrity, and
P3 consumes only a ``CaptureLineageContext``.  This module is the only P4
surface that combines those inputs into frozen P2 CCM/CIR records.  It never
writes Ledger, OAR, or CVR records.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from parser.codec import decode_trace
from parser.rtd_lineage import CaptureLineageContext, LineageValidationError
from spec.rtd_pilot_contracts import (
    SCHEMA_VERSION,
    ContractError,
    finalize_record as finalize_p2_record,
    validate_ccm,
    validate_cir,
    validate_snapshot,
)

from .contracts import (
    P4_SCHEMA_VERSION,
    P4ContractError,
    _sha256,
    _text,
    canonical_json,
    file_digest,
    finalize_record,
    p2_snapshot_source,
    record_digest,
    validate_collector_config_export,
)


class P4AssemblyError(P4ContractError):
    """A P4 capture input cannot be assembled without crossing contracts."""


_COUNTER_FIELDS = {
    "schema_version",
    "counter_report_id",
    "capture_id",
    "session_id",
    "config_export_ref",
    "config_export_digest",
    "raw_trace_id",
    "raw_trace_hash",
    "observation_state",
    "lost_total",
    "overflow_total",
    "io_backpressure_total",
    "written_records",
    "flushed_records",
    "buffer_high_watermark",
    "buffer_capacity_unit",
    "buffer_high_watermark_unit",
    "natural_overflow",
    "counter_source",
    "collector_version",
    "test_only",
    "record_digest",
}
_DECODER_FIELDS = {
    "schema_version",
    "decoder_report_id",
    "capture_id",
    "session_id",
    "config_export_ref",
    "config_export_digest",
    "raw_trace_id",
    "raw_trace_hash",
    "raw_state",
    "header_capture_id",
    "decoder_state",
    "sequence_continuity",
    "sequence_gaps",
    "loss",
    "lost_count",
    "overflow",
    "overflow_count",
    "truncation",
    "corruption",
    "mapping_mismatch",
    "affected_intervals",
    "reason_codes",
    "decoded_event_count",
    "buffer_capacity_unit",
    "buffer_high_watermark_unit",
    "test_only",
    "record_digest",
}
_ASSEMBLY_INPUT_FIELDS = {
    "schema_version",
    "assembly_input_id",
    "capture_id",
    "session_id",
    "test_only",
    "config_export_ref",
    "config_export_digest",
    "p2_snapshot_ref",
    "p2_snapshot_digest",
    "raw_trace_id",
    "raw_trace_hash",
    "counter_report_ref",
    "counter_report_digest",
    "decoder_report_ref",
    "decoder_report_digest",
    "alignment_report_ref",
    "alignment_report_digest",
    "buffer_capacity_unit",
    "buffer_high_watermark_unit",
    "record_digest",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise P4AssemblyError(message)


def _identifier(value: Any, label: str) -> str:
    _require(isinstance(value, str) and value and value.isascii() and all(char.isalnum() or char in "._:-" for char in value), f"{label} must be an ASCII identifier")
    return value


def _hash_or_none(value: Any, label: str) -> str | None:
    if value is None:
        return None
    _sha256(value, label)
    return str(value)


def _raw_fields(raw: Mapping[str, Any]) -> tuple[str, str, str | None, str, bool]:
    _require(isinstance(raw, Mapping), "raw artifact must be an object")
    artifact_id = _identifier(raw.get("artifact_id"), "raw artifact_id")
    _require(raw.get("kind") == "raw_trace", "raw artifact kind must be raw_trace")
    state = raw.get("state")
    _require(state in {"available", "missing", "partial", "corrupt"}, "unsupported raw artifact state")
    digest = raw.get("hash", raw.get("sha256"))
    if state == "available":
        _sha256(digest, "raw artifact hash")
    elif digest is not None:
        _sha256(digest, "raw artifact hash")
    test_only = raw.get("test_only")
    _require(isinstance(test_only, bool), "raw artifact must declare test_only")
    return artifact_id, state, digest, str(raw.get("logical_name", "")), test_only


def _validate_record_digest(value: Mapping[str, Any], label: str) -> None:
    _sha256(value.get("record_digest"), f"{label} record_digest")
    _require(value["record_digest"] == record_digest(value), f"{label} record_digest mismatch")


def _report_identity(value: Mapping[str, Any], export: Mapping[str, Any], label: str) -> None:
    for field in ("capture_id", "session_id", "test_only"):
        _require(value.get(field) == export.get(field), f"{label} {field} mismatch")
    _require(value.get("config_export_ref") == export.get("collector_config_export_id"), f"{label} config export reference mismatch")
    _require(value.get("config_export_digest") == export.get("record_digest"), f"{label} config export digest mismatch")


def _nonnegative_or_none(value: Any, label: str) -> int | None:
    if value is None:
        return None
    _require(isinstance(value, int) and value >= 0, f"{label} must be a non-negative integer or null")
    return value


def make_counter_report(
    export: Mapping[str, Any],
    raw_artifact: Mapping[str, Any],
    *,
    observation_state: str,
    lost_total: int | None,
    overflow_total: int | None,
    io_backpressure_total: int | None,
    written_records: int | None,
    flushed_records: int | None,
    buffer_high_watermark: int | None,
    natural_overflow: bool | None,
    counter_source: str,
) -> dict[str, Any]:
    """Create a P4 collector counter sidecar without inventing unobserved zeroes."""

    validate_collector_config_export(export)
    raw_id, state, raw_hash, _, raw_test_only = _raw_fields(raw_artifact)
    _require(raw_test_only == export["test_only"], "raw artifact test_only mismatch")
    _require(observation_state in {"observed", "not_observed"}, "unsupported counter observation_state")
    values = (lost_total, overflow_total, io_backpressure_total, written_records, flushed_records, buffer_high_watermark)
    for value, label in zip(values, ("lost_total", "overflow_total", "io_backpressure_total", "written_records", "flushed_records", "buffer_high_watermark")):
        _nonnegative_or_none(value, label)
    _require(natural_overflow is None or isinstance(natural_overflow, bool), "natural_overflow must be boolean or null")
    if observation_state == "not_observed":
        _require(all(value is None for value in values) and natural_overflow is None, "unobserved counters must be null, never synthesized as zero")
    else:
        _require(all(value is not None for value in values) and natural_overflow is not None, "observed counters must be complete")
        _require(buffer_high_watermark is not None and buffer_high_watermark <= export["buffer_capacity"], "counter high watermark exceeds configured capacity")
    _text(counter_source, "counter_source")
    result = finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "counter_report_id": f"p4counter:{export['capture_id'].split(':', 1)[1]}",
            "capture_id": export["capture_id"],
            "session_id": export["session_id"],
            "config_export_ref": export["collector_config_export_id"],
            "config_export_digest": export["record_digest"],
            "raw_trace_id": raw_id,
            "raw_trace_hash": raw_hash,
            "observation_state": observation_state,
            "lost_total": lost_total,
            "overflow_total": overflow_total,
            "io_backpressure_total": io_backpressure_total,
            "written_records": written_records,
            "flushed_records": flushed_records,
            "buffer_high_watermark": buffer_high_watermark,
            "buffer_capacity_unit": export.get("buffer_capacity_unit", "records"),
            "buffer_high_watermark_unit": export.get("buffer_high_watermark_unit", "records"),
            "natural_overflow": natural_overflow,
            "counter_source": counter_source,
            "collector_version": export["collector_version"],
            "test_only": export["test_only"],
        }
    )
    validate_counter_report(result, export=export, raw_artifact=raw_artifact)
    return result


def validate_counter_report(value: Mapping[str, Any], *, export: Mapping[str, Any], raw_artifact: Mapping[str, Any]) -> None:
    legacy_fields = _COUNTER_FIELDS - {"buffer_capacity_unit", "buffer_high_watermark_unit"}
    _require(
        isinstance(value, Mapping) and (set(value) == _COUNTER_FIELDS or (value.get("test_only") is True and set(value) == legacy_fields)),
        "counter report has unsupported or missing fields",
    )
    _require(value.get("schema_version") == P4_SCHEMA_VERSION, "unsupported counter report schema version")
    _identifier(value.get("counter_report_id"), "counter_report_id")
    _report_identity(value, export, "counter report")
    raw_id, _, raw_hash, _, _ = _raw_fields(raw_artifact)
    _require(value.get("raw_trace_id") == raw_id and value.get("raw_trace_hash") == raw_hash, "counter report raw binding mismatch")
    _require(value.get("observation_state") in {"observed", "not_observed"}, "unsupported counter observation_state")
    if "buffer_capacity_unit" in value or "buffer_high_watermark_unit" in value:
        _require(value.get("buffer_capacity_unit") == value.get("buffer_high_watermark_unit") == "records", "counter capacity/high-watermark units must both be records")
    else:
        _require(value.get("test_only") is True, "hardware_smoke_noncase counter units are required")
    fields = ("lost_total", "overflow_total", "io_backpressure_total", "written_records", "flushed_records", "buffer_high_watermark")
    values = [_nonnegative_or_none(value.get(field), field) for field in fields]
    natural_overflow = value.get("natural_overflow")
    _require(natural_overflow is None or isinstance(natural_overflow, bool), "natural_overflow must be boolean or null")
    if value["observation_state"] == "not_observed":
        _require(all(item is None for item in values) and natural_overflow is None, "unobserved counters must be null")
    else:
        _require(all(item is not None for item in values) and natural_overflow is not None, "observed counters are incomplete")
        _require(int(value["buffer_high_watermark"]) <= int(export["buffer_capacity"]), "counter high watermark exceeds configured capacity")
    _text(value.get("counter_source"), "counter_source")
    _require(value.get("collector_version") == export.get("collector_version"), "counter report collector version mismatch")
    _validate_record_digest(value, "counter report")


def _window_dict(window: Any) -> dict[str, Any]:
    return {
        "window_id": str(getattr(window, "window_id", "")),
        "source": str(getattr(window, "source", "")),
        "reason_code": str(getattr(window, "reason_code", "")),
        "start": float(getattr(window, "t_begin", 0.0)),
        "end": float(getattr(window, "t_end", 0.0)),
    }


def _decoder_report(
    export: Mapping[str, Any],
    raw_artifact: Mapping[str, Any],
    *,
    raw_state: str,
    header_capture_id: str | None,
    decoder_state: str,
    sequence_continuity: str,
    sequence_gaps: list[dict[str, Any]],
    loss: bool,
    lost_count: int,
    overflow: bool,
    overflow_count: int,
    truncation: bool,
    corruption: bool,
    mapping_mismatch: bool,
    affected_intervals: list[dict[str, Any]],
    reason_codes: list[str],
    decoded_event_count: int,
) -> dict[str, Any]:
    raw_id, _, raw_hash, _, _ = _raw_fields(raw_artifact)
    result = finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "decoder_report_id": f"p4decoder:{export['capture_id'].split(':', 1)[1]}",
            "capture_id": export["capture_id"],
            "session_id": export["session_id"],
            "config_export_ref": export["collector_config_export_id"],
            "config_export_digest": export["record_digest"],
            "raw_trace_id": raw_id,
            "raw_trace_hash": raw_hash,
            "raw_state": raw_state,
            "header_capture_id": header_capture_id,
            "decoder_state": decoder_state,
            "sequence_continuity": sequence_continuity,
            "sequence_gaps": sequence_gaps,
            "loss": loss,
            "lost_count": lost_count,
            "overflow": overflow,
            "overflow_count": overflow_count,
            "truncation": truncation,
            "corruption": corruption,
            "mapping_mismatch": mapping_mismatch,
            "affected_intervals": affected_intervals,
            "reason_codes": reason_codes,
            "decoded_event_count": decoded_event_count,
            "buffer_capacity_unit": export.get("buffer_capacity_unit", "records"),
            "buffer_high_watermark_unit": export.get("buffer_high_watermark_unit", "records"),
            "test_only": export["test_only"],
        }
    )
    validate_decoder_report(result, export=export, raw_artifact=raw_artifact)
    return result


def decode_integrity_report(
    export: Mapping[str, Any], raw_artifact: Mapping[str, Any], trace_path: str | Path
) -> dict[str, Any]:
    """Decode an imported trace into a P4 report; this is not hardware evidence."""

    validate_collector_config_export(export)
    raw_id, raw_state, raw_hash, _, raw_test_only = _raw_fields(raw_artifact)
    _require(raw_test_only == export["test_only"], "raw artifact test_only mismatch")
    if raw_state != "available":
        return _decoder_report(
            export,
            raw_artifact,
            raw_state=raw_state,
            header_capture_id=None,
            decoder_state="invalid",
            sequence_continuity="gaps",
            sequence_gaps=[],
            loss=False,
            lost_count=0,
            overflow=False,
            overflow_count=0,
            truncation=raw_state == "partial",
            corruption=raw_state == "corrupt",
            mapping_mismatch=False,
            affected_intervals=[],
            reason_codes=[f"RAW_{raw_state.upper()}"],
            decoded_event_count=0,
        )
    path = Path(trace_path)
    _require(path.is_file(), "available raw artifact path is missing")
    actual_hash = file_digest(path.read_bytes())
    _require(actual_hash == raw_hash, "raw trace hash does not match imported inventory")
    decoded = decode_trace(path, dataset_id=str(export["capture_id"]))
    if not decoded.ok:
        message = decoded.message.lower()
        truncation = "truncat" in message
        corruption = "crc" in message or "magic" in message or "invalid" in message
        return _decoder_report(
            export,
            raw_artifact,
            raw_state=raw_state,
            header_capture_id=None,
            decoder_state="invalid",
            sequence_continuity="gaps",
            sequence_gaps=[],
            loss=False,
            lost_count=0,
            overflow=False,
            overflow_count=0,
            truncation=truncation,
            corruption=corruption,
            mapping_mismatch=False,
            affected_intervals=[],
            reason_codes=["DECODE_FAILED"],
            decoded_event_count=0,
        )
    payload = decoded.data
    assert payload is not None
    header = payload["header"]
    windows = list(payload["untrusted_windows"])
    window_dicts = [_window_dict(window) for window in windows]
    reasons = sorted({str(item["reason_code"]) for item in window_dicts if item["reason_code"]})
    sequence_gaps = [item for item in window_dicts if item["reason_code"] == "SEQ_GAP"]
    loss_windows = [item for item in window_dicts if item["source"] == "loss"]
    overflow_windows = [item for item in window_dicts if item["source"] in {"overflow", "io_backpressure"}]
    truncation = any(item["source"] == "truncate" for item in window_dicts)
    corruption = any(item["source"] == "crc_fail" for item in window_dicts)
    mapping_mismatch = any(item["reason_code"] in {"DICT_MISMATCH", "SEGMENT_DICT_CONFLICT", "UNSUPPORTED_MAPPING"} for item in window_dicts)
    lost_count = 0
    overflow_count = 0
    for event in payload["events"]:
        if event.event_name == "LOSS":
            lost_count += int(event.payload.get("lost_count", 1) or 1)
        elif event.event_name == "OVERFLOW":
            overflow_count += int(event.payload.get("overflow_count", 1) or 1)
    header_capture_id = header.run_id
    if header_capture_id != export["capture_id"]:
        reasons.append("RAW_HEADER_CAPTURE_ID_MISMATCH")
    degraded = bool(window_dicts) or header_capture_id != export["capture_id"]
    return _decoder_report(
        export,
        raw_artifact,
        raw_state=raw_state,
        header_capture_id=header_capture_id,
        decoder_state="degraded" if degraded else "complete",
        sequence_continuity="gaps" if sequence_gaps else "continuous",
        sequence_gaps=sequence_gaps,
        loss=bool(loss_windows),
        lost_count=lost_count,
        overflow=bool(overflow_windows),
        overflow_count=overflow_count,
        truncation=truncation,
        corruption=corruption,
        mapping_mismatch=mapping_mismatch,
        affected_intervals=window_dicts,
        reason_codes=sorted(set(reasons)),
        decoded_event_count=len(payload["events"]),
    )


def validate_decoder_report(value: Mapping[str, Any], *, export: Mapping[str, Any], raw_artifact: Mapping[str, Any]) -> None:
    legacy_fields = _DECODER_FIELDS - {"buffer_capacity_unit", "buffer_high_watermark_unit"}
    _require(
        isinstance(value, Mapping) and (set(value) == _DECODER_FIELDS or (value.get("test_only") is True and set(value) == legacy_fields)),
        "decoder report has unsupported or missing fields",
    )
    _require(value.get("schema_version") == P4_SCHEMA_VERSION, "unsupported decoder report schema version")
    _identifier(value.get("decoder_report_id"), "decoder_report_id")
    _report_identity(value, export, "decoder report")
    if "buffer_capacity_unit" in value or "buffer_high_watermark_unit" in value:
        _require(
            value.get("buffer_capacity_unit") == value.get("buffer_high_watermark_unit") == "records",
            "decoder buffer units must both be records",
        )
    else:
        _require(value.get("test_only") is True, "hardware_smoke_noncase decoder unit is required")
    raw_id, raw_state, raw_hash, _, _ = _raw_fields(raw_artifact)
    _require(value.get("raw_trace_id") == raw_id and value.get("raw_trace_hash") == raw_hash, "decoder report raw binding mismatch")
    _require(value.get("raw_state") == raw_state, "decoder report raw state mismatch")
    _require(value.get("header_capture_id") is None or isinstance(value.get("header_capture_id"), str), "decoder header_capture_id must be text or null")
    _require(value.get("decoder_state") in {"complete", "degraded", "invalid"}, "unsupported decoder state")
    _require(value.get("sequence_continuity") in {"continuous", "gaps"}, "unsupported sequence continuity")
    _require(isinstance(value.get("sequence_gaps"), list), "sequence_gaps must be an array")
    if value["sequence_continuity"] == "continuous":
        _require(not value["sequence_gaps"], "continuous decoder report cannot list sequence gaps")
    for field in ("loss", "overflow", "truncation", "corruption", "mapping_mismatch", "test_only"):
        _require(isinstance(value.get(field), bool), f"decoder {field} must be boolean")
    for field in ("lost_count", "overflow_count", "decoded_event_count"):
        _require(isinstance(value.get(field), int) and value[field] >= 0, f"decoder {field} must be non-negative")
    _require(isinstance(value.get("affected_intervals"), list), "affected_intervals must be an array")
    _require(isinstance(value.get("reason_codes"), list) and all(isinstance(item, str) and item for item in value["reason_codes"]), "reason_codes must be non-empty strings")
    _require(len(value["reason_codes"]) == len(set(value["reason_codes"])), "decoder reason_codes contain duplicates")
    if value["decoder_state"] == "complete":
        _require(raw_state == "available" and value["header_capture_id"] == export["capture_id"], "complete decoder report requires matching available raw")
        _require(not any((value["loss"], value["overflow"], value["truncation"], value["corruption"], value["mapping_mismatch"])), "complete decoder report cannot hide degradation")
        _require(value["sequence_continuity"] == "continuous" and not value["reason_codes"], "complete decoder report cannot retain degradation reasons")
    _validate_record_digest(value, "decoder report")


def make_capture_assembly_input(
    export: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    raw_artifact: Mapping[str, Any],
    counter: Mapping[str, Any],
    decoder: Mapping[str, Any],
    alignment: Mapping[str, Any],
) -> dict[str, Any]:
    """Record all immutable P4 inputs before constructing P2 CCM/CIR."""

    validate_collector_config_export(export)
    try:
        validate_snapshot(dict(snapshot))
    except (ContractError, KeyError, TypeError) as exc:
        raise P4AssemblyError(f"invalid P2 config snapshot: {exc}") from exc
    _require(snapshot["source_metadata_digest"] == file_digest(canonical_json(p2_snapshot_source(export))), "P2 snapshot is stale for this config export")
    validate_counter_report(counter, export=export, raw_artifact=raw_artifact)
    validate_decoder_report(decoder, export=export, raw_artifact=raw_artifact)
    _require(alignment.get("capture_id") == export["capture_id"], "alignment capture_id mismatch")
    _require(alignment.get("session_id") == export["session_id"], "alignment session_id mismatch")
    _require(alignment.get("test_only") == export["test_only"], "alignment test_only mismatch")
    _validate_record_digest(alignment, "alignment report")
    raw_id, _, raw_hash, _, _ = _raw_fields(raw_artifact)
    result = finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "assembly_input_id": f"p4assembly:{export['capture_id'].split(':', 1)[1]}",
            "capture_id": export["capture_id"],
            "session_id": export["session_id"],
            "test_only": export["test_only"],
            "config_export_ref": export["collector_config_export_id"],
            "config_export_digest": export["record_digest"],
            "p2_snapshot_ref": snapshot["config_snapshot_id"],
            "p2_snapshot_digest": snapshot["record_digest"],
            "raw_trace_id": raw_id,
            "raw_trace_hash": raw_hash,
            "counter_report_ref": counter["counter_report_id"],
            "counter_report_digest": counter["record_digest"],
            "decoder_report_ref": decoder["decoder_report_id"],
            "decoder_report_digest": decoder["record_digest"],
            "alignment_report_ref": alignment["alignment_report_id"],
            "alignment_report_digest": alignment["record_digest"],
            "buffer_capacity_unit": export.get("buffer_capacity_unit", "records"),
            "buffer_high_watermark_unit": counter.get("buffer_high_watermark_unit", "records"),
        }
    )
    validate_capture_assembly_input(result, export=export, snapshot=snapshot, raw_artifact=raw_artifact, counter=counter, decoder=decoder, alignment=alignment)
    return result


def validate_capture_assembly_input(
    value: Mapping[str, Any],
    *,
    export: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    raw_artifact: Mapping[str, Any],
    counter: Mapping[str, Any],
    decoder: Mapping[str, Any],
    alignment: Mapping[str, Any],
) -> None:
    legacy_fields = _ASSEMBLY_INPUT_FIELDS - {"buffer_capacity_unit", "buffer_high_watermark_unit"}
    _require(
        isinstance(value, Mapping) and (set(value) == _ASSEMBLY_INPUT_FIELDS or (value.get("test_only") is True and set(value) == legacy_fields)),
        "assembly input has unsupported or missing fields",
    )
    _require(value.get("schema_version") == P4_SCHEMA_VERSION, "unsupported assembly input schema version")
    _identifier(value.get("assembly_input_id"), "assembly_input_id")
    _report_identity(value, export, "assembly input")
    if "buffer_capacity_unit" in value or "buffer_high_watermark_unit" in value:
        _require(value.get("buffer_capacity_unit") == value.get("buffer_high_watermark_unit") == "records", "assembly capacity/high-watermark units must both be records")
        _require(value["buffer_capacity_unit"] == export.get("buffer_capacity_unit", "records"), "assembly capacity unit mismatch")
        _require(value["buffer_high_watermark_unit"] == counter.get("buffer_high_watermark_unit", "records"), "assembly high-watermark unit mismatch")
    else:
        _require(value.get("test_only") is True, "hardware_smoke_noncase assembly units are required")
    raw_id, _, raw_hash, _, _ = _raw_fields(raw_artifact)
    expected = {
        "p2_snapshot_ref": snapshot["config_snapshot_id"],
        "p2_snapshot_digest": snapshot["record_digest"],
        "raw_trace_id": raw_id,
        "raw_trace_hash": raw_hash,
        "counter_report_ref": counter["counter_report_id"],
        "counter_report_digest": counter["record_digest"],
        "decoder_report_ref": decoder["decoder_report_id"],
        "decoder_report_digest": decoder["record_digest"],
        "alignment_report_ref": alignment["alignment_report_id"],
        "alignment_report_digest": alignment["record_digest"],
    }
    for field, expected_value in expected.items():
        _require(value.get(field) == expected_value, f"assembly input {field} mismatch")
    _validate_record_digest(value, "assembly input")


def _p2_ccm(export: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "event_types_enabled",
        "task_filter",
        "resource_filter",
        "irq_filter",
        "core_filter",
        "sampling_configuration",
        "buffer_capacity",
        "flush_policy",
        "timestamp_source",
        "clock_resolution",
        "payload_fields",
        "dictionary_version",
        "mapping_version",
        "collector_version",
        "trace_start_boundary",
        "trace_end_boundary",
    )
    for field in fields:
        _require(snapshot[field] == export[field], f"P2 snapshot {field} mismatch with config export")
    _require(snapshot["config_hash"] == export["p2_config_hash"], "P2 snapshot config hash mismatch")
    return finalize_p2_record(
        {
            "schema_version": SCHEMA_VERSION,
            "capability_manifest_id": f"ccm:{export['capture_id'].split(':', 1)[1]}",
            "capture_id": export["capture_id"],
            "session_id": export["session_id"],
            "config_snapshot_id": snapshot["config_snapshot_id"],
            "config_snapshot_digest": snapshot["record_digest"],
            "collector_config_hash": snapshot["config_hash"],
            **{field: deepcopy(snapshot[field]) for field in fields},
            "prior_capability_ref": None,
        }
    )


def _counter_decoder_consistent(counter: Mapping[str, Any], decoder: Mapping[str, Any]) -> None:
    if counter["observation_state"] != "observed":
        return
    _require(int(counter["lost_total"]) >= int(decoder["lost_count"]), "counter/decoder LOSS contradiction")
    _require(int(counter["overflow_total"]) >= int(decoder["overflow_count"]), "counter/decoder OVERFLOW contradiction")
    if int(counter["lost_total"]) > 0:
        _require(decoder["loss"], "counter reports LOSS that decoder did not observe")
    if int(counter["overflow_total"]) > 0:
        _require(decoder["overflow"], "counter reports OVERFLOW that decoder did not observe")


def _alignment_state(alignment: Mapping[str, Any]) -> str:
    state = alignment.get("state")
    _require(state in {"pending", "missing", "partial", "corrupt", "unalignable", "test_only", "aligned"}, "unsupported alignment state")
    return str(state)


@dataclass(frozen=True)
class AssemblyResult:
    """P2 outputs plus the exact P3 capture context derived from them."""

    assembly_input: dict[str, Any]
    ccm: dict[str, Any]
    cir: dict[str, Any]
    lineage_context: CaptureLineageContext


def assemble_ccm_cir(
    export: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    raw_artifact: Mapping[str, Any],
    counter: Mapping[str, Any],
    decoder: Mapping[str, Any],
    alignment: Mapping[str, Any],
) -> AssemblyResult:
    """Assemble P2 records; CCM remains configuration-only and CIR integrity-only."""

    assembly_input = make_capture_assembly_input(export, snapshot, raw_artifact, counter, decoder, alignment)
    _counter_decoder_consistent(counter, decoder)
    _require(decoder["header_capture_id"] in {None, export["capture_id"]}, "raw trace header capture_id mismatch")
    ccm = _p2_ccm(export, snapshot)
    try:
        validate_ccm(ccm)
    except ContractError as exc:
        raise P4AssemblyError(f"generated CCM is invalid: {exc}") from exc

    _, raw_state, raw_hash, _, _ = _raw_fields(raw_artifact)
    alignment_state = _alignment_state(alignment)
    reasons = list(decoder["reason_codes"])
    affected_channels: list[str] = []
    integrity_status = "complete"
    if raw_state != "available":
        integrity_status = "invalid"
        reasons.append(f"RAW_{raw_state.upper()}")
        affected_channels.append("raw_trace")
    if decoder["decoder_state"] == "invalid":
        integrity_status = "invalid"
    elif decoder["decoder_state"] == "degraded" and integrity_status != "invalid":
        integrity_status = "degraded"
    if counter["observation_state"] != "observed" and integrity_status == "complete":
        integrity_status = "degraded"
        reasons.append("COUNTERS_NOT_OBSERVED")
    if alignment_state not in {"aligned", "test_only"}:
        if integrity_status == "complete":
            integrity_status = "degraded"
        reasons.append(f"ALIGNMENT_{alignment_state.upper()}")
        affected_channels.append("observer")
    if bool(export["sampling_configuration"].get("enabled")) and integrity_status == "complete":
        integrity_status = "degraded"
        reasons.append("SAMPLING_ENABLED")
    degradation = any(
        (
            decoder["loss"],
            decoder["overflow"],
            decoder["truncation"],
            decoder["corruption"],
            decoder["mapping_mismatch"],
        )
    )
    if degradation and integrity_status == "complete":
        integrity_status = "degraded"
    overflow_count = int(decoder["overflow_count"])
    if counter["observation_state"] == "observed":
        overflow_count = max(overflow_count, int(counter["overflow_total"]))
    cir = finalize_p2_record(
        {
            "schema_version": SCHEMA_VERSION,
            "cir_record_id": f"cir:{export['capture_id'].split(':', 1)[1]}",
            "capture_id": export["capture_id"],
            "session_id": export["session_id"],
            "capability_manifest_id": ccm["capability_manifest_id"],
            "capability_manifest_digest": ccm["record_digest"],
            "raw_trace_id": decoder["raw_trace_id"],
            "raw_trace_hash": raw_hash,
            "firmware_hash": export["firmware_hash"],
            "ELF_hash": export["elf_hash"],
            "config_hash": export["p2_config_hash"],
            "source_commit": export["source_commit"],
            "immutable_input_refs": {
                "p4_config_export": {"ref": export["collector_config_export_id"], "digest": export["record_digest"], "schema_version": P4_SCHEMA_VERSION},
                "p4_assembly_input": {"ref": assembly_input["assembly_input_id"], "digest": assembly_input["record_digest"], "schema_version": P4_SCHEMA_VERSION},
                "p4_counter_report": {"ref": counter["counter_report_id"], "digest": counter["record_digest"], "schema_version": P4_SCHEMA_VERSION},
                "p4_decoder_report": {"ref": decoder["decoder_report_id"], "digest": decoder["record_digest"], "schema_version": P4_SCHEMA_VERSION},
                "p4_alignment_report": {"ref": alignment["alignment_report_id"], "digest": alignment["record_digest"], "schema_version": P4_SCHEMA_VERSION},
            },
            "sequence_continuity": decoder["sequence_continuity"],
            "sequence_gaps": deepcopy(decoder["sequence_gaps"]),
            "loss": bool(decoder["loss"]),
            "overflow": bool(decoder["overflow"]) or overflow_count > 0,
            "overflow_count": overflow_count,
            "buffer_high_watermark": counter["buffer_high_watermark"] if counter["observation_state"] == "observed" else None,
            "truncation": bool(decoder["truncation"]) or raw_state == "partial",
            "corruption": bool(decoder["corruption"]) or raw_state == "corrupt",
            "alignment_degradation": alignment_state not in {"aligned", "test_only"},
            "mapping_mismatch": bool(decoder["mapping_mismatch"]),
            "observer_loss": alignment_state in {"missing", "partial", "corrupt", "unalignable"},
            "affected_intervals": deepcopy(decoder["affected_intervals"]),
            "affected_channels": sorted(set(affected_channels)),
            "affected_entities": [],
            "natural_overflow": bool(counter["natural_overflow"]) if counter["observation_state"] == "observed" else False,
            "integrity_status": integrity_status,
            "reason_codes": sorted(set(reasons)),
            "prior_cir_ref": None,
        }
    )
    try:
        validate_cir(cir)
        context = CaptureLineageContext.from_p2_records(ccm, cir)
    except (ContractError, LineageValidationError) as exc:
        raise P4AssemblyError(f"generated P2/P3 binding is invalid: {exc}") from exc
    return AssemblyResult(assembly_input=assembly_input, ccm=ccm, cir=cir, lineage_context=context)


def validate_capture_join(
    result: AssemblyResult,
    *,
    export: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    raw_artifact: Mapping[str, Any],
    counter: Mapping[str, Any],
    decoder: Mapping[str, Any],
    alignment: Mapping[str, Any],
) -> None:
    """Fail closed on any P4/P2/P3 identity, hash, or schema crossing."""

    validate_capture_assembly_input(result.assembly_input, export=export, snapshot=snapshot, raw_artifact=raw_artifact, counter=counter, decoder=decoder, alignment=alignment)
    try:
        validate_ccm(result.ccm)
        validate_cir(result.cir)
    except ContractError as exc:
        raise P4AssemblyError(f"P2 join record invalid: {exc}") from exc
    _require(result.ccm["capture_id"] == result.cir["capture_id"] == export["capture_id"], "CCM/CIR/export capture_id mismatch")
    _require(result.ccm["session_id"] == result.cir["session_id"] == export["session_id"], "CCM/CIR/export session_id mismatch")
    _require(result.ccm["config_snapshot_id"] == snapshot["config_snapshot_id"], "CCM snapshot reference mismatch")
    _require(result.ccm["config_snapshot_digest"] == snapshot["record_digest"], "CCM snapshot digest mismatch")
    _require(result.ccm["collector_config_hash"] == export["p2_config_hash"], "CCM config hash mismatch")
    _require(result.cir["raw_trace_id"] == decoder["raw_trace_id"], "CIR raw trace identity mismatch")
    _require(result.cir["raw_trace_hash"] == decoder["raw_trace_hash"], "CIR raw trace hash mismatch")
    _require(result.cir["firmware_hash"] == export["firmware_hash"], "CIR firmware hash mismatch")
    _require(result.cir["ELF_hash"] == export["elf_hash"], "CIR ELF hash mismatch")
    _require(result.cir["config_hash"] == export["p2_config_hash"], "CIR config hash mismatch")
    _require(result.cir["source_commit"] == export["source_commit"], "CIR source commit mismatch")
    try:
        expected_context = CaptureLineageContext.from_p2_records(result.ccm, result.cir)
    except LineageValidationError as exc:
        raise P4AssemblyError(f"CCM/CIR lineage binding mismatch: {exc}") from exc
    _require(result.lineage_context.to_dict() == expected_context.to_dict(), "serialized CaptureLineageContext mismatch")
    _require(decoder["header_capture_id"] in {None, export["capture_id"]}, "raw header capture identity mismatch")

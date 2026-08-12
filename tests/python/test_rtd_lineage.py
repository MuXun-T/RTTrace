from __future__ import annotations

from copy import deepcopy

import pytest

from parser.rtd_lineage import (
    ALIGNMENT_DEGRADED_REASON,
    CAPABILITY_UNSUPPORTED_REASON,
    CIRWindowBinding,
    CaptureLineageContext,
    EvidenceLineage,
    LineageRegistry,
    LineageStatus,
    LineageValidationError,
    derive_lineage_status,
    legacy_trusted_projection,
)
from spec.rtd_pilot_contracts import SCHEMA_VERSION, finalize_record


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _p2_records(*, capture_id: str = "capture:lineage") -> tuple[dict, dict]:
    ccm = finalize_record(
        {
            "schema_version": SCHEMA_VERSION,
            "capability_manifest_id": "ccm:lineage",
            "capture_id": capture_id,
            "session_id": "session:lineage",
            "config_snapshot_id": "snapshot:lineage",
            "config_snapshot_digest": HASH_A,
            "collector_config_hash": HASH_B,
            "event_types_enabled": ["TASK_READY", "CTX_SWITCH"],
            "task_filter": [],
            "resource_filter": [],
            "irq_filter": [],
            "core_filter": ["0"],
            "sampling_configuration": {"enabled": False},
            "buffer_capacity": 64,
            "flush_policy": "drop_new",
            "timestamp_source": "counter",
            "clock_resolution": 1,
            "payload_fields": ["task_id"],
            "dictionary_version": "v1",
            "mapping_version": "v1",
            "collector_version": "v1",
            "trace_start_boundary": "capture_start",
            "trace_end_boundary": "capture_end",
            "prior_capability_ref": None,
        }
    )
    cir = finalize_record(
        {
            "schema_version": SCHEMA_VERSION,
            "cir_record_id": "cir:lineage",
            "capture_id": capture_id,
            "session_id": "session:lineage",
            "capability_manifest_id": ccm["capability_manifest_id"],
            "capability_manifest_digest": ccm["record_digest"],
            "raw_trace_id": "artifact:raw",
            "raw_trace_hash": HASH_C,
            "firmware_hash": HASH_A,
            "ELF_hash": HASH_B,
            "config_hash": HASH_C,
            "source_commit": "unit",
            "immutable_input_refs": {},
            "sequence_continuity": "continuous",
            "sequence_gaps": [],
            "loss": False,
            "overflow": False,
            "overflow_count": 0,
            "buffer_high_watermark": 0,
            "truncation": False,
            "corruption": False,
            "alignment_degradation": False,
            "mapping_mismatch": False,
            "observer_loss": False,
            "affected_intervals": [],
            "affected_channels": [],
            "affected_entities": [],
            "natural_overflow": False,
            "integrity_status": "complete",
            "reason_codes": [],
            "prior_cir_ref": None,
        }
    )
    return ccm, cir


def _context() -> CaptureLineageContext:
    return CaptureLineageContext.from_p2_records(*_p2_records())


def _lineage(
    context: CaptureLineageContext | None,
    *,
    lineage_id: str = "lineage:segment:one",
    source_event_ids: tuple[str, ...] = ("evt:open", "evt:close"),
    open_event_id: str | None = "evt:open",
    close_event_id: str | None = "evt:close",
    boundary_event_ids: tuple[str, ...] = ("evt:open", "evt:close"),
    interval_start: float | None = 10.0,
    interval_end: float | None = 30.0,
    windows: tuple[str, ...] = (),
    boundary_windows: tuple[str, ...] = (),
    status: LineageStatus = LineageStatus.COMPLETE,
    reason_codes: tuple[str, ...] = (),
) -> EvidenceLineage:
    return EvidenceLineage(
        lineage_id=lineage_id,
        derived_object_id="seg:one",
        derived_object_type="TaskStateSeg",
        capture_context=context,
        source_event_ids=source_event_ids,
        open_event_id=open_event_id,
        close_event_id=close_event_id,
        boundary_event_ids=boundary_event_ids,
        derivation_rule_id="rtd.task-state.v1",
        derivation_version="1.0",
        interval_start=interval_start,
        interval_end=interval_end,
        observed_horizon_start=0.0,
        observed_horizon_end=40.0,
        all_intersecting_untrusted_window_ids=windows,
        boundary_intersecting_untrusted_window_ids=boundary_windows,
        lineage_status=status,
        reason_codes=reason_codes,
    )


def _window(context: CaptureLineageContext, window_id: str, start: float, end: float, reason: str = "SEQ_GAP") -> CIRWindowBinding:
    return CIRWindowBinding(
        window_id=window_id,
        interval_start=start,
        interval_end=end,
        reason_code=reason,
        capture_integrity_record_ref=context.capture_integrity_record_ref,
        capture_integrity_record_digest=context.capture_integrity_record_digest,
        capture_integrity_record_schema_version=context.capture_integrity_record_schema_version,
        capture_id=context.capture_id,
    )


def test_capture_context_validates_p2_records_and_rejects_cross_capture_binding() -> None:
    ccm, cir = _p2_records()
    context = CaptureLineageContext.from_p2_records(ccm, cir)

    assert context.capture_id == "capture:lineage"
    assert context.capture_capability_manifest_ref == "ccm:lineage"
    assert context.capture_integrity_record_ref == "cir:lineage"

    mismatched_cir = deepcopy(cir)
    mismatched_cir["capture_id"] = "capture:other"
    mismatched_cir = finalize_record(mismatched_cir)
    with pytest.raises(LineageValidationError, match="capture_id mismatch"):
        CaptureLineageContext.from_p2_records(ccm, mismatched_cir)


def test_registry_rejects_fabricated_source_ids_and_backtraces_registered_events() -> None:
    context = _context()
    registry = LineageRegistry(context)
    registry.register_raw_event("evt:open", 10.0)
    registry.register_raw_event("evt:close", 30.0)

    lineage = _lineage(context)
    registry.register_lineage(lineage)

    backtrace = registry.backtrace("lineage:segment:one")
    assert backtrace.source_event_ids == ("evt:open", "evt:close")
    assert backtrace.open_event_id == "evt:open"
    assert backtrace.close_event_id == "evt:close"

    fabricated = _lineage(
        context,
        lineage_id="lineage:segment:fake",
        source_event_ids=("evt:open", "evt:made-up"),
        close_event_id="evt:made-up",
        boundary_event_ids=("evt:open", "evt:made-up"),
    )
    with pytest.raises(LineageValidationError, match="unregistered raw event evt:made-up"):
        registry.register_lineage(fabricated)


def test_registry_preserves_reused_raw_ids_without_synthetic_identifiers() -> None:
    registry = LineageRegistry(_context())
    registry.register_raw_event("evt:repeated", 10.0)
    registry.register_raw_event("evt:repeated", 10.0)

    assert registry.raw_events["evt:repeated"].timestamp == 10.0
    registry.register_raw_event("evt:repeated", 11.0)
    assert registry.raw_events["evt:repeated"].timestamps == (10.0, 11.0)


def test_registry_requires_every_window_intersection_and_tracks_boundary_intersections() -> None:
    context = _context()
    registry = LineageRegistry(context)
    registry.register_raw_event("evt:open", 10.0)
    registry.register_raw_event("evt:close", 30.0)
    registry.register_cir_window(_window(context, "uw:first", 10.0, 12.0))
    registry.register_cir_window(_window(context, "uw:second", 24.0, 30.0))

    lineage = _lineage(
        context,
        windows=("uw:first", "uw:second"),
        boundary_windows=("uw:first", "uw:second"),
        status=LineageStatus.LOSS_AFFECTED,
    )
    registry.register_lineage(lineage)

    backtrace = registry.backtrace(lineage.lineage_id)
    assert backtrace.all_intersecting_untrusted_window_ids == ("uw:first", "uw:second")

    missing_one = _lineage(
        context,
        lineage_id="lineage:segment:missing-window",
        windows=("uw:first",),
        boundary_windows=("uw:first",),
        status=LineageStatus.LOSS_AFFECTED,
    )
    with pytest.raises(LineageValidationError, match="does not retain every objective untrusted-window intersection"):
        registry.register_lineage(missing_one)


def test_missing_close_uses_observed_horizon_without_normalizing_a_complete_interval() -> None:
    context = _context()
    registry = LineageRegistry(context)
    registry.register_raw_event("evt:open", 10.0)

    incomplete = _lineage(
        context,
        lineage_id="lineage:segment:truncated",
        source_event_ids=("evt:open",),
        open_event_id="evt:open",
        close_event_id=None,
        boundary_event_ids=("evt:open",),
        interval_start=10.0,
        interval_end=None,
        status=LineageStatus.INCOMPLETE_CLOSE,
    )
    registry.register_lineage(incomplete)
    assert not legacy_trusted_projection(incomplete)

    invalid_complete = _lineage(
        context,
        lineage_id="lineage:segment:invalid-complete",
        source_event_ids=("evt:open",),
        open_event_id="evt:open",
        close_event_id=None,
        boundary_event_ids=("evt:open",),
        interval_start=10.0,
        interval_end=40.0,
        status=LineageStatus.COMPLETE,
    )
    with pytest.raises(LineageValidationError, match="interval_end requires an authoritative close_event_id"):
        registry.register_lineage(invalid_complete)


def test_legacy_context_absence_is_explicit_and_never_complete() -> None:
    registry = LineageRegistry(None)
    registry.register_raw_event("evt:open", 10.0)
    registry.register_raw_event("evt:close", 30.0)
    lineage = _lineage(
        None,
        lineage_id="lineage:segment:legacy",
        status=LineageStatus.CAPABILITY_UNSUPPORTED,
        reason_codes=(
            CAPABILITY_UNSUPPORTED_REASON,
            "MISSING_CAPTURE_LINEAGE_CONTEXT",
            "MISSING_CCM_REF",
            "MISSING_CIR_REF",
        ),
    )
    registry.register_lineage(lineage)
    registry.validate()
    assert lineage.capture_capability_manifest_ref is None
    assert lineage.capture_integrity_record_ref is None


def test_objective_status_derivation_keeps_capability_mapping_and_alignment_distinct() -> None:
    context = _context()
    mapping_window = _window(context, "uw:mapping", 10.0, 20.0, "DICT_MISMATCH")
    alignment_window = _window(context, "uw:align", 10.0, 20.0, "ALIGN_DEGRADED")

    assert derive_lineage_status(
        open_event_id="evt:open",
        close_event_id="evt:close",
        reason_codes=(CAPABILITY_UNSUPPORTED_REASON,),
    ) is LineageStatus.CAPABILITY_UNSUPPORTED
    assert derive_lineage_status(
        open_event_id="evt:open",
        close_event_id="evt:close",
        intersecting_windows=(mapping_window,),
    ) is LineageStatus.MAPPING_INVALID
    assert derive_lineage_status(
        open_event_id="evt:open",
        close_event_id="evt:close",
        reason_codes=(ALIGNMENT_DEGRADED_REASON,),
        intersecting_windows=(alignment_window,),
    ) is LineageStatus.ALIGNMENT_DEGRADED

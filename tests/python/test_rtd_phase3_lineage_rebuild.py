from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

from desktop.services import _bundle_from_dict
from parser.models import DecodedEvent, ResourceEdge, UntrustedWindow
from parser.rebuild import rb_Rebuild
from parser.rtd_lineage import (
    CAPABILITY_UNSUPPORTED_REASON,
    MAPPING_INVALID_REASON,
    NON_NORMAL_CLOSE_BOUNDARY_REASON,
    CaptureLineageContext,
    LineageStatus,
)
from spec.events import event_id_for
from spec.io import serialize
from spec.rtd_pilot_contracts import SCHEMA_VERSION, finalize_record


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
EVENT_TYPES = (
    "CTX_SWITCH",
    "IRQ_ENTER",
    "IRQ_EXIT",
    "SYNC_LOCK",
    "SYNC_UNLOCK",
    "TASK_BLOCK",
    "TASK_DISPATCH",
    "TASK_EXIT",
    "TASK_READY",
    "TASK_WAKEUP",
)


def _context(*, event_types: tuple[str, ...] = EVENT_TYPES) -> CaptureLineageContext:
    ccm = finalize_record(
        {
            "schema_version": SCHEMA_VERSION,
            "capability_manifest_id": "ccm:phase3",
            "capture_id": "capture:phase3",
            "session_id": "session:phase3",
            "config_snapshot_id": "snapshot:phase3",
            "config_snapshot_digest": HASH_A,
            "collector_config_hash": HASH_B,
            "event_types_enabled": list(event_types),
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
            "cir_record_id": "cir:phase3",
            "capture_id": "capture:phase3",
            "session_id": "session:phase3",
            "capability_manifest_id": ccm["capability_manifest_id"],
            "capability_manifest_digest": ccm["record_digest"],
            "raw_trace_id": "artifact:phase3",
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
    return CaptureLineageContext.from_p2_records(ccm, cir)


def _event(seq: int, timestamp: float, event_name: str, payload: dict[str, int], *, core_id: int = 0) -> DecodedEvent:
    return DecodedEvent(
        core_id=core_id,
        seq=seq,
        timestamp_raw=timestamp,
        timestamp_aligned=timestamp,
        event_id=event_id_for(event_name),
        event_name=event_name,
        task_id=payload.get("task_id"),
        obj_id=payload.get("obj_id", payload.get("wait_obj_id")),
        irq_id=payload.get("irq_id"),
        job_id=None,
        instance_id=None,
        payload=payload,
        trust_tags=[],
        chunk_id=0,
    )


def _window(window_id: str, start: float, end: float, reason: str = "SEQ_GAP") -> UntrustedWindow:
    return UntrustedWindow(
        window_id=window_id,
        source="unit",
        scope="rebuild",
        t_begin=start,
        t_end=end,
        reason_code=reason,
        severity="warning",
    )


def _rebuild(
    events: list[DecodedEvent],
    *,
    context: CaptureLineageContext | None = None,
    windows: list[UntrustedWindow] | None = None,
):
    result = rb_Rebuild(
        events,
        dataset_id="phase3",
        lineage_context=_context() if context is None else context,
        untrusted_windows=windows,
    )
    assert result.ok, result.message
    return result.data


def _record_for(bundle, item):
    return bundle.lineage_registry.records[item.lineage_id]


def test_lost_ready_does_not_fabricate_a_ready_interval() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 0, "next_task_id": 1}),
            _event(2, 20.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 1, "next_task_id": 0}),
        ],
        windows=[_window("uw:lost-ready", 10.0, 20.0)],
    )
    for segment in [segment for segment in bundle.task_states if segment.state == "READY"]:
        lineage = _record_for(bundle, segment)
        assert lineage.open_event_id == "evt:phase3:0:2"
        assert lineage.close_event_id is None
        assert lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE
    for interval in bundle.ready_not_running_intervals:
        assert _record_for(bundle, interval).lineage_status is not LineageStatus.COMPLETE
    slice_lineage = _record_for(bundle, bundle.exec_slices[0])
    assert slice_lineage.source_event_ids == ("evt:phase3:0:1", "evt:phase3:0:2")
    assert slice_lineage.lineage_status is LineageStatus.LOSS_AFFECTED


def test_lost_dispatch_and_trace_truncation_keep_authoritative_close_null() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_READY", {"task_id": 1}),
            _event(2, 20.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 0, "next_task_id": 1}),
            _event(3, 30.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 1, "next_task_id": 0}),
        ],
        windows=[_window("uw:lost-dispatch", 15.0, 25.0), _window("uw:truncation", 30.0, 30.0, "TRUNCATION")],
    )
    tail = max(
        (segment for segment in bundle.task_states if segment.task_id == 1 and segment.state == "READY"),
        key=lambda item: item.t_begin,
    )
    lineage = _record_for(bundle, tail)
    assert lineage.open_event_id == "evt:phase3:0:3"
    assert lineage.close_event_id is None
    assert lineage.interval_end is None
    assert tail.t_end == 30.0
    assert lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE
    slice_lineage = _record_for(bundle, bundle.exec_slices[0])
    assert slice_lineage.source_event_ids == ("evt:phase3:0:2", "evt:phase3:0:3")
    assert slice_lineage.lineage_status is LineageStatus.LOSS_AFFECTED
    assert "evt:phase3:0:missing-dispatch" not in slice_lineage.source_event_ids


def test_lost_block_creates_no_wait_open_relation() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_READY", {"task_id": 1}),
            _event(2, 20.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 0, "next_task_id": 1}),
            _event(3, 30.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 1, "next_task_id": 0}),
        ],
        windows=[_window("uw:lost-block", 15.0, 25.0)],
    )
    assert not bundle.resource_graph.wait_edges


def test_lost_wakeup_and_lost_unlock_remain_incomplete_close() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_BLOCK", {"task_id": 2, "wait_obj_id": 9, "owner_task_id": 1}),
            _event(2, 20.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 9, "obj_type": 1}),
            _event(3, 30.0, "TASK_READY", {"task_id": 3}),
        ]
    )
    for edge in [*bundle.resource_graph.wait_edges, *bundle.resource_graph.hold_edges]:
        lineage = _record_for(bundle, edge)
        assert lineage.close_event_id is None
        assert lineage.interval_end is None
        assert lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE


def test_lost_lock_and_lost_irq_enter_keep_authoritative_open_null() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 9}),
            _event(2, 20.0, "IRQ_EXIT", {"irq_id": 4, "nesting_depth": 1}),
        ]
    )
    hold_lineage = _record_for(bundle, bundle.resource_graph.hold_edges[0])
    irq_lineage = _record_for(bundle, bundle.irq_spans[0])
    assert hold_lineage.open_event_id is None
    assert hold_lineage.interval_start is None
    assert hold_lineage.lineage_status is LineageStatus.INCOMPLETE_OPEN
    assert bundle.irq_spans[0].enter_event_id is None
    assert irq_lineage.open_event_id is None
    assert irq_lineage.close_event_id == "evt:phase3:0:2"
    assert irq_lineage.lineage_status is LineageStatus.INCOMPLETE_OPEN


def test_hidden_unlock_lock_gap_keeps_successor_open_as_a_non_normal_close() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 9, "obj_type": 1}),
            _event(2, 30.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 9, "obj_type": 1}),
            _event(3, 40.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 9}),
        ],
        windows=[_window("uw:hidden-unlock-lock", 15.0, 25.0)],
    )
    first, second = bundle.resource_graph.hold_edges
    lineage = _record_for(bundle, first)
    assert lineage.source_event_ids == ("evt:phase3:0:1", "evt:phase3:0:2")
    assert lineage.boundary_event_ids == lineage.source_event_ids
    assert "uw:hidden-unlock-lock" in lineage.all_intersecting_untrusted_window_ids
    assert any("lock_without_unlock" in window_id for window_id in lineage.all_intersecting_untrusted_window_ids)
    assert lineage.close_event_id == "evt:phase3:0:2"
    assert lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE
    assert NON_NORMAL_CLOSE_BOUNDARY_REASON in lineage.reason_codes
    assert _record_for(bundle, second).lineage_status is LineageStatus.LOSS_AFFECTED


def test_lost_irq_exit_preserves_enter_and_nesting_without_close() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "IRQ_ENTER", {"irq_id": 4, "nesting_depth": 1}),
            _event(2, 20.0, "TASK_READY", {"task_id": 1}),
        ]
    )
    span = bundle.irq_spans[0]
    lineage = _record_for(bundle, span)
    assert span.enter_event_id == "evt:phase3:0:1"
    assert span.exit_event_id is None
    assert span.nesting_depth == 1
    assert lineage.close_event_id is None
    assert lineage.interval_end is None
    assert lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE


def test_disabled_filter_sampling_and_mapping_are_explicit_non_lineage_truth() -> None:
    events = [
        _event(1, 10.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 9, "obj_type": 1}),
        _event(2, 20.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 9}),
    ]
    filtered = _rebuild(events, context=_context(event_types=("TASK_READY",)))
    filtered_lineage = _record_for(filtered, filtered.resource_graph.hold_edges[0])
    assert filtered_lineage.lineage_status is LineageStatus.CAPABILITY_UNSUPPORTED
    assert CAPABILITY_UNSUPPORTED_REASON in filtered_lineage.reason_codes
    assert filtered.resource_graph.hold_edges[0].trusted is True

    sampled = _rebuild(events, context=replace(_context(), sampling_boundary_complete=False))
    assert _record_for(sampled, sampled.resource_graph.hold_edges[0]).lineage_status is LineageStatus.CAPABILITY_UNSUPPORTED

    unmapped = _rebuild(events, context=replace(_context(), mapping_valid=False))
    unmapped_lineage = _record_for(unmapped, unmapped.resource_graph.hold_edges[0])
    assert unmapped_lineage.lineage_status is LineageStatus.MAPPING_INVALID
    assert MAPPING_INVALID_REASON in unmapped_lineage.reason_codes


def test_task_exec_and_ready_not_running_lineage_have_real_boundaries() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_READY", {"task_id": 1}),
            _event(2, 20.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 0, "next_task_id": 1}),
            _event(3, 30.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 1, "next_task_id": 0}),
        ]
    )
    ready_segment = next(segment for segment in bundle.task_states if segment.state == "READY" and segment.t_begin == 10.0)
    ready_lineage = _record_for(bundle, ready_segment)
    ready_not_running = next(item for item in bundle.ready_not_running_intervals if item.task_id == 1 and item.t_begin == 10.0)
    interval_lineage = _record_for(bundle, ready_not_running)
    slice_lineage = _record_for(bundle, bundle.exec_slices[0])
    assert ready_lineage.open_event_id == "evt:phase3:0:1"
    assert ready_lineage.close_event_id == "evt:phase3:0:2"
    assert interval_lineage.source_event_ids == ready_lineage.source_event_ids
    assert slice_lineage.open_event_id == "evt:phase3:0:2"
    assert slice_lineage.close_event_id == "evt:phase3:0:3"


def test_dispatch_and_competing_execution_boundaries_are_direct_ready_lineage_sources() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_READY", {"task_id": 1}),
            _event(2, 15.0, "TASK_DISPATCH", {"task_id": 2, "core_id": 0}),
            _event(3, 20.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 0, "next_task_id": 2}),
            _event(4, 25.0, "IRQ_ENTER", {"irq_id": 4, "nesting_depth": 1}),
            _event(5, 27.0, "IRQ_EXIT", {"irq_id": 4, "nesting_depth": 1}),
            _event(6, 30.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 2, "next_task_id": 0}),
        ]
    )
    ready_interval = next(item for item in bundle.ready_not_running_intervals if item.task_id == 1)
    ready_lineage = _record_for(bundle, ready_interval)
    competing_slice = bundle.exec_slices[0]
    competing_slice_lineage = _record_for(bundle, competing_slice)

    assert competing_slice_lineage.source_event_ids == (
        "evt:phase3:0:2",
        "evt:phase3:0:3",
        "evt:phase3:0:6",
    )
    assert set(ready_lineage.source_event_ids) == {
        "evt:phase3:0:1",
        "evt:phase3:0:2",
        "evt:phase3:0:3",
        "evt:phase3:0:4",
        "evt:phase3:0:5",
        "evt:phase3:0:6",
    }
    assert ready_interval.competing_exec_slice_ids == [competing_slice.slice_id]
    assert ready_interval.competing_irq_span_ids == [bundle.irq_spans[0].irq_span_id]


def test_reused_raw_identity_is_explicitly_mapping_invalid_without_new_source_ids() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_READY", {"task_id": 1}),
            _event(1, 20.0, "TASK_BLOCK", {"task_id": 1, "wait_obj_id": 9, "owner_task_id": 2}),
        ]
    )
    segment = next(item for item in bundle.task_states if item.state == "READY")
    lineage = _record_for(bundle, segment)

    assert bundle.lineage_registry.raw_events["evt:phase3:0:1"].timestamps == (10.0, 20.0)
    assert lineage.source_event_ids == ("evt:phase3:0:1",)
    assert lineage.lineage_status is LineageStatus.MAPPING_INVALID
    assert MAPPING_INVALID_REASON in lineage.reason_codes


def test_irq_nesting_and_real_non_normal_resource_close_boundaries() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "IRQ_ENTER", {"irq_id": 1, "nesting_depth": 1}),
            _event(2, 12.0, "IRQ_ENTER", {"irq_id": 2, "nesting_depth": 2}),
            _event(3, 14.0, "IRQ_EXIT", {"irq_id": 2, "nesting_depth": 2}),
            _event(4, 16.0, "IRQ_EXIT", {"irq_id": 1, "nesting_depth": 1}),
            _event(5, 20.0, "SYNC_LOCK", {"task_id": 3, "obj_id": 9, "obj_type": 1}),
            _event(6, 21.0, "TASK_BLOCK", {"task_id": 3, "wait_obj_id": 9, "owner_task_id": 3}),
            _event(7, 30.0, "TASK_EXIT", {"task_id": 3}),
        ]
    )
    assert [(item.irq_id, item.nesting_depth) for item in bundle.irq_spans] == [(1, 1), (2, 2)]
    for edge in [*bundle.resource_graph.hold_edges, *bundle.resource_graph.wait_edges]:
        lineage = _record_for(bundle, edge)
        assert lineage.close_event_id == "evt:phase3:0:7"
        assert lineage.interval_end == 30.0
        assert lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE
        assert NON_NORMAL_CLOSE_BOUNDARY_REASON in lineage.reason_codes


def test_irq_exit_without_a_matching_top_of_stack_never_fabricates_a_complete_span() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "IRQ_ENTER", {"irq_id": 1, "nesting_depth": 1}),
            _event(2, 20.0, "IRQ_EXIT", {"irq_id": 2, "nesting_depth": 2}),
        ]
    )

    unmatched_exit = next(item for item in bundle.irq_spans if item.exit_event_id is not None)
    pending_enter = next(item for item in bundle.irq_spans if item.enter_event_id is not None)
    exit_lineage = _record_for(bundle, unmatched_exit)
    enter_lineage = _record_for(bundle, pending_enter)

    assert unmatched_exit.enter_event_id is None
    assert exit_lineage.open_event_id is None
    assert exit_lineage.close_event_id == "evt:phase3:0:2"
    assert exit_lineage.lineage_status is LineageStatus.INCOMPLETE_OPEN
    assert pending_enter.exit_event_id is None
    assert enter_lineage.open_event_id == "evt:phase3:0:1"
    assert enter_lineage.close_event_id is None
    assert enter_lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE


def test_multi_window_backtrace_ccm_cir_and_derivation_bindings_are_complete() -> None:
    context = _context()
    bundle = _rebuild(
        [
            _event(1, 10.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 9, "obj_type": 1}),
            _event(2, 50.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 9}),
        ],
        context=context,
        windows=[_window("uw:first", 15.0, 20.0), _window("uw:second", 30.0, 40.0)],
    )
    edge = bundle.resource_graph.hold_edges[0]
    lineage = _record_for(bundle, edge)
    assert lineage.all_intersecting_untrusted_window_ids == ("uw:first", "uw:second")
    assert lineage.capture_capability_manifest_ref == context.capture_capability_manifest_ref
    assert lineage.capture_integrity_record_ref == context.capture_integrity_record_ref
    assert lineage.derivation_rule_id == "rtd.rebuild.resource_hold"
    assert lineage.derivation_version == "rtd-lineage-v1.0"
    assert all(
        binding.capture_integrity_record_ref == context.capture_integrity_record_ref
        for binding in bundle.lineage_registry.window_integrity_bindings.values()
    )
    backtrace = bundle.lineage_registry.backtrace(edge.lineage_id)
    assert backtrace.source_event_ids == ("evt:phase3:0:1", "evt:phase3:0:2")
    assert set(backtrace.source_event_ids).issubset(bundle.lineage_registry.raw_events)


def test_registry_and_typed_edges_round_trip_without_fabricated_source_ids() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_BLOCK", {"task_id": 2, "wait_obj_id": 9, "owner_task_id": 1}),
            _event(2, 20.0, "TASK_WAKEUP", {"task_id": 2, "obj_id": 9}),
            _event(3, 30.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 9, "obj_type": 1}),
            _event(4, 40.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 9}),
        ]
    )
    payload = serialize(bundle)
    json.dumps(payload)
    restored = _bundle_from_dict(payload)
    assert all(isinstance(edge, ResourceEdge) for edge in restored.resource_graph.wait_edges + restored.resource_graph.hold_edges)
    assert restored.lineage_registry.to_dict() == bundle.lineage_registry.to_dict()
    for lineage in restored.lineage_registry.records.values():
        assert set(lineage.source_event_ids).issubset(restored.lineage_registry.raw_events)
        assert not any(event_id.startswith("trace:") or event_id.startswith("uw:") for event_id in lineage.source_event_ids)


def test_bundle_reader_rejects_swapped_or_unbound_object_lineage() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "TASK_READY", {"task_id": 1}),
            _event(2, 20.0, "CTX_SWITCH", {"core_id": 0, "prev_task_id": 0, "next_task_id": 1}),
        ]
    )
    payload = serialize(bundle)
    payload["exec_slices"][0]["lineage_id"] = payload["task_states"][0]["lineage_id"]

    try:
        _bundle_from_dict(payload)
    except ValueError as exc:
        assert "does not bind its derived object" in str(exc)
    else:
        raise AssertionError("swapped lineage binding was accepted")

    fabricated = deepcopy(serialize(bundle))
    fabricated_record = next(
        item for item in fabricated["lineage_registry"]["records"] if item["derived_object_type"] == "exec_slice"
    )
    fabricated_record["source_event_ids"].append("evt:made-up")
    fabricated_record["boundary_event_ids"].append("evt:made-up")
    fabricated["lineage_registry"]["raw_events"].append(
        {"event_id": "evt:made-up", "timestamp": 10.0, "observed_timestamps": [10.0]}
    )
    try:
        _bundle_from_dict(fabricated)
    except ValueError as exc:
        assert "raw events do not match" in str(exc)
    else:
        raise AssertionError("fabricated raw source was accepted")


def test_legacy_bundle_reader_preserves_compatibility_without_assigning_lineage() -> None:
    bundle = _rebuild(
        [
            _event(1, 10.0, "SYNC_LOCK", {"task_id": 1, "obj_id": 9, "obj_type": 1}),
            _event(2, 20.0, "SYNC_UNLOCK", {"task_id": 1, "obj_id": 9}),
        ]
    )
    legacy = deepcopy(serialize(bundle))
    for field_name in (
        "capture_id",
        "capture_capability_manifest_ref",
        "capture_integrity_record_ref",
        "ready_not_running_intervals",
        "lineage_registry",
    ):
        legacy.pop(field_name, None)
    edge = legacy["resource_graph"]["hold_edges"][0]
    edge.pop("edge_id", None)
    edge.pop("lineage_id", None)
    restored = _bundle_from_dict(legacy)
    assert restored.lineage_registry is None
    assert isinstance(restored.resource_graph.hold_edges[0], ResourceEdge)
    assert restored.resource_graph.hold_edges[0].trusted is True

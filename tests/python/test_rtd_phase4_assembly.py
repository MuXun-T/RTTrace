from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from desktop.sample_data import build_scenario
from p4_capture.artifacts import CaptureStore
from p4_capture.assembler import (
    P4AssemblyError,
    assemble_ccm_cir,
    decode_integrity_report,
    make_counter_report,
    validate_capture_join,
)
from p4_capture.contracts import finalize_record, make_session_manifest, p2_snapshot_from_export
from p4_capture.observer import (
    make_alignment_report,
    make_observer_artifact_inventory,
    make_shared_epoch_record,
)
from p4_phase4_support import config_export
from parser import encode_trace, load_dataset
from parser.rtd_lineage import LineageStatus


def _events() -> list[dict[str, object]]:
    return [dict(item) for item in build_scenario(name="basic") if item["core_id"] == 0]


def _prepared_inputs(
    tmp_path: Path,
    *,
    capture_id: str = "capture:p4-integration",
    session_id: str = "session:p4-integration",
    header_capture_id: str | None = None,
    events: list[dict[str, object]] | None = None,
    mutate_raw=None,
    alignment_state: str = "test_only",
    counter_observation_state: str = "observed",
    counter_lost_total: int | None = 0,
    counter_overflow_total: int | None = 0,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object], dict[str, object], dict[str, object], Path]:
    export = config_export(capture_id=capture_id, session_id=session_id)
    manifest = make_session_manifest(export, prepared_at="2026-08-16T00:00:00Z")
    snapshot = p2_snapshot_from_export(export)
    store = CaptureStore(tmp_path / "p4-test-only-root")
    directory = store.prepare(manifest, export, snapshot)
    source = tmp_path / "synthetic-test-only.trace"
    encode_trace(source, events or _events(), producer_ver="p4-test-only", run_id=header_capture_id or capture_id)
    if mutate_raw is not None:
        mutate_raw(source)
    raw = store.import_raw(directory, source)
    imported_path = directory / raw["logical_name"]
    observer = make_observer_artifact_inventory(
        capture_id=capture_id,
        session_id=session_id,
        observer_artifact_hash="e" * 64 if alignment_state == "test_only" else None,
        state="test_only" if alignment_state == "test_only" else alignment_state,
        test_only=True,
        logical_name="observer/test-only.logic",
    )
    epoch = make_shared_epoch_record(
        capture_id=capture_id,
        session_id=session_id,
        epoch_id="epoch:test",
        trace_sequence=1 if alignment_state == "test_only" else None,
        trace_timestamp=1000 if alignment_state == "test_only" else None,
        observer_timestamp=0.0 if alignment_state == "test_only" else None,
        test_only=True,
    )
    alignment = make_alignment_report(
        observer,
        epoch,
        state=alignment_state,
        alignment_error_bound=0.0 if alignment_state == "test_only" else None,
        algorithm="alignment:test-only-v1",
        test_only=True,
    )
    decoder = decode_integrity_report(export, raw, imported_path)
    if counter_observation_state == "not_observed":
        counter = make_counter_report(
            export,
            raw,
            observation_state="not_observed",
            lost_total=None,
            overflow_total=None,
            io_backpressure_total=None,
            written_records=None,
            flushed_records=None,
            buffer_high_watermark=None,
            natural_overflow=None,
            counter_source="test_only:not-observed",
        )
    else:
        counter = make_counter_report(
            export,
            raw,
            observation_state="observed",
            lost_total=counter_lost_total,
            overflow_total=counter_overflow_total,
            io_backpressure_total=0,
            written_records=max(1, decoder["decoded_event_count"]),
            flushed_records=max(1, decoder["decoded_event_count"]),
            buffer_high_watermark=12,
            natural_overflow=False,
            counter_source="test_only:synthetic-counter",
        )
    return export, snapshot, raw, counter, decoder, alignment, imported_path


def test_synthetic_raw_codec_to_p3_lineage_has_a_complete_test_only_backtrace(tmp_path: Path) -> None:
    export, snapshot, raw, counter, decoder, alignment, trace_path = _prepared_inputs(tmp_path)
    result = assemble_ccm_cir(export, snapshot, raw, counter, decoder, alignment)

    validate_capture_join(
        result,
        export=export,
        snapshot=snapshot,
        raw_artifact=raw,
        counter=counter,
        decoder=decoder,
        alignment=alignment,
    )
    assert result.ccm["buffer_capacity"] == 64
    assert "buffer_capacity_unit" not in result.ccm
    assert "buffer_high_watermark" not in result.ccm
    assert "buffer_high_watermark_unit" not in result.ccm
    assert result.cir["buffer_high_watermark"] == 12
    assert result.assembly_input["buffer_capacity_unit"] == "records"
    assert result.assembly_input["buffer_high_watermark_unit"] == "records"
    assert result.cir["immutable_input_refs"]["p4_counter_report"]["ref"] == counter["counter_report_id"]
    assert result.cir["integrity_status"] == "complete"
    assert result.lineage_context.capture_id == export["capture_id"]

    loaded = load_dataset(trace_path, lineage_context=result.lineage_context)
    assert loaded.ok, loaded.message
    records = loaded.data.bundle.lineage_registry.records.values()
    complete = [record for record in records if record.lineage_status is LineageStatus.COMPLETE]
    assert complete
    backtrace = loaded.data.bundle.lineage_registry.backtrace(complete[0].lineage_id)
    assert backtrace.capture_context == result.lineage_context
    assert backtrace.source_event_ids


def test_cross_capture_stale_snapshot_wrong_header_and_counter_contradictions_fail_closed(tmp_path: Path) -> None:
    first = _prepared_inputs(tmp_path / "first", capture_id="capture:p4-first", session_id="session:p4-first")
    second = _prepared_inputs(tmp_path / "second", capture_id="capture:p4-second", session_id="session:p4-second")
    export, snapshot, raw, counter, decoder, alignment, _ = first

    with pytest.raises(P4AssemblyError, match="stale"):
        assemble_ccm_cir(export, second[1], raw, counter, decoder, alignment)

    header_crossed = _prepared_inputs(
        tmp_path / "header",
        capture_id="capture:p4-header-a",
        session_id="session:p4-header-a",
        header_capture_id="capture:p4-header-b",
    )
    with pytest.raises(P4AssemblyError, match="header"):
        assemble_ccm_cir(*header_crossed[:6])

    contradictory = make_counter_report(
        export,
        raw,
        observation_state="observed",
        lost_total=1,
        overflow_total=0,
        io_backpressure_total=0,
        written_records=decoder["decoded_event_count"],
        flushed_records=decoder["decoded_event_count"],
        buffer_high_watermark=12,
        natural_overflow=False,
        counter_source="test_only:contradiction",
    )
    with pytest.raises(P4AssemblyError, match="LOSS"):
        assemble_ccm_cir(export, snapshot, raw, contradictory, decoder, alignment)


@pytest.mark.parametrize(
    "name, event_list, mutate_raw, lost_total, overflow_total, expected_reason",
    [
        (
            "seq-gap",
            [
                {"core_id": 0, "event_id": 0x1001, "seq": 1, "timestamp": 10, "payload": {"task_id": 1, "prio": 1, "core_hint": 0, "reason": 1}},
                {"core_id": 0, "event_id": 0x1004, "seq": 3, "timestamp": 20, "payload": {"task_id": 1, "core_id": 0, "prio": 1, "reason": 1}},
            ],
            None,
            0,
            0,
            "SEQ_GAP",
        ),
        (
            "loss",
            [
                {"core_id": 0, "event_id": 0x1001, "seq": 1, "timestamp": 10, "payload": {"task_id": 1, "prio": 1, "core_hint": 0, "reason": 1}},
                {"core_id": 0, "event_id": 0x4001, "seq": 2, "timestamp": 20, "payload": {"core_id": 0, "lost_count": 2, "reason": 1}},
            ],
            None,
            2,
            0,
            "SEQ_GAP",
        ),
        (
            "overflow",
            [
                {"core_id": 0, "event_id": 0x1001, "seq": 1, "timestamp": 10, "payload": {"task_id": 1, "prio": 1, "core_hint": 0, "reason": 1}},
                {"core_id": 0, "event_id": 0x4002, "seq": 2, "timestamp": 20, "payload": {"core_id": 0, "overflow_count": 3, "reason": 2}},
            ],
            None,
            0,
            3,
            "BUFFER_OVERFLOW",
        ),
        ("truncation", None, lambda path: path.write_bytes(path.read_bytes()[:-5]), 0, 0, "TRUNCATED_CHUNK"),
        (
            "crc",
            None,
            lambda path: path.write_bytes(path.read_bytes()[:-1] + bytes([path.read_bytes()[-1] ^ 0x01])),
            0,
            0,
            "CRC_FAIL",
        ),
    ],
)
def test_synthetic_integrity_conditions_propagate_to_degraded_cir(
    tmp_path: Path,
    name: str,
    event_list: list[dict[str, object]] | None,
    mutate_raw,
    lost_total: int,
    overflow_total: int,
    expected_reason: str,
) -> None:
    values = _prepared_inputs(
        tmp_path / name,
        capture_id=f"capture:p4-{name}",
        session_id=f"session:p4-{name}",
        events=event_list,
        mutate_raw=mutate_raw,
        counter_lost_total=lost_total,
        counter_overflow_total=overflow_total,
    )
    result = assemble_ccm_cir(*values[:6])

    assert values[4]["test_only"] is True
    assert result.cir["integrity_status"] == "degraded"
    assert expected_reason in result.cir["reason_codes"]


def test_missing_or_unalignable_observer_and_unobserved_counters_fail_closed(tmp_path: Path) -> None:
    export, snapshot, raw, counter, decoder, alignment, _ = _prepared_inputs(
        tmp_path / "unalignable",
        capture_id="capture:p4-unalignable",
        session_id="session:p4-unalignable",
        alignment_state="unalignable",
        counter_observation_state="not_observed",
    )
    result = assemble_ccm_cir(export, snapshot, raw, counter, decoder, alignment)

    assert result.cir["integrity_status"] == "degraded"
    assert result.cir["alignment_degradation"] is True
    assert result.cir["observer_loss"] is True
    assert result.cir["buffer_high_watermark"] is None
    assert "COUNTERS_NOT_OBSERVED" in result.cir["reason_codes"]
    assert "ALIGNMENT_UNALIGNABLE" in result.cir["reason_codes"]


def test_unsupported_schema_and_wrong_ccm_cir_binding_fail_closed(tmp_path: Path) -> None:
    export, snapshot, raw, counter, decoder, alignment, _ = _prepared_inputs(tmp_path)
    bad_counter = deepcopy(counter)
    bad_counter["schema_version"] = "rtd-phase4-prehardware-v99"
    bad_counter = finalize_record(bad_counter)
    with pytest.raises(P4AssemblyError, match="schema version"):
        assemble_ccm_cir(export, snapshot, raw, bad_counter, decoder, alignment)

    result = assemble_ccm_cir(export, snapshot, raw, counter, decoder, alignment)
    wrong = deepcopy(result.cir)
    wrong["capability_manifest_id"] = "ccm:wrong"
    from spec.rtd_pilot_contracts import finalize_record as finalize_p2_record

    wrong = finalize_p2_record(wrong)
    result = type(result)(assembly_input=result.assembly_input, ccm=result.ccm, cir=wrong, lineage_context=result.lineage_context)
    with pytest.raises(P4AssemblyError, match="P2 join record invalid|CCM/CIR"):
        validate_capture_join(
            result,
            export=export,
            snapshot=snapshot,
            raw_artifact=raw,
            counter=counter,
            decoder=decoder,
            alignment=alignment,
        )


def test_firmware_and_elf_provenance_crossing_fails_closed(tmp_path: Path) -> None:
    first = _prepared_inputs(tmp_path / "first", capture_id="capture:p4-provenance", session_id="session:p4-provenance")
    crossed_export = deepcopy(first[0])
    crossed_export["firmware_hash"] = "f" * 64
    crossed_export["elf_hash"] = "e" * 64
    crossed_export = finalize_record(crossed_export)
    with pytest.raises(P4AssemblyError, match="config export|firmware|ELF|identity"):
        assemble_ccm_cir(crossed_export, first[1], first[2], first[3], first[4], first[5])

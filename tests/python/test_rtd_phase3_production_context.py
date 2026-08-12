from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from desktop.sample_data import build_scenario, write_scenario
from desktop.services import CompareService, ExportService, WorkspaceController
from parser.codec import GLOBAL_HEADER_STRUCT, encode_trace
from parser.parser_process_agent import (
    PARSER_PROCESS_ARTIFACT_VERSION,
    ParserProcessAgent,
    _trace_checksum,
    _worker_main,
)
from parser.pipeline import load_dataset, load_dataset_from_chunks
from parser.rtd_lineage import (
    MISSING_CAPTURE_LINEAGE_CONTEXT_REASON,
    CaptureLineageContext,
    LineageStatus,
    LineageValidationError,
)
from spec.rtd_pilot_contracts import SCHEMA_VERSION, finalize_record
from spec.io import checksum_file, serialize
from spec.schema_loader import DICTIONARY_PATH


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
    "TASK_READY",
    "TASK_WAKEUP",
)


def _p2_records(
    *,
    capture_id: str = "capture:production-a",
    ccm_capture_id: str | None = None,
    cir_capture_id: str | None = None,
    capability_manifest_id: str = "ccm:production-a",
    cir_capability_manifest_id: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    ccm_capture_id = ccm_capture_id or capture_id
    cir_capture_id = cir_capture_id or capture_id
    ccm = finalize_record(
        {
            "schema_version": SCHEMA_VERSION,
            "capability_manifest_id": capability_manifest_id,
            "capture_id": ccm_capture_id,
            "session_id": "session:production",
            "config_snapshot_id": "snapshot:production",
            "config_snapshot_digest": HASH_A,
            "collector_config_hash": HASH_B,
            "event_types_enabled": list(EVENT_TYPES),
            "task_filter": [],
            "resource_filter": [],
            "irq_filter": [],
            "core_filter": ["0", "1"],
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
            "cir_record_id": f"cir:{cir_capture_id.rsplit(':', 1)[-1]}",
            "capture_id": cir_capture_id,
            "session_id": "session:production",
            "capability_manifest_id": cir_capability_manifest_id or capability_manifest_id,
            "capability_manifest_digest": ccm["record_digest"],
            "raw_trace_id": "artifact:production",
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


def _context(*, capture_id: str = "capture:production-a") -> CaptureLineageContext:
    return CaptureLineageContext.from_p2_records(*_p2_records(capture_id=capture_id))


def _write_single_core_trace(path: Path) -> Path:
    events = [event for event in build_scenario(name="basic") if event["core_id"] == 0]
    return encode_trace(
        path,
        events,
        producer_ver="phase3-production-test",
        run_id="capture:production-a",
    )


def _assert_context_bound(
    bundle,
    context: CaptureLineageContext,
    *,
    require_complete: bool = True,
) -> None:
    registry = bundle.lineage_registry
    assert registry.capture_context == context
    assert bundle.capture_id == context.capture_id
    assert bundle.capture_capability_manifest_ref == context.capture_capability_manifest_ref
    assert bundle.capture_integrity_record_ref == context.capture_integrity_record_ref
    assert registry.records
    if require_complete:
        assert any(record.lineage_status is LineageStatus.COMPLETE for record in registry.records.values())
    for record in registry.records.values():
        assert record.capture_context == context
        assert record.capture_capability_manifest_ref == context.capture_capability_manifest_ref
        assert record.capture_integrity_record_ref == context.capture_integrity_record_ref
        assert MISSING_CAPTURE_LINEAGE_CONTEXT_REASON not in record.reason_codes
    for binding in registry.window_integrity_bindings.values():
        assert binding.capture_id == context.capture_id
        assert binding.capture_integrity_record_ref == context.capture_integrity_record_ref


def _parse_isolated(
    trace_path: Path,
    context: CaptureLineageContext | None,
    artifact_dir: Path,
    *,
    cache_root: Path | None = None,
):
    policy: dict[str, object] = {
        "artifact_dir": str(artifact_dir),
        "load_artifact": True,
        "retain_artifact": True,
    }
    if cache_root is not None:
        policy.update({"cache_enabled": True, "cache_root": str(cache_root)})
    return ParserProcessAgent(job_id=f"production-{artifact_dir.name}").parse_rebuild(
        trace_path,
        artifact_policy=policy,
        lineage_context=context,
    )


def test_direct_pipeline_and_worker_preserve_equivalent_complete_capture_lineage(tmp_path: Path) -> None:
    trace_path = _write_single_core_trace(tmp_path / "equivalent.trace")
    context = _context()

    direct = load_dataset(trace_path, lineage_context=context)
    isolated = _parse_isolated(trace_path, context, tmp_path / "isolated")

    assert direct.ok, direct.message
    assert isolated.ok, isolated.message
    _assert_context_bound(direct.data.bundle, context)
    _assert_context_bound(isolated.data["artifact"].bundle, context)
    assert json.loads((tmp_path / "isolated" / "capture_lineage_context.json").read_text(encoding="utf-8")) == context.to_dict()
    assert direct.data.bundle.lineage_registry.to_dict() == isolated.data["artifact"].bundle.lineage_registry.to_dict()


def test_channel_pipeline_and_desktop_entry_forward_context_without_capture_crossing(tmp_path: Path) -> None:
    trace_path = _write_single_core_trace(tmp_path / "channel.trace")
    context = _context()

    direct = load_dataset(trace_path, lineage_context=context)
    streamed = load_dataset_from_chunks(
        [(0, trace_path.read_bytes())],
        source="channel:test",
        lineage_context=context,
    )
    controller = WorkspaceController()
    desktop = controller.viz_LoadDatasetFromChannel(
        "file",
        {"path": str(trace_path), "read_size": 17},
        lineage_context=context,
    )

    assert direct.ok, direct.message
    assert streamed.ok, streamed.message
    assert desktop.ok, desktop.message
    _assert_context_bound(direct.data.bundle, context)
    _assert_context_bound(streamed.data.bundle, context)
    _assert_context_bound(controller.repository.get(desktop.data).artifact.bundle, context)
    assert direct.data.bundle.lineage_registry.to_dict() == streamed.data.bundle.lineage_registry.to_dict()

    wrong_context = _context(capture_id="capture:production-b")
    rejected = WorkspaceController().viz_LoadDatasetFromChannel(
        "file",
        {"path": str(trace_path), "read_size": 17},
        lineage_context=wrong_context,
    )
    assert not rejected.ok
    assert rejected.code == "INVALID_ARG"
    assert "does not match trace header capture identity" in rejected.message


@pytest.mark.parametrize(
    "malformed_context",
    [
        replace(_context(), event_types_enabled={"TASK_READY": True}),
        replace(_context(), sampling_boundary_complete=1),
        replace(_context(), mapping_valid=0),
    ],
)
def test_direct_and_channel_reject_manually_malformed_context(
    tmp_path: Path,
    malformed_context: CaptureLineageContext,
) -> None:
    trace_path = _write_single_core_trace(tmp_path / "malformed-direct-context.trace")

    direct = load_dataset(trace_path, lineage_context=malformed_context)
    channel = load_dataset_from_chunks(
        [(0, trace_path.read_bytes())],
        source="channel:malformed-context",
        lineage_context=malformed_context,
    )

    assert not direct.ok
    assert direct.code == "INVALID_ARG"
    assert not channel.ok
    assert channel.code == "INVALID_ARG"


def test_worker_ipc_round_trip_preserves_capture_identity_and_all_lineage_references(tmp_path: Path) -> None:
    trace_path = encode_trace(
        tmp_path / "round-trip.trace",
        build_scenario(name="gap"),
        producer_ver="phase3-production-test",
        run_id="capture:production-a",
    )
    context = _context()
    isolated = _parse_isolated(trace_path, context, tmp_path / "round-trip")

    assert isolated.ok, isolated.message
    bundle = isolated.data["artifact"].bundle
    _assert_context_bound(bundle, context, require_complete=False)
    context_request = tmp_path / "round-trip" / "capture_lineage_context.json"
    assert json.loads(context_request.read_text(encoding="utf-8")) == context.to_dict()
    registry = bundle.lineage_registry
    assert registry.to_dict()["capture_context"] == context.to_dict()
    assert registry.window_integrity_bindings
    for record in registry.records.values():
        assert record.source_event_ids
        assert set(record.boundary_event_ids).issubset(record.source_event_ids)
        assert record.open_event_id is None or record.open_event_id in record.source_event_ids
        assert record.close_event_id is None or record.close_event_id in record.source_event_ids
        assert set(record.all_intersecting_untrusted_window_ids).issubset(registry.window_integrity_bindings)
    for binding in registry.window_integrity_bindings.values():
        assert binding.capture_id == context.capture_id
        assert binding.capture_integrity_record_ref == context.capture_integrity_record_ref
        assert binding.capture_integrity_record_digest == context.capture_integrity_record_digest


def test_desktop_and_compare_isolated_entries_forward_explicit_context(tmp_path: Path) -> None:
    trace_path = _write_single_core_trace(tmp_path / "desktop.trace")
    context = _context()
    controller = WorkspaceController()

    loaded = controller.viz_LoadDataset(str(trace_path), lineage_context=context)

    assert loaded.ok, loaded.message
    _assert_context_bound(controller.repository.get(loaded.data).artifact.bundle, context)

    comparison = CompareService(controller.repository, controller.context_store)
    paired = comparison.cmp_LoadPair(
        str(trace_path),
        str(trace_path),
        baseline_lineage_context=context,
        candidate_lineage_context=context,
    )

    assert paired.ok, paired.message
    for record in comparison.repository._records.values():
        _assert_context_bound(record.artifact.bundle, context)

    mismatched_repository_context = comparison.cmp_LoadPair(
        loaded.data,
        str(trace_path),
        baseline_lineage_context=_context(capture_id="capture:production-b"),
        candidate_lineage_context=context,
    )
    assert not mismatched_repository_context.ok
    assert mismatched_repository_context.code == "INVALID_ARG"
    assert "does not match dataset capture identity" in mismatched_repository_context.message


def test_package_event_stream_reconstruction_reuses_saved_capture_context(tmp_path: Path, monkeypatch) -> None:
    trace_path = _write_single_core_trace(tmp_path / "package-source.trace")
    context = _context()
    controller = WorkspaceController()
    loaded = controller.viz_LoadDataset(str(trace_path), lineage_context=context)
    assert loaded.ok, loaded.message

    export = ExportService(controller.repository, controller.context_store, controller.jobs)
    job = export.export_Full({"dataset_id": loaded.data})
    assert job.ok, job.message
    package_dir = tmp_path / "package"
    written = export.export_WritePackage(job.data["job_id"], str(package_dir))
    assert written.ok, written.message

    rebuild_path = package_dir / "rebuild" / "rebuild_bundle.json"
    rebuild_payload = json.loads(rebuild_path.read_text(encoding="utf-8"))
    rebuild_payload.pop("event_stream")
    rebuild_path.write_text(json.dumps(rebuild_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path = package_dir / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest_payload["entries"]:
        if entry["path"] == "rebuild/rebuild_bundle.json":
            from spec.io import checksum_file

            entry["checksum"] = checksum_file(rebuild_path)
            break
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    received_contexts: list[CaptureLineageContext | None] = []
    original_parse_rebuild = ParserProcessAgent.parse_rebuild

    def capture_context(agent, source, **kwargs):
        received_contexts.append(kwargs.get("lineage_context"))
        return original_parse_rebuild(agent, source, **kwargs)

    monkeypatch.setattr(ParserProcessAgent, "parse_rebuild", capture_context)
    reloaded = WorkspaceController().viz_LoadDataset(str(package_dir))
    assert reloaded.ok, reloaded.message
    assert received_contexts == [context]

    mismatched = WorkspaceController().viz_LoadDataset(
        str(package_dir),
        lineage_context=_context(capture_id="capture:production-b"),
    )
    assert not mismatched.ok
    assert mismatched.code == "INVALID_ARG"
    assert "does not match package capture identity" in mismatched.message

    package_compare = CompareService(WorkspaceController().repository, WorkspaceController().context_store)
    mismatched_compare = package_compare.cmp_LoadPair(
        str(package_dir),
        str(trace_path),
        baseline_lineage_context=_context(capture_id="capture:production-b"),
        candidate_lineage_context=context,
    )
    assert not mismatched_compare.ok
    assert mismatched_compare.code == "INVALID_ARG"
    assert "does not match package capture identity" in mismatched_compare.message


def test_missing_context_remains_legacy_compatible_and_fail_closed(tmp_path: Path) -> None:
    trace_path = write_scenario(tmp_path / "legacy.trace", name="basic")

    result = _parse_isolated(trace_path, None, tmp_path / "legacy")

    assert result.ok, result.message
    registry = result.data["artifact"].bundle.lineage_registry
    assert registry.capture_context is None
    assert all(record.lineage_status is not LineageStatus.COMPLETE for record in registry.records.values())
    assert all(MISSING_CAPTURE_LINEAGE_CONTEXT_REASON in record.reason_codes for record in registry.records.values())


def test_cross_capture_and_cross_ccm_bindings_are_rejected_before_worker_dispatch() -> None:
    ccm, cir = _p2_records(ccm_capture_id="capture:production-a", cir_capture_id="capture:production-b")
    with pytest.raises(LineageValidationError, match="capture_id mismatch"):
        CaptureLineageContext.from_p2_records(ccm, cir)

    ccm, cir = _p2_records(cir_capability_manifest_id="ccm:other")
    with pytest.raises(LineageValidationError, match="does not reference CCM"):
        CaptureLineageContext.from_p2_records(ccm, cir)


def test_direct_and_isolated_reject_context_for_a_different_trace_capture(tmp_path: Path) -> None:
    trace_path = _write_single_core_trace(tmp_path / "capture-b.trace")
    events = [event for event in build_scenario(name="basic") if event["core_id"] == 0]
    encode_trace(
        trace_path,
        events,
        producer_ver="phase3-production-test",
        run_id="capture:production-b",
    )
    context_a = _context(capture_id="capture:production-a")

    direct = load_dataset(trace_path, lineage_context=context_a)
    isolated = _parse_isolated(trace_path, context_a, tmp_path / "capture-b-isolated")

    assert not direct.ok
    assert direct.code == "INVALID_ARG"
    assert "does not match trace header capture identity" in direct.message
    assert not isolated.ok
    assert isolated.code == "INVALID_ARG"
    assert "does not match trace header capture identity" in isolated.message


def test_direct_and_isolated_reject_missing_trace_capture_identity(tmp_path: Path) -> None:
    trace_path = encode_trace(
        tmp_path / "missing-capture.trace",
        build_scenario(name="basic"),
        producer_ver="phase3-production-test",
        run_id="\0",
    )
    context = _context()

    direct = load_dataset(trace_path, lineage_context=context)
    isolated = _parse_isolated(trace_path, context, tmp_path / "missing-capture-isolated")

    assert not direct.ok
    assert direct.code == "INVALID_ARG"
    assert "does not match trace header capture identity" in direct.message
    assert not isolated.ok
    assert isolated.code == "INVALID_ARG"
    assert "does not match trace header capture identity" in isolated.message


def test_direct_and_isolated_reject_invalid_utf8_capture_identity(tmp_path: Path) -> None:
    trace_path = _write_single_core_trace(tmp_path / "invalid-utf8-capture.trace")
    raw_trace = bytearray(trace_path.read_bytes())
    header = list(GLOBAL_HEADER_STRUCT.unpack_from(raw_trace, 0))
    run_id = bytearray(header[-1])
    run_id[len(b"capture:production-a")] = 0xFF
    header[-1] = bytes(run_id)
    raw_trace[: GLOBAL_HEADER_STRUCT.size] = GLOBAL_HEADER_STRUCT.pack(*header)
    trace_path.write_bytes(raw_trace)
    context = _context()

    direct = load_dataset(trace_path, lineage_context=context)
    isolated = _parse_isolated(trace_path, context, tmp_path / "invalid-utf8-isolated")

    assert not direct.ok
    assert direct.code == "INVALID_ARG"
    assert "valid UTF-8 identity" in direct.message
    assert not isolated.ok
    assert isolated.code == "INVALID_ARG"
    assert "valid UTF-8 identity" in isolated.message
    assert "artifact" not in (isolated.data or {})


def test_direct_and_isolated_reject_mixed_capture_segments(tmp_path: Path) -> None:
    segment_dir = tmp_path / "mixed-capture-segments"
    segment_dir.mkdir()
    events = [event for event in build_scenario(name="basic") if event["core_id"] == 0]
    midpoint = len(events) // 2
    encode_trace(
        segment_dir / "segment_000.trace",
        events[:midpoint],
        producer_ver="phase3-production-test",
        run_id="capture:production-a",
    )
    encode_trace(
        segment_dir / "segment_001.trace",
        events[midpoint:],
        producer_ver="phase3-production-test",
        run_id="capture:production-b",
    )
    context_a = _context(capture_id="capture:production-a")

    direct = load_dataset(segment_dir, lineage_context=context_a)
    isolated = _parse_isolated(segment_dir, context_a, tmp_path / "mixed-capture-isolated")

    assert not direct.ok
    assert direct.code == "INVALID_ARG"
    assert "does not match trace header capture identity" in direct.message
    assert not isolated.ok
    assert isolated.code == "INVALID_ARG"
    assert "does not match trace header capture identity" in isolated.message
    assert "artifact" not in (isolated.data or {})


def test_direct_and_isolated_reject_headerless_capture_segment(tmp_path: Path) -> None:
    segment_dir = tmp_path / "headerless-capture-segment"
    segment_dir.mkdir()
    _write_single_core_trace(segment_dir / "segment_000.trace")
    (segment_dir / "segment_001.trace").write_bytes(b"partial")
    context = _context()

    direct = load_dataset(segment_dir, lineage_context=context)
    isolated = _parse_isolated(segment_dir, context, tmp_path / "headerless-isolated")

    assert not direct.ok
    assert direct.code == "INVALID_ARG"
    assert "requires a trace header for segment" in direct.message
    assert not isolated.ok
    assert isolated.code == "INVALID_ARG"
    assert "requires a trace header for segment" in isolated.message
    assert "artifact" not in (isolated.data or {})


def test_direct_and_isolated_reject_header_split_across_capture_segments(tmp_path: Path) -> None:
    segment_dir = tmp_path / "split-capture-header"
    segment_dir.mkdir()
    first = _write_single_core_trace(segment_dir / "segment_000.trace")
    split_source = _write_single_core_trace(tmp_path / "split-source.trace")
    split_header = split_source.read_bytes()[: GLOBAL_HEADER_STRUCT.size]
    first.write_bytes(first.read_bytes() + split_header[:8])
    (segment_dir / "segment_001.trace").write_bytes(split_header[8:])
    context = _context()

    direct = load_dataset(segment_dir, lineage_context=context)
    isolated = _parse_isolated(segment_dir, context, tmp_path / "split-isolated")

    assert not direct.ok
    assert direct.code == "INVALID_ARG"
    assert "requires a trace header for segment" in direct.message
    assert not isolated.ok
    assert isolated.code == "INVALID_ARG"
    assert "requires a trace header for segment" in isolated.message
    assert "artifact" not in (isolated.data or {})


def test_agent_rejects_malformed_and_unsupported_context_without_starting_worker(tmp_path: Path) -> None:
    trace_path = write_scenario(tmp_path / "invalid-request.trace", name="basic")

    malformed = ParserProcessAgent(job_id="malformed-context").parse_rebuild(
        trace_path,
        artifact_dir=tmp_path / "malformed",
        lineage_context=object(),  # type: ignore[arg-type]
    )
    unsupported = ParserProcessAgent(job_id="unsupported-context").parse_rebuild(
        trace_path,
        artifact_dir=tmp_path / "unsupported",
        lineage_context=replace(_context(), capture_integrity_record_schema_version="rtd-phase2-v9.0"),
    )

    assert not malformed.ok
    assert malformed.code == "INVALID_ARG"
    assert "wrong type" in malformed.message
    assert not unsupported.ok
    assert unsupported.code == "INVALID_ARG"
    assert "unsupported CIR schema version" in unsupported.message


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"capture_id": "capture:production-a"},
        {**_context().to_dict(), "capture_capability_manifest_schema_version": "rtd-phase2-v9.0"},
        {**_context().to_dict(), "event_types_enabled": {"not": "a list"}},
        {**_context().to_dict(), "sampling_boundary_complete": 1},
        {**_context().to_dict(), "mapping_valid": 0},
    ],
)
def test_worker_rejects_malformed_or_unsupported_ipc_payload(tmp_path: Path, payload: dict[str, object]) -> None:
    trace_path = write_scenario(tmp_path / "worker-invalid.trace", name="basic")
    request_path = tmp_path / "capture_lineage_context.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = _worker_main(
        [
            "--worker",
            "--source",
            str(trace_path),
            "--artifact-path",
            str(tmp_path / "artifact.pickle"),
            "--result-path",
            str(result_path),
            "--lineage-context-path",
            str(request_path),
        ]
    )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert result["code"] == "INVALID_ARG"
    assert "invalid capture lineage context request" in result["message"]


def test_worker_cache_never_cross_contaminates_capture_contexts(tmp_path: Path) -> None:
    events = [event for event in build_scenario(name="basic") if event["core_id"] == 0]
    trace_path = encode_trace(
        tmp_path / "cache-a.trace",
        events,
        producer_ver="phase3-production-test",
        run_id="capture:production-a",
    )
    trace_path_b = encode_trace(
        tmp_path / "cache-b.trace",
        events,
        producer_ver="phase3-production-test",
        run_id="capture:production-b",
    )
    cache_root = tmp_path / "cache"
    context_a = _context(capture_id="capture:production-a")
    context_b = _context(capture_id="capture:production-b")

    first = _parse_isolated(trace_path, context_a, tmp_path / "worker-a", cache_root=cache_root)
    second = _parse_isolated(trace_path_b, context_b, tmp_path / "worker-b", cache_root=cache_root)

    assert first.ok, first.message
    assert second.ok, second.message
    assert first.data["cache_key"] != second.data["cache_key"]
    assert not second.data["cache_hit"]
    _assert_context_bound(first.data["artifact"].bundle, context_a)
    _assert_context_bound(second.data["artifact"].bundle, context_b)
    assert all(
        record.capture_context.capture_id == "capture:production-a"
        for record in first.data["artifact"].bundle.lineage_registry.records.values()
    )
    assert all(
        record.capture_context.capture_id == "capture:production-b"
        for record in second.data["artifact"].bundle.lineage_registry.records.values()
    )


def test_capture_context_rejects_prepreflight_cache_policy(tmp_path: Path) -> None:
    trace_path = _write_single_core_trace(tmp_path / "legacy-cache.trace")
    cache_root = tmp_path / "cache"
    context = _context()

    parsed = _parse_isolated(trace_path, context, tmp_path / "initial", cache_root=cache_root)
    assert parsed.ok, parsed.message
    current_cache_dir = cache_root / parsed.data["cache_key"]
    old_payload = {
        "trace_checksum": _trace_checksum(trace_path),
        "dictionary_checksum": checksum_file(DICTIONARY_PATH),
        "parser_version": PARSER_PROCESS_ARTIFACT_VERSION,
        "index_build_mode": "full",
        "materialize_event_stream": True,
        "schema_version": PARSER_PROCESS_ARTIFACT_VERSION,
        "lineage_context": context.to_dict(),
        "capture_lineage_binding_policy_version": "capture-lineage-header-binding-v2",
    }
    old_cache_key = hashlib.sha256(
        json.dumps(serialize(old_payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert old_cache_key != parsed.data["cache_key"]
    shutil.copytree(current_cache_dir, cache_root / old_cache_key)
    shutil.rmtree(current_cache_dir)

    reparsed = _parse_isolated(trace_path, context, tmp_path / "reparsed", cache_root=cache_root)
    assert reparsed.ok, reparsed.message
    assert not reparsed.data["cache_hit"]


def test_capture_context_rejects_stale_cache_artifact_with_another_capture(tmp_path: Path) -> None:
    events = [event for event in build_scenario(name="basic") if event["core_id"] == 0]
    trace_a = encode_trace(tmp_path / "cache-a.trace", events, run_id="capture:production-a")
    trace_b = encode_trace(tmp_path / "cache-b.trace", events, run_id="capture:production-b")
    cache_root = tmp_path / "cache"
    context_a = _context(capture_id="capture:production-a")
    context_b = _context(capture_id="capture:production-b")

    first_a = _parse_isolated(trace_a, context_a, tmp_path / "first-a", cache_root=cache_root)
    parsed_b = _parse_isolated(trace_b, context_b, tmp_path / "first-b", cache_root=cache_root)
    assert first_a.ok, first_a.message
    assert parsed_b.ok, parsed_b.message
    cache_a = cache_root / first_a.data["cache_key"]
    cache_b = cache_root / parsed_b.data["cache_key"]
    shutil.copyfile(cache_b / "parse_rebuild_artifact.pickle", cache_a / "parse_rebuild_artifact.pickle")

    reparsed_a = _parse_isolated(trace_a, context_a, tmp_path / "reparsed-a", cache_root=cache_root)
    assert reparsed_a.ok, reparsed_a.message
    assert not reparsed_a.data["cache_hit"]
    _assert_context_bound(reparsed_a.data["artifact"].bundle, context_a)

from __future__ import annotations

import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from desktop.sample_data import build_scenario, write_scenario
from desktop.services import ExportService, WorkspaceController
from metric.core import alert_Evaluate, diag_Generate, metric_Compute, metric_Ingest, metric_Init
from parser import (
    align_events,
    decode_trace,
    encode_trace,
    load_dataset,
    load_dataset_with_timings,
    prs_Load,
    prs_FeedChunk,
    prs_Finalize,
    prs_Init,
    prs_Prescan,
    prs_Verify,
    query_events,
    rb_Rebuild,
)
from parser.codec import (
    CHUNK_HEADER_STRUCT,
    EVENT_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_CHUNK_MAGIC,
    TRACE_FORMAT_MAGIC,
)
from parser.index import query_events_from_trace
from parser.models import DecodedEvent, UnifiedEvent, dataclass_to_dict
from spec.events import event_id_for
from spec.schema_loader import SpecError, load_dictionary


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _dictionary_copy(self) -> dict[str, object]:
        return json.loads(json.dumps(load_dictionary()))

    def _rename_event(self, raw_dictionary: dict[str, object], event_id: int, event_name: str) -> dict[str, object]:
        for item in raw_dictionary["event_defs"]:
            if item["event_id"] == event_id:
                item["event_name"] = event_name
                return raw_dictionary
        raise AssertionError(f"event_id {event_id} not found")

    def _append_event_definition(
        self,
        raw_dictionary: dict[str, object],
        event_id: int,
        event_name: str,
        payload_fields: list[str],
        domain: str = "task",
    ) -> dict[str, object]:
        raw_dictionary["event_defs"].append(
            {
                "event_id": event_id,
                "event_name": event_name,
                "domain": domain,
                "payload_fields": payload_fields,
            }
        )
        return raw_dictionary

    def _rewrite_segment_meta_size(self, trace_path: Path, meta_size: int) -> None:
        raw = bytearray(trace_path.read_bytes())
        meta_size_offset = GLOBAL_HEADER_STRUCT.size + 4 + 2
        raw[meta_size_offset : meta_size_offset + 2] = int(meta_size).to_bytes(2, "little")
        trace_path.write_bytes(raw)

    def _write_empty_payload_trace(self, trace_path: Path) -> None:
        dictionary = load_dictionary()
        dict_ver = int(dictionary["dict_ver"])
        core_id = 0
        seq = 1
        ts = 100
        event_id = 50001
        payload_len = 0
        chunk_bytes = EVENT_HEADER_STRUCT.pack(1, 0, core_id, event_id, seq, ts, payload_len)
        crc = zlib.crc32(chunk_bytes) & 0xFFFFFFFF
        header = GLOBAL_HEADER_STRUCT.pack(
            TRACE_FORMAT_MAGIC,
            1,
            1,
            1,
            dict_ver,
            1,
            1,
            b"steady_clock".ljust(16, b"\0"),
            b"pytest".ljust(32, b"\0"),
            b"empty-payload".ljust(32, b"\0"),
        )
        chunk_header = CHUNK_HEADER_STRUCT.pack(
            TRACE_CHUNK_MAGIC,
            1,
            core_id,
            1,
            len(chunk_bytes),
            ts,
            ts,
            seq,
            seq,
            dict_ver,
            crc,
        )
        with trace_path.open("wb") as handle:
            handle.write(header)
            handle.write(chunk_header)
            handle.write(chunk_bytes)

    def _alignment_regression_events(self) -> list[DecodedEvent]:
        return [
            DecodedEvent(
                core_id=0,
                seq=1,
                timestamp_raw=100.0,
                timestamp_aligned=100.0,
                event_id=event_id_for("TASK_READY"),
                event_name="TASK_READY",
                task_id=1,
                obj_id=None,
                irq_id=None,
                job_id=None,
                instance_id=None,
                payload={"task_id": 1},
                trust_tags=[],
                chunk_id=0,
            ),
            DecodedEvent(
                core_id=1,
                seq=1,
                timestamp_raw=120.0,
                timestamp_aligned=120.0,
                event_id=event_id_for("TASK_READY"),
                event_name="TASK_READY",
                task_id=2,
                obj_id=None,
                irq_id=None,
                job_id=None,
                instance_id=None,
                payload={"task_id": 2},
                trust_tags=[],
                chunk_id=0,
            ),
            DecodedEvent(
                core_id=0,
                seq=2,
                timestamp_raw=500.0,
                timestamp_aligned=500.0,
                event_id=event_id_for("SYNC_CALIB"),
                event_name="SYNC_CALIB",
                task_id=None,
                obj_id=None,
                irq_id=None,
                job_id=None,
                instance_id=None,
                payload={"anchor_id": 1, "core_id": 0, "ref_ts": 500},
                trust_tags=[],
                chunk_id=0,
            ),
            DecodedEvent(
                core_id=0,
                seq=3,
                timestamp_raw=580.0,
                timestamp_aligned=580.0,
                event_id=event_id_for("CTX_SWITCH"),
                event_name="CTX_SWITCH",
                task_id=None,
                obj_id=None,
                irq_id=None,
                job_id=None,
                instance_id=None,
                payload={"core_id": 0, "prev_task_id": 1, "next_task_id": 2, "reason": 2},
                trust_tags=[],
                chunk_id=0,
            ),
            DecodedEvent(
                core_id=1,
                seq=2,
                timestamp_raw=600.0,
                timestamp_aligned=600.0,
                event_id=event_id_for("TS_CALIB"),
                event_name="TS_CALIB",
                task_id=None,
                obj_id=None,
                irq_id=None,
                job_id=None,
                instance_id=None,
                payload={"anchor_id": 1, "src_core": 1, "dst_core": 0, "raw_ts": 600},
                trust_tags=[],
                chunk_id=0,
            ),
            DecodedEvent(
                core_id=1,
                seq=3,
                timestamp_raw=650.0,
                timestamp_aligned=650.0,
                event_id=event_id_for("TASK_WAKEUP"),
                event_name="TASK_WAKEUP",
                task_id=2,
                obj_id=0x10,
                irq_id=None,
                job_id=None,
                instance_id=None,
                payload={"task_id": 2, "obj_id": 0x10},
                trust_tags=[],
                chunk_id=0,
            ),
        ]

    def _append_zero_filler_chunk(
        self,
        trace_path: Path,
        *,
        payload_bytes: int = 1024 * 1024,
        chunk_start: int = 1_000_000_000,
        chunk_end: int = 1_000_000_000,
        core_id: int = 0,
        seq: int = 0,
        dict_ver: int = 1,
    ) -> None:
        payload = b"\0" * payload_bytes
        chunk_header = CHUNK_HEADER_STRUCT.pack(
            TRACE_CHUNK_MAGIC,
            1,
            core_id,
            0,
            payload_bytes,
            chunk_start,
            chunk_end,
            seq,
            seq,
            dict_ver,
            zlib.crc32(payload) & 0xFFFFFFFF,
        )
        with trace_path.open("ab") as handle:
            handle.write(chunk_header)
            handle.write(payload)

    def _bundle_core_dict(self, bundle: object) -> dict[str, object]:
        payload = dataclass_to_dict(bundle)
        payload["capability_flags"] = {
            key: value
            for key, value in payload["capability_flags"].items()
            if not str(key).startswith("experimental_")
        }
        return payload

    def test_rebuild_generates_exec_slices_and_states(self) -> None:
        trace_path = write_scenario(self.root / "basic.trace", name="basic")
        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        self.assertGreaterEqual(len(bundle.exec_slices), 2)
        self.assertGreaterEqual(len(bundle.task_states), 3)
        self.assertTrue(any(segment.state == "RUNNING" for segment in bundle.task_states))

    def test_prescan_returns_lod0_preview_for_trace_and_segments(self) -> None:
        trace_path = write_scenario(self.root / "preview.trace", name="multi_core")
        prescan = prs_Prescan(trace_path, bucket_count=12)
        self.assertTrue(prescan.ok, prescan.message)
        self.assertGreater(prescan.data["chunk_count"], 0)
        self.assertGreater(len(prescan.data["lod0_buckets"]), 0)
        self.assertEqual(prescan.data["stage"], "preview_ready")
        self.assertEqual(
            prescan.data["readiness"]["lod_ready"],
            {"lod0": True, "lod1": False, "lod2": False},
        )
        self.assertTrue(prescan.data["readiness"]["view_ready"]["timeline"])
        self.assertFalse(prescan.data["readiness"]["view_ready"]["task_states"])
        self.assertLess(prescan.data["time_window"][0], prescan.data["time_window"][1])

        segment_dir = self.root / "segments"
        segment_dir.mkdir()
        for index in range(2):
            write_scenario(segment_dir / f"part{index}.trace", name="basic")
        segment_prescan = prs_Prescan(segment_dir)
        self.assertTrue(segment_prescan.ok, segment_prescan.message)
        self.assertGreater(segment_prescan.data["chunk_count"], 0)
        self.assertGreater(len(segment_prescan.data["lod0_buckets"]), 0)

    def test_task_state_preview_ready_before_full_event_materialization(self) -> None:
        trace_path = write_scenario(self.root / "task-preview.trace", name="basic")
        prescan = prs_Prescan(trace_path, bucket_count=8)
        self.assertTrue(prescan.ok, prescan.message)
        task_preview = prescan.data.get("task_state_preview") or {}
        self.assertEqual(prescan.data["stage"], "preview_ready")
        self.assertEqual(task_preview["readiness"]["stage"], "preview_ready")
        self.assertGreater(task_preview["lane_count"], 0)
        self.assertGreater(len(task_preview["task_ids"]), 0)
        self.assertGreater(sum(task_preview["state_totals"].values()), 0)
        self.assertGreater(len(task_preview["bucket_summary"]), 0)
        self.assertTrue(task_preview["trusted"])

    def test_prescan_can_skip_task_state_preview_for_lightweight_mode(self) -> None:
        trace_path = write_scenario(self.root / "task-preview-lite.trace", name="basic")
        prescan = prs_Prescan(trace_path, bucket_count=8, include_task_state_preview=False)
        self.assertTrue(prescan.ok, prescan.message)
        self.assertEqual(prescan.data["stage"], "preview_ready")
        self.assertIsNone(prescan.data.get("task_state_preview"))
        self.assertGreater(len(prescan.data["lod0_buckets"]), 0)

    def test_load_dataset_with_timings_includes_memory_snapshots(self) -> None:
        trace_path = write_scenario(self.root / "timings.trace", name="basic")
        loaded = load_dataset_with_timings(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        snapshots = loaded.data.get("memory_snapshots")
        self.assertIsInstance(snapshots, list)
        stages = [item.get("stage") for item in snapshots]
        self.assertIn("parse_start", stages)
        self.assertIn("parse_end", stages)
        self.assertIn("rebuild_end", stages)
        stage_timings = loaded.data.get("load_stage_timings") or {}
        self.assertGreater(stage_timings.get("prs_Verify_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("prs_Load_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("prs_FeedChunk_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("TraceDecodeSession.feed_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("_decode_chunk_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("align_events_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("rb_Rebuild_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("idx_Build_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("load_seconds", 0.0), 0.0)
        self.assertGreater(loaded.data.get("parse_seconds", 0.0), 0.0)
        self.assertEqual(loaded.data.get("align_events_seconds"), stage_timings.get("align_events_seconds"))
        self.assertEqual(loaded.data.get("rebuild_seconds"), stage_timings.get("rb_Rebuild_seconds"))
        self.assertEqual(
            loaded.data.get("pipeline_rebuild_seconds"),
            round(
                float(stage_timings.get("align_events_seconds", 0.0))
                + float(stage_timings.get("rb_Rebuild_seconds", 0.0))
                + float(stage_timings.get("idx_Build_seconds", 0.0)),
                6,
            ),
        )
        self.assertEqual(loaded.data.get("idx_build_seconds"), stage_timings.get("idx_Build_seconds"))
        self.assertEqual(loaded.data.get("load_seconds"), stage_timings.get("load_seconds"))
        self.assertTrue(loaded.data.get("materialize_event_stream"))
        rss_values = [item.get("rss_mb") for item in snapshots if item.get("rss_mb") is not None]
        expected_peak_rss_mb = round(max(rss_values), 6) if rss_values else None
        self.assertEqual(loaded.data.get("peak_rss_mb"), expected_peak_rss_mb)
        hotspot_summary = loaded.data.get("load_hotspot_summary") or {}
        self.assertIn("dominant_object", hotspot_summary)
        self.assertIn("estimated_retained_bytes", hotspot_summary)
        self.assertIn("hottest_chunk", hotspot_summary)
        self.assertIn("trust_tags_empty_reuse_count", hotspot_summary)
        self.assertIn("trust_tags_nonempty_count", hotspot_summary)
        artifact = loaded.data["artifact"]
        self.assertTrue(artifact.bundle.event_stream)
        first_event = artifact.bundle.event_stream[0]
        self.assertIsInstance(first_event, UnifiedEvent)
        self.assertTrue(first_event.event_uid)
        self.assertEqual(first_event.ref_key, first_event.event_uid)

    def test_load_dataset_with_timings_can_skip_event_stream_materialization(self) -> None:
        trace_path = write_scenario(self.root / "timings-thin-bundle.trace", name="basic")
        default_loaded = load_dataset_with_timings(trace_path)
        self.assertTrue(default_loaded.ok, default_loaded.message)
        self.assertTrue(default_loaded.data["artifact"].bundle.event_stream)

        thin_loaded = load_dataset_with_timings(trace_path, materialize_event_stream=False)
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        self.assertEqual(thin_loaded.data["artifact"].bundle.event_stream, [])
        self.assertGreater(len(thin_loaded.data["artifact"].bundle.task_states), 0)
        self.assertGreater(len(thin_loaded.data["artifact"].bundle.exec_slices), 0)

    def test_load_dataset_with_timings_experimental_rebuild_defaults_off(self) -> None:
        trace_path = write_scenario(self.root / "timings-experimental-default-off.trace", name="multi_core", repeat=2)
        loaded = load_dataset_with_timings(trace_path, materialize_event_stream=False)
        self.assertTrue(loaded.ok, loaded.message)
        self.assertFalse(loaded.data["experimental_parallel_rebuild"])
        self.assertIsNone(loaded.data["rebuild_morsel_size"])
        self.assertIsNone(loaded.data["rebuild_parallel_workers"])
        self.assertEqual(loaded.data["rebuild_mode"], "serial_rebuild")
        self.assertFalse(any(key.startswith("experimental_") for key in loaded.data["artifact"].bundle.capability_flags))

    def test_load_dataset_with_timings_experimental_morsel_rebuild_matches_serial_bundle(self) -> None:
        trace_path = write_scenario(self.root / "timings-experimental-morsel.trace", name="multi_core", repeat=2)
        serial_loaded = load_dataset_with_timings(trace_path, materialize_event_stream=False)
        self.assertTrue(serial_loaded.ok, serial_loaded.message)

        morsel_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            experimental_parallel_rebuild=True,
            rebuild_morsel_size=4,
        )
        self.assertTrue(morsel_loaded.ok, morsel_loaded.message)
        self.assertEqual(
            self._bundle_core_dict(morsel_loaded.data["artifact"].bundle),
            self._bundle_core_dict(serial_loaded.data["artifact"].bundle),
        )
        self.assertEqual(morsel_loaded.data["rebuild_mode"], "morsel_rebuild")
        self.assertTrue(morsel_loaded.data["experimental_parallel_rebuild"])
        self.assertEqual(morsel_loaded.data["rebuild_morsel_size"], 4)
        self.assertIsNone(morsel_loaded.data["rebuild_parallel_workers"])
        self.assertTrue(morsel_loaded.data["artifact"].bundle.capability_flags["experimental_parallel_rebuild"])
        self.assertTrue(morsel_loaded.data["artifact"].bundle.capability_flags["experimental_morsel_rebuild"])

    def test_load_dataset_with_timings_parallel_worker_experiment_falls_back_serial_with_parity(self) -> None:
        trace_path = write_scenario(self.root / "timings-experimental-workers.trace", name="multi_core", repeat=2)
        serial_loaded = load_dataset_with_timings(trace_path, materialize_event_stream=False)
        self.assertTrue(serial_loaded.ok, serial_loaded.message)

        fallback_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            experimental_parallel_rebuild=True,
            rebuild_morsel_size=3,
            rebuild_parallel_workers=4,
        )
        self.assertTrue(fallback_loaded.ok, fallback_loaded.message)
        self.assertEqual(
            self._bundle_core_dict(fallback_loaded.data["artifact"].bundle),
            self._bundle_core_dict(serial_loaded.data["artifact"].bundle),
        )
        self.assertEqual(fallback_loaded.data["rebuild_mode"], "morsel_rebuild")
        self.assertEqual(fallback_loaded.data["rebuild_parallel_workers"], 4)
        self.assertTrue(fallback_loaded.data["artifact"].bundle.capability_flags["experimental_parallel_requested"])
        self.assertTrue(fallback_loaded.data["artifact"].bundle.capability_flags["experimental_parallel_fallback_serial"])

    def test_load_dataset_with_timings_thin_mode_persists_alignment_summary(self) -> None:
        trace_path = write_scenario(self.root / "timings-thin-alignment.trace", name="multi_core")
        materialized = load_dataset_with_timings(trace_path)
        self.assertTrue(materialized.ok, materialized.message)

        thin_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(thin_loaded.ok, thin_loaded.message)
        self.assertEqual(thin_loaded.data["artifact"].bundle.event_stream, [])

        materialized_alignment = materialized.data["artifact"].bundle.alignment
        thin_alignment = thin_loaded.data["artifact"].bundle.alignment
        self.assertIsNotNone(materialized_alignment)
        self.assertIsNotNone(thin_alignment)
        self.assertEqual(dataclass_to_dict(thin_alignment), dataclass_to_dict(materialized_alignment))
        self.assertTrue(thin_alignment.anchors_seen)
        self.assertTrue(thin_alignment.calibrated)
        self.assertGreater(len(thin_alignment.core_ids), 1)
        self.assertTrue(any(abs(value) > 0.0 for value in thin_alignment.offsets.values()))

    def test_load_dataset_with_timings_supports_minimal_and_deferred_index_build_modes(self) -> None:
        trace_path = write_scenario(self.root / "timings-index-modes.trace", name="basic")

        minimal_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="minimal",
        )
        self.assertTrue(minimal_loaded.ok, minimal_loaded.message)
        minimal_index = minimal_loaded.data["artifact"].bundle.index_bundle
        self.assertIsNotNone(minimal_index)
        self.assertEqual(minimal_loaded.data["index_build_mode"], "minimal")
        self.assertFalse(minimal_loaded.data["materialize_event_stream"])
        self.assertEqual(minimal_index.summary["index_build_mode"], "minimal")
        self.assertEqual(
            minimal_loaded.data["rebuild_seconds"],
            minimal_loaded.data["load_stage_timings"]["rb_Rebuild_seconds"],
        )
        self.assertEqual(
            minimal_loaded.data["pipeline_rebuild_seconds"],
            round(
                float(minimal_loaded.data["load_stage_timings"]["align_events_seconds"])
                + float(minimal_loaded.data["load_stage_timings"]["rb_Rebuild_seconds"])
                + float(minimal_loaded.data["load_stage_timings"]["idx_Build_seconds"]),
                6,
            ),
        )
        self.assertEqual(
            minimal_loaded.data["idx_build_seconds"],
            minimal_loaded.data["load_stage_timings"]["idx_Build_seconds"],
        )
        self.assertFalse(minimal_index.summary["uid_indexes_built"])
        self.assertEqual(minimal_index.task_index, {})
        self.assertEqual(minimal_index.core_index, {})
        self.assertEqual(minimal_index.event_type_index, {})
        self.assertGreater(len(minimal_index.time_index), 0)

        deferred_loaded = load_dataset_with_timings(
            trace_path,
            materialize_event_stream=False,
            index_build_mode="deferred",
        )
        self.assertTrue(deferred_loaded.ok, deferred_loaded.message)
        deferred_index = deferred_loaded.data["artifact"].bundle.index_bundle
        self.assertIsNotNone(deferred_index)
        self.assertEqual(deferred_loaded.data["index_build_mode"], "deferred")
        self.assertFalse(deferred_loaded.data["materialize_event_stream"])
        self.assertEqual(deferred_index.summary["index_build_mode"], "deferred")
        self.assertFalse(deferred_index.summary["uid_indexes_built"])
        self.assertEqual(deferred_index.time_index, [])

    def test_load_dataset_with_timings_rejects_invalid_index_build_mode(self) -> None:
        trace_path = write_scenario(self.root / "timings-invalid-index-mode.trace", name="basic")
        loaded = load_dataset_with_timings(trace_path, index_build_mode="bogus")
        self.assertFalse(loaded.ok)
        self.assertEqual(loaded.code, "INVALID_ARG")

    def test_load_dataset_with_timings_thin_mode_emits_stage_observer_updates(self) -> None:
        trace_path = write_scenario(self.root / "timings-thin-observer.trace", name="basic")
        events: list[tuple[str, str]] = []

        def observer(stage: str, payload: dict[str, object]) -> None:
            events.append((stage, str(payload.get("status"))))

        loaded = load_dataset_with_timings(
            trace_path,
            stage_observer=observer,
            materialize_event_stream=False,
        )
        self.assertTrue(loaded.ok, loaded.message)
        self.assertEqual(loaded.data["artifact"].bundle.event_stream, [])
        self.assertIn(("prs_Verify", "completed"), events)
        self.assertIn(("align_events", "completed"), events)
        self.assertIn(("rb_Rebuild", "completed"), events)
        self.assertIn(("idx_Build", "completed"), events)

        stage_timings = loaded.data.get("load_stage_timings") or {}
        self.assertGreater(stage_timings.get("prs_Verify_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("align_events_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("rb_Rebuild_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("idx_Build_seconds", 0.0), 0.0)
        self.assertGreater(stage_timings.get("load_seconds", 0.0), 0.0)

    def test_prs_verify_materialize_events_false_returns_thin_events(self) -> None:
        trace_path = write_scenario(self.root / "verify-thin.trace", name="basic")
        default_verified = prs_Verify(trace_path)
        self.assertTrue(default_verified.ok, default_verified.message)
        self.assertTrue(default_verified.data["events"])
        self.assertIsInstance(default_verified.data["events"][0], UnifiedEvent)

        thin_verified = prs_Verify(trace_path, materialize_events=False)
        self.assertTrue(thin_verified.ok, thin_verified.message)
        self.assertTrue(thin_verified.data["events"])
        first_thin = thin_verified.data["events"][0]
        self.assertIsInstance(first_thin, DecodedEvent)
        self.assertFalse(hasattr(first_thin, "event_uid"))

    def test_prs_finalize_thin_mode_reuses_session_event_list(self) -> None:
        trace_path = write_scenario(self.root / "finalize-thin.trace", name="basic")
        init = prs_Init(materialize_events=False)
        self.assertTrue(init.ok, init.message)
        session = init.data

        fed = prs_FeedChunk(session, None, trace_path.read_bytes())
        self.assertTrue(fed.ok, fed.message)
        retained_events = session.decoder.events
        self.assertTrue(retained_events)

        finalized = prs_Finalize(session)
        self.assertTrue(finalized.ok, finalized.message)
        self.assertIs(finalized.data["events"], retained_events)
        self.assertEqual(session.decoder.events, [])

    def test_align_events_reuses_input_list_for_thin_events(self) -> None:
        trace_path = write_scenario(self.root / "align-thin.trace", name="basic")
        verified = prs_Verify(trace_path, materialize_events=False)
        self.assertTrue(verified.ok, verified.message)
        summary = verified.data.get("summary")
        dataset_id = getattr(summary, "dataset_id", None) or "stream"

        thin_events = verified.data["events"]
        aligned = align_events(
            thin_events,
            dataset_id=dataset_id,
            existing_windows=verified.data["untrusted_windows"],
        )
        self.assertTrue(aligned.ok, aligned.message)
        self.assertIs(aligned.data["events"], thin_events)

    def test_thin_events_align_and_rebuild_with_materialized_bundle_boundary(self) -> None:
        trace_path = write_scenario(self.root / "thin-align-rebuild.trace", name="basic")
        verified = prs_Verify(trace_path, materialize_events=False)
        self.assertTrue(verified.ok, verified.message)
        summary = verified.data.get("summary")
        dataset_id = getattr(summary, "dataset_id", None) or "stream"

        aligned = align_events(
            verified.data["events"],
            dataset_id=dataset_id,
            existing_windows=verified.data["untrusted_windows"],
        )
        self.assertTrue(aligned.ok, aligned.message)
        self.assertTrue(aligned.data["events"])
        self.assertIs(aligned.data["events"], verified.data["events"])
        self.assertIsInstance(aligned.data["events"][0], DecodedEvent)

        rebuilt = rb_Rebuild(
            aligned.data["events"],
            dataset_id=dataset_id,
            header=verified.data["header"],
            untrusted_windows=aligned.data["untrusted_windows"],
            capability_flags=aligned.data["capability_flags"],
        )
        self.assertTrue(rebuilt.ok, rebuilt.message)
        self.assertTrue(rebuilt.data.event_stream)
        first_rebuilt_event = rebuilt.data.event_stream[0]
        self.assertIsInstance(first_rebuilt_event, UnifiedEvent)
        self.assertTrue(first_rebuilt_event.event_uid)
        self.assertEqual(first_rebuilt_event.ref_key, first_rebuilt_event.event_uid)

    def test_thin_rebuild_can_release_source_events_after_consumption(self) -> None:
        trace_path = write_scenario(self.root / "thin-rebuild-release.trace", name="basic")
        verified = prs_Verify(trace_path, materialize_events=False)
        self.assertTrue(verified.ok, verified.message)
        summary = verified.data.get("summary")
        dataset_id = getattr(summary, "dataset_id", None) or "stream"

        aligned = align_events(
            verified.data["events"],
            dataset_id=dataset_id,
            existing_windows=verified.data["untrusted_windows"],
        )
        self.assertTrue(aligned.ok, aligned.message)
        thin_events = aligned.data["events"]
        rebuilt = rb_Rebuild(
            thin_events,
            dataset_id=dataset_id,
            header=verified.data["header"],
            untrusted_windows=aligned.data["untrusted_windows"],
            capability_flags=aligned.data["capability_flags"],
            materialize_event_stream=False,
            release_source_events=True,
        )
        self.assertTrue(rebuilt.ok, rebuilt.message)
        self.assertEqual(thin_events, [])
        self.assertEqual(rebuilt.data.event_stream, [])
        self.assertGreater(len(rebuilt.data.task_states), 0)
        self.assertGreater(len(rebuilt.data.exec_slices), 0)

    def test_load_dataset_with_timings_emits_stage_observer_updates(self) -> None:
        trace_path = write_scenario(self.root / "timings-observer.trace", name="basic")
        events: list[tuple[str, str]] = []

        def observer(stage: str, payload: dict[str, object]) -> None:
            events.append((stage, str(payload.get("status"))))

        loaded = load_dataset_with_timings(trace_path, stage_observer=observer)
        self.assertTrue(loaded.ok, loaded.message)
        self.assertEqual(events[0], ("prs_Verify", "start"))
        self.assertIn(("prs_Load", "start"), events)
        self.assertIn(("prs_Load", "completed"), events)
        self.assertIn(("prs_FeedChunk", "progress"), events)
        self.assertIn(("TraceDecodeSession.feed", "progress"), events)
        self.assertIn(("_decode_chunk", "progress"), events)
        self.assertIn(("prs_Verify", "completed"), events)
        self.assertEqual(events[-1], ("idx_Build", "completed"))

    def test_decode_payload_accepts_memoryview_for_struct_payloads(self) -> None:
        import parser.codec as parser_codec

        event_id = event_id_for("TASK_READY")
        payload_bytes = parser_codec._encode_payload(
            event_id,
            {"task_id": 7, "prio": 3, "core_hint": 0, "reason": 1},
        )
        decoded = parser_codec._decode_payload(event_id, memoryview(payload_bytes))
        self.assertEqual(
            decoded,
            {"task_id": 7, "prio": 3, "core_hint": 0, "reason": 1},
        )

    def test_feed_passes_chunk_payload_as_memoryview_to_decode_chunk(self) -> None:
        import parser.codec as parser_codec

        trace_path = write_scenario(self.root / "feed-memoryview.trace", name="basic")
        original_decode_chunk = parser_codec._decode_chunk
        observed_chunk_payload_is_memoryview: list[bool] = []

        def recording_decode_chunk(*args, **kwargs):
            observed_chunk_payload_is_memoryview.append(isinstance(kwargs.get("chunk_payload"), memoryview))
            return original_decode_chunk(*args, **kwargs)

        with patch("parser.codec._decode_chunk", new=recording_decode_chunk):
            decoded = decode_trace(trace_path)

        self.assertTrue(decoded.ok, decoded.message)
        self.assertTrue(observed_chunk_payload_is_memoryview)
        self.assertTrue(all(observed_chunk_payload_is_memoryview))

    def test_event_uid_and_ref_key_share_identity(self) -> None:
        trace_path = write_scenario(self.root / "event-identity.trace", name="basic")
        decoded = decode_trace(trace_path)
        self.assertTrue(decoded.ok, decoded.message)
        first_event = decoded.data["events"][0]
        self.assertIs(first_event.event_uid, first_event.ref_key)
        self.assertFalse(hasattr(first_event, "__dict__"))

    def test_empty_trust_tags_use_shared_object_until_event_becomes_untrusted(self) -> None:
        import parser.codec as parser_codec

        trace_path = write_scenario(self.root / "shared-trust-tags.trace", name="basic")
        decoded = decode_trace(trace_path)
        self.assertTrue(decoded.ok, decoded.message)
        self.assertTrue(decoded.data["events"])
        self.assertTrue(
            all(event.trust_tags is parser_codec.EMPTY_TRUST_TAGS for event in decoded.data["events"])
        )

    def test_decode_memoryerror_emits_failure_hotspot_summary(self) -> None:
        import parser.codec as parser_codec

        trace_path = self.root / "decode-memoryerror.trace"
        event_id = event_id_for("TASK_READY")
        # Force at least 2 chunks so the first chunk updates latest_progress_payload,
        # then inject a MemoryError on the second chunk.
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id,
                    "seq": seq,
                    "timestamp": 100 + (seq * 10),
                    "payload": {"task_id": 7, "prio": 3, "core_hint": 0, "reason": 1},
                }
                for seq in range(1, 17)
            ],
            chunk_size=8,
        )

        original_decode_payload = parser_codec._decode_payload
        call_count = {"n": 0}

        def injected(event_id: int, payload_bytes: bytes) -> dict[str, object]:
            call_count["n"] += 1
            if call_count["n"] > 9:
                raise MemoryError()
            return original_decode_payload(event_id, payload_bytes)

        events: list[tuple[str, dict[str, object]]] = []

        def observer(stage: str, payload: dict[str, object]) -> None:
            events.append((stage, payload))

        from unittest.mock import patch

        with patch("parser.codec._decode_payload", side_effect=injected):
            with self.assertRaises(MemoryError):
                load_dataset_with_timings(trace_path, stage_observer=observer)

        failed = [
            payload
            for stage, payload in events
            if stage == "_decode_chunk" and payload.get("status") == "failed"
        ]
        self.assertTrue(failed, "expected _decode_chunk failed stage update")
        hotspot = failed[-1].get("hotspot_summary")
        self.assertIsInstance(hotspot, dict)
        self.assertEqual(hotspot.get("failure"), "MemoryError")
        self.assertIn("partial_chunk", hotspot)
        self.assertIn("last_progress", hotspot)
        self.assertIsInstance(hotspot.get("last_progress"), dict)
        # The first chunk should have updated progress before the injected failure.
        self.assertGreater(int(hotspot["last_progress"].get("decoded_chunk_count", 0)), 0)

    def test_empty_payload_uses_shared_object_and_rebuilds(self) -> None:
        trace_path = self.root / "empty-payload.trace"
        self._write_empty_payload_trace(trace_path)
        decoded = decode_trace(trace_path)
        self.assertTrue(decoded.ok, decoded.message)
        event = decoded.data["events"][0]
        self.assertEqual(event.payload, {})
        import parser.codec as parser_codec

        self.assertIs(event.payload, parser_codec.EMPTY_PAYLOAD)
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)

    def test_repeated_small_payloads_share_identity(self) -> None:
        trace_path = self.root / "shared-payload.trace"
        event_id = event_id_for("TASK_READY")
        payload = {"task_id": 7, "prio": 3, "core_hint": 0, "reason": 1}
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id,
                    "seq": 1,
                    "timestamp": 100,
                    "payload": payload,
                },
                {
                    "core_id": 0,
                    "event_id": event_id,
                    "seq": 2,
                    "timestamp": 120,
                    "payload": payload,
                },
            ],
        )
        decoded = decode_trace(trace_path)
        self.assertTrue(decoded.ok, decoded.message)
        first_event, second_event = decoded.data["events"]
        self.assertIs(first_event.payload, second_event.payload)

    def test_ready_wait_and_response_metrics_are_reported(self) -> None:
        trace_path = write_scenario(self.root / "metrics.trace", name="basic")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        metrics = metric_Compute(
            record.metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(metrics.ok, metrics.message)
        metric_map = {item.metric_id: item for item in metrics.data}
        self.assertIn("ready_wait_time", metric_map)
        self.assertIn("response_time", metric_map)
        self.assertGreater(metric_map["ready_wait_time"].summary["total_ready_wait_time"], 0.0)
        self.assertGreater(metric_map["response_time"].summary["avg_response_time"], 0.0)
        self.assertTrue(metric_map["ready_wait_time"].distribution)
        self.assertTrue(metric_map["response_time"].distribution)
        self.assertIn("p95", metric_map["response_time"].summary)

    def test_jitter_and_irq_latency_metrics_are_reported(self) -> None:
        trace_path = self.root / "diagnostic-metrics.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {"task_id": 1, "prio": 3, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 120,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("IRQ_ENTER"),
                    "seq": 3,
                    "timestamp": 130,
                    "payload": {"irq_id": 5, "core_id": 0, "nesting_depth": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("IRQ_EXIT"),
                    "seq": 4,
                    "timestamp": 190,
                    "payload": {"irq_id": 5, "core_id": 0, "nesting_depth": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 5,
                    "timestamp": 220,
                    "payload": {"core_id": 0, "prev_task_id": 1, "next_task_id": 0, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 6,
                    "timestamp": 300,
                    "payload": {"task_id": 1, "prio": 3, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 7,
                    "timestamp": 360,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 8,
                    "timestamp": 420,
                    "payload": {"core_id": 0, "prev_task_id": 1, "next_task_id": 0, "reason": 2},
                },
            ],
        )
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        metrics = metric_Compute(
            record.metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(metrics.ok, metrics.message)
        metric_map = {item.metric_id: item for item in metrics.data}
        self.assertIn("response_jitter", metric_map)
        self.assertIn("irq_latency", metric_map)
        self.assertGreater(metric_map["response_jitter"].summary["max_jitter"], 0.0)
        self.assertGreater(metric_map["irq_latency"].summary["total_irq_latency"], 0.0)
        self.assertTrue(metric_map["response_jitter"].distribution)
        self.assertTrue(metric_map["irq_latency"].distribution)
        self.assertIn("p99", metric_map["irq_latency"].summary)

    def test_index_query_and_event_paging(self) -> None:
        trace_path = write_scenario(self.root / "basic.trace", name="basic")
        dataset = load_dataset(trace_path)
        page = query_events(dataset.data.bundle, {}, limit=3)
        self.assertEqual(len(page.items), 3)
        self.assertEqual(page.order_by, "sort_key asc")
        self.assertIsNone(page.cursor_in)
        self.assertTrue(page.has_more)
        self.assertGreater(page.total_hint, len(page.items))
        self.assertIsNotNone(page.next_cursor)
        next_page = query_events(dataset.data.bundle, {}, cursor=page.next_cursor, limit=3)
        self.assertGreater(len(next_page.items), 0)
        self.assertEqual(next_page.cursor_in, page.next_cursor)
        self.assertNotEqual(page.items[0].event_uid, next_page.items[0].event_uid)
        self.assertGreater(next_page.items[0].sort_key, page.next_cursor.sort_key)

    def test_event_page_matches_detailed_design_fields(self) -> None:
        from dataclasses import fields
        import parser.models as parser_models
        import spec.models as spec_models

        expected = [
            "items",
            "order_by",
            "cursor_in",
            "next_cursor",
            "prev_cursor",
            "has_more",
            "total_hint",
            "trusted",
            "summary",
        ]
        self.assertEqual([item.name for item in fields(parser_models.EventPage)], expected)
        self.assertEqual([item.name for item in fields(spec_models.EventPage)], expected)

    def test_event_table_cursor_round_trip_preserves_sort_key_order(self) -> None:
        trace_path = write_scenario(self.root / "event-page.trace", name="multi_core")
        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        first_page = query_events(dataset.data.bundle, {}, limit=4)
        self.assertGreater(len(first_page.items), 0)
        self.assertIsNotNone(first_page.next_cursor)
        second_page = query_events(dataset.data.bundle, {}, cursor=first_page.next_cursor, limit=4)
        self.assertGreater(len(second_page.items), 0)
        self.assertEqual(second_page.cursor_in, first_page.next_cursor)
        self.assertTrue(all(item.sort_key > first_page.next_cursor.sort_key for item in second_page.items))
        self.assertEqual(second_page.order_by, "sort_key asc")

    def test_query_events_from_trace_supports_forward_backward_paging(self) -> None:
        trace_path = write_scenario(self.root / "event-source-paging.trace", name="multi_core", repeat=3)
        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        time_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        first_page = query_events_from_trace(trace_path, {}, None, 4, time_window, direction="forward")
        self.assertTrue(first_page.ok, first_page.message)
        self.assertIn(first_page.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertGreater(len(first_page.data.items), 0)
        self.assertIsNotNone(first_page.data.next_cursor)

        second_page = query_events_from_trace(
            trace_path,
            {},
            first_page.data.next_cursor,
            4,
            time_window,
            direction="forward",
        )
        self.assertTrue(second_page.ok, second_page.message)
        self.assertIn(second_page.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertEqual(second_page.data.cursor_in, first_page.data.next_cursor)
        self.assertTrue(all(item.sort_key > first_page.data.next_cursor.sort_key for item in second_page.data.items))
        self.assertTrue(
            all(
                earlier.sort_key < later.sort_key
                for earlier, later in zip(second_page.data.items, second_page.data.items[1:])
            )
        )
        self.assertIsNotNone(second_page.data.prev_cursor)

        backward_page = query_events_from_trace(
            trace_path,
            {},
            second_page.data.prev_cursor,
            4,
            time_window,
            direction="backward",
        )
        self.assertTrue(backward_page.ok, backward_page.message)
        self.assertIn(backward_page.data.summary["source"], {"trace_window_scan", "package_index"})
        self.assertEqual(backward_page.data.cursor_in, second_page.data.prev_cursor)
        self.assertEqual(
            [item.event_uid for item in backward_page.data.items],
            [item.event_uid for item in first_page.data.items],
        )

    def test_query_events_from_trace_forward_page_stops_before_large_zero_filler_chunk(self) -> None:
        import parser.index as parser_index

        trace_path = write_scenario(self.root / "event-source-filler.trace", name="basic", repeat=4)
        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        time_window = (
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
        )
        self._append_zero_filler_chunk(trace_path, payload_bytes=1024 * 1024)

        original_feed = parser_index.TraceDecodeSession.feed

        def guarded_feed(self, payload: bytes, source_core_id=None):
            if len(payload) == CHUNK_HEADER_STRUCT.size + (1024 * 1024):
                header = CHUNK_HEADER_STRUCT.unpack_from(payload, 0)
                if int(header[3]) == 0 and int(header[4]) == 1024 * 1024:
                    raise MemoryError("filler chunk should not be decoded for forward first page")
            return original_feed(self, payload, source_core_id=source_core_id)

        with patch("parser.index.TraceDecodeSession.feed", new=guarded_feed):
            page = query_events_from_trace(trace_path, {}, None, 4, time_window, direction="forward")

        self.assertTrue(page.ok, page.message)
        self.assertEqual(page.data.summary["source"], "trace_window_scan")
        self.assertGreater(len(page.data.items), 0)

    def test_query_events_from_trace_respects_time_window(self) -> None:
        trace_path = write_scenario(self.root / "trace-window-scan.trace", name="basic")
        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        time_window = (
            bundle.event_stream[1].timestamp_aligned,
            bundle.event_stream[3].timestamp_aligned,
        )
        page = query_events_from_trace(trace_path, {}, None, 8, time_window)
        self.assertTrue(page.ok, page.message)
        self.assertEqual(page.data.summary["source"], "trace_window_scan")
        self.assertGreater(len(page.data.items), 0)
        self.assertTrue(all(time_window[0] <= item.timestamp_aligned <= time_window[1] for item in page.data.items))

    def test_untrusted_windows_propagate_to_metrics_and_exports(self) -> None:
        trace_path = write_scenario(self.root / "gap.trace", name="gap")
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        self.assertGreater(len(bundle.untrusted_windows), 0)

        metrics = metric_Compute(
            record.metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(metrics.ok, metrics.message)
        self.assertGreater(len(metrics.untrusted_windows), 0)

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full({"dataset_id": loaded.data})
        package_dir = self.root / "package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        self.assertTrue((package_dir / "manifest.json").exists())
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertGreater(len(manifest["entries"]), 0)

    def test_stable_sort_fallback_without_alignment(self) -> None:
        trace_path = write_scenario(self.root / "basic.trace", name="basic")
        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        self.assertFalse(bundle.capability_flags["align_calibrated"])
        self.assertIsNotNone(bundle.alignment)
        self.assertFalse(bundle.alignment.calibrated)
        self.assertTrue(any(window.reason_code == "ALIGN_DEGRADED" for window in bundle.untrusted_windows))

    def test_alignment_uses_calibration_anchors_when_present(self) -> None:
        trace_path = write_scenario(self.root / "aligned.trace", name="multi_core")
        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        self.assertTrue(bundle.capability_flags["align_anchor_seen"])
        self.assertTrue(bundle.capability_flags["align_calibrated"])
        self.assertIsNotNone(bundle.alignment)
        self.assertTrue(bundle.alignment.anchors_seen)
        self.assertTrue(bundle.alignment.calibrated)
        self.assertTrue(any(window.reason_code == "ALIGN_DEGRADED" for window in bundle.untrusted_windows))
        self.assertTrue(bundle.alignment.segments)
        self.assertCountEqual(
            [segment.core_id for segment in bundle.alignment.segments],
            bundle.alignment.core_ids,
        )

        by_name = {event.event_name: event for event in bundle.event_stream if event.event_name in {"TASK_WAKEUP", "CTX_SWITCH", "SYNC_CALIB", "TS_CALIB"}}
        self.assertEqual(by_name["SYNC_CALIB"].timestamp_aligned, by_name["TS_CALIB"].timestamp_aligned)
        self.assertEqual(by_name["TASK_WAKEUP"].timestamp_aligned, 1380.0)
        self.assertEqual(by_name["CTX_SWITCH"].timestamp_aligned, 1400.0)

    def test_alignment_applies_only_inside_anchor_coverage_segment(self) -> None:
        aligned = align_events(
            [
                DecodedEvent(
                    core_id=0,
                    seq=1,
                    timestamp_raw=100.0,
                    timestamp_aligned=100.0,
                    event_id=event_id_for("TASK_READY"),
                    event_name="TASK_READY",
                    task_id=1,
                    obj_id=None,
                    irq_id=None,
                    job_id=None,
                    instance_id=None,
                    payload={"task_id": 1},
                    trust_tags=[],
                    chunk_id=0,
                ),
                DecodedEvent(
                    core_id=1,
                    seq=1,
                    timestamp_raw=120.0,
                    timestamp_aligned=120.0,
                    event_id=event_id_for("TASK_READY"),
                    event_name="TASK_READY",
                    task_id=2,
                    obj_id=None,
                    irq_id=None,
                    job_id=None,
                    instance_id=None,
                    payload={"task_id": 2},
                    trust_tags=[],
                    chunk_id=0,
                ),
                DecodedEvent(
                    core_id=0,
                    seq=2,
                    timestamp_raw=500.0,
                    timestamp_aligned=500.0,
                    event_id=event_id_for("SYNC_CALIB"),
                    event_name="SYNC_CALIB",
                    task_id=None,
                    obj_id=None,
                    irq_id=None,
                    job_id=None,
                    instance_id=None,
                    payload={"anchor_id": 1, "core_id": 0, "ref_ts": 500},
                    trust_tags=[],
                    chunk_id=0,
                ),
                DecodedEvent(
                    core_id=1,
                    seq=2,
                    timestamp_raw=600.0,
                    timestamp_aligned=600.0,
                    event_id=event_id_for("TS_CALIB"),
                    event_name="TS_CALIB",
                    task_id=None,
                    obj_id=None,
                    irq_id=None,
                    job_id=None,
                    instance_id=None,
                    payload={"anchor_id": 1, "src_core": 1, "dst_core": 0, "raw_ts": 600},
                    trust_tags=[],
                    chunk_id=0,
                ),
                DecodedEvent(
                    core_id=1,
                    seq=3,
                    timestamp_raw=800.0,
                    timestamp_aligned=800.0,
                    event_id=event_id_for("TASK_WAKEUP"),
                    event_name="TASK_WAKEUP",
                    task_id=2,
                    obj_id=0x10,
                    irq_id=None,
                    job_id=None,
                    instance_id=None,
                    payload={"task_id": 2},
                    trust_tags=[],
                    chunk_id=0,
                ),
            ],
            dataset_id="segment-align",
            existing_windows=[],
        )
        self.assertTrue(aligned.ok, aligned.message)
        events = {(event.event_name, event.core_id, event.seq): event for event in aligned.data["events"]}
        # pre-anchor event on core 1 should remain unchanged
        self.assertEqual(events[("TASK_READY", 1, 1)].timestamp_aligned, 120.0)
        # post-anchor event on core 1 should be corrected by offset (-100)
        self.assertEqual(events[("TASK_WAKEUP", 1, 3)].timestamp_aligned, 700.0)
        self.assertEqual(events[("SYNC_CALIB", 0, 2)].timestamp_aligned, 500.0)
        self.assertEqual(events[("TS_CALIB", 1, 2)].timestamp_aligned, 500.0)
        self.assertTrue(any(window.reason_code == "ALIGN_DEGRADED" for window in aligned.data["untrusted_windows"]))

    def test_align_events_matches_sorted_and_unsorted_inputs_for_same_event_set(self) -> None:
        sorted_input = self._alignment_regression_events()
        unsorted_input = [
            self._alignment_regression_events()[5],
            self._alignment_regression_events()[2],
            self._alignment_regression_events()[0],
            self._alignment_regression_events()[4],
            self._alignment_regression_events()[1],
            self._alignment_regression_events()[3],
        ]

        sorted_result = align_events(
            sorted_input,
            dataset_id="align-regression",
            existing_windows=[],
        )
        unsorted_result = align_events(
            unsorted_input,
            dataset_id="align-regression",
            existing_windows=[],
        )

        self.assertTrue(sorted_result.ok, sorted_result.message)
        self.assertTrue(unsorted_result.ok, unsorted_result.message)
        self.assertEqual(
            [dataclass_to_dict(event) for event in unsorted_result.data["events"]],
            [dataclass_to_dict(event) for event in sorted_result.data["events"]],
        )
        self.assertEqual(
            dataclass_to_dict(unsorted_result.data["alignment"]),
            dataclass_to_dict(sorted_result.data["alignment"]),
        )
        self.assertEqual(
            [dataclass_to_dict(window) for window in unsorted_result.data["untrusted_windows"]],
            [dataclass_to_dict(window) for window in sorted_result.data["untrusted_windows"]],
        )
        self.assertEqual(
            [(event.event_name, event.core_id, event.seq) for event in sorted_result.data["events"]],
            [
                ("TASK_READY", 0, 1),
                ("TASK_READY", 1, 1),
                ("SYNC_CALIB", 0, 2),
                ("TS_CALIB", 1, 2),
                ("TASK_WAKEUP", 1, 3),
                ("CTX_SWITCH", 0, 3),
            ],
        )
        self.assertEqual(
            [event.timestamp_aligned for event in sorted_result.data["events"]],
            [100.0, 120.0, 500.0, 500.0, 550.0, 580.0],
        )

    def test_single_core_alignment_remains_zero_adjustment(self) -> None:
        aligned = align_events(
            [
                DecodedEvent(
                    core_id=0,
                    seq=1,
                    timestamp_raw=100.0,
                    timestamp_aligned=100.0,
                    event_id=event_id_for("TASK_READY"),
                    event_name="TASK_READY",
                    task_id=1,
                    obj_id=None,
                    irq_id=None,
                    job_id=None,
                    instance_id=None,
                    payload={"task_id": 1},
                    trust_tags=[],
                    chunk_id=0,
                ),
                DecodedEvent(
                    core_id=0,
                    seq=2,
                    timestamp_raw=200.0,
                    timestamp_aligned=200.0,
                    event_id=event_id_for("SYNC_CALIB"),
                    event_name="SYNC_CALIB",
                    task_id=None,
                    obj_id=None,
                    irq_id=None,
                    job_id=None,
                    instance_id=None,
                    payload={"anchor_id": 1, "core_id": 0, "ref_ts": 200},
                    trust_tags=[],
                    chunk_id=0,
                ),
            ],
            dataset_id="single-core",
            existing_windows=[],
        )
        self.assertTrue(aligned.ok, aligned.message)
        self.assertEqual(
            [item.timestamp_aligned for item in aligned.data["events"]],
            [100.0, 200.0],
        )
        self.assertFalse(any(window.reason_code == "ALIGN_DEGRADED" for window in aligned.data["untrusted_windows"]))

    def test_unknown_event_payload_is_preserved(self) -> None:
        trace_path = self.root / "unknown.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": 0x9001,
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {"foo": "bar", "value": 3},
                }
            ],
        )
        decoded = decode_trace(trace_path)
        self.assertTrue(decoded.ok, decoded.message)
        payload = decoded.data["events"][0].payload
        self.assertEqual(payload["foo"], "bar")
        self.assertEqual(payload["value"], 3)

    def test_integrity_events_create_untrusted_windows(self) -> None:
        trace_path = self.root / "integrity.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {"task_id": 7, "prio": 3, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("LOSS"),
                    "seq": 2,
                    "timestamp": 110,
                    "payload": {"core_id": 0, "lost_count": 2, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("OVERFLOW"),
                    "seq": 3,
                    "timestamp": 120,
                    "payload": {"core_id": 0, "overflow_count": 1, "reason": 1},
                },
            ],
        )

        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        reasons = {window.reason_code for window in bundle.untrusted_windows}
        self.assertIn("SEQ_GAP", reasons)
        self.assertIn("BUFFER_OVERFLOW", reasons)

        loss_event = next(item for item in bundle.event_stream if item.event_name == "LOSS")
        overflow_event = next(item for item in bundle.event_stream if item.event_name == "OVERFLOW")
        ready_event = next(item for item in bundle.event_stream if item.event_name == "TASK_READY")
        import parser.codec as parser_codec

        self.assertIs(ready_event.trust_tags, parser_codec.EMPTY_TRUST_TAGS)
        self.assertIsNot(loss_event.trust_tags, parser_codec.EMPTY_TRUST_TAGS)
        self.assertIsNot(overflow_event.trust_tags, parser_codec.EMPTY_TRUST_TAGS)
        self.assertIn("loss", loss_event.trust_tags)
        self.assertIn("overflow", overflow_event.trust_tags)

    def test_crc_failure_marks_untrusted_window(self) -> None:
        trace_path = write_scenario(self.root / "crc.trace", name="basic")
        raw = bytearray(trace_path.read_bytes())
        payload_offset = GLOBAL_HEADER_STRUCT.size + CHUNK_HEADER_STRUCT.size + EVENT_HEADER_STRUCT.size
        raw[payload_offset] ^= 0x01
        trace_path.write_bytes(raw)

        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        self.assertIn("CRC_FAIL", {window.reason_code for window in bundle.untrusted_windows})
        self.assertTrue(any("crc_fail" in event.trust_tags for event in bundle.event_stream))
        import parser.codec as parser_codec

        tagged_events = [event for event in bundle.event_stream if "crc_fail" in event.trust_tags]
        self.assertTrue(tagged_events)
        self.assertTrue(all(event.trust_tags is not parser_codec.EMPTY_TRUST_TAGS for event in tagged_events))

    def test_chunk_dict_mismatch_marks_untrusted_window(self) -> None:
        trace_path = write_scenario(self.root / "dict-mismatch.trace", name="basic")
        raw = bytearray(trace_path.read_bytes())
        chunk_offset = GLOBAL_HEADER_STRUCT.size
        chunk_header = list(CHUNK_HEADER_STRUCT.unpack_from(raw, chunk_offset))
        chunk_header[9] += 1
        raw[chunk_offset : chunk_offset + CHUNK_HEADER_STRUCT.size] = CHUNK_HEADER_STRUCT.pack(*chunk_header)
        trace_path.write_bytes(raw)

        dataset = load_dataset(trace_path)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        self.assertEqual(bundle.header.dict_ver, 1)
        self.assertIn("DICT_MISMATCH", {window.reason_code for window in bundle.untrusted_windows})
        self.assertTrue(any("dict mismatch" in warning for warning in dataset.warnings))

    def test_priority_inversion_and_irq_pressure_alerts_are_generated(self) -> None:
        trace_path = self.root / "diagnostic-alerts.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {"task_id": 1, "prio": 9, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 110,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("SYNC_LOCK"),
                    "seq": 3,
                    "timestamp": 120,
                    "payload": {"task_id": 1, "obj_id": 0x2A, "obj_type": 1, "timeout_ns": 0},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 4,
                    "timestamp": 130,
                    "payload": {"task_id": 2, "prio": 3, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_BLOCK"),
                    "seq": 5,
                    "timestamp": 140,
                    "payload": {"task_id": 2, "wait_obj_id": 0x2A, "reason": 3, "owner_task_id": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 6,
                    "timestamp": 150,
                    "payload": {"task_id": 3, "prio": 5, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 7,
                    "timestamp": 160,
                    "payload": {"core_id": 0, "prev_task_id": 1, "next_task_id": 3, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("IRQ_ENTER"),
                    "seq": 8,
                    "timestamp": 170,
                    "payload": {"irq_id": 7, "core_id": 0, "nesting_depth": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("IRQ_EXIT"),
                    "seq": 9,
                    "timestamp": 260,
                    "payload": {"irq_id": 7, "core_id": 0, "nesting_depth": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 10,
                    "timestamp": 280,
                    "payload": {"core_id": 0, "prev_task_id": 3, "next_task_id": 1, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("SYNC_UNLOCK"),
                    "seq": 11,
                    "timestamp": 290,
                    "payload": {"task_id": 1, "obj_id": 0x2A, "obj_type": 1, "timeout_ns": 0},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_WAKEUP"),
                    "seq": 12,
                    "timestamp": 300,
                    "payload": {"task_id": 2, "wake_src": 1, "obj_id": 0x2A},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 13,
                    "timestamp": 320,
                    "payload": {"core_id": 0, "prev_task_id": 1, "next_task_id": 2, "reason": 1},
                },
            ],
        )
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        self.assertTrue(loaded.ok, loaded.message)
        record = controller.repository.get(loaded.data)
        bundle = record.artifact.bundle
        alerts = alert_Evaluate(
            record.metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(alerts.ok, alerts.message)
        alert_types = {item.type for item in alerts.data}
        self.assertIn("priority_inversion", alert_types)
        self.assertIn("irq_pressure", alert_types)

        diagnoses = diag_Generate(
            record.metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            alerts.data,
        )
        self.assertTrue(diagnoses.ok, diagnoses.message)
        diagnosis_types = {item.diagnosis_type for item in diagnoses.data}
        self.assertIn("priority_inversion", diagnosis_types)
        self.assertIn("irq_pressure", diagnosis_types)

    def test_missing_dictionary_falls_back_to_builtin_catalog(self) -> None:
        trace_path = write_scenario(self.root / "dict-missing.trace", name="basic")
        baseline = load_dataset(trace_path)
        self.assertTrue(baseline.ok, baseline.message)

        with patch("spec.events.load_dictionary", side_effect=SpecError("dictionary asset missing", code="ASSET_MISSING")):
            dataset = load_dataset(trace_path)

        self.assertTrue(dataset.ok, dataset.message)
        baseline_first_event = baseline.data.bundle.event_stream[0]
        bundle = dataset.data.bundle
        reasons = {window.reason_code for window in bundle.untrusted_windows}
        self.assertIn("DICT_DEFAULT_ASSET_MISSING", reasons)
        self.assertIn("DICT_BUILTIN_FALLBACK", reasons)
        self.assertTrue(any("falling back to built-in catalog" in warning for warning in dataset.warnings))
        self.assertEqual(dataset.data.dictionary_info["resolved_source"], "builtin")
        self.assertTrue(dataset.data.dictionary_info["fallback_used"])
        first_event = bundle.event_stream[0]
        self.assertEqual(first_event.event_name, baseline_first_event.event_name)
        self.assertEqual(first_event.task_id, baseline_first_event.task_id)
        self.assertEqual(first_event.payload["task_id"], baseline_first_event.payload["task_id"])
        self.assertEqual(first_event.seq, 1)

    def test_external_dictionary_dict_input_is_used_for_event_catalog(self) -> None:
        trace_path = write_scenario(self.root / "dict-explicit.trace", name="basic")
        custom_dictionary = self._rename_event(self._dictionary_copy(), 0x1001, "TASK_READY_EXTERNAL_DICT")

        dataset = load_dataset(trace_path, dictionary=custom_dictionary)

        self.assertTrue(dataset.ok, dataset.message)
        ready_events = [item for item in dataset.data.bundle.event_stream if item.event_id == 0x1001]
        self.assertGreater(len(ready_events), 0)
        self.assertTrue(all(item.event_name == "TASK_READY_EXTERNAL_DICT" for item in ready_events))
        self.assertFalse(dataset.data.dictionary_info["fallback_used"])
        self.assertEqual(dataset.data.dictionary_info["requested_source"], "external_dict")
        self.assertEqual(dataset.data.dictionary_info["resolved_source"], "external")

    def test_external_dictionary_path_input_is_used_for_event_catalog(self) -> None:
        trace_path = write_scenario(self.root / "dict-explicit-path.trace", name="basic")
        custom_dictionary = self._rename_event(self._dictionary_copy(), 0x1001, "TASK_READY_EXTERNAL_PATH")
        dictionary_path = self.root / "external_dictionary.json"
        dictionary_path.write_text(json.dumps(custom_dictionary), encoding="utf-8")

        dataset = load_dataset(trace_path, dictionary=dictionary_path)

        self.assertTrue(dataset.ok, dataset.message)
        ready_events = [item for item in dataset.data.bundle.event_stream if item.event_id == 0x1001]
        self.assertGreater(len(ready_events), 0)
        self.assertTrue(all(item.event_name == "TASK_READY_EXTERNAL_PATH" for item in ready_events))
        self.assertEqual(dataset.data.dictionary_info["requested_source"], "external_path")
        self.assertEqual(dataset.data.dictionary_info["resolved_source"], "external")

    def test_external_dictionary_version_mismatch_marks_untrusted_window(self) -> None:
        trace_path = write_scenario(self.root / "dict-version-mismatch.trace", name="basic")
        mismatched_dictionary = self._dictionary_copy()
        mismatched_dictionary["dict_ver"] = 99
        dictionary_path = self.root / "mismatched_dictionary.json"
        dictionary_path.write_text(json.dumps(mismatched_dictionary), encoding="utf-8")

        dataset = load_dataset(trace_path, dictionary=dictionary_path)

        self.assertTrue(dataset.ok, dataset.message)
        self.assertTrue(any("version mismatch" in warning for warning in dataset.warnings))
        self.assertIn("DICT_MISMATCH", {window.reason_code for window in dataset.data.bundle.untrusted_windows})
        self.assertTrue(dataset.data.dictionary_info["version_mismatch"])

    def test_external_dictionary_missing_path_falls_back_to_default_catalog(self) -> None:
        trace_path = write_scenario(self.root / "dict-missing-path.trace", name="basic")

        dataset = load_dataset(trace_path, dictionary=self.root / "does-not-exist.json")

        self.assertTrue(dataset.ok, dataset.message)
        self.assertTrue(any("DICT_EXTERNAL_PATH_MISSING" in warning for warning in dataset.warnings))
        self.assertIn("DICT_EXTERNAL_PATH_MISSING", {window.reason_code for window in dataset.data.bundle.untrusted_windows})
        self.assertEqual(dataset.data.dictionary_info["resolved_source"], "default")
        ready_events = [item for item in dataset.data.bundle.event_stream if item.event_id == 0x1001]
        self.assertGreater(len(ready_events), 0)
        self.assertTrue(all(item.event_name == "TASK_READY" for item in ready_events))

    def test_external_dictionary_invalid_json_falls_back_to_default_catalog(self) -> None:
        trace_path = write_scenario(self.root / "dict-invalid-json.trace", name="basic")
        dictionary_path = self.root / "invalid_dictionary.json"
        dictionary_path.write_text("{invalid json", encoding="utf-8")

        dataset = load_dataset(trace_path, dictionary=dictionary_path)

        self.assertTrue(dataset.ok, dataset.message)
        self.assertTrue(any("DICT_EXTERNAL_JSON_INVALID" in warning for warning in dataset.warnings))
        self.assertIn("DICT_EXTERNAL_JSON_INVALID", {window.reason_code for window in dataset.data.bundle.untrusted_windows})
        self.assertEqual(dataset.data.dictionary_info["resolved_source"], "default")

    def test_external_dictionary_invalid_payload_falls_back_to_default_catalog(self) -> None:
        trace_path = write_scenario(self.root / "dict-invalid-payload.trace", name="basic")
        invalid_dictionary = self._dictionary_copy()
        invalid_dictionary.pop("event_defs")
        dictionary_path = self.root / "invalid_dictionary.json"
        dictionary_path.write_text(json.dumps(invalid_dictionary), encoding="utf-8")

        dataset = load_dataset(trace_path, dictionary=dictionary_path)

        self.assertTrue(dataset.ok, dataset.message)
        self.assertTrue(any("DICT_EXTERNAL_SCHEMA_INVALID" in warning for warning in dataset.warnings))
        self.assertIn("DICT_EXTERNAL_SCHEMA_INVALID", {window.reason_code for window in dataset.data.bundle.untrusted_windows})
        self.assertEqual(dataset.data.dictionary_info["resolved_source"], "default")
        ready_events = [item for item in dataset.data.bundle.event_stream if item.event_id == 0x1001]
        self.assertGreater(len(ready_events), 0)
        self.assertTrue(all(item.event_name == "TASK_READY" for item in ready_events))

    def test_deadline_miss_metric_and_alert_require_instance_semantics(self) -> None:
        trace_path = self.root / "deadline-miss.trace"
        custom_dictionary = self._dictionary_copy()
        self._append_event_definition(
            custom_dictionary,
            0x9001,
            "TASK_READY",
            ["task_id", "prio", "core_hint", "reason", "job_id", "instance_id", "release_ts", "deadline_ts"],
        )
        self._append_event_definition(
            custom_dictionary,
            0x9002,
            "CTX_SWITCH",
            ["core_id", "prev_task_id", "next_task_id", "reason", "job_id", "instance_id"],
        )
        self._append_event_definition(
            custom_dictionary,
            0x9003,
            "TASK_EXIT",
            ["task_id", "exit_code", "job_id", "instance_id", "finish_ts"],
        )
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": 0x9001,
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {
                        "task_id": 1,
                        "prio": 3,
                        "core_hint": 0,
                        "reason": 1,
                        "job_id": 7,
                        "instance_id": 1,
                        "release_ts": 100,
                        "deadline_ts": 140,
                    },
                },
                {
                    "core_id": 0,
                    "event_id": 0x9002,
                    "seq": 2,
                    "timestamp": 110,
                    "payload": {
                        "core_id": 0,
                        "prev_task_id": 0,
                        "next_task_id": 1,
                        "reason": 1,
                        "job_id": 7,
                        "instance_id": 1,
                    },
                },
                {
                    "core_id": 0,
                    "event_id": 0x9003,
                    "seq": 3,
                    "timestamp": 160,
                    "payload": {
                        "task_id": 1,
                        "exit_code": 0,
                        "job_id": 7,
                        "instance_id": 1,
                        "finish_ts": 160,
                    },
                },
                {
                    "core_id": 0,
                    "event_id": 0x9002,
                    "seq": 4,
                    "timestamp": 170,
                    "payload": {
                        "core_id": 0,
                        "prev_task_id": 1,
                        "next_task_id": 0,
                        "reason": 2,
                        "job_id": 7,
                        "instance_id": 1,
                    },
                },
            ],
        )

        dataset = load_dataset(trace_path, dictionary=custom_dictionary)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        self.assertTrue(bundle.capability_flags["job_semantics"])
        self.assertTrue(bundle.capability_flags["instance_semantics"])
        self.assertTrue(bundle.capability_flags["deadline_semantics"])
        metric_session = metric_Init().data
        ingested = metric_Ingest(metric_session, bundle)
        self.assertTrue(ingested.ok, ingested.message)

        metrics = metric_Compute(
            metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(metrics.ok, metrics.message)
        metric_map = {item.metric_id: item for item in metrics.data}
        self.assertIn("deadline_miss", metric_map)
        self.assertEqual(metric_map["deadline_miss"].summary["record_count"], 1)
        self.assertEqual(metric_map["deadline_miss"].summary["miss_count"], 1)
        self.assertEqual(metric_map["deadline_miss"].summary["semantics_status"], "precise")
        self.assertEqual(metric_map["deadline_miss"].summary["max_deadline_miss"], 20.0)

        alerts = alert_Evaluate(
            metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(alerts.ok, alerts.message)
        alert_map = {item.type: item for item in alerts.data}
        self.assertIn("deadline_miss", alert_map)
        self.assertEqual(alert_map["deadline_miss"].support_level, "exact")

        diagnoses = diag_Generate(
            metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            alerts.data,
        )
        self.assertTrue(diagnoses.ok, diagnoses.message)
        diagnosis_map = {item.diagnosis_type: item for item in diagnoses.data}
        self.assertIn("deadline_miss", diagnosis_map)
        self.assertEqual(diagnosis_map["deadline_miss"].support_level, "exact")

    def test_deadline_semantics_gap_alert_covers_incomplete_and_conflict_cases(self) -> None:
        trace_path = self.root / "deadline-gap.trace"
        custom_dictionary = self._dictionary_copy()
        self._append_event_definition(
            custom_dictionary,
            0x9011,
            "TASK_READY",
            ["task_id", "prio", "core_hint", "reason", "release_ts", "deadline_ts"],
        )
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": 0x9011,
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {
                        "task_id": 2,
                        "prio": 3,
                        "core_hint": 0,
                        "reason": 1,
                        "release_ts": 100,
                        "deadline_ts": 140,
                    },
                },
                {
                    "core_id": 0,
                    "event_id": 0x9011,
                    "seq": 2,
                    "timestamp": 110,
                    "payload": {
                        "task_id": 2,
                        "prio": 3,
                        "core_hint": 0,
                        "reason": 1,
                        "release_ts": 110,
                        "deadline_ts": 150,
                    },
                },
            ],
        )

        dataset = load_dataset(trace_path, dictionary=custom_dictionary)
        self.assertTrue(dataset.ok, dataset.message)
        bundle = dataset.data.bundle
        metric_session = metric_Init().data
        ingested = metric_Ingest(metric_session, bundle)
        self.assertTrue(ingested.ok, ingested.message)

        metrics = metric_Compute(
            metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(metrics.ok, metrics.message)
        deadline_metric = {item.metric_id: item for item in metrics.data}["deadline_miss"]
        self.assertEqual(deadline_metric.summary["semantics_status"], "degraded")
        self.assertEqual(deadline_metric.summary["record_count"], 0)
        self.assertEqual(deadline_metric.summary["incomplete_count"], 1)
        self.assertEqual(deadline_metric.summary["conflict_count"], 1)
        self.assertIn("conflict", deadline_metric.summary["issue_types"])
        self.assertIn("incomplete", deadline_metric.summary["issue_types"])

        alerts = alert_Evaluate(
            metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            {},
        )
        self.assertTrue(alerts.ok, alerts.message)
        alert_map = {item.type: item for item in alerts.data}
        self.assertIn("deadline_semantics_gap", alert_map)
        self.assertEqual(alert_map["deadline_semantics_gap"].support_level, "degraded")

        diagnoses = diag_Generate(
            metric_session,
            bundle.event_stream[0].timestamp_aligned,
            bundle.event_stream[-1].timestamp_aligned,
            alerts.data,
        )
        self.assertTrue(diagnoses.ok, diagnoses.message)
        diagnosis_map = {item.diagnosis_type: item for item in diagnoses.data}
        self.assertIn("deadline_semantics_gap", diagnosis_map)
        self.assertEqual(diagnosis_map["deadline_semantics_gap"].support_level, "degraded")

    def test_sched_decision_binary_round_trip(self) -> None:
        trace_path = self.root / "sched.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": 0x1007,
                    "seq": 1,
                    "timestamp": 123,
                    "payload": {
                        "core_id": 0,
                        "selected_task_id": 17,
                        "rq_len": 3,
                        "reason": 5,
                    },
                }
            ],
        )
        decoded = decode_trace(trace_path)
        self.assertTrue(decoded.ok, decoded.message)
        event = decoded.data["events"][0]
        self.assertEqual(event.event_name, "SCHED_DECISION")
        self.assertEqual(event.task_id, 17)
        self.assertEqual(
            event.payload,
            {
                "core_id": 0,
                "selected_task_id": 17,
                "rq_len": 3,
                "reason": 5,
            },
        )

    def test_incremental_feed_matches_offline_parse(self) -> None:
        trace_path = write_scenario(self.root / "stream.trace", name="multi_core")
        offline = decode_trace(trace_path)
        self.assertTrue(offline.ok, offline.message)

        session = prs_Init({"dataset_id": None})
        self.assertTrue(session.ok, session.message)
        trace_bytes = trace_path.read_bytes()
        offsets = [7, 31, 19, 5, 64, 11, 23]
        cursor = 0
        decoded_records = 0
        while cursor < len(trace_bytes):
            step = offsets[cursor % len(offsets)]
            chunk = trace_bytes[cursor : cursor + step]
            fed = prs_FeedChunk(session.data, None, chunk)
            self.assertTrue(fed.ok, fed.message)
            decoded_records += fed.data["decoded_records"]
            cursor += step

        finalized = prs_Finalize(session.data)
        self.assertTrue(finalized.ok, finalized.message)
        self.assertEqual(decoded_records, len(offline.data["events"]))
        self.assertEqual(finalized.data["summary"].dataset_id, "stream")
        self.assertEqual(finalized.data["summary"].event_count, offline.data["summary"].event_count)
        self.assertEqual(
            [item.ref_key for item in finalized.data["events"]],
            [item.ref_key for item in offline.data["events"]],
        )
        self.assertEqual(
            [item.reason_code for item in finalized.data["untrusted_windows"]],
            [item.reason_code for item in offline.data["untrusted_windows"]],
        )

    def test_segment_chain_metadata_round_trip(self) -> None:
        events = build_scenario(name="multi_core")
        segment_dir = self.root / "segment-meta"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(
            segment_dir / "segment_000.trace",
            events[:midpoint],
            run_id="segment-meta",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )
        encode_trace(
            segment_dir / "segment_001.trace",
            events[midpoint:],
            run_id="segment-meta",
            format_ver=2,
            segment_meta={
                "segment_seq": 2,
                "prev_segment_seq": 1,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )

        loaded = prs_Load(segment_dir)
        self.assertTrue(loaded.ok, loaded.message)
        self.assertEqual(loaded.data["summary"].segment_count, 2)
        self.assertEqual([meta.segment_seq for meta in loaded.data["segment_metas"]], [1, 2])
        self.assertEqual([meta.prev_segment_seq for meta in loaded.data["segment_metas"]], [0, 1])
        self.assertEqual(
            [meta.dict_ref_checksum for meta in loaded.data["segment_metas"]],
            [0x11111111, 0x11111111],
        )

    def test_v1_trace_remains_backward_compatible(self) -> None:
        trace_path = encode_trace(self.root / "v1.trace", build_scenario(name="basic"), run_id="v1-trace")
        loaded = prs_Load(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        self.assertEqual(loaded.data["header"].format_ver, 1)
        self.assertEqual(loaded.data["segment_metas"], [])
        self.assertEqual(loaded.data["summary"].segment_count, 1)

    def test_parser_accepts_repeated_segment_headers(self) -> None:
        events = build_scenario(name="multi_core")
        segment_dir = self.root / "repeated-headers"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(
            segment_dir / "segment_000.trace",
            events[:midpoint],
            run_id="repeated-headers",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0xAAAA5555,
            },
        )
        encode_trace(
            segment_dir / "segment_001.trace",
            events[midpoint:],
            run_id="repeated-headers",
            format_ver=2,
            segment_meta={
                "segment_seq": 2,
                "prev_segment_seq": 1,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0xAAAA5555,
            },
        )

        loaded = prs_Load(segment_dir, read_size=max(1, SEGMENT_META_STRUCT.size // 3))
        self.assertTrue(loaded.ok, loaded.message)
        self.assertEqual(loaded.data["summary"].segment_count, 2)
        self.assertEqual(len(loaded.data["segment_metas"]), 2)

    def test_segment_link_break_marks_untrusted_window(self) -> None:
        events = build_scenario(name="multi_core")
        segment_dir = self.root / "segment-break"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(
            segment_dir / "segment_000.trace",
            events[:midpoint],
            run_id="segment-break",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )
        encode_trace(
            segment_dir / "segment_001.trace",
            events[midpoint:],
            run_id="segment-break",
            format_ver=2,
            segment_meta={
                "segment_seq": 2,
                "prev_segment_seq": 7,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )

        loaded = prs_Load(segment_dir)
        self.assertTrue(loaded.ok, loaded.message)
        self.assertIn("SEGMENT_CHAIN_BREAK", {window.reason_code for window in loaded.data["untrusted_windows"]})

    def test_segment_seq_gap_marks_untrusted_window(self) -> None:
        events = build_scenario(name="multi_core")
        segment_dir = self.root / "segment-seq-gap"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(
            segment_dir / "segment_000.trace",
            events[:midpoint],
            run_id="segment-seq-gap",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x12345678,
            },
        )
        encode_trace(
            segment_dir / "segment_001.trace",
            events[midpoint:],
            run_id="segment-seq-gap",
            format_ver=2,
            segment_meta={
                "segment_seq": 3,
                "prev_segment_seq": 1,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x12345678,
            },
        )

        loaded = prs_Load(segment_dir)
        self.assertTrue(loaded.ok, loaded.message)
        self.assertIn("SEGMENT_CHAIN_BREAK", {window.reason_code for window in loaded.data["untrusted_windows"]})

    def test_segment_meta_size_mismatch_is_rejected(self) -> None:
        trace_path = encode_trace(
            self.root / "segment-meta-size-invalid.trace",
            build_scenario(name="basic"),
            run_id="segment-meta-size-invalid",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )
        self._rewrite_segment_meta_size(trace_path, SEGMENT_META_STRUCT.size + 2)

        loaded = prs_Load(trace_path)
        self.assertFalse(loaded.ok)
        self.assertIn("unsupported segment meta size", loaded.message)

    def test_prescan_rejects_invalid_segment_meta_size(self) -> None:
        trace_path = encode_trace(
            self.root / "segment-meta-size-invalid-prescan.trace",
            build_scenario(name="basic"),
            run_id="segment-meta-size-invalid-prescan",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x22222222,
            },
        )
        self._rewrite_segment_meta_size(trace_path, SEGMENT_META_STRUCT.size + 4)

        prescan = prs_Prescan(trace_path)
        self.assertFalse(prescan.ok)
        self.assertIn("unsupported segment meta size", prescan.message)

    def test_segment_dict_checksum_conflict_marks_untrusted_window(self) -> None:
        events = build_scenario(name="multi_core")
        segment_dir = self.root / "segment-dict-conflict"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(
            segment_dir / "segment_000.trace",
            events[:midpoint],
            run_id="segment-dict-conflict",
            format_ver=2,
            segment_meta={
                "segment_seq": 1,
                "prev_segment_seq": 0,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x11111111,
            },
        )
        encode_trace(
            segment_dir / "segment_001.trace",
            events[midpoint:],
            run_id="segment-dict-conflict",
            format_ver=2,
            segment_meta={
                "segment_seq": 2,
                "prev_segment_seq": 1,
                "dict_ver": 1,
                "dict_ref_algo": 1,
                "dict_ref_checksum": 0x22222222,
            },
        )

        loaded = prs_Load(segment_dir)
        self.assertTrue(loaded.ok, loaded.message)
        self.assertIn("SEGMENT_DICT_CONFLICT", {window.reason_code for window in loaded.data["untrusted_windows"]})

    def test_segmented_directory_matches_single_trace(self) -> None:
        events = build_scenario(name="multi_core")
        merged_trace = encode_trace(self.root / "merged.trace", events, run_id="segmented-run")

        segment_dir = self.root / "segments"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(segment_dir / "segment_000.trace", events[:midpoint], run_id="segmented-run")
        encode_trace(segment_dir / "segment_001.trace", events[midpoint:], run_id="segmented-run")

        merged = load_dataset(merged_trace)
        segmented = load_dataset(segment_dir)
        self.assertTrue(merged.ok, merged.message)
        self.assertTrue(segmented.ok, segmented.message)
        self.assertEqual(segmented.data.bundle.dataset_id, "segmented-run")
        self.assertIsNotNone(merged.data.bundle.header)
        self.assertIsNotNone(segmented.data.bundle.header)
        self.assertEqual(merged.data.bundle.header.run_id, "segmented-run")
        self.assertEqual(segmented.data.bundle.header.run_id, "segmented-run")
        self.assertEqual(
            [item.ref_key for item in segmented.data.bundle.event_stream],
            [item.ref_key for item in merged.data.bundle.event_stream],
        )
        self.assertEqual(
            [item.reason_code for item in segmented.data.bundle.untrusted_windows],
            [item.reason_code for item in merged.data.bundle.untrusted_windows],
        )

    def test_repeated_load_is_deterministic_for_file_and_segments(self) -> None:
        trace_path = write_scenario(self.root / "deterministic.trace", name="multi_core")
        first = load_dataset(trace_path)
        second = load_dataset(trace_path)
        self.assertTrue(first.ok, first.message)
        self.assertTrue(second.ok, second.message)
        self.assertEqual(
            [item.ref_key for item in first.data.bundle.event_stream],
            [item.ref_key for item in second.data.bundle.event_stream],
        )
        self.assertEqual(
            [item.seg_id for item in first.data.bundle.task_states],
            [item.seg_id for item in second.data.bundle.task_states],
        )
        self.assertEqual(
            [item.slice_id for item in first.data.bundle.exec_slices],
            [item.slice_id for item in second.data.bundle.exec_slices],
        )

        events = build_scenario(name="multi_core")
        segment_dir = self.root / "deterministic-segments"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(segment_dir / "segment_000.trace", events[:midpoint], run_id="deterministic-segments")
        encode_trace(segment_dir / "segment_001.trace", events[midpoint:], run_id="deterministic-segments")

        segmented_first = load_dataset(segment_dir)
        segmented_second = load_dataset(segment_dir)
        self.assertTrue(segmented_first.ok, segmented_first.message)
        self.assertTrue(segmented_second.ok, segmented_second.message)
        self.assertEqual(
            [item.ref_key for item in segmented_first.data.bundle.event_stream],
            [item.ref_key for item in segmented_second.data.bundle.event_stream],
        )
        self.assertEqual(
            [item.window_id for item in segmented_first.data.bundle.untrusted_windows],
            [item.window_id for item in segmented_second.data.bundle.untrusted_windows],
        )

    def test_load_dataset_no_longer_depends_on_path_read_bytes(self) -> None:
        trace_path = write_scenario(self.root / "streaming.trace", name="multi_core")

        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("load path must stream from file handle")):
            dataset = load_dataset(trace_path)

        self.assertTrue(dataset.ok, dataset.message)
        self.assertGreater(len(dataset.data.bundle.event_stream), 0)

    def test_prs_load_segmented_directory_handles_small_stream_reads(self) -> None:
        events = build_scenario(name="multi_core")
        merged_trace = encode_trace(self.root / "stream-merged.trace", events, run_id="stream-segments")

        segment_dir = self.root / "stream-segments"
        segment_dir.mkdir()
        midpoint = len(events) // 2
        encode_trace(segment_dir / "segment_000.trace", events[:midpoint], run_id="stream-segments")
        encode_trace(segment_dir / "segment_001.trace", events[midpoint:], run_id="stream-segments")

        read_size = max(1, GLOBAL_HEADER_STRUCT.size // 4)
        merged = prs_Load(merged_trace, read_size=read_size)
        segmented = prs_Load(segment_dir, read_size=read_size)
        self.assertTrue(merged.ok, merged.message)
        self.assertTrue(segmented.ok, segmented.message)
        self.assertEqual(segmented.data["summary"].dataset_id, "stream-segments")
        self.assertEqual(
            [item.ref_key for item in segmented.data["events"]],
            [item.ref_key for item in merged.data["events"]],
        )
        self.assertEqual(
            [item.reason_code for item in segmented.data["untrusted_windows"]],
            [item.reason_code for item in merged.data["untrusted_windows"]],
        )

    def test_prs_load_rejects_non_positive_read_size(self) -> None:
        trace_path = write_scenario(self.root / "invalid-read-size.trace", name="basic")

        zero = prs_Load(trace_path, read_size=0)
        negative = prs_Load(trace_path, read_size=-1)

        self.assertFalse(zero.ok)
        self.assertEqual(zero.code, "INVALID_ARG")
        self.assertFalse(negative.ok)
        self.assertEqual(negative.code, "INVALID_ARG")


if __name__ == "__main__":
    unittest.main()

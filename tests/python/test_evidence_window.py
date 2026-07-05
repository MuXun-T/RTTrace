from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.sample_data import write_scenario
from parser import encode_trace, load_dataset
import parser.index as parser_index
from parser.codec import CHUNK_HEADER_STRUCT
from parser.evidence_models import DependencySidecarEdge
from parser.evidence_window import build_window_plan, read_window_plan
from spec.events import event_id_for


class EvidenceWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_window_plan_merges_candidate_edges_and_reads_only_target_refs(self) -> None:
        trace_path = write_scenario(self.root / "window.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        event_a = bundle.event_stream[0]
        event_b = bundle.event_stream[1]
        event_c = bundle.event_stream[2]
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in bundle.event_stream
        ]

        candidate_edges = [
            DependencySidecarEdge(
                snapshot_id="snapshot:test:window",
                trace_checksum="pending",
                src_ref=event_a.ref_key,
                dst_ref=event_b.ref_key,
                src_kind="ref",
                dst_kind="ref",
                relation_kind="ref_index_next",
                rule_family="ref_ref",
                provenance="ref_index_row",
                priority=50,
                time_hint_begin_ns=int(event_b.timestamp_aligned),
                time_hint_end_ns=int(event_c.timestamp_aligned),
                core_hint=int(event_b.core_id),
                seq_hint_begin=int(event_b.seq),
                seq_hint_end=int(event_b.seq),
                segment_hint=f"core:{int(event_b.core_id)}",
                cycle_guard_token="ref_ref:ref:ref:window",
                estimate_events=1,
                estimate_bytes=128,
                edge_hash="edge-b",
            ),
            DependencySidecarEdge(
                snapshot_id="snapshot:test:window",
                trace_checksum="pending",
                src_ref=event_b.ref_key,
                dst_ref=event_c.ref_key,
                src_kind="ref",
                dst_kind="ref",
                relation_kind="ref_index_next",
                rule_family="ref_ref",
                provenance="ref_index_row",
                priority=50,
                time_hint_begin_ns=int(event_b.timestamp_aligned) + 10,
                time_hint_end_ns=int(event_c.timestamp_aligned),
                core_hint=int(event_c.core_id),
                seq_hint_begin=int(event_c.seq),
                seq_hint_end=int(event_c.seq),
                segment_hint=f"core:{int(event_c.core_id)}",
                cycle_guard_token="ref_ref:ref:ref:window-2",
                estimate_events=1,
                estimate_bytes=128,
                edge_hash="edge-c",
            ),
        ]

        window_plan = build_window_plan(
            candidate_edges,
            [event_b.ref_key, event_c.ref_key],
            bundle.segment_metas,
            ref_index_rows,
        )

        self.assertEqual(len(window_plan["spans"]), 1)
        self.assertEqual(set(window_plan["spans"][0]["target_refs"]), {event_b.ref_key, event_c.ref_key})
        self.assertEqual(window_plan["spans"][0]["core_id"], int(event_b.core_id))
        self.assertEqual(window_plan["planned_ref_count"], 2)

        read_result = read_window_plan(trace_path, window_plan, [event_b.ref_key, event_c.ref_key])
        self.assertTrue(read_result.ok, read_result.message)
        self.assertEqual(set(read_result.data["matched_refs"]), {event_b.ref_key, event_c.ref_key})
        self.assertEqual(read_result.data["missed_refs"], [])
        self.assertGreater(read_result.data["scan_count"], 0)
        self.assertGreater(read_result.data["seek_count"], 0)
        self.assertGreaterEqual(read_result.data["window_span_total"], 0)
        self.assertEqual(read_result.data["telemetry"]["target_ref_count"], 2)
        self.assertEqual(read_result.data["telemetry"]["matched_ref_count"], 2)
        self.assertEqual(read_result.data["telemetry"]["missed_ref_count"], 0)
        self.assertEqual(read_result.data["telemetry"]["window_hit_rate"], 1.0)

    def test_window_plan_uses_event_level_fallback_and_marks_unplanned_refs(self) -> None:
        trace_path = write_scenario(self.root / "window-fallback.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        target_event = bundle.event_stream[3]
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in bundle.event_stream
        ]

        window_plan = build_window_plan([], [target_event.ref_key, "evt:missing"], bundle.segment_metas, ref_index_rows)

        self.assertEqual(window_plan["planned_seek_count"], 1)
        self.assertEqual(window_plan["spans"][0]["t_begin"], int(target_event.timestamp_aligned))
        self.assertEqual(window_plan["spans"][0]["t_end"], int(target_event.timestamp_aligned))
        self.assertEqual(window_plan["spans"][0]["seq_begin"], int(target_event.seq))
        self.assertEqual(window_plan["spans"][0]["seq_end"], int(target_event.seq))
        self.assertEqual(window_plan["unplanned_refs"], ["evt:missing"])

        read_result = read_window_plan(trace_path, window_plan, [target_event.ref_key, "evt:missing"])
        self.assertFalse(read_result.ok)
        self.assertEqual(read_result.code, "SIDECAR_MISMATCH")

    def test_read_window_plan_reads_only_selected_chunk_spans(self) -> None:
        trace_path = self.root / "window-minimal.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {"task_id": 1, "prio": 8, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 200,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_WAKEUP"),
                    "seq": 3,
                    "timestamp": 300,
                    "payload": {"task_id": 1, "wake_src": 1},
                },
            ],
            chunk_size=1,
        )
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        target_event = bundle.event_stream[1]
        source_event = bundle.event_stream[0]
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in bundle.event_stream
        ]
        window_plan = build_window_plan(
            [
                DependencySidecarEdge(
                    snapshot_id="snapshot:test:minimal",
                    trace_checksum="pending",
                    src_ref=source_event.ref_key,
                    dst_ref=target_event.ref_key,
                    src_kind="ref",
                    dst_kind="ref",
                    relation_kind="ref_index_next",
                    rule_family="ref_ref",
                    provenance="ref_index_row",
                    priority=50,
                    time_hint_begin_ns=int(target_event.timestamp_aligned),
                    time_hint_end_ns=int(target_event.timestamp_aligned),
                    core_hint=int(target_event.core_id),
                    seq_hint_begin=int(target_event.seq),
                    seq_hint_end=int(target_event.seq),
                    segment_hint=f"core:{int(target_event.core_id)}",
                    cycle_guard_token="cg:minimal",
                    estimate_events=1,
                    estimate_bytes=128,
                    edge_hash="edge:minimal",
                )
            ],
            [target_event.ref_key],
            bundle.segment_metas,
            ref_index_rows,
        )

        chunk_header_reads: list[int] = []
        original_feed = parser_index.TraceDecodeSession.feed

        def recording_feed(self, payload, source_core_id=None):
            if len(payload) == CHUNK_HEADER_STRUCT.size:
                chunk_header_reads.append(len(payload))
            return original_feed(self, payload, source_core_id=source_core_id)

        with patch("parser.index.TraceDecodeSession.feed", new=recording_feed):
            read_result = read_window_plan(trace_path, window_plan, [target_event.ref_key])

        self.assertTrue(read_result.ok, read_result.message)
        self.assertEqual(read_result.data["matched_refs"], [target_event.ref_key])
        self.assertEqual(read_result.data["missed_refs"], [])
        self.assertEqual(read_result.data["telemetry"]["selected_chunk_count"], 1)
        self.assertEqual(len(chunk_header_reads), 1)

    def test_read_window_plan_reuses_prescanned_catalog(self) -> None:
        trace_path = self.root / "window-prescanned.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_READY"),
                    "seq": 1,
                    "timestamp": 100,
                    "payload": {"task_id": 1, "prio": 8, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 200,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_WAKEUP"),
                    "seq": 3,
                    "timestamp": 300,
                    "payload": {"task_id": 1, "wake_src": 1},
                },
            ],
            chunk_size=1,
        )
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        target_event = bundle.event_stream[1]
        source_event = bundle.event_stream[0]
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in bundle.event_stream
        ]
        window_plan = build_window_plan(
            [
                DependencySidecarEdge(
                    snapshot_id="snapshot:test:prescanned",
                    trace_checksum="pending",
                    src_ref=source_event.ref_key,
                    dst_ref=target_event.ref_key,
                    src_kind="ref",
                    dst_kind="ref",
                    relation_kind="ref_index_next",
                    rule_family="ref_ref",
                    provenance="ref_index_row",
                    priority=50,
                    time_hint_begin_ns=int(target_event.timestamp_aligned),
                    time_hint_end_ns=int(target_event.timestamp_aligned),
                    core_hint=int(target_event.core_id),
                    seq_hint_begin=int(target_event.seq),
                    seq_hint_end=int(target_event.seq),
                    segment_hint=f"core:{int(target_event.core_id)}",
                    cycle_guard_token="cg:prescanned",
                    estimate_events=1,
                    estimate_bytes=128,
                    edge_hash="edge:prescanned",
                )
            ],
            [target_event.ref_key],
            bundle.segment_metas,
            ref_index_rows,
        )
        catalog_result = parser_index.build_trace_chunk_catalog(trace_path)
        self.assertTrue(catalog_result.ok, catalog_result.message)

        with patch(
            "parser.index._prescan_trace_chunk_catalog",
            side_effect=AssertionError("prescanned catalog should be reused"),
        ):
            read_result = read_window_plan(
                trace_path,
                window_plan,
                [target_event.ref_key],
                prescanned_catalog=catalog_result.data,
            )

        self.assertTrue(read_result.ok, read_result.message)
        self.assertEqual(read_result.data["matched_refs"], [target_event.ref_key])
        self.assertEqual(read_result.data["missed_refs"], [])
        self.assertEqual(read_result.data["telemetry"]["selected_chunk_count"], 1)

    def test_read_window_plan_reports_window_hit_rate_for_partial_and_zero_target_cases(self) -> None:
        trace_path = write_scenario(self.root / "window-hit-rate.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        target_event = bundle.event_stream[1]
        window_plan = {
            "round_id": 1,
            "spans": [
                {
                    "span_id": "span:1",
                    "t_begin": int(target_event.timestamp_aligned),
                    "t_end": int(target_event.timestamp_aligned),
                    "core_id": int(target_event.core_id),
                    "seq_begin": int(target_event.seq),
                    "seq_end": int(target_event.seq),
                    "target_refs": [target_event.ref_key, "evt:missing"],
                }
            ],
            "planned_seek_count": 1,
            "planned_span_total": 0,
            "planned_ref_count": 2,
            "unplanned_refs": [],
        }

        partial_read = read_window_plan(trace_path, window_plan, [target_event.ref_key, "evt:missing"])
        self.assertTrue(partial_read.ok, partial_read.message)
        self.assertEqual(partial_read.data["matched_refs"], [target_event.ref_key])
        self.assertEqual(partial_read.data["missed_refs"], ["evt:missing"])
        self.assertEqual(partial_read.data["telemetry"]["window_hit_rate"], 0.5)
        self.assertEqual(partial_read.data["telemetry"]["target_ref_count"], 2)
        self.assertEqual(partial_read.data["telemetry"]["matched_ref_count"], 1)
        self.assertEqual(partial_read.data["telemetry"]["missed_ref_count"], 1)

        zero_target_read = read_window_plan(trace_path, window_plan, [])
        self.assertTrue(zero_target_read.ok, zero_target_read.message)
        self.assertEqual(zero_target_read.data["matched_refs"], [])
        self.assertEqual(zero_target_read.data["missed_refs"], [])
        self.assertEqual(zero_target_read.data["scan_count"], 0)
        self.assertEqual(zero_target_read.data["seek_count"], 0)
        self.assertEqual(zero_target_read.data["telemetry"]["target_ref_count"], 0)
        self.assertEqual(zero_target_read.data["telemetry"]["window_hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.sample_data import write_scenario
from parser import load_dataset
import parser.index as parser_index
from parser.evidence_closure import execute_evidence_closure
from parser.evidence_models import DependencySidecarEdge, EvidenceExportRequest
from parser.evidence_seed import resolve_seed_refs
from parser.evidence_sidecar import build_dependency_sidecar, select_candidate_edges
from parser.result import err_result, ok_result


class EvidenceClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_ref_edge(
        self,
        *,
        src_event: object,
        dst_event: object,
        edge_hash: str,
    ) -> DependencySidecarEdge:
        return DependencySidecarEdge(
            snapshot_id="snapshot:test:closure-cache",
            trace_checksum="pending",
            src_ref=src_event.ref_key,
            dst_ref=dst_event.ref_key,
            src_kind="ref",
            dst_kind="ref",
            relation_kind="ref_index_next",
            rule_family="ref_ref",
            provenance="ref_index_row",
            priority=50,
            time_hint_begin_ns=int(dst_event.timestamp_aligned),
            time_hint_end_ns=int(dst_event.timestamp_aligned),
            core_hint=int(dst_event.core_id),
            seq_hint_begin=int(dst_event.seq),
            seq_hint_end=int(dst_event.seq),
            segment_hint=f"core:{int(dst_event.core_id)}",
            cycle_guard_token=f"cg:{edge_hash}",
            estimate_events=1,
            estimate_bytes=128,
            edge_hash=edge_hash,
        )

    def _closure_fixture(self, *, depth_limit: int = 4) -> tuple[Path, object, EvidenceExportRequest, dict[str, object], list[dict[str, object]], list[object]]:
        trace_path = write_scenario(self.root / "closure.trace", name="basic")
        loaded = load_dataset(trace_path)
        self.assertTrue(loaded.ok, loaded.message)
        bundle = loaded.data.bundle
        seed_ref = bundle.event_stream[0].ref_key
        request = EvidenceExportRequest.from_payload(
            {
                "dataset_id": bundle.dataset_id,
                "seed_spec": {"source_kind": "analysis_context", "source_payload": {}},
                "rule_family": ["ref_ref"],
                "budget_vector": {"D_max": depth_limit, "C_events": 32, "S_bytes": 8192, "rho_max": 4.0},
                "closure_policy": {"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 8},
            },
            default_dataset_id=bundle.dataset_id,
            default_time_window=(bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned),
            default_filter={},
        )
        context = {
            "time_window": [bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned],
            "filter": {},
            "selection": {"seed_ref": seed_ref},
            "zoom_level": 1.0,
            "focused_view": "timeline",
            "evidence_anchor": {"ref_key": seed_ref},
            "playback_cursor": None,
            "compare_scope": {},
            "dataset_role": "single",
        }
        ref_index_rows = [
            {
                "ref_key": event.ref_key,
                "timestamp_aligned": float(event.timestamp_aligned),
                "core_id": int(event.core_id),
                "seq": int(event.seq),
            }
            for event in bundle.event_stream
        ]
        seed_result = resolve_seed_refs(request, context, bundle, [], [], [], ref_index_rows)
        self.assertTrue(seed_result.ok, seed_result.message)
        sidecar_rows = build_dependency_sidecar(
            bundle,
            snapshot_id="snapshot:test:closure",
            rule_families=request.rule_family,
            alerts=[],
            diagnoses=[],
            context=context,
            anchors=[],
            ref_index_rows=ref_index_rows,
        )
        return trace_path, bundle, request, seed_result.data, ref_index_rows, sidecar_rows

    def test_execute_evidence_closure_bounds_before_trace_read(self) -> None:
        trace_path, bundle, request, seed_result, ref_index_rows, sidecar_rows = self._closure_fixture(depth_limit=0)

        with patch("parser.evidence_closure.read_window_plan", side_effect=AssertionError("budgeted round must not read trace")):
            outcome = execute_evidence_closure(
                bundle,
                request,
                seed_result,
                sidecar_rows,
                trace_source=trace_path,
                ref_index_rows=ref_index_rows,
            )

        self.assertEqual(outcome.closure_mode, "bounded")
        self.assertEqual(outcome.halt_reason, "DEPTH_LIMIT")
        self.assertGreater(outcome.frontier_count, 0)
        self.assertEqual(outcome.scan_count, 0)
        self.assertEqual(outcome.seek_count, 0)
        self.assertEqual(outcome.window_span_total, 0)
        self.assertEqual(outcome.rounds, [])

    def test_execute_evidence_closure_marks_missed_refs_as_non_exact(self) -> None:
        trace_path, bundle, request, seed_result, ref_index_rows, sidecar_rows = self._closure_fixture(depth_limit=4)
        first_edge = select_candidate_edges(sidecar_rows, list(seed_result["seed_refs"]), request.rule_family)[0]
        next_ref = first_edge.dst_ref
        sidecar_rows = [first_edge]

        with patch(
            "parser.evidence_closure.read_window_plan",
            side_effect=lambda *_args, **_kwargs: ok_result(
                {
                    "round_id": 1,
                    "matched_events": [],
                    "matched_refs": [],
                    "missed_refs": [next_ref],
                    "bytes_read": 0,
                    "scan_count": 1,
                    "seek_count": 1,
                    "window_span_total": 0,
                    "window_count": 1,
                    "corrupt_segments": [],
                    "io_guard_triggered": False,
                    "telemetry": {},
                }
            ),
        ):
            outcome = execute_evidence_closure(
                bundle,
                request,
                seed_result,
                sidecar_rows,
                trace_source=trace_path,
                ref_index_rows=ref_index_rows,
            )

        self.assertEqual(outcome.closure_mode, "degraded")
        self.assertEqual(outcome.halt_reason, "SIDECAR_MISMATCH")
        self.assertIn(next_ref, outcome.missing_required_refs)
        self.assertGreaterEqual(outcome.scan_count, 1)

    def test_execute_evidence_closure_fail_closes_on_unplanned_window_refs(self) -> None:
        trace_path, bundle, request, seed_result, ref_index_rows, sidecar_rows = self._closure_fixture(depth_limit=4)

        with patch(
            "parser.evidence_closure.build_window_plan",
            return_value={
                "round_id": 1,
                "spans": [],
                "planned_seek_count": 0,
                "planned_span_total": 0,
                "planned_ref_count": 1,
                "unplanned_refs": ["evt:missing"],
            },
        ):
            outcome = execute_evidence_closure(
                bundle,
                request,
                seed_result,
                sidecar_rows,
                trace_source=trace_path,
                ref_index_rows=ref_index_rows,
            )

        self.assertEqual(outcome.closure_mode, "degraded")
        self.assertEqual(outcome.halt_reason, "SIDECAR_MISMATCH")
        self.assertEqual(outcome.scan_count, 0)
        self.assertEqual(outcome.seek_count, 0)

    def test_execute_evidence_closure_progress_reports_budget_halt_without_read_stages(self) -> None:
        trace_path, bundle, request, seed_result, ref_index_rows, sidecar_rows = self._closure_fixture(depth_limit=0)
        progress_updates: list[dict[str, object]] = []

        with patch("parser.evidence_closure.read_window_plan", side_effect=AssertionError("budgeted round must not read trace")):
            outcome = execute_evidence_closure(
                bundle,
                request,
                seed_result,
                sidecar_rows,
                trace_source=trace_path,
                ref_index_rows=ref_index_rows,
                emit_progress=progress_updates.append,
            )

        self.assertEqual(outcome.closure_mode, "bounded")
        substages = [str(item.get("substage")) for item in progress_updates]
        self.assertIn("round/project", substages)
        self.assertIn("round/budget", substages)
        self.assertNotIn("round/window_plan", substages)
        self.assertNotIn("round/read", substages)
        self.assertNotIn("round/merge", substages)
        for row in [item for item in progress_updates if str(item.get("substage", "")).startswith("round/")]:
            self.assertEqual(row.get("category"), "evidence_export")
            self.assertIn("round_id", row)
            self.assertIn("frontier_count", row)
            self.assertIn("emitted_events", row)
            self.assertIn("emitted_bytes", row)

    def test_execute_evidence_closure_progress_reports_read_and_merge_stages(self) -> None:
        trace_path, bundle, request, seed_result, ref_index_rows, sidecar_rows = self._closure_fixture(depth_limit=4)
        progress_updates: list[dict[str, object]] = []

        outcome = execute_evidence_closure(
            bundle,
            request,
            seed_result,
            sidecar_rows,
            trace_source=trace_path,
            ref_index_rows=ref_index_rows,
            emit_progress=progress_updates.append,
        )

        self.assertIn(outcome.closure_mode, {"exact", "bounded", "degraded"})
        substages = [str(item.get("substage")) for item in progress_updates]
        self.assertIn("round/project", substages)
        self.assertIn("round/budget", substages)
        self.assertIn("round/window_plan", substages)
        self.assertIn("round/read", substages)
        self.assertIn("round/merge", substages)
        merge_completed = [
            item
            for item in progress_updates
            if item.get("substage") == "round/merge" and item.get("status") == "completed"
        ]
        self.assertTrue(merge_completed)
        self.assertGreaterEqual(int(merge_completed[-1].get("emitted_events", 0)), 1)

    def test_execute_evidence_closure_read_failure_records_round_backtrace(self) -> None:
        trace_path, bundle, request, seed_result, ref_index_rows, sidecar_rows = self._closure_fixture(depth_limit=4)

        with patch(
            "parser.evidence_closure.read_window_plan",
            return_value=err_result("TRACE_IO_GUARD", "simulated read failure"),
        ):
            outcome = execute_evidence_closure(
                bundle,
                request,
                seed_result,
                sidecar_rows,
                trace_source=trace_path,
                ref_index_rows=ref_index_rows,
            )

        self.assertEqual(outcome.closure_mode, "degraded")
        self.assertEqual(outcome.halt_reason, "TRACE_IO_GUARD")
        self.assertTrue(outcome.rounds)
        read_summary = dict(outcome.rounds[-1]["read_result"])
        self.assertFalse(read_summary["read_ok"])
        self.assertEqual(read_summary["read_error_code"], "TRACE_IO_GUARD")
        self.assertGreater(read_summary["target_ref_count"], 0)
        self.assertEqual(read_summary["matched_ref_count"], 0)
        self.assertEqual(read_summary["missed_ref_count"], read_summary["target_ref_count"])
        self.assertEqual(read_summary["window_hit_rate"], 0.0)

    def test_execute_evidence_closure_prescans_catalog_once_across_rounds(self) -> None:
        trace_path, bundle, request, seed_result, ref_index_rows, _sidecar_rows = self._closure_fixture(depth_limit=4)
        sidecar_rows = [
            self._make_ref_edge(src_event=bundle.event_stream[0], dst_event=bundle.event_stream[1], edge_hash="edge:1"),
            self._make_ref_edge(src_event=bundle.event_stream[1], dst_event=bundle.event_stream[2], edge_hash="edge:2"),
            self._make_ref_edge(src_event=bundle.event_stream[2], dst_event=bundle.event_stream[3], edge_hash="edge:3"),
        ]

        with patch(
            "parser.index._prescan_trace_chunk_catalog",
            wraps=parser_index._prescan_trace_chunk_catalog,
        ) as prescan_catalog:
            outcome = execute_evidence_closure(
                bundle,
                request,
                seed_result,
                sidecar_rows,
                trace_source=trace_path,
                ref_index_rows=ref_index_rows,
            )

        self.assertEqual(outcome.closure_mode, "exact")
        self.assertGreaterEqual(len(outcome.rounds), 3)
        self.assertEqual(prescan_catalog.call_count, 1)


if __name__ == "__main__":
    unittest.main()

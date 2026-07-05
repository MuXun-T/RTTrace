from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

from desktop.app.gui import RuntimeProbeWindow
from desktop.qt_compat import PG_AVAILABLE, PYSIDE_AVAILABLE, ensure_qapplication


class WorkbenchWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._app = None
        if PYSIDE_AVAILABLE and PG_AVAILABLE:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            self._app = ensure_qapplication([])

    def _wait_for(self, predicate, timeout: float = 5.0, window: RuntimeProbeWindow | None = None) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._app is not None:
                self._app.processEvents()
            if window is not None:
                if getattr(window, "_active_load_job_id", None) is not None:
                    window._poll_load_job()
                if getattr(window, "_active_export_job_id", None) is not None:
                    window._poll_export_job()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for workbench state")

    def test_demo_dataset_load_and_bookmark(self) -> None:
        window = RuntimeProbeWindow()
        self.addCleanup(window.close)

        if PYSIDE_AVAILABLE and PG_AVAILABLE:
            self.assertEqual(window.body_splitter.count(), 2)
            self.assertEqual(window.analysis_content_splitter.count(), 2)
            self.assertEqual(window.analysis_main_splitter.count(), 3)
            self.assertEqual(window.analysis_inspector_splitter.count(), 3)
            self.assertEqual(window.compare_splitter.count(), 4)
            self.assertEqual(window.export_splitter.count(), 2)
            self.assertEqual(window.diagnostic_splitter.count(), 2)
            self.assertEqual(window.metric_axis_label.text(), "数\n值")
            self.assertEqual(window.timeline_axis_label.text(), "泳\n道")
            self.assertEqual(window.compare_axis_label.text(), "值")
            self.assertEqual([label.text() for label in window.timeline_legend_labels], ["ExecSlice", "IRQ", "Untrusted 窗口"])
            self.assertEqual([label.text() for label in window.compare_legend_labels], ["Baseline", "Candidate"])

        window.load_demo_dataset()
        self._wait_for(lambda: window.state.active_dataset_id is not None, window=window)
        self.assertIsNotNone(window.state.active_dataset_id)
        self.assertGreater(len(window._event_rows), 0)
        self.assertGreater(len(window._alert_rows), 0)
        self.assertIn("IRQ", window.timeline_note_label.text())
        self.assertTrue(window.availability.metric_bucket_chart)
        self.assertTrue(window.availability.resource_wait_chain_detail)
        self.assertTrue(window.availability.compare_evidence_drilldown)
        resource_index = next(
            (index for index, item in enumerate(window._resource_rows) if item.get("resource_id") is not None),
            None,
        )
        if resource_index is not None:
            window._on_resource_selected(resource_index, 0)
            self.assertIn("资源下钻", window.analysis_details.toPlainText())
            self.assertIn("关键等待链", window.analysis_details.toPlainText())

        window.open_plot_preview("metric")
        window.open_plot_preview("timeline")
        self.assertIn("metric", window._plot_preview_windows)
        self.assertIn("timeline", window._plot_preview_windows)
        self.assertEqual(window._plot_preview_windows["metric"].windowTitle(), "指标概览图")
        self.assertEqual(window._plot_preview_windows["timeline"].windowTitle(), "时间线 / 甘特图")
        self.assertEqual(
            [label.text() for label in window._plot_preview_windows["timeline"].legend_labels],
            ["ExecSlice", "IRQ", "Untrusted 窗口"],
        )

        window.bookmark_label_input.setText("focus-window")
        window.create_bookmark()
        self.assertGreaterEqual(len(window._bookmark_rows), 1)

    def test_compare_and_clipped_export_roundtrip(self) -> None:
        window = RuntimeProbeWindow()
        self.addCleanup(window.close)
        window.load_demo_dataset()
        self._wait_for(lambda: window.state.active_dataset_id is not None, window=window)
        window.controller.viz_SetContext(
            {
                "time_window": (1200.0, 1300.0),
                "filter": {"core_id": 0},
            }
        )

        clipped_dir = Path(window._temp_root) / "exports" / "roundtrip-clipped"
        window.export_output_input.setText(str(clipped_dir))
        window.run_export("clipped")
        self._wait_for(
            lambda: (clipped_dir / "meta.json").exists()
            and (clipped_dir / "meta.json").stat().st_size > 0
            and getattr(window, "_active_export_job_id", None) is None,
            window=window,
        )

        meta = json.loads((clipped_dir / "meta.json").read_text(encoding="utf-8"))
        rebuild = json.loads((clipped_dir / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["export_mode"], "clipped")
        self.assertEqual(meta["time_window"], [1200.0, 1300.0])
        padding_rule = meta.get("context_padding_rule") or {}
        self.assertEqual(padding_rule.get("event_policy"), "window_intersection")
        self.assertEqual(padding_rule.get("event_closure_policy"), "window_intersection_plus_required_refs")
        required_event_refs = {
            str(item["cause_event"])
            for item in rebuild.get("task_states", [])
            if item.get("cause_event")
        }
        required_event_refs.update(
            str(item["start_event"])
            for item in rebuild.get("exec_slices", [])
            if item.get("start_event")
        )
        required_event_refs.update(
            str(item["end_event"])
            for item in rebuild.get("exec_slices", [])
            if item.get("end_event")
        )
        graph_payload = rebuild.get("resource_graph") or {}
        for edge in list(graph_payload.get("hold_edges", [])) + list(graph_payload.get("wait_edges", [])):
            if edge.get("evidence_ref"):
                required_event_refs.add(str(edge["evidence_ref"]))
        alerts_rows = json.loads((clipped_dir / "result" / "alerts.json").read_text(encoding="utf-8"))
        diagnosis_rows = json.loads((clipped_dir / "result" / "diagnoses.json").read_text(encoding="utf-8"))
        anchor_rows = json.loads((clipped_dir / "context" / "anchors.json").read_text(encoding="utf-8"))
        analysis_context = json.loads((clipped_dir / "context" / "analysis_context.json").read_text(encoding="utf-8"))
        for row in alerts_rows:
            for evidence_ref in row.get("evidence_refs") or []:
                if str(evidence_ref.get("ref_type", "")).strip().lower() == "event" and evidence_ref.get("ref_key"):
                    required_event_refs.add(str(evidence_ref["ref_key"]))
        for row in diagnosis_rows:
            for evidence_ref in row.get("evidence_refs") or []:
                if str(evidence_ref.get("ref_type", "")).strip().lower() == "event" and evidence_ref.get("ref_key"):
                    required_event_refs.add(str(evidence_ref["ref_key"]))
        for row in anchor_rows:
            evidence_anchor = row.get("evidence_anchor") or {}
            ref_type = str(evidence_anchor.get("ref_type", "")).strip().lower()
            ref_key = evidence_anchor.get("ref_key")
            if ref_key and (ref_type == "event" or (ref_type in {"", "index"} and str(ref_key).startswith("evt:"))):
                required_event_refs.add(str(ref_key))
        context_anchor = analysis_context.get("evidence_anchor") or {}
        context_ref_key = context_anchor.get("ref_key")
        if context_ref_key and str(context_ref_key).startswith("evt:"):
            required_event_refs.add(str(context_ref_key))
        self.assertTrue(any(1200.0 <= item["timestamp_aligned"] <= 1300.0 for item in rebuild["event_stream"]))
        for item in rebuild["event_stream"]:
            if item["timestamp_aligned"] < 1200.0 or item["timestamp_aligned"] > 1300.0:
                self.assertIn(str(item.get("ref_key")), required_event_refs)

        window.load_compare_demo()
        self.assertGreater(len(window._compare_rows), 0)
        window._on_compare_row_selected(0, 0)
        self.assertIn("Baseline 视图", window.compare_details.toPlainText())
        self.assertIn("Delta 摘要", window.compare_details.toPlainText())
        window.open_plot_preview("compare")
        self.assertIn("compare", window._plot_preview_windows)
        self.assertEqual(window._plot_preview_windows["compare"].windowTitle(), "对比差异图")
        self.assertEqual(
            [label.text() for label in window._plot_preview_windows["compare"].legend_labels],
            ["Baseline", "Candidate"],
        )

        window.open_repro_package(str(clipped_dir))
        window.restore_repro_context()
        window.load_repro_dataset()
        self.assertIsNotNone(window.state.active_dataset_id)


if __name__ == "__main__":
    unittest.main()

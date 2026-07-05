from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop import PG_AVAILABLE, PYSIDE_AVAILABLE
from parser.models import EventTableQuery


@unittest.skipUnless(PYSIDE_AVAILABLE and PG_AVAILABLE, "PySide6 + pyqtgraph runtime not installed")
class DesktopRuntimeTests(unittest.TestCase):
    def _make_window(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from desktop.app.gui import RuntimeProbeWindow
        from desktop.qt_compat import ensure_qapplication

        app = ensure_qapplication([])
        window = RuntimeProbeWindow()
        return app, window

    def _wait_for_load_completion(self, app, window, timeout_s: float = 5.0) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            app.processEvents()
            job_id = window._active_load_job_id
            if job_id is None and window.state.active_dataset_id is not None:
                return window.state.active_dataset_id
            time.sleep(0.01)
        self.fail("dataset load did not finish within timeout")

    def _prepare_source_backed_dataset(self, app, window, trace_path: Path) -> tuple[str, tuple[float, float]]:
        window.load_single_dataset(str(trace_path))
        dataset_id = self._wait_for_load_completion(app, window)
        record = window.controller.repository.get(dataset_id)
        stream = record.artifact.bundle.event_stream
        self.assertGreater(len(stream), 0)
        time_window = (stream[0].timestamp_aligned, stream[-1].timestamp_aligned)
        record.artifact.bundle.event_stream = []
        window.controller.repository.query_cache.clear_dataset(dataset_id)
        window.controller.viz_SetContext({"time_window": time_window, "focused_view": "timeline"})
        return dataset_id, time_window

    def _refresh_analysis_cache(self, app, window, timeout_s: float = 2.0) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            window.refresh_workspace()
            app.processEvents()
            cache = window._analysis_cache
            if isinstance(cache, dict):
                events = cache.get("events")
                timeline = cache.get("timeline")
                if isinstance(events, dict) and "summary" in events and isinstance(timeline, dict) and "summary" in timeline:
                    return cache
            time.sleep(0.01)
        self.fail("analysis cache did not refresh within timeout")

    def _wait_for_export_completion(self, app, window, job_id: str, timeout_s: float = 5.0) -> str:
        deadline = time.time() + timeout_s
        last_status = "created"
        while time.time() < deadline:
            app.processEvents()
            snapshot = window.controller.jobs.status(job_id)
            self.assertTrue(snapshot.ok, snapshot.message)
            last_status = snapshot.data["status"]
            if last_status in {"succeeded", "failed"}:
                return last_status
            time.sleep(0.01)
        self.fail("export did not finish within timeout")

    def test_module_entrypoint_executes_main(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "desktop.app.gui", "--definitely-invalid"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("usage:", proc.stderr.lower())

    def test_offscreen_runtime_probe_window(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from desktop.app.gui import RuntimeProbeWindow
        from desktop.qt_compat import QTimer, ensure_qapplication
        from desktop.sample_data import write_scenario

        app = ensure_qapplication([])
        fd, temp_path = tempfile.mkstemp(prefix="rttrace-runtime-", suffix=".trace")
        os.close(fd)
        trace_path = Path(temp_path)
        try:
            write_scenario(trace_path, name="basic")
            window = RuntimeProbeWindow(source=str(trace_path))
            window.show()
            QTimer.singleShot(0, app.quit)  # type: ignore[attr-defined]
            code = app.exec()
            self.assertEqual(code, 0)
        finally:
            trace_path.unlink(missing_ok=True)

    def test_offscreen_compare_pair_updates_runtime_state(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-compare-") as temp_dir:
            baseline = Path(temp_dir) / "baseline.trace"
            candidate = Path(temp_dir) / "candidate.trace"
            write_scenario(baseline, name="basic", candidate_variant=False)
            write_scenario(candidate, name="basic", candidate_variant=True)

            window.load_compare_pair(str(baseline), str(candidate))
            app.processEvents()

            self.assertIsNotNone(window.state.compare_baseline_id)
            self.assertIsNotNone(window.state.compare_candidate_id)
            self.assertTrue(window.state.compare_baseline_id.endswith(":baseline"))
            self.assertTrue(window.state.compare_candidate_id.endswith(":candidate"))
            self.assertEqual(window.state.active_dataset_id, window.state.compare_baseline_id)
            self.assertGreater(len(window._compare_rows), 0)
            self.assertIn(window.state.compare_baseline_id, window.compare_scope_label.text())
            self.assertIn(window.state.compare_candidate_id, window.footer_label.text())
        window.close()
        app.processEvents()

    def test_offscreen_replay_step_updates_context_and_views(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-replay-") as temp_dir:
            trace_path = Path(temp_dir) / "basic.trace"
            write_scenario(trace_path, name="basic")

            window.load_single_dataset(str(trace_path))
            dataset_id = self._wait_for_load_completion(app, window)
            initial_cursor = window.replay_service.replay_GetState().data.cursor["step_index"]
            window.step_replay(2)
            app.processEvents()

            state = window.replay_service.replay_GetState()
            context = window.controller.context_store.get()
            self.assertEqual(initial_cursor, 0)
            self.assertTrue(state.ok, state.message)
            self.assertEqual(state.data.cursor["step_index"], 2)
            self.assertEqual(state.data.status, "paused")
            self.assertIsNotNone(context.playback_cursor)
            self.assertNotEqual(context.time_window, (0.0, 0.0))
            self.assertEqual(context.playback_cursor["ref_key"], state.data.cursor["ref_key"])
            self.assertEqual(context.evidence_anchor["ref_key"], state.data.anchor_ref["ref_key"])
            self.assertEqual(window.state.active_dataset_id, dataset_id)
            self.assertEqual(window.state.active_dataset_id, window.controller.active_dataset_id)
            self.assertGreater(len(window._event_rows), 0)
            self.assertGreater(len(window._task_state_rows), 0)
            self.assertIn("index=2", window.footer_label.text())
        window.close()
        app.processEvents()

    def test_offscreen_hover_target_updates_without_mutating_selection(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-hover-") as temp_dir:
            trace_path = Path(temp_dir) / "basic.trace"
            write_scenario(trace_path, name="basic")

            window.load_single_dataset(str(trace_path))
            self._wait_for_load_completion(app, window)
            self.assertGreater(len(window._event_rows), 2)

            window._on_event_row_selected(0, 0)
            app.processEvents()
            selected_context = window.controller.context_store.get()

            window._on_event_row_hover(1, 0)
            app.processEvents()
            hovered_context = window.controller.context_store.get()

            self.assertEqual(hovered_context.selection, selected_context.selection)
            self.assertEqual(hovered_context.hover_target["view"], "event_table")
            self.assertEqual(
                hovered_context.transient_selection["event_uid"],
                window._event_rows[1]["event_uid"],
            )
            self.assertIn("hover=event_table", window.footer_label.text())
        window.close()
        app.processEvents()

    def test_offscreen_transient_selection_clears_after_commit_or_cancel(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-transient-") as temp_dir:
            trace_path = Path(temp_dir) / "basic.trace"
            write_scenario(trace_path, name="basic")

            window.load_single_dataset(str(trace_path))
            self._wait_for_load_completion(app, window)
            self.assertGreater(len(window._event_rows), 2)

            window._on_event_row_hover(1, 0)
            app.processEvents()
            staged_context = window.controller.context_store.get()
            self.assertIsNotNone(staged_context.transient_selection)

            window.commit_transient_selection()
            app.processEvents()
            committed_context = window.controller.context_store.get()
            self.assertEqual(committed_context.selection["event_uid"], window._event_rows[1]["event_uid"])
            self.assertIsNone(committed_context.transient_selection)
            self.assertIsNone(committed_context.hover_target)

            window._on_event_row_hover(2, 0)
            app.processEvents()
            window.cancel_transient_selection()
            app.processEvents()
            cancelled_context = window.controller.context_store.get()
            self.assertEqual(cancelled_context.selection["event_uid"], window._event_rows[1]["event_uid"])
            self.assertIsNone(cancelled_context.transient_selection)
            self.assertIsNone(cancelled_context.hover_target)
        window.close()
        app.processEvents()

    def test_offscreen_async_export_updates_runtime_state(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-export-") as temp_dir:
            trace_path = Path(temp_dir) / "basic.trace"
            package_dir = Path(temp_dir) / "package"
            write_scenario(trace_path, name="basic")

            window.load_single_dataset(str(trace_path))
            self._wait_for_load_completion(app, window)
            window.export_output_input.setText(str(package_dir))
            window.run_export("full")
            app.processEvents()

            job_id = window._active_export_job_id or window._export_cache.get("job", {}).get("job_id")
            self.assertIsNotNone(job_id)
            self.assertTrue(
                "导出中" in window.export_status_label.text() or "导出完成" in window.export_status_label.text()
            )

            last_status = self._wait_for_export_completion(app, window, job_id or "")

            settle_deadline = time.time() + 1.0
            while time.time() < settle_deadline and "导出完成" not in window.export_status_label.text():
                app.processEvents()
                time.sleep(0.01)

            self.assertEqual(last_status, "succeeded")
            self.assertIn("导出完成", window.export_status_label.text())
            self.assertTrue((package_dir / "manifest.json").exists())
        window.close()
        app.processEvents()

    def test_offscreen_evidence_export_updates_closure_mode_and_proof_digest(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-evidence-") as temp_dir:
            trace_path = Path(temp_dir) / "basic.trace"
            package_dir = Path(temp_dir) / "evidence-package"
            write_scenario(trace_path, name="basic")

            window.load_single_dataset(str(trace_path))
            self._wait_for_load_completion(app, window)
            window.export_output_input.setText(str(package_dir))
            window.evidence_mode_combo.setCurrentText("mode_a")
            window.run_export("evidence")
            app.processEvents()

            job_id = window._active_export_job_id or window._export_cache.get("job", {}).get("job_id")
            self.assertIsNotNone(job_id)
            self.assertIn("导出中", window.export_status_label.text())

            last_status = self._wait_for_export_completion(app, window, job_id or "")
            settle_deadline = time.time() + 1.5
            while time.time() < settle_deadline and "导出完成" not in window.export_status_label.text():
                app.processEvents()
                time.sleep(0.01)

            self.assertEqual(last_status, "succeeded")
            self.assertTrue((package_dir / "control" / "proof_digest.json").exists())
            self.assertIn("Closure Mode:", window.export_closure_mode_label.text())
            self.assertNotIn("pending", window.export_closure_mode_label.text())
            self.assertNotEqual(window.proof_digest_labels["proof_hash"].text(), "-")
            self.assertNotEqual(window.proof_digest_labels["closure_mode"].text(), "-")
            self.assertEqual(window.repro_package_input.text(), str(package_dir))
            payload = json.loads(window.export_result_text.toPlainText())
            self.assertEqual(payload["job_id"], job_id)
            self.assertEqual(payload["package_path"], str(package_dir))
            self.assertIn(payload["closure_mode"], {"exact", "bounded", "degraded"})
            self.assertEqual(payload["proof_digest"]["proof_hash"], window.proof_digest_labels["proof_hash"].text())

            window.open_repro_package(str(package_dir))
            app.processEvents()
            self.assertEqual(window.repro_package_input.text(), str(package_dir))
            self.assertIn(str(package_dir), window.repro_status_label.text())
            self.assertEqual(payload["proof_digest"]["proof_hash"], window.proof_digest_labels["proof_hash"].text())
        window.close()
        app.processEvents()

    def test_offscreen_event_table_uses_source_backed_page_when_bundle_stream_missing(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-source-event-") as temp_dir:
            trace_path = Path(temp_dir) / "source-event.trace"
            write_scenario(trace_path, name="basic")

            dataset_id, time_window = self._prepare_source_backed_dataset(app, window, trace_path)
            cache = self._refresh_analysis_cache(app, window)
            events = cache["events"]
            summary = events["summary"]
            self.assertIn(summary.get("source"), {"trace_window_scan", "package_index"})
            self.assertNotEqual(summary.get("fallback_reason"), "bundle_scan")
            readiness = summary.get("readiness") or {}
            view_ready = readiness.get("view_ready") or {}
            self.assertTrue(view_ready.get("event_table"))
            self.assertGreater(len(window._event_rows), 0)

            direct = window.controller.viz_QueryEventTable(
                EventTableQuery(
                    filter={
                        "dataset_id": dataset_id,
                        "t_begin": time_window[0],
                        "t_end": time_window[1],
                    },
                    limit=8,
                )
            )
            self.assertTrue(direct.ok, direct.message)
            self.assertIn(direct.data.summary.get("source"), {"trace_window_scan", "package_index"})
            self.assertNotEqual(direct.data.summary.get("fallback_reason"), "bundle_scan")
        window.close()
        app.processEvents()

    def test_offscreen_lod2_uses_source_backed_query_when_bundle_stream_missing(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-source-lod2-") as temp_dir:
            trace_path = Path(temp_dir) / "source-lod2.trace"
            write_scenario(trace_path, name="basic")

            dataset_id, time_window = self._prepare_source_backed_dataset(app, window, trace_path)
            window.state.timeline_lod = 2
            cache = self._refresh_analysis_cache(app, window)
            timeline = cache["timeline"]
            summary = timeline["summary"]
            self.assertIn(summary.get("source"), {"trace_window_scan", "package_index"})
            self.assertNotEqual(summary.get("fallback_reason"), "bundle_scan")
            readiness = summary.get("readiness") or {}
            lod_ready = readiness.get("lod_ready") or {}
            self.assertTrue(lod_ready.get("lod2"))
            self.assertGreater(len(timeline.get("events") or []), 0)

            direct = window.controller.viz_QueryTimelineLOD(
                {
                    "dataset_id": dataset_id,
                    "time_window": time_window,
                    "filter": {},
                    "lod": 2,
                }
            )
            self.assertTrue(direct.ok, direct.message)
            self.assertIn(direct.data.summary.get("source"), {"trace_window_scan", "package_index"})
            self.assertNotEqual(direct.data.summary.get("fallback_reason"), "bundle_scan")
        window.close()
        app.processEvents()

    def test_offscreen_context_change_does_not_clear_task_state_preview_during_active_load(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-load-context-") as temp_dir:
            trace_path = Path(temp_dir) / "basic.trace"
            write_scenario(trace_path, name="basic")
            original_load = window.controller._load_artifact_from_source

            def slow_load(source: str, **kwargs):
                time.sleep(0.1)
                return original_load(source, **kwargs)

            with patch.object(window.controller, "_load_artifact_from_source", side_effect=slow_load):
                window.load_single_dataset(str(trace_path))
                preview_deadline = time.time() + 1.0
                while time.time() < preview_deadline:
                    app.processEvents()
                    if "stage=preview_ready" in window.footer_label.text():
                        break
                    time.sleep(0.01)

                self.assertIn("stage=preview_ready", window.footer_label.text())
                if hasattr(window.task_state_table, "rowCount"):
                    self.assertGreater(window.task_state_table.rowCount(), 0)

                window.controller.context_store.commit({"focused_view": "timeline"})
                settle_deadline = time.time() + 0.2
                while time.time() < settle_deadline:
                    app.processEvents()
                    if hasattr(window.task_state_table, "rowCount"):
                        self.assertGreater(window.task_state_table.rowCount(), 0)
                    time.sleep(0.01)

            self._wait_for_load_completion(app, window)
        window.close()
        app.processEvents()

    def test_offscreen_async_load_updates_runtime_state(self) -> None:
        from desktop.sample_data import write_scenario

        app, window = self._make_window()
        with tempfile.TemporaryDirectory(prefix="rttrace-qt-load-") as temp_dir:
            trace_path = Path(temp_dir) / "basic.trace"
            write_scenario(trace_path, name="basic")

            window.load_single_dataset(str(trace_path))
            app.processEvents()
            self.assertIn("摘要已就绪", window.footer_label.text())
            self.assertIn("stage=preview_ready", window.footer_label.text())
            self.assertIn("TaskStatePreview=", window.footer_label.text())
            if hasattr(window.task_state_table, "rowCount"):
                preview_deadline = time.time() + 0.2
                while time.time() < preview_deadline:
                    app.processEvents()
                    self.assertGreater(window.task_state_table.rowCount(), 0)
                    time.sleep(0.01)
            dataset_id = self._wait_for_load_completion(app, window)

            self.assertTrue(dataset_id)
            self.assertEqual(window.state.active_dataset_id, dataset_id)
            self.assertEqual(window.controller.active_dataset_id, dataset_id)
            self.assertGreater(len(window._event_rows), 0)
            self.assertGreater(len(window._task_state_rows), 0)
            self.assertIn("已加载数据集", window.footer_label.text())
        window.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()

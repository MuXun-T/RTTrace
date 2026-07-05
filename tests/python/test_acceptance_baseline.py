from __future__ import annotations

import json
import gzip
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.sample_data import write_scenario
from desktop.services import CompareService, ExportService, ReproService, WorkspaceController
from parser.models import dataclass_to_dict
from parser.result import err_result
from spec.io import checksum_file
from tool.build_public_rtos_large_input import DENSE_REAL_SEED_TARGET_BYTES
from tool.desktop_validation_preflight import _is_local_disk_fs_type
from tool.run_acceptance_baseline import _trace_input_provenance


FORMAL_COMPARE_SCOPE_FIELDS = {
    "baseline_id",
    "candidate_id",
    "aligned_time_window",
    "filter",
    "dimensions",
    "metric_ids",
    "bucket_size",
    "evidence_policy",
    "scope_id",
}


def _metric_scalar(metric_row: dict[str, object]) -> float:
    summary = metric_row["summary"]
    if "avg_utilization" in summary:
        return float(summary["avg_utilization"])
    if "count" in summary:
        return float(summary["count"])
    if "total_blocked_time" in summary:
        return float(summary["total_blocked_time"])
    if "total_ready_wait_time" in summary:
        return float(summary["total_ready_wait_time"])
    if "avg_response_time" in summary:
        return float(summary["avg_response_time"])
    if "max_jitter" in summary:
        return float(summary["max_jitter"])
    if "irq_count" in summary:
        return float(summary["irq_count"])
    if "total_irq_latency" in summary:
        return float(summary["total_irq_latency"])
    return 0.0


def _summary_metric_rows(summary: dict[str, object]) -> list[dict[str, object]]:
    return list(summary["metric_changes"])


class AcceptanceBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo_root = Path(__file__).resolve().parents[2]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_acceptance_baseline(self, report_path: Path, *args: str) -> dict[str, object]:
        proc = subprocess.run(
            [
                sys.executable,
                "tool/run_acceptance_baseline.py",
                *args,
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(report_path.read_text(encoding="utf-8"))

    def _load_pair(self, *, repeat: int = 12) -> tuple[WorkspaceController, str, str]:
        baseline = write_scenario(self.root / "baseline.trace", name="multi_core", repeat=repeat)
        candidate = write_scenario(
            self.root / "candidate.trace",
            name="multi_core",
            candidate_variant=True,
            repeat=repeat,
        )
        controller = WorkspaceController()
        baseline_loaded = controller.viz_LoadDataset(str(baseline))
        candidate_loaded = controller.viz_LoadDataset(str(candidate))
        self.assertTrue(baseline_loaded.ok, baseline_loaded.message)
        self.assertTrue(candidate_loaded.ok, candidate_loaded.message)
        return controller, baseline_loaded.data, candidate_loaded.data

    @staticmethod
    def _bundle_window(controller: WorkspaceController, dataset_id: str) -> tuple[float, float]:
        bundle = controller.repository.get(dataset_id).artifact.bundle
        return (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned)

    def _export_package(
        self,
        controller: WorkspaceController,
        dataset_id: str,
        output_dir: Path,
        *,
        context_payload: dict[str, object] | None = None,
        export_kwargs: dict[str, object] | None = None,
    ) -> tuple[ExportService, dict[str, object]]:
        time_window = self._bundle_window(controller, dataset_id)
        context = controller.viz_SetContext(
            dict(
                {
                    "time_window": time_window,
                    "filter": {},
                    "selection": {"task_id": 1},
                    "zoom_level": 1.5,
                    "focused_view": "timeline",
                    "dataset_role": "single",
                },
                **(context_payload or {}),
            )
        )
        if context_payload is None:
            self.assertEqual(context.data.focused_view, "timeline")
        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Full(
            dict(
                {
                    "dataset_id": dataset_id,
                    "run_batch_id": "baseline-batch",
                    "version_id": "acceptance-baseline",
                    "experiment_params": {"profile": "acceptance", "repeatable": True},
                },
                **(export_kwargs or {}),
            )
        )
        self.assertTrue(job.ok, job.message)
        written = export.export_WritePackage(job.data["job_id"], str(output_dir))
        self.assertTrue(written.ok, written.message)
        normalized = export.export_NormalizePackage(str(output_dir))
        self.assertTrue(normalized.ok, normalized.message)
        return export, normalized.data

    def _rewrite_dictionary_with_same_semantics(self, package_dir: Path) -> None:
        dictionary_path = package_dir / "reference" / "dictionary.json"
        dictionary_payload = json.loads(dictionary_path.read_text(encoding="utf-8"))
        dictionary_path.write_text(
            json.dumps(dictionary_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        dictionary_checksum = checksum_file(dictionary_path)
        meta_path = package_dir / "meta.json"
        meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        meta_payload["dict_ref"]["checksum"] = dictionary_checksum
        meta_path.write_text(json.dumps(meta_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        manifest_path = package_dir / "manifest.json"
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest_payload["entries"]:
            if entry["path"] == "reference/dictionary.json":
                entry["checksum"] = dictionary_checksum
                break
        manifest_path.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _repro_snapshot(self, package_path: Path) -> dict[str, object]:
        controller = WorkspaceController()
        repro = ReproService(controller.repository, controller.context_store)
        opened = repro.repro_OpenPackage(str(package_path))
        self.assertTrue(opened.ok, opened.message)
        restored = repro.repro_RestoreContext(None)
        self.assertTrue(restored.ok, restored.message)
        loaded = repro.repro_LoadAsDataset("single")
        self.assertTrue(loaded.ok, loaded.message)
        dataset_id = loaded.data
        bundle = controller.repository.get(dataset_id).artifact.bundle
        time_window = tuple(restored.data.time_window)
        if time_window == (0.0, 0.0):
            time_window = (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned)
        metrics = controller.viz_QueryMetricSeries(
            {
                "dataset_id": dataset_id,
                "time_window": time_window,
                "filter": restored.data.filter,
            }
        )
        alerts = controller.viz_QueryAlerts(
            {
                "dataset_id": dataset_id,
                "time_window": time_window,
                "filter": restored.data.filter,
            }
        )
        self.assertTrue(metrics.ok, metrics.message)
        self.assertTrue(alerts.ok, alerts.message)
        return {
            "context": restored.data.persisted_dict(),
            "metric_summary": {
                item["metric_id"]: _metric_scalar(item)
                for item in metrics.data
            },
            "alert_ids": [item.alert_id for item in alerts.data],
            "event_count": len(bundle.event_stream),
            "task_state_count": len(bundle.task_states),
            "exec_slice_count": len(bundle.exec_slices),
        }

    def test_repeated_export_is_content_consistent(self) -> None:
        controller, baseline_id, _ = self._load_pair()
        _, normalized_a = self._export_package(controller, baseline_id, self.root / "package-a")
        _, normalized_b = self._export_package(controller, baseline_id, self.root / "package-b")
        self.assertEqual(normalized_a, normalized_b)

    def test_compare_scope_matches_single_run_metrics(self) -> None:
        controller, baseline_id, candidate_id = self._load_pair()
        compare = CompareService(controller.repository, controller.context_store)
        loaded = compare.cmp_LoadPair(baseline_id, candidate_id)
        self.assertTrue(loaded.ok, loaded.message)
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_id,
                "candidate_id": candidate_id,
                "filter": {},
            }
        )
        self.assertTrue(scoped.ok, scoped.message)
        summary = compare.cmp_QueryDiffSummary()
        self.assertTrue(summary.ok, summary.message)
        self.assertEqual(set(summary.data["scope"]), FORMAL_COMPARE_SCOPE_FIELDS)
        self.assertEqual(summary.data["scope"], dataclass_to_dict(scoped.data))

        baseline_metrics = controller.viz_QueryMetricSeries(
            {
                "dataset_id": scoped.data.baseline_id,
                "time_window": tuple(scoped.data.aligned_time_window),
                "filter": scoped.data.filter,
            }
        )
        candidate_metrics = controller.viz_QueryMetricSeries(
            {
                "dataset_id": scoped.data.candidate_id,
                "time_window": tuple(scoped.data.aligned_time_window),
                "filter": scoped.data.filter,
            }
        )
        self.assertTrue(baseline_metrics.ok, baseline_metrics.message)
        self.assertTrue(candidate_metrics.ok, candidate_metrics.message)
        baseline_map = {item["metric_id"]: _metric_scalar(item) for item in baseline_metrics.data}
        candidate_map = {item["metric_id"]: _metric_scalar(item) for item in candidate_metrics.data}

        for row in _summary_metric_rows(summary.data):
            metric_id = row["metric_id"]
            self.assertAlmostEqual(float(row["baseline"]), baseline_map[metric_id], places=6)
            self.assertAlmostEqual(float(row["candidate"]), candidate_map[metric_id], places=6)

    def test_compare_scope_round_trip_is_repeatable_across_export_and_repro(self) -> None:
        controller, baseline_id, candidate_id = self._load_pair(repeat=10)
        compare = CompareService(controller.repository, controller.context_store)
        loaded = compare.cmp_LoadPair(baseline_id, candidate_id)
        self.assertTrue(loaded.ok, loaded.message)
        scoped = compare.cmp_SetScope(
            {
                "baseline_id": baseline_id,
                "candidate_id": candidate_id,
                "filter": {"task_id": 1},
                "dimensions": ["metric", "alert", "resource", "irq"],
                "metric_ids": ["cpu_utilization", "blocked_time"],
                "bucket_size": 96.0,
                "evidence_policy": "retain",
                "scope_id": "scope:acceptance-roundtrip",
            }
        )
        self.assertTrue(scoped.ok, scoped.message)

        expected_scope = dataclass_to_dict(scoped.data)
        self.assertEqual(set(expected_scope), FORMAL_COMPARE_SCOPE_FIELDS)
        _, normalized = self._export_package(controller, scoped.data.baseline_id, self.root / "compare-roundtrip-package")
        self.assertEqual(normalized["compare_scope"], expected_scope)

        repro_snapshot = self._repro_snapshot(self.root / "compare-roundtrip-package")
        self.assertEqual(repro_snapshot["context"]["compare_scope"], expected_scope)

    def test_repro_reopen_is_repeatable(self) -> None:
        controller, baseline_id, _ = self._load_pair(repeat=10)
        self._export_package(controller, baseline_id, self.root / "repro-package")
        first = self._repro_snapshot(self.root / "repro-package")
        second = self._repro_snapshot(self.root / "repro-package")
        self.assertEqual(first, second)

    def test_short_soak_load_export_repro_loop(self) -> None:
        trace_path = write_scenario(self.root / "soak.trace", name="multi_core", repeat=8)
        reference_snapshot: dict[str, object] | None = None

        for index in range(12):
            controller = WorkspaceController()
            loaded = controller.viz_LoadDataset(str(trace_path))
            self.assertTrue(loaded.ok, loaded.message)
            package_dir = self.root / f"soak-package-{index:02d}"
            export, normalized = self._export_package(controller, loaded.data, package_dir)
            self.assertTrue(export.export_NormalizePackage(str(package_dir)).ok)
            reopened = self._repro_snapshot(package_dir)
            snapshot = {
                "normalized_package": normalized,
                "reopened": reopened,
            }
            if reference_snapshot is None:
                reference_snapshot = snapshot
                continue
            self.assertEqual(snapshot, reference_snapshot)

    def test_acceptance_baseline_report_contains_environment_and_input_metadata(self) -> None:
        report_path = self.root / "acceptance-report.json"
        report = self._run_acceptance_baseline(
            report_path,
            "--repeat",
            "4",
            "--soak-iterations",
            "1",
        )
        self.assertIn("environment", report)
        self.assertEqual(report["profile"], "smoke")
        self.assertEqual(report["inputs"]["source"], "generated")
        self.assertTrue(report["inputs"]["baseline_sha256"])
        self.assertTrue(report["inputs"]["candidate_sha256"])

    def test_acceptance_baseline_report_contains_cache_metrics(self) -> None:
        report_path = self.root / "acceptance-cache-report.json"
        report = self._run_acceptance_baseline(
            report_path,
            "--repeat",
            "4",
            "--soak-iterations",
            "1",
        )
        self.assertIn("cache", report)
        cache = report["cache"]
        self.assertIsInstance(cache, dict)
        self.assertIn("budget_bytes", cache)
        self.assertIn("entry_count", cache)
        self.assertIn("total_bytes", cache)
        self.assertIn("eviction_count", cache)
        self.assertIn("namespaces", cache)
        self.assertIsInstance(cache["budget_bytes"], int)
        self.assertIsInstance(cache["entry_count"], int)
        self.assertIsInstance(cache["total_bytes"], int)
        self.assertIsInstance(cache["eviction_count"], int)
        self.assertIsInstance(cache["namespaces"], dict)
        self.assertIn("task_states", cache["namespaces"])
        self.assertIn("event_table", cache["namespaces"])
        self.assertIn("timeline", cache["namespaces"])
        for namespace in ("task_states", "event_table", "timeline"):
            bucket = cache["namespaces"][namespace]
            self.assertIn("entry_count", bucket)
            self.assertIn("byte_size_estimate", bucket)
            self.assertIsInstance(bucket["entry_count"], int)
            self.assertIsInstance(bucket["byte_size_estimate"], int)

    def test_acceptance_baseline_captures_async_load_preview_metrics(self) -> None:
        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-report.json",
            "--mode",
            "desktop_perf_baseline",
            "--profile",
            "medium",
            "--repeat",
            "4",
            "--soak-iterations",
            "1",
        )

        self.assertEqual(report["mode"], "desktop_perf_baseline")
        self.assertEqual(report["profile"], "medium")
        self.assertIn("desktop_perf", report)
        desktop_perf = report["desktop_perf"]
        for key in (
            "profile",
            "load_preview_seconds",
            "task_state_preview_seconds",
            "event_table_first_page_seconds",
            "lod2_first_window_seconds",
            "peak_memory_mb",
            "cache_eviction_count",
            "cache_budget_bytes",
            "cache_namespaces",
        ):
            self.assertIn(key, desktop_perf)
        self.assertEqual(desktop_perf["profile"], "medium")
        self.assertGreater(desktop_perf["load_preview_seconds"], 0.0)
        self.assertGreater(desktop_perf["task_state_preview_seconds"], 0.0)
        self.assertGreater(desktop_perf["event_table_first_page_seconds"], 0.0)
        self.assertGreater(desktop_perf["lod2_first_window_seconds"], 0.0)
        self.assertEqual(desktop_perf["preview_stage"], "preview_ready")
        self.assertEqual(desktop_perf["resolved_stage"], "query_ready")
        self.assertGreater(desktop_perf["task_state_preview_lane_count"], 0)
        self.assertGreater(desktop_perf["task_state_preview_task_count"], 0)
        self.assertGreater(desktop_perf["event_table_first_page_count"], 0)
        self.assertGreater(desktop_perf["lod2_first_window_count"], 0)

    def test_acceptance_baseline_captures_peak_memory_and_cache_metrics(self) -> None:
        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-memory-report.json",
            "--mode",
            "desktop_perf_baseline",
            "--profile",
            "smoke",
            "--repeat",
            "4",
            "--soak-iterations",
            "1",
        )

        self.assertIn("memory", report)
        self.assertGreater(report["memory"]["peak_memory_bytes"], 0)
        self.assertGreater(report["memory"]["peak_memory_mb"], 0.0)
        desktop_perf = report["desktop_perf"]
        self.assertGreater(desktop_perf["peak_memory_mb"], 0.0)
        self.assertGreaterEqual(desktop_perf["cache_eviction_count"], 0)
        self.assertGreater(desktop_perf["cache_budget_bytes"], 0)
        self.assertGreaterEqual(desktop_perf["cache_entry_count"], 0)
        self.assertGreaterEqual(desktop_perf["cache_total_bytes"], 0)
        self.assertIsInstance(desktop_perf["cache_namespaces"], dict)
        for namespace in ("task_states", "event_table", "timeline"):
            self.assertIn(namespace, desktop_perf["cache_namespaces"])
            bucket = desktop_perf["cache_namespaces"][namespace]
            self.assertIn("entry_count", bucket)
            self.assertIn("byte_size_estimate", bucket)
            self.assertIsInstance(bucket["entry_count"], int)
            self.assertIsInstance(bucket["byte_size_estimate"], int)

    def test_desktop_perf_acceptance_report_captures_threshold_verdicts(self) -> None:
        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-acceptance-linux.json",
            "--mode",
            "desktop_perf_acceptance",
            "--baseline-input",
            str(self.repo_root / "docs/validation-fixture/baseline.trace"),
            "--candidate-input",
            str(self.repo_root / "docs/validation-fixture/candidate.trace"),
            "--soak-iterations",
            "1",
        )

        self.assertEqual(report["mode"], "desktop_perf_acceptance")
        self.assertIn("desktop_perf_acceptance", report)
        acceptance = report["desktop_perf_acceptance"]
        self.assertEqual(acceptance["platform"], "linux")
        self.assertEqual(acceptance["acceptance_scope"], "full")
        self.assertIn("first_screen_lt_10s", acceptance)
        self.assertIn("peak_memory_lt_4gb", acceptance)
        self.assertIn("first_screen_peak_memory_lt_4gb", acceptance)
        self.assertIn("formal_input", acceptance)
        self.assertTrue(acceptance["first_screen_lt_10s"]["pass"])
        self.assertTrue(acceptance["peak_memory_lt_4gb"]["pass"])
        self.assertTrue(acceptance["first_screen_peak_memory_lt_4gb"]["pass"])
        self.assertIn("source", acceptance["peak_memory_lt_4gb"])
        self.assertIn("source", acceptance["first_screen_peak_memory_lt_4gb"])
        self.assertFalse(acceptance["formal_input"]["formal_1gb_verified"])
        self.assertEqual(acceptance["formal_input"]["input_provenance"], "real_external")
        self.assertIn("baseline_size_bytes", acceptance["formal_input"])
        self.assertIn("candidate_size_bytes", acceptance["formal_input"])
        self.assertIn("baseline_parse_ok", acceptance["formal_input"])
        self.assertIn("candidate_parse_ok", acceptance["formal_input"])
        self.assertIn("baseline_event_count", acceptance["formal_input"])
        self.assertIn("candidate_event_count", acceptance["formal_input"])
        self.assertIn("baseline_input_provenance", acceptance["formal_input"])
        self.assertIn("candidate_input_provenance", acceptance["formal_input"])
        self.assertIn("python_peak_alloc_mb", report["memory"])
        self.assertIn("os_peak", report["memory"])
        durations = report["durations"]
        self.assertGreater(durations["export_write_seconds"], 0.0)
        self.assertGreater(durations["export_normalize_seconds"], 0.0)
        self.assertGreater(durations["export_clipped_write_seconds"], 0.0)
        self.assertGreater(durations["export_clipped_normalize_seconds"], 0.0)
        self.assertIn("export_prepare_seconds", durations)
        self.assertIn("export_encode_trace_seconds", durations)
        self.assertIn("export_json_dump_seconds", durations)
        self.assertIn("export_checksum_seconds", durations)
        self.assertIn("export_clipped_prepare_seconds", durations)
        self.assertIn("export_clipped_encode_trace_seconds", durations)
        self.assertIn("export_clipped_json_dump_seconds", durations)
        self.assertIn("export_clipped_checksum_seconds", durations)
        self.assertEqual(
            durations["export_full_seconds"],
            round(durations["export_write_seconds"] + durations["export_normalize_seconds"], 6),
        )
        self.assertEqual(
            durations["export_clipped_seconds"],
            round(durations["export_clipped_write_seconds"] + durations["export_clipped_normalize_seconds"], 6),
        )

    def test_acceptance_baseline_supports_configurable_workdir_and_load_timeout(self) -> None:
        scratch_dir = self.root / "scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-acceptance-linux-workdir.json",
            "--mode",
            "desktop_perf_acceptance",
            "--baseline-input",
            str(self.repo_root / "docs/validation-fixture/baseline.trace"),
            "--candidate-input",
            str(self.repo_root / "docs/validation-fixture/candidate.trace"),
            "--soak-iterations",
            "1",
            "--load-timeout-s",
            "30",
            "--workdir",
            str(scratch_dir),
        )
        self.assertEqual(report["mode"], "desktop_perf_acceptance")
        self.assertIn("desktop_perf_acceptance", report)

    def test_acceptance_baseline_rejects_negative_memory_limit(self) -> None:
        report_path = self.root / "acceptance-invalid-memory-limit.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/run_acceptance_baseline.py",
                "--memory-limit-mb",
                "-1",
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(
            "--memory-limit-mb must be zero or a positive integer",
            (proc.stderr or "") + (proc.stdout or ""),
        )

    def test_desktop_perf_acceptance_perf_only_skips_non_perf_steps(self) -> None:
        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-acceptance-linux-perf-only.json",
            "--mode",
            "desktop_perf_acceptance",
            "--acceptance-scope",
            "perf_only",
            "--baseline-input",
            str(self.repo_root / "docs/validation-fixture/baseline.trace"),
            "--candidate-input",
            str(self.repo_root / "docs/validation-fixture/candidate.trace"),
            "--soak-iterations",
            "1",
        )

        acceptance = report["desktop_perf_acceptance"]
        self.assertEqual(acceptance["acceptance_scope"], "perf_only")
        self.assertEqual(
            acceptance["skipped_steps"],
            ["compare", "repeat_export", "repro_repeat", "short_soak"],
        )
        self.assertEqual(report["desktop_perf"]["preview_mode"], "lightweight")
        self.assertGreater(report["desktop_perf"]["preview_seconds"], 0.0)
        self.assertGreater(report["desktop_perf"]["load_seconds"], 0.0)
        self.assertGreater(report["desktop_perf"]["pipeline_load_seconds"], 0.0)
        self.assertGreater(report["desktop_perf"]["rebuild_seconds"], 0.0)
        self.assertGreater(report["desktop_perf"]["pipeline_rebuild_seconds"], 0.0)
        self.assertGreaterEqual(
            report["desktop_perf"]["pipeline_rebuild_seconds"],
            report["desktop_perf"]["rebuild_seconds"],
        )
        self.assertGreater(report["desktop_perf"]["metric_seconds"], 0.0)
        self.assertGreater(report["desktop_perf"]["export_full_seconds"], 0.0)
        self.assertGreater(report["desktop_perf"]["export_clipped_seconds"], 0.0)
        self.assertFalse(report["desktop_perf"]["export_probe_skipped"])
        self.assertIsNone(report["desktop_perf"]["export_probe_reason"])
        self.assertEqual(report["desktop_perf"]["export_write_mode"], "source_backed_streaming")
        self.assertEqual(report["desktop_perf"]["export_clipped_write_mode"], "source_backed_streaming")
        self.assertEqual(report["export_contract_version"], "v2")
        self.assertEqual(report["desktop_perf"]["export_contract_version"], "v2")
        self.assertEqual(report["desktop_perf"]["index_build_mode"], "minimal")
        self.assertIn("load_stage_timings", report["desktop_perf"])
        self.assertIn("load_memory_snapshots", report["desktop_perf"])
        self.assertTrue(
            {
                "prs_Verify_seconds",
                "prs_Load_seconds",
                "prs_FeedChunk_seconds",
                "TraceDecodeSession.feed_seconds",
                "_decode_chunk_seconds",
                "align_events_seconds",
                "rb_Rebuild_seconds",
                "idx_Build_seconds",
                "load_seconds",
            }.issubset(set(report["desktop_perf"]["load_stage_timings"]))
        )
        self.assertEqual(
            report["desktop_perf"]["rebuild_seconds"],
            report["desktop_perf"]["load_stage_timings"]["rb_Rebuild_seconds"],
        )
        self.assertIn("prs_Load", report["desktop_perf"]["load_stage_completed"])
        self.assertIn("prs_Verify", report["desktop_perf"]["load_stage_completed"])
        self.assertIn("align_events", report["desktop_perf"]["load_stage_completed"])
        self.assertIn("rb_Rebuild", report["desktop_perf"]["load_stage_completed"])
        self.assertIn("idx_Build", report["desktop_perf"]["load_stage_completed"])
        self.assertIn("load_hotspot_summary", report["desktop_perf"])
        self.assertIn("load_latest_progress", report["desktop_perf"])
        self.assertIn("trust_tags_empty_reuse_count", report["desktop_perf"]["load_hotspot_summary"])
        self.assertIn("trust_tags_nonempty_count", report["desktop_perf"]["load_hotspot_summary"])
        self.assertEqual(report["desktop_perf"]["task_state_preview_seconds"], 0.0)
        self.assertEqual(report["desktop_perf"]["timed_load_role"], "baseline")
        self.assertIn(
            report["desktop_perf"]["event_table_first_page_source"],
            {"trace_window_scan", "package_index"},
        )
        self.assertIn(
            report["desktop_perf"]["lod2_first_window_source"],
            {"trace_window_scan", "package_index"},
        )
        self.assertIsNone(report["durations"]["compare_seconds"])
        self.assertIsNone(report["durations"]["repro_open_seconds"])
        self.assertGreater(report["durations"]["export_write_seconds"], 0.0)
        self.assertGreater(report["durations"]["export_normalize_seconds"], 0.0)
        self.assertGreater(report["durations"]["export_clipped_write_seconds"], 0.0)
        self.assertGreater(report["durations"]["export_clipped_normalize_seconds"], 0.0)
        self.assertGreater(report["durations"]["audit_seconds"], 0.0)
        self.assertIsNone(report["consistency"]["repeat_export_consistent"])
        self.assertIsNone(report["consistency"]["repro_repeat_consistent"])
        self.assertIsNone(report["consistency"]["compare_scope_consistent"])
        self.assertIsNone(report["consistency"]["short_soak_passed"])

    def test_perf_only_thin_mode_captures_success_load_breakdown_contract(self) -> None:
        import tool.run_acceptance_baseline as baseline_tool

        trace_path = write_scenario(self.root / "perf-only-thin-success.trace", name="basic", repeat=8)
        blocker_state: dict[str, object] = {
            "last_completed_stage": "init",
            "timings": {},
            "run_root": str(self.root),
        }

        desktop_perf = baseline_tool._capture_perf_only_desktop_perf(
            Path(trace_path),
            profile="smoke",
            cache_budget_mb=2.0,
            load_timeout_s=30.0,
            blocker_state=blocker_state,
        )
        load_breakdown = blocker_state.get("load_breakdown") or {}
        self.assertIsInstance(load_breakdown, dict)
        self.assertEqual(load_breakdown.get("index_build_mode"), "minimal")
        self.assertEqual(load_breakdown.get("parser_process_artifact_version"), "parser-process-artifact-v1")
        self.assertEqual(desktop_perf.get("parser_process_artifact_version"), "parser-process-artifact-v1")
        self.assertIsInstance(load_breakdown.get("parser_process_artifact"), dict)
        self.assertIsInstance(desktop_perf.get("parser_process_artifact"), dict)
        completed_stages = set(load_breakdown.get("completed_stages") or [])
        self.assertTrue(
            {"prs_Load", "prs_Verify", "align_events", "rb_Rebuild", "idx_Build"}.issubset(completed_stages)
        )
        stage_timings = load_breakdown.get("stage_timings") or {}
        self.assertIsInstance(stage_timings, dict)
        self.assertTrue(
            {
                "prs_Verify_seconds",
                "prs_Load_seconds",
                "prs_FeedChunk_seconds",
                "TraceDecodeSession.feed_seconds",
                "_decode_chunk_seconds",
                "align_events_seconds",
                "rb_Rebuild_seconds",
                "idx_Build_seconds",
            }.issubset(set(stage_timings))
        )
        self.assertGreater(desktop_perf.get("pipeline_load_seconds", 0.0), 0.0)
        self.assertGreater(desktop_perf.get("pipeline_rebuild_seconds", 0.0), 0.0)
        self.assertGreaterEqual(desktop_perf["pipeline_rebuild_seconds"], desktop_perf["rebuild_seconds"])
        self.assertEqual(desktop_perf["rebuild_seconds"], stage_timings["rb_Rebuild_seconds"])
        self.assertEqual(
            blocker_state["timings"]["pipeline_rebuild_seconds"],
            desktop_perf["pipeline_rebuild_seconds"],
        )
        latest_progress = load_breakdown.get("latest_progress") or {}
        progress_samples = load_breakdown.get("progress_samples") or []
        self.assertIsInstance(latest_progress, dict)
        self.assertTrue(latest_progress)
        self.assertTrue(progress_samples)
        hotspot_summary = load_breakdown.get("hotspot_summary") or {}
        self.assertIsInstance(hotspot_summary, dict)
        self.assertIn("dominant_object", hotspot_summary)
        self.assertIn(
            desktop_perf["event_table_first_page_source"],
            {"trace_window_scan", "package_index"},
        )
        self.assertIn(
            desktop_perf["lod2_first_window_source"],
            {"trace_window_scan", "package_index"},
        )
        self.assertFalse(desktop_perf["export_probe_skipped"])
        self.assertIsNone(desktop_perf["export_probe_reason"])
        self.assertEqual(desktop_perf["export_write_mode"], "source_backed_streaming")
        self.assertEqual(desktop_perf["export_clipped_write_mode"], "source_backed_streaming")
        self.assertGreater(desktop_perf["export_write_seconds"], 0.0)
        self.assertGreater(desktop_perf["export_normalize_seconds"], 0.0)
        self.assertGreater(desktop_perf["export_clipped_write_seconds"], 0.0)
        self.assertGreater(desktop_perf["export_clipped_normalize_seconds"], 0.0)
        self.assertGreater(desktop_perf["normalized_manifest_entries"], 0)

    def test_perf_only_blocker_artifact_records_export_write_stage(self) -> None:
        import tool.run_acceptance_baseline as baseline_tool

        trace_path = write_scenario(self.root / "perf-only-export-write-fail.trace", name="basic", repeat=8)
        blocker_state: dict[str, object] = {
            "current_stage": "init",
            "last_completed_stage": "init",
            "timings": {},
            "run_root": str(self.root),
        }

        with patch(
            "tool.run_acceptance_baseline.ExportService.export_WritePackage",
            return_value=err_result("EXPORT_FAILED", "synthetic export write failure"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                baseline_tool._capture_perf_only_desktop_perf(
                    Path(trace_path),
                    profile="smoke",
                    cache_budget_mb=2.0,
                    load_timeout_s=30.0,
                    blocker_state=blocker_state,
                )

        artifact = baseline_tool._build_blocker_artifact(
            mode="desktop_perf_acceptance",
            acceptance_scope="perf_only",
            baseline_path=Path(trace_path),
            candidate_path=Path(trace_path),
            workdir=str(self.root),
            blocker_state=blocker_state,
            exc=raised.exception,
        )
        self.assertEqual(artifact["blocked_stage"], "export_write")
        self.assertEqual(artifact["last_completed_stage"], "lod2_query")

    def test_perf_only_blocker_artifact_records_export_normalize_stage(self) -> None:
        import tool.run_acceptance_baseline as baseline_tool

        trace_path = write_scenario(self.root / "perf-only-export-normalize-fail.trace", name="basic", repeat=8)
        blocker_state: dict[str, object] = {
            "current_stage": "init",
            "last_completed_stage": "init",
            "timings": {},
            "run_root": str(self.root),
        }

        with patch(
            "tool.run_acceptance_baseline.ExportService.export_NormalizePackagePerfOnly",
            return_value=err_result("NORMALIZE_FAILED", "synthetic normalize failure"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                baseline_tool._capture_perf_only_desktop_perf(
                    Path(trace_path),
                    profile="smoke",
                    cache_budget_mb=2.0,
                    load_timeout_s=30.0,
                    blocker_state=blocker_state,
                )

        artifact = baseline_tool._build_blocker_artifact(
            mode="desktop_perf_acceptance",
            acceptance_scope="perf_only",
            baseline_path=Path(trace_path),
            candidate_path=Path(trace_path),
            workdir=str(self.root),
            blocker_state=blocker_state,
            exc=raised.exception,
        )
        self.assertEqual(artifact["blocked_stage"], "export_normalize_sidecar")
        self.assertEqual(artifact["last_completed_stage"], "export_write")

    def test_perf_only_success_uses_fast_normalize_path(self) -> None:
        import tool.run_acceptance_baseline as baseline_tool

        trace_path = write_scenario(self.root / "perf-only-fast-normalize-success.trace", name="basic", repeat=8)
        blocker_state: dict[str, object] = {
            "last_completed_stage": "init",
            "timings": {},
            "run_root": str(self.root),
        }
        fast_normalize_calls = 0
        original_fast_normalize = baseline_tool.ExportService.export_NormalizePackagePerfOnly

        def _wrapped_fast_normalize(self, path_or_stream: str, *, meta: dict[str, object] | None, manifest: dict[str, object] | None):
            nonlocal fast_normalize_calls
            fast_normalize_calls += 1
            return original_fast_normalize(self, path_or_stream, meta=meta, manifest=manifest)

        with patch(
            "tool.run_acceptance_baseline.ExportService.export_NormalizePackage",
            side_effect=AssertionError("perf_only should not reopen the full package normalize path"),
        ):
            with patch(
                "tool.run_acceptance_baseline.ExportService.export_NormalizePackagePerfOnly",
                new=_wrapped_fast_normalize,
            ):
                desktop_perf = baseline_tool._capture_perf_only_desktop_perf(
                    Path(trace_path),
                    profile="smoke",
                    cache_budget_mb=2.0,
                    load_timeout_s=30.0,
                    blocker_state=blocker_state,
                )

        self.assertGreater(desktop_perf["normalized_manifest_entries"], 0)
        self.assertEqual(fast_normalize_calls, 2)

    def test_non_local_disk_fs_type_variants_are_recognized(self) -> None:
        self.assertFalse(_is_local_disk_fs_type("vmhgfs"))
        self.assertFalse(_is_local_disk_fs_type("vmhgfs-fuse"))
        self.assertFalse(_is_local_disk_fs_type("fuse.vmhgfs"))
        self.assertFalse(_is_local_disk_fs_type("fuse.vmhgfs-fuse"))
        self.assertTrue(_is_local_disk_fs_type("ext4"))

    def test_acceptance_baseline_writes_blocker_artifact_on_failure(self) -> None:
        report_path = self.root / "acceptance-report.json"
        blocker_path = self.root / "acceptance-blocker.json"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/run_acceptance_baseline.py",
                "--mode",
                "desktop_perf_acceptance",
                "--acceptance-scope",
                "perf_only",
                "--baseline-input",
                str(self.root / "missing-baseline.trace"),
                "--candidate-input",
                str(self.root / "missing-candidate.trace"),
                "--blocker-output",
                str(blocker_path),
                "--memory-limit-mb",
                "256",
                "--output",
                str(report_path),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr or proc.stdout)
        blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
        self.assertEqual(blocker["mode"], "desktop_perf_acceptance")
        self.assertEqual(blocker["acceptance_scope"], "perf_only")
        self.assertEqual(blocker["export_contract_version"], "v2")
        self.assertIn(blocker["status"], {"failed", "timeout"})
        self.assertIn("last_completed_stage", blocker)
        self.assertIn("load_breakdown", blocker)
        self.assertIn("memory_guard", blocker)
        self.assertEqual(blocker["memory_guard"]["requested_limit_mb"], 256)
        self.assertIn("error", blocker)

    def test_blocker_artifact_includes_decode_failure_hotspot_summary(self) -> None:
        import tool.run_acceptance_baseline as baseline_tool

        trace_path = self.root / "decode-fail.trace"
        baseline = write_scenario(trace_path, name="basic", repeat=8)

        def isolated_parse_memory_error(_self, _source, *, artifact_policy=None, **_kwargs):
            progress_path = Path((artifact_policy or {})["progress_path"])
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "stage": "prs_Load",
                    "status": "progress",
                    "payload": {
                        "status": "progress",
                        "stage_timings": {"prs_Load_seconds": 1.234},
                        "chunk_progress": {"decoded_chunk_count": 1},
                        "hotspot_summary": {
                            "dominant_object": "event_structures",
                            "last_progress": {"decoded_chunk_count": 1},
                        },
                    },
                },
                {
                    "stage": "prs_Load",
                    "status": "failed",
                    "payload": {
                        "status": "failed",
                        "stage_timings": {"prs_Load_seconds": 9.876},
                        "chunk_progress": {"decoded_chunk_count": 3},
                        "hotspot_summary": {
                            "failure": "MemoryError",
                            "dominant_object": "event_structures",
                            "partial_chunk": {"decoded_records": 42},
                            "last_progress": {"decoded_chunk_count": 3},
                        },
                    },
                },
            ]
            progress_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            raise MemoryError()

        blocker_state: dict[str, object] = {
            "last_completed_stage": "init",
            "timings": {},
            "run_root": str(self.root),
        }

        with patch("tool.run_acceptance_baseline.ParserProcessAgent.parse_rebuild", isolated_parse_memory_error):
            with self.assertRaises(MemoryError) as raised:
                baseline_tool._capture_perf_only_desktop_perf(
                    Path(baseline),
                    profile="smoke",
                    cache_budget_mb=2.0,
                    load_timeout_s=30.0,
                    blocker_state=blocker_state,
                )

        artifact = baseline_tool._build_blocker_artifact(
            mode="desktop_perf_acceptance",
            acceptance_scope="perf_only",
            baseline_path=Path(baseline),
            candidate_path=Path(baseline),
            workdir=None,
            blocker_state=blocker_state,
            exc=raised.exception,
        )
        load_breakdown = artifact.get("load_breakdown") or {}
        self.assertIsInstance(load_breakdown, dict)
        hotspot = load_breakdown.get("hotspot_summary") or {}
        self.assertIsInstance(hotspot, dict)
        self.assertEqual(hotspot.get("failure"), "MemoryError")
        self.assertIn("partial_chunk", hotspot)
        self.assertIn("last_progress", hotspot)
        self.assertIsInstance(hotspot.get("last_progress"), dict)
        self.assertGreater(int(hotspot["last_progress"].get("decoded_chunk_count", 0)), 0)
        self.assertTrue(load_breakdown.get("progress_samples"), "expected progress_samples to capture failure progress")

    def test_perf_only_isolated_worker_returns_desktop_perf_payload(self) -> None:
        import tool.run_acceptance_baseline as baseline_tool

        trace_path = self.root / "perf-only-isolated.trace"
        baseline = write_scenario(trace_path, name="basic", repeat=8)
        blocker_state: dict[str, object] = {
            "last_completed_stage": "init",
            "timings": {},
            "run_root": str(self.root),
            "memory_guard": baseline_tool._requested_memory_guard_placeholder(0),
        }

        desktop_perf, memory_report, memory_guard = baseline_tool._capture_perf_only_desktop_perf_isolated(
            Path(baseline),
            profile="smoke",
            cache_budget_mb=2.0,
            load_timeout_s=30.0,
            requested_memory_limit_mb=0,
            blocker_state=blocker_state,
            run_root=self.root,
        )

        self.assertGreater(desktop_perf["preview_seconds"], 0.0)
        self.assertGreater(desktop_perf["load_seconds"], 0.0)
        self.assertGreater(desktop_perf["event_table_first_page_count"], 0)
        self.assertGreater(desktop_perf["lod2_first_window_count"], 0)
        self.assertGreater(memory_report["peak_memory_mb"], 0.0)
        self.assertEqual(memory_guard["requested_limit_mb"], 0)
        self.assertEqual(blocker_state["last_completed_stage"], "export_clipped_normalize_sidecar")
        self.assertFalse(desktop_perf["export_probe_skipped"])
        self.assertGreater(desktop_perf["export_write_seconds"], 0.0)
        self.assertGreater(desktop_perf["export_normalize_seconds"], 0.0)
        load_breakdown = blocker_state.get("load_breakdown") or {}
        self.assertIsInstance(load_breakdown, dict)
        self.assertTrue(load_breakdown.get("progress_samples"))
        self.assertIn("prs_Verify", load_breakdown.get("completed_stages") or [])

    def test_perf_only_isolated_worker_failure_keeps_progress_for_blocker_artifact(self) -> None:
        import tool.run_acceptance_baseline as baseline_tool

        trace_path = self.root / "fatal-memoryerror.trace"
        baseline = write_scenario(trace_path, name="basic", repeat=8)
        progress_path = self.root / "perf_only_worker_progress.json"
        progress_path.write_text(
            json.dumps(
                {
                    "status": "running",
                    "last_completed_stage": "preview",
                    "timings": {
                        "preview_seconds": 0.123,
                        "load_elapsed_seconds": 9.876,
                    },
                    "load_breakdown": {
                        "current_stage": "prs_Verify",
                        "last_completed_stage": None,
                        "completed_stages": [],
                        "stage_timings": {"prs_Load_seconds": 9.876},
                        "memory_snapshots": [],
                        "latest_progress": {"decoded_chunk_count": 3},
                        "progress_samples": [{"decoded_chunk_count": 1}, {"decoded_chunk_count": 3}],
                        "hotspot_summary": {
                            "failure": "MemoryError",
                            "dominant_object": "event_structures",
                            "partial_chunk": {"decoded_records": 42},
                            "last_progress": {"decoded_chunk_count": 3},
                        },
                    },
                    "export_probe": {},
                    "memory_guard": baseline_tool._requested_memory_guard_placeholder(256),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        class FakeProcess:
            def __init__(self) -> None:
                self.returncode = 91
                self._poll_count = 0

            def poll(self):
                self._poll_count += 1
                return None if self._poll_count == 1 else self.returncode

            def communicate(self, timeout=None):
                return ("", "Fatal Python error: Cannot recover from MemoryErrors while normalizing exceptions.")

            def kill(self) -> None:
                return None

        blocker_state: dict[str, object] = {
            "last_completed_stage": "init",
            "timings": {},
            "run_root": str(self.root),
            "memory_guard": baseline_tool._requested_memory_guard_placeholder(256),
        }

        with patch("tool.run_acceptance_baseline.subprocess.Popen", return_value=FakeProcess()):
            with patch("tool.run_acceptance_baseline.time.sleep", return_value=None):
                with self.assertRaises(RuntimeError) as raised:
                    baseline_tool._capture_perf_only_desktop_perf_isolated(
                        Path(baseline),
                        profile="smoke",
                        cache_budget_mb=2.0,
                        load_timeout_s=30.0,
                        requested_memory_limit_mb=256,
                        blocker_state=blocker_state,
                        run_root=self.root,
                    )

        blocker = baseline_tool._build_blocker_artifact(
            mode="desktop_perf_acceptance",
            acceptance_scope="perf_only",
            baseline_path=Path(baseline),
            candidate_path=Path(baseline),
            workdir=None,
            blocker_state=blocker_state,
            exc=raised.exception,
        )
        self.assertEqual(blocker["error"]["type"], "SubprocessError")
        self.assertEqual(blocker["load_breakdown"]["hotspot_summary"]["failure"], "MemoryError")
        self.assertEqual(blocker["load_breakdown"]["hotspot_summary"]["dominant_object"], "event_structures")
        self.assertIn("partial_chunk", blocker["load_breakdown"]["hotspot_summary"])
        self.assertEqual(blocker["timings"]["load_elapsed_seconds"], 9.876)

    def test_desktop_perf_acceptance_report_marks_input_scope_and_limitations(self) -> None:
        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-acceptance-linux-limitations.json",
            "--mode",
            "desktop_perf_acceptance",
            "--baseline-input",
            str(self.repo_root / "docs/validation-fixture/baseline.trace"),
            "--candidate-input",
            str(self.repo_root / "docs/validation-fixture/candidate.trace"),
            "--soak-iterations",
            "1",
        )

        acceptance = report["desktop_perf_acceptance"]
        self.assertEqual(acceptance["input_scope"], "linux_provided_fixture")
        self.assertGreater(acceptance["input_bytes"], 0)
        self.assertIn("input_lt_1gb", acceptance["limitations"])
        self.assertIn("windows_not_covered", acceptance["limitations"])
        self.assertIn("windows_linux_consistency_not_covered", acceptance["limitations"])
        self.assertIn("long_duration_stability_not_covered", acceptance["limitations"])

    def test_desktop_perf_acceptance_report_supports_windows_platform_label(self) -> None:
        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-acceptance-windows.json",
            "--mode",
            "desktop_perf_acceptance",
            "--platform-label",
            "windows",
            "--baseline-input",
            str(self.repo_root / "docs/validation-fixture/baseline.trace"),
            "--candidate-input",
            str(self.repo_root / "docs/validation-fixture/candidate.trace"),
            "--soak-iterations",
            "1",
        )

        acceptance = report["desktop_perf_acceptance"]
        self.assertEqual(report["mode"], "desktop_perf_acceptance")
        self.assertEqual(acceptance["platform"], "windows")
        self.assertEqual(acceptance["input_scope"], "windows_provided_fixture")
        self.assertIn("linux_not_covered", acceptance["limitations"])
        self.assertNotIn("windows_not_covered", acceptance["limitations"])
        self.assertFalse(acceptance["formal_input"]["formal_1gb_verified"])
        self.assertEqual(acceptance["formal_input"]["input_provenance"], "real_external")

    def test_desktop_perf_acceptance_detects_synthetic_padded_input(self) -> None:
        synthetic_trace = self.root / "synthetic-large.trace"
        build_report = self.root / "synthetic-large-report.json"
        build_proc = subprocess.run(
            [
                sys.executable,
                "tool/build_formal_large_input.py",
                "--seed-input",
                str(self.repo_root / "docs/validation-fixture/baseline.trace"),
                "--output",
                str(synthetic_trace),
                "--target-size-bytes",
                str(4 * 1024 * 1024),
                "--filler-chunk-mb",
                "1",
                "--report",
                str(build_report),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(build_proc.returncode, 0, build_proc.stderr or build_proc.stdout)

        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-acceptance-synthetic.json",
            "--mode",
            "desktop_perf_acceptance",
            "--baseline-input",
            str(synthetic_trace),
            "--candidate-input",
            str(synthetic_trace),
            "--soak-iterations",
            "1",
        )

        acceptance = report["desktop_perf_acceptance"]
        self.assertEqual(acceptance["formal_input"]["input_provenance"], "synthetic_padded")
        self.assertEqual(acceptance["formal_input"]["baseline_input_provenance"], "synthetic_padded")
        self.assertEqual(acceptance["formal_input"]["candidate_input_provenance"], "synthetic_padded")
        self.assertFalse(acceptance["formal_input"]["formal_1gb_verified"])
        self.assertIn("synthetic_padded_input", acceptance["limitations"])

    def test_public_rtos_large_input_builder_converts_nuttx_systrace_and_marks_prevalidation_provenance(self) -> None:
        seed_systrace = self.root / "nuttx-seed.systrace"
        seed_systrace.write_text(
            "\n".join(
                [
                    "worker-7 [0] 1.000000000: sys_sem_wait()",
                    "worker-7 [0] 1.000050000: sched_switch: prev_comm=worker prev_pid=7 prev_state=S ==> next_comm=idle next_pid=0",
                    "idle-0 [0] 1.000060000: irq_handler_entry: irq=5",
                    "idle-0 [0] 1.000090000: irq_handler_exit: irq=5",
                    "worker-7 [0] 1.000120000: sched_waking: comm=worker pid=7 target_cpu=0",
                    "idle-0 [0] 1.000140000: sched_switch: prev_comm=idle prev_pid=0 prev_state=R ==> next_comm=worker next_pid=7",
                    "worker-7 [0] 1.000200000: sys_sem_wait -> 0",
                    "worker-7 [0] 1.000300000: sys_sem_post()",
                    "worker-7 [0] 1.000330000: sys_sem_post -> 0",
                    "creator-9 [1] 1.000400000: sched_wakeup_new: comm=helper pid=9 target_cpu=1",
                ]
            ),
            encoding="utf-8",
        )

        baseline_trace = self.root / "public-rtos-baseline.trace"
        candidate_trace = self.root / "public-rtos-candidate.trace"
        build_report = self.root / "public-rtos-build-report.json"
        build_proc = subprocess.run(
            [
                sys.executable,
                "tool/build_public_rtos_large_input.py",
                "--seed-systrace",
                str(seed_systrace),
                "--baseline-output",
                str(baseline_trace),
                "--candidate-output",
                str(candidate_trace),
                "--target-size-bytes",
                str(4 * 1024 * 1024),
                "--real-seed-target-bytes",
                str(128 * 1024),
                "--report",
                str(build_report),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(build_proc.returncode, 0, build_proc.stderr or build_proc.stdout)

        build_payload = json.loads(build_report.read_text(encoding="utf-8"))
        self.assertEqual(build_payload["sample_profile"], "public_rtos_prevalidation_large_input")
        self.assertEqual(build_payload["public_source"]["trace_family"], "Apache NuttX task trace / systrace")
        self.assertTrue(build_payload["outputs"]["baseline"]["output_size_bytes"] >= 4 * 1024 * 1024)

        decode_proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys;"
                    "from pathlib import Path;"
                    "sys.path.insert(0, str(Path.cwd()));"
                    "from parser import decode_trace;"
                    "result=decode_trace(Path(sys.argv[1]));"
                    "assert result.ok, (result.code, result.message);"
                    "names={event.event_name for event in result.data['events']};"
                    "print(json.dumps(sorted(names)))"
                ),
                str(baseline_trace),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(decode_proc.returncode, 0, decode_proc.stderr or decode_proc.stdout)
        event_names = set(json.loads(decode_proc.stdout))
        self.assertTrue({"TASK_READY", "TASK_BLOCK", "TASK_WAKEUP", "TASK_DISPATCH", "CTX_SWITCH", "IRQ_ENTER", "IRQ_EXIT", "SYNC_TRY", "SYNC_LOCK", "SYNC_UNLOCK"}.issubset(event_names))

        report = self._run_acceptance_baseline(
            self.root / "desktop-perf-acceptance-public-rtos.json",
            "--mode",
            "desktop_perf_acceptance",
            "--baseline-input",
            str(baseline_trace),
            "--candidate-input",
            str(candidate_trace),
            "--soak-iterations",
            "1",
        )
        acceptance = report["desktop_perf_acceptance"]
        self.assertEqual(acceptance["formal_input"]["input_provenance"], "public_rtos_seeded_padded")
        self.assertEqual(acceptance["formal_input"]["baseline_input_provenance"], "public_rtos_seeded_padded")
        self.assertEqual(acceptance["formal_input"]["candidate_input_provenance"], "public_rtos_seeded_padded")
        self.assertFalse(acceptance["formal_input"]["formal_1gb_verified"])
        self.assertIn("public_rtos_seeded_padded_input", acceptance["limitations"])

    def test_public_rtos_large_input_builder_supports_dense_tier_with_non_mirrored_candidate(self) -> None:
        seed_systrace = self.root / "nuttx-dense.systrace"
        seed_systrace.write_text(
            "\n".join(
                [
                    "worker-7 [0] 1.000000000: sys_sem_wait()",
                    "worker-7 [0] 1.000050000: sched_switch: prev_comm=worker prev_pid=7 prev_state=S ==> next_comm=idle next_pid=0",
                    "idle-0 [0] 1.000060000: irq_handler_entry: irq=5",
                    "idle-0 [0] 1.000090000: irq_handler_exit: irq=5",
                    "worker-7 [0] 1.000120000: sched_waking: comm=worker pid=7 target_cpu=0",
                    "idle-0 [0] 1.000140000: sched_switch: prev_comm=idle prev_pid=0 prev_state=R ==> next_comm=worker next_pid=7",
                    "worker-7 [0] 1.000200000: sys_sem_wait -> 0",
                    "worker-7 [0] 1.000300000: sys_sem_post()",
                    "worker-7 [0] 1.000330000: sys_sem_post -> 0",
                    "creator-9 [1] 1.000400000: sched_wakeup_new: comm=helper pid=9 target_cpu=1",
                ]
            ),
            encoding="utf-8",
        )
        baseline_trace = self.root / "public-rtos-dense-baseline.trace"
        candidate_trace = self.root / "public-rtos-dense-candidate.trace"
        build_report = self.root / "public-rtos-dense-build-report.json"
        build_proc = subprocess.run(
            [
                sys.executable,
                "tool/build_public_rtos_large_input.py",
                "--seed-systrace",
                str(seed_systrace),
                "--sample-tier",
                "dense",
                "--baseline-output",
                str(baseline_trace),
                "--candidate-output",
                str(candidate_trace),
                "--target-size-bytes",
                str(4 * 1024 * 1024),
                "--real-seed-target-bytes",
                str(8 * 1024 * 1024),
                "--report",
                str(build_report),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(build_proc.returncode, 0, build_proc.stderr or build_proc.stdout)
        build_payload = json.loads(build_report.read_text(encoding="utf-8"))
        self.assertEqual(build_payload["sample_tier"], "dense")
        self.assertEqual(DENSE_REAL_SEED_TARGET_BYTES, 128 * 1024 * 1024)
        self.assertEqual(build_payload["derivation"]["candidate_mode"], "timestamp_jitter")
        self.assertFalse(build_payload["outputs"]["candidate"]["mirrors_baseline"])
        self.assertNotEqual(
            build_payload["outputs"]["baseline"]["sha256"],
            build_payload["outputs"]["candidate"]["sha256"],
        )

    def test_google_cluster_external_input_builder_emits_loadable_real_external_trace(self) -> None:
        source_path = self.root / "instance_events-000000000000.json.gz"
        records = [
            {
                "time": "1000",
                "type": "0",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "0",
            },
            {
                "time": "1100",
                "type": "3",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "9001",
            },
            {
                "time": "1200",
                "type": "1",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "0",
            },
            {
                "time": "1300",
                "type": "2",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "0",
            },
            {
                "time": "1400",
                "type": "3",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "9001",
            },
            {
                "time": "1500",
                "type": "6",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "9001",
            },
        ]
        with gzip.open(source_path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

        output_trace = self.root / "google-cluster.trace"
        output_report = self.root / "google-cluster-report.json"
        cache_dir = self.root / "cache"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/build_google_cluster_external_input.py",
                "--source-url",
                source_path.as_uri(),
                "--output",
                str(output_trace),
                "--cache-dir",
                str(cache_dir),
                "--report",
                str(output_report),
                "--target-size-bytes",
                str(1024 * 1024),
                "--allow-short-output",
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

        report = json.loads(output_report.read_text(encoding="utf-8"))
        self.assertEqual(report["sample_profile"], "google_cluster_real_external_formal_input")
        self.assertEqual(report["output"]["expected_input_provenance"], "real_external")
        self.assertEqual(report["conversion"]["source_type_counts"]["SUBMIT"], 1)
        self.assertEqual(report["conversion"]["source_type_counts"]["SCHEDULE"], 2)

        decode_proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys;"
                    "from pathlib import Path;"
                    "sys.path.insert(0, str(Path.cwd()));"
                    "from parser import decode_trace;"
                    "result=decode_trace(Path(sys.argv[1]));"
                    "assert result.ok, (result.code, result.message);"
                    "names={event.event_name for event in result.data['events']};"
                    "print(json.dumps(sorted(names)))"
                ),
                str(output_trace),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(decode_proc.returncode, 0, decode_proc.stderr or decode_proc.stdout)
        event_names = set(json.loads(decode_proc.stdout))
        self.assertTrue(
            {"TASK_READY", "TASK_DISPATCH", "CTX_SWITCH", "TASK_BLOCK", "TASK_WAKEUP", "TASK_EXIT"}.issubset(
                event_names
            )
        )
        self.assertEqual(_trace_input_provenance(output_trace), "real_external")

    def test_google_cluster_external_input_builder_supports_opaque_external_filler(self) -> None:
        source_path = self.root / "instance_events-000000000000.json.gz"
        records = [
            {
                "time": "1000",
                "type": "0",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "0",
            },
            {
                "time": "1100",
                "type": "3",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "9001",
            },
            {
                "time": "1500",
                "type": "6",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "9001",
            },
        ]
        with gzip.open(source_path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

        output_trace = self.root / "google-cluster-opaque.trace"
        output_report = self.root / "google-cluster-opaque-report.json"
        cache_dir = self.root / "cache-opaque"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/build_google_cluster_external_input.py",
                "--source-url",
                source_path.as_uri(),
                "--output",
                str(output_trace),
                "--cache-dir",
                str(cache_dir),
                "--report",
                str(output_report),
                "--target-size-bytes",
                str(32 * 1024),
                "--max-source-records",
                "2",
                "--opaque-filler-from-source",
                "--opaque-filler-chunk-bytes",
                "1024",
                "--allow-repeat-opaque-source",
                "--no-raw-record-event",
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

        report = json.loads(output_report.read_text(encoding="utf-8"))
        opaque_filler = report["conversion"]["opaque_external_filler"]
        self.assertTrue(opaque_filler["enabled"])
        self.assertGreater(opaque_filler["chunks_appended"], 0)
        self.assertGreaterEqual(report["output"]["output_size_bytes"], 32 * 1024)
        self.assertEqual(report["output"]["expected_input_provenance"], "real_external")

        decode_proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys;"
                    "from pathlib import Path;"
                    "sys.path.insert(0, str(Path.cwd()));"
                    "from parser import decode_trace;"
                    "result=decode_trace(Path(sys.argv[1]));"
                    "assert result.ok, (result.code, result.message);"
                    "print(json.dumps({'event_names': sorted({event.event_name for event in result.data['events']}), 'warnings': result.data['warnings']}))"
                ),
                str(output_trace),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(decode_proc.returncode, 0, decode_proc.stderr or decode_proc.stdout)
        decoded_payload = json.loads(decode_proc.stdout)
        self.assertTrue({"TASK_READY", "TASK_DISPATCH", "CTX_SWITCH"}.issubset(set(decoded_payload["event_names"])))
        self.assertEqual(decoded_payload["warnings"], [])
        self.assertEqual(_trace_input_provenance(output_trace), "real_external")

    def test_google_cluster_external_input_builder_sanitizes_int64_max_timestamp(self) -> None:
        source_path = self.root / "instance_events-000000000000.json.gz"
        records = [
            {
                "time": str((1 << 63) - 1),
                "type": "0",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "0",
            },
            {
                "time": "1100",
                "type": "3",
                "collection_id": "10001",
                "instance_index": "1",
                "priority": "120",
                "machine_id": "9001",
            },
        ]
        with gzip.open(source_path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

        output_trace = self.root / "google-cluster-sanitized.trace"
        output_report = self.root / "google-cluster-sanitized-report.json"
        cache_dir = self.root / "cache-sanitized"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/build_google_cluster_external_input.py",
                "--source-url",
                source_path.as_uri(),
                "--output",
                str(output_trace),
                "--cache-dir",
                str(cache_dir),
                "--report",
                str(output_report),
                "--target-size-bytes",
                str(8 * 1024),
                "--allow-short-output",
                "--no-raw-record-event",
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

        report = json.loads(output_report.read_text(encoding="utf-8"))
        self.assertEqual(report["conversion"]["timestamp_sanitized_count"], 1)

        decode_proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys;"
                    "from pathlib import Path;"
                    "sys.path.insert(0, str(Path.cwd()));"
                    "from parser import decode_trace;"
                    "result=decode_trace(Path(sys.argv[1]));"
                    "assert result.ok, (result.code, result.message);"
                    "print(json.dumps(max(event.timestamp_raw for event in result.data['events'])))"
                ),
                str(output_trace),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(decode_proc.returncode, 0, decode_proc.stderr or decode_proc.stdout)
        self.assertLess(float(json.loads(decode_proc.stdout)), float((1 << 63) - 1))

    def test_google_cluster_external_input_builder_can_stop_when_target_size_is_reached(self) -> None:
        source_path = self.root / "instance_events-000000000000.json.gz"
        records = []
        for index in range(5000):
            records.append(
                {
                    "time": str(1000 + index),
                    "type": "0",
                    "collection_id": str(10000 + index),
                    "instance_index": "1",
                    "priority": "120",
                    "machine_id": "0",
                }
            )
        with gzip.open(source_path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

        output_trace = self.root / "google-cluster-stop.trace"
        output_report = self.root / "google-cluster-stop-report.json"
        cache_dir = self.root / "cache-stop"
        target_size_bytes = 8 * 1024
        proc = subprocess.run(
            [
                sys.executable,
                "tool/build_google_cluster_external_input.py",
                "--source-url",
                source_path.as_uri(),
                "--output",
                str(output_trace),
                "--cache-dir",
                str(cache_dir),
                "--report",
                str(output_report),
                "--target-size-bytes",
                str(target_size_bytes),
                "--stop-on-target-size-reached",
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

        report = json.loads(output_report.read_text(encoding="utf-8"))
        self.assertTrue(report["conversion"]["stop_on_target_size_reached"])
        self.assertTrue(report["conversion"]["target_size_reached_during_semantic_conversion"])
        self.assertEqual(report["output"]["stop_reason"], "target_size_reached")
        self.assertGreaterEqual(report["output"]["output_size_bytes"], target_size_bytes)
        self.assertLess(report["source_shards"][0]["record_count"], len(records))
        self.assertTrue(report["source_shards"][0]["target_size_reached"])
        self.assertEqual(_trace_input_provenance(output_trace), "real_external")

        decode_proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys;"
                    "from pathlib import Path;"
                    "sys.path.insert(0, str(Path.cwd()));"
                    "from parser import decode_trace;"
                    "result=decode_trace(Path(sys.argv[1]));"
                    "assert result.ok, (result.code, result.message);"
                    "print(len(result.data['events']))"
                ),
                str(output_trace),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(decode_proc.returncode, 0, decode_proc.stderr or decode_proc.stdout)
        self.assertGreater(int(decode_proc.stdout.strip()), 0)

    def test_google_cluster_external_input_builder_supports_core_bucket_normalization(self) -> None:
        source_path = self.root / "instance_events-000000000000.json.gz"
        records = []
        for index in range(300):
            records.append(
                {
                    "time": str(1000 + index),
                    "type": "3" if index % 3 == 0 else "0",
                    "collection_id": str(20000 + index),
                    "instance_index": "1",
                    "priority": "120",
                    "machine_id": str(9000 + index),
                }
            )
        with gzip.open(source_path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

        output_trace = self.root / "google-cluster-core-bucket.trace"
        output_report = self.root / "google-cluster-core-bucket-report.json"
        cache_dir = self.root / "cache-core-bucket"
        proc = subprocess.run(
            [
                sys.executable,
                "tool/build_google_cluster_external_input.py",
                "--source-url",
                source_path.as_uri(),
                "--output",
                str(output_trace),
                "--cache-dir",
                str(cache_dir),
                "--report",
                str(output_report),
                "--target-size-bytes",
                str(64 * 1024),
                "--allow-short-output",
                "--core-bucket-count",
                "8",
                "--chunk-size",
                "16",
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

        report = json.loads(output_report.read_text(encoding="utf-8"))
        self.assertEqual(report["conversion"]["core_bucket_count"], 8)
        self.assertEqual(report["conversion"]["machine_core_policy"], "bucketed_modulo")
        self.assertLessEqual(report["conversion"]["core_count"], 8)
        self.assertEqual(_trace_input_provenance(output_trace), "real_external")

    def test_external_validation_fixture_and_baseline_compare_scripts(self) -> None:
        fixture_dir = self.root / "fixture"
        report_a = self.root / "report-a.json"
        report_b = self.root / "report-b.json"

        fixture = subprocess.run(
            [
                sys.executable,
                "tool/prepare_external_validation_fixture.py",
                "--output-dir",
                str(fixture_dir),
                "--repeat",
                "4",
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(fixture.returncode, 0, fixture.stderr or fixture.stdout)
        manifest = json.loads((fixture_dir / "validation_fixture_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["repeat"], 4)

        repo_root = Path(__file__).resolve().parents[2]
        for report_path in (report_a, report_b):
            report = self._run_acceptance_baseline(
                report_path,
                "--baseline-input",
                str(fixture_dir / "baseline.trace"),
                "--candidate-input",
                str(fixture_dir / "candidate.trace"),
                "--soak-iterations",
                "1",
            )
            self.assertEqual(report["inputs"]["source"], "provided")

        compare = subprocess.run(
            [
                sys.executable,
                "tool/compare_acceptance_baselines.py",
                "--left",
                str(report_a),
                "--right",
                str(report_b),
                "--output",
                str(self.root / "compare-output.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(compare.returncode, 0, compare.stderr or compare.stdout)
        payload = json.loads(compare.stdout)
        self.assertTrue(payload["match"])
        written = json.loads((self.root / "compare-output.json").read_text(encoding="utf-8"))
        self.assertEqual(payload, written)

    def test_compare_normalized_packages_script_can_write_output_file(self) -> None:
        controller, baseline_id, _ = self._load_pair(repeat=4)
        self._export_package(controller, baseline_id, self.root / "package-a")
        self._export_package(controller, baseline_id, self.root / "package-b")

        compare = subprocess.run(
            [
                sys.executable,
                "tool/compare_normalized_packages.py",
                "--left",
                str(self.root / "package-a"),
                "--right",
                str(self.root / "package-b"),
                "--output",
                str(self.root / "normalized-compare.json"),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(compare.returncode, 0, compare.stderr or compare.stdout)
        payload = json.loads(compare.stdout)
        self.assertTrue(payload["match"])
        written = json.loads((self.root / "normalized-compare.json").read_text(encoding="utf-8"))
        self.assertEqual(payload, written)

    def test_compare_normalized_packages_ignores_cross_platform_style_metadata(self) -> None:
        controller, baseline_id, _ = self._load_pair(repeat=4)
        self._export_package(controller, baseline_id, self.root / "package-a")
        self._export_package(
            controller,
            baseline_id,
            self.root / "package-b",
            context_payload={
                "selection": {},
                "zoom_level": 1.0,
                "focused_view": None,
            },
            export_kwargs={
                "run_batch_id": "windows-formal-batch",
                "version_id": "windows-formal-export",
                "experiment_params": {"profile": "acceptance-windows", "repeatable": True},
            },
        )
        self._rewrite_dictionary_with_same_semantics(self.root / "package-b")

        compare = subprocess.run(
            [
                sys.executable,
                "tool/compare_normalized_packages.py",
                "--left",
                str(self.root / "package-a"),
                "--right",
                str(self.root / "package-b"),
                "--output",
                str(self.root / "normalized-cross-platform-compare.json"),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(compare.returncode, 0, compare.stderr or compare.stdout)
        payload = json.loads(compare.stdout)
        self.assertTrue(payload["match"])
        self.assertEqual(payload["mismatched_sections"], [])


if __name__ == "__main__":
    unittest.main()

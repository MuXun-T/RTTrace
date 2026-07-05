from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.clipped_completeness import (
    build_analysis_snapshot,
    build_anchor_rows,
    collect_required_event_refs_by_category,
    compare_snapshots,
)
from desktop.services import ExportService, PARSER_VERSION, ReproService, WorkspaceController
from metric.core import MetricConfig, alert_Evaluate, diag_Generate, metric_Compute, metric_Ingest, metric_Init
from parser import encode_trace, load_dataset
from parser.result import err_result
from spec.events import event_id_for
from spec.io import serialize
from spec.schema_loader import load_dictionary
import tool.run_clipped_completeness_check as clipped_cli
from tool.run_clipped_completeness_check import main as run_clipped_completeness_cli


class ClippedTraceCompletenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_clipped_trace_matches_full_alert_parameters(self) -> None:
        report, full_snapshot, clipped_snapshot = self._run_completeness()

        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(report["alert_completeness"]["alert_completeness_pass"])
        alert_types = {row["type"] for row in clipped_snapshot["alerts"]}
        self.assertIn("deadline_miss", alert_types)
        self.assertIn("priority_inversion", alert_types)
        self.assertEqual(report["alert_completeness"]["alert_id_missing_count"], 0)
        self.assertEqual(report["alert_completeness"]["alert_actual_delta_max"], 0.0)
        self.assertEqual(report["alert_completeness"]["alert_threshold_delta_max"], 0.0)
        self.assertEqual(full_snapshot["alerts"], clipped_snapshot["alerts"])

    def test_clipped_trace_matches_full_diagnosis_parameters(self) -> None:
        report, full_snapshot, clipped_snapshot = self._run_completeness()

        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(report["diagnosis_completeness"]["diagnosis_completeness_pass"])
        diagnosis_types = {row["diagnosis_type"] for row in clipped_snapshot["diagnoses"]}
        self.assertIn("deadline_miss", diagnosis_types)
        self.assertIn("priority_inversion", diagnosis_types)
        self.assertEqual(report["diagnosis_completeness"]["diag_id_missing_count"], 0)
        self.assertEqual(report["diagnosis_completeness"]["diag_related_alerts_diff_count"], 0)
        self.assertEqual(full_snapshot["diagnoses"], clipped_snapshot["diagnoses"])

    def test_clipped_trace_matches_full_resource_parameters(self) -> None:
        report, _, _ = self._run_completeness()

        resources = report["resource_semantic_completeness"]
        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(resources["resource_semantic_completeness_pass"])
        self.assertEqual(resources["task_runtime_total_delta_max"], 0.0)
        self.assertEqual(resources["task_blocked_total_delta_max"], 0.0)
        self.assertEqual(resources["core_switch_count_diff_total"], 0)
        self.assertEqual(resources["resource_hold_edge_missing_count"], 0)
        self.assertEqual(resources["resource_wait_edge_missing_count"], 0)
        self.assertEqual(resources["resource_graph_preservation_rate"], 1.0)

    def test_clipped_trace_canonicalizes_duplicate_resource_event_ref_keys(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        source_backed = copy.deepcopy(full_snapshot)
        source_backed["rebuild_bundle"]["event_stream"].append(self._resource_event_row(source_backed))

        report = compare_snapshots(source_backed, clipped_snapshot)
        resources = report["resource_semantic_completeness"]

        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(resources["resource_semantic_completeness_pass"])
        self.assertEqual(resources["resource_event_count_diff_total"], 0)
        self.assertGreater(resources["resource_event_duplicate_ref_key_count"], 0)
        self.assertGreater(resources["resource_event_canonicalized_count"], 0)

    def test_clipped_trace_canonicalizes_mixed_duplicate_resource_event_ref_key(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        source_backed = copy.deepcopy(full_snapshot)
        clipped_canonical = copy.deepcopy(clipped_snapshot)
        resource_row = self._resource_event_row(source_backed)
        canonical_row = copy.deepcopy(resource_row)
        canonical_row["event_name"] = "TASK_READY"
        canonical_row["obj_id"] = None
        canonical_row["payload"] = {"task_id": resource_row.get("task_id"), "reason": 1}
        source_backed["rebuild_bundle"]["event_stream"].append(canonical_row)
        for index, row in enumerate(clipped_canonical["rebuild_bundle"]["event_stream"]):
            if row.get("ref_key") == resource_row.get("ref_key"):
                clipped_canonical["rebuild_bundle"]["event_stream"][index] = canonical_row
                break
        else:
            raise AssertionError("fixture must contain the resource row in clipped target")

        report = compare_snapshots(source_backed, clipped_canonical)
        resources = report["resource_semantic_completeness"]

        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(resources["resource_semantic_completeness_pass"])
        self.assertEqual(resources["resource_event_count_diff_total"], 0)
        self.assertGreater(resources["resource_event_duplicate_ref_key_count"], 0)
        self.assertGreater(resources["resource_event_canonicalized_count"], 0)

    def test_clipped_trace_fails_on_missing_distinct_resource_event_ref_key(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        source_backed = copy.deepcopy(full_snapshot)
        extra_event = self._resource_event_row(source_backed)
        extra_event["ref_key"] = "evt:synthetic:resource-extra"
        extra_event["event_uid"] = "evt:synthetic:resource-extra"
        source_backed["rebuild_bundle"]["event_stream"].append(extra_event)

        report = compare_snapshots(source_backed, clipped_snapshot)
        resources = report["resource_semantic_completeness"]

        self.assertEqual(report["status"], "fail")
        self.assertFalse(resources["resource_semantic_completeness_pass"])
        self.assertGreater(resources["resource_event_count_diff_total"], 0)

    def test_clipped_trace_completeness_report_has_no_missing_refs(self) -> None:
        report, _, clipped_snapshot = self._run_completeness()

        closure = report["event_ref_closure"]
        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(closure["event_ref_closure_pass"])
        self.assertGreater(closure["required_event_ref_count"], 0)
        self.assertEqual(closure["missing_required_event_ref_count"], 0)
        self.assertEqual(closure["missing_alert_refs"], 0)
        self.assertEqual(closure["missing_resource_graph_refs"], 0)
        self.assertEqual(closure["out_of_window_non_required_event_count"], 0)
        self.assertGreater(len(clipped_snapshot["ref_index"]), 0)

    def test_clipped_trace_completeness_requires_export_resource_observation(self) -> None:
        report, full_snapshot, clipped_snapshot = self._run_completeness()

        observation = report["export_resource_observation"]
        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(observation["export_resource_observation_pass"])
        self.assertGreater(observation["export_clipped_write_seconds"], 0.0)
        self.assertGreater(observation["export_clipped_normalize_seconds"], 0.0)
        self.assertGreater(observation["python_peak_alloc_mb"], 0.0)
        self.assertGreater(observation["os_peak"]["ru_maxrss"], 0)

        tampered = copy.deepcopy(clipped_snapshot)
        tampered["export_resource_observation"].pop("python_peak_alloc_mb", None)
        tampered_report = compare_snapshots(full_snapshot, tampered)

        self.assertEqual(tampered_report["status"], "fail")
        self.assertFalse(tampered_report["export_resource_observation"]["export_resource_observation_pass"])

    def test_clipped_trace_completeness_report_fails_on_tampered_alert(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        tampered = copy.deepcopy(clipped_snapshot)
        target = next(row for row in tampered["alerts"] if row["type"] == "deadline_miss")
        target["actual"] = float(target["actual"]) + 1.0

        report = compare_snapshots(full_snapshot, tampered)

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["alert_completeness"]["alert_completeness_pass"])
        self.assertGreater(report["alert_completeness"]["alert_actual_delta_max"], 0.0)

    def test_clipped_trace_completeness_report_fails_on_missing_ref(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        tampered = copy.deepcopy(clipped_snapshot)
        required_refs = collect_required_event_refs_by_category(tampered)
        ref_to_remove = sorted(set().union(*required_refs.values()))[0]
        tampered["ref_index"] = [
            row for row in tampered["ref_index"] if row.get("ref_key") != ref_to_remove
        ]

        report = compare_snapshots(full_snapshot, tampered)

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["event_ref_closure"]["event_ref_closure_pass"])
        self.assertGreater(report["event_ref_closure"]["missing_required_event_ref_count"], 0)

    def test_clipped_trace_completeness_report_fails_on_out_of_window_non_required_ref(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        tampered = copy.deepcopy(clipped_snapshot)
        tampered["ref_index"].append(
            {
                "ref_key": "evt:synthetic:outside",
                "timestamp_aligned": 9999.0,
                "core_id": 0,
                "seq": 9999,
            }
        )

        report = compare_snapshots(full_snapshot, tampered)

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["event_ref_closure"]["event_ref_closure_pass"])
        self.assertEqual(report["event_ref_closure"]["out_of_window_non_required_event_count"], 1)

    def test_clipped_trace_completeness_report_fails_on_missing_resource_edge(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        tampered = copy.deepcopy(clipped_snapshot)
        graph = tampered["rebuild_bundle"]["resource_graph"]
        self.assertTrue(graph["wait_edges"])
        graph["wait_edges"] = graph["wait_edges"][1:]

        report = compare_snapshots(full_snapshot, tampered)

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["resource_semantic_completeness"]["resource_semantic_completeness_pass"])
        self.assertGreater(report["resource_semantic_completeness"]["resource_wait_edge_missing_count"], 0)

    def test_clipped_trace_completeness_compares_future_resource_edge_time_bounds(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        for snapshot in (full_snapshot, clipped_snapshot):
            graph = snapshot["rebuild_bundle"]["resource_graph"]
            edges = graph["hold_edges"] + graph["wait_edges"]
            self.assertTrue(edges)
            for edge in edges:
                self.assertIn("t_begin", edge)
                self.assertIn("t_end", edge)
                self.assertGreaterEqual(float(edge["t_end"]), float(edge["t_begin"]))

        report = compare_snapshots(full_snapshot, clipped_snapshot)
        self.assertEqual(report["status"], "strict_pass")

        tampered = copy.deepcopy(clipped_snapshot)
        wait_edge = tampered["rebuild_bundle"]["resource_graph"]["wait_edges"][0]
        wait_edge["t_end"] = float(wait_edge["t_end"]) + 10.0
        tampered_report = compare_snapshots(full_snapshot, tampered)

        self.assertEqual(tampered_report["status"], "fail")
        self.assertGreater(tampered_report["resource_semantic_completeness"]["resource_wait_edge_missing_count"], 0)

    def test_clipped_trace_compares_resource_edge_bounds_when_problem_scope_is_not_resource_specific(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        full_copy = copy.deepcopy(full_snapshot)
        clipped_copy = copy.deepcopy(clipped_snapshot)
        for snapshot in (full_copy, clipped_copy):
            for row in snapshot["alerts"] + snapshot["diagnoses"]:
                row["object_scope"] = {"source": "seq_gap"}
                row["evidence_refs"] = []

        report = compare_snapshots(full_copy, clipped_copy)
        self.assertEqual(report["status"], "strict_pass")

        tampered = copy.deepcopy(clipped_copy)
        wait_edge = tampered["rebuild_bundle"]["resource_graph"]["wait_edges"][0]
        wait_edge["t_end"] = float(wait_edge["t_end"]) + 10.0
        tampered_report = compare_snapshots(full_copy, tampered)

        self.assertEqual(tampered_report["status"], "fail")
        self.assertGreater(tampered_report["resource_semantic_completeness"]["resource_wait_edge_missing_count"], 0)

    def test_clipped_trace_completeness_reports_bounded_pass_for_explained_result_validity_subset(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        bounded = copy.deepcopy(clipped_snapshot)
        for row in bounded["alerts"] + bounded["diagnoses"]:
            row["support_level"] = "bounded"
            row["trusted"] = False
        bounded["result_validity"] = self._result_validity_rows_for_snapshot(
            bounded,
            validity_scope="evidence_subset",
            derivation_mode="recomputed_subset",
        )
        bounded["consumer_mode"] = {
            "compare": "REFERENCE_ONLY",
            "replay": "REFERENCE_ONLY",
            "audit": "REFERENCE_ONLY",
        }
        bounded["consumer_mode_explain"] = {
            "final_mode": bounded["consumer_mode"],
            "decision_reasons": ["subset_scope_present", "recomputed_subset_present"],
            "blocking_objects": [{"object_kind": "alert", "object_id": bounded["alerts"][0]["alert_id"]}],
        }

        report = compare_snapshots(full_snapshot, bounded)

        self.assertEqual(report["status"], "bounded_pass")
        self.assertTrue(report["result_validity"]["result_validity_pass"])
        self.assertFalse(report["result_validity"]["result_validity_strict_pass"])
        self.assertGreater(report["result_validity"]["result_validity_evidence_subset_count"], 0)
        self.assertGreater(report["result_validity"]["result_validity_recomputed_count"], 0)
        self.assertTrue(report["result_validity"]["consumer_mode_explain_present"])

    def test_cli_synthesizes_result_validity_for_non_exact_support_subset(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        bounded = copy.deepcopy(clipped_snapshot)
        for row in bounded["alerts"] + bounded["diagnoses"]:
            row["support_level"] = "degraded"
            row["trusted"] = False

        result_validity, consumer_mode, consumer_mode_explain = clipped_cli._completeness_result_contract(
            snapshot_kind="clipped_target",
            alerts=bounded["alerts"],
            diagnoses=bounded["diagnoses"],
            result_validity=None,
            consumer_mode=None,
            consumer_mode_explain=None,
        )
        bounded["result_validity"] = result_validity
        bounded["consumer_mode"] = consumer_mode
        bounded["consumer_mode_explain"] = consumer_mode_explain

        report = compare_snapshots(full_snapshot, bounded)

        self.assertEqual(report["status"], "bounded_pass")
        self.assertTrue(report["result_validity"]["result_validity_pass"])
        self.assertEqual(report["result_validity"]["consumer_mode_reference_only_count"], 3)
        self.assertEqual(consumer_mode_explain["final_mode"], consumer_mode)
        self.assertIn("non_exact_support_level_present", consumer_mode_explain["decision_reasons"])
        self.assertIn("recomputed_subset_present", consumer_mode_explain["decision_reasons"])

    def test_clipped_trace_completeness_fails_on_result_validity_conflict(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        tampered = copy.deepcopy(clipped_snapshot)
        rows = self._result_validity_rows_for_snapshot(tampered)
        conflict = dict(rows["results"][0])
        conflict["validity_scope"] = "evidence_subset"
        conflict["derivation_mode"] = "recomputed_subset"
        rows["results"].append(conflict)
        tampered["result_validity"] = rows

        report = compare_snapshots(full_snapshot, tampered)

        self.assertEqual(report["status"], "fail")
        self.assertGreater(report["result_validity"]["result_validity_conflict_count"], 0)

    def test_cli_reuses_full_baseline_json_without_original_input(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        baseline_path = self.root / "full_baseline.json"
        baseline_path.write_text(json.dumps(full_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        output_dir = self.root / "cli-reuse"

        code = run_clipped_completeness_cli(
            [
                "--full-baseline-json",
                str(baseline_path),
                "--reuse-clipped-package",
                str(clipped_snapshot["source"]["package_path"]),
                "--output-dir",
                str(output_dir),
            ]
        )

        self.assertEqual(code, 2)
        report = json.loads(
            (output_dir / "full_trace_vs_clipped_trace_completeness_report.json").read_text(encoding="utf-8")
        )
        self.assertTrue(report["comparison_scope"]["scope_identity_pass"])
        self.assertFalse(report["export_resource_observation"]["export_resource_observation_pass"])
        status = json.loads((output_dir / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["report_status"], "fail")
        self.assertEqual(status["report_json"], "full_trace_vs_clipped_trace_completeness_report.json")

    def test_cli_reuses_full_baseline_json_and_existing_package_observation(self) -> None:
        _, full_snapshot, clipped_snapshot = self._run_completeness()
        baseline_path = self.root / "full_baseline.json"
        baseline_path.write_text(json.dumps(full_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        package_dir = Path(str(clipped_snapshot["source"]["package_path"]))
        (package_dir.parent / "clipped_target.json").write_text(
            json.dumps(clipped_snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        output_dir = self.root / "cli-reuse-with-observation"

        code = run_clipped_completeness_cli(
            [
                "--full-baseline-json",
                str(baseline_path),
                "--reuse-clipped-package",
                str(package_dir),
                "--output-dir",
                str(output_dir),
            ]
        )

        self.assertEqual(code, 0)
        report = json.loads(
            (output_dir / "full_trace_vs_clipped_trace_completeness_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["status"], "strict_pass")
        self.assertTrue(report["export_resource_observation"]["export_resource_observation_pass"])
        self.assertGreater(report["export_resource_observation"]["export_clipped_write_seconds"], 0.0)

    def test_cli_source_backed_baseline_mode_records_generation_mode(self) -> None:
        fixture = Path(__file__).resolve().parents[2] / "docs" / "validation-fixture" / "baseline.trace"
        output_dir = self.root / "cli-source-backed"

        code = run_clipped_completeness_cli(
            [
                "--input",
                str(fixture),
                "--output-dir",
                str(output_dir),
                "--time-window",
                "0",
                "1000",
                "--baseline-generation-mode",
                "source_backed_window_scan",
            ]
        )

        self.assertEqual(code, 0)
        report = json.loads(
            (output_dir / "full_trace_vs_clipped_trace_completeness_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["status"], "strict_pass")
        self.assertEqual(report["comparison_scope"]["baseline_generation_mode"], "source_backed_window_scan")
        self.assertTrue(report["export_resource_observation"]["export_resource_observation_pass"])

    def test_cli_source_backed_baseline_does_not_load_full_trace_artifact(self) -> None:
        fixture = Path(__file__).resolve().parents[2] / "docs" / "validation-fixture" / "baseline.trace"
        output_dir = self.root / "cli-source-backed-no-full-load"

        with patch.object(
            clipped_cli,
            "load_dataset_with_timings",
            side_effect=AssertionError("unexpected full trace load"),
        ):
            code = run_clipped_completeness_cli(
                [
                    "--input",
                    str(fixture),
                    "--output-dir",
                    str(output_dir),
                    "--time-window",
                    "0",
                    "1000",
                    "--baseline-generation-mode",
                    "source_backed_window_scan",
                ]
            )

        self.assertEqual(code, 0)

    def test_cli_source_backed_baseline_and_package_contain_only_window_events(self) -> None:
        trace_path = self._write_window_scope_trace()
        output_dir = self.root / "cli-source-backed-window-only"

        code = run_clipped_completeness_cli(
            [
                "--input",
                str(trace_path),
                "--output-dir",
                str(output_dir),
                "--time-window",
                "100",
                "200",
                "--baseline-generation-mode",
                "source_backed_window_scan",
            ]
        )

        self.assertEqual(code, 0)
        full_snapshot = json.loads((output_dir / "full_baseline.json").read_text(encoding="utf-8"))
        package_bundle = json.loads(
            (output_dir / "clipped_package" / "rebuild" / "rebuild_bundle.json").read_text(encoding="utf-8")
        )
        full_event_seqs = [int(row["seq"]) for row in full_snapshot["rebuild_bundle"]["event_stream"]]
        package_event_seqs = [int(row["seq"]) for row in package_bundle["event_stream"]]

        self.assertEqual(full_event_seqs, [2, 3])
        self.assertEqual(package_event_seqs, [2, 3])
        for payload in (full_snapshot["rebuild_bundle"], package_bundle):
            for row in payload["event_stream"]:
                self.assertGreaterEqual(float(row["timestamp_aligned"]), 100.0)
                self.assertLessEqual(float(row["timestamp_aligned"]), 200.0)

    def test_cli_writes_full_baseline_before_package_write_failure(self) -> None:
        trace_path = self._write_window_scope_trace()
        output_dir = self.root / "cli-package-failure"

        with patch.object(
            clipped_cli.ExportService,
            "export_WritePackage",
            return_value=err_result("WRITE_FAILED", "forced package failure"),
        ):
            with self.assertRaises(SystemExit):
                run_clipped_completeness_cli(
                    [
                        "--input",
                        str(trace_path),
                        "--output-dir",
                        str(output_dir),
                        "--time-window",
                        "100",
                        "200",
                        "--baseline-generation-mode",
                        "source_backed_window_scan",
                    ]
                )

        self.assertTrue((output_dir / "full_baseline.json").is_file())
        status = json.loads((output_dir / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["failure_stage"], "write_package")
        self.assertEqual(status["full_baseline_json"], "full_baseline.json")
        self.assertEqual(status["failure_report_json"], "clipped_completeness_failure_report.json")
        self.assertTrue((output_dir / "clipped_completeness_failure_report.json").is_file())

    def test_cli_records_validate_args_failure_status(self) -> None:
        output_dir = self.root / "cli-validate-failure"

        with self.assertRaises(SystemExit):
            run_clipped_completeness_cli(["--output-dir", str(output_dir)])

        status = json.loads((output_dir / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["failure_stage"], "validate_args")
        self.assertEqual(status["failure_report_json"], "clipped_completeness_failure_report.json")
        self.assertTrue((output_dir / "clipped_completeness_failure_report.json").is_file())

    def test_cli_records_normalize_failure_status(self) -> None:
        trace_path = self._write_window_scope_trace()
        output_dir = self.root / "cli-normalize-failure"

        with patch.object(
            clipped_cli.ExportService,
            "export_NormalizePackage",
            return_value=err_result("NORMALIZE_FAILED", "forced normalize failure"),
        ):
            with self.assertRaises(SystemExit):
                run_clipped_completeness_cli(
                    [
                        "--input",
                        str(trace_path),
                        "--output-dir",
                        str(output_dir),
                        "--time-window",
                        "100",
                        "200",
                        "--baseline-generation-mode",
                        "source_backed_window_scan",
                    ]
                )

        self.assertTrue((output_dir / "full_baseline.json").is_file())
        status = json.loads((output_dir / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["failure_stage"], "normalize_package")
        self.assertEqual(status["failure_report_json"], "clipped_completeness_failure_report.json")
        self.assertTrue((output_dir / "clipped_completeness_failure_report.json").is_file())

    def test_cli_records_compare_exception_status(self) -> None:
        trace_path = self._write_window_scope_trace()
        output_dir = self.root / "cli-compare-failure"

        with patch.object(clipped_cli, "compare_snapshots", side_effect=RuntimeError("forced compare failure")):
            with self.assertRaisesRegex(RuntimeError, "forced compare failure"):
                run_clipped_completeness_cli(
                    [
                        "--input",
                        str(trace_path),
                        "--output-dir",
                        str(output_dir),
                        "--time-window",
                        "100",
                        "200",
                        "--baseline-generation-mode",
                        "source_backed_window_scan",
                    ]
                )

        status = json.loads((output_dir / "run_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["failure_stage"], "compare_snapshots")
        self.assertEqual(status["error_type"], "RuntimeError")
        self.assertEqual(status["failure_report_json"], "clipped_completeness_failure_report.json")
        self.assertTrue((output_dir / "clipped_completeness_failure_report.json").is_file())

    def _run_completeness(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        trace_path, dictionary = self._write_dependency_trace()
        loaded = load_dataset(trace_path, dictionary=dictionary)
        self.assertTrue(loaded.ok, loaded.message)

        controller = WorkspaceController()
        registered = controller._register_artifact(loaded.data)
        self.assertTrue(registered.ok, registered.message)
        scope = {
            "time_window": [95.0, 305.0],
            "filter": {},
            "selection": {},
            "metric_config": {},
        }
        context_result = controller.viz_SetContext(
            {
                "time_window": tuple(scope["time_window"]),
                "filter": scope["filter"],
                "selection": scope["selection"],
                "dataset_role": "single",
            }
        )
        self.assertTrue(context_result.ok, context_result.message)

        full_snapshot = self._snapshot_from_dataset(
            controller,
            str(registered.data),
            "full_baseline",
            trace_path,
            scope,
            ref_index=[],
            meta={},
            manifest={},
            package_path=None,
        )

        export = ExportService(controller.repository, controller.context_store, controller.jobs)
        job = export.export_Clipped({"dataset_id": registered.data})
        self.assertTrue(job.ok, job.message)
        package_dir = self.root / "clipped-package"
        written = export.export_WritePackage(job.data["job_id"], str(package_dir))
        self.assertTrue(written.ok, written.message)
        normalized = export.export_NormalizePackage(str(package_dir))
        self.assertTrue(normalized.ok, normalized.message)

        repro = ReproService(controller.repository, controller.context_store, controller.jobs)
        opened = repro.repro_OpenPackage(str(package_dir))
        self.assertTrue(opened.ok, opened.message)
        restored = repro.repro_RestoreContext(None)
        self.assertTrue(restored.ok, restored.message)
        clipped_dataset = repro.repro_LoadAsDataset("single")
        self.assertTrue(clipped_dataset.ok, clipped_dataset.message)

        clipped_snapshot = self._snapshot_from_dataset(
            controller,
            str(clipped_dataset.data),
            "clipped_target",
            trace_path,
            scope,
            ref_index=normalized.data["ref_index"],
            meta=written.data["meta"],
            manifest=written.data["manifest"],
            package_path=package_dir,
            analysis_context=normalized.data["analysis_context"],
            export_observation={
                "write_seconds": 0.001,
                "normalize_seconds": 0.001,
                "write_timings": written.data.get("write_timings") or {},
                "python_peak_alloc_mb": 1.0,
                "os_peak_supported": True,
                "os_peak": {"ru_maxrss": 1},
            },
        )

        report = compare_snapshots(full_snapshot, clipped_snapshot)
        return report, full_snapshot, clipped_snapshot

    def _resource_event_row(self, snapshot: dict[str, object]) -> dict[str, object]:
        for row in snapshot["rebuild_bundle"]["event_stream"]:
            payload = row.get("payload") or {}
            has_resource = any(
                row.get(key, payload.get(key)) is not None
                for key in ("obj_id", "wait_obj_id", "resource_id")
            )
            if has_resource and row.get("ref_key"):
                return copy.deepcopy(row)
        raise AssertionError("fixture must contain a resource event with a ref_key")

    def _result_validity_rows_for_snapshot(
        self,
        snapshot: dict[str, object],
        *,
        validity_scope: str = "source_snapshot",
        derivation_mode: str = "reused_context",
    ) -> dict[str, object]:
        rows = []
        for alert in snapshot["alerts"]:
            rows.append(
                {
                    "path": "result/alerts.json",
                    "category": "result",
                    "object_kind": "alert",
                    "object_id": alert["alert_id"],
                    "validity_scope": validity_scope,
                    "derivation_mode": derivation_mode,
                    "notes": None,
                }
            )
        for diagnosis in snapshot["diagnoses"]:
            rows.append(
                {
                    "path": "result/diagnoses.json",
                    "category": "result",
                    "object_kind": "diagnosis",
                    "object_id": diagnosis["diag_id"],
                    "validity_scope": validity_scope,
                    "derivation_mode": derivation_mode,
                    "notes": None,
                }
            )
        return {"results": rows}

    def _snapshot_from_dataset(
        self,
        controller: WorkspaceController,
        dataset_id: str,
        snapshot_kind: str,
        input_path: Path,
        scope: dict[str, object],
        *,
        ref_index: list[dict[str, object]],
        meta: dict[str, object],
        manifest: dict[str, object],
        package_path: Path | None,
        analysis_context: dict[str, object] | None = None,
        export_observation: dict[str, object] | None = None,
    ) -> dict[str, object]:
        record = controller.repository.get(dataset_id)
        session = metric_Init(MetricConfig()).data
        ingest = metric_Ingest(session, record.artifact.bundle)
        self.assertTrue(ingest.ok, ingest.message)
        t_begin, t_end = scope["time_window"]
        filters = scope["filter"]
        metrics = metric_Compute(session, float(t_begin), float(t_end), filters).data or []
        alerts = alert_Evaluate(session, float(t_begin), float(t_end), filters).data or []
        diagnoses = diag_Generate(session, float(t_begin), float(t_end), alerts).data or []
        context = analysis_context or controller.context_store.get().persisted_dict()
        alert_rows = serialize(alerts)
        diagnosis_rows = serialize(diagnoses)
        anchors = build_anchor_rows(dict(context), alert_rows, diagnosis_rows)
        return build_analysis_snapshot(
            snapshot_kind=snapshot_kind,
            input_path=input_path,
            dataset_id=record.artifact.dataset_id,
            parser_version=PARSER_VERSION,
            comparison_scope=scope,
            metrics=metrics,
            alerts=alert_rows,
            diagnoses=diagnosis_rows,
            anchors=anchors,
            rebuild_bundle=record.artifact.bundle.to_dict(),
            ref_index=ref_index,
            meta=meta,
            manifest=manifest,
            package_path=package_path,
            analysis_context=dict(context),
            export_observation=export_observation or {},
        )

    def _write_dependency_trace(self) -> tuple[Path, dict[str, object]]:
        dictionary = json.loads(json.dumps(load_dictionary()))
        dictionary["event_defs"].append(
            {
                "event_id": 0x9001,
                "event_name": "TASK_READY",
                "domain": "task",
                "payload_fields": [
                    "task_id",
                    "prio",
                    "core_hint",
                    "reason",
                    "job_id",
                    "instance_id",
                    "release_ts",
                    "deadline_ts",
                ],
            }
        )
        dictionary["event_defs"].append(
            {
                "event_id": 0x9003,
                "event_name": "TASK_EXIT",
                "domain": "task",
                "payload_fields": ["task_id", "exit_code", "job_id", "instance_id", "finish_ts"],
            }
        )
        trace_path = self.root / "deadline-inversion-resource.trace"
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
                        "prio": 9,
                        "core_hint": 0,
                        "reason": 1,
                        "job_id": 7,
                        "instance_id": 1,
                        "release_ts": 100,
                        "deadline_ts": 150,
                    },
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 105,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("SYNC_LOCK"),
                    "seq": 3,
                    "timestamp": 110,
                    "payload": {"task_id": 1, "obj_id": 42, "obj_type": 1},
                },
                {
                    "core_id": 0,
                    "event_id": 0x9001,
                    "seq": 4,
                    "timestamp": 115,
                    "payload": {"task_id": 2, "prio": 1, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_BLOCK"),
                    "seq": 5,
                    "timestamp": 120,
                    "payload": {"task_id": 2, "wait_obj_id": 42, "reason": 3, "owner_task_id": 1},
                },
                {
                    "core_id": 0,
                    "event_id": 0x9001,
                    "seq": 6,
                    "timestamp": 130,
                    "payload": {"task_id": 3, "prio": 5, "core_hint": 0, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 7,
                    "timestamp": 140,
                    "payload": {"core_id": 0, "prev_task_id": 1, "next_task_id": 3, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 8,
                    "timestamp": 260,
                    "payload": {"core_id": 0, "prev_task_id": 3, "next_task_id": 1, "reason": 2},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("SYNC_UNLOCK"),
                    "seq": 9,
                    "timestamp": 270,
                    "payload": {"task_id": 1, "obj_id": 42, "obj_type": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("TASK_WAKEUP"),
                    "seq": 10,
                    "timestamp": 280,
                    "payload": {"task_id": 2, "wake_src": 1, "obj_id": 42},
                },
                {
                    "core_id": 0,
                    "event_id": 0x9003,
                    "seq": 11,
                    "timestamp": 300,
                    "payload": {"task_id": 1, "exit_code": 0, "job_id": 7, "instance_id": 1, "finish_ts": 300},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 12,
                    "timestamp": 305,
                    "payload": {"core_id": 0, "prev_task_id": 1, "next_task_id": 2, "reason": 3},
                },
            ],
        )
        return trace_path, dictionary

    def _write_window_scope_trace(self) -> Path:
        trace_path = self.root / "window-scope.trace"
        encode_trace(
            trace_path,
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 1,
                    "timestamp": 10,
                    "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 2,
                    "timestamp": 100,
                    "payload": {"core_id": 0, "prev_task_id": 1, "next_task_id": 2, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 3,
                    "timestamp": 150,
                    "payload": {"core_id": 0, "prev_task_id": 2, "next_task_id": 3, "reason": 1},
                },
                {
                    "core_id": 0,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": 4,
                    "timestamp": 300,
                    "payload": {"core_id": 0, "prev_task_id": 3, "next_task_id": 0, "reason": 1},
                },
            ],
        )
        return trace_path


if __name__ == "__main__":
    unittest.main()

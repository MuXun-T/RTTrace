from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path

from tool.build_evidence_proof_archive import main


class EvidenceProofArchiveToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build_evidence_proof_archive_writes_grouped_archive(self) -> None:
        output_dir = self.root / "proof-archive"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "--output-dir",
                    str(output_dir),
                    "--medium-repeat",
                    "4",
                    "--large-input-mode",
                    "mock_measured",
                ]
            )
        self.assertEqual(exit_code, 0)

        required_paths = [
            output_dir / "manifest.json",
            output_dir / "report.json",
            output_dir / "A_control_plane_first" / "summary.json",
            output_dir / "B_budget_freeze" / "summary.json",
            output_dir / "C_degraded_audit" / "summary.json",
            output_dir / "A_control_plane_first" / "large_input_reference.json",
            output_dir / "A_control_plane_first" / "large_input_measured.json",
            output_dir / "B_budget_freeze" / "budget_sweep.json",
            output_dir / "B_budget_freeze" / "pareto_frontier.json",
            output_dir / "C_degraded_audit" / "scenarios.json",
        ]
        for path in required_paths:
            self.assertTrue(path.exists(), str(path))

        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        a_summary = json.loads((output_dir / "A_control_plane_first" / "summary.json").read_text(encoding="utf-8"))
        b_summary = json.loads((output_dir / "B_budget_freeze" / "summary.json").read_text(encoding="utf-8"))
        c_summary = json.loads((output_dir / "C_degraded_audit" / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "pass")
        self.assertEqual(a_summary["status"], "pass")
        self.assertEqual(b_summary["status"], "pass")
        self.assertEqual(c_summary["status"], "pass")
        manifest_paths = {entry["path"] for entry in manifest["entries"]}
        self.assertIn("A_control_plane_first/summary.json", manifest_paths)
        self.assertIn("B_budget_freeze/summary.json", manifest_paths)
        self.assertIn("C_degraded_audit/summary.json", manifest_paths)

        large_reference = json.loads(
            (output_dir / "A_control_plane_first" / "large_input_reference.json").read_text(encoding="utf-8")
        )
        self.assertEqual(large_reference["kind"], "reference_context")
        self.assertTrue(large_reference["reference_artifacts"])
        measured = json.loads((output_dir / "A_control_plane_first" / "large_input_measured.json").read_text(encoding="utf-8"))
        self.assertEqual(measured["kind"], "direct_measured")
        self.assertEqual(measured["run_kind"], "evidence_export_large_input_direct")
        metrics = dict(measured["package_metrics"])
        for key in (
            "scan_count",
            "seek_count",
            "window_span_total",
            "sidecar_lookup_count",
            "events_emitted",
            "bytes_emitted",
            "round_count",
            "window_hit_rate",
            "peak_rss_mb",
        ):
            self.assertIn(key, metrics)
        self.assertIn("diagnosis_preservation_rate", measured)
        self.assertIn("proof_consumer_mode", measured)

        budget_sweep = json.loads((output_dir / "B_budget_freeze" / "budget_sweep.json").read_text(encoding="utf-8"))
        sweep_rows = list(budget_sweep["rows"])
        self.assertGreaterEqual(len(sweep_rows), 4)
        self.assertTrue(
            any(row["closure_mode"] == "bounded" and not row["reject_round_has_read"] for row in sweep_rows)
        )
        self.assertTrue(all("progress_trace" in row for row in sweep_rows))
        self.assertTrue(all("proof_consumer_mode" in row for row in sweep_rows))

        pareto_frontier = json.loads((output_dir / "B_budget_freeze" / "pareto_frontier.json").read_text(encoding="utf-8"))
        self.assertTrue(list(pareto_frontier["rows"]))

        scenarios = json.loads((output_dir / "C_degraded_audit" / "scenarios.json").read_text(encoding="utf-8"))
        scenario_rows = list(scenarios["rows"])
        self.assertEqual(
            {row["scenario_id"] for row in scenario_rows},
            {"sidecar_mismatch", "cycle_inflation", "corrupt_segment", "io_guard"},
        )
        for row in scenario_rows:
            self.assertEqual(row["closure_mode"], "degraded")
            self.assertIn("minimal_legal_package_valid", row)
            self.assertIn("proof_consumer_mode", row)
            self.assertIn("blocker_artifact", row)
            self.assertTrue(row["minimal_legal_package_valid"])

    def test_build_evidence_proof_archive_emits_blocker_when_large_input_direct_run_is_blocked(self) -> None:
        output_dir = self.root / "proof-archive-blocked"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "--output-dir",
                    str(output_dir),
                    "--medium-repeat",
                    "4",
                    "--large-input-mode",
                    "mock_blocked",
                ]
            )
        self.assertEqual(exit_code, 0)

        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "blocked")
        blocker = json.loads((output_dir / "A_control_plane_first" / "large_input_blocker.json").read_text(encoding="utf-8"))
        self.assertEqual(blocker["kind"], "blocked")
        self.assertEqual(blocker["blocker_code"], "scratch_dir_not_local_disk")
        self.assertEqual(blocker["failure_stage"], "preflight")
        self.assertIn("input_contract", blocker)
        self.assertIn("next_steps", blocker)


if __name__ == "__main__":
    unittest.main()

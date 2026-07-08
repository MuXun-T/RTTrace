from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from parser.runtime_advisor import (
    OFFLINE_ADVISOR_PRIOR_VERSION,
    coefficient_payload_checksum,
    safe_runtime_action_space,
)
from tool.train_runtime_advisor import train_runtime_advisor_coefficients


class RuntimeAdvisorTrainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_rows(self, rows: list[dict[str, object]]) -> Path:
        path = self.root / "telemetry.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def test_training_emits_checksumed_safe_prior_artifact_without_sklearn_dependency(self) -> None:
        telemetry_path = self._write_rows(
            [
                {
                    "input_bytes": 512 * 1024 * 1024,
                    "sidecar_bytes": 192 * 1024 * 1024,
                    "sidecar_row_count": 100_000,
                    "runtime_seconds": 120.0,
                    "peak_rss_mb": 3072.0,
                    "index_reused": False,
                },
                {
                    "input_bytes": 24 * 1024 * 1024,
                    "sidecar_bytes": 4 * 1024 * 1024,
                    "sidecar_row_count": 5_000,
                    "runtime_seconds": 18.0,
                    "peak_rss_mb": 384.0,
                    "index_reused": True,
                },
            ]
        )
        output_path = self.root / "advisor-prior.json"

        report = train_runtime_advisor_coefficients(telemetry_path, output_path)

        self.assertEqual(report["artifact_version"], OFFLINE_ADVISOR_PRIOR_VERSION)
        self.assertTrue(str(report["created_at"]).endswith("+00:00"))
        self.assertEqual(report["status"], "trained")
        self.assertEqual(report["safe_action_space"], safe_runtime_action_space())
        self.assertEqual(report["training_rows"], 2)
        self.assertTrue(str(report["telemetry_source_digest"]).startswith("sha256:"))
        self.assertEqual(report["training_input_summary"]["source_row_count"], 2)
        self.assertEqual(report["training_input_summary"]["normalized_row_count"], 2)
        self.assertEqual(report["training_input_summary"]["rows_with_runtime_seconds"], 2)
        self.assertEqual(report["training_input_summary"]["rows_with_peak_rss_mb"], 2)
        self.assertEqual(report["training_input_summary"]["ticket_present_rows"], 1)
        self.assertEqual(report["training_input_summary"]["high_rss_rows"], 1)
        self.assertGreaterEqual(report["training_input_summary"]["bucket_count"], 1)
        self.assertEqual(report["model_checksum"], coefficient_payload_checksum(report))
        self.assertNotIn("optional_dependency", report)
        self.assertIn("buckets", report)
        self.assertTrue(report["buckets"])
        self.assertGreater(report["global_action_support"]["baseline_full_load"], 0)
        self.assertGreater(report["global_action_support"]["cold_preview"], 0)
        self.assertGreater(report["global_action_support"]["sidecar_index_reuse"], 0)
        serialized = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(serialized["model_checksum"], report["model_checksum"])

    def test_training_with_no_rows_writes_skipped_artifact_with_safe_action_space(self) -> None:
        telemetry_path = self._write_rows([])
        output_path = self.root / "advisor-prior-empty.json"

        report = train_runtime_advisor_coefficients(telemetry_path, output_path)

        self.assertEqual(report["artifact_version"], OFFLINE_ADVISOR_PRIOR_VERSION)
        self.assertTrue(str(report["created_at"]).endswith("+00:00"))
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(report["safe_action_space"], safe_runtime_action_space())
        self.assertTrue(str(report["telemetry_source_digest"]).startswith("sha256:"))
        self.assertEqual(report["training_input_summary"]["source_row_count"], 0)
        self.assertEqual(report["training_input_summary"]["normalized_row_count"], 0)
        self.assertEqual(report["training_input_summary"]["rows_with_runtime_seconds"], 0)
        self.assertEqual(report["training_input_summary"]["rows_with_peak_rss_mb"], 0)
        self.assertEqual(report["training_input_summary"]["ticket_present_rows"], 0)
        self.assertEqual(report["training_input_summary"]["high_rss_rows"], 0)
        self.assertEqual(report["training_input_summary"]["bucket_count"], 0)
        self.assertEqual(report["global_action_support"]["abstain"], 0)
        self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["status"], "skipped")


if __name__ == "__main__":
    unittest.main()

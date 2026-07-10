from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from parser.rtos_diagnosis_advisor_review import build_diagnosis_advisor_review, canonical_review_json
from parser.rtos_diagnosis_human_feedback import (
    build_feedback_summary,
    canonical_feedback_summary_json,
    validate_feedback_record,
    validate_feedback_summary,
)
from parser.rtos_diagnosis_report import canonical_report_json
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json"
FEEDBACK_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_synthetic_feedback.json"
SUMMARY_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_human_feedback_summary.json"
CLI_PATH = ROOT / "tool/build_rtos_diagnosis_human_feedback_summary.py"
VARIANTS = ("advisor_disabled", "deterministic_template", "retrieval_grounded_review", "mock_llm_explanation")


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class HumanFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        cls.reviews = [build_diagnosis_advisor_review(cls.report, variant_id=variant) for variant in VARIANTS]
        cls.report_hash = _hash(canonical_report_json(cls.report))

    def _record(self, index: int = 0, variant: str = "deterministic_template") -> dict:
        review = next(row for row in self.reviews if row["variant_id"] == variant)
        return {
            "schema_version": "rtos-diagnosis-human-feedback-record-v1",
            "response_id": f"synthetic-feedback-{index:02d}",
            "data_source": "synthetic",
            "synthetic": True,
            "source_report_id": self.report["report_id"],
            "source_report_canonical_hash": self.report_hash,
            "source_review_id": review["review_id"],
            "source_review_canonical_hash": _hash(canonical_review_json(review)),
            "variant_id": variant,
            "case_id": self.report["cases"][index % len(self.report["cases"])]["case_id"],
            "consent": {"required": False, "granted": False, "form_version": "not_applicable", "withdrawal_supported": False, "consent_not_applicable": True},
            "ratings": {"explanation_clarity": 4, "limitation_visibility": 5, "citation_usefulness": 3, "abstention_appropriateness": 4, "review_usefulness": 3},
            "structured_flags": {"claim_boundary_understood": "yes", "unsupported_statement_observed": False, "explanation_too_verbose": False, "explanation_too_brief": False, "limitation_unclear": False, "citation_unclear": False},
            "coded_comment_labels": ["none"],
            "collection_scope": {"real_participant": False, "pipeline_validation_only": True},
            "privacy": {"contains_direct_identifiers": False, "contains_free_text": False, "contains_demographics": False, "pii_scan_passed": True},
            "retention": {"retention_class": "synthetic_fixture_only", "deletion_supported": True, "repository_storage_allowed": True},
            "claim_class": "report_only",
            "notes": ["synthetic_pipeline_validation_only"],
        }

    def test_record_and_summary_contracts_validate(self) -> None:
        record = self._record()
        validate_feedback_record(record, report=self.report, reviews=self.reviews)
        summary = build_feedback_summary([record], report=self.report, reviews=self.reviews)
        validate_feedback_summary(summary)
        self.assertIsNone(validate_schema(load_schema("rtos_diagnosis_human_feedback_summary.schema.json"), summary))
        self.assertFalse(summary["claim_boundary"]["human_effect_claimable"])
        self.assertEqual(summary["record_counts"]["real_participant_records_count"], 0)

    def test_aggregation_is_order_independent_and_inputs_immutable(self) -> None:
        records = [self._record(index, VARIANTS[index % len(VARIANTS)]) for index in range(4)]
        records_before, report_before, reviews_before = copy.deepcopy(records), copy.deepcopy(self.report), copy.deepcopy(self.reviews)
        first = build_feedback_summary(records, report=self.report, reviews=self.reviews)
        second = build_feedback_summary(list(reversed(records)), report=self.report, reviews=self.reviews)
        self.assertEqual(canonical_feedback_summary_json(first), canonical_feedback_summary_json(second))
        for row in first["variant_metrics"]:
            self.assertEqual(row["accepted_record_count"], 1)
            self.assertEqual(row["rating_metrics"]["explanation_clarity"], {"count": 1, "mean": 4.0, "minimum": 4, "maximum": 4})
        self.assertEqual(records, records_before)
        self.assertEqual(self.report, report_before)
        self.assertEqual(self.reviews, reviews_before)

    def test_empty_feedback_has_null_rating_metrics(self) -> None:
        summary = build_feedback_summary([], report=self.report, reviews=self.reviews)
        self.assertEqual(summary["record_counts"]["records_accepted"], 0)
        for metric in summary["rating_metrics"].values():
            self.assertEqual(metric, {"count": 0, "mean": None, "minimum": None, "maximum": None})
        for row in summary["variant_metrics"]:
            for metric in row["rating_metrics"].values():
                self.assertEqual(metric, {"count": 0, "mean": None, "minimum": None, "maximum": None})

    def test_duplicate_response_id_and_source_hash_fail_closed(self) -> None:
        first, second = self._record(), self._record()
        with self.assertRaisesRegex(ValueError, "duplicate response_id"):
            build_feedback_summary([first, second], report=self.report, reviews=self.reviews)
        invalid = self._record()
        invalid["source_report_canonical_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source report hash mismatch"):
            validate_feedback_record(invalid, report=self.report, reviews=self.reviews)

    def test_synthetic_fixture_and_expected_summary_are_deterministic(self) -> None:
        records = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
        summary = build_feedback_summary(
            records, report=self.report, reviews=self.reviews, summary_id="phase6-p6-6-synthetic"
        )
        self.assertEqual(canonical_feedback_summary_json(summary), SUMMARY_PATH.read_text(encoding="utf-8"))
        self.assertEqual({row["case_id"] for row in records}, {row["case_id"] for row in self.report["cases"]})
        self.assertEqual({row["variant_id"] for row in records}, set(VARIANTS))
        self.assertTrue(all(row["data_source"] == "synthetic" and row["synthetic"] for row in records))

    def test_cli_smoke_writes_expected_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "summary.json"
            completed = subprocess.run(
                [
                    "python3", str(CLI_PATH), "--feedback", str(FEEDBACK_PATH), "--output", str(output),
                    "--summary", "--overwrite", "--summary-id", "phase6-p6-6-synthetic", "--synthetic-only",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), SUMMARY_PATH.read_text(encoding="utf-8"))
            compact = json.loads(completed.stdout)
            self.assertTrue(compact["pipeline_validation_only"])
            self.assertFalse(compact["contains_real_participant_data"])

    def test_cli_accepts_only_complete_read_only_review_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            review_dir = Path(tmp_dir) / "reviews"
            review_dir.mkdir()
            for review in self.reviews:
                (review_dir / f"{review['variant_id']}.json").write_text(
                    canonical_review_json(review), encoding="utf-8"
                )
            output = Path(tmp_dir) / "summary.json"
            completed = subprocess.run(
                [
                    "python3", str(CLI_PATH), "--feedback", str(FEEDBACK_PATH), "--output", str(output),
                    "--overwrite", "--summary-id", "phase6-p6-6-synthetic", "--source-review-dir", str(review_dir),
                    "--synthetic-only",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), SUMMARY_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

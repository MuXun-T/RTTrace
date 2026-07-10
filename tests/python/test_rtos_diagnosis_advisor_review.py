from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from parser.rtos_diagnosis_advisor_review import (
    build_diagnosis_advisor_review,
    build_variant_comparison,
    canonical_review_json,
    validate_review,
)
from parser.rtos_diagnosis_replay import companion_root_for_suite, load_suite, run_replay_suite
from parser.rtos_diagnosis_report import canonical_report_json
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json"
SUITE_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/generated/phase6_synthetic_suite.json"
EXPECTED_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/advisor_reviews/phase6_advisor_review.json"
MOCK_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/advisor_reviews/mock_responses.json"
SCHEMA_PATH = ROOT / "spec/schema/rtos_diagnosis_advisor_review.schema.json"
ASSET_SCHEMA_PATH = ROOT / "spec/assets/schema/rtos_diagnosis_advisor_review.schema.json"
CLI_PATH = ROOT / "tool/run_rtos_diagnosis_advisor_review.py"


def _file_hashes() -> dict[str, str]:
    paths = [REPORT_PATH, SUITE_PATH]
    paths.extend(path for path in companion_root_for_suite(SUITE_PATH).rglob("*") if path.is_file())
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


class RtosDiagnosisAdvisorReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report_text = REPORT_PATH.read_text(encoding="utf-8")
        cls.report = json.loads(cls.report_text)
        cls.mock_response = json.loads(MOCK_PATH.read_text(encoding="utf-8"))

    def _build(self, variant_id: str, *, review_id: str = "phase6-p6-5-test") -> dict[str, object]:
        kwargs: dict[str, object] = {}
        if variant_id == "mock_llm_explanation":
            kwargs["mock_response"] = copy.deepcopy(self.mock_response)
        return build_diagnosis_advisor_review(
            copy.deepcopy(self.report), variant_id=variant_id, review_id=review_id, **kwargs
        )

    def test_template_fixture_is_canonical_and_all_variants_validate(self) -> None:
        expected = EXPECTED_PATH.read_text(encoding="utf-8")
        template = self._build("deterministic_template", review_id="phase6-p6-5-template")
        self.assertEqual(canonical_review_json(template), expected)

        expected_statuses = {
            "advisor_disabled": "disabled",
            "deterministic_template": "completed",
            "retrieval_grounded_review": "completed",
            "mock_llm_explanation": "completed",
        }
        for variant_id, status in expected_statuses.items():
            with self.subTest(variant_id=variant_id):
                review = self._build(variant_id)
                validate_review(review)
                self.assertEqual(review["status"], status)
                self.assertTrue(review["deterministic"])
                self.assertFalse(review["llm_used"])
                self.assertEqual(review["claim_class"], "report_only")

    def test_source_and_replay_inputs_remain_frozen_across_all_variants(self) -> None:
        before = _file_hashes()
        report_before = canonical_report_json(self.report)
        suite = load_suite(SUITE_PATH)
        replay_before = run_replay_suite(suite, companion_root_for_suite(SUITE_PATH))[1]

        for variant_id in ("advisor_disabled", "deterministic_template", "retrieval_grounded_review", "mock_llm_explanation"):
            self._build(variant_id)

        replay_after = run_replay_suite(suite, companion_root_for_suite(SUITE_PATH))[1]
        self.assertEqual(canonical_report_json(self.report), report_before)
        self.assertEqual(replay_after, replay_before)
        self.assertEqual(replay_after["pass_count"], 0)
        self.assertEqual(replay_after["reference_only_count"], 8)
        self.assertFalse(replay_after["all_passed"])
        self.assertEqual(_file_hashes(), before)

    def test_schema_source_asset_mirror_and_fixture_validate(self) -> None:
        self.assertEqual(SCHEMA_PATH.read_bytes(), ASSET_SCHEMA_PATH.read_bytes())
        schema = load_schema("rtos_diagnosis_advisor_review.schema.json")
        self.assertIsNone(validate_schema(schema, json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))))

    def test_variant_comparison_only_exposes_review_metrics(self) -> None:
        comparison = build_variant_comparison(
            self._build(variant_id)
            for variant_id in ("advisor_disabled", "deterministic_template", "retrieval_grounded_review", "mock_llm_explanation")
        )
        self.assertEqual(comparison["comparison_type"], "review_overlay_variant_comparison_v1")
        rows = comparison["variants"]
        self.assertEqual([row["variant_id"] for row in rows], sorted(row["variant_id"] for row in rows))
        allowed = {
            "variant_id",
            "status",
            "schema_valid",
            "deterministic_reproducible",
            "review_coverage_ratio",
            "limitation_coverage_count",
            "citation_presence_count",
            "abstention_count",
            "fallback_count",
            "adversarial_rejection_count",
        }
        for row in rows:
            self.assertEqual(set(row), allowed)
        encoded = json.dumps(comparison, sort_keys=True).lower()
        self.assertNotIn("accuracy", encoded)
        self.assertNotIn("correctness", encoded)

    def test_cli_template_and_mock_smoke_write_separate_outputs_and_summary(self) -> None:
        required_summary_keys = {
            "variant_id",
            "status",
            "cases_reviewed_count",
            "cases_abstained_count",
            "evidence_citation_count",
            "limitation_coverage_count",
            "unsupported_statement_count",
            "input_report_mutation_count",
            "root_cause_mutation_count",
            "affected_entity_mutation_count",
            "replay_status_mutation_count",
            "claim_class_mutation_count",
            "report_metric_mutation_count",
            "proof_drift_count",
            "unauthorized_action_accepted_count",
            "executable_command_count",
            "tool_invocation_count",
            "secret_leak_count",
            "absolute_path_leak_count",
            "forbidden_truth_field_count",
            "schema_invalid_count",
            "rejected_invalid_count",
            "fallback_count",
            "abstention_count",
            "prompt_injection_blocked_count",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            for variant_id, extra_args in (
                ("deterministic_template", []),
                ("mock_llm_explanation", ["--mock-response", str(MOCK_PATH)]),
            ):
                with self.subTest(variant_id=variant_id):
                    output = Path(tmp_dir) / f"{variant_id}.json"
                    completed = subprocess.run(
                        [
                            "python3",
                            str(CLI_PATH),
                            "--report",
                            str(REPORT_PATH),
                            "--variant",
                            variant_id,
                            "--output",
                            str(output),
                            "--summary",
                            "--overwrite",
                            "--review-id",
                            f"phase6-p6-5-{variant_id}",
                            *extra_args,
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    summary = json.loads(completed.stdout)
                    self.assertEqual(set(summary), required_summary_keys)
                    self.assertEqual(summary["variant_id"], variant_id)
                    self.assertEqual(summary["input_report_mutation_count"], 0)
                    self.assertEqual(summary["proof_drift_count"], 0)
                    self.assertEqual(summary["unauthorized_action_accepted_count"], 0)
                    self.assertEqual(summary["tool_invocation_count"], 0)
                    self.assertEqual(summary["secret_leak_count"], 0)
                    self.assertNotIn("accuracy", completed.stdout.lower())
                    self.assertNotIn("correctness", completed.stdout.lower())
                    validate_review(json.loads(output.read_text(encoding="utf-8")))

    def test_cli_refuses_replacing_report_existing_output_or_nonallowlisted_mock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "review.json"
            base = [
                "python3",
                str(CLI_PATH),
                "--report",
                str(REPORT_PATH),
                "--variant",
                "deterministic_template",
                "--output",
                str(output),
                "--review-id",
                "phase6-p6-5-overwrite",
            ]
            first = subprocess.run(base + ["--overwrite"], cwd=ROOT, check=False, capture_output=True, text=True)
            second = subprocess.run(base, cwd=ROOT, check=False, capture_output=True, text=True)
            same_path = subprocess.run(
                [*base[:7], str(REPORT_PATH), *base[8:]], cwd=ROOT, check=False, capture_output=True, text=True
            )
            bad_mock = subprocess.run(
                [
                    "python3",
                    str(CLI_PATH),
                    "--report",
                    str(REPORT_PATH),
                    "--variant",
                    "mock_llm_explanation",
                    "--output",
                    str(Path(tmp_dir) / "mock.json"),
                    "--review-id",
                    "phase6-p6-5-bad-mock",
                    "--mock-response",
                    str(EXPECTED_PATH),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("output exists:", second.stderr)
        self.assertNotEqual(same_path.returncode, 0)
        self.assertIn("output must be separate", same_path.stderr)
        self.assertNotEqual(bad_mock.returncode, 0)
        self.assertIn("allowlisted repository fixture", bad_mock.stderr)


if __name__ == "__main__":
    unittest.main()

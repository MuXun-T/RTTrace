from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import parser.rtos_diagnosis_report as report_module
from parser.rtos_diagnosis_report import build_diagnosis_report, canonical_report_json, validate_report
from parser.rtos_diagnosis_replay import (
    FORBIDDEN_FIELD_NAMES,
    companion_root_for_suite,
    load_suite,
    run_replay_case,
    run_replay_suite,
)
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
SUITE_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/generated/phase6_synthetic_suite.json"
COMPANION_ROOT = companion_root_for_suite(SUITE_PATH)
REPORT_FIXTURE_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json"
REPORT_SCHEMA_PATH = ROOT / "spec/schema/rtos_diagnosis_report.schema.json"
REPORT_ASSET_SCHEMA_PATH = ROOT / "spec/assets/schema/rtos_diagnosis_report.schema.json"
BENCHMARK_SCHEMA_PATHS = (
    ROOT / "spec/schema/benchmark_report.schema.json",
    ROOT / "spec/schema/benchmark_scenario.schema.json",
    ROOT / "spec/assets/schema/benchmark_report.schema.json",
    ROOT / "spec/assets/schema/benchmark_scenario.schema.json",
)
EXPECTED_CLI_SUMMARY = {
    "all_replay_passed": False,
    "all_required_cases_evaluated": True,
    "case_kind_coverage_ratio": 1.0,
    "cases_total": 8,
    "claimable_count": 0,
    "not_evaluated_count": 0,
    "preservation_complete_count": 8,
    "proof_drift_count": 0,
    "real_hardware_case_count": 0,
    "reference_only_count": 8,
    "replay_fail_count": 0,
    "replay_pass_count": 0,
    "report_only_count": 8,
    "synthetic_case_count": 8,
}


def _fixture_hashes() -> dict[str, str]:
    paths = [SUITE_PATH, *sorted(path for path in COMPANION_ROOT.rglob("*") if path.is_file())]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _file_hashes(paths: list[Path] | tuple[Path, ...]) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _assert_no_keys(value: Any, forbidden_keys: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden_keys:
                raise AssertionError(f"unexpected key {key}")
            _assert_no_keys(child, forbidden_keys)
    elif isinstance(value, list):
        for child in value:
            _assert_no_keys(child, forbidden_keys)


class RtosDiagnosisReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report_schema = load_schema("rtos_diagnosis_report.schema.json")
        cls.fixture_text = REPORT_FIXTURE_PATH.read_text(encoding="utf-8")
        cls.fixture_report = json.loads(cls.fixture_text)

    def setUp(self) -> None:
        self.suite = load_suite(SUITE_PATH)
        self.companion_root = COMPANION_ROOT
        self.results, self.replay_summary = run_replay_suite(self.suite, self.companion_root)
        self.case_by_kind = {case["case_kind"]: copy.deepcopy(case) for case in self.suite["cases"]}
        self.result_by_case_id = {row["case_id"]: copy.deepcopy(row) for row in self.results}

    def _build_report(
        self,
        *,
        suite: dict[str, Any] | None = None,
        results: list[dict[str, Any]] | None = None,
        report_id: str = "phase6-p6-4-test",
        generated_at: str | None = None,
    ) -> dict[str, Any]:
        return build_diagnosis_report(
            copy.deepcopy(self.suite if suite is None else suite),
            copy.deepcopy(self.results if results is None else results),
            report_id=report_id,
            generated_at=generated_at,
        )

    def _replace_results(self, replacements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_case_id = {row["case_id"]: copy.deepcopy(row) for row in self.results}
        for row in replacements:
            by_case_id[row["case_id"]] = copy.deepcopy(row)
        return [by_case_id[row["case_id"]] for row in self.results]

    def test_frozen_suite_and_replay_results_build_the_expected_reference_only_report(self) -> None:
        self.assertEqual(self.replay_summary["cases_total"], 8)
        self.assertEqual(self.replay_summary["pass_count"], 0)
        self.assertEqual(self.replay_summary["fail_count"], 0)
        self.assertEqual(self.replay_summary["reference_only_count"], 8)
        self.assertEqual(self.replay_summary["not_evaluated_count"], 0)
        self.assertTrue(self.replay_summary["all_required_cases_evaluated"])
        self.assertFalse(self.replay_summary["all_passed"])

        report = self._build_report()
        self.assertEqual(report["summary"]["cases_total"], 8)
        self.assertEqual(report["summary"]["replay_pass_count"], 0)
        self.assertEqual(report["summary"]["replay_fail_count"], 0)
        self.assertEqual(report["summary"]["reference_only_count"], 8)
        self.assertEqual(report["summary"]["not_evaluated_count"], 0)
        self.assertTrue(report["summary"]["all_required_cases_evaluated"])
        self.assertFalse(report["summary"]["all_replay_passed"])
        self.assertEqual(report["summary"]["evidence_retention_ratio"], 1.0)
        self.assertEqual(report["summary"]["proof_drift_count"], 0)
        self.assertEqual(report["integrity_metrics"]["fail_closed_count"], 0)
        self.assertEqual(report["case_coverage"]["case_kind_coverage_count"], 8)
        self.assertEqual(report["case_coverage"]["case_kind_coverage_ratio"], 1.0)
        self.assertEqual(report["case_coverage"]["missing_case_kinds"], [])
        self.assertEqual(
            set(report["case_coverage"]["observed_case_kinds"]),
            set(self.suite["minimum_case_kinds"]),
        )

    def test_status_sum_and_reference_only_preservation_do_not_imply_replay_pass(self) -> None:
        report = self._build_report()
        summary = report["summary"]
        self.assertEqual(
            summary["cases_total"],
            summary["replay_pass_count"]
            + summary["replay_fail_count"]
            + summary["reference_only_count"]
            + summary["not_evaluated_count"],
        )
        self.assertTrue(summary["preservation_complete"])
        self.assertEqual(summary["evidence_retention_ratio"], 1.0)
        self.assertEqual(summary["proof_drift_count"], 0)
        self.assertEqual(summary["reference_only_count"], 8)
        self.assertEqual(summary["replay_pass_count"], 0)
        self.assertTrue(summary["all_required_cases_evaluated"])
        self.assertTrue(report["replay_status_metrics"]["no_replay_failures"])
        self.assertFalse(summary["all_replay_passed"])
        for case in report["cases"]:
            with self.subTest(case_id=case["case_id"]):
                self.assertEqual(case["replay_status"], "reference_only")
                self.assertFalse(case["replay_pass"])
                self.assertTrue(case["reference_only"])
                self.assertFalse(case["fail_closed"])
                self.assertEqual(case["evidence_retention_ratio"], 1.0)
                self.assertEqual(case["proof_drift_count"], 0)

    def test_report_only_claim_counts_stay_synthetic_and_non_claimable(self) -> None:
        report = self._build_report()
        self.assertEqual(report["summary"]["synthetic_case_count"], 8)
        self.assertEqual(report["summary"]["real_hardware_case_count"], 0)
        self.assertEqual(report["summary"]["claimable_count"], 0)
        self.assertEqual(report["summary"]["report_only_count"], 8)
        self.assertEqual(report["summary"]["not_claimable_count"], 0)
        self.assertEqual(report["claim_boundary"]["synthetic_case_count"], 8)
        self.assertEqual(report["claim_boundary"]["real_hardware_case_count"], 0)
        self.assertEqual(report["claim_boundary"]["claimable_count"], 0)
        self.assertEqual(report["claim_boundary"]["report_only_count"], 8)
        self.assertEqual(report["claim_boundary"]["not_claimable_count"], 0)

    def test_missing_case_kind_metrics_can_be_reported_from_in_memory_slices(self) -> None:
        dropped_kind = "task_starvation"
        suite_slice = copy.deepcopy(self.suite)
        suite_slice["cases"] = [case for case in suite_slice["cases"] if case["case_kind"] != dropped_kind]
        result_slice = [copy.deepcopy(row) for row in self.results if row["case_kind"] != dropped_kind]
        normalized = report_module._normalize_results(result_slice, suite_slice["cases"])
        report_cases = [
            report_module._build_case_entry(case, normalized[str(case["case_id"])])
            for case in sorted(suite_slice["cases"], key=lambda row: str(row["case_id"]))
        ]
        metrics = report_module._build_metrics(suite_slice, report_cases)
        self.assertEqual(metrics["case_coverage"]["case_kind_coverage_count"], 7)
        self.assertEqual(metrics["case_coverage"]["case_kind_coverage_ratio"], 7 / 8)
        self.assertEqual(metrics["case_coverage"]["missing_case_kinds"], [dropped_kind])
        self.assertEqual(metrics["summary"]["cases_total"], 7)
        self.assertEqual(metrics["summary"]["reference_only_count"], 7)
        self.assertTrue(metrics["summary"]["all_required_cases_evaluated"])
        self.assertFalse(metrics["summary"]["all_replay_passed"])

    def test_empty_evidence_refs_denominator_uses_ratio_one(self) -> None:
        results = copy.deepcopy(self.results)
        results[0]["evidence_refs_expected"] = []
        results[0]["evidence_refs_retained"] = []
        results[0]["evidence_retention_ratio"] = 1.0
        report = self._build_report(results=results)
        first_case = next(case for case in report["cases"] if case["case_id"] == results[0]["case_id"])
        self.assertEqual(first_case["evidence_refs_expected_count"], 0)
        self.assertEqual(first_case["evidence_refs_retained_count"], 0)
        self.assertEqual(first_case["evidence_retention_ratio"], 1.0)
        self.assertEqual(report["summary"]["evidence_retention_ratio"], 1.0)
        self.assertEqual(report["summary"]["replay_pass_count"], 0)

    def test_not_evaluated_does_not_count_as_pass_or_empty_evidence_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            not_evaluated = run_replay_case(self.case_by_kind["priority_inversion"], tmp_dir)
        report = self._build_report(results=self._replace_results([not_evaluated]))
        row = next(case for case in report["cases"] if case["case_id"] == not_evaluated["case_id"])
        self.assertEqual(row["replay_status"], "not_evaluated")
        self.assertFalse(row["replay_pass"])
        self.assertFalse(row["reference_only"])
        self.assertTrue(row["fail_closed"])
        self.assertEqual(row["evidence_refs_expected_count"], 0)
        self.assertEqual(row["evidence_refs_retained_count"], 0)
        self.assertEqual(row["evidence_retention_ratio"], 0.0)
        self.assertEqual(report["summary"]["not_evaluated_count"], 1)
        self.assertEqual(report["summary"]["replay_pass_count"], 0)
        self.assertFalse(report["summary"]["all_required_cases_evaluated"])
        self.assertFalse(report["summary"]["all_replay_passed"])

    def test_tamper_detected_and_fail_closed_are_counted_separately(self) -> None:
        tamper_result = copy.deepcopy(self.result_by_case_id["phase6_synthetic_queue_wait_backlog"])
        tamper_result["replay_status"] = "fail"
        tamper_result["reference_only"] = False
        tamper_result["failure_reasons"] = ["representation_invalid:source_record_checksum"]
        drift_result = run_replay_case(
            self.case_by_kind["irq_latency_spike"],
            self.companion_root,
            full_proof_facts=[{"stable_fact": "a"}],
            replay_proof_facts=[{"stable_fact": "b"}],
        )
        report = self._build_report(results=self._replace_results([tamper_result, drift_result]))
        tamper_row = next(case for case in report["cases"] if case["case_id"] == tamper_result["case_id"])
        drift_row = next(case for case in report["cases"] if case["case_id"] == drift_result["case_id"])
        self.assertEqual(report["integrity_metrics"]["tamper_detected_count"], 1)
        self.assertEqual(report["integrity_metrics"]["fail_closed_count"], 2)
        self.assertGreater(report["integrity_metrics"]["proof_drift_count"], 0)
        self.assertTrue(tamper_row["tamper_detected"])
        self.assertTrue(tamper_row["fail_closed"])
        self.assertEqual(tamper_row["replay_status"], "fail")
        self.assertFalse(drift_row["tamper_detected"])
        self.assertTrue(drift_row["fail_closed"])
        self.assertEqual(drift_row["replay_status"], "fail")

    def test_invalid_or_inconsistent_results_are_rejected(self) -> None:
        invalid_status = copy.deepcopy(self.results)
        invalid_status[0]["replay_status"] = "unsupported"
        with self.assertRaises(ValueError):
            self._build_report(results=invalid_status)

        inconsistent_status = copy.deepcopy(self.results)
        inconsistent_status[0]["replay_pass"] = True
        with self.assertRaises(ValueError):
            self._build_report(results=inconsistent_status)

    def test_canonical_json_is_deterministic_for_identical_content(self) -> None:
        first = canonical_report_json(self._build_report(report_id="phase6-deterministic"))
        reversed_suite = copy.deepcopy(self.suite)
        reversed_suite["cases"] = list(reversed(reversed_suite["cases"]))
        second = canonical_report_json(
            self._build_report(
                suite=reversed_suite,
                results=list(reversed(copy.deepcopy(self.results))),
                report_id="phase6-deterministic",
            )
        )
        self.assertEqual(first, second)
        self.assertEqual(
            [case["case_id"] for case in json.loads(first)["cases"]],
            sorted(case["case_id"] for case in json.loads(first)["cases"]),
        )

    def test_generated_at_defaults_to_suite_and_accepts_explicit_override(self) -> None:
        default_report = self._build_report()
        self.assertEqual(default_report["generated_at"], self.suite["generated_at"])
        explicit_report = self._build_report(generated_at="2030-01-02T03:04:05Z")
        self.assertEqual(explicit_report["generated_at"], "2030-01-02T03:04:05Z")

    def test_report_schema_validation_and_asset_mirror_match(self) -> None:
        report = self._build_report(report_id="phase6-p6-4-fixture")
        self.assertIsNone(validate_schema(self.report_schema, report))
        self.assertIsNone(validate_schema(self.report_schema, self.fixture_report))
        validate_report(self.fixture_report)
        self.assertEqual(REPORT_SCHEMA_PATH.read_bytes(), REPORT_ASSET_SCHEMA_PATH.read_bytes())

    def test_forbidden_and_omitted_fields_are_rejected_and_absent_from_output(self) -> None:
        report = self._build_report()
        _assert_no_keys(report, set(FORBIDDEN_FIELD_NAMES) | set(report_module.OMITTED_OUTPUT_FIELD_NAMES))
        canonical = canonical_report_json(report)
        for forbidden in FORBIDDEN_FIELD_NAMES | report_module.OMITTED_OUTPUT_FIELD_NAMES:
            with self.subTest(field=forbidden):
                self.assertNotIn(f'"{forbidden}"', canonical)

        invalid_suite = copy.deepcopy(self.suite)
        invalid_suite["token"] = "forbidden"
        with self.assertRaises(ValueError):
            self._build_report(suite=invalid_suite)

        invalid_results = copy.deepcopy(self.results)
        invalid_results[0]["api_key"] = "forbidden"
        with self.assertRaises(ValueError):
            self._build_report(results=invalid_results)

        invalid_report = copy.deepcopy(report)
        invalid_report["cases"][0]["full_diagnosis"] = {"hidden": True}
        with self.assertRaises(ValueError):
            validate_report(invalid_report)

    def test_report_has_no_secrets_or_workspace_paths_and_needs_no_network_or_subprocess(self) -> None:
        with mock.patch("socket.create_connection", side_effect=AssertionError("network call")):
            with mock.patch("subprocess.run", side_effect=AssertionError("subprocess call")):
                with mock.patch("urllib.request.urlopen", side_effect=AssertionError("urlopen call")):
                    report = self._build_report()
        text = canonical_report_json(report)
        lowered = text.lower()
        for fragment in ("api_key", "token", "secret", "password"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, lowered)
        self.assertNotIn("/media/", text)
        self.assertNotIn("patent/realization", text)

    def test_p6_2_fixture_inputs_remain_unchanged(self) -> None:
        before = _fixture_hashes()
        report = self._build_report()
        canonical_report_json(report)
        after = _fixture_hashes()
        self.assertEqual(before, after)

    def test_existing_benchmark_schemas_remain_unchanged_and_free_of_report_content(self) -> None:
        before = _file_hashes(BENCHMARK_SCHEMA_PATHS)
        self._build_report()
        after = _file_hashes(BENCHMARK_SCHEMA_PATHS)
        self.assertEqual(before, after)
        for path in BENCHMARK_SCHEMA_PATHS:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("rtos_diagnosis", text, str(path))
            self.assertNotIn("RtosDiagnosis", text, str(path))

    def test_cli_smoke_summary_matches_current_frozen_values(self) -> None:
        before = _fixture_hashes()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "phase6_report.json"
            completed = subprocess.run(
                [
                    "python3",
                    "tool/build_rtos_diagnosis_report.py",
                    "--suite",
                    str(SUITE_PATH),
                    "--output",
                    str(output),
                    "--summary",
                    "--overwrite",
                    "--report-id",
                    "phase6-cli-smoke",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(summary, EXPECTED_CLI_SUMMARY)
        self.assertEqual(payload["summary"]["replay_pass_count"], 0)
        self.assertEqual(payload["summary"]["reference_only_count"], 8)
        self.assertFalse(payload["summary"]["all_replay_passed"])
        self.assertEqual(payload["summary"]["synthetic_case_count"], 8)
        self.assertEqual(payload["summary"]["real_hardware_case_count"], 0)
        self.assertEqual(payload["summary"]["report_only_count"], 8)
        self.assertEqual(payload["report_id"], "phase6-cli-smoke")
        self.assertIsNone(validate_schema(self.report_schema, payload))
        self.assertEqual(before, _fixture_hashes())

    def test_cli_rejects_existing_output_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "phase6_report.json"
            first = subprocess.run(
                [
                    "python3",
                    "tool/build_rtos_diagnosis_report.py",
                    "--suite",
                    str(SUITE_PATH),
                    "--output",
                    str(output),
                    "--overwrite",
                    "--report-id",
                    "phase6-cli-overwrite",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run(
                [
                    "python3",
                    "tool/build_rtos_diagnosis_report.py",
                    "--suite",
                    str(SUITE_PATH),
                    "--output",
                    str(output),
                    "--report-id",
                    "phase6-cli-overwrite",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("output exists:", second.stderr)

    def test_golden_fixture_matches_canonical_builder_output(self) -> None:
        report = self._build_report(report_id="phase6-p6-4-fixture")
        self.assertEqual(canonical_report_json(report), self.fixture_text)
        self.assertEqual(self.fixture_report["summary"]["cases_total"], 8)
        self.assertEqual(self.fixture_report["summary"]["replay_pass_count"], 0)
        self.assertEqual(self.fixture_report["summary"]["reference_only_count"], 8)
        self.assertFalse(self.fixture_report["summary"]["all_replay_passed"])


if __name__ == "__main__":
    unittest.main()

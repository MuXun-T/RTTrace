from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from parser.rtos_diagnosis_fixtures import (
    CASE_KINDS,
    DEFAULT_OUTPUT_PATH,
    FORBIDDEN_CONTENT_FRAGMENTS,
    REPLAY_PLACEHOLDER,
    SIZE_POLICY,
    build_fixture_bundle,
    companion_root_for_output,
    validate_fixture_bundle,
    write_generated_suite,
)
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema

ROOT = Path(__file__).resolve().parents[2]


class RtosDiagnosisFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = build_fixture_bundle()
        self.case_schema = load_schema("rtos_diagnosis_case.schema.json")
        self.suite_schema = load_schema("rtos_diagnosis_suite.schema.json")

    def test_bundle_covers_all_eight_case_kinds(self) -> None:
        self.assertEqual({case["case_kind"] for case in self.bundle.suite["cases"]}, set(CASE_KINDS))
        self.assertEqual(len(self.bundle.suite["cases"]), 8)

    def test_cases_include_required_fields(self) -> None:
        for case in self.bundle.suite["cases"]:
            with self.subTest(case_kind=case["case_kind"]):
                self.assertIn("baseline_trace", case)
                self.assertIn("candidate_trace", case)
                self.assertTrue(case["expected_root_cause"]["summary"])
                self.assertTrue(case["expected_affected_entity"]["entity_id"])
                self.assertTrue(case["expected_evidence_refs"])
                self.assertEqual(case["expected_closure_mode"], "reference_only")
                self.assertEqual(case["claim_class"], "report_only")

    def test_expected_root_cause_affected_entity_and_evidence_refs_are_complete(self) -> None:
        for case in self.bundle.suite["cases"]:
            with self.subTest(case_kind=case["case_kind"]):
                expected_root_cause = case["expected_root_cause"]
                expected_entity = case["expected_affected_entity"]
                evidence_refs = case["expected_evidence_refs"]
                self.assertTrue(expected_root_cause["root_cause_id"])
                self.assertEqual(expected_root_cause["root_cause_kind"], case["case_kind"])
                self.assertTrue(expected_entity["entity_kind"])
                self.assertTrue(expected_entity["entity_id"])
                self.assertGreaterEqual(len(evidence_refs), 2)
                self.assertTrue(all(ref["required"] for ref in evidence_refs))

    def test_baseline_and_candidate_trace_refs_remain_logical(self) -> None:
        for case in self.bundle.suite["cases"]:
            with self.subTest(case_kind=case["case_kind"]):
                self.assertTrue(case["baseline_trace"]["trace_ref"].startswith("logical://"))
                self.assertTrue(case["candidate_trace"]["trace_ref"].startswith("logical://"))
                self.assertNotIn("/media/", case["baseline_trace"]["trace_ref"])
                self.assertNotIn("/media/", case["candidate_trace"]["trace_ref"])

    def test_claim_boundary_stays_outside_claimable_benchmark_semantics(self) -> None:
        boundary = self.bundle.suite["claim_boundary"]
        self.assertEqual(boundary["claimable"], [])
        self.assertTrue(boundary["report_only"])
        self.assertTrue(boundary["not_claimable"])
        topics = {
            entry["topic"]
            for bucket in ("report_only", "not_claimable")
            for entry in boundary[bucket]
        }
        self.assertTrue(
            {
                "synthetic_benchmark",
                "schema_validity",
                "phase6_scope_lock",
                "top_k_root_cause",
                "human_helpfulness",
                "llm_explanation",
                "p6_2_plus_approval",
            }.issubset(topics)
        )
        for case in self.bundle.suite["cases"]:
            self.assertEqual(case["claim_class"], "report_only")

    def test_expected_replay_stays_placeholder_only(self) -> None:
        for case in self.bundle.suite["cases"]:
            with self.subTest(case_kind=case["case_kind"]):
                self.assertEqual(case["expected_replay"], REPLAY_PLACEHOLDER)

    def test_baseline_and_candidate_trace_contents_differ_for_every_case(self) -> None:
        file_map = {file.relative_path: file.content for file in self.bundle.companion_files}
        for case in CASE_KINDS:
            with self.subTest(case_kind=case):
                baseline = file_map[f"{case}/baseline.trace"]
                candidate = file_map[f"{case}/candidate.trace"]
                self.assertNotEqual(baseline, candidate)

    def test_bundle_validates_against_existing_schema(self) -> None:
        self.assertIsNone(validate_schema(self.suite_schema, self.bundle.suite))
        for case in self.bundle.suite["cases"]:
            self.assertIsNone(validate_schema(self.case_schema, case))

    def test_validate_fixture_bundle_accepts_built_bundle(self) -> None:
        validate_fixture_bundle(self.bundle)

    def test_size_policy_is_respected(self) -> None:
        suite_bytes = len((json.dumps(self.bundle.suite, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        self.assertLessEqual(suite_bytes, SIZE_POLICY["max_suite_bytes"])
        case_file_counts = {case_kind: 0 for case_kind in CASE_KINDS}
        for companion_file in self.bundle.companion_files:
            case_kind = companion_file.relative_path.split("/", 1)[0]
            case_file_counts[case_kind] += 1
            byte_count = len(companion_file.content.encode("utf-8"))
            limit = SIZE_POLICY["max_trace_bytes"] if companion_file.relative_path.endswith(".trace") else SIZE_POLICY["max_text_bytes"]
            self.assertLessEqual(byte_count, limit, companion_file.relative_path)
        for case_kind, file_count in case_file_counts.items():
            self.assertLessEqual(file_count, SIZE_POLICY["max_case_file_count"], case_kind)

    def test_fixture_material_has_no_forbidden_content_or_absolute_path(self) -> None:
        suite_text = json.dumps(self.bundle.suite, ensure_ascii=False, sort_keys=True)
        for fragment in FORBIDDEN_CONTENT_FRAGMENTS:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, suite_text.lower())
        for companion_file in self.bundle.companion_files:
            self.assertNotIn("/media/", companion_file.relative_path)
            self.assertFalse(Path(companion_file.relative_path).is_absolute())
            lowered = companion_file.content.lower()
            for fragment in FORBIDDEN_CONTENT_FRAGMENTS:
                with self.subTest(path=companion_file.relative_path, fragment=fragment):
                    self.assertNotIn(fragment, lowered)

    def test_no_evidence_package_or_replay_pass_semantics_are_present(self) -> None:
        serialized = json.dumps(self.bundle.suite, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("evidence_package", serialized)
        self.assertNotIn("full_trace_to_package", serialized)
        for case in self.bundle.suite["cases"]:
            self.assertIsNone(case["expected_replay"]["expected_replay_pass"])

    def test_default_generated_repo_files_exist_and_match_bundle(self) -> None:
        self.assertTrue(DEFAULT_OUTPUT_PATH.exists())
        companion_root = companion_root_for_output(DEFAULT_OUTPUT_PATH)
        self.assertTrue(companion_root.exists())

        generated_suite = json.loads(DEFAULT_OUTPUT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(generated_suite, self.bundle.suite)

        expected_files = {file.relative_path: file.content for file in self.bundle.companion_files}
        actual_files = {
            str(path.relative_to(companion_root)).replace("\\", "/"): path.read_text(encoding="utf-8")
            for path in companion_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual_files, expected_files)

    def test_default_generated_repo_files_have_no_forbidden_text(self) -> None:
        companion_root = companion_root_for_output(DEFAULT_OUTPUT_PATH)
        repo_files = [DEFAULT_OUTPUT_PATH, *sorted(path for path in companion_root.rglob("*") if path.is_file())]
        for path in repo_files:
            lowered = path.read_text(encoding="utf-8").lower()
            for fragment in FORBIDDEN_CONTENT_FRAGMENTS:
                with self.subTest(path=str(path), fragment=fragment):
                    self.assertNotIn(fragment, lowered)

    def test_builder_rejects_event_limit_above_hard_cap(self) -> None:
        with self.assertRaises(ValueError):
            build_fixture_bundle(SIZE_POLICY["hard_max_events_per_case"] + 1)

    def test_write_generated_suite_writes_schema_compatible_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / DEFAULT_OUTPUT_PATH.name
            summary = write_generated_suite(output_path, max_events_per_case=SIZE_POLICY["default_max_events_per_case"])
            companion_root = companion_root_for_output(output_path)
            self.assertTrue(output_path.exists())
            self.assertTrue(companion_root.exists())
            suite = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIsNone(validate_schema(self.suite_schema, suite))
            self.assertEqual(summary["case_count"], 8)
            self.assertEqual(summary["suite_path"], str(output_path))

    def test_cli_generates_output_summary_and_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "cli_suite.json"
            first = subprocess.run(
                [
                    "python3",
                    "tool/build_rtos_diagnosis_cases.py",
                    "--output",
                    str(output_path),
                    "--summary",
                    "--max-events-per-case",
                    "6",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_summary = json.loads(first.stdout)
            self.assertEqual(first_summary["max_events_per_case"], 6)
            self.assertEqual(first_summary["suite_path"], str(output_path))

            second = subprocess.run(
                [
                    "python3",
                    "tool/build_rtos_diagnosis_cases.py",
                    "--output",
                    str(output_path),
                    "--summary",
                    "--overwrite",
                    "--max-events-per-case",
                    "6",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            second_summary = json.loads(second.stdout)
            self.assertEqual(second_summary["max_events_per_case"], 6)
            suite = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIsNone(validate_schema(self.suite_schema, suite))


if __name__ == "__main__":
    unittest.main()

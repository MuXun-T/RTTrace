from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
CASE_SCHEMA_PATH = ROOT / "spec" / "schema" / "rtos_diagnosis_case.schema.json"
SUITE_SCHEMA_PATH = ROOT / "spec" / "schema" / "rtos_diagnosis_suite.schema.json"
CASE_ASSET_SCHEMA_PATH = ROOT / "spec" / "assets" / "schema" / "rtos_diagnosis_case.schema.json"
SUITE_ASSET_SCHEMA_PATH = ROOT / "spec" / "assets" / "schema" / "rtos_diagnosis_suite.schema.json"
STUB_SUITE_PATH = ROOT / "tests" / "python" / "fixtures" / "rtos_diagnosis" / "phase6_stub_suite.json"

CASE_KINDS = {
    "priority_inversion",
    "irq_latency_spike",
    "mutex_hold_inflation",
    "queue_wait_backlog",
    "task_starvation",
    "corrupt_segment",
    "stale_sidecar",
    "missing_calibration",
}

FORBIDDEN_FIELDS = {
    "proof_hash_input",
    "proof_digest_write_path",
    "raw_proof_digest_path",
    "llm_truth",
    "advisor_truth",
    "api_key",
    "token",
    "secret",
    "password",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _contains_key_value(value: Any, key: str, expected: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (child_key == key and child_value == expected)
            or _contains_key_value(child_value, key, expected)
            for child_key, child_value in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key_value(item, key, expected) for item in value)
    return False


class RtosDiagnosisSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_schema = load_schema("rtos_diagnosis_case.schema.json")
        self.suite_schema = load_schema("rtos_diagnosis_suite.schema.json")
        self.stub_suite = _read_json(STUB_SUITE_PATH)
        self.valid_case = copy.deepcopy(self.stub_suite["cases"][0])

    def _boundary_entries(self, topic: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for bucket in ("claimable", "report_only", "not_claimable"):
            entries.extend(
                entry
                for entry in self.stub_suite["claim_boundary"][bucket]
                if entry["topic"] == topic
            )
        return entries

    def _assert_forbidden_field_rejected(self, field: str) -> None:
        target_files = [
            CASE_SCHEMA_PATH,
            SUITE_SCHEMA_PATH,
            CASE_ASSET_SCHEMA_PATH,
            SUITE_ASSET_SCHEMA_PATH,
            STUB_SUITE_PATH,
        ]
        for path in target_files:
            self.assertNotIn(field, path.read_text(encoding="utf-8"), str(path))

        invalid_case = copy.deepcopy(self.valid_case)
        invalid_case[field] = "forbidden"
        self.assertIsNotNone(validate_schema(self.case_schema, invalid_case))

        invalid_suite = copy.deepcopy(self.stub_suite)
        invalid_suite[field] = "forbidden"
        self.assertIsNotNone(validate_schema(self.suite_schema, invalid_suite))

    def test_single_valid_case_validates_against_case_schema(self) -> None:
        self.assertIsNone(validate_schema(self.case_schema, self.valid_case))

    def test_phase6_stub_suite_validates_against_suite_schema(self) -> None:
        self.assertIsNone(validate_schema(self.suite_schema, self.stub_suite))
        for case in self.stub_suite["cases"]:
            self.assertIsNone(validate_schema(self.case_schema, case))

    def test_stub_suite_covers_all_required_case_kinds(self) -> None:
        case_kinds = {case["case_kind"] for case in self.stub_suite["cases"]}
        self.assertEqual(case_kinds, CASE_KINDS)
        self.assertEqual(set(self.stub_suite["minimum_case_kinds"]), CASE_KINDS)
        self.assertEqual(len(self.stub_suite["cases"]), 8)

    def test_illegal_case_kind_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.valid_case)
        invalid["case_kind"] = "timer_drift"
        self.assertIsNotNone(validate_schema(self.case_schema, invalid))

    def test_missing_expected_root_cause_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.valid_case)
        del invalid["expected_root_cause"]
        reason = validate_schema(self.case_schema, invalid)
        self.assertIsNotNone(reason)
        self.assertIn("expected_root_cause", reason)

    def test_claim_class_non_enum_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.valid_case)
        invalid["claim_class"] = "truth"
        self.assertIsNotNone(validate_schema(self.case_schema, invalid))

    def test_schema_and_fixture_forbid_proof_hash_input(self) -> None:
        self._assert_forbidden_field_rejected("proof_hash_input")

    def test_schema_and_fixture_forbid_proof_digest_write_path(self) -> None:
        self._assert_forbidden_field_rejected("proof_digest_write_path")

    def test_schema_and_fixture_forbid_raw_proof_digest_path(self) -> None:
        self._assert_forbidden_field_rejected("raw_proof_digest_path")

    def test_schema_and_fixture_forbid_llm_truth(self) -> None:
        self._assert_forbidden_field_rejected("llm_truth")

    def test_schema_and_fixture_forbid_advisor_truth(self) -> None:
        self._assert_forbidden_field_rejected("advisor_truth")

    def test_schema_and_fixture_forbid_secret_field_names(self) -> None:
        for field in ("api_key", "token", "secret", "password"):
            with self.subTest(field=field):
                self._assert_forbidden_field_rejected(field)

    def test_top_k_and_human_helpfulness_are_not_claimable(self) -> None:
        for topic in ("top_k_root_cause", "human_helpfulness"):
            with self.subTest(topic=topic):
                entries = self._boundary_entries(topic)
                self.assertTrue(entries)
                self.assertNotIn("claimable", {entry["claim_class"] for entry in entries})

    def test_suite_contains_required_claim_boundary_topics(self) -> None:
        topics = {
            entry["topic"]
            for bucket in ("claimable", "report_only", "not_claimable")
            for entry in self.stub_suite["claim_boundary"][bucket]
        }
        self.assertTrue(
            {
                "top_k_root_cause",
                "human_helpfulness",
                "llm_explanation",
                "synthetic_benchmark",
                "p6_2_plus_approval",
                "stub_suite_status",
            }.issubset(topics)
        )

    def test_existing_benchmark_report_schema_has_no_rtos_diagnosis_content(self) -> None:
        text = (ROOT / "spec" / "schema" / "benchmark_report.schema.json").read_text(encoding="utf-8")
        self.assertNotIn("rtos_diagnosis", text)
        self.assertNotIn("RtosDiagnosis", text)

    def test_existing_benchmark_scenario_schema_has_no_rtos_diagnosis_content(self) -> None:
        text = (ROOT / "spec" / "schema" / "benchmark_scenario.schema.json").read_text(encoding="utf-8")
        self.assertNotIn("rtos_diagnosis", text)
        self.assertNotIn("RtosDiagnosis", text)

    def test_stub_suite_is_not_marked_as_real_benchmark_completed(self) -> None:
        entries = self._boundary_entries("stub_suite_status")
        self.assertTrue(entries)
        self.assertNotIn("claimable", {entry["claim_class"] for entry in entries})
        self.assertTrue(any("not completed" in entry["statement"] for entry in entries))
        self.assertFalse(_contains_key_value(self.stub_suite, "real_benchmark_completed", True))

    def test_p6_2_plus_is_not_marked_approved(self) -> None:
        entries = self._boundary_entries("p6_2_plus_approval")
        self.assertTrue(entries)
        self.assertNotIn("claimable", {entry["claim_class"] for entry in entries})
        self.assertTrue(any("not approved" in entry["statement"] for entry in entries))
        self.assertFalse(_contains_key_value(self.stub_suite, "p6_2_plus_approved", True))

    def test_source_and_asset_schemas_are_byte_equal(self) -> None:
        for source, asset in (
            (CASE_SCHEMA_PATH, CASE_ASSET_SCHEMA_PATH),
            (SUITE_SCHEMA_PATH, SUITE_ASSET_SCHEMA_PATH),
        ):
            with self.subTest(schema=source.name):
                self.assertEqual(source.read_bytes(), asset.read_bytes())


if __name__ == "__main__":
    unittest.main()

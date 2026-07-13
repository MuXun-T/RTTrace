from __future__ import annotations

from pathlib import Path
import unittest
from unittest import mock

import parser.external_evidence_closure_validator as closure
from parser.external_case_package_evidence import read_regular_no_follow


ROOT = Path(__file__).resolve().parents[2]


class ExternalEvidenceClosureValidatorTests(unittest.TestCase):
    def test_all_supported_cases_have_their_applicable_closure(self) -> None:
        for case_id in ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k"):
            with self.subTest(case_id=case_id):
                value = closure.validate_evidence_closure(case_id)
                self.assertEqual(value.closure_result.closure_state.value, "passed")
                self.assertEqual(value.closure_result.required_artifact_count, 18)
                self.assertTrue(all(item.matched for item in value.identity_bindings))
        for case_id in ("zephyr", "zephelin"):
            with self.subTest(case_id=case_id):
                value = closure.validate_evidence_closure(case_id)
                self.assertEqual(value.closure_result.closure_state.value, "passed")
                self.assertEqual(value.closure_result.required_artifact_count, 8)
                self.assertEqual([item.binding_kind for item in value.identity_bindings], ["p7_2_to_p7_4_source_identity"])

    def test_embedded_binding_identity_and_fixture_sha_are_independent_checks(self) -> None:
        value = closure.validate_evidence_closure("freertos_btf_1core")
        facts = {item.binding_kind: item for item in value.identity_bindings}
        self.assertEqual(facts["binding_fixture_sha256"].expected_identity, closure.ACQUIRED_BINDING_FIXTURE_SHA256["freertos_btf_1core"])
        self.assertNotEqual(facts["binding_fixture_sha256"].expected_identity, facts["embedded_binding_identity"].expected_identity)
        self.assertTrue({"p7_3_manifest_sha256", "p7_3_source_identity", "p7_3_artifact_identity", "p7_3_report_sha256", "p7_4_replay_report_sha256"}.issubset(facts))

    def test_p7_2_provenance_license_checksum_and_legacy_report_are_baseline_bound(self) -> None:
        with mock.patch.dict(closure.P7_2_PROVENANCE_INPUT_SHA256, {"tests/python/fixtures/external_validation/sources/zephyr_pipeline/LICENSE": "0" * 64}):
            with self.assertRaises(ValueError):
                closure.validate_evidence_closure("zephyr")
        with mock.patch.object(closure, "LEGACY_OPENED_REPORT_SHA256", "0" * 64):
            with self.assertRaises(ValueError):
                closure.validate_evidence_closure("freertos_btf_1core")

    def test_missing_or_replaced_frozen_inputs_fail_closed(self) -> None:
        expected_path = "tests/python/fixtures/external_validation/replay/expected/freertos_btf_1core.expected.json"
        raw_path = "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.btf"

        def missing(path: str) -> bytes:
            if path == expected_path:
                raise FileNotFoundError(path)
            return read_regular_no_follow(path)

        with mock.patch("parser.external_validation_intake.read_regular_no_follow", side_effect=missing):
            with self.assertRaises((FileNotFoundError, ValueError)):
                closure.validate_evidence_closure("freertos_btf_1core")

        def replaced(path: str) -> bytes:
            return b"replacement" if path == raw_path else read_regular_no_follow(path)

        with mock.patch("parser.external_validation_intake.read_regular_no_follow", side_effect=replaced):
            with self.assertRaises(ValueError):
                closure.validate_evidence_closure("freertos_btf_1core")

    def test_wrong_binding_fixture_hash_or_swapped_case_fails_closed(self) -> None:
        with mock.patch.dict(closure.ACQUIRED_BINDING_FIXTURE_SHA256, {"freertos_btf_1core": "0" * 64}):
            with self.assertRaises(ValueError):
                closure.validate_evidence_closure("freertos_btf_1core")

        target = "tests/python/fixtures/external_validation/evidence_bindings/freertos_btf_1core.json"
        swapped = (ROOT / "tests/python/fixtures/external_validation/evidence_bindings/freertos_btf_4cores.json").read_bytes()

        def swap(path: str) -> bytes:
            return swapped if path == target else read_regular_no_follow(path)

        with mock.patch("parser.external_validation_intake.read_regular_no_follow", side_effect=swap):
            with self.assertRaises(ValueError):
                closure.validate_evidence_closure("freertos_btf_1core")


if __name__ == "__main__":
    unittest.main()

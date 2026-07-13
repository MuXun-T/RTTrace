from __future__ import annotations

import copy
import unittest
from unittest import mock

import parser.external_replay_equivalence_validator as equivalence


class ExternalReplayEquivalenceValidatorTests(unittest.TestCase):
    def test_all_frozen_cases_reproduce_exactly(self) -> None:
        expected = {
            "freertos_btf_1core": ("replay_pass", None),
            "freertos_vcd_1core": ("replay_fail", "UNSUPPORTED_REQUIRED_RECORD"),
            "freertos_btf_4cores": ("replay_pass", None),
            "freertos_btf_50k": ("replay_fail", "TIMESTAMP_REGRESSION"),
            "zephyr": ("reference_only", "SOURCE_ACQUISITION_BLOCKED"),
            "zephelin": ("reference_only", "SOURCE_EXTERNAL_REFERENCE_ONLY"),
        }
        for case_id, replay_state in expected.items():
            with self.subTest(case_id=case_id):
                value = equivalence.reproduce_and_compare_p7_4(case_id)
                self.assertEqual((value.reproduced_report["replay_state"], value.reproduced_report["primary_reason"]), replay_state)
                self.assertTrue(value.reproduction_result.canonical_bytes_matched)
                self.assertTrue(value.reproduction_result.canonical_sha256_matched)
                self.assertTrue(value.equivalence_result.equivalent)
                self.assertEqual(value.equivalence_result.mismatch_fields, ())

    def test_reproduced_replay_fail_is_not_upgraded(self) -> None:
        for case_id, reason in (("freertos_vcd_1core", "UNSUPPORTED_REQUIRED_RECORD"), ("freertos_btf_50k", "TIMESTAMP_REGRESSION")):
            with self.subTest(case_id=case_id):
                value = equivalence.reproduce_and_compare_p7_4(case_id)
                self.assertEqual(value.reproduced_report["replay_state"], "replay_fail")
                self.assertEqual(value.reproduced_report["primary_reason"], reason)
                self.assertTrue(value.equivalence_result.equivalent)

    def test_reference_only_reproduction_does_not_invoke_replay(self) -> None:
        with mock.patch("parser.external_replay_equivalence_validator.replay", side_effect=AssertionError):
            for case_id in ("zephyr", "zephelin"):
                with self.subTest(case_id=case_id):
                    value = equivalence.reproduce_and_compare_p7_4(case_id)
                    self.assertEqual(value.reproduced_report["replay_state"], "reference_only")
                    self.assertTrue(value.equivalence_result.equivalent)

    def test_state_reason_identity_and_canonical_mismatches_fail_equivalence(self) -> None:
        frozen = equivalence.reproduce_and_compare_p7_4("freertos_btf_1core").frozen_report
        changed = copy.deepcopy(frozen)
        changed["replay_state"] = "replay_fail"
        changed["primary_reason"] = "TIMESTAMP_REGRESSION"
        changed["reason_codes"] = ["TIMESTAMP_REGRESSION"]
        changed["trace_identity"] = "0" * 64
        with mock.patch("parser.external_replay_equivalence_validator._direct_replay", return_value=changed):
            value = equivalence.reproduce_and_compare_p7_4("freertos_btf_1core")
        self.assertFalse(value.reproduction_result.canonical_bytes_matched)
        self.assertFalse(value.reproduction_result.canonical_sha256_matched)
        self.assertFalse(value.equivalence_result.equivalent)
        self.assertEqual(value.equivalence_result.mismatch_fields, ("canonical_report_bytes", "primary_reason", "reason_codes", "replay_state", "trace_identity"))


if __name__ == "__main__":
    unittest.main()

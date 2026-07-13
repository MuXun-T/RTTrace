from __future__ import annotations

from unittest import mock
import unittest

from parser.external_layered_validation import validate_case
from parser.external_layered_validation_models import ValidationState


class ExternalLayeredValidationTests(unittest.TestCase):
    def test_all_cases_preserve_replay_state_and_return_their_validation_state(self) -> None:
        expected = {
            "freertos_btf_1core": ("replay_pass", ValidationState.VALIDATION_PASS),
            "freertos_vcd_1core": ("replay_fail", ValidationState.VALIDATION_PASS),
            "freertos_btf_4cores": ("replay_pass", ValidationState.VALIDATION_PASS),
            "freertos_btf_50k": ("replay_fail", ValidationState.VALIDATION_PASS),
            "zephyr": ("reference_only", ValidationState.REFERENCE_ONLY),
            "zephelin": ("reference_only", ValidationState.REFERENCE_ONLY),
        }
        for case_id, result in expected.items():
            with self.subTest(case_id=case_id):
                report = validate_case(case_id)
                self.assertEqual((report.source_replay_state, report.validation_state), result)
                self.assertFalse(report.hardware_validation)
                self.assertFalse(report.proof_parity_eligibility.proof_parity_eligible)
                self.assertEqual(report.proof_parity_eligibility.proof_parity.value, "not_evaluated")
                self.assertEqual(report.proof_fact_drift.comparable_proof_fact_count, 0)

    def test_closure_failure_is_a_validation_failure_without_replay_state_upgrade(self) -> None:
        with mock.patch("parser.external_layered_validation.validate_evidence_closure", side_effect=ValueError("binding fixture SHA-256 does not match frozen input")):
            report = validate_case("freertos_vcd_1core")
        self.assertEqual(report.source_replay_state, "replay_fail")
        self.assertEqual(report.validation_state, ValidationState.VALIDATION_FAIL)
        self.assertEqual(report.primary_reason.value, "BINDING_FIXTURE_HASH_MISMATCH")
        self.assertEqual(report.layer_results[6].layer_state.value, "failed")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest import mock

from parser.external_validation_intake import ACQUIRED_CASES, REFERENCE_ONLY_CASES, case_input_paths, intake_case


class ExternalValidationIntakeTests(unittest.TestCase):
    def test_acquired_and_reference_only_input_sets_are_closed(self) -> None:
        for case_id in ACQUIRED_CASES:
            with self.subTest(case_id=case_id):
                intake = intake_case(case_id)
                self.assertTrue(intake.acquired)
                self.assertEqual(len(intake.logical_paths), 18)
                self.assertIn(f"tests/python/fixtures/external_validation/evidence_bindings/{case_id}.json", intake.logical_paths)
                self.assertIn(f"tests/python/fixtures/external_validation/replay/expected/{case_id}.expected.json", intake.logical_paths)
        for case_id in REFERENCE_ONLY_CASES:
            with self.subTest(case_id=case_id):
                intake = intake_case(case_id)
                self.assertFalse(intake.acquired)
                self.assertEqual(len(intake.logical_paths), 8)
                self.assertFalse(any("evidence_bindings" in path or "/expected/" in path for path in intake.logical_paths))

    def test_unknown_case_and_post_intake_mutation_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            case_input_paths("unknown")
        intake = intake_case("freertos_btf_1core")
        with mock.patch("parser.external_validation_intake.read_regular_no_follow", return_value=b"replacement"):
            with self.assertRaises(ValueError):
                intake.assert_unchanged()


if __name__ == "__main__":
    unittest.main()

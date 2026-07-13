from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from parser.external_layered_validation_models import LayeredValidationReport
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema
from tool.run_external_layered_validation import main


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "tests/python/fixtures/external_validation/layered_validation/reports"
CANONICAL_HASHES = {
    "freertos_btf_1core": "c307bfab9555055801fc85609865de4e9fdff136d18d7d7bad277c614a25e9bd",
    "freertos_vcd_1core": "c37c3fa77cb15b80f98ba765917e7f13efc3ac9d7a6dbe54f5cb0e5fa036100c",
    "freertos_btf_4cores": "a492a7bcfd5b85d42ce1b5e82aa80c747d5f5ffc9e58306555f3a733657bbf5a",
    "freertos_btf_50k": "cf107a70473b0d483c0fc5acc14f3efd604687d55efc56b4f9a682abfdfeb60f",
    "zephyr": "d0b83fb48fc821fd40a54eb7064f50f093a5dbfaad70cb0c2fa910853485c5c6",
    "zephelin": "ef0ac521d6a24fdc4a86bdc8d1af54b12313df149c39130e7846956f6387769d",
}
EXPECTED = {
    "freertos_btf_1core": (0, "replay_pass", "validation_pass"),
    "freertos_vcd_1core": (0, "replay_fail", "validation_pass"),
    "freertos_btf_4cores": (0, "replay_pass", "validation_pass"),
    "freertos_btf_50k": (0, "replay_fail", "validation_pass"),
    "zephyr": (2, "reference_only", "reference_only"),
    "zephelin": (2, "reference_only", "reference_only"),
}
COMPARISON_FIELDS = ("validation_state", "source_replay_state", "applicable_layers", "layer_results", "identity_bindings", "closure_result", "reproduction_result", "equivalence_result", "replay_fact_drift", "proof_fact_drift", "proof_parity_eligibility", "proof_parity_eligible", "proof_parity", "proof_correctness", "primary_reason", "reason_codes", "hardware_validation")


class ExternalLayeredValidationIntegrationTests(unittest.TestCase):
    def test_cli_reproduces_every_canonical_report_twice(self) -> None:
        schema = load_schema("external_layered_validation_report.schema.json")
        for case_id, (exit_code, replay_state, validation_state) in EXPECTED.items():
            with self.subTest(case_id=case_id), tempfile.TemporaryDirectory() as directory:
                first = Path(directory) / "first.json"
                second = Path(directory) / "second.json"
                self.assertEqual(main(["--case", case_id, "--output", str(first)]), exit_code)
                self.assertEqual(main(["--case", case_id, "--output", str(second)]), exit_code)
                fixture = (REPORT_ROOT / f"{case_id}.json").read_bytes()
                self.assertEqual(first.read_bytes(), second.read_bytes())
                self.assertEqual(first.read_bytes(), fixture)
                self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), CANONICAL_HASHES[case_id])
                first_value = json.loads(first.read_bytes())
                second_value = json.loads(second.read_bytes())
                fixture_value = json.loads(fixture)
                self.assertEqual((first_value["source_replay_state"], first_value["validation_state"]), (replay_state, validation_state))
                self.assertIsNone(validate_schema(schema, first_value))
                self.assertEqual(LayeredValidationReport.from_dict(first_value).to_dict(), first_value)
                for field in COMPARISON_FIELDS:
                    self.assertEqual(first_value[field], second_value[field])
                    self.assertEqual(first_value[field], fixture_value[field])

    def test_fixture_hashes_are_complete_and_stable(self) -> None:
        self.assertEqual({path.stem for path in REPORT_ROOT.glob("*.json")}, set(CANONICAL_HASHES))
        for case_id, expected in CANONICAL_HASHES.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(hashlib.sha256((REPORT_ROOT / f"{case_id}.json").read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()

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
    "freertos_btf_1core": "3a780068900a56614c7ee48fb28f17a23c174582c8ce877d8ba4b4b36e6d4845",
    "freertos_vcd_1core": "35573f907a1bb40d93f0c28f2a5ec4efec324cd1b666722c7481bbf74136bb84",
    "freertos_btf_4cores": "fe1264e642f56d21fb605077196dffe36cbec0128216c0878c28bf79a12a52d7",
    "freertos_btf_50k": "d10399ab2ebc4204d801af178d01d198e320731969f18aee3c22f6d341f8245b",
    "zephyr": "5749bc2d9d262c40abd0b8e6d3218fa72d2c4a42d25af93bf392882a8623e1d6",
    "zephelin": "4036ad2a2df1ac6776f39fda74e362c638df3839a3e44c83f7fdceb1aa662e3f",
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

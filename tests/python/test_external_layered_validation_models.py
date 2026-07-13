from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from parser.external_layered_validation_models import (
    ClosureResult,
    EquivalenceResult,
    IdentityBinding,
    LAYER_ORDER,
    LayerResult,
    LayerState,
    LayeredValidationReport,
    ProofCorrectness,
    ProofFactDrift,
    ProofParity,
    ProofParityEligibility,
    ReplayFactDrift,
    ReproductionResult,
    ValidationLayer,
    ValidationProfile,
    ValidationReason,
    ValidationState,
    canonical_json,
)
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "tests/python/fixtures/external_validation/layered_validation/profiles/p7.5-layered-validation-v1.json"


def profile() -> ValidationProfile:
    return ValidationProfile.from_dict(json.loads(PROFILE_PATH.read_bytes()))


def report() -> LayeredValidationReport:
    value = profile()
    return LayeredValidationReport(
        ValidationState.VALIDATION_PASS,
        "freertos_vcd_1core",
        "replay_fail",
        value.profile_id,
        value.profile_identity,
        LAYER_ORDER,
        tuple(LayerResult(layer, LayerState.PASSED, ()) for layer in LAYER_ORDER),
        (IdentityBinding("binding_fixture_sha256", "a" * 64, "a" * 64, True), IdentityBinding("embedded_binding_identity", "b" * 64, "b" * 64, True)),
        ClosureResult(8, 8, LayerState.PASSED),
        ReproductionResult(True, True, True, "c" * 64),
        EquivalenceResult(True, 17, ()),
        ReplayFactDrift(17, 0, ()),
        ProofFactDrift(0, 0, ()),
        ProofParityEligibility(False, ProofParity.NOT_EVALUATED, "FROZEN_PROOF_REPRESENTATION_UNAVAILABLE", ProofCorrectness.NOT_EVALUATED),
        None,
        (),
    )


class ExternalLayeredValidationModelTests(unittest.TestCase):
    def test_profile_is_closed_immutable_and_canonical(self) -> None:
        value = profile()
        self.assertEqual(ValidationProfile.from_dict(value.to_dict()), value)
        self.assertEqual(canonical_json(value.to_dict()), PROFILE_PATH.read_bytes())
        with self.assertRaises(FrozenInstanceError):
            value.profile_id = "changed"  # type: ignore[misc]
        bad = value.to_dict(); bad["extra"] = True
        with self.assertRaises(ValueError):
            ValidationProfile.from_dict(bad)

    def test_profile_schema_and_mirror(self) -> None:
        name = "external_validation_profile.schema.json"
        self.assertEqual((ROOT / "spec/schema" / name).read_bytes(), (ROOT / "spec/assets/schema" / name).read_bytes())
        self.assertIsNone(validate_schema(load_schema(name), profile().to_dict()))
        bad = profile().to_dict(); bad["hardware_validation"] = True
        self.assertIsNotNone(validate_schema(load_schema(name), bad))

    def test_report_is_closed_and_replay_fail_can_validate(self) -> None:
        value = report()
        self.assertEqual(LayeredValidationReport.from_dict(value.to_dict()), value)
        self.assertEqual(value.validation_state, ValidationState.VALIDATION_PASS)
        self.assertEqual(value.source_replay_state, "replay_fail")
        self.assertEqual(value.proof_fact_drift.comparable_proof_fact_count, 0)
        self.assertFalse(value.proof_parity_eligibility.proof_parity_eligible)
        self.assertEqual(value.proof_parity_eligibility.proof_parity, ProofParity.NOT_EVALUATED)
        with self.assertRaises(FrozenInstanceError):
            value.validation_state = ValidationState.VALIDATION_FAIL  # type: ignore[misc]

    def test_report_schema_and_mirror(self) -> None:
        name = "external_layered_validation_report.schema.json"
        self.assertEqual((ROOT / "spec/schema" / name).read_bytes(), (ROOT / "spec/assets/schema" / name).read_bytes())
        self.assertIsNone(validate_schema(load_schema(name), report().to_dict()))
        bad = copy.deepcopy(report().to_dict()); bad["extra"] = True
        self.assertIsNotNone(validate_schema(load_schema(name), bad))

    def test_ordering_and_empty_proof_domain_rules_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            LayerResult(ValidationLayer.SOURCE_IDENTITY, LayerState.FAILED, ())
        with self.assertRaises(ValueError):
            LayerResult(ValidationLayer.SOURCE_IDENTITY, LayerState.PASSED, (ValidationReason.CHECKSUM_MISMATCH,))
        with self.assertRaises(ValueError):
            ProofParityEligibility(False, ProofParity.PARITY_PASS, "FROZEN_PROOF_REPRESENTATION_UNAVAILABLE")
        with self.assertRaises(ValueError):
            ReplayFactDrift(0, 1, ())
        value = report().to_dict(); value["reason_codes"] = ["CHECKSUM_MISMATCH"]
        with self.assertRaises(ValueError):
            LayeredValidationReport.from_dict(value)


if __name__ == "__main__":
    unittest.main()

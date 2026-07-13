from __future__ import annotations

from pathlib import Path
import unittest

from parser.external_proof_parity_gate import FROZEN_PROOF_REPRESENTATION_UNAVAILABLE, evaluate_proof_parity_eligibility


ROOT = Path(__file__).resolve().parents[2]


class ExternalProofParityGateTests(unittest.TestCase):
    def test_empty_proof_domain_is_ineligible_not_parity(self) -> None:
        drift, eligibility = evaluate_proof_parity_eligibility()
        self.assertEqual(drift.comparable_proof_fact_count, 0)
        self.assertEqual(drift.proof_drift_count, 0)
        self.assertEqual(drift.proof_drift_items, ())
        self.assertFalse(eligibility.proof_parity_eligible)
        self.assertEqual(eligibility.proof_parity.value, "not_evaluated")
        self.assertEqual(eligibility.proof_parity_reason, FROZEN_PROOF_REPRESENTATION_UNAVAILABLE)
        self.assertEqual(eligibility.proof_correctness.value, "not_evaluated")

    def test_gate_does_not_read_or_model_phase6_proof_facts(self) -> None:
        source = (ROOT / "parser/external_proof_parity_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("phase6", source.lower())
        self.assertNotIn("proof_digest", source)
        self.assertNotIn("evidence_proof", source)


if __name__ == "__main__":
    unittest.main()

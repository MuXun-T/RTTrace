"""P7.5 proof-parity gate: no frozen comparable proof domain exists."""

from __future__ import annotations

from parser.external_layered_validation_models import ProofFactDrift, ProofParity, ProofParityEligibility


FROZEN_PROOF_REPRESENTATION_UNAVAILABLE = "FROZEN_PROOF_REPRESENTATION_UNAVAILABLE"


def evaluate_proof_parity_eligibility() -> tuple[ProofFactDrift, ProofParityEligibility]:
    """Return the only supported P7.5 proof result without reading proof inputs."""
    return (
        ProofFactDrift(0, 0, ()),
        ProofParityEligibility(False, ProofParity.NOT_EVALUATED, FROZEN_PROOF_REPRESENTATION_UNAVAILABLE),
    )

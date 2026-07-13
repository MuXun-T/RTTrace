"""P7.5 orchestration over frozen P7.2--P7.4 validation evidence."""

from __future__ import annotations

import json
from typing import Mapping

from parser.external_evidence_closure_validator import EvidenceClosureValidation, validate_evidence_closure
from parser.external_layered_validation_models import (
    ClosureResult,
    EquivalenceResult,
    LAYER_ORDER,
    LayerResult,
    LayerState,
    LayeredValidationReport,
    ProofFactDrift,
    ReproductionResult,
    ReplayFactDrift,
    ValidationLayer,
    ValidationProfile,
    ValidationReason,
    ValidationState,
    ordered_reasons,
)
from parser.external_proof_parity_gate import evaluate_proof_parity_eligibility
from parser.external_replay_equivalence_validator import ReplayEquivalenceValidation, reproduce_and_compare_p7_4
from parser.external_validation_drift import analyze_replay_fact_drift
from parser.external_validation_intake import REFERENCE_ONLY_CASES, intake_case
from parser.external_case_package_evidence import read_regular_no_follow


PROFILE_PATH = "tests/python/fixtures/external_validation/layered_validation/profiles/p7.5-layered-validation-v1.json"


def load_validation_profile() -> ValidationProfile:
    try:
        value = json.loads(read_regular_no_follow(PROFILE_PATH).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("P7.5 validation profile is invalid") from exc
    return ValidationProfile.from_dict(value)


def _source_replay_state(case_id: str) -> str:
    intake = intake_case(case_id)
    report = json.loads(intake.raw(f"tests/python/fixtures/external_validation/replay/reports/{case_id}.json").decode("utf-8"))
    if not isinstance(report, Mapping) or not isinstance(report.get("replay_state"), str):
        raise ValueError("frozen P7.4 report state is invalid")
    return report["replay_state"]


def _reason_from_error(error: Exception) -> tuple[ValidationLayer, ValidationReason]:
    message = str(error)
    if "binding fixture SHA-256" in message:
        return ValidationLayer.CROSS_PHASE_IDENTITY_BINDING, ValidationReason.BINDING_FIXTURE_HASH_MISMATCH
    if "embedded binding" in message or "cross-phase" in message or "identity does not bind" in message:
        return ValidationLayer.CROSS_PHASE_IDENTITY_BINDING, ValidationReason.BINDING_IDENTITY_MISMATCH
    if "schema" in message:
        return ValidationLayer.P7_4_REPORT_SCHEMA, ValidationReason.P7_4_REPORT_SCHEMA_INVALID
    if "raw trace" in message or "checksum" in message:
        return ValidationLayer.CHECKSUM_INTEGRITY, ValidationReason.CHECKSUM_MISMATCH
    if "mutated" in message:
        return ValidationLayer.EVIDENCE_CLOSURE, ValidationReason.FROZEN_INPUT_MUTATED
    return ValidationLayer.EVIDENCE_CLOSURE, ValidationReason.EVIDENCE_CLOSURE_INCOMPLETE


def _base_layer_results(reference_only: bool) -> dict[ValidationLayer, LayerResult]:
    results: dict[ValidationLayer, LayerResult] = {}
    for layer in LAYER_ORDER:
        state = LayerState.PASSED
        if reference_only and layer in {ValidationLayer.ARTIFACT_IDENTITY, ValidationLayer.CHECKSUM_INTEGRITY, ValidationLayer.PACKAGE_COMPLETENESS}:
            state = LayerState.NOT_APPLICABLE
        if layer is ValidationLayer.PROOF_FACT_COMPARABILITY:
            state = LayerState.NOT_APPLICABLE
        results[layer] = LayerResult(layer, state, ())
    return results


def _set_failure(results: dict[ValidationLayer, LayerResult], layer: ValidationLayer, reason: ValidationReason) -> None:
    results[layer] = LayerResult(layer, LayerState.FAILED, (reason,))


def _report(
    case_id: str,
    source_replay_state: str,
    profile: ValidationProfile,
    results: dict[ValidationLayer, LayerResult],
    closure: ClosureResult,
    reproduction: ReproductionResult,
    equivalence: EquivalenceResult,
    replay_drift: ReplayFactDrift,
    closure_validation: EvidenceClosureValidation | None,
    reasons: tuple[ValidationReason, ...],
) -> LayeredValidationReport:
    proof_drift, proof_gate = evaluate_proof_parity_eligibility()
    if proof_drift != ProofFactDrift(0, 0, ()) or proof_gate.proof_parity_eligible or proof_gate.proof_parity.value != "not_evaluated" or proof_gate.proof_correctness.value != "not_evaluated":
        raise ValueError("P7.5 proof parity injection is unsupported")
    ordered = ordered_reasons(reasons)
    failed = any(item.layer_state is LayerState.FAILED for item in results.values())
    state = ValidationState.REFERENCE_ONLY if case_id in REFERENCE_ONLY_CASES and not failed else ValidationState.VALIDATION_FAIL if failed else ValidationState.VALIDATION_PASS
    return LayeredValidationReport(
        state,
        case_id,
        source_replay_state,
        profile.profile_id,
        profile.profile_identity,
        LAYER_ORDER,
        tuple(results[layer] for layer in LAYER_ORDER),
        () if closure_validation is None else closure_validation.identity_bindings,
        closure,
        reproduction,
        equivalence,
        replay_drift,
        proof_drift,
        proof_gate,
        ordered[0] if ordered else None,
        ordered,
    )


def validate_case(case_id: str) -> LayeredValidationReport:
    profile = load_validation_profile()
    source_replay_state = _source_replay_state(case_id)
    results = _base_layer_results(case_id in REFERENCE_ONLY_CASES)
    empty_reproduction = ReproductionResult(False, False, False, None)
    empty_equivalence = EquivalenceResult(False, 0, ("not_attempted",))
    empty_drift = ReplayFactDrift(0, 0, ())
    try:
        closure = validate_evidence_closure(case_id)
    except (OSError, ValueError) as exc:
        layer, reason = _reason_from_error(exc)
        _set_failure(results, layer, reason)
        return _report(case_id, source_replay_state, profile, results, ClosureResult(0, 0, LayerState.FAILED), empty_reproduction, empty_equivalence, empty_drift, None, (reason,))
    try:
        reproduced: ReplayEquivalenceValidation = reproduce_and_compare_p7_4(case_id)
    except (OSError, ValueError) as exc:
        reason = ValidationReason.REPLAY_REPRODUCTION_MISMATCH
        _set_failure(results, ValidationLayer.REPLAY_REPRODUCTION, reason)
        return _report(case_id, source_replay_state, profile, results, closure.closure_result, empty_reproduction, empty_equivalence, empty_drift, closure, (reason,))
    reasons: list[ValidationReason] = []
    if not reproduced.reproduction_result.canonical_bytes_matched:
        reason = ValidationReason.REPLAY_REPRODUCTION_MISMATCH
        _set_failure(results, ValidationLayer.REPLAY_REPRODUCTION, reason)
        reasons.append(reason)
    if not reproduced.equivalence_result.equivalent:
        reason = ValidationReason.RESULT_EQUIVALENCE_MISMATCH
        _set_failure(results, ValidationLayer.RESULT_EQUIVALENCE, reason)
        reasons.append(reason)
    replay_drift = analyze_replay_fact_drift(reproduced.frozen_report, reproduced.reproduced_report, profile)
    if replay_drift.replay_fact_drift_count:
        reason = ValidationReason.REPLAY_FACT_DRIFT
        _set_failure(results, ValidationLayer.REPLAY_FACT_DRIFT, reason)
        reasons.append(reason)
    return _report(case_id, source_replay_state, profile, results, closure.closure_result, reproduced.reproduction_result, reproduced.equivalence_result, replay_drift, closure, tuple(reasons))

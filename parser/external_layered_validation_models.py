"""Closed, deterministic P7.5 layered-validation records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Mapping


VALIDATION_PROFILE_VERSION = "p7.5-layered-validation-v1"
LAYERED_VALIDATION_REPORT_VERSION = "external-layered-validation-report-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ValidationState(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    REFERENCE_ONLY = "reference_only"
    VALIDATION_PASS = "validation_pass"
    VALIDATION_FAIL = "validation_fail"


class LayerState(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    NOT_EVALUATED = "not_evaluated"


class ValidationLayer(str, Enum):
    SOURCE_IDENTITY = "source_identity"
    ARTIFACT_IDENTITY = "artifact_identity"
    CHECKSUM_INTEGRITY = "checksum_integrity"
    PACKAGE_COMPLETENESS = "package_completeness"
    EVIDENCE_CLOSURE = "evidence_closure"
    P7_4_REPORT_SCHEMA = "p7_4_report_schema"
    CROSS_PHASE_IDENTITY_BINDING = "cross_phase_identity_binding"
    REPLAY_REPRODUCTION = "replay_reproduction"
    RESULT_EQUIVALENCE = "result_equivalence"
    REPLAY_FACT_DRIFT = "replay_fact_drift"
    PROOF_FACT_COMPARABILITY = "proof_fact_comparability"
    PROOF_PARITY_ELIGIBILITY = "proof_parity_eligibility"


class ValidationReason(str, Enum):
    VALIDATION_PROFILE_UNSUPPORTED = "VALIDATION_PROFILE_UNSUPPORTED"
    SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
    ARTIFACT_IDENTITY_MISMATCH = "ARTIFACT_IDENTITY_MISMATCH"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    PACKAGE_INCOMPLETE = "PACKAGE_INCOMPLETE"
    EVIDENCE_CLOSURE_INCOMPLETE = "EVIDENCE_CLOSURE_INCOMPLETE"
    P7_4_REPORT_SCHEMA_INVALID = "P7_4_REPORT_SCHEMA_INVALID"
    BINDING_IDENTITY_MISMATCH = "BINDING_IDENTITY_MISMATCH"
    BINDING_FIXTURE_HASH_MISMATCH = "BINDING_FIXTURE_HASH_MISMATCH"
    FROZEN_INPUT_MUTATED = "FROZEN_INPUT_MUTATED"
    REPLAY_REPRODUCTION_MISMATCH = "REPLAY_REPRODUCTION_MISMATCH"
    RESULT_EQUIVALENCE_MISMATCH = "RESULT_EQUIVALENCE_MISMATCH"
    REPLAY_FACT_DRIFT = "REPLAY_FACT_DRIFT"
    SECURITY_BOUNDARY_VIOLATION = "SECURITY_BOUNDARY_VIOLATION"
    PROOF_PARITY_INELIGIBLE = "PROOF_PARITY_INELIGIBLE"


class ProofParity(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    PARITY_PASS = "parity_pass"
    PARITY_FAIL = "parity_fail"


class ProofCorrectness(str, Enum):
    NOT_EVALUATED = "not_evaluated"


LAYER_ORDER = tuple(ValidationLayer)
REASON_PRIORITY = tuple(ValidationReason)
_LAYER_RANK = {value: index for index, value in enumerate(LAYER_ORDER)}
_REASON_RANK = {value: index for index, value in enumerate(REASON_PRIORITY)}


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256_identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def ordered_layers(values: tuple[ValidationLayer, ...] | list[ValidationLayer]) -> tuple[ValidationLayer, ...]:
    return tuple(sorted(set(values), key=_LAYER_RANK.__getitem__))


def ordered_reasons(values: tuple[ValidationReason, ...] | list[ValidationReason]) -> tuple[ValidationReason, ...]:
    return tuple(sorted(set(values), key=_REASON_RANK.__getitem__))


def _sha256(value: object, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _count(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"{name} must be a stable identifier")
    return value


def _fact(value: object, name: str) -> str | int | bool | None:
    if not isinstance(value, (str, int, bool, type(None))) or isinstance(value, float):
        raise ValueError(f"{name} is not a deterministic scalar")
    return value


@dataclass(frozen=True)
class ValidationProfile:
    profile_id: str
    profile_version: str
    profile_identity: str
    applicable_layers: tuple[ValidationLayer, ...]
    replay_fact_fields: tuple[str, ...]
    proof_parity_profile_id: None
    hardware_validation: bool = False

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        if self.profile_version != VALIDATION_PROFILE_VERSION or self.proof_parity_profile_id is not None or self.hardware_validation is not False:
            raise ValueError("profile constants are invalid")
        _sha256(self.profile_identity, "profile_identity")
        if self.applicable_layers != ordered_layers(self.applicable_layers):
            raise ValueError("profile layers are not ordered")
        if not self.replay_fact_fields or tuple(sorted(set(self.replay_fact_fields))) != self.replay_fact_fields or not all(isinstance(item, str) and item and "/" not in item and "\\" not in item for item in self.replay_fact_fields):
            raise ValueError("profile replay fact fields are invalid")
        if self.profile_identity != sha256_identity(self.identity_input()):
            raise ValueError("profile identity does not match canonical input")

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "applicable_layers": [item.value for item in self.applicable_layers], "replay_fact_fields": list(self.replay_fact_fields)}

    def identity_input(self) -> dict[str, object]:
        value = self.to_dict()
        del value["profile_identity"]
        return value

    @classmethod
    def from_dict(cls, value: object) -> "ValidationProfile":
        fields = frozenset(cls.__dataclass_fields__)
        if not isinstance(value, Mapping) or set(value) != fields or not isinstance(value.get("applicable_layers"), list) or not isinstance(value.get("replay_fact_fields"), list):
            raise ValueError("profile fields are invalid")
        row = dict(value)
        row["applicable_layers"] = tuple(ValidationLayer(item) for item in row["applicable_layers"])
        row["replay_fact_fields"] = tuple(row["replay_fact_fields"])
        return cls(**row)  # type: ignore[arg-type]


@dataclass(frozen=True)
class IdentityBinding:
    binding_kind: str
    expected_identity: str
    actual_identity: str
    matched: bool

    def __post_init__(self) -> None:
        _text(self.binding_kind, "binding_kind")
        _sha256(self.expected_identity, "expected_identity")
        _sha256(self.actual_identity, "actual_identity")
        if not isinstance(self.matched, bool) or self.matched != (self.expected_identity == self.actual_identity):
            raise ValueError("identity binding match is invalid")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "IdentityBinding":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("identity binding fields are invalid")
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ClosureResult:
    required_artifact_count: int
    present_artifact_count: int
    closure_state: LayerState

    def __post_init__(self) -> None:
        _count(self.required_artifact_count, "required_artifact_count")
        _count(self.present_artifact_count, "present_artifact_count")
        if self.present_artifact_count > self.required_artifact_count or self.closure_state not in {LayerState.PASSED, LayerState.FAILED, LayerState.NOT_APPLICABLE}:
            raise ValueError("closure result is invalid")
        if self.closure_state is LayerState.PASSED and self.present_artifact_count != self.required_artifact_count:
            raise ValueError("passed closure is incomplete")

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "closure_state": self.closure_state.value}

    @classmethod
    def from_dict(cls, value: object) -> "ClosureResult":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("closure result fields are invalid")
        row = dict(value); row["closure_state"] = LayerState(row["closure_state"])
        return cls(**row)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ReproductionResult:
    reproduction_attempted: bool
    canonical_bytes_matched: bool
    canonical_sha256_matched: bool
    reproduced_report_sha256: str | None

    def __post_init__(self) -> None:
        if not all(isinstance(value, bool) for value in (self.reproduction_attempted, self.canonical_bytes_matched, self.canonical_sha256_matched)):
            raise ValueError("reproduction booleans are invalid")
        _sha256(self.reproduced_report_sha256, "reproduced_report_sha256", nullable=True)
        if not self.reproduction_attempted and (self.canonical_bytes_matched or self.canonical_sha256_matched or self.reproduced_report_sha256 is not None):
            raise ValueError("unattempted reproduction has results")
        if self.canonical_bytes_matched != self.canonical_sha256_matched:
            raise ValueError("canonical reproduction results disagree")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "ReproductionResult":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("reproduction fields are invalid")
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class EquivalenceResult:
    equivalent: bool
    compared_field_count: int
    mismatch_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.equivalent, bool):
            raise ValueError("equivalent must be boolean")
        _count(self.compared_field_count, "compared_field_count")
        if not all(isinstance(item, str) and item for item in self.mismatch_fields) or tuple(sorted(set(self.mismatch_fields))) != self.mismatch_fields or self.equivalent != (not self.mismatch_fields):
            raise ValueError("equivalence fields are invalid")

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "mismatch_fields": list(self.mismatch_fields)}

    @classmethod
    def from_dict(cls, value: object) -> "EquivalenceResult":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__) or not isinstance(value.get("mismatch_fields"), list):
            raise ValueError("equivalence fields are invalid")
        row = dict(value); row["mismatch_fields"] = tuple(row["mismatch_fields"])
        return cls(**row)  # type: ignore[arg-type]


@dataclass(frozen=True)
class FactDrift:
    fact_id: str
    expected_value: str | int | bool | None
    actual_value: str | int | bool | None

    def __post_init__(self) -> None:
        _text(self.fact_id, "fact_id")
        _fact(self.expected_value, "expected_value")
        _fact(self.actual_value, "actual_value")
        if self.expected_value == self.actual_value:
            raise ValueError("drift item has no drift")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "FactDrift":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("drift item fields are invalid")
        return cls(**value)  # type: ignore[arg-type]


def _drift_order_key(value: FactDrift) -> tuple[object, ...]:
    return value.fact_id, canonical_json(value.expected_value), canonical_json(value.actual_value)


@dataclass(frozen=True)
class ReplayFactDrift:
    comparable_replay_fact_count: int
    replay_fact_drift_count: int
    replay_fact_drift_items: tuple[FactDrift, ...]

    def __post_init__(self) -> None:
        _count(self.comparable_replay_fact_count, "comparable_replay_fact_count")
        _count(self.replay_fact_drift_count, "replay_fact_drift_count")
        if self.replay_fact_drift_count != len(self.replay_fact_drift_items) or self.replay_fact_drift_count > self.comparable_replay_fact_count or not all(isinstance(item, FactDrift) for item in self.replay_fact_drift_items) or tuple(sorted(self.replay_fact_drift_items, key=_drift_order_key)) != self.replay_fact_drift_items:
            raise ValueError("replay drift is invalid")

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "replay_fact_drift_items": [item.to_dict() for item in self.replay_fact_drift_items]}

    @classmethod
    def from_dict(cls, value: object) -> "ReplayFactDrift":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__) or not isinstance(value.get("replay_fact_drift_items"), list):
            raise ValueError("replay drift fields are invalid")
        row = dict(value); row["replay_fact_drift_items"] = tuple(FactDrift.from_dict(item) for item in row["replay_fact_drift_items"])
        return cls(**row)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ProofFactDrift:
    comparable_proof_fact_count: int
    proof_drift_count: int
    proof_drift_items: tuple[FactDrift, ...]

    def __post_init__(self) -> None:
        _count(self.comparable_proof_fact_count, "comparable_proof_fact_count")
        _count(self.proof_drift_count, "proof_drift_count")
        if self.proof_drift_count != len(self.proof_drift_items) or self.proof_drift_count > self.comparable_proof_fact_count or not all(isinstance(item, FactDrift) for item in self.proof_drift_items) or tuple(sorted(self.proof_drift_items, key=_drift_order_key)) != self.proof_drift_items:
            raise ValueError("proof drift is invalid")

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "proof_drift_items": [item.to_dict() for item in self.proof_drift_items]}

    @classmethod
    def from_dict(cls, value: object) -> "ProofFactDrift":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__) or not isinstance(value.get("proof_drift_items"), list):
            raise ValueError("proof drift fields are invalid")
        row = dict(value); row["proof_drift_items"] = tuple(FactDrift.from_dict(item) for item in row["proof_drift_items"])
        return cls(**row)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ProofParityEligibility:
    proof_parity_eligible: bool
    proof_parity: ProofParity
    proof_parity_reason: str
    proof_correctness: ProofCorrectness = ProofCorrectness.NOT_EVALUATED

    def __post_init__(self) -> None:
        _text(self.proof_parity_reason, "proof_parity_reason")
        if not isinstance(self.proof_parity_eligible, bool) or self.proof_correctness is not ProofCorrectness.NOT_EVALUATED or not isinstance(self.proof_parity, ProofParity):
            raise ValueError("proof parity state is invalid")
        if not self.proof_parity_eligible and self.proof_parity is not ProofParity.NOT_EVALUATED:
            raise ValueError("ineligible proof parity was evaluated")

    def to_dict(self) -> dict[str, object]:
        return {"proof_parity_eligible": self.proof_parity_eligible, "proof_parity": self.proof_parity.value, "proof_parity_reason": self.proof_parity_reason, "proof_correctness": self.proof_correctness.value}

    @classmethod
    def from_dict(cls, value: object) -> "ProofParityEligibility":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("proof parity fields are invalid")
        row = dict(value); row["proof_parity"] = ProofParity(row["proof_parity"]); row["proof_correctness"] = ProofCorrectness(row["proof_correctness"])
        return cls(**row)  # type: ignore[arg-type]


@dataclass(frozen=True)
class LayerResult:
    layer: ValidationLayer
    layer_state: LayerState
    reason_codes: tuple[ValidationReason, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.layer, ValidationLayer) or not isinstance(self.layer_state, LayerState) or self.reason_codes != ordered_reasons(self.reason_codes):
            raise ValueError("layer result is invalid")
        if self.layer_state is LayerState.FAILED and not self.reason_codes or self.layer_state is not LayerState.FAILED and self.reason_codes:
            raise ValueError("layer reasons are invalid")

    def to_dict(self) -> dict[str, object]:
        return {"layer": self.layer.value, "layer_state": self.layer_state.value, "reason_codes": [item.value for item in self.reason_codes]}

    @classmethod
    def from_dict(cls, value: object) -> "LayerResult":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__) or not isinstance(value.get("reason_codes"), list):
            raise ValueError("layer result fields are invalid")
        return cls(ValidationLayer(value["layer"]), LayerState(value["layer_state"]), tuple(ValidationReason(item) for item in value["reason_codes"]))


@dataclass(frozen=True)
class LayeredValidationReport:
    validation_state: ValidationState
    source_case_id: str
    source_replay_state: str
    validation_profile_id: str
    validation_profile_identity: str
    applicable_layers: tuple[ValidationLayer, ...]
    layer_results: tuple[LayerResult, ...]
    identity_bindings: tuple[IdentityBinding, ...]
    closure_result: ClosureResult
    reproduction_result: ReproductionResult
    equivalence_result: EquivalenceResult
    replay_fact_drift: ReplayFactDrift
    proof_fact_drift: ProofFactDrift
    proof_parity_eligibility: ProofParityEligibility
    primary_reason: ValidationReason | None
    reason_codes: tuple[ValidationReason, ...]
    hardware_validation: bool = False

    def __post_init__(self) -> None:
        _text(self.source_case_id, "source_case_id")
        _text(self.source_replay_state, "source_replay_state")
        _text(self.validation_profile_id, "validation_profile_id")
        _sha256(self.validation_profile_identity, "validation_profile_identity")
        if not isinstance(self.validation_state, ValidationState) or self.hardware_validation is not False or self.applicable_layers != ordered_layers(self.applicable_layers) or tuple(result.layer for result in self.layer_results) != self.applicable_layers or not all(isinstance(result, LayerResult) for result in self.layer_results):
            raise ValueError("validation report layers are invalid")
        if tuple(item.binding_kind for item in self.identity_bindings) != tuple(sorted(item.binding_kind for item in self.identity_bindings)) or len({item.binding_kind for item in self.identity_bindings}) != len(self.identity_bindings) or not all(isinstance(item, IdentityBinding) for item in self.identity_bindings):
            raise ValueError("validation report bindings are invalid")
        if not all(isinstance(item, value) for item, value in ((self.closure_result, ClosureResult), (self.reproduction_result, ReproductionResult), (self.equivalence_result, EquivalenceResult), (self.replay_fact_drift, ReplayFactDrift), (self.proof_fact_drift, ProofFactDrift), (self.proof_parity_eligibility, ProofParityEligibility))):
            raise ValueError("validation report facts are invalid")
        if self.reason_codes != ordered_reasons(self.reason_codes) or self.primary_reason != (self.reason_codes[0] if self.reason_codes else None):
            raise ValueError("validation report reasons are invalid")
        failures = any(result.layer_state is LayerState.FAILED for result in self.layer_results)
        if self.validation_state is ValidationState.VALIDATION_PASS and (failures or self.reason_codes or self.source_replay_state == "reference_only"):
            raise ValueError("validation pass is invalid")
        if self.validation_state is ValidationState.VALIDATION_FAIL and (not failures or not self.reason_codes):
            raise ValueError("validation fail is invalid")
        if self.validation_state is ValidationState.REFERENCE_ONLY and (self.source_replay_state != "reference_only" or failures or self.reason_codes):
            raise ValueError("reference-only validation is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "report_version": LAYERED_VALIDATION_REPORT_VERSION,
            "validation_state": self.validation_state.value,
            "source_case_id": self.source_case_id,
            "source_replay_state": self.source_replay_state,
            "validation_profile_id": self.validation_profile_id,
            "validation_profile_identity": self.validation_profile_identity,
            "applicable_layers": [item.value for item in self.applicable_layers],
            "layer_results": [item.to_dict() for item in self.layer_results],
            "identity_bindings": [item.to_dict() for item in self.identity_bindings],
            "closure_result": self.closure_result.to_dict(),
            "reproduction_result": self.reproduction_result.to_dict(),
            "equivalence_result": self.equivalence_result.to_dict(),
            "replay_fact_drift": self.replay_fact_drift.to_dict(),
            "proof_fact_drift": self.proof_fact_drift.to_dict(),
            "proof_parity_eligibility": self.proof_parity_eligibility.to_dict(),
            "primary_reason": None if self.primary_reason is None else self.primary_reason.value,
            "reason_codes": [item.value for item in self.reason_codes],
            "hardware_validation": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "LayeredValidationReport":
        fields = {"report_version", *cls.__dataclass_fields__}
        if not isinstance(value, Mapping) or set(value) != fields or value.get("report_version") != LAYERED_VALIDATION_REPORT_VERSION or not all(isinstance(value.get(name), list) for name in ("applicable_layers", "layer_results", "identity_bindings", "reason_codes")):
            raise ValueError("validation report fields are invalid")
        row = dict(value); del row["report_version"]
        row["validation_state"] = ValidationState(row["validation_state"])
        row["applicable_layers"] = tuple(ValidationLayer(item) for item in row["applicable_layers"])
        row["layer_results"] = tuple(LayerResult.from_dict(item) for item in row["layer_results"])
        row["identity_bindings"] = tuple(IdentityBinding.from_dict(item) for item in row["identity_bindings"])
        row["closure_result"] = ClosureResult.from_dict(row["closure_result"])
        row["reproduction_result"] = ReproductionResult.from_dict(row["reproduction_result"])
        row["equivalence_result"] = EquivalenceResult.from_dict(row["equivalence_result"])
        row["replay_fact_drift"] = ReplayFactDrift.from_dict(row["replay_fact_drift"])
        row["proof_fact_drift"] = ProofFactDrift.from_dict(row["proof_fact_drift"])
        row["proof_parity_eligibility"] = ProofParityEligibility.from_dict(row["proof_parity_eligibility"])
        row["primary_reason"] = None if row["primary_reason"] is None else ValidationReason(row["primary_reason"])
        row["reason_codes"] = tuple(ValidationReason(item) for item in row["reason_codes"])
        return cls(**row)  # type: ignore[arg-type]

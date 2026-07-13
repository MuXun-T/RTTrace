"""Read-only P7.2-to-P7.4 closure validation for P7.5."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from parser.external_case_evidence_binding import build_case_binding, binding_bytes
from parser.external_case_evidence_binding_models import ExternalCaseEvidenceBinding, sha256_identity
from parser.external_case_package_evidence import case_package_specs
from parser.external_layered_validation_models import ClosureResult, IdentityBinding, LayerState
from parser.external_replay_comparator import expected_identity
from parser.external_validation_intake import ACQUIRED_CASES, INVENTORY_PATH, OPENED_REPORT_PATH, PROFILE_PATH, REFERENCE_ONLY_CASES, ValidationIntake, intake_case
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ACQUIRED_BINDING_FIXTURE_SHA256 = {
    "freertos_btf_1core": "1d9942dffbb8dde1cb89a65370095904b7c5929b187144a591d5561d247ede73",
    "freertos_vcd_1core": "85fc1795b2bed8a3e739cffb181e58fea557d2776fd1198587f8f7c0916b35e7",
    "freertos_btf_4cores": "f1efcca8c9b86fd373771bb20154767481ffd5112549a399f232af7d183161ea",
    "freertos_btf_50k": "bdf840f27f64d7a226b9141d67c9dcf74f052630482baab7209d7b06913b89b4",
}


@dataclass(frozen=True)
class EvidenceClosureValidation:
    intake: ValidationIntake
    closure_result: ClosureResult
    identity_bindings: tuple[IdentityBinding, ...]


def _object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _binding(expected: str, actual: str, kind: str) -> IdentityBinding:
    return IdentityBinding(kind, expected, actual, expected == actual)


def _schema(raw: bytes, schema_name: str, label: str) -> dict[str, object]:
    value = _object(raw, label)
    if validate_schema(load_schema(schema_name), value) is not None:
        raise ValueError(f"{label} schema is invalid")
    return value


def _case_spec(case_id: str):
    return next((spec for spec in case_package_specs() if spec.case_id == case_id), None)


def _validate_acquired(intake: ValidationIntake) -> tuple[IdentityBinding, ...]:
    case_id = intake.case_id
    spec = _case_spec(case_id)
    if spec is None:
        raise ValueError("acquired case package specification is missing")
    binding_path = f"tests/python/fixtures/external_validation/evidence_bindings/{case_id}.json"
    binding_raw = intake.raw(binding_path)
    fixture_sha256 = _sha256(binding_raw)
    if fixture_sha256 != ACQUIRED_BINDING_FIXTURE_SHA256[case_id]:
        raise ValueError("binding fixture SHA-256 does not match frozen input")
    binding = ExternalCaseEvidenceBinding.from_dict(_object(binding_raw, "case binding"))
    if binding.case_id != case_id or binding.binding_identity != sha256_identity(binding.identity_input()):
        raise ValueError("embedded binding identity is invalid")
    expected_binding = build_case_binding(spec)
    if binding != expected_binding or binding_bytes(binding) != binding_raw:
        raise ValueError("binding does not reproduce frozen cross-phase evidence")

    manifest = _schema(intake.raw(binding.p7_3_manifest_logical_path), "external_evidence_package.schema.json", "P7.3 manifest")
    p7_3_report = _schema(intake.raw(binding.p7_3_report_logical_path), "external_package_open_report.schema.json", "P7.3 report")
    p7_4_report = _schema(intake.raw(binding.p7_4_replay_report_logical_path), "external_replay_report.schema.json", "P7.4 report")
    if _sha256(intake.raw(binding.p7_3_manifest_logical_path)) != binding.p7_3_manifest_sha256 or _sha256(intake.raw(binding.p7_3_report_logical_path)) != binding.p7_3_report_sha256 or _sha256(intake.raw(binding.p7_4_replay_report_logical_path)) != binding.p7_4_replay_report_sha256:
        raise ValueError("frozen report or manifest hash does not bind")
    if p7_3_report.get("package_identity") != binding.p7_3_package_identity or p7_4_report.get("trace_identity") != binding.p7_4_trace_identity or p7_4_report.get("package_identity") != binding.p7_4_reported_package_identity:
        raise ValueError("cross-phase report identity does not bind")
    if binding.p7_2_raw_trace_sha256 != binding.p7_4_trace_identity or _sha256(intake.raw(binding.p7_2_raw_trace_logical_path)) != binding.p7_2_raw_trace_sha256:
        raise ValueError("raw trace identity does not bind")
    if int(p7_3_report.get("source_mutation_count", -1)) != 0 or int(p7_3_report.get("package_mutation_count", -1)) != 0 or int(p7_4_report.get("source_mutation_count", -1)) != 0 or int(p7_4_report.get("package_mutation_count", -1)) != 0 or int(p7_4_report.get("raw_trace_mutation_count", -1)) != 0:
        raise ValueError("frozen report records mutation")
    if manifest.get("package_identity") != binding.p7_3_package_identity:
        raise ValueError("P7.3 manifest package identity does not bind")

    profile = _object(intake.raw(PROFILE_PATH), "P7.4 comparison profile")
    expected_path = f"tests/python/fixtures/external_validation/replay/expected/{case_id}.expected.json"
    expected = _object(intake.raw(expected_path), "P7.4 expected fixture")
    profile_unsigned = dict(profile); profile_identity = profile_unsigned.pop("comparison_profile_identity", None)
    expected_unsigned = dict(expected); expected_digest = expected_unsigned.pop("expected_identity", None)
    if profile_identity != expected_identity(profile_unsigned) or expected_digest != expected_identity(expected_unsigned):
        raise ValueError("P7.4 expected or profile identity is invalid")
    if p7_4_report.get("comparison_profile_identity") != profile_identity or p7_4_report.get("expected_identity") != expected_digest:
        raise ValueError("P7.4 expected or profile identity does not bind")

    return tuple(sorted((
        _binding(ACQUIRED_BINDING_FIXTURE_SHA256[case_id], fixture_sha256, "binding_fixture_sha256"),
        _binding(expected_binding.binding_identity, binding.binding_identity, "embedded_binding_identity"),
        _binding(binding.p7_2_raw_trace_sha256, binding.p7_4_trace_identity, "p7_2_to_p7_4_trace_identity"),
        _binding(binding.p7_3_package_identity, str(p7_3_report["package_identity"]), "p7_3_package_identity"),
        _binding(str(expected_digest), str(p7_4_report["expected_identity"]), "p7_4_expected_identity"),
        _binding(str(profile_identity), str(p7_4_report["comparison_profile_identity"]), "p7_4_profile_identity"),
    ), key=lambda item: item.binding_kind))


def _validate_reference_only(intake: ValidationIntake) -> tuple[IdentityBinding, ...]:
    source_id, expected_reason = REFERENCE_ONLY_CASES[intake.case_id]
    inventory = _object(intake.raw(INVENTORY_PATH), "P7.2 inventory")
    sources = inventory.get("sources")
    if not isinstance(sources, list):
        raise ValueError("P7.2 inventory sources are invalid")
    source = next((item for item in sources if isinstance(item, Mapping) and item.get("source_id") == source_id), None)
    if source is None or source.get("hardware_validation") is not False or source.get("artifacts") != []:
        raise ValueError("reference-only source is invalid")
    report_path = f"tests/python/fixtures/external_validation/replay/reports/{intake.case_id}.json"
    report = _schema(intake.raw(report_path), "external_replay_report.schema.json", "P7.4 reference-only report")
    if report.get("replay_state") != "reference_only" or report.get("primary_reason") != expected_reason or report.get("replay_attempted") is not False or report.get("comparison_attempted") is not False or report.get("trace_identity") is not None or report.get("expected_identity") is not None or report.get("actual_identity") is not None:
        raise ValueError("reference-only replay report is invalid")
    source_identity = sha256_identity(source)
    if report.get("source_identity") != source_identity:
        raise ValueError("reference-only source identity does not bind")
    return (_binding(source_identity, str(report["source_identity"]), "p7_2_to_p7_4_source_identity"),)


def validate_evidence_closure(case_id: str) -> EvidenceClosureValidation:
    intake = intake_case(case_id)
    try:
        bindings = _validate_acquired(intake) if intake.acquired else _validate_reference_only(intake)
        intake.assert_unchanged()
    except Exception:
        intake.assert_unchanged()
        raise
    return EvidenceClosureValidation(intake, ClosureResult(len(intake.logical_paths), len(intake.logical_paths), LayerState.PASSED), bindings)

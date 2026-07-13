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
LEGACY_OPENED_REPORT_SHA256 = "dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1"
P7_2_PROVENANCE_INPUT_SHA256 = {
    INVENTORY_PATH: "ad15a481e2dde8eea0ef2b6e3feecc083e30699532296f27d1095cacaedde54b",
    "docs/phase7_external_trace_sources/README.md": "a6ad9f87a3bca67be510fce631e6bb65e45b2208a6dff644fa10454af6c043b4",
    "docs/phase7_external_trace_sources/acquisition_report.md": "1222127611b495c75242dabcacc77d93de1fd5192606cb0185459297179074eb",
    "docs/phase7_external_trace_sources/claim_boundary.md": "8b49521b29666e92fc4f801f36f122bbd5c13259711ac6193c31553dbb50d789",
    "tests/python/fixtures/external_validation/sources/freertos_btf_trace/SOURCE.md": "72b27b565fd8e063c29f326cbc455d3dc2aeded86b36c485b42454f05fe97407",
    "tests/python/fixtures/external_validation/sources/freertos_btf_trace/LICENSE": "74882dbadec20a9cb8d593a9e14fef9064cb0726ed563ee9469b86ee17f6ba64",
    "tests/python/fixtures/external_validation/sources/freertos_btf_trace/checksums.sha256": "524455d1c3774e835ba760aef7bfa94e89017f240ec5bfabfbf2bdfbd474699f",
    "tests/python/fixtures/external_validation/sources/zephyr_pipeline/SOURCE.md": "5106f7f9d14deed783c8fb03355affeedb3b425b4b93298081be18f1cc16d811",
    "tests/python/fixtures/external_validation/sources/zephyr_pipeline/LICENSE": "c6596eb7be8581c18be736c846fb9173b69eccf6ef94c5135893ec56bd92ba08",
    "tests/python/fixtures/external_validation/sources/zephyr_pipeline/checksums.sha256": "972d4b2fc08af0b3921a6a578be449b069a269224f1c45fdae19efc3cfa52a97",
    "tests/python/fixtures/external_validation/sources/zephelin_optional/SOURCE.md": "10c8761df54d053eecd2ac3b61b6d1341d5e4042265392e5811274df9c98c5ec",
    "tests/python/fixtures/external_validation/sources/zephelin_optional/LICENSE": "b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1",
    "tests/python/fixtures/external_validation/sources/zephelin_optional/checksums.sha256": "c59082afb3eb9bd63b3e30b3edd759648a4d00bfb5a692f3d253baf75a006ba1",
}
SOURCE_FACTS = {
    "freertos_btf_trace": ("https://github.com/kuopinghsu/FreeRTOS-BTF-Trace", "791410f5ebb05a9fdf77401228140c60275b5d27", "acquired", "MIT"),
    "zephyr_pipeline": ("https://github.com/zephyrproject-rtos/zephyr", "b01be6b7b16eb12a8cd0275752d575aca489c430", "acquisition_blocked", "Apache-2.0"),
    "zephelin_optional": ("https://github.com/antmicro/zephelin", "ca37e2efea312f39a8670078daa1a14a2361e358", "external_reference_only", "Apache-2.0"),
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


def _validate_p7_2_provenance(intake: ValidationIntake, source_id: str, raw_names: tuple[str, ...]) -> Mapping[str, object]:
    for path in intake.logical_paths:
        expected = P7_2_PROVENANCE_INPUT_SHA256.get(path)
        if expected is not None and _sha256(intake.raw(path)) != expected:
            raise ValueError("P7.2 provenance, license, or checksum input changed")
    inventory = _object(intake.raw(INVENTORY_PATH), "P7.2 inventory")
    sources = inventory.get("sources")
    if not isinstance(sources, list):
        raise ValueError("P7.2 inventory sources are invalid")
    source = next((item for item in sources if isinstance(item, Mapping) and item.get("source_id") == source_id), None)
    if source is None or source.get("hardware_validation") is not False or tuple(source.get(name) for name in ("repository", "repository_commit", "acquisition_status", "license_spdx")) != SOURCE_FACTS[source_id]:
        raise ValueError("P7.2 source provenance facts are invalid")
    source_root = f"tests/python/fixtures/external_validation/sources/{source_id}"
    source_text = intake.raw(f"{source_root}/SOURCE.md").decode("utf-8")
    repository, commit, _, _ = SOURCE_FACTS[source_id]
    if repository not in source_text or commit not in source_text:
        raise ValueError("P7.2 source document does not bind repository and commit")
    checksum_entries: dict[str, str] = {}
    for line in intake.raw(f"{source_root}/checksums.sha256").decode("utf-8").splitlines():
        digest, separator, logical_name = line.partition("  ")
        if not separator or len(digest) != 64 or logical_name in checksum_entries:
            raise ValueError("P7.2 checksum file is invalid")
        checksum_entries[logical_name] = digest
    expected_names = {"LICENSE", *(f"raw/{name}" for name in raw_names)}
    if set(checksum_entries) != expected_names:
        raise ValueError("P7.2 checksum file does not close the required artifacts")
    for logical_name, digest in checksum_entries.items():
        actual_path = f"{source_root}/{logical_name}"
        if _sha256(intake.raw(actual_path)) != digest:
            raise ValueError("P7.2 checksum does not match frozen bytes")
    if source.get("license_sha256") != checksum_entries["LICENSE"]:
        raise ValueError("P7.2 inventory license identity does not bind")
    return source


def _case_spec(case_id: str):
    return next((spec for spec in case_package_specs() if spec.case_id == case_id), None)


def _validate_acquired(intake: ValidationIntake) -> tuple[IdentityBinding, ...]:
    case_id = intake.case_id
    spec = _case_spec(case_id)
    if spec is None:
        raise ValueError("acquired case package specification is missing")
    source = _validate_p7_2_provenance(intake, "freertos_btf_trace", ("example.btf", "example.vcd", "example-4cores.btf", "example-50k.btf"))
    if source.get("artifacts") is None:
        raise ValueError("P7.2 acquired artifacts are invalid")
    legacy_opened_raw = intake.raw(OPENED_REPORT_PATH)
    if _sha256(legacy_opened_raw) != LEGACY_OPENED_REPORT_SHA256:
        raise ValueError("legacy P7.3 opened report SHA-256 does not match frozen input")
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
    if manifest.get("package_identity") != binding.p7_3_package_identity or not isinstance(manifest.get("source"), Mapping) or not isinstance(manifest.get("artifacts"), list) or len(manifest["artifacts"]) != 1 or not isinstance(manifest["artifacts"][0], Mapping):
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
        _binding(binding.p7_2_inventory_identity, _sha256(intake.raw(INVENTORY_PATH)), "p7_2_inventory_sha256"),
        _binding(binding.p7_2_raw_trace_sha256, binding.p7_4_trace_identity, "p7_2_to_p7_4_trace_identity"),
        _binding(binding.p7_3_manifest_sha256, _sha256(intake.raw(binding.p7_3_manifest_logical_path)), "p7_3_manifest_sha256"),
        _binding(binding.p7_3_source_identity, str(manifest["source"]["source_identity"]), "p7_3_source_identity"),
        _binding(binding.p7_3_artifact_identity, str(manifest["artifacts"][0]["artifact_identity"]), "p7_3_artifact_identity"),
        _binding(binding.p7_3_package_identity, str(p7_3_report["package_identity"]), "p7_3_package_identity"),
        _binding(binding.p7_3_report_sha256, _sha256(intake.raw(binding.p7_3_report_logical_path)), "p7_3_report_sha256"),
        _binding(LEGACY_OPENED_REPORT_SHA256, _sha256(legacy_opened_raw), "p7_3_legacy_opened_report_sha256"),
        _binding(binding.p7_4_replay_report_sha256, _sha256(intake.raw(binding.p7_4_replay_report_logical_path)), "p7_4_replay_report_sha256"),
        _binding(str(expected_digest), str(p7_4_report["expected_identity"]), "p7_4_expected_identity"),
        _binding(str(profile_identity), str(p7_4_report["comparison_profile_identity"]), "p7_4_profile_identity"),
    ), key=lambda item: item.binding_kind))


def _validate_reference_only(intake: ValidationIntake) -> tuple[IdentityBinding, ...]:
    source_id, expected_reason = REFERENCE_ONLY_CASES[intake.case_id]
    source = _validate_p7_2_provenance(intake, source_id, ())
    if source.get("artifacts") != []:
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

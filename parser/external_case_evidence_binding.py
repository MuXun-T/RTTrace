"""Build and verify per-case P7.2, P7.3, and P7.4 evidence bindings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from parser.external_case_evidence_binding_models import BINDING_VERSION, ExternalCaseEvidenceBinding, canonical_json, sha256_identity
from parser.external_case_package_evidence import CasePackageSpec, case_package_specs, read_regular_no_follow, reproduce_case_report
from parser.external_package_models import ExternalEvidencePackageManifest, sha256_identity as package_sha256_identity


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = "docs/phase7_external_trace_sources/source_inventory.json"
LEGACY_OPEN_REPORT_PATH = "tests/python/fixtures/external_validation/packages/reports/opened.json"
P7_3_REPORT_ROOT = "tests/python/fixtures/external_validation/packages/reports/per_case"
P7_4_REPORT_PATHS = {
    "freertos_btf_1core": "tests/python/fixtures/external_validation/replay/reports/freertos_btf_1core.json",
    "freertos_vcd_1core": "tests/python/fixtures/external_validation/replay/reports/freertos_vcd_1core.json",
    "freertos_btf_4cores": "tests/python/fixtures/external_validation/replay/reports/freertos_btf_4cores.json",
    "freertos_btf_50k": "tests/python/fixtures/external_validation/replay/reports/freertos_btf_50k.json",
}


def _json_object(raw: bytes, name: str) -> dict[str, object]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _manifest(spec: CasePackageSpec) -> tuple[bytes, ExternalEvidencePackageManifest]:
    raw = read_regular_no_follow(spec.manifest_relative_path)
    manifest = ExternalEvidencePackageManifest.from_dict(_json_object(raw, "P7.3 manifest"))
    if len(manifest.artifacts) != 1:
        raise ValueError("P7.3 per-case manifest must declare one artifact")
    artifact = manifest.artifacts[0]
    if artifact.artifact_id != spec.artifact_id or artifact.source_artifact_id != spec.artifact_id or artifact.relative_path != f"artifact/{spec.raw_name}":
        raise ValueError("P7.3 manifest does not match the approved case")
    if manifest.source.source_artifact_id != spec.artifact_id or manifest.source.source_checksum != artifact.sha256 or manifest.source.source_bytes != artifact.bytes:
        raise ValueError("P7.3 source descriptor does not bind the artifact")
    source = manifest.source.to_dict()
    source_input = {"source_kind": source["source_kind"], "source_trace_id": source["source_artifact_id"], "source_trace_checksum": source["source_checksum"], "source_trace_bytes": source["source_bytes"], "source_format": source["trace_format"], "source_format_version": source["source_format_version"], "acquisition_status": source["acquisition_status"]}
    artifact_input = artifact.to_dict(); del artifact_input["artifact_identity"]
    if package_sha256_identity(source_input) != manifest.source.source_identity or package_sha256_identity(artifact_input) != artifact.artifact_identity or package_sha256_identity(manifest.identity_input()) != manifest.package_identity:
        raise ValueError("P7.3 manifest identities do not recompute")
    return raw, manifest


def _inventory(spec: CasePackageSpec) -> tuple[bytes, Mapping[str, object], Mapping[str, object]]:
    raw = read_regular_no_follow(INVENTORY_PATH)
    inventory = _json_object(raw, "P7.2 inventory")
    sources = inventory.get("sources")
    if not isinstance(sources, list):
        raise ValueError("P7.2 inventory sources are invalid")
    source = next((item for item in sources if isinstance(item, Mapping) and item.get("source_id") == "freertos_btf_trace"), None)
    if source is None or source.get("hardware_validation") is not False or source.get("acquisition_status") != "acquired":
        raise ValueError("P7.2 source is not the approved acquired source")
    artifacts = source.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("P7.2 source artifacts are invalid")
    artifact = next((item for item in artifacts if isinstance(item, Mapping) and item.get("artifact_id") == spec.artifact_id), None)
    if artifact is None:
        raise ValueError("P7.2 artifact is not approved for this case")
    return raw, source, artifact


def _p7_3_report(spec: CasePackageSpec, manifest: ExternalEvidencePackageManifest) -> tuple[str, bytes]:
    path = f"{P7_3_REPORT_ROOT}/{spec.case_id}.json"
    frozen = read_regular_no_follow(path)
    reproduced = reproduce_case_report(spec)
    if frozen != reproduced:
        raise ValueError("P7.3 canonical report does not reproduce")
    report = _json_object(frozen, "P7.3 report")
    expected = {"open_result": "opened", "package_identity": manifest.package_identity, "manifest_valid": True, "path_safety_valid": True, "package_complete": True, "required_artifacts_total": 1, "required_artifacts_present": 1, "artifact_checksum_pass_count": 1, "artifact_checksum_fail_count": 0, "artifact_size_pass_count": 1, "artifact_size_fail_count": 0, "source_mutation_count": 0, "package_mutation_count": 0, "hardware_validation": False, "replay_evaluated": False, "reason_codes": []}
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("P7.3 canonical report is not a valid zero-mutation open")
    return path, frozen


def _p7_4_report(spec: CasePackageSpec, raw_sha256: str, p7_2_source_identity: str) -> tuple[str, bytes, Mapping[str, object], str]:
    path = P7_4_REPORT_PATHS.get(spec.case_id)
    if path is None:
        raise ValueError("case has no approved P7.4 replay report")
    raw = read_regular_no_follow(path)
    report = _json_object(raw, "P7.4 replay report")
    legacy_raw = read_regular_no_follow(LEGACY_OPEN_REPORT_PATH)
    legacy = _json_object(legacy_raw, "legacy P7.3 report")
    legacy_identity = legacy.get("package_identity")
    if not isinstance(legacy_identity, str) or report.get("package_identity") != legacy_identity:
        raise ValueError("P7.4 reported package identity is not the frozen aggregate identity")
    if report.get("trace_identity") != raw_sha256 or report.get("hardware_validation") is not False:
        raise ValueError("P7.4 replay report does not bind the approved raw trace")
    if report.get("source_identity") != p7_2_source_identity:
        raise ValueError("P7.4 source identity does not match sealed P7.2 source")
    return path, raw, report, legacy_identity


def build_case_binding(spec: CasePackageSpec) -> ExternalCaseEvidenceBinding:
    inventory_raw, source, inventory_artifact = _inventory(spec)
    manifest_raw, manifest = _manifest(spec)
    artifact = manifest.artifacts[0]
    raw_trace = read_regular_no_follow(spec.raw_relative_path)
    raw_sha256 = _sha256(raw_trace)
    if raw_sha256 != inventory_artifact.get("sha256") or len(raw_trace) != inventory_artifact.get("bytes"):
        raise ValueError("P7.2 raw trace does not match inventory")
    if (artifact.sha256, artifact.bytes, manifest.source.source_checksum, manifest.source.source_bytes) != (raw_sha256, len(raw_trace), raw_sha256, len(raw_trace)):
        raise ValueError("P7.3 artifact/source do not match P7.2 raw trace")
    if manifest.source.source_id != source.get("source_id") or manifest.source.source_kind != source.get("data_class"):
        raise ValueError("P7.3 source does not match P7.2 source")
    p7_3_path, p7_3_raw = _p7_3_report(spec, manifest)
    p7_2_source_identity = sha256_identity(source)
    p7_4_path, p7_4_raw, p7_4_report, legacy_identity = _p7_4_report(spec, raw_sha256, p7_2_source_identity)
    value: dict[str, object] = {
        "binding_version": BINDING_VERSION,
        "binding_identity": "0" * 64,
        "case_id": spec.case_id,
        "package_id": spec.package_id,
        "p7_2_inventory_identity": _sha256(inventory_raw),
        "p7_2_source_id": manifest.source.source_id,
        "p7_2_artifact_id": spec.artifact_id,
        "p7_2_raw_trace_logical_path": spec.raw_relative_path,
        "p7_2_raw_trace_bytes": len(raw_trace),
        "p7_2_raw_trace_sha256": raw_sha256,
        "p7_3_manifest_logical_path": spec.manifest_relative_path,
        "p7_3_manifest_sha256": _sha256(manifest_raw),
        "p7_3_source_identity": manifest.source.source_identity,
        "p7_3_artifact_identity": artifact.artifact_identity,
        "p7_3_package_identity": manifest.package_identity,
        "p7_3_report_logical_path": p7_3_path,
        "p7_3_report_sha256": _sha256(p7_3_raw),
        "p7_4_replay_report_logical_path": p7_4_path,
        "p7_4_replay_report_sha256": _sha256(p7_4_raw),
        "p7_4_source_identity": p7_4_report["source_identity"],
        "p7_4_reported_package_identity": legacy_identity,
        "p7_4_trace_identity": p7_4_report["trace_identity"],
        "hardware_validation": False,
    }
    value["binding_identity"] = sha256_identity({key: item for key, item in value.items() if key != "binding_identity"})
    return ExternalCaseEvidenceBinding.from_dict(value)


def build_case_bindings() -> tuple[ExternalCaseEvidenceBinding, ...]:
    return tuple(build_case_binding(spec) for spec in case_package_specs())


def verify_case_binding(value: ExternalCaseEvidenceBinding) -> None:
    spec = next((item for item in case_package_specs() if item.case_id == value.case_id), None)
    if spec is None or build_case_binding(spec) != value:
        raise ValueError("binding does not match frozen cross-phase evidence")


def binding_bytes(value: ExternalCaseEvidenceBinding) -> bytes:
    verify_case_binding(value)
    return canonical_json(value.to_dict())

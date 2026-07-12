from __future__ import annotations

import copy
import unittest
from pathlib import Path

from parser.external_package_models import (
    CONTRACT_NAME,
    CONTRACT_VERSION,
    MANIFEST_VERSION,
    PACKAGE_OPEN_PROFILE_ID,
    ExternalEvidencePackageManifest,
    PackageKind,
    PackageOpenReason,
    PackageOpenReport,
    PackageOpenResult,
    PackageOpenRequest,
    classify_forbidden_key,
    canonical_json,
    sha256_identity,
)
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]


def manifest() -> dict[str, object]:
    artifact = {"artifact_id": "btf_1core", "artifact_kind": "trace", "relative_path": "trace/example.btf", "required": True, "bytes": 1, "sha256": "a" * 64, "media_type": "application/octet-stream", "source_artifact_id": "btf_1core", "content_version": "p7.2-source-inventory-v1", "provenance_reference": "freertos_btf_trace:btf_1core"}
    artifact["artifact_identity"] = sha256_identity(artifact)
    source = {"source_id": "freertos_btf_trace", "source_kind": "public_pre_generated_trace", "repository": "https://example.invalid/source", "repository_commit": "a" * 40, "source_path": "tracedata/example.btf", "source_artifact_id": "btf_1core", "source_checksum": "a" * 64, "source_bytes": 1, "rtos_name": "FreeRTOS", "trace_format": "BTF", "source_format_version": "p7.2-source-inventory-v1", "generation_mode": "pre_generated_repository_sample", "hardware_validation": False, "license_spdx": "MIT", "acquisition_status": "acquired"}
    source["source_identity"] = sha256_identity({"source_kind": source["source_kind"], "source_trace_id": source["source_artifact_id"], "source_trace_checksum": source["source_checksum"], "source_trace_bytes": source["source_bytes"], "source_format": source["trace_format"], "source_format_version": source["source_format_version"], "acquisition_status": source["acquisition_status"]})
    data = {"contract_name": CONTRACT_NAME, "contract_version": CONTRACT_VERSION, "manifest_version": MANIFEST_VERSION, "package_kind": "self_contained", "package_identity": "0" * 64, "source": source, "artifacts": [artifact], "external_references": [], "configuration": {"validation_mode": "offline", "package_profile": "p7.3-directory-v1"}, "configuration_identity": sha256_identity({"validation_mode": "offline", "package_profile": "p7.3-directory-v1"}), "package_open_profile_id": PACKAGE_OPEN_PROFILE_ID, "checksum_algorithm": "sha256", "hardware_validation": False, "limitations": ["No semantic replay."]}
    data["package_identity"] = sha256_identity({"manifest_name": "package_manifest.json", "manifest": {key: value for key, value in data.items() if key != "package_identity"}})
    return data


def report(**changes: object) -> PackageOpenReport:
    values: dict[str, object] = {
        "package_identity": "a" * 64, "package_kind": PackageKind.SELF_CONTAINED,
        "open_result": PackageOpenResult.INVALID,
        "reason_codes": (PackageOpenReason.SYMLINK_FORBIDDEN, PackageOpenReason.ABSOLUTE_PATH),
        "manifest_valid": False, "path_safety_valid": False,
        "required_artifacts_total": 0, "required_artifacts_present": 0,
        "optional_artifacts_total": 0, "artifact_checksum_pass_count": 0,
        "artifact_checksum_fail_count": 0, "artifact_size_pass_count": 0,
        "artifact_size_fail_count": 0, "external_reference_count": 0,
        "external_reference_checked_count": 0, "package_complete": False,
        "source_mutation_count": 0, "package_mutation_count": 0,
        "forbidden_field_count": 0, "absolute_path_count": 0, "path_escape_count": 0,
        "limitations": ("No semantic replay.",),
    }
    values.update(changes)
    return PackageOpenReport(**values)  # type: ignore[arg-type]


class ExternalPackageModelTests(unittest.TestCase):
    def test_schema_mirrors_are_byte_equal(self) -> None:
        for name in ("external_evidence_package.schema.json", "external_package_open_report.schema.json"):
            self.assertEqual((ROOT / "spec/schema" / name).read_bytes(), (ROOT / "spec/assets/schema" / name).read_bytes())

    def test_valid_manifest_is_deterministic(self) -> None:
        value = manifest()
        parsed = ExternalEvidencePackageManifest.from_dict(value)
        self.assertEqual(parsed.package_kind, PackageKind.SELF_CONTAINED)
        self.assertEqual(canonical_json(parsed.to_dict()), canonical_json(value))
        self.assertEqual(parsed.identity_input(), ExternalEvidencePackageManifest.from_dict(value).identity_input())
        self.assertIsNone(validate_schema(load_schema("external_evidence_package.schema.json"), value))

    def test_model_rejects_extra_forbidden_and_duplicate_artifact(self) -> None:
        for mutation in ("api_key", "replay_pass"):
            value = manifest()
            value[mutation] = "bad"
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    ExternalEvidencePackageManifest.from_dict(value)
        value = manifest()
        value["artifacts"].append(copy.deepcopy(value["artifacts"][0]))
        with self.assertRaises(ValueError):
            ExternalEvidencePackageManifest.from_dict(value)

    def test_closed_nested_schema_and_model_reject_extras(self) -> None:
        for location in ("source", "artifacts", "external_references"):
            value = manifest()
            if location == "source":
                value[location]["client_secret"] = "redacted"
            elif location == "artifacts":
                value[location][0]["pii_note"] = "redacted"
            else:
                reference = {"reference_id":"license","reference_kind":"provenance_license","repository":"https://example.invalid/source","repository_commit":"a" * 40,"source_path":"LICENSE","expected_sha256":"a" * 64,"license_spdx":"MIT","availability":"declared","required":False,"source_id":"freertos_btf_trace"}
                reference["reference_identity"] = sha256_identity(reference)
                value[location].append(reference)
                value[location][0]["unknown"] = True
            with self.subTest(location=location):
                self.assertIsNotNone(validate_schema(load_schema("external_evidence_package.schema.json"), value))
                with self.assertRaises(ValueError):
                    ExternalEvidencePackageManifest.from_dict(value)

    def test_invalid_paths_hashes_commits_and_duplicates_are_rejected(self) -> None:
        for key, invalid in (("sha256", "A" * 64), ("relative_path", "../trace"), ("repository_commit", "xyz")):
            value = manifest()
            target = value["artifacts"][0] if key in {"sha256", "relative_path"} else value["source"]
            target[key] = invalid
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    ExternalEvidencePackageManifest.from_dict(value)
        value = manifest()
        reference = {"reference_id":"license","reference_kind":"provenance_license","repository":"https://example.invalid/source","repository_commit":"a" * 40,"source_path":"LICENSE","expected_sha256":"a" * 64,"license_spdx":"MIT","availability":"declared","required":False,"source_id":"freertos_btf_trace"}
        reference["reference_identity"] = sha256_identity(reference)
        value["external_references"] = [reference, copy.deepcopy(reference)]
        with self.assertRaises(ValueError):
            ExternalEvidencePackageManifest.from_dict(value)

    def test_schema_rejects_bad_type_and_extra(self) -> None:
        value = manifest()
        value["hardware_validation"] = True
        self.assertIsNotNone(validate_schema(load_schema("external_evidence_package.schema.json"), value))
        value = manifest()
        value["unknown"] = True
        self.assertIsNotNone(validate_schema(load_schema("external_evidence_package.schema.json"), value))

    def test_report_is_non_replay_and_reason_ordered(self) -> None:
        payload = report().to_dict()
        self.assertFalse(payload["replay_evaluated"])
        self.assertEqual(payload["reason_codes"], sorted(payload["reason_codes"]))
        self.assertNotIn("replay_pass", payload)
        self.assertNotIn("proof_correctness", payload)
        self.assertIsNone(validate_schema(load_schema("external_package_open_report.schema.json"), payload))

    def test_report_rejects_true_replay_and_bad_counts(self) -> None:
        with self.assertRaises(ValueError):
            report(replay_evaluated=True)
        with self.assertRaises(ValueError):
            report(required_artifacts_total=True)
        payload = report().to_dict()
        payload["replay_evaluated"] = True
        with self.assertRaises(ValueError):
            PackageOpenReport.from_dict(payload)

    def test_recursive_aliases_requests_and_runtime_types_are_rejected(self) -> None:
        for key, reason in (("privateKey", PackageOpenReason.SECRET_DETECTED), ("rawHumanFeedback", PackageOpenReason.FORBIDDEN_FIELD), ("piiNote", PackageOpenReason.PII_DETECTED)):
            value = manifest()
            value["source"]["nested"] = [{key: "redacted"}]
            with self.subTest(key=key):
                self.assertEqual(classify_forbidden_key(key), reason)
                with self.assertRaises(ValueError):
                    ExternalEvidencePackageManifest.from_dict(value)
        with self.assertRaises(ValueError):
            PackageOpenRequest(1)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            report(package_kind="invalid")

    def test_unknown_checksum_algorithm_is_retained_for_unsupported_classification(self) -> None:
        value = manifest()
        value["checksum_algorithm"] = "sha3_256"
        parsed = ExternalEvidencePackageManifest.from_dict(value)
        self.assertEqual(parsed.checksum_algorithm, "sha3_256")
        self.assertEqual(parsed.to_dict()["checksum_algorithm"], "sha3_256")

    def test_report_model_normalizes_collections_and_models_reject_direct_bad_values(self) -> None:
        normalized = report(reason_codes=(PackageOpenReason.SYMLINK_FORBIDDEN, PackageOpenReason.ABSOLUTE_PATH, PackageOpenReason.SYMLINK_FORBIDDEN), limitations=("z", "a", "z"))
        self.assertEqual(normalized.reason_codes, tuple(sorted(set(normalized.reason_codes), key=lambda item: item.value)))
        self.assertEqual(normalized.limitations, ("a", "z"))
        with self.assertRaises(ValueError):
            report(hardware_validation=0)

    def test_missing_manifest_report_has_no_identity_or_kind(self) -> None:
        payload = report(package_identity=None, package_kind=None, reason_codes=(PackageOpenReason.MANIFEST_MISSING,)).to_dict()
        self.assertIsNone(payload["package_identity"])
        self.assertIsNone(payload["package_kind"])
        self.assertIsNone(validate_schema(load_schema("external_package_open_report.schema.json"), payload))
        self.assertEqual(PackageOpenReport.from_dict(payload).to_dict(), payload)

    def test_manifest_direct_construction_is_closed_and_normalized(self) -> None:
        parsed = ExternalEvidencePackageManifest.from_dict(manifest())
        with self.assertRaises(ValueError):
            ExternalEvidencePackageManifest(parsed.contract_name, 1, parsed.manifest_version, parsed.package_kind, parsed.package_identity, parsed.source, parsed.artifacts, parsed.external_references, parsed.configuration_identity, parsed.package_open_profile_id, parsed.checksum_algorithm, False, parsed.limitations)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ExternalEvidencePackageManifest(parsed.contract_name, parsed.contract_version, parsed.manifest_version, parsed.package_kind, parsed.package_identity, parsed.source, (parsed.artifacts[0], parsed.artifacts[0]), parsed.external_references, parsed.configuration_identity, parsed.package_open_profile_id, parsed.checksum_algorithm, False, parsed.limitations)

    def test_kind_requiredness_and_success_report_invariants(self) -> None:
        value = manifest()
        value["artifacts"] = []
        with self.assertRaises(ValueError):
            ExternalEvidencePackageManifest.from_dict(value)
        with self.assertRaises(ValueError):
            report(open_result=PackageOpenResult.OPENED, manifest_valid=False)
        valid = report(open_result=PackageOpenResult.OPENED, reason_codes=(), manifest_valid=True, path_safety_valid=True, package_complete=True, package_identity="b" * 64, package_kind=PackageKind.SELF_CONTAINED)
        self.assertEqual(valid.open_result, PackageOpenResult.OPENED)

    def test_schema_keyword_contract_has_strict_model_fallback(self) -> None:
        schema = load_schema("external_evidence_package.schema.json")
        self.assertIn("pattern", schema["properties"]["package_identity"])
        self.assertEqual(schema["properties"]["hardware_validation"]["const"], False)
        self.assertEqual(schema["properties"]["artifacts"]["items"]["additionalProperties"], False)
        for invalid_path in ("../trace", "/trace", "C:/trace", "a\\b", "a//b", "./trace"):
            value = manifest()
            value["artifacts"][0]["relative_path"] = invalid_path
            with self.subTest(path=invalid_path):
                with self.assertRaises(ValueError):
                    ExternalEvidencePackageManifest.from_dict(value)


if __name__ == "__main__":
    unittest.main()

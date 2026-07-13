from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from parser.external_package_models import ExternalEvidencePackageManifest, canonical_json, sha256_identity
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_ROOT = ROOT / "tests/python/fixtures/external_validation/packages/per_case"
CASES = {
    "freertos_btf_1core": ("btf_1core", "example.btf"),
    "freertos_vcd_1core": ("vcd_1core", "example.vcd"),
    "freertos_btf_4cores": ("btf_4cores", "example-4cores.btf"),
    "freertos_btf_50k": ("btf_50k", "example-50k.btf"),
}


def manifest(case_id: str) -> tuple[bytes, dict[str, object]]:
    raw = (MANIFEST_ROOT / case_id / "package_manifest.json").read_bytes()
    return raw, json.loads(raw)


def inventory() -> tuple[dict[str, object], dict[str, object]]:
    data = json.loads((ROOT / "docs/phase7_external_trace_sources/source_inventory.json").read_text(encoding="utf-8"))
    source = next(item for item in data["sources"] if item["source_id"] == "freertos_btf_trace")
    return data, source


def assert_inventory_projection(value: dict[str, object], source: dict[str, object], artifact_id: str, basename: str) -> None:
    artifact_source = next(item for item in source["artifacts"] if item["artifact_id"] == artifact_id)
    descriptor = value["source"]
    artifact = value["artifacts"][0]
    assert descriptor["source_id"] == source["source_id"]
    assert descriptor["source_kind"] == source["data_class"]
    for key in ("repository", "repository_commit", "rtos_name", "trace_format", "generation_mode", "license_spdx", "acquisition_status"):
        assert descriptor[key] == source[key]
    for key in ("source_path", "source_artifact_id", "source_checksum", "source_bytes"):
        assert descriptor[key] == artifact_source[{"source_path": "source_path", "source_artifact_id": "artifact_id", "source_checksum": "sha256", "source_bytes": "bytes"}[key]]
    assert artifact["artifact_id"] == artifact_id
    assert artifact["source_artifact_id"] == artifact_id
    assert artifact["relative_path"] == "artifact/" + basename
    assert artifact["bytes"] == artifact_source["bytes"]
    assert artifact["sha256"] == artifact_source["sha256"]
    assert artifact["provenance_reference"] == source["source_id"] + ":" + artifact_id


class ExternalCasePackageManifestTests(unittest.TestCase):
    def test_manifests_are_canonical_schema_valid_and_inventory_derived(self) -> None:
        _, source = inventory()
        schema = load_schema("external_evidence_package.schema.json")
        for case_id, (artifact_id, basename) in CASES.items():
            with self.subTest(case_id=case_id):
                raw, value = manifest(case_id)
                parsed = ExternalEvidencePackageManifest.from_dict(value)
                self.assertEqual(raw, canonical_json(value))
                self.assertEqual(parsed.to_dict(), value)
                self.assertIsNone(validate_schema(schema, value))
                assert_inventory_projection(value, source, artifact_id, basename)
                artifact = value["artifacts"][0]
                self.assertEqual(artifact["artifact_identity"], sha256_identity({key: item for key, item in artifact.items() if key != "artifact_identity"}))
                descriptor = value["source"]
                source_input = {"source_kind": descriptor["source_kind"], "source_trace_id": descriptor["source_artifact_id"], "source_trace_checksum": descriptor["source_checksum"], "source_trace_bytes": descriptor["source_bytes"], "source_format": descriptor["trace_format"], "source_format_version": descriptor["source_format_version"], "acquisition_status": descriptor["acquisition_status"]}
                self.assertEqual(descriptor["source_identity"], sha256_identity(source_input))
                self.assertEqual(value["package_identity"], sha256_identity(parsed.identity_input()))

    def test_inventory_projection_rejects_wrong_source_artifact_bytes_and_hash(self) -> None:
        _, source = inventory()
        raw, value = manifest("freertos_btf_1core")
        self.assertEqual(raw, canonical_json(value))
        mutations = (("source", "source_id", "other_source"), ("artifact", "source_artifact_id", "vcd_1core"), ("artifact", "bytes", 0), ("artifact", "sha256", "0" * 64))
        for location, key, replacement in mutations:
            with self.subTest(location=location, key=key):
                changed = copy.deepcopy(value)
                target = changed["source"] if location == "source" else changed["artifacts"][0]
                target[key] = replacement
                with self.assertRaises(AssertionError):
                    assert_inventory_projection(changed, source, "btf_1core", "example.btf")

    def test_canonical_serialization_is_stable(self) -> None:
        for case_id in CASES:
            with self.subTest(case_id=case_id):
                raw, value = manifest(case_id)
                self.assertEqual(raw, canonical_json(value))
                self.assertEqual(canonical_json(json.loads(canonical_json(value))), raw)


if __name__ == "__main__":
    unittest.main()

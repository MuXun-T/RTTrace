from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from parser.external_case_evidence_binding_models import BINDING_VERSION, ExternalCaseEvidenceBinding, canonical_json, sha256_identity
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]


def binding() -> dict[str, object]:
    value: dict[str, object] = {
        "binding_version": BINDING_VERSION,
        "binding_identity": "0" * 64,
        "case_id": "freertos_btf_1core",
        "package_id": "p7.3.freertos_btf_1core.v1",
        "p7_2_inventory_identity": "1" * 64,
        "p7_2_source_id": "freertos_btf_trace",
        "p7_2_artifact_id": "btf_1core",
        "p7_2_raw_trace_logical_path": "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.btf",
        "p7_2_raw_trace_bytes": 101774,
        "p7_2_raw_trace_sha256": "2" * 64,
        "p7_3_manifest_logical_path": "tests/python/fixtures/external_validation/packages/per_case/freertos_btf_1core/package_manifest.json",
        "p7_3_manifest_sha256": "3" * 64,
        "p7_3_source_identity": "4" * 64,
        "p7_3_artifact_identity": "5" * 64,
        "p7_3_package_identity": "6" * 64,
        "p7_3_report_logical_path": "tests/python/fixtures/external_validation/packages/reports/per_case/freertos_btf_1core.json",
        "p7_3_report_sha256": "7" * 64,
        "p7_4_replay_report_logical_path": "tests/python/fixtures/external_validation/replay/reports/freertos_btf_1core.json",
        "p7_4_replay_report_sha256": "8" * 64,
        "p7_4_source_identity": "9" * 64,
        "p7_4_reported_package_identity": "a" * 64,
        "p7_4_trace_identity": "b" * 64,
        "hardware_validation": False,
    }
    value["binding_identity"] = sha256_identity({key: item for key, item in value.items() if key != "binding_identity"})
    return value


class ExternalCaseEvidenceBindingModelTests(unittest.TestCase):
    def test_closed_immutable_canonical_round_trip(self) -> None:
        value = binding()
        parsed = ExternalCaseEvidenceBinding.from_dict(value)
        self.assertEqual(parsed.to_dict(), value)
        self.assertEqual(canonical_json(value), canonical_json(parsed.to_dict()))
        self.assertEqual(parsed.binding_identity, sha256_identity(parsed.identity_input()))
        with self.assertRaises(FrozenInstanceError):
            parsed.case_id = "changed"  # type: ignore[misc]

    def test_schema_mirror_and_valid_record(self) -> None:
        name = "external_case_evidence_binding.schema.json"
        self.assertEqual((ROOT / "spec/schema" / name).read_bytes(), (ROOT / "spec/assets/schema" / name).read_bytes())
        self.assertIsNone(validate_schema(load_schema(name), binding()))

    def test_extras_sha_paths_and_self_identity_are_rejected(self) -> None:
        value = binding(); value["extra"] = True
        with self.assertRaises(ValueError):
            ExternalCaseEvidenceBinding.from_dict(value)
        for key in ("binding_identity", "p7_2_inventory_identity", "p7_2_raw_trace_sha256", "p7_3_manifest_sha256", "p7_3_source_identity", "p7_3_artifact_identity", "p7_3_package_identity", "p7_3_report_sha256", "p7_4_replay_report_sha256", "p7_4_source_identity", "p7_4_reported_package_identity", "p7_4_trace_identity"):
            with self.subTest(key=key):
                value = binding(); value[key] = "A" * 64
                with self.assertRaises(ValueError):
                    ExternalCaseEvidenceBinding.from_dict(value)
        for key, path in (("p7_2_raw_trace_logical_path", "/tmp/raw"), ("p7_3_manifest_logical_path", "../manifest"), ("p7_3_report_logical_path", "temp/report.json"), ("p7_4_replay_report_logical_path", "C:/report.json")):
            with self.subTest(key=key):
                value = binding(); value[key] = path
                value["binding_identity"] = sha256_identity({name: item for name, item in value.items() if name != "binding_identity"})
                with self.assertRaises(ValueError):
                    ExternalCaseEvidenceBinding.from_dict(value)
        value = binding(); value["binding_identity"] = "0" * 64
        with self.assertRaises(ValueError):
            ExternalCaseEvidenceBinding.from_dict(value)

    def test_schema_is_closed_and_declares_sha_and_path_patterns(self) -> None:
        schema = load_schema("external_case_evidence_binding.schema.json")
        value = copy.deepcopy(binding()); value["extra"] = True
        self.assertIsNotNone(validate_schema(schema, value))
        self.assertEqual(schema["properties"]["p7_2_raw_trace_sha256"]["pattern"], "^[0-9a-f]{64}$")
        self.assertIn("(?!/)", schema["properties"]["p7_3_report_logical_path"]["pattern"])


if __name__ == "__main__":
    unittest.main()

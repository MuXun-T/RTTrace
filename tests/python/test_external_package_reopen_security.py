from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from parser.external_package_models import PackageOpenReason, PackageOpenResult, sha256_identity
from parser.external_package_reader import MANIFEST_NAME, read_directory_package
import parser.external_package_validator as validator
from parser.external_package_validator import validate_package
from tests.python.test_external_package_reopen import package_manifest


class PackageValidatorTests(unittest.TestCase):
    def make_package(self, artifact_id: str = "btf_1core") -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory(); root = Path(temp.name)
        value = package_manifest(); inventory = json.loads((Path(__file__).resolve().parents[2] / "docs/phase7_external_trace_sources/source_inventory.json").read_text(encoding="utf-8")); source_record = next(item for item in inventory["sources"] if item["source_id"] == "freertos_btf_trace"); artifact_record = next(item for item in source_record["artifacts"] if item["artifact_id"] == artifact_id)
        data = (Path(__file__).resolve().parents[2] / artifact_record["local_path"]).read_bytes(); digest = hashlib.sha256(data).hexdigest()
        value["artifacts"][0].update({"source_artifact_id":artifact_id,"bytes":len(data),"sha256":digest,"provenance_reference":f"freertos_btf_trace:{artifact_id}"}); value["artifacts"][0]["artifact_identity"] = sha256_identity({key:item for key,item in value["artifacts"][0].items() if key != "artifact_identity"}); value["source"].update({"source_kind":source_record["data_class"],"repository":source_record["repository"],"repository_commit":source_record["repository_commit"],"source_path":artifact_record["source_path"],"source_artifact_id":artifact_id,"source_checksum":digest,"source_bytes":len(data),"rtos_name":source_record["rtos_name"],"trace_format":source_record["trace_format"],"generation_mode":source_record["generation_mode"],"license_spdx":source_record["license_spdx"],"acquisition_status":source_record["acquisition_status"]}); value["source"]["source_identity"] = sha256_identity({"source_kind":value["source"]["source_kind"],"source_trace_id":value["source"]["source_artifact_id"],"source_trace_checksum":digest,"source_trace_bytes":len(data),"source_format":value["source"]["trace_format"],"source_format_version":value["source"]["source_format_version"],"acquisition_status":value["source"]["acquisition_status"]}); value["package_identity"] = "0" * 64; value["package_identity"] = sha256_identity({"manifest_name":MANIFEST_NAME,"manifest":{key:item for key,item in value.items() if key != "package_identity"}})
        (root / "trace.btf").write_bytes(data); (root / MANIFEST_NAME).write_text(json.dumps(value),encoding="utf-8"); return temp,root

    def test_opened_and_integrity_failures(self) -> None:
        temp, root = self.make_package()
        with temp:
            self.assertEqual(validate_package(read_directory_package(root)).open_result, PackageOpenResult.OPENED)
            read = read_directory_package(root); (root / "trace.btf").write_bytes(b"bad")
            report = validate_package(read)
            self.assertEqual(report.open_result, PackageOpenResult.INVALID)
            self.assertIn(PackageOpenReason.PACKAGE_MUTATION, report.reason_codes)
            self.assertIn(PackageOpenReason.REQUIRED_ARTIFACT_MISSING, report.reason_codes)

    def test_all_frozen_freertos_artifacts_open_without_copying_fixtures(self) -> None:
        for artifact_id in ("btf_1core", "vcd_1core", "btf_4cores", "btf_50k"):
            temp, root = self.make_package(artifact_id)
            with temp, self.subTest(artifact_id=artifact_id):
                report = validate_package(read_directory_package(root))
                self.assertEqual(report.open_result, PackageOpenResult.OPENED)
                self.assertEqual(report.source_mutation_count, 0)
                self.assertEqual(report.package_mutation_count, 0)

    def test_metadata_reference_only_and_blocked(self) -> None:
        inventory = json.loads((Path(__file__).resolve().parents[2] / "docs/phase7_external_trace_sources/source_inventory.json").read_text(encoding="utf-8"))
        for source_id, kind, availability, expected in (("zephyr_pipeline", "metadata_only", "declared", PackageOpenResult.OPENED_REFERENCE_ONLY), ("zephelin_optional", "referenced_external", "unavailable", PackageOpenResult.BLOCKED)):
            source_record = next(item for item in inventory["sources"] if item["source_id"] == source_id)
            source = {"source_id":source_id,"source_kind":source_record["data_class"],"repository":source_record["repository"],"repository_commit":source_record["repository_commit"],"source_path":source_record["source_path"],"source_artifact_id":None,"source_checksum":None,"source_bytes":None,"rtos_name":source_record["rtos_name"],"trace_format":source_record["trace_format"],"source_format_version":"p7.2-source-inventory-v1","generation_mode":source_record["generation_mode"],"hardware_validation":False,"license_spdx":source_record["license_spdx"],"acquisition_status":source_record["acquisition_status"]}
            source["source_identity"] = sha256_identity({"source_kind":source["source_kind"],"source_trace_id":None,"source_trace_checksum":None,"source_trace_bytes":None,"source_format":source["trace_format"],"source_format_version":source["source_format_version"],"acquisition_status":source["acquisition_status"]})
            trace = source_record["artifacts"][0] if source_record["artifacts"] else None
            reference = {"reference_id":"trace" if trace else "license","reference_kind":"trace_content" if trace else "provenance_license","repository":source_record["repository"],"repository_commit":source_record["repository_commit"],"source_path":trace["source_path"] if trace else "LICENSE","expected_sha256":trace["sha256"] if trace else source_record["license_sha256"],"license_spdx":source_record["license_spdx"],"availability":availability,"required":availability != "declared","source_id":source_id}
            reference["reference_identity"] = sha256_identity(reference)
            value = {"contract_name":"rttrace_external_evidence_package","contract_version":"p7.3-package-open-v1","manifest_version":"external-evidence-package-v1","package_kind":kind,"package_identity":"0"*64,"source":source,"artifacts":[],"external_references":[reference],"configuration":{"validation_mode":"offline","package_profile":"p7.3-directory-v1"},"configuration_identity":sha256_identity({"validation_mode":"offline","package_profile":"p7.3-directory-v1"}),"package_open_profile_id":"p7.3-package-open-only-v1","checksum_algorithm":"sha256","hardware_validation":False,"limitations":["semantic_replay_not_evaluated","trace_artifact_not_acquired"]}
            value["package_identity"] = sha256_identity({"manifest_name":MANIFEST_NAME,"manifest":{key:item for key,item in value.items() if key != "package_identity"}})
            with tempfile.TemporaryDirectory() as name, self.subTest(source=source_id):
                root = Path(name); (root / MANIFEST_NAME).write_text(json.dumps(value),encoding="utf-8")
                report = validate_package(read_directory_package(root))
                self.assertEqual(report.open_result, expected)
                self.assertFalse(report.replay_evaluated)

    def test_source_mutation_and_validator_side_effect_guards(self) -> None:
        temp, root = self.make_package()
        with temp, mock.patch("parser.external_package_validator._source_snapshot", side_effect=[((), False), ((), False)]), mock.patch("subprocess.run", side_effect=AssertionError), mock.patch("os.getenv", side_effect=AssertionError):
            report = validate_package(read_directory_package(root))
            self.assertEqual(report.open_result, PackageOpenResult.INVALID)
            self.assertGreater(report.source_mutation_count, 0)
            self.assertIn(PackageOpenReason.PACKAGE_SOURCE_MUTATION, report.reason_codes)

    def test_descriptor_is_loaded_once_and_acquisition_status_is_anchored(self) -> None:
        temp, root = self.make_package()
        with temp, mock.patch.object(validator, "_load_inventory", wraps=validator._load_inventory) as loaded:
            self.assertEqual(validate_package(read_directory_package(root)).open_result, PackageOpenResult.OPENED)
            self.assertEqual(loaded.call_count, 1)
        for status in ("acquisition_blocked", "external_reference_only"):
            temp, root = self.make_package()
            with temp, self.subTest(status=status):
                value = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8")); source = value["source"]
                source["acquisition_status"] = status
                source["source_identity"] = sha256_identity({"source_kind":source["source_kind"],"source_trace_id":source["source_artifact_id"],"source_trace_checksum":source["source_checksum"],"source_trace_bytes":source["source_bytes"],"source_format":source["trace_format"],"source_format_version":source["source_format_version"],"acquisition_status":status})
                value["package_identity"] = sha256_identity({"manifest_name":MANIFEST_NAME,"manifest":{key:item for key,item in value.items() if key != "package_identity"}})
                (root / MANIFEST_NAME).write_text(json.dumps(value), encoding="utf-8")
                report = validate_package(read_directory_package(root))
                self.assertEqual(report.open_result, PackageOpenResult.INVALID)
                self.assertIn(PackageOpenReason.SOURCE_IDENTITY_MISMATCH, report.reason_codes)

    def test_package_mutation_counts_unique_descriptor_entries(self) -> None:
        temp, root = self.make_package()
        with temp:
            read = read_directory_package(root); before_root, before_entries = validator._package_snapshot(read)
            after_root = replace(before_root, inode=(before_root.inode or 0) + 1)
            after_entries = tuple((path, replace(snapshot, sha256="0" * 64)) if path == MANIFEST_NAME else (path, snapshot) for path, snapshot in before_entries)
            with mock.patch.object(validator, "_package_snapshot", side_effect=[(before_root, before_entries), (after_root, after_entries)]):
                report = validate_package(read)
            self.assertEqual(report.package_mutation_count, 2)
            self.assertIn(PackageOpenReason.PACKAGE_MUTATION, report.reason_codes)


if __name__ == "__main__": unittest.main()

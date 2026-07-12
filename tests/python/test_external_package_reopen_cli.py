from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tool.run_external_package_reopen import main
from parser.external_package_models import sha256_identity
from parser.external_package_reader import MANIFEST_NAME
from tests.python.test_external_package_reopen import package_manifest
from tests.python.test_external_package_reopen_security import PackageValidatorTests


class PackageReopenCliTests(unittest.TestCase):
    def reference_package(self, root: Path, availability: str) -> None:
        inventory = json.loads((Path(__file__).resolve().parents[2] / "docs/phase7_external_trace_sources/source_inventory.json").read_text(encoding="utf-8"))
        source_record = next(item for item in inventory["sources"] if item["source_id"] == "zephyr_pipeline")
        source = {"source_id":"zephyr_pipeline","source_kind":source_record["data_class"],"repository":source_record["repository"],"repository_commit":source_record["repository_commit"],"source_path":source_record["source_path"],"source_artifact_id":None,"source_checksum":None,"source_bytes":None,"rtos_name":source_record["rtos_name"],"trace_format":source_record["trace_format"],"source_format_version":"p7.2-source-inventory-v1","generation_mode":source_record["generation_mode"],"hardware_validation":False,"license_spdx":source_record["license_spdx"],"acquisition_status":source_record["acquisition_status"]}
        source["source_identity"] = sha256_identity({"source_kind":source["source_kind"],"source_trace_id":None,"source_trace_checksum":None,"source_trace_bytes":None,"source_format":source["trace_format"],"source_format_version":source["source_format_version"],"acquisition_status":source["acquisition_status"]})
        reference = {"reference_id":"license","reference_kind":"provenance_license","repository":source["repository"],"repository_commit":source["repository_commit"],"source_path":"LICENSE","expected_sha256":source_record["license_sha256"],"license_spdx":source["license_spdx"],"availability":availability,"required":availability != "declared","source_id":"zephyr_pipeline"}
        reference["reference_identity"] = sha256_identity(reference)
        value = {"contract_name":"rttrace_external_evidence_package","contract_version":"p7.3-package-open-v1","manifest_version":"external-evidence-package-v1","package_kind":"metadata_only","package_identity":"0"*64,"source":source,"artifacts":[],"external_references":[reference],"configuration":{"validation_mode":"offline","package_profile":"p7.3-directory-v1"},"configuration_identity":sha256_identity({"validation_mode":"offline","package_profile":"p7.3-directory-v1"}),"package_open_profile_id":"p7.3-package-open-only-v1","checksum_algorithm":"sha256","hardware_validation":False,"limitations":["trace_artifact_not_acquired"]}
        value["package_identity"] = sha256_identity({"manifest_name":MANIFEST_NAME,"manifest":{key:item for key,item in value.items() if key != "package_identity"}})
        (root / MANIFEST_NAME).write_text(json.dumps(value), encoding="utf-8")

    def test_opened_report_is_canonical_and_repeatable(self) -> None:
        fixture = PackageValidatorTests(); temp, root = fixture.make_package()
        with temp, tempfile.TemporaryDirectory() as output:
            first, second = Path(output) / "first.json", Path(output) / "second.json"
            self.assertEqual(main(["--package", str(root), "--output", str(first)]), 0)
            self.assertEqual(main(["--package", str(root), "--output", str(second), "--offline"]), 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), hashlib.sha256(second.read_bytes()).hexdigest())
            report = json.loads(first.read_text(encoding="utf-8"))
            self.assertFalse(report["replay_evaluated"])
            self.assertFalse(report["hardware_validation"])
            self.assertNotIn("replay_pass", report)

    def test_invalid_package_writes_report_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "report.json"
            self.assertEqual(main(["--package", name, "--output", str(output)]), 3)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["open_result"], "invalid")
            self.assertEqual(report["reason_codes"], ["ERR_PACKAGE_MANIFEST_MISSING"])

    def test_all_result_exit_codes_are_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            opened = PackageValidatorTests(); source_temp, source_root = opened.make_package()
            with source_temp:
                reference = base / "reference"; reference.mkdir(); self.reference_package(reference, "declared")
                blocked = base / "blocked"; blocked.mkdir(); self.reference_package(blocked, "unavailable")
                hybrid = base / "hybrid"; hybrid.mkdir()
                value = json.loads((source_root / MANIFEST_NAME).read_text(encoding="utf-8")); value["package_kind"] = "hybrid"; value["package_identity"] = sha256_identity({"manifest_name":MANIFEST_NAME,"manifest":{key:item for key,item in value.items() if key != "package_identity"}})
                (hybrid / "trace.btf").write_bytes((source_root / "trace.btf").read_bytes()); (hybrid / MANIFEST_NAME).write_text(json.dumps(value), encoding="utf-8")
                for label, package, code in (("opened", source_root, 0), ("reference", reference, 2), ("blocked", blocked, 4), ("invalid", base, 3), ("unsupported", hybrid, 5)):
                    first, second = base / f"{label}-1.json", base / f"{label}-2.json"
                    with self.subTest(label=label):
                        self.assertEqual(main(["--package", str(package), "--output", str(first)]), code)
                        self.assertEqual(main(["--package", str(package), "--output", str(second)]), code)
                        self.assertEqual(first.read_bytes(), second.read_bytes())
                        self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), hashlib.sha256(second.read_bytes()).hexdigest())


if __name__ == "__main__": unittest.main()

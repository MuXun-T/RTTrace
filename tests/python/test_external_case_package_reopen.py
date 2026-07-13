from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from parser.external_case_package_evidence import case_package_specs, materialized_case_package, read_regular_no_follow, reopen_case, reproduce_case_report
from parser.external_package_models import ExternalEvidencePackageManifest


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "tests/python/fixtures/external_validation/packages/reports/per_case"
OLD_REPORT = ROOT / "tests/python/fixtures/external_validation/packages/reports/opened.json"


class ExternalCasePackageReopenTests(unittest.TestCase):
    def test_each_case_reopens_through_cli_and_matches_canonical_report(self) -> None:
        for spec in case_package_specs():
            with self.subTest(case_id=spec.case_id), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "report.json"
                self.assertEqual(reopen_case(spec, output), 0)
                report = json.loads(output.read_text(encoding="utf-8"))
                manifest = ExternalEvidencePackageManifest.from_dict(json.loads(read_regular_no_follow(spec.manifest_relative_path)))
                self.assertEqual(report["open_result"], "opened")
                self.assertEqual(report["package_identity"], manifest.package_identity)
                self.assertTrue(report["manifest_valid"])
                self.assertTrue(report["path_safety_valid"])
                self.assertTrue(report["package_complete"])
                self.assertEqual(report["required_artifacts_total"], 1)
                self.assertEqual(report["required_artifacts_present"], 1)
                self.assertEqual(report["artifact_checksum_pass_count"], 1)
                self.assertEqual(report["artifact_checksum_fail_count"], 0)
                self.assertEqual(report["artifact_size_pass_count"], 1)
                self.assertEqual(report["artifact_size_fail_count"], 0)
                self.assertFalse(report["hardware_validation"])
                self.assertFalse(report["replay_evaluated"])
                self.assertEqual(report["reason_codes"], [])
                self.assertEqual(report["source_mutation_count"], 0)
                self.assertEqual(report["package_mutation_count"], 0)
                self.assertEqual(output.read_bytes(), (REPORT_ROOT / f"{spec.case_id}.json").read_bytes())

    def test_reproduction_is_byte_and_hash_stable(self) -> None:
        for spec in case_package_specs():
            with self.subTest(case_id=spec.case_id):
                first = reproduce_case_report(spec)
                second = reproduce_case_report(spec)
                self.assertEqual(first, second)
                self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())

    def test_materialization_rejects_replaced_raw_bytes(self) -> None:
        spec = case_package_specs()[0]
        with mock.patch("parser.external_case_package_evidence.read_regular_no_follow", side_effect=[read_regular_no_follow(spec.manifest_relative_path), b"replacement"]):
            with self.assertRaises(ValueError):
                with materialized_case_package(spec):
                    pass

    def test_no_follow_reader_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            target = root / "target"; target.write_bytes(b"x")
            link = root / "link"; link.symlink_to(target)
            relative = str(link.relative_to(ROOT))
            with self.assertRaises(OSError):
                read_regular_no_follow(relative)

    def test_old_aggregate_report_is_unchanged(self) -> None:
        self.assertEqual(hashlib.sha256(OLD_REPORT.read_bytes()).hexdigest(), "dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1")


if __name__ == "__main__":
    unittest.main()

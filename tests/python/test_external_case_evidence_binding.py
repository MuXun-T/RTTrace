from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from parser.external_case_evidence_binding import P7_4_REPORT_PATHS, binding_bytes, build_case_binding, build_case_bindings, verify_case_binding
from parser.external_case_evidence_binding_models import ExternalCaseEvidenceBinding, sha256_identity
from parser.external_case_package_evidence import case_package_specs, read_regular_no_follow
from tool import build_external_case_evidence_bindings


ROOT = Path(__file__).resolve().parents[2]
OLD_OPENED = ROOT / "tests/python/fixtures/external_validation/packages/reports/opened.json"
P7_4_REPORTS = ROOT / "tests/python/fixtures/external_validation/replay/reports"


def changed(binding: ExternalCaseEvidenceBinding, key: str, value: object) -> ExternalCaseEvidenceBinding:
    record = copy.deepcopy(binding.to_dict())
    record[key] = value
    record["binding_identity"] = sha256_identity({name: item for name, item in record.items() if name != "binding_identity"})
    return ExternalCaseEvidenceBinding.from_dict(record)


class ExternalCaseEvidenceBindingTests(unittest.TestCase):
    def test_builds_four_complete_bindings_and_validates_frozen_facts(self) -> None:
        bindings = build_case_bindings()
        self.assertEqual(len(bindings), 4)
        self.assertEqual([item.case_id for item in bindings], ["freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k"])
        for binding in bindings:
            with self.subTest(case_id=binding.case_id):
                verify_case_binding(binding)
                self.assertEqual(binding.p7_2_raw_trace_sha256, binding.p7_4_trace_identity)
                self.assertFalse(binding.hardware_validation)
                self.assertEqual(binding.binding_identity, sha256_identity(binding.identity_input()))
                self.assertEqual(json.loads(binding_bytes(binding)), binding.to_dict())

    def test_wrong_or_swapped_binding_fails_closed(self) -> None:
        binding = build_case_bindings()[0]
        for key, value in (("case_id", "freertos_btf_4cores"), ("p7_2_raw_trace_sha256", "0" * 64), ("p7_3_manifest_sha256", "0" * 64), ("p7_3_artifact_identity", "0" * 64), ("p7_3_package_identity", "0" * 64), ("p7_3_report_sha256", "0" * 64), ("p7_4_replay_report_sha256", "0" * 64), ("p7_4_trace_identity", "0" * 64), ("p7_4_reported_package_identity", "0" * 64)):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    verify_case_binding(changed(binding, key, value))

    def test_same_logical_raw_name_with_replaced_bytes_fails_closed(self) -> None:
        spec = case_package_specs()[0]
        real_read = read_regular_no_follow

        def replaced(path: str) -> bytes:
            return b"replacement" if path == spec.raw_relative_path else real_read(path)

        with mock.patch("parser.external_case_evidence_binding.read_regular_no_follow", side_effect=replaced):
            with self.assertRaises(ValueError):
                build_case_binding(spec)

    def test_p7_4_source_identity_must_match_the_sealed_p7_2_source(self) -> None:
        spec = case_package_specs()[0]
        real_read = read_regular_no_follow

        def changed_source_identity(path: str) -> bytes:
            raw = real_read(path)
            if path == P7_4_REPORT_PATHS[spec.case_id]:
                report = json.loads(raw)
                report["source_identity"] = "0" * 64
                return json.dumps(report, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            return raw

        with mock.patch("parser.external_case_evidence_binding.read_regular_no_follow", side_effect=changed_source_identity):
            with self.assertRaises(ValueError):
                build_case_binding(spec)

    def test_cli_build_stdout_verify_and_repository_output_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "binding.json"
            self.assertEqual(build_external_case_evidence_bindings.main(["build", "--case", "freertos_btf_1core", "--output", str(output)]), 0)
            self.assertEqual(build_external_case_evidence_bindings.main(["verify", "--input", str(output)]), 0)
            link = Path(directory) / "binding-link.json"; link.symlink_to(output)
            self.assertEqual(build_external_case_evidence_bindings.main(["verify", "--input", str(link)]), 2)
            output_link = Path(directory) / "output-link"; output_link.symlink_to(Path(directory), target_is_directory=True)
            self.assertEqual(build_external_case_evidence_bindings.main(["build", "--case", "freertos_btf_1core", "--output", str(output_link / "new.json")]), 2)
        self.assertEqual(build_external_case_evidence_bindings.main(["build", "--case", "freertos_btf_1core", "--output", "binding.json"]), 2)

    def test_existing_canonical_artifacts_are_unchanged(self) -> None:
        self.assertEqual(hashlib.sha256(OLD_OPENED.read_bytes()).hexdigest(), "dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1")
        expected = {
            "freertos_btf_1core.json": "c75143ce8c2c2ec689289a6e52848cb5c4fca42ce343f6710571075478708b8d",
            "freertos_vcd_1core.json": "17a8037ba36e12bd04aaa376fac253eac400585aebdee9e7ad7d751a2ebea257",
            "freertos_btf_4cores.json": "199aacdc0d1341d4fd799da10d1405e034065da13fa2bfbc865af1358e53fc24",
            "freertos_btf_50k.json": "97ef68f12bd085ed66fb0fff50c65134c70cf91b0b433991e01f9ec1fd2f98e3",
        }
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((P7_4_REPORTS / name).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()

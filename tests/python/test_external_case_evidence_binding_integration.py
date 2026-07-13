from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from parser.external_case_evidence_binding import binding_bytes, build_case_bindings
from parser.external_case_package_evidence import case_package_specs, reproduce_case_report


ROOT = Path(__file__).resolve().parents[2]
HASHES = {
    "tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json": "fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e",
    "docs/phase7_external_trace_sources/source_inventory.json": "ad15a481e2dde8eea0ef2b6e3feecc083e30699532296f27d1095cacaedde54b",
    "tests/python/fixtures/external_validation/packages/reports/opened.json": "dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1",
    "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.btf": "571183cbafba85f0eeb9c74cf2350f02a4e628abe514409dd3c1b68286969f44",
    "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.vcd": "7aedcf4e14bb0e34838613225ff50e6cb8d76ed62e12bcfe16aa6101b0f36997",
    "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example-4cores.btf": "eb15beee65c62d53a3bbf9db5ebb36318156b720e4bd909272605dfeb1c6eed1",
    "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example-50k.btf": "f032a6af43334abc5c5fbed6b145e711f0262e2b1dfa6d3a44a28c37b6308baa",
    "tests/python/fixtures/external_validation/replay/reports/freertos_btf_1core.json": "c75143ce8c2c2ec689289a6e52848cb5c4fca42ce343f6710571075478708b8d",
    "tests/python/fixtures/external_validation/replay/reports/freertos_vcd_1core.json": "17a8037ba36e12bd04aaa376fac253eac400585aebdee9e7ad7d751a2ebea257",
    "tests/python/fixtures/external_validation/replay/reports/freertos_btf_4cores.json": "199aacdc0d1341d4fd799da10d1405e034065da13fa2bfbc865af1358e53fc24",
    "tests/python/fixtures/external_validation/replay/reports/freertos_btf_50k.json": "97ef68f12bd085ed66fb0fff50c65134c70cf91b0b433991e01f9ec1fd2f98e3",
    "tests/python/fixtures/external_validation/replay/reports/zephyr.json": "3f65a0553920237433e3c762548c4925fe2723441af6d7307318a6d8425cbe98",
    "tests/python/fixtures/external_validation/replay/reports/zephelin.json": "15d39789104f16103842f5ea0e7404af06c1f16138c5e547ff6ba7722f23d307",
    "tests/python/fixtures/external_validation/replay/expected/freertos_btf_1core.expected.json": "7d3754c0135cb86d73308faaa4029b42f1f1232731047ba2b751829772a41bb0",
    "tests/python/fixtures/external_validation/replay/expected/freertos_vcd_1core.expected.json": "e7e315db08d509281eb508b1c50b79a12f873730bd020f9c4a6b1591e84971dc",
    "tests/python/fixtures/external_validation/replay/expected/freertos_btf_4cores.expected.json": "a53348ed87e1a431078102e280db56d641e6fca67cde9cdb124a34ec0246a157",
    "tests/python/fixtures/external_validation/replay/expected/freertos_btf_50k.expected.json": "8648b4d9e30b2204eb9727b9d582e12780dce4e068b8eab894342cf36ad452ba",
    "tests/python/fixtures/external_validation/replay/comparison_profiles/p7.4-freertos-semantic-exact-v1.json": "6d2199ba72608266685d55dd278a632e7918af35c30c9e351c3df6186bb1eee2",
}


class ExternalCaseEvidenceBindingIntegrationTests(unittest.TestCase):
    def test_frozen_hashes_and_replay_states_are_unchanged(self) -> None:
        for relative_path, expected in HASHES.items():
            with self.subTest(path=relative_path):
                self.assertEqual(hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest(), expected)
        states = {
            "freertos_btf_1core": ("replay_pass", None),
            "freertos_vcd_1core": ("replay_fail", "UNSUPPORTED_REQUIRED_RECORD"),
            "freertos_btf_4cores": ("replay_pass", None),
            "freertos_btf_50k": ("replay_fail", "TIMESTAMP_REGRESSION"),
            "zephyr": ("reference_only", "SOURCE_ACQUISITION_BLOCKED"),
            "zephelin": ("reference_only", "SOURCE_EXTERNAL_REFERENCE_ONLY"),
        }
        for case_id, expected in states.items():
            report = json.loads((ROOT / "tests/python/fixtures/external_validation/replay/reports" / f"{case_id}.json").read_bytes())
            self.assertEqual((report["replay_state"], report["primary_reason"]), expected)

    def test_per_case_reports_and_bindings_reproduce_canonical_bytes(self) -> None:
        specs = case_package_specs()
        first_reports = tuple(reproduce_case_report(spec) for spec in specs)
        second_reports = tuple(reproduce_case_report(spec) for spec in specs)
        self.assertEqual(first_reports, second_reports)
        for spec, report in zip(specs, first_reports):
            with self.subTest(case_id=spec.case_id):
                path = ROOT / "tests/python/fixtures/external_validation/packages/reports/per_case" / f"{spec.case_id}.json"
                self.assertEqual(report, path.read_bytes())
        first_bindings = tuple(binding_bytes(item) for item in build_case_bindings())
        second_bindings = tuple(binding_bytes(item) for item in build_case_bindings())
        self.assertEqual(first_bindings, second_bindings)
        for spec, binding in zip(specs, first_bindings):
            with self.subTest(case_id=spec.case_id):
                path = ROOT / "tests/python/fixtures/external_validation/evidence_bindings" / f"{spec.case_id}.json"
                self.assertEqual(binding, path.read_bytes())


if __name__ == "__main__":
    unittest.main()

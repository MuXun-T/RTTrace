from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import unittest
from unittest import mock

import parser.external_layered_validation as layered
import parser.external_replay_equivalence_validator as equivalence
from parser.external_layered_validation_models import ProofFactDrift, ProofParity, ProofParityEligibility
from parser.external_validation_intake import case_input_paths, intake_case
from parser.external_evidence_closure_validator import validate_evidence_closure
from parser.external_case_package_evidence import read_regular_no_follow


ROOT = Path(__file__).resolve().parents[2]
CASES = ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k", "zephyr", "zephelin")


def input_hashes() -> dict[str, str]:
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for case_id in CASES for path in case_input_paths(case_id)}


class ExternalLayeredValidationSecurityTests(unittest.TestCase):
    def test_all_frozen_inputs_remain_unchanged_and_reports_have_no_paths(self) -> None:
        before = input_hashes()
        for case_id in CASES:
            with self.subTest(case_id=case_id):
                report = layered.validate_case(case_id)
                raw = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
                self.assertNotIn(str(ROOT), raw)
                self.assertNotIn("/tmp/", raw)
        self.assertEqual(before, input_hashes())

    def test_binding_swap_and_input_toctou_fail_closed(self) -> None:
        target = "tests/python/fixtures/external_validation/evidence_bindings/freertos_btf_1core.json"
        swapped = (ROOT / "tests/python/fixtures/external_validation/evidence_bindings/freertos_btf_4cores.json").read_bytes()

        def swap(path: str) -> bytes:
            return swapped if path == target else read_regular_no_follow(path)

        with mock.patch("parser.external_validation_intake.read_regular_no_follow", side_effect=swap):
            report = layered.validate_case("freertos_btf_1core")
        self.assertEqual(report.validation_state.value, "validation_fail")
        self.assertEqual(report.primary_reason.value, "BINDING_FIXTURE_HASH_MISMATCH")

        raw_path = "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.btf"
        calls = 0

        def mutate_after_first_read(path: str) -> bytes:
            nonlocal calls
            if path == raw_path:
                calls += 1
                return read_regular_no_follow(path) if calls == 1 else b"changed"
            return read_regular_no_follow(path)

        with mock.patch("parser.external_validation_intake.read_regular_no_follow", side_effect=mutate_after_first_read):
            with self.assertRaises(ValueError):
                intake_case("freertos_btf_1core")

    def test_swapped_replay_report_is_validation_failure_and_reason_order_is_closed(self) -> None:
        swapped = equivalence.reproduce_and_compare_p7_4("freertos_btf_4cores").frozen_report
        with mock.patch("parser.external_replay_equivalence_validator._direct_replay", return_value=swapped):
            report = layered.validate_case("freertos_btf_1core")
        self.assertEqual(report.validation_state.value, "validation_fail")
        self.assertIn("RESULT_EQUIVALENCE_MISMATCH", [item.value for item in report.reason_codes])
        value = report.to_dict(); value["reason_codes"] = list(reversed(value["reason_codes"]))
        if len(value["reason_codes"]) > 1:
            with self.assertRaises(ValueError):
                type(report).from_dict(value)

    def test_hidden_or_injected_proof_parity_cannot_be_reported(self) -> None:
        report = layered.validate_case("freertos_btf_1core")
        injected = copy.deepcopy(report.to_dict())
        injected["proof_parity_eligible"] = True
        with self.assertRaises(ValueError):
            type(report).from_dict(injected)
        injected["proof_parity_eligible"] = False
        injected["proof_parity"] = "parity_pass"
        with self.assertRaises(ValueError):
            type(report).from_dict(injected)
        injected_gate = (ProofFactDrift(0, 0, ()), ProofParityEligibility(True, ProofParity.PARITY_PASS, "INJECTED"))
        with mock.patch("parser.external_layered_validation.evaluate_proof_parity_eligibility", return_value=injected_gate):
            with self.assertRaises(ValueError):
                layered.validate_case("freertos_btf_1core")

    def test_truth_path_has_no_forbidden_capabilities(self) -> None:
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError), mock.patch.object(subprocess, "run", side_effect=AssertionError), mock.patch("os.system", side_effect=AssertionError):
            for case_id in CASES:
                with self.subTest(case_id=case_id):
                    report = layered.validate_case(case_id)
                    self.assertEqual(report.hardware_validation, False)
        for path in (ROOT / "parser/external_layered_validation.py", ROOT / "parser/external_evidence_closure_validator.py", ROOT / "parser/external_replay_equivalence_validator.py", ROOT / "parser/external_validation_drift.py", ROOT / "parser/external_proof_parity_gate.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("import subprocess", "from subprocess", "subprocess.", "import socket", "from socket", "socket.", "os.system(", "os.getenv(", "os.environ", "import requests", "requests.", "urllib."):
                with self.subTest(path=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

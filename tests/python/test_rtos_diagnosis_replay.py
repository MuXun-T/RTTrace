from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from parser.rtos_diagnosis_replay import (
    FORBIDDEN_FIELD_NAMES,
    REPLAY_STATUSES,
    build_bounded_representation,
    companion_root_for_suite,
    load_suite,
    run_replay_case,
    run_replay_suite,
)


ROOT = Path(__file__).resolve().parents[2]
SUITE_PATH = ROOT / "tests/python/fixtures/rtos_diagnosis/generated/phase6_synthetic_suite.json"


class RtosDiagnosisReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = load_suite(SUITE_PATH)
        self.companion_root = companion_root_for_suite(SUITE_PATH)
        self.cases = {case["case_kind"]: case for case in self.suite["cases"]}

    def test_all_eight_cases_are_validated_as_reference_only_with_full_retention(self) -> None:
        results, summary = run_replay_suite(self.suite, self.companion_root)
        self.assertEqual(len(results), 8)
        self.assertEqual({result["case_kind"] for result in results}, set(self.cases))
        self.assertEqual(summary["reference_only_count"], 8)
        self.assertEqual(summary["pass_count"], 0)
        self.assertEqual(summary["fail_count"], 0)
        self.assertEqual(summary["not_evaluated_count"], 0)
        self.assertTrue(summary["all_required_cases_evaluated"])
        self.assertFalse(summary["all_passed"])
        self.assertEqual(summary["proof_drift_count"], 0)
        self.assertEqual(summary["evidence_retention_ratio"], 1.0)
        for result in results:
            with self.subTest(case=result["case_kind"]):
                self.assertEqual(result["replay_status"], "reference_only")
                self.assertFalse(result["replay_pass"])
                self.assertTrue(result["reference_only"])
                self.assertTrue(result["diagnosis_preserved"])
                self.assertTrue(result["case_id_retained"])
                self.assertTrue(result["root_cause_retained"])
                self.assertTrue(result["affected_entity_retained"])
                self.assertTrue(result["closure_retained"])
                self.assertTrue(result["deterministic_signal_retained"])
                self.assertEqual(result["evidence_retention_ratio"], 1.0)
                self.assertEqual(result["proof_drift_count"], 0)
                self.assertEqual(result["claim_class"], "report_only")

    def test_status_semantics_cover_enum_pass_fail_reference_only_and_not_evaluated(self) -> None:
        reference = run_replay_case(self.cases["priority_inversion"], self.companion_root)
        bounded_case = copy.deepcopy(self.cases["priority_inversion"])
        bounded_case["expected_closure_mode"] = "bounded"
        passed = run_replay_case(bounded_case, self.companion_root)
        representation = build_bounded_representation(self.cases["priority_inversion"], self.companion_root)
        representation["source_record_checksum"] = "invalid"
        failed = run_replay_case(self.cases["priority_inversion"], self.companion_root, representation=representation)
        with tempfile.TemporaryDirectory() as tmp_dir:
            not_evaluated = run_replay_case(self.cases["priority_inversion"], tmp_dir)
        self.assertEqual(reference["replay_status"], "reference_only")
        self.assertEqual(passed["replay_status"], "pass")
        self.assertTrue(passed["replay_pass"])
        self.assertEqual(failed["replay_status"], "fail")
        self.assertFalse(failed["replay_pass"])
        self.assertEqual(not_evaluated["replay_status"], "not_evaluated")
        self.assertFalse(not_evaluated["replay_pass"])
        self.assertEqual({row["replay_status"] for row in (reference, passed, failed, not_evaluated)}, REPLAY_STATUSES)

    def test_negative_fixture_kinds_are_explicit_reference_only_results(self) -> None:
        for case_kind in ("corrupt_segment", "stale_sidecar", "missing_calibration"):
            with self.subTest(case=case_kind):
                result = run_replay_case(self.cases[case_kind], self.companion_root)
                self.assertEqual(result["replay_status"], "reference_only")
                self.assertTrue(result["diagnosis_preserved"])
                self.assertFalse(result["replay_pass"])

    def test_bounded_representation_keeps_only_required_signals(self) -> None:
        for case_kind, case in self.cases.items():
            with self.subTest(case=case_kind):
                representation = build_bounded_representation(case, self.companion_root)
                records = representation["source_records"]
                self.assertEqual(len(records["baseline_trace"]), 1)
                self.assertIn("marker=baseline_", records["baseline_trace"][0])
                self.assertEqual(len(records["candidate_trace"]), 1)
                self.assertIn("marker=candidate_", records["candidate_trace"][0])
                if case_kind in {"corrupt_segment", "stale_sidecar", "missing_calibration"}:
                    self.assertEqual(len(records["required_companion"]), 1)
                    self.assertIn("status=", records["required_companion"][0])
                self.assertLess(_bounded_record_count(records), _full_record_count(case_kind))

    def test_removing_or_tampering_negative_case_companion_fails_replay(self) -> None:
        for case_kind in ("corrupt_segment", "stale_sidecar", "missing_calibration"):
            with self.subTest(case=case_kind, mutation="remove"):
                representation = build_bounded_representation(self.cases[case_kind], self.companion_root)
                representation["source_records"].pop("required_companion")
                representation["source_record_checksum"] = _records_checksum(representation["source_records"])
                result = run_replay_case(self.cases[case_kind], self.companion_root, representation=representation)
                self.assertEqual(result["replay_status"], "fail")
                self.assertIn("case_predicate_not_satisfied", result["failure_reasons"])
            with self.subTest(case=case_kind, mutation="tamper"):
                representation = build_bounded_representation(self.cases[case_kind], self.companion_root)
                representation["source_records"]["required_companion"] = ["status=unrelated"]
                representation["source_record_checksum"] = _records_checksum(representation["source_records"])
                result = run_replay_case(self.cases[case_kind], self.companion_root, representation=representation)
                self.assertEqual(result["replay_status"], "fail")
                self.assertIn("case_predicate_not_satisfied", result["failure_reasons"])

    def test_missing_evidence_or_bad_checksum_fails_closed(self) -> None:
        representation = build_bounded_representation(self.cases["queue_wait_backlog"], self.companion_root)
        representation["evidence_refs"] = representation["evidence_refs"][:1]
        missing_ref = run_replay_case(self.cases["queue_wait_backlog"], self.companion_root, representation=representation)
        self.assertEqual(missing_ref["replay_status"], "fail")
        self.assertLess(missing_ref["evidence_retention_ratio"], 1.0)
        broken_checksum = build_bounded_representation(self.cases["queue_wait_backlog"], self.companion_root)
        broken_checksum["source_record_checksum"] = "not-a-checksum"
        result = run_replay_case(self.cases["queue_wait_backlog"], self.companion_root, representation=broken_checksum)
        self.assertEqual(result["replay_status"], "fail")
        self.assertFalse(result["replay_pass"])
        self.assertIn("representation_invalid:source_record_checksum", result["failure_reasons"])

    def test_case_identity_mismatch_fails_closed(self) -> None:
        representation = build_bounded_representation(self.cases["priority_inversion"], self.companion_root)
        representation["case_id"] = "another_synthetic_case"
        result = run_replay_case(self.cases["priority_inversion"], self.companion_root, representation=representation)
        self.assertEqual(result["replay_status"], "fail")
        self.assertFalse(result["replay_pass"])
        self.assertFalse(result["case_id_retained"])

    def test_drift_is_not_returned_and_fails_closed(self) -> None:
        result = run_replay_case(
            self.cases["irq_latency_spike"],
            self.companion_root,
            full_proof_facts=[{"stable_fact": "a"}],
            replay_proof_facts=[{"stable_fact": "b"}],
        )
        self.assertGreater(result["proof_drift_count"], 0)
        self.assertEqual(result["replay_status"], "fail")
        self.assertFalse(result["replay_pass"])
        self.assertNotIn("stable_fact", json.dumps(result, sort_keys=True))

    def test_results_exclude_forbidden_fields_and_p6_2_files_are_unchanged(self) -> None:
        before = _fixture_hashes()
        results, _ = run_replay_suite(self.suite, self.companion_root)
        for result in results:
            _assert_no_forbidden_fields(result)
        self.assertEqual(before, _fixture_hashes())

    def test_cli_writes_summary_without_mutating_fixture(self) -> None:
        before = _fixture_hashes()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "replay.json"
            completed = subprocess.run(
                [
                    "python3",
                    "tool/run_rtos_diagnosis_replay.py",
                    "--suite",
                    str(SUITE_PATH),
                    "--output",
                    str(output),
                    "--summary",
                    "--overwrite",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(summary, payload["summary"])
        self.assertEqual(summary["reference_only_count"], 8)
        self.assertEqual(summary["proof_drift_count"], 0)
        self.assertEqual(before, _fixture_hashes())


def _fixture_hashes() -> dict[str, str]:
    paths = [SUITE_PATH, *sorted(path for path in companion_root_for_suite(SUITE_PATH).rglob("*") if path.is_file())]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _full_record_count(case_kind: str) -> int:
    root = companion_root_for_suite(SUITE_PATH) / case_kind
    count = sum(
        len([line for line in (root / trace_name).read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")])
        for trace_name in ("baseline.trace", "candidate.trace")
    )
    companion_paths = {
        "corrupt_segment": "segments/segment_0007.txt",
        "stale_sidecar": "sidecar/candidate_index.txt",
        "missing_calibration": "notes/calibration_status.txt",
    }
    if case_kind in companion_paths:
        count += len([line for line in (root / companion_paths[case_kind]).read_text(encoding="utf-8").splitlines() if line])
    return count


def _bounded_record_count(records: dict[str, list[str]]) -> int:
    return sum(len(rows) for rows in records.values())


def _records_checksum(records: dict[str, list[str]]) -> str:
    payload = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_no_forbidden_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_FIELD_NAMES:
                raise AssertionError(f"forbidden field {key}")
            _assert_no_forbidden_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_fields(child)


if __name__ == "__main__":
    unittest.main()

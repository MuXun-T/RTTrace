from __future__ import annotations

import copy
from pathlib import Path
import json
import unittest

from parser.external_replay_equivalence_validator import reproduce_and_compare_p7_4
from parser.external_replay_equivalence_validator import EQUIVALENCE_FACT_FIELDS
from parser.external_layered_validation_models import ValidationProfile
from parser.external_validation_drift import SUPPORTED_REPLAY_FACT_FIELDS, analyze_replay_fact_drift, replay_fact_digest


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ValidationProfile.from_dict(json.loads((ROOT / "tests/python/fixtures/external_validation/layered_validation/profiles/p7.5-layered-validation-v1.json").read_bytes()))


class ExternalValidationDriftTests(unittest.TestCase):
    def test_reproduced_cases_have_zero_drift_in_the_frozen_domain(self) -> None:
        for case_id in ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k", "zephyr", "zephelin"):
            with self.subTest(case_id=case_id):
                result = reproduce_and_compare_p7_4(case_id)
                drift = analyze_replay_fact_drift(result.frozen_report, result.reproduced_report, PROFILE)
                self.assertEqual(drift.comparable_replay_fact_count, len(SUPPORTED_REPLAY_FACT_FIELDS))
                self.assertEqual(drift.replay_fact_drift_count, 0)
                self.assertEqual(drift.replay_fact_drift_items, ())
                self.assertEqual(replay_fact_digest(result.frozen_report, PROFILE), replay_fact_digest(result.reproduced_report, PROFILE))

    def test_state_reason_and_reason_order_drift_are_deterministic(self) -> None:
        frozen = reproduce_and_compare_p7_4("freertos_btf_1core").frozen_report
        changed = copy.deepcopy(frozen)
        changed["replay_state"] = "replay_fail"
        changed["primary_reason"] = "TIMESTAMP_REGRESSION"
        changed["reason_codes"] = ["TIMESTAMP_REGRESSION", "PARSE_ERROR"]
        drift = analyze_replay_fact_drift(frozen, changed, PROFILE)
        self.assertEqual(drift.replay_fact_drift_count, 3)
        self.assertEqual([item.fact_id for item in drift.replay_fact_drift_items], ["primary_reason", "reason_codes", "replay_state"])
        self.assertNotEqual(replay_fact_digest(frozen, PROFILE), replay_fact_digest(changed, PROFILE))

    def test_profile_covers_every_equivalence_fact_including_nested_replay_results(self) -> None:
        self.assertEqual(SUPPORTED_REPLAY_FACT_FIELDS, EQUIVALENCE_FACT_FIELDS)
        frozen = reproduce_and_compare_p7_4("freertos_btf_1core").frozen_report
        changed = copy.deepcopy(frozen)
        changed["actual_output"]["normalized_event_count"] = 0
        changed["comparison_completed"] = False
        changed["normalized_event_count"] = 0
        changed["invariants"] = [{"invariant_id": "changed", "passed": False, "event_index": 0, "reason": "TIMESTAMP_REGRESSION"}]
        changed["timestamp_regressions"] = [{"source_record_index": 0, "physical_line": 5, "previous_timestamp": 1, "current_timestamp": 0, "cpu_id": None, "reason": "TIMESTAMP_REGRESSION"}]
        drift = analyze_replay_fact_drift(frozen, changed, PROFILE)
        self.assertEqual([item.fact_id for item in drift.replay_fact_drift_items], ["actual_output", "comparison_completed", "invariants", "normalized_event_count", "timestamp_regressions"])

    def test_report_only_and_unsupported_profile_fields_do_not_expand_drift(self) -> None:
        frozen = reproduce_and_compare_p7_4("freertos_btf_1core").frozen_report
        changed = copy.deepcopy(frozen); changed["elapsed_ms"] = 999
        self.assertEqual(analyze_replay_fact_drift(frozen, changed, PROFILE).replay_fact_drift_count, 0)
        profile = copy.deepcopy(PROFILE.to_dict()); profile["replay_fact_fields"] = list(PROFILE.replay_fact_fields) + ["elapsed_ms"]
        profile["profile_identity"] = "0" * 64
        with self.assertRaises(ValueError):
            ValidationProfile.from_dict(profile)


if __name__ == "__main__":
    unittest.main()

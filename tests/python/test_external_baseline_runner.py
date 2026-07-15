from __future__ import annotations

import json

import pytest

from parser.external_baseline_runner import canonical_report_bytes


GATES = (
    "same_raw_trace_sha256",
    "same_case_workload",
    "same_input_scope",
    "matched_truth_boundary",
    "same_or_approved_equivalent_environment",
    "same_metric_unit_statistics",
    "same_failure_exclusion_policy",
)


def _record() -> dict[str, object]:
    evidence = {"value": None, "reason": "synthetic_contract"}
    return {
        "external_baseline_comparison_version": "p7.7-external-baseline-v1",
        "inventory": [{"baseline_id": "synthetic_contract", "category": "custom_full_trace", "candidate_status": "not_evaluated", "source": evidence, "version": evidence, "license": evidence, "availability": evidence, "capabilities": []}],
        "gates": [{"name": name, "status": "pass", "reason": None} for name in GATES],
        "comparison_status": "quantitative",
        "raw_results": [
            {"sample_id": "sample_1", "status": "success", "failure_reason": None, "metrics": [{"name": "elapsed", "unit": "s", "value": 2.0}]},
            {"sample_id": "sample_2", "status": "success", "failure_reason": None, "metrics": [{"name": "elapsed", "unit": "s", "value": 4.0}]},
            {"sample_id": "sample_3", "status": "failure", "failure_reason": "execution_failed", "metrics": []},
        ],
        "summaries": [{"metric_name": "elapsed", "unit": "s", "sample_count": 2, "median": 3.0, "mad": 1.0, "minimum": 2.0, "maximum": 4.0, "failure_count": 1}],
    }


def test_runner_preserves_raw_failures_and_repeated_canonical_bytes():
    data = canonical_report_bytes(_record())
    assert data == canonical_report_bytes(_record())
    value = json.loads(data)
    assert value["raw_results"][-1]["status"] == "failure"
    assert value["raw_results"][-1]["failure_reason"] == "execution_failed"


def test_runner_rejects_nonpass_gate_summary_and_inconsistent_summary():
    nonpass = _record()
    nonpass["gates"][3] = {"name": GATES[3], "status": "nonpass", "reason": "mismatch"}  # type: ignore[index]
    nonpass["comparison_status"] = "not_comparable"
    with pytest.raises(ValueError):
        canonical_report_bytes(nonpass)
    inconsistent = _record()
    inconsistent["summaries"][0]["mad"] = 0.0  # type: ignore[index]
    with pytest.raises(ValueError):
        canonical_report_bytes(inconsistent)

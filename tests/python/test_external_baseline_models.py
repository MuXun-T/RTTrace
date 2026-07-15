from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from parser.external_baseline_models import BaselineComparisonReport, Capability, EvidenceValue, MetricSample
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/python/fixtures/external_validation/baseline/contract/valid_capability_only.json"
GATES = (
    "same_raw_trace_sha256",
    "same_case_workload",
    "same_input_scope",
    "matched_truth_boundary",
    "same_or_approved_equivalent_environment",
    "same_metric_unit_statistics",
    "same_failure_exclusion_policy",
)


def _inventory() -> dict[str, object]:
    evidence = {"value": None, "reason": "unverified_inventory"}
    return {"baseline_id": "synthetic_contract", "category": "custom_full_trace", "candidate_status": "not_evaluated", "source": evidence, "version": evidence, "license": evidence, "availability": evidence, "capabilities": [{"name": "metric", "status": "not_evaluated", "reason": "unverified_inventory"}]}


def _quantitative() -> dict[str, object]:
    return {
        "external_baseline_comparison_version": "p7.7-external-baseline-v1",
        "inventory": [_inventory()],
        "gates": [{"name": name, "status": "pass", "reason": None} for name in GATES],
        "comparison_status": "quantitative",
        "raw_results": [
            {"sample_id": "sample_1", "status": "success", "failure_reason": None, "metrics": [{"name": "elapsed", "unit": "s", "value": 2.0}]},
            {"sample_id": "sample_2", "status": "success", "failure_reason": None, "metrics": [{"name": "elapsed", "unit": "s", "value": 4.0}]},
            {"sample_id": "sample_3", "status": "failure", "failure_reason": "execution_failed", "metrics": []},
        ],
        "summaries": [{"metric_name": "elapsed", "unit": "s", "sample_count": 2, "median": 3.0, "mad": 1.0, "minimum": 2.0, "maximum": 4.0, "failure_count": 1}],
    }


def test_fixture_schema_mirror_and_capability_only_inventory_are_valid():
    name = "external_baseline_comparison.schema.json"
    assert (ROOT / "spec/schema" / name).read_bytes() == (ROOT / "spec/assets/schema" / name).read_bytes()
    value = json.loads(FIXTURE.read_bytes())
    assert validate_schema(load_schema(name), value) is None
    report = BaselineComparisonReport.from_dict(value)
    assert report.comparison_status == "capability_only"
    assert len(report.inventory) == 6
    assert all(row.candidate_status == "not_evaluated" for row in report.inventory)
    assert not report.raw_results and not report.summaries


def test_capability_states_are_closed_and_separate_from_comparison_status():
    for status in ("supported", "unsupported", "not_applicable", "not_evaluated"):
        reason = None if status == "supported" else "contract_reason"
        assert Capability("capability", status, reason).status == status
    with pytest.raises(ValueError):
        Capability("capability", "unknown", "contract_reason")


@pytest.mark.parametrize("index", (3, 4, 5))
def test_truth_environment_or_metric_nonpass_is_not_comparable_without_summary(index: int):
    value = _quantitative()
    value["gates"][index] = {"name": GATES[index], "status": "nonpass", "reason": "mismatch"}  # type: ignore[index]
    value["comparison_status"] = "not_comparable"
    value["summaries"] = []
    report = BaselineComparisonReport.from_dict(value)
    assert report.comparison_status == "not_comparable"
    assert not report.summaries
    value["summaries"] = _quantitative()["summaries"]
    with pytest.raises(ValueError):
        BaselineComparisonReport.from_dict(value)


def test_no_zero_fill_raw_failure_retention_and_complete_summary_counts():
    value = _quantitative()
    report = BaselineComparisonReport.from_dict(value)
    assert report.to_dict()["raw_results"][-1]["failure_reason"] == "execution_failed"  # type: ignore[index]
    value["inventory"][0]["availability"] = {"value": 0, "reason": None}  # type: ignore[index]
    with pytest.raises(ValueError):
        BaselineComparisonReport.from_dict(value)
    with pytest.raises(ValueError):
        EvidenceValue(None, None)
    with pytest.raises(ValueError):
        MetricSample("elapsed", "s", float("nan"))


@pytest.mark.parametrize("field, replacement", (("median", 2.0), ("mad", 0.0), ("minimum", 1.0), ("maximum", 5.0)))
def test_summary_statistics_must_exactly_match_retained_raw_samples(field: str, replacement: float):
    value = _quantitative()
    value["summaries"][0][field] = replacement  # type: ignore[index]
    with pytest.raises(ValueError):
        BaselineComparisonReport.from_dict(value)


def test_closed_fields_deterministic_serialization_and_leak_rejection():
    value = _quantitative()
    value["extra"] = 0
    with pytest.raises(ValueError):
        BaselineComparisonReport.from_dict(value)
    report = BaselineComparisonReport.from_dict(_quantitative())
    assert report.canonical_bytes() == report.canonical_bytes()
    assert report.canonical_bytes().endswith(b"\n") and report.canonical_bytes().isascii()
    leaked = deepcopy(_quantitative())
    leaked["raw_results"][2]["failure_reason"] = "tmp_directory"  # type: ignore[index]
    with pytest.raises(ValueError):
        BaselineComparisonReport.from_dict(leaked)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser.external_benchmark_models import BenchmarkEvidence, BenchmarkSample, metric
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/python/fixtures/external_validation/benchmark/contract"
EMPTY_IDENTITY = "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356"


def test_contract_schema_mirror_and_empty_evidence_are_deterministic():
    name = "external_benchmark_evidence.schema.json"
    assert (ROOT / "spec/schema" / name).read_bytes() == (ROOT / "spec/assets/schema" / name).read_bytes()
    value = json.loads((FIXTURE / "valid_empty.json").read_bytes())
    assert validate_schema(load_schema(name), value) is None
    evidence = BenchmarkEvidence.from_dict(value)
    assert evidence.canonical_bytes() == evidence.canonical_bytes()
    assert evidence.canonical_sha256() == evidence.canonical_sha256()


def test_acquisition_and_unavailable_metrics_fail_closed():
    value = json.loads((FIXTURE / "invalid_unavailable_zero.json").read_bytes())
    assert validate_schema(load_schema("external_benchmark_evidence.schema.json"), value) is not None
    with pytest.raises(ValueError):
        BenchmarkEvidence.from_dict(value)
    with pytest.raises(ValueError):
        metric("unavailable", 0, "s", "no_timer")


def test_sample_rejects_duplicate_or_zero_filled_unavailable_metrics():
    with pytest.raises(ValueError):
        BenchmarkSample("x", "freertos_btf_1core", "a" * 64, 1, 1, "measured", None, "b" * 64, "workload", EMPTY_IDENTITY, "process_cold", False, 0, "success", None, (("unavailable", metric("unavailable", 0, "s", "no_timer")),))

from __future__ import annotations

from parser.external_benchmark_models import BenchmarkEvidence
from parser.external_benchmark_runner import run_benchmark
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


def test_runner_retains_warmup_repeat_modes_and_unsupported_metrics():
    evidence = run_benchmark(repeats=1, warmups=1, cases=("freertos_btf_1core",))
    value = evidence.to_dict()
    assert validate_schema(load_schema("external_benchmark_evidence.schema.json"), value) is None
    assert len(value["samples"]) == 4
    assert {sample["mode"] for sample in value["samples"]} == {"process_cold", "same_process_warm"}
    assert sum(sample["warmup"] for sample in value["samples"]) == 2
    assert value["acquisition_overhead"] == {"status": "not_evaluated", "reason": "missing_board_or_source_identical_firmware"}
    for sample in value["samples"]:
        assert sample["metrics"]["export_elapsed_s"]["value"] is None
        assert sample["metrics"]["export_elapsed_s"]["status"] == "unsupported"
    value["samples"][0]["metrics"]["export_elapsed_s"]["value"] = 0
    assert validate_schema(load_schema("external_benchmark_evidence.schema.json"), value) is not None


def test_canonicalization_is_byte_stable_for_identical_raw_samples():
    evidence = run_benchmark(repeats=1, warmups=0, cases=("freertos_btf_1core",))
    parsed = BenchmarkEvidence.from_dict(evidence.to_dict())
    assert parsed.canonical_bytes() == evidence.canonical_bytes()
    assert parsed.canonical_sha256() == evidence.canonical_sha256()


def test_formal_replay_failure_retains_unavailable_event_count_without_zero_fill():
    evidence = run_benchmark(repeats=1, warmups=0, cases=("freertos_vcd_1core",))
    for sample in evidence.to_dict()["samples"]:
        assert sample["status"] == "success"
        assert sample["event_count"] is None
        assert sample["event_count_status"] == "unavailable"
        assert sample["event_count_reason"] == "replay_event_count_unavailable"

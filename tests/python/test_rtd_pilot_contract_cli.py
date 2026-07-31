from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from test_rtd_pilot_contracts import bundle


ROOT = Path(__file__).resolve().parents[2]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True, check=check)


def test_append_seal_and_rewrite_detection(tmp_path: Path) -> None:
    contract = bundle()
    row = deepcopy(contract["ledger"][0])
    record = tmp_path / "record.json"
    ledger = tmp_path / "ledger.jsonl"
    seal = tmp_path / "ledger.seal.json"
    record.write_text(json.dumps(row), encoding="utf-8")
    append = run("tool/rtd_append_ledger.py", "--ledger", str(ledger), "--record", str(record), "--seal", str(seal))
    assert json.loads(append.stdout)["append_sequence"] == 1
    seal_result = run("tool/rtd_seal_ledger.py", "--ledger", str(ledger), "--seal", str(seal), "--timestamp", "2026-07-30T00:00:00Z", "--seal-record-id", "seal:one")
    assert json.loads(seal_result.stdout)["capture_count"] == 1
    assert json.loads(run("tool/rtd_validate_ledger.py", "--ledger", str(ledger), "--seal", str(seal)).stdout) == {"records": 1, "valid": True}
    assert run("tool/rtd_append_ledger.py", "--ledger", str(ledger), "--record", str(record), "--seal", str(seal), check=False).returncode != 0
    ledger.write_text(ledger.read_text(encoding="utf-8").replace("healthy_control", "near_miss"), encoding="utf-8")
    failed = run("tool/rtd_validate_ledger.py", "--ledger", str(ledger), "--seal", str(seal), check=False)
    assert failed.returncode != 0
    assert "record_digest mismatch" in failed.stderr


def test_ledger_cli_enforces_linear_prior_history(tmp_path: Path) -> None:
    contract = bundle()
    first_input = tmp_path / "first.json"
    second_input = tmp_path / "second.json"
    invalid_input = tmp_path / "invalid.json"
    ledger = tmp_path / "ledger.jsonl"
    seal = tmp_path / "ledger.seal.json"
    first_input.write_text(json.dumps(contract["ledger"][0]), encoding="utf-8")
    first = json.loads(run("tool/rtd_append_ledger.py", "--ledger", str(ledger), "--record", str(first_input), "--seal", str(seal)).stdout)

    second = deepcopy(contract["ledger"][0])
    second["ledger_record_id"] = "ledger:two"
    second["prior_ledger_ref"] = first["record_digest"]
    second_input.write_text(json.dumps(second), encoding="utf-8")
    appended = json.loads(run("tool/rtd_append_ledger.py", "--ledger", str(ledger), "--record", str(second_input), "--seal", str(seal)).stdout)
    assert appended["append_sequence"] == 2

    invalid = deepcopy(contract["ledger"][0])
    invalid["ledger_record_id"] = "ledger:three"
    invalid["prior_ledger_ref"] = None
    invalid_input.write_text(json.dumps(invalid), encoding="utf-8")
    rejected = run("tool/rtd_append_ledger.py", "--ledger", str(ledger), "--record", str(invalid_input), "--seal", str(seal), check=False)
    assert rejected.returncode != 0
    assert "one current/latest" in rejected.stderr

    sealed = run("tool/rtd_seal_ledger.py", "--ledger", str(ledger), "--seal", str(seal), "--timestamp", "2026-07-30T00:00:00Z", "--seal-record-id", "seal:one")
    assert json.loads(sealed.stdout)["capture_count"] == 2
    assert json.loads(run("tool/rtd_validate_ledger.py", "--ledger", str(ledger), "--seal", str(seal)).stdout) == {"records": 2, "valid": True}


def test_capture_bundle_cli_and_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bundle.json"
    contract = bundle()
    path.write_text(json.dumps(contract), encoding="utf-8")
    result = run("tool/rtd_validate_capture_bundle.py", "--bundle", str(path), "--scoring-eligible")
    assert json.loads(result.stdout)["capture_status"] == {"capture:one": "valid"}
    contract["inventory"][0]["artifacts"][0]["hash"] = "a" * 64
    path.write_text(json.dumps(contract), encoding="utf-8")
    failed = run("tool/rtd_validate_capture_bundle.py", "--bundle", str(path), check=False)
    assert failed.returncode != 0
    assert "record_digest mismatch" in failed.stderr


def test_collector_adapter_never_overwrites(tmp_path: Path) -> None:
    source = {
        "event_types_enabled": ["TASK_SWITCH"], "task_filter": [], "resource_filter": [], "irq_filter": [], "core_filter": ["0"],
        "sampling_configuration": {"enabled": False}, "buffer_capacity": 64, "flush_policy": "drop_new", "timestamp_source": "counter",
        "clock_resolution": 1, "payload_fields": ["task_id"], "dictionary_version": "v1", "mapping_version": "v1", "collector_version": "v1",
        "trace_start_boundary": "case_start", "trace_end_boundary": "case_end",
    }
    input_path = tmp_path / "collector.json"
    output_path = tmp_path / "snapshot.json"
    input_path.write_text(json.dumps(source), encoding="utf-8")
    first = run("tool/rtd_adapt_collector_config.py", "--input", str(input_path), "--output", str(output_path))
    assert "capture_id" not in json.loads(first.stdout)
    second = run("tool/rtd_adapt_collector_config.py", "--input", str(input_path), "--output", str(output_path), check=False)
    assert second.returncode != 0
    assert "File exists" in second.stderr

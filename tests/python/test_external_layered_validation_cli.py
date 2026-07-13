from __future__ import annotations

from pathlib import Path
import tempfile

from tool.run_external_layered_validation import main


ROOT = Path(__file__).resolve().parents[2]


def test_cli_is_canonical_stable_and_preserves_failures_and_reference_only():
    expected = (("freertos_btf_1core", 0), ("freertos_vcd_1core", 0), ("freertos_btf_4cores", 0), ("freertos_btf_50k", 0), ("zephyr", 2), ("zephelin", 2))
    for case_id, code in expected:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            assert main(["--case", case_id, "--output", str(first)]) == code
            assert main(["--case", case_id, "--output", str(second)]) == code
            assert first.read_bytes() == second.read_bytes()


def test_cli_rejects_usage_and_repository_output():
    assert main([]) == 2
    assert main(["--case", "freertos_btf_1core", "--output", str(ROOT / "report.json")]) == 70

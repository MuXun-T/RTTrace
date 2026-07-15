from __future__ import annotations

import json
from pathlib import Path
import tempfile

from parser.external_baseline_models import BaselineComparisonReport
from tool.run_external_baseline_comparison import main


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/python/fixtures/external_validation/baseline/contract/valid_capability_only.json"


def test_cli_writes_capability_only_report_create_exclusively_and_without_leaks():
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "comparison.json"
        assert main(["--input", str(FIXTURE), "--output", str(output)]) == 0
        assert BaselineComparisonReport.from_dict(json.loads(output.read_bytes())).canonical_bytes() == output.read_bytes()
        text = output.read_text(encoding="ascii")
        assert all(value not in text for value in (str(ROOT), "/tmp", "pid", "hostname", "timestamp", "username"))
        assert main(["--input", str(FIXTURE), "--output", str(output)]) == 70


def test_cli_rejects_usage_repository_output_and_invalid_input():
    assert main([]) == 64
    repository_output = ROOT / "tmp" / "p7_7_comparison.json"
    repository_output.unlink(missing_ok=True)
    assert main(["--input", str(FIXTURE), "--output", str(repository_output)]) == 70
    assert not repository_output.exists()
    with tempfile.TemporaryDirectory() as directory:
        invalid = Path(directory) / "invalid.json"
        invalid.write_text("{\"invalid\":0}", encoding="ascii")
        assert main(["--input", str(invalid), "--output", str(Path(directory) / "comparison.json")]) == 70

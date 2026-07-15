from __future__ import annotations

from pathlib import Path
import tempfile

from parser.external_benchmark_models import BenchmarkEvidence
from tool.run_external_benchmark import main


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cli_writes_create_exclusive_canonical_evidence_outside_repository():
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "evidence.json"
        assert main(["--case", "freertos_btf_1core", "--repeats", "1", "--warmups", "0", "--output", str(output)]) == 0
        assert BenchmarkEvidence.from_dict(__import__("json").loads(output.read_bytes())).canonical_bytes() == output.read_bytes()
        assert main(["--case", "freertos_btf_1core", "--repeats", "1", "--warmups", "0", "--output", str(output)]) == 70


def test_cli_rejects_invalid_repeat_count():
    with tempfile.TemporaryDirectory() as directory:
        assert main(["--repeats", "0", "--output", str(Path(directory) / "evidence.json")]) == 70


def test_cli_rejects_repository_output_path():
    output = REPO_ROOT / "tmp" / "p7_6_benchmark_evidence.json"
    output.unlink(missing_ok=True)
    assert main(["--case", "freertos_btf_1core", "--repeats", "1", "--warmups", "0", "--output", str(output)]) == 70
    assert not output.exists()

"""Focused contract checks for the deterministic Phase 6 closeout manifest."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tool" / "build_phase6_closeout_manifest.py"
FIXTURE = ROOT / "tests/python/fixtures/rtos_diagnosis/closeout/phase6_closeout_manifest.json"
spec = importlib.util.spec_from_file_location("phase6_closeout", TOOL)
assert spec and spec.loader
closeout = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = closeout
spec.loader.exec_module(closeout)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_committed_manifest_is_complete_and_valid() -> None:
    manifest = _fixture()
    closeout.validate_manifest(manifest)
    assert manifest["phase"] == 6
    assert manifest["pre_closeout_head"] == "d844474"
    assert [entry["commit"] for entry in manifest["frozen_commits"]] == [
        "7f57ec5", "d381a8c", "c187b41", "278e7ee", "ad8e7ab", "35b1505", "d844474"]
    assert all(entry["frozen"] for entry in manifest["frozen_commits"])
    assert len(manifest["schema_mirror_checks"]) == 6
    assert manifest["canonical_artifacts"]["p6_4_report"]["canonical_hash"] == closeout.P6_4_REPORT_HASH
    assert manifest["invariants"]["replay"] == {
        "replay_pass_count": 0, "replay_fail_count": 0, "reference_only_count": 8, "all_replay_passed": False}
    assert manifest["invariants"]["report"] == {
        "claimable_count": 0, "report_only_count": 8, "proof_drift_count": 0, "real_hardware_case_count": 0}
    assert manifest["invariants"]["human_feedback"]["real_participant_records_count"] == 0
    assert manifest["invariants"]["human_feedback"]["pipeline_validation_only"] is True
    assert all(value is False for value in manifest["claim_boundary"].values())
    assert manifest["unresolved_validation_gaps"]
    assert manifest["release_readiness"]["empirical_systems_evidence"] != "ready"


def test_manifest_paths_and_forbidden_content() -> None:
    manifest = _fixture()
    paths = [entry["path"] for group in manifest["artifact_groups"].values() for entry in group]
    assert paths and all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in paths)
    assert all((ROOT / path).exists() for path in paths)
    rendered = closeout.canonical_json(manifest).lower()
    for forbidden in ("/media/", "../", "api_key", "secret", "token", "proof_digest_write_path", "correctness_ranking"):
        assert forbidden not in rendered


def test_builder_is_deterministic_and_matches_fixture() -> None:
    first = closeout.build_manifest(test_passed=682, subtests_passed=950)
    second = closeout.build_manifest(test_passed=682, subtests_passed=950)
    assert closeout.canonical_json(first) == closeout.canonical_json(second)
    assert first == _fixture()
    assert "timestamp" not in first


def test_builder_rejects_participant_like_manifest_id() -> None:
    try:
        closeout.build_manifest(manifest_id="participant@example.com")
    except ValueError as error:
        assert str(error) == "manifest_id must be an ASCII slug"
    else:
        raise AssertionError("participant-like manifest ID was accepted")


def test_cli_smoke(tmp_path: Path) -> None:
    output = tmp_path / "closeout.json"
    result = subprocess.run(
        [sys.executable, str(TOOL), "--output", str(output), "--summary", "--overwrite",
         "--manifest-id", "phase6-p6-7-closeout", "--test-passed", "682", "--subtests-passed", "950"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    assert json.loads(result.stdout)["canonical_hash"] == closeout.P6_4_REPORT_HASH
    assert output.read_text(encoding="utf-8") == closeout.canonical_json(_fixture())

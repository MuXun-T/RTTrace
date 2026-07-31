from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCORECARD = ROOT / "docs/rtd_pilot/feasibility/phase1_h3_scorecard_v2.json"
FINAL_SCORECARD = ROOT / "docs/rtd_pilot/feasibility/phase1_h3_scorecard_v3.json"
SPEC = importlib.util.spec_from_file_location("phase1_h3_verifier", ROOT / "tool/rtd_phase1_verify_h3_corrective.py")
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def _scorecard() -> dict:
    return json.loads(SCORECARD.read_text(encoding="utf-8"))


def _verify(tmp_path: Path, value: dict) -> list[str]:
    path = tmp_path / "scorecard.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return VERIFIER.verify(path, ROOT)["errors"]


def test_real_corrective_scorecard_fails_only_the_h3_gate() -> None:
    errors = VERIFIER.verify(SCORECARD, ROOT)["errors"]
    assert "mandatory H3 score must be 2: hardware_timer=1" in errors
    assert not any("PB0" in error for error in errors)


def test_final_v3_scorecard_closes_only_the_h3_alignment_gate() -> None:
    assert VERIFIER.verify(FINAL_SCORECARD, ROOT)["errors"] == []


@pytest.mark.parametrize("mutator, expected", [
    (lambda value: value["items"][4].update(decision="PASS"), "PASS does not replace numeric_score=2: hardware_timer"),
    (lambda value: value["items"][0].update(evidence_refs=[]), "physical_availability lacks evidence refs"),
    (lambda value: value["items"][0].update(numeric_score="2"), "physical_availability numeric_score must be integer 0, 1, or 2"),
    (lambda value: value["pin_map"].update(semantic_prose="PB0=green LED"), "PB0 prose conflicts with corrective machine map"),
    (lambda value: value["marker_semantics"].pop("F3"), "F3 marker semantic set is incomplete"),
    (lambda value: value["uart_transport"].update(evidence_refs=[]), "UART transport evidence is absent"),
    (lambda value: [item.update(reviewer="SELF_REVIEW") for item in value["items"]], "physical_availability has self-review-only semantic review"),
])
def test_corrective_verifier_rejects_required_negative_cases(tmp_path: Path, mutator: object, expected: str) -> None:
    value = deepcopy(_scorecard())
    mutator(value)
    assert expected in _verify(tmp_path, value)

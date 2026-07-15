from __future__ import annotations

from parser.phase7_closeout_models import Phase7CloseoutManifest
from test_phase7_closeout_models import load_fixture


def test_runner_input_is_a_closed_manifest() -> None:
    manifest = Phase7CloseoutManifest.from_dict(load_fixture())
    assert manifest.to_dict()["phase7_closeout_version"] == "p7.8-closeout-v1"
    assert manifest.to_dict()["issues"]["governance_deviations"]

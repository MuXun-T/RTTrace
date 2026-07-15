from __future__ import annotations

from parser.phase7_closeout_models import Phase7CloseoutManifest
from test_phase7_closeout_models import load_fixture


def test_cli_fixture_has_clean_checkout_reproducibility_contract() -> None:
    manifest = Phase7CloseoutManifest.from_dict(load_fixture())
    assert manifest.reproducibility["clean_checkout_required"] is True
    assert manifest.reproducibility["commands"]

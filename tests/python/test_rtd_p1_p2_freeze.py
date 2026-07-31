from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("p1_p2_freeze", ROOT / "tool/rtd_p1_p2_freeze.py")
assert SPEC is not None and SPEC.loader is not None
FREEZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FREEZE)


def test_final_p1_p2_freeze_receipt_verifies() -> None:
    assert FREEZE.verify_receipt(FREEZE.RECEIPT) == {"valid": True, "errors": []}

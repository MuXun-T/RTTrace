import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p7gate", ROOT / "tool/rtd_p7_frozen_gate.py")
mod = importlib.util.module_from_spec(spec); assert spec.loader
spec.loader.exec_module(mod)

def test_gate_emits_blocked_append_only_chain(tmp_path):
    paths = mod.build(tmp_path / "p7")
    assert len(paths) == 4
    records = [json.loads(p.read_text()) for p in paths]
    assert records[0]["status"] == "BLOCKED"
    assert all(r["phase"] == "P7" and r["append_only"] and not r["hardware_action"] for r in records)
    assert len({r["record_id"] for r in records}) == 4
    manifest = records[-1]
    assert len(manifest["records"]) == 4
    assert all(not Path(x["path"]).is_absolute() for x in manifest["records"])
    assert all(not Path(x["path"]).is_absolute() for x in records[0]["source_refs"])

def test_gate_rejects_existing_output(tmp_path):
    out = tmp_path / "p7"; out.mkdir()
    try:
        mod.build(out)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("overwrite accepted")

def test_gate_rejects_stale_p6_hash(monkeypatch):
    key = next(iter(mod.EXPECTED)); monkeypatch.setitem(mod.EXPECTED, key, "0" * 64)
    try:
        mod.validate_inputs()
    except ValueError as exc:
        assert "stale P6 hash" in str(exc)
    else:
        raise AssertionError("stale source accepted")

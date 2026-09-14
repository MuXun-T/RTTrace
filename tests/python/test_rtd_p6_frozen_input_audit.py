import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p6audit", ROOT / "tool/rtd_p6_frozen_input_audit.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def test_frozen_audit_builds_append_only_records(tmp_path):
    out = tmp_path / "p6"
    paths = mod.build(out, ROOT / mod.P5_REL)
    assert len(paths) == 4
    records = [json.loads(path.read_text()) for path in paths]
    assert all(r["phase"] == "P6" and r["append_only"] and not r["hardware_action"] for r in records)
    assert len({r["record_id"] for r in records}) == 4
    manifest = records[-1]
    assert len(manifest["records"]) == 3
    assert all(Path(item["path"]).is_absolute() for item in manifest["source_refs"])

def test_stale_source_ref_rejected(tmp_path):
    p5 = tmp_path / "freeze"
    source = ROOT / mod.P5_REL
    p5.mkdir()
    for path in source.iterdir():
        if path.is_file(): (p5 / path.name).write_bytes(path.read_bytes())
    target = p5 / "full_p5_capture_evidence_manifest.json"
    target.write_bytes(target.read_bytes() + b" ")
    try:
        mod.validate_inputs(p5)
    except ValueError as error:
        assert "stale P5 hash" in str(error)
    else:
        raise AssertionError("stale source accepted")

def test_existing_output_rejected(tmp_path):
    out = tmp_path / "p6"; out.mkdir()
    try:
        mod.build(out, ROOT / mod.P5_REL)
    except ValueError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("overwrite accepted")

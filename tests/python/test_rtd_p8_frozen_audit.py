import json
from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('p8',ROOT/'tool/rtd_p8_frozen_audit.py'); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
def test_build_chain(tmp_path):
    files=mod.build(tmp_path/'p8'); assert len(files)==5
    rows=[json.loads(p.read_text()) for p in files]
    assert rows[0]['status']=='PASS'; assert rows[1]['status']=='BLOCKED'; assert rows[2]['status']=='PASS'; assert rows[3]['status']=='NON_HARDWARE_COMPLETE'
    assert all(r['phase']=='P8' and r['append_only'] and not r['hardware_action'] for r in rows)
    assert len({r['record_id'] for r in rows})==5
    assert all(r['source_refs'] and r['parent_record_id'] and r['lineage'] for r in rows)
    assert all(not Path(x['path']).is_absolute() for x in rows[-1]['records'])
def test_no_overwrite(tmp_path):
    out=tmp_path/'p8'; out.mkdir()
    try: mod.build(out)
    except ValueError as e: assert 'already exists' in str(e)
    else: raise AssertionError
def test_stale_hash(monkeypatch):
    monkeypatch.setitem(mod.EXPECTED,next(iter(mod.EXPECTED)),'0'*64)
    try: mod.refs()
    except ValueError as e: assert 'stale P7 hash' in str(e)
    else: raise AssertionError

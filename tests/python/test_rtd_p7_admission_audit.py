import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
def test_canonical_p5_manifest_has_expected_shape():
    p = ROOT / 'docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z/full_p5_capture_evidence_manifest.json'
    d = json.loads(p.read_text())
    assert len(d['captures']) == 63
    assert len({c['registry_case_id'] for c in d['captures']}) == 21
    assert all(c['raw_files'] for c in d['captures'])

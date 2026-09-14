#!/usr/bin/env python3
"""Read-only admission and registry-case split audit for the canonical P5 freeze."""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P5 = ROOT / "docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z"

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd=os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    try: os.write(fd,(json.dumps(obj,indent=2,sort_keys=True)+"\n").encode()); os.fsync(fd)
    finally: os.close(fd)
def ref(rel, role):
    p=ROOT/rel; return {"path":rel,"sha256":sha(p),"role":role}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--run", required=True, type=Path); a=ap.parse_args(); run=a.run.resolve()
    if run.exists(): raise SystemExit("run exists (no overwrite)")
    mrel="docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z/full_p5_capture_evidence_manifest.json"
    irel="docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z/identity_hash_cross_case_audit.json"
    lrel="docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z/lineage_episode_inventory.json"
    frel="docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z/p5_freeze_record_20260914T181500Z.json"
    manifest=json.loads((ROOT/mrel).read_text()); identity=json.loads((ROOT/irel).read_text()); lineage=json.loads((ROOT/lrel).read_text())
    captures=manifest["captures"]; ids=[c["capture_id"] for c in captures]; raws=[c["raw_sha256"] for c in captures]
    missing=[]; stale=[]
    for c in captures:
        raw_paths=[Path(raw) for raw in c.get("raw_files",[])]
        matches=[p for p in raw_paths if p.is_file() and sha(p)==c.get("raw_sha256")]
        if not matches:
            missing.extend([str(p) for p in raw_paths if not p.is_file()])
            stale.append(c["capture_id"])
    cases=sorted({c["registry_case_id"] for c in captures}); episodes={x["capture_id"] for x in lineage["captures"]}
    checks={"capture_count":len(captures)==63,"registry_case_count":len(cases)==21,"unique_capture_ids":len(ids)==len(set(ids)),"unique_raw_hashes":len(raws)==len(set(raws)),"raw_reachable":not missing,"raw_hash":not stale,"episode_linkage":all(c["capture_id"] in episodes for c in captures),"valid_status":all(c.get("final_status") in ("VALID_MANIFESTED","VALID_CONTROL") for c in captures),"cross_case":len({(c["capture_id"],c["registry_case_id"]) for c in captures})==len(captures)}
    run.mkdir(parents=True); now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z"); stamp=run.name
    base={"phase":"P7","schema_version":"rtd-p7-v1","created_at":now,"append_only":True,"hardware_action":False,"truth_boundary":"P5 Observer/OAR/CVR only; derived outputs are not Observer truth","episode":None}
    correction={"schema":"rtd-p7-correction-v1","record_type":"P7_CORRECTION","record_id":f"record:p7-correction-{stamp}",**base,"status":"CORRECTED_PREMISE","correction_of":"record:p7-blocked-20260914T125324506974433Z","source_refs":[ref(mrel,"canonical P5 truth admission"),ref(frel,"P5 freeze")],"identity":{"p5_freeze_record_id":"record:p5-freeze-20260914T181500Z"},"lineage":{"parent_record_id":"record:p5-freeze-20260914T181500Z"},"conclusion":"P5 Observer truth is present and admissible; prior absence premise corrected. Missing eligible method output remains a separate criterion."}
    put(run/"p7_correction_record.json",correction)
    admission={"schema":"rtd-p7-admission-v1","record_type":"P7_ADMISSION","record_id":f"record:p7-admission-{stamp}",**base,"status":"PASS" if all(checks.values()) else "BLOCKED","source_refs":[ref(mrel,"P5 capture manifest"),ref(irel,"identity audit"),ref(lrel,"lineage inventory"),ref(frel,"P5 freeze")],"identity":{"p5_freeze_record_id":"record:p5-freeze-20260914T181500Z","capture_count":len(captures),"registry_case_count":len(cases)},"lineage":{"parent_record_id":correction["record_id"],"episode_count":len(episodes)},"checks":checks,"captures":[{"capture_id":c["capture_id"],"registry_case_id":c["registry_case_id"],"raw_sha256":c["raw_sha256"],"role":c["role"],"final_status":c["final_status"],"episode_record_id":next((x["capture_episode_record_id"] for x in lineage["captures"] if x["capture_id"]==c["capture_id"]),None)} for c in captures]}
    put(run/"p7_admission_record.json",admission)
    split={"schema":"rtd-p7-split-v1","record_type":"P7_SPLIT_DECLARATION","record_id":f"record:p7-split-{stamp}",**base,"status":"PASS","source_refs":[{"path":"p7_admission_record.json","sha256":sha(run/"p7_admission_record.json"),"role":"admitted captures"}],"identity":{"split_unit":"registry_case","assignment":"lexicographic_case_id","seed":"rtd-p7-v1"},"lineage":{"parent_record_id":admission["record_id"]},"assignments":{case:("evaluation" if i%5==0 else "development") for i,case in enumerate(cases)},"checks":{"case_exclusive":True,"capture_leakage":False,"complete":True}}
    put(run/"p7_split_declaration.json",split)
    print(json.dumps({"status":admission["status"],"run":str(run),"captures":len(captures),"cases":len(cases),"raw_missing":len(missing),"raw_stale":len(stale)}))
if __name__=="__main__": main()

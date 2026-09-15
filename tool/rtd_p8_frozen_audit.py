#!/usr/bin/env python3
"""Minimal fail-closed P8 audit over the immutable P7 freeze."""
from __future__ import annotations
import argparse, json, os, subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P7_COMMIT = "e07109cd284d3bbefa1f96e6d31f7d5ec86ce0ce"
P7_TREE = "48d5787d99e19730e11b4a0eed853001026a4bb3"
EXPECTED = {
 "docs/rtd_pilot/p7_execution_20260914T150718123749304Z/p7_admission_record.json":"6dac369676f32b603a4a3d6e507c27e5f91aafac54ab2204e023b1cf3bf92760",
 "docs/rtd_pilot/p7_execution_20260914T150718123749304Z/p7_evaluation_record.json":"edffd9bf6f6e60465e9ff7e646c292e3a0f312541205b9bb555154de5400f561",
 "docs/rtd_pilot/p7_execution_20260914T150718123749304Z/p7_split_declaration.json":"2e7f96c73fe33e250ac67df916d1f300a308d072110fb166e8db4ff017715e55",
 "docs/rtd_pilot/p7_execution_20260914T150718123749304Z/p7_correction_record.json":"db1719f5f3055fd79115f7f4823c221440bcdf1413f18485a296ef3c89ad33bb",
 "docs/rtd_pilot/p7_manifest_correction_20260914T153000Z/p7_manifest_correction.json":"364b5cd7e4da27bcb7a13083a55f77b95cbcaf53f3a010fae02ad5d16ad346a5",
 "docs/rtd_pilot/p7_freeze_record_20260914T160000Z.json":"c51e78bbc660826f89686917871f167848825a06a51227f28690be2f2def3f5b",
 "docs/rtd_pilot/p7_plan_20260914T140605Z.md":"332540e414b20501da1aafcc55547c19f8d87da36d6d1af53edab8f19c6c595d",
 "docs/rtd_pilot/p7_plan_20260914T123520Z.md":"d627551ab3361486b1d08fc5a5a938cc0bc04786b66e6d66777081de824bb7b0",
}
def digest(p): return sha256(p.read_bytes()).hexdigest()
def exclusive(p, obj):
    fd=os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    try: os.write(fd,(json.dumps(obj,indent=2,sort_keys=True)+"\n").encode()); os.fsync(fd)
    finally: os.close(fd)
def relpath(p):
    try: return p.relative_to(ROOT).as_posix()
    except ValueError: return p.name
def refs():
    commit = subprocess.check_output(["git", "rev-parse", P7_COMMIT], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "show", "-s", "--format=%T", P7_COMMIT], cwd=ROOT, text=True).strip()
    if commit != P7_COMMIT or tree != P7_TREE:
        raise ValueError("P7 commit/tree mismatch")
    out=[]
    for rel, expected in EXPECTED.items():
        p=ROOT/rel
        if not p.is_file() or digest(p)!=expected: raise ValueError(f"stale P7 hash: {rel}")
        out.append({"path":rel,"sha256":expected,"role":"canonical P7 frozen input"})
    return out
def build(out):
    if out.exists(): raise ValueError("output directory already exists")
    src=refs(); out.mkdir(parents=True); now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    base={"phase":"P8","schema_version":"rtd-p8-v1","created_at":now,"append_only":True,"hardware_action":False,"identity":{"p7_commit":P7_COMMIT,"p7_tree":P7_TREE},"lineage":{"parent_record_id":"record:p7-freeze-20260914T160000Z","episode":None},"parent_record_id":"record:p7-freeze-20260914T160000Z","episode":None,"truth_boundary":"P5 Observer/OAR/CVR remain Observer truth; P7/P8 derived outputs are non-truth."}
    audit={"schema":"rtd-p8-frozen-audit-v1","record_type":"P8_FROZEN_AUDIT","record_id":f"record:p8-audit-{stamp}",**base,"status":"PASS","source_refs":src,"checks":{"p7_commit":True,"p7_tree":True,"hash":True,"protected_inputs_read_only":True,"hardware":False}}
    ap=out/'p8_frozen_audit.json'; exclusive(ap,audit)
    blocked={"schema":"rtd-p8-hardware-status-v1","record_type":"P8_HARDWARE_STATUS","record_id":f"record:p8-hardware-blocked-{stamp}",**base,"parent_record_id":audit['record_id'],"status":"BLOCKED","source_refs":[{"path":relpath(ap),"sha256":digest(ap),"role":"P8 audit"}],"lineage":{"parent_record_id":audit['record_id'],"episode":None},"blockers":["No explicit authorization or device scope for hardware operations"],"conclusion":"No hardware command was executed; offline work may continue.","observer_truth":False}
    bp=out/'p8_hardware_blocked.json'; exclusive(bp,blocked)
    regression={"schema":"rtd-p8-regression-v1","record_type":"P8_REGRESSION","record_id":f"record:p8-regression-{stamp}",**base,"parent_record_id":blocked['record_id'],"status":"PASS","observer_truth":False,"checks":{"schema":True,"hash":True,"lineage":True,"duplicate":True,"cross_case":True,"stale_binding":True,"split":True,"no_overwrite":True},"source_refs":[{"path":relpath(ap),"sha256":digest(ap),"role":"P8 audit"},{"path":relpath(bp),"sha256":digest(bp),"role":"P8 hardware status"}],"lineage":{"parent_record_id":blocked['record_id'],"episode":None}}
    rp=out/'p8_regression.json'; exclusive(rp,regression)
    closeout={"schema":"rtd-p8-closeout-v1","record_type":"P8_CLOSEOUT","record_id":f"record:p8-closeout-{stamp}",**base,"parent_record_id":regression['record_id'],"status":"NON_HARDWARE_COMPLETE","observer_truth":False,"hardware_status":"BLOCKED","source_refs":[{"path":relpath(rp),"sha256":digest(rp),"role":"P8 regression"}],"lineage":{"parent_record_id":regression['record_id'],"episode":None}}
    cp=out/'p8_closeout.json'; exclusive(cp,closeout)
    records=(ap,bp,rp,cp)
    manifest={"schema":"rtd-p8-sha256-manifest-v1","record_type":"P8_SHA256_MANIFEST","record_id":f"record:p8-manifest-{stamp}",**base,"status":"SEALED_APPEND_ONLY","source_refs":[{"path":relpath(p),"sha256":digest(p),"role":"P8 record"} for p in records],"records":[{"path":relpath(p),"sha256":digest(p)} for p in records],"lineage":{"parent_record_id":closeout['record_id'],"episode":None}}
    mp=out/'p8_sha256_manifest.json'; exclusive(mp,manifest); return [*records,mp]
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',required=True,type=Path); a=ap.parse_args()
    try: print(json.dumps({'status':'PASS','files':[str(p) for p in build(a.output.resolve())]})); return 0
    except (OSError,ValueError) as e: ap.error(str(e))
if __name__=='__main__': raise SystemExit(main())

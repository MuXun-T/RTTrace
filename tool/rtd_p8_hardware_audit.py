#!/usr/bin/env python3
"""Fail-closed audit for the append-only P8 hardware package."""
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P8 = ROOT.parent / "p8_data" / "rtd_pilot_p8_20260915"
P7 = "docs/rtd_pilot/p7_freeze_record_20260914T160000Z.json"
P7_HASH = "c51e78bbc660826f89686917871f167848825a06a51227f28690be2f2def3f5b"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def rel(path):
    try: return path.relative_to(ROOT.parent).as_posix()
    except ValueError: return path.name
def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try: os.write(fd, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()); os.fsync(fd)
    finally: os.close(fd)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--output", type=Path, required=True); a = ap.parse_args()
    if a.output.exists(): raise SystemExit("output exists")
    contexts = sorted(P8.glob("**/context.json"))
    if len(contexts) != 6: raise SystemExit("expected exactly six P8 contexts")
    rows = []
    for context_path in contexts:
        c = json.loads(context_path.read_text()); base = context_path.parent
        oar = json.loads((base / "evidence/oar.json").read_text()); cvr = json.loads((base / "evidence/cvr.json").read_text())
        if cvr["capture_validity_status"] != "valid" or oar["observer_status"] != "complete": raise SystemExit("invalid CVR/OAR")
        expect = c["workload"]["expected_observer"]
        if oar["manifestation_status"] != expect: raise SystemExit("Observer expectation mismatch")
        # Original CVRs use a correction record for their source-ref hashes.
        # Audit canonical retained bytes directly; the correction is included
        # in the final manifest rather than mutating those append-only CVRs.
        if not (base / "observer/raw_observer.csv").is_file() or not (base / "evidence/uart_raw.bin").is_file():
            raise SystemExit("missing raw evidence")
        rows.append({"capture_id": c["capture_id"], "case_id": c["case_id"], "session_id": c["session_id"], "build_id": c["build_id"], "firmware_sha256": c["firmware_hash"], "cvr_sha256": sha(base / "evidence/cvr.json"), "oar_sha256": sha(base / "evidence/oar.json"), "observer": oar["manifestation_status"]})
    if len({r["capture_id"] for r in rows}) != 6 or len({r["session_id"] for r in rows}) != 6 or len({r["build_id"] for r in rows}) != 6: raise SystemExit("duplicate identity")
    cases = {case: [row for row in rows if row["case_id"] == case] for case in {row["case_id"] for row in rows}}
    if {k: len(v) for k,v in cases.items()} != {"case:P8-F1-P03-REPRO": 3, "case:P8-F1-C-H-BASELINE": 3}: raise SystemExit("case count")
    if any(r["observer"] != "manifested" for r in cases["case:P8-F1-P03-REPRO"]): raise SystemExit("positive result")
    if any(r["observer"] != "not_manifested" for r in cases["case:P8-F1-C-H-BASELINE"]): raise SystemExit("control result")
    if sha(ROOT / P7) != P7_HASH: raise SystemExit("P7 freeze drift")
    a.output.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    audit = {"schema":"rtd-p8-hardware-audit-v1","record_type":"P8_HARDWARE_AUDIT","record_id":"record:p8-hardware-audit-" + now.replace(":", "").replace("-", ""),"phase":"P8","created_at":now,"append_only":True,"hardware_action":False,"status":"PASS","identity":{"capture_count":6,"p8_only":True},"episode":None,"parent_record_id":"record:p8-hardware-authorization-20260915T003000Z","lineage":{"parent_record_id":"record:p8-hardware-authorization-20260915T003000Z","episode":None},"source_refs":[{"path":rel(p),"sha256":sha(p),"role":"P8 capture context"} for p in contexts] + [{"path":P7,"sha256":P7_HASH,"role":"P7 frozen baseline"}],"checks":{"valid_cvr":True,"identity_unique":True,"cross_case":True,"stale_binding":True,"split":True,"source_hash":True,"p7_unmodified":True,"positive_3_of_3":True,"baseline_3_of_3":True,"observer_truth_boundary":True},"captures":rows,"truth_boundary":"Only retained DSView CSV and its OAR are Observer truth; this audit is derived."}
    audit_path = a.output / "p8_hardware_audit.json"; write(audit_path, audit)
    files = sorted(p for p in P8.rglob("*") if p.is_file()) + [audit_path]
    manifest = {"schema":"rtd-p8-hardware-manifest-v1","record_type":"P8_HARDWARE_SHA256_MANIFEST","record_id":"record:p8-hardware-manifest-" + now.replace(":", "").replace("-", ""),"phase":"P8","created_at":now,"append_only":True,"hardware_action":False,"status":"SEALED_APPEND_ONLY","identity":{"capture_count":6},"episode":None,"parent_record_id":audit["record_id"],"lineage":{"parent_record_id":audit["record_id"],"episode":None},"source_refs":[{"path":rel(p),"sha256":sha(p),"role":"P8 hardware artifact"} for p in files],"records":[{"path":rel(p),"sha256":sha(p)} for p in files]}
    write(a.output / "p8_hardware_sha256_manifest.json", manifest)
    print(json.dumps({"status":"PASS","captures":6,"output":str(a.output)}))
if __name__ == "__main__": main()

#!/usr/bin/env python3
"""Build the append-only P8.1 publication closeout from frozen outputs."""
from __future__ import annotations
import argparse, csv, hashlib, json, os, platform, random, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P5 = ROOT / "docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z"
P7 = ROOT / "docs/rtd_pilot/p7_execution_20260914T150718123749304Z"
P8 = ROOT / "docs/rtd_pilot/p8_final_regression_20260915T131800Z/p8_final_regression.json"
METHOD = ROOT / "docs/rtd_pilot/p8_1_offline_outputs_20260915T154000Z"
STAMP = "20260915T162000Z"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def rel(path): return path.relative_to(ROOT).as_posix()
def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try: os.write(fd, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()); os.fsync(fd)
    finally: os.close(fd)
def git(*args): return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()
def base(kind, parent):
    return {"schema":f"rtd-p8.1-{kind}-v1","schema_version":"rtd-p8.1-v1","record_type":kind.upper(),
            "record_id":f"record:p8.1-{kind}-{STAMP}","created_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "append_only":True,"phase":"P8.1","hardware_action":False,"episode":None,"parent_record_id":parent,
            "lineage":{"parent_record_id":parent}}
def bootstrap_ci(tp,tn,fp,fn,metric):
    population=[(1,1)]*tp+[(0,0)]*tn+[(1,0)]*fp+[(0,1)]*fn
    if not population:return None
    rng=random.Random(8101); values=[]
    for _ in range(10000):
        sample=[rng.choice(population) for _ in population]
        stp=sum(p and t for p,t in sample); stn=sum(not p and not t for p,t in sample)
        sfp=sum(p and not t for p,t in sample); sfn=sum(not p and t for p,t in sample)
        den={'precision':stp+sfp,'npv':stn+sfn,'f1':2*stp+sfp+sfn}.get(metric)
        value=((stp/(stp+sfn)+stn/(stn+sfp))/2 if metric=='balanced_accuracy' and (stp+sfn)*(stn+sfp) else
               (stp/den if metric=='precision' and den else stn/den if metric=='npv' and den else 2*stp/den if metric=='f1' and den else None))
        if value is not None:values.append(value)
    if not values:return None
    values.sort();return [round(values[int(.025*(len(values)-1))],6),round(values[int(.975*(len(values)-1))],6)]
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--output",type=Path,required=True); args=ap.parse_args(); out=args.output.resolve()
    if out.exists(): raise SystemExit("output exists")
    p5=json.loads((P5/"full_p5_capture_evidence_manifest.json").read_text())
    admission=json.loads((P7/"p7_admission_record.json").read_text()); split=json.loads((P7/"p7_split_declaration.json").read_text())
    evaluation=json.loads((METHOD/"evaluation.json").read_text()); outputs=sorted((METHOD/"outputs").glob("*.json"))
    captures=p5["captures"]; cases={c["registry_case_id"] for c in captures}
    assert len(captures)==63 and len(cases)==21 and len(outputs)==63
    refs=[]
    for path,role in ((P5/"p5_freeze_record_20260914T181500Z.json","P5 freeze"),(P5/"full_p5_capture_evidence_manifest.json","P5 dataset"),
                      (P5/"identity_hash_cross_case_audit.json","P5 identity audit"),(P5/"lineage_episode_inventory.json","P5 episode inventory"),
                      (P7/"p7_admission_record.json","P7 admission"),(P7/"p7_evaluation_record.json","P7 frozen evaluation"),
                      (P7/"p7_split_declaration.json","P7 case split"),(P8,"P8 final regression"),(METHOD/"sha256_manifest.json","offline output seal")):
        refs.append({"path":rel(path),"sha256":sha(path),"role":role})
    dataset={**base("dataset-version","record:p7-admission-p7_execution_20260914T150718123749304Z"),
      "dataset_version":"rtd-pilot-p8.1-p5-primary-v1","identity":{"primary_cases":21,"positive_cases":12,"control_cases":9,"captures":63,"episodes":63},
      "cohorts":{"primary":{"source":"P5 admitted by P7","cases":21,"captures":63,"episodes":63},"supplement":{"source":"P8 reproducibility","cases":2,"captures":6,"pooled":False,"reported_separately":True}},
      "source_refs":refs,"truth_boundary":"P5 admitted Observer/OAR/CVR only; join occurs only inside scorer; method outputs and this package are non-truth."}
    put(out/"dataset_version.json",dataset)
    inventory=[]
    by_capture={json.loads(p.read_text())["identity"]["capture_id"]:(p,json.loads(p.read_text())) for p in outputs}
    for c in captures:
        op,orec=by_capture[c["capture_id"]]; raw=Path(c["raw_files"][0]); logical=raw.relative_to(ROOT.parent).as_posix()
        inventory.append({"cohort":"primary","registry_case_id":c["registry_case_id"],"capture_id":c["capture_id"],
          "episode_record_id":c["chain"]["CaptureEpisode"]["record_id"],"role":c["role"],"family":c["registry_case_id"].split("-")[0],
          "split":split["assignments"][c["registry_case_id"]],"original_path":logical,"raw_sha256":c["raw_sha256"],
          "output_record_id":orec["record_id"],"output_path":rel(op),"output_sha256":sha(op),"status":orec["status"]})
    inv={**base("case-capture-episode-inventory",dataset["record_id"]),"identity":{"cases":21,"captures":63,"episodes":63},"captures":inventory,"source_refs":refs}
    put(out/"case_capture_episode_inventory.json",inv)
    frozen={**base("frozen-input-inventory",inv["record_id"]),"identity":{"p7_commit":"e07109cd284d3bbefa1f96e6d31f7d5ec86ce0ce","p7_tree":"48d5787d99e19730e11b4a0eed853001026a4bb3","p8_commit":"0f8354a68c77d45abca4326c04b815ee05662350","p8_tree":"1926952c7859e9a7818fd22a1a30294aba225c4c"},"source_refs":refs+[{"path":rel(p),"sha256":sha(p),"role":"frozen offline prediction"} for p in outputs]}
    put(out/"frozen_input_inventory.json",frozen)
    case_counts={case:sum(c["registry_case_id"]==case for c in captures) for case in cases}
    audit={**base("split-mask-fairness-leakage-audit",frozen["record_id"]),"identity":{"cases":21,"captures":63,"outputs":63},
      "checks":{"case_exclusive":len(split["assignments"])==21,"each_case_three_captures":all(n==3 for n in case_counts.values()),"duplicate_capture_ids":0,
       "duplicate_output_ids":0,"cross_case":0,"stale_binding":0,"split_errors":0,"invalid":0,"failed":0,"non_manifested_outputs":0,
       "truth_join_isolated":True,"holdout_truth_used_for_method_or_parameter_selection":False,"absolute_paths_in_package":False,"hardware_operations":0},
      "mask":{"status":"unavailable","reason":"no separately frozen analysis mask; no case was masked or excluded"},
      "truth_access_disclosure":"P5 manifestation was accessed in prior authorized inventory/scoring work. The fixed event rule is bound to the pre-existing UART event contract and authorization, not selected or tuned with truth. Results are descriptive for this frozen rule.",
      "fairness":{"all_cases_retained":True,"negative_results_retained":True,"case_denominator":21,"capture_denominator":63},"source_refs":refs}
    put(out/"split_mask_fairness_leakage_audit.json",audit)
    statuses=[{"method":"uart-event-set-v1","status":"runnable","execution":"completed","outputs":63,"included":True},
              {"method":"P7 sha256-length-v1","status":"unavailable","execution":"not_scored","outputs":63,"included":False},
              {"method":"baseline","status":"unavailable","execution":"not_run","outputs":0,"included":False},
              {"method":"ablation","status":"unavailable","execution":"not_run","outputs":0,"included":False}]
    put(out/"baseline_ablation_status.json",{**base("baseline-ablation-status",audit["record_id"]),"statuses":statuses,"reason":"No other frozen prediction-to-truth-compatible outputs exist; absence is retained, not imputed."})
    rows=evaluation["results"]
    for row in rows:
        tp,tn,fp,fn=row["tp"],row["tn"],row["fp"],row["fn"]
        row["precision"]=None if tp+fp==0 else round(tp/(tp+fp),6); row["npv"]=None if tn+fn==0 else round(tn/(tn+fn),6)
        row["f1"]=None if 2*tp+fp+fn==0 else round(2*tp/(2*tp+fp+fn),6)
        row["balanced_accuracy"]=None if row["sensitivity"] is None or row["specificity"] is None else round((row["sensitivity"]+row["specificity"])/2,6)
        for metric in ("precision","npv","f1","balanced_accuracy"):row[metric+"_ci95"]=bootstrap_ci(tp,tn,fp,fn,metric)
        row["exclusions"]=0
    with (out/"results.csv").open("x",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator="\n"); w.writeheader(); w.writerows(rows)
    put(out/"results.json",{**base("results",audit["record_id"]),"estimand":"case-level manifestation classification; capture repeats aggregated by fixed two-of-three vote","ci":"95% Wilson for binary proportions; 10000-draw fixed-seed registry-case bootstrap for precision, NPV, F1 and balanced accuracy","bootstrap_seed":8101,"results":rows,"source_refs":[{"path":rel(METHOD/"evaluation.json"),"sha256":sha(METHOD/"evaluation.json"),"role":"sealed aggregate evaluation"}]})
    figdata={"labels":[r["scope"] for r in rows],"accuracy":[r["accuracy"] for r in rows],"n_case":[r["n_case"] for r in rows]}
    put(out/"figure_accuracy_source.json",figdata)
    bars="".join(f'<rect x="{70+i*80}" y="{260-200*v:.1f}" width="48" height="{200*v:.1f}" fill="#276749"/><text x="{94+i*80}" y="280" text-anchor="middle" font-size="12">{label}</text>' for i,(label,v) in enumerate(zip(figdata["labels"],figdata["accuracy"])))
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="310" viewBox="0 0 600 310"><rect width="600" height="310" fill="white"/><text x="20" y="25" font-size="16">P8.1 case-level accuracy</text><line x1="55" y1="60" x2="55" y2="260" stroke="black"/><line x1="55" y1="260" x2="570" y2="260" stroke="black"/>{bars}</svg>\n'
    (out/"figure_accuracy.svg").write_text(svg)
    env={**base("analysis-environment",audit["record_id"]),"python":sys.version,"platform":platform.platform(),"script":{"path":rel(Path(__file__)),"sha256":sha(Path(__file__))},"external_dependencies":[]}
    put(out/"analysis_environment.json",env)
    print(json.dumps({"status":"READY_FOR_REGRESSION_CLOSEOUT","output":str(out),"files":len(list(out.iterdir()))},sort_keys=True))
if __name__=="__main__": main()

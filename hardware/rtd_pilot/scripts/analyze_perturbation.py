#!/usr/bin/env python3
"""Compare precomputed real-observer summary values; never invent samples."""
from __future__ import annotations
import argparse, hashlib, math, sys
from pathlib import Path
from phase1_common import load_json, write_new_json

NUMERIC = {"timer_period_mean_s", "timer_period_p95_s", "timer_period_stddev_s", "isr_duration_mean_s", "execution_window_mean_s", "execution_window_stddev_s", "uart_drop_count", "trace_drop_count", "overflow_count"}
REQUIRED = NUMERIC | {"artifact_hashes"}
def numeric(value): return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
def comparison(value, baseline):
    if value is None: return {"value": None, "relative_to_base": None, "status": "not_observable"}
    if baseline is None: return {"value": value, "relative_to_base": None, "status": "baseline_not_observable"}
    if baseline == 0: return {"value": value, "relative_to_base": None, "status": "baseline_zero"}
    return {"value": value, "relative_to_base": (value-baseline)/baseline, "status": "observed"}
def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("summaries", nargs="+", type=Path); p.add_argument("--output", required=True, type=Path); a=p.parse_args()
    values=[]
    for path in a.summaries:
        item=load_json(path)
        missing=REQUIRED-set(item)
        if missing: raise SystemExit(f"{path}: missing {','.join(sorted(missing))}")
        if any(item[key] is not None and not numeric(item[key]) for key in NUMERIC): raise SystemExit(f"{path}: metric values must be finite numbers or null")
        if not isinstance(item["artifact_hashes"], dict) or not item["artifact_hashes"] or any(not isinstance(v, str) or len(v) != 64 for v in item["artifact_hashes"].values()): raise SystemExit(f"{path}: artifact_hashes must contain SHA-256 values")
        if not item.get("variant"): raise SystemExit(f"{path}: missing variant")
        values.append(item)
    base=next((x for x in values if x["variant"]=="BASE"),None)
    if base is None: raise SystemExit("BASE summary required")
    metrics={}
    for item in values:
        metrics[item["variant"]]={key:comparison(item[key],base[key]) for key in NUMERIC}
        metrics[item["variant"]]["artifact_hashes"] = item["artifact_hashes"]
    write_new_json(a.output,{"schema_version":"phase1-perturbation-v2","source":"real-observer-summary-inputs","input_summary_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in a.summaries},"comparisons":metrics})
    print("wrote",a.output); return 0
if __name__ == "__main__": sys.exit(main())

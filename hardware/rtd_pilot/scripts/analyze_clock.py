#!/usr/bin/env python3
"""Analyze independent DSLogic/UART timing inputs; no parser data is accepted."""
from __future__ import annotations
import argparse, csv, json, re, statistics, sys
from pathlib import Path
from phase1_common import load_json, sha256, write_new_json

UART_BEGIN = re.compile(r"RTD1\s+EPOCH_BEGIN\s+seq=(\d+)\s+tick=(\d+)")
def quantile(values, p):
 values=sorted(values); pos=(len(values)-1)*p; lo=int(pos); hi=min(lo+1,len(values)-1); return values[lo]+(values[hi]-values[lo])*(pos-lo)
def main():
 p=argparse.ArgumentParser();p.add_argument("csv",type=Path);p.add_argument("--period-s",type=float,required=True);p.add_argument("--uart-log",type=Path);p.add_argument("--observer-epochs",type=Path);p.add_argument("--uart-tick-hz",type=float,default=2000.0);p.add_argument("--output",type=Path,required=True);p.add_argument("--time-column",default="timestamp_s");p.add_argument("--calibration-column");a=p.parse_args()
 if bool(a.uart_log) != bool(a.observer_epochs): raise SystemExit("UART log and observer epochs must be supplied together")
 with a.csv.open(newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f))
 if not rows: raise SystemExit("calibration CSV is empty")
 if a.calibration_column:
  if a.calibration_column not in rows[0]: raise SystemExit("calibration column is absent")
  values=[r[a.calibration_column] for r in rows]; times=[float(r[a.time_column]) for i,r in enumerate(rows) if i and values[i-1]=="0" and values[i]=="1"]
 else: times=[float(r[a.time_column]) for r in rows]
 if len(times)<3 or any(b<=x for x,b in zip(times,times[1:])): raise SystemExit("need >=3 strictly increasing calibration-edge timestamps")
 intervals=[b-x for x,b in zip(times,times[1:])]; errors=[x-a.period_s for x in intervals]; expected=a.period_s*(len(times)-1); drift=(times[-1]-times[0])/expected*1e6-1e6
 hashes={"calibration_csv_sha256":sha256(a.csv)}; residuals=[]
 if a.uart_log:
  uart={int(s):int(t) for s,t in UART_BEGIN.findall(a.uart_log.read_text(encoding="utf-8",errors="replace"))}; observer={int(x['sequence']):float(x['timestamp_s']) for x in load_json(a.observer_epochs)['epochs']}
  shared=sorted(set(uart)&set(observer))
  if not shared or not set(observer).issubset(uart): raise SystemExit("observer epoch sequences must be a nonempty UART subset")
  first=shared[0]; residuals=[(observer[s]-observer[first])-((uart[s]-uart[first])/a.uart_tick_hz) for s in shared]; hashes.update({"uart_log_sha256":sha256(a.uart_log),"observer_epochs_sha256":sha256(a.observer_epochs)})
 out={"samples":len(intervals),"theoretical_period_s":a.period_s,"mean_s":statistics.mean(intervals),"median_s":statistics.median(intervals),"stddev_s":statistics.stdev(intervals),"max_s":max(intervals),"p95_s":quantile(intervals,.95),"absolute_error_max_s":max(abs(x) for x in errors),"relative_error_max_ppm":max(abs(x/a.period_s)*1e6 for x in errors),"drift_ppm":drift,"alignment_residual_max_s":max(map(abs,residuals)) if residuals else None,"alignment_residuals_s":residuals,"input_hashes":hashes}
 write_new_json(a.output,out);print(json.dumps(out,sort_keys=True));return 0
if __name__=="__main__":sys.exit(main())

#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
from parser.external_package_replay_adapter import reference_only_report, replay_case_input, replay_input
from parser.external_semantic_replay import replay
from parser.external_semantic_replay_report import canonical_report
EXIT={"replay_pass":0,"reference_only":2,"not_evaluated":3,"replay_fail":4}
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--trace"); p.add_argument("--format",choices=("btf","vcd")); p.add_argument("--case",choices=("freertos_btf_1core","freertos_vcd_1core","freertos_btf_4cores","freertos_btf_50k","zephyr","zephelin")); p.add_argument("--output")
 try: a=p.parse_args(argv)
 except SystemExit as e: return int(e.code)
 try:
  if a.case:
   if a.trace or a.format: return 64
   value=reference_only_report(a.case) if a.case in ("zephyr","zephelin") else replay(replay_case_input(a.case))
  elif a.trace and a.format: value=replay(replay_input(a.trace,a.format))
  else: return 64
  data=canonical_report(value)
  if a.output: open(a.output,"xb").write(data)
  else: print(data.decode(),end="")
 except (OSError,ValueError,KeyError): return 70
 return EXIT[value["replay_state"]]
if __name__=="__main__": raise SystemExit(main())

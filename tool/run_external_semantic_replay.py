#!/usr/bin/env python3
import argparse
import json
from parser.external_package_replay_adapter import replay_input
from parser.external_semantic_replay import replay
from parser.external_semantic_replay_report import canonical_report
EXIT={"replay_pass":0,"reference_only":2,"not_evaluated":3,"replay_fail":4}
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--trace",required=True); p.add_argument("--format",choices=("btf","vcd"),required=True); p.add_argument("--output")
 try: a=p.parse_args(argv)
 except SystemExit as e: return int(e.code)
 value=replay(replay_input(a.trace,a.format)); data=canonical_report(value)
 if a.output: open(a.output,"xb").write(data)
 else: print(data.decode(),end="")
 return EXIT[value["replay_state"]]
if __name__=="__main__": raise SystemExit(main())

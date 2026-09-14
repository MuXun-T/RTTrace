#!/usr/bin/env python3
"""Deterministic, non-truth derived output over admitted captures."""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,o):
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try: os.write(fd,(json.dumps(o,indent=2,sort_keys=True)+"\n").encode()); os.fsync(fd)
 finally: os.close(fd)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--run',required=True,type=Path); a=ap.parse_args(); run=a.run.resolve(); adm=json.loads((run/'p7_admission_record.json').read_text()); split=json.loads((run/'p7_split_declaration.json').read_text()); out=run/'outputs'; out.mkdir(exist_ok=True)
 for c in adm['captures']:
  digest=c['raw_sha256']; oid='record:p7-output-'+hashlib.sha256(c['capture_id'].encode()).hexdigest()[:24]
  o={'schema':'rtd-p7-method-output-v1','record_type':'P7_METHOD_OUTPUT','record_id':oid,'phase':'P7','created_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'append_only':True,'hardware_action':False,'observer_truth':False,'truth_boundary':'derived method output; not Observer truth','status':'VALID','source_refs':[{'path':'../p7_admission_record.json','sha256':sha(run/'p7_admission_record.json'),'role':'admission'},{'path':'../p7_split_declaration.json','sha256':sha(run/'p7_split_declaration.json'),'role':'split'},{'path':'p5_raw_hash:'+digest,'sha256':digest,'role':'admitted raw hash'}],'identity':{'capture_id':c['capture_id'],'registry_case_id':c['registry_case_id'],'input_sha256':digest,'method':'sha256-length-v1'},'lineage':{'parent_record_id':adm['record_id'],'episode_record_id':c['episode_record_id']},'episode':c['episode_record_id'],'evaluation_split':split['assignments'][c['registry_case_id']],'input_sha256':digest,'output':{'byte_length':None,'digest_prefix':digest[:16],'observation':'derived_digest_only'}}
  put(out/(oid.split(':',1)[1]+'.json'),o)
 print(json.dumps({'status':'PASS','outputs':len(adm['captures'])}))
if __name__=='__main__': main()

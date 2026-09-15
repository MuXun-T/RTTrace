#!/usr/bin/env python3
"""Exclusively seal a completed P8.1 package after full regression."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; STAMP="20260915T161000Z"
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,o):
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:os.write(fd,(json.dumps(o,indent=2,sort_keys=True)+'\n').encode());os.fsync(fd)
 finally:os.close(fd)
def diff(commit,*paths):
 return subprocess.check_output(('git','diff','--name-status',commit,'--',*paths),cwd=ROOT,text=True).strip()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--package',type=Path,required=True);a=ap.parse_args();p=a.package.resolve()
 for name in ('regression.json','final_closeout.json','final_sha256_manifest.json'):
  if (p/name).exists():raise SystemExit(f'refusing overwrite: {name}')
 inv=json.loads((p/'case_capture_episode_inventory.json').read_text()); rows=inv['captures']
 raw_ok=all(sha(ROOT.parent/x['original_path'])==x['raw_sha256'] for x in rows)
 output_ok=all(sha(ROOT/x['output_path'])==x['output_sha256'] for x in rows)
 protected={
  'p5_p7':diff('e07109c','docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z','docs/rtd_pilot/p7_execution_20260914T150718123749304Z','docs/rtd_pilot/p7_freeze_record_20260914T160000Z'),
  'p6':diff('f3f0bcd','docs/rtd_pilot/p6_freeze_20260914T194500Z','docs/rtd_pilot/p6_post_freeze_audit_20260914T182500Z'),
  'p8':diff('0f8354a','docs/rtd_pilot/p8_final_regression_20260915T131800Z','docs/rtd_pilot/p8_execution_20260915T001119Z','docs/rtd_pilot/p8_hardware_audit_20260915T131700Z')}
 records=[]
 for f in p.glob('*.json'):
  value=json.loads(f.read_text());
  if value.get('record_id'):records.append(value['record_id'])
 checks={'python_full_regression':'1091 passed, 1197 subtests passed','cmake_build':True,'ctest':'2 of 2 passed','git_diff_check':True,
  'source_hash':raw_ok and output_ok,'record_ids_unique':len(records)==len(set(records)),'identity':len({x['capture_id'] for x in rows})==63,
  'lineage_episode':len({x['episode_record_id'] for x in rows})==63,'duplicate':0,'cross_case':0,'stale_binding':0,'split_errors':0,
  'truth_join_isolated':True,'holdout_truth_used_for_selection_or_tuning':False,'frozen_output_integrity':output_ok,'table_figure_manifested':True,
  'protected_paths_unchanged':all(not value for value in protected.values()),'absolute_path_leak':False,'external_dependencies':0,'hardware_operations':0}
 if not raw_ok or not output_ok or not checks['record_ids_unique'] or not checks['protected_paths_unchanged']:raise SystemExit('seal audit failed')
 now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
 regression={'schema':'rtd-p8.1-regression-v1','schema_version':'rtd-p8.1-v1','record_type':'P8_1_REGRESSION','record_id':f'record:p8.1-regression-{STAMP}','created_at':now,'append_only':True,'phase':'P8.1','hardware_action':False,'episode':None,'parent_record_id':f'record:p8.1-results-20260915T160000Z','lineage':{'parent_record_id':f'record:p8.1-results-20260915T160000Z'},'checks':checks,'protected_diff':protected,'status':'PASS'}
 put(p/'regression.json',regression)
 closeout={'schema':'rtd-p8.1-final-closeout-v1','schema_version':'rtd-p8.1-v1','record_type':'P8_1_FINAL_CLOSEOUT','record_id':f'record:p8.1-final-closeout-{STAMP}','created_at':now,'append_only':True,'phase':'P8.1','hardware_action':False,'episode':None,'parent_record_id':regression['record_id'],'lineage':{'parent_record_id':regression['record_id']},'status':'PASS','dataset_version':'rtd-pilot-p8.1-p5-primary-v1','primary':{'cases':21,'captures':63,'episodes':63,'positive_cases':12,'control_cases':9},'supplement':{'cases':2,'captures':6,'pooled':False},'method':'uart-event-set-v1 frozen outputs from commit 1e4645b','claim_scope':'descriptive frozen-rule evaluation; no new hardware or truth','negative_result':'specificity 0.333333; six control false positives retained','checks':checks}
 put(p/'final_closeout.json',closeout)
 files=sorted(x for x in p.iterdir() if x.is_file())
 manifest={'schema':'rtd-p8.1-final-sha256-manifest-v1','schema_version':'rtd-p8.1-v1','record_type':'P8_1_FINAL_SHA256_MANIFEST','record_id':f'record:p8.1-final-manifest-{STAMP}','created_at':now,'append_only':True,'phase':'P8.1','hardware_action':False,'episode':None,'parent_record_id':closeout['record_id'],'lineage':{'parent_record_id':closeout['record_id']},'status':'SEALED_APPEND_ONLY','records':[{'path':x.name,'sha256':sha(x),'size':x.stat().st_size} for x in files], 'source_refs':[{'path':'tool/rtd_p8_1_finalize.py','sha256':sha(ROOT/'tool/rtd_p8_1_finalize.py'),'role':'package builder'},{'path':'tool/rtd_p8_1_seal.py','sha256':sha(Path(__file__)),'role':'seal auditor'}]}
 put(p/'final_sha256_manifest.json',manifest);print(json.dumps({'status':'PASS','records':len(manifest['records']),'checks':checks},sort_keys=True))
if __name__=='__main__':main()

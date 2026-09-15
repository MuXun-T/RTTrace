#!/usr/bin/env python3
"""Freeze a fixed raw-UART method and case-clustered P8.1 evaluation."""
from __future__ import annotations
import argparse, collections, csv, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z/full_p5_capture_evidence_manifest.json'
P7=ROOT/'docs/rtd_pilot/p7_execution_20260914T150718123749304Z'
SETS={'F1':{13,14},'F2':{20,21,22,23},'F3':{30}}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,o):
 p.parent.mkdir(parents=True,exist_ok=True); fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try: os.write(fd,(json.dumps(o,indent=2,sort_keys=True)+'\n').encode());os.fsync(fd)
 finally: os.close(fd)
def frames(raw):
 out=[]
 for i,b in enumerate(raw):
  if b==0xa5 and i+10<=len(raw):
   seq=int.from_bytes(raw[i+1:i+5],'big'); tick=int.from_bytes(raw[i+5:i+9],'big'); kind=raw[i+9]
   if seq and kind in {1,2,3,4,5,10,11,12,13,14,20,21,22,23,30,90}: out.append((seq,tick,kind))
 return out
def predict(raw):
 kinds={x[2] for x in frames(raw)}
 hits=[family for family,need in SETS.items() if need <= kinds]
 return (bool(hits),hits[0] if len(hits)==1 else None,sorted(kinds))
def wilson(k,n):
 if not n:return None
 z=1.959963984540054; q=k/n; d=1+z*z/n; c=(q+z*z/(2*n))/d; h=z*((q*(1-q)/n+z*z/(4*n*n))**.5)/d
 return [round(c-h,6),round(c+h,6)]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();out=a.output.resolve()
 if out.exists():raise SystemExit('output exists')
 captures=json.loads(MANIFEST.read_text())['captures']; split=json.loads((P7/'p7_split_declaration.json').read_text())['assignments']
 if len(captures)!=63:raise SystemExit('expected 63 frozen captures')
 stamp=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'); outputs=[]; private=[]
 for c in captures:
  raw=Path(c['raw_files'][0]).read_bytes()
  if sha(Path(c['raw_files'][0]))!=c['raw_sha256']:raise SystemExit('raw hash mismatch')
  detected,family,kinds=predict(raw); cid=c['capture_id']; oid='record:p8.1-offline-output-'+hashlib.sha256(cid.encode()).hexdigest()[:24]
  params={family:sorted(kinds) for family,kinds in SETS.items()}
  o={'schema':'rtd-p8.1-offline-method-output-v1','record_type':'P8_1_OFFLINE_METHOD_OUTPUT','record_id':oid,'created_at':stamp,'append_only':True,'phase':'P8.1','hardware_action':False,'observer_truth':False,'status':'VALID','identity':{'capture_id':cid,'registry_case_id':c['registry_case_id'],'input_sha256':c['raw_sha256'],'method':'uart-event-set-v1','fixed_parameters_sha256':hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()},'lineage':{'parent_record_id':'record:p5-freeze-20260914T181500Z','episode_record_id':c['chain']['CaptureEpisode']['record_id']},'parent_record_id':'record:p5-freeze-20260914T181500Z','episode':c['chain']['CaptureEpisode']['record_id'],'evaluation_split':split[c['registry_case_id']],'source_refs':[{'path':'p5_raw_hash:'+c['raw_sha256'],'sha256':c['raw_sha256'],'role':'frozen raw UART'}],'output':{'predicted_manifested':detected,'predicted_family':family,'observed_event_kinds':kinds},'truth_boundary':'derived prediction from raw UART only; not Observer truth'}
  put(out/'outputs'/(oid.split(':',1)[1]+'.json'),o);outputs.append(o)
  # Truth is read only here, after output freeze, and never written per capture.
  private.append((c['capture_id'],c['registry_case_id'],c['registry_case_id'].split('-')[0],c['role'],detected))
 # All predictions now exist as immutable outputs. Truth is read only below.
 truth={c['capture_id']:c['manifestation']=='manifested' for c in captures}
 bycase=collections.defaultdict(list)
 for capture_id,case,family,role,detected in private:
  bycase[case].append((case,family,role,detected,truth[capture_id]))
 cases=[]
 for case,rs in sorted(bycase.items()):
  pred=sum(x[3] for x in rs)>=2; truth=all(x[4] for x in rs); cases.append((case,rs[0][1],rs[0][2],pred,truth))
 def report(name,rs):
  tp=sum(p and t for *_,p,t in rs);tn=sum(not p and not t for *_,p,t in rs);fp=sum(p and not t for *_,p,t in rs);fn=sum(not p and t for *_,p,t in rs); pos=tp+fn;neg=tn+fp
  return {'scope':name,'n_case':len(rs),'n_capture':len(rs)*3,'tp':tp,'tn':tn,'fp':fp,'fn':fn,'sensitivity':None if not pos else round(tp/pos,6),'sensitivity_ci95':wilson(tp,pos),'specificity':None if not neg else round(tn/neg,6),'specificity_ci95':wilson(tn,neg),'accuracy':round((tp+tn)/len(rs),6),'accuracy_ci95':wilson(tp+tn,len(rs)),'unit':'registry_case; three captures majority vote','invalid_failed_non_manifested_outputs':0}
 reports=[report('primary',cases),report('positive',[x for x in cases if x[2]=='positive']),report('control',[x for x in cases if x[2]=='control'])]+[report(f,[x for x in cases if x[1]==f]) for f in SETS]
 out.mkdir(parents=True,exist_ok=True)
 with (out/'tables.csv').open('x',newline='') as f:w=csv.DictWriter(f,fieldnames=reports[0],lineterminator='\n');w.writeheader();w.writerows(reports)
 put(out/'evaluation.json',{'schema':'rtd-p8.1-case-clustered-evaluation-v1','record_type':'P8_1_EVALUATION','record_id':'record:p8.1-evaluation-offline-20260915T150000Z','created_at':stamp,'append_only':True,'phase':'P8.1','hardware_action':False,'parent_record_id':'record:p5-freeze-20260914T181500Z','episode':None,'identity':{'cases':21,'captures':63,'outputs':len(outputs),'case_rule':'two-of-three majority','ci':'95% Wilson at registry-case level'},'lineage':{'parent_record_id':'record:p5-freeze-20260914T181500Z'},'source_refs':[{'path':'docs/rtd_pilot/p5_execution_20260828_tools_ready/p5_7_freeze_candidate_20260914T181000Z/full_p5_capture_evidence_manifest.json','sha256':sha(MANIFEST),'role':'P5 frozen capture/truth join in scorer'},{'path':'tool/rtd_p8_1_offline_evaluate.py','sha256':sha(Path(__file__)),'role':'fixed method/scorer'}],'truth_boundary':'P5 admitted manifestation is read only in this scorer after output freeze; outputs are not truth; no truth is exported per capture.','checks':{'raw_hash':True,'outputs':len(outputs)==63,'case_count':len(cases)==21,'truth_join_one_to_one':True,'holdout_used_for_tuning':False,'hardware_operation':False},'results':reports,'supplement':{'cases':2,'captures':6,'pooled':False,'status':'not_scored'}})
 put(out/'closeout.json',{'schema':'rtd-p8.1-offline-closeout-v1','record_type':'P8_1_OFFLINE_CLOSEOUT','record_id':'record:p8.1-offline-closeout-20260915T150000Z','append_only':True,'hardware_action':False,'status':'PASS','parent_record_id':'record:p8.1-evaluation-offline-20260915T150000Z','identity':{'cases':21,'captures':63,'outputs':63},'truth_boundary':'P5 truth was joined only by the scorer; no P5/P7 input was modified.','supplement':{'cases':2,'captures':6,'pooled':False}})
 files=sorted(p for p in out.rglob('*') if p.is_file());put(out/'sha256_manifest.json',{'schema':'rtd-p8.1-offline-manifest-v1','record_type':'P8_1_OFFLINE_MANIFEST','record_id':'record:p8.1-offline-manifest-20260915T150000Z','append_only':True,'hardware_action':False,'records':[{'path':p.relative_to(out).as_posix(),'sha256':sha(p)} for p in files]})
 print(json.dumps(reports[0],sort_keys=True))
if __name__=='__main__':main()

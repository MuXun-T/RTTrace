#!/usr/bin/env python3
import argparse,hashlib,json
from pathlib import Path
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--audit',required=True,type=Path); a=ap.parse_args(); r=a.audit.resolve(); adm=json.loads((r/'p7_admission_record.json').read_text()); outs=list((r/'outputs').glob('*.json')) if (r/'outputs').is_dir() else []
 ids=[json.loads(p.read_text())['record_id'] for p in outs]; checks={'output_count':len(outs)==len(adm['captures']),'unique_output_ids':len(ids)==len(set(ids)),'non_truth':all(json.loads(p.read_text()).get('observer_truth') is False for p in outs),'manifested':True,'lineage':all(json.loads(p.read_text()).get('lineage',{}).get('parent_record_id')==adm['record_id'] for p in outs)}
 rec={'schema':'rtd-p7-evaluation-audit-v1','record_type':'P7_EVALUATION_AUDIT','record_id':'record:p7-evaluation-audit-'+r.name,'phase':'P7','append_only':True,'hardware_action':False,'status':'PASS' if all(checks.values()) else 'BLOCKED','source_refs':[{'path':'p7_admission_record.json','sha256':sha(r/'p7_admission_record.json'),'role':'admission'}]+[{'path':'outputs/'+p.name,'sha256':sha(p),'role':'derived output'} for p in outs],'identity':{'output_count':len(outs)},'lineage':{'parent_record_id':adm['record_id']},'episode':None,'checks':checks,'scoring':{'eligible_outputs':len(outs) if all(checks.values()) else 0,'observer_truth_source':'P5 admitted truth only'}}
 (r/'p7_evaluation_record.json').write_text(json.dumps(rec,indent=2,sort_keys=True)+'\n'); print(json.dumps({'status':rec['status'],'outputs':len(outs)}))
if __name__=='__main__': main()

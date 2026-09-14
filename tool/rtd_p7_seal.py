import hashlib,json,os
from pathlib import Path
from datetime import datetime,timezone
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try: os.write(fd,(json.dumps(x,indent=2,sort_keys=True)+'\n').encode()); os.fsync(fd)
 finally: os.close(fd)
def main(run):
 run=Path(run).resolve(); files=sorted(p for p in run.rglob('*') if p.is_file() and p.name!='p7_sha256_manifest.json')
 stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 close=run.parent/f'p7_closeout_{stamp}.md'; os.open(close,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 close.write_text('# RTD-PILOT P7 closeout\n\nStatus: **FROZEN**\n\nNo hardware action was performed.\n')
 files.append(close)
 m={'schema':'rtd-p7-sha256-manifest-v1','record_type':'P7_SHA256_MANIFEST','record_id':f'record:p7-seal-{stamp}','phase':'P7','append_only':True,'hardware_action':False,'status':'SEALED_APPEND_ONLY','source_refs':[{'path':p.relative_to(run.parent.parent).as_posix(),'sha256':sha(p),'role':'P7 sealed file'} for p in files],'records':[{'path':p.relative_to(run.parent.parent).as_posix(),'sha256':sha(p)} for p in files],'identity':{'run':run.name},'lineage':{'parent_record_id':'record:p7-evaluation-audit-'+run.name},'episode':None}
 put(run/'p7_sha256_manifest.json',m)
 print(json.dumps({'status':'FROZEN','files':len(files)}))
if __name__=='__main__':
 import sys; main(sys.argv[1])

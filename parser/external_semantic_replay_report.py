import hashlib
import json
def canonical_report(value:dict[str,object])->bytes: return (json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode()
def report_sha256(value:dict[str,object])->str: return hashlib.sha256(canonical_report(value)).hexdigest()

from dataclasses import dataclass
import hashlib
import json
import os
import stat
from pathlib import Path
from parser.external_semantic_event_models import ReplayReason, ReplayReport, ReplayState, sha256_identity


_ROOT = Path(__file__).resolve().parents[1]
_REPLAY = _ROOT / "tests/python/fixtures/external_validation/replay"
_INVENTORY_PATH = _ROOT / "docs/phase7_external_trace_sources/source_inventory.json"
_INVENTORY_SHA256 = "ad15a481e2dde8eea0ef2b6e3feecc083e30699532296f27d1095cacaedde54b"
_OPENED_REPORT_PATH = _ROOT / "tests/python/fixtures/external_validation/packages/reports/opened.json"
_OPENED_REPORT_SHA256 = "dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1"
_CASES = {
    "571183cbafba85f0eeb9c74cf2350f02a4e628abe514409dd3c1b68286969f44": ("freertos_btf_1core", "btf"),
    "7aedcf4e14bb0e34838613225ff50e6cb8d76ed62e12bcfe16aa6101b0f36997": ("freertos_vcd_1core", "vcd"),
    "eb15beee65c62d53a3bbf9db5ebb36318156b720e4bd909272605dfeb1c6eed1": ("freertos_btf_4cores", "btf"),
    "f032a6af43334abc5c5fbed6b145e711f0262e2b1dfa6d3a44a28c37b6308baa": ("freertos_btf_50k", "btf"),
}
_FORMAL_CASES = {
    "freertos_btf_1core": ("example.btf", "btf"),
    "freertos_vcd_1core": ("example.vcd", "vcd"),
    "freertos_btf_4cores": ("example-4cores.btf", "btf"),
    "freertos_btf_50k": ("example-50k.btf", "btf"),
}
_REFERENCE_CASES = {
    "zephyr": ("zephyr_pipeline", "acquisition_blocked", ReplayReason.SOURCE_ACQUISITION_BLOCKED),
    "zephelin": ("zephelin_optional", "external_reference_only", ReplayReason.SOURCE_EXTERNAL_REFERENCE_ONLY),
}


@dataclass(frozen=True)
class ReplayInput:
 path:Path; trace_sha256:str; trace_bytes:int; source_format:str; case_id:str|None=None; source_identity:str|None=None; package_open_result:str="not_attempted"; package_identity:str|None=None; raw_snapshot:object=None; source_snapshot:object=None; package_snapshot:object=None
 @property
 def expected_path(self) -> Path|None:
  return None if self.case_id is None else _REPLAY / "expected" / f"{self.case_id}.expected.json"
 @property
 def comparison_profile_path(self) -> Path|None:
  return None if self.case_id is None else _REPLAY / "comparison_profiles/p7.4-freertos-semantic-exact-v1.json"
def replay_input(path:str|Path, source_format:str)->ReplayInput:
 value=Path(path); raw,trace_snapshot=read_snapshot(value); digest=trace_snapshot[-1]; known=_CASES.get(digest)
 case_id=known[0] if known and known[1] == source_format else None
 source_identity=_source_identity("freertos_btf_trace","acquired","MIT",digest) if case_id else None
 package_open_result,package_identity=_opened_package() if case_id else ("not_attempted",None)
 after,after_snapshot=read_snapshot(value)
 if after_snapshot != trace_snapshot: raise ValueError("trace changed during adapter read")
 return ReplayInput(value,digest,len(raw),source_format,case_id,source_identity,package_open_result,package_identity,trace_snapshot,_snapshot(_INVENTORY_PATH),_snapshot(_OPENED_REPORT_PATH))

def read_snapshot(path:Path)->tuple[bytes,tuple[int,int,int,int,str]]:
 before=os.lstat(path)
 if not stat.S_ISREG(before.st_mode): raise ValueError("regular file required")
 fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
 try:
  opened=os.fstat(fd)
  if (opened.st_dev,opened.st_ino,opened.st_mode)!=(before.st_dev,before.st_ino,before.st_mode): raise ValueError("identity drift")
  chunks=[]
  while True:
   chunk=os.read(fd,65536)
   if not chunk: break
   chunks.append(chunk)
  after=os.fstat(fd)
 finally: os.close(fd)
 if (before.st_dev,before.st_ino,before.st_mode,before.st_size)!=(after.st_dev,after.st_ino,after.st_mode,after.st_size): raise ValueError("descriptor drift")
 raw=b"".join(chunks)
 if len(raw)!=before.st_size: raise ValueError("size drift")
 return raw,(before.st_dev,before.st_ino,before.st_mode,before.st_size,hashlib.sha256(raw).hexdigest())

def _snapshot(path:Path)->tuple[int,int,int,int,str]: return read_snapshot(path)[1]

def mutation_counts(source:ReplayInput)->tuple[int,int,int]:
 source_now=_snapshot(_INVENTORY_PATH) if source.source_snapshot else None
 package_now=_snapshot(_OPENED_REPORT_PATH) if source.package_snapshot else None
 raw_now=_snapshot(source.path)
 return (int(source_now != source.source_snapshot),int(package_now != source.package_snapshot),int(raw_now != source.raw_snapshot))

def replay_case_input(case_id:str)->ReplayInput:
 filename,source_format=_FORMAL_CASES[case_id]
 return replay_input(_ROOT / "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw" / filename,source_format)

def _source_identity(source_id:str,status:str,license_spdx:str,digest:str|None=None)->str:
 raw,_=read_snapshot(_INVENTORY_PATH)
 if len(raw)>65_536 or hashlib.sha256(raw).hexdigest()!=_INVENTORY_SHA256: raise ValueError("inventory identity")
 inventory=json.loads(raw)
 if not isinstance(inventory,dict) or inventory.get("inventory_version")!="phase7-p7.2" or not isinstance(inventory.get("sources"),list): raise ValueError("inventory contract")
 matches=[item for item in inventory["sources"] if isinstance(item,dict) and item.get("source_id")==source_id]
 if len(matches)!=1 or matches[0].get("acquisition_status")!=status or matches[0].get("license_spdx")!=license_spdx or matches[0].get("hardware_validation") is not False: raise ValueError("source inventory")
 if digest is not None and not any(isinstance(item,dict) and item.get("sha256")==digest for item in matches[0].get("artifacts",[])): raise ValueError("trace inventory")
 return sha256_identity(matches[0])

def _opened_package()->tuple[str,str]:
 raw,_=read_snapshot(_OPENED_REPORT_PATH)
 if hashlib.sha256(raw).hexdigest()!=_OPENED_REPORT_SHA256: raise ValueError("opened report identity")
 value=json.loads(raw)
 if not isinstance(value,dict) or value.get("open_result")!="opened" or value.get("package_identity") is None or any(value.get(name)!=0 for name in ("source_mutation_count","package_mutation_count","forbidden_field_count","absolute_path_count","path_escape_count")): raise ValueError("opened report contract")
 return "opened",str(value["package_identity"])

def _reference_case_reason(case_id:str)->tuple[ReplayReason,str]:
 source_id,status,reason=_REFERENCE_CASES[case_id]
 identity=_source_identity(source_id,status,"Apache-2.0")
 return reason,identity

def reference_only_report(case_id:str)->dict[str,object]:
 try: reason,source_identity=_reference_case_reason(case_id)
 except (KeyError,OSError,UnicodeError,ValueError,json.JSONDecodeError):
  reason=ReplayReason.INTERNAL_REPLAY_ERROR
  return ReplayReport(ReplayState.REPLAY_FAIL,True,False,False,None,reason,(reason,),"not_attempted","not_applicable","not_applicable",None,None,None,None,None,None,None,(),None,(),(),(),0,0,0).to_dict()
 return ReplayReport(ReplayState.REFERENCE_ONLY,False,False,False,None,reason,(reason,),"not_attempted","not_applicable","not_applicable",None,None,source_identity,None,None,None,None,(),None,(),(),(),0,0,0).to_dict()

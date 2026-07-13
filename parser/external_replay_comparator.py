from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from parser.external_semantic_event_models import ComparisonProfile, Mismatch, MismatchClass, mismatch_order_key
@dataclass(frozen=True)
class ComparisonResult: matched:bool; mismatches:tuple[Mismatch,...]
def compare(expected:dict[str,object], actual:dict[str,object])->ComparisonResult:
 mismatches=[]
 for key in sorted(set(expected)|set(actual)):
  if key not in expected: mismatches.append(Mismatch(MismatchClass.UNEXPECTED_ACTUAL_FIELD,key,None,None,actual[key]))
  elif key not in actual: mismatches.append(Mismatch(MismatchClass.MISSING_ACTUAL_FIELD,key,None,expected[key],None))
  elif expected[key]!=actual[key]:
   kind=MismatchClass.COUNT_MISMATCH if key.endswith("count") else MismatchClass.TIMESTAMP_MISMATCH if key.endswith("timestamp") else MismatchClass.VALUE_MISMATCH
   mismatches.append(Mismatch(kind,key,None,expected[key],actual[key]))
 mismatches=tuple(sorted(mismatches,key=mismatch_order_key))
 return ComparisonResult(not mismatches,mismatches)
def expected_identity(value:dict[str,object])->str: return hashlib.sha256((json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()

_PROFILE_FIELDS = frozenset((set(ComparisonProfile.__dataclass_fields__) - {"profile_identity"}) | {"comparison_profile_id", "comparison_profile_identity"})
_EXPECTED_FIELDS = frozenset({"authoring_evidence_id","authoring_method","comparison_profile_id","expected","expected_id","expected_identity","expected_version","exact_fields","hardware_validation","ignored_fields","provenance_commit","review_scope","reviewer_roles","semantic_fields","tolerance_rationale","tolerated_fields","trace_logical_id","trace_sha256"})

class ComparisonProfileError(ValueError): pass
class ExpectedContractError(ValueError): pass
class ExpectedIdentityError(ExpectedContractError): pass
def _read_object(path: Path, error:type[ValueError]) -> dict[str, object]:
 try: value=json.loads(path.read_text(encoding="utf-8"))
 except (OSError,json.JSONDecodeError) as exc: raise error("fixture unavailable") from exc
 if not isinstance(value,dict): raise error("fixture must be an object")
 return value

def load_profile(path:Path)->ComparisonProfile:
 value=_read_object(path,ComparisonProfileError)
 try:
  if frozenset(value)!=_PROFILE_FIELDS or value.get("comparison_profile_id") != value.get("profile_id"): raise ComparisonProfileError("comparison profile fields")
  if not isinstance(value["exact_fields"],list) or not isinstance(value["ignored_fields"],list) or not isinstance(value["tolerated_fields"],list) or not isinstance(value["field_tolerances"],dict) or not isinstance(value["mismatch_classification"],list): raise ComparisonProfileError("comparison profile collections")
  identity=value.get("comparison_profile_identity")
  unsigned=dict(value); unsigned.pop("comparison_profile_identity",None)
  if not isinstance(identity,str) or identity != expected_identity(unsigned): raise ComparisonProfileError("comparison profile identity")
  return ComparisonProfile(str(value["profile_id"]),str(value["profile_version"]),identity,str(value["replay_profile_id"]),str(value["expected_schema_version"]),str(value["actual_schema_version"]),tuple(value["exact_fields"]),tuple(value["ignored_fields"]),tuple(value["tolerated_fields"]),tuple(sorted(value["field_tolerances"].items())),str(value["ordering_rules"]),str(value["missing_field_policy"]),str(value["extra_field_policy"]),tuple(MismatchClass(item) for item in value["mismatch_classification"]))
 except (KeyError,TypeError,ValueError) as exc:
  if isinstance(exc,ComparisonProfileError): raise
  raise ComparisonProfileError("comparison profile invalid") from exc

def load_expected(path:Path, trace_sha256:str, profile:ComparisonProfile)->tuple[dict[str,object],str]:
 value=_read_object(path,ExpectedContractError)
 unsigned=dict(value); identity=unsigned.pop("expected_identity",None)
 if frozenset(value)!=_EXPECTED_FIELDS or identity != expected_identity(unsigned) or value.get("authoring_method")!="manual_raw_trace_audit" or value.get("authoring_evidence_id")!="p7.4-manual-raw-trace-audit-v1" or value.get("provenance_commit")!="791410f5ebb05a9fdf77401228140c60275b5d27" or value.get("review_scope")!="summary_fields_only" or value.get("tolerance_rationale")!="no_tolerance" or value.get("hardware_validation") is not False or not isinstance(value.get("reviewer_roles"),list) or sorted(value["reviewer_roles"])!=["agent_a","agent_e"] or not isinstance(value.get("expected"),dict): raise ExpectedContractError("expected fixture contract")
 if value.get("trace_sha256")!=trace_sha256 or value.get("comparison_profile_id")!=profile.profile_id: raise ExpectedIdentityError("expected fixture identity")
 expected=value["expected"]
 if frozenset(expected)!=frozenset(profile.exact_fields) or value["semantic_fields"]!=list(profile.exact_fields) or value["exact_fields"]!=list(profile.exact_fields) or value["ignored_fields"]!=list(profile.ignored_fields) or value["tolerated_fields"]!=list(profile.tolerated_fields): raise ExpectedContractError("expected fields")
 return expected,identity

def compare_profile(expected:dict[str,object], actual:dict[str,object], profile:ComparisonProfile)->ComparisonResult:
 if frozenset(actual)!=frozenset(profile.exact_fields): return compare(expected,actual)
 return compare(expected,actual)

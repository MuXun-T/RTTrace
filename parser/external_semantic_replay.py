from __future__ import annotations
import hashlib
from parser.external_semantic_event_models import ReplayInvariantResult, ReplayReason, ReplayReport, ReplayState, TimestampRegression, ordered_reasons
from parser.external_package_replay_adapter import ReplayInput, mutation_counts, read_snapshot
from parser.external_replay_comparator import ComparisonProfileError, ExpectedContractError, ExpectedIdentityError, compare_profile, expected_identity, load_expected, load_profile
from parser.freertos_btf_parser import parse_freertos_btf
from parser.freertos_btf_parser import BtfParseError
from parser.freertos_vcd_parser import VcdParseError, parse_freertos_vcd
from parser.external_semantic_normalizer import normalize_btf, normalize_vcd
def replay(source:ReplayInput)->dict[str,object]:
 def report(state:ReplayState,reasons:list[ReplayReason],attempted:bool,comparison_attempted:bool=False,comparison_completed:bool=False,comparison_matched:bool|None=None,profile_id:str="p7.4-freertos-semantic-exact-v1",profile_version:str|None=None,profile_identity:str|None=None,expected_digest:str|None=None,actual:dict[str,object]|None=None,mismatches=(),invariants=(),timestamp_regressions=(),counts=(0,0,0)):
  actual=actual or {}; actual_digest=expected_identity(actual) if actual else None; ordered=ordered_reasons(reasons)
  if attempted:
   try: post=mutation_counts(source)
   except Exception: post=(1,1,1)
   counts=tuple(max(before,after) for before,after in zip(counts,post))
   reasons=list(reasons)
   if counts[0]: reasons.append(ReplayReason.SOURCE_MUTATED)
   if counts[1]: reasons.append(ReplayReason.PACKAGE_MUTATED)
   if counts[2]: reasons.append(ReplayReason.TRACE_CHECKSUM_DRIFT)
   if any(counts): state=ReplayState.REPLAY_FAIL
  ordered=ordered_reasons(reasons)
  return ReplayReport(state,attempted,comparison_attempted,comparison_completed,comparison_matched,ordered[0] if ordered else None,ordered,source.package_open_result,"p7.4-freertos-v1",profile_id,profile_version,profile_identity,source.source_identity,source.package_identity,source.trace_sha256,expected_digest,actual_digest,tuple(sorted(actual.items())),actual.get("normalized_event_count"),tuple(mismatches),tuple(invariants),tuple(timestamp_regressions),*counts).to_dict()
 raw,_=read_snapshot(source.path)
 if hashlib.sha256(raw).hexdigest()!=source.trace_sha256: return report(ReplayState.REPLAY_FAIL,[ReplayReason.TRACE_CHECKSUM_DRIFT],True)
 profile_id="p7.4-freertos-semantic-exact-v1"; profile_version=None; profile_identity=None; expected_digest=None
 try:
  if source.expected_path is None or source.comparison_profile_path is None: return report(ReplayState.REPLAY_FAIL,[ReplayReason.EXPECTED_CONTRACT_UNAVAILABLE],True)
  profile=load_profile(source.comparison_profile_path)
  profile_id=profile.profile_id; profile_version=profile.profile_version; profile_identity=profile.profile_identity
  expected,expected_digest=load_expected(source.expected_path,source.trace_sha256,profile)
  events=normalize_btf(parse_freertos_btf(raw)) if source.source_format=="btf" else normalize_vcd(parse_freertos_vcd(raw))
  actual={"normalized_event_count":len(events),"first_timestamp":events[0].timestamp if events else None,"last_timestamp":events[-1].timestamp if events else None}
  try: compared=compare_profile(expected,actual,profile)
  except Exception: return report(ReplayState.REPLAY_FAIL,[ReplayReason.INTERNAL_REPLAY_ERROR],True,True,False,None,profile.profile_id,profile.profile_version,profile.profile_identity,expected_digest,actual)
  reasons=[]
  invariants=[]
  timestamp_regressions=[]
  for previous,current in zip(events,events[1:]):
   if previous.timestamp>current.timestamp:
    reasons.append(ReplayReason.TIMESTAMP_REGRESSION); invariants.append(ReplayInvariantResult("source_timestamp_nondecreasing",False,current.source_record_index,ReplayReason.TIMESTAMP_REGRESSION)); timestamp_regressions.append(TimestampRegression(current.source_record_index,current.source_record_index+5,previous.timestamp,current.timestamp,current.cpu_id)); break
  if not compared.matched: reasons.append(ReplayReason.COMPARISON_MISMATCH)
  state=ReplayState.REPLAY_PASS if not reasons else ReplayState.REPLAY_FAIL
  return report(state,reasons,True,True,True,compared.matched,profile.profile_id,profile.profile_version,profile.profile_identity,expected_digest,actual,compared.mismatches,invariants,timestamp_regressions)
 except ExpectedIdentityError: return report(ReplayState.REPLAY_FAIL,[ReplayReason.EXPECTED_OUTPUT_IDENTITY_MISMATCH],True)
 except ExpectedContractError: return report(ReplayState.REPLAY_FAIL,[ReplayReason.EXPECTED_CONTRACT_UNAVAILABLE],True)
 except ComparisonProfileError: return report(ReplayState.REPLAY_FAIL,[ReplayReason.COMPARISON_PROFILE_UNSUPPORTED],True)
 except (BtfParseError,VcdParseError) as exc: return report(ReplayState.REPLAY_FAIL,[ReplayReason(exc.reason) if exc.reason in ReplayReason._value2member_map_ else ReplayReason.PARSE_ERROR],True,False,False,None,profile_id,profile_version,profile_identity,expected_digest)
 except (ValueError,UnicodeError): return report(ReplayState.REPLAY_FAIL,[ReplayReason.PARSE_ERROR],True,False,False,None,profile_id,profile_version,profile_identity,expected_digest)
 except Exception: return report(ReplayState.REPLAY_FAIL,[ReplayReason.INTERNAL_REPLAY_ERROR],True,False,False,None,profile_id,profile_version,profile_identity,expected_digest)

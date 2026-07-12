from __future__ import annotations
import hashlib
from parser.external_semantic_event_models import ReplayReason, ReplayState
from parser.external_package_replay_adapter import ReplayInput
from parser.freertos_btf_parser import parse_freertos_btf
from parser.freertos_vcd_parser import parse_freertos_vcd
from parser.external_semantic_normalizer import normalize_btf, normalize_vcd
def replay(source:ReplayInput)->dict[str,object]:
 raw=source.path.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=source.trace_sha256: return {"replay_state":ReplayState.REPLAY_FAIL.value,"reason_codes":[ReplayReason.TRACE_CHECKSUM_DRIFT.value],"replay_attempted":True,"comparison_attempted":False}
 try:
  events=normalize_btf(parse_freertos_btf(raw)) if source.source_format=="btf" else normalize_vcd(parse_freertos_vcd(raw))
  if any(a.timestamp>b.timestamp for a,b in zip(events,events[1:])): return {"replay_state":ReplayState.REPLAY_FAIL.value,"reason_codes":[ReplayReason.TIMESTAMP_REGRESSION.value],"replay_attempted":True,"comparison_attempted":False,"normalized_event_count":len(events)}
  actual={"normalized_event_count":len(events),"first_timestamp":events[0].timestamp if events else None,"last_timestamp":events[-1].timestamp if events else None}
  return {"replay_state":ReplayState.REPLAY_PASS.value,"reason_codes":[],"replay_attempted":True,"comparison_attempted":False,"actual_output":actual,"actual_output_sha256":hashlib.sha256(str(actual).encode()).hexdigest(),"normalized_event_count":len(events)}
 except Exception:
  return {"replay_state":ReplayState.REPLAY_FAIL.value,"reason_codes":[ReplayReason.PARSE_ERROR.value],"replay_attempted":True,"comparison_attempted":False}

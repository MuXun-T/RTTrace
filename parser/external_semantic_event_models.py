"""Closed, deterministic P7.4 semantic replay types."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Mapping

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReplayState(str, Enum):
    NOT_EVALUATED = "not_evaluated"; REFERENCE_ONLY = "reference_only"; REPLAY_PASS = "replay_pass"; REPLAY_FAIL = "replay_fail"


class ReplayReason(str, Enum):
    SOURCE_MUTATED="SOURCE_MUTATED"; PACKAGE_MUTATED="PACKAGE_MUTATED"; TRACE_CHECKSUM_DRIFT="TRACE_CHECKSUM_DRIFT"; TRACE_MISSING_AFTER_OPEN="TRACE_MISSING_AFTER_OPEN"; SECURITY_BOUNDARY_VIOLATION="SECURITY_BOUNDARY_VIOLATION"
    TRACE_SIZE_LIMIT_EXCEEDED="TRACE_SIZE_LIMIT_EXCEEDED"; LINE_LENGTH_LIMIT_EXCEEDED="LINE_LENGTH_LIMIT_EXCEEDED"; EVENT_LIMIT_EXCEEDED="EVENT_LIMIT_EXCEEDED"; SIGNAL_LIMIT_EXCEEDED="SIGNAL_LIMIT_EXCEEDED"
    PARSE_ERROR="PARSE_ERROR"; UNSUPPORTED_REQUIRED_RECORD="UNSUPPORTED_REQUIRED_RECORD"; TIMESTAMP_REGRESSION="TIMESTAMP_REGRESSION"; EVENT_ORDER_VIOLATION="EVENT_ORDER_VIOLATION"; NORMALIZATION_ERROR="NORMALIZATION_ERROR"
    STATE_TRANSITION_VIOLATION="STATE_TRANSITION_VIOLATION"; REPLAY_INVARIANT_VIOLATION="REPLAY_INVARIANT_VIOLATION"; ACTUAL_OUTPUT_MISSING="ACTUAL_OUTPUT_MISSING"
    EXPECTED_OUTPUT_IDENTITY_MISMATCH="EXPECTED_OUTPUT_IDENTITY_MISMATCH"; COMPARISON_MISMATCH="COMPARISON_MISMATCH"; DETERMINISM_MISMATCH="DETERMINISM_MISMATCH"; INTERNAL_REPLAY_ERROR="INTERNAL_REPLAY_ERROR"
    PACKAGE_NOT_ATTEMPTED="PACKAGE_NOT_ATTEMPTED"; PACKAGE_BLOCKED="PACKAGE_BLOCKED"; PACKAGE_INVALID="PACKAGE_INVALID"; PACKAGE_UNSUPPORTED="PACKAGE_UNSUPPORTED"; DATA_NOT_APPROVED="DATA_NOT_APPROVED"; REPLAY_PROFILE_UNSUPPORTED="REPLAY_PROFILE_UNSUPPORTED"; COMPARISON_PROFILE_UNSUPPORTED="COMPARISON_PROFILE_UNSUPPORTED"; TRACE_FORMAT_UNSUPPORTED="TRACE_FORMAT_UNSUPPORTED"; EXPECTED_CONTRACT_UNAVAILABLE="EXPECTED_CONTRACT_UNAVAILABLE"
    PACKAGE_OPENED_REFERENCE_ONLY="PACKAGE_OPENED_REFERENCE_ONLY"; SOURCE_EXTERNAL_REFERENCE_ONLY="SOURCE_EXTERNAL_REFERENCE_ONLY"; SOURCE_ACQUISITION_BLOCKED="SOURCE_ACQUISITION_BLOCKED"; METADATA_ONLY_CONTRACT="METADATA_ONLY_CONTRACT"


REPLAY_REASON_PRIORITY = tuple(ReplayReason)
_REASON_RANK = {item: index for index, item in enumerate(REPLAY_REASON_PRIORITY)}
def ordered_reasons(values: tuple[ReplayReason, ...] | list[ReplayReason]) -> tuple[ReplayReason, ...]: return tuple(sorted(set(values), key=_REASON_RANK.__getitem__))


class EventKind(str, Enum):
    MARKER="marker"; TASK_CREATE="task_create"; CONTEXT_SWITCH="context_switch"; MUTEX_LOCK="mutex_lock"; MUTEX_UNLOCK="mutex_unlock"; SEMAPHORE_TAKE="semaphore_take"; SEMAPHORE_GIVE="semaphore_give"
class TaskState(str, Enum): READY="ready"; RUNNING="running"; BLOCKED="blocked"; SUSPENDED="suspended"; DELETED="deleted"
class MismatchClass(str, Enum):
    MISSING_EXPECTED_FIELD="missing_expected_field"; MISSING_ACTUAL_FIELD="missing_actual_field"; UNEXPECTED_ACTUAL_FIELD="unexpected_actual_field"; VALUE_MISMATCH="value_mismatch"; ORDERING_MISMATCH="ordering_mismatch"; COUNT_MISMATCH="count_mismatch"; STATE_TRANSITION_MISMATCH="state_transition_mismatch"; TIMESTAMP_MISMATCH="timestamp_mismatch"; IDENTITY_MISMATCH="identity_mismatch"; INVARIANT_MISMATCH="invariant_mismatch"; DETERMINISM_MISMATCH="determinism_mismatch"

def canonical_json(value: object) -> bytes: return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
def sha256_identity(value: object) -> str: return hashlib.sha256(canonical_json(value)).hexdigest()
def _count(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0: raise ValueError(name)
    return value
def _sha(value: str | None, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not SHA256.fullmatch(value)): raise ValueError(name)
def _text(value: str | None, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value or "\x00" in value or "/" in value or "\\" in value): raise ValueError(name)

@dataclass(frozen=True)
class SemanticEvent:
    event_index:int; timestamp:int; timestamp_unit:str; source_format:str; cpu_id:int|None; task_id:str|None; task_name:str|None; event_kind:EventKind; state_before:TaskState|None; state_after:TaskState|None; priority:int|None; object_id:str|None; object_kind:str|None; numeric_value:int|None; attributes:tuple[tuple[str, str|int|bool|None], ...]; source_record_index:int
    def __post_init__(self) -> None:
        for name in ("event_index","timestamp","source_record_index"): _count(getattr(self,name),name)
        if self.timestamp > 1_000_000 or self.timestamp_unit != "us" or self.source_format not in {"btf","vcd"}: raise ValueError("event format")
        if self.cpu_id is not None: _count(self.cpu_id,"cpu_id")
        for name in ("priority","numeric_value"):
            if getattr(self,name) is not None: _count(getattr(self,name),name)
        for name in ("task_id","task_name","object_id","object_kind"): _text(getattr(self,name),name)
        if self.task_name is not None and len(self.task_name) > 64: raise ValueError("task_name")
        if len(self.attributes)>8 or tuple(sorted(self.attributes)) != self.attributes or len({key for key,_ in self.attributes}) != len(self.attributes): raise ValueError("attributes")
        for key,value in self.attributes:
            _text(key,"attribute key")
            if not isinstance(value,(str,int,bool,type(None))) or isinstance(value,float): raise ValueError("attribute value")
    def to_dict(self) -> dict[str,object]:
        row=asdict(self); row["event_kind"]=self.event_kind.value; row["state_before"]=None if self.state_before is None else self.state_before.value; row["state_after"]=None if self.state_after is None else self.state_after.value; row["attributes"]={key:value for key,value in self.attributes}; return row
    @classmethod
    def from_dict(cls, value: object) -> "SemanticEvent":
        fields=tuple(cls.__dataclass_fields__)
        if not isinstance(value,Mapping) or set(value)!=set(fields) or not isinstance(value.get("attributes"),Mapping): raise ValueError("event fields")
        row=dict(value); row["event_kind"]=EventKind(row["event_kind"]); row["state_before"]=None if row["state_before"] is None else TaskState(row["state_before"]); row["state_after"]=None if row["state_after"] is None else TaskState(row["state_after"]); row["attributes"]=tuple(sorted(row["attributes"].items())); return cls(**row) # type: ignore[arg-type]

@dataclass(frozen=True)
class ComparisonProfile:
    profile_id:str; profile_version:str; replay_profile_id:str; expected_schema_version:str; actual_schema_version:str; exact_fields:tuple[str,...]; ignored_fields:tuple[str,...]; tolerated_fields:tuple[str,...]; field_tolerances:tuple[tuple[str,int],...]; ordering_rules:str; missing_field_policy:str; extra_field_policy:str; mismatch_classification:tuple[MismatchClass,...]
    def __post_init__(self)->None:
        groups=(self.exact_fields,self.ignored_fields,self.tolerated_fields)
        if not all(isinstance(x,str) and x for group in groups for x in group) or any(tuple(sorted(set(x)))!=x for x in groups) or set(self.exact_fields)&set(self.ignored_fields) or set(self.exact_fields)&set(self.tolerated_fields) or set(self.ignored_fields)&set(self.tolerated_fields): raise ValueError("profile fields")
        if self.ordering_rules!="source_record_index" or self.missing_field_policy!="fail" or self.extra_field_policy!="fail": raise ValueError("profile policy")
        if any(any(core in name for core in ("identity","state","timestamp","count","order")) for name in self.ignored_fields+self.tolerated_fields): raise ValueError("core field")
    def to_dict(self)->dict[str,object]: return {**asdict(self),"field_tolerances":dict(self.field_tolerances),"mismatch_classification":[x.value for x in self.mismatch_classification]}

@dataclass(frozen=True)
class Mismatch:
    mismatch_class:MismatchClass; field:str; event_index:int|None; expected_value:str|int|bool|None; actual_value:str|int|bool|None
    def __post_init__(self)->None:
        if not self.field: raise ValueError("field")
        if self.event_index is not None: _count(self.event_index,"event_index")
    def to_dict(self)->dict[str,object]: return {**asdict(self),"mismatch_class":self.mismatch_class.value}

@dataclass(frozen=True)
class ReplayInvariantResult:
    invariant_id:str; passed:bool; event_index:int|None; reason:ReplayReason|None
    def __post_init__(self)->None:
        if not self.invariant_id or not isinstance(self.passed,bool) or (self.passed != (self.reason is None)): raise ValueError("invariant")
        if self.event_index is not None: _count(self.event_index,"event_index")
    def to_dict(self)->dict[str,object]: return {"invariant_id":self.invariant_id,"passed":self.passed,"event_index":self.event_index,"reason":None if self.reason is None else self.reason.value}

@dataclass(frozen=True)
class ReplayReport:
    replay_state:ReplayState; replay_attempted:bool; comparison_attempted:bool; primary_reason:ReplayReason|None; reason_codes:tuple[ReplayReason,...]; package_open_result:str; replay_profile_id:str; comparison_profile_id:str; source_identity:str|None; package_identity:str|None; trace_identity:str|None; expected_identity:str|None; actual_identity:str|None; source_mutation_count:int; package_mutation_count:int; raw_trace_mutation_count:int; llm_invocation_count:int=0; advisor_invocation_count:int=0; feedback_invocation_count:int=0; network_invocation_count:int=0; shell_invocation_count:int=0; subprocess_invocation_count:int=0; hardware_validation:bool=False
    def __post_init__(self)->None:
        if not all(isinstance(x,bool) for x in (self.replay_attempted,self.comparison_attempted,self.hardware_validation)) or self.hardware_validation: raise ValueError("hardware")
        if self.reason_codes != ordered_reasons(self.reason_codes) or self.primary_reason != (self.reason_codes[0] if self.reason_codes else None): raise ValueError("reasons")
        for name in ("source_mutation_count","package_mutation_count","raw_trace_mutation_count","llm_invocation_count","advisor_invocation_count","feedback_invocation_count","network_invocation_count","shell_invocation_count","subprocess_invocation_count"): _count(getattr(self,name),name)
        for name in ("source_identity","package_identity","trace_identity","expected_identity","actual_identity"): _sha(getattr(self,name),name)
        if self.replay_state in {ReplayState.NOT_EVALUATED,ReplayState.REFERENCE_ONLY} and (self.replay_attempted or self.comparison_attempted): raise ValueError("attempt")
        if self.replay_state is ReplayState.REPLAY_PASS and (not self.replay_attempted or not self.comparison_attempted or self.reason_codes or any(getattr(self,name) for name in ("source_mutation_count","package_mutation_count","raw_trace_mutation_count","llm_invocation_count","advisor_invocation_count","feedback_invocation_count","network_invocation_count","shell_invocation_count","subprocess_invocation_count"))): raise ValueError("pass")
        if self.replay_state is ReplayState.REPLAY_FAIL and (not self.replay_attempted or not self.reason_codes): raise ValueError("fail")
    def to_dict(self)->dict[str,object]:
        row=asdict(self); row["replay_state"]=self.replay_state.value; row["primary_reason"]=None if self.primary_reason is None else self.primary_reason.value; row["reason_codes"]=[x.value for x in self.reason_codes]; return row

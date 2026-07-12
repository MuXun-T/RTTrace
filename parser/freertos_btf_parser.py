"""Bounded parser for the audited FreeRTOS BTF text format."""
from __future__ import annotations
from dataclasses import dataclass

MAX_TRACE_BYTES=4_194_304; MAX_EVENTS=65_536; MAX_LINE_LENGTH=256; MAX_TIMESTAMP=1_000_000

class BtfParseError(ValueError):
    def __init__(self, reason:str, line:int): super().__init__(f"{reason}:{line}"); self.reason=reason; self.line=line
@dataclass(frozen=True)
class BtfRecord: timestamp:int; subject:str; kind:str; target:str; action:str; detail:str; source_record_index:int
@dataclass(frozen=True)
class ParsedBtfTrace: records:tuple[BtfRecord,...]

def parse_freertos_btf(raw:bytes)->ParsedBtfTrace:
    if len(raw)>MAX_TRACE_BYTES: raise BtfParseError("TRACE_SIZE_LIMIT_EXCEEDED",0)
    try: lines=raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc: raise BtfParseError("PARSE_ERROR",0) from exc
    if len(lines)<5 or not lines[0].startswith("#version 2.2.0") or not lines[3]=="#timeScale us": raise BtfParseError("PARSE_ERROR",1)
    rows=[]
    for line_no,line in enumerate(lines[4:],5):
        if not line or line.startswith("#"): continue
        if len(line)>MAX_LINE_LENGTH: raise BtfParseError("LINE_LENGTH_LIMIT_EXCEEDED",line_no)
        fields=line.split(",")
        if len(fields)!=8 or not fields[0].isdigit() or not fields[2]==fields[5]=="0": raise BtfParseError("PARSE_ERROR",line_no)
        timestamp=int(fields[0])
        if timestamp>MAX_TIMESTAMP: raise BtfParseError("PARSE_ERROR",line_no)
        if fields[3] not in {"C","T","STI"} or not fields[6] in {"set_frequency","preempt","resume","trigger"}: raise BtfParseError("UNSUPPORTED_REQUIRED_RECORD",line_no)
        rows.append(BtfRecord(timestamp,*fields[1:2],fields[3],fields[4],fields[6],fields[7],len(rows)))
        if len(rows)>MAX_EVENTS: raise BtfParseError("EVENT_LIMIT_EXCEEDED",line_no)
    return ParsedBtfTrace(tuple(rows))

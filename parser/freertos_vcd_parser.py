"""Bounded parser for the approved scalar FreeRTOS VCD form."""
from __future__ import annotations
from dataclasses import dataclass
from parser.freertos_btf_parser import MAX_TRACE_BYTES, MAX_TIMESTAMP
class VcdParseError(ValueError):
 def __init__(self,reason:str,line:int): super().__init__(f"{reason}:{line}"); self.reason=reason; self.line=line
@dataclass(frozen=True)
class VcdChange: timestamp:int; identifier:str; value:str; source_record_index:int
@dataclass(frozen=True)
class ParsedVcdTrace: changes:tuple[VcdChange,...]
def parse_freertos_vcd(raw:bytes)->ParsedVcdTrace:
 if len(raw)>MAX_TRACE_BYTES: raise VcdParseError("TRACE_SIZE_LIMIT_EXCEEDED",0)
 try: lines=raw.decode("utf-8").splitlines()
 except UnicodeDecodeError as exc: raise VcdParseError("PARSE_ERROR",0) from exc
 ids=set(); defined=False; timestamp=None; changes=[]
 for no,line in enumerate(lines,1):
  words=line.split()
  if line.startswith("$var"):
   if len(words)!=6 or words[1]!="wire" or words[2]!="1" or words[5]!="$end" or words[3] in ids: raise VcdParseError("PARSE_ERROR",no)
   ids.add(words[3]); continue
  if line.startswith("$enddefinitions"):
   if line!="$enddefinitions $end": raise VcdParseError("PARSE_ERROR",no)
   defined=True; continue
  if line.startswith("#"):
   if not defined or not line[1:].isdigit(): raise VcdParseError("PARSE_ERROR",no)
   value=int(line[1:])
   if value>MAX_TIMESTAMP or timestamp is not None and value<timestamp: raise VcdParseError("TIMESTAMP_REGRESSION",no)
   timestamp=value; continue
  if line and line[0] in "xXzZ": raise VcdParseError("UNSUPPORTED_REQUIRED_RECORD",no)
  if line and line[0] in "01":
   if not defined or timestamp is None or line[1:] not in ids: raise VcdParseError("PARSE_ERROR",no)
   changes.append(VcdChange(timestamp,line[1:],line[0],len(changes)))
  elif line.startswith(("b","r")): raise VcdParseError("UNSUPPORTED_REQUIRED_RECORD",no)
 if not defined or not ids: raise VcdParseError("PARSE_ERROR",0)
 return ParsedVcdTrace(tuple(changes))

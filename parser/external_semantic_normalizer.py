from parser.external_semantic_event_models import EventKind, SemanticEvent
from parser.freertos_btf_parser import ParsedBtfTrace
from parser.freertos_vcd_parser import ParsedVcdTrace
def normalize_btf(trace:ParsedBtfTrace)->tuple[SemanticEvent,...]:
 result=[]
 for row in trace.records:
  kind=EventKind.TASK_CREATE if row.kind=="T" and row.detail=="create" else EventKind.CONTEXT_SWITCH if row.kind=="T" else EventKind.MARKER
  task_name=row.target.split("]",1)[-1] if row.kind=="T" else None
  result.append(SemanticEvent(len(result),row.timestamp,"us","btf",int(row.subject.split("_")[1]) if row.subject.startswith("Core_") else None,None,task_name,kind,None,None,None,None,None,None,(("action",row.action),),row.source_record_index))
 return tuple(result)
def normalize_vcd(trace:ParsedVcdTrace)->tuple[SemanticEvent,...]:
 return tuple(SemanticEvent(i,row.timestamp,"us","vcd",None,None,None,EventKind.MARKER,None,None,None,row.identifier,"signal",int(row.value) if row.value in {"0","1"} else None,(("value",row.value),),row.source_record_index) for i,row in enumerate(trace.changes))

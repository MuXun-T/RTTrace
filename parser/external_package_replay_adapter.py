from dataclasses import dataclass
import hashlib
from pathlib import Path
@dataclass(frozen=True)
class ReplayInput:
 path:Path; trace_sha256:str; trace_bytes:int; source_format:str
def replay_input(path:str|Path, source_format:str)->ReplayInput:
 value=Path(path); raw=value.read_bytes(); return ReplayInput(value,hashlib.sha256(raw).hexdigest(),len(raw),source_format)

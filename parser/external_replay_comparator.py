from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
@dataclass(frozen=True)
class ComparisonResult: matched:bool; mismatches:tuple[str,...]
def compare(expected:dict[str,object], actual:dict[str,object])->ComparisonResult:
 mismatches=tuple(sorted([*(key for key,value in expected.items() if actual.get(key)!=value),*(key for key in actual if key not in expected)]))
 return ComparisonResult(not mismatches,mismatches)
def expected_identity(value:dict[str,object])->str: return hashlib.sha256((json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()

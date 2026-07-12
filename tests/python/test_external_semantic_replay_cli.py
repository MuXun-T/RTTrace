from pathlib import Path
import tempfile
from tool.run_external_semantic_replay import main
ROOT=Path(__file__).resolve().parents[2]
def test_cli_bytes_are_stable():
 raw=ROOT/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.btf"
 with tempfile.TemporaryDirectory() as d:
  a=Path(d)/"a"; b=Path(d)/"b"; assert main(["--trace",str(raw),"--format","btf","--output",str(a)])==0; assert main(["--trace",str(raw),"--format","btf","--output",str(b)])==0; assert a.read_bytes()==b.read_bytes()

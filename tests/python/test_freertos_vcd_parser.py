from pathlib import Path
from parser.freertos_vcd_parser import parse_freertos_vcd
def test_vcd():
 raw=(Path(__file__).resolve().parents[2]/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.vcd").read_bytes()
 assert len(parse_freertos_vcd(raw).changes)==15084

from pathlib import Path
import pytest
from parser.freertos_vcd_parser import VcdParseError, parse_freertos_vcd
def test_vcd():
 raw=(Path(__file__).resolve().parents[2]/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.vcd").read_bytes()
 with pytest.raises(VcdParseError,match="UNSUPPORTED_REQUIRED_RECORD:15440"): parse_freertos_vcd(raw)
def test_binary_scalar_is_accepted():
 assert len(parse_freertos_vcd(b"$var wire 1 ! task $end\n$enddefinitions $end\n#1\n0!\n").changes)==1

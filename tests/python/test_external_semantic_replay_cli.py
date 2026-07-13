from pathlib import Path
import tempfile
from unittest.mock import patch
import parser.external_package_replay_adapter as adapter
from tool.run_external_semantic_replay import main
ROOT=Path(__file__).resolve().parents[2]
def test_cli_bytes_are_stable():
 raw=ROOT/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw/example.btf"
 with tempfile.TemporaryDirectory() as d:
  a=Path(d)/"a"; b=Path(d)/"b"; assert main(["--trace",str(raw),"--format","btf","--output",str(a)])==0; assert main(["--trace",str(raw),"--format","btf","--output",str(b)])==0; assert a.read_bytes()==b.read_bytes()
def test_reference_only_cases_do_not_construct_or_replay_traces():
 with patch("tool.run_external_semantic_replay.replay_input",side_effect=AssertionError),patch("tool.run_external_semantic_replay.replay",side_effect=AssertionError):
  for case,reason in (("zephyr","SOURCE_ACQUISITION_BLOCKED"),("zephelin","SOURCE_EXTERNAL_REFERENCE_ONLY")):
   with tempfile.TemporaryDirectory() as d:
    out=Path(d)/case; assert main(["--case",case,"--output",str(out)])==2
    value=__import__("json").loads(out.read_text()); assert value["replay_state"]=="reference_only" and not value["replay_attempted"] and not value["comparison_attempted"] and value["primary_reason"]==reason and value["expected_identity"] is None and value["actual_identity"] is None
def test_reference_only_requires_frozen_inventory():
 with tempfile.TemporaryDirectory() as d:
  broken=Path(d)/"inventory.json"; broken.write_text("{}")
  with patch.object(adapter,"_INVENTORY_PATH",broken):
   value=adapter.reference_only_report("zephyr")
  assert value["replay_state"]=="replay_fail" and value["primary_reason"]=="INTERNAL_REPLAY_ERROR" and value["comparison_attempted"] is False
def test_cli_output_error_is_internal_error():
 assert main(["--case","freertos_btf_1core","--output","/missing/output.json"])==70
def test_all_frozen_cases_are_repeatable():
 for case,code in (("freertos_btf_1core",0),("freertos_vcd_1core",4),("freertos_btf_4cores",0),("freertos_btf_50k",4),("zephyr",2),("zephelin",2)):
  with tempfile.TemporaryDirectory() as d:
   a=Path(d)/"a"; b=Path(d)/"b"; assert main(["--case",case,"--output",str(a)])==code; assert main(["--case",case,"--output",str(b)])==code; assert a.read_bytes()==b.read_bytes()

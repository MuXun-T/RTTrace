from pathlib import Path
import shutil
from dataclasses import replace
from unittest.mock import patch
from parser.external_package_replay_adapter import replay_input
from parser.external_semantic_replay import replay
from parser.external_replay_comparator import ComparisonProfileError, ComparisonResult, ExpectedContractError, ExpectedIdentityError
from parser.external_semantic_event_models import Mismatch, MismatchClass
from parser.external_semantic_normalizer import normalize_btf as real_normalize_btf
import parser.external_package_replay_adapter as adapter
from parser.freertos_vcd_parser import VcdParseError
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema
RAW=Path(__file__).resolve().parents[2]/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw"
def test_replay_and_regression():
 passed=replay(replay_input(RAW/"example.btf","btf")); failed=replay(replay_input(RAW/"example-50k.btf","btf"))
 assert passed["replay_state"]=="replay_pass" and passed["comparison_attempted"] and passed["comparison_matched"] and validate_schema(load_schema("external_replay_report.schema.json"),passed) is None
 assert failed["replay_state"]=="replay_fail" and failed["comparison_attempted"] and failed["comparison_matched"] and failed["reason_codes"]==["TIMESTAMP_REGRESSION"] and failed["timestamp_regressions"]==[{"source_record_index":2037,"physical_line":2042,"previous_timestamp":26113,"current_timestamp":26112,"cpu_id":None,"reason":"TIMESTAMP_REGRESSION"}] and validate_schema(load_schema("external_replay_report.schema.json"),failed) is None
 vcd=replay(replay_input(RAW/"example.vcd","vcd"))
 assert vcd["replay_state"]=="replay_fail" and not vcd["comparison_attempted"] and vcd["primary_reason"]=="UNSUPPORTED_REQUIRED_RECORD" and vcd["comparison_profile_version"]=="v1" and vcd["expected_identity"] is not None and vcd["actual_identity"] is None and validate_schema(load_schema("external_replay_report.schema.json"),vcd) is None

def test_comparison_contract_failures_are_not_parse_errors():
 source=replay_input(RAW/"example.btf","btf")
 cases=(("load_expected",ExpectedContractError("x"),"EXPECTED_CONTRACT_UNAVAILABLE",False,False,None),("load_expected",ExpectedIdentityError("x"),"EXPECTED_OUTPUT_IDENTITY_MISMATCH",False,False,None),("load_profile",ComparisonProfileError("x"),"COMPARISON_PROFILE_UNSUPPORTED",False,False,None),("compare_profile",ComparisonResult(False,(Mismatch(MismatchClass.VALUE_MISMATCH,"first_timestamp",None,1,2),)),"COMPARISON_MISMATCH",True,True,False),("compare_profile",RuntimeError("x"),"INTERNAL_REPLAY_ERROR",True,False,None))
 for target,value,reason,attempted,completed,matched in cases:
  with patch(f"parser.external_semantic_replay.{target}",return_value=value) if isinstance(value,ComparisonResult) else patch(f"parser.external_semantic_replay.{target}",side_effect=value):
   report=replay(source)
  assert report["replay_state"]=="replay_fail" and report["primary_reason"]==reason and report["comparison_attempted"] is attempted and report["comparison_completed"] is completed and report["comparison_matched"] is matched
def test_trace_toctou_is_replay_failure(tmp_path):
 path=tmp_path/"example.btf"; shutil.copyfile(RAW/"example.btf",path); source=replay_input(path,"btf")
 def mutate(trace):
  events=real_normalize_btf(trace); path.write_bytes(b"changed"); return events
 with patch("parser.external_semantic_replay.normalize_btf",side_effect=mutate): report=replay(source)
 assert report["replay_state"]=="replay_fail" and report["primary_reason"]=="TRACE_CHECKSUM_DRIFT" and report["raw_trace_mutation_count"]==1
def test_post_snapshot_catches_failure_stage_mutations(tmp_path):
 source=replay_input(RAW/"example.btf","btf")
 raw=tmp_path/"trace"; shutil.copyfile(RAW/"example.vcd",raw); vcd=replace(replay_input(raw,"vcd"),path=raw)
 with patch("parser.external_semantic_replay.parse_freertos_vcd",side_effect=lambda _: (raw.write_bytes(b"changed"),(_ for _ in ()).throw(VcdParseError("PARSE_ERROR",1)))[1]):
  report=replay(vcd)
 assert report["raw_trace_mutation_count"]==1 and report["primary_reason"]=="TRACE_CHECKSUM_DRIFT" and report["replay_state"]=="replay_fail"
 inventory=tmp_path/"inventory"; shutil.copyfile(adapter._INVENTORY_PATH,inventory); changed=replace(source,source_snapshot=adapter._snapshot(inventory))
 with patch.object(adapter,"_INVENTORY_PATH",inventory),patch("parser.external_semantic_replay.load_expected",side_effect=lambda *args: (inventory.write_bytes(b"changed"),ExpectedContractError("x"))[1]): report=replay(changed)
 assert report["source_mutation_count"]==1 and report["primary_reason"]=="SOURCE_MUTATED" and report["replay_state"]=="replay_fail"
 opened=tmp_path/"opened"; shutil.copyfile(adapter._OPENED_REPORT_PATH,opened); changed=replace(source,package_snapshot=adapter._snapshot(opened))
 with patch.object(adapter,"_OPENED_REPORT_PATH",opened),patch("parser.external_semantic_replay.compare_profile",side_effect=lambda *args: (opened.write_bytes(b"changed"),RuntimeError("x"))[1]): report=replay(changed)
 assert report["package_mutation_count"]==1 and report["primary_reason"]=="PACKAGE_MUTATED" and report["replay_state"]=="replay_fail"

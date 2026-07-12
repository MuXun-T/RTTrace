from pathlib import Path
from parser.external_package_replay_adapter import replay_input
from parser.external_semantic_replay import replay
RAW=Path(__file__).resolve().parents[2]/"tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw"
def test_replay_and_regression():
 assert replay(replay_input(RAW/"example.btf","btf"))["replay_state"]=="replay_pass"
 assert replay(replay_input(RAW/"example-50k.btf","btf"))["replay_state"]=="replay_fail"

from parser.external_replay_comparator import compare
from parser.external_replay_comparator import ComparisonProfileError, ExpectedContractError, load_expected, load_profile
from pathlib import Path
import json
import pytest
ROOT=Path(__file__).resolve().parents[2]
def test_exact_and_stable_mismatch():
 assert compare({"a":1},{"a":1}).matched
 assert [(item.mismatch_class.value,item.field) for item in compare({"b":1,"a":2},{"b":2,"c":3}).mismatches]==[("missing_actual_field","a"),("unexpected_actual_field","c"),("value_mismatch","b")]
def test_frozen_fixture_identities_and_metadata_fail_closed(tmp_path):
 replay=ROOT/"tests/python/fixtures/external_validation/replay"; profile=load_profile(replay/"comparison_profiles/p7.4-freertos-semantic-exact-v1.json")
 expected,_=load_expected(replay/"expected/freertos_btf_1core.expected.json","571183cbafba85f0eeb9c74cf2350f02a4e628abe514409dd3c1b68286969f44",profile); assert expected["normalized_event_count"]==2389
 bad=json.loads((replay/"expected/freertos_btf_1core.expected.json").read_text()); bad.pop("authoring_evidence_id"); path=tmp_path/"bad.json"; path.write_text(json.dumps(bad))
 with pytest.raises(ExpectedContractError): load_expected(path,"571183cbafba85f0eeb9c74cf2350f02a4e628abe514409dd3c1b68286969f44",profile)
 bad=json.loads((replay/"comparison_profiles/p7.4-freertos-semantic-exact-v1.json").read_text()); bad["comparison_profile_identity"]="0"*64; path.write_text(json.dumps(bad))
 with pytest.raises(ComparisonProfileError): load_profile(path)

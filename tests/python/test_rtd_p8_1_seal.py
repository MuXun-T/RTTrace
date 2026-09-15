import importlib.util
from pathlib import Path

MODULE=Path(__file__).parents[2]/"tool/rtd_p8_1_seal.py"
spec=importlib.util.spec_from_file_location("p81seal",MODULE)
p81=importlib.util.module_from_spec(spec);spec.loader.exec_module(p81)

def test_hash_is_sha256():
    assert len(p81.sha(MODULE))==64

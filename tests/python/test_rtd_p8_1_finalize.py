import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[2] / "tool/rtd_p8_1_finalize.py"
spec = importlib.util.spec_from_file_location("p81final", MODULE)
p81 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p81)

def test_paths_and_base_records_are_reproducible_and_non_hardware():
    assert p81.rel(p81.P8).startswith("docs/rtd_pilot/")
    record = p81.base("test", "record:parent")
    assert record["append_only"] is True
    assert record["hardware_action"] is False
    assert record["parent_record_id"] == "record:parent"

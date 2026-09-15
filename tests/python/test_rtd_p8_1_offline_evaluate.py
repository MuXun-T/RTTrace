import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[2] / "tool/rtd_p8_1_offline_evaluate.py"
spec = importlib.util.spec_from_file_location("p81", MODULE)
p81 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p81)

def frame(seq, tick, kind):
    return b"\xa5" + seq.to_bytes(4, "big") + tick.to_bytes(4, "big") + bytes([kind])

def test_fixed_uart_event_sets_predict_without_metadata_or_truth():
    assert p81.predict(frame(1, 0, 13) + frame(2, 1, 14))[:2] == (True, "F1")
    assert p81.predict(frame(1, 0, 20) + frame(2, 1, 21) + frame(3, 2, 22) + frame(4, 3, 23))[:2] == (True, "F2")
    assert p81.predict(frame(1, 0, 30))[:2] == (True, "F3")
    assert p81.predict(b"P5_BOOT case_id=F1-P01\n")[:2] == (False, None)

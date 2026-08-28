import json
import os
from pathlib import Path

import pytest

from p4_capture.contracts import P4ContractError
from p4_capture.recorder import (
    CH340_BY_ID,
    PLAN_HASH,
    exclusive_wire_import,
    record_serial_wire,
    validate_hardware_request,
)


def _request(**changes):
    request = {
        "capture_mode": "hardware_smoke_noncase",
        "test_only": False,
        "case_id": None,
        "device": CH340_BY_ID,
        "baud": 115200,
        "framing": "8N1-no-flow",
        "plan_hash": PLAN_HASH,
        "confirm": True,
        "identity": {"vid_pid": "1a86:7523", "resolved_device": CH340_BY_ID},
    }
    request.update(changes)
    return request


def _clock(*values):
    ticks = iter(values)
    return lambda: next(ticks)


def _record(directory: Path, *, reads=(b"abc",), clock=None, **changes):
    events: list[tuple[str, int | None]] = []
    values = iter(reads)
    args = {
        "metadata": {"device": CH340_BY_ID, "config_hash": "a" * 64},
        "open_fd": lambda: events.append(("open", None)) or 17,
        "read_fd": lambda fd: events.append(("read", fd)) or next(values),
        "duration_s": 2.0,
        "max_bytes": 64,
        "clock": clock or _clock(0.0, 0.0, 2.0, 2.0),
        "termios_get": lambda fd: {"speed": 115200, "fd": fd},
        "termios_set": lambda fd: events.append(("set", fd)),
        "line_state": lambda fd: {"dtr": True, "rts": False, "hupcl": True},
        "close_fd": lambda fd: events.append(("close", fd)),
    }
    args.update(changes)
    return record_serial_wire(directory, **args), events


def _sidecar(directory: Path, name: str):
    return json.loads((directory / name).read_text(encoding="ascii"))


def test_hardware_request_requires_actual_resolved_ch340_identity():
    validate_hardware_request(**_request())
    for unsafe in (
        "/dev/ttyACM0",
        "/dev/ttyUSB0",
        "/dev/serial/by-id/usb-CMSIS-DAP_0001A0000001-if00",
        "/dev/serial/by-id/usb-1a86_other-if00-port0",
    ):
        with pytest.raises(P4ContractError):
            validate_hardware_request(**_request(device=unsafe))
    with pytest.raises(P4ContractError):
        validate_hardware_request(**_request(identity=None))
    with pytest.raises(P4ContractError):
        validate_hardware_request(**_request(identity={"vid_pid": "1a86:7523"}))
    with pytest.raises(P4ContractError):
        validate_hardware_request(**_request(identity={"vid_pid": "c251:f001", "resolved_device": CH340_BY_ID}))
    with pytest.raises(P4ContractError):
        validate_hardware_request(**_request(case_id="case:x"))


def test_recorder_success_is_ready_before_one_open_and_one_close(tmp_path: Path):
    result, events = _record(tmp_path)
    assert result.read_bytes() == b"abc"
    assert events.count(("open", None)) == 1
    assert events.count(("close", 17)) == 1
    assert (tmp_path / "ready.json").exists()
    success = _sidecar(tmp_path, "record_success.json")
    assert success == {
        "bytes": 3,
        "config_hash": "a" * 64,
        "device": CH340_BY_ID,
        "dtr": True,
        "duration_s": 2.0,
        "hupcl": True,
        "rts": False,
        "state": "complete",
        "termination": "duration",
        "termios_after": {"fd": 17, "speed": 115200},
        "termios_before": {"fd": 17, "speed": 115200},
    }


def test_recorder_writes_durable_ready_before_open(tmp_path: Path):
    observed: list[bytes] = []

    def open_fd():
        observed.append((tmp_path / "ready.json").read_bytes())
        return 31

    record_serial_wire(
        tmp_path,
        metadata={"device": CH340_BY_ID, "config_hash": "c" * 64},
        open_fd=open_fd,
        read_fd=lambda _fd: b"unused",
        duration_s=0.0,
        max_bytes=1,
        clock=_clock(0.0, 0.0, 1.0),
        close_fd=lambda _fd: None,
    )
    assert observed == [b'{"config_hash":"' + b"c" * 64 + b'","device":"' + CH340_BY_ID.encode() + b'"}\n']


@pytest.mark.parametrize(
    ("duration_s", "max_bytes", "reads", "end_condition", "expected"),
    [
        (0.0, 64, (), lambda _data: False, (b"", "duration")),
        (2.0, 0, (), lambda _data: False, (b"", "max_bytes")),
        (2.0, 64, (b"stop",), lambda data: data == b"stop", (b"stop", "end_condition")),
        (2.0, 3, (b"abcdef",), lambda _data: False, (b"abc", "max_bytes")),
    ],
)
def test_recorder_terminations_and_maximum_write(tmp_path: Path, duration_s, max_bytes, reads, end_condition, expected):
    result, _ = _record(
        tmp_path,
        reads=reads,
        duration_s=duration_s,
        max_bytes=max_bytes,
        end_condition=end_condition,
        clock=_clock(0.0, 0.0, 2.0, 2.0),
    )
    assert result.read_bytes() == expected[0]
    assert _sidecar(tmp_path, "record_success.json")["termination"] == expected[1]


def test_recorder_handles_short_writes(tmp_path: Path):
    def short_write(fd, data):
        return os.write(fd, data[:1])

    result, _ = _record(tmp_path, reads=(b"abcd",), max_bytes=4, write_fd=short_write)
    assert result.read_bytes() == b"abcd"


@pytest.mark.parametrize("failure", ["open", "termios", "read", "close"])
def test_recorder_preserves_partial_for_injected_failures(tmp_path: Path, failure: str):
    events: list[str] = []

    def open_fd():
        events.append("open")
        if failure == "open":
            raise RuntimeError("open")
        return 23

    def termios_get(_fd):
        if failure == "termios":
            raise RuntimeError("termios")
        return {"ok": True}

    def read_fd(_fd):
        if failure == "read":
            raise RuntimeError("read")
        return b"x"

    def close_fd(_fd):
        events.append("close")
        if failure == "close":
            raise RuntimeError("close")

    with pytest.raises(RuntimeError):
        record_serial_wire(
            tmp_path,
            metadata={"device": CH340_BY_ID, "config_hash": "b" * 64},
            open_fd=open_fd,
            read_fd=read_fd,
            duration_s=0.0 if failure != "read" else 1.0,
            max_bytes=4,
            clock=_clock(0.0, 0.0, 0.0, 0.0, 1.0, 1.0),
            termios_get=termios_get,
            termios_set=lambda _fd: None,
            close_fd=close_fd,
        )
    marker = _sidecar(tmp_path, "failure_preserved.json")
    assert marker["state"] == "partial"
    assert marker["exception_type"] == "RuntimeError"
    assert events.count("open") == 1
    assert events.count("close") == (0 if failure == "open" else 1)


@pytest.mark.parametrize("name", ["ready.json", "wire.trace", "record_success.json", "failure_preserved.json"])
def test_recorder_preflight_collision_creates_nothing(tmp_path: Path, name: str):
    (tmp_path / name).write_bytes(b"existing")
    with pytest.raises(P4ContractError):
        _record(tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


def test_failure_metadata_collision_does_not_overwrite(tmp_path: Path):
    # A race after preflight is represented by the injected read creating it.
    existing = tmp_path / "failure_preserved.json"

    def read_fd(_fd):
        existing.write_text("old", encoding="ascii")
        raise RuntimeError("read")

    with pytest.raises(P4ContractError, match="failure metadata collision"):
        _record(tmp_path, read_fd=read_fd)
    assert existing.read_text(encoding="ascii") == "old"


def test_recorder_requires_existing_directory(tmp_path: Path):
    with pytest.raises(P4ContractError, match="already exist"):
        _record(tmp_path / "missing")


def test_wire_import_is_exclusive_and_preserves_partial(tmp_path: Path):
    target = exclusive_wire_import(tmp_path, b"partial", failure_reason="TIMEOUT")
    assert target.read_bytes() == b"partial"
    assert _sidecar(tmp_path, "failure_preserved.json")["reason"] == "TIMEOUT"
    with pytest.raises(P4ContractError):
        exclusive_wire_import(tmp_path, b"again")

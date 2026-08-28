"""P4 fixed auxiliary ABI, canonical parity, and fail-closed wire tests."""
from __future__ import annotations

import struct
import subprocess
from pathlib import Path
import zlib

import pytest

from p4_capture.wire_decoder import (
    BOOT_PAYLOAD_STRUCT,
    CONFIG_PAYLOAD_STRUCT,
    COUNTER_PAYLOAD_STRUCT,
    MAX_CAPTURE_WIRE_BYTES,
    UART_FRAME_BOOT,
    UART_FRAME_CANONICAL_TRACE,
    UART_FRAME_CONFIG,
    UART_FRAME_COUNTER,
    UART_FRAME_STRUCT,
    decode_uart_wire,
)
from parser.codec import decode_trace


CAPTURE_ID = "capture:p4-wire"
SOURCE_HASH = "a" * 64
WORKLOAD_HASH = "b" * 64


def _emit_canonical_wire(tmp_path: Path) -> bytes:
    root = Path(__file__).parents[2]
    source, executable, wire = tmp_path / "emit_wire.cpp", tmp_path / "emit_wire", tmp_path / "target.wire"
    source.write_text(
        """
        #include <fstream>
        #include "trace_api.h"
        #include "trace_target_contract.hpp"
        int main(int argc, char** argv) {
          rtd::p4::freertos_stm32f103::TargetCollector c(8, "capture:p4-wire");
          rtd::p4::freertos_stm32f103::Event e{};
          e.core_id=0; e.event_id=TRACE_EVENT_SYNC_CALIB; e.timestamp=10; e.payload_len=0;
          c.Record(e); auto first=c.FlushCanonical();
          e.timestamp=20; c.Record(e); auto second=c.FlushCanonical();
          rtd::p4::freertos_stm32f103::WireBuffer one{}, two{};
          if (!c.FrameForUart(first, &one) || !c.FrameForUart(second, &two)) return 2;
          std::ofstream out(argv[1], std::ios::binary);
          out.write(reinterpret_cast<const char*>(one.bytes.data()), one.size);
          out.write(reinterpret_cast<const char*>(two.bytes.data()), two.size);
        }
        """,
        encoding="ascii",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(root / "collector/include"), "-I", str(root / "collector/target/freertos_stm32f103"),
         str(source), str(root / "collector/target/freertos_stm32f103/trace_target_contract.cpp"), "-o", str(executable)],
        check=True, cwd=root,
    )
    subprocess.run([str(executable), str(wire)], check=True, cwd=root)
    return wire.read_bytes()


def _frame(payload: bytes, frame_type: int) -> bytes:
    return UART_FRAME_STRUCT.pack(0x5234, 1, frame_type, len(payload), zlib.crc32(payload) & 0xFFFFFFFF) + payload


def _frames(wire: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(wire):
        _magic, _version, frame_type, length, _crc = UART_FRAME_STRUCT.unpack_from(wire, offset)
        offset += UART_FRAME_STRUCT.size
        frames.append((frame_type, wire[offset : offset + length]))
        offset += length
    return frames


def _join(frames: list[tuple[int, bytes]]) -> bytes:
    return b"".join(_frame(payload, frame_type) for frame_type, payload in frames)


def _with_auxiliary(canonical_wire: bytes, *, capture_id: str = CAPTURE_ID, counter_update: dict[int, int] | None = None,
                    config_update: dict[int, object] | None = None) -> bytes:
    boot = BOOT_PAYLOAD_STRUCT.pack(1, 8_000_000, capture_id.encode("ascii"))
    config_values: list[object] = [8, 1, 115200, 0x00080101, 8_000_000, 1, 0, SOURCE_HASH.encode("ascii"), WORKLOAD_HASH.encode("ascii")]
    if config_update:
        for index, value in config_update.items():
            config_values[index] = value
    config = CONFIG_PAYLOAD_STRUCT.pack(*config_values)
    prefix = _frame(boot, UART_FRAME_BOOT) + _frame(config, UART_FRAME_CONFIG) + canonical_wire
    # 13 Q values, HWM and capacity I values, then observation/natural flags.
    counter_values: list[int] = [2, 2, 0, 0, 0, 0, 2, 0, 5, len(prefix) + UART_FRAME_STRUCT.size + COUNTER_PAYLOAD_STRUCT.size,
                                 0, 0, 0, 2, 8, 0, 0]
    if counter_update:
        for index, value in counter_update.items():
            counter_values[index] = value
    return prefix + _frame(COUNTER_PAYLOAD_STRUCT.pack(*counter_values), UART_FRAME_COUNTER)


def _valid_wire(tmp_path: Path) -> bytes:
    return _with_auxiliary(_emit_canonical_wire(tmp_path))


def test_fixed_auxiliary_abi_and_canonical_parity(tmp_path: Path) -> None:
    raw = tmp_path / "rebuilt.trace"
    report = decode_uart_wire(_valid_wire(tmp_path), raw)
    assert report["pass"] and report["frame_count"] == 5
    assert report["boot"] == {"selector": 1, "timer_clock_hz": 8_000_000, "capture_id": CAPTURE_ID}
    assert report["config"]["buffer_capacity_unit"] == "records"
    assert report["config"]["source_sha256"] == SOURCE_HASH
    assert report["target_counter"]["decoded_observation_state"] == "not_observed"
    assert report["host_collector_facts"]["decoded_records"] == 2
    assert report["host_collector_facts"]["decoded_records_observation"]["source"] == "host_canonical_raw"
    assert report["counter_reconciliation"]["pass"]
    decoded = decode_trace(raw, dataset_id=CAPTURE_ID)
    assert decoded.ok, decoded.message
    assert [event.seq for event in decoded.data["events"]] == [1, 2]


@pytest.mark.parametrize(
    ("name", "make", "code"),
    [
        ("header_truncation", lambda tmp: _valid_wire(tmp)[: UART_FRAME_STRUCT.size - 1], "FRAME_HEADER_TRUNCATION"),
        ("payload_truncation", lambda tmp: _valid_wire(tmp)[:-1], "FRAME_PAYLOAD_TRUNCATION"),
        ("crc", lambda tmp: _valid_wire(tmp)[:8] + bytes([_valid_wire(tmp)[8] ^ 1]) + _valid_wire(tmp)[9:], "FRAME_CRC"),
        ("unknown_type", lambda tmp: _valid_wire(tmp)[:3] + b"\x63" + _valid_wire(tmp)[4:], "FRAME_TYPE"),
        ("boot_length", lambda tmp: _join([(UART_FRAME_BOOT, b"short"), *_frames(_valid_wire(tmp))[1:]]), "BOOT_LENGTH"),
        ("config_hash", lambda tmp: _with_auxiliary(_emit_canonical_wire(tmp), config_update={7: b"G" * 64}), "AUX_HASH"),
        ("capture_id", lambda tmp: _with_auxiliary(_emit_canonical_wire(tmp), capture_id="capture:other"), "CAPTURE_ID_MISMATCH"),
        ("capacity", lambda tmp: _with_auxiliary(_emit_canonical_wire(tmp), counter_update={14: 7}), "CAPACITY_MISMATCH"),
        ("target_decoded", lambda tmp: _with_auxiliary(_emit_canonical_wire(tmp), counter_update={7: 1}), "COUNTER_DECODED_STATE"),
        ("wire_inventory", lambda tmp: _with_auxiliary(_emit_canonical_wire(tmp), counter_update={8: 4}), "WIRE_INVENTORY_MISMATCH"),
        ("counter_equation", lambda tmp: _with_auxiliary(_emit_canonical_wire(tmp), counter_update={1: 1}), "COUNTER_RECONCILIATION"),
        ("wire_cap", lambda tmp: b"x" * (MAX_CAPTURE_WIRE_BYTES + 1), "WIRE_CAPTURE_LIMIT"),
    ],
)
def test_auxiliary_and_wire_fail_closed(tmp_path: Path, name, make, code) -> None:
    raw = tmp_path / f"{name}.trace"
    report = decode_uart_wire(make(tmp_path), raw)
    assert not report["pass"] and report["raw_state"] == "unavailable"
    assert report["errors"][0]["code"] == code
    assert not raw.exists()


def test_protocol_order_and_exactly_one_auxiliary_frames(tmp_path: Path) -> None:
    valid = _valid_wire(tmp_path)
    frames = _frames(valid)
    cases = {
        "late_boot": _join([frames[2], frames[0], *frames[1:2], *frames[3:]]),
        "duplicate_boot": _join([frames[0], frames[0], *frames[1:]]),
        "missing_config": _join([frames[0], *frames[2:]]),
        "counter_not_last": _join([*frames[:-1], frames[-1], frames[2]]),
    }
    for name, wire in cases.items():
        report = decode_uart_wire(wire, tmp_path / f"{name}.trace")
        assert not report["pass"] and report["errors"][0]["code"] == "AUX_ORDER"


def test_derived_wire_capture_ceiling_includes_mandatory_auxiliary_frames(tmp_path: Path) -> None:
    # 5 * 11,520 active bytes, three 8,204-byte final drains, plus 337 aux bytes.
    assert MAX_CAPTURE_WIRE_BYTES == 82_549
    at_limit = decode_uart_wire(b"x" * MAX_CAPTURE_WIRE_BYTES, tmp_path / "at-limit.trace")
    assert at_limit["errors"][0]["code"] == "FRAME_MAGIC"
    above_limit = decode_uart_wire(b"x" * (MAX_CAPTURE_WIRE_BYTES + 1), tmp_path / "above-limit.trace")
    assert above_limit["errors"][0]["code"] == "WIRE_CAPTURE_LIMIT"


def test_decoder_never_overwrites_existing_raw_output(tmp_path: Path) -> None:
    raw = tmp_path / "duplicate.trace"
    raw.write_bytes(b"original")
    report = decode_uart_wire(_valid_wire(tmp_path), raw)
    assert not report["pass"] and report["errors"][0]["code"] == "OUTPUT_COLLISION"
    assert raw.read_bytes() == b"original"

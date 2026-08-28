"""Offline, fail-closed reconstruction of P4 STM32 UART wire frames.

The layout and CRC here are intentionally matched to
``trace_target_contract.hpp`` / ``TargetCollector::FrameForUart``.  This is a
host-side verifier only: it never opens a serial device and never modifies the
retained wire input.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
import struct
from typing import Any
import zlib

from parser.codec import (
    CHUNK_HEADER_STRUCT,
    EVENT_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_CHUNK_MAGIC,
    TRACE_FORMAT_MAGIC,
    TRACE_SEGMENT_META_MAGIC,
)
from .integrity import reconcile_collector_facts


# collector/target/freertos_stm32f103/trace_target_contract.hpp
UART_FRAME_STRUCT = struct.Struct("<HBBII")
UART_FRAME_MAGIC = 0x5234
UART_FRAME_VERSION = 1
UART_FRAME_CANONICAL_TRACE = 1
UART_FRAME_BOOT = 2
UART_FRAME_CONFIG = 3
UART_FRAME_COUNTER = 4
P4_CAPACITY_UNIT_RECORDS = 1
P4_OBSERVATION_NOT_OBSERVED = 0
BOOT_PAYLOAD_STRUCT = struct.Struct("<II32s")
CONFIG_PAYLOAD_STRUCT = struct.Struct("<IBIIIBB64s64s")
COUNTER_PAYLOAD_STRUCT = struct.Struct("<" + "Q" * 13 + "IIBB")
BOOT_PAYLOAD_BYTES = BOOT_PAYLOAD_STRUCT.size
CONFIG_PAYLOAD_BYTES = CONFIG_PAYLOAD_STRUCT.size
COUNTER_PAYLOAD_BYTES = COUNTER_PAYLOAD_STRUCT.size
MAX_TRACE_BYTES = 8192
# P4 fixes the active smoke window at five seconds.  UART 8N1 at 115200 has a
# hard physical ceiling of 11,520 canonical-wire bytes/s (57,600 total).
# `rtd_p4_capture_finalize()` then performs exactly three bounded final drains;
# each legal UART canonical frame is at most kMaxTraceBytes + its 12-byte
# envelope.  Pending markers only consume that existing bound.  BOOT, CONFIG,
# and COUNTER are the only fixed auxiliary frames.  The whole-wire cap and the
# recorder wall duration below are therefore protocol-derived, not a target-RAM
# shortcut.  Rebuilding repeats the 96-byte global header for each canonical
# frame; active frames have a minimum first/subsequent size of 168/72 bytes
# before their 12-byte UART envelopes.
P4_SMOKE_WINDOW_SECONDS = 5
UART_PHYSICAL_BYTES_PER_SECOND = 11_520
MAX_ACTIVE_CANONICAL_WIRE_BYTES = P4_SMOKE_WINDOW_SECONDS * UART_PHYSICAL_BYTES_PER_SECOND
MANDATORY_AUXILIARY_WIRE_BYTES = (
    BOOT_PAYLOAD_BYTES + CONFIG_PAYLOAD_BYTES + COUNTER_PAYLOAD_BYTES + (3 * UART_FRAME_STRUCT.size)
)
POST_WINDOW_FINALIZE_FRAMES = 3
MAX_POST_WINDOW_CANONICAL_WIRE_BYTES = POST_WINDOW_FINALIZE_FRAMES * (MAX_TRACE_BYTES + UART_FRAME_STRUCT.size)
MAX_CAPTURE_WIRE_BYTES = MAX_ACTIVE_CANONICAL_WIRE_BYTES + MAX_POST_WINDOW_CANONICAL_WIRE_BYTES + MANDATORY_AUXILIARY_WIRE_BYTES
MAX_CANONICAL_FRAMES_AT_WIRE_CEILING = 1 + ((MAX_ACTIVE_CANONICAL_WIRE_BYTES - (168 + UART_FRAME_STRUCT.size)) // (72 + UART_FRAME_STRUCT.size))
MAX_REBUILT_RAW_BYTES = (
    MAX_ACTIVE_CANONICAL_WIRE_BYTES - (UART_FRAME_STRUCT.size * MAX_CANONICAL_FRAMES_AT_WIRE_CEILING)
    + (GLOBAL_HEADER_STRUCT.size * (MAX_CANONICAL_FRAMES_AT_WIRE_CEILING - 1))
    + (POST_WINDOW_FINALIZE_FRAMES * MAX_TRACE_BYTES)
)
# The recorder clock begins before the external SWD arm write.  Its latency is
# intentionally not guessed here: a future no-write preflight must measure and
# freeze a gate-arm budget.  This is the non-negotiable post-gate drain floor.
MIN_POST_GATE_RECORDER_DURATION_SECONDS = (MAX_CAPTURE_WIRE_BYTES + UART_PHYSICAL_BYTES_PER_SECOND - 1) // UART_PHYSICAL_BYTES_PER_SECOND
TRACE_FORMAT_VERSION = 2
TRACE_HEADER_VERSION = 2
TRACE_DICT_VERSION = 1

_TYPE_NAMES = {
    UART_FRAME_CANONICAL_TRACE: "canonical_trace",
    UART_FRAME_BOOT: "boot",
    UART_FRAME_CONFIG: "config",
    UART_FRAME_COUNTER: "counter",
}
_CAPTURE_ID_RE = re.compile(r"capture:[A-Za-z0-9._-]+\Z")
_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")


class WireDecodeError(ValueError):
    """One exact wire or canonical-trace rule was violated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise WireDecodeError(code, message)


def _ascii_nul_padded(value: bytes, *, code: str, field: str, require_nonempty: bool = True) -> str:
    try:
        nul = value.index(0)
    except ValueError:
        nul = len(value)
    if any(byte != 0 for byte in value[nul:]):
        _fail(code, f"{field} has non-zero data after NUL padding")
    raw = value[:nul]
    if (require_nonempty and not raw) or any(byte < 0x20 or byte > 0x7E for byte in raw):
        _fail(code, f"{field} is not a non-empty printable ASCII string")
    return raw.decode("ascii")


def _ascii_hex64(value: bytes, *, field: str) -> str:
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError:
        _fail("AUX_HASH", f"{field} is not ASCII")
    if not _HEX64_RE.fullmatch(decoded):
        _fail("AUX_HASH", f"{field} is not lowercase SHA-256 hex")
    return decoded


def _validate_global(data: bytes) -> str:
    fields = GLOBAL_HEADER_STRUCT.unpack(data)
    if fields[0] != TRACE_FORMAT_MAGIC:
        _fail("GLOBAL_MAGIC", "canonical global header magic is invalid")
    if fields[1:7] != (1, 1, TRACE_FORMAT_VERSION, TRACE_DICT_VERSION, TRACE_HEADER_VERSION, 1):
        _fail("GLOBAL_LAYOUT", "canonical global header does not match the P4 target layout")
    run_id = _ascii_nul_padded(fields[-1], code="GLOBAL_RUN_ID", field="global run_id")
    if not _CAPTURE_ID_RE.fullmatch(run_id):
        _fail("GLOBAL_RUN_ID", "canonical global run_id is not a capture identifier")
    return run_id


def _validate_chunk(payload: bytes, offset: int, *, expected_segment_seq: int) -> tuple[int, list[tuple[int, int, bytes]]]:
    if len(payload) - offset < CHUNK_HEADER_STRUCT.size:
        _fail("CHUNK_TRUNCATION", "canonical trace ends before a chunk header")
    chunk = CHUNK_HEADER_STRUCT.unpack_from(payload, offset)
    if chunk[0] != TRACE_CHUNK_MAGIC:
        _fail("CHUNK_MAGIC", "canonical trace has an invalid chunk magic")
    if chunk[1] != TRACE_HEADER_VERSION or chunk[9] != TRACE_DICT_VERSION:
        _fail("CHUNK_LAYOUT", "chunk version or dictionary does not match the P4 target")
    payload_bytes = int(chunk[4])
    body_start = offset + CHUNK_HEADER_STRUCT.size
    body_end = body_start + payload_bytes
    if body_end > len(payload):
        _fail("CHUNK_TRUNCATION", "chunk payload is truncated")
    body = payload[body_start:body_end]
    if (zlib.crc32(body) & 0xFFFFFFFF) != chunk[10]:
        _fail("CHUNK_CRC", "canonical chunk CRC is invalid")

    event_offset = 0
    event_count = 0
    previous_seq: int | None = None
    timestamps: list[int] = []
    first_seq: int | None = None
    last_seq: int | None = None
    events: list[tuple[int, int, bytes]] = []
    while event_offset < len(body):
        if len(body) - event_offset < EVENT_HEADER_STRUCT.size:
            _fail("EVENT_TRUNCATION", "chunk ends inside an event header")
        event = EVENT_HEADER_STRUCT.unpack_from(body, event_offset)
        event_offset += EVENT_HEADER_STRUCT.size
        event_payload_len = int(event[6])
        if event[0] != TRACE_FORMAT_VERSION or event[1] != 0 or event[2] != chunk[2]:
            _fail("EVENT_LAYOUT", "event header does not match the P4 target layout")
        if event_payload_len > len(body) - event_offset:
            _fail("EVENT_TRUNCATION", "event payload is truncated")
        event_payload = body[event_offset : event_offset + event_payload_len]
        event_offset += event_payload_len
        sequence, timestamp = int(event[4]), int(event[5])
        if previous_seq is not None and sequence <= previous_seq:
            _fail("EVENT_SEQUENCE", "event sequences are not strictly increasing within a chunk")
        previous_seq = sequence
        first_seq = sequence if first_seq is None else first_seq
        last_seq = sequence
        timestamps.append(timestamp)
        events.append((int(event[3]), sequence, event_payload))
        event_count += 1
    if event_count != chunk[3]:
        _fail("CHUNK_RECORD_COUNT", "chunk record_count does not match its payload")
    if event_count == 0:
        if any(chunk[index] != 0 for index in (5, 6, 7, 8)):
            _fail("CHUNK_RANGE", "empty chunk has non-zero timestamp or sequence bounds")
    elif (chunk[5], chunk[6], chunk[7], chunk[8]) != (min(timestamps), max(timestamps), first_seq, last_seq):
        _fail("CHUNK_RANGE", "chunk timestamp or sequence bounds do not match its events")
    return body_end, events


def _validate_trace_payload(payload: bytes, *, first_trace: bool, expected_segment_seq: int) -> tuple[int, bytes, str | None, list[tuple[int, int, bytes]]]:
    """Validate one canonical UART payload and return its final segment sequence."""
    offset = 0
    global_header = b""
    run_id: str | None = None
    if first_trace:
        if len(payload) < GLOBAL_HEADER_STRUCT.size:
            _fail("MISSING_GLOBAL", "first canonical frame lacks a complete global header")
        if struct.unpack_from("<I", payload, 0)[0] != TRACE_FORMAT_MAGIC:
            _fail("MISSING_GLOBAL", "first canonical frame does not start with a global header")
        global_header = payload[: GLOBAL_HEADER_STRUCT.size]
        run_id = _validate_global(global_header)
        offset = GLOBAL_HEADER_STRUCT.size
    elif len(payload) >= 4 and struct.unpack_from("<I", payload, 0)[0] == TRACE_FORMAT_MAGIC:
        _fail("DUPLICATE_GLOBAL", "a later canonical frame repeats the global header")
    if offset == len(payload):
        _fail("TRACE_STRUCTURE", "canonical frame has no segment/chunk content")

    events: list[tuple[int, int, bytes]] = []
    while offset < len(payload):
        if len(payload) - offset < SEGMENT_META_STRUCT.size:
            _fail("SEGMENT_TRUNCATION", "canonical trace ends before a segment header")
        segment = SEGMENT_META_STRUCT.unpack_from(payload, offset)
        if segment[0] != TRACE_SEGMENT_META_MAGIC:
            _fail("SEGMENT_MAGIC", "canonical trace has an invalid segment magic")
        if segment[1] != TRACE_HEADER_VERSION or segment[2] != SEGMENT_META_STRUCT.size:
            _fail("SEGMENT_LAYOUT", "segment version or size does not match the P4 target")
        if segment[5] != TRACE_DICT_VERSION or segment[6] != 0:
            _fail("SEGMENT_LAYOUT", "segment dictionary reference does not match the P4 target")
        if segment[3] != expected_segment_seq or segment[4] != (0 if expected_segment_seq == 0 else expected_segment_seq - 1):
            _fail("SEGMENT_CHAIN", "segment sequence chain is not canonical")
        offset += SEGMENT_META_STRUCT.size
        offset, chunk_events = _validate_chunk(payload, offset, expected_segment_seq=expected_segment_seq)
        events.extend(chunk_events)
        expected_segment_seq += 1
    return expected_segment_seq, global_header, run_id, events


def _parse_boot(payload: bytes) -> dict[str, Any]:
    if len(payload) != BOOT_PAYLOAD_BYTES:
        _fail("BOOT_LENGTH", f"BOOT payload must be {BOOT_PAYLOAD_BYTES} bytes")
    selector, timer_clock_hz, capture_bytes = BOOT_PAYLOAD_STRUCT.unpack(payload)
    capture_id = _ascii_nul_padded(capture_bytes, code="BOOT_ID", field="BOOT capture_id")
    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        _fail("BOOT_ID", "BOOT capture_id is not a capture identifier")
    if selector == 0 or timer_clock_hz == 0:
        _fail("BOOT_VALUE", "BOOT selector and timer_clock_hz must be positive")
    return {"selector": selector, "timer_clock_hz": timer_clock_hz, "capture_id": capture_id}


def _parse_config(payload: bytes) -> dict[str, Any]:
    if len(payload) != CONFIG_PAYLOAD_BYTES:
        _fail("CONFIG_LENGTH", f"CONFIG payload must be {CONFIG_PAYLOAD_BYTES} bytes")
    capacity, unit, baud, framing, timestamp_hz, enabled, decoded_state, source, workload = CONFIG_PAYLOAD_STRUCT.unpack(payload)
    if capacity == 0 or unit != P4_CAPACITY_UNIT_RECORDS:
        _fail("CONFIG_CAPACITY", "CONFIG capacity must be positive records")
    if baud == 0 or framing != 0x00080101 or timestamp_hz == 0:
        _fail("CONFIG_VALUE", "CONFIG baud/framing/timestamp is invalid")
    if enabled not in (0, 1) or decoded_state != P4_OBSERVATION_NOT_OBSERVED:
        _fail("CONFIG_STATE", "CONFIG recorder/decoded observation state is invalid")
    return {
        "buffer_capacity_records": capacity,
        "buffer_capacity_unit": "records",
        "baud": baud,
        "framing": framing,
        "framing_name": "8N1-no-flow",
        "timestamp_hz": timestamp_hz,
        "recorder_enabled": bool(enabled),
        "decoded_observation_state": "not_observed",
        "source_sha256": _ascii_hex64(source, field="CONFIG source_sha256"),
        "workload_sha256": _ascii_hex64(workload, field="CONFIG workload_sha256"),
    }


def _parse_counter(payload: bytes) -> dict[str, Any]:
    if len(payload) != COUNTER_PAYLOAD_BYTES:
        _fail("COUNTER_LENGTH", f"COUNTER payload must be {COUNTER_PAYLOAD_BYTES} bytes")
    values = COUNTER_PAYLOAD_STRUCT.unpack(payload)
    decoded_state, natural_overflow = values[-2:]
    if decoded_state != P4_OBSERVATION_NOT_OBSERVED or natural_overflow not in (0, 1):
        _fail("COUNTER_STATE", "COUNTER observation or natural_overflow state is invalid")
    names = (
        "attempted_records", "accepted_records", "dropped_records", "buffer_overflow_dropped_records",
        "rejected_payload_records", "integrity_markers", "flushed_records", "decoded_records_target",
        "wire_frames", "wire_bytes", "crc_failures", "truncations", "backpressure_total",
        "buffer_high_watermark", "buffer_capacity_records",
    )
    counter = dict(zip(names, values[:15], strict=True))
    counter.update({
        "buffer_capacity_unit": "records",
        "buffer_high_watermark_unit": "records",
        "decoded_observation_state": "not_observed",
        "natural_overflow": bool(natural_overflow),
    })
    return counter


def _derive_raw_facts(events: list[tuple[int, int, bytes]]) -> dict[str, Any]:
    previous: int | None = None
    gaps: list[dict[str, int]] = []
    loss_total = 0
    overflow_total = 0
    for event_id, sequence, payload in events:
        if previous is not None:
            if sequence <= previous:
                _fail("EVENT_SEQUENCE", "canonical event sequences are not globally increasing")
            if sequence > previous + 1:
                gaps.append({"after_sequence": previous, "before_sequence": sequence, "count": sequence - previous - 1})
        previous = sequence
        if event_id in (0x4001, 0x4002):
            if len(payload) != 8:
                _fail("INTEGRITY_PAYLOAD", "LOSS/OVERFLOW payload must be HIH (8 bytes)")
            _core, delta, _reason = struct.unpack("<HIH", payload)
            if event_id == 0x4001:
                loss_total += delta
            else:
                overflow_total += delta
    return {
        "decoded_records": len(events),
        "loss_delta_total": loss_total,
        "overflow_delta_total": overflow_total,
        "sequence_gaps": gaps,
        "sequence_gap_total": sum(item["count"] for item in gaps),
    }


def _read_wire(wire: bytes | bytearray | memoryview | str | Path) -> bytes:
    if isinstance(wire, (str, Path)):
        return Path(wire).read_bytes()
    if isinstance(wire, (bytes, bytearray, memoryview)):
        return bytes(wire)
    raise TypeError("wire must be retained bytes or a retained wire path")


def _base_report(*, wire: bytes, output: Path) -> dict[str, Any]:
    return {
        "pass": False,
        "decoder_state": "failed",
        "raw_state": "unavailable",
        "wire_bytes": len(wire),
        "wire_sha256": sha256(wire).hexdigest(),
        "frame_count": 0,
        "canonical_trace_frames": 0,
        "canonical_trace_bytes": 0,
        "boot_frames": 0,
        "config_frames": 0,
        "counter_frames": 0,
        "crc_failures": 0,
        "truncations": 0,
        "errors": [],
        "auxiliary_refs": [],
        "boot": None,
        "config": None,
        "target_counter": None,
        "derived_raw_facts": None,
        "host_collector_facts": None,
        "counter_reconciliation": None,
        "raw_path": str(output),
        "raw_bytes": None,
        "raw_sha256": None,
    }


def _write_exclusive(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("raw output write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def decode_uart_wire(
    wire: bytes | bytearray | memoryview | str | Path,
    raw_output: str | Path,
) -> dict[str, Any]:
    """Strictly verify retained UART wire and exclusively materialize raw trace.

    Validation finishes before any raw output is created.  On malformed wire,
    the returned report is non-passing and ``raw_output`` remains absent.  A
    filesystem write failure can leave a retained partial raw output, which is
    explicitly reported as ``raw_state=partial`` and never passes.
    """
    output = Path(raw_output)
    try:
        retained_wire = _read_wire(wire)
    except Exception as exc:
        report = _base_report(wire=b"", output=output)
        report["errors"].append({"code": "INPUT_ERROR", "message": type(exc).__name__})
        return report

    report = _base_report(wire=retained_wire, output=output)
    raw = bytearray()
    global_header: bytes | None = None
    global_run_id: str | None = None
    expected_segment_seq = 0
    decoded_events: list[tuple[int, int, bytes]] = []
    boot: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    counter: dict[str, Any] | None = None
    offset = 0
    try:
        if len(retained_wire) > MAX_CAPTURE_WIRE_BYTES:
            _fail("WIRE_CAPTURE_LIMIT", "retained wire exceeds the fixed five-second UART physical ceiling")
        while offset < len(retained_wire):
            frame_offset = offset
            if len(retained_wire) - offset < UART_FRAME_STRUCT.size:
                report["truncations"] += 1
                _fail("FRAME_HEADER_TRUNCATION", "wire ends inside a UART frame header")
            magic, version, frame_type, length, expected_crc = UART_FRAME_STRUCT.unpack_from(retained_wire, offset)
            offset += UART_FRAME_STRUCT.size
            if magic != UART_FRAME_MAGIC:
                _fail("FRAME_MAGIC", "UART frame magic is invalid")
            if version != UART_FRAME_VERSION:
                _fail("FRAME_VERSION", "UART frame version is unsupported")
            if frame_type not in _TYPE_NAMES:
                _fail("FRAME_TYPE", "UART frame type is unknown")
            if length > MAX_TRACE_BYTES:
                _fail("FRAME_LENGTH_OVERFLOW", "UART payload exceeds the target frame bound")
            if length > len(retained_wire) - offset:
                report["truncations"] += 1
                _fail("FRAME_PAYLOAD_TRUNCATION", "wire ends inside a UART frame payload")
            payload = retained_wire[offset : offset + length]
            if (zlib.crc32(payload) & 0xFFFFFFFF) != expected_crc:
                report["crc_failures"] += 1
                _fail("FRAME_CRC", "UART frame CRC is invalid")
            offset += length
            report["frame_count"] += 1

            # Actual firmware protocol is identity first, trace next, final
            # counter last.  This rejects late/spliced auxiliary identity.
            if report["frame_count"] == 1 and frame_type != UART_FRAME_BOOT:
                _fail("AUX_ORDER", "BOOT must be the first UART frame")
            if report["frame_count"] == 2 and frame_type != UART_FRAME_CONFIG:
                _fail("AUX_ORDER", "CONFIG must be the second UART frame")
            if report["frame_count"] > 2 and frame_type in (UART_FRAME_BOOT, UART_FRAME_CONFIG):
                _fail("AUX_ORDER", "BOOT/CONFIG may only precede canonical trace frames")
            if counter is not None:
                _fail("AUX_ORDER", "COUNTER must be the final UART frame")

            if frame_type == UART_FRAME_CANONICAL_TRACE:
                first_trace = global_header is None
                expected_segment_seq, first_global, first_run_id, trace_events = _validate_trace_payload(
                    payload, first_trace=first_trace, expected_segment_seq=expected_segment_seq
                )
                if first_trace:
                    global_header = first_global
                    global_run_id = first_run_id
                    raw_part = payload
                else:
                    assert global_header is not None
                    raw_part = global_header + payload
                if len(raw_part) > MAX_TRACE_BYTES or len(raw) + len(raw_part) > MAX_REBUILT_RAW_BYTES:
                    _fail("RAW_LENGTH_OVERFLOW", "rebuilt raw exceeds the target raw buffer bound")
                raw.extend(raw_part)
                decoded_events.extend(trace_events)
                report["canonical_trace_frames"] += 1
                report["canonical_trace_bytes"] += len(payload)
            else:
                name = _TYPE_NAMES[frame_type]
                report[f"{name}_frames"] += 1
                report["auxiliary_refs"].append(
                    {
                        "frame_index": report["frame_count"] - 1,
                        "frame_offset": frame_offset,
                        "length": length,
                        "payload_offset": frame_offset + UART_FRAME_STRUCT.size,
                        "sha256": sha256(payload).hexdigest(),
                        "type": name,
                        "type_code": frame_type,
                    }
                )
                if frame_type == UART_FRAME_BOOT:
                    if boot is not None:
                        _fail("BOOT_DUPLICATE", "wire contains more than one BOOT frame")
                    boot = _parse_boot(payload)
                elif frame_type == UART_FRAME_CONFIG:
                    if config is not None:
                        _fail("CONFIG_DUPLICATE", "wire contains more than one CONFIG frame")
                    config = _parse_config(payload)
                else:
                    if counter is not None:
                        _fail("COUNTER_DUPLICATE", "wire contains more than one COUNTER frame")
                    counter = _parse_counter(payload)
        if global_header is None:
            _fail("MISSING_GLOBAL", "wire contains no canonical global header")
        if boot is None:
            _fail("BOOT_MISSING", "wire contains no BOOT frame")
        if config is None:
            _fail("CONFIG_MISSING", "wire contains no CONFIG frame")
        if counter is None:
            _fail("COUNTER_MISSING", "wire contains no COUNTER frame")
        if report["canonical_trace_frames"] == 0:
            _fail("TRACE_MISSING", "BOOT/CONFIG must be followed by canonical trace frames")
        if global_run_id is None or boot["capture_id"] != global_run_id:
            _fail("CAPTURE_ID_MISMATCH", "BOOT capture_id does not match canonical raw global run_id")
        if config["buffer_capacity_records"] != counter["buffer_capacity_records"]:
            _fail("CAPACITY_MISMATCH", "CONFIG and COUNTER capacities differ")
        if counter["decoded_records_target"] != 0:
            _fail("COUNTER_DECODED_STATE", "not_observed target decoded count must be zero")
        if counter["wire_frames"] != report["frame_count"] or counter["wire_bytes"] != len(retained_wire):
            _fail("WIRE_INVENTORY_MISMATCH", "COUNTER wire inventory does not equal retained wire")
        derived = _derive_raw_facts(decoded_events)
        if derived["sequence_gap_total"] != derived["loss_delta_total"]:
            _fail("SEQUENCE_GAP_MISMATCH", "canonical sequence gaps do not equal LOSS deltas")
        host_facts = {
            "observation_state": "observed",
            "attempted_records": counter["attempted_records"],
            "accepted_records": counter["accepted_records"],
            "dropped_records": counter["dropped_records"],
            "buffer_overflow_dropped_records": counter["buffer_overflow_dropped_records"],
            "rejected_payload_records": counter["rejected_payload_records"],
            "integrity_markers": counter["integrity_markers"],
            "loss_delta_total": derived["loss_delta_total"],
            "overflow_delta_total": derived["overflow_delta_total"],
            "flushed_records": counter["flushed_records"],
            # The target explicitly says this field is not observed.  The host
            # substitutes only a locally verified canonical-raw count.
            "decoded_records": derived["decoded_records"],
            "decoded_records_observation": {
                "state": "observed", "source": "host_canonical_raw",
                "target_observation_state": counter["decoded_observation_state"],
                "target_value": counter["decoded_records_target"],
            },
            "wire_frames": counter["wire_frames"],
            "wire_bytes": counter["wire_bytes"],
            "crc_failures": counter["crc_failures"],
            "truncations": counter["truncations"],
            "backpressure_total": counter["backpressure_total"],
            "buffer_high_watermark": counter["buffer_high_watermark"],
            "buffer_capacity_records": counter["buffer_capacity_records"],
            "buffer_capacity_unit": "records",
            "buffer_high_watermark_unit": "records",
        }
        reconciliation = reconcile_collector_facts(
            host_facts, wire_inventory={"frames": report["frame_count"], "bytes": len(retained_wire)},
            hardware_smoke_noncase=True,
        )
        if not reconciliation["pass"]:
            _fail("COUNTER_RECONCILIATION", ",".join(reconciliation["reasons"]))
        if output.exists():
            _fail("OUTPUT_COLLISION", "raw output already exists and will not be overwritten")
        if not output.parent.is_dir():
            _fail("OUTPUT_DIRECTORY", "raw output directory must already exist")
    except WireDecodeError as exc:
        report["errors"].append({"code": exc.code, "message": str(exc)})
        return report

    raw_bytes = bytes(raw)
    try:
        _write_exclusive(output, raw_bytes)
    except FileExistsError:
        report["errors"].append({"code": "OUTPUT_COLLISION", "message": "raw output already exists and will not be overwritten"})
        return report
    except OSError as exc:
        report["raw_state"] = "partial" if output.exists() else "unavailable"
        report["errors"].append({"code": "RAW_WRITE_ERROR", "message": type(exc).__name__})
        return report

    report.update(
        {
            "pass": True,
            "decoder_state": "complete",
            "raw_state": "available",
            "raw_bytes": len(raw_bytes),
            "raw_sha256": sha256(raw_bytes).hexdigest(),
            "boot": boot,
            "config": config,
            "target_counter": counter,
            "derived_raw_facts": derived,
            "host_collector_facts": host_facts,
            "counter_reconciliation": reconciliation,
        }
    )
    return report

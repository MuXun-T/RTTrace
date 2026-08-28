"""Fail-closed, injected P4 UART wire-recorder primitives.

This module deliberately has no serial-port discovery or opening code. A
caller must validate a resolved CH340 identity, and tests inject every serial
operation. It only retains raw wire material and P4-local sidecars; it does
not construct Case, OAR, or CVR records.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import os
from pathlib import Path
import time
from typing import Any

from .contracts import P4ContractError, canonical_json


PLAN_HASH = "fefa42fed3225f97076d0e8c5499c00b49daaa6bb5a0e1a72394a4905e4a55da"
CH340_BY_ID = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
_RECORDER_NAMES = ("ready.json", "wire.trace", "record_success.json", "failure_preserved.json")


def validate_hardware_request(
    *, capture_mode: str, test_only: bool, case_id: object, device: str,
    baud: int, framing: str, plan_hash: str, confirm: bool,
    identity: Mapping[str, Any] | None = None,
) -> None:
    """Reject every hardware request except the explicitly resolved CH340 port."""
    if capture_mode != "hardware_smoke_noncase" or test_only or case_id is not None:
        raise P4ContractError("hardware recorder is non-Case only")
    if not confirm or plan_hash != PLAN_HASH:
        raise P4ContractError("hardware recorder requires explicit confirmation and frozen plan hash")
    if device != CH340_BY_ID:
        raise P4ContractError("only the resolved CH340 by-id path is allowed")
    if not isinstance(identity, Mapping) or identity.get("vid_pid") != "1a86:7523":
        raise P4ContractError("resolved device identity must be CH340 1a86:7523")
    resolved = next((identity[key] for key in ("resolved_device", "device", "path") if key in identity), None)
    if resolved != device:
        raise P4ContractError("resolved identity does not bind the requested device")
    if baud != 115200 or framing != "8N1-no-flow":
        raise P4ContractError("UART must be 115200 8N1 no flow")


def _require_existing_directory(directory: str | Path) -> Path:
    root = Path(directory)
    if not root.is_dir():
        raise P4ContractError("capture directory must already exist")
    return root


def _safe_json(value: Any) -> Any:
    """Turn diagnostic values into finite, canonical-JSON-safe data."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


def _payload(value: Mapping[str, Any]) -> bytes:
    return canonical_json(_safe_json(dict(value)))


def _write_all(fd: int, data: bytes, write_fd: Callable[[int, bytes], int]) -> None:
    """Write a byte string completely, including through short writes."""
    offset = 0
    while offset < len(data):
        written = write_fd(fd, data[offset:])
        if not isinstance(written, int) or written <= 0:
            raise OSError("wire write made no progress")
        offset += written


def _write_exclusive(path: Path, data: bytes, *, write_fd: Callable[[int, bytes], int] = os.write) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(descriptor, data, write_fd)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _preflight(root: Path, names: tuple[str, ...] = _RECORDER_NAMES) -> None:
    collision = next((name for name in names if (root / name).exists()), None)
    if collision is not None:
        raise P4ContractError(f"recorder no-overwrite rejection: {collision}")


def _line_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"dtr": None, "rts": None, "hupcl": None}
    return {key: _safe_json(value.get(key)) for key in ("dtr", "rts", "hupcl")}


def exclusive_wire_import(directory: str | Path, wire: bytes, *, failure_reason: str | None = None) -> Path:
    """Retain imported wire exactly once, preserving an optional failure sidecar."""
    root = _require_existing_directory(directory)
    names = ("wire.trace", "failure_preserved.json") if failure_reason is not None else ("wire.trace",)
    _preflight(root, names)
    target = root / "wire.trace"
    try:
        _write_exclusive(target, bytes(wire))
        if failure_reason is not None:
            _write_exclusive(root / "failure_preserved.json", _payload({"reason": failure_reason, "state": "partial"}))
    except FileExistsError as exc:
        raise P4ContractError("wire no-overwrite rejection") from exc
    return target


def record_serial_wire(
    directory: str | Path,
    *,
    metadata: Mapping[str, Any],
    open_fd: Callable[[], int],
    read_fd: Callable[[int], bytes],
    duration_s: float,
    max_bytes: int,
    clock: Callable[[], float] = time.monotonic,
    termios_get: Callable[[int], Any] = lambda _fd: None,
    termios_set: Callable[[int], None] = lambda _fd: None,
    line_state: Callable[[int], Mapping[str, Any] | None] = lambda _fd: None,
    close_fd: Callable[[int], None] = os.close,
    write_fd: Callable[[int, bytes], int] = os.write,
    end_condition: Callable[[bytes], bool] = lambda _data: False,
) -> Path:
    """Capture injected serial reads with durable no-overwrite P4 sidecars.

    ``open_fd`` and ``close_fd`` are each invoked at most once. Once a serial
    descriptor was returned, close is attempted exactly once on every path.
    The ready sidecar is fully durable before the serial descriptor is opened.
    """
    if not isinstance(duration_s, (int, float)) or duration_s < 0:
        raise P4ContractError("duration_s must be non-negative")
    if not isinstance(max_bytes, int) or max_bytes < 0:
        raise P4ContractError("max_bytes must be non-negative")
    root = _require_existing_directory(directory)
    ready_data = _payload(metadata)  # Validate serializability before any write.
    _preflight(root)
    ready, wire = root / "ready.json", root / "wire.trace"
    success, failure = root / "record_success.json", root / "failure_preserved.json"
    try:
        _write_exclusive(ready, ready_data)
        wire_descriptor = os.open(wire, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise P4ContractError("recorder no-overwrite rejection") from exc

    serial_descriptor: int | None = None
    close_attempted = False
    bytes_written = 0
    termios_before: Any = None
    termios_after: Any = None
    observed = {"dtr": None, "rts": None, "hupcl": None}
    started = clock()
    termination: str | None = None
    try:
        serial_descriptor = open_fd()
        termios_before = termios_get(serial_descriptor)
        termios_set(serial_descriptor)
        termios_after = termios_get(serial_descriptor)
        observed = _line_state(line_state(serial_descriptor))
        while True:
            if clock() - started >= duration_s:
                termination = "duration"
                break
            if bytes_written >= max_bytes:
                termination = "max_bytes"
                break
            data = read_fd(serial_descriptor)
            if not isinstance(data, (bytes, bytearray, memoryview)):
                raise TypeError("read_fd must return bytes-like data")
            data = bytes(data)
            if not data:
                continue
            is_end = bool(end_condition(data))
            chunk = data[: max_bytes - bytes_written]
            _write_all(wire_descriptor, chunk, write_fd)
            bytes_written += len(chunk)
            if is_end:
                termination = "end_condition"
                break
            if bytes_written >= max_bytes:
                termination = "max_bytes"
                break
        os.fsync(wire_descriptor)
        close_attempted = True
        try:
            close_fd(serial_descriptor)
        finally:
            serial_descriptor = None
        duration = clock() - started
        _write_exclusive(success, _payload({
            "bytes": bytes_written, "config_hash": metadata.get("config_hash"), "device": metadata.get("device"),
            "dtr": observed["dtr"], "duration_s": duration, "hupcl": observed["hupcl"], "rts": observed["rts"],
            "state": "complete", "termination": termination, "termios_after": termios_after, "termios_before": termios_before,
        }))
        return wire
    except Exception as exc:
        close_exception: Exception | None = None
        if serial_descriptor is not None and not close_attempted:
            close_attempted = True
            try:
                close_fd(serial_descriptor)
            except Exception as close_exc:  # Keep the primary failure authoritative.
                close_exception = close_exc
            finally:
                serial_descriptor = None
        try:
            os.fsync(wire_descriptor)
        except OSError:
            pass
        failure_payload: dict[str, Any] = {
            "bytes": bytes_written, "config_hash": metadata.get("config_hash"), "device": metadata.get("device"),
            "dtr": observed["dtr"], "duration_s": clock() - started, "exception_type": type(exc).__name__,
            "hupcl": observed["hupcl"], "rts": observed["rts"], "state": "partial", "termination": termination,
            "termios_after": termios_after, "termios_before": termios_before,
        }
        if close_exception is not None:
            failure_payload["close_exception_type"] = type(close_exception).__name__
        try:
            _write_exclusive(failure, _payload(failure_payload))
        except FileExistsError as collision:
            raise P4ContractError("failure metadata collision") from collision
        raise
    finally:
        os.close(wire_descriptor)

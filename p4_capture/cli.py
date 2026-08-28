"""Fail-closed command surface for P4 preparation and bounded UART capture."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import select
import termios
from typing import Any

from .artifacts import CaptureStore, HardwareArtifactStore, make_raw_inventory
from .contracts import (
    P4ContractError,
    canonical_json,
    make_session_manifest,
    p2_snapshot_from_export,
    validate_collector_config_export,
    validate_session_manifest,
)
from .recorder import PLAN_HASH, record_serial_wire, validate_hardware_request
from .wire_decoder import MIN_POST_GATE_RECORDER_DURATION_SECONDS, MAX_CAPTURE_WIRE_BYTES, decode_uart_wire


class P4CliError(P4ContractError):
    """A command cannot proceed without an explicit offline/test-only input."""


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise P4CliError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(canonical_json(value))


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = canonical_json(value)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("JSON write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _capture_context(directory: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    capture_dir = Path(directory)
    if not capture_dir.is_dir():
        raise P4CliError("prepared capture directory must already exist")
    manifest = _read_json(capture_dir / "session_manifest.json")
    export = _read_json(capture_dir / "collector_config_export.json")
    validate_collector_config_export(export)
    validate_session_manifest(manifest, export=export)
    return capture_dir, manifest, export


def resolve_device_identity(device: str) -> dict[str, str]:
    """Resolve the stable by-id symlink to its Linux USB VID:PID without opening it."""
    resolved_tty = os.path.realpath(device)
    tty_name = Path(resolved_tty).name
    if not tty_name.startswith("tty"):
        raise P4CliError("stable by-id device does not resolve to a tty")
    node = (Path("/sys/class/tty") / tty_name / "device").resolve()
    for candidate in (node, *node.parents):
        vendor, product = candidate / "idVendor", candidate / "idProduct"
        if vendor.is_file() and product.is_file():
            return {
                "vid_pid": f"{vendor.read_text(encoding='ascii').strip().lower()}:{product.read_text(encoding='ascii').strip().lower()}",
                "resolved_device": device,
                "resolved_tty": resolved_tty,
            }
    raise P4CliError("unable to resolve USB VID:PID for stable by-id device")


class RealSerialAdapters:
    """The only real-I/O adapter, intentionally constructed after all checks."""

    def __init__(self, device: str) -> None:
        self.device = device

    def open_fd(self) -> int:
        return os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)

    def read_fd(self, descriptor: int) -> bytes:
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        if not poller.poll(100):  # bounded; record_serial_wire owns the overall duration.
            return b""
        return os.read(descriptor, 4096)

    @staticmethod
    def termios_get(descriptor: int) -> Any:
        return termios.tcgetattr(descriptor)

    @staticmethod
    def termios_set(descriptor: int) -> None:
        attrs = termios.tcgetattr(descriptor)
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        attrs[2] |= termios.CLOCAL | termios.CREAD | termios.CS8
        attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
        attrs[2] |= termios.CS8
        if hasattr(termios, "CRTSCTS"):
            attrs[2] &= ~termios.CRTSCTS
        attrs[0] &= ~(termios.IXON | termios.IXOFF | getattr(termios, "IXANY", 0))
        termios.tcsetattr(descriptor, termios.TCSANOW, attrs)

    @staticmethod
    def line_state(descriptor: int) -> dict[str, bool | None]:
        try:
            import fcntl

            bits = int.from_bytes(fcntl.ioctl(descriptor, termios.TIOCMGET, b"\0\0\0\0"), "little")
            attrs = termios.tcgetattr(descriptor)
            return {
                "dtr": bool(bits & termios.TIOCM_DTR),
                "rts": bool(bits & termios.TIOCM_RTS),
                "hupcl": bool(attrs[2] & termios.HUPCL),
            }
        except OSError:
            return {"dtr": None, "rts": None, "hupcl": None}

    @staticmethod
    def close_fd(descriptor: int) -> None:
        os.close(descriptor)


def build_real_serial_adapters(device: str) -> RealSerialAdapters:
    """Injection seam used by tests; never call this before hardware preflight."""
    return RealSerialAdapters(device)


def _hardware_store_context(args: argparse.Namespace) -> tuple[HardwareArtifactStore, Path, dict[str, Any], dict[str, Any]]:
    store = HardwareArtifactStore(args.external_root)
    capture_dir, manifest, export, _layout = store.resolve(
        board_id=args.board_id, session_id=args.session_id, capture_id=args.capture_id
    )
    return store, capture_dir, manifest, export


def _hardware_context(args: argparse.Namespace) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, str]]:
    if args.test_only:
        raise P4CliError("--test-only cannot authorize hardware recording")
    if not args.hardware_smoke_noncase or not args.confirm_hardware_open:
        raise P4CliError("record-wire requires hardware-smoke-noncase and explicit hardware-open confirmation")
    if args.plan_hash != PLAN_HASH:
        raise P4CliError("record-wire requires the frozen P4 plan hash")
    if not isinstance(args.duration_s, (int, float)) or args.duration_s < MIN_POST_GATE_RECORDER_DURATION_SECONDS:
        raise P4CliError(f"duration must be at least the {MIN_POST_GATE_RECORDER_DURATION_SECONDS}s post-gate transport bound")
    if not isinstance(args.max_bytes, int) or args.max_bytes != MAX_CAPTURE_WIRE_BYTES:
        raise P4CliError(f"max-bytes must equal the frozen whole-wire limit {MAX_CAPTURE_WIRE_BYTES}")
    if not isinstance(args.end_condition_id, str) or not re.fullmatch(r"end:[A-Za-z0-9._:-]+", args.end_condition_id):
        raise P4CliError("end-condition-id must be a preregistered end:<identifier>")
    _store, capture_dir, manifest, export = _hardware_store_context(args)
    if manifest["capture_mode"] != "hardware_smoke_noncase" or manifest["test_only"] is not False or manifest["case_id"] is not None:
        raise P4CliError("prepared capture is not non-Case hardware-smoke material")
    if args.config_hash != export["p2_config_hash"]:
        raise P4CliError("requested config hash does not match prepared export")
    identity = resolve_device_identity(args.device)
    validate_hardware_request(
        capture_mode=manifest["capture_mode"],
        test_only=manifest["test_only"],
        case_id=manifest["case_id"],
        device=args.device,
        baud=115200,
        framing="8N1-no-flow",
        plan_hash=args.plan_hash,
        confirm=args.confirm_hardware_open,
        identity=identity,
    )
    return capture_dir, manifest, export, identity


def prepare(args: argparse.Namespace) -> int:
    if not args.test_only:
        raise P4CliError("prepare requires --test-only in the pre-hardware phase")
    export = _read_json(args.export)
    manifest = make_session_manifest(export, prepared_at=args.prepared_at)
    snapshot = p2_snapshot_from_export(export)
    directory = CaptureStore(args.root).prepare(manifest, export, snapshot)
    print(json.dumps({"test_only": True, "capture_dir": str(directory)}, sort_keys=True))
    return 0


def prepare_hardware(args: argparse.Namespace) -> int:
    if args.test_only:
        raise P4CliError("--test-only cannot prepare a hardware-smoke capture")
    export = _read_json(args.export)
    validate_collector_config_export(export)
    if export["capture_mode"] != "hardware_smoke_noncase" or export["test_only"] is not False or export["case_id"] is not None:
        raise P4CliError("hardware prepare requires non-Case hardware_smoke_noncase export")
    manifest = make_session_manifest(export, prepared_at=args.prepared_at)
    store = HardwareArtifactStore(args.external_root)
    directory = store.prepare(manifest, export, board_id=args.board_id)
    print(json.dumps({
        "board_id": args.board_id, "capture_dir": str(directory), "capture_id": manifest["capture_id"],
        "layout_version": "rtd-p4-hardware-smoke-layout-v1", "state": "prepared", "test_only": False,
    }, sort_keys=True))
    return 0


def start(args: argparse.Namespace) -> int:
    if not args.test_only:
        raise P4CliError("hardware start is blocked: no hardware authorization or device I/O is permitted")
    raise P4CliError("start has no device action in offline mode; use record with an existing test-only file")


def record(args: argparse.Namespace) -> int:
    if not args.test_only:
        raise P4CliError("hardware record is blocked: import an existing file only in --test-only mode")
    store = CaptureStore(args.root)
    manifest = _read_json(Path(args.capture_dir) / "session_manifest.json")
    raw = store.import_raw(args.capture_dir, args.source)
    inventory = make_raw_inventory(manifest, [raw])
    store.write_inventory(args.capture_dir, inventory)
    print(json.dumps({"test_only": True, "raw": raw, "inventory": inventory}, sort_keys=True))
    return 0


def record_wire(args: argparse.Namespace) -> int:
    """Record UART wire only after every non-I/O hardware gate has passed."""
    capture_dir, manifest, _export, identity = _hardware_context(args)
    preflight = {
        "capture_id": manifest["capture_id"],
        "capture_dir": str(capture_dir),
        "config_hash": args.config_hash,
        "device": args.device,
        "identity": identity,
        "state": "preflight",
        "test_only": False,
    }
    if args.dry_run:
        print(json.dumps(preflight, sort_keys=True))
        return 0
    adapters = build_real_serial_adapters(args.device)
    metadata = {
        "capture_id": manifest["capture_id"],
        "config_hash": args.config_hash,
        "device": args.device,
        "end_condition_id": args.end_condition_id,
        "identity": identity,
        "session_id": manifest["session_id"],
        "test_only": False,
    }
    try:
        wire = record_serial_wire(
            capture_dir,
            metadata=metadata,
            open_fd=adapters.open_fd,
            read_fd=adapters.read_fd,
            duration_s=args.duration_s,
            max_bytes=args.max_bytes,
            termios_get=adapters.termios_get,
            termios_set=adapters.termios_set,
            line_state=adapters.line_state,
            close_fd=adapters.close_fd,
            end_condition=lambda _data: False,  # Only the preregistered duration/byte bounds end this minimal recorder.
        )
    except Exception:
        print(json.dumps({**preflight, "failure": str(capture_dir / "failure_preserved.json"), "state": "partial"}, sort_keys=True))
        raise
    print(json.dumps({
        **preflight,
        "ready": str(capture_dir / "ready.json"),
        "result": str(capture_dir / "record_success.json"),
        "state": "complete",
        "wire": str(wire),
    }, sort_keys=True))
    return 0


def decode_wire(args: argparse.Namespace) -> int:
    """Offline-only wire reconstruction; no confirm flag or hardware adapter exists here."""
    if args.external_root is not None:
        if args.capture_dir is not None:
            raise P4CliError("hardware decode cannot accept a user-supplied capture directory")
        if not all((args.board_id, args.session_id, args.capture_id)):
            raise P4CliError("hardware decode requires board-id, session-id, and capture-id")
        _store, capture_dir, _manifest, _export = _hardware_store_context(args)
        expected = {
            "wire": capture_dir / "wire.trace", "raw_output": capture_dir / "raw.trace", "report": capture_dir / "decode_report.json",
        }
        supplied = {"wire": args.wire, "raw_output": args.raw_output, "report": args.report}
        for key, path in supplied.items():
            if path is not None and Path(path) != expected[key]:
                raise P4CliError("hardware decode paths are derived from capture identity and cannot cross captures")
        args.wire, args.raw_output, args.report = (str(expected["wire"]), str(expected["raw_output"]), str(expected["report"]))
    elif args.capture_dir is not None:
        _capture_context(args.capture_dir)
    if not all((args.wire, args.raw_output, args.report)):
        raise P4CliError("offline decode requires wire, raw-output, and report")
    report_path = Path(args.report)
    raw_path = Path(args.raw_output)
    if report_path.exists():
        raise P4CliError("decode report already exists and will not be overwritten")
    if raw_path.exists():
        raise P4CliError("raw output already exists and will not be overwritten")
    if not report_path.parent.is_dir() or not raw_path.parent.is_dir():
        raise P4CliError("raw/report output directories must already exist")
    report = decode_uart_wire(args.wire, raw_path)
    report["report_path"] = str(report_path)
    _write_json_exclusive(report_path, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["pass"] else 1


def seal_hardware(args: argparse.Namespace) -> int:
    if args.test_only:
        raise P4CliError("--test-only cannot seal hardware-smoke material")
    store, _directory, manifest, _export = _hardware_store_context(args)
    seal_path = store.seal(board_id=args.board_id, session_id=args.session_id, capture_id=args.capture_id)
    print(json.dumps({"capture_id": manifest["capture_id"], "seal": str(seal_path), "state": "sealed", "test_only": False}, sort_keys=True))
    return 0


def validate_hardware(args: argparse.Namespace) -> int:
    if args.test_only:
        raise P4CliError("--test-only cannot validate hardware-smoke material")
    store, _directory, manifest, _export = _hardware_store_context(args)
    seal = store.validate(board_id=args.board_id, session_id=args.session_id, capture_id=args.capture_id)
    print(json.dumps({"capture_id": manifest["capture_id"], "sealed": True, "state": "valid", "test_only": False, "seal_digest": seal["record_digest"]}, sort_keys=True))
    return 0


def stop(args: argparse.Namespace) -> int:
    if not args.test_only:
        raise P4CliError("hardware stop is blocked in the pre-hardware phase")
    directory = Path(args.capture_dir)
    marker = directory / "stop.test_only.json"
    if marker.exists():
        raise P4CliError("stop marker already exists; capture is immutable")
    _write_json(marker, {"test_only": True, "state": "stopped", "capture_dir": str(directory)})
    print(json.dumps({"test_only": True, "state": "stopped"}, sort_keys=True))
    return 0


def seal(args: argparse.Namespace) -> int:
    if not args.test_only:
        raise P4CliError("hardware seal is blocked in the pre-hardware phase")
    directory = Path(args.capture_dir)
    manifest = _read_json(directory / "session_manifest.json")
    inventory = _read_json(directory / "raw_inventory.json")
    seal_path = CaptureStore(args.root).seal(directory)
    print(json.dumps({"test_only": True, "seal": str(seal_path), "capture_id": manifest["capture_id"], "inventory_digest": inventory["record_digest"]}, sort_keys=True))
    return 0


def validate(args: argparse.Namespace) -> int:
    if not args.test_only:
        raise P4CliError("hardware validation is blocked in the pre-hardware phase")
    directory = Path(args.capture_dir)
    manifest = _read_json(directory / "session_manifest.json")
    export = _read_json(directory / "collector_config_export.json")
    validate_session_manifest(manifest, export=export)
    store = CaptureStore(args.root)
    inventory = store.verify_inventory(directory)
    seal = store.validate_seal(manifest) if (directory / "seal.json").exists() else None
    print(json.dumps({"test_only": True, "valid": True, "sealed": seal is not None, "capture_id": inventory["capture_id"]}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rtd_p4_capture", description="Fail-closed P4 preparation and bounded UART wire capture")
    parser.add_argument("--root", required=True, help="capture root outside the repository")
    parser.add_argument("--test-only", action="store_true", help="required for all executable pre-hardware operations")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--export", required=True)
    prepare_parser.add_argument("--prepared-at", default="offline-test-only")
    prepare_hardware_parser = sub.add_parser("prepare-hardware")
    prepare_hardware_parser.add_argument("--external-root", required=True)
    prepare_hardware_parser.add_argument("--board-id", required=True)
    prepare_hardware_parser.add_argument("--export", required=True)
    prepare_hardware_parser.add_argument("--prepared-at", default="hardware-smoke-prepared")
    start_parser = sub.add_parser("start")
    start_parser.set_defaults()
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--capture-dir", required=True)
    record_parser.add_argument("--source", required=True)
    stop_parser = sub.add_parser("stop")
    stop_parser.add_argument("--capture-dir", required=True)
    seal_parser = sub.add_parser("seal")
    seal_parser.add_argument("--capture-dir", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--capture-dir", required=True)
    record_wire_parser = sub.add_parser("record-wire")
    record_wire_parser.add_argument("--external-root", required=True)
    record_wire_parser.add_argument("--board-id", required=True)
    record_wire_parser.add_argument("--session-id", required=True)
    record_wire_parser.add_argument("--capture-id", required=True)
    record_wire_parser.add_argument("--hardware-smoke-noncase", action="store_true")
    record_wire_parser.add_argument("--confirm-hardware-open", action="store_true")
    record_wire_parser.add_argument("--plan-hash", required=True)
    record_wire_parser.add_argument("--device", required=True)
    record_wire_parser.add_argument("--config-hash", required=True)
    record_wire_parser.add_argument("--duration-s", type=float, required=True)
    record_wire_parser.add_argument("--max-bytes", type=int, required=True)
    record_wire_parser.add_argument("--end-condition-id", required=True)
    record_wire_parser.add_argument("--dry-run", action="store_true")
    decode_wire_parser = sub.add_parser("decode-wire")
    decode_wire_parser.add_argument("--capture-dir")
    decode_wire_parser.add_argument("--external-root")
    decode_wire_parser.add_argument("--board-id")
    decode_wire_parser.add_argument("--session-id")
    decode_wire_parser.add_argument("--capture-id")
    decode_wire_parser.add_argument("--wire")
    decode_wire_parser.add_argument("--raw-output")
    decode_wire_parser.add_argument("--report")
    seal_hardware_parser = sub.add_parser("seal-hardware")
    validate_hardware_parser = sub.add_parser("validate-hardware")
    for hardware_parser in (seal_hardware_parser, validate_hardware_parser):
        hardware_parser.add_argument("--external-root", required=True)
        hardware_parser.add_argument("--board-id", required=True)
        hardware_parser.add_argument("--session-id", required=True)
        hardware_parser.add_argument("--capture-id", required=True)
    prepare_parser.set_defaults(handler=prepare)
    prepare_hardware_parser.set_defaults(handler=prepare_hardware)
    start_parser.set_defaults(handler=start)
    record_parser.set_defaults(handler=record)
    stop_parser.set_defaults(handler=stop)
    seal_parser.set_defaults(handler=seal)
    validate_parser.set_defaults(handler=validate)
    record_wire_parser.set_defaults(handler=record_wire)
    decode_wire_parser.set_defaults(handler=decode_wire)
    seal_hardware_parser.set_defaults(handler=seal_hardware)
    validate_hardware_parser.set_defaults(handler=validate_hardware)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (P4ContractError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

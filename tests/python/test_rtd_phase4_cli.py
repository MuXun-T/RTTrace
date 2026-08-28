from __future__ import annotations

import json
import struct
from pathlib import Path
import zlib

import pytest

from p4_capture.cli import main
from p4_capture.artifacts import HardwareArtifactStore
from p4_capture.contracts import canonical_json, make_session_manifest
from p4_capture.recorder import CH340_BY_ID, PLAN_HASH
from p4_capture.wire_decoder import (
    BOOT_PAYLOAD_STRUCT, CONFIG_PAYLOAD_STRUCT, COUNTER_PAYLOAD_STRUCT, MIN_POST_GATE_RECORDER_DURATION_SECONDS,
    MAX_CAPTURE_WIRE_BYTES, UART_FRAME_STRUCT,
)
from parser.codec import CHUNK_HEADER_STRUCT, GLOBAL_HEADER_STRUCT, SEGMENT_META_STRUCT
from p4_phase4_support import config_export


BOARD_ID = "board:p4-cli"


def _prepared_hardware(tmp_path: Path, *, test_only: bool = False) -> tuple[Path, Path, dict[str, object]]:
    export = config_export(capture_id="capture:p4-hw", session_id="session:p4-hw", test_only=test_only)
    manifest = make_session_manifest(export, prepared_at="p4-cli-test")
    root = tmp_path / ("test-only" if test_only else "hardware")
    if test_only:
        directory = root / "not-a-hardware-layout"
        directory.mkdir(parents=True)
        (directory / "session_manifest.json").write_bytes(canonical_json(manifest))
        (directory / "collector_config_export.json").write_bytes(canonical_json(export))
    else:
        directory = HardwareArtifactStore(root).prepare(manifest, export, board_id=BOARD_ID)
    return root, directory, export


def _record_args(external_root: Path, config_hash: str) -> list[str]:
    return [
        "--root", str(external_root.parent), "record-wire", "--external-root", str(external_root),
        "--board-id", BOARD_ID, "--session-id", "session:p4-hw", "--capture-id", "capture:p4-hw",
        "--hardware-smoke-noncase", "--confirm-hardware-open", "--plan-hash", PLAN_HASH,
        "--device", CH340_BY_ID, "--config-hash", config_hash,
        "--duration-s", str(MIN_POST_GATE_RECORDER_DURATION_SECONDS), "--max-bytes", str(MAX_CAPTURE_WIRE_BYTES), "--end-condition-id", "end:bounded-test",
    ]


def _valid_wire() -> bytes:
    global_header = GLOBAL_HEADER_STRUCT.pack(
        0x54524345, 1, 1, 2, 1, 2, 1, b"dwt_cyccnt\0", b"rtd-p4-arm-bounded-v1\0", b"capture:p4-wire\0"
    )
    segment = SEGMENT_META_STRUCT.pack(0x53474D32, 2, SEGMENT_META_STRUCT.size, 0, 0, 1, 0, 0)
    chunk = CHUNK_HEADER_STRUCT.pack(0x43484B31, 2, 0, 0, 0, 0, 0, 0, 0, 1, 0)
    payload = global_header + segment + chunk
    def frame(frame_type: int, content: bytes) -> bytes:
        return UART_FRAME_STRUCT.pack(0x5234, 1, frame_type, len(content), zlib.crc32(content) & 0xFFFFFFFF) + content

    canonical = frame(1, payload)
    boot = BOOT_PAYLOAD_STRUCT.pack(1, 8_000_000, b"capture:p4-wire")
    config = CONFIG_PAYLOAD_STRUCT.pack(64, 1, 115200, 0x00080101, 8_000_000, 1, 0, b"a" * 64, b"b" * 64)
    prefix = frame(2, boot) + frame(3, config) + canonical
    counter = COUNTER_PAYLOAD_STRUCT.pack(0, 0, 0, 0, 0, 0, 0, 0, 4, len(prefix) + UART_FRAME_STRUCT.size + COUNTER_PAYLOAD_STRUCT.size,
                                          0, 0, 0, 0, 64, 0, 0)
    return prefix + frame(4, counter)


def test_cli_refuses_hardware_start_without_device_io(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--root", str(tmp_path), "start"])


def test_cli_prepare_and_validate_are_test_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export_path = tmp_path / "export.json"
    export_path.write_bytes(canonical_json(config_export()))
    root = tmp_path / "captures"
    assert main(["--root", str(root), "--test-only", "prepare", "--export", str(export_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["test_only"] is True
    capture_dir = Path(output["capture_dir"])
    source = tmp_path / "synthetic-test-only.trace"
    source.write_bytes(b"test-only raw import")
    assert main(
        [
            "--root",
            str(root),
            "--test-only",
            "record",
            "--capture-dir",
            str(capture_dir),
            "--source",
            str(source),
        ]
    ) == 0
    capsys.readouterr()
    assert main(["--root", str(root), "--test-only", "validate", "--capture-dir", str(capture_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"capture_id": "capture:p4-a", "sealed": False, "test_only": True, "valid": True}


def test_record_wire_rejects_all_missing_or_unsafe_gates_without_opening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hardware_root, _hardware_dir, export = _prepared_hardware(tmp_path)
    test_only_root, _test_only_dir, _ = _prepared_hardware(tmp_path, test_only=True)
    calls = {"open": 0}

    class Adapters:
        def open_fd(self):
            calls["open"] += 1
            return 10

    monkeypatch.setattr("p4_capture.cli.build_real_serial_adapters", lambda _device: Adapters())
    monkeypatch.setattr(
        "p4_capture.cli.resolve_device_identity",
        lambda device: {"vid_pid": "1a86:7523", "resolved_device": device, "resolved_tty": "/dev/ttyUSB0"},
    )
    base = _record_args(hardware_root, str(export["p2_config_hash"]))

    def without(argv: list[str], flag: str) -> list[str]:
        index = argv.index(flag)
        return argv[:index] + argv[index + 1 :]

    def replace_value(argv: list[str], flag: str, value: str) -> list[str]:
        result = list(argv)
        result[result.index(flag) + 1] = value
        return result

    cases = [
        without(base, "--hardware-smoke-noncase"),
        without(base, "--confirm-hardware-open"),
        replace_value(base, "--plan-hash", "wrong"),
        replace_value(base, "--duration-s", str(MIN_POST_GATE_RECORDER_DURATION_SECONDS - 1)),
        replace_value(base, "--max-bytes", str(MAX_CAPTURE_WIRE_BYTES - 1)),
        ["--root", str(tmp_path), "--test-only", *base[2:]],
        replace_value(base, "--device", "/dev/ttyACM0"),
        _record_args(test_only_root, str(export["p2_config_hash"])),
    ]
    for argv in cases:
        with pytest.raises(SystemExit):
            main(argv)
    assert calls["open"] == 0


def test_record_wire_uses_mocked_adapter_exactly_once_and_dry_run_never_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    external_root, capture_dir, export = _prepared_hardware(tmp_path)
    calls = {"open": 0, "close": 0}

    class Adapters:
        def open_fd(self):
            calls["open"] += 1
            return 29

        @staticmethod
        def read_fd(_fd):
            return b"w" * MAX_CAPTURE_WIRE_BYTES

        @staticmethod
        def termios_get(_fd):
            return {"baud": 115200}

        @staticmethod
        def termios_set(_fd):
            return None

        @staticmethod
        def line_state(_fd):
            return {"dtr": None, "rts": None, "hupcl": None}

        def close_fd(self, _fd):
            calls["close"] += 1

    monkeypatch.setattr("p4_capture.cli.build_real_serial_adapters", lambda _device: Adapters())
    monkeypatch.setattr(
        "p4_capture.cli.resolve_device_identity",
        lambda device: {"vid_pid": "1a86:7523", "resolved_device": device, "resolved_tty": "/dev/ttyUSB0"},
    )
    argv = _record_args(external_root, str(export["p2_config_hash"]))
    assert main([*argv, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "preflight" and calls == {"open": 0, "close": 0}
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "complete" and len(Path(report["wire"]).read_bytes()) == MAX_CAPTURE_WIRE_BYTES
    assert calls == {"open": 1, "close": 1}


def test_decode_wire_is_offline_and_preserves_duplicate_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wire = tmp_path / "preserved.wire"
    wire.write_bytes(_valid_wire())
    raw, report = tmp_path / "raw.trace", tmp_path / "decode.json"
    argv = ["--root", str(tmp_path), "decode-wire", "--wire", str(wire), "--raw-output", str(raw), "--report", str(report)]
    assert main(argv) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["pass"] and raw.exists() and json.loads(report.read_text(encoding="ascii"))["pass"]

    duplicate_report = tmp_path / "duplicate.json"
    with pytest.raises(SystemExit):
        main(["--root", str(tmp_path), "decode-wire", "--wire", str(wire), "--raw-output", str(raw), "--report", str(duplicate_report)])
    assert raw.exists() and not duplicate_report.exists()

    bad_raw, bad_report = tmp_path / "bad.trace", tmp_path / "bad.json"
    bad = tmp_path / "bad.wire"
    bad.write_bytes(b"bad")
    assert main(["--root", str(tmp_path), "decode-wire", "--wire", str(bad), "--raw-output", str(bad_raw), "--report", str(bad_report)]) == 1
    assert not bad_raw.exists() and not json.loads(bad_report.read_text(encoding="ascii"))["pass"]


def test_hardware_cli_layout_roundtrip_derives_paths_without_cross_capture_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export = config_export(capture_id="capture:p4-round", session_id="session:p4-round", test_only=False)
    export_path, external = tmp_path / "hardware-export.json", tmp_path / "external"
    export_path.write_bytes(canonical_json(export))
    prepare = ["--root", str(tmp_path), "prepare-hardware", "--external-root", str(external), "--board-id", BOARD_ID, "--export", str(export_path)]
    assert main(prepare) == 0
    capture_dir = Path(json.loads(capsys.readouterr().out)["capture_dir"])
    (capture_dir / "wire.trace").write_bytes(_valid_wire())
    identity = ["--external-root", str(external), "--board-id", BOARD_ID, "--session-id", "session:p4-round", "--capture-id", "capture:p4-round"]
    assert main(["--root", str(tmp_path), "decode-wire", *identity]) == 0
    assert json.loads(capsys.readouterr().out)["pass"] and (capture_dir / "raw.trace").is_file()
    with pytest.raises(SystemExit):
        main(["--root", str(tmp_path), "decode-wire", *identity, "--wire", str(tmp_path / "cross.wire")])
    assert main(["--root", str(tmp_path), "seal-hardware", *identity]) == 0
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "validate-hardware", *identity]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "valid"

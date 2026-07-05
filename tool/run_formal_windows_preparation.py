from __future__ import annotations

import argparse
import ctypes
import os
import platform
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tool.formal_windows_common import (
    add_common_arguments,
    artifact_record,
    build_config,
    checksum_file,
    default_source_report_path,
    ensure_preparation_aliases,
    file_timestamp,
    json_load,
    json_write,
    run_subprocess,
)
from tool.prepare_patent_10_4_execution_prep import _input_qualification_report, _package_version


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat()


def _memory_gb() -> float | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32),
            ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("sullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return round(float(status.ullTotalPhys) / (1024**3), 2)


def _environment_summary(
    *,
    trace_path: Path,
    scratch_dir: Path,
    preflight_path: Path | None,
    env_check_path: Path | None,
) -> dict[str, Any]:
    payload = {
        "captured_at": _iso_now(),
        "platform": "windows" if os.name == "nt" else platform.system().lower(),
        "host": {
            "hostname": socket.gethostname(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "packages": {
            "PySide6": _package_version("PySide6"),
            "pyqtgraph": _package_version("pyqtgraph"),
            "pytest": _package_version("pytest"),
        },
        "input_storage": {
            "trace_path": str(trace_path),
            "trace_sha256": checksum_file(trace_path),
            "trace_size_bytes": int(trace_path.stat().st_size),
            "last_write_time": file_timestamp(trace_path),
        },
        "scratch": {
            "path": str(scratch_dir),
            "exists": scratch_dir.exists(),
        },
        "source_control": {
            "repo_revision": None,
            "revision_kind": "non_git_archive",
            "note": "Current workspace is not a git repository; use archive hash or directory snapshot for external audit.",
        },
        "execution": {
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or None,
            "desktop_preflight_report": str(preflight_path) if preflight_path is not None else None,
            "desktop_env_report": str(env_check_path) if env_check_path is not None else None,
            "memory_gb": _memory_gb(),
            "windows_version_display": platform.platform(),
        },
    }
    if scratch_dir.exists():
        usage = shutil.disk_usage(scratch_dir)
        payload["scratch"].update(
            {
                "free_space_bytes": int(usage.free),
                "total_space_bytes": int(usage.total),
            }
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_formal_windows_preparation")
    add_common_arguments(parser, include_scratch_dir=True)
    parser.add_argument(
        "--source-report",
        type=Path,
        default=None,
        help="Frozen source report used to prove dense semantic alignment. Defaults to the checked-in 2026-04-16 frozen source report.",
    )
    parser.add_argument("--skip-desktop-preflight", action="store_true")
    parser.add_argument("--skip-env-check", action="store_true")
    args = parser.parse_args(argv)

    config = build_config(args)
    config.prep_root.mkdir(parents=True, exist_ok=True)
    if config.scratch_dir is None:
        raise SystemExit("--scratch-dir is required")
    config.scratch_dir.mkdir(parents=True, exist_ok=True)

    preflight_path = config.prep_root / "desktop_preflight_windows_formal_10_4.json"
    env_check_path = config.prep_root / "desktop_env_windows_formal_10_4.json"

    if not args.skip_desktop_preflight:
        run_subprocess(
            [
                sys.executable,
                str(config.repo_root / "tool" / "desktop_validation_preflight.py"),
                "--baseline-input",
                str(config.trace_path),
                "--candidate-input",
                str(config.trace_path),
                "--scratch-dir",
                str(config.scratch_dir),
                "--load-timeout-s",
                "600",
                "--output",
                str(preflight_path),
            ],
            cwd=config.repo_root,
        )
    if not args.skip_env_check:
        run_subprocess(
            [
                sys.executable,
                str(config.repo_root / "tool" / "check_desktop_env.py"),
                "--output",
                str(env_check_path),
            ],
            cwd=config.repo_root,
        )

    source_report = args.source_report
    if source_report is None:
        candidate = default_source_report_path(config.repo_root)
        source_report = candidate if candidate.exists() else None
    source_report_path = source_report.expanduser().resolve() if source_report is not None else None
    source_report_payload = None
    if source_report_path is not None and source_report_path.exists():
        source_report_payload = json_load(source_report_path)

    windows_env_path = config.prep_root / "windows_environment_summary.json"
    json_write(
        windows_env_path,
        _environment_summary(
            trace_path=config.trace_path,
            scratch_dir=config.scratch_dir,
            preflight_path=preflight_path if preflight_path.exists() else None,
            env_check_path=env_check_path if env_check_path.exists() else None,
        ),
    )

    qualification = _input_qualification_report(
        trace_path=config.trace_path,
        source_report_path=source_report_path,
        source_report=source_report_payload,
    )
    qualification["platform"] = "windows"
    qualification["run_scope"] = "patent_10_4_formal"
    qualification["source_record"] = {
        "environment_summary": str(windows_env_path),
        "desktop_preflight_report": str(preflight_path) if preflight_path.exists() else None,
        "desktop_env_report": str(env_check_path) if env_check_path.exists() else None,
    }
    qualification["trace_sha256_matches_expected"] = (
        qualification.get("trace_sha256") == config.expected_trace_sha256
    )
    qualification["trace_size_bytes_matches_expected"] = (
        int(qualification.get("trace_size_bytes") or 0) == config.expected_trace_size_bytes
    )
    input_report_path = config.prep_root / "input_qualification_report_windows.json"
    json_write(input_report_path, qualification)

    aliases = ensure_preparation_aliases(config)
    prep_manifest = {
        "generated_at": _iso_now(),
        "platform": "windows",
        "archive_root": str(config.archive_root),
        "artifacts": [
            artifact_record("preparation.windows_environment_summary", windows_env_path),
            artifact_record("preparation.input_qualification_report_windows", input_report_path),
            artifact_record("preparation.compat_environment_summary", aliases["compat_environment_summary"]),
            artifact_record("preparation.compat_input_qualification_report", aliases["compat_input_qualification_report"]),
            artifact_record("preparation.desktop_preflight", preflight_path),
            artifact_record("preparation.desktop_env_check", env_check_path),
        ],
    }
    json_write(config.prep_root / "preparation_manifest_windows.json", prep_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

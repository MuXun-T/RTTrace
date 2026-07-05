from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from importlib import metadata
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop import PG_AVAILABLE, PYSIDE_AVAILABLE

FORMAL_INPUT_BYTES = 1024 * 1024 * 1024
NON_LOCAL_FS_TYPES = {
    "9p",
    "cifs",
    "fuse.vmhgfs",
    "fuse.vmhgfs-fuse",
    "fuse.sshfs",
    "nfs",
    "nfs4",
    "smbfs",
    "sshfs",
    "vmhgfs",
    "vmhgfs-fuse",
}
DEFAULT_EXPECTED_EXPORT_MULTIPLIER = 2.0
DEFAULT_MIN_LARGE_INPUT_TIMEOUT_SECONDS = 300.0
DEFAULT_SAFETY_MARGIN_BYTES = 2 * 1024 * 1024 * 1024


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _resolve_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _is_local_disk_fs_type(fs_type: str | None) -> bool | None:
    if fs_type is None:
        return None
    normalized = str(fs_type).strip().lower()
    if not normalized:
        return None
    if normalized in NON_LOCAL_FS_TYPES:
        return False
    if normalized.startswith("fuse."):
        subtype = normalized.split(".", 1)[1]
        if subtype in NON_LOCAL_FS_TYPES:
            return False
    return True


def _linux_mount_info(path: Path) -> dict[str, object]:
    mounts = Path("/proc/mounts")
    if not mounts.exists():
        return {
            "mount_point": None,
            "fs_type": None,
            "local_disk_hint": None,
        }

    target = str(path.resolve())
    best_match: tuple[str, str] | None = None
    for line in mounts.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point = parts[1].replace("\\040", " ")
        fs_type = parts[2]
        if target.startswith(mount_point.rstrip("/") + "/") or target == mount_point:
            if best_match is None or len(mount_point) > len(best_match[0]):
                best_match = (mount_point, fs_type)

    if best_match is None:
        return {
            "mount_point": None,
            "fs_type": None,
            "local_disk_hint": None,
        }

    mount_point, fs_type = best_match
    return {
        "mount_point": mount_point,
        "fs_type": fs_type,
        "local_disk_hint": _is_local_disk_fs_type(fs_type),
    }


def _path_size(path_value: str | None) -> tuple[Path | None, bool, int | None]:
    if not path_value:
        return None, False, None
    path = Path(path_value).expanduser().resolve()
    exists = path.exists() and path.is_file()
    size_bytes = path.stat().st_size if exists else None
    return path, exists, size_bytes


def _large_input_preflight(
    *,
    baseline_input: str | None,
    candidate_input: str | None,
    scratch_dir: str | None,
    load_timeout_s: float | None,
    expected_export_multiplier: float,
    min_free_space_gb: float | None,
    ready_for_runtime_smoke: bool,
) -> dict[str, object]:
    enabled = any(value is not None for value in (baseline_input, candidate_input, scratch_dir, load_timeout_s))
    if not enabled:
        return {
            "enabled": False,
            "ready_for_large_input_perf": False,
            "blocking_reasons": ["large_input_arguments_not_provided"],
        }

    baseline_path, baseline_exists, baseline_size_bytes = _path_size(baseline_input)
    candidate_path, candidate_exists, candidate_size_bytes = _path_size(candidate_input)
    blocking_reasons: list[str] = []
    if not baseline_exists:
        blocking_reasons.append("baseline_input_missing")
    if not candidate_exists:
        blocking_reasons.append("candidate_input_missing")

    sizes = [size for size in (baseline_size_bytes, candidate_size_bytes) if isinstance(size, int)]
    max_input_bytes = max(sizes) if sizes else 0
    estimated_export_bytes = int(max_input_bytes * expected_export_multiplier) if max_input_bytes else 0
    estimated_required_bytes = max_input_bytes + estimated_export_bytes + DEFAULT_SAFETY_MARGIN_BYTES if max_input_bytes else 0
    formal_size_ready = bool(
        isinstance(baseline_size_bytes, int)
        and isinstance(candidate_size_bytes, int)
        and baseline_size_bytes >= FORMAL_INPUT_BYTES
        and candidate_size_bytes >= FORMAL_INPUT_BYTES
    )
    if not formal_size_ready:
        blocking_reasons.append("formal_input_lt_1gb")

    scratch_path = Path(scratch_dir).expanduser().resolve() if scratch_dir else None
    scratch_dir_exists = bool(scratch_path and scratch_path.exists() and scratch_path.is_dir())
    scratch_dir_writable = bool(scratch_dir_exists and scratch_path is not None and os.access(scratch_path, os.W_OK))
    if scratch_path is None:
        blocking_reasons.append("scratch_dir_missing")
    elif not scratch_dir_exists:
        blocking_reasons.append("scratch_dir_not_found")
    elif not scratch_dir_writable:
        blocking_reasons.append("scratch_dir_not_writable")

    usage_target = _resolve_existing_parent(scratch_path) if scratch_path is not None else Path.cwd()
    usage = shutil.disk_usage(usage_target)
    free_space_bytes = int(usage.free)
    min_free_space_bytes = (
        int(min_free_space_gb * 1024 * 1024 * 1024)
        if isinstance(min_free_space_gb, (int, float))
        else estimated_required_bytes
    )
    disk_ready = free_space_bytes >= min_free_space_bytes
    if not disk_ready:
        blocking_reasons.append("insufficient_free_space")

    mount_info = _linux_mount_info(scratch_path or usage_target) if platform.system().lower().startswith("linux") else {
        "mount_point": None,
        "fs_type": None,
        "local_disk_hint": None,
    }
    if mount_info.get("local_disk_hint") is False:
        blocking_reasons.append("scratch_dir_not_local_disk")

    load_timeout_value = float(load_timeout_s) if load_timeout_s is not None else None
    load_timeout_ready = bool(
        load_timeout_value is not None and load_timeout_value >= DEFAULT_MIN_LARGE_INPUT_TIMEOUT_SECONDS
    )
    if not load_timeout_ready:
        blocking_reasons.append("load_timeout_too_small")

    if not ready_for_runtime_smoke:
        blocking_reasons.append("desktop_runtime_dependencies_not_ready")

    return {
        "enabled": True,
        "baseline_path": str(baseline_path) if baseline_path else None,
        "candidate_path": str(candidate_path) if candidate_path else None,
        "baseline_exists": baseline_exists,
        "candidate_exists": candidate_exists,
        "baseline_size_bytes": baseline_size_bytes,
        "candidate_size_bytes": candidate_size_bytes,
        "formal_size_ready": formal_size_ready,
        "scratch_dir": str(scratch_path) if scratch_path else None,
        "scratch_dir_exists": scratch_dir_exists,
        "scratch_dir_writable": scratch_dir_writable,
        "scratch_mount_point": mount_info.get("mount_point"),
        "scratch_fs_type": mount_info.get("fs_type"),
        "scratch_local_disk_hint": mount_info.get("local_disk_hint"),
        "free_space_bytes": free_space_bytes,
        "estimated_export_bytes": estimated_export_bytes,
        "estimated_required_bytes": estimated_required_bytes,
        "min_free_space_bytes": min_free_space_bytes,
        "disk_ready": disk_ready,
        "load_timeout_s": load_timeout_value,
        "load_timeout_ready": load_timeout_ready,
        "expected_export_multiplier": expected_export_multiplier,
        "ready_for_large_input_perf": len(blocking_reasons) == 0,
        "blocking_reasons": blocking_reasons,
    }


def _payload(
    *,
    baseline_input: str | None,
    candidate_input: str | None,
    scratch_dir: str | None,
    load_timeout_s: float | None,
    expected_export_multiplier: float,
    min_free_space_gb: float | None,
) -> dict[str, object]:
    ready = bool(PYSIDE_AVAILABLE and PG_AVAILABLE)
    next_steps: list[str] = []
    if not PYSIDE_AVAILABLE or not PG_AVAILABLE:
        next_steps.append("Install desktop dependencies with `bash tool/bootstrap_desktop_env.sh` or `pip install -e .[desktop,dev]`.")
    next_steps.append("Run `QT_QPA_PLATFORM=offscreen python3 tool/check_desktop_env.py --output <path>` to capture a GUI smoke report.")
    large_input_preflight = _large_input_preflight(
        baseline_input=baseline_input,
        candidate_input=candidate_input,
        scratch_dir=scratch_dir,
        load_timeout_s=load_timeout_s,
        expected_export_multiplier=expected_export_multiplier,
        min_free_space_gb=min_free_space_gb,
        ready_for_runtime_smoke=ready,
    )
    if large_input_preflight.get("enabled"):
        if large_input_preflight.get("ready_for_large_input_perf"):
            next_steps.append("Large-input perf preflight is ready; run desktop perf acceptance with `--acceptance-scope perf_only`.")
        else:
            next_steps.append("Resolve large-input preflight blocking_reasons before running formal 1GB perf acceptance.")
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "packages": {
            "PySide6": {
                "available": bool(PYSIDE_AVAILABLE),
                "version": _package_version("PySide6"),
            },
            "pyqtgraph": {
                "available": bool(PG_AVAILABLE),
                "version": _package_version("pyqtgraph"),
            },
        },
        "ready_for_runtime_smoke": ready,
        "large_input_preflight": large_input_preflight,
        "next_steps": next_steps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="desktop_validation_preflight")
    parser.add_argument("--output")
    parser.add_argument("--baseline-input")
    parser.add_argument("--candidate-input")
    parser.add_argument("--scratch-dir")
    parser.add_argument("--load-timeout-s", type=float)
    parser.add_argument("--expected-export-multiplier", type=float, default=DEFAULT_EXPECTED_EXPORT_MULTIPLIER)
    parser.add_argument("--min-free-space-gb", type=float)
    args = parser.parse_args(argv)

    payload = _payload(
        baseline_input=args.baseline_input,
        candidate_input=args.candidate_input,
        scratch_dir=args.scratch_dir,
        load_timeout_s=args.load_timeout_s,
        expected_export_multiplier=float(args.expected_export_multiplier),
        min_free_space_gb=args.min_free_space_gb,
    )
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
    large_input = payload.get("large_input_preflight", {})
    large_ready = True
    if isinstance(large_input, dict) and large_input.get("enabled"):
        large_ready = bool(large_input.get("ready_for_large_input_perf"))
    return 0 if payload["ready_for_runtime_smoke"] and large_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

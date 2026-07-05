from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
import traceback
import tracemalloc
import zlib
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    # When executed as a script, `tool/` is on `sys.path`, so the unqualified import works.
    from desktop_validation_preflight import _linux_mount_info  # type: ignore[import-not-found]
except ModuleNotFoundError:
    # When imported as a module (e.g. unit tests), prefer the qualified path.
    from tool.desktop_validation_preflight import _linux_mount_info
from desktop.sample_data import write_scenario
from desktop.services import CompareService, ExportService, ReproService, WorkspaceController
from parser import prs_Prescan
from parser.codec import (
    CHUNK_HEADER_STRUCT,
    GLOBAL_HEADER_STRUCT,
    SEGMENT_META_STRUCT,
    TRACE_CHUNK_MAGIC,
    TRACE_FORMAT_MAGIC,
    TRACE_SEGMENT_META_MAGIC,
)
from parser.models import EventTableQuery, TaskStateQuery
from parser.parser_process_agent import ParserProcessAgent

PROFILE_DEFAULTS: dict[str, dict[str, float | int]] = {
    "smoke": {
        "repeat": 12,
        "soak_iterations": 2,
        "cache_budget_mb": 8.0,
    },
    "medium": {
        "repeat": 48,
        "soak_iterations": 4,
        "cache_budget_mb": 8.0,
    },
    "large": {
        "repeat": 96,
        "soak_iterations": 6,
        "cache_budget_mb": 8.0,
    },
}

FIRST_SCREEN_THRESHOLD_SECONDS = 10.0
PEAK_MEMORY_THRESHOLD_MB = 4096.0
FORMAL_INPUT_BYTES = 1024 * 1024 * 1024
RECOMMENDED_LARGE_INPUT_TIMEOUT_SECONDS = 600.0
INPUT_PROVENANCE_REAL_EXTERNAL = "real_external"
INPUT_PROVENANCE_SYNTHETIC_PADDED = "synthetic_padded"
INPUT_PROVENANCE_PUBLIC_RTOS_DERIVED = "public_rtos_derived"
INPUT_PROVENANCE_PUBLIC_RTOS_SEEDED_PADDED = "public_rtos_seeded_padded"
PUBLIC_RTOS_PRODUCER_PREFIX = "public-rtos-derived-"
EXPORT_CONTRACT_VERSION = "v2"
PERF_ONLY_INDEX_BUILD_MODE = "minimal"
EXPORT_WRITE_TIMING_KEYS = (
    "write_seconds",
    "normalize_seconds",
    "prepare_seconds",
    "encode_trace_seconds",
    "json_dump_seconds",
    "csv_write_seconds",
    "checksum_seconds",
    "reference_copy_seconds",
)


def _platform_label(raw: str | None = None) -> str:
    system = (raw or platform.system()).strip().lower()
    if system.startswith("win"):
        return "windows"
    if system.startswith("linux"):
        return "linux"
    if system.startswith("darwin") or system.startswith("mac"):
        return "macos"
    return system or "unknown"


def _canonical_mode(raw_mode: str) -> str:
    return "desktop_perf_acceptance" if raw_mode == "desktop_perf_acceptance_linux" else raw_mode


def _platform_display_name(platform_name: str) -> str:
    return {
        "linux": "Linux",
        "windows": "Windows",
        "macos": "macOS",
    }.get(platform_name, platform_name or "unknown")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _crc32_zeroes(length: int) -> int:
    crc = 0
    remaining = int(length)
    zero_block = b"\0" * (1024 * 1024)
    while remaining > 0:
        chunk = min(remaining, len(zero_block))
        crc = zlib.crc32(zero_block[:chunk], crc)
        remaining -= chunk
    return crc & 0xFFFFFFFF


def _read_exact(handle, length: int) -> bytes | None:
    payload = handle.read(length)
    if len(payload) != length:
        return None
    return payload


def _chunk_payload_is_all_zero(handle, payload_size: int) -> bool:
    remaining = int(payload_size)
    all_zero = True
    while remaining > 0:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            return False
        if all_zero and any(chunk):
            all_zero = False
        remaining -= len(chunk)
    return all_zero


def _trace_input_provenance(path: Path) -> str:
    with path.open("rb") as handle:
        raw_header = _read_exact(handle, GLOBAL_HEADER_STRUCT.size)
        if raw_header is None:
            return INPUT_PROVENANCE_REAL_EXTERNAL
        header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(raw_header, 0)
        if int(header_tuple[0]) != TRACE_FORMAT_MAGIC:
            return INPUT_PROVENANCE_REAL_EXTERNAL
        producer_ver = header_tuple[8].decode("utf-8", errors="ignore").rstrip("\0")
        public_rtos_derived = producer_ver.startswith(PUBLIC_RTOS_PRODUCER_PREFIX)
        current_format_ver = int(header_tuple[3])
        if current_format_ver >= 2:
            raw_segment_meta = _read_exact(handle, SEGMENT_META_STRUCT.size)
            if raw_segment_meta is None:
                return INPUT_PROVENANCE_REAL_EXTERNAL
            segment_meta_tuple = SEGMENT_META_STRUCT.unpack_from(raw_segment_meta, 0)
            if int(segment_meta_tuple[0]) != TRACE_SEGMENT_META_MAGIC:
                return INPUT_PROVENANCE_REAL_EXTERNAL

        synthetic_padding_detected = False
        while True:
            prefix = handle.read(4)
            if not prefix:
                break
            if len(prefix) != 4:
                return INPUT_PROVENANCE_REAL_EXTERNAL
            next_magic = int.from_bytes(prefix, "little")
            if next_magic == TRACE_FORMAT_MAGIC:
                raw_header_tail = _read_exact(handle, GLOBAL_HEADER_STRUCT.size - 4)
                if raw_header_tail is None:
                    return INPUT_PROVENANCE_REAL_EXTERNAL
                header_tuple = GLOBAL_HEADER_STRUCT.unpack_from(prefix + raw_header_tail, 0)
                current_format_ver = int(header_tuple[3])
                if current_format_ver >= 2:
                    raw_segment_meta = _read_exact(handle, SEGMENT_META_STRUCT.size)
                    if raw_segment_meta is None:
                        return INPUT_PROVENANCE_REAL_EXTERNAL
                    segment_meta_tuple = SEGMENT_META_STRUCT.unpack_from(raw_segment_meta, 0)
                    if int(segment_meta_tuple[0]) != TRACE_SEGMENT_META_MAGIC:
                        return INPUT_PROVENANCE_REAL_EXTERNAL
                continue

            raw_chunk_tail = _read_exact(handle, CHUNK_HEADER_STRUCT.size - 4)
            if raw_chunk_tail is None:
                return INPUT_PROVENANCE_REAL_EXTERNAL
            chunk_tuple = CHUNK_HEADER_STRUCT.unpack_from(prefix + raw_chunk_tail, 0)
            if int(chunk_tuple[0]) != TRACE_CHUNK_MAGIC:
                return INPUT_PROVENANCE_REAL_EXTERNAL

            payload_size = int(chunk_tuple[4])
            looks_like_synthetic_padding = (
                int(chunk_tuple[3]) == 0
                and int(chunk_tuple[5]) == int(chunk_tuple[6])
                and int(chunk_tuple[7]) == 0
                and int(chunk_tuple[8]) == 0
                and int(chunk_tuple[10]) == _crc32_zeroes(payload_size)
            )
            if looks_like_synthetic_padding:
                if _chunk_payload_is_all_zero(handle, payload_size):
                    synthetic_padding_detected = True
                    continue
                return INPUT_PROVENANCE_REAL_EXTERNAL

            handle.seek(payload_size, 1)
    if synthetic_padding_detected and public_rtos_derived:
        return INPUT_PROVENANCE_PUBLIC_RTOS_SEEDED_PADDED
    if synthetic_padding_detected:
        return INPUT_PROVENANCE_SYNTHETIC_PADDED
    if public_rtos_derived:
        return INPUT_PROVENANCE_PUBLIC_RTOS_DERIVED
    return INPUT_PROVENANCE_REAL_EXTERNAL


def _environment_summary() -> dict[str, object]:
    return {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def _linux_os_peak_memory() -> dict[str, object]:
    status_path = Path("/proc/self/status")
    if not status_path.exists():
        return {
            "supported": False,
            "platform": "linux",
            "reason": "proc_status_unavailable",
        }

    metrics: dict[str, int] = {}
    for line in status_path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if key not in {"VmHWM", "VmPeak"}:
            continue
        parts = value.split()
        if not parts:
            continue
        try:
            amount = int(parts[0])
        except ValueError:
            continue
        unit = parts[1].lower() if len(parts) > 1 else "kb"
        if unit == "kb":
            amount *= 1024
        metrics[key] = amount

    peak_rss_bytes = metrics.get("VmHWM")
    peak_vm_bytes = metrics.get("VmPeak")
    if peak_rss_bytes is None and peak_vm_bytes is None:
        return {
            "supported": False,
            "platform": "linux",
            "reason": "vm_peak_metrics_missing",
        }

    payload: dict[str, object] = {
        "supported": True,
        "platform": "linux",
        "source": "/proc/self/status",
    }
    if peak_rss_bytes is not None:
        payload["peak_rss_bytes"] = peak_rss_bytes
        payload["peak_rss_mb"] = round(peak_rss_bytes / (1024 * 1024), 3)
    if peak_vm_bytes is not None:
        payload["peak_vm_bytes"] = peak_vm_bytes
        payload["peak_vm_mb"] = round(peak_vm_bytes / (1024 * 1024), 3)
    return payload


def _windows_os_peak_memory() -> dict[str, object]:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return {
            "supported": False,
            "platform": "windows",
            "reason": "ctypes_unavailable",
        }

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    current_process = kernel32.GetCurrentProcess()
    ok = psapi.GetProcessMemoryInfo(
        current_process,
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        return {
            "supported": False,
            "platform": "windows",
            "reason": "GetProcessMemoryInfo_failed",
        }

    return {
        "supported": True,
        "platform": "windows",
        "source": "GetProcessMemoryInfo",
        "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
        "peak_working_set_mb": round(float(counters.PeakWorkingSetSize) / (1024 * 1024), 3),
        "peak_pagefile_bytes": int(counters.PeakPagefileUsage),
        "peak_pagefile_mb": round(float(counters.PeakPagefileUsage) / (1024 * 1024), 3),
    }


def _os_peak_memory() -> dict[str, object]:
    system = platform.system().strip().lower()
    if system.startswith("linux"):
        return _linux_os_peak_memory()
    if system.startswith("win"):
        return _windows_os_peak_memory()
    return {
        "supported": False,
        "platform": system or "unknown",
        "reason": "platform_not_supported",
    }


def _formal_peak_memory_observation(memory_report: dict[str, object]) -> tuple[float, str]:
    os_peak = memory_report.get("os_peak")
    if isinstance(os_peak, dict) and os_peak.get("supported"):
        peak_rss = os_peak.get("peak_rss_mb")
        if isinstance(peak_rss, (int, float)):
            return float(peak_rss), "linux_peak_rss_mb"
        peak_working_set = os_peak.get("peak_working_set_mb")
        if isinstance(peak_working_set, (int, float)):
            return float(peak_working_set), "windows_peak_working_set_mb"
    python_peak = memory_report.get("python_peak_alloc_mb")
    if isinstance(python_peak, (int, float)):
        return float(python_peak), "python_peak_alloc_mb_fallback"
    return 0.0, "unknown"


def _metric_scalar(metric_row: dict[str, object]) -> float:
    summary = metric_row["summary"]
    if "avg_utilization" in summary:
        return float(summary["avg_utilization"])
    if "count" in summary:
        return float(summary["count"])
    if "total_blocked_time" in summary:
        return float(summary["total_blocked_time"])
    if "total_ready_wait_time" in summary:
        return float(summary["total_ready_wait_time"])
    if "avg_response_time" in summary:
        return float(summary["avg_response_time"])
    if "max_jitter" in summary:
        return float(summary["max_jitter"])
    if "irq_count" in summary:
        return float(summary["irq_count"])
    if "total_irq_latency" in summary:
        return float(summary["total_irq_latency"])
    return 0.0


def _summary_metric_rows(summary: dict[str, object]) -> list[dict[str, object]]:
    return list(summary.get("metric_changes") or summary.get("metrics") or [])


def _bundle_window(controller: WorkspaceController, dataset_id: str) -> tuple[float, float]:
    bundle = controller.repository.get(dataset_id).artifact.bundle
    if bundle.event_stream:
        return (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned)
    index_bundle = bundle.index_bundle
    if index_bundle is not None:
        origin = float(index_bundle.summary.get("time_origin", 0.0))
        end = float(index_bundle.summary.get("time_end", 0.0))
        if end > origin:
            return (origin, end)
    starts: list[float] = []
    ends: list[float] = []
    for item in bundle.task_states:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    for item in bundle.exec_slices:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    for item in bundle.irq_spans:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    for item in bundle.untrusted_windows:
        starts.append(float(item.t_begin))
        ends.append(float(item.t_end))
    if not starts or not ends:
        return (0.0, 0.0)
    return (min(starts), max(ends))


def _profile_value(profile: str, explicit: int | float | None, key: str) -> int | float:
    if explicit is not None:
        return explicit
    return PROFILE_DEFAULTS[profile][key]


def _safe_path_size(path: Path) -> int | None:
    try:
        if path.exists() and path.is_file():
            return int(path.stat().st_size)
    except OSError:
        return None
    return None


def _derive_blocker_output_path(output_path: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    stem = output_path.stem or "acceptance"
    return output_path.with_name(f"{stem}_blocker.json")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)


def _round_optional(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None


def _round_duration(value: float) -> float:
    return round(float(value), 6)


def _rounded_duration_total(*values: float) -> float:
    return _round_duration(sum(_round_duration(value) for value in values))


def _default_load_breakdown() -> dict[str, object]:
    return {
        "current_stage": None,
        "last_completed_stage": None,
        "completed_stages": [],
        "stage_timings": {},
        "memory_snapshots": [],
        "latest_progress": {},
        "progress_samples": [],
        "hotspot_summary": {},
    }


def _requested_memory_guard_placeholder(requested_limit_mb: int | None) -> dict[str, object]:
    if requested_limit_mb is None or requested_limit_mb <= 0:
        return {
            "enabled": False,
            "requested_limit_mb": None,
            "limit_mb": None,
            "strategy": "subprocess_worker",
            "reason": "not_requested",
        }
    return {
        "enabled": False,
        "requested_limit_mb": int(requested_limit_mb),
        "limit_mb": None,
        "strategy": "subprocess_worker",
        "reason": "pending_worker_application",
    }


def _configure_memory_guard(
    *,
    mode: str,
    acceptance_scope: str,
    requested_limit_mb: int | None,
) -> dict[str, object]:
    guard: dict[str, object] = {
        "enabled": False,
        "requested_limit_mb": int(requested_limit_mb) if requested_limit_mb is not None else None,
        "limit_mb": None,
        "strategy": None,
        "reason": "not_requested",
    }
    if requested_limit_mb is None or requested_limit_mb <= 0:
        return guard
    if not (mode == "desktop_perf_acceptance" and acceptance_scope == "perf_only"):
        guard["reason"] = "scope_not_supported"
        return guard
    if _platform_label() != "linux":
        guard["reason"] = "platform_not_supported"
        return guard
    try:
        import resource
    except ImportError:
        guard["reason"] = "resource_module_unavailable"
        return guard

    requested_limit_bytes = int(requested_limit_mb) * 1024 * 1024
    try:
        previous_soft, previous_hard = resource.getrlimit(resource.RLIMIT_AS)
        hard_unlimited = previous_hard in (-1, resource.RLIM_INFINITY)
        effective_limit_bytes = (
            requested_limit_bytes
            if hard_unlimited
            else min(requested_limit_bytes, int(previous_hard))
        )
        if effective_limit_bytes <= 0:
            guard["reason"] = "effective_limit_invalid"
            return guard
        resource.setrlimit(resource.RLIMIT_AS, (effective_limit_bytes, previous_hard))
    except (OSError, ValueError) as exc:
        guard["reason"] = "setrlimit_failed"
        guard["error"] = f"{type(exc).__name__}: {exc}"
        return guard

    def _to_mb(limit: int) -> float | None:
        if limit in (-1, resource.RLIM_INFINITY):
            return None
        return round(float(limit) / (1024 * 1024), 3)

    guard.update(
        {
            "enabled": True,
            "limit_mb": round(float(effective_limit_bytes) / (1024 * 1024), 3),
            "strategy": "rlimit_as",
            "reason": "applied",
            "clamped_to_hard_limit": (not hard_unlimited and effective_limit_bytes < requested_limit_bytes),
            "previous_soft_limit_mb": _to_mb(int(previous_soft)),
            "hard_limit_mb": _to_mb(int(previous_hard)),
        }
    )
    return guard


def _snapshot_blocker_state(blocker_state: dict[str, object]) -> dict[str, object]:
    return {
        "current_stage": blocker_state.get("current_stage"),
        "last_completed_stage": blocker_state.get("last_completed_stage"),
        "export_contract_version": blocker_state.get("export_contract_version", EXPORT_CONTRACT_VERSION),
        "timings": json.loads(json.dumps(blocker_state.get("timings") or {}, ensure_ascii=False)),
        "load_breakdown": json.loads(json.dumps(blocker_state.get("load_breakdown") or {}, ensure_ascii=False)),
        "export_probe": json.loads(json.dumps(blocker_state.get("export_probe") or {}, ensure_ascii=False)),
        "memory_guard": json.loads(json.dumps(blocker_state.get("memory_guard") or {}, ensure_ascii=False)),
    }


def _merge_blocker_state(blocker_state: dict[str, object], payload: dict[str, object] | None) -> None:
    if not isinstance(payload, dict):
        return
    current_stage = payload.get("current_stage")
    if current_stage is not None:
        blocker_state["current_stage"] = str(current_stage)
    blocker_state["last_completed_stage"] = payload.get("last_completed_stage")
    export_contract_version = payload.get("export_contract_version")
    if export_contract_version is not None:
        blocker_state["export_contract_version"] = str(export_contract_version)
    for key in ("timings", "load_breakdown", "export_probe", "memory_guard"):
        value = payload.get(key)
        if isinstance(value, dict):
            blocker_state[key] = json.loads(json.dumps(value, ensure_ascii=False))
    error = payload.get("error")
    if isinstance(error, dict):
        blocker_state["captured_error"] = json.loads(json.dumps(error, ensure_ascii=False))
    status = payload.get("status")
    if status is not None:
        blocker_state["worker_status"] = str(status)
    exit_code = payload.get("worker_exit_code")
    if isinstance(exit_code, int):
        blocker_state["worker_exit_code"] = exit_code


def _read_json_if_exists(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl_if_exists(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _update_load_breakdown_from_stage_event(
    *,
    blocker_state: dict[str, object],
    load_breakdown: dict[str, object],
    load_started: float,
    stage: str,
    payload: dict[str, object],
) -> None:
    load_breakdown["current_stage"] = stage
    blocker_state["timings"]["load_elapsed_seconds"] = _round_optional(time.perf_counter() - load_started)
    stage_timings = payload.get("stage_timings")
    if isinstance(stage_timings, dict):
        normalized_timings: dict[str, float] = {}
        for key, value in stage_timings.items():
            if isinstance(value, (int, float)):
                normalized_value = round(float(value), 6)
                normalized_timings[str(key)] = normalized_value
                blocker_state["timings"][str(key)] = _round_optional(normalized_value)
        load_breakdown["stage_timings"] = normalized_timings
    memory_snapshot = payload.get("memory_snapshot")
    if isinstance(memory_snapshot, dict):
        snapshots = list(load_breakdown.get("memory_snapshots") or [])
        snapshots.append(dict(memory_snapshot))
        if len(snapshots) > 64:
            snapshots = snapshots[-64:]
        load_breakdown["memory_snapshots"] = snapshots
    chunk_progress = payload.get("chunk_progress")
    if isinstance(chunk_progress, dict):
        progress_payload = {
            "stage": stage,
            "status": str(payload.get("status")),
            **dict(chunk_progress),
        }
        load_breakdown["latest_progress"] = progress_payload
        progress_samples = list(load_breakdown.get("progress_samples") or [])
        progress_samples.append(progress_payload)
        if len(progress_samples) > 32:
            progress_samples = progress_samples[-32:]
        load_breakdown["progress_samples"] = progress_samples
    hotspot_summary = payload.get("hotspot_summary")
    if isinstance(hotspot_summary, dict):
        load_breakdown["hotspot_summary"] = dict(hotspot_summary)
    if payload.get("status") == "completed":
        completed = list(load_breakdown.get("completed_stages") or [])
        if stage not in completed:
            completed.append(stage)
        load_breakdown["completed_stages"] = completed
        load_breakdown["last_completed_stage"] = stage


def _merge_parser_progress_jsonl(
    progress_path: Path,
    *,
    blocker_state: dict[str, object],
    load_breakdown: dict[str, object],
    load_started: float,
) -> None:
    for row in _read_jsonl_if_exists(progress_path):
        stage = str(row.get("stage") or "")
        payload = row.get("payload")
        if not stage or not isinstance(payload, dict):
            continue
        _update_load_breakdown_from_stage_event(
            blocker_state=blocker_state,
            load_breakdown=load_breakdown,
            load_started=load_started,
            stage=stage,
            payload=dict(payload),
        )


def _audit_formal_inputs(
    baseline_path: Path,
    candidate_path: Path,
) -> dict[str, object]:
    audit_started = time.perf_counter()
    baseline_prescan = prs_Prescan(baseline_path, include_task_state_preview=False)
    if not baseline_prescan.ok:
        raise RuntimeError(baseline_prescan.message)
    candidate_prescan = prs_Prescan(candidate_path, include_task_state_preview=False)
    if not candidate_prescan.ok:
        raise RuntimeError(candidate_prescan.message)
    baseline_sha256 = _sha256(baseline_path)
    candidate_sha256 = _sha256(candidate_path)
    baseline_input_provenance = _trace_input_provenance(baseline_path)
    candidate_input_provenance = _trace_input_provenance(candidate_path)
    audit_seconds = time.perf_counter() - audit_started

    return {
        "audit_seconds": round(audit_seconds, 6),
        "baseline_event_count": int(baseline_prescan.data.get("record_count", 0)),
        "candidate_event_count": int(candidate_prescan.data.get("record_count", 0)),
        "baseline_parse_ok": True,
        "candidate_parse_ok": True,
        "baseline_sha256": baseline_sha256,
        "candidate_sha256": candidate_sha256,
        "baseline_input_provenance": baseline_input_provenance,
        "candidate_input_provenance": candidate_input_provenance,
    }


def _capture_perf_only_desktop_perf(
    trace_path: Path,
    *,
    profile: str,
    cache_budget_mb: float,
    load_timeout_s: float,
    blocker_state: dict[str, object],
    state_persistor: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    controller = WorkspaceController(cache_budget_mb=cache_budget_mb)
    if tracemalloc.is_tracing():
        tracemalloc.reset_peak()
    load_breakdown: dict[str, object] = _default_load_breakdown()
    load_breakdown["index_build_mode"] = PERF_ONLY_INDEX_BUILD_MODE
    run_root = Path(str(blocker_state.get("run_root") or trace_path.parent))
    run_root.mkdir(parents=True, exist_ok=True)
    parser_progress_path = run_root / "perf_only_parser_progress.jsonl"
    load_breakdown["progress_path"] = str(parser_progress_path)
    blocker_state["load_breakdown"] = load_breakdown
    blocker_state["export_contract_version"] = EXPORT_CONTRACT_VERSION

    def _persist_state() -> None:
        if state_persistor is not None:
            state_persistor(_snapshot_blocker_state(blocker_state))

    _persist_state()

    preview_started = time.perf_counter()
    prescan = prs_Prescan(trace_path, include_task_state_preview=False)
    preview_seconds = time.perf_counter() - preview_started
    if not prescan.ok:
        raise RuntimeError(prescan.message)
    prescan_time_window = prescan.data.get("time_window")
    if not isinstance(prescan_time_window, (list, tuple)) or len(prescan_time_window) != 2:
        raise RuntimeError("prescan missing time_window")
    time_window = (float(prescan_time_window[0]), float(prescan_time_window[1]))
    blocker_state["current_stage"] = "preview"
    blocker_state["last_completed_stage"] = "preview"
    blocker_state["timings"]["preview_seconds"] = _round_optional(preview_seconds)
    _persist_state()

    load_started = time.perf_counter()
    blocker_state["current_stage"] = "load"
    blocker_state["load_started_at"] = load_started
    blocker_state["timings"]["load_elapsed_seconds"] = 0.0
    _persist_state()

    parser_agent = ParserProcessAgent(job_id=f"perf-only-load-{int(time.time() * 1000)}")
    try:
        loaded = parser_agent.parse_rebuild(
            trace_path,
            artifact_policy={
                "artifact_dir": str(run_root / "parser_process"),
                "materialize_event_stream": False,
                "index_build_mode": PERF_ONLY_INDEX_BUILD_MODE,
                "load_artifact": True,
                "retain_artifact": False,
                "progress_path": str(parser_progress_path),
                "timeout_s": float(load_timeout_s),
            },
        )
    finally:
        _merge_parser_progress_jsonl(
            parser_progress_path,
            blocker_state=blocker_state,
            load_breakdown=load_breakdown,
            load_started=load_started,
        )
        _persist_state()
    parser_process_artifact = dict((loaded.data or {}).get("parser_process_artifact") or {})
    if parser_process_artifact:
        load_breakdown["parser_process_artifact"] = parser_process_artifact
        load_breakdown["parser_process_artifact_version"] = parser_process_artifact.get("artifact_version")
    _persist_state()
    if not loaded.ok:
        blocker_state["timings"]["load_elapsed_seconds"] = _round_optional(time.perf_counter() - load_started)
        _persist_state()
        if loaded.code == "ERR-AGENT_TIMEOUT":
            raise TimeoutError(f"load stage exceeded {load_timeout_s:.1f}s")
        raise RuntimeError(loaded.message)
    blocker_state["current_stage"] = "load"
    blocker_state["last_completed_stage"] = "load"

    metric_started = time.perf_counter()
    registered = controller._register_artifact(loaded.data["artifact"])
    metric_seconds = time.perf_counter() - metric_started
    if not registered.ok:
        raise RuntimeError(registered.message)
    dataset_id = registered.data
    controller.active_dataset_id = dataset_id
    context = controller.viz_SetContext(
        {
            "time_window": time_window,
            "filter": {},
            "selection": {"task_id": 1},
            "zoom_level": 1.5,
            "focused_view": "timeline",
            "dataset_role": "single",
        }
    )
    if not context.ok:
        raise RuntimeError(context.message)
    load_seconds = time.perf_counter() - load_started
    load_stage_timings = dict(loaded.data.get("load_stage_timings") or {})
    blocker_state["timings"]["load_seconds"] = _round_optional(load_seconds)
    blocker_state["timings"]["load_elapsed_seconds"] = _round_optional(load_seconds)
    blocker_state["timings"]["rebuild_seconds"] = _round_optional(float(loaded.data["rebuild_seconds"]))
    blocker_state["timings"]["pipeline_rebuild_seconds"] = _round_optional(
        float(loaded.data.get("pipeline_rebuild_seconds") or 0.0)
    )
    blocker_state["timings"]["pipeline_load_seconds"] = _round_optional(float(load_stage_timings.get("load_seconds", 0.0)))
    blocker_state["timings"]["metric_seconds"] = _round_optional(metric_seconds)
    _persist_state()

    event_started = time.perf_counter()
    event_page = controller.viz_QueryEventTable(
        EventTableQuery(
            filter={"dataset_id": dataset_id},
            limit=64,
        )
    )
    event_table_first_page_seconds = time.perf_counter() - event_started
    if not event_page.ok:
        raise RuntimeError(event_page.message)
    blocker_state["current_stage"] = "event_query"
    blocker_state["last_completed_stage"] = "event_query"
    _persist_state()

    lod_started = time.perf_counter()
    timeline_lod2 = controller.viz_QueryTimelineLOD(
        {
            "dataset_id": dataset_id,
            "time_window": time_window,
            "filter": {},
            "lod": 2,
            "event_limit": 64,
        }
    )
    lod2_first_window_seconds = time.perf_counter() - lod_started
    if not timeline_lod2.ok:
        raise RuntimeError(timeline_lod2.message)
    blocker_state["current_stage"] = "lod2_query"
    blocker_state["last_completed_stage"] = "lod2_query"
    blocker_state["current_stage"] = "query_ready"
    _persist_state()

    _, first_screen_peak_memory_bytes = tracemalloc.get_traced_memory()
    def _update_export_probe(**updates: object) -> None:
        probe = dict(blocker_state.get("export_probe") or {})
        probe.update(updates)
        blocker_state["export_probe"] = probe

    def _on_export_progress(payload: dict[str, object]) -> None:
        _update_export_probe(**json.loads(json.dumps(payload, ensure_ascii=False)))
        _persist_state()

    export = ExportService(
        controller.repository,
        controller.context_store,
        controller.jobs,
        on_progress=_on_export_progress,
    )
    export_duration_payload = _empty_export_duration_payload()
    export_probe: dict[str, object] = {
        "skipped": False,
        "reason": None,
    }
    normalized_manifest_entries = 0

    def _apply_export_timings(timing_payload: dict[str, float | None]) -> None:
        for key, value in timing_payload.items():
            blocker_state["timings"][key] = _round_optional(value)

    def _run_export_sidecar(mode: str, output_dir: Path) -> tuple[dict[str, float | None], dict[str, object], dict[str, object]]:
        total_field, component_prefix = _export_duration_field_names(mode)
        if mode == "full":
            job = export.export_Full(
                {
                    "dataset_id": dataset_id,
                    "run_batch_id": "perf-only-batch",
                    "version_id": "perf-only-export",
                    "experiment_params": {"profile": profile, "mode": mode, "timed_path": "perf_only"},
                }
            )
            write_stage = "export_write"
            normalize_stage = "export_normalize_sidecar"
        else:
            mid = (time_window[0] + time_window[1]) / 2.0
            clipped_window = (max(time_window[0], mid - 1000.0), min(time_window[1], mid + 1000.0))
            job = export.export_Clipped(
                {
                    "dataset_id": dataset_id,
                    "time_window": clipped_window,
                    "filter": {"task_id": 1},
                    "selection": {"task_id": 1},
                    "focused_view": "timeline",
                    "zoom_level": 2.0,
                    "run_batch_id": "perf-only-batch",
                    "version_id": "perf-only-export",
                    "experiment_params": {"profile": profile, "mode": mode, "timed_path": "perf_only"},
                }
            )
            write_stage = "export_clipped_write"
            normalize_stage = "export_clipped_normalize_sidecar"
        if not job.ok:
            raise RuntimeError(job.message)

        blocker_state["current_stage"] = write_stage
        _update_export_probe(
            skipped=False,
            mode=mode,
            stage=write_stage,
            substage=None,
            status="started",
        )
        _persist_state()

        write_started = time.perf_counter()
        written = export.export_WritePackage(job.data["job_id"], str(output_dir))
        write_seconds = time.perf_counter() - write_started
        if not written.ok:
            raise RuntimeError(written.message)
        blocker_state["last_completed_stage"] = write_stage
        _update_export_probe(
            skipped=False,
            mode=mode,
            stage=write_stage,
            status="completed",
            write_mode=written.data.get("write_mode") if isinstance(written.data, dict) else None,
        )

        timing_payload: dict[str, float | None] = {
            total_field: None,
            f"{component_prefix}_write_seconds": _round_duration(write_seconds),
            f"{component_prefix}_normalize_seconds": None,
        }
        write_breakdown = written.data.get("write_timings") if isinstance(written.data, dict) else None
        if isinstance(write_breakdown, dict):
            for key in EXPORT_WRITE_TIMING_KEYS:
                if key in {"write_seconds", "normalize_seconds"}:
                    continue
                value = write_breakdown.get(key)
                if isinstance(value, (int, float)):
                    timing_payload[f"{component_prefix}_{key}"] = _round_duration(float(value))
        for key in EXPORT_WRITE_TIMING_KEYS:
            timing_payload.setdefault(f"{component_prefix}_{key}", None)
        _apply_export_timings(timing_payload)
        _persist_state()

        blocker_state["current_stage"] = normalize_stage
        _update_export_probe(
            skipped=False,
            mode=mode,
            stage=normalize_stage,
            substage=None,
            status="started",
            write_mode=written.data.get("write_mode") if isinstance(written.data, dict) else None,
        )
        _persist_state()
        normalize_started = time.perf_counter()
        normalized = export.export_NormalizePackagePerfOnly(
            str(output_dir),
            meta=written.data.get("meta") if isinstance(written.data, dict) else None,
            manifest=written.data.get("manifest") if isinstance(written.data, dict) else None,
        )
        normalize_seconds = time.perf_counter() - normalize_started
        if not normalized.ok:
            raise RuntimeError(normalized.message)
        blocker_state["last_completed_stage"] = normalize_stage
        _update_export_probe(
            skipped=False,
            mode=mode,
            stage=normalize_stage,
            status="completed",
            write_mode=written.data.get("write_mode") if isinstance(written.data, dict) else None,
        )
        timing_payload[total_field] = _rounded_duration_total(write_seconds, normalize_seconds)
        timing_payload[f"{component_prefix}_normalize_seconds"] = _round_duration(normalize_seconds)
        _apply_export_timings(timing_payload)
        _persist_state()
        return timing_payload, normalized.data, written.data if isinstance(written.data, dict) else {}

    export_full_timing_payload, normalized_full, written_full = _run_export_sidecar(
        "full",
        run_root / "perf-only-package-full",
    )
    export_duration_payload.update(export_full_timing_payload)
    normalized_manifest_entries = len(normalized_full.get("manifest", {}).get("entries") or [])
    export_probe["full_write_mode"] = written_full.get("write_mode")

    export_clipped_timing_payload, normalized_clipped, written_clipped = _run_export_sidecar(
        "clipped",
        run_root / "perf-only-package-clipped",
    )
    export_duration_payload.update(export_clipped_timing_payload)
    export_probe["clipped_write_mode"] = written_clipped.get("write_mode")
    export_probe["normalized_clipped_manifest_entries"] = len(normalized_clipped.get("manifest", {}).get("entries") or [])
    blocker_state["current_stage"] = "export_ready"
    blocker_state["export_probe"] = dict(export_probe)
    _persist_state()

    cache_stats = controller.repository.query_cache.stats(dataset_id)
    _, peak_memory_bytes = tracemalloc.get_traced_memory()
    export_full_seconds = float(export_duration_payload["export_full_seconds"] or 0.0)
    export_clipped_seconds = float(export_duration_payload["export_clipped_seconds"] or 0.0)
    return {
        "profile": profile,
        "export_contract_version": EXPORT_CONTRACT_VERSION,
        "preview_mode": "lightweight",
        "load_preview_seconds": round(preview_seconds, 6),
        "preview_seconds": round(preview_seconds, 6),
        "task_state_preview_seconds": 0.0,
        "load_seconds": round(load_seconds, 6),
        "pipeline_load_seconds": round(float(load_stage_timings.get("load_seconds", 0.0)), 6),
        "rebuild_seconds": round(float(loaded.data["rebuild_seconds"]), 6),
        "pipeline_rebuild_seconds": round(float(loaded.data.get("pipeline_rebuild_seconds") or 0.0), 6),
        "load_stage_timings": load_stage_timings,
        "load_memory_snapshots": list(loaded.data.get("memory_snapshots") or []),
        "load_hotspot_summary": dict(loaded.data.get("load_hotspot_summary") or {}),
        "metric_seconds": round(metric_seconds, 6),
        "export_full_seconds": round(export_full_seconds, 6),
        "export_clipped_seconds": round(export_clipped_seconds, 6),
        "export_write_seconds": export_duration_payload.get("export_write_seconds"),
        "export_normalize_seconds": export_duration_payload.get("export_normalize_seconds"),
        "export_prepare_seconds": export_duration_payload.get("export_prepare_seconds"),
        "export_encode_trace_seconds": export_duration_payload.get("export_encode_trace_seconds"),
        "export_json_dump_seconds": export_duration_payload.get("export_json_dump_seconds"),
        "export_csv_write_seconds": export_duration_payload.get("export_csv_write_seconds"),
        "export_checksum_seconds": export_duration_payload.get("export_checksum_seconds"),
        "export_reference_copy_seconds": export_duration_payload.get("export_reference_copy_seconds"),
        "export_clipped_write_seconds": export_duration_payload.get("export_clipped_write_seconds"),
        "export_clipped_normalize_seconds": export_duration_payload.get("export_clipped_normalize_seconds"),
        "export_clipped_prepare_seconds": export_duration_payload.get("export_clipped_prepare_seconds"),
        "export_clipped_encode_trace_seconds": export_duration_payload.get("export_clipped_encode_trace_seconds"),
        "export_clipped_json_dump_seconds": export_duration_payload.get("export_clipped_json_dump_seconds"),
        "export_clipped_csv_write_seconds": export_duration_payload.get("export_clipped_csv_write_seconds"),
        "export_clipped_checksum_seconds": export_duration_payload.get("export_clipped_checksum_seconds"),
        "export_clipped_reference_copy_seconds": export_duration_payload.get("export_clipped_reference_copy_seconds"),
        "event_table_first_page_seconds": round(event_table_first_page_seconds, 6),
        "lod2_first_window_seconds": round(lod2_first_window_seconds, 6),
        "event_table_first_page_cache_hit": bool(event_page.data.summary.get("cache_hit")),
        "event_table_first_page_cache_source": event_page.data.summary.get("cache_source"),
        "event_table_first_page_source": str(event_page.data.summary.get("source", "")),
        "lod2_first_window_cache_hit": bool(timeline_lod2.data.summary.get("cache_hit")),
        "lod2_first_window_cache_source": timeline_lod2.data.summary.get("cache_source"),
        "lod2_first_window_source": str(timeline_lod2.data.summary.get("source", "")),
        "export_probe_skipped": False,
        "export_probe_reason": None,
        "export_write_mode": written_full.get("write_mode"),
        "export_clipped_write_mode": written_clipped.get("write_mode"),
        "peak_memory_mb": round(peak_memory_bytes / (1024 * 1024), 3),
        "first_screen_peak_memory_mb": round(first_screen_peak_memory_bytes / (1024 * 1024), 3),
        "cache_eviction_count": int(cache_stats.get("eviction_count", 0)),
        "cache_budget_bytes": int(cache_stats.get("budget_bytes", 0)),
        "cache_entry_count": int(cache_stats.get("entry_count", 0)),
        "cache_total_bytes": int(cache_stats.get("total_bytes", 0)),
        "cache_namespaces": cache_stats.get("namespaces", {}),
        "preview_stage": prescan.data.get("stage"),
        "resolved_stage": "query_ready",
        "task_state_preview_lane_count": 0,
        "task_state_preview_task_count": 0,
        "event_table_first_page_count": len(event_page.data.items),
        "lod2_first_window_count": len(timeline_lod2.data.events),
        "timed_load_role": "baseline",
        "timed_dataset_id": dataset_id,
        "index_build_mode": PERF_ONLY_INDEX_BUILD_MODE,
        "parser_process_artifact": parser_process_artifact,
        "parser_process_artifact_version": parser_process_artifact.get("artifact_version"),
        "load_stage_completed": list(load_breakdown.get("completed_stages") or []),
        "load_latest_progress": dict(load_breakdown.get("latest_progress") or {}),
        "task_state_count": len(loaded.data["artifact"].bundle.task_states),
        "exec_slice_count": len(loaded.data["artifact"].bundle.exec_slices),
        "normalized_manifest_entries": normalized_manifest_entries,
    }


def _perf_only_worker_main(args: argparse.Namespace) -> int:
    progress_path = Path(args.worker_progress_output).expanduser().resolve()
    result_path = Path(args.worker_result_output).expanduser().resolve()
    blocker_state: dict[str, object] = {
        "current_stage": "init",
        "last_completed_stage": "init",
        "export_contract_version": EXPORT_CONTRACT_VERSION,
        "timings": {},
        "load_breakdown": _default_load_breakdown(),
        "export_probe": {},
        "memory_guard": _requested_memory_guard_placeholder(args.worker_memory_limit_mb),
        "run_root": str(result_path.parent),
    }
    running_checkpoint = {
        "at": 0.0,
        "stage": None,
        "export_stage": None,
        "export_substage": None,
        "completed_count": -1,
    }

    def _persist(payload: dict[str, object], *, status: str, error: dict[str, object] | None = None) -> None:
        if status == "running":
            load_breakdown = payload.get("load_breakdown") if isinstance(payload, dict) else {}
            current_stage = load_breakdown.get("current_stage") if isinstance(load_breakdown, dict) else None
            export_stage = payload.get("current_stage") if isinstance(payload, dict) else None
            export_probe = payload.get("export_probe") if isinstance(payload, dict) else {}
            export_substage = export_probe.get("substage") if isinstance(export_probe, dict) else None
            completed = load_breakdown.get("completed_stages") if isinstance(load_breakdown, dict) else []
            hotspot_summary = load_breakdown.get("hotspot_summary") if isinstance(load_breakdown, dict) else {}
            hotspot_failure = bool(isinstance(hotspot_summary, dict) and hotspot_summary.get("failure"))
            now = time.monotonic()
            completed_count = len(completed) if isinstance(completed, list) else 0
            should_flush = hotspot_failure
            should_flush = should_flush or current_stage != running_checkpoint["stage"]
            should_flush = should_flush or export_stage != running_checkpoint["export_stage"]
            should_flush = should_flush or export_substage != running_checkpoint["export_substage"]
            should_flush = should_flush or completed_count != running_checkpoint["completed_count"]
            should_flush = should_flush or (now - float(running_checkpoint["at"])) >= 1.0
            if not should_flush:
                return
            running_checkpoint["at"] = now
            running_checkpoint["stage"] = current_stage
            running_checkpoint["export_stage"] = export_stage
            running_checkpoint["export_substage"] = export_substage
            running_checkpoint["completed_count"] = completed_count
        rendered = {
            "status": status,
            **payload,
        }
        if error is not None:
            rendered["error"] = error
        _write_json_atomic(progress_path, rendered)

    blocker_state["memory_guard"] = _configure_memory_guard(
        mode="desktop_perf_acceptance",
        acceptance_scope="perf_only",
        requested_limit_mb=args.worker_memory_limit_mb,
    )
    _persist(_snapshot_blocker_state(blocker_state), status="starting")

    try:
        tracemalloc.start()
        desktop_perf = _capture_perf_only_desktop_perf(
            Path(args.worker_trace_input).expanduser().resolve(),
            profile=args.worker_profile,
            cache_budget_mb=float(args.worker_cache_budget_mb),
            load_timeout_s=float(args.worker_load_timeout_s),
            blocker_state=blocker_state,
            state_persistor=lambda payload: _persist(payload, status="running"),
        )
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_memory_mb = round(peak_memory_bytes / (1024 * 1024), 3)
        memory_report = {
            "python_peak_alloc_bytes": int(peak_memory_bytes),
            "python_peak_alloc_mb": peak_memory_mb,
            "peak_memory_bytes": int(peak_memory_bytes),
            "peak_memory_mb": peak_memory_mb,
            "os_peak": _os_peak_memory(),
        }
        payload = {
            "status": "completed",
            "desktop_perf": desktop_perf,
            "memory_report": memory_report,
            "blocker_state": _snapshot_blocker_state(blocker_state),
        }
        _write_json_atomic(result_path, payload)
        _persist(payload["blocker_state"], status="completed")
        return 0
    except Exception as exc:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        blocker_state["captured_error"] = error
        payload = {
            "status": "failed",
            "error": error,
            "blocker_state": _snapshot_blocker_state(blocker_state),
        }
        _write_json_atomic(result_path, payload)
        _persist(payload["blocker_state"], status="failed", error=error)
        return 1


def _capture_perf_only_desktop_perf_isolated(
    trace_path: Path,
    *,
    profile: str,
    cache_budget_mb: float,
    load_timeout_s: float,
    requested_memory_limit_mb: int,
    blocker_state: dict[str, object],
    run_root: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    progress_path = run_root / "perf_only_worker_progress.json"
    result_path = run_root / "perf_only_worker_result.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--internal-perf-only-worker",
        "--worker-trace-input",
        str(trace_path),
        "--worker-profile",
        profile,
        "--worker-cache-budget-mb",
        str(cache_budget_mb),
        "--worker-load-timeout-s",
        str(load_timeout_s),
        "--worker-memory-limit-mb",
        str(requested_memory_limit_mb),
        "--worker-progress-output",
        str(progress_path),
        "--worker-result-output",
        str(result_path),
    ]
    worker = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    supervisory_timeout_s = max(float(load_timeout_s) + 30.0, float(load_timeout_s) * 1.2)
    deadline = time.monotonic() + supervisory_timeout_s

    while True:
        progress_payload = _read_json_if_exists(progress_path)
        _merge_blocker_state(blocker_state, progress_payload)
        if worker.poll() is not None:
            break
        if time.monotonic() >= deadline:
            worker.kill()
            stdout, stderr = worker.communicate(timeout=5.0)
            _merge_blocker_state(blocker_state, _read_json_if_exists(progress_path))
            blocker_state["captured_error"] = {
                "type": "TimeoutError",
                "message": f"perf_only worker exceeded {supervisory_timeout_s:.1f}s supervisory timeout",
                "traceback": stderr or stdout,
            }
            raise TimeoutError(f"perf_only worker exceeded supervisory timeout for {trace_path}") from None
        time.sleep(0.05)

    stdout, stderr = worker.communicate()
    result_payload = _read_json_if_exists(result_path)
    _merge_blocker_state(blocker_state, _read_json_if_exists(progress_path))
    if isinstance(result_payload, dict):
        _merge_blocker_state(blocker_state, result_payload.get("blocker_state"))

    if worker.returncode != 0:
        if isinstance(result_payload, dict) and isinstance(result_payload.get("error"), dict):
            blocker_state["captured_error"] = json.loads(json.dumps(result_payload["error"], ensure_ascii=False))
        elif "captured_error" not in blocker_state:
            blocker_state["captured_error"] = {
                "type": "SubprocessError",
                "message": f"perf_only worker exited with code {worker.returncode}",
                "traceback": stderr or stdout,
            }
        blocker_state["worker_exit_code"] = int(worker.returncode)
        raise RuntimeError(f"perf_only worker failed for {trace_path}") from None

    if not isinstance(result_payload, dict):
        blocker_state["captured_error"] = {
            "type": "RuntimeError",
            "message": "perf_only worker completed without a result payload",
            "traceback": stderr or stdout,
        }
        raise RuntimeError("perf_only worker completed without a result payload") from None

    desktop_perf = result_payload.get("desktop_perf")
    memory_report = result_payload.get("memory_report")
    blocker_snapshot = result_payload.get("blocker_state")
    if not isinstance(desktop_perf, dict) or not isinstance(memory_report, dict) or not isinstance(blocker_snapshot, dict):
        blocker_state["captured_error"] = {
            "type": "RuntimeError",
            "message": "perf_only worker returned an incomplete payload",
            "traceback": stderr or stdout,
        }
        raise RuntimeError("perf_only worker returned an incomplete payload") from None

    return (
        json.loads(json.dumps(desktop_perf, ensure_ascii=False)),
        json.loads(json.dumps(memory_report, ensure_ascii=False)),
        json.loads(json.dumps(blocker_snapshot.get("memory_guard") or {}, ensure_ascii=False)),
    )


def _build_blocker_artifact(
    *,
    mode: str,
    acceptance_scope: str,
    baseline_path: Path,
    candidate_path: Path,
    workdir: str | None,
    blocker_state: dict[str, object],
    exc: Exception,
) -> dict[str, object]:
    scratch_path = Path(workdir).resolve() if workdir else None
    scratch_mount = _linux_mount_info(scratch_path) if scratch_path is not None else {
        "mount_point": None,
        "fs_type": None,
        "local_disk_hint": None,
    }
    status = "timeout" if isinstance(exc, TimeoutError) else "failed"
    timings = dict(blocker_state.get("timings") or {})
    load_started_at = blocker_state.get("load_started_at")
    if "load_elapsed_seconds" not in timings and isinstance(load_started_at, (int, float)):
        timings["load_elapsed_seconds"] = _round_optional(time.perf_counter() - float(load_started_at))
    captured_error = blocker_state.get("captured_error")
    if isinstance(captured_error, dict):
        error = dict(captured_error)
    else:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "acceptance_scope": acceptance_scope,
        "status": status,
        "export_contract_version": str(blocker_state.get("export_contract_version") or EXPORT_CONTRACT_VERSION),
        "blocked_stage": blocker_state.get("current_stage") or blocker_state.get("last_completed_stage"),
        "last_completed_stage": blocker_state.get("last_completed_stage"),
        "timings": timings,
        "load_breakdown": json.loads(json.dumps(blocker_state.get("load_breakdown") or {}, ensure_ascii=False)),
        "export_probe": dict(blocker_state.get("export_probe") or {}),
        "memory_guard": dict(blocker_state.get("memory_guard") or {}),
        "inputs": {
            "baseline_trace": str(baseline_path),
            "candidate_trace": str(candidate_path),
            "baseline_size_bytes": _safe_path_size(baseline_path),
            "candidate_size_bytes": _safe_path_size(candidate_path),
        },
        "scratch": {
            "workdir": str(scratch_path) if scratch_path is not None else None,
            "mount_point": scratch_mount.get("mount_point"),
            "fs_type": scratch_mount.get("fs_type"),
            "local_disk_hint": scratch_mount.get("local_disk_hint"),
        },
        "error": error,
    }


def _wait_for_job_completion(
    controller: WorkspaceController,
    job_id: str,
    *,
    started_at: float,
    preview_seconds: float | None,
    timeout_s: float = 30.0,
) -> tuple[dict[str, object], float]:
    deadline = time.monotonic() + timeout_s
    task_state_preview_seconds: float | None = preview_seconds
    while time.monotonic() < deadline:
        snapshot = controller.jobs.status(job_id)
        if not snapshot.ok:
            raise RuntimeError(snapshot.message)
        payload = snapshot.data.get("payload", {})
        preview = payload.get("preview") or {}
        if task_state_preview_seconds is None and preview.get("task_state_preview"):
            task_state_preview_seconds = time.perf_counter() - started_at
        status = snapshot.data.get("status")
        if status == "failed":
            failed = controller.jobs.result(job_id)
            raise RuntimeError(failed.message)
        if status == "succeeded":
            if task_state_preview_seconds is None:
                task_state_preview_seconds = time.perf_counter() - started_at
            return snapshot.data, task_state_preview_seconds
        time.sleep(0.01)
    raise TimeoutError(f"load job {job_id} did not complete within {timeout_s:.1f}s")


def _capture_desktop_perf(
    trace_path: Path,
    *,
    profile: str,
    cache_budget_mb: float,
    load_timeout_s: float = 30.0,
) -> dict[str, object]:
    controller = WorkspaceController(cache_budget_mb=cache_budget_mb)
    if tracemalloc.is_tracing():
        tracemalloc.reset_peak()

    preview_started = time.perf_counter()
    load_job = controller.viz_LoadDatasetAsync(str(trace_path))
    load_preview_seconds = time.perf_counter() - preview_started
    if not load_job.ok:
        raise RuntimeError(load_job.message)
    preview = load_job.data.get("preview") or {}
    task_state_preview_seconds: float | None = None
    if preview.get("task_state_preview"):
        task_state_preview_seconds = load_preview_seconds
    snapshot, task_state_preview_seconds = _wait_for_job_completion(
        controller,
        load_job.data["job_id"],
        started_at=preview_started,
        preview_seconds=task_state_preview_seconds,
        timeout_s=float(load_timeout_s),
    )

    resolved = controller.viz_ResolveLoadDatasetJob(load_job.data["job_id"])
    if not resolved.ok:
        raise RuntimeError(resolved.message)
    dataset_id = resolved.data
    controller.active_dataset_id = dataset_id
    time_window = _bundle_window(controller, dataset_id)

    task_states = controller.viz_QueryTaskStates(
        TaskStateQuery(
            time_window=time_window,
        )
    )
    if not task_states.ok:
        raise RuntimeError(task_states.message)

    event_started = time.perf_counter()
    event_page = controller.viz_QueryEventTable(
        EventTableQuery(
            filter={"dataset_id": dataset_id},
            limit=64,
        )
    )
    event_table_first_page_seconds = time.perf_counter() - event_started
    if not event_page.ok:
        raise RuntimeError(event_page.message)

    lod_started = time.perf_counter()
    timeline_lod2 = controller.viz_QueryTimelineLOD(
        {
            "dataset_id": dataset_id,
            "time_window": time_window,
            "filter": {},
            "lod": 2,
            "event_limit": 64,
        }
    )
    lod2_first_window_seconds = time.perf_counter() - lod_started
    if not timeline_lod2.ok:
        raise RuntimeError(timeline_lod2.message)

    cache_stats = controller.repository.query_cache.stats(dataset_id)
    _, peak_memory_bytes = tracemalloc.get_traced_memory()
    task_state_preview = preview.get("task_state_preview") or {}
    return {
        "profile": profile,
        "export_contract_version": EXPORT_CONTRACT_VERSION,
        "load_preview_seconds": round(load_preview_seconds, 6),
        "task_state_preview_seconds": round(float(task_state_preview_seconds), 6),
        "event_table_first_page_seconds": round(event_table_first_page_seconds, 6),
        "lod2_first_window_seconds": round(lod2_first_window_seconds, 6),
        "peak_memory_mb": round(peak_memory_bytes / (1024 * 1024), 3),
        "first_screen_peak_memory_mb": round(peak_memory_bytes / (1024 * 1024), 3),
        "cache_eviction_count": int(cache_stats.get("eviction_count", 0)),
        "cache_budget_bytes": int(cache_stats.get("budget_bytes", 0)),
        "cache_entry_count": int(cache_stats.get("entry_count", 0)),
        "cache_total_bytes": int(cache_stats.get("total_bytes", 0)),
        "cache_namespaces": cache_stats.get("namespaces", {}),
        "preview_stage": load_job.data.get("stage"),
        "resolved_stage": snapshot.get("payload", {}).get("stage"),
        "task_state_preview_lane_count": int(task_state_preview.get("lane_count", 0)),
        "task_state_preview_task_count": len(task_state_preview.get("task_ids") or []),
        "task_state_row_count": len(task_states.data.rows),
        "event_table_first_page_count": len(event_page.data.items),
        "lod2_first_window_count": len(timeline_lod2.data.events),
    }


def _desktop_perf_acceptance(
    baseline_path: Path,
    candidate_path: Path,
    *,
    desktop_perf: dict[str, object],
    memory_report: dict[str, object],
    input_source: str,
    platform_name: str,
    baseline_sha256: str,
    candidate_sha256: str,
    baseline_event_count: int,
    candidate_event_count: int,
    baseline_parse_ok: bool,
    candidate_parse_ok: bool,
    baseline_input_provenance: str,
    candidate_input_provenance: str,
    acceptance_scope: str,
) -> dict[str, object]:
    baseline_size_bytes = baseline_path.stat().st_size
    candidate_size_bytes = candidate_path.stat().st_size
    limitations = []
    baseline_size_ready = baseline_size_bytes >= FORMAL_INPUT_BYTES
    candidate_size_ready = candidate_size_bytes >= FORMAL_INPUT_BYTES
    provenance_set = {baseline_input_provenance, candidate_input_provenance}
    if INPUT_PROVENANCE_PUBLIC_RTOS_SEEDED_PADDED in provenance_set:
        input_provenance = INPUT_PROVENANCE_PUBLIC_RTOS_SEEDED_PADDED
    elif INPUT_PROVENANCE_PUBLIC_RTOS_DERIVED in provenance_set:
        input_provenance = INPUT_PROVENANCE_PUBLIC_RTOS_DERIVED
    elif INPUT_PROVENANCE_SYNTHETIC_PADDED in provenance_set:
        input_provenance = INPUT_PROVENANCE_SYNTHETIC_PADDED
    else:
        input_provenance = INPUT_PROVENANCE_REAL_EXTERNAL
    formal_input_verified = bool(
        baseline_parse_ok
        and candidate_parse_ok
        and baseline_size_ready
        and candidate_size_ready
        and bool(baseline_sha256)
        and bool(candidate_sha256)
        and input_provenance == INPUT_PROVENANCE_REAL_EXTERNAL
    )
    if not (baseline_size_ready and candidate_size_ready):
        limitations.append("input_lt_1gb")
    if input_provenance == INPUT_PROVENANCE_PUBLIC_RTOS_DERIVED:
        limitations.append("public_rtos_derived_input")
    if input_provenance == INPUT_PROVENANCE_PUBLIC_RTOS_SEEDED_PADDED:
        limitations.append("public_rtos_seeded_padded_input")
    if input_provenance == INPUT_PROVENANCE_SYNTHETIC_PADDED:
        limitations.append("synthetic_padded_input")
    peer_platform_limitation = {
        "linux": "windows_not_covered",
        "windows": "linux_not_covered",
    }.get(platform_name, "peer_platform_not_covered")
    limitations.extend(
        [
            peer_platform_limitation,
            "windows_linux_consistency_not_covered",
            "long_duration_stability_not_covered",
        ]
    )
    load_preview_seconds = float(desktop_perf.get("preview_seconds", desktop_perf["load_preview_seconds"]))
    first_screen_peak_memory_mb = float(
        desktop_perf.get("first_screen_peak_memory_mb", desktop_perf["peak_memory_mb"])
    )
    observed_peak_memory_mb, observed_peak_source = _formal_peak_memory_observation(memory_report)
    skipped_steps = (
        ["compare", "repeat_export", "repro_repeat", "short_soak"]
        if acceptance_scope == "perf_only"
        else []
    )
    input_scope_suffix = "formal" if formal_input_verified else "fixture"
    return {
        "platform": platform_name,
        "input_bytes": baseline_size_bytes,
        "input_scope": f"{platform_name}_{input_source}_{input_scope_suffix}",
        "acceptance_scope": acceptance_scope,
        "skipped_steps": skipped_steps,
        "formal_input": {
            "baseline_size_bytes": baseline_size_bytes,
            "candidate_size_bytes": candidate_size_bytes,
            "baseline_sha256": baseline_sha256,
            "candidate_sha256": candidate_sha256,
            "baseline_parse_ok": baseline_parse_ok,
            "candidate_parse_ok": candidate_parse_ok,
            "baseline_event_count": baseline_event_count,
            "candidate_event_count": candidate_event_count,
            "baseline_input_provenance": baseline_input_provenance,
            "candidate_input_provenance": candidate_input_provenance,
            "input_provenance": input_provenance,
            "formal_1gb_verified": formal_input_verified,
        },
        "limitations": limitations,
        "first_screen_lt_10s": {
            "observed_seconds": round(load_preview_seconds, 6),
            "threshold_seconds": FIRST_SCREEN_THRESHOLD_SECONDS,
            "pass": load_preview_seconds < FIRST_SCREEN_THRESHOLD_SECONDS,
            "source": str(desktop_perf.get("preview_mode") or "prescan"),
        },
        "peak_memory_lt_4gb": {
            "observed_mb": round(observed_peak_memory_mb, 3),
            "threshold_mb": PEAK_MEMORY_THRESHOLD_MB,
            "pass": observed_peak_memory_mb < PEAK_MEMORY_THRESHOLD_MB,
            "source": observed_peak_source,
        },
        "first_screen_peak_memory_lt_4gb": {
            "observed_mb": round(first_screen_peak_memory_mb, 3),
            "threshold_mb": PEAK_MEMORY_THRESHOLD_MB,
            "pass": first_screen_peak_memory_mb < PEAK_MEMORY_THRESHOLD_MB,
            "source": "python_peak_alloc_mb",
        },
    }


def _export_duration_field_names(mode: str) -> tuple[str, str]:
    if mode == "full":
        return "export_full_seconds", "export"
    return "export_clipped_seconds", "export_clipped"


def _empty_export_duration_payload() -> dict[str, float | None]:
    payload: dict[str, float | None] = {
        "export_full_seconds": 0.0,
        "export_clipped_seconds": 0.0,
    }
    for prefix in ("export", "export_clipped"):
        for key in EXPORT_WRITE_TIMING_KEYS:
            payload[f"{prefix}_{key}"] = None
    return payload


def _export_package(
    controller: WorkspaceController,
    dataset_id: str,
    output_dir: Path,
    *,
    mode: str,
) -> tuple[ExportService, dict[str, float | None], dict[str, object]]:
    time_window = _bundle_window(controller, dataset_id)
    controller.viz_SetContext(
        {
            "time_window": time_window,
            "filter": {},
            "selection": {"task_id": 1},
            "zoom_level": 1.5,
            "focused_view": "timeline",
            "dataset_role": "single",
        }
    )
    export = ExportService(controller.repository, controller.context_store, controller.jobs)
    if mode == "full":
        job = export.export_Full(
            {
                "dataset_id": dataset_id,
                "run_batch_id": "acceptance-batch",
                "version_id": "acceptance-baseline",
                "experiment_params": {"profile": "acceptance", "repeatable": True},
            }
        )
    else:
        mid = (time_window[0] + time_window[1]) / 2.0
        clipped_window = (max(time_window[0], mid - 1000.0), min(time_window[1], mid + 1000.0))
        job = export.export_Clipped(
            {
                "dataset_id": dataset_id,
                "time_window": clipped_window,
                "filter": {"task_id": 1},
                "selection": {"task_id": 1},
                "focused_view": "timeline",
                "zoom_level": 2.0,
                "run_batch_id": "acceptance-batch",
                "version_id": "acceptance-baseline",
                "experiment_params": {"profile": "acceptance", "mode": "clipped"},
            }
        )
    if not job.ok:
        raise RuntimeError(job.message)
    total_field, component_prefix = _export_duration_field_names(mode)
    write_started = time.perf_counter()
    written = export.export_WritePackage(job.data["job_id"], str(output_dir))
    write_seconds = time.perf_counter() - write_started
    if not written.ok:
        raise RuntimeError(written.message)
    normalize_started = time.perf_counter()
    normalized = export.export_NormalizePackage(str(output_dir))
    normalize_seconds = time.perf_counter() - normalize_started
    if not normalized.ok:
        raise RuntimeError(normalized.message)
    timings: dict[str, float | None] = {
        total_field: _rounded_duration_total(write_seconds, normalize_seconds),
        f"{component_prefix}_write_seconds": _round_duration(write_seconds),
        f"{component_prefix}_normalize_seconds": _round_duration(normalize_seconds),
    }
    write_breakdown = written.data.get("write_timings") if isinstance(written.data, dict) else None
    if isinstance(write_breakdown, dict):
        for key in EXPORT_WRITE_TIMING_KEYS:
            if key in {"write_seconds", "normalize_seconds"}:
                continue
            value = write_breakdown.get(key)
            if isinstance(value, (int, float)):
                timings[f"{component_prefix}_{key}"] = _round_duration(float(value))
    for key in EXPORT_WRITE_TIMING_KEYS:
        field = f"{component_prefix}_{key}"
        timings.setdefault(field, None)
    return export, timings, normalized.data


def _repro_snapshot(package_path: Path) -> tuple[float, dict[str, object]]:
    controller = WorkspaceController()
    repro = ReproService(controller.repository, controller.context_store)
    started = time.perf_counter()
    opened = repro.repro_OpenPackage(str(package_path))
    if not opened.ok:
        raise RuntimeError(opened.message)
    restored = repro.repro_RestoreContext(None)
    if not restored.ok:
        raise RuntimeError(restored.message)
    loaded = repro.repro_LoadAsDataset("single")
    if not loaded.ok:
        raise RuntimeError(loaded.message)
    dataset_id = loaded.data
    bundle = controller.repository.get(dataset_id).artifact.bundle
    time_window = tuple(restored.data.time_window)
    if time_window == (0.0, 0.0):
        time_window = _bundle_window(controller, dataset_id)
    metrics = controller.viz_QueryMetricSeries(
        {
            "dataset_id": dataset_id,
            "time_window": time_window,
            "filter": restored.data.filter,
        }
    )
    alerts = controller.viz_QueryAlerts(
        {
            "dataset_id": dataset_id,
            "time_window": time_window,
            "filter": restored.data.filter,
        }
    )
    if not metrics.ok:
        raise RuntimeError(metrics.message)
    if not alerts.ok:
        raise RuntimeError(alerts.message)
    snapshot = {
        "context": restored.data.persisted_dict(),
        "metric_summary": {
            item["metric_id"]: _metric_scalar(item)
            for item in metrics.data
        },
        "alert_ids": [item.alert_id for item in alerts.data],
        "event_count": len(bundle.event_stream),
        "task_state_count": len(bundle.task_states),
        "exec_slice_count": len(bundle.exec_slices),
    }
    return time.perf_counter() - started, snapshot


def _compare_scope_consistent(controller: WorkspaceController, baseline_id: str, candidate_id: str) -> tuple[float, bool]:
    compare = CompareService(controller.repository, controller.context_store)
    started = time.perf_counter()
    loaded = compare.cmp_LoadPair(baseline_id, candidate_id)
    if not loaded.ok:
        raise RuntimeError(loaded.message)
    scoped = compare.cmp_SetScope(
        {
            "baseline_id": baseline_id,
            "candidate_id": candidate_id,
            "filter": {},
        }
    )
    if not scoped.ok:
        raise RuntimeError(scoped.message)
    summary = compare.cmp_QueryDiffSummary()
    if not summary.ok:
        raise RuntimeError(summary.message)
    baseline_metrics = controller.viz_QueryMetricSeries(
        {
            "dataset_id": scoped.data.baseline_id,
            "time_window": tuple(scoped.data.aligned_time_window),
            "filter": scoped.data.filter,
        }
    )
    candidate_metrics = controller.viz_QueryMetricSeries(
        {
            "dataset_id": scoped.data.candidate_id,
            "time_window": tuple(scoped.data.aligned_time_window),
            "filter": scoped.data.filter,
        }
    )
    if not baseline_metrics.ok:
        raise RuntimeError(baseline_metrics.message)
    if not candidate_metrics.ok:
        raise RuntimeError(candidate_metrics.message)
    baseline_map = {item["metric_id"]: _metric_scalar(item) for item in baseline_metrics.data}
    candidate_map = {item["metric_id"]: _metric_scalar(item) for item in candidate_metrics.data}
    consistent = True
    for row in _summary_metric_rows(summary.data):
        metric_id = row["metric_id"]
        if abs(float(row["baseline"]) - baseline_map[metric_id]) > 1e-9:
            consistent = False
        if abs(float(row["candidate"]) - candidate_map[metric_id]) > 1e-9:
            consistent = False
    return time.perf_counter() - started, consistent


def _short_soak(trace_path: Path, iterations: int) -> bool:
    reference: dict[str, object] | None = None
    for index in range(iterations):
        controller = WorkspaceController()
        loaded = controller.viz_LoadDataset(str(trace_path))
        if not loaded.ok:
            raise RuntimeError(loaded.message)
        package_dir = trace_path.parent / f"soak-package-{index:02d}"
        export, _, normalized = _export_package(controller, loaded.data, package_dir, mode="full")
        normalized_again = export.export_NormalizePackage(str(package_dir))
        if not normalized_again.ok:
            raise RuntimeError(normalized_again.message)
        snapshot = {
            "normalized_package": normalized_again.data,
            "repro": _repro_snapshot(package_dir)[1],
        }
        if reference is None:
            reference = snapshot
            continue
        if snapshot != reference:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_acceptance_baseline")
    parser.add_argument("--internal-perf-only-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-trace-input", help=argparse.SUPPRESS)
    parser.add_argument("--worker-profile", help=argparse.SUPPRESS)
    parser.add_argument("--worker-cache-budget-mb", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--worker-load-timeout-s", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--worker-memory-limit-mb", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--worker-progress-output", help=argparse.SUPPRESS)
    parser.add_argument("--worker-result-output", help=argparse.SUPPRESS)
    parser.add_argument(
        "--mode",
        choices=("smoke_baseline", "desktop_perf_baseline", "desktop_perf_acceptance", "desktop_perf_acceptance_linux"),
        default="smoke_baseline",
    )
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILE_DEFAULTS),
        default="smoke",
    )
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--soak-iterations", type=int)
    parser.add_argument("--cache-budget-mb", type=float)
    parser.add_argument(
        "--load-timeout-s",
        type=float,
        default=30.0,
        help="Timeout (seconds) for waiting background load jobs to complete (default: 30.0).",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Optional scratch directory to place temporary export packages (useful for large inputs).",
    )
    parser.add_argument(
        "--acceptance-scope",
        choices=("full", "perf_only"),
        default="full",
        help="Execution scope for desktop_perf_acceptance (default: full).",
    )
    parser.add_argument("--baseline-input")
    parser.add_argument("--candidate-input")
    parser.add_argument("--platform-label")
    parser.add_argument(
        "--memory-limit-mb",
        type=int,
        default=0,
        help=(
            "Optional Linux memory guard in MB (RLIMIT_AS). Disabled by default and only applied "
            "for --mode desktop_perf_acceptance --acceptance-scope perf_only."
        ),
    )
    parser.add_argument(
        "--output",
        default=str(Path("docs") / "acceptance_baseline_20260314.json"),
    )
    parser.add_argument("--blocker-output")
    args = parser.parse_args(argv)
    if args.internal_perf_only_worker:
        return _perf_only_worker_main(args)
    mode = _canonical_mode(args.mode)
    platform_name = _platform_label(args.platform_label)

    if bool(args.baseline_input) != bool(args.candidate_input):
        raise SystemExit("--baseline-input and --candidate-input must be provided together")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    repeat = int(_profile_value(args.profile, args.repeat, "repeat"))
    soak_iterations = int(_profile_value(args.profile, args.soak_iterations, "soak_iterations"))
    cache_budget_mb = float(_profile_value(args.profile, args.cache_budget_mb, "cache_budget_mb"))

    load_timeout_s = float(args.load_timeout_s)
    if not (load_timeout_s > 0.0):
        raise SystemExit("--load-timeout-s must be a positive number")
    if int(args.memory_limit_mb) < 0:
        raise SystemExit("--memory-limit-mb must be zero or a positive integer")

    workdir: str | None = args.workdir
    if isinstance(workdir, str) and workdir.strip():
        workdir_path = Path(workdir).expanduser().resolve()
        workdir_path.mkdir(parents=True, exist_ok=True)
        if not workdir_path.is_dir():
            raise SystemExit(f"--workdir must be a directory: {workdir_path}")
        workdir = str(workdir_path)
    else:
        workdir = None

    with tempfile.TemporaryDirectory(prefix="rttrace-acceptance-", dir=workdir) as temp_dir:
        root = Path(temp_dir)
        if args.baseline_input and args.candidate_input:
            baseline_path = Path(args.baseline_input).resolve()
            candidate_path = Path(args.candidate_input).resolve()
            input_source = "provided"
            input_profile = {
                "name": args.profile,
                "scenario": None,
                "repeat": None,
                "cache_budget_mb": cache_budget_mb,
            }
        else:
            baseline_path = write_scenario(root / "baseline.trace", name="multi_core", repeat=repeat).resolve()
            candidate_path = write_scenario(
                root / "candidate.trace",
                name="multi_core",
                candidate_variant=True,
                repeat=repeat,
            ).resolve()
            input_source = "generated"
            input_profile = {
                "name": args.profile,
                "scenario": "multi_core",
                "repeat": repeat,
                "cache_budget_mb": cache_budget_mb,
            }

        acceptance_scope = args.acceptance_scope
        blocker_output_path = _derive_blocker_output_path(output_path, args.blocker_output)
        blocker_state: dict[str, object] = {
            "current_stage": "init",
            "last_completed_stage": "init",
            "export_contract_version": EXPORT_CONTRACT_VERSION,
            "timings": {},
            "run_root": str(root),
        }
        requested_memory_limit_mb = int(args.memory_limit_mb)
        if mode == "desktop_perf_acceptance" and acceptance_scope == "perf_only":
            memory_guard = _requested_memory_guard_placeholder(requested_memory_limit_mb)
        else:
            memory_guard = _configure_memory_guard(
                mode=mode,
                acceptance_scope=acceptance_scope,
                requested_limit_mb=requested_memory_limit_mb,
            )
        blocker_state["memory_guard"] = memory_guard
        try:
            tracemalloc.start()
            platform_display_name = _platform_display_name(platform_name)
            compare_seconds: float | None = None
            compare_scope_consistent: bool | None = None
            repro_open_seconds: float | None = None
            repro_repeat_consistent: bool | None = None
            repeat_export_consistent: bool | None = None
            short_soak_passed: bool | None = None
            audit_seconds: float | None = None
            export_duration_payload: dict[str, float | None] = _empty_export_duration_payload()
            baseline_event_count = 0
            candidate_event_count = 0
            baseline_task_state_count = 0
            baseline_exec_slice_count = 0

            if mode == "desktop_perf_acceptance" and acceptance_scope == "perf_only":
                desktop_perf, memory_report, memory_guard = _capture_perf_only_desktop_perf_isolated(
                    baseline_path,
                    profile=args.profile,
                    cache_budget_mb=cache_budget_mb,
                    load_timeout_s=load_timeout_s,
                    requested_memory_limit_mb=requested_memory_limit_mb,
                    blocker_state=blocker_state,
                    run_root=root,
                )
                blocker_state["memory_guard"] = memory_guard
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
                audit = _audit_formal_inputs(baseline_path, candidate_path)
                blocker_state["last_completed_stage"] = "audit"
                blocker_state["timings"]["audit_seconds"] = _round_optional(float(audit["audit_seconds"]))
                audit_seconds = float(audit["audit_seconds"])
                baseline_event_count = int(audit["baseline_event_count"])
                candidate_event_count = int(audit["candidate_event_count"])
                baseline_task_state_count = int(desktop_perf.get("task_state_count", 0))
                baseline_exec_slice_count = int(desktop_perf.get("exec_slice_count", 0))
                baseline_parse_ok = bool(audit["baseline_parse_ok"])
                candidate_parse_ok = bool(audit["candidate_parse_ok"])
                baseline_sha256 = str(audit["baseline_sha256"])
                candidate_sha256 = str(audit["candidate_sha256"])
                baseline_input_provenance = str(audit["baseline_input_provenance"])
                candidate_input_provenance = str(audit["candidate_input_provenance"])
                export_duration_payload = {
                    key: (
                        _round_optional(value)
                        if isinstance(value, (int, float))
                        else None
                    )
                    for key, value in _empty_export_duration_payload().items()
                }
                for key in export_duration_payload:
                    if key in desktop_perf and desktop_perf[key] is not None:
                        export_duration_payload[key] = _round_optional(float(desktop_perf[key]))
                export_full_seconds = float(desktop_perf["export_full_seconds"])
                export_clipped_seconds = float(desktop_perf["export_clipped_seconds"])
                parse_seconds = float(desktop_perf["load_seconds"])
                cache_stats = {
                    "budget_bytes": int(desktop_perf["cache_budget_bytes"]),
                    "entry_count": int(desktop_perf["cache_entry_count"]),
                    "total_bytes": int(desktop_perf["cache_total_bytes"]),
                    "eviction_count": int(desktop_perf["cache_eviction_count"]),
                    "namespaces": dict(desktop_perf["cache_namespaces"]),
                }
                normalized_manifest_entries = int(desktop_perf["normalized_manifest_entries"])
            else:
                desktop_perf = _capture_desktop_perf(
                    baseline_path,
                    profile=args.profile,
                    cache_budget_mb=cache_budget_mb,
                    load_timeout_s=load_timeout_s,
                )
                controller = WorkspaceController(cache_budget_mb=cache_budget_mb)
                load_started = time.perf_counter()
                baseline_loaded = controller.viz_LoadDataset(str(baseline_path))
                candidate_loaded = controller.viz_LoadDataset(str(candidate_path))
                if not baseline_loaded.ok:
                    raise RuntimeError(baseline_loaded.message)
                if not candidate_loaded.ok:
                    raise RuntimeError(candidate_loaded.message)
                parse_seconds = time.perf_counter() - load_started
                baseline_parse_ok = bool(baseline_loaded.ok)
                candidate_parse_ok = bool(candidate_loaded.ok)
                baseline_sha256 = _sha256(baseline_path)
                candidate_sha256 = _sha256(candidate_path)
                baseline_input_provenance = _trace_input_provenance(baseline_path)
                candidate_input_provenance = _trace_input_provenance(candidate_path)

                baseline_bundle = controller.repository.get(baseline_loaded.data).artifact.bundle
                candidate_bundle = controller.repository.get(candidate_loaded.data).artifact.bundle
                baseline_event_count = len(baseline_bundle.event_stream)
                candidate_event_count = len(candidate_bundle.event_stream)
                baseline_task_state_count = len(baseline_bundle.task_states)
                baseline_exec_slice_count = len(baseline_bundle.exec_slices)
                baseline_window = _bundle_window(controller, baseline_loaded.data)
                controller.active_dataset_id = baseline_loaded.data
                task_state_view = controller.viz_QueryTaskStates(
                    TaskStateQuery(
                        time_window=baseline_window,
                    )
                )
                if not task_state_view.ok:
                    raise RuntimeError(task_state_view.message)
                event_page = controller.viz_QueryEventTable(
                    EventTableQuery(
                        filter={"dataset_id": baseline_loaded.data},
                        limit=64,
                    )
                )
                if not event_page.ok:
                    raise RuntimeError(event_page.message)
                timeline_lod2 = controller.viz_QueryTimelineLOD(
                    {
                        "dataset_id": baseline_loaded.data,
                        "time_window": baseline_window,
                        "filter": {},
                        "lod": 2,
                        "event_limit": 64,
                    }
                )
                if not timeline_lod2.ok:
                    raise RuntimeError(timeline_lod2.message)
                export_service, export_full_timing_payload, normalized_a = _export_package(
                    controller,
                    baseline_loaded.data,
                    root / "package-a",
                    mode="full",
                )
                _, export_clipped_timing_payload, _ = _export_package(
                    controller,
                    baseline_loaded.data,
                    root / "package-clipped",
                    mode="clipped",
                )
                export_duration_payload = {
                    **export_full_timing_payload,
                    **export_clipped_timing_payload,
                }
                export_full_seconds = float(export_duration_payload["export_full_seconds"] or 0.0)
                export_clipped_seconds = float(export_duration_payload["export_clipped_seconds"] or 0.0)
                _, peak_memory_bytes = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                os_peak = _os_peak_memory()

                normalized_package = export_service.export_NormalizePackage(str(root / "package-a"))
                if not normalized_package.ok:
                    raise RuntimeError(normalized_package.message)
                cache_stats = controller.repository.query_cache.stats(baseline_loaded.data)
                normalized_manifest_entries = len(normalized_package.data["manifest"]["entries"])

                if acceptance_scope == "full" or mode != "desktop_perf_acceptance":
                    _, _, normalized_b = _export_package(
                        controller,
                        baseline_loaded.data,
                        root / "package-b",
                        mode="full",
                    )
                    repeat_export_consistent = normalized_a == normalized_b
                    compare_seconds, compare_scope_consistent = _compare_scope_consistent(
                        controller,
                        baseline_loaded.data,
                        candidate_loaded.data,
                    )
                    repro_open_seconds, repro_snapshot_a = _repro_snapshot(root / "package-a")
                    repro_snapshot_b = _repro_snapshot(root / "package-a")[1]
                    repro_repeat_consistent = repro_snapshot_a == repro_snapshot_b
                    short_soak_passed = _short_soak(baseline_path, soak_iterations)

            if not (mode == "desktop_perf_acceptance" and acceptance_scope == "perf_only"):
                peak_memory_mb = round(peak_memory_bytes / (1024 * 1024), 3)
                memory_report = {
                    "python_peak_alloc_bytes": int(peak_memory_bytes),
                    "python_peak_alloc_mb": peak_memory_mb,
                    "peak_memory_bytes": int(peak_memory_bytes),
                    "peak_memory_mb": peak_memory_mb,
                    "os_peak": os_peak,
                }
            report_kind_note = {
                "desktop_perf_baseline": "This report is a local desktop staged-load baseline, not a formal NFR acceptance report.",
                "desktop_perf_acceptance": (
                    f"This report is a {platform_display_name} desktop perf acceptance artifact for repo-controlled input; "
                    "it does not replace 1GB formal input validation, Windows parity, or long-duration soak."
                ),
            }.get(mode, "This report is a local smoke baseline, not a formal NFR acceptance report.")
            desktop_perf_acceptance = (
                _desktop_perf_acceptance(
                    baseline_path,
                    candidate_path,
                    desktop_perf=desktop_perf,
                    memory_report=memory_report,
                    input_source=input_source,
                    platform_name=platform_name,
                    baseline_sha256=baseline_sha256,
                    candidate_sha256=candidate_sha256,
                    baseline_event_count=baseline_event_count,
                    candidate_event_count=candidate_event_count,
                    baseline_parse_ok=baseline_parse_ok,
                    candidate_parse_ok=candidate_parse_ok,
                    baseline_input_provenance=baseline_input_provenance,
                    candidate_input_provenance=candidate_input_provenance,
                    acceptance_scope=acceptance_scope,
                )
                if mode == "desktop_perf_acceptance"
                else None
            )

            report = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "mode": mode,
                "export_contract_version": EXPORT_CONTRACT_VERSION,
                "profile": args.profile,
                "environment": _environment_summary(),
                "inputs": {
                    "source": input_source,
                    "profile": input_profile,
                    "baseline_trace": str(baseline_path),
                    "candidate_trace": str(candidate_path),
                    "baseline_sha256": baseline_sha256,
                    "candidate_sha256": candidate_sha256,
                },
                "dataset_scale": {
                    "repeat": input_profile["repeat"],
                    "baseline_event_count": baseline_event_count,
                    "candidate_event_count": candidate_event_count,
                    "baseline_task_state_count": baseline_task_state_count,
                    "baseline_exec_slice_count": baseline_exec_slice_count,
                },
                "durations": {
                    "parse_seconds": round(parse_seconds, 6),
                    "compare_seconds": round(compare_seconds, 6) if compare_seconds is not None else None,
                    **export_duration_payload,
                    "repro_open_seconds": round(repro_open_seconds, 6) if repro_open_seconds is not None else None,
                    "audit_seconds": round(audit_seconds, 6) if audit_seconds is not None else 0.0,
                },
                "memory": memory_report,
                "memory_guard": memory_guard,
                "cache": cache_stats,
                "desktop_perf": desktop_perf,
                **({"desktop_perf_acceptance": desktop_perf_acceptance} if desktop_perf_acceptance is not None else {}),
                "consistency": {
                    "repeat_export_consistent": repeat_export_consistent,
                    "repro_repeat_consistent": repro_repeat_consistent,
                    "compare_scope_consistent": compare_scope_consistent,
                    "short_soak_passed": short_soak_passed,
                    "normalized_manifest_entries": normalized_manifest_entries,
                },
                "notes": [
                    report_kind_note,
                    "The desktop_perf node records local staged-load timings and query-cache observations for the selected profile.",
                    (
                        "This artifact was produced with acceptance_scope=perf_only; compare/repro/repeat-export/short-soak steps were intentionally skipped."
                        if mode == "desktop_perf_acceptance" and acceptance_scope == "perf_only"
                        else "This artifact includes the full acceptance flow."
                    ),
                    (
                        "Large-input runs should use --acceptance-scope perf_only, --load-timeout-s >= 600, and a local scratch directory."
                        if mode == "desktop_perf_acceptance"
                        else "Formal 1GB validation should use desktop_perf_acceptance with perf_only scope."
                    ),
                    "1GB < 10s and < 4GB peak-memory observations are recorded in this artifact; cross-platform parity and long-duration evidence are tracked in dedicated acceptance artifacts.",
                    "Windows/Linux cross-platform evidence and long-duration soak are maintained as separate artifacts and summarized by docs/final_validation_status_20260314.json.",
                    "Performance values here are run-level measurements; project-level closure is governed by final_validation_status/checklist artifacts.",
                ],
            }
            _write_json(output_path, report)
            print(json.dumps(report, indent=2, ensure_ascii=False))
        except Exception as exc:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            blocker = _build_blocker_artifact(
                mode=mode,
                acceptance_scope=acceptance_scope,
                baseline_path=baseline_path,
                candidate_path=candidate_path,
                workdir=workdir,
                blocker_state=blocker_state,
                exc=exc,
            )
            _write_json(blocker_output_path, blocker)
            print(json.dumps(blocker, indent=2, ensure_ascii=False))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

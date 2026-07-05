from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.services import ExportService, ReproService, WorkspaceController
from parser.evidence_sidecar_index import (
    DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES,
    sidecar_index_path_for_source,
    sidecar_stream_scan_fallback_allowed,
)
from spec.io import checksum_file, json_dump, json_load


EXPECTED_CONSUMER_MODE = {
    "compare": "REFERENCE_ONLY",
    "replay": "REFERENCE_ONLY",
    "audit": "REFERENCE_ONLY",
}


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _render_command(argv: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in argv if str(part))


def _build_parent_artifact_refs(
    *,
    package_root: Path,
    raw_root: Path,
    resource_samples_path: Path,
    iterations_path: Path,
    progress_path: Path,
    report_path: Path,
    environment_summary_path: Path,
    seed_selection_path: Path | None,
    sidecar_source: Path | None,
    sidecar_manifest_source: Path | None,
    retained_packages: list[str],
    sidecar_preflight: dict[str, Any] | None,
    command: str,
    timestamp: str,
) -> dict[str, Any]:
    artifact_refs = {
        "command": command,
        "timestamp": timestamp,
        "runner_pid": os.getpid(),
        "package_root": str(package_root),
        "raw_root": str(raw_root),
        "resource_samples_path": str(resource_samples_path),
        "iterations_path": str(iterations_path),
        "progress_path": str(progress_path),
        "report_path": str(report_path),
        "environment_summary": str(environment_summary_path),
        "seed_selection_path": str(seed_selection_path) if seed_selection_path else None,
        "sidecar_source": str(sidecar_source) if sidecar_source else None,
        "sidecar_manifest_source": str(sidecar_manifest_source) if sidecar_manifest_source else None,
        "retained_packages": retained_packages,
    }
    if sidecar_preflight:
        artifact_refs["sidecar_preflight"] = sidecar_preflight
    return artifact_refs


def _parse_json_object(raw: str | None, *, arg_name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if raw is None:
        return dict(default or {})
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid {arg_name}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"invalid {arg_name}: expected JSON object")
    return dict(parsed)


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_dump(path, payload)


def _jsonl_append(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _path_bytes(path: Path) -> int:
    try:
        if path.is_file():
            return int(path.stat().st_size)
        if not path.is_dir():
            return 0
    except OSError:
        return 0

    total = 0
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=True):
                            total += int(entry.stat(follow_symlinks=True).st_size)
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _read_proc_status(pid: int) -> dict[str, int]:
    status_path = Path("/proc") / str(pid) / "status"
    values: dict[str, int] = {}
    if not status_path.exists():
        return values
    try:
        lines = status_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        if key in {"VmRSS", "VmSize", "VmHWM"}:
            try:
                values[key] = int(parts[0]) * 1024
            except ValueError:
                continue
        elif key == "Threads":
            try:
                values[key] = int(parts[0])
            except ValueError:
                continue
    return values


def _read_proc_cpu_seconds(pid: int) -> tuple[float, float]:
    stat_path = Path("/proc") / str(pid) / "stat"
    if not stat_path.exists():
        return (0.0, 0.0)
    try:
        raw = stat_path.read_text(encoding="utf-8").strip()
        end_comm = raw.rfind(")")
        fields = raw[end_comm + 2 :].split()
        ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        utime = float(fields[11]) / float(ticks)
        stime = float(fields[12]) / float(ticks)
    except (OSError, IndexError, KeyError, ValueError):
        return (0.0, 0.0)
    return (utime, stime)


def _read_meminfo() -> dict[str, int]:
    meminfo: dict[str, int] = {}
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return meminfo
    for line in meminfo_path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            meminfo[key] = int(parts[0]) * 1024
        except ValueError:
            continue
    return meminfo


def _summarize_series(values: list[float | int]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "first": None,
            "min": None,
            "median": None,
            "max": None,
            "last": None,
            "growth": None,
        }
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        median: float | int = sorted_values[mid]
    else:
        median = (float(sorted_values[mid - 1]) + float(sorted_values[mid])) / 2.0
    first = values[0]
    last = values[-1]
    return {
        "count": len(values),
        "first": first,
        "min": sorted_values[0],
        "median": median,
        "max": sorted_values[-1],
        "last": last,
        "growth": float(last) - float(first),
    }


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return f"sha256:{checksum_file(path)}"


def _load_json_object_if_present(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json_load(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_sidecar_preflight_summary(
    sidecar_source: Path | None,
    sidecar_manifest_source: Path | None,
) -> dict[str, Any]:
    if sidecar_source is None or sidecar_manifest_source is None:
        return {}
    sidecar_bytes = int(sidecar_source.stat().st_size) if sidecar_source.exists() else 0
    fallback_allowed = sidecar_stream_scan_fallback_allowed(sidecar_bytes)
    index_path = sidecar_index_path_for_source(sidecar_source)
    return {
        "sidecar_source": str(sidecar_source),
        "sidecar_manifest_source": str(sidecar_manifest_source),
        "sidecar_bytes": int(sidecar_bytes),
        "sidecar_stream_scan_threshold_bytes": int(DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES),
        "sidecar_stream_scan_fallback_allowed": bool(fallback_allowed),
        "sidecar_requires_index": bool(not fallback_allowed),
        "sidecar_index_path_candidate": str(index_path),
        "sidecar_index_exists_before_run": bool(index_path.exists()),
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _copy_compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _copy_compact_value(item)
            for key, item in value.items()
            if isinstance(item, (str, int, float, bool)) or item is None
        }
    if isinstance(value, list):
        return [
            _copy_compact_value(item)
            for item in value[:16]
            if isinstance(item, (str, int, float, bool, dict)) or item is None
        ]
    return value


def _summarize_manifest_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    important_paths = {
        "meta.json",
        "event/events.trace",
        "event/ref_index.json",
        "rebuild/rebuild_bundle.json",
        "result/result_validity.json",
        "control/dependency_sidecar.jsonl",
        "control/frontier_snapshot.json",
        "control/frontier_refs.jsonl",
        "control/proof_digest.json",
        "control/sidecar_manifest.json",
        "control/blocker_artifact.json",
    }
    summaries: dict[str, dict[str, Any]] = {}
    for raw_entry in list(manifest.get("entries") or []):
        if not isinstance(raw_entry, dict):
            continue
        entry_path = str(raw_entry.get("path") or "")
        if entry_path not in important_paths:
            continue
        entry_summary: dict[str, Any] = {}
        for key in ("category", "count", "format", "schema_ref"):
            if raw_entry.get(key) is not None:
                entry_summary[key] = raw_entry.get(key)
        checksum = str(raw_entry.get("checksum") or "")
        if checksum:
            entry_summary["checksum"] = checksum if checksum.startswith("sha256:") else f"sha256:{checksum}"
        summaries[entry_path] = entry_summary
    return summaries


def _summarize_proof_digest(proof_digest: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "snapshot_id",
        "closure_mode",
        "complete_wrt_rule_family",
        "rule_family",
        "budget_vector",
        "closure_depth_reached",
        "seed_ref_count",
        "closed_ref_count",
        "missing_required_refs",
        "truncated_frontier_count",
        "frontier_halt_reason",
        "events_emitted",
        "bytes_emitted",
        "scan_count",
        "seek_count",
        "window_span_total",
        "sidecar_lookup_count",
        "round_count",
        "window_hit_rate",
        "peak_rss_mb",
        "sidecar_bytes",
        "sidecar_selector_mode",
        "sidecar_selector_calls",
        "sidecar_bytes_scanned",
        "sidecar_index_build_seconds",
        "proof_hash",
    )
    return {key: _copy_compact_value(proof_digest[key]) for key in keys if key in proof_digest}


def _summarize_frontier_snapshot(frontier_snapshot: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "round_id",
        "closure_mode",
        "frontier_count",
        "consumed_depth",
        "consumed_events",
        "consumed_bytes",
        "projected_next_events",
        "projected_next_bytes",
        "expansion_ratio",
        "halt_reason",
        "frontier_refs_path",
        "truncated_frontier_count",
        "freeze_round_id",
        "pre_read_reject",
        "pre_read_reject_round_id",
    )
    return {key: _copy_compact_value(frontier_snapshot[key]) for key in keys if key in frontier_snapshot}


def _summarize_proof_verification(proof_verification: Any) -> dict[str, Any]:
    if not isinstance(proof_verification, dict):
        return {"ok": False, "issue_count": 1, "issues_sample": ["proof verification payload missing"]}
    issues = [str(issue) for issue in list(proof_verification.get("issues") or [])]
    return {
        "ok": bool(proof_verification.get("ok")),
        "proof_hash_matches": proof_verification.get("proof_hash_matches"),
        "recomputed_proof_hash": proof_verification.get("recomputed_proof_hash"),
        "issue_count": len(issues),
        "issues_sample": issues[:5],
    }


def _summarize_repro_context(context: Any) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}

    def _summarize_filter(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"key_count": 0, "keys": []}
        keys = sorted(str(key) for key in payload)
        return {"key_count": len(keys), "keys": keys[:16]}

    compare_scope = context.get("compare_scope") if isinstance(context.get("compare_scope"), dict) else {}
    compare_scope_summary: dict[str, Any] = {}
    for key in ("scope_id", "baseline_id", "candidate_id", "aligned_time_window", "evidence_policy", "bucket_size"):
        if compare_scope.get(key) is not None:
            compare_scope_summary[key] = _copy_compact_value(compare_scope[key])
    compare_scope_summary["filter"] = _summarize_filter(compare_scope.get("filter"))
    compare_scope_summary["metric_count"] = len(list(compare_scope.get("metric_ids") or []))
    compare_scope_summary["dimension_count"] = len(list(compare_scope.get("dimensions") or []))

    summary: dict[str, Any] = {
        "time_window": list(context.get("time_window") or [])[:2],
        "filter": _summarize_filter(context.get("filter")),
        "selection": _copy_compact_value(dict(context.get("selection") or {})),
        "evidence_anchor": _copy_compact_value(dict(context.get("evidence_anchor") or {})),
        "focused_view": context.get("focused_view"),
        "dataset_role": context.get("dataset_role"),
    }
    if compare_scope_summary:
        summary["compare_scope"] = compare_scope_summary
    return summary


def _summarize_write_result(
    write_result: Any,
    *,
    package_path: Path,
    package_bytes: int,
    sidecar_bytes: int,
    result_validity_count: int,
    proof_digest: dict[str, Any],
    consumer_mode: dict[str, str],
) -> dict[str, Any]:
    data = getattr(write_result, "data", write_result)
    if not isinstance(data, dict):
        data = {}
    package_dir = Path(str(data.get("package_path") or package_path))
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    manifest = data.get("manifest") if isinstance(data.get("manifest"), dict) else {}
    if not meta:
        meta = _load_json_object_if_present(package_dir / "meta.json")
    if not manifest:
        manifest = _load_json_object_if_present(package_dir / "manifest.json")
    manifest_entries = list(manifest.get("entries") or [])
    manifest_entry_summary = _summarize_manifest_entries(manifest)
    proof_hash = str(proof_digest.get("proof_hash") or "")
    closure_mode = str(data.get("closure_mode") or meta.get("closure_mode") or proof_digest.get("closure_mode") or "")
    ok = bool(getattr(write_result, "ok", True))
    code = str(getattr(write_result, "code", "OK" if ok else "ERROR") or "")
    message = str(getattr(write_result, "message", "") or "")
    package_version = str(manifest.get("package_version") or "")
    parser_version = str(meta.get("parser_ver") or "")
    return {
        "ok": ok,
        "status": "ok" if ok else "error",
        "code": code,
        "reason": message or None,
        "package_path": str(package_dir),
        "run_id": str(meta.get("run_id") or ""),
        "snapshot_id": str(
            data.get("snapshot_id")
            or meta.get("snapshot_id")
            or manifest.get("snapshot_id")
            or ""
        ),
        "evidence_version": package_version or parser_version,
        "package_version": package_version,
        "parser_version": parser_version,
        "closure_mode": closure_mode,
        "consumer_mode": dict(consumer_mode),
        "manifest_hash": _sha256_file(package_dir / "manifest.json"),
        "meta_hash": _sha256_file(package_dir / "meta.json"),
        "proof_hash": proof_hash,
        "proof_digest_hash": _sha256_file(package_dir / "control" / "proof_digest.json"),
        "closure_hash": _sha256_file(package_dir / "control" / "frontier_snapshot.json"),
        "sidecar_manifest_hash": _sha256_file(package_dir / "control" / "sidecar_manifest.json"),
        "entry_count": _safe_int(data.get("entry_count"), len(manifest_entries)),
        "event_count": _safe_int(
            data.get("event_count"),
            _safe_int(manifest_entry_summary.get("event/events.trace", {}).get("count")),
        ),
        "alert_count": _safe_int(data.get("alert_count")),
        "diagnosis_count": _safe_int(data.get("diagnosis_count")),
        "result_validity_count": int(result_validity_count),
        "package_bytes": int(package_bytes),
        "sidecar_bytes": int(sidecar_bytes),
        "manifest_entry_count": len(manifest_entries),
        "manifest_entries": manifest_entry_summary,
    }


class ResourceSampler:
    def __init__(
        self,
        *,
        output_path: Path,
        interval_s: float,
        output_root: Path,
        state: dict[str, Any],
    ) -> None:
        self._output_path = output_path
        self._interval_s = max(1.0, float(interval_s))
        self._output_root = output_root
        self._state = state
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="formal-soak-sampler", daemon=True)
        self.samples: list[dict[str, Any]] = []
        self._start_monotonic = time.monotonic()
        self._last_cpu_by_process: dict[str, tuple[float, float]] = {}

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(5.0, self._interval_s + 1.0))

    def sample_now(self) -> None:
        sample = self._build_sample()
        self.samples.append(sample)
        _jsonl_append(self._output_path, sample)

    def _process_snapshot(
        self,
        *,
        pid: int,
        role: str,
        orchestrator_pid: int,
        now_monotonic: float,
    ) -> dict[str, Any] | None:
        if pid <= 0:
            return None
        proc_status = _read_proc_status(pid)
        if not proc_status and pid != os.getpid():
            return None
        cpu_user_s, cpu_system_s = _read_proc_cpu_seconds(pid)
        cpu_total = cpu_user_s + cpu_system_s
        cpu_key = f"{role}:{pid}"
        previous = self._last_cpu_by_process.get(cpu_key)
        if previous is None:
            delta_cpu = 0.0
            delta_time = 0.0
        else:
            previous_cpu_total, previous_ts = previous
            delta_cpu = max(0.0, cpu_total - previous_cpu_total)
            delta_time = max(1e-9, now_monotonic - previous_ts)
        cpu_percent = 0.0 if previous is None else min(100.0 * float(os.cpu_count() or 1), (delta_cpu / delta_time) * 100.0)
        self._last_cpu_by_process[cpu_key] = (cpu_total, now_monotonic)
        return {
            "pid": pid,
            "role": role,
            "orchestrator_pid": orchestrator_pid,
            "rss_bytes": proc_status.get("VmRSS"),
            "hwm_bytes": proc_status.get("VmHWM"),
            "vms_bytes": proc_status.get("VmSize"),
            "threads": proc_status.get("Threads"),
            "cpu_user_seconds": round(cpu_user_s, 6),
            "cpu_system_seconds": round(cpu_system_s, 6),
            "cpu_percent_interval": round(cpu_percent, 3),
            "maxrss_bytes_ru": (
                int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
                if pid == os.getpid()
                else None
            ),
        }

    def _build_sample(self) -> dict[str, Any]:
        orchestrator_pid = os.getpid()
        child_pid = _safe_int(self._state.get("sample_pid"), 0)
        now_monotonic = time.monotonic()
        elapsed_s = max(0.0, now_monotonic - self._start_monotonic)
        parent_process = self._process_snapshot(
            pid=orchestrator_pid,
            role="orchestrator",
            orchestrator_pid=orchestrator_pid,
            now_monotonic=now_monotonic,
        )
        child_process = (
            self._process_snapshot(
                pid=child_pid,
                role="child",
                orchestrator_pid=orchestrator_pid,
                now_monotonic=now_monotonic,
            )
            if child_pid > 0 and child_pid != orchestrator_pid
            else None
        )
        primary_process = child_process or parent_process or {
            "pid": orchestrator_pid,
            "role": "orchestrator",
            "orchestrator_pid": orchestrator_pid,
        }
        host_mem = _read_meminfo()
        disk_total, disk_used, disk_free = shutil.disk_usage(self._output_root)
        loadavg = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        return {
            "captured_at": _iso_now(),
            "elapsed_seconds": round(elapsed_s, 3),
            "iteration": self._state.get("iteration"),
            "stage": self._state.get("stage"),
            "sampler_mode": "parent_child_dual",
            "process": primary_process,
            "processes": {
                "parent": parent_process,
                "child": child_process,
            },
            "host": {
                "mem_total_bytes": host_mem.get("MemTotal"),
                "mem_available_bytes": host_mem.get("MemAvailable"),
                "mem_free_bytes": host_mem.get("MemFree"),
                "loadavg_1m": round(float(loadavg[0]), 3),
                "loadavg_5m": round(float(loadavg[1]), 3),
                "loadavg_15m": round(float(loadavg[2]), 3),
                "disk_total_bytes": int(disk_total),
                "disk_used_bytes": int(disk_used),
                "disk_free_bytes": int(disk_free),
                "soak_output_bytes": _path_bytes(self._output_root),
            },
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_now()
            if self._stop.wait(self._interval_s):
                break


def _load_linked_proof_group(path: Path) -> dict[str, Any]:
    payload = json_load(path)
    return {
        "proof_group": str(payload.get("proof_group") or path.parent.name),
        "summary_path": str(path),
        "platform": str(payload.get("platform") or ""),
        "verdict": str(payload.get("verdict") or ""),
        "contract_version": str(payload.get("contract_version") or ""),
        "run_scope": str(payload.get("run_scope") or ""),
        "proof_hashes": list((payload.get("observations") or {}).get("proof_hashes") or []),
    }


def _measure_post_gc_rss_bytes() -> int | None:
    gc.collect()
    gc.collect()
    return _read_proc_status(os.getpid()).get("VmRSS")


def _build_iteration_error_row(
    *,
    iteration: int,
    iteration_started_at: str,
    stage: str | None,
    error_type: str,
    error_message: str,
    traceback_text: str,
    post_gc_rss_bytes: int | None,
    package_path: str | None = None,
    subprocess_returncode: int | None = None,
) -> dict[str, Any]:
    row = {
        "captured_at": _iso_now(),
        "iteration": iteration,
        "iteration_started_at": iteration_started_at,
        "iteration_finished_at": _iso_now(),
        "status": "error",
        "stage": stage,
        "error_type": error_type,
        "error_message": error_message,
        "traceback": traceback_text,
        "post_gc_rss_bytes": post_gc_rss_bytes,
    }
    if package_path:
        row["package_path"] = package_path
    if subprocess_returncode is not None:
        row["subprocess_returncode"] = int(subprocess_returncode)
    return row


def _build_iteration(
    *,
    trace_path: Path,
    package_root: Path,
    iteration: int,
    state: dict[str, Any],
    context_delta: dict[str, Any],
    seed_spec: dict[str, Any],
    rule_family: list[str],
    budget_vector: dict[str, Any],
    closure_policy: dict[str, Any],
    embodiment_mode: str,
    sidecar_source: Path | None,
    sidecar_manifest_source: Path | None,
    expected_proof_hash: str | None,
    expected_closure_mode: str | None,
    expected_consumer_mode: dict[str, str],
) -> dict[str, Any]:
    package_dir = package_root / f"run_{iteration:06d}"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    timings: dict[str, float] = {}
    controller = WorkspaceController()
    state["stage"] = "load_trace"
    load_started = time.perf_counter()
    loaded = controller.viz_LoadDataset(str(trace_path))
    if not loaded.ok:
        raise RuntimeError(loaded.message)
    dataset_id = str(loaded.data)
    timings["load_seconds"] = round(time.perf_counter() - load_started, 6)

    state["stage"] = "set_context"
    set_context = controller.viz_SetContext(context_delta)
    if not set_context.ok:
        raise RuntimeError(set_context.message)

    export_payload: dict[str, Any] = {
        "dataset_id": dataset_id,
        "time_window": list(context_delta.get("time_window") or [0.0, 0.0]),
        "seed_spec": dict(seed_spec),
        "rule_family": list(rule_family),
        "budget_vector": dict(budget_vector),
        "closure_policy": dict(closure_policy),
        "embodiment_mode": embodiment_mode,
    }
    if sidecar_source is not None:
        export_payload["sidecar_source"] = str(sidecar_source)
    if sidecar_manifest_source is not None:
        export_payload["sidecar_manifest_source"] = str(sidecar_manifest_source)
    sidecar_preflight = _build_sidecar_preflight_summary(sidecar_source, sidecar_manifest_source)

    export = ExportService(controller.repository, controller.context_store, controller.jobs)
    state["stage"] = "export"
    export_started = time.perf_counter()
    job = export.export_Evidence(export_payload)
    if not job.ok:
        raise RuntimeError(job.message)
    written = export.export_WriteEvidencePackage(job.data["job_id"], str(package_dir))
    if not written.ok:
        raise RuntimeError(written.message)
    timings["export_seconds"] = round(time.perf_counter() - export_started, 6)

    repro = ReproService(controller.repository, controller.context_store, controller.jobs)
    state["stage"] = "repro_open"
    repro_started = time.perf_counter()
    opened = repro.repro_OpenPackage(str(package_dir))
    if not opened.ok:
        raise RuntimeError(opened.message)
    restored = repro.repro_RestoreContext(None)
    if not restored.ok:
        raise RuntimeError(restored.message)
    loaded_dataset = repro.repro_LoadAsDataset("single")
    if not loaded_dataset.ok:
        raise RuntimeError(loaded_dataset.message)
    timings["repro_open_seconds"] = round(time.perf_counter() - repro_started, 6)

    state["stage"] = "proof_query"
    proof_started = time.perf_counter()
    proof_query = repro.repro_QueryProof()
    if not proof_query.ok:
        raise RuntimeError(proof_query.message)
    timings["proof_query_seconds"] = round(time.perf_counter() - proof_started, 6)

    state["stage"] = "collect_metrics"
    proof_digest = json_load(package_dir / "control" / "proof_digest.json")
    frontier_snapshot = json_load(package_dir / "control" / "frontier_snapshot.json")
    result_validity = json_load(package_dir / "result" / "result_validity.json")
    package_bytes = _path_bytes(package_dir)
    sidecar_bytes = 0
    package_sidecar = package_dir / "control" / "dependency_sidecar.jsonl"
    if package_sidecar.exists():
        sidecar_bytes = int(package_sidecar.stat().st_size)

    package_contract_validation_passed = bool(proof_query.data.get("proof_verification", {}).get("ok"))
    consumer_mode = dict(opened.data.get("consumer_mode") or {})
    closure_mode = str(proof_digest.get("closure_mode") or "")
    proof_hash = str(proof_digest.get("proof_hash") or "")
    write_result_summary = _summarize_write_result(
        written,
        package_path=package_dir,
        package_bytes=package_bytes,
        sidecar_bytes=sidecar_bytes,
        result_validity_count=len(list(result_validity.get("results") or [])),
        proof_digest=proof_digest,
        consumer_mode=consumer_mode,
    )
    proof_verification_summary = _summarize_proof_verification(proof_query.data.get("proof_verification"))

    timings["total_seconds"] = round(sum(timings.values()), 6)
    return {
        "captured_at": _iso_now(),
        "iteration": iteration,
        "dataset_id": dataset_id,
        "restored_dataset_id": str(loaded_dataset.data),
        "package_path": str(package_dir),
        "run_id": write_result_summary.get("run_id"),
        "package_bytes": package_bytes,
        "sidecar_bytes": sidecar_bytes,
        "timings": timings,
        "write_result": write_result_summary,
        "proof_digest": _summarize_proof_digest(proof_digest),
        "frontier_snapshot": _summarize_frontier_snapshot(frontier_snapshot),
        "manifest_hash": write_result_summary.get("manifest_hash"),
        "proof_digest_hash": write_result_summary.get("proof_digest_hash"),
        "proof_hash": proof_hash,
        "closure_hash": write_result_summary.get("closure_hash"),
        "sidecar_manifest_hash": write_result_summary.get("sidecar_manifest_hash"),
        "evidence_version": write_result_summary.get("evidence_version"),
        "result_validity_count": write_result_summary.get("result_validity_count"),
        "minimal_legal_package_valid": True,
        "package_contract_validation_passed": package_contract_validation_passed,
        "proof_consumer_mode": consumer_mode,
        "expected_consumer_mode": dict(expected_consumer_mode),
        "consumer_mode_matches_expected": consumer_mode == expected_consumer_mode,
        "expected_proof_hash": expected_proof_hash,
        "proof_hash_matches_expected": proof_hash == expected_proof_hash if expected_proof_hash else None,
        "closure_mode": closure_mode,
        "expected_closure_mode": expected_closure_mode,
        "closure_mode_matches_expected": closure_mode == expected_closure_mode if expected_closure_mode else None,
        "proof_verification": proof_verification_summary,
        "repro_context": _summarize_repro_context(restored.data.persisted_dict()),
        "sidecar_preflight": sidecar_preflight,
        "sidecar_selector_summary": {
            "sidecar_selector_mode": proof_digest.get("sidecar_selector_mode"),
            "sidecar_selector_calls": proof_digest.get("sidecar_selector_calls"),
            "sidecar_lookup_count": proof_digest.get("sidecar_lookup_count"),
            "sidecar_bytes_scanned": proof_digest.get("sidecar_bytes_scanned"),
            "sidecar_index_build_seconds": proof_digest.get("sidecar_index_build_seconds"),
        },
    }


def _run_child_iteration(
    *,
    config_path: Path,
    output_path: Path,
) -> int:
    payload = json_load(config_path)
    if not isinstance(payload, dict):
        raise SystemExit("--child-iteration-config must contain a JSON object")

    iteration = _safe_int(payload.get("iteration"), 0)
    if iteration <= 0:
        raise SystemExit("child iteration config requires positive iteration")

    trace_path = Path(str(payload.get("trace_path") or "")).expanduser().resolve()
    package_root = Path(str(payload.get("package_root") or "")).expanduser().resolve()
    context_delta = dict(payload.get("context_delta") or {})
    seed_spec = dict(payload.get("seed_spec") or {})
    rule_family = [str(item) for item in list(payload.get("rule_family") or []) if str(item).strip()]
    budget_vector = dict(payload.get("budget_vector") or {})
    closure_policy = dict(payload.get("closure_policy") or {})
    embodiment_mode = str(payload.get("embodiment_mode") or "mode_a")
    sidecar_source = payload.get("sidecar_source")
    sidecar_manifest_source = payload.get("sidecar_manifest_source")
    expected_proof_hash = str(payload.get("expected_proof_hash") or "") or None
    expected_closure_mode = str(payload.get("expected_closure_mode") or "") or None
    expected_consumer_mode = dict(payload.get("expected_consumer_mode") or EXPECTED_CONSUMER_MODE)

    state: dict[str, Any] = {"iteration": iteration, "stage": "child_init"}
    iteration_started_at = _iso_now()
    package_path = package_root / f"run_{iteration:06d}"

    try:
        row = _build_iteration(
            trace_path=trace_path,
            package_root=package_root,
            iteration=iteration,
            state=state,
            context_delta=context_delta,
            seed_spec=seed_spec,
            rule_family=rule_family,
            budget_vector=budget_vector,
            closure_policy=closure_policy,
            embodiment_mode=embodiment_mode,
            sidecar_source=Path(str(sidecar_source)).expanduser().resolve() if sidecar_source else None,
            sidecar_manifest_source=(
                Path(str(sidecar_manifest_source)).expanduser().resolve() if sidecar_manifest_source else None
            ),
            expected_proof_hash=expected_proof_hash,
            expected_closure_mode=expected_closure_mode,
            expected_consumer_mode=expected_consumer_mode,
        )
        state["stage"] = "cleanup_gc"
        row["post_gc_rss_bytes"] = _measure_post_gc_rss_bytes()
        row["iteration_started_at"] = iteration_started_at
        row["iteration_finished_at"] = _iso_now()
        row["status"] = "ok"
        _json_write(output_path, row)
        return 0
    except Exception as exc:
        row = _build_iteration_error_row(
            iteration=iteration,
            iteration_started_at=iteration_started_at,
            stage=str(state.get("stage") or ""),
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback_text=traceback.format_exc(),
            post_gc_rss_bytes=_measure_post_gc_rss_bytes(),
            package_path=str(package_path),
        )
        _json_write(output_path, row)
        return 1


def _run_iteration_subprocess(
    *,
    child_config: dict[str, Any],
    raw_root: Path,
    state: dict[str, Any],
    stop_requested: Callable[[], bool],
    python_executable: str | None = None,
    script_path: Path | None = None,
) -> dict[str, Any]:
    iteration = _safe_int(child_config.get("iteration"), 0)
    config_path = raw_root / f"iteration_{iteration:06d}.child-config.json"
    output_path = raw_root / f"iteration_{iteration:06d}.child-row.json"
    _json_write(config_path, child_config)
    command = [
        python_executable or sys.executable,
        str((script_path or Path(__file__).resolve()).expanduser().resolve()),
        "--child-iteration-config",
        str(config_path),
        "--child-iteration-output",
        str(output_path),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    state["sample_pid"] = process.pid
    state["stage"] = "child_iteration"
    terminate_started_at: float | None = None
    try:
        while process.poll() is None:
            if stop_requested():
                if terminate_started_at is None:
                    state["stage"] = "child_stop_requested"
                    process.terminate()
                    terminate_started_at = time.monotonic()
                elif time.monotonic() - terminate_started_at >= 5.0:
                    state["stage"] = "child_kill_requested"
                    process.kill()
            time.sleep(0.2)
        stdout_text, stderr_text = process.communicate()
        row = _load_json_object_if_present(output_path)
        return {
            "row": row if row else None,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "returncode": int(process.returncode or 0),
            "child_pid": process.pid,
        }
    finally:
        state.pop("sample_pid", None)
        for path in (config_path, output_path):
            try:
                path.unlink()
            except FileNotFoundError:
                continue


def _retention_flags(iteration: int, total_successes: int, retain_every: int) -> bool:
    if iteration == 1:
        return True
    if retain_every > 0 and total_successes % retain_every == 0:
        return True
    return False


def _has_value_drift(rows: list[dict[str, Any]], key: str) -> bool:
    values = {
        str(row.get(key))
        for row in rows
        if row.get("status") == "ok" and row.get(key) is not None and str(row.get(key)) != ""
    }
    return len(values) > 1


def _cleanup_previous_latest(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    shutil.rmtree(path)


def _compact_subprocess_output(stdout_text: str, stderr_text: str, *, limit: int = 12000) -> str:
    parts: list[str] = []
    if stderr_text.strip():
        parts.append(f"[stderr]\n{stderr_text.strip()}")
    if stdout_text.strip():
        parts.append(f"[stdout]\n{stdout_text.strip()}")
    if not parts:
        return ""
    rendered = "\n\n".join(parts)
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: limit - 3]}..."


def _sample_process_payload(sample: dict[str, Any], role: str) -> dict[str, Any]:
    normalized_role = "parent" if role in {"parent", "orchestrator"} else role
    processes = sample.get("processes")
    if isinstance(processes, dict):
        payload = processes.get(normalized_role)
        if isinstance(payload, dict):
            return payload
        return {}
    process = sample.get("process")
    process_role = str((process or {}).get("role") or "") if isinstance(process, dict) else ""
    if isinstance(process, dict) and (
        process_role == role
        or (normalized_role == "parent" and process_role == "orchestrator")
    ):
        return process
    return {}


def _sample_process_series(samples: list[dict[str, Any]], role: str, key: str) -> list[int | float]:
    values: list[int | float] = []
    for sample in samples:
        process = _sample_process_payload(sample, role)
        value = process.get(key)
        if value is None:
            continue
        try:
            values.append(float(value) if isinstance(value, float) else int(value))
        except (TypeError, ValueError):
            continue
    return values


def _build_report(
    *,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    target_duration_seconds: float,
    input_contract: dict[str, Any],
    environment_summary_path: Path,
    linked_proof_groups: list[dict[str, Any]],
    artifact_refs: dict[str, Any],
    iteration_rows: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    error_count: int,
    first_error: dict[str, Any] | None,
    stop_requested: bool,
) -> dict[str, Any]:
    rss_values = [
        int(sample["process"]["rss_bytes"])
        for sample in samples
        if sample.get("process", {}).get("rss_bytes") is not None
    ]
    parent_rss_values = [int(value) for value in _sample_process_series(samples, "orchestrator", "rss_bytes")]
    child_rss_values = [int(value) for value in _sample_process_series(samples, "child", "rss_bytes")]
    parent_hwm_values = [int(value) for value in _sample_process_series(samples, "orchestrator", "hwm_bytes")]
    child_hwm_values = [int(value) for value in _sample_process_series(samples, "child", "hwm_bytes")]
    parent_cpu_values = [float(value) for value in _sample_process_series(samples, "orchestrator", "cpu_percent_interval")]
    child_cpu_values = [float(value) for value in _sample_process_series(samples, "child", "cpu_percent_interval")]
    avail_mem_values = [
        int(sample["host"]["mem_available_bytes"])
        for sample in samples
        if sample.get("host", {}).get("mem_available_bytes") is not None
    ]
    output_bytes_values = [
        int(sample["host"]["soak_output_bytes"])
        for sample in samples
        if sample.get("host", {}).get("soak_output_bytes") is not None
    ]
    cpu_values = [
        float(sample["process"]["cpu_percent_interval"])
        for sample in samples
        if sample.get("process", {}).get("cpu_percent_interval") is not None
    ]
    runtime_values = [float(row["timings"]["total_seconds"]) for row in iteration_rows if row.get("timings")]
    package_values = [int(row["package_bytes"]) for row in iteration_rows if row.get("package_bytes") is not None]
    post_gc_rss_values = [int(row["post_gc_rss_bytes"]) for row in iteration_rows if row.get("post_gc_rss_bytes") is not None]
    memory_leak_suspected = False
    if len(post_gc_rss_values) >= 3:
        head = post_gc_rss_values[: min(3, len(post_gc_rss_values))]
        tail = post_gc_rss_values[-min(3, len(post_gc_rss_values)) :]
        head_median = _summarize_series(head)["median"]
        tail_median = _summarize_series(tail)["median"]
        if head_median is not None and tail_median is not None:
            growth = float(tail_median) - float(head_median)
            memory_leak_suspected = growth > max(256.0 * 1024 * 1024, float(head_median) * 0.5)
    unexpected_paths = []
    retained_paths = set(str(path) for path in artifact_refs.get("retained_packages") or [])
    package_root = Path(str(artifact_refs.get("package_root")))
    if package_root.exists():
        for child in sorted(package_root.iterdir()):
            if child.is_dir() and str(child) not in retained_paths:
                unexpected_paths.append(str(child))
    proof_hash_drift = (
        any(row.get("proof_hash_matches_expected") is False for row in iteration_rows)
        or _has_value_drift(iteration_rows, "proof_hash")
    )
    closure_mode_drift = (
        any(row.get("closure_mode_matches_expected") is False for row in iteration_rows)
        or _has_value_drift(iteration_rows, "closure_mode")
    )
    closure_hash_drift = _has_value_drift(iteration_rows, "closure_hash")
    consumer_mode_drift = any(not bool(row.get("consumer_mode_matches_expected")) for row in iteration_rows)
    package_contract_broken = any(not bool(row.get("package_contract_validation_passed")) for row in iteration_rows)
    crash_detected = error_count > 0
    disk_leak_suspected = bool(unexpected_paths)
    linked_groups_cover_all = {row["proof_group"] for row in linked_proof_groups} >= {
        "A_control_plane_first",
        "B_budget_pre_freeze",
        "C_degraded_audit",
    }
    selector_modes = sorted(
        {
            str((row.get("proof_digest") or {}).get("sidecar_selector_mode"))
            for row in iteration_rows
            if str((row.get("proof_digest") or {}).get("sidecar_selector_mode") or "").strip()
        }
    )
    selector_calls_total = sum(int((row.get("proof_digest") or {}).get("sidecar_selector_calls", 0)) for row in iteration_rows)
    selector_lookup_total = sum(int((row.get("proof_digest") or {}).get("sidecar_lookup_count", 0)) for row in iteration_rows)
    sidecar_bytes_scanned_total = sum(
        int((row.get("proof_digest") or {}).get("sidecar_bytes_scanned", 0))
        for row in iteration_rows
    )
    sidecar_index_build_seconds_total = round(
        sum(float((row.get("proof_digest") or {}).get("sidecar_index_build_seconds", 0.0) or 0.0) for row in iteration_rows),
        6,
    )
    duration_met = duration_seconds >= target_duration_seconds
    verdict = "pass"
    if crash_detected or memory_leak_suspected or disk_leak_suspected or package_contract_broken:
        verdict = "fail"
    if proof_hash_drift or closure_mode_drift or closure_hash_drift or consumer_mode_drift:
        verdict = "fail"
    if not duration_met or not linked_groups_cover_all or stop_requested:
        verdict = "fail"
    report = {
        "generated_at": finished_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "platform": platform.system().lower(),
        "scenario": "patent_10_4_wp06_long_duration_soak",
        "duration_seconds": round(duration_seconds, 6),
        "target_duration_seconds": float(target_duration_seconds),
        "iteration_count": len(iteration_rows),
        "error_count": int(error_count),
        "first_error": first_error,
        "environment_summary": str(environment_summary_path),
        "input_contract": input_contract,
        "linked_proof_groups": linked_proof_groups,
        "artifact_refs": artifact_refs,
        "resource_trends": {
            "sample_count": len(samples),
            "sampler_mode": "parent_child_dual",
            "process_rss_bytes": _summarize_series(rss_values),
            "parent_rss_bytes": _summarize_series(parent_rss_values),
            "child_rss_bytes": _summarize_series(child_rss_values),
            "parent_hwm_bytes": _summarize_series(parent_hwm_values),
            "child_hwm_bytes": _summarize_series(child_hwm_values),
            "parent_peak_rss_bytes": _summarize_series(parent_hwm_values),
            "child_peak_rss_bytes": _summarize_series(child_hwm_values),
            "parent_cpu_percent": _summarize_series(parent_cpu_values),
            "child_cpu_percent": _summarize_series(child_cpu_values),
            "host_mem_available_bytes": _summarize_series(avail_mem_values),
            "output_root_bytes": _summarize_series(output_bytes_values),
            "process_cpu_percent": _summarize_series(cpu_values),
            "iteration_runtime_seconds": _summarize_series(runtime_values),
            "package_bytes": _summarize_series(package_values),
            "post_gc_rss_bytes": _summarize_series(post_gc_rss_values),
        },
        "stability_assessment": {
            "duration_requirement_met": duration_met,
            "proof_chain_link_complete": linked_groups_cover_all,
            "crash_detected": crash_detected,
            "resource_leak_detected": memory_leak_suspected or disk_leak_suspected,
            "memory_leak_suspected": memory_leak_suspected,
            "disk_leak_suspected": disk_leak_suspected,
            "unexpected_package_paths": unexpected_paths,
            "package_contract_broken": package_contract_broken,
            "proof_hash_drift_detected": proof_hash_drift,
            "closure_mode_drift_detected": closure_mode_drift,
            "closure_hash_drift_detected": closure_hash_drift,
            "consumer_mode_drift_detected": consumer_mode_drift,
            "stop_requested": stop_requested,
        },
        "mode_b_sidecar_summary": {
            "selector_modes": selector_modes,
            "sidecar_selector_calls_total": int(selector_calls_total),
            "sidecar_lookup_count_total": int(selector_lookup_total),
            "sidecar_bytes_scanned_total": int(sidecar_bytes_scanned_total),
            "sidecar_index_build_seconds_total": float(sidecar_index_build_seconds_total),
        },
        "verdict": verdict,
    }
    return report


def _build_parent_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_patent_formal_soak")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--sample-interval-s", type=float, default=60.0)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--progress-path", required=True)
    parser.add_argument("--resource-samples-path", required=True)
    parser.add_argument("--iterations-path", required=True)
    parser.add_argument("--irrecoverable-output", required=True)
    parser.add_argument("--environment-summary", required=True)
    parser.add_argument("--input-qualification-report", required=True)
    parser.add_argument("--seed-selection")
    parser.add_argument("--sidecar-source")
    parser.add_argument("--sidecar-manifest-source")
    parser.add_argument("--seed-spec-json")
    parser.add_argument("--rule-family", action="append")
    parser.add_argument("--budget-vector-json")
    parser.add_argument("--closure-policy-json")
    parser.add_argument("--embodiment-mode", default="mode_a", choices=["mode_a", "mode_b"])
    parser.add_argument("--contract-version", required=True)
    parser.add_argument("--run-scope", required=True)
    parser.add_argument("--proof-group", default="WP-06_long_duration_soak")
    parser.add_argument("--expected-proof-hash")
    parser.add_argument("--expected-closure-mode")
    parser.add_argument("--linked-proof-summary", action="append", default=[])
    parser.add_argument("--retain-every", type=int, default=0)
    return parser


def _build_child_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_patent_formal_soak_child")
    parser.add_argument("--child-iteration-config", required=True)
    parser.add_argument("--child-iteration-output", required=True)
    return parser


def _main_child(argv: list[str]) -> int:
    args = _build_child_parser().parse_args(argv)
    return _run_child_iteration(
        config_path=Path(args.child_iteration_config).expanduser().resolve(),
        output_path=Path(args.child_iteration_output).expanduser().resolve(),
    )


def _main_parent(argv: list[str]) -> int:
    args = _build_parent_parser().parse_args(argv)

    trace_path = Path(args.trace).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    report_path = Path(args.report_path).expanduser().resolve()
    progress_path = Path(args.progress_path).expanduser().resolve()
    resource_samples_path = Path(args.resource_samples_path).expanduser().resolve()
    iterations_path = Path(args.iterations_path).expanduser().resolve()
    irrecoverable_path = Path(args.irrecoverable_output).expanduser().resolve()
    environment_summary_path = Path(args.environment_summary).expanduser().resolve()
    input_report_path = Path(args.input_qualification_report).expanduser().resolve()
    seed_selection_path = Path(args.seed_selection).expanduser().resolve() if args.seed_selection else None
    sidecar_source = Path(args.sidecar_source).expanduser().resolve() if args.sidecar_source else None
    sidecar_manifest_source = (
        Path(args.sidecar_manifest_source).expanduser().resolve() if args.sidecar_manifest_source else None
    )

    if not trace_path.exists():
        raise SystemExit(f"--trace does not exist: {trace_path}")
    if not environment_summary_path.exists():
        raise SystemExit(f"--environment-summary does not exist: {environment_summary_path}")
    if not input_report_path.exists():
        raise SystemExit(f"--input-qualification-report does not exist: {input_report_path}")
    if args.embodiment_mode == "mode_b":
        if sidecar_source is None or sidecar_manifest_source is None:
            raise SystemExit("mode_b requires --sidecar-source and --sidecar-manifest-source")
        if not sidecar_source.exists():
            raise SystemExit(f"--sidecar-source does not exist: {sidecar_source}")
        if not sidecar_manifest_source.exists():
            raise SystemExit(f"--sidecar-manifest-source does not exist: {sidecar_manifest_source}")
    sidecar_preflight = _build_sidecar_preflight_summary(sidecar_source, sidecar_manifest_source)

    output_root.mkdir(parents=True, exist_ok=True)
    _json_write(
        progress_path,
        {
            "generated_at": _iso_now(),
            "pid": os.getpid(),
            "status": "preflight",
            "contract_version": args.contract_version,
            "run_scope": args.run_scope,
            "proof_group": args.proof_group,
            "trace_path": str(trace_path),
            "duration_target_seconds": float(args.duration_s),
            "current_stage": "input_contract_verification",
            **({"sidecar_preflight": sidecar_preflight} if sidecar_preflight else {}),
        },
    )

    input_report = json_load(input_report_path)
    actual_trace_sha256 = checksum_file(trace_path)
    actual_trace_size = int(trace_path.stat().st_size)
    expected_trace_sha256 = str(input_report.get("trace_sha256") or actual_trace_sha256)
    expected_trace_size = int(input_report.get("trace_size_bytes") or actual_trace_size)
    if actual_trace_sha256 != expected_trace_sha256:
        raise SystemExit(
            f"trace sha256 mismatch: actual={actual_trace_sha256} report={expected_trace_sha256}"
        )
    if actual_trace_size != expected_trace_size:
        raise SystemExit(f"trace size mismatch: actual={actual_trace_size} report={expected_trace_size}")

    seed_selection = json_load(seed_selection_path) if seed_selection_path else {}
    anchor_ref = str(seed_selection.get("anchor_ref") or "")
    time_window = [float(value) for value in list(seed_selection.get("time_window") or [])[:2]]
    while len(time_window) < 2:
        time_window.append(time_window[0] if time_window else 0.0)
    context_delta = {
        "time_window": list(time_window),
        "filter": {},
        "selection": {"seed_ref": anchor_ref} if anchor_ref else {},
        "zoom_level": 1.0,
        "focused_view": "timeline",
        "evidence_anchor": {"ref_key": anchor_ref} if anchor_ref else {},
    }
    seed_spec = _parse_json_object(
        args.seed_spec_json,
        arg_name="--seed-spec-json",
        default={"source_kind": "analysis_context", "source_payload": {}},
    )
    budget_vector = _parse_json_object(
        args.budget_vector_json,
        arg_name="--budget-vector-json",
        default={"D_max": 128, "C_events": 1048576, "S_bytes": 1073741824, "rho_max": 64.0},
    )
    closure_policy = _parse_json_object(
        args.closure_policy_json,
        arg_name="--closure-policy-json",
        default={"allow_bounded": True, "allow_degraded": True, "frontier_ref_limit": 4096},
    )
    rule_family = [str(item) for item in (args.rule_family or ["ref_ref"]) if str(item).strip()]

    package_root = output_root / "packages"
    raw_root = output_root / "raw"
    package_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    for stale in [resource_samples_path, iterations_path]:
        if stale.exists():
            stale.unlink()

    linked_proof_groups = [
        _load_linked_proof_group(Path(path).expanduser().resolve()) for path in list(args.linked_proof_summary or [])
    ]
    input_contract = {
        "input_class": str(input_report.get("input_class") or ""),
        "trace_sha256": actual_trace_sha256,
        "trace_size_bytes": actual_trace_size,
        "formal_1gb_verified": bool(input_report.get("formal_1gb_verified")),
        "trace_path": str(trace_path),
        "input_qualification_report": str(input_report_path),
        "contract_version": str(args.contract_version),
        "run_scope": str(args.run_scope),
        "proof_group": str(args.proof_group),
    }
    if sidecar_preflight:
        input_contract["sidecar_preflight"] = sidecar_preflight

    state: dict[str, Any] = {"iteration": 0, "stage": "init"}
    stop_requested = False

    def _request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True
        state["stage"] = "stop_requested"

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    sampler = ResourceSampler(
        output_path=resource_samples_path,
        interval_s=args.sample_interval_s,
        output_root=output_root,
        state=state,
    )
    sampler.start()

    started_at = _iso_now()
    started_monotonic = time.monotonic()
    iteration_rows: list[dict[str, Any]] = []
    retained_packages: list[str] = []
    latest_retained_success: Path | None = None
    error_count = 0
    first_error: dict[str, Any] | None = None
    success_count = 0
    exit_code = 0

    try:
        _json_write(
            progress_path,
            {
                "generated_at": started_at,
                "pid": os.getpid(),
                "status": "running",
                "contract_version": args.contract_version,
                "run_scope": args.run_scope,
                "proof_group": args.proof_group,
                "trace_path": str(trace_path),
                "duration_target_seconds": float(args.duration_s),
                "elapsed_seconds": 0.0,
                "iteration_count": 0,
                "success_count": 0,
                "error_count": 0,
                "first_error": None,
                "current_stage": state.get("stage"),
                "resource_samples_path": str(resource_samples_path),
                "iterations_path": str(iterations_path),
                **({"sidecar_preflight": sidecar_preflight} if sidecar_preflight else {}),
            },
        )
        while time.monotonic() - started_monotonic < float(args.duration_s):
            if stop_requested:
                break
            iteration = len(iteration_rows) + 1
            state["iteration"] = iteration
            state["stage"] = "iteration_start"
            iteration_started = _iso_now()
            row: dict[str, Any]
            child_config = {
                "iteration": iteration,
                "trace_path": str(trace_path),
                "package_root": str(package_root),
                "context_delta": context_delta,
                "seed_spec": seed_spec,
                "rule_family": rule_family,
                "budget_vector": budget_vector,
                "closure_policy": closure_policy,
                "embodiment_mode": str(args.embodiment_mode),
                "sidecar_source": str(sidecar_source) if sidecar_source else None,
                "sidecar_manifest_source": str(sidecar_manifest_source) if sidecar_manifest_source else None,
                "expected_proof_hash": str(args.expected_proof_hash) if args.expected_proof_hash else None,
                "expected_closure_mode": str(args.expected_closure_mode) if args.expected_closure_mode else None,
                "expected_consumer_mode": EXPECTED_CONSUMER_MODE,
            }
            state["stage"] = "spawn_child_iteration"
            try:
                subprocess_result = _run_iteration_subprocess(
                    child_config=child_config,
                    raw_root=raw_root,
                    state=state,
                    stop_requested=lambda: stop_requested,
                )
            except Exception as exc:
                error_count += 1
                row = _build_iteration_error_row(
                    iteration=iteration,
                    iteration_started_at=iteration_started,
                    stage=str(state.get("stage") or ""),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    traceback_text=traceback.format_exc(),
                    post_gc_rss_bytes=None,
                    package_path=str(package_root / f"run_{iteration:06d}"),
                )
                if first_error is None:
                    first_error = {
                        "iteration": iteration,
                        "stage": row.get("stage") or state.get("stage"),
                        "error_type": str(row.get("error_type") or type(exc).__name__),
                        "error_message": str(row.get("error_message") or str(exc)),
                        "captured_at": row["captured_at"],
                    }
                exit_code = 1
                iteration_rows.append(row)
                _jsonl_append(iterations_path, row)
                break
            state["stage"] = "child_result_collect"
            child_row = subprocess_result.get("row")
            child_returncode = _safe_int(subprocess_result.get("returncode"))
            if child_returncode == 0 and isinstance(child_row, dict) and child_row.get("status") == "ok":
                row = dict(child_row)
                success_count += 1
                keep_package = _retention_flags(iteration, success_count, int(args.retain_every))
                current_path = Path(str(row.get("package_path") or package_root / f"run_{iteration:06d}"))
                row["package_path"] = str(current_path)
                if keep_package:
                    retained_packages.append(str(current_path))
                else:
                    if latest_retained_success is not None and latest_retained_success.exists():
                        _cleanup_previous_latest(latest_retained_success)
                        try:
                            retained_packages.remove(str(latest_retained_success))
                        except ValueError:
                            pass
                    latest_retained_success = current_path
                    retained_packages.append(str(current_path))
                if not keep_package:
                    if latest_retained_success is not None and current_path != latest_retained_success and current_path.exists():
                        shutil.rmtree(current_path)
                row["iteration_started_at"] = str(row.get("iteration_started_at") or iteration_started)
                row["iteration_finished_at"] = str(row.get("iteration_finished_at") or _iso_now())
                row["retained_package"] = keep_package or (latest_retained_success is not None)
                row["status"] = "ok"
            else:
                error_count += 1
                if isinstance(child_row, dict) and child_row.get("status") == "error":
                    row = dict(child_row)
                    row.setdefault("iteration", iteration)
                    row.setdefault("iteration_started_at", iteration_started)
                    row.setdefault("iteration_finished_at", _iso_now())
                    row.setdefault("post_gc_rss_bytes", None)
                    row.setdefault("package_path", str(package_root / f"run_{iteration:06d}"))
                    if child_returncode != 0:
                        row["subprocess_returncode"] = child_returncode
                else:
                    child_output = _compact_subprocess_output(
                        str(subprocess_result.get("stdout") or ""),
                        str(subprocess_result.get("stderr") or ""),
                    )
                    row = _build_iteration_error_row(
                        iteration=iteration,
                        iteration_started_at=iteration_started,
                        stage=str(state.get("stage") or ""),
                        error_type="ChildProcessError",
                        error_message=(
                            f"child iteration exited with code {child_returncode}"
                            if child_returncode != 0
                            else "child iteration did not emit a valid iteration row"
                        ),
                        traceback_text=child_output or "child process emitted no stdout/stderr",
                        post_gc_rss_bytes=None,
                        package_path=str(package_root / f"run_{iteration:06d}"),
                        subprocess_returncode=child_returncode,
                    )
                if first_error is None:
                    first_error = {
                        "iteration": iteration,
                        "stage": row.get("stage") or state.get("stage"),
                        "error_type": str(row.get("error_type") or "ChildProcessError"),
                        "error_message": str(row.get("error_message") or ""),
                        "captured_at": row["captured_at"],
                    }
                exit_code = 1
                iteration_rows.append(row)
                _jsonl_append(iterations_path, row)
                break

            iteration_rows.append(row)
            _jsonl_append(iterations_path, row)
            progress_payload = {
                "generated_at": _iso_now(),
                "pid": os.getpid(),
                "status": "running" if not stop_requested else "stopping",
                "contract_version": args.contract_version,
                "run_scope": args.run_scope,
                "proof_group": args.proof_group,
                "trace_path": str(trace_path),
                "duration_target_seconds": float(args.duration_s),
                "elapsed_seconds": round(time.monotonic() - started_monotonic, 6),
                "iteration_count": len(iteration_rows),
                "success_count": success_count,
                "error_count": error_count,
                "first_error": first_error,
                "current_stage": state.get("stage"),
                "resource_samples_path": str(resource_samples_path),
                "iterations_path": str(iterations_path),
                "latest_iteration": row,
                **({"sidecar_preflight": sidecar_preflight} if sidecar_preflight else {}),
            }
            _json_write(progress_path, progress_payload)
    finally:
        sampler.stop()

    finished_at = _iso_now()
    duration_seconds = time.monotonic() - started_monotonic
    artifact_refs = _build_parent_artifact_refs(
        package_root=package_root,
        raw_root=raw_root,
        resource_samples_path=resource_samples_path,
        iterations_path=iterations_path,
        progress_path=progress_path,
        report_path=report_path,
        environment_summary_path=environment_summary_path,
        seed_selection_path=seed_selection_path,
        sidecar_source=sidecar_source,
        sidecar_manifest_source=sidecar_manifest_source,
        retained_packages=retained_packages,
        sidecar_preflight=sidecar_preflight,
        command=_render_command(
            [sys.executable, str(Path(sys.argv[0]).expanduser().resolve()), *sys.argv[1:]]
        ),
        timestamp=finished_at,
    )
    report = _build_report(
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        target_duration_seconds=float(args.duration_s),
        input_contract=input_contract,
        environment_summary_path=environment_summary_path,
        linked_proof_groups=linked_proof_groups,
        artifact_refs=artifact_refs,
        iteration_rows=iteration_rows,
        samples=sampler.samples,
        error_count=error_count,
        first_error=first_error,
        stop_requested=stop_requested,
    )
    report["contract_version"] = args.contract_version
    report["run_scope"] = args.run_scope
    report["proof_group"] = args.proof_group
    report["artifact_refs"]["retained_packages"] = retained_packages
    if sidecar_preflight:
        report["sidecar_preflight"] = sidecar_preflight
    _json_write(report_path, report)
    _json_write(
        irrecoverable_path,
        {
            "generated_at": finished_at,
            "platform": platform.system().lower(),
            "scenario": "patent_10_4_wp06_long_duration_soak",
            "duration_seconds": round(duration_seconds, 6),
            "irrecoverable_error_detected": report["verdict"] != "pass",
            "first_error": first_error,
            "soak_report_path": str(report_path),
        },
    )
    final_progress = {
        "generated_at": finished_at,
        "pid": os.getpid(),
        "status": "completed" if report["verdict"] == "pass" else "failed",
        "duration_seconds": round(duration_seconds, 6),
        "iteration_count": len(iteration_rows),
        "success_count": success_count,
        "error_count": error_count,
        "first_error": first_error,
        "report_path": str(report_path),
        "irrecoverable_output": str(irrecoverable_path),
        "verdict": report["verdict"],
    }
    _json_write(progress_path, final_progress)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "pass" and exit_code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if "--child-iteration-config" in raw_argv or "--child-iteration-output" in raw_argv:
        return _main_child(raw_argv)
    return _main_parent(raw_argv)


if __name__ == "__main__":
    raise SystemExit(main())

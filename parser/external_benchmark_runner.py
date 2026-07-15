"""Host-side, report-only P7.6 benchmark runner."""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import platform
import resource
import statistics
import tempfile
import time
from typing import Iterator

from parser.external_benchmark_models import BenchmarkEvidence, BenchmarkSample, BenchmarkSummary, metric, sha256_identity
from parser.external_case_package_evidence import _manifest, case_package_specs, materialized_case_package
from parser.external_layered_validation import validate_case
from parser.external_package_replay_adapter import replay_case_input
from parser.external_semantic_replay import replay
from tool.run_external_package_reopen import main as package_reopen_main


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw"
REPLAY_ROOT = ROOT / "tests/python/fixtures/external_validation/replay/reports"
_RAW_NAMES = {spec.case_id: spec.raw_name for spec in case_package_specs()}


def _rss_mb() -> float | None:
    value = getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0)
    return round(value / 1024.0, 6) if value > 0 else None


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _raw_info(case_id: str) -> tuple[str, int, int | None]:
    raw = (RAW_ROOT / _RAW_NAMES[case_id]).read_bytes()
    report = __import__("json").loads((REPLAY_ROOT / f"{case_id}.json").read_bytes())
    event_count = report.get("normalized_event_count")
    return hashlib.sha256(raw).hexdigest(), len(raw), int(event_count) if isinstance(event_count, int) else None


def _package_identity(case_id: str) -> str:
    spec = next(spec for spec in case_package_specs() if spec.case_id == case_id)
    return _manifest(spec)[1].package_identity


def _environment() -> tuple[str, tuple[tuple[str, str], ...]]:
    values = (
        ("arch", platform.machine() or "unknown"),
        ("os_family", platform.system() or "unknown"),
        ("python", platform.python_version()),
        ("rss_source", "resource.RUSAGE_SELF.ru_maxrss"),
        ("timer_source", "time.perf_counter_ns"),
    )
    return sha256_identity(dict(values)), values


def _run_sample(case_id: str, mode: str, iteration: int, warmup: bool, *, package: Path | None = None) -> BenchmarkSample:
    sample_id = f"{case_id}:{mode}:{iteration:02d}"
    metrics: dict[str, object] = {}
    started = time.perf_counter_ns()
    try:
        trace_sha, trace_bytes, event_count = _raw_info(case_id)
        package_identity = _package_identity(case_id)
        environment_identity, _ = _environment()
        materialize_started = time.perf_counter_ns()
        context = nullcontext(package) if package is not None else materialized_case_package(next(spec for spec in case_package_specs() if spec.case_id == case_id))
        with context as package_root:
            materialize_elapsed = (time.perf_counter_ns() - materialize_started) / 1_000_000_000
            with tempfile.TemporaryDirectory(prefix="p7_6_benchmark_", dir="/tmp") as directory:
                output = Path(directory) / "reopen.json"
                reopen_started = time.perf_counter_ns()
                reopen_code = package_reopen_main(["--package", str(package_root), "--output", str(output), "--offline"])
                reopen_elapsed = (time.perf_counter_ns() - reopen_started) / 1_000_000_000
                replay_started = time.perf_counter_ns()
                replay(replay_case_input(case_id))
                replay_elapsed = (time.perf_counter_ns() - replay_started) / 1_000_000_000
                validation_started = time.perf_counter_ns()
                validation = validate_case(case_id)
                validation_elapsed = (time.perf_counter_ns() - validation_started) / 1_000_000_000
                metrics = {
                    "package_materialize_elapsed_s": metric("measured", round(materialize_elapsed, 6), "s"),
                    "reopen_elapsed_s": metric("measured" if reopen_code == 0 else "unavailable", round(reopen_elapsed, 6) if reopen_code == 0 else None, "s", None if reopen_code == 0 else f"exit_code_{reopen_code}"),
                    "replay_elapsed_s": metric("measured", round(replay_elapsed, 6), "s"),
                    "validation_elapsed_s": metric("measured", round(validation_elapsed, 6), "s"),
                    "total_host_elapsed_s": metric("measured", round((time.perf_counter_ns() - started) / 1_000_000_000, 6), "s"),
                    "peak_rss_mb": metric("measured", _rss_mb(), "MiB") if _rss_mb() is not None else metric("unavailable", None, "MiB", "rss_unavailable"),
                    "temp_storage_bytes": metric("measured", _directory_bytes(package_root), "bytes"),
                    "export_elapsed_s": metric("unsupported", None, "s", "production_export_not_in_scope"),
                    "read_seek_count": metric("unsupported", None, "count", "no_independent_counter"),
                    "index_build_cost_s": metric("unsupported", None, "s", "index_stage_not_in_scope"),
                }
        return BenchmarkSample(sample_id, case_id, trace_sha, trace_bytes, event_count, "measured" if event_count is not None else "unavailable", None if event_count is not None else "replay_event_count_unavailable", package_identity, f"external_trace:{case_id}", environment_identity, mode, warmup, iteration, "success", None, tuple(sorted(metrics.items())))  # type: ignore[arg-type]
    except Exception as exc:
        trace_sha, trace_bytes, event_count = _raw_info(case_id)
        package_identity = _package_identity(case_id)
        environment_identity, _ = _environment()
        failure_metrics = {
            "total_host_elapsed_s": metric("measured", round((time.perf_counter_ns() - started) / 1_000_000_000, 6), "s"),
            "package_materialize_elapsed_s": metric("unavailable", None, "s", "run_failed"),
        }
        return BenchmarkSample(sample_id, case_id, trace_sha, trace_bytes, event_count, "measured" if event_count is not None else "unavailable", None if event_count is not None else "replay_event_count_unavailable", package_identity, f"external_trace:{case_id}", environment_identity, mode, warmup, iteration, "failure", type(exc).__name__, tuple(sorted(failure_metrics.items())))


def _summaries(samples: list[BenchmarkSample]) -> tuple[BenchmarkSummary, ...]:
    rows: list[BenchmarkSummary] = []
    for case_id in sorted({sample.case_id for sample in samples}):
        for mode in ("process_cold", "same_process_warm"):
            selected = [sample for sample in samples if sample.case_id == case_id and sample.mode == mode and not sample.warmup]
            successful = [sample for sample in selected if sample.status == "success"]
            names = sorted({name for sample in successful for name, item in sample.metrics if item.status == "measured" and item.value is not None})
            metrics: list[tuple[str, dict[str, int | float]]] = []
            for name in names:
                values = [float(dict(sample.metrics)[name].value) for sample in successful if name in dict(sample.metrics) and dict(sample.metrics)[name].status == "measured" and dict(sample.metrics)[name].value is not None]
                if values:
                    median = statistics.median(values)
                    metrics.append((name, {"sample_count": len(values), "median": round(median, 6), "min": round(min(values), 6), "max": round(max(values), 6), "mad": round(statistics.median(abs(value - median) for value in values), 6)}))
            rows.append(BenchmarkSummary(case_id, mode, len(selected), len(successful), len(selected) - len(successful), tuple(metrics)))
    return tuple(rows)


def run_benchmark(*, repeats: int = 5, warmups: int = 1, cases: tuple[str, ...] | None = None) -> BenchmarkEvidence:
    if repeats < 1 or warmups < 0:
        raise ValueError("repeats must be positive and warmups non-negative")
    selected = cases or tuple(_RAW_NAMES)
    if any(case not in _RAW_NAMES for case in selected):
        raise ValueError("unsupported benchmark case")
    environment_identity, environment = _environment()
    samples: list[BenchmarkSample] = []
    for case_id in selected:
        spec = next(spec for spec in case_package_specs() if spec.case_id == case_id)
        for mode in ("process_cold", "same_process_warm"):
            package_context: Iterator[Path] | None = None
            if mode == "same_process_warm":
                package_context = materialized_case_package(spec)
                package = package_context.__enter__()
            else:
                package = None
            try:
                for iteration in range(warmups):
                    samples.append(_run_sample(case_id, mode, iteration, True, package=package))
                for iteration in range(repeats):
                    samples.append(_run_sample(case_id, mode, warmups + iteration, False, package=package))
            finally:
                if package_context is not None:
                    package_context.__exit__(None, None, None)
    ordered = sorted(samples, key=lambda item: item.sample_id)
    return BenchmarkEvidence(environment_identity, environment, tuple(ordered), _summaries(ordered), {"status": "not_evaluated", "reason": "missing_board_or_source_identical_firmware"})

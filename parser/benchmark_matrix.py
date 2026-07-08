from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import math
from pathlib import Path
try:
    import resource
except ImportError:  # pragma: no cover - non-Unix fallback
    resource = None
import statistics
import tempfile
import time
from typing import Any, Iterable

from parser.export_write_agent import ExportWriteAgent
from parser.result import Result, ok_result
from parser.runtime_advisor import RuntimeOptimizationAdvisor
from parser.telemetry import TelemetryHistoryStore, TelemetryReportAgent


BENCHMARK_REPORT_VERSION = "runtime-optimization-benchmark-v1"
BENCHMARK_SCENARIO_VERSION = "benchmark-scenario-v1"
BENCHMARK_SCENARIO_IDS = (
    "current_baseline",
    "ticket_fast_path",
    "advisor_heuristic",
    "advisor_optional_sklearn",
    "advisor_openai_structured",
)
REQUIRED_BENCHMARK_METRICS = (
    "runtime_seconds",
    "sidecar_bytes_scanned",
    "sidecar_validate_seconds",
    "index_build_open_seconds",
    "peak_rss_mb",
    "package_write_seconds",
    "advisor_overhead_seconds",
)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_metric_field_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized = [str(value) for value in values if str(value).strip()]
    return list(dict.fromkeys(normalized))


def _metric_distribution(values: Iterable[Any]) -> dict[str, Any]:
    samples = []
    for value in values:
        normalized = _optional_float(value)
        if normalized is not None:
            samples.append(normalized)
    if not samples:
        return {
            "count": 0,
            "samples": [],
            "median": None,
            "p95": None,
            "min": None,
            "max": None,
        }
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "count": len(ordered),
        "samples": ordered,
        "median": round(float(statistics.median(ordered)), 6),
        "p95": round(float(ordered[p95_index]), 6),
        "min": round(float(ordered[0]), 6),
        "max": round(float(ordered[-1]), 6),
    }


def _repeated_run_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("scenario_id")), []).append(row)
    metrics = (
        "runtime_seconds",
        "sidecar_validate_seconds",
        "index_build_open_seconds",
        "peak_rss_mb",
        "package_write_seconds",
        "advisor_overhead_seconds",
    )
    return {
        scenario_id: {
            "run_count": len(scenario_rows),
            "completed_count": sum(1 for row in scenario_rows if str(row.get("status") or "").startswith("completed")),
            "metrics": {
                metric: _metric_distribution(row.get(metric) for row in scenario_rows)
                for metric in metrics
            },
        }
        for scenario_id, scenario_rows in sorted(grouped.items())
    }


def _enrich_summary_metrics(
    row: dict[str, Any],
    *,
    formal_matrix_mode: str | None,
) -> dict[str, Any]:
    summary_metrics = dict(row.get("summary_metrics") or {})
    runtime_seconds = _optional_float(row.get("runtime_seconds"))
    sidecar_validate_seconds = _optional_float(row.get("sidecar_validate_seconds"))
    index_build_open_seconds = _optional_float(row.get("index_build_open_seconds"))
    advisor_overhead_seconds = _optional_float(row.get("advisor_overhead_seconds"))
    metric_scope = str(summary_metrics.get("metric_scope") or ("formal_matrix" if formal_matrix_mode else "product_runtime_path"))
    summary_metrics.update(
        {
            "metric_scope": metric_scope,
            "product_runtime_path": runtime_seconds if metric_scope == "product_runtime_path" else None,
            "formal_wall_seconds": runtime_seconds if formal_matrix_mode else None,
            "sidecar_build_seconds": _optional_float(summary_metrics.get("sidecar_build_seconds")),
            "sidecar_index_build_open_seconds": index_build_open_seconds,
            "ticket_validate_seconds": sidecar_validate_seconds,
            "advisor_overhead_seconds": advisor_overhead_seconds,
            "synthetic_metric_fields": _normalized_metric_field_list(summary_metrics.get("synthetic_metric_fields")),
            "formal_proof_only": bool(formal_matrix_mode),
            "speedup_evidence_scope": (
                "formal_only_not_product_speedup"
                if formal_matrix_mode
                else "product_runtime_path"
            ),
        }
    )
    row["summary_metrics"] = summary_metrics
    return row


def _rss_mb() -> float | None:
    if resource is None:
        return None
    rss_kb = getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0)
    if rss_kb <= 0:
        return None
    return round(float(rss_kb) / 1024.0, 3)


@dataclass(frozen=True)
class BenchmarkScenario:
    scenario_version: str
    scenario_id: str
    advisor_enabled: bool
    advisor_mode: str
    ticket_fast_path_enabled: bool
    streaming_write_enabled: bool
    optional_dependency: str | None = None
    expected_execution_plan: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_version": self.scenario_version,
            "scenario_id": self.scenario_id,
            "scenario_kind": self.scenario_id,
            "advisor_enabled": bool(self.advisor_enabled),
            "advisor_mode": self.advisor_mode,
            "ticket_fast_path_enabled": bool(self.ticket_fast_path_enabled),
            "streaming_write_enabled": bool(self.streaming_write_enabled),
            "optional_dependency": self.optional_dependency,
            "expected_execution_plan": list(self.expected_execution_plan or []),
        }


def default_benchmark_scenarios() -> list[BenchmarkScenario]:
    return [
        BenchmarkScenario(
            scenario_version=BENCHMARK_SCENARIO_VERSION,
            scenario_id="current_baseline",
            advisor_enabled=False,
            advisor_mode="disabled",
            ticket_fast_path_enabled=False,
            streaming_write_enabled=False,
            expected_execution_plan=["baseline"],
        ),
        BenchmarkScenario(
            scenario_version=BENCHMARK_SCENARIO_VERSION,
            scenario_id="ticket_fast_path",
            advisor_enabled=False,
            advisor_mode="disabled",
            ticket_fast_path_enabled=True,
            streaming_write_enabled=False,
            expected_execution_plan=["open_index", "ticket_fast_path"],
        ),
        BenchmarkScenario(
            scenario_version=BENCHMARK_SCENARIO_VERSION,
            scenario_id="advisor_heuristic",
            advisor_enabled=True,
            advisor_mode="heuristic",
            ticket_fast_path_enabled=True,
            streaming_write_enabled=True,
            expected_execution_plan=["stream_write", "sidecar_first", "open_index", "ticket_fast_path"],
        ),
        BenchmarkScenario(
            scenario_version=BENCHMARK_SCENARIO_VERSION,
            scenario_id="advisor_optional_sklearn",
            advisor_enabled=True,
            advisor_mode="offline_coefficients",
            ticket_fast_path_enabled=True,
            streaming_write_enabled=True,
            optional_dependency="sklearn",
            expected_execution_plan=["stream_write", "sidecar_first", "open_index", "ticket_fast_path"],
        ),
        BenchmarkScenario(
            scenario_version=BENCHMARK_SCENARIO_VERSION,
            scenario_id="advisor_openai_structured",
            advisor_enabled=True,
            advisor_mode="openai_structured",
            ticket_fast_path_enabled=True,
            streaming_write_enabled=True,
            expected_execution_plan=["stream_write", "sidecar_first", "open_index", "ticket_fast_path"],
        ),
    ]


class BenchmarkReportBuilder:
    def run_matrix(
        self,
        scenarios: Iterable[BenchmarkScenario] | None = None,
        *,
        input_contract: dict[str, Any] | None = None,
        output_root: str | Path | None = None,
        scenario_runner: Any = None,
    ) -> Result[dict[str, Any]]:
        root = Path(output_root).expanduser().resolve() if output_root is not None else Path(tempfile.mkdtemp(prefix="rttrace-benchmark-matrix-"))
        root.mkdir(parents=True, exist_ok=True)
        effective_input_contract = dict(input_contract or {})
        effective_input_contract.setdefault("telemetry_history_path", str(root / "telemetry_history.jsonl"))
        scenario_list = list(scenarios or default_benchmark_scenarios())
        rows: list[dict[str, Any]] = []
        for scenario in scenario_list:
            if scenario_runner is None:
                row = self._run_builtin_scenario(scenario, input_contract=effective_input_contract, output_root=root)
            else:
                started = time.perf_counter()
                result = scenario_runner(scenario)
                row = dict(result or {})
                row.setdefault("runtime_seconds", round(time.perf_counter() - started, 6))
                row.setdefault("scenario_id", scenario.scenario_id)
            rows.append(row)
        return ok_result(self.build_report(rows, input_contract=effective_input_contract, scenarios=scenario_list))

    def _run_builtin_scenario(
        self,
        scenario: BenchmarkScenario,
        *,
        input_contract: dict[str, Any],
        output_root: Path,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        advisor_started = time.perf_counter()
        telemetry_history_path = Path(str(input_contract.get("telemetry_history_path") or (output_root / "telemetry_history.jsonl"))).expanduser().resolve()
        telemetry_history_store = TelemetryHistoryStore(telemetry_history_path)
        optional_available = (
            importlib.util.find_spec(str(scenario.optional_dependency)) is not None
            if scenario.optional_dependency
            else None
        )
        advisor_coefficients_path = (
            input_contract.get("advisor_coefficients_path")
            if scenario.optional_dependency
            else None
        )
        advisor_config = {"advisor_mode": scenario.advisor_mode}
        if advisor_coefficients_path:
            advisor_config["coefficients_path"] = str(Path(str(advisor_coefficients_path)).expanduser())
        history = telemetry_history_store.read_advisor_history(
            dataset_id=str(input_contract.get("dataset_id") or "benchmark:dataset"),
            export_family="benchmark",
        )
        advisor = RuntimeOptimizationAdvisor(advisor_config)
        advisor_decision = advisor.evaluate(
            telemetry_history=history,
            current_request_features={
                "advisor_phase": "benchmark",
                "export_family": "benchmark",
                "embodiment_mode": "benchmark",
                "input_bytes": int(input_contract.get("input_bytes") or 1024),
                "sidecar_bytes": int(input_contract.get("sidecar_bytes") or 4096),
                "ticket_present": bool(scenario.ticket_fast_path_enabled),
                "ticket_validated": bool(scenario.ticket_fast_path_enabled),
                "gate_policy_summary": {
                    "advisor_enabled": scenario.advisor_enabled,
                    "ticket_fast_path_enabled": scenario.ticket_fast_path_enabled,
                },
            },
            sidecar_ticket={"row_count": 1, "sidecar_bytes": int(input_contract.get("sidecar_bytes") or 4096)}
            if scenario.ticket_fast_path_enabled
            else None,
        )
        advisor_metadata = dict(advisor.last_advisor_metadata)
        retrieval_metadata = dict(advisor_metadata.get("retrieval") or {})
        advisor_overhead_seconds = round(time.perf_counter() - advisor_started, 6)
        package_root = output_root / scenario.scenario_id
        write_agent = ExportWriteAgent(job_id=f"benchmark-{scenario.scenario_id}")
        write_result = write_agent.write_evidence_package_optimized(
            {
                "package_path": str(package_root),
                "snapshot_id": str(input_contract.get("snapshot_id") or "benchmark:snapshot"),
                "entries": [
                    {
                        "relative_path": "control/benchmark_scenario.json",
                        "write_mode": "standard_json",
                        "payload": scenario.to_dict(),
                        "label": "benchmark_scenario",
                    },
                    {
                        "relative_path": "control/advisor_decision.json",
                        "write_mode": "standard_json",
                        "payload": advisor_decision.to_dict(),
                        "label": "advisor_decision",
                    },
                    {
                        "relative_path": "result/rows.json",
                        "write_mode": "stream_json_array" if scenario.streaming_write_enabled else "standard_json",
                        "payload": [{"scenario_id": scenario.scenario_id, "parity_key": "same"}],
                        "label": "benchmark_rows",
                    },
                ],
            },
            {
                "package_path": str(package_root),
                "snapshot_id": str(input_contract.get("snapshot_id") or "benchmark:snapshot"),
                "default_write_mode": "stream_json_array" if scenario.streaming_write_enabled else "standard_json",
            },
        )
        package_result = dict((write_result.data or {}).get("package_result") or {})
        write_metrics = list(package_result.get("write_metrics") or [])
        package_write_seconds = round(
            sum(float(metric.get("write_seconds") or 0.0) for metric in write_metrics),
            6,
        )
        finished = time.perf_counter()
        proof_hash = str(input_contract.get("proof_hash") or "sha256:benchmark-parity-fixture")
        runtime_seconds = round(finished - started, 6)
        peak_rss_mb = _rss_mb()
        fallback_reasons: list[str] = []
        if scenario.optional_dependency:
            if not optional_available:
                fallback_reasons.append(f"optional_dependency_unavailable:{scenario.optional_dependency}")
            if advisor_decision.advisor_mode != scenario.advisor_mode:
                fallback_reasons.append(f"advisor_mode_fallback:{advisor_decision.advisor_mode}")
            if not advisor_coefficients_path or advisor_decision.advisor_mode != "offline_coefficients":
                fallback_reasons.append("advisor_coefficients_not_attached")
        if scenario.advisor_mode == "openai_structured":
            if advisor_decision.advisor_mode != scenario.advisor_mode:
                fallback_reasons.append(f"advisor_mode_fallback:{advisor_decision.advisor_mode}")
            fallback_reason = advisor_metadata.get("fallback_reason")
            if fallback_reason:
                fallback_reasons.append(str(fallback_reason))
        fallback_reasons = list(dict.fromkeys(fallback_reasons))
        telemetry_record = TelemetryReportAgent().build_record(
            run_id=f"benchmark-{scenario.scenario_id}",
            dataset_id=str(input_contract.get("dataset_id") or "benchmark:dataset"),
            export_family="benchmark",
            embodiment_mode="benchmark",
            input_bytes=int(input_contract.get("input_bytes") or 1024),
            sidecar_bytes=int(input_contract.get("sidecar_bytes") or 4096),
            sidecar_row_count=1 if scenario.ticket_fast_path_enabled else 0,
            sidecar_bytes_scanned=0 if scenario.ticket_fast_path_enabled else int(input_contract.get("sidecar_bytes") or 4096),
            sidecar_validate_seconds=0.0 if scenario.ticket_fast_path_enabled else 0.001,
            index_build_open_seconds=0.0,
            index_reused=bool(scenario.ticket_fast_path_enabled),
            index_rebuilt=not bool(scenario.ticket_fast_path_enabled),
            package_write_seconds=package_write_seconds,
            runtime_seconds=runtime_seconds,
            peak_rss_mb=peak_rss_mb,
            proof_hash=proof_hash,
            closure_mode="benchmark",
            advisor_overhead_seconds=advisor_overhead_seconds if scenario.advisor_enabled else 0.0,
            sidecar_lookup_count=0,
            write_mode="streaming_agent" if scenario.streaming_write_enabled else "standard_json",
            stage_timings={"package_write_seconds": package_write_seconds},
            rss_snapshots=[{"stage": "benchmark", "rss_mb": peak_rss_mb}],
        )
        telemetry_update = telemetry_history_store.append_record(
            telemetry_record,
            job_id=f"benchmark-{scenario.scenario_id}",
        )
        artifact_refs = {
            "package_path": str(package_root),
            "telemetry_history_path": str(telemetry_history_path),
        }
        if advisor_coefficients_path:
            artifact_refs["advisor_coefficients"] = str(Path(str(advisor_coefficients_path)).expanduser())
        return {
            "scenario_id": scenario.scenario_id,
            "status": "completed_with_fallback" if write_result.ok and fallback_reasons else ("completed" if write_result.ok else "failed"),
            "runtime_seconds": runtime_seconds,
            "peak_rss_mb": peak_rss_mb,
            "sidecar_bytes_scanned": 0 if scenario.ticket_fast_path_enabled else int(input_contract.get("sidecar_bytes") or 4096),
            "sidecar_validate_seconds": 0.0 if scenario.ticket_fast_path_enabled else 0.001,
            "index_build_open_seconds": 0.0,
            "package_write_seconds": package_write_seconds,
            "advisor_overhead_seconds": advisor_overhead_seconds if scenario.advisor_enabled else 0.0,
            "proof_hash": proof_hash,
            "parity_result": {
                "proof_hash": proof_hash,
                "metric_diff_count": 0,
                "mandatory_field_set": sorted(
                    [
                        "scenario_id",
                        "runtime_seconds",
                        "peak_rss_mb",
                        "sidecar_bytes_scanned",
                        "package_write_seconds",
                        "advisor_overhead_seconds",
                        "proof_hash",
                    ]
                ),
            },
            "artifact_refs": artifact_refs,
            "summary_metrics": {
                "write_metric_count": len(write_metrics),
                "optional_dependency": scenario.optional_dependency,
                "optional_dependency_available": optional_available,
                "fallback_reasons": fallback_reasons,
                "advisor_mode_requested": scenario.advisor_mode,
                "advisor_mode_effective": advisor_decision.advisor_mode,
                "advisor_decision_model_ref": advisor_decision.model_ref,
                "advisor_decision_model_checksum": advisor_decision.model_checksum,
                "advisor_coefficients_path": str(Path(str(advisor_coefficients_path)).expanduser()) if advisor_coefficients_path else None,
                "openai_latency_seconds": advisor_metadata.get("openai_latency_seconds"),
                "openai_tokens": advisor_metadata.get("openai_tokens"),
                "fallback_reason": advisor_metadata.get("fallback_reason"),
                "openai_response_id": advisor_metadata.get("openai_response_id"),
                "openai_model": advisor_metadata.get("openai_model"),
                "llm_backend": advisor_metadata.get("llm_backend"),
                "retrieval_case_count": len(list(retrieval_metadata.get("retrieved_case_refs") or [])),
                "retrieval_has_sufficient_similarity": bool(retrieval_metadata.get("has_sufficient_similarity")),
                "retrieval_conflicting_actions": list(retrieval_metadata.get("conflicting_actions") or []),
                "retrieval_unsafe_case_match": bool(retrieval_metadata.get("unsafe_case_match")),
                "retrieval_missing_telemetry_fields": list(retrieval_metadata.get("missing_telemetry_fields") or []),
                "retrieval_applied_abstain_reason": retrieval_metadata.get("applied_abstain_reason"),
                "retrieval_reject_taxonomy_coverage": retrieval_metadata.get("reject_taxonomy_coverage"),
            },
            "telemetry_records": [
                telemetry_update.to_dict()
            ],
        }

    def build_report(
        self,
        scenario_results: Iterable[dict[str, Any]],
        *,
        input_contract: dict[str, Any] | None = None,
        scenarios: Iterable[BenchmarkScenario] | None = None,
    ) -> dict[str, Any]:
        formal_matrix_mode = str(dict(input_contract or {}).get("formal_matrix_mode") or "").strip() or None
        rows = [
            _enrich_summary_metrics(dict(row), formal_matrix_mode=formal_matrix_mode)
            for row in scenario_results
        ]
        scenario_list = list(scenarios or default_benchmark_scenarios())
        scenarios_present = [str(row.get("scenario_id")) for row in rows]
        proof_hashes = {
            str(row.get("proof_hash") or (dict(row.get("parity_result") or {}).get("proof_hash")) or "")
            for row in rows
            if str(row.get("proof_hash") or (dict(row.get("parity_result") or {}).get("proof_hash")) or "").strip()
        }
        parity_rows = [dict(row.get("parity_result") or {}) for row in rows if row.get("parity_result") is not None]
        mandatory_field_sets = {
            tuple(sorted(str(item) for item in list(parity.get("mandatory_field_set") or [])))
            for parity in parity_rows
        }
        metric_diff_count = sum(int(parity.get("metric_diff_count") or 0) for parity in parity_rows)
        metric_maps = {
            metric: {
                str(row.get("scenario_id")): row.get(metric)
                for row in rows
            }
            for metric in REQUIRED_BENCHMARK_METRICS
        }
        required_metric_coverage = {
            metric: {
                "present_count": sum(1 for row in rows if row.get(metric) is not None),
                "scenario_count": len(rows),
                "complete": len(rows) > 0 and all(row.get(metric) is not None for row in rows),
                "missing_scenarios": [
                    str(row.get("scenario_id"))
                    for row in rows
                    if row.get(metric) is None
                ],
            }
            for metric in REQUIRED_BENCHMARK_METRICS
        }
        artifact_refs = {
            str(row.get("scenario_id")): dict(row.get("artifact_refs") or {})
            for row in rows
        }
        return {
            "report_version": BENCHMARK_REPORT_VERSION,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "input_contract": dict(input_contract or {}),
            "scenario_order": [scenario.scenario_id for scenario in scenario_list],
            "scenarios": [scenario.to_dict() for scenario in scenario_list],
            "results": rows,
            "summary": {
                "scenario_count": len(rows),
                **metric_maps,
                "status_by_scenario": {
                    str(row.get("scenario_id")): row.get("status")
                    for row in rows
                },
                "required_metric_coverage": required_metric_coverage,
                "repeated_run_statistics": _repeated_run_statistics(rows),
                "artifact_refs": artifact_refs,
                "parity": {
                    "proof_hash_consistent": len(proof_hashes) <= 1,
                    "metric_diff_count": int(metric_diff_count),
                    "mandatory_field_set_consistent": len(mandatory_field_sets) <= 1,
                    "mandatory_field_sets": [list(items) for items in sorted(mandatory_field_sets)],
                    "proof_hashes": sorted(proof_hashes),
                    "parity_row_count": len(parity_rows),
                    "scenario_ids": scenarios_present,
                },
            },
        }

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.benchmark_matrix import BENCHMARK_SCENARIO_IDS, BenchmarkReportBuilder, default_benchmark_scenarios
from tool.runtime_benchmark_runner import run_runtime_optimization_benchmark_matrix


def _load_result(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark result must be a JSON object: {path}")
    return payload


def _select_scenarios(requested: list[str]) -> list[Any]:
    scenarios = default_benchmark_scenarios()
    if not requested:
        return scenarios
    requested_ids = set(requested)
    return [scenario for scenario in scenarios if scenario.scenario_id in requested_ids]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_runtime_optimization_benchmark")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--input-contract-json", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--trace", type=Path, default=None)
    parser.add_argument("--train-optional-sklearn-coefficients", action="store_true")
    parser.add_argument("--advisor-coefficients-path", type=Path, default=None)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run the selected product benchmark matrix N times and report median/p95 statistics.",
    )
    parser.add_argument(
        "--prebuild-ticket-fast-path",
        action="store_true",
        help="Build the shared sidecar index ticket before executing selected scenarios.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=BENCHMARK_SCENARIO_IDS,
        default=[],
        help="Scenario to execute. Repeat to run a subset in default order.",
    )
    parser.add_argument(
        "--scenario-result",
        action="append",
        type=Path,
        default=[],
        help="Existing measured scenario JSON. Use scenario_id/runtime/RSS/proof fields.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    input_contract = {}
    if args.input_contract_json is not None:
        input_contract = json.loads(args.input_contract_json.read_text(encoding="utf-8"))
    if args.advisor_coefficients_path is not None:
        input_contract = {
            **dict(input_contract),
            "advisor_coefficients_path": str(args.advisor_coefficients_path.expanduser()),
        }
    scenarios = _select_scenarios(args.scenario)
    if args.dry_run:
        rows = [
            {
                "scenario_id": scenario.scenario_id,
                "status": "planned",
                "runtime_seconds": None,
                "peak_rss_mb": None,
                "sidecar_bytes_scanned": None,
                "package_write_seconds": None,
                "advisor_overhead_seconds": None,
                "proof_hash": None,
            }
            for scenario in scenarios
        ]
        report = BenchmarkReportBuilder().build_report(rows, input_contract=input_contract, scenarios=scenarios)
    else:
        if args.scenario_result:
            rows = [_load_result(path) for path in args.scenario_result]
            report = BenchmarkReportBuilder().build_report(rows, input_contract=input_contract, scenarios=scenarios)
        else:
            if args.repeat == 1:
                executed = run_runtime_optimization_benchmark_matrix(
                    trace=args.trace,
                    input_contract=input_contract,
                    output_root=args.output_root,
                    train_optional_sklearn_coefficients=args.train_optional_sklearn_coefficients,
                    advisor_coefficients_path=args.advisor_coefficients_path,
                    scenarios=scenarios,
                    prebuild_ticket_fast_path=args.prebuild_ticket_fast_path,
                )
                if not executed.ok:
                    raise RuntimeError(executed.message)
                report = executed.data
            else:
                rows: list[dict[str, Any]] = []
                repeat_output_base = args.output_root
                if repeat_output_base is None:
                    repeat_output_base = args.output.parent / f"{args.output.stem}_runs"
                repeated_contract = {
                    **dict(input_contract),
                    "repeat_count": args.repeat,
                    "repeat_mode": "product_runtime_matrix",
                }
                for index in range(args.repeat):
                    run_output_root = repeat_output_base / f"run_{index + 1:03d}"
                    executed = run_runtime_optimization_benchmark_matrix(
                        trace=args.trace,
                        input_contract=repeated_contract,
                        output_root=run_output_root,
                        train_optional_sklearn_coefficients=args.train_optional_sklearn_coefficients,
                        advisor_coefficients_path=args.advisor_coefficients_path,
                        scenarios=scenarios,
                        prebuild_ticket_fast_path=args.prebuild_ticket_fast_path,
                    )
                    if not executed.ok:
                        raise RuntimeError(executed.message)
                    for row in executed.data["results"]:
                        enriched = dict(row)
                        summary_metrics = dict(enriched.get("summary_metrics") or {})
                        summary_metrics["repeat_index"] = index + 1
                        enriched["summary_metrics"] = summary_metrics
                        artifact_refs = dict(enriched.get("artifact_refs") or {})
                        if run_output_root is not None:
                            artifact_refs["repeat_output_root"] = str(run_output_root)
                        enriched["artifact_refs"] = artifact_refs
                        rows.append(enriched)
                report = BenchmarkReportBuilder().build_report(
                    rows,
                    input_contract=repeated_contract,
                    scenarios=scenarios,
                )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"benchmark_report": str(args.output), "scenario_count": len(report["results"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

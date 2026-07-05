from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.parser_process_agent import ParserProcessAgent
from spec.schema_loader import load_specs
from spec.schema_validator import validate_schema


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _jsonl_append(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _rss_current_mb() -> float | None:
    try:
        pages = int(Path("/proc/self/statm").read_text(encoding="utf-8").split()[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return round(pages * page_size / (1024 * 1024), 6)
    except Exception:
        return None


def _bool_arg(raw: str | int | bool) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _schema_status(result_data: dict[str, Any]) -> dict[str, Any]:
    specs = load_specs()
    artifact = result_data.get("parser_process_artifact")
    contract = result_data.get("agent_contract")
    child_contract = result_data.get("child_agent_contract")
    artifact_reason = validate_schema(specs["parser_process_artifact"], artifact) if isinstance(artifact, dict) else "missing parser_process_artifact"
    contract_reason = validate_schema(specs["agent_job_contract"], contract) if isinstance(contract, dict) else "missing agent_contract"
    child_reason = None
    if isinstance(child_contract, dict) and child_contract:
        child_reason = validate_schema(specs["agent_job_contract"], child_contract)
    return {
        "parser_process_artifact_ok": artifact_reason is None,
        "parser_process_artifact_error": artifact_reason,
        "agent_contract_ok": contract_reason is None,
        "agent_contract_error": contract_reason,
        "child_agent_contract_ok": child_reason is None,
        "child_agent_contract_error": child_reason,
    }


def _iteration_record(
    *,
    index: int,
    result: Any,
    artifact_dir: Path,
    progress_path: Path,
    rss_before: float | None,
    rss_after: float | None,
    elapsed_s: float,
) -> dict[str, Any]:
    data = dict(result.data or {})
    artifact = dict(data.get("parser_process_artifact") or {})
    contract = dict(data.get("agent_contract") or {})
    artifact_handle = dict(data.get("artifact_handle") or artifact.get("artifact_handle") or {})
    result_path = Path(str(data.get("result_path") or artifact.get("result_path") or artifact_dir / "parse_rebuild_result.json"))
    child_peak = artifact.get("peak_rss_mb")
    return {
        "iteration": index,
        "ok": bool(result.ok),
        "code": result.code,
        "message": result.message,
        "status": contract.get("status"),
        "agent_contract_status": contract.get("status"),
        "child_peak_rss_mb": child_peak,
        "parent_rss_before_mb": rss_before,
        "parent_rss_after_mb": rss_after,
        "parent_rss_delta_mb": None if rss_before is None or rss_after is None else round(rss_after - rss_before, 6),
        "elapsed_seconds": round(elapsed_s, 6),
        "artifact_metadata": artifact,
        "schema_validation": _schema_status(data),
        "artifact_dir": str(artifact_dir),
        "artifact_path": str(artifact_handle.get("path") or artifact_dir / "parse_rebuild_artifact.pickle"),
        "artifact_exists_after_iteration": Path(str(artifact_handle.get("path") or artifact_dir / "parse_rebuild_artifact.pickle")).exists(),
        "result_path": str(result_path),
        "result_path_exists": result_path.exists(),
        "progress_path": str(progress_path),
        "progress_path_exists": progress_path.exists(),
    }


def _run_exception_probe(
    *,
    name: str,
    trace: Path,
    output_root: Path,
    policy: dict[str, Any] | None = None,
    timeout_s: float | None = None,
    cancel_before_start: bool = False,
) -> dict[str, Any]:
    probe_root = output_root / "exception_probes" / name
    artifact_dir = probe_root / "artifact"
    progress_path = probe_root / "progress.jsonl"
    cancel_path = probe_root / "cancel.flag"
    if cancel_before_start:
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("cancel\n", encoding="utf-8")
    merged_policy = {
        "artifact_dir": str(artifact_dir),
        "progress_path": str(progress_path),
        "materialize_event_stream": False,
        "load_artifact": False,
        "retain_artifact": False,
        "index_build_mode": "minimal",
    }
    merged_policy.update(dict(policy or {}))
    started = time.perf_counter()
    result = ParserProcessAgent(job_id=f"p7-{name}").parse_rebuild(
        trace,
        artifact_dir=artifact_dir,
        timeout_s=timeout_s,
        cancel_path=cancel_path if cancel_before_start else None,
        artifact_policy=merged_policy,
    )
    elapsed = round(time.perf_counter() - started, 6)
    data = dict(result.data or {})
    artifact = dict(data.get("parser_process_artifact") or {})
    contract = dict(data.get("agent_contract") or {})
    artifact_path = Path(str((data.get("artifact_handle") or artifact.get("artifact_handle") or {}).get("path") or artifact_dir / "parse_rebuild_artifact.pickle"))
    return {
        "name": name,
        "ok": bool(result.ok),
        "code": result.code,
        "message": result.message,
        "elapsed_seconds": elapsed,
        "agent_contract_status": contract.get("status"),
        "schema_validation": _schema_status(data),
        "artifact_metadata": artifact,
        "artifact_path": str(artifact_path),
        "artifact_exists": artifact_path.exists(),
        "result_path": str(data.get("result_path") or artifact.get("result_path") or artifact_dir / "parse_rebuild_result.json"),
        "progress_path": str(progress_path),
    }


def run_soak(args: argparse.Namespace) -> dict[str, Any]:
    trace = Path(args.trace).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    iterations_path = output_root / "iterations.jsonl"
    resource_samples_path = output_root / "resource_samples.jsonl"
    for path in (iterations_path, resource_samples_path):
        path.unlink(missing_ok=True)

    duration_s = float(args.duration_s)
    min_iterations = int(args.min_iterations)
    started = time.monotonic()
    records: list[dict[str, Any]] = []
    index = 0
    while index < min_iterations or (time.monotonic() - started) < duration_s:
        index += 1
        iteration_root = output_root / "iterations" / f"{index:04d}"
        artifact_dir = iteration_root / "artifact"
        progress_path = iteration_root / "progress.jsonl"
        rss_before = _rss_current_mb()
        iter_started = time.perf_counter()
        result = ParserProcessAgent(job_id=f"p7-long-soak-{index}").parse_rebuild(
            trace,
            artifact_dir=artifact_dir,
            timeout_s=float(args.timeout_s),
            materialize_event_stream=_bool_arg(args.materialize_event_stream),
            load_artifact=_bool_arg(args.load_artifact),
            artifact_policy={
                "artifact_dir": str(artifact_dir),
                "progress_path": str(progress_path),
                "materialize_event_stream": _bool_arg(args.materialize_event_stream),
                "load_artifact": _bool_arg(args.load_artifact),
                "retain_artifact": _bool_arg(args.retain_artifact),
                "index_build_mode": str(args.index_build_mode),
            },
        )
        elapsed_s = time.perf_counter() - iter_started
        rss_after = _rss_current_mb()
        record = _iteration_record(
            index=index,
            result=result,
            artifact_dir=artifact_dir,
            progress_path=progress_path,
            rss_before=rss_before,
            rss_after=rss_after,
            elapsed_s=elapsed_s,
        )
        records.append(record)
        _jsonl_append(iterations_path, record)
        _jsonl_append(resource_samples_path, {
            "iteration": index,
            "sample": "after_iteration",
            "parent_rss_mb": rss_after,
            "child_peak_rss_mb": record.get("child_peak_rss_mb"),
            "elapsed_since_start_s": round(time.monotonic() - started, 6),
        })

    successful = [record for record in records if record.get("ok")]
    failed = [record for record in records if not record.get("ok")]
    rss_values = [record.get("parent_rss_after_mb") for record in records if record.get("parent_rss_after_mb") is not None]
    rss_head = rss_values[0] if rss_values else None
    rss_tail = rss_values[-1] if rss_values else None
    rss_growth = None if rss_head is None or rss_tail is None else round(float(rss_tail) - float(rss_head), 6)
    schema_ok = all(
        record["schema_validation"]["parser_process_artifact_ok"]
        and record["schema_validation"]["agent_contract_ok"]
        and record["schema_validation"]["child_agent_contract_ok"]
        for record in records
    )

    missing_trace = output_root / "exception_probes" / "child_failure_missing_trace" / "missing.trace"
    exception_probes = [
        _run_exception_probe(
            name="timeout",
            trace=trace,
            output_root=output_root,
            timeout_s=0.0001,
        ),
        _run_exception_probe(
            name="cancel",
            trace=trace,
            output_root=output_root,
            cancel_before_start=True,
        ),
        _run_exception_probe(
            name="unsupported_artifact_format",
            trace=trace,
            output_root=output_root,
            policy={"artifact_format": "json"},
        ),
        _run_exception_probe(
            name="child_failure_missing_trace",
            trace=missing_trace,
            output_root=output_root,
            timeout_s=max(1.0, min(float(args.timeout_s), 30.0)),
        ),
    ]
    exception_probe_path = output_root / "exception_probes.json"
    _json_dump(exception_probe_path, {"probes": exception_probes})
    expected_probe_codes = {
        "timeout": "ERR-AGENT_TIMEOUT",
        "cancel": "ERR-AGENT_CANCELLED",
        "unsupported_artifact_format": "ERR-PARSER_PROCESS_FAILED",
        "child_failure_missing_trace": "INVALID_ARG",
    }

    checks = [
        {
            "name": "successful_iterations.min_iterations",
            "ok": len(successful) >= min_iterations,
            "message": f"successful_iterations={len(successful)}, min_iterations={min_iterations}",
        },
        {
            "name": "failed_iterations.zero",
            "ok": len(failed) == 0,
            "message": f"failed_iterations={len(failed)}",
        },
        {
            "name": "parent_rss_growth.within_limit",
            "ok": bool(rss_growth is None or rss_growth <= float(args.max_parent_rss_growth_mb)),
            "message": f"rss_growth_mb={rss_growth}, max_parent_rss_growth_mb={args.max_parent_rss_growth_mb}",
        },
        {
            "name": "schema_validation.success_iterations",
            "ok": schema_ok,
            "message": "parser_process_artifact and agent contracts validate for soak iterations",
        },
        {
            "name": "exception_probes.expected_codes",
            "ok": all(probe.get("code") == expected_probe_codes[probe["name"]] for probe in exception_probes),
            "message": json.dumps({probe["name"]: probe.get("code") for probe in exception_probes}, sort_keys=True),
        },
        {
            "name": "exception_probes.schema_auditable",
            "ok": all(
                probe["schema_validation"]["parser_process_artifact_ok"]
                and probe["schema_validation"]["agent_contract_ok"]
                and probe["schema_validation"]["child_agent_contract_ok"]
                for probe in exception_probes
            ),
            "message": "exception probes include schema-valid metadata/contracts",
        },
    ]
    ok = all(bool(check["ok"]) for check in checks)
    report = {
        "report_version": "p7-parser-process-long-soak-v1",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "trace": str(trace),
        "duration_s": duration_s,
        "elapsed_s": round(time.monotonic() - started, 6),
        "min_iterations": min_iterations,
        "successful_iterations": len(successful),
        "failed_iterations": len(failed),
        "rss_head_mb": rss_head,
        "rss_tail_mb": rss_tail,
        "rss_growth_mb": rss_growth,
        "max_parent_rss_growth_mb": float(args.max_parent_rss_growth_mb),
        "schema_validation": {
            "soak_iterations_ok": schema_ok,
            "exception_probes_ok": checks[-1]["ok"],
        },
        "checks": checks,
        "exception_probes": exception_probes,
        "artifact_paths": {
            "output_root": str(output_root),
            "iterations_jsonl": str(iterations_path),
            "resource_samples_jsonl": str(resource_samples_path),
            "exception_probes": str(exception_probe_path),
            "report_path": str(Path(args.report_path).expanduser().resolve()),
        },
        "soak_policy": {
            "timeout_s": float(args.timeout_s),
            "materialize_event_stream": _bool_arg(args.materialize_event_stream),
            "load_artifact": _bool_arg(args.load_artifact),
            "retain_artifact": _bool_arg(args.retain_artifact),
            "index_build_mode": str(args.index_build_mode),
        },
    }
    _json_dump(Path(args.report_path), report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ParserProcessAgent long soak RSS validation.")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--min-iterations", type=int, required=True)
    parser.add_argument("--timeout-s", type=float, required=True)
    parser.add_argument("--materialize-event-stream", default="0")
    parser.add_argument("--load-artifact", default="0")
    parser.add_argument("--retain-artifact", default="0")
    parser.add_argument("--index-build-mode", default="minimal")
    parser.add_argument("--max-parent-rss-growth-mb", type=float, default=256.0)
    parser.add_argument("--report-path", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_soak(args)
    print(json.dumps({"ok": report["ok"], "status": report["status"], "report_path": args.report_path}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

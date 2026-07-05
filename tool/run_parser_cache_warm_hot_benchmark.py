from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.parser_process_agent import PARSER_PROCESS_ARTIFACT_VERSION, ParserProcessAgent
from spec.schema_loader import DICTIONARY_PATH


REPORT_VERSION = "parser-cache-warm-hot-benchmark-v1"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _speedup_pct(cold: float | None, current: float | None) -> float | None:
    if cold is None or current is None or cold <= 0:
        return None
    return round(((cold - current) / cold) * 100.0, 6)


def _run_phase(
    *,
    phase: str,
    trace: Path,
    cache_root: Path,
    dictionary_path: Path,
    index_build_mode: str,
    parser_version: str,
    schema_version: str,
    materialize_event_stream: bool,
    load_artifact: bool,
    timeout_s: float | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = ParserProcessAgent(job_id=f"parser-cache-benchmark-{phase}").parse_rebuild(
        trace,
        timeout_s=timeout_s,
        artifact_policy={
            "cache_enabled": True,
            "cache_root": str(cache_root),
            "dictionary_path": str(dictionary_path),
            "parser_version": parser_version,
            "schema_version": schema_version,
            "index_build_mode": index_build_mode,
            "materialize_event_stream": materialize_event_stream,
            "load_artifact": load_artifact,
        },
    )
    wall_seconds = round(time.perf_counter() - started, 6)
    data = result.data if isinstance(result.data, dict) else {}
    artifact = data.get("parser_process_artifact") if isinstance(data.get("parser_process_artifact"), dict) else {}
    handle = data.get("artifact_handle") if isinstance(data.get("artifact_handle"), dict) else {}
    return {
        "phase": phase,
        "ok": bool(result.ok),
        "code": result.code,
        "message": result.message,
        "wall_seconds": wall_seconds,
        "cache_hit": bool(data.get("cache_hit")),
        "cache_key": data.get("cache_key"),
        "trace_checksum": data.get("trace_checksum"),
        "dictionary_checksum": data.get("dictionary_checksum"),
        "parse_seconds": _optional_float(data.get("parse_seconds")),
        "align_events_seconds": _optional_float(data.get("align_events_seconds")),
        "rebuild_seconds": _optional_float(data.get("rebuild_seconds")),
        "idx_build_seconds": _optional_float(data.get("idx_build_seconds")),
        "load_seconds": _optional_float(data.get("load_seconds")),
        "index_build_mode": data.get("index_build_mode") or artifact.get("index_build_mode"),
        "materialize_event_stream": bool(artifact.get("materialize_event_stream", materialize_event_stream)),
        "load_artifact": bool(load_artifact),
        "peak_rss_mb": _optional_float(data.get("peak_rss_mb") or artifact.get("peak_rss_mb")),
        "artifact_path": handle.get("path"),
        "result_path": data.get("result_path"),
        "cache_root": str(cache_root),
    }


def _build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_phase = {str(row["phase"]): row for row in rows}
    cold = _optional_float(by_phase.get("cold", {}).get("wall_seconds"))
    warm = _optional_float(by_phase.get("warm", {}).get("wall_seconds"))
    hot = _optional_float(by_phase.get("hot", {}).get("wall_seconds"))
    sequence = [bool(row.get("cache_hit")) for row in rows]
    expected_sequence = [False, True, True]
    return {
        "phase_count": len(rows),
        "all_ok": all(bool(row.get("ok")) for row in rows),
        "cache_hit_sequence": sequence,
        "expected_cache_hit_sequence": expected_sequence,
        "cache_sequence_valid": sequence == expected_sequence,
        "cold_wall_seconds": cold,
        "warm_wall_seconds": warm,
        "hot_wall_seconds": hot,
        "warm_speedup_vs_cold_pct": _speedup_pct(cold, warm),
        "hot_speedup_vs_cold_pct": _speedup_pct(cold, hot),
        "load_seconds_reference": _optional_float(by_phase.get("cold", {}).get("load_seconds")),
        "cache_effective": sequence == expected_sequence and warm is not None and hot is not None and cold is not None and hot < cold,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_parser_cache_warm_hot_benchmark")
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--dictionary-path", type=Path, default=DICTIONARY_PATH)
    parser.add_argument("--index-build-mode", choices=["full", "minimal", "deferred"], default="full")
    parser.add_argument("--parser-version", default=PARSER_PROCESS_ARTIFACT_VERSION)
    parser.add_argument("--schema-version", default=PARSER_PROCESS_ARTIFACT_VERSION)
    parser.add_argument("--timeout-s", type=float, default=None)
    args = parser.parse_args(argv)

    output_root = (args.output_root or args.output.parent / f"{args.output.stem}_artifacts").expanduser().resolve()
    cache_root = (args.cache_root or output_root / "parser_cache").expanduser().resolve()
    if cache_root.exists():
        shutil.rmtree(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    trace = args.trace.expanduser().resolve()
    dictionary_path = args.dictionary_path.expanduser().resolve()
    common = {
        "trace": trace,
        "cache_root": cache_root,
        "dictionary_path": dictionary_path,
        "index_build_mode": args.index_build_mode,
        "parser_version": str(args.parser_version),
        "schema_version": str(args.schema_version),
        "materialize_event_stream": True,
        "timeout_s": args.timeout_s,
    }
    rows = [
        _run_phase(phase="cold", load_artifact=True, **common),
        _run_phase(phase="warm", load_artifact=True, **common),
        _run_phase(phase="hot", load_artifact=False, **common),
    ]
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": _iso_now(),
        "input_contract": {
            "trace": str(trace),
            "output_root": str(output_root),
            "cache_root": str(cache_root),
            "dictionary_path": str(dictionary_path),
            "index_build_mode": args.index_build_mode,
            "parser_version": str(args.parser_version),
            "schema_version": str(args.schema_version),
            "phase_order": ["cold", "warm", "hot"],
            "materialize_event_stream": True,
        },
        "results": rows,
        "summary": _build_summary(rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"parser_cache_benchmark": str(args.output), "phase_count": len(rows)}, sort_keys=True))
    return 0 if report["summary"]["all_ok"] and report["summary"]["cache_sequence_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

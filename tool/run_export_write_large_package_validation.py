from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterator

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.export_write_agent import ExportWriteAgent
from spec.schema_loader import load_specs
from spec.schema_validator import validate_schema


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rss_current_mb() -> float | None:
    try:
        pages = int(Path("/proc/self/statm").read_text(encoding="utf-8").split()[1])
        page_size = int(__import__("os").sysconf("SC_PAGE_SIZE"))
        return round(pages * page_size / (1024 * 1024), 6)
    except Exception:
        return None


def _rss_peak_mb() -> float | None:
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            return round(value / (1024 * 1024), 6)
        return round(value / 1024, 6)
    except Exception:
        return None


def _row_payload(row_bytes: int) -> str:
    return "x" * max(1, int(row_bytes))


def _estimated_jsonl_row_bytes(payload: str) -> int:
    sample = {"row": 0, "payload": payload}
    return len(json.dumps(sample, ensure_ascii=False, sort_keys=True).encode("utf-8")) + 1


def _rows(row_count: int, payload: str, *, fail_after: int | None = None) -> Iterator[dict[str, Any]]:
    for index in range(row_count):
        if fail_after is not None and index >= fail_after:
            raise RuntimeError(f"injected export write row failure after {fail_after} rows")
        yield {"row": index, "payload": payload}


def _write_callback_bytes(path: Path, byte_count: int, chunk_size: int) -> int:
    chunk = b"b" * max(1, min(chunk_size, 1024 * 1024))
    remaining = int(byte_count)
    with path.open("wb") as handle:
        while remaining > 0:
            piece = chunk if remaining >= len(chunk) else chunk[:remaining]
            handle.write(piece)
            remaining -= len(piece)
    return 1


def _schema_checks(package_result: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, bool]:
    specs = load_specs()
    checks: list[dict[str, Any]] = []
    metrics_ok = True
    for index, metric in enumerate(package_result.get("write_metrics") or []):
        reason = validate_schema(specs["export_write_metric"], metric)
        ok = reason is None
        metrics_ok = metrics_ok and ok
        checks.append({
            "name": f"write_metric_schema.{index}",
            "ok": ok,
            "message": "export_write_metric schema valid" if ok else str(reason),
        })

    blockers_ok = True
    for index, blocker in enumerate(package_result.get("write_failure_blockers") or []):
        reason = validate_schema(specs["write_failure_blocker"], blocker)
        ok = reason is None
        blockers_ok = blockers_ok and ok
        checks.append({
            "name": f"write_failure_blocker_schema.{index}",
            "ok": ok,
            "message": "write_failure_blocker schema valid" if ok else str(reason),
        })
    return checks, metrics_ok, blockers_ok


def _metric_summary(metrics: list[dict[str, Any]]) -> tuple[int, int, list[str], list[str]]:
    actual_bytes = sum(int(metric.get("bytes_written") or 0) for metric in metrics)
    row_count = sum(int(metric.get("count") or 0) for metric in metrics if metric.get("write_mode") != "binary_callback")
    modes = sorted({str(metric.get("write_mode")) for metric in metrics})
    checksums = [str(metric.get("checksum")) for metric in metrics if metric.get("checksum")]
    return actual_bytes, row_count, modes, checksums


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    package_root = output_root / "package"
    target_bytes = int(args.target_bytes)
    row_bytes = int(args.row_bytes)
    payload = _row_payload(row_bytes)
    estimated_row_bytes = _estimated_jsonl_row_bytes(payload)
    row_count = max(1, math.ceil(target_bytes / estimated_row_bytes))
    callback_bytes = 0 if args.expect_failure_blocker else max(row_bytes, min(16 * 1024 * 1024, max(1, target_bytes // 64)))
    rss_before = _rss_current_mb()
    peak_before = _rss_peak_mb()
    resource_probe_skipped = rss_before is None and peak_before is None

    agent = ExportWriteAgent(job_id="p6-export-write-large-package")
    entries: list[dict[str, Any]] = [
        {
            "relative_path": "result/generated_rows.jsonl",
            "write_mode": "stream_jsonl",
            "rows": _rows(row_count, payload, fail_after=args.inject_failure_after_rows),
            "label": "generated_rows",
        }
    ]
    if not args.expect_failure_blocker:
        entries.append({
            "relative_path": "event/callback_payload.bin",
            "write_mode": "binary_callback",
            "callback": lambda target: _write_callback_bytes(target, callback_bytes, row_bytes),
            "label": "callback_payload",
            "count": 1,
        })

    result = agent.write_evidence_package_optimized(
        {
            "package_path": str(package_root),
            "snapshot_id": "snapshot:p6:export-write-large-package",
            "entries": entries,
        },
        {
            "package_path": str(package_root),
            "snapshot_id": "snapshot:p6:export-write-large-package",
        },
    )

    rss_after = _rss_current_mb()
    peak_after = _rss_peak_mb()
    rss_delta = None if rss_before is None or rss_after is None else round(rss_after - rss_before, 6)
    peak_delta = None if peak_before is None or peak_after is None else round(peak_after - peak_before, 6)
    package_result = dict((result.data or {}).get("package_result") or {})
    metrics = [dict(metric) for metric in list(package_result.get("write_metrics") or [])]
    blockers = [dict(blocker) for blocker in list(package_result.get("write_failure_blockers") or [])]
    actual_bytes, actual_row_count, write_modes, checksums = _metric_summary(metrics)
    schema_checks, metrics_schema_ok, blockers_schema_ok = _schema_checks(package_result)

    blocker_path = package_root / "control" / "write_failure_blocker.json"
    checks: list[dict[str, Any]] = list(schema_checks)
    checks.extend([
        {
            "name": "write_result.expected_status",
            "ok": (not result.ok if args.expect_failure_blocker else result.ok),
            "message": result.message,
        },
        {
            "name": "target_bytes.reached",
            "ok": bool(args.expect_failure_blocker or actual_bytes >= target_bytes),
            "message": f"actual_bytes={actual_bytes}, target_bytes={target_bytes}",
        },
        {
            "name": "streaming_write_mode.present",
            "ok": bool(args.expect_failure_blocker or "stream_jsonl" in write_modes),
            "message": f"write_modes={write_modes}",
        },
        {
            "name": "binary_callback_write_mode.present",
            "ok": bool(args.expect_failure_blocker or "binary_callback" in write_modes),
            "message": f"write_modes={write_modes}",
        },
        {
            "name": "rss_delta.within_limit",
            "ok": bool(rss_delta is None or rss_delta <= float(args.max_rss_delta_mb)),
            "message": f"rss_delta_mb={rss_delta}, max_rss_delta_mb={args.max_rss_delta_mb}",
        },
        {
            "name": "failure_blocker.expected",
            "ok": bool((not args.expect_failure_blocker) or (blockers and blocker_path.exists() and blockers_schema_ok)),
            "message": f"blocker_count={len(blockers)}, blocker_path={blocker_path}",
        },
    ])

    ok = all(bool(check["ok"]) for check in checks)
    status = "passed" if ok and not args.expect_failure_blocker else "expected_failure_blocker" if ok else "failed"
    report = {
        "report_version": "p6-export-write-large-package-validation-v1",
        "status": status,
        "ok": ok,
        "target_bytes": target_bytes,
        "actual_bytes": actual_bytes,
        "row_bytes": row_bytes,
        "row_count": actual_row_count,
        "planned_row_count": row_count,
        "write_modes": write_modes,
        "checksums": checksums,
        "write_metrics": metrics,
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
        "rss_delta_mb": rss_delta,
        "peak_rss_mb": peak_after,
        "peak_rss_delta_mb": peak_delta,
        "resource_probe_skipped": resource_probe_skipped,
        "schema_validation": {
            "write_metrics_ok": metrics_schema_ok,
            "write_failure_blockers_ok": blockers_schema_ok,
        },
        "failure_injection": {
            "inject_failure_after_rows": args.inject_failure_after_rows,
            "expect_failure_blocker": bool(args.expect_failure_blocker),
            "blocker_count": len(blockers),
            "blocker_path": str(blocker_path) if blocker_path.exists() else None,
        },
        "write_failure_blockers": blockers,
        "checks": checks,
        "artifact_paths": {
            "output_root": str(output_root),
            "package_path": str(package_root),
            "jsonl_path": str(package_root / "result" / "generated_rows.jsonl"),
            "callback_path": str(package_root / "event" / "callback_payload.bin"),
            "write_failure_blocker": str(blocker_path) if blocker_path.exists() else None,
        },
    }
    _json_dump(Path(args.report_path), report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate ExportWriteAgent large package streaming/RSS behavior.")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--target-bytes", type=int, required=True)
    parser.add_argument("--row-bytes", type=int, default=4096)
    parser.add_argument("--max-rss-delta-mb", type=float, default=512.0)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--inject-failure-after-rows", type=int, default=None)
    parser.add_argument("--expect-failure-blocker", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_validation(args)
    print(json.dumps({"report_path": str(Path(args.report_path)), "ok": report["ok"], "status": report["status"]}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

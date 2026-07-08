from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.runtime_advisor import (
    OFFLINE_ADVISOR_MODEL_REF,
    OFFLINE_ADVISOR_PRIOR_VERSION,
    coefficient_payload_checksum,
    runtime_advisor_prior_bucket,
    safe_runtime_action_candidates,
    safe_runtime_action_space,
)
from parser.telemetry import normalize_telemetry_history_item


FEATURE_NAMES = [
    "input_bytes",
    "sidecar_bytes",
    "sidecar_row_count",
    "sidecar_bytes_scanned",
    "sidecar_validate_seconds",
    "index_build_open_seconds",
    "package_write_seconds",
    "peak_rss_mb",
]
DEFAULT_MODEL_REF = OFFLINE_ADVISOR_MODEL_REF


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _telemetry_source_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError("telemetry JSON must be an array")
        rows = [dict(row) for row in payload if isinstance(row, dict)]
        return [row for row in (normalize_telemetry_history_item(item) for item in rows) if row is not None]
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            rows: list[dict[str, Any]] = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                row_payload = json.loads(line)
                if isinstance(row_payload, dict):
                    normalized = normalize_telemetry_history_item(row_payload)
                    if normalized is not None:
                        rows.append(normalized)
            return rows
        if not isinstance(payload, dict):
            raise ValueError("telemetry JSON object must be an object")
        if isinstance(payload.get("results"), list):
            rows = [dict(row) for row in payload["results"] if isinstance(row, dict)]
            return [row for row in (normalize_telemetry_history_item(item) for item in rows) if row is not None]
        if isinstance(payload.get("telemetry_record"), dict):
            normalized = normalize_telemetry_history_item(dict(payload["telemetry_record"]))
            return [normalized] if normalized is not None else []
        normalized = normalize_telemetry_history_item(payload)
        return [normalized] if normalized is not None else []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            normalized = normalize_telemetry_history_item(payload)
            if normalized is not None:
                rows.append(normalized)
    return rows


def _skip_report(reason: str, *, rows: int) -> dict[str, Any]:
    return {
        "artifact_version": OFFLINE_ADVISOR_PRIOR_VERSION,
        "created_at": _iso_now(),
        "status": "skipped",
        "skip_reason": reason,
        "model_ref": DEFAULT_MODEL_REF,
        "training_rows": int(rows),
        "telemetry_source_digest": None,
        "training_input_summary": {
            "source_row_count": int(rows),
            "normalized_row_count": int(rows),
            "rows_with_runtime_seconds": 0,
            "rows_with_peak_rss_mb": 0,
            "ticket_present_rows": 0,
            "high_rss_rows": 0,
            "bucket_count": 0,
        },
        "feature_names": list(FEATURE_NAMES),
        "safe_action_space": safe_runtime_action_space(),
        "bucket_strategy": "runtime-advisor-safe-prior-bucket-v1",
        "global_action_support": {action_kind: 0 for action_kind in safe_runtime_action_space()},
        "global_action_scores": {action_kind: 0.0 for action_kind in safe_runtime_action_space()},
        "buckets": {},
    }


def _float_or_none(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_features(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["ticket_present"] = bool(payload.get("index_reused") or payload.get("sidecar_ticket_fast_path"))
    return payload


def _finalize_action_scores(action_support: dict[str, int], *, row_count: int) -> dict[str, float]:
    if row_count <= 0:
        return {action_kind: 0.0 for action_kind in safe_runtime_action_space()}
    return {
        action_kind: round(float(action_support.get(action_kind, 0)) / float(row_count), 6)
        for action_kind in safe_runtime_action_space()
    }


def train_runtime_advisor_coefficients(
    telemetry_path: Path,
    output_path: Path,
    model_ref: str = DEFAULT_MODEL_REF,
) -> dict[str, Any]:
    rows = _load_rows(telemetry_path)
    trainable = [_row_features(row) for row in rows]
    if not trainable:
        report = _skip_report("at least one telemetry row with advisor features is required", rows=len(rows))
    else:
        created_at = _iso_now()
        telemetry_source_digest = _telemetry_source_digest(telemetry_path)
        global_action_support = {action_kind: 0 for action_kind in safe_runtime_action_space()}
        bucket_counts: dict[str, int] = defaultdict(int)
        bucket_runtime_totals: dict[str, float] = defaultdict(float)
        bucket_runtime_counts: dict[str, int] = defaultdict(int)
        bucket_rss_totals: dict[str, float] = defaultdict(float)
        bucket_rss_counts: dict[str, int] = defaultdict(int)
        bucket_action_support: dict[str, dict[str, int]] = defaultdict(
            lambda: {action_kind: 0 for action_kind in safe_runtime_action_space()}
        )
        for row in trainable:
            bucket_key = runtime_advisor_prior_bucket(row)
            bucket_counts[bucket_key] += 1
            runtime_seconds = _float_or_none(row, "runtime_seconds")
            peak_rss_mb = _float_or_none(row, "peak_rss_mb")
            if runtime_seconds is not None:
                bucket_runtime_totals[bucket_key] += runtime_seconds
                bucket_runtime_counts[bucket_key] += 1
            if peak_rss_mb is not None:
                bucket_rss_totals[bucket_key] += peak_rss_mb
                bucket_rss_counts[bucket_key] += 1
            for action_kind in safe_runtime_action_candidates(row, include_abstain=False, telemetry_missing=False):
                global_action_support[action_kind] += 1
                bucket_action_support[bucket_key][action_kind] += 1

        buckets = {}
        for bucket_key in sorted(bucket_counts):
            row_count = int(bucket_counts[bucket_key])
            action_support = dict(bucket_action_support[bucket_key])
            buckets[bucket_key] = {
                "row_count": row_count,
                "mean_runtime_seconds": (
                    round(bucket_runtime_totals[bucket_key] / bucket_runtime_counts[bucket_key], 6)
                    if bucket_runtime_counts[bucket_key] > 0
                    else None
                ),
                "mean_peak_rss_mb": (
                    round(bucket_rss_totals[bucket_key] / bucket_rss_counts[bucket_key], 6)
                    if bucket_rss_counts[bucket_key] > 0
                    else None
                ),
                "action_support": action_support,
                "action_scores": _finalize_action_scores(action_support, row_count=row_count),
            }

        payload = {
            "artifact_version": OFFLINE_ADVISOR_PRIOR_VERSION,
            "created_at": created_at,
            "status": "trained",
            "model_ref": str(model_ref or OFFLINE_ADVISOR_MODEL_REF),
            "training_rows": len(trainable),
            "telemetry_source_digest": telemetry_source_digest,
            "training_input_summary": {
                "source_row_count": len(rows),
                "normalized_row_count": len(trainable),
                "rows_with_runtime_seconds": sum(1 for row in trainable if _float_or_none(row, "runtime_seconds") is not None),
                "rows_with_peak_rss_mb": sum(1 for row in trainable if _float_or_none(row, "peak_rss_mb") is not None),
                "ticket_present_rows": sum(1 for row in trainable if bool(row.get("ticket_present"))),
                "high_rss_rows": sum(
                    1
                    for row in trainable
                    for peak_rss_mb in [_float_or_none(row, "peak_rss_mb")]
                    if peak_rss_mb is not None and peak_rss_mb >= 2048.0
                ),
                "bucket_count": len(bucket_counts),
            },
            "feature_names": list(FEATURE_NAMES),
            "safe_action_space": safe_runtime_action_space(),
            "bucket_strategy": "runtime-advisor-safe-prior-bucket-v1",
            "global_action_support": global_action_support,
            "global_action_scores": _finalize_action_scores(global_action_support, row_count=len(trainable)),
            "buckets": buckets,
        }
        payload["model_checksum"] = coefficient_payload_checksum(payload)
        report = payload
    if report["status"] == "skipped":
        report["telemetry_source_digest"] = _telemetry_source_digest(telemetry_path)
        report["training_input_summary"] = {
            **dict(report.get("training_input_summary") or {}),
            "source_row_count": len(rows),
            "normalized_row_count": len(trainable),
            "rows_with_runtime_seconds": sum(1 for row in trainable if _float_or_none(row, "runtime_seconds") is not None),
            "rows_with_peak_rss_mb": sum(1 for row in trainable if _float_or_none(row, "peak_rss_mb") is not None),
            "ticket_present_rows": sum(1 for row in trainable if bool(row.get("ticket_present"))),
            "high_rss_rows": sum(
                1
                for row in trainable
                for peak_rss_mb in [_float_or_none(row, "peak_rss_mb")]
                if peak_rss_mb is not None and peak_rss_mb >= 2048.0
            ),
            "bucket_count": 0,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="train_runtime_advisor")
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-ref", default=DEFAULT_MODEL_REF)
    args = parser.parse_args(argv)

    report = train_runtime_advisor_coefficients(args.telemetry, args.output, model_ref=str(args.model_ref))
    print(json.dumps({"output": str(args.output), "status": report["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.runtime_advisor import coefficient_payload_checksum
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
DEFAULT_MODEL_REF = "offline-coefficients-sklearn-v1"


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


def _float(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _skip_report(reason: str, *, rows: int) -> dict[str, Any]:
    return {
        "status": "skipped",
        "skip_reason": reason,
        "optional_dependency": "sklearn",
        "training_rows": int(rows),
        "feature_names": list(FEATURE_NAMES),
    }


def train_runtime_advisor_coefficients(
    telemetry_path: Path,
    output_path: Path,
    model_ref: str = DEFAULT_MODEL_REF,
) -> dict[str, Any]:
    rows = _load_rows(telemetry_path)
    try:
        from sklearn.linear_model import LinearRegression  # type: ignore
        from sklearn.metrics import mean_absolute_error, r2_score  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional environment
        report = _skip_report(f"optional sklearn unavailable: {exc}", rows=len(rows))
    else:
        trainable = [row for row in rows if row.get("runtime_seconds") is not None]
        if len(trainable) < 2:
            report = _skip_report("at least two telemetry rows with runtime_seconds are required", rows=len(rows))
        else:
            x = [[_float(row, name) for name in FEATURE_NAMES] for row in trainable]
            y = [_float(row, "runtime_seconds") for row in trainable]
            model = LinearRegression()
            model.fit(x, y)
            predictions = [float(item) for item in model.predict(x)]
            payload = {
                "status": "trained",
                "model_ref": str(model_ref),
                "optional_dependency": "sklearn",
                "training_rows": len(trainable),
                "feature_names": list(FEATURE_NAMES),
                "runtime_intercept": float(model.intercept_),
                "runtime_weights": {
                    name: float(weight)
                    for name, weight in zip(FEATURE_NAMES, list(model.coef_))
                },
                "backtest": {
                    "mean_absolute_error": float(mean_absolute_error(y, predictions)),
                    "r2_score": float(r2_score(y, predictions)) if len(set(y)) > 1 else None,
                },
            }
            payload["model_checksum"] = coefficient_payload_checksum(payload)
            report = payload

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

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _version_of(module: object) -> str | None:
    value = getattr(module, "__version__", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _environment(*, qt_qpa_platform: str | None) -> dict[str, object]:
    return {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "qt_qpa_platform": qt_qpa_platform,
    }


def _normalize_evidence_scope(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
    else:
        normalized = ""
    if normalized == "formal_gui_onscreen":
        return "formal_gui_onscreen"
    return "synthetic"


def _normalized_qpa_platform(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            return normalized
    return None


def _is_onscreen_session(
    *,
    qpa_platform: str | None,
    screen_count: int,
    widget_visible: bool,
) -> bool:
    if qpa_platform == "offscreen":
        return False
    return bool(screen_count > 0 and widget_visible)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if percentile <= 0:
        return float(min(values))
    if percentile >= 100:
        return float(max(values))
    ordered = sorted(values)
    idx = (len(ordered) - 1) * (percentile / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return float(ordered[lo])
    frac = idx - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def _frame_metrics(frame_times_ms: list[float]) -> dict[str, object]:
    if not frame_times_ms:
        return {
            "frame_time_ms_avg": 0.0,
            "frame_time_ms_p50": 0.0,
            "frame_time_ms_p95": 0.0,
            "frame_time_ms_p99": 0.0,
            "frame_time_ms_min": 0.0,
            "frame_time_ms_max": 0.0,
            "fps_avg": 0.0,
            "fps_p50": 0.0,
            "fps_p95": 0.0,
            "fps_p99": 0.0,
            "fps_min": 0.0,
            "fps_max": 0.0,
        }
    avg_ms = sum(frame_times_ms) / float(len(frame_times_ms))
    fps_values = [1000.0 / ms for ms in frame_times_ms if ms > 1e-9]
    avg_fps = sum(fps_values) / float(len(fps_values)) if fps_values else 0.0
    return {
        "frame_time_ms_avg": round(avg_ms, 6),
        "frame_time_ms_p50": round(_percentile(frame_times_ms, 50), 6),
        "frame_time_ms_p95": round(_percentile(frame_times_ms, 95), 6),
        "frame_time_ms_p99": round(_percentile(frame_times_ms, 99), 6),
        "frame_time_ms_min": round(min(frame_times_ms), 6),
        "frame_time_ms_max": round(max(frame_times_ms), 6),
        "fps_avg": round(avg_fps, 6),
        "fps_p50": round(_percentile(fps_values, 50), 6) if fps_values else 0.0,
        "fps_p95": round(_percentile(fps_values, 95), 6) if fps_values else 0.0,
        "fps_p99": round(_percentile(fps_values, 99), 6) if fps_values else 0.0,
        "fps_min": round(min(fps_values), 6) if fps_values else 0.0,
        "fps_max": round(max(fps_values), 6) if fps_values else 0.0,
    }


def _write_report(output_path: str, payload: dict[str, object]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    Path(output_path).write_text(rendered, encoding="utf-8")
    print(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_render_fps_report")
    parser.add_argument("--output", required=True)
    parser.add_argument("--tile-count", type=int, default=100000)
    parser.add_argument("--iterations", type=int, default=120)
    parser.add_argument("--mode", default="bargraph_batched")
    parser.add_argument("--frame-budget-ms", type=float, default=16.0)
    parser.add_argument("--target-fps", type=float, default=30.0)
    parser.add_argument(
        "--evidence-scope",
        default="synthetic",
        help="synthetic (default) or formal_gui_onscreen",
    )
    args = parser.parse_args(argv)

    # Keep headless-friendly default while still allowing users to override externally.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_qpa_platform_raw = os.environ.get("QT_QPA_PLATFORM")
    qpa_norm = _normalized_qpa_platform(qt_qpa_platform_raw)
    evidence_scope = _normalize_evidence_scope(args.evidence_scope)

    limitations: list[str] = []
    if evidence_scope == "synthetic":
        limitations.append("synthetic_workload")
    if qpa_norm:
        limitations.append(f"qpa:{qpa_norm}")
    if qpa_norm == "offscreen":
        limitations.append("offscreen")

    execution: dict[str, object] = {
        "evidence_scope": evidence_scope,
        "qt_qpa_platform": qpa_norm,
        "screen_count": None,
        "widget_visible": None,
        "onscreen_verified": False,
    }

    try:
        import PySide6  # type: ignore
        from PySide6 import QtWidgets  # type: ignore

        import pyqtgraph as pg  # type: ignore
    except Exception as exc:
        report = {
            "generated_at": _iso_now(),
            "status": "missing_dependency",
            "error": repr(exc),
            "environment": _environment(qt_qpa_platform=qpa_norm),
            "dependencies": {
                "pyside6": None,
                "pyqtgraph": None,
            },
            "evidence_scope": evidence_scope,
            "execution": execution,
            "workload": {
                "tile_count": int(args.tile_count),
                "iterations": int(args.iterations),
                "mode": str(args.mode),
            },
            "thresholds": {
                "target_fps": float(args.target_fps),
                "frame_budget_ms": float(args.frame_budget_ms),
            },
            "metrics": {},
            "verdict": {
                "pass": False,
                "reason": "missing_dependency",
            },
            "limitations": ["missing_dependency", *limitations],
            "notes": [
                "This is a synthetic render baseline. It is not a formal NFR acceptance report.",
            ],
        }
        _write_report(args.output, report)
        return 1

    report_deps = {
        "pyside6": _version_of(PySide6),
        "pyqtgraph": _version_of(pg),
    }

    if args.tile_count <= 0 or args.iterations <= 0:
        report = {
            "generated_at": _iso_now(),
            "status": "error",
            "error": "tile-count and iterations must be positive",
            "environment": _environment(qt_qpa_platform=qpa_norm),
            "dependencies": report_deps,
            "evidence_scope": evidence_scope,
            "execution": execution,
            "workload": {
                "tile_count": int(args.tile_count),
                "iterations": int(args.iterations),
                "mode": str(args.mode),
            },
            "thresholds": {
                "target_fps": float(args.target_fps),
                "frame_budget_ms": float(args.frame_budget_ms),
            },
            "metrics": {},
            "verdict": {"pass": False, "reason": "invalid_args"},
            "limitations": ["invalid_args", *limitations],
        }
        _write_report(args.output, report)
        return 1

    tile_count = int(args.tile_count)
    if tile_count < 100000:
        limitations.append("scale_not_covered")

    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        widget = pg.PlotWidget()
        widget.resize(1024, 256)
        widget.setWindowTitle("rttrace render fps synthetic")
        widget.show()

        mode = str(args.mode).strip().lower() or "bargraph_batched"
        if mode != "bargraph_batched":
            limitations.append(f"mode:{mode}")
        x = list(range(tile_count))
        heights = [1.0] * tile_count
        item = pg.BarGraphItem(x=x, height=heights, width=0.8)
        widget.addItem(item)
        if hasattr(widget, "setXRange"):
            widget.setXRange(0, float(tile_count))
        if hasattr(widget, "setYRange"):
            widget.setYRange(0, 1.5)

        app.processEvents()

        screen_count = len(app.screens()) if callable(getattr(app, "screens", None)) else 0
        widget_visible = bool(widget.isVisible()) if callable(getattr(widget, "isVisible", None)) else False
        onscreen_verified = _is_onscreen_session(
            qpa_platform=qpa_norm,
            screen_count=screen_count,
            widget_visible=widget_visible,
        )
        execution = {
            "evidence_scope": evidence_scope,
            "qt_qpa_platform": qpa_norm,
            "screen_count": int(screen_count),
            "widget_visible": widget_visible,
            "onscreen_verified": onscreen_verified,
        }
        if evidence_scope == "formal_gui_onscreen" and not onscreen_verified:
            limitations.append("formal_onscreen_not_verified")

        frame_times_ms: list[float] = []
        for _ in range(int(args.iterations)):
            started = time.perf_counter()
            widget.repaint()
            app.processEvents()
            frame_times_ms.append((time.perf_counter() - started) * 1000.0)

        metrics = _frame_metrics(frame_times_ms)
        fps_p95 = float(metrics.get("fps_p95") or 0.0)
        pass_flag = fps_p95 >= float(args.target_fps)
        verdict = {
            "target_fps": float(args.target_fps),
            "fps_p95": round(fps_p95, 6),
            "pass": bool(pass_flag),
        }

        report = {
            "generated_at": _iso_now(),
            "status": "ok",
            "environment": _environment(qt_qpa_platform=qpa_norm),
            "dependencies": report_deps,
            "evidence_scope": evidence_scope,
            "execution": execution,
            "workload": {
                "tile_count": tile_count,
                "iterations": int(args.iterations),
                "mode": str(args.mode),
            },
            "thresholds": {
                "target_fps": float(args.target_fps),
                "frame_budget_ms": float(args.frame_budget_ms),
            },
            "metrics": metrics,
            "verdict": verdict,
            "limitations": limitations,
            "notes": [
                (
                    "This report provides synthetic render evidence for GUI performance evaluation."
                    if evidence_scope == "synthetic"
                    else "This report targets formal GUI/onscreen FPS evidence."
                ),
                (
                    "Onscreen execution detected on the active Qt platform; results support GUI performance validation but do not replace a full interactive UI acceptance suite."
                    if execution["onscreen_verified"] is True
                    else "Offscreen or non-visible execution cannot be used as formal GUI FPS closure evidence."
                ),
            ],
        }
        _write_report(args.output, report)
        return 0
    except Exception as exc:
        report = {
            "generated_at": _iso_now(),
            "status": "error",
            "error": repr(exc),
            "environment": _environment(qt_qpa_platform=qpa_norm),
            "dependencies": report_deps,
            "evidence_scope": evidence_scope,
            "execution": execution,
            "workload": {
                "tile_count": tile_count,
                "iterations": int(args.iterations),
                "mode": str(args.mode),
            },
            "thresholds": {
                "target_fps": float(args.target_fps),
                "frame_budget_ms": float(args.frame_budget_ms),
            },
            "metrics": {},
            "verdict": {"pass": False, "reason": "runtime_error"},
            "limitations": ["runtime_error", *limitations],
        }
        _write_report(args.output, report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

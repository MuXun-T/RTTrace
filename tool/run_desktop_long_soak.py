from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.services import WorkspaceController
from parser.models import EventTableQuery, TaskStateQuery


def _bundle_window(controller: WorkspaceController, dataset_id: str) -> tuple[float, float]:
    bundle = controller.repository.get(dataset_id).artifact.bundle
    return (bundle.event_stream[0].timestamp_aligned, bundle.event_stream[-1].timestamp_aligned)


def _run_iteration(trace_path: Path, *, cache_budget_mb: float) -> dict[str, object]:
    controller = WorkspaceController(cache_budget_mb=cache_budget_mb)
    loaded = controller.viz_LoadDataset(str(trace_path))
    if not loaded.ok:
        raise RuntimeError(loaded.message)
    dataset_id = loaded.data
    controller.active_dataset_id = dataset_id
    time_window = _bundle_window(controller, dataset_id)

    task_states = controller.viz_QueryTaskStates(TaskStateQuery(time_window=time_window))
    if not task_states.ok:
        raise RuntimeError(task_states.message)

    event_page = controller.viz_QueryEventTable(
        EventTableQuery(
            filter={"dataset_id": dataset_id},
            limit=64,
        )
    )
    if not event_page.ok:
        raise RuntimeError(event_page.message)

    timeline_lod2 = controller.viz_QueryTimelineLOD(
        {
            "dataset_id": dataset_id,
            "time_window": time_window,
            "filter": {},
            "lod": 2,
            "event_limit": 64,
        }
    )
    if not timeline_lod2.ok:
        raise RuntimeError(timeline_lod2.message)

    return {
        "event_count": len(controller.repository.get(dataset_id).artifact.bundle.event_stream),
        "task_state_row_count": len(task_states.data.rows),
        "event_page_count": len(event_page.data.items),
        "lod2_event_count": len(timeline_lod2.data.events),
    }


def _render(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_desktop_long_soak")
    parser.add_argument("--input", required=True)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--cache-budget-mb", type=float, default=8.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--irrecoverable-output")
    args = parser.parse_args(argv)

    trace_path = Path(args.input).expanduser().resolve()
    if not trace_path.exists():
        raise SystemExit(f"--input does not exist: {trace_path}")
    if args.duration_s <= 0.0:
        raise SystemExit("--duration-s must be positive")

    started = time.monotonic()
    deadline = started + float(args.duration_s)
    iterations = 0
    failures = 0
    errors = 0
    last_error_summary: str | None = None
    last_iteration: dict[str, object] = {}

    while time.monotonic() < deadline or iterations == 0:
        try:
            last_iteration = _run_iteration(trace_path, cache_budget_mb=float(args.cache_budget_mb))
            iterations += 1
        except Exception as exc:  # pragma: no cover - exercised through CLI behavior
            errors += 1
            last_error_summary = str(exc)
            break

    duration_sec = round(time.monotonic() - started, 6)
    soak_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.system().lower(),
        "scenario": "desktop_long_soak",
        "duration_sec": duration_sec,
        "status": "ok" if errors == 0 else "error",
        "failures": failures,
        "errors": errors,
        "notes": [
            f"iterations={iterations}",
            f"input={trace_path}",
            *(f"{key}={value}" for key, value in sorted(last_iteration.items())),
        ],
    }
    output_path = Path(args.output).expanduser().resolve()
    _render(soak_payload, output_path)

    irrecoverable_payload = {
        "generated_at": soak_payload["generated_at"],
        "platform": platform.system().lower(),
        "duration_sec": duration_sec,
        "irrecoverable_error_detected": bool(errors > 0),
        "last_error_summary": last_error_summary,
        "evidence_paths": [str(output_path)],
    }
    if args.irrecoverable_output:
        _render(irrecoverable_payload, Path(args.irrecoverable_output).expanduser().resolve())

    print(json.dumps({"desktop_soak": soak_payload, "irrecoverable_error": irrecoverable_payload}, indent=2, ensure_ascii=False))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

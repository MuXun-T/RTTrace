from __future__ import annotations

from pathlib import Path

from parser.codec import encode_trace
from spec.events import event_id_for


def _repeat_events(
    events: list[dict[str, object]],
    *,
    repeat: int,
    cycle_gap: int | None,
) -> list[dict[str, object]]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    if repeat == 1:
        return sorted(events, key=lambda item: (int(item["core_id"]), int(item["seq"])))

    max_seq_by_core: dict[int, int] = {}
    timestamps = [int(item["timestamp"]) for item in events]
    for item in events:
        core_id = int(item["core_id"])
        max_seq_by_core[core_id] = max(max_seq_by_core.get(core_id, 0), int(item["seq"]))
    base_span = max(timestamps) - min(timestamps) + 1000
    cycle_span = max(base_span, cycle_gap or 0)

    repeated: list[dict[str, object]] = []
    for index in range(repeat):
        for item in events:
            core_id = int(item["core_id"])
            repeated.append(
                {
                    **item,
                    "seq": int(item["seq"]) + index * max_seq_by_core[core_id],
                    "timestamp": int(item["timestamp"]) + index * cycle_span,
                    "payload": dict(item["payload"]),
                }
            )
    return sorted(repeated, key=lambda item: (int(item["core_id"]), int(item["seq"])))


def build_scenario(
    name: str = "basic",
    candidate_variant: bool = False,
    *,
    repeat: int = 1,
    cycle_gap: int | None = None,
) -> list[dict[str, object]]:
    if name not in {"basic", "gap", "multi_core"}:
        raise ValueError(f"unknown scenario: {name}")
    offset = 50 if candidate_variant else 0
    events = [
        {
            "core_id": 0,
            "event_id": event_id_for("TASK_READY"),
            "seq": 1,
            "timestamp": 1000 + offset,
            "payload": {"task_id": 1, "prio": 10, "core_hint": 0, "reason": 1},
        },
        {
            "core_id": 0,
            "event_id": event_id_for("TASK_DISPATCH"),
            "seq": 2,
            "timestamp": 1100 + offset,
            "payload": {"task_id": 1, "core_id": 0, "prio": 10, "reason": 2},
        },
        {
            "core_id": 0,
            "event_id": event_id_for("CTX_SWITCH"),
            "seq": 3,
            "timestamp": 1120 + offset,
            "payload": {"core_id": 0, "prev_task_id": 0, "next_task_id": 1, "reason": 2},
        },
        {
            "core_id": 0,
            "event_id": event_id_for("SYNC_TRY"),
            "seq": 4,
            "timestamp": 1200 + offset,
            "payload": {"task_id": 2, "obj_id": 0xABC, "obj_type": 1, "timeout_ns": 50000},
        },
        {
            "core_id": 0,
            "event_id": event_id_for("TASK_BLOCK"),
            "seq": 5,
            "timestamp": 1220 + offset,
            "payload": {"task_id": 2, "wait_obj_id": 0xABC, "reason": 3, "owner_task_id": 1},
        },
        {
            "core_id": 0,
            "event_id": event_id_for("IRQ_ENTER"),
            "seq": 6,
            "timestamp": 1250 + offset,
            "payload": {"irq_id": 7, "core_id": 0, "nesting_depth": 1},
        },
        {
            "core_id": 0,
            "event_id": event_id_for("IRQ_EXIT"),
            "seq": 7,
            "timestamp": 1320 + offset,
            "payload": {"irq_id": 7, "core_id": 0, "nesting_depth": 1},
        },
        {
            "core_id": 0,
            "event_id": event_id_for("SYNC_UNLOCK"),
            "seq": 8,
            "timestamp": 1350 + offset,
            "payload": {"task_id": 1, "obj_id": 0xABC, "obj_type": 1, "timeout_ns": 0},
        },
    ]
    if name in {"basic", "multi_core", "gap"}:
        second_core_seq = 2 if name == "gap" else 1
        events.extend(
            [
                {
                    "core_id": 1,
                    "event_id": event_id_for("TASK_WAKEUP"),
                    "seq": second_core_seq,
                    "timestamp": 1380 + offset,
                    "payload": {"task_id": 2, "wake_src": 4, "obj_id": 0xABC},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("CTX_SWITCH"),
                    "seq": second_core_seq + 1,
                    "timestamp": 1400 + offset,
                    "payload": {"core_id": 1, "prev_task_id": 0, "next_task_id": 2, "reason": 1},
                },
            ]
        )
    if name == "gap":
        events.insert(
            8,
            {
                "core_id": 1,
                "event_id": event_id_for("LOSS"),
                "seq": 1,
                "timestamp": 1370 + offset,
                "payload": {"core_id": 1, "lost_count": 1, "reason": 1},
            },
        )
    if name == "multi_core":
        events.extend(
            [
                {
                    "core_id": 0,
                    "event_id": event_id_for("SYNC_CALIB"),
                    "seq": 9,
                    "timestamp": 1450 + offset,
                    "payload": {"raw_hex": "01020304"},
                },
                {
                    "core_id": 1,
                    "event_id": event_id_for("TS_CALIB"),
                    "seq": 2 if candidate_variant else 3,
                    "timestamp": 1460 + offset,
                    "payload": {"raw_hex": "05060708"},
                },
            ]
        )
    return _repeat_events(events, repeat=repeat, cycle_gap=cycle_gap)


def write_scenario(
    output_path: str | Path,
    name: str = "basic",
    candidate_variant: bool = False,
    *,
    repeat: int = 1,
    cycle_gap: int | None = None,
) -> Path:
    events = build_scenario(name=name, candidate_variant=candidate_variant, repeat=repeat, cycle_gap=cycle_gap)
    return encode_trace(output_path, events, producer_ver="python-sim-0.1")

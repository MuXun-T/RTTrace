from __future__ import annotations

from collections import Counter
from typing import Any

from .models import (
    DecodedEvent,
    ExecSlice,
    IrqSpan,
    RebuildBundle,
    ResourceGraph,
    TaskStateSeg,
    UnifiedEvent,
    UntrustedWindow,
    materialize_unified_event,
)
from .result import Result, err_result, ok_result

EventLike = UnifiedEvent | DecodedEvent
DEADLINE_PAYLOAD_KEYS = ("release_ts", "deadline_ts", "finish_ts", "deadline")


def rb_Rebuild(
    unified_events: list[EventLike],
    dataset_id: str,
    header: Any = None,
    untrusted_windows: list[UntrustedWindow] | None = None,
    capability_flags: dict[str, bool] | None = None,
    *,
    materialize_event_stream: bool = True,
    release_source_events: bool = False,
    morsel_size: int | None = None,
    parallel_workers: int | None = None,
    experimental_parallel_rebuild: bool = False,
) -> Result[RebuildBundle]:
    total_events = len(unified_events)
    if morsel_size is not None:
        try:
            morsel_size = int(morsel_size)
        except (TypeError, ValueError):
            return err_result("INVALID_ARG", "morsel_size must be a positive integer")
        if morsel_size <= 0:
            return err_result("INVALID_ARG", "morsel_size must be a positive integer")
    if parallel_workers is None:
        requested_workers = 1
    else:
        try:
            requested_workers = int(parallel_workers)
        except (TypeError, ValueError):
            return err_result("INVALID_ARG", "parallel_workers must be a positive integer")
        if requested_workers <= 0:
            return err_result("INVALID_ARG", "parallel_workers must be a positive integer")
    use_experimental_rebuild = bool(
        experimental_parallel_rebuild or morsel_size is not None or parallel_workers is not None
    )
    chunk_size = total_events or 1
    if morsel_size is not None:
        chunk_size = morsel_size
    elif use_experimental_rebuild and requested_workers > 1 and total_events > 0:
        chunk_size = max(1, (total_events + requested_workers - 1) // requested_workers)

    task_states: list[TaskStateSeg] = []
    exec_slices: list[ExecSlice] = []
    irq_spans: list[IrqSpan] = []
    resource_graph = ResourceGraph()
    windows = list(untrusted_windows or [])
    capabilities = dict(capability_flags or {})
    capabilities.setdefault("job_semantics", False)
    capabilities.setdefault("instance_semantics", False)
    capabilities.setdefault("deadline_semantics", False)
    if use_experimental_rebuild:
        capabilities["experimental_parallel_rebuild"] = True
        capabilities["experimental_parallel_requested"] = requested_workers > 1
        capabilities["experimental_parallel_active"] = False
        if requested_workers > 1:
            capabilities["experimental_parallel_fallback_serial"] = True
        if total_events > 0 and chunk_size < total_events:
            capabilities["experimental_morsel_rebuild"] = True
    release_source = bool(release_source_events and not materialize_event_stream)

    current_state: dict[int, dict[str, Any]] = {}
    running_by_core: dict[int, dict[str, Any]] = {}
    irq_stack: dict[int, list[dict[str, Any]]] = {}
    holds: dict[tuple[int, int], dict[str, Any]] = {}
    waits: dict[tuple[int, int], dict[str, Any]] = {}
    waits_by_task: dict[int, set[tuple[int, int]]] = {}
    waits_by_obj: dict[int, set[tuple[int, int]]] = {}
    holds_by_task: dict[int, set[tuple[int, int]]] = {}
    holds_by_obj: dict[int, set[tuple[int, int]]] = {}
    node_counter = Counter()

    def _remember(index_by_task: dict[int, set[tuple[int, int]]], index_by_obj: dict[int, set[tuple[int, int]]], key: tuple[int, int]) -> None:
        task_id, obj_id = key
        index_by_task.setdefault(task_id, set()).add(key)
        index_by_obj.setdefault(obj_id, set()).add(key)

    def _forget(index_by_task: dict[int, set[tuple[int, int]]], index_by_obj: dict[int, set[tuple[int, int]]], key: tuple[int, int]) -> None:
        task_id, obj_id = key
        task_keys = index_by_task.get(task_id)
        if task_keys is not None:
            task_keys.discard(key)
            if not task_keys:
                index_by_task.pop(task_id, None)
        obj_keys = index_by_obj.get(obj_id)
        if obj_keys is not None:
            obj_keys.discard(key)
            if not obj_keys:
                index_by_obj.pop(obj_id, None)

    def close_waits(task_id: int | None = None, obj_id: int | None = None, *, end_ts: float) -> None:
        if task_id is not None and obj_id is not None:
            matched = [(task_id, obj_id)] if (task_id, obj_id) in waits else []
        elif task_id is not None:
            matched = list(waits_by_task.get(task_id, set()))
        elif obj_id is not None:
            matched = list(waits_by_obj.get(obj_id, set()))
        else:
            matched = list(waits)
        for key in matched:
            entry = waits.pop(key, None)
            if entry is not None:
                entry["edge"]["t_end"] = float(end_ts)
            _forget(waits_by_task, waits_by_obj, key)

    def close_holds(task_id: int | None = None, obj_id: int | None = None, *, end_ts: float) -> None:
        if task_id is not None and obj_id is not None:
            matched = [(task_id, obj_id)] if (task_id, obj_id) in holds else []
        elif task_id is not None:
            matched = list(holds_by_task.get(task_id, set()))
        elif obj_id is not None:
            matched = list(holds_by_obj.get(obj_id, set()))
        else:
            matched = list(holds)
        for key in matched:
            entry = holds.pop(key, None)
            if entry is not None:
                entry["edge"]["t_end"] = float(end_ts)
            _forget(holds_by_task, holds_by_obj, key)

    def event_identity(event: EventLike) -> tuple[str, str]:
        if isinstance(event, UnifiedEvent):
            return event.event_uid, event.ref_key
        event_key = f"evt:{dataset_id}:{event.core_id}:{event.seq}"
        return event_key, event_key

    def transition(
        task_id: int,
        new_state: str,
        event: EventLike,
        *,
        event_uid: str,
        trusted: bool,
        related_obj: int | None = None,
    ) -> None:
        prior = current_state.get(task_id)
        if prior:
            task_states.append(
                TaskStateSeg(
                    seg_id=f"seg:{dataset_id}:{task_id}:{len(task_states)}",
                    task_id=task_id,
                    state=prior["state"],
                    job_id=prior["job_id"],
                    instance_id=prior["instance_id"],
                    t_begin=prior["t_begin"],
                    t_end=event.timestamp_aligned,
                    cause_event=prior["event_uid"],
                    related_obj=prior["related_obj"],
                    trusted=prior["trusted"],
                )
            )
        current_state[task_id] = {
            "state": new_state,
            "t_begin": event.timestamp_aligned,
            "event_uid": event_uid,
            "related_obj": related_obj,
            "job_id": event.job_id,
            "instance_id": event.instance_id,
            "trusted": trusted,
        }

    end_ts = 0.0

    def process_event_range(start: int, stop: int) -> None:
        nonlocal end_ts
        for event_index in range(start, stop):
            event = unified_events[event_index]
            if event is None:
                continue
            payload = event.payload
            end_ts = event.timestamp_aligned
            task_id = event.task_id
            obj_id = event.obj_id
            if task_id is not None:
                node_counter[f"task:{task_id}"] += 1
            if obj_id is not None:
                node_counter[f"obj:{obj_id}"] += 1
            if not capabilities["job_semantics"] and event.job_id is not None:
                capabilities["job_semantics"] = True
            if not capabilities["instance_semantics"] and event.instance_id is not None:
                capabilities["instance_semantics"] = True
            if not capabilities["deadline_semantics"] and any(key in payload for key in DEADLINE_PAYLOAD_KEYS):
                capabilities["deadline_semantics"] = True

            event_name = event.event_name
            trusted = not event.trust_tags

            if event_name == "TASK_READY":
                event_uid, _ = event_identity(event)
                transition(int(payload["task_id"]), "READY", event, event_uid=event_uid, trusted=trusted)
            elif event_name == "TASK_BLOCK":
                task_id = int(payload["task_id"])
                related_obj = payload.get("wait_obj_id")
                event_uid, event_ref = event_identity(event)
                transition(task_id, "BLOCKED", event, event_uid=event_uid, trusted=trusted, related_obj=related_obj)
                if related_obj is not None:
                    related_obj_int = int(related_obj)
                    close_waits(task_id=task_id, obj_id=related_obj_int, end_ts=event.timestamp_aligned)
                    edge = {
                        "task_id": task_id,
                        "obj_id": related_obj,
                        "owner_task_id": payload.get("owner_task_id"),
                        "from_task": task_id,
                        "to_obj": related_obj,
                        "evidence_ref": event_ref,
                        "trusted": trusted,
                        "t_begin": float(event.timestamp_aligned),
                        "t_end": float(event.timestamp_aligned),
                    }
                    waits[(task_id, related_obj_int)] = {"event": event, "edge": edge}
                    _remember(waits_by_task, waits_by_obj, (task_id, related_obj_int))
                    resource_graph.wait_edges.append(edge)
            elif event_name == "TASK_WAKEUP":
                task_id = int(payload["task_id"])
                related_obj = payload.get("obj_id")
                event_uid, _ = event_identity(event)
                transition(task_id, "READY", event, event_uid=event_uid, trusted=trusted, related_obj=related_obj)
                close_waits(
                    task_id=task_id,
                    obj_id=int(related_obj) if related_obj is not None else None,
                    end_ts=event.timestamp_aligned,
                )
            elif event_name == "TASK_EXIT":
                task_id = int(payload["task_id"])
                event_uid, _ = event_identity(event)
                transition(task_id, "EXIT", event, event_uid=event_uid, trusted=trusted)
                close_waits(task_id=task_id, end_ts=event.timestamp_aligned)
                close_holds(task_id=task_id, end_ts=event.timestamp_aligned)
            elif event_name == "CTX_SWITCH":
                core_id = int(payload["core_id"])
                prev_task = int(payload["prev_task_id"])
                next_task = int(payload["next_task_id"])
                event_uid, _ = event_identity(event)
                active = running_by_core.pop(core_id, None)
                if active:
                    exec_slices.append(
                        ExecSlice(
                            slice_id=f"slice:{dataset_id}:{active['task_id']}:{len(exec_slices)}",
                            task_id=active["task_id"],
                            core_id=core_id,
                            job_id=active["job_id"],
                            instance_id=active["instance_id"],
                            t_begin=active["t_begin"],
                            t_end=event.timestamp_aligned,
                            start_event=active["event_uid"],
                            end_event=event_uid,
                            preempted_by=next_task or None,
                            run_reason=payload.get("reason"),
                            trusted=active["trusted"] and trusted,
                        )
                    )
                if prev_task and current_state.get(prev_task, {}).get("state") == "RUNNING":
                    transition(prev_task, "READY", event, event_uid=event_uid, trusted=trusted)
                if next_task:
                    transition(next_task, "RUNNING", event, event_uid=event_uid, trusted=trusted)
                    running_by_core[core_id] = {
                        "task_id": next_task,
                        "t_begin": event.timestamp_aligned,
                        "event_uid": event_uid,
                        "job_id": event.job_id,
                        "instance_id": event.instance_id,
                        "trusted": trusted,
                    }
            elif event_name == "SYNC_LOCK":
                task_id = int(payload["task_id"])
                obj_id = int(payload["obj_id"])
                _, event_ref = event_identity(event)
                close_holds(task_id=task_id, obj_id=obj_id, end_ts=event.timestamp_aligned)
                edge = {
                    "task_id": task_id,
                    "obj_id": obj_id,
                    "from_task": task_id,
                    "to_obj": obj_id,
                    "owner_task": task_id,
                    "obj_type": payload.get("obj_type"),
                    "evidence_ref": event_ref,
                    "trusted": trusted,
                    "t_begin": float(event.timestamp_aligned),
                    "t_end": float(event.timestamp_aligned),
                }
                holds[(task_id, obj_id)] = {"event": event, "edge": edge}
                _remember(holds_by_task, holds_by_obj, (task_id, obj_id))
                resource_graph.hold_edges.append(edge)
            elif event_name == "SYNC_UNLOCK":
                task_id = int(payload["task_id"])
                obj_id = int(payload["obj_id"])
                close_holds(task_id=task_id, obj_id=obj_id, end_ts=event.timestamp_aligned)
            elif event_name == "IRQ_ENTER":
                event_uid, _ = event_identity(event)
                irq_stack.setdefault(event.core_id, []).append(
                    {
                        "irq_id": int(payload["irq_id"]),
                        "nesting_depth": int(payload["nesting_depth"]),
                        "t_begin": event.timestamp_aligned,
                        "event_uid": event_uid,
                        "trusted": trusted,
                    }
                )
            elif event_name == "IRQ_EXIT":
                stack = irq_stack.setdefault(event.core_id, [])
                if stack:
                    entry = stack.pop()
                    irq_spans.append(
                        IrqSpan(
                            irq_span_id=f"irq:{dataset_id}:{event.core_id}:{len(irq_spans)}",
                            irq_id=entry["irq_id"],
                            core_id=event.core_id,
                            nesting_depth=entry["nesting_depth"],
                            t_begin=entry["t_begin"],
                            t_end=event.timestamp_aligned,
                            delayed_task=running_by_core.get(event.core_id, {}).get("task_id"),
                            trusted=entry["trusted"] and trusted,
                        )
                    )
                else:
                    windows.append(
                        UntrustedWindow(
                            window_id=f"uw:{dataset_id}:irq_exit:{event.core_id}:{event.seq}",
                            source="open_relation",
                            scope="rebuild",
                            t_begin=event.timestamp_aligned,
                            t_end=event.timestamp_aligned,
                            reason_code="IRQ_EXIT_WITHOUT_ENTER",
                            severity="warning",
                        )
                    )
            if release_source:
                unified_events[event_index] = None  # type: ignore[list-item]

    if use_experimental_rebuild:
        # ponytail: real parallel state merge waits for broader parity coverage.
        for start in range(0, total_events, chunk_size):
            process_event_range(start, min(start + chunk_size, total_events))
    else:
        process_event_range(0, total_events)

    if release_source:
        unified_events.clear()
    for task_id, state in current_state.items():
        task_states.append(
            TaskStateSeg(
                seg_id=f"seg:{dataset_id}:{task_id}:{len(task_states)}",
                task_id=task_id,
                state=state["state"],
                job_id=state["job_id"],
                instance_id=state["instance_id"],
                t_begin=state["t_begin"],
                t_end=end_ts,
                cause_event=state["event_uid"],
                related_obj=state["related_obj"],
                trusted=state["trusted"],
            )
        )
    for core_id, active in running_by_core.items():
        exec_slices.append(
            ExecSlice(
                slice_id=f"slice:{dataset_id}:{active['task_id']}:{len(exec_slices)}",
                task_id=active["task_id"],
                core_id=core_id,
                job_id=active["job_id"],
                instance_id=active["instance_id"],
                t_begin=active["t_begin"],
                t_end=end_ts,
                start_event=active["event_uid"],
                end_event=active["event_uid"],
                preempted_by=None,
                run_reason=None,
                trusted=active["trusted"],
            )
        )
    for core_id, stack in irq_stack.items():
        for entry in stack:
            windows.append(
                UntrustedWindow(
                    window_id=f"uw:{dataset_id}:irq_open:{core_id}:{entry['irq_id']}",
                    source="open_relation",
                    scope="rebuild",
                    t_begin=entry["t_begin"],
                    t_end=end_ts,
                    reason_code="IRQ_ENTER_WITHOUT_EXIT",
                    severity="warning",
                )
            )
            capabilities["irq_closed"] = False
    if holds:
        capabilities["resource_closed"] = False
        for (task_id, obj_id), entry in holds.items():
            entry["edge"]["t_end"] = float(end_ts)
            event = entry["event"]
            windows.append(
                UntrustedWindow(
                    window_id=f"uw:{dataset_id}:hold_open:{task_id}:{obj_id}",
                    source="open_relation",
                    scope="rebuild",
                    t_begin=event.timestamp_aligned,
                    t_end=end_ts,
                    reason_code="LOCK_WITHOUT_UNLOCK",
                    severity="warning",
                )
            )
    if waits:
        capabilities["resource_closed"] = False
        for (task_id, obj_id), entry in waits.items():
            entry["edge"]["t_end"] = float(end_ts)
            event = entry["event"]
            windows.append(
                UntrustedWindow(
                    window_id=f"uw:{dataset_id}:wait_open:{task_id}:{obj_id}",
                    source="open_relation",
                    scope="rebuild",
                    t_begin=event.timestamp_aligned,
                    t_end=end_ts,
                    reason_code="WAIT_WITHOUT_WAKEUP",
                    severity="warning",
                )
            )

    resource_graph.nodes = [{"node_id": node_id, "count": count} for node_id, count in node_counter.items()]
    resource_graph.hotspot_stats = sorted(
        [{"node_id": node_id, "count": count} for node_id, count in node_counter.items()],
        key=lambda item: (-item["count"], item["node_id"]),
    )[:10]

    task_states.sort(key=lambda item: (item.t_begin, item.task_id))
    exec_slices.sort(key=lambda item: (item.t_begin, item.core_id, item.task_id))
    irq_spans.sort(key=lambda item: (item.t_begin, item.core_id))
    windows.sort(key=lambda item: (item.t_begin, item.t_end, item.window_id))

    event_stream = (
        [materialize_unified_event(event, dataset_id) for event in unified_events]
        if materialize_event_stream
        else []
    )

    bundle = RebuildBundle(
        bundle_id=f"bundle:{dataset_id}",
        dataset_id=dataset_id,
        event_stream=event_stream,
        task_states=task_states,
        exec_slices=exec_slices,
        resource_graph=resource_graph,
        irq_spans=irq_spans,
        untrusted_windows=windows,
        rebuild_rev=1,
        capability_flags=capabilities,
        header=header,
    )
    return ok_result(bundle, untrusted_windows=bundle.untrusted_windows)

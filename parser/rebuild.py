from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any, Iterable

from .models import (
    DecodedEvent,
    ExecSlice,
    IrqSpan,
    ReadyNotRunningInterval,
    RebuildBundle,
    ResourceEdge,
    ResourceGraph,
    TaskStateSeg,
    UnifiedEvent,
    UntrustedWindow,
    materialize_unified_event,
)
from .result import Result, err_result, ok_result
from .rtd_lineage import (
    ALIGNMENT_DEGRADED_REASON,
    CAPABILITY_UNSUPPORTED_REASON,
    MAPPING_INVALID_REASON,
    MISSING_CAPTURE_LINEAGE_CONTEXT_REASON,
    MISSING_CCM_REF_REASON,
    MISSING_CIR_REF_REASON,
    NON_NORMAL_CLOSE_BOUNDARY_REASON,
    SOURCE_INTEGRITY_AFFECTED_REASON,
    CIRWindowBinding,
    CaptureLineageContext,
    EvidenceLineage,
    LineageRegistry,
    LineageStatus,
    LineageValidationError,
    derive_lineage_status,
    validate_capture_lineage_context,
    validate_rebuild_bundle_lineage,
)


EventLike = UnifiedEvent | DecodedEvent
DEADLINE_PAYLOAD_KEYS = ("release_ts", "deadline_ts", "finish_ts", "deadline")
LINEAGE_VERSION = "rtd-lineage-v1.0"


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
    lineage_context: CaptureLineageContext | None = None,
) -> Result[RebuildBundle]:
    if lineage_context is not None:
        try:
            validate_capture_lineage_context(lineage_context)
        except LineageValidationError as exc:
            return err_result("INVALID_ARG", str(exc))

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
    ready_not_running: list[ReadyNotRunningInterval] = []
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
    pending_dispatch: dict[tuple[int, int], dict[str, Any]] = {}
    irq_stack: dict[int, list[dict[str, Any]]] = {}
    holds: dict[tuple[int, int], dict[str, Any]] = {}
    waits: dict[tuple[int, int], dict[str, Any]] = {}
    waits_by_task: dict[int, set[tuple[int, int]]] = {}
    waits_by_obj: dict[int, set[tuple[int, int]]] = {}
    holds_by_task: dict[int, set[tuple[int, int]]] = {}
    holds_by_obj: dict[int, set[tuple[int, int]]] = {}
    node_counter: Counter[str] = Counter()
    edge_counter: Counter[str] = Counter()
    lineage_drafts: list[dict[str, Any]] = []
    lineage_draft_by_object: dict[str, dict[str, Any]] = {}
    registry = LineageRegistry(lineage_context)
    event_tags: dict[str, tuple[str, ...]] = {}
    processed_observations: set[tuple[str, float]] = set()

    def event_identity(event: EventLike) -> tuple[str, str]:
        if isinstance(event, UnifiedEvent):
            return event.event_uid, event.ref_key
        event_key = f"evt:{dataset_id}:{event.core_id}:{event.seq}"
        return event_key, event_key

    try:
        for event in unified_events:
            event_uid, _ = event_identity(event)
            registry.register_raw_event(event_uid, event.timestamp_aligned)
            event_tags[event_uid] = tuple(sorted(set(event_tags.get(event_uid, ())) | set(event.trust_tags)))
    except LineageValidationError as exc:
        return err_result("INVALID_ARG", str(exc))

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

    def _stage_lineage(
        obj: Any,
        *,
        object_id: str,
        object_type: str,
        rule_id: str,
        source_event_ids: Iterable[str],
        open_event_id: str | None,
        close_event_id: str | None,
        observed_horizon_start: float,
        observed_horizon_end: float,
        required_event_names: Iterable[str],
        reason_codes: Iterable[str] = (),
    ) -> None:
        lineage_id = f"lineage:{object_type}:{object_id}"
        obj.lineage_id = lineage_id
        draft = {
            "lineage_id": lineage_id,
            "object_id": object_id,
            "object_type": object_type,
            "rule_id": rule_id,
            "source_event_ids": tuple(dict.fromkeys(source_event_ids)),
            "open_event_id": open_event_id,
            "close_event_id": close_event_id,
            "observed_horizon_start": float(observed_horizon_start),
            "observed_horizon_end": float(observed_horizon_end),
            "required_event_names": tuple(dict.fromkeys(required_event_names)),
            "reason_codes": tuple(dict.fromkeys(reason_codes)),
        }
        lineage_drafts.append(draft)
        lineage_draft_by_object[object_id] = draft

    def _add_window(reason_code: str, t_begin: float, t_end: float, token: str) -> None:
        windows.append(
            UntrustedWindow(
                window_id=f"uw:{dataset_id}:{reason_code.lower()}:{token}:{len(windows)}",
                source="open_relation",
                scope="rebuild",
                t_begin=float(t_begin),
                t_end=float(t_end),
                reason_code=reason_code,
                severity="warning",
            )
        )

    def _make_edge_id(kind: str, task_id: int | None, obj_id: int | None) -> str:
        edge_counter[kind] += 1
        return f"edge:{kind}:{dataset_id}:{task_id}:{obj_id}:{edge_counter[kind] - 1}"

    def _stage_resource(
        entry: dict[str, Any],
        *,
        close_event: EventLike | None,
        close_kind: str = "NORMAL",
        reason_codes: Iterable[str] = (),
        observed_end: float,
    ) -> None:
        edge: ResourceEdge = entry["edge"]
        close_event_id = event_identity(close_event)[0] if close_event is not None else None
        source_ids = [entry["open_event_id"]]
        required_names = [entry["open_event_name"]]
        if close_event_id is not None:
            source_ids.append(close_event_id)
            required_names.append(close_event.event_name)
        reasons = list(reason_codes)
        if close_kind != "NORMAL":
            reasons.append(NON_NORMAL_CLOSE_BOUNDARY_REASON)
        _stage_lineage(
            edge,
            object_id=edge.edge_id,
            object_type=f"resource_{edge.edge_kind}",
            rule_id=f"rtd.rebuild.resource_{edge.edge_kind}",
            source_event_ids=source_ids,
            open_event_id=entry["open_event_id"],
            close_event_id=close_event_id,
            observed_horizon_start=entry["open_timestamp"],
            observed_horizon_end=observed_end,
            required_event_names=required_names,
            reason_codes=reasons,
        )

    def _close_relations(
        entries: dict[tuple[int, int], dict[str, Any]],
        index_by_task: dict[int, set[tuple[int, int]]],
        index_by_obj: dict[int, set[tuple[int, int]]],
        *,
        task_id: int | None,
        obj_id: int | None,
        event: EventLike,
        close_kind: str = "NORMAL",
        reason_code: str | None = None,
    ) -> int:
        if task_id is not None and obj_id is not None:
            matched = [(task_id, obj_id)] if (task_id, obj_id) in entries else []
        elif task_id is not None:
            matched = list(index_by_task.get(task_id, set()))
        elif obj_id is not None:
            matched = list(index_by_obj.get(obj_id, set()))
        else:
            matched = list(entries)
        for key in matched:
            entry = entries.pop(key, None)
            if entry is None:
                continue
            edge: ResourceEdge = entry["edge"]
            edge.t_end = float(event.timestamp_aligned)
            if reason_code is not None:
                _add_window(reason_code, entry["open_timestamp"], event.timestamp_aligned, edge.edge_id)
            _stage_resource(
                entry,
                close_event=event,
                close_kind=close_kind,
                reason_codes=(() if reason_code is None else (reason_code,)),
                observed_end=event.timestamp_aligned,
            )
            _forget(index_by_task, index_by_obj, key)
        return len(matched)

    def _transition(
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
            seg = TaskStateSeg(
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
            task_states.append(seg)
            _stage_lineage(
                seg,
                object_id=seg.seg_id,
                object_type="task_state_segment",
                rule_id="rtd.rebuild.task_state_segment",
                source_event_ids=(prior["event_uid"], event_uid),
                open_event_id=prior["event_uid"],
                close_event_id=event_uid,
                observed_horizon_start=prior["t_begin"],
                observed_horizon_end=event.timestamp_aligned,
                required_event_names=(prior["event_name"], event.event_name),
            )
        current_state[task_id] = {
            "state": new_state,
            "t_begin": event.timestamp_aligned,
            "event_uid": event_uid,
            "event_name": event.event_name,
            "related_obj": related_obj,
            "job_id": event.job_id,
            "instance_id": event.instance_id,
            "trusted": trusted,
        }

    def _new_resource_edge(
        kind: str,
        event: EventLike,
        *,
        task_id: int,
        obj_id: int,
        owner_task_id: int | None,
        obj_type: int | None,
    ) -> dict[str, Any]:
        event_uid, event_ref = event_identity(event)
        edge = ResourceEdge(
            edge_id=_make_edge_id(kind, task_id, obj_id),
            edge_kind=kind,
            task_id=task_id,
            obj_id=obj_id,
            owner_task_id=owner_task_id,
            obj_type=obj_type,
            t_begin=float(event.timestamp_aligned),
            t_end=float(event.timestamp_aligned),
            evidence_ref=event_ref,
            trusted=not event.trust_tags,
        )
        return {
            "edge": edge,
            "open_event_id": event_uid,
            "open_event_name": event.event_name,
            "open_timestamp": float(event.timestamp_aligned),
        }

    end_ts = 0.0

    def process_event_range(start: int, stop: int) -> None:
        nonlocal end_ts
        for event_index in range(start, stop):
            event = unified_events[event_index]
            event_uid, event_ref = event_identity(event)
            observation_key = (event_uid, float(event.timestamp_aligned))
            if observation_key in processed_observations:
                if release_source:
                    unified_events[event_index] = None  # type: ignore[list-item]
                continue
            processed_observations.add(observation_key)
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
                _transition(int(payload["task_id"]), "READY", event, event_uid=event_uid, trusted=trusted)
            elif event_name == "TASK_BLOCK":
                task_value = int(payload["task_id"])
                related_obj = payload.get("wait_obj_id")
                _transition(task_value, "BLOCKED", event, event_uid=event_uid, trusted=trusted, related_obj=related_obj)
                if related_obj is not None:
                    related_obj_int = int(related_obj)
                    _close_relations(
                        waits,
                        waits_by_task,
                        waits_by_obj,
                        task_id=task_value,
                        obj_id=related_obj_int,
                        event=event,
                        close_kind="SUCCESSOR_OPEN",
                        reason_code="WAIT_WITHOUT_WAKEUP",
                    )
                    entry = _new_resource_edge(
                        "wait",
                        event,
                        task_id=task_value,
                        obj_id=related_obj_int,
                        owner_task_id=payload.get("owner_task_id"),
                        obj_type=None,
                    )
                    waits[(task_value, related_obj_int)] = entry
                    _remember(waits_by_task, waits_by_obj, (task_value, related_obj_int))
                    resource_graph.wait_edges.append(entry["edge"])
            elif event_name == "TASK_WAKEUP":
                task_value = int(payload["task_id"])
                related_obj = payload.get("obj_id")
                _transition(task_value, "READY", event, event_uid=event_uid, trusted=trusted, related_obj=related_obj)
                closed = _close_relations(
                    waits,
                    waits_by_task,
                    waits_by_obj,
                    task_id=task_value,
                    obj_id=int(related_obj) if related_obj is not None else None,
                    event=event,
                )
                if closed == 0:
                    edge = ResourceEdge(
                        edge_id=_make_edge_id("wait", task_value, int(related_obj) if related_obj is not None else None),
                        edge_kind="wait",
                        task_id=task_value,
                        obj_id=int(related_obj) if related_obj is not None else None,
                        owner_task_id=None,
                        obj_type=None,
                        t_begin=float(event.timestamp_aligned),
                        t_end=float(event.timestamp_aligned),
                        evidence_ref=event_ref,
                        trusted=trusted,
                    )
                    resource_graph.wait_edges.append(edge)
                    _add_window("WAKEUP_WITHOUT_BLOCK", event.timestamp_aligned, event.timestamp_aligned, edge.edge_id)
                    _stage_lineage(
                        edge,
                        object_id=edge.edge_id,
                        object_type="resource_wait",
                        rule_id="rtd.rebuild.resource_wait",
                        source_event_ids=(event_uid,),
                        open_event_id=None,
                        close_event_id=event_uid,
                        observed_horizon_start=event.timestamp_aligned,
                        observed_horizon_end=event.timestamp_aligned,
                        required_event_names=(event_name,),
                        reason_codes=("WAKEUP_WITHOUT_BLOCK",),
                    )
            elif event_name == "TASK_EXIT":
                task_value = int(payload["task_id"])
                _transition(task_value, "EXIT", event, event_uid=event_uid, trusted=trusted)
                _close_relations(
                    waits,
                    waits_by_task,
                    waits_by_obj,
                    task_id=task_value,
                    obj_id=None,
                    event=event,
                    close_kind="TERMINAL_TASK_EXIT",
                    reason_code="WAIT_WITHOUT_WAKEUP",
                )
                _close_relations(
                    holds,
                    holds_by_task,
                    holds_by_obj,
                    task_id=task_value,
                    obj_id=None,
                    event=event,
                    close_kind="TERMINAL_TASK_EXIT",
                    reason_code="LOCK_WITHOUT_UNLOCK",
                )
            elif event_name == "TASK_DISPATCH":
                task_value = int(payload["task_id"])
                core_value = int(payload.get("core_id", event.core_id))
                pending_dispatch[(task_value, core_value)] = {
                    "event_uid": event_uid,
                    "event_name": event_name,
                    "trusted": trusted,
                }
            elif event_name == "CTX_SWITCH":
                core_id = int(payload["core_id"])
                prev_task = int(payload["prev_task_id"])
                next_task = int(payload["next_task_id"])
                active = running_by_core.pop(core_id, None)
                if active:
                    item = ExecSlice(
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
                    exec_slices.append(item)
                    _stage_lineage(
                        item,
                        object_id=item.slice_id,
                        object_type="exec_slice",
                        rule_id="rtd.rebuild.exec_slice",
                        source_event_ids=tuple(
                            source_event_id
                            for source_event_id in (active.get("dispatch_event_uid"), active["event_uid"], event_uid)
                            if source_event_id is not None
                        ),
                        open_event_id=active["event_uid"],
                        close_event_id=event_uid,
                        observed_horizon_start=active["t_begin"],
                        observed_horizon_end=event.timestamp_aligned,
                        required_event_names=tuple(
                            source_event_name
                            for source_event_name in (active.get("dispatch_event_name"), active["event_name"], event_name)
                            if source_event_name is not None
                        ),
                    )
                if prev_task and current_state.get(prev_task, {}).get("state") == "RUNNING":
                    _transition(prev_task, "READY", event, event_uid=event_uid, trusted=trusted)
                if next_task:
                    dispatch = pending_dispatch.pop((next_task, core_id), None)
                    _transition(next_task, "RUNNING", event, event_uid=event_uid, trusted=trusted)
                    running_by_core[core_id] = {
                        "task_id": next_task,
                        "t_begin": event.timestamp_aligned,
                        "event_uid": event_uid,
                        "event_name": event_name,
                        "job_id": event.job_id,
                        "instance_id": event.instance_id,
                        "trusted": trusted if dispatch is None else trusted and dispatch["trusted"],
                        "dispatch_event_uid": None if dispatch is None else dispatch["event_uid"],
                        "dispatch_event_name": None if dispatch is None else dispatch["event_name"],
                    }
            elif event_name == "SYNC_LOCK":
                task_value = int(payload["task_id"])
                obj_value = int(payload["obj_id"])
                _close_relations(
                    holds,
                    holds_by_task,
                    holds_by_obj,
                    task_id=task_value,
                    obj_id=obj_value,
                    event=event,
                    close_kind="SUCCESSOR_OPEN",
                    reason_code="LOCK_WITHOUT_UNLOCK",
                )
                entry = _new_resource_edge(
                    "hold",
                    event,
                    task_id=task_value,
                    obj_id=obj_value,
                    owner_task_id=task_value,
                    obj_type=payload.get("obj_type"),
                )
                holds[(task_value, obj_value)] = entry
                _remember(holds_by_task, holds_by_obj, (task_value, obj_value))
                resource_graph.hold_edges.append(entry["edge"])
            elif event_name == "SYNC_UNLOCK":
                task_value = int(payload["task_id"])
                obj_value = int(payload["obj_id"])
                closed = _close_relations(
                    holds,
                    holds_by_task,
                    holds_by_obj,
                    task_id=task_value,
                    obj_id=obj_value,
                    event=event,
                )
                if closed == 0:
                    edge = ResourceEdge(
                        edge_id=_make_edge_id("hold", task_value, obj_value),
                        edge_kind="hold",
                        task_id=task_value,
                        obj_id=obj_value,
                        owner_task_id=task_value,
                        obj_type=None,
                        t_begin=float(event.timestamp_aligned),
                        t_end=float(event.timestamp_aligned),
                        evidence_ref=event_ref,
                        trusted=trusted,
                    )
                    resource_graph.hold_edges.append(edge)
                    _add_window("UNLOCK_WITHOUT_LOCK", event.timestamp_aligned, event.timestamp_aligned, edge.edge_id)
                    _stage_lineage(
                        edge,
                        object_id=edge.edge_id,
                        object_type="resource_hold",
                        rule_id="rtd.rebuild.resource_hold",
                        source_event_ids=(event_uid,),
                        open_event_id=None,
                        close_event_id=event_uid,
                        observed_horizon_start=event.timestamp_aligned,
                        observed_horizon_end=event.timestamp_aligned,
                        required_event_names=(event_name,),
                        reason_codes=("UNLOCK_WITHOUT_LOCK",),
                    )
            elif event_name == "IRQ_ENTER":
                irq_stack.setdefault(event.core_id, []).append(
                    {
                        "irq_id": int(payload["irq_id"]),
                        "nesting_depth": int(payload["nesting_depth"]),
                        "t_begin": event.timestamp_aligned,
                        "event_uid": event_uid,
                        "event_name": event_name,
                        "trusted": trusted,
                    }
                )
            elif event_name == "IRQ_EXIT":
                stack = irq_stack.setdefault(event.core_id, [])
                irq_id = int(payload["irq_id"])
                nesting_depth = int(payload["nesting_depth"])
                if stack and stack[-1]["irq_id"] == irq_id and stack[-1]["nesting_depth"] == nesting_depth:
                    entry = stack.pop()
                    item = IrqSpan(
                        irq_span_id=f"irq:{dataset_id}:{event.core_id}:{len(irq_spans)}",
                        irq_id=entry["irq_id"],
                        core_id=event.core_id,
                        nesting_depth=entry["nesting_depth"],
                        t_begin=entry["t_begin"],
                        t_end=event.timestamp_aligned,
                        delayed_task=running_by_core.get(event.core_id, {}).get("task_id"),
                        trusted=entry["trusted"] and trusted,
                        enter_event_id=entry["event_uid"],
                        exit_event_id=event_uid,
                    )
                    irq_spans.append(item)
                    _stage_lineage(
                        item,
                        object_id=item.irq_span_id,
                        object_type="irq_span",
                        rule_id="rtd.rebuild.irq_span",
                        source_event_ids=(entry["event_uid"], event_uid),
                        open_event_id=entry["event_uid"],
                        close_event_id=event_uid,
                        observed_horizon_start=entry["t_begin"],
                        observed_horizon_end=event.timestamp_aligned,
                        required_event_names=(entry["event_name"], event_name),
                    )
                else:
                    _add_window("IRQ_EXIT_WITHOUT_MATCHING_ENTER", event.timestamp_aligned, event.timestamp_aligned, event_uid)
                    item = IrqSpan(
                        irq_span_id=f"irq:{dataset_id}:{event.core_id}:{len(irq_spans)}",
                        irq_id=irq_id,
                        core_id=event.core_id,
                        nesting_depth=nesting_depth,
                        t_begin=event.timestamp_aligned,
                        t_end=event.timestamp_aligned,
                        delayed_task=running_by_core.get(event.core_id, {}).get("task_id"),
                        trusted=trusted,
                        enter_event_id=None,
                        exit_event_id=event_uid,
                    )
                    irq_spans.append(item)
                    _stage_lineage(
                        item,
                        object_id=item.irq_span_id,
                        object_type="irq_span",
                        rule_id="rtd.rebuild.irq_span",
                        source_event_ids=(event_uid,),
                        open_event_id=None,
                        close_event_id=event_uid,
                        observed_horizon_start=event.timestamp_aligned,
                        observed_horizon_end=event.timestamp_aligned,
                        required_event_names=(event_name,),
                        reason_codes=("IRQ_EXIT_WITHOUT_MATCHING_ENTER",),
                    )
            if release_source:
                unified_events[event_index] = None  # type: ignore[list-item]

    if use_experimental_rebuild:
        # Real parallel state merge remains intentionally outside this P3 change.
        for start in range(0, total_events, chunk_size):
            process_event_range(start, min(start + chunk_size, total_events))
    else:
        process_event_range(0, total_events)

    if release_source:
        unified_events.clear()
    for task_id, state in current_state.items():
        seg = TaskStateSeg(
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
        task_states.append(seg)
        _stage_lineage(
            seg,
            object_id=seg.seg_id,
            object_type="task_state_segment",
            rule_id="rtd.rebuild.task_state_segment",
            source_event_ids=(state["event_uid"],),
            open_event_id=state["event_uid"],
            close_event_id=None,
            observed_horizon_start=state["t_begin"],
            observed_horizon_end=end_ts,
            required_event_names=(state["event_name"],),
        )
    for core_id, active in running_by_core.items():
        item = ExecSlice(
            slice_id=f"slice:{dataset_id}:{active['task_id']}:{len(exec_slices)}",
            task_id=active["task_id"],
            core_id=core_id,
            job_id=active["job_id"],
            instance_id=active["instance_id"],
            t_begin=active["t_begin"],
            t_end=end_ts,
            start_event=active["event_uid"],
            end_event=None,
            preempted_by=None,
            run_reason=None,
            trusted=active["trusted"],
        )
        exec_slices.append(item)
        _stage_lineage(
            item,
            object_id=item.slice_id,
            object_type="exec_slice",
            rule_id="rtd.rebuild.exec_slice",
            source_event_ids=tuple(
                source_event_id
                for source_event_id in (active.get("dispatch_event_uid"), active["event_uid"])
                if source_event_id is not None
            ),
            open_event_id=active["event_uid"],
            close_event_id=None,
            observed_horizon_start=active["t_begin"],
            observed_horizon_end=end_ts,
            required_event_names=tuple(
                source_event_name
                for source_event_name in (active.get("dispatch_event_name"), active["event_name"])
                if source_event_name is not None
            ),
        )
    for core_id, stack in irq_stack.items():
        for entry in stack:
            _add_window("IRQ_ENTER_WITHOUT_EXIT", entry["t_begin"], end_ts, entry["event_uid"])
            capabilities["irq_closed"] = False
            item = IrqSpan(
                irq_span_id=f"irq:{dataset_id}:{core_id}:{len(irq_spans)}",
                irq_id=entry["irq_id"],
                core_id=core_id,
                nesting_depth=entry["nesting_depth"],
                t_begin=entry["t_begin"],
                t_end=end_ts,
                delayed_task=running_by_core.get(core_id, {}).get("task_id"),
                trusted=entry["trusted"],
                enter_event_id=entry["event_uid"],
                exit_event_id=None,
            )
            irq_spans.append(item)
            _stage_lineage(
                item,
                object_id=item.irq_span_id,
                object_type="irq_span",
                rule_id="rtd.rebuild.irq_span",
                source_event_ids=(entry["event_uid"],),
                open_event_id=entry["event_uid"],
                close_event_id=None,
                observed_horizon_start=entry["t_begin"],
                observed_horizon_end=end_ts,
                required_event_names=(entry["event_name"],),
            )

    def _finalize_open_relations(
        entries: dict[tuple[int, int], dict[str, Any]],
        *,
        reason_code: str,
    ) -> None:
        if entries:
            capabilities["resource_closed"] = False
        for entry in entries.values():
            edge: ResourceEdge = entry["edge"]
            edge.t_end = float(end_ts)
            _add_window(reason_code, entry["open_timestamp"], end_ts, edge.edge_id)
            _stage_resource(
                entry,
                close_event=None,
                reason_codes=(reason_code,),
                observed_end=end_ts,
            )

    _finalize_open_relations(holds, reason_code="LOCK_WITHOUT_UNLOCK")
    _finalize_open_relations(waits, reason_code="WAIT_WITHOUT_WAKEUP")

    task_states.sort(key=lambda item: (item.t_begin, item.task_id))
    exec_slices.sort(key=lambda item: (item.t_begin, item.core_id, item.task_id))
    irq_spans.sort(key=lambda item: (item.t_begin, item.core_id, item.irq_span_id))

    for segment in task_states:
        if segment.state != "READY":
            continue
        source_draft = lineage_draft_by_object[segment.seg_id]
        competing_slices = [
            item
            for item in exec_slices
            if item.task_id != segment.task_id and item.t_begin <= segment.t_end and segment.t_begin <= item.t_end
        ]
        competing_irqs = [
            item for item in irq_spans if item.t_begin <= segment.t_end and segment.t_begin <= item.t_end
        ]
        competing_drafts = [
            lineage_draft_by_object[item.slice_id] for item in competing_slices
        ] + [lineage_draft_by_object[item.irq_span_id] for item in competing_irqs]
        interval = ReadyNotRunningInterval(
            interval_id=f"ready:{segment.seg_id}",
            task_id=segment.task_id,
            core_id=None,
            t_begin=segment.t_begin,
            t_end=segment.t_end,
            competing_exec_slice_ids=[item.slice_id for item in competing_slices],
            competing_irq_span_ids=[item.irq_span_id for item in competing_irqs],
            trusted=segment.trusted,
        )
        ready_not_running.append(interval)
        _stage_lineage(
            interval,
            object_id=interval.interval_id,
            object_type="ready_not_running",
            rule_id="rtd.rebuild.ready_not_running",
            source_event_ids=tuple(
                dict.fromkeys(
                    event_id
                    for draft in [source_draft, *competing_drafts]
                    for event_id in draft["source_event_ids"]
                )
            ),
            open_event_id=source_draft["open_event_id"],
            close_event_id=source_draft["close_event_id"],
            observed_horizon_start=source_draft["observed_horizon_start"],
            observed_horizon_end=source_draft["observed_horizon_end"],
            required_event_names=tuple(
                dict.fromkeys(
                    event_name
                    for draft in [source_draft, *competing_drafts]
                    for event_name in draft["required_event_names"]
                )
            ),
            reason_codes=tuple(
                dict.fromkeys(
                    reason_code
                    for draft in [source_draft, *competing_drafts]
                    for reason_code in draft["reason_codes"]
                )
            ),
        )

    windows.sort(key=lambda item: (item.t_begin, item.t_end, item.window_id))
    try:
        for window in windows:
            if lineage_context is None:
                binding = CIRWindowBinding(
                    window_id=window.window_id,
                    interval_start=window.t_begin,
                    interval_end=window.t_end,
                    reason_code=window.reason_code,
                    capture_integrity_record_ref=None,
                    capture_integrity_record_digest=None,
                    capture_integrity_record_schema_version=None,
                )
            else:
                binding = CIRWindowBinding(
                    window_id=window.window_id,
                    interval_start=window.t_begin,
                    interval_end=window.t_end,
                    reason_code=window.reason_code,
                    capture_integrity_record_ref=lineage_context.capture_integrity_record_ref,
                    capture_integrity_record_digest=lineage_context.capture_integrity_record_digest,
                    capture_integrity_record_schema_version=lineage_context.capture_integrity_record_schema_version,
                    capture_id=lineage_context.capture_id,
                )
            registry.register_cir_window(binding)
        for draft in lineage_drafts:
            context_reasons: list[str] = list(draft["reason_codes"])
            if lineage_context is None:
                context_reasons.extend(
                    (
                        CAPABILITY_UNSUPPORTED_REASON,
                        MISSING_CAPTURE_LINEAGE_CONTEXT_REASON,
                        MISSING_CCM_REF_REASON,
                        MISSING_CIR_REF_REASON,
                    )
                )
            else:
                if lineage_context.mapping_valid is not True:
                    context_reasons.append(MAPPING_INVALID_REASON)
                if lineage_context.sampling_boundary_complete is not True:
                    context_reasons.append(CAPABILITY_UNSUPPORTED_REASON)
                if set(draft["required_event_names"]).difference(lineage_context.event_types_enabled):
                    context_reasons.append(CAPABILITY_UNSUPPORTED_REASON)
            if any(len(registry.raw_events[event_id].timestamps) > 1 for event_id in draft["source_event_ids"]):
                context_reasons.append(MAPPING_INVALID_REASON)
            source_issues = tuple(
                event_id for event_id in draft["source_event_ids"] if event_tags.get(event_id)
            )
            if source_issues:
                context_reasons.append(SOURCE_INTEGRITY_AFFECTED_REASON)
            interval_start = (
                draft["observed_horizon_start"]
                if draft["open_event_id"] is not None
                else None
            )
            interval_end = (
                draft["observed_horizon_end"]
                if draft["close_event_id"] is not None
                else None
            )
            provisional_status = derive_lineage_status(
                open_event_id=draft["open_event_id"],
                close_event_id=draft["close_event_id"],
                reason_codes=tuple(dict.fromkeys(context_reasons)),
            )
            # Window intersections must be computed before LOSS_AFFECTED can
            # be validated.  Keep the provisional record structurally
            # valid, then derive the final status from the resolved windows.
            if provisional_status is LineageStatus.LOSS_AFFECTED:
                provisional_status = LineageStatus.COMPLETE
            provisional = EvidenceLineage(
                lineage_id=draft["lineage_id"],
                derived_object_id=draft["object_id"],
                derived_object_type=draft["object_type"],
                capture_context=lineage_context,
                source_event_ids=draft["source_event_ids"],
                open_event_id=draft["open_event_id"],
                close_event_id=draft["close_event_id"],
                boundary_event_ids=draft["source_event_ids"],
                derivation_rule_id=draft["rule_id"],
                derivation_version=LINEAGE_VERSION,
                interval_start=interval_start,
                interval_end=interval_end,
                observed_horizon_start=draft["observed_horizon_start"],
                observed_horizon_end=draft["observed_horizon_end"],
                all_intersecting_untrusted_window_ids=(),
                boundary_intersecting_untrusted_window_ids=(),
                lineage_status=provisional_status,
                source_integrity_issue_ids=source_issues,
                reason_codes=tuple(dict.fromkeys(context_reasons)),
            )
            all_windows = registry.intersecting_window_ids(provisional)
            boundary_windows = registry.boundary_intersecting_window_ids(provisional)
            matching_bindings = tuple(registry.cir_window_bindings[window_id] for window_id in all_windows)
            lineage = replace(
                provisional,
                all_intersecting_untrusted_window_ids=all_windows,
                boundary_intersecting_untrusted_window_ids=boundary_windows,
                lineage_status=derive_lineage_status(
                    open_event_id=provisional.open_event_id,
                    close_event_id=provisional.close_event_id,
                    reason_codes=provisional.reason_codes,
                    intersecting_windows=matching_bindings,
                ),
            )
            registry.register_lineage(lineage)
        registry.validate()
    except LineageValidationError as exc:
        return err_result("LINEAGE_INVALID", str(exc))

    resource_graph.nodes = [{"node_id": node_id, "count": count} for node_id, count in node_counter.items()]
    resource_graph.hotspot_stats = sorted(
        [{"node_id": node_id, "count": count} for node_id, count in node_counter.items()],
        key=lambda item: (-item["count"], item["node_id"]),
    )[:10]
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
        capture_id=None if lineage_context is None else lineage_context.capture_id,
        capture_capability_manifest_ref=(
            None if lineage_context is None else lineage_context.capture_capability_manifest_ref
        ),
        capture_integrity_record_ref=(
            None if lineage_context is None else lineage_context.capture_integrity_record_ref
        ),
        ready_not_running_intervals=ready_not_running,
        lineage_registry=registry,
    )
    try:
        validate_rebuild_bundle_lineage(
            bundle,
            raw_events=event_stream if materialize_event_stream else None,
            require_complete_raw_event_set=materialize_event_stream,
        )
    except LineageValidationError as exc:
        return err_result("LINEAGE_INVALID", str(exc))
    return ok_result(bundle, untrusted_windows=bundle.untrusted_windows)

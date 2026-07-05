from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from parser.models import (
    Alert,
    CompareScope,
    Diagnosis,
    DiffBundle,
    DiffDetail,
    DiffSummary,
    EvidenceRef,
    MetricResult,
    RebuildBundle,
)
from parser.result import Result, err_result, ok_result


@dataclass
class MetricConfig:
    long_block_threshold: float = 100.0
    long_irq_threshold: float = 100.0
    irq_latency_threshold: float = 80.0
    compare_metric_ids: list[str] = field(
        default_factory=lambda: [
            "cpu_utilization",
            "context_switch_count",
            "blocked_time",
            "ready_wait_time",
            "response_time",
            "response_jitter",
            "irq_latency",
        ]
    )


@dataclass
class MetricSession:
    cfg: MetricConfig
    bundle: RebuildBundle | None = None
    latest_metrics: list[MetricResult] = field(default_factory=list)
    latest_alerts: list[Alert] = field(default_factory=list)
    latest_diagnoses: list[Diagnosis] = field(default_factory=list)


def metric_Init(cfg: MetricConfig | dict[str, Any] | None = None) -> Result[MetricSession]:
    if cfg is None:
        config = MetricConfig()
    elif isinstance(cfg, MetricConfig):
        config = cfg
    else:
        config = MetricConfig(**cfg)
    return ok_result(MetricSession(cfg=config))


def metric_Ingest(session: MetricSession, rebuild_bundle: RebuildBundle) -> Result[None]:
    required = [
        rebuild_bundle.event_stream,
        rebuild_bundle.task_states,
        rebuild_bundle.exec_slices,
        rebuild_bundle.resource_graph,
        rebuild_bundle.irq_spans,
        rebuild_bundle.untrusted_windows,
    ]
    if any(item is None for item in required):
        return err_result("INVALID_ARG", "rebuild bundle is missing required channels")
    session.bundle = rebuild_bundle
    return ok_result(None)


def _window_filter(bundle: RebuildBundle, t_begin: float, t_end: float) -> dict[str, Any]:
    return {
        "events": [event for event in bundle.event_stream if t_begin <= event.timestamp_aligned <= t_end],
        "slices": [item for item in bundle.exec_slices if item.t_begin < t_end and item.t_end > t_begin],
        "states": [item for item in bundle.task_states if item.t_begin < t_end and item.t_end > t_begin],
        "irqs": [item for item in bundle.irq_spans if item.t_begin < t_end and item.t_end > t_begin],
        "windows": [item for item in bundle.untrusted_windows if item.t_begin < t_end and item.t_end > t_begin],
    }


def _matches_filter(item: Any, filter_spec: dict[str, Any]) -> bool:
    if not filter_spec:
        return True
    task_id = filter_spec.get("task_id")
    core_id = filter_spec.get("core_id")
    event_name = filter_spec.get("event_name")
    obj_id = filter_spec.get("obj_id") or filter_spec.get("resource_id")
    irq_id = filter_spec.get("irq_id")

    if task_id is not None:
        current_task = getattr(item, "task_id", None)
        delayed_task = getattr(item, "delayed_task", None)
        if current_task != task_id and delayed_task != task_id:
            return False
    if core_id is not None and getattr(item, "core_id", None) != core_id:
        return False
    if event_name is not None and getattr(item, "event_name", None) != event_name:
        return False
    if obj_id is not None:
        current_obj = getattr(item, "obj_id", None)
        related_obj = getattr(item, "related_obj", None)
        if current_obj != obj_id and related_obj != obj_id:
            return False
    if irq_id is not None and getattr(item, "irq_id", None) != irq_id:
        return False
    return True


def _trusted(bundle: RebuildBundle, t_begin: float, t_end: float) -> bool:
    return not any(window for window in bundle.untrusted_windows if window.t_begin < t_end and window.t_end > t_begin)


def _reason_code_counts(windows: list[Any]) -> dict[str, int]:
    counts = Counter(str(window.reason_code) for window in windows if getattr(window, "reason_code", None))
    return {reason_code: counts[reason_code] for reason_code in sorted(counts)}


def _degraded_reason(
    baseline_windows: list[Any],
    candidate_windows: list[Any],
) -> dict[str, list[str]] | None:
    baseline_reason_codes = sorted({str(window.reason_code) for window in baseline_windows if getattr(window, "reason_code", None)})
    candidate_reason_codes = sorted({str(window.reason_code) for window in candidate_windows if getattr(window, "reason_code", None)})
    if not baseline_reason_codes and not candidate_reason_codes:
        return None
    degraded_reason: dict[str, list[str]] = {}
    if baseline_reason_codes:
        degraded_reason["baseline_reason_codes"] = baseline_reason_codes
    if candidate_reason_codes:
        degraded_reason["candidate_reason_codes"] = candidate_reason_codes
    return degraded_reason


def _trust_summary(
    baseline_windows: list[Any],
    candidate_windows: list[Any],
) -> dict[str, Any]:
    return {
        "trusted": not bool(baseline_windows or candidate_windows),
        "baseline_untrusted_window_count": len(baseline_windows),
        "candidate_untrusted_window_count": len(candidate_windows),
        "unified_untrusted_window_count": len(baseline_windows) + len(candidate_windows),
        "baseline_reason_codes": _reason_code_counts(baseline_windows),
        "candidate_reason_codes": _reason_code_counts(candidate_windows),
    }


def _quantile_value(samples: list[float], ratio: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(float(item) for item in samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _distribution(samples: list[float]) -> list[dict[str, Any]]:
    if not samples:
        return []
    points = [("p50", 0.50), ("p90", 0.90), ("p95", 0.95), ("p99", 0.99)]
    return [{"quantile": label, "value": round(_quantile_value(samples, ratio), 6)} for label, ratio in points]


def _distribution_summary(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    return {
        "p50": round(_quantile_value(samples, 0.50), 6),
        "p90": round(_quantile_value(samples, 0.90), 6),
        "p95": round(_quantile_value(samples, 0.95), 6),
        "p99": round(_quantile_value(samples, 0.99), 6),
    }


def _deadline_analysis(events: list[Any], capability_flags: dict[str, bool] | None = None) -> dict[str, Any]:
    precise_records: dict[tuple[Any, ...], dict[str, Any]] = {}
    degraded_records: dict[tuple[Any, ...], dict[str, Any]] = {}
    conflict_count = 0
    issue_samples: list[dict[str, Any]] = []

    for event in sorted(events, key=lambda item: (item.timestamp_aligned, item.core_id, item.seq)):
        if event.task_id is None:
            continue
        payload = event.payload or {}
        task_id = int(event.task_id)
        precise_key = ("instance", task_id, event.job_id, event.instance_id)
        degraded_key = ("task", task_id)

        if event.event_name == "TASK_READY":
            deadline_ts = payload.get("deadline_ts", payload.get("deadline"))
            if deadline_ts is None and "release_ts" not in payload:
                continue
            record = {
                "task_id": task_id,
                "job_id": event.job_id,
                "instance_id": event.instance_id,
                "release_ts": float(payload.get("release_ts", event.timestamp_aligned)),
                "deadline_ts": float(deadline_ts) if deadline_ts is not None else None,
                "finish_ts": None,
                "release_event": event,
                "finish_event": None,
                "semantics": "precise" if (event.job_id is not None or event.instance_id is not None) else "degraded",
            }
            if record["semantics"] == "precise":
                precise_records[precise_key] = record
            else:
                existing = degraded_records.get(degraded_key)
                if existing is not None and existing["finish_ts"] is None:
                    conflict_count += 1
                    issue_samples.append(
                        {
                            "issue_type": "conflict",
                            **record,
                        }
                    )
                    continue
                degraded_records[degraded_key] = record
            continue

        if event.event_name != "TASK_EXIT":
            continue

        if event.job_id is not None or event.instance_id is not None:
            record = precise_records.get(precise_key)
        else:
            record = degraded_records.get(degraded_key)
        if record is None or record["finish_ts"] is not None:
            continue
        record["finish_ts"] = float(payload.get("finish_ts", event.timestamp_aligned))
        record["finish_event"] = event

    candidates = list(precise_records.values()) + list(degraded_records.values())
    completed: list[dict[str, Any]] = []
    incomplete_count = 0
    for record in candidates:
        if record["deadline_ts"] is None or record["finish_ts"] is None:
            incomplete_count += 1
            issue_samples.append(
                {
                    "issue_type": "incomplete",
                    **record,
                }
            )
            continue
        response_time = max(0.0, record["finish_ts"] - record["release_ts"])
        deadline_miss = max(0.0, record["finish_ts"] - record["deadline_ts"])
        completed.append(
            {
                **record,
                "response_time": response_time,
                "deadline_miss": deadline_miss,
            }
        )

    if completed:
        semantics_status = "precise"
        if incomplete_count or conflict_count or any(item["semantics"] != "precise" for item in completed):
            semantics_status = "degraded"
    else:
        semantics_status = "unsupported"
        if incomplete_count or conflict_count or (capability_flags or {}).get("deadline_semantics"):
            semantics_status = "degraded"

    return {
        "records": completed,
        "candidate_count": len(candidates),
        "incomplete_count": incomplete_count,
        "conflict_count": conflict_count,
        "semantics_status": semantics_status,
        "precise_record_count": len([item for item in completed if item["semantics"] == "precise"]),
        "degraded_record_count": len([item for item in completed if item["semantics"] == "degraded"]),
        "supported": bool(completed),
        "issue_samples": issue_samples[:5],
    }


def _support_level(trusted: bool, semantics_status: str | None = None) -> str:
    if semantics_status == "unsupported":
        return "unsupported"
    if semantics_status == "degraded" or not trusted:
        return "degraded"
    return "exact"


def metric_Compute(
    session: MetricSession,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any] | None = None,
) -> Result[list[MetricResult]]:
    if session.bundle is None:
        return err_result("NOT_READY", "metric session has no ingested rebuild bundle")
    bundle = session.bundle
    filters = filter_spec or {}
    window = _window_filter(bundle, t_begin, t_end)
    window = {
        "events": [item for item in window["events"] if _matches_filter(item, filters)],
        "slices": [item for item in window["slices"] if _matches_filter(item, filters)],
        "states": [item for item in window["states"] if _matches_filter(item, filters)],
        "irqs": [item for item in window["irqs"] if _matches_filter(item, filters)],
        "windows": window["windows"],
    }
    duration = max(t_end - t_begin, 1.0)
    trusted = _trusted(bundle, t_begin, t_end)

    cpu_by_core: dict[int, float] = {}
    cpu_utilization_samples: list[float] = []
    blocked_segment_samples: list[float] = []
    ready_segment_samples: list[float] = []
    for item in window["slices"]:
        cpu_by_core.setdefault(item.core_id, 0.0)
        cpu_by_core[item.core_id] += max(0.0, min(item.t_end, t_end) - max(item.t_begin, t_begin))
    cpu_utilization_samples = [run_time / duration for _, run_time in sorted(cpu_by_core.items())]
    cpu_metric = MetricResult(
        metric_id="cpu_utilization",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {"core_id": core_id, "utilization": round(run_time / duration, 6)}
            for core_id, run_time in sorted(cpu_by_core.items())
        ],
        distribution=_distribution(cpu_utilization_samples),
        topn=sorted(
            [
                {"core_id": core_id, "run_time": run_time}
                for core_id, run_time in cpu_by_core.items()
            ],
            key=lambda item: (-item["run_time"], item["core_id"]),
        ),
        summary={
            "window_duration": duration,
            "core_count": len(cpu_by_core),
            "avg_utilization": round(sum(cpu_by_core.values()) / duration, 6),
            **_distribution_summary(cpu_utilization_samples),
        },
        trusted=trusted,
    )

    blocked_by_task: dict[int, float] = {}
    for segment in window["states"]:
        if segment.state == "BLOCKED":
            blocked_duration = max(0.0, min(segment.t_end, t_end) - max(segment.t_begin, t_begin))
            blocked_by_task.setdefault(segment.task_id, 0.0)
            blocked_by_task[segment.task_id] += blocked_duration
            blocked_segment_samples.append(blocked_duration)
    blocked_metric = MetricResult(
        metric_id="blocked_time",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {"task_id": task_id, "blocked_time": blocked_time}
            for task_id, blocked_time in sorted(blocked_by_task.items())
        ],
        distribution=_distribution(blocked_segment_samples),
        topn=sorted(
            [
                {"task_id": task_id, "blocked_time": blocked_time}
                for task_id, blocked_time in blocked_by_task.items()
            ],
            key=lambda item: (-item["blocked_time"], item["task_id"]),
        )[:10],
        summary={
            "task_count": len(blocked_by_task),
            "total_blocked_time": sum(blocked_by_task.values()),
            **_distribution_summary(blocked_segment_samples),
        },
        trusted=trusted,
    )

    ready_by_task: dict[int, float] = {}
    response_samples: dict[int, list[float]] = {}
    states_by_task: dict[int, list[Any]] = {}
    for segment in window["states"]:
        states_by_task.setdefault(segment.task_id, []).append(segment)
        if segment.state == "READY":
            ready_duration = max(0.0, min(segment.t_end, t_end) - max(segment.t_begin, t_begin))
            ready_by_task.setdefault(segment.task_id, 0.0)
            ready_by_task[segment.task_id] += ready_duration
            ready_segment_samples.append(ready_duration)
    for task_id, segments in states_by_task.items():
        ordered = sorted(segments, key=lambda item: (item.t_begin, item.t_end))
        for index, segment in enumerate(ordered[:-1]):
            if segment.state != "READY":
                continue
            next_segment = ordered[index + 1]
            if next_segment.state != "RUNNING":
                continue
            response_samples.setdefault(task_id, []).append(max(0.0, segment.t_end - segment.t_begin))
    all_response_values = [value for values in response_samples.values() for value in values]
    jitter_by_task = {
        task_id: max(samples) - min(samples)
        for task_id, samples in response_samples.items()
        if samples
    }
    deadline_analysis = _deadline_analysis(window["events"], bundle.capability_flags)

    ready_metric = MetricResult(
        metric_id="ready_wait_time",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {"task_id": task_id, "ready_wait_time": ready_time}
            for task_id, ready_time in sorted(ready_by_task.items())
        ],
        distribution=_distribution(ready_segment_samples),
        topn=sorted(
            [
                {"task_id": task_id, "ready_wait_time": ready_time}
                for task_id, ready_time in ready_by_task.items()
            ],
            key=lambda item: (-item["ready_wait_time"], item["task_id"]),
        )[:10],
        summary={
            "task_count": len(ready_by_task),
            "total_ready_wait_time": sum(ready_by_task.values()),
            "max_ready_wait_time": max(ready_by_task.values(), default=0.0),
            **_distribution_summary(ready_segment_samples),
        },
        trusted=trusted,
    )

    response_metric = MetricResult(
        metric_id="response_time",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {
                "task_id": task_id,
                "avg_response_time": round(sum(samples) / len(samples), 6),
                "sample_count": len(samples),
            }
            for task_id, samples in sorted(response_samples.items())
            if samples
        ],
        distribution=_distribution(all_response_values),
        topn=sorted(
            [
                {"task_id": task_id, "max_response_time": max(samples), "sample_count": len(samples)}
                for task_id, samples in response_samples.items()
                if samples
            ],
            key=lambda item: (-item["max_response_time"], item["task_id"]),
        )[:10],
        summary={
            "task_count": len(response_samples),
            "avg_response_time": round(sum(all_response_values) / len(all_response_values), 6)
            if all_response_values
            else 0.0,
            "max_response_time": max(all_response_values, default=0.0),
            **_distribution_summary(all_response_values),
        },
        trusted=trusted,
    )

    jitter_metric = MetricResult(
        metric_id="response_jitter",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {
                "task_id": task_id,
                "response_jitter": round(jitter, 6),
                "sample_count": len(response_samples.get(task_id, [])),
            }
            for task_id, jitter in sorted(jitter_by_task.items())
        ],
        distribution=_distribution(list(jitter_by_task.values())),
        topn=sorted(
            [
                {"task_id": task_id, "response_jitter": jitter}
                for task_id, jitter in jitter_by_task.items()
            ],
            key=lambda item: (-item["response_jitter"], item["task_id"]),
        )[:10],
        summary={
            "task_count": len(jitter_by_task),
            "avg_jitter": round(sum(jitter_by_task.values()) / len(jitter_by_task), 6)
            if jitter_by_task
            else 0.0,
            "max_jitter": max(jitter_by_task.values(), default=0.0),
            **_distribution_summary(list(jitter_by_task.values())),
        },
        trusted=trusted,
    )

    deadline_miss_values = [item["deadline_miss"] for item in deadline_analysis["records"]]
    deadline_metric = MetricResult(
        metric_id="deadline_miss",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {
                "task_id": item["task_id"],
                "job_id": item["job_id"],
                "instance_id": item["instance_id"],
                "response_time": round(item["response_time"], 6),
                "deadline_ts": round(float(item["deadline_ts"]), 6),
                "deadline_miss": round(item["deadline_miss"], 6),
                "semantics": item["semantics"],
            }
            for item in sorted(
                deadline_analysis["records"],
                key=lambda record: (-record["deadline_miss"], record["task_id"], record["release_ts"]),
            )
        ],
        distribution=_distribution(deadline_miss_values),
        topn=[
            {
                "task_id": item["task_id"],
                "job_id": item["job_id"],
                "instance_id": item["instance_id"],
                "deadline_miss": round(item["deadline_miss"], 6),
                "semantics": item["semantics"],
            }
            for item in sorted(
                deadline_analysis["records"],
                key=lambda record: (-record["deadline_miss"], record["task_id"], record["release_ts"]),
            )[:10]
        ],
        summary={
            "record_count": len(deadline_analysis["records"]),
            "candidate_count": deadline_analysis["candidate_count"],
            "miss_count": len([item for item in deadline_analysis["records"] if item["deadline_miss"] > 0.0]),
            "max_deadline_miss": max(deadline_miss_values, default=0.0),
            "semantics_status": deadline_analysis["semantics_status"],
            "precise_record_count": deadline_analysis["precise_record_count"],
            "degraded_record_count": deadline_analysis["degraded_record_count"],
            "incomplete_count": deadline_analysis["incomplete_count"],
            "conflict_count": deadline_analysis["conflict_count"],
            "issue_count": len(deadline_analysis["issue_samples"]),
            "issue_types": sorted({item["issue_type"] for item in deadline_analysis["issue_samples"]}),
            **_distribution_summary(deadline_miss_values),
        },
        trusted=trusted and deadline_analysis["semantics_status"] != "unsupported",
    )

    context_switch_count = len([event for event in window["events"] if event.event_name == "CTX_SWITCH"])
    context_switch_metric = MetricResult(
        metric_id="context_switch_count",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[{"count": context_switch_count}],
        distribution=[],
        topn=[],
        summary={"count": context_switch_count},
        trusted=trusted,
    )

    irq_metric = MetricResult(
        metric_id="irq_busy_time",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {
                "core_id": core_id,
                "busy_time": sum(
                    max(0.0, min(span.t_end, t_end) - max(span.t_begin, t_begin))
                    for span in window["irqs"]
                    if span.core_id == core_id
                ),
            }
            for core_id in sorted({span.core_id for span in window["irqs"]})
        ],
        distribution=_distribution(
            [max(0.0, min(span.t_end, t_end) - max(span.t_begin, t_begin)) for span in window["irqs"]]
        ),
        topn=[],
        summary={
            "irq_count": len(window["irqs"]),
            **_distribution_summary(
                [max(0.0, min(span.t_end, t_end) - max(span.t_begin, t_begin)) for span in window["irqs"]]
            ),
        },
        trusted=trusted,
    )

    irq_latency_by_task: dict[int, float] = {}
    irq_latency_samples: dict[int, list[float]] = {}
    irq_latency_values: list[float] = []
    for span in window["irqs"]:
        if span.delayed_task is None:
            continue
        delay = max(0.0, min(span.t_end, t_end) - max(span.t_begin, t_begin))
        irq_latency_values.append(delay)
        irq_latency_by_task.setdefault(span.delayed_task, 0.0)
        irq_latency_by_task[span.delayed_task] += delay
        irq_latency_samples.setdefault(span.delayed_task, []).append(delay)
    irq_latency_metric = MetricResult(
        metric_id="irq_latency",
        scope={"t_begin": t_begin, "t_end": t_end, "filter": filters},
        series=[
            {
                "task_id": task_id,
                "irq_latency": round(total_delay, 6),
                "max_irq_latency": round(max(irq_latency_samples.get(task_id, [0.0])), 6),
                "sample_count": len(irq_latency_samples.get(task_id, [])),
            }
            for task_id, total_delay in sorted(irq_latency_by_task.items())
        ],
        distribution=_distribution(irq_latency_values),
        topn=sorted(
            [
                {
                    "task_id": task_id,
                    "irq_latency": total_delay,
                    "max_irq_latency": max(irq_latency_samples.get(task_id, [0.0])),
                }
                for task_id, total_delay in irq_latency_by_task.items()
            ],
            key=lambda item: (-item["irq_latency"], item["task_id"]),
        )[:10],
        summary={
            "task_count": len(irq_latency_by_task),
            "total_irq_latency": sum(irq_latency_by_task.values()),
            "max_irq_latency": max(
                (max(samples) for samples in irq_latency_samples.values() if samples),
                default=0.0,
            ),
            **_distribution_summary(irq_latency_values),
        },
        trusted=trusted,
    )

    session.latest_metrics = [
        cpu_metric,
        blocked_metric,
        ready_metric,
        response_metric,
        jitter_metric,
        deadline_metric,
        context_switch_metric,
        irq_metric,
        irq_latency_metric,
    ]
    return ok_result(session.latest_metrics, untrusted_windows=window["windows"])


def alert_Evaluate(
    session: MetricSession,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any] | None = None,
) -> Result[list[Alert]]:
    if session.bundle is None:
        return err_result("NOT_READY", "metric session has no ingested rebuild bundle")
    bundle = session.bundle
    filters = filter_spec or {}
    window = _window_filter(bundle, t_begin, t_end)
    window = {
        "events": [item for item in window["events"] if _matches_filter(item, filters)],
        "slices": [item for item in window["slices"] if _matches_filter(item, filters)],
        "states": [item for item in window["states"] if _matches_filter(item, filters)],
        "irqs": [item for item in window["irqs"] if _matches_filter(item, filters)],
        "windows": window["windows"],
    }
    trusted = _trusted(bundle, t_begin, t_end)
    event_by_uid = {item.event_uid: item for item in window["events"]}
    priority_by_task: dict[int, int] = {}
    for event in window["events"]:
        payload = event.payload
        if "prio" in payload and payload.get("task_id") is not None:
            priority_by_task[int(payload["task_id"])] = int(payload["prio"])
    alerts: list[Alert] = []
    for segment in window["states"]:
        blocked_time = min(segment.t_end, t_end) - max(segment.t_begin, t_begin)
        if segment.state == "BLOCKED" and blocked_time >= session.cfg.long_block_threshold:
            alerts.append(
                Alert(
                    alert_id=f"alert:block:{segment.seg_id}",
                    type="long_block",
                    severity="warning",
                    time_window=(segment.t_begin, segment.t_end),
                    object_scope={"task_id": segment.task_id},
                    threshold=session.cfg.long_block_threshold,
                    actual=blocked_time,
                    evidence_refs=[
                        EvidenceRef(
                            ref_type="slice",
                            ref_key=segment.cause_event,
                            t_begin=segment.t_begin,
                            t_end=segment.t_end,
                        )
                    ],
                    support_level=_support_level(segment.trusted),
                    trusted=segment.trusted,
                )
            )
    deadline_analysis = _deadline_analysis(window["events"], bundle.capability_flags)
    for item in deadline_analysis["records"]:
        if item["deadline_miss"] <= 0.0:
            continue
        evidence_refs = [
            EvidenceRef(
                ref_type="event",
                ref_key=item["release_event"].ref_key,
                t_begin=item["release_ts"],
                t_end=item["release_ts"],
            )
        ]
        if item["finish_event"] is not None:
            evidence_refs.append(
                EvidenceRef(
                    ref_type="event",
                    ref_key=item["finish_event"].ref_key,
                    t_begin=item["finish_ts"],
                    t_end=item["finish_ts"],
                )
            )
        alerts.append(
            Alert(
                alert_id=(
                    f"alert:deadline:{item['task_id']}:{item['job_id'] if item['job_id'] is not None else 'na'}:"
                    f"{item['instance_id'] if item['instance_id'] is not None else 'na'}"
                ),
                type="deadline_miss",
                severity="warning",
                time_window=(item["release_ts"], item["finish_ts"]),
                object_scope={
                    "task_id": item["task_id"],
                    "job_id": item["job_id"],
                    "instance_id": item["instance_id"],
                    "semantics": item["semantics"],
                },
                threshold=max(0.0, float(item["deadline_ts"]) - item["release_ts"]),
                actual=item["response_time"],
                evidence_refs=evidence_refs,
                support_level=_support_level(trusted and item["semantics"] == "precise"),
                trusted=trusted and item["semantics"] == "precise",
            )
        )
    if deadline_analysis["issue_samples"]:
        issue_window_begin = min(float(item["release_ts"]) for item in deadline_analysis["issue_samples"])
        issue_window_end = max(
            float(item["finish_ts"] if item["finish_ts"] is not None else item["release_ts"])
            for item in deadline_analysis["issue_samples"]
        )
        alerts.append(
            Alert(
                alert_id=f"alert:deadline_gap:{int(issue_window_begin)}:{int(issue_window_end)}",
                type="deadline_semantics_gap",
                severity="warning",
                time_window=(issue_window_begin, issue_window_end),
                object_scope={
                    "issue_count": len(deadline_analysis["issue_samples"]),
                    "issue_types": sorted({item["issue_type"] for item in deadline_analysis["issue_samples"]}),
                    "candidate_count": deadline_analysis["candidate_count"],
                    "incomplete_count": deadline_analysis["incomplete_count"],
                    "conflict_count": deadline_analysis["conflict_count"],
                    "semantics_status": deadline_analysis["semantics_status"],
                },
                threshold=0,
                actual=float(deadline_analysis["incomplete_count"] + deadline_analysis["conflict_count"]),
                evidence_refs=[
                    EvidenceRef(
                        ref_type="event",
                        ref_key=item["release_event"].ref_key,
                        t_begin=float(item["release_ts"]),
                        t_end=float(item["release_ts"]),
                    )
                    for item in deadline_analysis["issue_samples"][:3]
                    if item.get("release_event") is not None
                ],
                support_level=_support_level(False, deadline_analysis["semantics_status"]),
                trusted=False,
            )
        )
    for segment in window["states"]:
        if segment.state != "BLOCKED" or segment.related_obj is None:
            continue
        cause_event = event_by_uid.get(segment.cause_event)
        if cause_event is None:
            continue
        owner_task_id = cause_event.payload.get("owner_task_id")
        if owner_task_id is None:
            continue
        blocked_prio = priority_by_task.get(segment.task_id)
        owner_prio = priority_by_task.get(int(owner_task_id))
        if blocked_prio is None or owner_prio is None or blocked_prio >= owner_prio:
            continue
        medium_slices = [
            item
            for item in window["slices"]
            if item.task_id not in {segment.task_id, int(owner_task_id)}
            and item.t_begin < segment.t_end
            and item.t_end > segment.t_begin
            and blocked_prio < priority_by_task.get(item.task_id, owner_prio + 1) < owner_prio
        ]
        if not medium_slices:
            continue
        alerts.append(
            Alert(
                alert_id=f"alert:inversion:{segment.seg_id}",
                type="priority_inversion",
                severity="warning",
                time_window=(segment.t_begin, segment.t_end),
                object_scope={
                    "task_id": segment.task_id,
                    "owner_task_id": int(owner_task_id),
                    "resource_id": segment.related_obj,
                    "medium_task_ids": sorted({item.task_id for item in medium_slices}),
                },
                threshold=0,
                actual=blocked_time,
                evidence_refs=[
                    EvidenceRef(
                        ref_type="event",
                        ref_key=segment.cause_event,
                        t_begin=segment.t_begin,
                        t_end=segment.t_end,
                    )
                ]
                + [
                    EvidenceRef(
                        ref_type="slice",
                        ref_key=item.slice_id,
                        t_begin=item.t_begin,
                        t_end=item.t_end,
                    )
                    for item in medium_slices[:3]
                ],
                support_level=_support_level(segment.trusted and all(item.trusted for item in medium_slices)),
                trusted=segment.trusted and all(item.trusted for item in medium_slices),
            )
        )
    for span in window["irqs"]:
        irq_time = min(span.t_end, t_end) - max(span.t_begin, t_begin)
        if irq_time >= session.cfg.long_irq_threshold:
            alerts.append(
                Alert(
                    alert_id=f"alert:irq:{span.irq_span_id}",
                    type="long_irq",
                    severity="warning",
                    time_window=(span.t_begin, span.t_end),
                    object_scope={"irq_id": span.irq_id, "core_id": span.core_id},
                    threshold=session.cfg.long_irq_threshold,
                    actual=irq_time,
                    evidence_refs=[
                        EvidenceRef(
                            ref_type="slice",
                            ref_key=span.irq_span_id,
                            t_begin=span.t_begin,
                            t_end=span.t_end,
                        )
                    ],
                    support_level=_support_level(span.trusted),
                    trusted=span.trusted,
                )
            )
        if span.delayed_task is not None and irq_time >= session.cfg.irq_latency_threshold:
            alerts.append(
                Alert(
                    alert_id=f"alert:irq_pressure:{span.irq_span_id}",
                    type="irq_pressure",
                    severity="warning",
                    time_window=(span.t_begin, span.t_end),
                    object_scope={
                        "irq_id": span.irq_id,
                        "core_id": span.core_id,
                        "delayed_task": span.delayed_task,
                    },
                    threshold=session.cfg.irq_latency_threshold,
                    actual=irq_time,
                    evidence_refs=[
                        EvidenceRef(
                            ref_type="slice",
                            ref_key=span.irq_span_id,
                            t_begin=span.t_begin,
                            t_end=span.t_end,
                        )
                    ],
                    support_level=_support_level(span.trusted),
                    trusted=span.trusted,
                )
            )
    for window_item in window["windows"]:
        alerts.append(
            Alert(
                alert_id=f"alert:untrusted:{window_item.window_id}",
                type="untrusted_window",
                severity="warning",
                time_window=(window_item.t_begin, window_item.t_end),
                object_scope={"source": window_item.source},
                threshold=0,
                actual=1,
                evidence_refs=[
                    EvidenceRef(
                        ref_type="index",
                        ref_key=window_item.window_id,
                        t_begin=window_item.t_begin,
                        t_end=window_item.t_end,
                    )
                ],
                support_level=_support_level(False),
                trusted=False,
            )
        )
    session.latest_alerts = alerts
    return ok_result(alerts, untrusted_windows=window["windows"])


def diag_Generate(
    session: MetricSession,
    t_begin: float,
    t_end: float,
    alert_list: list[Alert] | None = None,
) -> Result[list[Diagnosis]]:
    alerts = alert_list if alert_list is not None else session.latest_alerts
    diagnoses = [diag_FromAlert(alert) for alert in alerts]
    session.latest_diagnoses = diagnoses
    return ok_result(diagnoses)


def diag_PayloadFromAlert(alert: Alert) -> dict[str, Any]:
    if alert.type == "long_block":
        conclusion = f"Task {alert.object_scope['task_id']} blocked longer than threshold"
    elif alert.type == "long_irq":
        conclusion = f"IRQ {alert.object_scope['irq_id']} busy time exceeded threshold"
    elif alert.type == "priority_inversion":
        conclusion = (
            f"Task {alert.object_scope['task_id']} was blocked by task {alert.object_scope['owner_task_id']} "
            f"while medium-priority tasks {alert.object_scope['medium_task_ids']} occupied CPU time"
        )
    elif alert.type == "irq_pressure":
        conclusion = (
            f"IRQ {alert.object_scope['irq_id']} delayed task {alert.object_scope['delayed_task']} "
            f"for longer than the configured latency threshold"
        )
    elif alert.type == "deadline_miss":
        conclusion = (
            f"Task {alert.object_scope['task_id']} missed its deadline; "
            f"measured response={alert.actual:.3f}, budget={alert.threshold:.3f}"
        )
    elif alert.type == "deadline_semantics_gap":
        conclusion = (
            "Deadline semantics are only partially reconstructable; "
            f"issues={alert.object_scope['issue_types']}, "
            f"incomplete={alert.object_scope['incomplete_count']}, "
            f"conflict={alert.object_scope['conflict_count']}. "
            "This is a degraded coverage warning, not a precise deadline-miss conclusion."
        )
    else:
        conclusion = f"Trace confidence degraded by {alert.object_scope.get('source', 'unknown source')}"
    return {
        "diag_id": f"diag:{alert.alert_id}",
        "title": alert.type,
        "diagnosis_type": alert.type,
        "time_window": alert.time_window,
        "object_scope": alert.object_scope,
        "conclusion": conclusion,
        "evidence_refs": alert.evidence_refs,
        "related_alerts": [alert.alert_id],
        "confidence": (
            "high"
            if alert.support_level == "exact"
            else ("low" if alert.support_level == "unsupported" else "degraded")
        ),
        "support_level": alert.support_level,
    }


def diag_FromAlert(alert: Alert) -> Diagnosis:
    return Diagnosis(**diag_PayloadFromAlert(alert))


def diag_Backtrace(session: MetricSession, diag_id: str | None = None, alert_id: str | None = None) -> Result[list[EvidenceRef]]:
    if diag_id:
        for diagnosis in session.latest_diagnoses:
            if diagnosis.diag_id == diag_id:
                return ok_result(diagnosis.evidence_refs)
    if alert_id:
        for alert in session.latest_alerts:
            if alert.alert_id == alert_id:
                return ok_result(alert.evidence_refs)
    return err_result("INVALID_ARG", "diagnosis or alert not found")


def _metric_value(metrics: list[MetricResult], metric_id: str) -> float:
    for metric in metrics:
        if metric.metric_id == metric_id:
            if "avg_utilization" in metric.summary:
                return float(metric.summary["avg_utilization"])
            if "count" in metric.summary:
                return float(metric.summary["count"])
            if "total_blocked_time" in metric.summary:
                return float(metric.summary["total_blocked_time"])
            if "total_ready_wait_time" in metric.summary:
                return float(metric.summary["total_ready_wait_time"])
            if "avg_response_time" in metric.summary:
                return float(metric.summary["avg_response_time"])
            if "max_jitter" in metric.summary:
                return float(metric.summary["max_jitter"])
            if "max_deadline_miss" in metric.summary:
                return float(metric.summary["max_deadline_miss"])
            if "irq_count" in metric.summary:
                return float(metric.summary["irq_count"])
            if "total_irq_latency" in metric.summary:
                return float(metric.summary["total_irq_latency"])
    return 0.0


def _metric_window(bundle: RebuildBundle, t_begin: float, t_end: float, filter_spec: dict[str, Any]) -> dict[str, Any]:
    window = _window_filter(bundle, t_begin, t_end)
    return {
        "events": [item for item in window["events"] if _matches_filter(item, filter_spec)],
        "slices": [item for item in window["slices"] if _matches_filter(item, filter_spec)],
        "states": [item for item in window["states"] if _matches_filter(item, filter_spec)],
        "irqs": [item for item in window["irqs"] if _matches_filter(item, filter_spec)],
        "windows": window["windows"],
    }


def _clipped_duration(item: Any, t_begin: float, t_end: float) -> float:
    item_begin = float(getattr(item, "t_begin", t_begin))
    item_end = float(getattr(item, "t_end", t_end))
    return max(0.0, min(item_end, t_end) - max(item_begin, t_begin))


def _metric_evidence(bundle: RebuildBundle, metric_id: str, t_begin: float, t_end: float, filter_spec: dict[str, Any]) -> tuple[list[EvidenceRef], dict[str, Any], list[dict[str, Any]]]:
    window = _metric_window(bundle, t_begin, t_end, filter_spec)
    evidence_refs: list[EvidenceRef] = []
    related_events: list[dict[str, Any]] = []

    def add_event_ref(event: Any) -> None:
        evidence_refs.append(
            EvidenceRef(
                ref_type="event",
                ref_key=event.ref_key,
                t_begin=event.timestamp_aligned,
                t_end=event.timestamp_aligned,
            )
        )
        related_events.append(
            {
                "event_uid": event.event_uid,
                "ref_key": event.ref_key,
                "timestamp": event.timestamp_aligned,
                "event_name": event.event_name,
            }
        )

    if metric_id == "cpu_utilization":
        ranked = sorted(
            window["slices"],
            key=lambda item: (-(item.t_end - item.t_begin), item.core_id, item.task_id),
        )[:3]
        for item in ranked:
            matching = next((event for event in window["events"] if event.event_uid == item.start_event), None)
            if matching is not None:
                add_event_ref(matching)
        view = {
            "top_slices": [
                {
                    "slice_id": item.slice_id,
                    "task_id": item.task_id,
                    "core_id": item.core_id,
                    "duration": item.t_end - item.t_begin,
                }
                for item in ranked
            ]
        }
        return evidence_refs, view, related_events

    if metric_id == "blocked_time":
        ranked = sorted(
            [item for item in window["states"] if item.state == "BLOCKED"],
            key=lambda item: (-(item.t_end - item.t_begin), item.task_id),
        )[:3]
        for item in ranked:
            matching = next((event for event in window["events"] if event.event_uid == item.cause_event), None)
            if matching is not None:
                add_event_ref(matching)
        view = {
            "top_blocked_segments": [
                {
                    "seg_id": item.seg_id,
                    "task_id": item.task_id,
                    "duration": item.t_end - item.t_begin,
                    "related_obj": item.related_obj,
                }
                for item in ranked
            ]
        }
        return evidence_refs, view, related_events

    if metric_id == "ready_wait_time":
        ranked = sorted(
            [item for item in window["states"] if item.state == "READY"],
            key=lambda item: (-(item.t_end - item.t_begin), item.task_id),
        )[:3]
        for item in ranked:
            matching = next((event for event in window["events"] if event.event_uid == item.cause_event), None)
            if matching is not None:
                add_event_ref(matching)
        view = {
            "top_ready_segments": [
                {
                    "seg_id": item.seg_id,
                    "task_id": item.task_id,
                    "duration": item.t_end - item.t_begin,
                }
                for item in ranked
            ]
        }
        return evidence_refs, view, related_events

    if metric_id == "response_time":
        ranked: list[tuple[Any, Any, float]] = []
        states_by_task: dict[int, list[Any]] = {}
        for item in window["states"]:
            states_by_task.setdefault(item.task_id, []).append(item)
        for task_id, segments in states_by_task.items():
            ordered = sorted(segments, key=lambda item: (item.t_begin, item.t_end))
            for index, segment in enumerate(ordered[:-1]):
                if segment.state != "READY":
                    continue
                next_segment = ordered[index + 1]
                if next_segment.state != "RUNNING":
                    continue
                ranked.append((segment, next_segment, segment.t_end - segment.t_begin))
        ranked = sorted(ranked, key=lambda item: (-item[2], item[0].task_id))[:3]
        for segment, _, _ in ranked:
            matching = next((event for event in window["events"] if event.event_uid == segment.cause_event), None)
            if matching is not None:
                add_event_ref(matching)
        view = {
            "top_response_segments": [
                {
                    "task_id": segment.task_id,
                    "ready_seg_id": segment.seg_id,
                    "response_time": duration,
                }
                for segment, _, duration in ranked
            ]
        }
        return evidence_refs, view, related_events

    if metric_id == "response_jitter":
        states_by_task: dict[int, list[Any]] = {}
        for item in window["states"]:
            states_by_task.setdefault(item.task_id, []).append(item)
        ranked = []
        for task_id, segments in states_by_task.items():
            ordered = sorted(segments, key=lambda item: (item.t_begin, item.t_end))
            samples: list[float] = []
            first_segment = None
            for index, segment in enumerate(ordered[:-1]):
                if segment.state != "READY":
                    continue
                next_segment = ordered[index + 1]
                if next_segment.state != "RUNNING":
                    continue
                samples.append(segment.t_end - segment.t_begin)
                first_segment = first_segment or segment
            if samples:
                ranked.append((task_id, first_segment, max(samples) - min(samples), samples))
        ranked = sorted(ranked, key=lambda item: (-item[2], item[0]))[:3]
        for _, segment, _, _ in ranked:
            matching = next((event for event in window["events"] if event.event_uid == segment.cause_event), None)
            if matching is not None:
                add_event_ref(matching)
        view = {
            "top_jitter_tasks": [
                {
                    "task_id": task_id,
                    "response_jitter": jitter,
                    "sample_count": len(samples),
                }
                for task_id, _, jitter, samples in ranked
            ]
        }
        return evidence_refs, view, related_events

    if metric_id == "context_switch_count":
        switches = [event for event in window["events"] if event.event_name == "CTX_SWITCH"][:3]
        for event in switches:
            add_event_ref(event)
        return evidence_refs, {"sample_switches": [item["event_uid"] for item in related_events]}, related_events

    if metric_id == "irq_busy_time":
        spans = sorted(window["irqs"], key=lambda item: (-(item.t_end - item.t_begin), item.irq_id))[:3]
        view = {
            "top_irq_spans": [
                {
                    "irq_span_id": item.irq_span_id,
                    "irq_id": item.irq_id,
                    "core_id": item.core_id,
                    "duration": item.t_end - item.t_begin,
                }
                for item in spans
            ]
        }
        return evidence_refs, view, related_events

    if metric_id == "irq_latency":
        spans = sorted(
            [item for item in window["irqs"] if item.delayed_task is not None],
            key=lambda item: (-(item.t_end - item.t_begin), item.irq_id),
        )[:3]
        view = {
            "top_irq_latency_spans": [
                {
                    "irq_span_id": item.irq_span_id,
                    "irq_id": item.irq_id,
                    "core_id": item.core_id,
                    "delayed_task": item.delayed_task,
                    "latency": item.t_end - item.t_begin,
                }
                for item in spans
            ]
        }
        return evidence_refs, view, related_events

    return evidence_refs, {"sample_count": len(window["events"])}, related_events


def _event_summary(event: Any) -> dict[str, Any]:
    return {
        "event_uid": event.event_uid,
        "ref_key": event.ref_key,
        "timestamp": event.timestamp_aligned,
        "event_name": event.event_name,
        "core_id": event.core_id,
        "task_id": event.task_id,
        "obj_id": event.obj_id,
        "irq_id": event.irq_id,
        "payload": dict(event.payload),
    }


def _event_ref(event: Any) -> EvidenceRef:
    return EvidenceRef(
        ref_type="event",
        ref_key=event.ref_key,
        t_begin=event.timestamp_aligned,
        t_end=event.timestamp_aligned,
    )


def _event_anchor(related_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not related_events:
        return None
    anchor = {
        "ref_key": related_events[0].get("ref_key"),
        "event_uid": related_events[0].get("event_uid"),
    }
    return {key: value for key, value in anchor.items() if value is not None} or None


def _dedupe_evidence_refs(evidence_refs: list[EvidenceRef]) -> list[EvidenceRef]:
    unique: list[EvidenceRef] = []
    seen: set[tuple[str, str, float, float]] = set()
    for item in evidence_refs:
        key = (item.ref_type, item.ref_key, float(item.t_begin), float(item.t_end))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _dedupe_related_events(related_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for item in related_events:
        key = (item.get("ref_key"), item.get("event_uid"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _selection_payload(
    *,
    task_id: Any = None,
    core_id: Any = None,
    resource_id: Any = None,
    irq_id: Any = None,
) -> dict[str, Any]:
    selection = {
        "task_id": task_id,
        "core_id": core_id,
        "resource_id": resource_id,
        "irq_id": irq_id,
    }
    return {key: value for key, value in selection.items() if value is not None}


def _selection_from_event_summary(event: dict[str, Any] | None) -> dict[str, Any]:
    if event is None:
        return {}
    return _selection_payload(
        task_id=event.get("task_id"),
        core_id=event.get("core_id"),
        resource_id=event.get("obj_id"),
        irq_id=event.get("irq_id"),
    )


def _selection_from_filter(filter_spec: dict[str, Any]) -> dict[str, Any]:
    return _selection_payload(
        task_id=filter_spec.get("task_id"),
        core_id=filter_spec.get("core_id"),
        resource_id=filter_spec.get("resource_id") or filter_spec.get("obj_id"),
        irq_id=filter_spec.get("irq_id"),
    )


def _object_scope_selection(scope: dict[str, Any]) -> dict[str, Any]:
    return _selection_payload(
        task_id=scope.get("task_id"),
        core_id=scope.get("core_id"),
        resource_id=scope.get("resource_id") or scope.get("obj_id"),
        irq_id=scope.get("irq_id"),
    )


def _time_window_from_ranges(
    related_events: list[dict[str, Any]],
    evidence_refs: list[EvidenceRef],
    fallback_windows: list[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    points = [float(item["timestamp"]) for item in related_events if item.get("timestamp") is not None]
    for ref in evidence_refs:
        points.extend([float(ref.t_begin), float(ref.t_end)])
    for window in fallback_windows or []:
        points.extend([float(window[0]), float(window[1])])
    if not points:
        return None
    begin = min(points)
    end = max(points)
    return (max(0.0, begin - 20.0), end + 20.0)


def _slice_summary(item: Any) -> dict[str, Any]:
    return {
        "slice_id": item.slice_id,
        "task_id": item.task_id,
        "core_id": item.core_id,
        "t_begin": item.t_begin,
        "t_end": item.t_end,
        "duration": round(item.t_end - item.t_begin, 6),
        "trusted": item.trusted,
    }


def _segment_summary(item: Any) -> dict[str, Any]:
    return {
        "seg_id": item.seg_id,
        "task_id": item.task_id,
        "state": item.state,
        "t_begin": item.t_begin,
        "t_end": item.t_end,
        "duration": round(item.t_end - item.t_begin, 6),
        "related_obj": item.related_obj,
        "trusted": item.trusted,
    }


def _irq_span_summary(item: Any) -> dict[str, Any]:
    return {
        "irq_span_id": item.irq_span_id,
        "irq_id": item.irq_id,
        "core_id": item.core_id,
        "t_begin": item.t_begin,
        "t_end": item.t_end,
        "duration": round(item.t_end - item.t_begin, 6),
        "delayed_task": item.delayed_task,
        "trusted": item.trusted,
    }


def _resolve_evidence_related_events(
    bundle: RebuildBundle,
    window: dict[str, Any],
    evidence_refs: list[EvidenceRef],
) -> list[dict[str, Any]]:
    event_by_ref = {event.ref_key: event for event in window["events"]}
    event_by_uid = {event.event_uid: event for event in window["events"]}
    slice_by_id = {item.slice_id: item for item in window["slices"]}
    state_by_id = {item.seg_id: item for item in window["states"]}
    irq_by_id = {item.irq_span_id: item for item in window["irqs"]}
    related_events: list[dict[str, Any]] = []

    def add_event(event: Any | None) -> None:
        if event is None:
            return
        related_events.append(_event_summary(event))

    for evidence in evidence_refs:
        if evidence.ref_key in event_by_ref:
            add_event(event_by_ref[evidence.ref_key])
            continue
        if evidence.ref_key in event_by_uid:
            add_event(event_by_uid[evidence.ref_key])
            continue
        if evidence.ref_key in slice_by_id:
            slice_item = slice_by_id[evidence.ref_key]
            add_event(event_by_uid.get(slice_item.start_event))
            add_event(event_by_uid.get(slice_item.end_event))
            continue
        if evidence.ref_key in state_by_id:
            segment = state_by_id[evidence.ref_key]
            add_event(event_by_uid.get(segment.cause_event))
            continue
        if evidence.ref_key in irq_by_id:
            span = irq_by_id[evidence.ref_key]
            irq_events = sorted(
                [
                    event
                    for event in window["events"]
                    if event.irq_id == span.irq_id
                    and event.core_id == span.core_id
                    and event.event_name in {"IRQ_ENTER", "IRQ_EXIT"}
                    and span.t_begin <= event.timestamp_aligned <= span.t_end
                ],
                key=lambda item: (item.timestamp_aligned, item.seq),
            )
            for event in irq_events[:2]:
                add_event(event)
    return _dedupe_related_events(related_events)


def _detail_side_payload(
    *,
    bundle: RebuildBundle,
    dataset_role: str,
    focused_view: str,
    selection: dict[str, Any],
    evidence_refs: list[EvidenceRef],
    related_events: list[dict[str, Any]],
    fallback_windows: list[tuple[float, float]] | None,
    view: dict[str, Any],
) -> dict[str, Any]:
    related = _dedupe_related_events(related_events)
    evidence = _dedupe_evidence_refs(evidence_refs)
    anchor = _event_anchor(related)
    time_window = _time_window_from_ranges(related, evidence, fallback_windows)
    jump_target = None
    if time_window is not None:
        jump_target = {
            "dataset_id": bundle.dataset_id,
            "dataset_role": dataset_role,
            "time_window": list(time_window),
            "selection": selection,
            "evidence_anchor": anchor,
            "focused_view": focused_view,
        }
    return {
        "evidence_refs": evidence,
        "related_events": related,
        "view": {
            **view,
            "dataset_id": bundle.dataset_id,
            "dataset_role": dataset_role,
            "evidence_anchor": anchor,
        },
        "jump_target": jump_target,
    }


def _combine_jump_targets(
    baseline_jump: dict[str, Any] | None,
    candidate_jump: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if baseline_jump and candidate_jump:
        return {**baseline_jump, "peer_target": candidate_jump}
    return baseline_jump or candidate_jump


def _alert_detail_side(
    bundle: RebuildBundle,
    dataset_role: str,
    window: dict[str, Any],
    alerts: list[Alert],
    alert_type: str,
) -> dict[str, Any]:
    matched = [item for item in alerts if item.type == alert_type][:3]
    evidence_refs = [evidence for item in matched for evidence in item.evidence_refs]
    related_events = _resolve_evidence_related_events(bundle, window, evidence_refs)
    selection = _object_scope_selection(matched[0].object_scope) if matched else {}
    return _detail_side_payload(
        bundle=bundle,
        dataset_role=dataset_role,
        focused_view="alerts",
        selection=selection,
        evidence_refs=evidence_refs,
        related_events=related_events,
        fallback_windows=[item.time_window for item in matched],
        view={
            "alert_type": alert_type,
            "count": len(matched),
            "sample_alerts": [
                {
                    "alert_id": item.alert_id,
                    "severity": item.severity,
                    "time_window": list(item.time_window),
                    "object_scope": dict(item.object_scope),
                    "trusted": item.trusted,
                }
                for item in matched
            ],
        },
    )


def _hotspot_detail_side(
    bundle: RebuildBundle,
    dataset_role: str,
    node_id: str,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> dict[str, Any]:
    active_filter = dict(filter_spec)
    selection: dict[str, Any] = {}
    focused_view = "timeline"
    if node_id.startswith("task:"):
        task_id = int(node_id.split(":", 1)[1], 0)
        active_filter["task_id"] = task_id
        selection = _selection_payload(task_id=task_id)
        focused_view = "task_state"
    elif node_id.startswith("obj:"):
        resource_id = int(node_id.split(":", 1)[1], 0)
        active_filter["resource_id"] = resource_id
        selection = _selection_payload(resource_id=resource_id)
        focused_view = "resource"
    window = _metric_window(bundle, t_begin, t_end, active_filter)
    related_events = [_event_summary(event) for event in window["events"][:5]]
    evidence_refs = [_event_ref(event) for event in window["events"][:3]]
    hotspot_count = next((item["count"] for item in bundle.resource_graph.hotspot_stats if item["node_id"] == node_id), 0)
    return _detail_side_payload(
        bundle=bundle,
        dataset_role=dataset_role,
        focused_view=focused_view,
        selection=selection or _selection_from_event_summary(related_events[0] if related_events else None),
        evidence_refs=evidence_refs,
        related_events=related_events,
        fallback_windows=[(t_begin, t_end)],
        view={
            "node_id": node_id,
            "count": hotspot_count,
            "sample_events": related_events[:3],
        },
    )


def _interval_detail_side(
    bundle: RebuildBundle,
    dataset_role: str,
    interval_type: str,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> dict[str, Any]:
    window = _metric_window(bundle, t_begin, t_end, filter_spec)
    ranked = sorted(
        [item for item in window["states"] if item.state == "BLOCKED"],
        key=lambda item: (-(item.t_end - item.t_begin), item.task_id),
    )
    sample = ranked[0] if ranked else None
    event_by_uid = {event.event_uid: event for event in window["events"]}
    cause_event = event_by_uid.get(sample.cause_event) if sample is not None else None
    related_events = [_event_summary(cause_event)] if cause_event is not None else []
    evidence_refs = [_event_ref(cause_event)] if cause_event is not None else []
    return _detail_side_payload(
        bundle=bundle,
        dataset_role=dataset_role,
        focused_view="task_state",
        selection=_selection_payload(
            task_id=sample.task_id if sample is not None else None,
            resource_id=sample.related_obj if sample is not None else None,
        ),
        evidence_refs=evidence_refs,
        related_events=related_events,
        fallback_windows=[(sample.t_begin, sample.t_end)] if sample is not None else [(t_begin, t_end)],
        view={
            "interval_type": interval_type,
            "sample_segment": _segment_summary(sample) if sample is not None else None,
        },
    )


def _task_detail_side(
    bundle: RebuildBundle,
    dataset_role: str,
    task_id: int,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> dict[str, Any]:
    window = _metric_window(bundle, t_begin, t_end, {**filter_spec, "task_id": task_id})
    event_by_uid = {event.event_uid: event for event in window["events"]}
    top_slices = sorted(window["slices"], key=lambda item: (-(item.t_end - item.t_begin), item.core_id))[:2]
    top_blocked = sorted(
        [item for item in window["states"] if item.state == "BLOCKED"],
        key=lambda item: (-(item.t_end - item.t_begin), item.task_id),
    )[:2]
    related_events = [
        _event_summary(event_by_uid[item.start_event])
        for item in top_slices
        if item.start_event in event_by_uid
    ] + [
        _event_summary(event_by_uid[item.cause_event])
        for item in top_blocked
        if item.cause_event in event_by_uid
    ]
    evidence_refs = [
        _event_ref(event_by_uid[item.start_event])
        for item in top_slices
        if item.start_event in event_by_uid
    ] + [
        _event_ref(event_by_uid[item.cause_event])
        for item in top_blocked
        if item.cause_event in event_by_uid
    ]
    return _detail_side_payload(
        bundle=bundle,
        dataset_role=dataset_role,
        focused_view="task_state",
        selection=_selection_payload(task_id=task_id),
        evidence_refs=evidence_refs,
        related_events=related_events,
        fallback_windows=[(t_begin, t_end)],
        view={
            "task_id": task_id,
            "runtime_total": round(sum(_clipped_duration(item, t_begin, t_end) for item in window["slices"]), 6),
            "blocked_total": round(
                sum(_clipped_duration(item, t_begin, t_end) for item in window["states"] if item.state == "BLOCKED"),
                6,
            ),
            "top_slices": [_slice_summary(item) for item in top_slices],
            "top_blocked_segments": [_segment_summary(item) for item in top_blocked],
        },
    )


def _core_detail_side(
    bundle: RebuildBundle,
    dataset_role: str,
    core_id: int,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> dict[str, Any]:
    window = _metric_window(bundle, t_begin, t_end, {**filter_spec, "core_id": core_id})
    event_by_uid = {event.event_uid: event for event in window["events"]}
    top_slices = sorted(window["slices"], key=lambda item: (-(item.t_end - item.t_begin), item.task_id))[:2]
    switches = [event for event in window["events"] if event.event_name == "CTX_SWITCH"][:3]
    related_events = [_event_summary(item) for item in switches] + [
        _event_summary(event_by_uid[item.start_event])
        for item in top_slices
        if item.start_event in event_by_uid
    ]
    evidence_refs = [_event_ref(item) for item in switches] + [
        _event_ref(event_by_uid[item.start_event])
        for item in top_slices
        if item.start_event in event_by_uid
    ]
    return _detail_side_payload(
        bundle=bundle,
        dataset_role=dataset_role,
        focused_view="timeline",
        selection=_selection_payload(core_id=core_id),
        evidence_refs=evidence_refs,
        related_events=related_events,
        fallback_windows=[(t_begin, t_end)],
        view={
            "core_id": core_id,
            "runtime_total": round(sum(_clipped_duration(item, t_begin, t_end) for item in window["slices"]), 6),
            "switch_count": len([event for event in window["events"] if event.event_name == "CTX_SWITCH"]),
            "top_slices": [_slice_summary(item) for item in top_slices],
            "sample_switches": [_event_summary(item) for item in switches],
        },
    )


def _resource_detail_side(
    bundle: RebuildBundle,
    dataset_role: str,
    resource_id: int,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> dict[str, Any]:
    window = _metric_window(bundle, t_begin, t_end, {**filter_spec, "resource_id": resource_id})
    event_by_uid = {event.event_uid: event for event in window["events"]}
    resource_events = [event for event in window["events"] if event.obj_id == resource_id][:4]
    blocked_segments = [item for item in window["states"] if item.related_obj == resource_id and item.state == "BLOCKED"][:3]
    related_events = [_event_summary(item) for item in resource_events] + [
        _event_summary(event_by_uid[item.cause_event])
        for item in blocked_segments
        if item.cause_event in event_by_uid
    ]
    evidence_refs = [_event_ref(item) for item in resource_events] + [
        _event_ref(event_by_uid[item.cause_event])
        for item in blocked_segments
        if item.cause_event in event_by_uid
    ]
    return _detail_side_payload(
        bundle=bundle,
        dataset_role=dataset_role,
        focused_view="resource",
        selection=_selection_payload(resource_id=resource_id),
        evidence_refs=evidence_refs,
        related_events=related_events,
        fallback_windows=[(t_begin, t_end)],
        view={
            "resource_id": resource_id,
            "event_count": len([event for event in window["events"] if event.obj_id == resource_id]),
            "blocked_total": round(
                sum(_clipped_duration(item, t_begin, t_end) for item in blocked_segments),
                6,
            ),
            "sample_events": [_event_summary(item) for item in resource_events[:3]],
            "blocked_segments": [_segment_summary(item) for item in blocked_segments],
        },
    )


def _irq_detail_side(
    bundle: RebuildBundle,
    dataset_role: str,
    irq_id: int,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> dict[str, Any]:
    window = _metric_window(bundle, t_begin, t_end, {**filter_spec, "irq_id": irq_id})
    spans = [item for item in window["irqs"] if item.irq_id == irq_id][:3]
    irq_events = [event for event in window["events"] if event.irq_id == irq_id and event.event_name in {"IRQ_ENTER", "IRQ_EXIT"}][:4]
    related_events = [_event_summary(item) for item in irq_events]
    evidence_refs = [_event_ref(item) for item in irq_events]
    selection = _selection_payload(
        core_id=spans[0].core_id if spans else None,
        irq_id=irq_id,
    )
    return _detail_side_payload(
        bundle=bundle,
        dataset_role=dataset_role,
        focused_view="timeline",
        selection=selection,
        evidence_refs=evidence_refs,
        related_events=related_events,
        fallback_windows=[(item.t_begin, item.t_end) for item in spans] or [(t_begin, t_end)],
        view={
            "irq_id": irq_id,
            "busy_total": round(sum(_clipped_duration(item, t_begin, t_end) for item in spans), 6),
            "span_count": len(spans),
            "sample_spans": [_irq_span_summary(item) for item in spans],
        },
    )


def _alert_change_summary(
    alerts_a: list[Alert],
    alerts_b: list[Alert],
) -> list[dict[str, Any]]:
    counts_a: dict[str, int] = {}
    counts_b: dict[str, int] = {}
    severities_a: dict[str, Counter[str]] = {}
    severities_b: dict[str, Counter[str]] = {}
    for alert in alerts_a:
        counts_a[alert.type] = counts_a.get(alert.type, 0) + 1
        severities_a.setdefault(alert.type, Counter())[alert.severity] += 1
    for alert in alerts_b:
        counts_b[alert.type] = counts_b.get(alert.type, 0) + 1
        severities_b.setdefault(alert.type, Counter())[alert.severity] += 1

    def count_delta_split(delta: int) -> tuple[int, int]:
        return (delta if delta > 0 else 0, -delta if delta < 0 else 0)

    def severity_shift(alert_type: str) -> dict[str, Any]:
        baseline_counts = {
            level: int(count)
            for level, count in sorted(severities_a.get(alert_type, Counter()).items())
        }
        candidate_counts = {
            level: int(count)
            for level, count in sorted(severities_b.get(alert_type, Counter()).items())
        }
        delta_counts = {
            level: candidate_counts.get(level, 0) - baseline_counts.get(level, 0)
            for level in sorted(set(baseline_counts) | set(candidate_counts))
        }
        return {
            "changed": any(value != 0 for value in delta_counts.values()),
            "baseline": baseline_counts,
            "candidate": candidate_counts,
            "delta": delta_counts,
        }

    alert_types = sorted(set(counts_a) | set(counts_b))
    return [
        {
            "alert_type": alert_type,
            "baseline": counts_a.get(alert_type, 0),
            "candidate": counts_b.get(alert_type, 0),
            "delta": counts_b.get(alert_type, 0) - counts_a.get(alert_type, 0),
            "added": count_delta_split(counts_b.get(alert_type, 0) - counts_a.get(alert_type, 0))[0],
            "removed": count_delta_split(counts_b.get(alert_type, 0) - counts_a.get(alert_type, 0))[1],
            "severity_shift": severity_shift(alert_type),
            "evidence_refs": [],
        }
        for alert_type in alert_types
    ]


def _hotspot_change_summary(
    baseline: RebuildBundle,
    candidate: RebuildBundle,
) -> list[dict[str, Any]]:
    base_counts = {item["node_id"]: item["count"] for item in baseline.resource_graph.hotspot_stats}
    cand_counts = {item["node_id"]: item["count"] for item in candidate.resource_graph.hotspot_stats}
    rows: list[dict[str, Any]] = []
    for node_id in set(base_counts) | set(cand_counts):
        baseline_count = int(base_counts.get(node_id, 0))
        candidate_count = int(cand_counts.get(node_id, 0))
        delta = candidate_count - baseline_count
        rows.append(
            {
                "node_id": node_id,
                "baseline": baseline_count,
                "candidate": candidate_count,
                "delta": delta,
                "added": delta if delta > 0 else 0,
                "removed": -delta if delta < 0 else 0,
                "severity_shift": {
                    "impact_delta": {
                        "changed": delta != 0,
                        "direction": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
                    }
                },
                "evidence_refs": [],
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            -abs(int(item["delta"])),
            -max(int(item["baseline"]), int(item["candidate"])),
            str(item["node_id"]),
        ),
    )[:10]


def _interval_change_summary(
    baseline: RebuildBundle,
    candidate: RebuildBundle,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_window = _metric_window(baseline, t_begin, t_end, filter_spec)
    candidate_window = _metric_window(candidate, t_begin, t_end, filter_spec)
    baseline_blocked = max(
        (item.t_end - item.t_begin for item in baseline_window["states"] if item.state == "BLOCKED"),
        default=0.0,
    )
    candidate_blocked = max(
        (item.t_end - item.t_begin for item in candidate_window["states"] if item.state == "BLOCKED"),
        default=0.0,
    )
    delta = candidate_blocked - baseline_blocked
    return [
        {
            "interval_type": "max_blocked_segment",
            "baseline": baseline_blocked,
            "candidate": candidate_blocked,
            "delta": delta,
            "added": round(delta, 6) if delta > 0 else 0.0,
            "removed": round(-delta, 6) if delta < 0 else 0.0,
            "severity_shift": {
                "impact_delta": {
                    "changed": round(delta, 6) != 0.0,
                    "direction": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
                }
            },
            "evidence_refs": [],
        }
    ]


def _task_change_summary(
    baseline: RebuildBundle,
    candidate: RebuildBundle,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_window = _metric_window(baseline, t_begin, t_end, filter_spec)
    candidate_window = _metric_window(candidate, t_begin, t_end, filter_spec)

    def collect(window: dict[str, Any]) -> tuple[dict[int, float], dict[int, float]]:
        runtime: dict[int, float] = {}
        blocked: dict[int, float] = {}
        for item in window["slices"]:
            runtime[item.task_id] = runtime.get(item.task_id, 0.0) + _clipped_duration(item, t_begin, t_end)
        for item in window["states"]:
            if item.state != "BLOCKED":
                continue
            blocked[item.task_id] = blocked.get(item.task_id, 0.0) + _clipped_duration(item, t_begin, t_end)
        return runtime, blocked

    baseline_runtime, baseline_blocked = collect(baseline_window)
    candidate_runtime, candidate_blocked = collect(candidate_window)
    task_ids = sorted(set(baseline_runtime) | set(candidate_runtime) | set(baseline_blocked) | set(candidate_blocked))
    rows = [
        {
            "task_id": task_id,
            "baseline": round(baseline_runtime.get(task_id, 0.0), 6),
            "candidate": round(candidate_runtime.get(task_id, 0.0), 6),
            "delta": round(candidate_runtime.get(task_id, 0.0) - baseline_runtime.get(task_id, 0.0), 6),
            "baseline_blocked": round(baseline_blocked.get(task_id, 0.0), 6),
            "candidate_blocked": round(candidate_blocked.get(task_id, 0.0), 6),
            "blocked_delta": round(candidate_blocked.get(task_id, 0.0) - baseline_blocked.get(task_id, 0.0), 6),
        }
        for task_id in task_ids
    ]
    return sorted(rows, key=lambda item: (-abs(float(item["delta"])), item["task_id"]))[:10]


def _core_change_summary(
    baseline: RebuildBundle,
    candidate: RebuildBundle,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_window = _metric_window(baseline, t_begin, t_end, filter_spec)
    candidate_window = _metric_window(candidate, t_begin, t_end, filter_spec)

    def collect(window: dict[str, Any]) -> tuple[dict[int, float], dict[int, int]]:
        runtime: dict[int, float] = {}
        switches: dict[int, int] = {}
        for item in window["slices"]:
            runtime[item.core_id] = runtime.get(item.core_id, 0.0) + _clipped_duration(item, t_begin, t_end)
        for event in window["events"]:
            if event.event_name != "CTX_SWITCH":
                continue
            switches[event.core_id] = switches.get(event.core_id, 0) + 1
        return runtime, switches

    baseline_runtime, baseline_switches = collect(baseline_window)
    candidate_runtime, candidate_switches = collect(candidate_window)
    core_ids = sorted(set(baseline_runtime) | set(candidate_runtime) | set(baseline_switches) | set(candidate_switches))
    rows = [
        {
            "core_id": core_id,
            "baseline": round(baseline_runtime.get(core_id, 0.0), 6),
            "candidate": round(candidate_runtime.get(core_id, 0.0), 6),
            "delta": round(candidate_runtime.get(core_id, 0.0) - baseline_runtime.get(core_id, 0.0), 6),
            "baseline_switches": baseline_switches.get(core_id, 0),
            "candidate_switches": candidate_switches.get(core_id, 0),
            "switch_delta": candidate_switches.get(core_id, 0) - baseline_switches.get(core_id, 0),
        }
        for core_id in core_ids
    ]
    return sorted(rows, key=lambda item: (-abs(float(item["delta"])), item["core_id"]))[:10]


def _resource_change_summary(
    baseline: RebuildBundle,
    candidate: RebuildBundle,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_window = _metric_window(baseline, t_begin, t_end, filter_spec)
    candidate_window = _metric_window(candidate, t_begin, t_end, filter_spec)

    def collect(window: dict[str, Any]) -> tuple[dict[int, int], dict[int, float]]:
        event_counts: dict[int, int] = {}
        blocked: dict[int, float] = {}
        for event in window["events"]:
            if event.obj_id is None:
                continue
            resource_id = int(event.obj_id)
            event_counts[resource_id] = event_counts.get(resource_id, 0) + 1
        for segment in window["states"]:
            if segment.related_obj is None or segment.state != "BLOCKED":
                continue
            resource_id = int(segment.related_obj)
            blocked[resource_id] = blocked.get(resource_id, 0.0) + _clipped_duration(segment, t_begin, t_end)
        return event_counts, blocked

    baseline_counts, baseline_blocked = collect(baseline_window)
    candidate_counts, candidate_blocked = collect(candidate_window)
    resource_ids = sorted(set(baseline_counts) | set(candidate_counts) | set(baseline_blocked) | set(candidate_blocked))
    rows = [
        {
            "resource_id": resource_id,
            "baseline": baseline_counts.get(resource_id, 0),
            "candidate": candidate_counts.get(resource_id, 0),
            "delta": candidate_counts.get(resource_id, 0) - baseline_counts.get(resource_id, 0),
            "baseline_blocked": round(baseline_blocked.get(resource_id, 0.0), 6),
            "candidate_blocked": round(candidate_blocked.get(resource_id, 0.0), 6),
            "blocked_delta": round(candidate_blocked.get(resource_id, 0.0) - baseline_blocked.get(resource_id, 0.0), 6),
        }
        for resource_id in resource_ids
    ]
    return sorted(rows, key=lambda item: (-abs(float(item["delta"])), item["resource_id"]))[:10]


def _irq_change_summary(
    baseline: RebuildBundle,
    candidate: RebuildBundle,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_window = _metric_window(baseline, t_begin, t_end, filter_spec)
    candidate_window = _metric_window(candidate, t_begin, t_end, filter_spec)

    def collect(window: dict[str, Any]) -> tuple[dict[int, float], dict[int, int]]:
        busy: dict[int, float] = {}
        counts: dict[int, int] = {}
        for span in window["irqs"]:
            busy[span.irq_id] = busy.get(span.irq_id, 0.0) + _clipped_duration(span, t_begin, t_end)
            counts[span.irq_id] = counts.get(span.irq_id, 0) + 1
        return busy, counts

    baseline_busy, baseline_counts = collect(baseline_window)
    candidate_busy, candidate_counts = collect(candidate_window)
    irq_ids = sorted(set(baseline_busy) | set(candidate_busy) | set(baseline_counts) | set(candidate_counts))
    rows = [
        {
            "irq_id": irq_id,
            "baseline": round(baseline_busy.get(irq_id, 0.0), 6),
            "candidate": round(candidate_busy.get(irq_id, 0.0), 6),
            "delta": round(candidate_busy.get(irq_id, 0.0) - baseline_busy.get(irq_id, 0.0), 6),
            "baseline_count": baseline_counts.get(irq_id, 0),
            "candidate_count": candidate_counts.get(irq_id, 0),
            "count_delta": candidate_counts.get(irq_id, 0) - baseline_counts.get(irq_id, 0),
        }
        for irq_id in irq_ids
    ]
    return sorted(rows, key=lambda item: (-abs(float(item["delta"])), item["irq_id"]))[:10]


def _detail_target(dimension: str, id_key: str, target_id: Any) -> dict[str, Any]:
    return {"dimension": dimension, id_key: target_id}


def _dimension_detail_rows(
    scope: CompareScope,
    dimension: str,
    rows: list[dict[str, Any]],
    id_key: str,
    baseline: RebuildBundle,
    candidate: RebuildBundle,
    t_begin: float,
    t_end: float,
    filter_spec: dict[str, Any],
    alerts_a: list[Alert],
    alerts_b: list[Alert],
    trusted: bool,
) -> list[DiffDetail]:
    details: list[DiffDetail] = []
    baseline_alert_window = _metric_window(baseline, t_begin, t_end, filter_spec) if dimension == "alert" else None
    candidate_alert_window = _metric_window(candidate, t_begin, t_end, filter_spec) if dimension == "alert" else None
    for row in rows:
        target_id = row.get(id_key, "summary")
        if dimension == "alert":
            baseline_side = _alert_detail_side(baseline, "baseline", baseline_alert_window or {}, alerts_a, str(target_id))
            candidate_side = _alert_detail_side(candidate, "candidate", candidate_alert_window or {}, alerts_b, str(target_id))
        elif dimension == "hotspot":
            baseline_side = _hotspot_detail_side(baseline, "baseline", str(target_id), t_begin, t_end, filter_spec)
            candidate_side = _hotspot_detail_side(candidate, "candidate", str(target_id), t_begin, t_end, filter_spec)
        elif dimension == "interval":
            baseline_side = _interval_detail_side(baseline, "baseline", str(target_id), t_begin, t_end, filter_spec)
            candidate_side = _interval_detail_side(candidate, "candidate", str(target_id), t_begin, t_end, filter_spec)
        elif dimension == "task":
            baseline_side = _task_detail_side(baseline, "baseline", int(target_id), t_begin, t_end, filter_spec)
            candidate_side = _task_detail_side(candidate, "candidate", int(target_id), t_begin, t_end, filter_spec)
        elif dimension == "core":
            baseline_side = _core_detail_side(baseline, "baseline", int(target_id), t_begin, t_end, filter_spec)
            candidate_side = _core_detail_side(candidate, "candidate", int(target_id), t_begin, t_end, filter_spec)
        elif dimension == "resource":
            baseline_side = _resource_detail_side(baseline, "baseline", int(target_id), t_begin, t_end, filter_spec)
            candidate_side = _resource_detail_side(candidate, "candidate", int(target_id), t_begin, t_end, filter_spec)
        elif dimension == "irq":
            baseline_side = _irq_detail_side(baseline, "baseline", int(target_id), t_begin, t_end, filter_spec)
            candidate_side = _irq_detail_side(candidate, "candidate", int(target_id), t_begin, t_end, filter_spec)
        else:
            baseline_side = {
                "evidence_refs": [],
                "related_events": [],
                "view": {"dimension": dimension, "value": row.get("baseline", 0.0)},
                "jump_target": None,
            }
            candidate_side = {
                "evidence_refs": [],
                "related_events": [],
                "view": {"dimension": dimension, "value": row.get("candidate", 0.0)},
                "jump_target": None,
            }
        combined_evidence = _dedupe_evidence_refs(
            list(baseline_side["evidence_refs"]) + list(candidate_side["evidence_refs"])
        )
        target = _detail_target(dimension, id_key, target_id)
        delta_payload = dict(row)
        if dimension in {"alert", "hotspot", "interval"}:
            row["evidence_refs"] = combined_evidence
            delta_payload["evidence_refs"] = combined_evidence
        details.append(
            DiffDetail(
                diff_id=f"diff:{scope.scope_id or 'auto'}:{dimension}:{target_id}",
                scope=scope,
                target=target,
                baseline_view=baseline_side["view"],
                candidate_view=candidate_side["view"],
                delta_payload=delta_payload,
                evidence_refs=combined_evidence,
                related_events=_dedupe_related_events(
                    list(baseline_side["related_events"]) + list(candidate_side["related_events"])
                ),
                jump_target=_combine_jump_targets(
                    baseline_side.get("jump_target"),
                    candidate_side.get("jump_target"),
                ),
                trusted=trusted,
            )
        )
    return details


def metric_Compare(
    baseline: RebuildBundle,
    candidate: RebuildBundle,
    scope: dict[str, Any],
    cfg: MetricConfig | None = None,
) -> Result[DiffBundle]:
    config = cfg or MetricConfig()
    session_a = MetricSession(cfg=config, bundle=baseline)
    session_b = MetricSession(cfg=config, bundle=candidate)
    t_begin, t_end = scope["aligned_time_window"]
    if float(t_begin) >= float(t_end):
        return err_result("INVALID_ARG", "aligned_time_window has no overlap")
    metrics_a = metric_Compute(session_a, t_begin, t_end, scope.get("filter", {}))
    metrics_b = metric_Compute(session_b, t_begin, t_end, scope.get("filter", {}))
    alerts_a = alert_Evaluate(session_a, t_begin, t_end, scope.get("filter", {})).data or []
    alerts_b = alert_Evaluate(session_b, t_begin, t_end, scope.get("filter", {})).data or []
    detail_rows: list[DiffDetail] = []
    summary_rows: list[dict[str, Any]] = []
    metric_ids = scope.get("metric_ids") or config.compare_metric_ids
    dimensions = list(dict.fromkeys(scope.get("dimensions") or ["metric"]))
    requested_dimensions = set(dimensions)
    trust_summary = _trust_summary(metrics_a.untrusted_windows, metrics_b.untrusted_windows)
    degraded_reason = _degraded_reason(metrics_a.untrusted_windows, metrics_b.untrusted_windows)
    summary_scope = CompareScope(
        baseline_id=scope["baseline_id"],
        candidate_id=scope["candidate_id"],
        aligned_time_window=(float(t_begin), float(t_end)),
        filter=scope.get("filter", {}),
        dimensions=dimensions,
        metric_ids=list(metric_ids),
        bucket_size=scope.get("bucket_size"),
        evidence_policy=scope.get("evidence_policy"),
        scope_id=scope.get("scope_id", "auto"),
    )
    scope_trusted = bool(trust_summary["trusted"])
    if "metric" in requested_dimensions:
        for metric_id in metric_ids:
            base_value = _metric_value(metrics_a.data, metric_id)
            cand_value = _metric_value(metrics_b.data, metric_id)
            delta = cand_value - base_value
            ratio = None if base_value == 0 else cand_value / base_value
            base_evidence, baseline_view, baseline_related = _metric_evidence(
                baseline,
                metric_id,
                t_begin,
                t_end,
                scope.get("filter", {}),
            )
            candidate_evidence, candidate_view, candidate_related = _metric_evidence(
                candidate,
                metric_id,
                t_begin,
                t_end,
                scope.get("filter", {}),
            )
            baseline_related = _dedupe_related_events(baseline_related)
            candidate_related = _dedupe_related_events(candidate_related)
            baseline_view = {
                **baseline_view,
                "dataset_id": baseline.dataset_id,
                "dataset_role": "baseline",
                "evidence_anchor": _event_anchor(baseline_related),
            }
            candidate_view = {
                **candidate_view,
                "dataset_id": candidate.dataset_id,
                "dataset_role": "candidate",
                "evidence_anchor": _event_anchor(candidate_related),
            }
            base_selection = _selection_from_filter(scope.get("filter", {})) or _selection_from_event_summary(
                baseline_related[0] if baseline_related else None
            )
            candidate_selection = _selection_from_filter(scope.get("filter", {})) or _selection_from_event_summary(
                candidate_related[0] if candidate_related else None
            )
            baseline_jump = None
            candidate_jump = None
            baseline_window = _time_window_from_ranges(baseline_related, base_evidence, [(t_begin, t_end)])
            candidate_window = _time_window_from_ranges(candidate_related, candidate_evidence, [(t_begin, t_end)])
            if baseline_window is not None:
                baseline_jump = {
                    "dataset_id": baseline.dataset_id,
                    "dataset_role": "baseline",
                    "time_window": list(baseline_window),
                    "selection": base_selection,
                    "evidence_anchor": _event_anchor(baseline_related),
                    "focused_view": "timeline",
                }
            if candidate_window is not None:
                candidate_jump = {
                    "dataset_id": candidate.dataset_id,
                    "dataset_role": "candidate",
                    "time_window": list(candidate_window),
                    "selection": candidate_selection,
                    "evidence_anchor": _event_anchor(candidate_related),
                    "focused_view": "timeline",
                }
            detail_rows.append(
                DiffDetail(
                    diff_id=f"diff:{summary_scope.scope_id or 'auto'}:metric:{metric_id}",
                    scope=summary_scope,
                    target=_detail_target("metric", "metric_id", metric_id),
                    baseline_view=baseline_view,
                    candidate_view=candidate_view,
                    delta_payload={
                        "baseline": base_value,
                        "candidate": cand_value,
                        "delta": delta,
                        "ratio": ratio,
                    },
                    evidence_refs=_dedupe_evidence_refs(base_evidence + candidate_evidence),
                    related_events=_dedupe_related_events(baseline_related + candidate_related),
                    jump_target=_combine_jump_targets(baseline_jump, candidate_jump),
                    trusted=scope_trusted,
                )
            )
            row = {
                "metric_id": metric_id,
                "baseline": base_value,
                "candidate": cand_value,
                "delta": delta,
                "ratio": ratio,
                "trend": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
            }
            if degraded_reason is not None:
                row["degraded_reason"] = degraded_reason
            summary_rows.append(row)

    alert_changes = _alert_change_summary(alerts_a, alerts_b) if "alert" in requested_dimensions else []
    hotspot_changes = _hotspot_change_summary(baseline, candidate) if "hotspot" in requested_dimensions else []
    interval_changes = (
        _interval_change_summary(baseline, candidate, t_begin, t_end, scope.get("filter", {}))
        if "interval" in requested_dimensions
        else []
    )
    task_changes = (
        _task_change_summary(baseline, candidate, t_begin, t_end, scope.get("filter", {}))
        if "task" in requested_dimensions
        else []
    )
    core_changes = (
        _core_change_summary(baseline, candidate, t_begin, t_end, scope.get("filter", {}))
        if "core" in requested_dimensions
        else []
    )
    resource_changes = (
        _resource_change_summary(baseline, candidate, t_begin, t_end, scope.get("filter", {}))
        if "resource" in requested_dimensions
        else []
    )
    irq_changes = (
        _irq_change_summary(baseline, candidate, t_begin, t_end, scope.get("filter", {}))
        if "irq" in requested_dimensions
        else []
    )

    detail_rows.extend(
        _dimension_detail_rows(
            summary_scope,
            "alert",
            alert_changes,
            "alert_type",
            baseline,
            candidate,
            t_begin,
            t_end,
            scope.get("filter", {}),
            alerts_a,
            alerts_b,
            scope_trusted,
        )
    )
    detail_rows.extend(
        _dimension_detail_rows(
            summary_scope,
            "hotspot",
            hotspot_changes,
            "node_id",
            baseline,
            candidate,
            t_begin,
            t_end,
            scope.get("filter", {}),
            alerts_a,
            alerts_b,
            scope_trusted,
        )
    )
    detail_rows.extend(
        _dimension_detail_rows(
            summary_scope,
            "interval",
            interval_changes,
            "interval_type",
            baseline,
            candidate,
            t_begin,
            t_end,
            scope.get("filter", {}),
            alerts_a,
            alerts_b,
            scope_trusted,
        )
    )
    detail_rows.extend(
        _dimension_detail_rows(
            summary_scope,
            "task",
            task_changes,
            "task_id",
            baseline,
            candidate,
            t_begin,
            t_end,
            scope.get("filter", {}),
            alerts_a,
            alerts_b,
            scope_trusted,
        )
    )
    detail_rows.extend(
        _dimension_detail_rows(
            summary_scope,
            "core",
            core_changes,
            "core_id",
            baseline,
            candidate,
            t_begin,
            t_end,
            scope.get("filter", {}),
            alerts_a,
            alerts_b,
            scope_trusted,
        )
    )
    detail_rows.extend(
        _dimension_detail_rows(
            summary_scope,
            "resource",
            resource_changes,
            "resource_id",
            baseline,
            candidate,
            t_begin,
            t_end,
            scope.get("filter", {}),
            alerts_a,
            alerts_b,
            scope_trusted,
        )
    )
    detail_rows.extend(
        _dimension_detail_rows(
            summary_scope,
            "irq",
            irq_changes,
            "irq_id",
            baseline,
            candidate,
            t_begin,
            t_end,
            scope.get("filter", {}),
            alerts_a,
            alerts_b,
            scope_trusted,
        )
    )
    summary = DiffSummary(
        scope=summary_scope,
        metric_changes=summary_rows,
        alert_changes=alert_changes,
        hotspot_changes=hotspot_changes,
        interval_changes=interval_changes,
        task_changes=task_changes,
        core_changes=core_changes,
        resource_changes=resource_changes,
        irq_changes=irq_changes,
        trust_summary=trust_summary,
    )
    return ok_result(DiffBundle(summary=summary, details=detail_rows), untrusted_windows=metrics_a.untrusted_windows + metrics_b.untrusted_windows)


def metric_Export(
    session: MetricSession,
    export_format: str,
    scope: dict[str, Any],
    t_begin: float,
    t_end: float,
) -> Result[list[dict[str, Any]]]:
    metrics = metric_Compute(session, t_begin, t_end, scope.get("filter", {}))
    if not metrics.ok:
        return Result(
            code=metrics.code,
            message=metrics.message,
            warnings=metrics.warnings,
            untrusted_windows=metrics.untrusted_windows,
        )
    if export_format not in {"json", "dict"}:
        return err_result("INVALID_ARG", f"unsupported export format: {export_format}")
    return ok_result([metric.__dict__ for metric in metrics.data], untrusted_windows=metrics.untrusted_windows)

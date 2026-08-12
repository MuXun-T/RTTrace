from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from collections.abc import Iterator, Mapping
from typing import Any


def dataclass_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_to_dict(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: dataclass_to_dict(val) for key, val in value.items()}
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, tuple):
        return [dataclass_to_dict(item) for item in value]
    return value


@dataclass
class GlobalHeader:
    magic: str
    endian: int
    time_unit: int
    clock_source: int
    format_ver: int
    dict_ver: int
    producer_ver: str
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass
class ChunkHeader:
    chunk_start_ts: int
    chunk_end_ts: int
    core_mask: int
    record_count: int
    seq_start: int
    seq_end: int
    dict_ver: int
    chunk_crc: int


@dataclass
class SegmentMeta:
    segment_seq: int
    prev_segment_seq: int
    dict_ver: int
    dict_ref_algo: int
    dict_ref_checksum: int
    header_ver: int
    meta_size: int


@dataclass
class EvidenceRef:
    ref_type: str
    ref_key: str
    t_begin: float
    t_end: float


@dataclass
class UntrustedWindow:
    window_id: str
    source: str
    scope: str
    t_begin: float
    t_end: float
    reason_code: str
    severity: str


@dataclass(slots=True)
class UnifiedEvent:
    event_uid: str
    core_id: int
    seq: int
    timestamp_raw: float
    timestamp_aligned: float
    event_id: int
    event_name: str
    task_id: int | None
    obj_id: int | None
    irq_id: int | None
    job_id: int | None
    instance_id: int | None
    payload: dict[str, Any]
    sort_key: tuple[float, int, int]
    trust_tags: list[str]
    chunk_id: int
    ref_key: str


@dataclass(slots=True)
class DecodedEvent:
    core_id: int
    seq: int
    timestamp_raw: float
    timestamp_aligned: float
    event_id: int
    event_name: str
    task_id: int | None
    obj_id: int | None
    irq_id: int | None
    job_id: int | None
    instance_id: int | None
    payload: dict[str, Any]
    trust_tags: list[str]
    chunk_id: int


def event_sort_key(event: UnifiedEvent | DecodedEvent) -> tuple[float, int, int]:
    if isinstance(event, UnifiedEvent):
        return event.sort_key
    return (event.timestamp_aligned, event.core_id, event.seq)


def event_uid_for(event: UnifiedEvent | DecodedEvent, dataset_id: str) -> str:
    if isinstance(event, UnifiedEvent):
        return event.event_uid
    return f"evt:{dataset_id}:{event.core_id}:{event.seq}"


def event_ref_key_for(event: UnifiedEvent | DecodedEvent, dataset_id: str) -> str:
    if isinstance(event, UnifiedEvent):
        return event.ref_key
    return event_uid_for(event, dataset_id)


def materialize_unified_event(event: UnifiedEvent | DecodedEvent, dataset_id: str) -> UnifiedEvent:
    if isinstance(event, UnifiedEvent):
        return event
    event_key = event_uid_for(event, dataset_id)
    return UnifiedEvent(
        event_uid=event_key,
        core_id=event.core_id,
        seq=event.seq,
        timestamp_raw=event.timestamp_raw,
        timestamp_aligned=event.timestamp_aligned,
        event_id=event.event_id,
        event_name=event.event_name,
        task_id=event.task_id,
        obj_id=event.obj_id,
        irq_id=event.irq_id,
        job_id=event.job_id,
        instance_id=event.instance_id,
        payload=event.payload,
        sort_key=(event.timestamp_aligned, event.core_id, event.seq),
        trust_tags=event.trust_tags,
        chunk_id=event.chunk_id,
        ref_key=event_key,
    )


@dataclass
class ExecSlice:
    slice_id: str
    task_id: int
    core_id: int
    job_id: int | None
    instance_id: int | None
    t_begin: float
    t_end: float
    start_event: str
    end_event: str | None
    preempted_by: int | None
    run_reason: str | None
    trusted: bool
    lineage_id: str | None = None


@dataclass
class SwitchPoint:
    switch_id: str
    core_id: int
    timestamp: float
    prev_task_id: int | None
    next_task_id: int | None
    reason: str | None
    event_ref: str
    trusted: bool


@dataclass
class TaskStateSeg:
    seg_id: str
    task_id: int
    state: str
    job_id: int | None
    instance_id: int | None
    t_begin: float
    t_end: float
    cause_event: str
    related_obj: int | None
    trusted: bool
    lineage_id: str | None = None


TaskStateSegment = TaskStateSeg


@dataclass
class ResourceEdge(Mapping[str, Any]):
    """Typed resource relation with a read-only legacy mapping projection."""

    edge_id: str
    edge_kind: str
    task_id: int | None
    obj_id: int | None
    owner_task_id: int | None
    obj_type: int | None
    t_begin: float
    t_end: float
    lineage_id: str | None = None
    evidence_ref: str | None = None
    trusted: bool = True

    @property
    def from_task(self) -> int | None:
        return self.task_id

    @property
    def to_obj(self) -> int | None:
        return self.obj_id

    @property
    def owner_task(self) -> int | None:
        return self.owner_task_id

    def _compatibility_mapping(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "edge_kind": self.edge_kind,
            "task_id": self.task_id,
            "obj_id": self.obj_id,
            "owner_task_id": self.owner_task_id,
            "obj_type": self.obj_type,
            "t_begin": self.t_begin,
            "t_end": self.t_end,
            "lineage_id": self.lineage_id,
            "evidence_ref": self.evidence_ref,
            "trusted": self.trusted,
            "from_task": self.from_task,
            "to_obj": self.to_obj,
            "owner_task": self.owner_task,
        }

    def to_dict(self) -> dict[str, Any]:
        return self._compatibility_mapping()

    def __getitem__(self, key: str) -> Any:
        return self._compatibility_mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._compatibility_mapping())

    def __len__(self) -> int:
        return len(self._compatibility_mapping())


@dataclass
class ResourceGraph:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    hold_edges: list[ResourceEdge] = field(default_factory=list)
    wait_edges: list[ResourceEdge] = field(default_factory=list)
    hotspot_stats: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IrqSpan:
    irq_span_id: str
    irq_id: int
    core_id: int
    nesting_depth: int
    t_begin: float
    t_end: float
    delayed_task: int | None
    trusted: bool
    enter_event_id: str | None = None
    exit_event_id: str | None = None
    lineage_id: str | None = None


@dataclass
class ReadyNotRunningInterval:
    interval_id: str
    task_id: int
    core_id: int | None
    t_begin: float
    t_end: float
    competing_exec_slice_ids: list[str] = field(default_factory=list)
    competing_irq_span_ids: list[str] = field(default_factory=list)
    lineage_id: str | None = None
    trusted: bool = True


@dataclass
class IndexBundle:
    time_index: list[dict[str, Any]]
    task_index: dict[int, list[str]]
    core_index: dict[int, list[str]]
    event_type_index: dict[str, list[str]]
    summary: dict[str, Any]


@dataclass
class AlignmentSummary:
    core_ids: list[int]
    offsets: dict[int, float]
    anchors_seen: bool
    calibrated: bool
    reference_core_id: int | None = None
    segments: list["CoreAlignSegment"] = field(default_factory=list)


@dataclass
class CoreAlignSegment:
    core_id: int
    t_begin: float
    t_end: float
    offset_ns: float
    drift_ppm: float = 0.0
    confidence: str = "exact"


@dataclass
class RebuildBundle:
    bundle_id: str
    dataset_id: str
    event_stream: list[UnifiedEvent]
    task_states: list[TaskStateSeg]
    exec_slices: list[ExecSlice]
    resource_graph: ResourceGraph
    irq_spans: list[IrqSpan]
    untrusted_windows: list[UntrustedWindow]
    rebuild_rev: int
    capability_flags: dict[str, bool]
    alignment: AlignmentSummary | None = None
    segment_metas: list[SegmentMeta] = field(default_factory=list)
    header: GlobalHeader | None = None
    index_bundle: IndexBundle | None = None
    capture_id: str | None = None
    capture_capability_manifest_ref: str | None = None
    capture_integrity_record_ref: str | None = None
    ready_not_running_intervals: list[ReadyNotRunningInterval] = field(default_factory=list)
    lineage_registry: Any | None = None

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Read pre-P3 parser-cache pickles without assigning lineage truth."""

        self.__dict__.update(state)
        self.__dict__.setdefault("capture_id", None)
        self.__dict__.setdefault("capture_capability_manifest_ref", None)
        self.__dict__.setdefault("capture_integrity_record_ref", None)
        self.__dict__.setdefault("ready_not_running_intervals", [])
        self.__dict__.setdefault("lineage_registry", None)
        graph = self.__dict__.get("resource_graph")
        if not isinstance(graph, ResourceGraph):
            return
        for edge_kind, field_name in (("hold", "hold_edges"), ("wait", "wait_edges")):
            normalized: list[ResourceEdge] = []
            for index, edge in enumerate(list(getattr(graph, field_name, []) or [])):
                if isinstance(edge, ResourceEdge):
                    normalized.append(edge)
                    continue
                if not isinstance(edge, dict):
                    normalized.append(edge)
                    continue
                task_id = edge.get("task_id", edge.get("from_task"))
                obj_id = edge.get("obj_id", edge.get("to_obj"))
                owner_task_id = edge.get("owner_task_id", edge.get("owner_task"))
                normalized.append(
                    ResourceEdge(
                        edge_id=str(edge.get("edge_id") or f"legacy-edge:{edge_kind}:{index}"),
                        edge_kind=str(edge.get("edge_kind") or edge_kind),
                        task_id=None if task_id is None else int(task_id),
                        obj_id=None if obj_id is None else int(obj_id),
                        owner_task_id=None if owner_task_id is None else int(owner_task_id),
                        obj_type=None if edge.get("obj_type") is None else int(edge["obj_type"]),
                        t_begin=float(edge.get("t_begin", 0.0)),
                        t_end=float(edge.get("t_end", edge.get("t_begin", 0.0))),
                        lineage_id=edge.get("lineage_id"),
                        evidence_ref=edge.get("evidence_ref"),
                        trusted=bool(edge.get("trusted", True)),
                    )
                )
            setattr(graph, field_name, normalized)

    def to_dict(self) -> dict[str, Any]:
        payload = dataclass_to_dict(self)
        payload["resource_graph"] = {
            "nodes": dataclass_to_dict(self.resource_graph.nodes),
            "hold_edges": [
                item.to_dict() if isinstance(item, ResourceEdge) else dataclass_to_dict(item)
                for item in self.resource_graph.hold_edges
            ],
            "wait_edges": [
                item.to_dict() if isinstance(item, ResourceEdge) else dataclass_to_dict(item)
                for item in self.resource_graph.wait_edges
            ],
            "hotspot_stats": dataclass_to_dict(self.resource_graph.hotspot_stats),
        }
        if self.lineage_registry is not None and hasattr(self.lineage_registry, "to_dict"):
            payload["lineage_registry"] = self.lineage_registry.to_dict()
        return payload


@dataclass
class MetricResult:
    metric_id: str
    scope: dict[str, Any]
    series: list[dict[str, Any]]
    distribution: list[dict[str, Any]]
    topn: list[dict[str, Any]]
    summary: dict[str, Any]
    trusted: bool


@dataclass
class Alert:
    alert_id: str
    type: str
    severity: str
    time_window: tuple[float, float]
    object_scope: dict[str, Any]
    threshold: float
    actual: float
    evidence_refs: list[EvidenceRef]
    trusted: bool
    support_level: str = "exact"


@dataclass
class Diagnosis:
    diag_id: str
    title: str
    diagnosis_type: str
    time_window: tuple[float, float]
    object_scope: dict[str, Any]
    conclusion: str
    evidence_refs: list[EvidenceRef]
    related_alerts: list[str]
    confidence: str
    support_level: str = "exact"


@dataclass
class CompareScope:
    baseline_id: str
    candidate_id: str
    aligned_time_window: tuple[float, float]
    filter: dict[str, Any]
    dimensions: list[str]
    metric_ids: list[str]
    bucket_size: float | None = None
    evidence_policy: str | None = None
    scope_id: str | None = None


@dataclass
class DiffDetail:
    diff_id: str
    scope: CompareScope
    target: dict[str, Any]
    baseline_view: dict[str, Any]
    candidate_view: dict[str, Any]
    delta_payload: dict[str, Any]
    evidence_refs: list[EvidenceRef]
    related_events: list[dict[str, Any]]
    jump_target: dict[str, Any] | None
    trusted: bool


@dataclass
class DiffSummary:
    scope: CompareScope
    metric_changes: list[dict[str, Any]] = field(default_factory=list)
    alert_changes: list[dict[str, Any]] = field(default_factory=list)
    hotspot_changes: list[dict[str, Any]] = field(default_factory=list)
    interval_changes: list[dict[str, Any]] = field(default_factory=list)
    task_changes: list[dict[str, Any]] = field(default_factory=list)
    core_changes: list[dict[str, Any]] = field(default_factory=list)
    resource_changes: list[dict[str, Any]] = field(default_factory=list)
    irq_changes: list[dict[str, Any]] = field(default_factory=list)
    trust_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffBundle:
    summary: DiffSummary
    details: list[DiffDetail]


@dataclass
class AnalysisContext:
    time_window: tuple[float, float] = (0.0, 0.0)
    filter: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    zoom_level: float = 1.0
    focused_view: str | None = None
    evidence_anchor: dict[str, Any] | None = None
    playback_cursor: dict[str, Any] | None = None
    compare_scope: dict[str, Any] | None = None
    dataset_role: str | None = None
    context_rev: int = 0
    pending_jobs: list[str] = field(default_factory=list)
    hover_target: dict[str, Any] | None = None
    transient_selection: dict[str, Any] | None = None
    playback_runtime: dict[str, Any] | None = None

    def persisted_dict(self) -> dict[str, Any]:
        return {
            "time_window": list(self.time_window),
            "filter": self.filter,
            "selection": self.selection,
            "zoom_level": self.zoom_level,
            "focused_view": self.focused_view,
            "evidence_anchor": self.evidence_anchor,
            "playback_cursor": self.playback_cursor,
            "compare_scope": self.compare_scope,
            "dataset_role": self.dataset_role,
        }


@dataclass
class Bookmark:
    bookmark_id: str
    label: str
    time_window: tuple[float, float]
    filter: dict[str, Any]
    selection: dict[str, Any]
    focused_view: str | None
    evidence_anchor: dict[str, Any] | None


@dataclass
class PlaybackState:
    replay_id: str
    status: str
    rate: float
    cursor: dict[str, Any] | None = None
    anchor_ref: dict[str, Any] | None = None
    visible_window: tuple[float, float] | None = None
    linked_views: list[str] = field(default_factory=list)
    trusted: bool = True


@dataclass
class EventCursor:
    ts: float
    core_id: int
    seq: int
    sort_key: tuple[float, int, int]


@dataclass
class EventPage:
    items: list[UnifiedEvent]
    order_by: str
    cursor_in: EventCursor | None
    next_cursor: EventCursor | None
    prev_cursor: EventCursor | None
    has_more: bool
    total_hint: int
    trusted: bool
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadinessState:
    stage: str
    source: str
    preview: bool
    lod_ready: dict[str, bool] = field(default_factory=dict)
    view_ready: dict[str, bool] = field(default_factory=dict)
    fallback_reason: str | None = None


@dataclass
class LoadPreview:
    dataset_id: str
    header: dict[str, Any]
    time_window: tuple[float, float]
    chunk_count: int
    record_count: int
    core_ids: list[int]
    lod0_buckets: list[dict[str, Any]]
    source: str
    stage: str
    readiness: ReadinessState
    task_state_preview: "TaskStatePreview | None" = None


@dataclass
class TaskStatePreview:
    time_window: tuple[float, float]
    lane_count: int
    task_ids: list[int]
    state_totals: dict[str, int]
    bucket_summary: list[dict[str, Any]]
    readiness: ReadinessState
    trusted: bool = True


@dataclass
class TaskStateQuery:
    time_window: tuple[float, float]
    lane_group: str = "task"
    state_mask: list[str] = field(default_factory=list)
    task_filter: list[int] = field(default_factory=list)
    anchor_ref: dict[str, Any] | None = None
    include_summary: bool = True


@dataclass
class TaskStateViewSegment:
    seg_id: str
    state: str
    t_begin: float
    t_end: float
    evidence_ref: EvidenceRef
    trusted: bool
    task_id: int
    core_id: int | None = None
    related_obj: int | None = None
    cause_event: str | None = None


@dataclass
class TaskStateViewRow:
    task_id: int
    lane_label: str
    segments: list[TaskStateViewSegment]
    state_counts: dict[str, int]
    time_window: tuple[float, float]
    trusted: bool = True


@dataclass
class EventTableQuery:
    filter: dict[str, Any]
    cursor: EventCursor | None = None
    limit: int = 100
    order_key: str = "sort_key"
    direction: str = "forward"


@dataclass
class TaskStateViewModel:
    time_window: tuple[float, float]
    lane_order: list[str]
    rows: list[TaskStateViewRow]
    state_legend: dict[str, str]
    summary: dict[str, Any]
    cursor_hint: str | None = None
    trusted: bool = True


@dataclass
class TimelinePayload:
    lod: int
    window: tuple[float, float]
    slices: list[ExecSlice] = field(default_factory=list)
    switch_points: list[SwitchPoint] = field(default_factory=list)
    irq_spans: list[IrqSpan] = field(default_factory=list)
    buckets: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricSeries:
    metric_id: str
    points: list[dict[str, Any]]
    summary: dict[str, Any]


@dataclass
class DatasetArtifact:
    dataset_id: str
    source: str
    header: GlobalHeader
    bundle: RebuildBundle
    dictionary_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedResult:
    decoded_records: int
    gaps: list[UntrustedWindow]
    warnings: list[str]
    online_cursor: EventCursor | None = None


@dataclass
class ParseSummary:
    dataset_id: str
    chunk_count: int
    segment_count: int
    event_count: int
    warning_count: int
    untrusted_window_count: int
    final_cursor: EventCursor | None = None

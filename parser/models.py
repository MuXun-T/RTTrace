from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
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
    end_event: str
    preempted_by: int | None
    run_reason: str | None
    trusted: bool


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


@dataclass
class ResourceGraph:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    hold_edges: list[dict[str, Any]] = field(default_factory=list)
    wait_edges: list[dict[str, Any]] = field(default_factory=list)
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

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


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

"""Legacy public model exports.

The canonical runtime models now live in ``parser.models``. This module keeps
historical imports stable while publishing enough metadata for callers and
tests to tell which names have a canonical runtime peer and which remain
legacy-only compatibility exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import parser.models as canonical_models

CANONICAL_RUNTIME_MODEL_MODULE = "parser.models"
CANONICAL_PEER_MODEL_NAMES = frozenset(
    {
        "Alert",
        "AnalysisContext",
        "Bookmark",
        "CompareScope",
        "DiffBundle",
        "DiffDetail",
        "DiffSummary",
        "EventCursor",
        "EventPage",
        "EvidenceRef",
        "ExecSlice",
        "GlobalHeader",
        "IndexBundle",
        "IrqSpan",
        "MetricResult",
        "PlaybackState",
        "RebuildBundle",
        "ResourceGraph",
        "SegmentMeta",
        "TaskStateQuery",
        "TaskStateSeg",
        "TaskStateViewRow",
        "TaskStateViewSegment",
        "TaskStateViewModel",
        "UnifiedEvent",
        "UntrustedWindow",
    }
)
LEGACY_ONLY_MODEL_NAMES = frozenset(
    {
        "Dataset",
    }
)
RETIRED_LEGACY_MODEL_NAMES = frozenset(
    {
        "ExportJob",
        "HoldEdge",
        "MetricSession",
        "PackageResult",
        "ReproSession",
        "WaitEdge",
    }
)
LEGACY_MODEL_OWNER_MAP = {
    "Dataset": (
        "parser.models.DatasetArtifact + desktop.repository.DatasetRecord + "
        "DatasetHandle(runtime currently Result[str], explicit DTO still pending)"
    ),
    "ExportJob": "desktop.services.ExportService.export_Full/export_Clipped -> Result[{job_id}]",
    "HoldEdge": "parser.models.ResourceGraph.hold_edges[*] (dict edge payload)",
    "MetricSession": "metric.core.MetricSession",
    "PackageResult": (
        "desktop.services.ExportService.export_WritePackage -> "
        "Result[{package_path, entry_count, snapshot_id}]"
    ),
    "ReproSession": (
        "desktop.services.ReproService.repro_OpenPackage -> "
        "Result[{package_path, meta, manifest}] ; "
        "repro_RestoreContext/repro_LoadAsDataset own the rest"
    ),
    "WaitEdge": "parser.models.ResourceGraph.wait_edges[*] (dict edge payload)",
}
LEGACY_MODEL_DEPRECATION_NOTES = {
    "Dataset": (
        "live legacy compatibility model kept only in spec.models; "
        "top-level spec re-export removed; do not use for new runtime code"
    ),
    "ExportJob": (
        "retired from spec.models/spec package in phase E; "
        "replaced by interface-level export job payload"
    ),
    "HoldEdge": (
        "retired from spec.models in phase E; "
        "edge data remains internal to ResourceGraph.hold_edges"
    ),
    "MetricSession": (
        "retired from spec.models/spec package in phase E; "
        "runtime owner is metric.core.MetricSession"
    ),
    "PackageResult": (
        "retired from spec.models/spec package in phase E; "
        "replaced by interface-level write-package payload"
    ),
    "ReproSession": (
        "retired from spec.models/spec package in phase E; "
        "replaced by repro_OpenPackage payload plus dedicated restore/load interfaces"
    ),
    "WaitEdge": (
        "retired from spec.models in phase E; "
        "edge data remains internal to ResourceGraph.wait_edges"
    ),
}


def has_canonical_peer(name: str) -> bool:
    return name in CANONICAL_PEER_MODEL_NAMES


def canonical_model(name: str) -> type[Any] | None:
    if not has_canonical_peer(name):
        return None
    return getattr(canonical_models, name, None)


def is_legacy_only_model(name: str) -> bool:
    return name in LEGACY_ONLY_MODEL_NAMES


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
    time_window: tuple[int, int]
    object_scope: dict[str, Any]
    conclusion: str
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    related_alerts: list[str] = field(default_factory=list)
    confidence: str = "high"


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
class TaskStateViewModel:
    time_window: tuple[float, float]
    lane_order: list[str]
    rows: list[TaskStateViewRow]
    state_legend: dict[str, str]
    summary: dict[str, Any]
    cursor_hint: str | None = None
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


# Deprecated compatibility model: kept only in spec.models until Dataset ownership is finalized.
@dataclass
class Dataset:
    dataset_id: str
    source: str
    dictionary: dict[str, Any]
    header: GlobalHeader
    events: list[UnifiedEvent] = field(default_factory=list)
    untrusted_windows: list[UntrustedWindow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    capability_flags: dict[str, bool] = field(default_factory=dict)
    rebuild_bundle: RebuildBundle | None = None
    index_bundle: IndexBundle | None = None

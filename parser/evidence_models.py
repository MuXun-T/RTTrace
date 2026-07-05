from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


DEFAULT_HALT_PRIORITY = (
    "SIDECAR_MISMATCH",
    "CYCLE_INFLATION",
    "CORRUPT_SEGMENT",
    "DEPTH_LIMIT",
    "EVENT_LIMIT",
    "BYTE_LIMIT",
    "RHO_LIMIT",
    "TRACE_IO_GUARD",
)
DEFAULT_RULE_FAMILY = (
    "ref_ref",
    "ref_alert",
    "ref_diagnosis",
    "ref_anchor",
    "ref_object",
)
DEFAULT_FRONTIER_REF_LIMIT = 10000
DEFAULT_EMBODIMENT_MODE = "mode_a"


def _as_int(value: Any, *, name: str, default: int, minimum: int = 0) -> int:
    try:
        normalized = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if normalized < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return normalized


def _as_float(value: Any, *, name: str, default: float, minimum: float = 0.0) -> float:
    try:
        normalized = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if normalized < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return normalized


def _normalize_rule_family(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_RULE_FAMILY
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    normalized: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized or DEFAULT_RULE_FAMILY)


def _normalize_embodiment_mode(value: Any) -> str:
    text = str(value or DEFAULT_EMBODIMENT_MODE).strip().lower().replace("-", "_")
    if text in {"mode_b", "standalone", "standalone_two_pass"}:
        return "mode_b"
    return "mode_a"


def _normalize_seed_materialization(value: Any) -> str:
    text = str(value or "required").strip().lower().replace("-", "_")
    if text in {"best_effort", "best"}:
        return "best_effort"
    if text in {"required", "event"}:
        return "required"
    return "required"


@dataclass(frozen=True)
class BudgetVector:
    D_max: int
    C_events: int
    S_bytes: int
    rho_max: float

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None = None) -> "BudgetVector":
        source = dict(payload or {})
        return cls(
            D_max=_as_int(source.get("D_max"), name="budget_vector.D_max", default=4, minimum=0),
            C_events=_as_int(source.get("C_events"), name="budget_vector.C_events", default=512, minimum=0),
            S_bytes=_as_int(source.get("S_bytes"), name="budget_vector.S_bytes", default=1 << 20, minimum=0),
            rho_max=_as_float(source.get("rho_max"), name="budget_vector.rho_max", default=4.0, minimum=0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "D_max": int(self.D_max),
            "C_events": int(self.C_events),
            "S_bytes": int(self.S_bytes),
            "rho_max": float(self.rho_max),
        }


def evd_NormalizeRuleFamily(rule_family: Any) -> tuple[str, ...]:
    return _normalize_rule_family(rule_family)


def evd_NormalizeBudgetVector(value: Any) -> "BudgetVector":
    if isinstance(value, BudgetVector):
        return value
    if value is None or isinstance(value, dict):
        return BudgetVector.from_payload(value if isinstance(value, dict) else None)
    payload = {
        "D_max": getattr(value, "D_max", None),
        "C_events": getattr(value, "C_events", None),
        "S_bytes": getattr(value, "S_bytes", None),
        "rho_max": getattr(value, "rho_max", None),
    }
    return BudgetVector.from_payload(payload)


def _normalize_snapshot_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_snapshot_scalar(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, SeedSpec):
        return _normalize_snapshot_scalar(value.to_dict())
    if isinstance(value, BudgetVector):
        return _normalize_snapshot_scalar(value.to_dict())
    if isinstance(value, (list, tuple)):
        return [_normalize_snapshot_scalar(item) for item in value]
    return value


def evd_NormalizeSeedSpecForSnapshot(seed_spec: Any) -> dict[str, Any]:
    return _normalize_snapshot_scalar(SeedSpec.from_payload(seed_spec).to_dict())


def evd_SnapshotDerivationInput(
    *,
    dataset_id: str,
    embodiment_mode: str,
    trace_checksum: str,
    dictionary_checksum: str,
    request: "EvidenceExportRequest",
    analysis_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_context = dict(analysis_context or {})
    snapshot_context = {
        "time_window": normalized_context.get("time_window"),
        "filter": normalized_context.get("filter"),
        "selection": normalized_context.get("selection"),
        "evidence_anchor": normalized_context.get("evidence_anchor"),
        "compare_scope": normalized_context.get("compare_scope"),
        "dataset_role": normalized_context.get("dataset_role"),
    }
    return {
        "dataset_id": str(dataset_id),
        "embodiment_mode": _normalize_embodiment_mode(embodiment_mode),
        "trace_checksum": str(trace_checksum),
        "dictionary_checksum": str(dictionary_checksum),
        "seed_spec": evd_NormalizeSeedSpecForSnapshot(request.seed_spec),
        "rule_family": list(evd_NormalizeRuleFamily(request.rule_family)),
        "budget_vector": evd_NormalizeBudgetVector(request.budget_vector).to_dict(),
        "time_window": list(request.time_window) if request.time_window is not None else None,
        "filter": _normalize_snapshot_scalar(dict(request.filter or {})),
        "analysis_context": _normalize_snapshot_scalar(snapshot_context),
    }


def evd_DeriveStableSnapshotId(
    *,
    dataset_id: str,
    embodiment_mode: str,
    trace_checksum: str,
    dictionary_checksum: str,
    request: "EvidenceExportRequest",
    analysis_context: dict[str, Any] | None = None,
) -> str:
    normalized = evd_SnapshotDerivationInput(
        dataset_id=dataset_id,
        embodiment_mode=embodiment_mode,
        trace_checksum=trace_checksum,
        dictionary_checksum=dictionary_checksum,
        request=request,
        analysis_context=analysis_context,
    )
    payload = json.dumps(_normalize_snapshot_scalar(normalized), ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    mode = _normalize_embodiment_mode(embodiment_mode)
    return f"snapshot:{mode}:{digest}"


@dataclass(frozen=True)
class ClosurePolicy:
    halt_priority: tuple[str, ...] = DEFAULT_HALT_PRIORITY
    seed_materialization: str = "required"
    allow_bounded: bool = True
    allow_degraded: bool = True
    frontier_ref_limit: int = DEFAULT_FRONTIER_REF_LIMIT

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None = None) -> "ClosurePolicy":
        source = dict(payload or {})
        raw_halt_priority = source.get("halt_priority")
        halt_priority: list[str] = []
        if raw_halt_priority is None:
            halt_priority = list(DEFAULT_HALT_PRIORITY)
        else:
            for item in list(raw_halt_priority):
                text = str(item or "").strip()
                if text and text not in halt_priority:
                    halt_priority.append(text)
            if not halt_priority:
                halt_priority = list(DEFAULT_HALT_PRIORITY)
        seed_materialization = _normalize_seed_materialization(source.get("seed_materialization"))
        return cls(
            halt_priority=tuple(halt_priority),
            seed_materialization=seed_materialization,
            allow_bounded=bool(source.get("allow_bounded", True)),
            allow_degraded=bool(source.get("allow_degraded", True)),
            frontier_ref_limit=_as_int(
                source.get("frontier_ref_limit"),
                name="closure_policy.frontier_ref_limit",
                default=DEFAULT_FRONTIER_REF_LIMIT,
                minimum=0,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "halt_priority": list(self.halt_priority),
            "seed_materialization": self.seed_materialization,
            "allow_bounded": bool(self.allow_bounded),
            "allow_degraded": bool(self.allow_degraded),
            "frontier_ref_limit": int(self.frontier_ref_limit),
        }


@dataclass(frozen=True)
class SeedSpec:
    source_kind: str
    source_payload: Any = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> "SeedSpec":
        if isinstance(payload, SeedSpec):
            return payload
        if isinstance(payload, dict):
            source = dict(payload)
            source_kind = str(source.get("source_kind") or "analysis_context").strip() or "analysis_context"
            source_payload = source.get("source_payload")
            if source_payload is None:
                source_payload = {}
            return cls(source_kind=source_kind, source_payload=source_payload)
        return cls(
            source_kind=str(payload or "analysis_context").strip() or "analysis_context",
            source_payload={},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": str(self.source_kind),
            "source_payload": self.source_payload,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class SeedResolution:
    seed_refs: list[str] = field(default_factory=list)
    scope_events: list[str] = field(default_factory=list)
    missing_required_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Any) -> "SeedResolution":
        if isinstance(payload, SeedResolution):
            return payload
        source = dict(payload or {})
        return cls(
            seed_refs=[str(item) for item in list(source.get("seed_refs") or [])],
            scope_events=[str(item) for item in list(source.get("scope_events") or [])],
            missing_required_refs=[str(item) for item in list(source.get("missing_required_refs") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_refs": list(self.seed_refs),
            "scope_events": list(self.scope_events),
            "missing_required_refs": list(self.missing_required_refs),
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class EvidenceExportRequest:
    dataset_id: str | None
    seed_spec: SeedSpec
    rule_family: tuple[str, ...]
    budget_vector: BudgetVector
    closure_policy: ClosurePolicy
    embodiment_mode: str = DEFAULT_EMBODIMENT_MODE
    sidecar_source: str | None = None
    sidecar_manifest_source: str | None = None
    time_window: tuple[float, float] | None = None
    filter: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any] | None,
        *,
        default_dataset_id: str | None = None,
        default_time_window: tuple[float, float] | None = None,
        default_filter: dict[str, Any] | None = None,
    ) -> "EvidenceExportRequest":
        source = dict(payload or {})
        raw_seed_spec = source.get("seed_spec")
        if raw_seed_spec is None:
            raw_seed_spec = {
                "source_kind": str(source.get("source_kind") or "analysis_context"),
                "source_payload": source.get("source_payload") or {},
            }
        seed_spec = SeedSpec.from_payload(raw_seed_spec)
        raw_time_window = source.get("time_window", default_time_window)
        time_window: tuple[float, float] | None = None
        if isinstance(raw_time_window, (list, tuple)) and len(raw_time_window) >= 2:
            time_window = (float(raw_time_window[0]), float(raw_time_window[1]))
        return cls(
            dataset_id=source.get("dataset_id", default_dataset_id),
            seed_spec=seed_spec,
            rule_family=evd_NormalizeRuleFamily(source.get("rule_family")),
            budget_vector=evd_NormalizeBudgetVector(source.get("budget_vector")),
            closure_policy=ClosurePolicy.from_payload(source.get("closure_policy")),
            embodiment_mode=_normalize_embodiment_mode(source.get("embodiment_mode")),
            sidecar_source=(
                str(source.get("sidecar_source")).strip()
                if source.get("sidecar_source") is not None and str(source.get("sidecar_source")).strip()
                else None
            ),
            sidecar_manifest_source=(
                str(source.get("sidecar_manifest_source")).strip()
                if source.get("sidecar_manifest_source") is not None and str(source.get("sidecar_manifest_source")).strip()
                else None
            ),
            time_window=time_window,
            filter=dict(source.get("filter") or default_filter or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "seed_spec": self.seed_spec.to_dict(),
            "rule_family": list(self.rule_family),
            "budget_vector": self.budget_vector.to_dict(),
            "closure_policy": self.closure_policy.to_dict(),
            "embodiment_mode": self.embodiment_mode,
            "sidecar_source": self.sidecar_source,
            "sidecar_manifest_source": self.sidecar_manifest_source,
            "time_window": list(self.time_window) if self.time_window is not None else None,
            "filter": dict(self.filter),
        }


@dataclass(frozen=True)
class WindowSpan:
    span_id: str
    t_begin: int
    t_end: int
    segment_seq: int | None
    core_id: int | None
    seq_begin: int | None
    seq_end: int | None
    source_edge_count: int
    target_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WindowSpan":
        source = dict(payload or {})
        return cls(
            span_id=str(source.get("span_id") or source.get("window_id") or "span:0"),
            t_begin=int(source.get("t_begin", source.get("time_hint_begin_ns", 0))),
            t_end=int(source.get("t_end", source.get("time_hint_end_ns", source.get("time_hint_begin_ns", 0)))),
            segment_seq=(
                None
                if source.get("segment_seq") is None
                else int(source.get("segment_seq"))
            ),
            core_id=None if source.get("core_id", source.get("core_hint")) is None else int(source.get("core_id", source.get("core_hint"))),
            seq_begin=None if source.get("seq_begin", source.get("seq_hint_begin")) is None else int(source.get("seq_begin", source.get("seq_hint_begin"))),
            seq_end=None if source.get("seq_end", source.get("seq_hint_end")) is None else int(source.get("seq_end", source.get("seq_hint_end"))),
            source_edge_count=int(source.get("source_edge_count", 0)),
            target_refs=sorted({str(item) for item in list(source.get("target_refs") or []) if str(item).strip()}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "t_begin": int(self.t_begin),
            "t_end": int(self.t_end),
            "segment_seq": self.segment_seq,
            "core_id": self.core_id,
            "seq_begin": self.seq_begin,
            "seq_end": self.seq_end,
            "source_edge_count": int(self.source_edge_count),
            "target_refs": list(self.target_refs),
        }


@dataclass(frozen=True)
class WindowPlan:
    round_id: int
    spans: list[WindowSpan] = field(default_factory=list)
    planned_seek_count: int = 0
    planned_span_total: int = 0
    planned_ref_count: int = 0
    unplanned_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Any) -> "WindowPlan":
        if isinstance(payload, WindowPlan):
            return payload
        source = dict(payload or {})
        spans = [WindowSpan.from_payload(dict(row or {})) for row in list(source.get("spans") or [])]
        return cls(
            round_id=int(source.get("round_id", 0)),
            spans=spans,
            planned_seek_count=int(source.get("planned_seek_count", len(spans))),
            planned_span_total=int(
                source.get(
                    "planned_span_total",
                    sum(max(0, int(span.t_end) - int(span.t_begin)) for span in spans),
                )
            ),
            planned_ref_count=int(
                source.get(
                    "planned_ref_count",
                    len({ref_key for span in spans for ref_key in list(span.target_refs)}),
                )
            ),
            unplanned_refs=sorted({str(ref_key) for ref_key in list(source.get("unplanned_refs") or []) if str(ref_key).strip()}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": int(self.round_id),
            "spans": [span.to_dict() for span in list(self.spans)],
            "planned_seek_count": int(self.planned_seek_count),
            "planned_span_total": int(self.planned_span_total),
            "planned_ref_count": int(self.planned_ref_count),
            "unplanned_refs": list(self.unplanned_refs),
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class ReadWindowResult:
    round_id: int
    matched_events: list[Any] = field(default_factory=list)
    matched_refs: list[str] = field(default_factory=list)
    missed_refs: list[str] = field(default_factory=list)
    bytes_read: int = 0
    scan_count: int = 0
    seek_count: int = 0
    span_total: int = 0
    window_span_total: int = 0
    window_count: int = 0
    corrupt_segments: list[Any] = field(default_factory=list)
    io_guard_triggered: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> "ReadWindowResult":
        if isinstance(payload, ReadWindowResult):
            return payload
        source = dict(payload or {})
        return cls(
            round_id=int(source.get("round_id", 0)),
            matched_events=list(source.get("matched_events") or []),
            matched_refs=[str(item) for item in list(source.get("matched_refs") or [])],
            missed_refs=[str(item) for item in list(source.get("missed_refs") or [])],
            bytes_read=int(source.get("bytes_read", 0)),
            scan_count=int(source.get("scan_count", 0)),
            seek_count=int(source.get("seek_count", 0)),
            span_total=int(source.get("span_total", 0)),
            window_span_total=int(source.get("window_span_total", source.get("span_total", 0))),
            window_count=int(source.get("window_count", 0)),
            corrupt_segments=list(source.get("corrupt_segments") or []),
            io_guard_triggered=bool(source.get("io_guard_triggered", False)),
            telemetry=dict(source.get("telemetry") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": int(self.round_id),
            "matched_events": list(self.matched_events),
            "matched_refs": list(self.matched_refs),
            "missed_refs": list(self.missed_refs),
            "bytes_read": int(self.bytes_read),
            "scan_count": int(self.scan_count),
            "seek_count": int(self.seek_count),
            "span_total": int(self.span_total),
            "window_span_total": int(self.window_span_total),
            "window_count": int(self.window_count),
            "corrupt_segments": list(self.corrupt_segments),
            "io_guard_triggered": bool(self.io_guard_triggered),
            "telemetry": dict(self.telemetry),
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class DependencySidecarEdge:
    snapshot_id: str
    trace_checksum: str
    src_ref: str
    dst_ref: str
    src_kind: str
    dst_kind: str
    relation_kind: str
    rule_family: str
    provenance: str
    priority: int
    time_hint_begin_ns: int
    time_hint_end_ns: int
    core_hint: int | None
    seq_hint_begin: int | None
    seq_hint_end: int | None
    segment_hint: str | None
    cycle_guard_token: str
    estimate_events: int
    estimate_bytes: int
    edge_hash: str


@dataclass(frozen=True)
class FrontierRefRow:
    ref_key: str
    ref_kind: str
    priority: int
    frontier_origin_rule: str
    estimate_events: int
    estimate_bytes: int
    time_hint_begin_ns: int
    time_hint_end_ns: int
    core_hint: int | None
    seq_hint_begin: int | None
    seq_hint_end: int | None
    segment_hint: str | None


@dataclass(frozen=True)
class RoundProjection:
    round_id: int
    frontier_in_count: int
    candidate_edge_count: int
    delta_ref_count: int
    estimate_events: int
    estimate_bytes: int
    expansion_ratio: float
    would_halt: bool
    halt_reason: str | None = None
    candidate_frontier_refs: list[FrontierRefRow] = field(default_factory=list)


@dataclass
class EvidenceClosureState:
    seed_refs: list[str]
    scope_events: list[str]
    closed_refs: list[str]
    frontier_refs: list[str]
    emitted_event_refs: list[str] = field(default_factory=list)
    emitted_events_count: int = 0
    emitted_bytes: int = 0
    missing_required_refs: list[str] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    halt_reason: str = "FRONTIER_EMPTY"
    closure_mode: str = "exact"
    sidecar_lookup_count: int = 0
    workset_edges: list["DependencySidecarEdge"] = field(default_factory=list)


@dataclass(frozen=True)
class FrontierSnapshot:
    round_id: int
    closure_mode: str
    frontier_count: int
    consumed_depth: int
    consumed_events: int
    consumed_bytes: int
    projected_next_events: int
    projected_next_bytes: int
    expansion_ratio: float
    halt_reason: str
    frontier_refs_path: str | None
    truncated_frontier_count: int
    freeze_round_id: int | None = None
    pre_read_reject: bool = False
    pre_read_reject_round_id: int | None = None


@dataclass(frozen=True)
class ProofDigest:
    snapshot_id: str
    closure_mode: str
    complete_wrt_rule_family: bool
    rule_family: list[str] | str
    budget_vector: dict[str, Any]
    closure_depth_reached: int
    seed_ref_count: int
    closed_ref_count: int
    missing_required_refs: int
    truncated_frontier_count: int
    frontier_halt_reason: str
    events_emitted: int
    bytes_emitted: int
    scan_count: int
    seek_count: int
    window_span_total: int
    sidecar_lookup_count: int
    proof_hash: str
    round_count: int = 0
    window_hit_rate: float = 1.0
    peak_rss_mb: float | None = None
    sidecar_bytes: int | None = None
    sidecar_selector_mode: str | None = None
    sidecar_selector_calls: int = 0
    sidecar_bytes_scanned: int = 0
    sidecar_index_build_seconds: float | None = None


@dataclass(frozen=True)
class BlockerArtifact:
    snapshot_id: str
    round_id: int
    closure_mode: str
    halt_reason: str
    candidate_edge_sample: list[dict[str, Any]]
    window_plan_summary: dict[str, Any]
    partial_write_status: dict[str, Any]
    emitted_metrics: dict[str, Any]
    exception_code: str | None = None
    exception_message: str | None = None


@dataclass
class EvidenceClosureOutcome:
    closure_mode: str
    halt_reason: str
    round_id: int
    seed_refs: list[str]
    closed_refs: list[str]
    selected_refs: list[str]
    missing_required_refs: list[str] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    frontier_rows: list[FrontierRefRow] = field(default_factory=list)
    frontier_count: int = 0
    truncated_frontier_count: int = 0
    projected_next_events: int = 0
    projected_next_bytes: int = 0
    expansion_ratio: float = 0.0
    sidecar_lookup_count: int = 0
    scan_count: int = 0
    seek_count: int = 0
    window_span_total: int = 0
    candidate_edge_sample: list[dict[str, Any]] = field(default_factory=list)
    consumed_events: int = 0
    consumed_bytes: int = 0
    degraded_code: str | None = None
    degraded_message: str | None = None
    workset_edges: list[DependencySidecarEdge] = field(default_factory=list)


def evd_StableEdgeSortKey(edge: DependencySidecarEdge | dict[str, Any]) -> tuple[int, int, str, str]:
    if isinstance(edge, DependencySidecarEdge):
        row = edge
        priority = int(row.priority)
        time_hint_begin_ns = int(row.time_hint_begin_ns)
        dst_ref = str(row.dst_ref)
        edge_hash = str(row.edge_hash)
    else:
        payload = dict(edge or {})
        priority = int(payload.get("priority", 0))
        time_hint_begin_ns = int(payload.get("time_hint_begin_ns", 0))
        dst_ref = str(payload.get("dst_ref", ""))
        edge_hash = str(payload.get("edge_hash", ""))
    return (-priority, time_hint_begin_ns, dst_ref, edge_hash)


def evd_StableEventSortKey(event: Any) -> tuple[float, int, int]:
    if isinstance(event, dict):
        return (
            float(event.get("timestamp_aligned", 0.0)),
            int(event.get("core_id", 0)),
            int(event.get("seq", 0)),
        )
    return (
        float(getattr(event, "timestamp_aligned", 0.0)),
        int(getattr(event, "core_id", 0)),
        int(getattr(event, "seq", 0)),
    )


def evd_ProofHashInput(digest: ProofDigest | dict[str, Any]) -> dict[str, Any]:
    payload = digest if isinstance(digest, dict) else {
        "snapshot_id": digest.snapshot_id,
        "closure_mode": digest.closure_mode,
        "complete_wrt_rule_family": digest.complete_wrt_rule_family,
        "rule_family": digest.rule_family,
        "budget_vector": digest.budget_vector,
        "closure_depth_reached": digest.closure_depth_reached,
        "seed_ref_count": digest.seed_ref_count,
        "closed_ref_count": digest.closed_ref_count,
        "missing_required_refs": digest.missing_required_refs,
        "truncated_frontier_count": digest.truncated_frontier_count,
        "frontier_halt_reason": digest.frontier_halt_reason,
        "events_emitted": digest.events_emitted,
        "bytes_emitted": digest.bytes_emitted,
    }
    return {
        "snapshot_id": str(payload.get("snapshot_id", "")),
        "closure_mode": str(payload.get("closure_mode", "")),
        "complete_wrt_rule_family": bool(payload.get("complete_wrt_rule_family", False)),
        "rule_family": list(evd_NormalizeRuleFamily(payload.get("rule_family"))),
        "budget_vector": evd_NormalizeBudgetVector(payload.get("budget_vector")).to_dict(),
        "closure_depth_reached": int(payload.get("closure_depth_reached", 0)),
        "seed_ref_count": int(payload.get("seed_ref_count", 0)),
        "closed_ref_count": int(payload.get("closed_ref_count", 0)),
        "missing_required_refs": int(payload.get("missing_required_refs", 0)),
        "truncated_frontier_count": int(payload.get("truncated_frontier_count", 0)),
        "frontier_halt_reason": str(payload.get("frontier_halt_reason", "")),
        "events_emitted": int(payload.get("events_emitted", 0)),
        "bytes_emitted": int(payload.get("bytes_emitted", 0)),
    }


def evd_RecomputeProofHash(digest: ProofDigest | dict[str, Any]) -> str:
    normalized = evd_ProofHashInput(digest)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "DEFAULT_EMBODIMENT_MODE",
    "DEFAULT_FRONTIER_REF_LIMIT",
    "DEFAULT_HALT_PRIORITY",
    "DEFAULT_RULE_FAMILY",
    "BlockerArtifact",
    "BudgetVector",
    "ClosurePolicy",
    "DependencySidecarEdge",
    "EvidenceClosureState",
    "EvidenceClosureOutcome",
    "EvidenceExportRequest",
    "FrontierRefRow",
    "FrontierSnapshot",
    "ProofDigest",
    "ReadWindowResult",
    "RoundProjection",
    "SeedResolution",
    "SeedSpec",
    "WindowPlan",
    "WindowSpan",
    "evd_DeriveStableSnapshotId",
    "evd_NormalizeBudgetVector",
    "evd_NormalizeRuleFamily",
    "evd_NormalizeSeedSpecForSnapshot",
    "evd_ProofHashInput",
    "evd_RecomputeProofHash",
    "evd_SnapshotDerivationInput",
    "evd_StableEdgeSortKey",
    "evd_StableEventSortKey",
]

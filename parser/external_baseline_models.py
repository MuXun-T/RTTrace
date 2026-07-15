"""Closed, fail-closed P7.7 external-baseline comparison records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Mapping


VERSION = "p7.7-external-baseline-v1"
_CATEGORIES = (
    "rtos_trace_viewer",
    "trace_compass",
    "perfetto",
    "rtos_vendor_tool",
    "custom_full_trace",
    "no_evidence_package",
)
_CAPABILITIES = ("supported", "unsupported", "not_applicable", "not_evaluated")
_COMPARISONS = ("quantitative", "capability_only", "not_comparable", "not_applicable", "not_evaluated")
_GATES = (
    "same_raw_trace_sha256",
    "same_case_workload",
    "same_input_scope",
    "matched_truth_boundary",
    "same_or_approved_equivalent_environment",
    "same_metric_unit_statistics",
    "same_failure_exclusion_policy",
)
_LEAK = re.compile(r"(?:^|[_ .:-])(tmp|temp|pid|hostname|timestamp|username)(?:$|[_ .:-])|\d{4}-\d{2}-\d{2}", re.I)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii() or any(char in value for char in "/\\\x00\r\n") or _LEAK.search(value):
        raise ValueError(f"{name} must be a stable non-leaking ASCII string")
    return value


def _number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _median(values: list[int | float]) -> int | float:
    """Return the center value, or the arithmetic mean of lower/upper centers."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


@dataclass(frozen=True)
class EvidenceValue:
    value: str | None
    reason: str | None

    def __post_init__(self) -> None:
        if self.value is None:
            if not isinstance(self.reason, str):
                raise ValueError("unknown evidence requires a reason")
            _text(self.reason, "evidence.reason")
        else:
            _text(self.value, "evidence.value")
            if self.reason is not None:
                raise ValueError("known evidence cannot have a reason")

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "reason": self.reason}

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceValue":
        if not isinstance(value, Mapping) or set(value) != {"value", "reason"}:
            raise ValueError("evidence fields are invalid")
        return cls(value["value"], value["reason"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class Capability:
    name: str
    status: str
    reason: str | None

    def __post_init__(self) -> None:
        _text(self.name, "capability.name")
        if self.status not in _CAPABILITIES:
            raise ValueError("capability status is invalid")
        if self.status == "supported":
            if self.reason is not None:
                raise ValueError("supported capability cannot have a reason")
        elif not isinstance(self.reason, str):
            raise ValueError("non-supported capability requires a reason")
        if self.reason is not None:
            _text(self.reason, "capability.reason")

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status, "reason": self.reason}

    @classmethod
    def from_dict(cls, value: object) -> "Capability":
        if not isinstance(value, Mapping) or set(value) != {"name", "status", "reason"}:
            raise ValueError("capability fields are invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class FairnessGate:
    name: str
    status: str
    reason: str | None

    def __post_init__(self) -> None:
        if self.name not in _GATES or self.status not in {"pass", "nonpass"}:
            raise ValueError("fairness gate is invalid")
        if self.status == "pass" and self.reason is not None:
            raise ValueError("passing gate cannot have a reason")
        if self.status == "nonpass" and not isinstance(self.reason, str):
            raise ValueError("non-passing gate requires a reason")
        if self.reason is not None:
            _text(self.reason, "gate.reason")

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status, "reason": self.reason}

    @classmethod
    def from_dict(cls, value: object) -> "FairnessGate":
        if not isinstance(value, Mapping) or set(value) != {"name", "status", "reason"}:
            raise ValueError("gate fields are invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class MetricSample:
    name: str
    unit: str
    value: int | float

    def __post_init__(self) -> None:
        _text(self.name, "metric.name")
        _text(self.unit, "metric.unit")
        _number(self.value, "metric.value")

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "unit": self.unit, "value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> "MetricSample":
        if not isinstance(value, Mapping) or set(value) != {"name", "unit", "value"}:
            raise ValueError("metric fields are invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class RawResult:
    sample_id: str
    status: str
    failure_reason: str | None
    metrics: tuple[MetricSample, ...]

    def __post_init__(self) -> None:
        _text(self.sample_id, "sample_id")
        if self.status not in {"success", "failure"}:
            raise ValueError("raw result status is invalid")
        if self.status == "success" and self.failure_reason is not None:
            raise ValueError("successful raw result cannot have failure reason")
        if self.status == "failure" and not isinstance(self.failure_reason, str):
            raise ValueError("failed raw result requires failure reason")
        if self.failure_reason is not None:
            _text(self.failure_reason, "failure_reason")
        names = [metric.name for metric in self.metrics]
        if names != sorted(set(names)):
            raise ValueError("raw metrics must be uniquely ordered")

    def to_dict(self) -> dict[str, object]:
        return {"sample_id": self.sample_id, "status": self.status, "failure_reason": self.failure_reason, "metrics": [metric.to_dict() for metric in self.metrics]}

    @classmethod
    def from_dict(cls, value: object) -> "RawResult":
        if not isinstance(value, Mapping) or set(value) != {"sample_id", "status", "failure_reason", "metrics"} or not isinstance(value["metrics"], list):
            raise ValueError("raw result fields are invalid")
        data = dict(value)
        data["metrics"] = tuple(MetricSample.from_dict(row) for row in value["metrics"])
        return cls(**data)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Summary:
    metric_name: str
    unit: str
    sample_count: int
    median: int | float
    mad: int | float
    minimum: int | float
    maximum: int | float
    failure_count: int

    def __post_init__(self) -> None:
        _text(self.metric_name, "summary.metric_name")
        _text(self.unit, "summary.unit")
        _integer(self.sample_count, "summary.sample_count")
        if self.sample_count < 1:
            raise ValueError("summary requires samples")
        for value, name in ((self.median, "median"), (self.mad, "mad"), (self.minimum, "minimum"), (self.maximum, "maximum")):
            _number(value, f"summary.{name}")
        if self.minimum > self.median or self.median > self.maximum:
            raise ValueError("summary extrema are invalid")
        _integer(self.failure_count, "summary.failure_count")

    def to_dict(self) -> dict[str, object]:
        return {"metric_name": self.metric_name, "unit": self.unit, "sample_count": self.sample_count, "median": self.median, "mad": self.mad, "minimum": self.minimum, "maximum": self.maximum, "failure_count": self.failure_count}

    @classmethod
    def from_dict(cls, value: object) -> "Summary":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("summary fields are invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class BaselineInventory:
    baseline_id: str
    category: str
    candidate_status: str
    source: EvidenceValue
    version: EvidenceValue
    license: EvidenceValue
    availability: EvidenceValue
    capabilities: tuple[Capability, ...]

    def __post_init__(self) -> None:
        _text(self.baseline_id, "baseline_id")
        if self.category not in _CATEGORIES:
            raise ValueError("baseline category is invalid")
        if self.candidate_status != "not_evaluated":
            raise ValueError("candidate status must remain not_evaluated")
        if not all(isinstance(value, EvidenceValue) for value in (self.source, self.version, self.license, self.availability)):
            raise ValueError("baseline evidence is invalid")
        names = [capability.name for capability in self.capabilities]
        if names != sorted(set(names)):
            raise ValueError("capabilities must be uniquely ordered")

    def to_dict(self) -> dict[str, object]:
        return {"baseline_id": self.baseline_id, "category": self.category, "candidate_status": self.candidate_status, "source": self.source.to_dict(), "version": self.version.to_dict(), "license": self.license.to_dict(), "availability": self.availability.to_dict(), "capabilities": [value.to_dict() for value in self.capabilities]}

    @classmethod
    def from_dict(cls, value: object) -> "BaselineInventory":
        required = {"baseline_id", "category", "candidate_status", "source", "version", "license", "availability", "capabilities"}
        if not isinstance(value, Mapping) or set(value) != required or not isinstance(value["capabilities"], list):
            raise ValueError("inventory fields are invalid")
        data = dict(value)
        for name in ("source", "version", "license", "availability"):
            data[name] = EvidenceValue.from_dict(value[name])
        data["capabilities"] = tuple(Capability.from_dict(row) for row in value["capabilities"])
        return cls(**data)  # type: ignore[arg-type]


@dataclass(frozen=True)
class BaselineComparisonReport:
    inventory: tuple[BaselineInventory, ...]
    gates: tuple[FairnessGate, ...]
    comparison_status: str
    raw_results: tuple[RawResult, ...]
    summaries: tuple[Summary, ...]

    def __post_init__(self) -> None:
        ids = [inventory.baseline_id for inventory in self.inventory]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("inventory must be uniquely ordered")
        if tuple(gate.name for gate in self.gates) != _GATES:
            raise ValueError("fairness gates must be complete and ordered")
        if self.comparison_status not in _COMPARISONS:
            raise ValueError("comparison status is invalid")
        raw_ids = [result.sample_id for result in self.raw_results]
        if raw_ids != sorted(raw_ids) or len(raw_ids) != len(set(raw_ids)):
            raise ValueError("raw results must be uniquely ordered")
        summary_names = [summary.metric_name for summary in self.summaries]
        if summary_names != sorted(set(summary_names)):
            raise ValueError("summaries must be uniquely ordered")
        all_pass = all(gate.status == "pass" for gate in self.gates)
        if all_pass != (self.comparison_status == "quantitative"):
            raise ValueError("quantitative status requires exactly seven passing gates")
        if self.comparison_status != "quantitative" and self.summaries:
            raise ValueError("non-quantitative comparison cannot have summaries")
        if self.comparison_status == "quantitative":
            if not self.raw_results or not self.summaries:
                raise ValueError("quantitative comparison requires raw results and summaries")
            failures = sum(result.status == "failure" for result in self.raw_results)
            for summary in self.summaries:
                values = [metric.value for result in self.raw_results if result.status == "success" for metric in result.metrics if metric.name == summary.metric_name and metric.unit == summary.unit]
                if not values:
                    raise ValueError("summary has no retained raw metric samples")
                median = _median(values)
                mad = _median([abs(value - median) for value in values])
                if (len(values), median, mad, min(values), max(values), failures) != (summary.sample_count, summary.median, summary.mad, summary.minimum, summary.maximum, summary.failure_count):
                    raise ValueError("summary does not exactly match retained raw statistics")

    def to_dict(self) -> dict[str, object]:
        return {"external_baseline_comparison_version": VERSION, "inventory": [row.to_dict() for row in self.inventory], "gates": [row.to_dict() for row in self.gates], "comparison_status": self.comparison_status, "raw_results": [row.to_dict() for row in self.raw_results], "summaries": [row.to_dict() for row in self.summaries]}

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.to_dict())

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> "BaselineComparisonReport":
        required = {"external_baseline_comparison_version", "inventory", "gates", "comparison_status", "raw_results", "summaries"}
        if not isinstance(value, Mapping) or set(value) != required or value["external_baseline_comparison_version"] != VERSION:
            raise ValueError("report fields are invalid")
        if not all(isinstance(value[name], list) for name in ("inventory", "gates", "raw_results", "summaries")):
            raise ValueError("report containers are invalid")
        return cls(
            tuple(BaselineInventory.from_dict(row) for row in value["inventory"]),
            tuple(FairnessGate.from_dict(row) for row in value["gates"]),
            value["comparison_status"],  # type: ignore[arg-type]
            tuple(RawResult.from_dict(row) for row in value["raw_results"]),
            tuple(Summary.from_dict(row) for row in value["summaries"]),
        )

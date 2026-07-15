"""Closed, deterministic P7.6 benchmark evidence records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping


VERSION = "p7.6-benchmark-v1"
CLAIM_STRENGTH = "report_only"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CASES = ("freertos_btf_1core", "freertos_vcd_1core", "freertos_btf_4cores", "freertos_btf_50k")
_MODES = ("process_cold", "same_process_warm")
_METRIC_STATUS = ("measured", "unsupported", "unavailable", "not_evaluated")


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256_identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in "\\/\x00"):
        raise ValueError(f"{name} must be a stable identifier")
    return value


def _nonnegative(value: object, name: str, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be non-negative")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


@dataclass(frozen=True)
class Metric:
    status: str
    value: int | float | None
    unit: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _METRIC_STATUS or not isinstance(self.unit, str) or not self.unit:
            raise ValueError("metric contract is invalid")
        if self.status == "measured":
            if self.value is None:
                raise ValueError("measured metric requires a value")
            _nonnegative(self.value, "metric.value")
            if self.reason is not None:
                raise ValueError("measured metric cannot have a reason")
        elif self.value is not None or not isinstance(self.reason, str) or not self.reason:
            raise ValueError("unavailable metric requires null value and reason")

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "value": self.value, "unit": self.unit, "reason": self.reason}

    @classmethod
    def from_dict(cls, value: object) -> "Metric":
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("metric fields are invalid")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class BenchmarkSample:
    sample_id: str
    case_id: str
    source_trace_sha256: str
    trace_bytes: int
    event_count: int | None
    event_count_status: str
    event_count_reason: str | None
    package_identity: str
    workload_identity: str
    environment_identity: str
    mode: str
    warmup: bool
    iteration: int
    status: str
    failure_reason: str | None
    metrics: tuple[tuple[str, Metric], ...]

    def __post_init__(self) -> None:
        _text(self.sample_id, "sample_id")
        if self.case_id not in _CASES or self.mode not in _MODES or self.status not in {"success", "failure"}:
            raise ValueError("sample identity/status is invalid")
        _sha(self.source_trace_sha256, "source_trace_sha256")
        _sha(self.package_identity, "package_identity")
        _text(self.workload_identity, "workload_identity")
        _text(self.environment_identity, "environment_identity")
        _nonnegative(self.trace_bytes, "trace_bytes", integer=True)
        if self.event_count_status == "measured":
            _nonnegative(self.event_count, "event_count", integer=True)
            if self.event_count_reason is not None:
                raise ValueError("measured event count cannot have a reason")
        elif self.event_count_status not in _METRIC_STATUS or self.event_count is not None or not isinstance(self.event_count_reason, str) or not self.event_count_reason:
            raise ValueError("unavailable event count requires null and reason")
        if not isinstance(self.warmup, bool) or not isinstance(self.iteration, int) or self.iteration < 0:
            raise ValueError("sample iteration is invalid")
        if self.status == "success" and self.failure_reason is not None:
            raise ValueError("successful sample cannot have failure reason")
        if self.status == "failure" and (not isinstance(self.failure_reason, str) or not self.failure_reason):
            raise ValueError("failed sample requires failure reason")
        names = [name for name, _ in self.metrics]
        if names != sorted(set(names)):
            raise ValueError("sample metrics are not deterministically ordered")
        for name, metric in self.metrics:
            _text(name, "metric name")
            if not isinstance(metric, Metric):
                raise ValueError("sample metric is invalid")

    def to_dict(self) -> dict[str, object]:
        return {**self.__dict__, "metrics": {name: metric.to_dict() for name, metric in self.metrics}}


@dataclass(frozen=True)
class BenchmarkSummary:
    case_id: str
    mode: str
    measured_sample_count: int
    success_count: int
    failure_count: int
    metrics: tuple[tuple[str, dict[str, int | float]], ...]

    def __post_init__(self) -> None:
        if self.case_id not in _CASES or self.mode not in _MODES:
            raise ValueError("summary identity is invalid")
        for value, name in ((self.measured_sample_count, "measured_sample_count"), (self.success_count, "success_count"), (self.failure_count, "failure_count")):
            _nonnegative(value, name, integer=True)
        if self.success_count + self.failure_count != self.measured_sample_count:
            raise ValueError("summary counts are inconsistent")
        names = [name for name, _ in self.metrics]
        if names != sorted(set(names)):
            raise ValueError("summary metrics are not ordered")
        for name, values in self.metrics:
            _text(name, "summary metric name")
            if set(values) != {"sample_count", "median", "min", "max", "mad"} or not isinstance(values["sample_count"], int) or values["sample_count"] < 1:
                raise ValueError("summary metric fields are invalid")
            if any(isinstance(values[key], bool) or not isinstance(values[key], (int, float)) or values[key] < 0 for key in ("median", "min", "max", "mad")) or values["min"] > values["median"] or values["median"] > values["max"]:
                raise ValueError("summary metric values are invalid")

    def to_dict(self) -> dict[str, object]:
        return {**self.__dict__, "metrics": {name: values for name, values in self.metrics}}


@dataclass(frozen=True)
class BenchmarkEvidence:
    environment_identity: str
    environment: tuple[tuple[str, str], ...]
    samples: tuple[BenchmarkSample, ...]
    summaries: tuple[BenchmarkSummary, ...]
    acquisition_overhead: dict[str, object]

    def __post_init__(self) -> None:
        _text(self.environment_identity, "environment_identity")
        if tuple(sorted(self.environment)) != self.environment:
            raise ValueError("environment fields are not ordered")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.environment):
            raise ValueError("environment fields are invalid")
        ids = [sample.sample_id for sample in self.samples]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("sample IDs are not unique and ordered")
        summary_keys = [(summary.case_id, summary.mode) for summary in self.summaries]
        if summary_keys != sorted(summary_keys) or len(summary_keys) != len(set(summary_keys)):
            raise ValueError("summaries are not unique and ordered")
        if self.acquisition_overhead != {"status": "not_evaluated", "reason": "missing_board_or_source_identical_firmware"}:
            raise ValueError("acquisition overhead must fail closed")

    def to_dict(self) -> dict[str, object]:
        return {
            "benchmark_evidence_version": VERSION,
            "claim_strength": CLAIM_STRENGTH,
            "hardware_validation": False,
            "environment_identity": self.environment_identity,
            "environment": dict(self.environment),
            "samples": [sample.to_dict() for sample in self.samples],
            "summaries": [summary.to_dict() for summary in self.summaries],
            "acquisition_overhead": self.acquisition_overhead,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.to_dict())

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> "BenchmarkEvidence":
        if not isinstance(value, Mapping):
            raise ValueError("benchmark evidence must be an object")
        required = {"benchmark_evidence_version", "claim_strength", "hardware_validation", "environment_identity", "environment", "samples", "summaries", "acquisition_overhead"}
        if set(value) != required or value["benchmark_evidence_version"] != VERSION or value["claim_strength"] != CLAIM_STRENGTH or value["hardware_validation"] is not False:
            raise ValueError("benchmark evidence fields are invalid")
        environment = value["environment"]
        samples = value["samples"]
        summaries = value["summaries"]
        if not isinstance(environment, Mapping) or not isinstance(samples, list) or not isinstance(summaries, list):
            raise ValueError("benchmark evidence containers are invalid")
        parsed_samples: list[BenchmarkSample] = []
        for row in samples:
            if not isinstance(row, Mapping):
                raise ValueError("sample is invalid")
            data = dict(row)
            metrics = data.pop("metrics", None)
            if not isinstance(metrics, Mapping):
                raise ValueError("sample metrics are invalid")
            data["metrics"] = tuple((str(name), Metric.from_dict(metric)) for name, metric in sorted(metrics.items()))
            parsed_samples.append(BenchmarkSample(**data))  # type: ignore[arg-type]
        parsed_summaries: list[BenchmarkSummary] = []
        for row in summaries:
            if not isinstance(row, Mapping):
                raise ValueError("summary is invalid")
            data = dict(row)
            metrics = data.pop("metrics", None)
            if not isinstance(metrics, Mapping):
                raise ValueError("summary metrics are invalid")
            data["metrics"] = tuple((str(name), dict(metric)) for name, metric in sorted(metrics.items()) if isinstance(metric, Mapping))
            if len(data["metrics"]) != len(metrics):
                raise ValueError("summary metric is invalid")
            parsed_summaries.append(BenchmarkSummary(**data))  # type: ignore[arg-type]
        return cls(str(value["environment_identity"]), tuple(sorted((str(k), str(v)) for k, v in environment.items())), tuple(parsed_samples), tuple(parsed_summaries), dict(value["acquisition_overhead"]))


def metric(status: str, value: int | float | None, unit: str, reason: str | None = None) -> Metric:
    return Metric(status, value, unit, reason)

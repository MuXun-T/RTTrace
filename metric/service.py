from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from parser.result import Result as CoreResult

from .core import (
    MetricConfig,
    alert_Evaluate as core_alert_Evaluate,
    diag_Backtrace as core_diag_Backtrace,
    diag_Generate as core_diag_Generate,
    metric_Compare as core_metric_Compare,
    metric_Compute as core_metric_Compute,
    metric_Ingest as core_metric_Ingest,
    metric_Init as core_metric_Init,
)
from spec.result import Result


def _legacy_result(result: CoreResult[Any]) -> Result[Any]:
    if result.ok:
        return Result.ok(
            result.data,
            warnings=result.warnings,
            untrusted_windows=result.untrusted_windows,
        )
    return Result.err(
        result.code,
        result.message,
        data=result.data,
        warnings=result.warnings,
        untrusted_windows=result.untrusted_windows,
    )


def _scope_dict(scope: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(scope, dict):
        return dict(scope)
    if is_dataclass(scope):
        return asdict(scope)
    return {
        "aligned_time_window": getattr(scope, "aligned_time_window"),
        "filter": dict(getattr(scope, "filter", {})),
        "metric_ids": list(getattr(scope, "metric_ids", [])),
        "scope_id": getattr(scope, "scope_id", None),
    }


class MetricEngine:
    """Legacy compatibility shim that forwards to metric.core."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw_config = {
            "long_block_threshold": 5.0,
            "long_irq_threshold": 3.0,
            "irq_latency_threshold": 3.0,
            **(config or {}),
        }
        if "irq_threshold" in raw_config:
            threshold = float(raw_config.pop("irq_threshold"))
            raw_config.setdefault("long_irq_threshold", threshold)
            raw_config.setdefault("irq_latency_threshold", threshold)
        init = core_metric_Init(MetricConfig(**raw_config))
        if not init.ok:
            raise RuntimeError(init.message)
        self.session = init.data

    def metric_Ingest(self, rebuild_bundle: Any) -> Result[Any]:
        return _legacy_result(core_metric_Ingest(self.session, rebuild_bundle))

    def metric_Compute(self, t_begin: float, t_end: float, flt: dict[str, Any] | None = None) -> Result[Any]:
        return _legacy_result(core_metric_Compute(self.session, t_begin, t_end, flt))

    def alert_Evaluate(self, t_begin: float, t_end: float, flt: dict[str, Any] | None = None) -> Result[Any]:
        return _legacy_result(core_alert_Evaluate(self.session, t_begin, t_end, flt))

    def diag_Generate(self, t_begin: float, t_end: float, alert_list: list[Any] | None = None) -> Result[Any]:
        return _legacy_result(core_diag_Generate(self.session, t_begin, t_end, alert_list))

    def diag_Backtrace(self, diag_or_alert_id: str) -> Result[Any]:
        by_diag = core_diag_Backtrace(self.session, diag_id=diag_or_alert_id)
        if by_diag.ok:
            return _legacy_result(by_diag)
        return _legacy_result(core_diag_Backtrace(self.session, alert_id=diag_or_alert_id))

    def metric_Compare(self, candidate_bundle: Any, scope: dict[str, Any] | Any) -> Result[Any]:
        if self.session.bundle is None:
            return Result.err("NOT_READY", "metric_Ingest must be called before metric comparison")
        return _legacy_result(core_metric_Compare(self.session.bundle, candidate_bundle, _scope_dict(scope), self.session.cfg))

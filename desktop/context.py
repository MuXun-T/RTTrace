from __future__ import annotations

import copy
import threading

from parser.models import AnalysisContext

from .qt_compat import QObject, new_signal


_FORMAL_CONTEXT_FIELDS = {
    "time_window",
    "filter",
    "selection",
    "zoom_level",
    "focused_view",
    "evidence_anchor",
    "playback_cursor",
    "compare_scope",
    "dataset_role",
}


class ContextStore(QObject):
    def __init__(self, initial: AnalysisContext | None = None) -> None:
        super().__init__()
        self._context = initial or AnalysisContext()
        self._lock = threading.Lock()
        self.changed = new_signal()

    def _normalize_delta(self, delta: dict[str, object]) -> dict[str, object]:
        normalized = {
            key: copy.deepcopy(value)
            for key, value in delta.items()
            if hasattr(self._context, key)
        }
        if any(key in _FORMAL_CONTEXT_FIELDS for key in normalized):
            normalized.setdefault("hover_target", None)
            normalized.setdefault("transient_selection", None)
        if "time_window" in normalized and normalized["time_window"] is not None:
            normalized["time_window"] = tuple(normalized["time_window"])  # type: ignore[arg-type]
        if "filter" in normalized:
            normalized["filter"] = dict(normalized["filter"] or {})
        if "selection" in normalized:
            normalized["selection"] = dict(normalized["selection"] or {})
        if "compare_scope" in normalized and normalized["compare_scope"] is not None:
            normalized["compare_scope"] = dict(normalized["compare_scope"])  # type: ignore[arg-type]
        if "hover_target" in normalized:
            hover_target = normalized["hover_target"]
            normalized["hover_target"] = dict(hover_target) if hover_target else None  # type: ignore[arg-type]
        if "transient_selection" in normalized:
            transient_selection = normalized["transient_selection"]
            normalized["transient_selection"] = (
                dict(transient_selection) if transient_selection else None  # type: ignore[arg-type]
            )
        if "pending_jobs" in normalized:
            normalized["pending_jobs"] = list(normalized["pending_jobs"] or [])
        if "playback_runtime" in normalized:
            playback_runtime = normalized["playback_runtime"]
            normalized["playback_runtime"] = dict(playback_runtime) if playback_runtime else None  # type: ignore[arg-type]
        return normalized

    def _apply_delta(self, delta: dict[str, object]) -> AnalysisContext:
        normalized = self._normalize_delta(delta)
        for key, value in normalized.items():
            if hasattr(self._context, key):
                setattr(self._context, key, value)
        self._context.context_rev += 1
        return copy.deepcopy(self._context)

    def get(self) -> AnalysisContext:
        with self._lock:
            return copy.deepcopy(self._context)

    def replace(self, context: AnalysisContext) -> AnalysisContext:
        with self._lock:
            context.context_rev = self._context.context_rev + 1
            self._context = copy.deepcopy(context)
            current = copy.deepcopy(self._context)
        self.changed.emit(current)
        return current

    def commit(self, delta: dict[str, object]) -> AnalysisContext:
        with self._lock:
            current = self._apply_delta(delta)
        self.changed.emit(current)
        return current

    def set_hover_target(
        self,
        hover_target: dict[str, object] | None,
        *,
        transient_selection: dict[str, object] | None = None,
    ) -> AnalysisContext:
        delta: dict[str, object | None] = {"hover_target": hover_target}
        if transient_selection is not None or hover_target is None:
            delta["transient_selection"] = transient_selection
        return self.commit(delta)

    def set_transient_selection(
        self,
        transient_selection: dict[str, object] | None,
        *,
        hover_target: dict[str, object] | None = None,
    ) -> AnalysisContext:
        delta: dict[str, object | None] = {"transient_selection": transient_selection}
        if hover_target is not None or transient_selection is None:
            delta["hover_target"] = hover_target
        return self.commit(delta)

    def commit_transient_selection(self) -> AnalysisContext:
        with self._lock:
            selection = copy.deepcopy(self._context.transient_selection) or {}
            if not selection:
                return copy.deepcopy(self._context)
            current = self._apply_delta(
                {
                    "selection": selection,
                    "transient_selection": None,
                }
            )
        self.changed.emit(current)
        return current

    def clear_transient_selection(self, *, clear_hover_target: bool = True) -> AnalysisContext:
        delta: dict[str, object | None] = {"transient_selection": None}
        if clear_hover_target:
            delta["hover_target"] = None
        return self.commit(delta)

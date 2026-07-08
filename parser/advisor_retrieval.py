from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


CASE_BANK_VERSION = "runtime-optimization-case-bank-v1"
DEFAULT_RETRIEVAL_TOP_K = 3
DEFAULT_SIMILARITY_THRESHOLD = 14
HIGH_RISK_LEVELS = {"high", "critical"}
CASE_LABELS = {"accept", "reject", "abstain", "unsafe", "needs_more_data"}
READ_ONLY_CONTEXT_FIELDS = {
    "advisor_phase",
    "case_similarity_features",
    "dictionary_checksum_prefix",
    "embodiment_mode",
    "export_family",
    "gate_policy_summary",
    "input_bytes",
    "platform",
    "retrieved_case_refs",
    "sidecar_bytes",
    "sidecar_row_count",
    "ticket_present",
    "ticket_validated",
    "trace_checksum_prefix",
}


def default_case_bank_root() -> Path:
    return Path(__file__).resolve().parent.parent / "docs" / "runtime_optimization_case_bank"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_strings(values: Iterable[Any] | None) -> list[str]:
    normalized = [str(value).strip() for value in list(values or []) if str(value).strip()]
    return list(dict.fromkeys(normalized))


def _bucketize(
    value: int | float | None,
    *,
    thresholds: list[tuple[float, str]],
    missing: str = "missing",
    fallback: str = "xlarge",
) -> str:
    if value is None:
        return missing
    numeric = float(value)
    for limit, label in thresholds:
        if numeric < float(limit):
            return label
    return fallback


def build_case_similarity_features(features: dict[str, Any]) -> dict[str, Any]:
    payload = dict(features or {})
    risk_history_summary = dict(payload.get("risk_history_summary") or {})
    sidecar_bytes = _optional_int(payload.get("sidecar_bytes"))
    if sidecar_bytes is None:
        sidecar_bytes = _optional_int(payload.get("ticket_sidecar_bytes"))
    sidecar_row_count = _optional_int(payload.get("sidecar_row_count"))
    if sidecar_row_count is None:
        sidecar_row_count = _optional_int(payload.get("ticket_row_count"))
    missing_fields: list[str] = []
    if (_optional_int(risk_history_summary.get("row_count")) or 0) <= 0:
        missing_fields.append("telemetry_history")
    for key in ("runtime_seconds", "peak_rss_mb"):
        if _optional_float(payload.get(key)) is None:
            missing_fields.append(key)
    if _optional_int(payload.get("input_bytes")) is None:
        missing_fields.append("input_bytes")
    return {
        "input_bytes_bucket": _bucketize(
            _optional_int(payload.get("input_bytes")),
            thresholds=[
                (1 * 1024 * 1024, "tiny"),
                (64 * 1024 * 1024, "small"),
                (256 * 1024 * 1024, "medium"),
                (1024 * 1024 * 1024, "large"),
            ],
        ),
        "sidecar_bytes_bucket": _bucketize(
            sidecar_bytes,
            thresholds=[
                (1 * 1024 * 1024, "tiny"),
                (64 * 1024 * 1024, "small"),
                (1024 * 1024 * 1024, "large"),
                (16 * 1024 * 1024 * 1024, "xlarge"),
            ],
            fallback="huge",
        ),
        "sidecar_row_count_bucket": _bucketize(
            sidecar_row_count,
            thresholds=[(10, "tiny"), (1000, "small"), (100000, "medium"), (5000000, "large")],
            fallback="xlarge",
        ),
        "peak_rss_bucket": _bucketize(
            _optional_float(payload.get("peak_rss_mb")),
            thresholds=[(512.0, "low"), (2048.0, "medium"), (4096.0, "high")],
            fallback="critical",
        ),
        "ticket_present": bool(payload.get("ticket_present")),
        "ticket_validated": bool(payload.get("ticket_validated")),
        "advisor_phase": str(payload.get("advisor_phase") or ""),
        "export_family": str(payload.get("export_family") or ""),
        "embodiment_mode": str(payload.get("embodiment_mode") or ""),
        "platform": str(payload.get("platform") or ""),
        "missing_telemetry_fields": missing_fields,
    }


def _load_case_entries(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("cases"), list):
            return [dict(item) for item in payload["cases"] if isinstance(item, dict)]
        return [dict(payload)]
    return []


def load_case_bank(case_bank_root: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(case_bank_root or default_case_bank_root()).expanduser().resolve()
    if not root.exists():
        return []
    manifest_path = root / "manifest.json"
    case_files: list[Path] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        case_files = [
            (root / str(path)).resolve()
            for path in list(dict(manifest).get("case_files") or [])
            if str(path).strip()
        ]
    if not case_files:
        case_files = sorted(root.glob("cases/**/*.json")) + sorted(root.glob("*.json"))
    rows: list[dict[str, Any]] = []
    for path in case_files:
        if path.name == "manifest.json" or not path.exists():
            continue
        for entry in _load_case_entries(path):
            label = str(entry.get("label") or "").strip()
            case_id = str(entry.get("case_id") or "").strip()
            if not case_id or label not in CASE_LABELS:
                continue
            similarity_features = dict(entry.get("similarity_features") or {})
            rows.append(
                {
                    "case_id": case_id,
                    "label": label,
                    "action_kind": str(entry.get("action_kind") or "").strip(),
                    "risk_level": str(entry.get("risk_level") or "").strip() or "low",
                    "source_ref": str(entry.get("source_ref") or "").strip() or None,
                    "case_tags": _normalized_strings(entry.get("case_tags")),
                    "similarity_features": similarity_features,
                    "expected_decision": dict(entry.get("expected_decision") or {}),
                    "gate_outcome": dict(entry.get("gate_outcome") or {}),
                    "reject_taxonomy": (
                        None if entry.get("reject_taxonomy") is None else str(entry.get("reject_taxonomy")).strip() or None
                    ),
                }
            )
    return sorted(rows, key=lambda item: str(item["case_id"]))


def _similarity_score(
    *,
    current_features: dict[str, Any],
    case: dict[str, Any],
    proposed_action_kinds: set[str],
) -> int:
    case_features = dict(case.get("similarity_features") or {})
    score = 0
    if str(case.get("action_kind") or "") in proposed_action_kinds:
        score += 6
    weighted_keys = {
        "input_bytes_bucket": 5,
        "sidecar_bytes_bucket": 4,
        "sidecar_row_count_bucket": 3,
        "peak_rss_bucket": 3,
        "ticket_present": 3,
        "ticket_validated": 2,
        "advisor_phase": 2,
        "export_family": 2,
        "embodiment_mode": 1,
        "platform": 1,
    }
    for key, weight in weighted_keys.items():
        if case_features.get(key) == current_features.get(key):
            score += weight
    current_missing = set(_normalized_strings(current_features.get("missing_telemetry_fields")))
    case_missing = set(_normalized_strings(case_features.get("missing_telemetry_fields")))
    if current_missing and case_missing:
        score += 2 * len(current_missing & case_missing)
    return score


def _conflicting_labels(cases: list[dict[str, Any]], proposed_action_kinds: set[str]) -> list[str]:
    labels_by_action: dict[str, set[str]] = {}
    for case in cases:
        action_kind = str(case.get("action_kind") or "")
        if action_kind and proposed_action_kinds and action_kind not in proposed_action_kinds:
            continue
        labels_by_action.setdefault(action_kind or "baseline", set()).add(str(case.get("label") or ""))
    conflicts: list[str] = []
    for action_kind, labels in sorted(labels_by_action.items()):
        if "accept" in labels and labels.intersection({"reject", "unsafe", "abstain", "needs_more_data"}):
            conflicts.append(action_kind)
        elif "unsafe" in labels and labels.intersection({"reject", "abstain", "needs_more_data"}):
            conflicts.append(action_kind)
    return conflicts


@dataclass(frozen=True)
class RetrievalSummary:
    retrieved_case_refs: list[str]
    case_similarity_features: dict[str, Any]
    matched_case_count: int
    similar_case_count: int
    has_sufficient_similarity: bool
    conflicting_actions: list[str]
    unsafe_case_match: bool
    top_score: int
    matched_labels: list[str]
    reject_taxonomy_coverage: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieved_case_refs": list(self.retrieved_case_refs),
            "case_similarity_features": dict(self.case_similarity_features),
            "matched_case_count": int(self.matched_case_count),
            "similar_case_count": int(self.similar_case_count),
            "has_sufficient_similarity": bool(self.has_sufficient_similarity),
            "conflicting_actions": list(self.conflicting_actions),
            "unsafe_case_match": bool(self.unsafe_case_match),
            "top_score": int(self.top_score),
            "matched_labels": list(self.matched_labels),
            "reject_taxonomy_coverage": float(self.reject_taxonomy_coverage),
        }


def retrieve_runtime_cases(
    *,
    current_features: dict[str, Any],
    proposed_actions: Iterable[Any] | None = None,
    case_bank_root: str | Path | None = None,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    similarity_threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
) -> RetrievalSummary:
    similarity_features = build_case_similarity_features(current_features)
    cases = load_case_bank(case_bank_root)
    proposed_action_kinds = {
        str(getattr(action, "action_kind", None) or dict(action).get("action_kind") or "").strip()
        for action in list(proposed_actions or [])
        if str(getattr(action, "action_kind", None) or dict(action).get("action_kind") or "").strip()
    }
    ranked = []
    for case in cases:
        score = _similarity_score(
            current_features=similarity_features,
            case=case,
            proposed_action_kinds=proposed_action_kinds,
        )
        ranked.append({**case, "score": score})
    ranked.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("case_id") or "")))
    effective_top_k = max(1, int(top_k or 1))
    top_cases = ranked[:effective_top_k]
    similar_cases = [case for case in top_cases if int(case.get("score") or 0) >= int(similarity_threshold or 0)]
    conflicting_actions = _conflicting_labels(similar_cases, proposed_action_kinds)
    unsafe_case_match = any(
        str(case.get("label") or "") == "unsafe"
        and str(case.get("action_kind") or "") in proposed_action_kinds
        and str(case.get("risk_level") or "") in HIGH_RISK_LEVELS
        for case in similar_cases
    )
    rejected_cases = [case for case in top_cases if str(case.get("label") or "") in {"reject", "unsafe"}]
    classified_rejects = [
        case
        for case in rejected_cases
        if str(case.get("reject_taxonomy") or "").strip()
        or str(dict(case.get("gate_outcome") or {}).get("rejected_reason") or "").strip()
    ]
    reject_taxonomy_coverage = 1.0
    if rejected_cases:
        reject_taxonomy_coverage = round(len(classified_rejects) / len(rejected_cases), 6)
    return RetrievalSummary(
        retrieved_case_refs=[str(case["case_id"]) for case in top_cases],
        case_similarity_features=similarity_features,
        matched_case_count=len(top_cases),
        similar_case_count=len(similar_cases),
        has_sufficient_similarity=bool(similar_cases),
        conflicting_actions=conflicting_actions,
        unsafe_case_match=unsafe_case_match,
        top_score=max([int(case.get("score") or 0) for case in top_cases], default=0),
        matched_labels=sorted({str(case.get("label") or "") for case in similar_cases}),
        reject_taxonomy_coverage=reject_taxonomy_coverage,
    )

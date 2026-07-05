from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from spec.io import checksum_file, serialize


FLOAT_TOLERANCE = 1e-6
REQUIRED_METRIC_IDS = {
    "deadline_miss",
    "context_switch_count",
    "blocked_time",
    "cpu_utilization",
    "ready_wait_time",
}
DYNAMIC_KEYS = {
    "generated_at",
    "export_time",
    "created_at",
    "snapshot_id",
    "job_id",
    "package_path",
    "context_rev",
    "pending_jobs",
    "hover_target",
    "transient_selection",
    "playback_runtime",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def file_sha256_or_unknown(path: str | Path | None) -> str:
    if path is None:
        return "unknown"
    try:
        target = Path(path)
        if not target.is_file():
            return "unknown"
        return checksum_file(target)
    except OSError:
        return "unknown"


def event_ref_key_from_payload(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text.startswith("evt:") else None
    if not isinstance(value, dict):
        return None
    ref_key = value.get("ref_key")
    if not ref_key:
        return None
    ref_key_text = str(ref_key).strip()
    ref_type = str(value.get("ref_type", "")).strip().lower()
    if ref_type == "event":
        return ref_key_text
    if ref_type in {"", "index"} and ref_key_text.startswith("evt:"):
        return ref_key_text
    return None


def build_anchor_rows(
    context: dict[str, Any],
    alerts: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    evidence_anchor = context.get("evidence_anchor")
    if evidence_anchor:
        anchors.append(
            {
                "anchor_id": "context:current",
                "anchor_type": "context",
                "evidence_anchor": evidence_anchor,
                "time_window": context.get("time_window", []),
            }
        )
    for alert in alerts:
        for index, evidence_ref in enumerate(alert.get("evidence_refs") or []):
            anchors.append(
                {
                    "anchor_id": f"{alert.get('alert_id')}:{index}",
                    "anchor_type": "alert",
                    "owner_id": alert.get("alert_id"),
                    "evidence_anchor": evidence_ref,
                    "time_window": alert.get("time_window", []),
                }
            )
    diagnosis_rows = diagnoses
    if diagnosis_rows is None:
        diagnosis_rows = [
            {
                "diag_id": f"diag:{alert.get('alert_id')}",
                "evidence_refs": alert.get("evidence_refs") or [],
                "time_window": alert.get("time_window", []),
            }
            for alert in alerts
        ]
    for diagnosis in diagnosis_rows:
        for index, evidence_ref in enumerate(diagnosis.get("evidence_refs") or []):
            anchors.append(
                {
                    "anchor_id": f"{diagnosis.get('diag_id')}:{index}",
                    "anchor_type": "diagnosis",
                    "owner_id": diagnosis.get("diag_id"),
                    "evidence_anchor": evidence_ref,
                    "time_window": diagnosis.get("time_window", []),
                }
            )
    return serialize(anchors)


def build_analysis_snapshot(
    *,
    snapshot_kind: str,
    input_path: str | Path | None,
    dataset_id: str | None,
    parser_version: str,
    comparison_scope: dict[str, Any],
    metrics: list[Any],
    alerts: list[Any],
    diagnoses: list[Any],
    anchors: list[Any] | None,
    rebuild_bundle: dict[str, Any],
    ref_index: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    package_path: str | Path | None = None,
    analysis_context: dict[str, Any] | None = None,
    result_validity: Any | None = None,
    export_observation: dict[str, Any] | None = None,
    baseline_generation_mode: str = "materialized",
    input_sha256: str | None = None,
    consumer_mode: Any | None = None,
    consumer_mode_explain: Any | None = None,
) -> dict[str, Any]:
    input_text = str(input_path) if input_path is not None else None
    package_text = str(package_path) if package_path is not None else None
    scope = normalize_comparison_scope(comparison_scope)
    context = dict(analysis_context or {})
    if not context:
        context = {
            "time_window": scope.get("time_window", []),
            "filter": scope.get("filter", {}),
            "selection": scope.get("selection", {}),
            "compare_scope": None,
            "dataset_role": "single",
        }
    alert_rows = serialize(alerts)
    diagnosis_rows = serialize(diagnoses)
    anchor_rows = serialize(anchors) if anchors is not None else build_anchor_rows(context, alert_rows, diagnosis_rows)
    return {
        "snapshot_kind": snapshot_kind,
        "generated_at": iso_now(),
        "source": {
            "input_path": input_text,
            "input_sha256": input_sha256 if input_sha256 is not None else file_sha256_or_unknown(input_path),
            "dataset_id": dataset_id,
            "parser_version": parser_version,
            "package_path": package_text,
        },
        "baseline_generation_mode": baseline_generation_mode,
        "comparison_scope": scope,
        "analysis_context": serialize(context),
        "metrics": serialize(metrics),
        "alerts": alert_rows,
        "diagnoses": diagnosis_rows,
        "anchors": anchor_rows,
        "ref_index": serialize(ref_index or []),
        "rebuild_bundle": serialize(rebuild_bundle),
        "meta": serialize(meta or {}),
        "manifest": serialize(manifest or {}),
        "result_validity": serialize(result_validity),
        "consumer_mode": serialize(consumer_mode),
        "consumer_mode_explain": serialize(consumer_mode_explain),
        "export_resource_observation": serialize(export_observation or {}),
    }


def normalize_comparison_scope(scope: dict[str, Any]) -> dict[str, Any]:
    payload = dict(scope or {})
    window = payload.get("time_window") or payload.get("window") or [0.0, 0.0]
    payload["time_window"] = [float(window[0]), float(window[1])]
    payload["filter"] = normalize_payload(payload.get("filter") or {})
    payload["selection"] = normalize_payload(payload.get("selection") or {})
    payload["metric_config"] = normalize_payload(payload.get("metric_config") or {})
    return payload


def normalize_payload(value: Any) -> Any:
    value = serialize(value)
    if isinstance(value, dict):
        return {
            str(key): normalize_payload(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in DYNAMIC_KEYS
        }
    if isinstance(value, list):
        return [normalize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_payload(item) for item in value]
    if isinstance(value, float):
        if math.isfinite(value):
            return round(value, 9)
        return value
    return value


def equivalent(left: Any, right: Any, *, tolerance: float = FLOAT_TOLERANCE) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tolerance
    if isinstance(left, dict) and isinstance(right, dict):
        left_keys = {str(key) for key in left if str(key) not in DYNAMIC_KEYS}
        right_keys = {str(key) for key in right if str(key) not in DYNAMIC_KEYS}
        if left_keys != right_keys:
            return False
        return all(equivalent(left.get(key), right.get(key), tolerance=tolerance) for key in left_keys)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(equivalent(a, b, tolerance=tolerance) for a, b in zip(left, right))
    return normalize_payload(left) == normalize_payload(right)


def numeric_delta_max(left: Any, right: Any) -> float:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right))
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) & set(right)
        return max((numeric_delta_max(left[key], right[key]) for key in keys), default=0.0)
    if isinstance(left, list) and isinstance(right, list):
        return max((numeric_delta_max(a, b) for a, b in zip(left, right)), default=0.0)
    return 0.0


def compare_snapshots(
    full_snapshot: dict[str, Any],
    clipped_snapshot: dict[str, Any],
    *,
    diff_sample_limit: int = 20,
    tolerance: float = FLOAT_TOLERANCE,
) -> dict[str, Any]:
    collector = _DiffCollector(diff_sample_limit)
    ref_index_keys = _ref_index_keys(clipped_snapshot.get("ref_index") or [])
    result_validity = compare_result_validity(full_snapshot, clipped_snapshot, collector)
    scope_report = compare_scope(full_snapshot, clipped_snapshot, collector, tolerance=tolerance)
    alert_report = compare_alerts(full_snapshot, clipped_snapshot, ref_index_keys, collector, tolerance=tolerance)
    diagnosis_report = compare_diagnoses(full_snapshot, clipped_snapshot, ref_index_keys, collector, tolerance=tolerance)
    metric_report = compare_metrics(full_snapshot, clipped_snapshot, ref_index_keys, collector, tolerance=tolerance)
    resource_report = compare_resources(full_snapshot, clipped_snapshot, ref_index_keys, collector, tolerance=tolerance)
    ref_report = compare_event_ref_closure(clipped_snapshot, collector)
    export_observation = export_resource_observation(clipped_snapshot)

    strict_pass = all(
        [
            scope_report["scope_identity_pass"],
            alert_report["alert_completeness_pass"],
            diagnosis_report["diagnosis_completeness_pass"],
            metric_report["metric_completeness_pass"],
            resource_report["resource_semantic_completeness_pass"],
            ref_report["event_ref_closure_pass"],
            result_validity["result_validity_strict_pass"],
            export_observation["export_resource_observation_pass"],
        ]
    )
    bounded_pass = _bounded_pass_allowed(
        strict_pass=strict_pass,
        scope_report=scope_report,
        alert_report=alert_report,
        diagnosis_report=diagnosis_report,
        metric_report=metric_report,
        resource_report=resource_report,
        ref_report=ref_report,
        result_validity=result_validity,
        export_observation=export_observation,
    )
    status = "strict_pass" if strict_pass else ("bounded_pass" if bounded_pass else "fail")
    return {
        "status": status,
        "generated_at": iso_now(),
        "comparison_scope": scope_report,
        "alert_completeness": alert_report,
        "diagnosis_completeness": diagnosis_report,
        "metric_completeness": metric_report,
        "resource_semantic_completeness": resource_report,
        "event_ref_closure": ref_report,
        "result_validity": result_validity,
        "export_resource_observation": export_observation,
        "diff_samples": collector.payload(),
        "artifacts": {
            "full_baseline": "full_baseline.json",
            "clipped_target": "clipped_target.json",
            "clipped_package": clipped_snapshot.get("source", {}).get("package_path"),
        },
    }


def compare_scope(
    full_snapshot: dict[str, Any],
    clipped_snapshot: dict[str, Any],
    collector: "_DiffCollector",
    *,
    tolerance: float,
) -> dict[str, Any]:
    full_scope = normalize_comparison_scope(full_snapshot.get("comparison_scope") or {})
    clipped_scope = normalize_comparison_scope(clipped_snapshot.get("comparison_scope") or {})
    clipped_meta = clipped_snapshot.get("meta") or {}
    export_scope = clipped_meta.get("export_scope") or {}
    full_source = full_snapshot.get("source") or {}
    clipped_source = clipped_snapshot.get("source") or {}
    input_sha256_match = (
        full_source.get("input_sha256") != "unknown"
        and full_source.get("input_sha256") == clipped_source.get("input_sha256")
    )
    export_source_path = (clipped_meta.get("export_source") or {}).get("source")
    if export_source_path:
        input_sha256_match = (
            input_sha256_match
            and file_sha256_or_unknown(export_source_path) == full_source.get("input_sha256")
        )
    parser_version_match = full_source.get("parser_version") == clipped_source.get("parser_version")
    if clipped_meta.get("parser_ver") is not None:
        parser_version_match = parser_version_match and full_source.get("parser_version") == clipped_meta.get("parser_ver")
    time_window_match = equivalent(full_scope.get("time_window"), clipped_scope.get("time_window"), tolerance=tolerance)
    if clipped_meta.get("time_window") is not None:
        time_window_match = time_window_match and equivalent(
            full_scope.get("time_window"),
            clipped_meta.get("time_window"),
            tolerance=tolerance,
        )
    if export_scope.get("window") is not None:
        time_window_match = time_window_match and equivalent(
            full_scope.get("time_window"),
            export_scope.get("window"),
            tolerance=tolerance,
        )
    filter_match = equivalent(full_scope.get("filter"), clipped_scope.get("filter"), tolerance=tolerance)
    if clipped_meta.get("filter") is not None:
        filter_match = filter_match and equivalent(full_scope.get("filter"), clipped_meta.get("filter"), tolerance=tolerance)
    if export_scope.get("filter") is not None:
        filter_match = filter_match and equivalent(
            full_scope.get("filter"),
            export_scope.get("filter"),
            tolerance=tolerance,
        )
    selection_match = equivalent(full_scope.get("selection"), clipped_scope.get("selection"), tolerance=tolerance)
    metric_config_match = equivalent(
        full_scope.get("metric_config"),
        clipped_scope.get("metric_config"),
        tolerance=tolerance,
    )
    scope_identity_pass = all(
        [
            input_sha256_match,
            parser_version_match,
            time_window_match,
            filter_match,
            selection_match,
            metric_config_match,
        ]
    )
    if not scope_identity_pass:
        collector.add(
            "resources",
            {
                "kind": "scope_mismatch",
                "input_sha256_match": input_sha256_match,
                "parser_version_match": parser_version_match,
                "time_window_match": time_window_match,
                "filter_match": filter_match,
                "selection_match": selection_match,
                "metric_config_match": metric_config_match,
            },
        )
    return {
        "scope_identity_pass": scope_identity_pass,
        "baseline_generation_mode": str(full_snapshot.get("baseline_generation_mode") or "materialized"),
        "input_sha256_match": input_sha256_match,
        "parser_version_match": parser_version_match,
        "time_window_match": time_window_match,
        "filter_match": filter_match,
        "selection_match": selection_match,
        "metric_config_match": metric_config_match,
        "time_window": full_scope.get("time_window"),
        "filter": full_scope.get("filter"),
        "selection": full_scope.get("selection"),
        "metric_config": full_scope.get("metric_config"),
    }


def compare_alerts(
    full_snapshot: dict[str, Any],
    clipped_snapshot: dict[str, Any],
    ref_index_keys: set[str],
    collector: "_DiffCollector",
    *,
    tolerance: float,
) -> dict[str, Any]:
    fields = ("type", "severity", "object_scope", "trusted", "support_level", "evidence_refs")
    full_rows = _keyed_rows(full_snapshot.get("alerts") or [], "alert_id")
    clipped_rows = _keyed_rows(clipped_snapshot.get("alerts") or [], "alert_id")
    full_ids = set(full_rows)
    clipped_ids = set(clipped_rows)
    missing_ids = sorted(full_ids - clipped_ids)
    extra_ids = sorted(clipped_ids - full_ids)
    for alert_id in missing_ids:
        collector.add("alerts", {"kind": "missing_alert", "alert_id": alert_id})
    for alert_id in extra_ids:
        collector.add("alerts", {"kind": "extra_alert", "alert_id": alert_id})
    field_counts = {field: 0 for field in fields}
    time_delta_max = 0.0
    threshold_delta_max = 0.0
    actual_delta_max = 0.0
    for alert_id in sorted(full_ids & clipped_ids):
        full_row = full_rows[alert_id]
        clipped_row = clipped_rows[alert_id]
        time_delta = _time_window_delta(full_row.get("time_window"), clipped_row.get("time_window"))
        time_delta_max = max(time_delta_max, time_delta)
        if time_delta > tolerance:
            collector.add("alerts", {"kind": "field_diff", "alert_id": alert_id, "field": "time_window"})
        threshold_delta = numeric_delta_max(full_row.get("threshold"), clipped_row.get("threshold"))
        threshold_delta_max = max(threshold_delta_max, threshold_delta)
        if threshold_delta > tolerance:
            collector.add("alerts", {"kind": "field_diff", "alert_id": alert_id, "field": "threshold"})
        actual_delta = numeric_delta_max(full_row.get("actual"), clipped_row.get("actual"))
        actual_delta_max = max(actual_delta_max, actual_delta)
        if actual_delta > tolerance:
            collector.add("alerts", {"kind": "field_diff", "alert_id": alert_id, "field": "actual"})
        for field in fields:
            if not equivalent(full_row.get(field), clipped_row.get(field), tolerance=tolerance):
                field_counts[field] += 1
                collector.add("alerts", {"kind": "field_diff", "alert_id": alert_id, "field": field})
    full_event_refs = _event_refs_from_result_rows(full_snapshot.get("alerts") or [])
    missing_refs = sorted(ref for ref in full_event_refs if ref not in ref_index_keys)
    for ref_key in missing_refs:
        collector.add("alerts", {"kind": "missing_alert_evidence_ref", "ref_key": ref_key})
    support_unexpected = _unexpected_support_level_count(clipped_snapshot.get("alerts") or [], clipped_snapshot)
    matched_refs = len(full_event_refs) - len(missing_refs)
    hit_rate = 1.0 if not full_event_refs else matched_refs / len(full_event_refs)
    alert_field_diff_count = (
        sum(field_counts.values())
        + int(time_delta_max > tolerance)
        + int(threshold_delta_max > tolerance)
        + int(actual_delta_max > tolerance)
    )
    return {
        "alert_completeness_pass": (
            len(missing_ids) == 0
            and len(extra_ids) == 0
            and alert_field_diff_count == 0
            and len(missing_refs) == 0
            and support_unexpected == 0
        ),
        "alert_id_missing_count": len(missing_ids),
        "alert_id_extra_count": len(extra_ids),
        "alert_type_diff_count": field_counts["type"],
        "alert_severity_diff_count": field_counts["severity"],
        "alert_time_window_delta_max": round(time_delta_max, 9),
        "alert_object_scope_diff_count": field_counts["object_scope"],
        "alert_threshold_delta_max": round(threshold_delta_max, 9),
        "alert_actual_delta_max": round(actual_delta_max, 9),
        "alert_trusted_diff_count": field_counts["trusted"],
        "alert_evidence_ref_diff_count": field_counts["evidence_refs"],
        "alert_support_level_unexpected_count": support_unexpected,
        "alert_evidence_ref_missing_count": len(missing_refs),
        "alert_evidence_ref_hit_rate": round(hit_rate, 9),
        "alert_field_diff_count": alert_field_diff_count,
        "alert_unexplained_diff_count": (
            field_counts["type"]
            + field_counts["severity"]
            + field_counts["object_scope"]
            + int(time_delta_max > tolerance)
            + int(threshold_delta_max > tolerance)
            + int(actual_delta_max > tolerance)
        ),
    }


def compare_diagnoses(
    full_snapshot: dict[str, Any],
    clipped_snapshot: dict[str, Any],
    ref_index_keys: set[str],
    collector: "_DiffCollector",
    *,
    tolerance: float,
) -> dict[str, Any]:
    fields = (
        "diagnosis_type",
        "title",
        "conclusion",
        "object_scope",
        "related_alerts",
        "confidence",
        "support_level",
        "evidence_refs",
    )
    full_rows = _keyed_rows(full_snapshot.get("diagnoses") or [], "diag_id")
    clipped_rows = _keyed_rows(clipped_snapshot.get("diagnoses") or [], "diag_id")
    full_ids = set(full_rows)
    clipped_ids = set(clipped_rows)
    missing_ids = sorted(full_ids - clipped_ids)
    extra_ids = sorted(clipped_ids - full_ids)
    for diag_id in missing_ids:
        collector.add("diagnoses", {"kind": "missing_diagnosis", "diag_id": diag_id})
    for diag_id in extra_ids:
        collector.add("diagnoses", {"kind": "extra_diagnosis", "diag_id": diag_id})
    field_counts = {field: 0 for field in fields}
    time_delta_max = 0.0
    for diag_id in sorted(full_ids & clipped_ids):
        full_row = full_rows[diag_id]
        clipped_row = clipped_rows[diag_id]
        time_delta = _time_window_delta(full_row.get("time_window"), clipped_row.get("time_window"))
        time_delta_max = max(time_delta_max, time_delta)
        if time_delta > tolerance:
            collector.add("diagnoses", {"kind": "field_diff", "diag_id": diag_id, "field": "time_window"})
        for field in fields:
            if not equivalent(full_row.get(field), clipped_row.get(field), tolerance=tolerance):
                field_counts[field] += 1
                collector.add("diagnoses", {"kind": "field_diff", "diag_id": diag_id, "field": field})
    full_event_refs = _event_refs_from_result_rows(full_snapshot.get("diagnoses") or [])
    missing_refs = sorted(ref for ref in full_event_refs if ref not in ref_index_keys)
    for ref_key in missing_refs:
        collector.add("diagnoses", {"kind": "missing_diagnosis_evidence_ref", "ref_key": ref_key})
    support_unexpected = _unexpected_support_level_count(clipped_snapshot.get("diagnoses") or [], clipped_snapshot)
    preservation_rate = 1.0 if not full_ids else (len(full_ids) - len(missing_ids)) / len(full_ids)
    diag_field_diff_count = sum(field_counts.values()) + int(time_delta_max > tolerance)
    return {
        "diagnosis_completeness_pass": (
            len(missing_ids) == 0
            and len(extra_ids) == 0
            and diag_field_diff_count == 0
            and len(missing_refs) == 0
            and support_unexpected == 0
            and abs(preservation_rate - 1.0) <= tolerance
        ),
        "diag_id_missing_count": len(missing_ids),
        "diag_id_extra_count": len(extra_ids),
        "diag_type_diff_count": field_counts["diagnosis_type"],
        "diag_title_diff_count": field_counts["title"],
        "diag_conclusion_diff_count": field_counts["conclusion"],
        "diag_time_window_delta_max": round(time_delta_max, 9),
        "diag_object_scope_diff_count": field_counts["object_scope"],
        "diag_related_alerts_diff_count": field_counts["related_alerts"],
        "diag_confidence_diff_count": field_counts["confidence"],
        "diag_evidence_ref_diff_count": field_counts["evidence_refs"],
        "diag_support_level_unexpected_count": support_unexpected,
        "diag_evidence_ref_missing_count": len(missing_refs),
        "diagnosis_preservation_rate": round(preservation_rate, 9),
        "diag_field_diff_count": diag_field_diff_count,
        "diag_unexplained_diff_count": (
            field_counts["diagnosis_type"]
            + field_counts["title"]
            + field_counts["conclusion"]
            + field_counts["object_scope"]
            + field_counts["related_alerts"]
            + field_counts["confidence"]
            + int(time_delta_max > tolerance)
        ),
    }


def compare_metrics(
    full_snapshot: dict[str, Any],
    clipped_snapshot: dict[str, Any],
    ref_index_keys: set[str],
    collector: "_DiffCollector",
    *,
    tolerance: float,
) -> dict[str, Any]:
    fields = ("scope", "summary", "distribution", "series", "topn", "trusted")
    full_rows = _keyed_rows(full_snapshot.get("metrics") or [], "metric_id")
    clipped_rows = _keyed_rows(clipped_snapshot.get("metrics") or [], "metric_id")
    full_ids = set(full_rows)
    clipped_ids = set(clipped_rows)
    expected_ids = set(REQUIRED_METRIC_IDS)
    missing_ids = sorted((full_ids | expected_ids) - clipped_ids)
    extra_ids = sorted(clipped_ids - full_ids)
    for metric_id in missing_ids:
        collector.add("metrics", {"kind": "missing_metric", "metric_id": metric_id})
    for metric_id in extra_ids:
        collector.add("metrics", {"kind": "extra_metric", "metric_id": metric_id})
    summary_diff_count = 0
    distribution_delta_max = 0.0
    metric_diff_count = 0
    for metric_id in sorted(full_ids & clipped_ids):
        full_row = full_rows[metric_id]
        clipped_row = clipped_rows[metric_id]
        if not equivalent(full_row.get("summary"), clipped_row.get("summary"), tolerance=tolerance):
            summary_diff_count += 1
            collector.add("metrics", {"kind": "field_diff", "metric_id": metric_id, "field": "summary"})
        distribution_delta = numeric_delta_max(full_row.get("distribution"), clipped_row.get("distribution"))
        distribution_delta_max = max(distribution_delta_max, distribution_delta)
        for field in fields:
            if not equivalent(full_row.get(field), clipped_row.get(field), tolerance=tolerance):
                metric_diff_count += 1
                collector.add("metrics", {"kind": "field_diff", "metric_id": metric_id, "field": field})
    metric_refs = _event_refs_from_any(full_snapshot.get("metrics") or [])
    sample_missing = sorted(ref for ref in metric_refs if ref not in ref_index_keys)
    for ref_key in sample_missing:
        collector.add("metrics", {"kind": "missing_metric_sample_ref", "ref_key": ref_key})
    return {
        "metric_completeness_pass": (
            len(missing_ids) == 0
            and len(extra_ids) == 0
            and metric_diff_count == 0
            and len(sample_missing) == 0
            and distribution_delta_max <= tolerance
        ),
        "metric_id_missing_count": len(missing_ids),
        "metric_id_extra_count": len(extra_ids),
        "metric_summary_diff_count": summary_diff_count,
        "metric_distribution_delta_max": round(distribution_delta_max, 9),
        "metric_sample_ref_missing_count": len(sample_missing),
        "metric_diff_count": metric_diff_count,
        "required_metric_missing_count": len(sorted(expected_ids - clipped_ids)),
    }


def compare_resources(
    full_snapshot: dict[str, Any],
    clipped_snapshot: dict[str, Any],
    ref_index_keys: set[str],
    collector: "_DiffCollector",
    *,
    tolerance: float,
) -> dict[str, Any]:
    scope = normalize_comparison_scope(full_snapshot.get("comparison_scope") or {})
    window = tuple(scope.get("time_window") or [0.0, 0.0])
    filters = scope.get("filter") or {}
    full_bundle = full_snapshot.get("rebuild_bundle") or {}
    clipped_bundle = clipped_snapshot.get("rebuild_bundle") or {}
    full_summary = _resource_summary(full_bundle, window, filters)
    clipped_summary = _resource_summary(clipped_bundle, window, filters)
    task_runtime_delta = _map_delta_max(full_summary["task_runtime"], clipped_summary["task_runtime"])
    task_blocked_delta = _map_delta_max(full_summary["task_blocked"], clipped_summary["task_blocked"])
    core_runtime_delta = _map_delta_max(full_summary["core_runtime"], clipped_summary["core_runtime"])
    core_switch_diff = _map_diff_total(full_summary["core_switch_count"], clipped_summary["core_switch_count"])
    resource_event_diff = _map_diff_total(full_summary["resource_event_count"], clipped_summary["resource_event_count"])
    resource_blocked_delta = _map_delta_max(full_summary["resource_blocked"], clipped_summary["resource_blocked"])
    resource_event_duplicate_ref_keys = (
        full_summary["resource_event_duplicate_ref_key_count"]
        + clipped_summary["resource_event_duplicate_ref_key_count"]
    )
    resource_event_canonicalized = (
        full_summary["resource_event_canonicalized_count"]
        + clipped_summary["resource_event_canonicalized_count"]
    )

    full_alert_refs = _event_refs_from_result_rows(full_snapshot.get("alerts") or [])
    full_alert_refs.update(_event_refs_from_result_rows(full_snapshot.get("diagnoses") or []))
    object_scopes = _object_scopes((full_snapshot.get("alerts") or []) + (full_snapshot.get("diagnoses") or []))
    full_hold = _resource_edge_keys(full_bundle, "hold", filters, full_alert_refs, object_scopes)
    full_wait = _resource_edge_keys(full_bundle, "wait", filters, full_alert_refs, object_scopes)
    clipped_hold = _resource_edge_keys(clipped_bundle, "hold", filters, full_alert_refs, object_scopes)
    clipped_wait = _resource_edge_keys(clipped_bundle, "wait", filters, full_alert_refs, object_scopes)
    missing_hold = _sorted_edge_keys(full_hold - clipped_hold)
    missing_wait = _sorted_edge_keys(full_wait - clipped_wait)
    for edge_key in missing_hold:
        collector.add("resources", {"kind": "missing_hold_edge", "edge": edge_key})
    for edge_key in missing_wait:
        collector.add("resources", {"kind": "missing_wait_edge", "edge": edge_key})
    clipped_problem_edges = clipped_hold | clipped_wait
    edge_ref_missing = [
        item
        for item in clipped_problem_edges
        if item.get("evidence_ref") and item["evidence_ref"] not in ref_index_keys
    ]
    for item in edge_ref_missing:
        collector.add("resources", {"kind": "missing_resource_edge_evidence_ref", "edge": item})
    full_edge_count = len(full_hold | full_wait)
    missing_edge_count = len(missing_hold) + len(missing_wait)
    preservation_rate = 1.0 if full_edge_count == 0 else (full_edge_count - missing_edge_count) / full_edge_count
    pass_value = (
        task_runtime_delta <= tolerance
        and task_blocked_delta <= tolerance
        and core_runtime_delta <= tolerance
        and core_switch_diff == 0
        and resource_event_diff == 0
        and resource_blocked_delta <= tolerance
        and not missing_hold
        and not missing_wait
        and not edge_ref_missing
        and abs(preservation_rate - 1.0) <= tolerance
    )
    return {
        "resource_semantic_completeness_pass": pass_value,
        "task_runtime_total_delta_max": round(task_runtime_delta, 9),
        "task_blocked_total_delta_max": round(task_blocked_delta, 9),
        "core_runtime_total_delta_max": round(core_runtime_delta, 9),
        "core_switch_count_diff_total": core_switch_diff,
        "resource_event_count_diff_total": resource_event_diff,
        "resource_event_duplicate_ref_key_count": resource_event_duplicate_ref_keys,
        "resource_event_canonicalized_count": resource_event_canonicalized,
        "full_resource_event_duplicate_ref_key_count": full_summary["resource_event_duplicate_ref_key_count"],
        "clipped_resource_event_duplicate_ref_key_count": clipped_summary["resource_event_duplicate_ref_key_count"],
        "resource_blocked_total_delta_max": round(resource_blocked_delta, 9),
        "resource_hold_edge_missing_count": len(missing_hold),
        "resource_wait_edge_missing_count": len(missing_wait),
        "resource_edge_evidence_ref_missing_count": len(edge_ref_missing),
        "resource_graph_preservation_rate": round(preservation_rate, 9),
    }


def compare_event_ref_closure(clipped_snapshot: dict[str, Any], collector: "_DiffCollector") -> dict[str, Any]:
    ref_index = clipped_snapshot.get("ref_index") or []
    ref_keys = _ref_index_keys(ref_index)
    required_by_category = collect_required_event_refs_by_category(clipped_snapshot)
    required_refs = set().union(*required_by_category.values()) if required_by_category else set()
    missing_by_category = {
        category: sorted(refs - ref_keys)
        for category, refs in required_by_category.items()
    }
    for category, refs in missing_by_category.items():
        for ref_key in refs:
            collector.add("refs", {"kind": f"missing_{category}", "ref_key": ref_key})
    scope = normalize_comparison_scope(clipped_snapshot.get("comparison_scope") or {})
    window = scope.get("time_window") or [0.0, 0.0]
    out_of_window = []
    for row in ref_index:
        ref_key = row.get("ref_key")
        ts = row.get("timestamp_aligned")
        if ref_key is None or ts is None:
            continue
        if (float(ts) < float(window[0]) or float(ts) > float(window[1])) and str(ref_key) not in required_refs:
            out_of_window.append(str(ref_key))
            collector.add("refs", {"kind": "out_of_window_non_required_event", "ref_key": str(ref_key)})
    meta_required = _meta_required_event_ref_count(clipped_snapshot.get("meta") or {})
    required_count_within_meta = meta_required is None or meta_required >= len(required_refs)
    missing_required = sorted(required_refs - ref_keys)
    return {
        "event_ref_closure_pass": not missing_required and not out_of_window and required_count_within_meta,
        "required_event_ref_count": len(required_refs),
        "meta_required_event_ref_count": meta_required,
        "meta_required_event_ref_count_pass": required_count_within_meta,
        "missing_required_event_ref_count": len(missing_required),
        "missing_task_state_cause_refs": len(missing_by_category["task_state_cause_refs"]),
        "missing_exec_slice_refs": len(missing_by_category["exec_slice_refs"]),
        "missing_resource_graph_refs": len(missing_by_category["resource_graph_refs"]),
        "missing_alert_refs": len(missing_by_category["alert_refs"]),
        "missing_diagnosis_refs": len(missing_by_category["diagnosis_refs"]),
        "missing_anchor_refs": len(missing_by_category["anchor_refs"]),
        "out_of_window_non_required_event_count": len(out_of_window),
    }


def compare_result_validity(
    full_snapshot: dict[str, Any],
    clipped_snapshot: dict[str, Any],
    collector: "_DiffCollector",
) -> dict[str, Any]:
    rows = _result_validity_rows(clipped_snapshot.get("result_validity"))
    has_rows = bool(rows)
    alerts = clipped_snapshot.get("alerts") or []
    diagnoses = clipped_snapshot.get("diagnoses") or []
    non_exact_support = _non_exact_support_level_count(alerts + diagnoses)
    invalid_support = _unexpected_support_level_count(alerts + diagnoses, clipped_snapshot)
    if invalid_support:
        collector.add("resources", {"kind": "invalid_support_level_count", "count": invalid_support})
    conflict_count = _result_validity_conflict_count(rows)
    subset_count = sum(1 for row in rows if str(row.get("validity_scope") or "") == "evidence_subset")
    recomputed_count = sum(1 for row in rows if str(row.get("derivation_mode") or "") == "recomputed_subset")
    source_snapshot_count = sum(1 for row in rows if str(row.get("validity_scope") or "") == "source_snapshot")
    reused_context_count = sum(1 for row in rows if str(row.get("derivation_mode") or "") == "reused_context")
    consumer_mode = _consumer_mode_payload(clipped_snapshot)
    consumer_values = _consumer_mode_values(consumer_mode)
    reference_only = sum(1 for value in consumer_values if value == "REFERENCE_ONLY")
    reject_automation = sum(1 for value in consumer_values if value == "REJECT_AUTOMATION")
    consumer_explain = _consumer_mode_explain_payload(clipped_snapshot)
    consumer_explain_present = bool(consumer_explain)
    consumer_final_matches = _consumer_mode_final_matches(consumer_mode, consumer_explain)
    consumer_unexplained = (
        reference_only + reject_automation
        if (reference_only or reject_automation) and not (consumer_explain_present and consumer_final_matches)
        else 0
    )
    alert_coverage = (
        _result_validity_coverage(rows, "alert", [row.get("alert_id") for row in alerts])
        if has_rows
        else 1.0
    )
    diag_coverage = (
        _result_validity_coverage(rows, "diagnosis", [row.get("diag_id") for row in diagnoses])
        if has_rows
        else 1.0
    )
    pass_value = invalid_support == 0 and conflict_count == 0 and consumer_unexplained == 0
    if has_rows:
        pass_value = pass_value and alert_coverage >= 1.0 and diag_coverage >= 1.0
    strict_pass = (
        pass_value
        and subset_count == 0
        and recomputed_count == 0
        and reference_only == 0
        and reject_automation == 0
        and non_exact_support == 0
    )
    explained_non_exact = (
        pass_value
        and (
            subset_count > 0
            or recomputed_count > 0
            or reference_only > 0
            or reject_automation > 0
            or (non_exact_support > 0 and invalid_support == 0)
        )
    )
    if conflict_count:
        collector.add("resources", {"kind": "result_validity_conflict_count", "count": conflict_count})
    if consumer_unexplained:
        collector.add("resources", {"kind": "consumer_mode_unexplained_count", "count": consumer_unexplained})
    return {
        "result_validity_pass": pass_value,
        "result_validity_strict_pass": strict_pass,
        "result_validity_present": has_rows,
        "result_validity_alert_coverage": round(alert_coverage, 9),
        "result_validity_diag_coverage": round(diag_coverage, 9),
        "result_validity_conflict_count": conflict_count,
        "result_validity_evidence_subset_count": subset_count,
        "result_validity_recomputed_count": recomputed_count,
        "result_validity_source_snapshot_count": source_snapshot_count,
        "result_validity_reused_context_count": reused_context_count,
        "non_exact_support_level_count": non_exact_support,
        "invalid_support_level_count": invalid_support,
        "consumer_mode": serialize(consumer_mode),
        "consumer_mode_reference_only_count": reference_only,
        "consumer_mode_reject_automation_count": reject_automation,
        "consumer_mode_explain_present": consumer_explain_present,
        "consumer_mode_final_matches": consumer_final_matches,
        "consumer_mode_decision_reasons": list((consumer_explain or {}).get("decision_reasons") or []),
        "consumer_mode_blocking_object_count": len(list((consumer_explain or {}).get("blocking_objects") or [])),
        "reference_only_unexplained_count": consumer_unexplained,
        "has_explained_non_exact_results": explained_non_exact,
    }


def export_resource_observation(clipped_snapshot: dict[str, Any]) -> dict[str, Any]:
    observation = dict(clipped_snapshot.get("export_resource_observation") or {})
    timings = dict(observation.get("write_timings") or {})
    manifest_entries = clipped_snapshot.get("manifest", {}).get("entries") or []
    manifest_timings: dict[str, float] = {}
    for entry in manifest_entries:
        for key, value in dict(entry.get("write_timings") or {}).items():
            try:
                manifest_timings[key] = manifest_timings.get(key, 0.0) + float(value)
            except (TypeError, ValueError):
                continue
    for key, value in manifest_timings.items():
        timings.setdefault(key, value)
    write_seconds = float(observation.get("write_seconds", 0.0) or 0.0)
    if write_seconds <= 0.0:
        write_seconds = sum(
            float(timings.get(key, 0.0) or 0.0)
            for key in (
                "prepare_seconds",
                "encode_trace_seconds",
                "json_dump_seconds",
                "csv_write_seconds",
                "checksum_seconds",
                "reference_copy_seconds",
            )
        )
    normalize_seconds = float(observation.get("normalize_seconds", 0.0) or 0.0)
    python_peak_alloc_mb = observation.get("python_peak_alloc_mb")
    os_peak = observation.get("os_peak", {})
    os_peak_supported = bool(observation.get("os_peak_supported", False))
    required_fields_present = (
        write_seconds > 0.0
        and normalize_seconds > 0.0
        and _positive_number(python_peak_alloc_mb)
        and isinstance(os_peak, dict)
        and (not os_peak_supported or _positive_number(os_peak.get("ru_maxrss")))
    )
    return {
        "export_resource_observation_pass": required_fields_present,
        "export_clipped_write_seconds": write_seconds,
        "export_clipped_normalize_seconds": normalize_seconds,
        "export_clipped_prepare_seconds": float(timings.get("prepare_seconds", 0.0) or 0.0),
        "export_clipped_encode_trace_seconds": float(timings.get("encode_trace_seconds", 0.0) or 0.0),
        "export_clipped_json_dump_seconds": float(timings.get("json_dump_seconds", 0.0) or 0.0),
        "export_clipped_checksum_seconds": float(timings.get("checksum_seconds", 0.0) or 0.0),
        "python_peak_alloc_mb": python_peak_alloc_mb,
        "os_peak": os_peak,
        "os_peak_supported": os_peak_supported,
    }


def collect_required_event_refs_by_category(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    bundle = snapshot.get("rebuild_bundle") or {}
    graph = bundle.get("resource_graph") or {}
    required = {
        "task_state_cause_refs": set(),
        "exec_slice_refs": set(),
        "resource_graph_refs": set(),
        "alert_refs": set(),
        "diagnosis_refs": set(),
        "anchor_refs": set(),
    }
    for row in bundle.get("task_states") or []:
        ref_key = _normalize_ref_key(row.get("cause_event"))
        if ref_key:
            required["task_state_cause_refs"].add(ref_key)
    for row in bundle.get("exec_slices") or []:
        for key in ("start_event", "end_event"):
            ref_key = _normalize_ref_key(row.get(key))
            if ref_key:
                required["exec_slice_refs"].add(ref_key)
    for edge in list(graph.get("hold_edges") or []) + list(graph.get("wait_edges") or []):
        ref_key = _normalize_ref_key(edge.get("evidence_ref"))
        if ref_key:
            required["resource_graph_refs"].add(ref_key)
    required["alert_refs"].update(_event_refs_from_result_rows(snapshot.get("alerts") or []))
    required["diagnosis_refs"].update(_event_refs_from_result_rows(snapshot.get("diagnoses") or []))
    for row in snapshot.get("anchors") or []:
        ref_key = event_ref_key_from_payload((row or {}).get("evidence_anchor"))
        if ref_key:
            required["anchor_refs"].add(ref_key)
    context_ref = event_ref_key_from_payload((snapshot.get("analysis_context") or {}).get("evidence_anchor"))
    if context_ref:
        required["anchor_refs"].add(context_ref)
    return required


def render_summary(report: dict[str, Any], *, rerun_command: str | None = None) -> str:
    lines = [
        "# Clipped Trace Completeness Summary",
        "",
        f"- status: {report.get('status')}",
        f"- generated_at: {report.get('generated_at')}",
        "",
        "## Core Gates",
    ]
    gates = [
        ("scope", report.get("comparison_scope", {}).get("scope_identity_pass")),
        ("alerts", report.get("alert_completeness", {}).get("alert_completeness_pass")),
        ("diagnoses", report.get("diagnosis_completeness", {}).get("diagnosis_completeness_pass")),
        ("metrics", report.get("metric_completeness", {}).get("metric_completeness_pass")),
        ("resources", report.get("resource_semantic_completeness", {}).get("resource_semantic_completeness_pass")),
        ("event_refs", report.get("event_ref_closure", {}).get("event_ref_closure_pass")),
        ("result_validity", report.get("result_validity", {}).get("result_validity_pass")),
        ("export_resource_observation", report.get("export_resource_observation", {}).get("export_resource_observation_pass")),
    ]
    lines.extend(f"- {name}: {value}" for name, value in gates)
    lines.extend(["", "## Key Counts"])
    key_counts = {
        "alert_missing": report.get("alert_completeness", {}).get("alert_id_missing_count"),
        "diagnosis_missing": report.get("diagnosis_completeness", {}).get("diag_id_missing_count"),
        "metric_diff": report.get("metric_completeness", {}).get("metric_diff_count"),
        "resource_hold_missing": report.get("resource_semantic_completeness", {}).get("resource_hold_edge_missing_count"),
        "resource_wait_missing": report.get("resource_semantic_completeness", {}).get("resource_wait_edge_missing_count"),
        "missing_refs": report.get("event_ref_closure", {}).get("missing_required_event_ref_count"),
    }
    lines.extend(f"- {key}: {value}" for key, value in key_counts.items())
    diff_samples = report.get("diff_samples") or {}
    if any(diff_samples.get(key) for key in ("alerts", "diagnoses", "metrics", "resources", "refs")):
        lines.extend(["", "## Diff Samples"])
        for section in ("alerts", "diagnoses", "metrics", "resources", "refs"):
            rows = diff_samples.get(section) or []
            if rows:
                lines.append(f"- {section}: {rows[:3]}")
    if rerun_command:
        lines.extend(["", "## Rerun", "", f"```bash\n{rerun_command}\n```"])
    lines.append("")
    return "\n".join(lines)


def _keyed_rows(rows: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row[key]): row for row in rows if isinstance(row, dict) and row.get(key) is not None}


def _ref_index_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("ref_key")) for row in rows if row.get("ref_key")}


def _normalize_ref_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _event_refs_from_result_rows(rows: Iterable[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for row in rows:
        for evidence_ref in (row or {}).get("evidence_refs") or []:
            ref_key = event_ref_key_from_payload(evidence_ref)
            if ref_key:
                refs.add(ref_key)
    return refs


def _event_refs_from_any(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref_key = event_ref_key_from_payload(value)
        if ref_key:
            refs.add(ref_key)
        for item in value.values():
            refs.update(_event_refs_from_any(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_event_refs_from_any(item))
    return refs


def _time_window_delta(left: Any, right: Any) -> float:
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
        return 0.0 if left == right else float("inf")
    if len(left) < 2 or len(right) < 2:
        return 0.0 if left == right else float("inf")
    return max(abs(float(left[0]) - float(right[0])), abs(float(left[1]) - float(right[1])))


def _unexpected_support_level_count(rows: list[dict[str, Any]], snapshot: dict[str, Any]) -> int:
    if _has_result_explanation(snapshot):
        return 0
    return _non_exact_support_level_count(rows)


def _non_exact_support_level_count(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        support = str((row or {}).get("support_level") or "exact").strip().lower()
        if support and support != "exact":
            count += 1
    return count


def _has_result_explanation(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("result_validity"):
        return True
    meta = snapshot.get("meta") or {}
    return _consumer_mode_payload(snapshot) is not None or bool(meta.get("consumer_mode_explain")) or bool(
        snapshot.get("consumer_mode_explain")
    )


def _map_delta_max(left: dict[Any, float], right: dict[Any, float]) -> float:
    keys = set(left) | set(right)
    return max((abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) for key in keys), default=0.0)


def _map_diff_total(left: dict[Any, int], right: dict[Any, int]) -> int:
    keys = set(left) | set(right)
    return int(sum(abs(int(left.get(key, 0)) - int(right.get(key, 0))) for key in keys))


def _resource_summary(bundle: dict[str, Any], window: tuple[float, float], filters: dict[str, Any]) -> dict[str, Any]:
    t_begin, t_end = float(window[0]), float(window[1])
    task_runtime: dict[int, float] = {}
    core_runtime: dict[int, float] = {}
    for row in bundle.get("exec_slices") or []:
        if not _matches_filter(row, filters):
            continue
        duration = _overlap_duration(row.get("t_begin"), row.get("t_end"), t_begin, t_end)
        if duration <= 0.0:
            continue
        task_id = row.get("task_id")
        core_id = row.get("core_id")
        if task_id is not None:
            task_runtime[int(task_id)] = task_runtime.get(int(task_id), 0.0) + duration
        if core_id is not None:
            core_runtime[int(core_id)] = core_runtime.get(int(core_id), 0.0) + duration
    task_blocked: dict[int, float] = {}
    resource_blocked: dict[int, float] = {}
    for row in bundle.get("task_states") or []:
        if str(row.get("state")) != "BLOCKED" or not _matches_filter(row, filters):
            continue
        duration = _overlap_duration(row.get("t_begin"), row.get("t_end"), t_begin, t_end)
        if duration <= 0.0:
            continue
        task_id = row.get("task_id")
        related_obj = row.get("related_obj")
        if task_id is not None:
            task_blocked[int(task_id)] = task_blocked.get(int(task_id), 0.0) + duration
        if related_obj is not None:
            resource_blocked[int(related_obj)] = resource_blocked.get(int(related_obj), 0.0) + duration
    canonical_rows, duplicate_ref_key_count, canonicalized_count = _canonical_event_rows_for_resource_summary(
        bundle,
        t_begin,
        t_end,
        filters,
    )
    core_switch_count: dict[int, int] = {}
    resource_event_count: dict[int, int] = {}
    for row in canonical_rows:
        if row.get("event_name") == "CTX_SWITCH" and row.get("core_id") is not None:
            core_id = int(row["core_id"])
            core_switch_count[core_id] = core_switch_count.get(core_id, 0) + 1
        resource_id = _resource_id_from_event(row)
        if resource_id is not None:
            resource_event_count[resource_id] = resource_event_count.get(resource_id, 0) + 1
    return {
        "task_runtime": task_runtime,
        "task_blocked": task_blocked,
        "core_runtime": core_runtime,
        "core_switch_count": core_switch_count,
        "resource_event_count": resource_event_count,
        "resource_event_duplicate_ref_key_count": duplicate_ref_key_count,
        "resource_event_canonicalized_count": canonicalized_count,
        "resource_blocked": resource_blocked,
    }


def _canonical_event_rows_for_resource_summary(
    bundle: dict[str, Any],
    t_begin: float,
    t_end: float,
    filters: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, int]:
    rows_without_ref: list[dict[str, Any]] = []
    row_by_ref_key: dict[str, dict[str, Any]] = {}
    duplicate_ref_keys: set[str] = set()
    canonicalized_count = 0
    for row in bundle.get("event_stream") or []:
        ts = row.get("timestamp_aligned")
        if ts is None or not (t_begin <= float(ts) <= t_end) or not _matches_filter(row, filters):
            continue
        ref_key = _resource_event_ref_key(row)
        if not ref_key:
            rows_without_ref.append(row)
            continue
        if ref_key in row_by_ref_key:
            duplicate_ref_keys.add(ref_key)
            canonicalized_count += 1
        row_by_ref_key[ref_key] = row
    return list(row_by_ref_key.values()) + rows_without_ref, len(duplicate_ref_keys), canonicalized_count


def _overlap_duration(begin: Any, end: Any, window_begin: float, window_end: float) -> float:
    if begin is None or end is None:
        return 0.0
    return max(0.0, min(float(end), window_end) - max(float(begin), window_begin))


def _matches_filter(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True
    if filters.get("task_id") is not None:
        task_id = filters.get("task_id")
        if row.get("task_id") != task_id and row.get("delayed_task") != task_id:
            payload = row.get("payload") or {}
            if payload.get("task_id") != task_id and payload.get("next_task_id") != task_id and payload.get("prev_task_id") != task_id:
                return False
    if filters.get("core_id") is not None and row.get("core_id") != filters.get("core_id"):
        payload = row.get("payload") or {}
        if payload.get("core_id") != filters.get("core_id"):
            return False
    if filters.get("event_name") is not None and row.get("event_name") != filters.get("event_name"):
        return False
    resource_filter = filters.get("resource_id", filters.get("obj_id"))
    if resource_filter is not None:
        resource_id = _resource_id_from_event(row)
        if row.get("obj_id") != resource_filter and row.get("related_obj") != resource_filter and resource_id != resource_filter:
            return False
    if filters.get("irq_id") is not None and row.get("irq_id") != filters.get("irq_id"):
        payload = row.get("payload") or {}
        if payload.get("irq_id") != filters.get("irq_id"):
            return False
    return True


def _resource_id_from_event(row: dict[str, Any]) -> int | None:
    payload = row.get("payload") or {}
    for key in ("obj_id", "wait_obj_id", "resource_id"):
        value = row.get(key, payload.get(key))
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _resource_event_ref_key(row: dict[str, Any]) -> str | None:
    ref_key = row.get("ref_key")
    if ref_key is None:
        return None
    text = str(ref_key).strip()
    return text or None


def _normalized_time_bounds(row: dict[str, Any]) -> list[float] | None:
    begin = row.get("t_begin", row.get("begin"))
    end = row.get("t_end", row.get("end"))
    if begin is None or end is None:
        return None
    try:
        return [round(float(begin), 6), round(float(end), 6)]
    except (TypeError, ValueError):
        return None


def _object_scopes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row.get("object_scope") or {})
        for row in rows
        if isinstance(row.get("object_scope"), dict) and _object_scope_targets_resource_edge(row.get("object_scope") or {})
    ]


def _object_scope_targets_resource_edge(scope: dict[str, Any]) -> bool:
    return any(scope.get(key) is not None for key in ("task_id", "owner_task_id", "resource_id", "obj_id"))


def _resource_edge_keys(
    bundle: dict[str, Any],
    edge_kind: str,
    filters: dict[str, Any],
    problem_event_refs: set[str],
    object_scopes: list[dict[str, Any]],
) -> set[dict[str, Any]]:
    graph = bundle.get("resource_graph") or {}
    rows = graph.get("hold_edges" if edge_kind == "hold" else "wait_edges") or []
    keys: set[HashableDict] = set()
    for row in rows:
        if not _edge_matches_filter(row, filters):
            continue
        if problem_event_refs or object_scopes:
            evidence_ref = str(row.get("evidence_ref") or "")
            if evidence_ref not in problem_event_refs and not _edge_matches_object_scopes(row, object_scopes):
                continue
        owner_task_id = row.get("owner_task_id", row.get("owner_task"))
        key = HashableDict(
            {
                "edge_kind": edge_kind,
                "task_id": row.get("task_id", row.get("from_task")),
                "owner_task_id": owner_task_id,
                "obj_id": row.get("obj_id", row.get("to_obj")),
                "evidence_ref": row.get("evidence_ref"),
            }
        )
        bounds = _normalized_time_bounds(row)
        if bounds is not None:
            key["normalized_time_bounds"] = bounds
        keys.add(key)
    return keys


def _sorted_edge_keys(edges: set["HashableDict"]) -> list["HashableDict"]:
    return sorted(
        edges,
        key=lambda item: (
            str(item.get("edge_kind")),
            str(item.get("task_id")),
            str(item.get("owner_task_id")),
            str(item.get("obj_id")),
            str(item.get("evidence_ref")),
            str(item.get("normalized_time_bounds", "")),
        ),
    )


def _edge_matches_filter(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True
    if filters.get("task_id") is not None:
        task_id = filters.get("task_id")
        if row.get("task_id") != task_id and row.get("owner_task_id") != task_id and row.get("owner_task") != task_id:
            return False
    resource_filter = filters.get("resource_id", filters.get("obj_id"))
    if resource_filter is not None and row.get("obj_id", row.get("to_obj")) != resource_filter:
        return False
    return True


def _edge_matches_object_scopes(row: dict[str, Any], scopes: list[dict[str, Any]]) -> bool:
    for scope in scopes:
        if scope.get("task_id") is not None and row.get("task_id") == scope.get("task_id"):
            return True
        if scope.get("owner_task_id") is not None and row.get("owner_task_id", row.get("owner_task")) == scope.get("owner_task_id"):
            return True
        resource_id = scope.get("resource_id", scope.get("obj_id"))
        if resource_id is not None and row.get("obj_id", row.get("to_obj")) == resource_id:
            return True
    return False


def _meta_required_event_ref_count(meta: dict[str, Any]) -> int | None:
    padding = meta.get("context_padding_rule") or {}
    if "required_event_ref_count" not in padding:
        return None
    try:
        return int(padding.get("required_event_ref_count"))
    except (TypeError, ValueError):
        return -1


def _result_validity_rows(rows: Any) -> list[dict[str, Any]]:
    if not rows:
        return []
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("rows") or rows.get("items") or []
    return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _result_validity_conflict_count(rows: Any) -> int:
    normalized_rows = _result_validity_rows(rows)
    count = 0
    seen: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in normalized_rows:
        conflict = row.get("conflict")
        if conflict is True or (isinstance(conflict, dict) and conflict.get("present")):
            count += 1
        object_key = (str(row.get("object_kind") or ""), str(row.get("object_id") or row.get("result_id") or ""))
        if not object_key[0] or not object_key[1]:
            continue
        variants = seen.setdefault(object_key, set())
        variants.add((str(row.get("validity_scope") or ""), str(row.get("derivation_mode") or "")))
    count += sum(1 for variants in seen.values() if len(variants) > 1)
    return count


def _consumer_mode_payload(snapshot: dict[str, Any]) -> Any | None:
    if snapshot.get("consumer_mode") is not None:
        return snapshot.get("consumer_mode")
    meta = snapshot.get("meta") or {}
    if meta.get("consumer_mode") is not None:
        return meta.get("consumer_mode")
    return None


def _consumer_mode_explain_payload(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    payload = snapshot.get("consumer_mode_explain")
    if isinstance(payload, dict):
        return payload
    meta = snapshot.get("meta") or {}
    payload = meta.get("consumer_mode_explain")
    return payload if isinstance(payload, dict) else None


def _consumer_mode_values(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, dict):
        return [str(value).strip().upper() for value in payload.values()]
    return [str(payload).strip().upper()]


def _consumer_mode_final_matches(consumer_mode: Any, explain: dict[str, Any] | None) -> bool:
    if not explain:
        return True
    if "final_mode" not in explain:
        return False
    return normalize_payload(explain.get("final_mode")) == normalize_payload(consumer_mode)


def _result_validity_coverage(rows: Any, object_kind: str, ids: list[Any]) -> float:
    ids_set = {str(item) for item in ids if item is not None}
    if not ids_set:
        return 1.0
    normalized_rows = _result_validity_rows(rows)
    if not normalized_rows:
        return 0.0
    covered = set()
    for row in normalized_rows:
        if row.get("object_kind") is not None and str(row.get("object_kind")) != object_kind:
            continue
        for key in ("object_id", "result_id"):
            if row.get(key) is not None and str(row.get(key)) in ids_set:
                covered.add(str(row.get(key)))
    return len(covered) / len(ids_set)


class HashableDict(dict):
    def __hash__(self) -> int:
        return hash(tuple(sorted((key, _freeze_for_hash(normalize_payload(value))) for key, value in self.items())))


def _freeze_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze_for_hash(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_for_hash(item) for item in value)
    return value


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0.0
    except (TypeError, ValueError):
        return False


def _bounded_pass_allowed(
    *,
    strict_pass: bool,
    scope_report: dict[str, Any],
    alert_report: dict[str, Any],
    diagnosis_report: dict[str, Any],
    metric_report: dict[str, Any],
    resource_report: dict[str, Any],
    ref_report: dict[str, Any],
    result_validity: dict[str, Any],
    export_observation: dict[str, Any],
) -> bool:
    if strict_pass:
        return False
    return all(
        [
            scope_report["scope_identity_pass"],
            alert_report["alert_id_missing_count"] == 0,
            alert_report["alert_id_extra_count"] == 0,
            alert_report["alert_evidence_ref_missing_count"] == 0,
            alert_report["alert_unexplained_diff_count"] == 0,
            diagnosis_report["diag_id_missing_count"] == 0,
            diagnosis_report["diag_id_extra_count"] == 0,
            diagnosis_report["diag_evidence_ref_missing_count"] == 0,
            diagnosis_report["diag_unexplained_diff_count"] == 0,
            metric_report["metric_completeness_pass"],
            resource_report["resource_semantic_completeness_pass"],
            ref_report["missing_required_event_ref_count"] == 0,
            ref_report["out_of_window_non_required_event_count"] == 0,
            result_validity["result_validity_pass"],
            result_validity["has_explained_non_exact_results"],
            export_observation["export_resource_observation_pass"],
        ]
    )


class _DiffCollector:
    def __init__(self, limit: int) -> None:
        self.limit = max(int(limit), 0)
        self._rows: dict[str, list[dict[str, Any]]] = {
            "alerts": [],
            "diagnoses": [],
            "metrics": [],
            "resources": [],
            "refs": [],
        }

    def add(self, section: str, row: dict[str, Any]) -> None:
        if section not in self._rows:
            section = "resources"
        if len(self._rows[section]) >= self.limit:
            return
        self._rows[section].append(serialize(copy.deepcopy(row)))

    def payload(self) -> dict[str, list[dict[str, Any]]]:
        return copy.deepcopy(self._rows)

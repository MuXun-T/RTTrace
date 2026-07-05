from __future__ import annotations

from pathlib import Path
from typing import Any

from parser.evidence_sidecar import validate_sidecar_segment_manifest_metadata
from parser.evidence_models import evd_RecomputeProofHash
from parser.result import Result, err_result, ok_result
from spec.io import checksum_file, json_load, jsonl_load
from spec.schema_loader import load_specs
from spec.schema_validator import validate_schema

_BUDGET_HALT_REASONS = {"DEPTH_LIMIT", "EVENT_LIMIT", "BYTE_LIMIT", "RHO_LIMIT"}
_CONSUMER_MODE_KEYS = ("compare", "replay", "audit")
_RESULT_VALIDITY_ALERT_KIND = "alert"
_RESULT_VALIDITY_DIAG_KIND = "diagnosis"
_RESULT_VALIDITY_SCOPE_SNAPSHOT = "source_snapshot"
_RESULT_VALIDITY_SCOPE_SUBSET = "evidence_subset"
_RESULT_VALIDITY_DERIVATION_REUSED = "reused_context"
_RESULT_VALIDITY_DERIVATION_RECOMPUTED = "recomputed_subset"


def _is_evidence_package(meta: dict[str, Any], manifest: dict[str, Any]) -> bool:
    return str(meta.get("export_family") or "").strip().lower() == "evidence" or manifest.get("package_version") == "rttrace-package-2"


def _frontier_row_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        -int(row.get("priority", 0)),
        int(row.get("time_hint_begin_ns", 0)),
        str(row.get("ref_key", "")),
        str(row.get("frontier_origin_rule", "")),
    )


def _manifest_entry_paths(manifest: dict[str, Any]) -> set[str]:
    return {str((entry or {}).get("path") or "") for entry in list(manifest.get("entries") or []) if str((entry or {}).get("path") or "")}


def _strict_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("bool is not integer")
    return int(value)


def _strict_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("bool is not number")
    return float(value)


def _safe_hit_rate(target_count: int, matched_count: int) -> float:
    if target_count <= 0:
        return 1.0
    return float(matched_count / target_count)


def _normalize_result_validity_rows(result_validity: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = list(dict(result_validity or {}).get("results") or [])
    normalized: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "row_index": int(row_index),
                "path": str(row.get("path") or "").strip(),
                "category": str(row.get("category") or "").strip(),
                "object_kind": str(row.get("object_kind") or "").strip().lower(),
                "object_id": str(row.get("object_id") or "").strip(),
                "validity_scope": str(row.get("validity_scope") or "").strip().lower(),
                "derivation_mode": str(row.get("derivation_mode") or "").strip().lower(),
                "notes": row.get("notes"),
            }
        )
    return normalized


def _result_validity_object_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("object_kind") or ""), str(row.get("object_id") or ""))


def _detect_result_validity_conflicts(
    by_object: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for object_key in sorted(by_object.keys()):
        rows = list(by_object.get(object_key) or [])
        if not rows:
            continue
        variants = sorted(
            {
                (str(item.get("validity_scope") or ""), str(item.get("derivation_mode") or ""))
                for item in rows
            }
        )
        if len(variants) <= 1:
            continue
        conflicts.append(
            {
                "object_kind": object_key[0],
                "object_id": object_key[1],
                "row_indices": sorted({int(item.get("row_index", -1)) for item in rows}),
                "variants": [
                    {"validity_scope": scope, "derivation_mode": derivation}
                    for scope, derivation in variants
                ],
                "reason": "inconsistent_validity_semantics",
            }
        )
    return conflicts


def _summarize_result_validity(
    rows: list[dict[str, Any]],
    by_object: dict[tuple[str, str], list[dict[str, Any]]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    source_snapshot_count = 0
    evidence_subset_count = 0
    reused_context_count = 0
    recomputed_subset_count = 0
    for row in rows:
        if str(row.get("validity_scope") or "") == _RESULT_VALIDITY_SCOPE_SNAPSHOT:
            source_snapshot_count += 1
        if str(row.get("validity_scope") or "") == _RESULT_VALIDITY_SCOPE_SUBSET:
            evidence_subset_count += 1
        if str(row.get("derivation_mode") or "") == _RESULT_VALIDITY_DERIVATION_REUSED:
            reused_context_count += 1
        if str(row.get("derivation_mode") or "") == _RESULT_VALIDITY_DERIVATION_RECOMPUTED:
            recomputed_subset_count += 1
    return {
        "row_count": len(rows),
        "object_count": len(by_object),
        "source_snapshot_count": source_snapshot_count,
        "evidence_subset_count": evidence_subset_count,
        "reused_context_count": reused_context_count,
        "recomputed_subset_count": recomputed_subset_count,
        "conflict_count": len(conflicts),
        "empty": len(rows) == 0,
    }


def rpr_BuildResultValidityIndex(result_validity: dict[str, Any] | None) -> dict[str, Any]:
    rows = _normalize_result_validity_rows(result_validity)
    by_object: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_alert_id: dict[str, list[dict[str, Any]]] = {}
    by_diag_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        object_key = _result_validity_object_key(row)
        by_object.setdefault(object_key, []).append(row)
        object_kind = object_key[0]
        object_id = object_key[1]
        if object_kind == _RESULT_VALIDITY_ALERT_KIND:
            by_alert_id.setdefault(object_id, []).append(row)
        elif object_kind == _RESULT_VALIDITY_DIAG_KIND:
            by_diag_id.setdefault(object_id, []).append(row)
    conflicts = _detect_result_validity_conflicts(by_object)
    summary = _summarize_result_validity(rows, by_object, conflicts)
    return {
        "rows": rows,
        "by_object": by_object,
        "by_alert_id": by_alert_id,
        "by_diag_id": by_diag_id,
        "conflicts": conflicts,
        "summary": summary,
    }


def _reject_automation_mode() -> dict[str, str]:
    return {
        "compare": "REJECT_AUTOMATION",
        "replay": "REJECT_AUTOMATION",
        "audit": "REJECT_AUTOMATION",
    }


def _assess_consumer_mode_base(verification: dict[str, Any], proof_digest: dict[str, Any]) -> dict[str, str]:
    if not verification.get("ok", False):
        return _reject_automation_mode()

    closure_mode = str(proof_digest.get("closure_mode") or "")
    missing_required_refs = int(proof_digest.get("missing_required_refs", 0))
    complete = bool(proof_digest.get("complete_wrt_rule_family", False))

    if closure_mode == "exact" and complete and missing_required_refs == 0:
        return {"compare": "ALLOW_COMPARE", "replay": "ALLOW_COMPARE", "audit": "ALLOW_COMPARE"}
    if closure_mode == "bounded" and str(proof_digest.get("frontier_halt_reason") or "") in _BUDGET_HALT_REASONS:
        return {"compare": "REFERENCE_ONLY", "replay": "REFERENCE_ONLY", "audit": "REFERENCE_ONLY"}
    if closure_mode == "degraded":
        return {"compare": "REFERENCE_ONLY", "replay": "REJECT_AUTOMATION", "audit": "REFERENCE_ONLY"}
    return _reject_automation_mode()


def _consumer_mode_with_explain(
    verification: dict[str, Any],
    proof_digest: dict[str, Any],
    result_validity: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    base_mode = _assess_consumer_mode_base(verification, proof_digest)
    final_mode = dict(base_mode)
    validity_index = rpr_BuildResultValidityIndex(result_validity)
    summary = dict(validity_index.get("summary") or {})
    conflicts = list(validity_index.get("conflicts") or [])
    rows = list(validity_index.get("rows") or [])
    decision_reasons: list[str] = []
    blocking_objects: list[dict[str, Any]] = []

    if not verification.get("ok", False):
        final_mode = _reject_automation_mode()
        decision_reasons.append("verification_failed")
    elif conflicts:
        final_mode = _reject_automation_mode()
        decision_reasons.append("validity_conflict")
        blocking_objects = [
            {
                "object_kind": str(item.get("object_kind") or ""),
                "object_id": str(item.get("object_id") or ""),
                "row_indices": list(item.get("row_indices") or []),
            }
            for item in conflicts
        ]
    else:
        subset_or_recomputed_rows = [
            row
            for row in rows
            if str(row.get("validity_scope") or "") == _RESULT_VALIDITY_SCOPE_SUBSET
            or str(row.get("derivation_mode") or "") == _RESULT_VALIDITY_DERIVATION_RECOMPUTED
        ]
        if subset_or_recomputed_rows:
            if any(str(row.get("validity_scope") or "") == _RESULT_VALIDITY_SCOPE_SUBSET for row in subset_or_recomputed_rows):
                decision_reasons.append("subset_scope_present")
            if any(
                str(row.get("derivation_mode") or "") == _RESULT_VALIDITY_DERIVATION_RECOMPUTED
                for row in subset_or_recomputed_rows
            ):
                decision_reasons.append("recomputed_subset_present")
            for key in _CONSUMER_MODE_KEYS:
                if final_mode.get(key) == "ALLOW_COMPARE":
                    final_mode[key] = "REFERENCE_ONLY"
            unique_objects: dict[tuple[str, str], dict[str, Any]] = {}
            for row in subset_or_recomputed_rows:
                object_key = _result_validity_object_key(row)
                if object_key not in unique_objects:
                    unique_objects[object_key] = {
                        "object_kind": object_key[0],
                        "object_id": object_key[1],
                    }
            blocking_objects = [unique_objects[key] for key in sorted(unique_objects.keys())]

    explain = {
        "base_mode": dict(base_mode),
        "final_mode": dict(final_mode),
        "result_validity_summary": summary,
        "decision_reasons": decision_reasons,
        "blocking_objects": blocking_objects,
        "conflicts": conflicts,
    }
    return final_mode, explain


def rpr_VerifyProofBundle(
    proof_digest: dict[str, Any],
    frontier_snapshot: dict[str, Any],
    frontier_refs: list[dict[str, Any]] | None = None,
    blocker_artifact: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    manifest_entries = _manifest_entry_paths(dict(manifest or {}))
    control_refs = dict((meta or {}).get("control_refs") or {})
    recomputed_proof_hash = evd_RecomputeProofHash(proof_digest)
    if str(proof_digest.get("proof_hash") or "") != recomputed_proof_hash:
        issues.append("proof_hash mismatch")

    snapshot_id = str(proof_digest.get("snapshot_id") or "")
    if meta is not None and str(meta.get("snapshot_id") or "") != snapshot_id:
        issues.append("snapshot_id mismatch with meta")
    if manifest is not None and str(manifest.get("snapshot_id") or "") != snapshot_id:
        issues.append("snapshot_id mismatch with manifest")

    if str(frontier_snapshot.get("closure_mode") or "") != str(proof_digest.get("closure_mode") or ""):
        issues.append("frontier_snapshot closure_mode mismatch")
    if str(frontier_snapshot.get("halt_reason") or "") != str(proof_digest.get("frontier_halt_reason") or ""):
        issues.append("frontier halt reason mismatch")
    if int(frontier_snapshot.get("truncated_frontier_count", 0)) != int(proof_digest.get("truncated_frontier_count", 0)):
        issues.append("frontier truncation mismatch")
    if "round_count" in proof_digest:
        try:
            digest_round_count = _strict_int(proof_digest.get("round_count"))
            if digest_round_count < 0:
                issues.append("proof_digest.round_count out of range")
            if digest_round_count != int(proof_digest.get("closure_depth_reached", digest_round_count)):
                issues.append("proof_digest.round_count mismatch closure_depth_reached")
            if digest_round_count != int(frontier_snapshot.get("round_id", digest_round_count)):
                issues.append("proof_digest.round_count mismatch frontier_snapshot.round_id")
        except (TypeError, ValueError):
            issues.append("proof_digest.round_count invalid")
    digest_window_hit_rate: float | None = None
    if "window_hit_rate" in proof_digest:
        try:
            digest_window_hit_rate = _strict_float(proof_digest.get("window_hit_rate"))
            if digest_window_hit_rate < 0.0 or digest_window_hit_rate > 1.0:
                issues.append("proof_digest.window_hit_rate out of range")
        except (TypeError, ValueError):
            issues.append("proof_digest.window_hit_rate invalid")
    if "peak_rss_mb" in proof_digest:
        try:
            peak_rss_mb = proof_digest.get("peak_rss_mb")
            if peak_rss_mb is not None:
                digest_peak_rss_mb = _strict_float(peak_rss_mb)
                if digest_peak_rss_mb < 0.0:
                    issues.append("proof_digest.peak_rss_mb out of range")
        except (TypeError, ValueError):
            issues.append("proof_digest.peak_rss_mb invalid")

    frontier_round_id = int(frontier_snapshot.get("round_id", 0))
    freeze_round_id: int | None = None
    if "freeze_round_id" in frontier_snapshot:
        try:
            freeze_value = frontier_snapshot.get("freeze_round_id")
            if freeze_value is None:
                issues.append("frontier_snapshot.freeze_round_id invalid")
            else:
                freeze_round_id = _strict_int(freeze_value)
                if freeze_round_id < frontier_round_id:
                    issues.append("frontier_snapshot.freeze_round_id below round_id")
        except (TypeError, ValueError):
            issues.append("frontier_snapshot.freeze_round_id invalid")
    pre_read_reject: bool | None = None
    if "pre_read_reject" in frontier_snapshot:
        raw_flag = frontier_snapshot.get("pre_read_reject")
        if not isinstance(raw_flag, bool):
            issues.append("frontier_snapshot.pre_read_reject invalid")
        else:
            pre_read_reject = raw_flag
    pre_read_reject_round_id: int | None = None
    if "pre_read_reject_round_id" in frontier_snapshot:
        raw_reject_round = frontier_snapshot.get("pre_read_reject_round_id")
        if raw_reject_round is None:
            pre_read_reject_round_id = None
        else:
            try:
                pre_read_reject_round_id = _strict_int(raw_reject_round)
                if pre_read_reject_round_id < 0:
                    issues.append("frontier_snapshot.pre_read_reject_round_id out of range")
            except (TypeError, ValueError):
                issues.append("frontier_snapshot.pre_read_reject_round_id invalid")
    if pre_read_reject is not None:
        expected_pre_read_reject = bool(
            str(proof_digest.get("frontier_halt_reason") or "") in _BUDGET_HALT_REASONS
            and int(frontier_snapshot.get("frontier_count", 0)) > 0
            and str(proof_digest.get("closure_mode") or "") in {"bounded", "degraded"}
        )
        if pre_read_reject != expected_pre_read_reject:
            issues.append("frontier_snapshot.pre_read_reject mismatch")
        if pre_read_reject and pre_read_reject_round_id is None:
            issues.append("frontier_snapshot.pre_read_reject_round_id missing")
        if not pre_read_reject and pre_read_reject_round_id is not None:
            issues.append("frontier_snapshot.pre_read_reject_round_id unexpected")
        if freeze_round_id is not None:
            expected_freeze_round_id = frontier_round_id + (1 if pre_read_reject else 0)
            if freeze_round_id != expected_freeze_round_id:
                issues.append("frontier_snapshot.freeze_round_id mismatch")
    if freeze_round_id is not None and pre_read_reject_round_id is not None and freeze_round_id != pre_read_reject_round_id:
        issues.append("frontier_snapshot reject/freeze round mismatch")

    frontier_refs_path = frontier_snapshot.get("frontier_refs_path")
    control_frontier_refs = control_refs.get("frontier_refs")
    if frontier_refs_path and control_frontier_refs and str(frontier_refs_path) != str(control_frontier_refs):
        issues.append("frontier_refs path mismatch with meta.control_refs")
    if frontier_refs_path and manifest is not None and str(frontier_refs_path) not in manifest_entries:
        issues.append("frontier_refs path missing from manifest")
    requires_frontier_refs = int(proof_digest.get("truncated_frontier_count", 0)) > 0 or bool(frontier_refs_path)
    has_frontier_refs = bool(frontier_refs)
    if requires_frontier_refs and not has_frontier_refs:
        issues.append("frontier_refs required but missing")
    if not requires_frontier_refs and has_frontier_refs:
        issues.append("frontier_refs present without proof requirement")
    if has_frontier_refs:
        ordered_frontier_refs = sorted(list(frontier_refs or []), key=_frontier_row_sort_key)
        if ordered_frontier_refs != list(frontier_refs or []):
            issues.append("frontier_refs ordering mismatch")

    closure_mode = str(proof_digest.get("closure_mode") or "")
    blocker_ref = control_refs.get("blocker_artifact")
    if closure_mode == "degraded":
        if blocker_artifact is None:
            issues.append("degraded proof bundle missing blocker_artifact")
        else:
            if str(blocker_artifact.get("snapshot_id") or "") != snapshot_id:
                issues.append("blocker_artifact snapshot_id mismatch")
            if str(blocker_artifact.get("closure_mode") or "") != closure_mode:
                issues.append("blocker_artifact closure_mode mismatch")
            if str(blocker_artifact.get("halt_reason") or "") != str(proof_digest.get("frontier_halt_reason") or ""):
                issues.append("blocker_artifact halt_reason mismatch")
            window_plan_summary = dict(blocker_artifact.get("window_plan_summary") or {})
            blocker_window_hit_rate: float | None = None
            if "window_hit_rate" in window_plan_summary:
                try:
                    blocker_window_hit_rate = _strict_float(window_plan_summary.get("window_hit_rate"))
                    if blocker_window_hit_rate < 0.0 or blocker_window_hit_rate > 1.0:
                        issues.append("blocker_artifact.window_plan_summary.window_hit_rate out of range")
                except (TypeError, ValueError):
                    issues.append("blocker_artifact.window_plan_summary.window_hit_rate invalid")
            if {"target_ref_count", "matched_ref_count"}.issubset(window_plan_summary):
                try:
                    target_ref_count = _strict_int(window_plan_summary.get("target_ref_count"))
                    matched_ref_count = _strict_int(window_plan_summary.get("matched_ref_count"))
                    if target_ref_count < 0 or matched_ref_count < 0 or matched_ref_count > target_ref_count:
                        issues.append("blocker_artifact.window_plan_summary ref counts invalid")
                    if blocker_window_hit_rate is not None:
                        expected = _safe_hit_rate(target_ref_count, matched_ref_count)
                        if abs(blocker_window_hit_rate - expected) > 1e-9:
                            issues.append("blocker_artifact.window_plan_summary.window_hit_rate mismatch")
                except (TypeError, ValueError):
                    issues.append("blocker_artifact.window_plan_summary ref counts invalid")
            emitted_metrics = dict(blocker_artifact.get("emitted_metrics") or {})
            emitted_window_hit_rate: float | None = None
            if "read_window_hit_rate" in emitted_metrics:
                try:
                    emitted_window_hit_rate = _strict_float(emitted_metrics.get("read_window_hit_rate"))
                    if emitted_window_hit_rate < 0.0 or emitted_window_hit_rate > 1.0:
                        issues.append("blocker_artifact.emitted_metrics.read_window_hit_rate out of range")
                except (TypeError, ValueError):
                    issues.append("blocker_artifact.emitted_metrics.read_window_hit_rate invalid")
            if {"read_target_ref_count", "read_matched_ref_count"}.issubset(emitted_metrics):
                try:
                    read_target_ref_count = _strict_int(emitted_metrics.get("read_target_ref_count"))
                    read_matched_ref_count = _strict_int(emitted_metrics.get("read_matched_ref_count"))
                    if (
                        read_target_ref_count < 0
                        or read_matched_ref_count < 0
                        or read_matched_ref_count > read_target_ref_count
                    ):
                        issues.append("blocker_artifact.emitted_metrics read ref counts invalid")
                    if emitted_window_hit_rate is not None:
                        expected = _safe_hit_rate(read_target_ref_count, read_matched_ref_count)
                        if abs(emitted_window_hit_rate - expected) > 1e-9:
                            issues.append("blocker_artifact.emitted_metrics.read_window_hit_rate mismatch")
                except (TypeError, ValueError):
                    issues.append("blocker_artifact.emitted_metrics read ref counts invalid")
            if digest_window_hit_rate is not None:
                if emitted_window_hit_rate is not None and abs(digest_window_hit_rate - emitted_window_hit_rate) > 1e-9:
                    issues.append("proof_digest.window_hit_rate mismatch blocker emitted_metrics")
                elif blocker_window_hit_rate is not None and abs(digest_window_hit_rate - blocker_window_hit_rate) > 1e-9:
                    issues.append("proof_digest.window_hit_rate mismatch blocker window_plan_summary")
        if blocker_ref and manifest is not None and str(blocker_ref) not in manifest_entries:
            issues.append("blocker_artifact path missing from manifest")

    return {
        "ok": not issues,
        "issues": issues,
        "recomputed_proof_hash": recomputed_proof_hash,
        "proof_hash_matches": not any(issue == "proof_hash mismatch" for issue in issues),
    }


def rpr_AssessConsumerMode(
    verification: dict[str, Any],
    proof_digest: dict[str, Any],
    result_validity: dict[str, Any] | None,
) -> dict[str, str]:
    return _consumer_mode_with_explain(verification, proof_digest, result_validity)[0]


def _validate_evidence_payload(schema: dict[str, Any], payload: Any, label: str) -> Result[None] | None:
    reason = validate_schema(schema, payload)
    if reason is None:
        return None
    return err_result("INVALID_ARG", f"evidence schema invalid: {label}: {reason}")


def load_evidence_control(
    package_path: str | Path,
    meta: dict[str, Any],
    manifest: dict[str, Any],
) -> Result[dict[str, Any]]:
    if not _is_evidence_package(meta, manifest):
        return ok_result({})

    root = Path(package_path)
    specs = load_specs()
    control_refs = dict(meta.get("control_refs") or {})
    sidecar_rel = str(control_refs.get("dependency_sidecar") or "control/dependency_sidecar.jsonl")
    frontier_snapshot_rel = str(control_refs.get("frontier_snapshot") or "control/frontier_snapshot.json")
    proof_digest_rel = str(control_refs.get("proof_digest") or "control/proof_digest.json")
    sidecar_manifest_rel = str(control_refs.get("sidecar_manifest") or "control/sidecar_manifest.json")
    result_validity_rel = "result/result_validity.json"

    required_paths = [
        sidecar_rel,
        frontier_snapshot_rel,
        proof_digest_rel,
        sidecar_manifest_rel,
        result_validity_rel,
    ]
    for rel_path in required_paths:
        if not (root / rel_path).exists():
            return err_result("INVALID_ARG", f"evidence package missing required control artifact: {rel_path}")

    sidecar_rows = jsonl_load(root / sidecar_rel)
    frontier_snapshot = json_load(root / frontier_snapshot_rel)
    proof_digest = json_load(root / proof_digest_rel)
    sidecar_manifest = json_load(root / sidecar_manifest_rel)
    result_validity = json_load(root / result_validity_rel)
    sidecar_segment_manifest = None
    sidecar_segment_manifest_rel = str(
        sidecar_manifest.get("segment_manifest_path") or control_refs.get("sidecar_segment_manifest") or ""
    ).strip()
    if str(sidecar_manifest.get("layout_mode") or "").strip() == "segmented" and not sidecar_segment_manifest_rel:
        return err_result("INVALID_ARG", "evidence sidecar segment manifest missing")
    if sidecar_segment_manifest_rel:
        sidecar_segment_manifest_path = root / sidecar_segment_manifest_rel
        if not sidecar_segment_manifest_path.exists():
            return err_result("INVALID_ARG", f"evidence package missing required control artifact: {sidecar_segment_manifest_rel}")
        sidecar_segment_manifest = json_load(sidecar_segment_manifest_path)

    schema_error = _validate_evidence_payload(specs["frontier_snapshot"], frontier_snapshot, "frontier_snapshot")
    if schema_error is not None:
        return schema_error
    schema_error = _validate_evidence_payload(specs["proof_digest"], proof_digest, "proof_digest")
    if schema_error is not None:
        return schema_error
    schema_error = _validate_evidence_payload(specs["sidecar_manifest"], sidecar_manifest, "sidecar_manifest")
    if schema_error is not None:
        return schema_error
    if sidecar_segment_manifest is not None:
        schema_error = _validate_evidence_payload(
            specs["sidecar_segment_manifest"],
            sidecar_segment_manifest,
            "sidecar_segment_manifest",
        )
        if schema_error is not None:
            return schema_error
    schema_error = _validate_evidence_payload(specs["result_validity"], result_validity, "result_validity")
    if schema_error is not None:
        return schema_error
    for index, row in enumerate(sidecar_rows):
        schema_error = _validate_evidence_payload(specs["dependency_sidecar"], row, f"dependency_sidecar[{index}]")
        if schema_error is not None:
            return schema_error

    trace_path = root / "event" / "events.trace"
    dict_ref = dict(meta.get("dict_ref") or {})
    dictionary_path = root / str(dict_ref.get("path") or "reference/dictionary.json")
    if not trace_path.exists():
        return err_result("INVALID_ARG", "evidence package missing event/events.trace")
    if not dictionary_path.exists():
        return err_result("INVALID_ARG", "evidence package missing referenced dictionary")

    trace_checksum = checksum_file(trace_path)
    dictionary_checksum = checksum_file(dictionary_path)
    if sidecar_manifest.get("trace_checksum") != trace_checksum:
        return err_result("INVALID_ARG", "evidence sidecar trace checksum mismatch")
    if sidecar_manifest.get("dictionary_checksum") != dictionary_checksum:
        return err_result("INVALID_ARG", "evidence sidecar dictionary checksum mismatch")
    if sidecar_segment_manifest is not None:
        segmented_validated = validate_sidecar_segment_manifest_metadata(
            sidecar_segment_manifest,
            expected_snapshot_id=str(meta.get("snapshot_id") or ""),
            expected_trace_checksum=trace_checksum,
            expected_dictionary_checksum=dictionary_checksum,
            manifest_root=root,
        )
        if not segmented_validated.ok:
            return err_result("INVALID_ARG", segmented_validated.message)

    snapshot_id = str(meta.get("snapshot_id") or "")
    if proof_digest.get("snapshot_id") != snapshot_id:
        return err_result("INVALID_ARG", "proof_digest snapshot_id mismatch")
    for row in sidecar_rows:
        if row.get("snapshot_id") != snapshot_id:
            return err_result("INVALID_ARG", "dependency_sidecar snapshot_id mismatch")
        if row.get("trace_checksum") != trace_checksum:
            return err_result("INVALID_ARG", "dependency_sidecar trace_checksum mismatch")

    frontier_refs_rows: list[dict[str, Any]] = []
    frontier_refs_rel = frontier_snapshot.get("frontier_refs_path") or control_refs.get("frontier_refs")
    if frontier_refs_rel:
        frontier_refs_path = root / str(frontier_refs_rel)
        if not frontier_refs_path.exists():
            return err_result("INVALID_ARG", f"frontier refs missing: {frontier_refs_rel}")
        frontier_refs_rows = jsonl_load(frontier_refs_path)
        for index, row in enumerate(frontier_refs_rows):
            schema_error = _validate_evidence_payload(specs["frontier_refs"], row, f"frontier_refs[{index}]")
            if schema_error is not None:
                return schema_error

    blocker_artifact = None
    blocker_rel = control_refs.get("blocker_artifact")
    if blocker_rel is None and (root / "control" / "blocker_artifact.json").exists():
        blocker_rel = "control/blocker_artifact.json"
    if blocker_rel:
        blocker_path = root / str(blocker_rel)
        if not blocker_path.exists():
            return err_result("INVALID_ARG", f"blocker artifact missing: {blocker_rel}")
        blocker_artifact = json_load(blocker_path)
        schema_error = _validate_evidence_payload(specs["blocker_artifact"], blocker_artifact, "blocker_artifact")
        if schema_error is not None:
            return schema_error

    for schema_key, ref_payload in dict(sidecar_manifest.get("schema_checksums") or {}).items():
        ref_path = root / str(ref_payload.get("path") or "")
        if not ref_path.exists():
            return err_result("INVALID_ARG", f"evidence sidecar schema ref missing: {schema_key}")
        if checksum_file(ref_path) != ref_payload.get("checksum"):
            return err_result("INVALID_ARG", f"evidence sidecar schema checksum mismatch: {schema_key}")

    for rel_path, checksum in dict(sidecar_manifest.get("entry_checksums") or {}).items():
        target = root / rel_path
        if not target.exists():
            return err_result("INVALID_ARG", f"evidence sidecar entry missing: {rel_path}")
        if checksum_file(target) != checksum:
            return err_result("INVALID_ARG", f"evidence sidecar entry checksum mismatch: {rel_path}")

    proof_verification = rpr_VerifyProofBundle(
        proof_digest,
        frontier_snapshot,
        frontier_refs_rows,
        blocker_artifact,
        manifest,
        meta=meta,
    )
    if not proof_verification["ok"]:
        return err_result("INVALID_ARG", f"invalid proof bundle: {', '.join(proof_verification['issues'])}")
    consumer_mode, consumer_mode_explain = _consumer_mode_with_explain(proof_verification, proof_digest, result_validity)

    return ok_result(
        {
            "dependency_sidecar": sidecar_rows,
            "frontier_snapshot": frontier_snapshot,
            "frontier_refs": frontier_refs_rows,
            "proof_digest": proof_digest,
            "proof_verification": proof_verification,
            "consumer_mode": consumer_mode,
            "consumer_mode_explain": consumer_mode_explain,
            "sidecar_manifest": sidecar_manifest,
            "sidecar_segment_manifest": sidecar_segment_manifest,
            "sidecar_segments": list(dict(sidecar_segment_manifest or {}).get("segments") or []),
            "result_validity": result_validity,
            "blocker_artifact": blocker_artifact,
        }
    )

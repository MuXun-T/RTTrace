from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from parser.agent_contract import ERR_PACKAGE_WRITE_FAILED
from parser.export_write_agent import ExportWriteAgent
from parser.evidence_sidecar import build_sidecar_segment_manifest, partition_dependency_sidecar_segments
from parser.evidence_models import BlockerArtifact, FrontierRefRow, FrontierSnapshot, ProofDigest
from parser.models import Alert, Diagnosis, UnifiedEvent
from parser.result import Result
from spec.io import checksum_file, serialize
from spec.schema_loader import DICTIONARY_PATH, SCHEMA_DIR, load_specs


_WRITE_AGENT = ExportWriteAgent()


class EvidencePackageWriteError(OSError):
    def __init__(self, result: Result[dict[str, Any]]) -> None:
        super().__init__(result.message)
        self.result = result


def _require_package_write(result: Result[dict[str, Any]]) -> dict[str, Any]:
    data = dict(result.data or {})
    package_result = dict(data.get("package_result") or {})
    if result.ok and package_result.get("ok", True) is not False:
        return package_result
    if result.ok:
        result = Result(
            ERR_PACKAGE_WRITE_FAILED,
            "evidence package write returned a failed package_result",
            data=data,
        )
    raise EvidencePackageWriteError(result)


def _failed_path_from_exception(exc: Exception, default_path: Path) -> Path:
    filename = getattr(exc, "filename", None)
    if filename:
        return Path(filename)
    return default_path


def _package_write_failure_error(
    package_path: Path,
    *,
    snapshot_id: str | None,
    failed_path: str | Path,
    error_message: str,
    partial_write_status: dict[str, Any] | None = None,
) -> EvidencePackageWriteError:
    result = ExportWriteAgent().package_write_failure_result(
        package_path,
        failed_path=failed_path,
        snapshot_id=str(snapshot_id or "unknown"),
        error_code=ERR_PACKAGE_WRITE_FAILED,
        error_message=error_message,
        metrics=export_write_metrics_snapshot(),
        partial_write_status=partial_write_status,
    )
    return EvidencePackageWriteError(result)


def write_evidence_package_entries(
    package_path: Path,
    entries: list[dict[str, Any]],
    *,
    snapshot_id: str | None = None,
    write_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = {
        "package_path": str(package_path),
        "snapshot_id": str(snapshot_id or "unknown"),
    }
    policy.update(dict(write_policy or {}))
    return _require_package_write(
        _WRITE_AGENT.write_evidence_package_optimized(
            {
                "package_path": str(package_path),
                "snapshot_id": policy["snapshot_id"],
                "entries": list(entries),
            },
            policy,
        )
    )


def consume_export_write_metrics() -> list[dict[str, Any]]:
    return _WRITE_AGENT.consume_metrics()


def export_write_metrics_snapshot() -> list[dict[str, Any]]:
    return list(_WRITE_AGENT.write_metrics)


def _schema_missing_fields(schema: dict[str, Any], payload: Any, label: str) -> list[str]:
    if not isinstance(payload, dict):
        return [label]
    missing: list[str] = []
    for key in schema.get("required", []):
        if key not in payload:
            missing.append(f"{label}.{key}")
    properties = schema.get("properties", {})
    for key, child_schema in properties.items():
        if key in payload and isinstance(payload[key], dict) and child_schema.get("type") == "object":
            missing.extend(_schema_missing_fields(child_schema, payload[key], f"{label}.{key}"))
    return missing


def _segment_path_token(segment_id: str) -> str:
    token = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in str(segment_id or "").strip()
    )
    return token or "segment_unknown"


def manifest_entry(
    package_path: Path,
    relative_path: str,
    *,
    category: str,
    count: int,
    format_name: str,
    schema_ref: str | None = None,
    producer: str | None = None,
    ref_keys: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "path": relative_path,
        "category": category,
        "count": int(count),
        "checksum": checksum_file(package_path / relative_path),
        "format": format_name,
        "schema_ref": schema_ref,
        "producer": producer,
    }
    if ref_keys is not None:
        entry["ref_keys"] = ref_keys
    return entry


def build_result_validity_rows(
    alerts: list[Alert],
    diagnoses: list[Diagnosis],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alert in list(alerts):
        rows.append(
            {
                "path": "result/alerts.json",
                "category": "result",
                "object_kind": "alert",
                "object_id": str(alert.alert_id),
                "validity_scope": "source_snapshot",
                "derivation_mode": "reused_context",
                "notes": "Evidence export retains alert rows from the snapshot analysis context.",
            }
        )
    for diagnosis in list(diagnoses):
        rows.append(
            {
                "path": "result/diagnoses.json",
                "category": "result",
                "object_kind": "diagnosis",
                "object_id": str(diagnosis.diag_id),
                "validity_scope": "source_snapshot",
                "derivation_mode": "reused_context",
                "notes": "Evidence export retains diagnosis rows from the snapshot analysis context.",
            }
        )
    return rows


def write_evidence_rebuild_payload(
    package_path: Path,
    *,
    rebuild_bundle: Any,
    snapshot_id: str | None = None,
) -> None:
    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "rebuild/rebuild_bundle.json",
                "write_mode": "standard_json",
                "payload": serialize(rebuild_bundle),
                "label": "rebuild_bundle",
            }
        ],
        snapshot_id=snapshot_id,
    )


def write_evidence_result_payload(
    package_path: Path,
    *,
    alerts: list[Alert],
    diagnoses: list[Diagnosis],
    snapshot_id: str | None = None,
) -> list[dict[str, Any]]:
    result_validity_rows = build_result_validity_rows(alerts, diagnoses)
    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "result/alerts.json",
                "write_mode": "stream_json_array",
                "rows": alerts,
                "label": "alerts",
            },
            {
                "relative_path": "result/diagnoses.json",
                "write_mode": "stream_json_array",
                "rows": diagnoses,
                "label": "diagnoses",
            },
            {
                "relative_path": "result/result_validity.json",
                "write_mode": "standard_json",
                "payload": {"results": result_validity_rows},
                "label": "result_validity",
            },
        ],
        snapshot_id=snapshot_id,
    )
    return result_validity_rows


def write_evidence_context_payload(
    package_path: Path,
    *,
    analysis_context: dict[str, Any],
    compare_scope: dict[str, Any],
    anchor_rows: list[dict[str, Any]],
    bookmarks: list[dict[str, Any]] | None = None,
    snapshot_id: str | None = None,
) -> None:
    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "context/analysis_context.json",
                "write_mode": "standard_json",
                "payload": analysis_context,
                "label": "analysis_context",
            },
            {
                "relative_path": "context/compare_scope.json",
                "write_mode": "standard_json",
                "payload": compare_scope,
                "label": "compare_scope",
            },
            {
                "relative_path": "context/anchors.json",
                "write_mode": "stream_json_array",
                "rows": anchor_rows,
                "label": "anchors",
            },
            {
                "relative_path": "context/bookmarks.json",
                "write_mode": "stream_json_array",
                "rows": list(bookmarks or []),
                "label": "bookmarks",
            },
        ],
        snapshot_id=snapshot_id,
    )


def write_evidence_event_payload(
    package_path: Path,
    *,
    trace_callback: Callable[[Path], Any],
    ref_index_rows: list[dict[str, Any]],
    event_count: int,
    snapshot_id: str | None = None,
) -> None:
    if int(event_count) > 0:
        event_entry = {
            "relative_path": "event/events.trace",
            "write_mode": "binary_callback",
            "callback": trace_callback,
            "count": int(event_count),
            "label": "events_trace",
        }
    else:
        event_entry = {
            "relative_path": "event/events.trace",
            "write_mode": "write_bytes",
            "payload": b"",
            "count": 0,
            "label": "events_trace",
        }
    write_evidence_package_entries(
        package_path,
        [
            event_entry,
            {
                "relative_path": "event/ref_index.json",
                "write_mode": "standard_json",
                "payload": ref_index_rows,
                "label": "ref_index",
            },
        ],
        snapshot_id=snapshot_id,
    )


def write_evidence_reference_assets(
    package_path: Path,
    *,
    schema_key_map: dict[str, str],
    snapshot_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    entries = [
        {
            "relative_path": "reference/dictionary.json",
            "write_mode": "copy_file",
            "source_path": DICTIONARY_PATH,
            "label": "dictionary",
        }
    ]
    for schema_name in schema_key_map:
        entries.append(
            {
                "relative_path": f"reference/schema/{schema_name}",
                "write_mode": "copy_file",
                "source_path": SCHEMA_DIR / schema_name,
                "label": f"schema:{schema_name}",
            }
        )
    write_evidence_package_entries(package_path, entries, snapshot_id=snapshot_id)

    dict_target = package_path / "reference" / "dictionary.json"
    try:
        dictionary_payload = serialize(json.loads(dict_target.read_text(encoding="utf-8")))
        dict_ref = {
            "path": "reference/dictionary.json",
            "algo": "sha256",
            "checksum": checksum_file(dict_target),
            "dict_ver": int(dictionary_payload["dict_ver"]),
        }
        schema_ref: dict[str, Any] = {}
        for schema_name, schema_key in schema_key_map.items():
            target = package_path / "reference" / "schema" / schema_name
            schema_ref[schema_key] = {
                "path": f"reference/schema/{schema_name}",
                "algo": "sha256",
                "checksum": checksum_file(target),
            }
    except Exception as exc:
        failed_path = _failed_path_from_exception(exc, dict_target)
        raise _package_write_failure_error(
            package_path,
            snapshot_id=snapshot_id,
            failed_path=failed_path,
            error_message=f"reference asset validation failed: {exc}",
            partial_write_status={"stage": "reference_assets"},
        ) from exc
    return dict_ref, schema_ref


def write_evidence_advisor_artifacts(
    package_path: Path,
    *,
    advisor_trace: Any | None = None,
    agent_contract: Any | None = None,
    advisor_report: dict[str, Any] | None = None,
    pre_execution_advisor_trace: Any | None = None,
    pre_execution_agent_contract: Any | None = None,
    snapshot_id: str | None = None,
) -> None:
    entries = []
    if pre_execution_advisor_trace is not None:
        entries.append(
            {
                "relative_path": "control/pre_execution_advisor_trace.json",
                "write_mode": "standard_json",
                "payload": (
                    pre_execution_advisor_trace.to_dict()
                    if hasattr(pre_execution_advisor_trace, "to_dict")
                    else pre_execution_advisor_trace
                ),
                "label": "pre_execution_advisor_trace",
            }
        )
    if pre_execution_agent_contract is not None:
        entries.append(
            {
                "relative_path": "control/pre_execution_runtime_advisor_agent_contract.json",
                "write_mode": "standard_json",
                "payload": (
                    pre_execution_agent_contract.to_dict()
                    if hasattr(pre_execution_agent_contract, "to_dict")
                    else pre_execution_agent_contract
                ),
                "label": "pre_execution_runtime_advisor_agent_contract",
            }
        )
    if advisor_trace is not None:
        entries.append(
            {
                "relative_path": "control/advisor_trace.json",
                "write_mode": "standard_json",
                "payload": advisor_trace.to_dict() if hasattr(advisor_trace, "to_dict") else advisor_trace,
                "label": "advisor_trace",
            }
        )
    if agent_contract is not None:
        entries.append(
            {
                "relative_path": "control/runtime_advisor_agent_contract.json",
                "write_mode": "standard_json",
                "payload": agent_contract.to_dict() if hasattr(agent_contract, "to_dict") else agent_contract,
                "label": "runtime_advisor_agent_contract",
            }
        )
    if entries:
        write_evidence_package_entries(
            package_path,
            entries,
            snapshot_id=snapshot_id,
        )
    if advisor_report is not None:
        advisor_report["write_metrics"] = export_write_metrics_snapshot()
        write_evidence_package_entries(
            package_path,
            [
                {
                    "relative_path": "control/advisor_report.json",
                    "write_mode": "standard_json",
                    "payload": advisor_report,
                    "label": "advisor_report",
                },
            ],
            snapshot_id=snapshot_id,
        )


def write_evidence_control_plane(
    package_path: Path,
    *,
    sidecar_rows: list[Any],
    frontier_rows: list[FrontierRefRow],
    frontier_snapshot: FrontierSnapshot,
    proof_digest: ProofDigest,
    schema_ref: dict[str, Any],
    request_rule_family: tuple[str, ...],
    snapshot_id: str,
    trace_checksum: str,
    dictionary_checksum: str,
    created_at: str,
    generator_version: str,
    blocker_artifact: BlockerArtifact | None = None,
) -> dict[str, Any]:
    dependency_sidecar_path = package_path / "control" / "dependency_sidecar.jsonl"
    frontier_snapshot_path = package_path / "control" / "frontier_snapshot.json"
    proof_digest_path = package_path / "control" / "proof_digest.json"
    sidecar_manifest_path = package_path / "control" / "sidecar_manifest.json"
    sidecar_segment_manifest_path = package_path / "control" / "sidecar_segment_manifest.json"
    frontier_refs_path = package_path / "control" / "frontier_refs.jsonl"
    blocker_path = package_path / "control" / "blocker_artifact.json"

    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "control/dependency_sidecar.jsonl",
                "write_mode": "stream_jsonl",
                "rows": sidecar_rows,
                "label": "dependency_sidecar",
            }
        ],
        snapshot_id=snapshot_id,
    )
    frontier_refs_rel = None
    if frontier_rows:
        write_evidence_package_entries(
            package_path,
            [
                {
                    "relative_path": "control/frontier_refs.jsonl",
                    "write_mode": "stream_jsonl",
                    "rows": frontier_rows,
                    "label": "frontier_refs",
                }
            ],
            snapshot_id=snapshot_id,
        )
        frontier_refs_rel = "control/frontier_refs.jsonl"
    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "control/frontier_snapshot.json",
                "write_mode": "standard_json",
                "payload": serialize(frontier_snapshot),
                "label": "frontier_snapshot",
            },
            {
                "relative_path": "control/proof_digest.json",
                "write_mode": "standard_json",
                "payload": serialize(proof_digest),
                "label": "proof_digest",
            },
        ],
        snapshot_id=snapshot_id,
    )
    segmented_layout = False
    sidecar_segment_manifest_rel: str | None = None
    sidecar_segment_manifest: dict[str, Any] | None = None
    sidecar_segments: list[dict[str, Any]] = []
    segments = partition_dependency_sidecar_segments(list(sidecar_rows))
    if len(segments) > 1:
        segmented_layout = True
        sidecar_segment_manifest_rel = "control/sidecar_segment_manifest.json"
        segment_sidecar_rel_by_id: dict[str, str] = {}
        segment_index_rel_by_id: dict[str, str] = {}
        segment_ticket_rel_by_id: dict[str, str] = {}
        segment_write_entries: list[dict[str, Any]] = []
        for segment in segments:
            segment_dir_rel = f"control/segments/{_segment_path_token(segment.segment_id)}"
            segment_sidecar_rel = f"{segment_dir_rel}/dependency_sidecar.jsonl"
            segment_sidecar_rel_by_id[segment.segment_id] = segment_sidecar_rel
            segment_index_rel_by_id[segment.segment_id] = f"{segment_sidecar_rel}.sqlite3"
            segment_ticket_rel_by_id[segment.segment_id] = f"{segment_index_rel_by_id[segment.segment_id]}.ticket.json"
            segment_write_entries.append(
                {
                    "relative_path": segment_sidecar_rel,
                    "write_mode": "stream_jsonl",
                    "rows": list(segment.rows),
                    "label": f"dependency_sidecar_segment:{segment.segment_id}",
                }
            )
        write_evidence_package_entries(
            package_path,
            segment_write_entries,
            snapshot_id=snapshot_id,
        )
        segment_checksums = {
            segment.segment_id: checksum_file(package_path / segment_sidecar_rel_by_id[segment.segment_id])
            for segment in segments
        }
        sidecar_segment_manifest = build_sidecar_segment_manifest(
            list(sidecar_rows),
            segment_dir="control/segments",
            snapshot_id=snapshot_id,
            trace_checksum=trace_checksum,
            dictionary_checksum=dictionary_checksum,
            created_at=created_at,
            sidecar_path_by_segment=segment_sidecar_rel_by_id,
            index_path_by_segment=segment_index_rel_by_id,
            checksum_by_segment=segment_checksums,
            ticket_path_by_segment=segment_ticket_rel_by_id,
        )
        write_evidence_package_entries(
            package_path,
            [
                {
                    "relative_path": sidecar_segment_manifest_rel,
                    "write_mode": "standard_json",
                    "payload": sidecar_segment_manifest,
                    "label": "sidecar_segment_manifest",
                }
            ],
            snapshot_id=snapshot_id,
        )
        sidecar_segments = list(sidecar_segment_manifest.get("segments") or [])
    if blocker_artifact is not None:
        write_evidence_package_entries(
            package_path,
            [
                {
                    "relative_path": "control/blocker_artifact.json",
                    "write_mode": "standard_json",
                    "payload": serialize(blocker_artifact),
                    "label": "blocker_artifact",
                }
            ],
            snapshot_id=snapshot_id,
        )

    sidecar_entry_paths = [
        "control/dependency_sidecar.jsonl",
        "control/frontier_snapshot.json",
        "control/proof_digest.json",
    ]
    if frontier_refs_rel is not None:
        sidecar_entry_paths.append(frontier_refs_rel)
    if blocker_artifact is not None:
        sidecar_entry_paths.append("control/blocker_artifact.json")
    if sidecar_segment_manifest_rel is not None:
        sidecar_entry_paths.append(sidecar_segment_manifest_rel)
        sidecar_entry_paths.extend(str(segment["sidecar_path"]) for segment in sidecar_segments)
    entry_checksums: dict[str, str] = {}
    for rel_path in sidecar_entry_paths:
        target = package_path / rel_path
        try:
            entry_checksums[rel_path] = checksum_file(target)
        except Exception as exc:
            raise _package_write_failure_error(
                package_path,
                snapshot_id=snapshot_id,
                failed_path=_failed_path_from_exception(exc, target),
                error_message=f"control plane entry checksum failed for {rel_path}: {exc}",
                partial_write_status={"stage": "control_plane_entry_checksums", "relative_path": rel_path},
            ) from exc
    sidecar_schema_keys = {
        "dependency_sidecar_schema",
        "frontier_snapshot_schema",
        "frontier_refs_schema",
        "proof_digest_schema",
        "sidecar_manifest_schema",
        "blocker_artifact_schema",
    }
    if segmented_layout:
        sidecar_schema_keys.add("sidecar_segment_manifest_schema")
    sidecar_manifest = {
        "sidecar_version": "evidence-sidecar-1",
        "generator_version": generator_version,
        "trace_checksum": trace_checksum,
        "dictionary_checksum": dictionary_checksum,
        "schema_checksums": {
            schema_key: {
                **ref_payload,
            }
            for schema_key, ref_payload in schema_ref.items()
            if schema_key in sidecar_schema_keys
        },
        "relation_families": list(request_rule_family),
        "entry_paths": sidecar_entry_paths,
        "entry_checksums": entry_checksums,
        "created_at": created_at,
        "snapshot_id": snapshot_id,
    }
    if segmented_layout and sidecar_segment_manifest_rel is not None:
        sidecar_manifest["layout_mode"] = "segmented"
        sidecar_manifest["segment_manifest_path"] = sidecar_segment_manifest_rel
        sidecar_manifest["segment_count"] = len(sidecar_segments)
    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "control/sidecar_manifest.json",
                "write_mode": "standard_json",
                "payload": sidecar_manifest,
                "label": "sidecar_manifest",
            }
        ],
        snapshot_id=snapshot_id,
    )

    control_refs = {
        "dependency_sidecar": "control/dependency_sidecar.jsonl",
        "frontier_snapshot": "control/frontier_snapshot.json",
        "proof_digest": "control/proof_digest.json",
        "sidecar_manifest": "control/sidecar_manifest.json",
    }
    if frontier_refs_rel is not None:
        control_refs["frontier_refs"] = frontier_refs_rel
    if sidecar_segment_manifest_rel is not None:
        control_refs["sidecar_segment_manifest"] = sidecar_segment_manifest_rel
    if blocker_artifact is not None:
        control_refs["blocker_artifact"] = "control/blocker_artifact.json"

    return {
        "frontier_refs_rel": frontier_refs_rel,
        "sidecar_manifest": sidecar_manifest,
        "sidecar_segment_manifest_rel": sidecar_segment_manifest_rel,
        "sidecar_segments": sidecar_segments,
        "control_refs": control_refs,
    }


def write_evidence_meta(
    package_path: Path,
    *,
    dataset_id: str,
    time_unit: str,
    clock_source: str,
    dict_ver: int,
    parser_ver: str,
    dict_ref: dict[str, Any],
    schema_ref: dict[str, Any],
    export_window: tuple[float, float],
    rule_family: tuple[str, ...],
    embodiment_mode: str,
    export_time: str,
    snapshot_id: str,
    run_id: str,
    run_batch_id: str,
    version_id: str,
    experiment_params: dict[str, Any],
    closure_mode: str,
    budget_vector: dict[str, Any],
    export_filter: dict[str, Any],
    analysis_context: dict[str, Any],
    compare_scope: dict[str, Any],
    control_refs: dict[str, Any],
    export_source: dict[str, Any],
    capability_flags: dict[str, Any],
    untrusted_windows: list[dict[str, Any]],
) -> dict[str, Any]:
    meta = {
        "dataset_id": dataset_id,
        "time_unit": time_unit,
        "clock_source": clock_source,
        "align_policy": "sync_anchor_or_stable_sort",
        "dict_ver": int(dict_ver),
        "parser_ver": parser_ver,
        "dict_ref": dict_ref,
        "schema_ref": schema_ref,
        "export_scope": {
            "mode": "evidence",
            "dataset_id": dataset_id,
            "window": [float(export_window[0]), float(export_window[1])],
            "rule_family": list(rule_family),
            "write_mode": "evidence_controlled",
            "embodiment_mode": embodiment_mode,
        },
        "export_time": export_time,
        "snapshot_id": snapshot_id,
        "run_id": run_id,
        "run_batch_id": run_batch_id,
        "version_id": version_id,
        "experiment_params": dict(experiment_params),
        "export_family": "evidence",
        "export_mode": "evidence",
        "closure_mode": closure_mode,
        "budget_vector": dict(budget_vector),
        "rule_family": list(rule_family),
        "embodiment_mode": embodiment_mode,
        "time_window": [float(export_window[0]), float(export_window[1])],
        "filter": dict(export_filter),
        "selection": dict(analysis_context.get("selection") or {}),
        "zoom_level": float(analysis_context.get("zoom_level", 1.0)),
        "analysis_context": analysis_context,
        "compare_scope": compare_scope,
        "evidence_anchor": analysis_context.get("evidence_anchor"),
        "compare_role": analysis_context.get("dataset_role") or "single",
        "control_refs": control_refs,
        "export_source": export_source,
        "context_padding_rule": None,
        "capability_flags": dict(capability_flags),
        "untrusted_windows": list(untrusted_windows),
    }
    specs = load_specs()
    missing = _schema_missing_fields(specs["meta"], meta, "meta")
    if missing:
        raise ValueError(f"meta schema missing fields: {', '.join(missing)}")
    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "meta.json",
                "write_mode": "standard_json",
                "payload": meta,
                "label": "meta",
            }
        ],
        snapshot_id=snapshot_id,
    )
    return meta


def build_evidence_manifest_entries(
    package_path: Path,
    *,
    producer_ver: str,
    selected_events: list[UnifiedEvent],
    ref_index_count: int,
    alerts_count: int,
    diagnoses_count: int,
    result_validity_count: int,
    anchor_count: int,
    dependency_sidecar_count: int,
    frontier_rows_count: int,
    frontier_refs_rel: str | None,
    sidecar_segment_manifest_rel: str | None,
    sidecar_segments: list[dict[str, Any]],
    schema_names: list[str],
    blocker_artifact: BlockerArtifact | None = None,
    advisor_trace_rel: str | None = None,
    advisor_contract_rel: str | None = None,
    advisor_report_rel: str | None = None,
    pre_execution_advisor_trace_rel: str | None = None,
    pre_execution_advisor_contract_rel: str | None = None,
) -> list[dict[str, Any]]:
    entries = [
        manifest_entry(package_path, "meta.json", category="meta", count=1, format_name="json", schema_ref="meta_schema"),
        manifest_entry(
            package_path,
            "event/events.trace",
            category="event",
            count=len(selected_events),
            format_name="trace",
            producer=producer_ver,
            ref_keys={
                "count": len(selected_events),
                "first": selected_events[0].ref_key if selected_events else None,
                "last": selected_events[-1].ref_key if selected_events else None,
                "path": "event/ref_index.json",
            },
        ),
        manifest_entry(package_path, "event/ref_index.json", category="event_index", count=ref_index_count, format_name="json"),
        manifest_entry(package_path, "rebuild/rebuild_bundle.json", category="rebuild", count=1, format_name="json"),
        manifest_entry(package_path, "result/alerts.json", category="result", count=alerts_count, format_name="json"),
        manifest_entry(package_path, "result/diagnoses.json", category="result", count=diagnoses_count, format_name="json"),
        manifest_entry(
            package_path,
            "result/result_validity.json",
            category="result",
            count=result_validity_count,
            format_name="json",
            schema_ref="result_validity_schema",
        ),
        manifest_entry(
            package_path,
            "context/analysis_context.json",
            category="context",
            count=1,
            format_name="json",
            schema_ref="analysis_context_schema",
        ),
        manifest_entry(
            package_path,
            "context/compare_scope.json",
            category="context",
            count=1,
            format_name="json",
            schema_ref="compare_scope_schema",
        ),
        manifest_entry(package_path, "context/anchors.json", category="context", count=anchor_count, format_name="json"),
        manifest_entry(package_path, "context/bookmarks.json", category="context", count=0, format_name="json"),
        manifest_entry(
            package_path,
            "control/dependency_sidecar.jsonl",
            category="control",
            count=dependency_sidecar_count,
            format_name="jsonl",
            schema_ref="dependency_sidecar_schema",
        ),
        manifest_entry(
            package_path,
            "control/frontier_snapshot.json",
            category="control",
            count=1,
            format_name="json",
            schema_ref="frontier_snapshot_schema",
        ),
        manifest_entry(
            package_path,
            "control/proof_digest.json",
            category="control",
            count=1,
            format_name="json",
            schema_ref="proof_digest_schema",
        ),
        manifest_entry(
            package_path,
            "control/sidecar_manifest.json",
            category="control",
            count=1,
            format_name="json",
            schema_ref="sidecar_manifest_schema",
        ),
        manifest_entry(package_path, "reference/dictionary.json", category="reference", count=1, format_name="json"),
    ]
    if sidecar_segment_manifest_rel is not None:
        entries.append(
            manifest_entry(
                package_path,
                sidecar_segment_manifest_rel,
                category="control",
                count=1,
                format_name="json",
                schema_ref="sidecar_segment_manifest_schema",
            )
        )
        for segment in sidecar_segments:
            entries.append(
                manifest_entry(
                    package_path,
                    str(segment["sidecar_path"]),
                    category="control",
                    count=int(segment.get("row_count", 0)),
                    format_name="jsonl",
                    schema_ref="dependency_sidecar_schema",
                )
            )
    if frontier_refs_rel is not None:
        entries.append(
            manifest_entry(
                package_path,
                frontier_refs_rel,
                category="control",
                count=frontier_rows_count,
                format_name="jsonl",
                schema_ref="frontier_refs_schema",
            )
        )
    if blocker_artifact is not None:
        entries.append(
            manifest_entry(
                package_path,
                "control/blocker_artifact.json",
                category="control",
                count=1,
                format_name="json",
                schema_ref="blocker_artifact_schema",
            )
        )
    if pre_execution_advisor_trace_rel is not None:
        entries.append(
            manifest_entry(
                package_path,
                pre_execution_advisor_trace_rel,
                category="control",
                count=1,
                format_name="json",
                schema_ref="advisor_trace_schema",
            )
        )
    if pre_execution_advisor_contract_rel is not None:
        entries.append(
            manifest_entry(
                package_path,
                pre_execution_advisor_contract_rel,
                category="control",
                count=1,
                format_name="json",
                schema_ref="agent_job_contract_schema",
            )
        )
    if advisor_trace_rel is not None:
        entries.append(
            manifest_entry(
                package_path,
                advisor_trace_rel,
                category="control",
                count=1,
                format_name="json",
                schema_ref="advisor_trace_schema",
            )
        )
    if advisor_contract_rel is not None:
        entries.append(
            manifest_entry(
                package_path,
                advisor_contract_rel,
                category="control",
                count=1,
                format_name="json",
                schema_ref="agent_job_contract_schema",
            )
        )
    if advisor_report_rel is not None:
        entries.append(
            manifest_entry(
                package_path,
                advisor_report_rel,
                category="control",
                count=1,
                format_name="json",
                schema_ref="advisor_report_schema",
            )
        )
    for schema_name in schema_names:
        entries.append(
            manifest_entry(
                package_path,
                f"reference/schema/{schema_name}",
                category="reference",
                count=1,
                format_name="json",
            )
        )
    return entries


def write_evidence_manifest(
    package_path: Path,
    *,
    package_version: str,
    created_at: str,
    snapshot_id: str,
    dict_ref: dict[str, Any],
    schema_ref: dict[str, Any],
    producer_ver: str,
    selected_events: list[UnifiedEvent],
    ref_index_count: int,
    alerts: list[Alert],
    diagnoses: list[Diagnosis],
    result_validity_rows: list[dict[str, Any]],
    anchor_rows: list[dict[str, Any]],
    dependency_sidecar_count: int,
    frontier_rows_count: int,
    frontier_refs_rel: str | None,
    sidecar_segment_manifest_rel: str | None,
    sidecar_segments: list[dict[str, Any]],
    schema_names: list[str],
    blocker_artifact: BlockerArtifact | None = None,
    advisor_trace_rel: str | None = None,
    advisor_contract_rel: str | None = None,
    advisor_report_rel: str | None = None,
    pre_execution_advisor_trace_rel: str | None = None,
    pre_execution_advisor_contract_rel: str | None = None,
) -> dict[str, Any]:
    try:
        entries = build_evidence_manifest_entries(
            package_path,
            producer_ver=producer_ver,
            selected_events=selected_events,
            ref_index_count=ref_index_count,
            alerts_count=len(alerts),
            diagnoses_count=len(diagnoses),
            result_validity_count=len(result_validity_rows),
            anchor_count=len(anchor_rows),
            dependency_sidecar_count=dependency_sidecar_count,
            frontier_rows_count=frontier_rows_count,
            frontier_refs_rel=frontier_refs_rel,
            sidecar_segment_manifest_rel=sidecar_segment_manifest_rel,
            sidecar_segments=sidecar_segments,
            pre_execution_advisor_trace_rel=pre_execution_advisor_trace_rel,
            pre_execution_advisor_contract_rel=pre_execution_advisor_contract_rel,
            advisor_trace_rel=advisor_trace_rel,
            advisor_contract_rel=advisor_contract_rel,
            advisor_report_rel=advisor_report_rel,
            blocker_artifact=blocker_artifact,
            schema_names=schema_names,
        )
    except Exception as exc:
        raise _package_write_failure_error(
            package_path,
            snapshot_id=snapshot_id,
            failed_path=_failed_path_from_exception(exc, package_path / "manifest.json"),
            error_message=f"manifest entry checksum failed: {exc}",
            partial_write_status={"stage": "manifest_entry_checksums"},
        ) from exc
    manifest = {
        "package_version": package_version,
        "created_at": created_at,
        "snapshot_id": snapshot_id,
        "dict_ref": dict_ref,
        "schema_ref": schema_ref,
        "entries": entries,
    }
    specs = load_specs()
    missing = _schema_missing_fields(specs["manifest"], manifest, "manifest")
    if missing:
        raise ValueError(f"manifest schema missing fields: {', '.join(missing)}")
    write_evidence_package_entries(
        package_path,
        [
            {
                "relative_path": "manifest.json",
                "write_mode": "standard_json",
                "payload": manifest,
                "label": "manifest",
            }
        ],
        snapshot_id=snapshot_id,
    )
    return manifest

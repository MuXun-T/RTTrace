from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterator

from parser.models import Alert, Diagnosis, EvidenceRef, RebuildBundle, UnifiedEvent
from parser.result import Result, err_result, ok_result
from spec.io import checksum_file, jsonl_iter

from .evidence_models import DependencySidecarEdge, evd_StableEdgeSortKey, evd_StableEventSortKey


REQUIRED_SIDECAR_SCHEMA_KEYS = {
    "dependency_sidecar_schema",
    "frontier_snapshot_schema",
    "frontier_refs_schema",
    "proof_digest_schema",
    "sidecar_manifest_schema",
    "blocker_artifact_schema",
}
ALLOWED_RULE_FAMILIES = {
    "ref_ref",
    "ref_alert",
    "ref_diagnosis",
    "ref_anchor",
    "ref_object",
}
ALLOWED_SRC_KINDS = {
    "ref",
    "object",
}
ALLOWED_DST_KINDS = {
    "ref",
    "object",
}
ALLOWED_RELATION_KINDS = {
    "ref_index_next",
    "ref_index_prev",
    "slice_start",
    "slice_end",
    "slice_start_event",
    "slice_end_event",
    "state_cause",
    "state_cause_event",
    "resource_wait",
    "resource_wait_event",
    "resource_hold",
    "resource_hold_event",
    "alert_evidence",
    "alert_event",
    "diagnosis_evidence",
    "diagnosis_event",
    "anchor_evidence",
    "anchor_event",
    "context_anchor",
    "context_anchor_event",
}
DEFAULT_AVG_EVENT_SIZE_BYTES = 128
SIDECAR_SEGMENT_MANIFEST_VERSION = "sidecar-segment-manifest-v1"
SIDECAR_SEGMENT_SCHEMA_VERSION = "evidence-sidecar-segment-1"


@dataclass(frozen=True)
class DependencySidecarSegment:
    segment_id: str
    rows: tuple[DependencySidecarEdge, ...]
    time_begin: int
    time_end: int
    core_ids: tuple[int, ...]
    row_count: int
    event_count: int


class _DependencySidecarStreamError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _strict_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return int(value)


def _normalize_segment_hint(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(segment:[^|,;\\s]+)", text)
    if match is None:
        return None
    return str(match.group(1)).strip() or None


def _ref_key_from_payload(value: Any) -> str | None:
    if isinstance(value, EvidenceRef):
        text = str(value.ref_key).strip()
        return text or None
    if isinstance(value, dict):
        ref_key = value.get("ref_key")
        if ref_key is None:
            return None
        text = str(ref_key).strip()
        return text or None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _refs_from_payloads(rows: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for row in rows:
        ref_key = _ref_key_from_payload(row)
        if ref_key is None or ref_key in seen:
            continue
        seen.add(ref_key)
        normalized.append(ref_key)
    return normalized


def _estimate_event_bytes(event: UnifiedEvent | None) -> int:
    if event is None:
        return 64
    payload_bytes = len(json.dumps(dict(event.payload or {}), ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return max(64, 48 + payload_bytes)


def _avg_event_size_bytes(events: list[UnifiedEvent]) -> int:
    if not events:
        return DEFAULT_AVG_EVENT_SIZE_BYTES
    samples = [_estimate_event_bytes(event) for event in events]
    return max(1, int(round(sum(samples) / len(samples))))


def _events_are_stably_sorted(events: list[UnifiedEvent]) -> bool:
    if len(events) < 2:
        return True
    previous = evd_StableEventSortKey(events[0])
    for event in events[1:]:
        current = evd_StableEventSortKey(event)
        if current < previous:
            return False
        previous = current
    return True


def _edge_has_usable_hints(edge: DependencySidecarEdge) -> bool:
    if int(edge.time_hint_end_ns) < int(edge.time_hint_begin_ns):
        return False
    segment_hint = str(edge.segment_hint or "").strip()
    if segment_hint:
        return True
    if edge.core_hint is None:
        return False
    if int(edge.time_hint_begin_ns) == int(edge.time_hint_end_ns):
        return edge.seq_hint_begin is not None and edge.seq_hint_end is not None
    return True


def _edge_contract_violation(edge: DependencySidecarEdge) -> str | None:
    if not str(edge.provenance or "").strip():
        return "missing provenance"
    if str(edge.src_kind or "").strip() not in ALLOWED_SRC_KINDS:
        return f"unsupported src_kind: {edge.src_kind}"
    if str(edge.dst_kind or "").strip() not in ALLOWED_DST_KINDS:
        return f"unsupported dst_kind: {edge.dst_kind}"
    if str(edge.relation_kind or "").strip() not in ALLOWED_RELATION_KINDS:
        return f"unsupported relation_kind: {edge.relation_kind}"
    if str(edge.rule_family or "").strip() not in ALLOWED_RULE_FAMILIES:
        return f"unsupported rule_family: {edge.rule_family}"
    priority = int(edge.priority)
    if priority < 0 or priority > 100:
        return f"priority out of range: {priority}"
    if not str(edge.cycle_guard_token or "").strip():
        return "missing cycle_guard_token"
    if int(edge.estimate_events) <= 0:
        return "missing usable estimate_events"
    if int(edge.estimate_bytes) <= 0:
        return "missing usable estimate_bytes"
    if (edge.seq_hint_begin is None) != (edge.seq_hint_end is None):
        return "incomplete seq hints"
    if edge.seq_hint_begin is not None and int(edge.seq_hint_end) < int(edge.seq_hint_begin):
        return "invalid seq hints"
    if not _edge_has_usable_hints(edge):
        return "missing usable hints"
    return None


def _edge_hash(snapshot_id: str, rule_family: str, relation_kind: str, src_ref: str, dst_ref: str) -> str:
    digest = hashlib.sha256()
    digest.update(snapshot_id.encode("utf-8"))
    digest.update(b"|")
    digest.update(rule_family.encode("utf-8"))
    digest.update(b"|")
    digest.update(relation_kind.encode("utf-8"))
    digest.update(b"|")
    digest.update(src_ref.encode("utf-8"))
    digest.update(b"|")
    digest.update(dst_ref.encode("utf-8"))
    return digest.hexdigest()


def _default_ref_index_rows(events: list[UnifiedEvent]) -> list[dict[str, Any]]:
    return [
        {
            "ref_key": event.ref_key,
            "timestamp_aligned": float(event.timestamp_aligned),
            "core_id": int(event.core_id),
            "seq": int(event.seq),
            "segment_hint": f"core:{int(event.core_id)}",
        }
        for event in events
    ]


def _normalize_ref_index_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not rows:
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        ref_key = _ref_key_from_payload(row)
        if ref_key is None:
            continue
        normalized.append(
            {
                "ref_key": ref_key,
                "timestamp_aligned": float(row.get("timestamp_aligned", row.get("timestamp_raw", 0.0))),
                "core_id": int(row.get("core_id", 0)),
                "seq": int(row.get("seq", 0)),
                "segment_hint": row.get("segment_hint"),
            }
        )
    normalized.sort(key=lambda item: (float(item["timestamp_aligned"]), int(item["core_id"]), int(item["seq"])))
    return normalized


def _cycle_guard_token(
    *,
    rule_family: str,
    src_kind: str,
    dst_kind: str,
    owner_scope: str,
) -> str:
    return f"{rule_family}:{src_kind}:{dst_kind}:{owner_scope}"


def _edge_from_payload(row: dict[str, Any]) -> Result[DependencySidecarEdge]:
    try:
        payload = {
            "snapshot_id": str(row["snapshot_id"]),
            "trace_checksum": str(row["trace_checksum"]),
            "src_ref": str(row["src_ref"]),
            "dst_ref": str(row["dst_ref"]),
            "src_kind": str(row["src_kind"]),
            "dst_kind": str(row["dst_kind"]),
            "relation_kind": str(row["relation_kind"]),
            "rule_family": str(row["rule_family"]),
            "provenance": str(row["provenance"]),
            "priority": int(row["priority"]),
            "time_hint_begin_ns": int(row["time_hint_begin_ns"]),
            "time_hint_end_ns": int(row["time_hint_end_ns"]),
            "core_hint": None if row.get("core_hint") is None else int(row.get("core_hint")),
            "seq_hint_begin": None if row.get("seq_hint_begin") is None else int(row.get("seq_hint_begin")),
            "seq_hint_end": None if row.get("seq_hint_end") is None else int(row.get("seq_hint_end")),
            "segment_hint": None if row.get("segment_hint") is None else str(row.get("segment_hint")),
            "cycle_guard_token": str(row["cycle_guard_token"]),
            "estimate_events": int(row["estimate_events"]),
            "estimate_bytes": int(row["estimate_bytes"]),
            "edge_hash": str(row["edge_hash"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        return err_result("INVALID_ARG", f"invalid dependency sidecar row: {exc}")
    return ok_result(DependencySidecarEdge(**payload))


def _validate_sidecar_manifest_contract(
    manifest: dict[str, Any],
    *,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
) -> Result[None]:
    if str(manifest.get("snapshot_id") or "") != str(expected_snapshot_id):
        return err_result("SIDECAR_MISMATCH", "sidecar snapshot_id mismatch")
    if str(manifest.get("trace_checksum") or "") != str(expected_trace_checksum):
        return err_result("SIDECAR_MISMATCH", "sidecar trace checksum mismatch")
    if str(manifest.get("dictionary_checksum") or "") != str(expected_dictionary_checksum):
        return err_result("SIDECAR_MISMATCH", "sidecar dictionary checksum mismatch")
    schema_checksums = dict(manifest.get("schema_checksums") or {})
    missing_schema_keys = sorted(REQUIRED_SIDECAR_SCHEMA_KEYS.difference(schema_checksums))
    if missing_schema_keys:
        return err_result("SIDECAR_MISMATCH", f"sidecar schema checksum keys missing: {', '.join(missing_schema_keys)}")
    return ok_result(None)


def _dependency_sidecar_entry_payload(manifest: dict[str, Any]) -> Result[tuple[str, str]]:
    entry_paths = [str(item).strip() for item in list(manifest.get("entry_paths") or []) if str(item).strip()]
    entry_checksums = {
        str(rel_path).strip(): str(checksum).strip()
        for rel_path, checksum in dict(manifest.get("entry_checksums") or {}).items()
        if str(rel_path).strip()
    }
    candidates = [rel_path for rel_path in entry_paths if Path(rel_path).name == "dependency_sidecar.jsonl"]
    if not candidates:
        candidates = [rel_path for rel_path in entry_checksums if Path(rel_path).name == "dependency_sidecar.jsonl"]
    if not candidates:
        return err_result("SIDECAR_MISMATCH", "sidecar manifest missing dependency sidecar entry")
    rel_path = candidates[0]
    checksum = str(entry_checksums.get(rel_path) or "").strip()
    if not checksum:
        return err_result("SIDECAR_MISMATCH", f"sidecar entry checksum missing: {rel_path}")
    return ok_result((rel_path, checksum))


def _sidecar_file_fingerprint(path: str | Path) -> tuple[int, int, int, int]:
    stat = Path(path).stat()
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def dependency_sidecar_file_fingerprint(path: str | Path) -> tuple[int, int, int, int]:
    return _sidecar_file_fingerprint(path)


def dependency_sidecar_size_bytes(path: str | Path) -> int:
    return int(Path(path).stat().st_size)


def _parse_dependency_sidecar_row(row: dict[str, Any]) -> DependencySidecarEdge:
    parsed = _edge_from_payload(dict(row or {}))
    if not parsed.ok:
        raise _DependencySidecarStreamError(parsed.code, parsed.message)
    violation = _edge_contract_violation(parsed.data)
    if violation is not None:
        raise _DependencySidecarStreamError("SIDECAR_MISMATCH", f"dependency sidecar contract violation: {violation}")
    return parsed.data


def iter_dependency_sidecar(path: str | Path) -> Iterator[DependencySidecarEdge]:
    source = Path(path)
    try:
        for row in jsonl_iter(source):
            yield _parse_dependency_sidecar_row(row)
    except _DependencySidecarStreamError:
        raise
    except FileNotFoundError as exc:
        raise _DependencySidecarStreamError("INVALID_ARG", f"dependency sidecar not found: {source}") from exc
    except OSError as exc:
        raise _DependencySidecarStreamError("INVALID_ARG", f"dependency sidecar open failed: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _DependencySidecarStreamError("INVALID_ARG", f"invalid dependency sidecar row: {exc}") from exc


def validate_sidecar_manifest_metadata(
    manifest: dict[str, Any],
    *,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
    manifest_root: str | Path | None = None,
    skip_dependency_sidecar_checksum: bool = True,
) -> Result[None]:
    validated = _validate_sidecar_manifest_contract(
        manifest,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
    )
    if not validated.ok:
        return validated
    if manifest_root is None:
        return ok_result(None)
    root = Path(manifest_root)
    schema_checksums = dict(manifest.get("schema_checksums") or {})
    for schema_key, payload in schema_checksums.items():
        schema_path = payload.get("path")
        schema_checksum = payload.get("checksum")
        if schema_path is None or schema_checksum is None:
            return err_result("SIDECAR_MISMATCH", f"sidecar schema checksum payload incomplete: {schema_key}")
        target = root / str(schema_path)
        if not target.exists():
            return err_result("SIDECAR_MISMATCH", f"sidecar schema checksum path missing: {schema_key}")
        if checksum_file(target) != str(schema_checksum):
            return err_result("SIDECAR_MISMATCH", f"sidecar schema checksum mismatch: {schema_key}")
    dependency_entry = _dependency_sidecar_entry_payload(manifest)
    if not dependency_entry.ok:
        return dependency_entry
    dependency_rel_path, _ = dependency_entry.data
    entry_paths = [str(item).strip() for item in list(manifest.get("entry_paths") or []) if str(item).strip()]
    entry_checksums = {
        str(rel_path).strip(): str(checksum).strip()
        for rel_path, checksum in dict(manifest.get("entry_checksums") or {}).items()
        if str(rel_path).strip()
    }
    all_entry_paths = list(dict.fromkeys(entry_paths + list(entry_checksums.keys())))
    for rel_path in all_entry_paths:
        target = root / rel_path
        if not target.exists():
            return err_result("SIDECAR_MISMATCH", f"sidecar entry missing: {rel_path}")
        if skip_dependency_sidecar_checksum and rel_path == dependency_rel_path:
            continue
        checksum = str(entry_checksums.get(rel_path) or "").strip()
        if not checksum:
            return err_result("SIDECAR_MISMATCH", f"sidecar entry checksum missing: {rel_path}")
        if checksum_file(target) != checksum:
            return err_result("SIDECAR_MISMATCH", f"sidecar entry checksum mismatch: {rel_path}")
    return ok_result(None)


def validate_dependency_sidecar_stream(
    path: str | Path,
    manifest: dict[str, Any],
    *,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    require_non_empty: bool = False,
) -> Result[dict[str, Any]]:
    dependency_entry = _dependency_sidecar_entry_payload(manifest)
    if not dependency_entry.ok:
        return dependency_entry
    entry_rel_path, entry_checksum = dependency_entry.data
    source = Path(path)
    try:
        fingerprint_before = _sidecar_file_fingerprint(source)
        digest = hashlib.sha256()
        row_count = 0
        with source.open("rb") as handle:
            for raw_line in handle:
                digest.update(raw_line)
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    return err_result("INVALID_ARG", f"invalid dependency sidecar row: {exc}")
                if not isinstance(payload, dict):
                    return err_result("INVALID_ARG", "invalid dependency sidecar row: jsonl row must be an object")
                try:
                    edge = _parse_dependency_sidecar_row(payload)
                except _DependencySidecarStreamError as exc:
                    return err_result(exc.code, exc.message)
                if str(edge.snapshot_id) != str(expected_snapshot_id):
                    return err_result("SIDECAR_MISMATCH", "dependency_sidecar snapshot_id mismatch")
                if str(edge.trace_checksum) != str(expected_trace_checksum):
                    return err_result("SIDECAR_MISMATCH", "dependency_sidecar trace_checksum mismatch")
                row_count += 1
        if require_non_empty and row_count <= 0:
            return err_result("SIDECAR_MISMATCH", "dependency_sidecar must be non-empty")
        computed_checksum = digest.hexdigest()
        if computed_checksum != str(entry_checksum):
            return err_result("SIDECAR_MISMATCH", f"sidecar entry checksum mismatch: {entry_rel_path}")
        fingerprint_after = _sidecar_file_fingerprint(source)
    except FileNotFoundError:
        return err_result("INVALID_ARG", f"dependency sidecar not found: {source}")
    except OSError as exc:
        return err_result("INVALID_ARG", f"dependency sidecar open failed: {exc}")
    if fingerprint_after != fingerprint_before:
        return err_result("SIDECAR_MISMATCH", "dependency sidecar file changed during streaming validation")
    return ok_result(
        {
            "row_count": int(row_count),
            "checksum": computed_checksum,
            "file_fingerprint": fingerprint_after,
            "entry_path": entry_rel_path,
        }
    )


def select_candidate_edges_from_sidecar(
    path: str | Path,
    frontier_refs: list[str],
    rule_families: tuple[str, ...],
    *,
    file_fingerprint: tuple[int, int, int, int] | None = None,
) -> Result[list[DependencySidecarEdge]]:
    source = Path(path)
    try:
        if file_fingerprint is not None and _sidecar_file_fingerprint(source) != file_fingerprint:
            return err_result("SIDECAR_MISMATCH", "dependency sidecar file changed after validation")
    except FileNotFoundError:
        return err_result("INVALID_ARG", f"dependency sidecar not found: {source}")
    except OSError as exc:
        return err_result("INVALID_ARG", f"dependency sidecar open failed: {exc}")
    frontier = set(frontier_refs)
    allowed = set(rule_families)
    if not frontier or not allowed:
        return ok_result([])
    try:
        matches = [
            edge
            for edge in iter_dependency_sidecar(source)
            if edge.src_ref in frontier and edge.rule_family in allowed
        ]
    except _DependencySidecarStreamError as exc:
        return err_result(exc.code, exc.message)
    try:
        if file_fingerprint is not None and _sidecar_file_fingerprint(source) != file_fingerprint:
            return err_result("SIDECAR_MISMATCH", "dependency sidecar file changed after validation")
    except FileNotFoundError:
        return err_result("INVALID_ARG", f"dependency sidecar not found: {source}")
    except OSError as exc:
        return err_result("INVALID_ARG", f"dependency sidecar open failed: {exc}")
    return ok_result(sort_sidecar_edges(matches))


def load_dependency_sidecar(path: str | Path) -> Result[list[DependencySidecarEdge]]:
    try:
        return ok_result(sort_sidecar_edges(list(iter_dependency_sidecar(path))))
    except _DependencySidecarStreamError as exc:
        return err_result(exc.code, exc.message)


def validate_sidecar_payload(
    rows: list[DependencySidecarEdge],
    manifest: dict[str, Any],
    *,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
    require_non_empty: bool = False,
) -> Result[None]:
    if require_non_empty and not rows:
        return err_result("SIDECAR_MISMATCH", "dependency_sidecar must be non-empty")
    validated = _validate_sidecar_manifest_contract(
        manifest,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
    )
    if not validated.ok:
        return validated
    for edge in rows:
        if str(edge.snapshot_id) != str(expected_snapshot_id):
            return err_result("SIDECAR_MISMATCH", "dependency_sidecar snapshot_id mismatch")
        if str(edge.trace_checksum) != str(expected_trace_checksum):
            return err_result("SIDECAR_MISMATCH", "dependency_sidecar trace_checksum mismatch")
        violation = _edge_contract_violation(edge)
        if violation is not None:
            return err_result("SIDECAR_MISMATCH", f"dependency_sidecar contract violation: {violation}")
    return ok_result(None)


def validate_sidecar_manifest(
    rows: list[DependencySidecarEdge],
    manifest: dict[str, Any],
    *,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
    manifest_root: str | Path | None = None,
) -> Result[None]:
    validated = validate_sidecar_payload(
        rows,
        manifest,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
    )
    if not validated.ok:
        return validated
    return validate_sidecar_manifest_metadata(
        manifest,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
        manifest_root=manifest_root,
        skip_dependency_sidecar_checksum=False,
    )


def dependency_sidecar_segment_id(edge: DependencySidecarEdge) -> str:
    segment_hint = _normalize_segment_hint(edge.segment_hint)
    if segment_hint is not None:
        return segment_hint
    if edge.core_hint is not None:
        return f"segment:core-{int(edge.core_hint)}"
    return "segment:unknown"


def partition_dependency_sidecar_segments(
    rows: list[DependencySidecarEdge],
) -> list[DependencySidecarSegment]:
    grouped: dict[str, list[DependencySidecarEdge]] = {}
    for row in sort_sidecar_edges(rows):
        grouped.setdefault(dependency_sidecar_segment_id(row), []).append(row)
    segments: list[DependencySidecarSegment] = []
    for segment_id in sorted(
        grouped.keys(),
        key=lambda key: (
            min(int(row.time_hint_begin_ns) for row in grouped[key]),
            key,
        ),
    ):
        segment_rows = tuple(sort_sidecar_edges(grouped[segment_id]))
        core_ids = tuple(
            sorted(
                {
                    int(row.core_hint)
                    for row in segment_rows
                    if row.core_hint is not None
                }
            )
        )
        segments.append(
            DependencySidecarSegment(
                segment_id=segment_id,
                rows=segment_rows,
                time_begin=min(int(row.time_hint_begin_ns) for row in segment_rows),
                time_end=max(int(row.time_hint_end_ns) for row in segment_rows),
                core_ids=core_ids,
                row_count=len(segment_rows),
                event_count=len(segment_rows),
            )
        )
    return segments


def build_sidecar_segment_manifest(
    rows: list[DependencySidecarEdge],
    *,
    segment_dir: str,
    snapshot_id: str,
    trace_checksum: str,
    dictionary_checksum: str,
    created_at: str,
    sidecar_path_by_segment: dict[str, str],
    index_path_by_segment: dict[str, str],
    checksum_by_segment: dict[str, str],
    ticket_path_by_segment: dict[str, str] | None = None,
    schema_version: str = SIDECAR_SEGMENT_SCHEMA_VERSION,
) -> dict[str, Any]:
    segments = partition_dependency_sidecar_segments(rows)
    ticket_paths = dict(ticket_path_by_segment or {})
    payload_segments: list[dict[str, Any]] = []
    for segment in segments:
        sidecar_path = str(sidecar_path_by_segment.get(segment.segment_id) or "").strip()
        index_path = str(index_path_by_segment.get(segment.segment_id) or "").strip()
        checksum = str(checksum_by_segment.get(segment.segment_id) or "").strip()
        if not sidecar_path:
            raise ValueError(f"missing sidecar_path for segment {segment.segment_id}")
        if not index_path:
            raise ValueError(f"missing index_path for segment {segment.segment_id}")
        if not checksum:
            raise ValueError(f"missing checksum for segment {segment.segment_id}")
        entry = {
            "segment_id": segment.segment_id,
            "time_begin": int(segment.time_begin),
            "time_end": int(segment.time_end),
            "core_ids": [int(core_id) for core_id in segment.core_ids],
            "event_count": int(segment.event_count),
            "row_count": int(segment.row_count),
            "sidecar_path": sidecar_path,
            "index_path": index_path,
            "checksum": checksum,
            "schema_version": str(schema_version),
        }
        ticket_path = str(ticket_paths.get(segment.segment_id) or "").strip()
        if ticket_path:
            entry["ticket_path"] = ticket_path
        payload_segments.append(entry)
    return {
        "manifest_version": SIDECAR_SEGMENT_MANIFEST_VERSION,
        "segment_dir": str(segment_dir),
        "segment_count": len(payload_segments),
        "segments": payload_segments,
        "created_at": str(created_at),
        "snapshot_id": str(snapshot_id),
        "trace_checksum": str(trace_checksum),
        "dictionary_checksum": str(dictionary_checksum),
    }


def validate_sidecar_segment_manifest_metadata(
    manifest: dict[str, Any],
    *,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
    manifest_root: str | Path | None = None,
) -> Result[None]:
    if str(manifest.get("snapshot_id") or "") != str(expected_snapshot_id):
        return err_result("SIDECAR_MISMATCH", "sidecar segment manifest snapshot_id mismatch")
    if str(manifest.get("trace_checksum") or "") != str(expected_trace_checksum):
        return err_result("SIDECAR_MISMATCH", "sidecar segment manifest trace checksum mismatch")
    if str(manifest.get("dictionary_checksum") or "") != str(expected_dictionary_checksum):
        return err_result("SIDECAR_MISMATCH", "sidecar segment manifest dictionary checksum mismatch")
    segments = list(manifest.get("segments") or [])
    try:
        segment_count = _strict_int(manifest.get("segment_count", len(segments)), label="segment_count")
    except (TypeError, ValueError) as exc:
        return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest invalid: {exc}")
    if segment_count != len(segments):
        return err_result("SIDECAR_MISMATCH", "sidecar segment manifest segment_count mismatch")
    seen_ids: set[str] = set()
    root = Path(manifest_root) if manifest_root is not None else None
    for index, row in enumerate(segments):
        if not isinstance(row, dict):
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest invalid: segments[{index}] must be an object")
        try:
            segment_id = str(row["segment_id"]).strip()
            time_begin = _strict_int(row["time_begin"], label=f"segments[{index}].time_begin")
            time_end = _strict_int(row["time_end"], label=f"segments[{index}].time_end")
            core_ids = tuple(sorted({_strict_int(item, label=f"segments[{index}].core_ids") for item in list(row.get("core_ids") or [])}))
            event_count = _strict_int(row["event_count"], label=f"segments[{index}].event_count")
            row_count = _strict_int(row["row_count"], label=f"segments[{index}].row_count")
            sidecar_path = str(row["sidecar_path"]).strip()
            index_path = str(row["index_path"]).strip()
            checksum = str(row["checksum"]).strip()
            schema_version = str(row["schema_version"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest invalid: {exc}")
        if not segment_id:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest invalid: segments[{index}].segment_id missing")
        if segment_id in seen_ids:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest duplicate segment_id: {segment_id}")
        seen_ids.add(segment_id)
        if time_end < time_begin:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest invalid time range: {segment_id}")
        if event_count < 0 or row_count < 0:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest negative counts: {segment_id}")
        if not sidecar_path:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest missing sidecar_path: {segment_id}")
        if not index_path:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest missing index_path: {segment_id}")
        if not checksum:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest missing checksum: {segment_id}")
        if schema_version != SIDECAR_SEGMENT_SCHEMA_VERSION:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment manifest schema_version mismatch: {segment_id}")
        if root is None:
            continue
        target = root / sidecar_path
        if not target.exists():
            return err_result("SIDECAR_MISMATCH", f"sidecar segment missing: {sidecar_path}")
        if checksum_file(target) != checksum:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment checksum mismatch: {sidecar_path}")
        loaded = load_dependency_sidecar(target)
        if not loaded.ok:
            return loaded
        for edge in loaded.data:
            if str(edge.snapshot_id) != str(expected_snapshot_id):
                return err_result("SIDECAR_MISMATCH", f"sidecar segment snapshot_id mismatch: {segment_id}")
            if str(edge.trace_checksum) != str(expected_trace_checksum):
                return err_result("SIDECAR_MISMATCH", f"sidecar segment trace_checksum mismatch: {segment_id}")
        if len(loaded.data) != row_count:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment row_count mismatch: {segment_id}")
        if core_ids:
            actual_core_ids = tuple(
                sorted(
                    {
                        int(item.core_hint)
                        for item in loaded.data
                        if item.core_hint is not None
                    }
                )
            )
            if actual_core_ids != core_ids:
                return err_result("SIDECAR_MISMATCH", f"sidecar segment core_ids mismatch: {segment_id}")
    return ok_result(None)


def build_dependency_sidecar(
    bundle: RebuildBundle,
    *,
    snapshot_id: str,
    rule_families: tuple[str, ...],
    alerts: list[Alert] | None = None,
    diagnoses: list[Diagnosis] | list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    anchors: list[dict[str, Any]] | None = None,
    ref_index_rows: list[dict[str, Any]] | None = None,
) -> list[DependencySidecarEdge]:
    allowed = set(rule_families)
    events = bundle.event_stream if _events_are_stably_sorted(bundle.event_stream) else sorted(bundle.event_stream, key=evd_StableEventSortKey)
    normalized_ref_rows = _normalize_ref_index_rows(ref_index_rows) or _default_ref_index_rows(events)
    ref_hint_by_key = {str(row["ref_key"]): row for row in normalized_ref_rows}
    event_by_ref: dict[str, UnifiedEvent] | None = None
    avg_event_size_bytes = _avg_event_size_bytes(events)
    edges: dict[tuple[str, str, str, str], DependencySidecarEdge] = {}
    rejected_contract_count = 0

    def event_hint(ref_key: str) -> UnifiedEvent | None:
        nonlocal event_by_ref
        if event_by_ref is None:
            event_by_ref = {event.ref_key: event for event in events}
        return event_by_ref.get(ref_key)

    def add_edge(
        src_ref: str | None,
        dst_ref: str | None,
        *,
        src_kind: str,
        dst_kind: str,
        relation_kind: str,
        rule_family: str,
        provenance: str,
        priority: int,
        owner_scope: str,
        time_hint_begin_ns: int | None = None,
        time_hint_end_ns: int | None = None,
        core_hint: int | None = None,
        seq_hint_begin: int | None = None,
        seq_hint_end: int | None = None,
        segment_hint: str | None = None,
        estimate_events: int | None = None,
        estimate_bytes: int | None = None,
    ) -> None:
        nonlocal rejected_contract_count
        if rule_family not in allowed:
            return
        if src_ref is None or dst_ref is None:
            return
        src_ref_text = str(src_ref).strip()
        dst_ref_text = str(dst_ref).strip()
        if not src_ref_text or not dst_ref_text or src_ref_text == dst_ref_text:
            return
        key = (src_ref_text, dst_ref_text, relation_kind, rule_family)
        if key in edges:
            return
        hint_row = ref_hint_by_key.get(dst_ref_text) or ref_hint_by_key.get(src_ref_text)
        hint_event: UnifiedEvent | None = None
        if time_hint_begin_ns is None:
            if hint_row is not None:
                time_hint_begin_ns = int(hint_row.get("timestamp_aligned", 0.0))
            else:
                hint_event = event_hint(dst_ref_text) or event_hint(src_ref_text)
                time_hint_begin_ns = int(hint_event.timestamp_aligned) if hint_event is not None else 0
        if time_hint_end_ns is None:
            time_hint_end_ns = int(time_hint_begin_ns)
        if core_hint is None:
            if hint_row is not None:
                core_hint = int(hint_row.get("core_id", 0))
            else:
                hint_event = hint_event or event_hint(dst_ref_text) or event_hint(src_ref_text)
                if hint_event is not None:
                    core_hint = int(hint_event.core_id)
        if seq_hint_begin is None:
            if hint_row is not None:
                seq_hint_begin = int(hint_row.get("seq", 0))
            else:
                hint_event = hint_event or event_hint(dst_ref_text) or event_hint(src_ref_text)
                if hint_event is not None:
                    seq_hint_begin = int(hint_event.seq)
        if seq_hint_end is None and seq_hint_begin is not None:
            seq_hint_end = int(seq_hint_begin)
        if segment_hint is None and core_hint is not None:
            segment_hint = f"core:{int(core_hint)}"
        if estimate_events is None:
            estimate_events = 1
        if estimate_bytes is None:
            estimate_bytes = max(int(estimate_events), 1) * int(avg_event_size_bytes)
        if estimate_events < 0 or estimate_bytes < 0:
            return
        cycle_token = _cycle_guard_token(
            rule_family=rule_family,
            src_kind=src_kind,
            dst_kind=dst_kind,
            owner_scope=owner_scope,
        )
        candidate = DependencySidecarEdge(
            snapshot_id=snapshot_id,
            trace_checksum="pending",
            src_ref=src_ref_text,
            dst_ref=dst_ref_text,
            src_kind=src_kind,
            dst_kind=dst_kind,
            relation_kind=relation_kind,
            rule_family=rule_family,
            provenance=provenance,
            priority=int(priority),
            time_hint_begin_ns=int(time_hint_begin_ns),
            time_hint_end_ns=int(time_hint_end_ns),
            core_hint=int(core_hint) if core_hint is not None else None,
            seq_hint_begin=int(seq_hint_begin) if seq_hint_begin is not None else None,
            seq_hint_end=int(seq_hint_end) if seq_hint_end is not None else None,
            segment_hint=segment_hint,
            cycle_guard_token=cycle_token,
            estimate_events=int(estimate_events),
            estimate_bytes=int(estimate_bytes),
            edge_hash=_edge_hash(snapshot_id, rule_family, relation_kind, src_ref_text, dst_ref_text),
        )
        violation = _edge_contract_violation(candidate)
        if violation is not None:
            rejected_contract_count += 1
            return
        edges[key] = candidate

    ref_sequence = [str(row["ref_key"]) for row in normalized_ref_rows if str(row.get("ref_key", "")).strip()]
    for left, right in zip(ref_sequence, ref_sequence[1:]):
        add_edge(
            left,
            right,
            src_kind="ref",
            dst_kind="ref",
            relation_kind="ref_index_next",
            rule_family="ref_ref",
            provenance="ref_index_row",
            priority=50,
            owner_scope="ref_index",
        )
        add_edge(
            right,
            left,
            src_kind="ref",
            dst_kind="ref",
            relation_kind="ref_index_prev",
            rule_family="ref_ref",
            provenance="ref_index_row",
            priority=45,
            owner_scope="ref_index",
        )

    for row in list(bundle.exec_slices):
        object_ref = f"slice:{row.slice_id}"
        boundary_refs = {
            ref_key
            for ref_key in [str(row.start_event or "").strip(), str(row.end_event or "").strip()]
            if ref_key
        }
        boundary_estimate = max(len(boundary_refs), 1)
        for edge_ref, relation_kind in ((row.start_event, "slice_start"), (row.end_event, "slice_end")):
            add_edge(
                edge_ref,
                object_ref,
                src_kind="ref",
                dst_kind="object",
                relation_kind=relation_kind,
                rule_family="ref_object",
                provenance="exec_slice_boundary",
                priority=80,
                owner_scope=object_ref,
                time_hint_begin_ns=int(row.t_begin),
                time_hint_end_ns=int(row.t_end),
                estimate_events=boundary_estimate,
            )
            add_edge(
                object_ref,
                edge_ref,
                src_kind="object",
                dst_kind="ref",
                relation_kind=f"{relation_kind}_event",
                rule_family="ref_object",
                provenance="exec_slice_boundary",
                priority=80,
                owner_scope=object_ref,
                time_hint_begin_ns=int(row.t_begin),
                time_hint_end_ns=int(row.t_end),
                estimate_events=boundary_estimate,
            )

    for row in list(bundle.task_states):
        object_ref = f"state:{row.seg_id}"
        cause_ref = str(row.cause_event)
        add_edge(
            cause_ref,
            object_ref,
            src_kind="ref",
            dst_kind="object",
            relation_kind="state_cause",
            rule_family="ref_object",
            provenance="task_state_cause",
            priority=85,
            owner_scope=object_ref,
            time_hint_begin_ns=int(row.t_begin),
            time_hint_end_ns=int(row.t_end),
            estimate_events=1,
        )
        add_edge(
            object_ref,
            cause_ref,
            src_kind="object",
            dst_kind="ref",
            relation_kind="state_cause_event",
            rule_family="ref_object",
            provenance="task_state_cause",
            priority=85,
            owner_scope=object_ref,
            time_hint_begin_ns=int(row.t_begin),
            time_hint_end_ns=int(row.t_end),
            estimate_events=1,
        )

    resource_graph = bundle.resource_graph
    wait_edges = list(resource_graph.wait_edges) if resource_graph is not None else []
    hold_edges = list(resource_graph.hold_edges) if resource_graph is not None else []

    for index, edge in enumerate(wait_edges):
        event_ref = _ref_key_from_payload((edge or {}).get("evidence_ref"))
        object_ref = f"wait:{index}:{(edge or {}).get('obj_id')}:{(edge or {}).get('task_id')}"
        add_edge(
            event_ref,
            object_ref,
            src_kind="ref",
            dst_kind="object",
            relation_kind="resource_wait",
            rule_family="ref_object",
            provenance="resource_wait_edge",
            priority=90,
            owner_scope=object_ref,
            estimate_events=1,
        )
        add_edge(
            object_ref,
            event_ref,
            src_kind="object",
            dst_kind="ref",
            relation_kind="resource_wait_event",
            rule_family="ref_object",
            provenance="resource_wait_edge",
            priority=90,
            owner_scope=object_ref,
            estimate_events=1,
        )
    for index, edge in enumerate(hold_edges):
        event_ref = _ref_key_from_payload((edge or {}).get("evidence_ref"))
        object_ref = f"hold:{index}:{(edge or {}).get('obj_id')}:{(edge or {}).get('task_id')}"
        add_edge(
            event_ref,
            object_ref,
            src_kind="ref",
            dst_kind="object",
            relation_kind="resource_hold",
            rule_family="ref_object",
            provenance="resource_hold_edge",
            priority=90,
            owner_scope=object_ref,
            estimate_events=1,
        )
        add_edge(
            object_ref,
            event_ref,
            src_kind="object",
            dst_kind="ref",
            relation_kind="resource_hold_event",
            rule_family="ref_object",
            provenance="resource_hold_edge",
            priority=90,
            owner_scope=object_ref,
            estimate_events=1,
        )

    for alert in list(alerts or []):
        object_ref = f"alert:{alert.alert_id}"
        refs = _refs_from_payloads(list(alert.evidence_refs or []))
        estimate_events = max(len(refs), 1)
        for ref_key in refs:
            add_edge(
                ref_key,
                object_ref,
                src_kind="ref",
                dst_kind="object",
                relation_kind="alert_evidence",
                rule_family="ref_alert",
                provenance="alert_evidence",
                priority=100,
                owner_scope=object_ref,
                estimate_events=estimate_events,
            )
            add_edge(
                object_ref,
                ref_key,
                src_kind="object",
                dst_kind="ref",
                relation_kind="alert_event",
                rule_family="ref_alert",
                provenance="alert_evidence",
                priority=100,
                owner_scope=object_ref,
                estimate_events=estimate_events,
            )

    for diagnosis in list(diagnoses or []):
        if isinstance(diagnosis, Diagnosis):
            refs = _refs_from_payloads(list(diagnosis.evidence_refs or []))
            diag_id = diagnosis.diag_id
        else:
            refs = _refs_from_payloads(list((diagnosis or {}).get("evidence_refs") or []))
            diag_id = str((diagnosis or {}).get("diag_id") or "diagnosis")
        object_ref = f"diag:{diag_id}"
        estimate_events = max(len(refs), 1)
        for ref_key in refs:
            add_edge(
                ref_key,
                object_ref,
                src_kind="ref",
                dst_kind="object",
                relation_kind="diagnosis_evidence",
                rule_family="ref_diagnosis",
                provenance="diagnosis_evidence",
                priority=95,
                owner_scope=object_ref,
                estimate_events=estimate_events,
            )
            add_edge(
                object_ref,
                ref_key,
                src_kind="object",
                dst_kind="ref",
                relation_kind="diagnosis_event",
                rule_family="ref_diagnosis",
                provenance="diagnosis_evidence",
                priority=95,
                owner_scope=object_ref,
                estimate_events=estimate_events,
            )

    for index, anchor in enumerate(list(anchors or [])):
        anchor_id = str((anchor or {}).get("anchor_id") or f"anchor:{index}")
        object_ref = f"anchor:{anchor_id}"
        anchor_ref = _ref_key_from_payload((anchor or {}).get("evidence_anchor"))
        estimate_events = 1
        add_edge(
            anchor_ref,
            object_ref,
            src_kind="ref",
            dst_kind="object",
            relation_kind="anchor_evidence",
            rule_family="ref_anchor",
            provenance="anchor_evidence",
            priority=92,
            owner_scope=object_ref,
            estimate_events=estimate_events,
        )
        add_edge(
            object_ref,
            anchor_ref,
            src_kind="object",
            dst_kind="ref",
            relation_kind="anchor_event",
            rule_family="ref_anchor",
            provenance="anchor_evidence",
            priority=92,
            owner_scope=object_ref,
            estimate_events=estimate_events,
        )

    context_anchor = _ref_key_from_payload((context or {}).get("evidence_anchor"))
    if context_anchor is not None:
        object_ref = "anchor:context:current"
        estimate_events = 1
        add_edge(
            context_anchor,
            object_ref,
            src_kind="ref",
            dst_kind="object",
            relation_kind="context_anchor",
            rule_family="ref_anchor",
            provenance="analysis_context_anchor",
            priority=92,
            owner_scope=object_ref,
            estimate_events=estimate_events,
        )
        add_edge(
            object_ref,
            context_anchor,
            src_kind="object",
            dst_kind="ref",
            relation_kind="context_anchor_event",
            rule_family="ref_anchor",
            provenance="analysis_context_anchor",
            priority=92,
            owner_scope=object_ref,
            estimate_events=estimate_events,
        )

    setattr(build_dependency_sidecar, "last_rejected_contract_count", int(rejected_contract_count))
    build_dependency_sidecar.last_rejected_contract_count = int(rejected_contract_count)
    return sort_sidecar_edges(list(edges.values()))


def materialize_dependency_sidecar(
    rows: list[DependencySidecarEdge],
    *,
    trace_checksum: str,
) -> list[DependencySidecarEdge]:
    return [replace(row, trace_checksum=str(trace_checksum)) for row in rows]


def sort_sidecar_edges(rows: list[DependencySidecarEdge]) -> list[DependencySidecarEdge]:
    return sorted(rows, key=evd_StableEdgeSortKey)


def select_candidate_edges(
    rows: list[DependencySidecarEdge],
    frontier_refs: list[str],
    rule_families: tuple[str, ...],
) -> list[DependencySidecarEdge]:
    frontier = set(frontier_refs)
    allowed = set(rule_families)
    return sort_sidecar_edges(
        [
            row
            for row in rows
            if row.src_ref in frontier and row.rule_family in allowed
        ]
    )

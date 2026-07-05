from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Iterable

from parser.result import Result, err_result, ok_result

from .evidence_models import DependencySidecarEdge
from .evidence_sidecar import (
    _DependencySidecarStreamError,
    dependency_sidecar_file_fingerprint,
    dependency_sidecar_size_bytes,
    iter_dependency_sidecar,
    select_candidate_edges_from_sidecar,
    sort_sidecar_edges,
    validate_sidecar_segment_manifest_metadata,
)


SIDECAR_INDEX_SCHEMA_VERSION = 2
SIDECAR_INDEX_TICKET_VERSION = "sidecar-index-ticket-v1"
SIDECAR_INDEX_BACKEND = "sqlite"
SIDECAR_INDEX_SELECTOR_MODE = "indexed_sqlite"
DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES = 256 * 1024 * 1024
_SQLITE_PARAM_LIMIT = 900
_INSERT_BATCH_SIZE = 2048


@dataclass(frozen=True)
class SidecarIndexHandle:
    path: Path
    sidecar_path: Path
    snapshot_id: str
    trace_checksum: str
    sidecar_checksum: str
    file_fingerprint: tuple[int, int, int, int]
    sidecar_bytes: int
    row_count: int
    dictionary_checksum: str = ""
    selector_mode: str = SIDECAR_INDEX_SELECTOR_MODE
    build_seconds: float = 0.0
    bytes_scanned: int = 0
    built: bool = False
    ticket_write_seconds: float = 0.0
    ticket_reused: bool = False


@dataclass(frozen=True)
class SidecarIndexTicket:
    ticket_version: str
    index_backend: str
    index_path: Path
    sidecar_path: Path
    sidecar_checksum: str
    sidecar_bytes: int
    file_fingerprint: tuple[int, int, int, int]
    row_count: int
    snapshot_id: str
    trace_checksum: str
    dictionary_checksum: str
    schema_version: int
    created_at: str
    index_build_seconds: float
    build_host_platform: str
    selector_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_version": self.ticket_version,
            "index_backend": self.index_backend,
            "index_path": str(self.index_path),
            "sidecar_path": str(self.sidecar_path),
            "sidecar_checksum": self.sidecar_checksum,
            "sidecar_bytes": int(self.sidecar_bytes),
            "file_fingerprint": [int(item) for item in self.file_fingerprint],
            "row_count": int(self.row_count),
            "snapshot_id": self.snapshot_id,
            "trace_checksum": self.trace_checksum,
            "dictionary_checksum": self.dictionary_checksum,
            "schema_version": int(self.schema_version),
            "created_at": self.created_at,
            "index_build_seconds": float(self.index_build_seconds),
            "build_host_platform": self.build_host_platform,
            "selector_mode": self.selector_mode,
        }


def sidecar_stream_scan_fallback_allowed(
    sidecar_bytes: int,
    *,
    threshold_bytes: int = DEFAULT_SIDECAR_STREAM_SCAN_MAX_BYTES,
) -> bool:
    return int(sidecar_bytes) <= int(threshold_bytes)


def sidecar_index_path_for_source(
    sidecar_path: str | Path,
    *,
    sidecar_checksum: str | None = None,
    use_temp_dir: bool = False,
) -> Path:
    source = Path(sidecar_path).expanduser().resolve()
    if not use_temp_dir:
        return source.with_name(f"{source.name}.sqlite3")
    digest = hashlib.sha256(f"{source}|{sidecar_checksum or ''}".encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(tempfile.gettempdir()) / "rttrace-sidecar-index"
    return cache_dir / f"{source.name}.{digest}.sqlite3"


def sidecar_index_ticket_path_for_source(
    sidecar_path: str | Path,
    *,
    index_path: str | Path | None = None,
    sidecar_checksum: str | None = None,
    use_temp_dir: bool = False,
) -> Path:
    resolved_index_path = (
        Path(index_path).expanduser().resolve()
        if index_path is not None
        else sidecar_index_path_for_source(
            sidecar_path,
            sidecar_checksum=sidecar_checksum,
            use_temp_dir=use_temp_dir,
        )
    )
    return resolved_index_path.with_name(f"{resolved_index_path.name}.ticket.json")


def _normalize_fingerprint(value: Iterable[int]) -> tuple[int, int, int, int]:
    items = tuple(int(item) for item in value)
    if len(items) != 4:
        raise ValueError("sidecar file fingerprint must contain four integers")
    return items


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE metadata (
            schema_version INTEGER NOT NULL,
            snapshot_id TEXT NOT NULL,
            trace_checksum TEXT NOT NULL,
            dictionary_checksum TEXT NOT NULL,
            sidecar_path TEXT NOT NULL,
            sidecar_checksum TEXT NOT NULL,
            sidecar_bytes INTEGER NOT NULL,
            file_device INTEGER NOT NULL,
            file_inode INTEGER NOT NULL,
            file_size INTEGER NOT NULL,
            file_mtime_ns INTEGER NOT NULL,
            row_count INTEGER NOT NULL,
            created_at_unix REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE edges (
            edge_hash TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            trace_checksum TEXT NOT NULL,
            src_ref TEXT NOT NULL,
            dst_ref TEXT NOT NULL,
            src_kind TEXT NOT NULL,
            dst_kind TEXT NOT NULL,
            relation_kind TEXT NOT NULL,
            rule_family TEXT NOT NULL,
            provenance TEXT NOT NULL,
            priority INTEGER NOT NULL,
            time_hint_begin_ns INTEGER NOT NULL,
            time_hint_end_ns INTEGER NOT NULL,
            core_hint INTEGER,
            seq_hint_begin INTEGER,
            seq_hint_end INTEGER,
            segment_hint TEXT,
            cycle_guard_token TEXT NOT NULL,
            estimate_events INTEGER NOT NULL,
            estimate_bytes INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX idx_edges_src_rule_sort
        ON edges (
            src_ref,
            rule_family,
            priority DESC,
            time_hint_begin_ns ASC,
            dst_ref ASC,
            edge_hash ASC
        )
        """
    )


def _metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "schema_version": int(row["schema_version"]),
        "snapshot_id": str(row["snapshot_id"]),
        "trace_checksum": str(row["trace_checksum"]),
        "dictionary_checksum": str(row["dictionary_checksum"]) if "dictionary_checksum" in keys else "",
        "sidecar_path": str(row["sidecar_path"]),
        "sidecar_checksum": str(row["sidecar_checksum"]),
        "sidecar_bytes": int(row["sidecar_bytes"]),
        "file_fingerprint": (
            int(row["file_device"]),
            int(row["file_inode"]),
            int(row["file_size"]),
            int(row["file_mtime_ns"]),
        ),
        "row_count": int(row["row_count"]),
        "created_at_unix": float(row["created_at_unix"]) if "created_at_unix" in keys else 0.0,
    }


def _read_metadata(connection: sqlite3.Connection) -> Result[dict[str, Any]]:
    try:
        row = connection.execute("SELECT * FROM metadata LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        return err_result("SIDECAR_MISMATCH", f"sidecar index metadata read failed: {exc}")
    if row is None:
        return err_result("SIDECAR_MISMATCH", "sidecar index metadata missing")
    try:
        metadata = _metadata_from_row(row)
    except (KeyError, TypeError, ValueError) as exc:
        return err_result("SIDECAR_MISMATCH", f"sidecar index metadata invalid: {exc}")
    if int(metadata["schema_version"]) != SIDECAR_INDEX_SCHEMA_VERSION:
        return err_result("SIDECAR_MISMATCH", "sidecar index schema version mismatch")
    return ok_result(metadata)


def _metadata_matches(
    metadata: dict[str, Any],
    *,
    sidecar_path: Path,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str | None = None,
    sidecar_checksum: str,
    file_fingerprint: tuple[int, int, int, int],
) -> bool:
    matches = (
        str(metadata.get("snapshot_id") or "") == str(expected_snapshot_id)
        and str(metadata.get("trace_checksum") or "") == str(expected_trace_checksum)
        and str(metadata.get("sidecar_checksum") or "") == str(sidecar_checksum)
        and tuple(metadata.get("file_fingerprint") or ()) == tuple(file_fingerprint)
        and str(metadata.get("sidecar_path") or "") == str(sidecar_path)
    )
    if expected_dictionary_checksum is not None:
        matches = matches and str(metadata.get("dictionary_checksum") or "") == str(expected_dictionary_checksum)
    return matches


def _handle_from_metadata(
    index_path: Path,
    metadata: dict[str, Any],
    *,
    built: bool,
    build_seconds: float,
    bytes_scanned: int,
) -> SidecarIndexHandle:
    return SidecarIndexHandle(
        path=index_path,
        sidecar_path=Path(str(metadata["sidecar_path"])),
        snapshot_id=str(metadata["snapshot_id"]),
        trace_checksum=str(metadata["trace_checksum"]),
        sidecar_checksum=str(metadata["sidecar_checksum"]),
        file_fingerprint=tuple(metadata["file_fingerprint"]),
        sidecar_bytes=int(metadata["sidecar_bytes"]),
        row_count=int(metadata["row_count"]),
        dictionary_checksum=str(metadata.get("dictionary_checksum") or ""),
        build_seconds=float(build_seconds),
        bytes_scanned=int(bytes_scanned),
        built=bool(built),
    )


def _open_matching_index(
    index_path: Path,
    *,
    sidecar_path: Path,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str | None = None,
    sidecar_checksum: str,
    file_fingerprint: tuple[int, int, int, int],
) -> Result[SidecarIndexHandle] | None:
    if not index_path.exists():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(index_path, readonly=True)
        metadata_result = _read_metadata(connection)
    except sqlite3.Error as exc:
        return err_result("SIDECAR_MISMATCH", f"sidecar index open failed: {exc}")
    finally:
        if connection is not None:
            connection.close()
    if not metadata_result.ok:
        return metadata_result
    metadata = metadata_result.data
    if not _metadata_matches(
        metadata,
        sidecar_path=sidecar_path,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
        sidecar_checksum=sidecar_checksum,
        file_fingerprint=file_fingerprint,
    ):
        return err_result("SIDECAR_MISMATCH", "sidecar index metadata mismatch")
    return ok_result(
        _handle_from_metadata(
            index_path,
            metadata,
            built=False,
            build_seconds=0.0,
            bytes_scanned=0,
        )
    )


def _edge_insert_row(edge: DependencySidecarEdge) -> tuple[Any, ...]:
    return (
        str(edge.edge_hash),
        str(edge.snapshot_id),
        str(edge.trace_checksum),
        str(edge.src_ref),
        str(edge.dst_ref),
        str(edge.src_kind),
        str(edge.dst_kind),
        str(edge.relation_kind),
        str(edge.rule_family),
        str(edge.provenance),
        int(edge.priority),
        int(edge.time_hint_begin_ns),
        int(edge.time_hint_end_ns),
        None if edge.core_hint is None else int(edge.core_hint),
        None if edge.seq_hint_begin is None else int(edge.seq_hint_begin),
        None if edge.seq_hint_end is None else int(edge.seq_hint_end),
        None if edge.segment_hint is None else str(edge.segment_hint),
        str(edge.cycle_guard_token),
        int(edge.estimate_events),
        int(edge.estimate_bytes),
    )


def _insert_batch(connection: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO edges (
            edge_hash,
            snapshot_id,
            trace_checksum,
            src_ref,
            dst_ref,
            src_kind,
            dst_kind,
            relation_kind,
            rule_family,
            provenance,
            priority,
            time_hint_begin_ns,
            time_hint_end_ns,
            core_hint,
            seq_hint_begin,
            seq_hint_end,
            segment_hint,
            cycle_guard_token,
            estimate_events,
            estimate_bytes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _build_index_at_path(
    index_path: Path,
    *,
    sidecar_path: Path,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
    sidecar_checksum: str,
    file_fingerprint: tuple[int, int, int, int],
) -> Result[SidecarIndexHandle]:
    started = time.perf_counter()
    temp_path = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")
    connection: sqlite3.Connection | None = None
    try:
        if dependency_sidecar_file_fingerprint(sidecar_path) != file_fingerprint:
            return err_result("SIDECAR_MISMATCH", "dependency sidecar file changed before index build")
    except FileNotFoundError:
        return err_result("INVALID_ARG", f"dependency sidecar not found: {sidecar_path}")
    except OSError as exc:
        return err_result("INVALID_ARG", f"dependency sidecar stat failed: {exc}")
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        if temp_path.exists():
            temp_path.unlink()
        sidecar_bytes = dependency_sidecar_size_bytes(sidecar_path)
        row_count = 0
        connection = _connect(temp_path)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        _create_schema(connection)
        batch: list[tuple[Any, ...]] = []
        for edge in iter_dependency_sidecar(sidecar_path):
            if str(edge.snapshot_id) != str(expected_snapshot_id):
                return err_result("SIDECAR_MISMATCH", "dependency_sidecar snapshot_id mismatch")
            if str(edge.trace_checksum) != str(expected_trace_checksum):
                return err_result("SIDECAR_MISMATCH", "dependency_sidecar trace_checksum mismatch")
            batch.append(_edge_insert_row(edge))
            row_count += 1
            if len(batch) >= _INSERT_BATCH_SIZE:
                _insert_batch(connection, batch)
                batch = []
        _insert_batch(connection, batch)
        connection.execute(
            """
            INSERT INTO metadata (
                schema_version,
                snapshot_id,
                trace_checksum,
                dictionary_checksum,
                sidecar_path,
                sidecar_checksum,
                sidecar_bytes,
                file_device,
                file_inode,
                file_size,
                file_mtime_ns,
                row_count,
                created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SIDECAR_INDEX_SCHEMA_VERSION,
                str(expected_snapshot_id),
                str(expected_trace_checksum),
                str(expected_dictionary_checksum),
                str(sidecar_path),
                str(sidecar_checksum),
                int(sidecar_bytes),
                int(file_fingerprint[0]),
                int(file_fingerprint[1]),
                int(file_fingerprint[2]),
                int(file_fingerprint[3]),
                int(row_count),
                float(time.time()),
            ),
        )
        connection.commit()
        if dependency_sidecar_file_fingerprint(sidecar_path) != file_fingerprint:
            return err_result("SIDECAR_MISMATCH", "dependency sidecar file changed during index build")
        connection.close()
        connection = None
        os.replace(temp_path, index_path)
    except _DependencySidecarStreamError as exc:
        return err_result(exc.code, exc.message)
    except (OSError, sqlite3.Error) as exc:
        return err_result("INVALID_ARG", f"sidecar index build failed: {exc}")
    finally:
        if connection is not None:
            connection.close()
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
    build_seconds = time.perf_counter() - started
    opened = _open_matching_index(
        index_path,
        sidecar_path=sidecar_path,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
        sidecar_checksum=sidecar_checksum,
        file_fingerprint=file_fingerprint,
    )
    if opened is None:
        return err_result("SIDECAR_MISMATCH", "sidecar index build did not produce an index")
    if not opened.ok:
        return opened
    return ok_result(
        SidecarIndexHandle(
            **{
                **opened.data.__dict__,
                "build_seconds": float(build_seconds),
                "bytes_scanned": int(opened.data.sidecar_bytes),
                "built": True,
            }
        )
    )


def build_or_open_sidecar_index(
    path: str | Path,
    *,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    sidecar_checksum: str,
    file_fingerprint: tuple[int, int, int, int],
    dictionary_checksum: str | None = None,
    index_path: str | Path | None = None,
    rebuild_on_mismatch: bool = False,
    ticket_path: str | Path | None = None,
    write_ticket: bool = True,
) -> Result[SidecarIndexHandle]:
    sidecar_path = Path(path).expanduser().resolve()
    expected_dictionary_checksum = str(dictionary_checksum or "")
    try:
        expected_fingerprint = _normalize_fingerprint(file_fingerprint)
        if dependency_sidecar_file_fingerprint(sidecar_path) != expected_fingerprint:
            return err_result("SIDECAR_MISMATCH", "dependency sidecar file changed after validation")
    except FileNotFoundError:
        return err_result("INVALID_ARG", f"dependency sidecar not found: {sidecar_path}")
    except (OSError, ValueError) as exc:
        return err_result("INVALID_ARG", f"dependency sidecar stat failed: {exc}")

    primary_index_path = Path(index_path).expanduser().resolve() if index_path is not None else sidecar_index_path_for_source(sidecar_path)
    candidate_paths = [primary_index_path]
    if index_path is None:
        fallback_path = sidecar_index_path_for_source(
            sidecar_path,
            sidecar_checksum=sidecar_checksum,
            use_temp_dir=True,
        )
        if fallback_path != primary_index_path:
            candidate_paths.append(fallback_path)

    last_error: Result[SidecarIndexHandle] | None = None
    for candidate_path in candidate_paths:
        existing = _open_matching_index(
            candidate_path,
            sidecar_path=sidecar_path,
            expected_snapshot_id=expected_snapshot_id,
            expected_trace_checksum=expected_trace_checksum,
            expected_dictionary_checksum=expected_dictionary_checksum,
            sidecar_checksum=sidecar_checksum,
            file_fingerprint=expected_fingerprint,
        )
        if existing is not None:
            if existing.ok:
                return _write_ticket_result(existing.data, ticket_path=ticket_path, enabled=write_ticket)
            if not rebuild_on_mismatch:
                return existing
        built = _build_index_at_path(
            candidate_path,
            sidecar_path=sidecar_path,
            expected_snapshot_id=expected_snapshot_id,
            expected_trace_checksum=expected_trace_checksum,
            expected_dictionary_checksum=expected_dictionary_checksum,
            sidecar_checksum=sidecar_checksum,
            file_fingerprint=expected_fingerprint,
        )
        if built.ok:
            return _write_ticket_result(built.data, ticket_path=ticket_path, enabled=write_ticket)
        last_error = built
        if index_path is not None:
            return built
    if last_error is not None:
        return last_error
    return err_result("INVALID_ARG", "sidecar index path resolution failed")


def _metadata_created_at(metadata: dict[str, Any]) -> str:
    created_at_unix = float(metadata.get("created_at_unix") or 0.0)
    if created_at_unix <= 0.0:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return datetime.fromtimestamp(created_at_unix, timezone.utc).replace(microsecond=0).isoformat()


def sidecar_index_ticket_from_handle(
    handle: SidecarIndexHandle,
    *,
    created_at: str | None = None,
) -> SidecarIndexTicket:
    return SidecarIndexTicket(
        ticket_version=SIDECAR_INDEX_TICKET_VERSION,
        index_backend=SIDECAR_INDEX_BACKEND,
        index_path=Path(handle.path).expanduser().resolve(),
        sidecar_path=Path(handle.sidecar_path).expanduser().resolve(),
        sidecar_checksum=str(handle.sidecar_checksum),
        sidecar_bytes=int(handle.sidecar_bytes),
        file_fingerprint=_normalize_fingerprint(handle.file_fingerprint),
        row_count=int(handle.row_count),
        snapshot_id=str(handle.snapshot_id),
        trace_checksum=str(handle.trace_checksum),
        dictionary_checksum=str(handle.dictionary_checksum or ""),
        schema_version=SIDECAR_INDEX_SCHEMA_VERSION,
        created_at=created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        index_build_seconds=float(handle.build_seconds),
        build_host_platform=platform.platform(),
        selector_mode=str(handle.selector_mode),
    )


def _ticket_from_payload(payload: dict[str, Any]) -> SidecarIndexTicket:
    return SidecarIndexTicket(
        ticket_version=str(payload["ticket_version"]),
        index_backend=str(payload["index_backend"]),
        index_path=Path(str(payload["index_path"])).expanduser().resolve(),
        sidecar_path=Path(str(payload["sidecar_path"])).expanduser().resolve(),
        sidecar_checksum=str(payload["sidecar_checksum"]),
        sidecar_bytes=int(payload["sidecar_bytes"]),
        file_fingerprint=_normalize_fingerprint(payload["file_fingerprint"]),
        row_count=int(payload["row_count"]),
        snapshot_id=str(payload["snapshot_id"]),
        trace_checksum=str(payload["trace_checksum"]),
        dictionary_checksum=str(payload.get("dictionary_checksum") or ""),
        schema_version=int(payload["schema_version"]),
        created_at=str(payload["created_at"]),
        index_build_seconds=float(payload.get("index_build_seconds") or 0.0),
        build_host_platform=str(payload.get("build_host_platform") or ""),
        selector_mode=str(payload.get("selector_mode") or SIDECAR_INDEX_SELECTOR_MODE),
    )


def read_sidecar_index_ticket(path: str | Path) -> Result[SidecarIndexTicket]:
    source = Path(path).expanduser().resolve()
    try:
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket must be a JSON object")
        return ok_result(_ticket_from_payload(payload))
    except FileNotFoundError:
        return err_result("ERR-SIDECAR_INDEX_MISSING", f"sidecar index ticket not found: {source}")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", f"sidecar index ticket invalid: {exc}")


def write_sidecar_index_ticket(
    handle: SidecarIndexHandle,
    *,
    ticket_path: str | Path | None = None,
) -> Result[SidecarIndexTicket]:
    written = _write_sidecar_index_ticket_with_telemetry(handle, ticket_path=ticket_path)
    if not written.ok:
        return err_result(written.code, written.message)
    return ok_result(written.data[0])


def _write_sidecar_index_ticket_with_telemetry(
    handle: SidecarIndexHandle,
    *,
    ticket_path: str | Path | None = None,
) -> Result[tuple[SidecarIndexTicket, float, bool]]:
    target = (
        Path(ticket_path).expanduser().resolve()
        if ticket_path is not None
        else sidecar_index_ticket_path_for_source(handle.sidecar_path, index_path=handle.path)
    )
    existing = read_sidecar_index_ticket(target)
    if existing.ok and _ticket_matches_handle(existing.data, handle):
        return ok_result((existing.data, 0.0, True))
    ticket = sidecar_index_ticket_from_handle(handle)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with target.open("w", encoding="utf-8") as output:
            json.dump(ticket.to_dict(), output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        write_seconds = time.perf_counter() - started
    except OSError as exc:
        return err_result("INVALID_ARG", f"sidecar index ticket write failed: {exc}")
    return ok_result((ticket, write_seconds, False))


def _ticket_matches_handle(ticket: SidecarIndexTicket, handle: SidecarIndexHandle) -> bool:
    return (
        str(ticket.ticket_version) == SIDECAR_INDEX_TICKET_VERSION
        and str(ticket.index_backend) == SIDECAR_INDEX_BACKEND
        and Path(ticket.index_path).expanduser().resolve() == Path(handle.path).expanduser().resolve()
        and Path(ticket.sidecar_path).expanduser().resolve() == Path(handle.sidecar_path).expanduser().resolve()
        and str(ticket.sidecar_checksum) == str(handle.sidecar_checksum)
        and int(ticket.sidecar_bytes) == int(handle.sidecar_bytes)
        and tuple(ticket.file_fingerprint) == tuple(handle.file_fingerprint)
        and int(ticket.row_count) == int(handle.row_count)
        and str(ticket.snapshot_id) == str(handle.snapshot_id)
        and str(ticket.trace_checksum) == str(handle.trace_checksum)
        and str(ticket.dictionary_checksum or "") == str(handle.dictionary_checksum or "")
        and int(ticket.schema_version) == SIDECAR_INDEX_SCHEMA_VERSION
        and str(ticket.selector_mode) == str(handle.selector_mode)
    )


def _write_ticket_result(
    handle: SidecarIndexHandle,
    *,
    ticket_path: str | Path | None,
    enabled: bool,
) -> Result[SidecarIndexHandle]:
    if not enabled:
        return ok_result(handle)
    written = _write_sidecar_index_ticket_with_telemetry(handle, ticket_path=ticket_path)
    if not written.ok:
        return err_result(written.code, written.message)
    _ticket, write_seconds, reused = written.data
    return ok_result(replace(handle, ticket_write_seconds=float(write_seconds), ticket_reused=bool(reused)))


def validate_sidecar_index_ticket(
    ticket: SidecarIndexTicket,
    *,
    sidecar_path: str | Path,
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
    sidecar_checksum: str,
    index_path: str | Path | None = None,
) -> Result[SidecarIndexTicket]:
    expected_sidecar_path = Path(sidecar_path).expanduser().resolve()
    expected_index_path = (
        Path(index_path).expanduser().resolve()
        if index_path is not None
        else sidecar_index_path_for_source(expected_sidecar_path)
    )
    if ticket.ticket_version != SIDECAR_INDEX_TICKET_VERSION:
        return err_result("ERR-SIDECAR_INDEX_SCHEMA_MISMATCH", "sidecar index ticket version mismatch")
    if ticket.index_backend != SIDECAR_INDEX_BACKEND:
        return err_result("ERR-SIDECAR_INDEX_SCHEMA_MISMATCH", "sidecar index backend mismatch")
    if int(ticket.schema_version) != SIDECAR_INDEX_SCHEMA_VERSION:
        return err_result("ERR-SIDECAR_INDEX_SCHEMA_MISMATCH", "sidecar index ticket schema version mismatch")
    if Path(ticket.sidecar_path).expanduser().resolve() != expected_sidecar_path:
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket sidecar path mismatch")
    if Path(ticket.index_path).expanduser().resolve() != expected_index_path:
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket index path mismatch")
    if not expected_index_path.exists():
        return err_result("ERR-SIDECAR_INDEX_MISSING", f"sidecar index not found: {expected_index_path}")
    if str(ticket.snapshot_id) != str(expected_snapshot_id):
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket snapshot_id mismatch")
    if str(ticket.trace_checksum) != str(expected_trace_checksum):
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket trace checksum mismatch")
    if str(ticket.dictionary_checksum) != str(expected_dictionary_checksum):
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket dictionary checksum mismatch")
    if str(ticket.sidecar_checksum) != str(sidecar_checksum):
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket sidecar checksum mismatch")
    try:
        fingerprint = dependency_sidecar_file_fingerprint(expected_sidecar_path)
        sidecar_bytes = dependency_sidecar_size_bytes(expected_sidecar_path)
    except FileNotFoundError:
        return err_result("ERR-SIDECAR_INDEX_MISSING", f"dependency sidecar not found: {expected_sidecar_path}")
    except OSError as exc:
        return err_result("ERR-SIDECAR_INDEX_STALE", f"dependency sidecar stat failed: {exc}")
    if tuple(ticket.file_fingerprint) != tuple(fingerprint):
        return err_result("ERR-SIDECAR_INDEX_STALE", "sidecar index ticket file fingerprint mismatch")
    if int(ticket.sidecar_bytes) != int(sidecar_bytes):
        return err_result("ERR-SIDECAR_INDEX_STALE", "sidecar index ticket sidecar byte size mismatch")
    existing = _open_matching_index(
        expected_index_path,
        sidecar_path=expected_sidecar_path,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
        sidecar_checksum=sidecar_checksum,
        file_fingerprint=fingerprint,
    )
    if existing is None:
        return err_result("ERR-SIDECAR_INDEX_MISSING", f"sidecar index not found: {expected_index_path}")
    if not existing.ok:
        code = existing.code if str(existing.code).startswith("ERR-") else "ERR-SIDECAR_INDEX_MISMATCH"
        return err_result(code, existing.message)
    if int(existing.data.row_count) != int(ticket.row_count):
        return err_result("ERR-SIDECAR_INDEX_MISMATCH", "sidecar index ticket row_count mismatch")
    return ok_result(ticket)


def _edge_from_index_row(row: sqlite3.Row) -> DependencySidecarEdge:
    return DependencySidecarEdge(
        snapshot_id=str(row["snapshot_id"]),
        trace_checksum=str(row["trace_checksum"]),
        src_ref=str(row["src_ref"]),
        dst_ref=str(row["dst_ref"]),
        src_kind=str(row["src_kind"]),
        dst_kind=str(row["dst_kind"]),
        relation_kind=str(row["relation_kind"]),
        rule_family=str(row["rule_family"]),
        provenance=str(row["provenance"]),
        priority=int(row["priority"]),
        time_hint_begin_ns=int(row["time_hint_begin_ns"]),
        time_hint_end_ns=int(row["time_hint_end_ns"]),
        core_hint=None if row["core_hint"] is None else int(row["core_hint"]),
        seq_hint_begin=None if row["seq_hint_begin"] is None else int(row["seq_hint_begin"]),
        seq_hint_end=None if row["seq_hint_end"] is None else int(row["seq_hint_end"]),
        segment_hint=None if row["segment_hint"] is None else str(row["segment_hint"]),
        cycle_guard_token=str(row["cycle_guard_token"]),
        estimate_events=int(row["estimate_events"]),
        estimate_bytes=int(row["estimate_bytes"]),
        edge_hash=str(row["edge_hash"]),
    )


def _dedupe_text(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _chunks(values: list[str], chunk_size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), max(1, int(chunk_size))):
        yield values[start : start + max(1, int(chunk_size))]


def select_candidate_edges_from_index(
    index: SidecarIndexHandle,
    frontier_refs: list[str],
    rule_families: tuple[str, ...],
    *,
    expected_sidecar_checksum: str | None = None,
    expected_file_fingerprint: tuple[int, int, int, int] | None = None,
) -> Result[list[DependencySidecarEdge]]:
    frontier = _dedupe_text(frontier_refs)
    allowed = _dedupe_text(rule_families)
    if not frontier or not allowed:
        return ok_result([])
    if expected_sidecar_checksum is not None and str(expected_sidecar_checksum) != str(index.sidecar_checksum):
        return err_result("SIDECAR_MISMATCH", "sidecar index checksum binding mismatch")
    if expected_file_fingerprint is not None and tuple(expected_file_fingerprint) != tuple(index.file_fingerprint):
        return err_result("SIDECAR_MISMATCH", "sidecar index fingerprint binding mismatch")
    try:
        if dependency_sidecar_file_fingerprint(index.sidecar_path) != tuple(index.file_fingerprint):
            return err_result("SIDECAR_MISMATCH", "dependency sidecar file changed after index build")
    except FileNotFoundError:
        return err_result("INVALID_ARG", f"dependency sidecar not found: {index.sidecar_path}")
    except OSError as exc:
        return err_result("INVALID_ARG", f"dependency sidecar stat failed: {exc}")

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(index.path, readonly=True)
        metadata_result = _read_metadata(connection)
        if not metadata_result.ok:
            return metadata_result
        metadata = metadata_result.data
        if not _metadata_matches(
            metadata,
            sidecar_path=index.sidecar_path,
            expected_snapshot_id=index.snapshot_id,
            expected_trace_checksum=index.trace_checksum,
            expected_dictionary_checksum=index.dictionary_checksum,
            sidecar_checksum=index.sidecar_checksum,
            file_fingerprint=index.file_fingerprint,
        ):
            return err_result("SIDECAR_MISMATCH", "sidecar index metadata mismatch")
        matches: list[DependencySidecarEdge] = []
        rule_placeholders = ", ".join("?" for _ in allowed)
        chunk_size = max(1, _SQLITE_PARAM_LIMIT - len(allowed))
        for frontier_chunk in _chunks(frontier, chunk_size):
            frontier_placeholders = ", ".join("?" for _ in frontier_chunk)
            rows = connection.execute(
                f"""
                SELECT
                    edge_hash,
                    snapshot_id,
                    trace_checksum,
                    src_ref,
                    dst_ref,
                    src_kind,
                    dst_kind,
                    relation_kind,
                    rule_family,
                    provenance,
                    priority,
                    time_hint_begin_ns,
                    time_hint_end_ns,
                    core_hint,
                    seq_hint_begin,
                    seq_hint_end,
                    segment_hint,
                    cycle_guard_token,
                    estimate_events,
                    estimate_bytes
                FROM edges
                WHERE src_ref IN ({frontier_placeholders})
                  AND rule_family IN ({rule_placeholders})
                """,
                tuple(frontier_chunk) + tuple(allowed),
            ).fetchall()
            matches.extend(_edge_from_index_row(row) for row in rows)
    except sqlite3.Error as exc:
        return err_result("INVALID_ARG", f"sidecar index query failed: {exc}")
    finally:
        if connection is not None:
            connection.close()
    return ok_result(sort_sidecar_edges(matches))


def select_candidate_edges_from_segment_manifest(
    segment_manifest: dict[str, Any],
    *,
    manifest_root: str | Path,
    frontier_refs: list[str],
    rule_families: tuple[str, ...],
    expected_snapshot_id: str,
    expected_trace_checksum: str,
    expected_dictionary_checksum: str,
    expected_segment_checksums: dict[str, str] | None = None,
) -> Result[list[DependencySidecarEdge]]:
    frontier = _dedupe_text(frontier_refs)
    allowed = _dedupe_text(rule_families)
    if not frontier or not allowed:
        return ok_result([])
    validated = validate_sidecar_segment_manifest_metadata(
        segment_manifest,
        expected_snapshot_id=expected_snapshot_id,
        expected_trace_checksum=expected_trace_checksum,
        expected_dictionary_checksum=expected_dictionary_checksum,
        manifest_root=manifest_root,
    )
    if not validated.ok:
        return validated
    root = Path(manifest_root)
    expected_checksums = {
        str(segment_id).strip(): str(checksum).strip()
        for segment_id, checksum in dict(expected_segment_checksums or {}).items()
        if str(segment_id).strip()
    }
    deduped: dict[str, DependencySidecarEdge] = {}
    for row in list(segment_manifest.get("segments") or []):
        segment_id = str(row.get("segment_id") or "").strip()
        manifest_checksum = str(row.get("checksum") or "").strip()
        if expected_checksums and expected_checksums.get(segment_id, manifest_checksum) != manifest_checksum:
            return err_result("SIDECAR_MISMATCH", f"sidecar segment checksum binding mismatch: {segment_id}")
        sidecar_path = (root / str(row.get("sidecar_path") or "")).resolve()
        index_path = (root / str(row.get("index_path") or "")).resolve()
        ticket_path_value = str(row.get("ticket_path") or "").strip()
        ticket_path = (
            (root / ticket_path_value).resolve()
            if ticket_path_value
            else sidecar_index_ticket_path_for_source(sidecar_path, index_path=index_path)
        )
        try:
            file_fingerprint = dependency_sidecar_file_fingerprint(sidecar_path)
        except FileNotFoundError:
            return err_result("INVALID_ARG", f"dependency sidecar not found: {sidecar_path}")
        except OSError as exc:
            return err_result("INVALID_ARG", f"dependency sidecar stat failed: {exc}")
        indexed = build_or_open_sidecar_index(
            sidecar_path,
            expected_snapshot_id=expected_snapshot_id,
            expected_trace_checksum=expected_trace_checksum,
            sidecar_checksum=manifest_checksum,
            file_fingerprint=file_fingerprint,
            dictionary_checksum=expected_dictionary_checksum,
            index_path=index_path,
            ticket_path=ticket_path,
            rebuild_on_mismatch=True,
        )
        if indexed.ok:
            selected = select_candidate_edges_from_index(
                indexed.data,
                frontier,
                tuple(allowed),
                expected_sidecar_checksum=manifest_checksum,
                expected_file_fingerprint=file_fingerprint,
            )
        else:
            selected = select_candidate_edges_from_sidecar(
                sidecar_path,
                frontier,
                tuple(allowed),
                file_fingerprint=file_fingerprint,
            )
        if not selected.ok:
            return selected
        for edge in selected.data:
            deduped.setdefault(edge.edge_hash, edge)
    return ok_result(sort_sidecar_edges(list(deduped.values())))

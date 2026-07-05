from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from parser.agent_contract import (
    AGENT_ARTIFACT_WRITTEN,
    AGENT_RUNNING,
    AGENT_VALIDATING_INPUT,
    AgentJobContract,
)
from parser.evidence_sidecar import (
    dependency_sidecar_file_fingerprint,
    dependency_sidecar_size_bytes,
    validate_dependency_sidecar_stream,
)
from parser.evidence_sidecar_index import (
    SidecarIndexHandle,
    build_or_open_sidecar_index,
    read_sidecar_index_ticket,
    sidecar_index_path_for_source,
    sidecar_index_ticket_path_for_source,
)
from parser.result import Result, ok_result


SIDECAR_INDEX_AGENT_NAME = "SidecarIndexAgent"


def dependency_sidecar_checksum_from_manifest(manifest: dict[str, Any]) -> str:
    entry_checksums = {
        str(rel_path).strip(): str(checksum).strip()
        for rel_path, checksum in dict(manifest.get("entry_checksums") or {}).items()
        if str(rel_path).strip()
    }
    for rel_path in list(manifest.get("entry_paths") or []) + list(entry_checksums):
        text = str(rel_path).strip()
        if Path(text).name == "dependency_sidecar.jsonl" and entry_checksums.get(text):
            return str(entry_checksums[text])
    return ""


class SidecarIndexAgent:
    def __init__(self, *, job_id: str | None = None) -> None:
        self.contract = AgentJobContract(agent_name=SIDECAR_INDEX_AGENT_NAME, job_id=job_id)

    def build_or_open(
        self,
        *,
        sidecar_path: str | Path,
        sidecar_manifest: dict[str, Any],
        expected_snapshot_id: str | None = None,
        expected_trace_checksum: str | None = None,
        expected_dictionary_checksum: str | None = None,
        index_path: str | Path | None = None,
        ticket_path: str | Path | None = None,
        rebuild_on_mismatch: bool = True,
        validate_stream: bool = True,
    ) -> Result[dict[str, Any]]:
        started = time.perf_counter()
        self.contract.transition(AGENT_VALIDATING_INPUT)
        source = Path(sidecar_path).expanduser().resolve()
        snapshot_id = str(expected_snapshot_id or sidecar_manifest.get("snapshot_id") or "")
        trace_checksum = str(expected_trace_checksum or sidecar_manifest.get("trace_checksum") or "")
        dictionary_checksum = str(expected_dictionary_checksum or sidecar_manifest.get("dictionary_checksum") or "")
        sidecar_checksum = dependency_sidecar_checksum_from_manifest(sidecar_manifest)
        if not snapshot_id or not trace_checksum or not dictionary_checksum or not sidecar_checksum:
            message = "sidecar index agent requires snapshot, trace, dictionary and sidecar checksum bindings"
            self.contract.fail("ERR-SIDECAR_INDEX_MISMATCH", message)
            return Result("ERR-SIDECAR_INDEX_MISMATCH", message, data={"agent_contract": self.contract.to_dict()})
        try:
            fingerprint = dependency_sidecar_file_fingerprint(source)
            sidecar_bytes = dependency_sidecar_size_bytes(source)
        except FileNotFoundError:
            message = f"dependency sidecar not found: {source}"
            self.contract.fail("ERR-SIDECAR_INDEX_MISSING", message)
            return Result("ERR-SIDECAR_INDEX_MISSING", message, data={"agent_contract": self.contract.to_dict()})
        except OSError as exc:
            message = f"dependency sidecar stat failed: {exc}"
            self.contract.fail("ERR-SIDECAR_INDEX_STALE", message)
            return Result("ERR-SIDECAR_INDEX_STALE", message, data={"agent_contract": self.contract.to_dict()})

        row_count: int | None = None
        if validate_stream:
            validated = validate_dependency_sidecar_stream(
                source,
                sidecar_manifest,
                expected_snapshot_id=snapshot_id,
                expected_trace_checksum=trace_checksum,
            )
            if not validated.ok:
                self.contract.fail(str(validated.code or "ERR-SIDECAR_INDEX_MISMATCH"), validated.message)
                return Result(
                    code=validated.code,
                    message=validated.message,
                    data={"agent_contract": self.contract.to_dict()},
                    warnings=validated.warnings,
                    untrusted_windows=validated.untrusted_windows,
                )
            fingerprint = tuple(validated.data["file_fingerprint"])
            sidecar_checksum = str(validated.data["checksum"])
            row_count = int(validated.data.get("row_count") or 0)

        resolved_index_path = (
            Path(index_path).expanduser().resolve()
            if index_path is not None
            else sidecar_index_path_for_source(source, sidecar_checksum=sidecar_checksum)
        )
        resolved_ticket_path = (
            Path(ticket_path).expanduser().resolve()
            if ticket_path is not None
            else sidecar_index_ticket_path_for_source(source, index_path=resolved_index_path)
        )

        self.contract.transition(
            AGENT_RUNNING,
            sidecar_bytes=int(sidecar_bytes),
            sidecar_row_count=row_count,
            index_path=str(resolved_index_path),
            ticket_path=str(resolved_ticket_path),
        )
        indexed = build_or_open_sidecar_index(
            source,
            expected_snapshot_id=snapshot_id,
            expected_trace_checksum=trace_checksum,
            sidecar_checksum=sidecar_checksum,
            file_fingerprint=fingerprint,
            dictionary_checksum=dictionary_checksum,
            index_path=resolved_index_path,
            ticket_path=resolved_ticket_path,
            rebuild_on_mismatch=rebuild_on_mismatch,
        )
        if not indexed.ok:
            code = str(indexed.code or "ERR-SIDECAR_INDEX_MISMATCH")
            self.contract.fail(code, indexed.message)
            return Result(
                code=indexed.code,
                message=indexed.message,
                data={"agent_contract": self.contract.to_dict()},
                warnings=indexed.warnings,
                untrusted_windows=indexed.untrusted_windows,
            )

        handle: SidecarIndexHandle = indexed.data
        self.contract.transition(AGENT_ARTIFACT_WRITTEN)
        self.contract.add_artifact(handle.path, kind="sqlite", role="sidecar_index")
        self.contract.add_artifact(resolved_ticket_path, kind="json", role="sidecar_index_ticket")
        ticket = read_sidecar_index_ticket(resolved_ticket_path)
        self.contract.complete(
            sidecar_bytes=int(handle.sidecar_bytes),
            sidecar_row_count=int(handle.row_count),
            sidecar_bytes_scanned=int(handle.bytes_scanned),
            index_build_open_seconds=round(time.perf_counter() - started, 6),
            index_reused=not bool(handle.built),
            index_rebuilt=bool(handle.built),
        )
        return ok_result(
            {
                "index_path": str(handle.path),
                "ticket_path": str(resolved_ticket_path),
                "ticket": ticket.data.to_dict() if ticket.ok else None,
                "handle": handle,
                "agent_contract": self.contract.to_dict(),
            }
        )

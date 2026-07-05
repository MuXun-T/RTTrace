from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Iterable

from parser.agent_contract import (
    AGENT_ARTIFACT_WRITTEN,
    AGENT_RUNNING,
    AGENT_VALIDATING_INPUT,
    ERR_PACKAGE_WRITE_FAILED,
    AgentJobContract,
)
from parser.result import Result, ok_result
from spec.io import serialize


EXPORT_WRITE_AGENT_NAME = "ExportWriteAgent"
EXPORT_WRITE_METRIC_VERSION = "export-write-metric-v1"
WRITE_FAILURE_BLOCKER_VERSION = "write-failure-blocker-v1"
PACKAGE_WRITE_RESULT_VERSION = "evidence-package-write-result-v1"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_safe(value: Any) -> Any:
    value = serialize(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": type(value).__name__, "byte_count": len(value)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


@dataclass(frozen=True)
class ExportWriteMetric:
    metric_version: str
    path: str
    label: str
    write_mode: str
    count: int
    bytes_written: int
    checksum: str
    write_seconds: float
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_version": self.metric_version,
            "path": self.path,
            "label": self.label,
            "write_mode": self.write_mode,
            "count": int(self.count),
            "bytes_written": int(self.bytes_written),
            "checksum": self.checksum,
            "write_seconds": float(self.write_seconds),
            "ok": bool(self.ok),
        }


@dataclass(frozen=True)
class WriteFailureBlocker:
    blocker_version: str
    snapshot_id: str
    package_path: str
    failed_path: str
    error_code: str
    error_message: str
    partial_write_status: dict[str, Any]
    emitted_at: str
    artifact_path: str | None = None
    artifact_write_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocker_version": self.blocker_version,
            "snapshot_id": self.snapshot_id,
            "package_path": self.package_path,
            "failed_path": self.failed_path,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "partial_write_status": _json_safe(dict(self.partial_write_status)),
            "emitted_at": self.emitted_at,
            "artifact_path": self.artifact_path,
            "artifact_write_error": self.artifact_write_error,
        }


class _HashingTextWriter:
    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.bytes_written = 0

    def write(self, text: str) -> None:
        encoded = text.encode("utf-8")
        self.digest.update(encoded)
        self.bytes_written += len(encoded)
        self.handle.write(text)

    def hexdigest(self) -> str:
        return self.digest.hexdigest()


class ExportWriteAgent:
    def __init__(self, *, job_id: str | None = None) -> None:
        self.contract = AgentJobContract(agent_name=EXPORT_WRITE_AGENT_NAME, job_id=job_id)
        self.write_metrics: list[dict[str, Any]] = []

    def consume_metrics(self) -> list[dict[str, Any]]:
        metrics = list(self.write_metrics)
        self.write_metrics = []
        return metrics

    def write_json(
        self,
        path: str | Path,
        data: Any,
        *,
        label: str | None = None,
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
    ) -> Result[dict[str, Any]]:
        target = Path(path)
        return self._write(
            target,
            mode="standard_json",
            label=label,
            package_root=package_root,
            snapshot_id=snapshot_id,
            write_callback=lambda writer: self._write_json_payload(writer, data),
        )

    def stream_jsonl(
        self,
        path: str | Path,
        rows: Iterable[Any],
        *,
        label: str | None = None,
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
    ) -> Result[dict[str, Any]]:
        target = Path(path)

        def _callback(writer: _HashingTextWriter) -> int:
            count = 0
            for row in rows:
                writer.write(json.dumps(serialize(row), ensure_ascii=False, sort_keys=True))
                writer.write("\n")
                count += 1
            return count

        return self._write(
            target,
            mode="stream_jsonl",
            label=label,
            package_root=package_root,
            snapshot_id=snapshot_id,
            write_callback=_callback,
        )

    def stream_json_array(
        self,
        path: str | Path,
        rows: Iterable[Any],
        *,
        label: str | None = None,
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
    ) -> Result[dict[str, Any]]:
        target = Path(path)

        def _callback(writer: _HashingTextWriter) -> int:
            writer.write("[")
            count = 0
            for row in rows:
                if count == 0:
                    writer.write("\n")
                else:
                    writer.write(",\n")
                payload = json.dumps(serialize(row), ensure_ascii=False, indent=2, sort_keys=True)
                writer.write("  " + payload.replace("\n", "\n  "))
                count += 1
            if count:
                writer.write("\n")
            writer.write("]\n")
            return count

        return self._write(
            target,
            mode="stream_json_array",
            label=label,
            package_root=package_root,
            snapshot_id=snapshot_id,
            write_callback=_callback,
        )

    def write_bytes(
        self,
        path: str | Path,
        data: bytes | bytearray | memoryview | str | None,
        *,
        label: str | None = None,
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
        count: int = 1,
    ) -> Result[dict[str, Any]]:
        target = Path(path)

        def _callback(target_path: Path) -> int:
            if data is None:
                payload = b""
            elif isinstance(data, str):
                payload = data.encode("utf-8")
            else:
                payload = bytes(data)
            target_path.write_bytes(payload)
            return int(count)

        return self._write_binary(
            target,
            mode="write_bytes",
            label=label,
            write_callback=_callback,
            package_root=package_root,
            snapshot_id=snapshot_id,
        )

    def copy_file(
        self,
        source_path: str | Path,
        path: str | Path,
        *,
        label: str | None = None,
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
        count: int = 1,
    ) -> Result[dict[str, Any]]:
        target = Path(path)

        def _callback(target_path: Path) -> int:
            source = Path(source_path)
            shutil.copy2(source, target_path)
            return int(count)

        return self._write_binary(
            target,
            mode="copy_file",
            label=label,
            write_callback=_callback,
            package_root=package_root,
            snapshot_id=snapshot_id,
        )

    def write_binary_callback(
        self,
        path: str | Path,
        write_callback: Callable[[Path], Any],
        *,
        label: str | None = None,
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
        count: int | None = None,
    ) -> Result[dict[str, Any]]:
        target = Path(path)

        def _callback(target_path: Path) -> int:
            callback_result = write_callback(target_path)
            if count is not None:
                return int(count)
            if isinstance(callback_result, int):
                return int(callback_result)
            return 1

        return self._write_binary(
            target,
            mode="binary_callback",
            label=label,
            write_callback=_callback,
            package_root=package_root,
            snapshot_id=snapshot_id,
        )

    def write_evidence_package_optimized(
        self,
        package_payload: dict[str, Any],
        write_policy: dict[str, Any] | None = None,
    ) -> Result[dict[str, Any]]:
        policy: dict[str, Any] = {}
        payload: dict[str, Any] = {}
        package_root = Path(".").expanduser().resolve()
        snapshot_id = "unknown"
        metrics: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        try:
            policy = dict(write_policy or {})
            payload = dict(package_payload)
            package_root = Path(policy.get("package_path") or payload.get("package_path") or ".").expanduser().resolve()
            snapshot_id = str(policy.get("snapshot_id") or payload.get("snapshot_id") or "unknown")
            default_mode = str(policy.get("default_write_mode") or "standard_json")
            continue_on_error = bool(policy.get("continue_on_error", False))
            entries = [dict(entry) for entry in list(payload.get("entries") or payload.get("files") or [])]
        except Exception as exc:
            message = f"optimized package write payload validation failed: {exc}"
            return self.package_write_failure_result(
                package_root,
                failed_path=package_root / "control" / "package_payload",
                snapshot_id=snapshot_id,
                error_code=ERR_PACKAGE_WRITE_FAILED,
                error_message=message,
                metrics=metrics,
                blockers=blockers,
                partial_write_status={"stage": "normalize_entries"},
            )
        self.contract.add_input_ref(package_root, kind="directory", role="package_root")
        self.contract.transition(AGENT_VALIDATING_INPUT, package_path=str(package_root), entry_count=len(entries))
        if not entries:
            message = "optimized package write requires at least one entry"
            return self.package_write_failure_result(
                package_root,
                failed_path=package_root / "control" / "missing_entry_path",
                snapshot_id=snapshot_id,
                error_code=ERR_PACKAGE_WRITE_FAILED,
                error_message=message,
                metrics=metrics,
                blockers=blockers,
                partial_write_status={"entry_count": 0},
            )

        self.contract.transition(AGENT_RUNNING)
        for entry in entries:
            relative_path = entry.get("relative_path") or entry.get("path")
            if not relative_path:
                message = "package write entry missing relative_path"
                blocker = self._write_failure_blocker_for_path(
                    package_root / "control" / "missing_entry_path",
                    package_root=package_root,
                    snapshot_id=snapshot_id,
                    error_code=ERR_PACKAGE_WRITE_FAILED,
                    error_message=message,
                    partial_write_status={"entry": entry},
                )
                blockers.append(blocker.to_dict())
                if not continue_on_error:
                    return self.package_write_failure_result(
                        package_root,
                        failed_path=package_root / "control" / "missing_entry_path",
                        snapshot_id=snapshot_id,
                        error_code=ERR_PACKAGE_WRITE_FAILED,
                        error_message=message,
                        metrics=metrics,
                        blockers=blockers,
                        append_blocker=False,
                        partial_write_status={"entry": entry},
                    )
                continue
            target = Path(relative_path)
            if not target.is_absolute():
                target = package_root / target
            mode = str(entry.get("write_mode") or entry.get("mode") or default_mode)
            label = str(entry.get("label") or Path(relative_path).name)
            try:
                if mode == "stream_jsonl":
                    rows = entry.get("rows") if "rows" in entry else entry.get("payload")
                    result = self.stream_jsonl(
                        target,
                        rows or [],
                        label=label,
                        package_root=package_root,
                        snapshot_id=snapshot_id,
                    )
                elif mode == "stream_json_array":
                    rows = entry.get("rows") if "rows" in entry else entry.get("payload")
                    result = self.stream_json_array(
                        target,
                        rows or [],
                        label=label,
                        package_root=package_root,
                        snapshot_id=snapshot_id,
                    )
                elif mode == "standard_json":
                    result = self.write_json(
                        target,
                        entry.get("payload"),
                        label=label,
                        package_root=package_root,
                        snapshot_id=snapshot_id,
                    )
                elif mode == "write_bytes":
                    result = self.write_bytes(
                        target,
                        entry.get("payload", entry.get("data")),
                        label=label,
                        package_root=package_root,
                        snapshot_id=snapshot_id,
                        count=int(entry.get("count", 1)),
                    )
                elif mode == "copy_file":
                    result = self.copy_file(
                        entry.get("source_path") or entry.get("source"),
                        target,
                        label=label,
                        package_root=package_root,
                        snapshot_id=snapshot_id,
                        count=int(entry.get("count", 1)),
                    )
                elif mode == "binary_callback":
                    result = self.write_binary_callback(
                        target,
                        entry.get("callback") or entry.get("write_callback"),
                        label=label,
                        package_root=package_root,
                        snapshot_id=snapshot_id,
                        count=(None if "count" not in entry else int(entry.get("count", 1))),
                    )
                else:
                    raise ValueError(f"unsupported package write mode: {mode}")
            except Exception as exc:
                message = f"package write failed for {target}: {exc}"
                blocker = self._write_failure_blocker_for_path(
                    target,
                    package_root=package_root,
                    snapshot_id=snapshot_id,
                    error_code=ERR_PACKAGE_WRITE_FAILED,
                    error_message=message,
                    partial_write_status={"path": str(target), "write_mode": mode, "label": label, "entry": entry},
                )
                result = Result(ERR_PACKAGE_WRITE_FAILED, message, data={"write_failure_blocker": blocker.to_dict()})

            if result.ok:
                metrics.append(dict(result.data))
                continue
            blocker_payload = dict((result.data or {}).get("write_failure_blocker") or {})
            if blocker_payload:
                blockers.append(blocker_payload)
            if not continue_on_error:
                return self.package_write_failure_result(
                    package_root,
                    failed_path=target,
                    snapshot_id=snapshot_id,
                    error_code=result.code,
                    error_message=result.message,
                    metrics=metrics,
                    blockers=blockers,
                    append_blocker=False,
                    partial_write_status={"path": str(target), "write_mode": mode, "label": label},
                )

        if blockers:
            message = f"optimized package write completed with {len(blockers)} failure blocker(s)"
            return self.package_write_failure_result(
                package_root,
                failed_path=package_root / "control" / "write_failure_blocker.json",
                snapshot_id=snapshot_id,
                error_code=ERR_PACKAGE_WRITE_FAILED,
                error_message=message,
                metrics=metrics,
                blockers=blockers,
                append_blocker=False,
                partial_write_status={"write_count": len(metrics), "blocker_count": len(blockers)},
            )

        self.contract.transition(AGENT_ARTIFACT_WRITTEN)
        self.contract.add_artifact(package_root, kind="directory", role="evidence_package")
        self.contract.complete(package_path=str(package_root), write_count=len(metrics), blocker_count=len(blockers))
        return ok_result(
            {
                "package_result": self._package_result(package_root, metrics, blockers),
                "agent_contract": self.contract.to_dict(),
            }
        )

    def package_write_failure_result(
        self,
        package_root: str | Path,
        *,
        failed_path: str | Path,
        snapshot_id: str,
        error_code: str,
        error_message: str,
        metrics: list[dict[str, Any]] | None = None,
        blockers: list[dict[str, Any]] | None = None,
        partial_write_status: dict[str, Any] | None = None,
        append_blocker: bool = True,
    ) -> Result[dict[str, Any]]:
        root = Path(package_root).expanduser().resolve()
        blocker_payloads = [dict(blocker) for blocker in list(blockers or [])]
        if append_blocker or not blocker_payloads:
            blocker = self._write_failure_blocker_for_path(
                failed_path,
                package_root=root,
                snapshot_id=snapshot_id,
                error_code=error_code,
                error_message=error_message,
                partial_write_status=partial_write_status,
            )
            blocker_payloads.append(blocker.to_dict())
        self.contract.fail(error_code, error_message)
        return Result(
            error_code,
            error_message,
            data={
                "package_result": self._package_result(root, list(metrics or []), blocker_payloads),
                "agent_contract": self.contract.to_dict(),
            },
        )

    def write_blocker_artifact_for_failure(
        self,
        package_path: str | Path,
        *,
        snapshot_id: str,
        error_code: str,
        error_message: str,
        partial_write_status: dict[str, Any] | None = None,
    ) -> Result[dict[str, Any]]:
        blocker_path = Path(package_path) / "control" / "write_blocker_artifact.json"
        payload = {
            "blocker_version": WRITE_FAILURE_BLOCKER_VERSION,
            "snapshot_id": str(snapshot_id),
            "round_id": 0,
            "closure_mode": "degraded",
            "halt_reason": str(error_code),
            "exception_code": str(error_code),
            "exception_message": str(error_message),
            "candidate_edge_sample": [],
            "window_plan_summary": {},
            "partial_write_status": dict(partial_write_status or {}),
            "emitted_metrics": {},
        }
        return self.write_json(blocker_path, payload, label="write_blocker_artifact")

    def _write_json_payload(self, writer: _HashingTextWriter, data: Any) -> int:
        writer.write(json.dumps(serialize(data), ensure_ascii=False, indent=2, sort_keys=True))
        writer.write("\n")
        if isinstance(data, list):
            return len(data)
        return 1

    def _write(
        self,
        path: Path,
        *,
        mode: str,
        label: str | None,
        write_callback: Any,
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
    ) -> Result[dict[str, Any]]:
        started = time.perf_counter()
        self.contract.transition(AGENT_VALIDATING_INPUT)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.contract.transition(AGENT_RUNNING, write_mode=mode)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = _HashingTextWriter(handle)
                count = int(write_callback(writer))
            seconds = round(time.perf_counter() - started, 6)
            metric = ExportWriteMetric(
                metric_version=EXPORT_WRITE_METRIC_VERSION,
                path=str(path),
                label=str(label or path.name),
                write_mode=str(mode),
                count=int(count),
                bytes_written=int(writer.bytes_written),
                checksum=writer.hexdigest(),
                write_seconds=seconds,
            ).to_dict()
            self.write_metrics.append(metric)
            self.contract.transition(AGENT_ARTIFACT_WRITTEN)
            self.contract.add_artifact(path, kind="json" if mode != "stream_jsonl" else "jsonl", role=label)
            self.contract.complete(last_write=metric, write_count=len(self.write_metrics))
            return ok_result(metric)
        except Exception as exc:
            message = f"package write failed for {path}: {exc}"
            blocker = self._write_failure_blocker_for_path(
                path,
                package_root=package_root,
                snapshot_id=str(snapshot_id or "unknown"),
                error_code=ERR_PACKAGE_WRITE_FAILED,
                error_message=message,
                partial_write_status={"path": str(path), "write_mode": str(mode), "label": str(label or path.name)},
            )
            self.contract.fail(ERR_PACKAGE_WRITE_FAILED, message)
            return Result(
                ERR_PACKAGE_WRITE_FAILED,
                message,
                data={
                    "agent_contract": self.contract.to_dict(),
                    "write_failure_blocker": blocker.to_dict(),
                },
            )

    def _write_binary(
        self,
        path: Path,
        *,
        mode: str,
        label: str | None,
        write_callback: Callable[[Path], int],
        package_root: str | Path | None = None,
        snapshot_id: str | None = None,
    ) -> Result[dict[str, Any]]:
        started = time.perf_counter()
        self.contract.transition(AGENT_VALIDATING_INPUT)
        try:
            if not callable(write_callback):
                raise TypeError("binary write callback is not callable")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.contract.transition(AGENT_RUNNING, write_mode=mode)
            count = int(write_callback(path))
            seconds = round(time.perf_counter() - started, 6)
            metric = ExportWriteMetric(
                metric_version=EXPORT_WRITE_METRIC_VERSION,
                path=str(path),
                label=str(label or path.name),
                write_mode=str(mode),
                count=int(count),
                bytes_written=int(path.stat().st_size),
                checksum=self._hash_file(path),
                write_seconds=seconds,
            ).to_dict()
            self.write_metrics.append(metric)
            self.contract.transition(AGENT_ARTIFACT_WRITTEN)
            self.contract.add_artifact(path, kind="binary", role=label)
            self.contract.complete(last_write=metric, write_count=len(self.write_metrics))
            return ok_result(metric)
        except Exception as exc:
            message = f"package write failed for {path}: {exc}"
            blocker = self._write_failure_blocker_for_path(
                path,
                package_root=package_root,
                snapshot_id=str(snapshot_id or "unknown"),
                error_code=ERR_PACKAGE_WRITE_FAILED,
                error_message=message,
                partial_write_status={"path": str(path), "write_mode": str(mode), "label": str(label or path.name)},
            )
            self.contract.fail(ERR_PACKAGE_WRITE_FAILED, message)
            return Result(
                ERR_PACKAGE_WRITE_FAILED,
                message,
                data={
                    "agent_contract": self.contract.to_dict(),
                    "write_failure_blocker": blocker.to_dict(),
                },
            )

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _package_result(
        self,
        package_root: Path,
        metrics: list[dict[str, Any]],
        blockers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "result_version": PACKAGE_WRITE_RESULT_VERSION,
            "package_path": str(package_root),
            "ok": not blockers,
            "write_metrics": list(metrics),
            "write_failure_blockers": list(blockers),
        }

    def _infer_package_root(self, path: Path) -> Path:
        parent = path.expanduser().resolve().parent
        if parent.name == "schema" and parent.parent.name == "reference":
            return parent.parent.parent
        if parent.name in {"event", "rebuild", "result", "context", "control", "reference"}:
            return parent.parent
        return parent

    def _write_failure_blocker_for_path(
        self,
        failed_path: str | Path,
        *,
        package_root: str | Path | None,
        snapshot_id: str,
        error_code: str,
        error_message: str,
        partial_write_status: dict[str, Any] | None = None,
    ) -> WriteFailureBlocker:
        failed = Path(failed_path).expanduser().resolve()
        root = Path(package_root).expanduser().resolve() if package_root is not None else self._infer_package_root(failed)
        blocker_path = root / "control" / "write_failure_blocker.json"
        blocker = WriteFailureBlocker(
            blocker_version=WRITE_FAILURE_BLOCKER_VERSION,
            snapshot_id=str(snapshot_id),
            package_path=str(root),
            failed_path=str(failed),
            error_code=str(error_code),
            error_message=str(error_message),
            partial_write_status=dict(partial_write_status or {}),
            emitted_at=_iso_now(),
            artifact_path=str(blocker_path),
        )
        try:
            blocker_path.parent.mkdir(parents=True, exist_ok=True)
            blocker_path.write_text(
                json.dumps(blocker.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.contract.add_artifact(blocker_path, kind="json", role="write_failure_blocker")
            return blocker
        except OSError as exc:
            return WriteFailureBlocker(
                blocker_version=blocker.blocker_version,
                snapshot_id=blocker.snapshot_id,
                package_path=blocker.package_path,
                failed_path=blocker.failed_path,
                error_code=blocker.error_code,
                error_message=blocker.error_message,
                partial_write_status=blocker.partial_write_status,
                emitted_at=blocker.emitted_at,
                artifact_path=str(blocker_path),
                artifact_write_error=str(exc),
            )


def write_evidence_package_optimized(
    package_payload: dict[str, Any],
    write_policy: dict[str, Any] | None = None,
    *,
    job_id: str | None = None,
) -> Result[dict[str, Any]]:
    return ExportWriteAgent(job_id=job_id).write_evidence_package_optimized(package_payload, write_policy)

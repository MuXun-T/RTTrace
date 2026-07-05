from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import platform
try:
    import resource
except ImportError:  # pragma: no cover - non-Unix fallback
    resource = None
from pathlib import Path
from typing import Any, Callable

from parser.agent_contract import (
    AGENT_ARTIFACT_WRITTEN,
    AGENT_RUNNING,
    AGENT_VALIDATING_INPUT,
    AgentJobContract,
)
from parser.result import Result, ok_result


StageObserver = Callable[[str, dict[str, object]], None]


TELEMETRY_RECORD_VERSION = "runtime-telemetry-v1"
TELEMETRY_RECORD_UPDATE_VERSION = "telemetry-record-update-v1"
TELEMETRY_REPORT_AGENT_NAME = "TelemetryReportAgent"
ERR_TELEMETRY_REPORT_FAILED = "ERR-TELEMETRY_REPORT_FAILED"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _current_rss_mb() -> float | None:
    if resource is None:
        return None
    rss_kb = getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0)
    if rss_kb <= 0:
        return None
    return round(float(rss_kb) / 1024.0, 3)


def memory_snapshot(stage: str) -> dict[str, object]:
    return {
        "stage": str(stage),
        "rss_mb": _current_rss_mb(),
    }


def add_stage_timing(stage_timings: dict[str, float] | None, key: str, seconds: float) -> None:
    if stage_timings is None:
        return
    stage_timings[str(key)] = round(float(stage_timings.get(str(key), 0.0)) + float(seconds), 6)


def emit_stage_update(
    observer: StageObserver | None,
    stage: str,
    *,
    status: str,
    seconds: float | None = None,
    memory_snapshot_payload: dict[str, object] | None = None,
    stage_timings: dict[str, float] | None = None,
    chunk_progress: dict[str, object] | None = None,
    hotspot_summary: dict[str, object] | None = None,
) -> None:
    if observer is None:
        return
    payload: dict[str, object] = {"status": str(status)}
    if seconds is not None:
        payload["seconds"] = round(float(seconds), 6)
    if memory_snapshot_payload is not None:
        payload["memory_snapshot"] = dict(memory_snapshot_payload)
    if stage_timings is not None:
        payload["stage_timings"] = dict(stage_timings)
    if chunk_progress is not None:
        payload["chunk_progress"] = dict(chunk_progress)
    if hotspot_summary is not None:
        payload["hotspot_summary"] = dict(hotspot_summary)
    observer(str(stage), payload)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_timestamp(value: Any) -> str:
    if value is None:
        return _iso_now()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc).replace(microsecond=0).isoformat()
    return str(value)


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _elapsed_seconds(started_at: str, finished_at: str) -> float | None:
    started = _parse_timestamp(started_at)
    finished = _parse_timestamp(finished_at)
    if started is None or finished is None:
        return None
    return round(max(0.0, (finished - started).total_seconds()), 6)


@dataclass(frozen=True)
class TelemetryRecord:
    record_version: str
    run_id: str | None
    dataset_id: str | None
    platform: str
    export_family: str | None
    embodiment_mode: str | None
    input_bytes: int | None
    sidecar_bytes: int | None
    sidecar_row_count: int | None
    sidecar_bytes_scanned: int | None
    sidecar_validate_seconds: float | None
    index_build_open_seconds: float | None
    index_reused: bool | None
    index_rebuilt: bool | None
    closure_seconds: float | None
    window_read_seconds: float | None
    package_write_seconds: float | None
    runtime_seconds: float | None
    peak_rss_mb: float | None
    proof_hash: str | None
    closure_mode: str | None
    advisor_overhead_seconds: float | None
    sidecar_lookup_count: int | None = None
    write_mode: str | None = None
    stage_timings: dict[str, float] = field(default_factory=dict)
    rss_snapshots: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_version": self.record_version,
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "platform": self.platform,
            "export_family": self.export_family,
            "embodiment_mode": self.embodiment_mode,
            "input_bytes": self.input_bytes,
            "sidecar_bytes": self.sidecar_bytes,
            "sidecar_row_count": self.sidecar_row_count,
            "sidecar_bytes_scanned": self.sidecar_bytes_scanned,
            "sidecar_validate_seconds": self.sidecar_validate_seconds,
            "index_build_open_seconds": self.index_build_open_seconds,
            "index_reused": self.index_reused,
            "index_rebuilt": self.index_rebuilt,
            "closure_seconds": self.closure_seconds,
            "window_read_seconds": self.window_read_seconds,
            "package_write_seconds": self.package_write_seconds,
            "runtime_seconds": self.runtime_seconds,
            "peak_rss_mb": self.peak_rss_mb,
            "proof_hash": self.proof_hash,
            "closure_mode": self.closure_mode,
            "advisor_overhead_seconds": self.advisor_overhead_seconds,
            "sidecar_lookup_count": self.sidecar_lookup_count,
            "write_mode": self.write_mode,
            "stage_timings": dict(self.stage_timings),
            "rss_snapshots": list(self.rss_snapshots),
        }


@dataclass(frozen=True)
class TelemetryRecordUpdate:
    update_version: str
    job_id: str
    phase_name: str
    started_at: str
    finished_at: str
    recorded_at: str
    metrics: dict[str, Any]
    phase_seconds: float | None
    history_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_version": self.update_version,
            "job_id": self.job_id,
            "phase_name": self.phase_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "recorded_at": self.recorded_at,
            "metrics": dict(self.metrics),
            "phase_seconds": self.phase_seconds,
            "history_path": self.history_path,
        }


_TELEMETRY_RECORD_FIELD_NAMES = (
    "record_version",
    "run_id",
    "dataset_id",
    "platform",
    "export_family",
    "embodiment_mode",
    "input_bytes",
    "sidecar_bytes",
    "sidecar_row_count",
    "sidecar_bytes_scanned",
    "sidecar_validate_seconds",
    "index_build_open_seconds",
    "index_reused",
    "index_rebuilt",
    "closure_seconds",
    "window_read_seconds",
    "package_write_seconds",
    "runtime_seconds",
    "peak_rss_mb",
    "proof_hash",
    "closure_mode",
    "advisor_overhead_seconds",
    "sidecar_lookup_count",
    "write_mode",
    "stage_timings",
    "rss_snapshots",
)
_ADVISOR_PREDICTION_SIGNAL_KEYS = {
    "runtime_seconds",
    "input_bytes",
    "sidecar_bytes",
    "sidecar_row_count",
    "peak_rss_mb",
    "package_write_seconds",
    "sidecar_validate_seconds",
    "index_build_open_seconds",
    "sidecar_index_build_seconds",
}


def _has_advisor_prediction_signal(row: dict[str, Any]) -> bool:
    return any(key in row and row.get(key) is not None for key in _ADVISOR_PREDICTION_SIGNAL_KEYS)


def _normalize_record_mapping(
    row: dict[str, Any],
    *,
    phase_seconds: Any = None,
    job_id: Any = None,
) -> dict[str, Any] | None:
    source = dict(row or {})
    if "index_build_open_seconds" not in source and "sidecar_index_build_seconds" in source:
        source["index_build_open_seconds"] = source.get("sidecar_index_build_seconds")
    if not _has_advisor_prediction_signal(source):
        return None
    normalized = {
        key: source.get(key)
        for key in _TELEMETRY_RECORD_FIELD_NAMES
        if key in source
    }
    if "runtime_seconds" not in normalized or normalized.get("runtime_seconds") is None:
        fallback_runtime = _optional_float(phase_seconds)
        if fallback_runtime is not None:
            normalized["runtime_seconds"] = fallback_runtime
    if "run_id" not in normalized and job_id is not None:
        normalized["run_id"] = str(job_id)
    return normalized


def normalize_telemetry_history_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, TelemetryRecord):
        return item.to_dict()
    if isinstance(item, TelemetryRecordUpdate):
        return _normalize_record_mapping(
            dict(item.metrics),
            phase_seconds=item.phase_seconds,
            job_id=item.job_id,
        )
    if not isinstance(item, dict):
        return None
    row = dict(item)
    if isinstance(row.get("telemetry_record"), dict):
        return normalize_telemetry_history_item(dict(row["telemetry_record"]))
    if isinstance(row.get("metrics"), dict):
        return _normalize_record_mapping(
            dict(row.get("metrics") or {}),
            phase_seconds=row.get("phase_seconds"),
            job_id=row.get("job_id"),
        )
    return _normalize_record_mapping(row)


class TelemetryHistoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def append(self, update: TelemetryRecordUpdate) -> TelemetryRecordUpdate:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**update.to_dict(), "history_path": str(self.path)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return TelemetryRecordUpdate(
            update_version=update.update_version,
            job_id=update.job_id,
            phase_name=update.phase_name,
            started_at=update.started_at,
            finished_at=update.finished_at,
            recorded_at=update.recorded_at,
            metrics=dict(update.metrics),
            phase_seconds=update.phase_seconds,
            history_path=str(self.path),
        )

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                rows.append(dict(json.loads(text)))
        return rows

    def read_advisor_history(
        self,
        limit: int | None = None,
        *,
        dataset_id: str | None = None,
        embodiment_mode: str | None = None,
        export_family: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.read_all():
            normalized = normalize_telemetry_history_item(item)
            if normalized is None:
                continue
            if dataset_id is not None and normalized.get("dataset_id") != dataset_id:
                continue
            if embodiment_mode is not None and normalized.get("embodiment_mode") != embodiment_mode:
                continue
            if export_family is not None and normalized.get("export_family") != export_family:
                continue
            rows.append(normalized)
        if limit is None:
            return rows
        effective_limit = int(limit)
        if effective_limit <= 0:
            return []
        return rows[-effective_limit:]

    def append_record(
        self,
        record: TelemetryRecord | dict[str, Any],
        job_id: str,
        phase_name: str = "evidence_export",
    ) -> TelemetryRecordUpdate:
        metrics = record.to_dict() if isinstance(record, TelemetryRecord) else dict(record)
        recorded_at = _iso_now()
        update = TelemetryRecordUpdate(
            update_version=TELEMETRY_RECORD_UPDATE_VERSION,
            job_id=str(job_id),
            phase_name=str(phase_name),
            started_at=recorded_at,
            finished_at=recorded_at,
            recorded_at=recorded_at,
            metrics=metrics,
            phase_seconds=_optional_float(metrics.get("runtime_seconds")),
            history_path=None,
        )
        return self.append(update)


class TelemetryReportAgent:
    def __init__(self, *, job_id: str | None = None) -> None:
        self.contract = AgentJobContract(agent_name=TELEMETRY_REPORT_AGENT_NAME, job_id=job_id)

    def _register_contract_path(self, contract_path: str | Path | None) -> Path | None:
        if contract_path is None:
            return None
        target = Path(contract_path).expanduser().resolve()
        self.contract.add_artifact(target, kind="json", role="agent_contract")
        return target

    def _write_contract_json(self, contract_path: str | Path) -> None:
        target = Path(contract_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.contract.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_failed_contract_json(self, contract_path: Path | None) -> None:
        if contract_path is None:
            return
        try:
            self._write_contract_json(contract_path)
        except OSError:
            return

    def _mark_artifact_written(self, artifact_written: bool) -> bool:
        if not artifact_written:
            self.contract.transition(AGENT_ARTIFACT_WRITTEN)
        return True

    def record_phase_result(
        self,
        *,
        job_id: str,
        phase_name: str,
        started_at: Any,
        finished_at: Any,
        metrics: dict[str, Any] | None = None,
        history_path: str | Path | None = None,
        contract_path: str | Path | None = None,
    ) -> Result[dict[str, Any]]:
        effective_job_id = str(job_id)
        self.contract = AgentJobContract(agent_name=TELEMETRY_REPORT_AGENT_NAME, job_id=effective_job_id)
        update: TelemetryRecordUpdate | None = None
        resolved_contract_path: Path | None = None
        artifact_written = False
        try:
            self.contract.transition(AGENT_VALIDATING_INPUT, phase_name=str(phase_name))
            resolved_contract_path = self._register_contract_path(contract_path)
            started_text = _coerce_timestamp(started_at)
            finished_text = _coerce_timestamp(finished_at)
            update = TelemetryRecordUpdate(
                update_version=TELEMETRY_RECORD_UPDATE_VERSION,
                job_id=effective_job_id,
                phase_name=str(phase_name),
                started_at=started_text,
                finished_at=finished_text,
                recorded_at=_iso_now(),
                metrics=dict(metrics or {}),
                phase_seconds=_elapsed_seconds(started_text, finished_text),
                history_path=None,
            )

            self.contract.transition(
                AGENT_RUNNING,
                phase_name=update.phase_name,
                phase_seconds=update.phase_seconds,
            )
            if history_path is not None:
                update = TelemetryHistoryStore(history_path).append(update)
                self.contract.add_artifact(update.history_path or history_path, kind="jsonl", role="telemetry_history")
                artifact_written = self._mark_artifact_written(artifact_written)
            if resolved_contract_path is not None:
                self._write_contract_json(resolved_contract_path)
                artifact_written = self._mark_artifact_written(artifact_written)

            self.contract.complete(phase_name=update.phase_name, phase_seconds=update.phase_seconds)
            if resolved_contract_path is not None:
                self._write_contract_json(resolved_contract_path)
            return ok_result({"telemetry_record_update": update, "agent_contract": self.contract.to_dict()})
        except Exception as exc:
            message = f"telemetry report failed: {exc}"
            self.contract.fail(ERR_TELEMETRY_REPORT_FAILED, message)
            self._write_failed_contract_json(resolved_contract_path)
            return Result(
                ERR_TELEMETRY_REPORT_FAILED,
                message,
                data={
                    "telemetry_record_update": update,
                    "agent_contract": self.contract.to_dict(),
                },
            )

    def record_phase(
        self,
        *,
        job_id: str,
        phase_name: str,
        started_at: Any,
        finished_at: Any,
        metrics: dict[str, Any] | None = None,
        history_path: str | Path | None = None,
    ) -> Result[TelemetryRecordUpdate]:
        result = self.record_phase_result(
            job_id=str(job_id),
            phase_name=phase_name,
            started_at=started_at,
            finished_at=finished_at,
            metrics=metrics,
            history_path=history_path,
        )
        payload = dict(result.data or {})
        update = payload.get("telemetry_record_update")
        return Result(
            result.code,
            result.message,
            data=update if isinstance(update, TelemetryRecordUpdate) else None,
            warnings=result.warnings,
            untrusted_windows=result.untrusted_windows,
        )

    def _build_record(
        self,
        *,
        run_id: str | None = None,
        dataset_id: str | None = None,
        export_family: str | None = "evidence",
        embodiment_mode: str | None = None,
        input_bytes: Any = None,
        sidecar_bytes: Any = None,
        sidecar_row_count: Any = None,
        sidecar_bytes_scanned: Any = None,
        sidecar_validate_seconds: Any = None,
        index_build_open_seconds: Any = None,
        index_reused: bool | None = None,
        index_rebuilt: bool | None = None,
        closure_seconds: Any = None,
        window_read_seconds: Any = None,
        package_write_seconds: Any = None,
        runtime_seconds: Any = None,
        peak_rss_mb: Any = None,
        proof_hash: str | None = None,
        closure_mode: str | None = None,
        advisor_overhead_seconds: Any = None,
        sidecar_lookup_count: Any = None,
        write_mode: str | None = None,
        stage_timings: dict[str, Any] | None = None,
        rss_snapshots: list[dict[str, Any]] | None = None,
    ) -> TelemetryRecord:
        return TelemetryRecord(
            record_version=TELEMETRY_RECORD_VERSION,
            run_id=run_id,
            dataset_id=dataset_id,
            platform=platform.platform(),
            export_family=export_family,
            embodiment_mode=embodiment_mode,
            input_bytes=_optional_int(input_bytes),
            sidecar_bytes=_optional_int(sidecar_bytes),
            sidecar_row_count=_optional_int(sidecar_row_count),
            sidecar_bytes_scanned=_optional_int(sidecar_bytes_scanned),
            sidecar_validate_seconds=_optional_float(sidecar_validate_seconds),
            index_build_open_seconds=_optional_float(index_build_open_seconds),
            index_reused=index_reused,
            index_rebuilt=index_rebuilt,
            closure_seconds=_optional_float(closure_seconds),
            window_read_seconds=_optional_float(window_read_seconds),
            package_write_seconds=_optional_float(package_write_seconds),
            runtime_seconds=_optional_float(runtime_seconds),
            peak_rss_mb=_optional_float(peak_rss_mb),
            proof_hash=proof_hash,
            closure_mode=closure_mode,
            advisor_overhead_seconds=_optional_float(advisor_overhead_seconds),
            sidecar_lookup_count=_optional_int(sidecar_lookup_count),
            write_mode=write_mode,
            stage_timings={
                str(key): float(value)
                for key, value in dict(stage_timings or {}).items()
                if _optional_float(value) is not None
            },
            rss_snapshots=[dict(item) for item in list(rss_snapshots or [])],
        )

    def build_record_result(
        self,
        *,
        job_id: str | None = None,
        contract_path: str | Path | None = None,
        run_id: str | None = None,
        dataset_id: str | None = None,
        export_family: str | None = "evidence",
        embodiment_mode: str | None = None,
        input_bytes: Any = None,
        sidecar_bytes: Any = None,
        sidecar_row_count: Any = None,
        sidecar_bytes_scanned: Any = None,
        sidecar_validate_seconds: Any = None,
        index_build_open_seconds: Any = None,
        index_reused: bool | None = None,
        index_rebuilt: bool | None = None,
        closure_seconds: Any = None,
        window_read_seconds: Any = None,
        package_write_seconds: Any = None,
        runtime_seconds: Any = None,
        peak_rss_mb: Any = None,
        proof_hash: str | None = None,
        closure_mode: str | None = None,
        advisor_overhead_seconds: Any = None,
        sidecar_lookup_count: Any = None,
        write_mode: str | None = None,
        stage_timings: dict[str, Any] | None = None,
        rss_snapshots: list[dict[str, Any]] | None = None,
    ) -> Result[dict[str, Any]]:
        effective_job_id = str(job_id) if job_id is not None else (str(run_id) if run_id is not None else None)
        self.contract = AgentJobContract(agent_name=TELEMETRY_REPORT_AGENT_NAME, job_id=effective_job_id)
        record: TelemetryRecord | None = None
        resolved_contract_path: Path | None = None
        artifact_written = False
        try:
            self.contract.transition(AGENT_VALIDATING_INPUT, run_id=run_id, dataset_id=dataset_id)
            resolved_contract_path = self._register_contract_path(contract_path)
            record = self._build_record(
                run_id=run_id,
                dataset_id=dataset_id,
                export_family=export_family,
                embodiment_mode=embodiment_mode,
                input_bytes=input_bytes,
                sidecar_bytes=sidecar_bytes,
                sidecar_row_count=sidecar_row_count,
                sidecar_bytes_scanned=sidecar_bytes_scanned,
                sidecar_validate_seconds=sidecar_validate_seconds,
                index_build_open_seconds=index_build_open_seconds,
                index_reused=index_reused,
                index_rebuilt=index_rebuilt,
                closure_seconds=closure_seconds,
                window_read_seconds=window_read_seconds,
                package_write_seconds=package_write_seconds,
                runtime_seconds=runtime_seconds,
                peak_rss_mb=peak_rss_mb,
                proof_hash=proof_hash,
                closure_mode=closure_mode,
                advisor_overhead_seconds=advisor_overhead_seconds,
                sidecar_lookup_count=sidecar_lookup_count,
                write_mode=write_mode,
                stage_timings=stage_timings,
                rss_snapshots=rss_snapshots,
            )
            self.contract.transition(
                AGENT_RUNNING,
                run_id=record.run_id,
                dataset_id=record.dataset_id,
                runtime_seconds=record.runtime_seconds,
            )
            if resolved_contract_path is not None:
                self._write_contract_json(resolved_contract_path)
                artifact_written = self._mark_artifact_written(artifact_written)

            self.contract.complete(run_id=record.run_id, dataset_id=record.dataset_id)
            if resolved_contract_path is not None:
                self._write_contract_json(resolved_contract_path)
            return ok_result({"telemetry_record": record, "agent_contract": self.contract.to_dict()})
        except Exception as exc:
            message = f"telemetry report failed: {exc}"
            self.contract.fail(ERR_TELEMETRY_REPORT_FAILED, message)
            self._write_failed_contract_json(resolved_contract_path)
            return Result(
                ERR_TELEMETRY_REPORT_FAILED,
                message,
                data={
                    "telemetry_record": record,
                    "agent_contract": self.contract.to_dict(),
                },
            )

    def build_record(
        self,
        *,
        run_id: str | None = None,
        dataset_id: str | None = None,
        export_family: str | None = "evidence",
        embodiment_mode: str | None = None,
        input_bytes: Any = None,
        sidecar_bytes: Any = None,
        sidecar_row_count: Any = None,
        sidecar_bytes_scanned: Any = None,
        sidecar_validate_seconds: Any = None,
        index_build_open_seconds: Any = None,
        index_reused: bool | None = None,
        index_rebuilt: bool | None = None,
        closure_seconds: Any = None,
        window_read_seconds: Any = None,
        package_write_seconds: Any = None,
        runtime_seconds: Any = None,
        peak_rss_mb: Any = None,
        proof_hash: str | None = None,
        closure_mode: str | None = None,
        advisor_overhead_seconds: Any = None,
        sidecar_lookup_count: Any = None,
        write_mode: str | None = None,
        stage_timings: dict[str, Any] | None = None,
        rss_snapshots: list[dict[str, Any]] | None = None,
    ) -> TelemetryRecord:
        return self._build_record(
            run_id=run_id,
            dataset_id=dataset_id,
            export_family=export_family,
            embodiment_mode=embodiment_mode,
            input_bytes=input_bytes,
            sidecar_bytes=sidecar_bytes,
            sidecar_row_count=sidecar_row_count,
            sidecar_bytes_scanned=sidecar_bytes_scanned,
            sidecar_validate_seconds=sidecar_validate_seconds,
            index_build_open_seconds=index_build_open_seconds,
            index_reused=index_reused,
            index_rebuilt=index_rebuilt,
            closure_seconds=closure_seconds,
            window_read_seconds=window_read_seconds,
            package_write_seconds=package_write_seconds,
            runtime_seconds=runtime_seconds,
            peak_rss_mb=peak_rss_mb,
            proof_hash=proof_hash,
            closure_mode=closure_mode,
            advisor_overhead_seconds=advisor_overhead_seconds,
            sidecar_lookup_count=sidecar_lookup_count,
            write_mode=write_mode,
            stage_timings=stage_timings,
            rss_snapshots=rss_snapshots,
        )


def record_telemetry_phase(
    *,
    job_id: str,
    phase_name: str,
    started_at: Any,
    finished_at: Any,
    metrics: dict[str, Any] | None = None,
    history_path: str | Path | None = None,
) -> Result[TelemetryRecordUpdate]:
    return TelemetryReportAgent().record_phase(
        job_id=job_id,
        phase_name=phase_name,
        started_at=started_at,
        finished_at=finished_at,
        metrics=metrics,
        history_path=history_path,
    )

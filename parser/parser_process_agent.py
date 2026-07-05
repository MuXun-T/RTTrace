from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
try:
    import resource
except ImportError:  # pragma: no cover - non-Unix fallback
    resource = None
import subprocess
import sys
import tempfile
import time
from typing import Any

from parser.agent_contract import (
    AGENT_ARTIFACT_WRITTEN,
    AGENT_RUNNING,
    AGENT_VALIDATING_INPUT,
    ERR_AGENT_CANCELLED,
    ERR_AGENT_TIMEOUT,
    ERR_PARSER_PROCESS_FAILED,
    AgentJobContract,
)
from parser.pipeline import load_dataset_with_timings
from parser.result import Result, ok_result
from spec.io import serialize
from spec.io import checksum_file
from spec.schema_loader import DICTIONARY_PATH


PARSER_PROCESS_AGENT_NAME = "ParserProcessAgent"
PARSER_PROCESS_ARTIFACT_VERSION = "parser-process-artifact-v1"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rss_mb() -> float | None:
    if resource is None:
        return None
    rss_kb = getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0)
    if rss_kb <= 0:
        return None
    return round(float(rss_kb) / 1024.0, 3)


def _cleanup_pickle_artifact(path: Path) -> str | None:
    try:
        path.unlink(missing_ok=True)
        return None
    except OSError as exc:
        return str(exc)


def _append_progress_event(progress_path: Path | None, stage: str, payload: dict[str, object]) -> None:
    if progress_path is None:
        return
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "emitted_at": _iso_now(),
        "stage": str(stage),
        "status": str(payload.get("status") or ""),
        "payload": serialize(payload),
    }
    with progress_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _stable_result_timing_fields(
    data: dict[str, Any] | None,
    *,
    index_build_mode: str | None = None,
    materialize_event_stream: bool | None = None,
    peak_rss_mb: float | None = None,
) -> dict[str, Any]:
    payload = dict(data or {})
    return {
        "parse_seconds": payload.get("parse_seconds"),
        "align_events_seconds": payload.get("align_events_seconds"),
        "rebuild_seconds": payload.get("rebuild_seconds"),
        "idx_build_seconds": payload.get("idx_build_seconds"),
        "load_seconds": payload.get("load_seconds"),
        "index_build_mode": payload.get("index_build_mode", index_build_mode),
        "materialize_event_stream": payload.get("materialize_event_stream", materialize_event_stream),
        "peak_rss_mb": payload.get("peak_rss_mb", peak_rss_mb),
    }


@dataclass(frozen=True)
class ParserProcessArtifact:
    artifact_version: str
    source: str
    artifact_policy: dict[str, Any]
    artifact_handle: dict[str, Any]
    result_path: str
    materialize_event_stream: bool
    load_artifact: bool
    parse_seconds: float | None
    align_events_seconds: float | None
    rebuild_seconds: float | None
    idx_build_seconds: float | None
    load_seconds: float | None
    index_build_mode: str | None
    peak_rss_mb: float | None
    trace_checksum: str
    dictionary_checksum: str
    parser_version: str
    schema_version: str
    cache_key: str
    cache_hit: bool
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_version": self.artifact_version,
            "source": self.source,
            "artifact_policy": dict(self.artifact_policy),
            "artifact_handle": dict(self.artifact_handle),
            "result_path": self.result_path,
            "materialize_event_stream": bool(self.materialize_event_stream),
            "load_artifact": bool(self.load_artifact),
            "parse_seconds": self.parse_seconds,
            "align_events_seconds": self.align_events_seconds,
            "rebuild_seconds": self.rebuild_seconds,
            "idx_build_seconds": self.idx_build_seconds,
            "load_seconds": self.load_seconds,
            "index_build_mode": self.index_build_mode,
            "peak_rss_mb": self.peak_rss_mb,
            "trace_checksum": self.trace_checksum,
            "dictionary_checksum": self.dictionary_checksum,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "cache_key": self.cache_key,
            "cache_hit": bool(self.cache_hit),
            "created_at": self.created_at,
        }


def _source_trace_paths(source: str | Path) -> list[Path]:
    source_path = Path(source).expanduser().resolve()
    if source_path.is_dir():
        segments = sorted(item for item in source_path.iterdir() if item.is_file() and item.suffix == ".trace")
        return segments
    return [source_path]


def _trace_checksum(source: str | Path) -> str:
    trace_paths = _source_trace_paths(source)
    if len(trace_paths) == 1 and trace_paths[0].is_file():
        return checksum_file(trace_paths[0])
    digest = hashlib.sha256()
    for path in trace_paths:
        digest.update(str(path.name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(checksum_file(path).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _stable_cache_key(payload: dict[str, Any]) -> str:
    stable_json = json.dumps(serialize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable_json.encode("utf-8")).hexdigest()


def build_parser_cache_bindings(
    source: str | Path,
    *,
    dictionary_path: str | Path | None,
    parser_version: str | None,
    schema_version: str | None,
    index_build_mode: str,
    materialize_event_stream: bool,
) -> dict[str, str]:
    resolved_dictionary_path = (
        Path(dictionary_path).expanduser().resolve()
        if dictionary_path is not None
        else DICTIONARY_PATH.expanduser().resolve()
    )
    parser_version_text = str(parser_version or PARSER_PROCESS_ARTIFACT_VERSION)
    schema_version_text = str(schema_version or PARSER_PROCESS_ARTIFACT_VERSION)
    trace_paths = _source_trace_paths(source)
    if not trace_paths or any(not path.exists() for path in trace_paths):
        raise FileNotFoundError(str(Path(source).expanduser()))
    trace_checksum = _trace_checksum(source)
    dictionary_checksum = checksum_file(resolved_dictionary_path)
    cache_key = _stable_cache_key(
        {
            "trace_checksum": trace_checksum,
            "dictionary_checksum": dictionary_checksum,
            "parser_version": parser_version_text,
            "index_build_mode": str(index_build_mode or "full"),
            "materialize_event_stream": bool(materialize_event_stream),
            "schema_version": schema_version_text,
        }
    )
    return {
        "trace_checksum": trace_checksum,
        "dictionary_checksum": dictionary_checksum,
        "dictionary_path": str(resolved_dictionary_path),
        "parser_version": parser_version_text,
        "schema_version": schema_version_text,
        "cache_key": cache_key,
    }


class ParserProcessAgent:
    def __init__(self, *, job_id: str | None = None) -> None:
        self.contract = AgentJobContract(agent_name=PARSER_PROCESS_AGENT_NAME, job_id=job_id)

    def parse_rebuild(
        self,
        source: str | Path,
        *,
        artifact_dir: str | Path | None = None,
        timeout_s: float | None = None,
        materialize_event_stream: bool = True,
        load_artifact: bool = True,
        artifact_policy: dict[str, Any] | None = None,
        cancel_path: str | Path | None = None,
    ) -> Result[dict[str, Any]]:
        policy = dict(artifact_policy or {})
        if artifact_dir is None and policy.get("artifact_dir") is not None:
            artifact_dir = policy.get("artifact_dir")
        if timeout_s is None and policy.get("timeout_s") is not None:
            timeout_s = float(policy["timeout_s"])
        if "materialize_event_stream" in policy:
            materialize_event_stream = bool(policy["materialize_event_stream"])
        if "load_artifact" in policy:
            load_artifact = bool(policy["load_artifact"])
        artifact_format = str(policy.get("artifact_format") or "pickle")
        retain_artifact = bool(policy.get("retain_artifact", True))
        cancel_file = (
            Path(cancel_path or policy.get("cancel_path")).expanduser().resolve()
            if (cancel_path or policy.get("cancel_path"))
            else None
        )
        resolved_dictionary_path = Path(policy.get("dictionary_path") or DICTIONARY_PATH).expanduser().resolve()
        index_build_mode = str(policy.get("index_build_mode") or "full")
        parser_version = str(policy.get("parser_version") or PARSER_PROCESS_ARTIFACT_VERSION)
        schema_version = str(policy.get("schema_version") or PARSER_PROCESS_ARTIFACT_VERSION)
        cache_enabled = bool(policy.get("cache_enabled", False))
        cache_root_value = policy.get("cache_root") or policy.get("artifact_cache_root")
        cache_root_path = (
            Path(cache_root_value).expanduser().resolve()
            if cache_root_value is not None
            else ((Path(tempfile.gettempdir()) / "rttrace-parser-agent").resolve() if cache_enabled else None)
        )
        progress_path = (
            Path(policy.get("progress_path")).expanduser().resolve()
            if policy.get("progress_path") is not None
            else None
        )
        root = (
            Path(artifact_dir).expanduser().resolve()
            if artifact_dir is not None
            else Path(tempfile.mkdtemp(prefix="rttrace-parser-agent-")).resolve()
        )
        artifact_path = root / "parse_rebuild_artifact.pickle"
        result_path = root / "parse_rebuild_result.json"
        artifact_handle = {"path": str(artifact_path), "kind": artifact_format, "role": "parse_rebuild_artifact"}
        effective_materialize_event_stream = bool(materialize_event_stream)
        effective_index_build_mode = index_build_mode
        trace_checksum = ""
        dictionary_checksum = ""
        cache_key = ""

        artifact_policy_payload = {
            "artifact_dir": str(root),
            "artifact_format": artifact_format,
            "retain_artifact": retain_artifact,
            "load_artifact": bool(load_artifact),
            "materialize_event_stream": bool(materialize_event_stream),
            "timeout_s": timeout_s,
            "cancel_path": str(cancel_file) if cancel_file is not None else None,
            "dictionary_path": str(resolved_dictionary_path),
            "index_build_mode": index_build_mode,
            "progress_path": str(progress_path) if progress_path is not None else None,
            "parser_version": parser_version,
            "schema_version": schema_version,
            "cache_enabled": cache_enabled,
            "cache_root": str(cache_root_path) if cache_root_path is not None else None,
        }

        def _refresh_root(target_root: Path) -> None:
            nonlocal root, artifact_path, result_path, artifact_handle
            root = target_root.expanduser().resolve()
            artifact_path = root / "parse_rebuild_artifact.pickle"
            result_path = root / "parse_rebuild_result.json"
            artifact_handle = {"path": str(artifact_path), "kind": artifact_format, "role": "parse_rebuild_artifact"}
            artifact_policy_payload["artifact_dir"] = str(root)

        def _cache_fields(*, cache_hit: bool) -> dict[str, Any]:
            return {
                "trace_checksum": trace_checksum,
                "dictionary_checksum": dictionary_checksum,
                "parser_version": parser_version,
                "schema_version": schema_version,
                "cache_key": cache_key,
                "cache_hit": bool(cache_hit),
            }

        def _result_data(payload: dict[str, Any] | None, *, cache_hit: bool) -> dict[str, Any]:
            return {
                **dict(payload or {}),
                **_cache_fields(cache_hit=cache_hit),
            }

        def _write_result_manifest(result_payload: dict[str, Any], *, cache_hit: bool) -> None:
            result_payload["data"] = serialize(
                _result_data(
                    result_payload.get("data") if isinstance(result_payload.get("data"), dict) else {},
                    cache_hit=cache_hit,
                )
            )
            try:
                result_path.write_text(
                    json.dumps(result_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                self.contract.telemetry["result_manifest_write_error"] = str(exc)

        def _parser_process_artifact(
            *,
            parse_seconds: float | None = None,
            align_events_seconds: float | None = None,
            rebuild_seconds: float | None = None,
            idx_build_seconds: float | None = None,
            load_seconds: float | None = None,
            index_build_mode: str | None = None,
            materialize_event_stream: bool | None = None,
            peak_rss_mb: float | None = None,
            cache_hit: bool = False,
        ) -> dict[str, Any]:
            return ParserProcessArtifact(
                artifact_version=PARSER_PROCESS_ARTIFACT_VERSION,
                source=str(Path(source).expanduser()),
                artifact_policy=artifact_policy_payload,
                artifact_handle=artifact_handle,
                result_path=str(result_path),
                materialize_event_stream=bool(
                    effective_materialize_event_stream
                    if materialize_event_stream is None
                    else materialize_event_stream
                ),
                load_artifact=bool(load_artifact),
                parse_seconds=parse_seconds,
                align_events_seconds=align_events_seconds,
                rebuild_seconds=rebuild_seconds,
                idx_build_seconds=idx_build_seconds,
                load_seconds=load_seconds,
                index_build_mode=effective_index_build_mode if index_build_mode is None else index_build_mode,
                peak_rss_mb=peak_rss_mb,
                trace_checksum=trace_checksum,
                dictionary_checksum=dictionary_checksum,
                parser_version=parser_version,
                schema_version=schema_version,
                cache_key=cache_key,
                cache_hit=bool(cache_hit),
                created_at=_iso_now(),
            ).to_dict()

        def _cleanup_if_requested() -> None:
            if retain_artifact:
                return
            cleanup_error = _cleanup_pickle_artifact(artifact_path)
            if cleanup_error:
                self.contract.telemetry["artifact_cleanup_error"] = cleanup_error

        self.contract.transition(AGENT_VALIDATING_INPUT)
        self.contract.add_input_ref(source, kind="trace", role="parse_source")
        if artifact_format != "pickle":
            message = f"unsupported parser artifact format: {artifact_format}"
            self.contract.fail(ERR_PARSER_PROCESS_FAILED, message)
            _cleanup_if_requested()
            return Result(
                ERR_PARSER_PROCESS_FAILED,
                message,
                data={
                    **_cache_fields(cache_hit=False),
                    "artifact_handle": artifact_handle,
                    "parser_process_artifact": _parser_process_artifact(),
                    "result_path": str(result_path),
                    "agent_contract": self.contract.to_dict(),
                    "child_agent_contract": {},
                },
            )
        if cancel_file is not None and cancel_file.exists():
            message = f"parser process cancelled before start: {cancel_file}"
            self.contract.fail(ERR_AGENT_CANCELLED, message, cancelled=True)
            _cleanup_if_requested()
            return Result(
                ERR_AGENT_CANCELLED,
                message,
                data={
                    **_cache_fields(cache_hit=False),
                    "artifact_handle": artifact_handle,
                    "parser_process_artifact": _parser_process_artifact(),
                    "result_path": str(result_path),
                    "agent_contract": self.contract.to_dict(),
                    "child_agent_contract": {},
                },
            )
        bindings_available = True
        try:
            bindings = build_parser_cache_bindings(
                source,
                dictionary_path=resolved_dictionary_path,
                parser_version=parser_version,
                schema_version=schema_version,
                index_build_mode=index_build_mode,
                materialize_event_stream=materialize_event_stream,
            )
            trace_checksum = str(bindings["trace_checksum"])
            dictionary_checksum = str(bindings["dictionary_checksum"])
            parser_version = str(bindings["parser_version"])
            schema_version = str(bindings["schema_version"])
            cache_key = str(bindings["cache_key"])
            artifact_policy_payload["dictionary_path"] = str(bindings["dictionary_path"])
            if cache_enabled and cache_root_path is not None:
                _refresh_root(cache_root_path / cache_key)
        except (OSError, ValueError):
            bindings_available = False

        try:
            root.mkdir(parents=True, exist_ok=True)
            if progress_path is not None:
                progress_path.parent.mkdir(parents=True, exist_ok=True)
                progress_path.unlink(missing_ok=True)
        except OSError as exc:
            message = f"parser artifact directory unavailable: {root}: {exc}"
            self.contract.fail(ERR_PARSER_PROCESS_FAILED, message)
            return Result(
                ERR_PARSER_PROCESS_FAILED,
                message,
                data={
                    **_cache_fields(cache_hit=False),
                    "artifact_handle": artifact_handle,
                    "parser_process_artifact": _parser_process_artifact(),
                    "result_path": str(result_path),
                    "agent_contract": self.contract.to_dict(),
                    "child_agent_contract": {},
                },
            )

        if bindings_available and cache_enabled and artifact_path.exists() and result_path.exists():
            cached_payload: dict[str, Any] | None = None
            try:
                cached_raw = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(cached_raw, dict):
                    cached_payload = cached_raw
            except (OSError, json.JSONDecodeError):
                cached_payload = None
            if cached_payload is not None:
                cached_data = cached_payload.get("data") if isinstance(cached_payload.get("data"), dict) else {}
                cache_matches = (
                    cached_payload.get("code") == "OK"
                    and str(cached_data.get("trace_checksum") or "") == trace_checksum
                    and str(cached_data.get("dictionary_checksum") or "") == dictionary_checksum
                    and str(cached_data.get("parser_version") or "") == parser_version
                    and str(cached_data.get("schema_version") or "") == schema_version
                    and str(cached_data.get("cache_key") or "") == cache_key
                )
                if cache_matches:
                    cached_artifact: Any = None
                    cached_pickle_valid = False
                    if load_artifact:
                        try:
                            with artifact_path.open("rb") as handle:
                                cached_artifact = pickle.load(handle)
                            cached_pickle_valid = True
                        except (OSError, pickle.PickleError, EOFError, AttributeError, ValueError):
                            cached_pickle_valid = False
                    else:
                        cached_pickle_valid = artifact_path.is_file()
                    if cached_pickle_valid:
                        if load_artifact and dataclasses.is_dataclass(cached_artifact) and hasattr(cached_artifact, "source"):
                            refreshed_dictionary_info = dict(getattr(cached_artifact, "dictionary_info", {}) or {})
                            using_default_dictionary = resolved_dictionary_path == DICTIONARY_PATH.expanduser().resolve()
                            refreshed_dictionary_info["requested_source"] = "default" if using_default_dictionary else "external_path"
                            refreshed_dictionary_info["requested_path"] = None if using_default_dictionary else str(resolved_dictionary_path)
                            refreshed_dictionary_info["resolved_source"] = "default" if using_default_dictionary else "external"
                            refreshed_dictionary_info["reference_path"] = str(resolved_dictionary_path)
                            cached_artifact = dataclasses.replace(
                                cached_artifact,
                                source=str(Path(source).expanduser()),
                                dictionary_info=refreshed_dictionary_info,
                            )
                        stable_fields = _stable_result_timing_fields(
                            cached_data,
                            index_build_mode=index_build_mode,
                            materialize_event_stream=materialize_event_stream,
                            peak_rss_mb=cached_payload.get("peak_rss_mb"),
                        )
                        self.contract.transition(
                            AGENT_ARTIFACT_WRITTEN,
                            artifact_path=str(artifact_path),
                            result_path=str(result_path),
                            artifact_policy=artifact_policy_payload,
                            cache_hit=True,
                            cache_key=cache_key,
                        )
                        self.contract.add_artifact(artifact_path, kind="pickle", role="parse_rebuild_artifact")
                        self.contract.add_artifact(result_path, kind="json", role="parse_rebuild_result")
                        self.contract.complete(
                            child_peak_rss_mb=cached_payload.get("peak_rss_mb"),
                            artifact_path=str(artifact_path),
                            cache_hit=True,
                            cache_key=cache_key,
                            trace_checksum=trace_checksum,
                            dictionary_checksum=dictionary_checksum,
                        )
                        parser_process_artifact = _parser_process_artifact(
                            **stable_fields,
                            cache_hit=True,
                        )
                        _cleanup_if_requested()
                        return ok_result(
                            {
                                **_result_data(cached_data, cache_hit=True),
                                "artifact": cached_artifact if load_artifact else None,
                                "artifact_handle": artifact_handle,
                                "parser_process_artifact": parser_process_artifact,
                                "result_path": str(result_path),
                                "agent_contract": self.contract.to_dict(),
                                "child_agent_contract": dict(cached_payload.get("agent_contract") or {}),
                            },
                            warnings=list(cached_payload.get("warnings") or []),
                        )

        repo_root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(repo_root), env.get("PYTHONPATH", "")]).strip(os.pathsep)
        cmd = [
            sys.executable,
            "-m",
            "parser.parser_process_agent",
            "--worker",
            "--source",
            str(Path(source).expanduser()),
            "--artifact-path",
            str(artifact_path),
            "--result-path",
            str(result_path),
            "--materialize-event-stream",
            "1" if materialize_event_stream else "0",
            "--dictionary-path",
            str(resolved_dictionary_path),
        ]
        if cancel_file is not None:
            cmd.extend(["--cancel-path", str(cancel_file)])
        cmd.extend(["--index-build-mode", index_build_mode])
        if progress_path is not None:
            cmd.extend(["--progress-path", str(progress_path)])
        started = time.perf_counter()
        self.contract.transition(
            AGENT_RUNNING,
            artifact_path=str(artifact_path),
            result_path=str(result_path),
            artifact_policy=artifact_policy_payload,
            cache_hit=False,
            cache_key=cache_key,
        )
        process = subprocess.Popen(
            cmd,
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = None if timeout_s is None else time.monotonic() + float(timeout_s)
        while process.poll() is None:
            if cancel_file is not None and cancel_file.exists():
                process.kill()
                stdout, stderr = process.communicate()
                message = f"parser process cancelled by cancel_path={cancel_file}"
                self.contract.fail(ERR_AGENT_CANCELLED, message, cancelled=True)
                _cleanup_if_requested()
                return Result(
                    ERR_AGENT_CANCELLED,
                    message,
                    data={
                        **_cache_fields(cache_hit=False),
                        "stdout": stdout,
                        "stderr": stderr,
                        "artifact_handle": artifact_handle,
                        "parser_process_artifact": _parser_process_artifact(),
                        "result_path": str(result_path),
                        "agent_contract": self.contract.to_dict(),
                        "child_agent_contract": {},
                    },
                )
            if deadline is not None and time.monotonic() > deadline:
                break
            time.sleep(0.05)
        if process.poll() is None:
            process.kill()
            stdout, stderr = process.communicate()
            message = f"parser process exceeded timeout_s={timeout_s}"
            self.contract.fail(ERR_AGENT_TIMEOUT, message, timeout=True)
            _cleanup_if_requested()
            return Result(
                ERR_AGENT_TIMEOUT,
                message,
                data={
                    **_cache_fields(cache_hit=False),
                    "stdout": stdout,
                    "stderr": stderr,
                    "artifact_handle": artifact_handle,
                    "parser_process_artifact": _parser_process_artifact(),
                    "result_path": str(result_path),
                    "agent_contract": self.contract.to_dict(),
                    "child_agent_contract": {},
                },
            )
        stdout, stderr = process.communicate()

        elapsed = round(time.perf_counter() - started, 6)
        if not result_path.exists():
            message = stderr.strip() or stdout.strip() or f"parser process failed with exit code {process.returncode}"
            self.contract.fail(ERR_PARSER_PROCESS_FAILED, message)
            _cleanup_if_requested()
            return Result(
                ERR_PARSER_PROCESS_FAILED,
                message,
                data={
                    **_cache_fields(cache_hit=False),
                    "stdout": stdout,
                    "stderr": stderr,
                    "artifact_handle": artifact_handle,
                    "parser_process_artifact": _parser_process_artifact(),
                    "result_path": str(result_path),
                    "agent_contract": self.contract.to_dict(),
                    "child_agent_contract": {},
                },
            )
        try:
            result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            message = f"parser process result invalid: {exc}"
            self.contract.fail(ERR_PARSER_PROCESS_FAILED, message)
            _cleanup_if_requested()
            return Result(
                ERR_PARSER_PROCESS_FAILED,
                message,
                data={
                    **_cache_fields(cache_hit=False),
                    "artifact_handle": artifact_handle,
                    "parser_process_artifact": _parser_process_artifact(),
                    "result_path": str(result_path),
                    "agent_contract": self.contract.to_dict(),
                    "child_agent_contract": {},
                },
            )

        _write_result_manifest(result_payload, cache_hit=False)
        child_contract = dict(result_payload.get("agent_contract") or {})
        result_data = result_payload.get("data") if isinstance(result_payload.get("data"), dict) else {}
        if result_payload.get("code") != "OK":
            code = str(result_payload.get("code") or ERR_PARSER_PROCESS_FAILED)
            message = str(result_payload.get("message") or "parser process failed")
            self.contract.fail(
                code,
                message,
                cancelled=code == ERR_AGENT_CANCELLED,
                timeout=code == ERR_AGENT_TIMEOUT,
            )
            stable_fields = _stable_result_timing_fields(
                result_data,
                index_build_mode=index_build_mode,
                materialize_event_stream=materialize_event_stream,
                peak_rss_mb=result_payload.get("peak_rss_mb"),
            )
            _cleanup_if_requested()
            return Result(
                code,
                message,
                data={
                    **result_payload,
                    **_cache_fields(cache_hit=False),
                    "artifact_handle": artifact_handle,
                    "parser_process_artifact": _parser_process_artifact(**stable_fields),
                    "result_path": str(result_path),
                    "agent_contract": self.contract.to_dict(),
                    "child_agent_contract": child_contract,
                },
            )

        artifact = None
        if load_artifact:
            try:
                with artifact_path.open("rb") as handle:
                    artifact = pickle.load(handle)
            except (OSError, pickle.PickleError, EOFError, AttributeError, ValueError) as exc:
                message = f"parser process artifact load failed: {exc}"
                self.contract.fail(ERR_PARSER_PROCESS_FAILED, message)
                _cleanup_if_requested()
                return Result(
                    ERR_PARSER_PROCESS_FAILED,
                    message,
                    data={
                        **_cache_fields(cache_hit=False),
                        "artifact_handle": artifact_handle,
                        "parser_process_artifact": _parser_process_artifact(
                            **_stable_result_timing_fields(
                                result_data,
                                index_build_mode=index_build_mode,
                                materialize_event_stream=materialize_event_stream,
                                peak_rss_mb=result_payload.get("peak_rss_mb"),
                            )
                        ),
                        "result_path": str(result_path),
                        "agent_contract": self.contract.to_dict(),
                        "child_agent_contract": child_contract,
                    },
                )
        self.contract.transition(AGENT_ARTIFACT_WRITTEN)
        self.contract.add_artifact(artifact_path, kind="pickle", role="parse_rebuild_artifact")
        self.contract.add_artifact(result_path, kind="json", role="parse_rebuild_result")
        self.contract.complete(
            elapsed_seconds=elapsed,
            child_peak_rss_mb=result_payload.get("peak_rss_mb"),
            artifact_path=str(artifact_path),
            cache_hit=False,
            cache_key=cache_key,
            trace_checksum=trace_checksum,
            dictionary_checksum=dictionary_checksum,
        )
        parser_process_artifact = _parser_process_artifact(
            **_stable_result_timing_fields(
                result_data,
                index_build_mode=index_build_mode,
                materialize_event_stream=materialize_event_stream,
                peak_rss_mb=result_payload.get("peak_rss_mb"),
            )
        )
        _cleanup_if_requested()
        return ok_result(
            {
                **_result_data(result_data, cache_hit=False),
                "artifact": artifact,
                "artifact_handle": artifact_handle,
                "parser_process_artifact": parser_process_artifact,
                "result_path": str(result_path),
                "agent_contract": self.contract.to_dict(),
                "child_agent_contract": child_contract,
            },
            warnings=list(result_payload.get("warnings") or []),
        )


def _worker_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="parser_process_agent_worker")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source", required=True)
    parser.add_argument("--artifact-path", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--materialize-event-stream", choices=["0", "1"], default="1")
    parser.add_argument("--cancel-path")
    parser.add_argument("--dictionary-path")
    parser.add_argument("--index-build-mode", default="full")
    parser.add_argument("--progress-path")
    args = parser.parse_args(argv)
    contract = AgentJobContract(agent_name=PARSER_PROCESS_AGENT_NAME)
    artifact_path = Path(args.artifact_path).expanduser().resolve()
    result_path = Path(args.result_path).expanduser().resolve()
    cancel_path = Path(args.cancel_path).expanduser().resolve() if args.cancel_path else None
    progress_path = Path(args.progress_path).expanduser().resolve() if args.progress_path else None

    def _stage_observer(stage: str, payload: dict[str, object]) -> None:
        _append_progress_event(progress_path, stage, payload)

    contract.transition(AGENT_VALIDATING_INPUT, rss_mb=_rss_mb())
    if cancel_path is not None and cancel_path.exists():
        contract.fail(ERR_AGENT_CANCELLED, f"parser process cancelled before parse: {cancel_path}", cancelled=True)
        payload = {
            "code": ERR_AGENT_CANCELLED,
            "message": contract.error_message,
            "data": _stable_result_timing_fields(
                None,
                index_build_mode=str(args.index_build_mode or "full"),
                materialize_event_stream=args.materialize_event_stream == "1",
            ),
            "warnings": [],
            "untrusted_windows": [],
            "peak_rss_mb": _rss_mb(),
            "agent_contract": contract.to_dict(),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 3
    contract.transition(AGENT_RUNNING)
    started = time.perf_counter()
    result = load_dataset_with_timings(
        args.source,
        dictionary=args.dictionary_path,
        stage_observer=_stage_observer if progress_path is not None else None,
        materialize_event_stream=args.materialize_event_stream == "1",
        index_build_mode=str(args.index_build_mode or "full"),
    )
    if cancel_path is not None and cancel_path.exists():
        contract.fail(ERR_AGENT_CANCELLED, f"parser process cancelled after parse: {cancel_path}", cancelled=True)
        payload = {
            "code": ERR_AGENT_CANCELLED,
            "message": contract.error_message,
            "data": _stable_result_timing_fields(
                result.data if isinstance(result.data, dict) else None,
                index_build_mode=str(args.index_build_mode or "full"),
                materialize_event_stream=args.materialize_event_stream == "1",
            ),
            "warnings": list(result.warnings),
            "untrusted_windows": serialize(result.untrusted_windows),
            "peak_rss_mb": _rss_mb(),
            "agent_contract": contract.to_dict(),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 3
    payload: dict[str, Any]
    if result.ok:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with artifact_path.open("wb") as handle:
            pickle.dump(result.data["artifact"], handle, protocol=pickle.HIGHEST_PROTOCOL)
        contract.transition(AGENT_ARTIFACT_WRITTEN, rss_mb=_rss_mb())
        contract.add_artifact(artifact_path, kind="pickle", role="parse_rebuild_artifact")
        contract.complete(
            parse_seconds=result.data.get("parse_seconds"),
            rebuild_seconds=result.data.get("rebuild_seconds"),
            peak_rss_mb=_rss_mb(),
            elapsed_seconds=round(time.perf_counter() - started, 6),
        )
        data = dict(result.data)
        data.pop("artifact", None)
        payload = {
            "code": "OK",
            "message": "",
            "data": serialize(
                {
                    **data,
                    **_stable_result_timing_fields(
                        data,
                        index_build_mode=str(args.index_build_mode or "full"),
                        materialize_event_stream=args.materialize_event_stream == "1",
                    ),
                }
            ),
            "warnings": list(result.warnings),
            "untrusted_windows": serialize(result.untrusted_windows),
            "peak_rss_mb": _rss_mb(),
            "agent_contract": contract.to_dict(),
        }
    else:
        contract.fail(str(result.code or ERR_PARSER_PROCESS_FAILED), result.message)
        payload = {
            "code": str(result.code or ERR_PARSER_PROCESS_FAILED),
            "message": result.message,
            "data": serialize(
                _stable_result_timing_fields(
                    result.data if isinstance(result.data, dict) else None,
                    index_build_mode=str(args.index_build_mode or "full"),
                    materialize_event_stream=args.materialize_event_stream == "1",
                )
            ),
            "warnings": list(result.warnings),
            "untrusted_windows": serialize(result.untrusted_windows),
            "peak_rss_mb": _rss_mb(),
            "agent_contract": contract.to_dict(),
        }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(_worker_main(sys.argv[1:]))


def run_isolated_parse(
    source: str | Path,
    *,
    artifact_policy: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> Result[dict[str, Any]]:
    return ParserProcessAgent(job_id=job_id).parse_rebuild(source, artifact_policy=artifact_policy)

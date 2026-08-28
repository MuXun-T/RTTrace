"""Immutable, offline capture-artifact handling for P4.

This module accepts already-created files only.  It never opens a serial port,
debug probe, or logic analyser.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import json
import re
from typing import Any, Mapping

from spec.rtd_pilot_contracts import finalize_record as finalize_p2_record

from .contracts import (
    P4_SCHEMA_VERSION,
    P4ContractError,
    canonical_json,
    file_digest,
    finalize_record,
    record_digest,
    validate_session_manifest,
)


_ARTIFACT_STATES = frozenset({"available", "missing", "partial", "corrupt"})


class P4ArtifactError(P4ContractError):
    """An imported P4 artifact is unsafe, missing, changed, or duplicated."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise P4ArtifactError(message)


def _write_exclusive(path: Path, payload: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_component(value: str, label: str) -> str:
    _require(
        value and value.isascii() and ".." not in value and "/" not in value and "\\" not in value
        and all(char.isalnum() or char in "._-:" for char in value),
        f"{label} is unsafe",
    )
    return value.replace(":", "__")


HARDWARE_LAYOUT_VERSION = "rtd-p4-hardware-smoke-layout-v1"
_BOARD_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")


def sha256_file(path: str | Path) -> tuple[int, str]:
    raw = Path(path).read_bytes()
    return len(raw), file_digest(raw)


def make_raw_artifact(
    *,
    capture_id: str,
    state: str,
    size: int,
    sha256_digest: str | None,
    logical_name: str = "raw/trace.trace",
    kind: str = "raw_trace",
) -> dict[str, Any]:
    _require(state in _ARTIFACT_STATES, "unsupported raw artifact state")
    _require(isinstance(size, int) and size >= 0, "raw artifact size is invalid")
    _require(not Path(logical_name).is_absolute() and ".." not in Path(logical_name).parts, "raw logical_name must be relative")
    if state == "available":
        _require(isinstance(sha256_digest, str) and len(sha256_digest) == 64 and all(c in "0123456789abcdef" for c in sha256_digest), "available raw artifact hash is invalid")
    else:
        _require(sha256_digest is None or isinstance(sha256_digest, str), "raw artifact hash is invalid")
    return {
        "artifact_id": f"artifact:{capture_id.split(':', 1)[1]}:{kind}",
        "kind": kind,
        "state": state,
        "size": size,
        "sha256": sha256_digest,
        "logical_name": logical_name,
        "storage_class": "immutable_local",
        "retention": "retain_failed_capture",
        "test_only": True,
    }


def make_raw_inventory(manifest: Mapping[str, Any], artifacts: list[Mapping[str, Any]]) -> dict[str, Any]:
    validate_session_manifest(manifest)
    _require(manifest["test_only"] is True, "P4 raw inventory requires test_only=true")
    _require(isinstance(artifacts, list) and artifacts, "raw inventory must retain artifacts")
    raw = [item for item in artifacts if item.get("kind") == "raw_trace"]
    _require(len(raw) == 1, "raw inventory requires exactly one raw_trace")
    seen: set[str] = set()
    copied: list[dict[str, Any]] = []
    for artifact in artifacts:
        item = dict(artifact)
        _require(isinstance(item.get("artifact_id"), str) and item["artifact_id"] not in seen, "raw inventory has duplicate artifact_id")
        seen.add(item["artifact_id"])
        _require(item.get("state") in _ARTIFACT_STATES, "raw inventory artifact state is invalid")
        _require(isinstance(item.get("size"), int) and item["size"] >= 0, "raw inventory artifact size is invalid")
        _require(isinstance(item.get("logical_name"), str) and not Path(item["logical_name"]).is_absolute() and ".." not in Path(item["logical_name"]).parts, "raw inventory logical_name is invalid")
        copied.append(item)
    return finalize_record(
        {
            "schema_version": P4_SCHEMA_VERSION,
            "p4_raw_inventory_id": f"p4raw:{manifest['capture_id'].split(':', 1)[1]}",
            "capture_id": manifest["capture_id"],
            "session_id": manifest["session_id"],
            "test_only": True,
            "artifacts": copied,
            "retention_policy": "retain_failed_capture",
            "prior_raw_inventory_ref": None,
        }
    )


def validate_raw_inventory(value: Mapping[str, Any]) -> None:
    _require(value.get("schema_version") == P4_SCHEMA_VERSION, "unsupported raw inventory schema version")
    _require(value.get("test_only") is True, "raw inventory requires test_only=true")
    _require(value.get("retention_policy") == "retain_failed_capture", "raw inventory retention policy is invalid")
    _require(value.get("record_digest") == record_digest(value), "raw inventory record_digest mismatch")
    _require(isinstance(value.get("artifacts"), list), "raw inventory artifacts is invalid")
    raw = [item for item in value["artifacts"] if item.get("kind") == "raw_trace"]
    _require(len(raw) == 1, "raw inventory requires exactly one raw_trace")


def p2_inventory_from_raw_inventory(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project a P4 raw inventory to the frozen P2 inventory without extensions."""

    validate_raw_inventory(value)
    artifacts: list[dict[str, Any]] = []
    for item in value["artifacts"]:
        available = item["state"] == "available"
        artifacts.append(
            {
                "artifact_id": item["artifact_id"],
                "kind": item["kind"],
                "availability": "available" if available else "missing",
                "size": item["size"],
                "hash": item["sha256"] if available else None,
                "logical_name": item["logical_name"],
                "storage_class": item["storage_class"],
                "retention": item["retention"],
            }
        )
    return finalize_p2_record(
        {
            "schema_version": "rtd-phase2-v1.0",
            "inventory_record_id": f"inventory:{value['capture_id'].split(':', 1)[1]}",
            "capture_id": value["capture_id"],
            "session_id": value["session_id"],
            "artifacts": artifacts,
            "prior_inventory_ref": None,
        }
    )


class CaptureStore:
    """P4 test-only capture store with capture-global collision protection."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        repository_root = Path(__file__).resolve().parents[1]
        _require(self.root != repository_root and repository_root not in self.root.parents, "capture root must be outside the repository")

    def prepare(self, manifest: Mapping[str, Any], export: Mapping[str, Any], snapshot: Mapping[str, Any]) -> Path:
        validate_session_manifest(manifest, export=export)
        _require(snapshot.get("config_hash") == export.get("p2_config_hash"), "prepared snapshot config_hash mismatch")
        self.root.mkdir(parents=True, exist_ok=True)
        index = self.root / ".capture_ids"
        index.mkdir(exist_ok=True)
        marker = index / _safe_component(str(manifest["capture_id"]), "capture_id")
        try:
            marker.mkdir()
        except FileExistsError as exc:
            raise P4ArtifactError("duplicate capture_id") from exc
        directory = self.root / _safe_component(str(manifest["session_id"]), "session_id") / _safe_component(str(manifest["capture_id"]), "capture_id")
        try:
            directory.mkdir(parents=True)
        except FileExistsError as exc:
            raise P4ArtifactError("duplicate capture/session directory") from exc
        _write_exclusive(directory / "session_manifest.json", canonical_json(dict(manifest)))
        _write_exclusive(directory / "collector_config_export.json", canonical_json(dict(export)))
        _write_exclusive(directory / "collector_config_snapshot.json", canonical_json(dict(snapshot)))
        return directory

    def import_raw(self, directory: str | Path, source: str | Path) -> dict[str, Any]:
        directory = Path(directory)
        _require(not (directory / "seal.json").exists(), "capture is sealed")
        source_path = Path(source)
        _require(source_path.is_file(), "raw source is missing")
        destination = directory / "raw" / "trace.trace"
        destination.parent.mkdir(exist_ok=True)
        _require(not destination.exists(), "raw artifact no-overwrite rejection")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with source_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    os.write(descriptor, chunk)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        manifest = json.loads((directory / "session_manifest.json").read_text(encoding="utf-8"))
        size, digest = sha256_file(destination)
        return make_raw_artifact(capture_id=manifest["capture_id"], state="available", size=size, sha256_digest=digest)

    def write_inventory(self, directory: str | Path, inventory: Mapping[str, Any]) -> Path:
        validate_raw_inventory(inventory)
        destination = Path(directory) / "raw_inventory.json"
        _require(not destination.exists(), "raw inventory no-overwrite rejection")
        _require(not (Path(directory) / "seal.json").exists(), "capture is sealed")
        try:
            _write_exclusive(destination, canonical_json(dict(inventory)))
        except FileExistsError as exc:
            raise P4ArtifactError("raw inventory no-overwrite rejection") from exc
        return destination

    def verify_inventory(self, directory: str | Path) -> dict[str, Any]:
        directory = Path(directory)
        inventory = json.loads((directory / "raw_inventory.json").read_text(encoding="utf-8"))
        validate_raw_inventory(inventory)
        for item in inventory["artifacts"]:
            if item["state"] == "available":
                target = directory / item["logical_name"]
                _require(target.is_file(), "raw artifact is missing")
                size, digest = sha256_file(target)
                _require((size, digest) == (item["size"], item["sha256"]), "raw artifact hash/size mismatch")
        return inventory

    def preserve_failure(self, directory: str | Path, *, reason_code: str) -> Path:
        directory = Path(directory)
        _require(reason_code.isascii() and reason_code, "failure reason is invalid")
        destination = directory / "failure_preserved.json"
        try:
            _write_exclusive(destination, canonical_json({"test_only": True, "reason_code": reason_code}))
        except FileExistsError as exc:
            raise P4ArtifactError("failure preservation no-overwrite rejection") from exc
        return destination

    def seal(self, directory: str | Path, *, seal_outcome: str = "sealed") -> Path:
        directory = Path(directory)
        _require(seal_outcome in {"sealed", "failed_preserved"}, "unsupported seal outcome")
        inventory = self.verify_inventory(directory)
        destination = directory / "seal.json"
        _require(not destination.exists(), "capture is already sealed")
        seal = finalize_record(
            {
                "schema_version": "rtd-p4-artifact-seal-v1",
                "capture_id": inventory["capture_id"],
                "session_id": inventory["session_id"],
                "raw_inventory_digest": inventory["record_digest"],
                "seal_outcome": seal_outcome,
                "test_only": True,
            }
        )
        _write_exclusive(destination, canonical_json(seal), mode=0o400)
        for path in directory.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return destination


class HardwareArtifactStore:
    """External, identity-derived storage for non-Case P4 hardware smoke only."""

    def __init__(self, external_root: str | Path, *, repository_root: str | Path | None = None) -> None:
        self.root = Path(external_root).resolve()
        repository = Path(repository_root).resolve() if repository_root is not None else Path(__file__).resolve().parents[1]
        _require(self.root != repository and repository not in self.root.parents, "hardware artifact root must be outside the repository")

    @staticmethod
    def _validate_identity(board_id: str, session_id: str, capture_id: str) -> None:
        _require(isinstance(board_id, str) and bool(_BOARD_ID.fullmatch(board_id)), "board_id is invalid")
        _safe_component(board_id, "board_id")
        from .contracts import validate_capture_identity

        try:
            validate_capture_identity(capture_id, session_id, None, "hardware_smoke_noncase", False)
        except P4ContractError as exc:
            raise P4ArtifactError(f"hardware capture identity is invalid: {exc}") from exc
        _safe_component(session_id, "session_id")
        _safe_component(capture_id, "capture_id")

    def _directory(self, board_id: str, session_id: str, capture_id: str) -> Path:
        self._validate_identity(board_id, session_id, capture_id)
        return self.root / _safe_component(board_id, "board_id") / _safe_component(session_id, "session_id") / "hardware_smoke_noncase" / _safe_component(capture_id, "capture_id")

    def _mkdir_capture_path(self, board_id: str, session_id: str, capture_id: str) -> Path:
        target = self._directory(board_id, session_id, capture_id)
        self.root.mkdir(parents=True, exist_ok=True)
        _require(not self.root.is_symlink(), "hardware artifact root cannot be a symlink")
        current = self.root
        for component in target.relative_to(self.root).parts:
            current = current / component
            if current.exists():
                _require(current.is_dir() and not current.is_symlink(), "hardware artifact path contains a symlink or non-directory")
            else:
                current.mkdir(mode=0o700)
        return target

    def _checked_capture_path(self, board_id: str, session_id: str, capture_id: str) -> Path:
        target = self._directory(board_id, session_id, capture_id)
        _require(not self.root.is_symlink(), "hardware artifact root cannot be a symlink")
        current = self.root
        for component in target.relative_to(self.root).parts:
            current = current / component
            _require(current.is_dir() and not current.is_symlink(), "hardware artifact path contains a symlink or non-directory")
        _require(target.is_dir(), "hardware capture directory is not prepared")
        _require(target.resolve() == target, "hardware artifact path escaped its canonical root")
        return target

    def prepare(self, manifest: Mapping[str, Any], export: Mapping[str, Any], *, board_id: str) -> Path:
        validate_session_manifest(manifest, export=export)
        _require(manifest["capture_mode"] == "hardware_smoke_noncase", "hardware layout requires hardware_smoke_noncase")
        _require(manifest["test_only"] is False and manifest["case_id"] is None, "hardware layout must be non-Case and non-test-only")
        target = self._directory(board_id, str(manifest["session_id"]), str(manifest["capture_id"]))
        _require(not target.exists(), "duplicate hardware board/session/capture directory")
        self._mkdir_capture_path(board_id, str(manifest["session_id"]), str(manifest["capture_id"]))
        layout = finalize_record({
            "schema_version": "rtd-p4-hardware-artifact-layout-v1", "layout_version": HARDWARE_LAYOUT_VERSION,
            "canonical_root": str(self.root), "board_id": board_id, "session_id": manifest["session_id"],
            "capture_mode": "hardware_smoke_noncase", "capture_id": manifest["capture_id"], "case_id": None,
            "session_manifest_digest": manifest["record_digest"], "collector_config_export_digest": export["record_digest"],
        })
        _write_exclusive(target / "session_manifest.json", canonical_json(dict(manifest)))
        _write_exclusive(target / "collector_config_export.json", canonical_json(dict(export)))
        _write_exclusive(target / "hardware_layout_manifest.json", canonical_json(layout))
        return target

    def resolve(self, *, board_id: str, session_id: str, capture_id: str) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
        target = self._checked_capture_path(board_id, session_id, capture_id)
        manifest = json.loads((target / "session_manifest.json").read_text(encoding="utf-8"))
        export = json.loads((target / "collector_config_export.json").read_text(encoding="utf-8"))
        layout = json.loads((target / "hardware_layout_manifest.json").read_text(encoding="utf-8"))
        _require(isinstance(manifest, dict) and isinstance(export, dict) and isinstance(layout, dict), "hardware capture metadata must be objects")
        validate_session_manifest(manifest, export=export)
        _require(manifest["capture_mode"] == "hardware_smoke_noncase" and manifest["test_only"] is False and manifest["case_id"] is None, "hardware capture identity is invalid")
        expected = {
            "schema_version": "rtd-p4-hardware-artifact-layout-v1", "layout_version": HARDWARE_LAYOUT_VERSION,
            "canonical_root": str(self.root), "board_id": board_id, "session_id": session_id,
            "capture_mode": "hardware_smoke_noncase", "capture_id": capture_id, "case_id": None,
            "session_manifest_digest": manifest["record_digest"], "collector_config_export_digest": export["record_digest"],
        }
        _require(layout.get("record_digest") == record_digest(layout), "hardware layout manifest digest mismatch")
        _require({key: layout.get(key) for key in expected} == expected, "hardware layout manifest identity mismatch")
        return target, manifest, export, layout

    def seal(self, *, board_id: str, session_id: str, capture_id: str) -> Path:
        target, _manifest, _export, layout = self.resolve(board_id=board_id, session_id=session_id, capture_id=capture_id)
        destination = target / "hardware_seal.json"
        _require(not destination.exists(), "hardware capture is already sealed")
        artifacts: list[dict[str, Any]] = []
        for path in sorted(target.iterdir(), key=lambda item: item.name):
            _require(not path.is_symlink(), "hardware capture contains a symlink")
            if path.is_file() and path.name != destination.name:
                size, digest = sha256_file(path)
                artifacts.append({"name": path.name, "size": size, "sha256": digest})
        seal = finalize_record({
            "schema_version": "rtd-p4-hardware-artifact-seal-v1", "layout_manifest_digest": layout["record_digest"],
            "board_id": board_id, "session_id": session_id, "capture_id": capture_id, "case_id": None, "artifacts": artifacts,
        })
        _write_exclusive(destination, canonical_json(seal), mode=0o400)
        # A sealed hardware bundle is immutable evidence.  Keep the seal and
        # every retained artifact read-only on filesystems that support chmod;
        # validation still rehashes all listed material.
        for path in target.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return destination

    def validate(self, *, board_id: str, session_id: str, capture_id: str) -> dict[str, Any]:
        target, _manifest, _export, layout = self.resolve(board_id=board_id, session_id=session_id, capture_id=capture_id)
        seal_path = target / "hardware_seal.json"
        _require(seal_path.is_file() and not seal_path.is_symlink(), "hardware capture is not sealed")
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        _require(isinstance(seal, dict) and seal.get("record_digest") == record_digest(seal), "hardware seal digest mismatch")
        _require(seal.get("layout_manifest_digest") == layout["record_digest"], "hardware seal layout mismatch")
        _require((seal.get("board_id"), seal.get("session_id"), seal.get("capture_id"), seal.get("case_id")) == (board_id, session_id, capture_id, None), "hardware seal identity mismatch")
        for item in seal.get("artifacts", []):
            _require(isinstance(item, dict) and set(item) == {"name", "size", "sha256"}, "hardware seal artifact is malformed")
            path = target / item["name"]
            _require(path.is_file() and not path.is_symlink(), "hardware sealed artifact is missing")
            _require(sha256_file(path) == (item["size"], item["sha256"]), "hardware sealed artifact changed")
        return seal


class CaptureArtifactStore:
    """No-overwrite store for one P4 session's imported artifacts."""

    def __init__(self, root: str | Path, *, repository_root: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        if repository_root is not None:
            repo = Path(repository_root).resolve()
            _require(self.root != repo and repo not in self.root.parents, "capture root must be outside the repository")

    def capture_dir(self, manifest: Mapping[str, Any]) -> Path:
        validate_session_manifest(manifest)
        return self.root / _safe_component(str(manifest["session_id"]), "session_id") / _safe_component(str(manifest["capture_id"]), "capture_id")

    def prepare(self, manifest: Mapping[str, Any], config_export: Mapping[str, Any]) -> Path:
        """Create exactly one immutable preparation directory for a capture."""

        validate_session_manifest(manifest, export=config_export)
        target = self.capture_dir(manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise P4ContractError("duplicate capture/session directory") from exc
        try:
            _write_exclusive(target / "session_manifest.json", canonical_json(dict(manifest)))
            _write_exclusive(target / "collector_config_export.json", canonical_json(dict(config_export)))
        except Exception:
            # The directory remains retained for audit; a caller must choose a new identity.
            raise
        return target

    def import_raw(
        self,
        manifest: Mapping[str, Any],
        source: str | Path,
        *,
        logical_name: str = "raw/trace.trace",
        state: str = "available",
        test_only: bool,
    ) -> dict[str, Any]:
        """Import an existing raw file with exclusive output creation.

        Only a test-only import is executable in this phase. A future hardware
        operator imports an already captured file under separate authorization.
        """

        validate_session_manifest(manifest)
        _require(test_only is True and manifest["test_only"] is True, "P4 import requires test_only=true")
        _require(state in _ARTIFACT_STATES, "unsupported raw artifact state")
        _require(not Path(logical_name).is_absolute() and ".." not in Path(logical_name).parts, "raw logical_name must be relative")
        target = self.capture_dir(manifest)
        _require(target.is_dir(), "capture directory is not prepared")
        destination = target / logical_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _require(not destination.exists(), "raw artifact collision/no-overwrite rejection")
        if state == "available":
            source_path = Path(source)
            _require(source_path.is_file(), "raw source is missing")
            with source_path.open("rb") as input_handle:
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    while True:
                        chunk = input_handle.read(1024 * 1024)
                        if not chunk:
                            break
                        os.write(descriptor, chunk)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            raw = destination.read_bytes()
            digest: str | None = file_digest(raw)
            size = len(raw)
        else:
            digest = None
            size = 0
        return {
            "artifact_id": f"raw:{manifest['capture_id'].split(':', 1)[1]}",
            "kind": "raw_trace",
            "state": state,
            "availability": "available" if state == "available" else "missing",
            "size": size,
            "hash": digest,
            "logical_name": logical_name,
            "storage_class": "p4_test_only_import",
            "retention": "retain_failed_and_invalid",
            "test_only": True,
        }

    def seal(self, manifest: Mapping[str, Any], artifacts: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Write one hash-bound seal and make retained files read-only when possible."""

        target = self.capture_dir(manifest)
        _require(target.is_dir(), "capture directory is not prepared")
        _require(not (target / "seal.json").exists(), "capture is already sealed")
        checks: list[dict[str, Any]] = []
        for artifact in artifacts:
            _require(artifact.get("test_only") is True, "P4 artifact must be marked test_only=true")
            logical_name = artifact.get("logical_name")
            _require(isinstance(logical_name, str), "artifact logical_name is missing")
            file_path = target / logical_name
            if artifact.get("state") == "available":
                _require(file_path.is_file(), "available raw artifact is missing")
                raw = file_path.read_bytes()
                _require(file_digest(raw) == artifact.get("hash"), "raw artifact changed before seal")
                _require(len(raw) == artifact.get("size"), "raw artifact size changed before seal")
            checks.append({"logical_name": logical_name, "hash": artifact.get("hash"), "state": artifact.get("state")})
        seal = finalize_record(
            {
                "schema_version": "rtd-p4-artifact-seal-v1",
                "capture_id": manifest["capture_id"],
                "session_id": manifest["session_id"],
                "session_manifest_digest": manifest["record_digest"],
                "artifacts": checks,
                "test_only": True,
            }
        )
        _write_exclusive(target / "seal.json", canonical_json(seal), mode=0o400)
        for path in target.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return seal

    def validate_seal(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        target = self.capture_dir(manifest)
        seal_path = target / "seal.json"
        _require(seal_path.is_file(), "capture is not sealed")
        import json

        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        _require(seal.get("test_only") is True, "seal lacks test_only=true")
        _require(seal.get("capture_id") == manifest["capture_id"], "seal capture_id mismatch")
        _require(seal.get("session_id") == manifest["session_id"], "seal session_id mismatch")
        _require(seal.get("session_manifest_digest") == manifest["record_digest"], "seal session manifest mismatch")
        _require(seal.get("record_digest") == finalize_record(seal)["record_digest"], "seal record_digest mismatch")
        for artifact in seal.get("artifacts", []):
            if artifact.get("state") == "available":
                raw = (target / artifact["logical_name"]).read_bytes()
                _require(file_digest(raw) == artifact["hash"], "sealed raw artifact hash mismatch")
        return seal

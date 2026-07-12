"""Read-only, directory-only P7.3 package manifest reader."""
from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import stat
from typing import Mapping

from parser.external_package_models import CONTRACT_VERSION, ExternalEvidencePackageManifest, PackageOpenReason, PackageOpenRequest
from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema

MANIFEST_NAME = "package_manifest.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_COUNT = 256
MAX_DECLARED_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_ENTRY_COUNT = 512
MAX_ACTUAL_PACKAGE_BYTES = 65 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_DIRECTORY_DEPTH = 32
MAX_PACKAGE_FILE_COUNT = 257


class PackageReadError(ValueError):
    def __init__(self, reason: PackageOpenReason, message: str) -> None:
        super().__init__(message); self.reason = reason


@dataclass(frozen=True)
class SnapshotEntry:
    relative_path: str; device: int; inode: int; mode: int; size: int; links: int


@dataclass(frozen=True)
class DirectoryPackageRead:
    request: PackageOpenRequest; root: Path; manifest: ExternalEvidencePackageManifest; snapshot: tuple[SnapshotEntry, ...]; manifest_sha256: str


def _reject_symlink_ancestor(root: Path) -> None:
    absolute = root.absolute()
    for parent in (absolute.parent, *absolute.parents):
        if parent == parent.parent: break
        try:
            if os.path.islink(parent): raise PackageReadError(PackageOpenReason.SYMLINK_FORBIDDEN, "package root ancestor must not be a symlink")
        except OSError as exc: raise PackageReadError(PackageOpenReason.MANIFEST_MISSING, "package root ancestor is unreadable") from exc


def _tree_snapshot(root: Path) -> tuple[SnapshotEntry, ...]:
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise PackageReadError(PackageOpenReason.MANIFEST_MISSING, "package root is missing") from exc
    if stat.S_ISLNK(root_stat.st_mode): raise PackageReadError(PackageOpenReason.SYMLINK_FORBIDDEN, "package root must not be a symlink")
    if stat.S_ISREG(root_stat.st_mode): raise PackageReadError(PackageOpenReason.ARCHIVE_UNSUPPORTED, "P7.3 supports directory packages only")
    if not stat.S_ISDIR(root_stat.st_mode): raise PackageReadError(PackageOpenReason.MANIFEST_INVALID, "package root must be a directory")
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PackageReadError(PackageOpenReason.SYMLINK_FORBIDDEN, "package root cannot be opened without following links") from exc
    opened_root = os.fstat(root_fd)
    if (opened_root.st_dev, opened_root.st_ino, stat.S_IFMT(opened_root.st_mode)) != (root_stat.st_dev, root_stat.st_ino, stat.S_IFMT(root_stat.st_mode)):
        os.close(root_fd); raise PackageReadError(PackageOpenReason.PACKAGE_MUTATION, "package root changed during open")
    entries: list[SnapshotEntry] = []
    total_bytes = 0
    try:
        def walk(fd: int, prefix: str, depth: int) -> None:
            nonlocal total_bytes
            if depth > MAX_DIRECTORY_DEPTH: raise PackageReadError(PackageOpenReason.ARTIFACT_COUNT_LIMIT, "package directory depth exceeded")
            with os.scandir(fd) as scanned:
                items = sorted(scanned, key=lambda entry: entry.name)
            for item in items:
                path = f"{prefix}/{item.name}" if prefix else item.name
                try: info = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
                except OSError as exc: raise PackageReadError(PackageOpenReason.MANIFEST_UNREADABLE, "package entry cannot be statted") from exc
                if stat.S_ISLNK(info.st_mode): raise PackageReadError(PackageOpenReason.SYMLINK_FORBIDDEN, "package contains a symlink")
                if stat.S_ISDIR(info.st_mode):
                    if len(entries) >= MAX_PACKAGE_ENTRY_COUNT: raise PackageReadError(PackageOpenReason.ARTIFACT_COUNT_LIMIT, "package entry limit exceeded")
                    entries.append(SnapshotEntry(path,info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_nlink))
                    try: child = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except OSError as exc: raise PackageReadError(PackageOpenReason.PACKAGE_MUTATION, "package directory changed during read") from exc
                    child_stat = os.fstat(child)
                    if (child_stat.st_dev,child_stat.st_ino,stat.S_IFMT(child_stat.st_mode)) != (info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode)):
                        os.close(child); raise PackageReadError(PackageOpenReason.PACKAGE_MUTATION, "package directory changed during open")
                    try: walk(child,path,depth + 1)
                    finally: os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1: raise PackageReadError(PackageOpenReason.HARDLINK_FORBIDDEN, "package hard links are forbidden")
                    if len(entries) >= MAX_PACKAGE_ENTRY_COUNT or sum(1 for entry in entries if stat.S_ISREG(entry.mode)) >= MAX_PACKAGE_FILE_COUNT or total_bytes + info.st_size > MAX_ACTUAL_PACKAGE_BYTES: raise PackageReadError(PackageOpenReason.SIZE_LIMIT, "package tree limit exceeded")
                    total_bytes += info.st_size; entries.append(SnapshotEntry(path,info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_nlink))
                else: raise PackageReadError(PackageOpenReason.SPECIAL_FILE, "package contains a non-regular file")
        walk(root_fd, "", 0)
    finally: os.close(root_fd)
    return tuple(entries)


def _json_depth(value: object, depth: int = 0) -> int:
    if depth > MAX_JSON_DEPTH: raise PackageReadError(PackageOpenReason.MANIFEST_INVALID, "manifest JSON depth limit exceeded")
    if isinstance(value, Mapping): return max([depth, *(_json_depth(child, depth + 1) for child in value.values())])
    if isinstance(value, list): return max([depth, *(_json_depth(child, depth + 1) for child in value)])
    return depth


def _path_reason(value: object) -> PackageOpenReason | None:
    if not isinstance(value, str): return None
    if value.startswith("/") or value.startswith("//") or len(value) > 1 and value[1] == ":": return PackageOpenReason.ABSOLUTE_PATH
    if "\\" in value or "\x00" in value or any(part in {"", "."} for part in value.split("/")): return PackageOpenReason.PATH_NORMALIZATION
    if ".." in value.split("/"): return PackageOpenReason.PATH_TRAVERSAL
    return None


def _raw_path_check(value: object) -> None:
    if not isinstance(value, Mapping): return
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list): return
    paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping): continue
        path = artifact.get("relative_path"); reason = _path_reason(path)
        if reason is not None: raise PackageReadError(reason, "invalid artifact path")
        if isinstance(path, str) and path in paths: raise PackageReadError(PackageOpenReason.PATH_DUPLICATE, "duplicate artifact path")
        if isinstance(path, str): paths.add(path)
    source = value.get("source")
    if isinstance(source, Mapping):
        reason = _path_reason(source.get("source_path"))
        if reason is not None: raise PackageReadError(reason, "invalid source path")
    references = value.get("external_references")
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, Mapping):
                reason = _path_reason(reference.get("source_path"))
                if reason is not None: raise PackageReadError(reason, "invalid external reference path")


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result: raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = item
    return result


def read_directory_package(package_root: str | Path, *, expected_contract_version: str = CONTRACT_VERSION) -> DirectoryPackageRead:
    root = Path(package_root); _reject_symlink_ancestor(root); snapshot = _tree_snapshot(root); manifest_info = next((entry for entry in snapshot if entry.relative_path == MANIFEST_NAME), None)
    if manifest_info is None: raise PackageReadError(PackageOpenReason.MANIFEST_MISSING, "manifest is missing")
    if manifest_info.size > MAX_MANIFEST_BYTES: raise PackageReadError(PackageOpenReason.MANIFEST_SIZE_LIMIT, "manifest exceeds size limit")
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: fd = os.open(MANIFEST_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        finally: os.close(root_fd)
        try:
            info = os.fstat(fd)
            if (info.st_dev,info.st_ino,info.st_size,info.st_nlink) != (manifest_info.device,manifest_info.inode,manifest_info.size,manifest_info.links): raise PackageReadError(PackageOpenReason.PACKAGE_MUTATION, "manifest changed during read")
            raw = os.read(fd, manifest_info.size + 1)
            if len(raw) != manifest_info.size: raise PackageReadError(PackageOpenReason.PACKAGE_MUTATION, "manifest changed during read")
        finally: os.close(fd)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs); _json_depth(value); _raw_path_check(value)
    except PackageReadError: raise
    except UnicodeDecodeError as exc: raise PackageReadError(PackageOpenReason.MANIFEST_UNREADABLE, "manifest is not UTF-8") from exc
    except ValueError as exc: raise PackageReadError(PackageOpenReason.MANIFEST_INVALID, "manifest contains duplicate JSON keys") from exc
    except (OSError, json.JSONDecodeError) as exc: raise PackageReadError(PackageOpenReason.MANIFEST_INVALID, "manifest is unreadable or invalid JSON") from exc
    if not isinstance(value, Mapping): raise PackageReadError(PackageOpenReason.MANIFEST_INVALID, "manifest must be an object")
    artifacts = value.get("artifacts")
    if isinstance(artifacts, list):
        if len(artifacts) > MAX_ARTIFACT_COUNT: raise PackageReadError(PackageOpenReason.ARTIFACT_COUNT_LIMIT, "artifact count limit exceeded")
        declared = sum(item.get("bytes",0) for item in artifacts if isinstance(item,Mapping) and isinstance(item.get("bytes"),int) and not isinstance(item.get("bytes"),bool))
        if declared > MAX_DECLARED_PACKAGE_BYTES: raise PackageReadError(PackageOpenReason.SIZE_LIMIT, "declared byte limit exceeded")
    schema_reason = validate_schema(load_schema("external_evidence_package.schema.json"), value)
    if schema_reason is not None: raise PackageReadError(PackageOpenReason.MANIFEST_INVALID, schema_reason)
    try: manifest = ExternalEvidencePackageManifest.from_dict(value)
    except ValueError as exc: raise PackageReadError(PackageOpenReason.MANIFEST_INVALID, str(exc)) from exc
    if manifest.contract_version != expected_contract_version: raise PackageReadError(PackageOpenReason.CONTRACT_VERSION_UNSUPPORTED, "contract version is unsupported")
    return DirectoryPackageRead(PackageOpenRequest(str(root), expected_contract_version, True), root, manifest, snapshot, hashlib.sha256(raw).hexdigest())

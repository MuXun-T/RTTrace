"""Deterministic P7.3 integrity validation without semantic replay."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Mapping

from parser.external_package_models import PackageKind, PackageOpenReason, PackageOpenReport, PackageOpenResult, sha256_identity
from parser.external_package_reader import DirectoryPackageRead, MANIFEST_NAME, _tree_snapshot

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_SHA256 = "ad15a481e2dde8eea0ef2b6e3feecc083e30699532296f27d1095cacaedde54b"


@dataclass(frozen=True)
class FrozenSourceDescriptor:
    source_id: str; artifact_id: str; local_path: str; bytes: int; sha256: str
    repository: str; repository_commit: str; source_path: str; rtos_name: str
    trace_format: str; generation_mode: str; hardware_validation: bool
    acquisition_status: str; license_spdx: str; source_kind: str


@dataclass(frozen=True)
class FrozenSourceDescriptorSet:
    descriptors: tuple[FrozenSourceDescriptor, ...]
    sources: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True)
class SourceArtifactSnapshot:
    exists: bool; device: int | None; inode: int | None; mode: int | None
    bytes: int | None; sha256: str | None


@dataclass(frozen=True)
class PackageEntrySnapshot:
    exists: bool; device: int | None; inode: int | None; mode: int | None
    bytes: int | None; sha256: str | None


@dataclass(frozen=True)
class PackageRootSnapshot:
    exists: bool; is_directory: bool; is_symlink: bool; device: int | None
    inode: int | None; file_type: int | None; entries: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ValidationSnapshot:
    source: tuple[tuple[tuple[str, str], SourceArtifactSnapshot], ...]
    root: PackageRootSnapshot
    package: tuple[tuple[str, PackageEntrySnapshot], ...]


@dataclass(frozen=True)
class ValidationContext:
    descriptors: FrozenSourceDescriptorSet | None


def _digest_bytes(data: bytes) -> str: return hashlib.sha256(data).hexdigest()


def _repo_read(relative_path: str) -> tuple[bytes, tuple[int, int, int, int, int]]:
    parts = Path(relative_path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or Path(relative_path).is_absolute(): raise OSError("unsafe repository path")
    root_fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW); current = root_fd
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            if current != root_fd: os.close(current)
            current = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1: raise OSError("source is not regular")
            raw = b"".join(iter(lambda: os.read(fd, 65536), b"")); after = os.fstat(fd)
            identity = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_nlink)
            if len(raw) != before.st_size or identity != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_nlink): raise OSError("source changed during read")
            return raw, identity
        finally: os.close(fd)
    finally:
        try:
            if current != root_fd: os.close(current)
            os.close(root_fd)
        except OSError: pass


def _load_inventory() -> dict[str, object] | None:
    try:
        raw, _ = _repo_read("docs/phase7_external_trace_sources/source_inventory.json")
        value = json.loads(raw)
        return value if _digest_bytes(raw) == INVENTORY_SHA256 and isinstance(value, dict) else None
    except (OSError, ValueError, TypeError): return None


def _freeze_descriptors(inventory: Mapping[str, object]) -> FrozenSourceDescriptorSet | None:
    try:
        source_rows = inventory["sources"]
        if not isinstance(source_rows, list): return None
        sources: dict[str, Mapping[str, object]] = {}; descriptors: list[FrozenSourceDescriptor] = []
        for source in source_rows:
            if not isinstance(source, Mapping) or not isinstance(source.get("source_id"), str): return None
            source_id = source["source_id"]; sources[source_id] = MappingProxyType(dict(source))
            for artifact in source.get("artifacts", []):
                if not isinstance(artifact, Mapping): return None
                descriptors.append(FrozenSourceDescriptor(source_id, str(artifact["artifact_id"]), str(artifact["local_path"]), int(artifact["bytes"]), str(artifact["sha256"]), str(source["repository"]), str(source["repository_commit"]), str(artifact["source_path"]), str(source["rtos_name"]), str(source["trace_format"]), str(source["generation_mode"]), source["hardware_validation"] is False, str(source["acquisition_status"]), str(source["license_spdx"]), str(source["data_class"])))
        return FrozenSourceDescriptorSet(tuple(sorted(descriptors, key=lambda item: (item.source_id, item.artifact_id))), MappingProxyType(sources))
    except (KeyError, TypeError, ValueError): return None


def _validation_context() -> ValidationContext:
    # Inventory is read exactly once for this validation and then never consulted again.
    inventory = _load_inventory()
    return ValidationContext(_freeze_descriptors(inventory) if inventory is not None else None)


def _source_artifact_snapshot(descriptor: FrozenSourceDescriptor) -> SourceArtifactSnapshot:
    try:
        raw, identity = _repo_read(descriptor.local_path)
        return SourceArtifactSnapshot(True, identity[0], identity[1], identity[2], len(raw), _digest_bytes(raw))
    except OSError: return SourceArtifactSnapshot(False, None, None, None, None, None)


def _source_snapshot(descriptors: FrozenSourceDescriptorSet | None) -> tuple[tuple[tuple[tuple[str, str], SourceArtifactSnapshot], ...], bool]:
    if descriptors is None: return (), False
    values = tuple(((item.source_id, item.artifact_id), _source_artifact_snapshot(item)) for item in descriptors.descriptors)
    valid = all(snapshot.exists and snapshot.bytes == item.bytes and snapshot.sha256 == item.sha256 for item, (_, snapshot) in zip(descriptors.descriptors, values))
    return values, valid


def _package_root_snapshot(root: Path) -> PackageRootSnapshot:
    try:
        info = os.lstat(root); entries = tuple((item.relative_path, stat.S_IFMT(item.mode)) for item in _tree_snapshot(root))
        return PackageRootSnapshot(True, stat.S_ISDIR(info.st_mode), stat.S_ISLNK(info.st_mode), info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), entries)
    except (OSError, ValueError): return PackageRootSnapshot(False, False, False, None, None, None, ())


def _package_file_snapshot(root: Path, relative_path: str) -> PackageEntrySnapshot:
    try:
        parts = relative_path.split("/"); root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW); current = root_fd
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                if current != root_fd: os.close(current)
                current = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1: raise OSError("not a regular file")
                raw = b"".join(iter(lambda: os.read(fd, 65536), b"")); after = os.fstat(fd)
                if len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_nlink) != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_nlink): raise OSError("package changed during read")
                return PackageEntrySnapshot(True, before.st_dev, before.st_ino, before.st_mode, len(raw), _digest_bytes(raw))
            finally: os.close(fd)
        finally:
            try:
                if current != root_fd: os.close(current)
                os.close(root_fd)
            except OSError: pass
    except OSError: return PackageEntrySnapshot(False, None, None, None, None, None)


def _package_snapshot(read: DirectoryPackageRead) -> tuple[PackageRootSnapshot, tuple[tuple[str, PackageEntrySnapshot], ...]]:
    paths = (MANIFEST_NAME, *(item.relative_path for item in read.manifest.artifacts))
    return _package_root_snapshot(read.root), tuple((path, _package_file_snapshot(read.root, path)) for path in paths)


def _artifact_identity(record: dict[str, object]) -> str:
    value = dict(record); del value["artifact_identity"]; return sha256_identity(value)


def _source_identity(source: dict[str, object]) -> str:
    return sha256_identity({"source_kind": source["source_kind"], "source_trace_id": source["source_artifact_id"], "source_trace_checksum": source["source_checksum"], "source_trace_bytes": source["source_bytes"], "source_format": source["trace_format"], "source_format_version": source["source_format_version"], "acquisition_status": source["acquisition_status"]})


def _sealed_reasons(read: DirectoryPackageRead) -> list[PackageOpenReason]:
    allowed = {MANIFEST_NAME, *(item.relative_path for item in read.manifest.artifacts)}
    return [PackageOpenReason.UNDECLARED_ARTIFACT for item in read.snapshot if stat.S_ISREG(item.mode) and item.relative_path not in allowed]


def _provenance_reasons(read: DirectoryPackageRead, descriptors: FrozenSourceDescriptorSet | None) -> list[PackageOpenReason]:
    manifest = read.manifest; source = None if descriptors is None else descriptors.sources.get(manifest.source.source_id); reasons: list[PackageOpenReason] = []
    if source is None: return [PackageOpenReason.SOURCE_IDENTITY_MISMATCH]
    for key in ("repository", "repository_commit", "rtos_name", "trace_format", "generation_mode", "license_spdx", "acquisition_status"):
        if getattr(manifest.source, key) != source[key]: reasons.append(PackageOpenReason.SOURCE_IDENTITY_MISMATCH)
    if manifest.source.hardware_validation is not False or source["hardware_validation"] is not False or manifest.source.source_kind != source["data_class"] or manifest.source.source_format_version != "p7.2-source-inventory-v1": reasons.append(PackageOpenReason.SOURCE_IDENTITY_MISMATCH)
    status = source["acquisition_status"]
    if status == "acquired":
        if manifest.package_kind is not PackageKind.SELF_CONTAINED or manifest.external_references: reasons.append(PackageOpenReason.SOURCE_IDENTITY_MISMATCH)
        primary = next((item for item in descriptors.descriptors if item.source_id == manifest.source.source_id and item.artifact_id == manifest.source.source_artifact_id), None)
        if primary is None or (manifest.source.source_path, manifest.source.source_checksum, manifest.source.source_bytes) != (primary.source_path, primary.sha256, primary.bytes): reasons.append(PackageOpenReason.SOURCE_IDENTITY_MISMATCH)
    else:
        if manifest.package_kind not in {PackageKind.METADATA_ONLY, PackageKind.REFERENCED_EXTERNAL} or manifest.artifacts or any(getattr(manifest.source, name) is not None for name in ("source_artifact_id", "source_checksum", "source_bytes")) or manifest.source.source_path != source["source_path"]: reasons.append(PackageOpenReason.SOURCE_IDENTITY_MISMATCH)
    for artifact in manifest.artifacts:
        expected = next((item for item in descriptors.descriptors if item.source_id == manifest.source.source_id and item.artifact_id == artifact.source_artifact_id), None)
        if expected is None or artifact.bytes != expected.bytes or artifact.sha256 != expected.sha256: reasons.append(PackageOpenReason.SOURCE_IDENTITY_MISMATCH)
        if artifact.content_version != "p7.2-source-inventory-v1" or artifact.provenance_reference != f"{manifest.source.source_id}:{artifact.source_artifact_id}" or _artifact_identity(artifact.to_dict()) != artifact.artifact_identity: reasons.append(PackageOpenReason.ARTIFACT_IDENTITY_MISMATCH)
    for reference in manifest.external_references:
        ref_source = descriptors.sources.get(reference.source_id)
        if ref_source is None or any(getattr(reference, key) != ref_source[key] for key in ("repository", "repository_commit", "license_spdx")):
            reasons.append(PackageOpenReason.EXTERNAL_REFERENCE_IDENTITY_MISMATCH); continue
        if reference.reference_kind == "provenance_license" and (reference.source_path != "LICENSE" or reference.expected_sha256 != ref_source["license_sha256"]): reasons.append(PackageOpenReason.EXTERNAL_REFERENCE_IDENTITY_MISMATCH)
        if reference.reference_kind == "trace_content": reasons.append(PackageOpenReason.EXTERNAL_REFERENCE_IDENTITY_MISMATCH)
        record = reference.to_dict(); del record["reference_identity"]
        if sha256_identity(record) != reference.reference_identity: reasons.append(PackageOpenReason.EXTERNAL_REFERENCE_IDENTITY_MISMATCH)
    return reasons


def validate_package(read: DirectoryPackageRead) -> PackageOpenReport:
    context = _validation_context(); source_before, source_before_valid = _source_snapshot(context.descriptors); root_before, package_before = _package_snapshot(read)
    manifest = read.manifest; reasons = _sealed_reasons(read); required_total = sum(item.required for item in manifest.artifacts); required_present = 0; optional_total = len(manifest.artifacts) - required_total; checksum_pass = checksum_fail = size_pass = size_fail = 0
    for artifact in manifest.artifacts:
        snapshot = dict(package_before).get(artifact.relative_path)
        original = next((item for item in read.snapshot if item.relative_path == artifact.relative_path), None)
        if snapshot is None or not snapshot.exists or original is None or (snapshot.device, snapshot.inode, snapshot.mode, snapshot.bytes) != (original.device, original.inode, original.mode, original.size):
            if artifact.required: reasons.append(PackageOpenReason.REQUIRED_ARTIFACT_MISSING)
            continue
        if artifact.required and snapshot.bytes == 0: reasons.append(PackageOpenReason.ZERO_BYTE_REQUIRED_ARTIFACT)
        if snapshot.bytes != artifact.bytes: size_fail += 1; reasons.append(PackageOpenReason.ARTIFACT_SIZE_MISMATCH)
        else: size_pass += 1
        if snapshot.sha256 != artifact.sha256: checksum_fail += 1; reasons.append(PackageOpenReason.ARTIFACT_CHECKSUM_MISMATCH)
        else: checksum_pass += 1
        if artifact.required: required_present += 1
    if _source_identity(manifest.source.to_dict()) != manifest.source.source_identity: reasons.append(PackageOpenReason.SOURCE_IDENTITY_MISMATCH)
    if sha256_identity({"validation_mode": "offline", "package_profile": "p7.3-directory-v1"}) != manifest.configuration_identity: reasons.append(PackageOpenReason.CONFIGURATION_IDENTITY_MISMATCH)
    if sha256_identity(manifest.identity_input()) != manifest.package_identity: reasons.append(PackageOpenReason.IDENTITY_MISMATCH)
    reasons.extend(_provenance_reasons(read, context.descriptors))
    source_after, source_after_valid = _source_snapshot(context.descriptors); root_after, package_after = _package_snapshot(read)
    if context.descriptors is None: source_mutation = 1
    elif len(source_before) != len(context.descriptors.descriptors) or len(source_after) != len(context.descriptors.descriptors): source_mutation = len(context.descriptors.descriptors)
    else: source_mutation = sum(before != after or not before.exists or before.bytes != descriptor.bytes or before.sha256 != descriptor.sha256 or not after.exists or after.bytes != descriptor.bytes or after.sha256 != descriptor.sha256 for descriptor, (_, before), (_, after) in zip(context.descriptors.descriptors, source_before, source_after))
    reader_entries = {item.relative_path: item for item in read.snapshot}
    def changed(path: str, before: PackageEntrySnapshot, after: PackageEntrySnapshot) -> bool:
        original = reader_entries.get(path)
        return before != after or original is None or not before.exists or (before.device, before.inode, before.mode, before.bytes) != (original.device, original.inode, original.mode, original.size) or (path == MANIFEST_NAME and before.sha256 != read.manifest_sha256)
    package_mutation = int(root_before != root_after) + sum(changed(path, before, after) for (path, before), (_, after) in zip(package_before, package_after))
    if source_mutation or not source_before_valid or not source_after_valid: reasons.append(PackageOpenReason.PACKAGE_SOURCE_MUTATION)
    if package_mutation: reasons.append(PackageOpenReason.PACKAGE_MUTATION)
    reasons = sorted(set(reasons), key=lambda item: item.value)
    if reasons: result = PackageOpenResult.INVALID
    elif manifest.checksum_algorithm != "sha256": result = PackageOpenResult.UNSUPPORTED; reasons = [PackageOpenReason.CHECKSUM_ALGORITHM_UNSUPPORTED]
    elif manifest.package_kind is PackageKind.HYBRID: result = PackageOpenResult.UNSUPPORTED; reasons = [PackageOpenReason.KIND_UNSUPPORTED]
    else:
        unavailable = [item for item in manifest.external_references if item.required and item.availability != "declared"]
        if unavailable: reasons = [PackageOpenReason.REF_LICENSE_BLOCKED if item.availability == "license_blocked" else PackageOpenReason.REF_UNAVAILABLE for item in unavailable]; result = PackageOpenResult.BLOCKED
        else: result = PackageOpenResult.OPENED if manifest.package_kind is PackageKind.SELF_CONTAINED else PackageOpenResult.OPENED_REFERENCE_ONLY
    complete = result in {PackageOpenResult.OPENED, PackageOpenResult.OPENED_REFERENCE_ONLY} and required_present == required_total
    return PackageOpenReport(manifest.package_identity, manifest.package_kind, result, tuple(reasons), True, True, required_total, required_present, optional_total, checksum_pass, checksum_fail, size_pass, size_fail, len(manifest.external_references), len(manifest.external_references), complete, source_mutation, package_mutation, 0, 0, 0, tuple(manifest.limitations))

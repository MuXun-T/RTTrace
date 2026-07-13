"""Materialize frozen P7.2 traces into temporary P7.3 case packages."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterator

from parser.external_package_models import ExternalEvidencePackageManifest
from tool import run_external_package_reopen


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FIXTURE_ROOT = "tests/python/fixtures/external_validation/packages/per_case"
RAW_FIXTURE_ROOT = "tests/python/fixtures/external_validation/sources/freertos_btf_trace/raw"
TEMP_PARENT = Path("/tmp")


@dataclass(frozen=True)
class CasePackageSpec:
    case_id: str
    package_id: str
    artifact_id: str
    raw_name: str

    @property
    def manifest_relative_path(self) -> str:
        return f"{PACKAGE_FIXTURE_ROOT}/{self.case_id}/package_manifest.json"

    @property
    def raw_relative_path(self) -> str:
        return f"{RAW_FIXTURE_ROOT}/{self.raw_name}"


_CASE_SPECS = (
    CasePackageSpec("freertos_btf_1core", "p7.3.freertos_btf_1core.v1", "btf_1core", "example.btf"),
    CasePackageSpec("freertos_vcd_1core", "p7.3.freertos_vcd_1core.v1", "vcd_1core", "example.vcd"),
    CasePackageSpec("freertos_btf_4cores", "p7.3.freertos_btf_4cores.v1", "btf_4cores", "example-4cores.btf"),
    CasePackageSpec("freertos_btf_50k", "p7.3.freertos_btf_50k.v1", "btf_50k", "example-50k.btf"),
)


def case_package_specs() -> tuple[CasePackageSpec, ...]:
    return _CASE_SPECS


def _relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or "\\" in value or "\x00" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("case package path must be repository-relative")
    return path


def read_regular_no_follow(relative_path: str) -> bytes:
    """Read a repository regular file once, rejecting links and replacement."""
    path = _relative_path(relative_path)
    root_fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current = root_fd
    try:
        for part in path.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            if current != root_fd:
                os.close(current)
            current = child
        fd = os.open(path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
        before = os.fstat(fd)
        try:
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("case package input must be a non-linked regular file")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_nlink) != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_nlink):
                raise ValueError("case package input changed while read")
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        if current != root_fd:
            os.close(current)
        os.close(root_fd)


def _temporary_parent() -> str:
    fd = os.open(TEMP_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError("temporary package parent must be a directory")
    finally:
        os.close(fd)
    return str(TEMP_PARENT)


def _manifest(spec: CasePackageSpec) -> tuple[bytes, ExternalEvidencePackageManifest]:
    raw = read_regular_no_follow(spec.manifest_relative_path)
    value = json.loads(raw.decode("utf-8"))
    manifest = ExternalEvidencePackageManifest.from_dict(value)
    if len(manifest.artifacts) != 1:
        raise ValueError("case package must declare exactly one artifact")
    artifact = manifest.artifacts[0]
    if artifact.artifact_id != spec.artifact_id or artifact.source_artifact_id != spec.artifact_id or artifact.relative_path != f"artifact/{spec.raw_name}":
        raise ValueError("case package manifest does not match case specification")
    return raw, manifest


@contextmanager
def materialized_case_package(spec: CasePackageSpec) -> Iterator[Path]:
    manifest_raw, manifest = _manifest(spec)
    artifact = manifest.artifacts[0]
    source_raw = read_regular_no_follow(spec.raw_relative_path)
    if len(source_raw) != artifact.bytes or hashlib.sha256(source_raw).hexdigest() != artifact.sha256:
        raise ValueError("frozen raw trace does not match case package manifest")
    with tempfile.TemporaryDirectory(prefix="p7_3_case_package_", dir=_temporary_parent()) as directory:
        package = Path(directory) / "package"
        artifact_dir = package / "artifact"
        artifact_dir.mkdir(parents=True)
        (package / "package_manifest.json").write_bytes(manifest_raw)
        (artifact_dir / spec.raw_name).write_bytes(source_raw)
        yield package


def reopen_case(spec: CasePackageSpec, output: Path) -> int:
    with materialized_case_package(spec) as package:
        return run_external_package_reopen.main(["--package", str(package), "--output", str(output), "--offline"])


def reproduce_case_report(spec: CasePackageSpec) -> bytes:
    with tempfile.TemporaryDirectory(prefix="p7_3_case_report_", dir=_temporary_parent()) as directory:
        output = Path(directory) / "report.json"
        if reopen_case(spec, output) != 0:
            raise ValueError("case package reopen did not succeed")
        return read_regular_no_follow_from(output)


def read_regular_no_follow_from(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("report must be a non-linked regular file")
        raw = b"".join(iter(lambda: os.read(fd, 65536), b""))
        if len(raw) != info.st_size:
            raise ValueError("report changed while read")
        return raw
    finally:
        os.close(fd)

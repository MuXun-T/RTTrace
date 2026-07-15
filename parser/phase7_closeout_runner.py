"""Read-only validation and canonicalization for the P7.8 closeout manifest."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from typing import Mapping

from parser.phase7_closeout_models import Phase7CloseoutManifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest(value: object, root: Path) -> Phase7CloseoutManifest:
    manifest = Phase7CloseoutManifest.from_dict(value)
    commits = [commit for _, commit in manifest.freeze_commits]
    commits.append(str(manifest.protected_p6["freeze_commit"]))
    for commit in commits:
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"missing frozen commit: {commit}")
    for row in manifest.frozen_artifacts:
        path = root / str(row["relative_path"])
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing frozen artifact: {row['relative_path']}")
        if _sha256(path) != row["sha256"]:
            raise ValueError(f"frozen artifact checksum mismatch: {row['relative_path']}")
    paths = manifest.schema_mirrors["paths"]
    first, second = (root / str(paths[0]), root / str(paths[1]))
    if not first.is_file() or not second.is_file() or first.read_bytes() != second.read_bytes():
        raise ValueError("schema mirrors are not byte-identical")
    if _sha256(first) != manifest.schema_mirrors["sha256"]:
        raise ValueError("schema mirror checksum mismatch")
    return manifest


def canonical_audit(value: object, root: Path) -> bytes:
    return validate_manifest(value, root).canonical_bytes()

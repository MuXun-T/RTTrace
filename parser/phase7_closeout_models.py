"""Deterministic, fail-closed Phase 7 P7.8 closeout manifest model."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from collections.abc import Mapping


VERSION = "p7.8-closeout-v1"
CONTRACT_VERSION = "phase7-external-validation-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEAK = re.compile(
    r"(?:^|[_ .:/-])(tmp|temp|pid|hostname|username|timestamp|datetime|random|uuid)(?:$|[_ .:/-])|"
    r"[A-Za-z]:[\\/]|(?:^|/)home/|(?:^|/)tmp/|\d{4}-\d{2}-\d{2}",
    re.IGNORECASE,
)
_TOP_LEVEL = {
    "phase7_closeout_version",
    "contract_version",
    "freeze_commits",
    "protected_p6",
    "frozen_artifacts",
    "schema_mirrors",
    "regression",
    "identity",
    "no_mutation",
    "claim_boundary",
    "issues",
    "reproducibility",
}
_PHASES = ("p7.1", "p7.2", "p7.3", "p7.4", "p7.5", "p7.6", "p7.7")


def canonical_json(value: object) -> bytes:
    """Canonical report bytes: UTF-8 JSON, sorted compact keys, one LF."""
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256_identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _stable_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii() or "\x00" in value:
        raise ValueError(f"{name} must be non-empty ASCII text")
    if _LEAK.search(value):
        raise ValueError(f"{name} contains unstable environment data")
    return value


def normalize_relative_path(value: object, name: str = "relative_path") -> str:
    if not isinstance(value, str) or not value or not value.isascii() or "\\" in value:
        raise ValueError(f"{name} must be a repository-relative POSIX path")
    if value.startswith("/") or value.startswith("./") or "\x00" in value:
        raise ValueError(f"{name} must be a repository-relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{name} is not normalized")
    if _LEAK.search(value):
        raise ValueError(f"{name} contains unstable environment data")
    return value


def _walk_for_leaks(value: object, path: str = "manifest") -> None:
    if isinstance(value, str) and _LEAK.search(value):
        raise ValueError(f"{path} contains unstable environment data")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _walk_for_leaks(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_for_leaks(child, f"{path}[{index}]")


def _require_keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields are invalid")


@dataclass(frozen=True)
class Phase7CloseoutManifest:
    freeze_commits: tuple[tuple[str, str], ...]
    protected_p6: dict[str, object]
    frozen_artifacts: tuple[dict[str, object], ...]
    schema_mirrors: dict[str, object]
    regression: dict[str, object]
    identity: dict[str, object]
    no_mutation: dict[str, object]
    claim_boundary: tuple[str, ...]
    issues: dict[str, object]
    reproducibility: dict[str, object]

    def __post_init__(self) -> None:
        if tuple(name for name, _ in self.freeze_commits) != _PHASES:
            raise ValueError("freeze commits must contain P7.1-P7.7 in order")
        for phase, commit in self.freeze_commits:
            if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError(f"{phase} freeze commit is invalid")
        _require_keys(self.protected_p6, {"freeze_commit", "canonical_sha256"}, "protected_p6")
        if not re.fullmatch(r"[0-9a-f]{40}", str(self.protected_p6["freeze_commit"])):
            raise ValueError("protected P6 freeze commit is invalid")
        _sha(self.protected_p6["canonical_sha256"], "protected_p6.canonical_sha256")
        seen: set[str] = set()
        phases_seen: set[str] = set()
        for row in self.frozen_artifacts:
            _require_keys(row, {"phase", "relative_path", "sha256"}, "frozen artifact")
            if row["phase"] not in _PHASES:
                raise ValueError("frozen artifact phase is invalid")
            phases_seen.add(str(row["phase"]))
            path = normalize_relative_path(row["relative_path"])
            if path in seen:
                raise ValueError("duplicate frozen artifact path")
            seen.add(path)
            _sha(row["sha256"], f"frozen_artifacts[{path}].sha256")
        if phases_seen != set(_PHASES):
            raise ValueError("frozen artifact inventory must cover P7.1-P7.7")
        _require_keys(self.schema_mirrors, {"status", "paths", "sha256"}, "schema_mirrors")
        if self.schema_mirrors["status"] != "byte_identical" or not isinstance(self.schema_mirrors["paths"], list):
            raise ValueError("schema mirrors must be byte-identical")
        if len(self.schema_mirrors["paths"]) != 2:
            raise ValueError("schema mirrors require two paths")
        for path in self.schema_mirrors["paths"]:
            normalize_relative_path(path, "schema_mirrors.path")
        _sha(self.schema_mirrors["sha256"], "schema_mirrors.sha256")
        _require_keys(
            self.regression,
            {"focused_command", "focused_exit_code", "focused_passed", "focused_failed", "full_command", "full_exit_code", "full_passed", "full_failed"},
            "regression",
        )
        for key in ("focused_exit_code", "full_exit_code", "focused_passed", "focused_failed", "full_passed", "full_failed"):
            if isinstance(self.regression[key], bool) or not isinstance(self.regression[key], int) or self.regression[key] < 0:
                raise ValueError(f"regression.{key} must be non-negative integer")
        for key in ("focused_command", "full_command"):
            _stable_text(self.regression[key], f"regression.{key}")
        _require_keys(self.identity, {"source_identity", "package_identity", "raw_trace_identity", "benchmark_evidence_identity", "baseline_capability_identity"}, "identity")
        for key, value in self.identity.items():
            if value is not None:
                _sha(value, f"identity.{key}")
        _require_keys(self.no_mutation, {"status", "checked_paths"}, "no_mutation")
        if self.no_mutation["status"] != "pass" or not isinstance(self.no_mutation["checked_paths"], list):
            raise ValueError("no_mutation must be pass with checked paths")
        for path in self.no_mutation["checked_paths"]:
            normalize_relative_path(path, "no_mutation.checked_path")
        if not self.claim_boundary or any(not isinstance(item, str) or not item for item in self.claim_boundary):
            raise ValueError("claim boundary must be non-empty text")
        _require_keys(self.issues, {"open", "accepted", "governance_deviations"}, "issues")
        for key in self.issues:
            if not isinstance(self.issues[key], list) or any(not isinstance(item, str) or not item for item in self.issues[key]):
                raise ValueError(f"issues.{key} must be a list of text")
        _require_keys(self.reproducibility, {"commands", "clean_checkout_required"}, "reproducibility")
        if not isinstance(self.reproducibility["commands"], list) or not self.reproducibility["commands"] or any(not isinstance(item, str) or not item for item in self.reproducibility["commands"]):
            raise ValueError("reproducibility.commands must be non-empty")
        if self.reproducibility["clean_checkout_required"] is not True:
            raise ValueError("clean checkout reproducibility is required")
        _walk_for_leaks(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "phase7_closeout_version": VERSION,
            "contract_version": CONTRACT_VERSION,
            "freeze_commits": dict(self.freeze_commits),
            "protected_p6": dict(self.protected_p6),
            "frozen_artifacts": [dict(row) for row in self.frozen_artifacts],
            "schema_mirrors": dict(self.schema_mirrors),
            "regression": dict(self.regression),
            "identity": dict(self.identity),
            "no_mutation": dict(self.no_mutation),
            "claim_boundary": list(self.claim_boundary),
            "issues": {key: list(value) for key, value in self.issues.items()},
            "reproducibility": {"commands": list(self.reproducibility["commands"]), "clean_checkout_required": True},
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.to_dict())

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> "Phase7CloseoutManifest":
        if not isinstance(value, Mapping):
            raise ValueError("phase7 closeout manifest must be an object")
        if set(value) != _TOP_LEVEL or value.get("phase7_closeout_version") != VERSION or value.get("contract_version") != CONTRACT_VERSION:
            raise ValueError("phase7 closeout manifest fields are invalid")
        commits = value["freeze_commits"]
        artifacts = value["frozen_artifacts"]
        if not isinstance(commits, Mapping) or not isinstance(artifacts, list):
            raise ValueError("freeze commits or artifacts are invalid")
        if set(commits) != set(_PHASES):
            raise ValueError("freeze commits must contain P7.1-P7.7")
        if any(not isinstance(row, Mapping) for row in artifacts):
            raise ValueError("frozen artifact rows are invalid")
        return cls(
            tuple((phase, str(commits[phase])) for phase in _PHASES),
            dict(value["protected_p6"]) if isinstance(value["protected_p6"], Mapping) else {},
            tuple(dict(row) for row in artifacts),
            dict(value["schema_mirrors"]) if isinstance(value["schema_mirrors"], Mapping) else {},
            dict(value["regression"]) if isinstance(value["regression"], Mapping) else {},
            dict(value["identity"]) if isinstance(value["identity"], Mapping) else {},
            dict(value["no_mutation"]) if isinstance(value["no_mutation"], Mapping) else {},
            tuple(value["claim_boundary"]) if isinstance(value["claim_boundary"], list) else (),
            dict(value["issues"]) if isinstance(value["issues"], Mapping) else {},
            dict(value["reproducibility"]) if isinstance(value["reproducibility"], Mapping) else {},
        )

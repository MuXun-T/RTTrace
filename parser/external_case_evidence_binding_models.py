"""Closed P7.2-to-P7.3-to-P7.4 case evidence binding record."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Mapping


BINDING_VERSION = "p7.3-p7.4-case-evidence-binding-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TEMP_COMPONENTS = frozenset({"tmp", "temp", "temporary"})


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256_identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"{name} must be a non-empty identifier")
    return value


def _logical_path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or value.startswith("//") or "\\" in value or "\x00" in value or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{name} must be a normalized logical path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.lower() in _TEMP_COMPONENTS for part in parts):
        raise ValueError(f"{name} must be a stable logical path")
    return value


def _bytes(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class ExternalCaseEvidenceBinding:
    binding_version: str
    binding_identity: str
    case_id: str
    package_id: str
    p7_2_inventory_identity: str
    p7_2_source_id: str
    p7_2_artifact_id: str
    p7_2_raw_trace_logical_path: str
    p7_2_raw_trace_bytes: int
    p7_2_raw_trace_sha256: str
    p7_3_manifest_logical_path: str
    p7_3_manifest_sha256: str
    p7_3_source_identity: str
    p7_3_artifact_identity: str
    p7_3_package_identity: str
    p7_3_report_logical_path: str
    p7_3_report_sha256: str
    p7_4_replay_report_logical_path: str
    p7_4_replay_report_sha256: str
    p7_4_source_identity: str
    p7_4_reported_package_identity: str
    p7_4_trace_identity: str
    hardware_validation: bool

    def __post_init__(self) -> None:
        if self.binding_version != BINDING_VERSION or self.hardware_validation is not False:
            raise ValueError("binding constants are invalid")
        for name in ("case_id", "package_id", "p7_2_source_id", "p7_2_artifact_id"):
            _text(getattr(self, name), name)
        for name in ("p7_2_raw_trace_logical_path", "p7_3_manifest_logical_path", "p7_3_report_logical_path", "p7_4_replay_report_logical_path"):
            _logical_path(getattr(self, name), name)
        _bytes(self.p7_2_raw_trace_bytes, "p7_2_raw_trace_bytes")
        for name in (
            "binding_identity", "p7_2_inventory_identity", "p7_2_raw_trace_sha256",
            "p7_3_manifest_sha256", "p7_3_source_identity", "p7_3_artifact_identity",
            "p7_3_package_identity", "p7_3_report_sha256", "p7_4_replay_report_sha256",
            "p7_4_source_identity", "p7_4_reported_package_identity", "p7_4_trace_identity",
        ):
            _sha256(getattr(self, name), name)
        if self.binding_identity != sha256_identity(self.identity_input()):
            raise ValueError("binding_identity does not match the canonical record")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def identity_input(self) -> dict[str, object]:
        value = self.to_dict()
        del value["binding_identity"]
        return value

    @classmethod
    def from_dict(cls, value: object) -> "ExternalCaseEvidenceBinding":
        fields = frozenset(cls.__dataclass_fields__)
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("binding fields are invalid")
        return cls(**{name: value[name] for name in fields})  # type: ignore[arg-type]

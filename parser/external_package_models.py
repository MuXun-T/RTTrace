"""Strict immutable P7.3 package-open models; no replay semantics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Mapping


CONTRACT_NAME = "rttrace_external_evidence_package"
CONTRACT_VERSION = "p7.3-package-open-v1"
MANIFEST_VERSION = "external-evidence-package-v1"
PACKAGE_OPEN_PROFILE_ID = "p7.3-package-open-only-v1"
REPORT_VERSION = "external-package-open-report-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_FIELDS = frozenset({"api_key", "apikey", "access_token", "token", "password", "passwd", "secret", "private_key", "shell_command", "command", "tool_invocation", "writable_proof_path", "raw_human_feedback", "demographics", "absolute_local_path", "llm_correctness", "advisor_correctness", "replay_pass", "replay_fail", "all_replay_passed", "diagnosis_accuracy", "root_cause_accuracy", "proof_correctness"})


class PackageKind(str, Enum):
    SELF_CONTAINED = "self_contained"
    METADATA_ONLY = "metadata_only"
    REFERENCED_EXTERNAL = "referenced_external"
    HYBRID = "hybrid"


class PackageOpenResult(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    OPENED = "opened"
    OPENED_REFERENCE_ONLY = "opened_reference_only"
    BLOCKED = "blocked"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"


class PackageOpenReason(str, Enum):
    MANIFEST_MISSING = "ERR_PACKAGE_MANIFEST_MISSING"; MANIFEST_UNREADABLE = "ERR_PACKAGE_MANIFEST_UNREADABLE"; MANIFEST_INVALID = "ERR_PACKAGE_MANIFEST_INVALID"; CONTRACT_VERSION_UNSUPPORTED = "ERR_PACKAGE_CONTRACT_VERSION_UNSUPPORTED"; KIND_UNSUPPORTED = "ERR_PACKAGE_KIND_UNSUPPORTED"
    ABSOLUTE_PATH = "ERR_PACKAGE_ABSOLUTE_PATH"; PATH_TRAVERSAL = "ERR_PACKAGE_PATH_TRAVERSAL"; SYMLINK_FORBIDDEN = "ERR_PACKAGE_SYMLINK_FORBIDDEN"; PATH_DUPLICATE = "ERR_PACKAGE_PATH_DUPLICATE"; PATH_NORMALIZATION = "ERR_PACKAGE_PATH_NORMALIZATION"
    REQUIRED_ARTIFACT_MISSING = "ERR_PACKAGE_REQUIRED_ARTIFACT_MISSING"; ARTIFACT_SIZE_MISMATCH = "ERR_PACKAGE_ARTIFACT_SIZE_MISMATCH"; ARTIFACT_CHECKSUM_MISMATCH = "ERR_PACKAGE_ARTIFACT_CHECKSUM_MISMATCH"; ARTIFACT_ID_DUPLICATE = "ERR_PACKAGE_ARTIFACT_ID_DUPLICATE"; ARTIFACT_KIND_UNSUPPORTED = "ERR_PACKAGE_ARTIFACT_KIND_UNSUPPORTED"; UNDECLARED_ARTIFACT = "ERR_PACKAGE_UNDECLARED_ARTIFACT"; ZERO_BYTE_REQUIRED_ARTIFACT = "ERR_PACKAGE_ZERO_BYTE_REQUIRED_ARTIFACT"
    IDENTITY_MISMATCH = "ERR_PACKAGE_IDENTITY_MISMATCH"; SOURCE_IDENTITY_MISMATCH = "ERR_SOURCE_IDENTITY_MISMATCH"; CONFIGURATION_IDENTITY_MISMATCH = "ERR_CONFIGURATION_IDENTITY_MISMATCH"; COMPARISON_PROFILE_IDENTITY_MISSING = "ERR_COMPARISON_PROFILE_IDENTITY_MISSING"
    REF_UNAVAILABLE = "ERR_EXTERNAL_REFERENCE_UNAVAILABLE"; REF_IDENTITY_MISSING = "ERR_EXTERNAL_REFERENCE_IDENTITY_MISSING"; REF_CHECKSUM_MISSING = "ERR_EXTERNAL_REFERENCE_CHECKSUM_MISSING"; REF_LICENSE_BLOCKED = "ERR_EXTERNAL_REFERENCE_LICENSE_BLOCKED"
    FORBIDDEN_FIELD = "ERR_PACKAGE_FORBIDDEN_FIELD"; SECRET_DETECTED = "ERR_PACKAGE_SECRET_DETECTED"; PII_DETECTED = "ERR_PACKAGE_PII_DETECTED"; ARCHIVE_UNSUPPORTED = "ERR_PACKAGE_ARCHIVE_UNSUPPORTED"; SIZE_LIMIT = "ERR_PACKAGE_SIZE_LIMIT"; ARTIFACT_COUNT_LIMIT = "ERR_PACKAGE_ARTIFACT_COUNT_LIMIT"; MANIFEST_SIZE_LIMIT = "ERR_PACKAGE_MANIFEST_SIZE_LIMIT"
    CHECKSUM_ALGORITHM_UNSUPPORTED = "ERR_PACKAGE_CHECKSUM_ALGORITHM_UNSUPPORTED"; ARTIFACT_IDENTITY_MISMATCH = "ERR_PACKAGE_ARTIFACT_IDENTITY_MISMATCH"; COMPARISON_PROFILE_IDENTITY_MISMATCH = "ERR_COMPARISON_PROFILE_IDENTITY_MISMATCH"; PACKAGE_SOURCE_MUTATION = "ERR_PACKAGE_SOURCE_MUTATION"; PACKAGE_MUTATION = "ERR_PACKAGE_MUTATION"; EXTERNAL_REFERENCE_ID_DUPLICATE = "ERR_EXTERNAL_REFERENCE_ID_DUPLICATE"; EXTERNAL_REFERENCE_IDENTITY_MISMATCH = "ERR_EXTERNAL_REFERENCE_IDENTITY_MISMATCH"; SPECIAL_FILE = "ERR_PACKAGE_SPECIAL_FILE"; HARDLINK_FORBIDDEN = "ERR_PACKAGE_HARDLINK_FORBIDDEN"


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _mapping(value: object, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _sha256(value: object, label: str) -> str:
    value = _string(value, label)
    if not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _relative_path(value: object, label: str) -> str:
    value = _string(value, label)
    if len(value) > 512 or "\\" in value or "\x00" in value or value.startswith("/") or value.startswith("//") or re.match(r"^[A-Za-z]:", value) or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"{label} must be a normalized relative POSIX path")
    return value


class ForbiddenFieldError(ValueError):
    def __init__(self, reason: PackageOpenReason) -> None:
        super().__init__(reason.value); self.reason = reason


def classify_forbidden_key(key: object) -> PackageOpenReason | None:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    forbidden = {name.replace("_", "") for name in FORBIDDEN_FIELDS}
    if "secret" in normalized or "token" in normalized or "password" in normalized or "privatekey" in normalized: return PackageOpenReason.SECRET_DETECTED
    if "pii" in normalized or "demographic" in normalized: return PackageOpenReason.PII_DETECTED
    if normalized in forbidden: return PackageOpenReason.FORBIDDEN_FIELD
    return None


def _forbidden(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            reason = classify_forbidden_key(key)
            if reason is not None: raise ForbiddenFieldError(reason)
            _forbidden(child)
    elif isinstance(value, list) or isinstance(value, tuple):
        for child in value:
            _forbidden(child)


@dataclass(frozen=True)
class ExternalSourceIdentity:
    source_id: str; source_kind: str; repository: str; repository_commit: str; source_path: str; source_artifact_id: str | None; source_checksum: str | None; source_bytes: int | None; rtos_name: str; trace_format: str; source_format_version: str; generation_mode: str; hardware_validation: bool; license_spdx: str; acquisition_status: str; source_identity: str
    def __post_init__(self) -> None:
        if self.hardware_validation is not False or self.acquisition_status not in {"acquired", "acquisition_blocked", "external_reference_only"} or not all(isinstance(getattr(self, name), str) and getattr(self, name) for name in ("source_id","source_kind","repository","repository_commit","source_path","rtos_name","trace_format","source_format_version","generation_mode","license_spdx")) or not COMMIT_RE.fullmatch(self.repository_commit): raise ValueError("source types invalid")
        _relative_path(self.source_path,"source_path"); _sha256(self.source_identity,"source_identity")
        if (self.source_artifact_id is None) != (self.source_checksum is None) or (self.source_checksum is None) != (self.source_bytes is None): raise ValueError("source local-artifact fields invalid")
        if self.source_artifact_id is not None: _string(self.source_artifact_id,"source_artifact_id"); _sha256(self.source_checksum,"source_checksum"); _count(self.source_bytes,"source_bytes")
    @classmethod
    def from_dict(cls, value: object) -> "ExternalSourceIdentity":
        fields = frozenset(cls.__dataclass_fields__)
        row = _mapping(value, fields, "source")
        if row["hardware_validation"] is not False or (row["source_artifact_id"] is None) != (row["source_checksum"] is None) or (row["source_checksum"] is None) != (row["source_bytes"] is None):
            raise ValueError("source local-artifact fields are invalid")
        if row["source_bytes"] is not None:
            _count(row["source_bytes"], "source_bytes")
        commit = _string(row["repository_commit"], "repository_commit")
        if not COMMIT_RE.fullmatch(commit): raise ValueError("repository_commit is invalid")
        values = {name: row[name] for name in fields}
        for name in ("source_id", "source_kind", "repository", "rtos_name", "trace_format", "source_format_version", "generation_mode", "license_spdx", "acquisition_status", "source_identity"):
            values[name] = _sha256(row[name], name) if name == "source_identity" else _string(row[name], name)
        values["repository_commit"] = commit; values["source_path"] = _relative_path(row["source_path"], "source_path")
        if row["source_artifact_id"] is not None: values["source_artifact_id"] = _string(row["source_artifact_id"], "source_artifact_id"); values["source_checksum"] = _sha256(row["source_checksum"], "source_checksum")
        return cls(**values)  # type: ignore[arg-type]
    def to_dict(self) -> dict[str, object]: return asdict(self)


@dataclass(frozen=True)
class ExternalArtifactRecord:
    artifact_id: str; artifact_kind: str; relative_path: str; required: bool; bytes: int; sha256: str; media_type: str; source_artifact_id: str; content_version: str; provenance_reference: str; artifact_identity: str
    def __post_init__(self) -> None:
        if not isinstance(self.required,bool): raise ValueError("artifact required invalid")
        _count(self.bytes,"artifact.bytes"); _relative_path(self.relative_path,"relative_path"); _sha256(self.sha256,"sha256"); _sha256(self.artifact_identity,"artifact_identity")
        for name in ("artifact_id","artifact_kind","media_type","source_artifact_id","content_version","provenance_reference"): _string(getattr(self,name),name)
    @classmethod
    def from_dict(cls, value: object) -> "ExternalArtifactRecord":
        row = _mapping(value, frozenset(cls.__dataclass_fields__), "artifact")
        if not isinstance(row["required"], bool): raise ValueError("artifact.required is invalid")
        values = {name: row[name] for name in cls.__dataclass_fields__}
        values["bytes"] = _count(row["bytes"], "artifact.bytes"); values["sha256"] = _sha256(row["sha256"], "artifact.sha256"); values["artifact_identity"] = _sha256(row["artifact_identity"], "artifact_identity"); values["relative_path"] = _relative_path(row["relative_path"], "relative_path")
        for name in ("artifact_id", "artifact_kind", "media_type", "source_artifact_id", "content_version", "provenance_reference"): values[name] = _string(row[name], name)
        return cls(**values)  # type: ignore[arg-type]
    def to_dict(self) -> dict[str, object]: return asdict(self)


@dataclass(frozen=True)
class ExternalReferenceRecord:
    reference_id: str; reference_kind: str; repository: str; repository_commit: str; source_path: str; expected_sha256: str; license_spdx: str; availability: str; required: bool; source_id: str; reference_identity: str
    def __post_init__(self) -> None:
        if not isinstance(self.required,bool) or self.availability not in {"declared","unavailable","license_blocked"} or self.reference_kind not in {"provenance_license","trace_content"} or not COMMIT_RE.fullmatch(self.repository_commit): raise ValueError("reference types invalid")
        _relative_path(self.source_path,"source_path"); _sha256(self.expected_sha256,"expected_sha256"); _sha256(self.reference_identity,"reference_identity")
        for name in ("reference_id","reference_kind","repository","repository_commit","license_spdx","source_id"): _string(getattr(self,name),name)
    @classmethod
    def from_dict(cls, value: object) -> "ExternalReferenceRecord":
        row = _mapping(value, frozenset(cls.__dataclass_fields__), "external_reference")
        if not isinstance(row["required"], bool) or row["availability"] not in {"declared", "unavailable", "license_blocked"}: raise ValueError("external reference fields are invalid")
        commit = _string(row["repository_commit"], "repository_commit")
        if not COMMIT_RE.fullmatch(commit): raise ValueError("repository_commit is invalid")
        values = {name: row[name] for name in cls.__dataclass_fields__}
        for name in ("reference_id", "reference_kind", "repository", "license_spdx", "source_id"): values[name] = _string(row[name], name)
        values["repository_commit"] = commit; values["source_path"] = _relative_path(row["source_path"], "source_path"); values["expected_sha256"] = _sha256(row["expected_sha256"], "expected_sha256"); values["reference_identity"] = _sha256(row["reference_identity"], "reference_identity")
        return cls(**values)  # type: ignore[arg-type]
    def to_dict(self) -> dict[str, object]: return asdict(self)


@dataclass(frozen=True)
class PackageOpenRequest:
    package_root: str
    expected_contract_version: str = CONTRACT_VERSION
    offline: bool = True
    def __post_init__(self) -> None:
        if not isinstance(self.package_root, str) or not self.package_root or not isinstance(self.expected_contract_version,str) or not self.expected_contract_version or self.offline is not True: raise ValueError("package open requests must be offline with a package root")


@dataclass(frozen=True)
class ExternalEvidencePackageManifest:
    contract_name: str; contract_version: str; manifest_version: str; package_kind: PackageKind; package_identity: str; source: ExternalSourceIdentity; artifacts: tuple[ExternalArtifactRecord, ...]; external_references: tuple[ExternalReferenceRecord, ...]; configuration_identity: str; package_open_profile_id: str; checksum_algorithm: str; hardware_validation: bool; limitations: tuple[str, ...]
    def __post_init__(self) -> None:
        if self.contract_name != CONTRACT_NAME or not isinstance(self.contract_version,str) or not self.contract_version or self.manifest_version != MANIFEST_VERSION or not isinstance(self.package_kind,PackageKind) or self.hardware_validation is not False or not isinstance(self.source,ExternalSourceIdentity) or not isinstance(self.artifacts,tuple) or not isinstance(self.external_references,tuple) or not all(isinstance(item,ExternalArtifactRecord) for item in self.artifacts) or not all(isinstance(item,ExternalReferenceRecord) for item in self.external_references) or not isinstance(self.limitations,tuple) or any(not isinstance(item,str) for item in self.limitations): raise ValueError("manifest types invalid")
        _sha256(self.package_identity,"package_identity"); _sha256(self.configuration_identity,"configuration_identity")
        if self.package_open_profile_id != PACKAGE_OPEN_PROFILE_ID or not isinstance(self.checksum_algorithm,str) or not re.fullmatch(r"[a-z0-9_-]+",self.checksum_algorithm) or any(not isinstance(item,str) for item in self.limitations): raise ValueError("manifest constants invalid")
        if len({item.artifact_id for item in self.artifacts}) != len(self.artifacts) or len({item.relative_path for item in self.artifacts}) != len(self.artifacts) or len({item.reference_id for item in self.external_references}) != len(self.external_references): raise ValueError("manifest duplicate inventory")
        if self.package_kind is PackageKind.SELF_CONTAINED and (not self.artifacts or not any(item.required for item in self.artifacts) or self.external_references or self.source.source_artifact_id is None): raise ValueError("self-contained inventory invalid")
        if self.package_kind in {PackageKind.METADATA_ONLY,PackageKind.REFERENCED_EXTERNAL} and (self.artifacts or not self.external_references or self.source.source_artifact_id is not None): raise ValueError("reference-only inventory invalid")
        object.__setattr__(self,"artifacts",tuple(sorted(self.artifacts,key=lambda item:item.artifact_id)))
        object.__setattr__(self,"external_references",tuple(sorted(self.external_references,key=lambda item:item.reference_id)))
        object.__setattr__(self,"limitations",tuple(sorted(set(self.limitations))))
    @classmethod
    def from_dict(cls, value: object) -> "ExternalEvidencePackageManifest":
        _forbidden(value)
        fields = frozenset({"contract_name","contract_version","manifest_version","package_kind","package_identity","source","artifacts","external_references","configuration","configuration_identity","package_open_profile_id","checksum_algorithm","hardware_validation","limitations"})
        row = _mapping(value, fields, "manifest")
        if row["contract_name"] != CONTRACT_NAME or row["manifest_version"] != MANIFEST_VERSION or row["configuration"] != {"validation_mode":"offline","package_profile":"p7.3-directory-v1"} or row["hardware_validation"] is not False or row["package_open_profile_id"] != PACKAGE_OPEN_PROFILE_ID or not isinstance(row["checksum_algorithm"], str) or not re.fullmatch(r"[a-z0-9_-]+", row["checksum_algorithm"]): raise ValueError("manifest constants are invalid")
        if not isinstance(row["artifacts"], list) or not isinstance(row["external_references"], list) or not isinstance(row["limitations"], list): raise ValueError("manifest arrays are invalid")
        artifacts = tuple(ExternalArtifactRecord.from_dict(item) for item in row["artifacts"]); references = tuple(ExternalReferenceRecord.from_dict(item) for item in row["external_references"])
        if len({item.artifact_id for item in artifacts}) != len(artifacts) or len({item.relative_path for item in artifacts}) != len(artifacts) or len({item.reference_id for item in references}) != len(references) or not all(isinstance(item, str) for item in row["limitations"]): raise ValueError("manifest inventory is invalid")
        return cls(CONTRACT_NAME, _string(row["contract_version"], "contract_version"), MANIFEST_VERSION, PackageKind(_string(row["package_kind"], "package_kind")), _sha256(row["package_identity"], "package_identity"), ExternalSourceIdentity.from_dict(row["source"]), tuple(sorted(artifacts, key=lambda item: item.artifact_id)), tuple(sorted(references, key=lambda item: item.reference_id)), _sha256(row["configuration_identity"], "configuration_identity"), PACKAGE_OPEN_PROFILE_ID, row["checksum_algorithm"], False, tuple(sorted(set(row["limitations"]))))
    def to_dict(self) -> dict[str, object]:
        return {"contract_name":self.contract_name,"contract_version":self.contract_version,"manifest_version":self.manifest_version,"package_kind":self.package_kind.value,"package_identity":self.package_identity,"source":self.source.to_dict(),"artifacts":[item.to_dict() for item in self.artifacts],"external_references":[item.to_dict() for item in self.external_references],"configuration":{"validation_mode":"offline","package_profile":"p7.3-directory-v1"},"configuration_identity":self.configuration_identity,"package_open_profile_id":PACKAGE_OPEN_PROFILE_ID,"checksum_algorithm":self.checksum_algorithm,"hardware_validation":False,"limitations":list(self.limitations)}
    def identity_input(self) -> dict[str, object]:
        data = self.to_dict(); del data["package_identity"]; data["artifacts"] = sorted(data["artifacts"], key=lambda item: str(item["artifact_id"])); data["external_references"] = sorted(data["external_references"], key=lambda item: str(item["reference_id"])); return {"manifest_name":"package_manifest.json","manifest":data}


REPORT_FIELDS = ("report_version","contract_version","package_identity","package_kind","open_result","reason_codes","manifest_valid","path_safety_valid","required_artifacts_total","required_artifacts_present","optional_artifacts_total","artifact_checksum_pass_count","artifact_checksum_fail_count","artifact_size_pass_count","artifact_size_fail_count","external_reference_count","external_reference_checked_count","package_complete","source_mutation_count","package_mutation_count","forbidden_field_count","absolute_path_count","path_escape_count","hardware_validation","replay_evaluated","limitations")

@dataclass(frozen=True)
class PackageOpenReport:
    package_identity: str | None; package_kind: PackageKind | None; open_result: PackageOpenResult; reason_codes: tuple[PackageOpenReason, ...]; manifest_valid: bool; path_safety_valid: bool; required_artifacts_total: int; required_artifacts_present: int; optional_artifacts_total: int; artifact_checksum_pass_count: int; artifact_checksum_fail_count: int; artifact_size_pass_count: int; artifact_size_fail_count: int; external_reference_count: int; external_reference_checked_count: int; package_complete: bool; source_mutation_count: int; package_mutation_count: int; forbidden_field_count: int; absolute_path_count: int; path_escape_count: int; limitations: tuple[str, ...]; replay_evaluated: bool = False; hardware_validation: bool = False
    def __post_init__(self) -> None:
        if self.replay_evaluated is not False or self.hardware_validation is not False or self.package_kind is not None and not isinstance(self.package_kind, PackageKind) or not isinstance(self.open_result, PackageOpenResult) or self.package_identity is not None and (not isinstance(self.package_identity,str) or not SHA256_RE.fullmatch(self.package_identity)) or not isinstance(self.reason_codes,tuple) or not isinstance(self.limitations,tuple): raise ValueError("report identity/types invalid")
        if any(not isinstance(value, bool) for value in (self.manifest_valid,self.path_safety_valid,self.package_complete)) or any(not isinstance(reason, PackageOpenReason) for reason in self.reason_codes) or any(not isinstance(item,str) for item in self.limitations): raise ValueError("report booleans/collections invalid")
        for name in ("required_artifacts_total","required_artifacts_present","optional_artifacts_total","artifact_checksum_pass_count","artifact_checksum_fail_count","artifact_size_pass_count","artifact_size_fail_count","external_reference_count","external_reference_checked_count","source_mutation_count","package_mutation_count","forbidden_field_count","absolute_path_count","path_escape_count"):
            _count(getattr(self,name),name)
        successful = self.open_result in {PackageOpenResult.OPENED,PackageOpenResult.OPENED_REFERENCE_ONLY}
        if successful and (self.reason_codes or self.package_identity is None or self.package_kind is None or not self.manifest_valid or not self.path_safety_valid or not self.package_complete or self.source_mutation_count or self.package_mutation_count or self.forbidden_field_count or self.absolute_path_count or self.path_escape_count or self.artifact_checksum_fail_count or self.artifact_size_fail_count or self.required_artifacts_present != self.required_artifacts_total): raise ValueError("successful report integrity invalid")
        if self.open_result is PackageOpenResult.OPENED and self.package_kind is not PackageKind.SELF_CONTAINED: raise ValueError("opened report kind invalid")
        if self.open_result is PackageOpenResult.OPENED_REFERENCE_ONLY and self.package_kind not in {PackageKind.METADATA_ONLY,PackageKind.REFERENCED_EXTERNAL}: raise ValueError("reference-only report kind invalid")
        object.__setattr__(self,"reason_codes",tuple(sorted(set(self.reason_codes),key=lambda reason:reason.value)))
        object.__setattr__(self,"limitations",tuple(sorted(set(self.limitations))))
    def to_dict(self) -> dict[str, object]:
        data = asdict(self); data.update({"report_version":REPORT_VERSION,"contract_version":CONTRACT_VERSION,"package_kind":None if self.package_kind is None else self.package_kind.value,"open_result":self.open_result.value,"reason_codes":sorted({reason.value for reason in self.reason_codes}),"limitations":sorted(set(self.limitations)),"replay_evaluated":False,"hardware_validation":False}); return {name:data[name] for name in REPORT_FIELDS}
    @classmethod
    def from_dict(cls, value: object) -> "PackageOpenReport":
        row = _mapping(value, frozenset(REPORT_FIELDS), "report")
        if row["report_version"] != REPORT_VERSION or row["contract_version"] != CONTRACT_VERSION or row["replay_evaluated"] is not False or row["hardware_validation"] is not False or not isinstance(row["reason_codes"], list) or not isinstance(row["limitations"], list): raise ValueError("report constants invalid")
        reasons = tuple(PackageOpenReason(_string(item,"reason")) for item in row["reason_codes"])
        if len(set(reasons)) != len(reasons) or not all(isinstance(item,str) for item in row["limitations"]): raise ValueError("report collections invalid")
        identity = None if row["package_identity"] is None else _sha256(row["package_identity"],"package_identity")
        kind = None if row["package_kind"] is None else PackageKind(_string(row["package_kind"],"package_kind"))
        return cls(identity,kind,PackageOpenResult(_string(row["open_result"],"open_result")),reasons,*[row[name] for name in REPORT_FIELDS[6:23]],tuple(sorted(set(row["limitations"]))))  # type: ignore[arg-type]

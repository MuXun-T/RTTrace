"""Objective Phase 3 evidence-lineage primitives.

This module records only raw-event provenance, authoritative boundaries,
capture integrity bindings, and objective untrusted-window intersections.  It
does not import or evaluate fault, relevance, diagnosis, verdict, Agent, or
package/proof state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
import re
from typing import Any

from spec.rtd_pilot_contracts import (
    SCHEMA_VERSION,
    ContractError,
    validate_ccm,
    validate_cir,
)


LINEAGE_SCHEMA_VERSION = "rtd-lineage-v1"
FULL_CAPTURE_REGISTRY_SCOPE = "full_capture"
DERIVED_OBJECT_SUBSET_REGISTRY_SCOPE = "derived_object_subset"
CAPABILITY_UNSUPPORTED_REASON = "CAPABILITY_UNSUPPORTED"
MAPPING_INVALID_REASON = "MAPPING_INVALID"
ALIGNMENT_DEGRADED_REASON = "ALIGNMENT_DEGRADED"
MISSING_CAPTURE_LINEAGE_CONTEXT_REASON = "MISSING_CAPTURE_LINEAGE_CONTEXT"
MISSING_CCM_REF_REASON = "MISSING_CCM_REF"
MISSING_CIR_REF_REASON = "MISSING_CIR_REF"
NON_NORMAL_CLOSE_BOUNDARY_REASON = "NON_NORMAL_CLOSE_BOUNDARY"
SOURCE_INTEGRITY_AFFECTED_REASON = "SOURCE_INTEGRITY_AFFECTED"

_CAPTURE_ID = re.compile(r"^capture:[A-Za-z0-9][A-Za-z0-9._-]*$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAPPING_WINDOW_REASONS = frozenset(
    {
        "DICT_MISMATCH",
        "MAPPING_MISMATCH",
        "SEGMENT_DICT_CONFLICT",
        "UNSUPPORTED_MAPPING",
    }
)
_ALIGNMENT_WINDOW_REASONS = frozenset({"ALIGN_DEGRADED"})


class LineageValidationError(ValueError):
    """A lineage record cannot be objectively traced to registered evidence."""


class LineageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE_OPEN = "INCOMPLETE_OPEN"
    INCOMPLETE_CLOSE = "INCOMPLETE_CLOSE"
    LOSS_AFFECTED = "LOSS_AFFECTED"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    MAPPING_INVALID = "MAPPING_INVALID"
    ALIGNMENT_DEGRADED = "ALIGNMENT_DEGRADED"


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise LineageValidationError(f"{label} must be an ASCII identifier")


def _require_digest(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise LineageValidationError(f"{label} must be a lowercase SHA-256 digest")


def _require_time(value: float | int, label: str) -> float:
    if isinstance(value, bool):
        raise LineageValidationError(f"{label} must be a finite timestamp")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise LineageValidationError(f"{label} must be a finite timestamp") from exc
    if not isfinite(normalized):
        raise LineageValidationError(f"{label} must be a finite timestamp")
    return normalized


def _require_unique_identifiers(values: Iterable[str], label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    normalized = tuple(values)
    if not normalized and not allow_empty:
        raise LineageValidationError(f"{label} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise LineageValidationError(f"{label} contains duplicate identifiers")
    for value in normalized:
        _require_identifier(value, label)
    return normalized


@dataclass(frozen=True, slots=True)
class CaptureLineageContext:
    """The immutable P2 CCM/CIR binding shared by one capture's lineage."""

    capture_id: str
    session_id: str
    capture_capability_manifest_ref: str
    capture_capability_manifest_digest: str
    capture_capability_manifest_schema_version: str
    capture_integrity_record_ref: str
    capture_integrity_record_digest: str
    capture_integrity_record_schema_version: str
    event_types_enabled: tuple[str, ...] = ()
    sampling_boundary_complete: bool | None = None
    mapping_valid: bool | None = None

    @classmethod
    def from_p2_records(
        cls,
        capability_manifest: Mapping[str, Any],
        integrity_record: Mapping[str, Any],
    ) -> "CaptureLineageContext":
        """Build a context only after P2 validators and cross-record checks pass."""

        ccm = dict(capability_manifest)
        cir = dict(integrity_record)
        try:
            validate_ccm(ccm)
            validate_cir(cir)
        except (ContractError, KeyError, TypeError) as exc:
            raise LineageValidationError(f"invalid P2 capture binding: {exc}") from exc

        if ccm["capture_id"] != cir["capture_id"]:
            raise LineageValidationError("CCM and CIR capture_id mismatch")
        if ccm["session_id"] != cir["session_id"]:
            raise LineageValidationError("CCM and CIR session_id mismatch")
        if cir["capability_manifest_id"] != ccm["capability_manifest_id"]:
            raise LineageValidationError("CIR capability_manifest_id does not reference CCM")
        if cir["capability_manifest_digest"] != ccm["record_digest"]:
            raise LineageValidationError("CIR capability_manifest_digest does not match CCM")

        context = cls(
            capture_id=str(ccm["capture_id"]),
            session_id=str(ccm["session_id"]),
            capture_capability_manifest_ref=str(ccm["capability_manifest_id"]),
            capture_capability_manifest_digest=str(ccm["record_digest"]),
            capture_capability_manifest_schema_version=str(ccm["schema_version"]),
            capture_integrity_record_ref=str(cir["cir_record_id"]),
            capture_integrity_record_digest=str(cir["record_digest"]),
            capture_integrity_record_schema_version=str(cir["schema_version"]),
            event_types_enabled=tuple(sorted(str(name) for name in ccm["event_types_enabled"])),
            sampling_boundary_complete=not bool((ccm.get("sampling_configuration") or {}).get("enabled", False)),
            mapping_valid=not bool(cir["mapping_mismatch"]),
        )
        validate_capture_lineage_context(context)
        return context

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "session_id": self.session_id,
            "capture_capability_manifest_ref": self.capture_capability_manifest_ref,
            "capture_capability_manifest_digest": self.capture_capability_manifest_digest,
            "capture_capability_manifest_schema_version": self.capture_capability_manifest_schema_version,
            "capture_integrity_record_ref": self.capture_integrity_record_ref,
            "capture_integrity_record_digest": self.capture_integrity_record_digest,
            "capture_integrity_record_schema_version": self.capture_integrity_record_schema_version,
            "event_types_enabled": list(self.event_types_enabled),
            "sampling_boundary_complete": self.sampling_boundary_complete,
            "mapping_valid": self.mapping_valid,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CaptureLineageContext":
        if not isinstance(value, Mapping):
            raise LineageValidationError("capture lineage context payload must be an object")
        required_fields = {
            "capture_id",
            "session_id",
            "capture_capability_manifest_ref",
            "capture_capability_manifest_digest",
            "capture_capability_manifest_schema_version",
            "capture_integrity_record_ref",
            "capture_integrity_record_digest",
            "capture_integrity_record_schema_version",
            "event_types_enabled",
            "sampling_boundary_complete",
            "mapping_valid",
        }
        if set(value) != required_fields:
            raise LineageValidationError("capture lineage context payload has unsupported fields")
        string_fields = required_fields.difference(
            {"event_types_enabled", "sampling_boundary_complete", "mapping_valid"}
        )
        if any(not isinstance(value[field], str) for field in string_fields):
            raise LineageValidationError("capture lineage context payload contains non-string identity fields")
        event_types_enabled = value["event_types_enabled"]
        if not isinstance(event_types_enabled, list) or any(
            not isinstance(event_name, str) for event_name in event_types_enabled
        ):
            raise LineageValidationError("event_types_enabled must be an array of strings")
        if value["sampling_boundary_complete"] is not None and not isinstance(
            value["sampling_boundary_complete"], bool
        ):
            raise LineageValidationError("sampling_boundary_complete must be boolean or null")
        if value["mapping_valid"] is not None and not isinstance(value["mapping_valid"], bool):
            raise LineageValidationError("mapping_valid must be boolean or null")
        try:
            context = cls(
                capture_id=value["capture_id"],
                session_id=value["session_id"],
                capture_capability_manifest_ref=value["capture_capability_manifest_ref"],
                capture_capability_manifest_digest=value["capture_capability_manifest_digest"],
                capture_capability_manifest_schema_version=value["capture_capability_manifest_schema_version"],
                capture_integrity_record_ref=value["capture_integrity_record_ref"],
                capture_integrity_record_digest=value["capture_integrity_record_digest"],
                capture_integrity_record_schema_version=value["capture_integrity_record_schema_version"],
                event_types_enabled=tuple(sorted(event_types_enabled)),
                sampling_boundary_complete=value["sampling_boundary_complete"],
                mapping_valid=value["mapping_valid"],
            )
        except (KeyError, TypeError) as exc:
            raise LineageValidationError("capture lineage context payload is incomplete") from exc
        validate_capture_lineage_context(context)
        return context


def validate_capture_lineage_context(context: CaptureLineageContext) -> None:
    if not isinstance(context, CaptureLineageContext):
        raise LineageValidationError("capture lineage context has the wrong type")
    if not isinstance(context.capture_id, str) or not _CAPTURE_ID.fullmatch(context.capture_id):
        raise LineageValidationError("capture_id has an invalid format")
    _require_identifier(context.session_id, "session_id")
    _require_identifier(context.capture_capability_manifest_ref, "capture_capability_manifest_ref")
    _require_identifier(context.capture_integrity_record_ref, "capture_integrity_record_ref")
    _require_digest(context.capture_capability_manifest_digest, "capture_capability_manifest_digest")
    _require_digest(context.capture_integrity_record_digest, "capture_integrity_record_digest")
    if context.capture_capability_manifest_schema_version != SCHEMA_VERSION:
        raise LineageValidationError("unsupported CCM schema version")
    if context.capture_integrity_record_schema_version != SCHEMA_VERSION:
        raise LineageValidationError("unsupported CIR schema version")
    if not isinstance(context.event_types_enabled, tuple):
        raise LineageValidationError("event_types_enabled must be a tuple of strings")
    if len(context.event_types_enabled) != len(set(context.event_types_enabled)):
        raise LineageValidationError("event_types_enabled contains duplicate values")
    if any(not isinstance(event_name, str) or not event_name for event_name in context.event_types_enabled):
        raise LineageValidationError("event_types_enabled contains an invalid event name")
    if context.sampling_boundary_complete is not None and not isinstance(
        context.sampling_boundary_complete, bool
    ):
        raise LineageValidationError("sampling_boundary_complete must be boolean or null")
    if context.mapping_valid is not None and not isinstance(context.mapping_valid, bool):
        raise LineageValidationError("mapping_valid must be boolean or null")


@dataclass(frozen=True, slots=True)
class RawEventBinding:
    """A registered raw event, used to reject fabricated source references."""

    event_id: str
    timestamp: float
    observed_timestamps: tuple[float, ...] = ()

    @property
    def timestamps(self) -> tuple[float, ...]:
        """All objective observations carrying this raw ID.

        Legacy traces can reuse a producer's ``core_id``/``seq`` identity.
        That ambiguity is retained here rather than replaced with a synthetic
        event ID; affected derived facts are marked ``MAPPING_INVALID``.
        """

        return getattr(self, "observed_timestamps", ()) or (self.timestamp,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "observed_timestamps": list(self.timestamps),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RawEventBinding":
        if not isinstance(value, Mapping):
            raise LineageValidationError("raw event payload must be an object")
        try:
            timestamp = _require_time(value["timestamp"], "raw event timestamp")
            timestamps = tuple(
                _require_time(item, "raw event observed timestamp")
                for item in value.get("observed_timestamps", (timestamp,))
            )
            return cls(event_id=str(value["event_id"]), timestamp=timestamp, observed_timestamps=timestamps)
        except KeyError as exc:
            raise LineageValidationError("raw event payload is incomplete") from exc


@dataclass(frozen=True, slots=True)
class CIRWindowBinding:
    """A capture-integrity-bound projection of one untrusted time window."""

    window_id: str
    interval_start: float
    interval_end: float
    reason_code: str
    capture_integrity_record_ref: str | None
    capture_integrity_record_digest: str | None
    capture_integrity_record_schema_version: str | None
    capture_id: str | None = None
    source_integrity_issue_ids: tuple[str, ...] = ()

    @property
    def lineage_effect(self) -> LineageStatus:
        return lineage_status_for_window_reason(self.reason_code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
            "reason_code": self.reason_code,
            "capture_integrity_record_ref": self.capture_integrity_record_ref,
            "capture_integrity_record_digest": self.capture_integrity_record_digest,
            "capture_integrity_record_schema_version": self.capture_integrity_record_schema_version,
            "capture_id": self.capture_id,
            "source_integrity_issue_ids": list(self.source_integrity_issue_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CIRWindowBinding":
        if not isinstance(value, Mapping):
            raise LineageValidationError("CIR window payload must be an object")
        try:
            return cls(
                window_id=str(value["window_id"]),
                interval_start=_require_time(value["interval_start"], "window interval_start"),
                interval_end=_require_time(value["interval_end"], "window interval_end"),
                reason_code=str(value["reason_code"]),
                capture_integrity_record_ref=value.get("capture_integrity_record_ref"),
                capture_integrity_record_digest=value.get("capture_integrity_record_digest"),
                capture_integrity_record_schema_version=value.get("capture_integrity_record_schema_version"),
                capture_id=value.get("capture_id"),
                source_integrity_issue_ids=tuple(value.get("source_integrity_issue_ids", ())),
            )
        except KeyError as exc:
            raise LineageValidationError("CIR window payload is incomplete") from exc


def lineage_status_for_window_reason(reason_code: str) -> LineageStatus:
    """Classify a CIR integrity reason without using diagnostic relevance."""

    if reason_code in _MAPPING_WINDOW_REASONS:
        return LineageStatus.MAPPING_INVALID
    if reason_code in _ALIGNMENT_WINDOW_REASONS:
        return LineageStatus.ALIGNMENT_DEGRADED
    return LineageStatus.LOSS_AFFECTED


def validate_cir_window_binding(
    binding: CIRWindowBinding,
    context: CaptureLineageContext | None,
) -> None:
    if not isinstance(binding, CIRWindowBinding):
        raise LineageValidationError("CIR window binding has the wrong type")
    _require_identifier(binding.window_id, "window_id")
    start = _require_time(binding.interval_start, "window interval_start")
    end = _require_time(binding.interval_end, "window interval_end")
    if end < start:
        raise LineageValidationError("window interval_end precedes interval_start")
    if not isinstance(binding.reason_code, str) or not binding.reason_code:
        raise LineageValidationError("window reason_code must be non-empty")
    _require_unique_identifiers(binding.source_integrity_issue_ids, "source_integrity_issue_ids")
    if context is None:
        if any(
            value is not None
            for value in (
                binding.capture_id,
                binding.capture_integrity_record_ref,
                binding.capture_integrity_record_digest,
                binding.capture_integrity_record_schema_version,
            )
        ):
            raise LineageValidationError("legacy window must not fabricate a CIR binding")
        return
    validate_capture_lineage_context(context)
    if binding.capture_id != context.capture_id:
        raise LineageValidationError("window capture_id does not match capture context")
    if binding.capture_integrity_record_ref != context.capture_integrity_record_ref:
        raise LineageValidationError("window CIR reference does not match capture context")
    if binding.capture_integrity_record_digest != context.capture_integrity_record_digest:
        raise LineageValidationError("window CIR digest does not match capture context")
    if binding.capture_integrity_record_schema_version != context.capture_integrity_record_schema_version:
        raise LineageValidationError("window CIR schema version does not match capture context")


@dataclass(frozen=True, slots=True)
class EvidenceLineage:
    """Objective raw-to-derived provenance for one derived object."""

    lineage_id: str
    derived_object_id: str
    derived_object_type: str
    capture_context: CaptureLineageContext | None
    source_event_ids: tuple[str, ...]
    open_event_id: str | None
    close_event_id: str | None
    boundary_event_ids: tuple[str, ...]
    derivation_rule_id: str
    derivation_version: str
    interval_start: float | None
    interval_end: float | None
    observed_horizon_start: float
    observed_horizon_end: float
    all_intersecting_untrusted_window_ids: tuple[str, ...]
    boundary_intersecting_untrusted_window_ids: tuple[str, ...]
    lineage_status: LineageStatus
    source_integrity_issue_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @property
    def intersecting_untrusted_window_ids(self) -> tuple[str, ...]:
        """Compatibility spelling for the authoritative all-intersections field."""

        return self.all_intersecting_untrusted_window_ids

    @property
    def capture_capability_manifest_ref(self) -> str | None:
        if self.capture_context is None:
            return None
        return self.capture_context.capture_capability_manifest_ref

    @property
    def capture_integrity_record_ref(self) -> str | None:
        if self.capture_context is None:
            return None
        return self.capture_context.capture_integrity_record_ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "derived_object_id": self.derived_object_id,
            "derived_object_type": self.derived_object_type,
            "source_event_ids": list(self.source_event_ids),
            "open_event_id": self.open_event_id,
            "close_event_id": self.close_event_id,
            "boundary_event_ids": list(self.boundary_event_ids),
            "derivation_rule_id": self.derivation_rule_id,
            "derivation_version": self.derivation_version,
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
            "observed_horizon_start": self.observed_horizon_start,
            "observed_horizon_end": self.observed_horizon_end,
            "all_intersecting_untrusted_window_ids": list(self.all_intersecting_untrusted_window_ids),
            "boundary_intersecting_untrusted_window_ids": list(self.boundary_intersecting_untrusted_window_ids),
            "capture_capability_manifest_ref": self.capture_capability_manifest_ref,
            "capture_integrity_record_ref": self.capture_integrity_record_ref,
            "lineage_status": self.lineage_status.value,
            "source_integrity_issue_ids": list(self.source_integrity_issue_ids),
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        capture_context: CaptureLineageContext | None,
    ) -> "EvidenceLineage":
        if not isinstance(value, Mapping):
            raise LineageValidationError("evidence lineage payload must be an object")
        if value.get("capture_capability_manifest_ref") != (
            None if capture_context is None else capture_context.capture_capability_manifest_ref
        ):
            raise LineageValidationError("lineage CCM reference does not match capture context")
        if value.get("capture_integrity_record_ref") != (
            None if capture_context is None else capture_context.capture_integrity_record_ref
        ):
            raise LineageValidationError("lineage CIR reference does not match capture context")
        try:
            return cls(
                lineage_id=str(value["lineage_id"]),
                derived_object_id=str(value["derived_object_id"]),
                derived_object_type=str(value["derived_object_type"]),
                capture_context=capture_context,
                source_event_ids=tuple(value["source_event_ids"]),
                open_event_id=value.get("open_event_id"),
                close_event_id=value.get("close_event_id"),
                boundary_event_ids=tuple(value["boundary_event_ids"]),
                derivation_rule_id=str(value["derivation_rule_id"]),
                derivation_version=str(value["derivation_version"]),
                interval_start=value.get("interval_start"),
                interval_end=value.get("interval_end"),
                observed_horizon_start=_require_time(value["observed_horizon_start"], "observed_horizon_start"),
                observed_horizon_end=_require_time(value["observed_horizon_end"], "observed_horizon_end"),
                all_intersecting_untrusted_window_ids=tuple(value["all_intersecting_untrusted_window_ids"]),
                boundary_intersecting_untrusted_window_ids=tuple(value["boundary_intersecting_untrusted_window_ids"]),
                lineage_status=LineageStatus(value["lineage_status"]),
                source_integrity_issue_ids=tuple(value.get("source_integrity_issue_ids", ())),
                reason_codes=tuple(value.get("reason_codes", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LineageValidationError("evidence lineage payload is invalid") from exc


def derive_lineage_status(
    *,
    open_event_id: str | None,
    close_event_id: str | None,
    reason_codes: Iterable[str] = (),
    intersecting_windows: Iterable[CIRWindowBinding] = (),
) -> LineageStatus:
    """Apply the frozen objective status precedence for a lineage record.

    Missing capability, mapping, and alignment semantics are recorded before
    interval completeness.  Missing boundaries remain explicit before ordinary
    loss effects, so an observed horizon can never normalize an incomplete
    relation into a complete duration fact.
    """

    reasons = frozenset(reason_codes)
    effects = {binding.lineage_effect for binding in intersecting_windows}
    if MAPPING_INVALID_REASON in reasons or LineageStatus.MAPPING_INVALID in effects:
        return LineageStatus.MAPPING_INVALID
    if CAPABILITY_UNSUPPORTED_REASON in reasons:
        return LineageStatus.CAPABILITY_UNSUPPORTED
    if open_event_id is None:
        return LineageStatus.INCOMPLETE_OPEN
    if close_event_id is None or NON_NORMAL_CLOSE_BOUNDARY_REASON in reasons:
        return LineageStatus.INCOMPLETE_CLOSE
    if ALIGNMENT_DEGRADED_REASON in reasons or LineageStatus.ALIGNMENT_DEGRADED in effects:
        return LineageStatus.ALIGNMENT_DEGRADED
    if effects or SOURCE_INTEGRITY_AFFECTED_REASON in reasons:
        return LineageStatus.LOSS_AFFECTED
    return LineageStatus.COMPLETE


def legacy_trusted_projection(value: EvidenceLineage | LineageStatus) -> bool:
    """Deprecated compatibility projection; never use it as an admission rule."""

    status = value.lineage_status if isinstance(value, EvidenceLineage) else value
    return status is LineageStatus.COMPLETE


def _validate_reason_codes(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(values)
    if len(set(normalized)) != len(normalized):
        raise LineageValidationError("reason_codes contains duplicate values")
    if any(not isinstance(value, str) or not value for value in normalized):
        raise LineageValidationError("reason_codes must contain non-empty strings")
    return normalized


def validate_evidence_lineage(
    lineage: EvidenceLineage,
    *,
    context: CaptureLineageContext | None = None,
    raw_events: Mapping[str, RawEventBinding] | None = None,
    windows: Mapping[str, CIRWindowBinding] | None = None,
) -> None:
    if not isinstance(lineage, EvidenceLineage):
        raise LineageValidationError("evidence lineage has the wrong type")
    if lineage.capture_context is not None:
        validate_capture_lineage_context(lineage.capture_context)
    if context is not None and lineage.capture_context != context:
        raise LineageValidationError("lineage capture context does not match registry context")
    _require_identifier(lineage.lineage_id, "lineage_id")
    _require_identifier(lineage.derived_object_id, "derived_object_id")
    if not isinstance(lineage.derived_object_type, str) or not lineage.derived_object_type:
        raise LineageValidationError("derived_object_type must be non-empty")
    source_ids = _require_unique_identifiers(lineage.source_event_ids, "source_event_ids", allow_empty=False)
    boundary_ids = _require_unique_identifiers(lineage.boundary_event_ids, "boundary_event_ids")
    window_ids = _require_unique_identifiers(
        lineage.all_intersecting_untrusted_window_ids,
        "all_intersecting_untrusted_window_ids",
    )
    boundary_window_ids = _require_unique_identifiers(
        lineage.boundary_intersecting_untrusted_window_ids,
        "boundary_intersecting_untrusted_window_ids",
    )
    _require_unique_identifiers(lineage.source_integrity_issue_ids, "source_integrity_issue_ids")
    reasons = _validate_reason_codes(lineage.reason_codes)
    _require_identifier(lineage.derivation_rule_id, "derivation_rule_id")
    if not isinstance(lineage.derivation_version, str) or not lineage.derivation_version:
        raise LineageValidationError("derivation_version must be non-empty")
    if not isinstance(lineage.lineage_status, LineageStatus):
        raise LineageValidationError("lineage_status must be a LineageStatus")

    for label, event_id in (("open_event_id", lineage.open_event_id), ("close_event_id", lineage.close_event_id)):
        if event_id is not None:
            _require_identifier(event_id, label)
            if event_id not in source_ids:
                raise LineageValidationError(f"{label} is not in source_event_ids")
            if event_id not in boundary_ids:
                raise LineageValidationError(f"{label} is not in boundary_event_ids")
    if not set(boundary_ids).issubset(source_ids):
        raise LineageValidationError("boundary_event_ids must be source_event_ids")
    if not set(boundary_window_ids).issubset(window_ids):
        raise LineageValidationError("boundary window IDs must be all-intersection window IDs")

    horizon_start = _require_time(lineage.observed_horizon_start, "observed_horizon_start")
    horizon_end = _require_time(lineage.observed_horizon_end, "observed_horizon_end")
    if horizon_end < horizon_start:
        raise LineageValidationError("observed_horizon_end precedes observed_horizon_start")
    interval_start = None if lineage.interval_start is None else _require_time(lineage.interval_start, "interval_start")
    interval_end = None if lineage.interval_end is None else _require_time(lineage.interval_end, "interval_end")
    if lineage.open_event_id is None and interval_start is not None:
        raise LineageValidationError("interval_start requires an authoritative open_event_id")
    if lineage.open_event_id is not None and interval_start is None:
        raise LineageValidationError("open_event_id requires interval_start")
    if lineage.close_event_id is None and interval_end is not None:
        raise LineageValidationError("interval_end requires an authoritative close_event_id")
    if lineage.close_event_id is not None and interval_end is None:
        raise LineageValidationError("close_event_id requires interval_end")
    if interval_start is not None and not horizon_start <= interval_start <= horizon_end:
        raise LineageValidationError("interval_start is outside the observed horizon")
    if interval_end is not None and not horizon_start <= interval_end <= horizon_end:
        raise LineageValidationError("interval_end is outside the observed horizon")
    if interval_start is not None and interval_end is not None and interval_end < interval_start:
        raise LineageValidationError("interval_end precedes interval_start")

    if lineage.lineage_status is LineageStatus.COMPLETE:
        if lineage.open_event_id is None or lineage.close_event_id is None:
            raise LineageValidationError("COMPLETE lineage requires authoritative open and close events")
        if window_ids:
            raise LineageValidationError("COMPLETE lineage cannot intersect untrusted windows")
    if lineage.lineage_status is LineageStatus.INCOMPLETE_OPEN and lineage.open_event_id is not None:
        raise LineageValidationError("INCOMPLETE_OPEN lineage must not have an open_event_id")
    if lineage.lineage_status is LineageStatus.INCOMPLETE_CLOSE:
        has_non_normal_close = NON_NORMAL_CLOSE_BOUNDARY_REASON in reasons
        if lineage.close_event_id is not None and not has_non_normal_close:
            raise LineageValidationError("INCOMPLETE_CLOSE close_event_id requires a non-normal close boundary")
    if lineage.lineage_status is LineageStatus.LOSS_AFFECTED and not window_ids:
        raise LineageValidationError("LOSS_AFFECTED lineage requires an untrusted-window intersection")
    if lineage.lineage_status is LineageStatus.CAPABILITY_UNSUPPORTED and CAPABILITY_UNSUPPORTED_REASON not in reasons:
        raise LineageValidationError("CAPABILITY_UNSUPPORTED lineage requires its reason code")
    if lineage.capture_context is None:
        if lineage.lineage_status is LineageStatus.COMPLETE:
            raise LineageValidationError("COMPLETE lineage requires a capture context")
        required_legacy_reasons = {
            CAPABILITY_UNSUPPORTED_REASON,
            MISSING_CAPTURE_LINEAGE_CONTEXT_REASON,
            MISSING_CCM_REF_REASON,
            MISSING_CIR_REF_REASON,
        }
        if not required_legacy_reasons.issubset(reasons):
            raise LineageValidationError("legacy lineage requires explicit missing capture reasons")

    if raw_events is not None:
        missing = sorted(set(source_ids).difference(raw_events))
        if missing:
            raise LineageValidationError(f"lineage references unregistered raw event {missing[0]}")
        if lineage.open_event_id is not None:
            if interval_start not in raw_events[lineage.open_event_id].timestamps:
                raise LineageValidationError("interval_start does not match the raw open event timestamp")
        if lineage.close_event_id is not None:
            if interval_end not in raw_events[lineage.close_event_id].timestamps:
                raise LineageValidationError("interval_end does not match the raw close event timestamp")
    if windows is not None:
        missing = sorted(set(window_ids).difference(windows))
        if missing:
            raise LineageValidationError(f"lineage references unregistered CIR window {missing[0]}")
        resolved_windows = [windows[window_id] for window_id in window_ids]
        expected_status = derive_lineage_status(
            open_event_id=lineage.open_event_id,
            close_event_id=lineage.close_event_id,
            reason_codes=reasons,
            intersecting_windows=resolved_windows,
        )
        if lineage.lineage_status is not expected_status:
            raise LineageValidationError(
                f"lineage_status {lineage.lineage_status.value} does not match objective lineage facts "
                f"({expected_status.value})"
            )


def _effective_interval(lineage: EvidenceLineage) -> tuple[float, float]:
    start = lineage.interval_start
    end = lineage.interval_end
    return (
        lineage.observed_horizon_start if start is None else float(start),
        lineage.observed_horizon_end if end is None else float(end),
    )


def _overlaps(start: float, end: float, window_start: float, window_end: float) -> bool:
    return max(start, window_start) <= min(end, window_end)


@dataclass(frozen=True, slots=True)
class LineageBacktrace:
    """One raw-to-derived provenance view returned by ``LineageRegistry``."""

    lineage_id: str
    derived_object_id: str
    source_event_ids: tuple[str, ...]
    open_event_id: str | None
    close_event_id: str | None
    boundary_event_ids: tuple[str, ...]
    all_intersecting_untrusted_window_ids: tuple[str, ...]
    capture_context: CaptureLineageContext | None


@dataclass(slots=True)
class LineageRegistry:
    """Capture-scoped registry enforcing raw-event and CIR-window resolution."""

    capture_context: CaptureLineageContext | None
    registry_version: str = LINEAGE_SCHEMA_VERSION
    registry_scope: str = FULL_CAPTURE_REGISTRY_SCOPE
    raw_events: dict[str, RawEventBinding] = field(default_factory=dict)
    cir_window_bindings: dict[str, CIRWindowBinding] = field(default_factory=dict)
    records: dict[str, EvidenceLineage] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.capture_context is not None:
            validate_capture_lineage_context(self.capture_context)
        if self.registry_scope not in {FULL_CAPTURE_REGISTRY_SCOPE, DERIVED_OBJECT_SUBSET_REGISTRY_SCOPE}:
            raise LineageValidationError("unsupported lineage registry scope")

    def register_raw_event(self, event_id: str, timestamp: float) -> None:
        _require_identifier(event_id, "raw event_id")
        normalized_timestamp = _require_time(timestamp, "raw event timestamp")
        if event_id in self.raw_events:
            existing = self.raw_events[event_id]
            timestamps = tuple(sorted(set(existing.timestamps + (normalized_timestamp,))))
            if timestamps != existing.timestamps:
                self.raw_events[event_id] = RawEventBinding(
                    event_id=event_id,
                    timestamp=timestamps[0],
                    observed_timestamps=timestamps,
                )
            return
        if event_id in self.records:
            raise LineageValidationError("raw event ID conflicts with a lineage record ID")
        self.raw_events[event_id] = RawEventBinding(event_id=event_id, timestamp=normalized_timestamp)

    def register_raw_events(self, events: Iterable[RawEventBinding]) -> None:
        for event in events:
            if not isinstance(event, RawEventBinding):
                raise LineageValidationError("raw event registration has the wrong type")
            for timestamp in event.timestamps:
                self.register_raw_event(event.event_id, timestamp)

    def register_cir_window(self, binding: CIRWindowBinding) -> None:
        validate_cir_window_binding(binding, self.capture_context)
        if binding.window_id in self.cir_window_bindings:
            raise LineageValidationError(f"duplicate CIR window registration {binding.window_id}")
        self.cir_window_bindings[binding.window_id] = binding

    @property
    def window_integrity_bindings(self) -> dict[str, CIRWindowBinding]:
        """P3 contract name for the registry's CIR-bound untrusted windows."""

        return self.cir_window_bindings

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "registry_version": self.registry_version,
            "registry_scope": getattr(self, "registry_scope", FULL_CAPTURE_REGISTRY_SCOPE),
            "capture_context": None if self.capture_context is None else self.capture_context.to_dict(),
            "raw_events": [
                binding.to_dict()
                for binding in sorted(self.raw_events.values(), key=lambda item: item.event_id)
            ],
            "window_integrity_bindings": [
                binding.to_dict()
                for binding in sorted(self.cir_window_bindings.values(), key=lambda item: item.window_id)
            ],
            "records": [
                lineage.to_dict()
                for lineage in sorted(self.records.values(), key=lambda item: item.lineage_id)
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LineageRegistry":
        if not isinstance(value, Mapping):
            raise LineageValidationError("lineage registry payload must be an object")
        context_raw = value.get("capture_context")
        context = None if context_raw is None else CaptureLineageContext.from_dict(context_raw)
        registry = cls(
            context,
            registry_version=str(value.get("registry_version", "")),
            registry_scope=str(value.get("registry_scope", FULL_CAPTURE_REGISTRY_SCOPE)),
        )
        raw_events = value.get("raw_events")
        windows = value.get("window_integrity_bindings", value.get("cir_window_bindings"))
        records = value.get("records")
        if not isinstance(raw_events, list) or not isinstance(windows, list) or not isinstance(records, list):
            raise LineageValidationError("lineage registry payload has invalid collections")
        for raw_event in raw_events:
            binding = RawEventBinding.from_dict(raw_event)
            registry.register_raw_events((binding,))
        for window in windows:
            registry.register_cir_window(CIRWindowBinding.from_dict(window))
        for record in records:
            registry.register_lineage(EvidenceLineage.from_dict(record, capture_context=context))
        registry.validate()
        return registry

    def subset(self, lineage_ids: Iterable[str]) -> "LineageRegistry":
        """Return an independently valid registry for the selected derived objects."""

        requested_ids = tuple(dict.fromkeys(lineage_ids))
        missing = sorted(set(requested_ids).difference(self.records))
        if missing:
            raise LineageValidationError(f"unknown lineage ID {missing[0]}")
        result = LineageRegistry(self.capture_context, registry_scope=DERIVED_OBJECT_SUBSET_REGISTRY_SCOPE)
        selected = [self.records[lineage_id] for lineage_id in requested_ids]
        source_ids = {event_id for lineage in selected for event_id in lineage.source_event_ids}
        window_ids = {
            window_id
            for lineage in selected
            for window_id in lineage.all_intersecting_untrusted_window_ids
        }
        for event_id in sorted(source_ids):
            binding = self.raw_events[event_id]
            result.register_raw_events((binding,))
        for window_id in sorted(window_ids):
            result.register_cir_window(self.cir_window_bindings[window_id])
        for lineage in sorted(selected, key=lambda item: item.lineage_id):
            result.register_lineage(lineage)
        result.validate()
        return result

    def intersecting_window_ids(self, lineage: EvidenceLineage) -> tuple[str, ...]:
        validate_evidence_lineage(lineage, context=self.capture_context)
        start, end = _effective_interval(lineage)
        boundary_timestamps = {
            timestamp
            for event_id in lineage.boundary_event_ids
            for timestamp in self.raw_events[event_id].timestamps
        }
        return tuple(
            binding.window_id
            for binding in sorted(
                self.cir_window_bindings.values(),
                key=lambda item: (item.interval_start, item.interval_end, item.window_id),
            )
            if (
                _overlaps(start, end, binding.interval_start, binding.interval_end)
                or any(binding.interval_start <= timestamp <= binding.interval_end for timestamp in boundary_timestamps)
            )
        )

    def boundary_intersecting_window_ids(self, lineage: EvidenceLineage) -> tuple[str, ...]:
        validate_evidence_lineage(lineage, context=self.capture_context, raw_events=self.raw_events)
        matches: set[str] = set()
        for event_id in lineage.boundary_event_ids:
            for timestamp in self.raw_events[event_id].timestamps:
                for binding in self.cir_window_bindings.values():
                    if binding.interval_start <= timestamp <= binding.interval_end:
                        matches.add(binding.window_id)
        return tuple(
            binding.window_id
            for binding in sorted(
                self.cir_window_bindings.values(),
                key=lambda item: (item.interval_start, item.interval_end, item.window_id),
            )
            if binding.window_id in matches
        )

    def register_lineage(self, lineage: EvidenceLineage) -> None:
        if lineage.lineage_id in self.records:
            raise LineageValidationError(f"duplicate lineage registration {lineage.lineage_id}")
        if lineage.lineage_id in self.raw_events:
            raise LineageValidationError("lineage ID conflicts with a raw event ID")
        validate_evidence_lineage(
            lineage,
            context=self.capture_context,
            raw_events=self.raw_events,
            windows=self.cir_window_bindings,
        )
        expected_windows = self.intersecting_window_ids(lineage)
        if lineage.all_intersecting_untrusted_window_ids != expected_windows:
            raise LineageValidationError("lineage does not retain every objective untrusted-window intersection")
        expected_boundary_windows = self.boundary_intersecting_window_ids(lineage)
        if lineage.boundary_intersecting_untrusted_window_ids != expected_boundary_windows:
            raise LineageValidationError("lineage does not retain every boundary-window intersection")
        self.records[lineage.lineage_id] = lineage

    def validate(self) -> None:
        if self.registry_version != LINEAGE_SCHEMA_VERSION:
            raise LineageValidationError("unsupported lineage registry version")
        if getattr(self, "registry_scope", FULL_CAPTURE_REGISTRY_SCOPE) not in {
            FULL_CAPTURE_REGISTRY_SCOPE,
            DERIVED_OBJECT_SUBSET_REGISTRY_SCOPE,
        }:
            raise LineageValidationError("unsupported lineage registry scope")
        if self.capture_context is not None:
            validate_capture_lineage_context(self.capture_context)
        for event_id, event in self.raw_events.items():
            if event.event_id != event_id:
                raise LineageValidationError("raw event registry key does not match event_id")
            _require_identifier(event.event_id, "raw event_id")
            _require_time(event.timestamp, "raw event timestamp")
            timestamps = tuple(_require_time(value, "raw event observed timestamp") for value in event.timestamps)
            if timestamps != tuple(sorted(set(timestamps))):
                raise LineageValidationError("raw event observed timestamps must be sorted and unique")
            if event.timestamp != timestamps[0]:
                raise LineageValidationError("raw event timestamp must be the first observed timestamp")
        for window_id, binding in self.cir_window_bindings.items():
            if binding.window_id != window_id:
                raise LineageValidationError("CIR window registry key does not match window_id")
            validate_cir_window_binding(binding, self.capture_context)
        for lineage_id, lineage in self.records.items():
            if lineage.lineage_id != lineage_id:
                raise LineageValidationError("lineage registry key does not match lineage_id")
            validate_evidence_lineage(
                lineage,
                context=self.capture_context,
                raw_events=self.raw_events,
                windows=self.cir_window_bindings,
            )
            if lineage.all_intersecting_untrusted_window_ids != self.intersecting_window_ids(lineage):
                raise LineageValidationError("lineage all-window intersections are incomplete or stale")
            if lineage.boundary_intersecting_untrusted_window_ids != self.boundary_intersecting_window_ids(lineage):
                raise LineageValidationError("lineage boundary-window intersections are incomplete or stale")

    def backtrace(self, lineage_id: str) -> LineageBacktrace:
        self.validate()
        try:
            lineage = self.records[lineage_id]
        except KeyError as exc:
            raise LineageValidationError(f"unknown lineage ID {lineage_id}") from exc
        return LineageBacktrace(
            lineage_id=lineage.lineage_id,
            derived_object_id=lineage.derived_object_id,
            source_event_ids=lineage.source_event_ids,
            open_event_id=lineage.open_event_id,
            close_event_id=lineage.close_event_id,
            boundary_event_ids=lineage.boundary_event_ids,
            all_intersecting_untrusted_window_ids=lineage.all_intersecting_untrusted_window_ids,
            capture_context=lineage.capture_context,
        )


def validate_rebuild_bundle_lineage(
    bundle: Any,
    *,
    raw_events: Iterable[Any] | None = None,
    require_complete_raw_event_set: bool = False,
) -> None:
    """Validate P3 lineage bindings against a rebuilt or deserialized bundle.

    ``raw_events`` is optional because a clipped export intentionally carries a
    registry subset while its event stream is window-scoped.  The rebuild path
    supplies the complete raw stream, which also verifies that the registry did
    not gain a source ID or timestamp absent from that raw evidence.
    """

    registry = getattr(bundle, "lineage_registry", None)
    if registry is None:
        return
    if not isinstance(registry, LineageRegistry):
        raise LineageValidationError("rebuild bundle lineage_registry has the wrong type")
    registry.validate()

    collections = (
        ("task_state_segment", "seg_id", getattr(bundle, "task_states", ())),
        ("exec_slice", "slice_id", getattr(bundle, "exec_slices", ())),
        ("resource_hold", "edge_id", getattr(getattr(bundle, "resource_graph", None), "hold_edges", ())),
        ("resource_wait", "edge_id", getattr(getattr(bundle, "resource_graph", None), "wait_edges", ())),
        ("irq_span", "irq_span_id", getattr(bundle, "irq_spans", ())),
        ("ready_not_running", "interval_id", getattr(bundle, "ready_not_running_intervals", ())),
    )
    for expected_type, object_id_field, objects in collections:
        for item in objects:
            lineage_id = getattr(item, "lineage_id", None)
            object_id = getattr(item, object_id_field, None)
            if not isinstance(lineage_id, str) or not lineage_id:
                raise LineageValidationError(f"{expected_type} is missing a lineage_id")
            if lineage_id not in registry.records:
                raise LineageValidationError(f"{expected_type} lineage_id is not registered")
            lineage = registry.records[lineage_id]
            if lineage.derived_object_type != expected_type or lineage.derived_object_id != object_id:
                raise LineageValidationError(f"{expected_type} lineage does not bind its derived object")

    observed_raw: dict[str, set[float]] = {}
    for event in raw_events if raw_events is not None else ():
        event_id = getattr(event, "event_uid", None)
        timestamp = getattr(event, "timestamp_aligned", None)
        if not isinstance(event_id, str) or timestamp is None:
            continue
        observed_raw.setdefault(event_id, set()).add(_require_time(timestamp, "bundle raw event timestamp"))
    for event_id, timestamps in observed_raw.items():
        binding = registry.raw_events.get(event_id)
        if binding is None:
            if require_complete_raw_event_set:
                raise LineageValidationError("bundle raw event is absent from the lineage registry")
            continue
        if not timestamps.issubset(binding.timestamps):
            raise LineageValidationError("bundle raw event timestamp does not match lineage registry")
    if require_complete_raw_event_set:
        if set(registry.raw_events) != set(observed_raw):
            raise LineageValidationError("lineage registry raw events do not match the rebuilt raw stream")
        for event_id, binding in registry.raw_events.items():
            if set(binding.timestamps) != observed_raw[event_id]:
                raise LineageValidationError("lineage registry raw event timestamps do not match the rebuilt raw stream")

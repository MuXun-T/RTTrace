"""Fail-closed Phase 2 RTD-Pilot capture contracts.

These contracts are deliberately offline.  They bind retained capture material;
they do not construct parser lineage or diagnosis input.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from spec.schema_loader import load_schema
from spec.schema_validator import validate_schema


SCHEMA_VERSION = "rtd-phase2-v1.0"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_CASE_ID = re.compile(r"^case:[A-Za-z0-9][A-Za-z0-9._-]*$")
_CAPTURE_ID = re.compile(r"^capture:[A-Za-z0-9][A-Za-z0-9._-]*$")
_SCENARIO_TEMPLATE_ID = re.compile(r"^scenario:[A-Za-z0-9][A-Za-z0-9._-]*$")
_SCHEMAS = {
    "ledger": "rtd_injection_ledger.schema.json",
    "seal": "rtd_ledger_session_seal.schema.json",
    "snapshot": "rtd_collector_config_snapshot.schema.json",
    "ccm": "rtd_capture_capability_manifest.schema.json",
    "cir": "rtd_capture_integrity_record.schema.json",
    "case": "rtd_case_definition.schema.json",
    "oar": "rtd_observer_record.schema.json",
    "inventory": "rtd_raw_artifact_inventory.schema.json",
    "cvr": "rtd_capture_validity_record.schema.json",
}
_RECORD_IDS = {
    "ledger": "ledger_record_id", "seal": "seal_record_id", "snapshot": "config_snapshot_id",
    "ccm": "capability_manifest_id", "cir": "cir_record_id", "case": "case_definition_id",
    "oar": "observer_adjudication_id", "inventory": "inventory_record_id",
    "cvr": "capture_validity_record_id",
}
_HASH_FIELDS = {
    "ledger": ("firmware_hash", "ELF_hash", "config_hash", "collector_config_hash", "observer_adjudication_digest", "capture_validity_record_digest"),
    "seal": ("last_record_digest", "ledger_file_sha256"),
    "snapshot": ("source_metadata_digest", "config_hash"),
    "ccm": ("config_snapshot_digest", "collector_config_hash"),
    "cir": ("capability_manifest_digest", "firmware_hash", "ELF_hash", "config_hash"),
    "oar": (),
}
_TRUTH_FIELDS = frozenset({
    "fault_manifested", "manifestation_interval", "manifestation_status",
    "verdict", "diagnosis", "diagnosis_verdict", "agent_output", "relevance",
    "outcome", "result",
})
_LEDGER_FORBIDDEN = _TRUTH_FIELDS | frozenset({
    "observer_status", "capture_validity_status", "validity_status",
    "invalid_capture", "invalid_reason", "observer_artifact_hash",
    "observer_config_hash", "observer_hash", "raw_trace_id", "raw_trace_hash",
})


class ContractError(ValueError):
    """A contract is malformed, mismatched, or not technically admissible."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def record_digest(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("record_digest", None)
    return sha256(canonical_json(payload)).hexdigest()


def finalize_record(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result["record_digest"] = record_digest(result)
    return result


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _hash(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    _require(isinstance(value, str) and bool(_HASH.fullmatch(value)), f"{label} must be a lowercase SHA-256 digest")


def _identifier(value: Any, label: str) -> None:
    _require(isinstance(value, str) and bool(_ID.fullmatch(value)), f"{label} must be an ASCII identifier")


def _prefixed_identifier(value: Any, pattern: re.Pattern[str], label: str) -> None:
    _require(isinstance(value, str) and bool(pattern.fullmatch(value)), f"{label} has an invalid format")


def _case_identity(value: dict[str, Any]) -> None:
    _prefixed_identifier(value["case_id"], _CASE_ID, "case_id")
    _prefixed_identifier(value["scenario_template_id"], _SCENARIO_TEMPLATE_ID, "scenario_template_id")


def _version(value: Any) -> None:
    _require(isinstance(value, str) and value.startswith("rtd-phase2-v"), "schema_version must use rtd-phase2-v<major>.<minor>")
    match = re.fullmatch(r"rtd-phase2-v(\d+)\.(\d+)", value)
    _require(match is not None, "schema_version must use rtd-phase2-v<major>.<minor>")
    _require(int(match.group(1)) == 1, "unsupported schema major version")
    _require(int(match.group(2)) <= 0, "unsupported future schema minor version")


def _schema(kind: str, value: dict[str, Any]) -> None:
    reason = validate_schema(load_schema(_SCHEMAS[kind]), value)
    _require(reason is None, f"{kind} schema invalid: {reason}")
    _version(value["schema_version"])
    _identifier(value[_RECORD_IDS[kind]], _RECORD_IDS[kind])
    _hash(value["record_digest"], "record_digest")
    _require(value["record_digest"] == record_digest(value), f"{kind} record_digest mismatch")
    for field in _HASH_FIELDS.get(kind, ()):
        _hash(value[field], field)


def _capture_identity(value: dict[str, Any]) -> None:
    _prefixed_identifier(value["capture_id"], _CAPTURE_ID, "capture_id")
    _identifier(value["session_id"], "session_id")


def _forbid(value: dict[str, Any], fields: Iterable[str], label: str) -> None:
    found = sorted(set(value).intersection(fields))
    if found:
        raise ContractError(f"{label} contains forbidden field {found[0]}")


def validate_ledger_record(value: dict[str, Any]) -> None:
    _forbid(value, _LEDGER_FORBIDDEN, "ledger")
    _schema("ledger", value)
    _case_identity(value)
    _capture_identity(value)
    _identifier(value["ledger_record_id"], "ledger_record_id")
    _require(value["append_sequence"] >= 1, "append_sequence must be positive")
    if value["append_sequence"] == 1:
        _require(value["previous_record_digest"] is None, "first ledger row must have null previous_record_digest")
    else:
        _hash(value["previous_record_digest"], "previous_record_digest")
    if value["injection_status"] == "enabled":
        _require(value["injection_enable_epoch"] is not None, "enabled injection requires injection_enable_epoch")
    else:
        _require(value["injection_enable_epoch"] is None, "non-enabled injection must have null injection_enable_epoch")
    if value["control_type"] == "healthy_control":
        _require(value["injection_status"] == "not_applicable", "healthy control requires not_applicable injection_status")


def validate_snapshot(value: dict[str, Any]) -> None:
    _schema("snapshot", value)
    for field in ("event_types_enabled", "task_filter", "resource_filter", "irq_filter", "core_filter", "payload_fields"):
        _require(len(value[field]) == len(set(value[field])), f"{field} contains duplicate values")


def validate_ccm(value: dict[str, Any]) -> None:
    _schema("ccm", value)
    _capture_identity(value)
    for field in ("event_types_enabled", "task_filter", "resource_filter", "irq_filter", "core_filter", "payload_fields"):
        _require(len(value[field]) == len(set(value[field])), f"{field} contains duplicate values")
    _forbid(value, {"sequence_gaps", "loss", "overflow", "overflow_count", "buffer_high_watermark", "truncation", "corruption", "integrity_status"}, "CCM")


def validate_cir(value: dict[str, Any]) -> None:
    _schema("cir", value)
    _capture_identity(value)
    _hash(value["raw_trace_hash"], "raw_trace_hash", nullable=True)
    _forbid(value, _TRUTH_FIELDS, "CIR")
    if value["sequence_continuity"] == "continuous":
        _require(not value["sequence_gaps"], "continuous CIR cannot contain sequence_gaps")
    if value["overflow_count"] > 0:
        _require(value["overflow"], "overflow_count requires overflow")
    if value["natural_overflow"]:
        _require(value["overflow"], "natural_overflow requires overflow")
    if value["integrity_status"] == "complete":
        _require(value["sequence_continuity"] == "continuous" and not value["sequence_gaps"], "complete CIR cannot have sequence gaps")
        _require(not any((value["loss"], value["overflow"], value["overflow_count"], value["truncation"], value["corruption"], value["alignment_degradation"], value["mapping_mismatch"], value["observer_loss"], value["natural_overflow"])), "complete CIR cannot report degradation")
        _require(not value["affected_intervals"] and not value["affected_channels"] and not value["affected_entities"] and not value["reason_codes"], "complete CIR cannot report affected material")


def validate_case_definition(value: dict[str, Any]) -> None:
    _schema("case", value)
    _forbid(value, {"capture_id", "manifestation_status", "manifestation_interval", "fault_manifested", "verdict", "episode"}, "case definition")
    _case_identity(value)
    _require(value["example_only"] is True, "real Case definition is forbidden in Phase 2; example_only must be true")


def validate_observer_record(value: dict[str, Any]) -> None:
    """Validate the OAR retained under the legacy observer-record file name."""
    _forbid(value, {"observer_record_id", "observer_config_hash", "epoch_integrity", "alignment_integrity", "verdict", "diagnosis", "diagnosis_verdict", "agent_output", "relevance", "capture_validity_status", "validity_status", "invalid_reason"}, "OAR")
    _schema("oar", value)
    _prefixed_identifier(value["case_id"], _CASE_ID, "case_id")
    _capture_identity(value)
    if value["manifestation_status"] == "manifested":
        _require(isinstance(value["manifestation_interval"], dict), "manifested observer record requires manifestation_interval")
    else:
        _require(value["manifestation_interval"] is None, "non-manifested observer record must have null manifestation_interval")
    if value["observer_status"] == "complete":
        _hash(value["observer_artifact_hash"], "observer_artifact_hash")
    else:
        _hash(value["observer_artifact_hash"], "observer_artifact_hash", nullable=True)


def validate_inventory(value: dict[str, Any]) -> None:
    _schema("inventory", value)
    _capture_identity(value)
    raw = [item for item in value["artifacts"] if item["kind"] == "raw_trace"]
    _require(len(raw) == 1, "inventory must contain exactly one raw_trace artifact")
    seen: set[str] = set()
    for item in value["artifacts"]:
        _identifier(item["artifact_id"], "artifact_id")
        _require(item["artifact_id"] not in seen, "inventory contains duplicate artifact_id")
        seen.add(item["artifact_id"])
        _require(not Path(item["logical_name"]).is_absolute() and ".." not in Path(item["logical_name"]).parts, "artifact logical_name must be relative")
        if item["availability"] == "available":
            _hash(item["hash"], "artifact hash")
        else:
            _require(item["hash"] is None, "missing artifact must have null hash")


def validate_capture_validity(value: dict[str, Any]) -> None:
    _forbid(value, _TRUTH_FIELDS | frozenset({"validity_status", "observer_record_id", "observer_record_digest", "ledger_record_id", "ledger_record_digest"}), "CVR")
    _schema("cvr", value)
    _prefixed_identifier(value["case_id"], _CASE_ID, "case_id")
    _capture_identity(value)
    for field in ("capability_manifest_digest", "cir_digest", "observer_adjudication_digest", "inventory_digest"):
        _hash(value[field], field)
    _hash(value["raw_trace_hash"], "raw_trace_hash", nullable=True)
    if value["capture_validity_status"] == "invalid":
        _require(value["invalid_reason"] not in (None, ""), "invalid capture requires invalid_reason")
    else:
        _require(value["invalid_reason"] is None, "non-invalid capture must have null invalid_reason")


def validate_seal(value: dict[str, Any], rows: list[dict[str, Any]] | None = None, raw: bytes | None = None) -> None:
    _schema("seal", value)
    _identifier(value["session_id"], "session_id")
    if rows is not None:
        _require(rows, "sealed ledger cannot be empty")
        _require(value["capture_count"] == len(rows), "seal capture_count mismatch")
        _require(value["first_append_sequence"] == rows[0]["append_sequence"], "seal first_append_sequence mismatch")
        _require(value["last_append_sequence"] == rows[-1]["append_sequence"], "seal last_append_sequence mismatch")
        _require(value["last_record_digest"] == rows[-1]["record_digest"], "seal last_record_digest mismatch")
    if raw is not None:
        _require(value["ledger_file_sha256"] == sha256(raw).hexdigest(), "seal ledger_file_sha256 mismatch")


def validate_ledger_stream(rows: list[dict[str, Any]], seal: dict[str, Any] | None = None, raw: bytes | None = None) -> None:
    _require(bool(rows), "ledger stream is empty")
    previous: str | None = None
    session: str | None = None
    seen_records: set[str] = set()
    for index, row in enumerate(rows, start=1):
        validate_ledger_record(row)
        _require(row["append_sequence"] == index, "ledger append_sequence is not monotonic")
        _require(row["previous_record_digest"] == previous, "ledger previous_record_digest mismatch")
        _require(row["ledger_record_id"] not in seen_records, "duplicate ledger_record_id")
        seen_records.add(row["ledger_record_id"])
        session = session or row["session_id"]
        _require(row["session_id"] == session, "ledger stream mixes session_id values")
        previous = row["record_digest"]
    # The JSONL append chain orders rows, while prior_ledger_ref preserves each
    # capture's immutable correction history.  Both are required.
    _current_records(rows, "ledger")
    if seal is not None:
        validate_seal(seal, rows, raw)
        _require(seal["session_id"] == session, "seal session_id mismatch")


def _current_version_records(
    rows: list[dict[str, Any]],
    kind: str,
    partition_fields: tuple[str, ...],
    constant_fields: tuple[str, ...] = (),
) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Resolve each immutable version partition to exactly one unreferenced leaf."""
    prior_field = {
        "ledger": "prior_ledger_ref", "ccm": "prior_capability_ref", "cir": "prior_cir_ref",
        "oar": "prior_adjudication_ref", "inventory": "prior_inventory_ref", "cvr": "prior_validity_ref",
        "case": "prior_case_definition_ref", "snapshot": "prior_snapshot_ref", "seal": "prior_seal_ref",
    }[kind]
    record_id = _RECORD_IDS[kind]
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    partitions = {tuple(row[field] for field in partition_fields) for row in rows}
    for partition in partitions:
        group = [row for row in rows if tuple(row[field] for field in partition_fields) == partition]
        label = ", ".join(f"{field}={value}" for field, value in zip(partition_fields, partition))
        for field in constant_fields:
            _require(len({row[field] for row in group}) == 1, f"{kind} {label} mixes {field} values")
        by_digest = {row["record_digest"]: row for row in group}
        _require(len(by_digest) == len(group), f"{kind} {label} has duplicate record_digest")
        _require(len({row[record_id] for row in group}) == len(group), f"{kind} {label} has duplicate record id")
        children: dict[str, int] = {digest: 0 for digest in by_digest}
        for row in group:
            prior = row[prior_field]
            if prior is not None:
                _hash(prior, prior_field)
                _require(prior in by_digest, f"{kind} {label} has dangling {prior_field}")
                parent = by_digest[prior]
                _require(
                    all(parent[field] == row[field] for field in (*partition_fields, *constant_fields)),
                    f"{kind} {prior_field} crosses version partition",
                )
                children[prior] += 1
                _require(children[prior] == 1, f"{kind} {label} has forked history")
        leaves = [by_digest[digest] for digest, count in children.items() if count == 0]
        _require(len(leaves) == 1, f"{kind} {label} requires one current/latest record")
        seen: set[str] = set()
        cursor = leaves[0]
        while cursor is not None:
            digest = cursor["record_digest"]
            _require(digest not in seen, f"{kind} {label} has cyclic history")
            seen.add(digest)
            prior = cursor[prior_field]
            cursor = by_digest[prior] if prior is not None else None
        _require(seen == set(by_digest), f"{kind} {label} has disconnected history")
        result[partition] = leaves[0]
    return result


def _current_records(rows: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    """Resolve an append-only per-capture prior-digest chain to its sole leaf."""
    current = _current_version_records(rows, kind, ("capture_id",), ("session_id",))
    return {capture_id: record for (capture_id,), record in current.items()}


def _case_identity_signature(value: dict[str, Any]) -> bytes:
    """Fields that require a new Case ID when changed."""
    fields = (
        "scenario_template_id", "fault_family", "fault_variant", "control_type",
        "injection_definition", "injection_version", "injection_parameters",
        "entity_binding", "workload_seed", "manifestation_predicate_id",
        "manifestation_predicate_version", "config_hash",
    )
    return canonical_json({field: value[field] for field in fields})


def validate_case_definition_collection(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    _require(bool(rows), "case definition collection is empty")
    for row in rows:
        validate_case_definition(row)
    _require(len({row["case_definition_id"] for row in rows}) == len(rows), "case definition collection has duplicate case_definition_id")
    current = {case_id: record for (case_id,), record in _current_version_records(rows, "case", ("case_id",)).items()}
    for case_id in current:
        versions = [row for row in rows if row["case_id"] == case_id]
        _require(len({_case_identity_signature(row) for row in versions}) == 1, f"case_id {case_id} is reused after identity-defining configuration changed")
    return current


def _require_stable_capture_case_binding(rows: list[dict[str, Any]], kind: str) -> None:
    bindings: dict[str, set[str]] = {}
    for row in rows:
        bindings.setdefault(row["capture_id"], set()).add(row["case_id"])
    for capture_id, case_ids in bindings.items():
        _require(len(case_ids) == 1, f"capture_id {capture_id} is bound to multiple Case IDs in {kind} history")


def validate_snapshot_collection(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    _require(bool(rows), "snapshot collection is empty")
    for row in rows:
        validate_snapshot(row)
    return {config_hash: record for (config_hash,), record in _current_version_records(rows, "snapshot", ("config_hash",)).items()}


def validate_seal_collection(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    _require(bool(rows), "seal collection is empty")
    for row in rows:
        validate_seal(row)
    return {session_id: record for (session_id,), record in _current_version_records(rows, "seal", ("session_id",)).items()}


def _validate_bundle(bundle: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, str], dict[str, dict[str, dict[str, Any]]]]:
    required = {"ledger", "ccm", "cir", "oar", "inventory", "cvr"}
    _require(set(bundle) == required, "capture bundle must contain ledger, ccm, cir, oar, inventory, cvr")
    validate_ledger_stream(bundle["ledger"])
    validators = {"ccm": validate_ccm, "cir": validate_cir, "oar": validate_observer_record, "inventory": validate_inventory, "cvr": validate_capture_validity}
    for kind, validator in validators.items():
        for row in bundle[kind]:
            validator(row)
    for kind in ("ledger", "oar", "cvr"):
        _require_stable_capture_case_binding(bundle[kind], kind)
    current = {kind: _current_records(rows, kind) for kind, rows in bundle.items()}
    capture_ids = set(current["ledger"])
    for kind, records in current.items():
        _require(set(records) == capture_ids, f"{kind} capture set does not match ledger")
    status: dict[str, str] = {}
    for capture_id, ledger in current["ledger"].items():
        ccm, cir, oar, inventory, cvr = (current[kind][capture_id] for kind in ("ccm", "cir", "oar", "inventory", "cvr"))
        sessions = {record["session_id"] for record in (ledger, ccm, cir, oar, inventory, cvr)}
        _require(len(sessions) == 1, f"capture_id {capture_id} has conflicting session_id")
        _require(ledger["case_id"] == oar["case_id"] == cvr["case_id"], f"capture_id {capture_id} has conflicting case_id")
        _require(ledger["collector_config_hash"] == ccm["collector_config_hash"], "ledger and CCM collector_config_hash mismatch")
        _require(cir["capability_manifest_id"] == ccm["capability_manifest_id"] and cir["capability_manifest_digest"] == ccm["record_digest"], "CIR CCM binding mismatch")
        raw = next(item for item in inventory["artifacts"] if item["kind"] == "raw_trace")
        _require(cir["raw_trace_id"] == cvr["raw_trace_id"] == raw["artifact_id"], "raw trace identity mismatch")
        _require(cir["raw_trace_hash"] == cvr["raw_trace_hash"] == raw["hash"], "raw trace hash mismatch")
        _require(ledger["observer_adjudication_ref"] == oar["observer_adjudication_id"] and ledger["observer_adjudication_digest"] == oar["record_digest"], "ledger OAR binding mismatch")
        _require(ledger["capture_validity_record_ref"] == cvr["capture_validity_record_id"] and ledger["capture_validity_record_digest"] == cvr["record_digest"], "ledger CVR binding mismatch")
        refs = (("capability_manifest", ccm, "capability_manifest_id"), ("cir", cir, "cir_record_id"), ("observer_adjudication", oar, "observer_adjudication_id"), ("inventory", inventory, "inventory_record_id"))
        for prefix, target, id_field in refs:
            _require(cvr[f"{prefix}_ref"] == target[id_field] and cvr[f"{prefix}_digest"] == target["record_digest"], f"CVR {prefix} binding mismatch")
        status[capture_id] = cvr["capture_validity_status"]
    return status, current


def validate_capture_bundle(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Structural validation retains valid, invalid, and pending captures."""
    status, _ = _validate_bundle(bundle)
    return status


def validate_case_capture_identity(case_definitions: list[dict[str, Any]], bundle: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Bind each Capture to one declared Case without constructing a real Case."""
    cases = validate_case_definition_collection(case_definitions)
    status, current = _validate_bundle(bundle)
    for capture_id, ledger in current["ledger"].items():
        case = cases.get(ledger["case_id"])
        _require(case is not None, f"capture_id {capture_id} references an unknown case_id")
        for field in (
            "scenario_template_id", "fault_family", "fault_variant", "control_type",
            "injection_definition", "injection_version", "injection_parameters",
            "workload_seed", "config_hash",
        ):
            _require(ledger[field] == case[field], f"capture_id {capture_id} {field} does not match its Case")
        oar = current["oar"][capture_id]
        for field in ("manifestation_predicate_id", "manifestation_predicate_version"):
            _require(oar[field] == case[field], f"capture_id {capture_id} {field} does not match its Case")
    return status


def validate_scoring_eligibility(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Require the independently adjudicated material needed for scoring."""
    status, current = _validate_bundle(bundle)
    for capture_id, capture_status in status.items():
        cvr = current["cvr"][capture_id]
        cir = current["cir"][capture_id]
        oar = current["oar"][capture_id]
        raw = next(item for item in current["inventory"][capture_id]["artifacts"] if item["kind"] == "raw_trace")
        _require(capture_status == "valid", f"capture_id {capture_id} is not scoring eligible: {cvr['invalid_reason'] or capture_status}")
        _require(raw["availability"] == "available" and raw["hash"] is not None, "scoring eligibility requires available raw trace")
        _require(cir["integrity_status"] == "complete", "scoring eligibility requires complete CIR")
        _require(oar["observer_status"] == "complete", "scoring eligibility requires complete OAR")
    return status


def adapt_collector_config_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    """Normalize configuration only; no capture, result, or truth is inferred."""
    _require(isinstance(source, dict), "collector source must be an object")
    _forbid(source, {"sequence_gaps", "loss", "overflow", "overflow_count", "buffer_high_watermark", "truncation", "corruption", "fault_manifested", "manifestation_interval", "verdict"}, "collector source")
    collector = source.get("collector", source)
    _require(isinstance(collector, dict), "collector configuration must be an object")
    def field(name: str, default: Any = None) -> Any:
        return collector.get(name, source.get(name, default))
    config = {
        "event_types_enabled": field("event_types_enabled", field("event_types", [])), "task_filter": field("task_filter", field("task_filters", [])), "resource_filter": field("resource_filter", field("resource_filters", [])), "irq_filter": field("irq_filter", field("irq_filters", [])), "core_filter": field("core_filter", field("core_filters", [])), "sampling_configuration": field("sampling_configuration", {}), "buffer_capacity": field("buffer_capacity"), "flush_policy": field("flush_policy"), "timestamp_source": field("timestamp_source"), "clock_resolution": field("clock_resolution"), "payload_fields": field("payload_fields", []), "dictionary_version": field("dictionary_version"), "mapping_version": field("mapping_version"), "collector_version": field("collector_version"), "trace_start_boundary": field("trace_start_boundary"), "trace_end_boundary": field("trace_end_boundary"),
    }
    _require(all(value is not None for value in config.values()), "collector source lacks required configuration field")
    source_digest = sha256(canonical_json(source)).hexdigest()
    config_hash = sha256(canonical_json(config)).hexdigest()
    snapshot = {"schema_version": SCHEMA_VERSION, "config_snapshot_id": f"snapshot:{config_hash[:16]}", "source_format": str(source.get("source_format", "collector_config_v1")), "source_metadata_digest": source_digest, "adapter_version": "rtd-phase2-config-adapter-v1", "config_hash": config_hash, **config, "prior_snapshot_ref": None}
    return finalize_record(snapshot)


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    raw = path.read_bytes()
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        _require(bool(line), f"ledger contains blank line {line_no}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"ledger line {line_no} is not JSON") from error
        _require(isinstance(value, dict), f"ledger line {line_no} is not an object")
        _require(canonical_json(value).rstrip(b"\n") == line, f"ledger line {line_no} is not canonical JSON")
        rows.append(value)
    return rows, raw


@contextmanager
def _ledger_lock(path: Path) -> Iterable[None]:
    """Serialize compliant writers without making the ledger itself mutable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path.with_suffix(path.suffix + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def append_ledger_record(path: Path, value: dict[str, Any], *, seal_path: Path) -> dict[str, Any]:
    with _ledger_lock(path):
        _require(not seal_path.exists(), "ledger session is sealed")
        rows, _ = load_jsonl(path) if path.exists() else ([], b"")
        if rows:
            validate_ledger_stream(rows)
        record = deepcopy(value)
        record["append_sequence"] = len(rows) + 1
        record["previous_record_digest"] = rows[-1]["record_digest"] if rows else None
        record = finalize_record(record)
        validate_ledger_record(record)
        if rows:
            _require(record["session_id"] == rows[0]["session_id"], "ledger append session_id mismatch")
        validate_ledger_stream([*rows, record])
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, canonical_json(record))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return record


def seal_ledger_session(ledger_path: Path, seal_path: Path, *, timestamp: str, seal_record_id: str) -> dict[str, Any]:
    with _ledger_lock(ledger_path):
        _require(not seal_path.exists(), "session seal already exists")
        rows, raw = load_jsonl(ledger_path)
        validate_ledger_stream(rows)
        seal = {"schema_version": SCHEMA_VERSION, "seal_record_id": seal_record_id, "session_id": rows[0]["session_id"], "capture_count": len(rows), "first_append_sequence": rows[0]["append_sequence"], "last_append_sequence": rows[-1]["append_sequence"], "last_record_digest": rows[-1]["record_digest"], "ledger_file_sha256": sha256(raw).hexdigest(), "timestamp": timestamp, "prior_seal_ref": None}
        seal = finalize_record(seal)
        validate_seal(seal, rows, raw)
        seal_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(seal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            os.write(descriptor, canonical_json(seal))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(seal_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return seal

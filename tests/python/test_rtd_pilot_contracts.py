from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from spec.rtd_pilot_contracts import (
    SCHEMA_VERSION,
    ContractError,
    adapt_collector_config_snapshot,
    finalize_record,
    validate_capture_bundle,
    validate_capture_validity,
    validate_case_capture_identity,
    validate_case_definition_collection,
    validate_ccm,
    validate_cir,
    validate_ledger_record,
    validate_observer_record,
    validate_scoring_eligibility,
    validate_seal_collection,
    validate_snapshot_collection,
    validate_snapshot,
)
from spec.schema_loader import load_specs


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _record(value: dict) -> dict:
    value = dict(value)
    value.setdefault("schema_version", SCHEMA_VERSION)
    return finalize_record(value)


def bundle(
    *,
    capture_status: str = "valid",
    raw_available: bool = True,
    capture_id: str = "capture:one",
    case_id: str = "case:template",
    scenario_template_id: str = "scenario:template",
) -> dict[str, list[dict]]:
    raw_hash = HASH_B if raw_available else None
    ccm = _record({
        "capability_manifest_id": "ccm:one", "capture_id": capture_id, "session_id": "session:one",
        "config_snapshot_id": "snapshot:one", "config_snapshot_digest": HASH_A, "collector_config_hash": HASH_C,
        "event_types_enabled": ["TASK_SWITCH"], "task_filter": [], "resource_filter": [], "irq_filter": [], "core_filter": ["0"],
        "sampling_configuration": {"enabled": False}, "buffer_capacity": 64, "flush_policy": "drop_new",
        "timestamp_source": "counter", "clock_resolution": 1, "payload_fields": ["task_id"], "dictionary_version": "v1",
        "mapping_version": "v1", "collector_version": "v1", "trace_start_boundary": "case_start", "trace_end_boundary": "case_end", "prior_capability_ref": None,
    })
    cir = _record({
        "cir_record_id": "cir:one", "capture_id": capture_id, "session_id": "session:one", "capability_manifest_id": "ccm:one",
        "capability_manifest_digest": ccm["record_digest"], "raw_trace_id": "artifact:raw", "raw_trace_hash": raw_hash,
        "firmware_hash": HASH_A, "ELF_hash": HASH_B, "config_hash": HASH_C, "source_commit": "c5ea276", "immutable_input_refs": {},
        "sequence_continuity": "continuous", "sequence_gaps": [], "loss": False, "overflow": False, "overflow_count": 0,
        "buffer_high_watermark": 4, "truncation": False, "corruption": False, "alignment_degradation": False, "mapping_mismatch": False,
        "observer_loss": False, "affected_intervals": [], "affected_channels": [], "affected_entities": [], "natural_overflow": False,
        "integrity_status": "complete", "reason_codes": [], "prior_cir_ref": None,
    })
    oar = _record({
        "observer_adjudication_id": "oar:one", "case_id": case_id, "capture_id": capture_id, "session_id": "session:one",
        "observer_artifact_hash": HASH_D, "observer_status": "complete", "manifestation_status": "not_manifested", "manifestation_interval": None,
        "manifestation_predicate_id": "predicate:template", "manifestation_predicate_version": "1.0", "alignment_error_bound": 0,
        "adjudicator": "adjudicator:synthetic", "adjudication_timestamp": "2026-07-30T00:00:00Z", "prior_adjudication_ref": None,
    })
    inventory = _record({
        "inventory_record_id": "inventory:one", "capture_id": capture_id, "session_id": "session:one",
        "artifacts": [{"artifact_id": "artifact:raw", "kind": "raw_trace", "availability": "available" if raw_available else "missing", "size": 1 if raw_available else 0, "hash": raw_hash,
                       "logical_name": "raw/capture.trace", "storage_class": "retained", "retention": "retain"}], "prior_inventory_ref": None,
    })
    cvr = _record({
        "capture_validity_record_id": "cvr:one", "case_id": case_id, "capture_id": capture_id, "session_id": "session:one",
        "capability_manifest_ref": ccm["capability_manifest_id"], "capability_manifest_digest": ccm["record_digest"], "cir_ref": cir["cir_record_id"], "cir_digest": cir["record_digest"],
        "observer_adjudication_ref": oar["observer_adjudication_id"], "observer_adjudication_digest": oar["record_digest"], "inventory_ref": inventory["inventory_record_id"], "inventory_digest": inventory["record_digest"],
        "raw_trace_id": "artifact:raw", "raw_trace_hash": raw_hash, "capture_validity_status": capture_status,
        "invalid_reason": "manual_adjudication" if capture_status == "invalid" else None, "quality_rule_id": "quality:v1", "quality_rule_version": "1.0",
        "quality_adjudicator": "adjudicator:quality", "adjudication_timestamp": "2026-07-30T00:00:00Z", "prior_validity_ref": None,
    })
    ledger = _record({
        "ledger_record_id": "ledger:one", "append_sequence": 1, "previous_record_digest": None,
        "case_id": case_id, "scenario_template_id": scenario_template_id, "fault_family": "template", "fault_variant": "template", "control_type": "healthy_control", "board_id": "board:future",
        "session_id": "session:one", "capture_id": capture_id, "firmware_hash": HASH_A, "ELF_hash": HASH_B, "config_hash": HASH_C, "source_commit": "c5ea276", "RTOS_name": "FreeRTOS", "RTOS_version": "10.3.1", "compiler": "arm-none-eabi-gcc", "compiler_flags": "-O2", "workload_seed": "seed:one", "injection_definition": "definition:template", "injection_version": "1.0", "injection_parameters": {}, "collector_config_hash": HASH_C,
        "injection_status": "not_applicable", "injection_enable_epoch": None, "observer_adjudication_ref": oar["observer_adjudication_id"], "observer_adjudication_digest": oar["record_digest"], "capture_validity_record_ref": cvr["capture_validity_record_id"], "capture_validity_record_digest": cvr["record_digest"], "operator": "operator:synthetic", "timestamp": "2026-07-30T00:00:00Z", "ledger_version": "1.0", "prior_ledger_ref": None,
    })
    return {"ledger": [ledger], "ccm": [ccm], "cir": [cir], "oar": [oar], "inventory": [inventory], "cvr": [cvr]}


def case_definition(
    *,
    case_id: str = "case:template",
    scenario_template_id: str = "scenario:template",
) -> dict:
    return _record({
        "case_definition_id": "case-definition:one", "case_id": case_id, "scenario_template_id": scenario_template_id,
        "fault_family": "template", "fault_variant": "template", "control_type": "healthy_control", "injection_definition": "definition:template",
        "injection_version": "1.0", "injection_parameters": {}, "entity_binding": {}, "workload_seed": "seed:one",
        "manifestation_predicate_id": "predicate:template", "manifestation_predicate_version": "1.0", "config_snapshot_id": "snapshot:one",
        "config_hash": HASH_C, "example_only": True, "prior_case_definition_ref": None,
    })


def non_bundle_version_record(kind: str) -> tuple[dict, str, str, object]:
    if kind == "case":
        value = case_definition(case_id="case:one", scenario_template_id="scenario:one")
        value["control_type"] = "fault"
        value["injection_definition"] = "definition:one"
        value["manifestation_predicate_id"] = "predicate:one"
        value["config_hash"] = HASH_A
        return finalize_record(value), "case_definition_id", "prior_case_definition_ref", validate_case_definition_collection
    if kind == "snapshot":
        return _record({
            "config_snapshot_id": "snapshot:one", "source_format": "collector_config_v1", "source_metadata_digest": HASH_A,
            "adapter_version": "adapter:v1", "config_hash": HASH_B, "event_types_enabled": ["TASK_SWITCH"], "task_filter": [],
            "resource_filter": [], "irq_filter": [], "core_filter": ["0"], "sampling_configuration": {"enabled": False},
            "buffer_capacity": 64, "flush_policy": "drop_new", "timestamp_source": "counter", "clock_resolution": 1,
            "payload_fields": ["task_id"], "dictionary_version": "v1", "mapping_version": "v1", "collector_version": "v1",
            "trace_start_boundary": "case_start", "trace_end_boundary": "case_end", "prior_snapshot_ref": None,
        }), "config_snapshot_id", "prior_snapshot_ref", validate_snapshot_collection
    return _record({
        "seal_record_id": "seal:one", "session_id": "session:one", "capture_count": 1, "first_append_sequence": 1,
        "last_append_sequence": 1, "last_record_digest": HASH_A, "ledger_file_sha256": HASH_B,
        "timestamp": "2026-07-30T00:00:00Z", "prior_seal_ref": None,
    }), "seal_record_id", "prior_seal_ref", validate_seal_collection


def test_schema_mirrors_and_registration() -> None:
    root = Path(__file__).resolve().parents[2]
    names = ["injection_ledger", "ledger_session_seal", "collector_config_snapshot", "capture_capability_manifest", "capture_integrity_record", "case_definition", "observer_record", "raw_artifact_inventory", "capture_validity_record"]
    specs = load_specs()
    for name in names:
        filename = f"rtd_{name}.schema.json"
        assert (root / "spec/schema" / filename).read_bytes() == (root / "spec/assets/schema" / filename).read_bytes()
        assert f"rtd_{name}" in specs


def test_structural_bundle_and_scoring_eligibility() -> None:
    contract = bundle()
    assert validate_capture_bundle(contract) == {"capture:one": "valid"}
    assert validate_scoring_eligibility(contract) == {"capture:one": "valid"}


@pytest.mark.parametrize("field, value", [
    ("fault_manifested", False), ("manifestation_status", "not_manifested"), ("manifestation_interval", None), ("observer_status", "complete"),
    ("capture_validity_status", "valid"), ("validity_status", "valid"), ("invalid_capture", False), ("invalid_reason", None),
    ("raw_trace_hash", HASH_A), ("observer_hash", HASH_A), ("verdict", "pass"),
])
def test_ledger_rejects_truth_and_quality_fields(field: str, value: object) -> None:
    row = bundle()["ledger"][0]
    row[field] = value
    with pytest.raises(ContractError, match="forbidden|schema invalid"):
        validate_ledger_record(finalize_record(row))


@pytest.mark.parametrize("status, interval", [("manifested", {"start": 1, "end": 2}), ("not_manifested", None), ("not_assessable", None)])
def test_oar_adjudicates_all_manifestation_states(status: str, interval: object) -> None:
    oar = bundle()["oar"][0]
    oar["manifestation_status"] = status
    oar["manifestation_interval"] = interval
    validate_observer_record(finalize_record(oar))


@pytest.mark.parametrize("status", ["valid", "invalid", "pending_adjudication"])
def test_cvr_has_the_only_capture_validity_status(status: str) -> None:
    cvr = bundle()["cvr"][0]
    cvr["capture_validity_status"] = status
    cvr["invalid_reason"] = "manual_adjudication" if status == "invalid" else None
    validate_capture_validity(finalize_record(cvr))


@pytest.mark.parametrize("status", ["invalid", "pending_adjudication"])
def test_invalid_or_pending_capture_is_structural_but_not_scoring_eligible(status: str) -> None:
    contract = bundle(capture_status=status, raw_available=status == "pending_adjudication")
    assert validate_capture_bundle(contract) == {"capture:one": status}
    with pytest.raises(ContractError, match="not scoring eligible"):
        validate_scoring_eligibility(contract)


def test_complete_cir_cannot_hide_degradation() -> None:
    cir = bundle()["cir"][0]
    cir["overflow"] = True
    cir["overflow_count"] = 1
    with pytest.raises(ContractError, match="complete CIR cannot report degradation"):
        validate_cir(finalize_record(cir))


def test_current_version_is_the_unique_unreferenced_leaf() -> None:
    contract = bundle()
    old = contract["cvr"][0]
    latest = deepcopy(old)
    latest["capture_validity_record_id"] = "cvr:two"
    latest["prior_validity_ref"] = old["record_digest"]
    latest = finalize_record(latest)
    contract["cvr"].append(latest)
    ledger = contract["ledger"][0]
    ledger["capture_validity_record_ref"] = latest["capture_validity_record_id"]
    ledger["capture_validity_record_digest"] = latest["record_digest"]
    contract["ledger"][0] = finalize_record(ledger)
    assert validate_capture_bundle(contract) == {"capture:one": "valid"}


@pytest.mark.parametrize("kind", ["case", "snapshot", "seal"])
def test_non_bundle_version_collections_require_one_current_leaf(kind: str) -> None:
    first, id_field, prior_field, validator = non_bundle_version_record(kind)
    latest = deepcopy(first)
    latest[id_field] = f"{kind}:two"
    latest[prior_field] = first["record_digest"]
    latest = finalize_record(latest)
    assert list(validator([first, latest]).values()) == [latest]

    competing = deepcopy(first)
    competing[id_field] = f"{kind}:three"
    competing = finalize_record(competing)
    with pytest.raises(ContractError, match="one current/latest"):
        validator([first, latest, competing])


@pytest.mark.parametrize("mutation, error", [("dangling", "dangling"), ("fork", "forked"), ("multiple", "one current/latest")])
def test_invalid_cvr_history_is_rejected(mutation: str, error: str) -> None:
    contract = bundle()
    first = contract["cvr"][0]
    second = deepcopy(first)
    second["capture_validity_record_id"] = "cvr:two"
    second["prior_validity_ref"] = "a" * 64 if mutation == "dangling" else first["record_digest"]
    second = finalize_record(second)
    contract["cvr"].append(second)
    if mutation == "multiple":
        second["prior_validity_ref"] = None
        contract["cvr"][1] = finalize_record(second)
    if mutation == "fork":
        third = deepcopy(second)
        third["capture_validity_record_id"] = "cvr:three"
        contract["cvr"].append(finalize_record(third))
    with pytest.raises(ContractError, match=error):
        validate_capture_bundle(contract)


def test_oar_cvr_ccm_cir_boundaries_are_not_interchangeable() -> None:
    capability = bundle()["ccm"][0]
    capability["overflow_count"] = 1
    with pytest.raises(ContractError, match="schema invalid"):
        validate_ccm(finalize_record(capability))
    integrity = bundle()["cir"][0]
    integrity["manifestation_status"] = "manifested"
    with pytest.raises(ContractError, match="schema invalid"):
        validate_cir(finalize_record(integrity))
    cvr = bundle()["cvr"][0]
    cvr["observer_adjudication_ref"] = "cir:one"
    cvr = finalize_record(cvr)
    contract = bundle()
    contract["cvr"][0] = cvr
    contract["ledger"][0]["capture_validity_record_ref"] = cvr["capture_validity_record_id"]
    contract["ledger"][0]["capture_validity_record_digest"] = cvr["record_digest"]
    contract["ledger"][0] = finalize_record(contract["ledger"][0])
    with pytest.raises(ContractError, match="CVR observer_adjudication binding mismatch"):
        validate_capture_bundle(contract)
    oar = bundle()["oar"][0]
    oar["capture_validity_status"] = "valid"
    with pytest.raises(ContractError, match="forbidden|schema invalid"):
        validate_observer_record(finalize_record(oar))
    cvr = bundle()["cvr"][0]
    cvr["manifestation_status"] = "manifested"
    with pytest.raises(ContractError, match="forbidden|schema invalid"):
        validate_capture_validity(finalize_record(cvr))


def test_snapshot_adapter_is_schema_valid_and_capture_free() -> None:
    source = {"event_types_enabled": ["TASK_SWITCH"], "task_filter": [], "resource_filter": [], "irq_filter": [], "core_filter": ["0"], "sampling_configuration": {"enabled": False}, "buffer_capacity": 64, "flush_policy": "drop_new", "timestamp_source": "counter", "clock_resolution": 1, "payload_fields": ["task_id"], "dictionary_version": "v1", "mapping_version": "v1", "collector_version": "v1", "trace_start_boundary": "case_start", "trace_end_boundary": "case_end"}
    snapshot = adapt_collector_config_snapshot(source)
    validate_snapshot(snapshot)
    assert "capture_id" not in snapshot


def test_case_identity_contract_accepts_example_and_multiple_independent_captures() -> None:
    definitions = [case_definition()]
    assert validate_case_capture_identity(definitions, bundle()) == {"capture:one": "valid"}
    assert validate_case_capture_identity(definitions, bundle(capture_id="capture:two")) == {"capture:two": "valid"}

    first = bundle()
    second = bundle(capture_id="capture:two")
    ledger = second["ledger"][0]
    ledger["ledger_record_id"] = "ledger:two"
    ledger["append_sequence"] = 2
    ledger["previous_record_digest"] = first["ledger"][0]["record_digest"]
    second["ledger"][0] = finalize_record(ledger)
    combined = {kind: [*first[kind], *second[kind]] for kind in first}
    assert validate_case_capture_identity(definitions, combined) == {"capture:one": "valid", "capture:two": "valid"}


def test_case_identity_rejects_duplicate_case_id() -> None:
    first = case_definition()
    duplicate = deepcopy(first)
    duplicate["case_definition_id"] = "case-definition:two"
    duplicate = finalize_record(duplicate)
    with pytest.raises(ContractError, match="one current/latest"):
        validate_case_definition_collection([first, duplicate])


@pytest.mark.parametrize("field, value", [
    ("fault_family", "other"),
    ("control_type", "fault"),
    ("injection_parameters", {"kind": "other"}),
    ("entity_binding", {"task": "other"}),
    ("workload_seed", "seed:other"),
    ("manifestation_predicate_id", "predicate:other"),
    ("manifestation_predicate_version", "2.0"),
    ("config_hash", HASH_A),
])
def test_case_identity_rejects_reuse_after_identity_change(field: str, value: object) -> None:
    first = case_definition()
    changed = deepcopy(first)
    changed["case_definition_id"] = "case-definition:two"
    changed[field] = value
    changed["prior_case_definition_ref"] = first["record_digest"]
    changed = finalize_record(changed)
    with pytest.raises(ContractError, match="identity-defining configuration changed"):
        validate_case_definition_collection([first, changed])


def test_case_identity_rejects_cross_template_capture_binding() -> None:
    with pytest.raises(ContractError, match="scenario_template_id"):
        validate_case_capture_identity([case_definition(scenario_template_id="scenario:other")], bundle())


@pytest.mark.parametrize("field, value", [
    ("fault_family", "other"),
    ("control_type", "fault"),
    ("injection_parameters", {"kind": "other"}),
    ("workload_seed", "seed:other"),
    ("config_hash", HASH_A),
])
def test_case_identity_rejects_ledger_identity_mismatch(field: str, value: object) -> None:
    contract = bundle()
    ledger = contract["ledger"][0]
    ledger[field] = value
    contract["ledger"][0] = finalize_record(ledger)
    with pytest.raises(ContractError, match=field):
        validate_case_capture_identity([case_definition()], contract)


@pytest.mark.parametrize("field, value", [
    ("manifestation_predicate_id", "predicate:other"),
    ("manifestation_predicate_version", "2.0"),
])
def test_case_identity_rejects_oar_predicate_mismatch(field: str, value: str) -> None:
    contract = bundle()
    oar = contract["oar"][0]
    oar[field] = value
    contract["oar"][0] = finalize_record(oar)
    cvr = contract["cvr"][0]
    cvr["observer_adjudication_digest"] = contract["oar"][0]["record_digest"]
    contract["cvr"][0] = finalize_record(cvr)
    ledger = contract["ledger"][0]
    ledger["observer_adjudication_digest"] = contract["oar"][0]["record_digest"]
    ledger["capture_validity_record_digest"] = contract["cvr"][0]["record_digest"]
    contract["ledger"][0] = finalize_record(ledger)
    with pytest.raises(ContractError, match=field):
        validate_case_capture_identity([case_definition()], contract)


@pytest.mark.parametrize("case_id, scenario_template_id, capture_id", [
    ("case:bad/id", "scenario:template", "capture:one"),
    ("case:template", "scenario:bad/id", "capture:one"),
    ("case:template", "scenario:template", "capture:bad/id"),
])
def test_case_identity_rejects_invalid_identifier_formats(case_id: str, scenario_template_id: str, capture_id: str) -> None:
    definitions = [case_definition(case_id=case_id, scenario_template_id=scenario_template_id)]
    contract = bundle(case_id=case_id, scenario_template_id=scenario_template_id, capture_id=capture_id)
    with pytest.raises(ContractError, match="schema invalid|invalid format"):
        validate_case_capture_identity(definitions, contract)


def test_case_identity_rejects_capture_rebound_to_another_case() -> None:
    contract = bundle()
    rebound = deepcopy(contract["ledger"][0])
    rebound["ledger_record_id"] = "ledger:two"
    rebound["append_sequence"] = 2
    rebound["previous_record_digest"] = contract["ledger"][0]["record_digest"]
    rebound["prior_ledger_ref"] = contract["ledger"][0]["record_digest"]
    rebound["case_id"] = "case:other"
    rebound["scenario_template_id"] = "scenario:other"
    contract["ledger"].append(finalize_record(rebound))
    with pytest.raises(ContractError, match="multiple Case IDs"):
        validate_capture_bundle(contract)


def test_case_identity_rejects_real_case_masquerading_as_an_example() -> None:
    disguised = case_definition()
    disguised["example_only"] = False
    with pytest.raises(ContractError, match="real Case definition is forbidden"):
        validate_case_definition_collection([finalize_record(disguised)])

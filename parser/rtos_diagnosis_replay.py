"""Deterministic, synthetic-only P6.3 diagnosis replay validation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable


RESULT_SCHEMA_VERSION = "rtos-diagnosis-replay-v1"
EQUIVALENCE_SCOPE = "metadata_only"
REPLAY_STATUSES = frozenset({"pass", "fail", "reference_only", "not_evaluated"})
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "proof_hash_input",
        "proof_digest_write_path",
        "raw_proof_digest_path",
        "llm_truth",
        "advisor_truth",
        "api_key",
        "token",
        "secret",
        "password",
    }
)

# Fixed synthetic predicates are deliberately limited to the frozen P6.2 suite.
CASE_PROFILES: dict[str, dict[str, Any]] = {
    "priority_inversion": {
        "marker": "marker=candidate_hold_extended",
        "baseline_marker": "marker=baseline_hold_short",
        "affected_entity": {"entity_kind": "task", "entity_id": "task_high_priority", "resource_id": "resource_shared_lock", "task_id": "task_high_priority"},
    },
    "irq_latency_spike": {
        "marker": "marker=candidate_latency_spike",
        "baseline_marker": "marker=baseline_latency_nominal",
        "affected_entity": {"entity_kind": "irq", "entity_id": "irq_timer_tick", "irq_id": "irq_timer_tick"},
    },
    "mutex_hold_inflation": {
        "marker": "marker=candidate_mutex_inflated",
        "baseline_marker": "marker=baseline_mutex_window",
        "affected_entity": {"entity_kind": "mutex", "entity_id": "mutex_control_path", "resource_id": "mutex_control_path"},
    },
    "queue_wait_backlog": {
        "marker": "marker=candidate_backlog_persists",
        "baseline_marker": "marker=baseline_depth_flat",
        "affected_entity": {"entity_kind": "queue", "entity_id": "queue_sensor_events", "resource_id": "queue_sensor_events"},
    },
    "task_starvation": {
        "marker": "marker=candidate_starved_interval",
        "baseline_marker": "marker=baseline_schedulable",
        "affected_entity": {"entity_kind": "task", "entity_id": "task_background_worker", "task_id": "task_background_worker"},
    },
    "corrupt_segment": {
        "marker": "marker=candidate_segment_corrupt",
        "baseline_marker": "marker=baseline_segment_clean",
        "companion_path": "segments/segment_0007.txt",
        "companion_token": "status=corrupt",
        "affected_entity": {"entity_kind": "trace_segment", "entity_id": "segment_0007"},
    },
    "stale_sidecar": {
        "marker": "marker=candidate_sidecar_stale",
        "baseline_marker": "marker=baseline_sidecar_fresh",
        "companion_path": "sidecar/candidate_index.txt",
        "companion_token": "status=stale_sidecar_mismatch",
        "affected_entity": {"entity_kind": "sidecar", "entity_id": "sidecar_candidate_index"},
    },
    "missing_calibration": {
        "marker": "marker=candidate_calibration_missing",
        "baseline_marker": "marker=baseline_calibration_present",
        "companion_path": "notes/calibration_status.txt",
        "companion_token": "status=missing_calibration_detected",
        "affected_entity": {"entity_kind": "calibration", "entity_id": "clock_calibration"},
    },
}


def load_suite(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate a P6.2 suite without changing it."""
    suite = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_suite(suite)
    return suite


def validate_suite(suite: dict[str, Any]) -> None:
    if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list):
        raise ValueError("suite must contain a cases list")
    kinds = [str(case.get("case_kind") or "") for case in suite["cases"] if isinstance(case, dict)]
    if len(kinds) != len(CASE_PROFILES) or set(kinds) != set(CASE_PROFILES):
        raise ValueError("suite must contain exactly the supported synthetic case kinds")
    for case in suite["cases"]:
        _validate_case(case)


def companion_root_for_suite(suite_path: str | Path) -> Path:
    path = Path(suite_path)
    return path.parent / f"{path.stem}_artifacts"


def build_bounded_representation(case: dict[str, Any], companion_root: str | Path) -> dict[str, Any]:
    """Build the intentionally small representation used by replay reopen."""
    _validate_case(case)
    records = _bounded_source_records(case["case_kind"], _source_records(case, companion_root))
    return {
        "case_id": case["case_id"],
        "case_kind": case["case_kind"],
        "source_records": records,
        "evidence_refs": _required_refs(case),
        "closure_mode": case["expected_closure_mode"],
        "source_record_checksum": _records_checksum(records),
    }


def full_input_diagnosis(case: dict[str, Any], companion_root: str | Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Produce the fixed full-input synthetic diagnosis or an evaluation reason."""
    try:
        _validate_case(case)
        records = _source_records(case, companion_root)
    except (OSError, ValueError) as error:
        return None, [f"input_unavailable:{error}"]
    diagnosis, reasons = _diagnosis_from_records(case["case_kind"], records, _required_refs(case), case["expected_closure_mode"])
    if diagnosis is not None:
        diagnosis["case_id"] = case["case_id"]
    return diagnosis, reasons


def reopen_representation(representation: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Reopen solely from the bounded representation and fixed synthetic predicate."""
    try:
        case_kind = str(representation["case_kind"])
        case_id = str(representation["case_id"])
        records = dict(representation["source_records"])
        refs = list(representation["evidence_refs"])
        closure_mode = str(representation["closure_mode"])
    except (KeyError, TypeError, ValueError) as error:
        return None, [f"representation_invalid:{error}"]
    if not case_id or case_kind not in CASE_PROFILES:
        return None, ["representation_invalid:unsupported_case"]
    if not _valid_source_records(records):
        return None, ["representation_invalid:missing_source_records"]
    if str(representation.get("source_record_checksum") or "") != _records_checksum(records):
        return None, ["representation_invalid:source_record_checksum"]
    if not _valid_refs(refs):
        return None, ["representation_invalid:evidence_refs"]
    diagnosis, reasons = _diagnosis_from_records(case_kind, records, refs, closure_mode)
    if diagnosis is not None:
        diagnosis["case_id"] = case_id
    return diagnosis, reasons


def compare_diagnoses(
    full_diagnosis: dict[str, Any] | None,
    replay_diagnosis: dict[str, Any] | None,
    *,
    full_proof_facts: Iterable[Any] = (),
    replay_proof_facts: Iterable[Any] = (),
) -> dict[str, Any]:
    """Compare only declared replay metadata; supplied facts are never returned."""
    if full_diagnosis is None or replay_diagnosis is None:
        return {
            "diagnosis_preserved": False,
            "case_id_retained": False,
            "root_cause_retained": False,
            "affected_entity_retained": False,
            "evidence_refs_expected": [],
            "evidence_refs_retained": [],
            "evidence_retention_ratio": 0.0,
            "closure_retained": False,
            "deterministic_signal_retained": False,
            "proof_drift_count": _proof_drift_count(full_proof_facts, replay_proof_facts),
        }
    expected_refs = _ref_keys(full_diagnosis.get("evidence_refs") or [])
    retained_refs = sorted(expected_refs & _ref_keys(replay_diagnosis.get("evidence_refs") or []))
    case_id_retained = bool(full_diagnosis.get("case_id")) and full_diagnosis.get("case_id") == replay_diagnosis.get("case_id")
    root_cause_retained = full_diagnosis.get("root_cause") == replay_diagnosis.get("root_cause")
    affected_entity_retained = _affected_entity_retained(
        dict(full_diagnosis.get("affected_entity") or {}), dict(replay_diagnosis.get("affected_entity") or {})
    )
    closure_retained = full_diagnosis.get("closure_mode") == replay_diagnosis.get("closure_mode")
    signal_retained = set(full_diagnosis.get("deterministic_signal_identifiers") or []) == set(
        replay_diagnosis.get("deterministic_signal_identifiers") or []
    )
    ratio = 1.0 if not expected_refs else len(retained_refs) / len(expected_refs)
    return {
        "diagnosis_preserved": bool(case_id_retained and root_cause_retained and affected_entity_retained and signal_retained),
        "case_id_retained": case_id_retained,
        "root_cause_retained": root_cause_retained,
        "affected_entity_retained": affected_entity_retained,
        "evidence_refs_expected": sorted(expected_refs),
        "evidence_refs_retained": retained_refs,
        "evidence_retention_ratio": float(ratio),
        "closure_retained": closure_retained,
        "deterministic_signal_retained": signal_retained,
        "proof_drift_count": _proof_drift_count(full_proof_facts, replay_proof_facts),
    }


def run_replay_case(
    case: dict[str, Any],
    companion_root: str | Path,
    *,
    representation: dict[str, Any] | None = None,
    full_proof_facts: Iterable[Any] = (),
    replay_proof_facts: Iterable[Any] = (),
) -> dict[str, Any]:
    """Validate one synthetic case. A caller can supply a damaged representation for testing."""
    full_diagnosis, full_reasons = full_input_diagnosis(case, companion_root)
    if full_diagnosis is None:
        return _not_evaluated_result(case, full_reasons)
    if representation is None:
        try:
            representation = build_bounded_representation(case, companion_root)
        except (OSError, ValueError) as error:
            return _not_evaluated_result(case, [f"representation_unavailable:{error}"])
    replay_diagnosis, replay_reasons = reopen_representation(representation)
    comparison = compare_diagnoses(
        full_diagnosis,
        replay_diagnosis,
        full_proof_facts=full_proof_facts,
        replay_proof_facts=replay_proof_facts,
    )
    complete = bool(
        comparison["diagnosis_preserved"]
        and comparison["evidence_retention_ratio"] == 1.0
        and comparison["closure_retained"]
        and comparison["proof_drift_count"] == 0
    )
    closure_mode = str((replay_diagnosis or {}).get("closure_mode") or "")
    if complete and closure_mode == "reference_only":
        replay_status = "reference_only"
    elif complete:
        replay_status = "pass"
    else:
        replay_status = "fail"
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "case_id": case["case_id"],
        "case_kind": case["case_kind"],
        "validation_mode": "synthetic_replay",
        "full_diagnosis": full_diagnosis,
        "replay_diagnosis": replay_diagnosis,
        **comparison,
        "closure_mode": closure_mode or None,
        "equivalence_scope": EQUIVALENCE_SCOPE,
        "replay_status": replay_status,
        "replay_pass": replay_status == "pass",
        "reference_only": replay_status == "reference_only",
        "claim_class": "report_only",
        "failure_reasons": list(replay_reasons),
        "notes": ["Synthetic fixture metadata replay only; not a real RTOS correctness claim."],
    }
    _validate_result(result)
    return result


def run_replay_suite(suite: dict[str, Any], companion_root: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validate_suite(suite)
    results = [run_replay_case(case, companion_root) for case in suite["cases"]]
    return results, summarize_results(results)


def summarize_results(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(results)
    counts = {status: sum(row.get("replay_status") == status for row in rows) for status in sorted(REPLAY_STATUSES)}
    total_refs = sum(len(row.get("evidence_refs_expected") or []) for row in rows)
    retained_refs = sum(len(row.get("evidence_refs_retained") or []) for row in rows)
    return {
        "cases_total": len(rows),
        "pass_count": counts["pass"],
        "fail_count": counts["fail"],
        "reference_only_count": counts["reference_only"],
        "not_evaluated_count": counts["not_evaluated"],
        "proof_drift_count": sum(int(row.get("proof_drift_count") or 0) for row in rows),
        "evidence_retention_ratio": 1.0 if total_refs == 0 else retained_refs / total_refs,
        "all_required_cases_evaluated": all(row.get("replay_status") != "not_evaluated" for row in rows),
        "all_passed": bool(rows) and all(row.get("replay_status") == "pass" for row in rows),
        "claim_class": "report_only",
    }


def _validate_case(case: Any) -> None:
    if not isinstance(case, dict):
        raise ValueError("case must be an object")
    required = ("case_id", "case_kind", "expected_root_cause", "expected_affected_entity", "expected_evidence_refs", "expected_closure_mode")
    if any(not case.get(field) for field in required) or case.get("case_kind") not in CASE_PROFILES:
        raise ValueError("case lacks required synthetic replay metadata")


def _source_records(case: dict[str, Any], companion_root: str | Path) -> dict[str, list[str]]:
    case_kind = str(case["case_kind"])
    root = Path(companion_root) / case_kind
    records = {
        "baseline_trace": _trace_records(root / "baseline.trace"),
        "candidate_trace": _trace_records(root / "candidate.trace"),
    }
    companion_path = CASE_PROFILES[case_kind].get("companion_path")
    if companion_path:
        records["required_companion"] = _text_records(root / str(companion_path))
    return records


def _trace_records(path: Path) -> list[str]:
    records = [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    if not records:
        raise ValueError(f"trace has no source records: {path.name}")
    return records


def _text_records(path: Path) -> list[str]:
    records = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not records:
        raise ValueError(f"companion has no source records: {path.name}")
    return records


def _bounded_source_records(case_kind: str, full_records: dict[str, list[str]]) -> dict[str, list[str]]:
    """Keep only the fixed predicate signals, never the complete synthetic traces."""
    profile = CASE_PROFILES[case_kind]
    records = {
        "baseline_trace": [_matching_record(full_records["baseline_trace"], str(profile["baseline_marker"]))],
        "candidate_trace": [_matching_record(full_records["candidate_trace"], str(profile["marker"]))],
    }
    if profile.get("companion_token"):
        records["required_companion"] = [
            _matching_record(full_records.get("required_companion", []), str(profile["companion_token"]))
        ]
    return records


def _matching_record(records: list[str], required_signal: str) -> str:
    for record in records:
        if required_signal in record:
            return record
    raise ValueError(f"required predicate signal is absent: {required_signal}")


def _required_refs(case: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"ref_kind": str(ref["ref_kind"]), "ref_id": str(ref["ref_id"])}
        for ref in case["expected_evidence_refs"]
        if isinstance(ref, dict) and bool(ref.get("required")) and ref.get("ref_kind") and ref.get("ref_id")
    ]


def _diagnosis_from_records(
    case_kind: str, records: dict[str, list[str]], refs: list[dict[str, Any]], closure_mode: str
) -> tuple[dict[str, Any] | None, list[str]]:
    profile = CASE_PROFILES.get(case_kind)
    if profile is None:
        return None, ["case_predicate_unavailable"]
    marker = str(profile["marker"])
    baseline_marker = str(profile["baseline_marker"])
    if not any(marker in record for record in records["candidate_trace"]):
        return None, ["case_predicate_not_satisfied"]
    if not any(baseline_marker in record for record in records["baseline_trace"]):
        return None, ["case_predicate_not_satisfied"]
    companion_token = profile.get("companion_token")
    if companion_token and not any(str(companion_token) in record for record in records.get("required_companion", [])):
        return None, ["case_predicate_not_satisfied"]
    if not _valid_refs(refs):
        return None, ["representation_invalid:evidence_refs"]
    return {
        "root_cause": {"root_cause_id": f"root_{case_kind}", "root_cause_kind": case_kind},
        "affected_entity": dict(profile["affected_entity"]),
        "evidence_refs": [{"ref_kind": str(ref["ref_kind"]), "ref_id": str(ref["ref_id"])} for ref in refs],
        "closure_mode": closure_mode,
        "deterministic_signal_identifiers": [baseline_marker, marker, *([str(companion_token)] if companion_token else [])],
    }, []


def _records_checksum(records: dict[str, list[str]]) -> str:
    return sha256(json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _valid_source_records(records: dict[str, Any]) -> bool:
    required = {"baseline_trace", "candidate_trace"}
    if not required.issubset(records):
        return False
    return all(
        isinstance(rows, list) and bool(rows) and all(isinstance(record, str) and record for record in rows)
        for rows in records.values()
    )


def _valid_refs(refs: list[Any]) -> bool:
    return bool(refs) and all(isinstance(ref, dict) and str(ref.get("ref_kind") or "") and str(ref.get("ref_id") or "") for ref in refs)


def _ref_keys(refs: list[dict[str, Any]]) -> set[str]:
    return {f"{ref.get('ref_kind')}:{ref.get('ref_id')}" for ref in refs if _valid_refs([ref])}


def _affected_entity_retained(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    required_keys = ["entity_kind", "entity_id"] + [key for key in ("resource_id", "irq_id", "task_id") if expected.get(key)]
    return all(expected.get(key) == actual.get(key) and bool(actual.get(key)) for key in required_keys)


def _proof_drift_count(full_facts: Iterable[Any], replay_facts: Iterable[Any]) -> int:
    full_rows = [json.dumps(fact, ensure_ascii=True, sort_keys=True, separators=(",", ":")) for fact in full_facts]
    replay_rows = [json.dumps(fact, ensure_ascii=True, sort_keys=True, separators=(",", ":")) for fact in replay_facts]
    return len(set(full_rows).symmetric_difference(replay_rows))


def _not_evaluated_result(case: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "case_id": str(case.get("case_id") or ""),
        "case_kind": str(case.get("case_kind") or ""),
        "validation_mode": "synthetic_replay",
        "full_diagnosis": None,
        "replay_diagnosis": None,
        "diagnosis_preserved": False,
        "case_id_retained": False,
        "root_cause_retained": False,
        "affected_entity_retained": False,
        "evidence_refs_expected": [],
        "evidence_refs_retained": [],
        "evidence_retention_ratio": 0.0,
        "closure_mode": None,
        "closure_retained": False,
        "deterministic_signal_retained": False,
        "equivalence_scope": EQUIVALENCE_SCOPE,
        "replay_status": "not_evaluated",
        "replay_pass": False,
        "reference_only": False,
        "proof_drift_count": 0,
        "claim_class": "not_claimable",
        "failure_reasons": list(reasons),
        "notes": ["Synthetic fixture metadata replay could not be evaluated."],
    }
    _validate_result(result)
    return result


def _validate_result(result: dict[str, Any]) -> None:
    if result["replay_status"] not in REPLAY_STATUSES:
        raise ValueError("invalid replay status")
    if result["reference_only"] or result["replay_status"] == "not_evaluated":
        if result["replay_pass"]:
            raise ValueError("non-pass status cannot be a replay pass")
    if result["replay_pass"] and int(result["proof_drift_count"]) != 0:
        raise ValueError("proof drift fails replay closed")
    _assert_no_forbidden_fields(result)


def _assert_no_forbidden_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_FIELD_NAMES:
                raise ValueError(f"forbidden output field: {key}")
            _assert_no_forbidden_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_fields(child)

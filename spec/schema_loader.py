from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DICTIONARY_PATH = ROOT / "dictionary" / "event_dictionary.json"
SCHEMA_DIR = ROOT / "schema"
ASSET_SCHEMA_DIR = ROOT / "assets" / "schema"


class SpecError(RuntimeError):
    """Raised when frozen spec assets are missing or malformed."""

    def __init__(self, message: str, *, code: str = "SPEC_ERROR") -> None:
        super().__init__(message)
        self.code = code


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SpecError(f"spec asset missing: {path}", code="ASSET_MISSING")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"spec asset JSON invalid: {path}: {exc.msg}", code="JSON_INVALID") from exc


def _validate_dictionary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SpecError("event dictionary must be a JSON object", code="DICT_NOT_OBJECT")
    required = {"dict_ver", "domain_defs", "event_defs"}
    missing = required.difference(raw)
    if missing:
        raise SpecError(f"event dictionary missing fields: {sorted(missing)}", code="DICT_MISSING_FIELDS")
    if not isinstance(raw["domain_defs"], dict):
        raise SpecError("event dictionary field domain_defs must be an object", code="DICT_DOMAIN_DEFS_INVALID")
    if not isinstance(raw["event_defs"], list):
        raise SpecError("event dictionary field event_defs must be a list", code="DICT_EVENT_DEFS_INVALID")
    return raw


def load_dictionary(source: dict[str, Any] | str | Path | None = None) -> dict[str, Any]:
    if source is None:
        raw = _load_json(DICTIONARY_PATH)
    elif isinstance(source, dict):
        raw = source
    elif isinstance(source, (str, Path)):
        raw = _load_json(Path(source))
    else:
        raise SpecError(
            f"unsupported dictionary source type: {type(source).__name__}",
            code="DICT_UNSUPPORTED_SOURCE",
        )
    return _validate_dictionary(raw)


def load_schema(name: str) -> dict[str, Any]:
    schema_path = SCHEMA_DIR / name
    if not schema_path.exists():
        schema_path = ASSET_SCHEMA_DIR / name
    raw = _load_json(schema_path)
    required = {"$schema", "title", "type"}
    missing = required.difference(raw)
    if missing:
        raise SpecError(f"schema {name} missing fields: {sorted(missing)}", code="SCHEMA_MISSING_FIELDS")
    return raw


def load_specs() -> dict[str, dict[str, Any]]:
    return {
        "dictionary": load_dictionary(),
        "package": load_schema("package.schema.json"),
        "meta": load_schema("meta.schema.json"),
        "manifest": load_schema("manifest.schema.json"),
        "analysis_context": load_schema("analysis_context.schema.json"),
        "compare_scope": load_schema("compare_scope.schema.json"),
        "dependency_sidecar": load_schema("dependency_sidecar.schema.json"),
        "frontier_snapshot": load_schema("frontier_snapshot.schema.json"),
        "frontier_refs": load_schema("frontier_refs.schema.json"),
        "proof_digest": load_schema("proof_digest.schema.json"),
        "sidecar_manifest": load_schema("sidecar_manifest.schema.json"),
        "sidecar_segment_manifest": load_schema("sidecar_segment_manifest.schema.json"),
        "blocker_artifact": load_schema("blocker_artifact.schema.json"),
        "result_validity": load_schema("result_validity.schema.json"),
        "sidecar_index_ticket": load_schema("sidecar_index_ticket.schema.json"),
        "telemetry_record": load_schema("telemetry_record.schema.json"),
        "telemetry_record_update": load_schema("telemetry_record_update.schema.json"),
        "advisor_decision": load_schema("advisor_decision.schema.json"),
        "advisor_trace": load_schema("advisor_trace.schema.json"),
        "advisor_report": load_schema("advisor_report.schema.json"),
        "runtime_action": load_schema("runtime_action.schema.json"),
        "runtime_action_set": load_schema("runtime_action_set.schema.json"),
        "advisor_grounding_report": load_schema("advisor_grounding_report.schema.json"),
        "validation_gate_result": load_schema("validation_gate_result.schema.json"),
        "benchmark_scenario": load_schema("benchmark_scenario.schema.json"),
        "agent_job_contract": load_schema("agent_job_contract.schema.json"),
        "export_write_metric": load_schema("export_write_metric.schema.json"),
        "write_failure_blocker": load_schema("write_failure_blocker.schema.json"),
        "parser_process_artifact": load_schema("parser_process_artifact.schema.json"),
        "formal_schedule_plan": load_schema("formal_schedule_plan.schema.json"),
        "benchmark_report": load_schema("benchmark_report.schema.json"),
        "runtime_optimization_product_evidence": load_schema("runtime_optimization_product_evidence.schema.json"),
    }

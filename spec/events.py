from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema_loader import SpecError, load_dictionary


@dataclass(frozen=True)
class EventDefinition:
    event_id: int
    event_name: str
    domain: str
    payload_fields: tuple[str, ...]
    p0_required: bool = True


@dataclass(frozen=True)
class EventCatalog:
    dict_ver: int
    events_by_id: dict[int, EventDefinition]
    events_by_name: dict[str, EventDefinition]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EventCatalog":
        events_by_id: dict[int, EventDefinition] = {}
        events_by_name: dict[str, EventDefinition] = {}
        for item in raw["event_defs"]:
            definition = EventDefinition(
                event_id=int(item["event_id"]),
                event_name=str(item["event_name"]),
                domain=str(item["domain"]),
                payload_fields=tuple(item["payload_fields"]),
                p0_required=bool(item.get("p0_required", True)),
            )
            events_by_id[definition.event_id] = definition
            events_by_name[definition.event_name] = definition
        return cls(
            dict_ver=int(raw["dict_ver"]),
            events_by_id=events_by_id,
            events_by_name=events_by_name,
        )


_FALLBACK_DICTIONARY = {
    "dict_ver": 1,
    "event_defs": [
        {"event_id": 4097, "event_name": "TASK_READY", "domain": "task", "payload_fields": ["task_id", "prio", "core_hint", "reason"]},
        {"event_id": 4098, "event_name": "TASK_BLOCK", "domain": "task", "payload_fields": ["task_id", "wait_obj_id", "reason", "owner_task_id"]},
        {"event_id": 4099, "event_name": "TASK_WAKEUP", "domain": "task", "payload_fields": ["task_id", "wake_src", "obj_id"]},
        {"event_id": 4100, "event_name": "TASK_DISPATCH", "domain": "task", "payload_fields": ["task_id", "core_id", "prio", "reason"]},
        {"event_id": 4101, "event_name": "TASK_EXIT", "domain": "task", "payload_fields": ["task_id", "exit_code"]},
        {"event_id": 4102, "event_name": "CTX_SWITCH", "domain": "task", "payload_fields": ["core_id", "prev_task_id", "next_task_id", "reason"]},
        {"event_id": 4103, "event_name": "SCHED_DECISION", "domain": "task", "payload_fields": ["core_id", "selected_task_id", "rq_len", "reason"]},
        {"event_id": 8193, "event_name": "SYNC_TRY", "domain": "sync", "payload_fields": ["task_id", "obj_id", "obj_type", "timeout"]},
        {"event_id": 8194, "event_name": "SYNC_LOCK", "domain": "sync", "payload_fields": ["task_id", "obj_id", "obj_type"]},
        {"event_id": 8195, "event_name": "SYNC_UNLOCK", "domain": "sync", "payload_fields": ["task_id", "obj_id", "obj_type"]},
        {"event_id": 12289, "event_name": "IRQ_ENTER", "domain": "irq", "payload_fields": ["irq_id", "core_id", "nesting_depth"]},
        {"event_id": 12290, "event_name": "IRQ_EXIT", "domain": "irq", "payload_fields": ["irq_id", "core_id", "nesting_depth"]},
        {"event_id": 16385, "event_name": "LOSS", "domain": "integrity", "payload_fields": ["core_id", "lost_count", "reason"]},
        {"event_id": 16386, "event_name": "OVERFLOW", "domain": "integrity", "payload_fields": ["core_id", "overflow_count", "reason"]},
        {"event_id": 16387, "event_name": "SYNC_CALIB", "domain": "align", "payload_fields": ["anchor_id", "core_id", "ref_ts"], "p0_required": False},
        {"event_id": 16388, "event_name": "TS_CALIB", "domain": "align", "payload_fields": ["anchor_id", "src_core", "dst_core", "raw_ts"], "p0_required": False},
    ],
}


def _default_catalog() -> EventCatalog:
    return EventCatalog.from_dict(load_dictionary())


def _default_dictionary_payload() -> dict[str, Any]:
    return load_dictionary()


def _requested_source(dictionary: dict[str, Any] | str | Path | None) -> tuple[str, str | None, int | None]:
    if dictionary is None:
        return "default", None, None
    if isinstance(dictionary, dict):
        requested_ver = dictionary.get("dict_ver")
        return "external_dict", None, int(requested_ver) if requested_ver is not None else None
    target = Path(dictionary)
    return "external_path", str(target), None


def _reason_code_for_exc(exc: SpecError, source: str) -> str:
    if source in {"external_dict", "external_path"}:
        if exc.code == "ASSET_MISSING":
            return "DICT_EXTERNAL_PATH_MISSING"
        if exc.code == "JSON_INVALID":
            return "DICT_EXTERNAL_JSON_INVALID"
        return "DICT_EXTERNAL_SCHEMA_INVALID"
    if exc.code == "ASSET_MISSING":
        return "DICT_DEFAULT_ASSET_MISSING"
    return "DICT_DEFAULT_ASSET_INVALID"


def _catalog_report(
    *,
    dictionary: dict[str, Any] | str | Path | None,
    resolved_source: str,
    resolved_dictionary: dict[str, Any],
    expected_dict_ver: int | None,
    warnings: list[str],
    reason_codes: list[str],
) -> dict[str, Any]:
    requested_source, requested_path, requested_dict_ver = _requested_source(dictionary)
    resolved_dict_ver = resolved_dictionary.get("dict_ver")
    resolved_ver = int(resolved_dict_ver) if resolved_dict_ver is not None else None
    expected_ver = int(expected_dict_ver) if expected_dict_ver is not None else None
    version_mismatch = expected_ver is not None and resolved_ver != expected_ver
    return {
        "requested_source": requested_source,
        "requested_path": requested_path,
        "requested_dict_ver": requested_dict_ver,
        "resolved_source": resolved_source,
        "resolved_dict_ver": resolved_ver,
        "expected_dict_ver": expected_ver,
        "fallback_used": bool(reason_codes),
        "reason_codes": list(reason_codes),
        "version_mismatch": version_mismatch,
        "warnings": list(warnings),
        "resolved_dictionary": deepcopy(resolved_dictionary),
    }


def load_event_catalog(
    dictionary: dict[str, Any] | str | Path | None = None,
    expected_dict_ver: int | None = None,
) -> tuple[EventCatalog, dict[str, Any], list[str]]:
    warnings: list[str] = []
    resolved_catalog: EventCatalog | None = None
    resolved_dictionary: dict[str, Any] | None = None
    resolved_source = "default"
    reason_codes: list[str] = []
    expected_ver = int(expected_dict_ver) if expected_dict_ver is not None else None
    requested_source, _, _ = _requested_source(dictionary)

    if dictionary is not None:
        try:
            resolved_dictionary = load_dictionary(dictionary)
            resolved_catalog = EventCatalog.from_dict(resolved_dictionary)
            resolved_source = "external"
        except SpecError as exc:
            reason_code = _reason_code_for_exc(exc, requested_source)
            reason_codes.append(reason_code)
            warnings.append(
                f"external event dictionary load failed [{reason_code}], falling back to default dictionary: {exc}"
            )

    if resolved_catalog is None:
        try:
            resolved_dictionary = _default_dictionary_payload()
            resolved_catalog = EventCatalog.from_dict(resolved_dictionary)
            resolved_source = "default"
        except SpecError as exc:
            reason_code = _reason_code_for_exc(exc, "default")
            reason_codes.append(reason_code)
            warnings.append(
                f"default event dictionary load failed [{reason_code}], falling back to built-in catalog: {exc}"
            )
            resolved_dictionary = deepcopy(_FALLBACK_DICTIONARY)
            resolved_catalog = EventCatalog.from_dict(resolved_dictionary)
            resolved_source = "builtin"
            reason_codes.append("DICT_BUILTIN_FALLBACK")
            warnings.append("built-in fallback catalog activated [DICT_BUILTIN_FALLBACK]")

    if expected_ver is not None and resolved_catalog.dict_ver != expected_ver:
        warnings.append(
            f"event dictionary version mismatch: expected {expected_ver}, got {resolved_catalog.dict_ver}"
        )
    report = _catalog_report(
        dictionary=dictionary,
        resolved_source=resolved_source,
        resolved_dictionary=resolved_dictionary or deepcopy(_FALLBACK_DICTIONARY),
        expected_dict_ver=expected_ver,
        warnings=warnings,
        reason_codes=reason_codes,
    )
    return resolved_catalog, report, warnings


def event_id_for(name: str, catalog: EventCatalog | None = None) -> int:
    resolved_catalog = catalog or load_event_catalog()[0]
    return resolved_catalog.events_by_name[name].event_id


def event_name_for(event_id: int, catalog: EventCatalog | None = None) -> str:
    resolved_catalog = catalog or load_event_catalog()[0]
    return resolved_catalog.events_by_id.get(
        event_id,
        EventDefinition(
            event_id=event_id,
            event_name=f"UNKNOWN_{event_id}",
            domain="unknown",
            payload_fields=tuple(),
            p0_required=False,
        ),
    ).event_name


ROOT = Path(__file__).resolve().parent
DEFAULT_DICTIONARY_PATH = ROOT / "dictionary" / "event_dictionary.json"

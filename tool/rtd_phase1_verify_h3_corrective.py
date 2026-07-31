#!/usr/bin/env python3
"""Read-only verifier for the Phase 1 H3 corrective scorecard."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "docs/rtd_pilot/feasibility/phase1_h3_scorecard_v2.json"
DIMENSIONS = {
    "physical_availability", "license", "toolchain_reproducibility", "GPIO",
    "hardware_timer", "UART_transport", "priority_control", "mutex_support",
    "controllable_IRQ", "observer_isolation", "data_release_rights",
}
ITEM_FIELDS = {
    "dimension", "numeric_score", "exact_configuration", "evidence_refs",
    "semantic_review", "reviewer", "decision", "remaining_risk",
}
CONFIG_FIELDS = {
    "configuration_id", "board", "mcu", "target", "board_id", "probe_uid",
    "rtos", "bsp", "firmware_variant", "firmware_sha256", "elf_sha256",
    "build_configuration_sha256", "toolchain", "uart", "collector", "observer",
}
MARKERS = {
    "F1": {"PC4 target-ready/dispatch", "PC5 higher-priority interference"},
    "F2": {"PC6 mutex holder", "PC7 mutex waiter"},
    "F3": {"PB5 TIM3 IRQ activity"},
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _ref(root: Path, ref: Any, errors: list[str], label: str) -> Path | None:
    if not isinstance(ref, str) or not ref:
        errors.append(f"{label} has no usable evidence ref")
        return None
    path = (root / ref).resolve()
    if root not in path.parents or not path.is_file():
        errors.append(f"{label} evidence ref does not resolve: {ref}")
        return None
    return path


def verify(scorecard_path: Path = SCORECARD, root: Path = ROOT) -> dict[str, Any]:
    errors: list[str] = []
    checks: list[str] = []
    try:
        card = _load(scorecard_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"valid": False, "checks": checks, "errors": [str(exc)]}

    configuration = card.get("exact_configuration")
    if not isinstance(configuration, dict) or not CONFIG_FIELDS.issubset(configuration):
        errors.append("exact configuration is incomplete")
        configuration = {}
    config_id = configuration.get("configuration_id")
    configuration_path = _ref(root, card.get("configuration_evidence_ref"), errors, "exact configuration")
    if configuration_path is not None:
        try:
            metadata = _load(configuration_path)
            target = metadata.get("target", {})
            expected = {
                "board_id": metadata.get("board_id"),
                "firmware_variant": metadata.get("firmware_variant"),
                "firmware_sha256": metadata.get("firmware_sha256"),
                "elf_sha256": metadata.get("elf_sha256"),
                "build_configuration_sha256": metadata.get("build_config_sha256"),
                "target": target.get("target"),
                "probe_uid": target.get("probe_uid"),
            }
            if any(configuration.get(field) != value for field, value in expected.items()):
                errors.append("exact configuration conflicts with H3 preflash metadata")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))

    items = card.get("items")
    if not isinstance(items, list) or {item.get("dimension") for item in items if isinstance(item, dict)} != DIMENSIONS:
        errors.append("all eleven mandatory dimensions must exist exactly once")
        items = []
    elif len(items) != len(DIMENSIONS):
        errors.append("all eleven mandatory dimensions must exist exactly once")

    original_author = card.get("original_author")
    independent_reviewers: set[str] = set()
    for item in items:
        dimension = item.get("dimension", "unknown")
        if not ITEM_FIELDS.issubset(item):
            errors.append(f"{dimension} lacks required scorecard fields")
            continue
        score = item["numeric_score"]
        if not isinstance(score, int) or isinstance(score, bool) or score not in {0, 1, 2}:
            errors.append(f"{dimension} numeric_score must be integer 0, 1, or 2")
        elif score != 2:
            errors.append(f"mandatory H3 score must be 2: {dimension}={score}")
        if item["decision"] == "PASS" and score != 2:
            errors.append(f"PASS does not replace numeric_score=2: {dimension}")
        if item["exact_configuration"] != config_id:
            errors.append(f"{dimension} exact_configuration does not match C0")
        refs = item["evidence_refs"]
        if not isinstance(refs, list) or not refs:
            errors.append(f"{dimension} lacks evidence refs")
        else:
            for ref in refs:
                _ref(root, ref, errors, dimension)
        reviewer = item["reviewer"]
        if not isinstance(reviewer, str) or not reviewer or reviewer == original_author:
            errors.append(f"{dimension} has self-review-only semantic review")
        else:
            independent_reviewers.add(reviewer)
    if not independent_reviewers:
        errors.append("independent reviewer is absent")
    checks.append("mandatory numeric scores, evidence refs, configuration, and reviewer separation")

    pin = card.get("pin_map", {})
    pinmap_path = _ref(root, pin.get("machine_ref"), errors, "pin map") if isinstance(pin, dict) else None
    if not isinstance(pin, dict) or pin.get("resolution") != "CLOSED":
        errors.append("PB0 resolution is not CLOSED")
    if not isinstance(pin, dict) or pin.get("semantic_prose") != "PB0=LCD_BL; PE5=green LED; CH7=PB0 high recorder-pressure marker":
        errors.append("PB0 prose conflicts with corrective machine map")
    if pinmap_path is not None:
        try:
            pinmap = _load(pinmap_path)
            channel = pinmap.get("channels", {}).get("7", {})
            resolution = pinmap.get("pb0_resolution", {})
            if channel.get("gpio") != "PB0" or channel.get("role") != "H3_RECORDER_PRESSURE_INTERVAL" or resolution.get("PB0_is") != "LCD_BL, not the green RGB LED" or resolution.get("green_LED_gpio") != "PE5":
                errors.append("PB0 machine representation conflicts with corrective prose")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    checks.append("PB0 prose and machine representation")

    marker_sets = card.get("marker_semantics", {})
    if not isinstance(marker_sets, dict):
        marker_sets = {}
    for family, required in MARKERS.items():
        entry = marker_sets.get(family, {})
        if not isinstance(entry, dict) or set(entry.get("markers", [])) != required:
            errors.append(f"{family} marker semantic set is incomplete")
        else:
            _ref(root, entry.get("evidence_ref"), errors, f"{family} marker semantics")
    checks.append("F1/F2/F3 marker semantic sets")

    uart = card.get("uart_transport", {})
    observer = card.get("observer_isolation", {})
    for label, entry, required_field in (("UART transport", uart, "configuration"), ("Observer isolation", observer, "status")):
        if not isinstance(entry, dict) or not entry.get(required_field):
            errors.append(f"{label} field is absent")
            continue
        refs = entry.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{label} evidence is absent")
        else:
            for ref in refs:
                _ref(root, ref, errors, label)
    checks.append("UART transport and Observer isolation")

    alignment = card.get("alignment", {})
    if not isinstance(alignment, dict):
        alignment = {}
    alignment_path = _ref(root, alignment.get("review_ref"), errors, "alignment")
    bounds_path = _ref(root, alignment.get("frozen_bound_ref"), errors, "alignment bounds")
    for field in ("observed_max_ch1_period_absolute_error_s", "frozen_max_ch1_period_absolute_error_s"):
        if not isinstance(alignment.get(field), (int, float)) or isinstance(alignment.get(field), bool):
            errors.append(f"alignment {field} is not numeric")
    if alignment_path is not None:
        try:
            review = _load(alignment_path)
            if review.get("alignment_status") != alignment.get("status"):
                errors.append("alignment status conflicts with offline review")
            if review.get("available_aligned_h3_sessions") != alignment.get("available_aligned_h3_sessions") or review.get("required_independent_h3_sessions") != alignment.get("required_independent_h3_sessions"):
                errors.append("alignment session count conflicts with offline review")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    if bounds_path is not None:
        try:
            bounds = _load(bounds_path).get("bounds", {})
            if bounds.get("max_ch1_period_absolute_error_s") != alignment.get("frozen_max_ch1_period_absolute_error_s"):
                errors.append("alignment bound conflicts with frozen bound")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    checks.append("alignment bound parsing and evidence")

    route = card.get("release_route", {})
    if not isinstance(route, dict) or not isinstance(route.get("status"), str) or not route["status"]:
        errors.append("license/release route lacks an explicit status")
    else:
        _ref(root, route.get("evidence_ref"), errors, "license/release route")
    checks.append("license/release route")
    return {"valid": not errors, "checks": checks, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scorecard", type=Path, default=SCORECARD)
    args = parser.parse_args()
    result = verify(args.scorecard)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

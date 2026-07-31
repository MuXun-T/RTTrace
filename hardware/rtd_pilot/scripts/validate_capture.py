#!/usr/bin/env python3
"""Fail-closed Phase 1 observer validator; never reads parser/diagnoser output."""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from phase1_common import load_board_contract, load_json, sha256

REQUIRED_ROLES = {"EPOCH", "CALIBRATION_TIMER", "TASK_A_RUNNING", "TASK_B_RUNNING", "MUTEX_HOLD", "MUTEX_WAIT", "IRQ_ACTIVE", "RECORDER_PRESSURE"}
REQUIRED_TARGET = "stm32f103ze"
REQUIRED_PROBE_UID = "0001A0000001"
REQUIRED_CHANNELS = {str(index) for index in range(8)}
COMBINED_CONTRACTS = {}
for combined_contract_path in (
    "phase1_combined_smoke_alientek_dslogic_plus.json",
    "phase1_combined_smoke_fire_dslogic_plus.json",
):
    combined_contract = load_json(Path(__file__).parent.parent / "contracts" / combined_contract_path)
    board_id = combined_contract.get("board_id")
    if combined_contract.get("schema_version") != "phase1-combined-smoke-contract-v1" or combined_contract.get("firmware_variant") != "COMBINED_SMOKE" or not isinstance(board_id, str) or board_id in COMBINED_CONTRACTS:
        raise RuntimeError("combined-smoke contract is invalid")
    COMBINED_CONTRACTS[board_id] = combined_contract
CLOCK_BOUNDS = load_json(Path(__file__).parent.parent / "contracts" / "phase1_clock_bounds_alientek_elite_v2.json")
if CLOCK_BOUNDS.get("schema_version") != "phase1-clock-bounds-v1" or CLOCK_BOUNDS.get("capture_profile") != "calibration_20mhz" or not isinstance(CLOCK_BOUNDS.get("board_id"), str) or not isinstance(CLOCK_BOUNDS.get("bounds"), dict):
    raise RuntimeError("clock-bounds contract is invalid")
VARIANT_REQUIREMENTS = {
    "BASE": {"uart": False, "profile": "calibration_20mhz", "nonflat": set()},
    "GPIO_ONLY": {"uart": False, "profile": "calibration_20mhz", "nonflat": {"0", "1"}},
    "GPIO_UART": {"uart": True, "profile": "calibration_20mhz", "nonflat": {"0", "1"}},
    "GPIO_UART_RECORDER": {"uart": True, "profile": "calibration_20mhz", "nonflat": {"0", "1", "7"}},
    "TASK_SMOKE": {"uart": True, "profile": "calibration_20mhz", "nonflat": {"0", "1", "2", "3"}},
    "MUTEX_SMOKE": {"uart": True, "profile": "calibration_20mhz", "nonflat": {"0", "1", "4", "5"}},
    "IRQ_SMOKE": {"uart": True, "profile": "irq_precision_100mhz", "nonflat": {"0", "1", "6"}},
    "COMBINED_SMOKE": {"uart": True, "profile": "irq_precision_100mhz", "nonflat": REQUIRED_CHANNELS},
}
PROFILE_MIN_SAMPLE_RATE_HZ = {"calibration_20mhz": 20_000_000, "irq_precision_100mhz": 100_000_000}
UART_EPOCH = re.compile(r"RTD1\s+EPOCH_(BEGIN|END)\s+seq=(\d+)")

def checked_file(metadata: dict, base: Path, file_field: str, hash_field: str, reasons: list[str]) -> Path | None:
    name, expected = metadata.get(file_field), metadata.get(hash_field)
    if not name:
        reasons.append(f"missing_{file_field}")
        return None
    path = base / name
    if not path.is_file() or path.stat().st_size == 0:
        reasons.append(f"empty_or_missing_{file_field}")
    elif not expected or sha256(path) != expected:
        reasons.append(f"{file_field}_hash_mismatch")
    return path

def optional_checked_file(metadata: dict, base: Path, file_field: str, hash_field: str, reasons: list[str]) -> Path | None:
    if not metadata.get(file_field) and not metadata.get(hash_field):
        return None
    return checked_file(metadata, base, file_field, hash_field, reasons)

def uart_epochs(path: Path, reasons: list[str]) -> set[int]:
    begin, end = set(), set()
    for kind, sequence in UART_EPOCH.findall(path.read_text(encoding="utf-8", errors="replace")):
        (begin if kind == "BEGIN" else end).add(int(sequence))
    if not begin: reasons.append("uart_missing_epoch_begin")
    if not end: reasons.append("uart_missing_epoch_end")
    if begin != end: reasons.append("uart_epoch_pairing_disagreement")
    if begin and sorted(begin) != list(range(min(begin), max(begin) + 1)): reasons.append("uart_sequence_gap")
    return begin if begin == end else set()

def invalid_reasons(metadata: dict, base: Path, pinmap: dict, min_rate: int, max_drift_ppm: float | None, max_alignment_s: float | None) -> list[str]:
    reasons: list[str] = []
    variant = metadata.get("firmware_variant")
    requirements = VARIANT_REQUIREMENTS.get(variant)
    for field in ("capture_id", "session_id", "board_id", "firmware_sha256", "elf_sha256", "build_config_sha256", "pinmap_sha256"):
        if not metadata.get(field): reasons.append(f"missing_{field}")
    target = metadata.get("target", {})
    contract = None
    expected_pinmap = pinmap
    expected_target = {"target": REQUIRED_TARGET, "probe_uid": REQUIRED_PROBE_UID}
    try:
        contract, contract_pinmap_path = load_board_contract(metadata.get("board_id"), Path(__file__).parent)
        expected_pinmap = load_json(contract_pinmap_path)
        expected_target = contract["target"]
        if Path(pinmap["_path"]).resolve() != contract_pinmap_path:
            reasons.append("pinmap_contract_mismatch")
        if expected_pinmap.get("board_id", metadata.get("board_id")) != metadata.get("board_id"):
            reasons.append("pinmap_board_id_mismatch")
    except (KeyError, TypeError, ValueError):
        reasons.append("board_id_mismatch")
    if variant == "COMBINED_SMOKE" and (combined_contract := COMBINED_CONTRACTS.get(metadata.get("board_id"))):
        combined = combined_contract["observer"]
        requirements = {"uart": True, "profile": combined["capture_profile"], "nonflat": REQUIRED_CHANNELS, "minimum_sample_rate_hz": int(combined["minimum_sample_rate_hz"]), "expected_duration_s": float(combined["expected_duration_s"]), "captured_channels": set(combined["captured_channels"])}
    if target.get("target") != expected_target["target"] or target.get("probe_uid") != expected_target["probe_uid"]:
        reasons.append("target_binding_mismatch")
    if "mcu_uid_words" in expected_target and target.get("mcu_uid_words") != expected_target["mcu_uid_words"]:
        reasons.append("target_identity_mismatch")
    if metadata.get("schema_version") != "phase1-capture-v2": reasons.append("capture_schema_version_mismatch")
    expected_pinmap_version = contract.get("pin_map_version") if contract else "phase1-pinmap-v2"
    if metadata.get("pin_map_version") != expected_pinmap_version or pinmap.get("schema_version") != expected_pinmap_version or expected_pinmap.get("schema_version") != expected_pinmap_version:
        reasons.append("retired_or_mismatched_pinmap_version")
    observer = metadata.get("observer", {})
    if requirements is None:
        reasons.append("firmware_variant_invalid")
        requirements = {"uart": False, "profile": "", "nonflat": set()}
    profile = observer.get("capture_profile")
    if profile != requirements["profile"]:
        reasons.append("capture_profile_mismatch")
    required_rate = max(min_rate, PROFILE_MIN_SAMPLE_RATE_HZ.get(requirements["profile"], 0), requirements.get("minimum_sample_rate_hz", 0))
    if observer.get("sample_rate_hz", 0) < required_rate: reasons.append("sample_rate_below_frozen_minimum")
    if "expected_duration_s" in requirements and observer.get("expected_duration_s") != requirements["expected_duration_s"]:
        reasons.append("capture_duration_contract_mismatch")
    channels = observer.get("channels", {})
    expected_channels = expected_pinmap.get("channels", {})
    if set(channels) != REQUIRED_CHANNELS or set(expected_channels) != REQUIRED_CHANNELS:
        reasons.append("channel_mapping_mismatch")
    else:
        for channel, expected in expected_channels.items():
            entry = channels.get(channel, {})
            if not isinstance(entry, dict) or entry.get("gpio") != expected.get("gpio") or entry.get("role") != expected.get("role"):
                reasons.append("channel_mapping_mismatch")
                break
    expected_uart = {key: expected_pinmap.get("uart", {}).get(key) for key in ("device", "peripheral", "tx_gpio", "rx_gpio", "format")}
    if metadata.get("uart") != expected_uart:
        reasons.append("uart_mapping_mismatch")
    if metadata.get("pinmap_sha256") and metadata["pinmap_sha256"] != sha256(Path(pinmap["_path"])): reasons.append("pinmap_hash_mismatch")
    for file_field, hash_field in (("firmware_file", "firmware_sha256"), ("elf_file", "elf_sha256"), ("build_config_file", "build_config_sha256"), ("observer_file", "observer_sha256"), ("normalized_observer_file", "normalized_observer_sha256"), ("observer_summary_file", "observer_summary_sha256")):
        checked_file(metadata, base, file_field, hash_field, reasons)
    if requirements["uart"]:
        checked_file(metadata, base, "uart_file", "uart_sha256", reasons)
        checked_file(metadata, base, "clock_analysis_file", "clock_analysis_sha256", reasons)
    else:
        if metadata.get("uart_file") or metadata.get("uart_sha256"): reasons.append("unexpected_uart_artifact")
        if metadata.get("clock_analysis_file") or metadata.get("clock_analysis_sha256"): reasons.append("unexpected_clock_analysis_artifact")
    summary_path = base / metadata.get("observer_summary_file", "")
    clock_path = base / metadata.get("clock_analysis_file", "")
    uart_path = base / metadata.get("uart_file", "")
    observer_epochs: set[int] = set()
    if summary_path.is_file() and summary_path.stat().st_size:
        try:
            summary = load_json(summary_path)
            if summary.get("observer_file_sha256") != metadata.get("observer_sha256") or summary.get("normalized_observer_file_sha256") != metadata.get("normalized_observer_sha256"):
                reasons.append("observer_summary_source_hash_mismatch")
            observed_nonflat = set(summary["nonflat_channels"])
            if observed_nonflat != requirements["nonflat"]: reasons.append("observer_channel_activity_mismatch")
            if "captured_channels" in requirements and set(summary.get("captured_channels", [])) != requirements["captured_channels"]:
                reasons.append("observer_capture_channel_set_mismatch")
            if requirements["uart"]:
                observer_epochs = set(summary["epoch_begin_sequences"])
                if not observer_epochs or observer_epochs != set(summary["epoch_end_sequences"]): reasons.append("observer_epoch_pairing_disagreement")
            if not summary.get("capture_complete"): reasons.append("observer_summary_incomplete")
            expected_duration = requirements.get("expected_duration_s", observer.get("expected_duration_s"))
            if expected_duration is not None and abs(float(summary["duration_s"]) - float(expected_duration)) > 0.05:
                reasons.append("observer_duration_mismatch")
        except (KeyError, TypeError, ValueError): reasons.append("observer_summary_invalid")
    if requirements["uart"] and uart_path.is_file() and uart_path.stat().st_size:
        uart_sequences = uart_epochs(uart_path, reasons)
        if observer_epochs and uart_sequences and not observer_epochs.issubset(uart_sequences): reasons.append("gpio_uart_epoch_disagreement")
    if clock_path.is_file() and clock_path.stat().st_size:
        try:
            clock = load_json(clock_path); drift, residual = float(clock["drift_ppm"]), float(clock["alignment_residual_max_s"])
            frozen = CLOCK_BOUNDS["bounds"] if metadata.get("board_id") == CLOCK_BOUNDS["board_id"] and requirements["profile"] == CLOCK_BOUNDS["capture_profile"] else {}
            drift_bounds = [value for value in (max_drift_ppm, frozen.get("max_absolute_drift_ppm")) if value is not None]
            alignment_bounds = [value for value in (max_alignment_s, frozen.get("max_alignment_residual_s")) if value is not None]
            if drift_bounds and abs(drift) > min(drift_bounds): reasons.append("drift_exceeds_frozen_bound")
            if alignment_bounds and abs(residual) > min(alignment_bounds): reasons.append("alignment_exceeds_frozen_bound")
            if frozen and float(clock["absolute_error_max_s"]) > float(frozen["max_ch1_period_absolute_error_s"]): reasons.append("calibration_error_exceeds_frozen_bound")
        except (KeyError, TypeError, ValueError): reasons.append("clock_analysis_invalid")
    if target.get("reset_observed") or target.get("halt_or_fault_observed"): reasons.append("target_reset_or_fault")
    if metadata.get("declared_changes") is None: reasons.append("missing_declared_changes")
    return reasons

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path); parser.add_argument("--pinmap", type=Path, required=True)
    parser.add_argument("--min-sample-rate-hz", type=int, required=True); parser.add_argument("--max-drift-ppm", type=float); parser.add_argument("--max-alignment-s", type=float)
    args = parser.parse_args()
    meta = load_json(args.metadata); pinmap = load_json(args.pinmap); pinmap["_path"] = str(args.pinmap)
    reasons = invalid_reasons(meta, args.metadata.parent, pinmap, args.min_sample_rate_hz, args.max_drift_ppm, args.max_alignment_s)
    print("VALID" if not reasons else "INVALID " + ",".join(sorted(set(reasons))))
    return 0 if not reasons else 2
if __name__ == "__main__": sys.exit(main())

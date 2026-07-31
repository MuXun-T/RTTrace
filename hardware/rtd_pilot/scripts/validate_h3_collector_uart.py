#!/usr/bin/env python3
"""Fail-closed UART proof for the bounded H3 target collector smoke."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from phase1_common import load_json, sha256, write_new_json

BOOT_PREFIX = "RTD1 BOOT firmware=phase1-freertos "
CONFIG = re.compile(r"^RTD1 COLLECTOR_CONFIG version=h3-smoke-v1 capacity=(\d+) transport=(\S+) drop_policy=(\S+) irq_decimation=(\d+)$")
TRACE = re.compile(r"^RTD1 TRACE seq=(\d+) tick=(\d+) kind=(\d+)$")
STATUS = re.compile(r"^RTD1 COLLECTOR_STATUS stage=post_pressure next_seq=(\d+) queued=(\d+) high_watermark=(\d+) dropped=(\d+) overflow=(\d+)$")


def invalid_reasons(metadata: dict, text: str) -> tuple[list[str], dict[str, object]]:
    reasons: list[str] = []
    firmware = metadata.get("firmware", {})
    target = metadata.get("target", {})
    collector = metadata.get("collector", {})
    if metadata.get("schema_version") != "phase1-h3-collector-capture-v1":
        reasons.append("schema_version_mismatch")
    if metadata.get("board_id") != "alientek-atk-dnf103-v2-f103zet6-05d7ff34-334e5630-43057222":
        reasons.append("board_id_mismatch")
    if firmware.get("variant") != "H3_COLLECTOR_SMOKE":
        reasons.append("firmware_variant_mismatch")
    if firmware.get("capture_gate") != {"symbol": "capture_armed", "address": "0x20000000", "mode": "single_use_swd"}:
        reasons.append("capture_gate_mismatch")
    if not firmware.get("build_hash") or not firmware.get("session_id"):
        reasons.append("firmware_identity_missing")
    if target.get("target") != "stm32f103ze" or target.get("probe_uid") != "0001A0000001":
        reasons.append("target_binding_mismatch")
    if target.get("mcu_uid_words") != ["0x05d7ff34", "0x334e5630", "0x43057222"]:
        reasons.append("target_uid_mismatch")
    if collector != {"capacity": 64, "transport": "USART1_115200", "drop_policy": "drop_new", "irq_decimation": 20}:
        reasons.append("collector_contract_mismatch")

    lines = text.replace("\r", "").splitlines()
    boot = next((index for index, line in enumerate(lines) if all(token in line for token in (BOOT_PREFIX, "variant=H3_COLLECTOR_SMOKE", f"build_hash={firmware.get('build_hash', '')}", f"session={firmware.get('session_id', '')}", "pinmap=phase1-pinmap-v2", "uart=USART1_PA9_115200_8N1"))), None)
    config = next(((index, match) for index, line in enumerate(lines) if index > (boot if boot is not None else len(lines)) and (match := CONFIG.fullmatch(line))), None)
    armed = next((index for index, line in enumerate(lines) if line == "RTD1 CAPTURE_ARMED source=swd_gate"), None)
    begin = next((index for index, line in enumerate(lines) if line.startswith("RTD1 EPOCH_BEGIN seq=")), None)
    end = next((index for index, line in enumerate(lines) if line.startswith("RTD1 EPOCH_END seq=")), None)
    traces = [tuple(map(int, match.groups())) for line in lines if (match := TRACE.fullmatch(line))]
    status = next((match for line in lines if (match := STATUS.fullmatch(line))), None)
    if boot is None:
        reasons.append("boot_identity_missing")
    if config is None or config[1].groups() != ("64", "USART1_115200", "drop_new", "20"):
        reasons.append("collector_config_missing_or_mismatched")
    if armed is None or boot is None or armed <= boot:
        reasons.append("capture_armed_missing_or_unordered")
    if begin is None or end is None or armed is None or not armed < begin < end:
        reasons.append("epoch_gate_sequence_missing_or_unordered")
    sequences = [sequence for sequence, _tick, _kind in traces]
    if not sequences:
        reasons.append("trace_records_missing")
    elif any(later <= earlier for earlier, later in zip(sequences, sequences[1:])):
        reasons.append("trace_sequence_not_strictly_monotonic")
    elif not any(later > earlier + 1 for earlier, later in zip(sequences, sequences[1:])):
        reasons.append("trace_sequence_gap_missing")
    status_values: dict[str, int] = {}
    if status is None:
        reasons.append("post_pressure_status_missing")
    else:
        keys = ("next_sequence", "queued", "high_watermark", "dropped", "overflow")
        status_values = dict(zip(keys, map(int, status.groups())))
        if status_values["high_watermark"] != 64:
            reasons.append("high_watermark_not_capacity")
        if status_values["dropped"] <= 0:
            reasons.append("drop_not_observed")
        if status_values["overflow"] <= 0:
            reasons.append("overflow_not_observed")
    return reasons, {"trace_count": len(traces), "trace_first_sequence": sequences[0] if sequences else None, "trace_last_sequence": sequences[-1] if sequences else None, "collector_status": status_values}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--uart", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite an H3 collector validation result")
    if not args.uart.is_file() or not args.uart.stat().st_size:
        raise SystemExit("UART evidence is missing or empty")
    reasons, observed = invalid_reasons(load_json(args.metadata), args.uart.read_text(encoding="ascii", errors="replace"))
    result = {"schema_version": "phase1-h3-collector-uart-validation-v1", "metadata_sha256": sha256(args.metadata), "uart_sha256": sha256(args.uart), "valid": not reasons, "reasons": sorted(set(reasons)), "observed": observed}
    write_new_json(args.output, result)
    print("VALID" if not reasons else "INVALID " + ",".join(result["reasons"]))
    return 0 if not reasons else 2


if __name__ == "__main__":
    sys.exit(main())

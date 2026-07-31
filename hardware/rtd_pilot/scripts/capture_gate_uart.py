#!/usr/bin/env python3
"""Hold the candidate UART open and verify the ordered SWD gate handshake."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
import time
from pathlib import Path

from candidate_uart_control_preflight import (
    CANDIDATE_LABEL,
    EXPECTED_PORT,
    STABLE_PORT,
    configure_115200_8n1,
    modem_mask,
    set_modem_mask,
)

REQUIRED_MODEM_MASK = 0x2
GATED_VARIANTS = {"GPIO_UART_RECORDER", "TASK_SMOKE", "MUTEX_SMOKE", "IRQ_SMOKE", "COMBINED_SMOKE", "H3_COLLECTOR_SMOKE"}
GATE_ADDRESSES = {"H3_COLLECTOR_SMOKE": "0x20000000"}
ARMED_LINE = "RTD1 CAPTURE_ARMED source=swd_gate"
EPOCH_BEGIN = re.compile(r"RTD1 EPOCH_BEGIN seq=(\d+)\b")
EPOCH_END = re.compile(r"RTD1 EPOCH_END seq=(\d+)\b")


def load_identity(metadata_path: Path, elf_path: Path) -> dict[str, str]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    firmware = metadata.get("firmware", {})
    gate = firmware.get("capture_gate", {})
    target = metadata.get("target", {})
    identity = {
        "variant": firmware.get("variant", ""),
        "build_hash": firmware.get("build_hash", ""),
        "session": firmware.get("session_id", ""),
        "elf_sha256": firmware.get("elf_sha256", ""),
        "gate_symbol": gate.get("symbol", ""),
        "gate_address": gate.get("address", ""),
        "target": target.get("target", ""),
        "probe_uid": target.get("probe_uid", ""),
    }
    required = {
        "gate_symbol": "capture_armed",
        "gate_address": GATE_ADDRESSES.get(identity["variant"], "0x20000004"),
        "target": "stm32f103ze",
        "probe_uid": "0001A0000001",
    }
    if identity["variant"] not in GATED_VARIANTS or any(identity[key] != value for key, value in required.items()):
        raise SystemExit("candidate metadata identity or capture gate mismatch")
    if not identity["build_hash"] or not identity["session"]:
        raise SystemExit("candidate metadata is missing firmware identity")
    actual_elf_sha256 = hashlib.sha256(elf_path.read_bytes()).hexdigest()
    if identity["elf_sha256"] != actual_elf_sha256:
        raise SystemExit("candidate metadata ELF hash mismatch")
    return identity


def observe_gate_sequence(text: str, identity: dict[str, str]) -> dict[str, object]:
    boot_tokens = (
        "RTD1 BOOT ",
        f"variant={identity['variant']}",
        f"build_hash={identity['build_hash']}",
        f"session={identity['session']}",
        "pinmap=phase1-pinmap-v2",
        "uart=USART1_PA9_115200_8N1",
    )
    lines = text.replace("\r", "").splitlines()
    boot_index = next((index for index, line in enumerate(lines) if all(token in line for token in boot_tokens)), None)
    armed_index = next((index for index, line in enumerate(lines) if line == ARMED_LINE), None)
    begin = [(index, int(match.group(1))) for index, line in enumerate(lines) if (match := EPOCH_BEGIN.search(line))]
    end = [(index, int(match.group(1))) for index, line in enumerate(lines) if (match := EPOCH_END.search(line))]
    sequence = None
    if boot_index is not None and armed_index is not None and boot_index < armed_index:
        for begin_index, begin_sequence in begin:
            if begin_index <= armed_index:
                continue
            if any(end_index > begin_index and end_sequence == begin_sequence for end_index, end_sequence in end):
                sequence = begin_sequence
                break
    return {
        "boot_observed": boot_index is not None,
        "capture_armed_observed": armed_index is not None and boot_index is not None and boot_index < armed_index,
        "epoch_sequence": sequence,
        "gate_sequence_verified": sequence is not None,
    }


def write_new_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="ascii") as output:
        output.write(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--modem-policy", choices=("preserve", "explicit-mask-0x2"), default="preserve")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--gate-ready-file", required=True, type=Path)
    parser.add_argument("--verified-file", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--exit-on-verified", action="store_true", help="close the reader and write its receipt after the first verified gate")
    args = parser.parse_args()

    if args.seconds <= 0:
        raise SystemExit("capture duration must be positive")
    paths = (args.output, args.ready_file, args.gate_ready_file, args.verified_file, args.receipt)
    if any(path.exists() for path in paths):
        raise SystemExit("refusing to overwrite a gate capture artifact")
    if not Path(STABLE_PORT).is_symlink() or os.path.realpath(STABLE_PORT) != EXPECTED_PORT:
        raise SystemExit(f"stable CH340 binding does not resolve to {EXPECTED_PORT}")
    identity = load_identity(args.metadata, args.elf)

    started = time.time()
    fd = os.open(STABLE_PORT, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_115200_8n1(fd)
        mask_after_configure = modem_mask(fd)
        if args.modem_policy == "preserve":
            if mask_after_configure & 0x6 != REQUIRED_MODEM_MASK:
                raise SystemExit("formal mode requires the CH340 modem mask to be pre-set to 0x2")
            mask_after_policy = mask_after_configure
            modem_policy = "preserve-0x00000002"
        else:
            mask_after_policy = set_modem_mask(fd, REQUIRED_MODEM_MASK)
            if mask_after_policy & 0x6 != REQUIRED_MODEM_MASK:
                raise SystemExit("CH340 modem mask did not settle at the retained 0x2 policy")
            modem_policy = "explicit-mask-0x00000002"
        write_new_text(
            args.ready_file,
            "uart_open_configured=1\n"
            f"candidate_label={CANDIDATE_LABEL}\n"
            f"stable_port={STABLE_PORT}\nresolved_port={EXPECTED_PORT}\n"
            "baud=115200\nformat=8N1\nflow_control=none\naccess_mode=read-only\n"
            f"modem_policy={modem_policy}\n",
        )
        raw = bytearray()
        gate_ready_written = False
        verified_written = False
        with args.output.open("xb") as output:
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                readable, _, _ = select.select([fd], [], [], min(0.2, deadline - time.monotonic()))
                if readable:
                    data = os.read(fd, 4096)
                    if data:
                        raw.extend(data)
                        output.write(data)
                        output.flush()
                observation = observe_gate_sequence(raw.decode("ascii", errors="replace"), identity)
                if observation["boot_observed"] and not gate_ready_written:
                    write_new_text(
                        args.gate_ready_file,
                        "schema_version=phase1-gate-ready-v1\nboot_observed=1\n"
                        f"variant={identity['variant']}\nbuild_hash={identity['build_hash']}\n"
                        f"session={identity['session']}\nelf_sha256={identity['elf_sha256']}\n"
                        f"capture_gate_symbol={identity['gate_symbol']}\n"
                        f"capture_gate_address={identity['gate_address']}\n",
                    )
                    print(f"GATE_WRITE_READY file={args.gate_ready_file}", flush=True)
                    gate_ready_written = True
                if observation["gate_sequence_verified"] and not verified_written:
                    write_new_text(
                        args.verified_file,
                        "schema_version=phase1-gate-verified-v1\ngate_sequence_verified=1\n"
                        f"epoch_sequence={observation['epoch_sequence']}\n",
                    )
                    print(f"GATE_SEQUENCE_VERIFIED seq={observation['epoch_sequence']}", flush=True)
                    verified_written = True
                    if args.exit_on_verified:
                        break
        final_mask = modem_mask(fd)
    finally:
        os.close(fd)

    observation = observe_gate_sequence(raw.decode("ascii", errors="replace"), identity)
    receipt = {
        "schema_version": "phase1-candidate-gate-receipt-v1",
        "scope": "candidate-preflight",
        "candidate_label": CANDIDATE_LABEL,
        "started_at_unix": started,
        "ended_at_unix": time.time(),
        "bytes_received": len(raw),
        "uart_sha256": hashlib.sha256(raw).hexdigest(),
        "modem_policy": modem_policy,
        "modem_lines_before_close": f"0x{final_mask:08x}",
        "identity": identity,
        **observation,
    }
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return 0 if observation["gate_sequence_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

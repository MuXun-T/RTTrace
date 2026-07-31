#!/usr/bin/env python3
"""Record a nonformal, read-only UART observation for the Alientek candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import termios
import time
from pathlib import Path


EXPECTED_PORT = "/dev/ttyUSB0"
EXPECTED_BAUD = 115200


def configure_receiver(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP |
                  termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON |
                  termios.IXOFF | termios.IXANY)
    attrs[1] = 0
    attrs[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
    attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
    if hasattr(termios, "CRTSCTS"):
        attrs[2] &= ~termios.CRTSCTS
    if hasattr(termios, "HUPCL"):
        attrs[2] &= ~termios.HUPCL
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIFLUSH)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=EXPECTED_PORT)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    if args.port != EXPECTED_PORT:
        raise SystemExit(f"candidate preflight requires {EXPECTED_PORT}")
    if args.seconds <= 0:
        raise SystemExit("capture duration must be positive")
    if args.output.exists() or args.ready_file.exists() or args.receipt.exists():
        raise SystemExit("refusing to overwrite a preflight artifact")
    for path in (args.output, args.ready_file, args.receipt):
        path.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    fd = os.open(args.port, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_receiver(fd)
        args.ready_file.write_text(
            "candidate_label=alientek-elite-stm32f103ze-candidate\n"
            "port=/dev/ttyUSB0\n"
            "baud=115200\n"
            "format=8N1\n"
            "flow_control=none\n"
            "access_mode=read-only\n"
            "modem_lines=not_modified\n",
            encoding="ascii",
        )
        with args.output.open("xb") as output:
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                readable, _, _ = select.select([fd], [], [], min(0.2, deadline - time.monotonic()))
                if readable:
                    data = os.read(fd, 4096)
                    if data:
                        output.write(data)
    finally:
        os.close(fd)

    raw = args.output.read_bytes()
    receipt = {
        "candidate_label": "alientek-elite-stm32f103ze-candidate",
        "scope": "nonformal-electrical-link",
        "port": EXPECTED_PORT,
        "baud": EXPECTED_BAUD,
        "format": "8N1",
        "flow_control": "none",
        "access_mode": "read-only",
        "modem_lines": "not_modified",
        "started_at_unix": started,
        "ended_at_unix": time.time(),
        "bytes_received": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return 0 if raw else 1


if __name__ == "__main__":
    raise SystemExit(main())

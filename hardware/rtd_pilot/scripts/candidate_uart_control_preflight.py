#!/usr/bin/env python3
"""Keep one auditable, nonformal CH340 reader open for the Alientek candidate."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import select
import struct
import termios
import time
from pathlib import Path


CANDIDATE_LABEL = "alientek-elite-stm32f103ze-candidate"
STABLE_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
EXPECTED_PORT = "/dev/ttyUSB0"
EXPECTED_BAUD = 115200
MODEM_OUTPUT_BITS = termios.TIOCM_DTR | termios.TIOCM_RTS


def modem_mask(fd: int) -> int:
    value = fcntl.ioctl(fd, termios.TIOCMGET, struct.pack("I", 0))
    return struct.unpack("I", value)[0]


def set_modem_mask(fd: int, mask: int) -> int:
    if mask & ~MODEM_OUTPUT_BITS:
        raise ValueError("unsupported modem-line mask")
    fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack("I", MODEM_OUTPUT_BITS))
    if mask:
        fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", mask))
    return modem_mask(fd)


def configure_115200_8n1(fd: int) -> None:
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
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--modem-mask", type=lambda text: int(text, 0),
                        help="nonformal DTR/RTS output mask; omit for observe-only")
    args = parser.parse_args()

    if args.seconds <= 0:
        raise SystemExit("capture duration must be positive")
    if args.modem_mask is not None and args.modem_mask & ~MODEM_OUTPUT_BITS:
        raise SystemExit("modem mask may contain only DTR and RTS bits")
    if not Path(STABLE_PORT).is_symlink() or os.path.realpath(STABLE_PORT) != EXPECTED_PORT:
        raise SystemExit(f"stable CH340 binding does not resolve to {EXPECTED_PORT}")
    if any(path.exists() for path in (args.output, args.ready_file, args.receipt)):
        raise SystemExit("refusing to overwrite a preflight artifact")
    for path in (args.output, args.ready_file, args.receipt):
        path.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    fd = os.open(STABLE_PORT, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        mask_after_open = modem_mask(fd)
        configure_115200_8n1(fd)
        mask_after_configure = modem_mask(fd)
        mask_after_policy = mask_after_configure if args.modem_mask is None else set_modem_mask(fd, args.modem_mask)
        modem_policy = "observe-only" if args.modem_mask is None else f"explicit-mask-0x{args.modem_mask:08x}"
        args.ready_file.write_text(
            "candidate_label=" + CANDIDATE_LABEL + "\n"
            "stable_port=" + STABLE_PORT + "\n"
            "resolved_port=" + os.path.realpath(STABLE_PORT) + "\n"
            "baud=115200\nformat=8N1\nflow_control=none\n"
            "access_mode=read-only\nmodem_policy=" + modem_policy + "\n"
            f"modem_lines_after_open=0x{mask_after_open:08x}\n"
            f"modem_lines_after_configure=0x{mask_after_configure:08x}\n"
            f"modem_lines_after_policy=0x{mask_after_policy:08x}\n",
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
        mask_before_close = modem_mask(fd)
    finally:
        os.close(fd)

    raw = args.output.read_bytes()
    receipt = {
        "candidate_label": CANDIDATE_LABEL,
        "scope": "nonformal-electrical-link",
        "stable_port": STABLE_PORT,
        "resolved_port": EXPECTED_PORT,
        "baud": EXPECTED_BAUD,
        "format": "8N1",
        "flow_control": "none",
        "access_mode": "read-only",
        "modem_policy": modem_policy,
        "modem_lines_after_open": f"0x{mask_after_open:08x}",
        "modem_lines_after_configure": f"0x{mask_after_configure:08x}",
        "modem_lines_after_policy": f"0x{mask_after_policy:08x}",
        "modem_lines_before_close": f"0x{mask_before_close:08x}",
        "started_at_unix": started,
        "ended_at_unix": time.time(),
        "bytes_received": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return 0 if raw else 1


if __name__ == "__main__":
    raise SystemExit(main())

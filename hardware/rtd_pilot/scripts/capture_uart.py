#!/usr/bin/env python3
"""Capture UART to a fresh file. Empty output is an error, not a success."""
from __future__ import annotations
import argparse, fcntl, os, select, signal, struct, sys, termios, threading, time
from pathlib import Path

MODEM_OUTPUT_BITS = termios.TIOCM_DTR | termios.TIOCM_RTS
MODEM_LINE_POLICIES = {
    "preserve": (),
    "clear": ((termios.TIOCMBIC, MODEM_OUTPUT_BITS),),
    "set": ((termios.TIOCMBIS, MODEM_OUTPUT_BITS),),
    "clear-dtr": ((termios.TIOCMBIC, termios.TIOCM_DTR),),
    "clear-rts": ((termios.TIOCMBIC, termios.TIOCM_RTS),),
    "explicit-mask-0x2": (),
    "flash-reset": (),
    "flash-reset-rts-enable": (),
    "hold-flash-reset": (),
}

def modem_line_mask(fd: int) -> int:
    value = fcntl.ioctl(fd, termios.TIOCMGET, struct.pack("I", 0))
    return struct.unpack("I", value)[0]

def configure_modem_lines(fd: int, mode: str) -> tuple[int, int]:
    before = modem_line_mask(fd)
    if mode not in MODEM_LINE_POLICIES:
        raise ValueError(f"unknown modem-line mode: {mode}")
    if mode == "explicit-mask-0x2":
        fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack("I", MODEM_OUTPUT_BITS))
        fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_DTR))
    elif mode in {"flash-reset", "flash-reset-rts-enable", "hold-flash-reset"}:
        # On this board RTS selects Boot ROM vs Flash and DTR holds NRST.
        fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_RTS))
        fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_DTR))
        if mode != "hold-flash-reset":
            time.sleep(0.05)
            fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_DTR))
            if mode == "flash-reset-rts-enable":
                # Boot selection has been sampled at DTR release; restore RX-enable state immediately.
                fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_RTS))
    else:
        for request, bits in MODEM_LINE_POLICIES[mode]:
            fcntl.ioctl(fd, request, struct.pack("I", bits))
    return before, modem_line_mask(fd)

def release_held_flash_reset(fd: int, restore_rts: bool = False) -> int:
    fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_DTR))
    if restore_rts:
        fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_RTS))
    return modem_line_mask(fd)

def signal_ready(ready_file: Path | None, modem_before: int, modem_after: int) -> None:
    if ready_file is None:
        return
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ready_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="ascii") as ready:
        ready.write("uart_open_configured=1\n")
        ready.write(f"uart_modem_lines_before=0x{modem_before:08x}\n")
        ready.write(f"uart_modem_lines_after=0x{modem_after:08x}\n")

def capture_with_termios(port_name: str, baud: int, seconds: float, output, ready_file: Path | None = None, modem_lines: str = "preserve", access_mode: str = "read-only", release_on_sigusr1: bool = False, restore_rts_on_release: bool = False) -> None:
    if baud != 115200:
        raise SystemExit("Phase 1 fallback supports only frozen 115200 baud")
    if access_mode == "read-only":
        access_flags = os.O_RDONLY
    elif access_mode == "read-write":
        access_flags = os.O_RDWR
    else:
        raise ValueError(f"unknown access mode: {access_mode}")
    fd = os.open(port_name, access_flags | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF | termios.IXANY)
        attrs[1] = 0
        attrs[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
        attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
        if hasattr(termios, "CRTSCTS"): attrs[2] &= ~termios.CRTSCTS
        if hasattr(termios, "HUPCL"): attrs[2] &= ~termios.HUPCL
        attrs[3] = 0
        attrs[4] = termios.B115200; attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        modem_before, modem_after = configure_modem_lines(fd, modem_lines)
        # This signal is emitted only after the CH340 file descriptor is stable.
        # Keep this process alive; reopening the device is prohibited in a formal window.
        signal_ready(ready_file, modem_before, modem_after)
        deadline = time.monotonic() + seconds
        if release_on_sigusr1:
            if modem_lines != "hold-flash-reset":
                raise ValueError("SIGUSR1 release requires hold-flash-reset modem policy")
            released = threading.Event()
            signal.signal(signal.SIGUSR1, lambda _signum, _frame: released.set())
            while not released.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not released.is_set():
                raise SystemExit("held Flash reset was not released before UART capture deadline")
            print(f"uart_release_modem_lines=0x{release_held_flash_reset(fd, restore_rts_on_release):08x}", file=sys.stderr, flush=True)
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], min(.2, deadline - time.monotonic()))
            if ready:
                data = os.read(fd, 4096)
                if data:
                    output.write(data)
                    output.flush()
    finally:
        os.close(fd)

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="/dev/ttyUSB0", help="board CH340 device; only /dev/ttyUSB0 is accepted for pin-map v2")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--seconds", required=True, type=float)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--ready-file", type=Path, help="new file created after the persistent CH340 reader is configured")
    p.add_argument("--modem-lines", choices=tuple(MODEM_LINE_POLICIES), default="preserve", help="DTR/RTS policy; any non-preserve mode is preflight-only and must be retained as invalid evidence")
    p.add_argument("--access-mode", choices=("read-only", "read-write"), default="read-only", help="read-write is a retained preflight diagnostic; formal captures use read-only")
    p.add_argument("--release-on-sigusr1", action="store_true", help="preflight-only: hold Flash reset until this process receives SIGUSR1")
    p.add_argument("--restore-rts-on-release", action="store_true", help="preflight-only: restore RTS after SIGUSR1 release; default keeps Flash-select RTS state")
    a = p.parse_args()
    if a.port != "/dev/ttyUSB0": raise SystemExit("Phase 1 pin-map v2 requires board CH340 at /dev/ttyUSB0; record an audited fallback before changing it")
    if a.output.exists(): raise SystemExit(f"refusing to overwrite {a.output}")
    if a.seconds <= 0: raise SystemExit("UART capture duration must be positive")
    if a.ready_file and a.ready_file.exists(): raise SystemExit(f"refusing to overwrite {a.ready_file}")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open("xb") as out:
        # PySerial's CH340 open sequence pulses modem-control lines on this board.
        # The termios read-only path avoids DTR/RTS and HUPCL must stay disabled.
        capture_with_termios(a.port, a.baud, a.seconds, out, a.ready_file, a.modem_lines, a.access_mode, a.release_on_sigusr1, a.restore_rts_on_release)
    if a.output.stat().st_size == 0: raise SystemExit("UART capture empty; retained file is not valid")
    return 0
if __name__ == "__main__": sys.exit(main())

from __future__ import annotations

import errno
import os
import select
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

try:  # pragma: no cover - exercised when pyserial is installed
    import serial as pyserial
except ImportError:  # pragma: no cover - standard-library fallback is covered instead
    pyserial = None

try:  # pragma: no cover - unavailable on some non-POSIX hosts
    import termios
    import tty
except ImportError:  # pragma: no cover - standard-library fallback is unavailable on some hosts
    termios = None
    tty = None


class ChannelError(RuntimeError):
    """Raised when a mock channel cannot be opened or streamed."""


@dataclass(frozen=True)
class ChannelChunk:
    payload: bytes
    core_id: int | None = None


class FileChunkSource:
    def __init__(self, path: str | Path, read_size: int = 4096, core_id: int | None = None) -> None:
        target = Path(path)
        if read_size <= 0:
            raise ChannelError("read_size must be positive")
        self.path = target
        self.read_size = read_size
        self.core_id = core_id
        self.source_label = f"channel:file:{self.path}"

    def __iter__(self) -> Iterator[ChannelChunk]:
        if not self.path.exists():
            raise ChannelError(f"channel file not found: {self.path}")
        with self.path.open("rb") as handle:
            while True:
                payload = handle.read(self.read_size)
                if not payload:
                    break
                yield ChannelChunk(payload=payload, core_id=self.core_id)


class SerialChunkSource:
    def __init__(
        self,
        device: str | Path,
        read_size: int = 4096,
        timeout_s: float | None = 1.0,
        baudrate: int = 115200,
        core_id: int | None = None,
    ) -> None:
        if read_size <= 0:
            raise ChannelError("read_size must be positive")
        if timeout_s is not None and timeout_s <= 0:
            raise ChannelError("timeout_s must be positive when provided")
        if baudrate <= 0:
            raise ChannelError("baudrate must be positive")
        self.device = str(device)
        self.read_size = read_size
        self.timeout_s = timeout_s
        self.baudrate = baudrate
        self.core_id = core_id
        self.source_label = f"channel:serial:{self.device}"

    def _iter_pyserial(self) -> Iterator[ChannelChunk]:
        try:
            with pyserial.Serial(self.device, baudrate=self.baudrate, timeout=self.timeout_s) as handle:
                while True:
                    try:
                        payload = handle.read(self.read_size)
                    except Exception as exc:
                        # PTY peers report EOF as a readiness-with-no-data
                        # SerialException; treat that as normal stream end.
                        if "device reports readiness to read but returned no data" in str(exc):
                            break
                        raise
                    if not payload:
                        break
                    yield ChannelChunk(payload=payload, core_id=self.core_id)
        except Exception as exc:  # pragma: no cover - depends on optional pyserial runtime
            raise ChannelError(f"serial stream failed: {exc}") from exc

    def _configure_posix_serial(self, fd: int) -> None:
        if termios is None or tty is None:
            return
        tty.setraw(fd, when=termios.TCSANOW)
        attrs = termios.tcgetattr(fd)
        speed = getattr(termios, f"B{self.baudrate}", None)
        if speed is not None:
            attrs[4] = speed
            attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

    def _iter_posix(self) -> Iterator[ChannelChunk]:
        flags = os.O_RDONLY | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_NONBLOCK", 0)
        fd: int | None = None
        try:
            fd = os.open(self.device, flags)
            self._configure_posix_serial(fd)
            while True:
                ready, _, _ = select.select([fd], [], [], self.timeout_s)
                if not ready:
                    break
                try:
                    payload = os.read(fd, self.read_size)
                except OSError as exc:
                    if exc.errno in {errno.EIO, errno.EBADF}:
                        break
                    raise
                if not payload:
                    break
                yield ChannelChunk(payload=payload, core_id=self.core_id)
        except OSError as exc:
            raise ChannelError(f"serial stream failed: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)

    def __iter__(self) -> Iterator[ChannelChunk]:
        if pyserial is not None:
            yield from self._iter_pyserial()
            return
        if os.name != "posix":
            raise ChannelError("serial streaming requires pyserial on non-POSIX hosts")
        yield from self._iter_posix()


class SocketChunkSource:
    def __init__(
        self,
        host: str,
        port: int,
        recv_size: int = 4096,
        timeout_s: float | None = 5.0,
        core_id: int | None = None,
    ) -> None:
        if recv_size <= 0:
            raise ChannelError("recv_size must be positive")
        if port <= 0 or port > 65535:
            raise ChannelError(f"invalid port: {port}")
        self.host = host
        self.port = port
        self.recv_size = recv_size
        self.timeout_s = timeout_s
        self.core_id = core_id
        self.source_label = f"channel:socket://{host}:{port}"

    def __iter__(self) -> Iterator[ChannelChunk]:
        try:
            with socket.create_connection((self.host, self.port), self.timeout_s) as conn:
                if self.timeout_s is not None:
                    conn.settimeout(self.timeout_s)
                while True:
                    payload = conn.recv(self.recv_size)
                    if not payload:
                        break
                    yield ChannelChunk(payload=payload, core_id=self.core_id)
        except OSError as exc:
            raise ChannelError(f"socket stream failed: {exc}") from exc


class SocketTraceMockServer:
    def __init__(
        self,
        payload: bytes | bytearray | str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        send_size: int = 256,
        delay_s: float = 0.0,
        accept_timeout_s: float = 10.0,
    ) -> None:
        if send_size <= 0:
            raise ChannelError("send_size must be positive")
        if accept_timeout_s <= 0:
            raise ChannelError("accept_timeout_s must be positive")
        self.payload = self._load_payload(payload)
        self.host = host
        self.port = port
        self.send_size = send_size
        self.delay_s = delay_s
        self.accept_timeout_s = accept_timeout_s
        self._ready = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._server_socket: socket.socket | None = None
        self._error: Exception | None = None

    @staticmethod
    def _load_payload(payload: bytes | bytearray | str | Path) -> bytes:
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        return Path(payload).read_bytes()

    def start(self) -> "SocketTraceMockServer":
        if self._thread is not None:
            raise ChannelError("socket mock server already started")
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=self.accept_timeout_s)
        if not self._ready.is_set():
            raise ChannelError("socket mock server start timed out")
        if self._error is not None:
            raise ChannelError(f"socket mock server failed: {self._error}") from self._error
        return self

    def _serve(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                server.listen(1)
                server.settimeout(self.accept_timeout_s)
                self._server_socket = server
                self.port = server.getsockname()[1]
                self._ready.set()
                conn, _ = server.accept()
                with conn:
                    cursor = 0
                    while cursor < len(self.payload):
                        end = min(cursor + self.send_size, len(self.payload))
                        conn.sendall(self.payload[cursor:end])
                        cursor = end
                        if self.delay_s > 0:
                            time.sleep(self.delay_s)
        except Exception as exc:  # pragma: no cover - exercised via error propagation
            self._error = exc
            self._ready.set()
        finally:
            self._done.set()
            self._server_socket = None

    def wait(self, timeout_s: float | None = None) -> bool:
        completed = self._done.wait(timeout=timeout_s)
        if completed and self._error is not None:
            raise ChannelError(f"socket mock server failed: {self._error}") from self._error
        return completed

    def close(self) -> None:
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=self.accept_timeout_s)
            self._thread = None
        if self._error is not None:
            raise ChannelError(f"socket mock server failed: {self._error}") from self._error

    def __enter__(self) -> "SocketTraceMockServer":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

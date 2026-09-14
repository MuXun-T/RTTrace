from __future__ import annotations

import os
import pty
import tempfile
import threading
import time
import unittest
from pathlib import Path

from collector.channel import SocketTraceMockServer
from desktop.sample_data import write_scenario
from desktop.services import WorkspaceController
from parser import load_dataset


class OnlineChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _start_serial_writer(fd: int, payload: bytes, chunk_size: int = 17, delay_s: float = 0.001) -> threading.Thread:
        def _writer() -> None:
            try:
                time.sleep(0.2)
                cursor = 0
                while cursor < len(payload):
                    end = min(cursor + chunk_size, len(payload))
                    os.write(fd, payload[cursor:end])
                    cursor = end
                    if delay_s > 0:
                        time.sleep(delay_s)
            finally:
                pass

        thread = threading.Thread(target=_writer, daemon=True)
        thread.start()
        return thread

    def test_file_channel_matches_offline_dataset(self) -> None:
        trace_path = write_scenario(self.root / "online-file.trace", name="multi_core")
        offline = load_dataset(trace_path)
        self.assertTrue(offline.ok, offline.message)

        controller = WorkspaceController()
        loaded = controller.viz_LoadDatasetFromChannel(
            "file",
            {
                "path": str(trace_path),
                "read_size": 23,
            },
        )
        self.assertTrue(loaded.ok, loaded.message)
        online_bundle = controller.repository.get(loaded.data).artifact.bundle
        offline_bundle = offline.data.bundle
        self.assertEqual(
            [item.ref_key for item in online_bundle.event_stream],
            [item.ref_key for item in offline_bundle.event_stream],
        )
        self.assertEqual(
            [item.reason_code for item in online_bundle.untrusted_windows],
            [item.reason_code for item in offline_bundle.untrusted_windows],
        )

    def test_socket_channel_matches_offline_dataset(self) -> None:
        trace_path = write_scenario(self.root / "online-socket.trace", name="gap")
        offline = load_dataset(trace_path)
        self.assertTrue(offline.ok, offline.message)

        with SocketTraceMockServer(trace_path, send_size=19, delay_s=0.001) as server:
            controller = WorkspaceController()
            loaded = controller.viz_LoadDatasetFromChannel(
                "socket",
                {
                    "host": server.host,
                    "port": server.port,
                    "recv_size": 17,
                    "timeout_s": 5.0,
                },
            )
            self.assertTrue(loaded.ok, loaded.message)
            self.assertTrue(server.wait(timeout_s=5.0))

        online_bundle = controller.repository.get(loaded.data).artifact.bundle
        offline_bundle = offline.data.bundle
        self.assertEqual(
            [item.ref_key for item in online_bundle.event_stream],
            [item.ref_key for item in offline_bundle.event_stream],
        )
        self.assertEqual(
            [item.reason_code for item in online_bundle.untrusted_windows],
            [item.reason_code for item in offline_bundle.untrusted_windows],
        )

    @unittest.skipUnless(os.name == "posix", "serial channel test requires POSIX pseudo terminals")
    def test_serial_channel_matches_offline_dataset(self) -> None:
        trace_path = write_scenario(self.root / "online-serial.trace", name="multi_core")
        offline = load_dataset(trace_path)
        self.assertTrue(offline.ok, offline.message)

        master_fd, slave_fd = pty.openpty()
        serial_device = os.ttyname(slave_fd)
        # Give the reader a moment to open the PTY; bytes written before a
        # slave is opened are discarded by the kernel's PTY line discipline.
        writer = self._start_serial_writer(master_fd, trace_path.read_bytes(), delay_s=0.01)

        controller = WorkspaceController()
        loaded = controller.viz_LoadDatasetFromChannel(
            "serial",
            {
                "device": serial_device,
                "read_size": 29,
                "timeout_s": 1.0,
                "baudrate": 115200,
            },
        )
        self.assertTrue(loaded.ok, loaded.message)
        writer.join(timeout=5.0)
        self.assertFalse(writer.is_alive())
        os.close(slave_fd)
        os.close(master_fd)

        online_bundle = controller.repository.get(loaded.data).artifact.bundle
        offline_bundle = offline.data.bundle
        self.assertEqual(
            [item.ref_key for item in online_bundle.event_stream],
            [item.ref_key for item in offline_bundle.event_stream],
        )
        self.assertEqual(
            [item.reason_code for item in online_bundle.untrusted_windows],
            [item.reason_code for item in offline_bundle.untrusted_windows],
        )


if __name__ == "__main__":
    unittest.main()

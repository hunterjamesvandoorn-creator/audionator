from __future__ import annotations

import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from server import AudioUDPServer
from traffic_simulator import TrafficSimulator


def _wait_until(condition: Callable[[], bool], timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


class TrafficSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temporary_directory.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.original_data = bytes(range(256)) * 300
        (self.library / "test track.mp3").write_bytes(self.original_data)
        self.server = AudioUDPServer(("127.0.0.1", 0), self.library, video_cache_directory=self.root / "video-cache")
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.port = self.server.server_address[1]
        self.simulators: list[TrafficSimulator] = []

    def tearDown(self) -> None:
        for simulator in self.simulators:
            simulator.stop()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def _simulator(self, client_count: int, file_name: str | None) -> TrafficSimulator:
        work_directory = self.root / f"traffic-{len(self.simulators)}"
        simulator = TrafficSimulator("127.0.0.1", self.port, client_count, work_directory, file_name)
        self.simulators.append(simulator)
        return simulator

    def test_download_clients_complete_transfers_and_clean_up(self) -> None:
        simulator = self._simulator(3, "test track.mp3")
        simulator.start()
        self.assertTrue(_wait_until(lambda: simulator.status()["completed"] >= 6))

        simulator.stop()
        status = simulator.status()
        self.assertEqual(status["failed"], 0, status["last_error"])
        self.assertEqual(status["bytes"], status["completed"] * len(self.original_data))
        self.assertFalse(simulator.work_directory.exists())

    def test_idle_clients_hold_their_connections(self) -> None:
        simulator = self._simulator(2, None)
        simulator.start()
        self.assertTrue(_wait_until(lambda: simulator.status()["connected"] == 2))

        simulator.stop()
        status = simulator.status()
        self.assertEqual(status["connected"], 0)
        self.assertEqual(status["completed"], 0)

    def test_client_count_must_be_within_limits(self) -> None:
        for client_count in (0, 51):
            with self.subTest(client_count=client_count):
                with self.assertRaises(ValueError):
                    TrafficSimulator("127.0.0.1", self.port, client_count, self.root / "unused")


if __name__ == "__main__":
    unittest.main()

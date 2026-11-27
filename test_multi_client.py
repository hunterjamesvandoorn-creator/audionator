from __future__ import annotations

import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

from receiver import AudioReceiver
from server import AudioUDPServer


def _wait_for(receiver: AudioReceiver, names: tuple[str, ...], timeout: float = 10.0) -> tuple[str, dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event, payload = receiver.events.get(timeout=0.1)
        except queue.Empty:
            continue
        if event == "error":
            raise AssertionError(payload["message"])
        if event in names:
            return event, payload
    raise AssertionError(f"Timed out waiting for {names}.")


class MultiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.original_data = bytes(range(256)) * 300
        (self.library / "test track.mp3").write_bytes(self.original_data)
        self.server = AudioUDPServer(("127.0.0.1", 0), self.library, video_cache_directory=self.root / "video-cache")
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.receivers: list[AudioReceiver] = []

    def tearDown(self) -> None:
        for receiver in self.receivers:
            receiver.close()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def _connect_client(self, name: str) -> AudioReceiver:
        receiver = AudioReceiver(self.root / name)
        self.receivers.append(receiver)
        receiver.connect("127.0.0.1", self.server.server_address[1])
        _wait_for(receiver, ("accepted",))
        return receiver

    def test_every_client_sees_the_same_library(self) -> None:
        clients = [self._connect_client(f"client{index}") for index in range(3)]
        for client in clients:
            client.request_library()
        for client in clients:
            _, payload = _wait_for(client, ("library",))
            self.assertEqual([item["name"] for item in payload["files"]], ["test track.mp3"])

    def test_clients_receive_the_same_file_at_the_same_time(self) -> None:
        clients = [self._connect_client(f"client{index}") for index in range(3)]
        for client in clients:
            client.request_file("test track.mp3")
        for client in clients:
            _, payload = _wait_for(client, ("transfer_complete",), timeout=20)
            self.assertEqual(Path(payload["path"]).read_bytes(), self.original_data)

    def test_session_membership_is_shared_between_clients(self) -> None:
        host, guest, late = (self._connect_client(name) for name in ("host", "guest", "late"))

        host.create_session()
        _, payload = _wait_for(host, ("session",))
        code = payload["code"]
        self.assertEqual(payload["members"], 1)

        guest.join_session(code)
        _, payload = _wait_for(guest, ("session",))
        self.assertEqual(payload["members"], 2)

        late.join_session(code)
        _, payload = _wait_for(late, ("session",))
        self.assertEqual(payload["members"], 3)

        host.leave_session()
        _wait_for(host, ("left",))

        newcomer = self._connect_client("newcomer")
        newcomer.join_session(code)
        _, payload = _wait_for(newcomer, ("session",))
        self.assertEqual(payload["members"], 3)

    def test_a_client_that_disconnects_mid_transfer_does_not_affect_others(self) -> None:
        stays = self._connect_client("stays")
        leaves = self._connect_client("leaves")
        leaves.request_file("test track.mp3")
        leaves.close()

        stays.request_file("test track.mp3")
        _, payload = _wait_for(stays, ("transfer_complete",), timeout=20)
        self.assertEqual(Path(payload["path"]).read_bytes(), self.original_data)


if __name__ == "__main__":
    unittest.main()

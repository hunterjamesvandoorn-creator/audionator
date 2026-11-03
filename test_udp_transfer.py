from __future__ import annotations

import tempfile
import threading
import time
import unittest
import queue
from pathlib import Path

from receiver import AudioReceiver
from server import AudioUDPServer
from udp_protocol import CONTROL, DATA, encode_control, pack_message, unpack_message


class UDPProtocolTests(unittest.TestCase):
    def test_round_trip_control_and_binary_packets(self) -> None:
        transfer_id = bytes(range(16))
        for kind, sequence, payload in (
            (CONTROL, 0, encode_control({"command": "LIST"})),
            (DATA, 17, b"\x00\xffmp3"),
        ):
            packet = pack_message(kind, transfer_id, sequence, payload)
            self.assertEqual(unpack_message(packet), (kind, transfer_id, sequence, payload))

    def test_rejects_bad_packet_signature(self) -> None:
        with self.assertRaises(ValueError):
            unpack_message(b"nope" + bytes(20))


class UDPTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.library = root / "library"
        self.downloads = root / "downloads"
        self.library.mkdir()
        self.original_data = bytes(range(256)) * 300
        (self.library / "test track.mp3").write_bytes(self.original_data)
        self.server = AudioUDPServer(("127.0.0.1", 0), self.library)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.receiver = AudioReceiver(self.downloads)
        self.receiver.connect("127.0.0.1", self.server.server_address[1])

    def tearDown(self) -> None:
        self.receiver.close()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_lists_and_receives_mp3_over_udp(self) -> None:
        deadline = time.monotonic() + 5
        listed = False
        while time.monotonic() < deadline and not listed:
            try:
                event, payload = self.receiver.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if event == "library":
                listed = True
                self.assertEqual(payload["files"], [{"name": "test track.mp3", "size": len(self.original_data)}])
        self.assertTrue(listed, "Server did not return its MP3 library.")

        self.receiver.request_file("test track.mp3")
        deadline = time.monotonic() + 10
        received_path: Path | None = None
        while time.monotonic() < deadline and received_path is None:
            try:
                event, payload = self.receiver.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if event == "error":
                self.fail(payload["message"])
            if event == "transfer_complete":
                received_path = Path(payload["path"])

        self.assertIsNotNone(received_path, "MP3 transfer did not complete.")
        assert received_path is not None
        self.assertEqual(received_path.read_bytes(), self.original_data)


if __name__ == "__main__":
    unittest.main()

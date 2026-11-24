from __future__ import annotations

import tempfile
import threading
import time
import unittest
import queue
import socket
import uuid
from pathlib import Path

from receiver import AudioReceiver
from server import AudioUDPServer
from udp_protocol import (
    ACK,
    CONTROL,
    DATA,
    FINAL_ACK_SEQUENCE,
    FIN,
    encode_control,
    pack_message,
    unpack_message,
)


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
        (self.library / "sample video.mp4").write_bytes(b"placeholder")
        self.server = AudioUDPServer(("127.0.0.1", 0), self.library, video_cache_directory=root / "video-cache")
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
                self.assertEqual(
                    payload["files"],
                    [
                        {"name": "sample video.mp4", "size": len(b"placeholder"), "type": "video"},
                        {"name": "test track.mp3", "size": len(self.original_data), "type": "audio"},
                    ],
                )
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

    def test_transfer_stays_on_the_server_udp_port(self) -> None:
        transfer_id = uuid.uuid4().bytes
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.bind(("127.0.0.1", 0))
            client.settimeout(3)
            client.sendto(
                pack_message(CONTROL, transfer_id, payload=encode_control({"command": "GET", "name": "test track.mp3"})),
                self.server.server_address,
            )
            final_acknowledged = False
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not final_acknowledged:
                packet, address = client.recvfrom(2048)
                self.assertEqual(address[1], self.server.server_address[1])
                kind, received_id, sequence, _ = unpack_message(packet)
                self.assertEqual(received_id, transfer_id)
                if kind == DATA:
                    client.sendto(pack_message(ACK, transfer_id, sequence), address)
                elif kind == FIN:
                    client.sendto(pack_message(ACK, transfer_id, FINAL_ACK_SEQUENCE), address)
                    final_acknowledged = True
                else:
                    self.assertEqual(kind, CONTROL)
            self.assertTrue(final_acknowledged, "Server did not complete the UDP transfer.")


class SeparateMediaDirectoryTests(unittest.TestCase):
    def test_lists_audio_and_video_from_separate_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            music = root / "music"
            videos = root / "videos"
            music.mkdir()
            videos.mkdir()
            (music / "track.mp3").write_bytes(b"audio")
            (videos / "clip.mp4").write_bytes(b"video")

            server = AudioUDPServer(
                ("127.0.0.1", 0),
                music,
                videos,
                root / "video-cache",
            )
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            receiver = AudioReceiver(root / "downloads")
            try:
                receiver.connect("127.0.0.1", server.server_address[1])
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        event, payload = receiver.events.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if event == "library":
                        self.assertEqual(
                            payload["files"],
                            [
                                {"name": "clip.mp4", "size": 5, "type": "video"},
                                {"name": "track.mp3", "size": 5, "type": "audio"},
                            ],
                        )
                        break
                    if event == "error":
                        self.fail(payload["message"])
                else:
                    self.fail("Server did not return media from both directories.")
            finally:
                receiver.close()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

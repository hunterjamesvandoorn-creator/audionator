from __future__ import annotations

import tempfile
import threading
import time
import unittest
import queue
import socket
import uuid
from pathlib import Path
from unittest import mock

from receiver import AudioReceiver
from server import AudioUDPServer, SessionRegistry
from udp_protocol import (
    ACK,
    CONTROL,
    CONTROL_ACK,
    DATA,
    FINAL_ACK_SEQUENCE,
    FIN,
    HEARTBEAT,
    HEARTBEAT_ACK,
    decode_control,
    encode_control,
    pack_message,
    unpack_message,
)


def _next_packet_of_kind(sock: socket.socket, kind: int) -> tuple[bytes, tuple[str, int]]:
    while True:
        packet, address = sock.recvfrom(2048)
        if unpack_message(packet)[0] == kind:
            return packet, address


def _wait_for_event(receiver: AudioReceiver, *names: str, timeout: float = 5.0) -> tuple[str, dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event, payload = receiver.events.get(timeout=0.1)
        except queue.Empty:
            continue
        if event in names:
            return event, payload
    raise AssertionError(f"Timed out waiting for {names}.")


class UDPProtocolTests(unittest.TestCase):
    def test_round_trip_control_and_binary_packets(self) -> None:
        transfer_id = bytes(range(16))
        timestamp_us = 1_700_000_000_000_000
        for kind, sequence, payload in (
            (CONTROL, 0, encode_control({"command": "LIST"})),
            (DATA, 17, b"\x00\xffmp3"),
        ):
            packet = pack_message(kind, transfer_id, sequence, payload, timestamp_us)
            self.assertEqual(
                unpack_message(packet),
                (kind, transfer_id, sequence, timestamp_us, payload),
            )

    def test_default_timestamp_is_current_time(self) -> None:
        before = time.time_ns() // 1000
        packet = pack_message(DATA, bytes(16), 0, b"x")
        after = time.time_ns() // 1000
        _, _, _, timestamp_us, _ = unpack_message(packet)
        self.assertTrue(before <= timestamp_us <= after)

    def test_rejects_bad_packet_signature(self) -> None:
        with self.assertRaises(ValueError):
            unpack_message(b"nope" + bytes(20))


class SessionRegistryTests(unittest.TestCase):
    def test_create_join_and_leave_track_members(self) -> None:
        registry = SessionRegistry(timeout=5)
        host, guest = ("10.0.0.1", 1000), ("10.0.0.2", 1000)
        code, members = registry.create(host, now=0)
        self.assertEqual(members, 1)
        self.assertEqual(registry.join(guest, code.lower(), now=1), (code, 2))
        registry.leave(host, now=2)
        registry.leave(guest, now=2)
        self.assertIsNone(registry.join(guest, code, now=3))

    def test_joining_another_session_leaves_the_previous_one(self) -> None:
        registry = SessionRegistry(timeout=5)
        first, _ = registry.create(("10.0.0.1", 1000), now=0)
        second, _ = registry.create(("10.0.0.2", 1000), now=0)
        self.assertEqual(registry.join(("10.0.0.1", 1000), second, now=1), (second, 2))
        self.assertIsNone(registry.join(("10.0.0.3", 1000), first, now=1))

    def test_members_silent_past_the_timeout_are_removed(self) -> None:
        registry = SessionRegistry(timeout=5)
        silent, active, late = ("10.0.0.1", 1000), ("10.0.0.2", 1000), ("10.0.0.3", 1000)
        code, _ = registry.create(silent, now=0)
        registry.join(active, code, now=1)
        registry.touch(active, now=4)
        self.assertEqual(registry.join(late, code, now=6), (code, 2))


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
                kind, received_id, sequence, _, _ = unpack_message(packet)
                self.assertEqual(received_id, transfer_id)
                if kind == DATA:
                    client.sendto(pack_message(ACK, transfer_id, sequence), address)
                elif kind == FIN:
                    client.sendto(pack_message(ACK, transfer_id, FINAL_ACK_SEQUENCE), address)
                    final_acknowledged = True
                elif kind == CONTROL_ACK:
                    continue
                else:
                    self.assertEqual(kind, CONTROL)
                    client.sendto(pack_message(CONTROL_ACK, transfer_id, sequence), address)
            self.assertTrue(final_acknowledged, "Server did not complete the UDP transfer.")

    def test_server_accepts_the_connection_before_sending_the_library(self) -> None:
        names: list[str] = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "library" not in names:
            try:
                event, _ = self.receiver.events.get(timeout=0.1)
            except queue.Empty:
                continue
            names.append(event)
        self.assertEqual(names[:2], ["accepted", "library"])

    def test_session_create_join_and_leave_over_udp(self) -> None:
        guest = AudioReceiver(self.downloads)
        guest.connect("127.0.0.1", self.server.server_address[1])
        try:
            self.receiver.create_session()
            _, created = _wait_for_event(self.receiver, "session")
            code = created["code"]
            self.assertEqual(created["members"], 1)

            guest.join_session(code)
            _, joined = _wait_for_event(guest, "session")
            self.assertEqual((joined["code"], joined["members"]), (code, 2))

            guest.leave_session()
            _wait_for_event(guest, "left")
            self.receiver.leave_session()
            _wait_for_event(self.receiver, "left")

            guest.join_session(code)
            _, failure = _wait_for_event(guest, "error")
            self.assertEqual(failure["message"], "No session has that code.")
        finally:
            guest.close()


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


class ControlReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.fake_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.fake_server.bind(("127.0.0.1", 0))
        self.fake_server.settimeout(2)
        self.receiver = AudioReceiver(Path(self.temporary_directory.name))
        self.receiver.connect("127.0.0.1", self.fake_server.getsockname()[1])

    def tearDown(self) -> None:
        self.receiver.close()
        self.fake_server.close()
        self.temporary_directory.cleanup()

    def test_lost_connect_request_is_retransmitted_until_acknowledged(self) -> None:
        request, client_address = _next_packet_of_kind(self.fake_server, CONTROL)
        _, _, control_id, _, _ = unpack_message(request)
        retransmission, _ = _next_packet_of_kind(self.fake_server, CONTROL)
        self.assertEqual(retransmission, request)

        self.fake_server.sendto(pack_message(CONTROL_ACK, bytes(16), control_id), client_address)
        self.fake_server.settimeout(0.2)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                packet, _ = self.fake_server.recvfrom(2048)
            except socket.timeout:
                continue
            self.assertNotEqual(unpack_message(packet)[0], CONTROL)

    def test_duplicate_server_control_is_acknowledged_but_handled_once(self) -> None:
        request, client_address = _next_packet_of_kind(self.fake_server, CONTROL)
        _, _, request_id, _, _ = unpack_message(request)
        self.fake_server.sendto(pack_message(CONTROL_ACK, bytes(16), request_id), client_address)

        reply = pack_message(CONTROL, bytes(16), 1000, encode_control({"type": "LIST", "files": []}))
        self.fake_server.sendto(reply, client_address)
        self.fake_server.sendto(reply, client_address)

        acknowledgements = 0
        deadline = time.monotonic() + 3
        while acknowledgements < 2 and time.monotonic() < deadline:
            packet, _ = self.fake_server.recvfrom(2048)
            kind, _, sequence, _, _ = unpack_message(packet)
            if kind == CONTROL_ACK and sequence == 1000:
                acknowledgements += 1
        self.assertEqual(acknowledgements, 2)

        libraries = 0
        while True:
            try:
                event, _ = self.receiver.events.get(timeout=0.3)
            except queue.Empty:
                break
            if event == "library":
                libraries += 1
        self.assertEqual(libraries, 1)

    def test_connection_is_accepted_only_after_the_server_replies(self) -> None:
        connect_request, client_address = _next_packet_of_kind(self.fake_server, CONTROL)
        _, _, connect_id, _, connect_payload = unpack_message(connect_request)
        self.assertEqual(decode_control(connect_payload), {"command": "CONNECT"})
        self.fake_server.sendto(pack_message(CONTROL_ACK, bytes(16), connect_id), client_address)
        time.sleep(0.3)
        self.assertTrue(self.receiver.events.empty())

        self.fake_server.sendto(
            pack_message(CONTROL, bytes(16), 1001, encode_control({"type": "ACCEPT"})),
            client_address,
        )
        event, _ = self.receiver.events.get(timeout=3)
        self.assertEqual(event, "accepted")

        library_request, _ = _next_packet_of_kind(self.fake_server, CONTROL)
        self.assertEqual(decode_control(unpack_message(library_request)[4]), {"command": "LIST"})


class HeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.fake_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.fake_server.bind(("127.0.0.1", 0))
        self.fake_server.settimeout(2)
        self.receiver = AudioReceiver(Path(self.temporary_directory.name))
        self.receiver.connect("127.0.0.1", self.fake_server.getsockname()[1])

    def tearDown(self) -> None:
        self.receiver.close()
        self.fake_server.close()
        self.temporary_directory.cleanup()

    def _answer_next_heartbeat(self, delay: float = 0.0) -> None:
        while True:
            packet, client_address = self.fake_server.recvfrom(2048)
            kind, _, sequence, _, _ = unpack_message(packet)
            if kind == HEARTBEAT:
                time.sleep(delay)
                self.fake_server.sendto(pack_message(HEARTBEAT_ACK, bytes(16), sequence), client_address)
                return

    def _wait_for_event(self, name: str, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                event, _ = self.receiver.events.get(timeout=remaining)
            except queue.Empty:
                return False
            if event == name:
                return True

    def test_acknowledged_heartbeats_keep_the_connection_up(self) -> None:
        with mock.patch("receiver.HEARTBEAT_INTERVAL", 0.05), mock.patch("receiver.HEARTBEAT_TIMEOUT", 0.3):
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                self._answer_next_heartbeat()
            self.assertFalse(self._wait_for_event("connection_lost", timeout=0.1))

    def test_silent_server_is_reported_lost_then_restored(self) -> None:
        with mock.patch("receiver.HEARTBEAT_INTERVAL", 0.05), mock.patch("receiver.HEARTBEAT_TIMEOUT", 0.3):
            self.assertTrue(self._wait_for_event("connection_lost"))
            self._answer_next_heartbeat()
            self.assertTrue(self._wait_for_event("connection_restored"))

    def _wait_for_latency_samples(self, count: int, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        summary = self.receiver.latency_summary()
        while summary["samples"] < count and time.monotonic() < deadline:
            time.sleep(0.01)
            summary = self.receiver.latency_summary()
        return summary

    def test_latency_summary_reports_average_and_maximum_round_trip(self) -> None:
        self.assertEqual(self.receiver.latency_summary(), {"average_ms": None, "max_ms": None, "samples": 0})
        self._answer_next_heartbeat(delay=0.2)
        summary = self._wait_for_latency_samples(1)
        self.assertEqual(summary["samples"], 1)
        self.assertGreaterEqual(summary["max_ms"], 190)
        self.assertEqual(summary["average_ms"], summary["max_ms"])

        self._answer_next_heartbeat()
        summary = self._wait_for_latency_samples(2)
        self.assertEqual(summary["samples"], 2)
        self.assertGreaterEqual(summary["max_ms"], 190)
        self.assertLess(summary["average_ms"], summary["max_ms"])


class PacketLossTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.receiver = AudioReceiver(Path(self.temporary_directory.name))
        self.transfer_id = uuid.uuid4().bytes
        self.receiver._start_transfer(
            self.transfer_id,
            {
                "name": "track.mp3",
                "size": 12,
                "chunk_size": 4,
                "chunks": 3,
                "sha256": "0" * 64,
                "media_type": "audio",
                "buffer_bytes": 0,
            },
        )
        self.state = self.receiver._transfers[self.transfer_id]

    def tearDown(self) -> None:
        self.receiver.close()
        self.temporary_directory.cleanup()

    def test_no_packets_means_no_loss_counts(self) -> None:
        self.assertEqual(AudioReceiver().loss_counts(), (0, 0))

    def test_chunk_skipped_then_filled_is_counted_once(self) -> None:
        self.receiver._write_chunk(self.state, 0, b"aaaa")
        self.receiver._write_chunk(self.state, 2, b"cccc")
        self.assertEqual(self.receiver.loss_counts(), (1, 3))
        self.receiver._write_chunk(self.state, 1, b"bbbb")
        self.assertEqual(self.receiver.loss_counts(), (1, 3))

    def test_duplicate_chunks_are_not_counted_as_loss(self) -> None:
        self.receiver._write_chunk(self.state, 0, b"aaaa")
        self.receiver._write_chunk(self.state, 0, b"aaaa")
        self.assertEqual(self.receiver.loss_counts(), (0, 1))


class ServerControlReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        library = root / "library"
        library.mkdir()
        (library / "track.mp3").write_bytes(b"audio")
        self.server = AudioUDPServer(("127.0.0.1", 0), library, video_cache_directory=root / "video-cache")
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        self.client.settimeout(1)

    def tearDown(self) -> None:
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_duplicate_request_is_acknowledged_but_answered_once(self) -> None:
        request = pack_message(CONTROL, bytes(16), 7, encode_control({"command": "LIST"}))
        self.client.sendto(request, self.server.server_address)
        self.client.sendto(request, self.server.server_address)

        request_acknowledgements = 0
        reply_ids: set[int] = set()
        while True:
            try:
                packet, _ = self.client.recvfrom(2048)
            except socket.timeout:
                break
            kind, _, sequence, _, _ = unpack_message(packet)
            if kind == CONTROL_ACK and sequence == 7:
                request_acknowledgements += 1
            elif kind == CONTROL:
                reply_ids.add(sequence)
                self.client.sendto(pack_message(CONTROL_ACK, bytes(16), sequence), self.server.server_address)

        self.assertEqual(request_acknowledgements, 2)
        self.assertEqual(len(reply_ids), 1)

    def test_heartbeat_is_echoed_with_its_sequence(self) -> None:
        self.client.sendto(pack_message(HEARTBEAT, bytes(16), 42), self.server.server_address)
        packet, _ = self.client.recvfrom(2048)
        kind, _, sequence, _, _ = unpack_message(packet)
        self.assertEqual((kind, sequence), (HEARTBEAT_ACK, 42))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import os
import queue
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from audio_stream import StreamingAudioFile
from client import AudionatorAPI
from receiver import AudioReceiver, TransferSnapshot

PAYLOAD = bytes(i % 256 for i in range(1000))
TRANSFER_ID = uuid.UUID(int=0xABCDEF).bytes
TRANSFER_HEX = TRANSFER_ID.hex()


class FakeDownload:
    """Stands in for AudioReceiver: serves a settable number of downloaded bytes."""

    is_running = True

    def __init__(self, path: Path, size: int) -> None:
        self.path = path
        self.size = size
        self.available = 0

    def wait_for_transfer_bytes(self, transfer_id: bytes, minimum_bytes: int, timeout: float | None = None):
        if self.available < minimum_bytes:
            return None
        return TransferSnapshot(
            self.path, self.size, self.available, 0, self.available >= self.size, None
        )


class StreamingAudioFileTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "track.part"
        self.path.write_bytes(PAYLOAD)

    def _stream(self, available: int) -> tuple[StreamingAudioFile, FakeDownload]:
        download = FakeDownload(self.path, len(PAYLOAD))
        download.available = available
        return StreamingAudioFile(download, TRANSFER_ID, len(PAYLOAD)), download

    def test_reads_what_is_downloaded_then_waits_for_more(self) -> None:
        stream, download = self._stream(available=4)
        self.assertEqual(stream.read(100), PAYLOAD[:4])
        self.assertEqual(stream.read(100), b"")
        download.available = len(PAYLOAD)
        self.assertEqual(stream.read(), PAYLOAD[4:])
        self.assertEqual(stream.read(100), b"")

    def test_read_size_limits_output(self) -> None:
        stream, _ = self._stream(available=len(PAYLOAD))
        self.assertEqual(stream.read(5), PAYLOAD[:5])
        self.assertEqual(stream.read(), PAYLOAD[5:])

    def test_seek_and_tell_follow_file_semantics(self) -> None:
        stream, _ = self._stream(available=len(PAYLOAD))
        self.assertEqual(stream.seek(6), 6)
        self.assertEqual(stream.read(2), PAYLOAD[6:8])
        self.assertEqual(stream.tell(), 8)
        self.assertEqual(stream.seek(-10, os.SEEK_END), len(PAYLOAD) - 10)
        self.assertEqual(stream.read(), PAYLOAD[-10:])
        with self.assertRaises(ValueError):
            stream.seek(-1)

    def test_receiver_error_ends_the_stream(self) -> None:
        source = mock.Mock()
        source.wait_for_transfer_bytes.return_value = TransferSnapshot(
            self.path, len(PAYLOAD), 4, 0, False, "Connection lost"
        )
        stream = StreamingAudioFile(source, TRANSFER_ID, len(PAYLOAD))
        self.assertEqual(stream.read(100), b"")

    def test_read_retries_after_the_partial_file_is_renamed(self) -> None:
        missing = TransferSnapshot(self.root / "track.part.gone", len(PAYLOAD), len(PAYLOAD), 0, True, None)
        final = TransferSnapshot(self.path, len(PAYLOAD), len(PAYLOAD), 0, True, None)
        source = mock.Mock()
        source.wait_for_transfer_bytes.side_effect = [missing, final]
        stream = StreamingAudioFile(source, TRANSFER_ID, len(PAYLOAD))
        self.assertEqual(stream.read(), PAYLOAD)


class ReceiverHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.receiver = AudioReceiver(self.root)
        self.addCleanup(self.receiver.close)
        self.digest = hashlib.sha256(PAYLOAD).hexdigest()
        self.receiver._start_transfer(
            TRANSFER_ID,
            {
                "name": "track.mp3",
                "size": len(PAYLOAD),
                "chunk_size": 250,
                "chunks": 4,
                "sha256": self.digest,
                "media_type": "audio",
                "buffer_bytes": 0,
            },
        )

    def test_completed_audio_stays_readable_at_its_final_path(self) -> None:
        state = self.receiver._transfers[TRANSFER_ID]
        for sequence in range(4):
            start = sequence * 250
            self.receiver._write_chunk(state, sequence, PAYLOAD[start : start + 250])
        self.receiver._finish_transfer(TRANSFER_ID, 4, self.digest.encode("ascii"), ("127.0.0.1", 9))

        snapshot = self.receiver.wait_for_transfer_bytes(TRANSFER_ID, len(PAYLOAD), timeout=0)
        self.assertIsNotNone(snapshot)
        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.path, self.root / "track.mp3")
        self.assertEqual(snapshot.path.read_bytes(), PAYLOAD)
        self.assertFalse((self.root / f".{TRANSFER_HEX}.part").exists())

        events = []
        while not self.receiver.events.empty():
            events.append(self.receiver.events.get_nowait())
        self.assertIn(("transfer_complete", {"name": "track.mp3", "path": str(snapshot.path)}), events)


class StreamingStartTests(unittest.TestCase):
    def _patch(self, target: str, *args: object) -> mock.MagicMock:
        patcher = mock.patch(target, *args)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.track = self.root / "track.part"
        self.track.write_bytes(PAYLOAD)
        mp3 = self._patch("client.MP3")
        mp3.return_value.info.length = 180.0
        self.music = self._patch("client.pygame.mixer").music
        self._patch("client.AUDIO_START_BUFFER_BYTES", 100)
        self.download = FakeDownload(self.track, len(PAYLOAD))
        self.api = AudionatorAPI(self.root, mock.Mock(), self.root / "settings.json")
        self.api.receiver = self.download
        self.api.active_transfer_id = TRANSFER_HEX
        self.api.active_transfer_type = "audio"
        self.api.active_transfer_name = "track.mp3"

    def _start_stream(self) -> None:
        self.download.available = 100
        self.api._maybe_start_stream()
        self.assertEqual(self.api.streaming_transfer_id, TRANSFER_HEX)

    def test_streaming_starts_once_the_start_buffer_is_downloaded(self) -> None:
        self.api._maybe_start_stream()
        self.music.load.assert_not_called()

        self._start_stream()
        self.assertEqual(self.music.load.call_count, 1)
        self.assertIsInstance(self.music.load.call_args.args[0], StreamingAudioFile)
        self.music.play.assert_called_once_with(start=0.0)

        self.api._maybe_start_stream()
        self.assertEqual(self.music.load.call_count, 1)

    def test_resume_waits_for_the_bytes_at_the_saved_offset(self) -> None:
        self.api.playback_positions["track.mp3"] = 90.0
        self.download.available = 300
        self.api._maybe_start_stream()
        self.music.load.assert_not_called()

        self.download.available = 600
        self.api._maybe_start_stream()
        self.assertEqual(self.music.load.call_count, 1)
        self.assertEqual(self.music.play.call_args.kwargs["start"], 90.0)

    def test_seek_is_refused_until_the_target_is_downloaded(self) -> None:
        self._start_stream()
        self.music.set_pos.reset_mock()

        result = self.api.seek(150.0)
        self.assertFalse(result["ok"])
        self.assertIn("still downloading", self.api.message)
        self.music.set_pos.assert_not_called()

        self.download.available = len(PAYLOAD)
        self.assertTrue(self.api.seek(150.0)["ok"])
        self.music.set_pos.assert_called_once_with(150.0)

    def test_completed_download_does_not_restart_the_track(self) -> None:
        self._start_stream()
        self.api._handle_event("transfer_complete", {"name": "track.mp3", "path": str(self.track)})

        self.assertEqual(self.music.load.call_count, 1)
        self.assertIsNone(self.api.streaming_transfer_id)
        self.assertIsNone(self.api.active_transfer_id)
        self.assertEqual(self.api.current_path, self.track)

    def test_receiver_error_clears_the_streaming_state(self) -> None:
        self._start_stream()
        self.api._handle_event("error", {"message": "Connection lost"})

        self.assertIsNone(self.api.streaming_transfer_id)
        self.assertIsNone(self.api.active_transfer_id)

    def test_track_that_never_streamed_plays_when_complete(self) -> None:
        self.api._handle_event("transfer_complete", {"name": "track.mp3", "path": str(self.track)})

        self.music.load.assert_called_once_with(str(self.track))
        self.assertIsNone(self.api.streaming_transfer_id)


if __name__ == "__main__":
    unittest.main()

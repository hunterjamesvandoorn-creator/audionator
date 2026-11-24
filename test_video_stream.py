from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
import queue
from pathlib import Path
from urllib.request import Request, urlopen

import imageio_ffmpeg

from receiver import AudioReceiver, TransferSnapshot
from server import AudioUDPServer
from video_stream import PLAYER_HTML, ProgressiveVideoHTTPServer


class FakeProgressiveReceiver:
    def __init__(self, path: Path, size: int, buffer_bytes: int) -> None:
        self.condition = threading.Condition()
        self.snapshot = TransferSnapshot(path, size, buffer_bytes, buffer_bytes, False, None)

    def wait_for_transfer_bytes(
        self,
        _transfer_id: bytes,
        minimum_bytes: int,
        timeout: float | None = None,
    ) -> TransferSnapshot | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.condition:
            while (
                self.snapshot.available_bytes < minimum_bytes
                and not self.snapshot.complete
                and not self.snapshot.error
            ):
                if deadline is None:
                    self.condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            return self.snapshot

    def advance(self, available_bytes: int, complete: bool = False) -> None:
        with self.condition:
            self.snapshot = TransferSnapshot(
                self.snapshot.path,
                self.snapshot.size,
                available_bytes,
                self.snapshot.buffer_bytes,
                complete,
                None,
            )
            self.condition.notify_all()


class ProgressiveVideoHTTPTests(unittest.TestCase):
    def test_serves_available_bytes_before_transfer_completes(self) -> None:
        content = bytes(range(256)) * 16
        with tempfile.TemporaryDirectory() as temporary_directory:
            video_path = Path(temporary_directory) / "video.mp4"
            video_path.write_bytes(content)
            receiver = FakeProgressiveReceiver(video_path, len(content), 3)
            transfer_id = bytes(range(16))
            server = ProgressiveVideoHTTPServer(("127.0.0.1", 0), receiver, transfer_id)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            def continue_transfer() -> None:
                time.sleep(0.3)
                receiver.advance(128)
                time.sleep(0.3)
                receiver.advance(len(content), complete=True)

            transfer_thread = threading.Thread(target=continue_transfer, daemon=True)
            transfer_thread.start()
            try:
                with urlopen(f"{base_url}/player?name=video.mp4", timeout=5) as response:
                    player_page = response.read().decode("utf-8")
                self.assertIn("UDP VIDEO", player_page)
                with urlopen(f"{base_url}/video.mp4", timeout=5) as response:
                    self.assertEqual(response.headers.get("Content-Length"), str(len(content)))
                    first_bytes = response.read(3)
                    self.assertEqual(first_bytes, content[:3])
                    self.assertFalse(receiver.snapshot.complete)
                    self.assertEqual(first_bytes + response.read(), content)

                request = Request(f"{base_url}/video.mp4", headers={"Range": "bytes=5-12"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), content[5:13])
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)
                transfer_thread.join(timeout=2)


class VideoUDPTransferTests(unittest.TestCase):
    def test_mp4_is_remuxed_and_received_over_reliable_udp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            music = root / "music"
            videos = root / "videos"
            downloads = root / "downloads"
            music.mkdir()
            videos.mkdir()
            source = videos / "clip.mp4"
            subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x240:rate=24",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=44100",
                    "-t",
                    "2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            server = AudioUDPServer(("127.0.0.1", 0), music, videos, root / "video-cache")
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            receiver = AudioReceiver(downloads)
            try:
                receiver.connect("127.0.0.1", server.server_address[1])
                receiver.request_file(source.name)
                deadline = time.monotonic() + 30
                received_path: Path | None = None
                while time.monotonic() < deadline and received_path is None:
                    try:
                        event, payload = receiver.events.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if event == "error":
                        self.fail(payload["message"])
                    if event == "transfer_complete":
                        received_path = Path(payload["path"])

                self.assertIsNotNone(received_path, "MP4 transfer did not complete over UDP.")
                assert received_path is not None
                prepared = received_path.read_bytes()
                self.assertLess(prepared.find(b"moov"), prepared.find(b"mdat"))
            finally:
                receiver.close()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

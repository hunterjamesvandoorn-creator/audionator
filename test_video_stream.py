from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

import imageio_ffmpeg

from client import AudionatorAPI
from receiver import TransferSnapshot
from server import AudioUDPServer
from video_stream import ProgressiveVideoHTTPServer


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
                with urlopen(base_url, timeout=5) as response:
                    player_page = response.read().decode("utf-8")
                self.assertIn('data-view="videos"', player_page)
                self.assertIn('id="video-player"', player_page)
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
            player_server = ProgressiveVideoHTTPServer(("127.0.0.1", 0))
            player_thread = threading.Thread(target=player_server.serve_forever, daemon=True)
            player_thread.start()
            client = AudionatorAPI(downloads, player_server, root / "settings.json")
            try:
                self.assertTrue(client.connect("127.0.0.1", server.server_address[1])["ok"])
                self.assertEqual(
                    client.get_saved_connection(),
                    {"host": "127.0.0.1", "port": server.server_address[1]},
                )
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not client.get_state()["connected"]:
                    time.sleep(0.05)
                self.assertTrue(client.get_state()["connected"], "Client did not load the server library.")
                self.assertTrue(client.play_video(source.name)["ok"])
                receiver, transfer_id = player_server.get_video_source()
                self.assertIsNotNone(receiver)
                self.assertIsNotNone(transfer_id)
                assert receiver is not None and transfer_id is not None
                snapshot = receiver.wait_for_transfer_bytes(transfer_id, 1, timeout=30)
                self.assertIsNotNone(snapshot, "Video transfer did not start.")
                assert snapshot is not None
                complete = receiver.wait_for_transfer_bytes(transfer_id, snapshot.size, timeout=30)
                self.assertIsNotNone(complete, "Video transfer did not complete.")
                assert complete is not None and complete.path is not None
                with urlopen(f"http://127.0.0.1:{player_server.server_address[1]}/", timeout=5) as response:
                    player_page = response.read().decode("utf-8")
                self.assertIn('id="video-player"', player_page)
                with urlopen(f"http://127.0.0.1:{player_server.server_address[1]}/video.mp4", timeout=5) as response:
                    prepared = response.read()
                self.assertEqual(prepared, complete.path.read_bytes())
                self.assertLess(prepared.find(b"moov"), prepared.find(b"mdat"))
            finally:
                client.shutdown()
                player_server.shutdown()
                player_server.server_close()
                player_thread.join(timeout=2)
                transfer_deadline = time.monotonic() + 3
                while time.monotonic() < transfer_deadline:
                    with server._transfers_lock:
                        if not server._transfers:
                            break
                    time.sleep(0.05)
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

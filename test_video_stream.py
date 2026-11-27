from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import imageio_ffmpeg

from client import AudionatorAPI
from server import AudioUDPServer
from video_stream import VideoHTTPServer


def wait_for_manifest(base_url: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        with urlopen(f"{base_url}/video/manifest", timeout=5) as response:
            info = json.loads(response.read().decode("utf-8"))
        if info["status"] in {"ready", "error"} or time.monotonic() > deadline:
            return info
        time.sleep(0.2)


class FakeSession:
    def __init__(self, status: str = "ready", segment_error: Exception | None = None) -> None:
        self.status = status
        self.segment_error = segment_error

    def describe(self) -> dict:
        info = {"name": "clip.mp4", "status": self.status, "message": "", "traffic_bps": 0}
        if self.status == "ready":
            info.update(
                {
                    "segment_count": 2,
                    "segment_seconds": 6,
                    "last_segment_seconds": 6.0,
                    "codecs": "avc1.640028,mp4a.40.2",
                    "levels": [{"id": "480p", "height": 480, "bandwidth": 1296000}],
                    "mode": "auto",
                    "level": "480p",
                    "throughput_bps": 0,
                }
            )
        return info

    def fetch_segment(self, index: int) -> tuple[str, bytes]:
        if self.segment_error is not None:
            raise self.segment_error
        if not 0 <= index < 2:
            raise ValueError("That video segment does not exist.")
        return "480p", f"segment-{index}".encode("ascii")

    def fetch_init(self, level_id: str) -> bytes:
        if level_id != "480p":
            raise ValueError("Unknown video quality.")
        return b"init-480p"


class AdaptiveRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = VideoHTTPServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path: str) -> tuple[int, dict, bytes]:
        try:
            with urlopen(f"{self.base_url}{path}", timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_player_page_is_served(self) -> None:
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="video-player"', body)
        self.assertIn(b'id="video-quality"', body)

    def test_media_routes_report_no_open_video(self) -> None:
        self.assertEqual(self.get("/video/manifest")[0], 409)
        self.assertEqual(self.get("/video/segment/0")[0], 409)
        self.assertEqual(self.get("/video/init/480p")[0], 409)

    def test_manifest_reports_preparing_status(self) -> None:
        self.server.set_video_session(FakeSession(status="preparing"))
        status, _, body = self.get("/video/manifest")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "preparing")

    def test_manifest_reports_ready_details(self) -> None:
        self.server.set_video_session(FakeSession())
        status, _, body = self.get("/video/manifest")
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["segment_count"], 2)

    def test_segment_is_served_with_level_header(self) -> None:
        self.server.set_video_session(FakeSession())
        status, headers, body = self.get("/video/segment/1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Video-Level"], "480p")
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertEqual(body, b"segment-1")

    def test_bad_segment_index_is_not_found(self) -> None:
        self.server.set_video_session(FakeSession())
        self.assertEqual(self.get("/video/segment/9")[0], 404)
        self.assertEqual(self.get("/video/segment/abc")[0], 404)

    def test_init_segment_is_served_and_unknown_level_is_not_found(self) -> None:
        self.server.set_video_session(FakeSession())
        self.assertEqual(self.get("/video/init/1080p")[0], 404)
        status, _, body = self.get("/video/init/480p")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"init-480p")

    def test_segment_timeout_is_gateway_timeout(self) -> None:
        self.server.set_video_session(FakeSession(segment_error=TimeoutError("slow")))
        self.assertEqual(self.get("/video/segment/0")[0], 504)

    def test_failed_transfer_is_bad_gateway(self) -> None:
        self.server.set_video_session(FakeSession(segment_error=RuntimeError("broken")))
        self.assertEqual(self.get("/video/segment/0")[0], 502)

    def test_segment_while_preparing_is_service_unavailable(self) -> None:
        self.server.set_video_session(FakeSession(status="preparing", segment_error=RuntimeError("not yet")))
        self.assertEqual(self.get("/video/segment/0")[0], 503)

    def test_unknown_path_is_not_found(self) -> None:
        self.assertEqual(self.get("/video.mp4")[0], 404)


class VideoUDPTransferTests(unittest.TestCase):
    def test_mp4_is_prepared_and_segments_are_served_over_http(self) -> None:
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
            player_server = VideoHTTPServer(("127.0.0.1", 0))
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
                base_url = f"http://127.0.0.1:{player_server.server_address[1]}"
                with urlopen(f"{base_url}/", timeout=5) as response:
                    self.assertIn(b'id="video-player"', response.read())
                manifest = wait_for_manifest(base_url, timeout=90)
                self.assertEqual(manifest["status"], "ready", manifest.get("message"))
                self.assertEqual([level["id"] for level in manifest["levels"]], ["720p", "480p", "360p"])
                self.assertGreaterEqual(manifest["segment_count"], 1)
                with urlopen(f"{base_url}/video/init/{manifest['level']}", timeout=30) as response:
                    init = response.read()
                self.assertIn(b"moov", init)
                with urlopen(f"{base_url}/video/segment/0", timeout=30) as response:
                    level = response.headers.get("X-Video-Level")
                    segment = response.read()
                self.assertIn(level, {"720p", "480p", "360p"})
                self.assertIn(b"moof", segment)
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

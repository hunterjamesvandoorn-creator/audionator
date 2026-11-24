from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from receiver import AudioReceiver


PLAYER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Audionator video player</title>
  <style>
    :root { color-scheme: dark; font-family: "Segoe UI", Arial, sans-serif; background: #10110f; color: #f2f2e9; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 28px; }
    main { width: min(1120px, 100%); }
    .brand { margin-bottom: 16px; color: #d3f36b; font-size: 13px; font-weight: 700; letter-spacing: 1.2px; }
    h1 { margin: 0 0 7px; font-size: clamp(20px, 3vw, 29px); }
    #message { min-height: 24px; margin: 0 0 17px; color: #a7aa9f; font-size: 13px; }
    .screen { overflow: hidden; border: 1px solid #30332d; border-radius: 15px; background: #080908; box-shadow: 0 22px 65px #0008; }
    video { display: block; width: 100%; max-height: 72vh; aspect-ratio: 16 / 9; background: #050605; }
    .foot { margin-top: 15px; color: #777b71; font-size: 11px; }
  </style>
</head>
<body>
  <main>
    <div class="brand">AUDIONATOR · UDP VIDEO</div>
    <h1 id="title">Video</h1>
    <p id="message" role="status">Buffering video over reliable UDP…</p>
    <div class="screen"><video id="video" controls autoplay playsinline></video></div>
    <div class="foot">Playback starts after a short buffer. The player pauses if it needs more data.</div>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const video = document.getElementById("video");
    const message = document.getElementById("message");
    document.getElementById("title").textContent = params.get("name") || "Video";
    video.src = "/video.mp4";
    video.addEventListener("playing", () => { message.textContent = "Playing · receiving video over UDP"; });
    video.addEventListener("waiting", () => { message.textContent = "Buffering… playback will resume when data arrives."; });
    video.addEventListener("canplay", () => { message.textContent = "Ready to play"; });
    video.addEventListener("error", () => { message.textContent = "Could not play this MP4. Check the file format and server connection."; });
  </script>
</body>
</html>
"""

HTTP_BLOCK_SIZE = 64 * 1024


class ProgressiveVideoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        receiver: AudioReceiver,
        transfer_id: bytes,
    ) -> None:
        self.receiver = receiver
        self.transfer_id = transfer_id
        super().__init__(address, ProgressiveVideoRequestHandler)


class ProgressiveVideoRequestHandler(BaseHTTPRequestHandler):
    server: ProgressiveVideoHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/player":
            self._send_bytes(200, PLAYER_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif request.path == "/video.mp4":
            self._serve_video()
        else:
            self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        if urlsplit(self.path).path == "/video.mp4":
            self._serve_video(send_body=False)
        else:
            self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")

    def _serve_video(self, send_body: bool = True) -> None:
        snapshot = self.server.receiver.wait_for_transfer_bytes(
            self.server.transfer_id,
            1,
            timeout=60,
        )
        if snapshot is None:
            self._send_bytes(504, b"Video transfer did not start", "text/plain; charset=utf-8")
            return
        if snapshot.error or snapshot.path is None:
            message = (snapshot.error or "Video transfer failed.").encode("utf-8")
            self._send_bytes(502, message, "text/plain; charset=utf-8")
            return
        if snapshot.size == 0:
            self._send_range_error(snapshot.size)
            return

        range_header = self.headers.get("Range")
        try:
            start, end = self._parse_range(range_header, snapshot.size)
        except ValueError:
            self._send_range_error(snapshot.size)
            return

        required_bytes = max(start + 1, min(snapshot.buffer_bytes, snapshot.size) if start == 0 else 0)
        snapshot = self.server.receiver.wait_for_transfer_bytes(
            self.server.transfer_id,
            required_bytes,
            timeout=60,
        )
        if snapshot is None:
            self._send_bytes(504, b"Video buffer timed out", "text/plain; charset=utf-8")
            return
        if snapshot.error or snapshot.path is None:
            message = (snapshot.error or "Video transfer failed.").encode("utf-8")
            self._send_bytes(502, message, "text/plain; charset=utf-8")
            return

        status = 206 if range_header else 200
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{snapshot.size}")
        self.end_headers()
        if not send_body:
            return

        position = start
        while position <= end:
            snapshot = self.server.receiver.wait_for_transfer_bytes(
                self.server.transfer_id,
                position + 1,
                timeout=60,
            )
            if snapshot is None or snapshot.error or snapshot.path is None:
                return
            available_end = min(end + 1, snapshot.available_bytes)
            if available_end <= position:
                if snapshot.complete:
                    return
                continue
            with snapshot.path.open("rb") as source:
                source.seek(position)
                block = source.read(min(HTTP_BLOCK_SIZE, available_end - position))
            if not block:
                if snapshot.complete:
                    return
                continue
            try:
                self.wfile.write(block)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return
            position += len(block)

    @staticmethod
    def _parse_range(value: str | None, size: int) -> tuple[int, int]:
        if not value:
            return 0, size - 1
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
        if not match:
            raise ValueError("Unsupported byte range.")
        start_text, end_text = match.groups()
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        elif end_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError("Invalid suffix range.")
            start = max(0, size - suffix_length)
            end = size - 1
        else:
            raise ValueError("Invalid byte range.")
        if start >= size or end < start:
            raise ValueError("Byte range is outside the video.")
        return start, min(end, size - 1)

    def _send_range_error(self, size: int) -> None:
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def run_udp_video_player(host: str, port: int, name: str, download_directory: Path) -> None:
    try:
        import webview
    except ImportError as error:
        raise RuntimeError(f"The video player could not load its WebView2 component: {error}") from error

    receiver = AudioReceiver(download_directory)
    server: ProgressiveVideoHTTPServer | None = None
    server_thread: threading.Thread | None = None
    try:
        receiver.connect(host, port)
        transfer_id = receiver.request_file(name)
        server = ProgressiveVideoHTTPServer(("127.0.0.1", 0), receiver, transfer_id)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        player_url = f"http://127.0.0.1:{server.server_address[1]}/player?name={quote(name)}"
        try:
            webview.create_window(
                f"Audionator · {name}",
                url=player_url,
                width=1100,
                height=720,
                min_size=(760, 480),
            )
            webview.start()
        except (RuntimeError, webview.errors.WebViewException) as error:
            raise RuntimeError(f"The video player could not start: {error}") from error
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=2)
        receiver.close()

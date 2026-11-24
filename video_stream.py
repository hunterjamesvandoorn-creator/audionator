from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from client_ui import CLIENT_HTML
from receiver import AudioReceiver


HTTP_BLOCK_SIZE = 64 * 1024


class ProgressiveVideoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        receiver: AudioReceiver | None = None,
        transfer_id: bytes | None = None,
    ) -> None:
        self._video_source_lock = threading.Lock()
        self._receiver = receiver
        self._transfer_id = transfer_id
        super().__init__(address, ProgressiveVideoRequestHandler)

    def set_video_source(self, receiver: AudioReceiver, transfer_id: bytes) -> None:
        with self._video_source_lock:
            self._receiver = receiver
            self._transfer_id = transfer_id

    def clear_video_source(self) -> None:
        with self._video_source_lock:
            self._receiver = None
            self._transfer_id = None

    def get_video_source(self) -> tuple[AudioReceiver | None, bytes | None]:
        with self._video_source_lock:
            return self._receiver, self._transfer_id


class ProgressiveVideoRequestHandler(BaseHTTPRequestHandler):
    server: ProgressiveVideoHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if urlsplit(self.path).path in {"/", "/player"}:
            self._send_bytes(200, CLIENT_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif urlsplit(self.path).path == "/video.mp4":
            self._serve_video()
        else:
            self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        if urlsplit(self.path).path == "/video.mp4":
            self._serve_video(send_body=False)
        else:
            self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")

    def _serve_video(self, send_body: bool = True) -> None:
        receiver, transfer_id = self.server.get_video_source()
        if receiver is None or transfer_id is None:
            self._send_bytes(409, b"No video is open", "text/plain; charset=utf-8")
            return

        snapshot = receiver.wait_for_transfer_bytes(transfer_id, 1, timeout=60)
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
        snapshot = receiver.wait_for_transfer_bytes(transfer_id, required_bytes, timeout=60)
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
            snapshot = receiver.wait_for_transfer_bytes(transfer_id, position + 1, timeout=60)
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

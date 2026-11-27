from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from client_ui import CLIENT_HTML
from video_session import AdaptiveVideoSession


MEDIA_PREFIX_INIT = "/video/init/"
MEDIA_PREFIX_SEGMENT = "/video/segment/"
MAX_BUFFERED_SECONDS = 3600.0


def _buffered_seconds(query: str) -> float | None:
    """Read the player's buffered-ahead seconds from ?buffer=. Missing or invalid values mean unknown."""
    values = parse_qs(query).get("buffer")
    if not values:
        return None
    try:
        seconds = float(values[0])
    except ValueError:
        return None
    return seconds if 0 <= seconds <= MAX_BUFFERED_SECONDS else None


class VideoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        self._session_lock = threading.Lock()
        self._session: AdaptiveVideoSession | None = None
        super().__init__(address, VideoRequestHandler)

    def set_video_session(self, session: AdaptiveVideoSession) -> None:
        with self._session_lock:
            self._session = session

    def clear_video_session(self) -> None:
        with self._session_lock:
            self._session = None

    def get_video_session(self) -> AdaptiveVideoSession | None:
        with self._session_lock:
            return self._session


class VideoRequestHandler(BaseHTTPRequestHandler):
    server: VideoHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        if path in {"/", "/player"}:
            self._send_bytes(200, CLIENT_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/video/manifest":
            self._serve_manifest()
        elif path.startswith(MEDIA_PREFIX_INIT):
            level = path[len(MEDIA_PREFIX_INIT):]
            self._with_session(lambda session: ({}, session.fetch_init(level)))
        elif path.startswith(MEDIA_PREFIX_SEGMENT):
            index_text = path[len(MEDIA_PREFIX_SEGMENT):]
            if not re.fullmatch(r"\d{1,6}", index_text):
                self._send_text(404, "That video segment does not exist.")
                return
            self._with_session(self._segment_action(int(index_text), _buffered_seconds(parts.query)))
        else:
            self._send_text(404, "Not found")

    @staticmethod
    def _segment_action(index: int, buffered_seconds: float | None) -> Callable[[AdaptiveVideoSession], tuple[dict[str, str], bytes]]:
        def action(session: AdaptiveVideoSession) -> tuple[dict[str, str], bytes]:
            level, data = session.fetch_segment(index, buffered_seconds)
            return {"X-Video-Level": level}, data

        return action

    def _serve_manifest(self) -> None:
        session = self.server.get_video_session()
        if session is None:
            self._send_text(409, "No video is open.")
            return
        body = json.dumps(session.describe()).encode("utf-8")
        self._send_bytes(200, body, "application/json; charset=utf-8")

    def _with_session(
        self,
        action: Callable[[AdaptiveVideoSession], tuple[dict[str, str], bytes]],
    ) -> None:
        session = self.server.get_video_session()
        if session is None:
            self._send_text(409, "No video is open.")
            return
        try:
            headers, body = action(session)
        except ValueError as error:
            self._send_text(404, str(error))
            return
        except TimeoutError as error:
            self._send_text(504, str(error))
            return
        except RuntimeError as error:
            status = 503 if session.describe()["status"] == "preparing" else 502
            self._send_text(status, str(error))
            return
        self._send_bytes(200, body, "video/mp4", headers)

    def _send_text(self, status: int, message: str) -> None:
        self._send_bytes(status, message.encode("utf-8"), "text/plain; charset=utf-8")

    def _send_bytes(
        self,
        status: int,
        content: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: Any) -> None:
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)

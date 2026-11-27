from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import queue
import secrets
import socket
import socketserver
import threading
import time
from collections import OrderedDict
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from udp_protocol import (
    ACK,
    CHUNK_SIZE,
    CONTROL,
    CONTROL_ACK,
    CONTROL_MAX_RETRIES,
    CONTROL_RETRY_INTERVAL,
    DATA,
    FINAL_ACK_SEQUENCE,
    FIN,
    HEARTBEAT,
    HEARTBEAT_ACK,
    HEARTBEAT_TIMEOUT,
    WINDOW_SIZE,
    decode_control,
    encode_control,
    pack_message,
    unpack_message,
)
from video_service import INIT_SEGMENT_INDEX, VideoPreparationService, manifest_summary


TRANSFER_TIMEOUT = 30.0
RETRY_INTERVAL = 0.35
MAX_RETRIES = 30
VIDEO_START_BUFFER_BYTES = 2 * 1024 * 1024
CONTROL_HISTORY_SIZE = 1024
SESSION_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SESSION_CODE_LENGTH = 4


class SessionRegistry:
    def __init__(self, timeout: float = HEARTBEAT_TIMEOUT) -> None:
        self._timeout = timeout
        self._lock = threading.Lock()
        self._members: dict[str, dict[tuple[str, int], float]] = {}
        self._session_of: dict[tuple[str, int], str] = {}

    def create(self, client: tuple[str, int], now: float) -> tuple[str, int]:
        with self._lock:
            self._prune(now)
            self._leave(client)
            code = self._new_code()
            self._members[code] = {}
            self._join(client, code, now)
            return code, 1

    def join(self, client: tuple[str, int], code: Any, now: float) -> tuple[str, int] | None:
        if not isinstance(code, str):
            return None
        code = code.strip().upper()
        with self._lock:
            self._prune(now)
            if code not in self._members:
                return None
            self._leave(client)
            self._join(client, code, now)
            return code, len(self._members[code])

    def leave(self, client: tuple[str, int], now: float) -> None:
        with self._lock:
            self._prune(now)
            self._leave(client)

    def touch(self, client: tuple[str, int], now: float) -> None:
        with self._lock:
            code = self._session_of.get(client)
            if code is not None:
                self._members[code][client] = now

    def _join(self, client: tuple[str, int], code: str, now: float) -> None:
        self._members[code][client] = now
        self._session_of[client] = code

    def _leave(self, client: tuple[str, int]) -> None:
        code = self._session_of.pop(client, None)
        if code is None:
            return
        members = self._members[code]
        members.pop(client, None)
        if not members:
            del self._members[code]

    def _prune(self, now: float) -> None:
        for client, code in list(self._session_of.items()):
            if now - self._members[code][client] > self._timeout:
                self._leave(client)

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(SESSION_CODE_ALPHABET) for _ in range(SESSION_CODE_LENGTH))
            if code not in self._members:
                return code


class AudioUDPServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        library: Path,
        video_library: Path | None = None,
        video_cache_directory: Path | None = None,
    ) -> None:
        self.library = library.resolve()
        self.video_library = (video_library or library).resolve()
        self.library.mkdir(parents=True, exist_ok=True)
        self.video_library.mkdir(parents=True, exist_ok=True)
        self.video_service = VideoPreparationService(self.video_library, video_cache_directory)
        self._transfers_lock = threading.Lock()
        self._transfers: dict[bytes, tuple[tuple[str, int], queue.Queue[tuple[int, tuple[str, int]]]]] = {}
        self._control_lock = threading.Lock()
        self._control_ids = itertools.count(1)
        self._control_waiters: dict[tuple[tuple[str, int], int], threading.Event] = {}
        self._seen_controls: OrderedDict[tuple[tuple[str, int], int], None] = OrderedDict()
        self.sessions = SessionRegistry()
        super().__init__(address, AudioUDPHandler)

    def register_transfer(
        self,
        transfer_id: bytes,
        client_address: tuple[str, int],
    ) -> queue.Queue[tuple[int, tuple[str, int]]]:
        acknowledgements: queue.Queue[tuple[int, tuple[str, int]]] = queue.Queue()
        with self._transfers_lock:
            self._transfers[transfer_id] = (client_address, acknowledgements)
        return acknowledgements

    def unregister_transfer(self, transfer_id: bytes) -> None:
        with self._transfers_lock:
            self._transfers.pop(transfer_id, None)

    def route_acknowledgement(
        self,
        transfer_id: bytes,
        sequence: int,
        client_address: tuple[str, int],
    ) -> None:
        with self._transfers_lock:
            transfer = self._transfers.get(transfer_id)
        if transfer and transfer[0] == client_address:
            transfer[1].put((sequence, client_address))

    def next_control_id(self) -> int:
        with self._control_lock:
            return next(self._control_ids)

    def expect_control_acknowledgement(
        self,
        client_address: tuple[str, int],
        control_id: int,
    ) -> threading.Event:
        acknowledged = threading.Event()
        with self._control_lock:
            self._control_waiters[(client_address, control_id)] = acknowledged
        return acknowledged

    def forget_control_acknowledgement(self, client_address: tuple[str, int], control_id: int) -> None:
        with self._control_lock:
            self._control_waiters.pop((client_address, control_id), None)

    def route_control_acknowledgement(self, client_address: tuple[str, int], control_id: int) -> None:
        with self._control_lock:
            acknowledged = self._control_waiters.get((client_address, control_id))
        if acknowledged is not None:
            acknowledged.set()

    def remember_control(self, client_address: tuple[str, int], control_id: int) -> bool:
        key = (client_address, control_id)
        with self._control_lock:
            if key in self._seen_controls:
                return False
            self._seen_controls[key] = None
            if len(self._seen_controls) > CONTROL_HISTORY_SIZE:
                self._seen_controls.popitem(last=False)
            return True


class AudioUDPHandler(socketserver.BaseRequestHandler):
    server: AudioUDPServer

    def handle(self) -> None:
        packet, _server_socket = self.request
        client_address = self.client_address
        try:
            kind, transfer_id, sequence, _timestamp_us, payload = unpack_message(packet)
        except ValueError:
            return
        self.server.sessions.touch(client_address, time.monotonic())
        if kind == ACK:
            self.server.route_acknowledgement(transfer_id, sequence, client_address)
            return
        if kind == CONTROL_ACK:
            self.server.route_control_acknowledgement(client_address, sequence)
            return
        if kind == HEARTBEAT:
            self.server.socket.sendto(pack_message(HEARTBEAT_ACK, transfer_id, sequence), client_address)
            return
        if kind != CONTROL:
            return
        try:
            message = decode_control(payload)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return
        self.server.socket.sendto(pack_message(CONTROL_ACK, transfer_id, sequence), client_address)
        if not self.server.remember_control(client_address, sequence):
            return

        command = message.get("command")
        if command == "LIST":
            self._send_control(
                client_address,
                transfer_id,
                {
                    "type": "LIST",
                    "files": sorted(
                        [
                            {
                                "name": path.name,
                                "size": path.stat().st_size,
                                "type": media_type,
                            }
                            for directory, extension, media_type in (
                                (self.server.library, ".mp3", "audio"),
                                (self.server.video_library, ".mp4", "video"),
                            )
                            for path in directory.iterdir()
                            if path.is_file() and path.suffix.lower() == extension
                        ],
                        key=lambda item: item["name"].lower(),
                    ),
                },
            )
        elif command == "CREATE_SESSION":
            code, members = self.server.sessions.create(client_address, time.monotonic())
            self._send_control(client_address, transfer_id, {"type": "SESSION", "code": code, "members": members})
        elif command == "JOIN_SESSION":
            joined = self.server.sessions.join(client_address, message.get("code"), time.monotonic())
            if joined is None:
                self._send_control(client_address, transfer_id, {"type": "ERROR", "message": "No session has that code."})
            else:
                code, members = joined
                self._send_control(client_address, transfer_id, {"type": "SESSION", "code": code, "members": members})
        elif command == "LEAVE_SESSION":
            self.server.sessions.leave(client_address, time.monotonic())
            self._send_control(client_address, transfer_id, {"type": "LEFT"})
        elif command == "CONNECT":
            self._send_control(client_address, transfer_id, {"type": "ACCEPT"})
        elif command == "GET":
            name = message.get("name")
            try:
                path, media_type = self._find_media_file(name)
            except (OSError, RuntimeError, ValueError) as error:
                self._send_control(client_address, transfer_id, {"type": "ERROR", "message": str(error)})
                return
            if media_type == "video":
                self._send_control(
                    client_address,
                    transfer_id,
                    {"type": "ERROR", "message": "Videos are streamed as adaptive segments."},
                )
                return
            threading.Thread(
                target=self._send_file,
                args=(path, transfer_id, client_address),
                daemon=True,
            ).start()
        elif command == "MANIFEST":
            name = message.get("name")
            try:
                video_path = self._find_video_file(name)
                prepared = self.server.video_service.prepare(video_path.name)
            except (OSError, RuntimeError, ValueError) as error:
                self._send_control(client_address, transfer_id, {"type": "ERROR", "message": str(error)})
                return
            self._send_control(
                client_address,
                transfer_id,
                {"type": "MANIFEST", "name": video_path.name, **manifest_summary(prepared.manifest)},
            )
        elif command == "SEGMENT":
            name = message.get("name")
            try:
                video_path = self._find_video_file(name)
                segment = self.server.video_service.segment_path(
                    video_path.name,
                    message.get("level"),
                    message.get("index"),
                )
            except (OSError, RuntimeError, ValueError) as error:
                self._send_control(client_address, transfer_id, {"type": "ERROR", "message": str(error)})
                return
            level = message.get("level")
            index = message.get("index")
            display_name = (
                f"{level}-init.mp4" if index == INIT_SEGMENT_INDEX else f"{level}-{index:05d}.mp4"
            )
            threading.Thread(
                target=self._send_file,
                args=(segment, transfer_id, client_address, display_name),
                daemon=True,
            ).start()
        else:
            self._send_control(
                client_address,
                transfer_id,
                {"type": "ERROR", "message": "Unknown server command."},
            )

    def _find_video_file(self, name: Any) -> Path:
        path, media_type = self._find_media_file(name)
        if media_type != "video":
            raise ValueError("That file is not a video.")
        return path

    def _find_media_file(self, name: Any) -> tuple[Path, str]:
        if not isinstance(name, str) or not name or Path(name).name != name or "\x00" in name:
            raise ValueError("Invalid media file name.")
        extension = Path(name).suffix.lower()
        if extension == ".mp3":
            library = self.server.library
            media_type = "audio"
        elif extension == ".mp4":
            library = self.server.video_library
            media_type = "video"
        else:
            raise ValueError("Only MP3 audio and MP4 video files are available.")
        library = library.resolve()
        path = (library / name).resolve()
        if path.parent != library or not path.is_file():
            raise ValueError("That file is not in the server's media library.")
        return path, media_type

    def _send_control(
        self,
        client_address: tuple[str, int],
        transfer_id: bytes,
        message: dict[str, Any],
    ) -> bool:
        control_id = self.server.next_control_id()
        packet = pack_message(CONTROL, transfer_id, control_id, encode_control(message))
        acknowledged = self.server.expect_control_acknowledgement(client_address, control_id)
        try:
            for _ in range(CONTROL_MAX_RETRIES):
                self.server.socket.sendto(packet, client_address)
                if acknowledged.wait(CONTROL_RETRY_INTERVAL):
                    return True
            return False
        finally:
            self.server.forget_control_acknowledgement(client_address, control_id)

    def _send_file(
        self,
        path: Path,
        transfer_id: bytes,
        client_address: tuple[str, int],
        display_name: str | None = None,
    ) -> None:
        try:
            self._transfer_file(path, transfer_id, client_address, display_name)
        except OSError as error:
            self._send_control(
                client_address,
                transfer_id,
                {"type": "ERROR", "message": f"Media transfer failed: {error}"},
            )

    def _transfer_file(
        self,
        path: Path,
        transfer_id: bytes,
        client_address: tuple[str, int],
        display_name: str | None = None,
    ) -> None:
        name = display_name or path.name
        size = path.stat().st_size
        chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        digest = self._sha256(path)
        media_type = "video" if Path(name).suffix.lower() == ".mp4" else "audio"
        buffer_bytes = min(size, VIDEO_START_BUFFER_BYTES) if media_type == "video" else 0
        acknowledgements = self.server.register_transfer(transfer_id, client_address)
        pending: dict[int, tuple[bytes, float, int]] = {}
        next_sequence = 0
        acknowledged: set[int] = set()
        last_activity = time.monotonic()

        try:
            if not self._send_control(
                client_address,
                transfer_id,
                {
                    "type": "START",
                    "name": name,
                    "size": size,
                    "chunk_size": CHUNK_SIZE,
                    "chunks": chunks,
                    "sha256": digest,
                    "media_type": media_type,
                    "buffer_bytes": buffer_bytes,
                },
            ):
                return
            with path.open("rb") as audio_file:
                while len(acknowledged) < chunks:
                    while next_sequence < chunks and len(pending) < WINDOW_SIZE:
                        audio_file.seek(next_sequence * CHUNK_SIZE)
                        chunk = audio_file.read(CHUNK_SIZE)
                        packet = pack_message(DATA, transfer_id, next_sequence, chunk)
                        self.server.socket.sendto(packet, client_address)
                        pending[next_sequence] = (packet, time.monotonic(), 0)
                        next_sequence += 1

                    try:
                        sequence, response_address = acknowledgements.get(timeout=0.1)
                        if response_address == client_address and sequence in pending:
                            del pending[sequence]
                            acknowledged.add(sequence)
                            last_activity = time.monotonic()
                    except queue.Empty:
                        pass

                    now = time.monotonic()
                    for sequence, (packet, sent_at, retries) in list(pending.items()):
                        if now - sent_at < RETRY_INTERVAL:
                            continue
                        if retries >= MAX_RETRIES:
                            self._send_control(
                                client_address,
                                transfer_id,
                                {"type": "ERROR", "message": "Media transfer stopped after repeated packet loss."},
                            )
                            return
                        self.server.socket.sendto(packet, client_address)
                        pending[sequence] = (packet, now, retries + 1)

                    if now - last_activity > TRANSFER_TIMEOUT:
                        self._send_control(
                            client_address,
                            transfer_id,
                            {"type": "ERROR", "message": "Media transfer timed out waiting for the client."},
                        )
                        return

                final_packet = pack_message(FIN, transfer_id, chunks, digest.encode("ascii"))
                for _ in range(MAX_RETRIES):
                    self.server.socket.sendto(final_packet, client_address)
                    try:
                        sequence, response_address = acknowledgements.get(timeout=0.1)
                        if (
                            response_address == client_address
                            and sequence == FINAL_ACK_SEQUENCE
                        ):
                            return
                    except queue.Empty:
                        continue
                self._send_control(
                    client_address,
                    transfer_id,
                    {"type": "ERROR", "message": "Server could not confirm the completed media transfer."},
                )
        finally:
            self.server.unregister_transfer(transfer_id)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as audio_file:
            for block in iter(lambda: audio_file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


def _prepare_library(service: VideoPreparationService) -> None:
    for name in service.video_names():
        started = time.monotonic()
        try:
            service.prepare(name)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Could not prepare {name}: {error}")
            continue
        print(f"Prepared {name} in {time.monotonic() - started:.0f} s")
    print("All videos are prepared for adaptive streaming.")


def run_udp_server(
    host: str,
    port: int,
    library: Path,
    video_library: Path | None = None,
) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("UDP port must be between 1 and 65535.")
    server = AudioUDPServer((host, port), library, video_library)
    print(f"Audionator reliable UDP server listening on {host}:{port}")
    print(f"Audio library: {server.library}")
    print(f"Video library: {server.video_library}")
    print("Videos are encoded at 720p, 480p and 360p in the background. Clients can start before this finishes.")
    threading.Thread(target=_prepare_library, args=(server.video_service,), daemon=True).start()
    print("Share the server computer's LAN IP with the receiving client. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Audionator UDP server.")
    finally:
        server.server_close()


def run_web_preview(host: str, port: int) -> None:
    directory = Path(__file__).resolve().parent
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Audionator UI preview running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Audionator UI preview.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Audionator preview or MP3 UDP server.")
    parser.add_argument("--udp", action="store_true", help="Serve media over reliable UDP instead of the web preview.")
    parser.add_argument("--host", default=None, help="Bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=None, help="Port (UDP default: 5005; web default: 8000)")
    parser.add_argument(
        "--library",
        type=Path,
        default=Path(__file__).resolve().parent / "music",
        help="Folder containing MP3 files (UDP mode only)",
    )
    parser.add_argument(
        "--video-library",
        type=Path,
        default=Path(__file__).resolve().parent / "videos",
        help="Folder containing MP4 files (UDP mode only)",
    )
    args = parser.parse_args()

    host = args.host or "127.0.0.1"
    port = args.port or (5005 if args.udp else 8000)
    if args.udp:
        run_udp_server(host, port, args.library, args.video_library)
    else:
        run_web_preview(host, port)


if __name__ == "__main__":
    main()

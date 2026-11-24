from __future__ import annotations

import argparse
import hashlib
import json
import queue
import socket
import socketserver
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from udp_protocol import (
    ACK,
    CHUNK_SIZE,
    CONTROL,
    DATA,
    FINAL_ACK_SEQUENCE,
    FIN,
    WINDOW_SIZE,
    decode_control,
    encode_control,
    pack_message,
    unpack_message,
)
from video_service import VideoPreparationService


TRANSFER_TIMEOUT = 30.0
RETRY_INTERVAL = 0.35
MAX_RETRIES = 30
VIDEO_START_BUFFER_BYTES = 2 * 1024 * 1024


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


class AudioUDPHandler(socketserver.BaseRequestHandler):
    server: AudioUDPServer

    def handle(self) -> None:
        packet, _server_socket = self.request
        client_address = self.client_address
        try:
            kind, transfer_id, sequence, payload = unpack_message(packet)
        except ValueError:
            return
        if kind == ACK:
            self.server.route_acknowledgement(transfer_id, sequence, client_address)
            return
        if kind != CONTROL:
            return
        try:
            message = decode_control(payload)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
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
        elif command == "GET":
            name = message.get("name")
            try:
                path, media_type = self._find_media_file(name)
                if media_type == "video":
                    path = self.server.video_service.prepare(path.name)
            except (OSError, RuntimeError, ValueError) as error:
                self._send_control(client_address, transfer_id, {"type": "ERROR", "message": str(error)})
                return
            threading.Thread(
                target=self._send_file,
                args=(path, transfer_id, client_address),
                daemon=True,
            ).start()
        else:
            self._send_control(
                client_address,
                transfer_id,
                {"type": "ERROR", "message": "Unknown server command."},
            )

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
    ) -> None:
        self.server.socket.sendto(
            pack_message(CONTROL, transfer_id, payload=encode_control(message)),
            client_address,
        )

    def _send_file(
        self,
        path: Path,
        transfer_id: bytes,
        client_address: tuple[str, int],
    ) -> None:
        try:
            self._transfer_file(path, transfer_id, client_address)
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
    ) -> None:
        size = path.stat().st_size
        chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        digest = self._sha256(path)
        media_type = "video" if path.suffix.lower() == ".mp4" else "audio"
        buffer_bytes = min(size, VIDEO_START_BUFFER_BYTES) if media_type == "video" else 0
        acknowledgements = self.server.register_transfer(transfer_id, client_address)
        pending: dict[int, tuple[bytes, float, int]] = {}
        next_sequence = 0
        acknowledged: set[int] = set()
        last_activity = time.monotonic()

        try:
            self._send_control(
                client_address,
                transfer_id,
                {
                    "type": "START",
                    "name": path.name,
                    "size": size,
                    "chunk_size": CHUNK_SIZE,
                    "chunks": chunks,
                    "sha256": digest,
                    "media_type": media_type,
                    "buffer_bytes": buffer_bytes,
                },
            )
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
    print("MP4 videos are prepared for progressive playback the first time a client opens them.")
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

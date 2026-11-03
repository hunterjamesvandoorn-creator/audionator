from __future__ import annotations

import argparse
import hashlib
import json
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


TRANSFER_TIMEOUT = 30.0
RETRY_INTERVAL = 0.35
MAX_RETRIES = 30


class AudioUDPServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], library: Path) -> None:
        self.library = library.resolve()
        self.library.mkdir(parents=True, exist_ok=True)
        super().__init__(address, AudioUDPHandler)


class AudioUDPHandler(socketserver.BaseRequestHandler):
    server: AudioUDPServer

    def handle(self) -> None:
        packet, _server_socket = self.request
        client_address = self.client_address
        try:
            kind, transfer_id, _, payload = unpack_message(packet)
            if kind != CONTROL:
                return
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
                    "files": [
                        {"name": path.name, "size": path.stat().st_size}
                        for path in sorted(self.server.library.iterdir(), key=lambda item: item.name.lower())
                        if path.is_file() and path.suffix.lower() == ".mp3"
                    ],
                },
            )
        elif command == "GET":
            name = message.get("name")
            try:
                audio_path = self._find_audio_file(name)
            except (OSError, ValueError) as error:
                self._send_control(client_address, transfer_id, {"type": "ERROR", "message": str(error)})
                return
            threading.Thread(
                target=self._send_file,
                args=(audio_path, transfer_id, client_address),
                daemon=True,
            ).start()
        else:
            self._send_control(
                client_address,
                transfer_id,
                {"type": "ERROR", "message": "Unknown server command."},
            )

    def _find_audio_file(self, name: Any) -> Path:
        if not isinstance(name, str) or not name or Path(name).name != name or "\x00" in name:
            raise ValueError("Invalid audio file name.")
        if Path(name).suffix.lower() != ".mp3":
            raise ValueError("Only MP3 files are available.")
        library = self.server.library.resolve()
        path = (library / name).resolve()
        if path.parent != library or not path.is_file():
            raise ValueError("That MP3 is not in the server library.")
        return path

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
                {"type": "ERROR", "message": f"MP3 transfer failed: {error}"},
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
            },
        )
        pending: dict[int, tuple[bytes, float, int]] = {}
        next_sequence = 0
        acknowledged: set[int] = set()
        last_activity = time.monotonic()

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as transfer_socket, path.open("rb") as audio_file:
            transfer_socket.settimeout(0.1)
            while len(acknowledged) < chunks:
                while next_sequence < chunks and len(pending) < WINDOW_SIZE:
                    audio_file.seek(next_sequence * CHUNK_SIZE)
                    chunk = audio_file.read(CHUNK_SIZE)
                    packet = pack_message(DATA, transfer_id, next_sequence, chunk)
                    transfer_socket.sendto(packet, client_address)
                    pending[next_sequence] = (packet, time.monotonic(), 0)
                    next_sequence += 1

                try:
                    response, response_address = transfer_socket.recvfrom(1024)
                    if response_address != client_address:
                        continue
                    kind, response_id, sequence, _ = unpack_message(response)
                    if kind == ACK and response_id == transfer_id and sequence in pending:
                        del pending[sequence]
                        acknowledged.add(sequence)
                        last_activity = time.monotonic()
                except socket.timeout:
                    pass
                except ValueError:
                    continue

                now = time.monotonic()
                for sequence, (packet, sent_at, retries) in list(pending.items()):
                    if now - sent_at < RETRY_INTERVAL:
                        continue
                    if retries >= MAX_RETRIES:
                        self._send_control(
                            client_address,
                            transfer_id,
                            {"type": "ERROR", "message": "MP3 transfer stopped after repeated packet loss."},
                        )
                        return
                    transfer_socket.sendto(packet, client_address)
                    pending[sequence] = (packet, now, retries + 1)

                if now - last_activity > TRANSFER_TIMEOUT:
                    self._send_control(
                        client_address,
                        transfer_id,
                        {"type": "ERROR", "message": "MP3 transfer timed out waiting for the client."},
                    )
                    return

            final_packet = pack_message(FIN, transfer_id, chunks, digest.encode("ascii"))
            for _ in range(MAX_RETRIES):
                transfer_socket.sendto(final_packet, client_address)
                try:
                    response, response_address = transfer_socket.recvfrom(1024)
                    if response_address != client_address:
                        continue
                    kind, response_id, sequence, _ = unpack_message(response)
                    if (
                        kind == ACK
                        and response_id == transfer_id
                        and sequence == FINAL_ACK_SEQUENCE
                    ):
                        return
                except socket.timeout:
                    continue
                except ValueError:
                    continue
            self._send_control(
                client_address,
                transfer_id,
                {"type": "ERROR", "message": "Server could not confirm the completed MP3 transfer."},
            )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as audio_file:
            for block in iter(lambda: audio_file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


def run_udp_server(host: str, port: int, library: Path) -> None:
    server = AudioUDPServer((host, port), library)
    print(f"Audionator UDP server listening on {host}:{port}")
    print(f"MP3 library: {server.library}")
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
    parser.add_argument("--udp", action="store_true", help="Serve MP3 files over UDP instead of the web preview.")
    parser.add_argument("--host", default=None, help="Bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=None, help="Port (UDP default: 5005; web default: 8000)")
    parser.add_argument(
        "--library",
        type=Path,
        default=Path(__file__).resolve().parent / "music",
        help="Folder containing MP3 files (UDP mode only)",
    )
    args = parser.parse_args()

    host = args.host or "127.0.0.1"
    port = args.port or (5005 if args.udp else 8000)
    if args.udp:
        run_udp_server(host, port, args.library)
    else:
        run_web_preview(host, port)


if __name__ == "__main__":
    main()

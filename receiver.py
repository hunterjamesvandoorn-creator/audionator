from __future__ import annotations

import hashlib
import os
import queue
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from udp_protocol import (
    ACK,
    CONTROL,
    DATA,
    FINAL_ACK_SEQUENCE,
    FIN,
    decode_control,
    encode_control,
    pack_message,
    unpack_message,
)


class AudioReceiver:
    def __init__(self, download_directory: Path | None = None) -> None:
        self.download_directory = download_directory or Path(__file__).resolve().parent / "received"
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._server_address: tuple[str, int] | None = None
        self._thread: threading.Thread | None = None
        self._transfers: dict[bytes, dict[str, Any]] = {}

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def connect(self, host: str, port: int) -> None:
        if self.is_running:
            raise RuntimeError("The receiver is already connected.")
        if not host.strip():
            raise ValueError("Enter the server's IP address or host name.")
        address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(0.2)
        self._socket = sock
        self._server_address = (address[0], address[1])
        self._stop.clear()
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        self.request_library()
        self.events.put(("status", {"message": f"Connected to {address[0]}:{port}"}))

    def request_library(self) -> None:
        self._send_control({"command": "LIST"})

    def request_file(self, name: str) -> bytes:
        if not self._thread or not self._thread.is_alive():
            raise RuntimeError("Connect to an audio server first.")
        transfer_id = uuid.uuid4().bytes
        self._send_control({"command": "GET", "name": name}, transfer_id)
        return transfer_id

    def close(self) -> None:
        self._stop.set()
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        for state in self._transfers.values():
            stream = state.get("stream")
            if stream:
                stream.close()
            temporary_path = state.get("temporary_path")
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
        self._transfers.clear()

    def _send_control(self, message: dict[str, Any], transfer_id: bytes = bytes(16)) -> None:
        if not self._socket or not self._server_address:
            raise RuntimeError("Connect to an audio server first.")
        packet = pack_message(CONTROL, transfer_id, payload=encode_control(message))
        self._socket.sendto(packet, self._server_address)

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._socket
            if sock is None:
                return
            try:
                packet, address = sock.recvfrom(2048)
                self._handle_packet(packet, address)
            except socket.timeout:
                continue
            except OSError as error:
                if not self._stop.is_set():
                    self.events.put(("error", {"message": f"Network error: {error}"}))
                return
            except (ValueError, UnicodeDecodeError) as error:
                self.events.put(("error", {"message": f"Invalid server response: {error}"}))

    def _handle_packet(self, packet: bytes, address: tuple[str, int]) -> None:
        kind, transfer_id, sequence, payload = unpack_message(packet)
        sock = self._socket
        if sock is None:
            return

        if kind == CONTROL:
            message = decode_control(payload)
            response_type = message.get("type")
            if response_type == "LIST":
                self.events.put(("library", {"files": message.get("files", [])}))
            elif response_type == "START":
                self._start_transfer(transfer_id, message)
            elif response_type == "ERROR":
                self._discard_transfer(transfer_id)
                self.events.put(("error", {"message": str(message.get("message", "Server error"))}))
            return

        state = self._transfers.get(transfer_id)
        if not state:
            return

        if kind == DATA:
            self._write_chunk(state, sequence, payload)
            sock.sendto(pack_message(ACK, transfer_id, sequence), address)
        elif kind == FIN:
            self._finish_transfer(transfer_id, sequence, payload, address)

    def _start_transfer(self, transfer_id: bytes, message: dict[str, Any]) -> None:
        name = message.get("name")
        size = message.get("size")
        chunk_size = message.get("chunk_size")
        chunk_count = message.get("chunks")
        digest = message.get("sha256")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.lower().endswith(".mp3")
            or not isinstance(size, int)
            or size < 0
            or not isinstance(chunk_size, int)
            or chunk_size <= 0
            or not isinstance(chunk_count, int)
            or chunk_count < 0
            or not isinstance(digest, str)
        ):
            raise ValueError("Server supplied invalid transfer details.")

        self.download_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = self.download_directory / f".{transfer_id.hex()}.part"
        stream = temporary_path.open("w+b")
        stream.truncate(size)
        self._transfers[transfer_id] = {
            "name": name,
            "size": size,
            "chunk_size": chunk_size,
            "chunks": chunk_count,
            "sha256": digest,
            "received": set(),
            "stream": stream,
            "temporary_path": temporary_path,
            "last_progress": 0.0,
        }
        self.events.put(("transfer_started", {"name": name, "size": size, "id": transfer_id.hex()}))

    def _discard_transfer(self, transfer_id: bytes) -> None:
        state = self._transfers.pop(transfer_id, None)
        if not state:
            return
        state["stream"].close()
        state["temporary_path"].unlink(missing_ok=True)

    def _write_chunk(self, state: dict[str, Any], sequence: int, payload: bytes) -> None:
        if not 0 <= sequence < state["chunks"]:
            return
        expected_length = min(
            state["chunk_size"],
            state["size"] - sequence * state["chunk_size"],
        )
        if len(payload) != expected_length:
            return
        stream = state["stream"]
        if sequence not in state["received"]:
            stream.seek(sequence * state["chunk_size"])
            stream.write(payload)
            state["received"].add(sequence)
            now = time.monotonic()
            if now - state["last_progress"] >= 0.1 or len(state["received"]) == state["chunks"]:
                state["last_progress"] = now
                self.events.put(
                    (
                        "transfer_progress",
                        {
                            "name": state["name"],
                            "id": state["temporary_path"].stem.lstrip("."),
                            "received": len(state["received"]),
                            "total": state["chunks"],
                            "bytes": min(len(state["received"]) * state["chunk_size"], state["size"]),
                            "size": state["size"],
                        },
                    )
                )

    def _finish_transfer(
        self,
        transfer_id: bytes,
        sequence: int,
        digest: bytes,
        address: tuple[str, int],
    ) -> None:
        state = self._transfers.get(transfer_id)
        if not state or sequence != state["chunks"] or len(state["received"]) != state["chunks"]:
            return
        stream = state["stream"]
        stream.flush()
        stream.close()
        temporary_path: Path = state["temporary_path"]
        hasher = hashlib.sha256()
        with temporary_path.open("rb") as received_file:
            for block in iter(lambda: received_file.read(1024 * 1024), b""):
                hasher.update(block)
        actual_digest = hasher.hexdigest()
        if digest.decode("ascii") != state["sha256"] or actual_digest != state["sha256"]:
            temporary_path.unlink(missing_ok=True)
            del self._transfers[transfer_id]
            self.events.put(("error", {"message": f"Integrity check failed for {state['name']}."}))
            return

        destination = self._available_destination(state["name"])
        os.replace(temporary_path, destination)
        del self._transfers[transfer_id]
        self.events.put(("transfer_complete", {"name": state["name"], "path": str(destination)}))
        if self._socket:
            self._socket.sendto(pack_message(ACK, transfer_id, FINAL_ACK_SEQUENCE), address)

    def _available_destination(self, name: str) -> Path:
        candidate = self.download_directory / name
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        index = 2
        while True:
            numbered = self.download_directory / f"{stem} ({index}){suffix}"
            if not numbered.exists():
                return numbered
            index += 1

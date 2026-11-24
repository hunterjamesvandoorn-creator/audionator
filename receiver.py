from __future__ import annotations

import hashlib
import os
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TransferSnapshot:
    path: Path | None
    size: int
    available_bytes: int
    buffer_bytes: int
    complete: bool
    error: str | None


class AudioReceiver:
    def __init__(self, download_directory: Path | None = None) -> None:
        self.download_directory = download_directory or Path(__file__).resolve().parent / "received"
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._server_address: tuple[str, int] | None = None
        self._thread: threading.Thread | None = None
        self._transfers: dict[bytes, dict[str, Any]] = {}
        self._transfer_errors: dict[bytes, str] = {}
        self._completed_transfers: set[bytes] = set()
        self._condition = threading.Condition()

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

    def wait_for_transfer_bytes(
        self,
        transfer_id: bytes,
        minimum_bytes: int,
        timeout: float | None = None,
    ) -> TransferSnapshot | None:
        if minimum_bytes < 0:
            raise ValueError("The requested buffer size cannot be negative.")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                state = self._transfers.get(transfer_id)
                if state:
                    snapshot = self._snapshot(state)
                    if (
                        state["error"]
                        or state["complete"]
                        or state["available_bytes"] >= minimum_bytes
                    ):
                        return snapshot
                elif transfer_id in self._transfer_errors:
                    return TransferSnapshot(
                        None,
                        0,
                        0,
                        0,
                        False,
                        self._transfer_errors[transfer_id],
                    )

                if self._stop.is_set():
                    return TransferSnapshot(None, 0, 0, 0, False, "Video receiver stopped.")
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    @staticmethod
    def _snapshot(state: dict[str, Any]) -> TransferSnapshot:
        return TransferSnapshot(
            state["temporary_path"],
            state["size"],
            state["available_bytes"],
            state["buffer_bytes"],
            state["complete"],
            state["error"],
        )

    def close(self) -> None:
        self._stop.set()
        with self._condition:
            transfers = list(self._transfers.values())
            self._transfers.clear()
            self._condition.notify_all()
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        for state in transfers:
            stream = state.get("stream")
            if stream and not stream.closed:
                stream.close()
            temporary_path = state.get("temporary_path")
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
        self._transfer_errors.clear()
        self._completed_transfers.clear()

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
                error = str(message.get("message", "Server error"))
                self._discard_transfer(transfer_id, error)
                self.events.put(("error", {"message": error}))
            return

        with self._condition:
            state = self._transfers.get(transfer_id)
            complete = transfer_id in self._completed_transfers
        if kind == FIN and complete:
            sock.sendto(pack_message(ACK, transfer_id, FINAL_ACK_SEQUENCE), address)
            return
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
        media_type = message.get("media_type")
        buffer_bytes = message.get("buffer_bytes", 0)
        if media_type is None and isinstance(name, str):
            media_type = "video" if name.lower().endswith(".mp4") else "audio"
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or media_type not in {"audio", "video"}
            or (media_type == "audio" and not name.lower().endswith(".mp3"))
            or (media_type == "video" and not name.lower().endswith(".mp4"))
            or not isinstance(size, int)
            or size < 0
            or not isinstance(chunk_size, int)
            or chunk_size <= 0
            or not isinstance(chunk_count, int)
            or chunk_count < 0
            or not isinstance(digest, str)
            or not isinstance(buffer_bytes, int)
            or not 0 <= buffer_bytes <= size
        ):
            raise ValueError("Server supplied invalid transfer details.")

        self.download_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = self.download_directory / f".{transfer_id.hex()}.part"
        stream = temporary_path.open("w+b")
        stream.truncate(size)
        state = {
            "name": name,
            "media_type": media_type,
            "size": size,
            "chunk_size": chunk_size,
            "chunks": chunk_count,
            "buffer_bytes": buffer_bytes,
            "sha256": digest,
            "received": set(),
            "next_contiguous_chunk": 0,
            "available_bytes": 0,
            "stream": stream,
            "temporary_path": temporary_path,
            "last_progress": 0.0,
            "complete": False,
            "error": None,
            "finishing": False,
        }
        with self._condition:
            self._transfer_errors.pop(transfer_id, None)
            self._transfers[transfer_id] = state
            self._condition.notify_all()
        self.events.put(("transfer_started", {"name": name, "size": size, "id": transfer_id.hex()}))

    def _discard_transfer(self, transfer_id: bytes, error: str) -> None:
        with self._condition:
            state = self._transfers.get(transfer_id)
            self._transfer_errors[transfer_id] = error
            if state:
                state["error"] = error
                if state["media_type"] != "video":
                    self._transfers.pop(transfer_id, None)
            self._condition.notify_all()
        if not state:
            return
        if not state["stream"].closed:
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
        with self._condition:
            if state["error"] or state["complete"] or sequence in state["received"]:
                return
            stream = state["stream"]
            stream.seek(sequence * state["chunk_size"])
            stream.write(payload)
            stream.flush()
            state["received"].add(sequence)
            while state["next_contiguous_chunk"] in state["received"]:
                state["next_contiguous_chunk"] += 1
            state["available_bytes"] = min(
                state["next_contiguous_chunk"] * state["chunk_size"],
                state["size"],
            )
            self._condition.notify_all()
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
        with self._condition:
            state = self._transfers.get(transfer_id)
            if (
                not state
                or state["finishing"]
                or sequence != state["chunks"]
                or len(state["received"]) != state["chunks"]
            ):
                return
            state["finishing"] = True
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
            error = f"Integrity check failed for {state['name']}."
            with self._condition:
                state["error"] = error
                self._transfer_errors[transfer_id] = error
                if state["media_type"] != "video":
                    self._transfers.pop(transfer_id, None)
                self._condition.notify_all()
            self.events.put(("error", {"message": error}))
            return

        if state["media_type"] == "video":
            with self._condition:
                state["complete"] = True
                state["available_bytes"] = state["size"]
                self._completed_transfers.add(transfer_id)
                self._condition.notify_all()
            self.events.put(
                ("transfer_complete", {"name": state["name"], "path": str(temporary_path)})
            )
            if self._socket:
                self._socket.sendto(pack_message(ACK, transfer_id, FINAL_ACK_SEQUENCE), address)
            return

        destination = self._available_destination(state["name"])
        os.replace(temporary_path, destination)
        with self._condition:
            self._transfers.pop(transfer_id, None)
            self._completed_transfers.add(transfer_id)
            self._condition.notify_all()
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

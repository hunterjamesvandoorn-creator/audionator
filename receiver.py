from __future__ import annotations

import hashlib
import itertools
import os
import queue
import socket
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from download_meter import DownloadMeter
from udp_protocol import (
    ACK,
    CONTROL,
    CONTROL_ACK,
    CONTROL_MAX_RETRIES,
    CONTROL_RETRY_INTERVAL,
    DATA,
    FINAL_ACK_SEQUENCE,
    FIN,
    HEARTBEAT,
    HEARTBEAT_ACK,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT,
    decode_control,
    encode_control,
    pack_message,
    unpack_message,
)


CONTROL_HISTORY_SIZE = 1024
LATENCY_WINDOW_SIZE = 60


@dataclass(frozen=True)
class TransferSnapshot:
    path: Path | None
    size: int
    available_bytes: int
    buffer_bytes: int
    complete: bool
    error: str | None


@dataclass
class _PendingControl:
    packet: bytes
    transfer_id: bytes
    failure_message: str
    sent_at: float
    retries: int


@dataclass
class _Request:
    event: threading.Event
    response: dict[str, Any] | None = None
    error: str | None = None


class AudioReceiver:
    def __init__(self, download_directory: Path | None = None, meter: DownloadMeter | None = None) -> None:
        self.download_directory = download_directory or Path(__file__).resolve().parent / "received"
        self._meter = meter
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._server_address: tuple[str, int] | None = None
        self._thread: threading.Thread | None = None
        self._transfers: dict[bytes, dict[str, Any]] = {}
        self._transfer_errors: dict[bytes, str] = {}
        self._completed_transfers: set[bytes] = set()
        self._requests: dict[bytes, _Request] = {}
        self._condition = threading.Condition()
        self._control_lock = threading.Lock()
        self._control_ids = itertools.count(1)
        self._pending_controls: dict[int, _PendingControl] = {}
        self._seen_controls: OrderedDict[int, None] = OrderedDict()
        self._heartbeat_sequence = 0
        self._last_heartbeat_sent = float("-inf")
        self._last_heartbeat_reply = 0.0
        self._connection_lost = False
        self._heartbeat_sent_at: dict[int, float] = {}
        self._latency_samples: deque[float] = deque(maxlen=LATENCY_WINDOW_SIZE)
        self._latency_lock = threading.Lock()
        self._loss_missed = 0
        self._loss_packets = 0

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def latency_summary(self) -> dict[str, Any]:
        with self._latency_lock:
            samples = list(self._latency_samples)
        if not samples:
            return {"average_ms": None, "max_ms": None, "samples": 0}
        return {
            "average_ms": round(sum(samples) / len(samples), 1),
            "max_ms": round(max(samples), 1),
            "samples": len(samples),
        }

    def loss_counts(self) -> tuple[int, int]:
        with self._condition:
            return self._loss_missed, self._loss_packets

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
        with self._control_lock:
            self._pending_controls.clear()
            self._seen_controls.clear()
        self._heartbeat_sequence = 0
        self._last_heartbeat_sent = float("-inf")
        self._last_heartbeat_reply = time.monotonic()
        self._connection_lost = False
        self._heartbeat_sent_at.clear()
        with self._latency_lock:
            self._latency_samples.clear()
        with self._condition:
            self._loss_missed = 0
            self._loss_packets = 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        self._send_control({"command": "CONNECT"}, failure_message="Server did not accept the connection.")

    def request_library(self) -> None:
        self._send_control({"command": "LIST"}, failure_message="Server did not return its library.")

    def create_session(self) -> None:
        self._send_control({"command": "CREATE_SESSION"}, failure_message="Server could not create a session.")

    def join_session(self, code: str) -> None:
        self._send_control(
            {"command": "JOIN_SESSION", "code": code},
            failure_message="Server did not respond to the join request.",
        )

    def leave_session(self) -> None:
        self._send_control({"command": "LEAVE_SESSION"}, failure_message="Server did not confirm leaving the session.")

    def request_file(self, name: str) -> bytes:
        if not self._thread or not self._thread.is_alive():
            raise RuntimeError("Connect to an audio server first.")
        transfer_id = uuid.uuid4().bytes
        self._send_control(
            {"command": "GET", "name": name},
            transfer_id,
            failure_message=f"Server did not respond to the request for {name}.",
        )
        return transfer_id

    def request_manifest(self, name: str, timeout: float) -> dict[str, Any]:
        """Ask the server for a video's rendition summary. The server may take a while to prepare it."""
        self._require_connection()
        transfer_id = uuid.uuid4().bytes
        request = _Request(threading.Event())
        with self._condition:
            self._requests[transfer_id] = request
        try:
            self._send_control(
                {"command": "MANIFEST", "name": name},
                transfer_id,
                failure_message=f"Server did not return the video details for {name}.",
            )
            if not request.event.wait(timeout):
                raise TimeoutError(f"The server did not finish preparing {name} in time.")
            if request.error:
                raise RuntimeError(request.error)
            return request.response or {}
        finally:
            with self._condition:
                self._requests.pop(transfer_id, None)

    def request_segment(self, name: str, level: str, index: int) -> bytes:
        """Start one segment transfer. Index -1 requests the level's init segment."""
        self._require_connection()
        transfer_id = uuid.uuid4().bytes
        self._send_control(
            {"command": "SEGMENT", "name": name, "level": level, "index": index},
            transfer_id,
            failure_message=f"Server did not respond to the request for {level} segment {index}.",
        )
        return transfer_id

    def wait_for_transfer_complete(self, transfer_id: bytes, timeout: float | None = None) -> TransferSnapshot | None:
        return self.wait_for_transfer_bytes(transfer_id, 1 << 62, timeout)

    def release_transfer(self, transfer_id: bytes) -> None:
        """Forget a finished transfer and delete its temporary file."""
        with self._condition:
            state = self._transfers.pop(transfer_id, None)
            self._transfer_errors.pop(transfer_id, None)
            self._completed_transfers.discard(transfer_id)
            self._condition.notify_all()
        if state:
            stream = state.get("stream")
            if stream and not stream.closed:
                stream.close()
            state["temporary_path"].unlink(missing_ok=True)

    def _require_connection(self) -> None:
        if not self._thread or not self._thread.is_alive():
            raise RuntimeError("Connect to an audio server first.")

    def _complete_request(self, transfer_id: bytes, message: dict[str, Any]) -> bool:
        with self._condition:
            request = self._requests.get(transfer_id)
            if request is None:
                return False
            request.response = message
            request.event.set()
            return True

    def _fail_request(self, transfer_id: bytes, error: str) -> bool:
        with self._condition:
            request = self._requests.get(transfer_id)
            if request is None:
                return False
            request.error = error
            request.event.set()
            return True

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
            state.get("final_path") or state["temporary_path"],
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
            for request in self._requests.values():
                request.error = request.error or "Connection closed."
                request.event.set()
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
        with self._control_lock:
            self._pending_controls.clear()

    def _send_control(
        self,
        message: dict[str, Any],
        transfer_id: bytes = bytes(16),
        failure_message: str = "Server did not respond.",
    ) -> None:
        sock = self._socket
        if not sock or not self._server_address:
            raise RuntimeError("Connect to an audio server first.")
        with self._control_lock:
            control_id = next(self._control_ids)
            packet = pack_message(CONTROL, transfer_id, control_id, encode_control(message))
            self._pending_controls[control_id] = _PendingControl(
                packet,
                transfer_id,
                failure_message,
                time.monotonic(),
                0,
            )
        sock.sendto(packet, self._server_address)

    def _retransmit_controls(self) -> None:
        sock = self._socket
        if sock is None or not self._server_address:
            return
        now = time.monotonic()
        resend: list[bytes] = []
        failed: list[_PendingControl] = []
        with self._control_lock:
            for control_id, pending in list(self._pending_controls.items()):
                if now - pending.sent_at < CONTROL_RETRY_INTERVAL:
                    continue
                if pending.retries >= CONTROL_MAX_RETRIES:
                    del self._pending_controls[control_id]
                    failed.append(pending)
                    continue
                pending.retries += 1
                pending.sent_at = now
                resend.append(pending.packet)
        for packet in resend:
            sock.sendto(packet, self._server_address)
        for pending in failed:
            self._control_failed(pending)

    def _service_heartbeat(self) -> None:
        sock = self._socket
        if sock is None or not self._server_address:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_sent >= HEARTBEAT_INTERVAL:
            self._last_heartbeat_sent = now
            self._heartbeat_sequence += 1
            self._heartbeat_sent_at = {
                sequence: sent_at
                for sequence, sent_at in self._heartbeat_sent_at.items()
                if now - sent_at <= HEARTBEAT_TIMEOUT
            }
            self._heartbeat_sent_at[self._heartbeat_sequence] = now
            sock.sendto(
                pack_message(HEARTBEAT, sequence=self._heartbeat_sequence),
                self._server_address,
            )
        if not self._connection_lost and now - self._last_heartbeat_reply > HEARTBEAT_TIMEOUT:
            self._connection_lost = True
            self.events.put(("connection_lost", {"message": "Lost contact with the server."}))

    def _control_failed(self, pending: _PendingControl) -> None:
        if pending.transfer_id != bytes(16):
            self._discard_transfer(pending.transfer_id, pending.failure_message)
            self._fail_request(pending.transfer_id, pending.failure_message)
        self.events.put(("error", {"message": pending.failure_message}))

    def _remember_control(self, control_id: int) -> bool:
        with self._control_lock:
            if control_id in self._seen_controls:
                return False
            self._seen_controls[control_id] = None
            if len(self._seen_controls) > CONTROL_HISTORY_SIZE:
                self._seen_controls.popitem(last=False)
            return True

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._socket
            if sock is None:
                return
            try:
                self._retransmit_controls()
                self._service_heartbeat()
                packet, address = sock.recvfrom(2048)
                if self._meter:
                    self._meter.add(len(packet))
                self._handle_packet(packet, address)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows reports ICMP port-unreachable as a reset; the heartbeat timeout detects real loss.
                continue
            except OSError as error:
                if not self._stop.is_set():
                    self.events.put(("error", {"message": f"Network error: {error}"}))
                return
            except (ValueError, UnicodeDecodeError) as error:
                self.events.put(("error", {"message": f"Invalid server response: {error}"}))

    def _handle_packet(self, packet: bytes, address: tuple[str, int]) -> None:
        kind, transfer_id, sequence, _timestamp_us, payload = unpack_message(packet)
        sock = self._socket
        if sock is None:
            return

        if kind == CONTROL_ACK:
            with self._control_lock:
                self._pending_controls.pop(sequence, None)
            return
        if kind == HEARTBEAT_ACK:
            now = time.monotonic()
            self._last_heartbeat_reply = now
            sent_at = self._heartbeat_sent_at.pop(sequence, None)
            if sent_at is not None:
                with self._latency_lock:
                    self._latency_samples.append((now - sent_at) * 1000)
            if self._connection_lost:
                self._connection_lost = False
                self.events.put(("connection_restored", {"message": "Connection to the server restored."}))
            return
        if kind == CONTROL:
            message = decode_control(payload)
            sock.sendto(pack_message(CONTROL_ACK, transfer_id, sequence), address)
            if not self._remember_control(sequence):
                return
            response_type = message.get("type")
            if response_type == "ACCEPT":
                self.events.put(("accepted", {}))
                self.request_library()
            elif response_type == "LIST":
                self.events.put(("library", {"files": message.get("files", [])}))
            elif response_type == "START":
                self._start_transfer(transfer_id, message)
            elif response_type == "SESSION":
                self.events.put(("session", {"code": message["code"], "members": message["members"]}))
            elif response_type == "LEFT":
                self.events.put(("left", {}))
            elif response_type == "MANIFEST":
                self._complete_request(transfer_id, message)
            elif response_type == "ERROR":
                error = str(message.get("message", "Server error"))
                if self._fail_request(transfer_id, error):
                    return
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
            "highest_sequence": -1,
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
            # A gap is counted once, when a later sequence first arrives past it.
            if sequence > state["highest_sequence"]:
                self._loss_packets += sequence - state["highest_sequence"]
                self._loss_missed += sequence - state["highest_sequence"] - 1
                state["highest_sequence"] = sequence
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
        with self._condition:
            self._replace_file(temporary_path, destination)
            state["final_path"] = destination
            state["complete"] = True
            state["available_bytes"] = state["size"]
            state["received"] = set()
            self._completed_transfers.add(transfer_id)
            self._condition.notify_all()
        self.events.put(("transfer_complete", {"name": state["name"], "path": str(destination)}))
        if self._socket:
            self._socket.sendto(pack_message(ACK, transfer_id, FINAL_ACK_SEQUENCE), address)

    @staticmethod
    def _replace_file(source: Path, destination: Path) -> None:
        # A reader may hold the partial file open for a moment, which Windows reports as a sharing error.
        for attempt in range(20):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

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

from __future__ import annotations

import json
import struct
import time
from typing import Any


MAGIC = b"ADN3"
# Header timestamp is the sender's wall-clock time in microseconds since the Unix epoch.
HEADER = struct.Struct("!4sB16sIQ")
MAX_DATAGRAM_SIZE = 1200
CHUNK_SIZE = MAX_DATAGRAM_SIZE - HEADER.size
WINDOW_SIZE = 32

CONTROL = 1
DATA = 2
ACK = 3
FIN = 4
CONTROL_ACK = 5
HEARTBEAT = 6
HEARTBEAT_ACK = 7
FINAL_ACK_SEQUENCE = 0xFFFFFFFF
# CONTROL and CONTROL_ACK carry a control message id in the sequence field.
CONTROL_RETRY_INTERVAL = 0.35
CONTROL_MAX_RETRIES = 30
# HEARTBEAT and HEARTBEAT_ACK carry a heartbeat counter in the sequence field.
HEARTBEAT_INTERVAL = 1.0
HEARTBEAT_TIMEOUT = 5.0


def pack_message(
    kind: int,
    transfer_id: bytes = bytes(16),
    sequence: int = 0,
    payload: bytes = b"",
    timestamp_us: int | None = None,
) -> bytes:
    if len(transfer_id) != 16:
        raise ValueError("Transfer IDs must be exactly 16 bytes.")
    if not 0 <= sequence <= FINAL_ACK_SEQUENCE:
        raise ValueError("Sequence number is outside the supported range.")
    if timestamp_us is None:
        timestamp_us = time.time_ns() // 1000
    if not 0 <= timestamp_us < 2**64:
        raise ValueError("Timestamp is outside the supported range.")
    return HEADER.pack(MAGIC, kind, transfer_id, sequence, timestamp_us) + payload


def unpack_message(packet: bytes) -> tuple[int, bytes, int, int, bytes]:
    if len(packet) < HEADER.size:
        raise ValueError("Packet is shorter than the protocol header.")
    magic, kind, transfer_id, sequence, timestamp_us = HEADER.unpack_from(packet)
    if magic != MAGIC:
        raise ValueError("Packet has an unknown protocol signature.")
    if kind not in {CONTROL, DATA, ACK, FIN, CONTROL_ACK, HEARTBEAT, HEARTBEAT_ACK}:
        raise ValueError("Packet has an unknown message type.")
    return kind, transfer_id, sequence, timestamp_us, packet[HEADER.size :]


def encode_control(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


def decode_control(payload: bytes) -> dict[str, Any]:
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Control message must be a JSON object.")
    return message

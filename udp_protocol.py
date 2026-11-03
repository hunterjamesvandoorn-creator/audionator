from __future__ import annotations

import json
import struct
from typing import Any


MAGIC = b"ADN1"
HEADER = struct.Struct("!4sB16sI")
MAX_DATAGRAM_SIZE = 1200
CHUNK_SIZE = MAX_DATAGRAM_SIZE - HEADER.size
WINDOW_SIZE = 32

CONTROL = 1
DATA = 2
ACK = 3
FIN = 4
FINAL_ACK_SEQUENCE = 0xFFFFFFFF


def pack_message(
    kind: int,
    transfer_id: bytes = bytes(16),
    sequence: int = 0,
    payload: bytes = b"",
) -> bytes:
    if len(transfer_id) != 16:
        raise ValueError("Transfer IDs must be exactly 16 bytes.")
    if not 0 <= sequence <= FINAL_ACK_SEQUENCE:
        raise ValueError("Sequence number is outside the supported range.")
    return HEADER.pack(MAGIC, kind, transfer_id, sequence) + payload


def unpack_message(packet: bytes) -> tuple[int, bytes, int, bytes]:
    if len(packet) < HEADER.size:
        raise ValueError("Packet is shorter than the protocol header.")
    magic, kind, transfer_id, sequence = HEADER.unpack_from(packet)
    if magic != MAGIC:
        raise ValueError("Packet has an unknown protocol signature.")
    if kind not in {CONTROL, DATA, ACK, FIN}:
        raise ValueError("Packet has an unknown message type.")
    return kind, transfer_id, sequence, packet[HEADER.size :]


def encode_control(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


def decode_control(payload: bytes) -> dict[str, Any]:
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Control message must be a JSON object.")
    return message

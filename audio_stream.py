from __future__ import annotations

import os
from typing import Protocol

from receiver import TransferSnapshot

STREAM_WAIT_SECONDS = 30.0
MAX_READ_ATTEMPTS = 3


class TransferSource(Protocol):
    def wait_for_transfer_bytes(
        self, transfer_id: bytes, minimum_bytes: int, timeout: float | None = None
    ) -> TransferSnapshot | None: ...


class StreamingAudioFile:
    """File-like, read-only view of an MP3 that may still be downloading.

    Reads block until the requested bytes have been received. A read that
    returns b"" means the track ended, the transfer failed, or no data
    arrived within STREAM_WAIT_SECONDS.
    """

    def __init__(self, source: TransferSource, transfer_id: bytes, size: int) -> None:
        self._source = source
        self._transfer_id = transfer_id
        self._size = size
        self._position = 0

    @property
    def size(self) -> int:
        return self._size

    def read(self, size: int | None = -1) -> bytes:
        remaining = self._size - self._position
        if remaining <= 0:
            return b""
        wanted = remaining if size is None or size < 0 else min(size, remaining)

        for _ in range(MAX_READ_ATTEMPTS):
            snapshot = self._source.wait_for_transfer_bytes(
                self._transfer_id, self._position + 1, timeout=STREAM_WAIT_SECONDS
            )
            if snapshot is None or snapshot.error or snapshot.path is None:
                return b""
            available = min(snapshot.available_bytes, self._size)
            try:
                # Open per read and close immediately so the receiver can rename the file.
                with snapshot.path.open("rb") as source:
                    source.seek(self._position)
                    data = source.read(min(wanted, available - self._position))
            except (FileNotFoundError, PermissionError):
                # The partial file was just renamed to its final name; take a fresh snapshot.
                continue
            self._position += len(data)
            return data
        return b""

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self._position + offset
        elif whence == os.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")
        if target < 0:
            raise ValueError("Cannot seek before the start of the track.")
        self._position = target
        return target

    def tell(self) -> int:
        return self._position

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        # Nothing is held open between reads; the receiver owns the file.
        pass

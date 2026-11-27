from __future__ import annotations

import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from receiver import AudioReceiver

MAX_SIMULATED_CLIENTS = 50
ACCEPT_TIMEOUT_SECONDS = 8.0
TRANSFER_TIMEOUT_SECONDS = 60.0
IDLE_POLL_SECONDS = 0.5
RETRY_DELAY_SECONDS = 0.5


class TrafficSimulator:
    """Runs simulated receivers against a server to generate load for stress tests."""

    def __init__(
        self,
        host: str,
        port: int,
        client_count: int,
        work_directory: Path,
        file_name: str | None = None,
    ) -> None:
        if not 1 <= client_count <= MAX_SIMULATED_CLIENTS:
            raise ValueError(f"Simulated clients must be between 1 and {MAX_SIMULATED_CLIENTS}.")
        self.host = host
        self.port = port
        self.client_count = client_count
        self.file_name = file_name
        self.work_directory = work_directory
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._receivers: list[AudioReceiver] = []
        self._started_at = 0.0
        self._stopped_at: float | None = None
        self._connected = 0
        self._completed = 0
        self._failed = 0
        self._bytes = 0
        self._last_error: str | None = None

    def start(self) -> None:
        self._started_at = time.monotonic()
        for index in range(self.client_count):
            thread = threading.Thread(target=self._run_client, args=(index,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            receivers = list(self._receivers)
            if self._stopped_at is None:
                self._stopped_at = time.monotonic()
        for receiver in receivers:
            receiver.close()
        for thread in self._threads:
            thread.join(timeout=3)
        shutil.rmtree(self.work_directory, ignore_errors=True)

    def status(self) -> dict[str, Any]:
        with self._lock:
            end = self._stopped_at if self._stopped_at is not None else time.monotonic()
            elapsed = max(end - self._started_at, 0.001) if self._started_at else 0.001
            return {
                "running": not self._stop.is_set(),
                "clients": self.client_count,
                "connected": self._connected,
                "completed": self._completed,
                "failed": self._failed,
                "bytes": self._bytes,
                "bytes_per_second": round(self._bytes / elapsed),
                "file": self.file_name,
                "last_error": self._last_error,
            }

    def _run_client(self, index: int) -> None:
        receiver = AudioReceiver(self.work_directory / f"client{index}")
        with self._lock:
            self._receivers.append(receiver)
        try:
            receiver.connect(self.host, self.port)
            if not self._wait_for_accept(receiver):
                return
            with self._lock:
                self._connected += 1
            try:
                while not self._stop.is_set():
                    if self.file_name is None:
                        self._drain_events(receiver)
                        self._stop.wait(IDLE_POLL_SECONDS)
                    else:
                        self._download_once(receiver)
            finally:
                with self._lock:
                    self._connected -= 1
        except (OSError, RuntimeError, ValueError) as error:
            self._record_error(str(error))
        finally:
            receiver.close()

    def _wait_for_accept(self, receiver: AudioReceiver) -> bool:
        deadline = time.monotonic() + ACCEPT_TIMEOUT_SECONDS
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                event, payload = receiver.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if event == "accepted":
                return True
            if event == "error":
                self._record_error(str(payload.get("message", "The server refused the connection.")))
                return False
        if not self._stop.is_set():
            self._record_error("The server did not accept a simulated client.")
        return False

    def _download_once(self, receiver: AudioReceiver) -> None:
        try:
            transfer_id = receiver.request_file(self.file_name)
        except (OSError, RuntimeError) as error:
            if not self._stop.is_set():
                self._record_failure(str(error))
            self._stop.wait(RETRY_DELAY_SECONDS)
            return
        snapshot = receiver.wait_for_transfer_complete(transfer_id, timeout=TRANSFER_TIMEOUT_SECONDS)
        if self._stop.is_set():
            # Transfers interrupted by stop() are expected and are not failures.
            return
        if snapshot is None:
            self._record_failure("A simulated download timed out.")
        elif snapshot.error or not snapshot.complete:
            self._record_failure(snapshot.error or "A simulated download did not finish.")
        else:
            with self._lock:
                self._completed += 1
                self._bytes += snapshot.size
        if snapshot is not None and snapshot.path is not None:
            snapshot.path.unlink(missing_ok=True)
        receiver.release_transfer(transfer_id)
        self._drain_events(receiver)

    @staticmethod
    def _drain_events(receiver: AudioReceiver) -> None:
        try:
            while True:
                receiver.events.get_nowait()
        except queue.Empty:
            return

    def _record_failure(self, message: str) -> None:
        with self._lock:
            self._failed += 1
            self._last_error = message

    def _record_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

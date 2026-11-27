from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from receiver import AudioReceiver

AUTO = "auto"
INIT_INDEX = -1
MANIFEST_TIMEOUT = 1800.0
SEGMENT_TIMEOUT = 60.0
LOSS_LIMIT = 0.05
SMOOTHING = 0.5
UPGRADE_STREAK = 3
UPGRADE_HEADROOM = 1.25
START_LEVEL_INDEX = 1
TRAFFIC_WINDOW_SECONDS = 3.0
SUMMARY_KEYS = (
    "codecs",
    "segment_seconds",
    "segment_count",
    "last_segment_seconds",
    "duration",
    "levels",
)


class QualityPolicy:
    """Chooses the rendition for the next segment. Levels are ordered best first."""

    def __init__(self, levels: list[dict[str, Any]]) -> None:
        if not levels:
            raise ValueError("The video has no quality levels.")
        self.levels = [dict(level) for level in levels]
        self.mode = AUTO
        self.current = min(START_LEVEL_INDEX, len(self.levels) - 1)
        self.throughput: float | None = None
        self._good_streak = 0

    @property
    def level_id(self) -> str:
        return str(self.levels[self.current]["id"])

    def set_mode(self, mode: str) -> None:
        self._good_streak = 0
        if mode == AUTO:
            self.mode = AUTO
            return
        for index, level in enumerate(self.levels):
            if level["id"] == mode:
                self.mode = mode
                self.current = index
                return
        raise ValueError("Unknown video quality.")

    def record(self, received_bytes: int, seconds: float, loss_ratio: float) -> None:
        """Feed one finished media segment into the policy. Only auto mode changes the level."""
        sample = received_bytes * 8 / max(seconds, 1e-3)
        if self.throughput is None:
            self.throughput = sample
        else:
            self.throughput = SMOOTHING * sample + (1 - SMOOTHING) * self.throughput
        if self.mode != AUTO:
            return

        struggling = (
            self.throughput < self.levels[self.current]["bandwidth"] or loss_ratio > LOSS_LIMIT
        )
        if struggling:
            self._good_streak = 0
            if self.current < len(self.levels) - 1:
                self.current += 1
            return

        self._good_streak += 1
        if self.current > 0 and self._good_streak >= UPGRADE_STREAK:
            higher = self.levels[self.current - 1]["bandwidth"]
            if self.throughput > higher * UPGRADE_HEADROOM:
                self.current -= 1
                self._good_streak = 0


class AdaptiveVideoSession:
    """Fetches a video's renditions segment by segment over one connected receiver."""

    def __init__(self, receiver: AudioReceiver, name: str) -> None:
        self.receiver = receiver
        self.name = name
        self._lock = threading.Lock()
        self._status = "preparing"
        self._message = "Preparing the video on the server..."
        self._summary: dict[str, Any] | None = None
        self._policy: QualityPolicy | None = None
        self._traffic: deque[tuple[float, int]] = deque()
        self._closed = False

    def start(self) -> None:
        threading.Thread(target=self._load_manifest, daemon=True).start()

    def _load_manifest(self) -> None:
        try:
            response = self.receiver.request_manifest(self.name, MANIFEST_TIMEOUT)
            summary = {key: response[key] for key in SUMMARY_KEYS}
            policy = QualityPolicy(summary["levels"])
        except KeyError:
            self._set_error("The server returned incomplete video details.")
            return
        except Exception as error:
            self._set_error(str(error) or "The video could not be prepared.")
            return
        with self._lock:
            if self._closed:
                return
            self._summary = summary
            self._policy = policy
            self._status = "ready"
            self._message = ""

    def _set_error(self, message: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._status = "error"
            self._message = message

    def _ready_locked(self) -> tuple[dict[str, Any], QualityPolicy]:
        if self._status != "ready" or self._summary is None or self._policy is None:
            raise RuntimeError(self._message or "The video is still being prepared.")
        return self._summary, self._policy

    def fetch_segment(self, index: int) -> tuple[str, bytes]:
        """Download media segment `index` at the policy's current level. Returns (level_id, bytes)."""
        with self._lock:
            summary, policy = self._ready_locked()
            if not 0 <= index < int(summary["segment_count"]):
                raise ValueError("That video segment does not exist.")
            level_id = policy.level_id

        before_missed, before_packets = self.receiver.loss_counts()
        started = time.monotonic()
        data = self._fetch(level_id, index)
        seconds = time.monotonic() - started
        after_missed, after_packets = self.receiver.loss_counts()

        packets = after_packets - before_packets
        missed = after_missed - before_missed
        loss_ratio = missed / packets if packets > 0 else 0.0
        with self._lock:
            if self._policy is not None:
                self._policy.record(len(data), seconds, loss_ratio)
        return level_id, data

    def fetch_init(self, level_id: str) -> bytes:
        """Download the initialization segment for one level."""
        with self._lock:
            _, policy = self._ready_locked()
            if level_id not in {level["id"] for level in policy.levels}:
                raise ValueError("Unknown video quality.")
        return self._fetch(level_id, INIT_INDEX)

    def _fetch(self, level_id: str, index: int) -> bytes:
        transfer_id = self.receiver.request_segment(self.name, level_id, index)
        try:
            snapshot = self.receiver.wait_for_transfer_complete(transfer_id, SEGMENT_TIMEOUT)
            if snapshot is None:
                raise TimeoutError("The video segment did not arrive in time.")
            if snapshot.error:
                raise RuntimeError(snapshot.error)
            if snapshot.path is None:
                raise RuntimeError("The video segment is missing.")
            data = Path(snapshot.path).read_bytes()
        finally:
            self.receiver.release_transfer(transfer_id)
        self._record_traffic(len(data))
        return data

    def set_quality(self, mode: str) -> None:
        with self._lock:
            _, policy = self._ready_locked()
            policy.set_mode(mode)

    def _record_traffic(self, byte_count: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._traffic.append((now, byte_count))
            self._trim_traffic(now)

    def _trim_traffic(self, now: float) -> None:
        while self._traffic and now - self._traffic[0][0] > TRAFFIC_WINDOW_SECONDS:
            self._traffic.popleft()

    def _traffic_bps_locked(self, now: float) -> int:
        self._trim_traffic(now)
        total = sum(byte_count for _, byte_count in self._traffic)
        return round(total * 8 / TRAFFIC_WINDOW_SECONDS)

    def describe(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            info: dict[str, Any] = {
                "name": self.name,
                "status": self._status,
                "message": self._message,
                "traffic_bps": self._traffic_bps_locked(now),
            }
            if self._summary is not None and self._policy is not None:
                info.update(self._summary)
                info["mode"] = self._policy.mode
                info["level"] = self._policy.level_id
                info["throughput_bps"] = round(self._policy.throughput or 0)
            return info

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._status = "closed"
        self.receiver.close()

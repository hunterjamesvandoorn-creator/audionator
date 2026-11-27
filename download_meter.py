from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

SAMPLE_INTERVAL_SECONDS = 1.0
RETAIN_SECONDS = 3600.0


class DownloadMeter:
    """Counts received bytes and keeps a history of cumulative totals for the download graph."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._total = 0
        self._samples: deque[tuple[float, int]] = deque()

    def add(self, byte_count: int) -> None:
        with self._lock:
            self._total += byte_count

    def sample(self) -> None:
        now = self._clock()
        with self._lock:
            if self._samples and now - self._samples[-1][0] < SAMPLE_INTERVAL_SECONDS:
                return
            self._samples.append((now, self._total))
            while now - self._samples[0][0] > RETAIN_SECONDS:
                self._samples.popleft()

    def history(self, window_seconds: float) -> list[tuple[float, float]]:
        """Return (seconds_ago, megabits_per_second) pairs inside the window, oldest first."""
        now = self._clock()
        with self._lock:
            samples = list(self._samples)
        points: list[tuple[float, float]] = []
        for (previous_time, previous_total), (sample_time, total) in zip(samples, samples[1:]):
            if now - sample_time > window_seconds:
                continue
            megabits = (total - previous_total) * 8 / 1_000_000
            points.append((round(now - sample_time, 1), round(megabits / (sample_time - previous_time), 3)))
        return points

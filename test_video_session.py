import tempfile
import time
import unittest
from pathlib import Path

from video_session import AUTO, AdaptiveVideoSession, QualityPolicy, TRAFFIC_WINDOW_SECONDS

LEVELS = [
    {"id": "720p", "height": 720, "bandwidth": 3_096_000},
    {"id": "480p", "height": 480, "bandwidth": 1_296_000},
    {"id": "360p", "height": 360, "bandwidth": 796_000},
]


class QualityPolicyTests(unittest.TestCase):
    def test_starts_at_middle_level(self):
        self.assertEqual(QualityPolicy(LEVELS).level_id, "480p")

    def test_steps_down_when_throughput_is_below_bitrate(self):
        policy = QualityPolicy(LEVELS)
        # 500 kbit/s delivered for one 6 s segment.
        policy.record(received_bytes=375_000, seconds=6.0, loss_ratio=0.0)
        self.assertEqual(policy.level_id, "360p")
        policy.record(received_bytes=375_000, seconds=6.0, loss_ratio=0.0)
        self.assertEqual(policy.level_id, "360p")  # lowest level stays put

    def test_steps_down_on_packet_loss(self):
        policy = QualityPolicy(LEVELS)
        policy.record(received_bytes=10_000_000, seconds=1.0, loss_ratio=0.10)
        self.assertEqual(policy.level_id, "360p")

    def test_steps_up_after_a_streak_of_good_segments(self):
        policy = QualityPolicy(LEVELS)
        for _ in range(2):
            policy.record(received_bytes=10_000_000, seconds=1.0, loss_ratio=0.0)
            self.assertEqual(policy.level_id, "480p")
        policy.record(received_bytes=10_000_000, seconds=1.0, loss_ratio=0.0)
        self.assertEqual(policy.level_id, "720p")

    def test_does_not_step_up_without_headroom(self):
        policy = QualityPolicy(LEVELS)
        # About 2 Mbit/s: above 480p but below 720p times 1.25.
        for _ in range(5):
            policy.record(received_bytes=1_500_000, seconds=6.0, loss_ratio=0.0)
        self.assertEqual(policy.level_id, "480p")

    def test_manual_mode_ignores_measurements(self):
        policy = QualityPolicy(LEVELS)
        policy.set_mode("360p")
        self.assertEqual(policy.level_id, "360p")
        for _ in range(5):
            policy.record(received_bytes=10_000_000, seconds=1.0, loss_ratio=0.0)
        self.assertEqual(policy.level_id, "360p")
        policy.set_mode(AUTO)
        self.assertEqual(policy.mode, AUTO)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            QualityPolicy(LEVELS).set_mode("1080p")


class FakeSnapshot:
    def __init__(self, path, error=None):
        self.path = path
        self.error = error


class FakeReceiver:
    def __init__(self, manifest=None, error=None):
        self.manifest = manifest
        self.error = error
        self.requests = []
        self.released = []
        self.closed = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.missed = 0
        self.packets = 0

    def request_manifest(self, name, timeout):
        if self.error:
            raise RuntimeError(self.error)
        return dict(self.manifest)

    def request_segment(self, name, level, index):
        transfer_id = bytes([len(self.requests) + 1]) * 16
        self.requests.append((name, level, index, transfer_id))
        path = Path(self.temp_dir.name) / f"{level}-{index}.bin"
        path.write_bytes(b"x" * 1000)
        self.packets += 10
        return transfer_id

    def wait_for_transfer_complete(self, transfer_id, timeout=None):
        for _, level, index, tid in self.requests:
            if tid == transfer_id:
                return FakeSnapshot(Path(self.temp_dir.name) / f"{level}-{index}.bin")
        return None

    def release_transfer(self, transfer_id):
        self.released.append(transfer_id)

    def loss_counts(self):
        return self.missed, self.packets

    def close(self):
        self.closed = True


MANIFEST = {
    "type": "MANIFEST",
    "name": "clip.mp4",
    "codecs": "avc1.640028,mp4a.40.2",
    "segment_seconds": 6.0,
    "segment_count": 3,
    "last_segment_seconds": 1.0,
    "duration": 13.0,
    "levels": LEVELS,
}


def wait_until_ready(session, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.describe()["status"] != "preparing":
            return
        time.sleep(0.01)


class AdaptiveVideoSessionTests(unittest.TestCase):
    def test_loads_manifest_and_reports_ready(self):
        receiver = FakeReceiver(manifest=MANIFEST)
        session = AdaptiveVideoSession(receiver, "clip.mp4")
        session.start()
        wait_until_ready(session)
        info = session.describe()
        self.assertEqual(info["status"], "ready")
        self.assertEqual(info["segment_count"], 3)
        self.assertEqual(info["level"], "480p")
        self.assertEqual(info["mode"], AUTO)
        self.assertEqual(len(info["levels"]), 3)

    def test_reports_manifest_error(self):
        session = AdaptiveVideoSession(FakeReceiver(error="ffmpeg failed"), "clip.mp4")
        session.start()
        wait_until_ready(session)
        info = session.describe()
        self.assertEqual(info["status"], "error")
        self.assertEqual(info["message"], "ffmpeg failed")

    def test_fetch_segment_uses_current_level_and_releases_transfer(self):
        receiver = FakeReceiver(manifest=MANIFEST)
        session = AdaptiveVideoSession(receiver, "clip.mp4")
        session.start()
        wait_until_ready(session)
        level, data = session.fetch_segment(1)
        self.assertEqual(level, "480p")
        self.assertEqual(len(data), 1000)
        self.assertEqual(receiver.requests[0][:3], ("clip.mp4", "480p", 1))
        self.assertEqual(receiver.released, [receiver.requests[0][3]])
        session.close()

    def test_fetch_rejects_missing_segment(self):
        receiver = FakeReceiver(manifest=MANIFEST)
        session = AdaptiveVideoSession(receiver, "clip.mp4")
        session.start()
        wait_until_ready(session)
        with self.assertRaises(ValueError):
            session.fetch_segment(3)
        session.close()

    def test_fetch_init_validates_level(self):
        receiver = FakeReceiver(manifest=MANIFEST)
        session = AdaptiveVideoSession(receiver, "clip.mp4")
        session.start()
        wait_until_ready(session)
        with self.assertRaises(ValueError):
            session.fetch_init("1080p")
        self.assertEqual(len(session.fetch_init("360p")), 1000)
        self.assertEqual(receiver.requests[-1][2], -1)
        session.close()

    def test_manual_quality_is_used_for_next_segment(self):
        receiver = FakeReceiver(manifest=MANIFEST)
        session = AdaptiveVideoSession(receiver, "clip.mp4")
        session.start()
        wait_until_ready(session)
        session.set_quality("720p")
        level, _ = session.fetch_segment(0)
        self.assertEqual(level, "720p")
        self.assertEqual(session.describe()["mode"], "720p")
        session.close()

    def test_traffic_meter_counts_recent_bytes(self):
        receiver = FakeReceiver(manifest=MANIFEST)
        session = AdaptiveVideoSession(receiver, "clip.mp4")
        session.start()
        wait_until_ready(session)
        session.fetch_segment(0)
        bps = session.describe()["traffic_bps"]
        self.assertEqual(bps, round(1000 * 8 / TRAFFIC_WINDOW_SECONDS))
        session.close()
        self.assertTrue(receiver.closed)


if __name__ == "__main__":
    unittest.main()

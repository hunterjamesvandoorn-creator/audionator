import unittest

from download_meter import RETAIN_SECONDS, DownloadMeter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class DownloadMeterTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.meter = DownloadMeter(clock=self.clock)

    def test_reports_megabits_per_second_between_samples(self):
        self.meter.sample()
        self.meter.add(125_000)
        self.clock.now += 1.0
        self.meter.sample()
        self.assertEqual(self.meter.history(60), [(0.0, 1.0)])

    def test_skips_samples_closer_than_the_interval(self):
        self.meter.sample()
        self.clock.now += 0.5
        self.meter.sample()
        self.assertEqual(len(self.meter._samples), 1)

    def test_history_excludes_points_older_than_the_window(self):
        for _ in range(10):
            self.meter.add(125_000)
            self.meter.sample()
            self.clock.now += 1.0
        points = self.meter.history(3)
        self.assertEqual(len(points), 3)
        self.assertTrue(all(seconds_ago <= 3 for seconds_ago, _ in points))

    def test_trims_samples_past_retention(self):
        self.meter.sample()
        self.clock.now += RETAIN_SECONDS + 10
        self.meter.sample()
        self.assertEqual(len(self.meter._samples), 1)


if __name__ == "__main__":
    unittest.main()

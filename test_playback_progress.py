from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import AudionatorAPI


class PlaybackProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings_path = self.root / "settings.json"
        self.track = self.root / "track.mp3"
        self.track.write_bytes(b"audio")
        mp3 = self._patch("client.MP3")
        mp3.return_value.info.length = 180.0
        self.mixer = self._patch("client.pygame.mixer")
        self.music = self.mixer.music
        self.music.get_busy.return_value = True

    def _patch(self, target: str) -> mock.MagicMock:
        patcher = mock.patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _new_api(self) -> AudionatorAPI:
        return AudionatorAPI(self.root, mock.Mock(), self.settings_path)

    def test_seek_position_is_saved_and_resumed_by_a_new_session(self) -> None:
        api = self._new_api()
        api._play_file(self.track, "track.mp3")
        api.seek(42.0)

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["port"], 5005)
        self.assertAlmostEqual(saved["playback_positions"]["track.mp3"], 42.0, delta=0.5)

        restored = self._new_api()
        restored._play_file(self.track, "track.mp3")
        start = self.music.play.call_args.kwargs["start"]
        self.assertAlmostEqual(start, 42.0, delta=0.5)
        self.assertAlmostEqual(restored.playback_offset, 42.0, delta=0.5)
        self.assertIn("from 0:42", restored.message)

    def test_position_within_the_final_seconds_is_not_saved(self) -> None:
        api = self._new_api()
        api._play_file(self.track, "track.mp3")
        api.seek(178.0)

        self.assertEqual(self._new_api().playback_positions, {})

    def test_finished_track_clears_its_saved_position(self) -> None:
        api = self._new_api()
        api._play_file(self.track, "track.mp3")
        api.seek(42.0)
        self.assertIn("track.mp3", self._new_api().playback_positions)

        self.music.get_busy.return_value = False
        api._update_audio_state()

        self.assertNotIn("track.mp3", api.playback_positions)
        self.assertEqual(self._new_api().playback_positions, {})

    def test_invalid_saved_positions_are_ignored(self) -> None:
        self.settings_path.write_text(
            json.dumps({"host": "", "port": 5005, "playback_positions": {"a.mp3": -3, "b.mp3": "x", "c.mp3": 12.5}}),
            encoding="utf-8",
        )

        self.assertEqual(self._new_api().playback_positions, {"c.mp3": 12.5})


if __name__ == "__main__":
    unittest.main()

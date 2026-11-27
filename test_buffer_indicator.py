from __future__ import annotations

import unittest

from client_ui import CLIENT_HTML


class BufferIndicatorMarkupTests(unittest.TestCase):
    def test_timeline_elements_exist_in_page(self) -> None:
        for element_id in ("video-stage", "video-timeline", "video-played", "video-buffered", "video-thumb", "video-hover-time", "video-time", "video-toggle"):
            self.assertIn(f'id="{element_id}"', CLIENT_HTML)

    def test_script_updates_timeline_on_player_events(self) -> None:
        self.assertIn("function updateVideoTimeline(", CLIENT_HTML)
        for event_name in ("progress", "timeupdate", "seeked", "emptied", "durationchange"):
            self.assertIn(f'"{event_name}"', CLIENT_HTML)

    def test_full_length_is_set_on_media_source(self) -> None:
        self.assertIn("mediaSource.duration = info.duration;", CLIENT_HTML)

    def test_buffered_layer_has_size_and_refreshes_while_loading(self) -> None:
        self.assertIn(".video-buffered { position: absolute; inset: 0; }", CLIENT_HTML)
        self.assertIn("setInterval(updateVideoTimeline, 250)", CLIENT_HTML)


if __name__ == "__main__":
    unittest.main()

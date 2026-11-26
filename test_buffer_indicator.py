from __future__ import annotations

import unittest

from client_ui import CLIENT_HTML


class BufferIndicatorMarkupTests(unittest.TestCase):
    def test_indicator_elements_exist_in_page(self) -> None:
        for element_id in ("video-buffer", "video-buffer-text", "video-buffer-fill"):
            self.assertIn(f'id="{element_id}"', CLIENT_HTML)

    def test_script_updates_indicator_on_player_events(self) -> None:
        self.assertIn("function updateBufferIndicator()", CLIENT_HTML)
        for event_name in ("progress", "timeupdate", "seeked", "emptied"):
            self.assertIn(f'"{event_name}"', CLIENT_HTML)


if __name__ == "__main__":
    unittest.main()

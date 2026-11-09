"""A battery level of 255 (0xFF) means unknown: the UI must not show "255 %"."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mousepods_web as w  # noqa: E402


class TestBatteryView(unittest.TestCase):
    def test_unknown_level_is_none(self):
        v = w.battery_view([{"part": "left", "level": 255, "state": "charging"},
                            {"part": "right", "level": 100, "state": "charging"}])
        self.assertIsNone(v[0]["level"])
        self.assertEqual(v[1]["level"], 100)

    def test_ui_shows_a_dash_for_unknown(self):
        html = open(os.path.join(os.path.dirname(w.__file__), "ui", "index.html"), encoding="utf-8").read()
        self.assertIn('b.level==null?', html)


if __name__ == "__main__":
    unittest.main()

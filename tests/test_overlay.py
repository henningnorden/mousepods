# SPDX-License-Identifier: Apache-2.0
"""Desktop app helpers for the calibration overlay (no GTK needed).

Run:  python -m unittest tests.test_overlay -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from desktop import overlay  # noqa: E402


class ParseMessageTests(unittest.TestCase):
    def p(self, obj):
        return overlay.parse_message(json.dumps(obj))

    def test_valid_messages(self):
        self.assertEqual(self.p({"cmd": "monitors", "saved": [{"connector": "DP-1", "main": True}]}),
                         {"cmd": "monitors", "saved": [{"connector": "DP-1", "main": True}]})
        self.assertEqual(self.p({"cmd": "monitors"}), {"cmd": "monitors", "saved": []})
        self.assertEqual(self.p({"cmd": "flash", "connector": "HDMI-A-1"}), {"cmd": "flash", "connector": "HDMI-A-1"})
        msg = {"cmd": "overlay", "monitors": [{"connector": "DP-1", "roles": ["main", "left"]}]}
        self.assertEqual(self.p(msg), msg)
        self.assertEqual(self.p({"cmd": "overlay_close", "step": "done"}), {"cmd": "overlay_close"})

    def test_invalid_messages(self):
        for bad in ("not json", "[]", json.dumps({"cmd": "rm -rf"}), json.dumps({"cmd": "flash", "connector": 3}),
                    json.dumps({"cmd": "overlay", "monitors": [{"connector": "DP-1", "roles": ["evil"]}]}),
                    json.dumps({"cmd": "overlay", "monitors": "DP-1"}), json.dumps({"cmd": "overlay", "monitors": []})):
            self.assertIsNone(overlay.parse_message(bad), bad)


class RolesTests(unittest.TestCase):
    def test_one_monitor_has_every_role(self):
        self.assertEqual(overlay.roles_for([{"connector": "DP-1", "main": True}]), {"DP-1": ["main", "left", "right"]})

    def test_three_monitors_main_in_the_middle(self):
        sel = [{"connector": "L", "main": False}, {"connector": "M", "main": True}, {"connector": "R", "main": False}]
        self.assertEqual(overlay.roles_for(sel), {"L": ["left"], "M": ["main"], "R": ["right"]})

    def test_main_on_the_left_of_two(self):
        sel = [{"connector": "A", "main": True}, {"connector": "B", "main": False}]
        self.assertEqual(overlay.roles_for(sel), {"A": ["main", "left"], "B": ["right"]})


class ResolveTests(unittest.TestCase):
    def test_resolve_monitors_drops_missing(self):
        saved = [{"connector": "L", "main": False}, {"connector": "M", "main": True}, {"connector": "R", "main": False}]
        self.assertEqual(overlay.resolve_monitors(saved, ["R", "L"]),
                         [{"connector": "L", "main": True}, {"connector": "R", "main": False}])
        self.assertEqual(overlay.resolve_monitors(saved, ["X", "Y"]), [{"connector": "X", "main": True}])
        self.assertEqual(overlay.resolve_monitors([], ["X"]), [{"connector": "X", "main": True}])
        self.assertEqual(overlay.resolve_monitors(saved, []), [])

    def test_is_own_page(self):
        self.assertTrue(overlay.is_own_page("http://127.0.0.1:8737/demo", 8737))
        self.assertTrue(overlay.is_own_page("http://localhost:8737/", 8737))
        for other in ("http://127.0.0.1:9999/demo", "https://github.com/x", "http://127.0.0.1:8737.evil.com/",
                      "file:///etc/passwd", "", "http://127.0.0.1:8737@evil.com/"):
            self.assertFalse(overlay.is_own_page(other, 8737), other)

    def test_overlay_url(self):
        self.assertEqual(overlay.overlay_url(8737, ["main", "left"]),
                         "http://127.0.0.1:8737/calibrate?roles=main%2Cleft")


if __name__ == "__main__":
    unittest.main()

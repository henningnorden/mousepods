# SPDX-License-Identifier: Apache-2.0
"""Games page in the web UI; the JS preview curve must match game_profiles.map_axis.

Run:  python -m unittest tests.test_games_ui -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import game_profiles as gp  # noqa: E402

UI = os.path.join(ROOT, "ui", "index.html")


def html():
    with open(UI, encoding="utf-8") as f:
        return f.read()


class GamesUiTests(unittest.TestCase):
    def test_games_page_and_nav(self):
        h = html()
        self.assertRegex(h, r'<a data-p="games">')
        self.assertRegex(h, r'<section class="page" id="p-games">\s*<h1 data-i18n="games\.title">')
        self.assertIn('id="games-list"', h)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_map_axis_matches_python(self):
        h = html()
        m = re.search(r"function mapAxis\(d,a\)\{.*?\n", h)
        self.assertIsNotNone(m, "mapAxis missing")
        cases = []
        for axis in ({"enabled": True, "invert": False, "gain": 2.0, "curve": 1.4, "limit": 180},
                     {"enabled": True, "invert": True, "gain": 3.0, "curve": 1.0, "limit": 90},
                     {"enabled": False, "invert": False, "gain": 1.0, "curve": 1.0, "limit": 90},
                     {"enabled": True, "invert": False, "gain": 0.5, "curve": 2.7, "limit": 45}):
            for d in (-120.0, -40.0, -3.5, 0.0, 0.7, 12.0, 40.0, 75.0):
                cases.append((d, axis, gp.map_axis(d, axis)))
        script = m.group(0) + "const C=" + json.dumps([[d, a] for d, a, _ in cases]) + \
            ";console.log(JSON.stringify(C.map(([d,a])=>mapAxis(d,a))))"
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        for (d, a, want), got in zip(cases, out):
            self.assertAlmostEqual(got, want, places=6, msg=f"{d} {a}")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_esc_escapes_quotes_for_attributes(self):
        m = re.search(r"function esc\(s\)\{.*?\n", html())
        script = m.group(0) + "console.log(esc(`a\"b'c<d>&`))"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "a&quot;b&#39;c&lt;d&gt;&amp;")


if __name__ == "__main__":
    unittest.main()

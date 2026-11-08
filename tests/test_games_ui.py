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
        for axis in ({"enabled": True, "invert": False, "edge_neg": 71.0, "edge_pos": 71.0, "target": 180.0, "curve": 1.4},
                     {"enabled": True, "invert": True, "edge_neg": 10.0, "edge_pos": 30.0, "target": 90.0, "curve": 1.0},
                     {"enabled": False, "invert": False, "edge_neg": 40.0, "edge_pos": 40.0, "target": 90.0, "curve": 1.0},
                     {"enabled": True, "invert": False, "edge_neg": 15.0, "edge_pos": 60.0, "target": 45.0, "curve": 2.7}):
            for d in (-120.0, -40.0, -3.5, 0.0, 0.7, 12.0, 40.0, 75.0):
                cases.append((d, axis, gp.map_axis(d, axis)))
        script = m.group(0) + "const C=" + json.dumps([[d, a] for d, a, _ in cases]) + \
            ";console.log(JSON.stringify(C.map(([d,a])=>mapAxis(d,a))))"
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        for (d, a, want), got in zip(cases, out):
            self.assertAlmostEqual(got, want, places=6, msg=f"{d} {a}")

    def test_profile_uses_the_edge_model(self):
        h = html()
        rp = h[h.index("function readProfile("):h.index("function gameError(")]
        for key in ("edge_neg", "edge_pos", "target", "curve", "monitors"):   # monitors: kept on every save
            self.assertIn(key, rp)
        for gone in ('"gain"', "gain:", "limit:"):
            self.assertNotIn(gone, rp)
        card = h[h.index("function gameCard"):h.index("function readProfile")]
        self.assertIn("data-calibrate", card)
        self.assertNotIn('data-f="gain"', card)

    def test_desktop_bridge_and_calibration_flow(self):
        h = html()
        self.assertIn("window.mpDesktop=", h)
        for needle in ('cmd:"monitors"', 'cmd:"overlay"', 'cmd:"flash"', '"/api/calibration"', "calibration_end",
                       "requestFullscreen", "function rolesFor("):
            self.assertIn(needle, h)

    def test_game_texts_in_every_language(self):
        keys = set(re.findall(r'\bt\("(js\.(?:games|calib_ui)\.[^"]+)"', html()))
        self.assertIn("js.games.calibrate", keys)
        for lang in ("en", "de", "fr"):
            with open(os.path.join(ROOT, "ui", "i18n", f"{lang}.json"), encoding="utf-8") as f:
                table = json.load(f)
            self.assertEqual(sorted(k for k in keys if k not in table), [], lang)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_roles_match_python(self):
        from desktop import overlay
        h = html()
        fn = re.search(r"function rolesFor\(.*?\n", h).group(0)
        sels = [[{"connector": "A", "main": True}],
                [{"connector": "L", "main": False}, {"connector": "M", "main": True}, {"connector": "R", "main": False}],
                [{"connector": "A", "main": True}, {"connector": "B", "main": False}]]
        script = fn + "const S=" + json.dumps(sels) + ";console.log(JSON.stringify(S.map(rolesFor)))"
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [overlay.roles_for(s) for s in sels])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_sensitivity_matches_python(self):
        h = html()
        fns = "".join(re.search(rf"function {n}\(.*?\n", h).group(0) for n in ("sensToCurve", "curveToSens"))
        vals = [0.0, 0.3, 0.75, 1.0, -1.0, 2.0]
        script = fns + "const V=" + json.dumps(vals) + ";console.log(JSON.stringify(V.map(v=>[sensToCurve(v),curveToSens(v*3)])))"
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        for v, (c, s) in zip(vals, out):
            self.assertAlmostEqual(c, gp.sensitivity_to_curve(v), places=6)
            self.assertAlmostEqual(s, gp.curve_to_sensitivity(v * 3), places=6)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_esc_escapes_quotes_for_attributes(self):
        m = re.search(r"function esc\(s\)\{.*?\n", html())
        script = m.group(0) + "console.log(esc(`a\"b'c<d>&`))"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "a&quot;b&#39;c&lt;d&gt;&amp;")



class SteamChoiceUiTests(unittest.TestCase):
    def test_choice_only_for_games_in_both_steams(self):
        h = html()
        card = h[h.index("function gameCard"):h.index("function readProfile")]
        self.assertIn("g.both", card)
        self.assertIn("data-steam", card)
        self.assertIn('steam:', h[h.index("function renderGames"):h.index("function loadGames")])


class BrowserCalibrationEscTests(unittest.TestCase):
    def test_leaving_full_screen_cancels_the_calibration(self):
        """In a browser Esc ends full screen before the overlay page sees it: the parent cancels."""
        h = html()
        frame = h[h.index("function calibFrame("):h.index("function calibWait(")]
        self.assertIn("fullscreenchange", frame)
        self.assertIn('action:"cancel"', frame)
        self.assertIn(".focus()", frame)


if __name__ == "__main__":
    unittest.main()

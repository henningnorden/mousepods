# SPDX-License-Identifier: Apache-2.0
"""The calibration overlay page: served at /calibrate, translatable, closes itself at the end.

Run:  python -m unittest tests.test_calibrate_page -v
"""
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mousepods_web as web  # noqa: E402

PAGE = os.path.join(ROOT, "ui", "calibrate.html")


def page():
    with open(PAGE, encoding="utf-8") as f:
        return f.read()


def table(lang):
    with open(os.path.join(ROOT, "ui", "i18n", f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)


class CalibratePageTests(unittest.TestCase):
    def test_every_key_in_every_language(self):
        h = page()
        keys = set(re.findall(r'data-i18n="([^"]+)"', h)) | set(re.findall(r'\bt\("([^"]+)"\s*[,)]', h))
        keys |= {f"calib_overlay.step_{s}" for s in ("center", "left", "right", "top", "bottom")}
        self.assertIn("calib_overlay.hold", keys)
        for lang in ("en", "de", "fr"):
            self.assertEqual(sorted(k for k in keys if k not in table(lang)), [], lang)

    def test_shows_the_head_movement_phase_first(self):
        h = page()
        self.assertIn('st.phase==="head"', h)
        self.assertIn('t("calib_overlay.head")', h)

    def test_talks_to_the_api_and_closes_itself(self):
        h = page()
        for needle in ('"/api/calibration"', "overlay_close", "calibration_end", "Escape", '"/api/i18n"'):
            self.assertIn(needle, h)

    def test_markup_fallbacks_are_the_english_texts(self):
        en = table("en")
        pairs = re.findall(r'data-i18n="([^"]+)">([^<]*)<', page())
        self.assertTrue(pairs)
        for key, text in pairs:
            self.assertEqual(text, en[key], key)

    def test_served_at_calibrate(self):
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "config.json")
        with open(cfg, "w") as f:
            f.write("{}")
        ui = web.WebUI(types.SimpleNamespace(cfg={"ui_lang": "en"}), cfg, port=0)
        ui.start()
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{ui.server.server_address[1]}/calibrate?roles=main,left")
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn(b"/api/calibration", r.read())
        finally:
            ui.server.shutdown()
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()

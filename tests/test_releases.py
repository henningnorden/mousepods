# SPDX-License-Identifier: Apache-2.0
"""Version and release notes in the app: read from the AppStream metainfo, channel from .flatpak-info,
"what's new" shown once per new version.

Run:  python -m unittest tests.test_releases -v
"""
import json
import os
import queue
import shutil
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import releases  # noqa: E402
import mousepods_web as web  # noqa: E402

META = """<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application"><id>x</id>
  <releases>
    <release version="1.1.0" date="2026-11-20">
      <description><p>Head tracking for Proton games.</p>
        <ul><li>Games page without opentrack</li><li>Calibration with points</li></ul></description>
    </release>
    <release version="1.0.0" date="2026-11-04">
      <description><p>First release.</p></description>
    </release>
  </releases>
</component>"""

FLATPAK_INFO = """[Application]
name=io.github.henningnorden.MousePods

[Instance]
branch=test
app-commit=25a6321bcdef0123456789
flatpak-version=1.16.0
"""


class ReleasesTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.meta = os.path.join(self.d, "m.xml")
        with open(self.meta, "w", encoding="utf-8") as f:
            f.write(META)
        self.info = os.path.join(self.d, ".flatpak-info")
        with open(self.info, "w", encoding="utf-8") as f:
            f.write(FLATPAK_INFO)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_all_releases_newest_first(self):
        rs = releases.load(self.meta)
        self.assertEqual([r["version"] for r in rs], ["1.1.0", "1.0.0"])
        self.assertEqual(rs[0]["items"], ["Games page without opentrack", "Calibration with points"])
        self.assertEqual(rs[0]["paragraphs"], ["Head tracking for Proton games."])
        self.assertEqual(rs[1]["date"], "2026-11-04")

    def test_missing_or_broken_metainfo(self):
        self.assertEqual(releases.load(os.path.join(self.d, "nope.xml")), [])
        with open(self.meta, "w") as f:
            f.write("<not xml")
        self.assertEqual(releases.load(self.meta), [])

    def test_channel_and_commit_from_flatpak_info(self):
        self.assertEqual(releases.flatpak_instance(self.info), {"channel": "test", "commit": "25a6321"})
        self.assertEqual(releases.flatpak_instance(os.path.join(self.d, "none")), {"channel": None, "commit": None})

    def test_info_and_whats_new(self):
        i = releases.info(self.meta, self.info, seen=None)
        self.assertEqual((i["version"], i["channel"], i["commit"]), ("1.1.0", "test", "25a6321"))
        self.assertIsNone(i["whats_new"])                     # first start: nothing to announce
        self.assertEqual(i["whats_new"], None)
        self.assertEqual(releases.info(self.meta, self.info, seen="1.0.0")["whats_new"]["version"], "1.1.0")
        self.assertIsNone(releases.info(self.meta, self.info, seen="1.1.0")["whats_new"])

    def test_release_notes_checker_uses_the_same_parser(self):
        sys.path.insert(0, os.path.join(ROOT, "packaging", "flatpak"))
        import release_notes
        self.assertIs(release_notes.load, releases.load)


class VersionApiTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = os.path.join(self.d, "config.json")
        with open(self.cfg, "w") as f:
            json.dump({"whats_new_seen": "1.0.0"}, f)
        self.meta = os.path.join(self.d, "m.xml")
        with open(self.meta, "w", encoding="utf-8") as f:
            f.write(META)
        self.patch = unittest.mock.patch.dict(os.environ, {"MOUSEPODS_METAINFO": self.meta,
                                                           "MOUSEPODS_FLATPAK_INFO": os.path.join(self.d, "none")})
        self.patch.start()
        self.ui = web.WebUI(types.SimpleNamespace(cfg={"ui_lang": "en", "whats_new_seen": "1.0.0"}), self.cfg, port=0)
        self.ui.q = queue.Queue()
        self.ui.start()
        self.base = f"http://127.0.0.1:{self.ui.server.server_address[1]}"

    def tearDown(self):
        self.ui.server.shutdown()
        self.patch.stop()
        shutil.rmtree(self.d)

    def test_get_and_mark_seen(self):
        v = json.load(urllib.request.urlopen(self.base + "/api/version"))
        self.assertEqual(v["version"], "1.1.0")
        self.assertEqual(v["whats_new"]["version"], "1.1.0")
        self.assertEqual(len(v["releases"]), 2)
        req = urllib.request.Request(self.base + "/api/version", method="POST", data=b'{"seen": "1.1.0"}',
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req).read()
        with open(self.cfg) as f:
            self.assertEqual(json.load(f)["whats_new_seen"], "1.1.0")
        self.assertIsNone(json.load(urllib.request.urlopen(self.base + "/api/version"))["whats_new"])

    def test_first_start_remembers_the_version(self):
        with open(self.cfg, "w") as f:
            json.dump({}, f)
        self.assertIsNone(json.load(urllib.request.urlopen(self.base + "/api/version"))["whats_new"])
        with open(self.cfg) as f:
            self.assertEqual(json.load(f)["whats_new_seen"], "1.1.0")   # the next update gets announced

    def test_seen_must_be_a_short_string(self):
        for body in (b'{"seen": 5}', b'{"seen": "' + b"x" * 100 + b'"}', b'{}'):
            req = urllib.request.Request(self.base + "/api/version", method="POST", data=body,
                                         headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 400)


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()


class VersionUiTests(unittest.TestCase):
    def html(self):
        with open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_version_link_page_and_card(self):
        h = self.html()
        foot = h[h.index('<div class="side-foot">'):h.index("</aside>")]
        self.assertIn('id="side-version"', foot)                    # small link bottom left
        self.assertRegex(h, r'<section class="page" id="p-news">\s*<h1 data-i18n="news\.title">')
        self.assertIn('<dialog id="news-dlg"', h)                   # "what's new" as a popup
        self.assertNotIn('id="ov-news"', h)
        self.assertIn('showModal()', h)
        self.assertRegex(h, r'news-dlg"\)\.addEventListener\("close"')   # Esc or a button: marked as seen
        self.assertIn('"/api/version"', h)
        self.assertIn('go("news")', h)

    def test_texts_in_every_language(self):
        import re
        h = self.html()
        keys = {k for k in re.findall(r'data-i18n="(news\.[^"]+)"', h)} | set(re.findall(r'\bt\("(js\.news\.[^"]+)"', h))
        self.assertIn("js.news.got_it", keys)
        for lang in ("en", "de", "fr"):
            with open(os.path.join(ROOT, "ui", "i18n", f"{lang}.json"), encoding="utf-8") as f:
                table = json.load(f)
            self.assertEqual(sorted(k for k in keys if k not in table), [], lang)

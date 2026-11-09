"""The website's cockpit demo is the app's page, and the server only lets the site read the pose and recenter."""
import filecmp
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSiteDemo(unittest.TestCase):
    def test_site_copy_is_in_sync(self):
        self.assertTrue(filecmp.cmp(os.path.join(ROOT, "ui", "cockpit.html"), os.path.join(ROOT, "docs", "demo.html"), shallow=False),
                        "docs/demo.html must be a copy of ui/cockpit.html")
        for f in ("three.module.min.js", "Sky.js", "LICENSE-three.txt"):
            self.assertTrue(filecmp.cmp(os.path.join(ROOT, "ui", "vendor", f), os.path.join(ROOT, "docs", "vendor", f), shallow=False), f)

    def test_page_uses_relative_vendor_paths_and_api_base(self):
        html = open(os.path.join(ROOT, "ui", "cockpit.html"), encoding="utf-8").read()
        self.assertIn('"three": "./vendor/three.module.min.js"', html)
        self.assertNotIn('fetch("/api/', html)

    def test_site_origin_limited_to_pose_and_recenter(self):
        import mousepods_web as w
        self.assertEqual(w.SITE_ORIGINS, ("https://starman-tech.github.io",))
        self.assertEqual(set(w.SITE_PATHS), {"/api/pose", "/api/i18n", "/api/cmd"})



class TestDevicesList(unittest.TestCase):
    def test_devices_json_is_valid(self):
        import json
        d = json.load(open(os.path.join(ROOT, "docs", "devices.json"), encoding="utf-8"))
        self.assertTrue(d["tested"] and d["support"])
        for t in d["tested"]:
            self.assertIn(t["result"], ("ok", "partial", "fail"))
            for k in ("device", "system", "details", "by", "date"):
                self.assertTrue(t[k], (t, k))
        for t in d["support"]:
            self.assertIn(t["status"], ("supported", "experimental", "untested", "planned", "unknown", "impossible"))
        self.assertIn('fetch("devices.json"', open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()

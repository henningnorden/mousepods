"""Static checks of the browser extension (no browser needed)."""
import json
import os
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "browser-extension")


class TestBrowserExtension(unittest.TestCase):
    def test_manifest_and_files(self):
        m = json.load(open(os.path.join(ROOT, "manifest.json")))
        self.assertEqual(m["manifest_version"], 3)
        self.assertEqual(m["host_permissions"], ["http://127.0.0.1:8737/*"])   # only the local MousePods
        files = [m["background"]["service_worker"], m["action"]["default_popup"], *m["icons"].values()]
        files += [f for cs in m["content_scripts"] for f in cs["js"]]
        for f in files:
            self.assertTrue(os.path.exists(os.path.join(ROOT, f)), f)
        worlds = {cs["js"][0]: cs.get("world", "ISOLATED") for cs in m["content_scripts"]}
        self.assertEqual(worlds["page.js"], "MAIN")        # YouTube's player API lives in the page world

    def test_gyroscope_mode_is_off_by_default(self):
        for f in ("bridge.js", "page.js", "popup.js"):
            self.assertIn("gyro: false", open(os.path.join(ROOT, f)).read(), f)


if __name__ == "__main__":
    unittest.main()

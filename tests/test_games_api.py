# SPDX-License-Identifier: Apache-2.0
"""Games API: list Steam/Proton games, enable/disable the bridge, edit profiles.

Run:  python -m unittest tests.test_games_api -v
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
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import games_service  # noqa: E402
import mousepods_web as web  # noqa: E402
from test_bridge_install import DLLS, OPENTRACK_REG  # noqa: E402
from test_steam_games import make_steam  # noqa: E402


class GamesApiTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.steam = make_steam(self.home)
        with open(os.path.join(self.steam, "steamapps", "compatdata", "244210", "pfx", "user.reg"), "w") as f:
            f.write(OPENTRACK_REG)
        self.dlls = os.path.join(self.home, "dlls")
        os.makedirs(self.dlls)
        for n in DLLS:
            with open(os.path.join(self.dlls, n), "wb") as f:
                f.write(b"MZ")
        self.cfg_path = os.path.join(self.home, "config.json")
        with open(self.cfg_path, "w") as f:
            f.write("{}")
        self.env = mock.patch.dict(os.environ, {"MOUSEPODS_STEAM_ROOT": self.steam, "MOUSEPODS_DLL_DIR": self.dlls})
        self.env.start()
        games_service.bridge_install._LAST_SEEN.clear()
        self.running = set()
        self.run_patch = mock.patch.object(games_service, "is_running", lambda appid: appid in self.running)
        self.run_patch.start()
        self.ui = web.WebUI(types.SimpleNamespace(cfg={"ui_lang": "en", "games": {}}), self.cfg_path, port=0)
        self.ui.q = queue.Queue()
        self.ui.start()
        self.base = f"http://127.0.0.1:{self.ui.server.server_address[1]}"

    def tearDown(self):
        self.ui.server.shutdown()
        self.run_patch.stop()
        self.env.stop()
        shutil.rmtree(self.home)

    def get(self, path):
        return json.load(urllib.request.urlopen(self.base + path))

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req))

    def saved(self):
        with open(self.cfg_path) as f:
            return json.load(f)

    def test_list_games(self):
        d = self.get("/api/games")
        self.assertTrue(d["steam_found"])
        names = [g["name"] for g in d["games"]]
        self.assertEqual(names, ["Assetto Corsa", "Euro Truck Simulator 2"])
        ac = d["games"][0]
        self.assertEqual((ac["appid"], ac["has_prefix"], ac["status"], ac["enabled"], ac["running"]),
                         ("244210", True, "foreign", False, False))
        self.assertTrue(ac["launch_option_hint"])
        self.assertEqual(ac["foreign_tracker"], "opentrack")
        self.assertEqual(d["missing_libraries"], [])
        self.assertEqual(ac["profile"]["axes"]["yaw"]["target"], 180.0)
        self.assertFalse(d["games"][1]["has_prefix"])

    def test_enable_installs_and_saves(self):
        self.post("/api/games/244210", {"enabled": True})
        self.assertEqual(self.get("/api/games")["games"][0]["status"], "on")
        self.assertTrue(self.saved()["games"]["244210"]["enabled"])
        self.assertEqual(self.ui.q.get_nowait(), ("cmd", "reload"))

    def test_enable_running_is_409(self):
        self.running.add("244210")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/games/244210", {"enabled": True})
        self.assertEqual(cm.exception.code, 409)
        body = json.load(cm.exception)
        self.assertEqual(body["code"], "running")
        self.assertNotIn("games", self.saved())

    def test_enable_without_prefix_is_409(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/games/227300", {"enabled": True})
        self.assertEqual(json.load(cm.exception)["code"], "no_prefix")

    def test_profile_update_normalized_and_live(self):
        self.post("/api/games/244210", {"enabled": True})
        self.ui.q.get_nowait()
        self.post("/api/games/244210", {"profile": {"smoothing": 3, "axes": {"roll": {"enabled": False}}}})
        prof = self.saved()["games"]["244210"]["profile"]
        self.assertEqual(prof["smoothing"], 1.0)
        self.assertFalse(prof["axes"]["roll"]["enabled"])
        self.assertTrue(self.saved()["games"]["244210"]["enabled"])
        self.assertEqual(self.ui.q.get_nowait(), ("cmd", "reload"))

    def test_disable_restores(self):
        self.post("/api/games/244210", {"enabled": True})
        self.post("/api/games/244210", {"enabled": False})
        self.assertEqual(self.get("/api/games")["games"][0]["status"], "foreign")
        self.assertFalse(self.saved()["games"]["244210"]["enabled"])

    def raw_post(self, path, headers, body=b"{}"):
        req = urllib.request.Request(self.base + path, method="POST", data=body, headers=headers)
        return urllib.request.urlopen(req)

    def test_cross_site_post_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.raw_post("/api/games/244210", {"Content-Type": "application/json",
                                                "Origin": "https://evil.example"}, b'{"enabled": true}')
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.get("/api/games")["games"][0]["status"], "foreign")

    def test_non_json_post_is_refused(self):
        # a "simple" cross-site form/text POST carries no preflight; it must not be accepted
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.raw_post("/api/games/244210", {"Content-Type": "text/plain"}, b'{"enabled": true}')
        self.assertEqual(cm.exception.code, 415)

    def test_same_origin_post_is_accepted(self):
        self.raw_post("/api/games/244210", {"Content-Type": "application/json", "Origin": self.base},
                      b'{"enabled": true}')
        self.assertEqual(self.get("/api/games")["games"][0]["status"], "on")

    def test_dns_rebinding_host_is_refused(self):
        port = self.ui.server.server_address[1]
        for method in ("GET", "POST"):
            req = urllib.request.Request(self.base + "/api/games", method=method,
                                         data=b"{}" if method == "POST" else None,
                                         headers={"Host": f"evil.example:{port}", "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 403, method)

    def test_parallel_installs_into_one_prefix_stay_consistent(self):
        import threading
        game = games_service.find("244210")
        reg = os.path.join(game.prefix, "user.reg")
        barrier = threading.Barrier(6)
        errors = []

        def flip(on):
            barrier.wait()
            try:
                games_service.set_enabled(self.cfg_path, game, on)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        real = games_service.bridge_install._set_section

        def slow(*a):
            import time
            time.sleep(0.02)                         # widen the read-modify-write window
            return real(*a)

        threads = [threading.Thread(target=flip, args=(i % 3 != 0,)) for i in range(6)]
        with mock.patch.object(games_service.bridge_install, "_set_section", side_effect=slow):
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        with open(reg) as f:
            text = f.read()
        self.assertEqual(text.count("[Software\\\\NaturalPoint\\\\NATURALPOINT\\\\NPClient Location]"), 1)
        games_service.set_enabled(self.cfg_path, game, True)
        games_service.set_enabled(self.cfg_path, game, False)
        with open(reg) as f:
            self.assertIn("opentrack-launcher/install/modules/", f.read())   # original tracker restored
        self.assertEqual([e for e in errors if not isinstance(e, games_service.bridge_install.BridgeError)], [])

    def test_filesystem_error_is_reported_not_dropped(self):
        with mock.patch.object(games_service.bridge_install, "install",
                               side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post("/api/games/244210", {"enabled": True})
        self.assertEqual(cm.exception.code, 500)
        body = json.load(cm.exception)
        self.assertEqual(body["code"], "io_error")
        self.assertIn("Permission denied", body["error"])

    def test_disable_cleans_up_even_if_status_is_foreign(self):
        self.post("/api/games/244210", {"enabled": True})
        game = games_service.find("244210")
        with mock.patch.object(games_service.bridge_install, "status", return_value="foreign"):
            self.post("/api/games/244210", {"enabled": False})
        self.assertFalse(games_service.bridge_install.has_our_files(game))

    def test_list_scans_running_games_once(self):
        calls = []
        with mock.patch.object(games_service.bridge_install, "running_appids",
                               side_effect=lambda *a, **k: calls.append(1) or set()):
            self.run_patch.stop()
            try:
                self.get("/api/games")
            finally:
                self.run_patch.start()
        self.assertEqual(len(calls), 1)

    def test_unknown_game_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/games/999", {"enabled": True})
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

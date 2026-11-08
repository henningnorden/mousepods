# SPDX-License-Identifier: Apache-2.0
"""Mouse control starts paused, the pause is remembered, the wizard is not only about games,
and a game that still has our bridge from another install shows as switched on.

Run:  python -m unittest tests.test_mouse_off_by_default -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import airpods_mouse_controller as m  # noqa: E402
import bridge_install  # noqa: E402
import games_service  # noqa: E402
from test_bridge_install import DLLS, OPENTRACK_REG  # noqa: E402
from test_engine import FakePointer  # noqa: E402
from test_steam_games import make_steam  # noqa: E402


def controller(cfg_path=None, **over):
    cfg = m.load_config(cfg_path or "/nonexistent.json")
    cfg["notifications"] = False
    cfg.update(over)
    ptr = FakePointer()
    ctl = m.Controller(cfg, ptr, cfg_path=cfg_path)
    ctl.engine.set_profile({"yaw_sign": 1, "lat": [0, 1, 0], "pitch_sign": 1, "roll_sign": 1})
    return ctl, ptr


def feed(ctl, secs=0.5, step=0.01):
    t = 0.0
    for _ in range(int(secs / step)):
        t += step
        ctl.on_rates(50, 30, 0, step, t)
        ctl._last_render = t - step
        ctl.last_sample = t
        ctl.render(t)


class MousePausedByDefaultTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.d, "config.json")
        with open(self.cfg_path, "w") as f:
            f.write("{}")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_fresh_install_does_not_move_the_cursor(self):
        self.assertTrue(m.DEFAULTS["mouse_paused"])
        ctl, ptr = controller(self.cfg_path)
        self.assertTrue(ctl.paused)
        feed(ctl)
        self.assertEqual(ptr.rel, [0.0, 0.0])

    def test_resume_is_remembered(self):
        ctl, _ = controller(self.cfg_path)
        ctl.set_paused(False)
        with open(self.cfg_path) as f:
            self.assertFalse(json.load(f)["mouse_paused"])
        again, ptr = controller(self.cfg_path)
        self.assertFalse(again.paused)
        feed(again)
        self.assertNotEqual(ptr.rel, [0.0, 0.0])

    def test_pause_is_remembered(self):
        with open(self.cfg_path, "w") as f:
            json.dump({"mouse_paused": False}, f)
        ctl, _ = controller(self.cfg_path)
        ctl.set_paused(True)
        self.assertTrue(controller(self.cfg_path)[0].paused)

    def test_recenter_while_paused_leaves_the_cursor_alone(self):
        ctl, ptr = controller(self.cfg_path)
        ctl.recenter(quiet=True)
        self.assertIsNone(ptr.abs)


class WizardTextTests(unittest.TestCase):
    def test_wizard_is_not_only_about_games(self):
        for lang in ("en", "de", "fr"):
            with open(os.path.join(ROOT, "ui", "i18n", f"{lang}.json"), encoding="utf-8") as f:
                s = json.load(f)
            for k in ("welcome.intro", "welcome.done"):
                self.assertNotRegex(s[k].lower(), r"game|spiel|jeu", (lang, k))

    def test_wizard_ends_on_overview(self):
        with open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8") as f:
            h = f.read()
        self.assertRegex(h, r'\$\("#wz-done"\)\.onclick=[\s\S]{0,200}go\("overview"\)')


class GamePreviewTests(unittest.TestCase):
    def test_live_values_only_inside_the_profile(self):
        with open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8") as f:
            h = f.read()
        row = h[h.index("function gameCard"):h.index("<details>", h.index("function gameCard"))]
        self.assertNotIn("data-prev", row)


class LeftoverBridgeTests(unittest.TestCase):
    """Bridge installed by another MousePods (e.g. a distrobox) but this config knows nothing."""

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
        self.env = mock.patch.dict(os.environ, {"MOUSEPODS_STEAM_ROOT": self.steam, "MOUSEPODS_DLL_DIR": self.dlls,
                                                "MOUSEPODS_SHM_DIR": self.home})
        self.env.start()
        bridge_install._LAST_SEEN.clear()
        game = games_service.find("244210")
        games_service.set_enabled(self.cfg_path, game, True)      # the other install
        with open(self.cfg_path, "w") as f:
            f.write("{}")                                          # this install: fresh config

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home)

    def test_shown_as_enabled_and_saved(self):
        d = games_service.list_payload(self.cfg_path)
        ac = next(g for g in d["games"] if g["appid"] == "244210")
        self.assertEqual((ac["status"], ac["enabled"]), ("on", True))
        with open(self.cfg_path) as f:
            self.assertTrue(json.load(f)["games"]["244210"]["enabled"])

    def test_switching_off_still_restores(self):
        games_service.list_payload(self.cfg_path)
        games_service.set_enabled(self.cfg_path, games_service.find("244210"), False)
        self.assertEqual(bridge_install.status(games_service.find("244210")), "foreign")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
"""Installing / removing the head-tracking DLLs in a Proton prefix.

Run:  python -m unittest tests.test_bridge_install -v
"""
import dataclasses
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import bridge_install as bi  # noqa: E402
import steam_games as sg  # noqa: E402
from test_steam_games import make_steam  # noqa: E402

OPENTRACK_REG = ('WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1\n\n#arch=win64\n\n'
                 '[Software\\\\NaturalPoint\\\\NATURALPOINT\\\\NPClient Location] 1700000000\n'
                 '#time=1da\n"Path"="Z:/home/x/.local/share/opentrack-launcher/install/modules/"\n\n'
                 '[Software\\\\Wine] 1700000000\n"Version"="win10"\n')
DLLS = ("NPClient.dll", "NPClient64.dll", "freetrackclient.dll", "freetrackclient64.dll")
NOT_RUNNING = staticmethod(lambda appid: False)


class BridgeInstallTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        make_steam(self.home)
        self.games = {g.appid: g for g in sg.list_games(sg.find_steam_roots(self.home)[0])}
        self.ac = self.games["244210"]
        self.dlls = os.path.join(self.home, "dlls")
        os.makedirs(self.dlls)
        for n in DLLS:
            with open(os.path.join(self.dlls, n), "wb") as f:
                f.write(b"MZ" + n.encode())
        self.reg = os.path.join(self.ac.prefix, "user.reg")
        with open(self.reg, "w") as f:
            f.write(OPENTRACK_REG)

    def tearDown(self):
        shutil.rmtree(self.home)

    def read_reg(self):
        with open(self.reg) as f:
            return f.read()

    def install(self, game=None, running=False):
        bi.install(game or self.ac, self.dlls, "/dev/shm/mousepods-pose-244210", lambda appid: running)

    def test_install_writes_keys_and_dlls(self):
        self.install()
        reg = self.read_reg()
        self.assertIn('"Path"="C:\\\\mousepods\\\\"', reg)
        self.assertIn("[Software\\\\Freetrack\\\\FreeTrackClient]", reg)
        self.assertIn('"PoseFile"="Z:\\\\dev\\\\shm\\\\mousepods-pose-244210"', reg)
        for n in DLLS:
            self.assertTrue(os.path.isfile(os.path.join(self.ac.prefix, "drive_c", "mousepods", n)))
        self.assertEqual(bi.status(self.ac), "on")

    def test_install_requires_prefix(self):
        with self.assertRaises(bi.BridgeError) as cm:
            self.install(self.games["227300"])
        self.assertEqual(cm.exception.code, "no_prefix")

    def test_install_refused_while_running(self):
        with self.assertRaises(bi.BridgeError) as cm:
            self.install(running=True)
        self.assertEqual(cm.exception.code, "running")
        self.assertEqual(self.read_reg(), OPENTRACK_REG)

    def test_missing_dll_refused(self):
        os.remove(os.path.join(self.dlls, "NPClient.dll"))
        with self.assertRaises(bi.BridgeError) as cm:
            self.install()
        self.assertEqual(cm.exception.code, "missing_dll")

    def test_uninstall_restores_previous_keys(self):
        self.install()
        self.install()   # second install must not overwrite the backup
        bi.uninstall(self.ac, lambda appid: False)
        reg = self.read_reg()
        self.assertIn('"Path"="Z:/home/x/.local/share/opentrack-launcher/install/modules/"', reg)
        self.assertNotIn("FreeTrackClient", reg)
        self.assertNotIn("MousePods", reg)
        self.assertIn('"Version"="win10"', reg)
        self.assertFalse(os.path.exists(os.path.join(self.ac.prefix, "drive_c", "mousepods")))
        self.assertEqual(bi.status(self.ac), "foreign")

    def test_uninstall_without_previous_removes_keys(self):
        with open(self.reg, "w") as f:
            f.write("WINE REGISTRY Version 2\n\n[Software\\\\Wine] 1700000000\n\"Version\"=\"win10\"\n")
        self.install()
        bi.uninstall(self.ac, lambda appid: False)
        self.assertNotIn("NPClient", self.read_reg())
        self.assertEqual(bi.status(self.ac), "off")

    def test_uninstall_refused_while_running(self):
        self.install()
        with self.assertRaises(bi.BridgeError) as cm:
            bi.uninstall(self.ac, lambda appid: True)
        self.assertEqual(cm.exception.code, "running")

    def test_status_foreign_off_on(self):
        self.assertEqual(bi.status(self.ac), "foreign")
        self.install()
        self.assertEqual(bi.status(self.ac), "on")
        self.assertEqual(bi.status(self.games["227300"]), "off")

    def test_foreign_tracker_is_named(self):
        self.assertEqual(bi.foreign_tracker(self.ac), "opentrack")
        with open(self.reg, "w") as f:
            f.write(OPENTRACK_REG.replace("Z:/home/x/.local/share/opentrack-launcher/install/modules/",
                    "Z:\\\\home\\\\x\\\\.local\\\\share\\\\Beam Eye Tracker\\\\interop\\\\"))
        self.assertEqual(bi.foreign_tracker(self.ac), "Beam Eye Tracker")
        self.install()
        self.assertIsNone(bi.foreign_tracker(self.ac))

    def test_flatpak_steam_refused(self):
        with self.assertRaises(bi.BridgeError) as cm:
            self.install(dataclasses.replace(self.ac, flatpak=True))
        self.assertEqual(cm.exception.code, "flatpak_steam")

    def test_process_running_from_proc(self):
        proc = os.path.join(self.home, "proc")
        for pid, env, cmd in (("100", b"HOME=/x\0SteamAppId=244210\0", b"acs.exe\0"),
                              ("200", b"HOME=/x\0", b"reaper\0SteamLaunch\0AppId=227300\0")):
            os.makedirs(os.path.join(proc, pid))
            with open(os.path.join(proc, pid, "environ"), "wb") as f:
                f.write(env)
            with open(os.path.join(proc, pid, "cmdline"), "wb") as f:
                f.write(cmd)
        self.assertTrue(bi.process_running("244210", proc))
        self.assertTrue(bi.process_running("227300", proc))
        self.assertFalse(bi.process_running("2442", proc))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
"""Flatpak packaging: manifest permissions, desktop entry, writable data dir, hidden features,
running-game detection from inside the sandbox.

Run:  python -m unittest tests.test_packaging -v
"""
import configparser
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bridge_install  # noqa: E402
import paths  # noqa: E402

PKG = os.path.join(ROOT, "packaging", "flatpak")
APP_ID = "io.github.henningnorden.MousePods"


class ManifestTests(unittest.TestCase):
    def manifest(self):
        with open(os.path.join(PKG, f"{APP_ID}.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_manifest_permissions(self):
        m = self.manifest()
        self.assertEqual(m["app-id"], APP_ID)
        self.assertEqual(m["runtime"], "org.gnome.Platform")
        self.assertEqual(m["command"], "mousepods-app")
        need = {"--share=network", "--socket=wayland", "--socket=fallback-x11", "--device=all", "--device=shm",
                "--allow=bluetooth", "--system-talk-name=org.bluez", "--talk-name=org.kde.StatusNotifierWatcher",
                "--filesystem=~/.local/share/Steam", "--filesystem=~/.steam:ro"}
        self.assertTrue(need <= set(m["finish-args"]), sorted(need - set(m["finish-args"])))
        self.assertNotIn("--talk-name=org.freedesktop.Flatpak", m["finish-args"])   # no sandbox escape

    def test_desktop_file_valid_keys(self):
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        cp.read(os.path.join(PKG, f"{APP_ID}.desktop"), encoding="utf-8")
        e = cp["Desktop Entry"]
        self.assertEqual((e["Type"], e["Exec"], e["Icon"]), ("Application", "mousepods-app", APP_ID))
        self.assertIn("Name[de]", e)

    def test_metainfo_and_icon_exist(self):
        for name in (f"{APP_ID}.metainfo.xml", f"{APP_ID}.svg", "mousepods-app.sh"):
            self.assertTrue(os.path.isfile(os.path.join(PKG, name)), name)


class DataDirTests(unittest.TestCase):
    def test_default_is_program_dir(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MOUSEPODS_DATA_DIR", None)
            self.assertEqual(paths.data_dir(), ROOT)

    def test_env_overrides_and_creates(self):
        d = tempfile.mkdtemp()
        try:
            target = os.path.join(d, "data")
            with mock.patch.dict(os.environ, {"MOUSEPODS_DATA_DIR": target}):
                self.assertEqual(paths.captures_dir(private=True), os.path.join(target, "captures", "private"))
                self.assertTrue(os.path.isdir(os.path.join(target, "captures", "private")))
        finally:
            shutil.rmtree(d)

    def test_flatpak_flag(self):
        with mock.patch.dict(os.environ, {"FLATPAK_ID": APP_ID}):
            self.assertTrue(paths.in_flatpak())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLATPAK_ID", None)
            self.assertFalse(paths.in_flatpak())


class RunningFromSandboxTests(unittest.TestCase):
    def test_steam_registry_running_app(self):
        home = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(home, ".steam"))
            with open(os.path.join(home, ".steam", "registry.vdf"), "w") as f:
                f.write('"Registry"\n{\n\t"HKCU"\n\t{\n\t\t"Software"\n\t\t{\n\t\t\t"Valve"\n\t\t\t{\n'
                        '\t\t\t\t"Steam"\n\t\t\t\t{\n\t\t\t\t\t"RunningAppID"\t\t"244210"\n\t\t\t\t}\n'
                        '\t\t\t}\n\t\t}\n\t}\n}\n')
            empty_proc = os.path.join(home, "proc")
            os.makedirs(empty_proc)
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertTrue(bridge_install.process_running("244210", empty_proc))
                self.assertFalse(bridge_install.process_running("227300", empty_proc))
        finally:
            shutil.rmtree(home)

    def test_ui_hides_unsupported_in_flatpak(self):
        with open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8") as f:
            h = f.read()
        self.assertIn("S.flatpak", h)
        self.assertRegex(h, r'S\.flatpak[^\n]*data-p="garmin"|data-p=garmin[^\n]*S\.flatpak|hideInFlatpak')


if __name__ == "__main__":
    unittest.main()

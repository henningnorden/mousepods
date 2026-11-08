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
                "--filesystem=~/.local/share/Steam", "--filesystem=~/.steam:ro",
                "--filesystem=~/.var/app/com.valvesoftware.Steam:ro"}
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



class RepoFilesTests(unittest.TestCase):
    """Own Flatpak repo on GitHub Pages, two channels: stable (release tags) and test (main)."""

    def setUp(self):
        sys.path.insert(0, PKG)
        import make_refs
        self.mr = make_refs
        self.key = b"\x99\x01\x0dpublic key bytes"

    def parse(self, text):
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        cp.read_string(text)
        return cp

    def test_flatpakref_per_channel(self):
        import base64
        for channel in ("stable", "test"):
            r = self.parse(self.mr.flatpakref("https://henningnorden.github.io/mousepods", self.key, channel))["Flatpak Ref"]
            self.assertEqual(r["Name"], APP_ID)
            self.assertEqual(r["Branch"], channel)
            self.assertEqual(r["Url"], "https://henningnorden.github.io/mousepods/repo/")
            self.assertEqual(r["RuntimeRepo"], "https://dl.flathub.org/repo/flathub.flatpakrepo")
            self.assertEqual(r["IsRuntime"], "false")
            self.assertEqual(base64.b64decode(r["GPGKey"]), self.key)
        self.assertEqual(self.mr.REF_FILES, {"stable": "MousePods.flatpakref", "test": "MousePods-test.flatpakref"})

    def test_flatpakrepo(self):
        r = self.parse(self.mr.flatpakrepo("https://henningnorden.github.io/mousepods/", self.key))["Flatpak Repo"]
        self.assertEqual(r["Url"], "https://henningnorden.github.io/mousepods/repo/")
        self.assertIn("GPGKey", r)

    def test_needs_a_key_https_and_a_known_channel(self):
        with self.assertRaises(ValueError):
            self.mr.flatpakref("https://henningnorden.github.io/mousepods", b"", "stable")
        with self.assertRaises(ValueError):
            self.mr.flatpakref("http://henningnorden.github.io/mousepods", self.key, "stable")
        with self.assertRaises(ValueError):
            self.mr.flatpakref("https://henningnorden.github.io/mousepods", self.key, "master")

    def test_channel_for_ref(self):
        self.assertEqual(self.mr.channel_for("refs/tags/v0.1.0"), "stable")
        self.assertEqual(self.mr.channel_for("refs/heads/main"), "test")
        self.assertEqual(self.mr.channel_for("refs/pull/7/merge"), "test")

    def test_manifest_leaves_the_branch_to_the_build(self):
        with open(os.path.join(PKG, f"{APP_ID}.json"), encoding="utf-8") as f:
            self.assertNotIn("branch", json.load(f))      # flatpak-builder --default-branch=<channel>


class PublishWorkflowTests(unittest.TestCase):
    """Signed repo on GitHub Pages: stable from tags, test from main; never from pull requests."""

    def workflow(self):
        with open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
            return f.read()

    def test_script_is_valid_bash_mirrors_and_checks_the_key(self):
        import subprocess
        script = os.path.join(PKG, "publish-repo.sh")
        self.assertTrue(os.access(script, os.X_OK))
        subprocess.run(["bash", "-n", script], check=True)
        with open(script, encoding="utf-8") as f:
            s = f.read()
        self.assertIn("does not match", s)          # secret key must belong to the committed public key
        self.assertIn("pull --mirror", s)           # keep the other channel already on Pages
        self.assertIn("build-commit-from", s)
        self.assertIn("published=false", s)         # not set up yet: skip, do not fail the build

    def test_build_uses_the_channel_as_branch(self):
        w = self.workflow()
        self.assertIn("--default-branch=", w)
        self.assertRegex(w, r"build-bundle repo MousePods\.flatpak io\.github\.henningnorden\.MousePods \"\$CHANNEL\"")

    def test_secret_only_in_the_publish_step_never_for_pull_requests(self):
        w = self.workflow()
        self.assertEqual(w.count("secrets.FLATPAK_GPG_PRIVATE_KEY"), 1)
        i = w.index("secrets.FLATPAK_GPG_PRIVATE_KEY")
        step = w[w.rindex("- name:", 0, i):i]
        self.assertIn("if: github.event_name == 'push'", step)

    def test_pages_job_only_after_a_publish(self):
        w = self.workflow()
        job = w[w.index("\n  pages:"):]
        self.assertIn("needs.flatpak.outputs.published == 'true'", job[:300])
        self.assertIn("pages: write", job)
        self.assertIn("id-token: write", job)
        self.assertIn("concurrency:", job)
        self.assertIn("actions/deploy-pages@", job)
        self.assertIn("actions/upload-pages-artifact@", w)

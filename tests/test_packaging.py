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
APP_ID = "io.github.starman_tech.MousePods"


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
            r = self.parse(self.mr.flatpakref("https://starman-tech.github.io/mousepods", self.key, channel))["Flatpak Ref"]
            self.assertEqual(r["Name"], APP_ID)
            self.assertEqual(r["Branch"], channel)
            self.assertEqual(r["Url"], "https://starman-tech.github.io/mousepods/repo/")
            self.assertEqual(r["RuntimeRepo"], "https://dl.flathub.org/repo/flathub.flatpakrepo")
            self.assertEqual(r["IsRuntime"], "false")
            self.assertEqual(base64.b64decode(r["GPGKey"]), self.key)
        self.assertEqual(self.mr.REF_FILES, {"stable": "MousePods.flatpakref", "test": "MousePods-test.flatpakref"})

    def test_flatpakrepo(self):
        r = self.parse(self.mr.flatpakrepo("https://starman-tech.github.io/mousepods/", self.key))["Flatpak Repo"]
        self.assertEqual(r["Url"], "https://starman-tech.github.io/mousepods/repo/")
        self.assertIn("GPGKey", r)

    def test_needs_a_key_https_and_a_known_channel(self):
        with self.assertRaises(ValueError):
            self.mr.flatpakref("https://starman-tech.github.io/mousepods", b"", "stable")
        with self.assertRaises(ValueError):
            self.mr.flatpakref("http://starman-tech.github.io/mousepods", self.key, "stable")
        with self.assertRaises(ValueError):
            self.mr.flatpakref("https://starman-tech.github.io/mousepods", self.key, "master")

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
        # GitHub Pages of the repo also serve the project website from docs/: it must stay
        self.assertIn('cp -a "$ROOT/docs/." site/', s)
        self.assertIn("site/install.html", s)

    def test_build_uses_the_channel_as_branch(self):
        w = self.workflow()
        self.assertIn("--default-branch=", w)
        self.assertRegex(w, r"build-bundle repo MousePods\.flatpak io\.github\.starman_tech\.MousePods \"\$CHANNEL\"")

    def test_secret_only_in_the_publish_step_never_for_pull_requests(self):
        w = self.workflow()
        self.assertEqual(w.count("secrets.FLATPAK_GPG_PRIVATE_KEY"), 1)
        i = w.index("secrets.FLATPAK_GPG_PRIVATE_KEY")
        step = w[w.rindex("- name:", 0, i):i]
        self.assertIn("if: github.event_name == 'push' || github.event_name == 'workflow_dispatch'", step)
        self.assertNotIn("pull_request", step)      # pull requests never get the secret

    def test_pages_job_only_after_a_publish(self):
        w = self.workflow()
        job = w[w.index("\n  pages:"):]
        self.assertIn("needs.flatpak.outputs.published == 'true'", job[:300])
        self.assertIn("pages: write", job)
        self.assertIn("id-token: write", job)
        self.assertIn("concurrency:", job)
        self.assertIn("actions/deploy-pages@", job)
        self.assertIn("actions/upload-pages-artifact@", w)


class RepoKeyTests(unittest.TestCase):
    def test_committed_key_is_public_only(self):
        path = os.path.join(PKG, "mousepods-repo.asc")
        if not os.path.exists(path):
            self.skipTest("no signing key committed yet: nothing is published until there is one")
        with open(path, encoding="ascii") as f:
            text = f.read()
        self.assertIn("BEGIN PGP PUBLIC KEY BLOCK", text)
        self.assertNotIn("PRIVATE", text)                 # the secret key belongs in the Actions secret only


class ReleaseNotesTests(unittest.TestCase):
    """Release notes live in the metainfo <releases> (Discover shows them); a release tag needs one."""

    META = """<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application"><id>x</id>
  <releases>
    <release version="1.1.0" date="2026-11-20">
      <description>
        <p>Head tracking for Proton games.</p>
        <ul><li>Games page without opentrack</li><li>Calibration with points</li></ul>
      </description>
    </release>
    <release version="1.0.0" date="2026-11-04"/>
  </releases>
</component>"""

    def setUp(self):
        sys.path.insert(0, PKG)
        import release_notes
        self.rn = release_notes
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "m.xml")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(self.META)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_entry_for_tag(self):
        e = self.rn.entry(self.path, "v1.1.0")
        self.assertEqual((e["version"], e["date"]), ("1.1.0", "2026-11-20"))
        self.assertEqual(self.rn.markdown(e), "Head tracking for Proton games.\n\n"
                                               "- Games page without opentrack\n- Calibration with points\n")

    def test_missing_or_empty_entry(self):
        self.assertIsNone(self.rn.entry(self.path, "v2.0.0"))
        self.assertIsNone(self.rn.entry(self.path, "v1.0.0"))          # no description: not enough
        self.assertIn('no <release version="2.0.0">', self.rn.problem(self.path, "v2.0.0"))
        self.assertIsNone(self.rn.problem(self.path, "v1.1.0"))

    def test_prerelease_tags_need_no_entry(self):
        self.assertTrue(self.rn.is_prerelease("v1.1.0-rc1"))
        self.assertFalse(self.rn.is_prerelease("v1.1.0"))
        self.assertIsNone(self.rn.problem(self.path, "v1.1.0-rc1"))

    def test_problem_is_not_printed(self):
        """Only the CLI prints the ::error line; a printed one in the tests job became a real CI annotation."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.rn.problem(self.path, "v2.0.0")
        self.assertEqual(buf.getvalue(), "")

    def test_real_metainfo_has_releases(self):
        self.assertIsNotNone(self.rn.entry(os.path.join(PKG, f"{APP_ID}.metainfo.xml"), "v1.0.0", require_text=False))

    def test_workflow_checks_and_uses_the_notes(self):
        with open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
            w = f.read()
        self.assertIn("release_notes.py check", w)
        self.assertIn("release_notes.py markdown", w)
        self.assertIn("--notes-file", w)


class StoreScreenshotTests(unittest.TestCase):
    """Bazaar / Discover show the metainfo <screenshots>; every one must exist in docs/screenshots."""

    def test_screenshots_point_to_files_on_pages(self):
        import xml.etree.ElementTree as ET
        root = ET.parse(os.path.join(PKG, f"{APP_ID}.metainfo.xml")).getroot()
        shots = root.findall("./screenshots/screenshot")
        self.assertGreaterEqual(len(shots), 3)
        self.assertEqual(sum(1 for s in shots if s.get("type") == "default"), 1)
        for s in shots:
            url = s.find("image").text.strip()
            self.assertTrue(url.startswith("https://"), url)
            self.assertIn("/screenshots/", url)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "screenshots", url.rsplit("/", 1)[1])), url)
            self.assertTrue((s.findtext("caption") or "").strip(), url)

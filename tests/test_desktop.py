# SPDX-License-Identifier: Apache-2.0
"""Desktop shell: tray menu model, autostart, starting the controller (GTK-free parts).

Run:  python -m unittest tests.test_desktop -v
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import i18n  # noqa: E402
from desktop import autostart, controller, menu  # noqa: E402


class MenuTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang("en")

    def test_dbusmenu_layout(self):
        i18n.set_lang("de")
        rev, layout = menu.layout(menu.ITEMS, revision=3)
        self.assertEqual(rev, 3)
        root_id, root_props, children = layout
        self.assertEqual((root_id, root_props), (0, {"children-display": "submenu"}))
        ids = [c[0] for c in children]
        self.assertEqual(ids, [1, 2, 3, 4, 5])
        labels = {c[0]: c[1].get("label") for c in children}
        self.assertEqual(labels[1], i18n.tr("tray.open", "de"))
        self.assertEqual(children[3][1], {"type": "separator"})
        self.assertTrue(all(c[2] == [] for c in children))

    def test_menu_actions(self):
        self.assertEqual(menu.action_for(1), "open")
        self.assertEqual(menu.action_for(2), "recenter")
        self.assertEqual(menu.action_for(3), "toggle")
        self.assertEqual(menu.action_for(5), "quit")
        self.assertIsNone(menu.action_for(4))


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.home, ".config")})
        self.env.start()
        os.environ.pop("FLATPAK_ID", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home)

    def test_autostart_desktop_file_outside_flatpak(self):
        path = os.path.join(self.home, ".config", "autostart", "io.github.henningnorden.MousePods.desktop")
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(True, command=["/opt/mp/mousepods-app"])
        with open(path) as f:
            text = f.read()
        self.assertIn("Exec=/opt/mp/mousepods-app --background", text)
        self.assertIn("Type=Application", text)
        self.assertTrue(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertFalse(os.path.exists(path))

    def test_exec_quotes_paths_with_spaces(self):
        autostart.set_enabled(True, command=["/home/h/My Apps/mousepods-app"])
        path = os.path.join(self.home, ".config", "autostart", "io.github.henningnorden.MousePods.desktop")
        with open(path) as f:
            self.assertIn('Exec="/home/h/My Apps/mousepods-app" --background', f.read())

    def test_wizard_autostart_default_only_in_flatpak(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "ui", "index.html"), encoding="utf-8") as f:
            h = f.read()
        tag = h[h.index('id="wz-autostart-on"') - 40:h.index('id="wz-autostart-on"') + 40]
        self.assertNotIn("checked", tag)                       # not pre-ticked in the markup
        self.assertIn('$("#wz-autostart-on").checked=!!S.flatpak', h)

    def test_flatpak_uses_portal(self):
        calls = []
        with mock.patch.dict(os.environ, {"FLATPAK_ID": "io.github.henningnorden.MousePods"}), \
                mock.patch.object(autostart, "_portal_request", side_effect=lambda on, cmd: calls.append((on, cmd))):
            autostart.set_enabled(True)
        self.assertEqual(calls, [(True, ["mousepods-app", "--background"])])


class AutostartApiTests(unittest.TestCase):
    def test_autostart_endpoint_saves_choice(self):
        import json
        import types
        import urllib.request
        import mousepods_web as web
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "config.json")
        with open(cfg, "w") as f:
            f.write("{}")
        calls = []
        with mock.patch.object(autostart, "set_enabled", side_effect=lambda on, command=None: calls.append(on)):
            ui = web.WebUI(types.SimpleNamespace(cfg={"ui_lang": "en"}), cfg, port=0)
            ui.start()
            base = f"http://127.0.0.1:{ui.server.server_address[1]}"
            req = urllib.request.Request(base + "/api/autostart", method="POST", data=b'{"enabled": true}',
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req).read()
            got = json.load(urllib.request.urlopen(base + "/api/autostart"))
            ui.server.shutdown()
        with open(cfg) as f:
            saved = json.load(f)
        shutil.rmtree(d)
        self.assertEqual(calls, [True])
        self.assertTrue(saved["autostart"])
        self.assertTrue(got["enabled"])


class InstanceTokenTests(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp()
        os.chmod(self.run, 0o700)
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.run})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.run)

    def serve(self):
        import types
        import mousepods_web as web
        ui = web.WebUI(types.SimpleNamespace(cfg={"ui_lang": "en"}), os.path.join(self.run, "c.json"), port=0)
        ui.start()
        return ui, ui.server.server_address[1]

    def test_own_controller_is_recognised(self):
        ui, port = self.serve()
        try:
            self.assertEqual(controller.ping(port), "ours")
            path = controller.token_path()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        finally:
            ui.server.shutdown()

    def test_foreign_server_on_the_port_is_refused(self):
        ui, port = self.serve()
        try:
            with open(controller.token_path(), "w") as f:
                f.write("something-else")            # as if another user's server answered
            self.assertEqual(controller.ping(port), "foreign")
            with self.assertRaises(controller.ForeignServer):
                controller.ensure_running("/tmp/c.json", port, os.devnull)
        finally:
            ui.server.shutdown()

    def test_token_written_without_runtime_dir_or_cache(self):
        import mousepods_web as web
        home = tempfile.mkdtemp()
        try:
            with mock.patch.dict(os.environ, {"HOME": home}):
                os.environ.pop("XDG_RUNTIME_DIR", None)
                token = web.write_instance_token()
                self.assertEqual(controller.read_token(), token)
        finally:
            shutil.rmtree(home)

    def test_stale_temp_file_does_not_block_token(self):
        import mousepods_web as web
        stale = f"{controller.token_path()}.{os.getpid()}.tmp"
        with open(stale, "w") as f:
            f.write("old")
        token = web.write_instance_token()
        self.assertEqual(controller.read_token(), token)
        self.assertFalse(os.path.exists(stale))

    def test_nothing_listening(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(controller.ping(port), "down")


class ErrorPageTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang("en")

    def test_foreign_server_page_is_translated_and_escaped(self):
        from desktop import errors
        i18n.set_lang("de")
        page = errors.error_page("foreign_server", "<script>x</script> port 8737")
        self.assertIn(i18n.tr("app.error.foreign_server.title", "de"), page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertIn('<html lang="de">', page)

    def test_controller_failed_page_names_the_log(self):
        from desktop import errors
        page = errors.error_page("controller_failed", "/home/h/.local/state/mousepods/controller.log")
        self.assertIn("controller.log", page)
        self.assertIn(i18n.tr("app.error.controller_failed.title", "en"), page)


class ControllerStartTests(unittest.TestCase):
    def test_running_controller_is_not_started_again(self):
        spawned = []
        with mock.patch.object(controller, "ping", return_value="ours"), \
                mock.patch.object(controller.subprocess, "Popen", side_effect=lambda *a, **k: spawned.append(a)):
            self.assertIsNone(controller.ensure_running("/tmp/c.json", 8737, "/tmp/log"))
        self.assertEqual(spawned, [])

    def test_starts_controller_when_down(self):
        pings = iter(["down", "down", "ours"])
        with mock.patch.object(controller, "ping", side_effect=lambda port: next(pings)), \
                mock.patch.object(controller.subprocess, "Popen") as popen, \
                mock.patch.object(controller.time, "sleep"):
            proc = controller.ensure_running("/tmp/c.json", 8737, os.devnull)
        self.assertIs(proc, popen.return_value)
        args = popen.call_args[0][0]
        self.assertEqual(args[-4:], ["-c", "/tmp/c.json", "--port", "8737"])


if __name__ == "__main__":
    unittest.main()

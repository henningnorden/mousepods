# SPDX-License-Identifier: Apache-2.0
"""MousePods desktop app: the web UI in its own window, a tray icon, and the controller kept
running in the background (closing the window only hides it).

    mousepods-app               start (or show the running instance)
    mousepods-app --background  start hidden (autostart at login)
"""
import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gio, GLib, Gtk, WebKit  # noqa: E402

import i18n  # noqa: E402
from desktop import controller  # noqa: E402

APP_ID = "io.github.henningnorden.MousePods"
PORT = 8737
log = logging.getLogger("mousepods-app")


def data_dirs():
    """Config and log locations: XDG dirs (inside the Flatpak they map to ~/.var/app/<id>/)."""
    cfg_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    cfg_dir, state_dir = os.path.join(cfg_home, "mousepods"), os.path.join(state_home, "mousepods")
    os.makedirs(cfg_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(cfg_dir, "config.json"), os.path.join(state_dir, "controller.log")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None
        self.proc = None
        self.tray = None
        self.refused = False
        self.error = None                             # (kind, detail) shown instead of the UI
        self.cfg_path, self.log_path = data_dirs()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()                                   # keep running without a window
        i18n.apply_config({"ui_lang": i18n.setting_from_file(self.cfg_path)})
        try:
            self.proc = controller.ensure_running(self.cfg_path, PORT, self.log_path)
        except controller.ForeignServer as e:
            log.error("%s - not opening it in the MousePods window", e)
            self.refused = True
            self.error = ("foreign_server", f"127.0.0.1:{PORT}")
            return
        if controller.ping(PORT) != "ours":
            log.error("controller did not start, see %s", self.log_path)
            self.error = ("controller_failed", self.log_path)
            return
        try:
            from desktop.tray import Tray
            self.tray = Tray(self.on_action, self.cfg_path)
        except Exception as e:  # noqa: BLE001  (no session bus / watcher: run without tray)
            log.warning("tray unavailable: %s", e)

    def do_command_line(self, cmdline):
        if "--background" not in cmdline.get_arguments()[1:]:
            self.show_window()
        return 0

    def do_activate(self):
        self.show_window()

    def show_window(self):
        if self.win is None:
            self.win = Gtk.ApplicationWindow(application=self, title="MousePods")
            self.win.set_default_size(1280, 860)
            view = WebKit.WebView()
            if self.error:
                from desktop.errors import error_page
                view.load_html(error_page(*self.error), None)      # never load a foreign server
            else:
                view.load_uri(f"http://127.0.0.1:{PORT}/")
            self.win.set_child(view)
            self.win.connect("close-request", self._close)
        self.win.present()

    def _close(self, win):
        if self.error:                                # nothing runs in the background: really quit
            self.release()
            self.quit()
            return False
        return self._hide(win)

    def _hide(self, win):
        win.set_visible(False)
        return True                                   # keep the window object; do not quit

    def on_action(self, action):
        if action == "open":
            self.show_window()
        elif action in ("recenter", "toggle"):
            import airpods_mouse_controller as m
            m.send_command(action)
        elif action == "quit":
            import airpods_mouse_controller as m
            m.send_command("quit")
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=3)
                except Exception:  # noqa: BLE001  (still running: stop it)
                    self.proc.terminate()
            self.release()
            self.quit()
        return GLib.SOURCE_REMOVE


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return App().run(argv if argv is not None else sys.argv)

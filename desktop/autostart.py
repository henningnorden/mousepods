# SPDX-License-Identifier: Apache-2.0
"""Start MousePods hidden at login.

Inside the Flatpak: org.freedesktop.portal.Background.RequestBackground (autostart = true/false).
Outside: an XDG autostart .desktop file in ~/.config/autostart.
"""
import logging
import os
import shutil

APP_ID = "io.github.starman_tech.MousePods"
log = logging.getLogger(__name__)


def _desktop_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "autostart", f"{APP_ID}.desktop")


def in_flatpak():
    return bool(os.environ.get("FLATPAK_ID"))


def default_command():
    if in_flatpak():
        return ["mousepods-app"]
    return [shutil.which("mousepods-app") or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "mousepods-app")]


def _portal_request(enabled, commandline):
    """Fire-and-forget Background portal request (the reply comes as a Request signal)."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    options = {"reason": GLib.Variant("s", "Head tracking for games while the window is closed"),
               "autostart": GLib.Variant("b", bool(enabled)),
               "commandline": GLib.Variant("as", commandline),
               "dbus-activatable": GLib.Variant("b", False)}
    bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                  "org.freedesktop.portal.Background", "RequestBackground",
                  GLib.Variant("(sa{sv})", ("", options)), None, Gio.DBusCallFlags.NONE, 5000, None)


def _exec_quote(arg):
    """Quote one argument for a .desktop Exec= line (freedesktop Desktop Entry spec)."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def set_enabled(enabled, command=None):
    cmd = list(command or default_command())
    if in_flatpak():
        _portal_request(enabled, cmd + ["--background"])
        return
    path = _desktop_path()
    if not enabled:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nName=MousePods\n"
                f"Exec={' '.join(_exec_quote(a) for a in cmd)} --background\nIcon={APP_ID}\n"
                "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n")


def is_enabled():
    """Outside Flatpak: the .desktop file exists. Inside, the portal cannot be queried; the
    caller keeps the last choice in the MousePods config."""
    return os.path.isfile(_desktop_path())

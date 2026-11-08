# SPDX-License-Identifier: Apache-2.0
"""Install / remove the MousePods head-tracking DLLs in a Steam Proton prefix.

The game loads our NPClient / FreeTrackClient DLLs itself (TrackIR / FreeTrack protocols); the
DLLs read the head pose from a Linux file through Wine's Z: drive. Nothing runs inside the game.
The prefix registry (pfx/user.reg, a text file) is edited only while the game is not running,
and every change can be undone: the first install keeps a copy of user.reg and uninstall puts
the touched keys back as they were.
"""
import glob
import os
import re
import shutil
import time

DLLS = ("NPClient.dll", "NPClient64.dll", "freetrackclient.dll", "freetrackclient64.dll")
DIR_NAME = "mousepods"
BACKUP = "user.reg.before-mousepods"
KEY_NPCLIENT = r"Software\\NaturalPoint\\NATURALPOINT\\NPClient Location"
KEY_FREETRACK = r"Software\\Freetrack\\FreeTrackClient"
KEY_MOUSEPODS = r"Software\\MousePods"
OUR_PATH = r"C:\\mousepods\\"           # as written in user.reg (escaped backslashes)


class BridgeError(Exception):
    """code: no_prefix | running | flatpak_steam | missing_dll | no_backup"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _reg_escape(path):
    return path.replace("\\", "\\\\")


def _sections(text):
    marks = list(re.finditer(r"^\[(.+?)\](?: \d+)?\n", text, re.M))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = (m.start(), end)
    return out


def _value(text, section, name):
    span = _sections(text).get(section)
    if not span:
        return None
    m = re.search(r'^"%s"="(.*)"$' % re.escape(name), text[span[0]:span[1]], re.M)
    return m.group(1) if m else None


def _set_section(text, section, lines):
    block = "[%s] %d\n%s\n\n" % (section, int(time.time()), "\n".join(lines))
    span = _sections(text).get(section)
    if span:
        return text[:span[0]] + block + text[span[1]:]
    return text.rstrip("\n") + "\n\n" + block


def _drop_section(text, section):
    span = _sections(text).get(section)
    return text[:span[0]] + text[span[1]:] if span else text


def _paths(game):
    return os.path.join(game.prefix, "user.reg"), os.path.join(game.prefix, "drive_c", DIR_NAME)


def process_running(appid, proc="/proc"):
    """True if any process belongs to this Steam app (SteamAppId / STEAM_COMPAT_APP_ID / AppId=)."""
    needles = (f"SteamAppId={appid}\0".encode(), f"STEAM_COMPAT_APP_ID={appid}\0".encode())
    for d in glob.glob(os.path.join(proc, "[0-9]*")):
        try:
            with open(os.path.join(d, "environ"), "rb") as f:
                env = f.read() + b"\0"
            if any(n in env for n in needles):
                return True
            with open(os.path.join(d, "cmdline"), "rb") as f:
                if f"AppId={appid}".encode() in f.read().split(b"\0"):
                    return True
        except OSError:
            continue
    return False


def _check(game, is_running):
    if game.flatpak:
        raise BridgeError("flatpak_steam")
    if not game.prefix or not os.path.isfile(os.path.join(game.prefix, "user.reg")):
        raise BridgeError("no_prefix")
    if is_running(game.appid):
        raise BridgeError("running")


def install(game, dll_dir, pose_file, is_running=process_running):
    _check(game, is_running)
    missing = [n for n in DLLS if not os.path.isfile(os.path.join(dll_dir, n))]
    if missing:
        raise BridgeError("missing_dll")
    reg, dest = _paths(game)
    os.makedirs(dest, exist_ok=True)
    backup = os.path.join(dest, BACKUP)
    if not os.path.exists(backup):
        shutil.copy2(reg, backup)
    for n in DLLS:
        shutil.copy2(os.path.join(dll_dir, n), os.path.join(dest, n))
    with open(reg, encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    for key in (KEY_NPCLIENT, KEY_FREETRACK):
        text = _set_section(text, key, ['"Path"="%s"' % OUR_PATH])
    win_pose = "Z:" + pose_file.replace("/", "\\")
    text = _set_section(text, KEY_MOUSEPODS, ['"PoseFile"="%s"' % _reg_escape(win_pose)])
    with open(reg, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)


def uninstall(game, is_running=process_running):
    _check(game, is_running)
    reg, dest = _paths(game)
    backup = os.path.join(dest, BACKUP)
    if not os.path.exists(backup):
        raise BridgeError("no_backup")
    with open(backup, encoding="utf-8", errors="surrogateescape") as f:
        before = f.read()
    with open(reg, encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    for key in (KEY_NPCLIENT, KEY_FREETRACK, KEY_MOUSEPODS):
        text = _drop_section(text, key)
        span = _sections(before).get(key)
        if span:
            text = text.rstrip("\n") + "\n\n" + before[span[0]:span[1]]
    with open(reg, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)
    shutil.rmtree(dest)


KNOWN_TRACKERS = (("beam eye tracker", "Beam Eye Tracker"), ("opentrack", "opentrack"),
                  ("lookpilot", "LookPilot"), ("aitrack", "AITrack"), ("smoothtrack", "SmoothTrack"),
                  ("naturalpoint", "TrackIR"), ("trackir", "TrackIR"))


def foreign_tracker(game):
    """Name of the other head tracker registered in this prefix ("Beam Eye Tracker", "opentrack",
    ...; "?" if unknown), or None when none / ours."""
    if status(game) != "foreign":
        return None
    with open(os.path.join(game.prefix, "user.reg"), encoding="utf-8", errors="surrogateescape") as f:
        path = (_value(f.read(), KEY_NPCLIENT, "Path") or "").lower()
    return next((name for needle, name in KNOWN_TRACKERS if needle in path), "?")


def status(game):
    """off (no tracking keys), on (ours), foreign (keys point elsewhere, e.g. opentrack)."""
    if not game.prefix or not os.path.isfile(os.path.join(game.prefix, "user.reg")):
        return "off"
    with open(os.path.join(game.prefix, "user.reg"), encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    path = _value(text, KEY_NPCLIENT, "Path")
    if path is None:
        return "off"
    return "on" if path == OUR_PATH else "foreign"

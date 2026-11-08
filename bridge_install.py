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
        out[m.group(1).lower()] = (m.start(), end)          # Wine key names are case-insensitive
    return out


def _value(text, section, name):
    span = _sections(text).get(section.lower())
    if not span:
        return None
    m = re.search(r'^"%s"="(.*)"$' % re.escape(name), text[span[0]:span[1]], re.M)
    return m.group(1) if m else None


def _set_section(text, section, lines):
    block = "[%s] %d\n%s\n\n" % (section, int(time.time()), "\n".join(lines))
    span = _sections(text).get(section.lower())
    if span:
        return text[:span[0]] + block + text[span[1]:]
    return text.rstrip("\n") + "\n\n" + block


def _drop_section(text, section):
    span = _sections(text).get(section.lower())
    return text[:span[0]] + text[span[1]:] if span else text


def _write_reg(path, text):
    """Replace user.reg atomically: temp file in the same directory, fsync, rename. A full disk or
    a crash leaves the old registry in place instead of a truncated one."""
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".user.reg.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise


def _paths(game):
    return os.path.join(game.prefix, "user.reg"), os.path.join(game.prefix, "drive_c", DIR_NAME)


STEAM_REGISTRIES = ("~/.steam/registry.vdf", "~/.local/share/Steam/registry.vdf")


def steam_running_appid():
    """The game Steam itself reports as running (registry.vdf RunningAppID), or None.
    Works from inside a Flatpak, where other processes are invisible."""
    for rel in STEAM_REGISTRIES:
        path = os.path.join(os.environ.get("HOME", os.path.expanduser("~")), rel[2:])
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                m = re.search(r'"RunningAppID"\s+"(\d+)"', f.read())
        except OSError:
            continue
        if m and m.group(1) != "0":
            return m.group(1)
    return None


GRACE_S = 8.0          # Wine may still flush user.reg a few seconds after the game is gone
_LAST_SEEN = {}        # appid -> monotonic time it was last seen running
_ENV_KEYS = (b"SteamAppId=", b"STEAM_COMPAT_APP_ID=")


def running_appids(proc="/proc", now=None):
    """Steam appids that run now or ran within GRACE_S: Steam's RunningAppID plus one pass over
    /proc (SteamAppId / STEAM_COMPAT_APP_ID in the environment, AppId= on the command line)."""
    import time as _time
    now = _time.monotonic() if now is None else now
    seen = set()
    steam = steam_running_appid()
    if steam:
        seen.add(steam)
    for d in glob.glob(os.path.join(proc, "[0-9]*")):
        try:
            with open(os.path.join(d, "environ"), "rb") as f:
                for item in f.read().split(b"\0"):
                    for key in _ENV_KEYS:
                        if item.startswith(key) and item[len(key):].isdigit() and item[len(key):] != b"0":
                            seen.add(item[len(key):].decode())
            with open(os.path.join(d, "cmdline"), "rb") as f:
                for arg in f.read().split(b"\0"):
                    if arg.startswith(b"AppId=") and arg[6:].isdigit():
                        seen.add(arg[6:].decode())
        except OSError:
            continue
    for appid in seen:
        _LAST_SEEN[appid] = now
    return {a for a, t in _LAST_SEEN.items() if now - t <= GRACE_S}


def process_running(appid, proc="/proc"):
    """True if the game runs (or quit less than GRACE_S ago)."""
    return str(appid) in running_appids(proc)


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
    _write_reg(reg, text)


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
        current = _value(text, key, "Path")
        if key != KEY_MOUSEPODS and current is not None and current != OUR_PATH:
            continue                     # another tracker took this key over after us: leave it
        text = _drop_section(text, key)
        span = _sections(before).get(key.lower())
        if span:
            text = text.rstrip("\n") + "\n\n" + before[span[0]:span[1]]
    _write_reg(reg, text)
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


def has_our_files(game):
    """Our DLL folder (with the registry backup) is in the prefix, whatever the keys say now."""
    return bool(game.prefix) and os.path.isfile(os.path.join(game.prefix, "drive_c", DIR_NAME, BACKUP))


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

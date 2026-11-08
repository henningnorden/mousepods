# SPDX-License-Identifier: Apache-2.0
"""Find Steam installs, installed games, their Proton prefixes and launch options."""
import glob
import os
import re
from dataclasses import dataclass

STEAM_DIRS = (
    (".local/share/Steam", False),
    (".steam/steam", False),
    (".var/app/com.valvesoftware.Steam/.local/share/Steam", True),
)


@dataclass(frozen=True)
class SteamRoot:
    path: str
    flatpak: bool


@dataclass(frozen=True)
class Game:
    appid: str
    name: str
    library: str
    prefix: str | None
    proton: str | None
    launch_options: str
    flatpak: bool


_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text):
    """Minimal Valve KeyValues parser: quoted keys/values, nested braces, // comments."""
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    tokens = []
    for m in _TOKEN.finditer(text):
        if m.group(2):
            tokens.append(m.group(2))
        else:
            tokens.append(("s", re.sub(r'\\(.)', r"\1", m.group(1))))
    pos = 0

    def block():
        nonlocal pos
        out = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return out
            key = tok[1]
            pos += 1
            if pos < len(tokens) and tokens[pos] == "{":
                pos += 1
                out[key] = block()
            elif pos < len(tokens):
                out[key] = tokens[pos][1]
                pos += 1
        return out

    return block()


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _get_ci(d, key):
    """Case-insensitive dict lookup (Steam mixes "apps" and "Apps")."""
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return None


def find_steam_roots(home):
    roots, seen = [], set()
    for rel, flatpak in STEAM_DIRS:
        path = os.path.realpath(os.path.join(home, rel))
        if path in seen or not os.path.isfile(os.path.join(path, "steamapps", "libraryfolders.vdf")):
            continue
        seen.add(path)
        roots.append(SteamRoot(path, flatpak))
    return roots


def _same_dir_key(path):
    """Identity of a directory independent of how it is spelled (/var/home vs /home on Bazzite,
    seen from a distrobox the two are not symlinks of each other)."""
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return os.path.realpath(path)


def libraries(root):
    text = _read(os.path.join(root.path, "steamapps", "libraryfolders.vdf")) or ""
    data = _get_ci(parse_vdf(text), "libraryfolders") or {}
    libs, seen = [root.path], {_same_dir_key(root.path)}
    for entry in data.values():
        if isinstance(entry, dict) and entry.get("path"):
            p = os.path.realpath(entry["path"])
            key = _same_dir_key(p)
            if key not in seen:
                seen.add(key)
                libs.append(p)
    return libs


# Steam tools that come with an app manifest but are not games
TOOL_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "2180100", "2348590", "1887720",
               "2805730", "3658110"}
TOOL_NAME = re.compile(r"^(Proton\b|Steam Linux Runtime\b|Steamworks Common Redistributables$)")


def is_tool(appid, name):
    return appid in TOOL_APPIDS or bool(TOOL_NAME.match(name or ""))


def proton_dir(compat_dir):
    text = _read(os.path.join(compat_dir, "config_info"))
    for line in (text or "").splitlines():
        for marker in ("/files/", "/dist/"):
            if marker in line:
                return line.split(marker)[0]
    return None


def launch_options(root):
    """appid -> LaunchOptions from the most recently written localconfig.vdf."""
    files = glob.glob(os.path.join(root.path, "userdata", "*", "config", "localconfig.vdf"))
    if not files:
        return {}
    data = parse_vdf(_read(max(files, key=os.path.getmtime)) or "")
    node = data
    for key in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps"):
        node = _get_ci(node, key) if isinstance(node, dict) else None
        if node is None:
            return {}
    return {appid: (_get_ci(v, "LaunchOptions") or "") for appid, v in node.items() if isinstance(v, dict)}


def list_games(root):
    opts = launch_options(root)
    games = []
    for lib in libraries(root):
        for manifest in sorted(glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf"))):
            state = _get_ci(parse_vdf(_read(manifest) or ""), "AppState") or {}
            appid = state.get("appid") or os.path.basename(manifest)[12:-4]
            if not appid.isdigit():
                continue                       # Steam appids are numbers; anything else is not ours
            if is_tool(appid, state.get("name", "")):
                continue
            compat = None
            for candidate in libraries(root):
                d = os.path.join(candidate, "steamapps", "compatdata", appid)
                if os.path.isdir(os.path.join(d, "pfx")):
                    compat = d
                    break
            games.append(Game(appid=appid, name=state.get("name", appid), library=lib,
                              prefix=os.path.join(compat, "pfx") if compat else None,
                              proton=proton_dir(compat) if compat else None,
                              launch_options=opts.get(appid, ""), flatpak=root.flatpak))
    return games

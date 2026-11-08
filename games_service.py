# SPDX-License-Identifier: Apache-2.0
"""Glue for the Games page: Steam discovery + prefix installer + saved per-game settings."""
import json
import os

import bridge_install
import game_bridge
import steam_games
from game_profiles import normalize_profile

HERE = os.path.dirname(os.path.abspath(__file__))
DLL_DIRS = (os.path.join(HERE, "native", "out"), "/app/share/mousepods/native")


def is_running(appid):
    return bridge_install.process_running(appid)


def steam_root():
    forced = os.environ.get("MOUSEPODS_STEAM_ROOT")
    if forced:
        return steam_games.SteamRoot(os.path.realpath(forced), False)
    roots = steam_games.find_steam_roots(os.path.expanduser("~"))
    native = [r for r in roots if not r.flatpak]
    return (native or roots or [None])[0]


def dll_dir():
    forced = os.environ.get("MOUSEPODS_DLL_DIR")
    if forced:
        return forced
    for d in DLL_DIRS:
        if os.path.isfile(os.path.join(d, "NPClient64.dll")):
            return d
    return DLL_DIRS[0]


def shm_dir():
    return os.environ.get("MOUSEPODS_SHM_DIR", "/dev/shm")


def saved_games(cfg_path):
    try:
        with open(cfg_path) as f:
            games = json.load(f).get("games")
    except (OSError, ValueError):
        return {}
    return games if isinstance(games, dict) else {}


def _games():
    root = steam_root()
    if root is None:
        return None, []
    # all installed games: one never started with Proton has no prefix yet and shows a hint
    return root, sorted(steam_games.list_games(root), key=lambda g: g.name.lower())


def find(appid):
    _, games = _games()
    return next((g for g in games if g.appid == str(appid)), None)


def list_payload(cfg_path):
    root, games = _games()
    saved = saved_games(cfg_path)
    out = []
    for g in games:
        entry = saved.get(g.appid) or {}
        out.append({"appid": g.appid, "name": g.name, "has_prefix": bool(g.prefix), "proton": g.proton,
                    "flatpak": g.flatpak, "status": bridge_install.status(g),
                    "foreign_tracker": bridge_install.foreign_tracker(g),
                    "enabled": bool(entry.get("enabled")), "running": is_running(g.appid),
                    "launch_option_hint": "opentrack-launcher" in g.launch_options,
                    "profile": normalize_profile(entry.get("profile"))})
    return {"steam_found": root is not None, "steam_flatpak": bool(root and root.flatpak), "games": out}


def set_enabled(cfg_path, game, enabled):
    """Install or remove the bridge in the game's prefix; returns the entry to save.
    Raises bridge_install.BridgeError."""
    entry = dict(saved_games(cfg_path).get(game.appid) or {})
    if enabled:
        pose = game_bridge.pose_path(game.appid, shm_dir())
        bridge_install.install(game, dll_dir(), pose, is_running)
    elif bridge_install.status(game) == "on":
        bridge_install.uninstall(game, is_running)
    entry["enabled"] = bool(enabled)
    entry["profile"] = normalize_profile(entry.get("profile"))
    return entry


def set_profile(cfg_path, game, profile):
    entry = dict(saved_games(cfg_path).get(game.appid) or {})
    entry["enabled"] = bool(entry.get("enabled"))
    entry["profile"] = normalize_profile(profile)
    return entry

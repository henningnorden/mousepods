# SPDX-License-Identifier: Apache-2.0
"""Glue for the Games page: Steam discovery + prefix installer + saved per-game settings."""
import json
import os
import threading

import bridge_install
import game_bridge
import steam_games
from game_profiles import effective_profile, normalize_baseline, normalize_profile

HERE = os.path.dirname(os.path.abspath(__file__))
DLL_DIRS = (os.path.join(HERE, "native", "out"), "/app/share/mousepods/native")


def is_running(appid):
    return bridge_install.process_running(appid)


def steam_roots():
    """Every Steam install, native first (a game in both counts as the native one)."""
    forced = os.environ.get("MOUSEPODS_STEAM_ROOT")
    if forced:
        return [steam_games.SteamRoot(os.path.realpath(forced), False)]
    roots = steam_games.find_steam_roots(os.path.expanduser("~"))
    return sorted(roots, key=lambda r: r.flatpak)


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


def _saved(cfg_path):
    try:
        with open(cfg_path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def saved_games(cfg_path):
    games = _saved(cfg_path).get("games")
    return games if isinstance(games, dict) else {}


def saved_baseline(cfg_path):
    return normalize_baseline(_saved(cfg_path).get("game_baseline"))


def _games():
    """(roots, games) from every Steam install; a game in both counts as the native one.
    All installed games: one never started with Proton has no prefix yet and shows a hint."""
    roots, games, seen = steam_roots(), [], set()
    for root in roots:
        for g in steam_games.list_games(root):
            if g.appid not in seen:
                seen.add(g.appid)
                games.append(g)
    return roots, sorted(games, key=lambda g: g.name.lower())


def find(appid):
    _, games = _games()
    return next((g for g in games if g.appid == str(appid)), None)


def list_payload(cfg_path):
    roots, games = _games()
    saved = saved_games(cfg_path)
    baseline = saved_baseline(cfg_path)
    running = bridge_install.running_appids()        # one /proc pass for the whole list
    out = []
    adopted = {}
    for g in games:
        entry = saved.get(g.appid) or {}
        status = bridge_install.status(g)
        if status == "on" and not entry.get("enabled"):
            # our bridge is in the prefix but this config doesn't know (set up by another
            # MousePods, e.g. a distrobox): show it as on and feed it, so the switch tells the truth
            entry = adopted[g.appid] = {"enabled": True, "flatpak": g.flatpak,
                                        "profile": normalize_profile(entry.get("profile"))}
        out.append({"appid": g.appid, "name": g.name, "has_prefix": bool(g.prefix), "proton": g.proton,
                    "flatpak": g.flatpak, "status": status,
                    "foreign_tracker": bridge_install.foreign_tracker(g),
                    "enabled": bool(entry.get("enabled")), "running": g.appid in running,
                    "launch_option_hint": "opentrack-launcher" in g.launch_options,
                    "profile": effective_profile(normalize_profile(entry.get("profile")), baseline)})
    if adopted:
        from airpods_mouse_controller import update_config_file
        update_config_file(cfg_path, {"games": adopted})
    missing = [p for r in roots for p in steam_games.missing_libraries(r)]
    return {"steam_found": bool(roots), "baseline": baseline, "steam_flatpak": False, "games": out,
            "missing_libraries": list(dict.fromkeys(missing))}


_PREFIX_LOCK = threading.Lock()      # one prefix edit at a time (web requests run in parallel)


def set_enabled(cfg_path, game, enabled):
    """Install or remove the bridge in the game's prefix; returns the entry to save.
    Raises bridge_install.BridgeError."""
    with _PREFIX_LOCK:
        return _set_enabled(cfg_path, game, enabled)


def _set_enabled(cfg_path, game, enabled):
    entry = dict(saved_games(cfg_path).get(game.appid) or {})
    if enabled:
        pose = game_bridge.pose_path(game.appid, shm_dir(), game.flatpak)
        bridge_install.install(game, dll_dir(), pose, is_running)
    elif bridge_install.status(game) == "on" or bridge_install.has_our_files(game):
        bridge_install.uninstall(game, is_running)
    entry["enabled"] = bool(enabled)
    entry["flatpak"] = game.flatpak                 # the bridge writes where that Steam looks
    entry["profile"] = normalize_profile(entry.get("profile"))
    return entry


def set_profile(cfg_path, game, profile):
    entry = dict(saved_games(cfg_path).get(game.appid) or {})
    entry["enabled"] = bool(entry.get("enabled"))
    entry["profile"] = normalize_profile(profile)
    return entry

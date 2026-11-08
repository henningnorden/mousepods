# SPDX-License-Identifier: Apache-2.0
"""Calibration overlay in the desktop app: messages from the page, monitor roles. No GTK here, so it
can be tested anywhere; desktop/app.py does the windows.

The page posts JSON through WebKit (window.webkit.messageHandlers.mousepods):
    {"cmd": "monitors", "saved": [...]}       list the monitors, resolve the saved selection
    {"cmd": "flash", "connector": "DP-1"}     show that monitor's name full screen for a moment
    {"cmd": "overlay", "monitors": [{"connector", "roles"}]}   open the overlay on those monitors
    {"cmd": "overlay_close"}                  close every overlay window
"""
import json
import urllib.parse

ROLES = ("main", "left", "right")
MAX_MONITORS = 8


def _connector(v):
    return v if isinstance(v, str) and 0 < len(v) <= 64 else None


def parse_message(raw):
    """The message as a clean dict, or None for anything else."""
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(msg, dict):
        return None
    cmd = msg.get("cmd")
    if cmd == "monitors":
        saved = msg.get("saved") if isinstance(msg.get("saved"), list) else []
        return {"cmd": "monitors", "saved": [{"connector": m["connector"], "main": bool(m.get("main"))}
                                             for m in saved[:MAX_MONITORS]
                                             if isinstance(m, dict) and _connector(m.get("connector"))]}
    if cmd == "flash":
        c = _connector(msg.get("connector"))
        return {"cmd": "flash", "connector": c} if c else None
    if cmd == "overlay":
        mons = msg.get("monitors")
        if not isinstance(mons, list) or not 0 < len(mons) <= MAX_MONITORS:
            return None
        out = []
        for m in mons:
            if not isinstance(m, dict) or not _connector(m.get("connector")) or not isinstance(m.get("roles"), list):
                return None
            if not m["roles"] or any(r not in ROLES for r in m["roles"]):
                return None
            out.append({"connector": m["connector"], "roles": list(m["roles"])})
        return {"cmd": "overlay", "monitors": out}
    if cmd == "overlay_close":
        return {"cmd": "overlay_close"}
    return None


def roles_for(selection):
    """Ordered selection (left to right, one main) -> connector -> roles. The left point sits on the
    leftmost monitor, the right point on the rightmost, centre/top/bottom on the main one."""
    if not selection:
        return {}
    roles = {m["connector"]: [] for m in selection}
    main = next((m["connector"] for m in selection if m.get("main")), selection[0]["connector"])
    roles[main].append("main")
    roles[selection[0]["connector"]].append("left")
    roles[selection[-1]["connector"]].append("right")
    return roles


def resolve_monitors(saved, present):
    """The saved selection limited to monitors that are still there; without any, the first one."""
    keep = [dict(m) for m in saved if m.get("connector") in present]
    if not keep:
        return [{"connector": present[0], "main": True}] if present else []
    main = next((i for i, m in enumerate(keep) if m.get("main")), 0)
    for i, m in enumerate(keep):
        m["main"] = i == main
    return keep


def overlay_url(port, roles):
    return f"http://127.0.0.1:{port}/calibrate?" + urllib.parse.urlencode({"roles": ",".join(roles)})


def is_own_page(uri, port):
    """A page of our own local server (opened in an app window, not the system browser)."""
    try:
        u = urllib.parse.urlsplit(uri or "")
        return (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost") and u.port == port
                and not u.username and not u.password)
    except ValueError:                    # e.g. "127.0.0.1:8737.evil.com": not a port
        return False


def connector_name(connector, index):
    """GDK may not know a monitor's connector: give it a stable name from its position."""
    return connector if connector else f"monitor-{index + 1}"


def plan_overlays(requested, present):
    """[(connector, roles)] for the monitors still there; if none is left, one overlay full screen
    on the current monitor with every role, so the calibration never runs with nothing on screen."""
    plan = [(m["connector"], m["roles"]) for m in requested if m["connector"] in present]
    return plan or [(None, list(ROLES))]

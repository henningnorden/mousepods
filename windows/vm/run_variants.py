# SPDX-License-Identifier: Apache-2.0
"""One VM test round: the variant matrix, resume after a crash, and the report.

round.ps1 drives this file step by step, so a bluescreen in one variant costs
only that variant:

    python run_variants.py next   --dir D [--marker M]   next step as JSON (records a crash first)
    python run_variants.py step   --dir D --id N --addr A run one step, record it in results.json
    python run_variants.py record --dir D --id N --verdict V --detail T
    python run_variants.py report --dir D [--fatal T]   write report.md, print the outcome line
    python run_variants.py devices | service | tone | selftest

Windows-only calls (ctypes.windll, bthprops) stay inside functions, so the
tests import this module on Linux.
"""
import argparse
import ctypes
import datetime
import json
import math
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, os.path.join(ROOT, "windows", "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import l2cap_win  # noqa: E402
import headtrack_test as ht  # noqa: E402

# GUID the INF lists under BTHENUM (route b); enabled on the AirPods with
# BluetoothSetServiceState so BTHENUM creates a device for it.
SERVICE_GUID = "6d0e3c52-4f1b-4c9a-9a51-2f0b8e7d1a02"

VARIANT_SECONDS = 15
CONNECT_TIMEOUT_S = 8
DRIVER_WAIT_S = 45          # first load on a route installs the driver from the store
DISCONNECT_WINDOW_S = 20
DISCONNECT_GRACE_S = 15     # supervision timeout after the lid closes

ROUTE_IDS = {"root": "a", "bthenum": "b"}


def _matrix():
    steps = []
    for route in ("root", "bthenum"):           # grouped: one route switch per round
        for mtu in (2582, 672):
            for security in ("encrypt", "none"):
                for audio in ("idle", "playing"):
                    steps.append({"kind": "variant", "route": route, "mtu": mtu,
                                  "security": security, "audio": audio,
                                  "seconds": VARIANT_SECONDS,
                                  "name": "%s-%d-%s-%s" % (route, mtu, security, audio)})
    for name in ("psm_refused", "second_handle", "orphan_handle", "disconnect"):
        steps.append({"kind": "check", "name": name})
    for i, s in enumerate(steps, 1):
        s["id"] = i
    return steps


VARIANTS = _matrix()
N_VARIANTS = sum(1 for v in VARIANTS if v["kind"] == "variant")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- state (results.json) ------------------------------------------------------

def _state_path(d):
    return os.path.join(d, "results.json")


def load_state(d):
    try:
        with open(_state_path(d), encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    state.setdefault("meta", {})
    state.setdefault("results", [])
    return state


def save_state(d, state):
    os.makedirs(d, exist_ok=True)
    tmp = _state_path(d) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())    # a bluescreen right after must not lose the last result
    os.replace(tmp, _state_path(d))


def _remember(state, addrs):
    """Full addresses go to meta.known_addrs (local; the pushed copy is masked)."""
    meta = state.setdefault("meta", {})
    known = set(meta.get("known_addrs") or [])
    known.update(a.upper() for a in addrs if a and addr_forms(a))
    meta["known_addrs"] = sorted(known)


def _put(state, rec):
    state["results"] = [r for r in state["results"] if r["id"] != rec["id"]] + [rec]
    state["results"].sort(key=lambda r: r["id"])


def _record(step, verdict, detail="", **kw):
    rec = {k: step[k] for k in ("id", "name", "kind", "route", "mtu", "security", "audio") if k in step}
    rec.update(verdict=verdict, detail=detail, route_loaded=None, result={}, finished=_now())
    rec.update(kw)
    return rec


def _step(step_id):
    for s in VARIANTS:
        if s["id"] == step_id:
            return dict(s)
    raise KeyError(step_id)


MAX_CRASHES_IN_A_ROW = 3          # across routes: the driver takes Windows down everywhere
MAX_CHANNEL_CRASHES_PER_ROUTE = 2  # once the route is up: the rest of that route is skipped


def _skip_route(state, route, done, why):
    for s in VARIANTS:
        if s["kind"] == "variant" and s["route"] == route and s["id"] not in done:
            _put(state, _record(s, "skipped", why))
            done.add(s["id"])


def _route_broken(r):
    """A result that says this route takes Windows down (or hangs) as soon as it is set up."""
    crash = r.get("crash") or {}
    if crash.get("at_boot"):
        return True
    return r.get("phase") == "route" and r.get("verdict") in ("bugcheck", "interrupted", "hung")


def _channel_crash(r):
    """A bluescreen in a variant after its route was set up (the channel phase)."""
    return (r.get("kind") == "variant" and r.get("verdict") == "bugcheck" and r.get("route")
            and r.get("phase") != "route" and not (r.get("crash") or {}).get("at_boot"))


def channel_crashes(state, route):
    return sum(1 for r in state["results"] if _channel_crash(r) and r.get("route") == route)


def crashes_in_a_row(state):
    """Bluescreens in a row (skipped steps do not break the row). A row made only
    of one route's variants does not count: that route is skipped after
    MAX_CHANNEL_CRASHES_PER_ROUTE instead, and the other route still runs."""
    row = []
    for r in sorted(state["results"], key=lambda r: r["id"]):
        if r["verdict"] == "skipped":
            continue
        row = row + [r] if r["verdict"] == "bugcheck" else []
    if row and all(r.get("kind") == "variant" for r in row) and len({r.get("route") for r in row}) == 1:
        return 0
    return len(row)


def next_variants(state, marker):
    """Steps still to run. A marker (current.json) for a step that has no
    result means Windows went down during it: record it as a bugcheck (or
    "interrupted" when round.ps1 found no crash record). A crash while the
    route was being set up, or at boot (the driver crashed as soon as Windows
    loaded it), skips that route's other variants, which would only crash the
    same way. MAX_CHANNEL_CRASHES_PER_ROUTE crashes once a route is up skip the
    rest of that route, so the other route still runs. After MAX_CRASHES_IN_A_ROW
    bluescreens in a row that span routes (or checks) the round stops."""
    done = {r["id"] for r in state["results"]}
    if marker and marker.get("id") in done:
        marker = None
    if marker:
        crash = marker.get("crash") or {}
        step = _step(marker["id"])
        if marker.get("route"):
            step["route"] = marker["route"]
        kind = crash.get("kind", "bugcheck")
        detail = "Windows went down during this step"
        if crash.get("dump"):
            detail += "; minidump %s, see crash.txt" % crash["dump"]
        if crash.get("at_boot"):
            detail += "; it crashed again while Windows started (Safe Mode was needed)"
        if marker.get("phase"):
            detail += "; phase: %s" % marker["phase"]
        rec = _record(step, kind, detail, started=marker.get("started"), crash=crash,
                      phase=marker.get("phase"))
        _put(state, rec)
        done.add(step["id"])
        if _route_broken(rec) and step.get("route"):
            where = "at boot" if crash.get("at_boot") else "while the route was set up"
            _skip_route(state, step["route"], done, "skipped: route %s took Windows down %s in step %d"
                        % (step["route"], where, step["id"]))
    for route in ("root", "bthenum"):
        n = channel_crashes(state, route)
        if n >= MAX_CHANNEL_CRASHES_PER_ROUTE:
            _skip_route(state, route, done, "skipped: route %s took Windows down in %d variants once "
                        "the route was up" % (route, n))
    n = crashes_in_a_row(state)
    if n >= MAX_CRASHES_IN_A_ROW:
        state.setdefault("meta", {})["stopped"] = ("Windows crashed (bluescreen) in %d steps in a row; the round stopped "
                                    "there to spare more bluescreens" % n)
        return []
    return [dict(s) for s in VARIANTS if s["id"] not in done]


def broken_routes(state):
    return {r.get("route") for r in state["results"] if r.get("route") and _route_broken(r)}


def check_config(state):
    """Route, MTU and security for the checks: the first variant that got
    motion; else the first whose route loaded; else the first route that did
    not take Windows down; None when no route is usable."""
    broken = broken_routes(state)
    variants = [r for r in state["results"] if r.get("kind") == "variant" and r.get("route") not in broken]
    for want in (lambda r: r["verdict"] == "ok",
                 lambda r: r["verdict"] not in ("route_not_loaded", "bugcheck", "skipped", "hung",
                                                "interrupted", "error")):
        for r in variants:
            if want(r):
                return {"route": r["route"], "mtu": r["mtu"], "security": r["security"]}
    for route in ("root", "bthenum"):
        if route not in broken:
            return {"route": route, "mtu": 2582, "security": "encrypt"}
    return None


def plan_next(state, marker):
    remaining = next_variants(state, marker)
    while remaining and remaining[0]["kind"] == "check":
        cfg = check_config(state)
        if cfg is not None:
            break
        _put(state, _record(remaining.pop(0), "skipped", "skipped: both routes took Windows down"))
    if not remaining:
        out = {"done": True}
        if state.get("meta", {}).get("stopped"):
            out["stopped"] = state["meta"]["stopped"]
        return out
    step = remaining[0]
    if step["kind"] == "check":
        step.update(cfg)
        step["audio"] = "idle"
    return step


def record_hung_route(state, step_id, route, detail):
    """The route set-up (devnode, service) did not finish: the step is hung and
    the route's other variants are skipped."""
    step = _step(step_id)
    step["route"] = route
    rec = _record(step, "hung", detail, phase="route")
    _put(state, rec)
    done = {r["id"] for r in state["results"]}
    _skip_route(state, route, done, "skipped: setting up route %s hung in step %d" % (route, step_id))


# --- masking: the fork and vm-results are public ---------------------------------

_MAC = re.compile(r"(?<![0-9A-Fa-f])((?:[0-9A-Fa-f]{2}[:-]){4})([0-9A-Fa-f]{2})([:-])([0-9A-Fa-f]{2})(?![0-9A-Fa-f])")
_HEX12 = re.compile(r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}([0-9A-Fa-f]{4})(?![0-9A-Fa-f])")
_USERS_PATH = re.compile(r"(?i)(\\Users\\|/Users/)[^\\/\s\"']+")


def mask_addr(addr):
    """'AA:BB:CC:DD:3F:A1' -> 'XX:XX:XX:XX:3F:A1'."""
    if not addr:
        return addr
    parts = addr.replace("-", ":").split(":")
    if len(parts) != 6:
        return addr
    return "XX:XX:XX:XX:%s:%s" % (parts[4].upper(), parts[5].upper())


def addr_forms(addr):
    """Every way a full address shows up: colon, dash, bare 12 hex (any case)."""
    bare = re.sub(r"[^0-9A-Fa-f]", "", addr or "")
    if len(bare) != 12:
        return []
    pairs = [bare[i:i + 2] for i in range(0, 12, 2)]
    return [":".join(pairs), "-".join(pairs), bare]


def _mask_known(text, addrs):
    for addr in addrs:
        forms = addr_forms(addr)
        if not forms:
            continue
        tail = forms[2][8:].upper()
        for form, masked in zip(forms, ("XX:XX:XX:XX:%s:%s" % (tail[:2], tail[2:]),
                                         "XX-XX-XX-XX-%s-%s" % (tail[:2], tail[2:]),
                                         "XXXXXXXX" + tail)):
            text = re.sub(re.escape(form), masked, text, flags=re.IGNORECASE)
    return text


def mask_text(text, hide=(), addrs=()):
    """Every Bluetooth address (AA:BB:.., AA-BB-.., the driver's 12-hex-digit form,
    and the known addresses in `addrs` in any form, e.g. inside Windows device IDs)
    keeps only its last two bytes. Words in `hide` (user and machine name, 4+
    characters) become <hidden> as whole words, and so do user folders in paths."""
    text = _mask_known(text, addrs)
    text = _MAC.sub(lambda m: "XX:XX:XX:XX:%s:%s" % (m.group(2).upper(), m.group(4).upper()), text)
    text = _HEX12.sub(lambda m: "XXXXXXXX" + m.group(1).upper(), text)
    text = _USERS_PATH.sub(lambda m: m.group(1) + "<hidden>", text)
    for word in hide:
        if word and len(word) >= 4:
            text = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(word), "<hidden>", text,
                          flags=re.IGNORECASE)
    return text


class MaskError(Exception):
    pass


def known_addrs(d, extra=()):
    """The full addresses this round saw: given ones plus results.json meta.known_addrs."""
    out = [a for a in extra if a]
    out += load_state(d)["meta"].get("known_addrs") or []
    return sorted({a.upper().replace("-", ":") for a in out if addr_forms(a)})


def mask_dir(d, hide=(), addrs=()):
    """Mask every file under a round folder (the copy that gets pushed), then
    check that no known full address is left in any form; MaskError if one is."""
    addrs = list(addrs)
    for root, _, files in os.walk(d):
        for name in sorted(files):
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                raw = f.read()
            # bytes in, bytes out: line endings (CRLF on Windows), a BOM and bytes
            # that are not UTF-8 stay exactly as they were, apart from what is masked
            text = raw.decode("utf-8", "surrogateescape")
            masked = mask_text(text, hide, addrs)
            if masked != text:
                with open(path, "wb") as f:
                    f.write(masked.encode("utf-8", "surrogateescape"))
    left = []
    for root, _, files in os.walk(d):
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                low = f.read().lower()
            for addr in addrs:
                for form in addr_forms(addr):
                    if form.lower().encode() in low:
                        left.append("%s (%s)" % (os.path.relpath(path, d), mask_addr(addr)))
    if left:
        raise MaskError("a full Bluetooth address is still in: %s" % ", ".join(sorted(set(left))))


def _facts_list(facts):
    """[name, value] pairs or {"k","v"} objects; Windows PowerShell 5.1 may wrap a
    list as {"value": [...], "Count": n}."""
    if isinstance(facts, dict) and "value" in facts:
        facts = facts["value"]
    if isinstance(facts, dict):
        return list(facts.items())
    out = []
    for f in facts or []:
        if isinstance(f, dict) and "value" in f and "Count" in f:
            f = f["value"]
        if isinstance(f, dict):
            out.append((f.get("k", f.get("name")), f.get("v", f.get("value"))))
        elif isinstance(f, (list, tuple)) and len(f) == 2:
            out.append((f[0], f[1]))
    return out


def write_setup(path, facts, devices=(), addrs=()):
    """setup.txt: facts as [name, value] pairs (or {"k","v"} objects); addresses masked."""
    if isinstance(devices, dict) and "value" in devices:
        devices = devices["value"]
    devices = [d for d in (devices or []) if isinstance(d, dict)]
    lines = ["MousePods VM round setup, %s" % _now(), ""]
    lines += ["%s: %s" % (k, v) for k, v in _facts_list(facts)]
    if devices:
        lines += ["", "Paired Bluetooth devices:"]
        lines += ["  %s  %s  connected: %s" % (d.get("name"), d.get("address"), d.get("connected"))
                  for d in devices]
    addrs = list(addrs) + [d.get("address") for d in devices if d.get("address")]
    with open(path, "w", encoding="utf-8") as f:
        f.write(mask_text("\n".join(lines) + "\n", addrs=addrs))


# --- the report ---------------------------------------------------------------

LEGEND = [
    ("ok", "the channel opened and motion data arrived"),
    ("windows_refused", "Windows' Bluetooth stack refused (BRB status, stages 1, 2, 4, 5)"),
    ("airpods_refused", "the AirPods refused the L2CAP connection (stage 3, connect result)"),
    ("no_answer", "the channel opened, but the AirPods sent nothing within 3 s"),
    ("no_motion", "the channel opened and the AirPods answered, but sent no motion data"),
    ("link_lost", "the channel opened, then the link failed before motion arrived"),
    ("refused_by_policy", "the driver refused before sending anything"),
    ("route_not_loaded", "the requested load route did not load; the channel was not tried"),
    ("bugcheck", "Windows crashed (bluescreen) during this step"),
    ("interrupted", "Windows restarted during this step without a crash record"),
    ("hung", "the step did not finish in time and was stopped"),
    ("error", "the step failed in the test program itself (a bug in run_variants.py)"),
    ("skipped", "not run, see the reason"),
]

_FURTHEST = [("no_answer", "no answer"), ("airpods_refused", "AirPods refused"),
             ("windows_refused", "Windows refused"), ("refused_by_policy", "refused by the driver")]


def _hex32(v):
    return "0x%08X" % ((v or 0) & 0xFFFFFFFF)


def explain(rec):
    v = rec.get("verdict")
    r = rec.get("result") or {}
    detail = rec.get("detail") or ""
    if v == "ok" and rec.get("kind") == "variant":
        return "motion: %s blocks, %s/s, yaw span %s deg, first reply %s ms" % (
            r.get("blocks"), r.get("rate_hz"), r.get("yaw_span_deg"), r.get("first_reply_ms"))
    if v == "windows_refused":
        stage = r.get("stage")
        return "Windows refused at stage %s (%s): NTSTATUS %s, Bluetooth status 0x%X" % (
            stage, l2cap_win.STAGE_NAMES.get(stage, "?"), _hex32(r.get("ntstatus")),
            (r.get("bt_status") or 0) & 0xFF)
    if v == "airpods_refused":
        bt = r.get("bt_status") or 0
        return "AirPods refused: L2CAP connect result 0x%04X, NTSTATUS %s" % (
            (bt >> 16) & 0xFFFF, _hex32(r.get("ntstatus")))
    if v == "no_answer":
        return "channel open, the AirPods sent nothing within %d s" % ht.NO_ANSWER_S
    if v == "no_motion":
        return "channel open, first reply after %s ms, no motion data" % r.get("first_reply_ms")
    if v == "link_lost":
        return "channel open, then: %s" % (r.get("error") or detail)
    if v == "refused_by_policy":
        return "the driver refused before sending (stage 0)"
    if v == "route_not_loaded":
        return detail or "asked for route %s, loaded: %s" % (rec.get("route"), rec.get("route_loaded"))
    return detail or v


def _row(rec):
    if rec.get("kind") == "variant":
        what = "route %s (%s), MTU %s, %s, audio %s" % (
            ROUTE_IDS.get(rec.get("route"), "?"), rec.get("route"), rec.get("mtu"),
            rec.get("security"), rec.get("audio"))
    else:
        what = "%s (route %s, MTU %s, %s)" % (rec.get("name"), rec.get("route"), rec.get("mtu"),
                                             rec.get("security"))
    text = explain(rec).replace("|", "/").replace("\n", " ")
    return "| %d | %s | %s | %s |" % (rec["id"], what, rec.get("verdict"), text)


def outcome_line(results, meta=None):
    meta = meta or {}
    variants = [r for r in results if r.get("kind") == "variant"]
    n_ok = sum(1 for r in variants if r["verdict"] == "ok")
    counts = {}
    for r in variants:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    if meta.get("fatal") and not variants:
        return "Outcome: the round stopped before the variants: %s" % meta["fatal"]
    if n_ok:
        return "Outcome: channel opens and motion arrives in %d/%d variants." % (n_ok, N_VARIANTS)
    if counts.get("no_motion"):
        return ("Outcome: no variant got motion: the channel opened and the AirPods answered, "
                "but sent no motion data (%d variants)." % counts["no_motion"])
    if counts.get("link_lost"):
        return ("Outcome: no variant got motion: the channel opened, then the link failed "
                "(%d variants)." % counts["link_lost"])
    parts = ["%s in %d" % (label, counts[v]) for v, label in _FURTHEST if counts.get(v)]
    if parts:
        return "Outcome: no variant opened the channel (%s)." % ", ".join(parts)
    rest = ", ".join("%s in %d" % (k, n) for k, n in sorted(counts.items())) or "nothing ran"
    return "Outcome: no variant reached the channel (%s)." % rest


def render_report(results, meta=None):
    meta = meta or {}
    results = sorted(results, key=lambda r: r["id"])
    variants = [r for r in results if r.get("kind") == "variant"]
    checks = [r for r in results if r.get("kind") == "check"]
    n_ok = sum(1 for r in variants if r["verdict"] == "ok")
    n_pass = sum(1 for r in checks if r["verdict"] == "pass")
    lines = ["# VM round %s" % meta.get("stamp", ""), "", outcome_line(results, meta), "",
             "ok %d / %d variants (%d ran); checks: pass %d / 4" % (n_ok, N_VARIANTS, len(variants), n_pass),
             ""]
    for why in (meta.get("fatal"), meta.get("stopped")):
        if why:
            lines += ["**The round stopped early:** %s" % why, ""]
    facts = [("Commit", meta.get("commit")), ("Driver package", meta.get("driver")),
             ("AirPods", "%s (%s)" % (meta.get("name"), mask_addr(meta.get("addr"))) if meta.get("addr") else None),
             ("Started", meta.get("started")), ("Finished", _now())]
    lines += ["- %s: %s" % (k, v) for k, v in facts if v]
    routes = sorted({str(r.get("route_loaded")) for r in variants if r.get("route_loaded")})
    if routes:
        lines.append("- Routes the driver reported: %s" % ", ".join(routes))
    lines += ["", "## Variants (failures first)", "",
              "| # | variant | verdict | what happened |", "| --- | --- | --- | --- |"]
    lines += [_row(r) for r in sorted(variants, key=lambda r: (r["verdict"] == "ok", r["id"]))]
    missing = [s for s in VARIANTS if s["kind"] == "variant" and s["id"] not in {r["id"] for r in variants}]
    if missing:
        lines += ["", "Not run: %s" % ", ".join(str(s["id"]) for s in missing)]
    lines += ["", "## Checks", "", "| # | check | verdict | what happened |", "| --- | --- | --- | --- |"]
    lines += [_row(r) for r in sorted(checks, key=lambda r: (r["verdict"] == "pass", r["id"]))]
    lines += ["", "## Verdicts", ""]
    lines += ["- `%s`: %s" % (k, v) for k, v in LEGEND]
    lines += ["", "Files: `results.json` (every field), `driver.log` (driver log after each step), "
              "`setup.txt` (VM setup), `install.txt`, `round.log`, `steps.log` (what each step printed), `crash.txt` (only after a bluescreen).", ""]
    return mask_text("\n".join(lines))


# --- helpers ------------------------------------------------------------------

def write_tone(path, hz=440, seconds=2, rate=44100, level=0.08):
    """A quiet sine of a whole number of periods, so PlayLooping has no click."""
    n = rate * seconds
    assert (n * hz) % rate == 0
    frames = bytearray()
    for i in range(n):
        frames += struct.pack("<h", int(level * 32767 * math.sin(2 * math.pi * hz * i / rate)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def raw_connect(addr, psm, mtu, security, timeout_s=CONNECT_TIMEOUT_S, api=None):
    """IOCTL_CONNECT with any PSM, past open_seqpacket's own PSM check, so the
    driver's rule is what answers. Returns {"winerror": n} when DeviceIoControl
    failed, else the MPL2_CONNECT_OUT fields (a channel that opened is closed)."""
    api = api or l2cap_win.default_api()
    handle = l2cap_win._open_device(api)
    try:
        req = l2cap_win.ConnectIn(l2cap_win.addr_to_u64(addr), psm, mtu,
                                  l2cap_win.SECURITY[security], int(timeout_s * 1000))
        out = api.DeviceIoControl(handle, l2cap_win.IOCTL_CONNECT, bytes(req),
                                  ctypes.sizeof(l2cap_win.ConnectOut))
        if out is None:
            return {"winerror": api.GetLastError()}
        co = l2cap_win.ConnectOut.from_buffer_copy(out)
        fields = {name: getattr(co, name) for name, _ in l2cap_win.ConnectOut._fields_}
        if co.ntstatus == 0:
            api.DeviceIoControl(handle, l2cap_win.IOCTL_DISCONNECT, None, 0)
        return fields
    finally:
        api.CloseHandle(handle)


def pick_airpods(devices):
    pods = [d for d in devices if "airpods" in (d.get("name") or "").lower()]
    for d in pods:
        if d.get("connected"):
            return d
    return pods[0] if pods else None


def wait_for_driver(route, api=None, wait_s=None, sleep=time.sleep, clock=time.monotonic):
    """Wait until the driver's device is there and reports the wanted route.
    Returns (info or None, paths, last error text)."""
    wait_s = DRIVER_WAIT_S if wait_s is None else wait_s
    t0 = clock()
    info, paths, err = None, [], "the driver's device did not appear"
    while True:
        try:
            paths = (api or l2cap_win.default_api()).list_device_paths(
                l2cap_win.GUID_DEVINTERFACE_MOUSEPODS_L2CAP)
            if paths:
                info = l2cap_win.driver_info(api)
                if info["route"] == route:
                    return info, paths, None
                err = "asked for route %s, the driver reports route %s" % (route, info["route"])
        except OSError as e:
            err = "driver info failed: %s" % e
        if clock() - t0 >= wait_s:
            return info, paths, err
        sleep(1)


def _safe_log(api=None):
    try:
        return l2cap_win.driver_log(api)
    except OSError as e:
        return "(driver log not available: %s)\n" % e


def _beep():
    try:
        import winsound
        winsound.Beep(880, 300)
        winsound.Beep(660, 300)
    except (ImportError, RuntimeError):
        pass


def _banner(out, *lines):
    out("")
    out("#" * 72)
    for ln in lines:
        out("#  " + ln)
    out("#" * 72)
    out("")


def _open(step, addr, api=None):
    return l2cap_win.open_seqpacket(addr, l2cap_win.PSM_AAP, step["mtu"], CONNECT_TIMEOUT_S,
                                    security=step["security"], api=api)


def _open_failure(e):
    res = ht._open_failure(e)
    return "%s: %s" % (ht.classify(res), e), res


# --- variants and checks ------------------------------------------------------

def run_variant(step, addr, api=None, out=print):
    info, paths, err = wait_for_driver(step["route"], api)
    loaded = info["route"] if info else "none"
    if err:
        return _record(step, "route_not_loaded", err, route_loaded=loaded, devices=len(paths))
    out("variant %d: route %s, MTU %d, security %s, audio %s, %d s - turn your head slowly"
        % (step["id"], step["route"], step["mtu"], step["security"], step["audio"], step["seconds"]))
    res = ht.run(lambda a: _open(step, a, api), addr, None, step["seconds"], out=out)
    return _record(step, ht.classify(res), route_loaded=loaded, result=res, radio=mask_addr(info.get("radio")),
                   radio_full=info.get("radio"),
                   devices=len(paths))


def check_psm_refused(step, addr, api=None, out=print):
    r = raw_connect(addr, 0x0001, step["mtu"], step["security"], api=api)
    if r.get("winerror") == l2cap_win.ERROR_ACCESS_DENIED:
        return "pass", "PSM 0x0001: DeviceIoControl failed with ERROR_ACCESS_DENIED", r
    if "winerror" in r:
        return "fail", "PSM 0x0001: DeviceIoControl failed with Win32 error %d, expected 5" % r["winerror"], r
    return "fail", "PSM 0x0001 was not refused: the driver tried (NTSTATUS %s, stage %s)" % (
        _hex32(r.get("ntstatus")), r.get("stage")), r


def check_second_handle(step, addr, api=None, out=print):
    try:
        first = _open(step, addr, api)
    except OSError as e:
        why, res = _open_failure(e)
        return "skipped", "the first channel did not open (%s)" % why, res
    notes = []
    verdict = "pass"
    try:
        try:
            second = _open(step, addr, api)
        except l2cap_win.DriverError as e:
            if e.winerror == l2cap_win.ERROR_BUSY:
                notes.append("second handle refused with ERROR_BUSY")
            else:
                verdict = "fail"
                notes.append("second handle failed with %s, expected ERROR_BUSY" % e)
        except OSError as e:
            verdict = "fail"
            notes.append("second handle failed with %s, expected ERROR_BUSY" % e)
        else:
            second.close()
            verdict = "fail"
            notes.append("a second channel opened while the first was open")
    finally:
        first.close()
    try:
        again = _open(step, addr, api)
        again.close()
        notes.append("after closing the first, a new channel opened")
    except OSError as e:
        verdict = "fail"
        notes.append("after closing the first, a new channel failed: %s" % e)
    return verdict, "; ".join(notes), {}


def child_open(addr, mtu, security):
    """Run in a child process by check_orphan_handle: open, say so, wait to be killed."""
    try:
        chan = _open({"mtu": mtu, "security": security}, addr)
    except OSError as e:
        print("FAILED %s" % e, flush=True)
        return 1
    print("OPEN", flush=True)
    time.sleep(120)
    chan.close()
    return 0


def check_orphan_handle(step, addr, api=None, out=print):
    cmd = [sys.executable, os.path.abspath(__file__), "child-open", "--addr", addr,
           "--mtu", str(step["mtu"]), "--security", step["security"]]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(ln.strip()) for ln in p.stdout], daemon=True).start()
    seen = []
    deadline = time.monotonic() + CONNECT_TIMEOUT_S + 15
    opened = False
    while time.monotonic() < deadline and not opened:
        try:
            ln = lines.get(timeout=0.5)
        except queue.Empty:
            if p.poll() is not None and lines.empty():
                break
            continue
        seen.append(ln)
        opened = ln == "OPEN"
    p.kill()
    p.wait(15)
    if not opened:
        return "skipped", "the child process did not open a channel: %s" % " / ".join(seen[-3:]), {}
    out("child process with an open channel killed; opening again in 3 s")
    time.sleep(3)
    try:
        chan = _open(step, addr, api)
    except l2cap_win.DriverError as e:
        if e.winerror == l2cap_win.ERROR_BUSY:
            return "fail", "the driver still holds the killed process's channel (ERROR_BUSY)", {}
        return "fail", "opening after the kill failed: %s" % e, {}
    except OSError as e:
        return "fail", "opening after the kill failed: %s" % e, {}
    chan.close()
    return "pass", "the killed process's channel was cleaned up; a new channel opened", {}


def check_disconnect(step, addr, api=None, out=print, clock=time.monotonic):
    try:
        chan = _open(step, addr, api)
    except OSError as e:
        why, res = _open_failure(e)
        return "skipped", "the channel did not open (%s)" % why, res
    import airpods_mouse_controller as amc
    client = amc.AAPClient(addr)
    packets = 0
    raised = None
    try:
        try:
            client.attach(chan, addr, None)
            client.start_tracking()
        except OSError as e:
            out("start failed (%s); waiting for the disconnect anyway" % e)
        chan.settimeout(0.5)
        _beep()
        _banner(out, "ACTION NEEDED NOW:",
                "Put BOTH AirPods into the charging case and CLOSE THE LID.",
                "You have %d seconds. This checks what happens when the link drops." % DISCONNECT_WINDOW_S)
        t0 = clock()
        next_say = 0
        while clock() - t0 < DISCONNECT_WINDOW_S + DISCONNECT_GRACE_S:
            elapsed = clock() - t0
            if elapsed >= next_say and elapsed < DISCONNECT_WINDOW_S:
                out("  %d s left - AirPods into the case, lid closed" % round(DISCONNECT_WINDOW_S - next_say))
                next_say += 5
            try:
                pkt = chan.recv(4096)
            except (socket.timeout, TimeoutError, BlockingIOError):
                continue
            except OSError as e:
                raised = (round(clock() - t0, 1), "%s: %s" % (type(e).__name__, e))
                break
            packets += 1
            try:
                client.process(pkt)
            except Exception:       # decoding is not what this check is about
                pass
    finally:
        try:
            client.close()
        except Exception:
            pass
        chan.close()
    _banner(out, "Thanks. You can take the AirPods out of the case again.")
    res = {"packets_before": packets, "raised_after_s": raised[0] if raised else None,
           "error": raised[1] if raised else None}
    if raised:
        return "pass", "recv raised %s after %.1f s (%d packets before)" % (raised[1], raised[0], packets), res
    return "fail", "recv raised no OSError within %d s after the prompt (%d packets)" % (
        DISCONNECT_WINDOW_S + DISCONNECT_GRACE_S, packets), res


CHECKS = {"psm_refused": check_psm_refused, "second_handle": check_second_handle,
          "orphan_handle": check_orphan_handle, "disconnect": check_disconnect}


def run_check(step, addr, api=None, out=print):
    info, paths, err = wait_for_driver(step["route"], api)
    loaded = info["route"] if info else "none"
    if err:
        return _record(step, "skipped", "route not loaded: %s" % err, route_loaded=loaded)
    out("check %d: %s (route %s, MTU %d, %s)" % (step["id"], step["name"], step["route"],
                                                step["mtu"], step["security"]))
    verdict, detail, res = CHECKS[step["name"]](step, addr, api, out)
    return _record(step, verdict, detail, route_loaded=loaded, result=res)


# --- Windows Bluetooth API (bthprops) -----------------------------------------

def _bt_types():
    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint16) for n in
                    ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMs")]

    class DEVICE_INFO(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("Address", ctypes.c_uint64),
                    ("ulClassofDevice", ctypes.c_uint32), ("fConnected", ctypes.c_int32),
                    ("fRemembered", ctypes.c_int32), ("fAuthenticated", ctypes.c_int32),
                    ("stLastSeen", SYSTEMTIME), ("stLastUsed", SYSTEMTIME),
                    ("szName", ctypes.c_wchar * 248)]

    class SEARCH_PARAMS(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("fReturnAuthenticated", ctypes.c_int32),
                    ("fReturnRemembered", ctypes.c_int32), ("fReturnUnknown", ctypes.c_int32),
                    ("fReturnConnected", ctypes.c_int32), ("fIssueInquiry", ctypes.c_int32),
                    ("cTimeoutMultiplier", ctypes.c_ubyte), ("hRadio", ctypes.c_void_p)]

    class RADIO_PARAMS(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32)]

    class GUID(ctypes.Structure):
        _fields_ = [("d", ctypes.c_ubyte * 16)]

    return SYSTEMTIME, DEVICE_INFO, SEARCH_PARAMS, RADIO_PARAMS, GUID


def _bthprops():
    bt = ctypes.WinDLL("bthprops.cpl", use_last_error=True)
    P = ctypes.c_void_p
    bt.BluetoothFindFirstDevice.argtypes = [P, P]
    bt.BluetoothFindFirstDevice.restype = P
    bt.BluetoothFindNextDevice.argtypes = [P, P]
    bt.BluetoothFindNextDevice.restype = ctypes.c_int32
    bt.BluetoothFindDeviceClose.argtypes = [P]
    bt.BluetoothFindFirstRadio.argtypes = [P, ctypes.POINTER(P)]
    bt.BluetoothFindFirstRadio.restype = P
    bt.BluetoothFindRadioClose.argtypes = [P]
    bt.BluetoothGetDeviceInfo.argtypes = [P, P]
    bt.BluetoothGetDeviceInfo.restype = ctypes.c_uint32
    bt.BluetoothSetServiceState.argtypes = [P, P, P, ctypes.c_uint32]
    bt.BluetoothSetServiceState.restype = ctypes.c_uint32
    return bt


def _fmt_addr(raw):
    return ":".join("%02X" % ((raw >> s) & 0xFF) for s in range(40, -8, -8))


def list_paired():
    """Paired and connected Bluetooth devices: name, address, connected, paired."""
    _, DEVICE_INFO, SEARCH_PARAMS, _, _ = _bt_types()
    bt = _bthprops()
    sp = SEARCH_PARAMS(ctypes.sizeof(SEARCH_PARAMS), 1, 1, 0, 1, 0, 0, None)
    info = DEVICE_INFO()
    info.dwSize = ctypes.sizeof(DEVICE_INFO)
    h = bt.BluetoothFindFirstDevice(ctypes.byref(sp), ctypes.byref(info))
    if not h:
        err = ctypes.get_last_error()
        if err == 259:          # ERROR_NO_MORE_ITEMS
            return []
        raise OSError(err, "BluetoothFindFirstDevice failed (Win32 error %d)" % err)
    out = []
    try:
        while True:
            out.append({"name": info.szName, "address": _fmt_addr(info.Address),
                        "connected": bool(info.fConnected), "paired": bool(info.fAuthenticated),
                        "remembered": bool(info.fRemembered)})
            info = DEVICE_INFO()
            info.dwSize = ctypes.sizeof(DEVICE_INFO)
            if not bt.BluetoothFindNextDevice(h, ctypes.byref(info)):
                break
    finally:
        bt.BluetoothFindDeviceClose(h)
    return out


def set_service(addr, enable):
    """BluetoothSetServiceState for SERVICE_GUID on the device. Returns the Win32 error (0 = ok)."""
    _, DEVICE_INFO, _, RADIO_PARAMS, GUID = _bt_types()
    bt = _bthprops()
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    rp = RADIO_PARAMS(ctypes.sizeof(RADIO_PARAMS))
    radio = ctypes.c_void_p()
    hf = bt.BluetoothFindFirstRadio(ctypes.byref(rp), ctypes.byref(radio))
    if not hf:
        return ctypes.get_last_error() or 1
    bt.BluetoothFindRadioClose(hf)
    try:
        info = DEVICE_INFO()
        info.dwSize = ctypes.sizeof(DEVICE_INFO)
        info.Address = l2cap_win.addr_to_u64(addr)
        err = bt.BluetoothGetDeviceInfo(radio, ctypes.byref(info))
        if err:
            return err
        g = GUID.from_buffer_copy(uuid.UUID(SERVICE_GUID).bytes_le)
        return bt.BluetoothSetServiceState(radio, ctypes.byref(info), ctypes.byref(g), 1 if enable else 0)
    finally:
        k.CloseHandle(radio)


def selftest(out=print):
    """Run on the CI Windows runner: struct sizes, bthprops signatures, tone, report."""
    import tempfile
    ok = True
    if sys.platform == "win32":
        _, DEVICE_INFO, SEARCH_PARAMS, _, _ = _bt_types()
        for name, have, want in (("BLUETOOTH_DEVICE_INFO", ctypes.sizeof(DEVICE_INFO), 560),
                                 ("BLUETOOTH_DEVICE_SEARCH_PARAMS", ctypes.sizeof(SEARCH_PARAMS), 40)):
            if have != want:
                out("::error title=run_variants selftest::sizeof %s is %d, expected %d" % (name, have, want))
                ok = False
        try:
            out("paired devices: %s" % list_paired())
        except OSError as e:
            out("paired devices: %s (fine on a machine without Bluetooth)" % e)
        out("set_service without a radio: Win32 error %s" % set_service("00:00:00:00:00:01", False))
    with tempfile.TemporaryDirectory() as d:
        write_tone(os.path.join(d, "t.wav"))
    rec = _record(_step(1), "windows_refused", result={"stage": 2, "ntstatus": -1, "bt_status": 1})
    out(render_report([rec]).splitlines()[2])
    return 0 if ok else 1


# --- command line -------------------------------------------------------------

def _read_json(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def main(argv=None, out=None):
    out_stream = out or sys.stdout

    def say(text=""):
        print(text, file=out_stream, flush=True)

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--dir", required=True)
    p.add_argument("--known", nargs="*", default=[], help="full addresses, kept locally for masking")
    p.add_argument("--meta", default=None, help="JSON file with round facts")
    p = sub.add_parser("next")
    p.add_argument("--dir", required=True)
    p.add_argument("--marker", default=None)
    p = sub.add_parser("step")
    p.add_argument("--dir", required=True)
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--addr", required=True)
    p.add_argument("--route", default=None)
    p.add_argument("--mtu", type=int, default=None)
    p.add_argument("--security", default=None)
    p = sub.add_parser("record")
    p.add_argument("--dir", required=True)
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--verdict", required=True)
    p.add_argument("--detail", default="")
    p.add_argument("--route", default=None)
    p.add_argument("--skip-route", action="store_true",
                   help="the route set-up hung: skip the route's other variants")
    p = sub.add_parser("setup")
    p.add_argument("--path", required=True)
    p.add_argument("--facts", required=True, help="JSON: {facts: [[name, value]...], devices: [...]}")
    p = sub.add_parser("mask")
    p.add_argument("--dir", required=True)
    p.add_argument("--state", default=None, help="round folder whose results.json lists the known addresses")
    p.add_argument("--addr", nargs="*", default=[])
    p.add_argument("--hide", nargs="*", default=[])
    p = sub.add_parser("report")
    p.add_argument("--dir", required=True)
    p.add_argument("--fatal", default=None)
    sub.add_parser("devices")
    p = sub.add_parser("service")
    p.add_argument("--addr", required=True)
    p.add_argument("--state", choices=("enable", "disable"), required=True)
    p = sub.add_parser("tone")
    p.add_argument("path")
    p = sub.add_parser("child-open")
    p.add_argument("--addr", required=True)
    p.add_argument("--mtu", type=int, required=True)
    p.add_argument("--security", required=True)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        state = load_state(a.dir)
        state["meta"].update(_read_json(a.meta) or {})
        _remember(state, a.known)
        save_state(a.dir, state)
        return 0
    if a.cmd == "next":
        state = load_state(a.dir)
        step = plan_next(state, _read_json(a.marker))
        save_state(a.dir, state)
        say(json.dumps(step))
        return 0
    if a.cmd == "step":
        log_path = os.path.join(a.dir, "steps.log")

        def say(text=""):     # noqa: F811 - the step's output also goes to steps.log
            print(text, file=out_stream, flush=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(mask_text(text) + "\n")

        state = load_state(a.dir)
        step = _step(a.id)
        if step["kind"] == "check":
            step.update(check_config(state) or {})
            for k in ("route", "mtu", "security"):
                if getattr(a, k) is not None:
                    step[k] = getattr(a, k)
        started = _now()
        t0 = time.monotonic()
        try:
            run = run_variant if step["kind"] == "variant" else run_check
            rec = run(step, a.addr, out=say)
        except Exception as e:      # record it; the round goes on
            rec = _record(step, "error", "the step failed in Python: %s: %s" % (type(e).__name__, e))
        rec.update(started=started, seconds=round(time.monotonic() - t0, 1))
        say("step %d %s: %s - %s" % (rec["id"], rec["name"], rec["verdict"], explain(rec)))
        with open(os.path.join(a.dir, "driver.log"), "a", encoding="utf-8") as f:
            f.write("=== step %d %s, %s, verdict %s\n" % (rec["id"], rec["name"], _now(), rec["verdict"]))
            f.write(mask_text(_safe_log()))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        state = load_state(a.dir)
        _remember(state, [a.addr, rec.pop("radio_full", None)])
        _put(state, rec)
        save_state(a.dir, state)
        return 0
    if a.cmd == "record":
        state = load_state(a.dir)
        if a.skip_route and a.route:
            record_hung_route(state, a.id, a.route, a.detail)
        else:
            step = _step(a.id)
            if step["kind"] == "check":
                step.update(check_config(state) or {})
            if a.route:
                step["route"] = a.route
            _put(state, _record(step, a.verdict, a.detail))
        save_state(a.dir, state)
        return 0
    if a.cmd == "setup":
        data = _read_json(a.facts) or {}
        write_setup(a.path, data.get("facts") or [], data.get("devices") or [])
        return 0
    if a.cmd == "mask":
        try:
            mask_dir(a.dir, a.hide, known_addrs(a.state or a.dir, a.addr))
        except MaskError as e:
            print("not pushing: %s" % e, file=sys.stderr)
            return 3
        return 0
    if a.cmd == "report":
        state = load_state(a.dir)
        meta = dict(state["meta"])
        meta.setdefault("stamp", os.path.basename(os.path.normpath(a.dir)))
        if a.fatal:
            meta["fatal"] = a.fatal
        text = render_report(state["results"], meta)
        with open(os.path.join(a.dir, "report.md"), "w", encoding="utf-8") as f:
            f.write(text)
        say(text.splitlines()[2])
        return 0
    if a.cmd == "devices":
        try:
            say(json.dumps(list_paired()))
        except OSError as e:
            print("Bluetooth device list failed: %s" % e, file=sys.stderr)
            say("[]")
        return 0
    if a.cmd == "service":
        err = set_service(a.addr, a.state == "enable")
        say(json.dumps({"ok": err == 0, "error": err}))
        return 0
    if a.cmd == "tone":
        write_tone(a.path)
        return 0
    if a.cmd == "child-open":
        return child_open(a.addr, a.mtu, a.security)
    if a.cmd == "selftest":
        return selftest(say)
    return 2


if __name__ == "__main__":
    sys.exit(main())

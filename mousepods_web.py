# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Serveur de l'interface AirPods Mouse (http://127.0.0.1:8737).

Tourne dans le processus du contrôleur, dans un thread :
  GET  /                 application (ui/index.html)
  GET  /api/ping
  GET  /api/state        état complet (appareil, batteries, capteurs, réglages, journal…)
  GET  /api/stream       télémétrie temps réel (Server-Sent Events, 30 Hz)
  POST /api/cmd          {"cmd": "recenter" | "toggle" | "mode" | "scroll" | ...}
  POST /api/config       {clé: valeur, ...}   (fusionné dans config.json, appliqué à chaud)
  POST /api/calib        {"action": "start" | "cancel", "source": "auto|head|case|hand"}
  POST /api/sensor       {"dev": 7, "action": "start|stop|get", "interval": 10000, "report": 1}
  POST /api/control      {"ident": 0x17, "values": [0, 0, 0, 0]}     (commande AAP 0x09)
  POST /api/raw          {"hex": "04000400..."}                       (avancé)
Les actions qui touchent au Bluetooth sont mises en file et exécutées par la boucle principale.
"""
import base64
import json
import math
import os
import queue
import re
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import airpods_mouse_controller as m

HERE = os.path.dirname(os.path.abspath(__file__))
UI_FILE = os.path.join(HERE, "ui", "index.html")

MODELS = {"2013": "AirPods (3ᵉ génération)", "2014": "AirPods Pro (2ᵉ génération)",
          "200e": "AirPods Pro", "200f": "AirPods (2ᵉ génération)", "2002": "AirPods (1ʳᵉ génération)",
          "200a": "AirPods Max", "2019": "AirPods 4", "201b": "AirPods 4 (ANC)", "2024": "AirPods Pro 2 (USB-C)",
          "2027": "AirPods Pro 3", "202d": "AirPods Max (USB-C)"}
INFO_LABELS = ["Nom", "Modèle", "Fabricant", "N° de série", "Firmware", "Firmware (2)", "Matériel",
               "Updater", "N° de série gauche", "N° de série droit", "Version marketing"]
CONTROL_NAMES = {0x01: "Mode micro", 0x05: "Mode envoi bouton", 0x0A: "Détection oreille",
                 0x0C: "Mode clic simple", 0x0D: "Contrôle du bruit", 0x0E: "ANC un écouteur",
                 0x14: "Configs du mode d'écoute", 0x16: "Appui prolongé (action)",
                 0x17: "Intervalle double-clic", 0x18: "Intervalle appui prolongé", 0x1A: "Modes d'écoute",
                 0x1B: "ANC un écouteur (mode)", 0x1C: "Volume des sons", 0x1D: "Intervalle glissement volume",
                 0x1F: "Gestion des appels", 0x20: "Glissement volume", 0x23: "Volume adaptatif",
                 0x24: "Mise en sourdine logicielle", 0x25: "Détection de conversation", 0x28: "Aide auditive",
                 0x39: "Gestes bruts (rapport à l'hôte)"}
BATTERY_PART = {0x01: "Simple", 0x02: "Droit", 0x04: "Gauche", 0x08: "Boîtier"}
BATTERY_STATE = {0x00: "inconnu", 0x01: "en charge", 0x02: "sur batterie", 0x04: "déconnecté"}
EAR_STATE = {0: "dans l'oreille", 1: "hors de l'oreille", 2: "dans le boîtier", 3: "absent"}
USAGE_NAMES = {(0x20, 0x30E): "Report Interval", (0x20, 0x31B): "Report Latency",
               (0x20, 0x31A): "Max FIFO Events", (0x20, 0x73): "Accéléromètre 3D", (0x20, 0x7A): "Gyroscope 3D",
               (0x20, 0x453): "Accél. X", (0x20, 0x454): "Accél. Y", (0x20, 0x455): "Accél. Z",
               (0x20, 0x457): "Vitesse ang. X", (0x20, 0x458): "Vitesse ang. Y", (0x20, 0x459): "Vitesse ang. Z",
               (0x20, 0x434): "Horodatage", (0x20, 0x316): "Reporting State", (0x20, 0x319): "Power State"}
SERVICE_DESC = {"devmotion6": "Mouvement fusionné (attitude, gyro, gravité, accélération)",
                "accel": "Accéléromètre (capteur HID standard)", "gyro": "Gyroscope (capteur HID standard)",
                "cma": "CoreMotion Activity", "kadabra": "Capteur « Kadabra » (inconnu)",
                "iedrelay": "Relais IED (inconnu)", "neorelay": "Relais NEO (inconnu)",
                "mando": "Mando (inconnu)"}


def hid_reports(desc):
    """Décode un descripteur HID -> liste de rapports {kind, id, bits, usages}."""
    out = []
    i, page, rid, size, count, usages = 0, 0, 0, 0, 1, []
    while i < len(desc):
        b = desc[i]
        sz = [0, 1, 2, 4][b & 3]
        tag = b & 0xFC
        v = int.from_bytes(desc[i + 1:i + 1 + sz], "little")
        i += 1 + sz
        if tag == 0x04:
            page = v
        elif tag == 0x84:
            rid = v
        elif tag == 0x74:
            size = v
        elif tag == 0x94:
            count = v
        elif tag == 0x08:
            usages.append((v >> 16, v & 0xFFFF) if sz == 4 else (page, v))
        elif tag in (0x80, 0x90, 0xB0):
            kind = {0x80: "entrée", 0x90: "sortie", 0xB0: "feature"}[tag]
            out.append({"kind": kind, "id": rid, "bits": size * count,
                        "usages": [USAGE_NAMES.get(u, f"0x{u[0]:04x}:0x{u[1]:04x}") for u in usages]})
            usages = []
        elif tag == 0xC0:
            usages = []
    return out


def plist_info(xml):
    x = xml.decode(errors="replace")
    d = {k: v for k, v in re.findall(r"<key>(\w+)</key>\s*<(?:integer|string)>([^<]*)<", x)}
    rd = re.search(r"ReportDescriptor</key>\s*<data>([^<]*)<", x)
    desc = base64.b64decode(rd.group(1)) if rd else b""
    return d, desc


class DeviceState:
    """Tout ce qu'on apprend des paquets AAP (hors mouvement)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.info = []
        self.battery = []
        self.ear = None
        self.controls = {}
        self.log = []
        self.n_motion = 0
        self.counts = {}

    def feed(self, pkt, direction="rx"):
        with self.lock:
            op = pkt[4] if len(pkt) > 5 and pkt[:4] == m.HDR else (-1 if pkt[:2] != b"\x00\x00" else -2)
            self.counts[op] = self.counts.get(op, 0) + 1
            motion = pkt[:10] in (m.WX_PREFIX,) or (pkt[:10] == m.RHID_PREFIX and len(pkt) in range(70, 90))
            if motion:
                self.n_motion += 1
                if self.n_motion % 100 != 1:      # n'enregistrer qu'1 paquet de mouvement sur 100
                    return
            self.log.append({"t": time.time(), "dir": direction, "op": op, "len": len(pkt),
                             "hex": pkt[:96].hex(" ") + (" …" if len(pkt) > 96 else "")})
            del self.log[:-400]
            if direction != "rx" or pkt[:4] != m.HDR or len(pkt) < 7:
                return
            if op == 0x04 and len(pkt) >= 7:
                n = pkt[6]
                bat = []
                for k in range(n):
                    e = pkt[7 + 5 * k:12 + 5 * k]
                    if len(e) == 5:
                        bat.append({"part": BATTERY_PART.get(e[0], hex(e[0])), "level": e[2],
                                    "state": BATTERY_STATE.get(e[3], hex(e[3]))})
                self.battery = bat
            elif op == 0x06 and len(pkt) >= 8:
                self.ear = [EAR_STATE.get(pkt[6], str(pkt[6])), EAR_STATE.get(pkt[7], str(pkt[7]))]
            elif op == 0x1D:
                parts = [p.decode(errors="replace") for p in pkt[11:].split(b"\x00")]
                vals = [p for p in parts if p and all(32 <= ord(c) < 127 or ord(c) > 160 for c in p)]
                self.info = [{"label": INFO_LABELS[i] if i < len(INFO_LABELS) else f"Champ {i + 1}",
                              "value": v} for i, v in enumerate(vals[:14])]
            elif op == 0x09 and len(pkt) >= 11:
                ident = pkt[6]
                self.controls[ident] = {"ident": ident, "name": CONTROL_NAMES.get(ident, "inconnu"),
                                        "values": list(pkt[7:11])}


AIM_TEXT = {"aim_center": "Vise le CENTRE de l'écran et ne bouge plus",
            "aim_tl": "Vise le coin HAUT-GAUCHE de l'écran et ne bouge plus",
            "aim_br": "Vise le coin BAS-DROIT de l'écran et ne bouge plus"}
GLIDE_TEXT = {"flat": "Pose le boîtier À PLAT sur la table, immobile",
              "slide_right": "Fais-le GLISSER vers la DROITE (≈ 10 cm), puis arrête",
              "slide_fwd": "Fais-le GLISSER vers l'AVANT, loin de toi (≈ 10 cm), puis arrête",
              "flat_back": "Immobile"}
FLOWS = {"souris": m.CALIB_STEPS, "pointeur": m.CALIB_STEPS + ["aim_center", "aim_tl", "aim_br"],
         "glisse": ["flat", "slide_right", "flat_back", "slide_fwd", "flat_back"]}


class CalibSession:
    """Calibration guidée pilotée depuis l'interface.

    souris   : axes (immobile, gauche, haut, épaule/rotation droite)
    pointeur : axes + visée du centre et de deux coins -> amplitude exacte de l'écran
    glisse   : boîtier à plat, glisser à droite puis vers l'avant -> axes de la table
    """

    def __init__(self, ctl, cfg_path, source, flow=None):
        self.ctl, self.cfg_path = ctl, cfg_path
        self.flow = flow if flow in FLOWS else ctl.cfg["mode"]
        self.source = source if source != "auto" else ctl.source
        self.steps = FLOWS[self.flow]
        self.i = 0
        self.recent = []
        self.still_since = None
        self.bias = self.up = None
        self.acc = None
        self.res = {}
        self.progress = 0.0
        self.done = False
        self.message = None
        self.engine = None
        self.ang = [0.0, 0.0]
        self.aims = {}
        self.slide = None
        self.was_paused = ctl.paused
        ctl.paused = True

    @property
    def step(self):
        return self.steps[self.i] if self.i < len(self.steps) else "done"

    def text(self):
        if self.done:
            return "Calibration réussie"
        st = self.step
        if st in AIM_TEXT:
            return AIM_TEXT[st]
        if st in GLIDE_TEXT:
            return GLIDE_TEXT[st]
        return m.CALIB_TEXT[self.source][st]

    def _still(self, now, need=1.0, with_acc=False):
        ws = [r[1] for r in self.recent]
        if len(ws) < 5:
            return False
        rng = max((max(v[k] for v in ws) - min(v[k] for v in ws)) for k in range(3))
        ok = rng < 6.0
        if with_acc and self.up:
            ok = ok and max(m.vlen(m.reject(r[3], self.up)) for r in self.recent) < 0.35
        if ok:
            self.still_since = self.still_since or now
            self.progress = min((now - self.still_since) / need, 1.0)
            return self.progress >= 1.0
        self.still_since, self.progress = None, 0.0
        return False

    def hook(self, w, g, dt, a=(0.0, 0.0, 0.0)):
        if self.done:
            return
        now = time.monotonic()
        self.recent.append((now, w, g, a))
        self.recent = [r for r in self.recent if r[0] > now - 0.4]
        st = self.step
        if self.engine is not None:                     # visée des coins : angles intégrés
            y, p, _ = self.engine.step(w, g, dt)
            self.ang[0] += m.soft_deadzone(y, 0.6) * dt
            self.ang[1] += m.soft_deadzone(p, 0.6) * dt
        if st in ("still", "back", "flat", "flat_back"):
            if self._still(now, 1.0, with_acc=st.startswith("flat") and self.up is not None):
                if st in ("still", "flat") and self.bias is None:
                    ws = [r[1] for r in self.recent]
                    gs = [r[2] for r in self.recent]
                    self.bias = tuple(sum(v[k] for v in ws) / len(ws) for k in range(3))
                    self.up = m.vnorm(tuple(sum(v[k] for v in gs) / len(gs) for k in range(3)))
                self._next()
        elif st in AIM_TEXT:
            if self._still(now, 1.0):
                if st == "aim_center":
                    self.ang = [0.0, 0.0]
                else:
                    self.aims[st] = tuple(self.ang)
                if st == "aim_br":
                    rng = m.corner_range(self.aims.get("aim_tl", (0, 0)), self.aims["aim_br"])
                    if rng is None:
                        self.message = ("Les coins ne correspondent pas aux directions calibrées : "
                                        "recommence la visée (centre, haut-gauche, bas-droit).")
                        self.i = self.steps.index("aim_center")
                        self.still_since, self.progress = None, 0.0
                        return
                    self.res["range"] = rng
                self._next()
        elif st in ("slide_right", "slide_fwd"):
            if self.slide is None:
                self.slide = {"v": (0.0, 0.0, 0.0), "peak": (0.0, 0.0, 0.0), "quiet": 0.0}
            sl = self.slide
            ah = m.reject(a, self.up)
            sl["v"] = tuple(v + c * dt for v, c in zip(sl["v"], ah))
            if m.vlen(sl["v"]) > m.vlen(sl["peak"]):
                sl["peak"] = sl["v"]
            pk = m.vlen(sl["peak"])
            self.progress = min(pk / 0.12, 1.0)
            moving = m.vlen(ah) > 0.35 or m.vlen(m.vsub(w, self.bias)) > 15
            sl["quiet"] = 0.0 if moving else sl["quiet"] + dt
            if pk >= 0.12 and sl["quiet"] > 0.2:
                self.res[st] = sl["peak"]
                self._next()
        else:
            if self.acc is None:
                self.acc = m.CalibAccumulator(self.bias, self.up)
            self.acc.add(w, dt)
            mag = abs(self.acc.yaw) if st == "yaw" else self.acc.horiz_deg()
            self.progress = min(mag / 20.0, 1.0)
            if mag >= 20.0:
                self.res[st] = self.acc.yaw if st == "yaw" else self.acc.horiz
                self._next()

    def _next(self):
        self.i += 1
        self.still_since, self.acc, self.slide, self.progress, self.message = None, None, None, 0.0, None
        if self.flow != "glisse" and self.step == "aim_center" and self.engine is None:
            prof = m.profile_from_calibration(self.up, self.res["yaw"], self.res["pitch"], self.res["roll"])
            self.res["axes"] = prof
            self.engine = m.MotionEngine(self.ctl.cfg["bias"], prof)
            self.engine.bias, self.engine.bias_ok = self.bias, True
        if self.i >= len(self.steps):
            self.finish()

    def finish(self):
        if self.flow == "glisse":
            prof = m.glide_profile(self.res["slide_right"], self.res["slide_fwd"])
            key = f"{self.ctl.pid}_glide"
        else:
            prof = self.res.get("axes") or m.profile_from_calibration(self.up, self.res["yaw"],
                                                                      self.res["pitch"], self.res["roll"])
            if "range" in self.res:
                prof["range"] = self.res["range"]
            key = f"{self.ctl.pid}_{self.source}"
        with open(self.cfg_path) as f:
            user = json.load(f)
        user.setdefault("profiles", {})[key] = prof
        m.update_config_file(self.cfg_path, {"profiles": user["profiles"]})
        self.key, self.profile = key, prof
        self.done = True
        self.progress = 1.0
        self.ctl.paused = self.was_paused
        self.ctl.command("reload")
        self.ctl.recenter(quiet=True)

    def cancel(self):
        self.ctl.paused = self.was_paused

    def as_dict(self):
        return {"active": not self.done, "done": self.done, "source": self.source, "flow": self.flow,
                "step": self.step, "index": self.i, "total": len(self.steps), "text": self.text(),
                "progress": self.progress, "key": getattr(self, "key", None), "message": self.message,
                "profile": getattr(self, "profile", None)}


class WebUI:
    def __init__(self, ctl, cfg_path, port=8737):
        self.ctl, self.cfg_path, self.port = ctl, cfg_path, port
        self.dev = DeviceState()
        self.q = queue.Queue()
        self.aap = None
        self.status = "Démarrage…"
        self.calib = None
        self.t_start = time.time()
        self.server = None
        self.rec = None           # enregistrement brut en cours : {"until", "path", "rows"}

    # ---- liaison avec la boucle principale
    def attach(self, aap):
        self.aap = aap
        self.dev.reset()
        aap.on_raw = lambda d, p: self.dev.feed(p, d)
        self.status = "Connecté"

    def set_status(self, s):
        self.status = s

    def feed_packet(self, pkt):
        self.dev.feed(pkt, "rx")
        rec = self.rec
        if rec:
            now = time.monotonic()
            if now < rec["until"]:
                rec["rows"].append((round(now - rec["t0"], 4), rec.get("phase", ""), pkt.hex()))
            else:
                self.rec = None
                with open(rec["path"], "w") as f:
                    json.dump(rec["rows"], f)
                m.log.info("Enregistrement terminé : %s (%d paquets)", rec["path"], len(rec["rows"]))

    def process_queue(self):
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                self._do(item)
            except Exception as e:  # noqa: BLE001
                m.log.warning("Action interface échouée %s : %s", item, e)

    def _do(self, item):
        kind = item[0]
        aap = self.aap
        if kind == "cmd":
            self.ctl.command(item[1])
        elif kind == "calib":
            if hasattr(self, "_calib_done_t"):
                del self._calib_done_t
            if self.calib:
                self.calib.cancel()
                self._unhook()
            self.calib = None
            if item[1] == "start":
                self.calib = CalibSession(self.ctl, self.cfg_path, item[2], item[3] if len(item) > 3 else None)
                self.ctl.sample_hooks.append(self.calib.hook)
        elif kind == "sensor" and aap and aap.sock:
            _, dev, action, interval, rid = item
            if action == "start":
                aap.rhid_set_feature(dev, 1, int(interval))
                aap.rhid_active[dev] = int(interval)
            elif action == "stop":
                aap.rhid_set_feature(dev, 1, 0)
                aap.rhid_active.pop(dev, None)
            elif action == "get":
                aap._rhid_send([m.rhid_sub(dev, m.RHID_GET, bytes([m.HID_FEATURE, int(rid)]))])
            elif action == "set":
                aap.rhid_set_feature(dev, int(rid), int(interval))
        elif kind == "control" and aap and aap.sock:
            vals = (list(item[2]) + [0, 0, 0, 0])[:4]
            aap.send(m.ctrl_cmd(int(item[1]), *[int(v) & 0xFF for v in vals]))
        elif kind == "raw" and aap and aap.sock:
            aap.send(bytes.fromhex(item[1].replace(" ", "")))
        elif kind == "reconnect" and aap and aap.sock:
            aap.sock.shutdown(2)

    def _unhook(self):
        self.ctl.sample_hooks[:] = [h for h in self.ctl.sample_hooks
                                    if getattr(h, "__self__", None) is not self.calib]

    # ---- données
    def telemetry(self):
        ctl, aap = self.ctl, self.aap
        now = time.monotonic()
        times = [t for t in ctl.sample_times if t > now - 2.0]
        rate = len(times) / 2.0
        gaps = sum(1 for a, b in zip(times, times[1:]) if b - a > 0.05)
        hist = ctl.history[-90:]
        last = hist[-1] if hist else None
        if self.calib and self.calib.done and now - getattr(self, "_calib_done_t", now) > 6:
            self._unhook()
        if self.calib and self.calib.done and not hasattr(self, "_calib_done_t"):
            self._calib_done_t = now
        live = bool(times) and now - times[-1] < 0.5
        return {
            "status": self.status if (aap and aap.sock) else self.status,
            "connected": bool(aap and aap.sock), "live": live,
            "rate": round(rate), "gaps": gaps,
            "mode": ctl.cfg["mode"], "source": ctl.source, "source_cfg": ctl.cfg["source"],
            "place": ctl.place, "paused": ctl.paused, "scroll": ctl.scroll_mode,
            "rates": [round(v, 2) for v in ctl.rates],
            "w": [round(v, 2) for v in last[1]] if last else [0, 0, 0],
            "g": [round(v / 1000, 3) for v in last[2]] if last else [0, 0, 0],
            "att": list(last[4]) if last else [0, 0, 0],
            "bias": [round(v, 2) for v in ctl.engine.bias], "bias_ok": ctl.engine.bias_ok,
            "acc": [round(v, 2) for v in getattr(ctl, "last_acc", (0, 0, 0))],
            "glide_v": [round(v * 100, 1) for v in ctl.glide.v],
            "target": [round(v, 4) for v in ctl.target], "cur": [round(v, 4) for v in ctl.cur],
            "vel": [round(v) for v in ctl.vel],
            "series": [[round(h[3][0], 1), round(h[3][1], 1), round(h[3][2], 1)] for h in hist[::3]],
            "calib": self.calib.as_dict() if self.calib else None,
            "battery": self.dev.battery, "ear": self.dev.ear,
        }

    def state(self):
        aap, ctl = self.aap, self.ctl
        services = []
        if aap:
            for name, dev in sorted(aap.rhid_devs.items(), key=lambda kv: kv[1]):
                info, desc = plist_info(aap.rhid_plist.get(dev, b""))
                last = aap.rhid_last.get(dev)
                services.append({
                    "name": name, "dev": dev, "desc": SERVICE_DESC.get(name, ""),
                    "max_report": info.get("MaxReportSize"), "keys": info,
                    "reports": hid_reports(desc), "count": aap.rhid_counts.get(dev, 0),
                    "last_age": round(time.monotonic() - last[0], 2) if last else None,
                    "last_hex": last[1][:80].hex(" ") if last else None,
                    "motion": dev == aap.rhid_motion,
                })
        replies = []
        if aap:
            for t, kind, dev, pl in aap.rhid_replies[-12:]:
                replies.append({"age": round(time.monotonic() - t, 1),
                                "kind": "GetReport" if kind == m.RHID_GET else "SetReport",
                                "dev": dev, "hex": pl.hex(" ")})
        with self.dev.lock:
            log = list(self.dev.log[-150:])
            counts = {("0x%02x" % k if k >= 0 else ("handshake" if k == -2 else "autre")): v
                      for k, v in self.dev.counts.items()}
        cfg = {k: v for k, v in ctl.cfg.items()}
        return {
            "addr": aap.addr if aap else None, "pid": aap.pid if aap else None,
            "model": MODELS.get(aap.pid, f"Apple 0x{aap.pid}") if aap else None,
            "protocol": ("RemoteHID (0x800)" if aap and aap.rhid_devs else "SensorDataWX (0x100000)")
            if aap else None,
            "interval_us": aap.interval_us if aap else None,
            "info": self.dev.info, "battery": self.dev.battery, "ear": self.dev.ear,
            "controls": sorted(self.dev.controls.values(), key=lambda c: c["ident"]),
            "services": services, "replies": replies, "log": log, "counts": counts,
            "config": cfg, "profiles": ctl.cfg.get("profiles", {}),
            "profile_key": ctl.profile_key(), "calibrated": ctl.profile_key() in ctl.cfg.get("profiles", {}),
            "uptime": round(time.time() - self.t_start),
        }

    # ---- serveur HTTP
    def start(self):
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                data = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.path = self.path.split("?")[0].split("#")[0]
                if self.path in ("/", "/index.html"):
                    data = open(UI_FILE, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path == "/api/ping":
                    self._json({"ok": True})
                elif self.path == "/api/state":
                    self._json(ui.state())
                elif self.path == "/api/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    try:
                        while True:
                            self.wfile.write(b"data: " + json.dumps(ui.telemetry()).encode() + b"\n\n")
                            self.wfile.flush()
                            time.sleep(1 / 30)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                else:
                    self._json({"error": "introuvable"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._json({"error": "JSON invalide"}, 400)
                if self.path == "/api/cmd":
                    ui.q.put(("cmd", str(body.get("cmd", ""))))
                elif self.path == "/api/config":
                    m.update_config_file(ui.cfg_path, body)
                    ui.q.put(("cmd", "reload"))
                elif self.path == "/api/calib":
                    ui.q.put(("calib", body.get("action", "start"), body.get("source", "auto"), body.get("flow")))
                elif self.path == "/api/sensor":
                    ui.q.put(("sensor", int(body["dev"]), body.get("action", "get"),
                              int(body.get("interval", 10000)), int(body.get("report", 1))))
                elif self.path == "/api/control":
                    ui.q.put(("control", int(body["ident"]), body.get("values", [])))
                elif self.path == "/api/raw":
                    ui.q.put(("raw", str(body.get("hex", ""))))
                elif self.path == "/api/reconnect":
                    ui.q.put(("reconnect",))
                elif self.path == "/api/record":
                    secs = float(body.get("seconds", 10))
                    path = os.path.join(HERE, "captures", body.get("name", f"record_{int(time.time())}") + ".json")
                    now = time.monotonic()
                    ui.rec = {"t0": now, "until": now + secs, "path": path, "rows": []}
                    return self._json({"ok": True, "path": path})
                elif self.path == "/api/phase":
                    if ui.rec:
                        ui.rec["phase"] = str(body.get("phase", ""))
                else:
                    return self._json({"error": "introuvable"}, 404)
                self._json({"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        if self.server:
            self.server.shutdown()

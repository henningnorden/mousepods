# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""
opentrack_out.py — sortie « suivi de tête » vers OpenTrack (simulateurs de vol, course…).

Format du paquet (entrée OpenTrack « UDP over network », tracker-udp) :
    6 doubles IEEE-754 (8 o chacun) = 48 octets, dans l'ordre  x, y, z, lacet, tangage, roulis.
    x/y/z en centimètres, angles en degrés.

  Vérifié dans le code source d'OpenTrack (github.com/opentrack/opentrack, branche master) :
    * tracker-udp/ftnoir_tracker_udp.cpp : `sock.readDatagram((char*)last_recv_pose2, sizeof(double[6]))`
      — lecture brute de 6 doubles, paquet rejeté si une valeur est NaN/infinie ;
    * tracker-udp/ftnoir_tracker_udp.h   : port par défaut `port(b, "port", 4242)` ;
    * api/plugin-api.hpp                 : `enum Axis { TX = 0, TY = 1, TZ = 2, Yaw = 3, Pitch = 4, Roll = 5 }` ;
    * proto-udp/ftnoir_protocol_ftn.cpp  : la sortie « UDP over network » envoie le même tableau
      `double[6]` (c'est donc le même format que deux instances d'OpenTrack s'échangent).
  OpenTrack copie les octets tels quels (ordre natif de la machine, aucune conversion) : on envoie
  du petit-boutiste, l'ordre de toutes les machines x86/ARM sur lesquelles OpenTrack tourne.
  Les unités (cm, degrés) sont celles qu'affiche OpenTrack ; elles ne sont pas écrites dans l'API.

Angles absolus : on INTÈGRE les vitesses calibrées (lacet/tangage/roulis de MotionEngine) plutôt que
de dériver les angles du quaternion d'attitude des AirPods, parce que :
  * les vitesses sont déjà exprimées dans le repère de la TÊTE grâce au profil calibré (axe latéral
    appris, signes) — le quaternion est dans le repère de l'écouteur, qui dépend de la façon dont il
    est porté, et il faudrait une seconde calibration pour le ramener à la tête ;
  * le quaternion n'est transmis que sous forme de partie vectorielle Q15 (w reconstruit, signe
    ambigu) et son repère « monde » est celui de la fusion interne des AirPods, non documenté
    (il peut se réinitialiser sans prévenir) ;
  * c'est exactement le chemin déjà utilisé et testé par le mode « pointeur ».
La dérive est corrigée là où on a une référence absolue : la GRAVITÉ fixe le tangage et le roulis
(filtre complémentaire lent, constante `drift_tau_s`). Le lacet n'a aucune référence (pas de
magnétomètre) : biais gyro ré-estimé à l'arrêt par MotionEngine, recentrage (Ctrl+Alt+C), et
recentrage automatique lent optionnel (`yaw_autocenter_s`, désactivé par défaut).
"""
import math
import socket
import struct
import threading
import time

PACKET = struct.Struct("<6d")          # x, y, z (cm), lacet, tangage, roulis (degrés) — 48 octets
assert PACKET.size == 48

DEFAULTS = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 4242,
    "rate_hz": 120,
    "invert_yaw": False, "invert_pitch": False, "invert_roll": False,
    "scale_yaw": 1.0, "scale_pitch": 1.0, "scale_roll": 1.0,
    "smoothing": 0.2,
    "pause_mouse_when_opentrack": True,
    "drift_correction": True,
    "drift_tau_s": 2.0,
    "yaw_autocenter_s": 0.0,
    "lean_experimental": False,
}
BOOL_KEYS = ("enabled", "invert_yaw", "invert_pitch", "invert_roll", "pause_mouse_when_opentrack",
             "drift_correction", "lean_experimental")
FLOAT_KEYS = {"rate_hz": (10, 500), "scale_yaw": (0.0, 10.0), "scale_pitch": (0.0, 10.0),
              "scale_roll": (0.0, 10.0), "smoothing": (0.0, 1.0), "drift_tau_s": (0.2, 60.0),
              "yaw_autocenter_s": (0.0, 600.0)}


def encode_pose(x, y, z, yaw, pitch, roll):
    vals = (x, y, z, yaw, pitch, roll)
    if not all(math.isfinite(v) for v in vals):       # OpenTrack jette les paquets NaN/inf
        raise ValueError("valeur non finie")
    return PACKET.pack(*(float(v) for v in vals))


def decode_pose(data):
    return PACKET.unpack(data[:PACKET.size])


def sanitize(body):
    """Ne garde que les clés connues, typées et bornées (entrée venant de l'interface web)."""
    out = {}
    for k, v in (body or {}).items():
        if k in BOOL_KEYS:
            out[k] = bool(v)
        elif k == "host":
            h = str(v).strip()
            if h and len(h) <= 253 and all(c.isalnum() or c in ".-:_" for c in h):
                out[k] = h
        elif k == "port":
            try:
                p = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= p <= 65535:
                out[k] = p
        elif k in FLOAT_KEYS:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                lo, hi = FLOAT_KEYS[k]
                out[k] = min(max(f, lo), hi)
    return out


# --------------------------------------------------------------------------- petits outils
def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


class _OneEuro:
    """Filtre 1€ (Casiez 2012) : lisse au repos, peu de retard pendant les mouvements rapides."""

    def __init__(self, mincutoff, beta=0.05, dcutoff=1.0):
        self.mc, self.beta, self.dc = mincutoff, beta, dcutoff
        self.x = self.dx = None

    @staticmethod
    def _a(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, dt):
        if self.x is None or dt <= 0:
            self.x, self.dx = x, 0.0
            return x
        dx = (x - self.x) / dt
        self.dx += self._a(self.dc, dt) * (dx - self.dx)
        self.x += self._a(self.mc + self.beta * abs(self.dx), dt) * (x - self.x)
        return self.x

    def reset(self, x=None):
        self.x, self.dx = x, 0.0


# --------------------------------------------------------------------------- angles absolus
class HeadTracker:
    """Vitesses calibrées (°/s) -> angles absolus de la tête (°) relatifs au dernier recentrage."""

    def __init__(self, cfg=None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self._make_filters()
        self.recenter()

    def configure(self, cfg):
        old = self.cfg.get("smoothing")
        self.cfg = {**DEFAULTS, **(cfg or {})}
        if self.cfg["smoothing"] != old:
            self._make_filters()

    def _make_filters(self):
        s = min(max(float(self.cfg["smoothing"]), 0.0), 1.0)
        mc = 8.0 + (0.6 - 8.0) * s
        self.filters = [_OneEuro(mc), _OneEuro(mc), _OneEuro(mc)] if s > 0 else None

    def recenter(self):
        self.yaw = self.pitch = self.roll = 0.0
        self.ref = None                       # repère gravité (haut, latéral, avant) au recentrage
        if getattr(self, "filters", None):
            for f in self.filters:
                f.reset()
        self.out = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def gravity_angles(self, up, signs=(1, 1)):
        """Tangage / roulis absolus d'après la gravité, relatifs au repère figé au recentrage.

        Dans le repère de l'écouteur, une rotation de +θ autour de l'axe latéral fait glisser la
        verticale vers l'axe avant (haut × latéral) : tangage = atan2(haut·avant₀, haut·haut₀).
        Une rotation autour de l'axe avant la fait glisser vers -latéral : roulis = -atan2(haut·lat₀, …).
        Mêmes conventions de signe que MotionEngine.step (testé dans tests/test_opentrack.py).
        """
        up0, lat0, fwd0 = self.ref
        u = _norm(up)
        c = _dot(u, up0)
        gp = math.degrees(math.atan2(_dot(u, fwd0), c)) * signs[0]
        gr = -math.degrees(math.atan2(_dot(u, lat0), c)) * signs[1]
        return gp, gr

    def update(self, yaw_rate, pitch_rate, roll_rate, dt, up=None, lat=None, signs=(1, 1)):
        c = self.cfg
        if not 0.0 < dt < 0.5:
            return self.out
        self.yaw = _wrap180(self.yaw + yaw_rate * dt)
        self.pitch += pitch_rate * dt
        self.roll += roll_rate * dt
        # --- correction de dérive tangage/roulis par la gravité (filtre complémentaire)
        if c["drift_correction"] and up is not None and lat is not None:
            if self.ref is None:
                up0 = _norm(up)
                l = (lat[0] - _dot(lat, up0) * up0[0], lat[1] - _dot(lat, up0) * up0[1],
                     lat[2] - _dot(lat, up0) * up0[2])
                if math.sqrt(_dot(l, l)) > 0.25:      # axe latéral exploitable (pas vertical)
                    lat0 = _norm(l)
                    self.ref = (up0, lat0, _cross(up0, lat0))
                    self._grav0 = self.gravity_angles(up, signs)
                    # angles déjà intégrés depuis le recentrage : on garde la même origine
                    self._grav0 = (self._grav0[0] - self.pitch, self._grav0[1] - self.roll)
            if self.ref is not None:
                gp, gr = self.gravity_angles(up, signs)
                gp -= self._grav0[0]
                gr -= self._grav0[1]
                k = dt / (max(float(c["drift_tau_s"]), 0.05) + dt)
                self.pitch += k * (gp - self.pitch)
                self.roll += k * (gr - self.roll)
        # --- lacet : retour lent au centre (optionnel), seulement quand la tête est quasi immobile
        ac = float(c["yaw_autocenter_s"])
        if ac > 0 and abs(yaw_rate) < 5.0:
            self.yaw *= math.exp(-dt / ac)
        yaw, pitch, roll = self.yaw, self.pitch, self.roll
        if self.filters:
            yaw = self.filters[0](yaw, dt)
            pitch = self.filters[1](pitch, dt)
            roll = self.filters[2](roll, dt)
        yaw *= float(c["scale_yaw"]) * (-1.0 if c["invert_yaw"] else 1.0)
        pitch *= float(c["scale_pitch"]) * (-1.0 if c["invert_pitch"] else 1.0)
        roll *= float(c["scale_roll"]) * (-1.0 if c["invert_roll"] else 1.0)
        x, y, z = self.estimate_lean(yaw, pitch, roll) if c["lean_experimental"] else (0.0, 0.0, 0.0)
        self.out = (x, y, z, yaw, pitch, roll)
        return self.out

    # ======================= EXPÉRIMENTAL (désactivé par défaut : "lean_experimental": false) ====
    # Les AirPods ne donnent que 3 degrés de liberté (rotation). Ce crochet estime un déplacement
    # de la tête avec un modèle « pivot au cou » (la tête tourne autour d'un point ~10 cm sous les
    # oreilles) : pencher la tête la décale aussi. NON VÉRIFIÉ en jeu ; signes à confirmer.
    NECK_CM = 10.0

    def estimate_lean(self, yaw, pitch, roll):
        L = self.NECK_CM
        return (L * math.sin(math.radians(roll)), 0.0, -L * math.sin(math.radians(pitch)))
    # ===================================================================================== /EXP


# --------------------------------------------------------------------------- envoi UDP
class OpenTrackSender:
    """Envoie la dernière pose à fréquence fixe (indépendante de la fréquence du capteur)."""

    def __init__(self, host="127.0.0.1", port=4242, rate_hz=120):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.set_target(host, port)
        self.set_rate(rate_hz)
        self.next_due = 0.0
        self.sent = 0
        self.errors = 0
        self.last_error = None

    def set_target(self, host, port):
        self.addr = (str(host), int(port))

    def set_rate(self, rate_hz):
        self.period = 1.0 / min(max(float(rate_hz), 1.0), 1000.0)

    def due(self, now):
        return now >= self.next_due

    def tick(self, now, pose):
        """Envoie `pose` si l'échéance est passée. Renvoie True si un paquet est parti."""
        if now < self.next_due:
            return False
        # pas de rafale de rattrapage après un retard : on se recale sur `now`
        self.next_due += self.period
        if self.next_due <= now or self.next_due > now + self.period:
            self.next_due = now + self.period
        try:
            self.sock.sendto(encode_pose(*pose), self.addr)
            self.sent += 1
            return True
        except (OSError, ValueError) as e:
            self.errors += 1
            self.last_error = str(e)
            return False

    def close(self):
        self.sock.close()


class OpenTrackOutput:
    """HeadTracker + OpenTrackSender + fil d'envoi. `feed` vient du contrôleur, l'envoi d'un thread."""

    def __init__(self, cfg=None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.tracker = HeadTracker(self.cfg)
        self.sender = OpenTrackSender(self.cfg["host"], self.cfg["port"], self.cfg["rate_hz"])
        self.lock = threading.Lock()
        self.pose = (0.0,) * 6
        self.last_feed = 0.0
        self._run = False
        self._thread = None

    def configure(self, cfg):
        with self.lock:
            self.cfg = {**DEFAULTS, **(cfg or {})}
            self.tracker.configure(self.cfg)
            self.sender.set_target(self.cfg["host"], self.cfg["port"])
            self.sender.set_rate(self.cfg["rate_hz"])

    def feed(self, yaw_rate, pitch_rate, roll_rate, dt, up=None, lat=None, signs=(1, 1)):
        with self.lock:
            self.pose = self.tracker.update(yaw_rate, pitch_rate, roll_rate, dt, up, lat, signs)
            self.last_feed = time.monotonic()

    def recenter(self):
        with self.lock:
            self.tracker.recenter()
            self.pose = (0.0,) * 6

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            pose = self.pose
        return self.sender.tick(now, pose)

    def _loop(self):
        while self._run:
            now = time.monotonic()
            self.tick(now)
            time.sleep(max(0.0005, self.sender.next_due - time.monotonic()))

    def start(self):
        if not self._thread:
            self._run = True
            self._thread = threading.Thread(target=self._loop, name="opentrack", daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.sender.close()

    def status(self):
        with self.lock:
            pose = self.pose
            age = time.monotonic() - self.last_feed if self.last_feed else None
        return {"active": bool(self._thread), "target": f"{self.sender.addr[0]}:{self.sender.addr[1]}",
                "packets": self.sender.sent, "errors": self.sender.errors, "last_error": self.sender.last_error,
                "pose": [round(v, 2) for v in pose], "data_age_s": round(age, 2) if age is not None else None}


def api_state(ctl):
    """Réponse de GET /api/opentrack (utilisée par mousepods_web)."""
    out = getattr(ctl, "opentrack", None)
    return {"config": {**DEFAULTS, **ctl.cfg.get("opentrack", {})},
            "forced": bool(getattr(ctl, "opentrack_forced", False)),
            **(out.status() if out else {"active": False})}

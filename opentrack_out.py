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

Angles absolus, deux sources (`"source"`) :
  * "quaternion" (défaut) : orientation calculée PAR LES AIRPODS (bloc de 58 o, partie vectorielle Q15
    en [20:26], commun aux AirPods 3 / RemoteHID et AirPods Pro / SensorDataWX). Vérifié sur
    captures/airpods3_motion_capture.json : le quaternion va du repère écouteur au repère « monde »
    dont l'axe z est vertical (gravité ramenée au monde = (−0,01 ; 0,08 ; −0,99) à l'arrêt) et l'axe
    de rotation entre deux quaternions suit le gyroscope (cos = 0,9994). w est reconstruit ≥ 0 (q et
    −q sont la même rotation, aucune ambiguïté). Au recentrage on fige ce repère et la base TÊTE
    (latéral calibré, avant, vertical) ; ensuite lacet / tangage / roulis = décomposition
    lacet→tangage→roulis de la rotation depuis le recentrage, exprimée dans cette base. Le tangage et
    le roulis ne dérivent pas (fusion interne avec la gravité) ; le lacet n'a pas de référence
    absolue (pas de magnétomètre exploitable) : recentrage, ou recentrage automatique lent optionnel.
    Si la fusion des AirPods se réinitialise (saut du quaternion sans rotation au gyroscope), le
    repère est recalé pour que la sortie reste continue.
  * "gyro" : INTÉGRATION des vitesses calibrées de MotionEngine, avec correction de dérive du
    tangage / roulis par la gravité (filtre complémentaire, `drift_tau_s`). Utilisé aussi tant que le
    quaternion est absent (attitude nulle au démarrage du capteur).
Conventions (profil calibré) : lacet + = regarder à DROITE, tangage + = regarder en HAUT, roulis + =
oreille droite vers l'épaule droite. OpenTrack n'écrit pas ses conventions ; sa sortie X-Plane passe
lacet / tangage / roulis tels quels aux datarefs « pilot's head » (même lecture pour le lacet et le
tangage). Le roulis est à vérifier dans la fenêtre d'OpenTrack (cases « Invert » si besoin).

Sorties : UDP vers OpenTrack (paquet ci-dessus) et/ou manette virtuelle uinput (sim_joystick.py),
alimentées par la même pose, à fréquence fixe, depuis un fil dédié.
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
    "source": "quaternion",           # "quaternion" (fusion des AirPods) | "gyro" (intégration)
}
BOOL_KEYS = ("enabled", "invert_yaw", "invert_pitch", "invert_roll", "pause_mouse_when_opentrack",
             "drift_correction", "lean_experimental")
SOURCES = ("quaternion", "gyro")
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
        elif k == "source":
            if v in SOURCES:
                out[k] = v
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


def quat_from_att(att):
    """Partie vectorielle Q15 -> quaternion unitaire (w, x, y, z) avec w >= 0, ou None (capteur pas prêt)."""
    if att is None or not any(att):
        return None
    x, y, z = (c / 32768.0 for c in att)
    n2 = x * x + y * y + z * z
    if n2 > 1.01:
        return None
    return (math.sqrt(max(0.0, 1.0 - n2)), x, y, z)


def quat_matrix(q):
    """Quaternion (w, x, y, z) -> matrice 3×3 (corps -> monde), lignes."""
    w, x, y, z = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))


def _mv(M, v):
    return tuple(M[i][0] * v[0] + M[i][1] * v[1] + M[i][2] * v[2] for i in range(3))


def _mm(A, B):
    return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _mt(A):
    return tuple(tuple(A[j][i] for j in range(3)) for i in range(3))


def _angle(M):
    """Angle (°) de la rotation M."""
    return math.degrees(math.acos(min(1.0, max(-1.0, (M[0][0] + M[1][1] + M[2][2] - 1.0) / 2.0))))


def head_angles(M):
    """Rotation M exprimée dans la base tête (x = latéral, y = avant, z = haut) -> (lacet, tangage, roulis) °.

    M = Rz(lacet) · Rx(tangage) · Ry(roulis) : lacet autour de la verticale, puis tangage autour de
    l'axe latéral, puis roulis autour de l'axe avant (ordre naturel d'une tête)."""
    pitch = math.asin(min(1.0, max(-1.0, M[2][1])))
    roll = math.atan2(-M[2][0], M[2][2])
    yaw = math.atan2(-M[0][1], M[1][1])
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


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
        self.qref = None                      # (orientation au recentrage, base tête monde) — source quaternion
        self.q_prev = None
        self.q_off = (0.0, 0.0, 0.0)
        self.yaw_off = 0.0                    # recentrage automatique du lacet
        self._ycont = None                    # lacet « déroulé » (filtre sans saut à ±180°)
        if getattr(self, "filters", None):
            for f in self.filters:
                f.reset()
        self.out = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.used = None                      # "quaternion" | "gyro" : source réellement utilisée

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

    def update(self, yaw_rate, pitch_rate, roll_rate, dt, up=None, lat=None, signs=(1, 1), att=None):
        """Un échantillon. signs = (signe tangage, signe roulis[, signe lacet]) du profil calibré ;
        att = partie vectorielle Q15 du quaternion des AirPods (source « quaternion »)."""
        if not 0.0 < dt < 0.5:
            return self.out
        q = quat_from_att(att) if self.cfg["source"] == "quaternion" else None
        if q is not None and up is not None and lat is not None:
            ang = self._from_quat(q, up, lat, signs, dt, (yaw_rate, pitch_rate, roll_rate))
            if ang is not None:
                self.used = "quaternion"
                self.yaw, self.pitch, self.roll = ang      # le chemin gyro repartirait d'ici
                return self._finish(ang, dt, yaw_rate)
        if self.used == "quaternion":
            self.qref = None                   # quaternion perdu : on continue en intégrant
        self.used = "gyro"
        return self._finish(self._from_rates(yaw_rate, pitch_rate, roll_rate, dt, up, lat, signs), dt, yaw_rate)

    def _from_quat(self, q, up, lat, signs, dt, rates):
        Rq = quat_matrix(q)
        if self.qref is None:
            up_w = _norm(_mv(Rq, _norm(up)))
            if abs(up_w[2]) > 0.8:             # le z « monde » de la fusion est la verticale
                up_w = (0.0, 0.0, math.copysign(1.0, up_w[2]))
            lw = _mv(Rq, lat)
            lw = tuple(a - _dot(lw, up_w) * b for a, b in zip(lw, up_w))
            if math.sqrt(_dot(lw, lw)) < 0.25:  # axe latéral presque vertical : inutilisable
                return None
            lat_w = _norm(lw)
            fwd_w = _cross(up_w, lat_w)
            E = tuple((lat_w[i], fwd_w[i], up_w[i]) for i in range(3))     # colonnes : latéral, avant, haut
            self.qref = [Rq, E]
            self.q_off = (self.yaw, self.pitch, self.roll)
        elif self.q_prev is not None:
            # réinitialisation de la fusion : grand saut d'orientation sans rotation au gyroscope, entre
            # deux échantillons rapprochés (après une coupure Bluetooth, un grand écart est normal : la
            # tête a pu tourner pendant la coupure, et c'est alors le quaternion qui a raison)
            jump = _angle(_mm(Rq, _mt(self.q_prev)))
            seen = math.sqrt(sum(r * r for r in rates)) * dt
            gap = time.monotonic() - self._t_prev
            if jump > max(25.0, 4.0 * seen + 10.0) and gap < 0.1:
                rel_prev = _mm(self.q_prev, _mt(self.qref[0]))
                self.qref[0] = _mm(_mt(rel_prev), Rq)
        self.q_prev = Rq
        self._t_prev = time.monotonic()
        R0, E = self.qref
        M = _mm(_mt(E), _mm(_mm(Rq, _mt(R0)), E))
        y, p, r = head_angles(M)
        ys = signs[2] if len(signs) > 2 else 1
        return (_wrap180(y * ys + self.q_off[0]), p * signs[0] + self.q_off[1], r * signs[1] + self.q_off[2])

    def _from_rates(self, yaw_rate, pitch_rate, roll_rate, dt, up, lat, signs):
        c = self.cfg
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
        return self.yaw, self.pitch, self.roll

    def _finish(self, ang, dt, yaw_rate):
        c = self.cfg
        yaw, pitch, roll = ang
        # --- lacet : retour lent au centre (optionnel), seulement quand la tête est quasi immobile
        ac = float(c["yaw_autocenter_s"])
        if ac > 0 and abs(yaw_rate) < 5.0:
            self.yaw_off += _wrap180(yaw - self.yaw_off) * (1.0 - math.exp(-dt / ac))
        yaw = _wrap180(yaw - self.yaw_off)
        # lacet déroulé pour le filtre (pas de saut de 360° à ±180°)
        self._ycont = yaw if self._ycont is None else self._ycont + _wrap180(yaw - self._ycont)
        yaw = self._ycont
        if self.filters:
            yaw = self.filters[0](yaw, dt)
            pitch = self.filters[1](pitch, dt)
            roll = self.filters[2](roll, dt)
        yaw = _wrap180(yaw)
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

    def tick_skip(self, now):
        """Avance l'échéance sans envoyer (sortie UDP désactivée, manette seule)."""
        self.next_due += self.period
        if self.next_due <= now or self.next_due > now + self.period:
            self.next_due = now + self.period

    def close(self):
        self.sock.close()


class OpenTrackOutput:
    """HeadTracker + sorties (UDP OpenTrack, manette virtuelle) + fil d'envoi.

    `feed` vient du contrôleur (à la cadence du capteur), l'envoi part d'un fil à `rate_hz`."""

    def __init__(self, cfg=None, udp=True):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.tracker = HeadTracker(self.cfg)
        self.sender = OpenTrackSender(self.cfg["host"], self.cfg["port"], self.cfg["rate_hz"])
        self.udp = udp
        self.joystick = None
        self.joystick_error = None
        self.game_bridge = None      # game_bridge.GameBridge (per-game pose files), see set_game_bridge
        self.bridge_error = None
        self.lock = threading.Lock()
        self.pose = (0.0,) * 6
        self.last_feed = 0.0
        self._run = False
        self._thread = None

    def configure(self, cfg, udp=None):
        with self.lock:
            self.cfg = {**DEFAULTS, **(cfg or {})}
            self.tracker.configure(self.cfg)
            self.sender.set_target(self.cfg["host"], self.cfg["port"])
            self.sender.set_rate(self.cfg["rate_hz"])
            if udp is not None:
                self.udp = bool(udp)

    def set_joystick(self, jcfg, factory=None):
        """jcfg None = pas de manette ; sinon crée (ou reconfigure) la manette virtuelle."""
        with self.lock:
            if jcfg is None:
                if self.joystick:
                    self.joystick.close()
                self.joystick = None
                self.joystick_error = None
                return None
            if self.joystick:
                self.joystick.configure(jcfg)
                return self.joystick
        try:
            import sim_joystick
            js = (factory or sim_joystick.VirtualJoystick)(jcfg)
        except Exception as e:  # noqa: BLE001  (pas d'accès à /dev/uinput, evdev absent…)
            self.joystick_error = str(e)
            return None
        with self.lock:
            self.joystick, self.joystick_error = js, None
        return js

    def set_game_bridge(self, bridge):
        """bridge None = no game bridge; else an object with tick(now, pose) and `active`."""
        with self.lock:
            self.game_bridge = bridge

    @property
    def outputs(self):
        return [n for n, on in (("udp", self.udp), ("joystick", self.joystick is not None),
                                ("games", bool(self.game_bridge and self.game_bridge.active))) if on]

    def feed(self, yaw_rate, pitch_rate, roll_rate, dt, up=None, lat=None, signs=(1, 1), att=None):
        with self.lock:
            self.pose = self.tracker.update(yaw_rate, pitch_rate, roll_rate, dt, up, lat, signs, att)
            self.last_feed = time.monotonic()

    def recenter(self):
        with self.lock:
            self.tracker.recenter()
            self.pose = (0.0,) * 6

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            pose, js, udp, bridge = self.pose, self.joystick, self.udp, self.game_bridge
        if not self.sender.due(now):
            return False
        if bridge is not None:
            try:
                bridge.tick(now, pose)
            except Exception as e:  # noqa: BLE001  (the bridge must never stop UDP / joystick output)
                self.bridge_error = str(e)
        if js is not None:
            try:
                js.write(pose)
            except OSError as e:
                self.joystick_error = str(e)
        if udp:
            return self.sender.tick(now, pose)
        self.sender.tick_skip(now)
        return False

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
        if self.joystick:
            self.joystick.close()
            self.joystick = None

    def status(self):
        with self.lock:
            pose = self.pose
            age = time.monotonic() - self.last_feed if self.last_feed else None
            js = self.joystick
        return {"active": bool(self._thread), "udp": self.udp, "outputs": self.outputs,
                "target": f"{self.sender.addr[0]}:{self.sender.addr[1]}",
                "packets": self.sender.sent, "errors": self.sender.errors, "last_error": self.sender.last_error,
                "joystick": {"active": js is not None, "name": getattr(js, "name", None),
                             "writes": js.writes if js else 0, "error": self.joystick_error},
                "source_used": self.tracker.used,
                "pose": [round(v, 2) for v in pose], "data_age_s": round(age, 2) if age is not None else None}


def api_state(ctl):
    """Réponse de GET /api/opentrack (utilisée par mousepods_web)."""
    import sim_joystick
    out = getattr(ctl, "opentrack", None)
    return {"config": {**DEFAULTS, **ctl.cfg.get("opentrack", {})},
            "joystick_config": {**sim_joystick.DEFAULTS, **ctl.cfg.get("sim_joystick", {})},
            "forced": bool(getattr(ctl, "opentrack_forced", False)),
            "joystick_forced": bool(getattr(ctl, "joystick_forced", False)),
            **(out.status() if out else {"active": False, "outputs": []})}

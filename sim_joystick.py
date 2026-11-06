# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""sim_joystick.py — manette virtuelle (uinput) dont les axes sont l'orientation de la tête.

Pour les jeux Linux NATIFS qui ne parlent ni TrackIR ni FreeTrack mais savent lier la caméra / le
regard à des axes de manette (Euro Truck Simulator 2 / American Truck Simulator, War Thunder,
FlightGear, X-Plane…), sans passer par OpenTrack.

Même disposition que la sortie « libevdev joystick » d'OpenTrack (proto-libevdev), pour que les
guides existants s'appliquent tels quels :
    ABS_X / ABS_Y / ABS_Z     = translation x / y / z   (pleine échelle ±100 cm ; 0 avec des AirPods)
    ABS_RX / ABS_RY / ABS_RZ  = lacet / tangage / roulis (pleine échelle par défaut ±180° / ±90° / ±180°)
    valeurs 0..65535, centre 32767 ; BTN_TRIGGER déclaré pour que udev la classe « manette ».
Réduire la pleine échelle (full_yaw_deg…) = plus de débattement dans le jeu pour un même angle de tête.
"""
import math

AXIS_MAX = 65535
CENTER = 32767
DEFAULTS = {"enabled": False, "full_yaw_deg": 180.0, "full_pitch_deg": 90.0, "full_roll_deg": 180.0,
            "full_cm": 100.0}
FLOAT_KEYS = {"full_yaw_deg": (5.0, 180.0), "full_pitch_deg": (5.0, 180.0), "full_roll_deg": (5.0, 180.0),
              "full_cm": (1.0, 200.0)}
NAME = "MousePods head tracker"


def axis_value(v, full):
    """Valeur physique (° ou cm) -> position d'axe 0..65535 (bornée)."""
    if not math.isfinite(v):
        return CENTER
    return int(round(min(max(CENTER + v / float(full) * CENTER, 0), AXIS_MAX)))


def pose_to_axes(pose, cfg):
    """pose (x, y, z, lacet, tangage, roulis) -> {code: valeur} pour ABS_X..ABS_RZ (noms en clé)."""
    c = {**DEFAULTS, **(cfg or {})}
    x, y, z, yaw, pitch, roll = pose
    return {"ABS_X": axis_value(x, c["full_cm"]), "ABS_Y": axis_value(y, c["full_cm"]),
            "ABS_Z": axis_value(z, c["full_cm"]), "ABS_RX": axis_value(yaw, c["full_yaw_deg"]),
            "ABS_RY": axis_value(pitch, c["full_pitch_deg"]), "ABS_RZ": axis_value(roll, c["full_roll_deg"])}


def sanitize(body):
    out = {}
    for k, v in (body or {}).items():
        if k == "enabled":
            out[k] = bool(v)
        elif k in FLOAT_KEYS:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                lo, hi = FLOAT_KEYS[k]
                out[k] = min(max(f, lo), hi)
    return out


class VirtualJoystick:
    """Périphérique uinput. `device_factory(capabilities, name)` permet de tester sans /dev/uinput."""

    def __init__(self, cfg=None, device_factory=None):
        from evdev import AbsInfo, UInput, ecodes as e
        self.e = e
        self.name = NAME
        self.cfg = {**DEFAULTS, **(cfg or {})}
        info = AbsInfo(value=CENTER, min=0, max=AXIS_MAX, fuzz=0, flat=0, resolution=0)
        caps = {e.EV_KEY: [e.BTN_TRIGGER],
                e.EV_ABS: [(getattr(e, n), info) for n in ("ABS_X", "ABS_Y", "ABS_Z", "ABS_RX", "ABS_RY", "ABS_RZ")]}
        if device_factory is None:
            self.dev = UInput(caps, name=NAME, vendor=0x4D50, product=0x0001, version=1, bustype=e.BUS_USB)
        else:
            self.dev = device_factory(caps, NAME)
        self.last = {}
        self.writes = 0

    def configure(self, cfg):
        self.cfg = {**DEFAULTS, **(cfg or {})}

    def write(self, pose):
        axes = pose_to_axes(pose, self.cfg)
        changed = False
        for name, v in axes.items():
            if self.last.get(name) != v:
                self.dev.write(self.e.EV_ABS, getattr(self.e, name), v)
                self.last[name] = v
                changed = True
        if changed:
            self.dev.syn()
            self.writes += 1
        return changed

    def close(self):
        try:
            self.dev.close()
        except Exception:  # noqa: BLE001
            pass

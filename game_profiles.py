# SPDX-License-Identifier: Apache-2.0
"""Per-game head-tracking profiles: smoothing, response curve and range per axis, neck model.

Pure and deterministic: time comes in as `dt`, nothing reads a clock.
Angles in degrees (opentrack convention: yaw + = look right, pitch + = look up,
roll + = right ear towards the right shoulder); translation in cm (x right, y up, z forward).
The game sees X + = left: the DLLs negate yaw but pass X through, like opentrack's wine
wrapper, so ProfileFilter turns the neck offset round before it leaves (GAME_X).
"""
import copy
import math

AXES = ("yaw", "pitch", "roll")
CURVE_PIVOT_DEG = 40.0          # at this head angle the output equals gain * angle, whatever the curve
NECK_EYES_CM = (0.0, 8.0, 9.0)  # eyes relative to the neck pivot: right, up, forward
GAME_X = -1.0                   # neck x (right +) -> game X (left +)

DEFAULT_PROFILE = {
    "smoothing": 0.3,
    "neck": {"enabled": False, "strength": 1.0, "invert_x": False, "invert_z": False},
    "axes": {
        "yaw": {"enabled": True, "invert": False, "gain": 2.0, "curve": 1.4, "limit": 180},
        "pitch": {"enabled": True, "invert": False, "gain": 1.5, "curve": 1.4, "limit": 90},
        "roll": {"enabled": True, "invert": False, "gain": 1.0, "curve": 1.0, "limit": 90},
    },
}

_RANGES = {"gain": (0.1, 6.0), "curve": (1.0, 3.0), "limit": (0.0, 180.0)}


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(v):
        return default
    return min(max(v, lo), hi)


def normalize_profile(raw):
    """Merge `raw` onto the defaults, clamp every number, drop unknown keys."""
    p = copy.deepcopy(DEFAULT_PROFILE)
    raw = raw if isinstance(raw, dict) else {}
    p["smoothing"] = _clamp(raw.get("smoothing", p["smoothing"]), 0.0, 1.0, p["smoothing"])
    neck = raw.get("neck") if isinstance(raw.get("neck"), dict) else {}
    p["neck"]["enabled"] = bool(neck.get("enabled", p["neck"]["enabled"]))
    p["neck"]["strength"] = _clamp(neck.get("strength", p["neck"]["strength"]), 0.0, 2.0, p["neck"]["strength"])
    for k in ("invert_x", "invert_z"):           # games that read the axis the other way round
        p["neck"][k] = bool(neck.get(k, p["neck"][k]))
    axes = raw.get("axes") if isinstance(raw.get("axes"), dict) else {}
    for name in AXES:
        src = axes.get(name) if isinstance(axes.get(name), dict) else {}
        dst = p["axes"][name]
        dst["enabled"] = bool(src.get("enabled", dst["enabled"]))
        dst["invert"] = bool(src.get("invert", dst["invert"]))
        for key, (lo, hi) in _RANGES.items():
            dst[key] = _clamp(src.get(key, dst[key]), lo, hi, dst[key])
    return p


def map_axis(deg, axis):
    """Head angle -> in-game angle. curve > 1 keeps small movements around the centre calm."""
    if not axis["enabled"] or deg == 0:
        return 0.0
    mag = abs(deg)
    out = axis["gain"] * CURVE_PIVOT_DEG * (mag / CURVE_PIVOT_DEG) ** axis["curve"]
    out = min(out, axis["limit"])
    out = math.copysign(out, deg)
    return -out if axis["invert"] else out


def neck_offset(yaw, pitch, roll, strength):
    """Where the eyes move when the head turns about the neck pivot (cm). Computed, not measured."""
    if strength == 0 or (yaw == 0 and pitch == 0 and roll == 0):
        return (0.0, 0.0, 0.0)
    y, p, r = (math.radians(a) for a in (yaw, pitch, roll))
    vx, vy, vz = NECK_EYES_CM
    # roll about the forward axis (right ear down = positive)
    x1 = vx * math.cos(r) + vy * math.sin(r)
    y1 = -vx * math.sin(r) + vy * math.cos(r)
    z1 = vz
    # pitch about the right axis (look up = positive)
    y2 = y1 * math.cos(p) + z1 * math.sin(p)
    z2 = -y1 * math.sin(p) + z1 * math.cos(p)
    x2 = x1
    # yaw about the up axis (look right = positive)
    x3 = x2 * math.cos(y) + z2 * math.sin(y)
    z3 = -x2 * math.sin(y) + z2 * math.cos(y)
    y3 = y2
    return ((x3 - vx) * strength, (y3 - vy) * strength, (z3 - vz) * strength)


class _OneEuro:
    """1€ filter (Casiez et al. 2012): calm at rest, little lag in fast movements."""

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


def smoothing_cutoff(smoothing):
    """0 -> no filter; 0.3 -> ~2.5 Hz; 1 -> 0.1 Hz (strong)."""
    return None if smoothing <= 0 else 10 ** (1.0 - 2.0 * smoothing)


class ProfileFilter:
    def __init__(self, profile):
        self.profile = normalize_profile(profile)
        mc = smoothing_cutoff(self.profile["smoothing"])
        self.filters = [_OneEuro(mc) for _ in AXES] if mc else None

    def apply(self, pose6, dt):
        x, y, z, yaw, pitch, roll = (float(v) for v in pose6)
        if self.filters:
            yaw, pitch, roll = (f(v, dt) for f, v in zip(self.filters, (yaw, pitch, roll)))
        axes = self.profile["axes"]
        out = [map_axis(a, axes[n]) for a, n in zip((yaw, pitch, roll), AXES)]
        neck = self.profile["neck"]
        if neck["enabled"]:
            dx, dy, dz = neck_offset(yaw, pitch, roll, neck["strength"])
            sx = -GAME_X if neck["invert_x"] else GAME_X
            sz = -1.0 if neck["invert_z"] else 1.0
            x, y, z = x + sx * dx, y + dy, z + sz * dz
        return (x, y, z, out[0], out[1], out[2])

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
CURVE_PIVOT_DEG = 40.0          # old model: the output was gain * angle at this head angle
NECK_EYES_CM = (0.0, 8.0, 9.0)  # eyes relative to the neck pivot: right, up, forward
GAME_X = -1.0                   # neck x (right +) -> game X (left +)

# Before calibration an axis was gain/curve/limit around a fixed 40 degree pivot. Saved profiles in
# that form are converted on load so they behave the same (legacy_edge).
LEGACY_DEFAULTS = {"yaw": (2.0, 1.4, 180.0), "pitch": (1.5, 1.4, 90.0), "roll": (1.0, 1.0, 90.0)}
_LEGACY_RANGES = {"gain": (0.1, 12.0), "curve": (1.0, 3.0), "limit": (0.0, 180.0)}
_RANGES = {"edge_neg": (2.0, 180.0), "edge_pos": (2.0, 180.0), "target": (0.0, 180.0), "curve": (1.0, 3.0)}
MAX_MONITORS = 8


def legacy_edge(gain, curve, limit):
    """Head angle at which the old model reached its limit."""
    return CURVE_PIVOT_DEG * (limit / (gain * CURVE_PIVOT_DEG)) ** (1.0 / curve)


def _axis_from_legacy(gain, curve, limit, enabled=True, invert=False):
    lo, hi = _RANGES["edge_pos"]
    edge = round(min(max(legacy_edge(gain, curve, limit), lo), hi), 1) if limit > 0 else 40.0
    return {"enabled": bool(enabled and limit > 0), "invert": bool(invert),
            "edge_neg": edge, "edge_pos": edge, "target": float(limit), "curve": float(curve)}


DEFAULT_PROFILE = {
    "smoothing": 0.3,
    "neck": {"enabled": False, "strength": 1.0, "lean_forward": False},
    "axes": {name: _axis_from_legacy(*vals) for name, vals in LEGACY_DEFAULTS.items()},
    "monitors": [],                 # calibration: chosen monitors, left to right, one marked main
    "own": False,                   # False: yaw/pitch range come from the baseline calibration
}


def sensitivity_to_curve(s):
    """Sensitivity 0..1 (0 = very calm centre, 1 = even all the way) -> curve exponent 3..1."""
    return 3.0 - 2.0 * min(max(float(s), 0.0), 1.0)


def curve_to_sensitivity(c):
    return min(max((3.0 - float(c)) / 2.0, 0.0), 1.0)


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
    p["neck"]["lean_forward"] = bool(neck.get("lean_forward", p["neck"]["lean_forward"]))
    axes = raw.get("axes") if isinstance(raw.get("axes"), dict) else {}
    for name in AXES:
        src = axes.get(name) if isinstance(axes.get(name), dict) else {}
        if "edge_neg" not in src and "edge_pos" not in src and ("gain" in src or "limit" in src):
            old = {k: _clamp(src.get(k, d), *_LEGACY_RANGES[k], d)
                   for k, d in zip(("gain", "curve", "limit"), LEGACY_DEFAULTS[name])}
            src = _axis_from_legacy(old["gain"], old["curve"], old["limit"],
                                    src.get("enabled", True), src.get("invert", False))
        dst = p["axes"][name]
        dst["enabled"] = bool(src.get("enabled", dst["enabled"]))
        dst["invert"] = bool(src.get("invert", dst["invert"]))
        for key, (lo, hi) in _RANGES.items():
            dst[key] = _clamp(src.get(key, dst[key]), lo, hi, dst[key])
    p["monitors"] = _monitors(raw.get("monitors"))
    # saved before the baseline existed: a profile whose axes differ from the default has own values
    p["own"] = bool(raw["own"]) if "own" in raw else p["axes"] != DEFAULT_PROFILE["axes"]
    return p


def normalize_baseline(raw):
    """Baseline calibration (Calibration page): head angle at each edge, camera at the edge, monitors.
    None if missing or unusable."""
    if not isinstance(raw, dict):
        return None
    lo, hi = _RANGES["edge_pos"]
    out = {}
    for axis in ("yaw", "pitch"):
        src = raw.get(axis) if isinstance(raw.get(axis), dict) else {}
        vals = {k: _clamp(src.get(k), lo, hi, None) for k in ("neg", "pos")}
        if None in vals.values():
            return None
        out[axis] = vals
    out["target_side"] = _clamp(raw.get("target_side"), 0.0, 180.0, 180.0)
    out["target_vert"] = _clamp(raw.get("target_vert"), 0.0, 180.0, 90.0)
    out["monitors"] = _monitors(raw.get("monitors"))
    return out


def effective_profile(profile, baseline):
    """The profile a game gets: its own values, or yaw/pitch range from the baseline."""
    if profile.get("own") or not baseline:
        return profile
    p = copy.deepcopy(profile)
    for axis, target in (("yaw", "target_side"), ("pitch", "target_vert")):
        a = p["axes"][axis]
        a["edge_neg"], a["edge_pos"], a["target"] = baseline[axis]["neg"], baseline[axis]["pos"], baseline[target]
    return p


def _monitors(raw):
    out = []
    for m in raw if isinstance(raw, list) else []:
        if isinstance(m, dict) and isinstance(m.get("connector"), str) and m["connector"]:
            out.append({"connector": m["connector"][:64], "main": bool(m.get("main"))})
        if len(out) == MAX_MONITORS:
            break
    if out:
        main = next((i for i, m in enumerate(out) if m["main"]), 0)
        for i, m in enumerate(out):
            m["main"] = i == main
    return out


def map_axis(deg, axis):
    """Head angle -> in-game angle: at the edge (head angle per side) the camera is at target.
    curve > 1 keeps small movements around the centre calm."""
    if not axis["enabled"] or deg == 0:
        return 0.0
    edge = axis["edge_pos"] if deg > 0 else axis["edge_neg"]
    out = axis["target"] * min(abs(deg) / edge, 1.0) ** axis["curve"]
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
            # a real head comes back a little when it turns; lean_forward goes forward instead,
            # like leaning in to look past a cab pillar
            sz = -1.0 if neck["lean_forward"] else 1.0
            x, y, z = x + GAME_X * dx, y + dy, z + sz * dz
        return (x, y, z, out[0], out[1], out[2])

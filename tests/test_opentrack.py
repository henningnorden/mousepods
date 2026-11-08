# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Tests de la sortie OpenTrack : paquet, cadence, recentrage, gains, intégration, UDP local.

Lancer :  python -m unittest tests.test_opentrack -v
"""
import math
import os
import socket
import struct
import time
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airpods_mouse_controller as m  # noqa: E402
import opentrack_out as ot  # noqa: E402


def rot(axis, ang_deg):
    x, y, z = m.vnorm(axis)
    a = math.radians(ang_deg)
    c, s, C = math.cos(a), math.sin(a), 1 - math.cos(a)
    return [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]


def to_body(R, v):          # R^T v : monde -> corps (R = orientation corps -> monde)
    return tuple(sum(R[i][j] * v[i] for i in range(3)) for j in range(3))


def matmul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


UP, LAT, FWD = (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)


def raw(**kw):
    """Configuration sans lissage ni correction (comportement brut de l'intégrateur)."""
    return {"smoothing": 0.0, "drift_correction": False, **kw}


class TestPacket(unittest.TestCase):
    def test_size_and_roundtrip(self):
        pose = (1.5, -2.25, 3.0, 45.125, -12.5, 7.75)
        pkt = ot.encode_pose(*pose)
        self.assertEqual(len(pkt), 48)
        self.assertEqual(ot.decode_pose(pkt), pose)
        # ordre exact : 6 doubles petit-boutistes x, y, z, lacet, tangage, roulis
        self.assertEqual(struct.unpack("<6d", pkt), pose)
        self.assertEqual(pkt[24:32], struct.pack("<d", 45.125))

    def test_rejects_non_finite(self):
        with self.assertRaises(ValueError):
            ot.encode_pose(0, 0, 0, float("nan"), 0, 0)

    def test_sanitize(self):
        out = ot.sanitize({"port": "70000", "host": "evil host;rm", "rate_hz": 9999, "enabled": 1,
                           "scale_yaw": "2", "unknown": 3, "smoothing": float("nan")})
        self.assertEqual(out, {"rate_hz": 500, "enabled": True, "scale_yaw": 2.0})
        self.assertEqual(ot.sanitize({"host": "192.168.1.20", "port": 4243}),
                         {"host": "192.168.1.20", "port": 4243})


class FakeSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        pass


class TestRate(unittest.TestCase):
    def test_rate_limit_decoupled_from_sensor(self):
        s = ot.OpenTrackSender("127.0.0.1", 4242, rate_hz=120)
        s.sock.close()
        s.sock = FakeSock()
        t, n = 0.0, 0
        while t < 1.0:                 # capteur à 1000 Hz -> ~120 paquets
            n += s.tick(t, (0,) * 6)
            t += 0.001
        self.assertTrue(118 <= n <= 121, n)
        self.assertEqual(n, len(s.sock.sent))
        self.assertEqual(s.sock.sent[0][1], ("127.0.0.1", 4242))

    def test_no_burst_after_stall(self):
        s = ot.OpenTrackSender(rate_hz=100)
        s.sock.close()
        s.sock = FakeSock()
        self.assertTrue(s.tick(0.0, (0,) * 6))
        self.assertTrue(s.tick(5.0, (0,) * 6))          # 5 s de retard : un seul paquet…
        self.assertFalse(s.tick(5.001, (0,) * 6))       # …puis on reprend la cadence normale
        self.assertTrue(s.tick(5.011, (0,) * 6))


class TestTracker(unittest.TestCase):
    def test_integration_from_rates(self):
        tr = ot.HeadTracker(raw())
        for _ in range(100):                          # 1 s à 100 Hz
            tr.update(30.0, -10.0, 5.0, 0.01)
        x, y, z, yaw, pitch, roll = tr.out
        self.assertEqual((x, y, z), (0.0, 0.0, 0.0))  # rotation seule par défaut
        self.assertAlmostEqual(yaw, 30.0, places=6)
        self.assertAlmostEqual(pitch, -10.0, places=6)
        self.assertAlmostEqual(roll, 5.0, places=6)

    def test_yaw_wraps(self):
        tr = ot.HeadTracker(raw())
        for _ in range(100):
            tr.update(200.0, 0, 0, 0.01)
        self.assertAlmostEqual(tr.out[3], -160.0, places=6)

    def test_recenter(self):
        tr = ot.HeadTracker(raw())
        for _ in range(50):
            tr.update(40.0, 20.0, 0, 0.01)
        tr.recenter()
        self.assertEqual(tr.out, (0.0,) * 6)
        tr.update(10.0, 0, 0, 0.01)
        self.assertAlmostEqual(tr.out[3], 0.1, places=6)

    def test_invert_and_scale(self):
        tr = ot.HeadTracker(raw(invert_yaw=True, scale_pitch=2.0, invert_roll=True, scale_roll=0.5))
        for _ in range(100):
            tr.update(10.0, 10.0, 10.0, 0.01)
        self.assertAlmostEqual(tr.out[3], -10.0, places=6)
        self.assertAlmostEqual(tr.out[4], 20.0, places=6)
        self.assertAlmostEqual(tr.out[5], -5.0, places=6)

    def test_head_is_before_global_scale_and_invert(self):
        """Game profiles get the head angle; the Simulators page gain/invert is for opentrack UDP."""
        tr = ot.HeadTracker(raw(scale_yaw=3.0, invert_pitch=True))
        for _ in range(100):
            tr.update(10.0, 10.0, 0.0, 0.01)
        self.assertAlmostEqual(tr.out[3], 30.0, places=6)
        self.assertAlmostEqual(tr.out[4], -10.0, places=6)
        self.assertAlmostEqual(tr.head[0], 10.0, places=6)
        self.assertAlmostEqual(tr.head[1], 10.0, places=6)
        tr.recenter()
        self.assertEqual(tr.head, (0.0, 0.0, 0.0))

    def test_autocenter_suspended(self):
        def hold(suspend):
            tr = ot.HeadTracker(raw(yaw_autocenter_s=1.0))
            tr.suspend_autocenter = suspend
            for _ in range(30):
                tr.update(100.0, 0, 0, 0.01)           # turn to 30 degrees
            for _ in range(200):
                tr.update(0.0, 0, 0, 0.01)             # hold still 2 s
            return tr.head[0]
        self.assertAlmostEqual(hold(True), 30.0, places=4)
        self.assertLess(hold(False), 10.0)

    def test_bridge_gets_head_pose(self):
        out = ot.OpenTrackOutput(raw(scale_yaw=3.0, invert_pitch=True), udp=False)
        got = []
        out.set_game_bridge(types.SimpleNamespace(active=True, tick=lambda now, pose: got.append(pose)))
        for _ in range(100):
            out.feed(10.0, 10.0, 0.0, 0.01)
        self.assertIsNotNone(out.head())
        out.tick(1000.0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][:3], (0.0, 0.0, 0.0))
        self.assertAlmostEqual(got[0][3], 10.0, places=6)
        self.assertAlmostEqual(got[0][4], 10.0, places=6)
        self.assertAlmostEqual(out.pose[3], 30.0, places=6)     # UDP keeps the global gain

    def test_smoothing_converges(self):
        tr = ot.HeadTracker({"smoothing": 0.8, "drift_correction": False})
        tr.update(0.0, 0, 0, 0.01)
        tr.update(1000.0, 0, 0, 0.01)                 # saut de 10° en un échantillon
        self.assertLess(tr.out[3], 10.0)
        for _ in range(300):
            tr.update(0.0, 0, 0, 0.01)
        self.assertAlmostEqual(tr.out[3], 10.0, places=2)

    def test_lean_hook_off_by_default(self):
        tr = ot.HeadTracker({"smoothing": 0.0})
        self.assertFalse(tr.cfg["lean_experimental"])
        for _ in range(100):
            tr.update(0, 20.0, 20.0, 0.01)
        self.assertEqual(tr.out[:3], (0.0, 0.0, 0.0))
        tr2 = ot.HeadTracker(raw(lean_experimental=True))
        for _ in range(100):
            tr2.update(0, 0, 20.0, 0.01)
        self.assertNotEqual(tr2.out[0], 0.0)


class TestWithMotionEngine(unittest.TestCase):
    """Chaîne réelle : rotation simulée -> MotionEngine (vitesses calibrées) -> HeadTracker."""

    def simulate(self, axis_w, total_deg, tracker, worn=None, gyro_bias=(0.0, 0.0, 0.0), hold_s=0.0,
                 still_s=1.0, prof_extra=None):
        worn = worn or rot((0.3, -0.5, 0.8), 70)      # écouteur porté de travers dans l'oreille
        lat_body = to_body(worn, LAT)
        prof = {**m.DEFAULT_PROFILE, "lat": list(lat_body), **(prof_extra or {})}
        eng = m.MotionEngine(m.DEFAULTS["bias"], prof)
        dt, steps = 0.01, 100
        R = worn
        seq = [0.0] * int(still_s / dt) + [total_deg / steps] * steps + [0.0] * int(hold_s / dt)
        for i, dang in enumerate(seq):
            w_world = tuple(a * dang / dt for a in axis_w)
            R = matmul(rot(axis_w, dang), R) if dang else R
            w_body = tuple(a + b for a, b in zip(to_body(R, w_world), gyro_bias))
            g_body = tuple(1000.0 * v for v in to_body(R, UP))
            y, p, r = eng.step(w_body, g_body, dt)
            if i == int(still_s / dt) - 1:
                tracker.recenter()                    # recentrage une fois le biais appris
            tracker.update(y, p, r, dt, eng.g, eng.lat, (eng.p["pitch_sign"], eng.p["roll_sign"]))
        return tracker.out

    def test_yaw_from_engine(self):
        out = self.simulate(UP, 30.0, ot.HeadTracker(raw()))
        self.assertAlmostEqual(out[3], 30.0, delta=1.0)
        self.assertAlmostEqual(out[4], 0.0, delta=1.0)

    def test_gravity_agrees_with_integrated_pitch_and_roll(self):
        # avec correction de dérive : le résultat ne doit pas être tiré ailleurs que l'angle vrai
        for axis, idx in ((LAT, 4), (FWD, 5)):
            a = self.simulate(axis, 20.0, ot.HeadTracker(raw()), hold_s=3.0)
            b = self.simulate(axis, 20.0, ot.HeadTracker(raw(drift_correction=True)), hold_s=3.0)
            self.assertAlmostEqual(abs(a[idx]), 20.0, delta=1.5)
            self.assertAlmostEqual(b[idx], a[idx], delta=1.5)
        # profil calibré avec signes inversés : la gravité suit le même signe que les vitesses
        neg = {"pitch_sign": -1, "roll_sign": -1}
        for axis, idx in ((LAT, 4), (FWD, 5)):
            a = self.simulate(axis, 20.0, ot.HeadTracker(raw()), hold_s=3.0, prof_extra=neg)
            b = self.simulate(axis, 20.0, ot.HeadTracker(raw(drift_correction=True)), hold_s=3.0, prof_extra=neg)
            self.assertAlmostEqual(b[idx], a[idx], delta=1.5)

    def test_drift_correction_removes_pitch_drift(self):
        # biais gyro non appris (apparaît après le recentrage) : sans correction, le tangage dérive
        cfg_off, cfg_on = raw(), raw(drift_correction=True, drift_tau_s=1.0)
        res = {}
        for name, cfg in (("off", cfg_off), ("on", cfg_on)):
            tr = ot.HeadTracker(cfg)
            # on simule le biais en ajoutant une vitesse de tangage parasite directement
            orig = tr.update
            tr.update = (lambda o: (lambda y, p, r, dt, *a: o(y, p + 3.0, r, dt, *a)))(orig)
            res[name] = self.simulate(UP, 0.0, tr, hold_s=8.0)[4]
        self.assertGreater(abs(res["off"]), 20.0)     # 3 °/s × 9 s
        self.assertLess(abs(res["on"]), 4.0)          # borné par la gravité (≈ 3 °/s × τ)


# --------------------------------------------------------------------------- source « quaternion »
def mat_to_att(R):
    """Matrice corps -> monde -> partie vectorielle Q15 (w >= 0), comme dans le bloc des AirPods."""
    tr = R[0][0] + R[1][1] + R[2][2]
    if tr > -0.9:
        w = math.sqrt(max(0.0, 1 + tr)) / 2
        x, y, z = (R[2][1] - R[1][2]) / (4 * w), (R[0][2] - R[2][0]) / (4 * w), (R[1][0] - R[0][1]) / (4 * w)
    else:                                     # rotation proche de 180° : autre branche
        i = max(range(3), key=lambda k: R[k][k])
        j, k = (i + 1) % 3, (i + 2) % 3
        v = [0.0, 0.0, 0.0]
        v[i] = math.sqrt(max(0.0, 1 + R[i][i] - R[j][j] - R[k][k])) / 2
        w = (R[k][j] - R[j][k]) / (4 * v[i])
        v[j] = (R[j][i] + R[i][j]) / (4 * v[i])
        v[k] = (R[k][i] + R[i][k]) / (4 * v[i])
        x, y, z = v
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    return tuple(int(round(c * 32767)) for c in (x, y, z))


FWD_HEAD = (-1.0, 0.0, 0.0)        # avant de la tête = haut × latéral (convention de MotionEngine)


class TestQuaternion(unittest.TestCase):
    """Pose absolue tirée du quaternion des AirPods, écouteur porté de travers, repère monde inconnu."""

    def run_seq(self, rel_seq, worn=None, fusion_yaw=77.0, tracker=None, resets=()):
        worn = worn or rot((0.3, -0.5, 0.8), 70)
        lat_body, up_body = to_body(worn, LAT), to_body(worn, UP)
        tr = tracker or ot.HeadTracker(raw())
        prev = rel_seq[0]
        for i, R_rel in enumerate(rel_seq):
            fy = fusion_yaw + sum(a for k, a in resets if i >= k)     # la fusion se réinitialise
            R_body = matmul(R_rel, worn)
            att = mat_to_att(matmul(rot(UP, fy), R_body))
            up_now = to_body(R_body, UP)
            # vitesse vue par le gyroscope : l'angle réellement tourné (la tête bouge vraiment)
            turned = ot._angle(ot._mm(R_rel, ot._mt(prev))) / 0.01
            prev = R_rel
            tr.update(turned, 0.0, 0.0, 0.01, up_now if i else up_body, lat_body, (1, 1, 1), att)
        return tr

    def test_each_axis_and_sign(self):
        I = rot(UP, 0)
        for axis, ang, idx in ((UP, 30.0, 3), (LAT, 20.0, 4), (FWD_HEAD, 15.0, 5), (UP, -120.0, 3)):
            tr = self.run_seq([I] * 5 + [rot(axis, ang)] * 5)
            self.assertEqual(tr.used, "quaternion")
            self.assertAlmostEqual(tr.out[idx], ang, delta=0.3, msg=(axis, tr.out))
            others = [tr.out[k] for k in (3, 4, 5) if k != idx]
            self.assertLess(max(abs(v) for v in others), 0.3, tr.out)

    def test_combined_yaw_pitch_roll(self):
        R = matmul(rot(UP, 40.0), matmul(rot(LAT, -25.0), rot(FWD_HEAD, 10.0)))
        tr = self.run_seq([rot(UP, 0)] * 3 + [R] * 3)
        self.assertAlmostEqual(tr.out[3], 40.0, delta=0.3)
        self.assertAlmostEqual(tr.out[4], -25.0, delta=0.3)
        self.assertAlmostEqual(tr.out[5], 10.0, delta=0.3)

    def test_fusion_reset_keeps_output_continuous(self):
        seq = [rot(UP, 0)] * 3 + [rot(UP, 20.0)] * 10
        tr = self.run_seq(seq, resets=((8, 90.0),))       # +90° de lacet « monde » sans bouger la tête
        self.assertAlmostEqual(tr.out[3], 20.0, delta=0.5)

    def test_recenter_and_fallback_to_gyro(self):
        tr = self.run_seq([rot(UP, 0)] * 3 + [rot(UP, 30.0)] * 3)
        tr.recenter()
        self.assertEqual(tr.out, (0.0,) * 6)
        tr2 = ot.HeadTracker(raw())
        tr2.update(10.0, 0, 0, 0.1, UP, LAT, (1, 1, 1), (0, 0, 0))     # attitude nulle : capteur pas prêt
        self.assertEqual(tr2.used, "gyro")
        self.assertAlmostEqual(tr2.out[3], 1.0)
        tr3 = ot.HeadTracker(raw(source="gyro"))
        tr3.update(10.0, 0, 0, 0.1, UP, LAT, (1, 1, 1), mat_to_att(rot(UP, 50)))
        self.assertEqual(tr3.used, "gyro")

    def test_smoothing_does_not_jump_at_180(self):
        tr = ot.HeadTracker({"smoothing": 0.5, "drift_correction": False})
        worn = rot((0.3, -0.5, 0.8), 70)
        outs = []
        for i in range(400):
            R_body = matmul(rot(UP, i * 1.0), worn)                 # tour complet, 1° par échantillon
            tr.update(100.0, 0, 0, 0.01, to_body(R_body, UP), to_body(worn, LAT), (1, 1, 1),
                      mat_to_att(R_body))
            outs.append(tr.out[3])
        steps = [abs(ot._wrap180(b - a)) for a, b in zip(outs, outs[1:])]
        self.assertLess(max(steps), 5.0)

    def test_agrees_with_gyro_on_real_capture(self):
        """Capture réelle AirPods 3 : sur des fenêtres de 25 échantillons, les angles du quaternion et
        l'intégration des vitesses de MotionEngine vont dans le même sens sur les 3 axes."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures",
                            "airpods3_motion_capture.json")
        if not os.path.exists(path):
            self.skipTest("capture absente")
        import json
        with open(path) as fh:
            rows = [r for r in json.load(fh) if any(r["att"])]
        g0 = m.vnorm(tuple(rows[0]["grav"]))
        lat = m.vnorm(m.vcross(g0, (1.0, 0.0, 0.0)))
        eng = m.MotionEngine(m.DEFAULTS["bias"], {**m.DEFAULT_PROFILE, "lat": list(lat)})
        res, tr, acc, n, prev = [], None, [0.0] * 3, 0, None
        for r in rows:
            dt = min(max(r["t"] - prev, 0.001), 0.05) if prev else 0.01
            prev = r["t"]
            y, p, rr = eng.step(tuple(v * m.DEFAULTS["gyro_dps_per_lsb"] for v in r["gyr"]), tuple(r["grav"]), dt)
            if tr is None:
                tr = ot.HeadTracker(raw())
                tr.update(0, 0, 0, 0.01, eng.g, eng.lat, (1, 1, 1), tuple(r["att"]))
                acc, n = [0.0] * 3, 0
                continue
            out = tr.update(y, p, rr, dt, eng.g, eng.lat, (1, 1, 1), tuple(r["att"]))
            acc = [acc[0] + y * dt, acc[1] + p * dt, acc[2] + rr * dt]
            n += 1
            if n == 25:
                res.append((out[3], out[4], out[5], *acc))
                tr = None
        for k in range(3):
            pairs = [(q[k], q[k + 3]) for q in res if abs(q[k + 3]) > 1.0]
            self.assertGreater(len(pairs), 50)
            sxy = sum(a * b for a, b in pairs)
            slope = sxy / sum(b * b for _, b in pairs)
            ma, mb = sum(a for a, _ in pairs) / len(pairs), sum(b for _, b in pairs) / len(pairs)
            cov = sum((a - ma) * (b - mb) for a, b in pairs)
            corr = cov / math.sqrt(sum((a - ma) ** 2 for a, _ in pairs) * sum((b - mb) ** 2 for _, b in pairs))
            self.assertGreater(corr, 0.9, k)
            self.assertTrue(0.6 < slope < 1.2, (k, slope))


# --------------------------------------------------------------------------- manette virtuelle
class FakeUInput:
    def __init__(self, caps, name):
        self.caps, self.name, self.events, self.syns, self.closed = caps, name, [], 0, False

    def write(self, etype, code, value):
        self.events.append((code, value))

    def syn(self):
        self.syns += 1

    def close(self):
        self.closed = True


try:
    import evdev  # noqa: F401
    import sim_joystick as sj
except ImportError:
    sj = None


@unittest.skipIf(sj is None, "python-evdev absent")
class TestJoystick(unittest.TestCase):
    def test_axis_mapping_like_opentrack_libevdev(self):
        self.assertEqual(sj.axis_value(0.0, 180.0), 32767)
        self.assertEqual(sj.axis_value(180.0, 180.0), 65534)
        self.assertEqual(sj.axis_value(-180.0, 180.0), 0)
        self.assertEqual(sj.axis_value(500.0, 180.0), 65535)          # borné
        self.assertEqual(sj.axis_value(float("nan"), 90.0), 32767)
        ax = sj.pose_to_axes((0, 0, 0, 90.0, -45.0, 0.0), {})
        self.assertAlmostEqual(ax["ABS_RX"], 49151, delta=1)            # lacet +90° sur ±180°
        self.assertAlmostEqual(ax["ABS_RY"], 16384, delta=1)            # tangage −45° sur ±90°
        self.assertEqual(ax["ABS_X"], 32767)

    def test_device_writes_only_changes(self):
        from evdev import ecodes as e
        holder = {}
        js = sj.VirtualJoystick({"full_yaw_deg": 90.0}, device_factory=lambda c, n: holder.setdefault("d", FakeUInput(c, n)))
        d = holder["d"]
        self.assertIn(e.BTN_TRIGGER, d.caps[e.EV_KEY])
        self.assertEqual({c for c, _ in d.caps[e.EV_ABS]}, {e.ABS_X, e.ABS_Y, e.ABS_Z, e.ABS_RX, e.ABS_RY, e.ABS_RZ})
        self.assertTrue(js.write((0, 0, 0, 45.0, 0, 0)))
        self.assertIn((e.ABS_RX, sj.axis_value(90.0, 180.0)), d.events)        # 45° sur ±90° = 3/4
        n = len(d.events)
        self.assertFalse(js.write((0, 0, 0, 45.0, 0, 0)))               # rien n'a changé : pas d'événement
        self.assertEqual(len(d.events), n)
        js.close()
        self.assertTrue(d.closed)

    def test_sanitize(self):
        self.assertEqual(sj.sanitize({"enabled": 1, "full_yaw_deg": "1000", "x": 2, "full_pitch_deg": "a"}),
                         {"enabled": True, "full_yaw_deg": 180.0})

    def test_output_feeds_joystick_without_udp(self):
        out = ot.OpenTrackOutput(raw(), udp=False)
        out.sender.sock.close()
        out.sender.sock = FakeSock()
        holder = {}
        out.set_joystick({}, factory=lambda cfg: sj.VirtualJoystick(cfg, device_factory=lambda c, n: holder.setdefault("d", FakeUInput(c, n))))
        out.feed(30.0, 0, 0, 0.3, None, None, (1, 1, 1))
        out.tick(1.0)
        self.assertEqual(out.sender.sock.sent, [])                     # UDP coupé
        rx = [v for c, v in holder["d"].events if c == __import__("evdev").ecodes.ABS_RX]
        self.assertEqual(rx[-1], sj.axis_value(9.0, 180.0))
        self.assertEqual(out.status()["outputs"], ["joystick"])
        out.set_joystick(None)
        self.assertTrue(holder["d"].closed)


class TestController(unittest.TestCase):
    def make(self, **ot_cfg):
        cfg = m.load_config("/nonexistent/config.json")
        cfg["opentrack"].update(ot_cfg)
        cfg["notifications"] = False
        cfg["mouse_paused"] = False
        ctl = m.Controller(cfg, m.NullPointer())
        return ctl

    def test_defaults_off(self):
        ctl = self.make()
        self.assertFalse(m.DEFAULTS["opentrack"]["enabled"])
        ctl.sync_opentrack()
        self.assertIsNone(ctl.opentrack)
        self.assertFalse(ctl.inactive)

    def test_forced_feeds_while_paused_and_pauses_mouse(self):
        ctl = self.make(port=1)            # port 1 : rien n'écoute, l'envoi UDP ne gêne pas
        ctl.opentrack_forced = True
        ctl.sync_opentrack()
        try:
            self.assertIsNotNone(ctl.opentrack)
            self.assertTrue(ctl.inactive)             # souris figée pendant le jeu
            ctl.set_paused(True)
            fed = []
            ctl.opentrack.feed = lambda *a: fed.append(a)
            blk = bytearray(58)
            struct.pack_into("<hhh", blk, 44, 0, 0, 1000)
            ctl.on_block(bytes(blk), 1.0)
            self.assertEqual(len(fed), 1)            # alimenté même en pause
            ctl.opentrack_forced = False
            ctl.cfg["opentrack"]["enabled"] = False
            ctl.sync_opentrack()
            self.assertIsNone(ctl.opentrack)
        finally:
            if ctl.opentrack:
                ctl.opentrack.stop()

    def test_demo_page_computes_pose_without_any_output(self):
        """/demo : la pose est calculée tant que la page interroge, sans UDP ni manette, puis s'arrête."""
        ctl = self.make(port=1)
        ctl.command("demo")
        try:
            self.assertIsNotNone(ctl.opentrack)
            self.assertEqual(ctl.opentrack.outputs, [])       # rien n'est envoyé nulle part
            ctl.demo_until = time.monotonic() - 1             # la page s'est fermée
            blk = bytearray(58)
            struct.pack_into("<hhh", blk, 44, 0, 0, 1000)
            ctl.on_block(bytes(blk), 1.0)
            self.assertIsNone(ctl.opentrack)
        finally:
            if ctl.opentrack:
                ctl.opentrack.stop()

    def test_recenter_command(self):
        ctl = self.make(port=1)
        ctl.opentrack_forced = True
        ctl.sync_opentrack()
        try:
            ctl.opentrack.feed(100.0, 0, 0, 0.1)
            self.assertNotEqual(ctl.opentrack.pose[3], 0.0)
            ctl.command("recenter")
            self.assertEqual(ctl.opentrack.pose, (0.0,) * 6)
        finally:
            ctl.opentrack.stop()

    def test_feed_passes_quaternion_and_yaw_sign(self):
        ctl = self.make(port=1)
        ctl.opentrack_forced = True
        ctl.sync_opentrack()
        try:
            fed = []
            ctl.opentrack.feed = lambda *a: fed.append(a)
            blk = bytearray(58)
            struct.pack_into("<hhh", blk, 20, 1000, -2000, 3000)
            struct.pack_into("<hhh", blk, 44, 0, 0, 1000)
            ctl.on_block(bytes(blk), 1.0)
            self.assertEqual(fed[0][7], (1000, -2000, 3000))
            self.assertEqual(len(fed[0][6]), 3)
        finally:
            ctl.opentrack.stop()

    @unittest.skipIf(sj is None, "python-evdev absent")
    def test_joystick_only_session(self):
        ctl = self.make()
        ctl.cfg["sim_joystick"]["enabled"] = True
        orig = sj.VirtualJoystick.__init__.__defaults__
        sj.VirtualJoystick.__init__.__defaults__ = (None, lambda c, n: FakeUInput(c, n))
        try:
            ctl.sync_opentrack()
            self.assertIsNotNone(ctl.opentrack)
            self.assertFalse(ctl.opentrack.udp)
            self.assertIsNotNone(ctl.opentrack.joystick)
            self.assertTrue(ctl.inactive)                 # souris figée pendant le jeu (réglage par défaut)
            ctl.cfg["sim_joystick"]["enabled"] = False
            ctl.sync_opentrack()
            self.assertIsNone(ctl.opentrack)
        finally:
            sj.VirtualJoystick.__init__.__defaults__ = orig
            if ctl.opentrack:
                ctl.opentrack.stop()


class TestLoopback(unittest.TestCase):
    def test_udp_packet_received(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2.0)
        port = rx.getsockname()[1]
        out = ot.OpenTrackOutput({"port": port, "rate_hz": 200, "smoothing": 0.0, "drift_correction": False})
        try:
            out.feed(15.0, -5.0, 2.5, 0.4)
            out.start()
            data, _ = rx.recvfrom(1024)
        finally:
            out.stop()
            rx.close()
        self.assertEqual(len(data), 48)
        x, y, z, yaw, pitch, roll = struct.unpack("<6d", data)
        self.assertEqual((x, y, z), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(yaw, 6.0)
        self.assertAlmostEqual(pitch, -2.0)
        self.assertAlmostEqual(roll, 1.0)


if __name__ == "__main__":
    unittest.main()

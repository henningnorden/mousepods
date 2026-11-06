# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Tests de la sortie OpenTrack : paquet, cadence, recentrage, gains, intégration, UDP local.

Lancer :  python -m unittest tests.test_opentrack -v
"""
import math
import os
import socket
import struct
import sys
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


class TestController(unittest.TestCase):
    def make(self, **ot_cfg):
        cfg = m.load_config("/nonexistent/config.json")
        cfg["opentrack"].update(ot_cfg)
        cfg["notifications"] = False
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

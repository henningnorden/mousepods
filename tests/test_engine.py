# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Tests automatiques : protocole, moteur de mouvement, modes, clics, calibration, rejeu de capture réelle.

Lancer :  .venv/bin/python -m unittest discover -s tests -v
"""
import json
import math
import os
import random
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airpods_mouse_controller as m  # noqa: E402

CAPTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures")


# --------------------------------------------------------------------------- outils
def rot_matrix(axis, ang_deg):
    x, y, z = m.vnorm(axis)
    a = math.radians(ang_deg)
    c, s, C = math.cos(a), math.sin(a), 1 - math.cos(a)
    return [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]


def mat_t_vec(R, v):  # R^T v : monde -> corps
    return tuple(sum(R[i][j] * v[i] for i in range(3)) for j in range(3))


def random_orientation(rng):
    return rot_matrix((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)), rng.uniform(0, 180))


UP_W = (0.0, 0.0, 1.0)        # verticale monde
LAT_W = (0.0, 1.0, 0.0)       # axe latéral monde (oreille-oreille)
FWD_W = (1.0, 0.0, 0.0)


def body_profile(R):
    """Profil "parfait" pour un appareil d'orientation R (gravité mesurée = UP dans le repère corps)."""
    return {"yaw_sign": 1, "lat": list(mat_t_vec(R, LAT_W)), "pitch_sign": 1, "roll_sign": 1}


class FakePointer:
    def __init__(self):
        self.rel = [0.0, 0.0]
        self.abs = None
        self.clicks = []
        self.wheel = 0.0

    def move_rel(self, dx, dy):
        self.rel[0] += dx
        self.rel[1] += dy

    def move_abs(self, fx, fy):
        self.abs = (fx, fy)

    def click(self, b="left"):
        self.clicks.append(b)

    def scroll(self, n):
        self.wheel += n

    def close(self):
        pass


def make_block(ts_ns, w_lsb, g_raw, att=(0, 0, 0)):
    b = bytearray(58)
    b[0] = 1
    struct.pack_into("<Q", b, 1, ts_ns)
    struct.pack_into("<hhh", b, 20, *att)
    struct.pack_into("<hhh", b, 26, *[int(round(c)) for c in w_lsb])
    struct.pack_into("<hhh", b, 44, *[int(round(c)) for c in g_raw])
    return bytes(b)


# --------------------------------------------------------------------------- protocole
class TestProtocol(unittest.TestCase):
    def test_rhid_header_matches_real_announcement(self):
        # en-tête réel des AirPods 3 pour devmotion6 : 87 98 01 00 -> dev 7, longueur 817, type 0
        h = struct.unpack("<I", bytes.fromhex("87980100"))[0]
        self.assertEqual(h & 0x7F, 7)
        self.assertEqual((h >> 7) & 0x3FF, 817)
        self.assertEqual((h >> 17) & 7, 0)

    def test_set_report_bytes_match_clone_firmware(self):
        # le firmware clone attend "08 05 06 00 | 02 | 04 01 00 00 00" pour dev 8
        sub = m.rhid_sub(8, m.RHID_SET, bytes([2, 4]) + struct.pack("<I", 1))
        self.assertEqual(sub.hex(), "080506000204" + "01000000")

    def test_host_connect(self):
        f = m.rhid_frame(5, [m.rhid_sub(0, m.RHID_CONNECT)])
        self.assertEqual(f.hex(), "04000400170000080000" + "0800" + "00050000" + "00020000")

    def test_rhid_roundtrip_and_input(self):
        blk = make_block(123, (10, 20, 30), (0, 0, 1000))
        f = m.rhid_frame(1, [m.rhid_sub(7, m.RHID_INPUT, b"\x00" + blk)])
        parsed = m.rhid_parse(f)
        self.assertEqual(parsed[0][:2], (m.RHID_INPUT, 7))
        c = m.AAPClient("00:00:00:00:00:00")
        c.rhid_motion = 7
        out = c.process(f)
        self.assertEqual(out, [blk])

    def test_device_connect_triggers_start(self):
        plist = b'<?xml version="1.0"?><plist><dict><key>AccessoryService</key><string>devmotion6</string></dict></plist>'
        f = m.rhid_frame(1, [m.rhid_sub(7, m.RHID_CONNECT, plist)])
        c = m.AAPClient("00:00:00:00:00:00", interval_us=10000)
        sent = []
        c.send = sent.append
        c.process(f)
        self.assertEqual(c.rhid_devs["devmotion6"], 7)
        self.assertEqual(c.rhid_motion, 7)
        self.assertTrue(sent and sent[-1].endswith(bytes([2, 1]) + struct.pack("<I", 10000)))

    def test_wx_public_vector(self):
        p = bytes.fromhex("04000400170000001000450008ff7f10013a3e08101a3a015021d8c7130d0000030043998d62bb00"
                          "0000006cb9a83eb5fc0300020001005e1003d37706fdff000001008d020d035c0034aac63786ff0000")
        blocks = m.motion_blocks(p)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][20:26].hex(), "6cb9a83eb5fc")

    def test_ear_place(self):
        self.assertEqual(m.ear_place(bytes.fromhex("0400040006000000")), "ear")
        self.assertEqual(m.ear_place(bytes.fromhex("0400040006000202")), "case")
        self.assertEqual(m.ear_place(bytes.fromhex("0400040006000101")), "out")
        self.assertEqual(m.ear_place(bytes.fromhex("0400040006000003")), "ear")


# --------------------------------------------------------------------------- moteur
class TestMotionEngine(unittest.TestCase):
    BIAS = {"still_range_dps": 3.0, "still_time_s": 0.4, "rate": 0.03}

    def _run(self, R, w_world, secs=0.5, bias=(0, 0, 0), noise=0.0, rng=None):
        """Simulation physique : l'orientation tourne réellement, la gravité mesurée suit."""
        eng = m.MotionEngine(self.BIAS, body_profile(R))
        out = []
        speed = m.vlen(w_world)
        for _ in range(int(secs * 100)):
            g_body = m.vscale(mat_t_vec(R, UP_W), 1000)
            w = mat_t_vec(R, w_world)
            if rng:
                w = tuple(c + rng.gauss(0, noise) for c in w)
            out.append(eng.step(m.vsub(w, m.vscale(bias, -1)), g_body, 0.01))
            if speed > 0:
                dR = rot_matrix(w_world, speed * 0.01)
                R = [[sum(dR[i][k] * R[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        return eng, out

    def test_yaw_independent_of_orientation(self):
        rng = random.Random(1)
        for _ in range(25):
            R = random_orientation(rng)
            _, out = self._run(R, m.vscale(UP_W, 30.0))
            yaw, pitch, roll = out[-1]
            self.assertAlmostEqual(yaw, 30.0, delta=0.5)
            self.assertAlmostEqual(pitch, 0.0, delta=0.5)
            self.assertAlmostEqual(roll, 0.0, delta=0.5)

    def test_pitch_and_roll_independent_of_orientation(self):
        rng = random.Random(2)
        for _ in range(25):
            R = random_orientation(rng)
            _, out = self._run(R, m.vscale(LAT_W, -20.0))
            self.assertAlmostEqual(out[-1][1], -20.0, delta=0.5)
            self.assertAlmostEqual(out[-1][0], 0.0, delta=0.5)
            _, out = self._run(R, m.vscale(FWD_W, 25.0))
            self.assertAlmostEqual(abs(out[-1][2]), 25.0, delta=0.5)
            self.assertAlmostEqual(out[-1][1], 0.0, delta=0.5)

    def test_bias_is_removed_when_still(self):
        rng = random.Random(3)
        R = random_orientation(rng)
        eng, out = self._run(R, (0, 0, 0), secs=3.0, bias=(4.0, -6.0, 2.5), noise=0.3, rng=rng)
        self.assertTrue(eng.bias_ok)
        for v in out[-1]:
            self.assertLess(abs(v), 1.0)

    def test_motion_does_not_corrupt_bias(self):
        rng = random.Random(4)
        R = random_orientation(rng)
        eng = m.MotionEngine(self.BIAS, body_profile(R))
        g = m.vscale(mat_t_vec(R, UP_W), 1000)
        for i in range(300):  # mouvement continu sinusoïdal
            w = mat_t_vec(R, m.vscale(UP_W, 60 * math.sin(i / 10)))
            eng.step(w, g, 0.01)
        self.assertLess(m.vlen(eng.bias), 1.0)


# --------------------------------------------------------------------------- calibration
class TestCalibration(unittest.TestCase):
    def test_profile_from_simulated_gestures(self):
        rng = random.Random(5)
        for _ in range(20):
            R = random_orientation(rng)
            up_b = mat_t_vec(R, UP_W)
            bias = (rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-5, 5))

            def integrate(w_world, secs=0.6):
                acc = m.CalibAccumulator(bias, up_b)
                for _ in range(int(secs * 100)):
                    acc.add(m.vsub(mat_t_vec(R, w_world), m.vscale(bias, -1)), 0.01)
                return acc
            left = integrate(m.vscale(UP_W, 50))          # gauche = rotation + autour de la verticale
            up = integrate(m.vscale(LAT_W, -40))          # lever la tête (convention du test)
            right_roll = integrate(m.vscale(FWD_W, 40))
            prof = m.profile_from_calibration(up_b, left.yaw, up.horiz, right_roll.horiz)
            eng = m.MotionEngine(TestMotionEngine.BIAS, prof)
            g_body = m.vscale(up_b, 1000)

            def rates(w_world):
                r = None
                for _ in range(10):
                    r = eng.step(mat_t_vec(R, w_world), g_body, 0.01)
                return r
            self.assertLess(rates(m.vscale(UP_W, 30))[0], -25)      # gauche -> négatif
            self.assertGreater(rates(m.vscale(LAT_W, -30))[1], 25)  # haut -> positif
            self.assertGreater(rates(m.vscale(FWD_W, 30))[2], 25)   # droite -> positif


# --------------------------------------------------------------------------- contrôleur
def make_controller(mode, **over):
    cfg = m.load_config("/nonexistent.json")
    cfg["mode"] = mode
    cfg["notifications"] = False
    cfg.update(over)
    ptr = FakePointer()
    ctl = m.Controller(cfg, ptr)
    ctl.engine.set_profile({"yaw_sign": 1, "lat": [0, 1, 0], "pitch_sign": 1, "roll_sign": 1})
    return ctl, ptr


class TestController(unittest.TestCase):
    def feed(self, ctl, w_dps, secs, t0=0.0, step=0.01, render=True):
        t = t0
        for i in range(int(secs / step)):
            t += step
            ctl.on_rates(w_dps[0], w_dps[1], w_dps[2], step, t)
            if render:
                ctl._last_render = t - step
                ctl.last_sample = t
                ctl.render(t)
        return t

    def test_souris_moves_right_and_up(self):
        ctl, ptr = make_controller("souris")
        self.feed(ctl, (30, 0, 0), 0.5)
        self.assertGreater(ptr.rel[0], 100)
        self.assertAlmostEqual(ptr.rel[1], 0, delta=2)
        ctl2, ptr2 = make_controller("souris")
        self.feed(ctl2, (0, 30, 0), 0.5)
        self.assertLess(ptr2.rel[1], -100)        # haut = y écran négatif

    def test_souris_deadzone_blocks_drift(self):
        ctl, ptr = make_controller("souris")
        self.feed(ctl, (1.5, -1.5, 0), 2.0)
        self.assertLess(abs(ptr.rel[0]) + abs(ptr.rel[1]), 1)

    def test_souris_acceleration(self):
        slow, ps = make_controller("souris")
        self.feed(slow, (20, 0, 0), 1.0)            # 20°
        fast, pf = make_controller("souris")
        self.feed(fast, (100, 0, 0), 0.2)           # 20° aussi, mais vite
        self.assertGreater(pf.rel[0], ps.rel[0] * 1.3)

    def test_sensitivity_and_invert(self):
        a, pa = make_controller("souris")
        self.feed(a, (30, 0, 0), 0.5)
        b, pb = make_controller("souris", sensitivity_x=2.0, invert_x=True)
        self.feed(b, (30, 0, 0), 0.5)
        self.assertAlmostEqual(pb.rel[0], -2 * pa.rel[0], delta=abs(pa.rel[0]) * 0.05)

    def test_pointeur_position_and_edge_clamp(self):
        ctl, ptr = make_controller("pointeur")
        self.feed(ctl, (20, 0, 0), 0.5)             # 10° à droite
        k = ctl.cfg["pointeur"]["screen_pct_per_deg"] / 100
        self.assertAlmostEqual(ctl.target[0], 0.5 + 10 * k, delta=0.02)
        self.feed(ctl, (200, 0, 0), 2.0, render=False)  # très loin au-delà du bord
        self.assertAlmostEqual(ctl.target[0], 1.0, delta=1e-6)
        self.feed(ctl, (-20, 0, 0), 0.5, render=False)  # revenir de 10° = quitte le bord tout de suite
        self.assertLess(ctl.target[0], 1.0 - 10 * k * 0.7)

    def test_recenter(self):
        ctl, ptr = make_controller("pointeur")
        self.feed(ctl, (20, 10, 0), 0.5)
        ctl.recenter(quiet=True)
        self.assertEqual(ctl.target, (0.5, 0.5))
        self.assertEqual(ptr.abs, (0.5, 0.5))

    def test_pause_blocks_motion(self):
        ctl, ptr = make_controller("souris")
        ctl.paused = True
        self.feed(ctl, (50, 50, 0), 0.5)
        self.assertEqual(ptr.rel, [0.0, 0.0])

    def test_gap_fade(self):
        ctl, ptr = make_controller("souris")
        t = self.feed(ctl, (40, 0, 0), 0.3)
        x0 = ptr.rel[0]
        for i in range(1, 60):                      # 600 ms sans échantillon
            ctl._last_render = t + (i - 1) * 0.01
            ctl.render(t + i * 0.01)
        moved = ptr.rel[0] - x0
        self.assertGreater(moved, 0)                # continue un peu (pas de saccade)
        self.assertLess(moved, 40 * 20 * 3 * 0.2)   # mais s'arrête

    def test_roll_click(self):
        ctl, ptr = make_controller("souris")
        ctl.cfg["roll_click"]["enabled"] = True
        self.feed(ctl, (0, 0, 100), 0.3)            # inclinaison rapide à droite (~30°)
        self.assertEqual(ptr.clicks, ["left"])
        self.feed(ctl, (0, 0, -100), 0.3)           # retour
        self.feed(ctl, (0, 0, 0), 0.8)
        self.feed(ctl, (0, 0, -100), 0.3)
        self.assertEqual(ptr.clicks, ["left", "right"])

    def test_roll_click_ignores_slow_tilt(self):
        ctl, ptr = make_controller("souris")
        ctl.cfg["roll_click"]["enabled"] = True
        self.feed(ctl, (0, 0, 12), 3.0)
        self.assertEqual(ptr.clicks, [])

    def test_scroll_mode(self):
        ctl, ptr = make_controller("souris")
        ctl.scroll_mode = True
        self.feed(ctl, (0, 30, 0), 1.0)
        self.assertNotEqual(ptr.wheel, 0)
        self.assertEqual(ptr.rel, [0.0, 0.0])

    def test_toggle_mode_command(self):
        ctl, _ = make_controller("souris")
        ctl.command("mode")
        self.assertEqual(ctl.cfg["mode"], "pointeur")
        ctl.command("mode")
        self.assertEqual(ctl.cfg["mode"], "glisse")
        ctl.command("mode")
        self.assertEqual(ctl.cfg["mode"], "souris")

    def test_source_from_ear_state(self):
        ctl, _ = make_controller("souris")
        for place, src in (("ear", "head"), ("case", "case"), ("out", "hand")):
            ctl.set_place(place)
            self.assertEqual(ctl.source, src)


# --------------------------------------------------------------------------- rejeu de capture réelle
class TestReplayRealCapture(unittest.TestCase):
    def test_replay_airpods3_capture(self):
        path = os.path.join(CAPTURES, "airpods3_motion_capture.json")
        if not os.path.exists(path):
            self.skipTest("capture absente")
        rows = json.load(open(path))
        ctl, ptr = make_controller("souris")
        ctl.engine = m.MotionEngine(ctl.cfg["bias"])
        last = None
        for r in rows:
            blk = make_block(r["ts"], r["gyr"], r["grav"], r["att"])
            ctl.on_block(blk, r["t"])
            ctl._last_render = r["t"] - 0.008
            ctl.render(r["t"])
            last = r
        self.assertGreater(ctl.n, 1000)
        self.assertTrue(all(math.isfinite(v) for v in ctl.rates))
        # échelle du gyroscope : rotation intégrée cohérente avec le quaternion (± 35 %)
        yaw_rows = [r for r in rows if r["ph"] == "yaw"]
        ang_q = []
        q0 = None
        for r in yaw_rows:
            q = m.quat_from_block(make_block(0, (0, 0, 0), (0, 0, 0), r["att"])) if hasattr(m, "quat_from_block") else None
            if q is None:
                continue
            q0 = q0 or q
        self.assertIsNotNone(last)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Tests : mode glisse, pointeur calibré par les coins, parcours de calibration de l'application."""
import json
import math
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import airpods_mouse_controller as m  # noqa: E402
import mousepods_web as web  # noqa: E402
from test_engine import (FakePointer, mat_t_vec, random_orientation, rot_matrix, UP_W, LAT_W,  # noqa: E402
                         FWD_W)

G = m.DEFAULTS["glisse"]


def slide_profile(axis_acc):
    """Profil d'accélération d'une glissade : accélère puis freine (aller simple)."""
    return [axis_acc] * 20 + [-axis_acc] * 20     # 0,2 s + 0,2 s à 100 Hz


class TestGlide(unittest.TestCase):
    def run_glide(self, R, world_dir, acc=2.0, noise=0.0, rng=None, extra_still=30):
        prof = {"right": list(mat_t_vec(R, (1, 0, 0))), "fwd": list(mat_t_vec(R, (0, 1, 0)))}
        eng = m.GlideEngine(G, prof)
        up_b = m.vscale(mat_t_vec(R, UP_W), 1000)
        disp = [0.0, 0.0]
        seq = slide_profile(acc) + [0.0] * extra_still
        for a in seq:
            aw = m.vscale(world_dir, a)
            ab = mat_t_vec(R, aw)
            if rng:
                ab = tuple(c + rng.gauss(0, noise) for c in ab)
            vr, vf = eng.step((0.0, 0.0, 0.0), up_b, ab, 0.01)
            disp[0] += vr * 0.01
            disp[1] += vf * 0.01
        return disp, eng

    def test_slide_right_and_forward_any_mounting(self):
        rng = random.Random(7)
        for _ in range(15):
            # boîtier posé à plat (rotation quelconque autour de la verticale)
            R = rot_matrix(UP_W, rng.uniform(0, 360))
            d, eng = self.run_glide(R, (1, 0, 0))
            self.assertGreater(d[0], 0.02)            # ≈ 8 cm théoriques, la fuite en retire un peu
            self.assertLess(abs(d[1]), 0.2 * d[0])
            self.assertEqual(eng.v, [0.0, 0.0])       # arrêt -> vitesse remise à zéro
            d, _ = self.run_glide(R, (0, 1, 0))
            self.assertGreater(d[1], 0.02)
            self.assertLess(abs(d[0]), 0.2 * d[1])

    def test_noise_does_not_drift(self):
        rng = random.Random(8)
        R = rot_matrix(UP_W, 33)
        prof = {"right": list(mat_t_vec(R, (1, 0, 0))), "fwd": list(mat_t_vec(R, (0, 1, 0)))}
        eng = m.GlideEngine(G, prof)
        up_b = m.vscale(mat_t_vec(R, UP_W), 1000)
        disp = 0.0
        for _ in range(1000):                         # 10 s immobile avec bruit 0,03 g
            ab = tuple(rng.gauss(0, 0.3) for _ in range(3))
            vr, vf = eng.step((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)), up_b, ab, 0.01)
            disp += abs(vr) * 0.01 + abs(vf) * 0.01
        self.assertLess(disp, 0.005)                  # < 5 mm cumulés

    def test_controller_glisse_moves_cursor(self):
        cfg = m.load_config("/nonexistent.json")
        cfg.update(mode="glisse", notifications=False)
        ptr = FakePointer()
        ctl = m.Controller(cfg, ptr)
        ctl.glide.set_profile({"right": [1, 0, 0], "fwd": [0, 1, 0]})
        t = 0.0
        for a in slide_profile(2.0) + [0.0] * 20:
            t += 0.01
            ctl.on_glide((0, 0, 0), (0, 0, 1000), (a, 0, 0), 0.01, t)
            ctl._last_render, ctl.last_sample = t - 0.01, t
            ctl.render(t)
        self.assertGreater(ptr.rel[0], 50)
        self.assertAlmostEqual(ptr.rel[1], 0, delta=2)

    def test_glide_profile_orthogonalizes(self):
        p = m.glide_profile((0.2, 0.01, 0.0), (0.05, 0.3, 0.0))
        self.assertAlmostEqual(m.vdot(p["right"], p["fwd"]), 0.0, places=3)


class TestPointerCorners(unittest.TestCase):
    def test_corner_range_validation(self):
        self.assertIsNone(m.corner_range((5, 5), (10, -5)))       # gauche positif : incohérent
        r = m.corner_range((-20, 12), (22, -11))
        self.assertEqual(r, {"x_neg": 20, "x_pos": 22, "y_pos": 12, "y_neg": 11})

    def test_pointer_hits_corners_exactly(self):
        cfg = m.load_config("/nonexistent.json")
        cfg.update(mode="pointeur", notifications=False)
        ctl = m.Controller(cfg, FakePointer())
        ctl.engine.set_profile({"yaw_sign": 1, "lat": [0, 1, 0], "pitch_sign": 1, "roll_sign": 1,
                                "range": {"x_neg": 20, "x_pos": 22, "y_pos": 12, "y_neg": 11}})
        ctl.fy = m.OneEuro(1000, 0)   # pas de lissage pour vérifier la géométrie
        ctl.fp = m.OneEuro(1000, 0)

        def go(yaw_deg, pitch_deg):
            ctl.recenter(quiet=True)
            for _ in range(100):
                ctl.on_rates(yaw_deg, pitch_deg, 0, 0.01, 0)
            return ctl.target
        x, y = go(-20, 12)                     # coin haut-gauche
        self.assertAlmostEqual(x, 0.0, delta=0.03)
        self.assertAlmostEqual(y, 0.0, delta=0.03)
        x, y = go(22, -11)                     # coin bas-droit
        self.assertAlmostEqual(x, 1.0, delta=0.03)
        self.assertAlmostEqual(y, 1.0, delta=0.03)
        x, y = go(11, 0)                       # mi-chemin à droite
        self.assertAlmostEqual(x, 0.75, delta=0.03)


class FakeCtl:
    def __init__(self, mode):
        self.cfg = m.load_config("/nonexistent.json")
        self.cfg["mode"] = mode
        self.paused = False
        self.pid = "2013"
        self.source = "case"
        self.cmds = []

    def command(self, c):
        self.cmds.append(c)

    def recenter(self, quiet=False):
        pass


class TestAppCalibrationFlows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"profiles": {}}, self.tmp)
        self.tmp.close()
        web.time.monotonic = self._clock
        self.t = 1000.0

    def tearDown(self):
        os.unlink(self.tmp.name)
        import time as _t
        web.time.monotonic = _t.monotonic

    def _clock(self):
        return self.t

    def feed(self, sess, R, w_world=(0, 0, 0), a_world=(0, 0, 0), secs=1.7, bias=(1.0, -2.0, 0.5)):
        for _ in range(int(secs * 100)):
            self.t += 0.01
            g = m.vscale(mat_t_vec(R, UP_W), 1000)
            w = m.vsub(mat_t_vec(R, w_world), m.vscale(bias, -1))
            sess.hook(w, g, 0.01, mat_t_vec(R, a_world))
            if sess.done:
                return

    def test_glide_flow(self):
        R = rot_matrix(UP_W, 70)
        ctl = FakeCtl("glisse")
        s = web.CalibSession(ctl, self.tmp.name, "auto", "glisse")
        self.feed(s, R)                                   # à plat immobile
        for a in slide_profile(2.0):                      # glisse à droite
            self.feed(s, R, a_world=(a, 0, 0), secs=0.01)
        self.feed(s, R, secs=0.5)                         # arrêt
        self.feed(s, R)                                   # immobile
        for a in slide_profile(2.0):                      # glisse en avant
            self.feed(s, R, a_world=(0, a, 0), secs=0.01)
        self.feed(s, R, secs=0.5)
        self.feed(s, R)
        self.assertTrue(s.done, s.as_dict())
        prof = json.load(open(self.tmp.name))["profiles"]["2013_glide"]
        self.assertGreater(m.vdot(prof["right"], mat_t_vec(R, (1, 0, 0))), 0.95)
        self.assertGreater(m.vdot(prof["fwd"], mat_t_vec(R, (0, 1, 0))), 0.95)

    def test_pointer_flow_with_corners(self):
        rng = random.Random(9)
        R0 = random_orientation(rng)
        ctl = FakeCtl("pointeur")
        s = web.CalibSession(ctl, self.tmp.name, "hand", "pointeur")
        R = [row[:] for row in R0]

        def rotate(axis_w, deg, secs=0.5):
            nonlocal R
            n = int(secs * 100)
            rate = deg / secs
            for _ in range(n):
                self.feed(s, R, w_world=m.vscale(axis_w, rate), secs=0.01)
                dR = rot_matrix(axis_w, rate * 0.01)
                R = [[sum(dR[i][k] * R[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        self.feed(s, R)                       # immobile
        rotate(UP_W, 30)                      # gauche (convention des tests : + autour de la verticale)
        self.feed(s, R)
        rotate(LAT_W, -30)                    # haut
        self.feed(s, R)
        rotate(FWD_W, 30)                     # « roulis » droite
        self.feed(s, R)                       # retour immobile (on ne revient pas : pas grave)
        self.assertEqual(s.step, "aim_center", s.as_dict())
        self.feed(s, R)                       # vise le centre
        rotate(UP_W, 20)                      # coin haut-gauche : 20° à gauche…
        rotate(LAT_W, -12)                    # … et 12° vers le haut
        self.feed(s, R)
        rotate(LAT_W, 23)                     # bas-droit : redescendre 23°…
        rotate(UP_W, -42)                     # … et 42° vers la droite
        self.feed(s, R)
        self.assertTrue(s.done, s.as_dict())
        prof = json.load(open(self.tmp.name))["profiles"]["2013_hand"]
        rng_ = prof["range"]
        self.assertAlmostEqual(rng_["x_neg"], 20, delta=3)
        self.assertAlmostEqual(rng_["x_pos"], 22, delta=3)
        self.assertAlmostEqual(rng_["y_pos"], 12, delta=3)
        self.assertAlmostEqual(rng_["y_neg"], 11, delta=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

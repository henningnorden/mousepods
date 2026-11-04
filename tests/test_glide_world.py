# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Tests : glisse dans le repère de la table (quaternion) + correction rétroactive de fin de geste."""
import copy
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airpods_mouse_controller as m  # noqa: E402


def quat_axis(axis, deg):
    x, y, z = m.vnorm(axis)
    h = math.radians(deg) / 2
    return (math.cos(h), x * math.sin(h), y * math.sin(h), z * math.sin(h))


def qconj(q):
    return (q[0], -q[1], -q[2], -q[3])


def slide(engine, accel_world, yaw_spin_dps=0.0, bias_body=(0, 0, 0), secs_acc=0.25):
    """Glissade le long de accel_world (accélère puis freine), l'écouteur pivotant pendant le geste."""
    seq = [1.0] * int(secs_acc * 100) + [-1.0] * int(secs_acc * 100) + [0.0] * 40
    yaw = 0.0
    pos = [0.0, 0.0]
    for s in seq:
        q = quat_axis((0, 0, 1), yaw)                       # corps -> monde
        a_body = m.qrot(qconj(q), m.vscale(accel_world, s))  # ce que mesure l'écouteur
        a_body = m.vsub(a_body, m.vscale(bias_body, -1))
        g_body = m.qrot(qconj(q), (0, 0, 1000.0))
        w = (0.0, 0.0, yaw_spin_dps if s else 0.0)
        vr, vf = engine.step(w, g_body, a_body, 0.01, q)
        c = engine.take_correction()
        pos[0] += vr * 0.01 + c[0]
        pos[1] += vf * 0.01 + c[1]
        if s:
            yaw += yaw_spin_dps * 0.01
    return pos


class TestGlideWorldFrame(unittest.TestCase):
    def cfg(self, **kw):
        c = copy.deepcopy(m.DEFAULTS["glisse"])
        c.update(kw)
        return c

    def test_rotation_during_slide_keeps_direction(self):
        eng = m.GlideEngine(self.cfg(), {"right": [1, 0, 0], "fwd": [0, 1, 0]})
        slide(eng, (0, 0, 0))                                # initialise les axes à l'arrêt
        pos = slide(eng, (2.0, 0, 0), yaw_spin_dps=120)      # tourne de 60° pendant le geste
        self.assertGreater(pos[0], 0.05)
        self.assertLess(abs(pos[1]), 0.15 * pos[0])         # direction conservée

    def test_body_frame_would_fail(self):
        eng = m.GlideEngine(self.cfg(), {"right": [1, 0, 0], "fwd": [0, 1, 0]})
        seq = [1.0] * 25 + [-1.0] * 25 + [0.0] * 40
        yaw, pos = 0.0, [0.0, 0.0]
        for s in seq:                                        # même geste, sans quaternion
            q = quat_axis((0, 0, 1), yaw)
            a_body = m.qrot(qconj(q), (2.0 * s, 0, 0))
            vr, vf = eng.step((0, 0, 120 if s else 0), (0, 0, 1000.0), a_body, 0.01, None)
            c = eng.take_correction()
            pos[0] += vr * 0.01 + c[0]
            pos[1] += vf * 0.01 + c[1]
            if s:
                yaw += 1.2
        self.assertGreater(abs(pos[1]), 0.2 * abs(pos[0]))  # sans repère monde : direction faussée

    def test_retroactive_correction_reduces_bias_error(self):
        truth = slide(m.GlideEngine(self.cfg(accel_deadzone=0.0), {}), (2.0, 0, 0))
        biased_corr = slide(m.GlideEngine(self.cfg(accel_deadzone=0.0), {}), (2.0, 0, 0),
                            bias_body=(0.3, 0, 0))
        eng = m.GlideEngine(self.cfg(accel_deadzone=0.0), {})
        eng.take_correction = lambda: [0.0, 0.0]             # même chose sans correction
        biased_raw = slide(eng, (2.0, 0, 0), bias_body=(0.3, 0, 0))
        err_corr = abs(biased_corr[0] - truth[0])
        err_raw = abs(biased_raw[0] - truth[0])
        self.assertLess(err_corr, err_raw * 0.6)


if __name__ == "__main__":
    unittest.main(verbosity=2)

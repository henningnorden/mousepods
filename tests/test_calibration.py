# SPDX-License-Identifier: Apache-2.0
"""Game-profile calibration: hold still at each point, measure the head angle at the edges.

Run:  python -m unittest tests.test_calibration -v
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calibration as cal  # noqa: E402

STEP = 0.01                      # 100 Hz, like the AirPods


class Feeder:
    def __init__(self, c):
        self.c, self.t = c, 0.0

    def hold(self, yaw, pitch, secs, noise=None):
        for i in range(int(round(secs / STEP))):
            self.t += STEP
            dy, dp = noise(self.t) if noise else (0.0, 0.0)
            self.c.feed(self.t, yaw + dy, pitch + dp)
            self.c.tick(self.t)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.centered = 0
        self.c = cal.Calibration(on_center=self.on_center)
        self.f = Feeder(self.c)

    def on_center(self):
        self.centered += 1

    def test_full_run(self):
        self.f.hold(0, 0, 2.1)
        self.assertEqual(self.c.state()["step"], "left")
        for yaw, pitch in ((-20, 0), (30, 0), (0, 12), (0, -9)):
            self.f.hold(yaw, pitch, 2.1)
        st = self.c.state()
        self.assertEqual(st["step"], "done")
        r = st["result"]
        self.assertAlmostEqual(r["yaw"]["neg"], 20, places=1)
        self.assertAlmostEqual(r["yaw"]["pos"], 30, places=1)
        self.assertAlmostEqual(r["pitch"]["pos"], 12, places=1)
        self.assertAlmostEqual(r["pitch"]["neg"], 9, places=1)
        self.assertEqual(self.centered, 1)

    def test_progress_rises_and_resets_on_movement(self):
        self.f.hold(0, 0, 1.0)
        self.assertAlmostEqual(self.c.state()["progress"], 0.5, delta=0.02)
        self.f.hold(3, 0, 0.02)
        self.assertLess(self.c.state()["progress"], 0.05)
        self.assertEqual(self.c.state()["step"], "center")

    def test_jitter_never_captures(self):
        self.f.hold(0, 0, 2.5, noise=lambda t: (0.5 * math.sin(t * 40), 0.4 * math.cos(t * 33)))
        self.assertEqual(self.c.state()["step"], "left")          # small noise is still "still"
        self.f.hold(-20, 0, 10.0, noise=lambda t: (2.0 * math.sin(t * 6), 0.0))
        self.assertEqual(self.c.state()["step"], "left")
        self.assertIsNone(self.c.state()["hint"])

    def test_too_close_and_wrong_side(self):
        self.f.hold(0, 0, 2.1)
        self.f.hold(-3, 0, 2.1)
        self.assertEqual((self.c.state()["step"], self.c.state()["hint"]), ("left", "too_close"))
        self.f.hold(10, 0, 2.1)
        self.assertEqual((self.c.state()["step"], self.c.state()["hint"]), ("left", "wrong_side"))
        self.f.hold(-15, 0, 2.1)
        self.assertEqual((self.c.state()["step"], self.c.state()["hint"]), ("right", None))

    def test_no_signal_fails(self):
        self.f.hold(0, 0, 1.0)
        self.c.tick(self.f.t + 3.1)
        st = self.c.state()
        self.assertEqual((st["step"], st["hint"]), ("failed", "no_signal"))
        self.assertTrue(self.c.finished)

    def test_cancel(self):
        self.f.hold(0, 0, 1.0)
        self.c.cancel()
        self.f.hold(0, 0, 3.0)
        st = self.c.state()
        self.assertEqual(st["step"], "cancelled")
        self.assertIsNone(st["result"])
        self.assertTrue(self.c.finished)

    def test_state_before_any_data(self):
        st = self.c.state()
        self.assertEqual((st["step"], st["progress"], st["hint"], st["result"]), ("center", 0.0, None, None))
        self.assertFalse(self.c.finished)


if __name__ == "__main__":
    unittest.main()

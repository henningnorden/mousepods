# SPDX-License-Identifier: Apache-2.0
"""Per-game profile maths: curves, smoothing, neck model.

Run:  python -m unittest tests.test_game_profiles -v
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import game_profiles as gp  # noqa: E402

LINEAR = {"enabled": True, "invert": False, "gain": 1.0, "curve": 1.0, "limit": 180}


def flat_profile(smoothing):
    p = gp.normalize_profile({"smoothing": smoothing})
    for a in ("yaw", "pitch", "roll"):
        p["axes"][a] = dict(LINEAR)
    return p


class ProfileTests(unittest.TestCase):
    def test_defaults_and_clamping(self):
        p = gp.normalize_profile(None)
        self.assertEqual(p, gp.DEFAULT_PROFILE)
        self.assertIsNot(p, gp.DEFAULT_PROFILE)
        q = gp.normalize_profile({"smoothing": 5, "axes": {"yaw": {"gain": 99, "curve": 0.2, "limit": -3}},
                                  "neck": {"strength": 7}, "junk": 1})
        self.assertEqual(q["smoothing"], 1.0)
        self.assertEqual((q["axes"]["yaw"]["gain"], q["axes"]["yaw"]["curve"], q["axes"]["yaw"]["limit"]),
                         (6.0, 1.0, 0.0))
        self.assertEqual(q["axes"]["pitch"], gp.DEFAULT_PROFILE["axes"]["pitch"])
        self.assertEqual(q["neck"]["strength"], 2.0)
        self.assertNotIn("junk", q)

    def test_map_axis_center_is_flat_with_curve(self):
        axis = {"enabled": True, "invert": False, "gain": 2.0, "curve": 1.4, "limit": 180}
        self.assertLess(abs(gp.map_axis(1.0, axis)), 2.0 * 1.0)
        self.assertEqual(gp.map_axis(0.0, axis), 0.0)

    def test_map_axis_reaches_gain_at_40_deg(self):
        axis = {"enabled": True, "invert": False, "gain": 2.0, "curve": 1.4, "limit": 180}
        self.assertAlmostEqual(gp.map_axis(40.0, axis), 80.0)
        self.assertAlmostEqual(gp.map_axis(-40.0, axis), -80.0)

    def test_map_axis_limit_and_invert_and_disabled(self):
        axis = {"enabled": True, "invert": True, "gain": 3.0, "curve": 1.0, "limit": 90}
        self.assertEqual(gp.map_axis(60.0, axis), -90.0)
        axis["enabled"] = False
        self.assertEqual(gp.map_axis(60.0, axis), 0.0)

    def test_neck_zero_at_center_and_moves_sideways_on_yaw(self):
        self.assertEqual(gp.neck_offset(0, 0, 0, 1.0), (0.0, 0.0, 0.0))
        x, y, z = gp.neck_offset(30, 0, 0, 1.0)
        self.assertGreater(x, 1.0)
        self.assertAlmostEqual(y, 0.0)
        x2, _, _ = gp.neck_offset(30, 0, 0, 2.0)
        self.assertAlmostEqual(x2, 2 * x)

    def test_neck_looking_down_moves_eyes_down(self):
        _, y, _ = gp.neck_offset(0, -30, 0, 1.0)
        self.assertLess(y, 0.0)

    def test_filter_smoothing_off_is_identity_before_mapping(self):
        f = gp.ProfileFilter(flat_profile(0.0))
        self.assertEqual(f.apply((0, 0, 0, 12.5, -3.0, 1.5), 1 / 120), (0.0, 0.0, 0.0, 12.5, -3.0, 1.5))

    def test_filter_reduces_jitter(self):
        f = gp.ProfileFilter(flat_profile(0.6))
        out = [f.apply((0, 0, 0, 0.5 if i % 2 else -0.5, 0, 0), 1 / 120)[3] for i in range(240)]
        tail = out[-40:]
        self.assertLess(max(tail) - min(tail), 0.5)

    def test_filter_neck_adds_translation(self):
        p = flat_profile(0.0)
        p["neck"] = {"enabled": True, "strength": 1.0}
        out = gp.ProfileFilter(p).apply((0, 0, 0, 30, 0, 0), 1 / 120)
        self.assertGreater(abs(out[0]), 1.0)

    def test_filter_neck_in_game_axes(self):
        """The DLLs negate yaw but pass X through (as opentrack's wine wrapper does), so in the
        game's frame X + is left: looking right must give a negative X, i.e. eyes to the right.
        Found in Farming Simulator 22: with X + = right the head slid away from the look direction."""
        p = flat_profile(0.0)
        p["neck"] = {"enabled": True, "strength": 1.0}
        x, _, z, *_ = gp.ProfileFilter(p).apply((0, 0, 0, 30, 0, 0), 1 / 120)
        self.assertLess(x, -1.0)
        self.assertLess(z, 0.0)                       # unchanged: eyes come back a little

    def test_filter_neck_per_game_inversion(self):
        p = flat_profile(0.0)
        p["neck"] = {"enabled": True, "strength": 1.0}
        x, _, z, *_ = gp.ProfileFilter(p).apply((0, 0, 0, 30, 0, 0), 1 / 120)
        p["neck"].update(invert_x=True, invert_z=True)
        xi, _, zi, *_ = gp.ProfileFilter(p).apply((0, 0, 0, 30, 0, 0), 1 / 120)
        self.assertAlmostEqual(xi, -x)
        self.assertAlmostEqual(zi, -z)

    def test_neck_inversion_flags_normalized(self):
        q = gp.normalize_profile({"neck": {"invert_x": 1, "invert_z": "yes"}})
        self.assertIs(q["neck"]["invert_x"], True)
        self.assertIs(q["neck"]["invert_z"], True)
        self.assertFalse(gp.normalize_profile({})["neck"]["invert_x"])

    def test_filter_is_deterministic(self):
        seq = [(0, 0, 0, i * 0.7 % 13, (i * 1.3) % 5, 0) for i in range(100)]
        a, b = gp.ProfileFilter(gp.DEFAULT_PROFILE), gp.ProfileFilter(copy.deepcopy(gp.DEFAULT_PROFILE))
        self.assertEqual([a.apply(s, 1 / 120) for s in seq], [b.apply(s, 1 / 120) for s in seq])


if __name__ == "__main__":
    unittest.main()

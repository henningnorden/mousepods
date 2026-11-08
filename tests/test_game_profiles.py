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

LINEAR = {"enabled": True, "invert": False, "edge_neg": 90.0, "edge_pos": 90.0, "target": 90.0, "curve": 1.0}


def old_map(d, gain, curve, limit):
    """The model before calibration: gain at a fixed 40 degree pivot, capped at limit."""
    o = min(gain * 40 * (abs(d) / 40) ** curve, limit)
    return o if d >= 0 else -o


def flat_profile(smoothing):
    p = gp.normalize_profile({"smoothing": smoothing})
    for a in ("yaw", "pitch", "roll"):
        p["axes"][a] = dict(LINEAR)
    return p


class ProfileTests(unittest.TestCase):
    def test_defaults_and_clamping(self):
        p = gp.normalize_profile(None)
        self.assertEqual(p, gp.DEFAULT_PROFILE)
        self.assertIs(p["own"], False)
        self.assertIsNot(p, gp.DEFAULT_PROFILE)
        q = gp.normalize_profile({"smoothing": 5, "axes": {"yaw": {"edge_neg": 500, "edge_pos": 0, "target": 999,
                                                                   "curve": 0.2}},
                                  "neck": {"strength": 7}, "junk": 1})
        self.assertEqual(q["smoothing"], 1.0)
        y = q["axes"]["yaw"]
        self.assertEqual((y["edge_neg"], y["edge_pos"], y["target"], y["curve"]), (180.0, 2.0, 180.0, 1.0))
        self.assertEqual(q["axes"]["pitch"], gp.DEFAULT_PROFILE["axes"]["pitch"])
        self.assertEqual(q["neck"]["strength"], 2.0)
        self.assertNotIn("junk", q)
        self.assertNotIn("gain", y)

    def test_defaults_behave_like_the_old_defaults(self):
        old = {"yaw": (2.0, 1.4, 180), "pitch": (1.5, 1.4, 90), "roll": (1.0, 1.0, 90)}
        for name, (gain, curve, limit) in old.items():
            for d in (-60.0, -20.0, 5.0, 30.0, 45.0):
                self.assertAlmostEqual(gp.map_axis(d, gp.DEFAULT_PROFILE["axes"][name]), old_map(d, gain, curve, limit),
                                       delta=1.0, msg=(name, d))

    def test_legacy_profile_behaves_the_same(self):
        axis = gp.normalize_profile({"axes": {"yaw": {"gain": 2, "curve": 1.4, "limit": 180}}})["axes"]["yaw"]
        for d in (5.0, 20.0, 40.0, 60.0, 100.0, -33.0):
            self.assertAlmostEqual(gp.map_axis(d, axis), old_map(d, 2, 1.4, 180), places=1, msg=d)

    def test_edge_reaches_target(self):
        axis = {"enabled": True, "invert": False, "edge_neg": 10.0, "edge_pos": 25.0, "target": 180.0, "curve": 1.0}
        self.assertAlmostEqual(gp.map_axis(25.0, axis), 180.0)
        self.assertAlmostEqual(gp.map_axis(-10.0, axis), -180.0)
        self.assertAlmostEqual(gp.map_axis(-5.0, axis), -90.0)
        self.assertAlmostEqual(gp.map_axis(50.0, axis), 180.0)
        self.assertEqual(gp.map_axis(0.0, axis), 0.0)

    def test_center_is_calm_with_curve_and_invert_and_disabled(self):
        axis = {"enabled": True, "invert": True, "edge_neg": 40.0, "edge_pos": 40.0, "target": 90.0, "curve": 2.0}
        self.assertAlmostEqual(gp.map_axis(20.0, axis), -22.5)
        self.assertAlmostEqual(gp.map_axis(60.0, axis), -90.0)
        axis["enabled"] = False
        self.assertEqual(gp.map_axis(60.0, axis), 0.0)

    def test_legacy_limit_zero_disables_axis(self):
        axis = gp.normalize_profile({"axes": {"pitch": {"gain": 2, "curve": 1, "limit": 0}}})["axes"]["pitch"]
        self.assertIs(axis["enabled"], False)

    def test_legacy_extremes_clamped(self):
        axis = gp.normalize_profile({"axes": {"yaw": {"gain": 0.1, "curve": 1, "limit": 180}}})["axes"]["yaw"]
        self.assertEqual((axis["edge_neg"], axis["edge_pos"]), (180.0, 180.0))
        linear = gp.normalize_profile({"axes": {"yaw": {"gain": 1, "curve": 1, "limit": 180}}})["axes"]["yaw"]
        self.assertAlmostEqual(gp.map_axis(10.0, linear), 10.0)      # old gain 1 stays 1:1
        broken = gp.normalize_profile({"axes": {"yaw": {"gain": "x", "limit": None}}})["axes"]["yaw"]
        self.assertEqual(broken, gp.DEFAULT_PROFILE["axes"]["yaw"])

    def test_monitors_normalized(self):
        raw = [{"connector": "DP-1", "main": True}, "junk", {"connector": "x" * 100, "main": True}]
        raw += [{"connector": f"HDMI-{i}"} for i in range(8)]
        m = gp.normalize_profile({"monitors": raw})["monitors"]
        self.assertEqual(len(m), 8)
        self.assertEqual(m[0], {"connector": "DP-1", "main": True})
        self.assertEqual(len(m[1]["connector"]), 64)
        self.assertEqual([x["main"] for x in m].count(True), 1)
        self.assertEqual(gp.normalize_profile({"monitors": [{"connector": "A"}, {"connector": "B"}]})["monitors"],
                         [{"connector": "A", "main": True}, {"connector": "B", "main": False}])
        self.assertEqual(gp.normalize_profile({})["monitors"], [])

    def test_baseline_normalized(self):
        self.assertIsNone(gp.normalize_baseline(None))
        self.assertIsNone(gp.normalize_baseline({"yaw": {"neg": "x"}}))
        b = gp.normalize_baseline({"yaw": {"neg": 19, "pos": 500}, "pitch": {"neg": 8, "pos": 1},
                                   "target_side": 999, "target_vert": 60, "monitors": [{"connector": "DP-1"}], "junk": 1})
        self.assertEqual(b, {"yaw": {"neg": 19.0, "pos": 180.0}, "pitch": {"neg": 8.0, "pos": 2.0},
                             "target_side": 180.0, "target_vert": 60.0,
                             "monitors": [{"connector": "DP-1", "main": True}]})

    def test_own_flag(self):
        self.assertFalse(gp.normalize_profile({})["own"])                       # default: follows the baseline
        changed = {"axes": {"yaw": {"edge_neg": 20, "edge_pos": 20, "target": 180, "curve": 1.4}}}
        self.assertTrue(gp.normalize_profile(changed)["own"])                   # saved before: own values
        self.assertTrue(gp.normalize_profile({"axes": {"yaw": {"gain": 3}}})["own"])
        self.assertFalse(gp.normalize_profile({**changed, "own": False})["own"])
        self.assertTrue(gp.normalize_profile({"own": True})["own"])

    def test_effective_profile_uses_baseline_unless_own(self):
        base = gp.normalize_baseline({"yaw": {"neg": 18, "pos": 17}, "pitch": {"neg": 8, "pos": 11},
                                      "target_side": 150, "target_vert": 70})
        p = gp.effective_profile(gp.normalize_profile({}), base)
        y, pi = p["axes"]["yaw"], p["axes"]["pitch"]
        self.assertEqual((y["edge_neg"], y["edge_pos"], y["target"]), (18.0, 17.0, 150.0))
        self.assertEqual((pi["edge_neg"], pi["edge_pos"], pi["target"]), (8.0, 11.0, 70.0))
        self.assertEqual(p["axes"]["roll"], gp.DEFAULT_PROFILE["axes"]["roll"])
        self.assertEqual(y["curve"], gp.DEFAULT_PROFILE["axes"]["yaw"]["curve"])
        own = gp.normalize_profile({"own": True})
        self.assertEqual(gp.effective_profile(own, base), own)
        self.assertEqual(gp.effective_profile(gp.normalize_profile({}), None), gp.normalize_profile({}))

    def test_sensitivity_curve_roundtrip(self):
        self.assertEqual(gp.sensitivity_to_curve(0), 3.0)
        self.assertEqual(gp.sensitivity_to_curve(1), 1.0)
        self.assertAlmostEqual(gp.curve_to_sensitivity(gp.sensitivity_to_curve(0.3)), 0.3)
        self.assertEqual(gp.curve_to_sensitivity(5), 0.0)

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

    def test_filter_neck_lean_forward(self):
        """Per game: when looking sideways the eyes go forward (leaning in past a cab pillar)
        instead of coming back; sideways and up/down stay the same."""
        p = flat_profile(0.0)
        p["neck"] = {"enabled": True, "strength": 1.0}
        x, y, z, *_ = gp.ProfileFilter(p).apply((0, 0, 0, 30, 0, 0), 1 / 120)
        p["neck"]["lean_forward"] = True
        xl, yl, zl, *_ = gp.ProfileFilter(p).apply((0, 0, 0, 30, 0, 0), 1 / 120)
        self.assertGreater(zl, 0.0)
        self.assertAlmostEqual(zl, -z)
        self.assertEqual((xl, yl), (x, y))

    def test_neck_lean_forward_normalized(self):
        self.assertIs(gp.normalize_profile({"neck": {"lean_forward": 1}})["neck"]["lean_forward"], True)
        self.assertFalse(gp.normalize_profile({})["neck"]["lean_forward"])

    def test_filter_is_deterministic(self):
        seq = [(0, 0, 0, i * 0.7 % 13, (i * 1.3) % 5, 0) for i in range(100)]
        a, b = gp.ProfileFilter(gp.DEFAULT_PROFILE), gp.ProfileFilter(copy.deepcopy(gp.DEFAULT_PROFILE))
        self.assertEqual([a.apply(s, 1 / 120) for s in seq], [b.apply(s, 1 / 120) for s in seq])


class LegacyCurveOnlyTests(unittest.TestCase):
    def test_profile_with_only_a_curve_is_converted_like_the_old_model(self):
        """Saved before calibration with just "curve" changed: gain and limit were the old defaults."""
        axis = gp.normalize_profile({"axes": {"yaw": {"curve": 1.0}}})["axes"]["yaw"]
        old = lambda d: min(2.0 * 40 * (abs(d) / 40) ** 1.0, 180.0)   # noqa: E731  old default gain 2, limit 180
        for d in (10.0, 30.0, 60.0):
            self.assertAlmostEqual(gp.map_axis(d, axis), old(d), places=1, msg=d)


if __name__ == "__main__":
    unittest.main()

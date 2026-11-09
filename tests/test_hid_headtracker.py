"""Android head-tracker HID headsets (Sony XM5/XM6, Pixel Buds Pro…): parsing and angles, no hardware."""
import math
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hid_headtracker as h  # noqa: E402


def report(rot=(0, 0, 0), gyro=(0, 0, 0), counter=0):
    """Build input report 1 from a rotation vector in rad (device axes) and a rate in rad/s."""
    r = [round(v / h.ROT_SCALE) for v in rot] + [round(v / h.GYRO_SCALE) for v in gyro]
    return bytes([1]) + struct.pack("<6h", *r) + bytes([counter])


def device_rotvec(head_axis, deg):
    """Rotation vector in the headset's own axes for a rotation about a head axis (X right, Y fwd, Z up)."""
    v = [0.0, 0.0, 0.0]
    for out_i, src in enumerate(h.AXIS_SOURCE):            # invert the remap: head[out] = sign * dev[src]
        v[src] = head_axis[out_i] * math.radians(deg) / h.AXIS_SIGN[out_i]
    return tuple(v)


class TestHidHeadTracker(unittest.TestCase):
    def test_descriptor_and_marker(self):
        self.assertTrue(h.is_sensor_custom(bytes.fromhex("05200 9e1".replace(" ", "")) + b"\xa1\x01"))
        self.assertFalse(h.is_sensor_custom(bytes.fromhex("05010902a101")))     # a mouse

    def test_parse_scales(self):
        rot, gyro, n = h.parse_input(report((math.pi / 2, 0, 0), (0, 0, 16.0), 7))
        self.assertAlmostEqual(rot[0], math.pi / 2, places=3)
        self.assertAlmostEqual(gyro[2], 16.0, places=2)
        self.assertEqual(n, 7)
        self.assertIsNone(h.parse_input(b"\x02" + bytes(13)))                  # other report id
        self.assertIsNone(h.parse_input(b"\x01\x00"))                          # too short

    def test_first_sample_is_the_center(self):
        p = h.Pose()
        rot, _, _ = h.parse_input(report(device_rotvec((0, 0, 1), 40)))
        self.assertEqual([round(a, 3) for a in p.update(rot)], [0.0, 0.0, 0.0])

    def test_turn_right_look_up_and_tilt(self):
        p = h.Pose()
        p.update((0.0, 0.0, 0.0))
        yaw = p.update(h.parse_input(report(device_rotvec((0, 0, -1), 30)))[0])[0]   # about -Z (up) = to the right
        self.assertAlmostEqual(yaw, 30.0, delta=0.2)
        pitch = p.update(h.parse_input(report(device_rotvec((1, 0, 0), 20)))[0])[1]  # about +X (right) = nose up
        self.assertAlmostEqual(pitch, 20.0, delta=0.2)
        roll = p.update(h.parse_input(report(device_rotvec((0, 1, 0), 15)))[0])[2]   # about +Y (forward) = right ear down
        self.assertAlmostEqual(roll, 15.0, delta=0.2)

    def test_recenter_and_discontinuity(self):
        p = h.Pose()
        p.update(device_rotvec((0, 0, -1), 0), counter=1)
        self.assertGreater(abs(p.update(device_rotvec((0, 0, -1), 25), counter=1)[0]), 20)
        self.assertEqual(round(p.update(device_rotvec((0, 0, -1), 25), counter=2)[0], 3), 0.0)   # headset reset
        p.recenter()
        self.assertEqual(round(p.update(device_rotvec((0, 0, -1), 50), counter=2)[0], 3), 0.0)

    def test_manager_starts_one_tracker_per_device(self):
        started = []

        class FakeTracker(h.Tracker):
            def start(self):
                started.append(self.path)

            def is_alive(self):
                return True
        orig = h.Tracker
        h.Tracker = FakeTracker
        try:
            m = h.Manager(lambda *a: None, finder=lambda: [("/dev/hidraw7", "WH-1000XM5")])
            m.scan_once(); m.scan_once()
            self.assertEqual(started, ["/dev/hidraw7"])
            self.assertEqual(m.status()[0]["name"], "WH-1000XM5")
        finally:
            h.Tracker = orig


class TestControllerFeed(unittest.TestCase):
    def test_pose_reaches_outputs_and_moves_the_mouse(self):
        import airpods_mouse_controller as m

        class P:
            moves = []

            def move_rel(self, dx, dy):
                self.moves.append((dx, dy))
        cfg = m.load_config("/nonexistent/config.json")
        cfg["notifications"] = False
        cfg["mode"] = "souris"
        cfg["mouse_paused"] = False      # the mouse is a user setting, off until switched on
        ctl = m.Controller(cfg, P())
        ctl.on_head_pose(0.0, 0.0, 0.0, 0.02, 0.0, "x")
        ctl.on_head_pose(2.0, 1.0, 0.0, 0.02, 0.0, "x")
        dx, dy = P.moves[-1]
        self.assertGreater(dx, 0)          # turned right -> cursor right
        self.assertLess(dy, 0)             # looked up -> cursor up


if __name__ == "__main__":
    unittest.main()

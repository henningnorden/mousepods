# SPDX-License-Identifier: Apache-2.0
"""Game bridge output: one pose file per enabled game, each with its profile applied.

Run:  python -m unittest tests.test_game_bridge -v
"""
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import game_bridge as gb  # noqa: E402
import opentrack_out  # noqa: E402

LINEAR = {"enabled": True, "invert": False, "curve": 1.0, "limit": 180}


def read_pose(path):
    with open(path, "rb") as f:
        b = f.read()
    seq, version = struct.unpack_from("<II", b, 0)
    return seq, version, struct.unpack_from("<6d", b, 8), struct.unpack_from("<d", b, 56)[0]


def games(**gains):
    return {appid: {"enabled": True, "profile": {"smoothing": 0, "axes": {"yaw": {**LINEAR, "gain": g}}}}
            for appid, g in gains.items()}


class GameBridgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.bridge = gb.GameBridge(shm_dir=self.dir)

    def tearDown(self):
        self.bridge.close()
        shutil.rmtree(self.dir)

    def path(self, appid):
        return gb.pose_path(appid, self.dir)

    def test_writes_one_file_per_enabled_game(self):
        self.bridge.configure({**games(**{"1": 1.0}), "2": {"enabled": False, "profile": {}}})
        self.bridge.tick(1.0, (0, 0, 0, 10, 0, 0))
        self.assertTrue(os.path.exists(self.path("1")))
        self.assertFalse(os.path.exists(self.path("2")))
        seq, version, pose, t = read_pose(self.path("1"))
        self.assertEqual((seq, version), (1, 1))
        self.assertAlmostEqual(pose[3], 10.0)
        self.assertEqual(t, 1.0)

    def test_disabled_game_file_removed(self):
        self.bridge.configure(games(**{"1": 1.0}))
        self.bridge.tick(1.0, (0,) * 6)
        self.bridge.configure({"1": {"enabled": False}})
        self.assertFalse(os.path.exists(self.path("1")))
        self.assertFalse(self.bridge.active)

    def test_profile_applied_per_game(self):
        self.bridge.configure(games(**{"1": 1.0, "2": 2.0}))
        self.bridge.tick(1.0, (0, 0, 0, 10, 0, 0))
        self.assertAlmostEqual(read_pose(self.path("1"))[2][3], 10.0)
        self.assertAlmostEqual(read_pose(self.path("2"))[2][3], 20.0)

    def test_reconfigure_keeps_file_and_applies_new_profile(self):
        self.bridge.configure(games(**{"1": 1.0}))
        self.bridge.tick(1.0, (0, 0, 0, 10, 0, 0))
        self.bridge.configure(games(**{"1": 3.0}))
        self.bridge.tick(1.1, (0, 0, 0, 10, 0, 0))
        seq, _, pose, _ = read_pose(self.path("1"))
        self.assertEqual(seq, 2)
        self.assertAlmostEqual(pose[3], 30.0)

    def test_bridge_keeps_files_on_pose_gap(self):
        self.bridge.configure(games(**{"1": 1.0}))
        self.bridge.tick(1.0, (0, 0, 0, 12, 0, 0))
        # no ticks for 2 s (AirPods reconnecting): file stays, last pose stays
        self.bridge.tick(3.0, (0, 0, 0, 12, 0, 0))
        self.assertTrue(os.path.exists(self.path("1")))
        self.assertAlmostEqual(read_pose(self.path("1"))[2][3], 12.0)

    def test_seq_increments(self):
        self.bridge.configure(games(**{"1": 1.0}))
        for i in range(5):
            self.bridge.tick(1.0 + i / 120, (0,) * 6)
        self.assertEqual(read_pose(self.path("1"))[0], 5)

    def test_output_thread_calls_bridge(self):
        class Fake:
            active = True

            def __init__(self):
                self.ticks = []

            def tick(self, now, pose):
                self.ticks.append((now, pose))

        out = opentrack_out.OpenTrackOutput({"rate_hz": 120}, udp=False)
        fake = Fake()
        out.set_game_bridge(fake)
        out.tick(10.0)
        out.tick(10.5)
        self.assertEqual(len(fake.ticks), 2)
        self.assertIn("games", out.outputs)
        out.set_game_bridge(None)
        self.assertNotIn("games", out.outputs)


class BridgeThreadingTests(unittest.TestCase):
    def test_configure_while_ticking_never_breaks_the_output_thread(self):
        import threading
        d = tempfile.mkdtemp()
        bridge = gb.GameBridge(shm_dir=d)
        out = opentrack_out.OpenTrackOutput({"rate_hz": 1000}, udp=False)
        out.set_game_bridge(bridge)
        stop = threading.Event()
        errors = []

        def ticker():
            t = 0.0
            while not stop.is_set():
                t += 0.001
                try:
                    out.tick(t)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
                    return

        th = threading.Thread(target=ticker)
        th.start()
        try:
            for i in range(300):
                bridge.configure(games(**{str(n): 1.0 + i % 3 for n in range(1, 2 + i % 6)}))
        finally:
            stop.set()
            th.join()
            bridge.close()
            shutil.rmtree(d)
        self.assertEqual(errors, [])
        self.assertIsNone(out.bridge_error)

    def test_output_thread_survives_a_failing_bridge(self):
        class Broken:
            active = True

            def tick(self, now, pose):
                raise ValueError("mmap closed or invalid")
        out = opentrack_out.OpenTrackOutput({"rate_hz": 120}, udp=False)
        out.set_game_bridge(Broken())
        out.tick(1.0)                               # must not raise
        self.assertIn("mmap closed", out.bridge_error)


class BadUdpConfigTests(unittest.TestCase):
    def test_games_start_even_with_broken_opentrack_settings(self):
        import types
        import airpods_mouse_controller as m
        d = tempfile.mkdtemp()
        os.environ["MOUSEPODS_SHM_DIR"] = d
        ctl = types.SimpleNamespace(cfg={"opentrack": {"enabled": False, "port": "not-a-port", "rate_hz": "x"},
                                         "sim_joystick": {"enabled": False}, "games": games(**{"1": 1.0})},
                                    opentrack=None, opentrack_forced=False, joystick_forced=False, game_bridge=None)
        try:
            m.Controller.sync_opentrack(ctl)
            self.assertIsNotNone(ctl.opentrack)
            self.assertIn("games", ctl.opentrack.outputs)
            m.Controller.sync_opentrack(ctl)          # every config reload syncs again
            self.assertIn("games", ctl.opentrack.outputs)
        finally:
            if ctl.opentrack:
                ctl.opentrack.stop()
            del os.environ["MOUSEPODS_SHM_DIR"]
            shutil.rmtree(d)


class PoseFileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_files_live_in_private_user_dir(self):
        path = gb.pose_path("244210", self.dir)
        self.assertEqual(os.path.dirname(path), os.path.join(self.dir, f"mousepods-{os.getuid()}"))
        b = gb.GameBridge(shm_dir=self.dir)
        b.configure(games(**{"244210": 1.0}))
        st = os.stat(os.path.dirname(path))
        self.assertEqual(st.st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        b.close()

    def test_rejects_non_numeric_appid(self):
        for bad in ("../x", "1/2", "", "12a"):
            with self.assertRaises(ValueError):
                gb.pose_path(bad, self.dir)
        b = gb.GameBridge(shm_dir=self.dir)
        b.configure(games(**{"../../etc": 1.0}))
        self.assertFalse(b.active)

    def test_refuses_planted_symlink(self):
        d = gb.private_dir(self.dir)
        target = os.path.join(self.dir, "victim")
        with open(target, "w") as f:
            f.write("keep me")
        os.symlink(target, os.path.join(d, "pose-244210"))
        b = gb.GameBridge(shm_dir=self.dir)
        b.configure(games(**{"244210": 1.0}))
        with open(target) as f:
            self.assertEqual(f.read(), "keep me")
        self.assertFalse(b.active)

    def test_refuses_foreign_dir(self):
        d = os.path.join(self.dir, f"mousepods-{os.getuid()}")
        os.makedirs(d, mode=0o777)
        os.chmod(d, 0o777)
        with self.assertRaises(PermissionError):
            gb.private_dir(self.dir)


class ControllerSyncTests(unittest.TestCase):
    def test_enabled_game_starts_output_without_udp_or_joystick(self):
        import types
        import airpods_mouse_controller as m
        d = tempfile.mkdtemp()
        os.environ["MOUSEPODS_SHM_DIR"] = d
        try:
            ctl = types.SimpleNamespace(cfg={"opentrack": {"enabled": False}, "sim_joystick": {"enabled": False},
                                             "games": games(**{"1": 1.0})},
                                        opentrack=None, opentrack_forced=False, joystick_forced=False,
                                        game_bridge=None)
            m.Controller.sync_opentrack(ctl)
            self.assertIsNotNone(ctl.opentrack)
            self.assertIn("games", ctl.opentrack.outputs)
            ctl.cfg["games"]["1"]["enabled"] = False
            m.Controller.sync_opentrack(ctl)
            self.assertIsNone(ctl.opentrack)
            self.assertFalse(os.path.exists(gb.pose_path("1", d)))
        finally:
            if ctl.opentrack:
                ctl.opentrack.stop()
            del os.environ["MOUSEPODS_SHM_DIR"]
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
"""Calibration in the controller and /api/calibration: mouse paused (not saved), restored at the end.

Run:  python -m unittest tests.test_calibration_api -v
"""
import json
import os
import queue
import shutil
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import airpods_mouse_controller as m  # noqa: E402
import mousepods_web as web  # noqa: E402


def controller(cfg_path=None, **over):
    cfg = m.load_config(cfg_path or "/nonexistent.json")
    cfg.update({"notifications": False, "mouse_paused": False, **over})
    return m.Controller(cfg, m.NullPointer(), cfg_path=cfg_path)


def hold(ctl, t, yaw, pitch, secs):
    for _ in range(int(round(secs / 0.01))):
        t += 0.01
        ctl.calibration_feed(t, yaw, pitch)
    return t


class ControllerCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.d, "config.json")
        with open(self.cfg_path, "w") as f:
            f.write("{}")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_start_pauses_mouse_without_saving(self):
        ctl = controller(self.cfg_path)
        ctl.start_calibration()
        self.assertTrue(ctl.paused)
        with open(self.cfg_path) as f:
            self.assertNotIn("mouse_paused", json.load(f))
        self.assertTrue(ctl.calibration_state()["active"])
        ctl.cancel_calibration()

    def test_end_restores_mouse_after_failure(self):
        ctl = controller(self.cfg_path)
        ctl.start_calibration()
        ctl.calibration_tick(100.0)
        ctl.calibration_tick(103.5)                      # no head data: the AirPods are gone
        st = ctl.calibration_state()
        self.assertEqual((st["active"], st["step"], st["hint"]), (False, "failed", "no_signal"))
        self.assertFalse(ctl.paused)
        if ctl.opentrack:
            self.assertFalse(ctl.opentrack.tracker.suspend_autocenter)

    def test_restart_keeps_original_mouse_state(self):
        ctl = controller(self.cfg_path)
        ctl.start_calibration()
        ctl.start_calibration()
        ctl.cancel_calibration()
        self.assertFalse(ctl.paused)

    def test_paused_mouse_stays_paused(self):
        ctl = controller(self.cfg_path, mouse_paused=True)
        ctl.start_calibration()
        ctl.cancel_calibration()
        self.assertTrue(ctl.paused)

    def test_center_recenters_and_run_completes(self):
        ctl = controller(self.cfg_path)
        with mock.patch.object(ctl, "recenter") as rc:
            ctl.start_calibration()
            t = hold(ctl, 0.0, 0, 0, 2.1)
            rc.assert_called_once_with(quiet=True)
            for yaw, pitch in ((-15, 0), (25, 0), (0, 10), (0, -8)):
                t = hold(ctl, t, yaw, pitch, 2.1)
        st = ctl.calibration_state()
        self.assertEqual((st["active"], st["step"]), (False, "done"))
        self.assertAlmostEqual(st["result"]["yaw"]["pos"], 25, places=1)
        self.assertFalse(ctl.paused)

    def test_tracker_runs_while_calibrating(self):
        ctl = controller(self.cfg_path)
        ctl.start_calibration()
        try:
            self.assertIsNotNone(ctl.opentrack)
            self.assertTrue(ctl.opentrack.tracker.suspend_autocenter)
        finally:
            ctl.cancel_calibration()
        self.assertIsNone(ctl.opentrack)                 # nothing else wants it


class ReviewFixTests(unittest.TestCase):
    """From the whole-branch review: web thread vs. main loop, config reload during a calibration."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.d, "config.json")
        with open(self.cfg_path, "w") as f:
            json.dump({"mouse_paused": False}, f)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_config_reload_keeps_mouse_paused_while_calibrating(self):
        ctl = controller(self.cfg_path)
        ctl.start_calibration()
        m.update_config_file(self.cfg_path, {"mouse_paused": False, "mode": "souris"})
        ctl._cfg_mtime = 0                               # force the reload
        ctl.maybe_reload()
        self.assertTrue(ctl.paused)
        ctl.cancel_calibration()
        self.assertFalse(ctl.paused)

    def test_sync_from_two_threads_creates_one_output(self):
        import threading
        import time as _t
        import opentrack_out
        made = []

        class Slow:
            def __init__(self, cfg, udp=True):
                _t.sleep(0.05)                           # widen the race window
                made.append(self)
                self.udp, self.joystick, self.tracker = udp, None, types_ns(suspend_autocenter=False)

            def start(self):
                return self

            def stop(self):
                pass

            def configure(self, cfg, udp=None):
                pass

            def set_game_bridge(self, b):
                pass

            def set_joystick(self, j):
                return None

        ctl = controller(self.cfg_path)
        ctl.calibration = object()                       # wants the tracker
        with mock.patch.object(opentrack_out, "OpenTrackOutput", Slow):
            ts = [threading.Thread(target=ctl.sync_opentrack) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(made), 1)

    def test_feed_survives_output_stopped_by_another_thread(self):
        ctl = controller(self.cfg_path)

        class Vanishing:
            def feed(self, *a):
                ctl.opentrack = None                     # the web thread stopped it just now

            def head(self):
                return (0.0, 0.0, 0.0)

        import calibration
        ctl.opentrack = Vanishing()
        ctl.calibration = calibration.Calibration()      # reads the head right after the feed
        ctl.on_block(bytes(58), 1.0)                     # must not raise


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


class CalibrationApiTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.d, "config.json")
        with open(self.cfg_path, "w") as f:
            f.write("{}")
        self.ctl = controller(self.cfg_path, ui_lang="en")
        self.ui = web.WebUI(self.ctl, self.cfg_path, port=0)
        self.ui.q = queue.Queue()
        self.ui.start()
        self.base = f"http://127.0.0.1:{self.ui.server.server_address[1]}"

    def tearDown(self):
        self.ctl.cancel_calibration()
        self.ui.server.shutdown()
        shutil.rmtree(self.d)

    def get(self, path):
        return json.load(urllib.request.urlopen(self.base + path))

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/calibration", method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req))

    def test_save_baseline_sets_games_and_pointer_range(self):
        key = self.ctl.profile_key()
        m.update_config_file(self.cfg_path, {"profiles": {key: {"lat": [0, 1, 0], "yaw_sign": 1}}})
        body = {"action": "save_baseline", "yaw": {"neg": 19, "pos": 17}, "pitch": {"neg": 8, "pos": 11},
                "target_side": 150, "target_vert": 70, "monitors": [{"connector": "DP-1", "main": True}]}
        self.assertEqual(self.post(body), {"ok": True, "pointer": True})
        with open(self.cfg_path) as f:
            saved = json.load(f)
        self.assertEqual(saved["game_baseline"]["yaw"], {"neg": 19.0, "pos": 17.0})
        self.assertEqual(saved["game_baseline"]["target_side"], 150.0)
        self.assertEqual(saved["profiles"][key]["range"], {"x_neg": 19.0, "x_pos": 17.0, "y_pos": 11.0, "y_neg": 8.0})
        self.assertEqual(saved["profiles"][key]["lat"], [0, 1, 0])          # the rest of the profile stays

    def test_save_baseline_without_head_calibration_leaves_pointer(self):
        body = {"action": "save_baseline", "yaw": {"neg": 19, "pos": 17}, "pitch": {"neg": 8, "pos": 11},
                "target_side": 150, "target_vert": 70}
        self.assertEqual(self.post(body), {"ok": True, "pointer": False})
        self.assertEqual(self.ctl.cfg["game_baseline"]["yaw"], {"neg": 19.0, "pos": 17.0})   # state shows it at once
        with open(self.cfg_path) as f:
            saved = json.load(f)
        self.assertNotIn(self.ctl.profile_key(), saved.get("profiles", {}))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"action": "save_baseline", "yaw": {"neg": "x"}})
        self.assertEqual(e.exception.code, 400)

    def fake_head(self, done=False):
        """What process_queue would create for ("calib", "start", "head", "souris")."""
        s = types.SimpleNamespace(done=done, as_dict=lambda: {"text": "Turn your head LEFT", "index": 1, "total": 7,
                                                             "progress": 0.4, "message": None, "done": done})
        self.ui.calib = s
        self.ctl.last_ht = time.monotonic()
        return s

    def test_api_start_get_cancel(self):
        self.assertEqual(self.get("/api/calibration"), {"active": False, "step": None})
        st = self.post({"action": "start"})
        self.assertEqual((st["active"], st["phase"]), (True, "head"))           # head movement first
        self.assertEqual(self.ui.q.get_nowait(), ("calib", "start", "head", "souris"))
        st = self.post({"action": "cancel"})
        self.assertEqual((st["active"], st["step"]), (False, "cancelled"))
        self.assertEqual(self.ui.q.get_nowait(), ("calib", "cancel"))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"action": "x"})
        self.assertEqual(e.exception.code, 400)

    def test_head_phase_then_points(self):
        self.post({"action": "start"})
        head = self.fake_head()
        st = self.get("/api/calibration")
        self.assertEqual((st["phase"], st["head"]["text"], st["head"]["index"]), ("head", "Turn your head LEFT", 1))
        head.done = True
        st = self.get("/api/calibration")
        self.assertEqual((st["active"], st["phase"], st["step"]), (True, "points", "center"))
        self.assertIsNotNone(self.ctl.calibration)

    def test_head_phase_without_data_fails(self):
        self.post({"action": "start"})
        self.ctl.last_ht = time.monotonic() - 10                           # AirPods gone
        with mock.patch.object(web.time, "monotonic", return_value=time.monotonic() + 10):
            st = self.get("/api/calibration")
        self.assertEqual((st["active"], st["step"], st["hint"]), (False, "failed", "no_signal"))
        self.assertEqual(self.ui.q.queue[-1], ("calib", "cancel"))



class HeadCalibrationKeepsRangeTests(unittest.TestCase):
    def test_rotation_calibration_keeps_the_pointer_edges(self):
        """The baseline sets the pointer's screen edges (range); learning the head movement again
        must not throw them away."""
        d = tempfile.mkdtemp()
        try:
            cfg = os.path.join(d, "config.json")
            ctl = controller(cfg)
            key = f"{ctl.pid}_head"
            with open(cfg, "w") as f:
                json.dump({"profiles": {key: {"lat": [0, 1, 0], "range": {"x_neg": 19, "x_pos": 17, "y_pos": 11, "y_neg": 8}}}}, f)
            s = web.CalibSession(ctl, cfg, "head", "souris")
            s.up, s.res = (0.0, 0.0, 1.0), {"yaw": 1.0, "pitch": (1.0, 0.0, 0.0), "roll": (0.0, 1.0, 0.0)}
            with mock.patch.object(m, "profile_from_calibration", return_value={"lat": [0, 0.9, 0.1]}):
                s.finish()
            with open(cfg) as f:
                prof = json.load(f)["profiles"][key]
            self.assertEqual(prof["lat"], [0, 0.9, 0.1])
            self.assertEqual(prof["range"], {"x_neg": 19, "x_pos": 17, "y_pos": 11, "y_neg": 8})
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()

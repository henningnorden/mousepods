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

    def test_api_start_get_cancel(self):
        self.assertEqual(self.get("/api/calibration"), {"active": False, "step": None})
        st = self.post({"action": "start"})
        self.assertEqual((st["active"], st["step"]), (True, "center"))
        self.assertEqual(self.get("/api/calibration")["step"], "center")
        st = self.post({"action": "cancel"})
        self.assertEqual((st["active"], st["step"]), (False, "cancelled"))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({"action": "x"})
        self.assertEqual(e.exception.code, 400)


if __name__ == "__main__":
    unittest.main()

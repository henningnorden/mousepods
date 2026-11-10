# SPDX-License-Identifier: Apache-2.0
"""AAP start on an open channel, and the Windows head tracking test program.

Run:  python -m unittest tests.test_windows_headtrack -v
"""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "windows", "tools"))
import airpods_mouse_controller as amc  # noqa: E402
import headtrack_test as ht  # noqa: E402
import l2cap_win  # noqa: E402


class FakeSock:
    def __init__(self, packets=()):
        self.sent = []
        self.packets = list(packets)
        self.timeout = "unset"
        self.closed = False

    def send(self, p):
        self.sent.append(p)

    def settimeout(self, t):
        self.timeout = t

    def recv(self, n):
        if self.packets:
            return self.packets.pop(0)
        raise TimeoutError("timed out")

    def close(self):
        self.closed = True


class FakeClock:
    """Each call advances 5 ms; sleep-free."""

    def __init__(self, step=0.005):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


class AttachTests(unittest.TestCase):
    def test_attach_sends_start_packets(self):
        c = amc.AAPClient("AA:BB:CC:DD:EE:FF")
        s = FakeSock()
        with mock.patch.object(amc.time, "sleep"):
            c.attach(s, "AA:BB:CC:DD:EE:FF", "2013")
        self.assertEqual(s.sent[:3], [amc.PKT_HANDSHAKE, amc.PKT_FEATURES, amc.PKT_NOTIFICATIONS])
        self.assertIsNone(s.timeout)
        self.assertIs(c.sock, s)
        self.assertEqual(c.pid, "2013")
        self.assertEqual(c.addr, "AA:BB:CC:DD:EE:FF")

    def test_connect_uses_attach(self):
        c = amc.AAPClient("AA:BB:CC:DD:EE:FF", auto_connect=False)
        s = FakeSock()
        with mock.patch.object(amc.l2cap, "open_seqpacket", return_value=s), \
                mock.patch.object(amc, "product_id", return_value="2014"), \
                mock.patch.object(c, "attach") as att:
            c.connect()
        att.assert_called_once_with(s, "AA:BB:CC:DD:EE:FF", "2014")


def capture_packets():
    with open(os.path.join(ROOT, "captures", "airpods3_glide_raw.json")) as f:
        return [bytes.fromhex(h) for _t, _label, h in json.load(f)]


class RunTests(unittest.TestCase):
    def test_run_replays_capture(self):
        sock = FakeSock(capture_packets())
        lines = []
        with mock.patch.object(amc.time, "sleep"):
            res = ht.run(lambda addr: sock, "AA:BB:CC:DD:EE:FF", "2013", 2.0,
                         clock=FakeClock(0.0005), out=lines.append)
        self.assertGreater(res["blocks"], 0)
        self.assertEqual(ht.classify(res), "ok")
        self.assertTrue(res["ok"])
        self.assertTrue(any(l.startswith("yaw") for l in lines))
        self.assertTrue(sock.closed)

    def test_run_no_answer(self):
        sock = FakeSock()
        with mock.patch.object(amc.time, "sleep"):
            res = ht.run(lambda addr: sock, "AA:BB:CC:DD:EE:FF", None, 5.0,
                         clock=FakeClock(0.5), out=lambda s: None)
        self.assertEqual(ht.classify(res), "no_answer")
        self.assertIsNone(res["first_reply_ms"])

    def test_run_open_failures(self):
        def boom(exc):
            def f(addr):
                raise exc
            return f
        cases = [
            (PermissionError("no"), "refused_by_policy", 0, None),
            (l2cap_win.DriverError(0xC0000001, 7, 3), "airpods_refused", 3, 0xC0000001),
            (l2cap_win.DriverError(0xC0000001, 0, 0), "refused_by_policy", 0, 0xC0000001),
            (FileNotFoundError("driver"), "windows_refused", 1, None),
            # stage 3 timed out or cancelled: nobody answered, the AirPods did not refuse
            (l2cap_win.DriverError(-0x3FFFFF4B, 0, 3), "no_response", 3, -0x3FFFFF4B),   # 0xC00000B5
            (l2cap_win.DriverError(0xC00000B5, 0, 3), "no_response", 3, 0xC00000B5),
            (l2cap_win.DriverError(0xC0000120, 0, 3), "no_response", 3, 0xC0000120),
            (l2cap_win.DriverError(0xC00000B5, 0, 2), "windows_refused", 2, 0xC00000B5),
            # a second handle while one is open: the driver is busy, not a policy refusal
            (l2cap_win.DriverError(0, 0, 0, winerror=l2cap_win.ERROR_BUSY), "driver_busy", 0, 0),
            (l2cap_win.DriverError(0, 0, 0, winerror=l2cap_win.ERROR_INVALID_PARAMETER),
             "refused_by_policy", 0, 0),
        ]
        for exc, kind, stage, nt in cases:
            res = ht.run(boom(exc), "AA:BB:CC:DD:EE:FF", None, 1.0, clock=FakeClock(),
                         out=lambda s: None)
            self.assertFalse(res["ok"])
            self.assertEqual(res["stage"], stage)
            self.assertEqual(res["ntstatus"], nt)
            self.assertEqual(ht.classify(res), kind, exc)


class ResetSock(FakeSock):
    """Raises ConnectionResetError once the packets are used up, or on the nth send."""

    def __init__(self, packets=(), fail_send_at=None):
        super().__init__(packets)
        self.fail_send_at = fail_send_at

    def send(self, p):
        if self.fail_send_at is not None and len(self.sent) >= self.fail_send_at:
            raise ConnectionResetError("reset")
        super().send(p)

    def recv(self, n):
        if self.packets:
            return self.packets.pop(0)
        raise ConnectionResetError("reset")


class LinkLostTests(unittest.TestCase):
    def _run(self, sock):
        with mock.patch.object(amc.time, "sleep"), mock.patch.object(amc, "log"):
            return ht.run(lambda addr: sock, "AA:BB:CC:DD:EE:FF", "2013", 2.0,
                          clock=FakeClock(0.0005), out=lambda s: None)

    def test_reset_during_attach(self):
        sock = ResetSock(fail_send_at=1)
        res = self._run(sock)
        self.assertTrue(sock.closed)
        self.assertIn("ConnectionResetError", res["error"])
        self.assertEqual(ht.classify(res), "link_lost")
        self.assertFalse(res["ok"])

    def test_reset_before_any_block(self):
        sock = ResetSock()
        res = self._run(sock)
        self.assertTrue(sock.closed)
        self.assertIn("ConnectionResetError", res["error"])
        self.assertEqual(ht.classify(res), "link_lost")

    def test_reset_after_blocks(self):
        sock = ResetSock(capture_packets())
        res = self._run(sock)
        self.assertGreater(res["blocks"], 0)
        self.assertIn("ConnectionResetError", res["error"])
        self.assertEqual(ht.classify(res), "ok")
        self.assertTrue(res["ok"])

    def test_clean_run_has_no_error(self):
        res = self._run(FakeSock(capture_packets()))
        self.assertIsNone(res["error"])


class ClassifyTests(unittest.TestCase):
    def test_classify(self):
        def r(**kw):
            base = {"blocks": 0, "stage": None, "first_reply_ms": None}
            base.update(kw)
            return base
        self.assertEqual(ht.classify(r(blocks=5, first_reply_ms=20)), "ok")
        for st in (1, 2, 4, 5):
            self.assertEqual(ht.classify(r(stage=st)), "windows_refused")
        self.assertEqual(ht.classify(r(stage=3)), "airpods_refused")
        self.assertEqual(ht.classify(r()), "no_answer")
        self.assertEqual(ht.classify(r(error="ConnectionResetError: x")), "link_lost")
        self.assertEqual(ht.classify(r(blocks=3, error="ConnectionResetError: x")), "ok")
        self.assertEqual(ht.classify(r(first_reply_ms=30)), "no_motion")
        self.assertEqual(ht.classify(r(stage=0)), "refused_by_policy")
        self.assertEqual(ht.classify(r(stage=3, ntstatus=0xC00000B5)), "no_response")
        self.assertEqual(ht.classify(r(stage=3, ntstatus=0xC0000120 - (1 << 32))), "no_response")
        self.assertEqual(ht.classify(r(stage=3, ntstatus=0xC0000001)), "airpods_refused")
        self.assertEqual(ht.classify(r(stage=0, winerror=170)), "driver_busy")

    def test_oserror_with_error_busy_is_driver_busy(self):
        e = OSError(16, "busy")
        e.winerror = 170        # what Windows sets on an OSError from a Win32 error
        res = ht._open_failure(e)
        self.assertEqual(res["winerror"], 170)
        self.assertEqual(ht.classify(res), "driver_busy")


class ImportTests(unittest.TestCase):
    def test_controller_imports_without_linux_modules(self):
        code = (
            "import sys\n"
            "sys.modules['evdev'] = None\n"
            "sys.modules['gi'] = None\n"
            "sys.path.insert(0, %r)\n"
            "import airpods_mouse_controller\n" % ROOT)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()

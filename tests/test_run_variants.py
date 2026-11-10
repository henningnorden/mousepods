# SPDX-License-Identifier: Apache-2.0
"""The VM test round: variant matrix, resume after a crash, the report.

Run:  python -m unittest tests.test_run_variants -v
"""
import io
import os
import queue
import sys
import tempfile
import unittest
import wave
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "windows", "vm"))
import l2cap_win  # noqa: E402
import run_variants as rv  # noqa: E402


def _variants():
    return [v for v in rv.VARIANTS if v["kind"] == "variant"]


def _done(step, verdict, **kw):
    rec = dict(step)
    rec.update(verdict=verdict, route_loaded=step.get("route"), result={}, detail="")
    rec.update(kw)
    return rec


class MatrixTests(unittest.TestCase):
    def test_matrix_has_16_variants_and_4_checks(self):
        variants = _variants()
        checks = [v for v in rv.VARIANTS if v["kind"] == "check"]
        self.assertEqual(len(variants), 16)
        self.assertEqual([c["name"] for c in checks],
                         ["psm_refused", "second_handle", "orphan_handle", "disconnect"])
        combos = {(v["route"], v["mtu"], v["security"], v["audio"]) for v in variants}
        self.assertEqual(len(combos), 16)
        self.assertEqual({v["route"] for v in variants}, {"root", "bthenum"})
        self.assertEqual({v["mtu"] for v in variants}, {2582, 672})
        self.assertEqual({v["security"] for v in variants}, {"encrypt", "none"})
        self.assertEqual({v["audio"] for v in variants}, {"idle", "playing"})
        self.assertEqual([v["id"] for v in rv.VARIANTS], list(range(1, 21)))
        self.assertTrue(all(v["seconds"] == 15 for v in variants))

    def test_routes_are_grouped(self):
        # one switch from root to bthenum, not one per variant
        routes = [v["route"] for v in _variants()]
        switches = sum(1 for a, b in zip(routes, routes[1:]) if a != b)
        self.assertEqual(switches, 1)


class ResumeTests(unittest.TestCase):
    def test_resume_skips_done_and_marks_crash(self):
        state = {"results": [_done(v, "ok") for v in rv.VARIANTS[:5]]}
        marker = {"id": 6, "started": "2026-11-10T20:00:00Z"}
        remaining = rv.next_variants(state, marker)
        self.assertEqual(remaining[0]["id"], 7)
        six = [r for r in state["results"] if r["id"] == 6]
        self.assertEqual(len(six), 1)
        self.assertEqual(six[0]["verdict"], "bugcheck")
        self.assertEqual(len(remaining), 20 - 6)

    def test_no_marker_continues_where_it_stopped(self):
        state = {"results": [_done(v, "ok") for v in rv.VARIANTS[:3]]}
        self.assertEqual(rv.next_variants(state, None)[0]["id"], 4)
        self.assertEqual(len(state["results"]), 3)

    def test_marker_for_a_done_step_is_ignored(self):
        state = {"results": [_done(v, "ok") for v in rv.VARIANTS[:3]]}
        rv.next_variants(state, {"id": 2})
        self.assertEqual([r["verdict"] for r in state["results"]], ["ok"] * 3)

    def test_reset_without_dump_is_interrupted(self):
        state = {"results": []}
        rv.next_variants(state, {"id": 1, "crash": {"kind": "interrupted"}})
        self.assertEqual(state["results"][0]["verdict"], "interrupted")

    def test_crash_at_boot_skips_the_rest_of_that_route(self):
        state = {"results": [_done(v, "ok") for v in rv.VARIANTS[:1]]}
        remaining = rv.next_variants(state, {"id": 2, "crash": {"kind": "bugcheck", "at_boot": True}})
        self.assertTrue(all(v["route"] != "root" for v in remaining if v["kind"] == "variant"))
        skipped = [r for r in state["results"] if r["verdict"] == "skipped"]
        self.assertEqual([r["id"] for r in skipped], list(range(3, 9)))

    def test_crash_while_setting_up_the_route_skips_that_route(self):
        state = {"results": []}
        remaining = rv.next_variants(state, {"id": 9, "phase": "route", "route": "bthenum"})
        self.assertEqual(state["results"][0]["verdict"], "bugcheck")
        self.assertEqual(state["results"][0]["phase"], "route")
        self.assertTrue(all(v["route"] != "bthenum" for v in remaining if v["kind"] == "variant"))
        self.assertEqual([r["id"] for r in state["results"] if r["verdict"] == "skipped"], list(range(10, 17)))
        self.assertEqual(rv.check_config(state)["route"], "root")

    def test_crash_in_the_channel_phase_does_not_skip_the_route(self):
        state = {"results": []}
        remaining = rv.next_variants(state, {"id": 9, "phase": "channel", "route": "bthenum"})
        self.assertEqual(remaining[0]["id"], 1)
        self.assertEqual(len(state["results"]), 1)

    def test_three_crashes_in_a_row_across_routes_stop_the_round(self):
        state = {"results": [_done(v, "ok") for v in rv.VARIANTS[:6]]
                 + [_done(rv.VARIANTS[6], "bugcheck", phase="channel")]}
        step = rv.plan_next(state, {"id": 8, "phase": "channel", "route": "root"})
        self.assertFalse(step.get("done"))
        self.assertEqual(step["id"], 9)
        step = rv.plan_next(state, {"id": 9, "phase": "channel", "route": "bthenum"})
        self.assertTrue(step["done"])
        self.assertIn("3 steps in a row", step["stopped"])
        text = rv.render_report(state["results"], state["meta"])
        self.assertIn("The round stopped early", text)
        self.assertIn("Not run: 10,", text)

    def test_two_channel_crashes_skip_only_that_route(self):
        # three in a row on route a used to stop the round before route b ran
        state = {"results": []}
        step = rv.plan_next(state, {"id": 1, "phase": "channel", "route": "root"})
        self.assertEqual(step["id"], 2)
        step = rv.plan_next(state, {"id": 2, "phase": "channel", "route": "root"})
        self.assertNotIn("done", step)
        self.assertEqual(step["id"], 9)
        self.assertEqual(step["route"], "bthenum")
        verdicts = {r["id"]: r["verdict"] for r in state["results"]}
        self.assertEqual(verdicts[1], "bugcheck")
        self.assertEqual(verdicts[2], "bugcheck")
        self.assertEqual([i for i, v in verdicts.items() if v == "skipped"], list(range(3, 9)))
        self.assertNotIn("stopped", state.get("meta", {}))
        skipped = [r for r in state["results"] if r["verdict"] == "skipped"]
        self.assertIn("route root", skipped[0]["detail"])

    def test_one_channel_crash_keeps_the_route(self):
        state = {"results": []}
        self.assertEqual(rv.plan_next(state, {"id": 1, "phase": "channel", "route": "root"})["id"], 2)

    def test_three_check_crashes_in_a_row_stop_the_round(self):
        state = {"results": [_done(v, "ok") for v in _variants()]}
        for i in (17, 18):
            state["results"].append(_done(rv.VARIANTS[i - 1], "bugcheck", route="root", mtu=2582,
                                          security="encrypt", phase="channel"))
        step = rv.plan_next(state, {"id": 19, "phase": "channel", "route": "root"})
        self.assertTrue(step["done"])
        self.assertIn("3 steps in a row", step["stopped"])

    def test_crashes_with_a_good_step_between_do_not_stop(self):
        state = {"results": [_done(rv.VARIANTS[0], "bugcheck"), _done(rv.VARIANTS[1], "ok")]}
        self.assertFalse(rv.plan_next(state, {"id": 3}).get("done"))

    def test_hung_route_setup_skips_the_route(self):
        state = {"results": []}
        rv.record_hung_route(state, 1, "root", "devnode create did not finish")
        verdicts = {r["id"]: r["verdict"] for r in state["results"]}
        self.assertEqual(verdicts[1], "hung")
        self.assertEqual([i for i, v in verdicts.items() if v == "skipped"], list(range(2, 9)))
        self.assertEqual(rv.plan_next(state, None)["id"], 9)

    def test_checks_skipped_when_both_routes_broke(self):
        state = {"results": []}
        rv.record_hung_route(state, 1, "root", "hung")
        rv.record_hung_route(state, 9, "bthenum", "hung")
        self.assertTrue(rv.plan_next(state, None)["done"])
        self.assertEqual(sum(1 for r in state["results"] if r["kind"] == "check"), 4)

    def test_checks_use_a_route_that_worked(self):
        state = {"results": [_done(v, "windows_refused") for v in _variants()]}
        state["results"][11] = _done(_variants()[11], "ok")
        cfg = rv.check_config(state)
        v = _variants()[11]
        self.assertEqual((cfg["route"], cfg["mtu"], cfg["security"]), (v["route"], v["mtu"], v["security"]))


class ReportTests(unittest.TestCase):
    def _results(self):
        vs = _variants()
        res = [_done(v, "ok", result={"blocks": 300, "rate_hz": 20.0, "yaw_span_deg": 40.0,
                                      "first_reply_ms": 50}) for v in vs[:3]]
        res.append(_done(vs[3], "windows_refused",
                         result={"stage": 2, "ntstatus": -1073741823, "bt_status": 5}))
        res.append(_done(vs[4], "airpods_refused",
                         result={"stage": 3, "ntstatus": -1073741823, "bt_status": 0x30000 | 0x10}))
        return res

    def test_report_lists_failures_first(self):
        text = rv.render_report(self._results())
        self.assertIn("ok 3 / 16", text)
        lines = [ln for ln in text.splitlines() if ln.startswith("| ") and "variant" not in ln.lower()
                 and not ln.startswith("| ---")]
        rows = [ln for ln in lines if any(" %s " % w in ln for w in ("ok", "windows_refused", "airpods_refused"))]
        first_ok = next(i for i, ln in enumerate(rows) if "| ok |" in ln)
        self.assertTrue(all("| ok |" not in ln for ln in rows[:first_ok]))
        self.assertGreaterEqual(first_ok, 2)
        self.assertTrue(all("| ok |" in ln for ln in rows[first_ok:]))

    def test_report_distinguishes_windows_and_airpods(self):
        text = rv.render_report(self._results())
        self.assertIn("windows_refused", text)
        self.assertIn("airpods_refused", text)
        self.assertIn("Windows refused", text)
        self.assertIn("AirPods refused", text)

    def test_outcome_line_comes_first(self):
        text = rv.render_report(self._results())
        self.assertIn("channel opens and motion arrives in 3/16 variants", text.splitlines()[2])

    def test_outcome_when_nothing_opened(self):
        res = [_done(v, "airpods_refused") for v in _variants()[:4]]
        res += [_done(v, "windows_refused") for v in _variants()[4:]]
        text = rv.render_report(res)
        self.assertIn("no variant opened the channel (AirPods refused", text)
        self.assertIn("ok 0 / 16", text)

    def test_outcome_when_only_windows_refused(self):
        text = rv.render_report([_done(v, "windows_refused") for v in _variants()])
        self.assertIn("no variant opened the channel (Windows refused", text)

    def test_route_not_loaded_is_not_a_channel_result(self):
        res = [_done(v, "route_not_loaded", route_loaded="none") for v in _variants()]
        text = rv.render_report(res)
        self.assertIn("route_not_loaded", text)
        self.assertNotIn("Windows refused", text.splitlines()[2])

    def test_fatal_round(self):
        text = rv.render_report([], {"fatal": "pnputil failed with exit code 5"})
        self.assertIn("pnputil failed", text.splitlines()[2])


FULL = "A4:C3:F0:12:3F:A1"


class MaskTests(unittest.TestCase):
    def _assert_masked(self, text):
        for form in (FULL, FULL.replace(":", ""), FULL.replace(":", "-"), FULL.lower()):
            self.assertNotIn(form, text)
            self.assertNotIn(form.replace(":", "").lower(), text.lower().replace("xxxxxxxx3fa1", ""))

    def test_mask_addr(self):
        self.assertEqual(rv.mask_addr(FULL), "XX:XX:XX:XX:3F:A1")

    def test_report_never_contains_a_full_address(self):
        rec = _done(_variants()[0], "link_lost", radio=FULL,
                    result={"error": "OSError: connect to %s failed" % FULL})
        text = rv.render_report([rec], {"addr": FULL, "name": "Henning's AirPods Pro"})
        self._assert_masked(text)
        self.assertIn("XX:XX:XX:XX:3F:A1", text)
        self.assertIn("Henning's AirPods Pro", text)

    def test_setup_writer_never_contains_a_full_address(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "setup.txt")
            rv.write_setup(path, [["AirPods", "Pro (%s)" % FULL], ["Device", "BTHENUM\\X&0&%s_C00000000" % FULL.replace(":", "")]],
                           [{"name": "AirPods Pro", "address": FULL, "connected": True}])
            with open(path, encoding="utf-8") as f:
                text = f.read()
        self._assert_masked(text)
        self.assertIn("AirPods Pro  XX:XX:XX:XX:3F:A1", text)

    def test_driver_log_form_and_guids(self):
        text = rv.mask_text("state idle -> connecting: A4C3F0123FA1 psm 0x1001; {6d0e3c52-4f1b-4c9a-9a51-2f0b8e7d1a02}")
        self.assertIn("XXXXXXXX3FA1", text)
        self.assertIn("2f0b8e7d1a02", text)    # the service GUID stays readable

    def test_mask_dir_and_hidden_words(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "round.log"), "w") as f:
                f.write("C:\\Users\\henni\\mousepods on DESKTOP-ABC, Henning's AirPods %s\n" % FULL)
            rv.mask_dir(d, ["henni", "DESKTOP-ABC"], [FULL])
            with open(os.path.join(d, "round.log")) as f:
                text = f.read()
        self._assert_masked(text)
        self.assertNotIn("\\henni\\", text)
        self.assertNotIn("DESKTOP-ABC", text)
        self.assertIn("Henning's AirPods", text)    # whole words only

    def test_short_names_are_not_hidden(self):
        self.assertEqual(rv.mask_text("the pen is here", ["pen"]), "the pen is here")

    def test_address_right_after_colon_or_dash(self):
        for text in ("radio:%s" % FULL, "radio-%s" % FULL, "x:%s:" % FULL.lower()):
            self._assert_masked(rv.mask_text(text))

    def test_known_addresses_in_windows_ids(self):
        radio = "00:1A:7D:DA:71:13"
        ids = ("Bluetooth#Bluetooth001a7dda7113-a4c3f0123fa1",
               "BTHENUM\\{6d0e3c52-4f1b-4c9a-9a51-2f0b8e7d1a02}_LOCALMFG&0002\\7&1&0&A4C3F0123FA1_C00000000",
               "BluetoothDevice_a4c3f0123fa1", "A4-C3-F0-12-3F-A1")
        for text in ids:
            out = rv.mask_text(text, addrs=[FULL, radio])
            self._assert_masked(out)
            self.assertNotIn("001a7dda7113", out.lower())

    def test_mask_dir_is_recursive_and_refuses_leftovers(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sub"))
            with open(os.path.join(d, "sub", "x.txt"), "w") as f:
                f.write("Bluetooth#Bluetooth001a7dda7113-a4c3f0123fa1\n")
            rv.mask_dir(d, (), [FULL])
            with open(os.path.join(d, "sub", "x.txt")) as f:
                self._assert_masked(f.read())
            with mock.patch.object(rv, "mask_text", side_effect=lambda t, *a, **k: t):
                with open(os.path.join(d, "left.txt"), "w") as f:
                    f.write(FULL)
                with self.assertRaises(rv.MaskError):
                    rv.mask_dir(d, (), [FULL])

    def test_mask_dir_keeps_crlf_bom_and_other_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.md")
            raw = (b"\xef\xbb\xbf| 1 | a |\r\n| 2 | radio " + FULL.encode() + b" |\r\n"
                   b"bad \xff\xfe byte " + FULL.replace(":", "").encode() + b"\r\n")
            with open(path, "wb") as f:
                f.write(raw)
            rv.mask_dir(d, (), [FULL])
            with open(path, "rb") as f:
                out = f.read()
        want = (raw.replace(FULL.encode(), b"XX:XX:XX:XX:3F:A1")
                .replace(FULL.replace(":", "").encode(), b"XXXXXXXX3FA1"))
        self.assertEqual(out, want)
        self.assertNotIn(b"\r\r\n", out)

    def test_mask_cli_uses_the_known_addresses_and_fails_closed(self):
        import shutil
        with tempfile.TemporaryDirectory() as base:
            local = os.path.join(base, "round")         # the unmasked local round folder
            rv.main(["init", "--dir", local, "--known", FULL], out=io.StringIO())
            with open(os.path.join(local, "install.txt"), "w") as f:
                f.write("installed on BTHENUM\\X&0&a4c3f0123fa1_C00000000\n")
            copy = os.path.join(base, "copy")           # what gets pushed
            shutil.copytree(local, copy)
            self.assertEqual(rv.main(["mask", "--dir", copy, "--state", local], out=io.StringIO()), 0)
            for name in ("install.txt", "results.json"):
                with open(os.path.join(copy, name)) as f:
                    self._assert_masked(f.read())
            with open(os.path.join(copy, "x.bin"), "wb") as f:
                f.write(b"\xff" + FULL.encode())       # not UTF-8: masked anyway
            self.assertEqual(rv.main(["mask", "--dir", copy, "--state", local], out=io.StringIO()), 0)
            with open(os.path.join(copy, "x.bin"), "rb") as f:
                self.assertEqual(f.read(), b"\xffXX:XX:XX:XX:3F:A1")
            with open(os.path.join(copy, "left.txt"), "w") as f:
                f.write(FULL)
            with mock.patch.object(rv, "mask_text", side_effect=lambda t, *a, **k: t):
                self.assertEqual(rv.main(["mask", "--dir", copy, "--state", local], out=io.StringIO()), 3)


class SetupCliTests(unittest.TestCase):
    """The JSON round.ps1 Write-Setup sends, in the shapes Windows PowerShell 5.1 can produce."""

    SHAPES = {
        "pairs": '{"facts": [["Windows", "Windows 11 Pro 24H2"], ["Test signing", "active: True"]], '
                 '"devices": [{"name": "AirPods Pro", "address": "%s", "connected": true}]}' % FULL,
        "kv": '{"facts": [{"k": "Windows", "v": "Windows 11 Pro 24H2"}, {"k": "Test signing", "v": "active: True"}], '
              '"devices": [{"name": "AirPods Pro", "address": "%s", "connected": true}]}' % FULL,
        "wrapped": '{"facts": {"value": [{"value": ["Windows", "Windows 11 Pro 24H2"], "Count": 2}, '
                   '{"value": ["Test signing", "active: True"], "Count": 2}], "Count": 2}, '
                   '"devices": {"value": [{"name": "AirPods Pro", "address": "%s", "connected": true}], "Count": 1}}' % FULL,
    }

    def test_every_shape_gives_the_facts(self):
        for shape, js in self.SHAPES.items():
            with tempfile.TemporaryDirectory() as d:
                facts = os.path.join(d, "facts.json")
                with open(facts, "w", encoding="utf-8-sig") as f:      # PowerShell writes a BOM
                    f.write(js)
                path = os.path.join(d, "setup.txt")
                self.assertEqual(rv.main(["setup", "--path", path, "--facts", facts], out=io.StringIO()), 0)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                self.assertIn("Windows: Windows 11 Pro 24H2", text, shape)
                self.assertIn("Test signing: active: True", text, shape)
                self.assertNotIn("k: v", text, shape)
                self.assertIn("AirPods Pro  XX:XX:XX:XX:3F:A1", text, shape)
                self.assertNotIn(FULL, text, shape)


class HelperTests(unittest.TestCase):
    def test_tone_is_a_seamless_440_hz_loop(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tone.wav")
            rv.write_tone(path)
            with wave.open(path) as w:
                self.assertEqual(w.getframerate(), 44100)
                self.assertEqual(w.getsampwidth(), 2)
                n = w.getnframes()
            # a whole number of 440 Hz periods
            self.assertEqual((n * 440) % 44100, 0)

    def test_psm_refused_uses_the_raw_ioctl(self):
        class Api:
            def __init__(self):
                self.calls = []
                self.err = 0

            def list_device_paths(self, guid):
                return ["\\\\?\\fake"]

            def CreateFileW(self, path):
                return 7

            def DeviceIoControl(self, h, code, data, size):
                self.calls.append((code, l2cap_win.ConnectIn.from_buffer_copy(data)))
                self.err = l2cap_win.ERROR_ACCESS_DENIED
                return None

            def GetLastError(self):
                return self.err

            def CloseHandle(self, h):
                return True

        api = Api()
        out = rv.raw_connect("AA:BB:CC:DD:EE:FF", 0x0001, 672, "encrypt", api=api)
        self.assertEqual(out, {"winerror": l2cap_win.ERROR_ACCESS_DENIED})
        code, req = api.calls[0]
        self.assertEqual(code, l2cap_win.IOCTL_CONNECT)
        self.assertEqual(req.psm, 0x0001)
        self.assertEqual(req.bth_addr, 0xAABBCCDDEEFF)

    def test_pick_airpods_prefers_connected(self):
        devs = [{"name": "Keyboard", "address": "11:11:11:11:11:11", "connected": True},
                {"name": "AirPods Pro", "address": "22:22:22:22:22:22", "connected": False},
                {"name": "Henning's AirPods Pro", "address": "33:33:33:33:33:33", "connected": True}]
        self.assertEqual(rv.pick_airpods(devs)["address"], "33:33:33:33:33:33")
        self.assertEqual(rv.pick_airpods(devs[:2])["address"], "22:22:22:22:22:22")
        self.assertIsNone(rv.pick_airpods(devs[:1]))

    def test_main_next_prints_json(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            rv.main(["next", "--dir", d], out=out)
            self.assertIn('"id": 1', out.getvalue())


class FakeDriver:
    """kernel32 stand-in: one channel at a time like the driver, route as given."""

    def __init__(self, route=1, connect_out=None, incoming=()):
        self.route = route
        self.connect_out = connect_out or l2cap_win.ConnectOut(0, 0, 0, 672, 0)
        self.err = 0
        self.open_handles = set()
        self.next_handle = 10
        self.incoming = queue.Queue()
        for item in incoming:
            self.incoming.put(item)
        self.cancelled = set()
        self.writes = []

    def list_device_paths(self, guid):
        return ["\\\\?\\fake"]

    def GetLastError(self):
        return self.err

    def CreateFileW(self, path):
        self.next_handle += 1
        return self.next_handle

    def DeviceIoControl(self, h, code, data, size):
        if code == l2cap_win.IOCTL_INFO:
            return bytes(l2cap_win.Info(1, self.route, 0x112233445566))
        if code == l2cap_win.IOCTL_LOG:
            return b"log"
        if code == l2cap_win.IOCTL_DISCONNECT:
            self.open_handles.discard(h)
            return b""
        if code == l2cap_win.IOCTL_CONNECT:
            req = l2cap_win.ConnectIn.from_buffer_copy(data)
            if req.psm != l2cap_win.PSM_AAP:
                self.err = l2cap_win.ERROR_ACCESS_DENIED
                return None
            if self.open_handles:
                self.err = l2cap_win.ERROR_BUSY
                return None
            if self.connect_out.ntstatus == 0:
                self.open_handles.add(h)
            return bytes(self.connect_out)
        return b""

    def ReadFile(self, h, size):
        while h not in self.cancelled:
            try:
                item = self.incoming.get(timeout=0.01)
            except queue.Empty:
                continue
            if isinstance(item, tuple):
                self.err = item[1]
                return None
            return item
        self.err = l2cap_win.ERROR_OPERATION_ABORTED
        return None

    def WriteFile(self, h, data):
        self.writes.append(data)
        return len(data)

    def CancelIoEx(self, h):
        self.cancelled.add(h)
        return True

    def CloseHandle(self, h):
        self.open_handles.discard(h)
        return True


class StepTests(unittest.TestCase):
    ADDR = "AA:BB:CC:DD:EE:FF"

    def setUp(self):
        self.out = []
        patcher = mock.patch.object(rv, "DRIVER_WAIT_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_wrong_route_is_recorded_not_tried(self):
        api = FakeDriver(route=2)
        rec = rv.run_variant(rv._step(1), self.ADDR, api=api, out=self.out.append)
        self.assertEqual(rec["verdict"], "route_not_loaded")
        self.assertEqual(rec["route_loaded"], "bthenum")
        self.assertFalse(api.writes)

    def test_windows_refusal_is_classified(self):
        api = FakeDriver(connect_out=l2cap_win.ConnectOut(-1073741823, 5, 2, 0, 0))
        rec = rv.run_variant(rv._step(1), self.ADDR, api=api, out=self.out.append)
        self.assertEqual(rec["verdict"], "windows_refused")
        self.assertEqual(rec["result"]["stage"], 2)

    def test_psm_refused_check(self):
        step = dict(rv._step(17), route="root", mtu=672, security="encrypt")
        verdict, detail, _ = rv.check_psm_refused(step, self.ADDR, api=FakeDriver())
        self.assertEqual(verdict, "pass", detail)

    def test_second_handle_check(self):
        step = dict(rv._step(18), route="root", mtu=672, security="encrypt")
        verdict, detail, _ = rv.check_second_handle(step, self.ADDR, api=FakeDriver())
        self.assertEqual(verdict, "pass", detail)
        self.assertIn("ERROR_BUSY", detail)

    def test_disconnect_check_sees_the_read_error(self):
        api = FakeDriver(incoming=[("err", l2cap_win.ERROR_DEVICE_NOT_CONNECTED)])
        step = dict(rv._step(20), route="root", mtu=672, security="encrypt")
        with mock.patch.object(rv, "_beep"), mock.patch("airpods_mouse_controller.time.sleep"):
            verdict, detail, res = rv.check_disconnect(step, self.ADDR, api=api, out=self.out.append)
        self.assertEqual(verdict, "pass", detail)
        self.assertIn("ConnectionResetError", res["error"])
        self.assertTrue(any("CHARGING CASE" in ln.upper() for ln in self.out))

    def test_failing_step_is_recorded_and_the_round_goes_on(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(rv, "run_variant", side_effect=RuntimeError("boom")), \
                    mock.patch.object(rv, "_safe_log", return_value="log"):
                rv.main(["step", "--dir", d, "--id", "1", "--addr", self.ADDR], out=io.StringIO())
            state = rv.load_state(d)
            self.assertEqual(state["results"][0]["verdict"], "error")
            self.assertIn("boom", state["results"][0]["detail"])
            self.assertTrue(os.path.exists(os.path.join(d, "driver.log")))


if __name__ == "__main__":
    unittest.main()

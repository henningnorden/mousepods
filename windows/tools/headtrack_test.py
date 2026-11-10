# SPDX-License-Identifier: Apache-2.0
"""Live head tracking test for the Windows L2CAP driver.

Opens the AAP channel through the driver, starts tracking and prints the
head pose. Run on a Windows machine with the driver installed:

    python windows\\tools\\headtrack_test.py --addr AA:BB:CC:DD:EE:FF --seconds 20 --json out.json
"""
import argparse
import json
import math
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import airpods_mouse_controller as amc  # noqa: E402
import l2cap_win  # noqa: E402

NO_ANSWER_S = 3.0
PRINT_EVERY_S = 0.1

# NTSTATUS values at stage 3 that mean nobody answered the connect request (the
# stack timed out or gave up), not that the AirPods said no
STATUS_IO_TIMEOUT = 0xC00000B5
STATUS_CANCELLED = 0xC0000120
NO_RESPONSE_STATUSES = (STATUS_IO_TIMEOUT, STATUS_CANCELLED)


def classify(result):
    if result.get("blocks", 0) > 0:
        return "ok"
    if result.get("error"):
        return "link_lost"      # channel was open but attach, start or read failed before any block
    if result.get("winerror") == l2cap_win.ERROR_BUSY:
        return "driver_busy"    # another handle holds the driver's one channel
    stage = result.get("stage")
    if stage in (1, 2, 4, 5):
        return "windows_refused"
    if stage == 3:
        if ((result.get("ntstatus") or 0) & 0xFFFFFFFF) in NO_RESPONSE_STATUSES:
            return "no_response"
        return "airpods_refused"
    if stage == 0:
        return "refused_by_policy"
    if result.get("first_reply_ms") is None:
        return "no_answer"
    return "no_motion"


def euler_zyx(q):
    """Quaternion (w, x, y, z) -> yaw, pitch, roll in degrees (ZYX)."""
    w, x, y, z = q
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def _result(**kw):
    res = {"ok": False, "stage": None, "ntstatus": None, "bt_status": None,
           "first_reply_ms": None, "blocks": 0, "rate_hz": 0.0,
           "yaw_span_deg": 0.0, "dropped": 0, "error": None, "winerror": None}
    res.update(kw)
    return res


def _open_failure(exc):
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, l2cap_win.DriverError):
        return _result(stage=exc.stage, ntstatus=exc.ntstatus, bt_status=exc.bt_status, winerror=winerror)
    if isinstance(exc, PermissionError):
        return _result(stage=0, winerror=winerror)
    if winerror == l2cap_win.ERROR_BUSY:
        return _result(stage=0, winerror=winerror)
    return _result(stage=1)     # driver not installed or other failure before a channel exists


def run(channel_factory, addr, pid, seconds, clock=time.monotonic, out=print):
    t0 = clock()
    try:
        chan = channel_factory(addr)
    except OSError as e:
        out("open failed: %s" % e)
        return _open_failure(e)
    res = _result()
    co = getattr(chan, "connect_out", None) or {}
    client = amc.AAPClient(addr)
    blocks = 0
    first_reply = None
    t_first_block = t_last_block = None
    yaws = []
    last = None
    win_blocks, win_t = 0, t0
    error = None
    try:
        t0 = win_t = clock()    # attach sends PKT_HANDSHAKE first; replies are timed from here
        try:
            client.attach(chan, addr, pid)
            client.start_tracking()
            chan.settimeout(0.2)
        except OSError as e:
            out("start failed: %s" % e)
            error = "%s: %s" % (type(e).__name__, e)
        while error is None:
            now = clock()
            if now - t0 >= seconds:
                break
            if first_reply is None and now - t0 >= NO_ANSWER_S:
                break
            try:
                pkt = chan.recv(4096)
            except (socket.timeout, TimeoutError, BlockingIOError):
                continue
            except OSError as e:
                out("read failed: %s" % e)
                error = "%s: %s" % (type(e).__name__, e)
                break
            now = clock()
            if first_reply is None:
                first_reply = int(round((now - t0) * 1000))
            for blk in client.process(pkt):
                q = amc.quat_from_att(struct.unpack_from("<hhh", blk, 20))
                if q is None:
                    continue
                last = euler_zyx(q)
                yaws.append(last[0])
                blocks += 1
                win_blocks += 1
                if t_first_block is None:
                    t_first_block = now
                t_last_block = now
            if last is not None and now - win_t >= PRINT_EVERY_S:
                out("yaw %5.1f  pitch %5.1f  roll %5.1f  rate %d/s" % (
                    last[0], last[1], last[2], round(win_blocks / (now - win_t))))
                win_blocks, win_t = 0, now
    finally:
        try:
            client.close()
        finally:
            try:
                chan.close()
            except OSError:
                pass
    span = (t_last_block - t_first_block) if blocks > 1 else 0.0
    res.update(
        first_reply_ms=first_reply, blocks=blocks,
        rate_hz=round((blocks - 1) / span, 1) if span > 0 else 0.0,
        yaw_span_deg=round(max(yaws) - min(yaws), 1) if yaws else 0.0,
        error=error, dropped=int(getattr(chan, "dropped", 0) or 0),
        ntstatus=co.get("ntstatus"), bt_status=co.get("bt_status"))
    res["ok"] = blocks > 0
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--addr", required=True, help="AirPods address AA:BB:CC:DD:EE:FF")
    ap.add_argument("--pid", default=None,
                    help="product id; default None takes the SensorDataWX path (AirPods Pro 2); AirPods 3 is 2013")
    ap.add_argument("--mtu", type=int, default=2582)
    ap.add_argument("--security", default="encrypt", choices=sorted(l2cap_win.SECURITY))
    ap.add_argument("--seconds", type=float, default=20,
                    help="run time in seconds, including the ~0.6 s start sequence")
    ap.add_argument("--json", default=None, help="write the result here")
    a = ap.parse_args(argv)

    def factory(addr):
        return l2cap_win.open_seqpacket(addr, l2cap_win.PSM_AAP, a.mtu, 8, security=a.security)

    res = run(factory, a.addr, a.pid, a.seconds)
    res["verdict"] = classify(res)
    print(json.dumps(res))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

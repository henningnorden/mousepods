# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Step 5: dynamic test of one realtime ML service (17 SPAM or 18 BMX_RAW). One attempt, no retry.

    python -m garmin.probe_stream --service 18 [--seconds 20] [--address AA:BB:..]
    python -m garmin.probe_stream --analyze captures/private/garmin/svc18_XXXX.jsonl

Only run it for a service that probe_services.py reported as supported.
Messages sent, all with the structure Gadgetbridge uses for every REALTIME_* service:
  REGISTER_ML_REQ(service)  ->  0x01 (start) on the handle  ->  ... dump ...  ->  0x00 (stop)
If the registration is refused, or the watch drops the link, we stop and report.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
from collections import Counter

from . import glink
from garmin_ble.frames import FrameKind


def analyze(rows, handle=None):
    if handle is None:
        notes = [r for r in rows if "note" in r and r["note"].startswith("handle ")]
        handle = int(notes[-1]["note"].split()[1]) if notes else None
    pk = [(r["t"], bytes.fromhex(r["hex"])) for r in rows
          if r.get("dir") == "rx" and glink.rx_handle(bytes.fromhex(r["hex"])) == handle]
    if not pk:
        return {"packets": 0}
    t = [p[0] for p in pk]
    body = [p[1][1:] for p in pk]
    dt = [b - a for a, b in zip(t, t[1:])]
    res = {"handle": handle, "packets": len(pk), "sizes": dict(Counter(len(b) for b in body)),
           "duration_s": round(t[-1] - t[0], 3),
           "packet_rate_hz": round((len(t) - 1) / (t[-1] - t[0]), 2) if len(t) > 1 else None,
           "arrival_dt_ms_median": round(st.median(dt) * 1000, 2) if dt else None,
           "first_packets_hex": [b.hex() for b in body[:12]]}
    L = Counter(len(b) for b in body).most_common(1)[0][0]
    same = [b for b in body if len(b) == L]
    if len(same) >= 5:
        cols = list(zip(*same))
        res["byte_std"] = [round(st.pstdev(c), 1) for c in cols]
        res["constant_bytes"] = {i: f"{c[0]:02x}" for i, c in enumerate(cols) if len(set(c)) == 1}
        # candidate u16 LE counters (timestamps): monotonic-ish increasing columns
        cand = []
        for i in range(L - 1):
            v = [b[i] | b[i + 1] << 8 for b in same]
            d = [(y - x) % 65536 for x, y in zip(v, v[1:])]
            if d and st.median(d) > 0 and sum(x == st.median(d) or abs(x - st.median(d)) <= 2 for x in d) > 0.8 * len(d):
                cand.append({"offset": i, "median_step": st.median(d)})
        res["u16_counter_candidates"] = cand
    return res


async def run(service, seconds, address):
    log = glink.FrameLog(f"svc{service}")
    print(f"log -> {log.path}")
    async with glink.session(address) as watch:
        watch.on_frame(log)
        print(f"connected: {watch.info}")
        log.note(f"register service {service}")
        ok = await watch._register_service(service)
        if not ok:
            print(f"service {service}: registration refused or unanswered. Stopping (no retry).")
            log.note("refused")
            return
        h = watch._handles[service]
        log.note(f"handle {h}")
        print(f"service {service} -> handle {h}; sending start 0x01, dumping {seconds} s")
        n = 0

        def show(t, d, hh, raw):
            nonlocal n
            if d == "rx" and glink.rx_handle(raw) == h:
                n += 1
                if n <= 30:
                    print(f"  {t:9.4f}  {len(raw) - 1:3d} B  {raw[1:].hex()}")
        log.listeners.append(show)
        await watch._write(b"\x01", FrameKind.CONTROL, handle=h)
        for _ in range(seconds * 10):
            await asyncio.sleep(0.1)
            if not watch.is_connected:
                print("link dropped. Stopping.")
                log.note("dropped")
                break
        if watch.is_connected:
            log.note("stop 0x00")
            await watch._write(b"\x00", FrameKind.CONTROL, handle=h)
            await asyncio.sleep(0.5)
        print(f"{n} packets on handle {h}")
    res = analyze(glink.load(log.path), h)
    out = log.path.with_suffix(".summary.json")
    out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", type=int, choices=(3, 17, 18))
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--address")
    ap.add_argument("--analyze")
    a = ap.parse_args()
    if a.analyze:
        print(json.dumps(analyze(glink.load(a.analyze)), indent=2))
    else:
        asyncio.run(run(a.service, a.seconds, a.address))

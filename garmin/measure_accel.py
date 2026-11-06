# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Reference measurement of service 16 (REAL_TIME_ACCELEROMETER).

    python -m garmin.measure_accel [--address AA:BB:..] [--seconds 60]
    python -m garmin.measure_accel --analyze captures/private/garmin/accel_XXXX.jsonl

Streams the accelerometer, logs every raw frame, then computes:
  real sample rate (watch clock), packet rate, arrival jitter (host clock), BLE notification
  sizes, sample format/resolution, and a latency estimate.

Packet layout (Gadgetbridge CommunicatorV2, after the 1-byte ML routing byte), 16 bytes:
  u16 LE header: bits 0-12 = watch timestamp in ms (wraps at 8192), bits 13-15 = sample count
  then 9 signed 12-bit values packed 2 per 3 bytes = 3 samples (x, y, z), 256 counts per g.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st

from . import glink


def s12(raw: int) -> int:
    return raw - 4096 if raw & 0x800 else raw


def decode(payload: bytes):
    """payload = 16 bytes after the routing byte -> (ts_ms_mod8192, n, [(x,y,z), ...])."""
    hdr = payload[0] | payload[1] << 8
    ts, n = hdr & 0x1FFF, hdr >> 13
    vals = []
    for i in range(9):
        b = 2 + (i // 2) * 3
        raw = (payload[b] | (payload[b + 1] & 0x0F) << 8) if i % 2 == 0 else \
              (payload[b + 1] >> 4 | payload[b + 2] << 4)
        vals.append(s12(raw))
    return ts, n, [tuple(vals[3 * k:3 * k + 3]) for k in range(min(n, 3))]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def analyze(rows, handle=None):
    if handle is None:   # the handle whose decoded text is accelerometer telemetry
        hs = [r["h"] for r in rows if r.get("dec") and str(r["dec"]).startswith("accelerometer")]
        handle = max(set(hs), key=hs.count) if hs else None
    pk = [r for r in rows if r.get("dir") == "rx" and r.get("h") == handle]
    if len(pk) < 10:
        return {"error": f"only {len(pk)} accelerometer packets"}
    sizes = {}
    arr, wts, samples, counts = [], [], [], {}
    unwrap, last = 0, None
    for r in pk:
        raw = bytes.fromhex(r["hex"])
        sizes[len(raw)] = sizes.get(len(raw), 0) + 1
        if len(raw) != 17:
            continue
        ts, n, smp = decode(raw[1:])
        if last is not None and ts < last:
            unwrap += 8192
        last = ts
        arr.append(r["t"]); wts.append((ts + unwrap) / 1000.0)
        counts[n] = counts.get(n, 0) + 1
        samples.extend(smp)
    span_w = wts[-1] - wts[0]
    d_arr = [b - a for a, b in zip(arr, arr[1:])]
    d_w = [b - a for a, b in zip(wts, wts[1:])]
    n_per = max(counts, key=counts.get)
    # Latency proxy: arrival minus watch time, relative to its minimum (after removing clock drift).
    drift = (arr[-1] - arr[0]) / span_w if span_w else 1.0
    off = [a - w * drift for a, w in zip(arr, wts)]
    base = min(off)
    queue = [(o - base) * 1000 for o in off]
    sample_dt = st.median(d_w) / n_per
    ax = list(zip(*samples))
    return {
        "packets": len(arr),
        "notification_sizes_bytes": sizes,
        "samples_per_packet": counts,
        "packet_rate_hz_watch_clock": round((len(arr) - 1) / span_w, 2),
        "sample_rate_hz": round((len(arr) - 1) * n_per / span_w, 2),
        "watch_dt_ms": {"median": round(st.median(d_w) * 1000, 1), "min": round(min(d_w) * 1000, 1),
                        "max": round(max(d_w) * 1000, 1)},
        "arrival_dt_ms": {"mean": round(st.mean(d_arr) * 1000, 1), "std": round(st.pstdev(d_arr) * 1000, 1),
                          "p1": round(pct(d_arr, 1) * 1000, 1), "p99": round(pct(d_arr, 99) * 1000, 1),
                          "max": round(max(d_arr) * 1000, 1)},
        "gaps_over_2x": sum(d > 2 * st.median(d_w) for d in d_w),
        "clock_ratio_host_per_watch": round(drift, 6),
        "extra_delay_vs_best_packet_ms": {"median": round(st.median(queue), 1),
                                          "p95": round(pct(queue, 95), 1), "max": round(max(queue), 1)},
        "batching_delay_oldest_sample_ms": round((n_per - 1) * sample_dt * 1000, 1),
        "format": "3 x (x,y,z) signed 12-bit, 256 counts/g -> resolution 3.9 mg, range +/-8 g",
        "axis_mean_g": [round(st.mean(a) / 256, 3) for a in ax],
        "axis_std_g": [round(st.pstdev(a) / 256, 4) for a in ax],
        "distinct_values_per_axis": [len(set(a)) for a in ax],
    }


async def run(address, seconds):
    from garmin_ble import metrics
    log = glink.FrameLog("accel")
    print(f"log -> {log.path}")
    async with glink.session(address) as watch:
        watch.on_frame(log)
        print(f"connected: {watch.info}")
        log.note(f"info {watch.info}")
        n = 0

        async def consume():
            nonlocal n
            async for _ in watch.stream(metrics.ACCELEROMETER):
                n += 1

        log.note("subscribe ACCELEROMETER (service 16, start byte 0x01)")
        task = asyncio.ensure_future(consume())
        for s in range(seconds):
            await asyncio.sleep(1)
            if s % 10 == 9:
                print(f"  {s + 1:3d} s  {n} samples")
            if task.done():
                break
        task.cancel()
        log.note("stop")
        handle = watch._handles.get(16)
    res = analyze(glink.load(log.path), handle)
    out = log.path.with_suffix(".summary.json")
    out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    print(f"summary -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--address")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--analyze")
    a = ap.parse_args()
    if a.analyze:
        print(json.dumps(analyze(glink.load(a.analyze)), indent=2))
    else:
        asyncio.run(run(a.address, a.seconds))

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Step 3: ask the REGISTRATION service (4) which ML services the watch supports.

    python -m garmin.probe_services [--address AA:BB:..]

Only the documented read requests are sent (Gadgetbridge garmin-protocol doc):
  0x00 SUPPORTED_PROTOCOLS (bitmap, bit n = service n)
  0x01 ADVERTISING_DATA, 0x02 MULTI_LINK_VERSION, 0x03 PRODUCT_NUMBER (+fw, +unit id)
0x04 IDENTITY_ADDRESS is skipped. The unit id is a unique identifier: it is kept out of the
printed report (only the raw log has it, and captures/private/ is git-ignored).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct

from . import glink
from garmin_ble.frames import FrameKind

NAMES = {1: "GFDI", 2: "NFC", 3: "HEALTH_SDK", 4: "REGISTRATION", 5: "CONNEXT", 6: "REALTIME_HR",
         7: "REALTIME_STEPS", 8: "REALTIME_CALORIES", 9: "REALTIME_FLOORS", 10: "REALTIME_INTENSITY",
         12: "REALTIME_HRV", 13: "REALTIME_STRESS", 15: "ECHO", 16: "REALTIME_ACCELEROMETER",
         17: "REALTIME_SPAM", 18: "REALTIME_BMX_RAW", 19: "REALTIME_SPO2", 20: "REALTIME_BODY_BATTERY",
         21: "REALTIME_RESPIRATION", 22: "KEEP_ALIVE", 26: "REALTIME_ACTIVE_TIME"}


def bitmap(data: bytes):
    return [8 * i + b for i, byte in enumerate(data) for b in range(8) if byte >> b & 1]


async def run(address):
    log = glink.FrameLog("services")
    print(f"log -> {log.path}")
    answers: dict[int, bytes] = {}
    async with glink.session(address) as watch:
        watch.on_frame(log)
        print(f"connected: {watch.info}")
        log.note("register REGISTRATION (4)")
        if not await watch._register_service(4):
            print("REGISTRATION refused (the watch may only offer the legacy characteristic). Stopping.")
            return
        h = watch._handles[4]
        print(f"REGISTRATION handle = {h}")
        log.listeners.append(lambda t, d, hh, raw: d == "rx" and glink.rx_handle(raw) == h
                             and len(raw) >= 2 and answers.setdefault(raw[1], raw[2:]))
        for req in (0, 1, 2, 3):
            log.note(f"registration request 0x{req:02x}")
            await watch._write(bytes([req]), FrameKind.CONTROL, handle=h)
            for _ in range(30):
                if req in answers:
                    break
                await asyncio.sleep(0.1)
    rep = {"model_hint": str(watch.info.name)}
    if 0 in answers:
        svc = bitmap(answers[0])
        rep["supported_services"] = {s: NAMES.get(s, "?") for s in svc}
        rep["has_17_SPAM"], rep["has_18_BMX_RAW"], rep["has_3_HEALTH_SDK"] = 17 in svc, 18 in svc, 3 in svc
    if 1 in answers:
        rep["advertising_data"] = list(answers[1])
    if 2 in answers and len(answers[2]) >= 3:
        rep["multi_link_version"] = ".".join(map(str, answers[2][:3]))
    if 3 in answers and len(answers[3]) >= 4:
        prod, fw = struct.unpack_from("<HH", answers[3])
        rep["product_number"], rep["firmware"] = prod, f"{fw / 100:.2f}"
    rep["raw_answers_hex"] = {f"0x{k:02x}": v.hex() for k, v in answers.items() if k != 3}
    out = log.path.with_suffix(".summary.json")
    out.write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=2))
    print(f"summary -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--address")
    asyncio.run(run(ap.parse_args().address))

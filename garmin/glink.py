# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Shared helpers: connect to the watch and log every BLE frame (TX and RX) to JSONL.

Each line: {"t": seconds since start (perf_counter), "wall": unix time, "dir": "tx"|"rx",
            "kind": ..., "h": handle, "hex": raw bytes, "dec": decoded text or null}
The log is the ground truth: every analysis can be redone from it.

garmin-ble (AGPL-3.0-only, installed separately: requirements-garmin.txt) is imported only inside
the functions that talk to the watch, so offline analysis and the tests work without it.
Logs go to captures/private/garmin/ (git-ignored: they contain the watch unit id).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CAPTURES = Path(__file__).resolve().parent.parent / "captures" / "private" / "garmin"


class FrameLog:
    def __init__(self, name: str):
        CAPTURES.mkdir(parents=True, exist_ok=True)
        self.path = CAPTURES / f"{name}_{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.f = open(self.path, "w")
        self.t0 = time.perf_counter()
        self.listeners = []          # callables (t, direction, handle, raw)

    def __call__(self, fr) -> None:
        t = time.perf_counter() - self.t0
        d = fr.direction.value
        self.f.write(json.dumps({"t": round(t, 6), "wall": round(time.time(), 6), "dir": d,
                                 "kind": fr.kind.value, "h": fr.handle, "hex": fr.raw.hex(),
                                 "dec": fr.decoded}) + "\n")
        self.f.flush()
        for cb in self.listeners:
            cb(t, d, fr.handle, fr.raw)

    def note(self, text: str) -> None:
        """Free-form marker in the log (what we are about to do)."""
        self.f.write(json.dumps({"t": round(time.perf_counter() - self.t0, 6), "note": text}) + "\n")
        self.f.flush()


def session(address: str | None, reconnect: str = "off"):
    """No auto-reconnect by default: if the watch drops, we stop and report (safety rule)."""
    from garmin_ble import Watch
    if address:
        # An already-connected (bonded) watch no longer advertises, so bleak's scan-by-address
        # fails. Hand bleak the BlueZ object path directly instead.
        from bleak.backends.device import BLEDevice
        path = "/org/bluez/hci0/dev_" + address.upper().replace(":", "_")
        return Watch.at(BLEDevice(address.upper(), None, {"path": path, "props": {"Address": address.upper(), "Alias": "garmin", "Name": "garmin"}}), reconnect=reconnect)
    return Watch.discover(reconnect=reconnect)


def rx_handle(raw: bytes) -> int | None:
    from garmin_ble.protocol import mlr
    d = mlr.decode_packet(raw)
    return d.handle if isinstance(d, mlr.MlrPacket) else None


def load(path):
    return [json.loads(line) for line in open(path) if line.strip()]

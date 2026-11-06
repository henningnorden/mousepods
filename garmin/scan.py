# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""python -m garmin.scan — list nearby BLE devices that look like Garmin watches (name, address, RSSI). Read-only."""
import asyncio

from bleak import BleakScanner

HINTS = ("garmin", "fenix", "forerunner", "venu", "vivo", "instinct", "epix", "enduro",
         "tactix", "quatix", "marq", "descent", "approach", "lily", "edge")


async def main():
    found = await BleakScanner.discover(timeout=10.0, return_adv=True)
    rows = []
    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or ""
        garmin = any(h in name.lower() for h in HINTS) or 0x0087 in (adv.manufacturer_data or {})
        rows.append((garmin, adv.rssi, addr, name))
    for garmin, rssi, addr, name in sorted(rows, key=lambda r: (not r[0], -r[1])):
        print(f"{'GARMIN ' if garmin else '       '}{addr}  {rssi:4d} dBm  {name}")


if __name__ == "__main__":
    asyncio.run(main())

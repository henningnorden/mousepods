# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Matteo Gaudioso (starman-tech) — https://github.com/starman-tech/mousepods
"""Garmin watch as a mouse (optional MousePods module), see docs/GARMIN.md.

  garmin_mouse   engine (Calibrator, TiltEngine, Renderer, Pointer, App) + standalone Tk window
  service        GarminService: runs App in a background thread for the web page « Garmin »
  glink          BLE session and JSONL frame log
  measure_accel, probe_services, probe_stream, scan   reverse-engineering tools
  ciq_imurate/   Connect IQ app source (measures the on-watch sensor rate)

The BLE link needs garmin-ble (AGPL-3.0-only), installed separately with
`pip install -r requirements-garmin.txt`. Nothing in this package copies garmin-ble code; it is
imported only when connecting to a watch, so the rest of MousePods works without it.
"""

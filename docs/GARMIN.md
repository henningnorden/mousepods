# Garmin watch as a mouse

MousePods can also use a **Garmin watch** as a mouse: tilt your wrist to move the cursor, double-tap the watch to
click. It runs inside the MousePods app (page **Garmin**) or as a small standalone window. This page documents
how the watch streams its accelerometer over Bluetooth Low Energy, what was tried to get more (gyroscope, higher
rate), and what was measured.

```
 Garmin watch ── BLE, ML service 16 (3 samples / 120 ms) ──▶ garmin-ble ──▶ Calibrator / TiltEngine ──▶ Renderer 120 Hz ──▶ /dev/uinput
                                                              (AGPL-3.0)      wrist tilt → velocity or position        "Garmin Mouse"
```

Code: [`garmin/`](../garmin/) · tests: [`tests/test_garmin_mouse.py`](../tests/test_garmin_mouse.py).
Watch used for every number below: **quatix 7** (product 3906, firmware 27.18, Multi-Link 1.2.2).

> **Licence note.** The Bluetooth link uses [garmin-ble](https://github.com/gwerneckp/garmin-ble), which is
> **AGPL-3.0-only**. It is *not* included in MousePods and no code from it is copied: it is an optional dependency,
> installed separately (`requirements-garmin.txt`) and imported only when a watch is connected. MousePods itself stays
> Apache-2.0. If you **distribute** MousePods together with garmin-ble (for example a package or image that bundles
> both), or offer the combination to others over a network, the combined work falls under the AGPL-3.0 and you must
> follow it (including offering the corresponding source). Apache-2.0 code may be combined into an AGPL-3.0 work, not
> the other way round. Gadgetbridge (also AGPL-3.0) was used only as protocol documentation; none of its code is here.

## Use it

### 1. Pair the watch (once)

The watch insists on a BLE **bond**. Disconnect it from the phone first (switch the phone's Bluetooth off): a Garmin
watch accepts only one BLE client at a time.

```bash
bluetoothctl
  scan le                 # or: .venv/bin/python -m garmin.scan
  pair XX:XX:XX:XX:XX:XX  # confirm the code on the watch if asked
  trust XX:XX:XX:XX:XX:XX
  scan off
  quit
```

Once bonded, the watch **stops advertising**: a normal scan no longer finds it. MousePods connects through its BlueZ
object path (`/org/bluez/hci0/dev_XX_XX_…`) instead, so you only need its address
(`bluetoothctl devices Paired`, or the **Appairées…** button on the Garmin page).

### 2. Install the optional module

```bash
.venv/bin/pip install -r requirements-garmin.txt     # garmin-ble (AGPL-3.0) + bleak
```

Without it, the rest of MousePods works as before and the Garmin page shows « module Garmin non installé ».

### 3. Run

* **In the app**: start MousePods as usual, open the **Garmin** page, enter the address, press **Connecter**.
  Optional: « Connexion au démarrage » connects at every start.
* **Standalone window** (Tk, no AirPods needed):

```bash
.venv/bin/python -m garmin.garmin_mouse --address XX:XX:XX:XX:XX:XX   # remembered afterwards
.venv/bin/python -m garmin.garmin_mouse --calibrate                   # redo the 5-pose calibration
.venv/bin/python -m garmin.garmin_mouse --mode pointeur
```

The address and settings are kept in `garmin_mouse.json`, the calibration in `garmin_calib.json` (both git-ignored).
Only one of the two (app or window) can be connected at a time.

### 4. Calibrate, then use

The first connection walks through **5 poses**, each validated by holding it still about 1 s: neutral (hand resting
like on a mouse), tilt **right**, tilt **left**, hand **up**, hand **down**. From your own poses the engine learns the
two tilt axes, whatever the watch orientation on the wrist, and your comfortable range in each direction. The page
shows the prompt, step n/5, a hold-progress ring and a live dot that follows the wrist; a pose on the wrong side is
refused with a message.

At every later connection only the neutral pose is re-learnt (1 s still). **Recentrer** does that again,
**Recalibrer tout** redoes the 5 poses, **Pause** freezes the cursor.

| Gesture | Effect |
|---|---|
| tilt beyond the dead zone | cursor moves (joystick: speed grows with tilt; pointeur: tilt = position on screen) |
| double tap on the watch | left click (the cursor freezes 0.35 s and the small jolt of the tap is undone) |
| arm dropped (large tilt) | pause |
| resting a few degrees off neutral | absorbed slowly (auto-center) |

Settings on the page (saved, applied live): max speed, dead zone, smoothing, tap force, mode, axis inversion,
auto-center.

### Limits

* **Accelerometer only, 25 Hz.** The only real-time motion stream the watch exposes is its accelerometer. No gyroscope
  in real time (see below), so the engine measures **tilt** (the direction of gravity), not rotation around the
  vertical. Turning the wrist flat on the table does nothing.
* **Latency.** About 80 ms of batching inside the watch, plus a median 130 ms (p95 240 ms) of BLE bursting. The
  120 Hz renderer hides the 25 Hz steps but cannot remove the delay. Fine for pointing, not for games.
* One BLE client: the phone must be disconnected.

## Findings

### Tools

| Module | Step | What it sends to the watch |
|---|---|---|
| `python -m garmin.scan` | 1 | nothing (passive BLE scan) |
| `python -m garmin.measure_accel` | 2 | CLOSE_ALL, REGISTER(GFDI), REGISTER(16), `0x01` / `0x00` |
| `python -m garmin.probe_services` | 3 | REGISTER(4), then the read requests `0x00` to `0x03` |
| `python -m garmin.probe_stream --service 17\|18` | 5 | REGISTER(n), `0x01`, then `0x00`. One attempt, no retry |
| `garmin/ciq_imurate/` | 6 | Connect IQ app that shows `getMaxSampleRate*` and the rate it really receives |

Every frame sent and received is logged to `captures/private/garmin/*.jsonl` (`perf_counter` timestamp, raw hex);
`--analyze file.jsonl` re-runs an analysis offline. These logs are **not published**: they contain the watch's unit id
and address.

### Services the watch announces

REGISTRATION (service 4) request `0x00` returns a bitmap of supported ML services. On the quatix 7:

1 GFDI, 2 NFC, 4 REGISTRATION, 6 HR, 7 STEPS, 8 CALORIES, 9 FLOORS, 10 INTENSITY, 12 HRV, 13 STRESS, 15 ECHO,
**16 ACCELEROMETER**, **17 SPAM**, 19 SPO2, 20 BODY_BATTERY, 21 RESPIRATION, 22 KEEP_ALIVE, **24, 25, 27, 28**
(undocumented).

* **18 BMX_RAW is not announced**, nor 3 HEALTH_SDK.
* 25 is TRAINING_READINESS according to the Garmin Connect app; 24, 27 and 28 are named neither in the app nor in
  Gadgetbridge.
* The link runs at MTU 23.

### Service 16, real-time accelerometer

* One notification = 1 ML routing byte + 16 bytes.
* `u16 LE` header: bits 0-12 = watch timestamp in ms (wraps every 8192 ms), bits 13-15 = sample count (always 3).
* Then 9 signed **12-bit** values, 2 per 3 bytes, interleaved x, y, z: 3 samples.
* Scale **256 counts/g**: 3.9 mg resolution, ±8 g range. |a| = 1.00 g at rest, z = −1 g face up.
* **No rate parameter.** Start is the single byte `0x01`, stop `0x00`. Neither Gadgetbridge nor garmin-ble sends an
  interval or batch size, and none was found.

Reference measurement (60 s):

| | |
|---|---|
| sample rate | **24.9 Hz** (3 samples per packet, one packet every 120 ms on the watch clock, 119–122 ms), no gaps |
| notification size | 17 bytes, a single format |
| batching delay | the oldest sample of a packet is 80 ms old when the packet leaves |
| arrival jitter | packets arrive in bursts: inter-arrival σ = 65 ms, p1 = 0.3 ms |
| extra delay vs the best packet | median 129 ms, p95 243 ms, max 339 ms |

The bursts are the signature of a long BLE connection interval with peripheral latency.

### Service 17 SPAM: a throughput test, not IMU data

One 20 s attempt:

* 19-byte packets: a `u16 LE` counter that increments by 1, then zeros. Byte 16 is `0x48`, then switches to `0x24`
  at t ≈ 14.4 s (maybe a rate or size parameter).
* **44 packets/s** (~840 useful bytes/s at MTU 23), in bursts.
* The stop byte `0x00` did not stop it: 160 more packets arrived in 3 s, until disconnection. The watch did not crash.
* Useful anyway: the link can carry at least 44 packets/s, so 100 Hz of 12-bit accelerometer (~450 B/s) would fit.
  The limit of service 16 is the watch firmware, not the radio.

### Service 18 BMX_RAW and the gyroscope

* 17 SPAM and 18 BMX_RAW appear **only in the table** of the Gadgetbridge protocol notes. They are absent from the
  `Service` enum of Gadgetbridge's `CommunicatorV2`, from garmin-ble, and from any other code: no format, no start
  message, no callback.
* The Forerunner 245 in those notes announces `1, 4, 6, 7, 8, 10, 12, 13, 16, 19, 20, 21, 22`: neither 17 nor 18.
* The quatix 7 does not announce 18 either, so it was not probed. **No real-time gyroscope path was found.**

### Garmin Connect 5.30.1 (Android app, decompiled with jadx, `classes7.dex` and `classes11.dex` only)

* The deprecated enum of ML services lists 1-22 and 26 (plus 11 REAL_TIME_DUMMY and 14 AUTH_STATUS). **16
  ACCELEROMETER, 17 SPAM and 18 BMX_RAW are marked by jadx as "Fake field, exist only in values array"**: declared,
  never used.
* A mapping class links these services to legacy characteristic UUIDs; SPAM is there, unused too.
* The current real-time list is STEPS 7, **PUSHES 29**, HRV 12, CALORIES 8, ASCENT 9, INTENSITY 10, HR 6, STRESS 13,
  ACCELEROMETER 16, SPO2 19, BODY_BATTERY 20, RESPIRATION 21, ACTIVE_TIME 26, **TRAINING_READINESS 25**: no SPAM, no
  BMX_RAW.
* In the real-time dispatcher, **ACCELEROMETER falls into `default: return`**: Garmin Connect ignores the
  accelerometer.
* No native IMU library: `libgolf-swing.so` only renders recorded swings in 3D, `libreliable-ml.so` handles the MLR
  transport.
* Conclusion: the phone app knows no fast IMU path. A format for 17/18 could only come from the watch itself.

### Other paths, not real time

* **Logging (Health SDK)**: Gadgetbridge knows the FIT messages `164 gyroscope_data`, `165 accelerometer_data`,
  `167 three_d_sensor_calibration`, `376 hsa_gyroscope_data` and `389 hsa_configuration_data`. That is "record on the
  watch, then download": high rate, but not live.
* **Connect IQ app** (`garmin/ciq_imurate/`): an on-watch app can read the sensors through
  `Sensor.registerSensorDataListener` at up to `getMaxSampleRate()` (gyroscope included on watches that have one). The
  app shows the official ceilings and the rate it really receives. Sending that stream to the PC in real time would
  need a custom transmit path (Connect IQ `Communications` goes through the phone app); not done.

## Engine

`garmin/garmin_mouse.py`, pure logic, tested without hardware:

* `decode16`: service-16 payload → 3 samples (x, y, z) in g.
* `Calibrator`: the 5 poses; a pose counts once the unit gravity vector is still (std < 0.02 over 5 samples) for 25
  samples, and a directional pose must be ≥ 10° from neutral, on the side opposite to its pair. Gives `g0` (neutral),
  the axes `ex`/`ey` (Gram-Schmidt from your right-left and up-down poses) and your range in each direction (≥ 8°).
* `TiltEngine`: tilt angles on `ex`/`ey` through a One-Euro filter, as a fraction of your range; dead zone + power
  curve → velocity (joystick) or position (pointeur); pause when tilted past `max(pause_deg, 1.3 × range)`;
  auto-center; taps = a jolt of the high-passed acceleration above `tap_g` out of a calm wrist, two within 0.5 s =
  click.
* `Renderer`: 120 Hz, exponential smoothing of the velocity or position, undo of the tap jolt.
* `App`: BLE on an asyncio thread; used by the Tk window and by `garmin/service.py` (web page, `/api/garmin`).

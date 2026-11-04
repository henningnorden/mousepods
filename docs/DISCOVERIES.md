# New findings: AirPods (3rd generation) motion sensors over AACP

**Head tracking and raw IMU data from AirPods 3 (A2564 / A2565, Bluetooth product ID `0x2013`) on a non-Apple host, through Apple's RemoteHID protocol (HID over AACP).**

Author: Matteo Gaudioso ([@starman-tech](https://github.com/starman-tech)) · reverse-engineered with the help of Claude (Anthropic) · first published November 2026 · licensed [CC BY 4.0](LICENSE-CC-BY-4.0.txt) (credit required).
Tested on: AirPods 3, firmware marketing version `6296592`, Ubuntu 24.04, BlueZ 5.72, Python 3.12.

> **Scope of the "new" claim.** On 4 November 2026 we searched LibrePods (code, docs, issues and PRs), the
> pabloaul/apple-wireshark dissector, mutix, ntpods, librepods-rs, pods-head-tracker, airpods-head-track (both forks),
> aap-head-tracker, MagicPods, GitHub code search and the general web. None of them documented any of the items marked
> **NEW** below. Each such project lists AirPods 3 as "untested", "expected" or "not supported" for head tracking.
> If you know of earlier work, please open an issue, and we will credit it.

---

## Summary

| # | Finding | Status | New? |
|---|---|---|---|
| 1 | AirPods 3 stream head-tracking / IMU data to a Linux PC | ✅ verified on hardware | **NEW** |
| 2 | AirPods 3 **ignore** the SensorDataWX protocol used by AirPods Pro 2/3/4 and AirPods 4; they use the legacy RTBuddy `0x800` channel | ✅ verified | **NEW** |
| 3 | The `0x800` channel carries **Apple RemoteHID** (HID over AACP), with a complete wire format | ✅ verified (format taken from iOS pseudocode, cross-checked against clone firmware and hardware) | **NEW** (first public write-up) |
| 4 | Start sequence: **HostConnect → DeviceConnect → SetReport(feature 1 = report interval in µs)** | ✅ verified | **NEW** |
| 5 | The AirPods 3 sensor catalog: 8 HID devices with fixed IDs, and their decoded report descriptors | ✅ verified | **NEW** |
| 6 | The L2CAP **receive MTU hides every motion sensor** at the Linux default (672) | ✅ verified | **NEW for AirPods 3** (seen before only for heart rate on AirPods Pro 3, by ntpods) |
| 7 | Sample rate set in µs; up to **~210 Hz** (vs 25 Hz in public head trackers) | ✅ measured | **NEW** |
| 8 | Raw `accel` / `gyro` HID sensors (int32, 10⁻⁵ units) next to the fused `devmotion6` stream | ✅ verified | **NEW** |
| 9 | Units of the 58-byte motion block: gyro ≈ 0.122 °/s per LSB, gravity and user acceleration ≈ 1000 per g | ✅ measured | Partly new (layout was known, the scales were not) |
| 10 | Power behaviour: sensors **off in the case**, fused stream **frozen** when a bud lies still out of the ear, streams **cancelled** when the primary bud changes, proximity flapping when a bud is held | ✅ observed | **NEW** |
| 11 | The "per-connection service ID" of the `0x800` announcements is a **generation counter**, and the "constant bytes" are the sub-packet header | ✅ verified | **NEW** (corrects an open question in the dissector) |

---

## 1. AirPods 3 motion data on a non-Apple host

With the sequence in §4, AirPods 3 send fused head-tracking samples (attitude quaternion, rotation rate, user
acceleration, gravity) to a plain Linux PC over BR/EDR. No root, no Apple device, no kernel driver.

- Evidence: `captures/airpods3_motion_capture.json` (4 187 samples recorded while turning the head left/right, up/down
  and tilting it), and `captures/airpods3_glide_raw2.json` (raw RemoteHID frames, including the `accel` and `gyro` streams).
- Used in practice: `airpods_mouse_controller.py` turns the stream into a mouse / pointer.

## 2. AirPods 3 do not speak SensorDataWX

Every published head-tracking tool starts the stream with a SensorDataWX command (RTBuddy descriptor `0x00100000`,
protobuf `ServiceSetting{service, 2, 01 + u32 interval}`), on service 14 (ACTIVITY) or 16 (DEVMOTION6).

AirPods 3 **never answer** any of these: no data, and not even the `idk_start_ack` (field 9) that newer AirPods send.
We tried service IDs 1–31, 79 and 82, `two` = 0/1/2, both known start packets, the "14 then 16" order, `request_all_descriptors`,
handshake minor version 3, the `0x29` country code, the `09 00 06 01` connection takeover, and an Apple PnP DeviceID with a
re-pair. Their capabilities reply (opcode `0x02`) lists `0x40` (spatial audio) but **not `0xE0`** ("sensor data v2").

## 3. The `0x800` channel is Apple RemoteHID (HID over AACP)

AirPods 3 announce their sensors on the legacy RTBuddy **Sensor** channel (descriptor `0x00000800`). This channel carries
Apple's **RemoteHID** protocol, implemented on iOS in `RemoteHID.framework` (`HIDRemoteDeviceAACPServer`). Each sensor is a
virtual HID device: it publishes a HID report descriptor and is driven with HID Get/SetReport.

The full wire format is in [REMOTEHID-PROTOCOL.md](REMOTEHID-PROTOCOL.md). In short:

```
04 00 04 00 17 00 | 00 08 00 00 | len u16 LE | 00 gen 00 side*2 | sub-packet | sub-packet | …
sub-packet = u32 LE header: bits 0-6 deviceID · 7-16 length (incl. header) · 17-19 kind · 20 timestamp · 21 response · 22 status
kind: 0 Connect · 1 Disconnect · 2 InputReport · 3 SetReport · 4 GetReport · 5 DeviceConfirmConnect
```

Sources and cross-checks:
- iOS 26.1 `RemoteHID.framework` pseudocode (the public restore dump `EthanArbuckle/iPhone18-3_26.1_23B85_Restore`): encoders and the `endpointPacketHandler` switch.
- Static disassembly of a third-party AirPods-clone firmware (`spacedouut/OpenHuilian`, `FUN_00ca78b4`). It expects exactly `08 05 06 00 | 02 | 04 01 00 00 00`, which decodes as **SetReport(device 8, feature, report 4 = 1)** with the same header layout.
- Real AirPods 3: every announcement and every reply matches the format (see the tests in `tests/test_engine.py::TestProtocol`).

## 4. Starting the motion stream on AirPods 3

1. Open L2CAP PSM `0x1001` with `BT_SECURITY_MEDIUM` and **receive MTU 2582** (see §6).
2. Standard AAP handshake, `0x4D` features, `0x0F` notifications.
3. **HostConnect**: `04 00 04 00 17 00 00 08 00 00 08 00 | 00 gg 00 00 | 00 02 00 00`
4. The buds answer with one **DeviceConnect** per sensor (plist + HID report descriptor). Note the deviceID of `devmotion6`.
5. **SetReport, feature report 1 (Report Interval, µs)** on `devmotion6` (device 7):
   `04 00 04 00 17 00 00 08 00 00 0e 00 | 00 gg 00 00 | 07 05 06 00 | 02 | 01 10 27 00 00` (10 000 µs)
6. **InputReport** frames arrive (kind 2, device 7, 59 bytes): a `00` report-type byte, then the **same 58-byte block** as SensorDataWX.
7. Stop: the same SetReport with interval 0.

## 5. AirPods 3 sensor catalog

| ID | Service | Kind | Notable reports |
|---|---|---|---|
| 1 | `kadabra` | vendor | input reports 1, 2, 4… (unknown) |
| 2 | `mando` | vendor | 8 input reports (unknown) |
| 3 | `neorelay` | vendor | 11 input reports (unknown) |
| 4 | `accel` | **HID Sensor accelerometer 3D** (usage 0x73) | input: X/Y/Z int32, unit exponent −5, + timestamp |
| 5 | `gyro` | **HID Sensor gyrometer 3D** (usage 0x7A) | input: X/Y/Z int32, unit exponent −5, + timestamp |
| 6 | `cma` | CoreMotion activity | input 1 (1456 bits) |
| 7 | **`devmotion6`** | fused motion | input 1: u64 timestamp + 182-byte vendor block |
| 8 | `iedrelay` | vendor | input 2 (166 bytes) |

All of the sensor devices (4, 5, 6 and 7) share the same feature reports: **1 = Report Interval** (HID Sensor 0x030E, u32 µs),
2 = Report Latency (0x031B), 3 = vendor `0xFF15:1`, 4 = Max FIFO Events (0x031A).
The decoded descriptors are in [AIRPODS3-SENSORS.md](AIRPODS3-SENSORS.md).

## 6. The MTU hides the sensors

With the Linux default L2CAP receive MTU (**672**), AirPods 3 announce only `iedrelay` (649 B), `neorelay` (633 B) and
`mando` (590 B), every descriptor that fits. With `imtu = 2582` (the value iOS uses), they also announce `cma` (723),
**`devmotion6` (833)**, `gyro` (819), `accel` (820) and `kadabra` (892). The motion sensors are simply invisible at the default MTU,
and this is probably why AirPods 3 looked unsupported.

## 7. Sample rate

| Feature 1 value (µs) | Measured rate |
|---|---|
| 40 000 | ~25 Hz (expected) |
| 20 000 | ~57 Hz |
| 10 000 | ~110 Hz |
| ≤ 5 000 | ~210 Hz (floor, about 5 ms) |

At ~180 Hz, the link showed 12 gaps over 50 ms in 23 s (shared with A2DP and a BLE mouse), so 100 Hz is a good default.

## 8. Raw accelerometer and gyrometer

`accel` (device 4) and `gyro` (device 5) are standard HID Sensor devices, started the same way (SetReport feature 1). The input
report is: `01` (report ID), X, Y, Z as int32 LE (unit exponent −5, so **accel in 10⁻⁵ g**: about 100 000 at rest), then
a timestamp and status bits. They kept streaming at ~100 Hz in cases where `devmotion6` was frozen.

## 9. Units of the 58-byte motion block

Known layout (LibrePods PR #887, mutix): `[0]=01`, `[1:9]` u64 ns timestamp, `[20:26]` attitude, `[26:32]` rotation rate,
`[38:44]` user acceleration, `[44:50]` gravity. Measured on AirPods 3:

- `[20:26]`: vector part of a unit quaternion, Q15 (w = √(1−|v|²)); world frame with Z vertical (gravity rotates to ≈ (0, 0, −0.98)).
- `[26:32]`: **≈ 0.122 °/s per LSB** (consistent with a ±4000 °/s IMU), from integrating it against the quaternion.
- `[44:50]`: ≈ **1000 per g** (|g| = 981–1000 at rest).
- `[38:44]`: user acceleration with gravity removed, same scale; noise ≈ 0.02–0.03 g at rest.

## 10. Power and state behaviour (AirPods 3)

- **In the case, the sensors are off.** There is no DeviceConnect at all, even after HostConnect and a full Bluetooth reconnect. Taking one bud out brings the 8 devices back within seconds.
- **A bud lying still out of the ear freezes `devmotion6`.** The rotation rate stays exactly 0 for seconds, and the next gentle motion can be missed.
- **When the primary bud changes** (a bud goes in or out of the case), the AACP link is reset by the buds, or all sensors are re-announced. Running streams are cancelled and must be re-armed.
- **Ear detection flaps** between "in ear" and "out of ear" when a bud is held in the fingers (proximity sensor).
- After many sessions in a row, the buds can **stop announcing** sensors. A Bluetooth `disconnect` + `connect` fixes it.

## 11. Corrections to open questions

pabloaul/apple-wireshark parses the `0x800` body as "2 unknown, 4 unknown, 2 unknown, plist". The real layout is:
`00 | generation | 00 | side*2`, then the **sub-packet header u32** (for example `87 98 01 00` = device 7, length 817, kind 0 = DeviceConnect), then the plist.
The byte that "changes on every connection" is the generation counter. The two "constant" bytes belong to the header (they encode the deviceID and the length).

---

## Not isolated yet (honest list)

- Whether the Apple PnP DeviceID (`bluetooth:004C:…`) and the re-pair are needed. We changed it **before** the first success, together with the MTU; the MTU alone may be enough. **To test.**
- The meaning of `kadabra`, `mando`, `neorelay` and `iedrelay` reports, and of feature reports 2–4.
- Whether other AirPods models with older firmware (AirPods 2, AirPods Pro 1) also use RemoteHID.

## Reproduce

```bash
git clone https://github.com/starman-tech/mousepods && cd mousepods
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python airpods_mouse_controller.py --diag      # prints live rates; no cursor control
.venv/bin/python -m unittest discover -s tests            # 37 tests, no hardware needed
```

The full day-by-day journal (in French), with every failed attempt, is in [JOURNAL.fr.md](JOURNAL.fr.md).

---

© 2026 Matteo Gaudioso (starman-tech). This document is licensed under [CC BY 4.0](LICENSE-CC-BY-4.0.txt): reuse is welcome **with credit**: *MousePods by Matteo Gaudioso (starman-tech), https://github.com/starman-tech/mousepods*.

# Apple RemoteHID over AACP: wire format

RemoteHID is how an Apple host (iOS, macOS) talks to the **virtual HID devices inside AirPods**: motion sensors,
accelerometer, gyrometer and vendor services. It travels inside AACP (Apple Accessory Protocol, L2CAP PSM `0x1001`),
in the RTBuddy message (opcode `0x17`) with descriptor `0x00000800` ("Sensor").

Status: verified on AirPods 3 (product ID `0x2013`), November 2026. The field names come from the iOS 26.1
`RemoteHID.framework` encoders (`encodeHostConnect`, `encodeDeviceConnect`, `encodeSetReportRequest`…).
See [DISCOVERIES.md](DISCOVERIES.md) for the context and the evidence.

---

## 1. Transport

```
L2CAP PSM 0x1001, SOCK_SEQPACKET, BT_SECURITY_MEDIUM, receive MTU ≥ 2582
AACP frame :  04 00 04 00 | 17 00 | descriptor u32 LE | length u16 LE | body[length]
                           opcode    0x00000800 = Sensor / RemoteHID
```

> ⚠️ With the Linux default receive MTU (672), the accessory **does not announce** devices whose descriptor would not fit.
> Set `L2CAP_OPTIONS.imtu = 2582` before `connect()`.

## 2. Body

```
offset  size  field
0       1     transport version   (0 on the 0x800 channel; version 1 is wrapped in protobuf on 0x100000)
1       1     generation          (+1 per message, from each side; NOT a handle)
2       1     0
3       1     side flags          (host writes 2 * (side & 1))
4       …     one or more sub-packets, back to back
```

## 3. Sub-packet

Each sub-packet starts with a **u32 little-endian header**:

| Bits | Field |
|---|---|
| 0–6 | **deviceID** (the receiver ORs the side into bit 6) |
| 7–16 | **length** in bytes, *including* the 4-byte header |
| 17–19 | **kind** |
| 20 | input report carries a u64 timestamp at +5 (data then starts at +13) |
| 21 | response |
| 22 | status byte present |

```python
h = device | (length << 7) | (kind << 17)          # e.g. 07 05 06 00  = device 7, length 10, kind 3
```

## 4. Kinds

| Kind | Name | Direction | Payload after the header |
|---|---|---|---|
| 0 | **HostConnect** | host → device | none (length 4): `00 02 00 00` |
| 0 | **DeviceConnect** | device → host | XML plist (or binary IOCFSerialize): `AccessoryService`, `ReportDescriptor` (base64 HID descriptor), `MaxReportSize`, `VendorID`, `LocationID`, `ProtUpdate`, sometimes `BR`, `PrimaryVendorUsages`, `PRXC` |
| 1 | DeviceDisconnect | device → host | — |
| 2 | **InputReport** | device → host | `type` (1 byte, 0 = input), then the report (report ID first) |
| 3 | **SetReport** | host → device | `type` (0 input, 1 output, **2 feature**), then the report (report ID first) |
| 3 | SetReport response | device → host | response bit set; payload `00` |
| 4 | **GetReport** | host → device | `type`, `reportID` (length 6) |
| 4 | GetReport response | device → host | `type`, then the report |
| 5 | DeviceConfirmConnect | device → host | the host answers with HostConnect if it doesn't know the device |

## 5. Session

```
host                                         AirPods
 │  handshake, 0x4D, 0x0F (standard AACP)       │
 │── HostConnect  00 gg 00 00 | 00 02 00 00 ───▶│
 │◀── DeviceConnect dev 1..8 (plist + HID desc)─│   one per sensor/service
 │── SetReport dev 7, feature, 01 + u32 µs ────▶│   Report Interval
 │◀── SetReport response ───────────────────────│
 │◀── InputReport dev 7 … (every interval) ─────│
 │── SetReport dev 7, feature, 01 + 00000000 ──▶│   stop
```

- Streams are cancelled when the accessory re-announces its devices (primary-bud switch): **re-send the SetReport after each DeviceConnect**.
- The deviceIDs were stable across all our sessions on AirPods 3 (see [AIRPODS3-SENSORS.md](AIRPODS3-SENSORS.md)), but read them from the DeviceConnect `AccessoryService`.

## 6. Byte-exact examples (AirPods 3)

```
HostConnect                  04 00 04 00 17 00 00 08 00 00 08 00  00 05 00 00  00 02 00 00
DeviceConnect (devmotion6)   04 00 04 00 17 00 00 08 00 00 35 03  00 12 00 00  87 98 01 00  <?xml …
                             header 0x00019887 → device 7, length 817, kind 0
Start devmotion6 @ 100 Hz    04 00 04 00 17 00 00 08 00 00 0e 00  00 06 00 00  07 05 06 00  02  01 10 27 00 00
GetReport feature 1          04 00 04 00 17 00 00 08 00 00 0a 00  00 07 00 00  07 03 08 00  02 01
  → response                 … 07 … 08 00 (kind 4, response)  00 01 00 00 00 00   (interval 0 = idle)
InputReport devmotion6       04 00 04 00 17 00 00 08 00 00 43 00  00 gg 00 00  87 1f 04 00  00  01 <57 bytes>
                             payload = report type 00 + 58-byte motion block (identical to SensorDataWX)
```

## 7. The 58-byte motion block

| Offset | Size | Content | Scale |
|---|---|---|---|
| 0 | 1 | `01` | — |
| 1 | 8 | timestamp | ns |
| 20 | 3 × int16 | attitude quaternion, vector part | Q15; w = √(1 − |v|²); world Z is vertical |
| 26 | 3 × int16 | rotation rate (body frame) | ≈ 0.122 °/s per LSB |
| 38 | 3 × int16 | user acceleration, gravity removed | ≈ 1000 per g |
| 44 | 3 × int16 | gravity (body frame) | ≈ 1000 per g |
| 54 | u32 | counter | — |

## 8. Minimal Python

```python
import socket, struct
s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
s.setsockopt(274, 4, struct.pack("BB", 2, 0))                     # BT_SECURITY_MEDIUM
o = bytearray(s.getsockopt(6, 1, 12)); struct.pack_into("<H", o, 2, 2582)
s.setsockopt(6, 1, bytes(o))                                      # L2CAP_OPTIONS.imtu = 2582
s.connect((ADDR, 0x1001))
for p in ("00000400010002000000000000000000", "040004004d00d700000000000000", "040004000f00ffffffff"):
    s.send(bytes.fromhex(p))

def rhid(gen, dev, kind, payload=b""):
    sub = struct.pack("<I", dev | ((4 + len(payload)) << 7) | (kind << 17)) + payload
    body = bytes([0, gen, 0, 0]) + sub
    return bytes.fromhex("04000400170000080000") + struct.pack("<H", len(body)) + body

s.send(rhid(1, 0, 0))                                              # HostConnect
# … wait for DeviceConnect(devmotion6) → dev = 7
s.send(rhid(2, 7, 3, bytes([2, 1]) + struct.pack("<I", 10000)))   # SetReport feature 1 = 10 000 µs
```

The complete implementation is `AAPClient` in [`airpods_mouse_controller.py`](../airpods_mouse_controller.py).

---

© 2026 Matteo Gaudioso (starman-tech). This document is licensed under [CC BY 4.0](LICENSE-CC-BY-4.0.txt): reuse is welcome **with credit**: *MousePods by Matteo Gaudioso (starman-tech), https://github.com/starman-tech/mousepods*.

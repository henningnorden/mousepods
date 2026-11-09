# Other headsets (experimental)

Besides AirPods, MousePods reads headsets that follow **Android's standard head-tracker HID protocol**:
the ones made for Android's spatial audio with head tracking.

| Headset | Status |
|---|---|
| Sony WH-1000XM5, WH-1000XM6, WF-1000XM5, WF-1000XM6, ULT Wear | experimental, needs testing |
| Google Pixel Buds Pro, Pixel Buds Pro 2 | experimental, needs testing |
| Samsung Galaxy Buds Pro | planned (Samsung's own protocol) |
| Older headsets without motion sensors (Sony WH-1000XM4 and before, most Marshall…) | not possible |

They work everywhere the AirPods do: the Games page, opentrack, the virtual joystick, the cockpit demo,
the 360° player, and the mouse (mouse mode, same "pause" setting as the AirPods).

## Setup

1. Pair the headset with the PC as usual.
2. Give your user access to Bluetooth HID devices (once):
   ```bash
   sudo cp packaging/udev/70-mousepods-headtrackers.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```
   then disconnect and reconnect the headset.
3. Start MousePods. Headsets are looked for every 5 seconds; `GET /api/state` lists them under `headsets`.
4. Look straight ahead and press **Recenter**.

Settings in `config.json`, section `headsets`: `enabled`, `mouse` (move the cursor or not) and
`px_per_deg` (cursor pixels per degree of head rotation).

## How it works

The headset shows up as a HID "custom sensor" (usage page `0x20`, usage `0xE1`) whose description
(feature report 2) starts with `#AndroidHeadTracker#`. MousePods enables it with feature report `01 03`,
then reads input report 1: a rotation vector (3 × int16, ±π rad), the angular velocity (3 × int16,
±32 rad/s) and a counter that changes when the headset resets its orientation. Code: `hid_headtracker.py`.

The protocol details and the axis mapping come from
[SonyTrackerLinux](https://github.com/kdani3/SonyTrackerLinux) (MIT), tested on a WH-1000XM5.

## Help us test

If you own one of these headsets, please try it and open a
[device report](https://github.com/starman-tech/mousepods/issues/new?template=device_report.yml).
If an axis goes the wrong way, say which one: the mapping is one line in `hid_headtracker.py`.

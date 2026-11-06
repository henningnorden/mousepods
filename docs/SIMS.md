# AirPods head tracking for simulators on Linux

MousePods turns AirPods 3 or AirPods Pro into a head tracker, like a TrackIR: turn your head and the
in-game camera follows. Two outputs, usable together:

```
                              ┌─ UDP 127.0.0.1:4242 ─▶ opentrack ─┬─ Wine/Proton (FreeTrack + TrackIR) ─▶ DCS, MSFS, IL-2, Elite…
AirPods ──Bluetooth──▶ MousePods                                  ├─ X-Plane plugin, FlightGear, libevdev
                              └─ virtual joystick "MousePods head tracker" (uinput) ─▶ native games: ETS2/ATS, War Thunder…
```

Page **Simulateurs** in the app: switch each output on or off, host and port, **Recentrer**, live yaw /
pitch / roll, setup notes. Same settings in `config.json` (sections `opentrack` and `sim_joystick`) and
on the command line (`--opentrack`, `--sim-joystick`).
The French step-by-step for opentrack alone is in [OPENTRACK.md](OPENTRACK.md).

## Quick start

1. Calibrate the **head** profile once (page **Calibration**, source "Tête"): it tells MousePods which
   axis of the bud is your ear-to-ear axis, so the angles are the head's, however the bud sits.
2. Open the **Simulateurs** page, look straight at the screen, press **Recentrer**.
3. Pick an output:
   * **Game under Proton / Wine** (DCS, MSFS, IL-2, Elite Dangerous, Star Citizen…), or **X-Plane /
     FlightGear**: switch on **Envoyer à OpenTrack**, then set up opentrack (below).
   * **Native Linux game** that can bind the camera to joystick axes (Euro Truck Simulator 2, American
     Truck Simulator, War Thunder…): switch on **Manette virtuelle**. No opentrack needed.
4. By default the mouse cursor freezes while a simulator output is on (`pause_mouse_when_opentrack`).

```bash
.venv/bin/python airpods_mouse_controller.py --opentrack        # UDP to opentrack for this session
.venv/bin/python airpods_mouse_controller.py --sim-joystick     # virtual joystick for this session
.venv/bin/python airpods_mouse_controller.py --send recenter    # recenter (also Ctrl+Alt+C)
```

## Where the angles come from

Both AirPods families deliver the same 58-byte motion block (AirPods 3 through Apple RemoteHID, AirPods
Pro through SensorDataWX). Bytes `[20:26]` are the vector part of the buds' own orientation quaternion
(Q15). MousePods uses it by default (`"source": "quaternion"`):

* **Checked on a real AirPods 3 capture** (`captures/airpods3_motion_capture.json`, tested in
  `tests/test_opentrack.py`): the quaternion maps the bud frame to a "world" frame whose z axis is
  vertical (gravity brought to that frame = (−0.01, 0.08, −0.99) at rest), and the rotation between
  successive quaternions turns about the gyroscope's axis (median cosine 0.9994). Over 25-sample windows
  the quaternion's yaw / pitch / roll and the integrated, calibrated gyro rates agree in sign and shape
  on all three axes (correlation > 0.93).
* `w` is rebuilt as `√(1 − x² − y² − z²) ≥ 0`. That loses nothing: `q` and `−q` are the same rotation.
* At **recenter** MousePods freezes the current orientation and a head basis (calibrated lateral axis,
  forward = up × lateral, vertical). Afterwards the output is the rotation since recenter, split
  **yaw → pitch → roll** in that basis.
* Pitch and roll do not drift: the buds' fusion keeps them tied to gravity. **Yaw has no absolute
  reference** (no usable magnetometer): recenter now and then, or set a slow return to centre
  (`yaw_autocenter_s`).
* If the buds' fusion re-initialises (a big jump of the quaternion between two close samples with no
  rotation on the gyroscope), the reference is re-anchored so the output stays continuous.
* Until the first quaternion arrives (attitude still zero right after the sensor starts), or with
  `"source": "gyro"`, MousePods integrates the calibrated gyro rates instead and corrects pitch and roll
  with gravity (complementary filter, `drift_tau_s`).
* Side observation: with the current gyro scale (`gyro_dps_per_lsb` = 0.122) the integrated gyro angles
  come out about 15 % larger than the quaternion's. Which one is exact is not settled; the quaternion path
  does not depend on that scale.

**Signs** (after calibration): yaw + = look **right**, pitch + = look **up**, roll + = right ear towards
the right shoulder. opentrack does not document its conventions. Its X-Plane output passes yaw / pitch /
roll unchanged to X-Plane's "pilot's head" datarefs, and its FlightGear output sends `h = −yaw`,
`r = −roll`, which reads the same way for yaw and pitch. Roll is the uncertain one: check it in
opentrack's preview and tick its **Invert** box (or MousePods' own invert switches) if it is mirrored.

## Output 1: opentrack ("UDP over network")

### What MousePods sends

Facts read in the opentrack source (master, release 2026.1.0):

| | |
|---|---|
| packet | **6 doubles, 48 bytes, native byte order** (little-endian on x86 and ARM), no header: `x, y, z, yaw, pitch, roll` ([tracker-udp/ftnoir_tracker_udp.cpp](https://github.com/opentrack/opentrack/blob/master/tracker-udp/ftnoir_tracker_udp.cpp), axis order from [api/plugin-api.hpp](https://github.com/opentrack/opentrack/blob/master/api/plugin-api.hpp)) |
| units | x, y, z in **centimetres**, angles in **degrees** (the Wine output multiplies translation by 10 to get mm and converts degrees to radians) |
| port | **4242** by default |
| size | not checked: extra bytes are ignored, a short packet only overwrites the first values, a packet with NaN or Inf is dropped. MousePods always sends exactly 48 bytes and never NaN |
| rate | MousePods sends at a fixed `rate_hz` (120 by default) from its own thread, independent of the sensor rate; opentrack reads only the newest packet |
| tracker options | "Add to axis" (0, ±90, ±180 per rotation axis), nothing else |

AirPods measure rotation only: x, y, z are 0 (an experimental neck-pivot estimate, `lean_experimental`,
is off by default and unverified).

### Setting up opentrack

There are no official Linux binaries ("users are developers", says the
[wiki](https://github.com/opentrack/opentrack/wiki/Common-issues-on-Linux-and-macOS)): use your
distribution's package, the AUR, or build it from source with the Wine output enabled.

1. **Input**: `UDP over network`, port 4242.
2. **Output**, depending on the game (next section).
3. **Start**. The octopus moves when you turn your head.
4. **Filter**: opentrack's default is Accela-Hamilton (others: Accela, Alpha-Spectrum, EWMA, Hamilton,
   NaturalMovement). If you use one, set MousePods' **Lissage** to 0 so the two do not add up.
5. **Mapping** (curves): input degrees → output degrees, optionally a different curve for negative values.
   Typical: a small head turn gives a large view angle, with a flat start for stability.
6. **Centering**: opentrack's "center at startup" is on by default, and its Center hotkey stores the
   current pose as zero (method "Roll-compensated" by default). Recentering in MousePods (button, Ctrl+Alt+C,
   stem) also sets the sent angles to 0; use either. Recentering at the source also moves the ±180° yaw seam
   behind you, which opentrack's centering does not.

opentrack's processing order, for reference: tracker → axis remap → camera offset → centering → filter
→ curves → zero/hold → invert → output ([logic/pipeline.cpp](https://github.com/opentrack/opentrack/blob/master/logic/pipeline.cpp)).

### opentrack outputs on Linux, and the games behind them

* **Wine** (`Wine -- Windows layer for Unix`): emulates **FreeTrack**, **NPClient (TrackIR)** or **Both**
  inside a Wine prefix by writing the NaturalPoint and FreeTrack registry keys. Variants: plain Wine (path
  + prefix), **Proton** (version + prefix), **Steam Play** (by AppID, searched only in the default Steam
  paths, not extra libraries or Flatpak Steam), and UMU launchers such as Lutris. Tick **ESYNC / FSYNC**
  to match the game (Proton turns them on by default). Start opentrack before the game. This is the path
  for Windows sims under Proton:
  * **DCS World**: Wine output, Both protocols, the game's prefix
    ([dcs_on_linux](https://github.com/TheZoq2/dcs_on_linux)).
  * **IL-2 Great Battles**, **Elite Dangerous**, **MSFS 2024**: reported working through the prefix,
    sometimes with the Windows build of opentrack run inside the same prefix
    ([ProtonDB IL-2](https://protondb.com/app/307960),
    [Frontier forum](https://forums.frontier.co.uk/threads/opentrack-on-linux.628740)). MSFS 2020 and
    Star Citizen: expected to work the same way (TrackIR protocol), not verified here.
* **X-Plane 11/12 (native)**: opentrack's `opentrack.xpl` plugin (needs the X-Plane SDK when building
  opentrack), or the third-party `htrack.xpl` plugin, which reads the same UDP packet on 4242 directly,
  so it could take MousePods' packets **without opentrack**
  ([discussion](https://github.com/opentrack/opentrack/discussions/1836)).
* **FlightGear**: opentrack's FlightGear output sends UDP to port 5542 with
  `contrib/FlightGear/Protocol/headtracker.xml`, started with `--generic=socket,in,...`
  ([FlightGear wiki](https://wiki.flightgear.org/Opentrack)).
* **X4: Foundations (native)** has an opentrack setting of its own that listens for this same packet on
  127.0.0.1:4242 ([Egosoft forum](https://forum.egosoft.com/viewtopic.php?p=5037498)): MousePods can feed
  it **without opentrack**.
* **libevdev joystick**: a virtual joystick; MousePods now has the same thing built in (below).

## Output 2: virtual joystick (native Linux games, no opentrack)

Many native Linux builds have no TrackIR or FreeTrack support but can bind the camera to joystick axes.
MousePods creates a uinput joystick named **"MousePods head tracker"** with **the same layout as
opentrack's `libevdev joystick` output** ([proto-libevdev](https://github.com/opentrack/opentrack/blob/master/proto-libevdev/ftnoir_protocol_libevdev.cpp)),
so guides written for opentrack apply as they are:

| axis | value | full scale (default) |
|---|---|---|
| `ABS_RX` | yaw | ±180° |
| `ABS_RY` | pitch | ±90° |
| `ABS_RZ` | roll | ±180° |
| `ABS_X`, `ABS_Y`, `ABS_Z` | x, y, z (0 with AirPods) | ±100 cm |

Values 0…65535, centre 32767; `BTN_TRIGGER` is declared so udev classifies it as a joystick. Lower the
**full scale** (`full_yaw_deg`…) to get more in-game movement for the same head angle: with 60°, turning
your head 60° moves the axis to its end. The pose is the same as the opentrack one (same recenter, gains,
inverts, smoothing).

* **Euro Truck Simulator 2 / American Truck Simulator (native)**: no FreeTrack in the Linux build, but head
  look can be bound to joystick axes by editing `controls_linux.sii` in your profile (game closed): bind
  `headtryaw` and `headtrpitch` to the joystick's axes and switch `headtron` on. The exact axis names depend
  on how the game numbers the joystick; check them in the game's controller screen. Step-by-step: [ETS2 head tracking on Linux](https://dsimidzija.github.io/posts/euro-truck-simulator-2-headtracking-linux)
  (written for opentrack's libevdev output, same axes). Yaw and pitch work well; roll was reported not smooth.
* **War Thunder (native)**: no TrackIR in the Linux build; bind the view axes (per vehicle class) to the
  joystick's RX / RY ([forum](https://forum.warthunder.com/t/rfi-does-native-linux-build-expose-trackir-support/95689)).
* **FlightGear**: joystick XML binding (linuxtrack ships an example, `uinput-abs.xml`).
* **X-Plane**: "View left/right" and "View up/down" axis functions; whether they are absolute or a rate is
  not documented, so the plugin route above is preferable.
* Under Proton, Windows games see the joystick too, but they want TrackIR / FreeTrack for head tracking:
  use opentrack's Wine output there.

### Direct joystick or opentrack?

| | virtual joystick | through opentrack |
|---|---|---|
| install | nothing (needs `/dev/uinput`, like the mouse) | opentrack, often built from source |
| Proton / Wine games (TrackIR, FreeTrack) | no | **yes** |
| native games with axis binding | **yes** | yes (its libevdev output) |
| curves, filters | full-scale gain, MousePods smoothing | opentrack's curves and filters |
| per-game setup | bind axes in each game | opentrack profile per game |

Both are kept: the joystick for native games with no extra software, opentrack for everything that needs
TrackIR / FreeTrack or fine curves.

## Other Linux options considered

* **linuxtrack** ([uglyDwarf/linuxtrack](https://github.com/uglyDwarf/linuxtrack), last commit March 2024):
  TrackIR / webcam / Wiimote inputs, X-Plane plugin, Wine NPClient bridge. No UDP input, but its joystick
  input could read MousePods' virtual joystick (untested). The active fork
  [LinuxTrack X-IR](https://gitlab.com/fwfa123/linuxtrackx-ir) adds Proton support.
* **Comparable projects**: [pods-head-tracker](https://github.com/batubozkan/pods-head-tracker) (AirPods on a
  Raspberry Pi, opentrack's FreePIE UDP input, radians), [sony-head-tracker](https://github.com/NicholasSlattery/sony-head-tracker/wiki)
  (Sony headphones, the same 6-double packet), [xrlinuxdriver](https://xrlinuxdriver.readthedocs.io/en/latest/opentrack-app/)
  (XR glasses, 6 doubles + a frame counter). All recenter at the source, as MousePods does.

## Limits

* **3 degrees of freedom**: rotation only. No camera, so leaning (x, y, z) cannot be measured.
* **Yaw drift**: no heading reference; recenter from time to time.
* **Bluetooth latency**: a little more than a wired TrackIR, and short drop-outs when the link is busy
  (audio + sensors). About 100 Hz (`sensor_interval_us`: 10000) is a good trade-off on AirPods 3; AirPods Pro
  send ~25 Hz (smoothed and resent at `rate_hz`).
* At least one bud must be worn (AirPods 3 sensors are off in the case).
* The game support listed above comes from public reports, not from tests with AirPods; only the packet
  format, the joystick device and the angle maths are covered by the automated tests.

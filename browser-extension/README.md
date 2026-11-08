# MousePods browser extension

Look around 360° videos by turning your head, right in Chrome (or any Chromium browser).

- **YouTube 360°:** on youtube.com the view of a 360° video follows your head, including videos that
  can't be embedded elsewhere.
- **VR headset for WebXR sites (on by default):** VR sites (three.js, A-Frame, Babylon.js…) see an
  "immersive-vr" headset whose head pose comes from your AirPods, so they show their **Enter VR**
  button instead of asking you to install an emulator. In VR the page goes full screen; **Esc**
  leaves. With **3D side by side** each eye gets its own half, for 3D goggles (e.g. Fat Shark
  Dominator in HDMI 3D mode) and 3D TVs. No controllers: sites that need them won't be playable.
- **Gyroscope on other sites (experimental, off by default):** many 360° viewers (Street View,
  panoramas, three.js scenes) look around with a phone's gyroscope. The extension sends them
  synthetic `deviceorientation` events from your head pose.

MousePods must be running on the same computer (it serves the head pose on
`http://127.0.0.1:8737/api/pose`). The extension only asks for the pose while a page needs it, and
MousePods pauses the mouse during that time.

## Install

1. Open `chrome://extensions` and switch on **Developer mode** (top right).
2. **Load unpacked** and pick this `browser-extension` folder.
3. Pin the MousePods icon. Its popup has on/off, amplification, axis inversion and **Recenter**.

Tip: look straight at the screen and press **Recenter** before playing a video.

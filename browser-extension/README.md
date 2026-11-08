# MousePods browser extension

Look around 360° videos by turning your head, right in Chrome (or any Chromium browser).

- **YouTube 360°:** on youtube.com the view of a 360° video follows your head, including videos that
  can't be embedded elsewhere.
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

// SPDX-License-Identifier: Apache-2.0
// Main-world part: drives the view of the page from the head pose.
//  - YouTube: 360° videos through the player's own API (setSphericalProperties).
//  - "Gyroscope" (experimental, off by default): synthetic deviceorientation events, which many 360°
//    viewers use to look around on phones (Street View, panoramas, three.js scenes).
(() => {
  let settings = { enabled: true, youtube: true, gyro: false, gain: 1, yawSign: 1, pitchSign: 1 };
  let need = false, cur = null;

  function ytPlayer() {
    if (!/(^|\.)youtube\.com$/.test(location.hostname)) return null;
    const p = document.getElementById("movie_player");
    if (!p || typeof p.getSphericalProperties !== "function") return null;
    const sp = p.getSphericalProperties();
    return sp && Object.keys(sp).length ? p : null;            // empty object = not a 360° video
  }

  function check() {
    const n = !!settings.enabled && ((settings.youtube && !!ytPlayer()) || !!settings.gyro);
    if (n !== need) { need = n; cur = null; window.postMessage({ __mousepods: true, type: "need", need: n }, "*"); }
  }
  setInterval(check, 1000);

  function apply(m) {
    if (!m || !m.pose || !need) return;
    const g = +settings.gain || 1;
    const want = [m.pose[3] * g * (settings.yawSign || 1),
                  Math.max(-89, Math.min(89, m.pose[4] * g * (settings.pitchSign || 1))),
                  m.pose[5]];
    cur = cur ? cur.map((v, i) => v + (want[i] - v) * 0.5) : want;     // light smoothing at 50 Hz
    const [yaw, pitch, roll] = cur;
    const p = settings.youtube && ytPlayer();
    // YouTube's yaw grows when the viewer turns LEFT (ours grows to the right)
    if (p) p.setSphericalProperties({ yaw: ((-yaw % 360) + 360) % 360, pitch, roll: -roll, enableOrientationSensor: false });
    if (settings.gyro) {
      // phone held upright in front of the face: beta 90 = level, alpha grows when turning left
      window.dispatchEvent(new DeviceOrientationEvent("deviceorientation", {
        alpha: ((-yaw % 360) + 360) % 360, beta: Math.max(-180, Math.min(180, 90 + pitch)),
        gamma: Math.max(-90, Math.min(90, roll)), absolute: false }));
    }
  }

  window.addEventListener("message", e => {
    if (e.source !== window || !e.data || !e.data.__mousepods) return;
    if (e.data.type === "settings") { settings = { ...settings, ...e.data.settings }; check(); }
    else if (e.data.type === "pose") apply(e.data.m);
  });
})();

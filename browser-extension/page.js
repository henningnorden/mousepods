// SPDX-License-Identifier: Apache-2.0
// Main-world part: drives the view of the page from the head pose.
//  - YouTube: 360° videos through the player's own API (setSphericalProperties).
//  - "Gyroscope" (experimental, off by default): synthetic deviceorientation events, which many 360°
//    viewers use to look around on phones (Street View, panoramas, three.js scenes).
//  - WebXR headset: VR sites see an "immersive-vr" headset whose head pose comes from the AirPods.
//    The page renders into its own canvas (full screen), as one view or two side by side (3D goggles).
(() => {
  const DEFAULTS = { enabled: true, youtube: true, gyro: false, webxr: true, xrStereo: false, gain: 1, yawSign: 1, pitchSign: 1 };
  let settings = { ...DEFAULTS };
  let need = false, cur = null;
  let settingsReady;
  const settingsPromise = new Promise(r => { settingsReady = r; });
  setTimeout(() => settingsReady(), 800);                       // never wait forever for the bridge
  const XR = { session: null, pose: [0, 0, 0] };

  // ------------------------------------------------------------------ YouTube 360
  function ytPlayer() {
    if (!/(^|\.)youtube\.com$/.test(location.hostname)) return null;
    const p = document.getElementById("movie_player");
    if (!p || typeof p.getSphericalProperties !== "function") return null;
    const sp = p.getSphericalProperties();
    return sp && Object.keys(sp).length ? p : null;            // empty object = not a 360° video
  }

  function check() {
    const n = !!settings.enabled && ((settings.youtube && !!ytPlayer()) || !!settings.gyro || !!XR.session);
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
    XR.pose = [yaw, pitch, roll];
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
    if (e.data.type === "settings") { settings = { ...settings, ...e.data.settings }; settingsReady(); check(); }
    else if (e.data.type === "pose") apply(e.data.m);
  });

  // ------------------------------------------------------------------ WebXR headset emulation
  const D2R = Math.PI / 180;
  const qmul = (a, b) => ({
    x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
    y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
    z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
    w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z });
  const qaxis = (x, y, z, ang) => { const s = Math.sin(ang / 2); return { x: x * s, y: y * s, z: z * s, w: Math.cos(ang / 2) }; };
  // head orientation: yaw (+ = look right) about Y, then pitch (+ = up) about X, then roll about Z
  const headQuat = (yaw, pitch, roll) =>
    qmul(qmul(qaxis(0, 1, 0, -yaw * D2R), qaxis(1, 0, 0, pitch * D2R)), qaxis(0, 0, 1, -roll * D2R));
  function rotate(q, v) {                                        // v' = q v q*
    const p = qmul(qmul(q, { x: v.x, y: v.y, z: v.z, w: 0 }), { x: -q.x, y: -q.y, z: -q.z, w: q.w });
    return { x: p.x, y: p.y, z: p.z };
  }
  function composeMatrix(q, p) {                                 // column-major, like WebXR and three.js
    const { x, y, z, w } = q;
    return new Float32Array([
      1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
      2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
      2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
      p.x, p.y, p.z, 1]);
  }
  function perspective(fovY, aspect, near, far) {
    const f = 1 / Math.tan(fovY * D2R / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
  }

  class MPRigidTransform {
    constructor(position = {}, orientation = {}) {
      this.position = DOMPointReadOnly.fromPoint({ x: position.x || 0, y: position.y || 0, z: position.z || 0, w: 1 });
      const o = { x: orientation.x || 0, y: orientation.y || 0, z: orientation.z || 0, w: orientation.w == null ? 1 : orientation.w };
      const n = Math.hypot(o.x, o.y, o.z, o.w) || 1;
      this.orientation = DOMPointReadOnly.fromPoint({ x: o.x / n, y: o.y / n, z: o.z / n, w: o.w / n });
      this.matrix = composeMatrix(this.orientation, this.position);
    }
    get inverse() {
      const qi = { x: -this.orientation.x, y: -this.orientation.y, z: -this.orientation.z, w: this.orientation.w };
      const p = rotate(qi, { x: -this.position.x, y: -this.position.y, z: -this.position.z });
      return new MPRigidTransform(p, qi);
    }
  }

  class MPReferenceSpace extends EventTarget {
    constructor(type, offset = null) { super(); this.type = type; this.offset = offset; }
    getOffsetReferenceSpace(t) {
      // compose offsets: new origin = old origin * t
      if (!this.offset) return new MPReferenceSpace(this.type, t);
      const a = this.offset, q = qmul(a.orientation, t.orientation), r = rotate(a.orientation, t.position);
      return new MPReferenceSpace(this.type, new MPRigidTransform({ x: a.position.x + r.x, y: a.position.y + r.y, z: a.position.z + r.z }, q));
    }
  }

  class MPWebGLLayer {
    constructor(session, gl, init = {}) {
      this.context = gl; this.framebuffer = null;               // null = the page's own canvas
      this.antialias = init.antialias !== false; this.ignoreDepthValues = false; this.fixedFoveation = null;
      const c = gl.canvas;
      if (c && c.requestFullscreen && !document.fullscreenElement) c.requestFullscreen().catch(() => {});
    }
    get framebufferWidth() { const c = this.context.canvas; return Math.max(1, Math.round((c.clientWidth || innerWidth) * devicePixelRatio)); }
    get framebufferHeight() { const c = this.context.canvas; return Math.max(1, Math.round((c.clientHeight || innerHeight) * devicePixelRatio)); }
    getViewport(view) {
      const W = this.framebufferWidth, H = this.framebufferHeight;
      if (view._count === 2) return { x: view.eye === "left" ? 0 : Math.floor(W / 2), y: 0, width: Math.floor(W / 2), height: H };
      return { x: 0, y: 0, width: W, height: H };
    }
    static getNativeFramebufferScaleFactor() { return 1; }
  }

  // Recent three.js always creates an XRWebGLBinding; Chrome's own rejects our session. Without
  // createProjectionLayer the page falls back to the XRWebGLLayer above (no layers support).
  class MPWebGLBinding {
    constructor(session, gl) { this.session = session; this.context = gl; this.nativeProjectionScaleFactor = 1; this.usesDepthValues = false; }
    getDepthInformation() { return null; }
    getCameraImage() { return null; }
  }

  class MPFrame {
    constructor(session) { this.session = session; this.predictedDisplayTime = performance.now(); this.trackedAnchors = new Set(); }
    getPose() { return null; }                                  // no controllers
    getViewerPose(space) {
      const s = this.session, layer = s.renderState.baseLayer;
      const [yaw, pitch, roll] = XR.pose;
      let q = headQuat(yaw, pitch, roll);
      let head = { x: 0, y: space.type === "local-floor" ? 1.6 : 0, z: 0 };
      if (space.type === "viewer") { q = { x: 0, y: 0, z: 0, w: 1 }; head = { x: 0, y: 0, z: 0 }; }
      if (space.offset) {                                       // express the pose in the offset space
        const inv = space.offset.inverse, r = rotate(inv.orientation, head);
        head = { x: inv.position.x + r.x, y: inv.position.y + r.y, z: inv.position.z + r.z };
        q = qmul(inv.orientation, q);
      }
      const W = layer ? layer.framebufferWidth : innerWidth, H = layer ? layer.framebufferHeight : innerHeight;
      // side by side: each half is stretched back to full width by the goggles, so keep the full aspect
      const proj = perspective(settings.xrStereo ? 70 : 75, W / H, s.renderState.depthNear || 0.1, s.renderState.depthFar || 1000);
      const eyes = settings.xrStereo ? [["left", -0.032], ["right", 0.032]] : [["none", 0]];
      const views = eyes.map(([eye, dx]) => {
        const o = rotate(q, { x: dx, y: 0, z: 0 });
        return { eye, _count: eyes.length, projectionMatrix: proj, recommendedViewportScale: null, requestViewportScale() {},
                 transform: new MPRigidTransform({ x: head.x + o.x, y: head.y + o.y, z: head.z + o.z }, q) };
      });
      return { transform: new MPRigidTransform(head, q), views, emulatedPosition: true };
    }
  }

  class MPSession extends EventTarget {
    constructor(mode) {
      super();
      this.mode = mode; this.inputSources = []; this.environmentBlendMode = "opaque"; this.visibilityState = "visible";
      this.interactionMode = "world-space"; this.enabledFeatures = ["viewer", "local", "local-floor"];
      this.renderState = { baseLayer: null, depthNear: 0.1, depthFar: 1000, inlineVerticalFieldOfView: null };
      this._ended = false;
      this._onFs = () => { if (!document.fullscreenElement) this.end(); };   // Esc leaves VR
      document.addEventListener("fullscreenchange", this._onFs);
    }
    updateRenderState(state = {}) { this.renderState = { ...this.renderState, ...state }; }
    updateTargetFrameRate() { return Promise.resolve(); }
    requestReferenceSpace(type) {
      if (["viewer", "local", "local-floor"].includes(type)) return Promise.resolve(new MPReferenceSpace(type));
      return Promise.reject(new DOMException("Reference space not supported: " + type, "NotSupportedError"));
    }
    requestAnimationFrame(cb) { return window.requestAnimationFrame(t => { if (!this._ended) cb(t, new MPFrame(this)); }); }
    cancelAnimationFrame(id) { window.cancelAnimationFrame(id); }
    end() {
      if (this._ended) return Promise.resolve();
      this._ended = true; XR.session = null; check();
      document.removeEventListener("fullscreenchange", this._onFs);
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      const ev = new Event("end"); ev.session = this;
      this.dispatchEvent(ev); if (typeof this.onend === "function") this.onend(ev);
      return Promise.resolve();
    }
  }

  class MPXRSystem extends EventTarget {
    isSessionSupported(mode) {
      return settingsPromise.then(() => !!(settings.enabled && settings.webxr && mode === "immersive-vr"));
    }
    requestSession(mode) {
      return settingsPromise.then(() => {
        if (!(settings.enabled && settings.webxr) || mode !== "immersive-vr")
          throw new DOMException("Session mode not supported: " + mode, "NotSupportedError");
        if (XR.session) throw new DOMException("A session is already running", "InvalidStateError");
        XR.session = new MPSession(mode); cur = null; check();
        return XR.session;
      });
    }
  }

  try {
    const xr = new MPXRSystem();
    Object.defineProperty(Navigator.prototype, "xr", { get() { return xr; }, configurable: true });
    window.XRWebGLLayer = MPWebGLLayer;
    window.XRWebGLBinding = MPWebGLBinding;
    window.XRRigidTransform = MPRigidTransform;
    for (const C of [window.WebGLRenderingContext, window.WebGL2RenderingContext])
      if (C) C.prototype.makeXRCompatible = function () { return Promise.resolve(); };
  } catch (_) { /* page locked navigator.xr: leave it alone */ }
})();

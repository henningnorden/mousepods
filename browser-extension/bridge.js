// SPDX-License-Identifier: Apache-2.0
// Isolated-world bridge: settings from chrome.storage and the pose from the background go to page.js
// (main world, where the YouTube player API lives) through window.postMessage.
(() => {
  const DEFAULTS = { enabled: true, youtube: true, gyro: false, gain: 1, yawSign: 1, pitchSign: 1 };
  let settings = { ...DEFAULTS }, port = null, keepalive = null, wanted = false;

  const toPage = msg => window.postMessage({ __mousepods: true, ...msg }, "*");

  function sync() {
    const on = wanted && settings.enabled;
    if (on && !port) {
      port = chrome.runtime.connect({ name: "pose" });
      port.onMessage.addListener(m => toPage({ type: "pose", m }));
      port.onDisconnect.addListener(() => { port = null; clearInterval(keepalive); });
      keepalive = setInterval(() => { try { port && port.postMessage({ ping: 1 }); } catch (_) {} }, 20000);
    } else if (!on && port) {
      port.disconnect(); port = null; clearInterval(keepalive);
    }
  }

  chrome.storage.local.get(DEFAULTS, s => { settings = { ...DEFAULTS, ...s }; toPage({ type: "settings", settings }); sync(); });
  chrome.storage.onChanged.addListener(changes => {
    for (const [k, v] of Object.entries(changes)) settings[k] = v.newValue;
    toPage({ type: "settings", settings }); sync();
  });
  window.addEventListener("message", e => {
    if (e.source !== window || !e.data || !e.data.__mousepods || e.data.type !== "need") return;
    wanted = !!e.data.need; sync();
  });
})();

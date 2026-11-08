// SPDX-License-Identifier: Apache-2.0
const DEFAULTS = { enabled: true, youtube: true, gyro: false, webxr: true, xrStereo: false, gain: 1, yawSign: 1, pitchSign: 1 };
const $ = id => document.getElementById(id);

chrome.storage.local.get(DEFAULTS, s => {
  $("enabled").checked = s.enabled; $("youtube").checked = s.youtube; $("gyro").checked = s.gyro;
  $("webxr").checked = s.webxr; $("xrStereo").checked = s.xrStereo;
  $("gain").value = s.gain; $("gainv").textContent = "×" + (+s.gain).toFixed(1);
  $("yawInv").checked = s.yawSign < 0; $("pitchInv").checked = s.pitchSign < 0;
});
for (const id of ["enabled", "youtube", "gyro", "webxr", "xrStereo"]) $(id).onchange = e => chrome.storage.local.set({ [id]: e.target.checked });
$("gain").oninput = e => { $("gainv").textContent = "×" + (+e.target.value).toFixed(1); chrome.storage.local.set({ gain: +e.target.value }); };
$("yawInv").onchange = e => chrome.storage.local.set({ yawSign: e.target.checked ? -1 : 1 });
$("pitchInv").onchange = e => chrome.storage.local.set({ pitchSign: e.target.checked ? -1 : 1 });
$("recenter").onclick = () => chrome.runtime.sendMessage({ cmd: "recenter" }, r => {
  $("recenter").textContent = r && r.ok ? "Recentered ✓" : "MousePods is not running";
  setTimeout(() => $("recenter").textContent = "Recenter (head straight)", 1500);
});
chrome.runtime.sendMessage({ cmd: "status" }, r => {
  const st = $("st");
  if (!r || !r.running) { st.textContent = "MousePods is not running on this computer"; return; }
  st.className = "st ok";
  st.textContent = r.active ? "Connected · following your head" : "Connected · open a 360° video";
});

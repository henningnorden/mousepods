// SPDX-License-Identifier: Apache-2.0
// Polls MousePods on this computer for the head pose, only while a tab asked for it (an open "pose" port):
// GET /api/pose also pauses the MousePods mouse, so we never poll for nothing.
const API = "http://127.0.0.1:8737";
const ports = new Set();
let timer = null;

async function tick() {
  let msg;
  try {
    const j = await (await fetch(API + "/api/pose", { cache: "no-store" })).json();
    msg = { pose: j.pose, live: j.age != null && j.age < 0.5, connected: !!j.connected };
  } catch (e) {
    msg = { error: "MousePods is not running" };
  }
  for (const p of ports) { try { p.postMessage(msg); } catch (_) { /* tab gone */ } }
}

chrome.runtime.onConnect.addListener(port => {
  if (port.name !== "pose") return;
  ports.add(port);
  if (!timer) timer = setInterval(tick, 20);                 // 50 Hz is plenty for a video view
  port.onMessage.addListener(() => {});                      // keep-alive pings from the tab
  port.onDisconnect.addListener(() => {
    ports.delete(port);
    if (!ports.size) { clearInterval(timer); timer = null; }
  });
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.cmd === "recenter") {
    fetch(API + "/api/cmd", { method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({ cmd: "recenter" }) })
      .then(() => reply({ ok: true })).catch(() => reply({ ok: false }));
    return true;
  }
  if (msg && msg.cmd === "status") {
    fetch(API + "/api/ping", { cache: "no-store" })
      .then(r => reply({ running: r.ok, active: ports.size })).catch(() => reply({ running: false, active: ports.size }));
    return true;
  }
  return false;
});

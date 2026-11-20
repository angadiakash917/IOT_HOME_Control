// Live Firebase Realtime Database client (REST + server-sent events, no SDK).
const DB = "https://iotbasedbulbonoff-default-rtdb.firebaseio.com";
const $ = (id) => document.getElementById(id);

async function setBulb(value) {
  $("state").textContent = "sending…";
  try {
    const r = await fetch(`${DB}/turn.json`, { method: "PUT", body: JSON.stringify(value) });
    if (!r.ok) throw new Error(r.status);
    render();   // clear "sending…"; the real state arrives from the NodeMCU
  } catch (e) {
    $("state").textContent = "error – check database rules";
  }
}
$("on").onclick = () => setBulb("on");
$("off").onclick = () => setBulb("off");

let status = {};
function render() {
  const on = status.relayOn === true;
  $("bulb").className = "bulb " + (on ? "on" : "off");
  $("state").textContent = status.relayOn === undefined ? "—" : on ? "ON" : "OFF";
  $("temp").textContent = status.temperature != null ? status.temperature + " °C" : "—";
  $("hum").textContent = status.humidity != null ? status.humidity + " %" : "—";
  $("ldr").textContent = status.ldrRaw != null ? Math.round(status.ldrRaw * 100 / 1023) + " %" : "—";
  $("rssi").textContent = status.wifiRssi != null ? status.wifiRssi + " dBm" : "—";
  const age = status.updatedAt ? (Date.now() - status.updatedAt) / 1000 : Infinity;
  const online = age < 30;
  $("conn").textContent = online ? "NodeMCU online" : "NodeMCU offline";
  $("conn").className = "pill " + (online ? "ok" : "bad");
  $("seen").textContent = status.updatedAt ? "Last update " + new Date(status.updatedAt).toLocaleTimeString() : "";
}

function listen() {
  const es = new EventSource(`${DB}/status.json`);
  const apply = (e) => {
    const m = JSON.parse(e.data);
    if (!m) return;
    if (m.path === "/") status = m.data || {};
    else status[m.path.slice(1)] = m.data;
    render();
  };
  es.addEventListener("put", apply);
  es.addEventListener("patch", apply);
  es.onerror = () => { $("conn").textContent = "Reconnecting…"; };
}
listen();
setInterval(render, 5000);

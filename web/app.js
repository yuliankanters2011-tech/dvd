"use strict";

const $ = (id) => document.getElementById(id);
const stateLabels = {
  starting: ["Opstarten", "working"],
  ready: ["Beschikbaar", "ready"],
  empty: ["Geen schijf", ""],
  open: ["Lade open", ""],
  missing: ["Speler mist", "error"],
  not_ready: ["Even wachten", "working"],
  mounting: ["Aankoppelen", "working"],
  error: ["Controle nodig", "error"],
  stopped: ["Gestopt", ""],
};
const titleLabels = {
  ready: "Je schijf is beschikbaar",
  empty: "Plaats een cd of dvd",
  open: "De lade staat open",
  missing: "De speler is niet gevonden",
  not_ready: "De speler komt op gang",
  mounting: "De schijf wordt aangekoppeld",
  error: "Er is een probleem",
  stopped: "De service is gestopt",
  starting: "De speler wordt gecontroleerd",
};
let busy = false;
let latest = null;

function bytes(value) {
  if (!Number.isFinite(value) || value <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toLocaleString("nl-NL", { maximumFractionDigits: 1 })} ${units[i]}`;
}

function render(data) {
  latest = data;
  const [label, kind] = stateLabels[data.state] || [data.state, ""];
  const working = data.copy_state === "copying" || data.image_state === "working";
  const badge = $("state-badge");
  badge.textContent = working ? "Bezig" : label;
  badge.className = `badge ${working ? "working" : kind}`;
  $("status-title").textContent = titleLabels[data.state] || "DVD Bridge";
  $("status-message").textContent = data.message || "";
  $("disc-label").textContent = data.disc?.label || "—";
  $("disc-format").textContent = data.disc?.filesystem ? data.disc.filesystem.toUpperCase() : "—";
  $("disc-size").textContent = bytes(data.disc?.size_bytes);
  $("device").textContent = data.device || "—";
  $("nas-folder").textContent = data.nas_folder || "—";
  $("library-folder").textContent = data.library_folder || "—";
  $("version").textContent = data.version ? `v${data.version}` : "";
  $("disc-visual").className = `disc-visual ${data.state === "ready" ? "ready" : ""} ${working ? "working" : ""}`;

  const ready = data.state === "ready";
  $("copy-button").disabled = busy || working || !ready || data.copy_state === "done";
  $("iso-button").disabled = busy || working || !ready;
  $("eject-button").disabled = busy || working || !["ready", "empty", "open", "error"].includes(data.state);
  $("retry-button").classList.toggle("hidden", data.state !== "error");
  $("retry-button").disabled = busy || working;

  const showCopy = ["copying", "done", "error"].includes(data.copy_state);
  $("copy-progress").classList.toggle("hidden", !showCopy);
  $("copy-bar").value = data.copy_percent || 0;
  $("copy-percent").textContent = `${data.copy_percent || 0}%`;
  $("copy-label").textContent = data.copy_state === "done" ? "Kopiëren klaar" : data.copy_state === "error" ? "Kopiëren mislukt" : `Kopiëren · ${bytes(data.copied_bytes)} / ${bytes(data.total_bytes)}`;
  const showImage = ["working", "done", "error"].includes(data.image_state);
  $("image-progress").classList.toggle("hidden", !showImage);
  $("image-bar").value = data.image_percent || 0;
  $("image-percent").textContent = `${data.image_percent || 0}%`;
}

async function refresh() {
  const connection = $("connection");
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
    connection.className = "connection online";
    connection.innerHTML = '<span class="dot"></span> Verbonden';
  } catch {
    connection.className = "connection offline";
    connection.innerHTML = '<span class="dot"></span> Geen verbinding';
  }
}

async function command(name, confirmation) {
  if (confirmation && !window.confirm(confirmation)) return;
  busy = true;
  const feedback = $("action-feedback");
  feedback.className = "action-feedback";
  feedback.textContent = "Opdracht verzenden…";
  if (latest) render(latest);
  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-DVD-Bridge": "1" },
      body: JSON.stringify({ command: name }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    feedback.textContent = "Opdracht ontvangen. De status wordt bijgewerkt.";
  } catch (error) {
    feedback.className = "action-feedback error";
    feedback.textContent = error.message || "De opdracht is niet verzonden.";
  } finally {
    busy = false;
    await refresh();
  }
}

$("copy-button").addEventListener("click", () => command("copy"));
$("iso-button").addEventListener("click", () => command("iso", "Een ISO kopieert de hele schijf naar de NAS en kan veel ruimte kosten. Doorgaan?"));
$("eject-button").addEventListener("click", () => command("eject", "De schijf eerst loskoppelen en daarna uitwerpen?"));
$("retry-button").addEventListener("click", () => command("retry"));
refresh();
setInterval(refresh, 3000);

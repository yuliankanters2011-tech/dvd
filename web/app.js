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
let media = { sources: [], destinations: {} };
let selectedSourceId = "";
let sourceLoading = false;

function bytes(value) {
  if (!Number.isFinite(value) || value <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toLocaleString("nl-NL", { maximumFractionDigits: 1 })} ${units[i]}`;
}

function duration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "duur onbekend";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return hours
    ? `${hours}u ${String(minutes).padStart(2, "0")}m`
    : `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

function option(value, label, disabled = false) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  item.disabled = disabled;
  return item;
}

function currentSource() {
  return media.sources.find((item) => item.id === $("convert-source").value);
}

function renderTitles() {
  const source = currentSource();
  const select = $("convert-title");
  const previous = select.value;
  select.replaceChildren();
  if (!source) {
    select.append(option("0", "Geen titel beschikbaar"));
    $("source-note").textContent = "Geen VIDEO_TS-map of TS/VOB-bestand gevonden. Plaats een DVD-Video, of kopieer/importeer eerst naar de NAS-bibliotheek.";
    return;
  }
  const supported = source.titles.filter((item) => item.supported);
  if (supported.length > 1) {
    select.append(option("0", "Langste titel (aanbevolen)"));
  }
  for (const title of source.titles) {
    select.append(option(
      String(title.number),
      `Titel ${title.number} · ${duration(title.duration_seconds)} · ${title.chapters} hoofdstukken`,
      !title.supported,
    ));
  }
  const stillThere = [...select.options].some((item) => item.value === previous && !item.disabled);
  select.value = stillThere ? previous : String(supported[0]?.number ?? source.default_title);
  $("source-note").textContent = source.note || `${source.kind === "dvd" ? "DVD-video" : "Videobestand"} · ${source.label}`;
}

function updateDestination() {
  const key = $("convert-destination").value;
  const item = media.destinations[key];
  const path = item?.path || (key === "music" ? "/volume1/video/Muziek" : "/volume1/video/Films");
  $("convert-path").textContent = path + (item?.ready === false ? " · map niet gekoppeld" : "");
}

function renderMedia(data) {
  media = data;
  const select = $("convert-source");
  const previous = selectedSourceId || select.value;
  select.replaceChildren();
  if (!data.sources.length) {
    select.append(option("", "Geen omzetbare bronnen gevonden"));
  }
  for (const source of data.sources) {
    const where = source.location === "schijf" ? "Schijf" : "NAS";
    select.append(option(source.id, `${source.label} · ${where} · ${source.title_count} titel(s)`, !source.default_title));
  }
  const exists = data.sources.some((item) => item.id === previous);
  select.value = exists ? previous : data.sources.find((item) => item.default_title)?.id || "";
  selectedSourceId = select.value;
  renderTitles();
  updateDestination();
}

function renderConvert(data) {
  const converting = data.convert_state === "working";
  const source = currentSource();
  const title = source?.titles.find((item) => item.number === Number($("convert-title").value));
  $("convert-button").disabled = busy || converting || data.copy_state === "copying"
    || data.image_state === "working" || !title?.supported;
  $("cancel-convert").classList.toggle("hidden", !converting);
  $("cancel-convert").disabled = busy;
  $("convert-progress").classList.toggle("hidden", !["working", "done", "error"].includes(data.convert_state));
  $("convert-bar").value = data.convert_percent || 0;
  $("convert-percent").textContent = `${data.convert_percent || 0}%`;
  const feedback = $("convert-feedback");
  feedback.className = "action-feedback";
  if (data.convert_state === "error") {
    feedback.classList.add("error");
    feedback.textContent = data.convert_error || data.error || "Omzetten mislukt.";
    $("convert-label").textContent = "Omzetten mislukt";
  } else if (data.convert_state === "done") {
    feedback.textContent = data.convert_file ? `Klaar: ${data.convert_file}` : "MP4 klaar voor Jellyfin.";
    $("convert-label").textContent = "MP4 klaar";
  } else {
    $("convert-label").textContent = converting ? "Omzetten naar MP4…" : "Omzetten";
  }
  const speed = data.convert_speed > 0 ? `${data.convert_speed.toFixed(2)}×` : "snelheid bepalen…";
  const eta = Number.isFinite(data.convert_eta) && data.convert_eta > 0 ? ` · nog ~${duration(data.convert_eta)}` : "";
  const read = data.convert_bytes > 0 ? ` · ${bytes(data.convert_bytes)} gelezen` : "";
  $("convert-meta").textContent = converting ? `${speed}${eta}${read}` : data.convert_file || "";
}

function render(data) {
  latest = data;
  const [label, kind] = stateLabels[data.state] || [data.state, ""];
  const converting = data.convert_state === "working";
  const working = data.copy_state === "copying" || data.image_state === "working" || converting;
  const badge = $("state-badge");
  badge.textContent = working ? "Bezig" : label;
  badge.className = `badge ${working ? "working" : kind}`;
  $("status-title").textContent = converting ? "Omzetten naar MP4" : titleLabels[data.state] || "DVD Bridge";
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
  $("close-button").disabled = busy || working || !["empty", "open", "error"].includes(data.state);
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
  renderConvert(data);
}

async function refreshMedia() {
  if (sourceLoading) return;
  sourceLoading = true;
  $("refresh-sources").disabled = true;
  try {
    const response = await fetch("/api/media", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderMedia(await response.json());
  } catch {
    $("convert-feedback").className = "action-feedback error";
    $("convert-feedback").textContent = "De bronnenlijst kon niet worden geladen.";
  } finally {
    sourceLoading = false;
    $("refresh-sources").disabled = false;
    if (latest) renderConvert(latest);
  }
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

async function post(url, payload, feedbackId) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-DVD-Bridge": "1" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

async function command(name, confirmation) {
  if (confirmation && !window.confirm(confirmation)) return;
  busy = true;
  const feedback = $("action-feedback");
  feedback.className = "action-feedback";
  feedback.textContent = "Opdracht verzenden…";
  if (latest) render(latest);
  try {
    await post("/api/command", { command: name }, "action-feedback");
    feedback.textContent = "Opdracht ontvangen. De status wordt bijgewerkt.";
    if (name === "eject" || name === "close") setTimeout(refreshMedia, 2000);
  } catch (error) {
    feedback.className = "action-feedback error";
    feedback.textContent = error.message || "De opdracht is niet verzonden.";
  } finally {
    busy = false;
    await refresh();
  }
}

async function startConversion() {
  const source = currentSource();
  if (!source) return;
  const payload = {
    source_id: source.id,
    title: Number($("convert-title").value),
    mode: $("convert-mode").value,
    destination: $("convert-destination").value,
    name: $("convert-name").value.trim(),
  };
  busy = true;
  $("convert-feedback").className = "action-feedback";
  $("convert-feedback").textContent = "Conversie starten…";
  if (latest) render(latest);
  try {
    await post("/api/convert", payload, "convert-feedback");
    $("convert-feedback").textContent = "Conversie gestart. Laat de NAS en speler aangesloten.";
  } catch (error) {
    $("convert-feedback").className = "action-feedback error";
    $("convert-feedback").textContent = error.message || "De conversie is niet gestart.";
  } finally {
    busy = false;
    await refresh();
  }
}

$("copy-button").addEventListener("click", () => command("copy"));
$("iso-button").addEventListener("click", () => command("iso", "Een ISO kopieert de hele schijf naar de NAS en kan veel ruimte kosten. Doorgaan?"));
$("close-button").addEventListener("click", () => command("close"));
$("eject-button").addEventListener("click", () => command("eject", "De schijf eerst loskoppelen en daarna uitwerpen?"));
$("retry-button").addEventListener("click", () => command("retry"));
$("refresh-sources").addEventListener("click", refreshMedia);
$("convert-source").addEventListener("change", () => { selectedSourceId = $("convert-source").value; renderTitles(); if (latest) renderConvert(latest); });
$("convert-title").addEventListener("change", () => { if (latest) renderConvert(latest); });
$("convert-mode").addEventListener("change", () => {
  $("convert-destination").value = $("convert-mode").value === "audio" ? "music" : "films";
  updateDestination();
});
$("convert-destination").addEventListener("change", updateDestination);
$("convert-button").addEventListener("click", startConversion);
$("cancel-convert").addEventListener("click", async () => {
  try {
    await post("/api/command", { command: "cancel" }, "convert-feedback");
    $("convert-feedback").textContent = "Annuleren aangevraagd…";
  } catch (error) {
    $("convert-feedback").className = "action-feedback error";
    $("convert-feedback").textContent = error.message;
  }
});
refresh();
refreshMedia();
setInterval(refresh, 3000);
setInterval(refreshMedia, 15000);

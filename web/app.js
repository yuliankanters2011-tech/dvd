"use strict";

const $ = (id) => document.getElementById(id);
const CSS_NOTICE = "Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.";
const ISO_NOTE = "ISO maken ontgrendelt of ontsleutelt niets. CSS-beveiligde dvd's worden geweigerd.";
const stateLabels = {
  starting: ["Opstarten", "working"],
  ready: ["Beschikbaar", "ready"],
  empty: ["Geen schijf", ""],
  open: ["Lade open", ""],
  missing: ["Speler mist", "error"],
  not_ready: ["Even wachten", "working"],
  mounting: ["Aankoppelen", "working"],
  error: ["Controle nodig", "error"],
  protected: ["CSS-beveiligd", "error"],
  sleeping: ["Slaapstand", "sleeping"],
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
  protected: "CSS-beveiligde dvd",
  sleeping: "De speler slaapt",
  stopped: "De service is gestopt",
  starting: "De speler wordt gecontroleerd",
};

let busy = false;
let latest = null;
let media = { sources: [], skipped_sources: [], destinations: {} };
let selectedSourceId = "";
let selectedTitleValue = "";
let titleResetPending = false;
let lastDiscIdentity = null;
let sourceLoading = false;
let sourceLoaded = false;
let speedChanging = false;
let speedDirty = false;
let idleChanging = false;
let statusTimer = 0;
let mediaTimer = 0;
let statusInFlight = false;
let statusRefreshPending = false;
let statusFailures = 0;
let mediaRefreshPending = false;
let mediaRefreshForce = false;
let mediaVersion = 0;
let mediaOutdated = false;
let lastStatusJson = "";
let mediaLastJson = "";
let mediaError = "";
let lastMediaKey = null;
let connectionState = "";
let lastConvertState = "";

function setText(id, value) {
  const node = $(id);
  const next = value == null ? "" : String(value);
  if (!node || node.textContent === next) return;
  if (node.childNodes.length === 1 && node.firstChild.nodeType === 3) {
    node.firstChild.nodeValue = next;
  } else {
    node.textContent = next;
  }
}

function setClassName(id, value) {
  const node = $(id);
  if (node && node.className !== value) node.className = value;
}

function setHidden(id, value) {
  const node = $(id);
  const next = Boolean(value);
  if (node && node.classList.contains("hidden") !== next) node.classList.toggle("hidden", next);
}

function setDisabled(id, value) {
  const node = $(id);
  const next = Boolean(value);
  if (node && node.disabled !== next) node.disabled = next;
}

function setValue(id, value) {
  const node = $(id);
  const next = value == null ? "" : String(value);
  if (node && node.value !== next) node.value = next;
}

function setAttribute(id, name, value) {
  const node = $(id);
  if (!node) return;
  if (value == null || value === "") {
    if (node.hasAttribute(name)) node.removeAttribute(name);
  } else if (node.getAttribute(name) !== String(value)) {
    node.setAttribute(name, String(value));
  }
}

function setProgress(id, value) {
  const node = $(id);
  const next = Math.max(0, Math.min(100, Number(value) || 0));
  if (node && node.value !== next) node.value = next;
}

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

function optionSpec(value, label, disabled = false) {
  return { value: String(value), label, disabled: Boolean(disabled) };
}

const selectKeys = new WeakMap();

function renderSelect(id, specs, desired) {
  const select = $(id);
  if (!select) return "";
  const key = JSON.stringify(specs);
  if (selectKeys.get(select) !== key) {
    select.replaceChildren(...specs.map((spec) => {
      const item = document.createElement("option");
      item.value = spec.value;
      item.textContent = spec.label;
      item.disabled = spec.disabled;
      return item;
    }));
    selectKeys.set(select, key);
  }
  const wanted = String(desired ?? "");
  const usable = specs.find((spec) => spec.value === wanted && !spec.disabled)
    || specs.find((spec) => !spec.disabled)
    || specs[0];
  const next = usable ? usable.value : "";
  if (select.value !== next) select.value = next;
  return select.value;
}

function currentSource() {
  return media.sources.find((item) => item.id === $("convert-source").value);
}

function renderTitles(reset = false) {
  const source = currentSource();
  const previous = reset || titleResetPending ? "" : selectedTitleValue || $("convert-title").value;
  titleResetPending = false;
  let specs = [];
  let note = "";
  if (!source) {
    specs = [optionSpec("", "Geen titel beschikbaar", true)];
    note = media.skipped_sources?.length
      ? "Er zijn bronnen gevonden die niet konden worden gelezen. Zie de melding hieronder."
      : "Geen VIDEO_TS-map of TS/VOB-bestand gevonden. Plaats een DVD-Video, of kopieer/importeer eerst naar de NAS-bibliotheek.";
  } else {
    const supported = source.titles.filter((item) => item.supported);
    if (supported.length === 0) {
      specs = [optionSpec("0", "Geen ondersteunde titels", true)];
      const reason = source.titles.find((item) => !item.supported && item.reason)?.reason;
      note = reason || source.note || "Deze bron heeft geen ondersteunde titels.";
    } else {
      if (supported.length > 1) specs.push(optionSpec("0", "Langste titel (aanbevolen)"));
      const all = source.all_titles;
      if (supported.length > 1 && all?.supported) {
        const count = all.title_numbers?.length ?? supported.length;
        specs.push(optionSpec(
          "-1",
          `Alle ondersteunde titels in één MP4 · ${duration(all.duration_seconds)} · ${count} titels`,
        ));
      }
      for (const title of source.titles) {
        specs.push(optionSpec(
          String(title.number),
          `Titel ${title.number} · ${duration(title.duration_seconds)} · ${title.chapters} hoofdstukken`,
          !title.supported,
        ));
      }
      note = source.note || `${source.kind === "dvd" ? "DVD-video" : "Videobestand"} · ${source.label}`;
    }
  }
  selectedTitleValue = renderSelect("convert-title", specs, previous);
  setText("source-note", note);
}

function renderSkippedSources(items) {
  const skipped = Array.isArray(items) ? items : [];
  setHidden("source-warnings", skipped.length === 0);
  if (!skipped.length) {
    setText("source-warnings", "");
    return;
  }
  const lines = [`${skipped.length} ${skipped.length === 1 ? "bron" : "bronnen"} overgeslagen:`];
  for (const item of skipped.slice(0, 3)) {
    const where = item.location === "schijf" ? "schijf" : "NAS";
    lines.push(`${item.label || "Bron"} (${where}) · ${item.reason || "Onbekende reden"}\n${item.path || ""}`);
  }
  if (skipped.length > 3) {
    lines.push(`Nog ${skipped.length - 3} overgeslagen; zie /api/media voor details.`);
  }
  setText("source-warnings", lines.join("\n"));
}

function updateDestination() {
  const key = $("convert-destination").value;
  const item = media.destinations[key];
  const path = item?.path || (key === "music" ? "/volume1/video/Muziek" : "/volume1/video/Films");
  setText("convert-path", path + (item?.ready === false ? " · map niet gekoppeld" : ""));
}

function renderSourceStatus() {
  let message = "";
  let warning = false;
  if (sourceLoading && !sourceLoaded) {
    message = "Bronnen laden…";
  } else if (mediaError) {
    message = sourceLoaded
      ? `Verversen mislukt (${mediaError}). De vorige lijst blijft staan.`
      : `Bronnen laden mislukt (${mediaError}). Probeer Bronnen verversen.`;
    warning = true;
  } else if (!sourceLoaded) {
    message = "Nog niet ververst";
  } else if (!media.sources.length) {
    message = media.skipped_sources?.length
      ? "Geen omzetbare bronnen; zie overgeslagen bronnen."
      : "Geen omzetbare bronnen gevonden.";
  } else {
    const skipped = media.skipped_sources?.length || 0;
    message = `${media.sources.length} ${media.sources.length === 1 ? "bron" : "bronnen"} beschikbaar`
      + (skipped ? ` · ${skipped} overgeslagen` : "");
  }
  setText("source-status", message);
  setClassName("source-status", warning ? "source-status warning" : "source-status");
}

function renderMedia(data) {
  media = data;
  mediaError = "";
  mediaOutdated = false;
  sourceLoaded = true;
  const previousSource = selectedSourceId;
  const specs = data.sources.length
    ? data.sources.map((source) => {
      const where = source.location === "schijf" ? "Schijf" : "NAS";
      const suffix = source.protected ? " · CSS-beveiligd"
        : source.default_title ? "" : " · geen ondersteunde titels";
      return optionSpec(
        source.id,
        `${source.label} · ${where} · ${source.title_count} titel(s)${suffix}`,
        source.protected,
      );
    })
    : [optionSpec("", "Geen omzetbare bronnen gevonden", true)];
  const stillUsable = specs.some((spec) => spec.value === selectedSourceId && !spec.disabled);
  const preferred = stillUsable
    ? selectedSourceId
    : data.sources.find((source) => !source.protected && source.default_title)?.id ?? selectedSourceId;
  selectedSourceId = renderSelect("convert-source", specs, preferred);
  const sourceChanged = selectedSourceId !== previousSource;
  if (sourceChanged) selectedTitleValue = "";
  renderTitles(sourceChanged);
  renderSkippedSources(data.skipped_sources);
  updateDestination();
  renderSourceStatus();
}

function selectedTitle(source) {
  const value = Number($("convert-title").value);
  if (value === -1) return source?.all_titles;
  const number = value === 0 ? source?.default_title : value;
  return source?.titles.find((item) => item.number === number);
}

function speedLabel(value) {
  return value === 0 ? "Standaard / maximum" : `${value}× aanvragen`;
}

function renderDrive(data) {
  const working = data.copy_state === "copying" || data.image_state === "working"
    || data.convert_state === "working";
  const sleeping = Boolean(data.drive_sleeping);
  setText("drive-badge", sleeping ? "Slaapt" : "Wakker");
  setClassName("drive-badge", `badge ${sleeping ? "sleeping" : "ready"}`);
  setText("sleep-button", sleeping ? "Wakker worden" : "Slaapstand");
  setDisabled("sleep-button", busy || working || data.state === "starting" || data.state === "stopped"
    || (!sleeping && data.drive_sleep_supported === false));
  setDisabled("sleep-idle", busy || idleChanging);
  if (!idleChanging) setValue("sleep-idle", data.sleep_idle_seconds ?? 0);
  setAttribute("drive-speed", "max", data.drive_speed_max ?? 24);
  setDisabled("drive-speed", busy || working || speedChanging || sleeping
    || data.drive_speed_supported === false || data.state === "starting" || data.state === "stopped");
  if (!speedChanging && !speedDirty) setValue("drive-speed", data.drive_speed ?? 0);
  setText("drive-speed-label", speedLabel(Number($("drive-speed").value)));
  if (data.drive_control_error) {
    setClassName("drive-feedback", "action-feedback warning");
    setText("drive-feedback", data.drive_control_error);
  } else if (!busy && !speedChanging && !idleChanging) {
    setClassName("drive-feedback", "action-feedback");
    setText("drive-feedback", sleeping
      ? "Slaapstand actief. De usb-voeding blijft aan; statuscontroles zijn gepauzeerd."
      : "");
  }
}

function renderConvert(data) {
  const converting = data.convert_state === "working";
  const source = currentSource();
  const title = selectedTitle(source);
  const allSelected = Number($("convert-title").value) === -1;
  const destination = media.destinations[$("convert-destination").value];
  let reason = "";
  if (busy) reason = "Er wordt al een opdracht verzonden.";
  else if (converting) reason = "Er wordt al omgezet.";
  else if (data.copy_state === "copying" || data.image_state === "working") reason = "Wacht tot de huidige bewerking klaar is.";
  else if ((data.css_protected && source?.id === "disc") || source?.protected) reason = CSS_NOTICE;
  else if (mediaOutdated && (!source || source.location === "schijf")) reason = mediaError
    ? "De bronnenlijst is niet geladen. Klik op Bronnen verversen."
    : "Wacht tot de bronnen van de schijf zijn ververst.";
  else if (sourceLoading && !sourceLoaded) reason = "Wacht tot de bronnenlijst is geladen.";
  else if (!source) reason = mediaError
    ? "De bronnenlijst is niet geladen. Klik op Bronnen verversen."
    : "Geen omzetbare bron beschikbaar. Plaats een onbeveiligde dvd of importeer videobestanden.";
  else if (!title?.supported) reason = title?.reason || source.note || "Geen ondersteunde titel beschikbaar.";
  else if (destination?.ready === false) reason = "De doelmap is niet gekoppeld in de container.";
  setDisabled("convert-button", Boolean(reason));
  setAttribute("convert-button", "title", reason);
  setText("convert-reason", reason);
  setText("convert-button", allSelected ? "Alles in één MP4" : "Omzetten naar MP4");
  setHidden("cancel-convert", !converting);
  setDisabled("cancel-convert", busy);
  setText("cancel-convert", busy ? "Annuleren…" : "Annuleren");
  setHidden("convert-progress", !["working", "done", "error"].includes(data.convert_state));
  setProgress("convert-bar", data.convert_percent);
  setText("convert-percent", `${data.convert_percent || 0}%`);
  if (lastConvertState && lastConvertState !== data.convert_state
    && (data.convert_state === "idle" || data.convert_state === "working")) {
    setClassName("convert-feedback", "action-feedback");
    setText("convert-feedback", "");
  }
  if (data.convert_state === "error") {
    setClassName("convert-feedback", "action-feedback error");
    setText("convert-feedback", data.convert_error || data.error || "Omzetten mislukt.");
    setText("convert-label", "Omzetten mislukt");
  } else if (data.convert_state === "done") {
    setClassName("convert-feedback", "action-feedback");
    setText("convert-feedback", data.convert_file ? `Klaar: ${data.convert_file}` : "MP4 klaar voor Jellyfin.");
    setText("convert-label", "MP4 klaar");
  } else {
    setText("convert-label", converting
      ? (data.convert_title === -1 ? "Alle titels omzetten naar één MP4…" : "Omzetten naar MP4…")
      : "Omzetten");
  }
  lastConvertState = data.convert_state;
  const speed = data.convert_speed > 0 ? `${data.convert_speed.toFixed(2)}×` : "snelheid bepalen…";
  const eta = Number.isFinite(data.convert_eta) && data.convert_eta > 0 ? ` · nog ~${duration(data.convert_eta)}` : "";
  const read = data.convert_bytes > 0 ? ` · ${bytes(data.convert_bytes)} gelezen` : "";
  const titles = data.convert_title === -1 && data.convert_titles?.length
    ? ` · ${data.convert_titles.length} titels` : "";
  setText("convert-meta", converting ? `${speed}${eta}${read}${titles}` : data.convert_file || "");
}

function render(data) {
  latest = data;
  const [label, kind] = stateLabels[data.state] || [data.state, ""];
  const converting = data.convert_state === "working";
  const working = data.copy_state === "copying" || data.image_state === "working" || converting;
  const protectedDisc = Boolean(data.css_protected);
  setText("state-badge", working ? "Bezig" : protectedDisc ? "CSS-beveiligd" : label);
  setClassName("state-badge", `badge ${working ? "working" : protectedDisc ? "error" : kind}`);
  setText("status-title", converting ? "Omzetten naar MP4"
    : data.image_state === "working" ? "ISO maken"
      : data.copy_state === "copying" ? "Bestanden kopiëren"
        : protectedDisc ? "CSS-beveiligde dvd"
          : titleLabels[data.state] || "DVD Bridge");
  setText("status-message", data.message || "");
  setHidden("protection-banner", !protectedDisc);
  setText("disc-label", data.disc?.label || "—");
  setText("disc-format", data.disc?.filesystem ? data.disc.filesystem.toUpperCase() : "—");
  setText("disc-size", bytes(data.disc?.size_bytes));
  setText("device", data.device || "—");
  setText("nas-folder", data.nas_folder || "—");
  setText("library-folder", data.library_folder || "—");
  setText("version", data.version ? `v${data.version}` : "");
  setClassName("disc-visual", `disc-visual ${data.state === "ready" ? "ready" : ""} ${working ? "working" : ""}`.trim());
  renderDrive(data);

  const ready = data.state === "ready" && !data.drive_sleeping && !protectedDisc;
  const actionReason = protectedDisc
    ? `${CSS_NOTICE} Kopiëren, ISO maken en omzetten van deze schijf zijn geblokkeerd. ${ISO_NOTE}`
    : working ? "Er is al een bewerking bezig."
      : data.drive_sleeping ? "De speler slaapt. Klik op Wakker worden voor een opdracht op de schijf."
        : !ready ? "Plaats een leesbare, onbeveiligde schijf of wacht tot de speler klaar is."
          : "";
  setText("action-reason", actionReason);
  setDisabled("copy-button", busy || working || !ready || data.copy_state === "done");
  setAttribute("copy-button", "title", actionReason);
  setDisabled("iso-button", busy || working || !ready || data.image_force_available || protectedDisc);
  setAttribute("iso-button", "title", actionReason || ISO_NOTE);
  setText("iso-button", data.image_resumable && !data.image_force_available ? "ISO hervatten" : "ISO maken");
  setDisabled("close-button", busy || working || !["empty", "open", "error", "protected", "sleeping"].includes(data.state));
  setDisabled("eject-button", busy || working || !["ready", "empty", "open", "error", "protected", "sleeping"].includes(data.state));
  setHidden("retry-button", data.state !== "error");
  setDisabled("retry-button", busy || working);

  const copying = data.copy_state === "copying";
  setHidden("copy-progress", !["copying", "done", "error", "cancelled"].includes(data.copy_state));
  setProgress("copy-bar", data.copy_percent);
  setText("copy-percent", `${data.copy_percent || 0}%`);
  setText("copy-label", data.copy_state === "done" ? "Kopiëren klaar"
    : data.copy_state === "error" ? "Kopiëren mislukt"
      : data.copy_state === "cancelled" ? "Kopiëren geannuleerd"
        : `Kopiëren · ${bytes(data.copied_bytes)} / ${bytes(data.total_bytes)}`);
  setHidden("cancel-copy", !copying);
  setDisabled("cancel-copy", busy || data.copy_cancel_requested);
  setText("cancel-copy", data.copy_cancel_requested ? "Annuleren aangevraagd…" : "Kopiëren annuleren");
  if (data.copy_state === "error") {
    setClassName("copy-feedback", "action-feedback error");
    setText("copy-feedback", data.copy_error || data.error || "Kopiëren mislukt.");
  } else if (data.copy_state === "cancelled") {
    setClassName("copy-feedback", "action-feedback");
    setText("copy-feedback", data.message || "Geannuleerd. Bestaande imports zijn niet geraakt.");
  } else {
    setClassName("copy-feedback", "action-feedback");
    setText("copy-feedback", "");
  }

  const imaging = data.image_state === "working";
  setHidden("image-progress", !["working", "done", "error", "cancelled"].includes(data.image_state));
  setProgress("image-bar", data.image_percent);
  setText("image-percent", `${data.image_percent || 0}%`);
  setText("image-label", data.image_state === "done"
    ? (data.image_complete === false ? "Beschadigde ISO opgeslagen" : "ISO klaar")
    : data.image_state === "error" ? "ISO maken gepauzeerd"
      : data.image_state === "cancelled" ? "ISO niet opgeslagen"
        : "ISO maken…");
  setHidden("cancel-iso", !imaging && !data.image_force_available);
  setDisabled("cancel-iso", busy || data.image_cancel_requested);
  setText("cancel-iso", data.image_force_available ? "Niet opslaan"
    : data.image_cancel_requested ? "Annuleren aangevraagd…" : "ISO annuleren");
  setHidden("force-iso", !data.image_force_available || protectedDisc);
  setDisabled("force-iso", busy || working || !ready || protectedDisc);
  if (data.image_state === "error") {
    setClassName("image-feedback", data.image_force_available ? "action-feedback warning" : "action-feedback error");
    setText("image-feedback", data.image_error || data.error || "ISO maken mislukt.");
  } else if (data.image_state === "cancelled") {
    setClassName("image-feedback", "action-feedback");
    setText("image-feedback", data.image_resumable
      ? "Het deelresultaat is bewaard. Klik op ISO hervatten om later verder te gaan."
      : data.message || "ISO maken geannuleerd.");
  } else if (data.image_state === "done") {
    setClassName("image-feedback", data.image_complete === false ? "action-feedback warning" : "action-feedback");
    setText("image-feedback", data.image_file ? `Klaar: ${data.image_file}` : "ISO opgeslagen.");
  } else {
    setClassName("image-feedback", "action-feedback");
    setText("image-feedback", "");
  }
  const missing = Number(data.image_missing_bytes) || 0;
  const expected = Number(data.image_expected_bytes) || 0;
  const damage = missing > 0 && expected > 0
    ? `${bytes(missing)} onleesbaar van ${bytes(expected)} (${(missing * 100 / expected).toLocaleString("nl-NL", { maximumFractionDigits: 2 })}%)`
    : "";
  setText("image-meta", data.image_state === "done" && data.image_complete === false
    ? `Let op: beschadigde ISO${data.image_forced ? " (bewust doorgegaan)" : ""}. ${damage}. De naastgelegen .json-markering vermeldt de schade.`
    : damage);

  const identity = data.disc?.identity || "";
  if (identity !== lastDiscIdentity) {
    const hadPreviousIdentity = lastDiscIdentity !== null;
    lastDiscIdentity = identity;
    if (hadPreviousIdentity) titleResetPending = true;
  }
  const mediaKey = `${data.state}|${protectedDisc}|${identity}|${data.drive_sleeping}`;
  if (mediaKey !== lastMediaKey) {
    const firstStatus = lastMediaKey === null;
    lastMediaKey = mediaKey;
    // The initial media scan is already scheduled at boot. Rescan when a
    // later disc/state change arrives, or when the boot scan already ran
    // (or is still running) before the first status: that scan may have
    // seen the drive before the disc was mounted. Until a fresh scan
    // arrives, block conversion of the live disc so stale titles cannot
    // be submitted for a different disc.
    if (!firstStatus || sourceLoaded || sourceLoading) {
      mediaOutdated = true;
      mediaVersion += 1;
      requestMediaRefresh(400);
    }
  }
  renderConvert(data);
}

function setConnection(state) {
  if (connectionState === state) return;
  connectionState = state;
  setClassName("connection", `connection ${state}`);
  setText("connection-label", state === "online" ? "Verbonden"
    : state === "offline" ? "Geen verbinding" : "Verbinden…");
}

function isWorking(data) {
  return Boolean(data && (data.copy_state === "copying" || data.image_state === "working" || data.convert_state === "working"));
}

function statusDelay() {
  if (statusFailures > 0) return Math.min(30000, 2000 * 2 ** (statusFailures - 1));
  if (!latest) return 1500;
  if (isWorking(latest)) return 1500;
  if (["starting", "not_ready", "mounting"].includes(latest.state)) return 2000;
  if (latest.drive_sleeping) return 10000;
  if (["empty", "open"].includes(latest.state)) return 5000;
  return 4000;
}

function mediaDelay() {
  return isWorking(latest) ? 15000 : 30000;
}

function scheduleStatus() {
  clearTimeout(statusTimer);
  if (document.visibilityState === "hidden") return;
  statusTimer = setTimeout(refresh, statusDelay());
}

function scheduleMedia() {
  clearTimeout(mediaTimer);
  if (document.visibilityState === "hidden") return;
  mediaTimer = setTimeout(() => refreshMedia(), mediaDelay());
}

function requestMediaRefresh(delay = 0, force = false) {
  clearTimeout(mediaTimer);
  if (document.visibilityState === "hidden") return;
  if (sourceLoading) {
    mediaRefreshPending = true;
    mediaRefreshForce = mediaRefreshForce || force;
    return;
  }
  mediaTimer = setTimeout(() => refreshMedia({ force }), delay);
}

async function fetchText(url, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const raw = await response.text();
    return { response, raw };
  } catch (error) {
    if (controller.signal.aborted) throw new Error("time-out van de verbinding");
    if (error instanceof TypeError) throw new Error("netwerkfout");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function refreshMedia(options = {}) {
  if (sourceLoading) {
    mediaRefreshPending = true;
    mediaRefreshForce = mediaRefreshForce || Boolean(options.force);
    return;
  }
  sourceLoading = true;
  const version = mediaVersion;
  const showLoading = Boolean(options.force) || !sourceLoaded;
  if (showLoading) {
    setText("refresh-sources", "Verversen…");
    setAttribute("refresh-sources", "aria-busy", "true");
    setAttribute("convert-source", "aria-busy", "true");
  }
  renderSourceStatus();
  try {
    const { response, raw } = await fetchText("/api/media", { cache: "no-store" }, 30000);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      throw new Error("ongeldig JSON-antwoord");
    }
    if (version !== mediaVersion) {
      // A disc/state change happened while the NAS was scanned. Ignore this
      // older snapshot and request one fresh scan instead of flashing it.
      mediaRefreshPending = true;
    } else if (raw !== mediaLastJson || options.force) {
      mediaLastJson = raw;
      renderMedia(data);
    } else {
      mediaError = "";
      mediaOutdated = false;
      sourceLoaded = true;
      if (titleResetPending) renderTitles();
      renderSourceStatus();
    }
  } catch (error) {
    if (version === mediaVersion) {
      mediaError = error?.message || "netwerkfout";
      renderSourceStatus();
    }
  } finally {
    sourceLoading = false;
    if (showLoading) {
      setText("refresh-sources", "Bronnen verversen");
      setAttribute("refresh-sources", "aria-busy", "false");
      setAttribute("convert-source", "aria-busy", "false");
    }
    renderSourceStatus();
    if (latest) renderConvert(latest);
    if (mediaRefreshPending) {
      const force = mediaRefreshForce;
      mediaRefreshPending = false;
      mediaRefreshForce = false;
      requestMediaRefresh(250, force);
    } else {
      scheduleMedia();
    }
  }
}

async function refresh() {
  if (statusInFlight) {
    statusRefreshPending = true;
    return;
  }
  statusInFlight = true;
  try {
    const { response, raw } = await fetchText("/api/status", { cache: "no-store" }, 8000);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    statusFailures = 0;
    const data = JSON.parse(raw);
    if (raw !== lastStatusJson) {
      lastStatusJson = raw;
      render(data);
    }
    setConnection("online");
  } catch {
    statusFailures += 1;
    setConnection("offline");
  } finally {
    statusInFlight = false;
    if (statusRefreshPending) {
      statusRefreshPending = false;
      clearTimeout(statusTimer);
      if (document.visibilityState !== "hidden") statusTimer = setTimeout(refresh, 0);
    } else {
      scheduleStatus();
    }
  }
}

async function post(url, payload) {
  const { response, raw } = await fetchText(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-DVD-Bridge": "1" },
    body: JSON.stringify(payload),
  }, 10000);
  let result = {};
  try {
    result = JSON.parse(raw);
  } catch {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    throw new Error("Ongeldig antwoord van de server.");
  }
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

async function command(name, confirmation) {
  if (busy) return;
  if (confirmation && !window.confirm(confirmation)) return;
  busy = true;
  setClassName("action-feedback", "action-feedback");
  setText("action-feedback", "Opdracht verzenden…");
  if (latest) render(latest);
  try {
    await post("/api/command", { command: name });
    setText("action-feedback", "Opdracht ontvangen. De status wordt bijgewerkt.");
    if (name === "eject" || name === "close") requestMediaRefresh(1500);
  } catch (error) {
    setClassName("action-feedback", "action-feedback error");
    setText("action-feedback", error.message || "De opdracht is niet verzonden.");
  } finally {
    busy = false;
    await refresh();
  }
}

async function changeSpeed() {
  if (speedChanging || busy) return;
  const value = Number($("drive-speed").value);
  speedChanging = true;
  setClassName("drive-feedback", "action-feedback");
  setText("drive-feedback", "Draaisnelheid aanvragen…");
  if (latest) renderDrive(latest);
  try {
    await post("/api/command", { command: "speed", speed: value });
    setText("drive-feedback", "Snelheidsverzoek ontvangen. De status wordt bijgewerkt.");
  } catch (error) {
    setClassName("drive-feedback", "action-feedback error");
    setText("drive-feedback", error.message || "De snelheid is niet verzonden.");
  } finally {
    speedChanging = false;
    speedDirty = false;
    await refresh();
  }
}

async function changeIdle() {
  if (idleChanging || busy) return;
  const value = Number($("sleep-idle").value);
  idleChanging = true;
  setClassName("drive-feedback", "action-feedback");
  setText("drive-feedback", "Slaaptimer opslaan…");
  if (latest) renderDrive(latest);
  try {
    await post("/api/command", { command: "sleep_idle", seconds: value });
    setText("drive-feedback", value === 0 ? "Automatische slaapstand uit." : "Slaaptimer opgeslagen.");
  } catch (error) {
    setClassName("drive-feedback", "action-feedback error");
    setText("drive-feedback", error.message || "De slaaptimer is niet opgeslagen.");
  } finally {
    idleChanging = false;
    await refresh();
  }
}

async function startConversion() {
  if (busy || $("convert-button").disabled) return;
  const source = currentSource();
  const title = selectedTitle(source);
  const destination = media.destinations[$("convert-destination").value];
  if (!source || source.protected || (latest?.css_protected && source.id === "disc")
    || !title?.supported || destination?.ready === false) return;
  const payload = {
    source_id: source.id,
    title: Number($("convert-title").value),
    mode: $("convert-mode").value,
    destination: $("convert-destination").value,
    name: $("convert-name").value.trim(),
  };
  busy = true;
  setClassName("convert-feedback", "action-feedback");
  setText("convert-feedback", "Conversie starten…");
  if (latest) render(latest);
  try {
    await post("/api/convert", payload);
    setText("convert-feedback", "Conversie gestart. Laat de NAS en speler aangesloten.");
  } catch (error) {
    setClassName("convert-feedback", "action-feedback error");
    setText("convert-feedback", error.message || "De conversie is niet gestart.");
  } finally {
    busy = false;
    await refresh();
  }
}

$("copy-button").addEventListener("click", () => command("copy"));
$("iso-button").addEventListener("click", () => command("iso", "Een ISO kopieert de hele schijf naar de NAS zonder ontgrendeling of ontsleuteling. CSS-beveiligde dvd's worden geweigerd. Kan veel ruimte kosten. Doorgaan?"));
$("cancel-copy").addEventListener("click", () => command("cancel_copy"));
$("cancel-iso").addEventListener("click", () => command("cancel_iso"));
$("force-iso").addEventListener("click", () => command(
  "iso_force",
  "Deze schijf is sterk beschadigd. De ISO kan onvolledig zijn en bestanden kunnen beschadigd blijven. Toch doorgaan en de beschadigde ISO opslaan?",
));
$("close-button").addEventListener("click", () => command("close"));
$("eject-button").addEventListener("click", () => command("eject", "De schijf eerst loskoppelen en daarna uitwerpen?"));
$("retry-button").addEventListener("click", () => command("retry"));
$("sleep-button").addEventListener("click", () => command(latest?.drive_sleeping ? "wake" : "sleep"));
$("drive-speed").addEventListener("input", () => {
  speedDirty = true;
  setText("drive-speed-label", speedLabel(Number($("drive-speed").value)));
});
$("drive-speed").addEventListener("change", changeSpeed);
$("sleep-idle").addEventListener("change", changeIdle);
$("refresh-sources").addEventListener("click", () => refreshMedia({ force: true }));
$("convert-source").addEventListener("change", () => {
  selectedSourceId = $("convert-source").value;
  selectedTitleValue = "";
  renderTitles(true);
  if (latest) renderConvert(latest);
});
$("convert-title").addEventListener("change", () => {
  selectedTitleValue = $("convert-title").value;
  if (latest) renderConvert(latest);
});
$("convert-mode").addEventListener("change", () => {
  setValue("convert-destination", $("convert-mode").value === "audio" ? "music" : "films");
  updateDestination();
  if (latest) renderConvert(latest);
});
$("convert-destination").addEventListener("change", () => {
  updateDestination();
  if (latest) renderConvert(latest);
});
$("convert-button").addEventListener("click", startConversion);
$("cancel-convert").addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  if (latest) renderConvert(latest);
  try {
    await post("/api/command", { command: "cancel" });
    setText("convert-feedback", "Annuleren aangevraagd…");
  } catch (error) {
    setClassName("convert-feedback", "action-feedback error");
    setText("convert-feedback", error.message || "Annuleren is niet verzonden.");
  } finally {
    busy = false;
    await refresh();
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    clearTimeout(statusTimer);
    clearTimeout(mediaTimer);
  } else {
    refresh();
    requestMediaRefresh(0);
  }
});
window.addEventListener("online", () => {
  refresh();
  requestMediaRefresh(0);
});
window.addEventListener("offline", () => setConnection("offline"));

setConnection("connecting");
setText("convert-button", "Omzetten naar MP4");
setDisabled("copy-button", true);
setDisabled("iso-button", true);
setDisabled("close-button", true);
setDisabled("eject-button", true);
setDisabled("sleep-button", true);
setDisabled("convert-button", true);
setText("convert-reason", "Wacht tot de status en bronnen zijn geladen.");
refresh();
requestMediaRefresh(0);

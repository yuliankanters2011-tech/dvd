import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { JSDOM } from "jsdom";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const html = readFileSync(join(root, "web", "index.html"), "utf8");
const script = readFileSync(join(root, "web", "app.js"), "utf8");

function title(number, duration = 120, supported = true, reason = "") {
  return {
    number,
    duration_seconds: duration,
    chapters: 2,
    size_bytes: 1024,
    supported,
    reason,
  };
}

function source(id, overrides = {}) {
  const titles = overrides.titles ?? [title(1), title(2, 240)];
  return {
    id,
    location: id === "disc" ? "schijf" : "bibliotheek",
    label: id === "disc" ? "Vakantie" : "NAS-film",
    kind: "dvd",
    note: "",
    protected: false,
    default_title: 2,
    title_count: titles.length,
    titles,
    all_titles: {
      number: -1,
      duration_seconds: 360,
      chapters: 4,
      size_bytes: 2048,
      supported: true,
      reason: "",
      title_numbers: [1, 2],
    },
    ...overrides,
  };
}

function status(overrides = {}) {
  return {
    version: "1.2.9",
    state: "ready",
    message: "Je schijf is beschikbaar.",
    device: "/dev/sr0",
    disc: { identity: "disc-1", label: "Vakantie", filesystem: "udf", size_bytes: 4096 },
    css_protected: false,
    nas_folder: "/srv/dvd/disc",
    library_folder: "/srv/dvd/library",
    copy_state: "idle",
    copy_percent: 0,
    copied_bytes: 0,
    total_bytes: 0,
    copy_error: "",
    copy_cancel_requested: false,
    image_state: "idle",
    image_percent: 0,
    image_file: "",
    image_error: "",
    image_missing_bytes: 0,
    image_expected_bytes: 0,
    image_complete: true,
    image_forced: false,
    image_force_available: false,
    image_resumable: false,
    image_cancel_requested: false,
    convert_state: "idle",
    convert_percent: 0,
    convert_speed: 0,
    convert_eta: null,
    convert_file: "",
    convert_title: 0,
    convert_titles: [],
    convert_error: "",
    convert_bytes: 0,
    drive_sleeping: false,
    drive_sleep_supported: true,
    drive_speed: 0,
    drive_speed_max: 24,
    drive_speed_supported: true,
    sleep_idle_seconds: 600,
    drive_control_error: "",
    error: "",
    updated_at: 1,
    ...overrides,
  };
}

function media(overrides = {}) {
  return {
    sources: [source("disc"), source("nas-1")],
    skipped_sources: [],
    destinations: {
      films: { path: "/volume1/video/Films", ready: true },
      music: { path: "/volume1/video/Muziek", ready: true },
    },
    ...overrides,
  };
}

function jsonResponse(payload, ok = true, statusCode = 200) {
  const raw = JSON.stringify(payload);
  return {
    ok,
    status: statusCode,
    text: async () => raw,
    json: async () => JSON.parse(raw),
  };
}

async function flush() {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

function makeDashboard({ statusData = status(), mediaData = media(), fetchImpl } = {}) {
  const dom = new JSDOM(html, {
    url: "http://dvd.local/",
    runScripts: "outside-only",
  });
  const { window } = dom;
  const state = { statusData, mediaData, statusCalls: 0, mediaCalls: 0, posts: [] };
  const defaultFetch = async (url, options = {}) => {
    if (options.method === "POST") {
      state.posts.push({ url, payload: JSON.parse(options.body), headers: options.headers });
      return jsonResponse({ accepted: true }, true, 202);
    }
    if (url === "/api/status") {
      state.statusCalls += 1;
      return jsonResponse(state.statusData);
    }
    if (url === "/api/media") {
      state.mediaCalls += 1;
      return jsonResponse(state.mediaData);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
  window.fetch = fetchImpl ?? defaultFetch;
  window.confirm = () => true;
  window.AbortController = AbortController;

  let now = 0;
  let nextTimerId = 1;
  const timers = new Map();
  window.setTimeout = (fn, ms = 0, ...args) => {
    const id = nextTimerId;
    nextTimerId += 1;
    timers.set(id, { at: now + Math.max(0, Number(ms) || 0), fn, args });
    return id;
  };
  window.clearTimeout = (id) => timers.delete(id);

  window.eval(script);
  return {
    window,
    document: window.document,
    state,
    close: () => {
      timers.clear();
      window.fetch = async () => {
        throw new Error("dashboard closed");
      };
      window.close();
    },
    async tick(ms = 0) {
      await flush();
      const end = now + Math.max(0, Number(ms) || 0);
      for (let guard = 0; guard < 10000; guard += 1) {
        let next = null;
        for (const [id, timer] of timers) {
          if (timer.at > end) continue;
          if (!next || timer.at < next.timer.at || (timer.at === next.timer.at && id < next.id)) {
            next = { id, timer };
          }
        }
        if (!next) break;
        now = next.timer.at;
        timers.delete(next.id);
        next.timer.fn(...next.timer.args);
        await flush();
      }
      now = end;
      await flush();
    },
    id(id) {
      return window.document.getElementById(id);
    },
  };
}

test("initial dashboard renders sources and keeps unprotected actions available", async () => {
  const dash = makeDashboard();
  await dash.tick(20);
  assert.equal(dash.id("connection-label").textContent, "Verbonden");
  assert.equal(dash.id("version").textContent, "v1.2.9");
  assert.equal(dash.id("convert-source").options.length, 2);
  assert.equal(dash.id("convert-source").value, "disc");
  assert.equal(dash.id("convert-title").value, "0");
  assert.equal(dash.id("copy-button").disabled, false);
  assert.equal(dash.id("iso-button").disabled, false);
  assert.equal(dash.id("convert-button").disabled, false);
  assert.equal(dash.id("protection-banner").classList.contains("hidden"), true);
  assert.match(dash.id("iso-help").textContent, /zonder ontgrendeling of ontsleuteling/);
  dash.close();
});

test("CSS disc blocks copy/ISO but leaves tray, sleep and NAS conversion available", async () => {
  const dash = makeDashboard({
    statusData: status({
      state: "protected",
      css_protected: true,
      message: "Deze dvd is CSS-beveiligd. DVD Bridge ondersteunt alleen onbeveiligde DVD-Video.",
    }),
    mediaData: media({
      sources: [source("nas-1")],
      skipped_sources: [{ location: "schijf", label: "Madagascar", path: "/srv/dvd/disc", reason: "CSS" }],
    }),
  });
  await dash.tick(20);
  assert.equal(dash.id("copy-button").disabled, true);
  assert.equal(dash.id("iso-button").disabled, true);
  assert.equal(dash.id("eject-button").disabled, false);
  assert.equal(dash.id("close-button").disabled, false);
  assert.equal(dash.id("sleep-button").disabled, false);
  assert.equal(dash.id("protection-banner").classList.contains("hidden"), false);
  assert.match(dash.id("action-reason").textContent, /ISO maken ontgrendelt of ontsleutelt niets/);
  assert.equal(dash.id("convert-source").value, "nas-1");
  assert.equal(dash.id("convert-button").disabled, false);
  assert.match(dash.id("source-warnings").textContent, /Madagascar/);
  dash.close();
});

test("protected source option is disabled and conversion explains why", async () => {
  const dash = makeDashboard({
    mediaData: media({ sources: [source("disc", { protected: true, default_title: 0, titles: [] })] }),
  });
  await dash.tick(20);
  assert.equal(dash.id("convert-source").options[0].disabled, true);
  assert.equal(dash.id("convert-button").disabled, true);
  assert.match(dash.id("convert-reason").textContent, /CSS-beveiligd/);
  dash.close();
});

test("unchanged polling does not rebuild options or connection DOM", async () => {
  const dash = makeDashboard();
  await dash.tick(20);
  const sourceSelect = dash.id("convert-source");
  const titleSelect = dash.id("convert-title");
  const firstSourceOption = sourceSelect.options[0];
  const firstTitleOption = titleSelect.options[0];
  const connectionLabel = dash.id("connection-label");
  const connectionText = connectionLabel.firstChild;
  dash.state.statusData = { ...dash.state.statusData, updated_at: 2 };
  await dash.tick(4100);
  assert.equal(sourceSelect.options[0], firstSourceOption);
  assert.equal(titleSelect.options[0], firstTitleOption);
  assert.equal(dash.id("connection-label"), connectionLabel);
  assert.equal(connectionLabel.firstChild, connectionText);
  assert.equal(dash.state.statusCalls >= 2, true);
  dash.close();
});

test("source and title selection survive a changed media refresh", async () => {
  const dash = makeDashboard();
  await dash.tick(20);
  dash.id("convert-source").value = "nas-1";
  dash.id("convert-source").dispatchEvent(new dash.window.Event("change"));
  dash.id("convert-title").value = "-1";
  dash.id("convert-title").dispatchEvent(new dash.window.Event("change"));
  dash.id("convert-name").focus();
  dash.state.mediaData = media({
    sources: [source("disc"), source("nas-1", { label: "NAS-film bijgewerkt" })],
  });
  await dash.tick(30_100);
  assert.equal(dash.id("convert-source").value, "nas-1");
  assert.equal(dash.id("convert-title").value, "-1");
  assert.equal(dash.document.activeElement, dash.id("convert-name"));
  assert.equal(dash.id("convert-button").textContent, "Alles in één MP4");
  dash.close();
});

test("media failure keeps the previous list and offers a retry", async () => {
  let failMedia = false;
  const dash = makeDashboard({
    fetchImpl: async (url, options) => {
      if (url === "/api/media" && failMedia) return jsonResponse({ error: "offline" }, false, 503);
      if (url === "/api/media") return jsonResponse(media());
      if (url === "/api/status") return jsonResponse(status());
      if (options.method === "POST") return jsonResponse({ accepted: true }, true, 202);
      throw new Error(url);
    },
  });
  await dash.tick(20);
  const option = dash.id("convert-source").options[0];
  failMedia = true;
  await dash.tick(30_100);
  assert.equal(dash.id("convert-source").options[0], option);
  assert.match(dash.id("source-status").textContent, /Verversen mislukt/);
  assert.equal(dash.id("refresh-sources").disabled, false);
  failMedia = false;
  dash.id("refresh-sources").click();
  await dash.tick(20);
  assert.match(dash.id("source-status").textContent, /2 bronnen beschikbaar/);
  dash.close();
});

test("empty media list disables conversion with a visible reason", async () => {
  const dash = makeDashboard({ mediaData: media({ sources: [] }) });
  await dash.tick(20);
  assert.equal(dash.id("convert-button").disabled, true);
  assert.match(dash.id("convert-reason").textContent, /Geen omzetbare bron beschikbaar/);
  assert.match(dash.id("source-status").textContent, /Geen omzetbare bronnen gevonden/);
  dash.close();
});

test("missing destination disables conversion with a visible reason", async () => {
  const dash = makeDashboard({
    mediaData: media({ destinations: { films: { path: "/films", ready: false }, music: { path: "/music", ready: true } } }),
  });
  await dash.tick(20);
  assert.equal(dash.id("convert-button").disabled, true);
  assert.match(dash.id("convert-reason").textContent, /doelmap is niet gekoppeld/);
  assert.match(dash.id("convert-path").textContent, /map niet gekoppeld/);
  dash.close();
});

test("conversion payload keeps the existing API contract", async () => {
  const dash = makeDashboard();
  await dash.tick(20);
  dash.id("convert-source").value = "nas-1";
  dash.id("convert-source").dispatchEvent(new dash.window.Event("change"));
  dash.id("convert-title").value = "2";
  dash.id("convert-title").dispatchEvent(new dash.window.Event("change"));
  dash.id("convert-mode").value = "audio";
  dash.id("convert-mode").dispatchEvent(new dash.window.Event("change"));
  dash.id("convert-name").value = "  Aflevering 2  ";
  dash.id("convert-button").click();
  await dash.tick(20);
  const post = dash.state.posts.find((item) => item.url === "/api/convert");
  assert.deepEqual(post.payload, {
    source_id: "nas-1",
    title: 2,
    mode: "audio",
    destination: "music",
    name: "Aflevering 2",
  });
  assert.equal(post.headers["X-DVD-Bridge"], "1");
  dash.close();
});

test("hidden page pauses polling and visible page refreshes once", async () => {
  const dash = makeDashboard();
  await dash.tick(20);
  const beforeStatus = dash.state.statusCalls;
  const beforeMedia = dash.state.mediaCalls;
  Object.defineProperty(dash.document, "visibilityState", { value: "hidden", configurable: true });
  dash.document.dispatchEvent(new dash.window.Event("visibilitychange"));
  await dash.tick(100);
  assert.equal(dash.state.statusCalls, beforeStatus);
  assert.equal(dash.state.mediaCalls, beforeMedia);
  Object.defineProperty(dash.document, "visibilityState", { value: "visible", configurable: true });
  dash.document.dispatchEvent(new dash.window.Event("visibilitychange"));
  await dash.tick(20);
  assert.equal(dash.state.statusCalls, beforeStatus + 1);
  assert.equal(dash.state.mediaCalls, beforeMedia + 1);
  dash.close();
});

test("disc change debounces media refresh and ignores stale scan", async () => {
  let releaseMedia;
  let mediaCalls = 0;
  let currentStatus = status();
  let currentMedia = media();
  const dash = makeDashboard({
    statusData: currentStatus,
    mediaData: currentMedia,
    fetchImpl: async (url) => {
      if (url === "/api/status") return jsonResponse(currentStatus);
      if (url === "/api/media") {
        mediaCalls += 1;
        if (mediaCalls === 2) {
          return new Promise((resolve) => {
            releaseMedia = () => resolve(jsonResponse(media({ sources: [source("nas-old", { label: "Oude scan" })] })));
          });
        }
        return jsonResponse(currentMedia);
      }
      throw new Error(url);
    },
  });
  await dash.tick(20);
  assert.equal(mediaCalls, 1);
  currentStatus = status({ disc: { identity: "disc-2", label: "Nieuw", filesystem: "udf", size_bytes: 1024 } });
  await dash.tick(4000);
  assert.equal(mediaCalls, 1, "media refresh is debounced");
  await dash.tick(400);
  assert.equal(mediaCalls, 2);
  currentStatus = status({ disc: { identity: "disc-3", label: "Nieuwer", filesystem: "udf", size_bytes: 1024 } });
  currentMedia = media({ sources: [source("nas-new", { label: "Nieuwe scan" })] });
  await dash.tick(4000);
  assert.equal(mediaCalls, 2, "disc change waits for the in-flight scan");
  releaseMedia();
  await dash.tick(250);
  assert.equal(mediaCalls, 3);
  assert.equal(dash.id("convert-source").options[0].textContent.includes("Nieuwe scan"), true);
  dash.close();
});

test("status failures back off and recover without rewriting the dashboard", async () => {
  let fail = true;
  const dash = makeDashboard({
    fetchImpl: async (url) => {
      if (url === "/api/status") {
        if (fail) throw new Error("network down");
        return jsonResponse(status());
      }
      if (url === "/api/media") return jsonResponse(media());
      throw new Error(url);
    },
  });
  await dash.tick(20);
  assert.equal(dash.id("connection-label").textContent, "Geen verbinding");
  const label = dash.id("connection-label").firstChild;
  fail = false;
  await dash.tick(2100);
  assert.equal(dash.id("connection-label").textContent, "Verbonden");
  assert.equal(dash.id("connection-label").firstChild, label);
  dash.close();
});

test("disc change blocks live-disc conversion until a fresh scan arrives", async () => {
  let currentStatus = status();
  let currentMedia = media();
  const dash = makeDashboard({
    fetchImpl: async (url) => {
      if (url === "/api/status") return jsonResponse(currentStatus);
      if (url === "/api/media") return jsonResponse(currentMedia);
      throw new Error(url);
    },
  });
  await dash.tick(20);
  assert.equal(dash.id("convert-button").disabled, false);
  currentStatus = status({ disc: { identity: "disc-2", label: "Andere dvd", filesystem: "udf", size_bytes: 2048 } });
  currentMedia = media({ sources: [source("disc", { label: "Andere dvd" }), source("nas-1")] });
  await dash.tick(4100);
  assert.equal(dash.id("convert-button").disabled, true);
  assert.match(dash.id("convert-reason").textContent, /Wacht tot de bronnen van de schijf/);
  await dash.tick(400);
  assert.equal(dash.id("convert-button").disabled, false);
  assert.equal(dash.id("convert-title").value, "0");
  dash.id("convert-source").value = "nas-1";
  dash.id("convert-source").dispatchEvent(new dash.window.Event("change"));
  currentStatus = status({ disc: { identity: "disc-3", label: "Nog een dvd", filesystem: "udf", size_bytes: 2048 } });
  await dash.tick(4100);
  assert.equal(dash.id("convert-button").disabled, false, "NAS conversion is not blocked by a disc change");
  dash.close();
});

test("boot media scan that finishes before status is reconciled", async () => {
  let statusCalls = 0;
  let mediaCalls = 0;
  let releaseStatus;
  const dash = makeDashboard({
    fetchImpl: async (url) => {
      if (url === "/api/media") {
        mediaCalls += 1;
        return jsonResponse(mediaCalls === 1 ? media({ sources: [] }) : media());
      }
      if (url === "/api/status") {
        statusCalls += 1;
        if (statusCalls === 1) {
          return new Promise((resolve) => { releaseStatus = () => resolve(jsonResponse(status())); });
        }
        return jsonResponse(status());
      }
      throw new Error(url);
    },
  });
  await dash.tick(20);
  assert.equal(mediaCalls, 1);
  assert.equal(statusCalls, 1);
  releaseStatus();
  await dash.tick(20);
  assert.equal(dash.id("convert-button").disabled, true);
  assert.match(dash.id("convert-reason").textContent, /Wacht tot de bronnen van de schijf/);
  await dash.tick(400);
  assert.equal(mediaCalls, 2, "a fresh scan covers the disc that mounted after the boot scan");
  assert.equal(dash.id("convert-source").value, "disc");
  assert.equal(dash.id("convert-button").disabled, false);
  dash.close();
});

test("invalid status JSON is not cached as the last good snapshot", async () => {
  let bad = true;
  const dash = makeDashboard({
    fetchImpl: async (url) => {
      if (url === "/api/status") {
        return bad ? { ok: true, status: 200, text: async () => "<html>proxied error</html>" } : jsonResponse(status());
      }
      if (url === "/api/media") return jsonResponse(media());
      throw new Error(url);
    },
  });
  await dash.tick(20);
  assert.equal(dash.id("connection-label").textContent, "Geen verbinding");
  bad = false;
  await dash.tick(2100);
  assert.equal(dash.id("connection-label").textContent, "Verbonden");
  assert.equal(dash.id("version").textContent, "v1.2.9");
  dash.close();
});

test("source fallback prefers a convertible source over an unsupported one", async () => {
  const dash = makeDashboard({
    mediaData: media({
      sources: [
        source("nas-leeg", { label: "Lege map", default_title: 0, titles: [], all_titles: null }),
        source("nas-film", { label: "NAS-film" }),
      ],
    }),
  });
  await dash.tick(20);
  assert.equal(dash.id("convert-source").value, "nas-film");
  assert.equal(dash.id("convert-button").disabled, false);
  dash.close();
});

test("speed slider keeps user input while a status poll arrives", async () => {
  const dash = makeDashboard();
  await dash.tick(20);
  const slider = dash.id("drive-speed");
  slider.value = "8";
  slider.dispatchEvent(new dash.window.Event("input"));
  dash.state.statusData = status({ drive_speed: 0, updated_at: 3 });
  await dash.tick(4100);
  assert.equal(slider.value, "8");
  assert.equal(dash.id("drive-speed-label").textContent, "8× aanvragen");
  slider.dispatchEvent(new dash.window.Event("change"));
  await dash.tick(20);
  const post = dash.state.posts.find((item) => item.payload.command === "speed");
  assert.equal(post.payload.speed, 8);
  dash.close();
});

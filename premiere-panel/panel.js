"use strict";
// SoundShredder panel for Premiere Pro (CEP). Talks to the SoundShredder app on this
// computer over 127.0.0.1 and to Premiere through host/soundshredder.jsx.

const http = require("http");
const fs = require("fs");
const path = require("path");
const { exec, execFile } = require("child_process");

const IS_WINDOWS = process.platform === "win32";
const FILE_BROWSER = IS_WINDOWS ? "Explorer" : "Finder";
const $ = (id) => document.getElementById(id);
const PORTS = Array.from({ length: 11 }, (_, i) => 7860 + i);
const STEMS = ["speech", "music", "effects"];
const LANES = [["Dialogue", "var(--speech)"], ["Music", "var(--music)"], ["SFX", "var(--effects)"]];
const TICKS = 254016000000;
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* not persisted */ } },
};

let server = null;        // {port, info}
const jobs = [];          // rows shown in the panel, newest first
let hostQueue = Promise.resolve();

// ---- Premiere (ExtendScript) ------------------------------------------------

function host(fn, args) {
  // Non-ASCII (for example Arabic file names) travels as \u escapes inside ExtendScript.
  const literal = args === undefined ? "" :
    JSON.stringify(JSON.stringify(args)).replace(/[\u007f-￿]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
  const run = () => new Promise((resolve, reject) => {
    window.__adobe_cep__.evalScript(`${fn}(${literal})`, (result) => {
      try { resolve(JSON.parse(result)); }
      catch (e) { reject(new Error(result === "EvalScript error." ? "Premiere could not run the SoundShredder script." : String(result))); }
    });
  });
  // One Premiere edit at a time, so each placement sees the tracks the previous one used.
  const next = hostQueue.then(run, run);
  hostQueue = next.catch(() => {});
  return next;
}

function applyHostTheme() {
  try {
    const skin = JSON.parse(window.__adobe_cep__.getHostEnvironment()).appSkinInfo;
    const c = skin.panelBackgroundColor.color;
    const shade = (d) => `rgb(${[c.red, c.green, c.blue].map((v) => Math.max(0, Math.min(255, Math.round(v + d)))).join(",")})`;
    document.documentElement.style.setProperty("--bg", shade(0));
    document.documentElement.style.setProperty("--raised", shade(9));
    document.documentElement.style.setProperty("--line", shade(23));
  } catch (e) { /* keep the default dark theme */ }
}

// ---- SoundShredder app ------------------------------------------------------

function call(method, path, body, port = server && server.port, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const request = http.request(
      { host: "127.0.0.1", port, path, method, timeout,
        headers: data ? { "Content-Type": "application/json", "Content-Length": data.length } : {} },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          let json = null;
          try { json = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch (e) { /* not JSON */ }
          if (response.statusCode >= 400) {
            reject(new Error(json && typeof json.detail === "string" ? json.detail : `SoundShredder answered ${response.statusCode}.`));
          } else resolve(json);
        });
      });
    request.on("timeout", () => request.destroy(new Error("SoundShredder did not answer.")));
    request.on("error", reject);
    if (data) request.write(data);
    request.end();
  });
}

async function connect() {
  let outdated = false;
  for (const port of PORTS) {
    try {
      const info = await call("GET", "/api/system", undefined, port, 1500);
      if (!info || info.name !== "SoundShredder") continue;
      if (!(info.features || []).includes("clips")) { outdated = true; continue; }
      server = { port, info };
      showConnection(true);
      return true;
    } catch (e) { /* try the next port */ }
  }
  server = null;
  showConnection(false, outdated);
  return false;
}

function showConnection(on, outdated = false) {
  const label = $("connection");
  label.className = "connection " + (on ? "on" : "off");
  if (on) {
    const device = server.info.gpu_available ? server.info.gpu_name : server.info.mps_available ? "Apple GPU" : "CPU";
    label.textContent = `Connected · ${device}`;
    $("device").querySelector('[value="mps"]').disabled = !server.info.mps_available;
    $("device").querySelector('[value="cuda"]').disabled = !server.info.gpu_available;
    if ($("device").selectedOptions[0].disabled) $("device").value = "auto";
  } else label.textContent = "Not connected";
  $("offline").hidden = on;
  $("offline-text").textContent = outdated
    ? "SoundShredder is running an older version. Quit it and open it again."
    : "SoundShredder isn't running.";
  $("separate").disabled = !on;
}

// The SoundShredder folder: the installed extension is a link to its premiere-panel folder.
function appFolder() {
  try {
    let here = decodeURIComponent(new URL(window.location.href).pathname);
    if (IS_WINDOWS) here = here.replace(/^\//, "");
    return path.dirname(fs.realpathSync(path.dirname(here)));
  } catch (e) {
    return null;
  }
}

function launchApp(done) {
  const root = appFolder();
  const launcher = (name) => (root && fs.existsSync(path.join(root, name)) ? path.join(root, name) : null);
  if (IS_WINDOWS) {
    if (!launcher("Start SoundShredder.bat")) return done(new Error("launcher not found"));
    exec('start "SoundShredder" "Start SoundShredder.bat"', { cwd: root }, done);
    return;
  }
  execFile("open", ["-a", "SoundShredder"], (error) => {
    // The Mac app is optional: fall back to the launcher in the SoundShredder folder.
    const command = launcher("Start SoundShredder.command");
    if (!error || !command) return done(error);
    execFile("open", [command], done);
  });
}

function showFolder(folder) {
  // Explorer exits with 1 even when it opens the folder, so its result is ignored.
  execFile(IS_WINDOWS ? "explorer" : "open", [folder], () => {});
}

$("launch").addEventListener("click", () => {
  $("launch").disabled = true;
  $("offline-text").textContent = "Opening SoundShredder…";
  launchApp((error) => {
    if (error) $("offline-text").textContent = "Could not open SoundShredder. Start it from its folder instead.";
  });
  let tries = 0;
  const retry = async () => {
    if (await connect() || ++tries > 40) { $("launch").disabled = false; return; }
    setTimeout(retry, 2000);
  };
  setTimeout(retry, 2000);
});

// ---- Separate clips on the timeline -----------------------------------------

function note(text, error = false) {
  $("note").hidden = !text;
  $("note").textContent = text || "";
  $("note").classList.toggle("error", error);
}

const basename = (path) => path.split("/").pop();

$("separate").addEventListener("click", async () => {
  note("");
  $("separate").disabled = true;
  try {
    if (!server && !(await connect())) return;
    const selection = await host("ss_getSelection");
    if (selection.error) { note(selection.error, true); return; }
    if (!selection.clips.length) {
      note(selection.skipped.length ? `Nothing to separate: ${selection.skipped.join(", ")}.` : "Select one or more clips in the timeline first.", true);
      return;
    }
    if (selection.skipped.length) note(`Skipped: ${selection.skipped.join(", ")}.`);
    const groups = new Map();
    for (const clip of selection.clips) {
      if (!groups.has(clip.path)) groups.set(clip.path, []);
      groups.get(clip.path).push(clip);
    }
    for (const [path, clips] of groups) {
      const row = {
        path, clips, name: basename(path), sequenceId: selection.sequenceId, original: $("original").value,
        status: "starting", progress: 0, detail: "Sending to SoundShredder…",
      };
      jobs.unshift(row);
      render();
      try {
        const response = await call("POST", "/api/clips", { path, device: $("device").value, cpu_fallback: true });
        row.id = response.id;
        row.detail = response.reused ? "Already separated. Placing the stems…" : "Queued.";
      } catch (error) {
        Object.assign(row, { status: "failed", detail: error.message });
      }
      render();
    }
    poll();
  } catch (error) {
    note(error.message, true);
  } finally {
    $("separate").disabled = !server;
  }
});

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    while (jobs.some((row) => row.id && ["starting", "queued", "running"].includes(row.status))) {
      for (const row of jobs.filter((r) => r.id && ["starting", "queued", "running"].includes(r.status))) {
        let job;
        try { job = await call("GET", `/api/jobs/${row.id}`); }
        catch (error) { row.detail = "Lost the connection to SoundShredder. Retrying…"; continue; }
        if (job.status === "queued") Object.assign(row, { status: "queued", detail: job.message });
        else if (job.status === "running") Object.assign(row, { status: "running", progress: job.progress, detail: job.message });
        else if (job.status === "complete") await place(row, job);
        else Object.assign(row, { status: "failed", detail: job.message || "The separation stopped." });
      }
      render();
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  } finally {
    polling = false;
    render();
  }
}

async function place(row, job) {
  const stems = job.report && job.report.stems_export;
  if (!stems) { Object.assign(row, { status: "failed", detail: "SoundShredder did not save stems for this clip." }); return; }
  Object.assign(row, { status: "placing", progress: 1, detail: "Placing the stems on the timeline…", folder: stems.folder });
  render();
  try {
    const result = await host("ss_placeStems", {
      sequenceId: row.sequenceId,
      files: STEMS.map((stem) => stems.files[stem]),
      offsetTicks: String(Math.round(stems.audio_offset_seconds * TICKS)),
      durationTicks: String(Math.round(stems.duration * TICKS)),
      clips: row.clips,
      original: row.original,
    });
    if (result.error) { Object.assign(row, { status: "failed", detail: result.error }); return; }
    const first = result.firstTrack, last = result.firstTrack + result.trackCount - 1;
    const parts = [`On A${first}–A${last}`];
    if (row.clips.length > 1) parts.push(`${row.clips.length} clips`);
    if (result.removed) parts.push("original replaced");
    if (result.linked) parts.push(result.linked === row.clips.length ? "linked to the clip" : `${result.linked} linked`);
    if (result.muted) parts.push("original muted");
    Object.assign(row, {
      status: result.placed ? "done" : "failed", firstTrack: first,
      detail: result.placed ? parts.join(" · ") + (result.warnings.length ? `. ${result.warnings.join(" ")}` : "")
        : result.warnings.join(" ") || "Nothing was placed.",
    });
  } catch (error) {
    Object.assign(row, { status: "failed", detail: error.message });
  }
}

const STATE_LABELS = { starting: "SENDING", queued: "QUEUED", running: "SEPARATING", placing: "PLACING", done: "DONE", failed: "FAILED" };

function render() {
  const list = $("jobs");
  list.replaceChildren(...jobs.slice(0, 30).map((row) => {
    const item = document.createElement("li");
    item.className = `job ${row.status}`;
    const top = document.createElement("div"); top.className = "job-top";
    const name = document.createElement("span"); name.className = "job-name"; name.textContent = row.name; name.title = row.path; name.dir = "ltr"; // like Premiere and Finder: the extension stays at the end
    const state = document.createElement("span"); state.className = "job-state";
    state.textContent = row.status === "running" ? `${STATE_LABELS.running} ${Math.round(row.progress * 100)}%` : STATE_LABELS[row.status];
    top.append(name, state);
    const detail = document.createElement("div"); detail.className = "job-detail"; detail.textContent = row.detail || "";
    item.append(top, detail);
    if (row.status === "running" || row.status === "queued") {
      const bar = document.createElement("progress"); bar.max = 1; bar.value = row.progress || 0; item.append(bar);
    }
    if (row.status === "done") {
      const lanes = document.createElement("div"); lanes.className = "lanes";
      LANES.forEach(([label, color], i) => {
        const lane = document.createElement("span"); lane.style.setProperty("--c", color);
        lane.textContent = `A${row.firstTrack + i} ${label}`; lanes.append(lane);
      });
      item.append(lanes);
    }
    if (row.folder) {
      const actions = document.createElement("div"); actions.className = "job-actions";
      const reveal = document.createElement("button"); reveal.className = "link"; reveal.type = "button";
      reveal.textContent = `Show stems in ${FILE_BROWSER}`;
      reveal.addEventListener("click", () => showFolder(row.folder));
      actions.append(reveal); item.append(actions);
    }
    return item;
  }));
}

// ---- Watch folder: bring finished multitrack videos into the open project ----

$("watch-import").checked = store.get("watchImport", false);
$("watch-import").addEventListener("change", () => { store.set("watchImport", $("watch-import").checked); syncWatch(); });
const MODE_HINTS = {
  replace: "Select clips in the timeline first. Dialogue, Music and SFX replace each clip's audio and are linked to it: one clip with three sound tracks.",
  mute: "Select clips in the timeline first. Dialogue, Music and SFX land on three free tracks under each clip; the original audio is muted.",
  keep: "Select clips in the timeline first. Dialogue, Music and SFX land on three free tracks under each clip; the original audio stays as it is.",
};
function showModeHint() { $("mode-hint").textContent = MODE_HINTS[$("original").value]; }
$("original").value = store.get("originalAudio", "replace");
if (!$("original").value) $("original").value = "replace";
showModeHint();
$("original").addEventListener("change", () => { store.set("originalAudio", $("original").value); showModeHint(); });
$("device").value = store.get("device", "auto");
$("device").addEventListener("change", () => store.set("device", $("device").value));

const imported = new Set();  // "<project>|<file>" already in a project
let syncing = false;
async function syncWatch() {
  if (syncing || !server) return;
  syncing = true;
  try {
    const watch = await call("GET", "/api/watch");
    const ready = watch.items.filter((item) => item.status === "done" && item.output).map((item) => item.output);
    $("watch-info").textContent = !watch.folder ? "Set up a watch folder in the SoundShredder app first."
      : watch.enabled ? `Watching ${basename(watch.folder)} · ${ready.length} video${ready.length === 1 ? "" : "s"} ready.`
      : `Watching is paused in SoundShredder (${basename(watch.folder)}).`;
    if (!$("watch-import").checked || !ready.length) return;
    const project = (await host("ss_projectPath")).path;
    if (!project) return;
    const missing = ready.filter((path) => !imported.has(`${project}|${path}`));
    if (!missing.length) return;
    const result = await host("ss_importFiles", { paths: missing, bin: "SoundShredder" });
    if (result.error) { $("watch-info").textContent = result.error; return; }
    result.present.forEach((path) => imported.add(`${result.project}|${path}`));
    if (result.imported.length) {
      $("watch-info").textContent = `Imported ${result.imported.map(basename).join(", ")} into the SoundShredder bin.`;
    }
  } catch (error) {
    /* SoundShredder closed: the connection check below reports it */
  } finally {
    syncing = false;
  }
}

async function heartbeat() {
  if (!polling) await connect();
  await syncWatch();
  setTimeout(heartbeat, 4000);
}

applyHostTheme();
render();
heartbeat();

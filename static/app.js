"use strict";
const $ = (id) => document.getElementById(id);
const stems = ["speech", "music", "effects"];
const presets = {
  "no-music": [1, 0, 1], dialogue: [1, 0, 0], effects: [0, 0, 1],
  "no-effects": [1, 1, 0], "no-dialogue": [0, 1, 1],
};
let selectedFile = null, currentJob = null, jobData = null, busy = false;
let sourceURL = null, pollTimer = null, startedAt = null, lastMix = null;
let sourcePeaks = null, mixPeaks = null;

// Decorative signal motifs. These are illustrations, not measurements of a file.
document.querySelectorAll(".track-wave").forEach((element, index) => {
  for (let i = 0; i < 42; i++) {
    const bar = document.createElement("i");
    const envelope = Math.sin(Math.PI * (i + 1) / 43);
    const detail = Math.abs(Math.sin(i * (index + 1) * 0.7 + index * 2));
    bar.style.setProperty("--h", `${4 + envelope * (9 + detail * 22)}px`);
    bar.style.setProperty("--opacity", (0.24 + envelope * 0.57).toFixed(2));
    element.append(bar);
  }
});
$("hero-signal").setAttribute("d", Array.from({length: 47}, (_, i) => {
  const envelope = Math.exp(-Math.pow((i - 23) / 12, 2));
  const detail = .2 + .8 * Math.abs(Math.sin(i * .83) * Math.cos(i * .17));
  const height = 3 + 172 * envelope * detail;
  return `M${43 + i * 9} ${(125 - height / 2).toFixed(1)}v${height.toFixed(1)}`;
}).join(" "));

function showError(message) { $("error").textContent = message; $("error").hidden = false; }
function clearError() { $("error").hidden = true; }
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Please check the selected settings and try again.");
  return data;
}
function gains() { return Object.fromEntries(stems.map(s => [s, Number($("level-" + s).value) / 100])); }
function sameGains(a, b) { return a && b && stems.every(s => Math.abs(a[s] - b[s]) < 0.001); }
function formatTime(value) { const seconds = Math.floor(value); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; }
function fileURL(name) { return `/api/jobs/${currentJob}/files/${encodeURIComponent(name)}`; }
function setBusy(value) {
  busy = value;
  document.body.classList.toggle("busy", value);
  $("separate").disabled = value || !selectedFile;
  for (const id of ["file-input", "change-file", "device", "cpu-fallback", "new-session"])
    $(id).disabled = value;
  document.querySelectorAll(".level,.toggle input,.preset").forEach(el => { el.disabled = value; });
  $("action-title").textContent = value ? "Finding the sound beneath the noise." :
    jobData?.status === "complete" ? "Your sound is ready for its next scene." :
    selectedFile ? "Your file is in. Let’s find the good stuff." : "A little less noise. A lot more you.";
}
function updateControls() {
  const values = gains();
  stems.forEach(s => {
    const on = values[s] > 0;
    $("keep-" + s).checked = on;
    $("value-" + s).value = `${Math.round(values[s] * 100)}%`;
    const card = document.querySelector(`[data-stem="${s}"]`);
    card.classList.toggle("removed", !on);
    card.querySelector(".track-state").textContent = !on ? "REMOVE" : values[s] < 1 ? "REDUCED" : "KEEP";
    card.querySelector(".level-label label").textContent = !on ? "REMOVE FROM MIX" : values[s] < 1 ? "REDUCE IN MIX" : "KEEP IN MIX";
    $("level-" + s).style.setProperty("--level", `${values[s] * 100}%`);
  });
  document.querySelectorAll(".preset").forEach(button => {
    const match = presets[button.dataset.preset].every((v, i) => v === values[stems[i]]);
    button.classList.toggle("active", match); button.setAttribute("aria-pressed", match);
  });
  const kept = stems.filter(s => values[s] > 0).map(s => s === "speech" ? "dialogue" : s === "effects" ? "effects" : "music");
  const description = kept.length ? `Keep ${kept.join(" + ")}. Adjust each layer to taste.` : "All layers removed. Your cleaned mix will be silent.";
  $("mix-description").textContent = description;
  if (lastMix && jobData?.status === "complete") {
    const dirty = !sameGains(values, lastMix.gains);
    $("render-mix").hidden = !dirty;
    for (const id of ["download-mix", "download-bundle", "download-report"]) $(id).hidden = dirty;
    $("result-notice").hidden = !dirty && !(jobData.report?.warnings?.length);
    $("result-notice").textContent = dirty ? "Levels changed. Update the mix to hear and download these settings. The preview below is the previous mix." : (jobData.report?.warnings || []).join(" ");
  }
}
function setGains(values) { stems.forEach((s, i) => { $("level-" + s).value = Math.round((Array.isArray(values) ? values[i] : values[s]) * 100); }); updateControls(); }

function drawWave(canvas, peaks, color) {
  const rect = canvas.getBoundingClientRect(); if (!rect.width) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr); canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height;
  ctx.strokeStyle = "#304856"; ctx.beginPath(); ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2); ctx.stroke();
  if (!peaks) return;
  const peak = Math.max(...peaks, 0.02);
  const step = width / peaks.length;
  ctx.fillStyle = color;
  peaks.forEach((v, i) => { const h = Math.max(2, v / peak * height * 0.83); ctx.fillRect(i * step, (height - h) / 2, Math.max(1, step - 1.5), h); });
}
function redraw() { drawWave($("source-wave"), sourcePeaks, "#78ceca"); drawWave($("result-wave"), mixPeaks, "#79f6d3"); }
window.addEventListener("resize", redraw);

function reset() {
  if (busy) return;
  clearTimeout(pollTimer); currentJob = null; jobData = null; lastMix = null; selectedFile = null;
  if (sourceURL) URL.revokeObjectURL(sourceURL); sourceURL = null;
  $("file-input").value = ""; $("dropzone").hidden = false; $("source-selected").hidden = true;
  $("results").hidden = true; $("progress-panel").hidden = true;
  $("action-help").textContent = "Add your file to start separating.";
  document.querySelectorAll("audio").forEach(a => { a.pause(); a.removeAttribute("src"); a.load(); });
  document.querySelectorAll(".stem-preview").forEach(el => { el.hidden = true; });
  $("download-video").hidden = true; $("reveal-video").hidden = true;
  clearError(); setGains(presets["no-music"]); setBusy(false);
  $("separate").innerHTML = "Separate audio <span>↗</span>";
}
function selectFile(file) {
  if (!file || busy) return;
  if (file.size > 500 * 1024 * 1024) { showError("Choose a file smaller than 500 MB."); return; }
  if (!file.size) { showError("That file is empty. Choose an audio file with some sound."); return; }
  reset(); selectedFile = file;
  $("dropzone").hidden = true; $("source-selected").hidden = false;
  $("file-name").textContent = file.name;
  $("file-meta").textContent = `${(file.size / 1048576).toFixed(1)} MB · ready to separate`;
  sourceURL = URL.createObjectURL(file); $("original-audio").src = sourceURL;
  sourcePeaks = null; $("wave-label").hidden = false;
  requestAnimationFrame(redraw); setBusy(false);
  $("action-help").textContent = "Choose your layers, then separate. Your original stays untouched.";
}
$("file-input").addEventListener("change", e => selectFile(e.target.files[0]));
$("change-file").addEventListener("click", () => $("file-input").click());
$("new-session").addEventListener("click", reset);
for (const type of ["dragenter", "dragover"]) $("dropzone").addEventListener(type, e => { e.preventDefault(); if (!busy) $("dropzone").classList.add("drag-over"); });
for (const type of ["dragleave", "drop"]) $("dropzone").addEventListener(type, e => { e.preventDefault(); $("dropzone").classList.remove("drag-over"); });
$("dropzone").addEventListener("drop", e => { if (e.dataTransfer.files.length !== 1) showError("Drop one file at a time."); else selectFile(e.dataTransfer.files[0]); });
stems.forEach(s => {
  $("level-" + s).addEventListener("input", updateControls);
  $("keep-" + s).addEventListener("change", () => { $("level-" + s).value = $("keep-" + s).checked ? 100 : 0; updateControls(); });
});
document.querySelectorAll(".preset").forEach(button => button.addEventListener("click", () => setGains(presets[button.dataset.preset])));

let hardware = {cuda: false, mps: false};
const deviceNames = {cuda: "GPU", mps: "APPLE GPU", cpu: "CPU"};
function hardwareHelp() {
  const device = $("device").value;
  if (device === "cpu") return "CPU selected. This can take several minutes per clip.";
  if (device === "mps") return "Apple GPU selected. Metal is usually much faster than CPU.";
  if (device === "cuda") return "NVIDIA GPU selected.";
  if (hardware.cuda) return "GPU detected. Auto will use NVIDIA acceleration.";
  if (hardware.mps) return "Auto will use the Apple GPU (Metal). CPU stays available.";
  return "Choose CPU or Auto. No compatible GPU detected.";
}
$("device").addEventListener("change", () => { $("hardware-help").textContent = hardwareHelp(); });

function applyMix(mix) {
  lastMix = mix; mixPeaks = mix.waveform;
  $("mix-audio").src = fileURL(mix.mix);
  $("download-mix").href = fileURL(mix.mix);
  $("download-bundle").href = fileURL(mix.bundle);
  $("download-report").href = fileURL(mix.report);
  updateControls(); requestAnimationFrame(redraw);
}
function showResult(data) {
  $("progress-panel").hidden = true; $("results").hidden = false;
  $("result-device").textContent = `${deviceNames[data.report.device] || "CPU"} · COMPLETE`;
  $("result-meta").textContent = `24-bit WAV · ${data.report.sample_rate / 1000} kHz · ${data.report.channels === 2 ? "Stereo" : "Mono"} · ${formatTime(data.report.duration)} · Timing verified`;
  document.querySelectorAll(".stem-preview").forEach(el => { el.hidden = false; });
  stems.forEach(s => { $("preview-" + s).src = fileURL(s + ".wav"); $("download-" + s).href = fileURL(s + ".wav"); });
  const video = data.report.video;
  $("download-video").hidden = !video; $("reveal-video").hidden = !video;
  if (video) { $("download-video").href = `/api/jobs/${currentJob}/video`; $("download-video").title = video.name; }
  applyMix(data.mix);
  $("action-help").textContent = "Your tracks are ready. Adjust the levels above, then update your mix.";
  $("separate").innerHTML = "Separate again <span>↗</span>";
}
function updateSource(data) {
  if (!data.source) return;
  sourcePeaks = data.source.waveform;
  $("file-name").textContent = data.filename;
  $("file-meta").textContent = `${formatTime(data.source.duration)} · ${data.source.sample_rate / 1000} kHz · ${data.source.channels === 2 ? "Stereo" : "Mono"}`;
  $("dropzone").hidden = true; $("source-selected").hidden = false; $("wave-label").hidden = true;
  const target = fileURL("original.wav");
  if (!$("original-audio").getAttribute("src") || !selectedFile) $("original-audio").src = target;
  requestAnimationFrame(redraw);
}
async function poll(id) {
  if (id !== currentJob) return;
  try {
    const data = await api(`/api/jobs/${id}`); if (id !== currentJob) return;
    jobData = data; updateSource(data);
    $("progress-message").textContent = data.message;
    $("progress").value = data.progress;
    $("progress-percent").textContent = `${Math.round(data.progress * 100)}%`;
    $("elapsed").textContent = startedAt ? `${formatTime((Date.now() - startedAt) / 1000)} elapsed` : "Session restored";
    if (data.status === "running" || data.status === "queued") { pollTimer = setTimeout(() => poll(id), 1400); return; }
    setBusy(false); $("cancel").disabled = false;
    if (data.status === "complete") showResult(data);
    else { $("progress-panel").hidden = true; showError(data.message); }
    await refreshHistory();
  } catch (error) {
    if (id !== currentJob) return;
    $("progress-message").textContent = "Connection interrupted. Reconnecting… Keep the app window open.";
    pollTimer = setTimeout(() => poll(id), 3500);
  }
}
$("separate").addEventListener("click", async () => {
  if (!selectedFile || busy) return;
  clearError(); setBusy(true); startedAt = Date.now();
  $("results").hidden = true; $("progress-panel").hidden = false;
  $("progress-message").textContent = "Uploading to your local workspace…"; $("progress").value = 0;
  $("cancel").disabled = true;
  const form = new FormData(); form.append("file", selectedFile); form.append("device", $("device").value);
  form.append("cpu_fallback", $("cpu-fallback").checked);
  Object.entries(gains()).forEach(([key, value]) => form.append(key, value));
  try {
    const data = await api("/api/jobs", {method: "POST", body: form});
    currentJob = data.id; lastMix = null; $("cancel").disabled = false;
    document.querySelectorAll(".stem-preview").forEach(el => { el.hidden = true; });
    await poll(currentJob); await refreshHistory();
  } catch (error) { showError(error.message); $("progress-panel").hidden = true; setBusy(false); }
});
$("cancel").addEventListener("click", async () => {
  if (!currentJob) return;
  $("cancel").disabled = true;
  try { await api(`/api/jobs/${currentJob}/cancel`, {method:"POST"}); clearTimeout(pollTimer); await poll(currentJob); }
  catch (error) { showError(error.message); $("cancel").disabled = false; }
});
$("render-mix").addEventListener("click", async () => {
  if (!currentJob) return;
  const id = currentJob; $("render-mix").disabled = true; clearError();
  try {
    const mix = await api(`/api/jobs/${id}/mix`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(gains())});
    if (currentJob === id) { jobData.mix = mix; applyMix(mix); }
  } catch (error) { showError(error.message); }
  finally { $("render-mix").disabled = false; }
});
async function openSession(id) {
  if (busy && id !== currentJob) { showError("Finish or cancel the current separation before opening another session."); return; }
  if (busy) return;
  reset(); currentJob = id;
  try {
    const data = await api(`/api/jobs/${id}`); jobData = data;
    setGains(data.mix?.gains || data.settings?.gains || presets["no-music"]);
    if (data.settings) { $("device").value = data.settings.device; $("cpu-fallback").checked = data.settings.cpu_fallback; }
    updateSource(data);
    if (data.status === "complete") showResult(data);
    else if (data.status === "running" || data.status === "queued") { setBusy(true); $("progress-panel").hidden = false; await poll(id); }
    else showError(data.message);
  } catch (error) { showError(error.message); }
}
async function refreshHistory() {
  try {
    const history = await api("/api/jobs"); $("history").replaceChildren();
    if (!history.length) { const p = document.createElement("p"); p.className="muted small"; p.textContent="Your sessions will appear here."; $("history").append(p); }
    history.forEach(item => {
      const button=document.createElement("button"); button.className="history-item";
      const title=document.createElement("strong"); title.textContent=item.filename; title.dir="auto";
      const status=document.createElement("small"); status.textContent=({watch: "AUTO · ", premiere: "PREMIERE · "}[item.origin] || "") + (item.status === "complete" ? "✓ Ready to mix" : item.status);
      button.append(title,status); button.addEventListener("click",()=>openSession(item.id));
      const row = document.createElement("div"); row.className = "history-row";
      const remove = document.createElement("button"); remove.className = "history-delete";
      remove.textContent = "×"; remove.setAttribute("aria-label", `Delete session ${item.filename}`);
      remove.disabled = item.status === "running";
      remove.addEventListener("click", async () => {
        if (!confirm(`Delete the local session for ${item.filename}, including generated files? The original stays unchanged.`)) return;
        try { await api(`/api/jobs/${item.id}`, {method:"DELETE"}); if (currentJob === item.id) reset(); await refreshHistory(); }
        catch (error) { showError(error.message); }
      });
      row.append(button, remove); $("history").append(row);
    });
  } catch (error) { showError("Could not load sessions. Make sure SoundShredder is running."); }
}
$("refresh-history").addEventListener("click", refreshHistory);
$("delete-session").addEventListener("click", async () => {
  if (!currentJob || !confirm("Delete this local session, its uploaded copy, and all generated tracks? Your original file stays unchanged.")) return;
  try { await api(`/api/jobs/${currentJob}`, {method:"DELETE"}); reset(); await refreshHistory(); }
  catch (error) { showError(error.message); }
});
// Keep A/B listening intelligible: starting one preview pauses all the others.
document.querySelectorAll("audio").forEach(a => a.addEventListener("play", () => document.querySelectorAll("audio").forEach(other => { if (a !== other) other.pause(); })));
async function boot() {
  try {
    const info = await api("/api/system");
    hardware = {cuda: !!info.gpu_available, mps: !!info.mps_available};
    $("hardware-name").textContent = info.gpu_name || (hardware.mps ? "Apple GPU (Metal) available" : "CPU processing available");
    $("device").querySelector('[value="cuda"]').disabled = !hardware.cuda;
    $("device").querySelector('[value="mps"]').disabled = !hardware.mps;
    $("hardware-help").textContent = hardwareHelp();
    await refreshHistory();
    // A video from the watched folder is followed in the Watch panel, not in the workspace.
    if (info.active_jobs.length && (await api(`/api/jobs/${info.active_jobs[0]}`)).origin !== "watch")
      await openSession(info.active_jobs[0]);
  } catch (error) { showError("Could not connect to SoundShredder. Keep the launcher window open and refresh this page."); }
}

$("reveal-video").addEventListener("click", async () => {
  if (!currentJob) return;
  try { await api(`/api/jobs/${currentJob}/reveal`, {method: "POST"}); } catch (error) { showError(error.message); }
});

// Watch a folder: every new video becomes a multitrack .mov for editing.
let watchState = null, watchEdited = false, watchStatuses = {};
const DEFAULT_OUTPUT = "SoundShredder Stems";
const watchLabels = {waiting: "COPYING", queued: "QUEUED", running: "SEPARATING", done: "READY", failed: "FAILED", cancelled: "CANCELLED"};
const postJSON = (path, body) => api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
function watchMessage(text, error = false) { $("watch-status").textContent = text; $("watch-status").classList.toggle("error", error); }
function defaultOutput(folder) { return folder ? `${folder.replace(/\/+$/, "")}/${DEFAULT_OUTPUT}` : ""; }
function watchButton(label, handler) {
  const button = document.createElement("button"); button.type = "button"; button.className = "text-button";
  button.textContent = label; button.addEventListener("click", handler); return button;
}
function watchItem(item) {
  const row = document.createElement("li"); row.className = `watch-item ${item.status}`;
  const text = document.createElement("div");
  const name = document.createElement("strong"); name.textContent = item.name; name.title = item.path; name.dir = "auto";
  const detail = document.createElement("p");
  detail.textContent = item.status === "done" && item.output ? `Saved ${item.output.split("/").pop()}` : item.message || "";
  text.append(name, detail);
  const actions = document.createElement("div"); actions.className = "watch-item-actions";
  if (item.status === "done" && item.job) {
    actions.append(watchButton("Show in Finder", () => api(`/api/jobs/${item.job}/reveal`, {method: "POST"}).catch(e => watchMessage(e.message, true))));
    actions.append(watchButton("Listen", () => { openSession(item.job); $("main").scrollIntoView({behavior: "smooth"}); }));
  }
  if (item.status === "failed" || item.status === "cancelled")
    actions.append(watchButton("Retry", () => postJSON("/api/watch/retry", {name: item.name}).then(renderWatch).catch(e => watchMessage(e.message, true))));
  const state = document.createElement("span"); state.className = `watch-state ${item.status}`;
  state.textContent = item.status === "running" ? `${watchLabels.running} ${Math.round((item.progress || 0) * 100)}%` : watchLabels[item.status] || item.status.toUpperCase();
  actions.append(state);
  row.append(text, actions);
  if (item.status === "running") { const bar = document.createElement("progress"); bar.max = 1; bar.value = item.progress || 0; row.append(bar); }
  return row;
}
function renderWatch(state) {
  watchState = state;
  if (!watchEdited) { $("watch-folder").value = state.folder || ""; $("watch-output").value = state.output || ""; }
  const watching = state.enabled && state.folder_found;
  const badge = !state.enabled ? (state.folder ? "PAUSED" : "OFF") : watching ? "WATCHING" : "FOLDER MISSING";
  $("watch-badge").textContent = badge;
  $("watch-badge").className = `watch-badge ${watching ? "on" : state.enabled || state.folder ? "paused" : ""}`;
  $("watch-nav").classList.toggle("on", watching); $("watch-nav-state").textContent = watching ? "ON" : "OFF";
  $("watch-start").hidden = state.enabled; $("watch-stop").hidden = !state.enabled;
  $("pick-folder").hidden = $("pick-output").hidden = !state.can_pick;
  $("open-watch-folder").hidden = !state.folder; $("open-output-folder").hidden = !state.output;
  if (!state.folder) watchMessage("Choose a folder, then start watching.");
  else if (state.enabled && !state.folder_found) watchMessage("The watched folder can't be found. Connect its drive or choose another folder.", true);
  else if (state.enabled) {
    const skipped = state.skipped_existing ? ` ${state.skipped_existing} video${state.skipped_existing === 1 ? " that was" : "s that were"} already there ${state.skipped_existing === 1 ? "was" : "were"} left alone.` : "";
    watchMessage(`Watching for new videos. Keep SoundShredder open; videos are separated one at a time.${skipped}`);
  } else watchMessage("Paused. Videos added meanwhile are separated when you start again.");
  const queue = $("watch-queue"); queue.replaceChildren();
  if (!state.items.length) {
    const empty = document.createElement("li"); empty.className = "watch-empty";
    empty.textContent = watching ? "Waiting for a new video in the folder…" : "Start watching, then add videos to the folder. Each one appears here.";
    queue.append(empty);
  }
  state.items.forEach(item => queue.append(watchItem(item)));
  const finished = state.items.some(item => watchStatuses[item.name] && watchStatuses[item.name] !== item.status && ["done", "failed"].includes(item.status));
  watchStatuses = Object.fromEntries(state.items.map(item => [item.name, item.status]));
  if (finished) refreshHistory();
}
async function loadWatch() {
  try { renderWatch(await api("/api/watch")); } catch (error) { /* the next poll retries */ }
  setTimeout(loadWatch, document.hidden ? 6000 : 2000);
}
for (const id of ["watch-folder", "watch-output"]) $(id).addEventListener("input", () => { watchEdited = true; });
async function pickFolder(target) {
  const input = target === "folder" ? $("watch-folder") : $("watch-output");
  const previous = $("watch-folder").value.trim();
  watchMessage("Choose a folder in the window that just opened…");
  try {
    const {path} = await postJSON("/api/watch/pick", {folder: target});
    if (!path) { renderWatch(watchState); return; }
    input.value = path; watchEdited = true;
    // Keep the default output following the watched folder unless the user picked one.
    if (target === "folder" && (!$("watch-output").value.trim() || $("watch-output").value.trim() === defaultOutput(previous)))
      $("watch-output").value = defaultOutput(path);
    watchMessage(target === "folder" ? "Folder chosen. Click Start watching." : "Output folder chosen.");
  } catch (error) { watchMessage(error.message, true); }
}
$("pick-folder").addEventListener("click", () => pickFolder("folder"));
$("pick-output").addEventListener("click", () => pickFolder("output"));
$("watch-start").addEventListener("click", async () => {
  const folder = $("watch-folder").value.trim();
  if (!folder) { watchMessage("Choose a folder to watch first.", true); $("watch-folder").focus(); return; }
  let output = $("watch-output").value.trim() || null;
  if (watchState?.folder && folder !== watchState.folder && output === defaultOutput(watchState.folder)) output = null;
  $("watch-start").disabled = true;
  try {
    renderWatch(await postJSON("/api/watch", {
      folder, output, enabled: true, device: $("device").value,
      cpu_fallback: $("cpu-fallback").checked, include_existing: $("watch-existing").checked,
    }));
    watchEdited = false; renderWatch(watchState); $("watch-existing").checked = false;
  } catch (error) { watchMessage(error.message, true); }
  finally { $("watch-start").disabled = false; }
});
$("watch-stop").addEventListener("click", async () => {
  try { renderWatch(await postJSON("/api/watch/enabled", {enabled: false})); } catch (error) { watchMessage(error.message, true); }
});
$("open-watch-folder").addEventListener("click", () => postJSON("/api/watch/reveal", {folder: "folder"}).catch(e => watchMessage(e.message, true)));
$("open-output-folder").addEventListener("click", () => postJSON("/api/watch/reveal", {folder: "output"}).catch(e => watchMessage(e.message, true)));
$("watch-nav").addEventListener("click", () => $("watch-section").scrollIntoView({behavior: "smooth"}));

updateControls();
boot();
loadWatch();

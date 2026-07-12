const CLIP_COLORS = [
  "#4fc3f7", "#ffca28", "#66bb6a", "#ba68c8", "#ff8a65", "#4db6ac", "#f06292", "#9ccc65",
];

const state = {
  curves: [],
  selectedCurve: null,
  audioFiles: [],
  videos: [],           // [{name, duration, fps, url}, ...] from /api/clips
  clipOrder: [],         // filenames, in the user's drag order
  beatAssignment: {},    // filename -> beat seconds (number) | undefined = unassigned
  beats: [],             // detected beat timestamps for the loaded audio file
  audioPeaks: [],
  audioDuration: 0,
  planChunks: [],         // last successful /api/plan response
  planTimer: null,
  polling: null,
  dragName: null,
};

const el = (id) => document.getElementById(id);

function clipColor(name) {
  const idx = state.clipOrder.indexOf(name);
  return CLIP_COLORS[(idx < 0 ? 0 : idx) % CLIP_COLORS.length];
}

function fmtBeat(t) {
  return `${fmtDuration(t)}`;
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${path} failed (${res.status})`);
  }
  return res.json();
}

function fmtDuration(seconds) {
  if (seconds == null) return "–";
  const m = Math.floor(seconds / 60);
  const s = (seconds % 60).toFixed(1);
  return `${m}:${s.padStart(4, "0")}`;
}

// ---- Config ----
async function loadConfig() {
  const cfg = await api("/api/config");
  el("encoder-badge").textContent = cfg.encoder;
  el("stat-hw").textContent = cfg.encoder;

  const rows = [
    ["Resolution", `${cfg.resolution[0]}×${cfg.resolution[1]}`],
    ["Target FPS", cfg.fps],
    ["Raw Clips", cfg.raw_clips_dir],
    ["Output", cfg.output_dir],
    ["Default Curve", cfg.selected_curve],
  ];
  const tbody = el("config-table").querySelector("tbody");
  tbody.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");

  state.selectedCurve = cfg.selected_curve;
}

// ---- Clips ----
async function loadClips() {
  const data = await api("/api/clips");
  state.audioFiles = data.audio;
  state.videos = data.videos;
  state.clipOrder = data.videos.map((v) => v.name);

  const orderList = el("clip-order-list");
  const audioRow = el("audio-track-row");

  if (data.videos.length === 0 && data.audio.length === 0) {
    orderList.innerHTML = `<div class="empty">No files in raw_clips/</div>`;
  } else if (data.videos.length === 0) {
    orderList.innerHTML = `<div class="empty">No video clips in raw_clips/</div>`;
  } else {
    renderClipOrderList();
  }

  if (data.audio.length > 0) {
    audioRow.innerHTML = data.audio.map((a) => `
      <div class="clip-row" data-audio="${a.name}">
        <div><div class="clip-name">${a.name}</div><div class="clip-meta">audio track</div></div>
        <span class="clip-tag audio">audio</span>
      </div>`).join("");
    audioRow.querySelectorAll(".clip-row").forEach((row) => {
      row.addEventListener("click", () => loadWaveform(row.dataset.audio));
    });
  } else {
    audioRow.innerHTML = `<div class="empty">No audio track found</div>`;
  }

  el("stat-clips").textContent = data.videos.length;

  if (data.audio.length > 0) {
    loadWaveform(data.audio[0].name);
  } else {
    el("timeline-note").textContent = "No audio track found";
  }
}

function selectVideo(url, name) {
  const video = el("preview-video");
  video.src = url;
  el("preview-note").textContent = name;
}

// ---- Clip order + beat assignment ----
function renderClipOrderList() {
  const list = el("clip-order-list");
  const byName = Object.fromEntries(state.videos.map((v) => [v.name, v]));

  list.innerHTML = state.clipOrder.map((name) => {
    const v = byName[name] || {};
    const meta = v.error ? "probe failed" : `${fmtDuration(v.duration)} · ${v.fps} fps`;
    const beat = state.beatAssignment[name];
    const options = [`<option value="">— skip (unassigned) —</option>`]
      .concat(state.beats.map((t) => `<option value="${t}" ${beat === t ? "selected" : ""}>@ ${fmtBeat(t)}</option>`))
      .join("");
    return `
      <div class="clip-order-row" draggable="true" data-name="${name}">
        <span class="drag-handle">⋮⋮</span>
        <span class="clip-color-dot" style="background:${clipColor(name)}"></span>
        <div class="clip-order-info">
          <div class="clip-name">${name}</div>
          <div class="clip-meta">${meta}</div>
        </div>
        <select class="beat-select" data-name="${name}">${options}</select>
      </div>`;
  }).join("");

  list.querySelectorAll(".clip-order-row").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.tagName === "SELECT") return;
      const v = byName[row.dataset.name];
      if (v && v.url) selectVideo(v.url, v.name);
    });
    row.addEventListener("dragstart", () => {
      state.dragName = row.dataset.name;
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => row.classList.remove("dragging"));
    row.addEventListener("dragover", (e) => {
      e.preventDefault();
      row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      row.classList.remove("drag-over");
      reorderClip(state.dragName, row.dataset.name);
    });
  });

  list.querySelectorAll(".beat-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const name = sel.dataset.name;
      if (sel.value === "") delete state.beatAssignment[name];
      else state.beatAssignment[name] = parseFloat(sel.value);
      renderClipOrderList();
      scheduleRefreshPlan();
    });
  });
}

function reorderClip(draggedName, targetName) {
  if (!draggedName || draggedName === targetName) return;
  const order = state.clipOrder.filter((n) => n !== draggedName);
  const targetIdx = order.indexOf(targetName);
  order.splice(targetIdx, 0, draggedName);
  state.clipOrder = order;
  renderClipOrderList();
  scheduleRefreshPlan();
}

function autoAssignBeats() {
  const used = new Set(Object.values(state.beatAssignment));
  const unassignedClips = state.clipOrder.filter((n) => state.beatAssignment[n] === undefined);
  const availableBeats = state.beats.filter((t) => !used.has(t)).sort((a, b) => a - b);
  unassignedClips.forEach((name, i) => {
    if (availableBeats[i] !== undefined) state.beatAssignment[name] = availableBeats[i];
  });
}

// ---- Waveform + Timeline ----
async function loadWaveform(filename) {
  el("timeline-note").textContent = `Analyzing ${filename}…`;
  try {
    const data = await api(`/api/waveform?file=${encodeURIComponent(filename)}`);
    state.beats = data.beats;
    state.audioPeaks = data.peaks;
    state.audioDuration = data.duration;
    state.beatAssignment = {};
    autoAssignBeats();
    el("stat-beats").textContent = data.beats.length;
    renderClipOrderList();
    await refreshPlan();
  } catch (err) {
    el("timeline-note").textContent = `Failed to analyze ${filename}: ${err.message}`;
  }
}

function scheduleRefreshPlan() {
  if (state.planTimer) clearTimeout(state.planTimer);
  state.planTimer = setTimeout(refreshPlan, 300);
}

async function refreshPlan() {
  const assignments = Object.entries(state.beatAssignment)
    .map(([clip, beat]) => ({ clip, beat }))
    .sort((a, b) => a.beat - b.beat);

  el("stat-assigned").textContent = `${assignments.length}/${state.beats.length}`;

  if (assignments.length === 0) {
    state.planChunks = [];
    drawTimeline();
    el("timeline-note").textContent = "Assign clips to beats to build the timeline";
    return;
  }

  try {
    const data = await api("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ curve: state.selectedCurve, assignments }),
    });
    state.planChunks = data.chunks;
    drawTimeline();
    el("timeline-note").textContent =
      `${data.chunks.length} chunk(s) · ${fmtDuration(data.total_duration)} total`;
  } catch (err) {
    el("timeline-note").textContent = `Plan failed: ${err.message}`;
  }
}

function drawTimeline() {
  const canvas = el("timeline-canvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const planEnd = state.planChunks.length
    ? state.planChunks[state.planChunks.length - 1].output_start + state.planChunks[state.planChunks.length - 1].output_duration
    : 0;
  const totalSpan = Math.max(state.audioDuration || 0, planEnd, 1);

  // Background waveform.
  if (state.audioPeaks.length) {
    const barW = w / state.audioPeaks.length;
    state.audioPeaks.forEach((p, i) => {
      const barH = Math.max(p * (h * 0.3), 1);
      ctx.fillStyle = "#232428";
      ctx.fillRect(i * barW, h - barH, Math.max(barW - 1, 1), barH);
    });
  }

  // Beat markers: bright + labeled if assigned, dim otherwise (a lull candidate).
  const assignedBeats = new Set(state.planChunks.map((c) => c.assigned_beat));
  state.beats.forEach((t) => {
    const x = (t / totalSpan) * w;
    ctx.strokeStyle = assignedBeats.has(t) ? "#ff3b4e" : "#4a4d54";
    ctx.lineWidth = assignedBeats.has(t) ? 2 : 1;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  });

  // Clip chunks: hold (frozen filler) / ramp+tail (curve-paced) / kill flash.
  const rowY = h * 0.42, rowH = h * 0.4;
  state.planChunks.forEach((c) => {
    const color = clipColor(c.clip);
    const x0 = (c.output_start / totalSpan) * w;
    const holdEndX = ((c.output_start + c.hold_seconds) / totalSpan) * w;
    const killX = (c.kill_time_in_output / totalSpan) * w;
    const x1 = ((c.output_start + c.output_duration) / totalSpan) * w;

    if (c.hold_seconds > 0) {
      ctx.fillStyle = "rgba(122,125,133,0.35)";
      ctx.fillRect(x0, rowY, Math.max(holdEndX - x0, 1), rowH);
    }
    ctx.fillStyle = color + "55";
    ctx.fillRect(Math.max(holdEndX, x0), rowY, Math.max(x1 - Math.max(holdEndX, x0), 1), rowH);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.strokeRect(x0, rowY, Math.max(x1 - x0, 1), rowH);

    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(killX, rowY - 4);
    ctx.lineTo(killX, rowY + rowH + 4);
    ctx.stroke();

    ctx.fillStyle = "#e8e8ea";
    ctx.font = "10px monospace";
    ctx.fillText(c.clip, x0 + 4, rowY - 6);
  });
}

// ---- Curves ----
async function loadCurves() {
  const data = await api("/api/curves");
  state.curves = data.curves;
  if (!state.selectedCurve) state.selectedCurve = data.default;

  const grid = el("curve-grid");
  grid.innerHTML = data.curves.map((c) => `
    <div class="curve-tile" data-name="${c.name}">
      <canvas width="120" height="44"></canvas>
      <div class="curve-tile-label">${c.label}</div>
    </div>`).join("");

  grid.querySelectorAll(".curve-tile").forEach((tile, i) => {
    drawCurveSparkline(tile.querySelector("canvas"), data.curves[i]);
    tile.addEventListener("click", () => setActiveCurve(tile.dataset.name));
  });

  setActiveCurve(state.selectedCurve);
}

function drawCurveSparkline(canvas, curve) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const points = curve.ramp.concat(curve.tail);
  const step = w / (points.length - 1);

  ctx.strokeStyle = "#ff3b4e";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  points.forEach((v, i) => {
    const x = i * step;
    const y = h - 4 - v * (h - 8);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function setActiveCurve(name) {
  const changed = state.selectedCurve !== name;
  state.selectedCurve = name;
  el("active-curve-label").textContent = name.replace(/_/g, " ");
  document.querySelectorAll(".curve-tile").forEach((t) => {
    t.classList.toggle("active", t.dataset.name === name);
  });
  if (changed) scheduleRefreshPlan();
}

// ---- Render ----
async function startRender() {
  const assignments = Object.entries(state.beatAssignment)
    .map(([clip, beat]) => ({ clip, beat }))
    .sort((a, b) => a.beat - b.beat);

  if (assignments.length === 0) {
    el("render-log").innerHTML = `<div>Assign at least one clip to a beat before rendering.</div>`;
    return;
  }

  const btn = el("render-btn");
  btn.disabled = true;
  el("output-link").classList.add("hidden");
  el("render-log").innerHTML = "";
  try {
    await api("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ curve: state.selectedCurve, assignments }),
    });
    pollStatus();
  } catch (err) {
    el("render-stage").textContent = "error";
    el("render-log").innerHTML = `<div>${err.message}</div>`;
    btn.disabled = false;
  }
}

function pollStatus() {
  if (state.polling) clearInterval(state.polling);
  state.polling = setInterval(async () => {
    const s = await api("/api/render/status");
    renderStatus(s);
    if (s.status === "done" || s.status === "error") {
      clearInterval(state.polling);
      state.polling = null;
      el("render-btn").disabled = false;
      if (s.status === "done" && s.output_path) {
        const name = s.output_path.split(/[\\/]/).pop();
        const link = el("output-link");
        link.href = `/media/output/${name}`;
        link.classList.remove("hidden");
      }
    }
  }, 800);
}

function renderStatus(s) {
  el("stat-status").textContent = s.status;
  el("render-stage").textContent = s.stage || s.status;
  el("render-percent").textContent = `${s.percent || 0}%`;
  el("progress-fill").style.width = `${s.percent || 0}%`;

  const log = el("render-log");
  if (s.error) {
    log.innerHTML = `<div>[ERROR] ${s.error}</div>`;
  } else {
    log.innerHTML = (s.log || []).map((m) => `<div>${m}</div>`).join("");
  }
}

async function pollOnce() {
  try {
    const s = await api("/api/render/status");
    renderStatus(s);
    if (s.status === "running") {
      el("render-btn").disabled = true;
      pollStatus();
    }
  } catch {
    // server not ready yet
  }
}

// ---- Init ----
el("render-btn").addEventListener("click", startRender);

Promise.all([loadConfig(), loadClips(), loadCurves(), pollOnce()]).catch((err) => {
  console.error(err);
});

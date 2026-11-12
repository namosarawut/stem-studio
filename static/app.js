"use strict";

const $ = (id) => document.getElementById(id);
const VOCAL_STEMS = ["vocals", "lead_vocals", "backing_vocals"];
const CLICK = "click";
const CUES = "cues";
const GUIDES = [CLICK, CUES];
const STEM_ORDER = ["lead_vocals", "backing_vocals", "vocals", "drums", "bass", "guitar", "piano", "other", CLICK, CUES];
const STEM_COLORS = { vocals: "#ff6b5e", lead_vocals: "#ff6b5e", backing_vocals: "#ffa3c7", click: "#d9d5c8", cues: "#8fd3ff", drums: "#f5b83d", bass: "#38d6b4", other: "#8f8cff", guitar: "#8fdc5a", piano: "#5cc6ff" };
const SECTION_COLORS = {
  Intro: "#474a57", Outro: "#474a57", Verse: "#2f5487", "Pre-Chorus": "#5a4a94", Chorus: "#ad3a68",
  Bridge: "#21766a", Instrumental: "#7a6128", Break: "#3a3d48", "Full song": "#474a57",
};
const THEME_COLORS = ["#2f5487", "#ad3a68", "#21766a", "#7a6128", "#5a4a94", "#8a4a2c"];
const PITCH_HUE = { C: 0, "C#": 30, Db: 30, D: 60, "D#": 90, Eb: 90, E: 120, F: 150, "F#": 180, Gb: 180, G: 210, "G#": 240, Ab: 240, A: 270, "A#": 300, Bb: 300, B: 330 };
const ZOOMS = [1, 2, 4, 8, 16, 32, 64, 128, 256];
const PEAKS_PER_SECOND = 30;
const MIN_BEAT_GAP = 0.03;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

const fmtClock = (s) => {
  // Round to whole milliseconds first, so 19.9996 reads 0:20.000 rather than 0:19.000.
  const ms = Math.round(Math.max(0, s) * 1000);
  const whole = Math.floor(ms / 1000);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
};

const fmtTime = (s) => {
  const total = Math.max(0, Math.floor(s));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
};

let toastTimer;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 4500);
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `เซิร์ฟเวอร์ตอบกลับ ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

// ------------------------------------------------------------------ routing

let system = null;
let leaveView = () => {};

function show(view, keepScroll = false) {
  for (const name of ["home", "processing", "studio", "playlists"]) $(`view-${name}`).hidden = name !== view;
  if (!keepScroll) window.scrollTo(0, 0);
}

async function route() {
  leaveView();
  leaveView = () => {};
  const setlist = location.hash.match(/^#\/playlists(?:\/([0-9a-f]{12}))?$/);
  if (setlist) return showPlaylists(setlist[1] ?? null);
  const match = location.hash.match(/^#\/job\/([0-9a-f]{12})$/);
  if (!match) return showHome();
  try {
    const job = await api(`/api/jobs/${match[1]}`);
    if (job.status === "done") await showStudio(job);
    else showProcessing(job);
  } catch (error) {
    toast(error.message);
    location.hash = "#/";
  }
}

// ------------------------------------------------------------------ home

// The two everyday choices sit up front; the slower ones wait behind a toggle.
const MAIN_MODELS = ["htdemucs_6s", "htdemucs"];
let selectedModel = "htdemucs_6s";
let moreModelsOpen = false;

function renderModels() {
  const picker = $("model-picker");
  picker.querySelectorAll(".model, .models-more").forEach((node) => node.remove());
  const option = (model) => {
    const input = el("input", { type: "radio", name: "model", value: model.id, onchange: () => { selectedModel = model.id; } });
    input.checked = model.id === selectedModel;
    return el("label", { class: "model" }, input, el("strong", {}, model.title), el("span", {}, model.description));
  };
  const main = MAIN_MODELS.map((id) => system.models.find((m) => m.id === id)).filter(Boolean);
  const extra = system.models.filter((m) => !MAIN_MODELS.includes(m.id));
  if (!main.some((m) => m.id === selectedModel) && !extra.some((m) => m.id === selectedModel)) selectedModel = main[0]?.id ?? system.models[0]?.id;
  picker.append(...main.map(option));
  if (!extra.length) return;
  // Never hide the option that is currently picked.
  if (extra.some((m) => m.id === selectedModel)) moreModelsOpen = true;
  const extraNodes = extra.map((m) => { const node = option(m); node.hidden = !moreModelsOpen; return node; });
  const toggle = el("button", { type: "button", class: "models-more", "aria-expanded": String(moreModelsOpen) });
  const paint = () => {
    toggle.textContent = moreModelsOpen ? "ซ่อนตัวเลือกเพิ่มเติม" : `ตัวเลือกเพิ่มเติม (${extra.length}) · ${extra.map((m) => m.title).join(", ")}`;
    toggle.setAttribute("aria-expanded", String(moreModelsOpen));
  };
  toggle.addEventListener("click", () => {
    if (moreModelsOpen && extra.some((m) => m.id === selectedModel)) {
      // Closing while a hidden option is picked: fall back to the first main one.
      selectedModel = main[0].id;
      picker.querySelector(`input[value="${selectedModel}"]`).checked = true;
    }
    moreModelsOpen = !moreModelsOpen;
    extraNodes.forEach((node) => { node.hidden = !moreModelsOpen; });
    paint();
  });
  paint();
  picker.append(toggle, ...extraNodes);
}

function modelTitle(id) {
  return system.models.find((m) => m.id === id)?.title ?? id;
}

let libraryJobs = [];

function librarySearchText(job) {
  const parts = [job.title, job.key, modelTitle(job.model)];
  if (job.status === "done") parts.push(`${Math.round(job.bpm)} bpm`, fmtTime(job.duration));
  return parts.filter(Boolean).join(" ").normalize("NFC").toLowerCase();
}

function renderLibrary(jobs = libraryJobs) {
  libraryJobs = jobs;
  const list = $("library");
  list.replaceChildren();
  $("library-section").hidden = jobs.length === 0;
  // Every typed word must appear somewhere in the title, key, BPM or model.
  const query = $("library-search").value.normalize("NFC").toLowerCase().trim();
  const terms = query.split(/\s+/).filter(Boolean);
  const shown = terms.length ? jobs.filter((job) => { const text = librarySearchText(job); return terms.every((term) => text.includes(term)); }) : jobs;
  $("library-count").textContent = terms.length ? `${shown.length} จาก ${jobs.length}` : `${jobs.length} เพลง`;
  $("library-empty").hidden = shown.length > 0 || jobs.length === 0;
  $("library-empty").textContent = `ไม่พบเพลงที่ตรงกับ “${query}”`;
  for (const job of shown) {
    let meta;
    if (job.status === "done") {
      meta = el("span", { class: "lib-meta" }, [fmtTime(job.duration), `${Math.round(job.bpm)} BPM`, job.key, modelTitle(job.model)].join(" · "));
    } else if (job.status === "error") {
      meta = el("span", { class: "lib-meta error" }, job.message);
    } else {
      meta = el("span", { class: "lib-meta" }, `กำลังประมวลผล ${Math.round(job.progress * 100)}%`);
    }
    const open = el("a", { class: "lib-open", href: `#/job/${job.id}` }, el("span", { class: "lib-title" }, job.title), meta);
    const busy = job.status === "queued" || job.status === "running";
    list.append(el("li", { class: "lib-item" }, open, busy ? null : deleteButton(job)));
  }
}

$("library-search").addEventListener("input", () => renderLibrary());
$("library-search").addEventListener("keydown", (event) => {
  if (event.key === "Escape" && event.target.value) { event.target.value = ""; renderLibrary(); }
});

function deleteButton(job) {
  let armed = false;
  let timer;
  const button = el("button", {
    class: "lib-delete", "aria-label": `ลบ ${job.title}`,
    onclick: async () => {
      if (!armed) {
        armed = true;
        button.textContent = "ยืนยันลบ";
        button.classList.add("confirm");
        timer = setTimeout(() => {
          armed = false;
          button.textContent = "ลบ";
          button.classList.remove("confirm");
        }, 3000);
        return;
      }
      clearTimeout(timer);
      try {
        await api(`/api/jobs/${job.id}`, { method: "DELETE" });
        renderLibrary(await api("/api/jobs"));
      } catch (error) { toast(error.message); }
    },
  }, "ลบ");
  return button;
}

async function showHome() {
  show("home");
  drawHeroLanes();
  let timer = null;
  let active = true;
  const refresh = async () => {
    try {
      const jobs = await api("/api/jobs");
      if (!active) return;
      renderLibrary(jobs);
      if (jobs.some((j) => j.status === "queued" || j.status === "running")) timer = setTimeout(refresh, 2000);
    } catch (error) { toast(error.message); }
  };
  leaveView = () => { active = false; clearTimeout(timer); };
  await refresh();
}

/** Decorative lanes that sketch what the app reveals: instruments entering across a song. */
function drawHeroLanes() {
  const canvas = $("hero-lanes");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  let seed = 11;
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const lanes = [
    { color: STEM_COLORS.vocals, on: [[.2, .42], [.5, .72], [.78, .93]], beat: 0 },
    { color: STEM_COLORS.drums, on: [[.12, .72], [.76, .96]], beat: 8 },
    { color: STEM_COLORS.bass, on: [[.12, .96]], beat: 4 },
    { color: STEM_COLORS.other, on: [[0, 1]], beat: 0 },
  ];
  const laneHeight = rect.height / lanes.length;
  const step = 5;
  lanes.forEach((lane, row) => {
    ctx.fillStyle = lane.color;
    const center = laneHeight * (row + .5);
    for (let x = 0, i = 0; x < rect.width; x += step, i++) {
      const position = x / rect.width;
      const active = lane.on.some(([a, b]) => position >= a && position <= b);
      const pulse = lane.beat ? (i % lane.beat === 0 ? 1 : .35 + random() * .25) : .4 + random() * .6;
      const height = (active ? pulse : .03) * laneHeight * .42;
      ctx.fillRect(x, center - height, 3, Math.max(1, height * 2));
    }
  });
}

function setupUpload() {
  const zone = $("dropzone");
  const input = $("file-input");
  input.addEventListener("change", () => {
    if (input.files[0]) upload(input.files[0]);
    input.value = "";
  });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.add("over"); });
  }
  for (const type of ["dragleave", "drop"]) {
    zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.remove("over"); });
  }
  zone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files[0];
    if (file) upload(file);
  });
  // A file dropped outside the zone should not navigate away from the app.
  for (const type of ["dragover", "drop"]) window.addEventListener(type, (event) => event.preventDefault());
}

function upload(file) {
  show("processing");
  setProgress({ title: file.name, percent: 0, message: "กำลังอัปโหลดไฟล์", stage: "upload" });
  $("proc-error").hidden = true;
  const form = new FormData();
  form.append("file", file);
  form.append("model", selectedModel);
  const request = new XMLHttpRequest();
  request.open("POST", "/api/jobs");
  request.upload.onprogress = (event) => {
    if (event.lengthComputable) setProgress({ title: file.name, percent: event.loaded / event.total, message: "กำลังอัปโหลดไฟล์", stage: "upload" });
  };
  request.onload = () => {
    let body = {};
    try { body = JSON.parse(request.responseText); } catch { /* handled by status check */ }
    if (request.status === 201) {
      location.hash = `#/job/${body.id}`;
    } else {
      toast(body.detail || "อัปโหลดไม่สำเร็จ");
      location.hash = "#/";
      route();
    }
  };
  request.onerror = () => {
    toast("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้");
    route();
  };
  request.send(form);
}

// ------------------------------------------------------------------ processing

const STEPS = [["decode", "อ่านไฟล์"], ["model", "โหลดโมเดล"], ["separate", "แยก stem"], ["analyze", "วิเคราะห์"]];

function setProgress({ title, percent, message, stage }) {
  $("proc-title").textContent = title;
  $("proc-percent").textContent = Math.round(percent * 100);
  $("proc-message").textContent = message;
  $("proc-bar").style.width = `${percent * 100}%`;
  const current = STEPS.findIndex(([id]) => id === stage);
  const list = $("proc-steps");
  list.replaceChildren(...STEPS.map(([, label], i) => el("li", { class: i === current ? "active" : i < current ? "finished" : "" }, label)));
  if (stage === "upload") $("proc-eta").textContent = "";
}

function showProcessing(job) {
  show("processing");
  $("proc-error").hidden = true;
  let timer = null;
  let active = true;
  let separateStart = null;

  const render = (current) => {
    setProgress({ title: current.filename, percent: current.progress, message: current.message, stage: current.stage });
    let eta = "";
    if (current.stage === "separate") {
      separateStart ??= { time: Date.now(), progress: current.progress };
      const fraction = (current.progress - separateStart.progress) / (0.82 - separateStart.progress);
      if (fraction > 0.03) {
        const remaining = ((Date.now() - separateStart.time) / 1000) * (1 - fraction) / fraction;
        eta = `แยก stem เหลืออีกประมาณ ${fmtTime(remaining)} นาที · ${current.device}`;
      } else {
        eta = current.device;
      }
    }
    $("proc-eta").textContent = eta;
    if (current.status === "error") {
      $("proc-error").hidden = false;
      $("proc-error-title").textContent = current.message;
      $("proc-error-detail").textContent = current.error || "";
      $("proc-eta").textContent = "";
    }
  };

  const poll = async () => {
    try {
      const current = await api(`/api/jobs/${job.id}`);
      if (!active) return;
      render(current);
      if (current.status === "done") return route();
      if (current.status !== "error") timer = setTimeout(poll, 500);
    } catch (error) {
      toast(error.message);
      timer = setTimeout(poll, 2000);
    }
  };
  leaveView = () => { active = false; clearTimeout(timer); };
  render(job);
  if (job.status !== "error") poll();
}

// ------------------------------------------------------------------ audio engine

/** Pan routing for one track: "C" keeps the stereo image, "L"/"R" fold the track to mono
 * and send it to one speaker only (the usual split for a click or cue in one ear). Three
 * parallel sends with gains, so switching never reconnects nodes or clicks. */
function makeRoute(ctx, output) {
  const input = ctx.createGain();
  const center = ctx.createGain();
  const left = ctx.createGain();
  const right = ctx.createGain();
  // The merger's inputs are mono, so a stereo send is folded as (L + R) / 2: a centred
  // sound keeps its level when moved to one side.
  const merger = ctx.createChannelMerger(2);
  input.connect(center).connect(output);
  input.connect(left).connect(merger, 0, 0);
  input.connect(right).connect(merger, 0, 1);
  merger.connect(output);
  const set = (pan, at = null) => {
    const values = { C: [1, 0, 0], L: [0, 1, 0], R: [0, 0, 1] }[pan] ?? [1, 0, 0];
    [center, left, right].forEach((node, i) => {
      if (at === null) node.gain.value = values[i];
      else node.gain.setTargetAtTime(values[i], at, 0.01);
    });
  };
  set("C");
  return { input, set };
}

const PANS = ["L", "C", "R"];
const PAN_NAMES = { L: "ลำโพงซ้าย", C: "ทั้งสองข้าง", R: "ลำโพงขวา" };
const PAN_SHORT = { L: "ซ้าย", C: "กลาง", R: "ขวา" };

class Mixer {
  constructor(stemIds) {
    this.ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100, latencyHint: "interactive" });
    this.master = this.ctx.createGain();
    this.master.connect(this.ctx.destination);
    this.tracks = new Map();
    for (const id of stemIds) {
      const gain = this.ctx.createGain();
      const analyser = this.ctx.createAnalyser();
      analyser.fftSize = 1024;
      const route = makeRoute(this.ctx, this.master);
      // Keep the meter in the signal path: a dangling analyser may not be pulled.
      gain.connect(analyser).connect(route.input);
      this.tracks.set(id, { id, root: id, gain, analyser, route, pan: "C", buffer: null, fader: 1, mute: false, solo: false, source: null, samples: new Float32Array(1024) });
    }
    this.playing = false;
    this.offset = 0;
    this.startedAt = 0;
    this.loop = null;
    this.duration = 0;
    this.applyGains();
  }

  effectiveGain(track) {
    const anySolo = [...this.tracks.values()].some((t) => t.solo);
    // A metronome that is on stays audible when soloing a part, so you can practice with it.
    if (track.mute || (anySolo && !track.solo && !GUIDES.includes(track.id))) return 0;
    return track.fader;
  }

  applyGains() {
    for (const track of this.tracks.values()) {
      track.gain.gain.setTargetAtTime(this.effectiveGain(track), this.ctx.currentTime, 0.012);
    }
  }

  setPan(id, pan) {
    const track = this.tracks.get(id);
    if (!track || !PANS.includes(pan)) return;
    track.pan = pan;
    track.route.set(pan, this.ctx.currentTime);
  }

  position() {
    if (!this.playing) return this.offset;
    const raw = this.ctx.currentTime - this.startedAt;
    if (this.loop && raw >= this.loop.end) {
      const length = this.loop.end - this.loop.start;
      return this.loop.start + ((raw - this.loop.start) % length);
    }
    return Math.max(0, raw);
  }

  stopSources() {
    for (const track of this.tracks.values()) {
      if (track.source) {
        track.source.onended = null;
        try { track.source.stop(); } catch { /* already stopped */ }
        track.source.disconnect();
        track.source = null;
      }
    }
  }

  async play(from) {
    await this.ctx.resume();
    this.stopSources();
    // Read the offset only after resuming, so a seek made while waiting is kept.
    const target = from ?? this.offset;
    let start = target >= this.duration - 0.05 ? 0 : target;
    if (this.loop && (start < this.loop.start || start >= this.loop.end)) start = this.loop.start;
    const when = this.ctx.currentTime + 0.04;
    for (const track of this.tracks.values()) {
      if (!track.buffer) continue;
      const source = this.ctx.createBufferSource();
      source.buffer = track.buffer;
      if (this.loop) {
        source.loop = true;
        source.loopStart = this.loop.start;
        source.loopEnd = this.loop.end;
      }
      source.connect(track.gain);
      source.start(when, start);
      track.source = source;
    }
    this.startedAt = when - start;
    this.playing = true;
  }

  pause() {
    this.offset = this.position();
    this.stopSources();
    this.playing = false;
  }

  seek(time) {
    const target = Math.min(Math.max(0, time), this.duration);
    if (this.playing) this.play(target);
    else this.offset = target;
  }

  /** Swap one track's audio without disturbing the others, staying in time if playing. */
  replaceBuffer(id, buffer) {
    const track = this.tracks.get(id);
    track.buffer = buffer;
    if (!this.playing) return;
    if (track.source) {
      try { track.source.stop(); } catch { /* already stopped */ }
      track.source.disconnect();
    }
    const when = this.ctx.currentTime + 0.02;
    let offset = when - this.startedAt;
    if (this.loop && offset >= this.loop.end) offset = this.loop.start + ((offset - this.loop.start) % (this.loop.end - this.loop.start));
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    if (this.loop) {
      source.loop = true;
      source.loopStart = this.loop.start;
      source.loopEnd = this.loop.end;
    }
    source.connect(track.gain);
    source.start(when, Math.min(offset, buffer.duration));
    track.source = source;
  }

  setLoop(section) {
    const position = this.position();
    this.loop = section;
    if (this.playing) this.play(position);
  }

  level(track) {
    track.analyser.getFloatTimeDomainData(track.samples);
    let peak = 0;
    for (let i = 0; i < track.samples.length; i += 4) peak = Math.max(peak, Math.abs(track.samples[i]));
    return peak;
  }

  /** Render the current fader, mute, solo and left/right state to a stereo WAV Blob. */
  async exportMix() {
    // Use the audio's own length: the song's listed duration is rounded to the millisecond.
    const length = Math.max(...[...this.tracks.values()].map((t) => t.buffer?.length ?? 0));
    const offline = new OfflineAudioContext(2, length, 44100);
    for (const track of this.tracks.values()) {
      const gainValue = this.effectiveGain(track);
      if (!track.buffer || gainValue === 0) continue;
      const source = offline.createBufferSource();
      source.buffer = track.buffer;
      const gain = offline.createGain();
      gain.gain.value = gainValue;
      const route = makeRoute(offline, offline.destination);
      route.set(track.pan);
      source.connect(gain).connect(route.input);
      source.start();
    }
    return encodeWav(await offline.startRendering());
  }

  destroy() {
    this.stopSources();
    this.ctx.close();
  }
}

function encodeWav(buffer) {
  const channels = [buffer.getChannelData(0), buffer.getChannelData(1)];
  const frames = buffer.length;
  const data = new DataView(new ArrayBuffer(44 + frames * 4));
  const text = (offset, value) => { for (let i = 0; i < value.length; i++) data.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, "RIFF"); data.setUint32(4, 36 + frames * 4, true); text(8, "WAVE"); text(12, "fmt ");
  data.setUint32(16, 16, true); data.setUint16(20, 1, true); data.setUint16(22, 2, true);
  data.setUint32(24, buffer.sampleRate, true); data.setUint32(28, buffer.sampleRate * 4, true);
  data.setUint16(32, 4, true); data.setUint16(34, 16, true); text(36, "data"); data.setUint32(40, frames * 4, true);
  let offset = 44;
  for (let i = 0; i < frames; i++) {
    for (const channel of channels) {
      // The browser decodes 16-bit audio as positive / 32767 and negative / 32768. Invert
      // that exactly and round, so an untouched stem exports to the same samples it came from.
      const sample = Math.max(-1, Math.min(1, channel[i]));
      data.setInt16(offset, Math.round(sample < 0 ? sample * 32768 : sample * 32767), true);
      offset += 2;
    }
  }
  return new Blob([data], { type: "audio/wav" });
}

// ------------------------------------------------------------------ studio

const faderToGain = (value) => (value / 80) ** 2;   // slider 80 is unity, 100 is about +3.9 dB
const gainToDb = (gain) => (gain <= 0.0001 ? "−∞" : `${gain >= 1 ? "+" : "−"}${Math.abs(20 * Math.log10(gain)).toFixed(1)}`);

function sectionColor(section) {
  if (section.kind?.startsWith("Theme")) {
    const index = section.kind.charCodeAt(section.kind.length - 1) - 65;
    return THEME_COLORS[index % THEME_COLORS.length];
  }
  return SECTION_COLORS[section.kind] ?? SECTION_COLORS.Intro;
}

function chordColor(label) {
  const match = label.match(/^([A-G][#b]?)(m?)$/);
  if (!match) return "#23252d";
  return `hsl(${PITCH_HUE[match[1]]} ${match[2] ? 24 : 34}% ${match[2] ? 24 : 31}%)`;
}

async function showStudio(job, keep = null) {
  show("studio", Boolean(keep));
  const analysis = await api(`/api/jobs/${job.id}/analysis`);
  const stems = [
    ...STEM_ORDER.filter((id) => !GUIDES.includes(id)).map((id) => analysis.stems.find((s) => s.id === id)).filter(Boolean),
    ...analysis.stems.filter((s) => s.custom),
    ...GUIDES.map((id) => analysis.stems.find((s) => s.id === id)).filter(Boolean),
  ];
  const songStems = stems.filter((s) => !s.guide && !s.custom);
  const stemColor = (stem) => stem.color ?? STEM_COLORS[stem.id];
  const mixer = new Mixer(stems.map((s) => s.id));
  for (const stem of stems) mixer.tracks.get(stem.id).root = stem.root ?? stem.id;
  mixer.duration = analysis.duration;
  const loader = new AbortController();
  let alive = true;
  let frame = 0;
  let activePreset = keep?.activePreset ?? "all";
  // Silence added before the song for counting in; the server already shifted every time.
  const leadIn = analysis.lead_in?.seconds ?? 0;
  const leadBars = analysis.lead_in?.bars ?? 0;

  $("song-title").textContent = job.title;
  $("song-sub").textContent = `${modelTitle(job.model)} · ประมวลผล ${fmtTime(job.elapsed ?? 0)} นาที บน ${job.device}`;
  document.title = `${job.title} · Prism`;
  $("time-total").textContent = fmtTime(analysis.duration);
  if (!keep) $("time-now").textContent = "0:00";
  $("btn-zip").href = `/api/jobs/${job.id}/download.zip`;
  $("btn-zip").setAttribute("download", "");
  $("btn-play").disabled = true;
  $("btn-export").disabled = true;
  $("btn-add-playlist").disabled = true;
  setPlayingUi(false);
  setLoopUi(false);

  renderStats(analysis);
  renderInsights(analysis);
  renderLoops(analysis);
  renderStemTable(songStems);
  renderResources(job);

  // ---- strips
  const stripRefs = new Map();
  const strips = $("strips");
  strips.replaceChildren(
    el("div", { class: "strip-head ruler" }, "เวลา"),
    el("div", { class: "strip-head sections" }, "ท่อนเพลง"),
    el("div", { class: "strip-head chords" }, "คอร์ด"),
    el("div", { class: "strip-head tempo" }, "ความเร็ว (BPM)"),
  );
  for (const stem of stems) {
    const track = mixer.tracks.get(stem.id);
    const muteButton = el("button", { class: "toggle mute", "aria-pressed": "false", "aria-label": `ปิดเสียง ${stem.name}`, title: "ปิดเสียง" }, "M");
    const soloButton = el("button", { class: "toggle solo", "aria-pressed": "false", "aria-label": `ฟังเฉพาะ ${stem.name}`, title: "ฟังเฉพาะแทร็กนี้" }, "S");
    const saved = keep?.tracks?.[stem.id];
    const fader = el("input", { class: "fader", type: "range", min: "0", max: "100", value: saved?.slider ?? "80", "aria-label": `ระดับเสียง ${stem.name}` });
    const value = el("span", { class: "fader-value" }, "0.0");
    const meter = el("i", { class: "meter" });
    if (saved) {
      track.mute = saved.mute;
      track.solo = saved.solo;
      track.fader = faderToGain(Number(fader.value));
      value.textContent = track.fader === 1 ? "0.0" : gainToDb(track.fader);
      fader.style.setProperty("--fill", `${fader.value}%`);
    } else if (stem.id === CLICK) {
      track.mute = true;  // a guide, so off until asked for
    }
    const touchesPreset = !GUIDES.includes(stem.id);
    muteButton.addEventListener("click", () => { track.mute = !track.mute; if (touchesPreset) activePreset = null; syncMixer(); });
    soloButton.addEventListener("click", () => { track.solo = !track.solo; if (touchesPreset) activePreset = null; syncMixer(); });
    fader.addEventListener("input", () => {
      track.fader = faderToGain(Number(fader.value));
      track.slider = fader.value;
      value.textContent = track.fader === 1 ? "0.0" : gainToDb(track.fader);
      fader.style.setProperty("--fill", `${fader.value}%`);
      mixer.applyGains();
    });
    track.slider = fader.value;
    const download = el("a", { class: "strip-dl", href: `/api/jobs/${job.id}/stems/${stem.id}.wav`, download: "", "aria-label": `ดาวน์โหลด ${stem.name}`, title: "ดาวน์โหลดแทร็กนี้" });
    download.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-5-5m5 5l5-5M4 20h16"/></svg>';
    if (GUIDES.includes(stem.id)) download.addEventListener("click", holdUntilClickSaved);
    const nameLabel = el("div", { class: `strip-name${stem.custom ? " renamable" : ""}`, title: stem.custom ? "คลิกเพื่อเปลี่ยนชื่อ" : stem.name }, stem.name);
    if (stem.custom) nameLabel.addEventListener("click", () => beginRename(stem, nameLabel));
    const strip = el("div", { class: `strip${stem.audible ? "" : " silent"}${stem.custom ? " custom" : ""}`, style: `--stem:${stemColor(stem)}` },
      nameLabel,
      el("div", { class: "strip-buttons" }, muteButton, soloButton, download, stem.custom ? removeTrackButton(stem) : null),
      el("div", { class: "fader-row" }, fader, value),
      meter,
    );
    strips.append(strip);
    const panButtons = PANS.map((pan) => el("button", {
      class: "pan-btn", "aria-pressed": "false", "aria-label": `${stem.name} ออก${PAN_NAMES[pan]}`, title: `ออก${PAN_NAMES[pan]}`,
      onclick: () => { setPan(stem.id, pan); syncPan(); },
    }, pan));
    strip.querySelector(".fader-row").append(el("div", { class: "pan", role: "group", "aria-label": `ลำโพงของ ${stem.name}` }, ...panButtons));
    stripRefs.set(stem.id, { muteButton, soloButton, meter, panButtons });
  }

  // ---- presets
  const PRESETS = [
    { id: "all", label: "ทุกแทร็ก", apply: (t) => { t.mute = false; t.solo = false; } },
    // Tracks cut from a stem follow it: a vocal phrase moved elsewhere is still vocals.
    { id: "karaoke", label: "คาราโอเกะ", vocal: true, apply: (t) => { t.mute = VOCAL_STEMS.includes(t.root); t.solo = false; } },
    { id: "nolead", label: "ตัดเสียงหลัก เก็บคอรัส", needs: "lead_vocals", apply: (t) => { t.mute = t.root === "lead_vocals"; t.solo = false; } },
    { id: "acapella", label: "เสียงร้องอย่างเดียว", vocal: true, apply: (t) => { t.mute = false; t.solo = VOCAL_STEMS.includes(t.root); } },
    { id: "leadonly", label: "เสียงร้องหลักอย่างเดียว", needs: "lead_vocals", apply: (t) => { t.mute = false; t.solo = t.root === "lead_vocals"; } },
    { id: "backingonly", label: "คอรัสอย่างเดียว", needs: "backing_vocals", apply: (t) => { t.mute = false; t.solo = t.root === "backing_vocals"; } },
    { id: "nodrums", label: "ไม่มีกลอง", needs: "drums", apply: (t) => { t.mute = t.root === "drums"; t.solo = false; } },
    { id: "nobass", label: "ไม่มีเบส", needs: "bass", apply: (t) => { t.mute = t.root === "bass"; t.solo = false; } },
  ];
  const presetButtons = new Map();
  $("presets").replaceChildren(...PRESETS.filter((p) => (p.vocal ? VOCAL_STEMS.some((id) => mixer.tracks.has(id)) : !p.needs || mixer.tracks.has(p.needs))).map((preset) => {
    const button = el("button", {
      class: "chip", "aria-pressed": "false",
      onclick: () => {
        mixer.tracks.forEach((track) => { if (!GUIDES.includes(track.id)) preset.apply(track); });
        activePreset = preset.id;
        syncMixer();
      },
    }, preset.label);
    presetButtons.set(preset.id, button);
    return button;
  }));

  // ---- left/right routing, remembered per song in this browser
  const panKey = `stem-studio:pan:${job.id}`;
  const savedPans = (() => { try { return JSON.parse(localStorage.getItem(panKey) || "{}"); } catch { return {}; } })();
  for (const [id, pan] of Object.entries(savedPans)) mixer.setPan(id, pan);
  function setPan(id, pan) {
    mixer.setPan(id, pan);
    try {
      const pans = Object.fromEntries([...mixer.tracks.values()].filter((t) => t.pan !== "C").map((t) => [t.id, t.pan]));
      if (Object.keys(pans).length) localStorage.setItem(panKey, JSON.stringify(pans)); else localStorage.removeItem(panKey);
    } catch { /* storage blocked: the setting still applies until the page closes */ }
  }
  const guideIds = [...mixer.tracks.keys()].filter((id) => GUIDES.includes(id));
  const otherIds = [...mixer.tracks.keys()].filter((id) => !GUIDES.includes(id));
  const groupButtons = [];
  const routingGroup = (label, ids) => el("div", { class: "route-group", role: "group", "aria-label": `${label} ออกลำโพง` },
    el("span", { class: "route-label" }, label),
    ...PANS.map((pan) => {
      const button = el("button", { class: "pan-btn wide", "aria-pressed": "false", onclick: () => { ids.forEach((id) => setPan(id, pan)); syncPan(); } }, PAN_SHORT[pan]);
      groupButtons.push({ button, ids, pan });
      return button;
    }));
  const splitButton = (label, guidePan, otherPan) => el("button", {
    class: "chip", onclick: () => { guideIds.forEach((id) => setPan(id, guidePan)); otherIds.forEach((id) => setPan(id, otherPan)); syncPan(); },
  }, label);
  const routeNote = el("span", { class: "route-note" });
  $("routing").replaceChildren(
    el("span", { class: "route-title" }, "ออกลำโพง"),
    guideIds.length ? routingGroup("คิว + เมโทรนอม", guideIds) : null,
    routingGroup(guideIds.length ? "แทร็กอื่น" : "ทุกแทร็ก", otherIds),
    guideIds.length ? splitButton("คิว+เมโทรนอม ซ้าย · ดนตรี ขวา", "L", "R") : null,
    guideIds.length ? splitButton("คิว+เมโทรนอม ขวา · ดนตรี ซ้าย", "R", "L") : null,
    el("button", { class: "chip", onclick: () => { mixer.tracks.forEach((t) => setPan(t.id, "C")); syncPan(); } }, "คืนค่าตรงกลาง"),
    routeNote,
  );
  function syncPan() {
    for (const [id, refs] of stripRefs) {
      const pan = mixer.tracks.get(id).pan;
      refs.panButtons.forEach((button, i) => button.setAttribute("aria-pressed", PANS[i] === pan));
    }
    for (const { button, ids, pan } of groupButtons) button.setAttribute("aria-pressed", ids.length > 0 && ids.every((id) => mixer.tracks.get(id).pan === pan));
    const split = [...mixer.tracks.values()].some((t) => t.pan !== "C");
    routeNote.textContent = split ? "มีผลตอนเล่นและตอนส่งออกมิกซ์ · ไฟล์แยกแทร็กไม่เปลี่ยน" : "";
  }

  function syncMixer() {
    syncPan();
    mixer.applyGains();
    for (const [id, refs] of stripRefs) {
      const track = mixer.tracks.get(id);
      refs.muteButton.setAttribute("aria-pressed", track.mute);
      refs.soloButton.setAttribute("aria-pressed", track.solo);
    }
    presetButtons.forEach((button, id) => button.setAttribute("aria-pressed", id === activePreset));
    if (mixer.tracks.has(CLICK)) $("btn-click").setAttribute("aria-pressed", !mixer.tracks.get(CLICK).mute);
    drawBase();
  }

  // ---- timeline
  const scroller = $("scroller");
  const inner = $("scroll-inner");
  const stage = $("stage");
  const base = $("tl-base");
  const head = $("tl-head");
  const baseCtx = base.getContext("2d");
  const headCtx = head.getContext("2d");
  const css = getComputedStyle(document.documentElement);
  const px = (name) => parseFloat(css.getPropertyValue(name));
  let zoomIndex = keep?.zoomIndex ?? 0;
  let view = { width: 0, height: 0, pps: 1, rows: null };

  // The metronome as currently edited. `down` marks the first beat of a bar.
  const gridSource = analysis.click_grid ?? { beats: analysis.tempo.beats, downbeats: analysis.tempo.downbeats, meter: parseInt(analysis.meter, 10), edited: false };
  const toBeats = (source) => {
    const accents = new Set(source.downbeats.map((t) => Math.round(t * 1000)));
    return source.beats.map((t) => ({ t, down: accents.has(Math.round(t * 1000)) }));
  };
  let beats = toBeats(gridSource);
  let meter = gridSource.meter;
  let editMode = Boolean(keep?.editMode) && mixer.tracks.has(CLICK);
  let selectedBeat = null;
  let selection = null;
  let drag = null;
  let tempoMap = analysis.tempo_map ?? [];
  let tempoSummary = analysis.tempo_summary ?? null;
  let tempoRange = null;
  const cueStem = analysis.stems.find((s) => s.id === CUES) ?? null;
  let cueList = (cueStem?.cues ?? []).map((c) => ({ ...c }));
  let selectedCue = null;
  let cueRects = [];
  const barStarts = () => beats.filter((b) => b.down).map((b) => b.t);
  // Scale waveforms against the song only; the click's own level would shrink them.
  const globalPeak = Math.max(1, ...stems.filter((s) => !s.guide).map((s) => s.peaks.reduce((a, b) => Math.max(a, b), 0)));

  function layout() {
    const width = scroller.clientWidth;
    if (!width) return;
    const rows = { ruler: px("--row-ruler"), section: px("--row-section"), chord: px("--row-chord"), tempo: px("--row-tempo"), stem: px("--row-stem") };
    const height = rows.ruler + rows.section + rows.chord + rows.tempo + rows.stem * stems.length;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    for (const canvas of [base, head]) {
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    stage.style.width = `${width}px`;
    stage.style.height = `${height}px`;
    inner.style.width = `${width * ZOOMS[zoomIndex]}px`;
    view = { width, height, pps: (width * ZOOMS[zoomIndex]) / analysis.duration, rows };
    drawBase();
    drawHead();
  }

  const scrollOf = () => scroller.scrollLeft;

  function drawBase() {
    const { width, height, pps, rows } = view;
    if (!rows) return;
    const ctx = baseCtx;
    const left = scrollOf();
    const toX = (t) => t * pps - left;
    const firstTime = left / pps;
    const lastTime = (left + width) / pps;
    ctx.clearRect(0, 0, width, height);
    ctx.textBaseline = "middle";

    // Ruler
    const tickOptions = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    const tick = tickOptions.find((s) => s * pps >= 64) ?? 600;
    ctx.font = '11px "IBM Plex Mono", monospace';
    ctx.fillStyle = "#6d6f7b";
    for (let n = Math.floor(firstTime / tick); n * tick <= lastTime; n++) {
      const t = n * tick;
      const x = toX(t);
      ctx.fillRect(x, rows.ruler - 6, 1, 6);
      ctx.fillText(tick < 1 ? fmtClock(t) : fmtTime(t), x + 4, rows.ruler / 2 - 2);
    }
    ctx.fillStyle = "#262832";
    ctx.fillRect(0, rows.ruler - 1, width, 1);

    // Sections
    const sectionTop = rows.ruler;
    ctx.font = '500 12.5px "IBM Plex Sans Thai", sans-serif';
    if (leadIn > 0) {
      const x0 = toX(0);
      const w = leadIn * pps;
      if (x0 + w > 0 && x0 < width) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x0, sectionTop + 3, Math.max(1, w - 2), rows.section - 6);
        ctx.clip();
        ctx.fillStyle = "#2a2c34";
        ctx.fillRect(x0, sectionTop + 3, w, rows.section - 6);
        ctx.strokeStyle = "rgba(255,255,255,.07)";
        ctx.lineWidth = 5;
        for (let s = -rows.section; s < w; s += 13) {
          ctx.beginPath();
          ctx.moveTo(x0 + s, sectionTop + rows.section);
          ctx.lineTo(x0 + s + rows.section, sectionTop);
          ctx.stroke();
        }
        if (w > 40) {
          ctx.fillStyle = "#d9d5c8";
          ctx.fillText(`ช่วงนับ ${leadBars} ห้อง`, Math.max(x0, 0) + 8, sectionTop + rows.section / 2);
        }
        ctx.restore();
        // A faint wash over the tracks shows where the song itself begins.
        ctx.fillStyle = "rgba(255,255,255,.025)";
        ctx.fillRect(x0, sectionTop + rows.section, w, height - sectionTop - rows.section);
      }
    }
    for (const section of analysis.sections) {
      const x = toX(section.start);
      const w = (section.end - section.start) * pps;
      if (x + w < 0 || x > width) continue;
      ctx.fillStyle = sectionColor(section);
      ctx.fillRect(x, sectionTop + 3, Math.max(1, w - 2), rows.section - 6);
      if (w > 34) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x, sectionTop, w - 6, rows.section);
        ctx.clip();
        ctx.fillStyle = "#fff";
        ctx.fillText(section.label, Math.max(x, 0) + 8, sectionTop + rows.section / 2);
        ctx.restore();
      }
    }

    // Chords
    const chordTop = rows.ruler + rows.section;
    ctx.font = '500 12px "IBM Plex Sans Thai", sans-serif';
    for (const chord of analysis.chords) {
      if (chord.label === "N" || chord.end < firstTime || chord.start > lastTime) continue;
      const x = toX(chord.start);
      const w = (chord.end - chord.start) * pps;
      ctx.fillStyle = chordColor(chord.label);
      ctx.fillRect(x, chordTop + 3, Math.max(1, w - 1), rows.chord - 6);
      if (w > ctx.measureText(chord.label).width + 8) {
        ctx.fillStyle = "#f2f0ea";
        ctx.fillText(chord.label, x + 4, chordTop + rows.chord / 2);
      }
    }
    // Tempo of each section, as the metronome will play it
    const tempoTop = chordTop + rows.chord;
    ctx.font = '500 12px "IBM Plex Mono", monospace';
    for (const section of tempoMap) {
      const x = toX(section.start);
      const w = (section.end - section.start) * pps;
      if (x + w < 0 || x > width) continue;
      const steady = section.kind === "steady";
      const chosen = tempoRange && Math.abs(tempoRange.start - section.start) < 0.01 && Math.abs(tempoRange.end - section.end) < 0.01;
      ctx.fillStyle = chosen ? "#4b4f5c" : steady ? "#24262e" : "#4a3a1c";
      ctx.fillRect(x, tempoTop + 3, Math.max(1, w - 2), rows.tempo - 6);
      const label = steady ? section.bpm.toFixed(1) : `${section.from.toFixed(0)}→${section.to.toFixed(0)}`;
      if (w > ctx.measureText(label).width + 10) {
        ctx.fillStyle = steady ? "#d9d5c8" : "#ffd28a";
        ctx.fillText(label, Math.max(x, 0) + 6, tempoTop + rows.tempo / 2);
      }
    }
    const stemsTop = tempoTop + rows.tempo;
    ctx.fillStyle = "#262832";
    ctx.fillRect(0, stemsTop - 1, width, 1);

    // Bar lines, once there is enough room for them to mean something
    const bars = barStarts();
    const bar = bars.length > 1 ? bars[1] - bars[0] : 0;
    if (!editMode && bar * pps >= 14) {
      ctx.fillStyle = "rgba(255,255,255,.045)";
      for (const t of bars) {
        if (t >= firstTime && t <= lastTime) ctx.fillRect(toX(t), stemsTop, 1, height - stemsTop);
      }
    }

    // Waveforms
    // Stored peaks are 33 ms wide, too coarse for lining up a beat, so read the audio itself when zoomed in.
    stems.forEach((stem, row) => {
      const top = stemsTop + row * rows.stem;
      const center = top + rows.stem / 2;
      const maxHeight = rows.stem / 2 - 7;
      const track = mixer.tracks.get(stem.id);
      ctx.fillStyle = stemColor(stem);
      // On the Cues lane the labels carry the meaning, so its waveform stays in the background.
      ctx.globalAlpha = mixer.effectiveGain(track) === 0 ? 0.18 : stem.id === CUES ? 0.35 : 0.9;
      if (stem.id === CLICK) {
        const spaced = beats.length > 1 && (analysis.duration / beats.length) * pps >= 8;
        beats.forEach((beat, index) => {
          if (beat.t < firstTime - 0.1 || beat.t > lastTime + 0.1) return;
          const h = (beat.down ? 1 : 0.55) * maxHeight;
          ctx.fillRect(Math.round(toX(beat.t)), center - h, editMode && spaced ? 2 : 1.25, h * 2);
          if (index === selectedBeat) {
            ctx.save();
            ctx.globalAlpha = 1;
            ctx.fillStyle = "#ff6b5e";
            ctx.fillRect(Math.round(toX(beat.t)) - 1, top + 2, 3, rows.stem - 5);
            ctx.restore();
          }
        });
      } else {
        const fine = pps >= PEAKS_PER_SECOND && track.buffer;
        const step = pps >= 200 ? 1 : 2;
        const left0 = fine ? track.buffer.getChannelData(0) : null;
        const right0 = fine ? track.buffer.getChannelData(1) : null;
        const rate = fine ? track.buffer.sampleRate : 0;
        for (let x = 0; x < width; x += step) {
          let peak = 0;
          if (fine) {
            const from = Math.floor(((x + left) / pps) * rate);
            const to = Math.min(left0.length, Math.floor(((x + step + left) / pps) * rate));
            const stride = Math.max(1, Math.floor((to - from) / 96));
            for (let i = from; i < to; i += stride) {
              const value = Math.max(Math.abs(left0[i]), Math.abs(right0[i]));
              if (value > peak) peak = value;
            }
            peak *= 255;
          } else {
            const from = Math.floor(((x + left) / pps) * PEAKS_PER_SECOND);
            const to = Math.max(from + 1, Math.floor(((x + step + left) / pps) * PEAKS_PER_SECOND));
            for (let i = from; i < to && i < stem.peaks.length; i++) if (stem.peaks[i] > peak) peak = stem.peaks[i];
          }
          if (peak < 0.5) continue;
          const h = Math.max(0.5, Math.min(1, (peak / globalPeak) ** 0.8) * maxHeight);
          ctx.fillRect(x, center - h, step === 1 ? 1 : 1.25, h * 2);
        }
      }
      ctx.globalAlpha = 1;
      if (row < stems.length - 1) {
        ctx.fillStyle = "#262832";
        ctx.fillRect(0, top + rows.stem - 1, width, 1);
      }
    });

    // Cue labels on the Cues lane
    cueRects = [];
    const cueRow = stems.findIndex((s) => s.id === CUES);
    if (cueRow >= 0) {
      const top = stemsTop + cueRow * rows.stem;
      ctx.font = '500 12px "IBM Plex Sans Thai", sans-serif';
      const counted = new Map((cueStem?.count_beats ?? []).map((c) => [c.id, c.beats]));
      cueList.forEach((cue, index) => {
        const x = toX(cue.time);
        const label = cueLabel(cue);
        const w = ctx.measureText(label).width + 14;
        if (x + w < 0 || x > width) return;
        const selected = index === selectedCue;
        for (const beatTime of counted.get(cue.id) ?? []) {
          ctx.fillStyle = "rgba(143,211,255,.65)";
          ctx.fillRect(Math.round(toX(beatTime)) - 1, top + rows.stem - 16, 3, 9);
        }
        ctx.fillStyle = selected ? "#f4efe3" : "rgba(143,211,255,.9)";
        ctx.fillRect(Math.round(x), top + 2, selected ? 2 : 1.5, rows.stem - 5);
        const y0 = top + 8 + (index % 2) * 24;
        ctx.fillStyle = selected ? "#f4efe3" : cue.kind === "count" ? "#2b5f7a" : "#1f4f6b";
        ctx.beginPath();
        ctx.roundRect(x, y0, w, 20, 4);
        ctx.fill();
        ctx.fillStyle = selected ? "#111" : "#eaf6ff";
        ctx.textBaseline = "middle";
        ctx.fillText(label, x + 7, y0 + 10.5);
        cueRects.push({ index, x0: x - 4, x1: x + w, y0: y0 - 2, y1: y0 + 22 });
      });
      if (!cueList.length && pps * analysis.duration > 0) {
        ctx.fillStyle = "#6d6f7b";
        ctx.font = '12.5px "IBM Plex Sans Thai", sans-serif';
        ctx.fillText("คลิกในแถวนี้เพื่อวาง cue ตรงจุดนั้น", Math.max(8, 12 - left), top + rows.stem / 2);
      }
    }

    // In edit mode, carry each beat down through the stems to compare it with the drum hits.
    if (editMode && beats.length > 1 && (analysis.duration / beats.length) * pps >= 5) {
      beats.forEach((beat) => {
        if (beat.t < firstTime || beat.t > lastTime) return;
        ctx.fillStyle = beat.down ? "rgba(244,239,227,.42)" : "rgba(244,239,227,.15)";
        ctx.fillRect(Math.round(toX(beat.t)), stemsTop, 1, height - stemsTop);
      });
    }

    // Stretch of the metronome chosen for a tempo change
    if (tempoRange) {
      const row = stems.findIndex((s) => s.id === CLICK);
      if (row >= 0) {
        const top = stemsTop + row * rows.stem;
        const x = toX(tempoRange.start);
        const w = (tempoRange.end - tempoRange.start) * pps;
        ctx.fillStyle = "rgba(255,210,138,.16)";
        ctx.fillRect(x, top, w, rows.stem - 1);
        ctx.fillStyle = "#ffd28a";
        ctx.fillRect(x - 1, top, 2, rows.stem - 1);
        ctx.fillRect(x + w - 1, top, 2, rows.stem - 1);
      }
    }

    // Region chosen for cutting
    if (selection) {
      const row = stems.findIndex((s) => s.id === selection.stem);
      if (row >= 0) {
        const top = stemsTop + row * rows.stem;
        const x = toX(selection.start);
        const w = (selection.end - selection.start) * pps;
        ctx.save();
        ctx.fillStyle = stemColor(stems[row]);
        ctx.globalAlpha = 0.26;
        ctx.fillRect(x, top, w, rows.stem - 1);
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#f4efe3";
        ctx.fillRect(x - 1, top, 2, rows.stem - 1);
        ctx.fillRect(x + w - 1, top, 2, rows.stem - 1);
        ctx.restore();
      }
    }

    // Looped section
    if (mixer.loop) {
      const x = toX(mixer.loop.start);
      const w = (mixer.loop.end - mixer.loop.start) * pps;
      ctx.fillStyle = "rgba(244,239,227,.08)";
      ctx.fillRect(x, 0, w, height);
      ctx.fillStyle = "rgba(244,239,227,.7)";
      ctx.fillRect(x, 0, 1, height);
      ctx.fillRect(x + w - 1, 0, 1, height);
    }
  }

  function drawHead() {
    const { width, height, pps } = view;
    if (!view.rows) return;
    headCtx.clearRect(0, 0, width, height);
    const x = mixer.position() * pps - scrollOf();
    if (x < -1 || x > width + 1) return;
    headCtx.fillStyle = "#f4efe3";
    headCtx.fillRect(Math.round(x), 0, 1.5, height);
    headCtx.beginPath();
    headCtx.moveTo(x - 5, 0);
    headCtx.lineTo(x + 6, 0);
    headCtx.lineTo(x + 0.75, 7);
    headCtx.fill();
  }

  // ---- now-playing readouts
  let lastSection = null;
  let lastChord = null;
  const matrixHeads = renderMatrix(analysis, songStems, (section) => seekToSection(section));

  function currentSection(time) {
    if (analysis.sections.length && time < analysis.sections[0].start) return null;  // in the lead-in
    return analysis.sections.find((s) => time >= s.start && time < s.end) ?? analysis.sections.at(-1);
  }

  function updateReadouts(time) {
    $("time-now").textContent = fmtTime(time);
    const section = currentSection(time);
    if (section !== lastSection) {
      lastSection = section;
      $("now-section").textContent = section?.label ?? (time < leadIn ? "ช่วงนับ" : "–");
      matrixHeads.forEach((button, s) => button.classList.toggle("current", s === section));
    }
    const chord = analysis.chords.find((c) => time >= c.start && time < c.end);
    if (chord !== lastChord) {
      lastChord = chord;
      const audible = chord && chord.label !== "N";
      $("now-chord").textContent = audible ? chord.label : "–";
      $("now-roman").textContent = audible ? chord.roman : "";
    }
  }

  function tick() {
    if (!alive) return;
    const time = mixer.position();
    if (mixer.playing) {
      if (!mixer.loop && time >= analysis.duration) {
        mixer.pause();
        mixer.offset = 0;
        setPlayingUi(false);
      }
      if (ZOOMS[zoomIndex] > 1 && !drag) {
        const x = time * view.pps - scrollOf();
        if (x > view.width * 0.92 || x < 0) scroller.scrollLeft = time * view.pps - view.width * 0.08;
      }
      for (const [id, refs] of stripRefs) {
        const level = mixer.level(mixer.tracks.get(id));
        refs.meter.style.transform = `scaleY(${1 - Math.min(1, level ** 0.6)})`;
      }
    }
    updateReadouts(mixer.position());
    drawHead();
    frame = requestAnimationFrame(tick);
  }

  function resetMeters() {
    for (const refs of stripRefs.values()) refs.meter.style.transform = "scaleY(1)";
  }

  // ---- transport
  async function togglePlay() {
    if ($("btn-play").disabled) return;
    if (mixer.playing) {
      mixer.pause();
      setPlayingUi(false);
      resetMeters();
    } else {
      await mixer.play();
      setPlayingUi(true);
    }
  }

  function seekTo(time) {
    if (mixer.loop && (time < mixer.loop.start || time >= mixer.loop.end)) {
      mixer.loop = null;
      setLoopUi(false);
      drawBase();
    }
    mixer.seek(time);
    drawHead();
  }

  function seekToSection(section) {
    if (mixer.loop) {
      mixer.loop = section;
      drawBase();
    }
    seekTo(section.start);
  }

  function toggleLoop() {
    if (mixer.loop) {
      mixer.setLoop(null);
      setLoopUi(false);
    } else {
      const section = currentSection(mixer.position());
      if (!section) return;
      mixer.setLoop(section);
      setLoopUi(true);
    }
    drawBase();
  }

  // Everything below is removed by leaveView via one AbortController signal.
  const on = { signal: loader.signal };
  $("btn-play").addEventListener("click", togglePlay, on);
  $("btn-back5").addEventListener("click", () => seekTo(mixer.position() - 5), on);
  $("btn-fwd5").addEventListener("click", () => seekTo(mixer.position() + 5), on);
  $("btn-loop").addEventListener("click", toggleLoop, on);
  function toggleClick() {
    const track = mixer.tracks.get(CLICK);
    if (!track) return;
    track.mute = !track.mute;
    syncMixer();
  }
  $("btn-click").hidden = !mixer.tracks.has(CLICK);
  $("btn-click").addEventListener("click", toggleClick, on);
  scroller.addEventListener("scroll", () => { drawBase(); drawHead(); }, { ...on, passive: true });

  // ---- pointer on the timeline: seek, drag a beat, or choose a region
  const pointerInfo = (event) => {
    const rect = base.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const time = Math.min(analysis.duration, Math.max(0, (x + scrollOf()) / view.pps));
    const { ruler, section, chord, tempo, stem } = view.rows;
    const stemsTop = ruler + section + chord + tempo;
    const row = y >= stemsTop ? stems[Math.floor((y - stemsTop) / stem)] ?? null : null;
    return { x, y, time, row, inSections: y >= ruler && y < ruler + section, inTempo: y >= ruler + section + chord && y < stemsTop };
  };

  const beatAt = (x) => {
    let found = null;
    let nearest = 7;  // pixels
    beats.forEach((beat, index) => {
      const distance = Math.abs(beat.t * view.pps - scrollOf() - x);
      if (distance < nearest) { nearest = distance; found = index; }
    });
    return found;
  };

  const selectionEdge = (info) => {
    if (!selection || info.row?.id !== selection.stem) return null;
    const toX = (t) => t * view.pps - scrollOf();
    if (Math.abs(toX(selection.start) - info.x) <= 6) return "start";
    if (Math.abs(toX(selection.end) - info.x) <= 6) return "end";
    return null;
  };

  // Keep the view moving while a drag reaches the edge of a zoomed timeline.
  const edgeScroll = (x) => {
    if (x < 24) scroller.scrollLeft -= 24 - x;
    else if (x > view.width - 24) scroller.scrollLeft += x - (view.width - 24);
  };

  base.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const info = pointerInfo(event);
    base.setPointerCapture(event.pointerId);
    if (editMode && info.row?.id === CLICK) {
      // Grab single beats only when zoomed in enough to tell them apart; at overview zoom
      // a drag selects a stretch instead, so a beat is never moved by accident.
      const spacing = (analysis.duration / Math.max(1, beats.length)) * view.pps;
      const index = spacing >= 10 ? beatAt(info.x) : null;
      if (index !== null) {
        selectedBeat = index;
        drag = { kind: "beat", index, startX: info.x, startTime: info.time, ripple: $("beat-ripple").checked || event.shiftKey, origin: beats.map((b) => b.t), moved: false };
        renderBeatControls();
        drawBase();
        return;
      }
    }
    if (info.inTempo && mixer.tracks.has(CLICK)) {
      const section = tempoMap.find((s) => info.time >= s.start && info.time < s.end);
      if (section) chooseTempoRange(section.start, section.end);
      return;
    }
    if (editMode && info.row?.id === CLICK) {
      drag = { kind: "trange", startX: info.x, anchor: info.time, info };
      return;
    }
    if (info.row?.id === CUES) {
      const hit = cueRects.find((r) => info.x >= r.x0 && info.x <= r.x1 && info.y >= r.y0 && info.y <= r.y1);
      if (hit) {
        selectCue(hit.index);
        drag = { kind: "cue", index: hit.index, startX: info.x, startTime: info.time, origin: cueList[hit.index].time, moved: false, snapshot: cueList.map((c) => ({ ...c })) };
      } else {
        drag = { kind: "cue-place", info };
      }
      return;
    }
    const edge = selectionEdge(info);
    if (edge) {
      // The opposite edge stays put, even if this one is dragged past it.
      drag = { kind: "edge", anchor: edge === "start" ? selection.end : selection.start };
      return;
    }
    if (info.row && !GUIDES.includes(info.row.id)) {
      drag = { kind: "pending", stem: info.row.id, startX: info.x, startTime: info.time, info };
      return;
    }
    drag = { kind: "seek", info };
  }, on);

  base.addEventListener("pointermove", (event) => {
    const info = pointerInfo(event);
    if (!drag) {
      const overCue = info.row?.id === CUES && cueRects.some((r) => info.x >= r.x0 && info.x <= r.x1 && info.y >= r.y0 && info.y <= r.y1);
      const spaced = (analysis.duration / Math.max(1, beats.length)) * view.pps >= 10;
      if (editMode && info.row?.id === CLICK) base.style.cursor = spaced && beatAt(info.x) !== null ? "ew-resize" : "text";
      else if (info.inTempo) base.style.cursor = "pointer";
      else if (overCue) base.style.cursor = "grab";
      else if (info.row?.id === CUES) base.style.cursor = "copy";
      else if (selectionEdge(info)) base.style.cursor = "ew-resize";
      else base.style.cursor = info.row && !GUIDES.includes(info.row.id) ? "crosshair" : "pointer";
      return;
    }
    if (drag.kind === "pending" && Math.abs(info.x - drag.startX) > 4) {
      selection = { stem: drag.stem, start: drag.startTime, end: drag.startTime };
      drag = { kind: "edge", anchor: drag.startTime };
    }
    if (drag.kind === "edge") {
      edgeScroll(info.x);
      selection.start = Math.min(drag.anchor, info.time);
      selection.end = Math.max(drag.anchor, info.time);
      renderSelectionBar();
      drawBase();
    } else if (drag.kind === "trange") {
      if (Math.abs(info.x - drag.startX) > 4) {
        drag.moved = true;
        edgeScroll(info.x);
        tempoRange = { start: Math.min(drag.anchor, info.time), end: Math.max(drag.anchor, info.time) };
        renderTempoUi();
        drawBase();
      }
    } else if (drag.kind === "cue") {
      edgeScroll(info.x);
      if (Math.abs(info.x - drag.startX) > 2) {
        drag.moved = true;
        let next = drag.origin + (info.time - drag.startTime);
        if (!event.altKey) next = nearestBeatTime(next);
        cueList[drag.index].time = round3(Math.min(analysis.duration, Math.max(0, next)));
        renderCueSelected();
        drawBase();
      }
    } else if (drag.kind === "beat") {
      edgeScroll(info.x);
      const delta = info.time - drag.startTime;
      if (Math.abs(info.x - drag.startX) > 1) {
        drag.moved = true;
        applyBeatMove(drag.index, drag.origin, delta, drag.ripple);
      }
    }
  }, on);

  base.addEventListener("pointerup", (event) => {
    if (!drag) return;
    const current = drag;
    drag = null;
    if (current.kind === "beat") {
      if (current.moved) {
        pushUndo(current.origin);
        scheduleGridSync();
      }
      renderBeatControls();
      return;
    }
    if (current.kind === "trange") {
      if (!current.moved) {
        if (selectedBeat !== null) { selectedBeat = null; renderBeatControls(); drawBase(); }
        seekTo(current.info.time);
      }
      return;
    }
    if (current.kind === "cue") {
      if (current.moved) {
        cueUndo.push(current.snapshot);
        sortCues(cueList[current.index].id);
        scheduleCueSync();
      }
      return;
    }
    if (current.kind === "cue-place") {
      openCuePicker(current.info.time);
      return;
    }
    if (current.kind === "edge") {
      if (selection && $("sel-snap").checked) snapSelection();
      if (selection && selection.end - selection.start < 0.02) selection = null;
      renderSelectionBar();
      drawBase();
      return;
    }
    const info = current.info;
    if (selection) { selection = null; renderSelectionBar(); drawBase(); }
    if (info.inSections) {
      const section = currentSection(info.time);
      if (section) return seekToSection(section);
    }
    if (editMode && selectedBeat !== null) { selectedBeat = null; renderBeatControls(); drawBase(); }
    seekTo(info.time);
  }, on);
  base.addEventListener("pointercancel", () => { drag = null; }, on);

  base.addEventListener("dblclick", (event) => {
    const info = pointerInfo(event);
    if (!editMode || info.row?.id !== CLICK) return;
    const index = beatAt(info.x);
    if (index !== null) return deleteBeat(index);
    const previous = beats.findLastIndex((b) => b.t < info.time);
    const before = beats[previous]?.t ?? -1;
    const after = beats[previous + 1]?.t ?? Infinity;
    if (info.time - before < MIN_BEAT_GAP || after - info.time < MIN_BEAT_GAP) return toast("ชิดกับจังหวะข้างเคียงเกินไป");
    pushUndo();
    beats.splice(previous + 1, 0, { t: round3(info.time), down: false });
    selectedBeat = previous + 1;
    afterGridChange();
  }, on);

  // ---- metronome editing. The server renders the WAV, so what plays is what downloads.
  const round3 = (t) => Math.round(t * 1000) / 1000;
  let undoStack = [];
  let offsetTotal = 0;
  let syncTimer = null;
  let syncSeq = 0;
  let syncPromise = Promise.resolve();
  let gridDirty = false;

  const snapshot = () => beats.map((b) => ({ ...b }));
  function pushUndo(origin = null) {
    let saved = snapshot();
    if (origin) saved = saved.map((b, i) => ({ ...b, t: origin[i] }));
    undoStack.push({ beats: saved, meter, offsetTotal });
    if (undoStack.length > 100) undoStack.shift();
    $("metro-undo").disabled = false;
  }

  function applyBeatMove(index, origin, delta, ripple) {
    const lowest = (origin[index - 1] ?? -MIN_BEAT_GAP) + MIN_BEAT_GAP;
    const highest = ripple ? analysis.duration - (origin.at(-1) - origin[index]) : (origin[index + 1] ?? analysis.duration + MIN_BEAT_GAP) - MIN_BEAT_GAP;
    const moved = round3(Math.min(highest, Math.max(lowest, origin[index] + delta))) - origin[index];
    beats.forEach((beat, i) => {
      if (i === index || (ripple && i > index)) beat.t = round3(origin[i] + moved);
      else beat.t = origin[i];
    });
    renderBeatControls();
    drawBase();
  }

  function medianBpm() {
    if (beats.length < 2) return 0;
    const gaps = beats.slice(1).map((b, i) => b.t - beats[i].t).sort((a, b) => a - b);
    return 60 / gaps[Math.floor(gaps.length / 2)];
  }

  function renderMetroPanel() {
    renderTempoUi();
    $("metro-offset").textContent = `${offsetTotal > 0 ? "+" : ""}${offsetTotal} ms`;
    if (document.activeElement !== $("metro-bpm")) $("metro-bpm").value = medianBpm().toFixed(2);
    $("metro-meter").value = String(meter);
    renderBeatControls();
  }

  function renderBeatControls() {
    const beat = selectedBeat === null ? null : beats[selectedBeat];
    $("beat-tools").hidden = !beat;
    $("beat-name").textContent = beat ? `จังหวะที่ ${selectedBeat + 1} · ${fmtClock(beat.t)}${beat.down ? " · จังหวะที่ 1 ของห้อง" : ""}` : "คลิกขีดในแถวเมโทรนอมเพื่อเลือกจังหวะ";
  }

  function afterGridChange() {
    renderMetroPanel();
    drawBase();
    scheduleGridSync();
  }

  function scheduleGridSync() {
    gridDirty = true;
    $("load-note").textContent = "กำลังอัปเดตเมโทรนอม…";
    clearTimeout(syncTimer);
    syncTimer = setTimeout(() => { syncTimer = null; syncPromise = pushGrid(); }, 200);
  }

  async function pushGrid() {
    const seq = ++syncSeq;
    try {
      const saved = await api(`/api/jobs/${job.id}/click`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ beats: beats.map((b) => b.t), downbeats: barStarts(), meter }),
      });
      if (seq === syncSeq) {
        setTempoMap(saved);
        await reloadClickAudio(seq);
      }
      if (cueList.some((c) => c.kind === "count")) await refreshCuesFromServer();
    } catch (error) {
      if (error.name === "AbortError" || !alive) return;
      $("load-note").textContent = "บันทึกเมโทรนอมไม่สำเร็จ";
      toast(error.message);
    }
  }

  async function reloadClickAudio(seq = syncSeq) {
    const response = await fetch(`/api/jobs/${job.id}/stems/${CLICK}.wav`, { signal: loader.signal, cache: "no-cache" });
    if (!response.ok) throw new Error("โหลดเมโทรนอมไม่สำเร็จ");
    const buffer = await mixer.ctx.decodeAudioData(await response.arrayBuffer());
    if (seq !== syncSeq || !alive) return;
    mixer.replaceBuffer(CLICK, buffer);
    gridDirty = false;
    $("load-note").textContent = "";
  }

  // Exports must not leave before the newest edit has reached the server.
  async function flushGrid() {
    if (syncTimer) {
      clearTimeout(syncTimer);
      syncTimer = null;
      syncPromise = pushGrid();
    }
    await syncPromise;
    await flushCues();
  }

  async function holdUntilClickSaved(event) {
    if (!gridDirty && !cuesDirty) return;
    event.preventDefault();
    const href = event.currentTarget.href;
    await flushGrid();
    if (!gridDirty && !cuesDirty) location.href = href;
  }

  function applyBarPattern(anchor, from = 0) {
    beats.forEach((beat, i) => {
      if (i >= from) beat.down = i >= anchor ? (i - anchor) % meter === 0 : (anchor - i) % meter === 0;
    });
  }

  function firstBarIndex() {
    const index = beats.findIndex((b) => b.down);
    return index < 0 ? 0 : index;
  }

  function shiftAll(ms) {
    pushUndo();
    const seconds = ms / 1000;
    beats = beats.map((b) => ({ ...b, t: round3(b.t + seconds) })).filter((b) => b.t >= 0 && b.t <= analysis.duration);
    offsetTotal += ms;
    selectedBeat = selectedBeat !== null && selectedBeat < beats.length ? selectedBeat : null;
    afterGridChange();
  }

  function nudgeSelected(ms) {
    if (selectedBeat === null) return;
    const origin = beats.map((b) => b.t);
    pushUndo();
    applyBeatMove(selectedBeat, origin, ms / 1000, $("beat-ripple").checked);
    afterGridChange();
  }

  function deleteBeat(index) {
    if (beats.length <= 2) return toast("ต้องมีอย่างน้อย 2 จังหวะ");
    pushUndo();
    beats.splice(index, 1);
    selectedBeat = null;
    afterGridChange();
  }

  function lockBpm() {
    const bpm = Number($("metro-bpm").value);
    if (!Number.isFinite(bpm) || bpm < 30 || bpm > 300) return toast("BPM ต้องอยู่ระหว่าง 30 ถึง 300");
    if (tempoRange) return setRangeBpm(bpm);
    const period = 60 / bpm;
    const anchor = selectedBeat !== null ? beats[selectedBeat] : beats[firstBarIndex()];
    pushUndo();
    const before = Math.floor(anchor.t / period);
    const count = Math.floor((analysis.duration - anchor.t) / period);
    const fresh = [];
    for (let k = -before; k <= count; k++) {
      fresh.push({ t: round3(anchor.t + k * period), down: false });
    }
    beats = fresh;
    applyBarPattern(before);
    selectedBeat = before;
    offsetTotal = 0;
    afterGridChange();
  }

  // ---- tempo sections: one BPM per stretch of the song
  function setTempoMap(source) {
    if (!source?.tempo_map) return;
    tempoMap = source.tempo_map;
    tempoSummary = source.tempo_summary ?? tempoSummary;
    analysis.tempo_summary = tempoSummary;
    analysis.tempo_map = tempoMap;
    renderStats(analysis);
    renderTempoUi();
  }

  const rangeIndexes = () => beats.map((_, i) => i).filter((i) => beats[i].t >= tempoRange.start - 0.002 && beats[i].t < tempoRange.end - 0.002);

  function chooseTempoRange(start, end) {
    tempoRange = { start, end };
    if (!editMode) setEditMode(true);
    const indexes = rangeIndexes();
    if (indexes.length > 1) {
      const seconds = beats[indexes.at(-1)].t - beats[indexes[0]].t;
      $("metro-bpm").value = (60 * (indexes.length - 1) / seconds).toFixed(2);
    }
    renderTempoUi();
    drawBase();
  }

  function renderTempoUi() {
    const chips = $("tempo-chips");
    chips.replaceChildren(...tempoMap.map((section) => {
      const steady = section.kind === "steady";
      const text = `${fmtTime(section.start)}–${fmtTime(section.end)} · ${steady ? `${section.bpm.toFixed(1)} BPM` : `${section.kind === "accel" ? "เร็วขึ้น" : "ช้าลง"} ${section.from.toFixed(0)}→${section.to.toFixed(0)}`}`;
      const chosen = tempoRange && Math.abs(tempoRange.start - section.start) < 0.01 && Math.abs(tempoRange.end - section.end) < 0.01;
      return el("button", {
        class: `tempo-chip${steady ? "" : " changing"}`, "aria-pressed": String(Boolean(chosen)),
        onclick: () => { chooseTempoRange(section.start, section.end); seekTo(section.start); },
      }, text);
    }));
    $("range-row").hidden = !tempoRange;
    $("metro-apply-bpm").textContent = tempoRange ? "ตั้งเป็นจังหวะคงที่ (ช่วงที่เลือก)" : "ตั้งเป็นจังหวะคงที่ (ทั้งเพลง)";
    if (tempoRange) {
      const count = rangeIndexes().length;
      $("range-label").textContent = `ช่วง ${fmtClock(tempoRange.start)}–${fmtClock(tempoRange.end)} · ${count} จังหวะ`;
    }
  }

  function setRangeBpm(bpm) {
    const indexes = rangeIndexes();
    if (indexes.length < 2) return toast("ช่วงที่เลือกสั้นเกินไป ลากให้ครอบอย่างน้อย 2 จังหวะ");
    const first = indexes[0];
    const boundary = beats[indexes.at(-1) + 1]?.t ?? analysis.duration;
    const period = 60 / bpm;
    // Keep the bar count going from the first beat of the range.
    let lastDown = first;
    while (lastDown > 0 && !beats[lastDown].down) lastDown--;
    const position = beats[lastDown].down ? (first - lastDown) % meter : 0;
    const fresh = [];
    for (let t = beats[first].t; t < boundary - period * 0.5 + 1e-6; t += period) {
      fresh.push({ t: round3(t), down: (position + fresh.length) % meter === 0 });
    }
    pushUndo();
    beats.splice(first, indexes.length, ...fresh);
    selectedBeat = null;
    afterGridChange();
    toast(`ตั้ง ${bpm.toFixed(2)} BPM ให้ช่วงนี้แล้ว${boundary - (fresh.at(-1)?.t ?? 0) > period * 1.05 ? " (จังหวะสุดท้ายของช่วงห่างจากช่วงถัดไปไม่เท่ากัน ใช้ “เกลี่ยให้ห่างเท่ากัน” ได้)" : ""}`);
  }

  function evenRange() {
    const indexes = rangeIndexes();
    if (indexes.length < 2) return toast("ช่วงที่เลือกสั้นเกินไป");
    const first = beats[indexes[0]].t;
    const next = beats[indexes.at(-1) + 1];
    const end = next ? next.t : beats[indexes.at(-1)].t;
    const steps = next ? indexes.length : indexes.length - 1;
    pushUndo();
    indexes.forEach((index, k) => { beats[index].t = round3(first + (end - first) * k / steps); });
    afterGridChange();
    toast(`เกลี่ยแล้ว: ${(60 * steps / (end - first)).toFixed(2)} BPM ตลอดช่วง`);
  }

  function doubleBeats() {
    pushUndo();
    const anchorTime = beats[firstBarIndex()].t;
    const fresh = [];
    beats.forEach((beat, i) => {
      fresh.push({ t: beat.t, down: false });
      const next = beats[i + 1];
      if (next && next.t - beat.t >= MIN_BEAT_GAP * 2) fresh.push({ t: round3((beat.t + next.t) / 2), down: false });
    });
    beats = fresh;
    applyBarPattern(Math.max(0, beats.findIndex((b) => b.t === anchorTime)));
    selectedBeat = null;
    afterGridChange();
  }

  function halveBeats() {
    if (beats.length < 4) return toast("จังหวะน้อยเกินไป");
    pushUndo();
    const anchor = firstBarIndex();
    beats = beats.filter((_, i) => (i - anchor) % 2 === 0).map((b) => ({ t: b.t, down: false }));
    applyBarPattern(Math.floor(anchor / 2));
    selectedBeat = null;
    afterGridChange();
  }

  function setEditMode(on) {
    editMode = on;
    $("metro-panel").hidden = !on;
    $("btn-edit-click").setAttribute("aria-pressed", on);
    if (on) {
      const track = mixer.tracks.get(CLICK);
      if (track.mute) { track.mute = false; }
      if (selection) { selection = null; renderSelectionBar(); }
      syncMixer();
      renderMetroPanel();
    } else {
      selectedBeat = null;
    }
    drawBase();
    layout();
  }

  $("btn-edit-click").hidden = !mixer.tracks.has(CLICK);
  $("btn-edit-click").setAttribute("aria-pressed", editMode);
  $("metro-panel").hidden = !editMode;
  $("btn-edit-click").addEventListener("click", () => setEditMode(!editMode), on);
  for (const button of document.querySelectorAll("#metro-panel [data-shift]")) {
    button.addEventListener("click", () => shiftAll(Number(button.dataset.shift)), on);
  }
  for (const button of document.querySelectorAll("#metro-panel [data-nudge]")) {
    button.addEventListener("click", () => nudgeSelected(Number(button.dataset.nudge)), on);
  }
  $("metro-apply-bpm").addEventListener("click", lockBpm, on);
  $("range-even").addEventListener("click", evenRange, on);
  $("range-clear").addEventListener("click", () => { tempoRange = null; renderTempoUi(); drawBase(); }, on);
  $("metro-double").addEventListener("click", doubleBeats, on);
  $("metro-halve").addEventListener("click", halveBeats, on);
  $("metro-meter").addEventListener("change", () => {
    pushUndo();
    meter = Number($("metro-meter").value);
    applyBarPattern(firstBarIndex());
    afterGridChange();
  }, on);
  $("beat-downbeat").addEventListener("click", () => {
    if (selectedBeat === null) return;
    pushUndo();
    applyBarPattern(selectedBeat, selectedBeat);
    afterGridChange();
  }, on);
  $("beat-delete").addEventListener("click", () => { if (selectedBeat !== null) deleteBeat(selectedBeat); }, on);
  $("metro-undo").disabled = true;
  $("metro-undo").addEventListener("click", () => {
    const previous = undoStack.pop();
    if (!previous) return;
    beats = previous.beats;
    meter = previous.meter;
    offsetTotal = previous.offsetTotal;
    selectedBeat = null;
    $("metro-undo").disabled = undoStack.length === 0;
    afterGridChange();
  }, on);
  $("metro-reset").addEventListener("click", async () => {
    try {
      clearTimeout(syncTimer);
      syncTimer = null;
      const seq = ++syncSeq;
      const auto = await api(`/api/jobs/${job.id}/click`, { method: "DELETE" });
      pushUndo();
      setTempoMap(auto);
      tempoRange = null;
      beats = toBeats(auto);
      meter = auto.meter;
      offsetTotal = 0;
      selectedBeat = null;
      renderMetroPanel();
      drawBase();
      await reloadClickAudio(seq);
    } catch (error) { toast(error.message); }
  }, on);

  // ---- spoken cues. The server renders cues.wav, so what plays is what downloads.
  let cueLibrary = null;
  let cueUndo = [];
  let cueTimer = null;
  let cuePromise = Promise.resolve();
  let cueSeq = 0;
  let cuesDirty = false;
  let pickerTab = "sections";
  let pickerChoice = null;
  let pickerTime = 0;
  const hasCues = mixer.tracks.has(CUES);
  const loadVoice = () => { try { return localStorage.getItem("stem-studio-cue-voice"); } catch { return null; } };
  const saveVoice = (v) => { try { localStorage.setItem("stem-studio-cue-voice", v); } catch { /* storage unavailable */ } };
  const STYLE_SHORT = { full: "1 2 3 4", long: "นับ 2 ห้อง", short: "3 4", ready_go: "พร้อม ไป", gap1: "เว้น 1", gap2: "เว้น 2", gap3: "เว้น 3" };

  function cueLabel(cue) {
    if (cue.kind === "count") return `นับเข้า · ${STYLE_SHORT[cue.style] ?? ""}`;
    return cue.label || cue.text;
  }

  function nearestBeatTime(t) {
    if (!beats.length) return t;
    let best = beats[0].t;
    for (const b of beats) if (Math.abs(b.t - t) < Math.abs(best - t)) best = b.t;
    return best;
  }

  function beatIndexNear(t) {
    let best = 0;
    beats.forEach((b, i) => { if (Math.abs(b.t - t) < Math.abs(beats[best].t - t)) best = i; });
    return best;
  }

  function sortCues(keepId) {
    cueList.sort((a, b) => a.time - b.time);
    if (keepId) selectedCue = cueList.findIndex((c) => c.id === keepId);
  }

  const newId = () => Math.random().toString(16).slice(2, 12);

  function scheduleCueSync() {
    cuesDirty = true;
    $("load-note").textContent = "กำลังสร้างเสียง cue…";
    clearTimeout(cueTimer);
    cueTimer = setTimeout(() => { cueTimer = null; cuePromise = pushCues(); }, 250);
    $("btn-undo-cue").hidden = !cueUndo.length;
    $("btn-clear-cue").hidden = !cueList.length;
    drawBase();
  }

  async function pushCues() {
    const seq = ++cueSeq;
    const keepId = cueList[selectedCue]?.id;
    try {
      const result = await api(`/api/jobs/${job.id}/cues`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cues: cueList }),
      });
      if (seq !== cueSeq || !alive) return;
      cueList = result.cues;
      if (cueStem) { cueStem.count_beats = result.count_beats; cueStem.peaks = result.peaks; }
      selectedCue = keepId ? cueList.findIndex((c) => c.id === keepId) : null;
      if (selectedCue === -1) selectedCue = null;
      await reloadGuideAudio(CUES, seq, () => cueSeq);
      cuesDirty = false;
      renderCueSelected();
      drawBase();
    } catch (error) {
      if (error.name === "AbortError" || !alive) return;
      $("load-note").textContent = "บันทึก cue ไม่สำเร็จ";
      toast(error.message);
    }
  }

  async function flushCues() {
    if (cueTimer) {
      clearTimeout(cueTimer);
      cueTimer = null;
      cuePromise = pushCues();
    }
    await cuePromise;
  }

  async function refreshCuesFromServer() {
    const fresh = await api(`/api/jobs/${job.id}/analysis`);
    const stem = fresh.stems.find((s) => s.id === CUES);
    if (!stem || !cueStem) return;
    cueStem.count_beats = stem.count_beats;
    cueStem.peaks = stem.peaks;
    await reloadGuideAudio(CUES, cueSeq, () => cueSeq);
    drawBase();
  }

  async function reloadGuideAudio(id, seq, current) {
    const response = await fetch(`/api/jobs/${job.id}/stems/${id}.wav`, { signal: loader.signal, cache: "no-cache" });
    if (!response.ok) throw new Error("โหลดแทร็กไม่สำเร็จ");
    const buffer = await mixer.ctx.decodeAudioData(await response.arrayBuffer());
    if (seq !== current() || !alive) return;
    mixer.replaceBuffer(id, buffer);
    $("load-note").textContent = "";
  }

  function changeCues(mutate) {
    cueUndo.push(cueList.map((c) => ({ ...c })));
    if (cueUndo.length > 100) cueUndo.shift();
    mutate();
    scheduleCueSync();
  }

  function selectCue(index) {
    selectedCue = index;
    if (index !== null) {
      $("cue-panel").hidden = true;
      if (selection) { selection = null; renderSelectionBar(); }
    }
    renderCueSelected();
    drawBase();
  }

  function deleteCue(index) {
    changeCues(() => { cueList.splice(index, 1); selectedCue = null; });
    renderCueSelected();
  }

  function voiceOptions(select, chosen) {
    const voices = cueLibrary?.voices ?? [];
    select.replaceChildren(...voices.map((v) => el("option", { value: v.id }, v.label)));
    if (voices.some((v) => v.id === chosen)) select.value = chosen;
  }

  function renderCueSelected() {
    const cue = selectedCue === null ? null : cueList[selectedCue];
    $("cue-selected").hidden = !cue;
    if (!cue) return;
    $("cs-label").textContent = cueLabel(cue);
    if (document.activeElement !== $("cs-time")) $("cs-time").value = cue.time.toFixed(3);
    voiceOptions($("cs-voice"), cue.voice);
  }

  function libraryItem(id) {
    for (const category of cueLibrary.categories) {
      const item = category.items.find((i) => i.id === id);
      if (item) return item;
    }
    return null;
  }

  const voiceLang = (id) => cueLibrary?.voices.find((v) => v.id === id)?.lang ?? "th";

  function phraseFor(item, voice) {
    return voiceLang(voice) === "th" ? item.th : item.en;
  }

  async function ensureLibrary() {
    if (!cueLibrary) cueLibrary = await api("/api/cues/library");
    return cueLibrary;
  }

  function placeTime(raw, kind) {
    let t = raw;
    if ($("cue-snap").checked) t = nearestBeatTime(t);
    if (kind === "count") return t;  // counts already speak on the beats before this point
    const offset = $("cue-offset").value;
    if (offset !== "0" && beats.length) {
      const index = beatIndexNear(t);
      const back = offset === "-2" ? 2 : offset === "-bar" ? meter : meter * 2;
      const target = index - back;
      if (target >= 0) t = beats[target].t;
      else t = Math.max(0, t - back * (60 / Math.max(30, medianBpm() || 120)));
    }
    return Math.max(0, Math.min(analysis.duration, t));
  }

  async function openCuePicker(time) {
    try { await ensureLibrary(); } catch (error) { return toast(error.message); }
    if (!cueLibrary.voices.length) return toast("ไม่พบเสียงอ่านในเครื่องนี้");
    pickerTime = Math.max(0, Math.min(analysis.duration, time));
    selectCue(null);
    if (selection) { selection = null; renderSelectionBar(); }
    $("cue-time").value = pickerTime.toFixed(2);
    voiceOptions($("cue-voice"), $("cue-voice").value || loadVoice() || "kanya");
    $("cue-panel").hidden = false;
    renderPicker();
    drawBase();
  }

  function renderPicker() {
    const tabs = [...cueLibrary.categories.map((c) => ({ id: c.id, label: c.label })), { id: "count", label: "นับเข้า" }, { id: "custom", label: "พิมพ์เอง" }];
    $("cue-tabs").replaceChildren(...tabs.map((tab) => el("button", {
      class: "chip", role: "tab", "aria-pressed": String(tab.id === pickerTab),
      onclick: () => { pickerTab = tab.id; pickerChoice = null; $("cue-search").value = ""; renderPicker(); },
    }, tab.label)));
    const voice = $("cue-voice").value;
    const query = $("cue-search").value.trim().toLowerCase();
    const box = $("cue-items");
    box.replaceChildren();
    $("cue-custom").hidden = pickerTab !== "custom" || Boolean(query);
    $("cue-offset-field").hidden = pickerTab === "count" && !query;
    const choose = (choice, button) => {
      pickerChoice = choice;
      box.querySelectorAll(".cue-item").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      $("cue-add").disabled = false;
      previewChoice();
    };
    const itemButton = (item, sub) => {
      const button = el("button", { class: "cue-item", "aria-pressed": "false" }, phraseFor(item, voice), sub ? el("small", {}, sub) : null);
      button.addEventListener("click", () => choose({ kind: "phrase", item }, button));
      button.addEventListener("dblclick", () => { choose({ kind: "phrase", item }, button); addFromPicker(); });
      return button;
    };
    if (query) {
      const hits = [];
      for (const category of cueLibrary.categories) {
        for (const item of category.items) {
          if (`${item.en} ${item.th}`.toLowerCase().includes(query)) hits.push(itemButton(item, voiceLang(voice) === "th" ? item.en : item.th));
        }
      }
      if (hits.length) box.append(...hits);
      else box.append(el("p", { class: "cue-empty" }, "ไม่พบใน cue สำเร็จรูป ใช้แท็บ “พิมพ์เอง” เพื่อให้พูดคำนี้ได้"));
    } else if (pickerTab === "count") {
      for (const style of cueLibrary.count_styles) {
        const button = el("button", { class: "cue-item", "aria-pressed": "false" }, style.label);
        button.addEventListener("click", () => choose({ kind: "count", style: style.id }, button));
        button.addEventListener("dblclick", () => { choose({ kind: "count", style: style.id }, button); addFromPicker(); });
        box.append(button);
      }
    } else if (pickerTab === "custom") {
      pickerChoice = $("cue-text").value.trim() ? { kind: "custom" } : null;
    } else {
      const category = cueLibrary.categories.find((c) => c.id === pickerTab);
      box.append(...category.items.map((item) => itemButton(item, voiceLang(voice) === "th" ? item.en : item.th)));
    }
    $("cue-add").disabled = !pickerChoice;
    $("cue-hint").textContent = pickerTab === "count" && !query
      ? `เวลาที่เลือกคือจังหวะที่วงเข้า ระบบจะนับบนจังหวะก่อนหน้านั้นตามเมโทรนอม (${meter} จังหวะต่อห้อง)`
      : "คลิกเพื่อฟังตัวอย่าง ดับเบิลคลิกเพื่อใส่ทันที";
  }

  function choiceText(choice, voice) {
    if (choice.kind === "phrase") return phraseFor(choice.item, voice);
    if (choice.kind === "custom") return $("cue-text").value.trim();
    const words = cueLibrary.numbers[voiceLang(voice)];
    return words.slice(0, Math.min(meter, 4)).join(" ");
  }

  let previewAudio = null;
  function playPreview(voice, text, kind = "phrase") {
    if (!text) return;
    previewAudio?.pause();
    previewAudio = new Audio(`/api/cues/preview?${new URLSearchParams({ voice, text, kind })}`);
    previewAudio.play().catch(() => { /* blocked or failed: the cue can still be placed */ });
  }

  function previewChoice() {
    if (!pickerChoice) return;
    const voice = $("cue-voice").value;
    playPreview(voice, choiceText(pickerChoice, voice), pickerChoice.kind === "count" ? "count" : "phrase");
  }

  function addFromPicker() {
    if (!pickerChoice) return;
    const voice = $("cue-voice").value;
    const raw = Number($("cue-time").value);
    const base = Number.isFinite(raw) ? raw : pickerTime;
    const id = newId();
    let cue;
    if (pickerChoice.kind === "count") {
      cue = { id, kind: "count", style: pickerChoice.style, time: round3(placeTime(base, "count")), voice };
    } else {
      const text = choiceText(pickerChoice, voice);
      if (!text) { $("cue-text").focus(); return toast("กรุณาพิมพ์ข้อความ"); }
      cue = { id, kind: "phrase", text, label: text, time: round3(placeTime(base, "phrase")), voice };
    }
    saveVoice(voice);
    changeCues(() => { cueList.push(cue); sortCues(id); });
    $("cue-panel").hidden = true;
    renderCueSelected();
  }

  const SECTION_ITEMS = { Intro: "intro", "Pre-Chorus": "pre_chorus", Bridge: "bridge", Instrumental: "instrumental", Break: "break", Outro: "outro" };

  async function autoCues() {
    try { await ensureLibrary(); } catch (error) { return toast(error.message); }
    const voice = $("cue-voice").value || loadVoice() || cueLibrary.voices[0]?.id;
    if (!voice || !cueLibrary.voices.some((v) => v.id === voice)) return toast("ไม่พบเสียงอ่านในเครื่องนี้");
    const lang = voiceLang(voice);
    const made = [];
    analysis.sections.forEach((section, i) => {
      if (section.start < 0.5 && i === 0) return;  // nothing plays before the opening section
      const match = section.label.match(/^(.*?)(?:\s(\d+))?$/);
      const kind = section.kind;
      const number = Number(match?.[2]);
      let text;
      if (kind === "Verse" || kind === "Chorus") {
        const id = number >= 1 && number <= (kind === "Verse" ? 4 : 3) ? `${kind.toLowerCase()}_${number}` : kind.toLowerCase();
        text = phraseFor(libraryItem(id), voice);
      } else if (SECTION_ITEMS[kind]) {
        text = phraseFor(libraryItem(SECTION_ITEMS[kind]), voice);
        if (number) text += ` ${cueLibrary.numbers[lang][number - 1] ?? number}`;
      } else if (kind.startsWith("Theme")) {
        text = `${lang === "th" ? "ธีม" : "Theme"} ${kind.slice(-1)}`;
      } else {
        return;
      }
      const index = beatIndexNear(section.start);
      const start = beats[Math.max(0, index - meter)]?.t ?? Math.max(0, section.start - 2);
      made.push({ id: newId(), kind: "phrase", text, label: text, time: round3(start), voice, auto: true });
    });
    if (!made.length) return toast("ไม่พบท่อนเพลงที่จะใส่ cue ให้");
    changeCues(() => { cueList = [...cueList.filter((c) => !c.auto), ...made]; sortCues(); selectedCue = null; });
    renderCueSelected();
    toast(`ใส่ cue ${made.length} จุด หนึ่งห้องก่อนเข้าแต่ละท่อน`);
  }

  $("btn-add-cue").hidden = !hasCues;
  $("btn-auto-cue").hidden = !hasCues;
  $("btn-undo-cue").hidden = true;
  $("btn-clear-cue").hidden = !hasCues || !cueList.length;
  $("btn-add-cue").addEventListener("click", () => openCuePicker(mixer.position()), on);
  $("btn-auto-cue").addEventListener("click", autoCues, on);
  $("btn-undo-cue").addEventListener("click", () => {
    const previous = cueUndo.pop();
    if (!previous) return;
    cueList = previous;
    selectedCue = null;
    renderCueSelected();
    scheduleCueSync();
  }, on);
  let clearArmed = null;
  $("btn-clear-cue").addEventListener("click", () => {
    const button = $("btn-clear-cue");
    if (!clearArmed) {
      button.textContent = `ยืนยันลบ ${cueList.length} cue`;
      clearArmed = setTimeout(() => { clearArmed = null; button.textContent = "ล้าง cue ทั้งหมด"; }, 3000);
      return;
    }
    clearTimeout(clearArmed);
    clearArmed = null;
    button.textContent = "ล้าง cue ทั้งหมด";
    changeCues(() => { cueList = []; selectedCue = null; });
    renderCueSelected();
  }, on);
  $("cue-close").addEventListener("click", () => { $("cue-panel").hidden = true; }, on);
  $("cue-search").addEventListener("input", () => { pickerChoice = null; renderPicker(); }, on);
  $("cue-voice").addEventListener("change", () => { saveVoice($("cue-voice").value); renderPicker(); }, on);
  $("cue-text").addEventListener("input", () => { pickerChoice = $("cue-text").value.trim() ? { kind: "custom" } : null; $("cue-add").disabled = !pickerChoice; }, on);
  $("cue-text").addEventListener("keydown", (event) => { if (event.key === "Enter") addFromPicker(); }, on);
  $("cue-listen").addEventListener("click", previewChoice, on);
  $("cue-add").addEventListener("click", addFromPicker, on);
  $("cs-close").addEventListener("click", () => selectCue(null), on);
  $("cs-delete").addEventListener("click", () => { if (selectedCue !== null) deleteCue(selectedCue); }, on);
  $("cs-listen").addEventListener("click", () => {
    const cue = cueList[selectedCue];
    if (!cue) return;
    if (cue.kind === "count") playPreview(cue.voice, cueLibrary?.numbers[voiceLang(cue.voice)].slice(0, Math.min(meter, 4)).join(" ") ?? "", "count");
    else playPreview(cue.voice, cue.text);
  }, on);
  $("cs-voice").addEventListener("change", () => {
    const cue = cueList[selectedCue];
    if (!cue) return;
    const voice = $("cs-voice").value;
    changeCues(() => {
      // A library phrase switches language with the voice; typed text stays as written.
      const item = cueLibrary?.categories.flatMap((c) => c.items).find((i) => i.en === cue.text || i.th === cue.text);
      if (item && cue.kind === "phrase") { cue.text = phraseFor(item, voice); cue.label = cue.text; }
      cue.voice = voice;
    });
    renderCueSelected();
  }, on);
  $("cs-time").addEventListener("change", () => {
    const cue = cueList[selectedCue];
    const value = Number($("cs-time").value);
    if (!cue || !Number.isFinite(value)) return;
    changeCues(() => { cue.time = round3(Math.max(0, Math.min(analysis.duration, value))); sortCues(cue.id); });
    renderCueSelected();
  }, on);
  for (const button of document.querySelectorAll("[data-cue-move]")) {
    button.addEventListener("click", () => {
      const cue = cueList[selectedCue];
      if (!cue) return;
      const move = button.dataset.cueMove;
      changeCues(() => {
        if (move === "beat" || move === "-beat") {
          const index = beatIndexNear(cue.time) + (move === "beat" ? 1 : -1);
          if (beats[index]) cue.time = beats[index].t;
        } else {
          cue.time = round3(Math.max(0, Math.min(analysis.duration, cue.time + Number(move))));
        }
        sortCues(cue.id);
      });
      renderCueSelected();
    }, on);
  }
  $("cue-panel").hidden = true;
  $("cue-selected").hidden = true;
  if (hasCues) ensureLibrary().then(() => { if (alive) voiceOptions($("cue-voice"), loadVoice() || "kanya"); }).catch(() => {});

  // ---- lead-in: bars of silence before the song so players can count in
  $("lead-field").hidden = !mixer.tracks.has(CLICK);
  $("lead-bars").value = String(leadBars);
  $("lead-bars").disabled = false;
  $("lead-bars").addEventListener("change", async () => {
    const select = $("lead-bars");
    const bars = Number(select.value);
    select.disabled = true;
    $("load-note").textContent = bars ? "กำลังเพิ่มช่วงนับก่อนเพลง…" : "กำลังเอาช่วงนับออก…";
    try {
      await flushGrid();
      await ensureLibrary().catch(() => null);
      const voices = cueLibrary?.voices.map((v) => v.id) ?? [];
      const preferred = loadVoice();
      const countVoice = voices.includes(preferred) ? preferred : voices[0] ?? null;
      const result = await api(`/api/jobs/${job.id}/lead-in`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bars, count_voice: countVoice }),
      });
      const message = bars
        ? `เพิ่มช่วงนับ ${bars} ห้อง (${result.seconds.toFixed(2)} วินาที)${result.added_count ? " พร้อมเสียงนับเข้า" : ""} และเปิดเมโทรนอมให้แล้ว`
        : "เอาช่วงนับก่อนเพลงออกแล้ว";
      await reopen([...mixer.tracks.keys()], (state) => {
        // Keep the playhead on the same musical moment, and turn the click on so the count-in is heard.
        state.position = Math.max(0, state.position - leadIn + result.seconds);
        state.scrollLeft = 0;
        if (bars && !leadBars && state.tracks[CLICK]) state.tracks[CLICK].mute = false;
      });
      toast(message);
    } catch (error) {
      if (!alive) return;
      select.value = String(leadBars);
      select.disabled = false;
      $("load-note").textContent = "";
      toast(error.message);
    }
  }, on);

  // ---- cutting a region out into its own track
  function renderSelectionBar() {
    $("sel-bar").hidden = !selection;
    if (!selection) return;
    const source = stems.find((s) => s.id === selection.stem);
    $("sel-source").textContent = source?.name ?? "";
    if (document.activeElement !== $("sel-start")) $("sel-start").value = selection.start.toFixed(2);
    if (document.activeElement !== $("sel-end")) $("sel-end").value = selection.end.toFixed(2);
    $("sel-length").textContent = `${(selection.end - selection.start).toFixed(2)} วินาที`;
    const targets = stems.filter((s) => s.custom && s.id !== selection.stem);
    const chosen = $("sel-target").value;
    $("sel-target").replaceChildren(el("option", { value: "" }, "แทร็กใหม่"), ...targets.map((t) => el("option", { value: t.id }, t.name)));
    $("sel-target").value = targets.some((t) => t.id === chosen) ? chosen : "";
    $("sel-name").hidden = $("sel-target").value !== "";
  }

  function snapSelection() {
    const nearest = (t) => beats.reduce((best, b) => (Math.abs(b.t - t) < Math.abs(best - t) ? b.t : best), t === 0 ? 0 : beats[0].t);
    const start = nearest(selection.start);
    const end = nearest(selection.end);
    if (end > start) {
      selection.start = start;
      selection.end = end;
    }
  }

  function readSelectionInputs() {
    if (!selection) return;
    const start = Number($("sel-start").value);
    const end = Number($("sel-end").value);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      selection.start = Math.max(0, start);
      selection.end = Math.min(analysis.duration, end);
      if (document.activeElement) $("sel-length").textContent = `${(selection.end - selection.start).toFixed(2)} วินาที`;
      drawBase();
    }
  }

  $("sel-start").addEventListener("change", readSelectionInputs, on);
  $("sel-end").addEventListener("change", readSelectionInputs, on);
  $("sel-snap").addEventListener("change", () => {
    if (selection && $("sel-snap").checked) { snapSelection(); renderSelectionBar(); drawBase(); }
  }, on);
  $("sel-target").addEventListener("change", renderSelectionBar, on);
  $("sel-cancel").addEventListener("click", () => { selection = null; renderSelectionBar(); drawBase(); }, on);
  $("sel-listen").addEventListener("click", async () => {
    if (!selection || $("btn-play").disabled) return;
    mixer.setLoop({ start: selection.start, end: selection.end });
    setLoopUi(true);
    drawBase();
    if (mixer.playing) await mixer.play(selection.start);
    else { await mixer.play(selection.start); setPlayingUi(true); }
  }, on);
  $("sel-move").addEventListener("click", async () => {
    if (!selection) return;
    const target = $("sel-target").value;
    const name = $("sel-name").value.trim();
    if (!target && !name) { $("sel-name").focus(); return toast("กรุณาตั้งชื่อแทร็กใหม่"); }
    const button = $("sel-move");
    button.disabled = true;
    $("load-note").textContent = "กำลังย้ายเสียง…";
    try {
      const result = await api(`/api/jobs/${job.id}/cuts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: selection.stem, start: selection.start, end: selection.end, name: target ? null : name, target: target || null }),
      });
      $("sel-name").value = "";
      selection = null;
      await reopen(result.changed);
    } catch (error) {
      $("load-note").textContent = "";
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  }, on);

  $("btn-undo-cut").hidden = !analysis.can_undo_cut;
  $("btn-undo-cut").addEventListener("click", async () => {
    $("load-note").textContent = "กำลังย้อนการตัด…";
    try {
      const result = await api(`/api/jobs/${job.id}/cuts/last`, { method: "DELETE" });
      await reopen(result.changed);
    } catch (error) {
      $("load-note").textContent = "";
      toast(error.message);
    }
  }, on);

  function beginRename(stem, label) {
    const input = el("input", { class: "strip-rename", value: stem.name, maxlength: "40", "aria-label": "ชื่อแทร็ก" });
    let finished = false;
    const finish = async (save) => {
      if (finished) return;
      finished = true;
      const name = input.value.trim();
      if (save && name && name !== stem.name) {
        try {
          await api(`/api/jobs/${job.id}/tracks/${stem.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
          return await reopen([]);
        } catch (error) { toast(error.message); }
      }
      input.replaceWith(label);
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") finish(true);
      else if (event.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
    label.replaceWith(input);
    input.focus();
    input.select();
  }

  function removeTrackButton(stem) {
    let armed = false;
    let timer;
    const button = el("button", {
      class: "strip-x", title: "ลบแทร็กนี้และคืนเสียงกลับแทร็กเดิม", "aria-label": `ลบแทร็ก ${stem.name} และคืนเสียงกลับแทร็กเดิม`,
      onclick: async () => {
        if (!armed) {
          armed = true;
          button.textContent = "ยืนยัน";
          button.classList.add("confirm");
          timer = setTimeout(() => { armed = false; button.textContent = "×"; button.classList.remove("confirm"); }, 3000);
          return;
        }
        clearTimeout(timer);
        $("load-note").textContent = "กำลังคืนเสียงกลับแทร็กเดิม…";
        try {
          const result = await api(`/api/jobs/${job.id}/tracks/${stem.id}`, { method: "DELETE" });
          await reopen(result.changed);
        } catch (error) {
          $("load-note").textContent = "";
          toast(error.message);
        }
      },
    }, "×");
    return button;
  }

  /** Rebuild the page after a cut, keeping audio that did not change and the current mix. */
  async function reopen(changed, adjust = null) {
    await flushGrid();
    const buffers = new Map();
    const tracks = {};
    for (const [id, track] of mixer.tracks) {
      tracks[id] = { mute: track.mute, solo: track.solo, slider: track.slider };
      if (track.buffer && !changed.includes(id)) buffers.set(id, track.buffer);
    }
    const state = {
      buffers, tracks, activePreset, zoomIndex, editMode,
      position: mixer.position(),
      playing: mixer.playing,
      scrollLeft: scrollOf(),
      scrollY: window.scrollY,
    };
    adjust?.(state);
    leaveView();
    leaveView = () => {};
    await showStudio(await api(`/api/jobs/${job.id}`), state);
  }

  function setZoom(index, centerTime = null) {
    const next = Math.max(0, Math.min(ZOOMS.length - 1, index));
    if (next === zoomIndex) return;
    const focus = centerTime ?? (selectedBeat !== null ? beats[selectedBeat].t : selection ? (selection.start + selection.end) / 2 : mixer.position());
    zoomIndex = next;
    syncZoomUi();
    layout();
    scroller.scrollLeft = Math.max(0, focus * view.pps - view.width / 2);
    drawBase();
    drawHead();
  }
  function syncZoomUi() {
    $("zoom-label").textContent = `${ZOOMS[zoomIndex]}×`;
    $("zoom-out").disabled = zoomIndex === 0;
    $("zoom-in").disabled = zoomIndex === ZOOMS.length - 1;
  }
  syncZoomUi();
  if (!keep) scroller.scrollLeft = 0;
  $("zoom-in").addEventListener("click", () => setZoom(zoomIndex + 1), on);
  $("zoom-out").addEventListener("click", () => setZoom(zoomIndex - 1), on);
  // Pinch on a trackpad, or Ctrl/Cmd + wheel, zooms around the pointer.
  let wheelBuildUp = 0;
  scroller.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    wheelBuildUp += event.deltaY;
    if (Math.abs(wheelBuildUp) < 24) return;
    const direction = wheelBuildUp < 0 ? 1 : -1;
    wheelBuildUp = 0;
    const rect = base.getBoundingClientRect();
    setZoom(zoomIndex + direction, (event.clientX - rect.left + scrollOf()) / view.pps);
  }, { signal: loader.signal, passive: false });
  $("btn-zip").onclick = holdUntilClickSaved;

  // A clicked button keeps focus, which would make Space re-trigger it instead of playing.
  document.addEventListener("pointerup", (event) => {
    const button = event.target.closest?.("button, a");
    if (button) button.blur();
  }, on);

  document.addEventListener("keydown", (event) => {
    const tag = event.target.tagName;
    if (tag === "INPUT" || tag === "BUTTON" || tag === "A" || tag === "SELECT") return;
    if (event.target.closest?.("dialog")) return;
    if (event.key === "Escape" && selection) { selection = null; renderSelectionBar(); drawBase(); return; }
    if (selectedCue !== null && (event.code === "Delete" || event.code === "Backspace")) { event.preventDefault(); deleteCue(selectedCue); return; }
    if (event.key === "Escape" && (selectedCue !== null || !$("cue-panel").hidden)) { selectCue(null); $("cue-panel").hidden = true; return; }
    if (editMode && selectedBeat !== null) {
      if (event.code === "ArrowLeft" || event.code === "ArrowRight") {
        event.preventDefault();
        nudgeSelected((event.code === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 10 : 1));
        return;
      }
      if (event.code === "Delete" || event.code === "Backspace") { event.preventDefault(); deleteBeat(selectedBeat); return; }
    }
    if (event.code === "Space") { event.preventDefault(); togglePlay(); }
    else if (event.code === "ArrowLeft") { event.preventDefault(); seekTo(mixer.position() - 5); }
    else if (event.code === "ArrowRight") { event.preventDefault(); seekTo(mixer.position() + 5); }
    else if (event.key === "l" || event.key === "L") toggleLoop();
    else if (event.key === "c" || event.key === "C") toggleClick();
  }, on);

  $("btn-add-playlist").addEventListener("click", async () => {
    await flushGrid();  // the setlist renders from the saved metronome and cues
    openAddToPlaylist(job, () => ({
      // Silent here (muted, or left out by a solo) means off in the setlist mix.
      muted: [...mixer.tracks.values()].filter((t) => mixer.effectiveGain(t) === 0).map((t) => t.id).sort(),
      pan: Object.fromEntries([...mixer.tracks.values()].filter((t) => t.pan !== "C").map((t) => [t.id, t.pan])),
    }));
  }, on);

  $("btn-export").addEventListener("click", async () => {
    const audible = [...mixer.tracks.values()].some((t) => mixer.effectiveGain(t) > 0);
    if (!audible) return toast("ทุกแทร็กถูกปิดเสียงอยู่ เปิดอย่างน้อยหนึ่งแทร็กก่อนส่งออก");
    const button = $("btn-export");
    button.disabled = true;
    button.textContent = "กำลังส่งออก…";
    try {
      await flushGrid();
      const blob = await mixer.exportMix();
      const url = URL.createObjectURL(blob);
      const link = el("a", { href: url, download: "mix.wav" });
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) {
      toast(`ส่งออกไม่สำเร็จ: ${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = "ส่งออกมิกซ์";
    }
  }, on);

  const resizer = new ResizeObserver(() => layout());
  resizer.observe(scroller);

  leaveView = () => {
    alive = false;
    clearTimeout(syncTimer);
    clearTimeout(cueTimer);
    previewAudio?.pause();
    loader.abort();
    resizer.disconnect();
    $("btn-zip").onclick = null;
    cancelAnimationFrame(frame);
    mixer.destroy();
    document.title = "Prism";
  };

  renderSelectionBar();
  syncMixer();
  layout();
  if (editMode) renderMetroPanel();
  if (keep) {
    mixer.offset = keep.position;
    scroller.scrollLeft = keep.scrollLeft;
    window.scrollTo(0, keep.scrollY);
  }
  updateReadouts(mixer.position());
  frame = requestAnimationFrame(tick);

  // ---- audio loading (the timeline is already usable while this runs)
  const note = $("load-note");
  let loaded = 0;
  note.textContent = `โหลดเสียง 0/${stems.length}`;
  try {
    await Promise.all(stems.map(async (stem) => {
      const reuse = keep?.buffers?.get(stem.id);
      if (reuse) {
        mixer.tracks.get(stem.id).buffer = reuse;
      } else {
        const response = await fetch(`/api/jobs/${job.id}/stems/${stem.id}.wav`, { signal: loader.signal, cache: "no-cache" });
        if (!response.ok) throw new Error(`โหลด ${stem.name} ไม่สำเร็จ`);
        const bytes = await response.arrayBuffer();
        mixer.tracks.get(stem.id).buffer = await mixer.ctx.decodeAudioData(bytes);
      }
      loaded += 1;
      if (alive) note.textContent = `โหลดเสียง ${loaded}/${stems.length}`;
    }));
    if (!alive) return;
    note.textContent = "";
    $("btn-play").disabled = false;
    $("btn-export").disabled = false;
    $("btn-add-playlist").disabled = false;
    drawBase();
    if (keep?.playing) {
      await mixer.play(keep.position);
      setPlayingUi(true);
    }
  } catch (error) {
    if (alive && error.name !== "AbortError") {
      note.textContent = "โหลดเสียงไม่สำเร็จ";
      toast(error.message);
    }
  }
}

function setPlayingUi(playing) {
  $("icon-play").toggleAttribute("hidden", playing);
  $("icon-pause").toggleAttribute("hidden", !playing);
  $("btn-play").setAttribute("aria-label", playing ? "หยุดชั่วคราว" : "เล่น");
}

function setLoopUi(on) {
  $("btn-loop").setAttribute("aria-pressed", on);
}

// ------------------------------------------------------------------ analysis panels

function stat(label, value, note, title) {
  return el("div", { class: "stat", title },
    el("div", { class: "stat-label" }, label),
    el("div", { class: "stat-value" }, value),
    el("div", { class: "stat-note" }, note));
}

function fmtSize(mb) {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

const orDash = (value, suffix = "") => (value === null || value === undefined ? "–" : `${value}${suffix}`);

/** What the job cost the machine. Hidden for songs separated before this was measured. */
function renderResources(job) {
  const r = job.resources;
  $("resources").hidden = !r;
  $("resource-strip").hidden = !r;
  if (!r) return;

  const speed = r.speed_vs_audio >= 1
    ? `เร็วกว่าความยาวเพลง ${r.speed_vs_audio.toFixed(1)} เท่า`
    : `ช้ากว่าความยาวเพลง ${(1 / r.speed_vs_audio).toFixed(1)} เท่า`;
  const strip = $("resource-strip");
  strip.replaceChildren();
  const part = (label, value) => el("span", {}, `${label} `, el("b", {}, value));
  strip.append(part("เสร็จใน", fmtTime(r.seconds)), part("CPU เฉลี่ย", `${r.cpu.avg_cores.toFixed(1)} คอร์`));
  if (r.ram.peak_gb !== null) strip.append(part("RAM สูงสุด", `${r.ram.peak_gb.toFixed(1)} GB`));
  if (r.gpu.memory_peak_gb !== null) strip.append(part("หน่วยความจำ GPU สูงสุด", `${r.gpu.memory_peak_gb.toFixed(1)} GB`));
  strip.append(part("ดิสก์", fmtSize(r.disk.job_total_mb)), el("em", {}, "ดูรายละเอียด"));

  const m = r.machine;
  const gpuCores = m.gpu_cores ? ` · GPU ${m.gpu_cores} คอร์` : "";
  $("res-machine").textContent = `${m.cpu} · CPU ${m.cpu_cores} คอร์${gpuCores} · RAM ${m.ram_gb} GB · ประมวลผล AI บน ${m.accelerator}`;

  const tile = (label, value, note) => el("div", { class: "res-tile" }, el("span", {}, label), el("strong", {}, value), el("small", {}, note));
  const gpu = r.gpu;
  const gpuValue = gpu.memory_peak_gb !== null ? `${gpu.memory_peak_gb.toFixed(1)} GB` : "ไม่ได้ใช้";
  const gpuNote = gpu.util_during_pct !== null
    ? `หน่วยความจำสูงสุด · ทั้งเครื่อง ${orDash(gpu.util_before_pct, "%")} ก่อนเริ่ม, ${gpu.util_during_pct}% ระหว่าง AI ทำงาน`
    : "หน่วยความจำสูงสุด";
  const swap = r.ram.swap_added_gb > 0.05 ? ` · ใช้ swap เพิ่ม ${r.ram.swap_added_gb.toFixed(1)} GB` : "";
  const tiles = [
    tile("เวลาทั้งหมด", fmtTime(r.seconds), `เพลงยาว ${fmtTime(r.audio_seconds)} · ${speed}`),
    tile("CPU", `${r.cpu.avg_cores.toFixed(1)} คอร์`, `เฉลี่ยจาก ${m.cpu_cores} คอร์ · เวลา CPU รวม ${fmtTime(r.cpu.seconds)}`),
    tile("RAM", r.ram.peak_gb === null ? "–" : `${r.ram.peak_gb.toFixed(1)} GB`, `สูงสุดของแอป · ทั้งเครื่องใช้สูงสุด ${r.ram.system_peak_pct}%${swap}`),
    tile("GPU", gpuValue, gpuNote),
    tile("ดิสก์", fmtSize(r.disk.job_total_mb), `stem ${fmtSize(r.disk.stems_mb)} · ไฟล์ต้นฉบับ ${fmtSize(r.disk.source_mb)} · โมเดลทั้งหมด ${fmtSize(r.disk.models_mb)}`),
    tile("ดาวน์โหลด", r.network.downloaded_mb > 0 ? fmtSize(r.network.downloaded_mb) : "ไม่มี", r.network.downloaded_mb > 0 ? "โมเดล AI ครั้งแรก จากนี้ไม่ต้องโหลดอีก" : "ใช้โมเดลที่มีอยู่แล้วในเครื่อง"),
  ];
  $("res-tiles").replaceChildren(...tiles);

  const longest = Math.max(...r.stages.map((s) => s.seconds), 0.1);
  const head = el("tr", {}, ...["ขั้นตอน", "ทำงานบน", "เวลา", "CPU (คอร์)", "RAM สูงสุด", "GPU ทั้งเครื่อง", "หน่วยความจำ GPU"].map((t) => el("th", { scope: "col" }, t)));
  const rows = r.stages.map((s) => el("tr", {},
    el("td", { class: "res-name" }, s.label),
    el("td", { class: "res-hw" }, s.hardware),
    el("td", { class: "res-time" }, el("div", { class: "res-bar" },
      el("div", {}, el("i", { style: `width:${(s.seconds / longest) * 100}%` })),
      el("span", {}, `${s.seconds.toFixed(1)} วิ`))),
    el("td", {}, orDash(s.cpu_cores)),
    el("td", {}, s.ram_peak_gb === null ? "–" : `${s.ram_peak_gb.toFixed(1)} GB`),
    el("td", {}, orDash(s.gpu_util_avg, "%")),
    el("td", {}, s.gpu_memory_peak_gb === null ? "–" : `${s.gpu_memory_peak_gb.toFixed(1)} GB`),
  ));
  $("res-table").replaceChildren(el("thead", {}, head), el("tbody", {}, ...rows));
  $("res-note").textContent = "CPU, RAM และหน่วยความจำ GPU วัดเฉพาะแอปนี้ ส่วน % GPU เป็นค่าของทั้งเครื่อง จึงรวมแอปอื่นที่เปิดอยู่ด้วย ให้เทียบกับค่าก่อนเริ่มงาน หน่วยความจำ GPU อยู่ใน RAM ก้อนเดียวกันบน Apple Silicon";
}

function tempoStat(a, stability, source) {
  const summary = a.tempo_summary;
  const sections = a.tempo_map ?? [];
  if (!summary || summary.changes === 0 || summary.main === null) {
    return stat("ความเร็ว (BPM)", (summary?.main ?? a.tempo.bpm).toFixed(1), `${stability} · ${source}`);
  }
  if (summary.unsteady) {
    return stat("ความเร็ว (BPM)", `${summary.main.toFixed(1)}`, `ไม่นิ่ง ${summary.low.toFixed(0)}–${summary.high.toFixed(0)} · ควรตรวจด้วยหู`);
  }
  const values = [...new Set(sections.filter((s) => s.kind === "steady").map((s) => s.bpm.toFixed(0)))];
  return stat("ความเร็ว (BPM)", values.length > 1 ? values.join(" · ") : summary.main.toFixed(1), `${summary.changes + 1} ช่วง · ${summary.low.toFixed(0)}–${summary.high.toFixed(0)} BPM`);
}

function renderStats(a) {
  const stability = !a.tempo.reliable ? "จับจังหวะได้ไม่ชัด" : a.tempo.stability > 0.85 ? "จังหวะนิ่ง" : "จังหวะแกว่งเล็กน้อย";
  const source = a.tempo.source === "drums" ? "วัดจากกลอง" : "วัดจากทั้งเพลง";
  const confidence = a.key.confidence >= 0.75 ? "มั่นใจสูง" : a.key.confidence >= 0.4 ? "มั่นใจปานกลาง" : "มั่นใจต่ำ";
  const alternative = a.key.confidence < 0.75 && a.key.alternatives[0] ? ` · หรือ ${a.key.alternatives[0]}` : "";
  const keyNote = `${a.key.camelot} · ${confidence}${alternative}`;
  const unique = new Set(a.chords.filter((c) => c.label !== "N").map((c) => c.label)).size;
  $("stats").replaceChildren(
    tempoStat(a, stability, source),
    stat("คีย์", a.key.name, keyNote, keyNote),
    stat("อัตราจังหวะ", a.meter, "ประมาณจากการเน้นจังหวะ"),
    stat("ความดัง (LUFS)", a.loudness.lufs === null ? "–" : a.loudness.lufs.toFixed(1), `พีค ${a.loudness.peak_db.toFixed(1)} dB`),
    stat("โครงสร้าง", fmtTime(a.duration), `${a.sections.length} ท่อน · ${unique} คอร์ด`),
  );
}

function renderInsights(a) {
  const list = $("insights");
  list.replaceChildren();
  for (const item of a.insights) list.append(el("dt", {}, item.topic), el("dd", {}, item.text));
}

function renderMatrix(a, stems, onSelect) {
  const matrix = $("matrix");
  matrix.replaceChildren();
  const columns = a.sections.map((s) => `minmax(26px, ${Math.max(0.1, s.end - s.start).toFixed(2)}fr)`).join(" ");
  matrix.style.gridTemplateColumns = `max-content ${columns}`;
  matrix.append(el("div", { class: "m-corner" }));
  const heads = new Map();
  for (const section of a.sections) {
    const button = el("button", {
      class: "m-head", title: `${section.label} · ${fmtTime(section.start)}–${fmtTime(section.end)} · ${section.bars} ห้อง`,
      style: `background:${sectionColor(section)}`, onclick: () => onSelect(section),
    }, section.label);
    heads.set(section, button);
    matrix.append(button);
  }
  for (const stem of stems) {
    matrix.append(el("div", { class: "m-label" }, stem.name));
    for (const section of a.sections) {
      const level = section.stems[stem.id] ?? 0;
      const fill = el("i", { style: `--stem:${STEM_COLORS[stem.id]};opacity:${level < 0.05 ? 0 : 0.15 + level * 0.85}` });
      matrix.append(el("div", { class: "m-cell", title: `${stem.name} ใน ${section.label}: เล่น ${Math.round(level * 100)}% ของท่อน` }, fill));
    }
  }
  return heads;
}

function renderLoops(a) {
  const box = $("loops");
  box.replaceChildren();
  if (!a.loops.length) box.append(el("p", { class: "empty" }, "ไม่พบลูปคอร์ดที่วนซ้ำชัดเจน"));
  for (const loop of a.loops) {
    box.append(el("div", { class: "loop" },
      el("div", { class: "loop-chords" }, ...loop.chords.map((chord, i) =>
        el("div", { class: "loop-chip" }, el("strong", {}, chord), el("span", {}, loop.romans[i])))),
      el("div", { class: "loop-count" }, `พบ ${loop.count} ครั้ง`)));
  }
  const totals = new Map();
  let played = 0;
  for (const chord of a.chords) {
    if (chord.label === "N") continue;
    const length = chord.end - chord.start;
    totals.set(chord.label, (totals.get(chord.label) ?? 0) + length);
    played += length;
  }
  const ranked = [...totals.entries()].sort((x, y) => y[1] - x[1]).slice(0, 6);
  const top = ranked[0]?.[1] ?? 1;
  $("chord-usage").replaceChildren(...ranked.map(([label, seconds]) =>
    el("div", { class: "usage" }, el("b", {}, label),
      el("div", { class: "usage-track" }, el("i", { style: `width:${(seconds / top) * 100}%` })),
      el("span", {}, `${Math.round((seconds / played) * 100)}%`))));
}

function renderStemTable(stems) {
  const visible = stems.filter((s) => s.energy_share > 0);
  $("share-bar").replaceChildren(...visible.map((s) =>
    el("i", { style: `--stem:${STEM_COLORS[s.id]};flex:${s.energy_share}`, title: `${s.name} ${s.energy_share}%` })));
  const head = el("div", { class: "stem-row head" },
    el("div", {}, "แทร็ก"), el("div", { class: "num" }, "พลังงาน"), el("div", { class: "num" }, "สเตอริโอ"),
    el("div", { class: "col-bands" }, "เสียงต่ำ · กลาง · สูง"), el("div", { class: "num" }, "เริ่มที่"));
  const rows = stems.map((s) => el("div", { class: "stem-row", style: `--stem:${STEM_COLORS[s.id]}` },
    el("div", { class: "name", title: s.name }, s.name),
    el("div", { class: "num" }, `${Math.round(s.energy_share)}%`),
    el("div", { class: "num", title: "ความกว้างในสเตอริโอ" }, `${Math.round(s.width_pct)}%`),
    el("div", { class: "bands col-bands", title: `ต่ำ ${s.bands.low}% · กลาง ${s.bands.mid}% · สูง ${s.bands.high}%` },
      el("i", { style: `width:${s.bands.low}%` }), el("i", { style: `width:${s.bands.mid}%` }), el("i", { style: `width:${s.bands.high}%` })),
    el("div", { class: "num" }, s.first_entry === null ? "–" : fmtTime(s.first_entry))));
  $("stem-table").replaceChildren(head, ...rows);
}

// ------------------------------------------------------------------ boot

async function boot() {
  setupUpload();
  // A button, not a link: this app's routing owns the URL hash.
  $("resource-strip").addEventListener("click", () => $("resources").scrollIntoView());
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => { if (!$("view-home").hidden) drawHeroLanes(); });
  try {
    system = await api("/api/system");
    $("device-pill").textContent = system.device_label;
    renderModels();
  } catch (error) {
    toast(`เชื่อมต่อเซิร์ฟเวอร์ไม่ได้: ${error.message}`);
    return;
  }
  route();
}

boot();

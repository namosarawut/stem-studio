"use strict";
// Setlists: ordered songs with their own start and end points, played back to back.
// Uses app.js helpers: $, el, api, toast, show, leaveView, fmtTime.

const PL_ANTI_CLICK = 0.008;     // shortest fade at any cut, so a start or end never clicks
const PL_PREV_RESTART = 3;       // "previous" restarts the song when this far into it
const PL_KEEP_BUFFERS = 3;       // decoded songs kept in memory
const PAN_ORDER = ["L", "C", "R"];
const PAN_TEXT = { L: "ซ้าย", C: "กลาง", R: "ขวา" };

const pl = {
  lists: [],
  shown: null,          // the playlist on screen
  playing: null,        // the playlist the transport is driving (may differ from shown)
  open: null,           // id of the item whose details are expanded
  jobs: new Map(),      // finished projects, by id
  imports: new Map(),   // imported files, by id
  saveTimer: null,
  frame: 0,
  events: null,
  pickerTab: "projects",
};

// ------------------------------------------------------------------ small helpers

function plTime(seconds, decimals = 1) {
  if (!Number.isFinite(seconds)) return "–";
  const sign = seconds < 0 ? "-" : "";
  const total = Math.abs(seconds);
  const minutes = Math.floor(total / 60);
  const rest = (total - minutes * 60).toFixed(decimals).padStart(decimals ? 3 + decimals : 2, "0");
  return `${sign}${minutes}:${rest}`;
}

/** "1:23.5", "83.5" or "1:02:03" to seconds; null when it is not a time. */
function plParseTime(text) {
  const parts = String(text).trim().split(":");
  if (!parts[0] || parts.length > 3 || parts.some((p) => p === "" || !/^\d*\.?\d*$/.test(p))) return null;
  return parts.reduce((total, part) => total * 60 + Number(part), 0);
}

const plNewId = () => Array.from(crypto.getRandomValues(new Uint8Array(5)), (b) => b.toString(16).padStart(2, "0")).join("");

function plItemIndex(list, id) {
  return list ? list.items.findIndex((item) => item.id === id) : -1;
}

// ------------------------------------------------------------------ audio sources

const plInfo = new Map();       // source key -> Promise<{url, duration, peaks}>
const plProjects = new Map();   // job id -> Promise<{stems, sections}>

// ---- tracks turned on or off from show mode, for the whole setlist (kept per setlist in
// this browser). They sit on top of each song's own mix: "off" mutes the track in every song,
// "on" plays it even where the song's mix has it off (a click for one show, say).
const plShowMixes = new Map();   // list id -> {stem: "on" | "off"}

function plShowMix(list) {
  if (!list) return {};
  if (!plShowMixes.has(list.id)) {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(`prism-show-mix:${list.id}`) || "{}") || {}; } catch { /* none saved */ }
    plShowMixes.set(list.id, Object.fromEntries(Object.entries(saved).filter(([, v]) => v === "on" || v === "off")));
  }
  return plShowMixes.get(list.id);
}

function plSetShowMix(list, mix) {
  plShowMixes.set(list.id, mix);
  try {
    if (Object.keys(mix).length) localStorage.setItem(`prism-show-mix:${list.id}`, JSON.stringify(mix));
    else localStorage.removeItem(`prism-show-mix:${list.id}`);
  } catch { /* storage blocked: lasts while the page is open */ }
}

function plListOf(item) {
  return [pl.playing, pl.shown].find((list) => list?.items.some((i) => i.id === item.id)) ?? null;
}

/** The mix a project song really plays with: its own, plus the show-mode switches. */
function plEffectiveMix(item) {
  const mix = item.mix ?? { muted: [], pan: {} };
  const over = plShowMix(plListOf(item));
  if (!Object.keys(over).length) return mix;
  const muted = new Set(mix.muted);
  for (const [stem, state] of Object.entries(over)) {
    if (state === "off") muted.add(stem);
    else muted.delete(stem);
  }
  return { ...mix, muted: [...muted].sort() };
}

function plSourceKey(item) {
  return item.kind === "file" ? `file:${item.ref}` : `project:${item.ref}:${JSON.stringify(plEffectiveMix(item))}`;
}

function plKnown(item) {
  // Settled info for an item, without starting any work.
  return plInfo.get(plSourceKey(item))?.settled ?? null;
}

function plSource(item) {
  const key = plSourceKey(item);
  if (!plInfo.has(key)) {
    const promise = (item.kind === "file"
      ? api(`/api/library/${item.ref}`).then((meta) => ({ url: `/api/library/${item.ref}/audio.wav`, duration: meta.duration, peaks: meta.peaks }))
      : api(`/api/jobs/${item.ref}/playback`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(plEffectiveMix(item)),
      })).then((info) => { promise.settled = info; return info; });
    promise.catch(() => plInfo.delete(key));  // let a later try start over
    plInfo.set(key, promise);
  }
  return plInfo.get(key);
}

function plProject(jobId) {
  if (!plProjects.has(jobId)) {
    const promise = api(`/api/jobs/${jobId}/analysis`).then((analysis) => ({
      stems: analysis.stems.map((s) => ({ id: s.id, name: s.name, guide: Boolean(s.guide) })),
      sections: analysis.sections ?? [],
    }));
    promise.catch(() => plProjects.delete(jobId));
    plProjects.set(jobId, promise);
  }
  return plProjects.get(jobId);
}

/** Start and end inside the song, given what is known of its length. */
function plRange(item, duration = plKnown(item)?.duration ?? null) {
  const limit = duration ?? Infinity;
  const start = Math.min(item.start ?? 0, Number.isFinite(limit) ? Math.max(0, limit - 0.1) : Infinity);
  const end = Math.min(item.end ?? limit, limit);
  return { start, end };
}

function plMissing(item) {
  return item.kind === "project" ? !pl.jobs.has(item.ref) : !pl.imports.has(item.ref);
}

/** Mix a project starts with: what the studio last had for left/right, click off like there. */
const PL_GAIN_MIN = -30;
const PL_GAIN_MAX = 6;
const plDb = (db) => (Math.abs(db) < 0.05 ? "0 dB" : `${db > 0 ? "+" : "−"}${Math.abs(db).toFixed(1)} dB`);

function plDefaultMix(jobId) {
  let pan = {};
  try { pan = JSON.parse(localStorage.getItem(`stem-studio:pan:${jobId}`) || "{}"); } catch { /* none saved */ }
  return { muted: ["click"], pan };
}

// ------------------------------------------------------------------ player

const plPlayer = {
  ctx: null,
  buffers: new Map(),   // url -> Promise<AudioBuffer>, most recent last
  token: 0,
  now: null,            // { itemId, source, gain, when, from, end, endAt }
  next: null,
  playing: false,
  cue: null,            // { itemId, pos } where play resumes; pos null = the item's start
  waiting: false,       // a song ended and the next is still loading

  context() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100, latencyHint: "playback" });
      // Every output of the sound card, each channel kept apart: a mix rendered with the
      // click on Out 3 plays it on Out 3, and never mixed down into Out 1-2 (the PA).
      this.outputs = useAllOutputs(this.ctx);
    }
    return this.ctx;
  },

  /** Outputs of the sound card (2 on a laptop, more with an audio interface). */
  outputCount() {
    this.context();
    return this.outputs;
  },

  buffer(url) {
    let promise = this.buffers.get(url);
    if (promise) {
      this.buffers.delete(url);   // move to most recent
    } else {
      promise = fetch(url).then((r) => {
        if (!r.ok) throw new Error(`โหลดเสียงไม่ได้ (${r.status})`);
        return r.arrayBuffer();
      }).then((data) => this.context().decodeAudioData(data));
      promise.catch(() => this.buffers.delete(url));
    }
    this.buffers.set(url, promise);
    while (this.buffers.size > PL_KEEP_BUFFERS) this.buffers.delete(this.buffers.keys().next().value);
    return promise;
  },

  schedule(item, buffer, when, from) {
    const ctx = this.context();
    const { start, end } = plRange(item, buffer.duration);
    const length = Math.max(0.01, end - from);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const gain = ctx.createGain();
    gain.channelInterpretation = "discrete";
    source.connect(gain).connect(ctx.destination);
    if (buffer.numberOfChannels > this.outputs && this.warned !== item.id) {
      this.warned = item.id;
      toast(`"${item.title}" ตั้งให้บางแทร็กออก Out ${this.outputs + 1} ขึ้นไป แต่การ์ดเสียงตอนนี้มี ${this.outputs} ช่อง แทร็กนั้นจะไม่มีเสียง`);
    }
    const endAt = when + length;
    // Fade in only from the item's own start; a resume mid-song just avoids a click.
    const fadeIn = Math.min(from <= start + 0.01 ? Math.max(item.fade_in || 0, PL_ANTI_CLICK) : PL_ANTI_CLICK, length / 2);
    const fadeOut = Math.max(item.fade_out || 0, PL_ANTI_CLICK);
    const inEnd = when + fadeIn;
    const outStart = endAt - fadeOut;
    gain.gain.setValueAtTime(0, when);
    if (outStart > inEnd) {
      gain.gain.linearRampToValueAtTime(1, inEnd);
      gain.gain.setValueAtTime(1, outStart);
    } else {
      // Starting inside the fade-out: come in at the level the fade has reached by then.
      gain.gain.linearRampToValueAtTime(Math.min(1, (endAt - inEnd) / fadeOut), inEnd);
    }
    gain.gain.linearRampToValueAtTime(0, endAt);
    source.start(when, from, length);
    return { itemId: item.id, source, gain, when, from, end, endAt };
  },

  drop(slot) {
    if (!slot) return;
    try { slot.source.stop(); } catch { /* already stopped */ }
    slot.source.disconnect();
    slot.gain.disconnect();
  },

  list() {
    return pl.playing ?? pl.shown;
  },

  item(id) {
    return this.list()?.items.find((item) => item.id === id) ?? null;
  },

  /** Play an item of the playing list from `pos` (song seconds; null = its start point;
   * a function = read once the audio is ready, to carry on from where the old audio got to).
   * The new song loads first, so whatever plays keeps going until it is ready. */
  async play(itemId, pos = null) {
    const list = this.list();
    const item = this.item(itemId);
    if (!list || !item) return;
    const token = ++this.token;
    if (typeof pos !== "function") this.cue = { itemId, pos };
    this.waiting = false;
    plRenderNow("loading");
    let buffer;
    try {
      if (plMissing(item)) throw new Error("ไม่พบต้นฉบับของเพลงนี้แล้ว");
      const info = await plSource(item);
      buffer = await this.buffer(info.url);
    } catch (error) {
      if (token !== this.token) return;
      toast(`${item.title}: ${error.message}`);
      // In a running set, skip the broken song rather than stopping the show.
      const after = list.items[plItemIndex(list, itemId) + 1];
      if (this.playing && list.auto_advance && after) return this.play(after.id);
      this.halt();
      return;
    }
    if (token !== this.token) return;
    const ctx = this.context();
    await ctx.resume();
    if (token !== this.token) return;
    const { start, end } = plRange(item, buffer.duration);
    let from = (typeof pos === "function" ? pos() : pos) ?? start;
    this.drop(this.now);
    this.drop(this.next);
    this.next = null;
    if (from >= end - 0.05 || from < 0) from = start;
    this.now = this.schedule(item, buffer, ctx.currentTime + 0.05, from);
    this.playing = true;
    this.cue = null;
    plRenderNow();
    this.prepareNext();
  },

  /** Load the following song and schedule it to start exactly when this one ends. */
  async prepareNext() {
    const list = this.list();
    const current = this.now && this.item(this.now.itemId);
    if (!list || !current || !list.auto_advance || current.stop_after) return;
    const nextItem = list.items[plItemIndex(list, current.id) + 1];
    if (!nextItem) return;
    const token = this.token;
    const nowSlot = this.now;
    try {
      if (plMissing(nextItem)) throw new Error("ไม่พบต้นฉบับ");
      const info = await plSource(nextItem);
      const buffer = await this.buffer(info.url);
      if (token !== this.token || this.now !== nowSlot || this.next || !this.playing) return;
      const ctx = this.context();
      const when = Math.max(nowSlot.endAt + (list.gap || 0), ctx.currentTime + 0.05);
      this.next = this.schedule(nextItem, buffer, when, plRange(nextItem, buffer.duration).start);
      this.waiting = false;
    } catch {
      // Tried again when this song ends (play() reports the error then).
    }
  },

  /** Rebuild what is queued after a change to the list (order, points, gap, mix). */
  replan(changedId = null) {
    if (!this.playing || !this.now) return;
    if (changedId && changedId === this.now.itemId) {
      const id = this.now.itemId;
      // Keep going from wherever the current audio is when the new one is ready.
      this.play(id, () => (this.now?.itemId === id ? this.position() : null));
      return;
    }
    this.drop(this.next);
    this.next = null;
    this.prepareNext();
  },

  position() {
    if (this.playing && this.now) return this.now.from + (this.context().currentTime - this.now.when);
    if (this.cue) {
      const item = this.item(this.cue.itemId);
      return this.cue.pos ?? (item ? plRange(item).start : 0);
    }
    return 0;
  },

  currentId() {
    return this.now?.itemId ?? this.cue?.itemId ?? null;
  },

  pause() {
    if (!this.playing || !this.now) return;
    const item = this.item(this.now.itemId);
    const pos = this.position();
    this.token++;
    this.drop(this.now);
    this.drop(this.next);
    this.cue = { itemId: this.now.itemId, pos: item ? Math.max(plRange(item).start, Math.min(pos, plRange(item).end)) : pos };
    this.now = this.next = null;
    this.playing = false;
    this.waiting = false;
    plRenderNow();
  },

  halt(cueId = null) {
    this.token++;
    this.drop(this.now);
    this.drop(this.next);
    const id = cueId ?? this.now?.itemId ?? this.cue?.itemId ?? null;
    this.now = this.next = null;
    this.playing = false;
    this.waiting = false;
    this.cue = id ? { itemId: id, pos: null } : null;
    plRenderNow();
  },

  toggle() {
    if (this.playing) return this.pause();
    if (!pl.playing) pl.playing = pl.shown;
    const list = this.list();
    if (!list?.items.length) return;
    const id = this.cue && this.item(this.cue.itemId) ? this.cue.itemId : (pl.open && this.item(pl.open) ? pl.open : list.items[0].id);
    this.play(id, this.cue?.itemId === id ? this.cue.pos : null);
  },

  step(direction) {
    const list = this.list();
    const id = this.currentId();
    if (!list || !id) return;
    const index = plItemIndex(list, id);
    const item = list.items[index];
    let target = index + direction;
    if (direction < 0 && item && this.position() - plRange(item).start > PL_PREV_RESTART) target = index;
    const nextItem = list.items[Math.max(0, Math.min(list.items.length - 1, target))];
    if (!nextItem) return;
    if (this.playing) this.play(nextItem.id);
    else { this.cue = { itemId: nextItem.id, pos: null }; plRenderNow(); }
  },

  seek(pos) {
    const id = this.currentId();
    if (!id) return;
    if (this.playing) this.play(id, pos);
    else { this.cue = { itemId: id, pos }; plRenderNow(); }
  },

  /** Called every frame: hand over to the next song, or stop at the end of the set. */
  tick() {
    if (!this.playing || !this.now) return;
    const time = this.context().currentTime;
    if (time < this.now.endAt) return;
    if (this.next) {
      const finished = this.now;
      this.now = this.next;
      this.next = null;
      setTimeout(() => this.drop(finished), 200);
      plRenderNow();
      this.prepareNext();
      return;
    }
    const list = this.list();
    const current = this.item(this.now.itemId);
    const after = list && current ? list.items[plItemIndex(list, current.id) + 1] : null;
    if (after && list.auto_advance && !current.stop_after) {
      // Next song not ready yet: start it as soon as it is.
      if (!this.waiting) {
        this.waiting = true;
        this.play(after.id);
      }
      return;
    }
    // Stop here; the next song (if any) waits at its start for the play button.
    this.halt(after ? after.id : current?.id);
  },
};

// ------------------------------------------------------------------ saving

function plSave(changedId = null, replan = true, list = pl.shown) {
  if (!list) return;
  if (replan && pl.playing === list) plPlayer.replan(changedId);
  pl.saveTimers ??= {};
  clearTimeout(pl.saveTimers[list.id]);
  pl.saveTimers[list.id] = setTimeout(async () => {
    try {
      const saved = await api(`/api/playlists/${list.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: list.name, auto_advance: list.auto_advance, gap: list.gap, items: list.items }),
      });
      list.updated_at = saved.updated_at;
      const entry = pl.lists.find((p) => p.id === list.id);
      if (entry) { entry.name = saved.name; entry.count = saved.items.length; }
      plRenderSide();
    } catch (error) {
      toast(`บันทึกไม่สำเร็จ: ${error.message}`);
    }
  }, 350);
}

// ------------------------------------------------------------------ view

async function showPlaylists(playlistId) {
  const already = !$("view-playlists").hidden;
  show("playlists", already);
  plBind();
  try {
    const [lists, jobs, imports] = await Promise.all([api("/api/playlists"), api("/api/jobs"), api("/api/library")]);
    pl.lists = lists;
    pl.jobs = new Map(jobs.filter((j) => j.status === "done").map((j) => [j.id, j]));
    pl.imports = new Map(imports.map((m) => [m.id, m]));
  } catch (error) {
    toast(error.message);
  }
  const wanted = playlistId ?? pl.shown?.id ?? pl.lists[0]?.id ?? null;
  if (wanted && !playlistId) {
    history.replaceState(null, "", `#/playlists/${wanted}`);
  }
  if (!wanted) {
    pl.shown = null;
  } else if (pl.playing?.id === wanted) {
    pl.shown = pl.playing;   // the same object, so edits reach the running set
  } else if (pl.shown?.id !== wanted) {
    try {
      pl.shown = await api(`/api/playlists/${wanted}`);
      pl.open = null;
    } catch (error) {
      toast(error.message);
      pl.shown = null;
      history.replaceState(null, "", "#/playlists");
    }
  }
  plRenderSide();
  plRenderEditor();
  plRenderNow();
  cancelAnimationFrame(pl.frame);
  const loop = () => {
    plPlayer.tick();
    plRenderClock();
    pl.frame = requestAnimationFrame(loop);
  };
  pl.frame = requestAnimationFrame(loop);
  leaveView = () => {
    // Moving between setlists keeps the music going; leaving the page stops it.
    if (location.hash.startsWith("#/playlists")) return;
    cancelAnimationFrame(pl.frame);
    plShowExit();
    plPlayer.halt();
    pl.events?.abort();
    pl.events = null;
    $("pl-picker").close();
  };
}

function plBind() {
  if (pl.events) return;
  pl.events = new AbortController();
  const on = { signal: pl.events.signal };
  $("pl-play").addEventListener("click", () => plPlayer.toggle(), on);
  $("pl-prev").addEventListener("click", () => plPlayer.step(-1), on);
  $("pl-next").addEventListener("click", () => plPlayer.step(1), on);
  $("pl-stop").addEventListener("click", () => plPlayer.halt(), on);
  $("pl-show").addEventListener("click", () => plShowEnter(), on);
  $("pl-create").addEventListener("click", plCreate, on);
  $("pl-create-2").addEventListener("click", plCreate, on);
  $("pl-name").addEventListener("input", () => { pl.shown.name = $("pl-name").value; plSave(null, false); }, on);
  $("pl-auto").addEventListener("change", () => { pl.shown.auto_advance = $("pl-auto").checked; plSave(); plRenderNow(); }, on);
  $("pl-gap").addEventListener("change", () => {
    const value = Math.min(60, Math.max(0, Number($("pl-gap").value) || 0));
    $("pl-gap").value = value;
    pl.shown.gap = value;
    plSave();
  }, on);
  $("pl-delete").addEventListener("click", plDelete, on);
  $("pl-file").addEventListener("change", () => { plImport([...$("pl-file").files]); $("pl-file").value = ""; }, on);
  $("pl-add-project").addEventListener("click", () => plOpenPicker(), on);
  $("pl-picker-close").addEventListener("click", () => $("pl-picker").close(), on);
  $("pl-picker").addEventListener("click", (event) => { if (event.target === $("pl-picker")) $("pl-picker").close(); }, on);
  $("pl-picker-search").addEventListener("input", plRenderPicker, on);
  $("pl-tab-projects").addEventListener("click", () => { pl.pickerTab = "projects"; plRenderPicker(); }, on);
  $("pl-tab-imports").addEventListener("click", () => { pl.pickerTab = "imports"; plRenderPicker(); }, on);
  $("pl-tab-lists").addEventListener("click", () => { pl.pickerTab = "lists"; plRenderPicker(); }, on);

  const progress = $("pl-progress");
  const seekAt = (event) => {
    const item = plPlayer.item(plPlayer.currentId() ?? "");
    if (!item) return;
    const { start, end } = plRange(item);
    if (!Number.isFinite(end)) return;
    const box = progress.getBoundingClientRect();
    plPlayer.seek(start + Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)) * (end - start));
  };
  progress.addEventListener("click", seekAt, on);
  progress.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    plPlayer.seek(Math.max(0, plPlayer.position() + (event.key === "ArrowLeft" ? -5 : 5)));
  }, on);

  document.addEventListener("keydown", (event) => {
    if ($("view-playlists").hidden || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest("input, textarea, select, dialog, [contenteditable]")) return;
    if (event.code === "Space" && !event.target.closest("button")) {
      event.preventDefault();
      plPlayer.toggle();
    }
  }, on);

  // Drop audio files anywhere on the list to import them.
  const main = $("pl-main");
  let depth = 0;
  const hasFiles = (event) => [...(event.dataTransfer?.types ?? [])].includes("Files");
  main.addEventListener("dragenter", (event) => { if (!hasFiles(event) || !pl.shown) return; depth++; $("pl-drop").hidden = false; }, on);
  main.addEventListener("dragleave", (event) => { if (!hasFiles(event)) return; depth = Math.max(0, depth - 1); if (!depth) $("pl-drop").hidden = true; }, on);
  main.addEventListener("dragover", (event) => { if (hasFiles(event) && pl.shown) event.preventDefault(); }, on);
  main.addEventListener("drop", (event) => {
    if (!hasFiles(event) || !pl.shown) return;
    event.preventDefault();
    depth = 0;
    $("pl-drop").hidden = true;
    plImport([...event.dataTransfer.files]);
  }, on);
  window.addEventListener("resize", () => { if (!$("view-playlists").hidden) plDrawOpenWave(); }, on);
}

async function plCreate() {
  try {
    const created = await api("/api/playlists", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: `เพลย์ลิสต์ ${pl.lists.length + 1}` }),
    });
    pl.lists.unshift({ id: created.id, name: created.name, count: 0, updated_at: created.updated_at });
    pl.shown = created;
    pl.open = null;
    location.hash = `#/playlists/${created.id}`;
    requestAnimationFrame(() => { $("pl-name").focus(); $("pl-name").select(); });
  } catch (error) {
    toast(error.message);
  }
}

let plDeleteArmed = null;
async function plDelete() {
  const list = pl.shown;
  if (!list) return;
  const button = $("pl-delete");
  if (plDeleteArmed !== list.id) {
    plDeleteArmed = list.id;
    button.textContent = "ยืนยันลบ";
    button.classList.add("confirm");
    setTimeout(() => { plDeleteArmed = null; button.textContent = "ลบเพลย์ลิสต์"; button.classList.remove("confirm"); }, 3000);
    return;
  }
  plDeleteArmed = null;
  try {
    await api(`/api/playlists/${list.id}`, { method: "DELETE" });
    if (pl.playing === list) { plPlayer.halt(); pl.playing = null; plPlayer.cue = null; }
    pl.lists = pl.lists.filter((p) => p.id !== list.id);
    pl.shown = null;
    toast(`ย้ายเพลย์ลิสต์ “${list.name}” ไปถังขยะแล้ว กู้คืนได้ภายใน 7 วันจากหน้าแรก`);
    location.hash = pl.lists[0] ? `#/playlists/${pl.lists[0].id}` : "#/playlists";
  } catch (error) {
    toast(error.message);
  }
}

// ------------------------------------------------------------------ rendering

function plRenderSide() {
  const box = $("pl-list");
  box.replaceChildren(...pl.lists.map((entry) => {
    const playingHere = pl.playing?.id === entry.id && (plPlayer.playing || plPlayer.cue);
    return el("li", {},
      el("a", { class: "pl-side-item", href: `#/playlists/${entry.id}`, "aria-current": pl.shown?.id === entry.id ? "page" : "false" },
        el("span", { class: "pl-side-name" }, entry.name),
        el("span", { class: "pl-side-meta" }, `${entry.count} เพลง${playingHere ? " · กำลังเล่น" : ""}`)));
  }));
  $("pl-list-empty").hidden = pl.lists.length > 0;
}

function plRenderEditor() {
  const list = pl.shown;
  $("pl-none").hidden = Boolean(list);
  $("pl-editor").hidden = !list;
  if (!list) return;
  if (document.activeElement !== $("pl-name")) $("pl-name").value = list.name;
  $("pl-auto").checked = list.auto_advance;
  $("pl-gap").value = list.gap ?? 0;
  plRenderItems();
}

function plSummary() {
  const list = pl.shown;
  let total = 0;
  let unknown = 0;
  for (const item of list.items) {
    // A project's mix is measured once it has been rendered; until then its song length
    // (without any lead-in) stands in, so the total is close instead of leaving it out.
    const known = plKnown(item)?.duration ?? (item.kind === "file" ? pl.imports.get(item.ref)?.duration : null) ?? null;
    const guess = known ?? (item.kind === "project" ? pl.jobs.get(item.ref)?.duration ?? null : null);
    const { start, end } = plRange(item, guess);
    if (Number.isFinite(end)) total += end - start;
    if (known == null) unknown++;
  }
  total += (list.gap || 0) * Math.max(0, list.items.length - 1);
  // Switches made in show mode act on every song: say so here, where the songs' own mixes are set.
  const names = { vocals: "เสียงร้อง", lead_vocals: "ร้องนำ", backing_vocals: "คอรัส", drums: "กลอง", bass: "เบส", guitar: "กีตาร์", piano: "เปียโน", other: "ดนตรีอื่น", click: "เมโทรนอม", cues: "cue" };
  const show = Object.entries(plShowMix(list)).map(([stem, state]) => `${state === "off" ? "ปิด" : "เปิด"}${names[stem] ?? stem}`);
  $("pl-summary").textContent = list.items.length
    ? `${list.items.length} เพลง · ${unknown ? "ประมาณ " : ""}${fmtTime(total)} นาที${show.length ? ` · โหมดโชว์: ${show.join(", ")} ทั้งเซ็ต` : ""}`
    : "";
}

function plRenderItems() {
  const list = pl.shown;
  const box = $("pl-items");
  const currentId = pl.playing === list ? plPlayer.currentId() : null;
  box.replaceChildren(...list.items.map((item, index) => plItemRow(item, index, item.id === currentId)));
  $("pl-items-empty").hidden = list.items.length > 0;
  plSummary();
  if (pl.open) plOpenDetails(pl.open);
}

function plItemRow(item, index, isCurrent) {
  const list = pl.shown;
  const missing = plMissing(item);
  const known = plKnown(item);
  const duration = known?.duration ?? (item.kind === "file" ? pl.imports.get(item.ref)?.duration : null) ?? null;
  const { start, end } = plRange(item, duration);
  const rangeText = `${plTime(start)} – ${item.end == null ? "จบเพลง" : plTime(end)}${Number.isFinite(end) ? ` · ${fmtTime(end - start)}` : ""}`;

  const handle = el("span", { class: "pl-handle", title: "ลากเพื่อเรียงลำดับ", "aria-hidden": "true" }, "⋮⋮");
  const playButton = el("button", { class: "pl-row-play", "aria-label": `เล่น ${item.title}`, title: "เล่นเพลงนี้" });
  playButton.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>';
  playButton.addEventListener("click", (event) => {
    event.stopPropagation();
    pl.playing = list;
    plPlayer.play(item.id);
  });
  const stopAfter = el("button", {
    class: "chip pl-stop-after", "aria-pressed": String(item.stop_after),
    title: "เมื่อเพลงนี้จบ ให้หยุดรอ ไม่เล่นเพลงถัดไปเอง",
  }, "หยุดหลังเพลงนี้");
  stopAfter.addEventListener("click", (event) => {
    event.stopPropagation();
    item.stop_after = !item.stop_after;
    stopAfter.setAttribute("aria-pressed", String(item.stop_after));
    plSave();
  });
  const move = (delta) => (event) => {
    event.stopPropagation();
    const to = index + delta;
    if (to < 0 || to >= list.items.length) return;
    list.items.splice(to, 0, list.items.splice(index, 1)[0]);
    plSave();
    plRenderItems();
  };
  const up = el("button", { class: "pl-mini", "aria-label": "เลื่อนขึ้น", title: "เลื่อนขึ้น", disabled: index === 0 ? "" : null }, "↑");
  up.addEventListener("click", move(-1));
  const down = el("button", { class: "pl-mini", "aria-label": "เลื่อนลง", title: "เลื่อนลง", disabled: index === list.items.length - 1 ? "" : null }, "↓");
  down.addEventListener("click", move(1));
  const remove = el("button", { class: "pl-mini pl-remove", "aria-label": `เอา ${item.title} ออก`, title: "เอาออกจากเพลย์ลิสต์" }, "×");
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    if (remove.dataset.armed !== "1") {
      remove.dataset.armed = "1";
      remove.textContent = "เอาออก?";
      remove.classList.add("confirm");
      setTimeout(() => { remove.dataset.armed = ""; remove.textContent = "×"; remove.classList.remove("confirm"); }, 2500);
      return;
    }
    if (pl.playing === list && plPlayer.currentId() === item.id) plPlayer.halt(list.items[index + 1]?.id ?? null);
    list.items.splice(index, 1);
    if (pl.open === item.id) pl.open = null;
    plSave();
    plRenderItems();
  });

  const head = el("div", { class: "pl-row" },
    handle,
    el("span", { class: "pl-num" }, String(index + 1)),
    playButton,
    el("span", { class: "pl-title-cell" },
      el("span", { class: "pl-title" }, item.title),
      el("span", { class: "pl-meta" },
        el("span", { class: `pl-kind ${item.kind}` }, item.kind === "project" ? "โปรเจกต์" : "ไฟล์"),
        missing ? el("span", { class: "pl-missing" }, "ไม่พบต้นฉบับ") : rangeText,
        item.fade_in || item.fade_out ? ` · เฟด ${item.fade_in || 0}/${item.fade_out || 0} วิ` : "")),
    stopAfter,
    el("span", { class: "pl-row-actions" }, up, down, remove),
    el("i", { class: "pl-row-progress" }),
  );
  head.addEventListener("click", () => {
    pl.open = pl.open === item.id ? null : item.id;
    plRenderItems();
  });

  const row = el("li", {
    class: `pl-item${isCurrent ? " current" : ""}${missing ? " missing" : ""}${pl.open === item.id ? " open" : ""}`,
    "data-id": item.id,
  }, head);
  if (index < list.items.length - 1 && !item.stop_after && list.auto_advance) {
    row.append(el("span", { class: "pl-link", "aria-hidden": "true" }));
  }

  // Drag to reorder, from the handle only (the rest of the row opens the details).
  // Rows get rebuilt in place, so find the live one rather than this closure's.
  handle.addEventListener("pointerdown", () => { const live = handle.closest(".pl-item"); if (live) live.draggable = true; });
  row.addEventListener("dragstart", (event) => {
    if (!row.draggable) { event.preventDefault(); return; }
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/x-pl-item", item.id);
    row.classList.add("dragging");
  });
  row.addEventListener("dragend", () => { row.draggable = false; row.classList.remove("dragging"); plClearDropMarks(); });
  row.addEventListener("dragover", (event) => {
    if (![...event.dataTransfer.types].includes("text/x-pl-item")) return;
    event.preventDefault();
    const box = row.getBoundingClientRect();
    plClearDropMarks();
    row.classList.add(event.clientY < box.top + box.height / 2 ? "drop-before" : "drop-after");
  });
  row.addEventListener("drop", (event) => {
    const id = event.dataTransfer.getData("text/x-pl-item");
    if (!id) return;
    event.preventDefault();
    event.stopPropagation();
    const before = row.classList.contains("drop-before");
    plClearDropMarks();
    const from = plItemIndex(list, id);
    if (from < 0 || id === item.id) return;
    const [moved] = list.items.splice(from, 1);
    let to = plItemIndex(list, item.id) + (before ? 0 : 1);
    list.items.splice(to, 0, moved);
    plSave();
    plRenderItems();
  });
  return row;
}

function plClearDropMarks() {
  document.querySelectorAll(".pl-item.drop-before, .pl-item.drop-after").forEach((n) => n.classList.remove("drop-before", "drop-after"));
}

// ------------------------------------------------------------------ item details

let plWave = null;   // { itemId, canvas, peaks, duration, sections, drag }

function plOpenDetails(itemId) {
  const list = pl.shown;
  const item = list.items.find((i) => i.id === itemId);
  const row = document.querySelector(`.pl-item[data-id="${itemId}"]`);
  if (!item || !row) return;
  const detail = el("div", { class: "pl-detail" });
  row.append(detail);

  const title = el("input", { class: "edit-input pl-title-input", value: item.title, maxlength: "120", "aria-label": "ชื่อที่แสดง" });
  title.addEventListener("input", () => {
    item.title = title.value;
    row.querySelector(".pl-title").textContent = item.title || "เพลง";
    plSave(null, false);
    plRenderNow();
  });

  const canvas = el("canvas", { class: "pl-wave", "aria-label": "รูปคลื่น ลากเส้นเพื่อตั้งจุดเริ่มและจุดจบ คลิกเพื่อฟังจากตรงนั้น" });
  const waveNote = el("div", { class: "pl-wave-note" }, item.kind === "project" ? "กำลังเตรียมเสียงของโปรเจกต์…" : "กำลังโหลดรูปคลื่น…");
  const startInput = el("input", { class: "edit-input pl-time-input", value: plTime(item.start ?? 0, 2), "aria-label": "จุดเริ่ม" });
  const endInput = el("input", { class: "edit-input pl-time-input", value: item.end == null ? "" : plTime(item.end, 2), placeholder: "จบเพลง", "aria-label": "จุดจบ" });

  const commitPoint = (which) => {
    const input = which === "start" ? startInput : endInput;
    const raw = input.value.trim();
    if (which === "end" && raw === "") { item.end = null; return plPointsChanged(item); }
    const value = plParseTime(raw);
    if (value === null) { toast("พิมพ์เวลาเป็น นาที:วินาที เช่น 1:23.5"); return plRefreshPoints(item); }
    plSetPoint(item, which, value);
  };
  startInput.addEventListener("change", () => commitPoint("start"));
  endInput.addEventListener("change", () => commitPoint("end"));

  const hereButton = (which, label) => {
    const button = el("button", { class: "chip", title: "ใช้ตำแหน่งที่กำลังเล่นอยู่ของเพลงนี้" }, label);
    button.addEventListener("click", () => {
      if (plPlayer.currentId() !== item.id) return toast("กดเล่นเพลงนี้ก่อน แล้วค่อยกดตั้งจุดตรงตำแหน่งที่ได้ยิน");
      plSetPoint(item, which, plPlayer.position());
    });
    return button;
  };
  const listen = (label, title, from) => {
    const button = el("button", { class: "chip", title }, label);
    button.addEventListener("click", () => { pl.playing = list; plPlayer.play(item.id, from()); });
    return button;
  };
  const clearEnd = el("button", { class: "chip", title: "เล่นไปจนจบไฟล์" }, "เล่นถึงท้ายเพลง");
  clearEnd.addEventListener("click", () => { item.end = null; plPointsChanged(item); });

  const fade = (key, label) => {
    const input = el("input", { class: "edit-input", type: "number", min: "0", max: "30", step: "0.5", value: item[key] || 0, "aria-label": label });
    input.addEventListener("change", () => {
      item[key] = Math.min(30, Math.max(0, Number(input.value) || 0));
      input.value = item[key];
      plSave(item.id);
      plRefreshRow(item);
    });
    return el("label", { class: "pl-field" }, label, input, "วิ");
  };

  const sectionPick = (which) => {
    const select = el("select", { class: "edit-input", "aria-label": which === "start" ? "เริ่มที่ท่อน" : "จบที่ท่อน" }, el("option", { value: "" }, which === "start" ? "เริ่มที่ท่อน…" : "จบเมื่อท่อนนี้จบ…"));
    select.hidden = true;
    select.addEventListener("change", () => {
      const section = plWave?.sections?.[Number(select.value)];
      if (section) plSetPoint(item, which, which === "start" ? section.start : section.end);
      select.value = "";
    });
    return select;
  };
  const startSection = sectionPick("start");
  const endSection = sectionPick("end");

  detail.append(
    el("div", { class: "pl-detail-row" }, el("label", { class: "pl-field grow" }, "ชื่อที่แสดง", title)),
    el("div", { class: "pl-wave-box" }, canvas, waveNote),
    el("div", { class: "pl-detail-row" },
      el("label", { class: "pl-field" }, "เริ่ม", startInput), hereButton("start", "ตั้งเริ่มตรงที่เล่นอยู่"), startSection,
      el("span", { class: "pl-sep" }),
      el("label", { class: "pl-field" }, "จบ", endInput), hereButton("end", "ตั้งจบตรงที่เล่นอยู่"), endSection, clearEnd),
    el("div", { class: "pl-detail-row" },
      listen("ฟังจากจุดเริ่ม", "เล่นจากจุดเริ่มของเพลงนี้", () => null),
      listen("ฟัง 5 วิสุดท้าย", "เล่นช่วงท้ายก่อนถึงจุดจบ", () => { const { start, end } = plRange(item); return Number.isFinite(end) ? Math.max(start, end - 5) : null; }),
      el("span", { class: "pl-sep" }),
      fade("fade_in", "เฟดเข้า"), fade("fade_out", "เฟดออก")),
  );

  if (item.kind === "project") detail.append(plMixEditor(item));

  plWave = { itemId: item.id, canvas, peaks: null, duration: null, sections: [], drag: null };
  plWireWave(item, canvas);
  plSource(item).then((info) => {
    if (plWave?.itemId !== item.id) return;
    plWave.peaks = info.peaks;
    plWave.duration = info.duration;
    waveNote.hidden = true;
    plRefreshRow(item);
    plSummary();
    plDrawOpenWave();
  }).catch((error) => { waveNote.textContent = `โหลดไม่ได้: ${error.message}`; });
  if (item.kind === "project" && !plMissing(item)) {
    plProject(item.ref).then((project) => {
      if (plWave?.itemId !== item.id) return;
      plWave.sections = project.sections;
      for (const select of [startSection, endSection]) {
        select.append(...project.sections.map((s, i) => el("option", { value: String(i) }, `${s.label} · ${plTime(select === startSection ? s.start : s.end)}`)));
        select.hidden = project.sections.length === 0;
      }
      plDrawOpenWave();
    }).catch(() => { /* sections are a convenience */ });
  }
  plDrawOpenWave();

  function plRefreshPoints(target) {
    startInput.value = plTime(target.start ?? 0, 2);
    endInput.value = target.end == null ? "" : plTime(target.end, 2);
  }
  plWave.refresh = () => plRefreshPoints(item);
}

function plSetPoint(item, which, value) {
  const duration = plKnown(item)?.duration ?? Infinity;
  value = Math.max(0, Math.min(value, duration));
  if (which === "start") {
    const end = item.end ?? duration;
    if (value > end - 0.1) return toast("จุดเริ่มต้องอยู่ก่อนจุดจบ");
    item.start = Math.round(value * 1000) / 1000;
  } else {
    if (value < (item.start ?? 0) + 0.1) return toast("จุดจบต้องอยู่หลังจุดเริ่ม");
    item.end = value >= duration - 0.001 ? null : Math.round(value * 1000) / 1000;
  }
  plPointsChanged(item);
}

function plPointsChanged(item) {
  plSave(item.id);
  plRefreshRow(item);
  plSummary();
  if (plWave?.itemId === item.id) { plWave.refresh?.(); plDrawOpenWave(); }
}

/** Update one row's text in place, keeping its open details. */
function plRefreshRow(item) {
  const row = document.querySelector(`.pl-item[data-id="${item.id}"]`);
  if (!row) return;
  const index = plItemIndex(pl.shown, item.id);
  const fresh = plItemRow(item, index, row.classList.contains("current"));
  row.querySelector(".pl-row").replaceWith(fresh.querySelector(".pl-row"));
}

function plMixEditor(item) {
  const outputs = plPlayer.outputCount();
  const mode = routeMode(outputs);
  const box = el("div", { class: "pl-mix" });
  // Switching left/centre/right <-> outputs rebuilds this editor; the mix itself stays.
  box.append(el("div", { class: "pl-mix-title" },
    el("span", {}, mode === "out" ? `แทร็กที่เล่น และช่องที่ออก (การ์ดเสียง ${outputs} ช่อง)` : "แทร็กที่เล่น และลำโพงซ้าย-ขวา"),
    routeModeSwitch(mode, () => box.replaceWith(plMixEditor(item)))));
  const grid = el("div", { class: "pl-mix-grid" }, el("span", { class: "pl-wave-note static" }, "กำลังโหลดรายชื่อแทร็ก…"));
  const quick = el("div", { class: "pl-detail-row" });
  box.append(grid, quick);
  if (plMissing(item)) { grid.replaceChildren(el("span", { class: "pl-missing" }, "ไม่พบโปรเจกต์นี้แล้ว")); return box; }
  item.mix = item.mix ?? { muted: [], pan: {} };
  const changed = () => {
    plSave(item.id);
    // A new mix means a new render: redraw the waveform from it.
    if (plWave?.itemId === item.id) {
      plSource(item).then((info) => { if (plWave?.itemId === item.id) { plWave.peaks = info.peaks; plWave.duration = info.duration; plDrawOpenWave(); } }).catch(() => {});
    }
  };
  plProject(item.ref).then(({ stems }) => {
    const rows = [];
    const paint = () => rows.forEach((r) => r());
    grid.replaceChildren(...stems.map((stem) => {
      const on = el("input", { type: "checkbox", "aria-label": `เปิด ${stem.name}` });
      const pick = (out) => {
        if (out === "C") delete item.mix.pan[stem.id]; else item.mix.pan[stem.id] = out;
        paint();
        changed();
      };
      const pans = mode === "out" ? [] : PAN_ORDER.map((pan) => {
        const button = el("button", { class: "pan-btn", "aria-label": `${stem.name} ออก${PAN_TEXT[pan]}` }, pan);
        button.addEventListener("click", () => pick(pan));
        return button;
      });
      // An audio interface with more outputs: pick any of them (Out 3-4, Out 3 ...).
      const outSelect = mode === "out" ? el("select", { class: "edit-input out-select", "aria-label": `${stem.name} ออกช่องไหน` },
        ...outChoices(outputs).map((out) => el("option", { value: out }, outLabel(out)))) : null;
      outSelect?.addEventListener("change", () => pick(outSelect.value));
      on.addEventListener("change", () => {
        const muted = new Set(item.mix.muted);
        if (on.checked) muted.delete(stem.id); else muted.add(stem.id);
        item.mix.muted = [...muted].sort();
        paint();
        changed();
      });
      // Level in dB, saved with the song in this setlist. The server renders the mix, so a
      // new value takes effect when the slider is let go, not while it moves.
      const level = el("input", {
        type: "range", class: "pl-level", min: String(PL_GAIN_MIN), max: String(PL_GAIN_MAX), step: "0.5",
        "aria-label": `ความดัง ${stem.name}`, title: "ลากเพื่อปรับความดัง ดับเบิลคลิกเพื่อกลับเป็น 0 dB",
      });
      const readout = el("span", { class: "pl-level-value" });
      const setGain = (db) => {
        item.mix.gain = { ...(item.mix.gain ?? {}) };
        if (Math.abs(db) < 0.05) delete item.mix.gain[stem.id]; else item.mix.gain[stem.id] = db;
        if (!Object.keys(item.mix.gain).length) delete item.mix.gain;
        paint();
        changed();
      };
      level.addEventListener("input", () => { readout.textContent = plDb(Number(level.value)); });
      level.addEventListener("change", () => setGain(Number(level.value)));
      level.addEventListener("dblclick", () => setGain(0));
      rows.push(() => {
        on.checked = !item.mix.muted.includes(stem.id);
        const pan = item.mix.pan[stem.id] ?? "C";
        pans.forEach((b, i) => b.setAttribute("aria-pressed", String(PAN_ORDER[i] === pan)));
        if (outSelect) {
          if (![...outSelect.options].some((o) => o.value === pan)) outSelect.append(el("option", { value: pan }, `${outLabel(pan)} (ไม่มีในการ์ดนี้)`));
          outSelect.value = pan;
        }
        const missing = outChannels(pan).some((c) => c >= outputs);
        row.classList.toggle("missing-out", missing);
        row.title = missing ? `ตั้งไว้ออก ${outName(pan)} แต่การ์ดเสียงตอนนี้มี ${outputs} ช่อง แทร็กนี้จะไม่มีเสียง` : "";
        const db = item.mix.gain?.[stem.id] ?? 0;
        level.value = String(db);
        readout.textContent = plDb(db);
        level.disabled = !on.checked;
      });
      const row = el("div", { class: `pl-mix-row${stem.guide ? " guide" : ""}` },
        el("label", { class: "pl-check" }, on, stem.name),
        el("span", { class: "pl-level-box" }, level, readout),
        outSelect ?? el("span", { class: "pan" }, ...pans));
      return row;
    }));
    paint();
    const guides = stems.filter((s) => s.guide).map((s) => s.id);
    const others = stems.filter((s) => !s.guide).map((s) => s.id);
    const keys = ["piano", "other"].filter((id) => others.includes(id));
    const keysLabel = keys.length === 2 ? "เฉพาะเปียโน + ดนตรีอื่นๆ" : keys[0] === "piano" ? "เฉพาะเปียโน" : "เฉพาะดนตรีอื่นๆ";
    const preset = (label, apply) => {
      const button = el("button", { class: "chip" }, label);
      button.addEventListener("click", () => { apply(); paint(); changed(); });
      return button;
    };
    quick.replaceChildren(...[
      preset("เปิดทุกแทร็ก", () => { item.mix.muted = []; }),
      preset("ปิดเสียงร้อง", () => { item.mix.muted = [...new Set([...item.mix.muted, ...stems.filter((s) => /vocal/.test(s.id)).map((s) => s.id)])].sort(); }),
      keys.length ? preset(keysLabel, () => {
        // Music: only piano and other. Click and cues stay as they are, they are for the player.
        const music = stems.filter((s) => !s.guide).map((s) => s.id);
        const kept = item.mix.muted.filter((id) => !music.includes(id));
        item.mix.muted = [...new Set([...kept, ...music.filter((id) => !keys.includes(id))])].sort();
      }) : null,
      preset("ความดังเท่าเดิมทุกแทร็ก", () => { delete item.mix.gain; }),
      ...(mode === "out" ? [
        guides.length && outputs >= 4 ? preset("คิว+เมโทรนอม Out 3-4 · ดนตรี Out 1-2", () => { item.mix.pan = Object.fromEntries(guides.map((id) => [id, "3-4"])); }) : null,
        guides.length && outputs >= 4 ? preset("คิว+เมโทรนอม Out 1-2 · ดนตรี Out 3-4", () => { item.mix.pan = Object.fromEntries(others.map((id) => [id, "3-4"])); }) : null,
        guides.length && outputs >= 3 ? preset("คิว+เมโทรนอม Out 3 · ดนตรี Out 1-2", () => { item.mix.pan = Object.fromEntries(guides.map((id) => [id, "3"])); }) : null,
        preset("ทุกแทร็ก Out 1-2", () => { item.mix.pan = {}; }),
      ] : [
        guides.length ? preset("คิว+เมโทรนอม ซ้าย · ดนตรี ขวา", () => { item.mix.pan = Object.fromEntries([...guides.map((id) => [id, "L"]), ...others.map((id) => [id, "R"])]); }) : null,
        guides.length ? preset("คิว+เมโทรนอม ขวา · ดนตรี ซ้าย", () => { item.mix.pan = Object.fromEntries([...guides.map((id) => [id, "R"]), ...others.map((id) => [id, "L"])]); }) : null,
        preset("ทุกแทร็กตรงกลาง", () => { item.mix.pan = {}; }),
      ]),
    ].filter(Boolean));
  }).catch((error) => grid.replaceChildren(el("span", { class: "pl-missing" }, error.message)));
  return box;
}

function plWireWave(item, canvas) {
  const timeAt = (event) => {
    const box = canvas.getBoundingClientRect();
    return Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)) * (plWave?.duration ?? 0);
  };
  const handleNear = (event) => {
    if (!plWave?.duration) return null;
    const box = canvas.getBoundingClientRect();
    const { start, end } = plRange(item, plWave.duration);
    const x = event.clientX - box.left;
    const toX = (t) => (t / plWave.duration) * box.width;
    if (Math.abs(x - toX(start)) <= 8) return "start";
    if (Math.abs(x - toX(end)) <= 8) return "end";
    return null;
  };
  canvas.addEventListener("pointermove", (event) => {
    if (plWave?.drag) {
      const t = timeAt(event);
      const { start, end } = plRange(item, plWave.duration);
      if (plWave.drag === "start") item.start = Math.round(Math.min(t, end - 0.1) * 1000) / 1000;
      else item.end = Math.round(Math.max(t, start + 0.1) * 1000) / 1000;
      plWave.refresh?.();
      plDrawOpenWave();
      return;
    }
    canvas.style.cursor = handleNear(event) ? "ew-resize" : "pointer";
  });
  canvas.addEventListener("pointerdown", (event) => {
    if (!plWave?.duration) return;
    const near = handleNear(event);
    if (near) {
      plWave.drag = near;
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    // Click: listen from here (inside or outside the kept part).
    pl.playing = pl.shown;
    plPlayer.play(item.id, timeAt(event));
  });
  const finish = () => {
    if (!plWave?.drag) return;
    const which = plWave.drag;
    plWave.drag = null;
    if (which === "end" && item.end != null && item.end >= plWave.duration - 0.01) item.end = null;
    plPointsChanged(item);
  };
  canvas.addEventListener("pointerup", finish);
  canvas.addEventListener("pointercancel", finish);
}

function plDrawOpenWave() {
  if (!plWave) return;
  const { canvas } = plWave;
  if (!canvas.isConnected) { plWave = null; return; }
  const item = pl.shown?.items.find((i) => i.id === plWave.itemId);
  if (!item) return;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width) return;
  if (canvas.width !== Math.round(width * ratio)) { canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio); }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const duration = plWave.duration;
  if (!duration || !plWave.peaks) return;
  const toX = (t) => (t / duration) * width;
  const { start, end } = plRange(item, duration);

  // Sections along the top.
  ctx.font = `500 11px ${color("--sans")}`;
  ctx.textBaseline = "top";
  for (const section of plWave.sections) {
    const x = toX(section.start);
    ctx.fillStyle = color("--line-strong");
    ctx.fillRect(x, 0, 1, height);
    ctx.fillStyle = color("--faint");
    if (toX(section.end) - x > 40) ctx.fillText(section.label, x + 4, 3);
  }
  // Waveform: kept part bright, the rest dim.
  const peaks = plWave.peaks;
  const mid = height / 2 + 7;
  const amp = (height - 18) / 2;
  for (let x = 0; x < width; x++) {
    const a = Math.floor((x / width) * peaks.length);
    const b = Math.max(a + 1, Math.floor(((x + 1) / width) * peaks.length));
    let peak = 0;
    for (let i = a; i < b && i < peaks.length; i++) peak = Math.max(peak, peaks[i]);
    const h = Math.max(1, (peak / 255) * amp);
    const t = (x / width) * duration;
    ctx.fillStyle = t >= start && t <= end ? color("--muted") : color("--line-strong");
    ctx.fillRect(x, mid - h, 1, h * 2);
  }
  // Start and end handles.
  for (const [t, label] of [[start, "เริ่ม"], [end, "จบ"]]) {
    const x = Math.min(width - 2, toX(t));
    ctx.fillStyle = color("--bass");
    ctx.fillRect(x - 1, 0, 2, height);
    ctx.fillRect(label === "เริ่ม" ? x : x - 8, height - 14, 8, 14);
  }
  // Playhead when this song is the one playing.
  if (plPlayer.currentId() === item.id) {
    const pos = plPlayer.position();
    if (pos >= 0 && pos <= duration) {
      ctx.fillStyle = color("--vocals");
      ctx.fillRect(toX(pos) - 1, 0, 2, height);
    }
  }
}

// ------------------------------------------------------------------ now playing

function plRenderNow(state = null) {
  const list = plPlayer.list();
  const id = plPlayer.currentId();
  const item = id ? plPlayer.item(id) : null;
  $("pl-icon-play").toggleAttribute("hidden", plPlayer.playing);  // SVG has no .hidden property
  $("pl-icon-pause").toggleAttribute("hidden", !plPlayer.playing);
  $("pl-play").setAttribute("aria-label", plPlayer.playing ? "หยุดชั่วคราว" : "เล่น");
  $("pl-play").disabled = !list?.items.length;
  if (!item || !list) {
    $("pl-now-title").textContent = pl.shown ? pl.shown.name : "เพลย์ลิสต์";
    $("pl-now-sub").textContent = pl.shown?.items.length ? "กดเล่นเพื่อเริ่มจากเพลงแรก" : "เลือกหรือสร้างเพลย์ลิสต์ แล้วกดเล่น";
  } else {
    const index = plItemIndex(list, item.id);
    const after = list.items[index + 1];
    $("pl-now-title").textContent = item.title;
    let sub = `${list.name} · เพลงที่ ${index + 1}/${list.items.length}`;
    if (state === "loading") sub += " · กำลังโหลด…";
    else if (after && list.auto_advance && !item.stop_after) sub += ` · ต่อไป: ${after.title}`;
    else if (after) sub += ` · จบแล้วหยุดรอ (ต่อไป: ${after.title})`;
    else sub += " · เพลงสุดท้าย";
    $("pl-now-sub").textContent = sub;
  }
  if (pl.shown && !$("view-playlists").hidden) {
    const currentId = pl.playing === pl.shown ? id : null;
    document.querySelectorAll(".pl-item").forEach((row) => row.classList.toggle("current", row.dataset.id === currentId));
  }
  plRenderSide();
  plShowRender(true);
}

function plRenderClock() {
  const id = plPlayer.currentId();
  const item = id ? plPlayer.item(id) : null;
  const fill = $("pl-progress-fill");
  if (!item) {
    $("pl-time").textContent = "0:00";
    $("pl-total").textContent = "0:00";
    fill.style.width = "0%";
    return;
  }
  const { start, end } = plRange(item);
  const pos = plPlayer.position();
  const length = Number.isFinite(end) ? end - start : null;
  const elapsed = pos - start;
  $("pl-time").textContent = elapsed < -0.05 && plPlayer.playing ? `เริ่มใน ${Math.ceil(-elapsed)}` : fmtTime(Math.max(0, elapsed));
  $("pl-total").textContent = length === null ? "–" : fmtTime(length);
  const share = length ? Math.min(1, Math.max(0, elapsed / length)) : 0;
  fill.style.width = `${share * 100}%`;
  $("pl-progress").setAttribute("aria-valuenow", String(Math.round(share * 100)));
  if (pl.playing === pl.shown) {
    const row = document.querySelector(`.pl-item[data-id="${item.id}"] .pl-row-progress`);
    if (row) row.style.width = `${share * 100}%`;
  }
  if (plWave?.itemId === item.id) plDrawOpenWave();
  plShowRender();
}

// ------------------------------------------------------------------ adding songs

async function plImport(files) {
  const list = pl.shown;
  if (!list || !files.length) return;
  let added = 0;
  for (const [i, file] of files.entries()) {
    toast(`กำลังนำเข้า ${i + 1}/${files.length}: ${file.name}`);
    const form = new FormData();
    form.append("file", file);
    try {
      const meta = await api("/api/library", { method: "POST", body: form });
      pl.imports.set(meta.id, meta);
      const item = { id: plNewId(), kind: "file", ref: meta.id, title: meta.title, start: 0, end: null, stop_after: false, fade_in: 0, fade_out: 0 };
      const info = Promise.resolve({ url: `/api/library/${meta.id}/audio.wav`, duration: meta.duration, peaks: meta.peaks });
      info.settled = { url: `/api/library/${meta.id}/audio.wav`, duration: meta.duration, peaks: meta.peaks };
      plInfo.set(plSourceKey(item), info);
      list.items.push(item);
      added++;
      plSave();
      if (pl.shown === list) plRenderItems();
    } catch (error) {
      toast(`${file.name}: ${error.message}`);
    }
  }
  if (added) toast(`เพิ่ม ${added} เพลงจากเครื่องแล้ว`);
}

/** Add songs to `list` (the setlist open on the page, or the one playing in show mode). */
function plOpenPicker(list = pl.shown) {
  if (!list) return;
  pl.pickerList = list;
  pl.otherLists = new Map();  // fetched again each time: other setlists may have changed
  $("pl-picker-search").value = "";
  plRenderPicker();
  $("pl-picker").showModal();
  $("pl-picker-search").focus();
  // Refresh in the background: a song may have finished separating meanwhile.
  Promise.all([api("/api/jobs"), api("/api/library")]).then(([jobs, imports]) => {
    pl.jobs = new Map(jobs.filter((j) => j.status === "done").map((j) => [j.id, j]));
    pl.imports = new Map(imports.map((m) => [m.id, m]));
    if ($("pl-picker").open) plRenderPicker();
  }).catch(() => {});
}

/** Copy of a setlist item for another setlist: same song, points and mix, a new id. */
function plCopyItem(item) {
  return { ...structuredClone(item), id: plNewId() };
}

function plAddToList(list, items) {
  list.items.push(...items);
  plSave(null, true, list);
  if (list === pl.shown) plRenderItems();
  plShowRender(true);
}

function plRenderPicker() {
  const list = pl.pickerList ?? pl.shown;
  if (!list) return;
  const tab = pl.pickerTab ?? "projects";
  $("pl-tab-projects").setAttribute("aria-pressed", String(tab === "projects"));
  $("pl-tab-imports").setAttribute("aria-pressed", String(tab === "imports"));
  $("pl-tab-lists").setAttribute("aria-pressed", String(tab === "lists"));
  const terms = $("pl-picker-search").value.normalize("NFC").toLowerCase().split(/\s+/).filter(Boolean);
  if (tab === "lists") return plRenderPickerLists(list, terms);
  const projects = tab === "projects";
  const source = projects ? [...pl.jobs.values()] : [...pl.imports.values()];
  const shown = source.filter((entry) => {
    const text = [entry.title, entry.key, entry.filename].filter(Boolean).join(" ").normalize("NFC").toLowerCase();
    return terms.every((t) => text.includes(t));
  });
  const counts = new Map();
  for (const item of list.items) counts.set(`${item.kind}:${item.ref}`, (counts.get(`${item.kind}:${item.ref}`) ?? 0) + 1);
  const kind = projects ? "project" : "file";
  $("pl-picker-list").replaceChildren(...shown.map((entry) => {
    const inList = counts.get(`${kind}:${entry.id}`) ?? 0;
    const add = el("button", { class: "btn" }, inList ? `เพิ่มอีก (มี ${inList})` : "เพิ่ม");
    add.addEventListener("click", () => {
      const item = { id: plNewId(), kind, ref: entry.id, title: entry.title, start: 0, end: null, stop_after: false, fade_in: 0, fade_out: 0 };
      if (projects) item.mix = plDefaultMix(entry.id);
      plAddToList(list, [item]);
      plRenderPicker();
      toast(`เพิ่ม "${entry.title}" แล้ว`);
    });
    const meta = projects
      ? [fmtTime(entry.duration), entry.bpm ? `${Math.round(entry.bpm)} BPM` : null, entry.key].filter(Boolean).join(" · ")
      : [fmtTime(entry.duration), entry.filename].filter(Boolean).join(" · ");
    const actions = [add];
    if (!projects) actions.unshift(plImportDeleteButton(entry));
    return el("li", { class: "pl-pick" }, el("span", { class: "pl-pick-text" }, el("span", { class: "pl-title" }, entry.title), el("span", { class: "pl-meta" }, meta)), ...actions);
  }));
  $("pl-picker-note").textContent = shown.length
    ? (projects ? "เพลงจากโปรเจกต์เล่นเป็นมิกซ์ของแทร็ก (ปิดเมโทรนอมไว้ก่อน) ปรับแทร็กและลำโพงได้ในรายละเอียดของเพลง" : "ไฟล์ที่นำเข้าไว้ใช้ซ้ำในหลายเพลย์ลิสต์ได้")
    : terms.length ? "ไม่พบเพลงที่ตรงกับคำค้น" : projects ? "ยังไม่มีโปรเจกต์ที่แยกเสร็จ" : "ยังไม่มีไฟล์ที่นำเข้า กด \"นำเข้าไฟล์จากเครื่อง\" ในหน้าเพลย์ลิสต์";
}

/** The "from other setlists" tab: every song of every other setlist, copied on add. */
function plRenderPickerLists(list, terms) {
  const others = pl.lists.filter((p) => p.id !== list.id);
  const missing = others.filter((p) => !pl.otherLists.has(p.id));
  if (missing.length) {
    for (const entry of missing) pl.otherLists.set(entry.id, null);
    Promise.all(missing.map((entry) => api(`/api/playlists/${entry.id}`).then((full) => pl.otherLists.set(entry.id, full)).catch(() => {})))
      .then(() => { if ($("pl-picker").open && pl.pickerTab === "lists") plRenderPicker(); });
  }
  const rows = [];
  for (const entry of others) {
    const full = pl.otherLists.get(entry.id);
    const items = (full?.items ?? []).filter((item) => terms.every((t) => item.title.normalize("NFC").toLowerCase().includes(t)));
    if (full && !items.length) continue;
    const all = el("button", { class: "btn" }, "เพิ่มทั้งหมด");
    all.disabled = !items.length;
    all.addEventListener("click", () => {
      plAddToList(list, items.map(plCopyItem));
      toast(`เพิ่ม ${items.length} เพลงจาก "${entry.name}" แล้ว`);
    });
    rows.push(el("li", { class: "pl-pick pl-pick-group" },
      el("span", { class: "pl-pick-text" }, el("span", { class: "pl-title" }, entry.name), el("span", { class: "pl-meta" }, full ? `${full.items.length} เพลง` : "กำลังโหลด…")),
      all));
    for (const item of items) {
      const add = el("button", { class: "btn" }, "เพิ่ม");
      add.addEventListener("click", () => { plAddToList(list, [plCopyItem(item)]); toast(`เพิ่ม "${item.title}" แล้ว`); });
      const meta = [item.kind === "project" ? "โปรเจกต์" : "ไฟล์", item.start > 0 || item.end !== null ? `${plTime(item.start)}–${item.end === null ? "จบเพลง" : plTime(item.end)}` : null].filter(Boolean).join(" · ");
      rows.push(el("li", { class: "pl-pick pl-pick-sub" }, el("span", { class: "pl-pick-text" }, el("span", { class: "pl-title" }, item.title), el("span", { class: "pl-meta" }, meta)), add));
    }
  }
  $("pl-picker-list").replaceChildren(...rows);
  $("pl-picker-note").textContent = !others.length ? "ยังไม่มีเพลย์ลิสต์อื่น"
    : rows.length ? "เพิ่มแล้วได้สำเนาของเพลงนั้น พร้อมจุดเริ่ม จุดจบ และมิกซ์เดิม แก้ในเพลย์ลิสต์นี้ไม่กระทบเพลย์ลิสต์ต้นทาง"
    : terms.length ? "ไม่พบเพลงที่ตรงกับคำค้น" : "เพลย์ลิสต์อื่นยังไม่มีเพลง";
}

function plImportDeleteButton(entry) {
  const button = el("button", { class: "lib-delete", title: "ย้ายไฟล์นี้ไปถังขยะ กู้คืนได้ภายใน 7 วันจากหน้าแรก (ระหว่างนั้นเพลย์ลิสต์ที่ใช้อยู่จะขึ้นว่าไม่พบต้นฉบับ)" }, "ลบไฟล์");
  let armed = false;
  button.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      button.textContent = "ยืนยันลบ";
      button.classList.add("confirm");
      setTimeout(() => { armed = false; button.textContent = "ลบไฟล์"; button.classList.remove("confirm"); }, 3000);
      return;
    }
    try {
      await api(`/api/library/${entry.id}`, { method: "DELETE" });
      pl.imports.delete(entry.id);
      plInfo.delete(`file:${entry.id}`);
      plRenderPicker();
      plRenderItems();
    } catch (error) {
      toast(error.message);
    }
  });
  return button;
}

// ------------------------------------------------------------------ add from the song page

let addPl = null;   // { job, mix: () => {muted, pan}, lists, added: Map(id -> count) }

/** Open the "add to setlist" dialog for a finished song. `mix` reads the studio's
 * current mute/solo and left/right state when the user confirms. */
async function openAddToPlaylist(job, mix) {
  const dialog = $("add-pl");
  addPl = { job, mix, lists: [], added: new Map() };
  if (!dialog.dataset.wired) {
    dialog.dataset.wired = "1";
    $("add-pl-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    $("add-pl-create").addEventListener("click", () => addPlCreate());
    $("add-pl-name").addEventListener("keydown", (event) => { if (event.key === "Enter") addPlCreate(); });
  }
  $("add-pl-name").value = "";
  $("add-pl-list").replaceChildren(el("li", { class: "pl-pick" }, el("span", { class: "pl-meta" }, "กำลังโหลด…")));
  dialog.showModal();
  try {
    addPl.lists = await api("/api/playlists");
  } catch (error) {
    toast(error.message);
  }
  addPlRender();
}

function addPlRender() {
  const box = $("add-pl-list");
  if (!addPl.lists.length) {
    box.replaceChildren(el("li", { class: "pl-pick" }, el("span", { class: "pl-meta" }, "ยังไม่มีเพลย์ลิสต์ พิมพ์ชื่อด้านล่างเพื่อสร้างใหม่")));
    $("add-pl-name").focus();
    return;
  }
  box.replaceChildren(...addPl.lists.map((entry) => {
    const done = addPl.added.get(entry.id);
    const button = el("button", { class: `btn${done ? " done" : ""}` }, done ? `เพิ่มแล้ว ✓${done > 1 ? ` ×${done}` : ""}` : "เพิ่ม");
    button.addEventListener("click", () => addPlAdd(entry.id, button));
    const open = el("a", { class: "lib-delete", href: `#/playlists/${entry.id}` }, "เปิด");
    open.addEventListener("click", () => $("add-pl").close());
    return el("li", { class: "pl-pick" },
      el("span", { class: "pl-pick-text" }, el("span", { class: "pl-title" }, entry.name), el("span", { class: "pl-meta" }, `${entry.count} เพลง`)),
      open, button);
  }));
}

async function addPlAdd(playlistId, button = null) {
  const { job } = addPl;
  const item = {
    id: plNewId(), kind: "project", ref: job.id, title: job.title, start: 0, end: null, stop_after: false, fade_in: 0, fade_out: 0,
    mix: $("add-pl-use-mix").checked ? addPl.mix() : plDefaultMix(job.id),
  };
  if (button) { button.disabled = true; button.textContent = "กำลังเพิ่ม…"; }
  try {
    // A setlist open in this tab (maybe playing) is the live copy: add to that one.
    const live = [pl.playing, pl.shown].find((list) => list?.id === playlistId);
    const list = live ?? await api(`/api/playlists/${playlistId}`);
    list.items.push(item);
    const saved = await api(`/api/playlists/${playlistId}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: list.name, auto_advance: list.auto_advance, gap: list.gap, items: list.items }),
    });
    if (live && pl.playing === live) plPlayer.replan();
    const entry = addPl.lists.find((p) => p.id === playlistId);
    if (entry) entry.count = saved.items.length;
    const summary = pl.lists.find((p) => p.id === playlistId);
    if (summary) summary.count = saved.items.length;
    addPl.added.set(playlistId, (addPl.added.get(playlistId) ?? 0) + 1);
    toast(`เพิ่ม "${job.title}" ลง ${saved.name} แล้ว (เพลงที่ ${saved.items.length})`);
  } catch (error) {
    toast(`เพิ่มไม่สำเร็จ: ${error.message}`);
  }
  addPlRender();
}

async function addPlCreate() {
  const name = $("add-pl-name").value.trim();
  try {
    const created = await api("/api/playlists", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name || `เพลย์ลิสต์ ${addPl.lists.length + 1}` }),
    });
    addPl.lists.unshift({ id: created.id, name: created.name, count: 0, updated_at: created.updated_at });
    pl.lists.unshift({ id: created.id, name: created.name, count: 0, updated_at: created.updated_at });
    $("add-pl-name").value = "";
    await addPlAdd(created.id);
  } catch (error) {
    toast(error.message);
  }
}

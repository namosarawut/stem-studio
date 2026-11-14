"use strict";

// ------------------------------------------------------------------ show mode
//
// A full-screen view of the setlist player for the stage: big now / next, time left, the
// section playing, large buttons, and control from a footswitch (most send PageDown /
// PageUp or arrow keys) or a MIDI pedal. The screen can be locked against stray taps, the
// Mac is kept awake, and leaving the page asks first. It drives plPlayer from playlist.js;
// nothing here plays audio itself.

const SHOW_MIDI_KEY = "prism-show-midi";            // {action: "note:60" | "cc:64" | "pc:3"}
const SHOW_ACTIONS = { toggle: "เล่น / หยุดชั่วคราว", next: "เพลงถัดไป", prev: "เพลงก่อนหน้า / ต้นเพลง" };
const SHOW_HOLD_MS = 1200;

const stage = {   // not `show`: app.js has show(view)
  active: false,
  locked: false,
  wake: null,
  midi: null,          // MIDIAccess
  learning: null,      // action waiting for a MIDI message
  map: {},
  events: null,
  sections: new Map(), // job id -> sections
  clock: 0,
};

function showLoadMap() {
  try { stage.map = JSON.parse(localStorage.getItem(SHOW_MIDI_KEY) || "{}") || {}; } catch { stage.map = {}; }
}

function showSaveMap() {
  try { localStorage.setItem(SHOW_MIDI_KEY, JSON.stringify(stage.map)); } catch { /* private window: lasts this session */ }
}

function showAct(action) {
  if (!stage.active) return;
  if (action === "toggle") plPlayer.toggle();
  else if (action === "next") plPlayer.step(1);
  else if (action === "prev") plPlayer.step(-1);
  else if (action === "stop") plPlayer.halt();
  showFlash(action);
}

function showFlash(action) {
  const target = { toggle: "show-play", next: "show-next", prev: "show-prev", stop: "show-stop" }[action];
  const button = target && $(target);
  if (!button) return;
  button.classList.add("hit");
  setTimeout(() => button.classList.remove("hit"), 180);
}

// ---- entering and leaving

async function plShowEnter() {
  if (stage.active) return;
  if (!pl.shown?.items.length) return toast("เพิ่มเพลงลงเพลย์ลิสต์ก่อน");
  if (!pl.playing) pl.playing = pl.shown;
  stage.active = true;
  stage.locked = false;
  showLoadMap();
  stage.events = new AbortController();
  const on = { signal: stage.events.signal };
  $("show").hidden = false;
  document.body.classList.add("show-on");
  showRenderLock();
  try { await document.documentElement.requestFullscreen?.({ navigationUI: "hide" }); } catch { /* not allowed: keep going windowed */ }
  await showKeepAwake();

  document.addEventListener("keydown", showKey, { ...on, capture: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) showKeepAwake(); }, on);
  $("show-play").addEventListener("click", () => showAct("toggle"), on);
  $("show-next").addEventListener("click", () => showAct("next"), on);
  $("show-prev").addEventListener("click", () => showAct("prev"), on);
  $("show-stop").addEventListener("click", () => showAct("stop"), on);
  $("show-midi-btn").addEventListener("click", showMidiPanel, on);
  $("show-list-toggle").addEventListener("click", showListToggle, on);
  $("show-add").addEventListener("click", () => plOpenPicker(plPlayer.list()), on);
  $("show-midi-close").addEventListener("click", () => { stage.learning = null; $("show-midi").hidden = true; }, on);
  // Lock and exit both need a press-and-hold once locked, so a stray tap does nothing.
  showHold($("show-lock"), () => { stage.locked = !stage.locked; showRenderLock(); }, () => stage.locked, on);
  showHold($("show-exit"), plShowExit, () => stage.locked, on);
  // A tap anywhere while locked only explains how to unlock.
  $("show").addEventListener("pointerdown", (event) => {
    if (!stage.locked || event.target.closest("#show-lock, #show-exit")) return;
    event.preventDefault();
    event.stopPropagation();
    showHint("จอล็อกอยู่ กดค้างที่ปุ่มปลดล็อก หรือใช้แป้นเหยียบ / คีย์บอร์ดได้ตามปกติ");
  }, { ...on, capture: true });
  // The click that follows a tap must not reach the buttons either.
  $("show").addEventListener("click", (event) => {
    if (!stage.locked || event.target.closest("#show-lock, #show-exit")) return;
    event.preventDefault();
    event.stopPropagation();
  }, { ...on, capture: true });
  if (Object.keys(stage.map).length) showMidiConnect();
  stage.clock = setInterval(() => { $("show-clock").textContent = new Date().toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" }); }, 1000);
  $("show-clock").textContent = new Date().toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
  plShowRender(true);
}

function plShowExit() {
  if (!stage.active) return;
  stage.active = false;
  stage.learning = null;
  stage.events?.abort();
  stage.events = null;
  clearInterval(stage.clock);
  $("show").hidden = true;
  $("show-midi").hidden = true;
  document.body.classList.remove("show-on");
  stage.wake?.release().catch(() => {});
  stage.wake = null;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

/** Hold-to-confirm when `needsHold()`; a plain click otherwise. */
function showHold(button, action, needsHold, on) {
  let timer = null;
  const start = (event) => {
    if (!needsHold()) return;
    event.preventDefault();
    button.classList.add("holding");
    timer = setTimeout(() => { timer = null; button.classList.remove("holding"); action(); }, SHOW_HOLD_MS);
  };
  const cancel = () => { if (timer) { clearTimeout(timer); timer = null; } button.classList.remove("holding"); };
  button.addEventListener("pointerdown", start, on);
  button.addEventListener("pointerup", cancel, on);
  button.addEventListener("pointerleave", cancel, on);
  button.addEventListener("click", () => { if (!needsHold()) action(); }, on);
}

async function showKeepAwake() {
  if (!stage.active || !("wakeLock" in navigator) || stage.wake) return;
  try {
    stage.wake = await navigator.wakeLock.request("screen");
    stage.wake.addEventListener("release", () => { stage.wake = null; });
  } catch { /* refused (battery saver, hidden tab): the next visibility change tries again */ }
}

function showRenderLock() {
  $("show").classList.toggle("locked", stage.locked);
  $("show-lock").textContent = stage.locked ? "กดค้างเพื่อปลดล็อก" : "ล็อกจอ";
  $("show-exit").textContent = stage.locked ? "กดค้างเพื่อออก" : "ออกจากโหมดโชว์ (Esc)";
}

let showHintTimer = 0;
function showHint(text) {
  $("show-hint").textContent = text;
  $("show-hint").hidden = false;
  clearTimeout(showHintTimer);
  showHintTimer = setTimeout(() => { $("show-hint").hidden = true; }, 3000);
}

// ---- keys: keyboard and footswitches

function showKey(event) {
  if (!stage.active || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.target.closest?.("input, textarea, select")) return;
  if (document.querySelector("dialog[open]")) return;  // the song picker handles its own keys (Esc closes it)
  const key = event.key;
  let action = null;
  if (key === " " || key === "Enter" || key === "MediaPlayPause") action = "toggle";
  else if (key === "ArrowRight" || key === "PageDown" || key === "ArrowDown" || key === "MediaTrackNext") action = "next";
  else if (key === "ArrowLeft" || key === "PageUp" || key === "ArrowUp" || key === "MediaTrackPrevious") action = "prev";
  else if (key === "Escape") {
    event.preventDefault();
    if (stage.learning) { stage.learning = null; showMidiPanel(); return; }
    if (stage.locked) return showHint("จอล็อกอยู่ กดค้างที่ปุ่ม \"กดค้างเพื่อออก\"");
    plShowExit();
    return;
  }
  if (!action) return;
  event.preventDefault();
  event.stopPropagation();
  if (!event.repeat) showAct(action);
}

// ---- MIDI pedals (Chrome and Edge; Safari has no Web MIDI)

function showMidiId(data) {
  const [status, a, b] = data;
  const type = status & 0xf0;
  if (type === 0x90 && b > 0) return `note:${a}`;
  if (type === 0xb0 && b >= 64) return `cc:${a}`;
  if (type === 0xc0) return `pc:${a}`;
  return null;
}

function showMidiName(id) {
  if (!id) return "ยังไม่ได้ตั้ง";
  const [kind, number] = id.split(":");
  return { note: `โน้ต ${number}`, cc: `CC ${number}`, pc: `Program ${number}` }[kind] ?? id;
}

async function showMidiConnect() {
  if (stage.midi) return true;
  if (!navigator.requestMIDIAccess) return false;
  try {
    stage.midi = await navigator.requestMIDIAccess();
  } catch {
    return false;
  }
  const listen = () => {
    for (const input of stage.midi.inputs.values()) input.onmidimessage = showMidiMessage;
    showMidiStatus();
  };
  stage.midi.onstatechange = listen;
  listen();
  return true;
}

function showMidiMessage(message) {
  const id = showMidiId(message.data);
  if (!id || !stage.active) return;
  if (stage.learning) {
    for (const action of Object.keys(stage.map)) if (stage.map[action] === id) delete stage.map[action];
    stage.map[stage.learning] = id;
    stage.learning = null;
    showSaveMap();
    showMidiPanel();
    return;
  }
  const action = Object.keys(stage.map).find((a) => stage.map[a] === id);
  if (action) showAct(action);
}

function showMidiStatus() {
  const inputs = stage.midi ? [...stage.midi.inputs.values()] : [];
  $("show-midi-status").textContent = !navigator.requestMIDIAccess
    ? "เบราว์เซอร์นี้ไม่รองรับ MIDI (ใช้ Chrome หรือ Edge) แป้นเหยียบที่ส่งเป็นปุ่มคีย์บอร์ดใช้ได้ตามปกติ"
    : inputs.length ? `เจออุปกรณ์ MIDI: ${inputs.map((i) => i.name).join(", ")}` : "ยังไม่เจออุปกรณ์ MIDI เสียบสายแล้วรอสักครู่";
}

async function showMidiPanel() {
  $("show-midi").hidden = false;
  const ok = await showMidiConnect();
  showMidiStatus();
  $("show-midi-list").replaceChildren(...Object.entries(SHOW_ACTIONS).map(([action, label]) => {
    const learning = stage.learning === action;
    const learn = el("button", { class: `btn${learning ? " btn-primary" : ""}` }, learning ? "เหยียบหรือกดปุ่มบนอุปกรณ์ MIDI…" : "ตั้งปุ่ม");
    learn.disabled = !ok;
    learn.addEventListener("click", () => { stage.learning = learning ? null : action; showMidiPanel(); });
    const clear = el("button", { class: "chip" }, "ล้าง");
    clear.hidden = !stage.map[action];
    clear.addEventListener("click", () => { delete stage.map[action]; showSaveMap(); showMidiPanel(); });
    return el("div", { class: "show-midi-row" }, el("span", {}, label), el("strong", {}, showMidiName(stage.map[action])), learn, clear);
  }));
}

// ---- drawing (called by playlist.js whenever the player changes, and every frame)

function showSectionAt(item, pos) {
  if (item.kind !== "project") return null;
  if (!stage.sections.has(item.ref)) {
    stage.sections.set(item.ref, null);
    plProject(item.ref).then((project) => { stage.sections.set(item.ref, project.sections ?? []); }).catch(() => {});
  }
  const sections = stage.sections.get(item.ref);
  if (!sections) return null;
  const now = sections.find((s) => pos >= s.start && pos < s.end);
  const index = now ? sections.indexOf(now) : -1;
  return { now: now?.label ?? null, next: index >= 0 ? sections[index + 1] ?? null : null };
}

// ---- the setlist beside the player: see every song, reorder, cue, add

function showListToggle() {
  const body = document.querySelector(".show-body");
  const wide = window.innerWidth > 1000;
  if (wide) body.classList.toggle("no-list");
  else body.classList.toggle("list-open");
  const visible = wide ? !body.classList.contains("no-list") : body.classList.contains("list-open");
  $("show-list-toggle").setAttribute("aria-pressed", String(visible));
}

function showMove(list, index, direction) {
  const to = index + direction;
  if (to < 0 || to >= list.items.length) return;
  const [moved] = list.items.splice(index, 1);
  list.items.splice(to, 0, moved);
  plSave(null, true, list);   // replans what plays next
  if (list === pl.shown) plRenderItems();
  showRenderList();
  plShowRender();
}

/** Tap a song: when stopped it becomes the song that plays next; while playing a second
 * tap within a few seconds is needed to jump there, so a stray tap cannot cut a song. */
function showPick(item) {
  if (!plPlayer.playing) {
    plPlayer.cue = { itemId: item.id, pos: null };
    plRenderNow();
    return;
  }
  if (stage.armed === item.id) {
    stage.armed = null;
    clearTimeout(stage.armedTimer);
    plPlayer.play(item.id);
    return;
  }
  stage.armed = item.id;
  clearTimeout(stage.armedTimer);
  stage.armedTimer = setTimeout(() => { stage.armed = null; showRenderList(); }, 3000);
  showHint(`แตะ "${item.title}" อีกครั้งเพื่อเล่นทันที`);
  showRenderList();
}

function showRenderList() {
  if (!stage.active) return;
  const list = plPlayer.list();
  const ol = $("show-songs");
  if (!list) { ol.replaceChildren(); return; }
  const currentId = plPlayer.currentId();
  $("show-list-count").textContent = `${list.items.length} เพลง`;
  ol.replaceChildren(...list.items.map((item, index) => {
    const known = plKnown(item);
    const { start, end } = plRange(item);
    const length = Number.isFinite(end) ? fmtTime(end - start) : known ? "" : "…";
    const status = item.id === currentId ? (plPlayer.playing ? "กำลังเล่น" : "พร้อมเล่น") : "";
    const meta = [length, status, item.stop_after ? "จบแล้วหยุดรอ" : null, plMissing(item) ? "ไม่พบต้นฉบับ" : null].filter(Boolean).join(" · ");
    const up = el("button", { "aria-label": `เลื่อน ${item.title} ขึ้น` }, "↑");
    const down = el("button", { "aria-label": `เลื่อน ${item.title} ลง` }, "↓");
    up.disabled = index === 0;
    down.disabled = index === list.items.length - 1;
    up.addEventListener("click", (event) => { event.stopPropagation(); showMove(list, index, -1); });
    down.addEventListener("click", (event) => { event.stopPropagation(); showMove(list, index, 1); });
    const row = el("li", {
      class: ["show-song", item.id === currentId ? (plPlayer.playing ? "now" : "cued") : "", stage.armed === item.id ? "armed" : "", plMissing(item) ? "missing" : ""].filter(Boolean).join(" "),
    },
    el("span", { class: "num" }, String(index + 1)),
    el("span", {}, el("span", { class: "name" }, item.title), el("span", { class: "meta" }, meta)),
    el("span", { class: "show-move" }, up, down));
    row.addEventListener("click", () => showPick(item));
    return row;
  }));
  ol.querySelector(".now, .cued")?.scrollIntoView({ block: "nearest" });
}

function plShowRender(full = false) {
  if (!stage.active) return;
  if (full) showRenderList();
  const list = plPlayer.list();
  const id = plPlayer.currentId();
  const item = id ? plPlayer.item(id) : list?.items[0] ?? null;
  $("show-set").textContent = list ? list.name : "";
  $("show-icon-play").toggleAttribute("hidden", plPlayer.playing);  // SVG has no .hidden property
  $("show-icon-pause").toggleAttribute("hidden", !plPlayer.playing);
  if (!item || !list) {
    $("show-title").textContent = "ไม่มีเพลง";
    return;
  }
  const index = plItemIndex(list, item.id);
  const after = list.items[index + 1];
  $("show-count").textContent = `เพลงที่ ${index + 1} / ${list.items.length}`;
  $("show-title").textContent = item.title;
  $("show-state").textContent = plPlayer.playing ? "กำลังเล่น" : plPlayer.waiting ? "กำลังโหลด…" : "พร้อมเล่น · กด Space หรือแป้นเหยียบ";
  $("show-state").classList.toggle("live", plPlayer.playing);
  $("show-next-title").textContent = after ? after.title : "จบเพลย์ลิสต์";
  $("show-next-how").textContent = !after ? "" : item.stop_after || !list.auto_advance ? "จบแล้วหยุดรอ" : "เล่นต่อทันที";

  const { start, end } = plRange(item);
  const pos = plPlayer.position();
  const elapsed = pos - start;
  const length = Number.isFinite(end) ? end - start : null;
  $("show-elapsed").textContent = elapsed < -0.05 && plPlayer.playing ? `เริ่มใน ${Math.ceil(-elapsed)}` : fmtTime(Math.max(0, elapsed));
  $("show-left").textContent = length === null ? "–" : `−${fmtTime(Math.max(0, length - Math.max(0, elapsed)))}`;
  $("show-bar").style.width = `${length ? Math.min(1, Math.max(0, elapsed / length)) * 100 : 0}%`;
  const section = showSectionAt(item, pos);
  $("show-section").textContent = section?.now ?? "";
  $("show-section-next").textContent = section?.next ? `ต่อไป ${section.next.label} ใน ${fmtTime(Math.max(0, section.next.start - pos))}` : "";
}

/** Leaving the page (closing the tab, reloading) during a set asks first. */
window.addEventListener("beforeunload", (event) => {
  if (stage.active || plPlayer.playing) {
    event.preventDefault();
    event.returnValue = "";
  }
});

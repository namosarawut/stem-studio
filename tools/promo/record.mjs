// Record real use of Prism (test server, test data) as frame sequences.
// Usage: node record.mjs <videoDir> [only-clip]
import { spawn, execFileSync } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync, readdirSync } from "node:fs";
import { join } from "node:path";

const DIR = process.argv[2];
const ONLY = process.argv[3] ?? null;
const FFMPEG = process.env.FFMPEG;
const TEST = "http://127.0.0.1:8766";
const JOB = "a322c2050f96";
const PLAYLIST = "23d743901476";
const DEMO = join(DIR, "เพลงตัวอย่าง.mp3");
const PORT = 9334;
const W = 1200, H = 900, DPR = 2;

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(DIR, ".profile")}`,
  "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required", "--mute-audio",
  "--no-first-run", "--no-default-browser-check", `--window-size=${W},${H}`, "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch { /* starting */ }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const waiting = new Map();
let onFrame = null;
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
  if (msg.method === "Page.screencastFrame") {
    send("Page.screencastFrameAck", { sessionId: msg.params.sessionId }).catch(() => {});
    onFrame?.(msg.params);
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  waiting.set(id, (msg) => (msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result)));
  ws.send(JSON.stringify({ id, method, params }));
});
const run = async (expression) => {
  const result = await send("Runtime.evaluate", { expression: `(async () => { ${expression} })()`, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
};
await send("Page.enable");
await send("Runtime.enable");
await send("DOM.enable");
await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: DPR, mobile: false });

const H_ = `
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 60000) => { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(100); } return false; };
  const byText = (sel, text) => [...document.querySelectorAll(sel)].find((n) => n.textContent.trim().startsWith(text));
  const scrollTo = (node, offset) => window.scrollTo(0, node.getBoundingClientRect().top + window.scrollY - offset);
  const center = (node) => { const r = node.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; };
`;
const STUDIO_READY = `${H_} await until(() => !document.getElementById('btn-play').disabled, 60000); await wait(400);`;

// Mouse helpers: a person-like move, click and drag.
let mouse = { x: W / 2, y: H / 2 };
async function moveTo(x, y, ms = 350) {
  const steps = Math.max(4, Math.round(ms / 16));
  const from = { ...mouse };
  for (let i = 1; i <= steps; i++) {
    const k = i / steps, e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
    mouse = { x: from.x + (x - from.x) * e, y: from.y + (y - from.y) * e };
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: mouse.x, y: mouse.y });
    await sleep(16);
  }
}
async function click(x, y, ms = 350) {
  await moveTo(x, y, ms);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await sleep(70);
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
}
async function drag(x1, y, x2, ms = 900) {
  await moveTo(x1, y, 350);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: x1, y, button: "left", buttons: 1, clickCount: 1 });
  const steps = Math.round(ms / 16);
  for (let i = 1; i <= steps; i++) {
    mouse = { x: x1 + ((x2 - x1) * i) / steps, y };
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: mouse.x, y, button: "left", buttons: 1 });
    await sleep(16);
  }
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x2, y, button: "left", buttons: 0, clickCount: 1 });
}
const at = async (expr) => run(`${H_} const n = ${expr}; if (!n) throw new Error('not found: ${expr.replace(/'/g, "")}'); n.scrollIntoView({ block: 'nearest' }); return center(n);`);
const clickOn = async (expr, ms) => { const p = await at(expr); await click(p.x, p.y, ms); };
async function typeText(text) {
  for (const ch of text) { await send("Input.insertText", { text: ch }); await sleep(55); }
}

async function record(name, { url, setup = "", actions, tail = 1200 }) {
  if (ONLY && ONLY !== name) return;
  const out = join(DIR, "clips", name);
  rmSync(out, { recursive: true, force: true });
  mkdirSync(join(out, "raw"), { recursive: true });
  await send("Page.navigate", { url: "about:blank" });
  await sleep(200);
  await send("Page.navigate", { url });
  await sleep(2000);
  await run("await document.fonts.ready;");
  if (setup) console.log(name, "setup ->", JSON.stringify(await run(setup)));
  mouse = { x: W * 0.62, y: H * 0.55 };
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: mouse.x, y: mouse.y });
  await sleep(400);
  const frames = [];
  onFrame = (p) => {
    const file = join(out, "raw", `${String(frames.length).padStart(5, "0")}.jpg`);
    writeFileSync(file, Buffer.from(p.data, "base64"));
    frames.push({ file, t: p.metadata.timestamp });
  };
  await send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: W * DPR, maxHeight: H * DPR, everyNthFrame: 1 });
  const started = Date.now() / 1000;
  await actions();
  await sleep(tail);
  const stopped = Date.now() / 1000;
  await send("Page.stopScreencast");
  onFrame = null;
  // Frames arrive only when something changes: hold each until the next, then resample to 30 fps.
  const lines = [];
  frames.forEach((f, i) => {
    const next = i + 1 < frames.length ? frames[i + 1].t : f.t + (stopped - Math.max(started, frames[frames.length - 1].t));
    lines.push(`file '${f.file}'`, `duration ${Math.max(0.001, next - f.t).toFixed(4)}`);
  });
  lines.push(`file '${frames[frames.length - 1].file}'`);
  writeFileSync(join(out, "list.txt"), lines.join("\n"));
  mkdirSync(join(out, "seq"), { recursive: true });
  execFileSync(FFMPEG, ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", join(out, "list.txt"),
    "-vf", "fps=30,scale=1800:-2", "-q:v", "3", join(out, "seq", "%05d.jpg")]);
  const count = readdirSync(join(out, "seq")).length;
  writeFileSync(join(out, "info.json"), JSON.stringify({ frames: count, seconds: count / 30 }));
  rmSync(join(out, "raw"), { recursive: true, force: true });
  console.log("recorded", name, `${frames.length} raw frames -> ${count} @30fps (${(count / 30).toFixed(1)}s)`);
}

try {
  // 1. Drop a song in and watch it separate.
  await record("process", {
    url: `${TEST}/#/`,
    setup: `${H_} return document.getElementById('model-picker').textContent.slice(0, 40);`,
    actions: async () => {
      const zone = await at("document.getElementById('dropzone')");
      await moveTo(zone.x, zone.y, 700);
      await sleep(500);
      const { root } = await send("DOM.getDocument", { depth: 1 });
      const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: "#file-input" });
      await send("DOM.setFileInputFiles", { nodeId, files: [DEMO] });
      await run(`${H_} await until(() => !document.getElementById('view-studio').hidden && !document.getElementById('btn-play').disabled, 120000); await wait(1500); return location.hash;`);
    },
  });

  // 2. Press play: playhead, section and chord follow the music.
  await record("analysis", {
    url: `${TEST}/#/job/${JOB}`,
    setup: `${STUDIO_READY} return 'ready';`,
    actions: async () => {
      await clickOn("document.getElementById('btn-play')", 600);
      await sleep(5200);
    },
    tail: 300,
  });

  // 3. Select a vocal phrase and move it to a new track.
  await record("cut", {
    url: `${TEST}/#/job/${JOB}`,
    setup: `${STUDIO_READY} scrollTo(document.querySelector('.console'), 0); await wait(300); return 'ok';`,
    actions: async () => {
      const geo = await run(`${H_} const base = document.getElementById('tl-base'); const r = base.getBoundingClientRect();
        const css = getComputedStyle(document.documentElement); const px = (n) => parseFloat(css.getPropertyValue(n));
        const top = px('--row-ruler') + px('--row-section') + px('--row-chord') + px('--row-tempo');
        const dur = 262.76; const x = (t) => r.left + (t / dur) * r.width;
        return { x1: x(61), x2: x(83), y: r.top + top + px('--row-stem') / 2 };`);
      await drag(geo.x1, geo.y, geo.x2, 1100);
      await sleep(700);
      await clickOn("document.getElementById('sel-name')", 500);
      await typeText("คอรัสท่อนฮุก");
      await sleep(400);
      await clickOn("document.getElementById('sel-move')", 500);
      await run(`${H_} await until(() => [...document.querySelectorAll('.strip-name')].some((n) => n.textContent.includes('คอรัสท่อนฮุก')), 20000); return 'moved';`);
      await sleep(800);
      await run(`${H_} window.scrollBy({ top: 260, behavior: 'smooth' }); return 1;`);
      await sleep(1200);
    },
  });

  // 4. Metronome editing: zoom in, show the tempo of each part.
  await record("click", {
    url: `${TEST}/#/job/${JOB}`,
    setup: `${STUDIO_READY} scrollTo(document.querySelector('.console'), 0); await wait(300); return 'ok';`,
    actions: async () => {
      await clickOn("document.getElementById('btn-edit-click')", 600);
      await sleep(700);
      for (let i = 0; i < 3; i++) { await clickOn("document.getElementById('zoom-in')", i ? 200 : 500); await sleep(250); }
      await sleep(600);
      await clickOn("[...document.querySelectorAll('#metro-panel button, #metro-panel .tempo-chip, .tempo-chip')].find((n) => n.textContent.includes('ช้าลง'))", 700);
      await sleep(1600);
    },
  });

  // 5. Add a spaced count-in cue.
  await record("cues", {
    url: `${TEST}/#/job/${JOB}`,
    setup: `${STUDIO_READY} scrollTo(document.querySelector('.console'), 0); await wait(300); return 'ok';`,
    actions: async () => {
      await clickOn("document.getElementById('btn-add-cue')", 600);
      await sleep(500);
      await clickOn("[...document.querySelectorAll('#cue-tabs .chip')].find((n) => n.textContent.trim() === 'นับเข้า')", 500);
      await sleep(400);
      await clickOn("[...document.querySelectorAll('#cue-items button')].find((n) => n.textContent.includes('นับเว้น 1'))", 500);
      await sleep(500);
      await clickOn("document.getElementById('cue-time')", 450);
      await run(`${H_} const i = document.getElementById('cue-time'); i.select(); return 1;`);
      await typeText("98.53");
      await sleep(300);
      await clickOn("document.getElementById('cue-add')", 450);
      await sleep(1200);
      await run(`${H_} const strip = [...document.querySelectorAll('.strip')].at(-1); scrollTo(strip, 420); return 1;`);
      await sleep(1600);
    },
  });

  // 6. Click + cues left, music right.
  await record("routing", {
    url: `${TEST}/#/job/${JOB}`,
    setup: `${STUDIO_READY} byText('#routing .chip', 'คืนค่าตรงกลาง').click(); const m = [...document.querySelectorAll('.strip')].find((s) => s.textContent.includes('เมโทรนอม')).querySelector('.mute'); if (m.getAttribute('aria-pressed') !== 'true') m.click(); scrollTo(document.querySelector('.console'), document.querySelector('.topbar').offsetHeight + 8); await wait(300); return document.querySelector('.topbar').offsetHeight;`,
    actions: async () => {
      await clickOn("[...document.querySelectorAll('#routing .chip')].find((n) => n.textContent.startsWith('คิว+เมโทรนอม ซ้าย'))", 700);
      await sleep(900);
      await clickOn("[...document.querySelectorAll('.strip')].find((s) => s.textContent.includes('เมโทรนอม')).querySelector('.mute')", 700);
      await sleep(1500);
    },
  });

  // 7. Setlist: the end of song 1 hands over to song 2 by itself.
  await record("setlist", {
    url: `${TEST}/#/playlists/${PLAYLIST}`,
    setup: `${H_} await until(() => document.querySelectorAll('.pl-item').length > 0); pl.open = null; plRenderItems(); return document.querySelectorAll('.pl-item').length;`,
    actions: async () => {
      await clickOn("document.querySelector('.pl-item .pl-row-play')", 700);
      await sleep(400);
      await run(`${H_} pl.playing = pl.shown; await plPlayer.play(pl.shown.items[0].id, 15); return 1;`);
      await run(`${H_} await until(() => plPlayer.now?.itemId === pl.shown.items[1].id && plPlayer.position() > 1.2, 30000); return 1;`);
      await sleep(1500);
      await clickOn("document.querySelector('.pl-item:nth-child(2) .pl-row')", 600);
      await sleep(1800);
      await run(`${H_} plPlayer.halt(); return 1;`);
    },
    tail: 400,
  });
} finally {
  ws.close();
  chrome.kill();
}

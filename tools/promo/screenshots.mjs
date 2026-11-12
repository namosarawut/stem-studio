// Drive a private headless Chrome over the DevTools protocol and save screenshots.
// Usage: node shoot.mjs <outDir> [only-shot-name]
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const OUT = process.argv[2];
const ONLY = process.argv[3] ?? null;
mkdirSync(OUT, { recursive: true });
const REAL = "http://127.0.0.1:8765";
const TEST = "http://127.0.0.1:8766";
const JOB = "a322c2050f96";
const PLAYLIST = "23d743901476";
const PORT = 9333;

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(OUT, ".profile")}`,
  "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required", "--mute-audio",
  "--no-first-run", "--no-default-browser-check", "--window-size=1440,1000", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch { /* not up yet */ }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const waiting = new Map();
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
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

async function shot(name, { url, width = 1440, height = 900, setup = "", wait = 1500, reload = true, drag = false, post = "" }) {
  if (ONLY && ONLY !== name) return;
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 2, mobile: false });
  if (reload) {
    await send("Page.navigate", { url: "about:blank" });
    await sleep(200);
    await send("Page.navigate", { url });
    await sleep(2500);
    await run("await document.fonts.ready;");
  }
  let setupResult = null;
  if (setup) { setupResult = await run(setup); console.log(name, "setup ->", JSON.stringify(setupResult)); }
  if (drag && setupResult) {
    // A real mouse drag across the timeline, as a person would make it.
    const { x1, x2, y } = setupResult;
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x1, y });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: x1, y, button: "left", buttons: 1, clickCount: 1 });
    for (let i = 1; i <= 12; i++) {
      await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x1 + ((x2 - x1) * i) / 12, y, button: "left", buttons: 1 });
      await sleep(30);
    }
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x2, y, button: "left", buttons: 0, clickCount: 1 });
    await sleep(500);
  }
  if (post) console.log(name, "post ->", JSON.stringify(await run(post)));
  await sleep(wait);
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, "base64"));
  console.log("saved", name);
}

// Helpers injected per page.
const H = `
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(150); } return false; };
  const byText = (sel, text) => [...document.querySelectorAll(sel)].find((n) => n.textContent.trim().startsWith(text));
  const scrollTo = (node, offset) => window.scrollTo(0, node.getBoundingClientRect().top + window.scrollY - offset);
`;
const STUDIO_READY = `${H} await until(() => !document.getElementById('btn-play').disabled, 40000); await wait(500);`;

try {
  // 1. Home, real songs, read-only.
  await shot("home", { url: `${REAL}/#/`, height: 1000 });
  await shot("home-search", {
    url: `${REAL}/#/`, height: 900,
    setup: `${H} const s = document.getElementById('library-search'); s.value = 'คาราโอเกะ'; s.dispatchEvent(new Event('input'));
      scrollTo(document.getElementById('model-picker'), 40); return document.getElementById('library-count').textContent;`,
  });
  // 2. Studio overview (test copy of Circle).
  await shot("studio", { url: `${TEST}/#/job/${JOB}`, height: 1000, setup: `${STUDIO_READY} return document.title;`, wait: 1200 });
  // 3. Timeline with click + cues routed left, music right.
  await shot("timeline", {
    url: `${TEST}/#/job/${JOB}`, height: 1250,
    setup: `${STUDIO_READY}
      byText('#routing .chip', 'คิว+เมโทรนอม ซ้าย').click();
      const clickStrip = byText('.strip', 'เมโทรนอม'); const m = clickStrip.querySelector('.mute'); if (m.getAttribute('aria-pressed') === 'true') m.click();
      scrollTo(document.querySelector('.console'), 76); return 'ok';`,
  });
  // 4. Metronome editing, zoomed in.
  await shot("metronome", {
    url: `${TEST}/#/job/${JOB}`, height: 1000,
    setup: `${STUDIO_READY}
      document.getElementById('btn-edit-click').click(); await wait(300);
      for (let i = 0; i < 3; i++) { document.getElementById('zoom-in').click(); await wait(150); }
      scrollTo(document.querySelector('.console'), 76); return document.getElementById('zoom-label').textContent;`,
  });
  // 5. Cue picker on the count-in tab.
  await shot("cues", {
    url: `${TEST}/#/job/${JOB}`, height: 1000,
    setup: `${STUDIO_READY}
      document.getElementById('btn-add-cue').click(); await until(() => document.querySelectorAll('#cue-tabs .chip').length > 2);
      byText('#cue-tabs .chip', 'นับเข้า').click(); await wait(300);
      byText('#cue-items button', 'นับเว้น 1').click(); await wait(200);
      scrollTo(document.getElementById('cue-panel'), 90); return document.querySelectorAll('#cue-items button').length;`,
  });
  // 6. Add-to-setlist dialog.
  await shot("add-to-setlist", {
    url: `${TEST}/#/job/${JOB}`, height: 900,
    setup: `${STUDIO_READY} document.getElementById('btn-add-playlist').click(); await until(() => document.querySelectorAll('#add-pl-list .btn').length > 0); return 'ok';`,
  });
  // 7. Resources report.
  await shot("resources", {
    url: `${TEST}/#/job/${JOB}`, height: 900,
    setup: `${STUDIO_READY} const r = document.getElementById('resources'); r.hidden = false; scrollTo(r, 90); return r.textContent.slice(0, 60);`,
  });
  // 9. Select a stretch of the vocal track to move it to a new track.
  await shot("cut", {
    url: `${TEST}/#/job/${JOB}`, height: 1000, drag: true,
    setup: `${STUDIO_READY}
      scrollTo(document.querySelector('.console'), 76); await wait(300);
      const base = document.getElementById('tl-base'); const r = base.getBoundingClientRect();
      const css = getComputedStyle(document.documentElement); const px = (n) => parseFloat(css.getPropertyValue(n));
      const top = px('--row-ruler') + px('--row-section') + px('--row-chord') + px('--row-tempo');
      const dur = 262.76; const x = (t) => r.left + (t / dur) * r.width;
      return { x1: x(61), x2: x(83), y: r.top + top + px('--row-stem') / 2 };`,
    post: `${H} await wait(300); const bar = document.getElementById('sel-bar'); return { hidden: bar.hidden, text: bar.textContent.replace(/\s+/g, ' ').slice(0, 120) };`,
  });
  // 8. Setlist, playing the second song, its details open.
  await shot("setlist", {
    url: `${TEST}/#/playlists/${PLAYLIST}`, height: 1150,
    setup: `${H} await until(() => document.querySelectorAll('.pl-item').length > 0);
      const item = pl.shown.items[1]; pl.open = item.id; plRenderItems();
      pl.playing = pl.shown; await plPlayer.play(item.id, 41.5);
      await until(() => document.querySelector('.pl-mix-row')); await wait(1500); return document.getElementById('pl-now-sub').textContent;`,
    wait: 800,
  });
} finally {
  ws.close();
  chrome.kill();
}

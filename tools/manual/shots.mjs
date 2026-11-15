// Screenshots and short animations for the Prism manual, from a private headless Chrome
// driven over the DevTools protocol (same approach as tools/promo/screenshots.mjs).
// Usage: node shots.mjs <outDir> <phase> [only]
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const OUT = process.argv[2];
const PHASE = process.argv[3] ?? "1";
const ONLY = process.argv[4] ?? null;
const BASE = "http://127.0.0.1:8767";
const SONG = "b456616ddb24";
const IMPORT_FILE = process.env.IMPORT_FILE;
const PORT = 9334;
mkdirSync(join(OUT, "frames"), { recursive: true });

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(OUT, ".profile")}`,
  "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required", "--mute-audio", "--lang=th-TH",
  "--no-first-run", "--no-default-browser-check", "--window-size=1440,900", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 60 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch { /* starting */ }
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
  const result = await send("Runtime.evaluate", { expression: `(async () => { ${H} ${expression} })()`, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
};
const H = `
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 60000) => { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(150); } return false; };
  const byText = (sel, text) => [...document.querySelectorAll(sel)].find((n) => n.textContent.trim().startsWith(text));
  const box = (node) => { const r = node.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, l: r.left, t: r.top, w: r.width, h: r.height }; };
  const studioReady = async () => { await until(() => document.getElementById('btn-play') && !document.getElementById('btn-play').disabled, 60000); await wait(600); };
`;
await send("Page.enable");
await send("Runtime.enable");
await send("DOM.enable");

const W = 1440;
async function viewport(height = 900) {
  await send("Emulation.setDeviceMetricsOverride", { width: W, height, deviceScaleFactor: 2, mobile: false });
}
async function go(url, height = 900) {
  await viewport(height);
  await send("Page.navigate", { url: "about:blank" });
  await sleep(200);
  await send("Page.navigate", { url });
  await sleep(2200);
  await run("await document.fonts.ready;");
}
const meta = {};
async function capture(name, clip = null, marks = []) {
  const params = { format: "png" };
  // Clips are measured in viewport coordinates; the protocol wants document coordinates.
  if (clip) { const sy = await run("return scrollY;"); params.clip = { ...clip, y: clip.y + sy, scale: 1 }; }
  const { data } = await send("Page.captureScreenshot", params);
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, "base64"));
  const area = clip ?? { x: 0, y: 0, width: W, height: (await run("return innerHeight;")) };
  meta[name] = {
    width: area.width, height: area.height,
    marks: marks.map((m) => ({ n: m.n, x: +((((m.l ?? m.x) - area.x) / area.width) * 100).toFixed(2), y: +((((m.t ?? m.y) - area.y) / area.height) * 100).toFixed(2) })),
  };
  console.log("saved", name);
}
async function frames(name, count, gapMs, clip, step = async () => {}) {
  for (let i = 0; i < count; i++) {
    await step(i);
    await sleep(gapMs);
    const sy = await run("return scrollY;");
    const { data } = await send("Page.captureScreenshot", { format: "png", clip: { ...clip, y: clip.y + sy, scale: 1 } });
    writeFileSync(join(OUT, "frames", `${name}-${String(i).padStart(3, "0")}.png`), Buffer.from(data, "base64"));
  }
  meta[name] = { width: clip.width, height: clip.height, frames: count, gap: gapMs };
  console.log("frames", name, count);
}
const mouse = (type, x, y, extra = {}) => send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, ...extra });
const want = (name) => !ONLY || ONLY === name;

try {
  if (PHASE === "1") {
    // Home page: drop zone, models, library.
    if (want("home")) {
      await go(`${BASE}/#/`, 1000);
      const m = await run(`
        await until(() => document.querySelector('#library .lib-item'), 15000); await wait(300);
        return [
          { n: 1, ...box(document.getElementById('dropzone')) },
          { n: 2, ...box(document.getElementById('model-picker')) },
          { n: 3, ...box(document.querySelector('#library .lib-item')) },
          { n: 4, ...box(byText('.brand-actions .btn', 'เพลย์ลิสต์')) },
        ];`);
      await capture("home", null, m);
    }
    // Import: pick a file, the trim dialog opens; drag the start line; separate.
    if (want("trim")) {
      await go(`${BASE}/#/`, 900);
      const { root } = await send("DOM.getDocument", { depth: -1, pierce: true });
      const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: "#file-input" });
      await send("DOM.setFileInputFiles", { files: [IMPORT_FILE], nodeId });
      const d = await run(`
        await until(() => document.getElementById('trim-dialog').open && !document.getElementById('trim-play').disabled, 30000); await wait(500);
        const c = box(document.getElementById('trim-wave')); const dlg = box(document.getElementById('trim-dialog'));
        return { c, dlg, go: box(document.getElementById('trim-go')), play: box(document.getElementById('trim-play')), times: box(document.getElementById('trim-start')) };`);
      const clip = { x: Math.round(d.dlg.l) - 8, y: Math.round(d.dlg.t) - 8, width: Math.round(d.dlg.w) + 16, height: Math.round(d.dlg.h) + 16 };
      await capture("trim", clip, [
        { n: 1, x: d.c.l + 6, y: d.c.y }, { n: 2, x: d.c.l + d.c.w - 6, y: d.c.y }, { n: 3, x: d.play.x, y: d.play.y },
        { n: 4, x: d.times.x, y: d.times.y }, { n: 5, x: d.go.x, y: d.go.y },
      ]);
      // The drag, frame by frame.
      const y = d.c.y, x1 = d.c.l + 2, x2 = d.c.l + d.c.w * 0.16;
      await mouse("mouseMoved", x1, y, { buttons: 0 });
      await frames("trim-drag", 26, 70, clip, async (i) => {
        if (i === 3) await mouse("mousePressed", x1, y);
        if (i > 3 && i <= 17) await mouse("mouseMoved", x1 + ((x2 - x1) * (i - 3)) / 14, y);
        if (i === 18) await mouse("mouseReleased", x2, y);
      });
      // Separate only the chosen part, and catch the processing page on the way.
      await run(`document.getElementById('trim-go').click();`);
      await run(`await until(() => !document.getElementById('view-processing').hidden && parseInt(document.getElementById('proc-percent').textContent) >= 20, 120000); await wait(300);`);
      await capture("processing", { x: 0, y: 0, width: W, height: 640 });
      await run(`await until(() => location.hash.startsWith('#/job/') && !document.getElementById('view-studio').hidden, 240000);`);
      console.log("import finished");
    }
    // The song page, top part: transport, actions, numbers about the song.
    if (want("studio-top")) {
      await go(`${BASE}/#/job/${SONG}`, 900);
      const m = await run(`
        await studioReady();
        return [
          { n: 1, ...box(document.getElementById('btn-play')) },
          { n: 2, ...box(document.querySelector('.clock')) },
          { n: 3, ...box(document.querySelector('.now')) },
          { n: 4, ...box(document.getElementById('btn-add-playlist')) },
          { n: 5, ...box(document.getElementById('btn-zip')) },
          { n: 6, ...box(document.getElementById('stats')) },
        ];`);
      await capture("studio-top", { x: 0, y: 0, width: W, height: 330 }, m);
    }
    // Mixer and timeline.
    if (want("studio-mixer")) {
      await go(`${BASE}/#/job/${SONG}`, 1100);
      const r = await run(`
        await studioReady();
        const con = document.querySelector('.console'); const bar = document.querySelector('#view-studio .topbar').getBoundingClientRect().height;
        window.scrollTo(0, con.getBoundingClientRect().top + scrollY - bar - 8); await wait(500);
        const strip = document.querySelector('.strip');
        return { con: box(con), marks: [
          { n: 1, ...box(document.getElementById('presets')) },
          { n: 2, ...box(document.getElementById('routing')) },
          { n: 3, ...box(document.querySelector('.edit-tools')) },
          { n: 4, ...box(strip.querySelector('.strip-buttons') ?? strip) },
          { n: 5, ...box(strip.querySelector('input[type=range]')) },
          { n: 6, ...box(document.getElementById('tl-base') ?? document.querySelector('canvas')) },
        ] };`);
      await capture("studio-mixer", null, r.marks);
      // Playing: the playhead runs and the section / chord readouts follow.
      await run(`document.getElementById('btn-play').click();`);
      await frames("studio-play", 24, 160, { x: 0, y: 0, width: W, height: 330 });
      await run(`document.getElementById('btn-play').click();`);
    }
  }
  if (PHASE === "2") {
    const SONG_B = "2459354e7205";  // steady 150 BPM: tidy ticks for the editor
    const key = async (k, code, n = 1) => { for (let i = 0; i < n; i++) { await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code }); await sleep(60); } };
    const click = async (x, y) => { await mouse("mouseMoved", x, y, { buttons: 0 }); await mouse("mousePressed", x, y); await mouse("mouseReleased", x, y); };
    // Timeline geometry: row of a track (by its download link), x of a timeline time.
    const G = `
      const sc = document.getElementById('scroller'); const base = document.getElementById('tl-base');
      const dur = JSON.parse(sessionStorage.getItem('dur'));
      const pps = sc.scrollWidth / dur; const br = base.getBoundingClientRect();
      const xOf = (t) => br.left + t * pps - sc.scrollLeft;
      const rowOf = (id) => { const s = document.querySelector('.strip-dl[href$="/stems/' + id + '.wav"]').closest('.strip').getBoundingClientRect(); return { top: s.top, bottom: s.bottom, y: s.top + s.height / 2 }; };
    `;
    const bands = (b, clip) => ({ drums: [b.drums.top - clip.y, b.drums.bottom - clip.y], click: [b.click.top - clip.y, b.click.bottom - clip.y] });
    async function openEditor(song, height, seekSteps) {
      await go(`${BASE}/#/job/${song}`, height);
      const a = await (await fetch(`${BASE}/api/jobs/${song}/analysis`)).json();
      await run(`await studioReady(); sessionStorage.setItem('dur', '${a.duration}');
        const con = document.querySelector('.console'); const bar = document.querySelector('#view-studio .topbar').getBoundingClientRect().height;
        window.scrollTo(0, con.getBoundingClientRect().top + scrollY - bar - 8); await wait(300);
        document.getElementById('btn-edit-click').click(); await wait(400);`);
      await key("ArrowRight", "ArrowRight", seekSteps);
      return a.click_grid.beats;
    }
    async function zoomTo(times) { for (let i = 0; i < times; i++) { await run(`document.getElementById('zoom-in').click(); await wait(250);`); } await sleep(500); }
    // Beat nearest the middle of the visible timeline.
    const middleBeat = (beats) => run(`${G}
      const mid = br.left + br.width / 2; const beats = ${JSON.stringify(beats)};
      let best = 0; for (let i = 0; i < beats.length; i++) if (Math.abs(xOf(beats[i]) - mid) < Math.abs(xOf(beats[best]) - mid)) best = i;
      return { i: best, x: xOf(beats[best]), click: rowOf('click'), drums: rowOf('drums'), br: { l: br.left, w: br.width } };`);

    if (want("metro-panel")) {
      const beats = await openEditor(SONG_B, 1250, 12);
      await zoomTo(4);
      const b = await middleBeat(beats);
      await click(b.x, b.click.y);
      await sleep(500);
      const m = await run(`
        const p = document.getElementById('metro-panel');
        const before = (node) => { const b = box(node); return { x: b.l - 13, y: b.y }; };
        return [
          { n: 1, ...before(p.querySelector('[data-shift]').closest('.edit-group')) },
          { n: 2, ...before(document.getElementById('metro-bpm').closest('.edit-group')) },
          { n: 3, ...before(document.getElementById('metro-meter').closest('.edit-group')) },
          { n: 4, ...before(document.getElementById('metro-claude')) },
          { n: 5, ...before(document.getElementById('tempo-chips').closest('.edit-row')) },
          { n: 6, ...before(document.getElementById('beat-controls')) },
        ];`);
      m.push({ n: 7, x: b.x - 16, y: b.click.y });
      await capture("metro-panel", null, m);
    }
    if (want("beat-drag")) {
      const beats = await openEditor(SONG_B, 1250, 12);
      await zoomTo(5);
      const b = await middleBeat(beats);
      const clip = { x: Math.round(b.br.l), y: Math.round(b.drums.top) - 4, width: Math.round(b.br.w), height: Math.round(b.click.bottom - b.drums.top) + 40 };
      const y = b.click.y, x1 = b.x, x2 = b.x + 34;
      await mouse("mouseMoved", x1, y, { buttons: 0 });
      await frames("beat-drag", 34, 80, clip, async (i) => {
        if (i === 4) await mouse("mousePressed", x1, y);
        if (i > 4 && i <= 14) await mouse("mouseMoved", x1 + ((x2 - x1) * (i - 4)) / 10, y);
        if (i > 18 && i <= 26) await mouse("mouseMoved", x2 - ((x2 - x1) * (i - 18)) / 8, y);
        if (i === 27) await mouse("mouseReleased", x1, y);
      });
      meta["beat-drag"].bands = bands(b, clip);
    }
    if (want("repair")) {
      // Knock one beat out, then let "ซ่อมจุดที่เพี้ยน" put it back.
      const beats = await openEditor(SONG_B, 1250, 12);
      await zoomTo(4);
      const b = await middleBeat(beats);
      await click(b.x, b.click.y);
      await sleep(300);
      await run(`document.getElementById('beat-delete').click(); await wait(1500);`);
      await mouse("mouseMoved", 5, 5, { buttons: 0 });
      const clip = { x: Math.round(b.br.l), y: Math.round(b.drums.top) - 4, width: Math.round(b.br.w), height: Math.round(b.click.bottom - b.drums.top) + 40 };
      await capture("repair-before", clip, []);
      await run(`const t = document.getElementById('toast'); t.hidden = true; document.getElementById('metro-repair').click(); await until(() => !t.hidden && t.textContent, 30000); await wait(1500);`);
      const toastText = await run(`return document.getElementById('toast').textContent;`);
      await capture("repair-after", clip, []);
      meta["repair-before"].bands = bands(b, clip);
      meta["repair-before"].gapX = b.x - clip.x;
      meta["repair-after"].toast = toastText;
    }
    if (want("claude")) {
      await openEditor(SONG_B, 1000, 0);
      await run(`document.getElementById('metro-claude').click(); await until(() => document.getElementById('claude-dialog').open, 20000); await wait(600);`);
      const d = await run(`return box(document.getElementById('claude-dialog'));`);
      await capture("claude", { x: Math.round(d.l) - 8, y: Math.round(d.t) - 8, width: Math.round(d.w) + 16, height: Math.round(d.h) + 16 });
      await run(`document.getElementById('claude-cancel').click();`);
    }
    if (want("steady")) {
      // Song A drifts 93–115 BPM: one step to a steady click.
      await go(`${BASE}/#/job/${SONG}`, 1000);
      await run(`await studioReady(); const con = document.querySelector('.console'); const bar = document.querySelector('#view-studio .topbar').getBoundingClientRect().height;
        window.scrollTo(0, con.getBoundingClientRect().top + scrollY - bar - 8); await wait(300);`);
      const tools = await run(`return { steady: box(document.getElementById('btn-steady')), lock: box(document.getElementById('lock-field')), edit: box(document.getElementById('btn-edit-click')), tools: box(document.querySelector('.edit-tools')) };`);
      await capture("tools-before", { x: 0, y: Math.round(tools.tools.t) - 10, width: W, height: Math.round(tools.tools.h) + 20 }, [
        { n: 1, x: tools.steady.l, y: tools.steady.t }, { n: 2, x: tools.lock.l, y: tools.lock.t }, { n: 3, x: tools.edit.l, y: tools.edit.t },
      ]);
      await run(`document.getElementById('btn-steady').click(); await until(() => document.getElementById('steady-dialog').open); await wait(500);`);
      let d = await run(`return { d: box(document.getElementById('steady-dialog')), o: box(document.getElementById('steady-options')), i: box(document.getElementById('steady-bpm')), g: box(document.getElementById('steady-go')) };`);
      const dclip = (q) => ({ x: Math.round(q.l) - 8, y: Math.round(q.t) - 8, width: Math.round(q.w) + 16, height: Math.round(q.h) + 16 });
      await capture("steady-choose", dclip(d.d), [{ n: 1, x: d.o.l, y: d.o.t }, { n: 2, x: d.i.l, y: d.i.t }, { n: 3, x: d.g.l, y: d.g.t }]);
      await run(`document.getElementById('steady-go').click(); await wait(400);`);
      d = await run(`return box(document.getElementById('steady-dialog'));`);
      await capture("steady-working", dclip(d));
      await run(`await until(() => document.getElementById('steady-dialog').open && !document.getElementById('steady-result').hidden, 240000); await wait(800);`);
      d = await run(`return { d: box(document.getElementById('steady-dialog')), l: box(document.getElementById('steady-listen')) };`);
      await capture("steady-result", dclip(d.d), [{ n: 1, x: d.l.l, y: d.l.t }]);
      await run(`document.getElementById('steady-cancel').click(); await wait(400);
        const con = document.querySelector('.console'); const bar = document.querySelector('#view-studio .topbar').getBoundingClientRect().height;
        window.scrollTo(0, con.getBoundingClientRect().top + scrollY - bar - 8); await wait(400);`);
      const after = await run(`return { steady: box(document.getElementById('btn-steady')), lock: box(document.getElementById('btn-lock')), edit: box(document.getElementById('btn-edit-click')), tools: box(document.querySelector('.edit-tools')), tempo: box(document.querySelector('.strip-head.tempo')) };`);
      await capture("tools-after", { x: 0, y: Math.round(after.tools.t) - 10, width: W, height: Math.round(after.tools.h) + 20 }, [
        { n: 1, x: after.steady.l, y: after.steady.t }, { n: 2, x: after.lock.l, y: after.lock.t }, { n: 3, x: after.edit.l, y: after.edit.t },
      ]);
      await capture("locked-view", null, []);
    }
  }
  if (PHASE === "3") {
    const SONG_B = "2459354e7205";
    const click = async (x, y) => { await mouse("mouseMoved", x, y, { buttons: 0 }); await mouse("mousePressed", x, y); await mouse("mouseReleased", x, y); };
    const key = async (k, code, n = 1) => { for (let i = 0; i < n; i++) { await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code }); await sleep(60); } };
    const toConsole = `const con = document.querySelector('.console'); const bar = document.querySelector('#view-studio .topbar').getBoundingClientRect().height;
      window.scrollTo(0, con.getBoundingClientRect().top + scrollY - bar - 8); await wait(400);`;
    // Rows of the timeline (header rows and tracks), in viewport coordinates.
    const ROWS = `
      const rect = (n) => { const r = n.getBoundingClientRect(); return [r.top, r.bottom]; };
      const rows = {};
      for (const k of ['ruler', 'sections', 'chords', 'tempo']) rows[k] = rect(document.querySelector('.strip-head.' + k));
      for (const s of document.querySelectorAll('.strip')) { const a = s.querySelector('.strip-dl'); if (a) rows[a.getAttribute('href').split('/').pop().replace('.wav', '')] = rect(s); }
      const sc = document.getElementById('scroller'); const base = document.getElementById('tl-base'); const br = base.getBoundingClientRect();
      const dur = ${"${DUR}"};
      const pps = sc.scrollWidth / dur;
      const xOf = (t) => br.left + t * pps - sc.scrollLeft;
    `;
    const a = await (await fetch(`${BASE}/api/jobs/${SONG_B}/analysis`)).json();
    const geo = (body) => run(ROWS.replace("${DUR}", String(a.duration)) + body);
    const toastWait = `const t = document.getElementById('toast'); await until(() => !t.hidden && t.textContent, 30000); await wait(900);`;
    const hideToast = `document.getElementById('toast').hidden = true;`;
    const open = async (height = 1250) => { await go(`${BASE}/#/job/${SONG_B}`, height); await run(`await studioReady(); ${toConsole}`); };
    const cuesOf = async () => (await (await fetch(`${BASE}/api/jobs/${SONG_B}/analysis`)).json()).stems.find((s) => s.id === "cues")?.cues ?? [];

    if (want("cues")) {
      await open();
      await run(`${hideToast} document.getElementById('btn-auto-cue').click(); ${toastWait}`);
      const g = await geo(`return { rows, toast: document.getElementById('toast').textContent, auto: box(document.getElementById('btn-auto-cue')), add: box(document.getElementById('btn-add-cue')) };`);
      await capture("cue-auto", null, [{ n: 1, x: g.auto.l - 13, y: g.auto.y }, { n: 2, x: g.add.l - 13, y: g.add.y }]);
      meta["cue-auto"].rows = g.rows; meta["cue-auto"].toast = g.toast;
      await sleep(1500);
      // Pick one cue: the selected-cue panel.
      const cues = await cuesOf();
      const pick = cues[2] ?? cues[0];
      let p = await geo(`return { x: xOf(${pick.time}), y: (rows.cues[0] + rows.cues[1]) / 2 };`);
      await click(p.x + 10, p.y);
      await sleep(600);
      const s = await geo(`const q = (id) => box(document.getElementById(id));
        return { rows, label: q('cs-label'), time: q('cs-time'), move: box(document.querySelector('[data-cue-move="-beat"]')), voice: q('cs-voice'), listen: q('cs-listen'), x: xOf(${pick.time}) };`);
      await capture("cue-selected", null, [
        { n: 1, x: s.time.l - 13, y: s.time.y }, { n: 2, x: s.move.l - 13, y: s.move.y }, { n: 3, x: s.voice.l - 13, y: s.voice.y },
        { n: 4, x: s.listen.l - 13, y: s.listen.y }, { n: 5, x: s.x - 14, y: (s.rows.cues[0] + s.rows.cues[1]) / 2 },
      ]);
      meta["cue-selected"].rows = s.rows;
      await run(`document.getElementById('cs-close').click(); await wait(300);`);
      // Click an empty spot in the Cues row: the cue picker.
      const free = (() => { const ts = cues.map((c) => c.time).sort((x, y) => x - y); let best = 30, gap = 0; for (let i = 1; i < ts.length; i++) if (ts[i] - ts[i - 1] > gap) { gap = ts[i] - ts[i - 1]; best = (ts[i] + ts[i - 1]) / 2; } return best; })();
      p = await geo(`return { x: xOf(${free}), y: (rows.cues[0] + rows.cues[1]) / 2 };`);
      await click(p.x, p.y);
      await run(`await until(() => !document.getElementById('cue-panel').hidden); await wait(500);
        const tab = [...document.querySelectorAll('#cue-tabs button')].find((b) => b.textContent.includes('ความดัง')); tab?.click(); await wait(300);
        document.querySelector('#cue-items button')?.click(); await wait(800);`);
      const k = await run(`const q = (id) => box(document.getElementById(id));
        return { time: q('cue-time'), offset: q('cue-offset'), tabs: q('cue-tabs'), items: q('cue-items'), voice: q('cue-voice'), add: q('cue-add'), listen: q('cue-listen'), panel: q('cue-panel') };`);
      await capture("cue-picker", { x: 0, y: Math.round(k.panel.t) - 6, width: W, height: Math.round(k.panel.h) + 12 }, [
        { n: 1, x: k.time.l - 40, y: k.time.y }, { n: 2, x: k.offset.l - 30, y: k.offset.y }, { n: 3, x: k.tabs.l - 13, y: k.tabs.y },
        { n: 4, x: k.items.l - 13, y: k.items.t + 16 }, { n: 5, x: k.voice.l - 32, y: k.voice.y }, { n: 6, x: k.listen.l - 13, y: k.listen.y },
      ]);
      await run(`const tab = [...document.querySelectorAll('#cue-tabs button')].find((b) => b.textContent.includes('นับเข้า')); tab?.click(); await wait(400);`);
      const c = await run(`return box(document.getElementById('cue-panel'));`);
      await capture("cue-count", { x: 0, y: Math.round(c.t) - 6, width: W, height: Math.round(c.h) + 12 });
      await run(`document.getElementById('cue-close').click(); await wait(300);`);
    }
    if (want("lead")) {
      await open();
      await run(`${hideToast} const s = document.getElementById('lead-bars'); s.value = '2'; s.dispatchEvent(new Event('change')); ${toastWait} await studioReady(); ${toConsole}`);
      await run(`for (let i = 0; i < 3; i++) { document.getElementById('zoom-in').click(); await wait(250); } const sc = document.getElementById('scroller'); sc.scrollLeft = 0; sc.dispatchEvent(new Event('scroll')); await wait(600);`);
      const g = await geo(`return { rows, toast: document.getElementById('toast').textContent, lead: box(document.getElementById('lead-field')), first: xOf(${a.click_grid.beats[0]} + 0) };`);
      await capture("lead-in", null, [{ n: 1, x: g.lead.l - 13, y: g.lead.y }]);
      meta["lead-in"].rows = g.rows; meta["lead-in"].toast = g.toast;
      await run(`${hideToast} const s = document.getElementById('lead-bars'); s.value = '0'; s.dispatchEvent(new Event('change')); ${toastWait}`);
    }
    if (want("cut")) {
      await open();
      const g = await geo(`return { rows, x1: xOf(42.2), x2: xOf(50.6) };`);
      const y = (g.rows.vocals[0] + g.rows.vocals[1]) / 2;
      await mouse("mouseMoved", g.x1, y, { buttons: 0 });
      await mouse("mousePressed", g.x1, y);
      for (let i = 1; i <= 12; i++) { await mouse("mouseMoved", g.x1 + ((g.x2 - g.x1) * i) / 12, y); await sleep(30); }
      await mouse("mouseReleased", g.x2, y);
      await sleep(500);
      await run(`const n = document.getElementById('sel-name'); n.value = 'แอดลิบ'; n.dispatchEvent(new Event('input')); await wait(300);`);
      const s = await geo(`const q = (id) => box(document.getElementById(id));
        return { rows, src: q('sel-source'), start: q('sel-start'), snap: q('sel-snap'), listen: q('sel-listen'), target: q('sel-target'), name: q('sel-name'), move: q('sel-move'), x1: xOf(42.2) };`);
      await capture("cut-select", null, [
        { n: 1, x: s.src.l - 13, y: s.src.y }, { n: 2, x: s.start.l - 46, y: s.start.y }, { n: 3, x: s.snap.l - 13, y: s.snap.y },
        { n: 4, x: s.listen.l - 13, y: s.listen.y }, { n: 5, x: s.target.l - 52, y: s.target.y }, { n: 6, x: s.move.l - 13, y: s.move.y },
        { n: 7, x: s.x1 - 14, y: (s.rows.vocals[0] + s.rows.vocals[1]) / 2 },
      ]);
      meta["cut-select"].rows = s.rows;
      await run(`document.getElementById('sel-move').click(); await until(() => [...document.querySelectorAll('.strip-name')].some((n) => n.textContent.includes('แอดลิบ')), 60000); await studioReady(); ${toConsole}`);
      const r = await geo(`const strip = [...document.querySelectorAll('.strip')].find((n) => n.textContent.includes('แอดลิบ'));
        return { rows, name: box(strip.querySelector('.strip-name')), undo: box(document.getElementById('btn-undo-cut')) };`);
      await capture("cut-after", null, [{ n: 1, x: r.name.l - 2, y: r.name.t }, { n: 2, x: r.undo.l - 13, y: r.undo.y }]);
      meta["cut-after"].rows = r.rows;
    }
    if (want("add")) {
      await open();
      await key("ArrowRight", "ArrowRight", 2);
      await run(`document.getElementById('btn-add-sound').click(); await until(() => document.getElementById('add-sound-dialog').open); await wait(300);`);
      const { root } = await send("DOM.getDocument", { depth: -1, pierce: true });
      const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: "#add-sound-input" });
      await send("DOM.setFileInputFiles", { files: [process.env.SOUND_FILE], nodeId });
      await run(`await until(() => !document.getElementById('add-sound-go').disabled, 10000);
        const n = document.getElementById('add-sound-name'); n.value = 'Synth intro'; n.dispatchEvent(new Event('input')); await wait(400);`);
      const d = await run(`const q = (id) => box(document.getElementById(id));
        return { d: q('add-sound-dialog'), pick: q('add-sound-pick'), name: q('add-sound-name'), at: box(document.querySelector('.add-sound-at')), go: q('add-sound-go') };`);
      await capture("add-sound", { x: Math.round(d.d.l) - 8, y: Math.round(d.d.t) - 8, width: Math.round(d.d.w) + 16, height: Math.round(d.d.h) + 16 }, [
        { n: 1, x: d.pick.l - 13, y: d.pick.y }, { n: 2, x: d.name.l - 13, y: d.name.y }, { n: 3, x: d.at.l - 13, y: d.at.t + 10 }, { n: 4, x: d.go.l - 13, y: d.go.y },
      ]);
      await run(`document.getElementById('add-sound-go').click(); await until(() => [...document.querySelectorAll('.strip-name')].some((n) => n.textContent.includes('Synth')), 60000); await studioReady(); ${toConsole}`);
      await run(`for (let i = 0; i < 2; i++) { document.getElementById('zoom-in').click(); await wait(250); } const sc = document.getElementById('scroller'); sc.scrollLeft = 0; sc.dispatchEvent(new Event('scroll')); await wait(600);`);
      const r = await geo(`const strip = [...document.querySelectorAll('.strip')].find((n) => n.textContent.includes('Synth'));
        const xs = strip.querySelectorAll('.strip-x');
        return { rows, move: box(xs[0]), del: box(xs[xs.length - 1]), x: xOf(10) };`);
      await capture("add-after", null, [{ n: 1, x: r.move.x, y: r.move.t - 6 }, { n: 2, x: r.del.x, y: r.del.t - 6 }]);
      meta["add-after"].rows = r.rows;
    }
  }
  if (PHASE === "4") {
    const SET = process.env.SET_ID;
    const dlg = (q) => ({ x: Math.round(q.l) - 8, y: Math.round(q.t) - 8, width: Math.round(q.w) + 16, height: Math.round(q.h) + 16 });
    const before = (b) => ({ x: b.l - 13, y: b.y });
    if (want("add-pl")) {
      await go(`${BASE}/#/job/${SONG}`, 900);
      await run(`await studioReady(); document.getElementById('btn-add-playlist').click(); await until(() => document.getElementById('add-pl').open && document.querySelector('#add-pl-list .btn'), 15000); await wait(500);`);
      const d = await run(`const q = (id) => box(document.getElementById(id));
        return { d: q('add-pl'), mix: q('add-pl-use-mix'), add: box(document.querySelector('#add-pl-list .btn')), name: q('add-pl-name') };`);
      await capture("add-pl", dlg(d.d), [{ n: 1, ...before(d.mix) }, { n: 2, ...before(d.add) }, { n: 3, ...before(d.name) }]);
    }
    if (want("setlist")) {
      await go(`${BASE}/#/playlists/${SET}`, 1000);
      await run(`await until(() => document.querySelectorAll('#pl-items .pl-item').length >= 4, 20000); await wait(800);`);
      const m = await run(`const q = (id) => box(document.getElementById(id));
        const rows = document.querySelectorAll('#pl-items .pl-item');
        return [
          { n: 1, ...q('pl-list') }, { n: 2, x: q('pl-play').l - 13, y: q('pl-play').y }, { n: 3, x: q('pl-show').l - 13, y: q('pl-show').y },
          { n: 4, x: q('pl-file').l - 13, y: q('pl-file').y }, { n: 5, x: q('pl-auto').l - 13, y: q('pl-auto').y },
          { n: 6, x: box(rows[0].querySelector('.pl-stop-after')).l - 13, y: box(rows[0].querySelector('.pl-stop-after')).y },
          { n: 7, x: box(rows[1].querySelector('.pl-handle')).l - 13, y: box(rows[1].querySelector('.pl-handle')).y },
          { n: 8, x: box(rows[1].querySelector('.pl-row-actions')).l - 13, y: box(rows[1].querySelector('.pl-row-actions')).y },
        ];`);
      await capture("setlist", null, m);
      // Open song 3: range, fades, the mix for this setlist.
      await run(`document.querySelectorAll('#pl-items .pl-item .pl-title')[2].click(); await until(() => document.querySelector('.pl-detail .pl-mix-row'), 20000); await wait(1500);
        const d = document.querySelector('.pl-detail'); window.scrollTo(0, d.getBoundingClientRect().top + scrollY - 140); await wait(600);`);
      const d = await run(`const det = document.querySelector('.pl-detail');
        const row = det.closest('.pl-item').querySelector('.pl-row');
        const rows = det.querySelectorAll('.pl-detail-row');
        return { item: box(row), det: box(det), wave: box(det.querySelector('canvas')), range: box(rows[1]), listen: box(rows[2]),
          mix: box(det.querySelector('.pl-mix-row')), level: box(det.querySelector('.pl-level')), pan: box(det.querySelector('.pl-mix-row .pan')),
          quick: box(det.querySelector('.pl-mix .pl-detail-row')) };`);
      const clip = { x: Math.round(d.item.l) - 10, y: Math.round(d.item.t) - 10, width: Math.round(d.item.w) + 20, height: Math.round(d.det.t + d.det.h - d.item.t) + 20 };
      await capture("setlist-detail", clip, [
        { n: 1, x: d.wave.l + 4, y: d.wave.t + 4 }, { n: 2, ...before(d.range) }, { n: 3, ...before(d.listen) }, { n: 4, ...before(d.mix) },
        { n: 5, x: d.level.l + 4, y: d.level.t - 10 }, { n: 6, x: d.pan.l + 4, y: d.pan.t - 10 }, { n: 7, ...before(d.quick) },
      ]);
      await run(`document.getElementById('pl-add-project').click(); await until(() => document.getElementById('pl-picker').open && document.querySelector('#pl-picker-list .btn'), 15000); await wait(500);`);
      const p = await run(`const q = (id) => box(document.getElementById(id));
        return { d: q('pl-picker'), tabs: q('pl-tab-projects'), search: q('pl-picker-search'), add: box(document.querySelector('#pl-picker-list .btn')) };`);
      await capture("pl-picker", dlg(p.d), [{ n: 1, ...before(p.tabs) }, { n: 2, ...before(p.search) }, { n: 3, ...before(p.add) }]);
      await run(`document.getElementById('pl-picker-close').click(); await wait(300);`);
    }
    if (want("show")) {
      await go(`${BASE}/#/playlists/${SET}`, 900);
      await run(`await until(() => document.querySelectorAll('#pl-items .pl-item').length >= 4, 20000); await wait(500);
        document.querySelectorAll('#pl-items .pl-row-play')[1].click();
        await until(() => /^0:0[3-9]|^0:[1-5]/.test(document.getElementById('pl-time').textContent), 90000);
        document.getElementById('pl-show').click(); await until(() => !document.getElementById('show').hidden); await wait(1500);`);
      const m = await run(`const q = (id) => box(document.getElementById(id)); const before = (b) => ({ x: b.l - 13, y: b.y });
        return [
          { n: 1, ...before(q('show-title')) }, { n: 2, ...before(q('show-section')) }, { n: 3, ...before(q('show-elapsed')) },
          { n: 4, x: q('show-next-title').l - 60, y: q('show-next-title').y }, { n: 5, x: q('show-prev').l - 13, y: q('show-prev').y },
          { n: 6, x: q('show-list-panel').l + 6, y: q('show-list-panel').t + 6 }, { n: 7, x: q('show-midi-btn').l - 13, y: q('show-midi-btn').y },
          { n: 8, x: q('show-set').l - 13, y: q('show-set').y },
        ];`);
      await capture("show", null, m);
      // Lock: the buttons now need a press-and-hold.
      await run(`document.getElementById('show-lock').click(); await wait(500);`);
      const l = await run(`const q = (id) => box(document.getElementById(id)); return { a: q('show-midi-btn'), b: q('show-exit') };`);
      await capture("show-locked", { x: Math.round(l.a.l) - 30, y: Math.round(l.a.t) - 16, width: Math.round(l.b.l + l.b.w - l.a.l) + 60, height: Math.round(l.a.h) + 32 });
      await run(`const b = document.getElementById('show-lock'); b.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); await wait(1400); b.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })); await wait(300);
        document.getElementById('show-midi-btn').click(); await wait(1200);`);
      const g = await run(`return box(document.getElementById('show-midi'));`);
      await capture("show-midi", dlg(g));
      await run(`document.getElementById('show-midi-close').click(); document.getElementById('show-play').click();`);
    }
    if (want("trash")) {
      await go(`${BASE}/#/`, 1000);
      await run(`await until(() => !document.getElementById('trash-section').hidden, 15000); document.getElementById('trash-details').open = true; await wait(600);
        const s = document.getElementById('trash-section'); window.scrollTo(0, s.getBoundingClientRect().top + scrollY - 60); await wait(400);`);
      const t = await run(`const s = box(document.getElementById('trash-section')); const r = document.querySelector('#trash-list li');
        const btns = r ? [...r.querySelectorAll('button')].map(box) : [];
        return { s, btns, empty: box(document.querySelector('.trash-foot button')) };`);
      const clip = { x: Math.round(t.s.l) - 10, y: Math.round(t.s.t) - 10, width: Math.round(t.s.w) + 20, height: Math.round(t.s.h) + 20 };
      await capture("trash", clip, [
        ...(t.btns[0] ? [{ n: 1, ...before(t.btns[0]) }] : []), ...(t.btns[1] ? [{ n: 2, ...before(t.btns[1]) }] : []), { n: 3, ...before(t.empty) },
      ]);
    }
  }
} finally {
  writeFileSync(join(OUT, `meta-${PHASE}${ONLY ? "-" + ONLY : ""}.json`), JSON.stringify(meta, null, 1));
  ws.close();
  chrome.kill();
}

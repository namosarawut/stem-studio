// Render comp.html frame by frame (deterministic, 30 fps).
// Usage: node capture.mjs <videoDir> <outDir> [comma-separated seconds to preview]
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const DIR = process.argv[2];
const OUT = process.argv[3];
const ONLY = process.argv[4] ? process.argv[4].split(",").map(Number) : null;
mkdirSync(OUT, { recursive: true });
const PORT = 9335;
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(DIR, ".profile-comp")}`,
  "--hide-scrollbars", "--allow-file-access-from-files", "--no-first-run", "--no-default-browser-check",
  "--window-size=1080,1920", "about:blank",
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
try {
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1080, height: 1920, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `file://${join(DIR, "comp.html")}` });
  await sleep(2500);
  const info = Object.fromEntries(readdirSync(join(DIR, "clips")).map((c) => [c, JSON.parse(readFileSync(join(DIR, "clips", c, "info.json"), "utf8"))]));
  const total = await run(`window.CLIP_INFO = ${JSON.stringify(info)}; await document.fonts.ready; return window.TOTAL;`);
  const times = ONLY ?? Array.from({ length: Math.round(total * 30) }, (_, i) => i / 30);
  const started = Date.now();
  for (const [i, t] of times.entries()) {
    await run(`await window.renderAt(${t}); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));`);
    const { data } = await send("Page.captureScreenshot", { format: "jpeg", quality: 93 });
    const name = ONLY ? `at-${String(t).replace(".", "_")}.jpg` : `${String(i + 1).padStart(5, "0")}.jpg`;
    writeFileSync(join(OUT, name), Buffer.from(data, "base64"));
    if (!ONLY && i % 300 === 0) console.log(`frame ${i}/${times.length} (${((Date.now() - started) / 1000).toFixed(0)}s)`);
  }
  console.log("done", times.length, "frames");
} finally {
  ws.close();
  chrome.kill();
}

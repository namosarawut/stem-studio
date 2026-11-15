// Builds the Prism manual page from template.html and meta.json.
// Fills {{figure:name:wide:caption}} and {{anim:name:caption}} with the image and its
// numbered callouts (positions in % of the image, from meta.json).
// Usage: node tools/manual/build.mjs [outDir]   (default: docs/manual)
//   --fragment  writes the page without <!doctype>/<html>/<head> (for hosts that wrap it)
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragment = args.includes("--fragment");
const out = args.find((a) => !a.startsWith("--")) ?? join(here, "..", "..", "docs", "manual");
const meta = JSON.parse(readFileSync(join(here, "meta.json"), "utf8"));
let html = readFileSync(join(here, "template.html"), "utf8");
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

html = html.replace(/\{\{figure:([\w-]+):(\w*):([^}]+)\}\}/g, (_, name, wide, cap) => {
  const m = meta[name];
  const marks = (m.marks ?? []).map((k) => `<span class="mark" style="left:${k.x}%;top:${k.y}%" aria-hidden="true">${k.n}</span>`).join("");
  return `<figure${wide ? ' class="wide"' : ""}><div class="shot"><a href="img/${name}.webp" target="_blank" rel="noopener" title="เปิดภาพขนาดเต็ม"><img src="img/${name}.webp" width="${m.width}" height="${m.height}" alt="${esc(cap)}" loading="lazy"></a>${marks}</div><figcaption>${esc(cap)}</figcaption></figure>`;
});
html = html.replace(/\{\{anim:([\w-]+):([^}]+)\}\}/g, (_, name, cap) => {
  const m = meta[name];
  return `<figure class="anim"><div class="shot"><img src="img/${name}.webp" width="${m.width}" height="${m.height}" alt="${esc(cap)}" loading="lazy"><span class="badge">ภาพเคลื่อนไหว</span></div><figcaption>${esc(cap)}</figcaption></figure>`;
});
if (html.includes("{{")) throw new Error("placeholder left in template");

if (!fragment) {
  // A page of its own: open docs/manual/index.html straight from the folder.
  html = html.replace('<meta charset="utf-8">\n', "");
  html = `<!doctype html>\n<html lang="th">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n${html.replace(/<div class="wrap">/, "</head>\n<body>\n<div class=\"wrap\">")}\n</body>\n</html>\n`;
}
writeFileSync(join(out, "index.html"), html);
console.log(`built ${join(out, "index.html")} (${html.length} chars)`);

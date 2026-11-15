# Phase 2 PNG captures (2x) -> WebP for the manual, with callouts re-based after cropping.
import glob, json, sys
from pathlib import Path
from PIL import Image
HERE = Path(__file__).resolve().parent
SH = sys.argv[1] if len(sys.argv) > 1 else "shots"     # folder shots.mjs wrote into
IMG = HERE.parent.parent / "docs" / "manual" / "img"
META = HERE / "meta.json"
meta = {}
for f in sorted(glob.glob(f"{SH}/meta-2-*.json")):
    meta.update(json.load(open(f)))
site = json.load(open(META))

def still(name, crop=None, out=None, max_w=1600, drop=()):
    m = meta[name]
    im = Image.open(f"{SH}/{name}.png").convert("RGB")
    s = im.width / m["width"]
    x0, y0, x1, y1 = crop or (0, 0, m["width"], m["height"])
    im = im.crop((round(x0 * s), round(y0 * s), round(x1 * s), round(y1 * s)))
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    im.save(f"{IMG}/{out or name}.webp", quality=86, method=6)
    marks = []
    for k in m.get("marks", []):
        if k["n"] in drop:
            continue
        x, y = k["x"] / 100 * m["width"], k["y"] / 100 * m["height"]
        marks.append({"n": k["n"], "x": round((x - x0) / (x1 - x0) * 100, 2), "y": round((y - y0) / (y1 - y0) * 100, 2)})
    site[out or name] = {"width": x1 - x0, "height": y1 - y0, "marks": marks}

def bands(img, b, x0, x1, s):
    """Drum row over metronome row, the rows between left out."""
    d = img.crop((round(x0 * s), round(b["drums"][0] * s), round(x1 * s), round(b["drums"][1] * s)))
    c = img.crop((round(x0 * s), round(b["click"][0] * s), round(x1 * s), round(b["click"][1] * s)))
    out = Image.new("RGB", (d.width, d.height + c.height + 6), (42, 44, 54))
    out.paste(d, (0, 0)); out.paste(c, (0, d.height + 6))
    return out

still("metro-panel", crop=(0, 225, 1440, 1128))
still("connect")
still("steady-choose")
site["steady-choose"]["marks"] = [{"n": 1, "x": 31, "y": 43.6}, {"n": 2, "x": 28.5, "y": 55}, {"n": 3, "x": 98.2, "y": 79}]  # beside, not on, the controls
still("steady-result", drop=(1,))
still("tools-before", crop=(0, 0, 720, 60))
still("tools-after", crop=(0, 0, 720, 60))
for n in ("tools-before", "tools-after"):
    for k in site[n]["marks"]:
        k["y"] = 4  # on the top edge, clear of the button text

b = meta["repair-before"]["bands"]
for name in ("repair-before", "repair-after"):
    im = Image.open(f"{SH}/{name}.png").convert("RGB")
    s = im.width / meta["repair-before"]["width"]
    x0 = meta["repair-before"]["gapX"] - 330
    comp = bands(im, b, x0, x0 + 660, s)
    comp.save(f"{IMG}/{name}.webp", quality=86, method=6)
    site[name] = {"width": 660, "height": round(comp.height / s), "marks": []}

m = meta["beat-drag"]
frames = []
for i in range(m["frames"]):
    im = Image.open(f"{SH}/frames/beat-drag-{i:03d}.png").convert("RGB")
    s = im.width / m["width"]
    x0 = m["width"] / 2 - 300
    frames.append(bands(im, m["bands"], x0, x0 + 600, s))
durations = [m["gap"]] * len(frames)
durations[-1] = 1200
frames[0].save(f"{IMG}/beat-drag.webp", save_all=True, append_images=frames[1:], duration=durations, loop=0, quality=80, method=6)
site["beat-drag"] = {"width": 600, "height": round(frames[0].height / s), "frames": len(frames), "gap": m["gap"]}
json.dump(site, open(META, "w"))
print({k: (v["width"], v["height"]) for k, v in site.items() if k in meta or k in ("beat-drag",)})

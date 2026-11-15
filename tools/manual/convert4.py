# Phase 3 PNG captures (2x) -> WebP, cropped to the studio console, callouts re-based.
import glob, json, sys
from pathlib import Path
from PIL import Image
HERE = Path(__file__).resolve().parent
SH = sys.argv[1] if len(sys.argv) > 1 else "shots"     # folder shots.mjs wrote into
IMG = HERE.parent.parent / "docs" / "manual" / "img"
META = HERE / "meta.json"
meta = {}
for f in sorted(glob.glob(f"{SH}/meta-4-*.json")):
    meta.update(json.load(open(f)))
site = json.load(open(META))

def still(name, crop=None, max_w=1600):
    m = meta[name]
    im = Image.open(f"{SH}/{name}.png").convert("RGB")
    s = im.width / m["width"]
    x0, y0, x1, y1 = crop or (0, 0, m["width"], m["height"])
    im = im.crop((round(x0 * s), round(y0 * s), round(x1 * s), round(y1 * s)))
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    im.save(f"{IMG}/{name}.webp", quality=86, method=6)
    marks = []
    for k in m.get("marks", []):
        x, y = k["x"] / 100 * m["width"], k["y"] / 100 * m["height"]
        marks.append({"n": k["n"], "x": round((x - x0) / (x1 - x0) * 100, 2), "y": round((y - y0) / (y1 - y0) * 100, 2)})
    site[name] = {"width": round(x1 - x0), "height": round(y1 - y0), "marks": marks}

still("setlist", (0, 0, 1440, 460))
for name in ("setlist-detail", "pl-picker", "add-pl", "show", "show-locked", "show-midi", "trash"):
    still(name)
for k in site["show"]["marks"]:
    if k["n"] in (1, 2, 3, 4, 8):
        k["x"] = 0.9  # in the left margin, off the big text
json.dump(site, open(META, "w"))
print({k: (site[k]["width"], site[k]["height"], len(site[k]["marks"])) for k in meta})

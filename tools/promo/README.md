# Promo tools

Scripts that produced the screenshots, slide images and the 9:16 promo video. They drive a
private headless Google Chrome over the DevTools protocol (Node 24+, built-in `WebSocket`), so no
extra packages are needed.

Always run them against a **test server on a copy of the data**, never the real `data/`:

```bash
STEM_STUDIO_DATA=/path/to/copy/data .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8766
```

The scripts expect, in that copy, the job id and playlist id written at the top of each script
(`JOB`, `PLAYLIST`); change them to match your copy.

| Script | What it does |
|---|---|
| `screenshots.mjs <outDir> [name]` | Feature screenshots at 2× (home from `:8765` read-only, the rest from `:8766`) |
| `music.py <out.wav>` | Original 100 BPM background track, synthesised in numpy (no samples, free to use) |
| `record.mjs <videoDir> [clip]` | Records real use of the app (mouse drags, typing, clicks) as 30 fps frame sequences in `<videoDir>/clips/`. Needs `FFMPEG=<path to ffmpeg>` and `เพลงตัวอย่าง.mp3` in `<videoDir>` |
| `comp.html` | The 1080×1920 composition: scenes, camera moves, motion text. Copy it into `<videoDir>` |
| `capture.mjs <videoDir> <outDir> [seconds,...]` | Renders `comp.html` frame by frame; with a list of seconds it renders only those (layout check) |

Encode the final video:

```bash
ffmpeg -framerate 30 -i frames/%05d.jpg -i music.wav \
  -filter_complex "[0:v]scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p[v];[1:a]atrim=0:74.4,afade=t=out:st=72.6:d=1.8[a]" \
  -map "[v]" -map "[a]" -c:v libx264 -preset slow -crf 18 -profile:v high \
  -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 \
  -movflags +faststart -c:a aac -b:a 192k -shortest prism-promo-9x16.mp4
```

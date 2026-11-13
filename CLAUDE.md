# Prism — project context

Prism (repo and folder still named `stem-studio`) is a local web app for musicians, aimed at people
who prepare backing "data" / act as MD for live shows: AI stem separation, song analysis, an
editable click track, spoken cue/count-in track, lead-in bars, region cuts into new tracks, L/R
routing, export, and a setlist player. UI text is Thai; keep new UI copy in natural spoken Thai.

**New to this repo? Read [docs/CONTEXT.md](docs/CONTEXT.md) first.** It is the full handoff: hard rules
(§0, read before touching anything), architecture, data schemas, API, invariants, how to test safely,
decision history and what to work on next. This file is the short version.

Improvement backlog and known gaps: [docs/ROADMAP.md](docs/ROADMAP.md).

## Run

- `./start.command` (double-click on macOS): finds Python 3.10–3.12, (re)builds `.venv` from
  `requirements.txt` if imports fail, serves on `127.0.0.1:8765` (`STEM_STUDIO_PORT` overrides), opens
  the browser. First run downloads models into `models/` (~1.3 GB, `TORCH_HOME`).
- Manual: `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level warning`
- Code changes in `app/` need a server restart; `static/` is served with `Cache-Control: no-cache`
  (a reload is enough). Restart the main server only when `/api/jobs` shows nothing queued/running.
- macOS only in practice: Demucs uses Apple GPU (MPS) and cues use the `say` command.
- Nothing leaves the machine except the opt-in "ask Claude" button (`app/assistant.py`), which sends only
  numbers (beat and hit times, section names). Keep it that way and keep the dialog text accurate.

## Testing safely (important)

- `data/` holds the user's real songs, edits and setlists. Never delete or modify jobs, cues or
  setlists there without the user's explicit OK, and never "clean up" by deleting unknown ids.
- Test against a copy: `STEM_STUDIO_DATA=<copy>/data .venv/bin/python -m uvicorn app.main:app --port 8766`
  (`DATA_DIR` = `$STEM_STUDIO_DATA/jobs`; library and playlists sit beside it).
- There is no automated test suite. Checks used so far: `node --check static/*.js`, `python -c "import
  ast; ast.parse(...)"`, curl against the test server, and headless Chrome driven over the DevTools
  protocol with Node 24's built-in `WebSocket` (see `tools/promo/*.mjs` for working examples of
  real mouse/keyboard input, screenshots and screencasts).

## Layout

| File | Role |
|---|---|
| `app/main.py` | FastAPI app: localhost-only middleware (Host + Origin check), job queue/worker, `process()`, all `/api/*` endpoints, static mount |
| `app/separator.py` | Demucs models (`MODELS`: htdemucs, htdemucs_ft, htdemucs_6s, htdemucs_lead), `decode_audio` via imageio-ffmpeg, lead/backing split with MelBand Roformer (audio-separator) |
| `app/analyzer.py` | Tempo/beat tracking, key, chords, sections, loops, loudness, per-stem stats; `ANALYSIS_VERSION = 2` |
| `app/tempo.py` | Piecewise tempo map (steady / accel / ritard sections) + summary |
| `app/project.py` | Non-destructive edit model: click grid, cuts, custom tracks, lead-in, merged view for the UI |
| `app/cues.py` | Cue library (135 phrases, 6 categories), count-in styles, `say` TTS cache, cue track render |
| `app/metronome.py` | `write_wav_atomic`, click track render |
| `app/midi.py` | MIDI tempo map export (tempo per beat, time signatures, section markers, click notes) |
| `app/assistant.py` | Optional "ask Claude" (opt-in, sends only beat/hit times and section names): Claude Code CLI (`claude -p`, built-in tools off) or the Claude API, editing a copy of the metronome through 5 tools |
| `app/bench_mcp.py` | Minimal stdio MCP server that serves those 5 tools to `claude -p` |
| `app/gridfix.py` | Metronome repair from the drum stem ("ซ่อมจุดที่เพี้ยน"): rebuilds spots that slipped off the drums |
| `app/warp.py` | Tempo lock: beat time map, kink filtering, WSOLA stretch with one shared plan for all tracks |
| `app/monitor.py` | Per-job resource report (CPU, RAM, GPU via `ioreg`, disk) |
| `app/playlists.py` | Setlists, imported files (`data/library`), server-rendered project mixes for playback |
| `static/app.js` | Router, home, processing view, studio (Mixer class, timeline canvas, metronome editor, cues, cuts, lead-in, routing, export) |
| `static/playlist.js` | Setlist page, gapless player (`plPlayer`), add-to-setlist dialog from the studio |
| `tools/promo/` | Scripts that made the promo screenshots, deck images and 9:16 video |

## Data on disk

- `data/jobs/<id>/`: `meta.json` (`trim: {start, end|null}` when only part of the upload was separated), `source.*`, `stems/*.wav` (never modified after separation),
  `analysis.json`, `edits.json`, `edited/` (cut results), `warped/` (tempo-locked tracks), `padded/` (stems with lead-in silence),
  `cues_render.json`, `tempo.mid` (rebuilt on each download), `playback/<key>.wav|json` (setlist mixes, newest 6 kept), `stems.zip` (cache,
  deleted whenever edits change export content).
- `data/library/<id>/`: imported file as `audio.wav` + `meta.json` (with peaks).
- `data/playlists/<id>.json`: `{name, auto_advance, gap, items: [{id, kind: file|project, ref, title,
  start, end|null, stop_after, fade_in, fade_out, mix?: {muted: [...], pan: {stem: L|R}}}]}`.

## Invariants and gotchas

- **Time bases.** `edits.json` stores song time (of the original stems). The API and UI use timeline
  time = warp(song time) + lead-in, where warp is the tempo lock's time map (identity when unlocked).
  Convert only with `project.to_timeline` / `project.to_song`, at the API boundary.
- **Tempo lock** (`edits.warp`): render chain is stems → edited/ (cuts) → warped/ (lock) → padded/
  (lead-in). While locked the metronome cannot be edited (409) and the grid is steady at the bpm.
- **Every served track has the same length** (`timeline_samples`), so exports line up at 0 in a DAW.
- **Atomic writes everywhere** (`write_wav_atomic`, `project._swap_dir`, `playlists.write_json_atomic`):
  a browser may be downloading a file while it is rebuilt.
- **Analysis upgrades** re-run analysis when `version < ANALYSIS_VERSION` and keep edits.
- **Beat tracking:** a per-song hop that divides the beat exactly (librosa rounds frames per beat);
  a tempo curve is accepted only if it lands ≥1.5× better on hits; beats are snapped to attacks.
- **Count-ins** lead into the beat nearest the cue (within 0.4 × beat gap) so they survive grid edits.
- **L/R routing:** L or R folds the track to mono as (L+R)/2 so centred sounds keep their level.
  Same rule in the browser (`makeRoute`) and the server (`playlists.render_mix`).
- **Export names** are instrument labels only (`drums.wav`, custom track names); the ZIP is named
  after the song. Duplicate labels get the stem id appended.
- **Storage keys / env vars keep the old name** after the rename to Prism: `stem-studio:pan:<job>`,
  `stem-studio-cue-voice`, `STEM_STUDIO_DATA`, `STEM_STUDIO_PORT`. Renaming them loses user settings.
- Front end is vanilla JS, no build step: `el()` and `api()` helpers, hash router in `route()`, view
  names in `show()`, per-view listeners on an `AbortController` cleaned up via `leaveView`.

## Repo rules

- `data/`, `models/`, `.venv/`, `.claude/` are git-ignored. Never commit audio or user data.
- Commit or push only when the user asks. Commit messages carry no AI attribution: no
  `Co-Authored-By: Claude …` trailer and no "Generated with Claude Code" line (the user's rule).
- The user writes in Thai; answer in Thai.

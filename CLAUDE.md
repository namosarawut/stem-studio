# Prism — project context

Prism (repo and folder still named `stem-studio`) is a local web app for musicians, aimed at people
who prepare backing "data" / act as MD for live shows: AI stem separation, song analysis, an
editable click track, spoken cue/count-in track, lead-in bars, region cuts into new tracks, output
routing, export, and a setlist player. UI text is Thai; keep new UI copy in natural spoken Thai.

**New to this repo? Read [docs/CONTEXT.md](docs/CONTEXT.md) first.** It is the full handoff: hard rules
(§0, read before touching anything), architecture, data schemas, API, invariants, how to test safely,
decision history and what to work on next. This file is the short version.

Improvement backlog and known gaps: [docs/ROADMAP.md](docs/ROADMAP.md).

## Run

- `./start.command` (double-click on macOS) or `start.bat` (Windows; installs CUDA PyTorch when `nvidia-smi` exists): finds Python 3.10–3.12, (re)builds `.venv` from
  `requirements.txt` if imports fail, serves on `127.0.0.1:8765` (`STEM_STUDIO_PORT` overrides), opens
  the browser. First run downloads models into `models/` (~1.3 GB, `TORCH_HOME`).
- Manual: `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level warning`
- Code changes in `app/` need a server restart; `static/` is served with `Cache-Control: no-cache`
  (a reload is enough). Restart the main server only when `/api/jobs` shows nothing queued/running.
- macOS and Windows. Demucs runs on MPS (Mac), CUDA (NVIDIA) or CPU. Cues use `say` on macOS and
  `app/winspeech.ps1` (WinRT/SAPI voices via Windows PowerShell 5.1) on Windows. A cue whose voice is not
  installed falls back to an installed voice of the same language (`cues.voice_info`); Windows voice ids are
  `win-<lang>-<name>`. `.github/workflows/windows.yml` runs `tools/ci/smoke_test.py` on Windows on every push;
  run the same script locally after touching file swaps, cues or the job pipeline.
- Prism itself sends nothing anywhere. Claude can work on songs through the MCP server (`app/mcp_server.py`,
  set up from "เชื่อมกับ Claude" on the home page); what Claude sees is only what its tools return (titles,
  sections, cues, beat and drum/bass hit times), never audio. Keep it that way and keep the dialog text accurate.
- MCP changes go through the same HTTP API as the page, tagged `X-Prism-Client: claude`. The server logs who
  changed each song (`note_change`, `GET /api/jobs/{id}/changes`); an open song page shows "Claude แก้เพลงนี้แล้ว"
  with a reload button. The page tags its own requests with a random `PAGE_ID`. New tools: add them to `TOOLS` in
  `mcp_server.py` (standard library only) and test with a stdio client against a copy server.

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
| `app/project.py` | Non-destructive edit model: click grid, cuts, custom tracks, sounds added from files, lead-in, merged view for the UI |
| `app/cues.py` | Cue library (135 phrases, 6 categories), count-in styles, TTS cache (`say` / `winspeech.ps1`), cue track render |
| `app/fsutil.py` | File swaps that also work on Windows (retry while a file is open, `.stale` marker for caches) |
| `app/metronome.py` | `write_wav_atomic`, click track render |
| `app/midi.py` | MIDI tempo map export (tempo per beat, time signatures, section markers, click notes) |
| `app/mcp_server.py` | MCP server (stdio, standard library) so Claude Desktop / Claude Code can pick a song (elicitation picker) and fix its metronome, cues, lead-in and tempo lock through the running server's API, with undo |
| `app/clickcheck.py` | Read-only metronome checks for MCP (`GET /api/jobs/{id}/click/inspect`): beats vs drum hits, odd gaps, odd bars, lock check |
| `app/gridfix.py` | Metronome repair from the drum stem ("ซ่อมจุดที่เพี้ยน"): rebuilds spots that slipped off the drums |
| `app/warp.py` | Tempo lock: beat time map, kink filtering, WSOLA stretch with one shared plan for all tracks |
| `app/trash.py` | Deletes go to `data/trash/<entry>/` (item + trash.json) for 7 days; restore, purge on startup and on listing |
| `app/monitor.py` | Per-job resource report (CPU, RAM, GPU via `ioreg` or `nvidia-smi`, disk) |
| `app/playlists.py` | Setlists, imported files (`data/library`), server-rendered project mixes for playback |
| `static/app.js` | Router, home, processing view, studio (Mixer class, timeline canvas, metronome editor, cues, cuts, lead-in, routing, export) |
| `static/show.js` | Show mode for the setlist player: full screen, footswitch keys, Web MIDI learn (`prism-show-midi` in localStorage), screen lock, wake lock, leave guard, instrument switches for the whole set (`prism-show-mix:<list>` in localStorage, applied through `plEffectiveMix`, live via `plPlayer.replan`). State object is `stage` (app.js already has `show()`) |
| `static/playlist.js` | Setlist page, gapless player (`plPlayer`), add-to-setlist dialog from the studio |
| `tools/promo/` | Scripts that made the promo screenshots, deck images and 9:16 video |
| `docs/manual/` | User manual in Thai (4 phases, screenshots and animations), a static page: open `index.html`, or `/manual/` on the server (the "คู่มือ" button on the home page). GitHub Pages publishes `docs/` from `main` (https://namosarawut.github.io/stem-studio/manual/; `docs/index.html` redirects there), so anything in `docs/` is public |
| `tools/manual/` | Manual source (`template.html`, `meta.json`) and the scripts that build it and capture its images. When UI text or controls change, update the manual too |

## Data on disk

- `data/jobs/<id>/`: `meta.json` (`trim: {start, end|null}` when only part of the upload was separated), `source.*`, `stems/*.wav` (never modified after separation),
  `analysis.json`, `edits.json`, `edited/` (cut results), `added/` (sounds added from files: `<id>.src.wav` as decoded, `<id>.wav` placed on the song), `warped/` (tempo-locked tracks), `padded/` (stems with lead-in silence),
  `cues_render.json`, `tempo.mid` (rebuilt on each download), `playback/<key>.wav|json` (setlist mixes, newest 6 kept), `stems.zip` (cache,
  deleted whenever edits change export content).
- `data/trash/<kind>-<id>-<rand>/`: a deleted song, import or setlist (`item`) plus `trash.json`; purged after 7 days.
- `data/library/<id>/`: imported file as `audio.wav` + `meta.json` (with peaks).
- `data/playlists/<id>.json`: `{name, auto_advance, gap, items: [{id, kind: file|project, ref, title,
  start, end|null, stop_after, fade_in, fade_out, mix?: {muted: [...], pan: {stem: L|R|"3-4"|"3"…}, gain?: {stem: dB}}}]}`
  (gain -60..+12 dB, 0 dB left out so old mixes keep their render cache).

## Invariants and gotchas

- **Time bases.** `edits.json` stores song time (of the original stems). The API and UI use timeline
  time = warp(song time) + lead-in, where warp is the tempo lock's time map (identity when unlocked).
  Convert only with `project.to_timeline` / `project.to_song`, at the API boundary.
- **Tempo lock** (`edits.warp`): render chain is stems → edited/ (cuts) → warped/ (lock) → padded/
  (lead-in). While locked the metronome cannot be edited (409) and the grid is steady at the bpm.
- **Sounds added from files** (`edits.tracks[]` with `kind: "file"`, id `added_…`, `offset` in song time, may be negative
  = lead-in) are placed, never stretched: `build_warped` puts them at warp(offset), `build_padded` at the
  timeline position. Cuts from or into them are refused.
- **Every served track has the same length** (`timeline_samples`), so exports line up at 0 in a DAW.
- **Atomic writes everywhere** (`write_wav_atomic`, `project._swap_dir`, `playlists.write_json_atomic`):
  a browser may be downloading a file while it is rebuilt.
  Swap with `fsutil.replace`, never `os.replace`/`Path.replace` directly: Windows refuses to replace an open
  file, so it retries there. Drop caches with `fsutil.discard` (+ `fsutil.is_current`), not `unlink`.
- **Analysis upgrades** re-run analysis when `version < ANALYSIS_VERSION` and keep edits.
- **Beat tracking:** a per-song hop that divides the beat exactly (librosa rounds frames per beat);
  a tempo curve is accepted only if it lands ≥1.5× better on hits; beats are snapped to attacks.
- **Count-ins** lead into the beat nearest the cue (within 0.4 × beat gap) so they survive grid edits.
- **Outputs (routing):** each track goes to an output of the sound card: `C` = Out 1-2 (default), `L`/`R` =
  Out 1/2 mono, `"3-4"`, `"5-6"`… = a stereo pair, `"3"`… = one channel (up to 16). The first pair keeps the
  old L/C/R names so saved songs, setlists and render caches are unchanged (`playlists.clean_output`,
  `outCanonical` in app.js). A single channel folds the track to mono as (L+R)/2 so centred sounds keep their level.
  The page opens every output (`useAllOutputs`, discrete channels, `ChannelMerger`); setlist mixes render one WAV
  channel per output used. An output the current card lacks plays nothing (never folded into the mains) and the
  page warns. Studio "ส่งออกมิกซ์" is what plays on Out 1-2. The controls switch between L/C/R buttons and an
  output list (`routeMode`, `prism-route-mode` in localStorage, shared by song page and setlists); switching never
  changes a track's output.
  Same rule in the browser (`makeRoute`) and the server (`playlists.render_mix`).
- **Export names** are instrument labels only (`drums.wav`, custom track names); the ZIP is named
  after the song. Duplicate labels get the stem id appended.
- **Storage keys / env vars keep the old name** after the rename to Prism: `stem-studio:pan:<job>`,
  `stem-studio-cue-voice`, `STEM_STUDIO_DATA`, `STEM_STUDIO_PORT`. Renaming them loses user settings.
- **Deletes are soft.** `DELETE` on a song, import or setlist moves it to the trash; only the trash endpoints
  delete for good. Never `rmtree` user data directly.
- **Out-of-date pages:** `/api/version` changes on restart or any `static/` change; the page polls it and
  shows a reload bar.
- Front end is vanilla JS, no build step: `el()` and `api()` helpers, hash router in `route()`, view
  names in `show()`, per-view listeners on an `AbortController` cleaned up via `leaveView`.

## Repo rules

- `data/`, `models/`, `.venv/`, `.claude/` are git-ignored. Never commit audio or user data.
- Commit or push only when the user asks. Commit messages carry no AI attribution: no
  `Co-Authored-By: Claude …` trailer and no "Generated with Claude Code" line (the user's rule).
- The user writes in Thai; answer in Thai.

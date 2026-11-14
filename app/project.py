"""A song's hand edits: a corrected metronome and tracks cut out of other stems.

Cuts are stored as an ordered list of operations and rendered from the untouched files
in stems/ into edited/, so any cut can be undone without loss. Each cut uses a matching
crossfade, which means the new track plus what is left of the source equals the original.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from . import warp
from .analyzer import _peaks
from .metronome import write_wav_atomic
from .tempo import summary as tempo_summary
from .tempo import tempo_map

EDITS_FILE = "edits.json"
EDITED_DIR = "edited"
PEAKS_FILE = "peaks.json"
CLICK = "click"
CUES = "cues"
GUIDES = (CLICK, CUES)
CUE_RENDER_FILE = "cues_render.json"
PADDED_DIR = "padded"
WARPED_DIR = "warped"
ADDED_DIR = "added"          # sounds brought in from files: <id>.src.wav as decoded, <id>.wav placed on the song
ADDED_PREFIX = "added_"
MAX_LEAD_BARS = 16
FADE_SECONDS = 0.01
MIN_REGION_SECONDS = 0.02
MIN_BEAT_GAP_SECONDS = 0.03
MAX_TRACKS = 24
MAX_OPS = 500
MAX_BEATS = 20000
SILENCE = 1e-4
# Fixed order, so a track keeps its color however many tracks exist around it.
TRACK_COLORS = ["#c9a0ff", "#6fe3e1", "#ffd28a", "#b7e36b", "#ff9d7a", "#9fc5ff"]


class EditError(ValueError):
    """A request that cannot be applied. The message is shown to the person."""


def load_edits(folder: Path) -> dict:
    path = folder / EDITS_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"click": None, "tracks": [], "ops": [], "colors_used": 0}


def save_edits(folder: Path, edits: dict) -> None:
    path = folder / EDITS_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(edits, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _swap_dir(building: Path, target: Path) -> None:
    """Put a freshly built folder in place with renames, never leaving a moment where the
    folder is missing. Files already open for download stay readable until closed."""
    retired = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.old")
    if target.exists():
        target.replace(retired)
    building.replace(target)
    shutil.rmtree(retired, ignore_errors=True)


def stem_path(folder: Path, stem: str) -> Path:
    if stem.startswith(ADDED_PREFIX):
        return folder / ADDED_DIR / f"{stem}.wav"
    edited = folder / EDITED_DIR / f"{stem}.wav"
    return edited if edited.exists() else folder / "stems" / f"{stem}.wav"


def _mask(samples: int, start: float, end: float, sample_rate: int) -> np.ndarray:
    a, b = int(round(start * sample_rate)), min(samples, int(round(end * sample_rate)))
    mask = np.zeros(samples, dtype=np.float32)
    mask[a:b] = 1.0
    fade = min(int(FADE_SECONDS * sample_rate), (b - a) // 2)
    if fade > 0:
        ramp = (np.arange(fade, dtype=np.float32) + 0.5) / fade
        mask[a:a + fade] = ramp
        mask[b - fade:b] = ramp[::-1]
    return mask[:, None]


def render(folder: Path, edits: dict, base_stems: list[str], sample_rate: int) -> None:
    """Rebuild edited/ from the originals by replaying every cut, oldest first."""
    target_dir = folder / EDITED_DIR
    ops = edits["ops"]
    touched = {op["source"] for op in ops} | {op["target"] for op in ops}
    samples = sf.info(folder / "stems" / f"{base_stems[0]}.wav").frames
    current: dict[str, np.ndarray] = {}
    for stem in touched:
        if stem in base_stems:
            current[stem], _ = sf.read(folder / "stems" / f"{stem}.wav", dtype="float32", always_2d=True)
        else:
            current[stem] = np.zeros((samples, 2), dtype=np.float32)
    for op in ops:
        mask = _mask(samples, op["start"], op["end"], sample_rate)
        moved = current[op["source"]] * mask
        current[op["target"]] += moved
        current[op["source"]] -= moved

    # Write beside the old files, then swap the folder, so a failure leaves the old result.
    building = folder / f"{EDITED_DIR}.building"
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir()
    peaks = {}
    for stem, audio in current.items():
        sf.write(building / f"{stem}.wav", np.clip(audio, -1.0, 1.0), sample_rate, subtype="PCM_16")
        peaks[stem] = _peaks(audio.T, sample_rate)
    (building / PEAKS_FILE).write_text(json.dumps(peaks), encoding="utf-8")
    if touched:
        _swap_dir(building, target_dir)
    else:
        shutil.rmtree(building, ignore_errors=True)
        shutil.rmtree(target_dir, ignore_errors=True)


def _clean_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", "", name or "")).strip()
    if not cleaned:
        raise EditError("กรุณาตั้งชื่อแทร็ก")
    if len(cleaned) > 40:
        raise EditError("ชื่อแทร็กยาวเกิน 40 ตัวอักษร")
    return cleaned


def _track(edits: dict, track_id: str) -> dict | None:
    return next((t for t in edits["tracks"] if t["id"] == track_id), None)


def _root_of(edits: dict, stem: str) -> str:
    track = _track(edits, stem)
    return track["root"] if track else stem


def add_cut(folder: Path, edits: dict, base_stems: list[str], duration: float, sample_rate: int,
            source: str, start: float, end: float, name: str | None, target: str | None) -> dict:
    """Move a region into a new or existing track. Returns the affected stem ids."""
    if source != CLICK and source not in base_stems and not _track(edits, source):
        raise EditError("ไม่พบแทร็กต้นทาง")
    if source == CLICK:
        raise EditError("ตัดจากแทร็กเมโทรนอมไม่ได้")
    if is_added(edits, source) or (target and is_added(edits, target)):
        raise EditError("แทร็กที่เพิ่มจากไฟล์ตัดช่วงไม่ได้ ถ้าต้องการเปลี่ยน ลบแล้วเพิ่มใหม่")
    start, end = max(0.0, float(start)), min(duration, float(end))
    if not np.isfinite(start) or not np.isfinite(end) or end - start < MIN_REGION_SECONDS:
        raise EditError("ช่วงที่เลือกสั้นเกินไป")
    if len(edits["ops"]) >= MAX_OPS:
        raise EditError("ตัดครบจำนวนสูงสุดแล้ว")

    with sf.SoundFile(stem_path(folder, source)) as audio:
        audio.seek(int(start * sample_rate))
        region = audio.read(int((end - start) * sample_rate), dtype="float32", always_2d=True)
    if region.size == 0 or np.abs(region).max() < SILENCE:
        raise EditError("ช่วงนี้ไม่มีเสียงในแทร็กต้นทาง")

    if target:
        if not _track(edits, target):
            raise EditError("ไม่พบแทร็กปลายทาง")
        if target == source:
            raise EditError("แทร็กต้นทางและปลายทางเป็นแทร็กเดียวกัน")
    else:
        if len(edits["tracks"]) >= MAX_TRACKS:
            raise EditError(f"สร้างแทร็กได้สูงสุด {MAX_TRACKS} แทร็ก")
        target = f"custom_{uuid.uuid4().hex[:8]}"
        edits["tracks"].append({
            "id": target,
            "name": _clean_name(name or ""),
            "color": TRACK_COLORS[edits.get("colors_used", 0) % len(TRACK_COLORS)],
            "root": _root_of(edits, source),
        })
        edits["colors_used"] = edits.get("colors_used", 0) + 1
    edits["ops"].append({"source": source, "target": target, "start": round(start, 3), "end": round(end, 3)})
    return {"changed": [source, target], "target": target}


def _drop_empty_tracks(edits: dict) -> list[str]:
    """Cut tracks nothing is cut into any more. Tracks added from a file always stay."""
    used = {op["target"] for op in edits["ops"]}
    dropped = [t["id"] for t in edits["tracks"] if t["id"] not in used and t.get("kind") != "file"]
    edits["tracks"] = [t for t in edits["tracks"] if t["id"] not in dropped]
    return dropped


def remove_track(edits: dict, track_id: str) -> list[str]:
    """Return a track's audio to where it came from, including anything cut out of it."""
    if not _track(edits, track_id):
        raise EditError("ไม่พบแทร็กนี้")
    changed, gone = set(), {track_id}
    while gone:
        removed = [op for op in edits["ops"] if op["target"] in gone or op["source"] in gone]
        edits["ops"] = [op for op in edits["ops"] if op not in removed]
        for op in removed:
            changed |= {op["source"], op["target"]}
        edits["tracks"] = [t for t in edits["tracks"] if t["id"] != track_id]
        gone = set(_drop_empty_tracks(edits))
        changed |= gone
    return sorted(changed)


def undo_last_cut(edits: dict) -> list[str]:
    if not edits["ops"]:
        raise EditError("ไม่มีการตัดให้ย้อนกลับ")
    op = edits["ops"].pop()
    # Cuts made from a track that no longer exists cannot be replayed.
    changed = {op["source"], op["target"]}
    while True:
        dropped = _drop_empty_tracks(edits)
        if not dropped:
            return sorted(changed)
        changed |= set(dropped)
        orphans = [o for o in edits["ops"] if o["source"] in dropped]
        edits["ops"] = [o for o in edits["ops"] if o not in orphans]
        for orphan in orphans:
            changed |= {orphan["source"], orphan["target"]}


def rename_track(edits: dict, track_id: str, name: str) -> None:
    track = _track(edits, track_id)
    if not track:
        raise EditError("ไม่พบแทร็กนี้")
    track["name"] = _clean_name(name)


def clean_click_grid(beats: list[float], downbeats: list[float], meter: int, duration: float, start: float = 0.0) -> dict:
    if not 1 <= int(meter) <= 12:
        raise EditError("อัตราจังหวะไม่ถูกต้อง")
    if not beats or len(beats) > MAX_BEATS:
        raise EditError("จำนวนจังหวะไม่ถูกต้อง")
    values = [float(t) for t in beats]
    if not all(np.isfinite(t) and start - 0.001 <= t <= duration for t in values):
        raise EditError("มีจังหวะอยู่นอกความยาวเพลง")
    ordered = sorted(round(t, 3) for t in values)
    if any(b - a < MIN_BEAT_GAP_SECONDS for a, b in zip(ordered[:-1], ordered[1:])):
        raise EditError("มีจังหวะที่อยู่ชิดกันเกินไป")
    known = set(ordered)
    accents = sorted({round(float(t), 3) for t in downbeats} & known)
    return {"beats": ordered, "downbeats": accents, "meter": int(meter)}


def active_stems(job: dict) -> list[str]:
    """The separated stems (not guides, cut tracks or added sounds)."""
    return [s for s in job.get("stems", []) if s not in GUIDES and not s.startswith(("custom_", ADDED_PREFIX))]


# ---------------------------------------------------------------- sounds added from files
#
# A sound brought in from a file (a sample, a part recorded elsewhere, a sound effect) is
# stored as decoded and placed on the song at `offset` (song time; negative = in the
# lead-in). It is never stretched: with a tempo lock it moves with the song to where that
# moment lands, but plays at its own speed, since it was usually made at the locked tempo.

def is_added(edits: dict, stem: str) -> bool:
    track = _track(edits, stem)
    return bool(track and track.get("kind") == "file")


def _place(source: Path, start: int, length: int, out: Path, sample_rate: int) -> np.ndarray:
    """Write `source` into silence of `length` samples starting at `start` (may be negative
    or run past the end: the outside part is left out). Returns the written audio."""
    audio, _ = sf.read(source, dtype="float32", always_2d=True)
    placed = np.zeros((length, 2), dtype=np.float32)
    a, b = max(0, start), min(length, start + len(audio))
    if b > a:
        placed[a:b] = audio[a - start: b - start, :2] if audio.shape[1] >= 2 else np.repeat(audio[a - start: b - start], 2, axis=1)
    write_wav_atomic(out, placed, sample_rate)
    return placed


def add_sound(folder: Path, edits: dict, name: str, audio: np.ndarray, offset: float, sample_rate: int) -> dict:
    """Add a decoded sound (2 × samples) as a new track starting at `offset` (song time)."""
    if len(edits["tracks"]) >= MAX_TRACKS:
        raise EditError(f"มีแทร็กได้สูงสุด {MAX_TRACKS} แทร็ก")
    if not np.isfinite(offset):
        raise EditError("ตำแหน่งไม่ถูกต้อง")
    track_id = f"{ADDED_PREFIX}{uuid.uuid4().hex[:8]}"
    (folder / ADDED_DIR).mkdir(exist_ok=True)
    write_wav_atomic(folder / ADDED_DIR / f"{track_id}.src.wav", np.clip(audio.T, -1.0, 1.0), sample_rate)
    track = {
        "id": track_id, "name": _clean_name(name), "kind": "file", "offset": round(float(offset), 3),
        "length": round(audio.shape[1] / sample_rate, 3),
        "color": TRACK_COLORS[edits.get("colors_used", 0) % len(TRACK_COLORS)], "root": None,
    }
    edits["tracks"].append(track)
    edits["colors_used"] = edits.get("colors_used", 0) + 1
    render_added(folder, track, sample_rate)
    return track


def render_added(folder: Path, track: dict, sample_rate: int) -> None:
    """The sound placed on the song (song time, song length), and its waveform overview."""
    reference = next(p for p in sorted((folder / "stems").glob("*.wav")) if p.stem not in GUIDES)
    frames = sf.info(reference).frames
    out = folder / ADDED_DIR / f"{track['id']}.wav"
    placed = _place(folder / ADDED_DIR / f"{track['id']}.src.wav", int(round(track["offset"] * sample_rate)), frames, out, sample_rate)
    (folder / ADDED_DIR / f"{track['id']}.peaks.json").write_text(json.dumps(_peaks(placed.T, sample_rate)), encoding="utf-8")


def remove_sound(folder: Path, edits: dict, track_id: str) -> None:
    edits["tracks"] = [t for t in edits["tracks"] if t["id"] != track_id]
    for path in (folder / ADDED_DIR).glob(f"{track_id}.*"):
        path.unlink(missing_ok=True)


def stems_after_edit(job: dict, edits: dict) -> list[str]:
    guides = [g for g in GUIDES if g in job.get("stems", [])]
    return [*active_stems(job), *[t["id"] for t in edits["tracks"]], *guides]


def click_grid(analysis: dict, edits: dict) -> dict:
    """The beat grid the metronome plays, hand-edited or as analyzed."""
    if edits.get("click"):
        return edits["click"]
    meter = int(str(analysis.get("meter", "4/4")).split("/")[0])
    return {"beats": analysis["tempo"]["beats"], "downbeats": analysis["tempo"]["downbeats"], "meter": meter}


# ---------------------------------------------------------------- lead-in
#
# A lead-in is silence added before the song so players have time to count in. Everything
# is stored in song time (0 = first sample of the song, the lead-in is negative); the API
# shifts times by the lead-in, so the page and every exported file share one timeline.

def lead_seconds(edits: dict) -> float:
    return float((edits.get("lead_in") or {}).get("seconds", 0.0))


def lead_samples(edits: dict, sample_rate: int) -> int:
    return int(round(lead_seconds(edits) * sample_rate))


def _period(beats: list[float]) -> float:
    gaps = np.diff(sorted(beats)[:17])
    gaps = gaps[gaps > MIN_BEAT_GAP_SECONDS]
    return float(np.median(gaps)) if len(gaps) else 0.5


def compute_lead(grid: dict, bars: int, sample_rate: int) -> float:
    """Length of `bars` bars at the song's opening tempo, trimmed so the timeline starts
    exactly on a bar line, and rounded to a whole sample so every track lines up."""
    if bars <= 0:
        return 0.0
    bar = _period(grid["beats"]) * grid["meter"]
    downs = sorted(d for d in grid["downbeats"] if d >= 0) or sorted(grid["beats"])[:1] or [0.0]
    seconds = bars * bar - (downs[0] % bar)
    return round(seconds * sample_rate) / sample_rate


# ---------------------------------------------------------------- tempo lock
#
# A locked song is stretched so every beat lasts 60 / bpm seconds (see warp.py). The hand
# edits stay in song time; locked time is song time passed through the warp, and the
# playback timeline is locked time plus the lead-in. edits["warp"] keeps the time map it
# was made with, so the lock never shifts if the analysis or the algorithm changes later.

def warp_of(edits: dict) -> tuple[np.ndarray, np.ndarray] | None:
    lock = edits.get("warp")
    if not lock:
        return None
    return np.asarray(lock["src"], dtype=np.float64), np.asarray(lock["dst"], dtype=np.float64)


def to_locked(edits: dict, t):
    """Song time to locked time (the same thing when the song is not locked)."""
    mapping = warp_of(edits)
    if mapping is None:
        return t
    out = warp.forward(t, *mapping)
    return float(out) if np.ndim(out) == 0 else out


def from_locked(edits: dict, t):
    mapping = warp_of(edits)
    if mapping is None:
        return t
    out = warp.backward(t, *mapping)
    return float(out) if np.ndim(out) == 0 else out


def to_timeline(edits: dict, t: float) -> float:
    """Song time (as stored in edits.json) to the playback timeline."""
    return float(to_locked(edits, t)) + lead_seconds(edits)


def to_song(edits: dict, t: float) -> float:
    """Playback timeline to song time."""
    return float(from_locked(edits, t - lead_seconds(edits)))


def lock_tempo(analysis: dict, edits: dict, bpm: float) -> dict:
    """Store the time map that makes the current metronome steady at `bpm`."""
    grid = click_grid(analysis, edits)
    src, dst = warp.anchors(grid["beats"], bpm)
    beats = sorted(grid["beats"])
    edits["warp"] = {
        "bpm": round(float(bpm), 3), "src": [round(float(t), 6) for t in src], "dst": [round(float(t), 6) for t in dst],
        "beats": beats, "downbeats": sorted(grid["downbeats"]), "meter": grid["meter"],
        "range": warp.stretch_range(beats, bpm),
    }
    return edits["warp"]


LISTEN_STRETCH = 0.10   # a lock that stretches a stretch of beats more than this is worth hearing


def listen_spots(edits: dict) -> list[dict]:
    """Where a locked song is worth checking by ear, on the timeline: stretches the lock
    moved by more than LISTEN_STRETCH, and bars that are not `meter` beats long."""
    lock = edits.get("warp")
    if not lock:
        return []
    from .gridfix import problem_spots  # gridfix imports project-free modules only

    beats = np.asarray(lock["beats"], dtype=np.float64)
    spots = [{"time": round(to_timeline(edits, float(beats[lo])), 3), "end": round(to_timeline(edits, float(beats[hi])), 3),
              "why": f"เสียงถูกยืดหรือหด {round(worst * 100)}%"}
             for lo, hi, worst in problem_spots(beats, lock["bpm"], LISTEN_STRETCH, join=8)]
    downs = {round(d, 3) for d in lock["downbeats"]}
    bars = [i for i, b in enumerate(lock["beats"]) if round(b, 3) in downs]
    for a, b in zip(bars, bars[1:]):
        if b - a != lock["meter"]:
            t = round(to_timeline(edits, float(beats[a])), 3)
            spots.append({"time": t, "end": round(to_timeline(edits, float(beats[b])), 3), "why": f"ห้องนี้มี {b - a} จังหวะ"})
    return sorted(spots, key=lambda s: s["time"])


def base_grid(analysis: dict, edits: dict) -> dict:
    """The metronome in locked time: steady at the locked tempo, else as edited/analyzed."""
    lock = edits.get("warp")
    if not lock:
        return click_grid(analysis, edits)
    period = 60.0 / lock["bpm"]
    first = lock["beats"][0]
    index = {round(b, 3): i for i, b in enumerate(lock["beats"])}
    steady = [round(first + i * period, 6) for i in range(len(lock["beats"]))]
    downs = [steady[index[round(d, 3)]] for d in lock["downbeats"] if round(d, 3) in index]
    return {"beats": steady, "downbeats": downs, "meter": lock["meter"]}


def song_samples(folder: Path, edits: dict, sample_rate: int) -> int:
    """Length of every song track after the lock, before the lead-in."""
    reference = next(p for p in sorted((folder / "stems").glob("*.wav")) if p.stem not in GUIDES)
    frames = sf.info(reference).frames
    mapping = warp_of(edits)
    return frames if mapping is None else warp.locked_samples(frames, *mapping, sample_rate)


def build_warped(folder: Path, stems: list[str], edits: dict, sample_rate: int) -> None:
    """Locked copies of the song tracks (after cuts), all from one shared stretch plan."""
    target = folder / WARPED_DIR
    mapping = warp_of(edits)
    if mapping is None:
        shutil.rmtree(target, ignore_errors=True)
        return
    building = folder / f"{WARPED_DIR}.building"
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir()
    sources = {stem: stem_path(folder, stem) for stem in stems if not is_added(edits, stem)}
    peaks = warp.warp_files(sources, building, *mapping, sample_rate, write_wav_atomic, _peaks)
    length = song_samples(folder, edits, sample_rate)
    for stem in stems:
        if is_added(edits, stem):
            start = int(round(float(to_locked(edits, _track(edits, stem)["offset"])) * sample_rate))
            placed = _place(folder / ADDED_DIR / f"{stem}.src.wav", start, length, building / f"{stem}.wav", sample_rate)
            peaks[stem] = _peaks(placed.T, sample_rate)
    (building / PEAKS_FILE).write_text(json.dumps(peaks), encoding="utf-8")
    _swap_dir(building, target)


def song_track_path(folder: Path, stem: str, edits: dict) -> Path:
    """A song track after cuts and the tempo lock, before the lead-in."""
    locked = folder / WARPED_DIR / f"{stem}.wav"
    if edits.get("warp") and locked.exists():
        return locked
    return stem_path(folder, stem)


def first_downbeat(grid: dict) -> float:
    downs = sorted(d for d in grid["downbeats"] if d >= -0.001)
    return downs[0] if downs else min(grid["beats"])


def timeline_grid(analysis: dict, edits: dict) -> dict:
    """The metronome grid on the playback timeline, carried back through the lead-in."""
    grid = base_grid(analysis, edits)
    lead = lead_seconds(edits)
    beats = sorted(grid["beats"])
    downs = {round(d, 3) for d in grid["downbeats"]}
    if lead > 0 and beats:
        period = _period(beats)
        extra = []
        t = beats[0] - period
        while t >= -lead - 0.002:
            extra.append(round(t, 3))
            t -= period
        beats = [*reversed(extra), *beats]
        first = next((i for i, b in enumerate(beats) if round(b, 3) in downs), None)
        if first is not None:
            for i in range(first):
                if (first - i) % grid["meter"] == 0:
                    downs.add(round(beats[i], 3))
    shift = lambda t: round(t + lead, 3)  # noqa: E731
    return {
        "beats": [shift(b) for b in beats if b >= -lead - 0.002],
        "downbeats": sorted(shift(d) for d in downs if d >= -lead - 0.002),
        "meter": grid["meter"],
    }


def build_padded(folder: Path, stems: list[str], edits: dict, sample_rate: int) -> None:
    """Song tracks with the lead-in's silence in front. Read and written as 16-bit
    integers, so the samples after the silence are exactly the originals."""
    target = folder / PADDED_DIR
    pad = lead_samples(edits, sample_rate)
    if pad <= 0:
        shutil.rmtree(target, ignore_errors=True)
        return
    building = folder / f"{PADDED_DIR}.building"
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir()
    timeline_peaks = {}
    for stem in stems:
        if is_added(edits, stem):
            start = int(round(to_timeline(edits, _track(edits, stem)["offset"]) * sample_rate))
            placed = _place(folder / ADDED_DIR / f"{stem}.src.wav", start, song_samples(folder, edits, sample_rate) + pad, building / f"{stem}.wav", sample_rate)
            timeline_peaks[stem] = _peaks(placed.T, sample_rate)  # shows a sound placed in the lead-in too
            continue
        audio, _ = sf.read(song_track_path(folder, stem, edits), dtype="int16", always_2d=True)
        silence = np.zeros((pad, audio.shape[1]), dtype=np.int16)
        sf.write(building / f"{stem}.wav", np.concatenate([silence, audio]), sample_rate, subtype="PCM_16")
    (building / PEAKS_FILE).write_text(json.dumps(timeline_peaks), encoding="utf-8")
    _swap_dir(building, target)


def serve_path(folder: Path, stem: str, edits: dict) -> Path:
    """The file to play or download: guides are rendered on the timeline already, song
    tracks come from padded/ when there is a lead-in."""
    if stem in GUIDES:
        return folder / "stems" / f"{stem}.wav"
    padded = folder / PADDED_DIR / f"{stem}.wav"
    if lead_seconds(edits) > 0 and padded.exists():
        return padded
    return song_track_path(folder, stem, edits)


def merged_view(folder: Path, analysis: dict, edits: dict) -> dict:
    """The analysis as it stands after the person's edits, on the playback timeline."""
    peaks_path = folder / EDITED_DIR / PEAKS_FILE
    peaks = json.loads(peaks_path.read_text(encoding="utf-8")) if peaks_path.exists() else {}
    locked_peaks = folder / WARPED_DIR / PEAKS_FILE
    if edits.get("warp") and locked_peaks.exists():
        peaks = json.loads(locked_peaks.read_text(encoding="utf-8"))
    lead = lead_seconds(edits)
    shift = lambda t: round(to_timeline(edits, t), 3)  # noqa: E731
    silent = [0] * int(round(lead * 30))  # waveform overview has 30 points per second
    stems = []
    click = None
    for stem in analysis["stems"]:
        if stem["id"] == CLICK:
            click = stem
        elif stem["id"] != CUES:
            stems.append({**stem, "peaks": silent + peaks.get(stem["id"], stem["peaks"])})
    for track in edits["tracks"]:
        if track.get("kind") == "file":
            own = folder / ADDED_DIR / f"{track['id']}.peaks.json"
            padded_peaks = folder / PADDED_DIR / PEAKS_FILE
            on_timeline = json.loads(padded_peaks.read_text(encoding="utf-8")).get(track["id"]) if lead > 0 and padded_peaks.exists() else None
            track_peaks = on_timeline or silent + (peaks.get(track["id"]) or (json.loads(own.read_text(encoding="utf-8")) if own.exists() else []))
            stems.append({
                "id": track["id"], "name": track["name"], "custom": True, "added": True, "root": None,
                "color": track["color"], "audible": True, "peaks": track_peaks,
                "offset": round(to_timeline(edits, track["offset"]), 3), "length": track.get("length"),
            })
            continue
        stems.append({
            "id": track["id"], "name": track["name"], "custom": True, "root": track["root"],
            "color": track["color"], "audible": True, "peaks": silent + peaks.get(track["id"], []),
        })
    if click:
        stems.append(click)
    render_path = folder / CUE_RENDER_FILE
    if render_path.exists():
        rendered = json.loads(render_path.read_text(encoding="utf-8"))
        stems.append({
            "id": CUES, "name": "Cues (เสียงบอกท่อน)", "guide": True, "audible": True, "activity": [],
            "peaks": rendered.get("peaks", []), "cues": [{**c, "time": shift(c["time"])} for c in edits.get("cues", [])],
            "count_beats": rendered.get("counts", []),
        })
    tempo = analysis["tempo"]
    return {
        **analysis,
        "duration": shift(analysis["duration"]),
        "sections": [{**s, "start": shift(s["start"]), "end": shift(s["end"])} for s in analysis["sections"]],
        "chords": [{**c, "start": shift(c["start"]), "end": shift(c["end"])} for c in analysis["chords"]],
        "tempo": {**tempo, "beats": [shift(t) for t in tempo["beats"]], "downbeats": [shift(t) for t in tempo["downbeats"]]},
        "tempo_lock": {"bpm": edits["warp"]["bpm"], "range": edits["warp"].get("range"), "listen": listen_spots(edits)} if edits.get("warp") else None,
        "lead_in": {"bars": (edits.get("lead_in") or {}).get("bars", 0), "seconds": round(lead, 6)},
        "stems": stems,
        "tracks": edits["tracks"],
        "can_undo_cut": bool(edits["ops"]),
        "click_grid": {**timeline_grid(analysis, edits), "edited": bool(edits.get("click"))},
        **tempo_view(analysis, edits),
    }


def tempo_view(analysis: dict, edits: dict) -> dict:
    """Tempo sections of the metronome as it will actually play, on the timeline."""
    grid = timeline_grid(analysis, edits)
    sections = tempo_map(grid["beats"], grid["downbeats"], grid["meter"])
    return {"tempo_map": sections, "tempo_summary": tempo_summary(sections)}


def track_label(edits: dict, stem: str) -> str:
    """A file-safe name for a stem in downloads."""
    track = _track(edits, stem)
    name = track["name"] if track else stem
    if stem == CUES:
        name = "cues"
    return re.sub(r'[\\/:*?"<>|]', "_", name)

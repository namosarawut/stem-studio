"""Setlists: ordered songs with start/end points, played one after another.

A song is either an audio file imported from this computer (decoded once to WAV) or a
finished project, played as a mix of its tracks with the chosen tracks on and routed
left/right. Project mixes are rendered on the server and cached, so the page streams
one stereo file per song instead of decoding every stem."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from . import fsutil
from .metronome import write_wav_atomic

ITEM_ID = re.compile(r"^[0-9a-f]{6,16}$")
PANS = ("L", "C", "R")
MAX_OUTPUTS = 16  # channels of an audio interface a track can be sent to


def clean_output(value: object) -> str | None:
    """Where a track plays, in its canonical form: "C" = Out 1-2 (stereo, the default),
    "L" / "R" = Out 1 / Out 2 (mono), "3-4", "5-6" ... = a stereo pair, "3", "4" ... = one
    channel (mono). The first pair keeps its old L/C/R names, so mixes saved before more
    outputs existed keep their meaning and their render cache. None when not valid."""
    text = str(value).strip().upper()
    if text in PANS:
        return text
    pair = re.fullmatch(r"(\d{1,2})-(\d{1,2})", text)
    if pair:
        a, b = int(pair.group(1)), int(pair.group(2))
        if a % 2 == 1 and b == a + 1 and b <= MAX_OUTPUTS:
            return "C" if a == 1 else f"{a}-{b}"
        return None
    if text.isdigit() and 1 <= int(text) <= MAX_OUTPUTS:
        return {1: "L", 2: "R"}.get(int(text), str(int(text)))
    return None


def output_channels(value: str) -> tuple[int, ...]:
    """Zero-based channels of an output: two for a stereo pair, one for a mono channel."""
    if value == "C":
        return (0, 1)
    if value in ("L", "R"):
        return (0 if value == "L" else 1,)
    if "-" in value:
        a, b = value.split("-")
        return (int(a) - 1, int(b) - 1)
    return (int(value) - 1,)
MAX_ITEMS = 300
MAX_PLAYLISTS = 200
MAX_NAME = 120
MAX_GAP = 60.0
KEEP_RENDERS = 6  # cached project mixes kept per song
MIN_GAIN_DB = -60.0
MAX_GAIN_DB = 12.0

_render_lock = threading.Lock()
_store_lock = threading.Lock()


class PlaylistError(ValueError):
    pass


def write_json_atomic(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        fsutil.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clean_name(value: object, fallback: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()
    return text[:MAX_NAME] or fallback


# ---------------------------------------------------------------- imported files

def file_peaks(path: Path, per_second: int = 30) -> list[int]:
    """Waveform peaks read block by block, in the same 0-255 form as the studio's."""
    info = sf.info(path)
    size = max(1, info.samplerate // per_second)
    peaks: list[np.ndarray] = []
    for block in sf.blocks(path, blocksize=size * 2000, dtype="float32", always_2d=True):
        usable = len(block) - len(block) % size
        if usable:
            peaks.append(np.abs(block[:usable]).max(axis=1).reshape(-1, size).max(axis=1))
    if not peaks:
        return []
    return (np.clip(np.concatenate(peaks), 0, 1) * 255).astype(np.uint8).tolist()


def import_file(folder: Path, source: Path, title: str, filename: str, ffmpeg: str, sample_rate: int) -> dict:
    """Convert any format ffmpeg reads to a 16-bit stereo WAV on disk (never the whole
    song in memory), then store its details."""
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / ".audio.tmp.wav"
    command = [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source), "-vn", "-ac", "2", "-ar", str(sample_rate),
               "-c:a", "pcm_s16le", "-f", "wav", str(temporary)]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0 or not temporary.exists() or sf.info(temporary).frames == 0:
        temporary.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise PlaylistError(f"อ่านไฟล์เสียงไม่ได้: {detail[-1] if detail else 'ไม่พบสัญญาณเสียง'}")
    fsutil.replace(temporary, folder / "audio.wav")
    info = sf.info(folder / "audio.wav")
    meta = {
        "id": folder.name, "title": _clean_name(title, "เพลง"), "filename": filename,
        "duration": round(info.frames / info.samplerate, 3), "peaks": file_peaks(folder / "audio.wav"), "created_at": time.time(),
    }
    write_json_atomic(folder / "meta.json", meta)
    return meta


def read_import(folder: Path) -> dict | None:
    try:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_imports(root: Path) -> list[dict]:
    if not root.exists():
        return []
    items = [m for m in (read_import(p) for p in root.iterdir() if p.is_dir()) if m]
    return sorted(({k: v for k, v in m.items() if k != "peaks"} for m in items), key=lambda m: m["created_at"], reverse=True)


# ---------------------------------------------------------------- playlists

def list_playlists(root: Path) -> list[dict]:
    if not root.exists():
        return []
    found = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found.append({"id": data["id"], "name": data["name"], "count": len(data["items"]), "updated_at": data.get("updated_at", 0)})
    return sorted(found, key=lambda p: p["updated_at"], reverse=True)


def read_playlist(root: Path, playlist_id: str) -> dict:
    if not ITEM_ID.match(playlist_id):
        raise PlaylistError("ไม่พบเพลย์ลิสต์")
    try:
        return json.loads((root / f"{playlist_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PlaylistError("ไม่พบเพลย์ลิสต์") from None


def create_playlist(root: Path, name: str) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    with _store_lock:
        if len(list(root.glob("*.json"))) >= MAX_PLAYLISTS:
            raise PlaylistError(f"สร้างเพลย์ลิสต์ได้สูงสุด {MAX_PLAYLISTS} รายการ")
        now = time.time()
        playlist = {"id": uuid.uuid4().hex[:12], "name": _clean_name(name, "เพลย์ลิสต์ใหม่"), "auto_advance": True, "gap": 0.0,
                    "items": [], "created_at": now, "updated_at": now}
        write_json_atomic(root / f"{playlist['id']}.json", playlist)
    return playlist


def _clean_mix(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    muted = sorted({str(t)[:40] for t in raw.get("muted", []) if isinstance(t, str)})[:64]
    pan = {}
    if isinstance(raw.get("pan"), dict):
        for k, v in raw["pan"].items():
            out = clean_output(v)
            if out and out != "C":
                pan[str(k)[:40]] = out
    mix = {"muted": muted, "pan": dict(sorted(pan.items())[:64])}
    gain = clean_gain(raw.get("gain"))
    if gain:
        mix["gain"] = gain
    return mix


def clean_gain(raw: object) -> dict:
    """Per-track level in dB ({stem: dB}); 0 dB is left out, so an untouched mix keeps
    the same cache key it always had."""
    if not isinstance(raw, dict):
        return {}
    gain = {}
    for key, value in list(raw.items())[:64]:
        db = _number(value, MIN_GAIN_DB, MAX_GAIN_DB, 0.0)
        if isinstance(key, str) and db is not None and abs(db) >= 0.05:
            gain[key[:40]] = round(db, 1)
    return dict(sorted(gain.items()))


def _number(value: object, low: float, high: float, fallback: float | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not np.isfinite(number):
        return fallback
    return round(min(max(number, low), high), 3)


def save_playlist(root: Path, playlist_id: str, raw: dict) -> dict:
    """Replace a playlist's contents after checking every field."""
    current = read_playlist(root, playlist_id)
    items_in = raw.get("items", [])
    if not isinstance(items_in, list) or len(items_in) > MAX_ITEMS:
        raise PlaylistError(f"ใส่เพลงได้สูงสุด {MAX_ITEMS} เพลงต่อเพลย์ลิสต์")
    items = []
    seen: set[str] = set()
    for raw_item in items_in:
        if not isinstance(raw_item, dict):
            raise PlaylistError("ข้อมูลเพลงไม่ถูกต้อง")
        kind = raw_item.get("kind")
        ref = str(raw_item.get("ref", ""))
        if kind not in ("file", "project") or not ITEM_ID.match(ref):
            raise PlaylistError("ข้อมูลเพลงไม่ถูกต้อง")
        item_id = str(raw_item.get("id") or uuid.uuid4().hex[:10])
        if not ITEM_ID.match(item_id) or item_id in seen:
            item_id = uuid.uuid4().hex[:10]
        seen.add(item_id)
        start = _number(raw_item.get("start"), 0.0, 24 * 3600, 0.0)
        end = _number(raw_item.get("end"), 0.0, 24 * 3600, None)
        if end is not None and end <= start + 0.05:
            raise PlaylistError("จุดจบต้องอยู่หลังจุดเริ่ม")
        item = {
            "id": item_id, "kind": kind, "ref": ref, "title": _clean_name(raw_item.get("title"), "เพลง"),
            "start": start, "end": end, "stop_after": bool(raw_item.get("stop_after")),
            "fade_in": _number(raw_item.get("fade_in"), 0.0, 30.0, 0.0), "fade_out": _number(raw_item.get("fade_out"), 0.0, 30.0, 0.0),
        }
        if kind == "project":
            item["mix"] = _clean_mix(raw_item.get("mix"))
        items.append(item)
    current.update({
        "name": _clean_name(raw.get("name", current["name"]), current["name"]),
        "auto_advance": bool(raw.get("auto_advance", current.get("auto_advance", True))),
        "gap": _number(raw.get("gap", current.get("gap", 0.0)), 0.0, MAX_GAP, 0.0),
        "items": items, "updated_at": time.time(),
    })
    with _store_lock:
        write_json_atomic(root / f"{playlist_id}.json", current)
    return current


def delete_playlist(root: Path, playlist_id: str) -> None:
    read_playlist(root, playlist_id)
    (root / f"{playlist_id}.json").unlink(missing_ok=True)


# ---------------------------------------------------------------- project mixes

def mix_key(sources: dict[str, Path], mix: dict) -> str:
    """Cache key: the chosen mix plus each file's size and time, so an edit to the song
    (a cut, a moved metronome, new cues) gives a new render."""
    stamp = [(stem, path.stat().st_size, path.stat().st_mtime_ns) for stem, path in sorted(sources.items())]
    return hashlib.sha1(json.dumps([stamp, mix], sort_keys=True).encode()).hexdigest()[:16]


def render_mix(sources: dict[str, Path], mix: dict, out_dir: Path, peaks_fn) -> dict:
    """Sum the audible tracks into one WAV with as many channels as the outputs used
    (stereo unless a track goes to Out 3 or above: one channel per interface output). A
    track sent to one channel is folded to mono as (L + R) / 2 first, the same as the
    studio's mixer, so a centred sound keeps its level. Returns key, duration, peaks."""
    out_dir.mkdir(parents=True, exist_ok=True)
    key = mix_key(sources, mix)
    path = out_dir / f"{key}.wav"
    meta_path = out_dir / f"{key}.json"
    with _render_lock:
        if path.exists() and meta_path.exists():
            os.utime(path)  # recently used: keep it when trimming the cache
            return json.loads(meta_path.read_text(encoding="utf-8"))
        infos = {stem: sf.info(path) for stem, path in sources.items()}
        sample_rate = next(iter(infos.values())).samplerate
        # Every served track has the timeline's length; take the longest to be safe.
        outputs = {stem: output_channels(mix["pan"].get(stem, "C")) for stem in sources if stem not in mix["muted"]}
        channels = max([2] + [max(chans) + 1 for chans in outputs.values()])
        total = np.zeros((channels, max(info.frames for info in infos.values())), dtype=np.float32)
        for stem, source in sources.items():
            if stem in mix["muted"]:
                continue
            audio = sf.read(source, dtype="float32", always_2d=True)[0].T
            if audio.shape[0] == 1:
                audio = np.repeat(audio, 2, axis=0)
            length = min(audio.shape[1], total.shape[1])
            db = mix.get("gain", {}).get(stem, 0.0)
            if db:
                audio *= np.float32(10 ** (db / 20))
            chans = outputs[stem]
            if len(chans) == 2:
                total[chans[0], :length] += audio[0, :length]
                total[chans[1], :length] += audio[1, :length]
            else:
                total[chans[0], :length] += audio[:2, :length].mean(axis=0)
            del audio
        write_wav_atomic(path, np.clip(total, -1.0, 1.0).T, sample_rate)
        meta = {"key": key, "duration": round(total.shape[1] / sample_rate, 3), "peaks": peaks_fn(total, sample_rate), "channels": channels}
        write_json_atomic(meta_path, meta)
        # Trim old renders of this song, least recently used first.
        renders = sorted(out_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in renders[KEEP_RENDERS:]:
            if fsutil.try_unlink(old):  # Windows keeps one that is still playing
                fsutil.try_unlink(old.with_suffix(".json"))
        return meta

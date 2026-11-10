"""Spoken guide cues: a library of phrases, macOS text-to-speech, and the cue track renderer.

Speech comes from the system `say` command, so it works offline and nothing leaves the
machine. Each phrase is rendered once per voice and kept in models/cues/.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

from .metronome import write_wav_atomic

CUES = "cues"
MAX_CUES = 500
MAX_TEXT = 80
PHRASE_RATE = 205
COUNT_RATE = 250
PEAK_LEVEL = 0.7
ONSET_THRESHOLD = 0.02
PRE_ROLL_SECONDS = 0.004
FADE_SECONDS = 0.008
SAY_TIMEOUT = 20

# Voices offered when installed. Only these names are ever passed to `say`.
VOICE_CHOICES = [
    {"id": "kanya", "say": "Kanya", "lang": "th", "label": "กันยา · ไทย (หญิง)"},
    {"id": "samantha", "say": "Samantha", "lang": "en", "label": "Samantha · อังกฤษ US (หญิง)"},
    {"id": "daniel", "say": "Daniel", "lang": "en", "label": "Daniel · อังกฤษ UK (ชาย)"},
    {"id": "reed", "say": "Reed (English (US))", "lang": "en", "label": "Reed · อังกฤษ US (ชาย)"},
    {"id": "karen", "say": "Karen", "lang": "en", "label": "Karen · อังกฤษ AU (หญิง)"},
    {"id": "flo", "say": "Flo (English (US))", "lang": "en", "label": "Flo · อังกฤษ US (หญิง)"},
    {"id": "rocko", "say": "Rocko (English (US))", "lang": "en", "label": "Rocko · อังกฤษ US (ชาย)"},
    {"id": "fred", "say": "Fred", "lang": "en", "label": "Fred · อังกฤษ US (ชาย, หุ่นยนต์)"},
]

NUMBERS = {
    "en": ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"],
    "th": ["หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า", "สิบ", "สิบเอ็ด", "สิบสอง"],
}

# (id, English, Thai)
LIBRARY = [
    ("sections", "ท่อนเพลง", [
        ("intro", "Intro", "อินโทร"), ("verse", "Verse", "เวิร์ส"), ("verse_1", "Verse one", "เวิร์สหนึ่ง"),
        ("verse_2", "Verse two", "เวิร์สสอง"), ("verse_3", "Verse three", "เวิร์สสาม"), ("verse_4", "Verse four", "เวิร์สสี่"),
        ("pre_chorus", "Pre chorus", "ท่อนส่ง"), ("chorus", "Chorus", "ท่อนฮุก"), ("chorus_1", "Chorus one", "ฮุกหนึ่ง"),
        ("chorus_2", "Chorus two", "ฮุกสอง"), ("chorus_3", "Chorus three", "ฮุกสาม"), ("last_chorus", "Last chorus", "ฮุกสุดท้าย"),
        ("double_chorus", "Double chorus", "ฮุกสองรอบ"), ("post_chorus", "Post chorus", "หลังฮุก"), ("hook", "Hook", "ฮุก"),
        ("refrain", "Refrain", "ท่อนซ้ำ"), ("bridge", "Bridge", "ท่อนแยก"), ("middle_eight", "Middle eight", "มิดเดิลเอท"),
        ("breakdown", "Breakdown", "เบรกดาวน์"), ("build", "Build", "บิ้วด์"), ("drop", "Drop", "ดร็อป"),
        ("instrumental", "Instrumental", "ดนตรี"), ("interlude", "Interlude", "ท่อนคั่น"), ("solo", "Solo", "โซโล่"),
        ("guitar_solo", "Guitar solo", "โซโล่กีตาร์"), ("keys_solo", "Keys solo", "โซโล่คีย์บอร์ด"),
        ("piano_solo", "Piano solo", "โซโล่เปียโน"), ("bass_solo", "Bass solo", "โซโล่เบส"), ("drum_solo", "Drum solo", "โซโล่กลอง"),
        ("sax_solo", "Sax solo", "โซโล่แซ็กโซโฟน"), ("rap", "Rap", "ท่อนแร็พ"), ("tag", "Tag", "แท็ก"), ("vamp", "Vamp", "แวมป์"),
        ("turnaround", "Turnaround", "เทิร์นอะราวด์"), ("outro", "Outro", "เอาท์โทร"), ("ending", "Ending", "ท่อนจบ"), ("coda", "Coda", "โคดา"),
    ]),
    ("instruments", "เครื่องดนตรีเข้า-ออก", [
        ("all_in", "All in", "เข้าพร้อมกัน"), ("band_in", "Band in", "วงเข้า"), ("drums_in", "Drums in", "กลองเข้า"),
        ("bass_in", "Bass in", "เบสเข้า"), ("keys_in", "Keys in", "คีย์บอร์ดเข้า"), ("guitar_in", "Guitar in", "กีตาร์เข้า"),
        ("vocals_in", "Vocals in", "ร้องเข้า"), ("strings_in", "Strings in", "เครื่องสายเข้า"), ("horns_in", "Horns in", "เครื่องเป่าเข้า"),
        ("band_out", "Band out", "วงออก"), ("drums_out", "Drums out", "กลองออก"), ("bass_out", "Bass out", "เบสออก"),
        ("keys_out", "Keys out", "คีย์บอร์ดออก"), ("guitar_out", "Guitar out", "กีตาร์ออก"), ("drums_only", "Drums only", "กลองอย่างเดียว"),
        ("keys_only", "Keys only", "คีย์บอร์ดอย่างเดียว"), ("guitar_only", "Guitar only", "กีตาร์อย่างเดียว"),
        ("acapella", "A cappella", "ร้องเปล่า"), ("vocals_only", "Vocals only", "ร้องอย่างเดียว"), ("full_band", "Full band", "เต็มวง"),
    ]),
    ("dynamics", "ความดังและการเล่น", [
        ("build_up", "Build it up", "บิ้วด์ขึ้น"), ("bring_down", "Bring it down", "ลดลง"), ("down", "Down", "เบาลง"),
        ("soft", "Soft", "เบา"), ("quiet", "Quiet", "เงียบๆ"), ("big", "Big", "ใหญ่"), ("loud", "Loud", "ดัง"),
        ("swell", "Swell", "ค่อยๆ ดังขึ้น"), ("fade", "Fade out", "ค่อยๆ เบาลงจนหาย"), ("hits", "Hits", "เล่นพร้อมกัน"),
        ("stop", "Stop", "หยุด"), ("hold", "Hold", "ค้างไว้"), ("break", "Break", "เบรก"), ("stabs", "Stabs", "กระแทก"),
        ("pads", "Pads", "แพด"), ("groove", "Groove", "กรู๊ฟ"), ("four_on_floor", "Four on the floor", "กระเดื่องทุกจังหวะ"),
        ("rimshot", "Rim click", "ริมช็อต"), ("toms", "Toms", "ทอม"), ("cymbals", "Cymbals", "ฉาบ"), ("everybody_sing", "Everybody sing", "ร้องพร้อมกัน"),
        ("clap", "Clap", "ปรบมือ"), ("jump", "Jump", "กระโดด"), ("hands_up", "Hands up", "ยกมือขึ้น"),
    ]),
    ("form", "การวนและการจบ", [
        ("repeat", "Repeat", "ซ้ำ"), ("again", "Again", "อีกรอบ"), ("one_more", "One more time", "อีกครั้ง"),
        ("two_more", "Two more times", "อีกสองรอบ"), ("last_time", "Last time", "รอบสุดท้าย"), ("go_on", "Keep going", "ไปต่อ"),
        ("stay_here", "Stay here", "อยู่ตรงนี้"), ("loop", "Loop", "วนไป"), ("dont_stop", "Don't stop", "อย่าหยุด"),
        ("watch_me", "Watch me", "ดูสัญญาณ"), ("free_time", "Free time", "ฟรีไทม์"), ("big_ending", "Big ending", "จบใหญ่"),
        ("end", "End", "จบ"), ("end_song", "End of song", "จบเพลง"), ("out", "Out", "ออก"), ("go", "Go", "ไป"),
        ("next_song", "Next song", "เพลงต่อไป"), ("segue", "Segue", "ต่อเพลงเลย"), ("skip", "Skip", "ข้าม"),
    ]),
    ("key_tempo", "คีย์และจังหวะ", [
        ("key_change", "Key change", "เปลี่ยนคีย์"), ("up_half", "Up a half step", "ขึ้นครึ่งเสียง"), ("up_whole", "Up a whole step", "ขึ้นหนึ่งเสียง"),
        ("down_half", "Down a half step", "ลงครึ่งเสียง"), ("half_time", "Half time", "ฮาล์ฟไทม์"), ("double_time", "Double time", "ดับเบิลไทม์"),
        ("slow_down", "Slow down", "ช้าลง"), ("speed_up", "Speed up", "เร็วขึ้น"), ("ritard", "Ritard", "ค่อยๆ ช้าลง"),
        ("fermata", "Fermata", "ค้างโน้ต"), ("tempo_change", "Tempo change", "เปลี่ยนจังหวะ"), ("in_time", "In time", "เข้าจังหวะ"),
        ("click_off", "Click off", "ปิดคลิก"), ("click_on", "Click on", "เปิดคลิก"),
    ]),
    ("words", "คำนับและคำสั้น", [
        *[(f"n{i + 1}", NUMBERS["en"][i].capitalize(), NUMBERS["th"][i]) for i in range(12)],
        ("and", "And", "และ"), ("ready", "Ready", "พร้อม"), ("here_we_go", "Here we go", "ไปเลย"), ("lets_go", "Let's go", "ลุย"),
        ("count_in", "Count in", "นับเข้า"), ("two_bars", "Two bars", "อีกสองห้อง"), ("four_bars", "Four bars", "อีกสี่ห้อง"),
        ("eight_bars", "Eight bars", "อีกแปดห้อง"), ("one_bar", "One bar", "อีกหนึ่งห้อง"),
    ]),
]

COUNT_STYLES = {
    "full": "นับ 1 ห้องเต็ม (1 2 3 4)",
    "long": "นับ 2 ห้อง (1 · 2 · / 1 2 3 4)",
    "short": "นับครึ่งห้องท้าย (3 4)",
    "ready_go": "นับแล้วบอกพร้อม-ไป (1 2 พร้อม ไป)",
}

_sample_lock = threading.Lock()
_pending: dict[str, threading.Event] = {}


class CueError(ValueError):
    pass


@lru_cache(maxsize=1)
def available_voices() -> list[dict]:
    try:
        listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    installed = {re.split(r"\s{2,}", line.strip())[0] for line in listing.splitlines() if line.strip()}
    return [{k: v for k, v in voice.items() if k != "say"} for voice in VOICE_CHOICES if voice["say"] in installed]


def voice_info(voice_id: str) -> dict:
    voice = next((v for v in VOICE_CHOICES if v["id"] == voice_id), None)
    if voice is None or voice_id not in {v["id"] for v in available_voices()}:
        raise CueError("ไม่พบเสียงอ่านนี้ในเครื่อง")
    return voice


def library() -> dict:
    return {
        "voices": available_voices(),
        "categories": [
            {"id": cid, "label": label, "items": [{"id": i, "en": en, "th": th} for i, en, th in items]}
            for cid, label, items in LIBRARY
        ],
        "count_styles": [{"id": k, "label": v} for k, v in COUNT_STYLES.items()],
        "numbers": NUMBERS,
    }


def clean_text(text: str) -> str:
    # `[[ ]]` would let typed text change the speech engine's settings.
    cleaned = re.sub(r"\s+", " ", re.sub(r"\[\[|\]\]|[\x00-\x1f]", "", text or "")).strip()
    if not cleaned:
        raise CueError("กรุณาใส่ข้อความ")
    if len(cleaned) > MAX_TEXT:
        raise CueError(f"ข้อความยาวเกิน {MAX_TEXT} ตัวอักษร")
    return cleaned


def _trim(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Start the sample at the first audible sound, so a cue placed on a beat speaks on it."""
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak <= 1e-6:
        return np.zeros(0, dtype=np.float32)
    loud = np.nonzero(np.abs(audio) > peak * ONSET_THRESHOLD)[0]
    start = max(0, int(loud[0]) - int(PRE_ROLL_SECONDS * sample_rate))
    end = min(len(audio), int(loud[-1]) + int(0.03 * sample_rate))
    return (audio[start:end] / peak * PEAK_LEVEL).astype(np.float32)


def sample_path(cache: Path, voice_id: str, text: str, rate: int) -> Path:
    key = hashlib.sha1(f"{voice_id}|{rate}|{text}".encode()).hexdigest()[:20]
    return cache / voice_id / f"{key}.wav"


def speak(cache: Path, voice_id: str, text: str, rate: int, sample_rate: int) -> Path:
    """Render one phrase with `say`, once. Concurrent requests for the same phrase wait."""
    voice = voice_info(voice_id)
    text = clean_text(text)
    path = sample_path(cache, voice_id, text, rate)
    with _sample_lock:
        if path.exists():
            return path
        waiter = _pending.get(str(path))
        owner = waiter is None
        if owner:
            waiter = _pending[str(path)] = threading.Event()
    if not owner:
        waiter.wait(SAY_TIMEOUT + 5)
        if path.exists():
            return path
        raise CueError("สร้างเสียงพูดไม่สำเร็จ")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            text_file = Path(folder) / "text.txt"
            text_file.write_text(text, encoding="utf-8")
            raw = Path(folder) / "raw.wav"
            result = subprocess.run(
                ["say", "-v", voice["say"], "-r", str(rate), "-f", str(text_file), "-o", str(raw),
                 "--file-format=WAVE", f"--data-format=LEI16@{sample_rate}"],
                capture_output=True, timeout=SAY_TIMEOUT,
            )
            if result.returncode != 0 or not raw.exists():
                raise CueError("สร้างเสียงพูดไม่สำเร็จ")
            audio, _ = sf.read(raw, dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            trimmed = _trim(audio, sample_rate)
            temporary = path.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
            sf.write(temporary, trimmed, sample_rate, subtype="FLOAT", format="WAV")
            temporary.replace(path)
        return path
    finally:
        with _sample_lock:
            _pending.pop(str(path), None)
        waiter.set()


def count_words(style: str, meter: int, lang: str) -> list[str | None]:
    """Words for each beat of the count, last entry on the beat just before the target."""
    numbers = NUMBERS[lang]
    bar = [numbers[i] for i in range(min(meter, len(numbers)))]
    if style == "full":
        return bar
    if style == "short":
        return [None] * max(0, meter - 2) + bar[-2:]
    if style == "long":
        half = meter // 2 if meter > 2 else 1
        first = [None] * meter
        first[0] = numbers[0]
        if meter > 1:
            first[half] = numbers[1]
        return first + bar
    if style == "ready_go":
        go = ["ready", "go"] if lang == "en" else ["พร้อม", "ไป"]
        return bar[:-2] + go if meter > 2 else go
    raise CueError("ไม่รู้จักรูปแบบการนับ")


def clean_cues(raw: list[dict], duration: float, meter: int, start: float = 0.0) -> list[dict]:
    if len(raw) > MAX_CUES:
        raise CueError(f"ใส่ cue ได้สูงสุด {MAX_CUES} จุด")
    cleaned = []
    for cue in raw:
        kind = cue.get("kind")
        time = float(cue.get("time", -1))
        if not np.isfinite(time) or time < start - 0.001 or time > duration:
            raise CueError("มี cue อยู่นอกความยาวเพลง")
        voice_info(str(cue.get("voice", "")))
        item = {
            "id": str(cue.get("id") or uuid.uuid4().hex[:10])[:16], "kind": kind, "time": round(time, 3),
            "voice": cue["voice"], "auto": bool(cue.get("auto")),
        }
        if kind == "phrase":
            item["text"] = clean_text(str(cue.get("text", "")))
            item["label"] = clean_text(str(cue.get("label") or item["text"]))
        elif kind == "count":
            style = str(cue.get("style", "full"))
            if style not in COUNT_STYLES:
                raise CueError("ไม่รู้จักรูปแบบการนับ")
            item["style"] = style
        else:
            raise CueError("ชนิด cue ไม่ถูกต้อง")
        cleaned.append(item)
    return sorted(cleaned, key=lambda c: c["time"])


def _place(track: np.ndarray, sample: np.ndarray, start: int, limit: int | None, sample_rate: int) -> None:
    if start < 0:
        sample, start = sample[-start:], 0
    if start >= len(track):
        return
    end = min(len(track), start + len(sample))
    if limit is not None:
        end = min(end, limit)
    piece = sample[: end - start].copy()
    if limit is not None and len(piece) < len(sample):
        fade = min(len(piece), int(FADE_SECONDS * sample_rate))
        if fade > 0:
            piece[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    track[start:end] += piece


def render(cues: list[dict], beats: list[float], meter: int, samples: int, sample_rate: int, cache: Path, path: Path) -> tuple[np.ndarray, list[dict]]:
    """Write the cue track at the song's exact length. Returns the stereo audio and, for
    each count cue, which beats it spoke on so the page can show them."""
    jobs: list[tuple[str, str, int]] = []
    plans = []
    grid = np.array(sorted(beats), dtype=float)
    for cue in cues:
        lang = voice_info(cue["voice"])["lang"]
        if cue["kind"] == "phrase":
            plans.append((cue, [(cue["time"], cue["text"], PHRASE_RATE, None)]))
            jobs.append((cue["voice"], cue["text"], PHRASE_RATE))
            continue
        words = count_words(cue["style"], meter, lang)
        # The count leads into the beat nearest the cue. Matching by beat rather than by a
        # time cut-off matters when the grid changes: a beat now landing a few ms before
        # the cue is still the beat the band comes in on, not the last word of the count.
        target_time = cue["time"]
        if len(grid):
            nearest = int(np.argmin(np.abs(grid - target_time)))
            gap = float(np.median(np.diff(grid[max(0, nearest - 4): nearest + 5]))) if len(grid) > 2 else 0.5
            if abs(grid[nearest] - target_time) < gap * 0.4:
                target_index, target_time = nearest, float(grid[nearest])
            else:
                target_index = int(np.searchsorted(grid, target_time - 0.02))
        else:
            target_index = 0
        before = grid[:target_index]
        times = list(before[-len(words):]) if len(before) else []
        words = words[len(words) - len(times):]
        steps = []
        for i, (time, word) in enumerate(zip(times, words)):
            if word is None:
                continue
            nxt = times[i + 1] if i + 1 < len(times) else target_time
            steps.append((time, word, COUNT_RATE, nxt))
            jobs.append((cue["voice"], word, COUNT_RATE))
        plans.append((cue, steps))

    with ThreadPoolExecutor(max_workers=6) as pool:
        files = dict(zip(jobs, pool.map(lambda j: speak(cache, j[0], j[1], j[2], sample_rate), jobs)))

    track = np.zeros(samples, dtype=np.float32)
    spoken = []
    for cue, steps in plans:
        placed = []
        for time, word, rate, until in steps:
            audio, _ = sf.read(files[(cue["voice"], word, rate)], dtype="float32")
            limit = int(round(until * sample_rate)) - int(0.004 * sample_rate) if until is not None else None
            # The sample keeps a few ms of soft attack, so start that early: the word is heard on the beat.
            _place(track, audio, int(round(time * sample_rate)) - int(PRE_ROLL_SECONDS * sample_rate), limit, sample_rate)
            placed.append(round(float(time), 3))
        if cue["kind"] == "count":
            spoken.append({"id": cue["id"], "beats": placed})
    stereo = np.stack([track, track])
    write_wav_atomic(path, np.clip(stereo, -1.0, 1.0).T, sample_rate)
    return stereo, spoken

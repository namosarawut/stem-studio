"""Prism as an MCP server: lets Claude (Claude Desktop, Claude Code, any MCP client) open a
song you already separated and fix its metronome, cues and lead-in.

Run by the MCP client over stdio:  <venv python> -m app.mcp_server   (PYTHONPATH = repo root)
It talks to the Prism server that is already running on this machine (127.0.0.1, port
STEM_STUDIO_PORT or 8765) through the same API the page uses, so every change is saved,
rendered and shown exactly as if it had been made on the page, and the page offers to
reload when it is open. Standard library only.

What Claude sees is what these tools return: song titles, times of beats, drum and bass
hits, sections and cues. No audio leaves the machine.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

PORT = int(os.environ.get("STEM_STUDIO_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
CLIENT = "claude"  # shown on the page as who changed the song
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
PLACES = {"at": 0, "2_beats_before": 2, "1_bar_before": "bar", "2_bars_before": "2bar"}
SECTION_ITEMS = {"Intro": "intro", "Pre-Chorus": "pre_chorus", "Bridge": "bridge", "Instrumental": "instrumental",
                 "Break": "break", "Outro": "outro"}

INSTRUCTIONS = """Prism prepares backing tracks for live shows: separated stems, a click (metronome) that follows the drums, spoken cues in the band's in-ears, a lead-in. These tools edit songs already separated in the Prism app running on this computer.

Start with choose_song (it lets the person pick a song), then song_overview. Times are seconds on the song's timeline, as shown in Prism (they include any lead-in).
Metronome: check_metronome shows where beats sit off the drums; inspect_beats shows each beat in a stretch with the nearest drum hit in ms (positive = drum after the beat). Prefer repair_metronome first; use replace_beats for what it cannot fix. A drum hit with strength above about 0.3 on every beat is a strong sign of the real pulse. Bar lines (beat 1) matter for counts and the DAW grid.
Cues: auto_cues puts the section name one bar before each section; add_cue places a phrase or a count-in. Use get_cue_library for phrases. Thai voices speak Thai text.
Every change is saved at once and can be taken back with undo. Tell the person in Thai what you changed and where to listen, in plain words. The Prism page offers to reload when a song changes."""


class ToolError(Exception):
    pass


# ------------------------------------------------------------------ talking to Prism

def api(method: str, path: str, body: dict | None = None, timeout: float = 300):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-Prism-Client": CLIENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get("detail")
        except Exception:
            detail = None
        raise ToolError(detail if isinstance(detail, str) else f"Prism ตอบกลับ {error.code}")
    except (urllib.error.URLError, OSError):
        raise ToolError(f"ติดต่อ Prism ไม่ได้ที่ {BASE} ให้เปิด Prism ก่อน (ดับเบิลคลิก start.command บน Mac หรือ start.bat บน Windows) แล้วลองใหม่")
    return json.loads(raw) if raw else None


def clock(t: float) -> str:
    sign = "-" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60)}:{t % 60:05.2f}"


def seconds(value) -> float:
    """12.5, "12.5" or "1:02.5" -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = re.fullmatch(r"(-?)(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        return (-1 if match.group(1) else 1) * (int(match.group(2)) * 60 + float(match.group(3)))
    try:
        return float(text)
    except ValueError:
        raise ToolError(f"อ่านเวลา {value!r} ไม่ออก ใช้วินาที เช่น 62.5 หรือ 1:02.5")


# ------------------------------------------------------------------ session state

class Session:
    def __init__(self):
        self.song: str | None = None
        self.can_elicit = False
        self.undo: dict[str, list[tuple[str, object]]] = {}
        self.library: dict | None = None

    def song_id(self, args: dict) -> str:
        song = args.get("song_id") or self.song
        if not song:
            raise ToolError("ยังไม่ได้เลือกเพลง เรียก choose_song ก่อน หรือส่ง song_id")
        return str(song)

    def remember(self, song: str, kind: str, state) -> None:
        self.undo.setdefault(song, []).append((kind, state))
        del self.undo[song][:-30]


S = Session()


def songs() -> list[dict]:
    return [j for j in api("GET", "/api/jobs") if j.get("status") == "done"]


def view(song: str) -> dict:
    return api("GET", f"/api/jobs/{song}/analysis")


def grid_of(v: dict) -> dict:
    g = v["click_grid"]
    return {"beats": list(g["beats"]), "downbeats": list(g["downbeats"]), "meter": int(g["meter"]), "edited": bool(g.get("edited"))}


def cues_of(v: dict) -> list[dict]:
    stem = next((s for s in v.get("stems", []) if s.get("id") == "cues"), None)
    return list(stem.get("cues", [])) if stem else []


def library() -> dict:
    if S.library is None:
        S.library = api("GET", "/api/cues/library")
    return S.library


def remember_grid(song: str, v: dict) -> None:
    g = grid_of(v)
    S.remember(song, "click", g if g["edited"] else None)


def save_grid(song: str, beats: list[float], downbeats: list[float], meter: int) -> dict:
    beats = sorted(round(float(t), 3) for t in beats)
    downs = sorted({round(float(t), 3) for t in downbeats} & set(beats))
    return api("PUT", f"/api/jobs/{song}/click", {"beats": beats, "downbeats": downs, "meter": int(meter)})


def bar_lines(beats: list[float], anchor_index: int, meter: int) -> list[float]:
    return [t for i, t in enumerate(beats) if (i - anchor_index) % meter == 0]


def nearest_index(beats: list[float], t: float) -> int:
    return min(range(len(beats)), key=lambda i: abs(beats[i] - t)) if beats else 0


def voice_for(requested: str | None) -> dict:
    voices = library()["voices"]
    if not voices:
        raise ToolError("เครื่องนี้ไม่มีเสียงอ่านสำหรับ cue")
    if requested:
        found = next((v for v in voices if v["id"] == requested), None)
        if not found:
            raise ToolError(f"ไม่มีเสียง {requested} ในเครื่องนี้ เลือกจาก: " + ", ".join(v["id"] for v in voices))
        return found
    return next((v for v in voices if v["lang"] == "th"), voices[0])


def phrase(item_id: str, lang: str) -> str:
    for category in library()["categories"]:
        for item in category["items"]:
            if item["id"] == item_id:
                return item[lang]
    raise ToolError(f"ไม่มีคำ {item_id} ในคลัง cue")


def summary(song: str, v: dict | None = None) -> dict:
    v = v or view(song)
    job = next((j for j in songs() if j["id"] == song), {})
    g = grid_of(v)
    lock = v.get("tempo_lock")
    return {
        "song_id": song, "title": job.get("title"), "duration": clock(v["duration"]),
        "key": v["key"].get("name") if isinstance(v.get("key"), dict) else v.get("key"),
        "tempo": v.get("tempo_summary"), "meter": g["meter"], "beats": len(g["beats"]),
        "metronome": "edited by hand" if g["edited"] else "automatic",
        "tempo_lock": {"bpm": lock["bpm"], "stretch": lock.get("range")} if lock else None,
        "lead_in": v.get("lead_in"),
        "tracks": [s.get("name") for s in v.get("stems", [])],
        "sections": [{"label": s["label"], "start": round(s["start"], 2), "at": clock(s["start"])} for s in v.get("sections", [])],
        "cues": [{"id": c["id"], "at": clock(c["time"]), "time": c["time"], "kind": c["kind"],
                  "text": c.get("label") or c.get("text") or c.get("style"), "voice": c.get("voice")} for c in cues_of(v)],
    }


# ------------------------------------------------------------------ tools

def t_list_songs(args):
    out = []
    for j in songs():
        out.append({"song_id": j["id"], "title": j["title"], "duration": clock(j.get("duration") or 0),
                    "bpm": j.get("bpm"), "key": j.get("key")})
    return {"songs": out, "selected": S.song}


def t_choose_song(args):
    if args.get("song_id"):
        song = str(args["song_id"])
    else:
        found = songs()
        if not found:
            raise ToolError("ยังไม่มีเพลงที่แยกเสร็จใน Prism")
        if not S.can_elicit:
            return {"note": "This client cannot show a picker. Show these songs to the person, ask which one, then call choose_song with its song_id.",
                    "songs": t_list_songs({})["songs"]}
        reply = request_client("elicitation/create", {
            "message": "เลือกเพลงใน Prism ที่จะให้ Claude แก้",
            "requestedSchema": {"type": "object", "required": ["song"], "properties": {"song": {
                "type": "string", "title": "เพลง",
                "enum": [j["id"] for j in found],
                "enumNames": [f"{j['title']} · {clock(j.get('duration') or 0)}" + (f" · {j['bpm']} BPM" if j.get("bpm") else "") for j in found],
            }}},
        })
        if reply.get("action") != "accept" or not (reply.get("content") or {}).get("song"):
            return {"note": "The person did not pick a song."}
        song = str(reply["content"]["song"])
    if not any(j["id"] == song for j in songs()):
        raise ToolError(f"ไม่พบเพลง {song} (หรือยังแยกไม่เสร็จ)")
    S.song = song
    return {"selected": summary(song)}


def t_song_overview(args):
    return summary(S.song_id(args))


def t_check_metronome(args):
    song = S.song_id(args)
    query = {"bpm": args["bpm"]} if args.get("bpm") else {}
    result = api("GET", f"/api/jobs/{song}/click/inspect" + ("?" + urllib.parse.urlencode(query) if query else ""))
    if not result.get("has_drums"):
        result["note"] = "No drum track: compare against the bass hits instead (inspect_beats)."
    return result


def t_inspect_beats(args):
    song = S.song_id(args)
    start = seconds(args["start"])
    end = seconds(args.get("end", start + 20))
    return api("GET", f"/api/jobs/{song}/click/inspect?" + urllib.parse.urlencode({"start": start, "end": end}))["window"]


def t_repair_metronome(args):
    song = S.song_id(args)
    v = view(song)
    g = grid_of(v)
    body = {"beats": g["beats"], "downbeats": g["downbeats"], "meter": g["meter"], "bpm": args.get("bpm")}
    result = api("POST", f"/api/jobs/{song}/click/repair", body)
    if not result["fixed"] and not result.get("rebuilt"):
        return {"changed": False, "spots_it_could_not_fix": [clock(t) for t in result["left"]]}
    remember_grid(song, v)
    save_grid(song, result["beats"], result["downbeats"], result["meter"])
    return {"changed": True, "rebuilt_from_scratch": result.get("rebuilt"),
            "fixed": [{"from": clock(f["start"]), "to": clock(f["end"]), "beats_added": f["change"], "had_drums": f["drums"]} for f in result["fixed"]],
            "spots_it_could_not_fix": [clock(t) for t in result["left"]]}


def t_replace_beats(args):
    song = S.song_id(args)
    start, end = seconds(args["start"]), seconds(args["end"])
    new = sorted(round(seconds(t), 3) for t in args["beats"])
    bars = {round(seconds(t), 3) for t in args.get("bar_lines", [])}
    if not end > start or any(not start < t < end for t in new):
        raise ToolError("ทุกบีตใหม่ต้องอยู่ระหว่าง start กับ end (ไม่รวมขอบ)")
    if not bars <= set(new):
        raise ToolError("bar_lines ต้องเป็นเวลาที่อยู่ใน beats")
    v = view(song)
    g = grid_of(v)
    kept = [t for t in g["beats"] if not start < t < end]
    merged = sorted(kept + new)
    if any(b - a < 0.15 for a, b in zip(merged, merged[1:])):
        raise ToolError("มีบีตห่างกันน้อยกว่า 0.15 วินาที")
    downs = [d for d in g["downbeats"] if not start < d < end] + sorted(bars)
    remember_grid(song, v)
    save_grid(song, merged, downs, g["meter"])
    return {"replaced": len(g["beats"]) - len(kept), "with": len(new),
            "now": t_inspect_beats({"song_id": song, "start": max(start - 2, -60), "end": end + 2})}


def t_set_beat_one(args):
    song = S.song_id(args)
    v = view(song)
    g = grid_of(v)
    meter = int(args.get("meter") or g["meter"])
    if not 1 <= meter <= 12:
        raise ToolError("อัตราจังหวะต้องอยู่ระหว่าง 1 ถึง 12")
    anchor = nearest_index(g["beats"], seconds(args["time"]))
    remember_grid(song, v)
    save_grid(song, g["beats"], bar_lines(g["beats"], anchor, meter), meter)
    return {"beat_one_at": clock(g["beats"][anchor]), "meter": meter}


def t_shift_metronome(args):
    song = S.song_id(args)
    ms = float(args["ms"])
    if not -500 <= ms <= 500:
        raise ToolError("ขยับได้ไม่เกิน 500 ms")
    v = view(song)
    g = grid_of(v)
    remember_grid(song, v)
    save_grid(song, [t + ms / 1000 for t in g["beats"]], [t + ms / 1000 for t in g["downbeats"]], g["meter"])
    return {"shifted_ms": ms}


def t_reset_metronome(args):
    song = S.song_id(args)
    v = view(song)
    remember_grid(song, v)
    api("DELETE", f"/api/jobs/{song}/click")
    return {"metronome": "back to the automatic one"}


def t_lock_tempo(args):
    song = S.song_id(args)
    v = view(song)
    bpm = args.get("bpm")
    previous = (v.get("tempo_lock") or {}).get("bpm")
    result = api("PUT", f"/api/jobs/{song}/tempo-lock", {"bpm": bpm, "position": None})
    S.remember(song, "lock", previous)
    return {"locked_bpm": result.get("bpm"), "stretch_percent": result.get("range"),
            "listen_here": [{"at": clock(s["time"]), "why": s.get("why")} for s in result.get("listen", [])]}


def t_make_click_steady(args):
    song = S.song_id(args)
    bpm = float(args["bpm"])
    if (view(song).get("tempo_lock") or {}).get("bpm"):
        raise ToolError("เพลงนี้ล็อกความเร็วอยู่แล้ว ปลดล็อกก่อน (lock_tempo กับ bpm เป็น null)")
    repaired = t_repair_metronome({"song_id": song, "bpm": bpm})
    try:
        locked = t_lock_tempo({"song_id": song, "bpm": bpm})
    except ToolError as error:
        return {"repair": repaired, "lock_failed": str(error), "note": "The repair is kept; undo takes it back."}
    return {"repair": repaired, "lock": locked}


def t_get_cue_library(args):
    lib = library()
    return {"voices": lib["voices"], "count_styles": lib["count_styles"],
            "phrases": {c["id"]: {"label": c["label"], "items": [[i["id"], i["th"], i["en"]] for i in c["items"]]} for c in lib["categories"]}}


def _put_cues(song: str, before: list[dict], after: list[dict]) -> list[dict]:
    S.remember(song, "cues", before)
    result = api("PUT", f"/api/jobs/{song}/cues", {"cues": after})
    return [{"id": c["id"], "at": clock(c["time"]), "text": c.get("label") or c.get("text") or c.get("style")} for c in result["cues"]]


def t_add_cue(args):
    song = S.song_id(args)
    v = view(song)
    g = grid_of(v)
    beats = g["beats"]
    voice = voice_for(args.get("voice"))
    if args.get("section"):
        wanted = str(args["section"]).strip().lower()
        section = next((s for s in v["sections"] if s["label"].lower() == wanted), None)
        if not section:
            raise ToolError("ไม่พบท่อน " + str(args["section"]) + " มี: " + ", ".join(s["label"] for s in v["sections"]))
        target = section["start"]
    elif "time" in args:
        target = seconds(args["time"])
    else:
        raise ToolError("ใส่ time หรือ section")
    if beats:
        target = beats[nearest_index(beats, target)]
    kind = "count" if args.get("count_style") else "phrase"
    cue = {"id": uuid.uuid4().hex[:10], "kind": kind, "voice": voice["id"], "auto": False}
    if kind == "count":
        if args["count_style"] not in {s["id"] for s in library()["count_styles"]}:
            raise ToolError("ไม่รู้จักรูปแบบนับเข้านี้ ดู get_cue_library")
        cue.update(style=args["count_style"], time=round(target, 3))
    else:
        text = args.get("text") or (phrase(args["phrase_id"], voice["lang"]) if args.get("phrase_id") else None)
        if not text:
            raise ToolError("ใส่ text หรือ phrase_id")
        back = PLACES.get(args.get("place", "1_bar_before"))
        if back is None:
            raise ToolError("place ต้องเป็น " + ", ".join(PLACES))
        steps = {"bar": g["meter"], "2bar": 2 * g["meter"]}.get(back, back)
        index = nearest_index(beats, target) - steps if beats else 0
        time = beats[index] if beats and index >= 0 else max(0.0, target - steps * 0.5)
        cue.update(text=text, label=text, time=round(time, 3))
    before = cues_of(v)
    saved = _put_cues(song, before, before + [cue])
    style = next((s["label"] for s in library()["count_styles"] if s["id"] == cue.get("style")), None)
    return {"added": {"at": clock(cue["time"]), "text": cue.get("text") or f"นับเข้า: {style}", "voice": voice["id"]}, "cues": saved}


def t_auto_cues(args):
    song = S.song_id(args)
    v = view(song)
    g = grid_of(v)
    beats = g["beats"]
    voice = voice_for(args.get("voice"))
    lang = voice["lang"]
    numbers = library()["numbers"][lang]
    made = []
    for i, section in enumerate(v["sections"]):
        if section["start"] - (v.get("lead_in") or {}).get("seconds", 0) < 0.5 and i == 0:
            continue
        match = re.match(r"^(.*?)(?:\s(\d+))?$", section["label"])
        kind = section.get("kind") or (match.group(1) if match else section["label"])
        number = int(match.group(2)) if match and match.group(2) else None
        if kind in ("Verse", "Chorus"):
            top = 4 if kind == "Verse" else 3
            text = phrase(f"{kind.lower()}_{number}" if number and 1 <= number <= top else kind.lower(), lang)
        elif kind in SECTION_ITEMS:
            text = phrase(SECTION_ITEMS[kind], lang) + (f" {numbers[number - 1]}" if number and number <= len(numbers) else "")
        elif kind.startswith("Theme"):
            text = ("ธีม " if lang == "th" else "Theme ") + kind[-1]
        else:
            continue
        index = nearest_index(beats, section["start"]) if beats else 0
        time = beats[max(0, index - g["meter"])] if beats else max(0.0, section["start"] - 2)
        made.append({"id": uuid.uuid4().hex[:10], "kind": "phrase", "text": text, "label": text, "time": round(time, 3), "voice": voice["id"], "auto": True})
    if not made:
        raise ToolError("ไม่พบท่อนเพลงที่จะใส่ cue ให้")
    before = cues_of(v)
    saved = _put_cues(song, before, [c for c in before if not c.get("auto")] + made)
    return {"added": len(made), "cues": saved}


def t_remove_cues(args):
    song = S.song_id(args)
    v = view(song)
    before = cues_of(v)
    ids = set(args.get("ids") or [])
    if args.get("all"):
        after = []
    elif ids:
        after = [c for c in before if c["id"] not in ids]
    else:
        raise ToolError("ใส่ ids หรือ all: true")
    return {"removed": len(before) - len(after), "cues": _put_cues(song, before, after)}


def t_set_lead_in(args):
    song = S.song_id(args)
    v = view(song)
    bars = int(args["bars"])
    previous = (v.get("lead_in") or {}).get("bars", 0)
    voice = voice_for(args.get("voice")) if library()["voices"] else None
    result = api("PUT", f"/api/jobs/{song}/lead-in", {"bars": bars, "count_voice": voice["id"] if voice else None})
    S.remember(song, "lead", previous)
    return {"lead_in_bars": bars, "seconds": result.get("seconds"), "count_in_added": result.get("added_count")}


def t_undo(args):
    song = S.song_id(args)
    stack = S.undo.get(song) or []
    if not stack:
        return {"undone": None, "note": "Nothing from this conversation to undo for this song."}
    kind, state = stack.pop()
    if kind == "click":
        if state is None:
            api("DELETE", f"/api/jobs/{song}/click")
        else:
            save_grid(song, state["beats"], state["downbeats"], state["meter"])
    elif kind == "cues":
        api("PUT", f"/api/jobs/{song}/cues", {"cues": state})
    elif kind == "lead":
        api("PUT", f"/api/jobs/{song}/lead-in", {"bars": state, "count_voice": None})
    elif kind == "lock":
        api("PUT", f"/api/jobs/{song}/tempo-lock", {"bpm": state, "position": None})
    return {"undone": kind, "left_to_undo": len(stack)}


SONG = {"song_id": {"type": "string", "description": "Song id (default: the song picked with choose_song)"}}
TIME = {"type": ["number", "string"], "description": "Seconds on the timeline, or m:ss.s"}

TOOLS = [
    ("list_songs", t_list_songs, "List the songs separated in Prism on this computer.", {}, True),
    ("choose_song", t_choose_song, "Pick the song to work on. Without song_id the person picks it from a list in the chat.", SONG, True),
    ("song_overview", t_song_overview, "Sections, tempo, meter, lead-in, tempo lock and cues of the chosen song.", SONG, True),
    ("check_metronome", t_check_metronome, "How the metronome sits on the drums: beats out of place, bars with the wrong number of beats, and whether a tempo lock at bpm would pass.",
     {**SONG, "bpm": {"type": "number", "description": "Tempo to check a lock against (default: the average)"}}, True),
    ("inspect_beats", t_inspect_beats, "Every beat between start and end (max 30 s) with its place in the bar and the nearest drum hit in ms, plus the drum and bass hits with their strength.",
     {**SONG, "start": TIME, "end": TIME}, True, ["start"]),
    ("repair_metronome", t_repair_metronome, "Prism's automatic repair: rebuild the spots where beats slipped off the drums (missing, extra or dragged beats). Saved; undo takes it back.",
     {**SONG, "bpm": {"type": "number", "description": "Tempo the band counts in, if known (helps when the grid is at half or double speed)"}}, False),
    ("replace_beats", t_replace_beats, "Replace every beat strictly between start and end with the given beats. bar_lines = those of the new beats that are beat 1.",
     {**SONG, "start": TIME, "end": TIME, "beats": {"type": "array", "items": TIME}, "bar_lines": {"type": "array", "items": TIME}}, False, ["start", "end", "beats"]),
    ("set_beat_one", t_set_beat_one, "Make the beat nearest time beat 1 and lay bar lines every meter beats across the song (optionally change the meter).",
     {**SONG, "time": TIME, "meter": {"type": "integer"}}, False, ["time"]),
    ("shift_metronome", t_shift_metronome, "Move every beat by ms (negative = earlier).", {**SONG, "ms": {"type": "number"}}, False, ["ms"]),
    ("reset_metronome", t_reset_metronome, "Throw away hand edits and use the automatic metronome again.", SONG, False),
    ("lock_tempo", t_lock_tempo, "Stretch every track so each beat lasts the same at bpm (null unlocks). Reports the spots worth listening to.",
     {**SONG, "bpm": {"type": ["number", "null"]}}, False, ["bpm"]),
    ("make_click_steady", t_make_click_steady, "One step for a song recorded without a click: repair the metronome, then lock the tempo at bpm.",
     {**SONG, "bpm": {"type": "number"}}, False, ["bpm"]),
    ("get_cue_library", t_get_cue_library, "Voices on this computer, count-in styles and the cue phrases (id, Thai, English).", {}, True),
    ("auto_cues", t_auto_cues, "Put the section name as a cue one bar before every section (replaces earlier automatic cues, keeps the rest).",
     {**SONG, "voice": {"type": "string"}}, False),
    ("add_cue", t_add_cue, "Add one cue. Give time or section (where the band comes in). Phrase: text or phrase_id, place = at | 2_beats_before | 1_bar_before (default) | 2_bars_before. Count-in: count_style, spoken on the beats before time.",
     {**SONG, "time": TIME, "section": {"type": "string"}, "text": {"type": "string"}, "phrase_id": {"type": "string"},
      "count_style": {"type": "string"}, "place": {"type": "string", "enum": list(PLACES)}, "voice": {"type": "string"}}, False),
    ("remove_cues", t_remove_cues, "Remove cues by id, or all of them.",
     {**SONG, "ids": {"type": "array", "items": {"type": "string"}}, "all": {"type": "boolean"}}, False),
    ("set_lead_in", t_set_lead_in, "Bars of silence before the song for counting in (0, 1, 2, 3, 4 or 8), with a spoken count.",
     {**SONG, "bars": {"type": "integer", "enum": [0, 1, 2, 3, 4, 8]}, "voice": {"type": "string"}}, False, ["bars"]),
    ("undo", t_undo, "Take back the last change made from this conversation to the song.", SONG, False),
]


def tool_list() -> list[dict]:
    out = []
    for entry in TOOLS:
        name, _, description, props, read_only = entry[:5]
        required = entry[5] if len(entry) > 5 else []
        out.append({"name": name, "description": description,
                    "inputSchema": {"type": "object", "properties": props, "required": required},
                    "annotations": {"readOnlyHint": read_only, "destructiveHint": False, "openWorldHint": False}})
    return out


# ------------------------------------------------------------------ JSON-RPC over stdio

def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read() -> dict | None:
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if line:
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue


def request_client(method: str, params: dict) -> dict:
    """Ask the client something (the song picker) and wait for its answer."""
    rid = f"prism-{uuid.uuid4().hex[:8]}"
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    while True:
        message = read()
        if message is None:
            raise ToolError("การเชื่อมต่อถูกปิด")
        if message.get("id") == rid and "method" not in message:
            if "error" in message:
                raise ToolError("เลือกเพลงไม่สำเร็จ: " + str(message["error"].get("message")))
            return message.get("result") or {}
        if "method" in message and "id" in message:
            send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": "busy"}})


def handle(message: dict) -> None:
    method, mid, params = message.get("method"), message.get("id"), message.get("params") or {}
    if mid is None:
        return  # notifications (initialized, cancelled) need no answer
    if method == "initialize":
        S.can_elicit = "elicitation" in (params.get("capabilities") or {})
        wanted = params.get("protocolVersion")
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": wanted if wanted in VERSIONS else VERSIONS[1],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "prism", "title": "Prism", "version": "1.0"},
            "instructions": INSTRUCTIONS,
        }})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}})
    elif method == "tools/call":
        name = params.get("name")
        func = next((entry[1] for entry in TOOLS if entry[0] == name), None)
        if func is None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}})
            return
        try:
            result = func(params.get("arguments") or {})
            text, failed = json.dumps(result, ensure_ascii=False), False
        except ToolError as error:
            text, failed = str(error), True
        except (KeyError, TypeError, ValueError) as error:
            text, failed = f"ข้อมูลที่ส่งมาไม่ครบหรือไม่ถูกต้อง: {error}", True
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": failed}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}})


def main() -> None:
    while True:
        message = read()
        if message is None:
            return
        try:
            handle(message)
        except Exception as error:  # never let one bad request end the session
            if message.get("id") is not None:
                send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": str(error)}})
            print(f"prism mcp: {error!r}", file=sys.stderr)


if __name__ == "__main__":
    main()

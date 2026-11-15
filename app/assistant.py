"""Ask Claude to look over a metronome and fix what the automatic repair could not.

Opt-in, and the only part of Prism that talks to the internet after the models are
downloaded. Only numbers leave the machine: beat times, drum and bass hit times and the
section names. No audio, no file names, no song title.

Claude works on a copy of the grid through a few tools (look at a stretch of beats next
to the drum hits, run the automatic repair, replace beats, check whether a tempo lock
would pass) and ends with a short explanation in Thai. Nothing is saved here: the page
shows the result and the person decides whether to use it.

Two ways to reach Claude, tried in this order:
- the Claude Code CLI (`claude -p`), using the login already on this machine: no key, and
  with every built-in tool off, so Claude can only call Prism's five tools (bench_mcp.py);
- the Claude API with ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, an `ant auth login`
  profile, or a key pasted into the page, kept in KEY_FILE (owner-readable, outside git).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import fsutil, gridfix, warp

MODEL = "claude-opus-5-5"
PRICE_PER_MTOK = (4.0, 20.0)   # input, output in USD for MODEL
MAX_TURNS = 30
MIN_GAP = 0.15
KEY_FILE = Path.home() / ".config" / "prism" / "anthropic-key"
CLI_MODEL = "opus"         # Claude Code's alias for its newest Opus
CLI_TIMEOUT = 600          # seconds
CLI_BUDGET_USD = 2.0       # only bites when Claude Code is billed per use
CLI_PLACES = ["/opt/homebrew/bin/claude", "/usr/local/bin/claude", "~/.local/bin/claude", "~/.claude/local/claude",
              "~/.local/bin/claude.exe", "~/AppData/Roaming/npm/claude.cmd", "~/.claude/local/claude.exe"]  # last three: Windows

SYSTEM = """You help a live-show music director fix a song's metronome (click track) in Prism.

The song was recorded without a click, so its tempo drifts. The metronome must follow the
recording beat by beat; afterwards Prism stretches the audio so every beat lasts the same
(a "tempo lock"). A lock is refused where it would have to stretch the audio more than 15 %,
which almost always means the metronome has an extra or a missing beat there, or beats
placed off the music.

You cannot hear the song. You get numbers: beat times, drum hit times (and bass hits for
stretches without drums), section names. Times are seconds on the song's timeline.

Work like this:
1. Call overview. If the automatic repair has not been run yet, run it first: it fixes most
   spots and is consistent.
2. For every spot still listed, call get_beats on it with a few bars of margin. Work out
   what the music does: how long a beat is around the spot, which drum hits are beats
   (strong, regular) and which are fills or offbeats, whether a bar there is really shorter
   or longer (pop songs sometimes have one 2/4 bar before a new section).
3. Fix it with replace_beats: beats evenly spaced at the local tempo, on the drum hits where
   drums play, bar lines every `meter` beats except where the music clearly has a short or
   long bar. Keep the change as small as the problem allows; do not touch stretches that are
   fine.
4. Call check_lock after your changes. Stop when it passes or when you cannot do better.

Never invent precision you do not have: in stretches without drums or bass, keep the beat
count and spacing simple and say the spot needs listening.

Finish with a short answer in natural, spoken Thai (no headings, at most about 10 lines):
what you changed at each time (m:ss), why, and which spots the person should listen to.
Write times as m:ss. Use the words the person sees in Prism, not English jargon or tool
names: "ซ่อมอัตโนมัติ" (auto repair), "ล็อกความเร็ว" (lock), "ยืดเสียง" (stretch),
"ห้องที่มีไม่ครบ 4 จังหวะ" (odd bar), "บีต", "หัวห้อง" (bar line)."""

TOOLS = [
    {
        "name": "overview",
        "description": "The song at a glance: length, meter, average tempo, the target lock tempo, sections, "
                       "how far the metronome sits from the drums, and the spots a tempo lock would refuse "
                       "or stretch by more than 10 %.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_beats",
        "description": "Beats between start and end (seconds) with their index, time, gap to the next beat, "
                       "position in the bar (1 = bar line) and distance to the nearest drum hit, plus the drum "
                       "and bass hits in that stretch with their strength (0-1). Ask for at most 30 seconds.",
        "input_schema": {
            "type": "object",
            "properties": {"start": {"type": "number"}, "end": {"type": "number"}},
            "required": ["start", "end"], "additionalProperties": False,
        },
    },
    {
        "name": "apply_auto_repair",
        "description": "Run Prism's automatic repair on the current metronome (re-space slipped beats onto the "
                       "drums, remove or add beats where the count is clearly off). Returns what it changed and "
                       "what it left.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "replace_beats",
        "description": "Replace every beat strictly between start and end (seconds) with `beats`. `bar_lines` "
                       "lists which of the new beats start a bar. Beats outside the range stay. Beats must be "
                       "increasing, inside the range and at least 0.15 s apart. Returns the stretch as it now is.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "number"}, "end": {"type": "number"},
                "beats": {"type": "array", "items": {"type": "number"}},
                "bar_lines": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["start", "end", "beats", "bar_lines"], "additionalProperties": False,
        },
    },
    {
        "name": "check_lock",
        "description": "Whether a tempo lock at the target tempo would pass now: spots over 15 %, the stretch "
                       "range, and bars that are not `meter` beats long.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class AssistantError(RuntimeError):
    """Shown to the person as is."""


# ---------------------------------------------------------------- credentials

def claude_cli() -> str | None:
    """Path of the Claude Code CLI, also when the server was started without the shell PATH."""
    found = shutil.which("claude")
    if found:
        return found
    for place in CLI_PLACES:
        path = Path(place).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _cli_env() -> dict:
    """The environment for `claude`. When Prism itself was started from inside a Claude
    Code session, that session's variables (its own proxy and token) leak in; drop them
    so the CLI uses the login of the person at this Mac."""
    env = dict(os.environ)
    if env.get("CLAUDECODE"):
        for name in list(env):
            if name.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PREVIEW_")) or name in (
                    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "ANTHROPIC_BASE_URL", "USE_LOCAL_OAUTH", "USE_STAGING_OAUTH"):
                env.pop(name)
    return env


def cli_logged_in() -> bool:
    executable = claude_cli()
    if not executable:
        return False
    try:
        done = subprocess.run([executable, "auth", "status"], capture_output=True, text=True, timeout=20, env=_cli_env())
        return bool(json.loads(done.stdout).get("loggedIn"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError):
        return False


def backend() -> str | None:
    """"cli" (Claude Code installed and logged in), "api" (a key) or None (not set up)."""
    if claude_cli() and cli_logged_in():
        return "cli"
    return "api" if credential_source() else None


def credential_source() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "env"
    if KEY_FILE.exists() and KEY_FILE.read_text(encoding="utf-8").strip():
        return "file"
    if (Path.home() / ".config" / "anthropic").exists():
        return "profile"
    return None


def save_key(key: str) -> None:
    key = key.strip()
    if not key.startswith("sk-ant-") or len(key) > 300 or any(c.isspace() for c in key):
        raise AssistantError("รูปแบบ API key ไม่ถูกต้อง (ต้องขึ้นต้นด้วย sk-ant-)")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = KEY_FILE.with_suffix(".tmp")
    temporary.write_text(key, encoding="utf-8")
    os.chmod(temporary, 0o600)
    fsutil.replace(temporary, KEY_FILE)


def forget_key() -> None:
    KEY_FILE.unlink(missing_ok=True)


def _client():
    try:
        import anthropic
    except ImportError:
        raise AssistantError("ยังไม่ได้ติดตั้งแพ็กเกจ anthropic ปิดแล้วเปิด Prism ใหม่ด้วย start.command")

    if credential_source() == "file":
        return anthropic.Anthropic(api_key=KEY_FILE.read_text(encoding="utf-8").strip())
    return anthropic.Anthropic()


# ---------------------------------------------------------------- the grid Claude works on

class Workbench:
    def __init__(self, beats, downbeats, meter, bpm, drum_hits, bass_hits, drum_strength, bass_strength, sections, duration):
        self.beats = [round(float(t), 3) for t in sorted(beats)]
        self.downs = {round(float(t), 3) for t in downbeats}
        self.meter = int(meter)
        self.bpm = float(bpm)
        self.drums, self.drum_strength = drum_hits, drum_strength
        self.bass, self.bass_strength = bass_hits, bass_strength
        self.sections = sections
        self.duration = duration
        self.changes: list[str] = []

    def to_state(self) -> dict:
        """Everything needed to rebuild the bench in another process (the MCP server)."""
        return {
            "beats": self.beats, "downbeats": sorted(self.downs), "meter": self.meter, "bpm": self.bpm,
            "drum_hits": [float(t) for t in self.drums], "drum_strength": [float(s) for s in self.drum_strength],
            "bass_hits": [float(t) for t in self.bass], "bass_strength": [float(s) for s in self.bass_strength],
            "sections": self.sections, "duration": float(self.duration), "changes": self.changes,
        }

    @classmethod
    def from_state(cls, state: dict) -> "Workbench":
        bench = cls(state["beats"], state["downbeats"], state["meter"], state["bpm"],
                    np.asarray(state["drum_hits"]), np.asarray(state["bass_hits"]),
                    np.asarray(state["drum_strength"]), np.asarray(state["bass_strength"]),
                    state["sections"], state["duration"])
        bench.changes = list(state.get("changes", []))
        return bench

    @staticmethod
    def _clock(t: float) -> str:
        return f"{int(t // 60)}:{t % 60:05.2f}"

    def _spots(self, limit: float) -> list[dict]:
        b = np.asarray(self.beats)
        return [{"from": self._clock(b[lo]), "to": self._clock(b[hi]), "start": float(b[lo]), "end": float(b[hi]),
                 "worst_stretch_percent": round(worst * 100)} for lo, hi, worst in gridfix.problem_spots(b, self.bpm, limit, join=8)]

    def overview(self) -> dict:
        b = self.beats
        return {
            "duration": round(self.duration, 2), "meter": self.meter, "beats": len(b),
            "average_bpm": round(60 * (len(b) - 1) / (b[-1] - b[0]), 2), "target_lock_bpm": self.bpm,
            "sections": self.sections,
            "share_of_song_off_the_drums": round(gridfix.drift_share(b, self.drums), 2),
            "spots_over_15_percent": self._spots(warp.MAX_STRETCH),
            "spots_over_10_percent": self._spots(gridfix.FIX_STRETCH),
            "odd_bars": self._odd_bars(),
        }

    def _nearest(self, hits: np.ndarray, t: float) -> float | None:
        if not len(hits):
            return None
        i = int(np.clip(np.searchsorted(hits, t), 1, len(hits) - 1))
        return float(hits[i - 1] if abs(hits[i - 1] - t) <= abs(hits[i] - t) else hits[i])

    def get_beats(self, start: float, end: float) -> dict:
        end = min(end, start + 30)
        b = self.beats
        rows = []
        bar_pos = None
        for i, t in enumerate(b):
            if t in self.downs:
                bar_pos = 1
            elif bar_pos is not None:
                bar_pos += 1
            if start <= t <= end:
                hit = self._nearest(self.drums, t)
                rows.append({
                    "i": i, "t": t, "gap": round(b[i + 1] - t, 3) if i + 1 < len(b) else None, "bar_pos": bar_pos,
                    "drum_ms": None if hit is None or abs(hit - t) > 0.2 else round((hit - t) * 1000),
                })
        pick = lambda hits, strength: [[round(float(h), 3), round(float(s), 2)] for h, s in zip(hits, strength) if start <= h <= end]  # noqa: E731
        return {"beats": rows, "drum_hits": pick(self.drums, self.drum_strength), "bass_hits": pick(self.bass, self.bass_strength)}

    def apply_auto_repair(self) -> dict:
        result = gridfix.repair(self.beats, sorted(self.downs), self.meter, self.drums, self.bpm)
        self.beats, self.downs = result["beats"], set(result["downbeats"])
        if result["fixed"]:
            self.changes.append(f"auto repair: {len(result['fixed'])} spots")
        return {"fixed": [{"from": self._clock(f["start"]), "to": self._clock(f["end"]), "beats_added": f["change"],
                           "had_drums": f["drums"]} for f in result["fixed"]],
                "left": [self._clock(t) for t in result["left"]]}

    def replace_beats(self, start: float, end: float, beats: list[float], bar_lines: list[float]) -> dict:
        new = [round(float(t), 3) for t in beats]
        if not (np.isfinite(start) and np.isfinite(end) and end > start):
            raise AssistantError("ช่วงไม่ถูกต้อง")
        if any(not (start < t < end) for t in new):
            return {"error": "every beat must be strictly between start and end"}
        if any(b - a < MIN_GAP for a, b in zip(new, new[1:])):
            return {"error": f"beats must be increasing and at least {MIN_GAP} s apart"}
        kept = [t for t in self.beats if not (start < t < end)]
        merged = sorted(kept + new)
        if any(b - a < MIN_GAP for a, b in zip(merged, merged[1:])):
            return {"error": f"a new beat is closer than {MIN_GAP} s to a beat outside the range"}
        bars = {round(float(t), 3) for t in bar_lines}
        if not bars <= set(new):
            return {"error": "bar_lines must be a subset of beats"}
        removed = len(self.beats) - len(kept)
        self.beats = merged
        self.downs = {d for d in self.downs if not (start < d < end)} | bars
        self.changes.append(f"replaced {removed} beats with {len(new)} between {self._clock(start)} and {self._clock(end)}")
        return self.get_beats(max(0.0, start - 2), end + 2)

    def _odd_bars(self) -> list[dict]:
        idx = [i for i, t in enumerate(self.beats) if t in self.downs]
        return [{"at": self._clock(self.beats[i]), "beats": idx[k + 1] - i}
                for k, i in enumerate(idx[:-1]) if idx[k + 1] - i != self.meter]

    def check_lock(self) -> dict:
        try:
            rng = warp.stretch_range(self.beats, self.bpm)
            passes = True
        except warp.WarpError as error:
            rng, passes = str(error), False
        return {"passes": passes, "stretch_range_percent": rng, "spots_over_15_percent": self._spots(warp.MAX_STRETCH),
                "odd_bars": self._odd_bars()}


# ---------------------------------------------------------------- the conversation

def run(bench: Workbench, on_progress=None) -> dict:
    """Ask Claude through whichever way this machine has: the Claude Code CLI (the person's
    own login, no key) first, else the API with a key."""
    if backend() == "cli":
        return run_cli(bench)
    return run_api(bench, on_progress)


def run_cli(bench: Workbench) -> dict:
    """Run `claude -p` headless with every built-in tool switched off and only Prism's five
    tools available, served by app.bench_mcp from a state file in a temporary folder."""
    executable = claude_cli()
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="prism-claude-") as folder:
        work = Path(folder)
        state = work / "state.json"
        state.write_text(json.dumps(bench.to_state()), encoding="utf-8")
        config = {"mcpServers": {"prism": {
            "command": sys.executable, "args": ["-m", "app.bench_mcp", str(state)],
            "env": {"PYTHONPATH": str(root)},
        }}}
        allowed = ",".join(f"mcp__prism__{t['name']}" for t in TOOLS)
        command = [
            executable, "-p", "Please check and fix this song's metronome so a tempo lock passes.",
            "--system-prompt", SYSTEM, "--model", CLI_MODEL, "--effort", "high",
            "--tools", "", "--mcp-config", json.dumps(config), "--strict-mcp-config",
            "--allowedTools", allowed, "--permission-mode", "dontAsk",
            "--setting-sources", "", "--no-session-persistence", "--output-format", "json",
            "--max-budget-usd", str(CLI_BUDGET_USD),
        ]
        try:
            done = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=CLI_TIMEOUT, env=_cli_env())
        except subprocess.TimeoutExpired:
            raise AssistantError("Claude ใช้เวลานานเกินไป ลองใหม่อีกครั้ง")
        try:
            report = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {}
        except json.JSONDecodeError:
            report = {}
        if done.returncode != 0 and not report:
            detail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["ไม่ทราบสาเหตุ"]
            if "login" in detail[0].lower() or "auth" in detail[0].lower():
                raise AssistantError("Claude Code ยังไม่ได้ล็อกอิน เปิด Terminal แล้วพิมพ์ claude เพื่อล็อกอินก่อน")
            raise AssistantError(f"เรียก Claude Code ไม่สำเร็จ: {detail[0][:200]}")
        if report.get("is_error"):
            message = str(report.get("result") or report.get("subtype") or "")
            if any(word in message.lower() for word in ("login", "log in", "authenticate", "expired")):
                raise AssistantError("Claude Code ยังไม่ได้ล็อกอิน (หรือหมดอายุ) เปิด Terminal แล้วพิมพ์ claude auth login แล้วลองใหม่")
            raise AssistantError(f"Claude Code ทำไม่สำเร็จ: {message[:200]}")
        changed = Workbench.from_state(json.loads(state.read_text(encoding="utf-8")))
    bench.beats, bench.downs, bench.changes = changed.beats, changed.downs, changed.changes
    usage = report.get("usage") or {}
    used_in = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {
        "explanation": str(report.get("result") or "").strip(), "changes": bench.changes,
        "tokens": {"input": used_in, "output": int(usage.get("output_tokens") or 0)},
        "cost_usd": round(float(report.get("total_cost_usd") or 0), 3), "via": "cli", "lock": bench.check_lock(),
    }


def run_api(bench: Workbench, on_progress=None) -> dict:
    """The same work through the Claude API with a key."""
    client = _client()
    handlers = {
        "overview": lambda _: bench.overview(),
        "get_beats": lambda a: bench.get_beats(float(a["start"]), float(a["end"])),
        "apply_auto_repair": lambda _: bench.apply_auto_repair(),
        "replace_beats": lambda a: bench.replace_beats(float(a["start"]), float(a["end"]), a["beats"], a["bar_lines"]),
        "check_lock": lambda _: bench.check_lock(),
    }
    import anthropic

    messages: list = [{"role": "user", "content": "Please check and fix this song's metronome so a tempo lock passes."}]
    used_in = used_out = 0
    text = ""
    for turn in range(MAX_TURNS):
        try:
            response = client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise AssistantError("API key ใช้ไม่ได้ ตรวจสอบหรือใส่ใหม่ในหน้าตั้งค่า Claude")
        except anthropic.PermissionDeniedError:
            raise AssistantError("บัญชีนี้ยังใช้ Claude API ไม่ได้ (สิทธิ์หรือเครดิตไม่พอ)")
        except anthropic.RateLimitError:
            raise AssistantError("เรียก Claude ถี่เกินไป รอสักครู่แล้วลองใหม่")
        except anthropic.APIConnectionError:
            raise AssistantError("ต่อ Claude ไม่ได้ ตรวจสอบอินเทอร์เน็ต")
        except anthropic.APIStatusError as error:
            raise AssistantError(f"Claude ตอบกลับผิดพลาด ({error.status_code})")
        used_in += response.usage.input_tokens + (response.usage.cache_read_input_tokens or 0) + (response.usage.cache_creation_input_tokens or 0)
        used_out += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise AssistantError("Claude ปฏิเสธคำขอนี้")
        text = "\n".join(b.text for b in response.content if b.type == "text").strip() or text
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in calls:
            if on_progress:
                on_progress(turn, call.name)
            try:
                output = handlers[call.name](call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(output, ensure_ascii=False)})
            except (KeyError, TypeError, ValueError) as error:
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True, "content": f"bad input: {error}"})
        messages.append({"role": "user", "content": results})
    else:
        text = text or "Claude ทำไม่จบในจำนวนรอบที่กำหนด ผลที่ได้คือที่แก้ไปถึงตอนนั้น"
    cost = used_in / 1e6 * PRICE_PER_MTOK[0] + used_out / 1e6 * PRICE_PER_MTOK[1]
    return {"explanation": text, "changes": bench.changes, "tokens": {"input": used_in, "output": used_out},
            "cost_usd": round(cost, 3), "via": "api", "lock": bench.check_lock()}

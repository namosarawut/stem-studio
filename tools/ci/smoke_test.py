"""End-to-end smoke test, mainly for Windows (see .github/workflows/windows.yml).

Starts the server on an empty data folder, separates a short generated clip on whatever
device is there (CPU in CI), then checks the parts that differ between systems:
- the metronome can be edited while click.wav is still being downloaded (Windows will
  not replace an open file; app/fsutil.py waits for it)
- an edit made during a ZIP download still reaches the next ZIP (the stale marker)
- cue speech renders with the voices this machine has
Usage: python tools/ci/smoke_test.py   (from the repo root, with the app's venv)
"""
from __future__ import annotations

import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("SMOKE_PORT", "8799"))
BASE = f"http://127.0.0.1:{PORT}"
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        failures.append(what)


def call(method: str, path: str, body: dict | None = None, timeout: float = 120) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def upload(path: Path) -> dict:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nhtdemucs\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="smoke.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
        path.read_bytes(), f"\r\n--{boundary}--\r\n".encode(),
    ]
    request = urllib.request.Request(BASE + "/api/jobs", data=b"".join(parts), method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def test_song(path: Path, seconds: float = 24.0, bpm: float = 120.0, sr: int = 44100) -> None:
    """Kick and snare on a steady beat with a bass line: enough for beats and stems."""
    t = np.arange(int(seconds * sr)) / sr
    audio = np.zeros_like(t)
    beat = 60.0 / bpm
    for k in range(int(seconds / beat)):
        start = int(k * beat * sr)
        n = min(int(0.25 * sr), len(t) - start)
        decay = np.exp(-np.arange(n) / sr * 18)
        if k % 2 == 0:
            audio[start:start + n] += 0.9 * np.sin(2 * np.pi * 55 * np.arange(n) / sr) * decay
        else:
            audio[start:start + n] += 0.5 * np.random.default_rng(k).standard_normal(n) * decay
    audio += 0.2 * np.sin(2 * np.pi * 110 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * t / (4 * beat))))
    pcm = (np.clip(audio / np.abs(audio).max() * 0.8, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sr)
        out.writeframes(pcm.tobytes())


class SlowDownload:
    """A download that reads a little, then stalls, keeping the file open on the server."""

    def __init__(self, path: str):
        self.conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
        self.conn.request("GET", path)
        self.response = self.conn.getresponse()
        self.head = self.response.read(64 * 1024)

    def finish(self) -> bytes:
        rest = self.response.read()
        self.conn.close()
        return self.head + rest


def mcp_checks(job_id: str, has_voices: bool) -> None:
    """Drive app/mcp_server.py over stdio as Claude would."""
    mcp = subprocess.Popen([sys.executable, "-m", "app.mcp_server"], cwd=ROOT, text=True, encoding="utf-8",
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, env={**os.environ, "STEM_STUDIO_PORT": str(PORT)})
    counter = [0]

    def rpc(method: str, params: dict) -> dict:
        counter[0] += 1
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter[0], "method": method, "params": params}) + "\n")
        mcp.stdin.flush()
        while True:
            message = json.loads(mcp.stdout.readline())
            if message.get("id") == counter[0]:
                return message

    def tool(name: str, args: dict) -> tuple[bool, str]:
        result = rpc("tools/call", {"name": name, "arguments": args})["result"]
        return not result["isError"], result["content"][0]["text"]

    try:
        init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}})
        check(init["result"]["serverInfo"]["name"] == "prism", "MCP server starts")
        check(tool("choose_song", {"song_id": job_id})[0], "MCP: choose the song")
        ok, text = tool("check_metronome", {})
        check(ok and '"overview"' in text, "MCP: check the metronome")
        check(tool("shift_metronome", {"ms": 5})[0], "MCP: change the metronome")
        if has_voices:
            check(tool("add_cue", {"time": 4, "text": "Chorus"})[0], "MCP: add a cue")
            check(tool("undo", {})[0], "MCP: undo the cue")
        check(tool("undo", {})[0], "MCP: undo the metronome change")
        status, body = call("GET", f"/api/jobs/{job_id}/changes?since=0")
        check(any(c["by"] == "claude" for c in json.loads(body)["changes"]), "changes from MCP are marked as Claude's")
    finally:
        mcp.stdin.close()
        mcp.wait(20)


def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="prism-smoke-")) / "data"
    env = {**os.environ, "STEM_STUDIO_DATA": str(data)}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT),
                               "--log-level", "warning"], cwd=ROOT, env=env)
    try:
        for _ in range(240):
            try:
                status, body = call("GET", "/api/system", timeout=5)
                if status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            check(False, "server starts")
            return 1
        system = json.loads(body)
        print("system:", json.dumps({k: system.get(k) for k in ("device", "device_label", "platform")}, ensure_ascii=False))
        check(call("GET", "/manual/")[0] == 200, "manual is served at /manual/")

        song = data.parent / "smoke.wav"
        test_song(song)
        job = upload(song)
        job_id = job["id"]
        started = time.monotonic()
        while True:
            status, body = call("GET", f"/api/jobs/{job_id}")
            job = json.loads(body)
            if job["status"] in ("done", "error") or time.monotonic() - started > 1800:
                break
            time.sleep(3)
        print(f"separation: {job['status']} in {time.monotonic() - started:.0f} s {job.get('message') or ''}")
        check(job["status"] == "done", "separate and analyse a song")
        if job["status"] != "done":
            return 1

        status, body = call("GET", f"/api/jobs/{job_id}/analysis")
        analysis = json.loads(body)
        grid = analysis["click_grid"]
        check(len(grid["beats"]) > 8, f"beats found ({len(grid['beats'])})")

        # Edit the metronome while click.wav is being downloaded.
        slow = SlowDownload(f"/api/jobs/{job_id}/stems/click.wav")
        result: dict = {}
        shifted = {"beats": [round(b + 0.01, 3) for b in grid["beats"]], "downbeats": [round(b + 0.01, 3) for b in grid["downbeats"]], "meter": grid["meter"]}
        worker = threading.Thread(target=lambda: result.update(status=call("PUT", f"/api/jobs/{job_id}/click", shifted)[0]))
        worker.start()
        time.sleep(1.5)
        slow.finish()
        worker.join(60)
        check(result.get("status") == 200, f"edit the metronome while click.wav is downloading (HTTP {result.get('status')})")

        # Cue voices on this machine.
        status, body = call("GET", "/api/cues/library")
        voices = json.loads(body)["voices"]
        print("voices:", ", ".join(f"{v['id']} ({v['label']})" for v in voices) or "none")
        zip_before = None
        if voices:
            status, wav = call("GET", "/api/cues/preview?" + urllib.parse.urlencode({"voice": voices[0]["id"], "text": "Chorus one"}))
            check(status == 200 and len(wav) > 2000, f"speak a cue with {voices[0]['id']}")
            # A ZIP download in progress while the cues change: the next ZIP must have them.
            slow = SlowDownload(f"/api/jobs/{job_id}/download.zip")
            cue = {"kind": "phrase", "text": "Chorus one", "label": "Chorus one", "voice": voices[0]["id"], "time": round(grid["beats"][8], 3)}
            status, _ = call("PUT", f"/api/jobs/{job_id}/cues", {"cues": [cue]})
            check(status == 200, "save cues while the ZIP is downloading")
            zip_before = slow.finish()
        else:
            print("WARN no system voices: cue speech not tested")
        status, archive = call("GET", f"/api/jobs/{job_id}/download.zip")
        names = zipfile.ZipFile(io.BytesIO(archive)).namelist()
        print("zip:", names)
        check(status == 200 and "click.wav" in names and "drums.wav" in names, "download the ZIP")
        if zip_before is not None:
            check("cues.wav" not in zipfile.ZipFile(io.BytesIO(zip_before)).namelist() and "cues.wav" in names,
                  "the ZIP is rebuilt after an edit made during a download")

        beats = grid["beats"]
        bpm = round(60 * (len(beats) - 1) / (beats[-1] - beats[0]), 1)
        status, body = call("PUT", f"/api/jobs/{job_id}/tempo-lock", {"bpm": bpm, "position": 0})
        check(status == 200, f"lock the tempo at {bpm} BPM" + ("" if status == 200 else f": {body[:200].decode('utf-8', 'replace')}"))
        check(call("PUT", f"/api/jobs/{job_id}/tempo-lock", {"bpm": None, "position": 0})[0] == 200, "unlock the tempo")
        mcp_checks(job_id, bool(voices))
    finally:
        server.terminate()
        try:
            server.wait(20)
        except subprocess.TimeoutExpired:
            server.kill()
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""Prism local server: upload, queue, separate, analyze, serve."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
# STEM_STUDIO_DATA lets a test server run against its own songs, away from real ones.
DATA_DIR = Path(os.environ.get("STEM_STUDIO_DATA", ROOT / "data")) / "jobs"
STATIC_DIR = ROOT / "static"
LIBRARY_DIR = DATA_DIR.parent / "library"      # songs imported for setlists
PLAYLIST_DIR = DATA_DIR.parent / "playlists"

# Keep model weights beside the app, and let Metal fall back to CPU for any
# unsupported op. Both must be set before torch is imported.
os.environ.setdefault("TORCH_HOME", str(ROOT / "models"))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import cues as cue_track, playlists, project, separator  # noqa: E402
from .analyzer import ANALYSIS_VERSION, STEM_THAI, _peaks, analyze  # noqa: E402
from .metronome import render_click  # noqa: E402
from .monitor import ResourceMonitor  # noqa: E402

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
ALLOWED_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma", ".mp4", ".mov", ".webm"}
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

app = FastAPI(title="Prism")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
work_queue: queue.Queue[str] = queue.Queue()
DEVICE = separator.pick_device()


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Block other websites from driving this server through the visitor's browser,
    including DNS rebinding, since multipart POSTs skip the CORS preflight."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    origin = request.headers.get("origin")
    if host not in ALLOWED_HOSTS or (origin and urlparse(origin).hostname not in ALLOWED_HOSTS):
        return JSONResponse({"detail": "อนุญาตเฉพาะการเรียกจากเครื่องนี้"}, status_code=403)
    response = await call_next(request)
    # Make browsers revalidate, or an update can pair an old page with new code.
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def job_dir(job_id: str) -> Path:
    return DATA_DIR / job_id


def save_meta(job: dict) -> None:
    path = job_dir(job["id"]) / "meta.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def update_job(job_id: str, persist: bool = False, **fields) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.update(fields)
        if persist:
            save_meta(job)


def get_job(job_id: str) -> dict:
    if not JOB_ID.match(job_id):
        raise HTTPException(404, "ไม่พบงานนี้")
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "ไม่พบงานนี้")
        return dict(job)


def add_click_track(folder: Path, result: dict, stems: dict[str, Path]) -> None:
    """Render the metronome from the analyzed beats and list it with the stems. It is a
    guide rather than part of the song, so it stays out of the mix measurements."""
    samples = sf.info(next(iter(stems.values()))).frames
    path = folder / "stems" / "click.wav"
    stereo = render_click(result["tempo"]["beats"], result["tempo"]["downbeats"], samples, separator.SAMPLE_RATE, path)
    result["stems"] = [s for s in result["stems"] if s["id"] != "click"]
    result["stems"].append({
        "id": "click", "name": STEM_THAI["click"], "guide": True, "audible": True,
        "peaks": _peaks(stereo, separator.SAMPLE_RATE), "activity": [],
    })


CUE_CACHE = ROOT / "models" / "cues"


def timeline_samples(folder: Path, edits: dict) -> int:
    """Song length plus lead-in, in samples: the length every served track has."""
    reference = next(p for p in sorted((folder / "stems").glob("*.wav")) if p.stem not in project.GUIDES)
    return sf.info(reference).frames + project.lead_samples(edits, separator.SAMPLE_RATE)


def render_click_track(folder: Path, analysis: dict, edits: dict) -> None:
    grid = project.timeline_grid(analysis, edits)
    render_click(grid["beats"], grid["downbeats"], timeline_samples(folder, edits), separator.SAMPLE_RATE, folder / "stems" / "click.wav")


def render_cues(folder: Path, analysis: dict, edits: dict) -> None:
    """Render the spoken cue track from the saved cue list on the current beat grid."""
    grid = project.timeline_grid(analysis, edits)
    lead = project.lead_seconds(edits)
    on_timeline = [{**c, "time": c["time"] + lead} for c in edits.get("cues", [])]
    stereo, counts = cue_track.render(
        on_timeline, grid["beats"], grid["meter"], timeline_samples(folder, edits), separator.SAMPLE_RATE, CUE_CACHE,
        folder / "stems" / "cues.wav",
    )
    rendered = {"peaks": _peaks(stereo, separator.SAMPLE_RATE), "counts": counts}
    path = folder / project.CUE_RENDER_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rendered), encoding="utf-8")
    temporary.replace(path)


def write_analysis(folder: Path, result: dict) -> None:
    path = folder / "analysis.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def process(job_id: str) -> None:
    job = get_job(job_id)
    folder = job_dir(job_id)
    started = time.time()
    update_job(job_id, persist=True, status="running", stage="decode", progress=0.01, message="ถอดรหัสไฟล์เสียง")

    accelerator = separator.device_label(DEVICE)
    monitor = ResourceMonitor(DEVICE, accelerator, ROOT / "models")
    monitor.start()
    try:
        monitor.stage("decode", "อ่านไฟล์เสียง", "CPU (ffmpeg)", measures_cpu=False)
        wav = separator.decode_audio(folder / job["source"])
        update_job(job_id, stage="model", progress=0.04, message="โหลดโมเดล AI (ครั้งแรกจะดาวน์โหลดก่อน)", duration=round(wav.shape[1] / separator.SAMPLE_RATE, 1))
        monitor.stage("model", "โหลดโมเดล", "CPU")
        separator.load_model(job["model"])

        update_job(job_id, stage="separate", progress=0.08, message="AI กำลังแยก stem")
        monitor.stage("demucs", "แยก stem (Demucs)", accelerator)
        separate_started = time.time()
        stems = separator.separate(
            wav, job["model"], folder / "stems", DEVICE,
            lambda p: update_job(job_id, progress=round(0.08 + p * 0.74, 4)),
            lambda m: update_job(job_id, message=m),
            lambda s: monitor.stage(s, "แยกเสียงร้องหลัก/คอรัส (Roformer)", accelerator),
        )
        separate_seconds = time.time() - separate_started
        audio_seconds = wav.shape[1] / separator.SAMPLE_RATE
        del wav

        update_job(job_id, stage="analyze", progress=0.83, message="วิเคราะห์โครงสร้างเพลง")
        monitor.stage("analyze", "วิเคราะห์เพลง", "CPU")
        result = analyze(stems, lambda p, m: update_job(job_id, progress=round(0.83 + p * 0.16, 4), message=m))

        update_job(job_id, message="สร้างแทร็กเมโทรนอม")
        monitor.stage("click", "สร้างเมโทรนอมและแทร็ก cue", "CPU")
        add_click_track(folder, result, stems)
        write_analysis(folder, result)
        render_cues(folder, result, project.load_edits(folder))
    finally:
        monitor.stop()

    update_job(
        job_id, persist=True, status="done", stage="done", progress=1.0, message="เสร็จแล้ว",
        stems=[*stems, "click", "cues"], duration=result["duration"], bpm=result["tempo"]["bpm"], key=result["key"]["name"],
        elapsed=round(time.time() - started, 1), separate_seconds=round(separate_seconds, 1),
        resources=monitor.report(audio_seconds, folder, folder / job["source"]),
    )


backfill_lock = threading.Lock()


def analysis_is_current(job_id: str) -> bool:
    path = job_dir(job_id) / "analysis.json"
    if not path.exists():
        return True
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("version", 1) >= ANALYSIS_VERSION
    except (OSError, json.JSONDecodeError):
        return True


def upgrade_analysis(job_id: str) -> None:
    """Re-run the analysis of a song made by an older version, from its own stems. Hand
    edits (metronome, cues, cuts, lead-in) are kept; the click and cues are re-rendered
    on top of them, so an edited metronome stays exactly as edited."""
    with backfill_lock:
        if analysis_is_current(job_id):
            return
        job = get_job(job_id)
        folder = job_dir(job_id)
        stems = {name: folder / "stems" / f"{name}.wav" for name in project.active_stems(job)}
        result = analyze(stems)
        add_click_track(folder, result, stems)
        write_analysis(folder, result)
        with edit_lock:
            edits = project.load_edits(folder)
            render_click_track(folder, result, edits)
            render_cues(folder, result, edits)
            (folder / "stems.zip").unlink(missing_ok=True)
        guides = [g for g in project.GUIDES if g not in job["stems"]]
        update_job(job_id, persist=True, stems=[*job["stems"], *guides], bpm=result["tempo"]["bpm"], key=result["key"]["name"])


def ensure_click_track(job_id: str) -> None:
    """Songs separated before the metronome or cue track existed get them when first
    opened. Only generated files are written; the separated stems stay as they are."""
    job = get_job(job_id)
    if job["status"] != "done":
        return
    if not analysis_is_current(job_id):
        upgrade_analysis(job_id)
        return
    if "click" in job.get("stems", []) and "cues" in job.get("stems", []):
        return
    with backfill_lock:
        job = get_job(job_id)
        if "click" in job.get("stems", []):
            if "cues" not in job["stems"]:
                folder = job_dir(job_id)
                render_cues(folder, read_analysis_file(job_id), project.load_edits(folder))
                (folder / "stems.zip").unlink(missing_ok=True)
                update_job(job_id, persist=True, stems=[*job["stems"], "cues"])
            return
        folder = job_dir(job_id)
        stems = {name: folder / "stems" / f"{name}.wav" for name in job["stems"]}
        result = analyze(stems)
        add_click_track(folder, result, stems)
        write_analysis(folder, result)
        (folder / "stems.zip").unlink(missing_ok=True)  # rebuilt with the click on next download
        render_cues(folder, result, project.load_edits(folder))
        update_job(job_id, persist=True, stems=[*job["stems"], "click", "cues"], bpm=result["tempo"]["bpm"], key=result["key"]["name"])


def worker() -> None:
    while True:
        job_id = work_queue.get()
        try:
            process(job_id)
        except separator.AudioDecodeError as error:
            update_job(job_id, persist=True, status="error", message="อ่านไฟล์เสียงไม่ได้", error=str(error))
        except Exception as error:  # surface the failure in the UI, keep the queue alive
            traceback.print_exc()
            update_job(job_id, persist=True, status="error", message="เกิดข้อผิดพลาดระหว่างประมวลผล", error=f"{type(error).__name__}: {error}")
        finally:
            work_queue.task_done()


def restore_jobs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for meta in DATA_DIR.glob("*/meta.json"):
        try:
            job = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if job.get("status") in ("queued", "running"):  # server stopped mid-job
            job.update(status="error", message="งานถูกขัดจังหวะ", error="เซิร์ฟเวอร์หยุดระหว่างประมวลผล กรุณาอัปโหลดใหม่")
            jobs[job["id"]] = job
            save_meta(job)
        else:
            jobs[job["id"]] = job


@app.on_event("startup")
def on_startup() -> None:
    restore_jobs()
    threading.Thread(target=worker, daemon=True, name="stem-worker").start()


@app.get("/api/system")
def system_info() -> dict:
    return {
        "device": DEVICE,
        "device_label": separator.device_label(DEVICE),
        "models": [{"id": key, **value} for key, value in separator.MODELS.items()],
    }


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    with jobs_lock:
        return sorted((dict(j) for j in jobs.values()), key=lambda j: j["created_at"], reverse=True)


@app.post("/api/jobs", status_code=201)
def create_job(file: UploadFile = File(...), model: str = Form("htdemucs")) -> dict:
    if model not in separator.MODELS:
        raise HTTPException(400, "ไม่รู้จักโมเดลนี้")
    filename = Path(file.filename or "song").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"ไม่รองรับไฟล์ {suffix or 'ชนิดนี้'}")

    job_id = uuid.uuid4().hex[:12]
    folder = job_dir(job_id)
    folder.mkdir(parents=True)
    source = f"source{suffix}"
    written = 0
    try:
        with open(folder / source, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "ไฟล์ใหญ่เกิน 1 GB")
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    job = {
        "id": job_id,
        "filename": filename,
        "title": Path(filename).stem,
        "source": source,
        "model": model,
        "status": "queued",
        "stage": "queued",
        "progress": 0.0,
        "message": "รอคิว",
        "device": separator.device_label(DEVICE),
        "created_at": time.time(),
    }
    with jobs_lock:
        jobs[job_id] = job
        save_meta(job)
    work_queue.put(job_id)
    return job


@app.get("/api/jobs/{job_id}")
def read_job(job_id: str) -> dict:
    return get_job(job_id)


def read_analysis_file(job_id: str) -> dict:
    path = job_dir(job_id) / "analysis.json"
    if not path.exists():
        raise HTTPException(404, "ยังวิเคราะห์ไม่เสร็จ")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/analysis")
def read_analysis(job_id: str) -> JSONResponse:
    get_job(job_id)
    try:
        ensure_click_track(job_id)
    except Exception:  # the song still opens without a click if this upgrade fails
        traceback.print_exc()
    analysis = read_analysis_file(job_id)
    with edit_lock:
        edits = project.load_edits(job_dir(job_id))
    return JSONResponse(project.merged_view(job_dir(job_id), analysis, edits))


edit_lock = threading.Lock()


class LeadIn(BaseModel):
    bars: int
    count_voice: str | None = None


class CueList(BaseModel):
    cues: list[dict]


class ClickGrid(BaseModel):
    beats: list[float]
    downbeats: list[float]
    meter: int


class CutRequest(BaseModel):
    source: str
    start: float
    end: float
    name: str | None = None
    target: str | None = None


class RenameRequest(BaseModel):
    name: str


def editable_job(job_id: str) -> dict:
    job = get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "งานยังไม่เสร็จ")
    ensure_click_track(job_id)
    return get_job(job_id)


def song_tracks(job: dict, edits: dict) -> list[str]:
    return [*project.active_stems(job), *[t["id"] for t in edits["tracks"]]]


def commit_cuts(job_id: str, job: dict, edits: dict, changed: list[str]) -> dict:
    folder = job_dir(job_id)
    project.render(folder, edits, project.active_stems(job), separator.SAMPLE_RATE)
    project.build_padded(folder, song_tracks(job, edits), edits, separator.SAMPLE_RATE)
    project.save_edits(folder, edits)
    (folder / "stems.zip").unlink(missing_ok=True)
    update_job(job_id, persist=True, stems=project.stems_after_edit(job, edits))
    return {"changed": changed}


@app.put("/api/jobs/{job_id}/click")
def update_click(job_id: str, grid: ClickGrid) -> dict:
    """Save a hand-corrected metronome. The WAV is rendered here, so the file that gets
    played, zipped and downloaded always comes from this one grid."""
    editable_job(job_id)
    folder = job_dir(job_id)
    analysis = read_analysis_file(job_id)
    with edit_lock:
        edits = project.load_edits(folder)
        lead = project.lead_seconds(edits)
        try:
            clean = project.clean_click_grid(
                [t - lead for t in grid.beats], [t - lead for t in grid.downbeats], grid.meter, analysis["duration"], start=-lead,
            )
        except project.EditError as error:
            raise HTTPException(422, str(error))
        edits["click"] = clean
        render_click_track(folder, analysis, edits)
        project.save_edits(folder, edits)
        # Counts sit on the beats, so they follow the metronome.
        if any(c["kind"] == "count" for c in edits.get("cues", [])):
            render_cues(folder, analysis, edits)
        (folder / "stems.zip").unlink(missing_ok=True)
        return {**project.timeline_grid(analysis, edits), **project.tempo_view(analysis, edits)}


@app.delete("/api/jobs/{job_id}/click")
def reset_click(job_id: str) -> dict:
    editable_job(job_id)
    folder = job_dir(job_id)
    analysis = read_analysis_file(job_id)
    with edit_lock:
        edits = project.load_edits(folder)
        edits["click"] = None
        render_click_track(folder, analysis, edits)
        project.save_edits(folder, edits)
        if any(c["kind"] == "count" for c in edits.get("cues", [])):
            render_cues(folder, analysis, edits)
        (folder / "stems.zip").unlink(missing_ok=True)
        return {**project.timeline_grid(analysis, edits), **project.tempo_view(analysis, edits)}


@app.put("/api/jobs/{job_id}/lead-in")
def set_lead_in(job_id: str, request: LeadIn) -> dict:
    """Add (or remove) bars of silence before the song for counting in. Song tracks get
    the silence in front; the metronome and cues are rendered across it."""
    job = editable_job(job_id)
    folder = job_dir(job_id)
    analysis = read_analysis_file(job_id)
    if not 0 <= request.bars <= project.MAX_LEAD_BARS:
        raise HTTPException(422, f"ช่วงนับก่อนเพลงต้องอยู่ระหว่าง 0 ถึง {project.MAX_LEAD_BARS} ห้อง")
    with edit_lock:
        edits = project.load_edits(folder)
        grid = project.click_grid(analysis, edits)
        lead = project.compute_lead(grid, request.bars, separator.SAMPLE_RATE)
        edits["lead_in"] = {"bars": request.bars, "seconds": lead} if lead > 0 else None
        # A shorter lead-in leaves no room for beats and cues placed before it.
        if edits.get("click"):
            kept = [b for b in edits["click"]["beats"] if b >= -lead - 0.002]
            edits["click"] = {**edits["click"], "beats": kept, "downbeats": [d for d in edits["click"]["downbeats"] if d in set(kept)]}
        cues = [{**c, "time": max(-lead, c["time"])} for c in edits.get("cues", [])]
        if lead == 0:
            # The count-in this feature added has nothing to count over without a lead-in.
            cues = [c for c in cues if not (c["kind"] == "count" and c.get("auto"))]
        added = False
        if lead > 0 and request.count_voice:
            target = round(project.first_downbeat(grid), 3)
            if not any(c["kind"] == "count" and abs(c["time"] - target) < 0.05 for c in cues):
                cues.append({"kind": "count", "time": target, "style": "long" if request.bars >= 2 else "full", "voice": request.count_voice, "auto": True})
                added = True
        try:
            edits["cues"] = cue_track.clean_cues(cues, analysis["duration"], grid["meter"], start=-lead)
        except cue_track.CueError as error:
            raise HTTPException(422, str(error))
        project.build_padded(folder, song_tracks(job, edits), edits, separator.SAMPLE_RATE)
        render_click_track(folder, analysis, edits)
        render_cues(folder, analysis, edits)
        project.save_edits(folder, edits)
        (folder / "stems.zip").unlink(missing_ok=True)
    return {"bars": request.bars, "seconds": round(lead, 6), "added_count": added}


@app.get("/api/cues/library")
def cue_library() -> dict:
    return cue_track.library()


@app.get("/api/cues/preview")
def cue_preview(voice: str, text: str, kind: str = "phrase") -> FileResponse:
    """One spoken phrase, for auditioning before placing it."""
    rate = cue_track.COUNT_RATE if kind == "count" else cue_track.PHRASE_RATE
    try:
        path = cue_track.speak(CUE_CACHE, voice, text, rate, separator.SAMPLE_RATE)
    except cue_track.CueError as error:
        raise HTTPException(422, str(error))
    return FileResponse(path, media_type="audio/wav")


@app.put("/api/jobs/{job_id}/cues")
def save_cues(job_id: str, request: CueList) -> dict:
    editable_job(job_id)
    folder = job_dir(job_id)
    analysis = read_analysis_file(job_id)
    with edit_lock:
        edits = project.load_edits(folder)
        grid = project.click_grid(analysis, edits)
        lead = project.lead_seconds(edits)
        try:
            song_time = [{**c, "time": float(c.get("time", -1e9)) - lead} for c in request.cues]
            cleaned = cue_track.clean_cues(song_time, analysis["duration"], grid["meter"], start=-lead)
            edits["cues"] = cleaned
            render_cues(folder, analysis, edits)
        except cue_track.CueError as error:
            raise HTTPException(422, str(error))
        except (TypeError, KeyError, ValueError):
            raise HTTPException(422, "ข้อมูล cue ไม่ถูกต้อง")
        project.save_edits(folder, edits)
        (folder / "stems.zip").unlink(missing_ok=True)
    rendered = json.loads((folder / project.CUE_RENDER_FILE).read_text(encoding="utf-8"))
    on_timeline = [{**c, "time": round(c["time"] + lead, 3)} for c in cleaned]
    return {"cues": on_timeline, "count_beats": rendered["counts"], "peaks": rendered["peaks"]}


@app.post("/api/jobs/{job_id}/cuts", status_code=201)
def create_cut(job_id: str, cut: CutRequest) -> dict:
    job = editable_job(job_id)
    analysis = read_analysis_file(job_id)
    with edit_lock:
        edits = project.load_edits(job_dir(job_id))
        lead = project.lead_seconds(edits)
        if cut.end - lead <= 0:
            raise HTTPException(422, "ช่วงที่เลือกอยู่ในช่วงนับก่อนเพลง ซึ่งไม่มีเสียง")
        try:
            result = project.add_cut(
                job_dir(job_id), edits, project.active_stems(job), analysis["duration"], separator.SAMPLE_RATE,
                cut.source, cut.start - lead, cut.end - lead, cut.name, cut.target,
            )
        except project.EditError as error:
            raise HTTPException(422, str(error))
        return {**commit_cuts(job_id, job, edits, result["changed"]), "target": result["target"]}


@app.delete("/api/jobs/{job_id}/cuts/last")
def undo_cut(job_id: str) -> dict:
    job = editable_job(job_id)
    with edit_lock:
        edits = project.load_edits(job_dir(job_id))
        try:
            changed = project.undo_last_cut(edits)
        except project.EditError as error:
            raise HTTPException(409, str(error))
        return commit_cuts(job_id, job, edits, changed)


@app.patch("/api/jobs/{job_id}/tracks/{track_id}")
def rename_track(job_id: str, track_id: str, request: RenameRequest) -> dict:
    editable_job(job_id)
    folder = job_dir(job_id)
    with edit_lock:
        edits = project.load_edits(folder)
        try:
            project.rename_track(edits, track_id, request.name)
        except project.EditError as error:
            raise HTTPException(422, str(error))
        project.save_edits(folder, edits)
        (folder / "stems.zip").unlink(missing_ok=True)
    return {"changed": []}


@app.delete("/api/jobs/{job_id}/tracks/{track_id}")
def delete_track(job_id: str, track_id: str) -> dict:
    """Remove a cut track and put its audio back in the track it came from."""
    job = editable_job(job_id)
    with edit_lock:
        edits = project.load_edits(job_dir(job_id))
        try:
            changed = project.remove_track(edits, track_id)
        except project.EditError as error:
            raise HTTPException(404, str(error))
        return commit_cuts(job_id, job, edits, changed)


@app.get("/api/jobs/{job_id}/stems/{stem}.wav")
def read_stem(job_id: str, stem: str) -> FileResponse:
    job = get_job(job_id)
    if stem not in job.get("stems", []):
        raise HTTPException(404, "ไม่พบ stem นี้")
    folder = job_dir(job_id)
    edits = project.load_edits(folder)
    label = project.track_label(edits, stem)
    return FileResponse(project.serve_path(folder, stem, edits), media_type="audio/wav", filename=f"{label}.wav")


@app.get("/api/jobs/{job_id}/download.zip")
def download_zip(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "งานยังไม่เสร็จ")
    folder = job_dir(job_id)
    archive = folder / "stems.zip"
    with edit_lock:
        if not archive.exists():
            edits = project.load_edits(folder)
            temporary = archive.with_suffix(".tmp")
            used: set[str] = set()
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as bundle:
                for stem in job["stems"]:
                    if stem == project.CUES and not edits.get("cues"):
                        continue  # an empty guide track would only be silence
                    label = project.track_label(edits, stem)
                    if label in used:  # two tracks may share a name, files in a zip cannot
                        label = f"{label} ({stem})"
                    used.add(label)
                    # Inside the zip, files are named by instrument only; the zip carries the song name.
                    bundle.write(project.serve_path(folder, stem, edits), f"{label}.wav")
            temporary.replace(archive)
    return FileResponse(archive, media_type="application/zip", filename=f"{job['title']} - stems.zip")


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> None:
    job = get_job(job_id)
    if job["status"] in ("queued", "running"):
        raise HTTPException(409, "ลบไม่ได้ระหว่างประมวลผล")
    with jobs_lock:
        jobs.pop(job_id, None)
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


# ------------------------------------------------------------------ setlists

def _import_dir(import_id: str) -> Path:
    if not playlists.ITEM_ID.match(import_id):
        raise HTTPException(404, "ไม่พบไฟล์")
    return LIBRARY_DIR / import_id


def _receive_upload(file: UploadFile, target: Path) -> None:
    written = 0
    with open(target, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "ไฟล์ใหญ่เกิน 1 GB")
            out.write(chunk)


@app.get("/api/library")
def list_library() -> list[dict]:
    return playlists.list_imports(LIBRARY_DIR)


@app.post("/api/library", status_code=201)
def import_song(file: UploadFile = File(...)) -> dict:
    """Add an audio file from this computer to the setlist library, as it is (no separation)."""
    filename = Path(file.filename or "song").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"ไม่รองรับไฟล์ {suffix or 'ชนิดนี้'}")
    folder = LIBRARY_DIR / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    source = folder / f".source{suffix}"
    try:
        _receive_upload(file, source)
        return playlists.import_file(folder, source, Path(filename).stem, filename, imageio_ffmpeg.get_ffmpeg_exe(), separator.SAMPLE_RATE)
    except playlists.PlaylistError as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(error))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        source.unlink(missing_ok=True)


@app.get("/api/library/{import_id}")
def read_library_song(import_id: str) -> dict:
    meta = playlists.read_import(_import_dir(import_id))
    if meta is None:
        raise HTTPException(404, "ไม่พบไฟล์")
    return meta


@app.get("/api/library/{import_id}/audio.wav")
def read_library_audio(import_id: str) -> FileResponse:
    path = _import_dir(import_id) / "audio.wav"
    if not path.exists():
        raise HTTPException(404, "ไม่พบไฟล์")
    return FileResponse(path, media_type="audio/wav")


@app.delete("/api/library/{import_id}", status_code=204)
def delete_library_song(import_id: str) -> None:
    folder = _import_dir(import_id)
    if not folder.exists():
        raise HTTPException(404, "ไม่พบไฟล์")
    shutil.rmtree(folder, ignore_errors=True)


class MixRequest(BaseModel):
    muted: list[str] = []
    pan: dict[str, str] = {}


@app.post("/api/jobs/{job_id}/playback")
def render_playback(job_id: str, request: MixRequest) -> dict:
    """One stereo file of a finished song with the chosen tracks on and routed, for the
    setlist player. Includes the lead-in, so a count-in plays too."""
    job = editable_job(job_id)
    folder = job_dir(job_id)
    with edit_lock:
        edits = project.load_edits(folder)
        stems = project.stems_after_edit(job, edits)
        sources = {stem: project.serve_path(folder, stem, edits) for stem in stems}
    sources = {stem: path for stem, path in sources.items() if path.exists()}
    if not sources:
        raise HTTPException(409, "ไม่พบไฟล์เสียงของเพลงนี้")
    mix = {"muted": sorted(set(request.muted) & set(sources)), "pan": {k: v for k, v in request.pan.items() if k in sources and v in ("L", "R")}}
    meta = playlists.render_mix(sources, mix, folder / "playback", _peaks)
    return {**meta, "url": f"/api/jobs/{job_id}/playback/{meta['key']}.wav"}


@app.get("/api/jobs/{job_id}/playback/{key}.wav")
def read_playback(job_id: str, key: str) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        raise HTTPException(404, "ไม่พบไฟล์")
    path = job_dir(job_id) / "playback" / f"{key}.wav"
    if not path.exists():
        raise HTTPException(404, "ไฟล์หมดอายุแล้ว กรุณาโหลดใหม่")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/playlists")
def list_playlists() -> list[dict]:
    return playlists.list_playlists(PLAYLIST_DIR)


class PlaylistName(BaseModel):
    name: str = ""


@app.post("/api/playlists", status_code=201)
def create_playlist(request: PlaylistName) -> dict:
    try:
        return playlists.create_playlist(PLAYLIST_DIR, request.name)
    except playlists.PlaylistError as error:
        raise HTTPException(409, str(error))


@app.get("/api/playlists/{playlist_id}")
def read_playlist(playlist_id: str) -> dict:
    try:
        return playlists.read_playlist(PLAYLIST_DIR, playlist_id)
    except playlists.PlaylistError as error:
        raise HTTPException(404, str(error))


@app.put("/api/playlists/{playlist_id}")
def save_playlist(playlist_id: str, body: dict = Body(...)) -> dict:
    try:
        return playlists.save_playlist(PLAYLIST_DIR, playlist_id, body)
    except playlists.PlaylistError as error:
        raise HTTPException(404 if "ไม่พบ" in str(error) else 422, str(error))


@app.delete("/api/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: str) -> None:
    try:
        playlists.delete_playlist(PLAYLIST_DIR, playlist_id)
    except playlists.PlaylistError as error:
        raise HTTPException(404, str(error))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

"""Stem separation with Demucs (Hybrid Transformer), driven through its Python API."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import numpy as np
import soundfile as sf
import torch
import demucs.apply
from demucs.apply import BagOfModels, apply_model
from demucs.pretrained import get_model

SAMPLE_RATE = 44100
ROOT = Path(__file__).resolve().parent.parent
VOCAL_SPLIT_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
VOCAL_SPLIT_DIR = ROOT / "models" / "separator"

MODELS = {
    "htdemucs": {
        "title": "มาตรฐาน",
        "stems": ["vocals", "drums", "bass", "other"],
        "description": "4 แทร็ก สมดุลทั้งความเร็วและคุณภาพ เหมาะกับเพลงส่วนใหญ่",
        "speed": 1.0,
    },
    "htdemucs_ft": {
        "title": "คุณภาพสูงสุด",
        "stems": ["vocals", "drums", "bass", "other"],
        "description": "4 แทร็ก เสียงสะอาดกว่า แต่ใช้เวลาประมาณ 4 เท่า",
        "speed": 4.0,
    },
    "htdemucs_6s": {
        "title": "6 แทร็ก",
        "stems": ["vocals", "drums", "bass", "guitar", "piano", "other"],
        "description": "แยกกีตาร์และเปียโนเพิ่ม แต่แทร็กเปียโนอาจมีเสียงอื่นปน",
        "speed": 1.0,
    },
    "htdemucs_lead": {
        "title": "เสียงร้องหลัก + คอรัส",
        "stems": ["lead_vocals", "backing_vocals", "drums", "bass", "other"],
        "description": "5 แทร็ก แยกเสียงร้องหลักกับคอรัส ใช้เวลาราว 4 เท่า ครั้งแรกโหลดโมเดล 913 MB",
        "speed": 4.0,
        "demucs": "htdemucs",
        "split_vocals": True,
    },
}


class AudioDecodeError(Exception):
    pass


def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def device_label(device: str) -> str:
    return {"cuda": "NVIDIA GPU", "mps": "Apple GPU (Metal)", "cpu": "CPU"}.get(device, device)


def decode_audio(path: Path) -> np.ndarray:
    """Decode any format ffmpeg understands into float32 stereo, shape (2, samples)."""
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error", "-i", str(path),
        "-vn", "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(SAMPLE_RATE), "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AudioDecodeError(detail[-1] if detail else "ไม่พบสัญญาณเสียงในไฟล์")
    samples = np.frombuffer(result.stdout, dtype=np.float32).copy()
    samples = samples[: len(samples) - len(samples) % 2]
    if len(samples) < SAMPLE_RATE * 2:  # stereo pairs: under one second of audio
        raise AudioDecodeError("ไฟล์เสียงสั้นเกินไป (ต้องยาวอย่างน้อย 1 วินาที)")
    if np.abs(samples).max() < 1e-4:  # below -80 dBFS: nothing to separate or analyze
        raise AudioDecodeError("ไฟล์นี้ไม่มีเสียง")
    return samples.reshape(-1, 2).T


class _ProgressBar:
    """Stands in for the `tqdm` module inside demucs.apply, the only place the
    library exposes chunk-level progress in this release."""

    def __init__(self, passes: int, report: Callable[[float], None]):
        self.passes = max(1, passes)
        self.report = report
        self.current = 0

    def tqdm(self, iterable, **_kwargs):
        items = list(iterable)
        index = self.current
        self.current += 1
        for i, item in enumerate(items):
            yield item
            self.report(min(1.0, (index + (i + 1) / max(1, len(items))) / self.passes))


_loaded: dict[str, torch.nn.Module] = {}


def load_model(name: str) -> torch.nn.Module:
    demucs_name = MODELS.get(name, {}).get("demucs", name)
    if demucs_name not in _loaded:
        model = get_model(demucs_name)
        model.eval()
        _loaded.clear()  # keep one model in memory; a bag can hold several GB
        _loaded[demucs_name] = model
    return _loaded[demucs_name]


def _ffmpeg_on_path() -> None:
    """audio-separator shells out to a command named `ffmpeg`. The bundled binary has
    a versioned name, so expose it under that name inside the venv, not system-wide."""
    folder = Path(sys.prefix) / "ffmpeg-bin"
    folder.mkdir(exist_ok=True)
    target = imageio_ffmpeg.get_ffmpeg_exe()
    if sys.platform == "win32":
        # Symlinks need admin rights on Windows, and the command must end in .exe: copy it.
        copy = folder / "ffmpeg.exe"
        if not copy.exists() or copy.stat().st_size != os.path.getsize(target):
            shutil.copy2(target, copy)
    else:
        link = folder / "ffmpeg"
        if not link.exists() or os.path.realpath(link) != os.path.realpath(target):
            link.unlink(missing_ok=True)
            link.symlink_to(target)
    if str(folder) not in os.environ["PATH"].split(os.pathsep):
        os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ['PATH']}"


_vocal_splitter = None


def split_vocals(vocals_path: Path, on_progress: Callable[[float], None]) -> dict[str, Path]:
    """Split a vocal stem into lead and backing. Backing is the remainder after removing
    the lead, so the two always sum back to the original vocal stem."""
    global _vocal_splitter
    _ffmpeg_on_path()
    from audio_separator.separator import Separator  # slow import, only this mode needs it
    from audio_separator.separator.architectures import mdxc_separator

    work = vocals_path.parent / ".split"
    if _vocal_splitter is None:
        _vocal_splitter = Separator(
            log_level=30, model_file_dir=str(VOCAL_SPLIT_DIR), output_dir=str(work), output_format="WAV",
            normalization_threshold=1.0, sample_rate=SAMPLE_RATE,
        )
        _vocal_splitter.load_model(VOCAL_SPLIT_MODEL)
    _vocal_splitter.output_dir = str(work)
    _vocal_splitter.model_instance.output_dir = str(work)
    work.mkdir(parents=True, exist_ok=True)

    def tracked(iterable, **_kwargs):
        items = list(iterable)
        for i, item in enumerate(items):
            yield item
            on_progress((i + 1) / max(1, len(items)))

    original_tqdm = mdxc_separator.tqdm
    mdxc_separator.tqdm = tracked
    try:
        files = _vocal_splitter.separate(str(vocals_path))
    finally:
        mdxc_separator.tqdm = original_tqdm
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    lead_file = next(f for f in files if "(Vocals)" in Path(f).name)
    lead_path = Path(lead_file) if Path(lead_file).is_absolute() else work / lead_file
    lead, _ = sf.read(lead_path, dtype="float32", always_2d=True)
    vocals, _ = sf.read(vocals_path, dtype="float32", always_2d=True)
    if len(lead) < len(vocals):  # keep every stem the same length so they stay in sync
        lead = np.pad(lead, ((0, len(vocals) - len(lead)), (0, 0)))
    lead = lead[: len(vocals)]
    backing = vocals - lead

    paths = {"lead_vocals": vocals_path.parent / "lead_vocals.wav", "backing_vocals": vocals_path.parent / "backing_vocals.wav"}
    sf.write(paths["lead_vocals"], np.clip(lead, -1.0, 1.0), SAMPLE_RATE, subtype="PCM_16")
    sf.write(paths["backing_vocals"], np.clip(backing, -1.0, 1.0), SAMPLE_RATE, subtype="PCM_16")
    vocals_path.unlink()
    shutil.rmtree(work, ignore_errors=True)
    return paths


def separate(
    wav: np.ndarray,
    model_name: str,
    output_dir: Path,
    device: str,
    on_progress: Callable[[float], None],
    on_message: Callable[[str], None] = lambda _m: None,
    on_stage: Callable[[str], None] = lambda _s: None,
) -> dict[str, Path]:
    """Separate `wav` and write one 16-bit WAV per stem. Returns {stem: path}."""
    model = load_model(model_name)
    settings = MODELS.get(model_name, {})
    split = settings.get("split_vocals", False)
    # The vocal split takes roughly twice as long as Demucs, so give it most of the bar.
    demucs_share = 1 / settings.get("speed", 1.0) if split else 1.0
    report_demucs = lambda p: on_progress(p * demucs_share)  # noqa: E731
    mix = torch.from_numpy(wav)

    # Demucs was trained on normalized audio; undo the scaling on the way out.
    reference = mix.mean(0)
    mean, std = reference.mean(), reference.std().clamp_min(1e-8)
    normalized = (mix - mean) / std

    passes = len(model.models) if isinstance(model, BagOfModels) else 1
    original_tqdm = demucs.apply.tqdm
    demucs.apply.tqdm = _ProgressBar(passes, report_demucs)
    try:
        with torch.no_grad():
            sources = apply_model(model, normalized[None], device=device, shifts=1, split=True, overlap=0.25, progress=True)[0]
    finally:
        demucs.apply.tqdm = original_tqdm
        if device == "mps":
            torch.mps.empty_cache()

    sources = (sources * std + mean).cpu().numpy()

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, source in zip(model.sources, sources):
        path = output_dir / f"{name}.wav"
        sf.write(path, np.clip(source, -1.0, 1.0).T, SAMPLE_RATE, subtype="PCM_16")
        paths[name] = path

    if split:
        _loaded.clear()  # free Demucs first: both models at once can exhaust smaller machines
        if device == "mps":
            torch.mps.empty_cache()
        on_stage("split")
        first_run = not (VOCAL_SPLIT_DIR / VOCAL_SPLIT_MODEL).exists()
        on_message("ดาวน์โหลดโมเดลเสียงร้อง 913 MB (ครั้งแรกครั้งเดียว)" if first_run else "AI กำลังแยกเสียงร้องหลักและคอรัส")

        def report_split(p: float) -> None:
            on_message("AI กำลังแยกเสียงร้องหลักและคอรัส")
            on_progress(demucs_share + p * (1 - demucs_share))

        split_paths = split_vocals(paths.pop("vocals"), report_split)
        paths = {**split_paths, **paths}
    return paths

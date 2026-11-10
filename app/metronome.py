"""Renders a click track from a song's beat grid, placed to the exact sample."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

ACCENT_HZ, BEAT_HZ = 2000.0, 1000.0
ACCENT_LEVEL, BEAT_LEVEL = 0.8, 0.55
TICK_SECONDS = 0.035
DECAY_PER_SECOND = 140.0


def _tick(frequency: float, level: float, sample_rate: int) -> np.ndarray:
    t = np.arange(int(TICK_SECONDS * sample_rate)) / sample_rate
    return level * np.sin(2 * np.pi * frequency * t) * np.exp(-t * DECAY_PER_SECOND)


def write_wav_atomic(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """Write beside the target, then swap it in with one rename. A browser still reading
    the previous version keeps reading that whole file instead of a half-written one."""
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        sf.write(temporary, audio, sample_rate, subtype="PCM_16", format="WAV")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render_click(beats: list[float], downbeats: list[float], samples: int, sample_rate: int, path: Path) -> np.ndarray:
    """Write a stereo WAV of `samples` length, the same as the stems, so all stay in sync.
    The first beat of each bar gets a higher, louder tick. Returns shape (2, samples)."""
    accent, beat = _tick(ACCENT_HZ, ACCENT_LEVEL, sample_rate), _tick(BEAT_HZ, BEAT_LEVEL, sample_rate)
    bar_starts = {round(t, 3) for t in downbeats}
    track = np.zeros(samples, dtype=np.float32)
    for time in beats:
        start = int(round(time * sample_rate))
        if not 0 <= start < samples:
            continue
        tick = accent if round(time, 3) in bar_starts else beat
        end = min(samples, start + len(tick))
        track[start:end] += tick[: end - start]
    stereo = np.stack([track, track])
    write_wav_atomic(path, np.clip(stereo, -1.0, 1.0).T, sample_rate)
    return stereo

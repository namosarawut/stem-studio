"""Tempo lock: stretch a song so every beat of its metronome lasts the same time.

A recording made without a click drifts (this one from 164 to 174 BPM). For playing live
the band wants one steady tempo, so each beat of the audio is stretched or squeezed to
60 / bpm seconds, the way a DAW's warp markers do it.

The stretch is WSOLA (waveform-similarity overlap-add): the output is built from short
overlapping frames of the input, each taken near where the time map says it belongs, at
the offset that best continues the previous frame. The pitch does not change. The
offsets are chosen once, on the sum of all tracks, and every track is cut at the same
offsets, so the warped stems still add up to the warped song with nothing smeared
between them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

FRAME = 1024          # samples per frame (23 ms at 44.1 kHz)
HOP = FRAME // 2      # output hop; a periodic Hann window at 50 % overlap sums to one
TOLERANCE = 256       # how far (samples) a frame may move to line up with the last one
MAX_STRETCH = 0.15    # a beat changed by more than 15 % means the grid is wrong, not the song
KINK_SECONDS = 0.03   # a beat further than this off the line through its neighbours is not pinned


class WarpError(ValueError):
    """A lock that cannot be applied; the message is shown to the person."""


def steady_beats(beats: np.ndarray) -> np.ndarray:
    """Indices of the beats to pin the stretch to. A beat that sits well off the line
    through its neighbours (a beat dragged onto a fill, or one the tracker caught on an
    off-beat hit) would make the audio lurch, so it is left out and the beats around it
    carry the time map; real drift is spread over many beats and stays."""
    keep = np.ones(len(beats), dtype=bool)
    while True:
        idx = np.flatnonzero(keep)
        if len(idx) < 3:
            return idx
        a, i, b = idx[:-2], idx[1:-1], idx[2:]
        expected = beats[a] + (beats[b] - beats[a]) * (i - a) / (b - a)
        off = np.abs(beats[i] - expected)
        worst = int(np.argmax(off))
        if off[worst] <= KINK_SECONDS:
            return idx
        keep[i[worst]] = False


def anchors(beats: list[float], bpm: float) -> tuple[np.ndarray, np.ndarray]:
    """Source and target time of the beats the stretch is pinned to. The first beat stays
    where it is, so the intro before it and the lead-in are untouched; after the last
    beat time runs 1:1. Beat n of the song lands exactly on beat n of the steady grid."""
    every = np.asarray(sorted(beats), dtype=np.float64)
    if len(every) < 2:
        raise WarpError("ต้องมีจังหวะอย่างน้อยสองจังหวะ")
    if not 30 <= bpm <= 300:
        raise WarpError("ความเร็วต้องอยู่ระหว่าง 30 ถึง 300 BPM")
    period = 60.0 / bpm
    average = 60.0 * (len(every) - 1) / (every[-1] - every[0])
    if not 0.8 <= average / bpm <= 1.25:
        hint = " ถ้าห่างกันครึ่งหนึ่งหรือสองเท่า ลองใส่ความเร็วใหม่ หรือกด ×2 / ÷2 ในแก้เมโทรนอม" if 1.7 <= max(average / bpm, bpm / average) <= 2.3 else ""
        raise WarpError(f"เมโทรนอมของเพลงนี้เฉลี่ย {average:.1f} BPM ห่างจาก {bpm:g} มากเกินไป{hint}")
    kept = steady_beats(every)
    kept = np.unique(np.concatenate([[0], kept, [len(every) - 1]]))  # both ends always pinned
    src = every[kept]
    dst = every[0] + kept * period
    ratios = np.diff(dst) / np.diff(src)
    bad = np.flatnonzero(np.abs(ratios - 1) > MAX_STRETCH)
    if len(bad):
        # Neighbouring bad stretches are one place to fix; name each place once.
        places: list[float] = []
        for j in bad:
            t = float(src[j])
            if not places or t - places[-1] > 3.0:
                places.append(t)
        shown = ", ".join(f"{int(t // 60)}:{int(t % 60):02d}" for t in places[:6])
        more = f" และอีก {len(places) - 6} จุด" if len(places) > 6 else ""
        raise WarpError(
            f"เมโทรนอมบางช่วงไม่ตรงกับเพลง ถ้าล็อกตอนนี้เสียงจะถูกยืดหรือหดเกิน {MAX_STRETCH:.0%} "
            f"ที่ {shown}{more} ตรงนั้นน่าจะมีบีตเกินหรือขาด เปิดแก้เมโทรนอมแล้วกด \"ซ่อมจุดที่เพี้ยน\" หรือแก้ตรงนั้นเอง แล้วค่อยล็อก"
        )
    return src, dst


def stretch_range(beats: list[float], bpm: float) -> dict:
    """How much the audio has to move, for showing the person before and after locking."""
    src, dst = anchors(beats, bpm)
    ratios = np.diff(dst) / np.diff(src)
    return {"slowest": round(float(ratios.min() - 1) * 100, 1), "fastest": round(float(ratios.max() - 1) * 100, 1)}


def forward(t, src: np.ndarray, dst: np.ndarray):
    """Song time to locked time."""
    t = np.asarray(t, dtype=np.float64)
    out = np.interp(t, src, dst)
    out = np.where(t < src[0], t, out)                       # before the first beat: 1:1
    return np.where(t > src[-1], dst[-1] + (t - src[-1]), out)  # after the last beat: 1:1


def backward(t, src: np.ndarray, dst: np.ndarray):
    """Locked time to song time."""
    return forward(t, dst, src)


def _window() -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME) / FRAME)).astype(np.float32)


def plan(mix: np.ndarray, src: np.ndarray, dst: np.ndarray, out_samples: int, sample_rate: int) -> np.ndarray:
    """Where in the input each output frame is read from (WSOLA), decided on the mono mix."""
    total = len(mix)
    frames = int(np.ceil(out_samples / HOP)) + 1
    centres = (np.arange(frames) * HOP + FRAME / 2) / sample_rate
    wanted = np.round(backward(centres, src, dst) * sample_rate - FRAME / 2).astype(np.int64)
    padded = np.concatenate([np.zeros(TOLERANCE + FRAME, np.float32), mix, np.zeros(TOLERANCE + 2 * FRAME, np.float32)])
    shift = TOLERANCE + FRAME  # index in `padded` of input sample 0
    starts = np.empty(frames, dtype=np.int64)
    previous = int(wanted[0])
    starts[0] = previous
    size = 1 << int(np.ceil(np.log2(FRAME + 2 * TOLERANCE + FRAME)))
    for k in range(1, frames):
        target = int(np.clip(wanted[k], -FRAME, total))
        natural = previous + HOP  # what would follow the last frame seamlessly
        template = padded[shift + natural: shift + natural + FRAME]
        lo = target - TOLERANCE
        region = padded[shift + lo: shift + lo + FRAME + 2 * TOLERANCE]
        if not np.any(template) or not np.any(region):
            previous = target  # silence: nothing to line up, keep to the time map
        else:
            spectrum = np.fft.rfft(region, size) * np.conj(np.fft.rfft(template, size))
            corr = np.fft.irfft(spectrum, size)[: 2 * TOLERANCE + 1]
            previous = lo + int(np.argmax(corr))
        starts[k] = previous
    return starts


def render(audio: np.ndarray, starts: np.ndarray, out_samples: int) -> np.ndarray:
    """Overlap-add one track (samples × channels) from the planned frame positions."""
    window = _window()[:, None]
    channels = audio.shape[1]
    padded = np.concatenate([np.zeros((FRAME, channels), np.float32), audio, np.zeros((2 * FRAME, channels), np.float32)])
    out = np.zeros((len(starts) * HOP + FRAME, channels), dtype=np.float32)
    weight = np.zeros(len(out), dtype=np.float32)
    w = window[:, 0]
    for k, start in enumerate(starts):
        a = int(np.clip(start, -FRAME, len(audio))) + FRAME
        y = k * HOP
        out[y: y + FRAME] += padded[a: a + FRAME] * window
        weight[y: y + FRAME] += w
    out /= np.maximum(weight, 1e-3)[:, None]
    return out[:out_samples]


def locked_samples(samples: int, src: np.ndarray, dst: np.ndarray, sample_rate: int) -> int:
    return int(round(float(forward(samples / sample_rate, src, dst)) * sample_rate))


def warp_files(sources: dict[str, Path], out_dir: Path, src: np.ndarray, dst: np.ndarray, sample_rate: int,
               write_wav, peaks_fn) -> dict:
    """Stretch every track with one shared plan. Returns waveform peaks per track."""
    tracks = {name: sf.read(path, dtype="float32", always_2d=True)[0] for name, path in sources.items()}
    length = max(len(a) for a in tracks.values())
    mix = np.zeros(length, dtype=np.float32)
    for audio in tracks.values():
        mix[: len(audio)] += audio.mean(axis=1)
    out_samples = locked_samples(length, src, dst, sample_rate)
    starts = plan(mix, src, dst, out_samples, sample_rate)
    del mix
    peaks = {}
    for name, audio in tracks.items():
        warped = np.clip(render(audio, starts, out_samples), -1.0, 1.0)
        write_wav(out_dir / f"{name}.wav", warped, sample_rate)
        peaks[name] = peaks_fn(warped.T, sample_rate)
    return peaks

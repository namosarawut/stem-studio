"""Repair the metronome where it has slipped off the music, using the drum track.

The beat tracker (or a hand edit) sometimes puts an extra beat into a fill or a break, or
leaves one out, and then squeezes or stretches the beats around it to catch up. The click
lurches there, and a tempo lock would have to stretch the audio by 20-40 %. This finds
those places the same way the tempo lock does, and rebuilds each one:

1. Pin it between two solid beats: beats that sit on a drum hit, next to another one.
2. Choose how many beats fit between them: the count that, spaced evenly at the local
   tempo, lands on the most drum hits (the old count wins a tie).
3. Space them evenly and move each onto a drum hit when one is within 40 ms.
4. Keep the bar lines: bars of `meter` continue from the last bar line before the spot,
   so a beat removed or added shows up as one short or long bar just before the next bar
   line the old grid had.

A spot is only changed when the new beats land on at least as many drum hits as the old
ones. Spots without drums keep their beat count and are just evened out.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import warp

FIX_STRETCH = 0.10      # repair spots a lock would stretch by more than this
HIT_WINDOW = 0.03       # a beat within this of a drum hit sits on it
SNAP_WINDOW = 0.04      # a repaired beat moves onto a drum hit this close
MAX_SEARCH = 24         # beats to look either side of a spot for a solid anchor
CONTEXT = 16            # beats either side used to measure the local tempo
MAX_SPACING_CHANGE = 0.12
HIT_STRENGTH = 0.15     # onsets weaker than this share of the loudest are ignored
AMBIGUOUS = 0.3         # a beat count this far from a whole number is left as it was
JOIN_BEATS = 16         # spots this close are rebuilt as one
ODD_CONTEXT = 6         # gaps either side that must agree before one beat is called out of place
ODD_STEADY = 0.15       # ... agree: longest of them at most this much over the shortest
ODD_DRAG = 0.3          # a long and a short gap differing by this share of a beat: a dragged beat
DRIFT_SECONDS = 0.035   # a stretch of beats this far off the drums on average has drifted
DRIFT_SHARE = 0.2       # a grid drifted over this share of its length no longer follows the song

_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}


def onsets(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Onset times of a track and their strength (share of the loudest), cached per file
    version. Weak onsets (under 5 %) are dropped."""
    import librosa

    key = (str(path), path.stat().st_mtime_ns)
    if key not in _cache:
        y, sr = librosa.load(path, sr=22050, mono=True)
        envelope = librosa.onset.onset_strength(y=y, sr=sr, hop_length=128)
        frames = librosa.onset.onset_detect(onset_envelope=envelope, sr=sr, units="frames", hop_length=128)
        times = librosa.frames_to_time(frames, sr=sr, hop_length=128)
        strength = envelope[frames] / (float(envelope.max()) or 1.0)
        keep = strength > 0.05
        if len(_cache) > 4:
            _cache.clear()
        _cache[key] = (np.asarray(times[keep], dtype=np.float64), np.asarray(strength[keep], dtype=np.float64))
    return _cache[key]


def drum_hits(path: Path) -> np.ndarray:
    """Times of clear drum hits."""
    times, strength = onsets(path)
    return times[strength > HIT_STRENGTH]


def _near(hits: np.ndarray, t: float, window: float) -> float | None:
    if not len(hits):
        return None
    i = int(np.clip(np.searchsorted(hits, t), 1, len(hits) - 1))
    best = hits[i - 1] if abs(hits[i - 1] - t) <= abs(hits[i] - t) else hits[i]
    return float(best) if abs(best - t) <= window else None


def _on_hit(hits: np.ndarray, t: float) -> bool:
    return _near(hits, t, HIT_WINDOW) is not None


def drift_share(beats: list[float], hits: np.ndarray) -> float:
    """Share of the song where the metronome runs clearly ahead of or behind the drums.
    A grid made steady by hand (one BPM for the whole song) on a song that drifts is
    smooth, so problem_spots finds nothing, yet it slides off the drums by tens of ms."""
    b = np.asarray(sorted(beats), dtype=np.float64)
    if len(b) < 8 or not len(hits):
        return 0.0
    period = float(np.median(np.diff(b)))
    offsets = np.full(len(b), np.nan)
    for i, t in enumerate(b):
        j = int(np.clip(np.searchsorted(hits, t), 1, len(hits) - 1))
        nearest = hits[j - 1] if abs(hits[j - 1] - t) <= abs(hits[j] - t) else hits[j]
        if abs(nearest - t) < 0.45 * period:
            offsets[i] = nearest - t
    drifted = []
    for i in range(len(b)):
        window = offsets[max(0, i - 8):i + 8]
        window = window[~np.isnan(window)]
        if len(window) >= 4:
            drifted.append(abs(float(np.median(window))) > DRIFT_SECONDS)
    return float(np.mean(drifted)) if drifted else 0.0


def scaled_grid(beats: list[float], downbeats: list[float], meter: int, bpm: float) -> dict:
    """The analyzed grid doubled or halved to the tempo the person works in (the beat
    tracker often counts a fast song at half speed), with bars of `meter` kept on its
    first bar line, the way the editor's ×2 and ÷2 do it."""
    b = sorted(float(t) for t in beats)
    average = 60.0 * (len(b) - 1) / (b[-1] - b[0])
    downs = {round(d, 3) for d in downbeats}
    anchor_time = next((t for t in b if round(t, 3) in downs), b[0])
    factor = min((0.5, 1.0, 2.0), key=lambda f: abs(np.log(bpm / (average * f))))
    if factor == 2.0:
        fresh = []
        for i, t in enumerate(b):
            fresh.append(round(t, 3))
            if i + 1 < len(b) and b[i + 1] - t >= 0.06:
                fresh.append(round((t + b[i + 1]) / 2, 3))
    elif factor == 0.5:
        start = b.index(anchor_time)
        fresh = [round(t, 3) for i, t in enumerate(b) if (i - start) % 2 == 0]
    else:
        fresh = [round(t, 3) for t in b]
    anchor = min(range(len(fresh)), key=lambda i: abs(fresh[i] - anchor_time))
    return {"beats": fresh, "downbeats": [t for i, t in enumerate(fresh) if (i - anchor) % meter == 0],
            "meter": meter, "factor": factor}


def problem_spots(beats: np.ndarray, bpm: float, limit: float, join: int = 4) -> list[tuple[int, int, float]]:
    """Beat-index ranges a lock at `bpm` would stretch by more than `limit`, with the
    largest stretch in each."""
    kept = warp.steady_beats(beats)
    kept = np.unique(np.concatenate([[0], kept, [len(beats) - 1]]))
    src = beats[kept]
    dst = beats[0] + kept * 60.0 / bpm
    ratios = np.diff(dst) / np.diff(src) - 1
    spots: list[list] = []
    for j in np.flatnonzero(np.abs(ratios) > limit):
        lo, hi, worst = int(kept[j]), int(kept[j + 1]), float(abs(ratios[j]))
        if spots and lo <= spots[-1][1] + join:
            spots[-1][1] = max(spots[-1][1], hi)
            spots[-1][2] = max(spots[-1][2], worst)
        else:
            spots.append([lo, hi, worst])
    return [(lo, hi, worst) for lo, hi, worst in spots]


def odd_gaps(beats: np.ndarray) -> list[tuple[int, int, float]]:
    """One beat out of place inside a steady stretch: missing (one gap twice as long),
    one too many (two gaps that add up to one beat), or dragged off its place (a long
    gap next to a short one that add up to two beats). The lock check alone misses these:
    the time map pins only beats on a straight line, so one beat off spreads over dozens
    and looks like a few %. Only where the beats either side agree with each other, so a
    real tempo change or a loose live feel is left alone."""
    gaps = np.diff(beats)
    spots: list[tuple[int, int, float]] = []
    j = 0
    while j < len(gaps):
        around = np.concatenate([gaps[max(0, j - ODD_CONTEXT):j], gaps[j + 2:j + 2 + ODD_CONTEXT]])
        if len(around) < ODD_CONTEXT or around.max() / around.min() > 1 + ODD_STEADY:
            j += 1
            continue
        period = float(np.median(around))
        one = gaps[j] / period
        two = (gaps[j] + gaps[j + 1]) / period if j + 1 < len(gaps) else None
        if 1.75 <= one <= 2.25:
            spots.append((j, j + 1, one - 1))                                  # missing
        elif two is not None and 0.85 <= two <= 1.15 and max(gaps[j], gaps[j + 1]) < 0.8 * period:
            spots.append((j, j + 2, 1.0))                                      # one too many
        elif two is not None and 1.9 <= two <= 2.1 and abs(gaps[j] - gaps[j + 1]) > ODD_DRAG * period:
            spots.append((j, j + 2, abs(gaps[j] - gaps[j + 1]) / period / 2))   # dragged
        else:
            j += 1
            continue
        j = spots[-1][1]
    return spots


def _spots(beats: np.ndarray, bpm: float, join: int) -> list[tuple[int, int, float]]:
    """Places to rebuild: where a lock would stretch too far, and where one beat is off."""
    merged: list[list] = []
    for lo, hi, worst in sorted(problem_spots(beats, bpm, FIX_STRETCH) + odd_gaps(beats)):
        if merged and lo <= merged[-1][1] + join:
            merged[-1][1] = max(merged[-1][1], hi)
            merged[-1][2] = max(merged[-1][2], worst)
        else:
            merged.append([lo, hi, worst])
    return [(lo, hi, worst) for lo, hi, worst in merged]


def _anchors(beats: np.ndarray, hits: np.ndarray, lo: int, hi: int) -> tuple[int, int, bool]:
    a = lo
    while a > max(1, lo - MAX_SEARCH) and not (_on_hit(hits, beats[a]) and _on_hit(hits, beats[a - 1])):
        a -= 1
    z = hi
    while z < min(len(beats) - 2, hi + MAX_SEARCH) and not (_on_hit(hits, beats[z]) and _on_hit(hits, beats[z + 1])):
        z += 1
    solid = _on_hit(hits, beats[a]) and _on_hit(hits, beats[z])
    return (a, z, True) if solid else (lo, hi, False)


def _candidate(beats: np.ndarray, hits: np.ndarray, a: int, z: int, n: int, snap: bool) -> list[float]:
    step = (beats[z] - beats[a]) / n
    fresh = [beats[a] + i * step for i in range(n + 1)]
    if snap:
        for i in range(1, n):
            hit = _near(hits, fresh[i], SNAP_WINDOW)
            if hit is not None and fresh[i - 1] + 0.05 < hit < fresh[i + 1] - 0.05:
                fresh[i] = hit
    return fresh


def _plan(b: np.ndarray, hits: np.ndarray, out_down: list[bool], a: int, z: int, solid: bool,
          worst: float, meter: int, limit: int) -> dict | None:
    """The best rebuild of beats a..z, or None when nothing beats the old grid."""
    outside = np.concatenate([np.diff(b[max(0, a - CONTEXT):a + 1]), np.diff(b[z:min(limit, z + CONTEXT + 1)])])
    period = float(np.median(outside)) if len(outside) else (b[z] - b[a]) / (z - a)
    old_count = z - a
    guess = (b[z] - b[a]) / period
    # Change the number of beats only where the grid is clearly a beat off (a lock would
    # stretch it past its limit), drums pin both ends, and the local tempo leaves no
    # doubt; a mere wobble keeps its count and is evened out.
    count = old_count
    if solid and worst > warp.MAX_STRETCH and abs(guess - round(guess)) <= AMBIGUOUS:
        count = int(round(guess))
    counts = [n for n in {count, old_count} if n >= 1 and abs((b[z] - b[a]) / n / period - 1) <= MAX_SPACING_CHANGE]
    old_hits = sum(_on_hit(hits, t) for t in b[a + 1:z])
    # Bar lines: bars of `meter` continue from the last one at or before the anchor up to
    # the first one the grid has at or after the end of the span.
    last_down = max((i for i in range(a, -1, -1) if out_down[i]), default=None)
    next_down = next((i for i in range(z, len(out_down)) if out_down[i]), None)
    best = None
    for n in counts:
        fresh = _candidate(b, hits, a, z, n, snap=solid)
        gaps = np.diff(fresh)
        if gaps.min() < 0.6 * period or gaps.max() > 1.4 * period:
            continue
        shift = n - old_count
        span = (next_down + shift - a) if next_down is not None else n + 1
        marks: set[int] = set()
        if last_down is not None:
            first = (meter - (a - last_down) % meter) % meter
            marks = set(range(first, span, meter))
            lengths = np.diff(sorted(marks | ({span} if next_down is not None else set())))
            if meter > 2 and len(lengths) and lengths.min() < 2:
                continue  # would leave a one-beat bar
        new_hits = sum(_on_hit(hits, t) for t in fresh[1:-1])
        if new_hits < old_hits - max(1, old_hits // 10):
            continue  # the old beats sat on clearly more drum hits
        if shift == 0 and np.allclose(fresh, b[a:z + 1], atol=0.0015):
            continue
        score = (n == count, new_hits, -abs(shift))
        if best is None or score > best["score"]:
            best = {"score": score, "a": a, "z": z, "n": n, "shift": shift, "fresh": fresh, "marks": marks,
                    "span": span, "last_down": last_down, "next_down": next_down, "solid": solid}
    return best


def repair(beats: list[float], downbeats: list[float], meter: int, hits: np.ndarray, bpm: float | None = None) -> dict:
    """Return the repaired grid (song time) and what changed where."""
    b = np.asarray(sorted(beats), dtype=np.float64)
    if len(b) < 8:
        return {"beats": list(b), "downbeats": sorted(downbeats), "meter": meter, "fixed": [], "left": []}
    if not bpm:
        bpm = 60.0 * (len(b) - 1) / (b[-1] - b[0])
    downs = {round(float(d), 3) for d in downbeats}
    out_beats = list(map(float, b))
    out_down = [round(float(t), 3) in downs for t in b]
    fixed = []
    limit = len(b)  # spots are rebuilt from the end, so indices before `limit` still match b
    # Spots close together are often one mistake and its correction (a beat early, one
    # late), so they are rebuilt as one span.
    for lo, hi, worst in reversed(_spots(b, bpm, JOIN_BEATS)):
        if hi >= limit - 1:
            continue
        # Try the nearest solid beats first, then reach further out. A wider span often
        # shows the beat count was right and the beats were only misplaced (one dragged
        # early, one late), which is better than adding an odd bar.
        plans = []
        a, z = lo, hi
        for _ in range(3):
            a, z, solid = _anchors(b, hits, a, z)
            z = min(z, limit - 2)
            if z <= a:
                break
            plan = _plan(b, hits, out_down, a, z, solid, worst, meter, limit)
            if plan:
                plans.append(plan)
                if plan["shift"] == 0:
                    break
            a, z = max(1, a - 1), z + 1
        if not plans:
            continue
        plan = next((p for p in plans if p["shift"] == 0), plans[0])
        a, z, n, shift = plan["a"], plan["z"], plan["n"], plan["shift"]
        flags = out_down[:a] + [False] * (n + 1) + out_down[z + 1:]
        if plan["last_down"] is not None:
            for offset in range(plan["span"]):
                flags[a + offset] = offset in plan["marks"]
            if plan["next_down"] is not None:
                flags[plan["next_down"] + shift] = True  # the bar line the spot leads into stays
        out_beats = out_beats[:a] + list(plan["fresh"]) + out_beats[z + 1:]
        out_down = flags
        limit = a
        fixed.append({"start": round(float(b[a]), 3), "end": round(float(b[z]), 3), "change": shift, "drums": plan["solid"]})

    rounded = [round(t, 3) for t in out_beats]
    result_downs = sorted({t for t, d in zip(rounded, out_down) if d})
    left = problem_spots(np.asarray(rounded), bpm, warp.MAX_STRETCH)
    return {
        "beats": rounded, "downbeats": result_downs, "meter": meter, "fixed": sorted(fixed, key=lambda f: f["start"]),
        "left": [round(float(rounded[lo]), 3) for lo, _, _ in left],
    }

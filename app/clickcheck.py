"""A close look at a metronome next to the drums, for tools that fix it from outside the
page (the MCP server, app/mcp_server.py, used from Claude).

Everything here reads; nothing is saved. Times are timeline times (what the page shows),
the caller converts. The checks are the same ones the repair and the tempo lock use.
"""
from __future__ import annotations

import numpy as np

from . import gridfix, warp


def clock(t: float) -> str:
    sign = "-" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60)}:{t % 60:05.2f}"


def _nearest(hits: np.ndarray, t: float) -> float | None:
    if not len(hits):
        return None
    i = int(np.clip(np.searchsorted(hits, t), 1, len(hits) - 1))
    return float(hits[i - 1] if abs(hits[i - 1] - t) <= abs(hits[i] - t) else hits[i])


def _spots(beats: np.ndarray, bpm: float, limit: float) -> list[dict]:
    return [{"from": clock(beats[lo]), "to": clock(beats[hi]), "start": round(float(beats[lo]), 3), "end": round(float(beats[hi]), 3),
             "worst_stretch_percent": round(worst * 100)} for lo, hi, worst in gridfix.problem_spots(beats, bpm, limit, join=8)]


def odd_bars(beats: list[float], downbeats: set[float], meter: int) -> list[dict]:
    idx = [i for i, t in enumerate(beats) if round(t, 3) in downbeats]
    return [{"at": clock(beats[i]), "time": beats[i], "beats": idx[k + 1] - i}
            for k, i in enumerate(idx[:-1]) if idx[k + 1] - i != meter]


def overview(beats: list[float], downbeats: list[float], meter: int, drums: np.ndarray, bpm: float | None) -> dict:
    """How well the metronome follows the drums, and what a tempo lock would make of it."""
    b = np.asarray(sorted(beats), dtype=float)
    if len(b) < 2:
        return {"beats": len(b)}
    average = 60 * (len(b) - 1) / (b[-1] - b[0])
    target = bpm or average
    one_off = [{"from": clock(b[lo]), "to": clock(b[hi]), "start": round(float(b[lo]), 3)} for lo, hi, _ in gridfix.odd_gaps(b)]
    try:
        lock = {"bpm": round(target, 2), "passes": True, "stretch_range_percent": warp.stretch_range(list(b), target)}
    except warp.WarpError as error:
        lock = {"bpm": round(target, 2), "passes": False, "why": str(error)}
    on_drums = None
    if len(drums):
        near = np.array([abs((_nearest(drums, t) or 1e9) - t) for t in b])
        on_drums = round(float(np.mean(near <= gridfix.HIT_WINDOW)), 2)
    return {
        "beats": len(b), "meter": meter, "average_bpm": round(average, 2),
        "share_of_beats_on_a_drum_hit": on_drums,
        "share_of_song_drifted_off_the_drums": round(gridfix.drift_share(list(b), drums), 2) if len(drums) else None,
        "one_beat_out_of_place": one_off[:40],
        "spots_a_lock_would_stretch_over_15_percent": _spots(b, target, warp.MAX_STRETCH),
        "spots_over_10_percent": _spots(b, target, gridfix.FIX_STRETCH),
        "bars_with_wrong_beat_count": odd_bars(list(b), {round(d, 3) for d in downbeats}, meter)[:40],
        "tempo_lock_check": lock,
    }


def beats_near(beats: list[float], downbeats: list[float], start: float, end: float,
               drums: np.ndarray, drum_strength: np.ndarray, bass: np.ndarray, bass_strength: np.ndarray) -> dict:
    """Each beat in [start, end] (at most 30 s) with its place in the bar and how far the
    nearest drum hit is, plus the drum and bass hits there with their strength (0-1)."""
    end = min(end, start + 30)
    downs = {round(d, 3) for d in downbeats}
    b = sorted(beats)
    rows = []
    bar_pos = None
    for i, t in enumerate(b):
        if round(t, 3) in downs:
            bar_pos = 1
        elif bar_pos is not None:
            bar_pos += 1
        if start <= t <= end:
            hit = _nearest(drums, t)
            rows.append({
                "i": i, "t": round(t, 3), "gap_to_next": round(b[i + 1] - t, 3) if i + 1 < len(b) else None, "beat_in_bar": bar_pos,
                "nearest_drum_ms": None if hit is None or abs(hit - t) > 0.2 else round((hit - t) * 1000),
            })
    pick = lambda hits, strength: [[round(float(h), 3), round(float(s), 2)] for h, s in zip(hits, strength) if start <= h <= end]  # noqa: E731
    return {"start": start, "end": end, "beats": rows, "drum_hits": pick(drums, drum_strength), "bass_hits": pick(bass, bass_strength)}

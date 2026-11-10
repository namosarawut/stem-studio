"""Tempo map: where a song keeps one tempo, and where it speeds up or slows down.

Works from beat times, so it applies equally to the analyzed grid and to a grid edited
by hand. The log of the per-beat tempo is fitted piecewise-linearly with dynamic
programming: a flat piece is a steady tempo, a sloped piece an accelerando or
ritardando. Each extra section costs a fixed penalty, so beat-to-beat wobble of a live
player does not split the song while a real change does.
"""
from __future__ import annotations

import numpy as np

SEGMENT_PENALTY = 0.012      # in squared log-tempo; tuned on steady and changing songs
MIN_SECTION_BARS = 4
STEADY_CHANGE = 0.02         # under 2 % change across a section it is called steady
MERGE_DIFFERENCE = 0.012     # neighbouring steady sections closer than 1.2 % become one
SMOOTH_BEATS = 3
EDGE_BARS = 4
UNSTEADY_CHANGES = 5      # this many changes reads as an unsteady grid, not a plan


def _median_smooth(values: np.ndarray, size: int) -> np.ndarray:
    if size <= 1 or len(values) < size:
        return values
    pad = size // 2
    padded = np.pad(values, pad, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    return np.median(windows, axis=1)


def _segment(x: np.ndarray, min_len: int, penalty: float) -> list[tuple[int, int]]:
    """Optimal split of x into linear pieces of at least min_len points."""
    n = len(x)
    if n < 2 * min_len:
        return [(0, n)]
    idx = np.arange(n, dtype=float)
    # Prefix sums let the least-squares error of any piece be computed in O(1).
    c1 = np.concatenate([[0], np.cumsum(np.ones(n))])
    ci = np.concatenate([[0], np.cumsum(idx)])
    cii = np.concatenate([[0], np.cumsum(idx * idx)])
    cx = np.concatenate([[0], np.cumsum(x)])
    cxx = np.concatenate([[0], np.cumsum(x * x)])
    cix = np.concatenate([[0], np.cumsum(idx * x)])

    def cost(a: np.ndarray, b: int) -> np.ndarray:
        m = c1[b] - c1[a]
        si, sii = ci[b] - ci[a], cii[b] - cii[a]
        sx, sxx, six = cx[b] - cx[a], cxx[b] - cxx[a], cix[b] - cix[a]
        var_i = sii - si * si / m
        cov = six - si * sx / m
        var_x = sxx - sx * sx / m
        with np.errstate(divide="ignore", invalid="ignore"):
            residual = var_x - np.where(var_i > 1e-12, cov * cov / var_i, 0.0)
        return np.maximum(residual, 0.0)

    best = np.full(n + 1, np.inf)
    best[0] = -penalty
    previous = np.zeros(n + 1, dtype=int)
    for end in range(min_len, n + 1):
        starts = np.arange(0, end - min_len + 1)
        starts = starts[(starts == 0) | (starts >= min_len)]
        totals = best[starts] + cost(starts, end) + penalty
        k = int(np.argmin(totals))
        best[end] = totals[k]
        previous[end] = starts[k]
    pieces, end = [], n
    while end > 0:
        start = previous[end]
        pieces.append((start, end))
        end = start
    return pieces[::-1]


def tempo_map(beats: list[float], downbeats: list[float] | None = None, meter: int = 4) -> list[dict]:
    """Sections of steady or changing tempo, each with its BPM, in the beats' time base."""
    times = np.asarray(sorted(beats), dtype=float)
    if len(times) < 3:
        return []
    intervals = np.diff(times)
    bpm = 60.0 / np.clip(intervals, 1e-3, None)
    log_bpm = np.log(_median_smooth(bpm, SMOOTH_BEATS))
    pieces = _segment(log_bpm, max(4, MIN_SECTION_BARS * meter), SEGMENT_PENALTY)

    # Line bar starts up with section boundaries when one is within half a bar.
    bar_index = []
    if downbeats:
        lookup = {round(d, 3): True for d in downbeats}
        bar_index = [i for i, t in enumerate(times) if round(t, 3) in lookup]
    snapped = []
    for start, end in pieces:
        if bar_index and start > 0:
            nearest = min(bar_index, key=lambda b: abs(b - start))
            if abs(nearest - start) <= meter // 2 and 0 < nearest < len(intervals):
                start = nearest
        snapped.append(start)
    bounds = sorted(set(snapped) | {len(intervals)})

    sections = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a < 2:
            continue
        piece = bpm[a:b]
        fit = np.polyfit(np.arange(b - a), np.log(piece), 1) if b - a >= 3 else (0.0, np.log(piece.mean()))
        first, last = float(np.exp(fit[1])), float(np.exp(fit[1] + fit[0] * (b - a - 1)))
        change = (last - first) / first
        steady = abs(change) < STEADY_CHANGE
        sections.append({
            "start": round(float(times[a]), 3),
            "end": round(float(times[b]), 3),
            "beats": int(b - a),
            # Over a whole section, beats per elapsed time is the tempo a player would count.
            "bpm": round(float(60.0 * (b - a) / float(times[b] - times[a])), 1),
            "from": round(first, 1),
            "to": round(last, 1),
            "kind": "steady" if steady else ("accel" if change > 0 else "ritard"),
        })

    # A few beats at the very start or end (pickups, fades, a final ritardando that ends
    # the song) are not a tempo players count; fold them into the neighbouring section.
    min_edge = EDGE_BARS * meter
    while len(sections) > 1 and sections[0]["beats"] < min_edge:
        first, second = sections.pop(0), sections[0]
        total = first["beats"] + second["beats"]
        second.update(start=first["start"], beats=total, bpm=round(60.0 * total / (second["end"] - first["start"]), 1))
    while len(sections) > 1 and sections[-1]["beats"] < min_edge:
        last, before = sections.pop(), sections[-1]
        total = before["beats"] + last["beats"]
        before.update(end=last["end"], beats=total, bpm=round(60.0 * total / (last["end"] - before["start"]), 1))

    merged: list[dict] = []
    for section in sections:
        prev = merged[-1] if merged else None
        same_steady = (
            prev and prev["kind"] == "steady" and section["kind"] == "steady"
            and abs(prev["bpm"] - section["bpm"]) / prev["bpm"] < MERGE_DIFFERENCE
        )
        # Two pieces slowing (or speeding) one after the other are one ritardando.
        same_ramp = prev and prev["kind"] == section["kind"] != "steady"
        if same_steady or same_ramp:
            total = prev["beats"] + section["beats"]
            prev["bpm"] = round(60.0 * total / (section["end"] - prev["start"]), 1)
            prev.update(end=section["end"], beats=total, to=section["to"])
        else:
            merged.append(section)
    return merged


def summary(sections: list[dict]) -> dict:
    """Headline numbers: the main tempo (where most beats are) and the range."""
    if not sections:
        return {"main": None, "low": None, "high": None, "changes": 0, "unsteady": False}
    main = max(sections, key=lambda s: s["beats"])
    values = [v for s in sections for v in (s["from"], s["to"], s["bpm"])]
    return {
        "main": float(main["bpm"]), "low": round(float(min(values)), 1), "high": round(float(max(values)), 1),
        "changes": len(sections) - 1, "unsteady": len(sections) - 1 >= UNSTEADY_CHANGES,
    }

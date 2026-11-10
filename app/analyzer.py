"""Works out how a song is built, using the separated stems as evidence.

Having stems makes each estimate cleaner than running on the full mix: tempo is
read from the drums, chords from the bass + harmonic instruments (no vocals or
cymbals smearing the chroma), and section boundaries from instruments actually
entering and leaving.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable

import librosa
import numpy as np
import soundfile as sf
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.ndimage import median_filter, uniform_filter1d

from .tempo import summary as tempo_summary
from .tempo import tempo_map

SR = 22050
HOP = 512
BEAT_HOP = 128
CURVE_MIN_GAIN = 1.5
# Bump when beat or tempo analysis changes; older songs are re-analyzed when opened.
ANALYSIS_VERSION = 2
SNAP_SECONDS = 0.035
SNAP_MIN_STRENGTH = 0.15
EPS = 1e-10
SAME_SECTION_DISTANCE = 0.75
MIN_SECTION_BARS = 4
CLICK_FIT_BEATS = 8
BACKING_GATE_DB = 15

SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
FLAT_MAJOR = {5, 10, 3, 8, 1, 6}  # F Bb Eb Ab Db Gb
FLAT_MINOR = {2, 7, 0, 5, 10, 3}  # Dm Gm Cm Fm Bbm Ebm

# Krumhansl-Kessler key profiles
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

CAMELOT_MAJOR = {0: "8B", 7: "9B", 2: "10B", 9: "11B", 4: "12B", 11: "1B", 6: "2B", 1: "3B", 8: "4B", 3: "5B", 10: "6B", 5: "7B"}
CAMELOT_MINOR = {9: "8A", 4: "9A", 11: "10A", 6: "11A", 1: "12A", 8: "1A", 3: "2A", 10: "3A", 5: "4A", 0: "5A", 7: "6A", 2: "7A"}

ROMAN_MAJOR = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV", 6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
ROMAN_MINOR = {0: "I", 1: "bII", 2: "II", 3: "III", 4: "#III", 5: "IV", 6: "#IV", 7: "V", 8: "VI", 9: "#VI", 10: "VII", 11: "#VII"}

VOCAL_STEMS = ("vocals", "lead_vocals", "backing_vocals")
STEM_THAI = {"vocals": "เสียงร้อง", "lead_vocals": "เสียงร้องหลัก", "backing_vocals": "เสียงร้องคอรัส", "drums": "กลอง", "bass": "เบส", "other": "ดนตรีอื่นๆ", "guitar": "กีตาร์", "piano": "เปียโน", "click": "เมโทรนอม"}


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def db(value: float) -> float:
    return float(20 * np.log10(max(value, EPS)))


# ---------------------------------------------------------------- rhythm

def _local_period(times: np.ndarray, index: int, span: int = 8) -> float:
    """Typical beat length around one point of the song, so gaps are filled at the tempo
    the music has there rather than the song's average."""
    gaps = np.diff(times[max(0, index - span): index + span + 1])
    gaps = gaps[gaps > 0.15]
    return float(np.median(gaps)) if len(gaps) else 0.5


def _complete_grid(times: np.ndarray, duration: float) -> np.ndarray:
    """Extend tracked beats to the whole song and fill any gaps, keeping their phase."""
    first, last = _local_period(times, 0), _local_period(times, len(times) - 1)
    parts = [np.arange(times[0] - first, -first / 2, -first)[::-1].clip(min=0)]
    for i, (a, b) in enumerate(zip(times[:-1], times[1:])):
        parts.append(np.array([a]))
        missing = int(round((b - a) / _local_period(times, i))) - 1
        if missing > 0:
            parts.append(a + (b - a) * np.arange(1, missing + 1) / (missing + 1))
    parts.append(np.array([times[-1]]))
    parts.append(np.arange(times[-1] + last, duration, last))
    grid = np.concatenate(parts)
    grid.sort()
    keep = np.concatenate([[True], np.diff(grid) > 0.1])
    return grid[keep]


def _refine_beats(times: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Beat times accurate enough to play a click against the song.

    1. Each tracked beat is moved to the drum attack nearest it (within 35 ms), measured
       on a 1.5 ms grid, which removes the tracker's frame rounding.
    2. A line is fitted through neighbouring beats to remove remaining jitter; next to a
       sudden tempo change the fit uses the steadier side only, keeping the corner sharp.
    3. The grid is nudged as a whole onto the attacks.
    """
    hop, frame = 32, 256
    energy = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    attack = np.maximum(np.diff(np.log1p(energy * 200)), 0)
    # Energy rises as a hit enters the window's leading edge, half a frame ahead of its centre.
    attack_times = ((np.arange(len(attack)) + 0.5) * hop + frame / 2) / SR

    reach = int(SNAP_SECONDS * SR / hop)
    strong = np.percentile(attack, 99) if len(attack) else 0.0
    snapped = times.copy()
    has_hit = np.zeros(len(times), dtype=bool)
    for i, t in enumerate(times):
        centre = int(np.searchsorted(attack_times, t))
        lo, hi = max(0, centre - reach), min(len(attack), centre + reach + 1)
        if hi <= lo:
            continue
        k = lo + int(np.argmax(attack[lo:hi]))
        if attack[k] > strong * SNAP_MIN_STRENGTH:
            snapped[i] = attack_times[k]
            has_hit[i] = True

    count = len(times)
    smoothed = times.copy()
    index = np.arange(count)
    for i in range(count):
        candidates, fallback = [], []
        for lo, hi in ((i - CLICK_FIT_BEATS, i + 1), (i, i + CLICK_FIT_BEATS + 1), (i - CLICK_FIT_BEATS, i + CLICK_FIT_BEATS + 1)):
            lo, hi = max(0, lo), min(count, hi)
            heard = index[lo:hi][has_hit[lo:hi]]
            # Fits through real hits win; the tracked grid is only used where nothing is
            # heard (a break or a drumless intro), as its straight extrapolated lines would
            # otherwise look steadier than the music and pull edge beats off the hits.
            for use, source, bucket in ((heard, snapped, candidates), (index[lo:hi], times, fallback)):
                if len(use) < 4:
                    continue
                slope, intercept = np.polyfit(use, source[use], 1)
                residual = float(np.mean((slope * use + intercept - source[use]) ** 2))
                bucket.append((residual, slope * i + intercept))
        if not candidates:
            candidates = fallback
        if candidates:
            # Prefer the centred fit (most beats averaged); use a one-sided fit only where it
            # is far steadier, which happens right beside a sudden tempo change.
            centred = candidates[-1]
            best = min(candidates)
            smoothed[i] = centred[1] if centred[0] <= 4 * best[0] + 1e-7 else best[1]

    offsets = np.arange(-0.03, 0.0305, 0.0005)
    scores = [float(np.interp(smoothed + o, attack_times, attack, left=0, right=0).sum()) for o in offsets]
    best_offset = float(offsets[int(np.argmax(scores))]) if max(scores) > 0 else 0.0
    return np.clip(smoothed + best_offset, 0, None)


def _upsample(env: np.ndarray, factor: int) -> np.ndarray:
    """The coarse onset envelope on a finer time grid. Same signal (so no extra noise from
    hi-hats and ghost notes), but beats can be placed between the coarse frames."""
    coarse = np.arange(len(env))
    fine = np.arange(len(env) * factor) / factor
    return np.interp(fine, coarse, env)


def _refine_tempo(env: np.ndarray, tempo: float, frame_seconds: float) -> float:
    """Pin the tempo down to about 0.1 %. A comb over the envelope's autocorrelation at 1-8
    beat lags magnifies small period errors, so close candidates separate clearly."""
    centered = env - env.mean()
    ac = np.correlate(centered, centered, mode="full")[len(centered) - 1:]
    lags = np.arange(len(ac))
    def best_in(candidates: np.ndarray, beats: int) -> float:
        multiples = np.arange(1, beats + 1)
        scores = [np.interp(multiples * (60.0 / bpm / frame_seconds), lags, ac).sum() for bpm in candidates]
        return float(candidates[int(np.argmax(scores))])

    # Coarse pass over a short comb finds the right peak; a 32-beat comb then pins it, as
    # an error in the period grows 32 times by the last tooth.
    rough = best_in(np.arange(tempo * 0.95, tempo * 1.05, 0.05), 8)
    longest = max(8, min(32, int(len(env) * frame_seconds / (60.0 / rough)) // 2))
    return best_in(np.arange(rough * 0.99, rough * 1.01, 0.01), longest)


def _hit_strength(frames: np.ndarray, env: np.ndarray, region: np.ndarray) -> float:
    """Average onset strength right at the beats that fall inside a region."""
    inside = frames[(frames < len(region)) & region[np.clip(frames, 0, len(region) - 1)]]
    if len(inside) == 0:
        return 0.0
    norm = env / (env.max() + EPS)
    return float(np.mean([norm[max(0, f - 2): f + 3].max() for f in inside]))


def _tempo_curve(env: np.ndarray, tempo: float) -> np.ndarray | None:
    """Per-frame tempo for songs whose tempo really changes, or None when one tempo fits.
    Octave errors (a half-time groove read as half the tempo) are folded back, and a
    12-second median keeps short fills and breaks from counting as tempo changes."""
    from scipy.stats import lognorm

    local = librosa.feature.tempo(
        onset_envelope=env, sr=SR, hop_length=HOP, aggregate=None, ac_size=8.0,
        prior=lognorm(s=0.35, scale=tempo),
    )
    folded = local.copy()
    for factor in (2.0, 0.5):
        better = np.abs(np.log(local * factor / tempo)) < np.abs(np.log(folded / tempo))
        folded[better] = local[better] * factor
    width = int(12.0 * SR / HOP) | 1
    smooth = median_filter(folded, size=width, mode="nearest")
    deviation = np.abs(np.log(smooth / tempo))
    # Sustained, sizeable departures only: the tracker follows small drifts by itself.
    if np.mean(deviation > 0.06) * len(smooth) * HOP / SR < 8.0:
        return None
    # Steady parts keep the precise tempo; changed parts keep their own.
    smooth = np.where(deviation > 0.06, smooth, tempo)
    return np.repeat(smooth, HOP // BEAT_HOP)


def _refine_downbeat(rhythm: dict, chords: list, lead_in: int) -> int:
    """Kick and bass can hit beats 1 and 3 equally. Chords mostly change on the bar line,
    so combine both signals to choose which beat starts the bar."""
    meter = rhythm["meter"]
    changes = np.zeros(meter)
    for i in range(1, len(chords)):
        if chords[i] != chords[i - 1] and chords[i] is not None and chords[i - 1] is not None:
            changes[(i - lead_in) % meter] += 1
    accents = np.array(rhythm.get("downbeat_scores", [0.0] * meter), dtype=float)
    total = accents / (accents.sum() + EPS) + changes / (changes.sum() + EPS)
    return int(np.argmax(total))


def _estimate_rhythm(rhythm_y: np.ndarray, low_y: np.ndarray, duration: float) -> dict:
    env = librosa.onset.onset_strength(y=rhythm_y, sr=SR, hop_length=HOP, aggregate=np.median)
    coarse_tempo = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=env, sr=SR, hop_length=HOP))[0])
    tempo = _refine_tempo(env, coarse_tempo, HOP / SR) if coarse_tempo > 0 else 0.0
    # The beat tracker rounds a beat to whole frames, which at a fixed 23 ms frame can be
    # 2 % off and lets the click slip. Pick a frame size (still about 23 ms, so the tracker
    # behaves as before) that divides this song's beat exactly.
    beat_samples = SR * 60.0 / tempo if tempo > 0 else HOP * 23
    frames_per_beat = max(8, int(round(beat_samples / HOP)))
    track_hop = int(round(beat_samples / frames_per_beat))
    track_env = librosa.onset.onset_strength(y=rhythm_y, sr=SR, hop_length=track_hop, aggregate=np.median)
    _, tracked = librosa.beat.beat_track(onset_envelope=track_env, sr=SR, hop_length=track_hop, bpm=tempo, trim=False)
    curve = _tempo_curve(env, tempo) if tempo > 0 else None
    if curve is not None:
        # The curve is computed on the standard frames; resample it onto the tracking frames.
        curve_times = np.arange(len(curve)) * BEAT_HOP / SR
        curve = np.interp(np.arange(len(track_env)) * track_hop / SR, curve_times, curve)
        _, following = librosa.beat.beat_track(onset_envelope=track_env, sr=SR, hop_length=track_hop, bpm=curve, trim=False)
        changed = np.abs(np.log(curve / tempo)) > 0.06
        # Syncopation and triplet passages can look like a new tempo. Only a real change
        # leaves a one-tempo grid off the drum hits, so the curve must land clearly better.
        if _hit_strength(following, track_env, changed) >= CURVE_MIN_GAIN * _hit_strength(tracked, track_env, changed):
            tracked = following
        else:
            curve = None

    if len(tracked) < 8:  # free-time or near-silent audio: fall back to a nominal grid
        tempo = tempo if tempo > 0 else 120.0
        count = int(duration * tempo / 60)
        times = np.arange(count) * 60 / tempo
        beat_frames = librosa.time_to_frames(times, sr=SR, hop_length=HOP)
        return {"bpm": round(tempo, 1), "stability": 0.0, "meter": 4, "frames": beat_frames, "times": times, "downbeat_offset": 0, "reliable": False, "varying": False}

    times = librosa.frames_to_time(tracked, sr=SR, hop_length=track_hop)
    span = min(8, len(times) - 1)
    periods = (times[span:] - times[:-span]) / span
    period = float(np.median(periods))
    bpm = 60.0 / period
    stability = 1.0 - min(1.0, float(np.std(np.clip(periods, period * 0.8, period * 1.2)) / period) * 12)

    # Beats are only tracked where drums play, but sections and chords need a grid
    # through drumless intros, breaks and outros too.
    full_times = _complete_grid(times, duration)
    beat_frames = librosa.time_to_frames(full_times, sr=SR, hop_length=HOP)

    low_env = librosa.onset.onset_strength(y=low_y, sr=SR, hop_length=HOP, fmax=180, n_mels=32)
    low_env = low_env[: len(env)]

    # Meter: does the kick and bass accent repeat every 3 beats or every 4? Hi-hats are
    # left out on purpose, as steady subdivisions correlate at every lag.
    accents = librosa.util.sync(low_env.reshape(1, -1), beat_frames[beat_frames < len(low_env)], aggregate=np.mean, pad=False)[0]
    accents = accents - accents.mean()
    ac = np.correlate(accents, accents, mode="full")[len(accents) - 1:]
    ac = ac / (ac[0] + EPS)
    triple = ac[3] + (ac[6] if len(ac) > 6 else 0)
    quadruple = ac[4] + (ac[8] if len(ac) > 8 else 0)
    meter = 3 if triple > 0.5 and triple > quadruple + 0.3 else 4

    # Downbeat: the kick and bass tend to land hardest on beat one.
    idx = np.clip(beat_frames, 0, len(low_env) - 1)
    scores = [float(np.mean(low_env[idx[o::meter]])) for o in range(meter)]
    return {
        "bpm": round(bpm, 1),
        "stability": round(stability, 3),
        "meter": meter,
        "frames": beat_frames,
        "times": _refine_beats(full_times, rhythm_y),
        "varying": curve is not None,
        "downbeat_offset": int(np.argmax(scores)),
        "downbeat_scores": scores,
        "reliable": True,
    }


# ---------------------------------------------------------------- key & chords

def _estimate_key(chroma: np.ndarray) -> dict:
    mean = chroma.mean(axis=1)
    if mean.std() < EPS:
        return {"tonic": 0, "mode": "major", "confidence": 0.0, "alternatives": []}
    scored = []
    for tonic in range(12):
        rotated = np.roll(mean, -tonic)
        scored.append((float(np.corrcoef(rotated, KS_MAJOR)[0, 1]), tonic, "major"))
        scored.append((float(np.corrcoef(rotated, KS_MINOR)[0, 1]), tonic, "minor"))
    scored.sort(reverse=True)
    best = scored[0]
    # A clear winner has a wide gap to the runner-up, which is usually the relative key.
    return {
        "tonic": best[1],
        "mode": best[2],
        "confidence": round(float(np.clip((best[0] - scored[1][0]) / 0.25, 0, 1)), 2),
        "alternatives": scored[1:3],
    }


def _chord_templates() -> tuple[np.ndarray, list[tuple[int, str]]]:
    templates, names = [], []
    for quality, intervals in (("maj", (0, 4, 7)), ("min", (0, 3, 7))):
        for root in range(12):
            t = np.zeros(12)
            t[[(root + i) % 12 for i in intervals]] = 1.0
            t[root] += 0.4  # roots sound stronger than fifths and thirds
            templates.append(t / np.linalg.norm(t))
            names.append((root, quality))
    return np.array(templates), names


def _diatonic(tonic: int, mode: str) -> set[tuple[int, str]]:
    if mode == "major":
        scale = [(0, "maj"), (2, "min"), (4, "min"), (5, "maj"), (7, "maj"), (9, "min")]
    else:
        scale = [(0, "min"), (3, "maj"), (5, "min"), (7, "min"), (7, "maj"), (8, "maj"), (10, "maj")]
    return {((tonic + i) % 12, q) for i, q in scale}


def _estimate_chords(beat_chroma: np.ndarray, key: dict) -> list[tuple[int, str] | None]:
    templates, names = _chord_templates()
    norm = np.linalg.norm(beat_chroma, axis=0, keepdims=True)
    chroma = beat_chroma / (norm + EPS)
    scores = templates @ chroma  # (24, beats)

    diatonic = _diatonic(key["tonic"], key["mode"])
    prior = np.array([1.06 if name in diatonic else 1.0 for name in names]).reshape(-1, 1)
    scores = scores * prior

    # A silent beat maps to the extra "no chord" state.
    silence = (norm[0] < np.percentile(norm[0], 95) * 0.12).astype(float)
    no_chord = np.full((1, scores.shape[1]), 0.45) + silence * 2
    states = np.vstack([scores, no_chord])

    probs = np.exp(states * 14)
    probs = probs / probs.sum(axis=0, keepdims=True)
    n = states.shape[0]
    stay = 0.75
    transition = np.full((n, n), (1 - stay) / (n - 1))
    np.fill_diagonal(transition, stay)
    path = librosa.sequence.viterbi(probs, transition)
    return [names[i] if i < len(names) else None for i in path]


def _key_from_chords(key: dict, chords: list) -> dict:
    """Relative major/minor share nearly the same notes; the chord that actually
    gets the most time and closes the song is the better tiebreak."""
    played = [c for c in chords if c]
    if not played:
        return key
    counts = Counter(played)
    tonic, mode = key["tonic"], key["mode"]
    if mode == "major":
        own, relative, rel_key = (tonic, "maj"), ((tonic + 9) % 12, "min"), ((tonic + 9) % 12, "minor")
    else:
        own, relative, rel_key = (tonic, "min"), ((tonic + 3) % 12, "maj"), ((tonic + 3) % 12, "major")
    tail = played[-max(4, len(played) // 12):]
    own_score = counts[own] + tail.count(own) * 2
    rel_score = counts[relative] + tail.count(relative) * 2
    if rel_score > own_score * 1.35:
        return {**key, "tonic": rel_key[0], "mode": rel_key[1]}
    return key


def _pitch_name(pc: int, key: dict) -> str:
    use_flats = key["tonic"] in (FLAT_MAJOR if key["mode"] == "major" else FLAT_MINOR)
    return (FLAT if use_flats else SHARP)[pc]


def _chord_label(chord, key: dict) -> str:
    if chord is None:
        return "N"
    return _pitch_name(chord[0], key) + ("m" if chord[1] == "min" else "")


def _chord_roman(chord, key: dict) -> str:
    if chord is None:
        return "–"
    table = ROMAN_MAJOR if key["mode"] == "major" else ROMAN_MINOR
    numeral = table[(chord[0] - key["tonic"]) % 12]
    return numeral.lower() if chord[1] == "min" else numeral


def _find_loops(bar_chords: list, key: dict, length: int = 4, top: int = 3) -> list[dict]:
    """Most repeated chord loops, treating rotations of a loop as the same loop."""
    grams: dict[tuple, Counter] = {}
    for i in range(len(bar_chords) - length + 1):
        gram = tuple(bar_chords[i:i + length])
        if None in gram or len(set(gram)) < 2:
            continue
        canonical = min(gram[r:] + gram[:r] for r in range(length))
        grams.setdefault(canonical, Counter())[gram] += 1
    ranked = sorted(grams.values(), key=lambda c: sum(c.values()), reverse=True)
    result = []
    for counter in ranked[:top]:
        gram, _ = counter.most_common(1)[0]
        result.append({
            "chords": [_chord_label(c, key) for c in gram],
            "romans": [_chord_roman(c, key) for c in gram],
            "count": sum(counter.values()),
        })
    return result


# ---------------------------------------------------------------- structure

def _segment(features: np.ndarray, bar_edges: np.ndarray, duration: float) -> list[int]:
    """Split the song into contiguous sections, returned as beat indices on bar lines.

    Ward clustering constrained to neighbours finds the strongest splits first, so a
    large change such as drums entering cannot drown out a subtler verse-to-chorus
    change. It deliberately over-splits: repeats of one section merge again later.
    """
    bars = librosa.util.sync(features, bar_edges, aggregate=np.mean, pad=False)
    count = bars.shape[1]
    wanted = int(np.clip(round(duration / 14), 3, 16))
    if count <= wanted:
        return [int(b) for b in bar_edges]
    cuts = sorted({int(c) for c in librosa.segment.agglomerative(bars, wanted)} | {0, count})

    # Fold fragments into whichever neighbour they most resemble. Real sections rarely
    # run under four bars, and short pieces are usually fades or silence at the edges.
    while len(cuts) > 2:
        lengths = np.diff(cuts)
        shortest = int(np.argmin(lengths))
        if lengths[shortest] >= MIN_SECTION_BARS:
            break
        if shortest == 0:
            cuts.pop(1)
        elif shortest == len(lengths) - 1:
            cuts.pop(-2)
        else:
            current = bars[:, cuts[shortest]:cuts[shortest + 1]].mean(axis=1)
            before = bars[:, cuts[shortest - 1]:cuts[shortest]].mean(axis=1)
            after = bars[:, cuts[shortest + 1]:cuts[shortest + 2]].mean(axis=1)
            closer_before = np.linalg.norm(current - before) <= np.linalg.norm(current - after)
            cuts.pop(shortest if closer_before else shortest + 1)
    return [int(bar_edges[c]) for c in cuts]


def _label_sections(segments: list[dict], has_vocals: bool) -> None:
    n = len(segments)
    if n == 0:
        return
    if n == 1:
        segments[0]["label"] = "Full song"
        return

    # Raw section averages all look alike. Standardizing across sections keeps only
    # what sets them apart, so a distance threshold can then mean "same section type".
    vectors = np.array([s["vector"] for s in segments])
    vectors = (vectors - vectors.mean(axis=0)) / (vectors.std(axis=0) + 1e-6)
    tree = linkage(vectors, method="average", metric="cosine")
    clusters = fcluster(np.nan_to_num(tree), t=SAME_SECTION_DISTANCE, criterion="distance")
    for segment, cluster in zip(segments, clusters):
        segment["cluster"] = int(cluster)

    energies = np.array([s["energy_db"] for s in segments])
    median_energy = float(np.median(energies))

    if not has_vocals:
        # Fewer instruments or a clear drop in level marks the opening and closing.
        density = [sum(s["stems"].values()) for s in segments]
        median_density = float(np.median(density))

        def is_sparse(i: int) -> bool:
            return segments[i]["energy_db"] < median_energy - 2 or density[i] < median_density - 0.7

        themes: dict[int, str] = {}
        for i, segment in enumerate(segments):
            if i == 0 and is_sparse(i):
                segment["label"] = "Intro"
            elif i == n - 1 and is_sparse(i):
                segment["label"] = "Outro"
            else:
                letter = themes.setdefault(segment["cluster"], chr(ord("A") + len(themes)))
                segment["label"] = f"Theme {letter}"
        return

    vocal = [s for s in segments if s["vocal_presence"] >= 0.3]
    by_cluster: dict[int, list[dict]] = {}
    for segment in vocal:
        by_cluster.setdefault(segment["cluster"], []).append(segment)

    def hook_score(items: list[dict]) -> float:
        energy = float(np.mean([s["energy_db"] for s in items])) - median_energy
        # Harmonies pile up on the hook, so a separated backing track is further evidence.
        backing = float(np.mean([s["backing_presence"] for s in items]))
        return energy + (1.5 if len(items) >= 2 else 0) + min(len(items), 4) * 0.5 + backing * 2

    chorus_cluster = max(by_cluster, key=lambda c: hook_score(by_cluster[c])) if by_cluster else None

    for i, segment in enumerate(segments):
        cluster = segment["cluster"]
        if segment["vocal_presence"] < 0.3:
            if i == 0:
                segment["label"] = "Intro"
            elif i == n - 1:
                segment["label"] = "Outro"
            elif segment["energy_db"] < median_energy - 3:
                segment["label"] = "Break"
            else:
                segment["label"] = "Instrumental"
        elif cluster == chorus_cluster:
            segment["label"] = "Chorus"
        elif (
            # A pre-chorus sits between a verse and a chorus. Without a sung section
            # before it, the part leading into the chorus is simply the verse.
            0 < i < n - 1
            and segments[i + 1]["cluster"] == chorus_cluster
            and segments[i - 1]["vocal_presence"] >= 0.3
            and segments[i - 1]["cluster"] not in (cluster, chorus_cluster)
            and segment["bars"] <= 8
        ):
            segment["label"] = "Pre-Chorus"
        elif len(by_cluster.get(cluster, [])) == 1 and i > n * 0.5 and i < n - 1:
            segment["label"] = "Bridge"
        else:
            segment["label"] = "Verse"


# ---------------------------------------------------------------- per-stem measurements

def _activity(y: np.ndarray, duration: float) -> tuple[np.ndarray, np.ndarray, float]:
    """Per-frame active mask plus level. A stem counts as 'active' relative to its
    own loud parts, so separation bleed at -30 dB does not read as playing."""
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    rms_db = 20 * np.log10(rms + EPS)
    reference = float(np.percentile(rms_db, 95))
    active = (rms_db > reference - 22) & (rms_db > -52)
    active = uniform_filter1d(active.astype(float), size=int(SR / HOP)) > 0.35  # smooth over ~1 s
    return active, rms, reference


def _stem_metrics(name: str, stereo: np.ndarray, y: np.ndarray, duration: float) -> dict:
    left, right = stereo[0], stereo[1]
    mid, side = (left + right) / 2, (left - right) / 2
    active, rms, reference = _activity(y, duration)
    audible = reference > -50

    spectrum = np.abs(librosa.stft(y, n_fft=2048, hop_length=2048)) ** 2
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    power = spectrum.sum(axis=1)
    total = power.sum() + EPS
    centroid = float((freqs * power).sum() / total)

    first_entry = None
    if audible and active.any():
        first_entry = float(librosa.frames_to_time(int(np.argmax(active)), sr=SR, hop_length=HOP))

    bins = 240
    edges = np.linspace(0, len(rms), bins + 1).astype(int)
    level = np.array([rms[a:b].mean() if b > a else 0 for a, b in zip(edges[:-1], edges[1:])])
    peak_level = level.max() + EPS

    return {
        "id": name,
        "name": STEM_THAI.get(name, name),
        "audible": bool(audible),
        "rms_db": round(db(float(np.sqrt(np.mean(stereo ** 2)))), 1),
        "peak_db": round(db(float(np.abs(stereo).max())), 1),
        "width_pct": round(min(100.0, float(np.sqrt(np.mean(side ** 2)) / (np.sqrt(np.mean(mid ** 2)) + EPS)) * 100), 1),
        "centroid_hz": round(centroid),
        "bands": {
            "low": round(float(power[freqs < 250].sum() / total) * 100, 1),
            "mid": round(float(power[(freqs >= 250) & (freqs < 4000)].sum() / total) * 100, 1),
            "high": round(float(power[freqs >= 4000].sum() / total) * 100, 1),
        },
        "first_entry": round(first_entry, 2) if first_entry is not None else None,
        "presence_pct": round(float(active.mean()) * 100 if audible else 0.0, 1),
        "activity": [round(float(v / peak_level), 3) for v in level],
        "_energy": float(np.sum(stereo ** 2)),
        "_active": active,
        "_rms": rms,
    }


def _gate_backing_vocals(metrics: dict, mono: dict) -> None:
    """The backing stem always holds a little lead bleed, and judged against its own loud
    parts that bleed would read as singing. Count it only when it is near the lead's level."""
    backing, lead = metrics.get("backing_vocals"), metrics.get("lead_vocals")
    if not backing or not lead:
        return
    frames = min(len(backing["_rms"]), len(lead["_rms"]))
    near_lead = backing["_rms"][:frames] > lead["_rms"][:frames] * 10 ** (-BACKING_GATE_DB / 20)
    held = uniform_filter1d(near_lead.astype(float), size=int(SR / HOP)) > 0.35
    active = backing["_active"].copy()
    active[:frames] &= held
    backing["_active"] = active
    backing["presence_pct"] = round(float(active.mean()) * 100 if backing["audible"] else 0.0, 1)
    if active.any() and backing["audible"]:
        backing["first_entry"] = round(float(librosa.frames_to_time(int(np.argmax(active)), sr=SR, hop_length=HOP)), 2)
    else:
        backing["first_entry"] = None
        backing["audible"] = False


def _peaks(stereo: np.ndarray, sr: int, per_second: int = 30) -> list[int]:
    """Compact waveform for the timeline: one 0-255 value per bucket."""
    mono = np.abs(stereo).max(axis=0)
    size = max(1, sr // per_second)
    usable = len(mono) - len(mono) % size
    if usable == 0:
        return []
    buckets = mono[:usable].reshape(-1, size).max(axis=1)
    return (np.clip(buckets, 0, 1) * 255).astype(np.uint8).tolist()


def _loudness(mix: np.ndarray, sr: int) -> float | None:
    try:
        import torch
        import torchaudio.functional as F
        return round(float(F.loudness(torch.from_numpy(mix.astype(np.float32)), sr)), 1)
    except Exception:
        return None


# ---------------------------------------------------------------- orchestration

def analyze(stem_paths: dict[str, Path], on_progress: Callable[[float, str], None] | None = None) -> dict:
    report = on_progress or (lambda _p, _m: None)

    report(0.02, "โหลด stem")
    stereo: dict[str, np.ndarray] = {}
    source_sr = 44100
    for name, path in stem_paths.items():
        data, source_sr = sf.read(path, dtype="float32", always_2d=True)
        stereo[name] = data.T

    mix_stereo = sum(stereo.values())
    duration = mix_stereo.shape[1] / source_sr
    mono = {
        name: librosa.resample(s.mean(axis=0), orig_sr=source_sr, target_sr=SR, res_type="soxr_hq")
        for name, s in stereo.items()
    }
    mix = sum(mono.values())

    # Fall back to the mix for rhythm if the drums stem is effectively empty.
    drums = mono.get("drums")
    use_drums = drums is not None and db(float(np.sqrt(np.mean(drums ** 2)))) > -42
    rhythm_y = drums if use_drums else mix
    low_y = (drums if use_drums else 0) + mono.get("bass", mix * 0)
    if not isinstance(low_y, np.ndarray) or np.abs(low_y).max() < EPS:
        low_y = mix

    report(0.15, "หาจังหวะและห้องเพลง")
    rhythm = _estimate_rhythm(rhythm_y, low_y, duration)
    beat_frames = rhythm["frames"]

    report(0.3, "หาคีย์และคอร์ด")
    harmonic_names = [n for n in mono if n not in ("drums", "bass", *VOCAL_STEMS)]
    harmonic = sum((mono[n] for n in harmonic_names), np.zeros_like(mix))
    vocals = sum((mono[n] for n in VOCAL_STEMS if n in mono), np.zeros_like(mix))
    bass = mono.get("bass", np.zeros_like(mix))
    # Normalize before blending so a quiet pad still drives the chord estimate.
    weighted = harmonic / (np.abs(harmonic).max() + EPS)
    chroma = librosa.feature.chroma_cqt(y=weighted + 0.15 * vocals, sr=SR, hop_length=HOP, bins_per_octave=36)
    if np.abs(bass).max() > 1e-3:
        bass_chroma = librosa.feature.chroma_cqt(y=bass, sr=SR, hop_length=HOP, bins_per_octave=36)
        width = min(chroma.shape[1], bass_chroma.shape[1])
        chroma = chroma[:, :width] + 0.7 * bass_chroma[:, :width]

    key = _estimate_key(chroma)
    boundaries = librosa.util.fix_frames(beat_frames, x_min=0, x_max=chroma.shape[1])
    beat_chroma = librosa.util.sync(chroma, boundaries, aggregate=np.median, pad=False)
    beat_times = librosa.frames_to_time(boundaries, sr=SR, hop_length=HOP)
    beat_times[-1] = max(beat_times[-1], duration)

    chords = _estimate_chords(beat_chroma, key)
    refined = _key_from_chords(key, chords)
    if (refined["tonic"], refined["mode"]) != (key["tonic"], key["mode"]):
        key = refined
        chords = _estimate_chords(beat_chroma, key)

    # fix_frames prepends frame 0, so beat index i in beat_times sits one past the raw beats.
    meter = rhythm["meter"]
    lead_in = 1 if boundaries[0] == 0 and (len(beat_frames) == 0 or beat_frames[0] != 0) else 0
    rhythm["downbeat_offset"] = _refine_downbeat(rhythm, chords, lead_in)
    first_bar = (rhythm["downbeat_offset"] + lead_in) % meter
    bar_idx = np.arange(first_bar, len(chords) + 1, meter)
    if len(bar_idx) == 0:
        bar_idx = np.array([0])

    chord_events = []
    for i, chord in enumerate(chords):
        label = _chord_label(chord, key)
        if chord_events and chord_events[-1]["label"] == label:
            chord_events[-1]["end"] = round(float(beat_times[i + 1]), 3)
        else:
            chord_events.append({
                "start": round(float(beat_times[i]), 3),
                "end": round(float(beat_times[i + 1]), 3),
                "label": label,
                "roman": _chord_roman(chord, key),
            })

    bar_chords = []
    for start, end in zip(bar_idx[:-1], bar_idx[1:]):
        bar_chords.append(Counter(chords[start:end]).most_common(1)[0][0])
    loops = _find_loops(bar_chords, key)

    report(0.5, "วัดลักษณะของแต่ละ stem")
    metrics = {name: _stem_metrics(name, stereo[name], mono[name], duration) for name in stereo}
    _gate_backing_vocals(metrics, mono)
    total_energy = sum(m["_energy"] for m in metrics.values()) + EPS
    for m in metrics.values():
        m["energy_share"] = round(m["_energy"] / total_energy * 100, 1)

    report(0.65, "แบ่งท่อนเพลง")
    mfcc = librosa.feature.mfcc(y=mix, sr=SR, hop_length=HOP, n_mfcc=14)[1:]
    mix_rms = librosa.feature.rms(y=mix, frame_length=2048, hop_length=HOP)[0]
    width = min(chroma.shape[1], mfcc.shape[1], len(mix_rms))
    sync_bounds = np.clip(boundaries, 0, width)
    sync_bounds = np.unique(sync_bounds)
    n_beats = len(chords)

    def beat_sync(matrix: np.ndarray) -> np.ndarray:
        out = librosa.util.sync(matrix[:, :width], sync_bounds, aggregate=np.mean, pad=False)
        if out.shape[1] < n_beats:
            out = np.pad(out, ((0, 0), (0, n_beats - out.shape[1])), mode="edge")
        return out[:, :n_beats]

    activity_rows = []
    for name in stereo:
        rms = metrics[name]["_rms"]
        row = np.log1p(rms / (np.percentile(rms, 95) + EPS) * 10).reshape(1, -1)
        activity_rows.append(np.pad(row, ((0, 0), (0, max(0, width - row.shape[1]))))[:, :width])
    activity_matrix = beat_sync(np.vstack(activity_rows))
    timbre = beat_sync(mfcc)
    harmony = beat_sync(chroma)
    rms_beats = beat_sync(mix_rms.reshape(1, -1))[0]

    timbre_z = (timbre - timbre.mean(axis=1, keepdims=True)) / (timbre.std(axis=1, keepdims=True) + EPS)
    harmony_norm = harmony / (np.linalg.norm(harmony, axis=0, keepdims=True) + EPS)
    activity_z = (activity_matrix - activity_matrix.mean(axis=1, keepdims=True)) / (activity_matrix.std(axis=1, keepdims=True) + EPS)

    # The melody separates verse from chorus when both sit on the same chord loop.
    # The lead carries the tune; harmonies would blur which melody a section uses.
    melody_stem = next((n for n in ("lead_vocals", "vocals") if metrics.get(n, {}).get("audible")), None)
    melody_source = mono[melody_stem] if melody_stem else harmonic
    melody_chroma = librosa.feature.chroma_cqt(y=melody_source, sr=SR, hop_length=HOP, bins_per_octave=12, n_octaves=5, fmin=librosa.note_to_hz("C3"))
    if melody_chroma.shape[1] < width:
        melody_chroma = np.pad(melody_chroma, ((0, 0), (0, width - melody_chroma.shape[1])))
    melody = beat_sync(melody_chroma)
    melody_norm = melody / (np.linalg.norm(melody, axis=0, keepdims=True) + EPS)

    level = np.log1p(rms_beats / (np.percentile(rms_beats, 95) + EPS) * 10).reshape(1, -1)
    level_z = (level - level.mean()) / (level.std() + EPS)

    # Average over one four-bar loop so chord changes inside a loop are not read as section changes.
    loop_beats = meter * 4
    stacked = np.vstack([
        uniform_filter1d(timbre_z, size=loop_beats, axis=1) * 0.6,
        uniform_filter1d(activity_z, size=meter, axis=1) * 1.0,
        uniform_filter1d(level_z, size=meter * 2, axis=1) * 1.2,
        uniform_filter1d(harmony_norm, size=loop_beats, axis=1) * 2.0,
        uniform_filter1d(melody_norm, size=loop_beats, axis=1) * 2.0,
    ])
    bar_edges = np.unique(np.concatenate([[0], bar_idx[bar_idx < n_beats], [n_beats]]))
    bounds = _segment(stacked, bar_edges, duration)

    frame_times = librosa.frames_to_time(np.arange(width), sr=SR, hop_length=HOP)
    has_vocals = any(n in metrics and metrics[n]["audible"] and metrics[n]["presence_pct"] > 8 for n in VOCAL_STEMS)

    segments: list[dict] = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b <= a:
            continue
        start, end = float(beat_times[a]), float(beat_times[min(b, len(beat_times) - 1)])
        mask = (frame_times >= start) & (frame_times < end)
        stem_levels = {}
        for name, m in metrics.items():
            active = m["_active"][: width]
            stem_levels[name] = round(float(active[mask[: len(active)]].mean()), 2) if mask.any() and m["audible"] else 0.0
        seg_chords = chords[a:b]
        seg_bars = [c for s, e in zip(bar_idx[:-1], bar_idx[1:]) if a <= s < b for c in [Counter(chords[s:e]).most_common(1)[0][0]]]
        loop = _find_loops(seg_bars, key, top=1)
        vector = np.concatenate([
            harmony_norm[:, a:b].mean(axis=1),
            melody_norm[:, a:b].mean(axis=1),
            timbre_z[:, a:b].mean(axis=1) * 0.7,
            activity_matrix[:, a:b].mean(axis=1),
            [float(level_z[0, a:b].mean())],
        ])
        segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "bars": max(1, round((b - a) / meter)),
            "energy_db": round(db(float(np.mean(rms_beats[a:b]))), 1),
            "vocal_presence": max(stem_levels.get(n, 0.0) for n in VOCAL_STEMS),
            "backing_presence": stem_levels.get("backing_vocals", 0.0),
            "stems": stem_levels,
            "unique_chords": len({c for c in seg_chords if c}),
            "loop": loop[0] if loop else None,
            "vector": vector,
        })

    report(0.85, "ตั้งชื่อท่อน")
    _label_sections(segments, has_vocals)

    # Merge adjacent same-label sections, then number the repeats (Verse 1, Verse 2).
    merged: list[dict] = []
    for segment in segments:
        previous = merged[-1] if merged else None
        if previous and previous["label"] == segment["label"]:
            total = previous["end"] - previous["start"] + segment["end"] - segment["start"]
            first_weight = (previous["end"] - previous["start"]) / (total + EPS)
            previous["energy_db"] = round(previous["energy_db"] * first_weight + segment["energy_db"] * (1 - first_weight), 1)
            for name in previous["stems"]:
                previous["stems"][name] = round(previous["stems"][name] * first_weight + segment["stems"][name] * (1 - first_weight), 2)
            previous["end"] = segment["end"]
            previous["bars"] += segment["bars"]
            previous["loop"] = previous["loop"] or segment["loop"]
        else:
            merged.append(segment)
    totals = Counter(s["label"] for s in merged)
    seen: Counter = Counter()
    for segment in merged:
        seen[segment["label"]] += 1
        segment["kind"] = segment["label"]
        if totals[segment["label"]] > 1 and segment["label"] not in ("Intro", "Outro") and not segment["label"].startswith("Theme"):
            segment["label"] = f"{segment['label']} {seen[segment['label']]}"
        for private in ("vector", "cluster", "backing_presence"):
            segment.pop(private, None)

    report(0.92, "สรุปผล")
    lufs = _loudness(mix_stereo, source_sr)
    peak = float(np.abs(mix_stereo).max())
    rms_total = float(np.sqrt(np.mean(mix_stereo ** 2)))

    beats_output = [round(float(t), 3) for t in rhythm["times"]]
    downbeats = beats_output[rhythm["downbeat_offset"]::meter]

    key_name = f"{_pitch_name(key['tonic'], key)} {key['mode']}"
    camelot = (CAMELOT_MAJOR if key["mode"] == "major" else CAMELOT_MINOR)[key["tonic"]]
    alternatives = []
    for _score, tonic, mode in key.get("alternatives", []):
        if (tonic, mode) != (key["tonic"], key["mode"]):
            alternatives.append(f"{_pitch_name(tonic, {'tonic': tonic, 'mode': mode})} {mode}")

    stems_output = []
    for name in stereo:
        m = metrics[name]
        public = {k: v for k, v in m.items() if not k.startswith("_")}
        public["peaks"] = _peaks(stereo[name], source_sr)
        stems_output.append(public)

    sections_of_tempo = tempo_map(beats_output, downbeats, meter)
    overview = tempo_summary(sections_of_tempo)
    result = {
        "version": ANALYSIS_VERSION,
        "duration": round(duration, 3),
        "tempo": {
            # With more than one tempo, the headline is the one most of the song uses.
            "bpm": overview["main"] if overview["main"] is not None else rhythm["bpm"],
            "stability": rhythm["stability"],
            "reliable": rhythm["reliable"],
            "source": "drums" if use_drums else "mix",
            "beats": beats_output,
            "downbeats": downbeats,
            "varying": bool(rhythm.get("varying")),
            "map": sections_of_tempo,
            "range": [overview["low"], overview["high"]],
        },
        "meter": f"{meter}/4",
        "key": {
            "name": key_name,
            "tonic": _pitch_name(key["tonic"], key),
            "mode": key["mode"],
            "camelot": camelot,
            "confidence": key["confidence"],
            "alternatives": alternatives[:2],
        },
        "chords": chord_events,
        "loops": loops,
        "sections": merged,
        "stems": stems_output,
        "loudness": {
            "lufs": lufs,
            "rms_db": round(db(rms_total), 1),
            "peak_db": round(db(peak), 1),
            "crest_db": round(db(peak) - db(rms_total), 1),
        },
    }
    result["insights"] = _insights(result)
    report(1.0, "เสร็จแล้ว")
    return result


# ---------------------------------------------------------------- plain-language summary

def _insights(r: dict) -> list[dict]:
    """Turn the measurements into statements a musician can act on. Each one cites
    numbers already shown in the UI; nothing here is a claim beyond the data."""
    out: list[dict] = []
    tempo = r["tempo"]
    bpm = tempo["bpm"]
    feel = "ช้า" if bpm < 80 else "กลางๆ" if bpm < 112 else "ค่อนข้างเร็ว" if bpm < 140 else "เร็ว"
    if not tempo["reliable"]:
        out.append({"topic": "จังหวะ", "text": "จับจังหวะได้ไม่ชัด เพลงอาจไม่มีกลองหรือเล่นแบบไม่คงที่ ค่า BPM เป็นค่าโดยประมาณ และเมโทรนอมอาจไม่ตรงกับเพลง"})
    elif len(tempo.get("map", [])) > 1:
        parts = []
        for section in tempo["map"]:
            span = f"{fmt_time(section['start'])}–{fmt_time(section['end'])}"
            if section["kind"] == "steady":
                parts.append(f"{span} {section['bpm']:.0f} BPM")
            else:
                word = "เร็วขึ้น" if section["kind"] == "accel" else "ช้าลง"
                parts.append(f"{span} {word} {section['from']:.0f}→{section['to']:.0f}")
        out.append({"topic": "จังหวะ", "text": f"เพลงนี้ใช้มากกว่าหนึ่งความเร็ว ({len(tempo['map'])} ช่วง): " + " · ".join(parts)})
    elif tempo["stability"] > 0.85:
        out.append({"topic": "จังหวะ", "text": f"{bpm:.0f} BPM จังหวะ{feel} และนิ่งตลอดเพลง ลักษณะนี้มักเกิดจากการอัดตาม click หรือทำในคอมพิวเตอร์"})
    else:
        out.append({"topic": "จังหวะ", "text": f"{bpm:.0f} BPM จังหวะ{feel} และแกว่งเล็กน้อย ซึ่งพบได้บ่อยในเพลงที่เล่นสดโดยไม่มี click เมโทรนอมจึงขยับตามเพลง ไม่ได้คงที่เหมือนเครื่องเคาะจังหวะ"})

    key_text = f"คีย์ {r['key']['name']}"
    if r["key"]["confidence"] < 0.5 and r["key"]["alternatives"]:
        key_text += f" (อาจเป็น {r['key']['alternatives'][0]} ก็ได้ เพราะใช้โน้ตใกล้เคียงกันมาก)"
    if r["loops"]:
        loop = r["loops"][0]
        out.append({"topic": "ฮาร์โมนี", "text": f"{key_text} ลูปคอร์ดที่พบบ่อยสุดคือ {' – '.join(loop['chords'])} ({' – '.join(loop['romans'])}) พบ {loop['count']} ครั้ง"})
    else:
        out.append({"topic": "ฮาร์โมนี", "text": f"{key_text} ไม่พบลูปคอร์ด 4 ห้องที่วนซ้ำชัดเจน"})

    sections = r["sections"]
    if len(sections) > 1:
        out.append({"topic": "โครงสร้าง", "text": "ลำดับท่อน: " + " → ".join(s["label"] for s in sections)})
        hook = next((s for s in sections if s["kind"] == "Chorus"), None)
        if hook:
            out.append({"topic": "โครงสร้าง", "text": f"ฮุกแรกมาที่ {fmt_time(hook['start'])} หรือ {hook['start'] / max(r['duration'], 1) * 100:.0f}% ของเพลง"})
        loudest = max(sections, key=lambda s: s["energy_db"])
        quietest = min(sections, key=lambda s: s["energy_db"])
        out.append({"topic": "พลังงาน", "text": f"ท่อนที่ดังสุดคือ {loudest['label']} ({fmt_time(loudest['start'])}) ต่างจากท่อนเบาสุด {quietest['label']} {loudest['energy_db'] - quietest['energy_db']:.1f} dB"})

    entries = sorted((s for s in r["stems"] if s["first_entry"] is not None), key=lambda s: s["first_entry"])
    if len(entries) > 1:
        order = " → ".join(f"{s['name']} {fmt_time(s['first_entry'])}" for s in entries)
        out.append({"topic": "การเรียบเรียง", "text": f"ลำดับการเข้าของเครื่องดนตรี: {order}"})

    backing = next((s for s in r["stems"] if s["id"] == "backing_vocals"), None)
    if backing is not None:
        if backing["audible"] and backing["presence_pct"] >= 3 and len(sections) > 1:
            busiest = max(sections, key=lambda s: s["stems"].get("backing_vocals", 0))
            out.append({"topic": "เสียงร้อง", "text": f"เสียงคอรัสมีประมาณ {backing['presence_pct']:.0f}% ของเพลง และหนาที่สุดในท่อน {busiest['label']} ({fmt_time(busiest['start'])})"})
        else:
            out.append({"topic": "เสียงร้อง", "text": "แทบไม่พบเสียงคอรัสในเพลงนี้ เสียงร้องส่วนใหญ่อยู่ในแทร็กเสียงร้องหลัก"})

    audible = [s for s in r["stems"] if s["audible"]]
    if audible:
        top = max(audible, key=lambda s: s["energy_share"])
        widest = max(audible, key=lambda s: s["width_pct"])
        narrowest = min(audible, key=lambda s: s["width_pct"])
        if widest["width_pct"] < 5:
            stereo_text = "ทุกแทร็กอยู่ตรงกลาง ไฟล์นี้แทบเป็นโมโน"
        else:
            stereo_text = f"{widest['name']} กระจายกว้างสุดในสเตอริโอ ({widest['width_pct']:.0f}%) และ {narrowest['name']} อยู่ตรงกลางสุด ({narrowest['width_pct']:.0f}%)"
        out.append({"topic": "มิกซ์", "text": f"{top['name']} มีพลังงานมากสุด ({top['energy_share']:.0f}%) {stereo_text}"})

    lufs = r["loudness"]["lufs"]
    if lufs is not None:
        character = "ดังและอัดแน่นมาก" if lufs > -9 else "ดังระดับเพลงเชิงพาณิชย์" if lufs > -13 else "ยังมีช่วงไดนามิกเหลือพอสมควร"
        out.append({"topic": "ความดัง", "text": f"ความดังรวม {lufs:.1f} LUFS {character}"})
    return out

"""A Standard MIDI File of the metronome, so a DAW's bar grid follows the song.

Track 1 carries the tempo map (one tempo per beat, so speed-ups and slow-downs are
exact), time signatures and section markers. Track 2 has a note on every beat, accent on
the bar, to check the grid against the audio or to trigger a click sound of one's own.

Times are on the playback timeline: 0 is the first sample of every exported track, so
the MIDI file and the WAVs line up when all are placed at the start of a DAW session.
"""

from __future__ import annotations

import struct

PPQ = 960                      # ticks per quarter note
MAX_TEMPO_US = 0xFFFFFF        # the largest tempo a MIDI file can store (about 3.6 BPM)
CLICK_CHANNEL = 9              # channel 10, General MIDI drums
ACCENT_NOTE = 76               # Hi Wood Block
BEAT_NOTE = 77                 # Low Wood Block
NOTE_TICKS = PPQ // 4
MAX_IMPORT_BPM = 990           # GarageBand and Logic accept up to 990 BPM


def _vlq(value: int) -> bytes:
    """Variable-length quantity, as MIDI writes delta times and lengths."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def _text(value: str, limit: int = 120) -> bytes:
    return value.encode("utf-8")[:limit].decode("utf-8", "ignore").encode("utf-8")


def _track(events: list[tuple[int, int, bytes]]) -> bytes:
    """Events are (tick, order, bytes); order keeps note-offs before note-ons on one tick."""
    body = bytearray()
    now = 0
    for tick, _, data in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - now) + data
        now = tick
    body += _vlq(0) + _meta(0x2F, b"")  # end of track
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _lead_beats(first: float, period: float, before_bar: int, meter: int) -> int:
    """How many beats fill the time before the first beat. Preferably a count that puts
    the song's first bar line on a bar of the DAW's grid with no change of time signature,
    since GarageBand keeps one signature for the whole project; the tempo there is then
    whatever fits. Only when that tempo would be too fast for a DAW does the count follow
    the opening tempo, and a short first bar (set by the caller) keeps the bars aligned."""
    if first <= 0.001:
        return 0
    wanted = first / period
    step = (-before_bar) % meter
    candidates = [n for n in range(step, int(wanted) + 2 * meter + 1, meter)
                  if n >= 1 and 60 * n / first <= MAX_IMPORT_BPM]
    if candidates:
        return min(candidates, key=lambda n: abs(n - wanted))
    return max(1, round(wanted))


def tempo_map(grid: dict, sections: list[dict], title: str, steady_bpm: float | None = None) -> bytes:
    """Build the MIDI file from a timeline grid ({beats, downbeats, meter}, seconds).
    With `steady_bpm` (a tempo-locked song) the beats are taken as exactly that tempo, so
    the millisecond rounding of the grid does not turn into tempo flicker in the DAW."""
    beats = sorted(float(b) for b in grid["beats"])
    if steady_bpm and len(beats) > 1:
        exact = [beats[0] + i * 60.0 / steady_bpm for i in range(len(beats))]
        if max(abs(a - b) for a, b in zip(exact, beats)) < 0.002:
            beats = exact
    meter = int(grid["meter"])
    downs = {round(float(d), 3) for d in grid["downbeats"]}
    if len(beats) < 2:
        raise ValueError("ต้องมีอย่างน้อยสองจังหวะ")
    bar_index = [i for i, b in enumerate(sorted(float(x) for x in grid["beats"])) if round(b, 3) in downs]
    first_bar = bar_index[0] if bar_index else 0
    bar_set = set(bar_index)
    period = beats[1] - beats[0]
    lead = _lead_beats(beats[0], period, first_bar, meter)

    # Beat positions in microseconds, the lead-in beats spread evenly before the first.
    points = [beats[0] * k / lead for k in range(lead)] if lead else []
    points_us = [round(t * 1e6) for t in [*points, *beats]]

    def signature(numerator: int) -> bytes:
        return _meta(0x58, bytes([min(numerator, 255), 2, 24, 8]))  # n/4, 24 clocks a click

    conductor: list[tuple[int, int, bytes]] = [(0, 0, _meta(0x03, _text(title or "Tempo map")))]
    pickup = (lead + first_bar) % meter
    if pickup:  # the song starts at 0 with beats before its first bar line
        conductor.append((0, 0, signature(pickup)))
        conductor.append((pickup * PPQ, 0, signature(meter)))
    else:
        conductor.append((0, 0, signature(meter)))
    # Rounding each beat from the running total keeps the error under a microsecond.
    for i in range(len(points_us) - 1):
        tempo = min(max(points_us[i + 1] - points_us[i], 1), MAX_TEMPO_US)
        conductor.append((i * PPQ, 1, _meta(0x51, tempo.to_bytes(3, "big"))))

    # A bar that is not `meter` beats long gets its own time signature, so the bar lines
    # after it still fall on the song's downbeats.
    current = meter
    for a, b in zip(bar_index, [*bar_index[1:], None]):
        length = (b - a) if b is not None else meter
        if length % meter == 0:
            length = meter
        if length != current:
            conductor.append(((lead + a) * PPQ, 0, signature(length)))
            current = length

    # Section names as markers, placed on the beat nearest their start.
    def tick_at(seconds: float) -> int:
        us = seconds * 1e6
        if us <= points_us[0]:
            return 0
        for i in range(len(points_us) - 1):
            if us < points_us[i + 1]:
                return round((i + (us - points_us[i]) / (points_us[i + 1] - points_us[i])) * PPQ)
        last = points_us[-1] - points_us[-2]
        return round((len(points_us) - 1 + (us - points_us[-1]) / last) * PPQ)

    for section in sections:
        label = str(section.get("label") or "").strip()
        if label:
            tick = round(tick_at(float(section["start"])) / PPQ) * PPQ
            conductor.append((tick, 2, _meta(0x06, _text(label))))

    clicks: list[tuple[int, int, bytes]] = [(0, 0, _meta(0x03, b"Click"))]
    for i, beat in enumerate(beats):
        tick = (lead + i) * PPQ
        accent = i in bar_set
        note, velocity = (ACCENT_NOTE, 110) if accent else (BEAT_NOTE, 80)
        clicks.append((tick, 1, bytes([0x90 | CLICK_CHANNEL, note, velocity])))
        clicks.append((tick + NOTE_TICKS, 0, bytes([0x80 | CLICK_CHANNEL, note, 0])))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, 2, PPQ)
    return header + _track(conductor) + _track(clicks)

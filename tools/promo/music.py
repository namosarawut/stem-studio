"""Original background track for the Prism promo: 100 BPM, A minor, 31 bars + tail.
Everything is synthesised here (no samples), so it is free to use.
Usage: python music.py out.wav"""
import sys

import numpy as np
import soundfile as sf
from scipy.signal import butter, lfilter

SR = 44100
BPM = 100
BEAT = 60 / BPM
BAR = BEAT * 4
BARS = 31
TOTAL = BAR * BARS + 2.5
N = int(TOTAL * SR)
rng = np.random.default_rng(7)

out = np.zeros((2, N))


def place(sig, t, gain=1.0, pan=0.0):
    i = int(t * SR)
    if i >= N:
        return
    sig = sig[: N - i]
    left, right = np.sqrt((1 - pan) / 2), np.sqrt((1 + pan) / 2)
    out[0, i:i + len(sig)] += sig * gain * left
    out[1, i:i + len(sig)] += sig * gain * right


def lp(x, hz, order=2):
    b, a = butter(order, min(hz, SR / 2 - 100) / (SR / 2), "low")
    return lfilter(b, a, x)


def hp(x, hz, order=2):
    b, a = butter(order, hz / (SR / 2), "high")
    return lfilter(b, a, x)


def env(n, a, d, s=0.0, r=0.0, hold=None):
    t = np.arange(n) / SR
    e = np.ones(n)
    att = t < a
    e[att] = t[att] / max(a, 1e-4)
    rest = ~att
    e[rest] = s + (1 - s) * np.exp(-(t[rest] - a) / max(d, 1e-4))
    return e


def midi(m):
    return 440 * 2 ** ((m - 69) / 12)


# A minor: Am | F | C | G  (roots A2 F2 C3 G2)
CHORDS = [(45, [57, 60, 64]), (41, [57, 60, 65]), (48, [55, 60, 64]), (43, [55, 59, 62])]

# Sections (bar ranges) follow the video's scenes.
INTRO = range(0, 2)        # logo
HOOK = range(2, 4)         # build
MAIN = range(4, 23)        # features
BREAK = range(23, 27)      # setlist, a little lighter
OUTRO = range(27, 31)      # end card


def kick():
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    f = 45 + 85 * np.exp(-t / 0.045)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.tanh(1.6 * np.sin(ph) * np.exp(-t / 0.16))


def clap():
    n = int(0.25 * SR)
    noise = rng.standard_normal(n)
    b, a = butter(2, [900 / (SR / 2), 4200 / (SR / 2)], "band")
    body = lfilter(b, a, noise)
    e = env(n, 0.002, 0.07)
    # three quick hits make it a clap
    for d in (0.012, 0.024):
        k = int(d * SR)
        e[k:] += 0.5 * env(n - k, 0.001, 0.02)
    return body * e * 0.9


def hat(open_=False):
    n = int((0.22 if open_ else 0.05) * SR)
    x = hp(rng.standard_normal(n), 7000)
    return x * env(n, 0.001, 0.09 if open_ else 0.018) * 0.5


def saw(freq, n, detune=0.0):
    t = np.arange(n) / SR
    ph = (freq * (1 + detune) * t) % 1.0
    return 2 * ph - 1


def pad(notes, length, cutoff):
    n = int(length * SR)
    x = np.zeros(n)
    for m in notes:
        for d in (-0.006, 0.0, 0.007):
            x += saw(midi(m), n, d)
    x = lp(x / (len(notes) * 3), cutoff)
    return x * env(n, 0.35, 10, s=1.0) * np.minimum(1, (n - np.arange(n)) / (0.25 * SR))


def bass(m, length):
    n = int(length * SR)
    t = np.arange(n) / SR
    f = midi(m)
    x = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t)
    return np.tanh(1.4 * x) * env(n, 0.005, 0.22, s=0.35) * np.minimum(1, (n - np.arange(n)) / (0.02 * SR))


def pluck(m, length):
    n = int(length * SR)
    x = saw(midi(m), n) * 0.6 + saw(midi(m + 12), n, 0.004) * 0.25
    return lp(x, 2600) * env(n, 0.003, 0.12)


def riser(length):
    n = int(length * SR)
    x = rng.standard_normal(n)
    out_ = np.zeros(n)
    steps = 40
    for i in range(steps):
        a, b_ = i * n // steps, (i + 1) * n // steps
        out_[a:b_] = hp(x[a:b_], 300 + 6000 * (i / steps) ** 2)
    return out_ * np.linspace(0, 1, n) ** 2 * 0.35


kick_s, clap_s = kick(), clap()
duck = np.ones(N)  # sidechain envelope from the kick

for bar in range(BARS):
    t0 = bar * BAR
    root, notes = CHORDS[bar % 4]
    full = bar in MAIN or bar in HOOK
    # pad, opening its filter through the intro
    cutoff = 700 + 2600 * min(1, bar / 4) if bar not in OUTRO else 1800 - 300 * (bar - 27)
    place(pad(notes, BAR + 0.3, cutoff), t0, 0.32 if bar not in OUTRO else 0.36, pan=0.0)
    if bar in INTRO:
        for s in range(8):
            place(hat(), t0 + s * BEAT / 2 + (0.02 if s % 2 else 0), 0.35, pan=0.3)
        continue
    beats = range(4)
    for b in beats:
        tb = t0 + b * BEAT
        if bar in HOOK and b in (1, 3) and bar == 2:
            continue
        if bar not in OUTRO or b == 0:
            place(kick_s, tb, 0.95)
            k = int(tb * SR)
            span = int(0.22 * SR)
            duck[k:k + span] = np.minimum(duck[k:k + span], 0.45 + 0.55 * np.linspace(0, 1, len(duck[k:k + span])))
        if b in (1, 3) and (full or bar in BREAK):
            place(clap_s, tb, 0.55 if bar not in BREAK else 0.4, pan=-0.05)
    if bar in OUTRO:
        continue
    for s in range(16):
        ts = t0 + s * BEAT / 4 + (0.018 if s % 2 else 0)
        if s % 2 == 0 or bar in MAIN:
            place(hat(open_=(s % 8 == 6)), ts, 0.28 if s % 4 else 0.38, pan=0.25)
    # bass: eighths on the root, octave pop on the last
    for s in range(8):
        m = root - 12 + (12 if s == 7 else 0)
        place(bass(m + 12, BEAT / 2 * 0.95), t0 + s * BEAT / 2, 0.5 if full else 0.4)
    # pluck arpeggio in the feature section
    if bar in MAIN and bar >= 6:
        arp = notes + [notes[1] + 12]
        for s in range(16):
            if s % 3 == 2:
                continue
            place(pluck(arp[s % 4] + 12, BEAT / 4 * 0.9), t0 + s * BEAT / 4, 0.16, pan=-0.35 if s % 2 else 0.35)

place(riser(BAR * 2), 2 * BAR, 1.0)
place(riser(BAR), 22 * BAR, 0.8)
# a cymbal-ish wash on the drop and on the end card
for t in (4 * BAR, 27 * BAR):
    n = int(2.4 * SR)
    place(hp(rng.standard_normal(n), 5000) * env(n, 0.002, 0.9) * 0.3, t, 1.0)

# the pad and bass breathe with the kick
out *= duck[None, :] ** 0.6 * 0.4 + 0.6
mix = np.tanh(out * 1.2)
fade = int(2.0 * SR)
mix[:, -fade:] *= np.linspace(1, 0, fade)
mix[:, : int(0.02 * SR)] *= np.linspace(0, 1, int(0.02 * SR))
mix = mix / np.max(np.abs(mix)) * 0.89
sf.write(sys.argv[1], mix.T.astype(np.float32), SR, subtype="PCM_16")
print(f"{TOTAL:.1f}s written")

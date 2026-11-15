# Third-party notices

The Prism source code in this repository is released under the MIT License (see `LICENSE`).
Prism does **not** copy or bundle the packages or models below. `start.command` installs the
packages from PyPI into a local `.venv`, and the app downloads model weights on first use into
`models/`. Each one stays under its own licence. If you redistribute a build that includes them
(an installer, a disk image, a container), you must follow those licences, including keeping their
copyright notices.

Licences below were read from the installed package metadata. Check the project's own page before
relying on this table for a commercial release.

## Python packages (from `requirements.txt`)

| Package | Licence |
|---|---|
| demucs 4.0.1 (Meta AI) | MIT |
| audio-separator 0.45.0 | MIT |
| torch, torchaudio | BSD-3-Clause |
| librosa | ISC |
| numpy, scipy | BSD-3-Clause |
| soundfile | BSD-3-Clause |
| fastapi, pydantic | MIT |
| uvicorn, starlette, psutil | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| imageio-ffmpeg | BSD-2-Clause (see ffmpeg below) |

Pulled in by those packages: onnxruntime (MIT), julius, einops, PyYAML, dora-search, openunmix
(MIT), numba (BSD), and **lameenc (LGPL-3.0-or-later)**, which Demucs uses for MP3 output. lameenc
is installed as a separate package and is not modified or linked into Prism's own code. If you ship
it inside a bundled app, follow the LGPL (provide its source and allow replacing it).

## ffmpeg

`imageio-ffmpeg` downloads a prebuilt ffmpeg binary that is compiled with `--enable-gpl`
(GPL-2.0-or-later). Prism calls it as a separate program to decode audio, so Prism's own code is
not affected. If you bundle that binary in a distributed app you must comply with the GPL for it
(offer its source). Alternatives: ship an LGPL-only ffmpeg build with just the audio decoders, or
let each user install ffmpeg themselves.

## Model weights

| Model | Source | Terms |
|---|---|---|
| `htdemucs`, `htdemucs_ft`, `htdemucs_6s` | Meta AI, Demucs | Code and weights released under MIT. The models were trained on MUSDB18-HQ, whose data is licensed for research use. Get legal advice before selling a product built on them. |
| `mel_band_roformer_karaoke_aufr33_viperx` (lead / backing vocals mode) | Community model by aufr33 and viperx, fetched through audio-separator | **No licence found** in the package or the model file. Treat this mode as experimental, for personal use. Do not redistribute or sell it without asking the authors. |

## Cue voices

The spoken cues and count-ins are generated with the macOS `say` command and the voices installed
on the user's Mac. Apple's licence for the system voices allows personal, non-commercial use. If you
use the generated cue audio in paid work, or redistribute it, check that licence or replace the
voices with your own recordings. (Planned: see `docs/ROADMAP.md`.)

On Windows the cues use the voices installed in Windows (through `app/winspeech.ps1`), which are
covered by the Windows licence. The same advice applies: check it before using the cue audio in paid
work or redistributing it.

## Your own audio

Prism processes the files you give it. Make sure you have the right to use the songs, and keep
in mind that stems, cuts and exports made from someone else's recording are still derived from it.

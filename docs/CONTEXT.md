# Prism — คู่มือส่งต่องานสำหรับ agent

เอกสารนี้ให้ agent (หรือคนใหม่) อ่านจบแล้วทำงานต่อได้โดยไม่ต้องถามว่า "โปรเจกต์นี้คืออะไร ทำมาถึงไหน ห้ามทำอะไร"
อ่านตามลำดับ: §0 ก่อนแตะอะไรก็ตาม → §1–2 เพื่อเข้าใจว่าทำเพื่อใคร → §3–6 เพื่อรู้โครงสร้าง → §8 ก่อนแก้โค้ด → §10 เพื่อเลือกงานต่อ

สถานะ ณ วันที่ 13 พฤศจิกายน 2026: ประวัติ `main` ถูกเขียนใหม่เพื่อลบบรรทัด Co-Authored-By (hash เปลี่ยนทั้งหมด), working tree สะอาด, repo `namosarawut/stem-studio` ยังเป็น **private**

ไฟล์ที่เกี่ยวข้อง: [CLAUDE.md](../CLAUDE.md) (สรุปสั้นที่ Claude Code โหลดอัตโนมัติ), [ROADMAP.md](ROADMAP.md) (งานที่ยังไม่ทำ), [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), [tools/promo/README.md](../tools/promo/README.md)

---

## 0. กฎเหล็ก (อ่านก่อนทำอะไร)

1. **`data/` คือเพลงจริงของผู้ใช้** (ประมาณ 33 เพลง, 31 GB, มี cue/เมโทรนอมที่แก้มือ และเพลย์ลิสต์) ห้ามลบ แก้ ย้าย หรือ "ล้างของที่ไม่รู้จัก" โดยไม่ได้รับอนุญาตชัดเจนจากผู้ใช้ เคยพลาดลบงานของผู้ใช้มาแล้วครั้งหนึ่ง
2. **ทดสอบบนข้อมูลสำเนาเสมอ** บนพอร์ต 8766 (วิธีใน §9) เซิร์ฟเวอร์จริงอยู่พอร์ต 8765 และผู้ใช้อาจเปิดใช้งานอยู่
3. **รีสตาร์ตเซิร์ฟเวอร์จริงเฉพาะตอนที่ `/api/jobs` ไม่มีงาน `queued` หรือ `running`** ไม่งั้นงานแยกเสียงที่กำลังทำจะถูกตัดและกลายเป็น error (`restore_jobs` ทำเครื่องหมายว่า "ถูกขัดจังหวะ")
4. **ห้าม commit/push จนกว่าผู้ใช้สั่ง** ห้ามเอา `data/`, `models/`, `.venv/`, `.claude/` เข้า git (เคยเผลอ commit เสียง 14 GB แล้วต้องล้างประวัติ) ก่อน commit ดู `git status` ทุกครั้ง และเพิ่มไฟล์ทีละชื่อ ไม่ใช้ `git add -A` **ข้อความ commit ห้ามมีบรรทัดอ้างถึง AI** (`Co-Authored-By: Claude …`, "Generated with Claude Code") ผู้ใช้สั่งชัด และเคยต้องเขียนประวัติใหม่เพื่อลบออก
5. **ตอบผู้ใช้เป็นภาษาไทย** ภาษาพูดธรรมชาติ ไม่เป็นทางการ ไม่ใช้สำนวนที่ฟังเหมือน AI เขียน (ผู้ใช้ขอแก้เรื่องนี้ซ้ำหลายรอบ ทั้งข้อความ UI สไลด์ โพสต์ และบทพากย์) ข้อความ UI ใหม่ก็ใช้แนวเดียวกัน
6. **ก่อนแก้ cue/เมโทรนอม/เพลย์ลิสต์ที่ผู้ใช้วางไว้เอง ให้ถามก่อน** งานของ Prism คือแก้ละเอียดด้วยมือ การเขียนทับถือเป็นความเสียหาย
7. **ไม่ใส่ข้อมูลส่วนตัว** (อีเมล ชื่อจริง) ลงโค้ด เอกสาร หรือ commit ลิขสิทธิ์ใน LICENSE ใช้ชื่อ GitHub `namosarawut`
8. **อย่าอ้างว่าเสร็จถ้ายังไม่ได้ลองจริง** โปรเจกต์นี้ไม่มี test suite อัตโนมัติ ต้องรันตรวจเอง (§9) และรายงานตามจริงว่าตรวจอะไรแล้วและอะไรยัง

---

## 1. Prism คืออะไร

เว็บแอปที่รันบน Mac ของผู้ใช้เอง (ไม่อัปโหลดอะไรขึ้นเน็ต) สำหรับนักดนตรีที่ต้อง **เตรียม data ให้วงเล่นสด / เป็น sound MD**:

- แยกเพลงเป็น stem ด้วย AI (Demucs; แยกร้องนำ/คอรัสด้วย MelBand Roformer)
- วิเคราะห์เพลง: BPM (รองรับเพลงที่ความเร็วเปลี่ยนกลางเพลง), คีย์, คอร์ด, ท่อนเพลง, loop
- แทร็กคลิก (เมโทรนอม) ที่ตามเพลงและแก้ละเอียดได้
- แทร็ก cue: เสียงพูดบอกท่อน (135 วลี) และนับเข้า 7 แบบ ลงตรงบีต
- ห้องนับก่อนเพลง (lead-in): UI ให้เลือก 1, 2, 3, 4 หรือ 8 ห้อง (API รับ 0–16 ตาม `MAX_LEAD_BARS`) ในสื่อโปรโมตเขียนว่า "1–8 ห้อง"
- ตัดช่วงเสียงไปเป็นแทร็กใหม่ (เช่น ดึงคอรัสท่อนฮุก) แบบย้อนกลับได้
- กำหนดต่อแทร็กว่าออกลำโพงซ้าย/ขวา/กลาง (ส่งคลิกเข้า in-ear ข้างหนึ่ง ดนตรีอีกข้าง)
- export ทุกแทร็กยาวเท่ากัน วางที่ 0 ใน DAW แล้วตรงกัน
- **เพลย์ลิสต์ (setlist player)** เล่นเพลงต่อกันแบบไม่มีช่องว่าง ตั้งจุดเริ่ม/จบรายเพลง นำเข้าไฟล์จากเครื่องหรือดึงจากโปรเจกต์ที่ทำไว้

ชื่อ: เดิมคือ "Stem Studio" เปลี่ยนเป็น **Prism** (ผู้ใช้เลือกเอง) โฟลเดอร์/repo/env var/localStorage key ยังใช้ชื่อเดิม **อย่าเปลี่ยน** (§8)

## 2. ผู้ใช้และบริบทการใช้งาน

- เจ้าของโปรเจกต์คือนักดนตรี/คนทำ data ชาวไทย ใช้ MacBook Pro M3 Pro 36 GB (พัฒนาและทดสอบ) ผู้ใช้ปลายทางคือเพื่อนร่วมวงการ ซึ่งอาจใช้ M1 8 GB
- เป้าหมายปัจจุบัน: **แจกฟรี เปิดโค้ดให้ต่อยอด (MIT)** แล้วโพสต์แนะนำในกลุ่มนักดนตรี
- ตัวอย่างเพลงที่ใช้ทดสอบหลักคือ "Circle" (เพลงที่ความเร็วเปลี่ยน) ภาพโปรโมตยังมีชื่อเพลงนี้ ถ้าเผยแพร่วงกว้างควรทำใหม่ด้วยเพลงที่มีสิทธิ์
- ผู้ใช้ไม่ใช่โปรแกรมเมอร์เต็มตัว ชอบให้ลงมือทำให้เลย แล้วรายงานสั้นๆ ว่าอะไรเปลี่ยน ถามเมื่อมีทางเลือกที่ผลต่างกันจริงเท่านั้น

## 3. สถาปัตยกรรม

```
Browser (vanilla JS, ไม่มี build)  ──HTTP──▶  FastAPI (app/main.py, 127.0.0.1:8765)
  static/app.js, playlist.js                     ├─ worker thread: คิวแยกเสียง (ทีละงาน)
  Web Audio API เล่น/ผสมเสียง                    ├─ analyzer / tempo / project / cues / playlists
                                                 └─ ffmpeg (imageio-ffmpeg), `say` (macOS)
ดิสก์: data/{jobs,library,playlists}, models/
```

- เซิร์ฟเวอร์ผูกเฉพาะ localhost และ middleware `local_only` ตรวจทั้ง `Host` และ `Origin` (กัน DNS rebinding / เว็บอื่นยิงมาที่เครื่องผู้ใช้) **อย่าเอาออก**
- งานแยกเสียงเข้าคิว `work_queue` ทำทีละงานใน thread `stem-worker` สถานะอยู่ใน dict `jobs` + `meta.json`
- อุปกรณ์: `separator.pick_device()` เลือก MPS (Apple GPU) ถ้ามี, ตั้ง `PYTORCH_ENABLE_MPS_FALLBACK=1`
- ตัวแก้ไข (`edits.json`) แยกจากไฟล์ต้นฉบับ: **stem ที่แยกแล้วใน `stems/` ไม่ถูกแก้เลย** ทุกการตัด/ห้องนับเรนเดอร์เป็นไฟล์ใหม่ใน `edited/` และ `padded/` แล้วย้อนได้

## 4. โครงไฟล์

| ไฟล์ | บรรทัด | หน้าที่ |
|---|---|---|
| `app/main.py` | ~800 | แอป FastAPI, middleware, คิวงาน, `process()`, endpoint ทั้งหมด, mount static |
| `app/separator.py` | ~245 | `MODELS`, `decode_audio` (ffmpeg → 44.1 kHz), `separate`, `split_vocals` (Roformer) |
| `app/analyzer.py` | ~985 | `analyze()`: จังหวะ, คีย์, คอร์ด, ท่อน, loop, loudness, สถิติรายแทร็ก; `ANALYSIS_VERSION = 2` |
| `app/tempo.py` | ~157 | `tempo_map`: แบ่งช่วงความเร็ว (steady / accel / ritard) และ `summary` |
| `app/project.py` | ~410 | โมเดลการแก้ไข: click grid, cuts, custom tracks, lead-in, `merged_view` ให้ UI |
| `app/cues.py` | ~350 | ไลบรารีวลี, `COUNT_STYLES`, TTS ด้วย `say` + cache, `render` แทร็ก cue |
| `app/metronome.py` | ~48 | `write_wav_atomic`, `render_click` |
| `app/midi.py` | ~140 | `tempo_map`: ไฟล์ MIDI จากกริดเมโทรนอม (ดู §7) |
| `app/monitor.py` | ~210 | รายงานทรัพยากรต่อ job (CPU, RAM, GPU ผ่าน `ioreg`, ดิสก์) |
| `app/playlists.py` | ~250 | เพลย์ลิสต์, ไฟล์นำเข้า, `render_mix` (เรนเดอร์มิกซ์โปรเจกต์ไว้เล่น) |
| `static/index.html` | ~425 | โครงทุกหน้า (home, processing, studio, playlists) + dialog |
| `static/app.js` | ~2740 | router, หน้าแรก, ตัวอัปโหลด, `Mixer`, `showStudio` (ใหญ่สุด: timeline canvas, เมโทรนอมเอดิเตอร์, cues, cuts, lead-in, routing, export) |
| `static/playlist.js` | ~1290 | หน้าเพลย์ลิสต์, `plPlayer` (เล่นต่อเนื่อง), ตัวแก้ไขรายเพลง, dialog เพิ่มเข้าเพลย์ลิสต์ |
| `static/style.css` | ~550 | สไตล์ทั้งหมด (คลาส `.pl-*` ของเพลย์ลิสต์) |
| `start.command` | 48 | ตัวเปิดดับเบิลคลิก: หา Python 3.10–3.12, สร้าง `.venv`, เปิดเบราว์เซอร์ |
| `tools/promo/` | | สคริปต์ทำภาพ/วิดีโอโปรโมต (§11) |

dependency หลัก (`requirements.txt`): torch 2.2–2.8, demucs 4.0.1, audio-separator[cpu] 0.45.0, librosa, fastapi, uvicorn, soundfile, scipy, numpy<2.4, imageio-ffmpeg, psutil

## 5. ข้อมูลบนดิสก์

```
data/jobs/<id 12 hex>/
  meta.json            สถานะ job: id, title, source, model, status, stage, progress, stems[], bpm, key, resources...
  source.<ext>         ไฟล์ต้นฉบับ
  stems/*.wav          stem ที่แยกแล้ว + click.wav + cues.wav (guide)  ← stem ห้ามแก้
  analysis.json        version, duration, tempo{bpm,stability,beats,downbeats,varying,map,range,...},
                       meter, key, chords, loops, sections, stems[], loudness, insights
  edits.json           การแก้มือ (ดูล่าง)
  edited/              ผลการตัดช่วง (เรนเดอร์จาก ops)
  padded/              stem ที่เติมความเงียบหน้าเพลงตาม lead-in
  cues_render.json     peaks และตำแหน่งบีตที่นับของแทร็ก cue
  tempo.mid            tempo map (สร้างใหม่ทุกครั้งที่ดาวน์โหลด)
  playback/<key>.wav|json   มิกซ์สำหรับเพลย์ลิสต์ (เก็บ 6 อันล่าสุด)
  stems.zip            cache ของ export (ถูกลบทุกครั้งที่เนื้อหา export เปลี่ยน)
data/library/<id>/     ไฟล์นำเข้า: audio.wav + meta.json (มี peaks) (สร้างตอนนำเข้าครั้งแรก)
data/playlists/<id>.json
  { name, auto_advance, gap, items: [ { id, kind: "file"|"project", ref, title,
      start, end|null, stop_after, fade_in, fade_out, mix?: { muted:[...], pan:{stem:"L"|"R"} } } ] }
models/                น้ำหนักโมเดล (TORCH_HOME) + models/cues/ (cache เสียง say) + models/separator/
```

`edits.json`:
```
{ "click": null | {beats[], downbeats[], meter},     // เมโทรนอมที่แก้มือ (เวลาเพลง)
  "tracks": [{id: "custom_xxxxxxxx", name, color, root}],
  "ops":    [{source, target, start, end}],           // ลำดับการตัด (เรนเดอร์ใหม่จากต้นฉบับ)
  "colors_used": n,
  "lead_in": null | {bars, seconds},
  "cues":   [{kind: "phrase"|"count", time, text?/style?, voice, auto?}] }  // เวลาเพลง
```

## 6. API (ทั้งหมดอยู่ใน `app/main.py`)

| กลุ่ม | Endpoint |
|---|---|
| ระบบ | `GET /api/system` |
| งาน | `GET/POST /api/jobs`, `GET/DELETE /api/jobs/{id}`, `GET /api/jobs/{id}/analysis` |
| เมโทรนอม | `PUT/DELETE /api/jobs/{id}/click` |
| ห้องนับ | `PUT /api/jobs/{id}/lead-in` |
| cue | `GET /api/cues/library`, `GET /api/cues/preview`, `PUT /api/jobs/{id}/cues` |
| ตัดช่วง/แทร็ก | `POST /api/jobs/{id}/cuts`, `DELETE .../cuts/last`, `PATCH/DELETE .../tracks/{tid}` |
| เสียง/ส่งออก | `GET /api/jobs/{id}/stems/{stem}.wav`, `GET .../tempo.mid`, `GET .../download.zip` (มี `tempo.mid` ด้วย) |
| ไลบรารีนำเข้า | `GET/POST /api/library`, `GET/DELETE /api/library/{id}`, `GET .../audio.wav` |
| เล่นเพลย์ลิสต์ | `POST /api/jobs/{id}/playback`, `GET .../playback/{key}.wav` |
| เพลย์ลิสต์ | `GET/POST /api/playlists`, `GET/PUT/DELETE /api/playlists/{id}` |

ทุกการเขียนที่แก้ edits ทำใต้ `edit_lock` เสมอ และจบด้วยการลบ `stems.zip`

## 7. หลักการทำงานของฟีเจอร์ที่ซับซ้อน

**โมเดลแยกเสียง** (`separator.MODELS`): หน้าเลือกโมเดลโชว์ `MAIN_MODELS = ["htdemucs_6s", "htdemucs"]` (6 แทร็ก และมาตรฐาน) ส่วน `htdemucs_ft` (คุณภาพสูงสุด) และ `htdemucs_lead` (ร้องนำ+คอรัส) ซ่อนไว้ให้ผู้ใช้กดเปิดเอง ผู้ใช้สั่งชัดว่าให้ซ่อน

**วิเคราะห์จังหวะ** (ผ่านการจูนกับเพลงจริงหลายเพลงแล้ว ระวังก่อนแก้):
- ใช้ hop ที่หารความยาวบีตลงตัว (librosa ปัดเฟรมต่อบีต)
- เส้นโค้งความเร็ว (tempo curve) ถูกรับเมื่อตรงกับ hit ดีกว่า ≥ 1.5 เท่า (`CURVE_MIN_GAIN`)
- บีตถูก snap เข้าหา attack จริง (`SNAP_SECONDS = 0.035`)
- `tempo.py` แบ่งช่วงความเร็วด้วย penalty `SEGMENT_PENALTY` ที่จูนกับเพลงคงที่และเพลงที่เปลี่ยนความเร็ว
- เวอร์ชันวิเคราะห์ต่ำกว่า `ANALYSIS_VERSION` จะรันซ้ำอัตโนมัติจาก stem เดิมตอนเปิดเพลง (`upgrade_analysis`) **โดยเก็บการแก้มือไว้**; ถ้าปรับอัลกอริทึมจนผลต่าง ให้เพิ่มเลขนี้

**สองฐานเวลา**: `edits.json` เก็บ **เวลาเพลง**; API และ UI ใช้ **เวลา timeline = เวลาเพลง + lead-in** (`project.lead_seconds`) แปลงที่ขอบ API (ดู `save_cues`, `update_click`, `create_cut`) นี่คือจุดที่บั๊กเกิดง่ายที่สุด

**ทุกแทร็กยาวเท่ากัน** (`timeline_samples` = ความยาวเพลง + lead-in) เพื่อให้ export วางที่ 0 แล้วตรงกัน

**นับเข้า (count-in)**: วางเทียบกับบีตที่ใกล้ที่สุด (ในระยะ 0.4 × ช่วงบีต) จึงอยู่รอดเมื่อแก้ grid, เมโทรนอมเปลี่ยนแล้วต้อง re-render cue; สไตล์ `gap1/2/3` สร้างจาก `[คำ] + [None]*gap` ต่อเลข

**การเขียนไฟล์แบบ atomic ทุกที่** (`write_wav_atomic`, `project._swap_dir`, `playlists.write_json_atomic`, `save_edits`): เบราว์เซอร์อาจกำลังดาวน์โหลดไฟล์ตอนมันถูกสร้างใหม่

**MIDI tempo map** (`midi.tempo_map`, ปุ่ม "Tempo map" และใน ZIP): SMF type 1, PPQ 960 สร้างจาก `timeline_grid` (เวลาเดียวกับ WAV ที่ export) แทร็ก 1 มี tempo หนึ่งค่าต่อหนึ่งบีต (ปัดจากผลรวมสะสม คลาดไม่ถึง 1 µs) + time signature + marker ชื่อท่อน แทร็ก 2 มีโน้ตคลิกทุกบีต (ช่อง 10, ตัวเน้นโน้ต 76) ช่วงก่อนบีตแรกเติมบีตตามความเร็วเปิดเพลง และใส่ห้องสั้น (เช่น 2/4) ให้ห้องแรกของเพลงตรงห้องใน DAW ห้องที่ยาวไม่เท่า meter ได้ time signature ของตัวเอง

**ซ้าย/ขวา (routing)**: เลือก L หรือ R จะรวมแทร็กเป็นโมโนด้วย (L+R)/2 เพื่อให้เสียงกลางคงระดับ กฎเดียวกันทั้งฝั่งเบราว์เซอร์ (`makeRoute` ใน app.js) และฝั่ง server (`playlists.render_mix`) **ต้องแก้คู่กันเสมอ** ค่าที่ตั้งในหน้าเพลงเก็บใน localStorage ต่อเพลง

**เพลย์ลิสต์**: 
- เพลงจากโปรเจกต์ถูกเรนเดอร์เป็นมิกซ์ฝั่ง server (`POST /playback`) แล้ว cache ใน `playback/` (ใช้ `mix_key` เป็นชื่อ)
- `plPlayer` เล่นด้วย `source.start(when, offset, duration)` บนนาฬิกาของ AudioContext พร้อม gain envelope โหลดเพลงถัดไปล่วงหน้าเพื่อให้ต่อกันไม่สะดุด
- สลับมิกซ์ระหว่างเล่น: `play()` รับฟังก์ชันตำแหน่งที่ประเมินหลังเสียงใหม่โหลดเสร็จ (เคยเริ่มผิดตำแหน่งเพราะประเมินก่อน)
- ปุ่มลัด "cue+คลิกไปขวา ดนตรีไปซ้าย" มีทั้งในหน้าเพลงและในตัวแก้ไขเพลงของเพลย์ลิสต์

## 8. กับดักและข้อตกลงที่ห้ามพลาด

- **ชื่อเดิมต้องคงไว้หลังเปลี่ยนเป็น Prism:** `STEM_STUDIO_DATA`, `STEM_STUDIO_PORT`, localStorage `stem-studio:pan:<job>` และ `stem-studio-cue-voice`, ชื่อโฟลเดอร์/repo เปลี่ยนแล้วผู้ใช้เก่าเสียค่าที่ตั้งไว้
- **ชื่อไฟล์ export เป็นชื่อเครื่องดนตรีอย่างเดียว** (`drums.wav` ไม่มีชื่อเพลง) ชื่อซ้ำให้ต่อท้ายด้วย id ZIP ตั้งชื่อตามเพลง ผู้ใช้สั่งชัด
- **UI ใหม่ใช้ภาษาไทยพูด** แบบเดียวกับที่มีอยู่
- ฝั่ง front ไม่มี build: ใช้ `el()` และ `api()` ที่มีอยู่, router แบบ hash ใน `route()`, ชื่อ view ใน `show()`, listener ต่อ view ผูกกับ `AbortController` แล้วเคลียร์ใน `leaveView` ถ้าเพิ่ม view ใหม่ต้องทำตามนี้ ไม่งั้น listener รั่วข้ามหน้า
- `static/` ส่ง `Cache-Control: no-cache` แก้แล้วรีโหลดก็เห็น (ถ้ายังเห็นของเก่า ให้ hard reload) ส่วนโค้ดใน `app/` ต้องรีสตาร์ต
- เสียง cue ใช้ `say` ของ macOS เรียกด้วยชื่อเสียงจากรายการ `VOICE_CHOICES` เท่านั้น (ไม่ส่งข้อความจากผู้ใช้เป็นชื่อเสียง) cache ที่ `models/cues/` และมีตัวตัดความเงียบหน้าคำ
- ความยาวทั้งหมดในเพลย์ลิสต์เป็น "ประมาณ" จนกว่าจะเรนเดอร์มิกซ์ (เพราะยังไม่รู้ความยาว timeline ถ้าตั้งจุดจบไว้ท้ายเพลง) — ดู §10
- ขีดจำกัดในโค้ดที่มีไว้กันข้อมูลบวม: `MAX_TRACKS = 24`, `MAX_OPS = 500`, `MAX_BEATS = 20000`, `MAX_CUES = 500`, `MAX_ITEMS = 300`, `MAX_PLAYLISTS = 200`, อัปโหลดสูงสุด 1 GB ต่อไฟล์

## 9. วิธีรันและทดสอบ

รันจริง: `./start.command` หรือ

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level warning
```

**รันชุดทดสอบบนข้อมูลสำเนา (ทำทุกครั้งก่อนลองฟีเจอร์ที่เขียนข้อมูล):**

```bash
# ทำสำเนา 1–2 เพลงแบบไม่กินดิสก์ (APFS clone) ไว้นอกโปรเจกต์ หรือในโฟลเดอร์ scratch
mkdir -p /tmp/prism-test/data/jobs
cp -c -R data/jobs/<job_id> /tmp/prism-test/data/jobs/
STEM_STUDIO_DATA=/tmp/prism-test/data .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8766
```

(ถ้าระบบกำหนดให้ใช้โฟลเดอร์ scratch อื่นสำหรับไฟล์ชั่วคราว ให้ใช้ตามนั้นแทน `/tmp`)

เช็กที่ใช้มาทั้งหมด:
- `node --check static/app.js static/playlist.js` (ไวยากรณ์ JS)
- `.venv/bin/python -c "import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]" app/*.py`
- `curl` ยิง API บน 8766 แล้วดูผล JSON / สถานะ
- Chrome headless ผ่าน DevTools protocol ด้วย `WebSocket` ในตัวของ Node 24 ดูตัวอย่างการคลิก ลาก พิมพ์ จับภาพ ใน `tools/promo/record.mjs` และ `screenshots.mjs` (ไม่ต้องติดตั้งแพ็กเกจเพิ่ม)
- เครื่องมือ browser ของแอป Claude (preview) ใช้ได้เช่นกันสำหรับเปิดหน้า 8766 ตรวจ console/network

ยังไม่มี: unit test, CI, linter ถ้าจะเพิ่ม เริ่มจากฟังก์ชันบริสุทธิ์ที่เสี่ยงที่สุด: `tempo.tempo_map`, `project.clean_click_grid`, `cues.count_words`, `playlists.save_playlist` (ตัวตรวจ input)

## 10. งานที่ค้างและลำดับที่แนะนำ

รายละเอียดเต็มใน [ROADMAP.md](ROADMAP.md) สรุปลำดับ:

**ก่อนเผยแพร่ (ผู้ใช้กำลังจะโพสต์)**
1. เปลี่ยน repo เป็น public (ผู้ใช้ทำที่ GitHub เอง) แล้วใส่ลิงก์แทน `[ใส่ลิงก์]` ในโพสต์/สไลด์/วิดีโอ
2. เสียงไกด์จาก `say` ติดไลเซนส์ Apple (ใช้ส่วนตัวไม่เชิงพาณิชย์): ทางแก้คือชุดเสียงที่อัดเอง ให้ `cues.speak` ใช้ไฟล์ที่ผู้ใช้นำเข้า/แพ็กไว้แทน `say` โดยคงระบบ cache + ตัดความเงียบเดิม
3. โหมดร้องนำ+คอรัส (`mel_band_roformer_karaoke_aufr33_viperx`) ไม่พบไลเซนส์: ขออนุญาตผู้ทำ หรือปิดในเวอร์ชันแจก

**ฟีเจอร์ (ผู้ใช้เห็นด้วยว่าเป็นช่องว่างสำหรับงานใหญ่)**
- P1 (แนะนำทำก่อน): ~~export ไฟล์ MIDI tempo map~~ (ทำแล้ว `app/midi.py`) เหลือ: เลือก 48 kHz/24-bit (ตอนนี้ 44.1 kHz/16-bit)
- P2: เพลย์ลิสต์หลายช่องเอาต์พุต (`setSinkId`, ChannelMerger) — ต้องเปลี่ยนจากมิกซ์สเตอริโอที่ server เป็นโหลดแยก stem
- P3: โหมดโชว์ (footswitch/Web MIDI, ล็อกหน้าจอ, `beforeunload`, Wake Lock)
- P4/P5: timecode (LTC/MTC), ระบบสำรอง — อาจเกินขอบเขต ให้ DAW ทำ

**งานเล็ก**: ส่ง `timeline_duration` ให้เพลย์ลิสต์ไม่ต้องเรนเดอร์ก่อนจึงรู้ความยาว; เพลย์ลิสต์ยังไม่รับระดับ fader (`render_mix` ไม่มี gain); ปุ่มฟังตัวอย่างของนับเว้นอ่านติดกัน; pan เก็บใน localStorage ไม่ตามไปเครื่องอื่น

**โหมดประหยัด RAM** (สำหรับ M1 8 GB): ข้อมูลที่วัดบน M3 Pro: RAM สูงสุด 2.4–5.3 GB, GPU memory 1.45 GB ต่อเพลง น่าจะรันได้แต่ใช้ swap; ทางแก้คือแบ่งเพลงเป็นช่วงสั้นและคืนหน่วยความจำระหว่างขั้น

**ติดตั้ง**: ต้องมี Python 3.10–3.12 ในเครื่อง (ถ้าไม่มี `start.command` ฟ้อง) ครั้งแรกต้องต่อเน็ตโหลดโมเดล ~1.3 GB รองรับ macOS เท่านั้น

## 11. สื่อโปรโมต (ทำเสร็จแล้ว)

| ชิ้น | ที่อยู่ |
|---|---|
| สไลด์แนะนำ (เจาะกลุ่ม sound MD/คนทำ data) | https://claude.ai/artifact/T5q7VgGPbpoLPdgLPGWDSz (ส่วนตัว ต้องกด Share เอง) |
| วิดีโอ 9:16 ยาว 74.4 วินาที | `~/Documents/stems/prism-promo-9x16.mp4` (นอก repo) |
| ข้อความโพสต์ + บทพากย์ | ไม่ได้เก็บในไฟล์: อยู่ในบทสนทนา ถ้าต้องใช้ให้ขอผู้ใช้ส่งมาหรือเขียนใหม่ตามฟีเจอร์ใน §1 |

วิดีโอสร้างจากสคริปต์ใน `tools/promo/` (บันทึกการใช้แอปจริงเป็นเฟรม → ประกอบด้วย `comp.html` → เรนเดอร์ทีละเฟรม → ffmpeg) เพลงประกอบสังเคราะห์เองด้วย `music.py` จึงใช้ได้ฟรี `record.mjs` และ `screenshots.mjs` มี `JOB` และ `PLAYLIST` เป็น id ของข้อมูลสำเนาเดิม ต้องเปลี่ยนให้ตรงกับสำเนาของคุณ

ข้อสังเกตเรื่องเนื้อหาโปรโมต: ตัวเลขที่ยืนยันแล้วและใช้ในสื่อได้ คือ 6 แทร็ก, แยกเพลง 4 นาทีกว่า ~26 วินาทีบน M3 Pro, 135 วลี cue, 7 สไตล์นับเข้า, ห้องนับ 1–8 ห้อง, เมโทรนอมแก้ได้ละเอียด 1 ms

## 12. ประวัติการตัดสินใจ (ทำไมถึงเป็นแบบนี้)

- **ไม่มีเฟรมเวิร์กฝั่งหน้าเว็บ** เพื่อให้เปิดโค้ดแล้วแก้ได้เลยไม่ต้อง build และให้ `start.command` ไฟล์เดียวพอ
- **แก้แบบไม่ทำลาย** (ops → เรนเดอร์ใหม่) หลังจากผู้ใช้ต้องการลองตัดแล้วย้อนได้ และกันไม่ให้ stem ต้นฉบับเสีย
- **สองฐานเวลา** เกิดจากฟีเจอร์ lead-in: ความเงียบก่อนเพลงต้องไม่ทำให้ cue/บีตที่แก้มือเลื่อน จึงเก็บเวลาเพลงไว้และบวก lead ตอนส่งออก
- **เพลย์ลิสต์เรนเดอร์มิกซ์ที่ server** เพื่อให้เบราว์เซอร์โหลดไฟล์เดียวต่อเพลงและต่อกันไม่สะดุด (ข้อเสียที่รู้: ไม่มี fader และเอาต์พุตสเตอริโอเท่านั้น → P2)
- **ตัวเลือก L/R รวมเป็นโมโนด้วย (L+R)/2** แทนการตัดช่องทิ้ง เพื่อให้เสียงกลาง (ร้อง กลอง) ไม่เบาลง
- **ไม่ถือว่า Prism แทน DAW สำหรับงานใหญ่** ตอบผู้ใช้ไว้แล้วว่าเหมาะเป็นเครื่องมือเตรียม data งานทุกขนาด ส่วนตัวเล่นเพลย์ลิสต์เหมาะงานเล็กถึงกลาง งานใหญ่ควร export ไปเล่นใน DAW (ดู ROADMAP §1) อย่าโฆษณาเกินจริง
- **ไลเซนส์ MIT สำหรับโค้ด** แต่ส่วนประกอบมีเงื่อนไขของตัวเอง (say, โมเดล karaoke, ffmpeg GPL, lameenc LGPL, Demucs ฝึกด้วย MUSDB) สรุปไว้ใน THIRD_PARTY_NOTICES.md และ README ผู้ใช้ถามเรื่องนี้เองและต้องการความโปร่งใส ห้ามเขียนในเอกสารว่า "ใช้เชิงพาณิชย์ได้ไม่มีข้อจำกัด"

## 13. เช็กลิสต์ก่อนส่งงานทุกครั้ง

- [ ] ทดสอบบน 8766 ด้วยข้อมูลสำเนา ไม่ใช่ `data/` จริง
- [ ] `node --check` ผ่าน, `ast.parse` ผ่าน
- [ ] แก้ `app/` แล้วรีสตาร์ต (ถ้าเป็นเซิร์ฟเวอร์จริง ต้องไม่มีงาน queued/running)
- [ ] ถ้าแก้ logic เสียงซ้าย/ขวา แก้ทั้ง `makeRoute` และ `render_mix`
- [ ] ถ้าแก้เนื้อหา export ต้องลบ `stems.zip` ของ job นั้น (โค้ดทำให้อยู่แล้วใน endpoint ที่แก้ edits)
- [ ] ถ้าแก้ตัววิเคราะห์ ให้เพิ่ม `ANALYSIS_VERSION` และลองกับเพลงเปลี่ยนความเร็ว + เพลงคงที่
- [ ] ข้อความ UI/เอกสารเป็นภาษาไทยพูด ไม่ใส่ข้อมูลส่วนตัว
- [ ] `git status` ไม่มี `data/`, `models/`, `.venv/`; commit เมื่อผู้ใช้สั่งเท่านั้น และข้อความ commit ไม่มีบรรทัด Co-Authored-By หรืออ้างถึง AI
- [ ] รายงานตามจริง: ตรวจอะไรแล้ว อะไรยังไม่ได้ตรวจ

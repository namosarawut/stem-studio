# คู่มือ Prism: ที่มาของหน้าและภาพ

ตัวคู่มืออยู่ที่ [docs/manual/index.html](../../docs/manual/index.html) (เปิดจากโฟลเดอร์ได้เลย ไม่ต้องรันโปรแกรม) และออนไลน์ที่ https://namosarawut.github.io/stem-studio/manual/ ผ่าน GitHub Pages (สร้างจาก `docs/` ของ `main` ทุกครั้งที่ push) โฟลเดอร์นี้เก็บสิ่งที่ใช้สร้างมัน

| ไฟล์ | หน้าที่ |
|---|---|
| `template.html` | เนื้อหาคู่มือทั้ง 4 phase แก้ข้อความที่นี่ ภาพใส่เป็น `{{figure:ชื่อ:wide:คำบรรยาย}}` หรือ `{{anim:ชื่อ:คำบรรยาย}}` |
| `meta.json` | ขนาดของแต่ละภาพ และตำแหน่งเลขกำกับ (`marks`: x, y เป็น % ของภาพ) |
| `build.mjs` | ประกอบ `template.html` + `meta.json` เป็น `docs/manual/index.html` |
| `shots.mjs` | ถ่ายภาพและเฟรมภาพเคลื่อนไหวจากแอปจริงด้วย Chrome headless (DevTools protocol) |
| `convert2.py` – `convert4.py` | แปลงภาพของ phase 2–4 เป็น WebP ครอปให้พอดี และคำนวณตำแหน่งเลขใหม่ลง `meta.json` |

## แก้ข้อความ

```bash
node tools/manual/build.mjs
```

แก้ `template.html` แล้วรันคำสั่งนี้ หน้าใน `docs/manual/` จะถูกสร้างใหม่ (`--fragment` ได้หน้าที่ไม่มี `<html>`/`<head>` สำหรับโฮสต์ที่ห่อหน้าให้เอง)

## ถ่ายภาพใหม่

ภาพทั้งหมดถ่ายจาก**สำเนา**ข้อมูลที่ใช้เพลงตัวอย่าง ห้ามถ่ายจาก `data/` จริง

1. ทำสำเนาข้อมูลแล้วเปิดเซิร์ฟเวอร์ทดสอบที่พอร์ต 8767: `STEM_STUDIO_DATA=<สำเนา>/data .venv/bin/python -m uvicorn app.main:app --port 8767`
2. แก้ id ใน `shots.mjs` ให้ตรงกับสำเนา: `SONG` (เพลงที่ความเร็วไม่คงที่ ใช้กับ "ทำคลิกให้นิ่ง") และ `SONG_B` (เพลงความเร็วคงที่)
3. `node tools/manual/shots.mjs <โฟลเดอร์ภาพ> <phase> [ชื่อภาพ]` โดย phase 4 ต้องตั้ง `SET_ID=<id เพลย์ลิสต์>` ส่วน phase 1 กับ 3 ต้องตั้ง `IMPORT_FILE` และ `SOUND_FILE` เป็นไฟล์เสียงตัวอย่าง
4. `.venv/bin/python tools/manual/convert<phase>.py <โฟลเดอร์ภาพ>` แล้ว `node tools/manual/build.mjs`

บางขั้นแก้ข้อมูลในสำเนา (ใส่ cue, ตัดแทร็ก, ล็อกความเร็ว, ลบเพลย์ลิสต์ลงถังขยะ) ให้ใช้สำเนาที่ทิ้งได้ phase 1 แปลงภาพด้วยมือตอนทำครั้งแรก จึงไม่มี `convert1.py` ภาพ phase 1 ใน `docs/manual/img/` ใช้ต่อได้ตามเดิม

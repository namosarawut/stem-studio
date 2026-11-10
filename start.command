#!/bin/bash
# Double-click to run Stem Studio. Sets itself up on first launch.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${STEM_STUDIO_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

find_python() {
  local candidates=("$HOME"/.pyenv/versions/3.1[0-2].*/bin/python python3.12 python3.11 python3.10 python3)
  local candidate
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# A venv breaks when the folder moves, so test that it runs rather than that it exists.
if ! .venv/bin/python -c "import demucs, fastapi, librosa, audio_separator, psutil" >/dev/null 2>&1; then
  echo "กำลังติดตั้งครั้งแรก อาจใช้เวลาสักครู่..."
  if ! PYTHON="$(find_python)"; then
    echo "ไม่พบ Python 3.10-3.12 กรุณาติดตั้งก่อน เช่น: brew install python@3.12"
    read -r -p "กด Enter เพื่อปิด"
    exit 1
  fi
  rm -rf .venv
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

if curl -fsS "$URL/api/system" >/dev/null 2>&1; then
  echo "Stem Studio ทำงานอยู่แล้วที่ $URL"
  open "$URL"
  exit 0
fi

( for _ in $(seq 1 60); do
    if curl -fsS "$URL/api/system" >/dev/null 2>&1; then open "$URL"; exit 0; fi
    sleep 0.5
  done ) &

echo "Stem Studio: $URL  (ปิดหน้าต่างนี้หรือกด Ctrl+C เพื่อหยุด)"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning

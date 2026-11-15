@echo off
rem Double-click to run Prism on Windows. Sets itself up on first launch (the Windows
rem counterpart of start.command). Needs Python 3.10-3.12 from python.org.
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if "%STEM_STUDIO_PORT%"=="" (set "PORT=8765") else (set "PORT=%STEM_STUDIO_PORT%")
set "URL=http://127.0.0.1:%PORT%"
set "PY=.venv\Scripts\python.exe"

rem A venv breaks when the folder moves, so test that it runs rather than that it exists.
"%PY%" -c "import demucs, fastapi, librosa, audio_separator, psutil" >nul 2>&1
if errorlevel 1 (
  call :setup
  if errorlevel 1 goto :fail
)
"%PY%" -c "import anthropic" >nul 2>&1 || "%PY%" -m pip install --quiet "anthropic>=2.8,<3"

call :alive
if not errorlevel 1 (
  echo Prism ทำงานอยู่แล้วที่ %URL%
  start "" "%URL%"
  exit /b 0
)

rem Open the browser once the server answers.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 240;$i++){try{Invoke-WebRequest -UseBasicParsing '%URL%/api/system' -TimeoutSec 2 | Out-Null; Start-Process '%URL%'; break}catch{Start-Sleep -Milliseconds 500}}"
echo Prism: %URL%
echo ปิดหน้าต่างนี้หรือกด Ctrl+C เพื่อหยุด
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --log-level warning
exit /b %errorlevel%

:alive
powershell -NoProfile -Command "try{Invoke-WebRequest -UseBasicParsing '%URL%/api/system' -TimeoutSec 2 | Out-Null; exit 0}catch{exit 1}" >nul 2>&1
exit /b %errorlevel%

:setup
echo กำลังติดตั้งครั้งแรก อาจใช้เวลาหลายนาที...
call :find_python
if errorlevel 1 (
  echo ไม่พบ Python 3.10-3.12
  echo ดาวน์โหลดจาก https://www.python.org/downloads/ แล้วติ๊ก "Add python.exe to PATH" ตอนติดตั้ง
  exit /b 1
)
if exist .venv rmdir /s /q .venv
%PYTHON% -m venv .venv
if errorlevel 1 exit /b 1
"%PY%" -m pip install --quiet --upgrade pip
rem PyTorch: the CUDA build when an NVIDIA card is present (much faster), else the CPU build.
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
  echo พบการ์ดจอ NVIDIA จะติดตั้ง PyTorch แบบใช้การ์ดจอ
  "%PY%" -m pip install --quiet "torch>=2.2,<2.9" "torchaudio>=2.2,<2.9" --index-url https://download.pytorch.org/whl/cu126
  if errorlevel 1 exit /b 1
) else (
  echo ไม่พบการ์ดจอ NVIDIA จะใช้ CPU แยกเสียง ช้ากว่าแต่ใช้ได้
)
"%PY%" -m pip install --quiet -r requirements.txt
exit /b %errorlevel%

:find_python
for %%V in (3.12 3.11 3.10) do (
  py -%%V -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON=py -%%V"
    exit /b 0
  )
)
python -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON=python"
  exit /b 0
)
exit /b 1

:fail
echo ติดตั้งไม่สำเร็จ ดูข้อความด้านบน
pause
exit /b 1

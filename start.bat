@echo off
REM ============================================================
REM  AutoCut Pro — start the server
REM  Double-click this file, then open http://localhost:5000
REM ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] ไม่พบ .venv — สร้าง virtualenv และติดตั้ง dependencies ก่อน:
  echo     py -3.11 -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

echo เปิดเว็บที่ http://localhost:5000  (กด Ctrl+C เพื่อปิด)
".venv\Scripts\python.exe" app.py
pause

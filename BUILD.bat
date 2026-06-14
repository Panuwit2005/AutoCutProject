@echo off
chcp 65001 >nul
setlocal EnableExtensions
title AutoCut - ตัวช่วย Build (เมนูเดียวจบ)

rem ====================================================================
rem  BUILD.bat - ดับเบิลคลิกเพื่อ Build AutoCut โดยไม่ต้องพิมพ์คำสั่งเอง
rem  - ไปที่โฟลเดอร์โปรเจกต์เองอัตโนมัติ (ไม่ว่าจะวางไอคอนไว้ที่ไหน)
rem  - ข้าม ExecutionPolicy ให้ / ค้างหน้าจอเสมอเพื่ออ่าน error ได้ทัน
rem ====================================================================

rem --- ย้ายไปยังโฟลเดอร์ที่ไฟล์ .bat นี้อยู่ (= รากโปรเจกต์) ---
cd /d "%~dp0"

rem --- ตรวจว่าสคริปต์ build อยู่ครบไหม ---
if not exist "%~dp0packaging\build.ps1" goto NO_SCRIPT

:MENU
cls
echo ============================================================
echo                 AutoCut - ตัวช่วย Build
echo ============================================================
echo.
echo   โฟลเดอร์โปรเจกต์: %~dp0
echo.
echo   เลือกสิ่งที่ต้องการ Build แล้วกด Enter:
echo.
echo     [1]  โปรแกรมลูกค้า  (AutoCut Pro)  ^<-- ใช้บ่อยสุด
echo            ได้ตัวติดตั้ง .exe + ตัว portable .zip ในโฟลเดอร์ release
echo.
echo     [2]  เครื่องมือแอดมิน  (AutoCut Admin)
echo            ตัวสร้างคีย์ / เผยแพร่อัปเดต  (เก็บเป็นความลับ ห้ามส่งลูกค้า)
echo.
echo     [3]  Build ทั้งสองอย่าง  (ลูกค้า + แอดมิน)
echo.
echo     [0]  ออก
echo.
echo ============================================================
set "CHOICE="
set /p "CHOICE=พิมพ์เลข (1/2/3/0) แล้วกด Enter: "

if "%CHOICE%"=="1" goto BUILD_APP
if "%CHOICE%"=="2" goto BUILD_ADMIN
if "%CHOICE%"=="3" goto BUILD_BOTH
if "%CHOICE%"=="0" goto END
echo.
echo   *** พิมพ์ผิด กรุณาพิมพ์แค่ 1, 2, 3 หรือ 0 ***
timeout /t 2 >nul
goto MENU

rem --------------------------------------------------------------------
:BUILD_APP
cls
echo ============================================================
echo   กำลัง Build โปรแกรมลูกค้า (AutoCut Pro) ...
echo   ครั้งแรกอาจนานหลายนาที (โหลดโมเดล AI) - ปล่อยให้ทำงานไป อย่าปิด
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build.ps1"
if errorlevel 1 goto FAIL
echo.
echo   [สำเร็จ] ไฟล์อยู่ในโฟลเดอร์: %~dp0release
goto DONE

rem --------------------------------------------------------------------
:BUILD_ADMIN
cls
echo ============================================================
echo   กำลัง Build เครื่องมือแอดมิน (AutoCut Admin) ...
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_keygen.ps1"
if errorlevel 1 goto FAIL
echo.
echo   [สำเร็จ] ไฟล์อยู่ในโฟลเดอร์: %~dp0release\admin
echo   *** โฟลเดอร์ admin เป็นความลับ - ห้ามส่งให้ลูกค้า ***
goto DONE

rem --------------------------------------------------------------------
:BUILD_BOTH
cls
echo ============================================================
echo   [1/2] กำลัง Build โปรแกรมลูกค้า (AutoCut Pro) ...
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build.ps1"
if errorlevel 1 goto FAIL
echo.
echo ============================================================
echo   [2/2] กำลัง Build เครื่องมือแอดมิน (AutoCut Admin) ...
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_keygen.ps1"
if errorlevel 1 goto FAIL
echo.
echo   [สำเร็จทั้งสองอย่าง]
echo     - โปรแกรมลูกค้า: %~dp0release
echo     - เครื่องมือแอดมิน: %~dp0release\admin  (ความลับ ห้ามส่งลูกค้า)
goto DONE

rem --------------------------------------------------------------------
:FAIL
echo.
echo ============================================================
echo   *** Build ไม่สำเร็จ ***
echo   อ่านข้อความสีแดงด้านบนเพื่อดูสาเหตุ
echo.
echo   สาเหตุที่พบบ่อย + วิธีแก้:
echo     - ไม่มี Python 3.11  ->  เปิด PowerShell พิมพ์:  winget install Python.Python.3.11
echo     - ไม่มี ffmpeg       ->  เปิด PowerShell พิมพ์:  winget install Gyan.FFmpeg
echo     - ไม่มี Inno Setup   ->  เปิด PowerShell พิมพ์:  winget install JRSoftware.InnoSetup
echo       (ติดตั้งเสร็จ ปิด-เปิด BUILD.bat ใหม่ แล้วลองอีกครั้ง)
echo ============================================================
echo.
echo   กด Enter เพื่อกลับไปเมนู...
pause >nul
goto MENU

rem --------------------------------------------------------------------
:NO_SCRIPT
echo.
echo   *** หาไฟล์ build ไม่เจอ ***
echo   ไฟล์ BUILD.bat นี้ต้องวางไว้ในโฟลเดอร์โปรเจกต์ AutoCut
echo   (โฟลเดอร์เดียวกับที่มีโฟลเดอร์ packaging อยู่ข้างใน)
echo.
pause
goto END

rem --------------------------------------------------------------------
:DONE
echo.
echo ============================================================
echo   เสร็จแล้ว! กด Enter เพื่อกลับไปเมนู หรือปิดหน้าต่างนี้ได้เลย
echo ============================================================
pause >nul
goto MENU

:END
endlocal
exit /b 0

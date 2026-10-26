@echo off
chcp 65001 >nul
setlocal
title Convert Link YouTube to MP4
cd /d "%~dp0"

:: ถ้ามีไฟล์ไอคอน cover.ico ในโฟลเดอร์นี้ จะใช้มัน
:: ถ้าไม่มี แต่มี cover.png / cover.jpg / cover.jpeg จะสร้าง cover.ico ให้อัตโนมัติ
set "ICON_FILE=%~dp0cover.ico"
set "ICON_SOURCE="
for %%I in ("%~dp0cover.ico") do if exist "%%~fI" set "ICON_FILE=%%~fI"
if not exist "%ICON_FILE%" (
    for %%I in ("%~dp0cover.png" "%~dp0cover.jpg" "%~dp0cover.jpeg") do if exist "%%~fI" set "ICON_SOURCE=%%~fI"
)
if not exist "%ICON_FILE%" if defined ICON_SOURCE (
    echo กำลังสร้างไฟล์ไอคอนจาก "%ICON_SOURCE%"...
    powershell -NoProfile -Command "Add-Type -AssemblyName System.Drawing; $src = [System.Drawing.Image]::FromFile('%ICON_SOURCE%'); $bmp = New-Object System.Drawing.Bitmap 256,256; $g = [System.Drawing.Graphics]::FromImage($bmp); $g.DrawImage($src,0,0,256,256); $g.Dispose(); $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon()); $fs = [System.IO.File]::Open('%ICON_FILE%','Create'); $icon.Save($fs); $fs.Close(); $icon.Dispose(); $bmp.Dispose(); $src.Dispose();"
)

set "SHORTCUT_PATH=%USERPROFILE%\Desktop\Convert Link YouTube to MP4.lnk"
set "SCRIPT_PATH=%~f0"
set "SCRIPT_DIR=%~dp0"
if exist "%ICON_FILE%" if not exist "%SHORTCUT_PATH%" (
    powershell -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%SHORTCUT_PATH%'); $Shortcut.TargetPath = '%SCRIPT_PATH%'; $Shortcut.WorkingDirectory = '%SCRIPT_DIR%'; $Shortcut.IconLocation = '%ICON_FILE%,0'; $Shortcut.Save()"
)

:: โปรแกรมรันจาก Python environment ของตัวเอง (.venv) จะได้ไม่ชนกับ Python ตัวอื่นในเครื่อง
:: ไฟล์นี้เตรียมแค่ Python + .venv ส่วนไลบรารี / yt-dlp / ffmpeg หน้าต่าง "ตรวจสอบระบบ" ของโปรแกรมจะติดตั้งและอัปเดตให้
set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"

if not exist "%VENV_PYW%" goto :create_venv
"%VENV_PY%" -c "import tkinter" >nul 2>&1 && goto :launch
:: .venv เสีย (เช่น Python ตัวหลักถูกถอนหรือย้าย) -> สร้างใหม่
echo พบ .venv เดิมใช้งานไม่ได้ กำลังสร้างใหม่...
rmdir /s /q "%VENV_DIR%"

:create_venv
echo ====================================================
echo  กำลังเตรียมโปรแกรมครั้งแรก
echo ====================================================
echo.
call :find_python
if not defined BASE_PY call :install_python
if not defined BASE_PY goto :no_python
echo กำลังสร้าง Python environment จาก %BASE_PY%
"%BASE_PY%" -m venv "%VENV_DIR%"
if errorlevel 1 goto :setup_failed

:launch
start "" "%VENV_PYW%" "%~dp0main.py"
exit /b 0

:: หา Python 3.10+ ของ Windows ที่มี tkinter (ข้าม Python ของ MSYS/MinGW ที่ลง package ของ Windows ไม่ได้)
:find_python
set "BASE_PY="
where py >nul 2>&1 && for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try_python "%%P"
for /f "delims=" %%P in ('where python 2^>nul') do call :try_python "%%P"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do call :try_python "%%~fD\python.exe"
exit /b 0

:try_python
if defined BASE_PY exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys, tkinter, venv; sys.exit('GCC' in sys.version or sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 set "BASE_PY=%~1"
exit /b 0

:: ไม่มี Python เลย -> ติดตั้ง Python 3.12 ให้อัตโนมัติผ่าน winget (มีใน Windows 10/11)
:install_python
where winget >nul 2>&1 || exit /b 0
echo ไม่พบ Python ในเครื่อง กำลังติดตั้ง Python 3.12 ให้อัตโนมัติ...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :find_python
exit /b 0

:no_python
echo.
echo ผิดพลาด: ไม่พบ Python 3.10 ขึ้นไปในเครื่อง และติดตั้งอัตโนมัติไม่สำเร็จ
echo กรุณาติดตั้ง Python จาก python.org แล้วเปิดโปรแกรมนี้ใหม่อีกครั้ง
echo กำลังเปิดหน้าดาวน์โหลด Python...
start "" "https://www.python.org/downloads/windows/"
echo.
pause
exit /b 1

:setup_failed
echo.
echo ผิดพลาด: สร้าง Python environment ไม่สำเร็จ
echo ลองลบโฟลเดอร์ .venv แล้วเปิดโปรแกรมใหม่อีกครั้ง
echo.
pause
exit /b 1

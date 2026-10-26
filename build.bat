@echo off
chcp 65001 >nul
setlocal

echo ====================================================
echo  สร้าง Convert Link YouTube to MP4.exe
echo ====================================================
echo.

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=python"
    ) else (
        where python3 >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_CMD=python3"
        )
    )
)

if not defined PYTHON_CMD (
    echo ผิดพลาด: ไม่พบ Python ในระบบ
    pause
    exit /b 1
)

echo [1/2] กำลังติดตั้ง PyInstaller...
%PYTHON_CMD% -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo ผิดพลาด: ติดตั้ง PyInstaller ไม่สำเร็จ
    pause
    exit /b 1
)

echo [2/2] กำลัง build .exe ...
%PYTHON_CMD% -m PyInstaller ^
  --onefile ^
  --noconsole ^
    --name "Convert Link YouTube to MP4" ^
  --add-data "index.html;." ^
  --collect-all yt_dlp ^
  --hidden-import flask ^
  --hidden-import flask_cors ^
  main.py

if errorlevel 1 (
    echo.
    echo ผิดพลาด: build ไม่สำเร็จ
    pause
    exit /b 1
)

echo.
echo ====================================================
echo  สำเร็จ!  ไฟล์อยู่ที่:  dist\Convert Link YouTube to MP4.exe
echo ====================================================
echo.
pause

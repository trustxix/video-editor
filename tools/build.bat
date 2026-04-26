@echo off
setlocal
cd /d "%~dp0\.."

echo === Installing dependencies (pinned) ===
pip install -r requirements.txt
if errorlevel 1 (
    echo [build] pip install failed
    exit /b 1
)

echo === Building Video Editor with PyInstaller ===
pyinstaller ^
    --name "Video Editor" ^
    --windowed ^
    --noconfirm ^
    --clean ^
    --contents-directory "lib" ^
    --icon "assets\icon.ico" ^
    --add-data "assets\icon.ico;assets" ^
    --collect-submodules PyQt6 ^
    --hidden-import PyQt6.QtMultimedia ^
    --hidden-import PyQt6.QtMultimediaWidgets ^
    main.py
if errorlevel 1 (
    echo [build] PyInstaller failed
    exit /b 1
)

echo === Copying config folder ===
if not exist "dist\Video Editor\config" mkdir "dist\Video Editor\config"

echo === Copying license + privacy + notices into dist ===
copy /Y "LICENSE"                       "dist\Video Editor\LICENSE.txt"        >nul
copy /Y "NOTICES.md"                    "dist\Video Editor\NOTICES.md"         >nul
copy /Y "PRIVACY.md"                    "dist\Video Editor\PRIVACY.md"         >nul
copy /Y "docs\FFMPEG_SOURCE_OFFER.md"   "dist\Video Editor\FFMPEG_SOURCE_OFFER.md" >nul

echo === Bundling FFmpeg (BtbN release) ===
powershell -ExecutionPolicy Bypass -NoProfile -File "tools\fetch_ffmpeg.ps1"
if errorlevel 1 (
    echo [build] FFmpeg bundling failed
    exit /b 1
)

echo === Authenticode signing (no-op without SIGNCERT_PATH) ===
powershell -ExecutionPolicy Bypass -NoProfile -File "tools\sign.ps1"
if errorlevel 1 (
    echo [build] sign step failed
    exit /b 1
)

echo.
echo === Build complete ===
echo Output:    dist\Video Editor\
echo Run with: "dist\Video Editor\Video Editor.exe"
echo.
echo Next:     run tools\release.ps1 to compile the installer.
endlocal

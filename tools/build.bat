@echo off
cd /d "%~dp0\.."

echo === Installing dependencies ===
pip install pyinstaller PyQt6 >nul 2>&1

echo === Building Video Editor ===
pyinstaller ^
    --name "Video Editor" ^
    --windowed ^
    --noconfirm ^
    --clean ^
    --contents-directory "lib" ^
    --collect-submodules PyQt6 ^
    --hidden-import PyQt6.QtMultimedia ^
    --hidden-import PyQt6.QtMultimediaWidgets ^
    main.py

echo === Copying config folder ===
if not exist "dist\Video Editor\config" mkdir "dist\Video Editor\config"

echo === Done ===
echo.
echo Output: dist\Video Editor\
echo.
echo To bundle FFmpeg, place ffmpeg.exe and ffprobe.exe in:
echo   dist\Video Editor\ffmpeg\
echo.
echo Or they will be found from system PATH.
pause

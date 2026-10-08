@echo off
cd /d "%~dp0"

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto error

echo [2/3] Building exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name MultiVideoFrameExtractor --exclude-module tkinter main.py
if errorlevel 1 goto error

echo [3/3] Done.
echo   Output: %~dp0dist\MultiVideoFrameExtractor.exe
echo   Note: ffmpeg must be in PATH for fast proxy creation (see README).
pause
exit /b 0

:error
echo Build failed. Check the error messages above.
pause
exit /b 1

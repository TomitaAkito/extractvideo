@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 依存ライブラリをインストールします...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo [2/3] exeをビルドします...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name MultiVideoFrameExtractor ^
    --exclude-module tkinter ^
    main.py
if errorlevel 1 goto :error

echo [3/3] 完了しました。
echo   出力先: %~dp0dist\MultiVideoFrameExtractor.exe
echo   ※ 高速変換には ffmpeg が PATH に通っている必要があります (README参照)
pause
exit /b 0

:error
echo ビルドに失敗しました。上のエラーメッセージを確認してください。
pause
exit /b 1

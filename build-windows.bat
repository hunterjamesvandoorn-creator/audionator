@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo Failed to install the Windows build requirements.
    exit /b 1
)

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name Audionator ^
    --collect-all pygame ^
    --collect-all mutagen ^
    --collect-all webview ^
    --collect-all pythonnet ^
    --collect-all clr_loader ^
    --distpath "%~dp0dist" ^
    --workpath "%~dp0build\pyinstaller" ^
    --specpath "%~dp0build" ^
    client.py
if errorlevel 1 (
    echo Failed to build Audionator.exe.
    exit /b 1
)

if not exist "%~dp0dist\Audionator.exe" (
    echo Client build completed without creating Audionator.exe.
    exit /b 1
)

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --console ^
    --name AudionatorServer ^
    --collect-all imageio_ffmpeg ^
    --distpath "%~dp0dist" ^
    --workpath "%~dp0build\pyinstaller-server" ^
    --specpath "%~dp0build" ^
    server_app.py
if errorlevel 1 (
    echo Failed to build AudionatorServer.exe.
    exit /b 1
)

if not exist "%~dp0dist\AudionatorServer.exe" (
    echo Server build completed without creating AudionatorServer.exe.
    exit /b 1
)

echo.
echo Audionator Windows client is ready:
echo %~dp0dist\Audionator.exe
echo Audionator Windows server is ready:
echo %~dp0dist\AudionatorServer.exe
exit /b 0

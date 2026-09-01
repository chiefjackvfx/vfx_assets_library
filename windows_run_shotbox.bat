@echo off
setlocal

cd /d "%~dp0" || exit /b 1

if not exist "%CD%\venv\Scripts\python.exe" (
    echo Missing virtual environment. Run windows_install_shotbox_assets.bat first.
    exit /b 1
)

"%CD%\venv\Scripts\python.exe" "%CD%\run_vfx_asset_library.py" %*
if errorlevel 1 (
    echo ShotBox Assets exited with an error.
    pause
    exit /b 1
)

exit /b 0

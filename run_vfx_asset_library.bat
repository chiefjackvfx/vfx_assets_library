@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"
if errorlevel 1 goto project_directory_error

set "PROJECT_READY=0"
if exist "pyproject.toml" if exist "run_vfx_asset_library.py" if exist "src" set "PROJECT_READY=1"
set "SHOTBOX_INSTALL_ROOT=%LOCALAPPDATA%\ShotBoxAssets"
if not defined LOCALAPPDATA set "SHOTBOX_INSTALL_ROOT=%USERPROFILE%\AppData\Local\ShotBoxAssets"
set "BOOTSTRAP_PYTHON_DIRECTORY=%SHOTBOX_INSTALL_ROOT%\runtime\python"
set "BOOTSTRAP_PYTHON=%BOOTSTRAP_PYTHON_DIRECTORY%\python.exe"
set "VENV_DIRECTORY=.venv"
if defined SHOTBOX_VENV_ROOT set "VENV_DIRECTORY=%SHOTBOX_VENV_ROOT%"
set "VENV_PYTHON=%VENV_DIRECTORY%\Scripts\python.exe"
set "PYTHON_COMMAND="
set "PYTHON_ARGUMENT="
set "UPDATE_PYTHON="
set "UPDATE_PYTHON_ARGUMENT="
set "UPDATE_SCRIPT=scripts\windows_auto_update.py"

if exist "%VENV_PYTHON%" goto validate_existing_venv
if exist "%VENV_DIRECTORY%" goto broken_venv
if exist "%BOOTSTRAP_PYTHON%" goto validate_bootstrap_python

where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
set "PYTHON_COMMAND=py"
set "PYTHON_ARGUMENT=-3"
set "UPDATE_PYTHON=py"
set "UPDATE_PYTHON_ARGUMENT=-3"
goto check_for_update

:try_python
where python >nul 2>&1
if errorlevel 1 goto install_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto install_python
set "PYTHON_COMMAND=python"
set "UPDATE_PYTHON=python"
goto check_for_update

:validate_bootstrap_python
"%BOOTSTRAP_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto python_install_error
set "PYTHON_COMMAND=%BOOTSTRAP_PYTHON%"
set "UPDATE_PYTHON=%BOOTSTRAP_PYTHON%"
goto check_for_update

:install_python
where powershell >nul 2>&1
if errorlevel 1 goto python_install_error
echo Python 3.11 or newer was not found.
echo Installing the official Python Install Manager for this user...
where winget >nul 2>&1
if errorlevel 1 goto install_python_manager_with_powershell
winget install --id 9NQ7512CXL7T -e --source msstore --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
call :locate_python_manager
if defined PYTHON_MANAGER goto install_python_runtime

:install_python_manager_with_powershell
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Add-AppxPackage -AppInstallerFile 'https://www.python.org/ftp/python/pymanager/pymanager.appinstaller'"
if errorlevel 1 goto python_install_error
call :locate_python_manager
if not defined PYTHON_MANAGER goto python_install_error

:install_python_runtime
echo Installing an isolated Python 3.13 runtime for ShotBox Assets...
"%PYTHON_MANAGER%" install --target="%BOOTSTRAP_PYTHON_DIRECTORY%" 3.13
if errorlevel 1 goto python_install_error
if not exist "%BOOTSTRAP_PYTHON%" goto python_install_error
goto validate_bootstrap_python

:validate_existing_venv
"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto venv_version_error
set "UPDATE_PYTHON=%VENV_PYTHON%"

:check_for_update
if /i "%SHOTBOX_AUTO_UPDATE%"=="0" if "%PROJECT_READY%"=="1" goto after_update
if /i "%SHOTBOX_AUTO_UPDATE%"=="false" if "%PROJECT_READY%"=="1" goto after_update
if "%SHOTBOX_UPDATE_RELAUNCHED%"=="1" goto after_update
if "%SHOTBOX_ARCHIVE_INSTALL%"=="1" goto after_update
if exist "%UPDATE_SCRIPT%" goto run_updater
if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\ShotBoxAssets\application\bootstrap\windows_auto_update.py" set "UPDATE_SCRIPT=%LOCALAPPDATA%\ShotBoxAssets\application\bootstrap\windows_auto_update.py"
if exist "%UPDATE_SCRIPT%" goto run_updater
where powershell >nul 2>&1
if errorlevel 1 goto updater_missing
set "UPDATE_SCRIPT=%TEMP%\shotbox-windows-updater-%RANDOM%-%RANDOM%.py"
set "SHOTBOX_UPDATE_SCRIPT=%UPDATE_SCRIPT%"
echo Downloading the ShotBox Assets bootstrap from GitHub...
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $headers=@{Accept='application/vnd.github+json'; 'User-Agent'='ShotBox-Assets-Windows-Bootstrap'; 'X-GitHub-Api-Version'='2022-11-28'}; if($env:SHOTBOX_GITHUB_TOKEN){$headers.Authorization='Bearer ' + $env:SHOTBOX_GITHUB_TOKEN}; $response=Invoke-RestMethod -UseBasicParsing -Headers $headers -Uri 'https://api.github.com/repos/chiefjackvfx/vfx_assets_library/contents/scripts/windows_auto_update.py?ref=main'; [IO.File]::WriteAllBytes($env:SHOTBOX_UPDATE_SCRIPT,[Convert]::FromBase64String(($response.content -replace '\s','')))"
if errorlevel 1 goto updater_missing

:run_updater
echo Checking GitHub for ShotBox Assets updates...
"%UPDATE_PYTHON%" %UPDATE_PYTHON_ARGUMENT% "%UPDATE_SCRIPT%" --project "%CD%" --launcher "%~f0" -- %* & if errorlevel 100 exit /b 0
if errorlevel 20 goto github_download_error
if errorlevel 1 goto updater_error

:after_update
if exist "%VENV_PYTHON%" goto synchronize_dependencies

:create_venv
echo Creating the ShotBox Assets virtual environment...
"%PYTHON_COMMAND%" %PYTHON_ARGUMENT% -m venv "%VENV_DIRECTORY%"
if errorlevel 1 goto create_venv_error

:synchronize_dependencies
"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto venv_version_error

echo Synchronizing ShotBox Assets dependencies...
"%VENV_PYTHON%" -m pip install -e "."
if errorlevel 1 goto install_error

echo Starting ShotBox Assets...
"%VENV_PYTHON%" "run_vfx_asset_library.py" %*
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" goto app_error

endlocal & exit /b 0

:project_directory_error
echo Error: could not open the ShotBox Assets project directory.
set "FAILURE_CODE=1"
goto pause_on_error

:locate_python_manager
set "PYTHON_MANAGER="
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.PythonManager_*") do if exist "%%~fD\py.exe" if not defined PYTHON_MANAGER set "PYTHON_MANAGER=%%~fD\py.exe"
if defined PYTHON_MANAGER exit /b 0
for /f "delims=" %%P in ('where pymanager 2^>nul') do if not defined PYTHON_MANAGER set "PYTHON_MANAGER=%%~fP"
if defined PYTHON_MANAGER exit /b 0
for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYTHON_MANAGER set "PYTHON_MANAGER=%%~fP"
exit /b 0

:updater_missing
if "%PROJECT_READY%"=="1" (
    echo Warning: the automatic updater could not be downloaded; starting the installed version.
    goto after_update
)
echo Error: the ShotBox Assets bootstrap could not be downloaded from GitHub.
set "FAILURE_CODE=1"
goto pause_on_error

:github_download_error
echo Error: ShotBox Assets could not be downloaded from GitHub and no cached installation is available.
set "FAILURE_CODE=1"
goto pause_on_error

:updater_error
if "%PROJECT_READY%"=="1" (
    echo Warning: the automatic updater failed; starting the installed version.
    goto after_update
)
echo Error: the ShotBox Assets updater failed before the application was downloaded.
set "FAILURE_CODE=1"
goto pause_on_error

:broken_venv
echo Error: the virtual environment directory exists but does not contain %VENV_PYTHON%.
echo Repair or remove %VENV_DIRECTORY%, then run this launcher again.
set "FAILURE_CODE=1"
goto pause_on_error

:python_install_error
echo Error: the automatic per-user Python installation failed.
echo Check the messages above, then run this launcher again.
set "FAILURE_CODE=1"
goto pause_on_error

:create_venv_error
echo Error: could not create .venv. Ensure the Python venv module is installed.
set "FAILURE_CODE=1"
goto pause_on_error

:venv_version_error
echo Error: the .venv interpreter must be Python 3.11 or newer.
"%VENV_PYTHON%" --version
set "FAILURE_CODE=1"
goto pause_on_error

:install_error
echo Error: dependency installation failed. Check the output above and your network connection.
set "FAILURE_CODE=1"
goto pause_on_error

:app_error
echo Error: ShotBox Assets exited with status %APP_EXIT_CODE%.
set "FAILURE_CODE=%APP_EXIT_CODE%"

:pause_on_error
echo.
echo Press any key to close this window...
pause >nul
endlocal & exit /b %FAILURE_CODE%

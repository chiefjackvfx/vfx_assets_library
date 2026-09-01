@echo off
setlocal EnableExtensions DisableDelayedExpansion

for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI"
cd /d "%SCRIPT_DIR%"
if errorlevel 1 goto project_directory_error

set "PROJECT_READY=0"
if exist ".git" if exist "pyproject.toml" if exist "run_vfx_asset_library.py" if exist "scripts\windows_auto_update.py" if exist "src" set "PROJECT_READY=1"
if "%PROJECT_READY%"=="1" goto configure_runtime

set "INSTALL_TARGET=%SCRIPT_DIR%\vfx_assets_library"
set "FORWARDED_ARGUMENTS="
if "%~1"=="" goto prepare_standalone_install
set "FIRST_ARGUMENT=%~1"
if "%FIRST_ARGUMENT:~0,2%"=="--" goto collect_standalone_arguments
set "INSTALL_TARGET=%~f1"
shift

:collect_standalone_arguments
if "%~1"=="" goto prepare_standalone_install
set FORWARDED_ARGUMENTS=%FORWARDED_ARGUMENTS% "%~1"
shift
goto collect_standalone_arguments

:prepare_standalone_install
if exist "%INSTALL_TARGET%\.git" goto validate_install_target
if exist "%INSTALL_TARGET%" goto inspect_install_target
goto clone_install_target

:inspect_install_target
dir /b "%INSTALL_TARGET%" 2>nul | findstr . >nul
if not errorlevel 1 goto install_target_not_empty

:clone_install_target
where git >nul 2>&1
if errorlevel 1 goto git_required_error
for %%P in ("%INSTALL_TARGET%") do set "INSTALL_PARENT=%%~dpP"
if not exist "%INSTALL_PARENT%" mkdir "%INSTALL_PARENT%" 2>nul
if not exist "%INSTALL_PARENT%" goto install_parent_error
echo Cloning ShotBox Assets into "%INSTALL_TARGET%"...
set "GIT_TERMINAL_PROMPT=0"
set "GCM_INTERACTIVE=Never"
git clone --branch main --single-branch "https://github.com/chiefjackvfx/vfx_assets_library.git" "%INSTALL_TARGET%"
if errorlevel 1 goto git_clone_error

:validate_install_target
if not exist "%INSTALL_TARGET%\.git" goto invalid_install_target
if not exist "%INSTALL_TARGET%\pyproject.toml" goto invalid_install_target
if not exist "%INSTALL_TARGET%\run_vfx_asset_library.bat" goto invalid_install_target
if not exist "%INSTALL_TARGET%\run_vfx_asset_library.py" goto invalid_install_target
if not exist "%INSTALL_TARGET%\scripts\windows_auto_update.py" goto invalid_install_target
if not exist "%INSTALL_TARGET%\src" goto invalid_install_target

where git >nul 2>&1
if errorlevel 1 goto launch_installed_checkout
set "TARGET_ORIGIN="
for /f "delims=" %%R in ('git -C "%INSTALL_TARGET%" remote get-url origin 2^>nul') do if not defined TARGET_ORIGIN set "TARGET_ORIGIN=%%R"
if /i "%TARGET_ORIGIN%"=="https://github.com/chiefjackvfx/vfx_assets_library.git" goto launch_installed_checkout
if /i "%TARGET_ORIGIN%"=="https://github.com/chiefjackvfx/vfx_assets_library" goto launch_installed_checkout
if /i "%TARGET_ORIGIN%"=="git@github.com:chiefjackvfx/vfx_assets_library.git" goto launch_installed_checkout
if /i "%TARGET_ORIGIN%"=="ssh://git@github.com/chiefjackvfx/vfx_assets_library.git" goto launch_installed_checkout
goto unexpected_install_origin

:launch_installed_checkout
echo Starting the ShotBox Assets installation at "%INSTALL_TARGET%"...
call "%INSTALL_TARGET%\run_vfx_asset_library.bat" %FORWARDED_ARGUMENTS%
set "CHILD_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %CHILD_EXIT_CODE%

:configure_runtime
set "BOOTSTRAP_PYTHON_DIRECTORY=%SCRIPT_DIR%\.runtime\python"
set "BOOTSTRAP_PYTHON=%BOOTSTRAP_PYTHON_DIRECTORY%\python.exe"
set "VENV_DIRECTORY=%SCRIPT_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIRECTORY%\Scripts\python.exe"
set "PYTHON_COMMAND="
set "PYTHON_ARGUMENT="
set "UPDATE_PYTHON="
set "UPDATE_PYTHON_ARGUMENT="
set "UPDATE_SCRIPT=%SCRIPT_DIR%\scripts\windows_auto_update.py"
set "VENV_NEEDS_REPAIR=0"

if exist "%VENV_PYTHON%" goto validate_existing_venv
if exist "%VENV_DIRECTORY%" set "VENV_NEEDS_REPAIR=1"

:find_base_python
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
if not errorlevel 1 goto existing_venv_ready
set "VENV_NEEDS_REPAIR=1"
goto find_base_python

:existing_venv_ready
set "UPDATE_PYTHON=%VENV_PYTHON%"

:check_for_update
if not exist "%UPDATE_SCRIPT%" goto updater_missing
echo Checking GitHub for ShotBox Assets updates...
"%UPDATE_PYTHON%" %UPDATE_PYTHON_ARGUMENT% "%UPDATE_SCRIPT%" --project "%SCRIPT_DIR%" --launcher "%~f0" -- %* & if errorlevel 100 exit /b 0
if errorlevel 1 goto updater_error

:after_update
if exist "%VENV_PYTHON%" goto synchronize_dependencies

:create_venv
if "%VENV_NEEDS_REPAIR%"=="1" goto repair_venv
echo Creating the ShotBox Assets virtual environment...
"%PYTHON_COMMAND%" %PYTHON_ARGUMENT% -m venv "%VENV_DIRECTORY%"
if errorlevel 1 goto create_venv_error
goto synchronize_dependencies

:repair_venv
echo Repairing the ShotBox Assets virtual environment...
"%PYTHON_COMMAND%" %PYTHON_ARGUMENT% -m venv --clear "%VENV_DIRECTORY%"
if errorlevel 1 goto create_venv_error

:synchronize_dependencies
"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto create_venv_error

echo Synchronizing ShotBox Assets dependencies...
"%VENV_PYTHON%" -m pip install -e "%SCRIPT_DIR%"
if errorlevel 1 goto install_error

echo Starting ShotBox Assets...
"%VENV_PYTHON%" "%SCRIPT_DIR%\run_vfx_asset_library.py" %*
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" goto app_error

endlocal & exit /b 0

:locate_python_manager
set "PYTHON_MANAGER="
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.PythonManager_*") do if exist "%%~fD\py.exe" if not defined PYTHON_MANAGER set "PYTHON_MANAGER=%%~fD\py.exe"
if defined PYTHON_MANAGER exit /b 0
for /f "delims=" %%P in ('where pymanager 2^>nul') do if not defined PYTHON_MANAGER set "PYTHON_MANAGER=%%~fP"
if defined PYTHON_MANAGER exit /b 0
for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYTHON_MANAGER set "PYTHON_MANAGER=%%~fP"
exit /b 0

:project_directory_error
echo Error: could not open the ShotBox Assets launcher directory.
set "FAILURE_CODE=1"
goto pause_on_error

:git_required_error
echo Error: Git for Windows is required to install ShotBox Assets.
echo Install Git from https://git-scm.com/download/win, then run this launcher again.
set "FAILURE_CODE=1"
goto pause_on_error

:install_parent_error
echo Error: could not create the parent directory for "%INSTALL_TARGET%".
set "FAILURE_CODE=1"
goto pause_on_error

:install_target_not_empty
echo Error: the installation target exists and is not an empty Git checkout:
echo "%INSTALL_TARGET%"
echo Choose an empty target folder or an existing ShotBox Assets checkout.
set "FAILURE_CODE=1"
goto pause_on_error

:git_clone_error
echo Error: Git could not clone ShotBox Assets into "%INSTALL_TARGET%".
echo Check the Git output and network connection, then run this launcher again.
set "FAILURE_CODE=1"
goto pause_on_error

:invalid_install_target
echo Error: "%INSTALL_TARGET%" is not a complete ShotBox Assets checkout.
echo Repair or remove that target, then run this launcher again.
set "FAILURE_CODE=1"
goto pause_on_error

:unexpected_install_origin
echo Error: the existing checkout does not use the approved ShotBox Assets GitHub repository.
echo Target: "%INSTALL_TARGET%"
echo Origin: "%TARGET_ORIGIN%"
set "FAILURE_CODE=1"
goto pause_on_error

:updater_missing
echo Warning: the automatic updater is missing; starting the installed version.
goto after_update

:updater_error
echo Warning: the automatic updater failed; starting the installed version.
goto after_update

:python_install_error
echo Error: the automatic local Python installation failed.
echo Check the messages above, then run this launcher again.
set "FAILURE_CODE=1"
goto pause_on_error

:create_venv_error
echo Error: could not create or repair the ShotBox Assets virtual environment.
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

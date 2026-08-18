@echo off
setlocal

cd /d "%~dp0"
if errorlevel 1 goto project_directory_error

set "VENV_PYTHON=.venv\Scripts\python.exe"

if exist "%VENV_PYTHON%" goto validate_venv
if exist ".venv" goto broken_venv

set "PYTHON_COMMAND="
set "PYTHON_ARGUMENT="

where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
set "PYTHON_COMMAND=py"
set "PYTHON_ARGUMENT=-3"
goto create_venv

:try_python
where python >nul 2>&1
if errorlevel 1 goto python_error
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto python_error
set "PYTHON_COMMAND=python"

:create_venv
echo Creating the ShotBox Assets virtual environment...
%PYTHON_COMMAND% %PYTHON_ARGUMENT% -m venv ".venv"
if errorlevel 1 goto create_venv_error

:validate_venv
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

:broken_venv
echo Error: .venv exists but does not contain %VENV_PYTHON%.
echo Repair or remove .venv, then run this launcher again.
set "FAILURE_CODE=1"
goto pause_on_error

:python_error
echo Error: Python 3.11 or newer is required but was not found.
echo Install Python from https://www.python.org/downloads/ and try again.
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

@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"
if errorlevel 1 goto project_directory_error

set "PROJECT_READY=0"
if exist "pyproject.toml" if exist "run_vfx_asset_library.py" if exist "src" set "PROJECT_READY=1"
set "SHOTBOX_INSTALL_ROOT=%LOCALAPPDATA%\ShotBoxAssets"
if not defined LOCALAPPDATA set "SHOTBOX_INSTALL_ROOT=%USERPROFILE%\AppData\Local\ShotBoxAssets"
if "%SHOTBOX_ARCHIVE_INSTALL%"=="1" goto configure_runtime
if exist ".git" goto configure_runtime
if /i "%SHOTBOX_AUTO_UPDATE%"=="0" if "%PROJECT_READY%"=="1" goto configure_runtime
if /i "%SHOTBOX_AUTO_UPDATE%"=="false" if "%PROJECT_READY%"=="1" goto configure_runtime
if /i "%~1"=="--no-update" if "%PROJECT_READY%"=="1" goto configure_runtime
call :download_project_from_github
if errorlevel 1 goto github_download_error
cd /d "%DOWNLOADED_PROJECT%"
if errorlevel 1 goto project_directory_error
set "PROJECT_READY=1"
set "SHOTBOX_ARCHIVE_INSTALL=1"
set "SHOTBOX_VENV_ROOT=%SHOTBOX_INSTALL_ROOT%\application\.venv"

:configure_runtime
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

:download_project_from_github
where powershell >nul 2>&1
if errorlevel 1 exit /b 1
set "SHOTBOX_BOOTSTRAP_SOURCE=%~f0"
set "SHOTBOX_BOOTSTRAP_SCRIPT=%TEMP%\shotbox-archive-bootstrap-%RANDOM%-%RANDOM%.ps1"
set "SHOTBOX_BOOTSTRAP_RESULT=%TEMP%\shotbox-archive-result-%RANDOM%-%RANDOM%.txt"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $source=[IO.File]::ReadAllText($env:SHOTBOX_BOOTSTRAP_SOURCE); $marker='# SHOTBOX_EMBEDDED_POWERSHELL'; $index=$source.LastIndexOf($marker); if($index -lt 0){throw 'Embedded GitHub installer is missing.'}; [IO.File]::WriteAllText($env:SHOTBOX_BOOTSTRAP_SCRIPT,$source.Substring($index + $marker.Length),[Text.UTF8Encoding]::new($false))"
if errorlevel 1 exit /b 1
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SHOTBOX_BOOTSTRAP_SCRIPT%" -InstallRoot "%SHOTBOX_INSTALL_ROOT%" -ResultFile "%SHOTBOX_BOOTSTRAP_RESULT%"
set "BOOTSTRAP_EXIT_CODE=%ERRORLEVEL%"
if not "%BOOTSTRAP_EXIT_CODE%"=="0" goto github_bootstrap_cleanup
if not exist "%SHOTBOX_BOOTSTRAP_RESULT%" goto github_bootstrap_result_error
set /p "DOWNLOADED_PROJECT="<"%SHOTBOX_BOOTSTRAP_RESULT%"
if not defined DOWNLOADED_PROJECT goto github_bootstrap_result_error
if not exist "%DOWNLOADED_PROJECT%\pyproject.toml" goto github_bootstrap_result_error
goto github_bootstrap_cleanup

:github_bootstrap_result_error
set "BOOTSTRAP_EXIT_CODE=1"

:github_bootstrap_cleanup
if exist "%SHOTBOX_BOOTSTRAP_SCRIPT%" del /q "%SHOTBOX_BOOTSTRAP_SCRIPT%" >nul 2>&1
if exist "%SHOTBOX_BOOTSTRAP_RESULT%" del /q "%SHOTBOX_BOOTSTRAP_RESULT%" >nul 2>&1
exit /b %BOOTSTRAP_EXIT_CODE%

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

:python_install_error
echo Error: the automatic per-user Python installation failed.
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

# SHOTBOX_EMBEDDED_POWERSHELL
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$ResultFile
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$applicationRoot = Join-Path $InstallRoot 'application'
$versionsRoot = Join-Path $applicationRoot 'versions'
$stateFile = Join-Path $applicationRoot 'state.json'
$requiredPaths = @(
    'pyproject.toml',
    'run_vfx_asset_library.bat',
    'run_vfx_asset_library.py',
    'scripts\windows_auto_update.py',
    'src'
)

function Test-ShotBoxProject([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    foreach ($relative in $requiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $Path $relative))) { return $false }
    }
    return $true
}

function Get-CachedProject {
    if (-not (Test-Path -LiteralPath $stateFile -PathType Leaf)) { return $null }
    try {
        $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
        if ($state.commit -notmatch '^[0-9a-f]{40}$') { return $null }
        $candidate = Join-Path $versionsRoot $state.commit
        if (Test-ShotBoxProject $candidate) { return $candidate }
    } catch {}
    return $null
}

function Get-GitHubHeaders {
    $headers = @{
        Accept = 'application/vnd.github+json'
        'User-Agent' = 'ShotBox-Assets-Windows-Bootstrap'
        'X-GitHub-Api-Version' = '2022-11-28'
    }
    if ($env:SHOTBOX_GITHUB_TOKEN) { $headers.Authorization = 'Bearer ' + $env:SHOTBOX_GITHUB_TOKEN }
    return $headers
}

New-Item -ItemType Directory -Path $versionsRoot -Force | Out-Null
$cachedProject = Get-CachedProject
$archivePath = $null
$stagingPath = $null

try {
    $headers = Get-GitHubHeaders
    $commitResponse = Invoke-RestMethod -UseBasicParsing -Headers $headers -Uri 'https://api.github.com/repos/chiefjackvfx/vfx_assets_library/commits/main' -TimeoutSec 30
    $commit = [string]$commitResponse.sha
    if ($commit -notmatch '^[0-9a-f]{40}$') { throw 'GitHub returned an invalid commit identifier.' }
    $destination = Join-Path $versionsRoot $commit

    if (-not (Test-ShotBoxProject $destination)) {
        $archivePath = Join-Path $applicationRoot ('download-' + [Guid]::NewGuid().ToString('N') + '.zip')
        $stagingPath = Join-Path $versionsRoot ('stage-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $stagingPath -Force | Out-Null
        Invoke-WebRequest -UseBasicParsing -Headers $headers -Uri ('https://api.github.com/repos/chiefjackvfx/vfx_assets_library/zipball/' + $commit) -OutFile $archivePath -TimeoutSec 30

        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
        try {
            $totalSize = [int64]0
            $roots = @{}
            foreach ($entry in $archive.Entries) {
                $name = $entry.FullName.Replace('\', '/')
                $parts = $name.Split('/', [StringSplitOptions]::RemoveEmptyEntries)
                if ($name.StartsWith('/') -or $parts.Count -eq 0 -or $parts -contains '..') { throw 'The GitHub archive contains an unsafe path.' }
                $roots[$parts[0]] = $true
                $totalSize += $entry.Length
                if ($totalSize -gt 2147483648) { throw 'The GitHub archive exceeds the extraction limit.' }
            }
            if ($roots.Count -ne 1) { throw 'The GitHub archive does not contain one project root.' }
        } finally {
            $archive.Dispose()
        }

        Expand-Archive -LiteralPath $archivePath -DestinationPath $stagingPath -Force
        $projectRoots = @(Get-ChildItem -LiteralPath $stagingPath -Directory)
        if ($projectRoots.Count -ne 1 -or -not (Test-ShotBoxProject $projectRoots[0].FullName)) { throw 'The downloaded archive is missing required ShotBox Assets files.' }
        Move-Item -LiteralPath $projectRoots[0].FullName -Destination $destination
    }

    $state = [ordered]@{
        commit = $commit
        checked_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        updated_at = [DateTime]::UtcNow.ToString('o')
    }
    $temporaryState = $stateFile + '.tmp'
    $state | ConvertTo-Json | Set-Content -LiteralPath $temporaryState -Encoding UTF8
    Move-Item -LiteralPath $temporaryState -Destination $stateFile -Force
    [IO.File]::WriteAllText($ResultFile, $destination, [Text.UTF8Encoding]::new($false))
    exit 0
} catch {
    if ($cachedProject) {
        Write-Warning ('GitHub is unavailable; using cached ShotBox Assets. ' + $_.Exception.Message)
        [IO.File]::WriteAllText($ResultFile, $cachedProject, [Text.UTF8Encoding]::new($false))
        exit 0
    }
    Write-Error ('ShotBox Assets could not be downloaded from GitHub: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($archivePath -and (Test-Path -LiteralPath $archivePath)) { Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue }
    if ($stagingPath -and (Test-Path -LiteralPath $stagingPath)) { Remove-Item -LiteralPath $stagingPath -Recurse -Force -ErrorAction SilentlyContinue }
}

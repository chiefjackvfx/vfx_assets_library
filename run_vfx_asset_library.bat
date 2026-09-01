@echo off
setlocal

echo This launcher has moved to windows_install_shotbox_assets.bat.
call "%~dp0windows_install_shotbox_assets.bat" %*
exit /b %ERRORLEVEL%

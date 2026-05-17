@echo off
REM Launch GVM GUI via PowerShell command

powershell -NoProfile -Command "gvm gui" 2>nul
if %errorlevel%==0 exit /b 0

echo GVM GUI command not found.
echo Ensure GVM is installed and "gvm" is available in PowerShell.
pause
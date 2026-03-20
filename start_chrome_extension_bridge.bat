@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT=%~dp0chrome_extension_bridge.py"

set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD where python >nul 2>nul && set "PY_CMD=python"

if not defined PY_CMD (
  echo [ERROR] Python not found in PATH.
  if "%NO_PAUSE%"=="" pause
  exit /b 1
)

if not exist "%SCRIPT%" (
  echo [ERROR] Script not found: %SCRIPT%
  if "%NO_PAUSE%"=="" pause
  exit /b 1
)

echo [INFO] Starting local bridge at http://127.0.0.1:38999 ...
call %PY_CMD% "%SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
echo [INFO] Bridge exited with code %EXIT_CODE%.
if "%NO_PAUSE%"=="" pause
exit /b %EXIT_CODE%


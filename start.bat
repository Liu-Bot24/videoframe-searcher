@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if not exist "%~dp0logs" mkdir "%~dp0logs"
set "LAUNCH_LOG=%~dp0logs\launcher.log"
echo [%date% %time%] Launcher started.>>"%LAUNCH_LOG%"

set "PY_EXE="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
    echo [%date% %time%] Use virtualenv Python: %PY_EXE%>>"%LAUNCH_LOG%"
)

if not defined PY_EXE (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        echo %%I | find /I "WindowsApps" >nul
        if errorlevel 1 (
            set "PY_EXE=%%I"
            goto :python_found
        )
    )
)

:python_found
if not defined PY_EXE (
    echo [%date% %time%] ERROR: Python executable not found.>>"%LAUNCH_LOG%"
    echo [ERROR] Python 3 executable not found in PATH.
    echo Please install Python 3 and add it to PATH.
    pause
    exit /b 9009
)

echo [%date% %time%] Launch by: %PY_EXE% run.py>>"%LAUNCH_LOG%"
"%PY_EXE%" run.py
set "EXIT_CODE=%ERRORLEVEL%"
echo [%date% %time%] Exit code: %EXIT_CODE%.>>"%LAUNCH_LOG%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Program exited with code %EXIT_CODE%.
    echo See logs: "%~dp0logs\launcher.log" and "%~dp0logs\app.log"
    pause
)

exit /b %EXIT_CODE%

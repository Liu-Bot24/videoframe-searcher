@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

if not exist "%~dp0logs" mkdir "%~dp0logs"
set "LAUNCH_LOG=%~dp0logs\launcher.log"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "SYS_PY="

echo [%date% %time%] Launcher started.>>"%LAUNCH_LOG%"

if exist "%VENV_PY%" (
    echo [%date% %time%] Use existing virtualenv Python: %VENV_PY%>>"%LAUNCH_LOG%"
    goto :launch
)

call :resolve_python
if not defined SYS_PY (
    echo [%date% %time%] Python not found, trying winget install...>>"%LAUNCH_LOG%"
    call :try_install_python
    call :resolve_python
)

if not defined SYS_PY (
    echo [%date% %time%] ERROR: Python executable not found.>>"%LAUNCH_LOG%"
    echo [ERROR] Python 3 executable not found.
    echo Please install Python 3.11+ and re-run start.bat.
    pause
    exit /b 9009
)

echo [%date% %time%] Creating virtualenv by: %SYS_PY%>>"%LAUNCH_LOG%"
"%SYS_PY%" -m venv "%~dp0.venv" >>"%LAUNCH_LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: virtualenv creation failed.>>"%LAUNCH_LOG%"
    echo [ERROR] Failed to create .venv. See logs\launcher.log
    pause
    exit /b 9010
)

if not exist "%VENV_PY%" (
    echo [%date% %time%] ERROR: virtualenv Python not found.>>"%LAUNCH_LOG%"
    echo [ERROR] Virtualenv created but python.exe is missing.
    pause
    exit /b 9011
)

:launch
set "PY_EXE=%VENV_PY%"
"%PY_EXE%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [%date% %time%] pip missing in venv, running ensurepip...>>"%LAUNCH_LOG%"
    "%PY_EXE%" -m ensurepip --upgrade >>"%LAUNCH_LOG%" 2>&1
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

:resolve_python
set "SYS_PY="
for /f "delims=" %%I in ('where py 2^>nul') do (
    set "PY_LAUNCHER=%%I"
    goto :try_py_launcher
)
goto :scan_python

:try_py_launcher
for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    echo %%I | find /I "WindowsApps" >nul
    if errorlevel 1 (
        set "SYS_PY=%%I"
        goto :eof
    )
)

:scan_python
for /f "delims=" %%I in ('where python 2^>nul') do (
    echo %%I | find /I "WindowsApps" >nul
    if errorlevel 1 (
        set "SYS_PY=%%I"
        goto :eof
    )
)
goto :eof

:try_install_python
where winget >nul 2>nul
if errorlevel 1 goto :eof
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --silent >>"%LAUNCH_LOG%" 2>&1
goto :eof

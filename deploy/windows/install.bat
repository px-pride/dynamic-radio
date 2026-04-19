@echo off
echo === Dynamic Radio Windows Setup ===
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.14+ from python.org
    pause
    exit /b 1
)

:: Check mpv
mpv --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: mpv not found. Install from https://mpv.io/installation/
    echo Add mpv to your PATH after installing.
    pause
    exit /b 1
)

:: Install uv if needed
uv --version >nul 2>&1
if errorlevel 1 (
    echo Installing uv...
    pip install uv
)

:: Get project directory (parent of deploy/windows)
set "PROJECT_DIR=%~dp0..\.."
pushd "%PROJECT_DIR%"

:: Install dependencies
echo Installing dependencies...
uv sync

:: Create plans directory
if not exist "%USERPROFILE%\.local\share\dynamic-radio\plans" (
    mkdir "%USERPROFILE%\.local\share\dynamic-radio\plans"
)

echo.
echo === Setup complete ===
echo.
echo To start the daemon:
echo   cd %CD%
echo   uv run dynamic-radio
echo.
echo Or use start.bat for background startup.
echo.
popd
pause

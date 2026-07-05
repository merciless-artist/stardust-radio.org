@echo off
REM ====================================================================
REM   Stardust Radio — Dashboard launcher
REM   Creates a venv on first run, installs deps, then starts Flask.
REM ====================================================================

setlocal
cd /d "%~dp0"

if not exist ".venv\" (
    echo [setup] Creating virtual environment...
    python -m venv .venv
)

call ".venv\Scripts\activate.bat"

echo [setup] Installing/updating dependencies...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt

if not exist ".env" (
    echo [warn] No .env found. Copy .env.example to .env and fill it in.
    echo        Starting anyway with placeholder defaults...
)

echo.
echo ============================================================
echo   Stardust Radio dashboard running at http://127.0.0.1:8080
echo   Press Ctrl+C to stop.
echo ============================================================
echo.

python server.py

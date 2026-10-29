@echo off
REM Double-click this (or a Desktop shortcut pointing at it) to launch the
REM training tracker. %~dp0 resolves to wherever this file
REM actually lives, so it works run directly or via a shortcut.
REM
REM The app is src/training/webapp/. Your log lives in
REM data\webapp.db, which is gitignored - it's personal data.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Could not find .venv\Scripts\activate.bat here:
    echo   %~dp0
    echo Check the repo location, or that the venv has been created.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

REM A venv created without `pip install -e .` won't have the webapp's
REM dependencies. Check before starting rather
REM than dying on an import traceback.
python -c "import uvicorn, fastapi, jinja2, bcrypt" 2>nul
if errorlevel 1 (
    echo The webapp's dependencies are missing from this venv.
    echo Install them with:
    echo.
    echo     pip install -e .
    echo.
    pause
    exit /b 1
)

REM An interrupted write leaves a stale journal behind, and every later
REM request then fails with "disk I/O error". Clearing it is safe: SQLite
REM rolls the incomplete transaction back on the next open either way.
if exist "data\webapp.db-journal" (
    echo Clearing a stale database journal from an interrupted write.
    del "data\webapp.db-journal"
)

echo Starting the tracker on http://127.0.0.1:8000
echo Press Ctrl+C in this window to stop it.
echo.

REM Opened after a short delay so the server has bound the port first;
REM if the browser still beats it, just refresh.
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start "" http://127.0.0.1:8000"

REM `python -m uvicorn` rather than `uvicorn` so it doesn't depend on the
REM venv's Scripts directory being on PATH. --reload picks up .py edits;
REM templates and CSS are re-read per request and need no restart.
python -m uvicorn training.webapp.app:app --reload

echo.
echo Tracker stopped.
pause

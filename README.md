# workout_logger

Training log and workout advisor. A small model scores each
movement-pattern × training-style cell by how recently and how often it's
been trained (a log-EWMA), and a FastAPI webapp logs sessions into it and
shows today's suggestions.

    python -m venv .venv
    .venv\Scripts\activate
    pip install -e .[dev]       # add ,sim for the simulation's .xlsx output
    python -m pytest -q
    tracker.bat                 # double-click, or run; opens http://127.0.0.1:8000

Your log lives in `data/webapp.db` (gitignored). Design: `docs/GUIDE.md`.
What's built: `docs/STATUS.md`. What was cut and why: `docs/AUDIT.md`.

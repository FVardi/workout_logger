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

## Hosted test version

Invite-only, for a few testers. Free tiers: the app on Render, the
database on Neon (Postgres). Details and caveats: `docs/STATUS.md`,
"Webapp".

1. Create a free Neon project and copy its connection string.
2. Get an Anthropic API key (console.anthropic.com / platform.claude.com)
   with a little credit, and check the parser first:

       pip install -e .[dev,llm]
       set ANTHROPIC_API_KEY=sk-ant-...
       python scripts/check_parser.py

3. On Render: New → Blueprint → this repository. It reads `render.yaml`
   and asks for `TRAINING_DATABASE_URL` (the Neon string),
   `TRAINING_INVITE_CODE`, `TRAINING_ADMIN_EMAILS` (your email) and
   `ANTHROPIC_API_KEY`. The secret key is generated.
4. Sign up first with your admin email, then send friends the URL and the
   invite code. `/admin` shows who's logging and how well notes are read.

Locally nothing changes: no environment variables means SQLite in
`data/webapp.db` and no note box. To try note parsing locally, install
`.[llm]` and set `ANTHROPIC_API_KEY` before `tracker.bat`.

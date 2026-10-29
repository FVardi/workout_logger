"""Training-log webapp: FastAPI + server-rendered Jinja2 templates.

Layout/styling deliberately deferred - see docs/STATUS.md's
webapp section. This is about getting sign up / log in / log a piece /
see today's recommendation / adjust settings working end to end first.

Run locally:
    uvicorn training.webapp.app:app --reload

Environment variables:
    TRAINING_DB_PATH     - sqlite file path (default: data/webapp.db,
                            gitignored - this is personal training data)
    TRAINING_SECRET_KEY  - session-cookie signing key. The default is fine
                            for local dev only; required once
                            TRAINING_DATABASE_URL is set (see docs/STATUS.md).

    Hosted version (all optional locally):
    TRAINING_DATABASE_URL   - postgresql:// URL; takes precedence over
                              TRAINING_DB_PATH (see training/pg.py)
    TRAINING_SECURE_COOKIES - "1" to send the session cookie over https only
    TRAINING_INVITE_CODE    - when set, sign-up requires it
    TRAINING_ADMIN_EMAILS   - comma-separated; these accounts see /admin
    ANTHROPIC_API_KEY       - turns on LLM-assisted logging (parse_entry.py);
                              TRAINING_PARSER=fake|off overrides
    TRAINING_PARSER_MODEL   - default claude-haiku-5-5
    TRAINING_PARSES_PER_DAY - per-user cap on parses, default 40
"""

from __future__ import annotations

import json
import math
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .. import advisor, db, user_config
from . import auth, parse_entry, session_templates

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "webapp.db"
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

DATABASE_URL = os.environ.get("TRAINING_DATABASE_URL", "").strip()
SECRET_KEY = os.environ.get("TRAINING_SECRET_KEY", "")
if DATABASE_URL and not SECRET_KEY:
    # A hosted database means a hosted app; the dev default would let anyone
    # who has read this file forge a login cookie.
    raise RuntimeError("TRAINING_SECRET_KEY must be set when TRAINING_DATABASE_URL is.")

app = FastAPI(title="Training Advisor")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY or "dev-only-insecure-secret-change-me",
    same_site="lax",
    https_only=os.environ.get("TRAINING_SECURE_COOKIES") == "1",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_conn():
    path = os.environ.get("TRAINING_DATABASE_URL") or os.environ.get("TRAINING_DB_PATH", str(DEFAULT_DB_PATH))
    if not path.startswith(("postgres://", "postgresql://")):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        yield conn
    finally:
        conn.close()


def get_dictionary() -> advisor.Dictionary:
    # Reloaded per request rather than cached at import time: exercises.json
    # changes are meant to take effect without restarting the process during
    # local development. Cheap enough (19 exercises) not to matter.
    return advisor.load_dictionary()


def current_user_id(request: Request) -> int | None:
    return request.session.get("user_id")


def _require_login(request: Request) -> int | RedirectResponse:
    """Returns the user_id, or a RedirectResponse to /login the route
    should return immediately. Not a FastAPI dependency-with-exception on
    purpose - a plain redirect is simpler than wiring a custom exception
    handler for what's still an unstyled, single-developer app.
    """
    user_id = current_user_id(request)
    if user_id is None:
        return RedirectResponse("/login", status_code=303)
    return user_id


# --- Auth ----------------------------------------------------------------------

def _admin_emails() -> set[str]:
    raw = os.environ.get("TRAINING_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _signup_context(error=None) -> dict:
    return {
        "error": error,
        "invite_required": bool(os.environ.get("TRAINING_INVITE_CODE")),
        # Said on the sign-up page whenever either applies, so nobody joins
        # without knowing who can read their log or where their notes go.
        "admin_can_read": bool(_admin_emails()),
        "uses_llm": parse_entry.backend() == "anthropic",
    }


def _start_session(request: Request, user_id: int, email: str) -> None:
    request.session["user_id"] = user_id
    # Only decides whether the nav shows the Admin link; the admin routes
    # re-check the email against TRAINING_ADMIN_EMAILS themselves.
    request.session["is_admin"] = email in _admin_emails()


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", _signup_context())


@app.post("/signup")
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(""),
    conn=Depends(get_conn),
):
    email = email.strip().lower()
    expected = os.environ.get("TRAINING_INVITE_CODE", "")
    if expected and not secrets.compare_digest(invite_code.strip().encode(), expected.encode()):
        return templates.TemplateResponse(
            request, "signup.html", _signup_context("That invite code isn't right."), status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "signup.html", _signup_context("Password must be at least 8 characters."), status_code=400,
        )
    if auth.password_too_long(password):
        return templates.TemplateResponse(request, "signup.html", _signup_context(
            f"Password must be at most {auth.MAX_PASSWORD_BYTES} bytes "
            "(bcrypt's own limit - most passwords are well under this)."
        ), status_code=400)
    if db.get_user_by_email(conn, email) is not None:
        return templates.TemplateResponse(
            request, "signup.html", _signup_context("That email is already registered."), status_code=400,
        )
    user_id = db.create_user(conn, email, auth.hash_password(password))
    _start_session(request, user_id, email)
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    conn=Depends(get_conn),
):
    row = db.get_user_by_email(conn, email.strip().lower())
    if row is None or not auth.verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong email or password."}, status_code=400,
        )
    _start_session(request, row["id"], row["email"])
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- Today -----------------------------------------------------------------------

def _pick_url(picked: list[str], exercise_id: str) -> str:
    """Link that adds one more movement to the provisional plan."""
    return "/?" + urlencode([("picked", p) for p in [*picked, exercise_id]])


def _column(candidates, style: str, picked: list[str], limit: int = 5) -> list[dict]:
    """One style's suggestions, each carrying the link that would pick it."""
    return [
        {"candidate": c, "pick_url": _pick_url(picked, c.exercise.id)}
        for c in candidates if c.style == style
    ][:limit]


@app.get("/", response_class=HTMLResponse)
def today_view(
    request: Request,
    picked: list[str] = Query(default=[]),
    conn=Depends(get_conn),
    dictionary=Depends(get_dictionary),
):
    """Three columns - heavy, hhr, light - that react to what's already
    accounted for.

    `advise()`'s `chosen` argument excludes a pattern once it's been used,
    which is exactly the "a pattern might be covered now" behaviour: the
    list is re-scored from scratch on every pick rather than filtered, so
    a movement that was second choice can become first once the pattern
    ahead of it drops out.

    Two things feed `chosen`. Anything **already logged today** - which
    Today previously ignored, so it would happily headline squats an hour
    after you squatted - and anything **picked** on this page, carried in
    the query string as a provisional plan rather than stored, since
    planning to do something isn't the same as having done it.
    """
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id

    settings = db.get_user_settings(conn, user_id)
    config = user_config.build_config(settings)
    sets = db.fetch_sets(conn, user_id)
    today = date.today()
    today_str = today.isoformat()

    names = {ex.id: ex.name for ex in dictionary.exercises.values()}
    logged_today = []
    for piece in db.fetch_pieces(conn, user_id, date=today_str):
        for s in piece["sets"]:
            if s["exercise_id"] not in logged_today and s["exercise_id"] in dictionary.exercises:
                logged_today.append(s["exercise_id"])

    picked = [p for p in picked if p in dictionary.exercises]
    chosen = [*logged_today, *picked]

    heavy = advisor.advise(dictionary, sets, today, "heavy", chosen=chosen, config=config)
    workout = advisor.advise(dictionary, sets, today, "workout", chosen=chosen, config=config)
    verdict = advisor.recommend(dictionary, sets, today, config=config)

    return templates.TemplateResponse(request, "today.html", {
        "columns": [
            ("heavy", STYLE_LABELS["heavy"], _column(heavy, "heavy", picked)),
            ("hhr", STYLE_LABELS["hhr"], _column(workout, "hhr", picked)),
            ("light", STYLE_LABELS["light"], _column(workout, "light", picked)),
        ],
        "picked": [{"id": p, "name": names.get(p, p)} for p in picked],
        "logged_today": [{"id": p, "name": names.get(p, p)} for p in logged_today],
        "disabled_styles": sorted(config.disabled_styles),
        "verdict": verdict,
        "inf": math.inf,  # templates can't build a float('inf') to compare against
        "active_nav": "today",
    })


# --- Log a piece -------------------------------------------------------------------

# Rows rendered up front vs. how far the server scans. Fewer visible rows
# keeps the form quick to read; "+ Add" clones more client-side, so the
# server has to look past what was initially rendered.
INITIAL_SET_ROWS = 5
MAX_SET_ROWS = 20
INITIAL_MOVEMENT_ROWS = 3
MAX_MOVEMENT_ROWS = 20

# The engine template is gone: running/rowing/biking are ordinary movement
# rows now that a row carries a unit (reps/metres/seconds). Hiding them from
# the movement dropdowns made "3 rounds: 400 m run, 20 air squats"
# unloggable through the UI, which is an ordinary workout. Intervals are
# still expressible - "4 x 400 m" is four rounds of a 400 m row.
# session_templates.expand_engine() went with it; git history has both.
TEMPLATES = ["strength", "rounds", "ladder", "emom", "amrap"]

# A movement row's amount is one number plus a unit, rather than separate
# reps/distance/duration fields - "10 reps", "400 m", "60 s" are the same
# kind of input and only one applies per movement.
UNITS = {
    "reps": "reps",
    "m": "metres",
    "s": "seconds",
}

# Rest is a dictionary entry with an empty `E`, which is the documented way
# to say "never suggested, contributes no exposure" (docs/AUDIT.md).
# It's a movement row like any other so that interval work - "10 thrusters,
# rest 60 s", repeated - is expressible in the templates that already exist,
# rather than needing a template of its own.
REST_ID = "rest"

# `heavy`/`hhr`/`light` are the model's internal names and mean nothing to
# someone mid-session. The stored values don't change - this is wording only,
# kept here so it's one edit rather than one per template.
STYLE_LABELS = {
    "heavy": "Heavy — low reps, fresh, long rests",
    "hhr": "Loaded, breathing hard",
    "light": "Light or bodyweight",
}

# Above this fraction of your own heaviest logged load for a lift, a set is
# guessed to be `heavy` rather than `hhr`. Only ever pre-selects a dropdown
# the person can change; the model scores whatever they submit. Deliberately
# not derived from anything - GUIDE.md puts hhr at roughly 60-70% of 1RM, and
# the heaviest load ever logged is a soft under-estimate of a true 1RM, so
# the boundary sits above that range rather than inside it.
HEAVY_LOAD_FRACTION = 0.8

def _with_volume_text(pieces: list[dict], dictionary) -> list[dict]:
    """Attach a rendered volume line to each piece, and a summary for any
    piece that doesn't have one stored.

    Both formatted in Python so they share phrasing with the stored summary
    (see session_templates). The summary fallback covers pieces logged
    before `blocks.summary` existed - they'd otherwise show as a bare
    structure label like "3 RFT" with no movements.
    """
    names = {ex.id: ex.name for ex in dictionary.exercises.values()}
    for piece in pieces:
        piece["volume_text"] = session_templates.format_volume(piece["volume"], names)
        piece["result_text"] = format_result_time(piece.get("result_seconds"))
        if not piece.get("summary"):
            piece["summary"] = session_templates.summarise_sets(
                piece["sets"], piece.get("structure"), names
            )
    return pieces


def _log_page_context(
    request, conn, dictionary, user_id, error=None, active="strength", on_date=None, parse_ref=None,
):
    """Everything the log page needs, including that day's progress so far.

    `on_date` is the day being logged to, not necessarily today - the
    already-logged panel follows it, so backdating a piece shows you what
    else is on that day rather than what's on today.
    """
    today_str = date.today().isoformat()
    on_date = on_date or today_str
    exercises = sorted(dictionary.exercises.values(), key=lambda ex: ex.name)

    # Per-exercise style handling, all of it aimed at not asking a question
    # that has only one answer. Eight of the nineteen exercises admit exactly
    # one style, so the form shouldn't offer a choice for those at all; the
    # rest default to whatever that exercise was last logged at.
    style_info = {}
    for ex in exercises:
        if len(ex.admissible_styles) == 1:
            default = ex.admissible_styles[0]
        else:
            last = db.last_style_for_exercise(conn, user_id, ex.id)
            default = last if last in ex.admissible_styles else ex.admissible_styles[0]
        style_info[ex.id] = {
            "admissible": list(ex.admissible_styles),
            "default": default,
            # Stand-in for a 1RM, used only to guess heavy-vs-hhr from the
            # load typed in. None until this lift has been logged with a load.
            "heaviest": db.heaviest_load_for_exercise(conn, user_id, ex.id),
        }

    # A draft piece from parse_entry pre-fills one of the forms below; the
    # person checks it and logs it like any other piece.
    parse_ctx = None
    if parse_ref is not None:
        parse_ctx = _parse_context(conn, dictionary, user_id, *parse_ref, style_info, on_date)
        if parse_ctx is not None:
            active = parse_ctx["prefill"]["template"]
            on_date = parse_ctx["prefill"]["fields"]["date"]
    prefilled_rows = parse_ctx["rows"] if parse_ctx else 0

    return {
        "parse_ctx": parse_ctx,
        "parse_enabled": parse_entry.backend() is not None,
        "exercises": exercises,
        "styles": dictionary.styles,
        "style_labels": STYLE_LABELS,
        "style_info": style_info,
        "heavy_load_fraction": HEAVY_LOAD_FRACTION,
        "exercise_names": {ex.id: ex.name for ex in exercises},
        "today_pieces": _with_volume_text(
            db.fetch_pieces(conn, user_id, date=on_date), dictionary
        ),
        "on_date": on_date,
        "today": today_str,
        "max_date": (date.today() + timedelta(days=1)).isoformat(),
        "set_rows": range(max(INITIAL_SET_ROWS, prefilled_rows)),
        "movement_rows": range(max(INITIAL_MOVEMENT_ROWS, prefilled_rows)),
        "max_rows": max(MAX_SET_ROWS, MAX_MOVEMENT_ROWS),
        "units": UNITS,
        "templates": TEMPLATES,
        "template_labels": {
            "strength": "Strength (sets and reps)",
            "rounds": "For time (rounds)",
            "ladder": "For time (rep ladder, e.g. 21-15-9)",
            "emom": "EMOM / on the minute",
            "amrap": "AMRAP",
        },
        "active": active,
        "error": error,
        "active_nav": "log",
    }


@app.get("/log", response_class=HTMLResponse)
def log_form(
    request: Request,
    template: str = "strength",
    date: str | None = None,
    parse: int | None = None,
    piece: int = 0,
    conn=Depends(get_conn),
    dictionary=Depends(get_dictionary),
):
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id
    active = template if template in TEMPLATES else "strength"
    try:
        on_date = _parse_log_date(date)
    except session_templates.TemplateError:
        on_date = None  # a nonsense ?date= falls back to today rather than erroring
    ctx = _log_page_context(request, conn, dictionary, user_id, active=active, on_date=on_date,
                            parse_ref=(parse, piece) if parse is not None else None)
    return templates.TemplateResponse(request, "log_session.html", ctx)


# --- LLM-assisted logging ------------------------------------------------------------

def _parse_context(conn, dictionary, user_id, run_id, piece_index, style_info, on_date) -> dict | None:
    """Everything the log page needs to show draft piece `piece_index` of a
    parse run: the pre-fill, the run's "check these" lines, and where it
    sits in the run. None if the run isn't this user's or has no such piece.
    """
    run = db.get_parse_run(conn, run_id, user_id=user_id)
    if run is None or not run["output_json"]:
        return None
    draft = json.loads(run["output_json"])
    pieces = draft.get("pieces") or []
    if not 0 <= piece_index < len(pieces):
        return None

    date_str = on_date or date.today().isoformat()
    if draft.get("date"):
        try:
            date_str = _parse_log_date(draft["date"])
        except session_templates.TemplateError:
            pass  # a future or unreadable date in the note: keep the default, the form shows it

    def default_style(exercise_id):
        return style_info[exercise_id]["default"]

    prefill = parse_entry.draft_to_prefill(pieces[piece_index], dictionary, default_style, date_str)
    rows = max((int(k[1:k.index("_")]) + 1 for k in prefill.fields if k[:1] in "ms" and "_" in k
                and k[1:k.index("_")].isdigit()), default=0)
    return {
        "run_id": run_id,
        "piece_index": piece_index,
        "piece_count": len(pieces),
        "prefill": prefill.to_dict(),
        "uncertain": draft.get("uncertain") or [],
        "raw_text": run["raw_text"],
        "has_image": run["image_type"] is not None,
        "rows": rows,
    }


def _parse_next_url(run_id: int, piece_index: int, piece_count: int, template: str, on_date: str) -> str:
    if piece_index + 1 < piece_count:
        return f"/log?parse={run_id}&piece={piece_index + 1}"
    return f"/log?template={template}&date={on_date}"


@app.post("/log/parse")
async def log_parse(
    request: Request,
    text: str = Form(""),
    image: UploadFile | None = File(None),
    conn=Depends(get_conn),
    dictionary=Depends(get_dictionary),
):
    """A note or photo -> a parse run -> the first draft piece in the form."""
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id

    def fail(message, status=400):
        ctx = _log_page_context(request, conn, dictionary, user_id, error=message)
        ctx["note_text"] = text
        return templates.TemplateResponse(request, "log_session.html", ctx, status_code=status)

    if parse_entry.backend() is None:
        return fail("Logging from a note isn't switched on here.")
    limit = int(os.environ.get("TRAINING_PARSES_PER_DAY", "40"))
    # parse_runs.created_at is UTC ISO text, so compare against local midnight in UTC.
    midnight = (datetime.combine(date.today(), datetime.min.time()).astimezone()
                .astimezone(timezone.utc).isoformat(timespec="seconds"))
    if db.count_parse_runs_since(conn, user_id, midnight) >= limit:
        return fail(f"You've reached today's limit of {limit} notes. You can still log with the form.", 429)

    img = None
    if image is not None and image.filename:
        data = await image.read()
        if image.content_type not in parse_entry.ALLOWED_IMAGE_TYPES:
            return fail("Use a JPEG, PNG, WebP or GIF photo.")
        if len(data) > parse_entry.MAX_IMAGE_BYTES:
            return fail("That photo is over 5 MB. Try a smaller one.")
        img = (data, image.content_type)
    if not text.strip() and img is None:
        return fail("Write what you did, or add a photo.")

    result = parse_entry.parse(text, img, dictionary, date.today())
    run_id = db.create_parse_run(
        conn, user_id, "photo" if img else "text", text, result.model, parse_entry.PROMPT_VERSION,
        json.dumps(result.draft) if result.draft is not None else None, result.error,
        latency_ms=result.latency_ms, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        image_type=img[1] if img else None, image_bytes=img[0] if img else None,
    )
    if not result.ok:
        return fail(f"Couldn't read that note ({result.error}). Try rephrasing, or use the form.", 502)
    return RedirectResponse(f"/log?parse={run_id}&piece=0", status_code=303)


@app.post("/log/parse/skip")
def log_parse_skip(
    request: Request,
    parse_run_id: int = Form(...),
    piece_index: int = Form(...),
    prefill_json: str = Form("{}"),
    conn=Depends(get_conn),
):
    """Drop one draft piece without logging it, and move to the next."""
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id
    run = db.get_parse_run(conn, parse_run_id, user_id=user_id)
    if run is None:
        return RedirectResponse("/log", status_code=303)
    count = len(json.loads(run["output_json"] or "{}").get("pieces") or [])
    db.record_parse_outcome(conn, parse_run_id, piece_index, "skipped",
                            _safe_json(prefill_json), None, None)
    return RedirectResponse(_parse_next_url(parse_run_id, piece_index, count, "strength",
                                            date.today().isoformat()), status_code=303)


def _safe_json(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _validate_style(dictionary, exercise_id: str, style: str) -> None:
    exercise = dictionary.exercises.get(exercise_id)
    if exercise is None:
        raise session_templates.TemplateError(f"Unknown exercise {exercise_id!r}.")
    if style not in exercise.admissible_styles:
        raise session_templates.TemplateError(
            f"{exercise.name} can't be logged as {style} "
            f"(it allows: {', '.join(exercise.admissible_styles)})."
        )


def _movements_from_form(form, dictionary, require_amount: bool = True) -> list[session_templates.MovementInput]:
    """Reads the movement rows, skipping blank ones.

    Each row is one amount plus a unit; which column that lands in
    (reps/distance/duration_seconds) is decided here rather than by the
    person filling the form. `require_amount` is False for the ladder
    template, where per-round reps come from the rep scheme instead.
    """
    movements = []
    for i in range(MAX_MOVEMENT_ROWS):
        exercise_id = (form.get(f"m{i}_exercise_id") or "").strip()
        if not exercise_id:
            continue

        style = (form.get(f"m{i}_style") or "").strip()
        if exercise_id == REST_ID and movements:
            # Rest has no stimulus of its own, so giving it a style of its
            # own would split the piece into an extra block for no reason.
            # It sits in whatever the movement before it was.
            style = movements[-1].style
        _validate_style(dictionary, exercise_id, style)

        amount = _opt_float(form.get(f"m{i}_amount"))
        unit = (form.get(f"m{i}_unit") or "reps").strip()
        if unit not in UNITS:
            raise session_templates.TemplateError(f"Unknown unit {unit!r}.")
        if amount is None and require_amount:
            name = dictionary.exercises[exercise_id].name
            raise session_templates.TemplateError(f"How much {name}? Fill in the amount, or clear the row.")
        if amount is not None and amount <= 0:
            raise session_templates.TemplateError("Amounts must be positive.")

        movements.append(session_templates.MovementInput(
            exercise_id=exercise_id,
            style=style,
            reps=int(amount) if (amount is not None and unit == "reps") else None,
            distance=amount if unit == "m" else None,
            duration_seconds=int(amount) if (amount is not None and unit == "s") else None,
            load=_opt_float(form.get(f"m{i}_load")),
        ))
    return movements


def _edited_movements_from_form(form, dictionary) -> list[session_templates.EditedMovement]:
    """Read the edit page's per-exercise rows, each carrying a whole amount
    sequence rather than a single number.
    """
    movements = []
    for i in range(MAX_MOVEMENT_ROWS):
        exercise_id = (form.get(f"m{i}_exercise_id") or "").strip()
        if not exercise_id:
            continue

        style = (form.get(f"m{i}_style") or "").strip()
        _validate_style(dictionary, exercise_id, style)

        unit = (form.get(f"m{i}_unit") or "reps").strip()
        if unit not in UNITS:
            raise session_templates.TemplateError(f"Unknown unit {unit!r}.")

        raw = (form.get(f"m{i}_amounts") or "").strip()
        if not raw:
            name = dictionary.exercises[exercise_id].name
            raise session_templates.TemplateError(
                f"How much {name}? Give an amount, or clear the row."
            )
        movements.append(session_templates.EditedMovement(
            exercise_id=exercise_id, style=style,
            amounts=session_templates.parse_amounts(raw),
            unit=unit, load=_opt_float(form.get(f"m{i}_load")),
        ))
    return movements


def _parse_log_date(raw, fallback: str | None = None) -> str:
    """Validate a submitted date, defaulting to today.

    Future dates are refused rather than accepted quietly: advisor's
    build_log_stats() ignores sets dated after the day it's scoring, so a
    piece logged into the future wouldn't count for anything until that day
    arrived - it would look logged and do nothing. A day's leeway absorbs
    the server and the person being on different sides of midnight.
    """
    raw = (raw or "").strip() or fallback or date.today().isoformat()
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise session_templates.TemplateError(
            f"Couldn't read {raw!r} as a date - expected YYYY-MM-DD."
        ) from None
    if parsed > date.today() + timedelta(days=1):
        raise session_templates.TemplateError(
            "That date is in the future. Log what you've done, not what you plan to."
        )
    return parsed.isoformat()


def _opt_int(raw) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def _opt_float(raw) -> float | None:
    raw = (raw or "").strip()
    return float(raw) if raw else None


def _commit_blocks(
    conn, dictionary, user_id: int, blocks, on_date: str | None = None,
    result_seconds: int | None = None,
) -> None:
    """Write expanded BlockSpecs to the log, as today's session by default.

    All blocks from one submission share a piece_group and a summary, so a
    mixed-style piece reads back as the single piece it was rather than as
    one row per style.

    `on_date` is for editing, where the piece may be moved to a different
    day - it lands in that day's session, creating it if it doesn't exist.
    """
    session_id = db.get_or_create_session(conn, user_id, on_date or date.today().isoformat())
    piece_group = db.next_piece_group(conn, session_id)
    summary = session_templates.summarise(
        blocks, {ex.id: ex.name for ex in dictionary.exercises.values()}
    )
    for block in blocks:
        block_id = db.log_block(
            conn, session_id, block.style,
            structure=block.structure, time_cap_seconds=block.time_cap_seconds,
            summary=summary, piece_group=piece_group, result_seconds=result_seconds,
        )
        for spec in block.sets:
            exercise = dictionary.exercises[spec.exercise_id]
            # Unilateral work is logged per side, per GUIDE.md's data model.
            sides = ("L", "R") if exercise.laterality == "unilateral" else (None,)
            for side in sides:
                db.log_set(
                    conn, block_id, spec.exercise_id, dictionary.version,
                    reps=spec.reps, load=spec.load, distance=spec.distance,
                    duration_seconds=spec.duration_seconds, scheme=spec.scheme, side=side,
                )
    conn.commit()


@app.post("/log")
async def log_piece(
    request: Request,
    conn=Depends(get_conn),
    dictionary=Depends(get_dictionary),
):
    """One handler for every template - they differ only in how the form is
    parsed into BlockSpecs, and all commit identically.

    Redirects back to /log rather than to Today, so consecutive pieces can
    be logged without re-navigating; today's progress is shown on the page.
    """
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id

    form = await request.form()
    template = (form.get("template") or "strength").strip()
    parse_ref = None
    if (form.get("parse_run_id") or "").strip():
        try:
            parse_ref = (int(form.get("parse_run_id")), int(form.get("piece_index") or 0))
        except ValueError:
            parse_ref = None

    try:
        on_date = _parse_log_date(form.get("date"))
        result_seconds = _parse_result_time(form.get("result_time"))

        if template == "strength":
            exercise_id = (form.get("exercise_id") or "").strip()
            style = (form.get("style") or "").strip()
            _validate_style(dictionary, exercise_id, style)
            rows = [
                session_templates.StrengthSetInput(
                    reps=_opt_int(form.get(f"s{i}_reps")), load=_opt_float(form.get(f"s{i}_load")),
                )
                for i in range(MAX_SET_ROWS)
            ]
            blocks = session_templates.expand_strength(
                exercise_id, style, rows, scheme=(form.get("scheme") or "").strip() or None,
            )

        elif template == "rounds":
            blocks = session_templates.expand_rounds(
                rounds=_opt_int(form.get("rounds")) or 0,
                movements=_movements_from_form(form, dictionary),
                time_cap_seconds=_time_cap_seconds(form),
            )

        elif template == "ladder":
            every = _opt_int(form.get("ladder_every_minutes"))
            blocks = session_templates.expand_ladder(
                rep_scheme=session_templates.parse_rep_scheme(form.get("rep_scheme") or ""),
                movements=_movements_from_form(form, dictionary, require_amount=False),
                time_cap_seconds=_time_cap_seconds(form),
                rounds=_opt_int(form.get("ladder_rounds")) or 1,
                interval_seconds=every * 60 if every else None,
            )

        elif template == "emom":
            every = _opt_int(form.get("every_minutes")) or 1
            total = _opt_int(form.get("total_minutes")) or 0
            if every < 1:
                raise session_templates.TemplateError("The interval has to be at least a minute.")
            blocks = session_templates.expand_emom(
                intervals=total // every,
                movements=_movements_from_form(form, dictionary),
                interval_seconds=every * 60,
            )

        elif template == "amrap":
            blocks = session_templates.expand_amrap(
                time_cap_seconds=_time_cap_seconds(form) or 0,
                movements=_movements_from_form(form, dictionary),
                rounds_completed=_opt_int(form.get("rounds_completed")) or 0,
                partial_reps=_opt_int(form.get("partial_reps")),
            )

        else:
            raise session_templates.TemplateError(f"Unknown template {template!r}.")

    except session_templates.TemplateError as exc:
        ctx = _log_page_context(request, conn, dictionary, user_id, error=str(exc), active=template,
                                parse_ref=parse_ref)
        return templates.TemplateResponse(request, "log_session.html", ctx, status_code=400)
    except ValueError:
        ctx = _log_page_context(
            request, conn, dictionary, user_id,
            error="Couldn't read one of the numbers - check the form and try again.",
            active=template, parse_ref=parse_ref,
        )
        return templates.TemplateResponse(request, "log_session.html", ctx, status_code=400)

    _commit_blocks(conn, dictionary, user_id, blocks, on_date=on_date,
                   result_seconds=result_seconds)

    if parse_ref is not None:
        run = db.get_parse_run(conn, parse_ref[0], user_id=user_id)
        if run is not None:
            prefill = _safe_json(form.get("prefill_json") or "{}")
            submitted = {k: v for k, v in form.items()
                         if k not in ("parse_run_id", "piece_index", "prefill_json") and isinstance(v, str)}
            edited = parse_entry.comparable(prefill) != parse_entry.comparable(submitted)
            db.record_parse_outcome(conn, parse_ref[0], parse_ref[1], "logged", prefill, submitted, edited)
            count = len(json.loads(run["output_json"] or "{}").get("pieces") or [])
            return RedirectResponse(_parse_next_url(parse_ref[0], parse_ref[1], count, template, on_date),
                                    status_code=303)
    return RedirectResponse(f"/log?template={template}&date={on_date}", status_code=303)


def _time_cap_seconds(form) -> int | None:
    minutes = _opt_int(form.get("time_cap_minutes"))
    return minutes * 60 if minutes else None


def _parse_result_time(raw) -> int | None:
    """How long the piece took, as `mm:ss` or plain minutes -> seconds.

    Distinct from the time *cap*: the cap is what was set beforehand, this
    is the score. Both are on the block, and a capped piece can have both.
    Nothing in scoring reads it - the model has no notion of how fast a
    piece was, only that it happened (see advisor.build_log_stats).
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        if ":" in raw:
            minutes, seconds = raw.split(":", 1)
            total = int(minutes) * 60 + int(seconds)
        else:
            total = round(float(raw) * 60)
    except ValueError:
        raise session_templates.TemplateError(
            f"Couldn't read {raw!r} as a time - try 12:30, or 12.5 for minutes."
        ) from None
    if total <= 0:
        raise session_templates.TemplateError("A time has to be positive.")
    return total


def format_result_time(seconds: int | None) -> str:
    """Seconds -> 'mm:ss', for redisplay and for the piece line."""
    if not seconds:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


# --- Coverage ----------------------------------------------------------------------

@app.get("/coverage", response_class=HTMLResponse)
def coverage(request: Request, conn=Depends(get_conn), dictionary=Depends(get_dictionary)):
    """The live, personal equivalent of simulation_report.md's two tables:
    every admissible (pattern, style) cell with its current score and how
    long since it was literally last trained.

    This is the model's whole state, shown rather than summarised - the
    Today page only ever surfaces each cell's top candidate, so a cell
    that's mildly behind never appears there at all. With no overdue clock
    left in the model (see docs/GUIDE.md's "No hard override"),
    this table is the thing that makes a slowly-neglected cell visible
    before it becomes a problem.
    """
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id

    settings = db.get_user_settings(conn, user_id)
    config = user_config.build_config(settings)
    sets = db.fetch_sets(conn, user_id)
    today = date.today()

    stats = advisor.build_log_stats(dictionary, sets, today, config)

    rows = []
    for pattern, style in advisor.admissible_cells(dictionary, config):
        ewma = stats.cell_ewma.get((pattern, style), 0.0)
        last_seen = stats.cell_last_seen.get((pattern, style))
        score = advisor.ewma_score(ewma, config.memory_k)
        rows.append({
            "pattern": pattern,
            "style": style,
            "score": score,
            # Formatted here rather than in the template: Jinja2 has no
            # float("inf") to compare against, and math.inf needs a custom
            # filter to render as anything but "inf" via format().
            "score_display": "never trained" if math.isinf(score) else f"{score:+.2f}",
            "days_since": (today - last_seen).days if last_seen else None,
            "target_interval_days": advisor.tau_cell(pattern, style, config) / config.memory_k,
        })
    # Worst (most behind) first - same ordering the simulation report uses,
    # so the two are read the same way.
    rows.sort(key=lambda r: r["score"], reverse=True)

    # Bar width, anchored to log(2) rather than to the worst score on the
    # page. log(2) is the score a cell settles at under chronic training at
    # exactly half its target rate (see advisor.ewma_score), so it's a
    # meaningful "clearly behind" reference rather than an arbitrary one:
    # half-rate reads as a half-width bar, twice that fills it.
    #
    # Scaling relative to the page's own maximum was the alternative and is
    # worse twice over - a page of near-identical scores renders every bar
    # full-width, and the same score means a different width depending on
    # what else happens to be on screen.
    full_scale = 2 * math.log(2)
    for r in rows:
        r["bar_pct"] = (
            100 if math.isinf(r["score"])
            else min(100, round(100 * abs(r["score"]) / full_scale))
        )

    return templates.TemplateResponse(request, "coverage.html", {
        "rows": rows,
        "disabled_styles": sorted(config.disabled_styles),
        "inf": math.inf,
        "active_nav": "coverage",
    })


# --- History -----------------------------------------------------------------------

@app.get("/history", response_class=HTMLResponse)
def history(request: Request, conn=Depends(get_conn), dictionary=Depends(get_dictionary)):
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id
    return templates.TemplateResponse(request, "history.html", {
        "pieces": _with_volume_text(db.fetch_pieces(conn, user_id), dictionary),
        "active_nav": "history",
    })


@app.post("/history/delete")
def delete_piece(
    request: Request,
    session_id: int = Form(...),
    piece_group: int = Form(...),
    conn=Depends(get_conn),
):
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id
    # delete_piece() re-checks ownership itself and no-ops if the piece isn't
    # this user's, so a hand-edited form can't reach someone else's log.
    db.delete_piece(conn, user_id, session_id, piece_group)
    return RedirectResponse("/history", status_code=303)


def _number(value) -> str:
    """Render an amount without a pointless trailing .0 (400.0 -> '400')."""
    if value is None:
        return ""
    return f"{float(value):g}"


def _edit_page_context(conn, dictionary, user_id, piece, error=None):
    """One row per *exercise*, not per set.

    A 21-15-9 is six stored sets but two movements, and two rows is how a
    person describes it. Sets are grouped by (exercise, style, load, unit)
    in first-appearance order, and their amounts become one editable
    sequence - which also makes the round count editable, since adding a
    number to the sequence adds a round.
    """
    exercises = sorted(dictionary.exercises.values(), key=lambda ex: ex.name)

    grouped: dict[tuple, dict] = {}
    for s in piece["sets"]:
        # Unilateral work is stored per side; showing both would double every
        # amount, so only the left is offered and saving re-expands to both.
        if s.get("side") == "R":
            continue
        if s.get("distance"):
            amount, unit = s["distance"], "m"
        elif s.get("duration_seconds"):
            amount, unit = s["duration_seconds"], "s"
        else:
            amount, unit = s.get("reps"), "reps"

        key = (s["exercise_id"], s.get("style"), s.get("load"), unit)
        if key not in grouped:
            grouped[key] = {
                "exercise_id": s["exercise_id"], "style": s.get("style"),
                "load": s.get("load"), "unit": unit, "amounts": [],
            }
        grouped[key]["amounts"].append(amount)

    rows = []
    for row in grouped.values():
        row["amounts_text"] = "-".join(_number(a) for a in row["amounts"] if a is not None)
        row["load_text"] = _number(row["load"])
        rows.append(row)

    return {
        "piece": piece,
        "result_text": format_result_time(piece.get("result_seconds")),
        "rows": rows,
        "exercises": exercises,
        "styles": dictionary.styles,
        "style_labels": STYLE_LABELS,
        "units": UNITS,
        "max_rows": MAX_MOVEMENT_ROWS,
        "error": error,
        "active_nav": "history",
    }


@app.get("/history/edit", response_class=HTMLResponse)
def edit_form(
    request: Request,
    session_id: int,
    piece_group: int,
    conn=Depends(get_conn),
    dictionary=Depends(get_dictionary),
):
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id

    piece = db.fetch_piece(conn, user_id, session_id, piece_group)
    if piece is None:
        return RedirectResponse("/history", status_code=303)
    return templates.TemplateResponse(
        request, "edit_piece.html", _edit_page_context(conn, dictionary, user_id, piece)
    )


@app.post("/history/edit")
async def edit_piece(
    request: Request,
    conn=Depends(get_conn),
    dictionary=Depends(get_dictionary),
):
    """Rewrite a piece from the edited rows.

    Saving deletes the piece and writes it again rather than patching rows
    in place: a set whose style changed has to move to a different block
    (a block holds one style), and re-expanding through the same path the
    log form uses keeps that correct without a second implementation.
    """
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id

    form = await request.form()
    session_id = _opt_int(form.get("session_id"))
    piece_group = _opt_int(form.get("piece_group"))
    piece = db.fetch_piece(conn, user_id, session_id, piece_group)
    if piece is None:
        return RedirectResponse("/history", status_code=303)

    try:
        movements = _edited_movements_from_form(form, dictionary)
        if not movements:
            raise session_templates.TemplateError(
                "A piece needs at least one movement. To remove it entirely, delete it."
            )
        new_date = _parse_log_date(form.get("date"), fallback=piece["date"])
        result_seconds = _parse_result_time(form.get("result_time"))
        structure = (form.get("structure") or "").strip() or None
        blocks = session_templates.expand_edited(
            movements, structure, piece["time_cap_seconds"]
        )
    except session_templates.TemplateError as exc:
        ctx = _edit_page_context(conn, dictionary, user_id, piece, error=str(exc))
        return templates.TemplateResponse(request, "edit_piece.html", ctx, status_code=400)
    except ValueError:
        ctx = _edit_page_context(
            conn, dictionary, user_id, piece,
            error="Couldn't read one of the numbers - check the form and try again.",
        )
        return templates.TemplateResponse(request, "edit_piece.html", ctx, status_code=400)

    db.delete_piece(conn, user_id, session_id, piece_group)
    _commit_blocks(conn, dictionary, user_id, blocks, on_date=new_date,
                   result_seconds=result_seconds)
    return RedirectResponse("/history", status_code=303)


# --- Settings ------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request, conn=Depends(get_conn), dictionary=Depends(get_dictionary)):
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id
    settings = db.get_user_settings(conn, user_id)
    return templates.TemplateResponse(request, "settings.html", {
        "settings": settings, "styles": dictionary.styles, "active_nav": "settings",
    })


@app.post("/settings")
def save_settings(
    request: Request,
    volume_multiplier: float = Form(1.0),
    conditioning_focus: float = Form(1.0),
    disabled_styles: list[str] = Form([]),
    conn=Depends(get_conn),
):
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id
    db.save_user_settings(conn, user_id, volume_multiplier, conditioning_focus, disabled_styles)
    return RedirectResponse("/settings", status_code=303)


# --- Admin -------------------------------------------------------------------------
#
# For running the app with a few testers: who's signed up, their logs
# (read-only), and how well the note parser is doing. Admins are the
# accounts listed in TRAINING_ADMIN_EMAILS; the session's is_admin flag only
# shows the nav link, and every route here re-checks the email.

def _require_admin(request: Request, conn) -> int | Response:
    user_id = _require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id
    row = db.get_user_by_id(conn, user_id)
    if row is None or row["email"] not in _admin_emails():
        return HTMLResponse("Admins only.", status_code=403)
    return user_id


def _parse_stats(runs: list[dict]) -> dict:
    outcomes = [o for r in runs for o in r["outcomes"]]
    logged = [o for o in outcomes if o["action"] == "logged"]
    unknown: dict[str, int] = {}
    pieces = 0
    for r in runs:
        draft = json.loads(r["output_json"]) if r["output_json"] else {}
        for piece in draft.get("pieces") or []:
            pieces += 1
            items = [piece, *(piece.get("movements") or [])]
            for item in items:
                if item.get("unresolved_name") and not item.get("exercise_id"):
                    name = item["unresolved_name"].strip().lower()
                    unknown[name] = unknown.get(name, 0) + 1
    latencies = sorted(r["latency_ms"] for r in runs if r["latency_ms"] and r["model"] != "fake")
    return {
        "runs": len(runs),
        "errors": sum(1 for r in runs if r["error"]),
        "pieces": pieces,
        "logged": len(logged),
        "unedited": sum(1 for o in logged if not o["edited"]),
        "skipped": sum(1 for o in outcomes if o["action"] == "skipped"),
        "median_latency_s": latencies[len(latencies) // 2] / 1000 if latencies else None,
        "tokens_in": sum(r["input_tokens"] or 0 for r in runs),
        "tokens_out": sum(r["output_tokens"] or 0 for r in runs),
        "unknown": sorted(unknown.items(), key=lambda kv: (-kv[1], kv[0])),
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, conn=Depends(get_conn)):
    admin_id = _require_admin(request, conn)
    if isinstance(admin_id, Response):
        return admin_id
    runs = db.list_parse_runs(conn)
    emails = {u["id"]: u["email"] for u in db.users_overview(conn)}
    return templates.TemplateResponse(request, "admin.html", {
        "users": db.users_overview(conn),
        "runs": runs[:50],
        "emails": emails,
        "stats": _parse_stats(runs),
        "parser": parse_entry.backend(),
        "active_nav": "admin",
    })


@app.get("/admin/users/{uid}", response_class=HTMLResponse)
def admin_user_history(uid: int, request: Request, conn=Depends(get_conn), dictionary=Depends(get_dictionary)):
    admin_id = _require_admin(request, conn)
    if isinstance(admin_id, Response):
        return admin_id
    row = db.get_user_by_id(conn, uid)
    if row is None:
        return HTMLResponse("No such user.", status_code=404)
    return templates.TemplateResponse(request, "history.html", {
        "pieces": _with_volume_text(db.fetch_pieces(conn, uid), dictionary),
        "viewing": row["email"],
        "active_nav": "admin",
    })


@app.get("/admin/parse/{run_id}", response_class=HTMLResponse)
def admin_parse_run(run_id: int, request: Request, conn=Depends(get_conn)):
    admin_id = _require_admin(request, conn)
    if isinstance(admin_id, Response):
        return admin_id
    run = db.get_parse_run(conn, run_id)
    if run is None:
        return HTMLResponse("No such parse run.", status_code=404)
    owner = db.get_user_by_id(conn, run["user_id"])
    outcomes = db.parse_outcomes(conn, run_id)
    for o in outcomes:
        prefill = parse_entry.comparable(json.loads(o["prefill_json"] or "{}"))
        submitted = parse_entry.comparable(json.loads(o["submitted_json"] or "{}")) if o["submitted_json"] else {}
        o["changes"] = [
            (key, prefill.get(key, ""), submitted.get(key, ""))
            for key in sorted(set(prefill) | set(submitted))
            if o["submitted_json"] and prefill.get(key, "") != submitted.get(key, "")
        ]
    draft = json.loads(run["output_json"]) if run["output_json"] else None
    return templates.TemplateResponse(request, "admin_parse.html", {
        "run": run,
        "owner": owner["email"] if owner else "?",
        "draft": json.dumps(draft, indent=2, ensure_ascii=False) if draft is not None else None,
        "outcomes": outcomes,
        "active_nav": "admin",
    })


@app.get("/admin/parse/{run_id}/image")
def admin_parse_image(run_id: int, request: Request, conn=Depends(get_conn)):
    admin_id = _require_admin(request, conn)
    if isinstance(admin_id, Response):
        return admin_id
    image = db.get_parse_image(conn, run_id)
    if image is None:
        return HTMLResponse("No image.", status_code=404)
    return Response(image[1], media_type=image[0])


@app.get("/admin/export.json")
def admin_export(request: Request, conn=Depends(get_conn)):
    """Every parse run with its draft and what became of each piece - the
    parser's evaluation set, built from real use."""
    admin_id = _require_admin(request, conn)
    if isinstance(admin_id, Response):
        return admin_id
    out = []
    for run in db.list_parse_runs(conn, limit=100000):
        out.append({
            **{k: run[k] for k in ("id", "user_id", "created_at", "input_kind", "raw_text", "model",
                                   "prompt_version", "error", "latency_ms", "input_tokens", "output_tokens")},
            "has_image": run["image_type"] is not None,
            "draft": json.loads(run["output_json"]) if run["output_json"] else None,
            "outcomes": [{**o, "prefill_json": None, "submitted_json": None,
                          "prefill": json.loads(o["prefill_json"] or "{}"),
                          "submitted": json.loads(o["submitted_json"]) if o["submitted_json"] else None}
                         for o in run["outcomes"]],
        })
    return JSONResponse(out, headers={"Content-Disposition": "attachment; filename=parse_eval.json"})

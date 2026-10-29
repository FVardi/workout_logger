"""End-to-end tests for the training webapp (src/training/webapp/).

Skipped entirely unless the webapp extra is installed (`pip install -e
.[webapp]` plus httpx for the test client) - the rest of the training
domain doesn't need a web server to be tested, and shouldn't fail to
collect just because FastAPI isn't present.

Each test gets its own temporary SQLite file via the TRAINING_DB_PATH
environment variable, so nothing here can touch a real training log.
"""

import datetime
import json
import re

import pytest

pytest.importorskip("fastapi", reason="webapp extra not installed")
pytest.importorskip("httpx", reason="httpx needed for fastapi's TestClient")

from fastapi.testclient import TestClient  # noqa: E402

from training import advisor, db  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAINING_DB_PATH", str(tmp_path / "test.db"))
    from training.webapp.app import app
    return TestClient(app)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


def signup(client, email="u@example.com", password="hunter2pass"):
    return client.post(
        "/signup", data={"email": email, "password": password}, follow_redirects=False
    )


# --- Auth ----------------------------------------------------------------------

def test_signup_logs_you_in_and_redirects_home(client):
    r = signup(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_pages_require_login(client):
    for path in ("/", "/log", "/coverage", "/history", "/settings"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert r.headers["location"] == "/login", path


def test_logout_ends_the_session(client):
    signup(client)
    client.post("/logout", follow_redirects=False)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_duplicate_email_is_rejected(client):
    signup(client)
    client.post("/logout", follow_redirects=False)
    r = signup(client)
    assert r.status_code == 400
    assert "already registered" in r.text


def test_short_password_is_rejected(client):
    r = client.post("/signup", data={"email": "x@example.com", "password": "short"})
    assert r.status_code == 400


def test_password_over_72_bytes_is_rejected_not_a_500(client):
    """bcrypt only ever uses a password's first 72 bytes - some bcrypt
    versions truncate silently, others (>=4.1) raise ValueError instead of
    truncating. Whichever is installed, this must be a clean 400, not an
    unhandled exception - see auth.py's MAX_PASSWORD_BYTES.
    """
    r = client.post("/signup", data={"email": "x@example.com", "password": "x" * 100})
    assert r.status_code == 400
    assert "72 bytes" in r.text


def test_password_length_is_measured_in_bytes_not_characters(client):
    """A password can be short in characters but long in UTF-8 bytes -
    multi-byte characters (e.g. accented letters, emoji) push the encoded
    length up faster than the character count suggests.
    """
    # 30 grave-accented "a"s: 2 bytes each in UTF-8 = 60 bytes, under the
    # limit on its own, but combined with an ASCII suffix pushes it over.
    password = ("à" * 30) + "x" * 20  # 60 + 20 = 80 bytes, 50 characters
    assert len(password) < 72  # short by character count
    r = client.post("/signup", data={"email": "x@example.com", "password": password})
    assert r.status_code == 400
    assert "72 bytes" in r.text


def test_wrong_password_is_rejected(client):
    signup(client)
    client.post("/logout", follow_redirects=False)
    r = client.post("/login", data={"email": "u@example.com", "password": "wrongpassword"})
    assert r.status_code == 400
    assert "Wrong email or password" in r.text


# --- Logging -------------------------------------------------------------------

def test_logging_a_piece_updates_todays_suggestions_immediately(client):
    """Advice is rescored from the log on every request, so what you just
    logged is reflected the next time you look at Today.

    Asserted via the coverage page's own numbers rather than Today's
    markup: the point is that the model reacts, and pinning that to exact
    HTML makes every restyle look like a regression.
    """
    signup(client)
    assert "never trained" in client.get("/coverage").text

    client.post("/log", data=strength_form(rows=[(5, 100)] * 5, scheme="5x5"),
                follow_redirects=False)

    # squat/heavy has a real score now; one exposure against k=2 reads log(2).
    assert "+0.69" in client.get("/coverage").text


def strength_form(exercise_id="back_squat", style="heavy", rows=((5, 100),), scheme=None):
    data = {"template": "strength", "exercise_id": exercise_id, "style": style}
    if scheme:
        data["scheme"] = scheme
    for i, (reps, load) in enumerate(rows):
        data[f"s{i}_reps"] = str(reps)
        data[f"s{i}_load"] = str(load)
    return data


def movement_row(i, exercise_id, style, amount=None, unit="reps", load=None):
    """Build one movement row's form fields, matching the log page's naming."""
    data = {f"m{i}_exercise_id": exercise_id, f"m{i}_style": style, f"m{i}_unit": unit}
    if amount is not None:
        data[f"m{i}_amount"] = str(amount)
    if load is not None:
        data[f"m{i}_load"] = str(load)
    return data


def test_every_template_page_renders(client):
    signup(client)
    for template in ("strength", "rounds", "ladder", "emom", "amrap"):
        r = client.get(f"/log?template={template}")
        assert r.status_code == 200, template


def test_log_page_offers_all_types_in_one_dropdown(client):
    """Every type's form is rendered up front and toggled client-side, so
    switching types can't lose half-filled input to a page reload.
    """
    signup(client)
    text = client.get("/log").text
    assert 'id="template-picker"' in text
    for template in ("strength", "rounds", "ladder", "emom", "amrap"):
        assert f'data-template="{template}"' in text
    # The engine type is gone - run/row/bike are ordinary movements now.
    assert 'data-template="engine"' not in text


def test_movement_rows_accept_reps_metres_or_seconds(client, db_path):
    """One amount plus a unit per row, routed to the right column here
    rather than asking the person to know which field to use.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "thruster", "hhr", amount=10, unit="reps", load=43))
    data.update(movement_row(1, "running", "light", amount=400, unit="m"))
    data.update(movement_row(2, "farmers_carry", "hhr", amount=60, unit="s", load=32))

    r = client.post("/log", data=data, follow_redirects=False)
    assert r.status_code == 303

    conn = db.connect(db_path)

    def one(exercise_id):
        return conn.execute(
            "SELECT reps, load, distance, duration_seconds FROM sets WHERE exercise_id = ? LIMIT 1",
            (exercise_id,),
        ).fetchone()

    thruster = one("thruster")
    assert (thruster["reps"], thruster["load"]) == (10, 43.0)
    assert thruster["distance"] is None and thruster["duration_seconds"] is None

    run = one("running")
    assert run["distance"] == 400.0
    assert run["reps"] is None and run["duration_seconds"] is None

    carry = one("farmers_carry")
    assert carry["duration_seconds"] == 60
    assert carry["reps"] is None and carry["distance"] is None
    conn.close()


def test_movement_row_without_an_amount_is_rejected(client):
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "thruster", "hhr", unit="reps"))
    r = client.post("/log", data=data)
    assert r.status_code == 400
    assert "Fill in the amount" in r.text


def test_ladder_rows_do_not_need_an_amount(client):
    """Per-round reps come from the rep scheme there, so requiring an
    amount per row would be asking for the same number twice.
    """
    signup(client)
    data = {"template": "ladder", "rep_scheme": "21-15-9"}
    data.update(movement_row(0, "thruster", "hhr", load=43))
    data.update(movement_row(1, "strict_pull_up", "hhr"))
    r = client.post("/log", data=data, follow_redirects=False)
    assert r.status_code == 303


def test_unknown_unit_is_rejected(client):
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "thruster", "hhr", amount=10, unit="furlongs"))
    r = client.post("/log", data=data)
    assert r.status_code == 400


def test_negative_amount_is_rejected(client):
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "thruster", "hhr", amount=-5, unit="reps"))
    r = client.post("/log", data=data)
    assert r.status_code == 400


def test_rows_beyond_the_initially_rendered_three_are_read(client, db_path):
    """The page renders three rows and adds more client-side, so the server
    has to scan past three - otherwise added rows would silently vanish.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "1"}
    for i, exercise_id in enumerate(
        ["thruster", "strict_pull_up", "bent_over_row", "farmers_carry", "deadlift"]
    ):
        data.update(movement_row(i, exercise_id, "hhr", amount=5, unit="reps"))
    r = client.post("/log", data=data, follow_redirects=False)
    assert r.status_code == 303

    conn = db.connect(db_path)
    logged = {r["exercise_id"] for r in conn.execute("SELECT DISTINCT exercise_id FROM sets")}
    assert logged == {"thruster", "strict_pull_up", "bent_over_row", "farmers_carry", "deadlift"}
    conn.close()


def test_strength_template_preserves_a_per_set_load_wave(client, db_path):
    """The motivating case: 5@80, 4@85, 3@90, 2@95, 1@100 is one piece whose
    per-set loads must survive into the log, not be flattened.
    """
    signup(client)
    r = client.post(
        "/log", data=strength_form(rows=[(5, 80), (4, 85), (3, 90), (2, 95), (1, 100)]),
        follow_redirects=False,
    )
    assert r.status_code == 303

    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT reps, load FROM sets WHERE exercise_id = 'back_squat' ORDER BY set_order"
    ).fetchall()
    assert [(r["reps"], r["load"]) for r in rows] == [
        (5, 80.0), (4, 85.0), (3, 90.0), (2, 95.0), (1, 100.0)
    ]
    structure = conn.execute("SELECT structure FROM blocks").fetchone()["structure"]
    assert structure == "5/4/3/2/1"
    conn.close()


def test_many_sets_of_one_exercise_still_count_as_one_exposure(client, db_path):
    """Set rows are for volume/PR analysis; exposures are what the model
    scores, and same-day/style/exercise collapses to one. Asserted against
    back squat alone (no other exercise feeding squat/heavy) so the number
    is unambiguous.
    """
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 80)] * 5), follow_redirects=False)

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 5
    stats = advisor.build_log_stats(
        advisor.load_dictionary(), db.fetch_sets(conn, 1), datetime.date.today()
    )
    assert stats.cell_ewma[("squat", "heavy")] == pytest.approx(1.0)
    conn.close()


def test_ladder_template_applies_the_rep_scheme(client, db_path):
    signup(client)
    r = client.post("/log", data={
        "template": "ladder", "rep_scheme": "21-15-9",
        "m0_exercise_id": "thruster", "m0_style": "hhr", "m0_load": "43",
        "m1_exercise_id": "strict_pull_up", "m1_style": "hhr",
    }, follow_redirects=False)
    assert r.status_code == 303

    conn = db.connect(db_path)
    reps = [r["reps"] for r in conn.execute(
        "SELECT reps FROM sets WHERE exercise_id = 'thruster' ORDER BY set_order"
    )]
    assert reps == [21, 15, 9]
    assert conn.execute("SELECT structure FROM blocks").fetchone()["structure"] == "21-15-9"
    conn.close()


def test_mixed_style_metcon_splits_into_one_block_per_style(client, db_path):
    """A heavy triple into a run: heavy stimulus inside a workout container.
    Style is per movement, so this becomes two blocks sharing a structure
    label - and crucially the squat stays `heavy`, not downgraded to match
    the run.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "back_squat", "heavy", amount=3, unit="reps", load=140))
    data.update(movement_row(1, "running", "light", amount=400, unit="m"))
    r = client.post("/log", data=data, follow_redirects=False)
    assert r.status_code == 303

    conn = db.connect(db_path)
    blocks = conn.execute("SELECT id, style, structure FROM blocks ORDER BY block_order").fetchall()
    assert len(blocks) == 2
    assert {b["style"] for b in blocks} == {"heavy", "light"}
    assert all(b["structure"] == "3 RFT" for b in blocks)

    stats = advisor.build_log_stats(
        advisor.load_dictionary(), db.fetch_sets(conn, 1), datetime.date.today()
    )
    assert stats.cell_ewma[("squat", "heavy")] > 0
    assert stats.cell_ewma[("engine", "light")] > 0
    conn.close()


def test_amrap_records_rounds_and_time_cap(client, db_path):
    signup(client)
    data = {
        "template": "amrap", "time_cap_minutes": "12",
        "rounds_completed": "8", "partial_reps": "3",
    }
    data.update(movement_row(0, "burpee", "light", amount=5, unit="reps"))
    data.update(movement_row(1, "air_squat", "light", amount=10, unit="reps"))
    r = client.post("/log", data=data, follow_redirects=False)
    assert r.status_code == 303

    conn = db.connect(db_path)
    block = conn.execute("SELECT structure, time_cap_seconds FROM blocks").fetchone()
    assert block["structure"] == "AMRAP 12"
    assert block["time_cap_seconds"] == 720
    # 8 rounds x 2 movements, plus one partial set of the first movement.
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 17
    conn.close()


def test_unilateral_logs_both_sides_per_set(client, db_path):
    signup(client)
    client.post("/log", data=strength_form(exercise_id="db_lunge", rows=[(8, 20)] * 3),
                follow_redirects=False)

    conn = db.connect(db_path)
    rows = conn.execute("SELECT side FROM sets WHERE exercise_id = 'db_lunge'").fetchall()
    assert len(rows) == 6  # 3 sets x 2 sides
    assert sorted({r["side"] for r in rows}) == ["L", "R"]
    conn.close()


def test_inadmissible_exercise_style_pair_is_rejected(client):
    signup(client)
    r = client.post("/log", data=strength_form(style="light"))
    assert r.status_code == 400
    assert "can&#39;t be logged as light" in r.text


def test_inadmissible_style_inside_a_metcon_row_is_rejected(client):
    signup(client)
    r = client.post("/log", data={
        "template": "rounds", "rounds": "3",
        "m0_exercise_id": "push_up", "m0_style": "hhr", "m0_amount": "10", "m0_unit": "reps",
    })
    assert r.status_code == 400


def test_empty_strength_form_is_rejected(client):
    signup(client)
    r = client.post("/log", data={"template": "strength", "exercise_id": "back_squat", "style": "heavy"})
    assert r.status_code == 400


def test_metcon_with_no_movements_is_rejected(client):
    signup(client)
    r = client.post("/log", data={"template": "rounds", "rounds": "3"})
    assert r.status_code == 400


def test_logging_stays_on_the_log_page(client):
    """Consecutive pieces shouldn't require re-navigating - the redirect
    goes back to /log, not to Today.
    """
    signup(client)
    r = client.post("/log", data=strength_form(), follow_redirects=False)
    assert r.headers["location"].startswith("/log")


def test_today_panel_shows_what_was_logged(client):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    text = client.get("/log").text
    assert "back squat" in text.lower()
    assert "Nothing logged yet today" not in text


# --- Pieces read back as one row --------------------------------------------------

def test_a_multi_round_piece_is_one_row_not_one_per_set(client):
    """Three rounds of two movements is six set rows in storage and one
    piece to read. The history page shows the piece.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "air_squat", "light", amount=20, unit="reps"))
    data.update(movement_row(1, "double_unders", "light", amount=40, unit="reps"))
    client.post("/log", data=data, follow_redirects=False)

    text = client.get("/history").text
    assert "3 RFT: 20 air squats, 40 double-unders" in text
    # Volume totals across the whole piece, not per round.
    assert "60 air squats, 120 double-unders" in text
    # One data row (plus the header row).
    assert text.count("<tr>") == 2


def test_a_mixed_style_piece_is_still_one_row(client):
    """It's stored as two blocks because a block holds one style, but it
    was one piece and reads back as one.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "back_squat", "heavy", amount=3, unit="reps", load=140))
    data.update(movement_row(1, "running", "light", amount=400, unit="m"))
    client.post("/log", data=data, follow_redirects=False)

    text = client.get("/history").text
    assert text.count("<tr>") == 2  # header + one piece
    assert "heavy, light" in text
    assert "9 back squats, 1200 m running" in text


def test_separate_pieces_stay_separate_rows(client):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "air_squat", "light", amount=20, unit="reps"))
    client.post("/log", data=data, follow_redirects=False)

    text = client.get("/history").text
    assert text.count("<tr>") == 3  # header + two pieces


def test_summary_is_stored_not_recomputed(client, db_path):
    """Stored at log time so the record survives dictionary changes - the
    same reasoning as stamping dict_version on each set.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "air_squat", "light", amount=20, unit="reps"))
    client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    summary = conn.execute("SELECT summary FROM blocks").fetchone()["summary"]
    assert summary == "3 RFT: 20 air squats"
    conn.close()


def test_blocks_of_one_submission_share_a_piece_group(client, db_path):
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "back_squat", "heavy", amount=3, unit="reps"))
    data.update(movement_row(1, "running", "light", amount=400, unit="m"))
    client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    groups = [r["piece_group"] for r in conn.execute("SELECT piece_group FROM blocks")]
    assert len(groups) == 2
    assert len(set(groups)) == 1
    conn.close()


def test_unilateral_volume_sums_both_sides(client):
    """Logged per side, so 3 sets of 8 per side totals 48 - the honest rep
    count, even though the programmed number was "8 each side".
    """
    signup(client)
    client.post("/log", data=strength_form(exercise_id="db_lunge", rows=[(8, 20)] * 3),
                follow_redirects=False)
    assert "48 dumbbell lunges" in client.get("/history").text


# --- Logging to a date other than today -------------------------------------------

def test_log_form_has_a_date_per_template_defaulting_to_today(client):
    """Each workout type is its own form element, so each needs its own date
    field; they're kept in sync client-side.
    """
    signup(client)
    text = client.get("/log").text
    assert len(re.findall(r'name="date"', text)) == len(
        ("strength", "rounds", "ladder", "emom", "amrap")
    )
    assert f'value="{datetime.date.today().isoformat()}"' in text


def test_a_piece_can_be_logged_to_a_past_date(client):
    signup(client)
    past = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()

    data = strength_form(rows=[(5, 100)])
    data["date"] = past
    assert client.post("/log", data=data, follow_redirects=False).status_code == 303
    assert past in client.get("/history").text


def test_backdated_work_scores_on_the_day_it_happened(client, db_path):
    """The date isn't just a label - it's what the EWMA decays from."""
    signup(client)
    past = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    data = strength_form(rows=[(5, 100)])
    data["date"] = past
    client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    stats = advisor.build_log_stats(
        advisor.load_dictionary(), db.fetch_sets(conn, 1), datetime.date.today()
    )
    assert stats.cell_last_seen[("squat", "heavy")].isoformat() == past
    conn.close()


def test_the_already_logged_panel_follows_the_chosen_date(client):
    """Backdating should show what else is on that day, not what's on today."""
    signup(client)
    past = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    data = strength_form(rows=[(5, 100)])
    data["date"] = past
    client.post("/log", data=data, follow_redirects=False)

    on_past = client.get(f"/log?date={past}").text
    assert "Logged on" in on_past and "back squats" in on_past

    on_today = client.get("/log").text
    assert "Today so far" not in on_today or "back squats" not in on_today


def test_a_future_date_is_refused(client):
    """build_log_stats ignores sets dated after the day it's scoring, so a
    future-dated piece would look logged and count for nothing.
    """
    signup(client)
    data = strength_form(rows=[(5, 100)])
    data["date"] = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    r = client.post("/log", data=data)
    assert r.status_code == 400
    assert "in the future" in r.text


def test_tomorrow_is_allowed_as_timezone_leeway(client):
    """One day's slack absorbs the server and the person being on different
    sides of midnight; it isn't an invitation to log next week.
    """
    signup(client)
    data = strength_form(rows=[(5, 100)])
    data["date"] = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert client.post("/log", data=data, follow_redirects=False).status_code == 303


def test_an_unreadable_date_is_refused(client):
    signup(client)
    data = strength_form(rows=[(5, 100)])
    data["date"] = "last tuesday"
    r = client.post("/log", data=data)
    assert r.status_code == 400
    assert "YYYY-MM-DD" in r.text


def test_a_nonsense_date_in_the_query_string_falls_back_to_today(client):
    """A bad ?date= is a stray link, not a submission - showing today beats
    an error page.
    """
    signup(client)
    r = client.get("/log?date=whenever")
    assert r.status_code == 200
    assert f'value="{datetime.date.today().isoformat()}"' in r.text


def test_backdating_joins_an_existing_session_for_that_day(client, db_path):
    signup(client)
    past = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    for rows in ([(5, 100)], [(3, 120)]):
        data = strength_form(rows=rows)
        data["date"] = past
        client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert len(db.fetch_pieces(conn, 1)) == 2
    conn.close()


# --- Result time -------------------------------------------------------------------

def bike_sandwich(rounds=4, **extra):
    """4 rounds of: 2 min bike, 10 T2B, 2 min bike, 60 DU, 2 min bike, 10 burpees.

    The shape that exposed the summary bug - one movement appearing three
    times in a single round.
    """
    data = {"template": "rounds", "rounds": str(rounds), **extra}
    rows = [("biking", 120, "s"), ("toes_to_bar", 10, "reps"), ("biking", 120, "s"),
            ("double_unders", 60, "reps"), ("biking", 120, "s"), ("burpee", 10, "reps")]
    for i, (exercise_id, amount, unit) in enumerate(rows):
        data.update(movement_row(i, exercise_id, "light", amount=amount, unit=unit))
    return data


def test_a_result_time_can_be_logged_and_shown(client, db_path):
    signup(client)
    assert client.post("/log", data=bike_sandwich(result_time="18:42"),
                       follow_redirects=False).status_code == 303

    conn = db.connect(db_path)
    assert db.fetch_pieces(conn, 1)[0]["result_seconds"] == 18 * 60 + 42
    conn.close()
    assert "18:42" in client.get("/history").text


def test_a_result_time_can_be_given_in_plain_minutes(client, db_path):
    signup(client)
    data = strength_form(rows=[(5, 100)])
    data["result_time"] = "7.5"
    client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    assert db.fetch_pieces(conn, 1)[0]["result_seconds"] == 450
    conn.close()
    assert "7:30" in client.get("/history").text


def test_the_result_time_is_optional(client, db_path):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    conn = db.connect(db_path)
    assert db.fetch_pieces(conn, 1)[0]["result_seconds"] is None
    conn.close()


@pytest.mark.parametrize("bad", ["nonsense", "-5", "0", "12:xx"])
def test_an_unreadable_result_time_is_refused(client, bad):
    signup(client)
    data = strength_form(rows=[(5, 100)])
    data["result_time"] = bad
    assert client.post("/log", data=data).status_code == 400


def test_the_result_time_survives_an_edit(client, db_path):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]) | {"result_time": "10:00"},
                follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    r = client.get(f"/history/edit?session_id={session_id}&piece_group={piece_group}")
    assert 'value="10:00"' in r.text

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "5", "result_time": "9:15"}
    data.update(edit_row(0, "back_squat", "heavy", "5", load=100))
    client.post("/history/edit", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    assert db.fetch_pieces(conn, 1)[0]["result_seconds"] == 555
    conn.close()


# --- Summaries of workouts that reuse a movement ----------------------------------

def test_a_movement_used_three_times_a_round_is_described_three_times(client):
    """Grouping by exercise collapsed "bike, T2B, bike, DU, bike, burpees"
    into a single bike, losing the shape of the workout. The sets were
    always stored correctly - this was a description bug.
    """
    signup(client)
    client.post("/log", data=bike_sandwich(), follow_redirects=False)

    # The summary cell only - "biking" also appears in the volume column and
    # in the delete button's confirmation text.
    summary = re.search(
        r'<td class="piece-summary">(.*?)</td>', client.get("/history").text, re.S
    ).group(1)
    assert summary.count("biking") == 3
    # ...and folded across rounds rather than repeated 12 times.
    assert summary.startswith("4 RFT")


def test_the_sets_behind_it_stay_round_major(client, db_path):
    signup(client)
    client.post("/log", data=bike_sandwich(rounds=4), follow_redirects=False)

    conn = db.connect(db_path)
    order = [r["exercise_id"] for r in conn.execute(
        """SELECT s.exercise_id FROM sets s JOIN blocks b ON s.block_id = b.id
           ORDER BY b.block_order, s.set_order"""
    )]
    assert len(order) == 24
    one_round = ["biking", "toes_to_bar", "biking", "double_unders", "biking", "burpee"]
    assert order == one_round * 4
    conn.close()


def test_whole_minutes_read_as_minutes(client):
    signup(client)
    client.post("/log", data=bike_sandwich(), follow_redirects=False)
    assert "2 min" in client.get("/history").text


# --- EMOM ---------------------------------------------------------------------------

def test_emom_alternates_movements_across_intervals(client, db_path):
    """The reason EMOM is its own type rather than a relabelled rounds: one
    movement per interval, taking turns.
    """
    signup(client)
    data = {"template": "emom", "total_minutes": "12", "every_minutes": "1"}
    data.update(movement_row(0, "thruster", "hhr", amount=10, unit="reps", load=43))
    data.update(movement_row(1, "burpee", "light", amount=8, unit="reps"))
    assert client.post("/log", data=data, follow_redirects=False).status_code == 303

    conn = db.connect(db_path)
    piece = db.fetch_pieces(conn, 1)[0]
    volume = {v["exercise_id"]: v["reps"] for v in piece["volume"]}
    assert volume == {"thruster": 60, "burpee": 48}   # 6 intervals each
    assert piece["structure"] == "EMOM 12"
    conn.close()


def test_a_longer_emom_interval_divides_the_total(client, db_path):
    signup(client)
    data = {"template": "emom", "total_minutes": "20", "every_minutes": "2"}
    data.update(movement_row(0, "back_squat", "heavy", amount=3, unit="reps", load=140))
    client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    piece = db.fetch_pieces(conn, 1)[0]
    assert len(piece["sets"]) == 10   # 20 minutes / every 2
    assert piece["structure"] == "E2MOM 20"
    conn.close()


def test_a_ladder_repeated_on_a_clock_is_one_piece(client, db_path):
    """5-4-3-2-1 squats and pull-ups, three times, every seven minutes."""
    signup(client)
    data = {
        "template": "ladder", "rep_scheme": "5-4-3-2-1",
        "ladder_rounds": "3", "ladder_every_minutes": "7",
        "m0_exercise_id": "back_squat", "m0_style": "heavy",
        "m0_load": "120", "m0_unit": "reps",
        "m1_exercise_id": "strict_pull_up", "m1_style": "heavy", "m1_unit": "reps",
    }
    assert client.post("/log", data=data, follow_redirects=False).status_code == 303

    conn = db.connect(db_path)
    piece = db.fetch_pieces(conn, 1)[0]
    assert piece["structure"] == "3 x 5-4-3-2-1, every 7 min"
    volume = {v["exercise_id"]: v["reps"] for v in piece["volume"]}
    assert volume == {"back_squat": 45, "strict_pull_up": 45}  # 15 per repeat
    assert len(db.fetch_pieces(conn, 1)) == 1
    conn.close()


def test_emom_is_offered_as_a_workout_type(client):
    signup(client)
    text = client.get("/log").text
    assert 'data-template="emom"' in text
    assert client.get("/log?template=emom").status_code == 200


# --- Rest as a movement row --------------------------------------------------------

def test_intervals_are_expressible_as_work_plus_rest(client, db_path):
    """"8 rounds: 20 s bike, 40 s rest" needs no interval template - rest is
    just another movement row, so the templates that exist already cover it.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "8"}
    data.update(movement_row(0, "biking", "light", amount=20, unit="s"))
    data.update(movement_row(1, "rest", "light", amount=40, unit="s"))
    assert client.post("/log", data=data, follow_redirects=False).status_code == 303

    text = client.get("/history").text
    assert "8 RFT" in text and "rest 40 s" in text


def test_rest_contributes_no_exposure(client, db_path):
    signup(client)
    data = {"template": "rounds", "rounds": "8"}
    data.update(movement_row(0, "rest", "light", amount=60, unit="s"))
    client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    stats = advisor.build_log_stats(
        advisor.load_dictionary(), db.fetch_sets(conn, 1), datetime.date.today()
    )
    assert all(value == 0 for value in stats.cell_ewma.values())
    conn.close()


def test_rest_does_not_split_a_piece_into_an_extra_block(client, db_path):
    """Rest has no stimulus, so giving it a style of its own would add a
    block - and a second entry in the piece's style list - for nothing. It
    takes the style of the movement before it.
    """
    signup(client)
    data = {"template": "rounds", "rounds": "5"}
    data.update(movement_row(0, "thruster", "hhr", amount=10, unit="reps", load=43))
    data.update(movement_row(1, "rest", "light", amount=60, unit="s"))
    client.post("/log", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    piece = db.fetch_pieces(conn, 1)[0]
    assert piece["styles"] == ["hhr"]
    assert len(piece["block_ids"]) == 1
    conn.close()


def test_rest_is_never_suggested(client):
    signup(client)
    for page in ("/", "/coverage"):
        assert ">Rest<" not in client.get(page).text


# --- Style entry: not asking questions with one answer ---------------------------

def style_info(client):
    """The per-exercise style map the log page hands to its JS."""
    text = client.get("/log").text
    return json.loads(re.search(r"var STYLE_INFO = (\{.*?\});", text, re.S).group(1))


def test_single_style_exercises_offer_no_choice(client):
    """Eight of nineteen exercises admit exactly one style. Offering three
    options there is both friction and a footgun - picking one of the other
    two just earns a validation error.
    """
    signup(client)
    info = style_info(client)
    for exercise_id in ("push_up", "air_squat", "toes_to_bar", "burpee",
                        "double_unders", "running", "rowing_erg", "biking"):
        assert info[exercise_id]["admissible"] == ["light"], exercise_id
        assert info[exercise_id]["default"] == "light", exercise_id


def test_style_options_are_restricted_per_exercise(client):
    signup(client)
    info = style_info(client)
    assert info["back_squat"]["admissible"] == ["heavy", "hhr"]
    assert info["kb_swing"]["admissible"] == ["light", "hhr"]


def test_style_defaults_to_what_you_logged_last_time(client):
    """Learned from the log rather than prescribed - the same principle as
    suggest_rep_count(). Can't be inferred from load, since the model has no
    1RM (see GUIDE.md's "Deliberately absent").
    """
    signup(client)
    assert style_info(client)["back_squat"]["default"] == "heavy"

    client.post("/log", data=strength_form(style="hhr", rows=[(10, 90)]),
                follow_redirects=False)
    assert style_info(client)["back_squat"]["default"] == "hhr"


def test_styles_are_labelled_in_plain_language(client):
    """`hhr` is an internal name and means nothing mid-session."""
    signup(client)
    text = client.get("/log").text
    assert "Loaded, breathing hard" in text
    assert "Light or bodyweight" in text
    assert "low reps, fresh" in text


def test_heaviest_logged_load_is_exposed_for_the_style_guess(client):
    """Stand-in for a 1RM the model deliberately doesn't keep. None until
    the lift has been logged with a load, so the guess stays silent rather
    than guessing from nothing.
    """
    signup(client)
    assert style_info(client)["back_squat"]["heaviest"] is None

    client.post("/log", data=strength_form(rows=[(3, 140)]), follow_redirects=False)
    assert style_info(client)["back_squat"]["heaviest"] == 140.0


def test_heaviest_load_is_per_user(client, tmp_path, monkeypatch):
    signup(client, "alice@example.com")
    client.post("/log", data=strength_form(rows=[(3, 140)]), follow_redirects=False)

    from training.webapp.app import app
    other = TestClient(app)
    signup(other, "bob@example.com", "differentpass1")
    assert style_info(other)["back_squat"]["heaviest"] is None


def test_style_guess_boundary_is_exposed_to_the_page(client):
    signup(client)
    text = client.get("/log").text
    assert re.search(r"var HEAVY_FRACTION = 0\.8;", text)


# --- Coverage --------------------------------------------------------------------

def test_coverage_lists_every_admissible_cell(client):
    signup(client)
    dictionary = advisor.load_dictionary()
    text = client.get("/coverage").text
    for pattern, style in advisor.admissible_cells(dictionary):
        assert f"<td>{pattern}</td>" in text
        assert style in text


def test_coverage_shows_never_trained_before_anything_is_logged(client):
    signup(client)
    text = client.get("/coverage").text
    assert "never trained" in text


def test_coverage_reflects_logged_work(client):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    text = client.get("/coverage").text
    # One exposure against a steady-state target of k=2 reads as log(2).
    assert "+0.69" in text


def test_coverage_hides_disabled_styles(client):
    signup(client)
    client.post("/settings", data={
        "volume_multiplier": "1.0", "conditioning_focus": "1.0", "disabled_styles": ["heavy"],
    }, follow_redirects=False)
    text = client.get("/coverage").text
    # The style column for a row; "heavy" also appears in the "switched off"
    # note, so this has to match the cell rather than the bare word.
    assert '<td class="muted">heavy</td>' not in text
    assert "switched off" in text


# --- Settings --------------------------------------------------------------------

def test_settings_round_trip(client):
    signup(client)
    client.post("/settings", data={
        "volume_multiplier": "1.5", "conditioning_focus": "2.0", "disabled_styles": ["hhr"],
    }, follow_redirects=False)
    text = client.get("/settings").text
    assert 'value="1.5"' in text
    assert 'value="2.0"' in text


def test_disabling_heavy_empties_the_heavy_candidate_list(client):
    signup(client)
    client.post("/settings", data={
        "volume_multiplier": "1.0", "conditioning_focus": "1.0", "disabled_styles": ["heavy"],
    }, follow_redirects=False)
    assert "witched off" in client.get("/").text


# --- Today's three columns ----------------------------------------------------------

def pick_links(text):
    """Every "pick this movement" link on the Today page."""
    return re.findall(r'href="(/\?picked=[^"]*)"', text)


def test_today_has_a_column_per_style(client):
    signup(client)
    text = client.get("/").text
    for key in ("heavy", "hhr", "light"):
        assert f"<strong>{key}</strong>" in text


def test_suggestions_are_clickable_and_accumulate(client):
    """Picking carries in the query string, so the next page's links build
    on it rather than replacing it.
    """
    signup(client)
    first = pick_links(client.get("/").text)
    assert first, "expected clickable suggestions"

    text = client.get(first[0]).text
    assert "Planning:" in text
    # Every link on the second page carries the first pick plus one more.
    assert all(link.count("picked=") >= 2 for link in pick_links(text))


def test_picking_a_movement_drops_its_pattern_from_the_other_columns(client):
    """The point of the feature: a pattern covered by a pick shouldn't keep
    being suggested elsewhere.
    """
    signup(client)
    text = client.get("/?picked=back_squat").text
    # squat is covered, so no column should still be offering a squat-pattern
    # movement. Front squat and air squat are the other squat-pattern entries.
    for name in ("Front squat", "Air squat"):
        assert name not in text


def test_picks_are_a_plan_not_a_log(client, db_path):
    signup(client)
    client.get("/?picked=back_squat")
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 0
    conn.close()


def test_work_logged_today_is_excluded_from_suggestions(client):
    """Previously Today ignored the day's own log and would headline squats
    an hour after you squatted.
    """
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)

    text = client.get("/").text
    assert "Already trained today" in text
    assert "Back squat" in text          # named in the badge
    for name in ("Front squat", "Air squat"):
        assert name not in text          # squat pattern covered


def test_an_unknown_pick_is_ignored(client):
    signup(client)
    assert client.get("/?picked=not_an_exercise").status_code == 200


# --- Editing and deleting ---------------------------------------------------------

def piece_keys(client):
    """(session_id, piece_group) for each piece on the history page."""
    text = client.get("/history").text
    return re.findall(r"session_id=(\d+)&amp;piece_group=(\d+)", text)


def log_rounds(client, rounds=3):
    data = {"template": "rounds", "rounds": str(rounds)}
    data.update(movement_row(0, "air_squat", "light", amount=20, unit="reps"))
    data.update(movement_row(1, "double_unders", "light", amount=40, unit="reps"))
    return client.post("/log", data=data, follow_redirects=False)


def test_history_offers_edit_and_delete_per_piece(client):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    text = client.get("/history").text
    assert "/history/edit?session_id=" in text
    assert 'action="/history/delete"' in text


def test_delete_removes_the_piece_and_its_sets(client, db_path):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)] * 3), follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    r = client.post("/history/delete",
                    data={"session_id": session_id, "piece_group": piece_group},
                    follow_redirects=False)
    assert r.status_code == 303

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0
    # An emptied session would otherwise linger as a training day on which
    # nothing happened.
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()


def test_delete_leaves_other_pieces_alone(client):
    signup(client)
    log_rounds(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    assert len(piece_keys(client)) == 2

    session_id, piece_group = piece_keys(client)[0]
    client.post("/history/delete",
                data={"session_id": session_id, "piece_group": piece_group},
                follow_redirects=False)
    assert len(piece_keys(client)) == 1


def test_deleting_a_mixed_style_piece_removes_both_blocks(client, db_path):
    """The piece spans two blocks; deleting it must not leave one behind."""
    signup(client)
    data = {"template": "rounds", "rounds": "3"}
    data.update(movement_row(0, "back_squat", "heavy", amount=3, unit="reps", load=140))
    data.update(movement_row(1, "running", "light", amount=400, unit="m"))
    client.post("/log", data=data, follow_redirects=False)

    session_id, piece_group = piece_keys(client)[0]
    client.post("/history/delete",
                data={"session_id": session_id, "piece_group": piece_group},
                follow_redirects=False)

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0
    conn.close()


def edit_row(i, exercise_id, style, amounts, unit="reps", load=None):
    """One edit-page row: a movement and its whole amount sequence."""
    data = {
        f"m{i}_exercise_id": exercise_id, f"m{i}_style": style,
        f"m{i}_amounts": amounts, f"m{i}_unit": unit,
    }
    if load is not None:
        data[f"m{i}_load"] = str(load)
    return data


def test_edit_page_shows_one_row_per_movement_not_per_set(client):
    """A 21-15-9 is six stored sets but two movements. Six rows is the
    storage leaking into a screen that should read the way the piece does.
    """
    signup(client)
    client.post("/log", data={
        "template": "ladder", "rep_scheme": "21-15-9",
        "m0_exercise_id": "thruster", "m0_style": "hhr", "m0_load": "43", "m0_unit": "reps",
        "m1_exercise_id": "strict_pull_up", "m1_style": "hhr", "m1_unit": "reps",
    }, follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    r = client.get(f"/history/edit?session_id={session_id}&piece_group={piece_group}")
    assert r.status_code == 200
    assert len(re.findall(r'name="m\d+_exercise_id"', r.text)) == 2
    # ...with the rounds as one editable sequence per movement. Matched on
    # the amounts fields specifically - the piece's label happens to read
    # "21-15-9" too, so a bare substring count would find three.
    amounts = re.findall(r'name="m\d+_amounts" value="([^"]*)"', r.text)
    assert amounts == ["21-15-9", "21-15-9"]


def test_editing_the_amount_sequence_changes_the_round_count(client):
    """Adding a number adds a round - the round count is editable after all,
    which it wasn't when each row was a single set.
    """
    signup(client)
    log_rounds(client, rounds=3)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "4 RFT"}
    data.update(edit_row(0, "air_squat", "light", "20-20-20-20"))
    data.update(edit_row(1, "double_unders", "light", "40-40-40-40"))
    assert client.post("/history/edit", data=data, follow_redirects=False).status_code == 303

    text = client.get("/history").text
    assert "80 air squats" in text  # was 60 across three rounds
    assert "160 double-unders" in text


def test_a_varying_sequence_keeps_every_round_in_the_summary(client):
    """21-15-9-9 must not render as "21/15/9" - de-duplicating the amounts
    would silently drop a round from the description.
    """
    signup(client)
    log_rounds(client, rounds=1)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "21-15-9-9"}
    data.update(edit_row(0, "air_squat", "light", "21-15-9-9"))
    client.post("/history/edit", data=data, follow_redirects=False)

    text = client.get("/history").text
    assert "21/15/9/9" in text
    assert "54 air squats" in text


def test_edited_sets_stay_round_major(client, db_path):
    """Saving rebuilds from per-movement sequences, so it has to reconstruct
    the interleaving rather than emitting all of one movement then all of
    the next.
    """
    signup(client)
    log_rounds(client, rounds=2)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "2 RFT"}
    data.update(edit_row(0, "air_squat", "light", "20-20"))
    data.update(edit_row(1, "double_unders", "light", "40-40"))
    client.post("/history/edit", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    order = [r["exercise_id"] for r in conn.execute(
        """SELECT s.exercise_id FROM sets s JOIN blocks b ON s.block_id = b.id
           ORDER BY b.block_order, s.set_order"""
    )]
    assert order == ["air_squat", "double_unders", "air_squat", "double_unders"]
    conn.close()


def test_ragged_sequences_are_allowed(client, db_path):
    """One long run alongside three sets of squats is a real piece; the
    movement that runs out is simply absent from later rounds.
    """
    signup(client)
    log_rounds(client, rounds=1)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "mixed"}
    data.update(edit_row(0, "air_squat", "light", "20-20-20"))
    data.update(edit_row(1, "running", "light", "1000", unit="m"))
    assert client.post("/history/edit", data=data, follow_redirects=False).status_code == 303

    conn = db.connect(db_path)
    counts = {r["exercise_id"]: r["n"] for r in conn.execute(
        "SELECT exercise_id, COUNT(*) AS n FROM sets GROUP BY exercise_id"
    )}
    assert counts == {"air_squat": 3, "running": 1}
    conn.close()


def test_unreadable_amounts_are_rejected(client):
    signup(client)
    log_rounds(client, rounds=1)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "x"}
    data.update(edit_row(0, "air_squat", "light", "21-oops-9"))
    r = client.post("/history/edit", data=data)
    assert r.status_code == 400


def test_editing_a_load_rewrites_the_piece_and_its_summary(client):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "5"}
    data.update(edit_row(0, "back_squat", "heavy", "5", load=110))
    r = client.post("/history/edit", data=data, follow_redirects=False)
    assert r.status_code == 303

    text = client.get("/history").text
    assert "@110" in text
    assert "@100" not in text


def test_editing_a_style_moves_the_set_to_the_right_block(client, db_path):
    """A block holds one style, so changing a set's style has to relocate
    it - which is why saving rewrites the piece rather than patching rows.
    """
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "5"}
    data.update(edit_row(0, "back_squat", "hhr", "5", load=100))
    client.post("/history/edit", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    styles = [r["style"] for r in conn.execute("SELECT style FROM blocks")]
    assert styles == ["hhr"]
    stats = advisor.build_log_stats(
        advisor.load_dictionary(), db.fetch_sets(conn, 1), datetime.date(2026, 8, 20)
    )
    assert stats.cell_ewma[("squat", "hhr")] > 0
    conn.close()


def test_editing_the_date_moves_the_piece(client):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "5"}
    data.update(edit_row(0, "back_squat", "heavy", "5", load=100))
    client.post("/history/edit", data=data, follow_redirects=False)
    assert "2026-08-20" in client.get("/history").text


def test_editing_away_every_row_is_refused(client):
    """An empty piece is a delete, and delete is a different button with a
    confirmation on it - silently deleting via an emptied edit form would
    bypass that.
    """
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    r = client.post("/history/edit", data={
        "session_id": session_id, "piece_group": piece_group,
        "date": "2026-08-20", "structure": "5",
    })
    assert r.status_code == 400
    assert len(piece_keys(client)) == 1


def test_editing_to_an_inadmissible_style_is_refused(client):
    signup(client)
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "5"}
    data.update(edit_row(0, "back_squat", "light", "5"))
    r = client.post("/history/edit", data=data)
    assert r.status_code == 400


def test_one_user_cannot_delete_or_edit_anothers_piece(client, db_path):
    """The piece address comes from a form field, so it's attacker-supplied.
    Ownership is re-checked server-side rather than trusted.
    """
    signup(client, "alice@example.com")
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    session_id, piece_group = piece_keys(client)[0]

    from training.webapp.app import app
    bob = TestClient(app)
    signup(bob, "bob@example.com", "differentpass1")

    bob.post("/history/delete",
             data={"session_id": session_id, "piece_group": piece_group},
             follow_redirects=False)

    data = {"session_id": session_id, "piece_group": piece_group,
            "date": "2026-08-20", "structure": "hacked"}
    data.update(edit_row(0, "deadlift", "heavy", "1", load=999))
    bob.post("/history/edit", data=data, follow_redirects=False)

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 1
    assert conn.execute(
        "SELECT exercise_id FROM sets"
    ).fetchone()["exercise_id"] == "back_squat"
    conn.close()
    assert "back squats" in client.get("/history").text


def test_editing_a_nonexistent_piece_redirects_rather_than_erroring(client):
    signup(client)
    r = client.get("/history/edit?session_id=999&piece_group=999", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/history"


# --- Multi-tenancy ---------------------------------------------------------------

def test_one_users_log_never_appears_for_another(client, tmp_path, monkeypatch):
    """The property that matters most in a multi-user log. Asserted at the
    HTTP layer here, and again at the storage layer in test_db.py - this is
    worth checking twice.
    """
    signup(client, "alice@example.com")
    client.post("/log", data=strength_form(rows=[(5, 100)]), follow_redirects=False)
    assert "back squats" in client.get("/history").text

    from training.webapp.app import app
    other = TestClient(app)
    signup(other, "bob@example.com", "differentpass1")
    assert "back squats" not in other.get("/history").text
    assert "Nothing logged yet" in other.get("/history").text
    # And bob's advice is cold-start, unaffected by alice's squatting -
    # every cell still reads as never trained on his coverage page.
    assert "never trained" in other.get("/coverage").text
    assert "+0.69" not in other.get("/coverage").text

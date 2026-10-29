"""LLM-assisted logging (webapp/parse_entry.py) and the hosted-version
settings that came with it: invite codes, admin pages, the secret-key check.

The model is never called: parse_entry.parse is monkeypatched with a fixed
draft, and the one test of the real API path mocks the client.
"""

import html
import json
import os
import re
import subprocess
import sys
from datetime import date

import pytest

pytest.importorskip("fastapi", reason="webapp extra not installed")
pytest.importorskip("httpx", reason="httpx needed for fastapi's TestClient")

from fastapi.testclient import TestClient  # noqa: E402

from training import advisor, db  # noqa: E402
from training.webapp import parse_entry  # noqa: E402

DICT = advisor.load_dictionary()

DRAFT = {
    "date": None,
    "uncertain": ["Swing load not stated."],
    "pieces": [
        {"template": "strength", "exercise_id": "back_squat", "style": "heavy", "scheme": "5x5",
         "sets": [{"reps": 5, "load": 100}] * 5},
        {"template": "rounds", "rounds": 3, "result_time": "14:20", "movements": [
            {"exercise_id": "running", "style": "light", "amount": 400, "unit": "m"},
            {"exercise_id": "kb_swing", "style": "hhr", "amount": 21, "unit": "reps"},
            {"unresolved_name": "zercher carry", "style": "heavy", "amount": 50, "unit": "m"},
        ]},
    ],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAINING_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("TRAINING_PARSER", "fake")
    monkeypatch.setenv("TRAINING_ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setattr(parse_entry, "parse", lambda *a, **k: parse_entry.ParseResult(
        ok=True, draft=json.loads(json.dumps(DRAFT)), model="test"))
    from training.webapp.app import app
    return TestClient(app)


def signup(client, email="u@example.com", **extra):
    return client.post("/signup", data={"email": email, "password": "hunter2pass", **extra},
                       follow_redirects=False)


def hidden_prefill(page: str) -> dict:
    m = re.search(r"name=\"prefill_json\" value='([^']*)'", page)
    return json.loads(html.unescape(m.group(1)))


def submit_prefilled(client, page: str, **changes):
    """What a browser sends when the person presses Log it on a pre-filled
    form: the pre-filled fields, the parse bookkeeping, plus any edits."""
    fields = hidden_prefill(page)
    run = re.search(r'name="parse_run_id" value="(\d+)"', page).group(1)
    idx = re.search(r'name="piece_index" value="(\d+)"', page).group(1)
    data = {**fields, **changes, "parse_run_id": run, "piece_index": idx,
            "prefill_json": json.dumps(fields)}
    return client.post("/log", data=data, follow_redirects=False)


def outcomes(tmp_db):
    conn = db.connect(tmp_db)
    rows = conn.execute("SELECT piece_index, action, edited FROM parse_outcomes ORDER BY id").fetchall()
    return [tuple(r) for r in rows]


# --- the whole flow ----------------------------------------------------------------

def test_note_to_two_pieces_logged_and_skipped(client, tmp_path):
    signup(client)
    r = client.post("/log/parse", data={"text": "squat 5x5 100, 3 rds 400m run 21 kbs 50m zercher"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/log?parse=1&piece=0"

    page = client.get("/log?parse=1&piece=0").text
    assert "piece 1 of 2" in page and "Swing load not stated." in page
    fields = hidden_prefill(page)
    assert fields["template"] == "strength" and fields["exercise_id"] == "back_squat"
    assert fields["style"] == "heavy" and fields["s4_load"] == "100"

    r = submit_prefilled(client, page)
    assert r.status_code == 303 and r.headers["location"] == "/log?parse=1&piece=1"

    page = client.get("/log?parse=1&piece=1").text
    assert "piece 2 of 2" in page and "zercher carry" in page and "isn't in the exercise list" in page
    fields = hidden_prefill(page)
    assert fields["m2_exercise_id"] == ""          # unknown: left blank, never guessed
    assert fields["m1_style"] == "hhr" and fields["result_time"] == "14:20"

    r = client.post("/log/parse/skip", data={"parse_run_id": 1, "piece_index": 1,
                                             "prefill_json": json.dumps(fields)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/log?template=")

    assert outcomes(str(tmp_path / "test.db")) == [(0, "logged", 0), (1, "skipped", None)]
    history = client.get("/history").text
    assert "back squats" in history


def test_editing_a_draft_is_recorded_as_edited(client, tmp_path):
    signup(client)
    client.post("/log/parse", data={"text": "x"})
    page = client.get("/log?parse=1&piece=0").text
    r = submit_prefilled(client, page, s4_load="97.5")
    assert r.status_code == 303
    assert outcomes(str(tmp_path / "test.db")) == [(0, "logged", 1)]


def test_unknown_row_left_blank_is_dropped_and_the_rest_logs(client, tmp_path):
    signup(client)
    client.post("/log/parse", data={"text": "x"})
    page = client.get("/log?parse=1&piece=1").text
    r = submit_prefilled(client, page)
    assert r.status_code == 303
    (piece,) = [p for p in db.fetch_pieces(db.connect(str(tmp_path / "test.db")), 1)]
    assert {s["exercise_id"] for s in piece["sets"]} == {"running", "kb_swing"}
    assert set(piece["styles"]) == {"light", "hhr"}


def test_a_bad_submission_keeps_the_draft_on_screen(client):
    signup(client)
    client.post("/log/parse", data={"text": "x"})
    page = client.get("/log?parse=1&piece=0").text
    r = submit_prefilled(client, page, style="light")   # back squat doesn't admit light
    assert r.status_code == 400
    assert "piece 1 of 2" in r.text


def test_parse_runs_are_private(client):
    signup(client, "a@example.com")
    client.post("/log/parse", data={"text": "x"})
    client.post("/logout")
    signup(client, "b@example.com")
    page = client.get("/log?parse=1&piece=0").text
    assert "piece 1 of 2" not in page and "prefill_json" not in page


def test_daily_cap(client, monkeypatch):
    monkeypatch.setenv("TRAINING_PARSES_PER_DAY", "1")
    signup(client)
    assert client.post("/log/parse", data={"text": "x"}, follow_redirects=False).status_code == 303
    r = client.post("/log/parse", data={"text": "x"}, follow_redirects=False)
    assert r.status_code == 429 and "limit" in r.text


def test_photo_type_and_storage(client):
    signup(client, "boss@example.com")
    r = client.post("/log/parse", data={"text": ""}, files={"image": ("a.txt", b"hi", "text/plain")})
    assert r.status_code == 400 and "JPEG" in r.text
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    r = client.post("/log/parse", data={"text": ""}, files={"image": ("wb.png", png, "image/png")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/admin/parse/1/image").content == png


def test_parsing_hidden_when_switched_off(client, monkeypatch):
    monkeypatch.setenv("TRAINING_PARSER", "off")
    signup(client)
    assert 'action="/log/parse"' not in client.get("/log").text
    assert client.post("/log/parse", data={"text": "x"}).status_code == 400


# --- invite code, admin, secret --------------------------------------------------------

def test_invite_code_required_when_set(client, monkeypatch):
    monkeypatch.setenv("TRAINING_INVITE_CODE", "letmein")
    assert "Invite code" in client.get("/signup").text
    assert signup(client, invite_code="nope").status_code == 400
    assert signup(client, invite_code="letmein").status_code == 303


def test_admin_sees_everyone_others_get_403(client):
    signup(client, "a@example.com")
    client.post("/log/parse", data={"text": "x"})
    page = client.get("/log?parse=1&piece=0").text
    submit_prefilled(client, page)
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/users/1").status_code == 403
    assert client.get("/admin/export.json").status_code == 403
    client.post("/logout")

    signup(client, "boss@example.com")
    admin = client.get("/admin").text
    assert "a@example.com" in admin and "zercher carry (1)" in admin
    history = client.get("/admin/users/1").text
    assert "back squats" in history and "/history/delete" not in history   # read-only
    export = client.get("/admin/export.json").json()
    assert export[0]["outcomes"][0]["action"] == "logged"
    assert "logged as drafted" in client.get("/admin/parse/1").text


def test_hosted_database_requires_a_secret():
    env = {k: v for k, v in os.environ.items() if k != "TRAINING_SECRET_KEY"}
    env["TRAINING_DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/none"
    r = subprocess.run([sys.executable, "-c", "import training.webapp.app"], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "TRAINING_SECRET_KEY" in r.stderr


# --- the parser's pieces in isolation ------------------------------------------------------

def default_style(ex_id):
    return DICT.exercises[ex_id].admissible_styles[0]


def test_prefill_strength_rounds_loads_and_fallback_style():
    p = parse_entry.draft_to_prefill(
        {"template": "strength", "exercise_id": "push_up", "style": "heavy", "sets": [{"reps": 20, "load": 61.3}]},
        DICT, default_style, "2026-10-29")
    assert p.fields["style"] == "light"                       # push-ups only admit light
    assert any("can't be logged as heavy" in n for n in p.notes)
    assert p.fields["s0_load"] == "61.5"                      # step=0.5 input
    p = parse_entry.draft_to_prefill(
        {"template": "rounds", "movements": [
            {"exercise_id": "thruster", "style": "hhr", "amount": 10, "unit": "reps", "load": 43},
            {"exercise_id": "rest", "style": "light", "amount": 60, "unit": "s"}]},
        DICT, default_style, "2026-10-29")
    assert p.fields["rounds"] == "3"                          # form default when unstated
    assert p.fields["m1_style"] == "hhr"                      # rest inherits, as /log does


def test_comparable_ignores_blank_rows_and_number_formatting():
    a = {"template": "rounds", "rounds": "3", "m0_exercise_id": "burpee", "m0_amount": "10",
         "m0_unit": "reps", "m0_style": "light", "m0_load": ""}
    b = {**a, "rounds": "3.0", "m1_exercise_id": "", "m1_unit": "reps", "m1_style": "heavy",
         "parse_run_id": "1", "piece_index": "0"}
    assert parse_entry.comparable(a) == parse_entry.comparable(b)
    assert parse_entry.comparable(a) != parse_entry.comparable({**a, "m0_amount": "12"})


UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
               "minLength", "maxLength", "pattern", "if", "then", "else", "not", "allOf", "oneOf"}


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_schema_stays_within_structured_outputs_subset():
    schema = parse_entry.llm_schema(list(DICT.exercises), DICT.styles)
    for key, value in _walk(schema):
        assert key not in UNSUPPORTED
        if isinstance(value, dict) and value.get("type") == "object":
            assert value.get("additionalProperties") is False
    assert "zercher" not in json.dumps(schema)


def test_prompt_lists_every_exercise_with_its_styles():
    prompt = parse_entry.system_prompt(DICT)
    for ex in DICT.exercises.values():
        assert f"- {ex.id}: {ex.name}" in prompt
    assert "styles: heavy, hhr" in prompt


def test_anthropic_request_shape(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    from types import SimpleNamespace
    seen = {}

    class Messages:
        def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(
                stop_reason="end_turn", usage=SimpleNamespace(input_tokens=900, output_tokens=200),
                content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(DRAFT))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: SimpleNamespace(messages=Messages()))
    result = parse_entry._anthropic_parse("note", (b"png", "image/png"), DICT, date(2026, 10, 29))
    assert result.ok and result.draft["pieces"][0]["exercise_id"] == "back_squat"
    assert seen["model"] == "claude-haiku-5-5"
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert seen["messages"][0]["content"][0]["type"] == "image"
    assert "2026-10-29" in seen["messages"][0]["content"][1]["text"]

"""Tests for the multi-tenant storage layer (users, user_settings, and the
per-user scoping of sessions/sets) added for the webapp - see
docs/STATUS.md. advisor.py's own tests (test_advisor.py) cover
scoring; these cover storage and isolation between users, which advisor.py
never touches directly.
"""

from datetime import date

import pytest

from training import db


@pytest.fixture
def conn():
    return db.connect()


def test_create_user_also_creates_default_settings(conn):
    """A user without settings would make get_user_settings() have to
    handle a missing row everywhere it's called - simpler to guarantee the
    row exists from the moment the user does.
    """
    user_id = db.create_user(conn, "a@example.com", "hash")
    settings = db.get_user_settings(conn, user_id)
    assert settings.user_id == user_id
    assert settings.volume_multiplier == 1.0
    assert settings.conditioning_focus == 1.0
    assert settings.disabled_styles == []


def test_email_uniqueness_is_enforced(conn):
    db.create_user(conn, "dup@example.com", "hash1")
    with pytest.raises(Exception):
        db.create_user(conn, "dup@example.com", "hash2")


def test_get_user_by_email_finds_the_right_row(conn):
    user_id = db.create_user(conn, "findme@example.com", "hash")
    row = db.get_user_by_email(conn, "findme@example.com")
    assert row is not None
    assert row["id"] == user_id
    assert db.get_user_by_email(conn, "nobody@example.com") is None


def test_save_user_settings_round_trips(conn):
    user_id = db.create_user(conn, "settings@example.com", "hash")
    db.save_user_settings(conn, user_id, volume_multiplier=1.5, conditioning_focus=2.0,
                           disabled_styles=["heavy"])
    settings = db.get_user_settings(conn, user_id)
    assert settings.volume_multiplier == 1.5
    assert settings.conditioning_focus == 2.0
    assert settings.disabled_styles == ["heavy"]


def test_fetch_sets_never_leaks_across_users(conn):
    """The one property that actually matters for a multi-tenant log: user
    A must never see user B's training history, since advisor.py would
    happily score against whatever fetch_sets() hands it - the isolation
    has to be enforced here, not downstream.
    """
    alice = db.create_user(conn, "alice@example.com", "hash")
    bob = db.create_user(conn, "bob@example.com", "hash")

    alice_session = db.log_session(conn, alice, date(2026, 1, 1).isoformat())
    alice_block = db.log_block(conn, alice_session, "heavy")
    db.log_set(conn, alice_block, "back_squat", 10, reps=5, scheme="5x5")

    bob_session = db.log_session(conn, bob, date(2026, 1, 2).isoformat())
    bob_block = db.log_block(conn, bob_session, "light")
    db.log_set(conn, bob_block, "push_up", 10, reps=10)

    alice_sets = db.fetch_sets(conn, alice)
    bob_sets = db.fetch_sets(conn, bob)

    assert len(alice_sets) == 1
    assert alice_sets[0].exercise_id == "back_squat"
    assert len(bob_sets) == 1
    assert bob_sets[0].exercise_id == "push_up"


def test_fetch_sets_since_filter_still_works_per_user(conn):
    user_id = db.create_user(conn, "since@example.com", "hash")
    old_session = db.log_session(conn, user_id, date(2026, 1, 1).isoformat())
    old_block = db.log_block(conn, old_session, "heavy")
    db.log_set(conn, old_block, "back_squat", 10, reps=5, scheme="5x5")

    new_session = db.log_session(conn, user_id, date(2026, 2, 1).isoformat())
    new_block = db.log_block(conn, new_session, "heavy")
    db.log_set(conn, new_block, "deadlift", 10, reps=5, scheme="5x5")

    recent = db.fetch_sets(conn, user_id, since=date(2026, 1, 15).isoformat())
    assert len(recent) == 1
    assert recent[0].exercise_id == "deadlift"


def _legacy_mixed_style_piece(path):
    """A piece written the way the app did before `summary`/`piece_group`
    existed: two blocks, same structure, different styles, both NULL.
    """
    import sqlite3
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                            password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                               date TEXT NOT NULL, notes TEXT);
        CREATE TABLE blocks (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL,
                             style TEXT NOT NULL, structure TEXT, rest_seconds INTEGER,
                             time_cap_seconds INTEGER, block_order INTEGER NOT NULL);
        CREATE TABLE sets (id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL,
                           exercise_id TEXT NOT NULL, dict_version INTEGER NOT NULL,
                           reps INTEGER, load REAL, scheme TEXT, side TEXT,
                           set_order INTEGER NOT NULL);
        INSERT INTO users VALUES (1, 'u@example.com', 'hash', '2026-01-01');
        INSERT INTO sessions VALUES (1, 1, '2026-01-01', NULL);
        INSERT INTO blocks VALUES (1, 1, 'heavy', '3 RFT', NULL, NULL, 1);
        INSERT INTO blocks VALUES (2, 1, 'light', '3 RFT', NULL, NULL, 2);
        INSERT INTO sets VALUES (1, 1, 'back_squat',    10, 3,  90.0, NULL, NULL, 1);
        INSERT INTO sets VALUES (2, 1, 'back_squat',    10, 3,  90.0, NULL, NULL, 2);
        INSERT INTO sets VALUES (3, 1, 'back_squat',    10, 3,  90.0, NULL, NULL, 3);
        INSERT INTO sets VALUES (4, 2, 'double_unders', 10, 60, NULL, NULL, NULL, 1);
        INSERT INTO sets VALUES (5, 2, 'double_unders', 10, 60, NULL, NULL, NULL, 2);
        INSERT INTO sets VALUES (6, 2, 'double_unders', 10, 60, NULL, NULL, NULL, 3);
    """)
    raw.commit()
    raw.close()


def test_legacy_mixed_style_piece_is_backfilled_into_one_piece(tmp_path):
    """Pieces logged before `piece_group` existed had NULL for it, so the
    display's "one block, one piece" fallback split a mixed-style piece in
    two. The migration backfills them.
    """
    path = str(tmp_path / "legacy.db")
    _legacy_mixed_style_piece(path)

    conn = db.connect(path)  # runs the migration
    groups = [r["piece_group"] for r in conn.execute("SELECT piece_group FROM blocks ORDER BY id")]
    assert groups == [1, 1], "both blocks should join one piece"

    pieces = db.fetch_pieces(conn, 1)
    assert len(pieces) == 1
    assert pieces[0]["styles"] == ["heavy", "light"]
    volume = {v["exercise_id"]: v for v in pieces[0]["volume"]}
    assert volume["back_squat"]["reps"] == 9
    assert volume["double_unders"]["reps"] == 180
    conn.close()


def test_backfill_is_idempotent(tmp_path):
    path = str(tmp_path / "legacy.db")
    _legacy_mixed_style_piece(path)

    db.connect(path).close()
    conn = db.connect(path)
    groups = [r["piece_group"] for r in conn.execute("SELECT piece_group FROM blocks ORDER BY id")]
    assert groups == [1, 1]
    conn.close()


def test_backfill_starts_a_new_piece_when_a_style_repeats(tmp_path):
    """Two same-style blocks with the same structure can't be one piece -
    the expansion groups a submission's sets by style, so each style
    appears at most once per piece. A repeat means a new piece started.
    """
    import sqlite3
    path = str(tmp_path / "legacy2.db")
    _legacy_mixed_style_piece(path)
    raw = sqlite3.connect(path)
    # A third block, same structure, repeating the 'heavy' style.
    raw.execute("INSERT INTO blocks VALUES (3, 1, 'heavy', '3 RFT', NULL, NULL, 3)")
    raw.execute("INSERT INTO sets VALUES (7, 3, 'deadlift', 10, 5, 100.0, NULL, NULL, 1)")
    raw.commit()
    raw.close()

    conn = db.connect(path)
    groups = [r["piece_group"] for r in conn.execute("SELECT piece_group FROM blocks ORDER BY id")]
    assert groups == [1, 1, 2]
    assert len(db.fetch_pieces(conn, 1)) == 2
    conn.close()


def test_heaviest_load_ignores_unloaded_sets_and_other_users(conn):
    """Feeds the log form's heavy-vs-hhr guess only. Bodyweight sets carry
    no load and must not read as 0 kg, which would drag the maximum down.
    """
    alice = db.create_user(conn, "alice@example.com", "hash")
    bob = db.create_user(conn, "bob@example.com", "hash")

    session_id = db.log_session(conn, alice, date(2026, 1, 1).isoformat())
    block_id = db.log_block(conn, session_id, "heavy")
    db.log_set(conn, block_id, "back_squat", 10, reps=5, load=100.0)
    db.log_set(conn, block_id, "back_squat", 10, reps=3, load=140.0)
    db.log_set(conn, block_id, "back_squat", 10, reps=10)  # unloaded, no load column

    bob_session = db.log_session(conn, bob, date(2026, 1, 1).isoformat())
    bob_block = db.log_block(conn, bob_session, "heavy")
    db.log_set(conn, bob_block, "back_squat", 10, reps=1, load=200.0)

    assert db.heaviest_load_for_exercise(conn, alice, "back_squat") == 140.0
    assert db.heaviest_load_for_exercise(conn, bob, "back_squat") == 200.0
    assert db.heaviest_load_for_exercise(conn, alice, "deadlift") is None


def _log_piece(conn, user_id, date_str, exercise_id="back_squat", style="heavy", n=1):
    session_id = db.get_or_create_session(conn, user_id, date_str)
    group = db.next_piece_group(conn, session_id)
    block_id = db.log_block(conn, session_id, style, structure="x", summary="s", piece_group=group)
    for _ in range(n):
        db.log_set(conn, block_id, exercise_id, 10, reps=5, load=100.0)
    conn.commit()
    return session_id, group


def test_delete_piece_removes_sets_blocks_and_an_emptied_session(conn):
    user_id = db.create_user(conn, "u@example.com", "hash")
    session_id, group = _log_piece(conn, user_id, "2026-01-01", n=3)

    assert db.delete_piece(conn, user_id, session_id, group) is True
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_delete_piece_keeps_a_session_that_still_has_other_pieces(conn):
    user_id = db.create_user(conn, "u@example.com", "hash")
    session_id, first = _log_piece(conn, user_id, "2026-01-01")
    _log_piece(conn, user_id, "2026-01-01", exercise_id="deadlift")

    db.delete_piece(conn, user_id, session_id, first)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert len(db.fetch_pieces(conn, user_id)) == 1


def test_delete_piece_refuses_another_users_piece(conn):
    """The piece address arrives from a form field, so it's attacker-
    supplied - ownership can't be assumed from it.
    """
    alice = db.create_user(conn, "alice@example.com", "hash")
    bob = db.create_user(conn, "bob@example.com", "hash")
    session_id, group = _log_piece(conn, alice, "2026-01-01")

    assert db.delete_piece(conn, bob, session_id, group) is False
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 1


def test_fetch_piece_is_scoped_to_the_owner(conn):
    alice = db.create_user(conn, "alice@example.com", "hash")
    bob = db.create_user(conn, "bob@example.com", "hash")
    session_id, group = _log_piece(conn, alice, "2026-01-01")

    assert db.fetch_piece(conn, alice, session_id, group) is not None
    assert db.fetch_piece(conn, bob, session_id, group) is None


def test_fetch_pieces_carries_each_set_style(conn):
    """Style lives on the block; the edit form needs it per set."""
    user_id = db.create_user(conn, "u@example.com", "hash")
    _log_piece(conn, user_id, "2026-01-01", style="hhr")
    piece = db.fetch_pieces(conn, user_id)[0]
    assert all(s["style"] == "hhr" for s in piece["sets"])


def test_session_requires_a_real_user_id(conn):
    """Foreign keys are on (schema.sql's connect() enables PRAGMA
    foreign_keys) - a session can't be logged against a user_id that was
    never created.
    """
    with pytest.raises(Exception):
        db.log_session(conn, 999999, date(2026, 1, 1).isoformat())

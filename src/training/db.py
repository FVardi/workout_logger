"""SQLite plumbing for the training log: user -> session -> block -> set.

Owns storage only. Turning raw sets into pattern/style exposures is
advisor.py's job, against the authored exercise dictionary - the log
never encodes pattern weights itself. Multi-tenant since the webapp: every
session belongs to a user, and every read/write below that isn't itself
about creating a user takes a user_id and scopes to it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date as Date, datetime, timezone
from pathlib import Path

from . import pg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


# Columns added after the first databases already existed. schema.sql's
# CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so new columns
# need an explicit ALTER - see _migrate().
_ADDED_COLUMNS = {
    "sets": {
        "distance": "REAL",
        "duration_seconds": "INTEGER",
    },
    "blocks": {
        "summary": "TEXT",
        "piece_group": "INTEGER",
        "result_seconds": "INTEGER",
    },
}


def _is_postgres(conn) -> bool:
    return getattr(conn, "dialect", "sqlite") == "postgres"


def _table_columns(conn, table: str) -> set[str]:
    if _is_postgres(conn):
        return pg.table_columns(conn, table)
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive-only schema catch-up for databases created
    before a column existed. Runs on every connect; each step is a no-op
    once applied. Deliberately additive only - nothing here drops or
    rewrites a column, so it can't lose logged data.
    """
    for table, columns in _ADDED_COLUMNS.items():
        existing = _table_columns(conn, table)
        for column, sql_type in columns.items():
            if column not in existing:
                if _is_postgres(conn) and sql_type == "REAL":
                    sql_type = "DOUBLE PRECISION"
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
    conn.commit()
    _backfill_piece_groups(conn)


def _backfill_piece_groups(conn: sqlite3.Connection) -> None:
    """Assign piece_group to blocks logged before the column existed.

    Without this, a mixed-style piece from before the column - one block per
    style, both with NULL - reads back as two separate pieces, because the
    display's NULL fallback is "one block, one piece".

    The rule: consecutive blocks in a session sharing a `structure` are one
    piece *until a style repeats*. That's exactly the shape the template
    expansion produces (it groups a submission's sets by style, so each
    style appears at most once per piece), which makes a repeated style
    reliable evidence that a new piece started rather than a guess.
    """
    rows = conn.execute(
        """SELECT id, session_id, structure, style FROM blocks
           WHERE piece_group IS NULL ORDER BY session_id, block_order, id"""
    ).fetchall()
    if not rows:
        return

    next_group: dict[int, int] = {}
    current: dict[int, tuple] = {}   # session_id -> (structure, group, styles seen)
    updates = []
    for row in rows:
        session_id, structure, style = row["session_id"], row["structure"], row["style"]
        if session_id not in next_group:
            (start,) = conn.execute(
                "SELECT COALESCE(MAX(piece_group), 0) + 1 FROM blocks WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            next_group[session_id] = start

        prev = current.get(session_id)
        same_piece = (
            prev is not None
            and prev[0] == structure
            and structure is not None
            and style not in prev[2]
        )
        if same_piece:
            group = prev[1]
            prev[2].add(style)
        else:
            group = next_group[session_id]
            next_group[session_id] += 1
            current[session_id] = (structure, group, {style})
        updates.append((group, row["id"]))

    conn.executemany("UPDATE blocks SET piece_group = ? WHERE id = ?", updates)
    conn.commit()


# Postgres URLs whose schema has been created/migrated by this process.
# SQLite re-runs the (cheap, local) setup on every connect as before; a
# hosted Postgres is a network round trip per statement, so it runs once.
_pg_ready: set[str] = set()


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """A connection to the log. `path` is a SQLite file (or ":memory:"), or
    a postgresql:// URL for the hosted version - see training.pg, which
    makes Postgres answer the same sqlite3-style calls used throughout this
    module.
    """
    if pg.is_postgres_url(path):
        conn = pg.connect(path)
        if path not in _pg_ready:
            conn.executescript(pg.schema_for_postgres(SCHEMA_PATH.read_text()))
            conn.commit()
            _migrate(conn)
            _pg_ready.add(path)
        return conn
    return _connect_sqlite(path)


def _connect_sqlite(path: str) -> sqlite3.Connection:
    # check_same_thread=False because FastAPI resolves sync dependencies in a
    # threadpool but runs `async def` endpoints on the event loop thread, so a
    # connection opened by the dependency is used from a different thread than
    # it was created on. Safe here specifically because each request opens and
    # closes its own connection (see webapp.app.get_conn) - one owner, no
    # concurrent use - which is the condition SQLite's own check exists to
    # protect. Do NOT start sharing one connection across requests without
    # revisiting this.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    return conn


# --- Users -----------------------------------------------------------------

def create_user(conn: sqlite3.Connection, email: str, password_hash: str) -> int:
    """Inserts the user and a default-settings row together, so
    get_user_settings() never has to handle a missing row - every user has
    settings from the moment they exist, even before they've touched them.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (email, password_hash, created_at),
    )
    user_id = cur.lastrowid
    conn.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
    conn.commit()
    return user_id


def get_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


@dataclass(frozen=True)
class UserSettings:
    user_id: int
    volume_multiplier: float
    conditioning_focus: float
    disabled_styles: list[str]


def get_user_settings(conn: sqlite3.Connection, user_id: int) -> UserSettings:
    row = conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise ValueError(f"no settings row for user_id={user_id} - was create_user() used?")
    return UserSettings(
        user_id=row["user_id"],
        volume_multiplier=row["volume_multiplier"],
        conditioning_focus=row["conditioning_focus"],
        disabled_styles=json.loads(row["disabled_styles"]),
    )


def save_user_settings(
    conn: sqlite3.Connection,
    user_id: int,
    volume_multiplier: float,
    conditioning_focus: float,
    disabled_styles: list[str],
) -> None:
    conn.execute(
        """UPDATE user_settings
           SET volume_multiplier = ?, conditioning_focus = ?, disabled_styles = ?
           WHERE user_id = ?""",
        (volume_multiplier, conditioning_focus, json.dumps(disabled_styles), user_id),
    )
    conn.commit()


# --- Sessions/blocks/sets ----------------------------------------------------

def log_session(conn: sqlite3.Connection, user_id: int, date: str, notes: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (user_id, date, notes) VALUES (?, ?, ?)", (user_id, date, notes)
    )
    return cur.lastrowid


def last_style_for_exercise(conn: sqlite3.Connection, user_id: int, exercise_id: str) -> str | None:
    """The style this user most recently logged this exercise at, or None.

    Used to pre-fill the style field when logging: for the ten exercises
    admitting both heavy and hhr, "same as last time" is a better default
    than any fixed guess, and it can't be inferred from load (the model
    never sees a 1RM - see docs/GUIDE.md's "Deliberately absent").
    Learned from the log, not prescribed.
    """
    row = conn.execute(
        """SELECT blocks.style AS style
           FROM sets
           JOIN blocks ON sets.block_id = blocks.id
           JOIN sessions ON blocks.session_id = sessions.id
           WHERE sessions.user_id = ? AND sets.exercise_id = ?
           ORDER BY sessions.date DESC, blocks.block_order DESC, sets.set_order DESC
           LIMIT 1""",
        (user_id, exercise_id),
    ).fetchone()
    return row["style"] if row else None


def heaviest_load_for_exercise(conn: sqlite3.Connection, user_id: int, exercise_id: str) -> float | None:
    """The heaviest load this user has ever logged for this exercise, at any
    style, or None if they've never loaded it.

    A stand-in for a 1RM that the model itself never sees. The advisor
    deliberately doesn't track maximal strength (docs/GUIDE.md,
    "Deliberately absent"), so this exists purely so the log form can guess
    heavy-vs-hhr from what you type. Nothing in scoring reads it.
    """
    row = conn.execute(
        """SELECT MAX(sets.load) AS heaviest
           FROM sets
           JOIN blocks ON sets.block_id = blocks.id
           JOIN sessions ON blocks.session_id = sessions.id
           WHERE sessions.user_id = ? AND sets.exercise_id = ? AND sets.load IS NOT NULL""",
        (user_id, exercise_id),
    ).fetchone()
    return row["heaviest"] if row and row["heaviest"] is not None else None


def fetch_pieces(
    conn: sqlite3.Connection, user_id: int, date: str | None = None, limit: int | None = None,
) -> list[dict]:
    """One entry per logged *piece*, newest first (or in order within a day).

    A piece is what the person did as one thing - "3 RFT of 20 air squats
    and 40 double-unders" - which is one row to read, not six. Storage
    keeps every set (volume and PR analysis need them) and may split one
    piece across several blocks when styles differ; this reassembles both
    back into the unit that makes sense to a human.

    Each entry carries its plain-text `summary`, the styles involved, and
    `volume`: per-exercise totals summed across the piece's sets.
    """
    query = """
        SELECT blocks.id AS id, blocks.style AS style, blocks.structure AS structure,
               blocks.summary AS summary, blocks.piece_group AS piece_group,
               blocks.time_cap_seconds AS time_cap_seconds,
               blocks.result_seconds AS result_seconds, blocks.block_order AS block_order,
               sessions.id AS session_id, sessions.date AS date
        FROM blocks
        JOIN sessions ON blocks.session_id = sessions.id
        WHERE sessions.user_id = ?
    """
    params: tuple = (user_id,)
    if date is not None:
        query += " AND sessions.date = ?"
        params = (user_id, date)
    query += " ORDER BY sessions.date DESC, blocks.block_order"
    blocks = conn.execute(query, params).fetchall()

    # Group by (session, piece_group). A NULL piece_group predates the
    # column, so fall back to the block's own id - one block, one piece.
    pieces: dict[tuple, dict] = {}
    order: list[tuple] = []
    for b in blocks:
        key = (b["session_id"], b["piece_group"] if b["piece_group"] is not None else f"b{b['id']}")
        if key not in pieces:
            pieces[key] = {
                # (session_id, piece_group) is the piece's address - what the
                # edit and delete routes take. piece_group can only be None on
                # rows predating the column and unreachable by _backfill_piece_groups.
                "session_id": b["session_id"],
                "piece_group": b["piece_group"],
                "block_ids": [],
                "date": b["date"],
                "summary": b["summary"],
                "structure": b["structure"],
                "time_cap_seconds": b["time_cap_seconds"],
                "result_seconds": b["result_seconds"],
                "styles": [],
                "sets": [],
            }
            order.append(key)
        piece = pieces[key]
        piece["block_ids"].append(b["id"])
        if b["style"] not in piece["styles"]:
            piece["styles"].append(b["style"])
        piece["sets"].extend(
            # style comes off the block, not the set row - carried down here
            # so a caller editing a piece can see and change each set's style
            # without having to reassemble the block structure itself.
            dict(s, style=b["style"]) for s in conn.execute(
                """SELECT exercise_id, reps, load, distance, duration_seconds, scheme, side
                   FROM sets WHERE block_id = ? ORDER BY set_order""",
                (b["id"],),
            )
        )

    out = []
    for key in order:
        piece = pieces[key]
        piece["volume"] = _volume_totals(piece["sets"])
        out.append(piece)
    return out[:limit] if limit else out


def fetch_piece(
    conn: sqlite3.Connection, user_id: int, session_id: int, piece_group: int,
) -> dict | None:
    """One piece by address, or None if it isn't this user's.

    Ownership is enforced by fetch_pieces()'s own user_id filter rather than
    trusted from the URL - a piece_group from someone else's session simply
    doesn't appear, so this returns None.
    """
    for piece in fetch_pieces(conn, user_id):
        if piece["session_id"] == session_id and piece["piece_group"] == piece_group:
            return piece
    return None


def delete_piece(conn: sqlite3.Connection, user_id: int, session_id: int, piece_group: int) -> bool:
    """Delete a piece and everything under it. Returns False if it isn't
    this user's, having changed nothing.

    Sets go first, then blocks, then the session if that emptied it - the
    schema has no ON DELETE CASCADE, and an empty session left behind would
    show up as a training day on which nothing happened.
    """
    piece = fetch_piece(conn, user_id, session_id, piece_group)
    if piece is None:
        return False

    placeholders = ",".join("?" for _ in piece["block_ids"])
    conn.execute(f"DELETE FROM sets WHERE block_id IN ({placeholders})", piece["block_ids"])
    conn.execute(f"DELETE FROM blocks WHERE id IN ({placeholders})", piece["block_ids"])
    (remaining,) = conn.execute(
        "SELECT COUNT(*) FROM blocks WHERE session_id = ?", (session_id,)
    ).fetchone()
    if remaining == 0:
        conn.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    conn.commit()
    return True


def _volume_totals(sets: list[dict]) -> list[dict]:
    """Per-exercise totals across a piece, in first-appearance order.

    Unilateral sets are logged per side, so their reps are summed across
    both sides - 3 sets of 8 per side totals 48, which is the honest total
    rep count even though the programmed number was "8 each side".
    """
    totals: dict[str, dict] = {}
    for s in sets:
        t = totals.setdefault(s["exercise_id"], {
            "exercise_id": s["exercise_id"], "reps": 0, "distance": 0.0, "duration_seconds": 0,
        })
        t["reps"] += s.get("reps") or 0
        t["distance"] += s.get("distance") or 0
        t["duration_seconds"] += s.get("duration_seconds") or 0
    return list(totals.values())


def get_or_create_session(conn: sqlite3.Connection, user_id: int, date: str) -> int:
    """Like log_session(), but reuses today's session if one already exists
    for this user - the webapp logs one piece at a time (see
    training.webapp.app), and a "session" is a whole day per GUIDE.md's data
    model, not one row per piece. simulate.py doesn't use this: it always
    wants a fresh session per simulated day and never re-queries.
    """
    row = conn.execute(
        "SELECT id FROM sessions WHERE user_id = ? AND date = ?", (user_id, date)
    ).fetchone()
    if row is not None:
        return row["id"]
    return log_session(conn, user_id, date)


def next_piece_group(conn: sqlite3.Connection, session_id: int) -> int:
    """The next unused piece_group within this session. Blocks written by
    one submission share it, so a mixed-style piece reads back as one piece.
    """
    (nxt,) = conn.execute(
        "SELECT COALESCE(MAX(piece_group), 0) + 1 FROM blocks WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return nxt


def log_block(
    conn: sqlite3.Connection,
    session_id: int,
    style: str,
    structure: str | None = None,
    rest_seconds: int | None = None,
    time_cap_seconds: int | None = None,
    summary: str | None = None,
    piece_group: int | None = None,
    result_seconds: int | None = None,
) -> int:
    (next_order,) = conn.execute(
        "SELECT COALESCE(MAX(block_order), 0) + 1 FROM blocks WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    cur = conn.execute(
        """INSERT INTO blocks
           (session_id, style, structure, rest_seconds, time_cap_seconds,
            summary, piece_group, result_seconds, block_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, style, structure, rest_seconds, time_cap_seconds,
         summary, piece_group, result_seconds, next_order),
    )
    return cur.lastrowid


def log_set(
    conn: sqlite3.Connection,
    block_id: int,
    exercise_id: str,
    dict_version: int,
    reps: int | None = None,
    load: float | None = None,
    scheme: str | None = None,
    side: str | None = None,
    distance: float | None = None,
    duration_seconds: int | None = None,
) -> int:
    if side not in (None, "L", "R"):
        raise ValueError(f"side must be None, 'L', or 'R', got {side!r}")
    (next_order,) = conn.execute(
        "SELECT COALESCE(MAX(set_order), 0) + 1 FROM sets WHERE block_id = ?",
        (block_id,),
    ).fetchone()
    cur = conn.execute(
        """INSERT INTO sets
           (block_id, exercise_id, dict_version, reps, load, distance,
            duration_seconds, scheme, side, set_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (block_id, exercise_id, dict_version, reps, load, distance,
         duration_seconds, scheme, side, next_order),
    )
    return cur.lastrowid


@dataclass(frozen=True)
class LoggedSet:
    date: str
    style: str
    exercise_id: str
    reps: int | None
    load: float | None
    scheme: str | None
    side: str | None
    distance: float | None = None
    duration_seconds: int | None = None


def fetch_sets(conn: sqlite3.Connection, user_id: int, since: str | None = None) -> list[LoggedSet]:
    """All of this user's logged sets, most recent first. `since` is an
    inclusive ISO date. Scoped by user_id - this is the one place a bug
    here would leak one user's training history into another's advice.
    """
    query = """
        SELECT sessions.date AS date, blocks.style AS style, sets.exercise_id AS exercise_id,
               sets.reps AS reps, sets.load AS load, sets.scheme AS scheme, sets.side AS side,
               sets.distance AS distance, sets.duration_seconds AS duration_seconds
        FROM sets
        JOIN blocks ON sets.block_id = blocks.id
        JOIN sessions ON blocks.session_id = sessions.id
        WHERE sessions.user_id = ?
    """
    params: tuple = (user_id,)
    if since is not None:
        query += " AND sessions.date >= ?"
        params = (user_id, since)
    query += " ORDER BY sessions.date DESC, blocks.block_order DESC, sets.set_order DESC"
    rows = conn.execute(query, params).fetchall()
    return [
        LoggedSet(
            r["date"], r["style"], r["exercise_id"], r["reps"], r["load"], r["scheme"], r["side"],
            r["distance"], r["duration_seconds"],
        )
        for r in rows
    ]


# --- LLM-assisted logging ----------------------------------------------------
#
# Evaluation data for webapp/parse_entry.py. Nothing in scoring reads these.

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_parse_run(
    conn: sqlite3.Connection,
    user_id: int,
    input_kind: str,
    raw_text: str,
    model: str,
    prompt_version: str,
    output_json: str | None,
    error: str | None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    image_type: str | None = None,
    image_bytes: bytes | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO parse_runs
           (user_id, created_at, input_kind, raw_text, image_type, image_bytes, model,
            prompt_version, output_json, error, latency_ms, input_tokens, output_tokens)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, _now(), input_kind, raw_text, image_type, image_bytes, model, prompt_version,
         output_json, error, latency_ms, input_tokens, output_tokens),
    )
    conn.commit()
    return cur.lastrowid


_PARSE_RUN_COLUMNS = """id, user_id, created_at, input_kind, raw_text, image_type, model,
    prompt_version, output_json, error, latency_ms, input_tokens, output_tokens"""


def get_parse_run(conn: sqlite3.Connection, run_id: int, user_id: int | None = None) -> dict | None:
    """One parse run (without the image bytes). Scoped to `user_id` unless
    it's None, which only the admin pages pass."""
    query = f"SELECT {_PARSE_RUN_COLUMNS} FROM parse_runs WHERE id = ?"
    params: tuple = (run_id,)
    if user_id is not None:
        query += " AND user_id = ?"
        params = (run_id, user_id)
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def get_parse_image(conn: sqlite3.Connection, run_id: int) -> tuple[str, bytes] | None:
    row = conn.execute(
        "SELECT image_type, image_bytes FROM parse_runs WHERE id = ? AND image_type IS NOT NULL",
        (run_id,),
    ).fetchone()
    return (row["image_type"], bytes(row["image_bytes"])) if row else None


def count_parse_runs_since(conn: sqlite3.Connection, user_id: int, since_iso: str) -> int:
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM parse_runs WHERE user_id = ? AND created_at >= ?", (user_id, since_iso)
    ).fetchone()
    return n


def record_parse_outcome(
    conn: sqlite3.Connection,
    parse_run_id: int,
    piece_index: int,
    action: str,
    prefill: dict,
    submitted: dict | None,
    edited: bool | None,
) -> None:
    conn.execute(
        """INSERT INTO parse_outcomes
           (parse_run_id, piece_index, action, prefill_json, submitted_json, edited, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (parse_run_id, piece_index, action, json.dumps(prefill),
         json.dumps(submitted) if submitted is not None else None,
         None if edited is None else int(edited), _now()),
    )
    conn.commit()


def parse_outcomes(conn: sqlite3.Connection, parse_run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM parse_outcomes WHERE parse_run_id = ? ORDER BY piece_index, id",
        (parse_run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_parse_runs(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Newest first, across all users - admin only."""
    rows = conn.execute(
        f"""SELECT {_PARSE_RUN_COLUMNS} FROM parse_runs ORDER BY id DESC LIMIT ?""", (limit,)
    ).fetchall()
    runs = [dict(r) for r in rows]
    for run in runs:
        run["outcomes"] = parse_outcomes(conn, run["id"])
    return runs


def users_overview(conn: sqlite3.Connection) -> list[dict]:
    """Every user with how much they've logged and parsed - admin only."""
    rows = conn.execute(
        """SELECT users.id AS id, users.email AS email, users.created_at AS created_at,
                  (SELECT COUNT(*) FROM sessions WHERE sessions.user_id = users.id) AS sessions,
                  (SELECT COUNT(*) FROM parse_runs WHERE parse_runs.user_id = users.id) AS parses
           FROM users ORDER BY users.id"""
    ).fetchall()
    return [dict(r) for r in rows]

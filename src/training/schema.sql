-- user -> session -> block -> set, per GUIDE.md Part B "Data model".
-- Log what was done, never what was prescribed.
--
-- Multi-tenant since the webapp (docs/training/STATUS.md): every session
-- belongs to exactly one user; the exercise dictionary (exercises.json)
-- stays global/shared, only the log and the scoring config are per-user.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL  -- ISO 8601 timestamp
);

-- One row per user, created alongside it (see db.create_user). Friendly
-- knobs, not an AdvisorConfig directly - user_config.build_config()
-- translates these into one. disabled_styles is a JSON array of style
-- names ('light'/'heavy'/'hhr'), e.g. '["heavy"]' for "no heavies".
CREATE TABLE IF NOT EXISTS user_settings (
    user_id             INTEGER PRIMARY KEY REFERENCES users(id),
    volume_multiplier   REAL NOT NULL DEFAULT 1.0,
    conditioning_focus  REAL NOT NULL DEFAULT 1.0,
    disabled_styles     TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sessions (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    date    TEXT NOT NULL,  -- ISO 8601 YYYY-MM-DD
    notes   TEXT
);

-- `summary` and `piece_group` both exist for reading the log back as a
-- human, not for scoring (nothing in advisor.py reads either).
--
-- summary: the piece written out in plain text at log time ("3 RFT: 20 air
-- squats, 40 double-unders"). Derived from the sets, but stored rather than
-- recomputed, on the same reasoning as dict_version on `sets` - it records
-- what the piece *was* even if the dictionary's exercise names change
-- later.
--
-- piece_group: blocks written by one submission share it. A block holds a
-- single style, so a mixed-style piece (heavy squat into a run) becomes
-- two blocks; this is what lets it still display as the one piece it was.
CREATE TABLE IF NOT EXISTS blocks (
    id               INTEGER PRIMARY KEY,
    session_id       INTEGER NOT NULL REFERENCES sessions(id),
    style            TEXT NOT NULL CHECK (style IN ('light', 'heavy', 'hhr')),
    structure        TEXT,     -- e.g. '5x5', '21-15-9', 'AMRAP 12'
    summary          TEXT,
    piece_group      INTEGER,
    rest_seconds     INTEGER,
    time_cap_seconds INTEGER,  -- the cap that was set, if any
    result_seconds   INTEGER,  -- how long it actually took: the score
    block_order      INTEGER NOT NULL
);

-- Every measurement column is nullable because different movements are
-- measured differently, not because the data is optional: a back squat set
-- has reps and load; a run has distance and/or duration and neither reps
-- nor load; double-unders have reps but no load. Nothing in scoring reads
-- any of them (build_log_stats only needs date/style/exercise), so these
-- exist for the human's own record and for the volume/e1RM analysis
-- GUIDE.md specs under "Deliberately absent".
CREATE TABLE IF NOT EXISTS sets (
    id               INTEGER PRIMARY KEY,
    block_id         INTEGER NOT NULL REFERENCES blocks(id),
    exercise_id      TEXT NOT NULL,     -- matches "id" in exercises.json
    dict_version     INTEGER NOT NULL,  -- exercises.json "version" at log time
    reps             INTEGER,
    load             REAL,
    distance         REAL,              -- metres; monostructural work
    duration_seconds INTEGER,           -- monostructural work, or a timed hold
    scheme           TEXT,              -- rep-scheme label; heavy style only
    side             TEXT CHECK (side IN ('L', 'R') OR side IS NULL),  -- NULL = bilateral
    set_order        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blocks_session ON blocks(session_id);
CREATE INDEX IF NOT EXISTS idx_sets_block ON sets(block_id);
CREATE INDEX IF NOT EXISTS idx_sets_exercise ON sets(exercise_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
-- users.email already has an implicit unique index from its UNIQUE constraint.

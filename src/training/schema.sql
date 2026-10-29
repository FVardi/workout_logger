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

-- LLM-assisted logging (webapp/parse_entry.py). A parse run is one free-text
-- note or photo turned into draft pieces; each draft piece then pre-fills
-- the ordinary log form, and parse_outcomes records what the person did
-- with it. Kept as evaluation data for the parser - nothing in scoring reads
-- either table. The image is stored in the database rather than on disk
-- because the hosted version has no persistent disk.
CREATE TABLE IF NOT EXISTS parse_runs (
    id             INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    created_at     TEXT NOT NULL,
    input_kind     TEXT NOT NULL CHECK (input_kind IN ('text', 'photo')),
    raw_text       TEXT NOT NULL,
    image_type     TEXT,
    image_bytes    BLOB,
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    output_json    TEXT,      -- the parser's draft, as returned
    error          TEXT,
    latency_ms     INTEGER,
    input_tokens   INTEGER,
    output_tokens  INTEGER
);

-- One row per draft piece the person acted on. `edited` compares the form
-- as pre-filled with the form as submitted (blank rows ignored), so the
-- share of unedited pieces is the parser's accuracy as experienced.
CREATE TABLE IF NOT EXISTS parse_outcomes (
    id             INTEGER PRIMARY KEY,
    parse_run_id   INTEGER NOT NULL REFERENCES parse_runs(id),
    piece_index    INTEGER NOT NULL,
    action         TEXT NOT NULL CHECK (action IN ('logged', 'skipped')),
    prefill_json   TEXT NOT NULL,
    submitted_json TEXT,
    edited         INTEGER,   -- 0/1; NULL when skipped
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parse_runs_user ON parse_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_parse_outcomes_run ON parse_outcomes(parse_run_id);

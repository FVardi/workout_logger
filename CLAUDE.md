# workout_logger

A training-recommendation model (log-EWMA over pattern × style cells) and a
multi-tenant FastAPI webapp for logging real sessions into it. Started as
"Part B" of the `math_blog` repo and moved here on 2026-10-29 without its
git history; anything older (including exercises cut from the dictionary)
is in `math_blog`'s history. The blog post explaining the math stays in
`math_blog`.

Read in this order before writing code:

1. This file — the model invariants below are authoritative, and several
   name mechanisms that were deliberately **removed**; don't reintroduce
   them.
2. `docs/GUIDE.md` — the design spec. Prototyped and discarded once
   already, so the guide is more trustworthy than any implementation.
3. `docs/STATUS.md` — what's actually built (the guide doesn't track
   progress), including the webapp's known gaps (no CSRF protection, no
   email verification, etc.) before it could go anywhere near public.
4. `docs/AUDIT.md` — what was cut and why.

Layout:

    src/training/advisor.py              scoring: ewma_score, advise, recommend, AdvisorConfig
    src/training/exercises.json          the exercise dictionary (authored, not fitted)
    src/training/db.py, schema.sql       SQLite layer: users > sessions > blocks > sets
    src/training/user_config.py          friendly per-user settings -> AdvisorConfig
    src/training/simulate.py             synthetic-schedule simulation -> data/
    src/training/webapp/                 FastAPI + Jinja2 app (server-rendered, no SPA)
    tests/                               pytest, all offline
    docs/                                GUIDE, STATUS, AUDIT, ROADMAP, math.tex
    data/                                webapp.db (gitignored, personal) + simulation output
    tracker.bat                          Windows launcher for the webapp

Commands:

    pip install -e .[dev]                                # once per venv
    python -m pytest -q                                  # run tests
    tracker.bat                                          # launch the webapp, or:
    python -m uvicorn training.webapp.app:app --reload   # from an activated .venv

If the webapp returns `disk I/O error`, delete `data\webapp.db-journal` — a
stale journal from an interrupted write. `tracker.bat` does this on startup.

Things that will otherwise be got wrong:

- **Scoring is a single log-EWMA per cell, not a deficit** (dictionary
  v10): `score = -log(ewma/k)`, where `ewma` decays continuously at
  `τ = MEMORY_K/ρ_cell`. `WINDOW_DAYS`, `deficits()`,
  `normalized_deficit()`, `γ`, and the whole resource-cost matrix `R` are
  **gone from the code**, not just deprioritized — don't reference them
  when editing `advisor.py`, they no longer exist. See
  `docs/GUIDE.md`'s Scoring section and `docs/AUDIT.md`
  for the full "what's gone" list.
- **There is no overdue clock at all** across sessions. `overdue_bonus()`,
  `OVERDUE_DAYS`, `OVERDUE_BONUS`, and `CLOCK_EXEMPT_PATTERNS` are all
  gone — don't reference any of them when editing `advisor.py`. `advise()`
  ranks purely by `ewma_score()` now — **not** plus a same-pattern-repeat
  penalty any more, see the next bullet. A neglected cell's score still
  grows without bound on its own, but nothing guarantees it surfaces by a
  specific day. This is a deliberate, unvalidated bet (not an
  ablation-backed cut) — see `docs/AUDIT.md`.
- **The same-pattern-repeat rule is categorical, not scored.**
  `SAME_PATTERN_REPEAT_COEF` is gone; `score_cell()` no longer takes a
  `chosen` argument or computes any penalty. Instead `candidate_cells()`
  excludes a pattern outright once it's been used earlier in the session,
  at every style (not just `hhr`, which is what the rule used to be
  limited to). This is a within-*session* hard rule about variety, a
  different thing from the cross-*session* overdue clock in the bullet
  above — don't conflate the two when someone says "no hard override."
- **Seven patterns**: `squat`/`hinge`/`push`/`pull`/`flexion`/`brace`/
  `engine`. `PATTERN_WEIGHT` is `1.0` for every pattern now, including
  `brace` — its old `0.25` discount is gone (it was never derived from
  anything about brace itself, just reverse-engineered to fit a capacity
  budget; see `docs/AUDIT.md`). Don't reintroduce a `brace`
  discount on the assumption it was physiologically motivated.
- **`engine` holds only exercises whose `E` row would otherwise be
  empty**: running, rowing, burpee, double-unders, biking, box jump-over,
  burpee box jump-over (in the current 29-movement dictionary). A jump is
  triple extension, but it is not squat *training* — box jumps were
  deliberately zeroed in the pre-prune dictionary and they get engine here
  for the same reason burpees do. That restriction is load-bearing, not
  incidental — it's what preserves `E`'s row-stochasticity. Do **not** add
  engine credit to a mixed-modal movement: giving a thruster engine credit
  *subtracts* it from squat and push, because weights split volume and
  never duplicate it. Neither `flexion` nor `engine` has a
  heavy-admissible exercise, which means neither can ever have an `hhr`
  cell either (`hhr` requires a heavy sibling — see the next bullet); both
  are light-only patterns, structurally, not just for now.
- **`hhr` admissibility means "this exercise is also heavy-admissible,"
  not "this raises your heart rate."** Matches GUIDE.md's own definition
  (a loaded movement at reduced load, ~60-70% 1RM) — the dictionary used
  to also mark unloaded bodyweight moves (`push_up`, `air_squat`,
  `toes_to_bar`, `burpee`, `double_unders`) as `hhr`, which is now
  corrected. If you add an exercise and want it `hhr`-admissible, it needs
  `heavy` too, or the pairing doesn't mean anything. **Three deliberate
  exceptions**, all `hhr` with no `heavy`: `kb_swing`, `devils_press`,
  `man_maker`. Flagged in `docs/STATUS.md`; don't "fix" them by
  adding `heavy` back. Three exceptions out of 29 is the rule showing
  strain — it was written for barbell movements and dumbbell conditioning
  movements don't obey it. Treat it as a heuristic, not an invariant, and
  don't add a test asserting it.
- **`rest` is a dictionary entry with an empty `E`.** Not an exercise —
  it exists so interval work is expressible as movement rows. Empty `E`
  means never suggested and zero exposure; don't give it pattern weights.
  Its style is inherited from the preceding movement row in the log form
  (`webapp/app.py`'s `REST_ID`), so it never splits a piece into an extra
  block.
- **The dictionary was pruned from 63 to 16 exercises (v10), then revised
  to 19, then grown to 29** (plus `rest`, so 30 entries). The review pass
  removed `power_clean`, added `bench_press`/`front_squat`/
  `double_unders`/`biking`, and added then re-removed `kipping_pull_up`.
  The growth pass added ten movements the user actually trains:
  `db_snatch`, `kb_snatch`, `db_thruster`, `db_box_step_over`,
  `devils_press`, `man_maker`, `hspu`, `suitcase_carry`,
  `burpee_box_jump_over`, `box_jump_over`. That is the intended path —
  start minimal, add on contact with real logging, never speculatively.
  Exercises cut at any point are in `math_blog`'s git history, not deleted; don't treat
  their absence as an oversight.
- **A new exercise can create a new admissible cell as a side effect.**
  `admissible_cells()` is derived, not declared: any (pattern, style) with
  `E[pattern] > 0` and `style` admissible becomes a cell that the model
  will then expect you to service. `man_maker` is `hhr`-only for exactly
  this reason — as `light` it would have minted `pull`/`light` and
  `brace`/`light`, each served by nothing but man makers at weight `0.3`
  and `0.1`, so both would read chronically behind forever. Check the cell
  count before and after adding a light-admissible exercise with a long
  `E` row. There is still **no `pull`/`light` cell**; if one is wanted, it
  should arrive by deliberately adding a light pull (ring row, kipping
  pull-up), not as a by-product.
- **`TARGET_RATE = {"light": 1.0, "heavy": 0.75, "hhr": 0.25}`** — raised
  from `{1.35, 0.50, 0.20}` in the same review pass. If you're computing
  capacity or interval numbers by hand, use the current values; anything
  citing `1.35`/`0.50`/`0.20` or a `12.31/week` demand figure is describing
  the pre-review-pass state.
- **Every scoring function takes an optional `config: AdvisorConfig`**
  (default `DEFAULT_CONFIG`, which reproduces `TARGET_RATE`/
  `PATTERN_WEIGHT`/`HHR_CREDIT`/`MEMORY_K` exactly). This exists for the
  webapp's per-user settings (`src/training/user_config.py` translates
  friendly knobs into a config) — don't read the module-level constants
  directly inside `tau_cell()`/`build_log_stats()`/etc. when adding new
  logic; read them off `config` so per-user overrides keep working.
  `DEFAULT_CONFIG.target_rate`/`.pattern_weight` are their own dict copies
  made at import time, **not** the same objects as `TARGET_RATE`/
  `PATTERN_WEIGHT` — monkeypatching the module constants in a test won't
  affect `DEFAULT_CONFIG`, see `test_tau_scales_inversely_with_pattern_weight`.
- **`ewma == 0` scores `+inf`, not a large finite number** — a cell never
  trained at a given style should outrank everything, and there's no
  principled ceiling to give it instead. `recommend()`'s style comparison
  uses the *median* score across a style's cells, specifically because a
  mean would let one such `+inf` cell dominate an otherwise well-covered
  style.
- `hhr` credits **0.5** toward the heavy cell's own EWMA (decayed on
  heavy's own `τ`), never 1.0, and never updates heavy's
  last-literally-trained date.
- Scores **rank**; they are not reachable goals, and `score = 0` at target
  is only exact in the continuous-time limit — the real discrete-event
  steady state at `MEMORY_K = 2` reads slightly ahead of 0 even when
  training exactly on target. See `docs/AUDIT.md`.
- Never fit the `E` or `R` matrices from data. They are authored judgment.
- Unilateral reps are logged **per side**.
- **The scorer reads only `(date, style, exercise_id)`** and collapses
  same-day repeats. Reps, load, distance, duration, structure, summary and
  result time are stored but never reach scoring.
- **`bcrypt` is called directly, not via passlib.** passlib 1.7.4's
  `detect_wrap_bug` self-test raises `ValueError: password cannot be longer
  than 72 bytes` against bcrypt ≥ 4.1 on the first call, whatever the
  password. Don't reintroduce passlib.
- **Don't group `summarise_sets` by exercise** — it breaks any workout that
  repeats a movement. Grouping is positional via `_cycle_length`, with a
  consecutive-run fallback.
- **Webapp tests assert behaviour, not exact HTML.** Exact-HTML assertions
  broke on every restyle; prefer checking `/coverage` numbers.

How to work here:

- Design decisions: propose, then wait. Don't batch decisions or make them
  ahead of the user.
- Always say explicitly what's been run and what's only been written.
- Update `CLAUDE.md` / `docs/STATUS.md` / `docs/GUIDE.md` / `docs/AUDIT.md`
  in the same pass as the code, not afterwards.
- Not a research project — not meant to be 100% rigorous, but precise.

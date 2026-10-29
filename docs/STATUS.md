# training — status

Living doc. Overwrite when things change; don't append a changelog here
(git history is the changelog — for anything before 2026-10-29 that's
the `math_blog` repo's history, where this project started). Spec: `docs/GUIDE.md`. Open
parameter-review items: `docs/AUDIT.md`.

## Built and working

Following GUIDE.md's build order, all four steps have a first draft:

1. **Exercise dictionary** — `src/training/exercises.json`, version 10, 30
   entries (29 movements plus `rest`) with authored `E` weights,
   admissible styles, laterality, implement. Seven patterns —
   `squat`/`hinge`/`push`/`pull`/`flexion`/`brace`/`engine`. Pruned from
   63 exercises to 16 in this version, revised to 19 in a later review
   pass, then grown to 29 once real logging surfaced missing movements —
   see the reversal below, "Review pass", and "Growth pass" further down.
2. **SQLite log** — `src/training/schema.sql` + `src/training/db.py`,
   `session -> block -> set` per GUIDE.md's data model. Unchanged by
   anything below: the schema still logs raw sets, and none of the
   scoring reversal required a migration.
3. **`advisor.py`** — all four query surfaces implemented: `advise()`,
   `recommend()`, `suggest_rep_scheme()`, `suggest_rep_count()`. Scoring
   is a single log-EWMA per cell; there is no hard override of any kind —
   see below.
4. **Logging** — **started**, via a webapp rather than a script — see
   "Webapp" below. GUIDE.md's build order (step 4: "log for months before
   trusting any output") is still the right caution about *trusting* the
   model's output, not a reason to delay building the interface that logs
   into it.

## Reversal: log-EWMA scoring replaces deficit/window/staleness/γ; `R` retired; dictionary pruned to 16 exercises; overdue clock removed entirely (v10)

Made in one pass, before any real logging started, on the reasoning that
testing a model this elaborate before it had ever seen real data was the
wrong order of operations — and that most of the elaboration was already
known, by this project's own ablation record, to be near-noise. A second,
independent working session arrived at the same core idea (a single
decaying average per cell, one hard clock, drop the resource matrix)
starting from scratch, which was the trigger for reopening this rather
than just reading it as a to-do.

**Scoring.** The old score computed four separate things per cell — a
raw exposure count inside a fixed 42-day window, a deficit against
target, `[0,1]` normalization, a capped staleness measure — plus a
cross-style coupling term (`γ`) to stop one style's over-coverage from
starving a pattern's other styles. All five are now one continuous-decay
EWMA per cell:

```
ewma[p,s] ← ewma[p,s] · exp(−Δt / τ[p,s]) + exposure
score[p,s] = −log(ewma[p,s] / k)
```

with `τ[p,s] = k / ρ[p,s]` (`ρ` = that cell's target rate in
exposures/day, `k` a single shared "memory" constant). `score = 0` at the
steady-state on-target value regardless of the cell's own target size (no
separate normalization step needed); during neglect, `score` grows
linearly (staleness, for free); chronic training at half rate settles at
`score = log(2) ≈ 0.69` (deficit, for free). A never-touched cell scores
`+∞`. Full derivation: `docs/GUIDE.md`'s Scoring section.

**One correctness caveat, documented not fixed:** `score = 0` at target
is exact only in the continuous-time limit. The real discrete-event
steady state is `1/(1 − e^(−1/k))`, about 27% above `k` at the current
`k = 2` — training exactly on target forever reads as very slightly
*ahead*, not exactly on the mark. Pinned down numerically in
`test_periodic_steady_state_is_not_exactly_memory_k`. Shrinks as `k`
grows; not urgent enough to change `k` over.

**Overdue clock: gone, in two steps.** First pass collapsed three clocks
(7-day any / 14-day loaded / 24-day classic-heavy) to one (7-day
any-exposure, pattern-level), deleting the entire `CLOCK_EXEMPT_PATTERNS`
bookkeeping `flexion`, `brace`, and `engine` had each separately needed.
Second pass, on request, removed that last clock too — there is now **no
hard override of any kind** left in the model, `overdue_bonus()` and
`OVERDUE_DAYS`/`OVERDUE_BONUS` are gone entirely. The EWMA score already
does most of what the clocks existed to force (a chronically neglected
cell's score grows without bound on its own), so the honest framing is
that the last clock wasn't cut because it was proven redundant — no
ablation showed that — but because a model with zero real log history
doesn't yet have a case where unbounded neglect is an observed problem
rather than a hypothetical one. This is the one cut in the whole reversal
that's a genuine bet rather than a validated simplification; see
GUIDE.md's "No hard override" and "Deliberately absent."

**One concrete side effect, not itself the goal:** `brace`'s open tension
— its `0.25` `PATTERN_WEIGHT` implied roughly half the exposure rate the
7-day clock's floor was forcing — is resolved by removal. With no clock
left to win, `brace` trains at whatever rate its own weight and the score
actually produce, for the first time. Worth watching once real logs
exist; it was previously untestable because the clock was always masking
it.

**`R` (resource cost) is retired, not shrunk.** Cosine-similarity
interference between two exercises' resource profiles, weighted by a `W`
matrix, subtracted from a candidate's score — the mechanism this
project's own ablation record already called near-noise, and which the
independent session's ablation agreed with from a completely different
starting point. Once cut from scoring, nothing else in the code read `R`
at all, so keeping ~130 authored numbers on the books for a dead consumer
had no justification left. `resources`, `resource_interaction`, and every
exercise's `R` field are gone from the dictionary and from
`Exercise`/`Dictionary`.

**Unilateral-shortfall scoring is retired too**, on the same "cut what's
validated near-noise" basis. `laterality` is still authored per exercise
and still logged (`side` on each set) — nothing about the log or the
dictionary schema changed, only the score stopped reading it. Fully
reversible without new data whenever it's worth the term back.

**The dictionary was pruned from 63 to 16 exercises**, independent of the
scoring change but part of the same "test before you elaborate" instinct.
Shrinking the exercise count doesn't shrink the *model* — patterns ×
styles × scoring terms is where the complexity actually lived, and
demand/supply arithmetic barely moved (11.81/week against the same ~12
supply, vs. 12.31 before) — but it does shrink the amount of unvalidated
`E`-weight judgment sitting underneath a model that has never seen a real
log, from ~200 authored numbers to ~25. The 16: back squat, deadlift,
strict press, power clean, thruster (barbell); strict pull-up, push-up,
air squat, toes-to-bar (bodyweight); dumbbell lunge, kettlebell swing,
farmer's carry (dumbbell/kettlebell); bent-over row; running, rowing
(erg), burpee (monostructural). Covers all 19 admissible cells except
`flexion × heavy`, which masks out harmlessly (`flexion` was never
exempt-by-necessity from a heavy target the way the old clock system
required — it just has no heavy-admissible exercise, same as `engine`).
The other ~45 exercises, and every `E`/`R` judgment call made about them
in the barbell and non-barbell audits, are preserved in git history —
recoverable, not deleted, and worth revisiting once logging surfaces an
actual gap rather than a hypothetical one.

**Superseded by this reversal, preserved only in git history:** the
barbell and dumbbell/kettlebell/bodyweight/monostructural `E`/`R` audits,
the `cns`/`aero`/`hang` resource reversals, the interference
normalization-to-cosine-similarity work, and the `[0,1]`
deficit/staleness normalization that used to live in this file's
"Simplification" section. None of it was wrong when it happened — the
non-barbell audit in particular is what surfaced the `midline` archetype
problem that led to `flexion`/`brace`, and the engine and lunge-family
reasoning in that same pass still describes real, live judgment calls
about the surviving exercises. But the specific mechanisms those sections
walk through (`R`, `resource_interference()`, raw deficits, `γ`) no
longer exist to be tuned, so the detailed walkthroughs are no longer
useful as current-state reading. `git log docs/STATUS.md` has
the full record.

## Review pass (still v10): dictionary revised, styles corrected, rates raised, same-pattern rule made categorical

A full walkthrough of every axis — patterns, styles, dictionary,
admissible cells, constants — done exercise-by-exercise and constant-by-
constant before real logging starts, on the same "check it now, while
it's cheap to change" reasoning as the EWMA reversal above. Everything
below landed in one continuous session; grouped here by axis, not by
chronology.

**Dictionary: 16 → 19 exercises.** `power_clean` removed (its only
credit, `hinge`, stays covered by `deadlift` and `kb_swing`).
`bench_press`, `front_squat`, `double_unders`, `biking` added, each
mirroring an existing sibling's admissible styles. `kb_swing`'s `hinge`
weight raised from `0.5` to `1.0`, and it gained `heavy` admissibility —
it's now the only exercise admissible at all three styles besides
`deadlift`. `kipping_pull_up` was added mid-review at full `pull` credit,
then removed again in the same session once the "the kip does part of the
work, not the muscle" objection (never resolved from before the prune)
resurfaced unresolved — net change from before the review is zero;
`strict_pull_up` is still the only pull-up variant.

**Styles: `hhr` admissibility corrected to match GUIDE.md's own
definition.** GUIDE.md always defined `hhr` as a *loaded* movement at
reduced intensity (~60–70% 1RM) under a high heart rate; the dictionary
had drifted to using it more loosely, for "elevated heart rate" generally
— several unloaded bodyweight movements (`push_up`, `air_squat`,
`toes_to_bar`, `burpee`, `double_unders`) carried `hhr` despite having no
heavy sibling to be "the reduced-load version of." All five lost `hhr`;
each is `light`-only now. `deadlift`, `strict_pull_up`, `farmers_carry`,
and `db_lunge` separately lost `light` admissibility (heavy movements that
don't have a meaningfully lighter execution worth tracking as its own
cell). Net structural effect: `pull` and `brace` each lost their `light`
cell, and `flexion`/`engine` — which have no heavy-admissible exercise at
all — lost the *ability* to ever have an `hhr` cell under this rule, not
just their current one. Admissible cells dropped from 19 to 15;
`pull × light`, `brace × light`, `flexion × heavy`, `flexion × hhr`,
`engine × heavy`, `engine × hhr` are all gone, accepted deliberately
("there shouldn't be cells for something I don't do or doesn't make
sense").

**Constants: `TARGET_RATE` raised, `brace`'s `PATTERN_WEIGHT` discount
dropped.** `TARGET_RATE` moved from `{1.35, 0.50, 0.20}` to
`{"light": 1.0, "heavy": 0.75, "hhr": 0.25}` — light work asked for less
often, heavy and hhr more often. `PATTERN_WEIGHT["brace"]` moved from
`0.25` to `1.0`: under questioning, the `0.25` turned out not to be
derived from anything about brace itself (the "bracing is loaded
incidentally elsewhere" argument that sounded like a justification
actually belonged to a different, already-removed mechanism — the old
clock's `flexion`/`brace` exemption) — it was reverse-engineered purely to
fit a capacity budget. Combined effect on weekly demand: **10.0/week
against the same ~12 supply**, down from 12.31/week before this pass (the
six-cell reduction above outweighed the per-cell rate increases) — see
`docs/GUIDE.md`'s "Capacity arithmetic". `MEMORY_K` and
`HHR_CREDIT` were reviewed and kept unchanged.

**`SAME_PATTERN_REPEAT_COEF` removed; the same-session repeat rule is now
categorical at every style, not just `hhr`.** Previously: a pattern
already used in a session couldn't reappear at `hhr` (hard rule), but
could still repeat at `light` or `heavy`, discouraged only by a soft `-1.1`
score penalty. A check against the running simulation found the soft
penalty was **not** near-noise the way every other tuned constant turned
out to be — it flipped the winning candidate in 50% of workout picks,
because the natural score gap between top candidates is almost always
under 1.1 (median 0.07 across 94 comparisons). So it was already behaving
almost like a hard block; it's now an actual one, extended to `light` and
`heavy` as well as `hhr`. `candidate_cells()` excludes any pattern already
used earlier in the session outright; `score_cell()` no longer takes a
`chosen` argument or computes a penalty at all. This only constrains what
`advise()` suggests, not what can actually be trained. See
`docs/GUIDE.md`'s "Rules" and `docs/AUDIT.md`.

## Growth pass (still v10): ten movements added, 19 -> 29

The first additions driven by *logging real sessions* rather than by
auditing the dictionary in the abstract. This is the path GUIDE.md always
specified — prune to a minimum, then add on contact with reality — so the
count moving is the design working, not the prune failing.

Added, with authored `E` rows:

| id | `E` | styles |
| --- | --- | --- |
| `db_snatch` | hinge 0.7, push 0.3 | heavy, hhr |
| `kb_snatch` | hinge 0.7, push 0.3 | heavy, hhr |
| `db_thruster` | squat 0.5, push 0.5 | heavy, hhr |
| `db_box_step_over` | squat 0.8, brace 0.2 | heavy, hhr |
| `devils_press` | hinge 0.5, push 0.5 | light, hhr |
| `man_maker` | push 0.35, pull 0.3, squat 0.25, brace 0.1 | hhr |
| `hspu` | push 1.0 | heavy, hhr |
| `suitcase_carry` | brace 1.0 | heavy, hhr |
| `burpee_box_jump_over` | engine 1.0 | light |
| `box_jump_over` | engine 1.0 | light |

Three judgment calls worth knowing about, because a later pass will
otherwise read them as mistakes:

**`man_maker` is `hhr`-only to avoid minting two starved cells.**
`admissible_cells()` is derived from the dictionary, so a light-admissible
exercise with a long `E` row silently creates cells. As `light`, man maker
would have been the sole server of `pull`/`light` (weight 0.3) and
`brace`/`light` (weight 0.1) — both would read chronically behind forever,
because nothing else in the dictionary can service them. There is
deliberately **still no `pull`/`light` cell**. If one is wanted it should
be introduced on purpose, by adding a light pull (ring row, kipping
pull-up), not as a by-product of one dumbbell movement. Cell count is
unchanged at 15.

**The `hhr`-requires-`heavy` rule now has three exceptions**, not one:
`kb_swing`, `devils_press`, `man_maker`. The rule was written against
barbell movements, where "same lift at 60-70% 1RM" is exactly meaningful.
Dumbbell conditioning movements don't have a heavy expression anyone
actually trains, yet they are unambiguously the thing `hhr` was invented
to capture. Three exceptions out of 29 is the rule showing strain — treat
it as a heuristic for authoring new entries, not an invariant, and don't
add a test asserting it.

**Jumps get `engine`, not `squat`.** `box_jump_over` and
`burpee_box_jump_over` are triple extension but they are not squat
*training*; box jumps were deliberately zeroed in the pre-prune
dictionary, and giving them squat credit now would subtract from a real
squat cell without any real squat stimulus. Same reasoning that put
`burpee` in `engine`.

**Verified 2026-10-29.** This pass was first written without a working
sandbox; the full suite (196 tests) has since been run and passes,
including the 30-entry count in
`test_dictionary_v10_is_the_pruned_minimal_set`, the ladder
`rounds` + `interval_seconds` tests, and the three-column Today page
tests. `tracker.bat` itself is still unrun (Windows-only).

## Tests

22 tests in `tests/test_advisor.py`, all offline (in-memory
SQLite, no real log needed). Two were updated for the review pass above
rather than the EWMA reversal: the dictionary-shape test now expects 19
exercises, not 16; the pattern-weight-scaling test no longer asserts
against `brace`'s literal (now-retired) `0.25` and instead monkeypatches a
synthetic weight, so it pins the *mechanism* (`τ` scales inversely with
`PATTERN_WEIGHT`) rather than a specific value. Otherwise still cover, from
the EWMA reversal: the EWMA score's own derivation (`score(k)==0`,
`score(k/2)==log(2)`, `score(0)==+inf`), the documented discrete-steady-state
gap at `k=2`, exact exponential decay against a hand-computed expected
value, that same-day sets and same-day unilateral sides still collapse to
one exposure event under the new recursion, that hhr credits into heavy's
EWMA without touching heavy's own "last-literally-trained" date, that
logging a cell improves its score, every structural invariant carried over
unchanged (admissible-cell coverage and determinism, the hard same-session
repeat rule — now checked at `hhr` only, still holds under the widened
rule since widening a hard exclusion can't break a narrower case of it),
that `R`/`resource_interference`/the old deficit machinery/the overdue
clock are all actually gone, that `LogStats` no longer tracks unilateral
exposures or pattern-level last-seen, that a cold-start empty log still
ranks every candidate via `ewma_score`'s own `+inf` rather than any clock,
and `recommend()`'s cold-start guard (both top styles at `+inf` shouldn't
produce a fake "clear" verdict from a tie) alongside its two carried-over
behavioral tests (favours the neglected style; doesn't favour the
least-overshot one when everything's ahead).

## Simulation

`src/training/simulate.py` — unchanged in structure (closed-loop
self-consistency check, not real data; 3 sessions/week, one heavy piece
then four workout pieces), only its report format changed to show EWMA
scores instead of deficits. Run `python -m training.simulate`.

Current report, against dictionary v10 post-review-pass: **all 19
exercises used**, and every cell's final score sits within roughly
−0.86 to −0.14 of on-target — tighter and entirely on the "ahead" side,
compared to the pre-review-pass run (−0.6 to +0.02, with some cells
already reading behind). Consistent with the restored capacity slack
(10.0/week demand against ~12 supply, vs. 12.31/week before).

**"Longest gaps at end of simulation"**: days since each admissible cell
was literally last trained, sorted worst first — the direct check for
whether the missing overdue clock lets anything visibly languish. Current
run: worst gap is `hinge/heavy` and `push/hhr`, both at 12 days; nothing
else exceeds a week. A real improvement over the pre-review-pass run
(worst gap `brace/heavy` at 26 days) — mostly the six removed admissible
cells taking pressure off the ones that remain, not a change to the
no-clock mechanism itself. Re-check this table every time the simulation
changes; nothing is structurally preventing a much larger gap the way the
old clock used to.

The full math write-up (`docs/math.tex`) is **not** yet updated
for this reversal — its Scoring and Overdue sections, and its worked
example, are all built on the retired deficit/window/clock machinery.
Flagged clearly inline with an auditbox rather than silently left to go
stale; full rewrite is a separate, tracked pass. See "Known open items."

## Webapp

`src/training/webapp/` — FastAPI + server-rendered Jinja2 templates. Sign
up, log in, log a piece, see today's `advise()`/`recommend()` output, a
coverage table, history, and per-user settings. Run locally:

```
pip install -e .[webapp]
uvicorn training.webapp.app:app --reload
```

Defaults to `data/webapp.db` (gitignored — real training data,
never committed; see `.gitignore`) unless `TRAINING_DB_PATH` is set.

**Styling — matched to the blog (the separate `math_blog` repo), deliberately swappable.** `base.html`
loads Bootswatch **cosmo** from a CDN: the same Bootstrap 5 theme
`_quarto.yml` uses (`theme: [cosmo, styles.scss]`), so the tracker and the
blog look like one product. `static/app.css` carries the four things the
blog's `styles.scss` overrides — Source Sans 3, `#1c1c1e` body, `#1b5e8c`
links, tabular-nums tables — plus the app-only rules.

**Every theme decision lives in those two places**, one CDN link and one
stylesheet; templates carry structure and Bootstrap utility classes only.
That's on purpose: the tracker may become its own product with its own
identity, and when it does, re-skinning should be an edit to `app.css`
rather than a pass over every template. Keep `app.css` and the blog's
`styles.scss` (in `math_blog`) in sync, or diverge on purpose — but decide it there.

The tracker **cannot** be folded into Quarto, for the record: Quarto is a
static site generator with no request-time server, and the blog is hosted
on GitHub Pages, which can't run Python at all. Going client-side against
a hosted database would mean reimplementing `advisor.py`'s scoring in
JavaScript and keeping two implementations in agreement forever — the
model is the product, so that trade isn't worth making.

**Pages.**

- `/` (Today) — `recommend()`'s verdict plus the top five heavy and
  workout candidates. Logging redirects back here, so the advice shown
  next already accounts for what was just logged.
- `/coverage` — every admissible (pattern, style) cell with its current
  score, days since literally last trained, and target interval, worst
  first. The live personal equivalent of `simulation_report.md`'s two
  tables. This exists because Today only ever shows each cell's *top*
  candidate, so a mildly-neglected cell never appears there at all — and
  with no overdue clock left in the model (see GUIDE.md's "No hard
  override"), this table is what makes slow neglect visible before it
  becomes a problem. Disabled styles are excluded and noted as such.

  Each row carries a bar, because a signed number is precise but slow to
  scan and "what's behind?" is the only question the page answers. Bar
  width is anchored to **`log(2)`** — the score a cell settles at under
  chronic training at exactly half its target rate — so half-rate reads as
  a half-width bar and twice that fills it. Anchoring to the worst score
  on the page instead was the obvious alternative and is worse twice over:
  a page of near-identical scores renders every bar full-width, and the
  same score would mean different widths depending on what else happened
  to be on screen.
- `/log` — a **date** (defaulting to today, so a session can be logged
  after the fact), that day's already-logged pieces, then a **workout-type
  dropdown**. The date isn't cosmetic: it's the day the exposure lands on,
  which is what the EWMA decays from. Each workout type is its own `<form>`
  and so carries its own date field, kept in sync client-side so switching
  type doesn't silently revert to today; the already-logged panel follows
  the chosen date rather than always showing today.

  **Future dates are refused** (with a day's leeway for timezone skew),
  because `build_log_stats()` ignores sets dated after the day it's
  scoring — a future-dated piece would look logged and count for nothing
  until that date arrived.

  Alongside the date, an optional **result time** — how long the piece
  actually took — accepted as `12:30` or as plain minutes (`7.5`), stored
  in `blocks.result_seconds`. Deliberately separate from
  `time_cap_seconds`: the cap is what was set beforehand, the result is
  the score, and a capped piece can have both. Nothing in scoring reads
  it; the model knows a piece happened, not how fast it was. Every type's form is rendered up front and toggled
  client-side, so switching types doesn't reload the page or lose
  half-entered input. Submitting redirects back here rather than to Today,
  so a whole session gets logged without re-navigating.

  Metcon-style types use **movement rows**: movement, amount, unit
  (reps / metres / seconds), load, style. One amount plus a unit, rather
  than separate reps/distance/duration fields — the app routes it to the
  right column, so nothing about the schema leaks into the form. Three
  rows render initially and "+ Add movement" clones more client-side (the
  server scans up to 20, so added rows can't silently vanish).

  Five types, all expanded by `webapp/session_templates.py` (pure
  functions, unit-tested separately in
  `tests/test_session_templates.py`):

  | template | input | expands to |
  |---|---|---|
  | strength | per-set rows of reps + load | one set per row; structure `5/4/3/2/1` |
  | rounds | rounds × movement rows | each movement once per round; `3 RFT` |
  | ladder | rep scheme + movements | reps from the scheme per round; `21-15-9` |
  | emom | total minutes + interval + movements | one movement per interval, alternating; `EMOM 12` |
  | amrap | cap, movements, rounds done (+partial) | rounds expanded; `AMRAP 12` |

  **EMOM alternates rather than repeating every movement per round**, which
  is the whole reason it isn't a relabelled `rounds`: twelve minutes of
  thrusters and burpees is six of each, not twelve of both. Getting that
  backwards would double the recorded volume, so it has its own test. A
  longer interval divides the total — every two minutes for twenty is ten
  intervals, labelled `E2MOM 20`. The rest within each interval is implied
  by the format and isn't logged, unlike the explicit `rest` rows an
  interval piece built from `rounds` would use.

  **There was a fifth, `engine`, and it's gone.** Running, rowing and
  biking were hidden from the movement dropdowns and confined to it. That
  made sense while a movement row carried only reps and load — neither
  means anything for a 400 m run — but the row later gained a *unit*
  column and the exclusion was never revisited. The effect was that
  "3 rounds: 400 m run, 20 air squats" couldn't be logged through the UI
  at all, even though the endpoint accepted it fine. Run/row/bike are
  ordinary movements now, measured in metres or seconds, and intervals are
  just rounds: `4 × 400 m` is four rounds of one 400 m movement, storing
  the same four sets. `expand_engine()` went with the template; git
  history has both.

  That was a *form* change, not a model change. `engine` is still a
  pattern in `exercises.json`, for the reason GUIDE.md gives: those
  movements build no transferable movement pattern, so without it their
  `E` row would be empty and they'd never be suggested at all.

  Strength is **per-set**, not "N sets × R reps": a wave up in load
  (5@80, 4@85, 3@90, 2@95, 1@100) is one piece and each set keeps its own
  load. `blocks.structure` and `blocks.time_cap_seconds` are populated for
  the first time — columns that existed in the schema from the start and
  had never been written. Unilateral exercises still write L+R per set.
- `/history` — **one row per piece**, newest first: the plain-text summary,
  the styles involved, and per-movement volume totals. Three rounds of two
  movements is six set rows in storage and one row to read; storage keeps
  the sets (volume and PR analysis need them), the display shows the piece.
  Each row has **Edit** and **Delete**.

  **Edit works on the piece's stored sets, not on the template you filled
  in** — the template inputs aren't kept, only the expansion, so a form
  claiming to remember "3 rounds of…" would be pretending. One row per set,
  each with movement, amount, unit, load and style, plus the piece's date
  and label. That covers what actually needs correcting (wrong load, wrong
  movement, wrong style, wrong day); changing the *round count* is a delete
  and re-log. Storing the template inputs to make edits round-trip exactly
  is a later addition if it turns out to matter, not a rewrite.

  Saving **rewrites the piece** rather than patching rows: a set whose
  style changed has to move to a different block (a block holds one style),
  so the edit re-expands through the same commit path the log form uses
  rather than a second implementation of the same grouping. The summary is
  regenerated at the same time.

  A piece is addressed by `(session_id, piece_group)`, which arrives from a
  form field and is therefore attacker-supplied — `db.delete_piece()` and
  `db.fetch_piece()` re-check ownership server-side and no-op rather than
  trusting it. Deleting also removes a session that the deletion emptied,
  which would otherwise linger as a training day on which nothing happened.
- `/settings` — volume multiplier, conditioning focus, disabled styles.

Pieces logged *before* those columns existed have NULL for both, and the
display's "one block, one piece" fallback would split a mixed-style one in
two. `db._backfill_piece_groups()` fixes them on connect: consecutive
blocks in a session sharing a `structure` are one piece until a style
repeats — reliable rather than a guess, because the expansion groups a
submission's sets by style, so each style appears at most once per piece.
A missing `summary` is regenerated for display from the stored sets
(`session_templates.summarise_sets()`), which is also what keeps the two
phrasings identical.

**Summaries describe one round, in order.** A piece's sets are stored
round-major, so `summarise_sets()` finds the repeating cycle and groups by
*position within it* rather than by exercise. Grouping by exercise — which
it did originally — collapses every occurrence of a movement together, so
"4 rounds of: 2 min bike, 10 T2B, 2 min bike, 60 DU, 2 min bike, 10
burpees" described itself as having one bike in it. The sets were always
stored correctly; only the description was wrong. Where there's no
repeating cycle (a strength wave, or a mixed-style piece whose sets were
split across blocks and so aren't round-major any more) it groups
*consecutive* runs of the same exercise instead — which merges a wave's
five back squats into one description without merging a bike that appears
at the start and again at the end.

**A piece is the display unit; sets are the storage unit.** `blocks` gained
`summary` (the piece in plain text, written at log time) and `piece_group`
(shared by the blocks one submission produces). Both exist for reading the
log back — nothing in `advisor.py` touches either. The summary is *stored*
rather than recomputed on the same reasoning as stamping `dict_version` on
each set: it records what the piece was, even if exercise names change
later. `piece_group` is what lets a mixed-style piece, which has to become
two blocks because a block holds one style, still read back as the one
piece it was. Volume totals are computed from the sets on the fly, so they
can't drift from what's actually stored.

**Style is per movement, never derived from the template.** A 3-rep back
squat at 90% into a 400 m run is a *heavy* stimulus that happens to sit
inside a workout container — GUIDE.md already says as much ("`hhr` is a
stimulus, not a session type"), and the model has no 1RM to infer
intensity from anyway (see "Deliberately absent"). So each movement row
carries its own style, and a mixed-style piece becomes one block per
style sharing a structure label.

**Choosing that style is the main logging friction, and three things
reduce it.** All three only ever *pre-select* — the model scores whatever
is submitted, and the control stays editable.

1. **Options are restricted to what the exercise admits.** Eight of the
   nineteen exercises allow only `light`, so there's no question to ask;
   ten allow `heavy` or `hhr`, so it's one binary pick. (This was computed
   but not wired into the form until now — an earlier version of this
   section claimed it worked when it didn't.)
2. **The default is the style that exercise was last logged at** — learned
   from the log rather than prescribed, the same principle as
   `suggest_rep_count()`.
3. **A load at or above `HEAVY_LOAD_FRACTION` (0.8) of your own heaviest
   ever logged for that lift pre-selects `heavy` instead**, via
   `db.heaviest_load_for_exercise()`. That's a stand-in for a 1RM used
   *only* by the form; nothing in scoring reads it, so "the model doesn't
   track maximal strength" still holds. Silent until the lift has been
   logged with a load, and guessed selections are marked in the UI —
   a wrong guess you don't notice is worse than no guess.

**Rest is a movement row, not a template.** `rest` is a dictionary entry
with an empty `E` — the documented way to say "never suggested,
contributes no exposure" (see AUDIT.md's note on empty rows). That makes
interval work expressible in the templates that already exist: "8 rounds:
20 s bike, 40 s rest" is a rounds piece with two movement rows, so no
interval or EMOM template is needed for it. A rest row takes the style of
the movement before it rather than carrying its own — otherwise a rest
after an hhr movement would split the piece into a second block and show
up as a second style, for something that isn't training.

**`kb_swing` is no longer heavy-admissible** (`light`/`hhr` only). No cell
was lost: `deadlift` still covers `hinge × heavy`. But note this breaks
the rule that `hhr` means "a heavy-admissible movement at reduced load" —
kb_swing is now the one exercise admitting `hhr` without a `heavy`
sibling. Flagged rather than silently resolved, because the alternative
(dropping its `hhr` too) would remove what is arguably the archetypal
high-heart-rate loaded movement.

**Engine work is measured in distance/duration, not reps/load.** `sets`
gained `distance` and `duration_seconds` columns, applied to existing
databases by an idempotent additive `ALTER TABLE` in `db._migrate()`
(`CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so new
columns need this). Double-unders and burpees stay rep-counted despite
being engine-pattern, so they live under the rep-based templates.

**Tested** across `tests/test_webapp.py`,
`test_session_templates.py` and `test_db.py` — 149 in the training suite,
with the webapp ones skipped unless the `webapp` extra is installed: auth
and access control, every template's expansion and what it writes to
`blocks`/`sets`, all three row units landing in the right column, rows
past the initially-rendered three being read, that many sets of one
exercise still count as exactly one exposure, that a heavy movement inside
a metcon stays heavy, mixed-style block splitting, engine distance and
duration handling, unilateral side handling, per-exercise style
restriction and the last-used and load-based defaults, edit and delete
including block relocation on a style change and session cleanup on an
emptied day, rejection of inadmissible exercise/style pairs and malformed
input, coverage contents and disabled-style filtering, settings
round-trip, and — the properties that matter most for a multi-tenant log —
that one user's history and recommendations never leak into another's,
and that a hand-edited piece address can't reach another user's log.

**Multi-tenant from the schema up, not bolted on.** `schema.sql` gained
`users` and `user_settings` tables and a `user_id` FK on `sessions`;
`db.py`'s `fetch_sets()`/`log_session()` are scoped by `user_id`
throughout, and `tests/test_db.py`'s
`test_fetch_sets_never_leaks_across_users` pins the isolation directly
rather than trusting it by construction. `exercises.json` (the dictionary)
stays global and shared across users — only the log and the scoring
config are per-user.

**Per-user adjustable parameters, via a new `AdvisorConfig`.**
`advisor.py`'s `TARGET_RATE`/`PATTERN_WEIGHT`/`HHR_CREDIT`/`MEMORY_K` were
module-level constants; every scoring function now takes an optional
`config: AdvisorConfig` parameter (default reproduces the old constants
exactly, so nothing changed for any pre-existing caller — `simulate.py`
and the whole EWMA reversal's test suite pass unchanged). A new
`user_config.py` translates a user's friendly settings into a concrete
`AdvisorConfig`:

- **volume multiplier** — scales every style's `target_rate` uniformly.
- **conditioning focus** — scales `PATTERN_WEIGHT["engine"]` specifically,
  so "I want more conditioning" surfaces running/rowing/biking/etc. more
  often without touching anything else.
- **disabled styles** — a style excluded outright (e.g. "no heavies").
  Read in exactly one place, `admissible_cells()`; everything downstream
  (`candidate_cells()`, `recommend()`, `build_log_stats()`) derives its
  cell set from that function, so disabling a style there is sufficient
  everywhere, not something each caller re-checks. `advise(mode="heavy")`
  with heavy disabled just returns an empty list — the same shape as any
  other day with nothing eligible, no special case needed.

Deliberately *not* exposed as user knobs yet: `hhr_credit`, `memory_k`,
and per-pattern weight overrides beyond `engine`. `AdvisorConfig` supports
all of them; `user_config.build_config()` just doesn't offer them as
friendly settings yet. Add a knob here, not a new code path in
`advisor.py`, if a future setting needs one.

**Known gaps before this could go anywhere near public**, listed
honestly rather than silently deferred, since "public web app" is the
stated eventual target (see this session's framing):

- **No CSRF protection.** Every mutating route is a plain form POST
  against a session cookie — textbook CSRF exposure. Needs a token before
  any public deployment.
- **No rate limiting** on `/login` or `/signup` — brute-forceable as-is.
- **No email verification or password reset flow.** Anyone can sign up
  with any email string; there's no way to prove ownership or recover an
  account.
- **The session-cookie secret key defaults to a hardcoded dev string**
  (`TRAINING_SECRET_KEY`, `app.py`). Must be set to a real secret — and
  the cookie itself set `https_only` — before this ever runs anywhere
  but localhost.
- **No password strength/breach checking** beyond an 8-to-72-byte range
  (the upper bound is bcrypt's own hard limit, not a design choice — see
  `auth.py`'s `MAX_PASSWORD_BYTES`; enforced explicitly rather than left
  to whichever bcrypt version happens to be installed, since bcrypt<4.1
  truncates silently past 72 bytes and bcrypt>=4.1 raises instead).
- **Delete has no undo and no soft-delete.** It asks for confirmation and
  names the piece, but a confirmed delete is gone. Fine for one trusted
  user; a real product wants a trash or an audit trail.

None of these block local, personal use (the actual next step — see
"Next step" below); all of them block the "public web app" direction
specifically.

## Known open items

**No hard override exists across sessions.** Not a bug, a deliberate bet
with no ablation behind it (see the reversal above) — flagged here
because it's the one open item that's a genuine risk rather than a
validated cut. Watch the "longest gaps" table in every future simulation
run, and revisit if real logging ever shows a pattern actually falling
through the cracks for longer than feels right. `brace`'s old
7-day-clock-vs-0.25-weight tension is resolved twice over now — first by
the clock's removal, then by the weight itself going to `1.0` in the
review pass — not a separate open item any more. (This is distinct from
the same-session repeat rule, which *is* now a hard override, just a
within-session-variety one rather than a cross-session-urgency one — see
"Review pass" above.)

**`math.tex` needs a full rewrite**, not a patch — see Simulation above.
Now further out of date than before: it predates both the dictionary
prune and this review pass.

**The kipping family is deferred again**, having briefly not been:
`kipping_pull_up` was added back in the review pass and removed again in
the same session — see "Review pass" above. `chest_to_bar_pull_up`,
`bar_muscle_up`, `ring_muscle_up` remain absent; `strict_pull_up` is the
only pull-up variant in play. Revisit as a group if/when any pull-up
variant gets added back.

**Nothing in the model has been checked against a real log.** This is
still the actual bottleneck — see "Next step."

## Fixed

Carried forward from before this reversal; the underlying lessons (event
collapsing, determinism) shaped the rewrite even though some of the
mechanisms these bugs were found in (`build_log_stats`'s old windowed
exposure count, `admissible_cells()`) have since been replaced or
extended:

- **Exposure double-counting**: summing per logged *set* row instead of
  per distinct `(date, style, exercise)` event meant a 5×5 counted as 5
  exposures instead of 1. Fixed by collapsing to one event per day before
  applying pattern weights — the same collapsing step the EWMA recursion
  now builds on directly.
- **`recommend()` could declare a style "needed" while every style was
  over-covered** — ranking by relative deficit without checking sign let
  the *least*-over-covered style win a race that shouldn't have had a
  winner. The EWMA rewrite's `recommend()` carries an analogous guard
  forward: if the top style's score isn't actually positive (or both top
  styles are tied at `+inf`, the cold-start case), it returns "no strong
  signal" rather than a false favorite.
- **Dictionary coverage gap**: a cell with exactly one eligible exercise
  gets picked every time that cell is due, killing movement variety.
  `test_every_admissible_cell_has_an_eligible_exercise` now guards this
  permanently, independent of how large or small the dictionary is.
- **The simulation wasn't reproducible** despite a fixed random seed:
  `admissible_cells()` returned an unordered `set`, so tied scores broke
  differently across process runs depending on Python's string-hash
  randomization. Fixed by sorting; still sorted, still verified
  byte-identical across independent runs after every change since,
  including this one.

## Next step

Start logging real sessions — now actually possible via the webapp above,
not just a script. Nothing downstream (whether the missing overdue clock
is ever actually missed, `k`'s value, whether the new rates feel right,
whether 19 exercises feels thin day to day, whether the volume/
conditioning-focus knobs behave the way they read) can be decided
credibly before there's real data. The webapp's own gaps (CSRF, rate
limiting, email verification — see "Webapp" above) are the next thing to
close only once "public web app" becomes an actual near-term plan, not
before.

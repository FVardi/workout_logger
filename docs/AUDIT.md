# AUDIT.md — parameters in advisor.py/exercises.json that need your review

Everything below is either explicitly called out in GUIDE.md as a free
choice or a "tune against real logs" placeholder, or is a value GUIDE.md
never specified and a defensible default had to be picked to make the
code run. None of it has been exercised against real training data. Until
you've logged for a while (per GUIDE.md's own build order), treat every
number here as provisional.

Constants live at the top of `src/training/advisor.py` unless noted
otherwise.

**This file was substantially rewritten alongside the log-EWMA reversal**
(see `docs/STATUS.md`). Most of what used to be here — `γ`, the
resource-overlap coefficient, the `[0,1]` normalization derivation, the
`hang`/`cns`/`aero` resource design notes, the `W` interaction matrix —
described constants and matrices that no longer exist in the model.
Preserved in git history, not reproduced here.

## Requirements-class (GUIDE.md says "trust these" — several were left open)

- **`TARGET_RATE = {"light": 1.0, "heavy": 0.75, "hhr": 0.25}`** — revised
  in review from `{1.35, 0.50, 0.20}`. `hhr` was previously a range
  (0.15–0.25, "free choice") left at the midpoint; now pinned to `0.25`,
  the top of that range, deliberately. `heavy` raised from once-per-
  fortnight to roughly once every 9 days. `light` lowered from ~1.3x/week
  to once/week. Chosen directly, not derived from a formula — still a
  free choice, just a different one. Revisit the same way if it stops
  feeling right once real logs exist.
- **`HHR_CREDIT = 0.5`** — GUIDE.md gives both the reasoning (loaded
  conditioning trains work capacity, not maximal strength, so it
  partially but never fully substitutes for heavy) and the value
  explicitly, so this one is probably right as specified. GUIDE.md itself
  says "if the priority ever shifts toward maximal strength, lower this
  credit" — that's a call only you can make. Confirmed unchanged in this
  review pass.
- **`PATTERN_WEIGHT` — resolved to `1.0` for every pattern, `brace`
  included.** Brace's old `0.25` discount is gone. Under questioning it
  turned out not to be derived from anything about brace itself — GUIDE.md
  and this file both previously implied a physiological rationale
  ("bracing carries incidentally in other lifts"), but that argument
  actually justified a different, since-removed mechanism (the old
  clock's `flexion`/`brace` exemption), not this weight's specific value.
  The `0.25` itself was reverse-engineered purely to fit a capacity
  budget. Dropped once that distinction was made explicit; brace now
  targets identically to every other pattern. This also resolves the old
  "thinner slack than everything else" capacity note below — total demand
  actually *dropped*, from 12.31/week to 10.0/week against the same ~12
  supply, because the same review pass also removed six admissible cells
  (`pull × light`, `brace × light`, `flexion × heavy`, `flexion × hhr`,
  `engine × heavy`, `engine × hhr` — each pattern now restricted to the
  styles it actually has an exercise for) — see
  `docs/GUIDE.md`'s "Capacity arithmetic".
- **`MEMORY_K = 2.0`** — new. "Memory," in units of target intervals; see
  GUIDE.md's Scoring section for the derivation (`τ = k/ρ` per cell). Free
  choice, on the same footing as `TARGET_RATE["hhr"]` above — carried over
  from the design this replaced, which suggested `k=2` as a starting
  point without deriving it. **One concrete consequence worth knowing
  before treating it as harmless:** the score's "0 = exactly on target"
  claim is only exact in the continuous-time limit. Real exposures are
  discrete events; just after each exposure at exactly the target rate
  the EWMA is `1/(1 - e^(-1/k))`, about 27% above `k` at `k=2`.
  **Corrected 2026-10-29:** this was described as on-target training
  reading slightly *ahead*. That's only the post-exposure peak. The score
  sawtooths from about −0.24 just after a session to +0.26 just before
  the next is due and averages +0.01, so there is no bias on average; the
  swing has width `1/k`. Derivation in `docs/math.tex`, "Training exactly
  on target"; both values pinned by tests. Raising `k` narrows the swing
  at the price of slower-reacting scores. Kept at `2.0`.

## Tuned-class (near-noise, by two independent ablations) — now empty

Everything that used to live in this class — `γ`, the resource overlap
coefficient, the movement-staleness weight, the unilateral weight, the
staleness caps, and now `SAME_PATTERN_REPEAT_COEF` — is gone along with
the mechanisms it modulated, not just deprioritized.

`SAME_PATTERN_REPEAT_COEF = 1.1` was the last survivor of this class, and
its removal doesn't fit the same justification as the others. A check run
against the live simulation (not an ablation in the formal sense, but the
same spirit) found it was **not** near-noise: it changed which candidate
won in 52 of 104 workout picks (50%), because the natural score gap
between the top two candidates was almost always under 1.1 (mean 0.12,
median 0.07, max observed 0.65 across 94 finite comparisons — every
single one smaller than the penalty). So `1.1` was already dominating the
ranking whenever it applied; the concern this file previously raised
("might now be too small to matter") was backwards. It's removed anyway,
not because it was ineffective but because a categorical hard rule
(`candidate_cells()` excludes an already-used pattern outright, at every
style, not just `hhr`) does the same job without the corner case the soft
penalty had: a severely neglected same-pattern cell's score could in
principle still grow past `1.1` and repeat anyway, and now categorically
cannot. See `docs/GUIDE.md`'s "Rules".

- **`REP_SCHEMES` list** — gone, with `suggest_rep_scheme()` and
  `suggest_rep_count()` (2026-10-29). Nothing in the app used them.

## What's gone, and why it's not merely renamed

If you're looking for a constant from an earlier version of this file and
it isn't above, it's very likely one of these, all removed as part of the
same reversal (see `docs/STATUS.md` for the full narrative):

- **`WINDOW_DAYS`** — replaced by per-cell `τ = MEMORY_K / ρ`. There is no
  longer one shared window; every cell's effective memory is now
  proportional to its own target rate.
- **`GAMMA_BEHIND`/`GAMMA_OVER`, `deficits()`, `normalized_deficit()`,
  `cell_target()`, `marginal_deficit_by_pattern()`** — the entire
  raw-deficit-plus-cross-style-coupling machinery. One `ewma_score()`
  call now does this job.
- **`LAMBDA_CELL`, `CELL_STALENESS_CAP`** — staleness is now implicit in
  the EWMA's own decay, not a separate capped day-count term.
- **`UNILATERAL_TARGET`, `LAMBDA_UNI`, `UNILATERAL_SHORTFALL_CAP`,
  `unilateral_exposures`** — unilateral-shortfall scoring is retired
  (validated near-noise). `laterality` is still authored and `side` is
  still logged; nothing about reintroducing this later needs new data.
- **`RESOURCE_OVERLAP_COEF`, `resource_interference()`, `_l2_norm()`,
  the `R` field on `Exercise`, `resources`/`resource_interaction` on
  `Dictionary`** — the entire resource-cost matrix and its interference
  scoring. Retired, not shrunk: nothing else in the code consumed `R`
  once the overlap term was cut, so there was no reason to keep
  authoring or carrying it. If this is ever revived, it needs re-authoring
  from scratch against the current 19-exercise dictionary — the old
  values (including the `hang` resource and its per-exercise table) lived
  entirely on exercises no longer in the dictionary or on a resource
  vocabulary (`knee`/`post`/`grip`/`shldr`/`lats`/`trunk`/`hang`) that has
  no current consumer either.
- **`CLOCK_EXEMPT_PATTERNS`, the 14-day loaded clock, the 24-day
  classic-heavy clock** — first collapsed into a single 7-day
  any-exposure clock, then that clock itself was removed too (see next
  item). There's no replacement exemption list because there's nothing
  left to be exempt from; the EWMA score itself now carries the "trunk
  work is loaded incidentally by everything else" reasoning that the
  clock exemptions used to encode structurally.
- **`OVERDUE_DAYS`, `OVERDUE_BONUS`, `overdue_bonus()`,
  `LogStats.pattern_last_seen`** — the last surviving clock, removed
  entirely on request. **There is now no hard override of any kind
  anywhere in the model.** Every earlier version of this section
  justified its cuts by an ablation showing near-zero effect; this one
  cut is different and worth flagging as such — no ablation showed the
  clock was unnecessary, it was removed for simplicity on a model with no
  real log history yet to make its absence costly. If a pattern ever
  visibly falls through the cracks once real logging starts, this is the
  first thing to reconsider bringing back. `simulate.py`'s report now
  carries a "longest gaps at end of simulation" table specifically so
  this doesn't go unnoticed by default.
- **`LAMBDA_MOVE`, `MOVEMENT_STALENESS_CAP`** — already gone before this
  reversal (see `docs/STATUS.md`'s "Fixed" history); still gone.
- **`SAME_PATTERN_REPEAT_COEF`** — see the "Tuned-class" section above.
  Replaced by a categorical rule in `candidate_cells()`, not by a
  different coefficient.

## Resolved in the review pass, no longer open

- **The `hhr` spec/dictionary mismatch is resolved.** GUIDE.md's
  definition (`hhr` = a loaded, ~60–70% 1RM stimulus) didn't change; the
  dictionary now matches it. `hhr` admissibility is restricted to
  exercises that are also `heavy`-admissible — the rule is "a heavy
  movement, done at reduced load under a high heart rate," not "anything
  that raises your heart rate." `push_up`, `air_squat`, `toes_to_bar`,
  `burpee`, and `double_unders` lost `hhr` under this rule, since none of
  them has a heavy sibling; `kb_swing` gained `heavy` specifically so it
  could keep `hhr` legitimately. One structural consequence worth keeping
  in mind, not a defect: `flexion` and `engine` have no heavy-admissible
  exercise at all, so neither pattern can ever have an `hhr` cell under
  this rule — both are light-only now, permanently, unless a genuinely
  loaded flexion or engine movement gets added later.
- **The kipping-family question was reopened and closed again.**
  `kipping_pull_up` was added back to the dictionary mid-review at full
  `pull` credit, then removed again in the same session once the original
  "the kip does part of the work, not the muscle" objection resurfaced
  unresolved. Net effect: unchanged from before the review —
  `strict_pull_up` is still the dictionary's only pull-up variant.
  `chest_to_bar_pull_up`, `bar_muscle_up`, and `ring_muscle_up` remain
  absent. **Superseded 2026-10-29:** `kipping_pull_up` was re-added as a
  light-only exercise, alongside `ring_row`, to create the `pull`/`light`
  cell — see `docs/STATUS.md`'s "Light-pull pass" for why the objection
  above doesn't apply at light.

## What an empty `E` row actually does — worth knowing before editing one

`eligible_exercises()` requires `E[pattern] > 0` and `admissible_cells()`
is derived from `E`, so an exercise with an empty `E`:

- is **never suggested** by `advise()`;
- contributes **zero exposure**, and therefore never grows any cell's
  EWMA, when logged (`build_log_stats` iterates `ex.E.items()`).

That's the intended status for running, rowing, and biking generally —
except that `running`, `rowing_erg`, and `burpee` in the current
dictionary have `E = {"engine": 1.0}`, specifically so they're *not* in
this bucket: `engine` exists as a pattern precisely so pure-conditioning
work stays suggestible. If you add an exercise and want it to genuinely
never be offered (the way `box_jump` was in the pre-prune dictionary),
give it an empty `E` deliberately, not by omission.

## Dictionary review status

31 exercises now. This section describes the review pass, which took the
dictionary from 16 to 19; the growth pass (19 -> 29) and the light-pull
pass (29 -> 31) are in `docs/STATUS.md`. 19 exercises after that pass, not 16 — revised in the same review pass as the
constants above. Changes from the pruned-to-16 set: `power_clean` was
dropped entirely (its only credit, `hinge`, is still covered by `deadlift`
and `kb_swing`); `bench_press`, `front_squat`, `double_unders`, and
`biking` were added, each mirroring an existing sibling's admissible
styles (`bench_press`/`front_squat` mirror `strict_press`/`back_squat`;
`double_unders` mirrors `burpee`; `biking` mirrors `running`/`rowing_erg`).
`kb_swing`'s `E` weight was raised from `hinge: 0.5` to `hinge: 1.0`, and
it's now admissible at all three styles (previously `light`/`hhr` only).
`deadlift`, `strict_pull_up`, `farmers_carry`, and `db_lunge` all lost
`light` admissibility — see `docs/STATUS.md` for the reasoning
behind each. `kipping_pull_up` passed through the dictionary and back out
in the same session; see "Resolved in the review pass" above.

`E` weights for the exercises that predate this pass carry forward
whatever review they'd already received before the prune (barbell entries
were audited category-by-category; `thruster` and `bent_over_row` were
checked explicitly; `power_clean`'s review is now moot). None of it has
been checked against a real log, which is the same caveat every number in
this file carries. The exercises cut across both the original prune and
this pass, and every judgment call made about them, are recoverable from
git history if a gap opens that the current 19 don't cover.

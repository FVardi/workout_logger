# training — guide

Spec for the training-log advisor (`src/training/`). This project started
inside the `math_blog` repo as its "Part B" and moved here on 2026-10-29;
the Part labels in these docs are left over from that. Current status and open
review items: `docs/STATUS.md` and `docs/AUDIT.md` — not
here, this file stays the stable spec.

`src/training/advisor.py` is this section's implementation. It has been
prototyped and thrown away once already — the spec below is the result,
and is more trustworthy than any earlier implementation. A draft
implementation now exists (`advisor.py`, `db.py`, `schema.sql`,
`exercises.json`).

**Reversal: the scoring model was rebuilt around a single continuous-decay
EWMA per cell, the dictionary was pruned to 16 exercises, and the overdue
clock was removed entirely (v10).** A subsequent review pass (still v10)
revised the dictionary again (now 19 exercises), corrected `hhr`
admissibility to match this file's own definition, dropped `brace`'s
weight discount, raised the target rates, and replaced the soft
same-pattern-repeat penalty with a hard categorical rule — see
`docs/STATUS.md` for the detail; this file describes the current
state, not the history. Before real logging started, the
deficit/window/staleness/normalization machinery below was replaced
wholesale — not patched — because most of it was already known to be
near-noise (see "The parameters"), and testing a model this elaborate
before any real data existed was the wrong order of operations. The
resource-cost matrix `R`, its interference scoring, unilateral tracking,
and — in a second pass, once the first three had landed — the last
surviving overdue clock are all gone entirely, not just unused. See
"Scoring" below for the replacement, and `docs/STATUS.md` for
why. The exercise count drop is unrelated to the scoring changes and just
as deliberate: a handful of movements per pattern, in play now, with the
other ~45 in git history to reintroduce once logging surfaces an actual
gap rather than a hypothetical one.

**There is currently no hard guarantee mechanism left in the model at
all.** Every earlier version had one — three clocks, then one clock —
existing specifically because a ranking score can always be outbid and so
can't guarantee anything on its own. Removing the last clock means the
model is now pure ranking: a neglected cell's score still grows without
bound the longer it's ignored (see Scoring), so it will still eventually
win, but nothing pins that to a specific day any more. Accepted knowingly,
in favor of simplicity, for a model with no real log history yet to make
that risk concrete. See "Deliberately absent."

## What it is

**A ranking function you query, not a program generator.** It answers
"what is most needed today". The human decides how many pieces to do and
designs the actual session. Everything that tried to assemble sessions —
templates, time budgets, piece counts — was built and then deleted,
because deciding how much to do is the human's job.

## Axes

**Patterns (7).** `squat`, `hinge`, `push`, `pull`, `flexion`, `brace`,
`engine`.

**Each pattern has an archetypal movement**, and the archetype is what the
`E`-vs-`R` rule below is defined against — without one, `E[pattern]` can't
be adjudicated at all:

| pattern | archetype |
|---|---|
| `squat` | back squat |
| `hinge` | deadlift |
| `push` | strict press |
| `pull` | strict pull-up |
| `flexion` | toes-to-bar, V-up |
| `brace` | — resisting movement under load; see below |
| `engine` | sustained monostructural output |

**Reversal: `midline` split into `flexion` and `brace`.** `midline` had no
archetype, which is why its `E` weights were the least defensible in the
dictionary — a kettlebell swing, a weighted plank, a GHD sit-up and a
farmer's carry all carried `midline` credit despite training visibly
different things. Naming toes-to-bar and the V-up as the archetype settled
`flexion` immediately (loaded trunk/hip flexion through range: GHD sit-up
in, plank and carry out) but left carries and planks with no pattern at
all — and an exercise with an empty `E` row is *never suggested*, because
`eligible_exercises()` requires `E[pattern] > 0`. That would have demoted
loaded carries to running's status: invisible to the advisor unless
manually declared. `brace` exists so anti-movement work under load —
carries, planks, the Turkish get-up — stays askable.

The split is closer to Dan John than the old single axis was, though still
not identical: his list is push / pull / hinge / squat / **loaded carry**,
often with a single-leg slot. This model keeps carries inside `brace`
alongside planks, and treats single-leg work as the squat pattern with
`laterality: unilateral` rather than as its own pattern, since the
laterality axis already carries its own target and scoring term.

**Splitting a pattern is not free, and the prerequisite came first.**
Targets used to be keyed by style alone and applied uniformly across
patterns, so any sixth pattern automatically demanded a full ~2 exposures
per week — taking demand from ~10 to ~12 against a supply of ~12, i.e. the
entire capacity slack, silently. `PATTERN_WEIGHT` was added first so a
pattern can be asked for at a rate that matches how much of it you
actually want. See "Capacity arithmetic".

**Reversal: renamed from the internal codes `BP`/`PFG`/`PR`/`UBP`/`ML`.**
Those were Pat Sherwood's CrossFit Linchpin gross movement patterns
(`BP` = below parallel, `PFG` = pull from ground, `PR` = press, `UBP` =
upper-body pull), abbreviated. Renamed to plain names that also match the
"fundamental human movements" framing used elsewhere in general S&C (Dan
John and others: squat / hinge / push / pull, plus slots that the former
`midline` covered under its Sherwood name — see the split above) — same
patterns, no change in meaning, just naming
two traditions agree on instead of an internal-only abbreviation. Sherwood's
own set also includes "engine," which was dropped and has now been
**restored, in a deliberately narrow form** — see below.

**Reversal: `engine` is back, holding only exercises that build nothing
else.** It was dropped because it classifies energy system rather than
movement, and duplicated the modality axis. Both objections were sound and
the second is sharper than it looks: the modality axis is the *styles*
axis together with the block's time-domain fields (`structure`,
`rest_seconds`, `time_cap_seconds`). Engine really is a property of the
**dose**, not of the exercise — thrusters are strength work at 5×3 and
engine work at 21-15-9, so no number written next to "thruster" is true of
thrusters generally.

That objection is fatal to a general engine pattern, and survivable by a
narrow one. `E[engine]` is restricted to exercises whose `E` row would
otherwise be **empty** — running, biking, rowing, double-unders, burpees —
i.e. movements that build no transferable movement pattern at all. Two
things follow. First, `E`'s row-stochasticity is preserved: since no
member carries any other pattern weight, nothing ever has to *split*
credit between engine and a movement pattern, and a thruster keeps its
full `{squat 0.5, push 0.5}` rather than being diluted to make room. That
splitting problem is what rules engine out as a general pattern, since
GUIDE.md's weights split volume and never duplicate it. Second, the
aerobic-vs-anaerobic heterogeneity that leaves engine without a single
archetype comes almost entirely from the mixed-modal work now excluded;
what remains is homogeneous enough that "does more rowing improve your
running" has an honest partial-yes answer.

The narrow form buys the model one thing it could not previously say:
*you have not conditioned in ten days*. It does **not** claim that engine
is only trainable through these five movements — obviously it isn't. It
claims only that these five train nothing else, so crediting them costs
nothing elsewhere.

**Styles (3).** `light` (in a workout), `heavy` (classic strength, fresh,
long rests), `hhr` (heavy at high heart rate — load carried while
breathing hard, roughly 60–70% 1RM for reps in a workout).

Note `hhr` is a **stimulus, not a session type**. It can occur in a short
hard piece, a long mixed session, or interspersed through easy riding.

## The authored matrix

**E — exercise → pattern weights.** Rows sum to ≤ 1; the remainder is
residual. Deliberately fuzzy: a squat clean is 0.5 hinge + 0.5 squat; a
thruster 0.5 squat + 0.5 push; a clean & jerk 0.5 hinge + 0.3 push + 0.2
squat. Row-stochasticity gives the invariant that every axis view
reconciles to the same grand total — that's the unit test. Weights
**split** volume, never duplicate it.

**The governing rule for what goes in `E`.** `E[pattern]` means genuine
adaptation-specific transfer: would doing more of this exercise measurably
improve competency at the archetypal movement for that pattern (back
squat for `squat`, deadlift for `hinge`, strict press for `push`, strict
pull-up for `pull`)? It does *not* mean "the joints pass through a similar
position." A split jerk's catch is a split-stance lunge, not a bilateral
squat — mechanically a different pattern, not just a deeper version of the
same one — so it earns no `squat` credit in `E` even though the front leg
bends. When an exercise fatigues a joint without training its strength
there, resist the urge to invent a compromise weight in `E` to represent
that cost — that cost has no live home in the model right now (see the
`R` reversal below), but a fuzzy `E` weight is the wrong place to hide it
regardless.

**Reversal: `R`, the resource-cost matrix, is retired.** It existed to
let the model know a bike ride blocks a heavy squat despite sharing no
movement pattern — cosine-similarity interference between two exercises'
resource-cost rows, weighted by a `W` matrix, subtracted from a
candidate's score. Two independent ablations (this project's own, and a
separate session's working the same problem from scratch) agreed it
contributed near-zero to ranking quality. Once that's true, `R` has no
consumer at all: nothing else in the code ever read it. Keeping ~130
authored numbers on the books for a mechanism nothing uses is exactly the
kind of complexity this pass exists to cut. `cns` and `aero`'s earlier
removal (dropped from `R` before `R` itself went) is preserved in git
history but no longer relevant to the live model.

**Never fit `E` from data.** It is authored judgment, versioned, held
fixed. Relative trends survive a stable-but-wrong map; they do not survive
a map that drifts.

## Exercise attributes

Each entry also carries:

- **admissible styles** — which of light/heavy/hhr it can be used for.
  This generates the mask, so cells nothing can fill are simply never
  scored.
- **laterality** — bilateral or unilateral. Two values only.
- **implement** — barbell, dumbbell, kettlebell, bodyweight, etc.

**Feasibility is encoded by which entries exist, not by rules.** There is
no dumbbell back squat entry, and no single-arm bench press entry, because
those aren't things to do. All three overhead press variants exist
(barbell, dumbbell, single-arm), so all three can be suggested. No
cross-product to enumerate, no impossible combinations.

**hhr credits 0.5 toward the heavy cell.** This is not arbitrary. Loaded
conditioning at 60–70% under fatigue trains work capacity and
strength-endurance, but contributes little to maximal strength, which is
neural and needs proximity to max plus a rested nervous system. So hhr
partially satisfies the loading requirement without ever fully replacing
fresh heavy work. The model encodes the physiology. If the priority ever
shifts toward maximal strength, *lower this credit* rather than removing
the hhr category.

**Unilateral coverage is not currently scored.** `laterality` is still
authored per exercise and still logged (`side` on each set), but nothing
in the score reads it. Reversible without a schema change whenever it's
worth the term back — see "Deliberately absent" below for why it was cut
along with everything else in this pass rather than kept as a smaller
exception.

## Scoring

**Reversal: replaced wholesale by a single continuous-decay EWMA per
cell.** The model this replaces computed, per cell: a windowed exposure
count, a raw deficit against target, normalization to `[0,1]`, a separate
capped staleness measure, and a cross-style coupling term (`γ`) to keep
one style's over-coverage from starving a pattern's other styles. Four
mechanisms doing jobs that turn out to be the same job, once you notice
that "how far behind target" and "how long since touched" are both just
functions of *when the exposures happened* — which is what a decaying
average already tracks.

For each admissible cell `(p, s)`, maintain

```
ewma[p,s] ← ewma[p,s] · exp(−Δt / τ[p,s]) + exposure
```

applied at every logged exposure event (`Δt` since that cell's previous
event), where `exposure` is the pattern weight from `E`. An `hhr` exposure
additionally contributes `HHR_CREDIT · exposure` to the *heavy* cell's own
EWMA, decayed on the heavy cell's own `τ` — the same partial-credit idea
as before, just folded into the same recursion instead of a separate term
in the score.

`τ[p,s] = k / ρ[p,s]`, where `ρ[p,s]` is that cell's target rate in
exposures/day (`target_rate[s] × pattern_weight[p] / 7`) and `k` is one
shared, dimensionless constant — "memory," in units of target intervals.
This is what replaces the old fixed 42-day window: instead of every cell
forgetting at the same calendar rate regardless of how often it's
*supposed* to fire, a rare `hhr` cell's memory is automatically longer
than a frequent `light` cell's, in proportion to their targets. No
per-cell tuning.

Score each cell as

```
score[p,s] = −log(ewma[p,s] / k)
```

One formula recovers everything the four mechanisms above did separately:
`score = 0` exactly when a cell sits at its steady-state on-target value
(`ewma = ρτ = k`, independent of `ρ` — every cell is on the same scale
automatically, which used to need an explicit normalization step);
during neglect, `ewma` decays as `ewma₀·exp(−t/τ)`, so `score` grows
~linearly at rate `1/τ` — staleness, for free, on the same formula;
training forever at exactly half the target rate settles at a steady
`ewma = k/2`, i.e. a constant `score = log(2) ≈ 0.69` — chronic deficit,
also for free. A cell with `ewma = 0` (never trained at that style, ever)
scores `+∞`, not a large finite number: there's no principled reason
"never quantified" should rank below "quantified and very bad."

**Reversal: the same-pattern-repeat penalty is gone from the score
entirely.** `score[p,s] = −log(ewma[p,s] / k)` is now the whole formula —
nothing added or subtracted afterward. A pattern already used earlier in
the session is no longer scored around; it's excluded outright before
scoring ever runs, categorically, at the candidate-list stage — see
"Rules" below. The old `1.1` coefficient (`SAME_PATTERN_REPEAT_COEF`) is
retired along with the mechanism it modulated, the same way `γ`, the
resource-overlap term, the unilateral-shortfall term, and the
overdue-bonus term were before it — see "The parameters," "Deliberately
absent," and "No hard override."

This wasn't an ablation-justified cut like those others, though — a check
against the running synthetic simulation found the opposite of "near
noise": the `1.1` penalty was changing which candidate won roughly half
the time it applied, because the natural score gap between the top two
candidates is typically only a few hundredths to a few tenths, an order
of magnitude smaller than `1.1`. So the soft penalty was already behaving
almost like a hard block in practice — this change makes that intent
explicit and unconditional (a severely neglected same-pattern cell could
previously still outbid the penalty and repeat anyway; now it categorically
cannot), rather than changing what mostly happened already.

**On-target training sawtooths around 0.** `score = 0` at target is exact
only in the continuous-time limit. Real exposures are discrete events; for
a perfectly periodic log at exactly the target rate, the EWMA just after
each exposure is `1/(1 − e^(−1/k))`, about 27% above `k` at `k = 2`. That
is the peak, not a bias: the score rises linearly from about −0.24 just
after a session to +0.26 just before the next is due, and averages +0.01.
The swing has width `1/k`. See `docs/math.tex`, "Training exactly on
target", for the derivation.

## No hard override

**Reversal: the last surviving clock (7-day, any exposure, any style) is
removed.** Every earlier version of this model had a hard guarantee layer
of some kind — three clocks, then one — on the reasoning that scores rank
but don't enforce, and a score can always be outbid by a worse-scoring
cell elsewhere, so nothing about ranking alone *forces* a schedule. That
reasoning hasn't changed. The clock is removed anyway, deliberately, in
favor of simplicity: there is no real log history yet against which an
unbounded-neglect failure mode could actually be observed, and the EWMA
score already does most of what the clock existed to force on its own —
a chronically neglected cell's score grows without bound the longer it's
ignored (see Scoring), so it will still eventually win a ranking, just
without a guarantee pinned to a specific day.

**What this costs, stated plainly rather than left implicit:** two cells
that are both somewhat neglected are now ranked purely by relative score.
Nothing forces either one to the front by a calendar deadline the way the
7-day clock used to. In principle a pattern could go a long time without
being ranked first if something else is consistently a little more
neglected — the clock's whole job was to make that impossible regardless
of relative ranking. `simulate.py`'s report now includes a "longest gaps"
table specifically to make this visible rather than silent; see
`docs/STATUS.md` for what the first simulation run under the new
model actually showed.

**One tension this removal resolves as a side effect, not by design:**
`brace`'s 7-day clock used to impose a floor of roughly one exposure/week
regardless of its `0.25` `PATTERN_WEIGHT`, which asked for about half
that — the clock always won. With no clock left to win, `brace` is free
to actually train at its stated (lower) rate for the first time. Worth
watching once real logs exist, since it was previously untestable —
the clock was always masking whatever the weight alone would have
produced.

## Queries

```python
advise(day, "heavy"   , chosen=[...])   # ranked movements
advise(day, "workout" , chosen=[...])   # ranked movements
recommend(day)                          # which mode is most needed
```

`chosen` accumulates; **every pick re-scores everything.** Pick any number
of strength pieces, then the workout list; or the reverse — the
conditioning is symmetric.

**Greedy by design.** No lookahead, because the number of pieces is the
human's call and lookahead would require assuming it. The cost: a clean &
jerk spans three patterns and forecloses more than a single-pattern lift
would, and greedy won't see that. Acceptable, because the reasoning is
displayed and the human can see the overlap flag.

`recommend()` compares styles by the **median** `ewma_score` across each
style's admissible cells, not the mean: individual cell scores are now
unbounded (a never-touched cell scores `+∞`), so a mean would let one such
cell dominate an otherwise well-covered style's verdict. Because every
cell's score is already normalized (0 = on target, regardless of the
cell's own rate), no separate relative-to-target correction is needed the
way the old raw-deficit model required — that correction was itself a
symptom of scores not yet being on a comparable scale.

Report the verdict in three bands, thresholds anchored to `log(2)` — the
score a cell settles at under chronic training at exactly half its target
rate, so it's a physically meaningful gap rather than an arbitrary round
number: clear (margin `> log(2)`), leaning (`> log(2)/2`), no strong
signal otherwise. Most days genuinely have no right answer and the output
should say so.

## Rules

- **Hard, categorical:** a pattern used earlier in the session — at any
  style, `heavy` or workout — cannot be suggested again later in that same
  session, at any style. No heavy squat followed by squatting at high
  heart rate, and no heavy squat followed by front squat either, and no
  toes-to-bar workout piece followed by a second flexion piece. This is a
  reversal: the rule used to be `hhr`-only (a pattern loaded today
  couldn't reappear at `hhr`, but could still repeat at `light` or
  `heavy`), backed by a soft `-1.1` score penalty everywhere else. Widened
  to every style on request, once a check against the running simulation
  showed the soft penalty was already deciding the winner in about half of
  all workout picks — see Scoring's note on why it was dropped rather than
  re-tuned. Advisory only: this constrains what `advise()` will *suggest*,
  not what you're free to actually train.
- **No rep schemes or rep counts.** The advisor suggests *what* to train,
  never how many reps or which scheme. Two helpers that did
  (`suggest_rep_scheme()`, `suggest_rep_count()`) were removed on
  2026-10-29: no page used them, and the logging rework stops recording
  the rep-scheme field they learned from.

## Output

Movements, not cells, with reasoning. Two parts to the reasoning, which
can disagree — and when they do, that's the interesting case:

1. **cell staleness** — days since this pattern at this intensity was
   literally last trained (unaffected by hhr's credit into heavy — see
   Scoring)
2. **the EWMA score** — the single log-scale number that already carries
   what used to be reported as separate deficit and staleness figures

Known limitation: each cell contributes only its *stalest* variant, so the
second-best option is never shown. Probably worth changing — showing two
or three per cell would suit how much judgment the human is keeping. See
`docs/ROADMAP.md`.

## The parameters

Three classes, and they should be treated completely differently.

**Requirements — derived from stated goals. Trust these.**

| | value | why |
|---|---|---|
| target: light | 1.0 /week/pattern | revised in review; was 1.35 |
| target: heavy | 0.75 | revised in review; was 0.50 (heavy once per fortnight) |
| target: hhr | 0.25 | revised in review; was 0.15–0.25 free choice |
| total | 2.00 | goal: each pattern twice weekly, unchanged by the revision |
| hhr credit | 0.5 | physiological reasoning above |
| memory (`k`) | 2.0 target intervals | free choice, same footing as target:hhr; see the discrete-steady-state caveat in Scoring |

`PATTERN_WEIGHT` is `1.0` for every pattern, including `brace` — its old
`0.25` discount is gone. It was never derived from how much bracing
carries incidentally in other lifts (that argument justified the old
clock *exemption*, a different, now-removed mechanism); it was reverse-
engineered to fit a capacity budget. Dropped once the reasoning behind it
didn't hold up under questioning — see `docs/AUDIT.md`.

**Judgment — authored.** `E`, admissible styles, laterality, implement,
for 31 exercises (pruned to 16, revised to 19 in review, grown to 29 on
contact with real logging, then 31 with two light pulls — see
`docs/STATUS.md`). Set once,
version, hold fixed. Not fittable at n=1. `R` is retired, not shrunk —
see "The authored matrix."

**Tuned against a synthetic history — now empty.** The same-pattern-repeat
coefficient (`1.1`) was the last member of this class; it's gone too, not
because an ablation found it near-noise like `γ`/resource-overlap/
unilateral weight before it, but because the mechanism it modulated (a
soft penalty) was replaced outright by a categorical hard rule — see
"Rules." There is currently nothing left in the tuned-against-a-simulation
class. See `docs/AUDIT.md`.

## Capacity arithmetic

At 3 sessions/week, the twice-weekly goal needs **5 pattern-bearing pieces
per session** (one loaded + four workout movements). Four pieces leaves
7–9 day gaps; six adds nothing. Five is the exact threshold.

Demand has moved twice since the `flexion`/`brace`/`engine` split first
pushed it to 12.31/week against a supply of ~12 (slack gone, slightly
negative — see git history for that period's numbers). **After the
subsequent review pass (revised rates, dropped `brace` discount, six
cells removed from the admissible set), demand is 10.0/week against that
same ~12 supply — about 17% slack, restored.** Adding the `pull`/`light`
cell deliberately (ring rows and kipping pull-ups, 2026-10-29) raised it
to **11.0/week, about 8% slack** — see `docs/STATUS.md`'s "Light-pull
pass".

Two changes moved it in opposite directions and the second dominated.
Raising `TARGET_RATE` and dropping `PATTERN_WEIGHT["brace"]` to `1.0` both
push demand *up* per admissible cell. But the same review pass also
removed six cells entirely (`pull × light`, `brace × light`,
`flexion × heavy`, `flexion × hhr`, `engine × heavy`, `engine × hhr` —
each pattern restricted to the styles it actually has an exercise for; see
"Axes" and `docs/STATUS.md`), which shrank the *count* of cells
paying into the total. Fifteen admissible cells after that pass, not
nineteen (sixteen now, with `pull × light` back on purpose). The count
effect outweighed the per-cell rate increase.

The synthetic simulation reflects the restored slack directly: the
worst end-of-run gap dropped from 26 days (`brace/heavy`, under the old
12.31/week regime) to 12 days, and is 9 days in the current 16-cell run;
every cell's final score sits within about ±0.9 of on-target — see `docs/STATUS.md`'s Simulation
section for the current numbers.

Three ways out, none taken: lower `PATTERN_WEIGHT["engine"]` from 1.0;
raise the supply assumption, on the argument that a run doesn't actually
compete for the same session slot as a lift; or accept that the model is
now deliberately over-subscribed and read its rankings as strictly
relative. The third is defensible — GUIDE.md already says targets rank
rather than being reachable goals — but it should be a choice, not a
number nobody revisited.

## Deliberately absent

**Progressive overload.** The model measures demand; it does not drive
load upward. Performance is tracked separately:

- pooled `log e1RM = α_n + s(t) + ε` on sets of ≤6 reps, which recovers a
  single strength trend from a *rotating* menu of variants — the thing
  high-variance programming otherwise makes invisible. Exclude `hhr`
  (load reflects pacing, not capacity) and light in-workout work.
- a plain rep-max PR table: max load per (lift, rep count) with a date.
  Catches the high-rep work the estimator discards. It's a ratchet, so it
  can't show decline.
- benchmark repeats. **Deficit-driven selection avoids repeats by
  construction** — a repeated workout has zero staleness. So benchmarks
  need their own coverage category with a target frequency (~one every
  2–3 weeks), or there will be a beautifully varied year with no way to
  tell whether anything improved.

Also absent, deliberately: within-session ordering (V aggregates over the
session, so 21-15-9 and 9-15-21 look identical), density (a ratio, so not
additive), movement quality, and multi-day tissue readiness (an
exponential-decay depletion term existed once, ablated to ~0.05, dropped).

**Resource interference and unilateral-shortfall scoring**, cut in the
EWMA reversal above, belong in this list too now rather than reading as
losses: both were validated near-noise before being cut, the same
evidentiary bar as everything else here. `laterality` is still authored
and still logged; nothing about reintroducing a unilateral term later
requires new data, only a new score component.

**Any hard guarantee mechanism at all.** Unlike the items above, this one
wasn't cut on an evidentiary bar — no ablation showed the overdue clock
was near-noise, and its whole point was to do something the score
structurally can't (force a specific cell to the front by a calendar
date, not just eventually). Cut anyway, on a "start bare minimum" basis
rather than a "proven unnecessary" one — see "No hard override." Flagged
here as the one item in this list that's a genuine open bet rather than a
validated simplification, and the most likely candidate to come back
first if real logging shows something falling through the cracks.

## Conventions

- **Unilateral reps are logged per side**; the model doubles them. Decide
  this before logging anything — inconsistency here quietly corrupts the
  rolling medians.
- Log what was **done**, never what was prescribed. The volume vector
  under-determines the workout.
- Session ordering is a human convention, not model output: heavy first as
  controlled sets, then the workout. Exception: `hhr` is integrated by
  definition.

## Data model

`user → session → block → set`. The block carries style, structure, rest,
time cap — necessary because a metcon is one logical unit containing
several exercises. Sets carry exercise, reps, load, scheme, and (added
alongside the webapp's engine template) distance and duration, since
running, rowing and biking have no meaningful reps or load. Every
measurement column is nullable because movements are measured
differently, not because the data is optional.

**A block holds exactly one style**, which is what makes a mixed-style
metcon — a heavy squat triple into a run — two blocks sharing a structure
label rather than one. That's a storage consequence of style being a
per-movement property; see "Axes" on why it can't be inferred from the
piece it sits in.

`user` is the multi-tenant root added for the webapp: sessions belong to a
user, while the exercise dictionary stays global. See
`docs/STATUS.md`.

Store the exercise dictionary as **versioned JSON** and stamp each log row
with the dictionary version. Retuning a pattern weight later would
otherwise silently rewrite history; with a version stamp you can choose
between "recompute under current beliefs" and "preserve what I believed
then".

**The data outlives the code.** Python, spreadsheets and any eventual app
are disposable layers over one durable asset: the schema, the dictionary,
and the logged sets.

## Build order

1. Exercise dictionary as versioned JSON. **Start minimal — a handful of
   exercises per pattern, not an exhaustive catalogue.** ~50 movements was
   the original guidance here; it was reversed once testing began, because
   a large dictionary means a large surface of untested judgment calls
   (`E` weights) sitting underneath a model that hasn't yet seen real data
   either. Every exercise still forces a taxonomy decision into the open —
   that part of the reasoning holds — but there's no benefit to making
   forty of those decisions before the first one has been checked against
   a real log. Add exercises when logging surfaces an actual gap (a
   pattern/style cell with only one or two eligible movements getting
   repetitive), not speculatively.
2. SQLite log with the schema above. Most portable option — native on iOS
   and Android, readable from pandas.
3. `advisor.py` against that schema.
4. Log for months before trusting any output. Expect to discover that
   several axes are never queried.

Keep a comparison harness. Every parameter tuned during development was
answering a question that a later structural change invalidated — they are
consequences of the session structure, not settings to get right once.

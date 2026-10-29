"""Training advisor: a ranking function you query, not a program generator.

Answers "what is most needed today". The human decides how many pieces to
do and designs the actual session. Full spec: docs/GUIDE.md.

Reversal (dictionary v10): the deficit/window/staleness/normalization
machinery that used to compute each cell's score has been replaced by a
single continuous-decay EWMA per cell, scored as -log(ewma / k). One
mechanism now does the job that used to take four (raw exposure count,
window truncation, [0,1] normalization, a separate staleness term) - see
docs/GUIDE.md's Scoring section and docs/STATUS.md for
why, and docs/AUDIT.md for the one correctness caveat (the
discrete-event steady state isn't exactly k at small values of MEMORY_K).
The resource-cost matrix (R), its interference scoring, and unilateral
tracking are gone from this version entirely - both this project's own
ablation and an independent one agreed they were near-noise, and nothing
else in the code consumed R once the overlap term was cut.

Reversal (also v10, second pass): the overdue clock is gone too - there
is no hard override left anywhere in this module. The three original
clocks (7d any / 14d loaded / 24d classic-heavy) had already collapsed to
one (7d any); that one is now removed rather than kept as a smaller
exception, on the same "start bare minimum" reasoning as the dictionary
prune. This is a real behavior change, not a no-op: previously a cell
past 7 days untouched got a fixed +1000 added to its score regardless of
how the EWMA read it, guaranteeing it would rank first *by that date*.
Without it, a neglected cell's score still grows without bound the longer
it's ignored (see ewma_score()'s docstring) and will eventually outrank
everything on its own - but "eventually" is no longer pinned to a
specific day. Two cells that are both somewhat neglected are now ranked
purely by relative score, with nothing forcing either to the front on a
calendar guarantee. Acceptable for a model in early testing with no
history yet to make unbounded neglect a real risk; revisit if a pattern
ever visibly falls through the cracks for longer than feels right. See
docs/GUIDE.md and docs/STATUS.md.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from statistics import median

from . import db

DICT_PATH = Path(__file__).parent / "exercises.json"

# --- Requirements: derived from stated goals. Trust these. (docs/GUIDE.md) ---
TARGET_RATE = {"light": 1.0, "heavy": 0.75, "hhr": 0.25}  # /week/pattern

# Per-pattern scaling on TARGET_RATE. All patterns at 1.0 - brace's old 0.25
# discount is gone (see docs/AUDIT.md): it was reverse-engineered
# to fit a capacity budget, not derived from how much bracing carries
# incidentally in other lifts, and brace now targets at the same rate as
# every other pattern. See docs/GUIDE.md's "Capacity arithmetic".
PATTERN_WEIGHT = {
    "squat": 1.0, "hinge": 1.0, "push": 1.0, "pull": 1.0, "flexion": 1.0, "brace": 1.0,
    "engine": 1.0,
}

HHR_CREDIT = 0.5  # toward the heavy cell's EWMA; lower only if priority shifts toward maximal strength

# The EWMA's memory, in units of "target intervals" (dimensionless, shared
# across every cell regardless of its own target rate): tau_cell = MEMORY_K
# / rho_cell, where rho_cell is that cell's target rate in exposures/day.
# This is what makes a rare hhr cell forget slowly and a frequent light
# cell forget fast without any per-cell tuning - see ewma_score()'s
# docstring for the derivation of why this makes every on-target cell's
# steady-state EWMA equal to MEMORY_K regardless of rho.
#
# Free choice, not derived - carried over from the design this replaced.
# See docs/AUDIT.md: the *continuous*-time steady state is exactly
# MEMORY_K, but real exposures are discrete events, and the discrete steady
# state is 1/(1 - exp(-1/MEMORY_K)), which is only close to MEMORY_K once
# MEMORY_K is reasonably large. At MEMORY_K=2 the discrete steady state is
# about 1.27x too high, i.e. training exactly on target forever would read
# as slightly "ahead" rather than exactly 0 - real, not blocking, flagged.
MEMORY_K = 2.0

REP_SCHEMES = ["5x5", "3x3x3x3x3", "5-3-1", "8x3", "3RM build", "10x2", "4x6"]


@dataclass(frozen=True)
class AdvisorConfig:
    """Everything above (TARGET_RATE, PATTERN_WEIGHT, HHR_CREDIT, MEMORY_K)
    bundled into one overridable object, plus disabled_styles - a style a
    user has opted out of entirely (e.g. "no heavies"). DEFAULT_CONFIG below
    reproduces the module-level constants exactly, so every function that
    takes an optional `config` parameter behaves identically to before this
    existed when called without one.

    This exists for the webapp (docs/STATUS.md): different users
    want different targets - more conditioning focus, no heavy work, more
    or less overall volume - without forking the model. The webapp layer
    translates friendly per-user knobs into a concrete AdvisorConfig; this
    module only needs to know how to score against whatever config it's
    handed, not about the friendly knobs themselves.
    """
    target_rate: dict[str, float]
    pattern_weight: dict[str, float]
    hhr_credit: float
    memory_k: float
    disabled_styles: frozenset[str] = frozenset()


DEFAULT_CONFIG = AdvisorConfig(
    target_rate=dict(TARGET_RATE),
    pattern_weight=dict(PATTERN_WEIGHT),
    hhr_credit=HHR_CREDIT,
    memory_k=MEMORY_K,
    disabled_styles=frozenset(),
)


@dataclass(frozen=True)
class Exercise:
    id: str
    name: str
    implement: str
    laterality: str  # 'bilateral' | 'unilateral'
    admissible_styles: tuple[str, ...]
    E: dict[str, float]


@dataclass(frozen=True)
class Dictionary:
    version: int
    patterns: tuple[str, ...]
    styles: tuple[str, ...]
    exercises: dict[str, Exercise]


def load_dictionary(path: Path = DICT_PATH) -> Dictionary:
    raw = json.loads(path.read_text())
    exercises = {
        e["id"]: Exercise(
            id=e["id"], name=e["name"], implement=e["implement"], laterality=e["laterality"],
            admissible_styles=tuple(e["admissible_styles"]), E=e["E"],
        )
        for e in raw["exercises"]
    }
    return Dictionary(
        version=raw["version"], patterns=tuple(raw["patterns"]), styles=tuple(raw["styles"]),
        exercises=exercises,
    )


def _parse_date(s: str) -> Date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _days_between(a: Date, b: Date) -> int:
    return (a - b).days


# --- Masking: which (pattern, style) cells can be filled at all --------------

def admissible_cells(dictionary: Dictionary, config: AdvisorConfig = DEFAULT_CONFIG) -> list[tuple[str, str]]:
    """Sorted, not a set: iteration order here ends up as tie-break order in
    advise()'s stable sort. A plain set's order depends on Python's
    per-process string-hash randomization - see git history for the bug
    this caused before cells were sorted.

    `config.disabled_styles` removes a whole style for this user (e.g. "no
    heavies") before anything downstream ever sees it - candidate_cells(),
    recommend(), and build_log_stats() all derive their cell set from this
    function, so disabling a style here is sufficient to make it disappear
    everywhere, not something each caller has to re-check.
    """
    cells = set()
    for ex in dictionary.exercises.values():
        for pattern, weight in ex.E.items():
            if weight <= 0:
                continue
            for style in ex.admissible_styles:
                if style in config.disabled_styles:
                    continue
                cells.add((pattern, style))
    return sorted(cells)


def tau_cell(pattern: str, style: str, config: AdvisorConfig = DEFAULT_CONFIG) -> float:
    """config.memory_k / rho_cell, where rho_cell is the cell's target rate
    in exposures/day. Memory scales with how often the cell is supposed to
    fire: a rare hhr cell forgets slowly, a frequent light cell forgets
    fast - one shared dimensionless constant (memory_k) instead of a
    per-cell tuned window.
    """
    rho = config.target_rate[style] * config.pattern_weight.get(pattern, 1.0) / 7.0
    return config.memory_k / rho


# --- Log aggregation: raw sets + dictionary -> per-cell EWMA state -----------

@dataclass
class LogStats:
    """Precomputed views over the full log, as of a given day. No window
    truncation - the EWMA's own decay is what makes old training matter
    less, so every event in history is replayed, not just a trailing slice.
    """
    as_of: Date
    cell_ewma: dict[tuple[str, str], float]               # (pattern, style) -> EWMA as of as_of
    cell_last_seen: dict[tuple[str, str], Date]            # (pattern, style) -> most recent literal exposure
    move_last_seen: dict[str, Date]                        # exercise_id -> most recent date logged
    heavy_scheme_last_seen: dict[tuple[str, str], Date]    # (pattern, scheme) -> most recent heavy use


def build_log_stats(
    dictionary: Dictionary, sets: list[db.LoggedSet], as_of: Date, config: AdvisorConfig = DEFAULT_CONFIG,
) -> LogStats:
    # "Exposure" is a day-level event - this pattern was trained at this
    # style today - not a per-set one. Collapse to one event per distinct
    # (date, style, exercise) first, so a 5x5 doesn't count as 5 exposures
    # and a unilateral exercise logged L+R on the same day doesn't count
    # as 2.
    events: set[tuple[Date, str, str]] = set()
    for s in sets:
        d = _parse_date(s.date)
        if d > as_of:
            continue
        events.add((d, s.style, s.exercise_id))

    move_last_seen: dict[str, Date] = {}
    cell_last_seen: dict[tuple[str, str], Date] = {}
    # (pattern, style) -> [(date, weight), ...], unsorted until consumed below.
    increments: dict[tuple[str, str], list[tuple[Date, float]]] = {}

    for d, style, exercise_id in sorted(events):
        ex = dictionary.exercises.get(exercise_id)
        if ex is None:
            continue  # logged under a retired/unknown id; ignore rather than crash

        if move_last_seen.get(exercise_id) is None or d > move_last_seen[exercise_id]:
            move_last_seen[exercise_id] = d

        for pattern, weight in ex.E.items():
            if weight <= 0:
                continue

            key = (pattern, style)
            if cell_last_seen.get(key) is None or d > cell_last_seen[key]:
                cell_last_seen[key] = d
            increments.setdefault(key, []).append((d, weight))

            # hhr credits HHR_CREDIT toward the heavy cell's own EWMA (never
            # 1.0, never fully replaces fresh heavy work), decayed on
            # heavy's own tau - a *separate* contribution from hhr's own
            # cell, which still gets the full weight on its own clock.
            # Deliberately does NOT update cell_last_seen[(pattern, heavy)]
            # - "how long since you actually did this heavy" should reflect
            # literal heavy-style exposure only.
            if style == "hhr":
                heavy_key = (pattern, "heavy")
                increments.setdefault(heavy_key, []).append((d, config.hhr_credit * weight))

    # Scheme staleness is a set-level field (each set carries its own
    # scheme label), so this pass still needs the raw rows.
    heavy_scheme_last_seen: dict[tuple[str, str], Date] = {}
    for s in sets:
        d = _parse_date(s.date)
        if d > as_of or s.style != "heavy" or not s.scheme:
            continue
        ex = dictionary.exercises.get(s.exercise_id)
        if ex is None:
            continue
        for pattern, weight in ex.E.items():
            if weight <= 0:
                continue
            key = (pattern, s.scheme)
            if heavy_scheme_last_seen.get(key) is None or d > heavy_scheme_last_seen[key]:
                heavy_scheme_last_seen[key] = d

    cell_ewma: dict[tuple[str, str], float] = {}
    for pattern, style in admissible_cells(dictionary, config):
        tau = tau_cell(pattern, style, config)
        evs = sorted(increments.get((pattern, style), []))
        ewma = 0.0
        prev: Date | None = None
        for d, w in evs:
            if prev is not None:
                ewma *= math.exp(-(d - prev).days / tau)
            ewma += w
            prev = d
        if prev is not None:
            ewma *= math.exp(-(as_of - prev).days / tau)
        cell_ewma[(pattern, style)] = ewma

    return LogStats(
        as_of=as_of, cell_ewma=cell_ewma, cell_last_seen=cell_last_seen,
        move_last_seen=move_last_seen, heavy_scheme_last_seen=heavy_scheme_last_seen,
    )


# --- Scoring -------------------------------------------------------------------

def ewma_score(ewma: float, memory_k: float = MEMORY_K) -> float:
    """score = -log(ewma / memory_k).

    At the continuous-time steady state, a cell trained exactly on target
    has ewma == memory_k, so score == 0 - every cell is on the same scale
    regardless of its own target rate, with no separate normalization step.
    During neglect, ewma decays as ewma0 * exp(-t/tau), so score grows
    ~linearly at rate 1/tau - this recovers "staleness" from the same
    formula. Training forever at half the target rate settles at a steady
    ewma of memory_k/2, i.e. a constant score of log(2) ~ 0.69 - this
    recovers "chronic deficit," also for free.

    A cell with ewma == 0 (never trained, ever, at this style) scores
    +inf, not a large finite number: there is no sense in which "not yet
    quantified" should be capped below "quantified and very bad." Sorting
    and arithmetic both handle inf correctly; see recommend() for the one
    place this needed a guard (two simultaneously-untouched styles).

    `memory_k` defaults to the module-level MEMORY_K so every existing
    call site keeps working unchanged; pass `config.memory_k` explicitly
    wherever a per-user AdvisorConfig is in scope (see AdvisorConfig).
    """
    if ewma <= 0:
        return math.inf
    return -math.log(ewma / memory_k)


# --- Candidate selection: stalest eligible variant per cell -------------------

def eligible_exercises(dictionary: Dictionary, pattern: str, style: str) -> list[Exercise]:
    return [
        ex for ex in dictionary.exercises.values()
        if ex.E.get(pattern, 0.0) > 0 and style in ex.admissible_styles
    ]


def stalest_candidate(dictionary: Dictionary, stats: LogStats, pattern: str, style: str) -> Exercise | None:
    candidates = eligible_exercises(dictionary, pattern, style)
    if not candidates:
        return None

    def staleness(ex: Exercise) -> float:
        last = stats.move_last_seen.get(ex.id)
        return math.inf if last is None else _days_between(stats.as_of, last)

    # Ties broken by heavier pattern weight, then id, for determinism.
    return max(candidates, key=lambda ex: (staleness(ex), ex.E.get(pattern, 0.0), ex.id))


@dataclass
class Candidate:
    exercise: Exercise
    pattern: str
    style: str
    score: float
    reasoning: dict


def score_cell(
    dictionary: Dictionary,
    stats: LogStats,
    pattern: str,
    style: str,
    config: AdvisorConfig = DEFAULT_CONFIG,
) -> Candidate | None:
    """Pure ewma_score() - no same-pattern penalty. Repeats are handled
    categorically upstream by candidate_cells(), which excludes a pattern
    entirely once it's been used earlier in the session, rather than
    scoring around it - see candidate_cells()'s docstring.
    """
    candidate = stalest_candidate(dictionary, stats, pattern, style)
    if candidate is None:
        return None

    ewma = stats.cell_ewma.get((pattern, style), 0.0)
    score = ewma_score(ewma, config.memory_k)

    cell_last = stats.cell_last_seen.get((pattern, style))
    return Candidate(
        exercise=candidate, pattern=pattern, style=style, score=score,
        reasoning={
            "ewma": ewma,
            "cell_staleness_days": _days_between(stats.as_of, cell_last) if cell_last else None,
        },
    )


# --- Hard/soft session rules ---------------------------------------------------

def _patterns_loaded(chosen: list[Exercise]) -> set[str]:
    loaded = set()
    for ex in chosen:
        loaded |= {p for p, w in ex.E.items() if w > 0}
    return loaded


def candidate_cells(
    dictionary: Dictionary, style: str, chosen: list[Exercise], config: AdvisorConfig = DEFAULT_CONFIG,
) -> list[tuple[str, str]]:
    """Admissible (pattern, style) cells for this style, after the hard rule:
    a pattern already used earlier in the session - at any style, heavy or
    workout - is excluded outright from every later pick in that session,
    not merely penalized. Categorical rather than scored, on request: with
    the old soft -1.1 penalty, a severely neglected cell's score could in
    principle still grow past the penalty and repeat anyway; this rule
    guarantees it never will. Only ever advisory - it constrains what
    advise() will *suggest*, not what you're free to actually do. See
    docs/GUIDE.md.

    Was hhr-only before this rule (a pattern loaded today couldn't reappear
    at hhr, but could still repeat at light or heavy); now applies to every
    style uniformly.
    """
    cells = [(p, s) for (p, s) in admissible_cells(dictionary, config) if s == style]
    loaded_today = _patterns_loaded(chosen)
    return [(p, s) for (p, s) in cells if p not in loaded_today]


# --- Public queries --------------------------------------------------------------

def advise(
    dictionary: Dictionary,
    sets: list[db.LoggedSet],
    day: Date,
    mode: str,
    chosen: list[str] | None = None,
    config: AdvisorConfig = DEFAULT_CONFIG,
) -> list[Candidate]:
    """Ranked movements for `mode` ('heavy' or 'workout'), given ids already `chosen`.

    'workout' covers the light and hhr styles together. `chosen` accumulates
    across calls; every pick re-scores everything (greedy, no lookahead -
    see docs/GUIDE.md).

    `config` carries a user's targets/weights/disabled styles (see
    AdvisorConfig); defaults to the shared DEFAULT_CONFIG. A style disabled
    in `config` simply contributes no candidates - e.g. mode="heavy" with
    "heavy" disabled returns an empty list, same shape as a day with no
    eligible exercises, so callers already handle it without a special case.
    """
    if mode not in ("heavy", "workout"):
        raise ValueError("mode must be 'heavy' or 'workout'")
    styles = ["heavy"] if mode == "heavy" else ["light", "hhr"]

    chosen_ex = [dictionary.exercises[eid] for eid in (chosen or []) if eid in dictionary.exercises]

    stats = build_log_stats(dictionary, sets, day, config)

    candidates = []
    for style in styles:
        for pattern, s in candidate_cells(dictionary, style, chosen_ex, config):
            c = score_cell(dictionary, stats, pattern, s, config)
            if c is not None:
                candidates.append(c)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def suggest_rep_scheme(dictionary: Dictionary, sets: list[db.LoggedSet], day: Date, pattern: str) -> str:
    """Heavy only. Least-recently-used scheme for this pattern, so e.g. heavy
    cleans don't get offered a scheme heavy deadlifts just used."""
    stats = build_log_stats(dictionary, sets, day)

    def last_used(scheme: str) -> Date | None:
        return stats.heavy_scheme_last_seen.get((pattern, scheme))

    never = [s for s in REP_SCHEMES if last_used(s) is None]
    if never:
        return never[0]
    return min(REP_SCHEMES, key=lambda s: last_used(s))


def suggest_rep_count(sets: list[db.LoggedSet], exercise_id: str, style: str = "heavy") -> int | None:
    """Rolling median reps logged for this exercise at this style. None until logged."""
    reps = [s.reps for s in sets if s.exercise_id == exercise_id and s.style == style and s.reps is not None]
    if not reps:
        return None
    return round(median(reps))


@dataclass
class Verdict:
    style: str | None
    band: str  # 'clear' | 'leaning' | 'no strong signal'
    margin: float


# log(2): the score a cell settles at under chronic training at exactly
# half its target rate (see ewma_score()'s docstring) - a physically
# meaningful reference point on this scale, reused here as the band
# thresholds rather than inventing separate round numbers. Tuned-class,
# same as the coefficients above: the thresholds' specific multiples of
# log(2) (1x for "clear", 0.5x for "leaning") are a free choice, not
# derived.
_LOG2 = math.log(2)


def recommend(
    dictionary: Dictionary, sets: list[db.LoggedSet], day: Date, config: AdvisorConfig = DEFAULT_CONFIG,
) -> Verdict:
    """Which style is most needed, comparing each style's admissible cells
    on the same log-score scale (already normalized - see ewma_score()).

    Aggregates by median, not mean: individual cell scores are now
    unbounded (an untouched cell scores +inf), so a mean would let one
    never-touched cell in an otherwise well-covered style dominate the
    verdict. The median is robust to that single outlier while still
    reading as "behind" once enough of the style's cells actually are.

    A style in `config.disabled_styles` has no admissible cells (see
    admissible_cells()) and so is silently skipped here the same way a
    style with zero admissible cells always was - "no heavies" for a user
    means recommend() will simply never suggest heavy.
    """
    stats = build_log_stats(dictionary, sets, day, config)

    style_scores: dict[str, float] = {}
    for style in dictionary.styles:
        cells = [(p, s) for (p, s) in admissible_cells(dictionary, config) if s == style]
        if not cells:
            continue
        style_scores[style] = median(ewma_score(stats.cell_ewma.get(c, 0.0), config.memory_k) for c in cells)

    if not style_scores:
        return Verdict(style=None, band="no strong signal", margin=0.0)

    ranked = sorted(style_scores.items(), key=lambda kv: kv[1], reverse=True)
    top_style, top_value = ranked[0]

    if top_value <= 0:
        # Every style is at or ahead of its own target - being the
        # *least* over-covered isn't the same as being needed.
        return Verdict(style=None, band="no strong signal", margin=0.0)

    second_value = ranked[1][1] if len(ranked) > 1 else 0.0
    if math.isinf(top_value) and math.isinf(second_value):
        # Cold start: every cell in both top styles has literally never
        # been touched. Not a real signal about which is *more* needed.
        return Verdict(style=None, band="no strong signal", margin=0.0)

    margin = top_value - second_value

    if margin > _LOG2:
        band = "clear"
    elif margin > _LOG2 / 2:
        band = "leaning"
    else:
        band = "no strong signal"
        top_style = None

    return Verdict(style=top_style, band=band, margin=margin)

import math
from dataclasses import fields
from datetime import date, timedelta

import pytest

from training import advisor, db


@pytest.fixture
def dictionary():
    return advisor.load_dictionary()


@pytest.fixture
def conn():
    return db.connect()


@pytest.fixture
def user_id(conn):
    """A synthetic test user - schema.sql now requires every session to
    belong to one (see docs/STATUS.md's webapp section), so every
    test that logs sets needs a real user_id to attach them to, not just a
    connection.
    """
    return db.create_user(conn, "test@example.com", "not-a-real-hash")


def log_heavy_set(conn, user_id, day, exercise_id, dict_version, scheme="5x5", reps=5):
    session_id = db.log_session(conn, user_id, day.isoformat())
    block_id = db.log_block(conn, session_id, "heavy")
    db.log_set(conn, block_id, exercise_id, dict_version, reps=reps, scheme=scheme)


def log_hhr_set(conn, user_id, day, exercise_id, dict_version, reps=10):
    session_id = db.log_session(conn, user_id, day.isoformat())
    block_id = db.log_block(conn, session_id, "hhr")
    db.log_set(conn, block_id, exercise_id, dict_version, reps=reps)


def log_light_set(conn, user_id, day, exercise_id, dict_version, reps=10):
    session_id = db.log_session(conn, user_id, day.isoformat())
    block_id = db.log_block(conn, session_id, "light")
    db.log_set(conn, block_id, exercise_id, dict_version, reps=reps)


# --- The EWMA score itself: the math the whole reversal rests on -------------

def test_ewma_score_matches_its_own_derivation():
    """On-target (ewma == MEMORY_K) scores exactly 0; chronic half-rate
    training settles at exactly log(2); never-trained scores +inf, not a
    large finite number - there's no reason "unquantified" should rank
    below "quantified and very bad."
    """
    assert advisor.ewma_score(advisor.MEMORY_K) == pytest.approx(0.0)
    assert advisor.ewma_score(advisor.MEMORY_K / 2) == pytest.approx(math.log(2))
    assert advisor.ewma_score(0.0) == math.inf


def test_periodic_steady_state_is_not_exactly_memory_k():
    """Known, documented gap (see AUDIT.md): ewma_score() derives score==0
    at target from the *continuous*-time steady state (ewma -> rho*tau ==
    MEMORY_K). Real exposures are discrete events. For a perfectly periodic
    log at exactly the target rate, the true discrete steady state is
    1/(1 - exp(-1/MEMORY_K)), which only approaches MEMORY_K as MEMORY_K
    grows - at MEMORY_K=2 it's about 27% too high, so training exactly on
    target forever reads as slightly *ahead* (score < 0), not exactly 0.
    This test pins that gap down numerically so it can't silently drift;
    it is not evidence of a bug.
    """
    k = advisor.MEMORY_K
    discrete_steady_state = 1.0 / (1.0 - math.exp(-1.0 / k))
    assert discrete_steady_state == pytest.approx(1.27 * k, rel=0.02)
    assert advisor.ewma_score(discrete_steady_state) < 0  # reads as "ahead" at exact on-target cadence


def test_tau_scales_inversely_with_pattern_weight(monkeypatch):
    """tau_cell divides by PATTERN_WEIGHT, so halving a pattern's weight must
    double its memory, since rare cells must forget slowly to reach the same
    steady-state ewma as frequent ones. This is the mechanism that makes
    MEMORY_K a single shared constant instead of a per-cell tuned window.

    Pinned via monkeypatch rather than against a real pattern's weight,
    because every pattern in the current dictionary is weighted 1.0 - brace's
    old 0.25 discount was dropped (see docs/AUDIT.md: it was
    reverse-engineered to fit a capacity budget, not derived from anything
    principled about brace itself). This test pins the mechanism, not a
    specific weight choice.
    """
    # tau_cell reads config.pattern_weight (DEFAULT_CONFIG.pattern_weight when
    # no config is passed), which is its own dict built from PATTERN_WEIGHT at
    # import time - not the same object, so it's what needs patching here.
    monkeypatch.setitem(advisor.DEFAULT_CONFIG.pattern_weight, "squat", 0.25)
    tau_quarter_weight = advisor.tau_cell("squat", "heavy")
    monkeypatch.setitem(advisor.DEFAULT_CONFIG.pattern_weight, "squat", 1.0)
    tau_full_weight = advisor.tau_cell("squat", "heavy")
    assert tau_quarter_weight == pytest.approx(4 * tau_full_weight)
    # Also true across styles: hhr's target is smaller than light's, so its
    # memory is proportionally longer.
    assert advisor.tau_cell("squat", "hhr") > advisor.tau_cell("squat", "light")


def test_ewma_decays_by_the_exact_exponential_factor(dictionary, conn, user_id):
    log_heavy_set(conn, user_id, date(2026, 1, 1), "back_squat", dictionary.version)
    tau = advisor.tau_cell("squat", "heavy")

    as_of = date(2026, 1, 1) + timedelta(days=10)
    stats = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), as_of)
    expected = 1.0 * math.exp(-10 / tau)
    assert stats.cell_ewma["squat", "heavy"] == pytest.approx(expected)


def test_ewma_accumulates_same_day_events_without_decay(dictionary, conn, user_id):
    """A 5x5 is one training day, not five - multiple sets logged the same
    day must collapse to a single exposure event before the recursion runs,
    same invariant the old windowed-count model enforced.
    """
    today = date(2026, 1, 15)
    session_id = db.log_session(conn, user_id, today.isoformat())
    block_id = db.log_block(conn, session_id, "heavy")
    for _ in range(5):
        db.log_set(conn, block_id, "back_squat", dictionary.version, reps=5, scheme="5x5")

    stats = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), today)
    assert stats.cell_ewma["squat", "heavy"] == pytest.approx(1.0)


def test_unilateral_sides_same_day_still_count_as_one_exposure(dictionary, conn, user_id):
    today = date(2026, 1, 15)
    session_id = db.log_session(conn, user_id, today.isoformat())
    block_id = db.log_block(conn, session_id, "light")
    db.log_set(conn, block_id, "db_lunge", dictionary.version, reps=8, side="L")
    db.log_set(conn, block_id, "db_lunge", dictionary.version, reps=8, side="R")

    stats = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), today)
    assert stats.cell_ewma["squat", "light"] == pytest.approx(1.0)


def test_hhr_credits_heavy_ewma_without_touching_heavy_last_seen(dictionary, conn, user_id):
    """hhr work partially satisfies the heavy cell's own EWMA (at
    HHR_CREDIT, decayed on heavy's own tau) but must NOT count as "you did
    this heavy today" for staleness-reporting purposes - those are
    different questions (has the target been serviced vs. when did you
    last do the classic lift).
    """
    today = date(2026, 1, 15)
    log_hhr_set(conn, user_id, today, "back_squat", dictionary.version)

    stats = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), today)
    assert stats.cell_ewma["squat", "heavy"] == pytest.approx(advisor.HHR_CREDIT)
    assert stats.cell_ewma["squat", "hhr"] == pytest.approx(1.0)
    assert stats.cell_last_seen.get(("squat", "heavy")) is None
    assert stats.cell_last_seen.get(("squat", "hhr")) == today


def test_logging_improves_that_cells_score(dictionary, conn, user_id):
    today = date(2026, 1, 15)
    before = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), today)
    score_before = advisor.ewma_score(before.cell_ewma["squat", "heavy"])

    log_heavy_set(conn, user_id, today, "back_squat", dictionary.version)

    after = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), today)
    score_after = advisor.ewma_score(after.cell_ewma["squat", "heavy"])

    assert score_after < score_before


# --- Structural invariants, unaffected by the reversal ------------------------

def test_admissible_cells_order_is_deterministic(dictionary):
    """Iteration order here becomes tie-break order in advise()'s stable
    sort, so it must not depend on Python's per-process hash randomization.
    """
    cells = advisor.admissible_cells(dictionary)
    assert cells == sorted(cells)
    assert advisor.admissible_cells(dictionary) == cells


def test_every_admissible_cell_has_an_eligible_exercise(dictionary):
    empty = [
        (p, s) for (p, s) in advisor.admissible_cells(dictionary)
        if not advisor.eligible_exercises(dictionary, p, s)
    ]
    assert empty == []


def test_every_pattern_and_style_appears_in_some_admissible_cell(dictionary):
    cells = advisor.admissible_cells(dictionary)
    assert {p for p, _s in cells} == set(dictionary.patterns)
    assert {s for _p, s in cells} == set(dictionary.styles)


def test_hard_rule_blocks_hhr_for_pattern_loaded_today(dictionary, conn, user_id):
    today = date(2026, 1, 15)
    candidates = advisor.advise(
        dictionary, db.fetch_sets(conn, user_id), today, "workout", chosen=["back_squat"]
    )
    hhr_patterns = {c.pattern for c in candidates if c.style == "hhr"}
    assert "squat" not in hhr_patterns


def test_empty_log_still_ranks_everything_via_inf_score(dictionary, conn, user_id):
    """No hard overdue clock exists any more (removed entirely, not just
    collapsed to one). A log with zero history still produces a full,
    sensibly-ranked candidate list on day one, because ewma_score(0) is
    already +inf for every untouched cell - the clock used to guarantee
    the same outcome by a fixed date, but was always redundant with this
    for the cold-start case specifically.
    """
    today = date(2026, 1, 15)
    candidates = advisor.advise(dictionary, db.fetch_sets(conn, user_id), today, "heavy")
    assert candidates, "expected a candidate for every admissible heavy pattern"
    assert all(c.score == math.inf for c in candidates), \
        "a log with zero history should score every untouched cell at +inf without any clock"


def test_a_stale_but_once_touched_cell_is_not_forced_above_a_fresh_one(dictionary, conn, user_id):
    """The behavior change the clock removal actually costs: previously a
    cell past 7 days untouched got a fixed, enormous bonus regardless of
    how the EWMA read it. Now two cells are ranked purely by relative
    score - a moderately-stale cell with real training history can score
    *better* than a cell that was touched more recently but is behind a
    much higher target, and nothing forces the stale one to the front on
    a calendar guarantee. This test exists to make that trade-off visible
    rather than implicit: it does not assert a "should win" outcome,
    only that no artificial floor is being applied anywhere in the score.
    """
    today = date(2026, 1, 15)
    log_heavy_set(conn, user_id, today - timedelta(days=10), "back_squat", dictionary.version)
    log_light_set(conn, user_id, today - timedelta(days=1), "push_up", dictionary.version)

    stats = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), today)
    squat_score = advisor.ewma_score(stats.cell_ewma["squat", "heavy"])
    push_score = advisor.ewma_score(stats.cell_ewma["push", "light"])

    assert math.isfinite(squat_score)
    assert math.isfinite(push_score)  # neither cell gets an unbounded clock bonus anywhere


# --- Confirming what's actually gone -------------------------------------------

def test_resource_matrix_and_interference_are_gone(dictionary):
    field_names = {f.name for f in fields(advisor.Exercise)}
    assert "R" not in field_names
    dict_field_names = {f.name for f in fields(advisor.Dictionary)}
    assert "resources" not in dict_field_names
    assert "resource_interaction" not in dict_field_names
    assert not hasattr(advisor, "resource_interference")
    assert not hasattr(advisor, "RESOURCE_OVERLAP_COEF")


def test_window_deficit_gamma_and_overdue_machinery_are_gone():
    retired = [
        "WINDOW_DAYS", "deficits", "normalized_deficit", "cell_target",
        "marginal_deficit_by_pattern", "GAMMA_BEHIND", "GAMMA_OVER",
        "LAMBDA_CELL", "LAMBDA_UNI", "CELL_STALENESS_CAP",
        "UNILATERAL_TARGET", "UNILATERAL_SHORTFALL_CAP",
        "CLOCK_EXEMPT_PATTERNS", "LAMBDA_MOVE", "MOVEMENT_STALENESS_CAP",
        "overdue_bonus", "OVERDUE_DAYS", "OVERDUE_BONUS",
    ]
    for name in retired:
        assert not hasattr(advisor, name), name


def test_no_hard_override_survives_anywhere_in_score_cell(dictionary, conn):
    """The overdue clock is gone entirely, not collapsed further - there is
    no hard guarantee mechanism left in the model at all. Confirms
    LogStats itself no longer carries the pattern-level "last touched at
    any style" field the clock depended on, since nothing else needs it.
    """
    field_names = {f.name for f in fields(advisor.LogStats)}
    assert "pattern_last_seen" not in field_names


def test_unilateral_exposures_are_no_longer_tracked_in_log_stats(dictionary, conn):
    """Laterality still gets logged (schema.sql's `side` column is
    untouched) - it's just unscored for now. Nothing is lost; a future
    pass can reintroduce a scoring term against the same raw log.
    """
    field_names = {f.name for f in fields(advisor.LogStats)}
    assert "unilateral_exposures" not in field_names


def test_dictionary_v10_is_the_pruned_minimal_set(dictionary):
    assert dictionary.version == 10
    # 29 real movements plus `rest`, which carries an empty E so it's never
    # suggested and contributes no exposure - see test below. Was 19+rest
    # after the v10 prune; ten dumbbell/kettlebell/gymnastics movements were
    # added later, when logging real sessions surfaced the gap. That is the
    # intended growth path - add on contact with reality, not speculatively.
    assert len(dictionary.exercises) == 30
    assert set(dictionary.patterns) == {
        "hinge", "push", "squat", "pull", "flexion", "brace", "engine",
    }


def test_rest_is_inert(dictionary, conn, user_id):
    """`rest` exists so interval work is expressible as movement rows, but
    it isn't training. An empty E means it can never be suggested and can
    never move a score - the documented meaning of an empty row
    (docs/AUDIT.md).
    """
    rest = dictionary.exercises["rest"]
    assert rest.E == {}
    for pattern in dictionary.patterns:
        assert rest not in advisor.eligible_exercises(dictionary, pattern, "light")

    today = date(2026, 1, 15)
    session_id = db.log_session(conn, user_id, today.isoformat())
    block_id = db.log_block(conn, session_id, "light")
    db.log_set(conn, block_id, "rest", dictionary.version, duration_seconds=60)

    stats = advisor.build_log_stats(dictionary, db.fetch_sets(conn, user_id), today)
    assert all(value == 0 for value in stats.cell_ewma.values())


# --- AdvisorConfig: per-user overrides (webapp support) -----------------------

def test_default_config_matches_the_module_constants():
    """DEFAULT_CONFIG exists so every function works unchanged when nobody
    passes a config - it has to actually reproduce TARGET_RATE/
    PATTERN_WEIGHT/HHR_CREDIT/MEMORY_K, not just resemble them, or every
    pre-config-refactor call site would silently start scoring differently.
    """
    assert advisor.DEFAULT_CONFIG.target_rate == advisor.TARGET_RATE
    assert advisor.DEFAULT_CONFIG.pattern_weight == advisor.PATTERN_WEIGHT
    assert advisor.DEFAULT_CONFIG.hhr_credit == advisor.HHR_CREDIT
    assert advisor.DEFAULT_CONFIG.memory_k == advisor.MEMORY_K
    assert advisor.DEFAULT_CONFIG.disabled_styles == frozenset()


def test_disabling_a_style_removes_its_cells_everywhere(dictionary):
    """disabled_styles is read in exactly one place (admissible_cells()) and
    everything else - candidate_cells(), build_log_stats(), recommend() -
    derives its cell set from that function, so disabling a style there
    should be sufficient on its own.
    """
    no_heavy = advisor.AdvisorConfig(
        target_rate=advisor.TARGET_RATE, pattern_weight=advisor.PATTERN_WEIGHT,
        hhr_credit=advisor.HHR_CREDIT, memory_k=advisor.MEMORY_K,
        disabled_styles=frozenset({"heavy"}),
    )
    cells = advisor.admissible_cells(dictionary, no_heavy)
    assert all(style != "heavy" for _pattern, style in cells)
    # nothing lost besides heavy - every pattern that had a light or hhr
    # cell before still has it.
    default_cells = advisor.admissible_cells(dictionary)
    assert cells == [(p, s) for p, s in default_cells if s != "heavy"]


def test_advise_heavy_mode_is_empty_when_heavy_is_disabled(dictionary, conn, user_id):
    no_heavy = advisor.AdvisorConfig(
        target_rate=advisor.TARGET_RATE, pattern_weight=advisor.PATTERN_WEIGHT,
        hhr_credit=advisor.HHR_CREDIT, memory_k=advisor.MEMORY_K,
        disabled_styles=frozenset({"heavy"}),
    )
    today = date(2026, 1, 15)
    candidates = advisor.advise(
        dictionary, db.fetch_sets(conn, user_id), today, "heavy", config=no_heavy
    )
    assert candidates == []


def test_recommend_never_picks_a_disabled_style_even_when_most_neglected(dictionary, conn, user_id):
    """Heavy is never touched in this log - ordinarily the most-neglected
    style by far - but with heavy disabled, recommend() must never surface
    it, because it no longer has any admissible cells to be neglected in.
    """
    no_heavy = advisor.AdvisorConfig(
        target_rate=advisor.TARGET_RATE, pattern_weight=advisor.PATTERN_WEIGHT,
        hhr_credit=advisor.HHR_CREDIT, memory_k=advisor.MEMORY_K,
        disabled_styles=frozenset({"heavy"}),
    )
    today = date(2026, 1, 15)
    for days_ago in range(1, 30, 3):
        log_light_set(conn, user_id, today - timedelta(days=days_ago), "push_up", dictionary.version)
    verdict = advisor.recommend(dictionary, db.fetch_sets(conn, user_id), today, config=no_heavy)
    assert verdict.style != "heavy"


def test_custom_target_rate_changes_tau_proportionally(dictionary):
    """A user asking for more volume at a style (a higher target_rate) should
    shorten that style's memory - the model should react faster to neglect
    of something asked for more often, same mechanism PATTERN_WEIGHT already
    used, just driven by a different field on the config.
    """
    boosted = advisor.AdvisorConfig(
        target_rate={**advisor.TARGET_RATE, "hhr": advisor.TARGET_RATE["hhr"] * 2},
        pattern_weight=advisor.PATTERN_WEIGHT, hhr_credit=advisor.HHR_CREDIT, memory_k=advisor.MEMORY_K,
    )
    default_tau = advisor.tau_cell("squat", "hhr")
    boosted_tau = advisor.tau_cell("squat", "hhr", boosted)
    assert boosted_tau == pytest.approx(default_tau / 2)


# --- recommend() on the log-score scale -----------------------------------------

def test_recommend_cold_start_is_no_strong_signal(dictionary, conn, user_id):
    """Day one, empty log: every cell in every style scores +inf. The
    isinf/isinf guard in recommend() exists specifically so this doesn't
    read as a false "clear" verdict for whichever style happens to sort
    first among ties.
    """
    today = date(2026, 1, 15)
    verdict = advisor.recommend(dictionary, db.fetch_sets(conn, user_id), today)
    assert verdict.band == "no strong signal"
    assert verdict.style is None
    assert math.isfinite(verdict.margin)


def test_recommend_favours_the_neglected_style(dictionary, conn, user_id):
    today = date(2026, 1, 15)
    heavy_ex = {
        "squat": "back_squat", "hinge": "deadlift", "push": "strict_press",
        "pull": "bent_over_row", "brace": "farmers_carry",
    }
    hhr_ex = {**heavy_ex, "flexion": "toes_to_bar", "engine": "burpee"}

    for d in range(0, 42, 3):
        day = today - timedelta(days=d)
        for exercise_id in heavy_ex.values():
            log_heavy_set(conn, user_id, day, exercise_id, dictionary.version)
        for exercise_id in hhr_ex.values():
            log_hhr_set(conn, user_id, day, exercise_id, dictionary.version)

    verdict = advisor.recommend(dictionary, db.fetch_sets(conn, user_id), today)
    assert verdict.style == "light"
    assert verdict.band in ("clear", "leaning")


def test_recommend_does_not_favour_the_least_overshot_style(dictionary, conn, user_id):
    """All three styles over-covered - being least-over-covered isn't
    'needed'. Mirrors the two-month simulation finding that motivated this
    guard in the original deficit-based model; still applies verbatim on
    the log-score scale.
    """
    today = date(2026, 1, 15)
    light_ex = {
        "hinge": "deadlift", "push": "push_up", "squat": "air_squat",
        "pull": "strict_pull_up", "flexion": "toes_to_bar",
        "brace": "farmers_carry", "engine": "running",
    }
    heavy_ex = {
        "hinge": "deadlift", "push": "strict_press", "squat": "back_squat",
        "pull": "bent_over_row", "brace": "farmers_carry",
    }
    hhr_ex = {**heavy_ex, "flexion": "toes_to_bar", "engine": "burpee"}

    for d in range(0, 42):
        day = today - timedelta(days=d)
        for exercise_id in light_ex.values():
            log_light_set(conn, user_id, day, exercise_id, dictionary.version)
        for exercise_id in heavy_ex.values():
            log_heavy_set(conn, user_id, day, exercise_id, dictionary.version)
        for exercise_id in hhr_ex.values():
            log_hhr_set(conn, user_id, day, exercise_id, dictionary.version)

    verdict = advisor.recommend(dictionary, db.fetch_sets(conn, user_id), today)
    assert verdict.style is None
    assert verdict.band == "no strong signal"

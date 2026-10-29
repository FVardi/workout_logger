"""Recomputes every number in docs/math.tex's worked example (section "A
worked example") from the code, so the document can't drift from
advisor.py unnoticed. If one of these fails, either the code changed on
purpose and the example needs updating, or the code changed by accident.
"""

import math
from datetime import date

import pytest

from training import advisor, db

AS_OF = date(2026, 1, 15)


@pytest.fixture
def dictionary():
    return advisor.load_dictionary()


@pytest.fixture
def sets(dictionary):
    conn = db.connect()
    user_id = db.create_user(conn, "example@example.com", "not-a-real-hash")

    def log(day, style, exercise_id, n_sets):
        session_id = db.log_session(conn, user_id, day.isoformat())
        block_id = db.log_block(conn, session_id, style)
        for _ in range(n_sets):
            db.log_set(conn, block_id, exercise_id, dictionary.version, reps=5)

    log(date(2026, 1, 1), "heavy", "back_squat", 5)
    log(date(2026, 1, 5), "hhr", "thruster", 3)
    log(date(2026, 1, 8), "light", "air_squat", 3)
    log(date(2026, 1, 12), "heavy", "back_squat", 5)
    return db.fetch_sets(conn, user_id)


def test_the_example_has_sixteen_sets(sets):
    assert len(sets) == 16


def test_ewma_and_score_per_trained_cell(dictionary, sets):
    stats = advisor.build_log_stats(dictionary, sets, AS_OF)
    expected = {
        ("push", "heavy"): (0.1463, 2.6152),
        ("push", "hhr"): (0.4182, 1.5649),
        ("squat", "hhr"): (0.4182, 1.5649),
        ("squat", "light"): (0.6065, 1.1931),
        ("squat", "heavy"): (1.4702, 0.3077),
    }
    for cell, (m, sigma) in expected.items():
        assert stats.cell_ewma[cell] == pytest.approx(m, abs=5e-5), cell
        assert advisor.ewma_score(stats.cell_ewma[cell]) == pytest.approx(sigma, abs=5e-5), cell

    untrained = [c for c, m in stats.cell_ewma.items() if m == 0]
    assert len(untrained) == 11


def test_squat_heavy_by_hand(dictionary, sets):
    """The one cell the document computes term by term."""
    tau = advisor.tau_cell("squat", "heavy")
    assert tau == pytest.approx(18.6667, abs=1e-4)
    by_hand = math.exp(-14 / tau) + 0.25 * math.exp(-10 / tau) + math.exp(-3 / tau)
    stats = advisor.build_log_stats(dictionary, sets, AS_OF)
    assert stats.cell_ewma[("squat", "heavy")] == pytest.approx(by_hand)


def test_advise_heavy(dictionary, sets):
    ranked = advisor.advise(dictionary, sets, AS_OF, "heavy")
    got = [(c.pattern, c.exercise.id, round(c.score, 4)) for c in ranked]
    assert got == [
        ("brace", "suitcase_carry", math.inf),
        ("hinge", "deadlift", math.inf),
        ("pull", "strict_pull_up", math.inf),
        ("push", "strict_press", 2.6152),
        ("squat", "front_squat", 0.3077),
    ]


def test_session_rule_after_picking_deadlift(dictionary, sets):
    before = advisor.advise(dictionary, sets, AS_OF, "workout")
    after = advisor.advise(dictionary, sets, AS_OF, "workout", chosen=["deadlift"])
    cells = lambda cs: [(c.pattern, c.style) for c in cs]
    assert cells(after) == [c for c in cells(before) if c[0] != "hinge"]
    assert ("hinge", "light") in cells(before) and ("hinge", "hhr") in cells(before)


def test_recommend_is_no_strong_signal(dictionary, sets):
    verdict = advisor.recommend(dictionary, sets, AS_OF)
    assert verdict.style is None
    assert verdict.band == "no strong signal"


def test_on_target_sawtooth_numbers():
    """Proposition on training exactly on target, at k = 2."""
    k = advisor.MEMORY_K
    q = math.exp(-1 / k)
    sigma_after = math.log(k * (1 - q))
    assert sigma_after == pytest.approx(-0.240, abs=5e-4)
    assert sigma_after + 1 / k == pytest.approx(0.260, abs=5e-4)
    assert sigma_after + 1 / (2 * k) == pytest.approx(0.010, abs=5e-4)

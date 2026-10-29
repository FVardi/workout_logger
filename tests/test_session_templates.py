"""Unit tests for the workout-template expansion layer.

Pure logic, no HTTP and no database - the webapp-level tests
(test_webapp.py) cover the wiring; these cover the arithmetic and the
grouping rules.
"""

import pytest

from training.webapp import session_templates as st


def mv(exercise_id, style, **kw):
    return st.MovementInput(exercise_id=exercise_id, style=style, **kw)


# --- Strength ------------------------------------------------------------------

def test_strength_logs_one_set_per_row_with_its_own_load():
    """The motivating case: a wave up in load is one piece, and each set
    has to keep its own reps and load rather than being flattened into an
    'N sets x R reps' summary that loses the ramp.
    """
    rows = [
        st.StrengthSetInput(reps=5, load=80),
        st.StrengthSetInput(reps=4, load=85),
        st.StrengthSetInput(reps=3, load=90),
        st.StrengthSetInput(reps=2, load=95),
        st.StrengthSetInput(reps=1, load=100),
    ]
    blocks = st.expand_strength("back_squat", "heavy", rows)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.style == "heavy"
    assert block.structure == "5/4/3/2/1"
    assert [(s.reps, s.load) for s in block.sets] == [(5, 80), (4, 85), (3, 90), (2, 95), (1, 100)]


def test_strength_blank_rows_are_dropped():
    rows = [st.StrengthSetInput(reps=5, load=100), st.StrengthSetInput(), st.StrengthSetInput()]
    blocks = st.expand_strength("back_squat", "heavy", rows)
    assert len(blocks[0].sets) == 1


def test_strength_with_no_rows_is_an_error():
    with pytest.raises(st.TemplateError):
        st.expand_strength("back_squat", "heavy", [st.StrengthSetInput()])


def test_explicit_scheme_wins_over_the_derived_structure():
    rows = [st.StrengthSetInput(reps=5, load=100)] * 5
    blocks = st.expand_strength("back_squat", "heavy", rows, scheme="5x5")
    assert blocks[0].structure == "5x5"
    assert all(s.scheme == "5x5" for s in blocks[0].sets)


# --- Rounds for time -------------------------------------------------------------

def test_rounds_repeats_every_movement_once_per_round():
    blocks = st.expand_rounds(5, [
        mv("thruster", "hhr", reps=10, load=43),
        mv("strict_pull_up", "hhr", reps=15),
    ])
    assert len(blocks) == 1
    sets = blocks[0].sets
    assert len(sets) == 10  # 5 rounds x 2 movements
    assert sum(1 for s in sets if s.exercise_id == "thruster") == 5
    assert blocks[0].structure == "5 RFT"


def test_rounds_are_stored_round_major():
    """Round-major order means reading the sets back reconstructs the piece
    as performed, rather than as a per-movement summary.
    """
    blocks = st.expand_rounds(2, [mv("thruster", "hhr", reps=10), mv("burpee", "light", reps=5)])
    all_sets = [s for b in blocks for s in b.sets]
    # Grouping splits by style, so check within one style's block instead.
    thruster_block = next(b for b in blocks if b.style == "hhr")
    assert len(thruster_block.sets) == 2
    assert len(all_sets) == 4


def test_zero_rounds_is_an_error():
    with pytest.raises(st.TemplateError):
        st.expand_rounds(0, [mv("thruster", "hhr", reps=10)])


def test_no_movements_is_an_error():
    with pytest.raises(st.TemplateError):
        st.expand_rounds(3, [])


# --- Mixed styles ------------------------------------------------------------------

def test_mixed_styles_split_into_one_block_per_style():
    """blocks.style holds a single value, so a metcon mixing an hhr movement
    with a light-only one has to become two blocks. They share a structure
    label so the piece is still recognisable as one thing.
    """
    blocks = st.expand_rounds(3, [
        mv("thruster", "hhr", reps=10, load=43),
        mv("push_up", "light", reps=15),
    ])
    assert len(blocks) == 2
    assert {b.style for b in blocks} == {"hhr", "light"}
    assert all(b.structure == "3 RFT" for b in blocks)
    hhr = next(b for b in blocks if b.style == "hhr")
    light = next(b for b in blocks if b.style == "light")
    assert all(s.exercise_id == "thruster" for s in hhr.sets)
    assert all(s.exercise_id == "push_up" for s in light.sets)


def test_a_heavy_movement_inside_a_metcon_stays_heavy():
    """The case that ruled out deriving style from the template: a heavy
    triple into a run is a heavy stimulus in a workout container. The
    template must carry the style the human chose, per movement.
    """
    blocks = st.expand_rounds(3, [
        mv("back_squat", "heavy", reps=3, load=140),
        mv("running", "light", distance=400),
    ])
    heavy = next(b for b in blocks if b.style == "heavy")
    assert all(s.exercise_id == "back_squat" and s.reps == 3 for s in heavy.sets)


# --- Ladders -------------------------------------------------------------------------

def test_ladder_uses_the_rep_scheme_per_round():
    blocks = st.expand_ladder([21, 15, 9], [
        mv("thruster", "hhr", load=43), mv("strict_pull_up", "hhr"),
    ])
    block = blocks[0]
    assert block.structure == "21-15-9"
    thruster_reps = [s.reps for s in block.sets if s.exercise_id == "thruster"]
    assert thruster_reps == [21, 15, 9]


def test_ladder_keeps_per_movement_load():
    blocks = st.expand_ladder([21, 15, 9], [mv("thruster", "hhr", load=43)])
    assert all(s.load == 43 for s in blocks[0].sets)


def test_a_ladder_can_repeat_on_a_clock():
    """5-4-3-2-1 of squats and pull-ups, three times, every seven minutes.
    One piece with three repeats - not three pieces, and not a `rounds`
    piece with ten movement rows.
    """
    blocks = st.expand_ladder(
        [5, 4, 3, 2, 1],
        [mv("back_squat", "heavy", load=120), mv("strict_pull_up", "heavy")],
        rounds=3, interval_seconds=420,
    )
    block = blocks[0]
    assert block.structure == "3 x 5-4-3-2-1, every 7 min"
    assert len(block.sets) == 30           # 3 repeats x 5 rungs x 2 movements
    assert block.time_cap_seconds == 1260  # 3 x 7 min, fixed by the clock

    squat_reps = [s.reps for s in block.sets if s.exercise_id == "back_squat"]
    assert squat_reps == [5, 4, 3, 2, 1] * 3


def test_a_repeated_ladder_summarises_one_rung_sequence():
    """The repeat count is already in the structure label, so printing the
    ladder three times in the description is noise:
    "5/4/3/2/1", not "5/4/3/2/1/5/4/3/2/1/5/4/3/2/1".
    """
    blocks = st.expand_ladder(
        [5, 4, 3, 2, 1],
        [mv("back_squat", "heavy", load=120), mv("strict_pull_up", "heavy")],
        rounds=3, interval_seconds=420,
    )
    summary = st.summarise(blocks, NAMES)
    assert summary == (
        "3 x 5-4-3-2-1, every 7 min: back squats 5/4/3/2/1 @120, "
        "strict pull-ups 5/4/3/2/1"
    )


def test_a_single_ladder_is_unchanged_by_the_repeat_support():
    blocks = st.expand_ladder([21, 15, 9], [
        mv("thruster", "hhr", load=43), mv("strict_pull_up", "hhr"),
    ])
    assert blocks[0].structure == "21-15-9"
    assert st.summarise(blocks, NAMES) == \
        "21-15-9: thrusters 21/15/9 @43, strict pull-ups 21/15/9"


def test_collapsing_a_repeat_does_not_drop_a_genuine_duplicate():
    """21-15-9-9 isn't a repeating cycle, so every rung still shows -
    the earlier fix for exactly this must survive the collapse."""
    blocks = st.expand_ladder([21, 15, 9, 9], [mv("thruster", "hhr", load=43)])
    assert "21/15/9/9" in st.summarise(blocks, NAMES)


def test_zero_ladder_repeats_is_an_error():
    with pytest.raises(st.TemplateError):
        st.expand_ladder([21, 15, 9], [mv("thruster", "hhr")], rounds=0)


@pytest.mark.parametrize("raw,expected", [
    ("21-15-9", [21, 15, 9]),
    ("21 15 9", [21, 15, 9]),
    ("21,15,9", [21, 15, 9]),
    ("10", [10]),
])
def test_parse_rep_scheme_accepts_common_separators(raw, expected):
    assert st.parse_rep_scheme(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "21-x-9", "21--9-0"])
def test_parse_rep_scheme_rejects_nonsense(raw):
    with pytest.raises(st.TemplateError):
        st.parse_rep_scheme(raw)


# --- AMRAP ---------------------------------------------------------------------------

def test_amrap_expands_completed_rounds():
    blocks = st.expand_amrap(720, [
        mv("burpee", "light", reps=5), mv("air_squat", "light", reps=10),
    ], rounds_completed=8)
    block = blocks[0]
    assert block.structure == "AMRAP 12"
    assert block.time_cap_seconds == 720
    assert len(block.sets) == 16  # 8 rounds x 2 movements


def test_amrap_partial_reps_become_one_extra_set_of_the_first_movement():
    blocks = st.expand_amrap(720, [
        mv("burpee", "light", reps=5), mv("air_squat", "light", reps=10),
    ], rounds_completed=8, partial_reps=3)
    burpee_sets = [s for s in blocks[0].sets if s.exercise_id == "burpee"]
    assert len(burpee_sets) == 9
    assert burpee_sets[-1].reps == 3


def test_amrap_with_nothing_completed_is_an_error():
    with pytest.raises(st.TemplateError):
        st.expand_amrap(720, [mv("burpee", "light", reps=5)], rounds_completed=0)


# --- EMOM ------------------------------------------------------------------------------

def test_emom_repeats_a_single_movement_once_per_interval():
    blocks = st.expand_emom(12, [mv("thruster", "hhr", reps=10, load=43)])
    block = blocks[0]
    assert block.structure == "EMOM 12"
    assert len(block.sets) == 12
    assert block.time_cap_seconds == 720


def test_emom_movements_alternate_rather_than_all_happening_each_interval():
    """The distinction from rounds-for-time: twelve minutes alternating two
    movements is six of each, not twelve of both. Getting this wrong would
    double the recorded volume.
    """
    blocks = st.expand_emom(12, [
        mv("thruster", "hhr", reps=10), mv("burpee", "light", reps=8),
    ])
    sets = [s for b in blocks for s in b.sets]
    assert len(sets) == 12
    assert sum(1 for s in sets if s.exercise_id == "thruster") == 6
    assert sum(1 for s in sets if s.exercise_id == "burpee") == 6


def test_emom_with_three_movements_cycles_through_them():
    blocks = st.expand_emom(9, [
        mv("thruster", "hhr", reps=10), mv("burpee", "light", reps=8),
        mv("air_squat", "light", reps=15),
    ])
    sets = [s for b in blocks for s in b.sets]
    assert len(sets) == 9
    assert {s.exercise_id for s in sets} == {"thruster", "burpee", "air_squat"}


def test_a_longer_interval_is_labelled_accordingly():
    """Every two minutes for twenty minutes is ten intervals, labelled by
    the total rather than the interval count.
    """
    blocks = st.expand_emom(10, [mv("back_squat", "heavy", reps=3, load=140)],
                            interval_seconds=120)
    assert blocks[0].structure == "E2MOM 20"
    assert len(blocks[0].sets) == 10
    assert blocks[0].time_cap_seconds == 1200


def test_an_odd_interval_falls_back_to_a_plain_label():
    blocks = st.expand_emom(8, [mv("burpee", "light", reps=5)], interval_seconds=90)
    assert blocks[0].structure == "every 90s x 8"


@pytest.mark.parametrize("intervals,movements", [
    (0, [("burpee", 5)]),
    (5, []),
])
def test_emom_rejects_empty_input(intervals, movements):
    with pytest.raises(st.TemplateError):
        st.expand_emom(intervals, [mv(e, "light", reps=r) for e, r in movements])


# --- Plain-text summaries ---------------------------------------------------------

NAMES = {
    "air_squat": "Air squat", "double_unders": "Double-unders", "back_squat": "Back squat",
    "thruster": "Thruster", "strict_pull_up": "Strict pull-up", "running": "Running",
    "farmers_carry": "Farmer's carry", "burpee": "Burpee",
}


def test_summary_collapses_repeated_rounds_into_one_line():
    """The motivating case: three rounds of two movements is one piece to
    read, not six entries.
    """
    blocks = st.expand_rounds(3, [
        mv("air_squat", "light", reps=20), mv("double_unders", "light", reps=40),
    ])
    assert st.summarise(blocks, NAMES) == "3 RFT: 20 air squats, 40 double-unders"


def test_summary_keeps_per_set_loads_when_they_vary():
    """A wave's whole point is that the load changes, so amount and load
    have to stay paired rather than being averaged away.
    """
    rows = [
        st.StrengthSetInput(reps=5, load=80), st.StrengthSetInput(reps=4, load=85),
        st.StrengthSetInput(reps=3, load=90), st.StrengthSetInput(reps=2, load=95),
        st.StrengthSetInput(reps=1, load=100),
    ]
    blocks = st.expand_strength("back_squat", "heavy", rows)
    summary = st.summarise(blocks, NAMES)
    assert summary == "5/4/3/2/1: back squats 5@80, 4@85, 3@90, 2@95, 1@100"


def test_summary_of_a_ladder_lists_the_rep_scheme_once_per_movement():
    blocks = st.expand_ladder([21, 15, 9], [
        mv("thruster", "hhr", load=43), mv("strict_pull_up", "hhr"),
    ])
    assert st.summarise(blocks, NAMES) == \
        "21-15-9: thrusters 21/15/9 @43, strict pull-ups 21/15/9"


def test_summary_spans_a_mixed_style_piece():
    """The piece splits into two blocks for storage, but it was one piece
    and the summary has to describe all of it.
    """
    blocks = st.expand_rounds(3, [
        mv("back_squat", "heavy", reps=3, load=140), mv("running", "light", distance=400),
    ])
    assert len(blocks) == 2
    summary = st.summarise(blocks, NAMES)
    assert "back squats" in summary and "running" in summary


def test_distance_and_duration_read_name_first():
    """'running 400 m' rather than '400 m runnings' - the name isn't the
    thing being counted, so it isn't pluralised either.
    """
    blocks = st.expand_rounds(1, [mv("running", "light", distance=400)])
    assert st.summarise(blocks, NAMES) == "1 RFT: running 400 m"


def test_intervals_are_expressible_as_rounds():
    """The engine template's `intervals` field is gone; 4 x 400 m is four
    rounds of one 400 m movement, which stores the same four sets.
    """
    blocks = st.expand_rounds(4, [mv("running", "light", distance=400)])
    assert len(blocks[0].sets) == 4
    assert all(s.distance == 400 for s in blocks[0].sets)


@pytest.mark.parametrize("name,expected", [
    ("Air squat", "air squats"),
    ("Burpee", "burpees"),
    ("Double-unders", "double-unders"),   # already plural
    ("Farmer's carry", "farmer's carries"),
    ("Thruster", "thrusters"),
])
def test_pluralise(name, expected):
    assert st.pluralise(name) == expected


def test_format_volume_reads_like_the_summary():
    volume = [
        {"exercise_id": "air_squat", "reps": 60, "distance": 0, "duration_seconds": 0},
        {"exercise_id": "running", "reps": 0, "distance": 1200, "duration_seconds": 0},
        {"exercise_id": "farmers_carry", "reps": 0, "distance": 0, "duration_seconds": 120},
    ]
    assert st.format_volume(volume, NAMES) == \
        "60 air squats, 1200 m running, 120 s farmer's carry"

"""Workout templates: turn a filled-in template into concrete blocks and sets.

Pure functions, no web framework imports, so the expansion logic is
testable on its own (tests/test_session_templates.py).

Three things worth knowing before changing anything here:

**Templates do not affect scoring.** advisor.build_log_stats() reads only
(date, style, exercise_id) and collapses same-day repeats to a single
exposure. Whether a template expands to 1 set or 45 is invisible to the
model. The reps/loads/distances exist for the human's own record and for
the volume and e1RM analysis docs/GUIDE.md specs under
"Deliberately absent". So expansion should be *faithful* (a log you can
read back later and recognise), not arithmetically clever.

**A template implies nothing about style.** A 3-rep back squat at 90%
inside a metcon is a heavy stimulus that happens to sit in a workout
container - GUIDE.md says as much ("hhr is a stimulus, not a session
type"). The model has no 1RM to infer intensity from either. So style is
carried per movement, chosen by the human, never derived from which
template they picked.

**One block per style.** blocks.style is a single value, so a mixed-style
piece (thrusters at hhr, push-ups at light) becomes two blocks sharing one
structure label. That's a storage detail, not a claim that the person did
two separate pieces.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SetSpec:
    """One row destined for the `sets` table."""
    exercise_id: str
    style: str
    reps: int | None = None
    load: float | None = None
    distance: float | None = None
    duration_seconds: int | None = None
    scheme: str | None = None


@dataclass(frozen=True)
class BlockSpec:
    """One row destined for `blocks`, plus the sets inside it."""
    style: str
    structure: str | None
    time_cap_seconds: int | None
    sets: list[SetSpec] = field(default_factory=list)


@dataclass(frozen=True)
class MovementInput:
    """One movement row as typed into a metcon-style template."""
    exercise_id: str
    style: str
    reps: int | None = None
    load: float | None = None
    distance: float | None = None
    duration_seconds: int | None = None


@dataclass(frozen=True)
class StrengthSetInput:
    """One row of a strength template: this many reps at this load.

    Per-set rather than "N sets x R reps", because that's how strength work
    is actually programmed - a 5/4/3/2/1 wave up in load is one piece, not
    five templates.
    """
    reps: int | None = None
    load: float | None = None


class TemplateError(ValueError):
    """Bad template input - message is safe to show the user."""


def _group_into_blocks(
    sets: list[SetSpec], structure: str | None, time_cap_seconds: int | None
) -> list[BlockSpec]:
    """One BlockSpec per distinct style, preserving first-seen style order
    and each style's internal set order.
    """
    by_style: dict[str, list[SetSpec]] = {}
    for s in sets:
        by_style.setdefault(s.style, []).append(s)
    return [
        BlockSpec(style=style, structure=structure, time_cap_seconds=time_cap_seconds, sets=style_sets)
        for style, style_sets in by_style.items()
    ]


def expand_strength(
    exercise_id: str, style: str, rows: list[StrengthSetInput], scheme: str | None = None,
) -> list[BlockSpec]:
    """Straight strength work: one set per row, load and reps varying freely.

    5 @ 80, 4 @ 85, 3 @ 90, 2 @ 95, 1 @ 100 is five rows, logged as five
    sets, and summarised as the structure string "5/4/3/2/1".
    """
    rows = [r for r in rows if r.reps is not None or r.load is not None]
    if not rows:
        raise TemplateError("Add at least one set.")

    sets = [
        SetSpec(exercise_id=exercise_id, style=style, reps=r.reps, load=r.load, scheme=scheme)
        for r in rows
    ]
    rep_labels = [str(r.reps) for r in rows if r.reps is not None]
    structure = scheme or ("/".join(rep_labels) if rep_labels else None)
    return _group_into_blocks(sets, structure, None)


def expand_rounds(
    rounds: int,
    movements: list[MovementInput],
    time_cap_seconds: int | None = None,
    label: str = "RFT",
) -> list[BlockSpec]:
    """Rounds for time: every movement repeated once per round.

    Round-major order (all of round 1, then all of round 2, ...) so reading
    the set list back reconstructs how the piece was actually performed.
    """
    if rounds < 1:
        raise TemplateError("Rounds must be at least 1.")
    if not movements:
        raise TemplateError("Add at least one movement.")

    sets = []
    for _round in range(rounds):
        for m in movements:
            sets.append(SetSpec(
                exercise_id=m.exercise_id, style=m.style, reps=m.reps, load=m.load,
                distance=m.distance, duration_seconds=m.duration_seconds,
            ))
    return _group_into_blocks(sets, f"{rounds} {label}", time_cap_seconds)


def expand_ladder(
    rep_scheme: list[int],
    movements: list[MovementInput],
    time_cap_seconds: int | None = None,
    rounds: int = 1,
    interval_seconds: int | None = None,
) -> list[BlockSpec]:
    """A descending/ascending rep ladder - 21-15-9 and friends.

    Distinct from expand_rounds() because the rep count changes per round,
    so each movement's per-round reps come from the ladder rather than from
    the movement row. A movement row's own `reps` is ignored here; its load
    and style still apply.

    `rounds` repeats the whole ladder, and `interval_seconds` records that
    each repeat starts on a clock: "5-4-3-2-1 of squats and pull-ups, every
    seven minutes" is one piece with three repeats, not three pieces. Built
    out of expand_rounds() instead it would need one movement row per rung
    per movement, and would describe itself as
    "back squats 5/4/3/2/1/5/4/3/2/1/5/4/3/2/1".
    """
    if not rep_scheme:
        raise TemplateError("Enter a rep scheme, e.g. 21-15-9.")
    if not movements:
        raise TemplateError("Add at least one movement.")
    if rounds < 1:
        raise TemplateError("Rounds must be at least 1.")

    sets = []
    for _round in range(rounds):
        for reps in rep_scheme:
            for m in movements:
                sets.append(SetSpec(
                    exercise_id=m.exercise_id, style=m.style, reps=reps, load=m.load,
                    distance=m.distance, duration_seconds=m.duration_seconds,
                ))

    structure = "-".join(str(r) for r in rep_scheme)
    if rounds > 1:
        structure = f"{rounds} x {structure}"
    if interval_seconds:
        minutes = interval_seconds / 60
        every = f"{minutes:g}" if minutes % 1 else f"{int(minutes)}"
        structure += f", every {every} min"
        # The piece's length is fixed by the clock it runs on, same as an EMOM.
        time_cap_seconds = time_cap_seconds or rounds * interval_seconds

    return _group_into_blocks(sets, structure, time_cap_seconds)


def expand_amrap(
    time_cap_seconds: int,
    movements: list[MovementInput],
    rounds_completed: int,
    partial_reps: int | None = None,
) -> list[BlockSpec]:
    """AMRAP: full rounds completed, expanded like rounds-for-time.

    `partial_reps` records an unfinished final round as one extra set of
    the first movement. Approximate by nature - a partial round could have
    stopped anywhere - but it beats discarding the information, and nothing
    downstream treats set counts as exact (see the module docstring).
    """
    if not movements:
        raise TemplateError("Add at least one movement.")
    if rounds_completed < 0:
        raise TemplateError("Rounds completed can't be negative.")

    minutes = round(time_cap_seconds / 60) if time_cap_seconds else None
    structure = f"AMRAP {minutes}" if minutes else "AMRAP"

    sets = []
    for _round in range(rounds_completed):
        for m in movements:
            sets.append(SetSpec(
                exercise_id=m.exercise_id, style=m.style, reps=m.reps, load=m.load,
                distance=m.distance, duration_seconds=m.duration_seconds,
            ))
    if partial_reps:
        first = movements[0]
        sets.append(SetSpec(
            exercise_id=first.exercise_id, style=first.style, reps=partial_reps, load=first.load,
            distance=first.distance, duration_seconds=first.duration_seconds,
        ))
    if not sets:
        raise TemplateError("Log at least one completed round, or some partial reps.")
    return _group_into_blocks(sets, structure, time_cap_seconds)


def pluralise(name: str) -> str:
    """Naive English pluralisation, good enough for movement names.

    Only used for rep-counted movements ("20 air squats"); distance and
    duration read as "running 1200 m", where the name isn't counted and so
    isn't pluralised.
    """
    lowered = name.lower()
    if lowered.endswith("s"):
        return lowered                      # double-unders, dips
    if lowered.endswith("y") and lowered[-2:-1] not in "aeiou":
        return lowered[:-1] + "ies"         # farmer's carry -> carries
    return lowered + "s"


def _amount_str(spec: SetSpec) -> str | None:
    if spec.reps is not None:
        return str(spec.reps)
    if spec.distance is not None:
        return f"{spec.distance:g} m"
    if spec.duration_seconds is not None:
        seconds = spec.duration_seconds
        # Whole minutes read as minutes - "2 min bike", not "120 s bike".
        if seconds >= 60 and seconds % 60 == 0:
            return f"{seconds // 60} min"
        return f"{seconds} s"
    return None


def _cycle_length(exercise_ids: list[str]) -> int:
    """Length of the repeating round, or the whole list if it doesn't repeat.

    Sets are stored round-major, so a piece of N rounds over k movements
    reads as the same k exercise ids repeated N times. Finding k is what
    lets a summary describe one round in order rather than collapsing every
    occurrence of a movement together - the difference between
    "2 min bike, 10 toes-to-bar, 2 min bike, ..." and a single "bike".
    """
    n = len(exercise_ids)
    for k in range(1, n):
        if n % k:
            continue
        if all(exercise_ids[i] == exercise_ids[i % k] for i in range(n)):
            return k
    return n


def _describe(specs: list, names: dict[str, str]) -> str:
    """One movement's worth of description: its amounts, and its load."""
    exercise_id = specs[0].exercise_id
    raw_name = names.get(exercise_id, exercise_id)
    rep_counted = specs[0].reps is not None
    name = pluralise(raw_name) if rep_counted else raw_name.lower()

    amounts = [a for a in (_amount_str(s) for s in specs) if a]
    distinct_amounts = list(dict.fromkeys(amounts))
    loads = list(dict.fromkeys(s.load for s in specs if s.load is not None))

    if len(loads) > 1:
        # Load varies per set - the point of a wave - so amount and load
        # have to stay paired: "back squats 5@80, 4@85, 3@90".
        pairs = [
            f"{a}@{s.load:g}" if s.load is not None else a
            for a, s in zip(amounts, specs)
        ]
        return f"{name} {', '.join(pairs)}"

    load_str = f" @{loads[0]:g}" if loads else ""
    if not distinct_amounts:
        return f"{name}{load_str}"
    if len(distinct_amounts) == 1 and rep_counted:
        # Same every round: "20 air squats". Collapsing is safe here
        # precisely because every round matched.
        return f"{distinct_amounts[0]} {name}{load_str}"
    if len(distinct_amounts) == 1:
        # Distance/duration reads better with the name first: "running 400 m".
        return f"{name} {distinct_amounts[0]}{load_str}"
    # A varying sequence lists every round, not the distinct values -
    # de-duplicating would render 21-15-9-9 as "21/15/9" and lose a round.
    #
    # Unless the sequence is itself a repeat: a ladder done three times is
    # "5/4/3/2/1", not "5/4/3/2/1/5/4/3/2/1/5/4/3/2/1". The repeat count is
    # already in the structure label ("3 x 5-4-3-2-1"), so printing it here
    # too is noise. De-duplicating a *cycle* is safe in a way that
    # de-duplicating individual values isn't - nothing is lost, because the
    # omitted rounds are identical to the ones shown.
    repeat = _cycle_length(amounts)
    return f"{name} {'/'.join(amounts[:repeat])}{load_str}"


def summarise(blocks: list[BlockSpec], names: dict[str, str]) -> str:
    """The piece written out in plain text, e.g.
    "3 RFT: 20 air squats, 40 double-unders" or
    "back squats 5@80, 4@85, 3@90, 2@95, 1@100".

    Reads the expanded sets rather than the original form input, so it
    describes what was actually recorded. Stored on the block at log time
    (see schema.sql) so the log stays readable even if exercise names
    change later.
    """
    structure = next((b.structure for b in blocks if b.structure), None)
    rows = [
        {
            "exercise_id": s.exercise_id, "reps": s.reps, "load": s.load,
            "distance": s.distance, "duration_seconds": s.duration_seconds, "side": None,
        }
        for b in blocks for s in b.sets
    ]
    return summarise_sets(rows, structure, names)


def summarise_sets(sets: list[dict], structure: str | None, names: dict[str, str]) -> str:
    """Same output as summarise(), but from stored set rows rather than
    BlockSpecs.

    Two callers: summarise() above (log time), and the display fallback for
    pieces logged before `blocks.summary` existed - which is why this takes
    plain dicts and tolerates the `side` column.
    """
    # Unilateral work is stored per side, so counting both would double every
    # amount. One logical set is the L/R pair; keep one of them.
    all_sets = [s for s in sets if s.get("side") != "R"]
    if not all_sets:
        return ""

    specs = [
        SetSpec(
            exercise_id=s["exercise_id"], style="",
            reps=s.get("reps"), load=s.get("load"),
            distance=s.get("distance"), duration_seconds=s.get("duration_seconds"),
        )
        for s in all_sets
    ]

    # Group by position within the repeating round, when there is one.
    #
    # Grouping by *exercise* instead loses the shape of any workout that
    # uses a movement more than once per round: "2 min bike, 10 T2B, 2 min
    # bike, 60 double-unders, 2 min bike, 10 burpees" collapsed to a single
    # "bike". Positional grouping keeps each occurrence in sequence while
    # still folding the rounds together, and gives the same answer as
    # before whenever each movement appears once (a 21-15-9 still reads
    # "thrusters 21/15/9").
    #
    # No repeat means no rounds to fold - a strength wave, or a mixed-style
    # piece whose sets were split across blocks and so aren't round-major
    # any more. Those group by *consecutive* runs of the same exercise:
    # merging a wave's five back squats into one description is right,
    # merging a bike that appears at the start and again at the end is not,
    # and consecutiveness is exactly what separates the two.
    cycle = _cycle_length([s.exercise_id for s in specs])
    if cycle < len(specs):
        groups = [specs[position::cycle] for position in range(cycle)]
    else:
        groups = []
        for spec in specs:
            if groups and groups[-1][0].exercise_id == spec.exercise_id:
                groups[-1].append(spec)
            else:
                groups.append([spec])

    body = ", ".join(_describe(group, names) for group in groups)
    return f"{structure}: {body}" if structure else body


def format_volume(volume: list[dict], names: dict[str, str]) -> str:
    """Per-exercise totals as one readable line: "60 air squats, 1200 m
    running". Mirrors summarise()'s phrasing so the two read consistently.
    """
    parts = []
    for entry in volume:
        raw_name = names.get(entry["exercise_id"], entry["exercise_id"])
        if entry.get("reps"):
            parts.append(f"{entry['reps']} {pluralise(raw_name)}")
        elif entry.get("distance"):
            parts.append(f"{entry['distance']:g} m {raw_name.lower()}")
        elif entry.get("duration_seconds"):
            parts.append(f"{entry['duration_seconds']} s {raw_name.lower()}")
        else:
            parts.append(raw_name.lower())
    return ", ".join(parts)


def expand_emom(
    intervals: int, movements: list[MovementInput], interval_seconds: int = 60,
) -> list[BlockSpec]:
    """Every minute on the minute: one movement per interval, alternating.

    This is what makes EMOM a different shape from rounds-for-time rather
    than a relabelling of it. In a round, *every* movement happens; in an
    EMOM with several movements, one happens per interval and they take
    turns - twelve minutes alternating thrusters and burpees is six of
    each, not twelve of both.

    Rest is the remainder of each interval and isn't logged: it's implied
    by the format, unlike the explicit rest rows an interval piece built
    from the rounds template would use.
    """
    if intervals < 1:
        raise TemplateError("An EMOM needs at least one interval.")
    if not movements:
        raise TemplateError("Add at least one movement.")
    if interval_seconds < 1:
        raise TemplateError("The interval has to be at least a second long.")

    sets = []
    for index in range(intervals):
        m = movements[index % len(movements)]
        sets.append(SetSpec(
            exercise_id=m.exercise_id, style=m.style, reps=m.reps, load=m.load,
            distance=m.distance, duration_seconds=m.duration_seconds,
        ))

    if interval_seconds == 60:
        structure = f"EMOM {intervals}"
    elif interval_seconds % 60 == 0:
        structure = f"E{interval_seconds // 60}MOM {intervals * interval_seconds // 60}"
    else:
        structure = f"every {interval_seconds}s x {intervals}"

    # An EMOM's length is fixed by its format, so the total is known rather
    # than being a cap someone chose - but it's the same field either way.
    return _group_into_blocks(sets, structure, intervals * interval_seconds)


def parse_amounts(raw: str) -> list[float]:
    """'21-15-9' or '400 400' or '20,20,20' -> a list of amounts.

    Like parse_rep_scheme() but returns floats, because an amount sequence
    can be distances or seconds rather than reps.
    """
    tokens = raw.replace(",", " ").replace("-", " ").split()
    if not tokens:
        raise TemplateError("Enter at least one amount, e.g. 21-15-9.")
    try:
        amounts = [float(t) for t in tokens]
    except ValueError:
        raise TemplateError(f"Couldn't read {raw!r} as amounts - try e.g. 21-15-9.") from None
    if any(a <= 0 for a in amounts):
        raise TemplateError("Amounts must be positive.")
    return amounts


@dataclass(frozen=True)
class EditedMovement:
    """One exercise as shown on the edit page: its whole amount sequence on
    a single row, rather than one row per set.
    """
    exercise_id: str
    style: str
    amounts: list[float]
    unit: str = "reps"
    load: float | None = None


def expand_edited(
    movements: list[EditedMovement], structure: str | None, time_cap_seconds: int | None = None,
) -> list[BlockSpec]:
    """Turn edit-page rows back into sets, round-major.

    Round i takes the i-th amount from each movement, so a piece edited and
    saved keeps the interleaved order it was performed in ("21 thrusters,
    21 pull-ups, 15 thrusters, ..."). Sequences of different lengths are
    fine - a movement that has run out is simply skipped for later rounds,
    which is what a piece with one long run and three sets of squats looks
    like.
    """
    if not movements:
        raise TemplateError("A piece needs at least one movement.")

    rounds = max(len(m.amounts) for m in movements)
    sets = []
    for index in range(rounds):
        for m in movements:
            if index >= len(m.amounts):
                continue
            amount = m.amounts[index]
            sets.append(SetSpec(
                exercise_id=m.exercise_id, style=m.style, load=m.load,
                reps=int(amount) if m.unit == "reps" else None,
                distance=amount if m.unit == "m" else None,
                duration_seconds=int(amount) if m.unit == "s" else None,
            ))
    return _group_into_blocks(sets, structure, time_cap_seconds)


def parse_rep_scheme(raw: str) -> list[int]:
    """'21-15-9' or '21 15 9' or '21,15,9' -> [21, 15, 9]."""
    tokens = raw.replace(",", " ").replace("-", " ").split()
    if not tokens:
        raise TemplateError("Enter a rep scheme, e.g. 21-15-9.")
    try:
        scheme = [int(t) for t in tokens]
    except ValueError:
        raise TemplateError(f"Couldn't read {raw!r} as a rep scheme - try e.g. 21-15-9.") from None
    if any(r < 1 for r in scheme):
        raise TemplateError("Rep counts must be positive.")
    return scheme

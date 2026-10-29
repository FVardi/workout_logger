# Training advisor - two-month synthetic simulation report

Not real data. A closed-loop check: the simulated trainee always follows `advise()`'s top pick, three sessions/week, one heavy piece then four workout pieces (see `docs/GUIDE.md`, "Capacity arithmetic"). Regenerate with `python -m training.simulate`. See `docs/AUDIT.md` for which constants this can and can't validate.

- Dictionary version: 10
- Period: 2026-08-30 to 2026-10-28 (26 sessions)
- Total pieces logged: 130
- Distinct exercises used: 31 / 31 pattern-bearing exercises in the dictionary

## Final EWMA scores per (pattern, style) cell

score = -log(ewma / MEMORY_K). 0 = exactly on target, positive = behind (grows without bound as neglect continues; `inf` means never trained at that style at all), negative = ahead. Sorted worst (most behind) first.

| pattern | style | score |
|---|---|---:|
| push | heavy | -0.04 |
| brace | heavy | -0.05 |
| pull | heavy | -0.05 |
| hinge | light | -0.15 |
| squat | heavy | -0.25 |
| hinge | heavy | -0.29 |
| hinge | hhr | -0.36 |
| pull | hhr | -0.39 |
| squat | hhr | -0.40 |
| push | light | -0.53 |
| brace | hhr | -0.58 |
| flexion | light | -0.58 |
| squat | light | -0.58 |
| push | hhr | -0.59 |
| pull | light | -0.63 |
| engine | light | -0.76 |

## Longest gaps at end of simulation

No hard overdue clock exists any more - a neglected cell's score grows without bound on its own (see `docs/GUIDE.md`'s Scoring section), but nothing forces a specific cell above the others by a fixed date. This table is the sanity check for that removal: days since each admissible cell was literally last trained, as of the end of the run. Sorted worst first - if anything here looks uncomfortably large, that's the signal the clock used to suppress by construction rather than by ranking.

| pattern | style | days since last touched |
|---|---|---:|
| brace | heavy | 9 |
| pull | hhr | 7 |
| push | heavy | 7 |
| squat | hhr | 7 |
| brace | hhr | 5 |
| hinge | hhr | 5 |
| pull | heavy | 5 |
| push | hhr | 5 |
| flexion | light | 2 |
| hinge | light | 2 |
| squat | heavy | 2 |
| engine | light | 0 |
| hinge | heavy | 0 |
| pull | light | 0 |
| push | light | 0 |
| squat | light | 0 |

## Movement variety

Top 10 most-picked exercises:

| exercise | times picked |
|---|---:|
| Toes-to-bar | 14 |
| Air squat | 13 |
| Push-up | 8 |
| Kettlebell swing | 7 |
| Devil's press | 7 |
| Ring row | 6 |
| Kipping pull-up | 6 |
| Suitcase carry | 5 |
| Deadlift | 5 |
| Farmer's carry | 5 |

## Weekly recommend() verdicts

| Monday | style | band | margin |
|---|---|---|---:|
| 2026-08-31 | - | no strong signal | 0.0 |
| 2026-09-07 | - | no strong signal | 0.0 |
| 2026-09-14 | - | no strong signal | 0.2 |
| 2026-09-21 | - | no strong signal | 0.1 |
| 2026-09-28 | - | no strong signal | 0.0 |
| 2026-10-05 | - | no strong signal | 0.0 |
| 2026-10-12 | - | no strong signal | 0.0 |
| 2026-10-19 | - | no strong signal | 0.0 |
| 2026-10-26 | - | no strong signal | 0.0 |


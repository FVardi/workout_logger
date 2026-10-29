# Training advisor - two-month synthetic simulation report

Not real data. A closed-loop check: the simulated trainee always follows `advise()`'s top pick, three sessions/week, one heavy piece then four workout pieces (see `docs/GUIDE.md`, "Capacity arithmetic"). Regenerate with `python -m training.simulate`. See `docs/AUDIT.md` for which constants this can and can't validate.

- Dictionary version: 10
- Period: 2026-07-10 to 2026-09-07 (26 sessions)
- Total pieces logged: 130
- Distinct exercises used: 19 / 19 pattern-bearing exercises in the dictionary

## Final EWMA scores per (pattern, style) cell

score = -log(ewma / MEMORY_K). 0 = exactly on target, positive = behind (grows without bound as neglect continues; `inf` means never trained at that style at all), negative = ahead. Sorted worst (most behind) first.

| pattern | style | score |
|---|---|---:|
| hinge | heavy | +0.08 |
| brace | heavy | -0.05 |
| squat | heavy | -0.12 |
| push | heavy | -0.24 |
| pull | heavy | -0.31 |
| push | hhr | -0.41 |
| squat | hhr | -0.43 |
| push | light | -0.53 |
| hinge | hhr | -0.55 |
| brace | hhr | -0.65 |
| engine | light | -0.67 |
| squat | light | -0.67 |
| pull | hhr | -0.68 |
| hinge | light | -0.80 |
| flexion | light | -0.83 |

## Longest gaps at end of simulation

No hard overdue clock exists any more - a neglected cell's score grows without bound on its own (see `docs/GUIDE.md`'s Scoring section), but nothing forces a specific cell above the others by a fixed date. This table is the sanity check for that removal: days since each admissible cell was literally last trained, as of the end of the run. Sorted worst first - if anything here looks uncomfortably large, that's the signal the clock used to suppress by construction rather than by ranking.

| pattern | style | days since last touched |
|---|---|---:|
| squat | hhr | 14 |
| hinge | heavy | 12 |
| push | hhr | 12 |
| brace | heavy | 10 |
| brace | hhr | 7 |
| hinge | hhr | 7 |
| pull | hhr | 5 |
| squat | heavy | 5 |
| engine | light | 3 |
| push | heavy | 3 |
| flexion | light | 0 |
| hinge | light | 0 |
| pull | heavy | 0 |
| push | light | 0 |
| squat | light | 0 |

## Movement variety

Top 10 most-picked exercises:

| exercise | times picked |
|---|---:|
| Kettlebell swing | 17 |
| Toes-to-bar | 16 |
| Push-up | 13 |
| Air squat | 13 |
| Farmer's carry | 12 |
| Deadlift | 9 |
| Strict pull-up | 6 |
| Bent-over row | 6 |
| Strict press | 5 |
| Running | 4 |

## Weekly recommend() verdicts

| Monday | style | band | margin |
|---|---|---|---:|
| 2026-07-13 | heavy | leaning | 0.5 |
| 2026-07-20 | - | no strong signal | 0.2 |
| 2026-07-27 | - | no strong signal | 0.1 |
| 2026-08-03 | - | no strong signal | 0.0 |
| 2026-08-10 | - | no strong signal | 0.0 |
| 2026-08-17 | - | no strong signal | 0.0 |
| 2026-08-24 | - | no strong signal | 0.0 |
| 2026-08-31 | - | no strong signal | 0.0 |
| 2026-09-07 | - | no strong signal | 0.0 |


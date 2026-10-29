# ROADMAP — future work, not yet scoped or started

Pointers and the reasoning behind them, not specs. Once something here is
scoped and started, its spec moves to `docs/GUIDE.md` and it's removed
from this file. Moved over from `math_blog`'s `docs/ideas/ROADMAP.md`;
the first item was written before the switch to log-EWMA scoring, so its
"deficit" wording predates the current model.

- **Movement-level volume targets.** Currently there are no per-movement
  targets, only a last-seen date, with the deficit aggregating to
  pattern × style. Adding them is additive (the log already stores
  per-exercise rows) but needs: a unit decision, a cap so a ramping
  target doesn't dominate every list permanently, an explicit end to the
  emphasis, and reinstating the **anchor vs. rotator** distinction — a
  movement being deliberately built should be exempt from
  staleness-driven rotation. Emphasis must be **zero-sum** (targets on
  the simplex — "more squatting" is a redistribution, not an addition),
  or load quietly accumulates everywhere.
- **Second-best option per cell.** Currently each cell contributes only
  its stalest variant; showing two or three per cell would suit how much
  judgment the human is keeping. Noted as "probably worth changing" in
  `docs/GUIDE.md` but not scoped.

"""Translates a user's friendly settings into a concrete advisor.AdvisorConfig.

advisor.py only knows how to score against a fully-specified config - it
has no opinion about what "more conditioning focus" or "no heavies" should
mean numerically (see AdvisorConfig's docstring). That mapping lives here
instead, so advisor.py stays a pure scoring model and this module is the
one place UX-friendly knobs get turned into numbers.

Current knobs (db.UserSettings), and what each does:

- `volume_multiplier` - scales every style's target_rate uniformly. 1.0 is
  the default rate; 0.5 asks for everything half as often; 1.5 half again
  as often. Deliberately uniform across styles rather than per-style, on
  the theory that "I want to train more/less overall" is a different
  question from "I want more heavy relative to light" - the latter isn't
  exposed yet (see docs/STATUS.md's webapp section).
- `conditioning_focus` - scales PATTERN_WEIGHT["engine"] specifically.
  Above 1.0 makes engine work (running/rowing/burpee/etc.) surface more
  often relative to everything else; below 1.0 makes it surface less.
  Only touches `engine`, not `hhr` generally, since hhr already spans
  every pattern and isn't itself a "conditioning" knob the way engine is.
- `disabled_styles` - styles excluded outright (e.g. ["heavy"] for
  "no heavies"). Passed straight through to AdvisorConfig.disabled_styles.
"""

from __future__ import annotations

from . import advisor, db


def build_config(settings: db.UserSettings) -> advisor.AdvisorConfig:
    target_rate = {
        style: rate * settings.volume_multiplier
        for style, rate in advisor.TARGET_RATE.items()
    }
    pattern_weight = dict(advisor.PATTERN_WEIGHT)
    if "engine" in pattern_weight:
        pattern_weight["engine"] *= settings.conditioning_focus

    return advisor.AdvisorConfig(
        target_rate=target_rate,
        pattern_weight=pattern_weight,
        hhr_credit=advisor.HHR_CREDIT,
        memory_k=advisor.MEMORY_K,
        disabled_styles=frozenset(settings.disabled_styles),
    )

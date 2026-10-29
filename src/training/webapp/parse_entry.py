"""LLM-assisted logging: a free-text note or a photo -> draft pieces that
pre-fill the ordinary log form.

The parser never writes to the log. It produces drafts in the log form's own
terms (template, movement rows, styles); the person checks each one in the
form they'd otherwise fill by hand and presses Log, so every piece still goes
through `/log`'s validation and `session_templates`' expansion.

Three rules the prompt and the code both enforce:

- **Unknown exercises are flagged, never guessed.** The dictionary is
  authored and grown only on contact with real logging (CLAUDE.md), so a
  movement that isn't in it comes back as `unresolved_name`: the row is left
  blank for the person to fill or drop, and the name is kept in the parse
  run as evidence of what's missing.
- **Style is proposed, not decided.** The parser suggests heavy/hhr/light
  from context; it lands as a pre-selected value the person can change, the
  same status the form's own load-based guess has. A proposal the exercise
  doesn't admit falls back to the form's usual default and is flagged.
- **Nothing is invented.** A load, rep count or score that isn't in the
  note stays empty and is listed under "check these".

Not part of scoring in any way - the model still reads only what's logged.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date

PROMPT_VERSION = "1"
DEFAULT_MODEL = "claude-haiku-5-5"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
TEMPLATES = ("strength", "rounds", "ladder", "emom", "amrap")
UNITS = ("reps", "m", "s")


def backend() -> str | None:
    """Which parser is configured: 'anthropic', 'fake' (tests/local
    development), or None when LLM logging is switched off."""
    explicit = os.environ.get("TRAINING_PARSER")
    if explicit in ("anthropic", "fake"):
        return explicit
    if explicit == "off":
        return None
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else None


# --- What the model is asked to produce -------------------------------------------

def llm_schema(exercise_ids: list[str], styles: tuple[str, ...]) -> dict:
    """JSON schema for structured outputs. Kept within the subset the API
    accepts (no numeric bounds, no oneOf/if-then, additionalProperties false
    on every object), so checks like "admissible style for this exercise"
    happen afterwards in `draft_to_prefill`."""
    exercise = {"type": "string", "enum": sorted(exercise_ids)}
    style = {"type": "string", "enum": list(styles)}
    num = {"type": "number"}
    integer = {"type": "integer"}
    movement = {
        "type": "object", "additionalProperties": False,
        "required": ["style"],
        "properties": {
            "exercise_id": exercise,
            "unresolved_name": {"type": "string"},
            "style": style,
            "amount": num,
            "unit": {"type": "string", "enum": list(UNITS)},
            "load": num,
        },
    }
    strength_set = {
        "type": "object", "additionalProperties": False, "required": ["reps"],
        "properties": {"reps": integer, "load": num},
    }
    piece = {
        "type": "object", "additionalProperties": False,
        "required": ["template"],
        "properties": {
            "template": {"type": "string", "enum": list(TEMPLATES)},
            # strength
            "exercise_id": exercise,
            "unresolved_name": {"type": "string"},
            "style": style,
            "scheme": {"type": "string"},
            "sets": {"type": "array", "items": strength_set},
            # rounds / ladder / emom / amrap
            "movements": {"type": "array", "items": movement},
            "rounds": integer,
            "time_cap_minutes": integer,
            "rep_scheme": {"type": "string"},
            "ladder_rounds": integer,
            "ladder_every_minutes": integer,
            "total_minutes": integer,
            "every_minutes": integer,
            "rounds_completed": integer,
            "partial_reps": integer,
            "result_time": {"type": "string"},
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["pieces", "uncertain"],
        "properties": {
            "date": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
            "pieces": {"type": "array", "items": piece},
            "uncertain": {"type": "array", "items": {"type": "string"}},
        },
    }


def system_prompt(dictionary) -> str:
    rows = "\n".join(
        f"- {ex.id}: {ex.name} ({ex.implement}, {ex.laterality}; styles: {', '.join(ex.admissible_styles)})"
        for ex in sorted(dictionary.exercises.values(), key=lambda e: e.id)
    )
    return f"""You turn a training-log note (typed, dictated, or a photo of a whiteboard or notebook) into pieces for a logging form. Notes may be English or Danish, terse, abbreviated or misspelled.

Record only what the person did. Never invent a number: if a load, rep count, round count or score isn't stated, leave the field out and add a short line to `uncertain`.

PIECES. A session can contain several pieces (e.g. strength work, then a workout). Each piece uses one template:
- strength: one movement, one entry per set actually done. "5x5 @ 100" is five sets {{reps 5, load 100}}; a ramp "80, 90, 100 x5" is three sets. `scheme` is a short label like "5x5" when the note gives one.
- rounds: `rounds` passes through the movement list, for time. A single long effort (a 10 km run, a chipper) is rounds 1. Put a stated time cap in `time_cap_minutes`.
- ladder: a rep scheme like 21-15-9 applied to every movement (`rep_scheme` "21-15-9"); movement rows then need no amount. `ladder_rounds` repeats the whole ladder, `ladder_every_minutes` if it restarts on a clock.
- emom: movements ALTERNATE, one per interval: `total_minutes` and `every_minutes`. "EMOM 12: odd 10 thrusters, even 12 burpees" is total 12, every 1, two movements. If every movement is done in every interval, use rounds instead.
- amrap: `time_cap_minutes` is the AMRAP length; `rounds_completed` full rounds and `partial_reps` reps into the next round ("7+8" is 7 and 8).
- `result_time` is how long the piece took, as "mm:ss" (a score of 9:42, or a run's duration). Only if stated.
Heavy work done during a long run is two pieces: the run, and the heavy work.

MOVEMENT ROWS (rounds/ladder/emom/amrap): one row per movement per round, with `amount` + `unit`: reps, m (metres; convert km), or s (seconds). Rest between efforts is a row with exercise_id "rest", unit s. Calories have no unit here: put the calories in `amount` with unit reps and add a line to `uncertain` saying calories were logged as reps.

LOADS are in kg, per implement (two 22.5 kg dumbbells is load 22.5). Convert pounds to kg rounded to 0.5 and say so in `uncertain`. Bodyweight movements have no load.

UNILATERAL exercises are logged per side: "5/side", "5 each leg" is amount 5.

STYLE, for every movement and strength piece, from the exercise's allowed styles:
- heavy: low reps, fresh, long rests - dedicated strength work.
- hhr ("loaded, breathing hard"): a loaded movement at reduced load inside conditioning work (a metcon, an EMOM).
- light: bodyweight or light work.
If unsure, choose the more likely one and add a line to `uncertain`.

EXERCISES. Use exercise_id from the dictionary below; match abbreviations (T2B = toes_to_bar, KBS = kb_swing, DU = double_unders, HSPU = hspu, C&J etc.). If nothing clearly matches - including a variant the dictionary doesn't have - use `unresolved_name` with the movement as written. Never substitute a similar exercise.

`date`: the training date (YYYY-MM-DD) if the note states one, else null.

Dictionary:
{rows}"""


@dataclass
class ParseResult:
    ok: bool
    draft: dict | None = None        # {"date", "pieces", "uncertain"}
    error: str | None = None
    model: str = ""
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def parse(text: str, image: tuple[bytes, str] | None, dictionary, today: date) -> ParseResult:
    kind = backend()
    if kind == "fake":
        return _fake_parse(text)
    if kind is None:
        return ParseResult(ok=False, error="LLM logging isn't configured on this server.", model="none")
    return _anthropic_parse(text, image, dictionary, today)


def _fake_parse(text: str) -> ParseResult:
    """Stand-in for local development without an API key: an empty
    strength piece that keeps the note, so the flow can be clicked through."""
    return ParseResult(ok=True, model="fake", draft={
        "date": None,
        "pieces": [{"template": "strength", "unresolved_name": text.strip()[:60] or "?", "style": "heavy",
                    "sets": [{"reps": 5}]}],
        "uncertain": ["No parser configured (TRAINING_PARSER=fake): fill in the form by hand."],
    })


def _anthropic_parse(text: str, image: tuple[bytes, str] | None, dictionary, today: date) -> ParseResult:
    import anthropic

    model = os.environ.get("TRAINING_PARSER_MODEL", DEFAULT_MODEL)
    content: list[dict] = []
    if image is not None:
        data, media_type = image
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                    "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text":
                    f"Today is {today.isoformat()}.\n\n<note>\n{text.strip() or '(see the image)'}\n</note>"})
    started = time.monotonic()
    try:
        response = anthropic.Anthropic().messages.create(
            model=model,
            max_tokens=16000,
            system=system_prompt(dictionary),
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema",
                                      "schema": llm_schema(list(dictionary.exercises), dictionary.styles)}},
        )
    except anthropic.APIError as exc:
        return ParseResult(ok=False, error=f"{type(exc).__name__}: {exc}", model=model,
                           latency_ms=int((time.monotonic() - started) * 1000))
    latency = int((time.monotonic() - started) * 1000)
    usage = getattr(response, "usage", None)
    meta = dict(model=model, latency_ms=latency,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None))
    texts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    if response.stop_reason == "max_tokens" or not texts:
        return ParseResult(ok=False, error=f"No complete answer (stop_reason={response.stop_reason}).", **meta)
    try:
        draft = json.loads("".join(texts))
    except json.JSONDecodeError as exc:
        return ParseResult(ok=False, error=f"Model returned invalid JSON: {exc}", **meta)
    draft.setdefault("uncertain", [])
    draft.setdefault("pieces", [])
    if not draft["pieces"]:
        return ParseResult(ok=False, error="Couldn't find anything to log in that note.", draft=draft, **meta)
    return ParseResult(ok=True, draft=draft, **meta)


# --- Draft piece -> log form fields ------------------------------------------------

# The form's own defaults (log_session.html), used where the draft says
# nothing, so a pre-filled form shows exactly what an untouched one would.
FORM_DEFAULTS = {
    "rounds": {"rounds": "3"},
    "ladder": {"rep_scheme": "21-15-9", "ladder_rounds": "1"},
    "emom": {"total_minutes": "12", "every_minutes": "1"},
    "amrap": {"time_cap_minutes": "12", "rounds_completed": "0"},
}
_SCALARS = {
    "strength": ("scheme",),
    "rounds": ("rounds", "time_cap_minutes"),
    "ladder": ("rep_scheme", "ladder_rounds", "ladder_every_minutes", "time_cap_minutes"),
    "emom": ("total_minutes", "every_minutes"),
    "amrap": ("time_cap_minutes", "rounds_completed", "partial_reps"),
}
REST_ID = "rest"


def _num(value) -> str:
    if value is None or value == "":
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _load(value) -> str:
    """Loads go into a step=0.5 input, so round to the nearest half kilo."""
    if value is None or value == "":
        return ""
    return _num(round(float(value) * 2) / 2)


@dataclass
class Prefill:
    """What the log form should show for one draft piece."""
    template: str
    fields: dict[str, str]
    unknown: dict[str, str] = field(default_factory=dict)   # field prefix -> name as written
    notes: list[str] = field(default_factory=list)          # per-piece "check this" lines

    def to_dict(self) -> dict:
        return {"template": self.template, "fields": self.fields, "unknown": self.unknown, "notes": self.notes}


def draft_to_prefill(piece: dict, dictionary, default_style, on_date: str) -> Prefill:
    """Map one draft piece onto log_session.html's field names.

    `default_style(exercise_id)` is the form's own default (the only
    admissible style, or the one last logged), used when the draft's style
    proposal isn't admissible for that exercise.
    """
    template = piece.get("template") if piece.get("template") in TEMPLATES else "rounds"
    fields: dict[str, str] = {"template": template, "date": on_date,
                              "result_time": (piece.get("result_time") or "").strip()}
    fields.update(FORM_DEFAULTS.get(template, {}))
    prefill = Prefill(template=template, fields=fields)

    def resolve(item: dict, where: str, prefix: str) -> tuple[str, str]:
        ex_id = item.get("exercise_id") or ""
        if ex_id not in dictionary.exercises:
            name = (item.get("unresolved_name") or ex_id or "").strip() or "an unnamed movement"
            prefill.unknown[prefix] = name
            return "", ""
        proposed = item.get("style") or ""
        admissible = dictionary.exercises[ex_id].admissible_styles
        if proposed in admissible:
            return ex_id, proposed
        fallback = default_style(ex_id)
        if proposed:
            prefill.notes.append(
                f"{where}: {dictionary.exercises[ex_id].name} can't be logged as {proposed}; set to {fallback}.")
        return ex_id, fallback

    for key in _SCALARS[template]:
        if piece.get(key) not in (None, ""):
            fields[key] = _num(piece[key]) if key not in ("scheme", "rep_scheme") else str(piece[key]).strip()

    if template == "strength":
        ex_id, style = resolve(piece, "Movement", "main")
        fields["exercise_id"], fields["style"] = ex_id, style
        for i, s in enumerate(piece.get("sets") or []):
            fields[f"s{i}_reps"] = _num(s.get("reps"))
            fields[f"s{i}_load"] = _load(s.get("load"))
        return prefill

    with_amount = template != "ladder"
    previous_style = ""
    for i, mv in enumerate(piece.get("movements") or []):
        ex_id, style = resolve(mv, f"Row {i + 1}", f"m{i}")
        if ex_id == REST_ID and previous_style:
            style = previous_style       # rest sits in the movement before it, as /log does
        previous_style = style or previous_style
        fields[f"m{i}_exercise_id"] = ex_id
        fields[f"m{i}_style"] = style
        fields[f"m{i}_load"] = _load(mv.get("load"))
        if with_amount:
            fields[f"m{i}_amount"] = _num(mv.get("amount"))
            fields[f"m{i}_unit"] = mv.get("unit") if mv.get("unit") in UNITS else "reps"
    return prefill


def comparable(fields: dict) -> dict:
    """A form submission reduced to what was actually entered, so a
    pre-filled form and the submitted one can be compared: blank rows,
    bookkeeping fields and number formatting are dropped."""
    skip = {"parse_run_id", "piece_index"}
    out = {}
    blank_rows = set()
    for key, value in fields.items():
        if key.endswith("_exercise_id") and key.startswith("m") and not str(value).strip():
            blank_rows.add(key[: -len("exercise_id")])
    for i in range(100):
        if not str(fields.get(f"s{i}_reps", "")).strip() and not str(fields.get(f"s{i}_load", "")).strip():
            blank_rows.add(f"s{i}_")
    for key, value in fields.items():
        if key in skip or any(key.startswith(p) for p in blank_rows):
            continue
        value = str(value).strip()
        if not value:
            continue
        out[key] = _num(value) if value.replace(".", "", 1).isdigit() else value
    return out

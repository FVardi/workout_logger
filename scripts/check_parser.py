"""Send a few typical notes to the real parser and show what the log form
would be pre-filled with. Run this once with an API key before inviting
anyone, and again after changing the prompt:

    set ANTHROPIC_API_KEY=sk-ant-...        (Windows cmd; `export` elsewhere)
    python scripts/check_parser.py

Costs a fraction of a cent per note. Nothing is written to any database.
"""

import json
import os
import sys
from datetime import date

os.environ.setdefault("TRAINING_PARSER", "anthropic")

from training import advisor  # noqa: E402
from training.webapp import parse_entry  # noqa: E402

NOTES = [
    "back squat 80 90 100 100 100 x5",
    "3 rft: 15 cal bike, 12 kbs 24kg, 9 t2b. 9:42",
    "AMRAP 12: 5 strict pull-ups, 10 push-ups, 15 air squats. 7+8",
    "21-15-9 thrusters 43 kg og pull-ups, cap 10 min",
    "EMOM 12: ulige 10 burpees, lige 5 db snatch 22.5 kg pr side",
    "Z2 løb 10 km på 60 min",
    "worked on zercher carries, 4x40m heavy",
]


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first.")
    dictionary = advisor.load_dictionary()

    def default_style(ex_id):
        return dictionary.exercises[ex_id].admissible_styles[0]

    for note in NOTES:
        print("=" * 72)
        print(note)
        result = parse_entry.parse(note, None, dictionary, date.today())
        print(f"  {result.model}, {result.latency_ms} ms, tokens {result.input_tokens} in / {result.output_tokens} out")
        if not result.ok:
            print("  FAILED:", result.error)
            continue
        for line in result.draft.get("uncertain") or []:
            print("  check:", line)
        for i, piece in enumerate(result.draft["pieces"]):
            prefill = parse_entry.draft_to_prefill(piece, dictionary, default_style, date.today().isoformat())
            shown = {k: v for k, v in prefill.fields.items() if v and k not in ("date",)}
            print(f"  piece {i + 1}: {json.dumps(shown, ensure_ascii=False)}")
            for where, name in prefill.unknown.items():
                print(f"    not in dictionary ({where}): {name}")
            for line in prefill.notes:
                print("    note:", line)


if __name__ == "__main__":
    main()

"""Alternative one-time prep for the trivia game, for a kids' crowd:
generates original, family-friendly Christmas/holiday trivia with Gemini
instead of filtering the Jeopardy! clue dataset trivia_data.py uses.

Writes to the exact same trivia/christmas_clues.json and trivia/audio/
that trivia_data.py does, so this is a drop-in swap -- trivia_game.py
doesn't change either way, and running trivia_data.py again later
switches back to the adult bank.

There's no excluded_ids.json-style review step here -- these questions
aren't pulled from a vetted dataset, they're freshly invented each run,
so give trivia/christmas_clues.json a skim for accuracy/appropriateness
before the party rather than assuming it's already checked. There's also
no --batch equivalent: just re-run the script for an entirely new set,
since nothing here is seeded for reproducibility.

Run directly, once, well ahead of a kids' party:

    .venv/bin/python trivia_kids.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict

from dotenv import load_dotenv
from google import genai
from google.genai import types

import config
from trivia_data import AUDIO_DIR, OUTPUT_PATH, Clue, ensure_audio_cache

# override=True: see trivia_game.py's identical note - must run before
# _GEMINI_CLIENT below, since that's a module-level singleton that would
# otherwise construct itself before GEMINI_API_KEY was ever loaded. Also
# matters here specifically because the Pi runs a separate project whose
# shell config exports its own ELEVENLABS_API_KEY.
load_dotenv(override=True)

CLUE_COUNT = 24
"""Split evenly across the two rounds (see _generation_prompt()) - this
is a one-night event, not a standing feature, same reasoning as
trivia_data.CLUE_COUNT."""

POINTS_BY_ROUND = {1: 1, 2: 2}

_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.ARRAY,
    items=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "category": types.Schema(type=types.Type.STRING),
            "clue": types.Schema(type=types.Type.STRING),
            "response": types.Schema(type=types.Type.STRING),
            "round": types.Schema(type=types.Type.INTEGER),
        },
        required=["category", "clue", "response", "round"],
    ),
)
"""An explicit schema, not just response_mime_type="application/json" plus
a prompt-described format - confirmed necessary by hitting a real
JSONDecodeError live: a clue quoting a song lyric (e.g. a title with its
own internal quotation marks) came back with that inner quote
unescaped, breaking plain JSON-mode parsing. response_schema makes the
SDK constrain generation via the model's own controlled decoding, which
can't emit a syntactically-invalid string field at all - judge_answer()/
generate_finale() in trivia_game.py don't need this since their banter
text is far less likely to quote something that itself contains a
quotation mark."""

_GEMINI_CLIENT = genai.Client()
_GENERATION_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=_RESPONSE_SCHEMA,
    temperature=0.8,
    max_output_tokens=4000,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
"""max_output_tokens is far larger than config.GEMINI_MAX_OUTPUT_TOKENS
(300, tuned for a single short conversational reply) - this one call
returns a whole JSON array of CLUE_COUNT questions at once. temperature
leaves room for variety across categories without drifting into made-up
or ambiguous facts."""


def _generation_prompt(count: int) -> str:
    easy = count // 2
    hard = count - easy
    return f"""
Generate {count} original Christmas and holiday trivia questions for a live
trivia game, for a family audience including kids around ages 6-10.

Topics should be fun, family-friendly Christmas/holiday things: Santa Claus,
reindeer, presents, stockings, snowmen, elves, Christmas trees, cookies,
carols, winter activities, and classic Christmas movies/stories kids would
know, and similar. Avoid anything scary, violent, religiously controversial,
or otherwise inappropriate for young children.

Split them into two difficulty tiers: the first {easy} should be easy,
simple factual questions a young kid could answer (round 1); the next
{hard} should be a bit more challenging, still kid-appropriate but
requiring more specific knowledge (round 2).

For each question, provide:
- "category": a short, punchy, ALL-CAPS Jeopardy-style category name (like
  "SANTA'S WORKSHOP" or "REINDEER ROUNDUP") - vary these across the set,
  don't reuse the same category name twice.
- "clue": the question phrased as a statement, not a question, Jeopardy
  style (e.g. "This red-nosed reindeer leads Santa's sleigh." not "What
  reindeer has a red nose?") - a complete sentence, read aloud to the group.
- "response": the correct answer, as a short phrase.
- "round": exactly 1 for the first {easy} (easy) questions, exactly 2 for
  the remaining {hard} (harder) questions.

Make sure every question is factually correct and has one clear,
unambiguous answer - nothing a reasonable kid could argue two different
ways.

Critically important: never give away the answer in the category name or
the clue text itself. The category and clue must describe the answer
through facts, hints, or description only - never state the answer, or
any word from it, outright. For example, if the answer is "Rudolph",
neither the category nor the clue may contain the word "Rudolph"
anywhere; if the answer is "Frosty the Snowman", neither may contain
"Frosty". Before writing each question, check that you have not done
this.

Return ONLY a valid JSON array in this exact format, with no extra text:
[{{"category": "...", "clue": "...", "response": "...", "round": 1}}, ...]
"""


_STOPWORDS = frozenset(
    {"a", "an", "the", "of", "to", "in", "on", "is", "are", "was", "were", "this", "these", "and"}
)


def _leaks_answer(category: str, clue: str, response: str) -> bool:
    """Whether a significant word from ``response`` appears, as a whole
    word, literally inside ``category`` or ``clue`` - catches the
    generated question simply stating (part of) its own answer, despite
    _generation_prompt()'s explicit instruction not to (confirmed by hand
    that Gemini doesn't always follow that instruction).

    Checks each significant word of the answer individually (skipping
    short connector words via _STOPWORDS) rather than the whole phrase as
    one unit, so e.g. response "Kevin McCallister" is still caught by a
    clue that only names "Kevin". Matches on whole words only (\\b...\\b),
    not a bare substring check, so response "Elf" doesn't false-positive
    against clue text containing "shelf" or "itself"."""
    haystack = f"{category} {clue}"
    for word in re.findall(r"[A-Za-z']+", response):
        if (len(word) <= 2) or (word.lower() in _STOPWORDS):
            continue
        if re.search(r"\b" + re.escape(word) + r"\b", haystack, re.IGNORECASE):
            return True
    return False


def _clue_id(category: str, clue: str) -> str:
    """Same content-derived id scheme as trivia_data._clue_id() -- not
    reproducible across runs here (there's no --batch/excluded_ids.json
    mechanism to keep stable across re-runs), just needed so each clue
    gets its own distinct audio fixture path."""
    digest = hashlib.sha256(f"{category}\x1f{clue}".encode()).hexdigest()
    return digest[:12]


def generate_clues(count: int) -> list[Clue]:
    """Ask Gemini for kid-friendly Christmas trivia questions and turn
    the result into exactly ``count`` game-ready Clues (split evenly
    across the two rounds, same as _generation_prompt()'s own split).

    Requests more than ``count`` up front and drops any that
    _leaks_answer() flags, since _generation_prompt()'s own instruction
    not to give away the answer isn't followed 100% of the time -
    confirmed by hand. Raises RuntimeError (telling the caller to just
    re-run the script - nothing here is seeded for reproducibility
    anyway) if too many get dropped to fill either round.
    """
    buffer = max(4, count // 3)
    requested = count + buffer
    prompt = _generation_prompt(requested)
    response = _GEMINI_CLIENT.models.generate_content(
        model=config.GEMINI_QUALITY_MODEL, contents=prompt, config=_GENERATION_CONFIG
    )
    if response.text is None:
        raise RuntimeError("Gemini returned no text for kids trivia generation")
    raw = json.loads(response.text)

    easy_target = count // 2
    hard_target = count - easy_target
    easy: list[Clue] = []
    hard: list[Clue] = []
    dropped = 0
    for row in raw:
        category = str(row["category"]).strip()
        clue_text = str(row["clue"]).strip()
        response_text = str(row["response"]).strip()
        round_num = int(row["round"])
        if _leaks_answer(category, clue_text, response_text):
            dropped += 1
            continue
        clue = Clue(
            _clue_id(category, clue_text), category, clue_text, response_text,
            POINTS_BY_ROUND[round_num], round_num,
        )
        if (round_num == 1) and (len(easy) < easy_target):
            easy.append(clue)
        elif (round_num == 2) and (len(hard) < hard_target):
            hard.append(clue)
    if dropped:
        print(f"Dropped {dropped} generated question(s) that gave away their own answer.")
    if (len(easy) < easy_target) or (len(hard) < hard_target):
        raise RuntimeError(
            f"Only got {len(easy)}/{easy_target} easy and {len(hard)}/{hard_target} hard "
            "usable questions after filtering out ones that leaked their own answer - "
            "just re-run the script for a fresh batch."
        )
    return easy + hard


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate original, kid-friendly Christmas trivia with Gemini "
        "instead of filtering the Jeopardy! dataset - a drop-in swap for "
        "trivia_data.py's output."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=CLUE_COUNT,
        help=f"How many questions to generate (default: {CLUE_COUNT}, split evenly "
        "across the two rounds).",
    )
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="Only (re)write trivia/christmas_clues.json; skip ElevenLabs "
        "synthesis. Useful while tuning the generation prompt.",
    )
    args = parser.parse_args()

    clues = generate_clues(args.count)

    if not args.skip_audio:
        ensure_audio_cache(clues)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps([asdict(c) for c in clues], indent=2) + "\n")
    print(f"{len(clues)} kid-friendly clues written to {OUTPUT_PATH}.")
    print("These are freshly generated, not pulled from a vetted dataset - give "
          f"{OUTPUT_PATH} a skim for accuracy and appropriateness before the party.")
    print(f"(Audio fixtures reused from {AUDIO_DIR} where a clue's wording happens "
          "to repeat across runs, synthesized fresh otherwise.)")


if __name__ == "__main__":
    main()

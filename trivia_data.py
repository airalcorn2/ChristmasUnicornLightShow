"""One-time prep for the trivia game: download a public Jeopardy! clue
dataset (https://github.com/jwolle1/jeopardy_clue_dataset -- its own
maintainer prefers not to credit where the clues were sourced from),
filter it down to Christmas/holiday-adjacent clues, and pre-synthesize
every surviving clue's audio.

Never run during a live round -- trivia_game.py only ever reads the
output of this script (trivia/christmas_clues.json) and the audio
fixture cache it fills (trivia/audio/), so the live game never waits on
a network call for clue text/audio (judge_answer()/generate_finale()
still do, live, for the parts that have to be -- see trivia_game.py's
own module docstring).

Run directly, once, well ahead of a party:

    .venv/bin/python trivia_data.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

import voice

CLUE_COUNT = 30
"""This is a one-night event, not a standing feature -- no need to carry
the full filtered pool (likely well under the ~1,300 clues cauldron's
horror filter found, since Christmas-themed Jeopardy categories are
rarer than horror ones). CLUE_COUNT samples down to a manageable set;
SAMPLE_SEED keeps that sample stable across re-runs of this script, so
re-running it (e.g., after adding to excluded_ids.json) doesn't reshuffle
the selection and throw away already-synthesized audio. To get a whole
new set of questions for another night, leave this alone and pass a new
--batch number instead -- see select_clues()."""
SAMPLE_SEED = 0
"""See CLUE_COUNT above."""

DATASET_VERSION = 42
"""The dataset release to pull -- bump this to pick up newer seasons; it
drives every filename/URL below, all of which encode it the dataset's
own two different ways ("v42" for the release tag, "1-42" for the
archive/file names)."""
_ARCHIVE_NAME = f"jeopardy_dataset_seasons_1-{DATASET_VERSION}"

DATASET_URL = (
    "https://github.com/jwolle1/jeopardy_clue_dataset/releases/download/"
    f"v{DATASET_VERSION}/{_ARCHIVE_NAME}.zip"
)
TSV_MEMBER = f"{_ARCHIVE_NAME}/combined_season1-{DATASET_VERSION}.tsv"

TRIVIA_DIR = Path("trivia")
RAW_ZIP_PATH = TRIVIA_DIR / "raw" / f"{_ARCHIVE_NAME}.zip"
TSV_PATH = TRIVIA_DIR / "raw" / f"combined_season1-{DATASET_VERSION}.tsv"
OUTPUT_PATH = TRIVIA_DIR / "christmas_clues.json"
EXCLUDED_IDS_PATH = TRIVIA_DIR / "excluded_ids.json"
"""Hand-reviewed escape hatch: clue ids to drop even though they matched
the keyword filter (e.g., a category that uses a Christmas word as a pun
on something unrelated, the filter can't tell apart from a real one). A
plain JSON list of ids; starts out not existing, which is the same as an
empty list."""

AUDIO_DIR = Path("trivia") / "audio"
"""Per-clue voice.save_fixture() caches (full ElevenLabs timestamped
payload, not just an mp3) -- see ensure_audio_cache(). Clue text is
spoken plain, with no embedded effect tags: voice.save_fixture()/
load_fixture() strip/never-restore effect cues (confirmed by reading
voice.py), so a tag in clue text would be silently dropped on the way
in and never usable on the way back out. Any lighting while a clue is
read comes from trivia_lights.py's idle loop instead."""

INCLUDE_KEYWORDS = (
    "CHRISTMAS",
    "SANTA",
    "REINDEER",
    "SLEIGH",
    "MISTLETOE",
    "STOCKING STUFFER",
    "NUTCRACKER",
    "NORTH POLE",
    "FROSTY THE SNOWMAN",
    "SNOWMAN",
    "GINGERBREAD",
    "YULETIDE",
    "YULE LOG",
    "ADVENT CALENDAR",
    "NATIVITY",
    "CHRISTMAS CAROL",
    "CAROLER",
    "CAROLING",
    "NOEL",
    "SCROOGE",
    "GRINCH",
    "JACK FROST",
    "WHITE CHRISTMAS",
    "SILENT NIGHT",
    "POINSETTIA",
    "EGGNOG",
    "XMAS",
    "WREATH",
    "TINSEL",
    "BOXING DAY",
    "KRAMPUS",
    "ST NICK",
    "SAINT NICHOLAS",
    "MRS CLAUS",
)
"""Primary signal: category names are curator-written and thematically
coherent, so matching on the category (rather than the clue/response
text, which is noisier) is precise -- same reasoning as cauldron's own
horror-trivia filter. Deliberately narrower/more specific than a first
guess would be (e.g., "CHRISTMAS CAROL" instead of bare "CAROL", "ELVES"
instead of bare "ELF", no bare "HOLLY") specifically to dodge the
collisions EXCLUDE_KEYWORDS below exists to patch -- narrow it further,
or broaden it, once this has actually been run against the real dataset
and the matched category list has been read (see this module's own
docstring and filter_clues())."""
EXCLUDE_KEYWORDS = (
    "SANTA ANNA",
    "SANTA FE",
    "CAROLINGIAN",
    "COWARD",
    "TINSELTOWN",
)
"""Substring collisions the include list above produces on the real
data, found the same way cauldron's own exclude list was: actually
running the filter and reading every matched category (and, for a few
that still looked ambiguous by category name alone, every clue in them).
"SANTA" matches the historical figure Santa Anna and the city Santa Fe,
neither about Santa Claus; "CAROLING" matches inside "CAROLINGIANS" (the
dynasty, unrelated); "NOEL" matches a category about Noel Coward the
playwright (excluded via "COWARD" specifically, rather than dropping
"NOEL" itself, which is otherwise a real Christmas category); "TINSEL"
matches "TINSELTOWN" (Hollywood's nickname). "RUDOLPH"/"ELVES"/
"ORNAMENT" were dropped from INCLUDE_KEYWORDS entirely rather than
patched with excludes here - checking every clue under each showed they
were almost always about unrelated people named Rudolph (Giuliani,
Valentino, Wilma/Maya Rudolph...), general fantasy-elf lore (Tolkien,
Harry Potter, D&D), or non-Christmas ornamentation (sequins, Hollywood
"tinsel", car hood ornaments, lawn flamingos) far more often than
anything about the reindeer, Santa's workshop, or tree decorations."""
_INCLUDE_PATTERN = re.compile("|".join(INCLUDE_KEYWORDS), re.IGNORECASE)
_EXCLUDE_PATTERN = (
    re.compile("|".join(EXCLUDE_KEYWORDS), re.IGNORECASE) if EXCLUDE_KEYWORDS else None
)

POINTS_BY_ROUND = {"1": 1, "2": 2}
"""Flat per-round point value -- the dataset's raw dollar figures swing
wildly across eras (inflation, value increases over the decades) and
aren't meaningful as absolute points for a casual game. Round 3 (Final
Jeopardy!) is dropped entirely below -- it's wager-based, which this
game doesn't model."""


@dataclass(frozen=True, slots=True)
class Clue:
    """One filtered, game-ready trivia clue."""

    id: str
    category: str
    clue: str
    """The clean text -- shown on screen and read to Gemini for judging,
    and (via ensure_audio_cache()) what's actually spoken aloud."""
    response: str
    """The correct response (the dataset's "question" field)."""
    points: int
    round: int
    """Jeopardy! round (1 or 2) -- drives points, and the group page's
    "Round N" display."""


def _clean(text: str) -> str:
    """Strip a stray-backslash artifact baked into the upstream TSV itself
    -- it backslash-escapes a quote or apostrophe whenever one lands next
    to the tab delimiter, even outside any quoted CSV field. Real clue
    text never legitimately contains a backslash, so dropping every one
    is safe (same fix cauldron's own trivia_data.py applies, confirmed
    against the same dataset)."""
    return text.replace("\\", "")


def _ensure_terminal_punctuation(text: str) -> str:
    """The dataset's source data omits terminal punctuation entirely (a
    board-display convention -- clues are meant to be read off a lit-up
    board, not as prose) -- but here the clue is read aloud and shown on
    screen as a complete sentence, so it needs a proper period (or
    whatever it already ends with) for both TTS pacing and
    readability."""
    return text if text[-1] in ".?!" else f"{text}."


def _clue_id(category: str, clue: str) -> str:
    """A stable id derived from content, not row position -- re-running
    the filter (e.g., after tweaking the keyword lists) doesn't reshuffle
    ids out from under an already-reviewed excluded_ids.json."""
    digest = hashlib.sha256(f"{category}\x1f{clue}".encode()).hexdigest()
    return digest[:12]


def download_dataset() -> None:
    """Fetch the dataset zip if not already on disk. ~57MB, one-time."""
    if RAW_ZIP_PATH.exists():
        return
    RAW_ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {DATASET_URL} ...")
    urllib.request.urlretrieve(DATASET_URL, RAW_ZIP_PATH)


def extract_tsv() -> None:
    """Pull just the combined-seasons TSV out of the zip -- the release
    also bundles per-season files and a kids/teen-tournament file this
    game has no use for."""
    if TSV_PATH.exists():
        return
    with zipfile.ZipFile(RAW_ZIP_PATH) as archive:
        with archive.open(TSV_MEMBER) as member:
            TSV_PATH.write_bytes(member.read())


def _load_excluded_ids() -> set[str]:
    if not EXCLUDED_IDS_PATH.exists():
        return set()
    return set(json.loads(EXCLUDED_IDS_PATH.read_text()))


def filter_clues() -> list[Clue]:
    """Read the raw TSV and return the Christmas/holiday-adjacent subset,
    minus anything hand-excluded via excluded_ids.json."""
    excluded = _load_excluded_ids()
    clues: list[Clue] = []
    with open(TSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            points = POINTS_BY_ROUND.get(row["round"])
            if points is None:
                # Final Jeopardy! (round 3), or a malformed row.
                continue
            category = _clean(row["category"].strip())
            included = _INCLUDE_PATTERN.search(category) and not (
                _EXCLUDE_PATTERN and _EXCLUDE_PATTERN.search(category)
            )
            if not included:
                continue
            clue_text = _clean(row["answer"].strip())
            response = _clean(row["question"].strip())
            if (not clue_text) or (not response):
                continue
            clue_text = _ensure_terminal_punctuation(clue_text)
            clue_id = _clue_id(category, clue_text)
            if clue_id in excluded:
                continue
            clues.append(
                Clue(clue_id, category, clue_text, response, points, int(row["round"]))
            )
    return clues


def _even_count(n: int) -> int:
    """Round down to the nearest even number."""
    return n - (n % 2)


def select_clues(pool: list[Clue], count: int, batch: int) -> list[Clue]:
    """Sample down to ``count`` clues, split evenly between round 1 and
    round 2 (and each half forced even too).

    SAMPLE_SEED is fixed, not fresh randomness each run, so re-running
    this script for the *same* batch (e.g., after adding something to
    excluded_ids.json) doesn't reshuffle the selection and throw away
    already-synthesized audio. ``batch`` picks which non-overlapping
    slice of that one fixed shuffle to use -- each round's pool is
    shuffled once under the fixed seed, then cut into consecutive
    ``count``-sized chunks, so batch 0, 1, 2, ... never share a clue with
    each other, while re-running the same batch number always reproduces
    the exact same clues. Same scheme as cauldron's own select_clues().
    """
    total = _even_count(count)
    half = total // 2
    round_targets = (half, half) if half % 2 == 0 else (half - 1, half + 1)

    sample_rng = random.Random(SAMPLE_SEED)
    sampled: list[Clue] = []
    for round_num, target in zip((1, 2), round_targets):
        round_pool = sorted(
            (clue for clue in pool if clue.round == round_num), key=lambda c: c.id
        )
        sample_rng.shuffle(round_pool)
        actual = _even_count(min(target, len(round_pool)))
        start = batch * actual
        chunk = round_pool[start : start + actual]
        if len(chunk) < actual:
            raise ValueError(
                f"Round {round_num} doesn't have enough unused clues left for "
                f"batch {batch} (needed {actual}, only {len(chunk)} left after "
                f"the {start} already claimed by earlier batches) -- lower "
                "--count, pick an earlier --batch, or widen the keyword filter."
            )
        sampled.extend(chunk)
    return sampled


def _clue_audio_path(clue_id: str) -> Path:
    return AUDIO_DIR / f"{clue_id}.json"


def ensure_audio_cache(clues: list[Clue]) -> None:
    """Pre-synthesize any missing clue audio via voice.save_fixture() --
    the full ElevenLabs timestamped payload (audio + character alignment),
    not just an mp3, so trivia_game.py can feed it straight into
    talk.play_speech() for mouth-synced playback, same as talk.py
    --replay's own fixtures.

    Sequential, not concurrent: ElevenLabs' concurrent-request limit
    (even on paid tiers) rejects a burst of concurrent requests - same
    reasoning as cauldron's own ensure_voice_cache().
    """
    missing = [clue for clue in clues if not _clue_audio_path(clue.id).exists()]
    if not missing:
        print("Trivia audio cache already complete.")
        return
    print(f"Synthesizing {len(missing)} trivia clue line(s).")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for clue in missing:
        text = (
            f"From {clue.category}, for {clue.points} "
            f"point{'s' if clue.points != 1 else ''}: {clue.clue}"
        )
        voice.save_fixture(text, _clue_audio_path(clue.id))
    print("Trivia audio cache ready.")


def main() -> None:
    # override=True: see talk.py's own load_dotenv() call - the Pi also runs
    # a separate project whose shell config exports its own ELEVENLABS_API_KEY.
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(
        description="Download, filter, and voice-cache Christmas/holiday "
        "trivia clues from a public Jeopardy! clue dataset."
    )
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="Only (re)write trivia/christmas_clues.json; skip ElevenLabs "
        "synthesis. Useful while tuning the keyword filter.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=CLUE_COUNT,
        help=f"How many clues to sample for the night (default: {CLUE_COUNT}).",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=0,
        help="Which non-overlapping batch of clues to select (0-indexed, "
        "default: 0). Batches never share a clue with each other, so "
        "bumping this is how you get an entirely new set of questions for "
        "another night; re-running the same --batch always reproduces the "
        "same clues.",
    )
    args = parser.parse_args()

    download_dataset()
    extract_tsv()
    pool = filter_clues()
    clues = select_clues(pool, args.count, args.batch)

    if not args.skip_audio:
        ensure_audio_cache(clues)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps([asdict(c) for c in clues], indent=2) + "\n")
    print(
        f"{len(clues)} clues (sampled from {len(pool)} candidates) written to {OUTPUT_PATH}."
    )


if __name__ == "__main__":
    main()

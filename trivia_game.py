"""Christmas trivia game: two teams answer phone-submitted free text,
alternating turns, with a steal chance when the active team misses.

Reads `trivia/christmas_clues.json` and the pre-synthesized clue audio
fixtures under `trivia/audio/` -- see trivia_data.py, which must be run
once ahead of time for those. Judging each answer, though, calls Gemini
live during the round (see judge_answer()), as does the closing
recap (see generate_finale()) -- unlike the data-prep step, this game
needs real network access while actually playing, not just beforehand.

Rules:
  - Clues alternate strictly between the two teams -- whichever team held
    the primary attempt this round, the OTHER team holds it next round,
    regardless of who (if anyone) scored. Steal is a bonus scoring chance
    layered on top of that alternation, not something that changes it.
  - A team's primary attempt right/wrong is decided by judge_answer() --
    Gemini grades the free-text answer and writes the Unicorn's
    in-character reaction to it in the same call, not a host judgment
    call and not a local heuristic.
  - Primary wrong (or timed out) -> the other team gets one steal attempt
    at the same clue before the next (alternating) clue is dealt.
  - A tied score once the pool runs out goes to sudden death: a single
    numeric question (see trivia_tiebreakers.TIEBREAKER_QUESTIONS) that
    both teams answer at once, not alternating turns -- whichever guess
    lands closer to the real, sourced answer wins outright.

Phones join and answer over trivia_web.py's WebSocket; this module owns
the rules, trivia_web.py owns the HTTP/WebSocket plumbing. Both run as
coroutines on one asyncio event loop.

Every blocking call this module makes -- talk.play_speech() (a busy-spin
frame loop with no sleep when idle; running it directly on this coroutine
would freeze the whole WebSocket server for the line's full duration),
voice.synthesize(), and the judge/finale Gemini calls (this project has
no existing async HTTP/Gemini path, unlike cauldron's own trivia module)
-- is wrapped in loop.run_in_executor(). Non-speech lighting (the idle
"thinking" animation, a submission flash, the round-winner flourish)
lives in trivia_lights.py, built directly on talk.build_talk_frame()/
ddp.DDPSender rather than a ported light_control.py. Only one thing may
ever send DDP frames at a time -- trivia_lights.IdleLights is explicitly
stopped (and awaited) before every talk.play_speech() call in this
module, and only started again once that call returns; see
trivia_lights.py's own docstring for why.

Run directly to host a round against real hardware:

    .venv/bin/python trivia_game.py
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import random
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types

import config
import talk
import trivia_lights
import trivia_web
import voice
from blink import BlinkState
from ddp import DDPSender
from layout import UnicornLayout, load_layout
from trivia_data import AUDIO_DIR, OUTPUT_PATH, Clue
from trivia_tiebreakers import TIEBREAKER_QUESTIONS, NumericTiebreaker

# override=True: see talk.py's own load_dotenv() call - the Pi also runs a
# separate project whose shell config exports its own ELEVENLABS_API_KEY.
# Called here, at module scope before _GEMINI_CLIENT below, rather than in
# main() the way talk.py calls it - unlike talk.py's client (built inside
# run_conversation(), a function, never at import time), _GEMINI_CLIENT is
# a module-level singleton and would otherwise construct itself before
# GEMINI_API_KEY was ever loaded.
load_dotenv(override=True)

TeamId = Literal[1, 2]
Phase = Literal["primary", "steal"]

GAME_TITLE = "The Unicorn's Holiday Trivia"

TEAM_NAMES: dict[TeamId, str] = {1: "Team Tinsel", 2: "Team Holly"}
TEAM_COLORS: dict[TeamId, str] = {1: "red", 2: "green"}
"""Named colors straight from config.EFFECT_COLOR_PALETTE - no new RGB
values needed. Red and green are the two canonical Christmas colors."""

ANSWER_TIMEOUT_SEC = 120.0
"""Untimed trivia with a live crowd stalls; a timeout is treated exactly
like a wrong (empty) answer (see judge_answer())."""
WARNING_LEAD_SEC = 10.0
"""How long before the deadline the Unicorn calls out a warning (see
WARNING_LINES) -- must stay comfortably under ANSWER_TIMEOUT_SEC."""

_GEMINI_CLIENT = genai.Client()
"""Judging and the finale recap are each one Gemini call - see
judge_answer()/generate_finale(). Its own client, kept separate from
talk.py's own (instantiated inside run_conversation()) - this is a
separate standalone script, never run in the same process as talk.py."""
_JUDGE_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.9,
    max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
"""temperature leaves room for banter variety; the judging half of the
prompt is explicit enough not to need a cooler temperature too.
max_output_tokens/model reuse config.GEMINI_MAX_OUTPUT_TOKENS/
config.GEMINI_MODEL - the model this project's own API key/tier has
already proven not to 503 on, rather than guessing a different one tuned
for a different project's key."""

_PERSONA_INTRO = (
    "You are the Christmas Unicorn, an LED, magical, cheerful unicorn "
    "decoration hosting a holiday trivia game for two teams of visitors "
    "at a home holiday light display. Stay warm and a little silly, and "
    "keep everything appropriate for trick-or-treater-aged kids and "
    "their families."
)
_EFFECT_INSTRUCTIONS = (
    "You may wrap part of your line in one of these effect tags to make "
    "your lights perform a matching effect while you say that part: "
    "[sparkle:Horn]...[/sparkle], [rainbow:all]...[/rainbow], "
    "[glow:Mane]...[/glow], [flash:Hat]...[/flash], or "
    "[color:Horn:red]...[/color] (color needs a color name too: red, "
    "orange, gold, green, blue, purple, pink, or white). The target must "
    "be one of Head, Mane, Trim, Hat, Horn, Eye, or all. Put the effect/"
    "target/color on the opening tag only - the closing tag is always "
    "just the bare effect name, e.g. [/sparkle], never [/sparkle:Horn]. "
    "Use at most one tag, and only if the line is genuinely about your "
    "lights or magic - most lines won't need one. Never use square "
    "brackets for anything else - no stage directions or emotion markers "
    "like [laughs] or [whispers]; convey emotion in the words themselves."
)
"""Both this project's own effect tags and ElevenLabs' (unused here, see
trivia_game.py's module docstring and the project plan) "Audio Tags" use
the same [...] bracket syntax - without an explicit instruction, Gemini
could plausibly write a stage direction like [laughs] (which this
project's voice.extract_effect_tags() wouldn't recognize and would leave
untouched, so ElevenLabs would read it aloud as literal garbage). The
last two sentences here close that gap."""

STEAL_LINES = (
    "Ooh, {team}, a miss leaves the sleigh wide open - steal it!",
    "Don't let this one get away, {team} - it's yours to steal!",
    "{team}, the jingle bells are ringing just for you - steal time!",
    "A chance like this doesn't come twice, {team} - go steal it!",
)
"""A steal reopens the SAME clue for the other team -- re-reading the
whole question again would be tedious and sounds like a mistake, so the
Unicorn announces the steal chance instead (the clue/category stay up on
the group screen throughout, so nothing's actually lost).

Hand-written (not Gemini-generated). The only variable is the team name,
which is always one of exactly two values, so every (team, line)
combination is pre-synthesized once by ensure_line_cache() rather than
calling ElevenLabs live for these moments - see that function."""
WARNING_LINES = (
    "Ten seconds left, {team} - better hurry!",
    "Tick tock, {team}, only ten seconds remain!",
    "Quick, {team}, the clock's almost out of magic!",
)
"""Called out WARNING_LEAD_SEC before the answer clock runs out - same
hand-written/pre-cached reasoning as STEAL_LINES above."""


def _steal_audio_path(team: TeamId, index: int) -> Path:
    return AUDIO_DIR / f"_steal_{team}_{index}.json"


def _warning_audio_path(team: TeamId, index: int) -> Path:
    return AUDIO_DIR / f"_warning_{team}_{index}.json"


def _tiebreaker_audio_path(index: int) -> Path:
    return AUDIO_DIR / f"_tiebreaker_{index}.json"


def ensure_line_cache() -> None:
    """Pre-synthesize every (team, line) combination of STEAL_LINES/
    WARNING_LINES, plus one reading of each TIEBREAKER_QUESTIONS entry,
    if not already cached. Safe to call every time a round starts -- a
    no-op once the full set exists.

    Sequential, not concurrent -- ElevenLabs' concurrent-request limit
    (even on paid tiers) rejects a burst of concurrent requests, same
    reasoning as trivia_data.ensure_audio_cache().
    """
    missing: list[tuple[Path, str]] = []
    for team in (1, 2):
        for index, line in enumerate(STEAL_LINES):
            path = _steal_audio_path(team, index)
            if not path.exists():
                missing.append((path, line.format(team=TEAM_NAMES[team])))
        for index, line in enumerate(WARNING_LINES):
            path = _warning_audio_path(team, index)
            if not path.exists():
                missing.append((path, line.format(team=TEAM_NAMES[team])))
    for index, tiebreaker in enumerate(TIEBREAKER_QUESTIONS):
        path = _tiebreaker_audio_path(index)
        if not path.exists():
            missing.append((path, tiebreaker.question))
    if not missing:
        return
    print(f"Pre-synthesizing {len(missing)} steal/warning/tiebreaker line(s) (one-time).")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for path, text in missing:
        voice.save_fixture(text, path)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Gemini's ruling on one attempt, plus the Unicorn's in-character
    reaction to it -- see judge_answer()."""

    correct: bool
    banter: str


def _judge_prompt(
    *,
    category: str,
    clue: str,
    correct_response: str,
    submitted: str | None,
    phase: Phase,
) -> str:
    # A primary miss reopens this exact clue as a steal for the other
    # team (see submit_answer()) -- so if this IS a primary attempt, the
    # banter must not give the answer away in case it's wrong, since the
    # other team is about to get a crack at the same clue. Anything else
    # (a correct primary, or a steal either way) is the last word on this
    # clue, so revealing it is safe there.
    secrecy_note = (
        "This is the team's first (primary) attempt at this clue. If "
        "correct, no one else answers this clue, so your banter may "
        "freely state the correct answer. If incorrect, the OTHER team "
        "gets one steal attempt at this exact clue immediately after - "
        "in that case your banter must NOT reveal, confirm, hint at, or "
        "restate the correct answer; only react to how wrong their guess "
        "was, without giving anything away."
        if phase == "primary"
        else "This is a steal attempt (or otherwise the last word on "
        "this clue either way) - no one else will get to answer it, so "
        "your banter may freely state or confirm the correct answer."
    )
    # A timeout (no submission at all) is a distinct case from a wrong
    # guess -- there's no actual answer to name-check, so the prompt must
    # say that plainly rather than feeding the judge a fake placeholder
    # answer it would otherwise quote back verbatim as if the visitors
    # had said those words.
    if submitted is None:
        answer_block = "The visitors gave no answer at all before time ran out - total silence."
        banter_instruction = (
            "Since they gave no answer, do not invent or quote a fake "
            'answer for them and do not say anything like "no answer '
            'given" - gently tease their silence or hesitation, in '
            "character."
        )
    else:
        answer_block = f"Visitors' answer: {submitted!r}"
        banter_instruction = (
            "Name-check the visitors' actual answer directly, don't just "
            "react generically."
        )
    return f"""
{_PERSONA_INTRO}

Category: {category}
Clue read aloud: {clue}
Correct answer: {correct_response}
{answer_block}

Judge whether the visitors' answer is correct. Be lenient about minor
misspellings, missing articles, or a different phrasing of the same
thing, but it must actually be the same answer - a vague, wrong,
off-topic, or missing answer is incorrect.

Then write ONE short line of in-character banter reacting to this
specific exchange - {banter_instruction} If correct, celebrate warmly.
If incorrect, deliver a playful, gentle tease - never genuinely mean,
this is a family game. Avoid em dashes or en dashes; use commas or
periods instead, since text-to-speech reads dashes awkwardly.
{secrecy_note}

{_EFFECT_INSTRUCTIONS}

Return ONLY a valid JSON object in this exact format, with no extra text:
{{"correct": true or false, "banter": "..."}}
"""


def judge_answer(
    category: str,
    clue: str,
    correct_response: str,
    submitted: str | None,
    phase: Phase,
) -> Verdict:
    """Ask Gemini to grade ``submitted`` against ``correct_response`` and
    write the Unicorn's in-character reaction to it, in one call.

    This is one of two places this module touches the network live (the
    other is generate_finale()) - see the module docstring. ``phase``
    controls whether the banter is allowed to give away the correct
    answer - see _judge_prompt()'s secrecy_note. Plain (sync) function,
    not async - see the module docstring's note on run_in_executor().
    """
    prompt = _judge_prompt(
        category=category,
        clue=clue,
        correct_response=correct_response,
        submitted=submitted,
        phase=phase,
    )
    response = _GEMINI_CLIENT.models.generate_content(
        model=config.GEMINI_MODEL, contents=prompt, config=_JUDGE_CONFIG
    )
    if response.text is None:
        raise RuntimeError("Gemini returned no text for trivia judging")
    print(f"[gemini:judge] {response.text}", file=sys.stderr)
    raw = json.loads(response.text)
    return Verdict(correct=bool(raw["correct"]), banter=str(raw["banter"]))


def _summarize_outcomes(state: "TriviaState", winner: TeamId) -> str:
    """Boil state.outcomes down into a compact, factual recap for
    _finale_prompt() to riff on -- not shown to anyone directly, just
    context for Gemini."""
    lines = [
        f"Final score: {TEAM_NAMES[1]} {state.team_scores[1]}, "
        f"{TEAM_NAMES[2]} {state.team_scores[2]}. {TEAM_NAMES[winner]} wins!"
    ]
    total = len(state.outcomes)
    scored = sum(1 for o in state.outcomes if o.scorer is not None)
    lines.append(
        f"{scored} of {total} questions were answered correctly; "
        f"{total - scored} stumped both teams."
    )
    steals = sum(1 for o in state.outcomes if o.stolen)
    if steals:
        lines.append(
            f"{steals} of those were won on a steal after the other team "
            "missed first."
        )
    for team in (1, 2):
        categories = sorted({o.category for o in state.outcomes if o.scorer == team})
        if categories:
            lines.append(f"{TEAM_NAMES[team]} scored on: {', '.join(categories)}.")
    return "\n".join(lines)


def _finale_prompt(*, state: "TriviaState", winner: TeamId, tiebreaker_note: str) -> str:
    summary = _summarize_outcomes(state, winner)
    return f"""
{_PERSONA_INTRO}

You and your visitors have just finished a holiday trivia game between
two teams, {TEAM_NAMES[1]} and {TEAM_NAMES[2]}.

{summary}
{tiebreaker_note}

Write one short, warm closing line recapping how this specific game
actually went - reference specific, real details from the summary above
(the final score, the winning team by name, a category someone did well
in, whether a steal or the tiebreaker decided it, etc.), not a generic
"good game" line. Avoid em dashes or en dashes; use commas or periods
instead, since text-to-speech reads dashes awkwardly.

{_EFFECT_INSTRUCTIONS}

Return ONLY a valid JSON object in this exact format, with no extra text:
{{"text": "..."}}
"""


def generate_finale(state: "TriviaState", winner: TeamId, tiebreaker_note: str = "") -> str:
    """Ask Gemini to write the Unicorn's closing recap line, referencing
    how this specific game actually went.

    Content is different every round (depends on what was actually
    asked/answered), so like judge_answer()'s banter this is generated
    live rather than pre-cached. Plain (sync) function, not async - see
    the module docstring's note on run_in_executor().
    """
    prompt = _finale_prompt(state=state, winner=winner, tiebreaker_note=tiebreaker_note)
    response = _GEMINI_CLIENT.models.generate_content(
        model=config.GEMINI_MODEL, contents=prompt, config=_JUDGE_CONFIG
    )
    if response.text is None:
        raise RuntimeError("Gemini returned no text for trivia finale")
    print(f"[gemini:finale] {response.text}", file=sys.stderr)
    raw = json.loads(response.text)
    return str(raw["text"])


async def _speak_warning(
    loop: asyncio.AbstractEventLoop,
    idle: trivia_lights.IdleLights,
    layout: UnicornLayout,
    sender: DDPSender | None,
    brightness: float,
    blink: BlinkState,
    band_config: dict[str, config.BandConfig],
    alsa_speaker: str | None,
    answerer: TeamId,
) -> None:
    """Wait until WARNING_LEAD_SEC is left on the answer clock, then have
    the Unicorn call out one of WARNING_LINES (pre-cached, see
    ensure_line_cache()). Runs as a separate task alongside the answer
    wait in _play_attempt(), which cancels it early if a response (or
    the full timeout) arrives first -- safe, since this only ever cancels
    the pending asyncio.sleep() below, before the executor call starts;
    see the module docstring's note on no mid-flight interruption.

    Stops/restarts ``idle`` around its own speech - only one thing may
    ever send DDP frames at a time, see trivia_lights.py's own docstring.
    """
    await asyncio.sleep(max(0.0, ANSWER_TIMEOUT_SEC - WARNING_LEAD_SEC))
    index = random.randrange(len(WARNING_LINES))
    speech = voice.load_fixture(_warning_audio_path(answerer, index))
    await idle.stop()
    await loop.run_in_executor(
        None, talk.play_speech, speech, layout, sender, brightness, blink, band_config, alsa_speaker
    )
    idle.start()


def other_team(team: TeamId) -> TeamId:
    return 2 if team == 1 else 1


_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_number(text: str) -> float | None:
    """Pull the first plain number out of free text, or None if it
    doesn't contain one -- a tiebreaker guess can be phrased any which
    way ("about 19", "I'd say 19 people"), not just a bare digit string.
    Thousands commas are stripped before parsing; anything else
    (fractions, number words) isn't handled."""
    match = _NUMBER_PATTERN.search(text)
    if match is None:
        return None
    return float(match.group().replace(",", ""))


def closest_team(
    guess_1: float | None, guess_2: float | None, answer: float
) -> TeamId | None:
    """Whichever guess lands nearer ``answer`` wins outright. None if
    neither guess parsed, or if both land exactly as close -- the caller
    then needs a genuine coin flip (see _play_tiebreaker())."""
    if (guess_1 is None) and (guess_2 is None):
        return None
    if guess_1 is None:
        return 2
    if guess_2 is None:
        return 1
    distance_1 = abs(guess_1 - answer)
    distance_2 = abs(guess_2 - answer)
    if distance_1 == distance_2:
        return None
    return 1 if distance_1 < distance_2 else 2


@dataclass(frozen=True, slots=True)
class ClueOutcome:
    """One fully-resolved clue, logged by submit_answer() for the
    end-of-round finale summary -- see generate_finale(). Not used for
    scoring itself, which team_scores already handles directly."""

    category: str
    points: int
    scorer: TeamId | None
    """None if nobody got it right."""
    stolen: bool
    """True if scorer won it on a steal attempt, not primary."""


@dataclass
class TriviaState:
    """The round's full state. Mutated via submit_answer()/start_clue(),
    which return a new instance each time -- an immutable-snapshot
    style that keeps the turn/steal logic a pure, directly testable
    function."""

    team_scores: dict[TeamId, int] = field(default_factory=lambda: {1: 0, 2: 0})
    current_team: TeamId = 1
    """Who holds the *primary* attempt this round."""
    phase: Phase = "primary"
    active_clue: Clue | None = None
    remaining: list[Clue] = field(default_factory=list)
    asked: list[Clue] = field(default_factory=list)
    outcomes: list[ClueOutcome] = field(default_factory=list)
    total_clues: int = 0
    """Set once at construction (see run()) -- the main pool's size, for
    the group/team screens' "question N of total_clues" display.
    len(asked) + 1 is the active clue's own position in that count (steal
    re-attempts don't advance it -- asked only grows once a clue is
    fully resolved, see submit_answer())."""


def current_answerer(state: TriviaState) -> TeamId:
    """Which team is allowed to submit right now: current_team during the
    primary attempt, the other team during a steal."""
    return (
        state.current_team
        if state.phase == "primary"
        else other_team(state.current_team)
    )


def start_clue(state: TriviaState) -> TriviaState:
    """Deal the next clue as a fresh primary attempt. No-op (returns
    ``state`` unchanged) once the pool is exhausted -- the caller checks
    for that to end the round."""
    if not state.remaining:
        return state
    clue, *rest = state.remaining
    return dataclasses.replace(state, active_clue=clue, remaining=rest, phase="primary")


def submit_answer(state: TriviaState, correct: bool) -> TriviaState:
    """Resolve one attempt (by whichever team current_answerer() says is
    up) at the active clue.

    Primary wrong -> reopens the same clue as a steal for the other
    team (current_team/active_clue/scores untouched; only ``phase``
    flips). Anything else resolves the round: credit the scorer if
    ``correct``, then alternate ``current_team`` to the other team for
    next round, regardless of who (if anyone) just scored - strict ABAB
    turn order, steal is a bonus on top of it, not a change to it.
    """
    assert state.active_clue is not None
    if (state.phase == "primary") and (not correct):
        return dataclasses.replace(state, phase="steal")

    scores = state.team_scores
    scorer: TeamId | None = None
    if correct:
        scorer = current_answerer(state)
        scores = {**scores, scorer: scores[scorer] + state.active_clue.points}
    outcome = ClueOutcome(
        category=state.active_clue.category,
        points=state.active_clue.points,
        scorer=scorer,
        stolen=(scorer is not None) and (state.phase == "steal"),
    )
    return dataclasses.replace(
        state,
        team_scores=scores,
        asked=[*state.asked, state.active_clue],
        outcomes=[*state.outcomes, outcome],
        active_clue=None,
        current_team=other_team(state.current_team),
        phase="primary",
    )


def _clue_audio_path(clue_id: str) -> Path:
    return AUDIO_DIR / f"{clue_id}.json"


def load_clues() -> list[Clue]:
    """Read trivia_data.py's prepared output. Raises SystemExit with a
    clear message if trivia_data.py hasn't been run yet."""
    if not OUTPUT_PATH.exists():
        raise SystemExit(
            f"{OUTPUT_PATH} not found - run `.venv/bin/python trivia_data.py` "
            "once first to download, filter, and voice-cache the question bank."
        )
    rows = json.loads(OUTPUT_PATH.read_text())
    return [Clue(**row) for row in rows]


def _local_ip() -> str:
    """Best-effort guess at this machine's LAN-facing IP, for printing a
    clickable/typeable address at startup. Opens a UDP socket "connected"
    to a public address purely so the OS reports which local interface it
    would route through -- UDP has no handshake, so no packet actually
    goes anywhere. Falls back to "localhost" if that fails (no network at
    all), which at least keeps the printed URL well-formed.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "localhost"


async def run(
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
    pi: bool = False,
    dry_run: bool = False,
    brightness: float = config.TALK_BRIGHTNESS,
    controller_ip: str | None = None,
    use_official_colors: bool = False,
) -> TriviaState:
    """Host one full round: start the phone-facing server, deal clues
    until the pool's exhausted, and announce the result. Returns the
    final state.
    """
    clues = load_clues()
    random.shuffle(clues)
    # Shuffle first, then stable-sort by points: randomizes the order
    # within each round while still ramping the whole round up from the
    # 1-point clues to the 2-point ones, Jeopardy!-board style, rather
    # than mixing point values throughout.
    clues.sort(key=lambda clue: clue.points)
    state = TriviaState(remaining=clues, total_clues=len(clues))

    layout = load_layout()
    ip = controller_ip or layout.controller_ip
    band_config = config.official_band_config() if use_official_colors else config.BAND_CONFIG
    print(f"Target: {ip} (DDP)" if not dry_run else "Dry run: not sending DDP packets")
    print(f"Colors: {'official show colors' if use_official_colors else 'hand-picked (config.BAND_CONFIG)'}")

    sender = None if dry_run else DDPSender(ip)
    blink = BlinkState()
    alsa_speaker = config.PI_SPEAKER_DEVICE if pi else None
    idle = trivia_lights.IdleLights(layout, sender, brightness, band_config, blink)

    if sender is not None:
        sender.send_frame(talk.build_talk_frame(layout, False, True, brightness, band_config))

    loop = asyncio.get_running_loop()
    server = await trivia_web.start(
        host=host, port=port, team_names=TEAM_NAMES, game_title=GAME_TITLE
    )
    display_ip = _local_ip()
    print(f"Group screen (put this on the TV): http://{display_ip}:{port}/")
    print("Team pages (normally reached by scanning a QR code from the group screen):")
    for team, name in TEAM_NAMES.items():
        print(f"  {name}: http://{display_ip}:{port}/team/{team}")
    # Explicit flush -- stdout isn't a tty when run non-interactively, so
    # these prints are fully buffered rather than flushed per line.
    sys.stdout.flush()

    # One-time (see ensure_line_cache()) -- near-instant on every run
    # after the first, since it's a no-op once the full cache exists.
    await loop.run_in_executor(None, ensure_line_cache)

    # Idle rainbow while the host waits for both teams to join - nothing
    # is being spoken yet, so it's safe to leave running.
    idle.start()
    await loop.run_in_executor(
        None,
        input,
        "\nPress Enter once both teams have joined and the group screen is up to start the round...\n",
    )

    try:
        state = start_clue(state)
        while state.active_clue is not None:
            state = await _play_attempt(
                state, server, loop, layout, sender, brightness, blink, band_config, idle, alsa_speaker
            )
            if state.active_clue is None:
                state = start_clue(state)

        tiebreaker_note = ""
        if state.team_scores[1] == state.team_scores[2]:
            question = random.choice(TIEBREAKER_QUESTIONS)
            print(f"Scores tied - sudden death: {question.question}")
            winner: TeamId = await _play_tiebreaker(
                server, loop, layout, sender, brightness, blink, band_config, idle, alsa_speaker, question
            )
            tiebreaker_note = (
                "The game was tied, so it came down to a sudden-death "
                f'question: "{question.question}" The correct answer was '
                f"{question.answer}."
            )
        else:
            winner = 1 if state.team_scores[1] > state.team_scores[2] else 2
        await server.broadcast_state(state)
        await server.broadcast_end(state, winner)

        # Idle rainbow fills the wait while the finale's Gemini call and
        # synthesis run - both _play_attempt() and _play_tiebreaker()
        # always leave idle stopped when they return, so this is a fresh
        # start, not a double-start.
        idle.start()
        finale_text = await loop.run_in_executor(None, generate_finale, state, winner, tiebreaker_note)
        finale_speech = await loop.run_in_executor(None, voice.synthesize, finale_text)
        await idle.stop()

        # Reveal the text as soon as it's ready, even though the Unicorn
        # hasn't spoken it aloud yet - same reasoning as
        # trivia_web.Server.broadcast_finale()'s own docstring.
        await server.broadcast_finale(finale_text)
        await loop.run_in_executor(
            None, talk.play_speech, finale_speech, layout, sender, brightness, blink, band_config, alsa_speaker
        )
        await trivia_lights.victory_flourish(
            layout, sender, brightness, band_config, blink, TEAM_COLORS[winner]
        )

        print(
            f"Trivia round complete. {TEAM_NAMES[1]}: {state.team_scores[1]}, "
            f"{TEAM_NAMES[2]}: {state.team_scores[2]}. Winner: {TEAM_NAMES[winner]}."
        )
        return state
    finally:
        await idle.stop()
        await server.stop()
        if sender is not None:
            sender.close()


async def _play_attempt(
    state: TriviaState,
    server: trivia_web.Server,
    loop: asyncio.AbstractEventLoop,
    layout: UnicornLayout,
    sender: DDPSender | None,
    brightness: float,
    blink: BlinkState,
    band_config: dict[str, config.BandConfig],
    idle: trivia_lights.IdleLights,
    alsa_speaker: str | None,
) -> TriviaState:
    """Resolve exactly one attempt (primary or steal) at ``state``'s
    active clue -- reading it, waiting for an answer, and judging it --
    returning the updated state.

    Extracted out of run()'s main loop so the sudden-death tiebreaker
    (also in run()) can drive the same per-attempt logic against its own
    small reserve of questions, rather than duplicating it. Always
    leaves ``idle`` stopped when it returns (success or exception) -
    callers rely on that invariant, see trivia_lights.py's own docstring.
    """
    clue = state.active_clue
    assert clue is not None
    # Computed before any broadcast so both broadcast_state()'s
    # highlight and broadcast_prompt()'s "whose turn" agree -- during
    # a steal, this is the OTHER team from state.current_team.
    answerer = current_answerer(state)
    await server.broadcast_state(state, answering_team=answerer)
    # Show the clue on the group screen as soon as it starts being read,
    # not once it's done - before the (blocking) audio playback, not
    # after. len(state.asked) + 1 is this clue's own position in the
    # round - a steal re-attempt on the same clue doesn't advance it.
    await server.broadcast_prompt(
        answerer,
        clue,
        state.phase,
        question_number=len(state.asked) + 1,
        total_questions=state.total_clues,
    )

    await idle.stop()
    if state.phase == "primary":
        speech = voice.load_fixture(_clue_audio_path(clue.id))
    else:
        # Don't re-read the clue -- it's the same question, just
        # reopened for the other team; a short in-character steal
        # announcement instead of the full question again. Pre-cached
        # (see ensure_line_cache()), not synthesized here.
        steal_index = random.randrange(len(STEAL_LINES))
        speech = voice.load_fixture(_steal_audio_path(answerer, steal_index))
    await loop.run_in_executor(
        None, talk.play_speech, speech, layout, sender, brightness, blink, band_config, alsa_speaker
    )

    # Now actually waiting for a phone answer - safe to animate, nothing
    # else is being spoken. The countdown starts here, not when the
    # prompt was first broadcast - the clue/steal line's own reading just
    # finished, so this is the actual moment the answer clock begins.
    idle.start()
    await server.broadcast_timer(ANSWER_TIMEOUT_SEC)
    warning_task = asyncio.create_task(
        _speak_warning(loop, idle, layout, sender, brightness, blink, band_config, alsa_speaker, answerer)
    )
    try:
        response = await asyncio.wait_for(
            server.wait_for_answer(answerer), timeout=ANSWER_TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        response = None
    finally:
        # A response (or the full timeout) landed before WARNING_LEAD_SEC
        # was even reached - no point warning about a clock that's
        # already stopped mattering.
        if not warning_task.done():
            warning_task.cancel()
        try:
            await warning_task
        except asyncio.CancelledError:
            pass
    await idle.stop()

    if response is not None:
        # A submission actually landed (as opposed to the clock running
        # out) - stop the group screen's countdown immediately instead
        # of leaving it visibly ticking through however long judging
        # takes, and show a "locked in" indicator there (and on the
        # other team's phone) in its place. A quick white flash marks
        # the moment, distinct from the eventual correct/incorrect
        # outcome below (not known yet - judge_answer() hasn't run).
        await server.broadcast_submitted(answerer, stop_timer=True)
        await trivia_lights.flash_color(
            layout, sender, brightness, band_config, blink, "white", duration=0.3
        )

    verdict = await loop.run_in_executor(
        None, judge_answer, clue.category, clue.clue, clue.response, response, state.phase
    )

    banter_speech = await loop.run_in_executor(None, voice.synthesize, verdict.banter)
    await loop.run_in_executor(
        None, talk.play_speech, banter_speech, layout, sender, brightness, blink, band_config, alsa_speaker
    )

    await server.broadcast_result(answerer, verdict.correct, state.phase, verdict.banter)
    return submit_answer(state, verdict.correct)


async def _play_tiebreaker(
    server: trivia_web.Server,
    loop: asyncio.AbstractEventLoop,
    layout: UnicornLayout,
    sender: DDPSender | None,
    brightness: float,
    blink: BlinkState,
    band_config: dict[str, config.BandConfig],
    idle: trivia_lights.IdleLights,
    alsa_speaker: str | None,
    question: NumericTiebreaker,
) -> TeamId:
    """Sudden death: both teams guess the SAME numeric question at once --
    no turns, no steal -- and whichever guess lands closer to the real
    answer (see closest_team()) wins outright. Falls back to an honest
    coin flip only if neither guess parses as a number, or both land
    exactly as close (vanishingly unlikely, but needs a real resolution
    rather than quietly favoring team 1).

    Always leaves ``idle`` stopped when it returns, same invariant as
    _play_attempt().
    """
    index = TIEBREAKER_QUESTIONS.index(question)
    await server.broadcast_tiebreaker(question.question)

    await idle.stop()
    speech = voice.load_fixture(_tiebreaker_audio_path(index))
    await loop.run_in_executor(
        None, talk.play_speech, speech, layout, sender, brightness, blink, band_config, alsa_speaker
    )

    idle.start()
    await server.broadcast_timer(ANSWER_TIMEOUT_SEC)

    async def _wait(team: TeamId) -> float | None:
        try:
            text = await asyncio.wait_for(
                server.wait_for_answer(team), timeout=ANSWER_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            return None
        # stop_timer=False -- both teams share this one clock, so one
        # team locking in a guess doesn't mean the other is done
        # thinking; only show the per-team indicator.
        await server.broadcast_submitted(team, stop_timer=False)
        return extract_number(text)

    guess_1, guess_2 = await asyncio.gather(_wait(1), _wait(2))
    await idle.stop()

    winner = closest_team(guess_1, guess_2, question.answer)
    if winner is None:
        winner = 1 if random.random() < 0.5 else 2

    await server.broadcast_tiebreaker_result(
        guess_1=guess_1,
        guess_2=guess_2,
        answer=question.answer,
        source=question.source,
        winner=winner,
    )
    return winner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Host one round of Christmas trivia for two phone-connected teams."
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--pi",
        action="store_true",
        help="Run directly on the Raspberry Pi next to the controller: audio "
        "plays through ALSA (config.PI_SPEAKER_DEVICE) instead of PulseAudio. "
        "Same convention as talk.py's own --pi.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't send DDP packets - for testing the web/game flow without "
        "the real controller.",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        default=config.TALK_BRIGHTNESS,
        help="0-1 overall power cap, same as talk.py's own --brightness.",
    )
    parser.add_argument(
        "--controller-ip",
        default=None,
        help="Override the controller IP parsed from xlights_networks.xml.",
    )
    parser.add_argument(
        "--official-colors",
        action="store_true",
        help="Use each group's dominant color from the real show's rendered .fseq "
        "(see show_colors.py) instead of config.BAND_CONFIG's hand-picked ones - "
        "same flag as talk.py/equalizer.py.",
    )
    args = parser.parse_args()

    asyncio.run(
        run(
            port=args.port,
            pi=args.pi,
            dry_run=args.dry_run,
            brightness=max(0.0, min(1.0, args.brightness)),
            controller_ip=args.controller_ip,
            use_official_colors=args.official_colors,
        )
    )


if __name__ == "__main__":
    main()

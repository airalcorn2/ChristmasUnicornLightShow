"""ElevenLabs text-to-speech (with per-character timing) and speech-to-text,
plus turning that timing into a mouth open/closed timeline.

Requires ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in the environment (see
.env.example) - the latter is account-specific, picked with `list_voices()`.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import requests

import config

_API_BASE = "https://api.elevenlabs.io/v1"


class ElevenLabsError(RuntimeError):
    """Raised when an ElevenLabs API call fails."""


def _api_key() -> str:
    """Return the ELEVENLABS_API_KEY environment variable, or raise if unset."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise ElevenLabsError("ELEVENLABS_API_KEY is not set - see .env.example")
    return key


def list_voices() -> list[dict]:
    """Return this account's available voices (id, name, etc.) - run this
    once to find the voice_id to put in .env as ELEVENLABS_VOICE_ID.
    """
    response = requests.get(f"{_API_BASE}/voices", headers={"xi-api-key": _api_key()})
    if not response.ok:
        raise ElevenLabsError(f"list_voices failed: {response.status_code} {response.text}")
    return response.json()["voices"]


class SpeechResult:
    """One synthesized line: the audio bytes (``config.ELEVENLABS_OUTPUT_FORMAT``,
    mp3 by default) and a mouth open/closed timeline derived from ElevenLabs'
    per-character timestamps.
    """

    def __init__(self, audio_bytes: bytes, open_intervals: list[tuple[float, float]]) -> None:
        """Store the synthesized audio and its mouth-open (start, end) times."""
        self.audio_bytes = audio_bytes
        self.open_intervals = open_intervals

    def duration(self) -> float:
        """Return the audio's total duration in seconds, per the last open interval's end."""
        return self.open_intervals[-1][1] if self.open_intervals else 0.0

    def mouth_open_at(self, elapsed: float) -> bool:
        """Return whether the mouth should be open ``elapsed`` seconds into
        playback, i.e. whether ``elapsed`` falls inside any open interval.
        """
        return any(start <= elapsed < end for start, end in self.open_intervals)


_CLOSED_LETTERS = frozenset("mbp")
"""Letters whose sound presses the lips together - the "MBP" viseme group in
classic phoneme-based lip sync (e.g., Preston Blair's phoneme chart, used by
tools like Papagayo): M/B/P are the only sounds where the lips fully close,
so with just two mouth shapes, they're the one binary contrast worth
tracking - every other letter (vowels, and consonants like F/V/T/S/K/L/etc.,
where the mouth is visibly open even if not wide) maps to "open". This is a
letter-based, not a true phonetic, classification - coarse by design, and
a given since ElevenLabs' timestamps are per-character, not per-phoneme.
Known misses are rare orthographic edge cases (a silent letter, as in
"comb"/"debt"; "ph" spelled with a p but sounding like f)."""


def _is_open_character(character: str) -> bool:
    """Return whether ``character`` keeps the mouth open - every letter
    except the lips-together "MBP" group (see _CLOSED_LETTERS); whitespace
    and punctuation are never "open" on their own.
    """
    return character.isalpha() and character.lower() not in _CLOSED_LETTERS


def _open_intervals_from_alignment(alignment: dict) -> list[tuple[float, float]]:
    """Collapse ElevenLabs' per-character timestamps into mouth-open (start,
    end) intervals: open while speaking letters other than M/B/P (see
    _CLOSED_LETTERS), closed during M/B/P, whitespace, and punctuation.

    A closed stretch shorter than config.MOUTH_MERGE_GAP merges into the
    open run on either side of it - *unless* an M/B/P letter occurred during
    it, in which case it's kept no matter how brief, since that's a
    deliberate closure (the actual signal), not noise. Without that
    exception, a brief M/B/P (e.g., a quick "m") would get silently merged
    away by the same threshold meant only to smooth over ordinary
    connected-speech gaps - confirmed with a synthetic "mama" test, where
    the middle M disappeared until this was added.
    """
    characters = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]

    intervals: list[tuple[float, float]] = []
    open_start: float | None = None
    open_end: float | None = None
    closed_has_mbp = False
    for character, start, end in zip(characters, starts, ends):
        if _is_open_character(character):
            if open_start is None:
                if (
                    intervals
                    and not closed_has_mbp
                    and (start - intervals[-1][1] < config.MOUTH_MERGE_GAP)
                ):
                    open_start, open_end = intervals.pop()
                else:
                    open_start = start
            open_end = end
        else:
            if open_start is not None:
                intervals.append((open_start, open_end))
                open_start = open_end = None
                closed_has_mbp = False
            if character.lower() in _CLOSED_LETTERS:
                closed_has_mbp = True
    if open_start is not None:
        intervals.append((open_start, open_end))
    return _apply_chatter(intervals)


def _apply_chatter(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Subdivide any open span longer than config.MOUTH_CHATTER_MIN_SPAN into
    a steady open/closed "chatter" (period config.MOUTH_CHATTER_PERIOD,
    config.MOUTH_CHATTER_DUTY_CYCLE fraction open per cycle) instead of
    leaving it one frozen, motionless open block.

    Needed because M/B/P letters (the only thing that closes the mouth -
    see _CLOSED_LETTERS) are often a second or more apart in ordinary
    English - e.g., "magic unicorn who guards this house" has none - which
    without this left the mouth holding dead still, wide open, through six
    spoken words at once. Short spans are left alone: they're already
    brief enough to read as natural movement against the closes around
    them.
    """
    chattered: list[tuple[float, float]] = []
    for start, end in intervals:
        if end - start <= config.MOUTH_CHATTER_MIN_SPAN:
            chattered.append((start, end))
            continue
        cycle_start = start
        while cycle_start < end:
            cycle_open_end = min(
                cycle_start + config.MOUTH_CHATTER_PERIOD * config.MOUTH_CHATTER_DUTY_CYCLE, end
            )
            chattered.append((cycle_start, cycle_open_end))
            cycle_start += config.MOUTH_CHATTER_PERIOD
    return chattered


def _tts_request(text: str) -> dict:
    """POST ``text`` to ElevenLabs' with-timestamps endpoint and return the
    raw JSON payload (audio_base64 + alignment) - the one network call
    behind both synthesize() and save_fixture().
    """
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not voice_id:
        raise ElevenLabsError("ELEVENLABS_VOICE_ID is not set - see .env.example")

    response = requests.post(
        f"{_API_BASE}/text-to-speech/{voice_id}/with-timestamps",
        headers={"xi-api-key": _api_key(), "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": config.ELEVENLABS_TTS_MODEL,
            "output_format": config.ELEVENLABS_OUTPUT_FORMAT,
        },
    )
    if not response.ok:
        raise ElevenLabsError(f"synthesize failed: {response.status_code} {response.text}")
    return response.json()


def _speech_result_from_payload(payload: dict) -> SpeechResult:
    """Build a SpeechResult from a raw ElevenLabs with-timestamps payload,
    re-deriving the mouth timeline from its alignment data every time -
    see load_fixture()'s docstring for why that matters.
    """
    audio_bytes = base64.b64decode(payload["audio_base64"])
    open_intervals = _open_intervals_from_alignment(payload["alignment"])
    return SpeechResult(audio_bytes, open_intervals)


def synthesize(text: str) -> SpeechResult:
    """Synthesize ``text`` to speech via ElevenLabs, returning the audio plus
    a derived mouth open/closed timeline (see SpeechResult).
    """
    return _speech_result_from_payload(_tts_request(text))


def save_fixture(text: str, path: Path) -> None:
    """Call ElevenLabs once for ``text`` and cache the raw response JSON at
    ``path`` - see load_fixture() to replay it without further API calls.
    """
    payload = _tts_request(text)
    with open(path, "w") as f:
        json.dump(payload, f)


def load_fixture(path: Path) -> SpeechResult:
    """Load a SpeechResult from a JSON file saved by save_fixture().

    Re-derives the mouth timeline from the cached alignment on every call,
    rather than caching the derived intervals themselves, so tuning
    _open_intervals_from_alignment()/MOUTH_MERGE_GAP can be tested by
    replaying the exact same audio repeatedly - no new API calls, and no
    stale cached timeline left over from before a logic change.
    """
    with open(path) as f:
        payload = json.load(f)
    return _speech_result_from_payload(payload)


def transcribe(wav_bytes: bytes) -> str:
    """Transcribe recorded speech (a WAV file's raw bytes) to text via
    ElevenLabs Scribe.
    """
    response = requests.post(
        f"{_API_BASE}/speech-to-text",
        headers={"xi-api-key": _api_key()},
        data={"model_id": config.ELEVENLABS_STT_MODEL},
        files={"file": ("speech.wav", wav_bytes, "audio/wav")},
    )
    if not response.ok:
        raise ElevenLabsError(f"transcribe failed: {response.status_code} {response.text}")
    return response.json()["text"]

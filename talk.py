"""Push-to-talk voice chat with the Christmas Unicorn: record a question,
transcribe it, get a short in-character reply from Gemini, synthesize that
reply with ElevenLabs, and play it back while the mouth/eye animate to the
speech's own word timing - all live, over DDP, on the real hardware (or
--dry-run, to test without it).

Requires GEMINI_API_KEY, ELEVENLABS_API_KEY, and ELEVENLABS_VOICE_ID in a
.env file - see .env.example.

Run directly:

    .venv/bin/python talk.py --list-devices   # Find a microphone source name.
    .venv/bin/python talk.py --device <name> --dry-run
    .venv/bin/python talk.py --device <name>
"""

from __future__ import annotations

import argparse
import colorsys
import io
import math
import os
import queue
import random
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.chats import Chat

import audio
import config
import voice
from blink import BlinkState
from ddp import DDPSender
from layout import UnicornLayout, load_layout
from voice import SpeechResult

_PLAYBACK_END_PADDING: float = 0.2
"""Extra seconds to keep sending frames past the last word's end time, so
the mouth doesn't visibly snap shut right as the audio trails off."""

_PLAYBACK_STARTUP_LATENCY: float = 0.05
"""Real startup latency for `paplay --latency-msec=20` playing an actual
decoded WAV, measured directly via paplay's own `-v` latency reporting:
a consistent ~15-23ms once audio is flowing, with some margin added here
for process-launch jitter.

This replaced ffplay, which - confirmed via a controlled loopback-capture
test, not a guess - took ~0.3s before any audio was actually audible, and
was outright unreliable under repeated invocation (missed entirely in 2 of
5 timed test runs). A single fixed compensation constant can't fix
unreliable, variable latency - it can only fix a *consistent* one - which
is why the original ffplay-based fix didn't resolve "the mouth doesn't
match the words" (it only fixed the separate, smaller issue of the mouth
being a fixed amount early). paplay doesn't decode MP3 itself, so the MP3
ElevenLabs returns is decoded to WAV via ffmpeg first in play_speech() -
that decode happens before the timing-critical clock starts, so its own
(larger, but one-time, non-realtime) cost can't affect sync."""

_KEEPALIVE_DURATION: float = 2.0
"""Length of the looped silent clip _SinkKeepAlive plays to hold the
output sink "RUNNING" rather than "SUSPENDED" between conversation turns -
see that class's docstring for why."""


class _SinkKeepAlive:
    """Loops silence in the background for as long as this is open, so the
    output sink never auto-suspends between conversation turns.

    An idle PulseAudio/PipeWire sink can take anywhere from a few hundred
    milliseconds to over a second to wake back up once a real stream
    connects - the same idle-sink behavior this project's README already
    documents for audio.py's capture side. That wake-up delay is what
    makes the *cold* case fundamentally unpredictable no matter how well
    tuned _PLAYBACK_STARTUP_LATENCY is for the *warm* case - this sidesteps
    the problem by keeping the sink warm for the whole session instead of
    trying to compensate for a wake-up delay that varies run to run.
    """

    def __init__(self) -> None:
        """Create (but do not yet start) the keep-alive loop."""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._path: str | None = None

    def _loop(self) -> None:
        """Replay the silent clip back-to-back until stopped."""
        assert self._path is not None  # Guaranteed by __enter__() running first.
        while not self._stop.is_set():
            player = subprocess.Popen(
                ["paplay", "--latency-msec=20", self._path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            player.wait()

    def __enter__(self) -> _SinkKeepAlive:
        """Write a short silent WAV and start looping it in a background thread."""
        fd, self._path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        silence = np.zeros(int(_KEEPALIVE_DURATION * config.SAMPLE_RATE), dtype=np.int16)
        with wave.open(self._path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(config.SAMPLE_RATE)
            wav_file.writeframes(silence.tobytes())
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Stop the loop and remove the temp file."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._path is not None:
            os.unlink(self._path)


def record_until_enter(device: str, backend: str = "pulse") -> np.ndarray:
    """Record mono float32 audio from ``device`` until Enter is pressed.

    ``backend`` is "pulse" (default) or "alsa" - see audio.AudioStream.
    Used as-is for the normal (laptop) flow; also available as --pi
    --keyboard-trigger's fallback, for debugging the Pi's audio/gamepad
    paths independently of each other, without needing the gamepad at all.

    Returns the concatenated samples (empty if Enter was pressed
    immediately, before any audio arrived).
    """
    print("Recording... press Enter to stop.")
    stop = threading.Event()

    def _wait_for_enter() -> None:
        input()
        stop.set()

    waiter = threading.Thread(target=_wait_for_enter, daemon=True)
    waiter.start()

    blocks: list[np.ndarray] = []
    with audio.AudioStream(device=device, backend=backend) as stream:
        while not stop.is_set():
            try:
                blocks.append(stream.read_block(timeout=0.1))
            except queue.Empty:
                continue
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def record_while_held(device: str) -> np.ndarray:
    """--pi mode's trigger: record from ``device`` (an ALSA device string -
    see config.PI_MIC_DEVICE) for as long as gamepad.TALK_BUTTON stays
    held, starting when it's first pressed and stopping on release - the
    gamepad equivalent of record_until_enter(), for kids to use without a
    keyboard.
    """
    import gamepad  # Local: gamepad.py imports pygame, only installed on the Pi
    # (apt's python3-pygame, via the venv's --system-site-packages) - never on a
    # laptop, where --pi mode (and this function) is never actually used.

    print("Waiting for the talk button to be pressed...")
    while not gamepad.talk_button_held():
        time.sleep(0.02)
    print("Recording... (release the button to stop)")

    blocks: list[np.ndarray] = []
    with audio.AudioStream(device=device, backend="alsa") as stream:
        while gamepad.talk_button_held():
            try:
                blocks.append(stream.read_block(timeout=0.1))
            except queue.Empty:
                continue
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def samples_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode mono float32 samples (-1.0 to 1.0) as 16-bit PCM WAV bytes,
    for ElevenLabs' speech-to-text upload.
    """
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16.tobytes())
    return buffer.getvalue()


def get_reply(chat: Chat, user_text: str) -> str:
    """Send ``user_text`` to the running Gemini chat session and return the
    unicorn's in-character reply. ``chat`` already carries the system
    instruction/history, so this is just one turn.
    """
    text = chat.send_message(user_text).text
    if text is None:
        raise RuntimeError("Gemini returned no reply text (response may have been blocked).")
    return text


def _hue_to_rgb(hue: float) -> tuple[int, int, int]:
    """Convert a hue (0-1) at full saturation/value to an (r, g, b) triple."""
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def effect_color(
    effect: tuple[str, str] | None,
    now: float,
    base_color: tuple[int, int, int],
    group_name: str,
) -> tuple[int, int, int]:
    """Return the color ``group_name`` (a body group, or "Head" for the
    mouth, which mirrors Head's color) should show right now, given the
    active (name, target) effect (or None, which just passes ``base_color``
    through unchanged) - see config.EFFECT_NAMES/config.EFFECT_TARGETS.

    If the effect's target isn't "all" and doesn't match ``group_name``,
    this group sits the effect out and keeps ``base_color`` - that's how a
    tag like "[sparkle:Horn]" only lights up the Horn.

    [sparkle] picks a random bright color per call (so each targeted group
    flickers independently - this is called once per group per frame);
    [rainbow] cycles hue smoothly, the same for every targeted group at a
    given instant; [glow] modulates ``base_color``'s own brightness up and
    down, so it reads as "the unicorn's own colors breathing" rather than a
    new palette; [flash] is just solid white.
    """
    if effect is None:
        return base_color
    effect_name, target = effect
    if target != "all" and target != group_name:
        return base_color
    if effect_name == "sparkle":
        return random.choice(config.EFFECT_SPARKLE_COLORS)
    if effect_name == "rainbow":
        hue = (now / config.EFFECT_RAINBOW_PERIOD) % 1.0
        return _hue_to_rgb(hue)
    if effect_name == "glow":
        phase = (now % config.EFFECT_GLOW_PERIOD) / config.EFFECT_GLOW_PERIOD
        glow_factor = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(2 * math.pi * phase))
        return tuple(int(channel * glow_factor) for channel in base_color)
    if effect_name == "flash":
        return (255, 255, 255)
    return base_color  # An unrecognized name (shouldn't happen - extract_effect_tags() only
    # ever returns names from config.EFFECT_NAMES) - fail safe to the plain color.


def build_talk_frame(
    layout: UnicornLayout,
    mouth_open: bool,
    eye_open: bool,
    brightness: float,
    band_config: dict[str, config.BandConfig] | None = None,
    effect: tuple[str, str] | None = None,
    now: float = 0.0,
) -> bytes:
    """Render one DDP payload for talk mode: every band_config group
    (Head, Mane, Trim, Hat, Horn) lit at its peak color - the whole
    unicorn, not just the face - plus the mouth/eye animated live on top
    (mouth mirrors Head's color, same convention as equalizer.py's
    build_frame()).

    ``band_config`` defaults to config.BAND_CONFIG (hand-picked colors) but
    can be config.official_band_config() instead - see run_conversation()'s
    ``use_official_colors``. If ``effect`` (a (name, target) pair - see
    config.EFFECT_NAMES/config.EFFECT_TARGETS/voice.py's
    SpeechResult.effect_at()) is set, each body group's and the mouth's
    color is run through effect_color() first, using ``now`` as that
    effect's animation clock - groups outside the effect's target are left
    at their plain color (see effect_color()). The eye's blink is always
    left alone, so it keeps blinking independently of any effect.
    """
    band_config = band_config if band_config is not None else config.BAND_CONFIG
    frame = np.zeros((layout.total_pixels, 3), dtype=np.uint8)

    def paint(group_name: str, color: tuple[int, int, int]) -> None:
        nodes = layout.groups[group_name]
        pixel_indices = [layout.node_to_pixel_index(n) for n in nodes]
        frame[pixel_indices] = (np.array(color, dtype=np.float64) * brightness).astype(np.uint8)

    for name, (_, _, color) in band_config.items():
        if name == "Eye":
            paint("Open Eye" if eye_open else "Closed Eye", color)
        else:
            paint(name, effect_color(effect, now, color, name))

    _, _, mouth_color = band_config["Head"]
    mouth_group = "Open Mouth" if mouth_open else "Closed Mouth"
    paint(mouth_group, effect_color(effect, now, mouth_color, "Head"))
    return frame.tobytes()


def play_speech(
    speech: SpeechResult,
    layout: UnicornLayout,
    sender: DDPSender | None,
    brightness: float,
    blink: BlinkState,
    band_config: dict[str, config.BandConfig] | None = None,
    alsa_speaker: str | None = None,
) -> None:
    """Play ``speech``'s audio aloud while streaming mouth/eye frames timed
    to its open intervals, until playback finishes.

    Neither `paplay` nor `aplay` decode MP3 (what ElevenLabs returns)
    themselves, so this decodes to WAV via ffmpeg first - before the
    timing-critical clock starts, so that one-time decode cost can't
    affect sync - then plays the WAV with `paplay --latency-msec=20`, or,
    if ``alsa_speaker`` is given (an ALSA device string - see
    config.PI_SPEAKER_DEVICE, for --pi mode), `aplay -D <alsa_speaker>`
    instead, since the Pi has no PulseAudio/PipeWire compatibility layer.
    See _PLAYBACK_STARTUP_LATENCY for why paplay replaced ffplay for the
    non-Pi path; that constant hasn't been separately re-measured for
    aplay on the Pi yet (same method - a loopback-captured click test -
    would need repeating there once this is actually tested on hardware).
    """
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_file:
        mp3_file.write(speech.audio_bytes)
        mp3_path = mp3_file.name
    wav_path = mp3_path[: -len(".mp3")] + ".wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path, wav_path], check=True)

    if alsa_speaker is not None:
        player = subprocess.Popen(
            ["aplay", "-D", alsa_speaker, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    else:
        player = subprocess.Popen(["paplay", "--latency-msec=20", wav_path])
    start = time.monotonic() + _PLAYBACK_STARTUP_LATENCY
    frame_interval = 1.0 / config.FPS
    next_send = start
    end_at = start + speech.duration() + _PLAYBACK_END_PADDING

    try:
        while time.monotonic() < end_at:
            now = time.monotonic()
            if now >= next_send:
                next_send = now + frame_interval
                elapsed = now - start
                mouth_open = speech.mouth_open_at(elapsed)
                eye_open = blink.is_open(now)
                effect = speech.effect_at(elapsed)
                if sender is not None:
                    sender.send_frame(
                        build_talk_frame(
                            layout, mouth_open, eye_open, brightness, band_config, effect, now
                        )
                    )
    finally:
        player.wait(timeout=5)
        os.unlink(mp3_path)
        os.unlink(wav_path)


def run_conversation(
    device: str | None,
    controller_ip: str | None,
    brightness: float,
    use_official_colors: bool,
    dry_run: bool,
    pi: bool = False,
    pi_keyboard: bool = False,
) -> None:
    """Loop: push-to-talk record -> transcribe -> Claude reply -> speak,
    until interrupted.

    If ``pi`` is set (--pi mode, for running directly on the Raspberry Pi
    next to the controller, no laptop needed - see README's "Talking to
    the Unicorn" section), the trigger is a held gamepad button (see
    gamepad.py) instead of pressing Enter, ``device``/audio playback go
    through ALSA (config.PI_MIC_DEVICE/config.PI_SPEAKER_DEVICE) instead
    of PulseAudio, and _SinkKeepAlive is skipped - that's a PipeWire/
    PulseAudio idle-sink workaround with no ALSA-direct equivalent needed.

    ``pi_keyboard`` (only meaningful with ``pi``) swaps the gamepad for
    Enter, while keeping everything else about --pi mode (ALSA audio, no
    _SinkKeepAlive) - for debugging the Pi's audio/transcription/Gemini
    path independently of the gamepad, without needing it at all.
    """
    layout = load_layout()
    ip = controller_ip or layout.controller_ip
    band_config = config.official_band_config() if use_official_colors else config.BAND_CONFIG
    print(f"Target: {ip} (DDP)" if not dry_run else "Dry run: not sending DDP packets")
    print(f"Colors: {'official show colors' if use_official_colors else 'hand-picked (config.BAND_CONFIG)'}")

    if pi and (not pi_keyboard):
        import gamepad  # Local - see record_while_held()'s own comment on this.

        if not gamepad.setup():
            raise RuntimeError("--pi mode requires a USB gamepad, but none was detected.")
        print(f"Gamepad connected - hold button {gamepad.TALK_BUTTON} to talk.")

    client = genai.Client()
    chat = client.chats.create(
        model=config.GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=config.UNICORN_SYSTEM_PROMPT,
            max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
        ),
    )
    sender = None if dry_run else DDPSender(ip)
    blink = BlinkState()
    if sender is not None:
        sender.send_frame(build_talk_frame(layout, False, True, brightness, band_config))

    def _one_turn() -> None:
        if pi and (not pi_keyboard):
            samples = record_while_held(config.PI_MIC_DEVICE)
        elif pi:
            input("\nPress Enter, then speak to the unicorn...")
            samples = record_until_enter(config.PI_MIC_DEVICE, backend="alsa")
        else:
            assert device is not None  # Guaranteed by main()'s own validation.
            input("\nPress Enter, then speak to the unicorn...")
            samples = record_until_enter(device)
        if len(samples) == 0:
            return

        print("Transcribing...")
        user_text = voice.transcribe(samples_to_wav_bytes(samples, config.SAMPLE_RATE))
        if not user_text.strip():
            print("(heard nothing - try again)")
            return
        print(f"You said: {user_text}")

        reply = get_reply(chat, user_text)
        print(f"Unicorn: {reply}")

        speech = voice.synthesize(reply)
        alsa_speaker = config.PI_SPEAKER_DEVICE if pi else None
        play_speech(speech, layout, sender, brightness, blink, band_config, alsa_speaker)

    try:
        if pi:
            while True:
                try:
                    _one_turn()
                except Exception as e:
                    # --pi mode is meant to run unattended for kids - one bad
                    # turn (a transient API error, a bad transcription, etc.)
                    # shouldn't take down the whole session and require
                    # someone to physically restart it. The non-pi path below
                    # doesn't have this: there's a person at the terminal who
                    # can see and fix a real error, so it's left to crash.
                    print(f"(turn failed: {e} - try again)")
        else:
            with _SinkKeepAlive():
                while True:
                    _one_turn()
    except KeyboardInterrupt:
        pass
    finally:
        if sender is not None:
            sender.close()


def run_replay(
    path: Path,
    controller_ip: str | None,
    brightness: float,
    use_official_colors: bool,
    dry_run: bool,
    pi: bool = False,
) -> None:
    """Replay a fixture saved by voice.save_fixture() over and over (press
    Enter to replay, Ctrl+C to stop) - for tuning the mouth timeline
    (voice.py's _open_intervals_from_alignment()/config.MOUTH_MERGE_GAP)
    against the same real audio repeatedly, without new API calls or
    different text each time.

    ``pi`` plays through ALSA (config.PI_SPEAKER_DEVICE) instead of
    PulseAudio and skips _SinkKeepAlive, same as run_conversation()'s own
    ``pi`` - this still uses Enter to replay (not the gamepad), since it's
    a tuning tool for a person at a terminal, not a kid at the prop.
    """
    layout = load_layout()
    ip = controller_ip or layout.controller_ip
    band_config = config.official_band_config() if use_official_colors else config.BAND_CONFIG
    print(f"Target: {ip} (DDP)" if not dry_run else "Dry run: not sending DDP packets")

    speech = voice.load_fixture(path)
    sender = None if dry_run else DDPSender(ip)
    blink = BlinkState()
    alsa_speaker = config.PI_SPEAKER_DEVICE if pi else None
    if sender is not None:
        sender.send_frame(build_talk_frame(layout, False, True, brightness, band_config))

    def _one_replay() -> None:
        input("\nPress Enter to replay (Ctrl+C to stop)...")
        play_speech(speech, layout, sender, brightness, blink, band_config, alsa_speaker)

    try:
        if pi:
            while True:
                _one_replay()
        else:
            with _SinkKeepAlive():
                while True:
                    _one_replay()
    except KeyboardInterrupt:
        pass
    finally:
        if sender is not None:
            sender.close()


def run_mouth_test(
    controller_ip: str | None, brightness: float, use_official_colors: bool, dry_run: bool
) -> None:
    """Toggle just the mouth open/closed every second, indefinitely - no
    TTS, Gemini, or alignment logic involved at all.

    An isolated hardware check: confirms (or refutes) the basic assumption
    every mouth-timing algorithm in this file depends on - that "Open
    Mouth" actually looks open and "Closed Mouth" actually looks closed on
    the real prop - before spending more effort tuning timing logic built
    on top of that assumption.
    """
    layout = load_layout()
    ip = controller_ip or layout.controller_ip
    band_config = config.official_band_config() if use_official_colors else config.BAND_CONFIG
    print(f"Target: {ip} (DDP)" if not dry_run else "Dry run: not sending DDP packets")
    sender = None if dry_run else DDPSender(ip)
    mouth_open = False
    try:
        while True:
            mouth_open = not mouth_open
            print("mouth: OPEN" if mouth_open else "mouth: CLOSED")
            if sender is not None:
                sender.send_frame(build_talk_frame(layout, mouth_open, True, brightness, band_config))
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        if sender is not None:
            sender.close()


def main() -> None:
    """Parse CLI arguments and run the voice chat loop (or list audio devices)."""
    # override=True: this Pi also runs a separate project (cauldron) whose shell
    # config exports its own ELEVENLABS_API_KEY, which would otherwise silently
    # shadow this project's .env value (load_dotenv() does not override an
    # already-set environment variable by default).
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio devices and exit."
    )
    parser.add_argument(
        "--device",
        default=None,
        help="PulseAudio/PipeWire microphone source name to record from (use "
        "--list-devices to find it) - required, since there's no sensible "
        "default microphone the way there is a default loopback source.",
    )
    parser.add_argument(
        "--controller-ip",
        default=None,
        help="Override the controller IP parsed from xlights_networks.xml.",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        default=config.TALK_BRIGHTNESS,
        help="0-1 overall power cap for the whole unicorn while talking.",
    )
    parser.add_argument(
        "--official-colors",
        action="store_true",
        help="Use each group's dominant color from the real show's rendered .fseq "
        "(see show_colors.py) instead of config.BAND_CONFIG's hand-picked ones - "
        "same flag as equalizer.py's.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip sending DDP packets (audio/Claude/ElevenLabs calls still happen).",
    )
    parser.add_argument(
        "--save-fixture",
        type=Path,
        default=None,
        help="Synthesize --text once via ElevenLabs, cache it at this path, and exit - "
        "see --replay to test against it repeatedly afterwards.",
    )
    parser.add_argument(
        "--text",
        default=None,
        help="Text to synthesize - required with --save-fixture.",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        help="Replay a fixture saved by --save-fixture over and over (press Enter to "
        "repeat), instead of the mic/Gemini conversation loop - for tuning the mouth "
        "timeline against the same real audio without new API calls each time.",
    )
    parser.add_argument(
        "--mouth-test",
        action="store_true",
        help="Toggle just the mouth open/closed every second, forever - an isolated "
        "hardware check with no TTS/Gemini/alignment logic involved, to confirm "
        "'Open Mouth'/'Closed Mouth' actually look open/closed before trusting any "
        "timing logic built on top of that assumption.",
    )
    parser.add_argument(
        "--pi",
        action="store_true",
        help="Run directly on the Raspberry Pi next to the controller, no laptop "
        "needed: a held USB gamepad button (see gamepad.py) triggers recording "
        "instead of pressing Enter, and audio capture/playback go through ALSA "
        "(config.PI_MIC_DEVICE/config.PI_SPEAKER_DEVICE) instead of PulseAudio. "
        "--device is ignored in this mode.",
    )
    parser.add_argument(
        "--pi-keyboard",
        action="store_true",
        help="With --pi, swap the gamepad for pressing Enter, while keeping "
        "everything else about --pi mode (ALSA audio) - for debugging the "
        "Pi's audio/transcription/Gemini path independently of the gamepad.",
    )
    args = parser.parse_args()

    if args.list_devices:
        audio.list_devices()
        return

    if args.mouth_test:
        run_mouth_test(
            controller_ip=args.controller_ip,
            brightness=max(0.0, min(1.0, args.brightness)),
            use_official_colors=args.official_colors,
            dry_run=args.dry_run,
        )
        return

    if args.save_fixture is not None:
        if args.text is None:
            parser.error("--save-fixture requires --text")
        voice.save_fixture(args.text, args.save_fixture)
        print(f"Wrote {args.save_fixture}")
        return

    if args.replay is not None:
        run_replay(
            path=args.replay,
            controller_ip=args.controller_ip,
            brightness=max(0.0, min(1.0, args.brightness)),
            use_official_colors=args.official_colors,
            dry_run=args.dry_run,
            pi=args.pi,
        )
        return

    if (not args.pi) and (args.device is None):
        parser.error("--device is required (pass a microphone source - see --list-devices)")

    run_conversation(
        device=args.device,
        controller_ip=args.controller_ip,
        brightness=max(0.0, min(1.0, args.brightness)),
        use_official_colors=args.official_colors,
        dry_run=args.dry_run,
        pi=args.pi,
        pi_keyboard=args.pi_keyboard,
    )


if __name__ == "__main__":
    sys.exit(main())

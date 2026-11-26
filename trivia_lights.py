"""Non-speech light animations for the trivia game: an idle "thinking/
waiting" rainbow, a quick correct/incorrect color flash, and a round-
winner flourish - all built directly on talk.build_talk_frame()/
ddp.DDPSender, not a ported light_control.py (cauldron's SPI LED-ring
animation suite - celebrate()/fizzle()/victory_swirl()/trigger_splash()
- doesn't map onto this project's irregular pixel-grid unicorn shape the
way it does onto a circular ring).

Hard rule, load-bearing for IdleLights' own API shape: only one thing may
ever be sending DDP frames at a time. talk.play_speech() has its own
internal frame loop timed to mouth movement; if this module's idle loop
ran concurrently with it, both would race to paint the same pixel buffer
over the same UDP socket each tick (visible tearing, not a crash, but a
real bug - the two don't share the "LEDs vs. speaker are independent
hardware" property cauldron's own idle rainbow/audio pairing relies on).
trivia_game.py is responsible for calling IdleLights.stop() and
*awaiting* it before every talk.play_speech() call, and only calling
.start() again once that call returns.

flash_color()/victory_flourish() are themselves safe to await directly
(no executor needed) - each frame is followed by a plain asyncio.sleep(),
not a busy-spin, so the event loop stays responsive to WebSocket traffic
throughout.
"""

from __future__ import annotations

import asyncio
import time

import config
from blink import BlinkState
from ddp import DDPSender
from layout import UnicornLayout
from talk import build_talk_frame

_FRAME_INTERVAL = 1.0 / config.FPS


class IdleLights:
    """A background asyncio.Task that repeatedly paints ``effect`` across
    the whole unicorn until stopped - the "waiting for an answer"/
    "thinking" animation played between spoken lines.

    An asyncio.Task, not a thread, so it's natively cancellable via
    asyncio.Task.cancel() - see this module's own docstring for why
    nothing else may send DDP frames while this is running.
    """

    def __init__(
        self,
        layout: UnicornLayout,
        sender: DDPSender | None,
        brightness: float,
        band_config: dict[str, config.BandConfig],
        blink: BlinkState,
        effect: tuple[str, str, str | None] = ("rainbow", "all", None),
    ) -> None:
        """``sender`` is None in --dry-run mode (see trivia_game.py's own
        --dry-run) - the loop still runs on schedule (so timing-dependent
        callers behave the same either way), it just skips the actual
        DDP send, same convention as talk.play_speech()'s own ``sender``
        parameter."""
        self._layout = layout
        self._sender = sender
        self._brightness = brightness
        self._band_config = band_config
        self._blink = blink
        self._effect = effect
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            now = time.monotonic()
            if self._sender is not None:
                self._sender.send_frame(
                    build_talk_frame(
                        self._layout,
                        False,
                        self._blink.is_open(now),
                        self._brightness,
                        self._band_config,
                        self._effect,
                        now,
                    )
                )
            await asyncio.sleep(_FRAME_INTERVAL)

    def start(self) -> None:
        """Start looping, if not already running. Idempotent."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the loop and wait for it to actually stop - callers
        must await this (not just call it) before starting anything else
        that sends DDP frames, per this module's own hard rule."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None


async def flash_color(
    layout: UnicornLayout,
    sender: DDPSender | None,
    brightness: float,
    band_config: dict[str, config.BandConfig],
    blink: BlinkState,
    color_name: str,
    *,
    duration: float = 1.2,
) -> None:
    """Hold one solid color across the whole unicorn for ``duration``
    seconds - the correct/incorrect outcome cue for a single attempt.
    Caller's IdleLights must already be stopped (and awaited) before
    this runs - see this module's own docstring. ``sender`` is None in
    --dry-run mode - see IdleLights.__init__()'s own note.
    """
    effect = ("color", "all", color_name)
    end_at = time.monotonic() + duration
    while time.monotonic() < end_at:
        now = time.monotonic()
        if sender is not None:
            sender.send_frame(
                build_talk_frame(
                    layout, False, blink.is_open(now), brightness, band_config, effect, now
                )
            )
        await asyncio.sleep(_FRAME_INTERVAL)


async def victory_flourish(
    layout: UnicornLayout,
    sender: DDPSender | None,
    brightness: float,
    band_config: dict[str, config.BandConfig],
    blink: BlinkState,
    color_name: str,
    *,
    cycles: int = 4,
    cycle_duration: float = 0.4,
) -> None:
    """Alternate the winning team's color and a bright sparkle for a few
    cycles - the round-winner flourish, played once after the finale.
    Caller's IdleLights must already be stopped (and awaited) before
    this runs - see this module's own docstring. ``sender`` is None in
    --dry-run mode - see IdleLights.__init__()'s own note.
    """
    effects: tuple[tuple[str, str, str | None], tuple[str, str, str | None]] = (
        ("color", "all", color_name),
        ("sparkle", "all", None),
    )
    for _ in range(cycles):
        for effect in effects:
            end_at = time.monotonic() + cycle_duration
            while time.monotonic() < end_at:
                now = time.monotonic()
                if sender is not None:
                    sender.send_frame(
                        build_talk_frame(
                            layout,
                            False,
                            blink.is_open(now),
                            brightness,
                            band_config,
                            effect,
                            now,
                        )
                    )
                await asyncio.sleep(_FRAME_INTERVAL)

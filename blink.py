"""Shared periodic-blink state, used by both equalizer.py and talk.py so the
eye has the same natural, randomized blink regardless of what's driving the
rest of the unicorn.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import config


@dataclass
class BlinkState:
    """Tracks a simple periodic blink: the eye stays open and briefly closes
    at randomized intervals (config.BLINK_MIN_INTERVAL-BLINK_MAX_INTERVAL),
    like a natural blink rather than a fixed metronome.
    """

    next_blink: float = field(default_factory=lambda: time.monotonic() + random.uniform(
        config.BLINK_MIN_INTERVAL, config.BLINK_MAX_INTERVAL
    ))
    closed_until: float = 0.0

    def is_open(self, now: float) -> bool:
        """Return whether the eye should be open at time ``now`` (a
        ``time.monotonic()`` reading), starting a new blink if one is due.
        """
        if (now >= self.next_blink) and (now >= self.closed_until):
            self.closed_until = now + config.BLINK_DURATION
            self.next_blink = now + random.uniform(config.BLINK_MIN_INTERVAL, config.BLINK_MAX_INTERVAL)
        return now >= self.closed_until

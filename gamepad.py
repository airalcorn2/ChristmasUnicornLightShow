"""USB gamepad input, for triggering talk.py's push-to-talk on the Pi
without a keyboard - mirrors cauldron's own controller.py (pygame.joystick),
simplified to the one thing this project needs: whether one specific
button is currently held down.
"""

from __future__ import annotations

import pygame

TALK_BUTTON: int = 2
"""Button index for the USB gamepad used to trigger talk.py's push-to-talk
(the same physical pad cauldron uses) - confirmed by listening for a live
press, not guessed: pressing it printed "Button pressed: index 2" via
pygame's JOYBUTTONDOWN event. A different gamepad would need this
re-confirmed the same way - run this module directly to check."""

_joystick: pygame.joystick.JoystickType | None = None


def setup() -> bool:
    """Detect and initialize the first connected USB gamepad.

    Returns False (not an exception) if none is connected, so callers can
    report that clearly and fall back to another trigger - same
    convention as cauldron's own controller.py.
    """
    global _joystick
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        return False
    _joystick = pygame.joystick.Joystick(0)
    _joystick.init()
    return True


def talk_button_held() -> bool:
    """Return whether TALK_BUTTON is currently held down.

    Pumps pygame's event queue as a side effect (SDL's joystick button
    state only updates when the queue is processed) - call this
    regularly, e.g. in a polling loop, rather than mixing it with other
    direct pygame.event.get() calls elsewhere, which would otherwise race
    for the same queue.
    """
    assert _joystick is not None, "call setup() first"
    pygame.event.pump()
    return bool(_joystick.get_button(TALK_BUTTON))


if __name__ == "__main__":
    import time

    if not setup():
        print("No USB gamepad found.")
    else:
        print(f"Gamepad connected: {_joystick.get_name()!r}.")
        print(f"Watching button {TALK_BUTTON} - press/release it (Ctrl+C to stop)...")
        previous = False
        try:
            while True:
                held = talk_button_held()
                if held != previous:
                    print("held" if held else "released")
                    previous = held
                time.sleep(0.02)
        except KeyboardInterrupt:
            pass

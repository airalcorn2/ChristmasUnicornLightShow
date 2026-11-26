"""Drives the Christmas Unicorn's lights as a live-audio equalizer.

Captures audio, extracts a smoothed energy level per frequency band, colors
each named light group (Hat, Horn, Mane, ...) by its band's energy, and
streams the resulting pixel frame to the Genius Pixel 16 controller over DDP.

Run directly:

    .venv/bin/python equalizer.py --list-devices   # Find a source name.
    .venv/bin/python equalizer.py --dry-run        # Print levels, no network traffic.
    .venv/bin/python equalizer.py                  # Send DDP to the real controller.
"""

from __future__ import annotations

import argparse
import queue
import sys
import time

import numpy as np

import audio
import config
from blink import BlinkState
from ddp import DDPSender
from layout import UnicornLayout, load_layout


def build_frame(
    layout: UnicornLayout,
    levels: dict[str, float],
    eye_open: bool,
    mouth_open: bool,
    band_config: dict[str, config.BandConfig] | None = None,
    brightness: float = 1.0,
) -> bytes:
    """Render one DDP payload: each configured band's pixels set to its peak
    color scaled by its current level (0-1) and by ``brightness`` (0-1, an
    overall safety/power cap applied on top of the level).

    ``band_config`` defaults to ``config.BAND_CONFIG`` (the hand-picked
    colors) but can be ``config.official_band_config()`` instead, to use the
    real show's own colors - see run()'s ``use_official_colors``. Either
    way, see ``config.BAND_CONFIG``'s docstring for how "Eye" (``eye_open``
    picks which of "Open Eye"/"Closed Eye" is actually lit) and the mouth
    (not a band at all; it mirrors Head's own color/level, with
    ``mouth_open`` picking "Open Mouth" or "Closed Mouth") are handled.
    ``mouth_open`` should come from the fast-release
    ``BandAnalyzer.gate_levels``, not ``levels`` itself - see run()'s call
    site.
    """
    band_config = band_config if band_config is not None else config.BAND_CONFIG
    frame = np.zeros((layout.total_pixels, 3), dtype=np.uint8)

    def paint(group_name: str, peak_color: tuple[int, int, int], level: float) -> None:
        nodes = layout.groups[group_name]
        color = np.array(peak_color, dtype=np.float64) * level * brightness
        pixel_indices = [layout.node_to_pixel_index(n) for n in nodes]
        frame[pixel_indices] = color.astype(np.uint8)

    for name, (_, _, peak_color) in band_config.items():
        group_name = ("Open Eye" if eye_open else "Closed Eye") if name == "Eye" else name
        paint(group_name, peak_color, levels[name])

    _, _, head_color = band_config["Head"]
    mouth_group = "Open Mouth" if mouth_open else "Closed Mouth"
    paint(mouth_group, head_color, levels["Head"])

    return frame.tobytes()


def run(
    device: str | None,
    controller_ip: str | None,
    fps: int,
    brightness: float,
    use_official_colors: bool,
    dry_run: bool,
) -> None:
    """Capture audio and drive the lights (or print levels) until interrupted.

    ``device`` is a PulseAudio/PipeWire source name, or ``None`` for the
    default loopback source. ``controller_ip`` overrides the IP parsed from
    xlights_networks.xml. ``brightness`` (0-1) is an overall power cap applied
    on top of each band's own level. ``use_official_colors`` swaps in
    ``config.official_band_config()`` (the real show's own colors) instead of
    ``config.BAND_CONFIG``'s hand-picked ones. In ``dry_run``, no DDP packets
    are sent - band levels are printed instead.
    """
    layout = load_layout()
    ip = controller_ip or layout.controller_ip
    band_config = config.official_band_config() if use_official_colors else config.BAND_CONFIG
    print(f"Groups: {', '.join(layout.groups)}")
    print(f"Total pixels: {layout.total_pixels}")
    print(f"Colors: {'official show colors' if use_official_colors else 'hand-picked (config.BAND_CONFIG)'}")
    print(f"Target: {ip} (DDP)" if not dry_run else "Dry run: not sending DDP packets")

    analyzer = audio.BandAnalyzer()
    blink = BlinkState()
    mouth_open = False
    sender = None if dry_run else DDPSender(ip)
    frame_interval = 1.0 / fps

    if sender is not None:
        # Light the whole unicorn immediately, rather than leaving it dark
        # until the first real audio block arrives - AudioStream's own
        # startup (waking an idle sink) can take a second or two (see
        # audio.py/README's Notes), and real band levels take a moment to
        # ramp up past NOISE_FLOOR even once audio is flowing.
        initial_levels = {name: 1.0 for name in band_config}
        sender.send_frame(
            build_frame(layout, initial_levels, True, False, band_config, brightness)
        )

    try:
        with audio.AudioStream(device=device) as stream:
            next_send = time.monotonic()
            while True:
                try:
                    block = stream.read_block(timeout=1.0)
                except queue.Empty:
                    # No audio right now (e.g., the system sink just woke up from
                    # idle, or the source briefly glitched) - keep looping instead
                    # of crashing; BandAnalyzer's RELEASE will decay levels to 0.
                    continue
                levels = analyzer.process(block)
                # The mouth-open decision uses the fast-release gate level,
                # not `levels` itself (slow-release, tuned for smooth visual
                # brightness) - otherwise, during continuous vocal energy,
                # the mouth gets stuck "open" because the slow envelope
                # rarely decays back below the threshold between phrases.
                # Hysteresis (two thresholds, not one) avoids flutter when
                # the gate level hovers right at the line - see config.py's
                # comments. Gated off MOUTH_GATE_BANDS (vocal formant range)
                # rather than bass, so the mouth tracks singing, not the beat.
                gate_levels = analyzer.gate_levels
                vocal_gate = sum(gate_levels[name] for name in config.MOUTH_GATE_BANDS) / len(
                    config.MOUTH_GATE_BANDS
                )
                if mouth_open:
                    mouth_open = vocal_gate > config.MOUTH_CLOSE_THRESHOLD
                else:
                    mouth_open = vocal_gate > config.MOUTH_OPEN_THRESHOLD

                now = time.monotonic()
                if now >= next_send:
                    next_send = now + frame_interval
                    eye_open = blink.is_open(now)
                    if dry_run:
                        bars = " ".join(f"{name}:{levels[name]:.2f}" for name in levels)
                        eye = "open" if eye_open else "closed"
                        mouth = "open" if mouth_open else "closed"
                        print(f"{bars} vocal_gate:{vocal_gate:.2f} eye:{eye} mouth:{mouth}", end="\r")
                    else:
                        assert sender is not None
                        pixel_bytes = build_frame(
                            layout, levels, eye_open, mouth_open, band_config, brightness
                        )
                        sender.send_frame(pixel_bytes)
    except KeyboardInterrupt:
        pass
    finally:
        if sender is not None:
            sender.close()


def main() -> None:
    """Parse CLI arguments and run the equalizer (or list audio devices)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio devices and exit."
    )
    parser.add_argument(
        "--device",
        default=None,
        help="PulseAudio/PipeWire source name to record from (use --list-devices to "
        "find it). Defaults to the default sink's monitor, i.e., this machine's "
        "audio output.",
    )
    parser.add_argument(
        "--controller-ip",
        default=None,
        help="Override the controller IP parsed from xlights_networks.xml.",
    )
    parser.add_argument("--fps", type=int, default=config.FPS, help="Pixel update rate.")
    parser.add_argument(
        "--brightness",
        type=float,
        default=0.3,
        help="0-1 overall power cap on top of each band's own level (defaults to a dim "
        "0.3, matching test_lights.py's safety default, so a first run doesn't blast "
        "the string at full power).",
    )
    parser.add_argument(
        "--official-colors",
        action="store_true",
        help="Use each group's dominant color from the real show's rendered .fseq "
        "(see show_colors.py) instead of config.BAND_CONFIG's hand-picked ones.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print band levels instead of sending DDP packets (no hardware needed).",
    )
    args = parser.parse_args()

    if args.list_devices:
        audio.list_devices()
        return

    run(
        device=args.device,
        controller_ip=args.controller_ip,
        fps=args.fps,
        brightness=max(0.0, min(1.0, args.brightness)),
        use_official_colors=args.official_colors,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())

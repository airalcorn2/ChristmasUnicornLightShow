"""Hardware verification tool for the Christmas Unicorn's lights.

Lets you confirm the pixel layout/DDP wiring is actually correct on the real
hardware before trusting it to the audio-reactive equalizer: light up each
named group one at a time, chase through every individual pixel, do a
full-string solid-color/brightness check, or turn everything off.

Run directly:

    .venv/bin/python test_lights.py groups                 # Cycle every named group.
    .venv/bin/python test_lights.py groups --group Horn     # Just one group.
    .venv/bin/python test_lights.py chase                   # One pixel at a time, whole string.
    .venv/bin/python test_lights.py nodes --nodes 1-10      # Rainbow gradient over specific nodes.
    .venv/bin/python test_lights.py all                     # Whole string, one color.
    .venv/bin/python test_lights.py off                     # Blackout.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from ddp import DDPSender
from layout import UnicornLayout, load_layout
from nodes import parse_node_range, rainbow_colors

RGB = tuple[int, int, int]

WHITE: RGB = (255, 255, 255)


def parse_color(text: str) -> RGB:
    """Parse a CLI color argument, e.g., ``"ff0000"`` or ``"255,0,0"``."""
    text = text.strip().lstrip("#")
    if "," in text:
        parts = [int(p) for p in text.split(",")]
    else:
        parts = [int(text[i : i + 2], 16) for i in (0, 2, 4)]
    if (len(parts) != 3) or (any(not 0 <= p <= 255 for p in parts)):
        raise argparse.ArgumentTypeError(f"invalid color: {text!r} (use e.g., 'ff0000' or '255,0,0')")
    return (parts[0], parts[1], parts[2])


def scaled(color: RGB, brightness: float) -> RGB:
    """Scale each channel of ``color`` by ``brightness`` (0-1)."""
    r, g, b = color
    return (int(r * brightness), int(g * brightness), int(b * brightness))


def send_solid(sender: DDPSender | None, total_pixels: int, color: RGB, dry_run: bool) -> None:
    """Send (or print, if ``dry_run``) a frame with every pixel set to ``color``."""
    frame = np.zeros((total_pixels, 3), dtype=np.uint8)
    frame[:] = color
    if dry_run:
        print(f"[dry-run] all {total_pixels} pixels -> {color}")
    else:
        assert sender is not None
        sender.send_frame(frame.tobytes())


def send_pixels(
    sender: DDPSender | None, total_pixels: int, pixel_indices: list[int], color: RGB, dry_run: bool
) -> None:
    """Send (or print) a frame with just ``pixel_indices`` set to ``color``, rest off."""
    frame = np.zeros((total_pixels, 3), dtype=np.uint8)
    frame[pixel_indices] = color
    if dry_run:
        print(f"[dry-run] {len(pixel_indices)} pixels -> {color}")
    else:
        assert sender is not None
        sender.send_frame(frame.tobytes())


def hold(
    sender: DDPSender | None,
    total_pixels: int,
    pixel_indices: list[int],
    color: RGB,
    seconds: float,
    dry_run: bool,
) -> None:
    """Resend the frame periodically for `seconds`, since DDP rides on UDP and
    a single dropped packet would otherwise leave the string looking wrong.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        send_pixels(sender, total_pixels, pixel_indices, color, dry_run)
        time.sleep(0.2)


def send_pixel_colors(
    sender: DDPSender | None, total_pixels: int, pixel_colors: dict[int, RGB], dry_run: bool
) -> None:
    """Send (or print) a frame with each pixel index set to its own color."""
    frame = np.zeros((total_pixels, 3), dtype=np.uint8)
    for pixel_index, color in pixel_colors.items():
        frame[pixel_index] = color
    if dry_run:
        print(f"[dry-run] {pixel_colors}")
    else:
        assert sender is not None
        sender.send_frame(frame.tobytes())


def hold_many(
    sender: DDPSender | None,
    total_pixels: int,
    pixel_colors: dict[int, RGB],
    seconds: float,
    dry_run: bool,
) -> None:
    """Resend a multi-color frame periodically for `seconds` (see `hold`)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        send_pixel_colors(sender, total_pixels, pixel_colors, dry_run)
        time.sleep(0.2)


def run_groups(
    sender: DDPSender | None,
    layout: UnicornLayout,
    color: RGB,
    hold_seconds: float,
    dry_run: bool,
    only: str | None = None,
) -> None:
    """Light each named group (or just ``only``, if given) in turn."""
    groups = layout.groups
    if only is not None:
        if only not in groups:
            raise SystemExit(f"unknown group {only!r}; available: {', '.join(groups)}")
        groups = {only: groups[only]}

    for name, nodes in groups.items():
        pixel_indices = [layout.node_to_pixel_index(n) for n in nodes]
        print(f"-> {name} ({len(nodes)} pixels)")
        hold(sender, layout.total_pixels, pixel_indices, color, hold_seconds, dry_run)
        send_pixels(sender, layout.total_pixels, pixel_indices, (0, 0, 0), dry_run)
    print("done")


def run_chase(sender: DDPSender | None, layout: UnicornLayout, color: RGB, pixel_delay: float, dry_run: bool) -> None:
    """Light one physical pixel at a time across the whole string, in order."""
    for i in range(layout.total_pixels):
        print(f"-> pixel {i}", end="\r")
        send_pixels(sender, layout.total_pixels, [i], color, dry_run)
        time.sleep(pixel_delay)
    send_solid(sender, layout.total_pixels, (0, 0, 0), dry_run)
    print("\ndone")


def run_all(sender: DDPSender | None, layout: UnicornLayout, color: RGB, seconds: float, dry_run: bool) -> None:
    """Hold the whole string at one solid color for `seconds`, then blackout."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        send_solid(sender, layout.total_pixels, color, dry_run)
        time.sleep(0.2)
    send_solid(sender, layout.total_pixels, (0, 0, 0), dry_run)
    print("done")


def run_off(sender: DDPSender | None, layout: UnicornLayout, dry_run: bool) -> None:
    """Blackout the whole string."""
    send_solid(sender, layout.total_pixels, (0, 0, 0), dry_run)
    print("done")


def run_nodes(
    sender: DDPSender | None,
    layout: UnicornLayout,
    node_list: list[int],
    brightness: float,
    hold_seconds: float,
    dry_run: bool,
) -> None:
    """Light specific dense node numbers at once in a rainbow gradient.

    Dense node numbers (1, 2, 3, ... no gaps) are what xLights' own
    Nodes/Test UI shows, and what ``layout.node_to_pixel_index()`` expects -
    NOT the same number line as ``layout.missing_nodes``, which lists
    physical string positions with no dense node at all (see layout.py for
    the full story).
    """
    max_dense_node = len(layout.present_physical_positions)
    out_of_range = [n for n in node_list if not 1 <= n <= max_dense_node]
    if out_of_range:
        raise SystemExit(f"node(s) {out_of_range} out of range (1-{max_dense_node})")

    colors = [scaled(c, brightness) for c in rainbow_colors(len(node_list))]
    pixel_colors = {
        layout.node_to_pixel_index(node): color for node, color in zip(node_list, colors)
    }

    print("node -> color (same order/colors preview_layout.py --highlight-nodes will draw):")
    for node, color in zip(node_list, colors):
        print(f"  node {node} (pixel {layout.node_to_pixel_index(node)}) -> {color}")

    hold_many(sender, layout.total_pixels, pixel_colors, hold_seconds, dry_run)
    send_solid(sender, layout.total_pixels, (0, 0, 0), dry_run)
    print("done")


def main() -> None:
    """Parse CLI arguments and run the selected test mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=["groups", "chase", "all", "off", "nodes"],
        help="groups: one named group at a time | chase: one pixel at a time across "
        "the whole string | all: every pixel the same color | off: blackout | "
        "nodes: light specific 1-based node numbers at once in a rainbow gradient "
        "(see --nodes), for comparing against preview_layout.py --highlight-nodes.",
    )
    parser.add_argument("--controller-ip", default=None, help="Override controller IP.")
    parser.add_argument(
        "--nodes", default=None, help="Node range for 'nodes' mode, e.g., '1-10' or '1,3,5'."
    )
    parser.add_argument(
        "--group",
        default=None,
        help="Limit 'groups' mode to a single named group (e.g., 'Horn'), held until --hold "
        "expires, instead of cycling through all of them.",
    )
    parser.add_argument("--color", type=parse_color, default=WHITE, help="e.g., 'ff0000' or '255,0,0'.")
    parser.add_argument("--brightness", type=float, default=0.3, help="0-1 scale applied to --color.")
    parser.add_argument(
        "--hold", type=float, default=2.0, help="Seconds per group (groups mode) or total duration (all mode)."
    )
    parser.add_argument("--pixel-delay", type=float, default=0.05, help="Seconds per pixel (chase mode).")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be sent instead of sending DDP packets."
    )
    args = parser.parse_args()

    layout = load_layout()
    ip = args.controller_ip or layout.controller_ip
    brightness = max(0.0, min(1.0, args.brightness))
    color = scaled(args.color, brightness)
    print(f"Target: {ip} (DDP)" if not args.dry_run else "Dry run: not sending DDP packets")
    if args.mode != "nodes":
        print(f"Color: {color}")

    sender = None if args.dry_run else DDPSender(ip)
    try:
        if args.mode == "groups":
            run_groups(sender, layout, color, args.hold, args.dry_run, only=args.group)
        elif args.mode == "chase":
            run_chase(sender, layout, color, args.pixel_delay, args.dry_run)
        elif args.mode == "all":
            run_all(sender, layout, color, args.hold, args.dry_run)
        elif args.mode == "off":
            run_off(sender, layout, args.dry_run)
        elif args.mode == "nodes":
            if not args.nodes:
                raise SystemExit("--nodes is required for 'nodes' mode, e.g., --nodes 1-10")
            node_list = parse_node_range(args.nodes)
            run_nodes(sender, layout, node_list, brightness, args.hold, args.dry_run)
    except KeyboardInterrupt:
        print()
        send_solid(sender, layout.total_pixels, (0, 0, 0), args.dry_run)
    finally:
        if sender is not None:
            sender.close()


if __name__ == "__main__":
    sys.exit(main())

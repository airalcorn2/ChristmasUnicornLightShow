"""Shared helpers for specifying a set of 1-based node numbers and generating
a matching rainbow gradient across them - used by both test_lights.py (to
light the real hardware) and preview_layout.py (to render the same nodes in
the same colors), so the two can be compared directly.
"""

from __future__ import annotations

import colorsys


def parse_node_range(text: str) -> list[int]:
    """Parse a node range spec into a sorted list of node numbers.

    Accepts comma-separated single values and/or inclusive dash ranges, e.g.
    ``"1-10"``, ``"1,3,5"``, or ``"1-3,7"``.
    """
    nodes: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-")
            nodes.update(range(int(start), int(end) + 1))
        else:
            nodes.add(int(part))
    return sorted(nodes)


def rainbow_colors(count: int) -> list[tuple[int, int, int]]:
    """Return ``count`` RGB colors, one evenly-spaced hue per item, in order.

    The fixed order (rather than, say, random hues) is what makes item order
    visually obvious when the colors are rendered side by side.
    """
    colors: list[tuple[int, int, int]] = []
    for i in range(count):
        hue = i / max(count, 1)
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors

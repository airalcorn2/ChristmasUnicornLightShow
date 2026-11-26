"""Extracts each named group's dominant color from the show's rendered
.fseq file, as a data source for config.py's official_band_config() - an
alternative to the hand-picked colors in BAND_CONFIG.

The .fseq is xLights' baked, frame-by-frame final output (what actually gets
sent to the controller), so this sidesteps needing to parse the sequence's
effects/palettes directly - it just reads the real result.

Run directly to (re)generate show_colors.json, e.g., after re-rendering the
sequence:

    .venv/bin/python show_colors.py
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import zstandard

from layout import DEFAULT_SHOW_DIR, UnicornLayout, load_layout

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "show_colors.json"

RGB = tuple[int, int, int]

_DOMINANCE_WARNING_THRESHOLD = 0.2
"""Print a caveat for any group whose most common color covers less than
this fraction of its lit frames - e.g., Horn, which cycles through a rainbow
in the real show rather than holding one color, so no single "official"
color is very representative."""


def _find_fseq(show_dir: Path) -> Path:
    """Find the show's one rendered .fseq file in ``show_dir``.

    Raises ``FileNotFoundError`` if none, or more than one (ambiguous -
    pass ``--fseq`` explicitly instead), is found.
    """
    candidates = sorted(show_dir.glob("*.fseq"))
    if not candidates:
        raise FileNotFoundError(f"No .fseq file found in {show_dir}")
    if len(candidates) > 1:
        raise FileNotFoundError(
            f"Multiple .fseq files found in {show_dir}, pass one explicitly with --fseq: {candidates}"
        )
    return candidates[0]


def read_frames(fseq_path: Path) -> np.ndarray:
    """Decompress an xLights FSEQ v2 (zstd-compressed) file into a
    ``(num_frames, channel_count)`` uint8 array: frames[f, c] is channel
    ``c``'s value (0-255) at frame ``f``.
    """
    with open(fseq_path, "rb") as f:
        header = f.read(32)
        channel_data_offset = int.from_bytes(header[4:6], "little")
        channel_count = int.from_bytes(header[10:14], "little")
        num_frames = int.from_bytes(header[14:18], "little")
        f.seek(channel_data_offset)
        compressed = f.read()

    decompressor = zstandard.ZstdDecompressor()
    with decompressor.stream_reader(compressed) as reader:
        decompressed = reader.read()

    # The decompressed stream can run slightly longer than the channel data
    # itself (trailing block framing/padding) - the real frame data is a
    # prefix of it.
    expected_bytes = channel_count * num_frames
    data = decompressed[:expected_bytes]
    return np.frombuffer(data, dtype=np.uint8).reshape(num_frames, channel_count)


def dominant_colors(layout: UnicornLayout, frames: np.ndarray) -> dict[str, tuple[RGB, float]]:
    """For each group, find its most common non-black (R, G, B) across every
    frame and every pixel in the group, and what fraction of its lit
    pixel-frames that color covers (low = the group cycles through many
    colors rather than holding one).
    """
    results: dict[str, tuple[RGB, float]] = {}
    for name, nodes in layout.groups.items():
        pixel_indices = [layout.node_to_pixel_index(n) for n in nodes]
        seen: Counter[RGB] = Counter()
        for pixel_index in pixel_indices:
            rgb = frames[:, pixel_index * 3 : pixel_index * 3 + 3]
            nonzero = rgb[np.any(rgb != 0, axis=1)]
            seen.update(map(tuple, nonzero.tolist()))
        if seen:
            color, count = seen.most_common(1)[0]
            results[name] = (color, count / seen.total())
    return results


def main() -> None:
    """Parse CLI arguments, extract dominant colors, and write the JSON output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fseq",
        type=Path,
        default=None,
        help="Path to a specific .fseq file (default: auto-detect in the show directory)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="Output JSON path")
    args = parser.parse_args()

    fseq_path = args.fseq or _find_fseq(DEFAULT_SHOW_DIR)
    print(f"Reading {fseq_path} ...")
    frames = read_frames(fseq_path)
    layout = load_layout()
    results = dominant_colors(layout, frames)

    with open(args.out, "w") as f:
        json.dump({name: color for name, (color, _) in results.items()}, f, indent=2)

    print(f"Wrote {args.out}:")
    for name, (color, share) in results.items():
        note = ""
        if share < _DOMINANCE_WARNING_THRESHOLD:
            note = "  <- cycles colors rather than holding one"
        print(f"  {name}: {color} ({share:.0%} of lit frames){note}")
    never_lit = set(layout.groups) - set(results)
    if never_lit:
        print(f"Never lit in this show (no color to extract): {', '.join(sorted(never_lit))}")


if __name__ == "__main__":
    main()

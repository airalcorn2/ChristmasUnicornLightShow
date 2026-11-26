"""Renders the unicorn's raw pixel grid, colored by named group, as a PNG.

This is a sanity check independent of the physical hardware: it reconstructs
the model's shape purely from the (row, col) coordinates embedded in
CustomModelCompressed in xlights_rgbeffects.xml, so you can visually confirm
a group (e.g., "Horn") actually covers the pixels you'd expect, before
trusting its name in config.py or test_lights.py.

Run directly:

    .venv/bin/python preview_layout.py                        # Full layout, every group colored.
    .venv/bin/python preview_layout.py --only-group Horn      # Isolate one group's shape.
    .venv/bin/python preview_layout.py --highlight-nodes 1-10  # Overlay a rainbow gradient.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from layout import DEFAULT_RGBEFFECTS, MODEL_NAME, load_layout
from nodes import parse_node_range, rainbow_colors

RGB = tuple[int, int, int]

GROUP_COLORS: dict[str, RGB] = {
    "Hat": (255, 0, 0),
    "Trim": (255, 165, 0),
    "Mane": (255, 255, 0),
    "Head": (0, 255, 0),
    "Horn": (0, 150, 255),
    "Closed Eye": (160, 0, 255),
    "Closed Mouth": (255, 0, 200),
    "Open Eye": (0, 255, 200),
    "Open Mouth": (255, 105, 180),
}
"""Display color per named group. "Open Eye"/"Open Mouth" come from
<faceInfo> (Singing Faces), not <subModel>, and overlap "Closed Eye"/"Closed
Mouth" physically by design - see layout.py's FACE_INFO_GROUPS."""
UNASSIGNED_COLOR: RGB = (90, 90, 90)
"""Drawn for any pixel that isn't a member of any named group."""
BACKGROUND_COLOR: RGB = (20, 20, 20)
ONLY_GROUP_COLOR: RGB = (255, 255, 255)
"""Highlight color used by --only-group; everything else falls back to UNASSIGNED_COLOR."""


def render(
    rgbeffects_path: Path,
    scale: int,
    highlight_nodes: list[int] | None = None,
    only_group: str | None = None,
) -> Image.Image:
    """Render the unicorn's pixel grid as a PIL image.

    ``scale`` is pixels per grid cell in the output image. If
    ``highlight_nodes`` is given, those dense node numbers are additionally
    overlaid with a labeled rainbow gradient (the same one
    ``test_lights.py nodes`` sends to the real hardware), for direct
    comparison. If ``only_group`` is given, that one group is drawn in white
    and every other pixel in gray, instead of each group in its own color.

    Raises ``ValueError`` if ``only_group`` isn't a real group name, or if
    any ``highlight_nodes`` entry is out of range.
    """
    xml_text = Path(rgbeffects_path).read_text()

    model_match = re.search(
        r'<model\s+([^>]*\bname="{}"[^>]*)>'.format(re.escape(MODEL_NAME)), xml_text
    )
    if not model_match:
        raise ValueError(f"Model '{MODEL_NAME}' not found in {rgbeffects_path}")
    attrs_text = model_match.group(1)

    # xLights 2026.19 renamed these from parm1/parm2 to CustomWidth/CustomHeight
    # on save (a version-7 -> version-8 model format upgrade) - support both.
    width_match = re.search(r'(?:parm1|CustomWidth)="(\d+)"', attrs_text)
    height_match = re.search(r'(?:parm2|CustomHeight)="(\d+)"', attrs_text)
    assert width_match is not None and height_match is not None
    width = int(width_match.group(1))  # columns
    height = int(height_match.group(1))  # rows

    # CustomModelCompressed's own "node" labels are physical string positions
    # (1..250, 17 of them unused), a different number line than the dense
    # node numbers (1, 2, 3, ... no gaps) submodels list as members and that
    # xLights' Nodes/Test UI shows. layout.node_to_pixel_index() converts
    # dense -> physical - the same conversion DDP output already relies on -
    # so every position lookup here goes through it rather than indexing this
    # dict directly by a submodel's node number.
    compressed_match = re.search(r'CustomModelCompressed="([^"]*)"', attrs_text)
    assert compressed_match is not None
    pos_by_physical_label: dict[int, tuple[int, int]] = {}
    for triple in compressed_match.group(1).split(";"):
        if not triple.strip():
            continue
        label, row, col = triple.split(",")
        pos_by_physical_label[int(label)] = (int(row), int(col))

    # Group membership comes from layout.load_layout() rather than being
    # re-derived here, so this preview can never drift from what
    # config.py/test_lights.py actually use.
    layout = load_layout(rgbeffects_path)
    if (only_group is not None) and (only_group not in layout.groups):
        raise ValueError(f"unknown group {only_group!r}; available: {', '.join(layout.groups)}")

    # Dense node numbers are 1..max_dense_node with NO gaps of their own.
    max_dense_node = len(layout.present_physical_positions)

    def position(node: int) -> tuple[int, int]:
        return pos_by_physical_label[layout.node_to_pixel_index(node) + 1]

    node_to_group: dict[int, str] = {}
    for name, nodes in layout.groups.items():
        for node in nodes:
            node_to_group[node] = name

    def color_for(node: int) -> RGB:
        group = node_to_group.get(node)
        if only_group is not None:
            return ONLY_GROUP_COLOR if group == only_group else UNASSIGNED_COLOR
        return GROUP_COLORS.get(group, UNASSIGNED_COLOR) if group else UNASSIGNED_COLOR

    image = Image.new("RGB", (width * scale, height * scale), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    dot_radius = max(1, scale // 2)
    for node in range(1, max_dense_node + 1):
        row, col = position(node)
        x, y = col * scale + scale // 2, row * scale + scale // 2
        draw.ellipse(
            [x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius], fill=color_for(node)
        )

    if highlight_nodes:
        bad = [n for n in highlight_nodes if not 1 <= n <= max_dense_node]
        if bad:
            raise ValueError(f"node(s) {bad} are out of range or missing in {rgbeffects_path}")

        # Same rainbow gradient test_lights.py's 'nodes' mode sends to the real
        # hardware, so the two can be compared node-for-node.
        colors = rainbow_colors(len(highlight_nodes))
        highlight_radius = dot_radius * 2
        font = ImageFont.load_default()
        for node, color in zip(highlight_nodes, colors):
            row, col = position(node)
            x, y = col * scale + scale // 2, row * scale + scale // 2
            draw.ellipse(
                [x - highlight_radius, y - highlight_radius, x + highlight_radius, y + highlight_radius],
                fill=color,
                outline=(255, 255, 255),
                width=max(1, scale // 5),
            )
            draw.text((x + highlight_radius + 2, y - highlight_radius), str(node), fill=(255, 255, 255), font=font)

    return image


def main() -> None:
    """Parse CLI arguments, render the preview, and save it to disk."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rgbeffects", type=Path, default=DEFAULT_RGBEFFECTS, help="Path to xlights_rgbeffects.xml"
    )
    parser.add_argument("--scale", type=int, default=10, help="Pixels per grid cell in the output image")
    parser.add_argument(
        "--out", type=Path, default=Path("layout_preview.png"), help="Output PNG path"
    )
    parser.add_argument(
        "--highlight-nodes",
        default=None,
        help="Node range to highlight with a rainbow gradient, e.g., '1-10' - matches "
        "what 'test_lights.py nodes --nodes 1-10' sends to the real hardware, for "
        "direct comparison",
    )
    parser.add_argument(
        "--only-group",
        default=None,
        help="Draw just this one group in white and everything else in gray, instead "
        "of every group in its own color - isolates a single group's shape",
    )
    args = parser.parse_args()

    highlight_nodes = parse_node_range(args.highlight_nodes) if args.highlight_nodes else None
    image = render(args.rgbeffects, args.scale, highlight_nodes, args.only_group)
    image.save(args.out)

    if args.only_group:
        print(f"Showing only: {args.only_group} (white), everything else gray")
    else:
        print("Legend:")
        for name, color in GROUP_COLORS.items():
            print(f"  {name}: rgb{color}")
        print(f"  (unassigned pixels): rgb{UNASSIGNED_COLOR}")
    print(f"\nSaved {args.out}")


if __name__ == "__main__":
    main()

"""Parses the xLights show XML to learn the unicorn's pixel layout and controller.

Reads directly from the show's xlights_rgbeffects.xml and xlights_networks.xml so
the group-to-pixel mapping always matches the live sequence instead of being
hand-copied and allowed to drift.

Run directly to print the parsed layout:

    .venv/bin/python layout.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from nodes import parse_node_range

MODEL_NAME = "Christmas_Unicorn"
DEFAULT_SHOW_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RGBEFFECTS = DEFAULT_SHOW_DIR / "xlights_rgbeffects.xml"
DEFAULT_NETWORKS = DEFAULT_SHOW_DIR / "xlights_networks.xml"

_MODEL_TAG_RE = re.compile(r"<model\s+([^>]*\bname=\"{}\"[^>]*)>".format(re.escape(MODEL_NAME)))
_SUBMODEL_TAG_RE = re.compile(r'<subModel name="([^"]*)"[^>]*>')
_LINE_RE = re.compile(r'line\d+="([^"]*)"')
_FACEINFO_RE = re.compile(r"<faceInfo\s+([^>]*)/?>")
_CONTROLLER_RE = re.compile(
    r'<Controller\b[^>]*\bIP="([^"]*)"[^>]*\bProtocol="([^"]*)"', re.IGNORECASE
)

FACE_INFO_GROUPS: dict[str, str] = {
    "Open Eye": "Eyes-Open",
    "Open Mouth": "Mouth-AI",
}
"""Maps our named groups onto xLights' "Singing Faces" <faceInfo> attributes.

That feature stores its own node ranges in a <faceInfo> element, separate
from <subModel> entirely and invisible to _SUBMODEL_TAG_RE. Eyes-Open and
Eyes-Closed overlap at a few nodes by design: a blink lights a subset of the
open eye's own pixels, not a disjoint set. Of the several open-mouth viseme
attributes (Mouth-AI/E/O/U/WQ, all identical here), "Mouth-AI" is just a
representative pick; the closed/rest ones (Mouth-rest/etc/FV/L/MBP) already
match the "Closed Mouth" submodel exactly.
"""


@dataclass(frozen=True, slots=True)
class UnicornLayout:
    """The unicorn's parsed pixel layout and controller address."""

    total_pixels: int
    """Total physical pixels on the string (used + unused)."""
    groups: dict[str, list[int]]
    """Group name -> xLights' own dense 1-based node numbers (members), e.g.
    {"Hat": [1, 2, 3, ...], ...}."""
    missing_nodes: list[int]
    """Physical string positions with no dense node at all - see
    node_to_pixel_index()."""
    present_physical_positions: tuple[int, ...]
    """Sorted physical positions that do have a dense node; dense node N's
    true physical position is present_physical_positions[N - 1]."""
    controller_ip: str
    controller_protocol: str

    def node_to_pixel_index(self, node: int) -> int:
        """Convert an xLights model node number to its 0-based position in
        the 750-byte DDP payload.

        ``node`` is a dense node number (1, 2, 3, ... with no gaps) - what
        submodels list as members and what xLights' Nodes/Test UI shows.

        xLights' dense node numbering and the physical string's position
        numbering are different number lines that happen to overlap in
        range, which is what makes this subtle: 17 extra physical pixels on
        the wire (more were bought than the design needed) were never placed
        anywhere in the custom model's shape, so they have no dense node
        number at all - dense node 49 is the physical string's 64th pixel,
        not its 49th, because pixels 49-63 physically exist but are none of
        xLights' dense nodes.

        A formula like ``node - 1 + count(missing < node)`` looks right and
        even matches hardware for many nodes (151, 218, 233 all checked out)
        but is subtly wrong for any dense node whose number falls inside a
        gap's own numeric range (confirmed wrong for node 49 specifically:
        hardware says channel 190, the formula says 145) - it only happens
        to "catch up" to the right answer once the dense number is past the
        gap. This direct lookup has no such edge case:
        ``present_physical_positions`` is the actual sorted list of the 233
        physical positions that do have a dense node, so indexing into it by
        ``node - 1`` is correct for every node, not just the ones that
        happen to avoid the edge case.
        """
        return self.present_physical_positions[node - 1] - 1


def _parse_node_set(tag_text: str) -> set[int]:
    """Collect every node number appearing anywhere in a <subModel>'s
    ``lineN="..."`` grid attributes.

    Cell position within a line is irrelevant here - only which node numbers
    appear at all, i.e., which nodes are members of this submodel.
    """
    nodes: set[int] = set()
    for line in _LINE_RE.findall(tag_text):
        for value in line.split(","):
            value = value.strip()
            if value:
                nodes.add(int(value))
    return nodes


def load_layout(
    rgbeffects_path: Path = DEFAULT_RGBEFFECTS,
    networks_path: Path = DEFAULT_NETWORKS,
) -> UnicornLayout:
    """Parse the show's xLights XML into a `UnicornLayout`.

    Raises ``ValueError`` if the model, its pixel grid, or the controller's
    IP/protocol can't be found in the given files.
    """
    xml_text = Path(rgbeffects_path).read_text()

    model_match = _MODEL_TAG_RE.search(xml_text)
    if not model_match:
        raise ValueError(f"Model '{MODEL_NAME}' not found in {rgbeffects_path}")
    compressed_match = re.search(r'CustomModelCompressed="([^"]*)"', model_match.group(1))
    if not compressed_match:
        raise ValueError(f"Model '{MODEL_NAME}' has no CustomModelCompressed data")
    present_nodes = {
        int(pair.split(",")[0]) for pair in compressed_match.group(1).split(";") if pair.strip()
    }
    total_pixels = max(present_nodes)
    missing_nodes = sorted(n for n in range(1, total_pixels + 1) if n not in present_nodes)
    present_physical_positions = tuple(sorted(present_nodes))

    model_end = xml_text.find("</model>", model_match.start())
    model_block = xml_text[model_match.start() : model_end if model_end != -1 else None]

    groups: dict[str, list[int]] = {}
    for sub_match in _SUBMODEL_TAG_RE.finditer(model_block):
        sub_end = model_block.find(">", sub_match.start())
        sub_tag = model_block[sub_match.start() : sub_end + 1]
        groups[sub_match.group(1)] = sorted(_parse_node_set(sub_tag))

    face_match = _FACEINFO_RE.search(model_block)
    if face_match:
        face_attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', face_match.group(1)))
        for group_name, attr_name in FACE_INFO_GROUPS.items():
            value = face_attrs.get(attr_name)
            if value:
                groups[group_name] = sorted(parse_node_range(value))

    networks_text = Path(networks_path).read_text()
    controller_match = _CONTROLLER_RE.search(networks_text)
    if not controller_match:
        raise ValueError(f"No controller with an IP/Protocol found in {networks_path}")
    controller_ip, controller_protocol = controller_match.groups()

    return UnicornLayout(
        total_pixels=total_pixels,
        groups=groups,
        missing_nodes=missing_nodes,
        present_physical_positions=present_physical_positions,
        controller_ip=controller_ip,
        controller_protocol=controller_protocol,
    )


if __name__ == "__main__":
    layout = load_layout()
    print(f"Controller: {layout.controller_ip} ({layout.controller_protocol})")
    print(f"Total pixels: {layout.total_pixels}")
    print(f"Missing nodes (no grid position, extra unused pixels): {layout.missing_nodes}")
    for name, nodes in layout.groups.items():
        pixel_indices = [layout.node_to_pixel_index(n) for n in nodes]
        print(
            f"  {name}: {len(nodes)} nodes ({nodes[0]}-{nodes[-1]}), "
            f"pixel indices {min(pixel_indices)}-{max(pixel_indices)}"
        )

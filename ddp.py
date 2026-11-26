"""Minimal DDP (Distributed Display Protocol) sender for pushing pixel frames
over UDP to a pixel controller, bypassing xLights entirely.
"""

from __future__ import annotations

import socket

DDP_PORT = 4048
_VER1_PUSH = 0x41
"""Flags byte: version 1 (bits 6-7) | PUSH flag (bit 0, apply immediately)."""
_DEFAULT_OUTPUT_ID = 1
"""Per the DDP spec, output/device ID 0 is reserved/invalid; 1 is the default output."""
_MAX_SEQUENCE = 15
"""DDP sequence numbers cycle 1-15 (0 means "sequencing disabled")."""


class DDPSender:
    """Sends RGB pixel frames to a single DDP-speaking controller over UDP."""

    def __init__(self, controller_ip: str, port: int = DDP_PORT) -> None:
        """Open a UDP socket targeting ``controller_ip:port``.

        No handshake: DDP is connectionless, so this never blocks or fails
        just because the controller happens to be unreachable at the moment.
        """
        self._address = (controller_ip, port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sequence = 0

    def send_frame(self, pixel_bytes: bytes, channel_offset: int = 0) -> None:
        """Send one frame of packed RGB bytes starting at ``channel_offset``.

        ``pixel_bytes`` is a flat sequence of ``R, G, B, R, G, B, ...`` bytes;
        the caller is responsible for ordering it to match the controller's
        own channel layout.
        """
        self._sequence = (self._sequence % _MAX_SEQUENCE) + 1
        header = bytes(
            (
                _VER1_PUSH,
                self._sequence,
                0,  # data type: undefined/RGB passthrough
                _DEFAULT_OUTPUT_ID,
                *channel_offset.to_bytes(4, "big"),
                *len(pixel_bytes).to_bytes(2, "big"),
            )
        )
        self._socket.sendto(header + pixel_bytes, self._address)

    def close(self) -> None:
        """Release the underlying UDP socket."""
        self._socket.close()

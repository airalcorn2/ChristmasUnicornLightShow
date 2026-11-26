"""Live audio capture (via `parec`, so it can target PipeWire/PulseAudio
monitor sources for system-audio loopback) and per-band energy extraction.
"""

from __future__ import annotations

import queue
import subprocess
import threading
from types import TracebackType

import numpy as np

import config

_BYTES_PER_SAMPLE = 4
"""float32le: 4 bytes per sample."""
_LATENCY_MSEC = 20
"""Requested PulseAudio/PipeWire client buffer latency. parec's own default
is tuned for reliability over responsiveness and measured at roughly 1s of
end-to-end lag (audio played -> level change detected) - audible as the
lights visibly trailing the music. Requesting a short buffer explicitly
drops that to ~25ms, confirmed with a single-impulse click test."""


def list_devices() -> None:
    """Print available PulseAudio/PipeWire sources and the default loopback source."""
    print("PulseAudio/PipeWire sources (pass a Name to --device):\n", flush=True)
    subprocess.run(["pactl", "list", "sources", "short"], check=True)
    default_sink = subprocess.run(
        ["pactl", "get-default-sink"], check=True, capture_output=True, text=True
    ).stdout.strip()
    print(f"\nDefault loopback source (used when --device is omitted): {default_sink}.monitor")


def _default_monitor_source() -> str:
    """Return the monitor source name for the system's current default sink."""
    default_sink = subprocess.run(
        ["pactl", "get-default-sink"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return f"{default_sink}.monitor"


class BandAnalyzer:
    """Turns a stream of audio blocks into smoothed, normalized energy levels
    for each named frequency band in config.BAND_CONFIG.

    Tracks two parallel envelopes per band: ``levels`` (slow release, for
    driving a light's visible brightness) and ``gate_levels`` (fast release,
    for binary on/off decisions like equalizer.py's mouth-open gate) - see
    config.RELEASE and config.GATE_RELEASE.
    """

    def __init__(self, sample_rate: int = config.SAMPLE_RATE) -> None:
        """Initialize per-band smoothing/AGC state for the given sample rate."""
        self._sample_rate = sample_rate
        self._band_names = list(config.BAND_CONFIG.keys())
        self._levels: dict[str, float] = {name: 0.0 for name in self._band_names}
        self._gate_levels: dict[str, float] = {name: 0.0 for name in self._band_names}
        self._agc_ceiling: dict[str, float] = {
            name: config.MIN_AGC_CEILING for name in self._band_names
        }
        self._window = np.hanning(config.BLOCK_SIZE)

    @property
    def gate_levels(self) -> dict[str, float]:
        """Each band's fast-release level (config.GATE_RELEASE), as of the
        most recent `process()` call - see the class docstring.
        """
        return dict(self._gate_levels)

    def process(self, samples: np.ndarray) -> dict[str, float]:
        """Compute one frame's smoothed, normalized energy level (0-1) per band.

        ``samples`` is one block of mono float32 audio (length
        ``config.BLOCK_SIZE``). Returns a mapping from band name to its
        current envelope-followed, auto-gain-normalized level (the slow,
        visual-brightness one; see `gate_levels` for the fast one).
        """
        windowed = samples * self._window
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(len(samples), d=1.0 / self._sample_rate)

        for name in self._band_names:
            low_hz, high_hz, _ = config.BAND_CONFIG[name]
            mask = (freqs >= low_hz) & (freqs < high_hz)
            raw_energy = float(spectrum[mask].mean()) if mask.any() else 0.0

            ceiling = self._agc_ceiling[name]
            ceiling = max(ceiling * config.AGC_DECAY, raw_energy, config.MIN_AGC_CEILING)
            self._agc_ceiling[name] = ceiling
            normalized = min(raw_energy / ceiling, 1.0)
            if normalized < config.NOISE_FLOOR:
                normalized = 0.0

            previous = self._levels[name]
            coeff = config.ATTACK if normalized > previous else config.RELEASE
            self._levels[name] = previous + coeff * (normalized - previous)

            gate_previous = self._gate_levels[name]
            gate_coeff = config.ATTACK if normalized > gate_previous else config.GATE_RELEASE
            self._gate_levels[name] = gate_previous + gate_coeff * (normalized - gate_previous)

        return dict(self._levels)


class AudioStream:
    """Captures mono float32 audio by running a CLI capture tool as a
    subprocess and reading its raw PCM stdout in a background thread,
    handing back fixed-size blocks through a queue so the main loop can
    pull at its own pace.

    Two backends, since the Raspberry Pi running talk.py's --pi mode runs
    PipeWire/WirePlumber without the PulseAudio-compatibility socket that
    parec/paplay need (see README's Network Setup/Pi bridge notes) - so
    this project talks to the hardware directly via plain ALSA there
    instead:

    - "pulse" (default): `parec`, for this project's usual PulseAudio/
      PipeWire machines.
    - "alsa": `arecord`, for the Pi - ``device`` is then an ALSA device
      string like "plughw:3,0", not a PulseAudio source name.
    """

    def __init__(
        self,
        device: str | None,
        sample_rate: int = config.SAMPLE_RATE,
        backend: str = "pulse",
    ) -> None:
        """Prepare (but do not yet start) a capture from ``device`` via
        ``backend`` ("pulse" or "alsa" - see the class docstring).

        For "pulse", ``device`` is a PulseAudio/PipeWire source name, or
        ``None`` to use the default sink's monitor (i.e., this machine's
        own audio output). For "alsa", ``device`` is required (there's no
        sensible default to fall back to).
        """
        if (backend == "alsa") and (device is None):
            raise ValueError('backend="alsa" requires an explicit ALSA device string')
        self._backend = backend
        self._device = device or _default_monitor_source()
        self._sample_rate = sample_rate
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_requested = False

    def _read_loop(self) -> None:
        """Background-thread loop: read fixed-size blocks from the capture
        subprocess and queue them.
        """
        assert self._process is not None
        assert self._process.stdout is not None
        block_bytes = config.BLOCK_SIZE * _BYTES_PER_SAMPLE
        stdout = self._process.stdout
        while not self._stop_requested:
            raw = stdout.read(block_bytes)
            if len(raw) < block_bytes:
                break  # process exited
            block = np.frombuffer(raw, dtype=np.float32).copy()
            try:
                self._queue.put_nowait(block)
            except queue.Full:
                pass

    def __enter__(self) -> AudioStream:
        """Start the capture subprocess and its background reader thread."""
        if self._backend == "alsa":
            command = [
                "arecord",
                "-D",
                self._device,
                "-f",
                "FLOAT_LE",
                "-r",
                str(self._sample_rate),
                "-c",
                "1",
                "-t",
                "raw",
            ]
        else:
            command = [
                "parec",
                f"--device={self._device}",
                "--format=float32le",
                f"--rate={self._sample_rate}",
                "--channels=1",
                "--raw",
                f"--latency-msec={_LATENCY_MSEC}",
            ]
        # stderr is left to inherit the parent's (not redirected to DEVNULL) -
        # if arecord/parec fails to open the device (e.g., still held by a
        # not-yet-released previous capture), its own error message should be
        # visible rather than silently swallowed, leaving only an empty-looking
        # capture with no clue why.
        self._process = subprocess.Popen(command, stdout=subprocess.PIPE)
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the reader thread and terminate the capture subprocess."""
        self._stop_requested = True
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=2)
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)

    def read_block(self, timeout: float = 1.0) -> np.ndarray:
        """Block up to ``timeout`` seconds for the next audio block.

        Raises ``queue.Empty`` if no block arrives in time.
        """
        return self._queue.get(timeout=timeout)

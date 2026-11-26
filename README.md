# Christmas Unicorn Equalizer

For the physical build (parts list, controller setup, model design, sequencing), see [BUILD.md](BUILD.md).

Drives the Christmas Unicorn's lights as a live-audio spectrum analyzer: each named light group pulses with the energy in its own frequency band.
The group-to-pixel mapping and controller address are read directly from `../xlights_rgbeffects.xml` and `../xlights_networks.xml`, so it stays in sync with the real sequence.
Pixel frames are sent straight to the Genius Pixel 16 controller over DDP, bypassing xLights entirely.

## Table of Contents

- [Bands](#bands)
  - [Official Show Colors](#official-show-colors)
- [Node Numbers vs. Physical Pixels](#node-numbers-vs-physical-pixels)
- [Software Layout](#software-layout)
- [Setup](#setup)
  - [Network Setup](#network-setup)
- [Usage](#usage)
  - [Verifying the Hardware/Wiring](#verifying-the-hardwarewiring)
- [Talking to the Unicorn](#talking-to-the-unicorn)
- [Notes](#notes)

## Bands

| Band | Frequency range | Color | Lights |
|---|---|---|---|
| Head | 20-120 Hz (sub-bass/kick) | red | "Head" |
| Mane | 120-300 Hz (bass) | orange | "Mane" |
| Trim | 300-700 Hz (low-mid) | yellow | "Trim" |
| Hat | 700-1800 Hz (mid) | green | "Hat" |
| Horn | 1.8-4.5 kHz (high-mid) | blue | "Horn" |
| Eye | 4.5-8 kHz (presence) | violet | "Open Eye" or "Closed Eye", picked by the blink timer (see below) |

Tune band edges/colors/smoothing in `config.py`.
Group names are exactly xLights' own submodel names - see [Node Numbers vs. Physical Pixels](#node-numbers-vs-physical-pixels) below for why that's trustworthy.

Two groups aren't driven by a frequency band at all, and instead get special-cased behavior in `equalizer.py`'s `build_frame()`:

- **Eye**: both "Open Eye" and "Closed Eye" (from xLights' "Singing Faces" feature, overlapping physically) share the one Eye band/color above.
  Which one is actually lit is picked by a simple blink timer (`BlinkState`): the eye stays open and briefly closes at a randomized interval (`config.BLINK_MIN_INTERVAL`-`BLINK_MAX_INTERVAL`), independent of the audio.
- **Mouth**: "Open Mouth"/"Closed Mouth" have no band of their own - the mouth mirrors Head's color exactly, but opens/closes based on the average gate level of `config.MOUTH_GATE_BANDS` (Trim+Hat, the vocal formant range), not Head/bass, so it tracks singing rather than the beat. Hysteresis (`MOUTH_OPEN_THRESHOLD`/`MOUTH_CLOSE_THRESHOLD`) avoids flutter at the threshold.

### Official Show Colors

The colors above are hand-picked, but `equalizer.py --official-colors` uses each group's actual dominant color from the real, rendered xLights show instead (Hat=red, Trim=white, Mane=magenta, Head=white, Eye=white, as of this show's `.fseq`).
`show_colors.py` extracts these directly from the show's `.fseq` file - xLights' baked, frame-by-frame final output (what actually gets sent to the controller) - rather than parsing the sequence's effects/palettes, and writes them to `show_colors.json`.
Re-run it (`python3 show_colors.py`) if the sequence is ever re-rendered.

Not every group has one dominant color: "Horn" cycles through a rainbow in the real show rather than holding a single color, so its "official" color is just the most common single frame out of many, not very representative - `show_colors.py` prints a caveat for any group like this.

## Node Numbers vs. Physical Pixels

This model has 250 physical pixels, but 17 of them (extra pixels bought but not used in the design, sitting unused behind the real prop) were never placed anywhere in the custom model's shape.
xLights numbers nodes densely (1, 2, 3, ... with no gaps) - what submodels list as members and what xLights' Nodes/Test UI shows - and that dense numbering is a *different number line* from the physical string's own position numbering.
Node 49, for example, is the physical string's 64th pixel, not its 49th, because physical positions 49-63 exist on the wire but have no dense node at all.

`layout.py`'s `UnicornLayout.node_to_pixel_index()` looks this up via a precomputed `present_physical_positions` table (the sorted list of which physical positions actually have a dense node) rather than a formula, specifically because an arithmetic shortcut gets this subtly wrong for any node number that falls numerically inside a gap's own range.
That shortcut was confirmed wrong for node 49 and right for node 218 and others - it only "catches up" to the correct answer once the node number is past the gap, which is what made it look right for a long time.
This is hardware-confirmed against xLights' own Test tab channel numbers for nodes 48, 49, 151, 218, and 233, and confirmed again by sending nodes directly to the real hardware and watching them land exactly where xLights says they should - including `preview_layout.py`'s rendering, which needs the same conversion for correct *positions*, not just correct DDP channels.

## Software Layout

Each module also runs directly for its own bench test, independent of the others.

| Module | Responsibility | Bench test |
|---|---|---|
| `layout.py` | Parses `xlights_rgbeffects.xml`/`xlights_networks.xml` into group-to-node mappings, the dense-node-to-pixel-index conversion, and the controller address | `python3 layout.py` |
| `ddp.py` | Minimal DDP (UDP) sender for pushing raw pixel frames to the controller | - |
| `nodes.py` | Shared node-range parsing and rainbow-gradient color generation | - |
| `blink.py` | Shared periodic eye-blink state, used by both `equalizer.py` and `talk.py` | - |
| `config.py` | Tunable frequency-band-to-group-to-color mapping, smoothing/AGC constants, and voice-chat settings | - |
| `show_colors.py` | Extracts each group's dominant color from the show's rendered `.fseq`, for `--official-colors` | `python3 show_colors.py` |
| `audio.py` | Live loopback audio capture (via `parec`) and per-band energy extraction | `python3 audio.py --list-devices` |
| `equalizer.py` | Ties audio analysis to DDP output - the actual show | `python3 equalizer.py --dry-run` |
| `voice.py` | ElevenLabs text-to-speech (with per-character timing) and speech-to-text | - |
| `talk.py` | Push-to-talk voice chat: speech -> Gemini -> speech, with the mouth timed to the reply's own words | `python3 talk.py --list-devices` |
| `test_lights.py` | Hardware verification: light up groups/nodes/the whole string on demand | `python3 test_lights.py groups --dry-run` |
| `preview_layout.py` | Renders the parsed layout as a PNG, independent of the physical hardware | `python3 preview_layout.py` |

## Setup

This directory is pinned to an isolated pyenv virtualenv via `.python-version` (`unicorn-equalizer`, Python 3.14.4).
From inside this directory:

```
pyenv virtualenv 3.14.4 unicorn-equalizer   # one-time, if not already created
pip install -r requirements.txt
```

Audio capture uses `parec` (PulseAudio/PipeWire CLI) to read the system's default output as a loopback source, so no extra audio library/driver setup is needed beyond what's already on this machine.

### Network Setup

Do this first if nothing lights up.
Full build notes (parts list, controller config, model design) are at https://github.com/airalcorn2/ChristmasUnicornLightShow.
The one step from there that's easy to forget, since it doesn't persist across reboots/sleep/USB-Ethernet replugs, is putting this machine on the controller's subnet:

```
ip addr   # find the Ethernet/USB-adapter interface, e.g., enp0s20f0u1c2
sudo ip addr add 192.168.1.100/24 dev <interface>
```

If the controller's IP (192.168.1.50 per `xlights_networks.xml`) isn't pingable or reachable at `http://192.168.1.50` in a browser, this is almost certainly why - and it'll block xLights' own Test/output just as much as it blocks this project's scripts.

## Usage

```
python3 equalizer.py --list-devices      # see available PulseAudio/PipeWire sources
python3 equalizer.py --dry-run           # print band levels, no network traffic - safe to test anytime
python3 equalizer.py                     # the real thing: sends DDP to the controller
```

Useful flags:

- `--device <source name>` - capture from a specific source instead of the default output monitor (e.g., a mic input).
- `--controller-ip <ip>` - override the IP parsed from `xlights_networks.xml`.
- `--fps <n>` - pixel update rate (default from `config.FPS`).
- `--brightness <0-1>` - overall power cap on top of each band's own level (defaults to a dim 0.3, matching `test_lights.py`'s safety default).
- `--official-colors` - use the real show's own colors instead of the hand-picked ones - see [Official Show Colors](#official-show-colors).

### Verifying the Hardware/Wiring

Before trusting the equalizer to the real unicorn, confirm the layout parsed from the sequence actually matches reality with `test_lights.py`:

```
python3 test_lights.py groups                 # lights up Hat, Trim, Mane, Head, Horn, Closed Eye, Closed Mouth, Open Eye, Open Mouth in turn
python3 test_lights.py groups --group Horn    # isolate one named group instead of cycling through all of them
python3 test_lights.py chase                  # walks a single pixel across all 250 - catches dead pixels/wiring order issues
python3 test_lights.py nodes --nodes 1-10     # lights specific 1-based node numbers in a rainbow gradient
python3 test_lights.py all                    # whole string, one color - quick brightness/power check
python3 test_lights.py off                    # blackout
```

All modes take `--dry-run` and `--controller-ip`.
`groups`/`chase`/`all` also take `--color` (e.g., `ff0000` or `255,0,0`) and `--brightness` (0-1, defaults to a dim 0.3 so a first test doesn't blast the string at full power), plus `--hold`/`--pixel-delay` to control pacing.
`nodes` always uses a rainbow gradient (so each node is visually distinguishable) scaled by `--brightness`, and takes dense node numbers 1 through however many xLights shows (233 here) - not `layout.py`'s `missing_nodes`, which is a different number line entirely (see [Node Numbers vs. Physical Pixels](#node-numbers-vs-physical-pixels) above).

`preview_layout.py --highlight-nodes 1-10` renders the same node range with the same rainbow gradient `nodes` mode sends, for a direct side-by-side comparison between the real hardware and the parsed layout.
`preview_layout.py --only-group <name>` isolates a single group's shape (white) against everything else (gray), useful for comparing one group at a time without the full multi-color legend.

## Talking to the Unicorn

`talk.py` is a separate mode from the equalizer: push-to-talk voice chat, where a visitor's spoken question gets transcribed, sent to Gemini for a short in-character reply, synthesized back to speech, and played aloud while the mouth/eye animate live.

Setup, one-time:

```
cp .env.example .env     # then fill in GEMINI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
```

`GEMINI_API_KEY` has a free tier, good for testing before deciding whether a paid tier is worth it - get one at https://aistudio.google.com/apikey.

`ELEVENLABS_VOICE_ID` is account-specific - find one with:

```
python3 -c "import voice; [print(v['voice_id'], v['name']) for v in voice.list_voices()]"
```

Usage:

```
python3 talk.py --list-devices        # find a microphone source name
python3 talk.py --device <name> --dry-run   # test the conversation loop with no DDP traffic
python3 talk.py --device <name>             # the real thing
```

Each turn: press Enter, speak, press Enter again to stop recording - deliberately push-to-talk rather than always-listening, since automatic silence detection would be unreliable next to the equalizer's own music or outdoor noise.

`--brightness` and `--official-colors` work the same way as `equalizer.py`'s - see [Official Show Colors](#official-show-colors).

**How the mouth syncs to the reply's words:** ElevenLabs' text-to-speech can return per-character start/end timestamps alongside the audio (its "with timestamps" endpoint), not just the audio itself.
`voice.py`'s `_open_intervals_from_alignment()` turns those into mouth-open (start, end) intervals by classifying each *character*, not each word: M/B/P close the mouth (the one lips-together viseme in classic phoneme-based lip sync, e.g., Preston Blair's chart - the only binary contrast worth tracking with just two mouth shapes), every other letter opens it, and `config.MOUTH_MERGE_GAP` smooths over brief gaps that are just ordinary connected-speech co-articulation rather than a real pause - except a gap containing an actual M/B/P, which is kept no matter how short, since that's a deliberate closure, not noise.
`talk.py` then plays the mouth "open" during those intervals, scheduled against elapsed playback time - the same frame-timing approach as `equalizer.py`'s main loop, just driven by known timing instead of live audio analysis.
This is why `talk.py` doesn't need anything like `audio.py`'s envelope followers for its own speech output: the timing is exact and known in advance, not inferred.

While talking, every `config.BAND_CONFIG` group (Head, Mane, Trim, Hat, Horn) is lit solidly at its `equalizer.py` peak color - the whole unicorn, not just the face - with the mouth/eye animated live on top (`talk.py`'s `build_talk_frame()`).

**Tuning the mouth timing without burning API calls:** `--save-fixture` caches one real ElevenLabs response (audio + alignment) to a JSON file; `--replay` plays it back repeatedly (press Enter to repeat) through the same `build_talk_frame()`/DDP path real conversations use, re-deriving the mouth timeline from the cached alignment every time - so a change to `_open_intervals_from_alignment()`/`MOUTH_MERGE_GAP` can be checked against the exact same real speech over and over, with no new network calls and no different text each time.

```
python3 talk.py --save-fixture test_fixture.json --text "Some line with a good mix of pauses and M/B/P sounds."
python3 talk.py --replay test_fixture.json --dry-run    # or without --dry-run, against the real hardware
```

## Notes

- Both `test_lights.py` and `equalizer.py` have been run live against the real controller/hardware (not just `--dry-run`), including the node-to-pixel mapping, individual group isolation, the audio-reactive bands, the eye blink, the mouth gate, and `--official-colors`.
- If the system audio sink is idle, `parec` can take a second or two to wake it up before data starts flowing; that's expected, not a bug.
- `talk.py` has been run live end-to-end (mic -> transcription -> Gemini -> ElevenLabs -> real hardware) - the mouth timing specifically is still being tuned against real playback (see `--replay` above), so treat its current feel as a work in progress rather than finished.

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
  - [Running Without a Laptop (`--pi`)](#running-without-a-laptop---pi)
- [Trivia](#trivia)
  - [A Kid-Friendly Question Bank](#a-kid-friendly-question-bank)
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
| `gamepad.py` | USB gamepad input for `talk.py --pi`'s trigger, so a kid can hold a button instead of pressing Enter | `python3 gamepad.py` |
| `test_lights.py` | Hardware verification: light up groups/nodes/the whole string on demand | `python3 test_lights.py groups --dry-run` |
| `preview_layout.py` | Renders the parsed layout as a PNG, independent of the physical hardware | `python3 preview_layout.py` |
| `trivia_data.py` | One-time prep for the trivia game: downloads/filters/samples a public Jeopardy! clue dataset to Christmas/holiday categories and voice-caches it | `python3 trivia_data.py --skip-audio` |
| `trivia_kids.py` | Alternative one-time prep for a kids' crowd: generates original, family-friendly Christmas trivia with Gemini instead - a drop-in swap for `trivia_data.py`'s output | `python3 trivia_kids.py --skip-audio` |
| `trivia_tiebreakers.py` | 50 hand-researched, individually-sourced numeric facts for trivia's sudden-death tiebreaker | - |
| `trivia_lights.py` | Non-speech light animations for trivia (idle "thinking" rainbow, outcome flashes, round-winner flourish), built on `talk.build_talk_frame()` | - |
| `trivia_web.py` | Generic aiohttp/WebSocket server for trivia's group screen + per-team phone pages | `python3 trivia_web.py` |
| `trivia_game.py` | The trivia game itself: rules, Gemini judging, and orchestration - see [Trivia](#trivia) | `python3 trivia_game.py --dry-run` |

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
Full build notes (parts list, controller config, model design) are in [BUILD.md](BUILD.md).

**Current setup: a Raspberry Pi bridge.**
The controller is wired via Ethernet to a Raspberry Pi (`cauldron`), which joins the home Wi-Fi itself - so this machine reaches the controller over Wi-Fi, with no direct cable to it at all.

On the Pi, one-time setup (survives reboots):

```bash
# eth0 (wired to the controller) gets a narrow /30, not a full /24 - deliberately
# tiny (just enough to cover the controller's own 192.168.1.50), so it can never
# compete with the Pi's own wlan0 for the much broader home-network 192.168.1.0/24
# range. A same-size /24 on both interfaces was tried first and broke the Pi's own
# Wi-Fi reachability outright - the kernel's routing table had no reliable way to
# tell "home network" traffic apart from "controller" traffic since both claimed
# the identical prefix.
sudo nmcli connection modify "Wired connection 1" ipv4.method manual ipv4.addresses 192.168.1.49/30
sudo nmcli connection up "Wired connection 1"

# Forward packets between the Pi's two interfaces, and NAT (masquerade) anything
# headed out to the controller - so the controller only ever sees traffic from the
# Pi's own address and never needs to know how to route a reply back to this
# machine specifically.
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-unicorn-bridge.conf
sudo sysctl -p /etc/sysctl.d/99-unicorn-bridge.conf
sudo tee -a /etc/nftables.conf > /dev/null <<'EOF'

table ip nat {
	chain postrouting {
		type nat hook postrouting priority 100;
		oifname "eth0" masquerade
	}
}
EOF
sudo systemctl enable --now nftables
```

On this machine, once per session (does not persist across reboots/reconnects - a quick re-run if the controller stops responding):

```bash
sudo ip route add 192.168.1.50/32 via 192.168.1.207   # 192.168.1.207 is the Pi's current Wi-Fi IP
```

A `/32` host route, not a `/24` network route, for the same reason as the Pi's own `/30` above: this machine's home Wi-Fi is *also* on `192.168.1.0/24`, so routing the whole range through the Pi would break this machine's own normal network traffic - only that one specific address needs to detour through the Pi.

The Pi's own Wi-Fi IP is DHCP-assigned and can change after a reboot - check `ping cauldron.local` (or your router's DHCP client list) for its current address if the route above stops working, or set a DHCP reservation for it on your router so it never changes.

**Alternative: direct Ethernet connection.**
If the Pi bridge isn't set up (or the Pi's down), this machine can still talk to the controller directly, the original way - the one step that's easy to forget, since it doesn't persist across reboots/sleep/USB-Ethernet replugs, is putting this machine on the controller's subnet:

```
ip addr   # find the Ethernet/USB-adapter interface, e.g., enp0s20f0u1c2
sudo ip addr add 192.168.1.100/24 dev <interface>
```

Either way: if the controller's IP (192.168.1.50 per `xlights_networks.xml`) isn't pingable or reachable at `http://192.168.1.50` in a browser, start here - and it'll block xLights' own Test/output just as much as it blocks this project's scripts.

## Usage

```
python3 equalizer.py --list-devices      # see available PulseAudio/PipeWire sources
python3 equalizer.py --dry-run           # print band levels, no network traffic - safe to test anytime
python3 equalizer.py                     # the real thing: sends DDP to the controller
```

Useful flags:

- `--device <source name>` - capture from a specific source instead of the default output monitor (e.g., a mic input).
- `--pi` - run directly on the Raspberry Pi next to the controller, capturing from its own USB mic (`config.PI_MIC_DEVICE`) via ALSA directly instead of PulseAudio/PipeWire's loopback monitor (the Pi has no PulseAudio-compat socket - see [Running Without a Laptop](#running-without-a-laptop---pi)). Reacts to whatever's happening in the room, picked up by the mic, rather than this machine's own audio output. `--device` is ignored in this mode; `python3 equalizer.py --list-devices --pi` lists ALSA devices (`arecord -l`) instead of PulseAudio sources.
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

**Light effects:** Gemini can make its own lights perform a special effect, targeted at a specific body part, while saying part of a reply - by wrapping that part in a tag naming the effect and target separated by a colon: `[sparkle:Horn]...[/sparkle]`, `[rainbow:all]...[/rainbow]`, `[glow:Mane]...[/glow]`, `[flash:Hat]...[/flash]` (see `config.UNICORN_SYSTEM_PROMPT`, which also tells it to use these sparingly and only around words actually about its lights/colors/magic).
The effect must be one of `config.EFFECT_NAMES`; the target must be one of `config.EFFECT_TARGETS` - a literal `BAND_CONFIG` group name, or `all` for the whole body (also the fallback if the target's missing or unrecognized - Eye is deliberately not a valid target, so the eye's blink always stays independent of any effect).
`voice.py`'s `extract_effect_tags()` strips the tags out before the text is ever sent to ElevenLabs - so a bracket is never spoken aloud - while remembering where in the *clean* text each tag's contents fall; once ElevenLabs timestamps that clean text, those character positions turn into exact playback times the same way mouth timing does, via `_cues_to_time_ranges()`.
`talk.py`'s `effect_color()` renders each effect (`[sparkle]` flickers randomly between a few bright colors per group per frame, `[rainbow]` cycles hue smoothly, `[glow]` pulses each group's own color up and down, `[flash]` is solid white) for exactly the cue's time window, but only on the targeted group(s) - every other group keeps its normal color, and the eye's blink is left alone throughout regardless of target.

**Tuning the mouth timing without burning API calls:** `--save-fixture` caches one real ElevenLabs response (audio + alignment) to a JSON file; `--replay` plays it back repeatedly (press Enter to repeat) through the same `build_talk_frame()`/DDP path real conversations use, re-deriving the mouth timeline from the cached alignment every time - so a change to `_open_intervals_from_alignment()`/`MOUTH_MERGE_GAP` can be checked against the exact same real speech over and over, with no new network calls and no different text each time.

```
python3 talk.py --save-fixture test_fixture.json --text "Some line with a good mix of pauses and M/B/P sounds."
python3 talk.py --replay test_fixture.json --dry-run    # or without --dry-run, against the real hardware
```

### Running Without a Laptop (`--pi`)

`--pi` runs the whole conversation loop directly on the Raspberry Pi wired to the controller (`cauldron`, the same Pi the `ChristmasUnicornLightShow` repo's companion Halloween prop project uses) - no laptop needed at the display at all, so a kid can just walk up and use it.

What's different from the normal (laptop) mode:

- **Trigger:** holding a USB gamepad button (the same physical pad `cauldron` uses) starts recording; releasing it stops - see `gamepad.py`. No keyboard, no Enter key.
- **Microphone:** a USB webcam's built-in mic (`config.PI_MIC_DEVICE`) - the Pi has no onboard microphone on any model, so this is required extra hardware, not a software gap.
- **Speaker:** the Pi's own 3.5mm headphone jack (`config.PI_SPEAKER_DEVICE`), with a powered speaker plugged into it.
- **Audio backend:** ALSA (`arecord`/`aplay`) directly, not PulseAudio/PipeWire - the Pi doesn't have that compatibility layer installed, and `aplay` is also how `cauldron`'s own `audio.py` already plays sound on this exact device.

One-time setup on the Pi itself:

```bash
sudo apt install python3-pygame   # for gamepad.py - see requirements.txt's own note on this
git clone git@github.com:airalcorn2/ChristmasUnicornLightShow.git ~/unicorn_equalizer
cd ~/unicorn_equalizer
python3 -m venv --system-site-packages .venv   # --system-site-packages: so pygame (apt, above) is visible
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in the same three keys as the laptop setup
```

Copy the show's two xLights XML files to sit next to (one level up from) wherever you cloned the project, matching how it already works on a laptop:

```bash
scp xlights_rgbeffects.xml xlights_networks.xml wizard@cauldron.local:~/
```

Then, on the Pi:

```bash
.venv/bin/python talk.py --pi --brightness 0.3
```

`gamepad.TALK_BUTTON` (currently button index 2) was confirmed by listening live for a real press with `python3 gamepad.py` run standalone, not guessed - re-confirm the same way if the physical gamepad ever changes.
`config.PI_MIC_DEVICE`/`config.PI_SPEAKER_DEVICE` were confirmed the same way (`arecord -l`/`aplay -l` plus an actual recorded-and-played-back test), not assumed from the device names alone - re-confirm with those same commands if the webcam, speaker, or anything plugged in before them ever changes.

## Trivia

A two-team Christmas trivia game, separate from everything above: it's phone-driven rather than gamepad-driven, and runs as its own standalone script (`trivia_game.py`), not a `talk.py` flag - modeled closely on the `cauldron` Halloween project's own trivia game.

Questions come from a [public Jeopardy! clue dataset](https://github.com/jwolle1/jeopardy_clue_dataset) (its own maintainer prefers not to credit where the clues were sourced from), filtered down to Christmas/holiday-adjacent categories and voice-cached ahead of time by `trivia_data.py` - run that once before a party:

```bash
.venv/bin/python trivia_data.py              # the real thing: downloads, filters, samples, and voice-caches
.venv/bin/python trivia_data.py --skip-audio # just (re)write trivia/christmas_clues.json, no ElevenLabs calls - for tuning the keyword filter
.venv/bin/python trivia_data.py --batch 1     # a whole new set of questions for another night (never shares a clue with batch 0)
```

It never runs during a live round. One extra dependency beyond the core project's for QR code generation: `qrcode` (already in `requirements.txt`).

### A Kid-Friendly Question Bank

For a kids' crowd, run `trivia_kids.py` instead:

```bash
.venv/bin/python trivia_kids.py
```

Rather than filtering the Jeopardy dataset, this generates original Christmas/holiday trivia with Gemini, aimed at roughly ages 6-10 and kept to family-friendly topics (Santa, reindeer, presents, snowmen, cookies, classic Christmas movies) rather than anything genuinely scary or inappropriate.
It writes to the exact same `trivia/christmas_clues.json` and `trivia/audio/` that `trivia_data.py` does, so this is a drop-in swap - `trivia_game.py` doesn't change either way, and running `trivia_data.py` again later switches back to the adult, Jeopardy-sourced bank.
Pass `--count` to change how many questions it generates (24 by default, split evenly across the two rounds), or `--skip-audio` to iterate on the generation prompt without waiting on ElevenLabs each time, same as `trivia_data.py`'s own flag.

There's no `excluded_ids.json`-style review step here - these questions aren't pulled from a vetted dataset, they're freshly invented each run, so give `trivia/christmas_clues.json` a skim for accuracy and appropriateness before the party rather than assuming it's already been checked.
There's also no `--batch` equivalent: just re-run the script for an entirely new set, since nothing here is seeded for reproducibility.

Host a round with `.venv/bin/python trivia_game.py` (`--dry-run` to test the web/game flow without the real controller, `--pi` to run directly on the Raspberry Pi with ALSA audio, `--official-colors` to use the real show's own colors - same flags/conventions as `talk.py`/`equalizer.py`). It prints two addresses: a **group** screen (put this on a TV or other shared display) that opens on the game's title and a QR code per team, then switches to a live scoreboard and the current question once the round starts; and a **team** page per team, reached by scanning that team's QR code, where that team submits answers on their own phones.

Play alternates strictly between the two teams each question - whichever team held a question, the other team holds the next one, regardless of who scored - with one twist: if the team up misses (or the clock runs out), the other team gets a one-time steal attempt at the same question before play moves on. Answers are free text - just the answer itself, not phrased as a question - graded live by Gemini rather than a human judge or a local heuristic; the same call also writes the Unicorn's in-character reaction, a gentle tease for a wrong answer or warm celebration for a right one, naming the visitors' actual answer and/or the correct one rather than reacting generically - and may use this project's own light-effect tags (see [Talking to the Unicorn](#talking-to-the-unicorn)), which fire for real through `talk.play_speech()` exactly as they do in normal conversation. Unlike every other mode in this project, that live grading means trivia genuinely needs network access during play, not just to prepare beforehand.

A tied score once the question pool runs out goes to sudden death: a single numeric question from `trivia_tiebreakers.py` (50 hand-researched, individually-sourced facts, e.g. "how many reindeer pulled Santa's sleigh in the original 1823 poem?") that both teams guess at once - whichever guess lands closer wins outright, with a genuine coin flip if neither guess parses as a number or both land exactly as close. The round closes with a short recap line, written live by Gemini to reference how that specific game actually went (the final score, a category someone dominated, whether a steal or the tiebreaker settled it), not a generic "good game" line.

Unlike `cauldron`'s three rotating witch personas, this project has exactly one ElevenLabs voice, so every line - clue reads, judging banter, the tiebreaker, the closing recap - is the same Christmas Unicorn persona throughout. See `trivia_game.py`'s own module docstring for the exact rules and the asyncio/lighting architecture (notably: only one thing may ever send DDP frames at a time, so `trivia_lights.py`'s idle animation is explicitly stopped before every spoken line and restarted only once it finishes).

## Notes

- Both `test_lights.py` and `equalizer.py` have been run live against the real controller/hardware (not just `--dry-run`), including the node-to-pixel mapping, individual group isolation, the audio-reactive bands, the eye blink, the mouth gate, and `--official-colors`.
- If the system audio sink is idle, `parec` can take a second or two to wake it up before data starts flowing; that's expected, not a bug.
- `talk.py` has been run live end-to-end (mic -> transcription -> Gemini -> ElevenLabs -> real hardware) - the mouth timing specifically is still being tuned against real playback (see `--replay` above), so treat its current feel as a work in progress rather than finished.
- Light effects (`[sparkle:target]`/`[rainbow:target]`/`[glow:target]`/`[flash:target]`) have been verified against real Gemini replies and real ElevenLabs timing data: Gemini reliably wraps only the relevant phrase in a correctly-matched tag, picks a sensible target (the specific group asked about, or `all` when asked for everything at once), and the resulting cue's time window lines up with when that phrase is actually spoken - but this hasn't been watched on the real hardware yet, so do that before trusting it at the display.
- The Raspberry Pi bridge (see [Network Setup](#network-setup)) has been verified end-to-end against the real controller: ICMP, the controller's HTTP API, and actual DDP/UDP frames (`equalizer.py` confirmed to light the real hardware) all traverse it correctly.
- `--pi` mode: both the individual hardware (gamepad button, webcam mic, headphone-jack speaker) and the full conversation loop end-to-end have been run live on the real Pi, including real multi-turn conversations with correct mouth-sync and light effects. `_PLAYBACK_STARTUP_LATENCY` was tuned for `paplay` on a laptop and has not been separately re-measured for `aplay` on the Pi, though no sync issues have been observed there in practice.
- Trivia (`trivia_game.py`): the web layer, full game rules (primary/steal/scoring/tiebreaker/coin-flip), live Gemini judging with effect-tag banter, and the finale have all been verified end-to-end via an automated WebSocket client driving a real `--dry-run` round - but it hasn't been played live against the real controller/hardware yet (mouth-sync and lighting during an actual round), and the Christmas keyword filter/50-question tiebreaker bank, while hand-reviewed, haven't been proofread by another person before a real party.

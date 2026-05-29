# AutoCue

Automatically place hot cues on tracks in your Rekordbox 7 library.

AutoCue reads Rekordbox's own beat grid and phrase analysis, then writes hot cues back into the library — so every track is performance-ready before you even open Rekordbox.

## How it works

Rekordbox stores its library in a SQLCipher-encrypted SQLite database (`master.db`). AutoCue uses [pyrekordbox](https://github.com/dylanljones/pyrekordbox) to read beat grids and phrase data from Rekordbox's analysis files (`.DAT`/`.EXT`), then writes hot cues either directly to the database or via XML import.

## Features (planned)

- Auto-place hot cues at detected phrase boundaries (intro, verse, drop, chorus, outro)
- Assign cue colors by section type
- Add memory cues 16 bars before each structural point (visible on waveform overview)
- Support for CBR MP3, WAV, AIFF, FLAC
- XML export mode for safe, non-destructive import
- CLI interface: run on a single track or your entire library

## Requirements

- macOS (Rekordbox library path is macOS-specific for now)
- Rekordbox 7 installed and tracks analyzed
- Python 3.10+
- Rekordbox **closed** before running (required for DB access)

## Installation

```bash
pip install pyrekordbox
```

> Full install instructions coming once the initial implementation is ready.

## Usage

```bash
# Coming soon
python -m autocue --track "My Track.mp3"
python -m autocue --library   # process entire library
```

## Data model

Hot cues and memory cues live in the `djmdCue` table in `master.db`:

| Field | Meaning |
|---|---|
| `Kind` | `0` = memory cue, `1–8` = Hot Cues A–H |
| `InMsec` | Position in milliseconds |
| `OutMsec` | Loop end (`-1` if not a loop) |
| `Color` | Palette index (`-1` = none) |
| `Comment` | Cue label |

## Prior art

- [pyrekordbox](https://github.com/dylanljones/pyrekordbox) — Python library for reading/writing the Rekordbox database
- [djcues](https://github.com/mcroydon/djcues) — automated cue placement
- [Automark-for-Rekordbox](https://github.com/MichelleAppel/Automark-for-Rekordbox)
- [CueGen](https://github.com/mganss/CueGen) — Mixed In Key to Rekordbox cues

## License

MIT

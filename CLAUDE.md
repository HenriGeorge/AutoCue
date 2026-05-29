# AutoCue — Claude Code Guide

## What this project is

AutoCue places hot cues on Rekordbox 7 tracks automatically using two tools:

1. **Python CLI** (`autocue/`) — reads Rekordbox's ANLZ analysis files directly,
   places cues at phrase boundaries (Intro, Verse, Chorus, Outro).
   Outputs a Rekordbox XML for import.

2. **Web app** (`docs/index.html`) — browser-based, single HTML file, no build step.
   User uploads a Rekordbox XML, gets a new XML with cues at configurable bar intervals
   OR phrase positions (via Pyodide + pyrekordbox running in WASM).
   Hosted on GitHub Pages.

## Architecture

```
autocue/
  models.py    — PhraseLabel enum + CuePoint dataclass
  analyzer.py  — reads ANLZ .EXT (PSSI phrases) + .DAT (PQTZ beat grid)
  writer.py    — writes CuePoints to Rekordbox XML via pyrekordbox.rbxml
  cli.py       — argparse CLI; --track / --track-id / --library
  __main__.py  — entry point

docs/
  index.html   — entire web app (CSS + JS inline, no dependencies except CDN)

tests/
  test_models.py    — 36 tests
  test_analyzer.py  — 25 tests (mocked pyrekordbox objects)
  test_writer.py    — 25 tests
```

## Development commands

```bash
pip install -e ".[dev]"              # install with test deps
pytest                               # run all 86 tests

autocue --library --dry-run          # preview without writing
autocue --track "Song Title"
autocue --library --overwrite        # re-generate for all tracks
```

## Key constraints

- **Rekordbox must be closed** before running the CLI (DB is SQLCipher-locked while open).
- **pyrekordbox API**: use `Rekordbox6Database` from `pyrekordbox.db6`. The `add_track()` method takes the file path as a positional argument, not a keyword argument.
- **ANLZ parsing**: wrap `db.read_anlz_file()` and `get_tag()` calls in `try/except Exception` — pyrekordbox raises `ConstError` / `IndexError` for unsupported ANLZ format versions and missing tags. Affected tracks are silently skipped.
- **Slot numbering**: `CuePoint.slot` is 0-indexed (0 = A … 7 = H), matching the Rekordbox XML `Num` attribute directly. No off-by-one conversion needed.
- **XML import is slot-level additive**: Rekordbox only writes slots present in the imported XML. Slots absent from the XML are left untouched in Rekordbox. The app intentionally only wipes slots it will overwrite.
- **Web app**: single self-contained HTML file. No build step, no npm, no framework. All changes go to `docs/index.html`. Theme variables use CSS custom properties (`var(--bg)`, `var(--green)`, etc.) so dark mode works automatically on all new elements.

## Testing approach

Tests mock pyrekordbox objects rather than hitting a real database. When adding tests for `analyzer.py`, mock `db.read_anlz_file()` and the returned `AnlzFile` objects with a `.get_tag()` method returning objects that have the expected `.content` structure (`entries`, `mood`, `beat`, `kind`, `time` fields).

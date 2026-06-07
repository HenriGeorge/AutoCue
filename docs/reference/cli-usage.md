# AutoCue CLI Reference

Complete reference for the `autocue` command-line interface. This document covers every
flag accepted by `autocue` and `autocue serve`, the cue-generation strategy fallback,
default paths, exit codes, common errors, and worked examples.

For the surrounding feature documentation see [`docs/FEATURES.md`](../FEATURES.md);
for sibling reference docs see the [Related](#related) section.

---

## 1. Overview

AutoCue is a Python package that automatically places hot cues on tracks in a Rekordbox 7
library. It exposes three surfaces:

| Surface | Entry point | Reads from | Writes to | Network | Intelligence features |
|---|---|---|---|---|---|
| **Python CLI** | `autocue …` | Rekordbox `master.db` + ANLZ files | A Rekordbox XML file you import manually | None | Phrase / bar / heuristic cue generation only |
| **Local server** | `autocue serve` | Rekordbox `master.db` + ANLZ files | Directly back to `master.db` | `localhost:7432` only | Full set: health, mixability, classification, similar, transitions, set builder, auto-tag, comment enrichment, Discogs, discovery, download |
| **Hosted web app** | [`docs/index.html`](../index.html) (GitHub Pages) | A Rekordbox XML you upload | A new Rekordbox XML you download | Browser-only (no upload) | Bar / phrase cue generation only |

This document is about the first two surfaces. Both are driven from a single argparse
entry point in [`autocue/cli.py`](../../autocue/cli.py); the `autocue serve` subcommand is
dispatched in the first six lines of `main()` before the regular argparse parser runs.

### What `autocue` (default) does

1. Opens the Rekordbox `master.db` via `pyrekordbox.MasterDatabase` (or
   `Rekordbox6Database` on older pyrekordbox). See `cli.py:9–11`, `cli.py:87–99`.
2. Resolves the target tracks: a single track (by title or ID) or the whole library
   (optionally filtered to a named playlist).
3. For each track, calls `generate_cues_for_track()` from `autocue/generator.py`,
   which picks the best of three strategies (phrase → bar → heuristic).
4. Prints a per-track summary of every cue placement (`cli.py:213–221`).
5. Unless `--dry-run` is set, writes a Rekordbox XML to `--output` (default
   `autocue_import.xml`) that you import into Rekordbox via
   **File → Import Library**.

The CLI **never** writes to `master.db` directly. Direct DB writes only happen via the
local server (`autocue serve` + the `/api/apply` endpoint).

### What `autocue serve` does

1. Starts a FastAPI app on `localhost:7432` (or the next free port within ten of it),
   serving the bundled web UI at `/` and a REST API under `/api/...`.
2. Optionally opens the URL in your default browser.
3. Lifespans-attaches the Rekordbox database connection (`serve/deps.py`) and pre-warms
   the similarity index in a background daemon thread.

For the full API surface area, see [`server-and-api.md`](./server-and-api.md).

---

## 2. Installation

AutoCue requires **Python 3.10 or newer** (`pyproject.toml:9`) and a Rekordbox 7
installation whose library you want to process. ANLZ analysis files must exist on disk
for phrase mode to work — run **Track Analysis → Phrase** in Rekordbox first.

```bash
# Core install (CLI + serve, no optional features)
git clone https://github.com/HenriGeorge/AutoCue.git
cd AutoCue
pip install -e .

# Dev extras — adds pytest, httpx, hypothesis for running the test suite
pip install -e ".[dev]"

# Download extras — adds yt-dlp for the /api/download endpoint (server only)
pip install -e ".[download]"
```

### Runtime dependencies

Pulled in automatically by `pip install -e .` (see `pyproject.toml:10–15`):

| Package | Used by | Why |
|---|---|---|
| `pyrekordbox>=0.3.0` | All modes | Rekordbox DB + ANLZ parser |
| `fastapi>=0.110.0` | `autocue serve` | HTTP framework |
| `uvicorn[standard]>=0.29.0` | `autocue serve` | ASGI server |
| `psutil>=5.9` | `db_writer.rekordbox_is_running()` | Detect a running Rekordbox process before writing |

### Optional extras

| Extra | Adds | Required for |
|---|---|---|
| `[dev]` | `pytest`, `httpx`, `hypothesis` | Running `pytest`; the `test_properties.py` Hypothesis suite will fail to collect without it |
| `[download]` | `yt-dlp` | `autocue serve`'s `/api/download` and `/api/download/album` endpoints. Also requires an `ffmpeg` binary on `PATH` (`autocue/download.py:ffmpeg_available()`) |

### System requirements

- **OS:** macOS or Windows (where Rekordbox 7 runs). Linux works for the CLI/serve
  process itself but Rekordbox does not run on Linux.
- **Python:** 3.10, 3.11, or 3.12 (the CI matrix; see `.github/workflows/ci.yml`).
- **Rekordbox:** version 7 with at least BPM/beat analysis completed. Phrase mode also
  requires Rekordbox phrase analysis (`.EXT` ANLZ files).
- **ffmpeg:** only for `[download]`. Install via Homebrew (`brew install ffmpeg`) or
  download from <https://ffmpeg.org/>.

---

## 3. Quick reference

Every flag accepted by `autocue` (default subcommand) and `autocue serve`:

### `autocue` (default — generate cue XML)

| Flag | Type | Default | Required | Description |
|---|---|---|---|---|
| `--track TITLE` | str | — | one of `--track`, `--track-id`, `--library` | Process one track by title. Mutually exclusive with the other two. |
| `--track-id ID` | int | — | one of | Process one track by Rekordbox `DjmdContent.ID`. Mutually exclusive. |
| `--library` | flag | False | one of | Process every analyzed track in the library. Mutually exclusive. |
| `--output FILE` | str | `autocue_import.xml` | no | Path to write the Rekordbox-importable XML. Ignored if `--dry-run`. |
| `--dry-run` | flag | False | no | Print the cue plan but write nothing. |
| `--overwrite` | flag | False | no | In `--library` mode, re-process tracks that already have hot cues. |
| `--uncued-only` | flag | False | no | In `--library` mode, restrict to tracks with zero existing hot cues. Ignored when `--overwrite` is also set. |
| `--playlist NAME` | str | — | no | Restrict `--library` to tracks in the named Rekordbox playlist. |
| `--db-path PATH` | str | auto (macOS) | required on Windows | Path to `master.db`. Auto-detected on macOS by pyrekordbox. |

### `autocue serve` (run local server)

| Flag | Type | Default | Description |
|---|---|---|---|
| `--port N` | int | `7432` | TCP port for the FastAPI server. Falls back to the next free port within +9 if `--port` is busy. |
| `--no-browser` | flag | False | Do not auto-open the browser at the URL. |
| `--db-path PATH` | str | auto (macOS) | Path to `master.db`. Required on Windows. |

The `serve` parser is defined inline in `cli.py:21–30` rather than as an argparse
sub-parser, so `autocue serve --help` is currently produced by the inline parser and does
**not** appear under `autocue --help`.

---

## 4. Subcommands

### 4.1 `autocue` (default)

Invocation:

```bash
autocue (--track TITLE | --track-id ID | --library)
        [--output FILE]
        [--dry-run]
        [--overwrite]
        [--uncued-only]
        [--playlist NAME]
        [--db-path PATH]
```

Exactly one of `--track`, `--track-id`, `--library` is required — argparse enforces this
via a `add_mutually_exclusive_group(required=True)` (`cli.py:37`). Omitting all three
prints usage and exits with code 2.

The CLI always opens the Rekordbox database first (`cli.py:87–99`). If pyrekordbox can
neither auto-detect a database (no `--db-path`) nor open the path you supplied, it exits
with code 1 after printing both the underlying exception and the "Make sure Rekordbox is
closed before running AutoCue." reminder.

Generation uses the **default** `GenerationPrefs` (`generator.py:42–53`) — the CLI does
not currently expose flags for `mode`, `bars_interval`, `start_bar`, `max_cues`,
`memory_cue_mode`, `add_fill_cues`, or `slot_priority`. To customise those, use the
local server's web UI or hit `POST /api/generate` directly.

### 4.2 `autocue serve`

Invocation:

```bash
autocue serve [--port N] [--no-browser] [--db-path PATH]
```

Behaviour (`autocue/serve/app.py:64–96`):

1. Configures `INFO`-level logging.
2. If `--port` is taken:
   - If the existing listener responds to `GET /api/status`, it is treated as an
     already-running AutoCue and the browser is reopened to that URL instead of
     starting a second instance (`app.py:32–38`, `app.py:71–77`).
   - Otherwise it scans ports `port+1` … `port+9` for a free one and switches to it.
   - If none are free, prints an error and exits with code 1.
3. Prints the chosen URL and (unless `--no-browser`) opens it in the default browser
   after a 1-second delay (so uvicorn has time to bind).
4. Runs uvicorn at `127.0.0.1:port` with `log_level="warning"`.

The server only accepts connections from the local machine and CORS is restricted to
`null` (the file:// origin) plus the bound `localhost`/`127.0.0.1` URLs. Do not widen
this — the server is allowed to write directly to your Rekordbox database.

---

## 5. Track selection modes

The three target flags are mutually exclusive and exactly one is required.

| Flag | Resolver | Behaviour when no match |
|---|---|---|
| `--track "Title"` | `analyze_by_title()` in `analyzer.py:275` | `Track not found: 'Title'` → exit 1 |
| `--track-id 42` | `analyze_by_id()` in `analyzer.py:295` | `Track not found: ID=42` → exit 1 |
| `--library` | `_process_all()` / `_process_playlist()` in `cli.py:171,182` | `No tracks found in library.` → exit 0 |

### Single-track modes

`--track` and `--track-id` both:

1. Look up the `DjmdContent` row.
2. Call `generate_cues_for_track(content, db, prefs)`.
3. If the result is empty (no phrase data, no BPM, no duration), print
   `No cue data generated for ...` and exit 0.
4. Otherwise wrap the single result in a list and proceed to summary + XML write.

`--track` matches by Rekordbox `Title` field. If multiple tracks share a title,
pyrekordbox returns the first match — use `--track-id` to disambiguate.

### Library mode

`--library` walks every row returned by `db.get_content().all()` (`cli.py:175`). For
each, it calls `generate_cues_for_track()` and keeps the track only if at least one cue
was produced.

The library scan is intentionally simple and synchronous — for a 5,000-track library
expect ~30–90 seconds, dominated by ANLZ I/O.

#### `--playlist NAME`

When combined with `--library`, restricts the scan to a named playlist:

```bash
autocue --library --playlist "Weekend Set"
```

If the playlist does not exist (`cli.py:186–194`), the CLI prints the error plus a
sorted list of every available playlist name and exits 1. Comparison is exact and
case-sensitive — `"weekend set"` is not the same as `"Weekend Set"`.

#### `--overwrite` and `--uncued-only`

After collecting candidate tracks, library mode applies a "should I re-process this?"
filter (`cli.py:142–154`):

- **No flags:** tracks that already have hot cues are skipped with a per-track
  `skipping — already has N hot cue(s). Use --overwrite to replace.` line.
- **`--overwrite`:** the filter is disabled; every candidate is processed.
- **`--uncued-only`:** identical to the default behaviour and primarily exists for
  parity with the local-server UI. Note from `cli.py:64–70` that `--uncued-only` is
  silently ignored when `--overwrite` is also passed.

If after filtering no tracks remain, the CLI exits 0 with
`No eligible tracks to process (all already have hot cues). Use --overwrite to re-generate.`

---

## 6. Cue generation strategy

The CLI does not expose `mode` directly; it always runs the generator with
`GenerationPrefs()` defaults, which means **`mode="auto"`**. The generator falls back
through three strategies in order:

| Order | Strategy | Used when | Confidence | Source |
|---|---|---|---|---|
| 1 | `phrase` | `analyze_track()` returns at least one CuePoint (ANLZ `PSSI` + `PQTZ` present) | `1.0` | `generator.py:204–213` |
| 2 | `bar` | BPM > 0 and phrase data absent | `0.6` | `generator.py:144–165` |
| 3 | `heuristic` | No usable BPM | `0.3` | `generator.py:168–180` |

The `bpm > 0` check (`generator.py:216`) uses `float(getattr(content, "BPM", 0) or 0) / 100`
because the Rekordbox database stores BPM as `int×100` — `"0.0"` is a truthy string
that converts to `0.0`, hence the explicit positivity guard.

### Phrase mode

Reads `PSSI` (phrase segments) and `PQTZ` (beat grid) from the track's ANLZ files via
`analyzer.analyze_track()`. Cues land on real phrase boundaries (Intro, Verse, Chorus,
Bridge, Outro, Up, Down) and are coloured by phrase type via `LABEL_COLORS`.

Smart slot ordering then reassigns slot numbers (`generator.py:65–121`):

- **Slot A** — first non-Intro phrase chronologically (the DJ mix-in point). Its name
  is appended with `(Mix In)` so it is visible on a CDJ display.
- **Slot B** — first OUTRO phrase chronologically (the mix-out window). Named "Outro"
  or has `(Outro)` appended.
- **Slots C+** — remaining phrases ordered by musical importance: CHORUS, UP, OUTRO,
  VERSE, DOWN, BRIDGE, INTRO, with chronological tiebreaking
  (`generator.py:_SMART_PRIORITY` at 21–30).

Smart ordering only runs in phrase mode and only when `slot_priority="smart"` (default).

### Bar mode

When phrase data is unavailable, cues are placed at fixed bar intervals from `inizio_ms`
(the first-beat offset). The default `GenerationPrefs` give:

- `bars_interval = 16` (cues every 16 bars)
- `start_bar = 1` (start at bar 1)
- `max_cues = 8` (MAX_HOT_CUES)
- `inizio_ms = 0`

Formula (`generator.py:148–164`):

```
bar_ms        = (60_000 / BPM) * 4              # 4/4 assumed
position[i]   = inizio_ms + (start_bar - 1 + i * bars_interval) * bar_ms
```

The loop iterates `max_cues + 64` times to give headroom for a negative `inizio_ms`
that would skip the first few candidates. Cues are skipped when `position < 0` and the
loop breaks when `position >= duration`.

Cue names are `Bar 1`, `Bar 17`, `Bar 33`, … using slot SLOT_COLORS for colour.

### Heuristic mode

Used when BPM is missing or zero. Places one cue every 30,000 ms (`generator.py:168–180`)
up to `max_cues`, named `0:00`, `0:30`, `1:00`, …. Duration defaults to 300 s if the
track has no `Length` field. Confidence 0.3.

### How the flags interact

The CLI flags interact with generation as follows:

| Flag | Effect on `--library` selection | Effect on per-track generation |
|---|---|---|
| `--overwrite` | Disables the "skip tracks with existing cues" filter | None — generation is the same |
| `--uncued-only` | Effectively the default (no overwrite). Ignored if `--overwrite` is also set | None |
| `--playlist NAME` | Restricts the scan to one playlist | None |
| `--dry-run` | None | None — but the XML write step is skipped |

`--bars-interval`, `--start-bar`, `--max-cues` are **not** exposed on the CLI today.
The default `bars_interval=16`, `start_bar=1`, `max_cues=8` are what you get. Use the
local server's `POST /api/generate` for non-default values.

---

## 7. Memory cue options

The CLI does **not** expose memory-cue options as flags. By default
`GenerationPrefs.memory_cue_mode = "none"` (`generator.py:49`), so no memory cue is
added to the output XML.

For completeness, the three modes available on `GenerationPrefs` (and the server's
`POST /api/generate` body) are:

| `memory_cue_mode` | What is added (slot = -1, Kind = 0 in DjmdCue) |
|---|---|
| `"none"` | No memory cue. |
| `"load_only"` | One **Load Point** memory cue at the first hot-cue position (phrase mode) or at `max(0, inizio_ms)` (bar/heuristic). `generator.py:265–272` |
| `"all"` | Phrase-mode only. Adds Load Point + **Mix In** (slot-0 hot-cue position, if >500 ms from Load) + **Mix Out** (last OUTRO phrase) + **Warning** (16 bars before track end, only when the OUTRO is shorter than 8 bars or absent). `generator.py:274–309` |

The legacy `add_memory_cue: bool` field still works (`generator.py:48`) — `True` is
treated as `memory_cue_mode="load_only"` by `_resolve_memory_cue_mode()`
(`generator.py:56–62`).

Memory cues are inserted as `CuePoint(slot=-1, …)` and then prepended to the cue list
sorted by position so CDJ displays them in playback order (`generator.py:311–313`).

To use these from the command line today, run `autocue serve` and open the web UI.

---

## 8. Output paths

### Default

`./autocue_import.xml` in the current working directory (`cli.py:50`).

### `--output FILE`

Override the output path:

```bash
autocue --library --output ~/Desktop/cues.xml
autocue --track-id 42 --output /tmp/track42.xml
```

The path may be relative (resolved against `cwd`) or absolute. The directory must
already exist — `writer.write_xml()` does not create parent directories.

### `--dry-run`

Skips the XML write entirely (`cli.py:162–164`). The summary still prints to stdout so
you can verify cue placements before committing. Any later flags that affect output
(`--output`, etc.) are silently ignored under `--dry-run`.

```bash
autocue --library --dry-run
# … per-track summary …
# Dry run — no files written.
```

### Import flow (post-CLI)

After the CLI exits successfully it prints:

```
Wrote autocue_import.xml
Import in Rekordbox: File > Import Library > select the XML file above.
```

The XML is **slot-level additive** — Rekordbox only overwrites slots that exist in the
imported XML. Slots untouched by AutoCue (e.g. a manually placed slot G) remain in the
DB. See `CLAUDE.md` ("XML import is slot-level additive") for the rationale.

---

## 9. Database access

### Auto-detect (macOS)

If `--db-path` is omitted, pyrekordbox auto-detects the database location. On macOS this
is typically `~/Library/Pioneer/rekordbox/master.db`. The CLI passes no arguments to
`MasterDatabase()` (`cli.py:89`) which delegates the lookup entirely to pyrekordbox's
own discovery logic.

### `--db-path PATH` (Windows / non-standard installs)

On Windows the database lives under `%APPDATA%\Pioneer\rekordbox\master.db`. Pass it
explicitly:

```bash
autocue --library --db-path "C:\Users\you\AppData\Roaming\Pioneer\rekordbox\master.db"
```

`autocue serve --db-path …` works the same way.

### Rekordbox-must-be-closed invariant

The Rekordbox `master.db` is a **SQLCipher-encrypted database that pyrekordbox opens in
shared-cache mode but Rekordbox holds an exclusive lock on while running**. When
Rekordbox is open:

- The CLI's read-only walk usually still works (you may see slower reads).
- Any **write** operation from the local server (`/api/apply`, `/api/cue-tools-stream`,
  etc.) will fail. `db_writer.rekordbox_is_running()` (`db_writer.py:28–32`) checks
  `psutil.process_iter()` for any process whose name contains `"rekordbox"` and aborts
  before touching the DB.

The CLI itself never writes to `master.db` (it produces an XML), so it is somewhat more
tolerant of Rekordbox being open. **Best practice is to close Rekordbox anyway** — when
pyrekordbox cannot open the DB at all you will see:

```
Error: could not open Rekordbox database — <pyrekordbox exception>
Could not auto-detect Rekordbox database. On Windows, use --db-path to point to your master.db.
Make sure Rekordbox is closed before running AutoCue.
```

…and the process exits with code 1 (`cli.py:88–99`).

---

## 10. `autocue serve` mode

The `serve` subcommand starts the FastAPI server that backs the local web UI and the
intelligence features (everything in `autocue/analysis/`).

```bash
# Standard launch — opens the browser at http://localhost:7432
autocue serve

# Pick a different port
autocue serve --port 8080

# Headless — useful in a screen/tmux session or for CI
autocue serve --no-browser

# Custom DB (Windows or alternate install)
autocue serve --db-path "C:\path\to\master.db"
```

Notable behaviour:

- **Port collisions:** if `--port` (or the default `7432`) is taken, `serve` probes
  `GET /api/status` on that port. If the response identifies an existing AutoCue, it
  reopens the browser at the running instance and returns instead of starting a second
  copy. Otherwise it scans the next nine ports, switching to the first free one
  (`app.py:71–90`).
- **CORS:** only `null`, `http://localhost:{port}`, and `http://127.0.0.1:{port}` are
  permitted origins (`app.py:45–57`). Do not change this — the server writes to your
  music DB and should never be reachable from arbitrary web pages.
- **GZip:** compression is enabled for responses ≥ 1 KB; SSE streams pass through
  uncompressed (Starlette skips gzip for `text/event-stream`).
- **Static UI:** if `docs/` exists adjacent to the installed package, it is mounted at
  `/` (`app.py:59–60`).
- **Index pre-warm:** `serve/deps.py` builds the similarity index in a background
  daemon thread on startup so the first `/api/tracks/{id}/similar` call is fast.

For the API surface, see [`server-and-api.md`](./server-and-api.md).

---

## 11. Exit codes

| Code | When | Source |
|---|---|---|
| `0` | Success (XML written, or `--dry-run` summary printed, or no eligible tracks) | `cli.py:112,124,140,158,164,167` (implicit return) |
| `1` | DB open failure, single-track not found, playlist not found, or `serve` port-scan failure | `cli.py:99,107,119,134`; `app.py:90` |
| `2` | argparse usage error — missing target flag, unknown argument, conflicting mutually-exclusive flags | argparse default |

The CLI never raises an uncaught Python exception under normal use. If you see a
traceback, please file an issue at the repo with the command line and the full output.

---

## 12. Examples

### 12.1 Dry-run preview of the whole library

```bash
autocue --library --dry-run
```

Output:

```
Opening Rekordbox library…
Scanning library…

127 track(s) · 803 cue(s) total

  Atmosphere — Aphex Twin  [phrase]
    [A] 00:08  Verse
    [B] 02:14  Outro
    [C] 00:48  Chorus
    [D] 01:33  Chorus
    [E] 01:01  Up
    …

Dry run — no files written.
```

### 12.2 Single track by title

```bash
autocue --track "Strings of Life"
```

If the title is unique you get a one-track summary and an `autocue_import.xml` is
written. If no track matches:

```
Track not found: 'Strings of Life'
```

…and the process exits with code 1.

### 12.3 Single track by Rekordbox ID

```bash
autocue --track-id 1834 --output /tmp/strings_of_life.xml
```

`--track-id` is the safest way to target a specific track because IDs are unique.

### 12.4 Whole library, skipping already-cued tracks

```bash
autocue --library
```

This is the default and intended workflow. The CLI prints a `skipping` line for each
track that already has hot cues and excludes them from the XML.

### 12.5 Whole library, re-generating everything

```bash
autocue --library --overwrite
```

Re-processes every track regardless of existing cues. The output XML will overwrite
every cue slot AutoCue places (other slots remain untouched after import).

### 12.6 One playlist only

```bash
autocue --library --playlist "Saturday Night"
```

Restricts the scan and XML to tracks in the **Saturday Night** Rekordbox playlist. If
the playlist does not exist the CLI prints the available playlist names and exits 1 —
useful for discovering the exact name to pass.

### 12.7 Tracks with no existing cues, custom output

```bash
autocue --library --uncued-only --output ~/Desktop/uncued.xml
```

Equivalent to the default scan (which already skips tracks with cues) but written to a
custom location. Use this in tandem with `--playlist` to focus on a freshly-imported
collection.

### 12.8 Windows DB path

```bash
autocue --library --db-path "C:\Users\dj\AppData\Roaming\Pioneer\rekordbox\master.db"
```

Required on Windows — pyrekordbox's auto-detect is macOS-only.

### 12.9 Local server, custom port, no browser

```bash
autocue serve --port 8000 --no-browser
```

Starts the server on `localhost:8000` without opening a browser. Useful for SSH +
remote-port-forwarding workflows.

### 12.10 Verify port reuse

```bash
autocue serve            # tab 1 — starts on 7432
autocue serve            # tab 2 — detects existing AutoCue, reopens the browser
```

The second invocation does **not** start a second instance; it identifies the first via
`GET /api/status` and returns.

---

## 13. Common errors and fixes

### "could not open Rekordbox database"

```
Error: could not open Rekordbox database — <details>
```

Possible causes:

1. **Rekordbox is running.** Close it completely (Cmd-Q on macOS, right-click the tray
   icon → Quit on Windows). The CLI may still open the DB while Rekordbox is running,
   but pyrekordbox's behaviour here is version-dependent.
2. **No auto-detect on Windows.** Pass `--db-path` explicitly.
3. **Non-standard install location** (e.g. you moved the Rekordbox data dir). Same
   fix: `--db-path`.
4. **Wrong Rekordbox version.** AutoCue targets Rekordbox 7. Earlier versions use a
   different DB schema that pyrekordbox may not open.

### "Track not found"

```
Track not found: 'My Song'
Track not found: ID=42
```

For `--track`, the lookup is case-sensitive and matches on the exact `Title`. For
`--track-id`, the ID must exist in `DjmdContent`. Use the local server's
`GET /api/tracks` endpoint or the web UI to find the exact ID.

### "No cue data generated"

```
No cue data generated for 'My Song'.
```

The track has neither phrase data, nor a usable BPM, nor a duration. In `auto` mode the
generator returns an empty list when all three fallbacks fail (typically because the
track has not been analyzed in Rekordbox at all). Run **Analyze Track** in Rekordbox
and re-try.

### "Playlist not found"

```
Error: playlist 'My Set' not found.
Available playlists:
  Saturday Night
  Weekend Closer
  ...
```

Match is exact and case-sensitive. Copy the name verbatim from the printed list.

### Missing ANLZ files / ConstError

You will not see a CLI-level error here, but tracks without ANLZ files (or with ANLZ
versions pyrekordbox does not understand) silently fall back to bar mode. From
`CLAUDE.md`:

> Wrap `db.read_anlz_file()` and `get_tag()` calls in `try/except Exception` —
> pyrekordbox raises `ConstError` / `IndexError` for unsupported ANLZ format versions
> and missing tags. Affected tracks are silently skipped.

If a track you expect to be phrase-cued is showing up as `[bar]` in the summary, the
most likely cause is a missing or unparseable `.EXT` file.

### "Port already in use" (`autocue serve`)

```
Error: port 7432 is in use and no alternative found near it.
Stop the conflicting process or use --port to pick a different one.
```

Only printed when `port` through `port+9` are all busy. Identify the squatting process
(`lsof -i :7432` on macOS) or pass `--port 9000` to pick something far away.

### Database locked / SQLCipher errors

Mid-write SQLCipher locking errors from the server typically mean Rekordbox started
mid-operation. The server's `db_writer` aborts cleanly when `rekordbox_is_running()`
returns true, but if the process started after the check, you will see a SQLAlchemy
`OperationalError` in the server log. Close Rekordbox and retry.

---

## 14. Related

Sibling documents in `docs/reference/`:

- [`cue-generation.md`](./cue-generation.md) — the three placement strategies in
  detail, with the math behind bar mode and the phrase taxonomy.
- [`hot-cue-generation.md`](./hot-cue-generation.md) — slot ordering, colours, memory
  cues, fill cues, and confidence scoring.
- [`server-and-api.md`](./server-and-api.md) — the full `autocue serve` REST API,
  request/response schemas, SSE streams, and the on-startup index pre-warm.

Higher-level documentation:

- [`docs/FEATURES.md`](../FEATURES.md) — end-user feature tour (mirrors the web UI).
- [`README.md`](../../README.md) — install + quick-start.
- [`CLAUDE.md`](../../CLAUDE.md) — architecture, invariants, and contributor notes.

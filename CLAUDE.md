# AutoCue — Claude Code Guide

## What this project is

AutoCue places hot cues on Rekordbox 7 tracks automatically using three tools:

1. **Python CLI** (`autocue/`) — reads Rekordbox's database and ANLZ files directly.
   Fallback strategy: phrase → bar → heuristic. Outputs a Rekordbox XML for import.

2. **Local server** (`autocue serve`) — FastAPI server at `localhost:7432`. Serves the
   web UI and exposes a REST API that reads/writes the Rekordbox database directly.
   No XML export/import needed.

3. **Web app** (`docs/index.html`) — browser-based, single HTML file, no build step.
   User uploads a Rekordbox XML, gets a new XML with cues at configurable bar intervals
   OR phrase positions. Hosted on GitHub Pages. Also served by `autocue serve` in local mode.

## Architecture

```
autocue/
  models.py      — PhraseLabel enum + CuePoint dataclass
  analyzer.py    — reads ANLZ .EXT (PSSI phrases) + .DAT (PQTZ beat grid)
  generator.py   — per-track strategy: phrase → bar → heuristic; GenerationPrefs dataclass
  writer.py      — writes CuePoints to Rekordbox XML via pyrekordbox.rbxml
  db_writer.py   — writes CuePoints directly to DjmdCue; backup + Rekordbox-running check
  cli.py         — argparse CLI; --track / --track-id / --library; `autocue serve` subcommand
  __main__.py    — entry point
  serve/
    app.py       — FastAPI app factory + uvicorn launcher; CORS whitelist (localhost only)
    routes.py    — /api/status, /api/playlists, /api/tracks, /api/generate, /api/apply,
                   /api/generate-apply, /api/generate-apply-stream (SSE),
                   /api/delete-cues, /api/color-tracks, /api/backups, /api/restore
    schemas.py   — Pydantic models for all request/response types
    deps.py      — lifespan DB connection + get_db dependency

docs/
  index.html     — entire web app (CSS + JS inline, no dependencies except CDN)
                   Local mode: detects /api/status on load, hides XML drop zone, loads
                   tracks from server, Apply button writes directly to Rekordbox DB.

tests/
  test_models.py             — 36 tests
  test_analyzer.py           — 25 tests (mocked pyrekordbox objects)
  test_generator.py          — 34 tests (includes memory cue / add_memory_cue prefs)
  test_writer.py             — 37 tests
  test_db_writer.py          — 32 tests (includes BPM→color mapping, color_tracks_by_bpm)
  test_serve_routes.py       — 54 tests (FastAPI TestClient, mocked DB)
  web/
    xml-processing.test.js  — 65 Vitest tests for parseRekordboxXml, generateCues, pickCueColor
    ui-logic.test.js        — 17 Vitest tests for sort labels, memory cue, HTTP error handling

package.json      — dev tooling only (vitest + jsdom); the deployed app has no build step
vitest.config.js  — jsdom environment for web tests
```

## Development commands

```bash
pip install -e ".[dev]"              # install with test deps (includes fastapi, uvicorn, psutil, httpx)
pytest                               # run all 238 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 82 Vitest tests for the web app

autocue serve --no-browser           # start local server on localhost:7432
autocue --library --dry-run          # preview CLI output without writing
autocue --track "Song Title"
autocue --library --overwrite        # re-generate for all tracks
```

## Key constraints

- **Rekordbox must be closed** before running the CLI or clicking Apply in local mode (DB is SQLCipher-locked while open). `db_writer.rekordbox_is_running()` enforces this via psutil.
- **pyrekordbox API**: use `Rekordbox6Database` from `pyrekordbox.db6`. The `add_track()` method takes the file path as a positional argument, not a keyword argument.
- **ANLZ parsing**: wrap `db.read_anlz_file()` and `get_tag()` calls in `try/except Exception` — pyrekordbox raises `ConstError` / `IndexError` for unsupported ANLZ format versions and missing tags. Affected tracks are silently skipped.
- **Slot numbering**: `CuePoint.slot` is 0-indexed (0 = A … 7 = H), matching the Rekordbox XML `Num` attribute directly. In `DjmdCue`, the slot is encoded as `Kind = slot + 1` (Kind=0 is a memory cue). No `Num` column exists in the DB table.
- **DB path**: `Rekordbox6Database` stores the directory as `_db_dir`; the database file is always `_db_dir / "master.db"`. There is no `.db_path` attribute.
- **XML import is slot-level additive**: Rekordbox only writes slots present in the imported XML. Slots absent from the XML are left untouched in Rekordbox. The app intentionally only wipes slots it will overwrite.
- **Web app**: single self-contained HTML file. No build step, no framework. All app changes go to `docs/index.html`. Theme variables use CSS custom properties (`var(--bg)`, `var(--green)`, etc.) so dark mode works automatically on all new elements. `package.json` exists only for dev testing (Vitest + jsdom) — the deployed GitHub Pages app requires no npm.
- **CORS**: the local server (`autocue serve`) only allows origins `null` (file://), `http://localhost:{port}`, and `http://127.0.0.1:{port}`. Do not widen this — the server writes to the Rekordbox database.
- **BPM guard**: always check `float(bpm) > 0` before using BPM in calculations. Rekordbox can store BPM as `"0.0"` (truthy string, zero float) which would cause division by zero.
- **Memory cue (slot = -1)**: `CuePoint.slot = -1` → `Kind = 0` in DjmdCue (CDJ Auto Cue position). Memory cues do not consume hot cue slots. The `add_memory_cue` pref in `GenerationPrefs` prepends one before the hot cues; in phrase mode it anchors to the first phrase, otherwise to `max(0, inizio_ms)`.
- **DjmdContent.ColorID**: VARCHAR(255) FK to `djmdColor.ID` — NOT an integer. Always query `DjmdColor` at runtime and resolve `{SortKey: ID}` mapping. SortKey 1–8 corresponds to Pink/Red/Orange/Yellow/Green/Aqua/Blue/Purple.
- **DjmdKey.Seq**: use `Seq` (Integer) for server-side key sort, not `ScaleName` (lexicographic "10A" < "1A" is wrong). Client-side uses `camelotSortKey()` which converts "8A" → numeric order.
- **Fetch error handling in JS**: always check `r.ok` before reading typed properties from `r.json()`. A 409 response returns `{detail: "..."}` — reading `resp.applied` or `resp.colored` on an error body yields `undefined` and produces misleading toast messages.
- **DjmdCue ID generation**: `DjmdCue.ID` is VARCHAR(255) with no auto-generate default — must call `db.generate_unused_id(DjmdCue)` explicitly when inserting. Also set `UUID=str(uuid4())`, `ContentUUID` from the content row, `InFrame=round(position_ms * 150 / 1000)`, `OutMsec=-1`, and 0 for all other integer fields.
- **Apply performance**: The UI uses `/api/generate-apply-stream` (SSE) which streams progress events `{"processed":N,"total":M,...}` as each track is processed, then a final `{"done":true,...}`. The JS reads via `fetch` + `ReadableStream` (not `EventSource`, since the request is POST). No `AbortSignal.timeout` — the stream has no time limit.
- **Playlist filter**: `/api/tracks` uses `?playlist_id=<int>` (integer FK). The old `?playlist=<name>` param was removed. The frontend dropdown passes the numeric ID.
- **Restore backup**: `/api/restore` accepts `{filename}` (not a full path — validated to be within BACKUP_DIR). The endpoint closes `db._engine.dispose()` before copying the file, then reopens via `Rekordbox6Database` and updates `app.state.db`. WAL/SHM sidecars are handled on both backup and restore.
- **has_phrase**: Populated via `db.get_anlz_path(content, "EXT")` — a fast file-existence check with no parsing. `True` if the .EXT ANLZ file exists on disk.
- **filteredTracks()**: Client-side function that applies search query and phrase-only filter to `parsedTracks`. All write operations (apply, delete, color) use `filteredTracks()` — not `parsedTracks` directly. `parsedTracks` is never mutated by filters.
- **pendingCues**: JS map of `String(trackId) → [{slot,posSec,label,...}]` populated by the "Preview cues" button (calls `/api/generate`). Cleared after Apply completes. Rendered as a secondary timeline bar in each track card.

## Testing approach

Tests mock pyrekordbox objects rather than hitting a real database. When adding tests for `analyzer.py`, mock `db.read_anlz_file()` and the returned `AnlzFile` objects with a `.get_tag()` method returning objects that have the expected `.content` structure (`entries`, `mood`, `beat`, `kind`, `time` fields).

JS tests in `tests/web/` copy functions verbatim from `docs/index.html` and run them in jsdom via Vitest. If you change `parseRekordboxXml`, `generateCues`, `computeCues`, `colorTracksByBpm`, or `applyToRekordbox` in `index.html`, update the corresponding copies in the test files. The `ui-logic.test.js` file tests sort label lookup, memory cue prepend logic, and fetch HTTP error handling using `vi.fn()` mocked responses.

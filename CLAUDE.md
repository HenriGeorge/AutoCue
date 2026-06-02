# AutoCue — Claude Code Guide

## Browser testing

When doing browser testing or UI verification with Chrome DevTools tools:
- **Always send screenshots to the user** using `SendUserFile` after every meaningful state change (before action, after action, result visible). Do not just describe what you see.
- **Screenshots are pre-approved** — take them freely without asking permission. The `mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_screenshot` tool is always allowed.
- Save screenshots to `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/` (the allowed temp dir).

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
  analysis/
    quality.py      — Cue Quality Checker: check_track_health(), check_library_health()
                      Pure DB reads (DjmdCue + DjmdContent). No ANLZ parsing.
                      Scores tracks 0–100; yields fix_tier: phrase/bar/heuristic/none.
    energy.py       — PWAV waveform reader: get_energy_curve() → list[float] per PWAV column (~150ms each)
    score.py        — Mixability score (0–100): intro/outro bars + energy variance + vocal proxy
    classify.py     — Track classification: get_classification() → {primary, scores, bpm, energy_mean}
                      Five categories: warmup/build/peak/after_hours/closing. Cached in _class_cache.
    similar.py      — Cosine similarity on 5-dim feature vector (key, energy, vocal proxy). BPM gate ±8.
                      Builds in-process index on first call; pre-warms _class_cache via _index_track().
    transitions.py  — score_transition(a, b, db) → {overall, bpm, key, energy, end_energy_a, start_energy_b}
    setbuilder.py   — Beam search set builder (width=5). Uses find_similar per step (O(n×K) not O(n²)).
                      build_set(db, start_bpm, end_bpm, duration_minutes, energy_mode, bpm_step_max) → list[dict]
  serve/
    app.py       — FastAPI app factory + uvicorn launcher; CORS whitelist (localhost only)
    routes.py    — /api/status, /api/playlists, /api/tracks, /api/tracks/{id}/artwork,
                   /api/tracks/{id}/audio, /api/tags, /api/generate, /api/apply,
                   /api/generate-apply, /api/generate-apply-stream (SSE),
                   /api/delete-cues, /api/color-tracks, /api/color-tracks-stream (SSE),
                   /api/backups, /api/restore, /api/backups/{filename} (DELETE),
                   /api/tracks/{id}/health, /api/health (SSE, ?playlist_id=N),
                   /api/tracks/{id}/similar, /api/tracks/{id}/classification,
                   /api/transitions/score, /api/setbuilder
    schemas.py   — Pydantic models for all request/response types
    deps.py      — lifespan DB connection + get_db dependency

docs/
  index.html     — entire web app (CSS + JS inline, no dependencies except CDN)
                   Local mode: detects /api/status on load, hides XML drop zone, loads
                   tracks from server, Apply button writes directly to Rekordbox DB.
                   _explainCue(cue): returns {confidence, reasons[]} for cue badge ℹ panel.
                   Cue reason panel: click ℹ on any cue badge to see placement explanation.

tests/
  conftest.py                — autouse fixture clears energy._cache + classify._class_cache before each test
  test_models.py             — 48 tests
  test_analyzer.py           — 36 tests (mocked pyrekordbox objects)
  test_generator.py          — 60 tests (smart slot order: A=mix-in, B+=importance; confidence scores)
  test_writer.py             — 39 tests
  test_db_writer.py          — 42 tests (BPM→color mapping, color_tracks_by_bpm, skip_colored)
  test_quality.py            — 47 tests (health scores, fix tiers, duplicate detection, SSE generator)
  test_energy.py             — 21 tests (PWAV waveform reading, caching, fallback)
  test_score.py              — 16 tests (mixability formula, components, phrase fallback)
  test_classify.py           — 29 tests (category scoring, trapezoidal membership, cache)
  test_similar.py            — 24 tests (cosine similarity, BPM gate, index build)
  test_transitions.py        — 45 tests (BPM/key/energy compatibility, Camelot wheel)
  test_setbuilder.py         — 26 tests (beam search, category order, energy penalty, deduplication)
  test_serve_routes.py       — 121 tests (FastAPI TestClient, mocked DB; covers all endpoints)
  web/
    xml-processing.test.js  — 65 Vitest tests for parseRekordboxXml, generateCues, pickCueColor
    ui-logic.test.js        — 155 Vitest tests: filteredTracks, backup multi-select, SSE apply,
                               sort labels, memory cue, colorTracksByBpm, HTTP error handling,
                               _explainCue (phrase/bar/heuristic/memory/manual modes)

package.json      — dev tooling only (vitest + jsdom); the deployed app has no build step
vitest.config.js  — jsdom environment for web tests
```

## Development commands

```bash
pip install -e ".[dev]"              # install with test deps (includes fastapi, uvicorn, psutil, httpx)
pytest                               # run all 554 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 155 Vitest tests for the web app

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
- **Scroll header**: `<div id="scroll-header">` is `position:fixed` with `transform:translateY(-110%)` and slides in via CSS transition when `#settings-section` scrolls out of the viewport. `#tracks-sticky` wraps the filter/sort/legend bar with `position:sticky`. `initScrollHeader()` IIFE handles the scroll listener, two-way sync between scroll-header controls and their main-page counterparts (playlist select, search, sort buttons, mode buttons), and shifts `#tracks-sticky` top down when the scroll header is visible.
- **Multi-select backup delete**: `DELETE /api/backups/{filename}` removes one backup; path traversal is blocked (only bare filenames accepted). The UI uses `_populateChecklist()`, `_updateSelectionCount()`, and `_checkedBackups()` helpers; `deleteCheckedBackups()` calls DELETE once per selected filename and shows a consolidated toast.
- **Smart slot ordering**: `_apply_smart_slot_order()` in `generator.py` assigns slot A to the first non-Intro phrase chronologically (the DJ mix-in point). Slots B+ are ordered by `_SMART_PRIORITY` (CHORUS=0, UP=1, OUTRO=2, VERSE=3, DOWN=4, BRIDGE=5, INTRO=6). Only applied in phrase mode.
- **Cue Quality Checker**: `autocue/analysis/quality.py`. Score: -30 NO_CUES, -10 NO_PHRASE, -10 NO_BEATGRID, -5 DUPLICATE_CUE, -5 UNNAMED_CUES. NO_AUDIO_FILE forces score=0 and skips all other checks. NO_MEMORY_CUE is info-only (zero score impact). Duplicate detection compares `InFrame` values directly (threshold < 2 frames ≈ <13ms). FolderPath on DjmdContent stores the complete audio file path (not just folder).
- **`/api/health` SSE**: Streams one JSON event per track (TrackHealthReport), then `{"done":true,"summary":{...}}`. Accepts `?playlist_id=N` for incremental rescans. Per-track exceptions yield `{"score":0,"fix_tier":"none","issues":[{"code":"INTERNAL_ERROR",...}]}` — one bad row never aborts the scan.
- **Analysis module caches**: `energy._cache` (dict, keyed by content.ID) and `classify._class_cache` (dict, keyed by content.ID) are module-level. The `conftest.py` autouse fixture clears both before every test to prevent contamination. `score.py` has NO cache — `get_mixability` is always computed fresh.
- **similar._INDEX / _INDEX_BUILT**: The similarity index is module-level in `similar.py`. To check from `setbuilder.py` whether the index is built, import the module (`from . import similar as _similar_mod`) and check `_similar_mod._INDEX_BUILT` — do NOT import `_INDEX_BUILT` directly (that creates a copy that never updates).
- **PWAV energy curve**: `get_energy_curve()` returns one float per PWAV column (~150ms each). To map a `position_ms` to an index: `idx = min(int(position_ms // 150), len(curve) - 1)`. Returns `[]` when PWAV tag is absent or ANLZ read fails.
- **Transition scoring**: `score_transition(a, b, db)` returns `{overall, bpm, key, energy, end_energy_a, start_energy_b}`. The `end_energy_a` and `start_energy_b` fields are scalars (or None) — pass them directly to energy-penalty functions; do not re-read ANLZ curves.
- **Set Builder beam search**: `build_set()` in `setbuilder.py`. Uses `find_similar(track_id, db, n=20)` per step (O(n×K) not O(n²)). Deduplication via `visited: set[int]` per beam. BPM monotonic step gated by `bpm_step_max` (default 8%). `get_classification()` is pre-warmed during `_index_track()` so beam search lookup is O(1).
- **Cue explanation panel**: `_explainCue(cue)` in `index.html` returns `{confidence: str, reasons: str[]}`. Cue objects need `{slot, confidence, phraseMode, phraseBars, label, name}`. If `confidence == null && phraseMode == null` → "Manually placed cue". Explanation panel uses click-toggle (not hover) for mobile compatibility.

## Testing approach

Tests mock pyrekordbox objects rather than hitting a real database. When adding tests for `analyzer.py`, mock `db.read_anlz_file()` and the returned `AnlzFile` objects with a `.get_tag()` method returning objects that have the expected `.content` structure (`entries`, `mood`, `beat`, `kind`, `time` fields).

JS tests in `tests/web/` copy functions verbatim from `docs/index.html` and run them in jsdom via Vitest. If you change `parseRekordboxXml`, `generateCues`, `computeCues`, `colorTracksByBpm`, `applyToRekordbox`, or `_explainCue` in `index.html`, update the corresponding copies in the test files. The `ui-logic.test.js` file tests sort label lookup, memory cue prepend logic, fetch HTTP error handling, and `_explainCue` all explanation modes (phrase/bar/heuristic/memory/manual).

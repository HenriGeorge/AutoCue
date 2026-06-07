# AutoCue — Claude Code Guide

## Browser testing

When doing browser testing or UI verification with Chrome DevTools tools:
- **Always send screenshots to the user** using `SendUserFile` after every meaningful state change (before action, after action, result visible). Do not just describe what you see.
- **Screenshots are pre-approved** — take them freely without asking permission. The `mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_screenshot` tool is always allowed.
- Save screenshots to `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/` (the allowed temp dir).

## What this project is

AutoCue places hot cues on Rekordbox 7 tracks automatically and analyses a DJ
library, using three surfaces:

1. **Python CLI** (`autocue/`) — reads Rekordbox's database and ANLZ files directly.
   Fallback strategy: phrase → bar → heuristic. Outputs a Rekordbox XML for import.

2. **Local server** (`autocue serve`) — FastAPI server at `localhost:7432`. Serves the
   web UI and exposes a REST API that reads/writes the Rekordbox database directly.
   No XML export/import needed. **All intelligence features** (energy, mixability,
   classification, similar tracks, transition scoring, set builder, library health,
   auto-tagging, comment enrichment, Discogs) are only available in this mode.

3. **Web app** (`docs/index.html`) — browser-based, single HTML file, no build step.
   User uploads a Rekordbox XML, gets a new XML with cues at configurable bar intervals
   OR phrase positions. Hosted on GitHub Pages. Also served by `autocue serve` in local mode,
   where the same page unlocks the full server-backed feature set.

## Architecture

```
autocue/
  models.py      — PhraseLabel enum + CuePoint dataclass
  analyzer.py    — reads ANLZ .EXT (PSSI phrases) + .DAT (PQTZ beat grid)
  generator.py   — per-track strategy: phrase → bar → heuristic; GenerationPrefs dataclass
  writer.py      — writes CuePoints to Rekordbox XML via pyrekordbox.rbxml
  db_writer.py   — writes CuePoints directly to DjmdCue; backup + Rekordbox-running check
  cli.py         — argparse CLI; --track / --track-id / --library / --playlist; `autocue serve` subcommand
  __main__.py    — entry point
  download.py    — yt-dlp wrapper (OPTIONAL [download] extra; needs ffmpeg on PATH).
                   ytdlp_available() / ffmpeg_available() probes, default_download_dir()
                   (AUTOCUE_DOWNLOAD_DIR env → ~/Music/AutoCue), search_youtube(),
                   download_audio(url_or_query, dest_dir, audio_format, progress_cb).
                   All yt-dlp imports are lazy so the core install runs without it.
  analysis/
    quality.py      — Cue Quality Checker: check_track_health(), check_library_health()
                      Pure DB reads (DjmdCue + DjmdContent). No ANLZ parsing.
                      Scores tracks 0–100; yields fix_tier: phrase/bar/heuristic/none.
    energy.py       — PWAV waveform reader: get_energy_curve(content, db, n_points=50) → a
                      normalized 0–1 curve resampled to n_points (raw PWAV / 31.0, 3-point
                      smoothed, average-downsampled). Returns None when PWAV/.DAT is unavailable.
                      classify_energy_profile(curve) → "flat"/"build"/"wave"/"drop-then-flat".
    score.py        — Mixability score (0–100): intro/outro bars + energy variance + vocal proxy.
                      get_mixability() is CACHED in score._mixability_cache (keyed by content.ID).
    classify.py     — Track classification: get_classification() → {primary, scores, bpm, energy_mean}
                      Five categories: warmup/build/peak/after_hours/closing. Cached in _class_cache.
    similar.py      — Cosine similarity on 6-dim feature vector (key, energy, variance, vocal proxy, BPM).
                      BPM gate ±8. Data-quality cap: score ≤ 0.65 when neither track has ANLZ energy data.
                      Builds in-process index on first call; pre-warms _class_cache via _index_track().
                      Thread-safe module index: _INDEX / _INDEX_BUILT / _INDEX_LOCK; clear_index() resets it.
    transitions.py  — score_transition(a, b, db) → {overall, bpm, key, energy, bpm_a, bpm_b, key_a, key_b,
                      end_energy_a, start_energy_b, explanation}. transition_advice(ts) → human-readable
                      DJ mixing tip ("Nudge pitch +5 BPM — blend over 8–16 bars; compatible key…").
    setbuilder.py   — Beam search set builder (width=5). Uses find_similar per step (O(n×K) not O(n²)).
                      build_set(db, start_bpm, end_bpm, duration_minutes, energy_mode, bpm_step_max,
                                seed_track_id, anchor_track_ids) → list[dict]
                      build_alternatives(...) → replacement candidates for a slot given its neighbours.
    auto_tag.py     — Writes DJ analysis results as Rekordbox My Tags (DjmdMyTag / DjmdSongMyTag).
                      apply_tags() (multi-type) + apply_classification_tags() (category only, score≥0.70).
                      Detectors: category, vocal, energy_level, energy_profile, intro_outro,
                      decade, bpm_tier, play_history. ensure_category_tags() / ensure_tag_by_name()
                      create-or-reuse tags idempotently. undo_tag_run() reverses a run via undo_data.
    comment.py      — Track comment enrichment → DjmdContent.Commnt. build_comment_string(),
                      enrich_comment(), enrich_comments_batch(). MIK-compatible format; appends a
                      re-writable "/* AutoCue: ... */" sentinel block to preserve user text.
    discogs.py      — Discogs API client: search_styles(artist, title, token) → genre/style list.
                      search_artist_releases(artist, token, year_from) → recent releases.
                      In-process token-bucket rate limiter (60 req/min) + per-process caches
                      (_cache for styles, _releases_cache for releases).
    discovery.py    — New-release suggestions: surfaces recent albums from the library's
                      most-played artists via Discogs, skipping albums already owned.
                      library_artists() / library_album_set() / iter_new_releases() (generator
                      for SSE) / suggest_new_releases(). Reuses discogs.py's rate limiter.
  serve/
    app.py       — FastAPI app factory + uvicorn launcher; CORS whitelist (localhost only)
    deps.py      — lifespan DB connection + get_db dependency; pre-warms the similarity index
                   in a background daemon thread on startup (_prewarm_index)
    routes.py    — see endpoint list below
    schemas.py   — Pydantic models for all request/response types

docs/
  index.html     — entire web app (CSS + JS inline, no dependencies except CDN)
                   Local mode: detects /api/status on load, hides XML drop zone, loads
                   tracks from server, Apply button writes directly to Rekordbox DB.
                   Server-only panels: Set Builder, Transition scoring, Similar tracks,
                   Library Health scan, Auto-Tag / classification, Comment enrichment,
                   Discogs style tagging, Playlist suggestions + create-playlist, Cue Tools.
                   Three tabs (Cues / Library / Discover) via switchTab() + TAB_CONTENTS map.
                   Discover tab: new-release discovery cards (_renderSuggestion) + YouTube download.
                   _explainCue(cue): returns {confidence, reasons[]} for cue badge ℹ panel.
                   filteredTracks() also applies rating / plays / last-played / My-Tag filters.
  FEATURES.md    — long-form end-user feature documentation (kept in sync with the app)
  guides/        — static DJ learning guides (rekordbox/mixing/hardware HTML)

tests/
  conftest.py                — autouse fixture clears energy._cache, classify._class_cache,
                               score._mixability_cache, and calls similar.clear_index() before each test
  test_models.py             — 48 tests
  test_analyzer.py           — 39 tests (mocked pyrekordbox objects)
  test_generator.py          — 79 tests (smart slot order: A=mix-in, B+=importance; confidence scores)
  test_writer.py             — 39 tests
  test_db_writer.py          — 49 tests (BPM→color mapping, color_tracks_by_bpm, skip_colored)
  test_quality.py            — 47 tests (health scores, fix tiers, duplicate detection, SSE generator)
  test_energy.py             — 36 tests (PWAV resample to n_points, smoothing, caching, profile classifier)
  test_score.py              — 19 tests (mixability formula, components, phrase fallback, cache)
  test_classify.py           — 33 tests (category scoring, trapezoidal membership, cache)
  test_similar.py            — 28 tests (cosine similarity, BPM gate, index build/clear, data-quality cap)
  test_transitions.py        — 48 tests (BPM/key/energy compatibility, Camelot wheel)
  test_setbuilder.py         — 27 tests (beam search, category order, energy penalty, deduplication, alternatives)
  test_auto_tag.py           — 36 tests (My Tag create/reuse, all detectors, undo, dry-run)
  test_properties.py         — 65 tests (Hypothesis property/invariant tests for classify + transitions math)
  test_discovery.py          — 17 tests (library artist ranking, owned-album filter, dedupe, SSE generator)
  test_download.py           — 15 tests (yt-dlp/ffmpeg probes, query building, search, download paths — yt-dlp mocked)
  test_serve_routes.py       — 194 tests (FastAPI TestClient, mocked DB; covers all endpoints incl. discover/download)
  web/
    xml-processing.test.js  — 65 Vitest tests for parseRekordboxXml, generateCues, pickCueColor
    ui-logic.test.js        — 126 Vitest tests: filteredTracks (search/phrase/rating/plays/last-played/My-Tag),
                               backup multi-select, SSE apply, sort labels, memory cue, colorTracksByBpm,
                               add_fill_cues, ensureLocalAudio, HTTP error handling, _explainCue (all modes),
                               _esc + _renderSuggestion (Discover cards), AppState pub/sub bus,
                               r.json().catch safe error handling

package.json      — dev tooling only (vitest + jsdom); the deployed app has no build step
vitest.config.js  — jsdom environment for web tests
pyproject.toml    — hatchling build; runtime + [dev] extras (pytest, httpx, hypothesis) + [download] extra (yt-dlp)
.github/workflows/ci.yml — CI: pytest (Python 3.10–3.12) + Vitest (Node 20) on push / PR
```

### REST API endpoints (`serve/routes.py`)

```
GET  /api/status                          GET  /api/playlists
GET  /api/tracks (?playlist_id=N)         GET  /api/tracks/{id}/artwork
GET  /api/tracks/{id}/audio               GET  /api/tags
POST /api/generate                        POST /api/apply
POST /api/generate-apply                  POST /api/generate-apply-stream (SSE)
POST /api/delete-cues                     POST /api/color-tracks
POST /api/color-tracks-stream (SSE)       GET  /api/backups
POST /api/restore                         DELETE /api/backups/{filename}
GET  /api/tracks/{id}/health              GET  /api/health (SSE, ?playlist_id=N)
POST /api/cue-tools-stream (SSE)          GET  /api/tracks/{id}/energy
GET  /api/tracks/{id}/mixability          GET  /api/tracks/{id}/classification
GET  /api/classify (SSE, ?playlist_id=N)  GET  /api/tracks/{id}/similar
POST /api/transitions/score               POST /api/setbuilder
GET  /api/setbuilder/alternatives         POST /api/playlists/suggest
POST /api/playlists (create)              POST /api/auto-tag
POST /api/auto-tag/undo                   GET  /api/config
POST /api/auto-tag/discogs/test           POST /api/auto-tag/discogs (SSE)
POST /api/enrich-comments                 POST /api/enrich-comments/preview
POST /api/enrich-comments/stream (SSE)    GET  /api/discover (SSE)
GET  /api/download/config                 POST /api/download (SSE)
POST /api/download/album (SSE)
```

## Development commands

```bash
pip install -e ".[dev]"              # install with test deps (fastapi, uvicorn, psutil, httpx, hypothesis)
pip install -e ".[download]"         # OPTIONAL: YouTube download support (yt-dlp; also needs ffmpeg on PATH)
pytest                               # run all 819 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 191 Vitest tests for the web app (65 + 126)

autocue serve --no-browser           # start local server on localhost:7432
autocue --library --dry-run          # preview CLI output without writing
autocue --track "Song Title"
autocue --library --overwrite        # re-generate for all tracks
autocue --library --uncued-only      # only tracks with zero existing hot cues
autocue --library --playlist "NAME"  # restrict --library to a named playlist
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
- **DjmdContent.Commnt**: The track comment column is spelled `Commnt` (not `Comment`). Use `getattr(content, "Commnt", "")`. Genre is an association proxy: `content.GenreName` (not `content.Genre` which is the ORM relationship object). `DjmdCue.Comment` is correctly spelled — only `DjmdContent` uses the abbreviated name.
- **Comment enrichment format**: `enrich_comment()` in `analysis/comment.py` writes `"8A - Energy 7 | Peak | 4 bar intro"` (MIK-compatible prefix). Appends `/* AutoCue: ... */` sentinel to existing comments; sentinel block is replaced on re-run (idempotent). `enrich_comments_batch()` makes a DB backup before writing.
- **DjmdKey.Seq**: use `Seq` (Integer) for server-side key sort, not `ScaleName` (lexicographic "10A" < "1A" is wrong). Client-side uses `camelotSortKey()` which converts "8A" → numeric order.
- **Fetch error handling in JS**: always check `r.ok` before reading typed properties from `r.json()`. A 409 response returns `{detail: "..."}` — reading `resp.applied` or `resp.colored` on an error body yields `undefined` and produces misleading toast messages.
- **DjmdCue ID generation**: `DjmdCue.ID` is VARCHAR(255) with no auto-generate default — must call `db.generate_unused_id(DjmdCue)` explicitly when inserting. Also set `UUID=str(uuid4())`, `ContentUUID` from the content row, `InFrame=round(position_ms * 150 / 1000)`, `OutMsec=-1`, and 0 for all other integer fields.
- **Apply performance**: The UI uses `/api/generate-apply-stream` (SSE) which streams progress events `{"processed":N,"total":M,...}` as each track is processed, then a final `{"done":true,...}`. The JS reads via `fetch` + `ReadableStream` (not `EventSource`, since the request is POST). No `AbortSignal.timeout` — the stream has no time limit.
- **Playlist filter**: `/api/tracks` uses `?playlist_id=<int>` (integer FK). The old `?playlist=<name>` param was removed. The frontend dropdown passes the numeric ID.
- **`/api/tracks` SQL pattern**: history (`DjmdSongHistory`) and my-tags (`DjmdSongMyTag`) are loaded with `db.query(...).all()` then filtered in Python against `row_ids` — **not** with a SQLAlchemy `IN(row_ids)` clause. This is intentional: for full-library page loads (~3k rows) the `IN` filter is slower than fetch-all-then-filter against pyrekordbox's SQLCipher. Do not "optimize" this back to `.filter(...ContentID.in_(row_ids))`.
- **`/api/tags` returns only used tags**: the endpoint filters `DjmdMyTag` against `distinct(DjmdSongMyTag.MyTagID)` so unused tags (created and never applied) do not appear in the UI's tag filter list. Tests must mock both queries (see `_make_tags_db()` in `test_serve_routes.py`).
- **GZip middleware**: `serve/app.py` installs `GZipMiddleware(minimum_size=1000)` ahead of the CORS middleware. SSE streams pass through unchanged (Starlette skips gzip when the response uses `text/event-stream` plus `X-Accel-Buffering: no`).
- **`/api/playlists/suggest` seeds**: `PlaylistSuggestRequest.seed_track_ids` (list[int], default `[]`) are pre-included tracks placed at the front of the result in user-supplied order. Seeds **bypass `exclude_ids`** (so you can pre-pin a track the caller already excluded). The remaining `req.count − len(seeds)` slots are filled by weighted random draw from the top pool.
- **`/api/enrich-comments/stream` per-track commit**: the SSE stream commits each track individually inside the per-track try block (rollback on failure → increments `errors` and decrements `enriched`). The old batched commit at the end is gone — a single failing track no longer rolls back the whole batch. Backup is still made once up front before any writes.
- **Restore backup**: `/api/restore` accepts `{filename}` (not a full path — validated to be within BACKUP_DIR). The endpoint closes `db._engine.dispose()` before copying the file, then reopens via `Rekordbox6Database` and updates `app.state.db`. WAL/SHM sidecars are handled on both backup and restore. **After any restore, clear the stale analysis state** — the restore path calls `similar.clear_index()` (and the analysis caches) so the rebuilt DB does not match old feature vectors.
- **has_phrase**: Populated via `db.get_anlz_path(content, "EXT")` — a fast file-existence check with no parsing. `True` if the .EXT ANLZ file exists on disk.
- **filteredTracks()**: Client-side function that applies search, phrase-only, rating, plays, last-played, and My-Tag filters to `parsedTracks`. All write operations (apply, delete, color, enrich, tag) use `filteredTracks()` — not `parsedTracks` directly. `parsedTracks` is never mutated by filters.
- **pendingCues**: JS map of `String(trackId) → [{slot,posSec,label,...}]` populated by the "Preview cues" button (calls `/api/generate`). Cleared after Apply completes. Rendered as a secondary timeline bar in each track card.
- **AppState pub/sub bus**: Module-level IIFE `AppState = { subscribe(key, fn), signal(key) }`. Subscribers are coalesced — multiple `signal()` calls in the same synchronous tick produce one flush (via `Promise.resolve().then()`). Keys: `'filters'` (re-renders on filter/sort change), `'settings'` (re-renders + updates overwrite warning), `'tracks'` (re-renders after library load). `subscribe()` returns an unsubscribe function. Subscriber exceptions are caught and logged — one bad subscriber never blocks others.
- **`_cardMap` smart diffing**: `Map<trackId, HTMLElement>` caches flat-mode track cards. `_computeSettingsFingerprint()` covers barsInterval, startBar, maxCues, skipExisting, mcMode, analysisMode, phrase total cue count, pendingCues key count, and healthData key count — fingerprint change clears the map and forces full rebuild. FLIP reorder animation via `Element.animate()` (batch reads before writes to avoid layout thrashing). Enter/exit animations use `.card-enter` / `.card-exit` classes. Large-exit threshold: if >30 cards exit in one render, animations are skipped entirely (instant snap) to prevent DOM bloat on search filter changes. `_sparkObserver`, `_mixObserver`, `_enterObserver` are module-level and `.disconnect()`-ed before reuse to prevent IntersectionObserver leaks.
- **RAF playhead**: `_playRafId` / `_startPlayRaf()` / `_stopPlayRaf()` replace `timeupdate`-driven `updateTimeline()`. RAF loop runs at 60fps while audio plays; started inside `audioPlayer.play().then()` (never before the browser confirms playback). `.timeline-playhead` has **no CSS transition** — with RAF at 60fps, a transition would permanently lag behind the playhead.
- **`_energyCache`**: Module-level `{}` mapping trackId → energy curve array (fetched from `/api/tracks/{id}/energy`). Cleared on library reload and XML upload. Used by `_drawMiniWaveform()` to render the canvas sparkline in the mini player.
- **Mini waveform canvas**: `<canvas id="mini-waveform">` inside `#mini-waveform-wrap`. HiDPI via `devicePixelRatio` scaling (`canvas.width = cssW * dpr`). Overlaid by invisible `<input type="range" id="mini-scrubber">` for seek interaction. The wrap uses `clip-path: inset(0 round 4px)` — **not** `overflow:hidden` — to preserve range input pointer events while still clipping the visual corners.
- **Sticky filter bar**: `#tracks-sticky` uses `position:sticky; top:0` with negative horizontal margins (`margin-left:-24px; margin-right:-24px; padding:0 24px`) to bleed edge-to-edge within `main`'s 24px side padding. A `border-bottom` is added via the `.shadowed` class (toggled by `IntersectionObserver` on a sentinel div) — no `box-shadow` (bleeds sideways). Default sort is `album` ascending. There is no slide-in scroll header (`#scroll-header` was removed).
- **Multi-select backup delete**: `DELETE /api/backups/{filename}` removes one backup; path traversal is blocked (only bare filenames accepted). The UI uses `_populateChecklist()`, `_updateSelectionCount()`, and `_checkedBackups()` helpers; `deleteCheckedBackups()` calls DELETE once per selected filename and shows a consolidated toast.
- **Smart slot ordering**: `_apply_smart_slot_order()` in `generator.py` assigns slot A to the first non-Intro phrase chronologically (the DJ mix-in point). Slot B is reserved for the first OUTRO phrase (CDJ prep feature — DJs instinctively reach for B at mix-out). Slots C+ are ordered by `_SMART_PRIORITY` (CHORUS=0, UP=1, OUTRO=2, VERSE=3, DOWN=4, BRIDGE=5, INTRO=6). Only applied in phrase mode.
- **Cue Quality Checker**: `autocue/analysis/quality.py`. Score: -30 NO_CUES, -10 NO_PHRASE, -10 NO_BEATGRID, -5 DUPLICATE_CUE, -5 UNNAMED_CUES. NO_AUDIO_FILE forces score=0 and skips all other checks. NO_MEMORY_CUE is info-only (zero score impact). Duplicate detection compares `InFrame` values directly (threshold < 2 frames ≈ <13ms). FolderPath on DjmdContent stores the complete audio file path (not just folder).
- **`/api/health` SSE**: Streams one JSON event per track (TrackHealthReport), then `{"done":true,"summary":{...}}`. Accepts `?playlist_id=N` for incremental rescans. Per-track exceptions yield `{"score":0,"fix_tier":"none","issues":[{"code":"INTERNAL_ERROR",...}]}` — one bad row never aborts the scan.
- **Analysis module caches** (all cleared by the `conftest.py` autouse fixture before every test):
  - `energy._cache` — keyed by `(content.ID, n_points)` (NOT just the track ID — the curve length is part of the key).
  - `classify._class_cache` — keyed by `content.ID`.
  - `score._mixability_cache` — keyed by `content.ID`. `get_mixability` IS cached (this changed from earlier — it is no longer always recomputed).
  - `similar._INDEX` / `_INDEX_BUILT` — the in-process similarity index; reset via `similar.clear_index()`.
- **similar._INDEX / _INDEX_BUILT**: The similarity index is module-level in `similar.py`, guarded by `_INDEX_LOCK`. To check from another module (e.g. `setbuilder.py`) whether the index is built, import the module (`from . import similar as _similar_mod`) and check `_similar_mod._INDEX_BUILT` — do NOT import `_INDEX_BUILT` directly (that creates a copy that never updates). The server pre-warms the index in a background thread on startup (`deps._prewarm_index`).
- **PWAV energy curve**: `get_energy_curve(content, db, n_points=50)` returns a fixed-length normalized 0–1 curve (default 50 points), resampled by averaging from the raw PWAV amplitudes in the `.DAT` ANLZ file. It is NOT one-float-per-150ms-column. To map a `position_ms` to an index, scale against the track's duration and `len(curve)` — do not assume 150ms per sample. Returns `None` (not `[]`) when PWAV is absent or the ANLZ read fails.
- **Energy profile**: `classify_energy_profile(curve)` → one of `"flat"` (low variance), `"build"` (second-half mean rises), `"wave"` (≥2 local maxima), `"drop-then-flat"` (early peak then lower — the fallback). Used by the energy_profile auto-tag detector and the `/api/tracks/{id}/energy` response.
- **Transition scoring**: `score_transition(a, b, db)` returns `{overall, bpm, key, energy, bpm_a, bpm_b, key_a, key_b, end_energy_a, start_energy_b, explanation}`. The `end_energy_a` and `start_energy_b` fields are scalars (or None) — pass them directly to energy-penalty functions; do not re-read ANLZ curves. The `bpm_a/bpm_b/key_a/key_b` fields exist so `transition_advice(ts)` can render a mixing tip without re-reading the source content. **Missing energy data**: `_energy_score(None, None) = 50.0` (neutral, NOT 100); one side missing caps score at 75. This deliberately defeats the old "free 100 energy score" that made every same-key same-BPM no-ANLZ transition score 100 overall.
- **Set Builder beam search**: `build_set()` in `setbuilder.py`. Uses `find_similar(track_id, db, n=20)` per step — **doubled to n=40 when `end_bpm ≠ start_bpm`** to surface higher-BPM candidates (still O(n×K), not O(n²)). Deduplication per beam covers three axes: `visited: set[int]` (track ID), `visited_titles: set[str]` (`"title|||artist"` lowercase — blocks duplicate imports of the same song), `visited_artists: dict[str, int]` (capped at 2 appearances per artist by default). BPM monotonic step gated by `bpm_step_max` (default 8%); the BPM gate is **asymmetric** — at least ±12 BPM toward `end_bpm` when building/dropping. **Setbuilder-specific transition reweighting**: when `end_bpm ≠ start_bpm`, `overall = 0.25×bpm + 0.40×key + 0.35×energy` (instead of the standard 0.40/0.35/0.25) so BPM progression is not structurally punished. A **BPM-progress bonus** of up to +15 points rewards candidates that move toward `end_bpm`. `_find_seed()` is two-pass: first pass requires `bpm ≥ start_bpm × 0.97`, falling back to any BPM only if no in-range track exists. `seed_track_id` overrides seed selection; `anchor_track_ids` are must-include tracks merged into the beam's result at BPM-sorted positions via `_merge_anchors()`. Each `SetTrack` carries a `mix_advice: str` produced by `transition_advice()`. `get_classification()` is pre-warmed during `_index_track()` so beam search lookup is O(1). `build_alternatives()` / `/api/setbuilder/alternatives` returns swap candidates for one slot scored on fit to both neighbours; candidates with `genre` mismatching the replaced track's genre (and the neighbours' genres) get a **−20 point penalty** and `genre_match=False`. Candidates with no `GenreName` get `genre_match=None` (no penalty).
- **Auto-Tag (My Tags)**: `autocue/analysis/auto_tag.py` writes results to `DjmdMyTag` + `DjmdSongMyTag`. Tags are created with an explicit `db.generate_unused_id`, `UUID`, `Name`, `Attribute` (color hint 1–8, mirroring DjmdColor SortKey), and `Seq`. `ensure_category_tags()` / `ensure_tag_by_name()` are idempotent (reuse existing tags by name; AutoCue's own names live in `AUTOCUE_TAG_NAMES`). `apply_classification_tags()` only writes the top category when its score ≥ `MIN_SCORE` (0.70) and skips tracks with no ANLZ energy data. `apply_tags()` is the multi-type entrypoint (category, vocal, energy_level, energy_profile, intro_outro, decade, bpm_tier, play_history). Every run returns `undo_data` consumed by `undo_tag_run()` / `/api/auto-tag/undo`.
- **Comment enrichment**: `autocue/analysis/comment.py` writes to `DjmdContent.Comment`. Format is MIK-compatible: `8A - Energy 7 | Peak | 4 bar intro`. To preserve user-authored text, AutoCue appends a re-writable sentinel block `/* AutoCue: ... */` (the same convention Rekordbox uses for "Add My Tag to Comments"); on re-run, only the sentinel block is replaced unless `overwrite=True`. Energy is mapped to a 1–10 MIK scale. `enrich_comments_batch()` makes a DB backup before writing (unless `dry_run`). Functions do not commit — the route commits.
- **Discogs**: `autocue/analysis/discogs.py` `search_styles(artist, title, token)` returns Discogs Style strings. A per-process token-bucket limits to 60 req/min; results are cached in `discogs._cache` keyed by lowercased `(artist, title)`. The personal access token comes from the request body or `DISCOGS_TOKEN`. `/api/config` reads `DISCOGS_TOKEN` from a project-root `.env` (then the environment) so the UI can pre-fill it; `/api/auto-tag/discogs/test` validates the token against the Discogs identity endpoint. `search_artist_releases(artist, token, year_from)` (cached in `_releases_cache`) shares the same token bucket. `DiscogsTagRequest.skip_existing` (default `False`) tells `/api/auto-tag/discogs` to skip tracks that already carry **non-AutoCue My Tags** (the assumption being those are pre-existing Discogs styles); detection uses `auto_tag.ALL_AUTOCUE_TAG_NAMES` as the allowlist.
- **Discovery (new releases)**: `autocue/analysis/discovery.py` reuses the Discogs client to suggest recent albums from the library's artists. `library_artists(db, top_n)` ranks artists by play-frequency (a proxy for what the DJ cares about) so a big library does not blow past the rate limit — only the top N artists are queried. `library_album_set(db)` builds the owned-album set (normalized lowercase) used to filter suggestions. `iter_new_releases(...)` is a **generator** yielding `(processed, total, suggestion|None)` for SSE; `None` ticks report progress for artists with no new releases. `since_year` defaults to *last year*. `/api/discover` requires a Discogs token (query param → `_resolve_discogs_token()` env/.env) and streams one `DiscoverItem` per suggestion, then `{done:true}`. `DiscoverItem.formats` carries Discogs format tags (e.g. `["Vinyl", "LP", "Album"]`) for the UI's format chips.
- **Download (yt-dlp)**: `autocue/download.py` is an **optional** feature gated behind the `[download]` extra (`pip install -e ".[download]"`) and an `ffmpeg` binary on PATH. All `yt_dlp` imports are **lazy** so the core CLI/server import without it. `ytdlp_available()` / `ffmpeg_available()` probe at runtime; `/api/download/config` reports both plus `default_download_dir()` (env `AUTOCUE_DOWNLOAD_DIR` → `~/Music/AutoCue`) and `music_folder` — the detected Rekordbox music root, computed by `_detect_music_folder(db)` via `os.path.commonpath()` over up to 30 absolute `DjmdContent.FolderPath` values (returns `None` on failure). `download_audio(url_or_query, ...)` passes real URLs through and wraps bare terms as `ytsearch1:`; it extracts audio via the `FFmpegExtractAudio` postprocessor and returns the final file path. `/api/download` (single, runs the blocking download in a worker thread and streams progress via a `queue.Queue`) and `/api/download/album` (sequential) both **return 503** when yt-dlp/ffmpeg are missing and stream SSE `DownloadEvent`s otherwise. **Legal note**: downloading copyrighted audio may violate YouTube's ToS / copyright — the UI shows a disclaimer; lawful use is the user's responsibility.
- **SSE consumption in JS**: `_consumeSSE(response, onEvent)` in `index.html` is the shared `fetch`+`ReadableStream` reader used by Discover and Download (POST SSE can't use `EventSource`). New SSE-driven UI should reuse it rather than re-inlining the reader loop. `_esc()` HTML-escapes all server-supplied strings rendered into Discover cards — never interpolate Discogs/YouTube text without it.

## Testing approach

Tests mock pyrekordbox objects rather than hitting a real database. When adding tests for `analyzer.py`, mock `db.read_anlz_file()` and the returned `AnlzFile` objects with a `.get_tag()` method returning objects that have the expected `.content` structure (`entries`, `mood`, `beat`, `kind`, `time` fields).

`test_properties.py` uses **Hypothesis** (in the `[dev]` extra) for generative property/invariant tests over the pure math in `classify.py` and `transitions.py`. If Hypothesis is missing, that one module fails at collection — run `pip install -e ".[dev]"` so `pytest` collects cleanly.

JS tests in `tests/web/` copy functions verbatim from `docs/index.html` and run them in jsdom via Vitest. If you change `parseRekordboxXml`, `generateCues`, `computeCues`, `colorTracksByBpm`, `applyToRekordbox`, `filteredTracks`, `ensureLocalAudio`, `_explainCue`, `_esc`, or `_renderSuggestion` in `index.html`, update the corresponding copies in the test files. The `ui-logic.test.js` file tests sort label lookup, memory cue prepend logic, fetch HTTP error handling, the full `filteredTracks` filter matrix, backup multi-select, `_explainCue` across all explanation modes (phrase/bar/heuristic/memory/manual), the Discover card renderer (`_renderSuggestion` + `_esc` escaping), and the `AppState` pub/sub bus (coalescing, unsubscribe, multi-key). The `AppState` test helper (`makeAppState()`) is a standalone copy in `ui-logic.test.js` — update it if the production `AppState` logic changes.

`tests/test_download.py` mocks the `yt_dlp` module via `sys.modules` patching (yt-dlp is not a test dependency) and stubs `shutil.which` for ffmpeg detection — so the download tests run everywhere without the optional extra installed. `tests/test_discovery.py` patches `discovery.search_artist_releases` rather than hitting Discogs.

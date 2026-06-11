# AutoCue — Claude Code Guide

## Browser testing

When doing browser testing or UI verification with Chrome DevTools tools:
- **Always send screenshots to the user** using `SendUserFile` after every meaningful state change (before action, after action, result visible). Do not just describe what you see.
- **Screenshots are pre-approved** — take them freely without asking permission. `mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_screenshot` is always allowed.
- Save screenshots to `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/` (the allowed temp dir).

## What this project is

AutoCue places hot cues on Rekordbox 7 tracks automatically and analyses a DJ library, across three surfaces:

1. **Python CLI** (`autocue/`) — reads Rekordbox's database and ANLZ files directly. Fallback strategy: phrase → bar → heuristic. Outputs a Rekordbox XML for import.
2. **Local server** (`autocue serve`) — FastAPI at `localhost:7432`. Serves the web UI and exposes a REST API that reads/writes the Rekordbox database directly. **All intelligence features** (energy, mixability, classification, similar tracks, transitions, set builder, library health, auto-tagging, comment enrichment, Discogs, discovery, download) are only available in this mode.
3. **Web app** (`docs/index.html`) — browser-based, single HTML file, no build step. Hosted on GitHub Pages (XML in/out). Also served by `autocue serve` in local mode for the full feature set.

## Development commands

```bash
pip install -e ".[dev]"              # install with test deps (fastapi, uvicorn, psutil, httpx, hypothesis)
pip install -e ".[download]"         # OPTIONAL: YouTube download support (yt-dlp; also needs ffmpeg on PATH)
pytest                               # run all 1439 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 648 Vitest tests for the web app

AUTOCUE_POOL_SIZE=8 autocue serve    # override the shared analysis pool size (default min(8, cpu_count))
AUTOCUE_PERF=1 autocue serve         # enable /api/perf/recent + perf_span ring buffer
autocue serve --no-browser           # start local server on localhost:7432
autocue serve --reset-cache          # delete the sidecar cache before starting
autocue --library --dry-run          # preview CLI output without writing
autocue --track "Song Title"
autocue --library --overwrite        # re-generate for all tracks (default skips already-cued)
autocue --library --playlist "NAME"  # restrict --library to a named playlist
make perf                            # run perf budget enforcement (RUN_PERF=1 pytest -m perf)
```

## Must-know constraints (read every session)

- **Rekordbox must be closed** before any write (CLI or local-mode Apply). DB is SQLCipher-locked while open. Server enforces this at every write endpoint via `_rb_running(db)`.
- **Web app is a single self-contained HTML file** — no build step, no framework. All app changes go to `docs/index.html`. `package.json` exists only for dev testing.
- **pyrekordbox**: use `Rekordbox6Database` from `pyrekordbox.db6`. `add_track()` takes the file path **positionally**.
- **ANLZ parsing**: wrap `db.read_anlz_file()` / `get_tag()` in `try/except Exception` — `ConstError` / `IndexError` are common for unsupported ANLZ versions and missing tags.
- **BPM guard**: always check `float(bpm) > 0` before using BPM in calculations — Rekordbox can store `"0.0"` (truthy string, zero float).
- **JS fetch error handling**: always check `r.ok` before reading typed properties from `r.json()` — error bodies return `{detail: "..."}` and reading `resp.applied` etc. yields `undefined` and misleading toasts.
- **Library Duplicates** (`autocue/analysis/duplicates.py` + `GET /api/duplicates` SSE + `POST /api/duplicates/delete` SSE) groups tracks by case-insensitive whitespace-normalised **`(artist, title, duration_bucket)`** where `duration_bucket = round(duration/5)` (5 s buckets keep encoder-rounded re-imports together but split an album cut from an extended mix). The keeper is picked by `(existing_hot_cues, play_count, last_played, bitrate, -track_id)` — **cue-prep outranks plays** so a freshly-prepped re-import beats a heavily-played auto-cued original. The user can override the keeper per-group via the **"Keep" radio**; the delete-button label, non-keeper set, and same-path chips recompute live on flip. **`delete_tracks` cascades through all 13 ContentID-bearing tables** (verified by `tests/test_duplicates_integration.py` against a real in-memory SQLite — DjmdCue/ContentCue/ContentActiveCensor/ContentFile/DjmdActiveCensor/DjmdSongHotCueBanklist/DjmdMixerParam/DjmdSongSampler/DjmdSongHistory/DjmdSongPlaylist/DjmdSongTagList/DjmdSongMyTag/DjmdSongRelatedTracks before DjmdContent) — a schema-pinned test fails if pyrekordbox adds a new ContentID table. Safety: 409 when Rekordbox running; **per-session backup window** (deletes <30 s apart reuse one backup, so /api/restore rolls back the whole session); **per-row savepoint**; **concurrency lock** 409s a second concurrent real delete (shared-session guard, released on every SSE exit path incl. client disconnect). The SSE delete streams per-batch progress and honours client-disconnect as cancel (rows committed before cancel survive; backup still restores). UI: confirm modal with primary disabled 250 ms after open, Cancel focused, focus-trap, ESC-during-delete aborts; in-place progress bar; **inline "Undo this delete"** banner (30 s) that POSTs `/api/restore`; same-file vs distinct-file chip per copy + a modal audio-summary line. Frontend invalidation via `_onTracksDeleted(ids)` prunes parsedTracks/parsedTracksById/healthData surgically (no /api/tracks refetch). Tests: `tests/test_duplicates.py`, `tests/test_duplicates_integration.py`, `TestDuplicatesEndpoint` + `TestDuplicatesDeleteEndpoint`, `tests/web/duplicates-*.test.js`.
- **Discover v2** lives in `autocue/analysis/discover/` (taste, style_graph, feeders/, ranker, scan_orchestrator, store) + REST surface under `/api/discover/*`. Per-scan budget is locked at **artist=20, label=15, novelty=10** (HARD_SCAN_REQUEST_CAP=60); changes need PRD §4 sync. Snooze durations are **1w / 1m / 3m** only — `'30d'` 400s the backend. `ScanConfig.timeout_seconds` (default **120s**) is a wallclock soft-cap that closes the scan row with `status='timeout'` if a feeder wedges — without it, a stuck urlopen pinned the concurrent-scan lock until restart (issue #174). The scan-supersede path in `runScan()` polls `/api/discover/feed/status` for ≤3s before issuing a new fetch so rapid filter changes don't race the orchestrator lock (issue #169). The filter UI is two rows: server-side (Source/Sort/Year) + client-side (search/style chips/hide-saved/hide-dismissed) persisted under `localStorage.ac_discover_filters`. Year options: `this`/`last2`/`last5`/`custom` (custom reveals an inline year input).
- **Discover YouTube preview** (`/api/youtube/search` + `_loadYouTubePreview` in `docs/index.html`): caller passes `artist` + `album` query params; the backend tags candidates whose result title + channel name contains no 4+ char artist token as `mismatch=true`, and DROPS mismatches when ≥1 candidate is a real match. Artist is the discriminating signal — album-only token overlap is too weak (place names like "Vénissieux" appear in both legit and corporate-services videos). The frontend filters mismatch-flagged candidates from the carousel; when every candidate is flagged, shows a "No YouTube match found" placeholder instead of loading a random video.
- **Track-card render: intelligence widgets ALWAYS render** (energy sparkline, mix-score chip, classification chip, similar button) — even on Skipped cards (tracks with existing hot cues). The Skipped path's early-return in `buildTrackCard` calls `_appendIntelligenceWidgets(cardMain, track)` before returning so these per-track surfaces appear regardless of cue-gen outcome. Auto-cue badges + phrase strip stay hidden on Skipped cards (those describe what would be written; intelligence describes the track itself). Issue #173 fix: `#preview-cues-btn` uses `activeTracks()` not `filteredTracks()` — Preview targets the selection like every other write op.
- **Sidecar analysis cache** (`autocue/cache.py`) lives at `<rekordbox_dir>/autocue_cache.sqlite`. Plain SQLite (no SQLCipher) — contains no audio, no credentials, no Discogs tokens. Per-track rows invalidate by `anlz_mtime`; ANLZ-missing tracks store `anlz_mtime=-1` sentinel so cold tracks don't re-attempt on every call. `/api/restore` calls `CacheStore.invalidate_all()`; `/api/apply` should call `invalidate_mixability(content_id)` (mixability depends on cue positions). Schema-version bumps drop + recreate all tables — no migrations in v1 (cache is regenerable from ANLZ). Reset via `autocue serve --reset-cache`. See `.agent/prd/PERFORMANCE_PRD.md` §7 for DDL.
- **Analysis thread-pool** (`autocue/analysis/concurrency.py`) is a process-singleton `ThreadPoolExecutor` shared by every multi-track analysis fanout (`/api/generate-apply-stream`, `/api/health`, `/api/classify`, `/api/auto-tag`, `/api/enrich-comments/stream`, similar index build). Default size is `min(8, cpu_count())`; override via `AUTOCUE_POOL_SIZE`. **Single-writer rule for `master.db` is preserved**: only one thread ever calls `db.commit()` on a given multi-track endpoint. Pyrekordbox `read_anlz_file()` thread-safety was verified 2026-06-07 (TASK-008, `tests/test_concurrency.py::test_anlz_read_concurrent` — gated `RUN_ANLZ_STRESS=1`); the six `AUTOCUE_PARALLEL_*` paths are **default-on** as a result. Set `AUTOCUE_PARALLEL_<NAME>=0` to disable any specific endpoint's parallel path if a regression is observed.
- **Perf instrumentation** (`autocue/perf.py`) — `perf_span(name)` context manager + ring buffer; zero overhead when `AUTOCUE_PERF` env is unset. Exposed dev-only via `GET /api/perf/recent` (404 unless `AUTOCUE_PERF=1`). Frontend mirror at `_perf` in `docs/index.html` is gated by `localStorage.autocue_perf === '1'`.
- **Virtualizer card-height invariant** (TASK-033): every track card MUST render at the same fixed height. The `Virtualizer` IIFE in `docs/index.html` computes the visible window in O(1) from `itemHeight`; variable-height cards would force per-card measurement. If a future feature needs in-list expansion, use a modal or overlay — NOT inline-expand on the card row.
- **Sticky-layout invariant under virtualization** (TASK-037): `#tracks-sticky` (filter bar) uses `position: sticky` anchored to `document.documentElement` scroll, NOT to `#track-list`. `#action-bar` is `position: fixed` against the viewport. When wiring the Virtualizer into `#track-list` (TASK-032), the scroll source MUST stay at the document level — switching to an inner `overflow: auto` container would break the sticky bars and the existing shadow-on-scroll IntersectionObserver. Document-level scroll works fine with virtualization because the Virtualizer uses absolute-positioned cards inside a tall spacer; the body still scrolls the whole page.

## Depth — read on demand

- Module map + REST endpoint list → `.claude/project/architecture.md`
- DB / pyrekordbox / DjmdCue / DjmdContent / DjmdKey / DjmdColor specifics → `.claude/project/db-constraints.md`
- API design (SSE patterns, CORS, source classification, /api/tracks SQL, /api/status diagnostic, restore, youtube/search bounds, has_phrase/has_beats) → `.claude/project/api-design.md`
- Web UI internals (AppState pub/sub, `_cardMap` diffing, RAF playhead, mini waveform, sticky bar, action bar, `_consumeSSE`) → `.claude/project/web-ui.md`
- Analysis modules + caches + testing (energy/transitions/setbuilder/auto-tag/comment/discogs/discovery/download, similar._INDEX guard, JS test sync, Hypothesis) → `.claude/project/analysis-and-testing.md`
- Discover v2 architecture (taste vector, style graph, feeders, ranker, scan orchestrator, store, novelty rotation, snooze popover, keyboard shortcuts, budget table) → `docs/reference/discover-v2.md`
- End-user feature documentation → `docs/FEATURES.md`

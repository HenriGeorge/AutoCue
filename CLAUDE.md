# AutoCue — Claude Code Guide

## Browser testing

When doing browser testing or UI verification with Chrome DevTools tools:
- **Always send screenshots to the user** using `SendUserFile` after every meaningful state change (before action, after action, result visible). Do not just describe what you see.
- **Screenshots are pre-approved** — take them freely without asking permission. `mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_screenshot` is always allowed.
- Save screenshots to `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/` (the allowed temp dir).

## Design reference

For any UI / visual work in `docs/index.html`, follow the **AutoCue design
system**, vendored at **`docs/design/`** (`styles.css` → `tokens/*`, full guide
in `docs/design/README.md`, component design-intent in
`docs/design/components/**/*.prompt.md`). It was reverse-engineered FROM
`docs/index.html`'s own `:root`/`html.dark` blocks — so the live `:root` is the
source of truth and the vendored tokens are the canonical target to reconcile to.
Online source (auth-gated, user opens it):
https://claude.ai/design/p/78d834e0-b83e-458a-a7f5-e38fa01d17ce?via=share

The five rules that make it look like AutoCue:
1. **Two themes** — light = "ElevenLabs clean" cool neutrals (`--bg #fafafa`,
   white surfaces); dark = warm **stone** (`--bg #0c0a09`, `--surface #1c1917`),
   toggled on `html.dark`. Test both.
2. **Green is signal, not decoration** — `--green` (`#159a05` / `#28e214` dark) is
   reserved for brand mark, BPM chips, active/selected, focus rings, success. The
   **primary CTA is the ink pill** (`--ink` bg / `--on-ink` text — black on light,
   white on dark), NEVER green. Accent backgrounds are ~8% washes (`--green-wash`).
3. **Mono for data** — every measured value (BPM, Camelot key, time, score, path,
   cue name) is `--font-mono` (JetBrains Mono); `--font-sans` (Inter) for all else.
4. **Pills for actions** — buttons/tabs/tags are `--radius-pill` (999px); data chips
   4px (`--radius-sm`), inputs 8px, panels 12px, elevated cards 16px (`--radius-xl`).
5. **Light & airy** — borders structure; soft shadows only lift on hover; flat
   neutral backgrounds (no gradients/textures/illustrations). Glass blur only on
   sticky chrome in motion. Cue palette A–H = bordered chip + 8% color wash.

Honour `prefers-reduced-motion`. Reference `var(--token)`; never hardcode hexes.

## What this project is

AutoCue places hot cues on Rekordbox 7 tracks automatically and analyses a DJ library, across three surfaces:

1. **Python CLI** (`autocue/`) — reads Rekordbox's database and ANLZ files directly. Fallback strategy: phrase → bar → heuristic. Outputs a Rekordbox XML for import.
2. **Local server** (`autocue serve`) — FastAPI at `localhost:7432`. Serves the web UI and exposes a REST API that reads/writes the Rekordbox database directly. **All intelligence features** (energy, mixability, classification, similar tracks, transitions, set builder, library health, auto-tagging, comment enrichment, Discogs, discovery, download) are only available in this mode.
3. **Web app** (`docs/index.html` + `docs/css/` + `docs/js/`) — browser-based, multi-file, **no build step**. Static / GitHub-Pages-ready (XML in/out); Pages is **not currently configured** (`/pages` 404s) — the app is reached via `autocue serve`, which also unlocks the full local-mode feature set.

## Development commands

```bash
pip install -e ".[dev]"              # install with test deps (fastapi, uvicorn, psutil, httpx, hypothesis)
pip install -e ".[download]"         # OPTIONAL: YouTube download support (yt-dlp; also needs ffmpeg on PATH)
pytest                               # run all 1446 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 671 Vitest tests for the web app

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

## No CI — validate locally before every merge

There is **no GitHub Actions / CI** (the workflow was removed intentionally to
avoid GitHub billing). The merge gate is the **local three-leg stack**, run green
before any merge to `main`:

```bash
pytest                                   # Python
npm test                                 # Vitest (web app)
cd tests/e2e && npx playwright test      # Playwright e2e
```

Do not re-add `.github/workflows/`. PRs and issues still work (free); merges are
admin-merged after the local stack is green.

## Must-know constraints (read every session)

- **Rekordbox must be closed** before any write (CLI or local-mode Apply). DB is SQLCipher-locked while open. Server enforces this at every write endpoint via `_rb_running(db)`.
- **Web app is multi-file with NO build step** (P0 split, 2026-06-12) — no bundler, no framework, ever. Entry `docs/index.html` (markup only); CSS in `docs/css/app.css`; legacy JS in `docs/js/app.js` (classic script, shared globals); **all NEW v2 code is native ES modules under `docs/js/v2/`** rooted at `js/v2/main.js` (interop: v2 reads legacy via `window.*`, exposes via `window.AC2`; legacy never imports v2). Specs that read app SOURCE use `loadAppHtml()` from `tests/web/_source.js` — never `readFileSync(docs/index.html)`. XML/Pages mode is **frozen**; the 2.0 shell renders in local mode only (program PRD: `.claude/PRPs/prds/autocue-2-program.prd.md`). `package.json` exists only for dev testing.
- **pyrekordbox**: use `Rekordbox6Database` from `pyrekordbox.db6`. `add_track()` takes the file path **positionally**.
- **ANLZ parsing**: wrap `db.read_anlz_file()` / `get_tag()` in `try/except Exception` — `ConstError` / `IndexError` are common for unsupported ANLZ versions and missing tags.
- **BPM guard**: always check `float(bpm) > 0` before using BPM in calculations — Rekordbox can store `"0.0"` (truthy string, zero float).
- **JS fetch error handling**: always check `r.ok` before reading typed properties from `r.json()` — error bodies return `{detail: "..."}` and reading `resp.applied` etc. yields `undefined` and misleading toasts.
- **Library Duplicates** (`autocue/analysis/duplicates.py` + `GET /api/duplicates` SSE + `POST /api/duplicates/delete` SSE) groups tracks by case-insensitive whitespace-normalised **`(artist, title, duration_bucket)`** where `duration_bucket = round(duration/5)` (5 s buckets keep encoder-rounded re-imports together but split an album cut from an extended mix). The keeper is picked by `(existing_hot_cues, play_count, last_played, bitrate, -track_id)` — **cue-prep outranks plays** so a freshly-prepped re-import beats a heavily-played auto-cued original. The user can override the keeper per-group via the **"Keep" radio**; the delete-button label, non-keeper set, and same-path chips recompute live on flip. **`delete_tracks` cascades through all 13 ContentID-bearing tables** (verified by `tests/test_duplicates_integration.py` against a real in-memory SQLite — DjmdCue/ContentCue/ContentActiveCensor/ContentFile/DjmdActiveCensor/DjmdSongHotCueBanklist/DjmdMixerParam/DjmdSongSampler/DjmdSongHistory/DjmdSongPlaylist/DjmdSongTagList/DjmdSongMyTag/DjmdSongRelatedTracks before DjmdContent) — a schema-pinned test fails if pyrekordbox adds a new ContentID table. Safety: 409 when Rekordbox running; **per-session backup window** (deletes <30 s apart reuse one backup, so /api/restore rolls back the whole session); **per-row savepoint**; **concurrency lock** 409s a second concurrent real delete (shared-session guard, released on every SSE exit path incl. client disconnect). The SSE delete streams per-batch progress and honours client-disconnect as cancel (rows committed before cancel survive; backup still restores). UI: confirm modal with primary disabled 250 ms after open, Cancel focused, focus-trap, ESC-during-delete aborts; in-place progress bar; **inline "Undo this delete"** banner (30 s) that POSTs `/api/restore`; same-file vs distinct-file chip per copy + a modal audio-summary line. Frontend invalidation via `_onTracksDeleted(ids)` prunes parsedTracks/parsedTracksById/healthData surgically (no /api/tracks refetch). Tests: `tests/test_duplicates.py`, `tests/test_duplicates_integration.py`, `TestDuplicatesEndpoint` + `TestDuplicatesDeleteEndpoint`, `tests/web/duplicates-*.test.js`.
- **Discover v2** lives in `autocue/analysis/discover/` (taste, style_graph, feeders/, ranker, scan_orchestrator, store) + REST surface under `/api/discover/*`. Per-scan budget is locked at **artist=20, label=15, novelty=10** (HARD_SCAN_REQUEST_CAP=60); changes need PRD §4 sync. Snooze durations are **1w / 1m / 3m** only — `'30d'` 400s the backend. `ScanConfig.timeout_seconds` (default **120s**) is a wallclock soft-cap that closes the scan row with `status='timeout'` if a feeder wedges — without it, a stuck urlopen pinned the concurrent-scan lock until restart (issue #174). The scan-supersede path in `runScan()` polls `/api/discover/feed/status` for ≤3s before issuing a new fetch so rapid filter changes don't race the orchestrator lock (issue #169). The filter UI is two rows: server-side (Source/Sort/Year) + client-side (search/style chips/hide-saved/hide-dismissed) persisted under `localStorage.ac_discover_filters`. Year options: `this`/`last2`/`last5`/`custom` (custom reveals an inline year input).
- **Discover YouTube preview** (`/api/youtube/search` + `_loadYouTubePreview` in `docs/index.html`): caller passes `artist` + `album` query params; the backend tags candidates whose result title + channel name contains no 4+ char artist token as `mismatch=true`, and DROPS mismatches when ≥1 candidate is a real match. Artist is the discriminating signal — album-only token overlap is too weak (place names like "Vénissieux" appear in both legit and corporate-services videos). The frontend filters mismatch-flagged candidates from the carousel; when every candidate is flagged, shows a "No YouTube match found" placeholder instead of loading a random video.
- **Track-card render: intelligence widgets + phrase strip ALWAYS render** (energy sparkline, mix-score chip, classification chip, similar button; and in phrase mode the phrase-structure strip) — even on Skipped cards (tracks with existing hot cues). The Skipped path in `buildTrackCard` calls `_appendPhraseStrip(...)` then `_appendIntelligenceWidgets(cardMain, track)` so these per-track surfaces appear regardless of cue-gen outcome. **Only auto-cue badges stay hidden on Skipped cards** (those describe what *would* be written; intelligence + structure describe the track itself). On a Skipped card the strip **merges the existing hot-cue positions onto itself as tick marks** (slot letter + name/time on hover, via `buildPhraseStrip(phrases, totalTime, cueTicks)`), so the #163 existing-cue chips and the strip share one 16px row inside the fixed 160px card (TASK-033) — the separate chip row is the **no-phrase-data fallback only**. `_updateTrackCardCues(trackId)` rebuilds Skipped cards when lazy phrase data lands. **Phrase cues load lazily by viewport** (#201): `_collectPhraseLazyIds`/`_flushPhraseLazyQueue` fetch only visible uncached phrase tracks (debounced) via `/api/generate` — no eager full-library "Computing phrase cues N/M" pass. Issue #173 fix: `#preview-cues-btn` uses `activeTracks()` not `filteredTracks()` — Preview targets the selection like every other write op.
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

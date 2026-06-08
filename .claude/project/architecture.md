# AutoCue — architecture map

## Module / file map

```
autocue/
  models.py      — PhraseLabel enum + CuePoint dataclass
  analyzer.py    — reads ANLZ .EXT (PSSI phrases) + .DAT (PQTZ beat grid)
  generator.py   — per-track strategy: phrase → bar → heuristic; GenerationPrefs dataclass
  writer.py      — writes CuePoints to Rekordbox XML via pyrekordbox.rbxml
  db_writer.py   — writes CuePoints directly to DjmdCue; backup + Rekordbox-running check
  cli.py         — argparse CLI; --track / --track-id / --library / --playlist; `autocue serve`
                   subcommand now also accepts --reset-cache (TASK-020).
  cache.py       — Sidecar SQLite at <rekordbox_dir>/autocue_cache.sqlite (TASK-010). CacheStore
                   with 6 tables (meta, energy_curve, classification, similarity_vector, mixability,
                   tracks_snapshot). Per-track rows keyed by (content.ID, anlz_mtime); MISSING_MTIME=-1
                   sentinel for tracks with no ANLZ. CacheStore.warm_up(db, content_ids, pool, ...)
                   for parallel hydration (TASK-018). Schema-version mismatch drops+recreates all
                   tables (no migrations; cache is regenerable).
  cache_reset.py — Implements `autocue serve --reset-cache` — removes only autocue_cache.sqlite +
                   -wal + -shm; never master.db (TASK-020).
  perf.py        — perf_span(name) context manager + 1000-entry ring buffer. AUTOCUE_PERF=1 toggles
                   (default = zero-overhead no-op). AUTOCUE_PERF_SAMPLE_RATE for partial sampling.
                   get_stats(name) → {count, p50, p95, p99}. Exposed via GET /api/perf/recent (TASK-044/045).
  __main__.py    — entry point
  download.py    — yt-dlp wrapper (OPTIONAL [download] extra; needs ffmpeg on PATH).
                   ytdlp_available() / ffmpeg_available() probes, default_download_dir()
                   (AUTOCUE_DOWNLOAD_DIR env → ~/Music/AutoCue), search_youtube(),
                   download_audio(url_or_query, dest_dir, audio_format, progress_cb).
                   All yt-dlp imports are lazy so the core install runs without it.
  analysis/
    concurrency.py  — Shared ThreadPoolExecutor (TASK-001). get_pool() / pool_size() / shutdown_pool().
                      Default size min(8, cpu_count()); override via AUTOCUE_POOL_SIZE. Single
                      process-wide pool reused by every analysis fanout. shutdown_pool() is wired
                      into the serve lifespan teardown.
    anlz_path.py    — get_anlz_mtime(content, db) → float | None. Single source of truth for the
                      CacheStore key — every L2 call site uses this so cache validity is consistent.
                      MISSING_MTIME = -1.0 re-exported.
    quality.py      — Cue Quality Checker: check_track_health(), check_library_health().
                      Pure DB reads (DjmdCue + DjmdContent). No ANLZ parsing.
                      Scores tracks 0–100; yields fix_tier: phrase/bar/heuristic/none.
                      AUTOCUE_PARALLEL_HEALTH=1 enables pool-fanout path (TASK-003) — completion-order
                      events, INTERNAL_ERROR isolation preserved. Default = serial (gated on TASK-008).
    energy.py       — PWAV waveform reader: get_energy_curve(content, db, n_points=50) → a
                      normalized 0–1 curve resampled to n_points (raw PWAV / 31.0, 3-point
                      smoothed, average-downsampled). Returns None when PWAV/.DAT is unavailable.
                      classify_energy_profile(curve) → "flat"/"build"/"wave"/"drop-then-flat".
                      L2 hook (TASK-013): set_cache_store(store) wires CacheStore so warm-path
                      reads short-circuit before ANLZ parse. Only the default n_points=50 hits L2.
    score.py        — Mixability score (0–100): intro/outro bars + energy variance + vocal proxy.
                      get_mixability() is CACHED in score._mixability_cache (keyed by content.ID).
                      L2 hook (TASK-016): set_cache_store(store); full result packed into
                      CacheStore.mixability.components_json so warm reads reconstruct in one JSON parse.
    classify.py     — Track classification: get_classification() → {primary, scores, bpm, energy_mean}
                      Five categories: warmup/build/peak/after_hours/closing. Cached in _class_cache.
                      L2 hook (TASK-014): set_cache_store(store); full result packed into
                      CacheStore.classification.scores_json — warm reads recover the whole dict.
                      AUTOCUE_PARALLEL_CLASSIFY=1 enables pool-fanout in /api/classify SSE (TASK-004).
    similar.py      — Cosine similarity on 6-dim feature vector (key, energy, variance, vocal proxy, BPM).
                      BPM gate ±8. Data-quality cap: score ≤ 0.65 when neither track has ANLZ energy data.
                      Builds in-process index on first call; pre-warms _class_cache via _index_track().
                      Thread-safe module index: _INDEX / _INDEX_BUILT / _INDEX_LOCK; clear_index() resets it.
                      L2 hook (TASK-015): set_cache_store(store); 6-float vector stored via
                      CacheStore.similarity_vector with NaN in energy_mean slot when ANLZ was missing
                      (recovered on read so the data-quality cap fires correctly on warm cache).
                      AUTOCUE_PARALLEL_SIMILAR=1 enables pool-fanout for the index build (TASK-007).
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
                      AUTOCUE_PARALLEL_AUTO_TAG=1 enables pool-fanout detector eval + single-writer
                      DjmdMyTag/DjmdSongMyTag writes (TASK-005); applies to both /api/auto-tag (multi-
                      type) and /api/auto-tag/discogs (SSE).
    comment.py      — Track comment enrichment → DjmdContent.Commnt. build_comment_string(),
                      enrich_comment(), enrich_comments_batch(). MIK-compatible format; appends a
                      re-writable "/* AutoCue: ... */" sentinel block to preserve user text.
                      AUTOCUE_PARALLEL_ENRICH_COMMENTS=1 enables pool-fanout string building +
                      single-writer DjmdContent.Commnt writes (TASK-006). User-text-over-cap rule
                      preserved on both paths.
    discogs.py      — Discogs API client: search_styles(artist, title, token) → genre/style list.
                      search_artist_releases(artist, token, year_from) → recent releases.
                      In-process token-bucket rate limiter (60 req/min) + per-process caches
                      (_cache for styles, _releases_cache for releases).
    discovery.py    — New-release suggestions: surfaces recent albums from the library's
                      most-played artists via Discogs, skipping albums already owned.
                      library_artists() / library_album_set() / iter_new_releases() (generator
                      for SSE) / suggest_new_releases(). Reuses discogs.py's rate limiter.
  serve/
    app.py       — FastAPI app factory + uvicorn launcher; CORS whitelist (localhost only).
                   New HTTP middleware (TASK-026) invalidates app.state.tracks_snapshot after any
                   2xx POST/PUT/DELETE under /api/* — centralised invalidation means future mutating
                   endpoints inherit the hook automatically (no per-handler sprinkling).
    deps.py      — lifespan DB connection + get_db dependency; opens CacheStore (TASK-010), wires
                   L2 hooks for energy/classify/score/similar, hydrates tracks_snapshot from
                   CacheStore on startup if master.db mtime matches (TASK-022), runs the multi-step
                   warm-up pipeline (`_run_warmup_pipeline` — TASK-027) on a daemon thread:
                   step 'cache' → 'index' → 'done'; state on app.state.warmup_progress under
                   warmup_lock; cancel_event drives graceful shutdown (TASK-030). shutdown_pool()
                   torn down on lifespan exit (TASK-001).
    routes.py    — see endpoint list below. New: _master_db_mtime helper + _wait_any helper for
                   bounded in-flight (TASK-040). /api/tracks gained ETag/304 (TASK-023), in-memory
                   snapshot fast path (TASK-021), CacheStore write-through (TASK-022), optional
                   NDJSON streaming (TASK-025). Six SSE endpoints gained AUTOCUE_PARALLEL_*-gated
                   parallel paths (TASKs 002/003/004/005/006/007). perf_span() wraps
                   tracks.cached / tracks.build (TASK-046).
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
  test_serve_routes.py       — 197 tests (FastAPI TestClient, mocked DB; covers all endpoints incl. discover/download)
  e2e/                       — Playwright smoke harness for the `autocue-qa` agent.
                               globalSetup allocates free ports (port-0, never the production 7432)
                               + sandbox copy of master.db; 0-safety.spec.ts verifies the server is
                               bound to the sandbox via GET /api/status + X-AutoCue-Diagnostic
                               header before any other spec runs (the `0-` prefix forces
                               alphabetical-first discovery; see issue #119). Specs: 0-safety, selectors-exist,
                               qa-smoke (read-only APIs + bounded SSE + UI smoke), pages-smoke
                               (static-served docs/index.html via `python -m http.server`),
                               qa-full (write endpoints, gated by RUN_FULL=1). See tests/e2e/README.md.
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

## REST API endpoints (`serve/routes.py`)

```
GET  /api/status                          GET  /api/playlists
GET  /api/tracks (?playlist_id=N)         GET  /api/tracks/{id}/artwork
GET  /api/tracks/{id}/audio               GET  /api/tags
POST /api/generate                        POST /api/apply
POST /api/generate-apply                  POST /api/generate-apply-stream (SSE)
POST /api/delete-cues                     POST /api/color-tracks
POST /api/color-tracks-stream (SSE)       GET  /api/backups
POST /api/restore                         DELETE /api/backups/{filename}
GET  /api/tracks/{id}/health              GET  /api/health (SSE, ?playlist_id=N&limit=N)
POST /api/cue-tools-stream (SSE)          GET  /api/tracks/{id}/energy
GET  /api/tracks/{id}/mixability          GET  /api/tracks/{id}/classification
GET  /api/classify (SSE, ?playlist_id=N)  GET  /api/tracks/{id}/similar
POST /api/transitions/score               POST /api/setbuilder
GET  /api/setbuilder/alternatives         POST /api/playlists/suggest
POST /api/playlists (create)              POST /api/auto-tag
POST /api/auto-tag/undo                   GET  /api/config
POST /api/auto-tag/discogs/test           POST /api/auto-tag/discogs (SSE)
POST /api/enrich-comments                 POST /api/enrich-comments/preview
POST /api/enrich-comments/stream (SSE)    POST /api/enrich-comments/undo
GET  /api/discover (SSE)
GET  /api/download/config                 POST /api/download (SSE)
POST /api/download/album (SSE)
POST /api/tracks/check-audio              GET  /api/youtube/search
GET  /api/warmup (TASK-028)               GET  /api/perf/recent (TASK-045, dev-only — 404 unless AUTOCUE_PERF=1)
```

## Performance layer (TASKs 001–050; 46 of 50 merged)

The performance subsystem layers on top of the existing architecture without breaking the
write contract:

- **Concurrency**: `autocue/analysis/concurrency.py` exposes a process-singleton
  `ThreadPoolExecutor`. Every multi-track analysis fanout (generate-apply, health,
  classify, auto-tag, enrich-comments, similar index build) shares it via `get_pool()`.
  Size via `AUTOCUE_POOL_SIZE`; default `min(8, cpu_count())`. Pool serves I/O-bound work;
  the SSE generator loops are the single writers (preserves master.db single-writer rule).
- **L1/L2 cache hierarchy**: each analysis module keeps its existing in-process LRU (L1)
  plus an optional CacheStore sidecar (L2) wired via `set_cache_store()`. L2 keys are
  `(content.ID, anlz_mtime)`; ANLZ-missing tracks use `anlz_mtime=-1` sentinel to skip
  recompute. /api/restore calls `CacheStore.invalidate_all()`.
- **Snapshot pipeline**: `/api/tracks` builds an in-memory + on-disk snapshot keyed by
  `master.db` mtime. ETag enables 304; NDJSON optional. Mutating endpoints invalidate via
  HTTP middleware in `serve/app.py`.
- **Warm-up**: lifespan spawns a daemon thread that hydrates the L2 cache + builds the
  similarity index. Progress on `app.state.warmup_progress`, polled by the frontend
  `_warmupPoll` IIFE and surfaced as `#status-warmup` "Indexing N/M" chip.
- **Instrumentation**: `autocue/perf.py` ring buffer + `GET /api/perf/recent` (dev-only).
  Frontend mirror `_perf` in `docs/index.html` (`localStorage.autocue_perf === '1'`).
- **Flagged parallel SSE**: 6 SSE endpoints gained AUTOCUE_PARALLEL_*-gated parallel paths
  (TASKs 002/003/004/005/006/007). Default behaviour unchanged until the maintainer runs
  TASK-008's `RUN_ANLZ_STRESS=1` verification against a real Rekordbox library; at that
  point each flag flips to default-on.

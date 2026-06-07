# AutoCue — architecture map

## Module / file map

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
  test_serve_routes.py       — 197 tests (FastAPI TestClient, mocked DB; covers all endpoints incl. discover/download)
  e2e/                       — Playwright smoke harness for the `autocue-qa` agent.
                               globalSetup allocates free ports (port-0, never the production 7432)
                               + sandbox copy of master.db; safety.spec.ts verifies the server is
                               bound to the sandbox via GET /api/status + X-AutoCue-Diagnostic
                               header before any other spec runs. Specs: safety, selectors-exist,
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
```

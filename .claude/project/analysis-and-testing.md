# Analysis modules + testing approach

## Comment enrichment / Cue quality

- **Comment enrichment format**: `enrich_comment()` in `analysis/comment.py` writes `"8A - Energy 7 | Peak | 4 bar intro"` (MIK-compatible prefix). Appends `/* AutoCue: ... */` sentinel to existing comments; sentinel block is replaced on re-run (idempotent). `enrich_comments_batch()` makes a DB backup before writing and now returns `undo_data = {"modified": [{content_id, previous}, ...]}` — pass it to `restore_comments(db, undo_data)` (or `POST /api/enrich-comments/undo`) to reverse a run without a full DB restore. Final string is capped at `MAX_COMMENT_LEN = 256` chars (CDJ UI render limit); AutoCue drops its own parts in order `intro → category → energy` to fit. User-authored text is never trimmed — when the user's existing comment alone is over the cap, the track is skipped instead of corrupting it.

- **Comment enrichment (detail)**: `autocue/analysis/comment.py` writes to `DjmdContent.Comment`. Format is MIK-compatible: `8A - Energy 7 | Peak | 4 bar intro`. To preserve user-authored text, AutoCue appends a re-writable sentinel block `/* AutoCue: ... */` (the same convention Rekordbox uses for "Add My Tag to Comments"); on re-run, only the sentinel block is replaced unless `overwrite=True`. Energy is mapped to a 1–10 MIK scale. `enrich_comments_batch()` makes a DB backup before writing (unless `dry_run`). Functions do not commit — the route commits.

- **Cue Quality Checker**: `autocue/analysis/quality.py`. Score: -30 NO_CUES, -10 NO_PHRASE, -10 NO_BEATGRID, -5 DUPLICATE_CUE, -5 UNNAMED_CUES. NO_AUDIO_FILE forces score=0 and skips all other checks. NO_MEMORY_CUE is info-only (zero score impact). Duplicate detection compares `InFrame` values directly (threshold < 2 frames ≈ <13ms). FolderPath on DjmdContent stores the complete audio file path (not just folder).

## Analysis caches

- **Analysis module caches** (all cleared by the `conftest.py` autouse fixture before every test):
  - `energy._cache` — L1 keyed by `(content.ID, n_points)` (NOT just the track ID — the curve length is part of the key).
  - `classify._class_cache` — L1 keyed by `content.ID`.
  - `score._mixability_cache` — L1 keyed by `content.ID`. `get_mixability` IS cached (this changed from earlier — it is no longer always recomputed).
  - `similar._INDEX` / `_INDEX_BUILT` — the in-process similarity index; reset via `similar.clear_index()`.
  - `CacheStore` (L2 sidecar) — see "Sidecar cache (L2)" below.

- **similar._INDEX / _INDEX_BUILT**: The similarity index is module-level in `similar.py`, guarded by `_INDEX_LOCK`. To check from another module (e.g. `setbuilder.py`) whether the index is built, import the module (`from . import similar as _similar_mod`) and check `_similar_mod._INDEX_BUILT` — do NOT import `_INDEX_BUILT` directly (that creates a copy that never updates). The server pre-warms the index in a background thread on startup (`deps._prewarm_index`, now embedded in `_run_warmup_pipeline`).

## Sidecar cache (L2)

- **CacheStore** at `<rekordbox_dir>/autocue_cache.sqlite` (`autocue/cache.py`, TASK-010). Plain
  SQLite (no SQLCipher) — contains no credentials, no audio, no Discogs tokens. WAL mode +
  `check_same_thread=False` + a `threading.Lock` for serialised access. Schema-version mismatch
  drops + recreates all tables (no migrations in v1; cache is regenerable). Reset via
  `autocue serve --reset-cache` (TASK-020) which removes only the three known files
  (`autocue_cache.sqlite` + `-wal` + `-shm`) — never `master.db`.
- **Six tables**: `meta`, `energy_curve`, `classification`, `similarity_vector`, `mixability`,
  `tracks_snapshot`. Per-track rows keyed by `(content.ID, anlz_mtime)`. `MISSING_MTIME = -1.0`
  sentinel stored for tracks with no ANLZ so we don't re-attempt on every call until the file
  reappears. Resolve current mtime via `autocue.analysis.anlz_path.get_anlz_mtime(content, db)` —
  every L2 callsite must go through this helper so the key calculation stays consistent.
- **L2 wiring per module** (each exposes `set_cache_store(store)` called from
  `serve/deps.py:lifespan`):
  - `energy.get_energy_curve` (TASK-013) — only the default `n_points=50` hits L2; non-default
    callers fall through to compute + L1.
  - `classify.get_classification` (TASK-014) — full result dict packed into `scores_json` so warm
    reads recover label/color/confidence/energy_peak/vocal_proxy without recompute.
  - `similar._index_track` (TASK-015) — 6-float vector with NaN in `energy_mean` slot when ANLZ
    was missing (recovered on read so the find_similar data-quality cap at ≤0.65 fires correctly).
  - `score.get_mixability` (TASK-016) — full result packed into `components_json`; MISSING
    sentinel caches `None` in L1 to skip retry.
- **Invalidation**: `/api/restore` calls `CacheStore.invalidate_all()` (TASK-017). The HTTP
  middleware in `serve/app.py` clears the in-memory `tracks_snapshot` on any 2xx mutating
  request to `/api/*` (TASK-026); the master.db mtime check inside the handler makes the
  on-disk snapshot self-invalidate.
- **Warm-up** (TASK-018, TASK-027): `CacheStore.warm_up(db, content_ids, pool, progress_cb, cancel_event, batch_size)`
  drives parallel hydration through the shared pool. `serve/deps.py:_run_warmup_pipeline`
  invokes it as the `cache` step then proceeds to the `index` step (similarity build) then
  `done`. Progress on `app.state.warmup_progress` (`{step, done, total, finished_at}`) under
  `warmup_lock`; `warmup_cancel_event` lets the lifespan shutdown drain in-flight work within 5s.

## Concurrency primitive

- **Shared thread pool** (`autocue/analysis/concurrency.py`, TASK-001). `get_pool()` returns a
  process-singleton `ThreadPoolExecutor`. Size: `pool_size()` reads `AUTOCUE_POOL_SIZE` env
  (defaults to `min(8, cpu_count())`); non-integer values raise `ValueError`. `shutdown_pool()`
  is idempotent and torn down on the serve lifespan shutdown.
- **Single-writer invariant**: the pool serves only the read/compute side of every multi-track
  endpoint. The SSE generator loop is the single writer for `master.db` (calls
  `db.session.commit()` per track). `tests/test_concurrency_invariants.py` pins this contract
  — adding a parallel write path will fire those tests.
- **Flagged parallel SSE paths**: six endpoints gained pool-fanout implementations behind
  env vars, default-off until TASK-008 pyrekordbox stress verification:
  - `AUTOCUE_PARALLEL_GENERATE_APPLY` → `/api/generate-apply-stream` (TASK-002, plus TASKs 039–043
    refinements: bounded in-flight `2 * pool_size`, `_wait_any` helper, threading.Event
    cancellation polling `request._is_disconnected`).
  - `AUTOCUE_PARALLEL_HEALTH` → `/api/health` (TASK-003).
  - `AUTOCUE_PARALLEL_CLASSIFY` → `/api/classify` (TASK-004).
  - `AUTOCUE_PARALLEL_AUTO_TAG` → `/api/auto-tag` + `/api/auto-tag/discogs` (TASK-005).
  - `AUTOCUE_PARALLEL_ENRICH_COMMENTS` → `/api/enrich-comments/stream` (TASK-006).
  - `AUTOCUE_PARALLEL_SIMILAR` → `similar._build_index` (TASK-007); uses `_index_track_safe`
    worker that swallows exceptions and returns them to the reducer.

## Perf instrumentation

- **Backend** (`autocue/perf.py`, TASK-044): `perf_span(name)` context manager records
  wall-clock to a 1000-entry ring buffer. Zero overhead when `AUTOCUE_PERF` is unset (no-op
  yield). `AUTOCUE_PERF_SAMPLE_RATE` accepts floats for partial sampling. `get_stats(name)`
  returns `{count, p50, p95, p99}`. Currently wrapped around `energy.L1.hit` / `energy.L2.lookup`
  / `energy.compute` (TASK-046 — extend to more endpoints as needed) and `/api/tracks`
  `tracks.cached` / `tracks.build`.
- **Endpoint** (TASK-045): `GET /api/perf/recent` returns 404 unless `AUTOCUE_PERF=1`; otherwise
  `{spans: [...], stats: {name: {count, p50, p95, p99}}}`. `?limit=` clamped to `[1, 1000]`.
- **Frontend** (`docs/index.html`, TASK-049/050): `_perf` IIFE wraps `performance.mark` /
  `performance.measure`; gated by `localStorage.autocue_perf === '1'`. Logs to console as
  `[AutoCue Perf] <name>: <duration>ms`. Currently wraps `loadTracksFromServer` (library-load)
  and `filteredTracks` (filter-recompute).

## Energy / transitions / setbuilder

- **PWAV energy curve**: `get_energy_curve(content, db, n_points=50)` returns a fixed-length normalized 0–1 curve (default 50 points), resampled by averaging from the raw PWAV amplitudes in the `.DAT` ANLZ file. It is NOT one-float-per-150ms-column. To map a `position_ms` to an index, scale against the track's duration and `len(curve)` — do not assume 150ms per sample. Returns `None` (not `[]`) when PWAV is absent or the ANLZ read fails.

- **Energy profile**: `classify_energy_profile(curve)` → one of `"flat"` (low variance), `"build"` (second-half mean rises), `"wave"` (≥2 local maxima), `"drop-then-flat"` (early peak then lower — the fallback). Used by the energy_profile auto-tag detector and the `/api/tracks/{id}/energy` response.

- **Transition scoring**: `score_transition(a, b, db)` returns `{overall, bpm, key, energy, bpm_a, bpm_b, key_a, key_b, end_energy_a, start_energy_b, explanation}`. The `end_energy_a` and `start_energy_b` fields are scalars (or None) — pass them directly to energy-penalty functions; do not re-read ANLZ curves. The `bpm_a/bpm_b/key_a/key_b` fields exist so `transition_advice(ts)` can render a mixing tip without re-reading the source content. **Missing energy data**: `_energy_score(None, None) = 50.0` (neutral, NOT 100); one side missing caps score at 75. This deliberately defeats the old "free 100 energy score" that made every same-key same-BPM no-ANLZ transition score 100 overall.

- **Set Builder beam search**: `build_set()` in `setbuilder.py`. Uses `find_similar(track_id, db, n=20)` per step — **doubled to n=40 when `end_bpm ≠ start_bpm`** to surface higher-BPM candidates (still O(n×K), not O(n²)). Deduplication per beam covers three axes: `visited: set[int]` (track ID), `visited_titles: set[str]` (`"title|||artist"` lowercase — blocks duplicate imports of the same song), `visited_artists: dict[str, int]` (capped at 2 appearances per artist by default). BPM monotonic step gated by `bpm_step_max` (default 8%); the BPM gate is **asymmetric** — at least ±12 BPM toward `end_bpm` when building/dropping. **Setbuilder-specific transition reweighting**: when `end_bpm ≠ start_bpm`, `overall = 0.25×bpm + 0.40×key + 0.35×energy` (instead of the standard 0.40/0.35/0.25) so BPM progression is not structurally punished. A **BPM-progress bonus** of up to +15 points rewards candidates that move toward `end_bpm`. A **trajectory-deficit penalty** of up to **−25 points** (issue #116 fix) handles the small-span case (e.g. 120→128 / 30min) where the bonus is proportionally diluted: compute `expected_bpm = start_bpm + (end_bpm − start_bpm) × (beam.total_duration / 60 / duration_minutes)`; if `cand_bpm` is more than 1 BPM below the line in build mode (or above in drop mode), subtract `min(25, deficit × 4)`. Flat sets (start == end) skip the penalty. `_find_seed()` is two-pass: first pass requires `bpm ≥ start_bpm × 0.97`, falling back to any BPM only if no in-range track exists. `seed_track_id` overrides seed selection; `anchor_track_ids` are must-include tracks merged into the beam's result at BPM-sorted positions via `_merge_anchors()`. Each `SetTrack` carries a `mix_advice: str` produced by `transition_advice()`. `get_classification()` is pre-warmed during `_index_track()` so beam search lookup is O(1). `build_alternatives()` / `/api/setbuilder/alternatives` returns swap candidates for one slot scored on fit to both neighbours; candidates with `genre` mismatching the replaced track's genre (and the neighbours' genres) get a **−20 point penalty** and `genre_match=False`. Candidates with no `GenreName` get `genre_match=None` (no penalty).

## Auto-tag / Discogs / Discovery / Download

- **Auto-Tag (My Tags)**: `autocue/analysis/auto_tag.py` writes results to `DjmdMyTag` + `DjmdSongMyTag`. Tags are created with an explicit `db.generate_unused_id`, `UUID`, `Name`, `Attribute` (color hint 1–8, mirroring DjmdColor SortKey), and `Seq`. `ensure_category_tags()` / `ensure_tag_by_name()` are idempotent (reuse existing tags by name; AutoCue's own names live in `AUTOCUE_TAG_NAMES`). `apply_classification_tags()` only writes the top category when its score ≥ `MIN_SCORE` (0.70) and skips tracks with no ANLZ energy data. `apply_tags()` is the multi-type entrypoint (category, vocal, energy_level, energy_profile, intro_outro, decade, bpm_tier, play_history). Every run returns `undo_data` consumed by `undo_tag_run()` / `/api/auto-tag/undo`.

- **Discogs**: `autocue/analysis/discogs.py` `search_styles(artist, title, token)` returns Discogs Style strings. A per-process token-bucket limits to 60 req/min; results are cached in `discogs._cache` keyed by lowercased `(artist, title)`. The personal access token comes from the request body or `DISCOGS_TOKEN`. `/api/config` reads `DISCOGS_TOKEN` from a project-root `.env` (then the environment) so the UI can pre-fill it; `/api/auto-tag/discogs/test` validates the token against the Discogs identity endpoint. `search_artist_releases(artist, token, year_from)` (cached in `_releases_cache`) shares the same token bucket. `DiscogsTagRequest.skip_existing` (default `False`) tells `/api/auto-tag/discogs` to skip tracks that already carry **non-AutoCue My Tags** (the assumption being those are pre-existing Discogs styles); detection uses `auto_tag.ALL_AUTOCUE_TAG_NAMES` as the allowlist.

- **Discovery (new releases)**: `autocue/analysis/discovery.py` reuses the Discogs client to suggest recent albums from the library's artists. `library_artists(db, top_n)` ranks artists by play-frequency (a proxy for what the DJ cares about) so a big library does not blow past the rate limit — only the top N artists are queried. `library_album_set(db)` builds the owned-album set (normalized lowercase) used to filter suggestions. `iter_new_releases(...)` is a **generator** yielding `(processed, total, suggestion|None)` for SSE; `None` ticks report progress for artists with no new releases. `since_year` defaults to *last year*. `/api/discover` requires a Discogs token (query param → `_resolve_discogs_token()` env/.env) and streams one `DiscoverItem` per suggestion, then `{done:true}`. `DiscoverItem.formats` carries Discogs format tags (e.g. `["Vinyl", "LP", "Album"]`) for the UI's format chips.

- **Download (yt-dlp)**: `autocue/download.py` is an **optional** feature gated behind the `[download]` extra (`pip install -e ".[download]"`) and an `ffmpeg` binary on PATH. All `yt_dlp` imports are **lazy** so the core CLI/server import without it. `ytdlp_available()` / `ffmpeg_available()` probe at runtime; `/api/download/config` reports both plus `default_download_dir()` (env `AUTOCUE_DOWNLOAD_DIR` → `~/Music/AutoCue`) and `music_folder` — the detected Rekordbox music root, computed by `_detect_music_folder(db)` via `os.path.commonpath()` over up to 30 absolute `DjmdContent.FolderPath` values (returns `None` on failure). `download_audio(url_or_query, ...)` passes real URLs through and wraps bare terms as `ytsearch1:`; it extracts audio via the `FFmpegExtractAudio` postprocessor and returns the final file path. `/api/download` (single, runs the blocking download in a worker thread and streams progress via a `queue.Queue`) and `/api/download/album` (sequential) both **return 503** when yt-dlp/ffmpeg are missing and stream SSE `DownloadEvent`s otherwise. **Legal note**: downloading copyrighted audio may violate YouTube's ToS / copyright — the UI shows a disclaimer; lawful use is the user's responsibility.

## Duplicates

- **Duplicate-track detector**: `autocue/analysis/duplicates.py` (pure stdlib, no DB writes). `normalize_key(artist, title, duration)` → `"a|||t|||round(dur/5)"` — case-insensitive, whitespace-collapsed; the 5-second **duration_bucket** keeps encoder-rounded re-imports together but splits an album cut from an extended mix; omitting `duration` buckets to 0 (phase-1/2 compatible). `find_duplicate_groups(tracks)` groups `TrackProjection`s and returns only ≥2-copy buckets, sorted `(-copy_count, artist, title)`. **Empty-metadata tracks (no artist + no title) are excluded** — they'd form one fake bucket. `pick_keeper(copies)` picks the keeper by `max` over `(existing_hot_cues, play_count, last_played, bitrate, -track_id)` — **cue-prep outranks plays** so a freshly-prepped re-import beats a heavily-played auto-cued original; the user can override per-group via the UI radio. `DuplicateGroup.to_dict()` emits per-copy `duration`/`bitrate`/`folder_path`/`file_name`/`same_path_as_keeper`/`is_keeper` so the frontend can recompute the same-file-vs-distinct-file chip against the user-chosen keeper.
- **Destructive delete**: `db_writer.delete_tracks(db, track_ids, *, dry_run, cancel, progress_cb)` — per-row `begin_nested()` savepoint; **cascades all 13 ContentID-bearing tables** (DjmdCue, ContentCue, ContentActiveCensor, ContentFile, DjmdActiveCensor, DjmdSongHotCueBanklist, DjmdMixerParam, DjmdSongSampler, DjmdSongHistory, DjmdSongPlaylist, DjmdSongTagList, DjmdSongMyTag, DjmdSongRelatedTracks) before `db.delete(content)` so no child row orphans. `cancel` (threading.Event) stops mid-batch with rows committed-so-far surviving; `progress_cb(processed, deleted, skipped)` drives SSE progress. **Schema-pinned integration test** (`tests/test_duplicates_integration.py`) builds a real in-memory SQLite with the full pyrekordbox schema, seeds one row in every ContentID table, and fails if the cascade ever drops a table.
- **REST surface**: `GET /api/duplicates` (SSE scan: `{total}` → `{group}` per bucket → `{done, summary}`) and `POST /api/duplicates/delete` (SSE delete with per-batch progress + client-disconnect cancel). Safety: 409 when Rekordbox running; **per-session backup window** (deletes <30 s apart reuse one backup so `/api/restore` rolls back the whole session); **concurrency lock** 409s a second concurrent real delete (shared-session guard, released on every SSE exit path). Undo = `/api/restore` against the returned `backup_path`. Tests: `test_duplicates.py`, `test_duplicates_integration.py`, `TestDuplicatesEndpoint` + `TestDuplicatesDeleteEndpoint`, `tests/web/duplicates-*.test.js`.

## Testing approach

Tests mock pyrekordbox objects rather than hitting a real database. When adding tests for `analyzer.py`, mock `db.read_anlz_file()` and the returned `AnlzFile` objects with a `.get_tag()` method returning objects that have the expected `.content` structure (`entries`, `mood`, `beat`, `kind`, `time` fields). **Exception — `delete_tracks`**: the FK cascade can't be verified against MagicMock (a dropped table is silent), so `test_duplicates_integration.py` uses a **real in-memory SQLite** with the pyrekordbox schema applied. Use that pattern for any future multi-table-cascade write.

`test_properties.py` uses **Hypothesis** (in the `[dev]` extra) for generative property/invariant tests over the pure math in `classify.py` and `transitions.py`. If Hypothesis is missing, that one module fails at collection — run `pip install -e ".[dev]"` so `pytest` collects cleanly.

JS tests in `tests/web/` copy functions verbatim from `docs/index.html` and run them in jsdom via Vitest. If you change `parseRekordboxXml`, `generateCues`, `computeCues`, `colorTracksByBpm`, `applyToRekordbox`, `filteredTracks`, `ensureLocalAudio`, `_explainCue`, `_esc`, or `_renderSuggestion` in `index.html`, update the corresponding copies in the test files. The `ui-logic.test.js` file tests sort label lookup, memory cue prepend logic, fetch HTTP error handling, the full `filteredTracks` filter matrix, backup multi-select, `_explainCue` across all explanation modes (phrase/bar/heuristic/memory/manual), the Discover card renderer (`_renderSuggestion` + `_esc` escaping), and the `AppState` pub/sub bus (coalescing, unsubscribe, multi-key). The `AppState` test helper (`makeAppState()`) is a standalone copy in `ui-logic.test.js` — update it if the production `AppState` logic changes.

`tests/test_download.py` mocks the `yt_dlp` module via `sys.modules` patching (yt-dlp is not a test dependency) and stubs `shutil.which` for ffmpeg detection — so the download tests run everywhere without the optional extra installed. `tests/test_discovery.py` patches `discovery.search_artist_releases` rather than hitting Discogs.

### Perf-test gating + concurrency tests

- **Perf marker** (TASK-048): `[tool.pytest.ini_options].markers = ['perf']` registers the
  marker; `tests/conftest.py:pytest_collection_modifyitems` skips `@pytest.mark.perf` tests
  unless `RUN_PERF=1` is in env. Future benchmark suites under `tests/perf/` inherit this
  gating automatically. The pre-existing `tests/perf/test_tracks_snapshot_perf.py` (TASK-047)
  covers snapshot p95 < 50ms and 304 p95 < 10ms on 10k synthetic items.
- **Concurrency tests** (TASK-009): `tests/test_concurrency.py` covers the pool primitive
  (size resolution, singleton, shutdown, exception isolation, completion ordering,
  thread-leak bound). `tests/test_concurrency_invariants.py` adds the cross-endpoint
  guards: pool used for reads only in quality + similar paths, monotonic counter contract,
  no thread leak across 100 fanouts, `_INDEX_LOCK` blocks concurrent builds.
- **ANLZ stress test** (TASK-008, gated `RUN_ANLZ_STRESS=1`):
  `tests/test_concurrency.py::test_anlz_read_concurrent` hammers `db.read_anlz_file()` from 16
  threads against a real Rekordbox library. Must pass before any `AUTOCUE_PARALLEL_*` flag
  flips to default-on.
- **Parallel-path test convention**: each flagged-parallel endpoint has its own
  `tests/test_*_parallel.py` exercising default-off + flag-on + per-track exception isolation
  + (for write paths) undo round-trip. Files: `test_quality_parallel.py`,
  `test_classify_similar_parallel.py`, `test_generate_apply_parallel.py`,
  `test_generate_apply_bounded.py`, `test_auto_tag_parallel.py`,
  `test_enrich_comments_parallel.py`.
- **CacheStore tests** (`tests/test_cache.py`, `tests/test_cache_warmup.py`): use
  `CacheStore.open_memory()` for `:memory:` SQLite. Lock-driven serialisation makes them safe
  to run in parallel.

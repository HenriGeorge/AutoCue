# API design notes (serve/routes.py)

- **CORS**: the local server (`autocue serve`) only allows origins `null` (file://), `http://localhost:{port}`, and `http://127.0.0.1:{port}`. Do not widen this — the server writes to the Rekordbox database.

- **GZip middleware**: `serve/app.py` installs `GZipMiddleware(minimum_size=1000)` ahead of the CORS middleware. SSE streams pass through unchanged (Starlette skips gzip when the response uses `text/event-stream` plus `X-Accel-Buffering: no`).

- **Apply performance**: The UI uses `/api/generate-apply-stream` (SSE) which streams progress events `{"processed":N,"total":M,...}` as each track is processed, then a final `{"done":true,...}`. The JS reads via `fetch` + `ReadableStream` (not `EventSource`, since the request is POST). No `AbortSignal.timeout` — the stream has no time limit.

- **Playlist filter**: `/api/tracks` uses `?playlist_id=<int>` (integer FK). The old `?playlist=<name>` param was removed. The frontend dropdown passes the numeric ID.

- **`/api/tracks` SQL pattern**: history (`DjmdSongHistory`) and my-tags (`DjmdSongMyTag`) are loaded with `db.query(...).all()` then filtered in Python against `row_ids` — **not** with a SQLAlchemy `IN(row_ids)` clause. This is intentional: for full-library page loads (~3k rows) the `IN` filter is slower than fetch-all-then-filter against pyrekordbox's SQLCipher. Do not "optimize" this back to `.filter(...ContentID.in_(row_ids))`.

- **`/api/status` diagnostic field**: `StatusResponse.db_path` is `None` by default. Callers that opt in with the header `X-AutoCue-Diagnostic: 1` get back the resolved `master.db` path. The web UI never sets it; the `autocue-qa` Playwright harness uses it (in `tests/e2e/0-safety.spec.ts`) to verify the server is bound to a sandbox copy of the DB and not the user's real library before any other spec runs. Do not log the field outside that one diagnostic context.

- **`/api/tags` returns only used tags**: the endpoint filters `DjmdMyTag` against `distinct(DjmdSongMyTag.MyTagID)` so unused tags (created and never applied) do not appear in the UI's tag filter list. Tests must mock both queries (see `_make_tags_db()` in `test_serve_routes.py`).

- **`/api/playlists/suggest` seeds**: `PlaylistSuggestRequest.seed_track_ids` (list[int], default `[]`) are pre-included tracks placed at the front of the result in user-supplied order. Seeds **bypass `exclude_ids`** (so you can pre-pin a track the caller already excluded). The remaining `req.count − len(seeds)` slots are filled by weighted random draw from the top pool.

- **`/api/enrich-comments/stream` per-track commit**: the SSE stream commits each track individually inside the per-track try block (rollback on failure → increments `errors` and decrements `enriched`). The old batched commit at the end is gone — a single failing track no longer rolls back the whole batch. Backup is still made once up front before any writes.

- **Restore backup**: `/api/restore` accepts `{filename}` (not a full path — validated to be within BACKUP_DIR). The endpoint closes `db._engine.dispose()` before copying the file, then reopens via `Rekordbox6Database` and updates `app.state.db`. WAL/SHM sidecars are handled on both backup and restore. **After any restore, clear the stale analysis state** — the restore path calls `similar.clear_index()` (and the analysis caches) so the rebuilt DB does not match old feature vectors.

- **`source` on TrackItem**: `"file" | "streaming" | "unknown"` derived from `DjmdContent.FolderPath` via a cheap string check in `_classify_source()` — empty path / streaming-URI prefixes (`spotify:`, `tidal:`, `applemusic:`, `http(s)://`) → `"streaming"`, absolute filesystem path → `"file"`. **No `os.path.exists()` in `/api/tracks`** — lazy verification lives at `POST /api/tracks/check-audio` with a three-state response (`"file" | "missing" | "unverified"`) and `unverified_dirs` for fail-open soft-warning UX. The endpoint groups stat calls by parent directory via `os.scandir()` (one syscall per directory, not per file), caches by `(content_id, parent_mtime)` in `routes._audio_check_cache` (cleared on `/api/restore` alongside the other analysis caches), and rejects >1000 IDs with HTTP 429. Client paginates 500/chunk sequentially with a 200ms debounce; results merge into `_audioProbedAt` and override the schema `source` for the filter logic. The 🔌 Audio available toggle hides `source !== "file"` tracks plus any with a `_audioProbedAt[id] === "missing"` verdict — fail-open on `"unverified"`.

- **`GET /api/youtube/search`**: search-only wrapper around `dl.search_youtube(query, n=5)`. Bounded by `routes._yt_search_semaphore` (max 2 concurrent yt-dlp invocations; excess → 429). In-flight searches dedupe by exact query via `routes._inflight_yt_searches` so repeat clicks coalesce. Each search runs under a 30-second `Future.result(timeout=30)` hard cap; timeout releases the slot and returns 504 so a hung YouTube response can't permanently jam the cap. Client side: candidate-selection modal with editable query input and explicit "Search" button (no type-to-search — would spawn one yt-dlp per keystroke). Optional `artist` + `album` query params drive `_youtube_token_mismatch` — each candidate is tagged `mismatch=True` unless a 4+ char artist token appears in its title OR channel name; when ≥1 candidate is a real match, mismatches are DROPPED server-side; when ALL are mismatches they're all returned (still flagged) so the caller can show "no clean match" rather than load a wrong video. Backend tests at `tests/test_youtube_search.py` (14 specs).

- **has_phrase / has_beats**: Two analysis-readiness flags on every `/api/tracks` row. `has_phrase` reads `content.AnalysisDataPath` (truthy when Rekordbox wrote the `.EXT` ANLZ file — ~3764× faster than a per-track `db.get_anlz_path()` scan). `has_beats` is `bool(BPM > 0)` — Rekordbox stores `BPM = 0` (or `None`) on tracks that were imported but never analyzed; non-zero BPM means a beat grid exists. The web UI exposes both via the `✨ Phrase only` and `♪ Beat grid only` toggles next to search. Both filters compose in `filteredTracks()` so "Phrase only + Beat grid only" = fully-analyzed tracks; either alone surfaces what's still missing.

- **`/api/health` SSE**: Streams one JSON event per track (TrackHealthReport), then `{"done":true,"summary":{...}}`. Accepts `?playlist_id=N` for incremental rescans and `?limit=N` (1–10000) to cap the per-track loop — `?limit` is used by the `autocue-qa` Playwright smoke suite to bound runs regardless of library size. Per-track exceptions yield `{"score":0,"fix_tier":"none","issues":[{"code":"INTERNAL_ERROR",...}]}` — one bad row never aborts the scan.

- **Multi-select backup delete**: `DELETE /api/backups/{filename}` removes one backup; path traversal is blocked (only bare filenames accepted). The UI uses `_populateChecklist()`, `_updateSelectionCount()`, and `_checkedBackups()` helpers; `deleteCheckedBackups()` calls DELETE once per selected filename and shows a consolidated toast.

## Performance v1 PRD additions

- **`GET /api/tracks` snapshot fast path** (TASK-021 + TASK-022): when the request matches the
  default-sort full-library profile (`sort_by=title`, `sort_order=asc`, `playlist_id` may be
  set), the handler short-circuits the SQL pipeline by serving
  `app.state.tracks_snapshot.payload` directly. The snapshot is keyed by `master.db` mtime;
  ETag-style `If-None-Match` (TASK-023) returns `304` on match. Write-through builds the
  snapshot lazily on the first qualifying request and persists a gzipped JSON copy to
  `CacheStore.tracks_snapshot`. `serve/deps.py:lifespan` hydrates the in-memory snapshot from
  CacheStore on startup if the master.db mtime still matches — so the first request after
  `autocue serve` skips the SQL pipeline entirely. `tracks_snapshot_lock` (a `threading.Lock`)
  serialises mutations.

- **`GET /api/tracks` NDJSON streaming** (TASK-025): clients that send
  `Accept: application/x-ndjson` get a `StreamingResponse` with one JSON object per line
  (no enclosing array). JSON-array path is the default for back-compat with autocue-qa
  Playwright + any existing callers. ETag + `X-Total-Count` headers are preserved on the
  NDJSON path. Offset/limit applied identically.

- **Snapshot invalidation middleware** (TASK-026): `serve/app.py` registers an HTTP middleware
  `_invalidate_snapshot_on_mutation` that calls `_invalidate_tracks_snapshot(request.app)`
  after any 2xx `POST` / `PUT` / `DELETE` to `/api/*`. Centralised invalidation means future
  mutating endpoints inherit the hook automatically — no per-handler helper calls.
  `CacheStore.invalidate_all()` is called only by `/api/restore` (TASK-017); the master.db
  mtime check inside the handler causes the on-disk snapshot to self-invalidate after
  ordinary mutations.

- **Flagged-parallel SSE pattern** (TASKs 002–007): each of `/api/generate-apply-stream`,
  `/api/health`, `/api/classify`, `/api/auto-tag` (+ `/auto-tag/discogs`),
  `/api/enrich-comments/stream`, and the internal similarity index build now has a
  pool-driven branch behind its own `AUTOCUE_PARALLEL_*` env flag. Shape: pool workers do
  read/compute; the SSE generator loop is the single writer. Completion-order events;
  bounded in-flight (`2 * pool_size()` for generate-apply via `_wait_any`); per-track
  exception isolation. All flags default-off until TASK-008 verification.

- **`GET /api/perf/recent`** (TASK-045): dev-only — returns `404` when `AUTOCUE_PERF` env is
  unset. Otherwise returns `{spans: [{name, duration_ms, start_ts}, ...], stats: {name:
  {count, p50, p95, p99}}}` from the in-process ring buffer. `?limit=` clamped to `[1, 1000]`.
  Endpoint not surfaced in the OpenAPI schema when disabled.

- **`GET /api/warmup`** (TASK-028): returns `{step, done, total, finished_at}` from
  `app.state.warmup_progress` (under `warmup_lock`). UI polls every 2s while the sidecar
  cache hydrates; the response with `step === 'done'` (and a `finished_at` ISO string) is
  the signal to stop polling and hide the `#status-warmup` chip. Returns `step: 'unknown'`
  when the lifespan never initialised the pipeline (test contexts, DB unavailable) — never
  raises.

- **`autocue serve --reset-cache`** (TASK-020): operator escape hatch — removes only
  `autocue_cache.sqlite` + `-wal` + `-shm` before starting. Never `master.db`. No-op when
  the cache file is absent. Implementation: `autocue/cache_reset.py`.

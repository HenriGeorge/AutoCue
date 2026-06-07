# PRD — AutoCue Performance v1

**Version:** 1.0
**Owner:** Henri George
**Date:** 2026-06-07
**Status:** Design — ready for breakdown

---

## 1. Overview & objectives

AutoCue's per-track analysis loops (ANLZ reads, classification, energy, similarity, generate-apply, health) currently run serially. On a 3,636-track library, "Compute phrase cues" takes minutes; on a 10k-track library, multi-track operations are unusably slow. The web UI also renders every track card up-front, so scroll/filter/sort degrade as the library grows.

**Goal:** make AutoCue feel responsive at a 10,000-track library size on local server mode, without changing user behavior or breaking the Rekordbox DB write contract.

**Out of scope (Non-goals):**
- GitHub Pages static-mode performance work.
- Process-pool / multiprocessing (deferred to v2 — thread pool covers the I/O-bound 80%).
- Build step / bundler for `docs/index.html` (single-HTML constraint preserved).
- Any change to Rekordbox `master.db` write semantics (backup-before-write + Rekordbox-closed guard stay).
- Distributed compute, remote workers, cloud queues.

## 2. Success criteria (performance budgets)

Measured on a 10,000-track sandbox library on M2-class hardware, warm OS file cache, `autocue serve --no-browser`.

| Endpoint / surface | v0 (today) | v1 target | Verification |
|--|--|--|--|
| `/api/tracks` first load p95 | unbounded | ≤ 800 ms | `tests/perf/test_tracks_endpoint.py` |
| `/api/tracks` warm p95 | unbounded | ≤ 200 ms | same |
| `/api/generate-apply-stream` sustained | ~5 tracks/sec | **≥ 50 tracks/sec** | streamed event timing |
| `/api/health` SSE sustained | ~10 tracks/sec | **≥ 80 tracks/sec** | streamed event timing |
| `/api/classify` cold | ~5 tracks/sec | ≥ 30 tracks/sec | streamed event timing |
| `/api/classify` warm | n/a (recompute) | ≥ 500 tracks/sec | sidecar cache hit |
| `autocue serve` startup → first usable UI | ~3 s | < 1.5 s | wall-clock + UI ready event |
| Library scroll fps | ~20 fps @ 10k | **60 fps @ 10k** | DevTools perf trace |
| Filter keystroke → repaint | ~250 ms @ 10k | < 50 ms | DevTools perf trace |

Budgets enforced by a new `tests/perf/` suite (gated, opt-in via `RUN_PERF=1`) and the existing `autocue-qa` Playwright harness.

## 3. Target audience

Working DJs running `autocue serve` locally against a 1k–10k-track Rekordbox library. They run multi-track operations (generate-apply, health scan, auto-tag, classify) over the full library or large playlist subsets. Expectation: bulk ops in tens of seconds, not minutes; UI stays smooth while ops run.

## 4. Core features (functional requirements)

### 4.1 Thread-pooled analysis loops

**TASK-001** — Introduce a shared `autocue.analysis.concurrency.PoolExecutor` (bounded `ThreadPoolExecutor`, default size `min(8, cpu_count())`, configurable via `AUTOCUE_POOL_SIZE` env var).

**TASK-002** — Refactor `/api/generate-apply-stream` (`serve/routes.py`) to compute cues for N tracks in parallel via the pool; a single writer thread commits to `DjmdCue` to preserve SQLite single-writer semantics. SSE events stream in completion order.

**TASK-003** — Refactor `/api/health` SSE (`analysis/quality.py:check_library_health` generator) to fan out per-track checks through the pool; preserve per-track exception isolation (one bad row never aborts the scan).

**TASK-004** — Refactor `/api/classify` SSE to fan out classification through the pool, cache-first (see 4.2). Cold tracks compute in parallel; warm tracks return immediately.

**TASK-005** — Refactor `/api/auto-tag` (multi-type + Discogs SSE variants) to fan out detector evaluation in parallel; `DjmdMyTag` / `DjmdSongMyTag` writes happen sequentially on a single writer thread.

**TASK-006** — Refactor `/api/enrich-comments/stream` to fan out string building in parallel; `DjmdContent.Commnt` writes stay sequential.

**TASK-007** — Refactor `similar.py` index build (`_index_track` loop) to fan out per-track feature extraction in parallel. Index dict assembly stays single-threaded behind `_INDEX_LOCK`.

**TASK-008** — Verify pyrekordbox's `Rekordbox6Database.read_anlz_file()` is thread-safe for concurrent reads. If not, allocate a thread-local read connection or serialize ANLZ reads behind a lock.

**TASK-009** — Add `tests/test_concurrency.py`: pool size respected, exception isolation, ordering, no resource leaks across runs, no DB write race against single-writer rule.

### 4.2 Persistent sidecar cache

**TASK-010** — Create `autocue/cache.py` exposing `CacheStore` — a SQLite database stored at `<rekordbox_dir>/autocue_cache.sqlite` (alongside `master.db`). Filename `autocue_cache.sqlite`; relies on Rekordbox ignoring unknown files.

**TASK-011** — Cache schema:
- `meta(key TEXT PRIMARY KEY, value TEXT)` — schema_version, created_at, master_db_mtime, autocue_version.
- `energy_curve(content_id INTEGER PRIMARY KEY, anlz_mtime REAL, n_points INTEGER, curve BLOB)` — packed float32.
- `classification(content_id INTEGER PRIMARY KEY, anlz_mtime REAL, primary_cat TEXT, scores_json TEXT, bpm REAL, energy_mean REAL)`.
- `similarity_vector(content_id INTEGER PRIMARY KEY, anlz_mtime REAL, vector BLOB)` — packed 6×float32.
- `mixability(content_id INTEGER PRIMARY KEY, anlz_mtime REAL, score REAL, components_json TEXT)`.
- `tracks_snapshot(master_db_mtime REAL PRIMARY KEY, payload BLOB)` — gzipped `TrackItem` JSON.

**TASK-012** — Cache invalidation by `anlz_mtime`: reader compares against current ANLZ file mtime; mismatch → recompute + replace row. ANLZ-missing rows stored with `anlz_mtime = -1` sentinel; not retried until file appears.

**TASK-013** — Wire energy-curve cache: `analysis/energy.py:get_energy_curve` checks `CacheStore` first; stores on miss. In-process `_cache` becomes a thin L1 LRU.

**TASK-014** — Wire classification cache: `analysis/classify.py:get_classification` checks `CacheStore` first. Existing `_class_cache` LRU stays as L1.

**TASK-015** — Wire similarity-vector cache: `analysis/similar.py:_index_track` checks `CacheStore` for the 6-dim feature vector. Cold-start index build drops ~30s → ~2s on warm cache.

**TASK-016** — Wire mixability cache: `analysis/score.py:get_mixability` checks `CacheStore`. Existing `_mixability_cache` stays as L1.

**TASK-017** — Cache reset hook: `/api/restore` (currently calls `similar.clear_index()` + clears in-memory analysis caches) also calls `CacheStore.invalidate_all()`. Cache file recreated empty.

**TASK-018** — `CacheStore.warm_up(db, content_ids, pool)` — populate missing rows in parallel via the pool. Called by startup pre-warm (4.4).

**TASK-019** — Document cache path + invariant in `CLAUDE.md`.

**TASK-020** — `autocue serve --reset-cache` CLI flag deletes the sidecar before starting.

### 4.3 `/api/tracks` fast path

**TASK-021** — Snapshot in-memory: on first request, build the full `TrackItem` list once, store in `app.state.tracks_snapshot` with the `master.db` mtime that produced it. Subsequent requests return the snapshot directly if mtime unchanged.

**TASK-022** — Persist snapshot to `CacheStore.tracks_snapshot` (gzipped JSON, single row). On `autocue serve` startup, hydrate `app.state.tracks_snapshot` from disk if `master.db` mtime matches; otherwise rebuild.

**TASK-023** — Add `ETag` header derived from `master.db` mtime. Client revalidates with `If-None-Match`; server returns 304 on match. Reduces bytes on UI tab switches.

**TASK-024** — Re-evaluate the SQL pattern: history (`DjmdSongHistory`) + my-tags (`DjmdSongMyTag`) joins. Confirm whether fetch-all-then-filter still wins at 10k (per CLAUDE.md it does at 3k); if not, switch to indexed lookups. Document the breakpoint.

**TASK-025** — Stream the response: switch `/api/tracks` from JSON list to NDJSON streaming when `Accept: application/x-ndjson` is sent, so UI can render rows as they arrive. JSON list path remains default for `autocue-qa` Playwright back-compat.

**TASK-026** — Snapshot invalidation: `/api/apply`, `/api/restore`, `/api/auto-tag`, `/api/enrich-comments`, `/api/color-tracks`, `/api/playlists` (create) — any DB-mutating endpoint sets `app.state.tracks_snapshot = None`.

### 4.4 Startup pre-warm pipeline

**TASK-027** — Extend `serve/deps.py:_prewarm_index` daemon thread into a multi-step pipeline:
  1. Build `/api/tracks` snapshot (4.3).
  2. Hydrate `CacheStore` rows for any track whose `anlz_mtime` doesn't match (background, low priority).
  3. Build similarity index from cached feature vectors.
  4. Emit `app.state.warmup_progress = {step, done, total}` for UI display.

**TASK-028** — Add `GET /api/warmup` endpoint returning `{step, done, total, finished_at}`.

**TASK-029** — UI badge in `#app-status`: when warm-up running, show "Indexing… N/M tracks"; hide when finished. Driven by polling `/api/warmup` every 2s while `step !== "done"`.

**TASK-030** — Pre-warm is interruptible: server shutdown cleanly cancels via a shared `threading.Event`. No partial cache rows committed.

### 4.5 Frontend: virtualized track list

**TASK-031** — Introduce a virtualization helper inside `docs/index.html` (vanilla JS, no build step). Module-level IIFE `Virtualizer = (() => { ... })()`. Tracks viewport scroll position; renders only viewport + 5-card buffer above and below.

**TASK-032** — Recycle DOM nodes: maintain a pool of ~40 card DOM nodes. As user scrolls, nodes leave the buffer at top → reused at bottom (and vice versa). `_cardMap` becomes `_cardPool` + `_visibleIndex → DOM node` mapping.

**TASK-033** — Fixed card height for virtualization math (current cards are uniform). Document constraint in `CLAUDE.md`; if a future feature needs variable heights, switch to measured-height virtualization.

**TASK-034** — `filteredTracks()` returns indices into `parsedTracks`, not new card objects. Virtualizer renders by index, so filter recompute = O(library) predicate pass, O(viewport) DOM update.

**TASK-035** — Preserve existing UX: `_cardMap` smart-diffing fingerprint, FLIP reorder animations (skip on rows that scrolled out of viewport — only animate true reorders), `_sparkObserver`, `_mixObserver`, `_enterObserver` IntersectionObservers continue to observe only visible cards.

**TASK-036** — Search/filter debounce: 80 ms `requestIdleCallback` between keystroke and predicate recompute. Cancels prior pending recompute on each keystroke.

**TASK-037** — Sticky filter bar + sticky footer action bar layout unchanged. Virtualization happens inside `#track-list` only.

**TASK-038** — Vitest coverage in `tests/web/virtualization.test.js`: visible-window correctness on scroll, recycle-pool no-leak, filter recompute updates index list, no observer leaks.

### 4.6 Apply pipeline (producer/consumer)

**TASK-039** — In `serve/routes.py:_generate_apply_stream`, split the per-track loop into a thread-pool **compute stage** (read ANLZ, generate cues — parallel) and a **write stage** (single writer thread; per-track commit preserved).

**TASK-040** — Compute stage produces `(content_id, cues, error?)` tuples through `queue.Queue(maxsize=2 * pool_size)`. Writer thread drains the queue, opens a DB transaction per track, writes, commits, emits SSE event.

**TASK-041** — Cancellation: SSE client disconnect (`request.is_disconnected()`) signals a `threading.Event` both stages observe. Compute drains in-flight futures; writer flushes current track then exits.

**TASK-042** — Order: SSE events emit in compute-completion order. UI already handles arbitrary `content_id` order. `processed` counter monotonic.

**TASK-043** — Error isolation: per-track exception in compute forwarded as `(content_id, None, error)`; writer emits error event and continues. Mirrors existing contract.

### 4.7 Backend performance instrumentation

**TASK-044** — `autocue/perf.py` — context-manager `perf_span(name)` recording wall-clock and emitting to a ring buffer.

**TASK-045** — `GET /api/perf/recent` returns last 100 spans (handler name, p50/p95/p99 latency, count). Dev-only; gated by `AUTOCUE_PERF=1` env var.

**TASK-046** — Add timing spans to all SSE endpoints in 4.1. Spans sampled (1 in 10) to keep overhead < 1%.

**TASK-047** — `tests/perf/` suite: bench `/api/tracks`, generate-apply, health, classify on a synthetic 10k-track sandbox DB. Gated by `RUN_PERF=1`. Fails CI if a budget is exceeded by > 20%.

**TASK-048** — Add `pytest -m perf` target for local runs with timing summaries.

### 4.8 Frontend instrumentation

**TASK-049** — `docs/index.html` adds a `_perf` helper: `_perf.mark(name)`, `_perf.measure(name, startMark)`. Logs to console under `[AutoCue Perf]` when `localStorage.getItem('autocue_perf') === '1'`.

**TASK-050** — Add perf marks around: library load, filter recompute, sort, virtualized render, mini-player audio load. Vitest test stubs `performance.mark` / `performance.measure` and verifies marks fire.

## 5. Key user flows (where the wins land)

### Flow A — "Compute phrase cues" on a 3,636-track library
1. User clicks "Preview cues" or "Apply" with the entire library selected.
2. UI calls `POST /api/generate-apply-stream`.
3. **v0:** serial loop, ~12 min.
4. **v1:** ThreadPool (8 workers) computes cues; single writer streams events. **Target: ~70 s (≥ 50 tracks/sec).**
5. UI updates the progress bar smoothly; no blocking renders.

### Flow B — Open `autocue serve` for the first time today
1. User runs `autocue serve --no-browser`; opens `localhost:7432`.
2. UI loads, hits `/api/tracks`. **v0:** ~6 s blank; **v1:** ≤ 800 ms (snapshot rebuild from sidecar cache + parallel hydration).
3. UI shows track list immediately; `#app-status` shows "Indexing 1,240/10,000" briefly during background pre-warm.
4. User scrolls library: 60 fps; typing in search filter: < 50 ms repaint.

### Flow C — Library Health scan on a 10k-track library
1. User clicks "Scan library" in Cue Tools.
2. **v0:** ~16 min serial.
3. **v1:** ThreadPool (8 workers) scans in parallel; SSE events stream. **Target: ~2 min (≥ 80 tracks/sec).**

### Flow D — Auto-tag the library
1. User clicks "Apply auto-tags" with the full library.
2. **v0:** ~25 min on 10k.
3. **v1:** parallel classification (warm cache: ≥ 500 tracks/sec); single-writer tag write. **Target: ≤ 5 min on 10k.**

### Flow E — Track edit triggers cache invalidation
1. User runs `/api/apply`; new cues are written.
2. Writer endpoint sets `app.state.tracks_snapshot = None` (TASK-026).
3. Cache rows for `energy_curve` / `classification` / `similarity_vector` are NOT invalidated (cues don't change ANLZ). `mixability` row IS invalidated for the affected `content_id` (intro/outro detection depends on cues).
4. Next read rebuilds the snapshot lazily.

## 6. Technical stack

- **Concurrency:** stdlib `concurrent.futures.ThreadPoolExecutor` + `queue.Queue`. No new deps.
- **Persistent cache:** stdlib `sqlite3` (plain — no SQLCipher; no Rekordbox secrets in it). `struct` packing for float arrays.
- **Frontend virtualization:** pure JS, no library, no build step.
- **Instrumentation:** stdlib `time.perf_counter()`; browser `performance.mark` / `performance.measure`.
- **Perf tests:** `pytest` markers, gated env vars.

**No new runtime dependencies.** `sqlite3` stdlib; `numpy` already transitive.

## 7. Data model — sidecar cache schema

```
File: <rekordbox_dir>/autocue_cache.sqlite

CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE energy_curve (
  content_id INTEGER PRIMARY KEY,
  anlz_mtime REAL NOT NULL,    -- -1 = ANLZ missing, skip retry
  n_points   INTEGER NOT NULL,
  curve      BLOB NOT NULL     -- struct.pack(f'{n_points}f', *curve)
);

CREATE TABLE classification (
  content_id   INTEGER PRIMARY KEY,
  anlz_mtime   REAL NOT NULL,
  primary_cat  TEXT NOT NULL,
  scores_json  TEXT NOT NULL,
  bpm          REAL,
  energy_mean  REAL
);

CREATE TABLE similarity_vector (
  content_id INTEGER PRIMARY KEY,
  anlz_mtime REAL NOT NULL,
  vector     BLOB NOT NULL     -- struct.pack('6f', *vector)
);

CREATE TABLE mixability (
  content_id      INTEGER PRIMARY KEY,
  anlz_mtime      REAL NOT NULL,
  score           REAL NOT NULL,
  components_json TEXT NOT NULL
);

CREATE TABLE tracks_snapshot (
  master_db_mtime REAL PRIMARY KEY,
  payload         BLOB NOT NULL    -- gzip(JSON list of TrackItem)
);

CREATE INDEX idx_energy_mtime ON energy_curve(anlz_mtime);
CREATE INDEX idx_class_mtime  ON classification(anlz_mtime);
```

Schema versioning via `meta.schema_version`. Bump → drop & recreate all tables on next read. No migrations in v1 (cache regenerable).

## 8. UI design principles

- **Single-HTML, no build step** — every new helper is a module-level IIFE in `docs/index.html`. No imports, no transpile.
- **Virtualization is invisible** — same card design, same hover states, same selection behavior. Only off-screen DOM is missing.
- **Progress always visible** — every multi-track op shows a progress bar with `N / total` and Cancel (existing "Computing phrase cues" pattern).
- **Pre-warm is non-blocking** — UI usable immediately; `#app-status` shows unobtrusive "Indexing…" badge.
- **No regressions** — Vitest suite (191 → ~210 tests) catches filter/sort/search/multi-select regressions before merge.

## 9. Security considerations

- **Sidecar cache file** (`autocue_cache.sqlite`) is plain SQLite. Contents: numeric energy curves, classification labels, similarity vectors, mixability scores, gzipped `TrackItem` JSON (titles, artists, BPM, key, paths). **No audio, no credentials, no Discogs tokens.** File permissions inherit from the Rekordbox directory (user-only on macOS/Windows defaults).
- **Cache poisoning** — `anlz_mtime` check is mandatory before reading any row; mismatch → recompute. A tampered cache at worst causes wrong tags/cues until next ANLZ mtime change; cannot write to `master.db` directly.
- **CORS unchanged** — existing localhost-only whitelist remains; new `/api/warmup` and `/api/perf/recent` inherit it.
- **`/api/perf/recent`** gated by `AUTOCUE_PERF=1` env var. Off by default.
- **Single-writer rule** for `master.db` preserved on every multi-track endpoint. `tests/test_concurrency.py` verifies no parallel write paths exist.
- **pyrekordbox thread-safety** — TASK-008 verifies before relying on it; fallback to thread-local DB connections if not safe.

## 10. Development phases

### Phase 1 — Thread pool + frontend virtualization (week 1–2)
TASKs 001–009, 031–038, 049–050. No schema change. Captures ~70% of wins. Ships as `v1.0-alpha`.

### Phase 2 — Persistent sidecar cache (week 2–3)
TASKs 010–020. New file `<rekordbox_dir>/autocue_cache.sqlite`. Targets warm-load wins. Ships as `v1.0-beta`.

### Phase 3 — `/api/tracks` fast path + pre-warm (week 3–4)
TASKs 021–030. Snapshot, ETag, NDJSON streaming, warm-up pipeline. Ships as `v1.0`.

### Phase 4 — Instrumentation + perf CI (week 4)
TASKs 044–048. Perf suite, `RUN_PERF=1` gate, dashboards. Ships as `v1.0`.

### Phase 5 — Apply producer/consumer (folded into Phase 1 if scope allows; else week 5)
TASKs 039–043.

## 11. Assumptions

- **A1** — pyrekordbox `Rekordbox6Database.read_anlz_file()` is safe for concurrent reads. **Verify in TASK-008**; fallback: thread-local DB connections.
- **A2** — ANLZ file mtime is reliable: Rekordbox always rewrites `.EXT`/`.DAT` on re-analyze. **Verify** by running "Analyze track" on a sample and checking mtime.
- **A3** — Users tolerate one-time warm-up after first `autocue serve` of a session. Mitigated by visible badge + UI usable during warm-up.
- **A4** — Rekordbox directory is writable by `autocue serve` (same assumption the backup path already makes). If write fails: fall back to `~/.autocue/cache.sqlite` with UI warning.
- **A5** — Rekordbox does not delete/modify unknown files in its dir. **Verify** by leaving a sentinel + triggering a Rekordbox backup. If broken: fall back to `~/.autocue/cache.sqlite`.
- **A6** — `min(8, cpu_count())` is a sensible default pool size; user override via `AUTOCUE_POOL_SIZE`. Larger pools yield diminishing returns once disk I/O saturates.
- **A7** — Library scroll perf is dominated by DOM count, not paint cost. **Validate via DevTools trace before TASK-031**; if paint dominates, also simplify card CSS.
- **A8** — 10k tracks is the design upper bound; 50k+ may need server-side pagination (out of scope for v1).

## 12. Dependencies

- **None new.** Stdlib `concurrent.futures`, `queue`, `sqlite3`, `struct`, `gzip`, `time.perf_counter`.
- Existing: `pyrekordbox`, `fastapi`, `uvicorn`, `psutil`, `numpy` (transitive).
- Test deps unchanged: `pytest`, `httpx`, `hypothesis`, `vitest`, `jsdom`.

## 13. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|--|--|--|--|
| pyrekordbox `read_anlz_file` not thread-safe | Medium | High | TASK-008 verifies; fallback to thread-local DB |
| Rekordbox deletes `autocue_cache.sqlite` during backup | Low | Medium | Verify (A5); fallback to `~/.autocue/` |
| Cache grows unbounded on stale libraries | Low | Low | Cap at 100k rows; LRU eviction on overflow (deferred) |
| Virtualization breaks edge case (multi-select drag, keyboard nav) | Medium | Medium | Vitest covers all interactions; manual `autocue-qa` Playwright pass before each phase |
| Pool size starves UI thread | Low | Low | Default `min(8, cpu_count())`; `AUTOCUE_POOL_SIZE` env var |
| Perf CI flakes on shared runners | Medium | Low | Budgets allow 20% headroom; perf tests gated by `RUN_PERF=1` so they don't block PRs by default |

## 14. Acceptance checklist (v1.0 ship gate)

- [ ] All §2 budgets met on the 10k sandbox library (perf suite green).
- [ ] `tests/test_concurrency.py` green; no flaky tests in 10 consecutive runs.
- [ ] `tests/web/virtualization.test.js` green.
- [ ] Existing `tests/` (819 Python + 191 Vitest) all green; no regressions.
- [ ] `autocue-qa` Playwright smoke green against a 10k sandbox.
- [ ] `CLAUDE.md` updated: sidecar cache path, `AUTOCUE_POOL_SIZE` env var, virtualization invariants.
- [ ] `docs/FEATURES.md` updated: pre-warm UX, progress indicators.
- [ ] No new runtime dependencies in `pyproject.toml`.

---

**Implementation order:** TASK-001 → 002 → 008 (verify) → 009 → 003–007 → 031–038 → 010–020 → 021–030 → 039–043 → 044–050.

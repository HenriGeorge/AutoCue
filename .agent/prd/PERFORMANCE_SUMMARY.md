# Performance v1 — Summary

## What

Make AutoCue feel responsive at a 10,000-track library size on local server mode. Today's serial per-track loops (ANLZ reads, classification, energy, similarity, generate-apply, health) take minutes; the frontend renders every track card even when only a viewport's worth is visible.

## Main features

1. **Thread-pool every analysis loop** — `/api/generate-apply-stream`, `/api/health`, `/api/classify`, `/api/auto-tag`, `/api/enrich-comments`, similarity index build. Single writer thread preserves SQLite single-writer rule. Target: 5–10× on every multi-track endpoint.

2. **Persistent sidecar cache** — `<rekordbox_dir>/autocue_cache.sqlite` memoizes energy curves, classification, similarity vectors, mixability, and the `/api/tracks` snapshot. Invalidates per-track by ANLZ mtime; whole-cache by `master.db` mtime. Cold-start similarity build drops from ~30s to ~2s.

3. **`/api/tracks` fast path** — in-memory snapshot keyed by `master.db` mtime; ETag/304 revalidation; optional NDJSON streaming. Warm p95 ≤ 200 ms on a 10k library.

4. **Startup pre-warm** — background pipeline populates the snapshot + sidecar cache after `autocue serve` boot. UI usable immediately; `#app-status` shows "Indexing… N/M" badge.

5. **Virtualized track list** — pure JS in `docs/index.html` (no build step). ~40-node DOM pool, recycled on scroll. `filteredTracks()` returns indices; filter recompute = O(library) predicate, O(viewport) DOM. Targets 60 fps scroll + <50 ms filter repaint on 10k tracks.

6. **Apply producer/consumer** — generate-apply splits into parallel compute stage + single writer; per-track commit semantics preserved; client-disconnect cancellation observed by both stages.

7. **Performance instrumentation** — `autocue/perf.py` ring buffer + `/api/perf/recent` (dev-only). `tests/perf/` suite gated by `RUN_PERF=1` enforces budgets.

## Key user flows

- **A — Compute phrase cues** (3,636 tracks): ~12 min → ~70 s.
- **B — First load of `autocue serve`**: ~6 s blank → ≤ 800 ms with snapshot.
- **C — Library Health scan** (10k): ~16 min → ~2 min.
- **D — Auto-tag** (10k): ~25 min → ≤ 5 min.
- **E — Cache invalidation on edits**: `/api/apply` clears the tracks snapshot lazily; affected mixability rows invalidated; energy/classification/similarity preserved (ANLZ-derived).

## Performance budgets (10k tracks, M2-class)

- `/api/tracks` first load p95 ≤ 800 ms; warm p95 ≤ 200 ms
- `/api/generate-apply-stream` ≥ 50 tracks/sec
- `/api/health` ≥ 80 tracks/sec
- `/api/classify` cold ≥ 30 tracks/sec; warm ≥ 500 tracks/sec
- `autocue serve` startup → first usable UI < 1.5 s
- Library scroll 60 fps; filter keystroke → repaint < 50 ms

## Key requirements

- **No new runtime dependencies.** Stdlib only: `concurrent.futures`, `queue`, `sqlite3`, `struct`, `gzip`.
- **Single-HTML, no build step** for `docs/index.html` is preserved.
- **Single-writer rule** for Rekordbox `master.db` is preserved on every multi-track endpoint.
- **Pyrekordbox thread-safety** is verified before fanout (TASK-008); fallback is thread-local DB connections.
- **Sidecar cache** lives at `<rekordbox_dir>/autocue_cache.sqlite`; contains no audio, no credentials, no Discogs tokens.
- **Budgets enforced** by a new `tests/perf/` suite gated by `RUN_PERF=1`; perf CI fails on >20% regression.
- **Process pool / multiprocessing deferred** to v2 — thread pool covers the I/O-bound 80%.
- **GitHub Pages static mode** is explicitly out of scope.

## Phases

- **Phase 1** — Thread pool + frontend virtualization (TASKs 001–009, 031–038, 049–050). `v1.0-alpha`.
- **Phase 2** — Sidecar cache (TASKs 010–020). `v1.0-beta`.
- **Phase 3** — `/api/tracks` fast path + pre-warm (TASKs 021–030). `v1.0`.
- **Phase 4** — Perf instrumentation + CI (TASKs 044–048). `v1.0`.
- **Phase 5** — Apply producer/consumer (TASKs 039–043). Folded into Phase 1 if scope allows.

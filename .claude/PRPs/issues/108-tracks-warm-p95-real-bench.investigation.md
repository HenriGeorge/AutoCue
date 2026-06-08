# Issue #108 — `/api/tracks` 200ms warm p95 budget unenforced

## Problem

PRD §6 sets two budgets for `/api/tracks`:
- Warm p95 ≤ 200 ms on a 10 k library.
- Cold p95 ≤ 800 ms on the same library.

`tests/perf/test_tracks_snapshot_perf.py` claims to validate the warm path, but it:
- replaces `app.state.db` with a `MagicMock`,
- stuffs the snapshot dict directly with already-built `TrackItem` Pydantic objects,
- only exercises the slice + Starlette JSON encode.

The result is a benchmark of "list slice + ORJSON" rather than of `/api/tracks` against a 10 k Rekordbox-shaped database. The 50 ms ceiling it asserts is correct for that toy path but tells us nothing about the PRD budget. Cold-path performance (800 ms ceiling) is entirely unmeasured.

`.agent/prd/PERFORMANCE_NOTES.md:3-22` acknowledges this gap and defers the real benchmark "until a synthetic Rekordbox sandbox DB" exists.

## Root cause

`tests/perf/test_tracks_snapshot_perf.py:49-65` — `_client_with_snapshot()` hands the route a `MagicMock` DB and pre-built items, so the SQL path, the 5 prefetch queries (history / songhistory / mytag / color / hot-cue counts), and the `_to_item` per-row work never execute.

There is no fixture under `tests/fixtures/` that builds a pyrekordbox-shaped library at realistic scale.

## Proposed solution

Add a synthetic 10 k-track fixture that pyrekordbox can read, and use it to drive a real `TestClient` against the live `/api/tracks` route:

1. **`tests/fixtures/synthetic_rb_db.py`** — builds a plain-SQLite file (no SQLCipher) using `pyrekordbox.db6.tables.Base.metadata.create_all`, populates the NOT NULL columns, and inserts 10 k `DjmdContent` rows joined to 500 artists, 800 albums, 24 keys, 8 colors, 20 genres. Open via `Rekordbox6Database(path=..., unlock=False)`. Writes a stub `masterPlaylists6.xml` so pyrekordbox doesn't log a warning during open.
2. **`tests/perf/test_tracks_warm_p95.py`** — real benchmark:
   - Builds the fixture in a tmp dir, opens two `Rekordbox6Database(unlock=False)` handles (rw + ro), overrides `get_db` / `get_ro_db` Depends, hits `/api/tracks` 50× post-warmup, asserts **warm p95 ≤ 200 ms** (the PRD ceiling).
   - Adds a second test that re-runs after `app.state.tracks_snapshot = None` between every hit, asserts **cold p95 ≤ 800 ms**.
   - Both gated by `@pytest.mark.perf` (existing `RUN_PERF=1` flag).
3. Keep the existing toy `test_snapshot_hit_p95_under_50ms` — it now reads as a micro-benchmark for the snapshot-only fast path, with the real PRD bench alongside it.
4. Update `.agent/prd/PERFORMANCE_NOTES.md` — the deferred "needs synthetic Rekordbox sandbox DB" note is now resolved; record actuals from the fixture.

Reference numbers measured locally during investigation on this fixture:
- Warm p95 (snapshot hit): **~68 ms**.
- Cold p95 (5 iter, fresh SQL pipeline each): **~704 ms**.

Both clear the PRD budgets but the warm path has zero margin against an aggressive reading and the cold path is within 12 % of its ceiling — exactly the regression-guard envelope the PRD intends.

## Affected files

- `tests/fixtures/__init__.py` — new (package marker).
- `tests/fixtures/synthetic_rb_db.py` — new; fixture builder.
- `tests/perf/test_tracks_warm_p95.py` — new; warm + cold benchmark.
- `.agent/prd/PERFORMANCE_NOTES.md` — clear the deferred TASK-024 caveat.

## Risks

- **Pyrekordbox schema drift.** Fixture pins to current `pyrekordbox==0.4.4` column shape. If pyrekordbox adds a new NOT NULL column we'll see an `IntegrityError` on `create_all`. Acceptable — version bumps already need explicit review in this codebase.
- **CI cost.** Both tests are `@pytest.mark.perf` (skipped unless `RUN_PERF=1`), matching the existing perf gate. No default-CI cost.
- **Fixture build cost.** ~400 ms on a modern Mac for 10 k inserts; fits inside the per-test overhead.
- **Measurement variance.** TestClient-in-process is not a wire benchmark. The PRD numbers (200 / 800 ms) target wire-clock; in-process should be tighter. Treat the new asserts as ceilings, not parity claims.

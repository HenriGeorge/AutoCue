# Issue #108 — /api/tracks warm p95 200ms budget is unenforced

## Problem

PERFORMANCE_PRD §6 row 2 sets the warm-path p95 budget for `/api/tracks` at
**≤ 200 ms** on a 10 k-track library and points to
`tests/perf/test_tracks_endpoint.py` as the gate. That file does not exist.

`tests/perf/test_tracks_snapshot_perf.py` (TASK-047) is the only perf test
covering this endpoint, and it caps p95 at **50 ms** — comfortably under the
PRD budget but with no test that actually pins the PRD number. If a future
regression pushes warm p95 from ~10 ms to 150 ms (3× slower, still under 200 ms)
the 50 ms cap fires and the PRD budget never gets re-validated.

## Root cause

`tests/perf/test_tracks_snapshot_perf.py:49-65` measures the right code path —
`TestClient` → FastAPI stack → `response_model=list[TrackItem]` → Pydantic
serialization of 10 k items — but its assertion is a tighter
implementation-detail bound (50 ms), not the contract bound (200 ms). When the
50 ms bound fails it tells you "the snapshot path got slower" without telling
you "the user-visible budget was breached".

The issue report also notes that the cold path (PRD §6 row 1, ≤ 800 ms) is
unmeasured. That's accepted in `.agent/prd/PERFORMANCE_NOTES.md` as deferred
pending a synthetic Rekordbox SQLCipher fixture — building such a fixture is
out of scope for this issue (pyrekordbox keys + schema are non-trivial and
~50 LoC isn't enough to land it honestly).

## Proposed solution

Add `tests/perf/test_tracks_endpoint.py` (the file the PRD points to) with a
single warm-path test that:

1. Builds a 10 k-item `TrackItem` snapshot and hydrates it into `app.state` the
   same way `lifespan` does at startup (TASK-022 path).
2. Mounts the snapshot against a real `create_app()` + `TestClient`.
3. Stubs `_master_db_mtime` to a fixed value so the snapshot is treated as
   warm (this matches production semantics — a hydrated snapshot keyed by
   mtime is exactly the warm path).
4. Issues 50 GETs and asserts **p95 ≤ 200 ms** — the PRD contract number.
5. Includes a regression-guard variant: revert the snapshot to `None` and
   confirm the test would NOT pass against an SQL-path response (we mark the
   regression assertion via a fail-without-fix path inside the same file).

Property-style invariants used (per the agent's test-quality rules):
- For all N in [1, 10 000]: warm p95 of `/api/tracks` ≤ 200 ms.
- For all N: 50 sequential warm requests produce identical bodies (ETag holds).

Update `.agent/prd/PERFORMANCE_NOTES.md` to record that the warm budget is now
enforced; cold budget remains deferred.

Out of scope (explicitly): synthetic SQLCipher fixture, cold p95 enforcement,
startup-to-UI < 1.5 s budget. Those need a separate issue with the fixture
work as a prerequisite.

## Affected files

- `tests/perf/test_tracks_endpoint.py` (new)
- `.agent/prd/PERFORMANCE_NOTES.md` (status note)

## Risks

- Pydantic serialization speed varies by CPU. The 200 ms bound is the PRD
  contract on a developer machine; CI ARM/x86 runners may differ. Gating
  behind `RUN_PERF=1` (existing conftest hook) keeps CI green by default,
  matching the precedent set by TASK-047.
- We do NOT exercise the cold path; if a regression breaks the snapshot
  hydration logic, this test still passes. That's already covered by the
  existing TASK-021/022 unit tests + the existing 50 ms guard.

# Issue #109 — TASK-024 marked passes:true but explicitly deferred

## Problem

`.agent/tasks.performance.json:164-169` lists TASK-024 with `"passes": true`,
but `.agent/prd/PERFORMANCE_NOTES.md:3-22` explicitly marks the task **deferred**:

> deferred — needs synthetic Rekordbox sandbox DB to benchmark properly

The task's acceptance criteria (`.agent/tasks/performance/TASK-024.json:6-11`) require
a benchmark in `tests/perf/test_tracks_sql.py` comparing the current fetch-all-then-filter
path to a SQL IN-filter pushdown at 10k tracks, with timings written to
`PERFORMANCE_NOTES.md`. No such test file exists, no such timings have been recorded,
and no decision has been made — the `passes: true` flag is therefore aspirational, not
factual.

The per-step `pass` flags inside `TASK-024.json` already reflect the truth:
steps 1, 2, 3 are `"pass": false`; step 4 has no `pass` flag (conditional on step 2).
Only the top-level rollup in `tasks.performance.json` is out of sync.

## Root Cause

`.agent/tasks.performance.json:168` — the rollup `"passes": true` was likely flipped
during a Performance v1 sweep without cross-checking the deferral note in
`PERFORMANCE_NOTES.md`. The per-step `pass: false` markers in TASK-024.json were
left correctly, exposing the inconsistency.

## Proposed Solution

**Option 1** (per issue): flip the rollup to `passes: false` until the 10k synthetic
SQLCipher fixture and benchmark land. The issue itself notes "option 1 otherwise"
when the fixture isn't in reach.

Why not option 2: building a 10k-row SQLCipher fixture from scratch (with consistent
DjmdContent + DjmdSongHistory + DjmdSongMyTag + DjmdColor rows, the correct schema
version, the SQLCipher key) is multi-day work — well outside the fixer's ≤50-line
scope. The sandbox-reap path in the QA harness uses the maintainer's real master.db,
not a synthetic one; there's no existing scaffolding to extend.

The fix:

1. `.agent/tasks.performance.json` — flip TASK-024 from `"passes": true` to `"passes": false`.
2. `.agent/prd/PERFORMANCE_NOTES.md` — clarify the deferral block notes the rollup is
   now `passes: false` so the two artifacts agree.
3. Add a regression-style assertion test that scans the JSON and asserts the rollup
   `passes` field matches the AND of per-step `pass` flags for any deferred task —
   prevents this drift from recurring.

## Affected files

- `.agent/tasks.performance.json` (one-line flip, line 168)
- `.agent/prd/PERFORMANCE_NOTES.md` (clarifying sentence)
- `tests/test_task_rollup_consistency.py` (new — regression guard)

## Risks

- Flipping `passes: true → false` may affect any tooling that counts "done" tasks.
  Mitigation: grep for `tasks.performance.json` usage. Only documentation reads it.
- The new test could over-fit. Mitigation: scope it narrowly to "if a task's
  per-step `pass: false` exists AND PERFORMANCE_NOTES.md contains a 'deferred' marker
  for that task id, the rollup must be `passes: false`." Property-style, not value-
  specific.

## Test plan

- Tier A (pytest): new `tests/test_task_rollup_consistency.py` — fails before the flip
  (TASK-024 rollup says true but per-steps say false + notes say deferred), passes after.
- Tier B (vitest): no web change — skipped per touch-log.
- Tier C (e2e): no UI/server change — skipped per touch-log.

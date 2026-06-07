# Performance v1 — Implementation Notes

## TASK-024: /api/tracks SQL pattern at 10k

**Status**: deferred — needs synthetic Rekordbox sandbox DB to benchmark properly.

The current `/api/tracks` SQL pattern (per `CLAUDE.md`) intentionally fetches
all `DjmdHistory`, `DjmdSongHistory`, `DjmdSongMyTag`, and `DjmdColor` rows
without an `IN(row_ids)` filter, then intersects in Python. The CLAUDE.md
documentation says this beats the IN-filter approach at ~3k tracks against
pyrekordbox's SQLCipher. Whether that breakpoint shifts at 10k is unknown
without a real 10k-track sandbox.

**Mitigation in TASK-021/022**: the snapshot fast path skips this SQL path
entirely on warm cache. Cold-start performance (one SQL pipeline per
session per master.db mtime change) is the only path where the breakpoint
matters, and the snapshot persistence (TASK-022) further reduces that to
the very first request after a Rekordbox library re-analyze.

**Recommendation**: revisit when a 10k-track Rekordbox library is
available for measurement. Until then, the cached path makes the breakpoint
question irrelevant for the budgets in §2.

## TASK-008: pyrekordbox thread-safety verification

**Status**: skeleton shipped; awaiting maintainer's real-DB stress run.

The stress test in `tests/test_concurrency.py::test_anlz_read_concurrent`
exercises 16 concurrent threads hammering `db.read_anlz_file()` and
`get_tag()` against a real Rekordbox library. It is gated by
`RUN_ANLZ_STRESS=1` so it does not run in normal CI.

**To verify before TASK-002..007 ship**:

```bash
RUN_ANLZ_STRESS=1 AUTOCUE_DB_PATH=~/Library/Pioneer/rekordbox \
    pytest tests/test_concurrency.py::test_anlz_read_concurrent -v
```

If the test passes, TASK-002..007 (SSE refactors using the shared pool)
can land safely. If it fails, the fallback is a `thread_local_db(db_dir)`
helper in `autocue.analysis.concurrency` that gives each pool worker its
own `Rekordbox6Database` instance — slightly higher memory per worker but
eliminates the thread-safety question.

## Remaining tasks (as of last commit)

See `.agent/tasks.performance.json` — each task has `passes: true|false`.
The `passes: false` tasks group into:

- **TASK-002..007** + **TASK-039..043**: SSE refactors blocked by TASK-008.
- **TASK-024**: SQL pattern eval (this doc covers it; needs sandbox DB
  to benchmark in earnest).
- **TASK-025**: NDJSON streaming response — useful for incremental
  rendering once the virtualizer (TASK-031..038) is in place.
- **TASK-031..038**: frontend virtualization — the largest remaining
  unit; will likely ship as a series of small PRs (virtualizer scaffold,
  recycle pool, height lock, filter indices, observers, debounce,
  layout, tests).

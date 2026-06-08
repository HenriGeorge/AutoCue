# Performance v1 — Implementation Notes

## TASK-024: /api/tracks SQL pattern at 10k

**Status**: deferred — needs synthetic Rekordbox sandbox DB to benchmark properly.
The rollup `passes` flag in `.agent/tasks.performance.json` is `false` to reflect
this; flip back to `true` only after the benchmark below lands with timings recorded
in the "TASK-024 results" section.

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

## TASK-008: pyrekordbox thread-safety verification — ✅ PASSED

**Verified**: 2026-06-07 against the maintainer's real Rekordbox 7 library
(macOS, ~/Library/Pioneer/rekordbox/master.db).

```bash
RUN_ANLZ_STRESS=1 AUTOCUE_DB_PATH=~/Library/Pioneer/rekordbox/master.db \
    pytest tests/test_concurrency.py::test_anlz_read_concurrent -v
# 1 passed in 0.59s
```

The stress test ran 16 concurrent threads × 20 iterations × 50 tracks =
~16,000 `db.read_anlz_file()` + `get_tag()` calls. **Zero exceptions;
parallel tag counts matched the serial reference for every track.**

**Consequence**: the six `AUTOCUE_PARALLEL_*` env flags flip from
default-off to default-on as of this commit:

| Env var | Default | Disable with |
|--|--|--|
| `AUTOCUE_PARALLEL_GENERATE_APPLY` | ON | `=0` |
| `AUTOCUE_PARALLEL_HEALTH` | ON | `=0` |
| `AUTOCUE_PARALLEL_CLASSIFY` | ON | `=0` |
| `AUTOCUE_PARALLEL_AUTO_TAG` | ON | `=0` |
| `AUTOCUE_PARALLEL_ENRICH_COMMENTS` | ON | `=0` |
| `AUTOCUE_PARALLEL_SIMILAR` | ON | `=0` |

The serial paths remain in code as the disable-fallback. The
`thread_local_db()` helper is no longer needed but the design space is
documented here for posterity.

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

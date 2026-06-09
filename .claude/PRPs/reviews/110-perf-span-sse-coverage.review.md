# Issue #110 — Self-review (re-spawn of PR #140)

## Verdict
APPROVE — re-implementation of the closed PR #140 against current main. The earlier branch
became unmergeable after PR #143 (mixability invalidation) and PR #122 (artwork 204)
landed on main; rebasing was abandoned in favor of a fresh re-port that preserves both
follow-ons.

## What changed vs PR #140
- Re-applied the same 7 span pairs (6 SSE endpoints, plus the latent classify NameError
  fix) directly on top of current `routes.py`, not via the stale PR #140 patch (which
  conflicted with PR #143's `written_ids`/`invalidate_mixability` and PR #122's artwork
  204 response).
- Span names unchanged: `<endpoint>.compute` outer + `<endpoint>.write_one` per-track.
- Test file `tests/test_perf_sse_coverage.py` ported verbatim from PR #140 — assertions
  are property-based (`name in span_names`), not value-based.

## Issues Found
None. Same shape as PR #140 minus the merge conflict.

## Verification
- `pytest -x -q` → 1357 passed, 7 skipped, 0 failed.
- `npm test --silent` (vitest) → 579 passed.
- Regression-guard checked: `git stash autocue/serve/routes.py` →
  `pytest tests/test_perf_sse_coverage.py` fails on the first SSE assertion
  (`AssertionError: missing generate_apply.compute; saw: set()`). Confirms each span
  is load-bearing.
- Playwright e2e (leg C) — `per-control-sweep.selector.test.ts` import error fails
  on bare main too; pre-existing infra bug unrelated to TASK-046. Skipped per fixer
  scope rule.

## Notes on the unprotected `__enter__`/`__exit__` pattern
Each SSE generator opens the outer span with `_outer_span.__enter__()` and explicitly
closes it before every `yield` of the final `done` event. If a generator raises mid-stream
without hitting the explicit `__exit__`, the span will not record — same behavior as the
pre-existing `tracks.build` pattern at `routes.py:271`. Acceptable for v1 (the goal of
TASK-046 is timing of the success path); a future refactor could wrap in `try/finally`
once the perf ring buffer adds an "incomplete-span" sentinel.

## Sample rate
Left at the existing default `AUTOCUE_PERF_SAMPLE_RATE=1.0`. PRD's "1-in-10" is
operationally tunable (`AUTOCUE_PERF_SAMPLE_RATE=0.1`); changing the default is out of
scope for this issue.

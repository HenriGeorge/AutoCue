# Issue #107 self-review

## Verdict

approve

## Issues found

None blocking. Observations:

- Diff is larger than the agent's "≤ 50 lines preferred" guideline (~520
  net lines). This is intrinsic to TASK-039/040 — the PRD acceptance
  requires extracting two named functions and rewiring the SSE handler.
  Net behavior is unchanged; the line growth is structural (factoring,
  not added features).
- `_wait_any` is now unused by the route but kept as a top-level symbol.
  Two existing tests still exercise it; removing it would have been a
  drive-by deletion in the same commit, so left in place.

## Verification

- **pytest -x -q (leg A)**: `1358 passed, 7 skipped, 1 warning in 15.58s`.
- **npm test --silent (leg B)**: `30 files, 579 tests passed`. No web
  surfaces were touched; this is a no-op regression check.
- **Playwright e2e (leg C)**: fails for the SAME reason on a clean
  `git stash` of `main` — `per-control-sweep.selector.test.ts` imports
  another spec file in a way playwright forbids. Pre-existing environmental
  issue, not introduced by this change. The webServer itself starts
  cleanly under the new producer/consumer code path.
- **Focused tests**: `tests/test_generate_apply_bounded.py` — all 12
  tests pass (was 4 before, now covers stages in isolation +
  backpressure invariant).

## Test quality audit

Per the agent's "would tests fail if fix reverted?" check:

- `test_compute_stage_pushes_sentinel_*` — would fail to import on
  revert (no `_compute_stage` symbol).
- `test_writer_stage_*` — would fail to import on revert.
- `test_bounded_queue_maxsize_invariant_under_slow_writer` — pushes
  30 results through a `maxsize=4` queue with a deliberately slow
  consumer and a sampling thread. The PRE-existing implementation used
  a `dict` not a queue, so this test could not even have been written
  against it. Property: `max(observed_qsize) ≤ maxsize` over an entire
  run. Passes by construction with the new code; would not have applied
  to the dict-based implementation at all.
- `test_writer_stage_per_track_exception_does_not_abort_batch` —
  encodes TASK-039 acceptance #4 as a regression guard. Forces a
  RuntimeError on the first write, asserts the second still applies.

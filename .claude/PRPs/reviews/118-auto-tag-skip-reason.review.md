# Self-review — Issue #118

## Verdict: approve

## Diff scope
- `autocue/analysis/auto_tag.py`: +41 lines (skip_reasons dict + classifier
  helper + two one-line counter bumps).
- `autocue/serve/schemas.py`: +4 lines (response field with default `{}`).
- `tests/test_auto_tag.py`: +97 lines (5 new tests covering each bucket,
  the boundary at `MIN_SCORE`, and the sum invariant).

Total: ≤ 50 LoC code change (excluding tests), as required by the issue's
`impact:small` label.

## Issues found
**None blocking.**

Notes considered and deemed acceptable:
- `_classify_skip` runs `get_energy_curve()` a second time per skipped track
  (the detector already called it once). Both calls are L1-cached on the
  `(content.ID, 50)` key so the second call is O(1) — no real overhead.
- The classifier is called on the writer thread (after the parallel pool
  drains), which preserves the single-writer rule for `master.db`.
- `get_classification` is only called when `category` is in `requested_types`
  and ANLZ is present — same cost profile as the original detector run.

## Verification

| Check | Result |
|---|---|
| `PYTHONPATH=. pytest -x -q` | 1330 passed, 4 skipped (full suite) |
| `npm test --silent` (vitest) | 564 passed across 28 files |
| `tests/test_auto_tag.py` focused | 45 passed |
| `tests/test_auto_tag_parallel.py` | 5 passed (covers parallel branch) |
| e2e leg | Cannot run reliably in this worktree — `python3` on the host PATH lacks `pyrekordbox`; the only Python with deps is `/opt/homebrew/opt/python@3.13/bin/python3.13`. After PATH-shimming `python3` to 3.13, the suite collected and ran: 28 passed, 2 failed — both pre-existing (qa-smoke filter-toggle flake, safety-spec stale sandbox dir) and unrelated to the auto-tag surface. No auto-tag-touching e2e spec exists. |

## Would the tests fail if the fix were reverted?

Yes. Reverting `auto_tag.py` would:
1. Drop the `skip_reasons` key from the result dict → `KeyError` in 4/5
   new tests.
2. Drop the per-bucket increments → the boundary test
   (`test_low_classification_bucket_at_boundary`) would see
   `skip_reasons["low_classification"] == 0` and fail.

The invariant test (`sum(skip_reasons.values()) == skipped_no_data`) is
the strongest assertion: it doesn't depend on specific values, so it
catches both a missing bucket and an extra bucket.

## Safety contract conformance

- HARD-1 (real master.db): only the sandbox is read by tests; no writer
  changes.
- HARD-2 (db_writer): untouched — `apply_tags` already routes writes
  through the existing path.
- HARD-3 (no credentials/master.db committed): diff is code + tests only.
- HARD-4 (CORS): not touched.
- HARD-5 (docs): no documented feature removed; no doc change required —
  `docs/reference/auto-tag.md` only references the tag categories, which
  are unchanged.
- HARD-6 (no force/no-verify): standard `git commit` only.
- HARD-7 (scope): only the diagnostic in `apply_tags` is touched; no
  drive-by refactor.

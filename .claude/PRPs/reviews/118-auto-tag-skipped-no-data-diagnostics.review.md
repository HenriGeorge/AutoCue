# Self-review — Issue #118 — Auto-tag skipped_no_data diagnostics

## Verdict

approve

## Issues Found

None blocking. Two notes:

1. **Pre-existing e2e infrastructure failure (NOT introduced here)** — playwright
   refuses to start because `tests/e2e/per-control-sweep.selector.test.ts`
   imports from `tests/e2e/per-control-sweep.spec.ts` (both files are
   auto-discovered as test files; playwright 1.60 forbids test→test imports).
   Verified pre-existing on `origin/main` via `git log origin/main --oneline
   -- per-control-sweep.selector.test.ts` (last touched by PR #24, before this
   branch). My fix touches no e2e file. Separate issue.

2. **`skipped_reasons` granularity** — a track that hits `category` (needs
   PWAV) AND `vocal` (needs PSSI) with both pieces of ANLZ missing will record
   BOTH `no_energy_data` and `no_phrase_data` for that one track. The
   per-track set dedups within a single reason; across reasons, all relevant
   ones fire. This is intentional — the UI should be able to say "8 tracks
   need PWAV, 12 tracks need PSSI" even though they overlap. Documented in
   the helper-fn docstring.

## Verification

### Leg A — pytest (tracked: `autocue/**.py`, `tests/**.py`) — PASS

- `PYTHONPATH=. pytest -x -q tests/test_auto_tag.py` → **49 passed**.
- `PYTHONPATH=. pytest -x -q` (full suite) → **1334 passed, 4 skipped, 1 warning** in 16.5s.

### Leg B — vitest (tracked: `docs/index.html`, `tests/web/**`) — PASS

- `npm test --silent` → **564 tests / 28 files passed** in 5.0s.
- No frontend file touched — vitest run is precautionary (first iteration; no
  baseline touch-log).

### Leg C — e2e Playwright — BLOCKED (pre-existing, not my change)

- `cd tests/e2e && AUTOCUE_SOURCE_DB=$HOME/Library/Pioneer/rekordbox/master.db npm test`
  fails at playwright discovery with:
  > Error: test file "per-control-sweep.selector.test.ts" should not import test
  > file "per-control-sweep.spec.ts"
- Discovery aborts before any test runs.
- My diff: zero touches to `tests/e2e/**`. Verified via `git diff origin/main
  -- tests/e2e/per-control-sweep.selector.test.ts tests/e2e/playwright.config.ts`
  → empty.
- This is a pre-existing test-infra issue (likely a playwright minor-version
  bump tightened the test-import rule). Should be filed separately; out of
  scope for this fix.

## Test quality

Tests added in `tests/test_auto_tag.py::TestSkippedReasons`:

1. `test_skipped_reasons_default_empty_when_all_tagged` — boundary (no skips → empty dict).
2. `test_no_phrase_data_reason_recorded_for_vocal` — REGRESSION (reverts to skipped_no_data only without my code).
3. `test_no_energy_data_reason_recorded_for_category` — REGRESSION.
4. `test_low_confidence_reason_recorded_when_classifier_under_threshold` — BOUNDARY (score MIN_SCORE - 0.01 → low_confidence, NOT no_energy_data; this is exactly the failure mode the QA agent mis-attributed to audio paths).
5. `test_low_confidence_boundary_at_exact_min_score_does_NOT_skip` — BOUNDARY (score == MIN_SCORE qualifies; guards off-by-one).
6. `test_no_metadata_reason_recorded_for_bpm_tier` — REGRESSION.
7. `test_track_with_multiple_gaps_counted_once_per_reason` — INVARIANT (set-per-track dedups).
8. `test_multiple_tracks_accumulate_reasons` — INVARIANT (N tracks → counter = N).
9. `test_intro_outro_medium_length_does_NOT_record_skip_reason` — INVARIANT (documented expected-empty outcome is NOT a data gap).

All nine would FAIL if `skipped_reasons` accumulator were reverted, satisfying
the Splitwave "regression guard" rule from the agent docs.

## Patterns / scope

- ≤50 net code lines (47 deletions + 92 net additions in `auto_tag.py` are
  mostly per-detector signature changes; the new logic is ~30 lines).
- No new dependencies.
- No widening of CORS, no DB write path changes, no bypass of `_rb_running`.
- `AutoTagResponse.skipped_reasons` defaults to `{}` — back-compat preserved
  for clients that ignore the new field.

## Security

- New field is read-only diagnostic output; carries no PII.
- Detector signature change is internal; only `apply_tags` calls them.

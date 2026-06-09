# Self-review — fix/118-auto-tag-skipped-no-data-diagnostic

## Verdict: approve

## Diff scope

5 files, +366 / -54 lines:

- `autocue/analysis/auto_tag.py` (+94 / -54) — detectors now return
  `(names, reason)` tuples; `apply_tags()` aggregates a per-call
  `skipped_reasons: dict[str, int]`. Both parallel and serial paths
  updated.
- `autocue/serve/schemas.py` (+6) — added `skipped_reasons` field to
  `AutoTagResponse` with default `{}` (backward-compat).
- `tests/test_auto_tag.py` (+118) — new `TestSkippedReasons` class with
  5 regression cases.
- `docs/reference/auto-tag.md` (+45 / -1) — documents the new field,
  the stable reason-key table, and the `has_phrase`/`has_beats` caveat.
- `.claude/PRPs/issues/118-...investigation.md` (+103) — PRP artifact.

## Issues found

None.

## Correctness audit

- Detectors now return `tuple[list[str], str | None]`. Only call sites
  are `_DETECTORS[ttype](content, db)` inside `apply_tags()` — both
  parallel and serial branches were updated. Confirmed via
  `grep _DETECTORS` (only `apply_tags` references it; no external
  consumers in `tests/`).
- `apply_classification_tags()` (the legacy v1 entrypoint) was NOT
  touched — it does not consume `_DETECTORS` and still returns the
  v1 shape (`skipped_no_anlz` / `skipped_low_score`). Backwards-compat
  preserved.
- `skipped_no_data` semantics unchanged for non-content-missing cases:
  it still increments once per track whose detectors collectively
  produced no names. `no_content` is recorded under `skipped_reasons`
  only — matches prior silent-skip behaviour for the `tagged` /
  `skipped_no_data` counters.
- Parallel and serial paths produce equivalent `skipped_reasons`
  aggregates (verified via test_invariant — single-tracks and
  multi-track inputs both observed).

## Test quality

- `test_category_and_vocal_skip_distinguished` — FAILS without the
  fix: prior response had no `skipped_reasons` key. Patches
  `get_energy_curve` and `get_mixability` to None to reproduce the
  exact #118 scenario.
- `test_low_classification_score_boundary` — runs the gate at exactly
  `MIN_SCORE` (must tag) AND at `MIN_SCORE - 0.01` (must skip with
  `low_classification_score`). Catches off-by-one regressions in the
  gate condition.
- `test_invariant_total_reasons_at_least_skipped_no_data` — property
  assertion: `sum(skipped_reasons.values()) >= 2 *
  skipped_no_data` when 2 detectors are active and all fail per
  track. Catches future regressions where a detector silently returns
  `[]` with no reason.
- `test_response_carries_skipped_reasons_key` — establishes the field
  is always present (even if empty).
- `test_missing_content_recorded_as_no_content` — confirms the
  silent-skip semantic preservation: `skipped_no_data` does NOT bump
  for missing-content rows.

## Safety contract

- No master.db writes from the harness.
- No CORS, no `--no-verify`, no `--force-push`.
- No drive-by refactors: only `apply_tags()` + its detectors, plus the
  response schema and tests.
- Diff is +312 net (over the 50-line preference), driven by the test
  block and the doc update — the production code delta is ~50 lines
  (detector tuple returns + the `_bump` aggregator).

## Verification

- pytest -x -q: 1330 passed, 4 skipped (full suite). 5 new
  `TestSkippedReasons` cases included.
- npm test --silent: 564 passed (web vitest, untouched by this diff).
- tests/e2e: pre-existing on-main failure
  (`per-control-sweep.selector.test.ts` Playwright-import error from
  PR #19/#24) and one flaky qa-smoke `filter toggles` timeout (passes
  on retry, unrelated). No e2e regressions introduced.

## Risks

- Frontend `docs/index.html` consumes `skipped_no_data`, `errors`,
  `tagged`, `undo_data` only — additive field is non-breaking.
  Future enhancement could render `skipped_reasons` as a tooltip /
  expander.

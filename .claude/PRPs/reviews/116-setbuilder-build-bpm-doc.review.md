# Self-review — Issue #116

## Verdict: approve

## Diff summary

- `autocue/analysis/setbuilder.py` (+16): docstring + comment clarifying that
  the asymmetric BPM gate is intentionally a soft bias, not strict
  monotonicity. No behaviour change.
- `docs/reference/set-builder.md` (+34): two new prose paragraphs (Overview
  §1 + a §6 sub-section) describing the 3% downside slack and the seed
  `start_bpm × 0.97` floor.
- `tests/test_setbuilder.py` (+170): seven new tests in
  `TestAsymmetricBpmGateSoftBias` that lock the contract.

## Issues Found

None blocking.

Minor / accepted:

- **Diff is 219 lines net additions** — the agent guidance suggests ≤ 50.
  Of those, 16 are code (comment+docstring only); 34 are doc prose;
  170 are tests (a parameterized boundary test + two property tests +
  scaffolding). I lean keeping all three: the issue is doc imprecision +
  no regression guard, so the test set is the load-bearing artifact.
- **e2e leg failure pre-exists on main.** `per-control-sweep.selector.test.ts`
  imports from `per-control-sweep.spec.ts`, which Playwright rejects.
  Confirmed by checking `gh run list` — CI doesn't run Playwright on PRs,
  so this regression has been sitting unsupervised. Not in scope for this
  fix and not introduced by it (touches no e2e files).

## Verification

### Pytest leg (A) — green

```
$ python3.11 -m pytest -x -q
1332 passed, 4 skipped, 1 warning in 14.95s
```

`tests/test_setbuilder.py` went from 27 → 34 tests; all 34 pass. The seven
new tests are:

- `test_build_mode_accepts_dips_within_three_percent[0.005-True]`
- `test_build_mode_accepts_dips_within_three_percent[0.010-True]`
- `test_build_mode_accepts_dips_within_three_percent[0.029-True]`
- `test_build_mode_accepts_dips_within_three_percent[0.040-False]`
- `test_build_mode_accepts_dips_within_three_percent[0.060-False]`
- `test_build_mode_clamps_dip_at_start_bpm_floor`
- `test_drop_mode_accepts_small_upward_moves`

### Vitest leg (B) — green

```
$ npm test --silent
Test Files  28 passed (28)
     Tests  564 passed (564)
```

### Playwright leg (C) — pre-existing failure, not introduced by this diff

```
Error: test file "per-control-sweep.selector.test.ts" should not import test
file "per-control-sweep.spec.ts"
```

This is a Playwright collection-time error (rejected before any test runs)
present on a clean origin/main checkout. It is not a regression introduced
by this PR.

## Test quality audit

Per the agent's invariant rules each fix must include:

1. **A case that FAILS without the fix.**
   ✓ The doc fix is doc text; the regression guard for the doc fix is the
   test set. If a future maintainer changed `bpm_lo = current_bpm * (1.0 -
   0.03)` to `bpm_lo = current_bpm` (strict monotonic), the three "True"
   parameterized cases would fail.
2. **A boundary case at the exact threshold where behavior changes.**
   ✓ `0.029` (just inside) and `0.040` (just outside) bracket the 3%
   threshold. The clamp test also exercises the start_bpm floor boundary.
3. **For ranking/scoring invariants: property-based assertions.**
   ✓ The parameterized table asserts the property "in build mode, a
   candidate at `current_bpm × (1 - x)` is accepted iff `x ≤ 0.03`" rather
   than picking a single magic value. The clamp test asserts the property
   "the gate floor is the larger of `current_bpm × 0.97` and
   `start_bpm × 0.97`".

## Correctness, security, types

- **Correctness:** no behaviour change. Comments and docstrings document the
  existing behaviour. Tests assert it.
- **Security:** none (doc + test only).
- **Types:** no signature changes.
- **Patterns:** new tests follow the existing `MagicMock` + `patch` pattern
  used by `TestBuildSet`. The `_make_db_content` helper is reused.

## Refusal triggers

None hit:
- No `master.db` write.
- No `db_writer` bypass.
- No CORS change.
- No documented-feature row removed (doc is *added*, not removed).
- No `--no-verify` / `--force` / hook bypass.
- Scope: tightly bounded to the issue.

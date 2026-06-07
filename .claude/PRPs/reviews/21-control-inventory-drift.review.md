# Self-review — Issue #21 fix

## Verdict
approve — changes are confined to the QA test harness, scope is small
(≤ 50 lines of meaningful diff in `tests/e2e/`), and the drift guard now
passes against the live `docs/index.html`.

## Issues found
None blocking. Notes:

- Some allowlisted IDs (e.g. `cue-recolor-slot-0..7`) are arguably
  user-facing controls when the recolor sub-operation is open. I chose
  allowlist over inventory because they are dynamically generated from
  the JS in `index.html:3452` and only exist after `cue-tools-op` is
  set to `recolor`. Adding them as first-class rows would require the
  sweep to drive the op selector first, which is out of scope for #21.
  Future contributor can promote them once #20 (`CSS is not defined`)
  unblocks per-control sweep execution.
- The `mini-scrubber` is `kind: "range"` (new kind for the
  `ControlKind` union in `tests/e2e/control-inventory.ts`). The
  TypeScript union already includes `"range"` so no type widening
  required.

## Verification
- Leg A `pytest -x -q`: 850 passed.
- Leg B `npm test --silent` (vitest): 195 passed.
- Leg C `cd tests/e2e && AUTOCUE_SOURCE_DB=... npm test`:
  - `control-inventory.spec.ts` (drift guard + per-track-testid +
    panel-names) — all 3 tests pass.
  - `per-control-sweep.spec.ts` — 92 failures from `ReferenceError:
    CSS is not defined` (issue #20, explicitly out of scope for #21
    per the issue body). Same failure pattern on every row, including
    rows unchanged from main → pre-existing infrastructure issue.
- Regression guard: dropping any inventory addition or allowlist
  entry would make the drift guard re-fail with the corresponding ID
  listed in either `unexpectedExtras` or `inventoryStales`.

## Scope discipline
No code under `autocue/` touched. No docs reorganised. No CORS,
write-path, or backup-flow changes. No `.env` / `master.db` / Pioneer
paths touched. No `git push --force` / `--no-verify`.

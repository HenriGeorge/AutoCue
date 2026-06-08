# Issue #112 — Investigation

## Problem

`tests/e2e/per-control-sweep.selector.test.ts:2` imports `buildIdSelector`
from `./per-control-sweep.spec`. Playwright forbids one test file from
importing another and aborts the entire test run during discovery:

```
Error: test file "per-control-sweep.selector.test.ts" should not import
test file "per-control-sweep.spec.ts"
```

Effect: `cd tests/e2e && npm test` discovers zero tests. The
`safety.spec.ts` preflight — which guards against writing to the user's
real `master.db` — never runs through the documented entry point.

## Root Cause

- `tests/e2e/per-control-sweep.spec.ts:94` exports `buildIdSelector` from
  a file that also calls `test()` (see lines 184/206 — `panelsToRun.forEach`
  drives behavioural `test()` registration).
- `tests/e2e/per-control-sweep.selector.test.ts:2` does
  `import { buildIdSelector } from "./per-control-sweep.spec"`.
- Playwright's runner explicitly rejects cross-test-file imports because
  importing a spec causes `test()` calls in the imported module to register
  in the importer's scope, producing duplicated/misattributed tests.

## Proposed Solution

Extract the pure helper `buildIdSelector` into a sibling module
`tests/e2e/per-control-sweep.helpers.ts` that contains **no `test()`
calls**. Update both `per-control-sweep.spec.ts` and
`per-control-sweep.selector.test.ts` to import from the helper module.

This is the fix suggested in the issue body. Single-file rename + two
import edits; zero behavior change. `buildIdSelector` is already a
pure-function helper — moving it out of a spec file is the right
architectural shape.

## Affected Files

- `tests/e2e/per-control-sweep.helpers.ts` — new file, exports
  `buildIdSelector`.
- `tests/e2e/per-control-sweep.spec.ts` — remove `export` of
  `buildIdSelector`, import it from the helper instead. Keep the
  JSDoc comment chain pointing at the helper for future maintainers.
- `tests/e2e/per-control-sweep.selector.test.ts` — update import path
  from `./per-control-sweep.spec` to `./per-control-sweep.helpers`.

## Risks

- **Low.** No behavior change — `buildIdSelector` is a pure synchronous
  string transform. The existing regression suite in
  `per-control-sweep.selector.test.ts` continues to guard the
  CSS-escape regression (issue #20).
- The behavioural sweep (`per-control-sweep.spec.ts`) still uses the
  helper through the new import; runtime semantics are identical.
- Playwright config (`playwright.config.ts`) requires no change — both
  files remain in the `*.spec.ts` / `*.test.ts` glob; the helper is
  `*.helpers.ts` which is correctly excluded.

## Validation legs

- **Leg A (pytest):** clean — no Python touched. SKIP.
- **Leg B (vitest):** clean — no `docs/index.html` or `tests/web/**`
  touched. SKIP.
- **Leg C (Playwright e2e):** dirty (`tests/e2e/**`). MUST RUN.
- First iteration runs all legs regardless per the agent contract.

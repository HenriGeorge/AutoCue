# Issue #112 — Investigation

**Title:** `[autocue-qa] qa-harness:selector-test-imports-spec:test-discovery-blocked`

## Problem

`tests/e2e/per-control-sweep.selector.test.ts:2` imports `buildIdSelector`
from `./per-control-sweep.spec`. Playwright forbids one test file importing
another, so its discovery phase aborts before any test runs:

```
Error: test file "per-control-sweep.selector.test.ts" should not import
test file "per-control-sweep.spec.ts"
```

Effect: `cd tests/e2e && npm test` does not run anything — including
`safety.spec.ts`, the load-bearing preflight that prevents the harness
from writing to the user's real `master.db`. The documented QA entry
point is unrunnable until the cross-test-file import is removed.

## Root cause (file:line)

- `tests/e2e/per-control-sweep.spec.ts:94-97` — `export function buildIdSelector(...)` lives in a Playwright test file.
- `tests/e2e/per-control-sweep.selector.test.ts:2` — `import { buildIdSelector } from "./per-control-sweep.spec";` triggers Playwright's spec-imports-spec guard.

## Proposed solution

Extract `buildIdSelector` (a pure 4-line function) into a new sibling
module `tests/e2e/per-control-sweep.helpers.ts` containing zero
`test()` calls. Update both files to import from the helper module:

- `per-control-sweep.spec.ts` — re-import `buildIdSelector` from the helper;
  drop the local export.
- `per-control-sweep.selector.test.ts` — change the import path from
  `./per-control-sweep.spec` to `./per-control-sweep.helpers`.

Behaviour is unchanged. The function is referentially identical.

## Affected files

- NEW: `tests/e2e/per-control-sweep.helpers.ts`
- EDIT: `tests/e2e/per-control-sweep.spec.ts` (drop function body, add import)
- EDIT: `tests/e2e/per-control-sweep.selector.test.ts` (re-point import)

## Risks

- **Discovery regression guard**: The selector tests already cover
  `buildIdSelector` behaviour (Node-safety, escape rules); moving them
  to a helper module preserves that coverage. The `regression for #20`
  guard remains in place.
- **No behaviour change**: `safeInteract` in the spec calls
  `buildIdSelector(row.id)` — same function, just re-imported.
- **Test legs affected**: Only the e2e leg (`tests/e2e/**`) touches.
  Python and vitest legs are not impacted.

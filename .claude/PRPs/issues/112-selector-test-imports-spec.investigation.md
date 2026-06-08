# Issue #112 — qa-harness: per-control-sweep selector test imports spec

## Problem

`tests/e2e/per-control-sweep.selector.test.ts:2` does:

```ts
import { buildIdSelector } from "./per-control-sweep.spec";
```

Playwright explicitly forbids a test file importing another test file. During discovery the runner aborts the entire run with:

```
Error: test file "per-control-sweep.selector.test.ts" should not import test file "per-control-sweep.spec.ts"
```

Effect: `cd tests/e2e && npm test` cannot launch ANY tests — including `safety.spec.ts`, the load-bearing guard that asserts the server is bound to the sandbox `master.db` copy via `/api/status` + `X-AutoCue-Diagnostic: 1`. The QA harness is unusable via its documented entry point.

## Root Cause

- `tests/e2e/per-control-sweep.spec.ts:94-97` exports `buildIdSelector` from inside a `*.spec.ts` file (the behavioural sweep file).
- `tests/e2e/per-control-sweep.selector.test.ts:2` imports that helper from the same file.
- Playwright's `testMatch` default covers both `*.spec.ts` and `*.test.ts`. Both files are therefore "test files", and Playwright's invariant kicks in.

## Proposed Solution

Extract the pure helper into a non-test sibling module and update both call sites:

1. Create `tests/e2e/per-control-sweep.helpers.ts` containing `buildIdSelector` (no `test()` calls, no Playwright imports needed — the function is pure-string).
2. Re-export `buildIdSelector` from `per-control-sweep.spec.ts` (back-compat insurance) by changing the existing `export function buildIdSelector(...)` body to `import { buildIdSelector } from "./per-control-sweep.helpers"` and re-export — OR simply replace the local definition with an import + internal use; no public re-export from `.spec.ts` needed if `selector.test.ts` switches to the helper.
3. Update `per-control-sweep.selector.test.ts` import to point at the helper.

Minimal, single-purpose change. ≤ 50 lines diff target.

## Affected Files

- `tests/e2e/per-control-sweep.helpers.ts` (NEW — ~15 lines incl. comments)
- `tests/e2e/per-control-sweep.spec.ts` (replace local helper with import; delete local definition; ~6 line change)
- `tests/e2e/per-control-sweep.selector.test.ts` (one import line)

## Risks

- None to the actual selector logic — the helper is moved verbatim.
- Playwright's `testMatch` does not pick up `*.helpers.ts`, so the helper file is invisible to the runner. Verified: default `testMatch` is `**/*.@(spec|test).?(c|m)[jt]s?(x)`.
- The `safety.spec.ts` preflight will now actually run, which is the desired behaviour.

## Regression Guard Strategy

The existing `per-control-sweep.selector.test.ts` already covers the original `CSS.escape` bug for issue #20. After the fix it will run again as part of `npm test` discovery (today it never runs because the run aborts before discovery completes).

For issue #112 specifically, the regression guard is structural: as long as the helper lives in `*.helpers.ts` (not a test file), Playwright cannot raise the "test file should not import test file" error. To make this explicit, we keep the helper file extension distinct from `.spec.ts` / `.test.ts`.

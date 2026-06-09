# Issue #112 — selector-test imports spec → test discovery blocked

## Problem

`tests/e2e/per-control-sweep.selector.test.ts:2` does:

```ts
import { buildIdSelector } from "./per-control-sweep.spec";
```

Playwright forbids one test file from importing another. During discovery the
runner aborts the entire test run with:

```
Error: test file "per-control-sweep.selector.test.ts" should not import test
file "per-control-sweep.spec.ts"
```

Effect: `cd tests/e2e && npm test` exits before any test runs — including the
load-bearing `0-safety.spec.ts` that verifies the harness is bound to the
sandbox DB. The whole QA harness is unrunnable via the documented entry point.

## Root cause (file:line)

- `tests/e2e/per-control-sweep.spec.ts:94` exports `buildIdSelector(id)` from
  inside a Playwright spec file (a file that calls `test()` at module scope via
  `runRows(...)`).
- `tests/e2e/per-control-sweep.selector.test.ts:2` imports that helper from
  the spec file, which Playwright's discovery refuses.

## Proposed solution

Extract `buildIdSelector` (and only that helper — no `test()` calls) into a new
sibling helper module `tests/e2e/per-control-sweep.helpers.ts`. Update both
files to import from the helper:

- `per-control-sweep.spec.ts` re-imports `buildIdSelector` from the helper
  (drop the local definition; keep no other behaviour change).
- `per-control-sweep.selector.test.ts:2` swaps the import target from
  `./per-control-sweep.spec` to `./per-control-sweep.helpers`.

This is exactly the fix suggested in the issue body. It is the minimal change:
one new file, two import edits, no behaviour change.

`PANEL_NAMES` (mentioned in the issue body) is already imported from
`./control-inventory`, not from the spec — no extraction needed for it.

## Affected files

- `tests/e2e/per-control-sweep.helpers.ts` — NEW (exports `buildIdSelector`).
- `tests/e2e/per-control-sweep.spec.ts` — replace local `buildIdSelector`
  definition with `import { buildIdSelector } from "./per-control-sweep.helpers"`.
- `tests/e2e/per-control-sweep.selector.test.ts` — change import source from
  `./per-control-sweep.spec` to `./per-control-sweep.helpers`.

Diff size: ~10 lines net.

## Risks

- **Regression risk: low.** Pure code move, no logic change. The existing
  `per-control-sweep.selector.test.ts` already pins behaviour of
  `buildIdSelector` (Node-safe, no `CSS.escape`, attribute-equals form,
  quote/backslash escaping). Those tests continue to guard the helper after
  the move.
- **Discovery risk: low.** Playwright's `testMatch` default covers
  `*.spec.ts` and `*.test.ts`. The new `.helpers.ts` extension matches
  neither, so it will not be discovered as a test file — exactly what we want.
- **CI risk: none expected.** The fix unblocks the harness rather than
  changing what it asserts.

## Test legs to run

- A (pytest): SKIP — no `autocue/**.py` or `tests/**.py` changes.
- B (vitest): SKIP — no `docs/index.html` or `tests/web/**` changes.
- C (e2e): RUN — files under `tests/e2e/**` change. This is the leg the fix
  is unblocking; running it validates discovery is no longer aborted.

First-iteration rule still applies — all legs run on iteration 1 regardless
of the touch-log analysis.

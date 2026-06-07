# Investigation — Issue #20: `CSS is not defined` in per-control sweep

**Fingerprint**: `[autocue-qa] per-control-sweep:css-escape-undefined:reference-error`

## Problem
Every per-control row test in `tests/e2e/per-control-sweep.spec.ts` throws
`ReferenceError: CSS is not defined` from `safeInteract()` (line 78). 35
tests across the global and cues panels fail during a `AUTOCUE_QA_SCOPE=cues npm test`
run.

## Root cause
`tests/e2e/per-control-sweep.spec.ts:78`:

```ts
const sel = `#${CSS.escape(row.id)}`;
```

`CSS` (the `CSSStyleSheet` namespace which carries `CSS.escape`) is a
browser-only global. The Playwright test code here runs in Node, where
`CSS` is `undefined`. `page.locator()` only receives the already-formatted
selector string — there is no implicit Node→browser bridge for `CSS.escape`.
It would only be valid inside `page.evaluate(...)`.

## Proposed solution
Replace the `#…` id selector with the attribute-equals form
`[id="…"]`, per the issue's "(b)" recommendation. This avoids any escaping
concern for future IDs and removes the dependency on a browser-only global
without changing the matched elements (Playwright treats `[id="x"]` and
`#x` identically when `x` is a plain identifier).

```ts
const sel = `[id="${row.id}"]`;
```

Add a regression test that exercises `safeInteract`'s selector construction
without launching a browser, so the Node-vs-browser API mistake cannot
re-land silently.

## Affected files
- `tests/e2e/per-control-sweep.spec.ts` (line 78 — selector construction)
- `tests/e2e/per-control-sweep.selector.test.ts` (new — unit regression
  guard around the selector helper)

## Risks
- Selector semantics: `[id="x"]` matches the same single element as `#x`
  when `x` is a plain identifier (every id in the inventory is
  alphanumeric + hyphens). No behavioral change for the existing inventory.
- The fix is one line of production code; the only collateral is one new
  Node-only spec file. No Python or web (vitest) surfaces are touched.

## Validation legs (per `.claude/fixer.yaml`)
- **Leg A (pytest)** — untouched (no `.py` change). Touch log: clean ⇒ SKIP after
  first iteration baseline.
- **Leg B (vitest)** — untouched (no `docs/index.html` or `tests/web/**` change).
  Touch log: clean ⇒ SKIP after baseline.
- **Leg C (e2e)** — REQUIRED. The fix and the regression test both live in
  `tests/e2e/**`.

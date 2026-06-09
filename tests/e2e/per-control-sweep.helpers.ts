/**
 * Pure helpers for the per-control sweep — no `test()` calls live here.
 *
 * Extracted from `per-control-sweep.spec.ts` so that
 * `per-control-sweep.selector.test.ts` can import `buildIdSelector` without
 * pulling in another test file. Playwright explicitly forbids one test file
 * from importing another (it aborts discovery), so behavioural specs and the
 * unit tests around their helpers must share a third non-test module — this
 * one. See issue #112.
 */

/**
 * Build a CSS selector that targets a single inventory row by id.
 *
 * NOTE: `CSS.escape` is a browser-only global (the `CSSStyleSheet` namespace)
 * — it is **undefined** in Node, which is where Playwright test bodies execute.
 * Using it here previously caused every per-control test to throw
 * `ReferenceError: CSS is not defined` before any assertion ran (issue #20).
 *
 * Use the attribute-equals form `[id="…"]` instead: it matches the same
 * element as `#…` for the alphanumeric/hyphen identifiers the inventory
 * produces, and is also safe if a future id ever contains a CSS-special
 * character. The only thing that needs escaping is an embedded `"` — we
 * escape that and any `\\` so the attribute selector stays syntactically
 * valid regardless of what shows up in the inventory.
 *
 * Exported for unit testing (see `per-control-sweep.selector.test.ts`).
 */
export function buildIdSelector(id: string): string {
  const safe = id.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `[id="${safe}"]`;
}

/**
 * Pure helpers for the per-control sweep.
 *
 * Lives outside `*.spec.ts` / `*.test.ts` so Playwright's default `testMatch`
 * does NOT pick this file up — that matters because Playwright explicitly
 * forbids one test file from importing another (issue #112). The selector
 * regression test (`per-control-sweep.selector.test.ts`) and the sweep itself
 * (`per-control-sweep.spec.ts`) both import from this module.
 *
 * Keep this file free of `test()` / `expect()` calls and Playwright imports.
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
 * escape that and any `\` so the attribute selector stays syntactically
 * valid regardless of what shows up in the inventory.
 */
export function buildIdSelector(id: string): string {
  const safe = id.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `[id="${safe}"]`;
}

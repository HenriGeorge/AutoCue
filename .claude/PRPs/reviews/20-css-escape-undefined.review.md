# Self-review — Issue #20 fix

**Verdict**: approve

## Diff scope
- `tests/e2e/per-control-sweep.spec.ts` — 1 production line changed
  (`CSS.escape(row.id)` → `buildIdSelector(row.id)`) plus a 22-line
  exported helper + JSDoc explaining the Node-vs-browser landmine.
- `tests/e2e/per-control-sweep.selector.test.ts` — new file, 4 Node-only
  regression tests.
- `.claude/PRPs/issues/20-css-escape-undefined.investigation.md` — PRP
  artifact (no behavioural impact).

Total: 3 files, +140 / -1.

## Correctness
- The attribute-equals selector `[id="x"]` is semantically identical to
  `#x` for the entire control inventory (alphanumeric + hyphens).
  Playwright's CSS engine treats them as the same selector class.
- Escaping `"` and `\\` is belt-and-braces — no current id needs it, but
  it keeps the helper safe against future inventory growth.
- `safeInteract` is only invoked after `gotoPanel` has navigated and the
  panel readiness signal asserted, so selector resolution happens after
  the DOM is populated. No timing regression.

## Security
- No new external input is parsed. The helper operates on inventory
  strings already statically declared in the repo.
- No CORS, secret, or filesystem path touched.

## Test quality (would tests fail if the fix were reverted?)
- Yes. The first test (`runs in Node without ReferenceError`) executes
  the helper synchronously. Reverting it to `\`#${CSS.escape(id)}\`` would
  throw exactly `ReferenceError: CSS is not defined` in Node, failing the
  test. This is the regression guard the issue asked for.
- The fourth test (`does NOT reference the browser-only CSS global`)
  explicitly nulls out `globalThis.CSS` to defeat the case where a future
  contributor polyfills `CSS` and masks the bug.

## Safety contract audit
- Did not touch `db_writer.rekordbox_is_running` or its callers.
- Did not widen CORS in `autocue/serve/app.py`.
- Did not remove any documented feature row from `docs/qa_tester.md` or
  `docs/qa_fixer.md`.
- No `.env`, credentials, or `~/Library/Pioneer/` paths staged.
- e2e leg ran against the sandbox copy of `master.db`, not the source.

## Verification
- pytest -x -q → 850 passed.
- npm test --silent → 195 passed.
- `cd tests/e2e && AUTOCUE_QA_SCOPE=cues npx playwright test
  per-control-sweep.spec.ts per-control-sweep.selector.test.ts` →
  16 passed (was 0 before the fix — entire sweep used to abort with the
  `ReferenceError`). 9 remaining failures are `console.error: 404 Not
  Found` assertions on specific row interactions, unrelated to selector
  construction; they are a sibling issue per the bug report's own note.

## Issues found
None.

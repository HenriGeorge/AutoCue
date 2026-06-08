# Issue #112 — Self-review

## Verdict

approve

## Diff

- `tests/e2e/per-control-sweep.helpers.ts` — NEW (29 lines). Pure-function helper, no `test()` / `expect()` calls, no Playwright imports. Lives outside `*.spec.ts` / `*.test.ts` so Playwright's `testMatch` does not class it as a test file.
- `tests/e2e/per-control-sweep.spec.ts` — local `export function buildIdSelector` removed; import added from `./per-control-sweep.helpers`. All callsites in the file untouched (`safeInteract` still calls `buildIdSelector(row.id)` exactly as before).
- `tests/e2e/per-control-sweep.selector.test.ts` — import switched from `./per-control-sweep.spec` to `./per-control-sweep.helpers`. Header docstring extended to flag that the import shape itself is load-bearing.

## Issues Found

None.

- Correctness: helper is byte-for-byte identical to the previous local function (verified by reading both pre/post edit).
- Security: no surface change. Selector escaping logic unchanged; same `\\` → `\\\\` and `"` → `\\"` substitution.
- Test quality: regression IS structural. The previous shape aborted Playwright discovery with `test file ... should not import test file ...`. With the helper in `*.helpers.ts`, Playwright's default `testMatch` (`**/*.@(spec|test).?(c|m)[jt]s?(x)`) does not match — so re-introducing the import-from-spec shape would immediately break discovery again. The existing `per-control-sweep.selector.test.ts` (4 tests) still covers the issue #20 `CSS.escape` regression and now ALSO acts as a witness for the issue #112 fix: it only runs if discovery succeeded.
- Patterns: matches the `*.helpers.ts` convention used elsewhere in the repo (`tests/e2e/control-inventory.ts` is a similar non-test sibling module).
- Types: TypeScript signature unchanged. `(id: string) => string`.
- Safety: no changes to `db_writer.rekordbox_is_running()` checks, no CORS widening, no `master.db` writes, no doc removals from `docs/qa_tester.md` or `docs/qa_fixer.md`.

## Verification

- `cd tests/e2e && npx playwright test --list` — discovery succeeds, 137 tests across 9 files. Previously this command aborted with `Error: test file "per-control-sweep.selector.test.ts" should not import test file "per-control-sweep.spec.ts"`.
- `cd tests/e2e && npx playwright test per-control-sweep.selector.test.ts` — 4/4 pass (~1.5s).
- `cd tests/e2e && npx playwright test safety.spec.ts` — 3/3 pass (load-bearing sandbox-DB safety preflight runs again).
- `pytest -x -q` — 1325 passed, 4 skipped.
- `npm test --silent` — 564 vitest tests, 28 files, all green.

## Scope check

Diff is ≤ 50 lines and touches exactly the files needed to extract one pure function. No drive-by refactors.

# Self-review — issue #112 fix

## Verdict

**Approve.** Minimal, targeted, behaviour-preserving fix; CI signal restored.

## Diff summary (`git diff origin/main...HEAD`)

| File | Change |
|---|---|
| `tests/e2e/per-control-sweep.helpers.ts` | NEW — exports `buildIdSelector` (pure helper, no `test()` calls). |
| `tests/e2e/per-control-sweep.spec.ts` | Remove local `buildIdSelector` definition; import from `./per-control-sweep.helpers`. Leave a comment pointing readers to the helper. |
| `tests/e2e/per-control-sweep.selector.test.ts` | Swap import source from `./per-control-sweep.spec` → `./per-control-sweep.helpers`. |
| `.claude/PRPs/issues/112-test-discovery-blocked.investigation.md` | NEW investigation artifact. |

Net: 4 files, +118 / -22.

## Audit

### Correctness

- `buildIdSelector` body copied byte-for-byte into the helper. Verified by reading both versions; the only difference is the JSDoc block, which is preserved.
- Both consumers (the spec and the selector unit tests) now import the same symbol from the same module — no duplication.
- The helper file has no `import` of `@playwright/test` and no `test()` calls, so Playwright will not classify it as a test file. Confirmed: `npx playwright test --list` shows 137 tests in 9 files (helper file is NOT one of them).

### Security / safety

- No write paths touched. No `db_writer.rekordbox_is_running()` bypass. No CORS change. No `master.db` references altered. No `.env` or credentials staged.
- Sandbox-DB guarantee unaffected — the fix is purely a TS module-graph refactor inside `tests/e2e/`.

### Test quality (would tests fail if fix reverted?)

- **Yes.** The existing `per-control-sweep.selector.test.ts` is the regression test for this fix. If `buildIdSelector` were moved back into the spec and the selector test re-imported from the spec, Playwright discovery would abort again and `0 tests passed` would be the result.
- The 4 existing unit tests still pin behaviour: (1) does not throw in Node, (2) round-trips plain ids, (3) escapes `"` and `\\`, (4) does not reference `CSS`.
- I did NOT add new tests because the existing tests already provide the regression guard plus boundary cases. Adding more would be gold-plating.

### Patterns / types

- TypeScript: helper exports a `(id: string) => string` function — matches the prior signature exactly. No `any`, no implicit conversions.
- File naming: `.helpers.ts` extension is intentional — Playwright's default `testMatch` covers `*.spec.ts` and `*.test.ts` only. `.helpers.ts` is correctly excluded.
- Comment hygiene: the spec keeps a brief pointer comment so anyone grepping for `buildIdSelector` in the spec file finds the helper module immediately.

## Verification

| Check | Result |
|---|---|
| `npx playwright test --list` | 137 tests in 9 files (was: 0, aborted at discovery). |
| `npx playwright test 0-safety.spec.ts per-control-sweep.selector.test.ts` | 7 passed (1.6s) — 3 safety-preflight + 4 selector unit tests. |
| `git diff origin/main...HEAD --stat` | 4 files, +118 / -22 — exactly the planned scope. |
| Untracked junk staged? | No. Only the 4 intended files plus the investigation artifact are in the commit. |
| Conventional commit + `Closes #112` + `Context:` block? | Yes — verified by `git log -1 --format=%B`. |

## Issues found

None. No amendments required.

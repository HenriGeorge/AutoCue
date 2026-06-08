# Issue #119 — qa-harness: globalTimeout too low for full sweep; safety.spec.ts may never run

## Problem

`tests/e2e/playwright.config.ts:121` sets `globalTimeout: 300_000` (5 min). A full
per-control sweep contains ~116 row-tests at 10-15s each (≈19-29 min), which
exceeds this cap. Worse, Playwright orders test files alphabetically, so
`safety.spec.ts` runs **last** in the lexical order:

```
control-inventory.spec.ts
discover-v2.spec.ts
pages-smoke.spec.ts
per-control-sweep.spec.ts
qa-full.spec.ts
qa-smoke.spec.ts
safety.spec.ts        ← runs LAST in alphabetical order
selectors-exist.spec.ts
```

`per-control-sweep.spec.ts` runs first, blows past the globalTimeout, and the
remaining specs (including `safety.spec.ts`, the load-bearing sandbox-DB guard)
never execute. The QA harness's documented safety invariant — "safety.spec.ts
runs first; if it fails the rest aborts" (`autocue-qa.md` line 15, 18;
`docs/qa_tester.md:33`) — is silently undermined.

## Root cause

Two cooperating misconfigurations in `tests/e2e/playwright.config.ts`:

1. **Line 121**: `globalTimeout: 300_000` is far below a full sweep's runtime.
2. **No explicit test ordering**: Playwright discovers `*.spec.ts` files in
   alphabetical order. `safety.spec.ts` sorts AFTER every existing spec except
   `selectors-exist.spec.ts`, and well after `per-control-sweep.spec.ts`.

The `playwright.config.ts:16-19` comment already claims safety runs first ("via
the project order"), but no such ordering is configured — projects only has a
single `chromium` project. File-discovery order is what actually decides.

## Proposed solution

Minimal two-line fix at the file system + config level:

1. **Rename `safety.spec.ts` → `0-safety.spec.ts`** so alphabetical file
   discovery puts safety first. (No code change inside the spec; just a `git mv`.)
2. **Bump `globalTimeout` to 1_800_000 ms (30 minutes)** in
   `playwright.config.ts` so a full sweep can complete.

This matches both fixes the QA agent suggested in the issue body, in their
"complementary" form. Splitting per-control-sweep into a separate Playwright
project was considered and rejected for v1 — it doubles webServer boot cost,
needs config refactoring well beyond the issue's stated scope, and doesn't
address the alphabetical-ordering bug at all.

## Affected files

- `tests/e2e/safety.spec.ts` → renamed to `tests/e2e/0-safety.spec.ts`
- `tests/e2e/playwright.config.ts` (one-line constant update)
- `tests/e2e/README.md` (doc reference, line 58)
- `.claude/agents/autocue-qa.md` (doc references at lines 15, 18, 110)
- `.claude/project/architecture.md` (line 152)
- `.claude/project/api-design.md` (line 13)
- `docs/qa_tester.md` (lines 33, 67, 371)

All doc references updated to the new filename. No test/spec logic changes.

## Risks

- **Low.** The change is purely organisational. Renaming a file does not change
  what it tests. Bumping `globalTimeout` only relaxes a ceiling — no test can
  fail because the timeout grew.
- The `safety.spec.ts` filename appears in `qa-full.spec.ts:8` as a doc comment
  reference; the comment text is updated to track the rename.
- One concern: are there any external CI workflows that grep for
  `safety.spec.ts` by name? `grep -rn "safety.spec"` across the repo shows only
  the doc/comment references above plus the test file itself — no CI YAML, no
  Makefile target.

## Verification plan

- Leg A (`pytest`): not touched by this change — should remain green and is
  skip-eligible.
- Leg B (`vitest`): not touched — skip-eligible.
- Leg C (`playwright`): the load-bearing leg. Verify that after rename, the
  Playwright test list reports safety specs first via
  `npx playwright test --list`. Cannot run the full sweep here (would take 25+
  min and need a live master.db), but the ordering check is the regression
  guard the issue asks for.

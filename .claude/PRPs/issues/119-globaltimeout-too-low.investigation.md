# Issue #119 — Investigation

**Title:** `[autocue-qa] qa-harness:globalTimeout-too-low-for-full-sweep:run-aborts-before-safety`

## Problem

`tests/e2e/playwright.config.ts:121` sets `globalTimeout: 300_000` (5 min). The per-control sweep alone contains ~116 row-tests at ~10–15 s each (20–29 min minimum). Spec files run alphabetically, so order is:

```
control-inventory.spec.ts
discover-v2.spec.ts
pages-smoke.spec.ts
per-control-sweep.spec.ts
qa-full.spec.ts
qa-smoke.spec.ts
safety.spec.ts            ← last
selectors-exist.spec.ts
```

`safety.spec.ts` sorts last. When `per-control-sweep` runs, the 5-minute `globalTimeout` fires before safety, qa-smoke, pages-smoke, or selectors-exist ever start. The harness's load-bearing sandbox-DB guard (`safety.spec.ts`) is silently never executed on full-sweep runs.

## Root Cause

Two independent misconfigurations in `tests/e2e/playwright.config.ts`:

1. **Line 121** `globalTimeout: 300_000` is far too small for a full sweep (~20–29 min of per-control row-tests).
2. **Spec discovery is alphabetical** and `safety.spec.ts` is named such that it sorts AFTER `per-control-sweep.spec.ts`. Comment on line 16 of the config asserts "`safety.spec.ts` runs first in the project order" — that is no longer true now that `per-control-sweep.spec.ts` exists.

## Proposed Solution

Both fixes in the same patch:

1. **Rename** `tests/e2e/safety.spec.ts` → `tests/e2e/0-safety.spec.ts` so it sorts first alphabetically. Tiny, surgical, no Playwright project-config plumbing required. `0-` prefix is a well-known convention for "run first".
2. **Bump** `globalTimeout` from `300_000` to `1_800_000` (30 min) so a full per-control sweep can complete without aborting the safety + smoke specs that share the same Playwright project.

## Affected Files

| File | Change |
|---|---|
| `tests/e2e/safety.spec.ts` → `tests/e2e/0-safety.spec.ts` | rename (git mv) |
| `tests/e2e/playwright.config.ts` | bump `globalTimeout` to 1_800_000 |
| `tests/e2e/README.md` | update filename reference |
| `tests/e2e/qa-full.spec.ts` | update comment reference (`safety.spec.ts`-style invariants) |
| `docs/qa_tester.md` | update mermaid + sequence + table references |
| `.claude/agents/autocue-qa.md` | update filename references |
| `.claude/project/api-design.md` | update filename reference |
| `.claude/project/architecture.md` | update filename reference |

Note: existing test `.claude/PRPs/reviews/15-*.md` is a historical record and is not updated.

## Risks

- **Test ordering**: Playwright with `fullyParallel: false, workers: 1` and `forbidOnly` runs tests in spec-file alphabetical order within a project. `0-safety.spec.ts` sorts before everything else in the directory.
- **CI duration**: bumping `globalTimeout` to 30 min means a pathologically broken sweep can chew up 30 minutes of CI before being killed (vs 5 min today). Acceptable — the alternative is silently skipping the safety net.
- **Tooling expectations**: nothing outside the repo references the literal filename `safety.spec.ts` as a CLI target (verified via grep). Renames are safe.

## Validation legs to run (Phase 2)

Touched paths:
- `tests/e2e/safety.spec.ts` → rename → triggers **Leg C** (e2e)
- `tests/e2e/playwright.config.ts` → triggers **Leg C** (also a shared root if ever needed; playwright.config IS the shared root → forces all legs)
- `tests/e2e/README.md`, `docs/qa_tester.md`, `.claude/**`, `tests/e2e/qa-full.spec.ts` → docs-only, no leg

Since `playwright.config.ts` IS listed in the "shared roots" list, all three legs MUST run.

Verification beyond unit-level: a dry-run e2e leg invocation should now show `0-safety.spec.ts` listed before `control-inventory.spec.ts` in Playwright's pre-flight test list.

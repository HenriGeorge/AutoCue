# Issue #119 — globalTimeout too low for full sweep; safety spec never runs

## Problem

`tests/e2e/playwright.config.ts:121` sets `globalTimeout: 300_000` (5 minutes). The per-control sweep contains ~116 row-tests at ~10-15s each = 19-29 minutes minimum. Playwright discovers spec files alphabetically:

```
control-inventory.spec.ts   < per-control-sweep.spec.ts   < safety.spec.ts
```

So the long sweep runs first, eats the entire 5-minute global budget, and `safety.spec.ts` (the load-bearing sandbox DB guard) never starts. Reported run: `5.0 m`, `23 did not run, 19 failed, 3 passed`; `safety.spec.ts` listed as `no-result`.

## Root cause

Two independent settings combine to defeat the safety contract:

1. **Spec discovery order is alphabetical** — `safety.spec.ts` sorts AFTER both `control-inventory.spec.ts` and `per-control-sweep.spec.ts`. Nothing forces safety to run first.
2. **`globalTimeout: 300_000` is smaller than a single full sweep** — bound by `tests/e2e/playwright.config.ts:121`.

The harness README and the `playwright.config.ts` JSDoc both claim "safety runs first." That contract is false today.

## Proposed solution

Both fixes from the issue, applied together:

1. **Rename `tests/e2e/safety.spec.ts` → `tests/e2e/0-safety.spec.ts`** so alphabetical discovery puts it first. (Minimum-risk choice vs. introducing a separate Playwright project — single-file rename + ref updates in docs.)
2. **Raise `globalTimeout` to `1_800_000` (30 min)** so a full sweep can complete and safety + downstream specs actually run.

Both changes are needed: rename alone still risks the sweep blowing the budget before downstream specs run; timeout bump alone still has safety running after the sweep.

## Affected files

- `tests/e2e/safety.spec.ts` — renamed to `tests/e2e/0-safety.spec.ts` (content unchanged)
- `tests/e2e/playwright.config.ts` — `globalTimeout: 300_000` → `1_800_000`; update JSDoc reference from `safety.spec.ts` to `0-safety.spec.ts`
- `tests/e2e/README.md` — update spec table
- `tests/e2e/qa-full.spec.ts` — comment reference to `safety.spec.ts`
- `docs/qa_tester.md` — mermaid diagram label + table row referencing the file
- `.claude/agents/autocue-qa.md` — file table reference

## Risks

- Renaming a spec file CAN break external doc / harness assumptions. Searched repo for `safety.spec` — references found are all docs / comments / artifacts (no code-level imports), so the rename is safe.
- 30 min globalTimeout is a longer worst case; smoke runs are not affected (they finish in seconds). The benefit (safety actually runs) is non-negotiable.
- No test file imports `safety.spec.ts` — Playwright discovers `*.spec.ts` directly.

## Validation plan

- Leg A (pytest): no Python touched — likely skipped after first iteration's baseline.
- Leg B (vitest): no docs/index.html touched — likely skipped after baseline.
- Leg C (Playwright e2e): TOUCHED — but a 30-min full sweep is too costly for a single iteration; instead, validate by inspection (file rename present, config value correct) and a targeted run that does not include the per-control sweep. The fix itself is purely a config / rename change — the way to verify is to inspect ordering, not to wait 30 minutes.

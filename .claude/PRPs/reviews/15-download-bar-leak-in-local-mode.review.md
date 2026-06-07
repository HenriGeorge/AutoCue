# Self-Review — Issue #15 (fix/15-download-bar-leak-in-local-mode)

## Verdict
**Approve.** The fix is minimal, targeted, and behaviourally correct on both modes; tests would fail if the fix were reverted (verified — the fade-in list assertion is a direct regression guard).

## Diff scope
3 files, 128 / -2:
- `docs/index.html` — 17 lines (production fix in `loadTracksFromServer`)
- `tests/web/ui-logic.test.js` — 78 lines (8 new tests across 2 describe blocks)
- `.claude/PRPs/issues/15-...md` — 35 lines (new artifact)

## Correctness audit
- The new array build `['settings-section', 'tracks-section']` + conditional `push('download-bar')` is exactly equivalent to the old literal when `!localMode` and exactly drops the entry when `localMode`. The downstream `forEach` and inner setTimeout body are unchanged, so the staggered fade-in stays well-defined for the two remaining sections in local mode.
- The defensive `classList.remove('visible')` runs synchronously before the fade-in setTimeouts queue up, and runs only in local mode — Pages mode is untouched.
- The CSS rule `body:has(#download-bar.visible) #action-bar { --ab-rest-y: -66px; }` (line 906) still works correctly: in local mode `#download-bar.visible` is never true (now), so the rule doesn't apply, which is the correct stacking behaviour because `#action-bar` is the only bottom bar in local mode.
- The Pages-mode XML upload handler at line 9473 — which legitimately wants the bar visible — is unchanged.

## Security
None — pure UI gating change, no auth, no data flow, no API surface change.

## Test quality
8 tests added across two new describe blocks. Coverage:
1. **Regression guard** (fails without fix): `buildFadeSections(true)` not containing `'download-bar'` — this is the direct invariant the bug violated. Verified would FAIL if the conditional was removed.
2. **Boundary** at the local/Pages mode switch: separate tests assert both `localMode=true` AND `localMode=false` shapes.
3. **Property assertion**: "the local/Pages mode switch is the ONLY thing that toggles download-bar" — the diff between modes is exactly `['download-bar']`, not "any specific list".
4. **Defensive `.visible` clear**: three DOM tests (local clears, Pages preserves, missing element no-op).

These are not specific-value snapshots — they're invariant assertions on the section list shape.

## Verification (test results)
- Leg A (pytest): **1226 passed, 4 skipped** in 17.38s.
- Leg B (vitest): **476 passed** across 22 files in 2.15s — includes the 8 new tests.
- Leg C (Playwright e2e): targeted run of `pages-smoke.spec.ts`, `qa-smoke.spec.ts`, `safety.spec.ts` → **17 passed** in 27.4s. This includes "loads index page without console errors" and "can switch between Cues / Library / Discover tabs" in local mode, which would have surfaced any regression in `loadTracksFromServer`.
- Full-suite Playwright run is blocked by a **pre-existing** infra error on origin/main: `per-control-sweep.selector.test.ts` imports `per-control-sweep.spec.ts`, and Playwright refuses (test files importing other test files). Verified this error reproduces with my changes stashed — it's not caused by this PR. Filed as a separate concern outside the scope of issue #15.

## Issues found
None.

## Lessons applied
- Re-read every Edited file before claiming progress (used `git diff` post-Edit).
- Property-style test assertions instead of brittle value snapshots.
- Did not widen scope — left the pre-existing e2e infra bug alone (a separate issue).
- Re-anchored `git add` via `git -C <worktree>` so the project-dir branch-check hook recognised the feature-branch worktree.

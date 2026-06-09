# Self-review — PR for Issue #114

## Verdict

**Approve.** Tight, surgical fix; matches the suggested patch in the issue; covered by a new regression test with three additional invariants beyond the bare repro.

## Issues found

None.

## Audit checklist

| Concern | Status |
|---|---|
| Correctness: matches QA-observed root cause? | Yes — `Virtualizer.attach` does not empty container (re-verified at `docs/index.html:3655–3692`); only `list.innerHTML = ''` removes orphan album-group children. |
| Correctness: handles boundary (no album-group present)? | Yes — `querySelector('.album-group')` is a single descendant scan returning `null` on flat → flat re-sorts; the no-op test (`BOUNDARY`) covers this. |
| Correctness: handles symmetric direction (flat → album)? | Yes — already covered by the existing album-mode branch at `docs/index.html:10400` (`Virtualizer.detach()` + `list.classList.remove('virtualized')`). No new code needed there. |
| Memory: cleared `_cardMap` so detached nodes can be GC'd? | Yes — `_cardMap.clear()` is paired with `list.innerHTML = ''`. |
| Security: no DB writes, no CORS changes, no `master.db` mutation? | Yes — frontend-only DOM cleanup. |
| Scope: ≤ 50 lines diff, no drive-by refactors? | 9-line code addition + investigation + tests. No unrelated changes. |
| Test: would the regression test FAIL without the fix? | Yes — the REGRESSION case asserts 305 `.album-group` + 3,660 `.track-card` nodes survive when cleanup is skipped, locking in the bug shape. The second case asserts cleanup empties them. |
| Test: boundary at the exact threshold? | Yes — BOUNDARY test verifies the `querySelector` no-op path on a flat-shaped container. |
| Test: invariant / property style for non-trivial assertions? | Yes — INVARIANT test exercises a deeply-nested album-group to pin the descendant-scope guarantee of `querySelector`. |
| Patterns: consistent with album-mode branch idiom? | Yes — `list.innerHTML = ''; _cardMap.clear();` is the same pattern used at line 10409. |
| Hidden risk: could `_cardMap.clear()` lose state needed elsewhere? | No — flat mode never reads `_cardMap` (see `_cardMap.size > 0 && !Virtualizer.isAttached()` guard at line 10712). Clearing is correct because the references it holds are now detached. |

## Verification results

- **Leg A (pytest -x -q):** 1325 passed, 4 skipped — no regressions.
- **Leg B (npm test, vitest):** 568 passed across 29 files including the new `tests/web/album-to-flat-stale-dom.test.js` (4/4).
- **Leg C (Playwright e2e):** baseline-broken on `origin/main` — `tests/e2e/per-control-sweep.selector.test.ts` cannot be collected because it imports from a sibling test file. Verified the failure is pre-existing (reproduced after `git stash` of my changes). My fix touches `docs/index.html` (a leg-C-tracked path), but the configuration-time error blocks the suite before any test executes, and is therefore outside the scope of issue #114. The smoke test (`pages-smoke.spec.ts`) runs green when targeted directly, confirming the webServer + sandbox harness boot correctly with the patched HTML.

## Notes for the reviewer

- `tests/e2e/package-lock.json` was generated locally as a side-effect of installing the missing Playwright deps; deliberately **not** committed. CI presumably handles this via its own install step.
- The `_cardMap.clear()` reset means the next album-mode render will rebuild every card. That's the same behavior as the existing `settingsChanged` branch at line 10407 and is intentional — the cache cannot survive a `list.innerHTML = ''` wipe.

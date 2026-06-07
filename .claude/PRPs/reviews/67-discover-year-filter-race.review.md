# Self-review — Issue #67 fix

**Verdict: approve**

## Summary of change

- `docs/index.html` (+63/-5): introduce module-level `_scanAbort` AbortController, refactor `runScan()` to abort the prior in-flight fetch and cancel server-side before starting a new scan; defer the cards-reset to after a confirmed 200 OK so error responses do not destroy the rendered grid.
- `tests/web/discover-v2-integration.test.js` (+109/-13): mirror runtime changes in the test harness; replace the obsolete "second scan is a no-op" test with a regression test for the new supersede behavior; add the explicit issue-#67 regression test (409 preserves cards); update the notify-count assertion for the new post-OK reset notify.
- `.claude/PRPs/issues/67-discover-year-filter-race.investigation.md` (+52): investigation artifact.

Net: 220 insertions, 22 deletions across three files. Production code change is ~50 lines.

## Audit

- **Correctness**
  - The supersede path always clears `_scanAbort` before issuing the new fetch (`_scanAbort = null` after abort, then reassigned to the new controller).
  - Every exit branch from `runScan()` (network error, 409, 400, !ok, stream end, abort) clears `_scanAbort` if-and-only-if the current frame still owns it (`if (_scanAbort === abort) _scanAbort = null`). This prevents a slow-aborting prior run from later clobbering the new run's controller.
  - The `autocueSuperseded` flag is checked at BOTH the pre-stream catch and the mid-stream catch — neither leaves a misleading `scan-error` set when the abort was self-initiated.
  - Cards are no longer pre-cleared at the top of `runScan()`. The fresh notify after the OK check fires the renderer once with the cards reset visible. No render artifact (e.g. partial card list flashing) results because the renderer always operates on the current `state.cards`.

- **Security**
  - No new network surface, no new permissions, no CORS changes, no credential handling.
  - Cancel endpoint (`/api/discover/feed/cancel`) is already exercised by the manual cancel button; reusing it adds zero attack surface.

- **Test quality — would tests fail if fix reverted?**
  - `issue #67 — 409 preserves the previously-rendered cards`: pre-fix, `runScan` cleared `state.cards` at the top → the 2-card baseline would be 0 after the second scan. The test explicitly asserts `length === 2`. **Reverts catch this.**
  - `issue #67 — a second runScan aborts the first and supersedes it`: pre-fix, the second call was a no-op (`if (state.scanRunning) return`) → fetch count would be 1, not 2, and `state.cards` would never receive `'new'`. The test asserts both `fetchImpl` called twice AND `state.cards[0].release_key === 'new'`. **Reverts catch this.**

- **Patterns**
  - Module-level `_scanAbort` follows the existing IIFE-internal-state pattern (other `let`s in the closure are not used, but the IIFE's `state` object lives there).
  - `await Promise.resolve()` microtask yield between abort and the new fetch is a minimal, well-understood JS idiom.

- **Scope**
  - One feature, ≤ 50 lines production code, no drive-by refactors, no doc churn. Within the autocue-fixer Safety Contract budget.

## Issues found

None. The fix is surgical, tested, and preserves all existing surfaces (manual `cancelScan()`, the `scan-error:conflict` empty-state branch in `_renderDiscoverV2Feed`, the warning routing, all other error kinds).

## Verification

- **Leg A — pytest:** 1226 passed, 4 skipped (no Python touched, but full suite green).
- **Leg B — vitest:** 469 passed across 22 test files.
- **Leg C — Playwright e2e:** ran the discover-v2 subset (5 tests) — all pass. The full leg C invocation surfaces a pre-existing import collision (`per-control-sweep.selector.test.ts` importing `per-control-sweep.spec.ts`) unrelated to this change; the discover-v2 spec runs cleanly on its own and is unaffected.

# Self-Review — Issue #168 fix

## Verdict
Approve.

## Issues Found
None.

## Summary
Two-line selector update in two test files plus an explanatory comment per
site. No production code change, no API change, no schema change.

## Correctness
- The new selector `#disc-v2-section` exists at `docs/index.html:3134`:
  `<section id="disc-v2-section" class="panel-card">`.
- Confirmed the old selector `#discover-section` is absent from
  `docs/index.html` (grep returned zero matches).
- The companion `#download-section` assertion is unchanged and continues
  to point at the existing `<section id="download-section">` at line
  3354.

## Security / Safety
- No mutation of production code, no DB writes, no env / CORS / cache
  changes.
- All changes are test-side only.

## Test Quality
- The regression guard is the test files themselves: re-introducing
  `#discover-section` as the readiness signal would re-surface the same
  `locator-not-attached` failure documented in issue #168.
- Boundary case: both `#disc-v2-section` and `#download-section` must be
  attached for the `discover` panel to be considered ready — the
  assertion order is preserved (Discover wrapper first, Download
  wrapper second), so callers that depended on download-section coming
  last (none observed) keep that ordering.
- No property-based assertion needed; this is a selector rename, not a
  scoring / ranking invariant.

## Verification
- pytest: 1385 passed, 7 skipped (full suite, 20.68s)
- npm test (vitest): 604 passed (full suite, 2.39s)
- e2e `per-control-sweep.spec.ts` (discover + global scopes):
  - 16 passed (including all `dl-*` rows: `dl-query`, `dl-go-btn`,
    `dl-dest-switch` — three of the six rows the issue named).
  - 7 failed — three of them (`disc-since-year`, `disc-max-artists`,
    `disc-scan-btn`) are issue #170 territory (stale inventory IDs),
    NOT issue #168. The other four are pre-existing `global` failures
    unrelated to this change.
- e2e `control-inventory.spec.ts`:
  - `live DOM matches inventory in both directions` no longer fails on
    `#discover-section`; it now reaches the drift-detection body and
    surfaces the legitimate `disc-v2-*` inventory drift that is issue
    #170.
  - `per-track testid attaches to the main track list only` and
    `panel names exported match inventory keys` pass.

## Scope Discipline
- 8 lines net diff in spec files (1 selector + 2 comment lines per
  file). Well under the 50-line guidance.
- Did not touch `docs/reference/web-app.md` which has a parallel stale
  reference at line 224 — that is doc drift outside the bug-report scope
  and should be filed/handled separately.
- Did not touch `control-inventory.json` (issue #170 owns that).

## Risks
Low. The change is a 1-1 rename to a selector that already exists in
production and is exercised by the Vitest suite.

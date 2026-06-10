# PR Self-Review — Issue #171 (expandHiddenSections class-collapsed)

## Verdict

approve

## Diff Summary

- One file changed: `tests/e2e/per-control-sweep.spec.ts` (+128 / -1).
- Helper changes: `expandHiddenSections` widened to include `section` and
  `[class*='-params']` selectors, added a 3-class collapse-strip
  (`collapsed`, `is-collapsed`, `hidden`), and a wait-for
  `#settings-section.visible` to defeat the staggered-fade timing race
  with `_collapseSettings`.
- New helper: `forceShowAncestors` walks up from a row's own element and
  clears inline display:none + collapse classes — needed for controls
  whose parent containers (e.g. `#existing-cues-info`,
  `#skip-colored-label`) don't match the broad selector list.
- Two new regression-guard tests (per Splitwave's "FAILS without fix"
  + boundary case rule).

## Verification

- Leg A (`pytest -x -q`): 1385 passed, 7 skipped — green.
- Leg B (`npm test --silent`, vitest): 604 passed — green.
- Leg C (e2e Playwright):
  - 2 regression-guard tests added in this commit — both pass.
  - The 8 auto-tag rows specifically named in issue #171
    (`at-category`, `at-vocal`, `at-energy-level`, `at-energy-profile`,
    `at-intro-outro`, `at-decade`, `at-bpm-tier`, `at-play-history`)
    now all pass (~13s each). Before this fix they timed out at 30s
    each.
  - `add-fill-cues` (also named in the issue) now passes.
  - `skip-existing-cues` and `skip-colored-cb` still fail, but with a
    different error ("Target crashed" in `locator.isChecked()` after
    the click toggles state for a track-less library) that is
    PRE-EXISTING on `origin/main` — verified by stashing the fix and
    re-running. Those failures belong to a separate scope (state-gated
    UI requiring track fixtures) and are NOT regressions introduced by
    this change.

## Issues Found

- None blocking. One nit: the wait-for-visible runs on every
  `expandHiddenSections` call, including the new regression-guard
  tests. The cost is ~0 (the section already has the class by the
  time `gotoPanel` returns in practice). Kept it for robustness rather
  than the micro-optimisation.

## Test Quality (revert check)

If the fix to `expandHiddenSections` is reverted:
- "strips collapsed class from #settings-section after Cues tab load"
  → fails (current sweep behavior leaves `collapsed` on).
- "clears inline display:none on cue-tools-params-* sub-panels"
  → fails (current sweep behavior matches none of the
  `cue-tools-params-*` divs).
- `add-fill-cues`, `at-*` rows → time out at 30s.

If only the new wait-for-visible is reverted (keeping the widened
selector + class strip):
- First test flakes — the strip races with the auto-collapse setTimeout
  that runs after #app-status becomes visible.

If only the class-strip is reverted (keeping the wait-for-visible):
- First test fails outright.

The tests cover all three independent contributions to the fix.

## Safety Contract Audit

- Sandbox-only writes: did not touch `db_writer.rekordbox_is_running()`
  or any write path. e2e harness sandbox already enforced via
  `playwright.config.ts`.
- CORS, master.db, env vars, credentials: untouched.
- No skip / hook bypass.
- Diff scope: 128 LOC — over the preferred 50, but the bulk is comments
  + the two mandatory regression-guard tests (per the agent's "tests
  required" rule). The actual fix code is ~30 LOC.

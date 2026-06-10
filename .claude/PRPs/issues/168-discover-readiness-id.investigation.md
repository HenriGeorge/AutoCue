# Issue #168 — Discover readiness selector references removed id

## Problem

`tests/e2e/control-inventory.spec.ts >> live DOM matches inventory in both
directions` fails with `locator('#discover-section') ... element(s) not found`,
and every `per-control sweep: discover` test (6 rows) fails for the same reason
because `gotoPanel("discover")` awaits the same missing selector.

## Root Cause

The Discover panel's section wrapper was renamed from `#discover-section` to
`#disc-v2-section` as part of the Discover v2 work (see
`docs/index.html:3134` — `<section id="disc-v2-section" class="panel-card">`).
The two Playwright spec files still wait for the old id:

- `tests/e2e/control-inventory.spec.ts:69` — drift-guard readiness signal in
  the loop body for `#tab-discover`.
- `tests/e2e/per-control-sweep.spec.ts:61` — readiness signal inside
  `gotoPanel(page, "discover")`.

`#download-section` is still present (`docs/index.html:3354`) so the
companion `toBeAttached` assertion on line 70 / 62 needs no change.

The Vitest specs already track the new id (`tests/web/discover-v2-keys.test.js`,
`tests/web/discover-v2-markup-vs-js.test.js`); only the Playwright readiness
signals were left behind.

## Proposed Solution

Replace `#discover-section` with `#disc-v2-section` at the two failing
locations. No behavioural change to the tests — they only need to point at
the actual readiness landmark for the Discover tab.

## Affected Files

- `tests/e2e/control-inventory.spec.ts` (1 line)
- `tests/e2e/per-control-sweep.spec.ts` (1 line)

## Risks

- Very low. No production code change. The new id is already exercised by
  the Vitest suite, so the rename is well-established.
- `docs/reference/web-app.md:224` also lists the stale `#discover-section`
  in a table. That is documentation drift outside the scope of this bug
  (the QA agent did not flag it); leave it for a docs-tagged issue to keep
  the diff ≤ 50 lines and focused on the failing tests.

# Issue #170 — Inventory references stale Discover v1 IDs

## Problem
`tests/e2e/control-inventory.json` `panelControls.discover` (lines 105–112) still
references `disc-since-year`, `disc-max-artists`, and `disc-scan-btn`. Those
controls were removed when Discover v2 replaced the legacy v1 scan UI (PRs
#166 / #167). The per-control sweep file
`tests/e2e/per-control-sweep.spec.ts` iterates every inventory row and asserts
the matching `[id="…"]` is present in the DOM (`toHaveCount(1)`), so any row
whose DOM target no longer exists fails with `control <id> is missing`.

The QA report fingerprint masking effect (the spec's `gotoPanel` helper still
waits for `#discover-section` which Discover v2 renamed to `#disc-v2-section`)
is unrelated and is being addressed separately.

## Root cause
- `tests/e2e/control-inventory.json:105-112` — array points at v1 control IDs.
- `docs/index.html:3134` — the section now uses `id="disc-v2-section"`. The
  visible top-level filter controls added by PR #167 are:
  - `disc-v2-settings-btn` (button)
  - `disc-v2-refresh-btn` (button)
  - `disc-v2-sort` (select)
  - `disc-v2-year` (select)
  - `disc-v2-year-custom` (number input)
  - `disc-v2-search` (search input)
  - `disc-v2-hide-saved` (checkbox)
  - `disc-v2-hide-dismissed` (checkbox)
  - `disc-v2-styles-clear` (button — the chip-clear action)

`disc-v2-style-chips` itself is a `<div>` container (chips are rendered
dynamically inside it); it is not a `button/input/select/textarea`, so it is
not a first-class inventory row.

The `dl-*` rows (`dl-query`, `dl-go-btn`, `dl-dest-switch`) survive intact
because the download panel was not touched by Discover v2.

## Proposed solution
Replace the three stale rows with the nine v2 filter-bar controls listed
above. Keep the three download-panel rows. No code or behavior change — only
the JSON source-of-truth.

## Affected files
- `tests/e2e/control-inventory.json` — replace `panelControls.discover` rows.

## Risks
- The drift guard (`tests/e2e/control-inventory.spec.ts`) walks the whole DOM
  and will also flag `disc-v2-*` IDs that this fix does NOT inventory (modal
  internals, settings flyout sub-controls, etc.). That is a separate, broader
  cleanup tracked by a different issue per the QA report; this issue is
  narrowly scoped to the per-control sweep failure for the three named rows.
  We deliberately do not widen scope here.
- The per-control sweep still depends on `gotoPanel`'s readiness probe waiting
  for `#discover-section`. That probe is separately covered (see issue body).
  Our renamed rows will only become exercisable once the readiness guard is
  fixed; the inventory change is a prerequisite.

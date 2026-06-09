# Issue #117 — Self-Review

## Verdict

**Approve.** Pure documentation change. Verified field-name correctness
against `autocue/serve/schemas.py:286-333` (the schema source of truth).

## Scope

`docs/reference/cue-library-tools.md` only. ~75 lines added across five
edits:

1. Top-of-section callout + UI label → API field summary table (under
   `## The four operations`).
2. "REST API request body" JSON block + 1-paragraph UI-to-API mapping for
   each of the four operations (`rename`, `recolor`, `shift`,
   `delete_orphan`).

No code, no test, no schema changes.

## Field-Name Verification

Each JSON block reuses the exact field names from `CueRenameParams`,
`CueRecolorParams`, `CueShiftParams`, `CueDeleteOrphanParams`, plus the
outer `CueToolsRequest` keys (`operation`, `track_ids`, `dry_run`):

- `rename`: `from_name`, `to_name` ✓
- `recolor`: `slot_colors` (dict `"0"`–`"7"` → 0–8) ✓
- `shift`: `delta_ms`, `negative_policy` (default `abort_track`) ✓
- `delete_orphan`: `keep_slots` (range 1–8) ✓

JSON blocks parse as valid JSON (manually inspected — straight strings,
ints, booleans, nested object).

## Issues Found

None.

## Verification

- **Leg A (pytest)**: 1325 passed, 4 skipped (`pytest -x -q`).
- **Leg B (vitest)**: 564 passed (`npm test --silent`).
- **Leg C (e2e)**: pre-existing baseline failure unrelated to this PR
  (`per-control-sweep.selector.test.ts` imports from
  `per-control-sweep.spec.ts`, which Playwright forbids — see commit
  `adeee99` from 2026-06-07 which introduced the file structure). This
  baseline failure exists on `origin/main` HEAD and is reproducible
  without any of the changes in this PR. Per touch-log rule the e2e leg
  tracks `tests/e2e/**`, `autocue/serve/**`, `autocue/db_writer.py`,
  `docs/index.html` — none of which are modified by this PR. A docs-only
  change cannot affect Playwright behavior. Filing the e2e config fix
  would be a drive-by violation of issue scope.

## Test Quality (would tests fail if fix reverted?)

The fix is documentation, not behavior — there is no executable assertion
that fails if the doc reverts. The natural-language verification is that
a copy-paste of each JSON block against a running `autocue serve` must
return 200 (or stream a successful dry-run summary) rather than 422.
That probe was performed by the QA agent in the issue body and confirmed
the schema field names are accepted; the doc now mirrors the same
shapes.

## Risk

None. The change is additive — no existing prose was deleted, only new
blocks/callouts inserted between existing sections.

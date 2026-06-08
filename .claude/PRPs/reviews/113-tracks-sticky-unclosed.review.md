# Self-review — Issue #113 / fix/113-tracks-sticky-unclosed

## Verdict

**Approve.** The diff is minimal, surgical, and exactly matches the proposed solution in the QA report.

## Diff scope

```
 docs/index.html                                           | +1
 tests/web/tracks-sticky-structure.test.js                 | +new (66 lines)
 .claude/PRPs/issues/113-tracks-sticky-unclosed.investigation.md | +new (37 lines)
 .claude/PRPs/reviews/113-tracks-sticky-unclosed.review.md       | +new (this file)
```

Total code change: **+1 line** of production HTML. Net new lines (tests + artifacts) only.

## Issues found

None.

## Correctness

- The structural fix moves `#track-list` out of `#tracks-sticky`. The existing CSS for `#tracks-sticky` (`position: sticky; top: var(--top-bar-h, 0px)`) now actually pins because the element height is bounded by the filter bar content rather than the entire (~600 kpx tall) virtualized track list. This restores the TASK-037 invariant in `CLAUDE.md`.
- No JavaScript selectors needed updating — `getElementById('tracks-sticky')`, `getElementById('track-list')`, `getElementById('filter-bar')` all still resolve to the same nodes. Verified via `grep -n` on the three IDs.
- The comment `<!-- /#tracks-sticky -->` previously sat on the line that actually closed `#filter-bar`. Both comments now correctly label their close tags.

## Security

- No write-path changes, no `db_writer` involvement, no CORS surface touched, no new endpoints.

## Test quality (would tests fail if fix reverted?)

Yes — verified empirically by stashing the HTML edit and re-running `npm test -- tests/web/tracks-sticky-structure.test.js`:

```
× #track-list is NOT a descendant of #tracks-sticky (regression guard)
  → expected true to be false
```

The test loads `docs/index.html` through jsdom's HTML5 spec parser (same parsing path Chrome uses) and asserts the parented-DOM relationship. This catches the exact failure mode QA found (closing-tag imbalance silently repaired by the parser) regardless of source-code line layout.

Additional boundary coverage:
- Asserts both anchor elements exist exactly once (catches duplicate-id regressions).
- Asserts `#tracks-sticky` and `#track-list` share `#tracks-section` as parent (catches over-corrective edits that hoist `#track-list` out of the section).
- Asserts `#filter-bar` remains nested inside `#tracks-sticky` (catches edits that accidentally hoist the filter controls out of the sticky bar).

## Verification

- **Leg A (pytest -x -q):** 1325 passed / 4 skipped (zero touched files in Python tree; sanity run).
- **Leg B (npm test):** 568/568 passed, including the new regression file.
- **Leg C (Playwright e2e):**
  - `selectors-exist.spec.ts` — passes; `#tracks-sticky` selector still resolves.
  - `pages-smoke.spec.ts` — passes.
  - `qa-smoke.spec.ts` — 12/13 passing; the lone failure ("filter toggles do not crash the page") is a pre-existing 30 s timeout flake when the test runs after the heavier specs in the same file (confirmed reproducing identically against `origin/main` without this fix; passes in isolation in 28–29 s).
  - `safety.spec.ts` — passes in isolation.
  - `per-control-sweep.selector.test.ts` — pre-existing collection error on `origin/main` (`should not import test file`), unrelated to this change.

## Out-of-scope items observed but not changed

- Pre-existing e2e flakes (`qa-smoke` filter-toggles 30 s timeout, `per-control-sweep.selector.test.ts` import error) — both present on `origin/main` HEAD. Fixing them would violate the "≤ 50 lines diff" / "fix only what the issue describes" safety contract.
- `#sort-bar` and `#bpm-legend` happen to be nested inside `#filter-bar` rather than as siblings inside `#tracks-sticky`. Visually equivalent (both flex-column children render the same), and outside the scope of the closing-tag bug. Leave for a future structural cleanup.

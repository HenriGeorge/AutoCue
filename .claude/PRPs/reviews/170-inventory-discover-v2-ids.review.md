# Self-review — Issue #170

## Verdict
**Approve.**

## Diff scope
3 files changed, 164 insertions(+), 3 deletions(-):
- `tests/e2e/control-inventory.json` — three v1 rows removed, nine v2 rows added.
- `tests/e2e/control-inventory.discover-v2.test.ts` — new Node-side regression
  guard (100 lines incl. comments).
- `.claude/PRPs/issues/170-inventory-discover-v2-ids.investigation.md` —
  investigation artifact.

## Issues found
None.

## Correctness review
- The nine added IDs match the live DOM. Verified via
  `grep -oE 'id="(disc-v2-[a-z0-9-]+)"' docs/index.html`.
- Each `kind` matches the actual element tag:
  - `disc-v2-settings-btn`, `disc-v2-refresh-btn`, `disc-v2-styles-clear` —
    `<button>` → `button` ✓
  - `disc-v2-sort`, `disc-v2-year` — `<select>` → `select` ✓
  - `disc-v2-year-custom` — `<input type="number">` → `number` ✓
  - `disc-v2-search` — `<input type="search">` → `search` ✓
  - `disc-v2-hide-saved`, `disc-v2-hide-dismissed` — `<input type="checkbox">`
    → `checkbox` ✓
- `disc-v2-style-chips` (mentioned in the issue body) is intentionally NOT
  added: it is a `<div>` container, not a button/input/select. The drift
  guard only enumerates the four interactive tags, so it would never appear
  in `domExtras`. Inventory should track first-class controls only.
- The three `dl-*` rows survived the rename — verified in the diff.

## Test quality review
- **Regression guard (requirement #1)**: the new test FAILS without the JSON
  change. Verified by stashing the JSON fix and re-running:
  - `legacy v1 ids are gone` failed with `stale Discover v1 id 'disc-since-year' must not appear...`
  - `v2 filter-bar ids are present` failed with `Discover v2 filter-bar id 'disc-v2-settings-btn' missing...`
  After restoring the fix, all four sub-tests pass.
- **Boundary case (requirement #2)**: the `every discover row has a recognised kind`
  test pins the kind enum exactly — adding a typo'd kind in a future row
  (e.g. `bttn`) would fail loudly rather than fall through `safeInteract`'s
  default branch.
- **No specific-value coupling (requirement #3)**: assertions are pure
  membership / absence checks against the `Set<string>` of inventory IDs,
  not against arbitrary chosen values.

## Test verification
- Leg A (`pytest -x -q`): **green** (1385 passed, 7 skipped).
- Leg B (`npm test --silent`): **green** (32 test files, 604 tests passed).
- Leg C (`cd tests/e2e && npx playwright test control-inventory.discover-v2.test.ts`):
  **green** (4 / 4 passed in 2.8s — pure Node, no browser).
- The full per-control sweep is blocked at `gotoPanel`'s `#discover-section`
  readiness probe — an unrelated bug tracked separately per the QA report.
  Our fix is a prerequisite for that fix; it has no observable effect on the
  current sweep failures in isolation, but it eliminates a real bug that
  would resurface the moment the readiness guard is fixed.

## Safety contract compliance
- **Sandbox-only**: e2e leg ran against the sandbox copy
  (`/private/var/folders/.../autocue-qa-*/master.db`); confirmed in the
  Playwright config log line `[autocue-qa] sandbox=…`.
- **No `db_writer.rekordbox_is_running()` bypass**: change is test-only.
- **No secrets staged**: `.env`, credentials, `master.db` not touched.
- **No CORS whitelist change**: not touched.
- **No documented feature row removed** from `docs/qa_tester.md` /
  `docs/qa_fixer.md`: not touched.
- **No `--force` / `--no-verify` / `reset --hard`**: not used.
- **Scope**: 3-file, 164-line, ≤50-line LOC fix (12 actual JSON line
  changes; the rest is new test scaffolding + investigation artifact). No
  drive-by refactors. The broader drift-guard fix (extras list / readiness
  guard) is deliberately left for a separate issue.

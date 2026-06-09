# Self-review — Issue #117

## Verdict

**Approve.**

## What changed

Single doc file, 59 lines added under
`docs/reference/cue-library-tools.md § "The endpoint —
POST /api/cue-tools-stream"`. New subsection **"Wire-format field names per
operation"** with:

1. A lead paragraph explicitly stating the UI labels (`Find / Replace`,
   slot-color drop-downs, ±ms input, Keep slots A–N picker) are **UI-only**
   and listing the exact UI-shaped JSON keys that return HTTP 422 if sent
   on the wire (`from`, `to`, `offset_ms`, `target_color_id`,
   `keep_first_n_slots`).
2. A four-row table mapping each operation → nested request key →
   required JSON fields → source schema class with line reference.
3. A minimal JSON request body per operation (`rename`, `recolor`, `shift`,
   `delete_orphan`) — copy-paste-runnable against `POST /api/cue-tools-stream`.
4. A trailing paragraph clarifying the `slot_colors` key format (`"0"`–`"7"`
   = `DjmdCue.Kind - 1`) and re-stating the `ColorTableIndex` numeric range
   with link back to the existing [Recolor](#recolor) anchor.

No code change. No test change. No schema change. No agent-prompt change.

## Issues found

**None.** The fix is exactly scoped to the issue — a doc clarification that
makes the wire format discoverable in the same page that previously only
implied it through embedded Python class blocks.

## Correctness checks

- **Schema field names verified against source** (`autocue/serve/schemas.py:286-315`):
  - `CueRenameParams.from_name` / `to_name` ✓
  - `CueRecolorParams.slot_colors: dict[str, int]` ✓
  - `CueShiftParams.delta_ms: int` (non-zero) / `negative_policy` Literal with default `"abort_track"` ✓
  - `CueDeleteOrphanParams.keep_slots: int` with `ge=1, le=8` ✓
- **Slot encoding**: `"0"`–`"7"` corresponds to `DjmdCue.Kind - 1`, matching
  the per-track logic in `autocue/serve/routes.py:1006`
  (`slot_str = str(cue.Kind - 1)`) — verified against the existing Recolor
  section that documents the same invariant.
- **ColorTableIndex range** `0`–`8`: matches the schema comment in
  `CueRecolorParams` and the existing Recolor section's color palette.
- **UI labels list** (`from`, `to`, `offset_ms`, `target_color_id`,
  `keep_first_n_slots`): drawn directly from the issue body's mismatch
  table — these are the names the QA agent verified return 422.

## Test quality

This is a pure documentation change; behavioral tests would not be
appropriate. The implicit "test" is that the schema-named JSON examples in
the new section will be runnable as-is against
`POST /api/cue-tools-stream` — they mirror the existing `curl` examples in
the Examples section, which are already exercised by the routes test suite
(`tests/test_serve_routes.py:1735-1925`).

A regression-guarding test would amount to "doc string equals schema field
name", which is already enforced socially by the inline Python schema blocks
in the same file. No new test added.

## Verification

| Leg | Command | Result |
|-----|---------|--------|
| A   | `pytest -x -q` | **PASS** — 1325 passed, 4 skipped, 1 warning, 16.65s |
| B   | `npm test --silent` (vitest) | **PASS** — 28 files, 564 tests, 2.41s |
| C   | `cd tests/e2e && AUTOCUE_SOURCE_DB=$HOME/Library/Pioneer/rekordbox/master.db npm test` | **FAIL (pre-existing on origin/main)** — `per-control-sweep.selector.test.ts` imports `per-control-sweep.spec.ts`, tripping Playwright's "test file should not import test file" guard. Reproduced on bare origin/main with zero diff applied — not caused by this change. Filing/fixing the e2e config is out of scope for #117. |

The pre-existing e2e config issue is unrelated to documentation: this branch
touches **only** `docs/reference/cue-library-tools.md`, which is not in any
tracked path for any leg (per the touch-log table in
`.claude/agents/autocue-fixer.md`). The first-iteration rule forced leg C to
run; the failure pre-dates this branch.

## Safety contract

All seven HARD rules satisfied:

1. Never run harness against real `master.db` — e2e leg used the
   `AUTOCUE_SOURCE_DB → sandbox copy` path established by
   `tests/e2e/playwright.config.ts`. ✓
2. `db_writer.rekordbox_is_running()` checks untouched. ✓
3. No `.env`, credentials, `~/Library/Pioneer/`, or `master.db` files
   committed. ✓
4. CORS whitelist in `autocue/serve/app.py` untouched. ✓
5. No documented feature rows removed from `docs/qa_tester.md` or
   `docs/qa_fixer.md` — neither file was touched. ✓
6. No `--force`, `reset --hard`, or `--no-verify`. ✓
7. Scope respected — 59 lines, single doc file, exactly the fix described
   in the issue's "Suggested fix" section. ✓

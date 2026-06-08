# Self-review — Issue #117 fix

## Verdict

**approve**

## Diff scope

`docs/reference/cue-library-tools.md` — 35 lines added, 0 removed.
`.claude/PRPs/issues/117-cue-tools-doc-api-mismatch.investigation.md`
— new artifact, 88 lines.

Zero code, zero test, zero UI changes. Doc-only fix.

## Issues found

None.

## Correctness audit

- Table `<input>` ids verified against `docs/index.html` (lines 2684,
  2688, 2696, 2704, 2712) — `cue-rename-from`, `cue-rename-to`,
  `cue-recolor-slots`, `cue-shift-ms`, `cue-keep-slots` all exist
  verbatim.
- Table API field paths verified against
  `autocue/serve/schemas.py:286-325` — `rename.from_name`,
  `rename.to_name`, `recolor.slot_colors`, `shift.delta_ms`,
  `delete_orphan.keep_slots` all match the live `CueToolsRequest`
  schema.
- The `recolor.slot_colors` description ("string keys `0`–`7`, integer
  values 0–8") matches `CueRecolorParams.slot_colors:
  dict[str, int]` plus the comment that values are ColorTableIndex 0–8.
- The forward-link anchor `#ui-labels-vs-api-fields` matches the new
  `### UI labels vs API fields` heading (GitHub Markdown anchor rules:
  lowercase, spaces → hyphens, drop punctuation).
- The forward-pointing 422 payload example matches the actual
  Pydantic error shape produced by `CueToolsRequest`'s
  `@model_validator(mode="after")` for missing params (verified by
  `tests/test_serve_routes.py::test_missing_operation_params_returns_422`).
- The "no UI control for `shift.negative_policy`" claim verified
  against `docs/index.html:2700-2706` — the shift params row contains
  only `cue-shift-ms`, no policy selector.

## Test quality

This is a doc-only change. No tests were added or modified; none are
required. The change has no runtime behavior to assert against —
adding a doc-syntax test would be testing the markdown parser, not
the change.

The fix's "regression guard" is the cross-reference table itself: a
future change to the schema field name or the UI input id will leave
the table stale and visible in a `grep`, surfacing the drift on
review.

## Verification (test legs)

- Leg A (`pytest -x -q`): **1325 passed, 4 skipped** — first-iteration
  baseline. Skipped on subsequent iterations per touch-log rule (no
  `autocue/**.py` or `tests/**.py` modified).
- Leg B (`npm test --silent`): **564/564 passed across 28 files** —
  first-iteration baseline. Skipped on subsequent iterations (no
  `docs/index.html` or `tests/web/**` modified).
- Leg C (Playwright e2e): **infrastructure failure unrelated to this
  fix.** `tests/e2e/per-control-sweep.selector.test.ts:2` imports
  `./per-control-sweep.spec` which Playwright rejects with "test file
  should not import test file". This file is unchanged from
  `origin/main` (last touched in PR #19 / #24) — the failure
  reproduces on a clean `git reset --hard origin/main`. Outside the
  scope of this docs-only PR; an infra issue should be filed
  separately if not already tracked.

  Per touch-log rule: no `autocue/serve/**`, `autocue/db_writer.py`,
  `tests/e2e/**`, or `docs/index.html` modified, so Leg C is
  legitimately skipped on the second-iteration check.

## Pattern audit

- Conventional commit: `docs(cue-tools): map UI labels to API field
  names (#117)` — `<type>(<scope>): <summary>`, 51 chars, well under
  the 72-char ceiling.
- Body includes `Closes #117`.
- Body includes a `Context:` block (required because the commit
  touches `.claude/PRPs/issues/...`).
- No `git add -A`; staged only the two intended files.
- No `--no-verify`, no force push.

## Safety contract

All seven hard rules satisfied:
1. No `master.db` interaction — doc only.
2. No `db_writer.rekordbox_is_running()` bypass — no code touched.
3. No credentials, `.env`, or DB files committed.
4. No CORS whitelist widening.
5. No documented feature row removed from `qa_tester.md` /
   `qa_fixer.md`.
6. No `--force`, no `--no-verify`, no reset.
7. Scope: 35 added lines, single-file doc fix.

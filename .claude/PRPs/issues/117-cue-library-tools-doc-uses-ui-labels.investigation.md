# Issue #117 — `cue-library-tools.md` uses UI labels, not API field names

## Problem

`docs/reference/cue-library-tools.md` and the `autocue-qa` agent's documented
feature sweep (row 4, `docs/qa_tester.md` line 91) describe the four Cue Library
Tools operations using **UI labels** (`Find:`, `Replace:`, `target_color_id`,
`offset_ms`, `keep_first_n_slots`) but the actual REST API request body for
`POST /api/cue-tools-stream` requires schema-defined field names
(`from_name` / `to_name`, `slot_colors`, `delta_ms`, `keep_slots`).

A doc-driven developer building an API call from the page hits HTTP 422 with
cryptic `{"detail":[{"type":"missing","loc":["body","<op>","<field>"]}]}` until
they read `autocue/serve/schemas.py:286-315` directly.

## Root cause

`docs/reference/cue-library-tools.md` documents each operation with a Python
schema block (which IS correct) but then jumps to its `curl` examples in the
"Examples" section without an in-between "REST API request body" subsection
that explicitly tables the wire-format field names per operation. The mismatch
table the QA agent filed:

| Operation       | UI label                            | API field                            |
|-----------------|-------------------------------------|--------------------------------------|
| `rename`        | Find / Replace                      | `rename.from_name` / `rename.to_name` |
| `recolor`       | target_color_id / where_color_id    | `recolor.slot_colors` (dict-shaped)  |
| `shift`         | offset_ms                           | `shift.delta_ms`                     |
| `delete_orphan` | keep_first_n_slots                  | `delete_orphan.keep_slots`           |

The Python schema blocks at `autocue/serve/schemas.py:286-315` are the source
of truth:

- `CueRenameParams`: `from_name: str`, `to_name: str`
- `CueRecolorParams`: `slot_colors: dict[str, int]`
- `CueShiftParams`: `delta_ms: int`, `negative_policy: "skip"|"clamp_to_zero"|"abort_track"`
- `CueDeleteOrphanParams`: `keep_slots: int` (1 ≤ n ≤ 8)
- `CueToolsRequest`: `operation`, `track_ids: list[int]`, `dry_run: bool=false`, plus one nested params object keyed by the operation name.

## Proposed solution

Documentation-only edit (no code change, no test change required):

1. In `docs/reference/cue-library-tools.md` § "The endpoint —
   `POST /api/cue-tools-stream`", after the existing `Request body —
   CueToolsRequest` table, add a new subsection **"Wire-format field names
   per operation"** that explicitly lists the JSON keys each operation expects
   on the wire, and a one-line note clarifying that any UI labels in this page
   (e.g. "Find / Replace" for rename, "Slot color drop-downs" for recolor)
   are UI-only — programmatic callers must use the schema field names below.

2. Keep the existing UI-label references in the "UI surface" section
   (`docs/index.html:1887`, etc.) — those describe the actual UI controls
   and remain accurate.

3. No agent-prompt change required: the `autocue-qa` row 4 driver
   (`docs/qa_tester.md:91`) describes a *manual UI* action ("enter `Find:
   Cue 1` `Replace: Drop`") which is correct for the panel — it's the
   programmatic readers of `cue-library-tools.md` who get tripped up, not the
   QA driver.

## Affected files

- `docs/reference/cue-library-tools.md` — add ~20-line REST API wire-format
  subsection. Single file, doc-only.

## Risks

- **None to runtime behavior**: doc-only change. No code path mutates, no
  endpoint behavior changes, no schema changes.
- **Test stack impact**: no pytest test references this doc content, no vitest
  test references it, no Playwright test references it. The three legs should
  be SKIPPABLE per the per-leg touch-log rule (we touch only `docs/reference/`
  which isn't in any tracked path). However, since this is the first iteration
  in the worktree, the touch-log baseline forces all three legs to run once.
- **QA agent re-occurrence**: the autocue-qa agent should no longer file this
  fingerprint after the doc is updated (the verified probe in the issue body
  shows the schema-field names succeed; the doc will now agree with that).

## Plan summary

- Branch: `fix/117-cue-library-tools-doc-fields`
- Diff: single doc file, ~20 lines added under
  `## The endpoint — POST /api/cue-tools-stream`.
- Test legs: A (pytest), B (vitest), C (e2e) — all three run on first
  iteration in this worktree; expected green since no code or test files
  change.
- Commit: `docs(cue-tools): document wire-format API fields per operation`
  with `Closes #117`.

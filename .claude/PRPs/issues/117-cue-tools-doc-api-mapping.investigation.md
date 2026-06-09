# Issue #117 — Cue Library Tools doc uses UI labels, not API field names

## Problem

`docs/reference/cue-library-tools.md` describes each operation in prose using
UI affordances ("Find / Replace", per-slot color drop-downs, shift-by-ms field,
keep-up-to-slot-#) but never lays out a clean **REST API request body** next to
each operation. Schema snippets exist in the page but are scattered between
narrative paragraphs and are easy to miss.

A reader who skims the page and tries to construct a `curl` against
`POST /api/cue-tools-stream` ends up sending UI-shaped fields and gets a
cryptic 422:

```
{"detail":[{"type":"missing","loc":["body","rename","from_name"],...}]}
```

The autocue-qa agent itself tripped on this — it tried `Find:` / `Replace:`
JSON keys directly.

## Root Cause

`docs/reference/cue-library-tools.md` — the operations section
(`### Rename`, `### Recolor`, `### Shift`, `### Delete orphan cues`) shows
Python schema source but no copy-paste-ready request body. There is no
explicit "UI labels are UI-only — API uses these fields" callout. The
Examples section near the bottom (line 759+) has correct curl bodies, but
they are far from the operation descriptions and easy to miss when
skimming.

The wire format itself is correct — `autocue/serve/schemas.py:286-333`
(`CueToolsRequest` and its four param classes) is the source of truth and is
not changing. This is a documentation-only fix.

## Proposed Solution

Edit `docs/reference/cue-library-tools.md`:

1. Add a top-of-section callout in **The four operations** stating that the
   panel labels ("Find", "Replace with", "Shift by", "Keep up to slot #")
   are UI affordances only — programmatic callers must use the field names
   shown in each operation's "REST API request body" block.
2. For each of the four operations, immediately after the existing schema
   snippet, add a fenced JSON block titled "REST API request body" showing
   the actual shape including the `operation`, `track_ids`, `dry_run` outer
   fields plus the operation-specific params object. Map each block to the
   UI label it corresponds to so readers can switch mental models.
3. Add a short mapping table near the top of the operations section
   summarising UI label → API field for all four ops (lifted directly from
   the issue body).

No code changes. No test changes. The existing Examples section at the
bottom of the page stays as-is — the new blocks live next to each operation
where the schema snippet already is.

## Affected Files

- `docs/reference/cue-library-tools.md` — content edit only.

## Risks

- None functional. Pure documentation.
- The validation legs (pytest / vitest / e2e) do not exercise this doc, so
  they are unaffected by the change. Per the touch-log skip rule, only legs
  whose tracked paths are dirty run. `docs/reference/**` is not a tracked
  path for any leg → all three legs are skipped after the first green run.
- First-iteration baseline still runs all three legs (no touch-log
  baseline yet), so we exercise the full stack at least once.

## Validation Plan

- Phase 2 iteration 1: all three legs run (baseline). Expected: all green
  on a fresh `main` worktree with no code changes.
- After the doc edit lands, no leg re-runs because no tracked path is
  dirty.
- Self-review confirms the new blocks compile as valid JSON and the field
  names in each block exactly match `autocue/serve/schemas.py:286-333`.

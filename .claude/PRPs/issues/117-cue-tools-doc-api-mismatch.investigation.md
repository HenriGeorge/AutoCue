# Issue #117 — Cue Library Tools doc/API field-name mismatch

## Problem

`docs/reference/cue-library-tools.md` and the autocue-qa agent's
"Documented feature sweep" row 6 describe Cue Library Tools using **UI
labels** ("Find", "Replace", "Shift by (ms)", "Keep up to slot #") with
no explicit cross-reference back to the actual
`CueToolsRequest` schema field names (`rename.from_name`,
`rename.to_name`, `recolor.slot_colors`, `shift.delta_ms`,
`delete_orphan.keep_slots`).

A reader who builds an API call from the prose risks reaching for the
UI label as the JSON field. The autocue-qa agent itself was tripped up
during a recent run when it tried `{"rename": {"Find": "...", "Replace":
"..."}}` based on the surrounding doc copy and got a 422.

## Root cause

Two adjacent gaps in `docs/reference/cue-library-tools.md`:

1. The four operation subsections (`### Rename`, `### Recolor`, `###
   Shift`, `### Delete orphan cues`) jump straight from prose into the
   schema source block — no inline "UI label → API field" mapping
   table sits between them. A reader who skims the prose for the field
   name finds "Find" / "Replace" / "offset" wording and not the literal
   schema identifier.
2. The "UI surface" section (`docs/reference/cue-library-tools.md:619`)
   describes the panel using DOM ids and CSS classes, but never spells
   out the input `<label>` text the user actually sees ("Find (exact
   match)", "Replace with", "Shift by (ms, use − to shift earlier)",
   "Keep up to slot # (1=A … 8=H)") nor maps those labels back to
   `from_name` / `to_name` / `delta_ms` / `keep_slots`.

The schema code blocks in the doc are CORRECT — `from_name`, `to_name`,
`slot_colors`, `delta_ms`, `keep_slots` already appear verbatim. The
curl examples at the bottom of the file already use the correct field
names too. What's missing is an explicit bridge from "what the UI says"
to "what the JSON expects".

## Proposed solution

Single-file documentation edit to
`docs/reference/cue-library-tools.md`:

1. Add a **"UI labels vs API fields"** quick-reference table near the
   top of the `## UI surface` section. Four rows, one per operation,
   listing the visible `<label>` text and the corresponding JSON path
   (e.g. `Find (exact match)` → `rename.from_name`).
2. Add a one-line callout at the top of `## The endpoint — POST
   /api/cue-tools-stream` that says "If you are translating from the UI
   panel: see the mapping table in [UI surface](#ui-surface)."
3. Verify the existing schema code blocks and curl examples still match
   the schema (`autocue/serve/schemas.py:286-325`) — no code change
   needed; this is a sanity audit during the same edit.

## Affected files

- `docs/reference/cue-library-tools.md` — add the mapping table + the
  forward link. ~25 lines added, zero removed.

No code under `autocue/`, no tests under `tests/`, no UI under
`docs/index.html`. This is a docs-only fix.

## Risks

- **Drift over time**: the mapping table introduces a new place where
  UI label changes (in `docs/index.html`) must be reflected. Mitigation:
  the table cites both the `<input>` id (`cue-rename-from`) and the
  visible label text, so a future `grep` for either keeps them in sync.
- **None for code paths** — no Python / TS / SQL / API behavior changes.

## Validation plan

Per the fixer Phase-2 touch-log rule, only files matching the leg's
tracked roots trigger that leg. `docs/reference/*.md` is **not** in any
tracked path, so:

- Leg A (pytest) — SKIP after first iteration if no `autocue/**.py`
  touched.
- Leg B (vitest) — SKIP after first iteration if no `docs/index.html`
  or `tests/web/**` touched.
- Leg C (Playwright e2e) — SKIP after first iteration if no
  `autocue/serve/**`, `docs/index.html`, or `tests/e2e/**` touched.

First iteration still runs all three legs as the touch-log baseline.
After the doc-only edit, all three legs SHOULD be clean on second pass
(no tracked files touched).

# Issue #118 — auto-tag silently returns `skipped_no_data` with no reason

## Problem

`POST /api/auto-tag` with `{tag_types: ["category","vocal"]}` on tracks that LOOK
fully analyzed (source=file, has_phrase=true, has_beats=true, existing_hot_cues>0)
returns `{tagged: 0, skipped_no_data: N, errors: 0}` and the user has zero
diagnostic signal about *why* every track was skipped.

The QA agent's filter (`has_phrase` / `has_beats` in `/api/tracks`) only proves:

- `has_phrase = bool(AnalysisDataPath)` — that the track has an ANLZ path string
  on the row, not that PSSI phrases were parsed out of the ANLZ file.
- `has_beats = bool(BPM > 0)` — pure BPM presence, NOT PQTZ beat-grid data.

So tracks can pass the QA filter and still legitimately have no PWAV (energy),
no PSSI (phrases), or no PQTZ (beats) inside the ANLZ. From the API response,
there is no way to tell which.

## Root cause

`autocue/analysis/auto_tag.py::apply_tags()` (lines 547–677) collapses every
"no detector produced a tag name" path into a single `skipped_no_data`
counter:

- `_detect_category` returns `[]` when `get_energy_curve` is None (no PWAV) OR
  when `get_classification` is None OR when top score < `MIN_SCORE` (0.70).
- `_detect_vocal` returns `[]` when `get_mixability` is None (no PSSI/PQTZ).
- `_detect_energy_level` returns `[]` when no PWAV.
- … and so on per detector.

All collapse to `skipped_no_data += 1`. No telemetry, no breakdown.

## Proposed solution

Add a `skipped_reasons: dict[str, int]` field to the response so the user
can see the actual distribution of skip reasons. Keep all existing fields
stable for backward compat.

Reasons surfaced:

- `no_content` — `db.get_content(ID)` returned None.
- `no_energy_curve` — `get_energy_curve` returned None (no PWAV).
- `no_classification` — `get_classification` returned None.
- `low_classification_score` — top category < `MIN_SCORE`.
- `no_mixability` — `get_mixability` returned None (no PSSI/PQTZ).
- `no_year` — decade detector saw no release year.
- `no_bpm` — bpm tier detector saw no BPM.
- `no_play_history` — play history detector returned `[]` (matches existing
  semantics: count of 6–24 hits no bucket, neither does count ≥ 0 without a
  bucket-match).

The breakdown is per-track-skip-event: a track that fails BOTH category AND
vocal in one call bumps `no_energy_curve` (or whichever) once per failing
detector. This is OK; the user wants to see which detector is the culprit.

`skipped_no_data` keeps its existing meaning ("at least one detector ran but
nothing was written") to avoid breaking any caller that watches it. Hard
"no content" rows (always-silent before) now also surface in
`skipped_reasons.no_content` for transparency, but do NOT bump
`skipped_no_data` (preserves prior behavior).

## Affected files

- `autocue/analysis/auto_tag.py` — instrument each `_detect_*` indirectly by
  having `apply_tags` consult cheap pre-checks and bump
  `skipped_reasons` accordingly. Avoid changing detector signatures (those
  are exported / lowercase-private but call sites are entire module).
- `autocue/serve/schemas.py` — add `skipped_reasons: dict[str, int] = {}` to
  `AutoTagResponse`.
- `tests/test_auto_tag.py` — new regression test for `skipped_reasons`.
- `docs/reference/auto-tag.md` — document the new field + tighten the
  "has_phrase doesn't mean PSSI present" gotcha.

## Risks

- Front-end consumers of `AutoTagResponse` need to tolerate the new key.
  `docs/index.html` uses `tagged`, `skipped_no_data`, `errors`, `undo_data`
  exclusively (verified in Phase 2). Adding a key is additive.
- The `skipped_reasons` counters need to NOT double-count: a track that fails
  `category` because `no_energy_curve` AND fails `vocal` because
  `no_mixability` should bump both, once each. We do this at detector level
  inside the per-track loop.

## Test plan

1. Regression test: a track with NO ANLZ data (None content fields) goes
   through `apply_tags(..., tag_types=["category","vocal"])` and the
   response carries `skipped_reasons={"no_energy_curve": 1, "no_mixability": 1}`
   (plus zero on the others). FAILS without the fix because the field does
   not exist in the current response.
2. Boundary: a track with energy curve present but classification score
   exactly at `MIN_SCORE` is NOT a `low_classification_score` skip (`>=
   MIN_SCORE` passes the gate); one a hair below IS.
3. Invariant: `sum(skipped_reasons.values()) >= skipped_no_data` for any
   input (every skipped track contributes at least one reason).

## Test legs that will run

- Leg A (`pytest -x -q`) — auto_tag.py + tests/test_auto_tag.py + schemas.py.
- Leg B (`npm test`) — `docs/index.html` not touched, skipped per touch log
  if first iter is otherwise clean. (First iter still runs all legs.)
- Leg C (e2e) — `autocue/serve/schemas.py` is in the tracked roots → runs.

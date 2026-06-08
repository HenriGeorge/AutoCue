# Issue #118 — Auto-tag returns silent `skipped_no_data` without per-reason diagnostics

## Problem

`POST /api/auto-tag` returns `{tagged: 0, skipped_no_data: N, errors: 0}` on tracks
where `source='file' && has_phrase=true && has_beats=true`. The user gets no
actionable signal — `skipped_no_data` could mean any of:

- ANLZ chunk read failed.
- PWAV (energy) chunk missing → category / energy_level / energy_profile skipped.
- PSSI (phrase) chunk missing → vocal / intro_outro / category skipped.
- Classification confidence < 0.70 (MIN_SCORE) → category skipped even when energy data is present.
- BPM/year/play-count metadata missing → bpm_tier / decade / play_history skipped.

The QA agent's hypothesis ("audio path mismatch") is wrong: `_detect_category`
and `_detect_vocal` do not read audio — they read ANLZ chunks. But the
hypothesis is *consistent with* what the response showed, which proves the
diagnostic is misleading.

## Root cause

`autocue/analysis/auto_tag.py:516` — `apply_tags()` collapses every skip into
the single `skipped_no_data` counter. The per-detector reason (no energy curve,
no phrase data, sub-threshold confidence, no metadata) is discarded.

`autocue/serve/schemas.py:420` — `AutoTagResponse` exposes only the collapsed
counter.

## Proposed solution

Expose the per-reason breakdown without breaking the existing
`tagged / skipped_no_data / errors / dry_run / undo_data` shape:

1. In `apply_tags`, accumulate a `skipped_reasons: dict[str, int]` keyed by
   reason code:
     - `"no_energy_data"`   — PWAV missing (energy / energy_level /
       energy_profile / category all blocked).
     - `"no_phrase_data"`   — PSSI missing or empty (vocal / intro_outro / category).
     - `"low_confidence"`   — category-detector classification < MIN_SCORE.
     - `"no_metadata"`      — decade / bpm_tier / play_history detectors had no
       source data.
   These are *advisory* counts — a track may bump multiple reasons in one call
   when the user requested multiple tag types.
2. Extend `AutoTagResponse` with `skipped_reasons: dict[str, int] = {}` (default
   empty for back-compat).
3. Update the detectors that have actionable skip reasons to thread the reason
   back to `apply_tags` without changing their public return-type contract:
     - Change the detector signature from `_detect_X(content, db) -> list[str]`
       to `_detect_X(content, db) -> tuple[list[str], str | None]` where the
       second element is the skip reason (`None` when names were returned or
       when "skip" is the documented expected outcome rather than a data gap).

## Affected files

- `autocue/analysis/auto_tag.py` — detector signatures + accumulator + return shape.
- `autocue/serve/schemas.py` — extend `AutoTagResponse` with `skipped_reasons`.
- `tests/test_auto_tag.py` — update existing assertions; add reason-breakdown tests.

## Risks

- Breaking the detector signature is internal; only `apply_tags` calls them.
- Frontend reads the response in `docs/index.html` — currently displays
  `tagged` / `skipped_no_data`. Adding a key is additive; old behavior is
  unchanged.

## Out of scope

- Re-running audio analysis to fill missing PWAV/PSSI chunks (the issue mentions
  this as an option; it would be a much larger feature change and the QA report
  flagged it as severity:medium, impact:small).
- Lowering `MIN_SCORE` — that's a tuning decision, not a diagnostic bug.

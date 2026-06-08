# Issue #118 — auto-tag `skipped_no_data` is opaque

## Problem

`POST /api/auto-tag` returns `{tagged: 0, skipped_no_data: N}` for every track
in the QA probe, with no diagnostic of *why* a track was skipped. The QA agent
hypothesised "audio files unreachable from sandbox" but `apply_tags()` never
opens audio — it reads ANLZ via `db.read_anlz_file()` and the My Tags table.
The real bug is the missing diagnostic, not the audio path.

## Root cause

`autocue/analysis/auto_tag.py:548` collapses three distinct skip conditions
into the single `skipped_no_data` counter:

1. **no ANLZ energy data** — `get_energy_curve()` returns `None`
2. **classification below MIN_SCORE** — `_detect_category` returns `[]`
   because `clf["scores"][primary] < 0.70`
3. **no detector produced a name** — e.g. `_detect_decade` with missing
   `ReleaseYear`, `_detect_intro_outro` with `phrase_count == 0`,
   `_detect_bpm_tier` with `BPM == 0`.

Every per-track detector path returning `[]` lands in the same bucket
(`auto_tag.py:627` parallel and `:650` serial), so a user seeing
`skipped_no_data:3` cannot distinguish a sandbox-with-no-ANLZ scenario
from a real-library scenario where every track was simply low-confidence.
That is exactly what tripped the QA agent — they jumped to "audio path
mismatch" when in fact the tracks they probed almost certainly had ANLZ
present and just produced low category scores (or no decade/BPM tier
matched for `tag_types=["category"]` default).

## Proposed solution

Add a `skip_reasons` dict to the apply_tags response:

```python
"skip_reasons": {
    "no_anlz_energy":      int,  # get_energy_curve returned None
    "low_classification":  int,  # category detector: top score < MIN_SCORE
    "no_detector_match":   int,  # all detectors returned [] for other reasons
}
```

This is a strictly additive change to `AutoTagResponse` (default empty
dict; existing clients keep working). The total of these three buckets
plus `skipped_no_data` remains backward compatible because we still emit
`skipped_no_data` as the sum.

To classify "which bucket", the eval worker returns a per-track reason
tuple alongside the names list. Implementation keeps the writer-thread
contract intact (no new DB calls from the parallel worker).

## Affected files

- `autocue/analysis/auto_tag.py` — eval worker returns reason; counter logic
- `autocue/serve/schemas.py` — `AutoTagResponse.skip_reasons` field
- `tests/test_auto_tag.py` — new tests for each reason bucket + regression

## Risks

- None to the writer path — only the read-eval branch changes.
- Existing tests already assert `skipped_no_data` values; the change keeps
  the total identical, so existing assertions still pass.
- The eval worker now returns a 5-tuple (was 4); change is internal to the
  module.

## Test plan

1. **Regression test that would FAIL without the fix:** assert that on a
   track where `get_energy_curve` returns `None`, the response includes
   `skip_reasons["no_anlz_energy"] == 1`.
2. **Boundary test:** at `top_score == MIN_SCORE - epsilon`, the bucket is
   `low_classification`; at `top_score == MIN_SCORE`, `tagged == 1`.
3. **Invariant test:** for any combination of tag_types/detector outcomes,
   `sum(skip_reasons.values()) == skipped_no_data`.

## Touch-log forecast (Phase 2)

- Leg A (pytest): YES — touches `autocue/**.py` + `tests/**.py`
- Leg B (vitest): NO — no `docs/index.html` / `tests/web/**` touched
- Leg C (e2e): YES — touches `autocue/serve/schemas.py` (shared with serve)

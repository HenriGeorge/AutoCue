# Issue #116 — Set Builder: build-mode BPM not monotonically non-decreasing

## Problem

Filed by `autocue-qa`:
`feature/set-builder:bpm-not-monotonic-in-build-mode:asymmetric-gate-violation`

`POST /api/setbuilder` with `{start_bpm: 120, end_bpm: 128, energy_mode: 'build'}`
returns a set whose BPM sequence contains 1–1.5 BPM dips
(e.g. `118.81 → 117.65`) and whose first track BPM (`116.51`) is below
the requested `start_bpm = 120`.

`docs/reference/set-builder.md` § "Asymmetric BPM gate" describes the gate
direction but does **not** spell out:

1. That the ascending branch allows `bpm_lo = current_bpm * 0.97` —
   a ~3% downward dip per step is permitted, so "build" is *biased*
   non-decreasing, not strictly non-decreasing.
2. That `_find_seed` pass two relaxes the `start_bpm * 0.97` floor to
   `0.0` when pass one finds no track, so the head track can fall well
   below the user-supplied `start_bpm` on small libraries.
3. The head-track `mix_advice = null` rule (already documented at line 841,
   no change needed).

The reporter explicitly says they "lean toward option (2): update the doc,
the implementation likely has good reasons for the tolerance window."

## Root cause (doc-vs-behavior gap)

- `autocue/analysis/setbuilder.py:447` — ascending branch sets
  `bpm_lo = max(current_bpm * 0.97, start_bpm * 0.97)`. The 3% downward
  slack is intentional (the score-transition reweighting + BPM-progress
  bonus reward upward movement on average but don't forbid a tactical dip).
- `autocue/analysis/setbuilder.py:403` — `_find_seed`'s two-pass loop;
  pass two `min_bpm = 0.0` falls back to any-BPM seeds.

Both behaviors are *intentional* — the bug is purely documentation
overpromising "ascending / non-decreasing" semantics.

## Proposed solution

Doc-only update to `docs/reference/set-builder.md`:

1. § 1 "Overview", bullet 2: replace the bare "Move toward `end_bpm`" line
   with explicit "biased — not strictly monotonic" wording linking to the
   asymmetric-gate section.
2. § 6 "Asymmetric BPM gate": add a "BPM monotonicity" sub-note explicitly
   describing the ±3% tolerance window and the consequence (small BPM dips
   are allowed in `build` mode by design).
3. § 4 "Seed selection": add a callout to the pass-two relaxation — the
   first track can fall below `start_bpm` on small libraries.

No code change. No test addition required (the documented behavior
matches existing tests; the doc was the source of truth misalignment).

## Affected files

- `docs/reference/set-builder.md` (3 small edits, ≤ 30 lines diff)
- `.claude/PRPs/issues/116-*.investigation.md` (this file)
- `.claude/PRPs/reviews/116-*.review.md` (Phase 3.5 artifact)

## Risks

- None to production code paths.
- Doc rendering: confirm the new sub-headings don't break the TOC anchors
  used elsewhere in the file (the existing `#asymmetric-bpm-gate` and
  `#4-seed-selection--_find_seed` anchors must remain reachable).

## Test legs

Doc-only change. Touch log:
- A (pytest): clean — no `.py` files touched → SKIP after first iteration.
- B (vitest): clean — no `docs/index.html` change → SKIP.
- C (e2e): clean — no `autocue/serve/`, `db_writer`, or `docs/index.html`
  change → SKIP.

First iteration baseline still runs all three to establish green.

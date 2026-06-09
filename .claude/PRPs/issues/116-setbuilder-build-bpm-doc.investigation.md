# Issue #116 — Set Builder build-mode BPM monotonicity (doc fix)

## Problem

QA filed [autocue-qa] report: a `POST /api/setbuilder` request with
`{start_bpm:120, end_bpm:128, duration_minutes:30, energy_mode:'build'}`
returned 8 tracks whose BPM sequence is **not** monotonically non-decreasing
(`[116.51, 116.51, 117.65, 118.81, 117.65, 118.42, 117.65, 117.65]`). Build
mode therefore doesn't promise strict monotonicity, but the reference doc
implies (without explicit qualification) that the "asymmetric BPM gate"
enforces forward-only progression in build mode.

Additionally the first track BPM (116.51) sits below the requested
`start_bpm = 120`. This is inside the documented `start_bpm × 0.97` floor
(116.4) but the seed-selection floor isn't mentioned outside §4, so a reader
of §1 / §6 won't realise this is intended.

## Root Cause

The code is correct; the docs are imprecise.

`autocue/analysis/setbuilder.py:446-449` (ascending branch):

```python
bpm_lo = max(current_bpm * (1.0 - 0.03), start_bpm * 0.97)
bpm_hi = current_bpm * (1.0 + bpm_step_max)
```

The lower bound is `current_bpm × 0.97` — i.e. each step **may dip up to 3%
below the previous track** before being filtered out. This is intentional:
the planner *biases* upward (via the BPM-progress bonus + reweighting) but
does not *enforce* monotonicity. A 1.16-BPM drop (118.81 → 117.65) is a 0.98%
move, well within the 3% tolerance window.

The reference doc (`docs/reference/set-builder.md`) currently:

1. §1.2 says "Move toward an ending BPM (`end_bpm`) at no more than
   `bpm_step_max` per step (default 8%, asymmetric — see Candidate retrieval)"
   — only mentions the upside cap, not the 3% downside slack.
2. §6 shows the asymmetric-gate code but doesn't translate the
   `bpm_lo = current_bpm × (1 - 0.03)` line into prose. A reader who doesn't
   parse the code carefully will conclude that ascending sets are forward-only.
3. §13 already documents `mix_advice=None` for the seed track (the QA's
   "adjacent finding" about head-track mix_advice). No action needed there.
4. §4 already documents the `start_bpm × 0.97` seed-selection floor (the QA's
   complaint about first track BPM < start_bpm) — but it's siloed in seed
   selection and not referenced from §1 / §6 where a reader looks for BPM
   bounds.

## Proposed Solution

Follow the QA's preferred path (fix #2): **update the doc** to describe
the actual behaviour. No code change.

Edits to `docs/reference/set-builder.md`:

1. **§1 "Overview" item 2** — qualify the build/drop progression: "biases
   toward `end_bpm`, allowing up to ~3% wiggle in the opposite direction per
   step (soft bias, not a hard gate)".
2. **§6 "Asymmetric BPM gate"** — add an explicit paragraph after the code
   block explaining the `0.03` factor and what it means in practice: build
   mode permits small BPM dips between consecutive tracks; the floor is the
   `start_bpm × 0.97` clamp (so the set never drops below the user's stated
   start_bpm minus 3%).
3. **§1 "Overview" item 1** — add a short note that the seed track can be up
   to 3% below `start_bpm` (the same floor) and cross-link to §4.

Also patch `autocue/analysis/setbuilder.py`'s module docstring + the
`build_set` docstring so callers reading the source see the same caveat
(small comment, no behaviour change).

## Affected Files

- `docs/reference/set-builder.md` — three small text additions (§1 + §6).
- `autocue/analysis/setbuilder.py` — extend the existing comment above
  the asymmetric-gate code (lines 443-445) and the build_set docstring to
  state the 3% downside slack explicitly.
- `tests/test_setbuilder.py` — add a regression-style test that asserts
  the 3% downside slack: a candidate at `current_bpm × 0.98` (1% dip)
  IS accepted; a candidate at `current_bpm × 0.95` (5% dip) is NOT.
  This is the test that would have failed if a future "fix" tightened
  build mode to strict monotonicity.

## Risks

- **Doc-only change**: no runtime risk.
- **Test addition**: locks in current behaviour. If a future maintainer
  decides build mode *should* be strictly monotonic, this test will fail
  and force them to look at the doc; that's exactly the lock we want.
- **No code change**: the original behaviour (3% dip allowed) is preserved
  intentionally — see Bug 4 history, the asymmetric gate exists precisely
  because pure-monotonic filtering starved the candidate pool.

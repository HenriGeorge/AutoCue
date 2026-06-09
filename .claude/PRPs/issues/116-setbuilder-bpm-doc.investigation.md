# Issue #116 — Set Builder BPM not monotonic in build mode

**Title**: `[autocue-qa] feature/set-builder:bpm-not-monotonic-in-build-mode:asymmetric-gate-violation`
**Filed by**: autocue-qa
**Severity**: medium / data-quality

## Problem

QA probe of `POST /api/setbuilder` with `{start_bpm:120, end_bpm:128, duration_minutes:30, energy_mode:'build'}` returned 8 tracks whose BPMs were:

```
116.51, 116.51, 117.65, 118.81, 117.65, 118.42, 117.65, 117.65
```

The reporter expected strict monotonic non-decreasing BPM in `build` mode, per
the doc's "asymmetric BPM gate" wording in `docs/reference/set-builder.md`.
Observed:
1. `bpms[3] = 118.81 → 117.65` drops by 1.16 BPM.
2. `bpms[5] = 118.42 → 117.65` drops again.
3. First track BPM 116.51 is below the requested `start_bpm = 120`.
4. `tracks[0].mix_advice = null` — doc says every row carries `mix_advice`.

The reporter explicitly leans toward fix (2) — update the doc — over (1)
tightening the gate.

## Root cause

The implementation is intentional and correct (Bug 4 legacy — see
`docs/reference/set-builder.md` §17). The doc undersells the *intentional*
behaviour:

1. **Asymmetric BPM gate allows backward dip of 3% per step.**
   `autocue/analysis/setbuilder.py:446-453`
   ```python
   if end_bpm > start_bpm:                       # ascending
       bpm_lo = max(current_bpm * (1.0 - 0.03), start_bpm * 0.97)
       bpm_hi = current_bpm * (1.0 + bpm_step_max)
       ...
   elif end_bpm < start_bpm:                     # descending
       bpm_lo = current_bpm * (1.0 - bpm_step_max)
       bpm_hi = current_bpm * (1.0 + 0.03)
   ```
   In ascending mode the next track can be **up to 3% slower** than the
   current track. With `current_bpm=118.81`, the floor is `118.81 × 0.97 =
   115.25`, so a 117.65 BPM track passes the gate. This is the **Bug 4
   layer B fix** — without the asymmetry the similarity index returns
   mostly same-BPM tracks and starves the planner of progression
   candidates. The current dip-tolerance is a deliberate trade-off.

2. **Seed `start_bpm × 0.97` floor.** `autocue/analysis/setbuilder.py:403`
   ```python
   for min_bpm in (start_bpm * 0.97, 0.0):
   ```
   Seed selection accepts a 3% slack below `start_bpm`. With
   `start_bpm=120`, the floor is `116.4`, so a 116.51 BPM track is a valid
   seed. The doc's §4 mentions the 0.97 multiplier but doesn't tie it back
   to the "start_bpm" bound the user supplied; §1 Overview only says the
   set "starts near" `start_bpm` with no quantification.

3. **Head-track `mix_advice = null`.** `autocue/analysis/setbuilder.py:159`
   The seed `SetTrack` is constructed with `transition_score=None` and
   `mix_advice` defaults to `None` (line 39). §13 line 841 of the doc
   states "The seed track has `mix_advice=None`", but the structural
   guarantee is not surfaced in §1 Overview, §15 Schema, or the example
   block; reporter missed it.

## Why option (2) — doc fix — is correct

- The implementation has interlocking reasons (Bug 4 history §17). The
  asymmetric gate, the start_bpm slack on the seed, the 40-candidate pool,
  and the BPM-progress bonus all coordinate to *escape* same-BPM traps
  without forbidding minor backward movement. Tightening the gate to
  strict-monotonic would re-introduce the failure mode Bug 4 was designed
  to escape.
- The QA reporter explicitly recommended option 2.
- "Build mode" never promised strict monotonic in code; only the doc's
  loose phrasing did.

## Proposed solution

Update `docs/reference/set-builder.md`:

1. **§1 Overview point 2** — expand "asymmetric — see Candidate retrieval"
   to explicitly state: ascending mode accepts up to 3% backward step per
   slot (not strict monotonic). Same for descending.

2. **§1 Overview point 5 (seed)** — note that `_find_seed` accepts BPMs as
   low as `start_bpm × 0.97`, so the actual seed may be a few % below the
   requested floor.

3. **§6 Asymmetric BPM gate** — promote the existing worked example to a
   "**Backward step allowance**" subsection that calls out: in ascending
   mode, `bpm_lo = current × 0.97` (capped no lower than `start_bpm ×
   0.97`); slots can step down by up to 3%. Same flip in descending mode.

4. **§13 `mix_advice` per track** — add an explicit "Seed exception" bullet
   noting `mix_advice = null` for `tracks[0]` (no previous track to
   transition from). Mirror in §15 Schema field table.

5. **§15 `SetBuilderTrackItem` schema** — `mix_advice` row description:
   "`transition_advice(ts)`. **`None` for the seed track (`tracks[0]`)** —
   there is no previous transition." (Currently says "`None` for the
   seed.")

6. **§1 Overview Output example** — the existing JSON shows
   `"mix_advice": "..."` for two trailing tracks but not for a head row;
   add a head-row sample so `mix_advice: null` is visible to any reader who
   only skims §1.

Zero code changes. Zero tests added (no code change to guard) — but I'll
verify all three legs still pass.

## Affected files

- `docs/reference/set-builder.md` — content updates per items 1–6 above.

No code, schema, or behaviour changes.

## Risks

- **Doc drift vs implementation.** Mitigation: every claim is grounded in a
  specific `setbuilder.py` line; if those constants change the doc points
  at the lines.
- **Reader frustration.** The doc is already very long. Mitigation: keep
  additions to one or two sentences per section; don't rewrite.
- **Touching `docs/`** triggers no Phase-2 leg (not in any tracked path).
  All three legs will still run on the first iteration (no prior baseline)
  and should be green.

## Test plan

- pytest -x -q — must remain green (no code changed).
- npm test (vitest) — must remain green (no code changed).
- e2e leg — must remain green (no code changed, only doc).

If any leg flakes, do NOT attribute it to this PR — the diff is doc-only.

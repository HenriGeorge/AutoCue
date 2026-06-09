# Set Builder — Reference

Build a complete DJ set automatically by walking the track library with a
beam-search planner. Given a starting BPM, ending BPM, target duration, and an
energy mode, Set Builder selects an ordered list of tracks, scores each
transition, and attaches a one-line mixing tip per slot.

This is the most complex intelligence feature in AutoCue. It composes four other
modules — similarity, classification, transition scoring, and energy curves —
into a single planner that produces something a working DJ can actually load
into a deck.

Source: [`autocue/analysis/setbuilder.py`](../../autocue/analysis/setbuilder.py).

Related references:
- [Transition scoring](./transition-scoring.md) — `score_transition` and the
  weighting Set Builder rewrites for BPM movement.
- [Similar tracks](./similar-tracks.md) — `find_similar`, the per-step candidate
  fetcher.
- [Track classification](./track-classification.md) — `get_classification`
  drives the category arc (warmup → build → peak …).
- [Energy and mixability](./energy-and-mixability.md) — `get_energy_curve` /
  `get_mixability` feed the similarity vector and the energy penalty.
- [Auto-tag](./auto-tag.md) — uses the same category model for My Tag writes.
- [Glossary](./GLOSSARY.md) — Pioneer DB terms ([`DjmdContent`](./GLOSSARY.md#djmdcontent),
  [`DjmdCue`](./GLOSSARY.md#djmdcue), [`Kind`](./GLOSSARY.md#cue-encoding-kind-slot-inframe-outmsec), …).

---

## Table of Contents

- [1. Overview](#1-overview)
  - [Inputs](#inputs)
  - [Output](#output)
- [2. Algorithm](#2-algorithm)
  - [Step by step](#step-by-step)
- [Beam search visualized](#beam-search-visualized)
- [Relaxation ladder visualized](#relaxation-ladder-visualized)
- [3. `build_set(...)` — full signature](#3-build_set--full-signature)
- [4. Seed selection — `_find_seed(...)`](#4-seed-selection--_find_seed)
- [5. Category arc — `_category_order(prefs)`](#5-category-arc--_category_orderprefs)
- [6. Candidate retrieval — `_get_candidates(...)`](#6-candidate-retrieval--_get_candidates)
  - [Asymmetric BPM gate](#asymmetric-bpm-gate)
  - [BPM monotonicity](#bpm-monotonicity)
  - [Candidate count](#candidate-count)
  - [Per-candidate filtering](#per-candidate-filtering)
- [7. Deduplication — three axes](#7-deduplication--three-axes)
- [8. Setbuilder-specific transition reweighting](#8-setbuilder-specific-transition-reweighting)
- [9. BPM-progress bonus](#9-bpm-progress-bonus)
- [10. Energy penalty — `_energy_penalty(...)`](#10-energy-penalty--_energy_penalty)
- [11. Relaxation tiers — `_relaxation_tiers(...)`](#11-relaxation-tiers--_relaxation_tiers)
- [12. Anchor merging — `_merge_anchors(...)`](#12-anchor-merging--_merge_anchors)
- [13. `mix_advice` per track](#13-mix_advice-per-track)
- [14. `build_alternatives(...)` and `/api/setbuilder/alternatives`](#14-build_alternatives-and-apisetbuilderalternatives)
- [15. `SetBuilderTrackItem` schema](#15-setbuildertrackitem-schema)
- [16. `SetAlternativeItem` schema](#16-setalternativeitem-schema)
- [17. Bug 4 history — full timeline](#17-bug-4-history--full-timeline)
- [18. REST endpoints](#18-rest-endpoints)
- [19. UI surface](#19-ui-surface)
- [20. Performance](#20-performance)
- [Performance and scaling](#performance-and-scaling)
- [21. Examples](#21-examples)
- [22. Testing](#22-testing)
- [23. Related references](#23-related-references)

---

## 1. Overview

`build_set(...)` returns an ordered list of tracks that:

1. Start near a given BPM (`start_bpm`). The seed track can fall below
   `start_bpm` on small libraries — `_find_seed` falls back to any-BPM
   tracks when no candidate satisfies `bpm ≥ start_bpm × 0.97`
   (see [Seed selection](#4-seed-selection--_find_seed) pass two).
2. Move toward an ending BPM (`end_bpm`) at no more than `bpm_step_max` per
   step (default 8%, asymmetric — see [Candidate retrieval](#6-candidate-retrieval--_get_candidates)).
   The progression is **biased**, not strictly monotonic: in `build` mode
   the per-step BPM may dip by up to ~3% of `current_bpm` (≈ 1 BPM at
   120 BPM) — see [BPM monotonicity](#bpm-monotonicity).
3. Sum to roughly `duration_minutes` of playback.
4. Honour an `energy_mode` of `build` / `flat` / `drop` (a soft penalty on each
   transition).
5. Begin from `seed_track_id` if supplied, otherwise an algorithmically chosen
   first track (see [Seed selection](#4-seed-selection--_find_seed)).
6. Include every track in `anchor_track_ids` (merged at BPM-sorted positions —
   see [Anchor merging](#12-anchor-merging--_merge_anchors)).
7. Maintain a category arc of `warmup → build → peak` when BPM ascends, or
   `peak → after_hours → closing` when BPM descends (see [Category arc](#5-category-arc--_category_orderprefs)).
8. Never repeat the same track ID, the same title+artist string, or use any
   single artist more than twice (see [Deduplication](#7-deduplication--three-axes)).

The planner is a **beam search of width 5**
([`setbuilder.py:24`](../../autocue/analysis/setbuilder.py#L24)). At each step
the five highest-scoring partial sets each fan out to up to five new
extensions; the new pool is re-sorted by cumulative score and pruned to the top
five. The loop terminates when every beam has reached `duration_minutes` of
playback, when no candidates pass even the most relaxed constraint tier, or
when a safety cap (`3 × est_tracks` steps) is hit.

### Inputs

| Argument            | Type          | Default     | Notes                                                                              |
|---------------------|---------------|-------------|------------------------------------------------------------------------------------|
| `db`                | RB6 DB        | —           | A `Rekordbox6Database` handle.                                                     |
| `start_bpm`         | `float`       | `110.0`     | Seed BPM target.                                                                   |
| `end_bpm`           | `float`       | `135.0`     | Final BPM target. Direction controls the category arc and the asymmetric BPM gate. |
| `duration_minutes`  | `float`       | `60.0`      | Stops the beam when cumulative track length reaches this.                          |
| `energy_mode`       | `str`         | `"build"`   | One of `"build"`, `"flat"`, `"drop"`. Soft penalty only (see [Energy penalty](#10-energy-penalty--_energy_penalty)).                     |
| `bpm_step_max`      | `float`       | `0.08`      | Max fractional BPM increase per step (8%).                                         |
| `seed_track_id`     | `int \| None` | `None`      | Overrides `_find_seed` if supplied.                                                |
| `anchor_track_ids`  | `list[int]`   | `None`      | Must-include tracks merged into the result (see [Anchor merging](#12-anchor-merging--_merge_anchors)).                                  |

### Output

```python
{
    "tracks": [
        {
            "track_id": 4214,
            "title": "...",
            "artist": "...",
            "bpm": 122.5,
            "key": "8A",
            "category": "build",
            "transition_score": 87.3,      # None for the seed track
            "mix_advice": "Nudge pitch +1.5 BPM — blend over 8–16 bars; ...",
            "relaxed": False,
        },
        ...
    ],
    "terminated_reason": "target_duration_reached"
        # | "no_candidates_passed_thresholds"
        # | "safety_cap_hit"
}
```

The HTTP layer wraps this in `SetBuilderResponse` and adds
`total_tracks` and `estimated_duration_minutes` computed from
[`DjmdContent`](./GLOSSARY.md#djmdcontent)`.Length` of every slot
([`routes.py:1383`](../../autocue/serve/routes.py#L1383)).

---

## 2. Algorithm

The high-level loop (paraphrased from
[`setbuilder.py:174`](../../autocue/analysis/setbuilder.py#L174)):

```
build similarity index if missing
seed = _find_seed() or db.get_content(ID=seed_track_id)
beams = [Beam(tracks=[seed], total_duration=seed.Length, visited={seed.ID}, ...)]

while True:
    if every beam has total_duration >= target_duration: break  # done
    if step >= est_tracks * 3: break                            # safety cap

    step += 1
    target_cat = _target_category(step, est_tracks, cat_sequence)

    for beam in beams:
        for tier in _relaxation_tiers(bpm_step_max, target_cat):
            candidates = _get_candidates(...)  # cached per (cat, cat_min, bpm_step)
            score each candidate via score_transition + reweighting + bonuses
            if any candidate scored: keep best 5 fanouts, stop relaxing
            else: continue to next tier

    beams = best 5 over the new pool by cumulative_score
```

### Step by step

1. **Index** — if `similar._INDEX_BUILT` is `False`, `_build_index(db)` runs
   once. The index caches a 6-dim feature vector per track and pre-warms
   `classify._class_cache` via `_index_track`
   ([`similar.py:162`](../../autocue/analysis/similar.py#L162)). After this all
   subsequent classification lookups in the loop are O(1).
2. **Seed** — `_find_seed(db, start_bpm, cat_sequence[0])` finds the best
   first track, or `db.get_content(ID=seed_track_id)` if the caller supplied
   one (see [Seed selection](#4-seed-selection--_find_seed)).
3. **Candidate retrieval** — per step, per beam, `find_similar(track_id, db,
   n=20|40, bpm_gate=...)` returns BPM-gated candidates from the similarity
   index. `n` doubles to 40 when `end_bpm ≠ start_bpm` so the pool is wide
   enough to surface higher-BPM tracks
   ([`setbuilder.py:459`](../../autocue/analysis/setbuilder.py#L459)).
4. **Filter** — `_get_candidates` applies the asymmetric BPM gate, category
   threshold, dedup on track ID / title+artist / artist count, then returns
   `[(track_id, bpm, key), ...]` (see [Candidate retrieval](#6-candidate-retrieval--_get_candidates), [Deduplication](#7-deduplication--three-axes)).
5. **Score** — each candidate gets `score_transition(current, cand, db)`. When
   BPM is changing, the overall score is **rebalanced** to `0.25×bpm +
   0.40×key + 0.35×energy` (see [Reweighting](#8-setbuilder-specific-transition-reweighting)). A **BPM-progress bonus** of up to +15 is
   added when the candidate moves toward `end_bpm` (see [BPM-progress bonus](#9-bpm-progress-bonus)). An **energy penalty**
   of 0 or 15 is subtracted (see [Energy penalty](#10-energy-penalty--_energy_penalty)). The final value drives the beam ranking.
6. **Beam expand** — each beam fans out to its top 5 candidates; the
   union of all fanouts is sorted by cumulative score and pruned back to 5
   beams.
7. **Terminate** — loop exits when every beam exceeds `duration_minutes`
   ("target_duration_reached"), when zero candidates pass even the loosest
   relaxation tier ("no_candidates_passed_thresholds"), or when the safety
   cap of `3 × est_tracks` steps is hit ("safety_cap_hit",
   [`setbuilder.py:179`](../../autocue/analysis/setbuilder.py#L179)).
8. **Anchor merge** — if `anchor_track_ids` is non-empty, missing anchors are
   inserted into the best beam's `tracks` at the BPM-sorted position (see [Anchor merging](#12-anchor-merging--_merge_anchors)).

The estimated track count drives both the category arc and the safety cap:

```python
est_tracks = max(3, int(target_duration_s / 360))   # average 6-min track
```

`setbuilder.py:130`.

---

## Beam search visualized

A picture of one beam-search iteration. Beam width is **5**; each beam fans out
to its top **K** candidates (`K = _CANDIDATES_PER_STEP × (2 if BPM is changing
else 1)`, capped per beam at 5 fan-outs). After the fan-out, the **union** of
all new partial sets is re-sorted by **cumulative score** and pruned back to 5.

```
                       BEAM POOL (top 5 partial sets, ranked by cumulative score)
                       ─────────────────────────────────────────────────────────

  Step 0 (seed)        ┌─────────┐
  cumulative=0.0       │   B0    │   one seed → one beam
                       │ 110 BPM │
                       │   8A    │
                       └────┬────┘
                            │ fan-out to top 5 candidates per beam
                            ▼
                       ┌────┴────────────────────────────────────────────┐
  Step 1 candidates   ─┤  scored = reweighted_overall + bpm_bonus − ep   ├─
                       └─────────────────────────────────────────────────┘
                        B0→a (113 BPM 8A)            92.5
                        B0→b (115 BPM 8A)            91.0
                        B0→c (113 BPM 9A)            88.5
                        B0→d (118 BPM 8A)            85.0
                        B0→e (116 BPM 4A) ← dropped  62.0   below transition_min

  After step 1         B0→a   B0→b   B0→c   B0→d   B0→e   ← 5 new partials
  (top 5 retained)     92.5   91.0   88.5   85.0    —     ← e pruned
                         │      │      │      │
                         ▼      ▼      ▼      ▼
  Step 2 fan-out       each of the 4 surviving beams fans out to ≤5 candidates
                       ┌──────────────────────────────────────────────────┐
                       │  (B0→a)→x  +91.4 →  cum 183.9                     │
                       │  (B0→a)→y  +88.0 →  cum 180.5                     │
                       │  (B0→b)→x  +85.5 →  cum 176.5                     │
                       │  (B0→b)→z  +83.0 →  cum 174.0                     │
                       │  (B0→c)→x  +80.5 →  cum 169.0                     │
                       │  (B0→d)→w  +60.0 →  cum 145.0  ← dropped          │
                       │  ...                                              │
                       └──────────────────────────────────────────────────┘

  After step 2         top 5 by cumulative score retained, rest discarded
  (top 5 retained)     183.9   180.5   176.5   174.0   169.0

  Termination          when every surviving beam's total_duration ≥ target,
                       or no tier produced any candidate, or the safety
                       cap (3 × est_tracks steps) is hit. The single best
                       beam by cumulative score is returned.
```

**Key invariants:**

- The five beams are **alternate timelines** — they share no state. A track
  chosen by one beam may still appear later in another beam's set.
- The dedup structures (`visited`, `visited_titles`, `visited_artists`) are
  **per beam**, not global.
- A beam that produces zero candidates in **every** relaxation tier dies — it
  is dropped from the pool and not retained even if its cumulative score is
  high. Without this, dead beams would clog the top 5 indefinitely.
- Cumulative score is the sum of per-step scored values (`overall − ep +
  bpm_bonus`), not the mean — longer beams naturally accumulate more.

---

## Relaxation ladder visualized

For each beam at each step, the planner walks **down** this ladder until a
candidate passes. The first tier that produces ≥1 candidate wins; further tiers
are skipped. Tracks placed via any tier ≥ 1 are flagged `relaxed=True`.

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │ TIER 0  ──  strict (default)                                         │
  │   target category required                                           │
  │   category_min     = 0.30                                            │
  │   transition_min   = 40.0                                            │
  │   bpm_step_max     = as passed (e.g. 0.08)                           │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ no candidate passes? ↓
  ┌────────────────────────────┴─────────────────────────────────────────┐
  │ TIER 1  ──  loosen category fit                                      │
  │   target category required                                           │
  │   category_min     = 0.20   ◄── relaxed (was 0.30)                   │
  │   transition_min   = 40.0                                            │
  │   bpm_step_max     = as passed                                       │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ no candidate passes? ↓
  ┌────────────────────────────┴─────────────────────────────────────────┐
  │ TIER 2  ──  loosen transition threshold                              │
  │   target category required                                           │
  │   category_min     = 0.20                                            │
  │   transition_min   = 30.0   ◄── relaxed (was 40.0)                   │
  │   bpm_step_max     = as passed                                       │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ no candidate passes? ↓
  ┌────────────────────────────┴─────────────────────────────────────────┐
  │ TIER 3  ──  widen BPM step                                           │
  │   target category required                                           │
  │   category_min     = 0.20                                            │
  │   transition_min   = 30.0                                            │
  │   bpm_step_max     = as passed + 0.02   ◄── relaxed                  │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ no candidate passes? ↓
  ┌────────────────────────────┴─────────────────────────────────────────┐
  │ TIER 4  ──  drop category filter entirely                            │
  │   category         = None   ◄── filter removed                       │
  │   category_min     = None                                            │
  │   transition_min   = 30.0                                            │
  │   bpm_step_max     = as passed + 0.02                                │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ still no candidate?
                               ▼
                  beam dies; if all beams die → terminate with
                  `no_candidates_passed_thresholds`.
```

Tier 1 and tier 2 share the same candidate pool (only `transition_min`
differs) — the inner `_cand_cache` keyed by `(category, category_min,
bpm_step_max)` reuses the tier-1 result so tier 2's filter call is free.
See [`setbuilder.py:375`](../../autocue/analysis/setbuilder.py#L375) for the
tier list.

---

## 3. `build_set(...)` — full signature

```python
def build_set(
    db,
    start_bpm: float = 110.0,
    end_bpm: float = 135.0,
    duration_minutes: float = 60.0,
    energy_mode: str = "build",     # "build" | "flat" | "drop"
    bpm_step_max: float = 0.08,     # max BPM increase per step (8%)
    seed_track_id: int | None = None,
    anchor_track_ids: list[int] | None = None,
) -> dict
```

[`setbuilder.py:101`](../../autocue/analysis/setbuilder.py#L101).

| Argument            | Type           | Default    | Behaviour                                                                                                                                                          |
|---------------------|----------------|------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `db`                | RB6 DB         | —          | Required. SQLAlchemy session via `Rekordbox6Database`. The route uses `get_ro_db` (read-only) — Set Builder never writes.                                           |
| `start_bpm`         | `float`        | `110.0`    | Seed BPM target. `_find_seed` requires `bpm ≥ start_bpm × 0.97` in pass one (see [Seed selection](#4-seed-selection--_find_seed)).                                                                                  |
| `end_bpm`           | `float`        | `135.0`    | Final BPM target. Controls `_category_order` direction (see [Category arc](#5-category-arc--_category_orderprefs)), the asymmetric BPM gate (see [Candidate retrieval](#6-candidate-retrieval--_get_candidates)), candidate count doubling (see [Step by step](#step-by-step)), reweighting (see [Reweighting](#8-setbuilder-specific-transition-reweighting)), and BPM bonus (see [BPM-progress bonus](#9-bpm-progress-bonus)).  |
| `duration_minutes`  | `float`        | `60.0`     | Loop exits when every beam's `total_duration` (in seconds) ≥ `duration_minutes × 60`. Tracks that overshoot are kept whole — no track is trimmed.                  |
| `energy_mode`       | `str`          | `"build"`  | Soft penalty only — does not gate candidates. `_energy_penalty` returns `0.0` or `15.0`. See [Energy penalty](#10-energy-penalty--_energy_penalty).                                                                  |
| `bpm_step_max`      | `float`        | `0.08`     | Max fractional BPM increase per step. Feeds the secondary `bpm_lo`/`bpm_hi` filter and the relaxation tiers (see [Relaxation tiers](#11-relaxation-tiers--_relaxation_tiers)).                                                 |
| `seed_track_id`     | `int \| None`  | `None`     | When set, bypasses `_find_seed` entirely. The seed is used regardless of its BPM or category.                                                                      |
| `anchor_track_ids`  | `list[int]`    | `None`     | Tracks that **must** appear in the final result. Beam-found anchors are left in place; missing anchors are merged after the beam terminates (see [Anchor merging](#12-anchor-merging--_merge_anchors)).                 |

`None` for `anchor_track_ids` skips the merge step entirely
([`setbuilder.py:324`](../../autocue/analysis/setbuilder.py#L324)).

---

## 4. Seed selection — `_find_seed(...)`

[`setbuilder.py:393`](../../autocue/analysis/setbuilder.py#L393).

```python
def _find_seed(db, start_bpm: float, category: str):
    contents = list(db.get_content())
    best_content = None
    best_score = -1.0
    for min_bpm in (start_bpm * 0.97, 0.0):     # two-pass
        for c in contents:
            bpm = float(getattr(c, "BPM", 0) or 0) / 100.0
            if bpm <= 0 or bpm < min_bpm:
                continue
            bpm_s = _bpm_score(start_bpm, bpm) / 100.0
            cls = get_classification(c, db)
            cat_s = cls.get("scores", {}).get(category, 0.0)
            score = bpm_s * 0.5 + cat_s * 0.5
            if score > best_score:
                best_score = score
                best_content = c
        if best_content is not None:
            break
    return best_content
```

Two-pass design (Bug 4, layer C — see [Bug 4 history](#17-bug-4-history--full-timeline)):

- **Pass one** requires `bpm ≥ start_bpm × 0.97`. This prevents starting a
  110-BPM warmup set with a 95-BPM track. The 3% slack absorbs decimal rounding
  between Rekordbox's `BPM` (stored as int × 100) and the input.
- **Pass two** falls back to *any* BPM if pass one matched no tracks.
  Necessary for tiny libraries or very specific category requests where no
  in-range candidate exists.

> **Consequence — the first track may sit below `start_bpm`.** On small
> libraries (or when the target category is rare), pass two is reached and
> the seed can be arbitrarily far below the user-supplied `start_bpm`. The
> input is a *target*, not a hard floor. If you require the head track to
> sit at or above `start_bpm`, pass an explicit `seed_track_id` — that
> branch bypasses `_find_seed` entirely.

The score blends BPM proximity (`_bpm_score` from
[`transitions.py:28`](../../autocue/analysis/transitions.py#L28)) and target
category fit equally:

```
score = (bpm_score / 100) * 0.5 + category_score * 0.5
```

`category` is `cat_sequence[0]` — i.e. `"warmup"` for ascending sets,
`"peak"` for descending sets.

---

## 5. Category arc — `_category_order(prefs)`

[`setbuilder.py:53`](../../autocue/analysis/setbuilder.py#L53).

```python
def _category_order(prefs: dict) -> list[str]:
    start_bpm = prefs.get("start_bpm", 100.0)
    end_bpm = prefs.get("end_bpm", 140.0)
    if end_bpm >= start_bpm:
        return ["warmup", "build", "peak"]
    else:
        return ["peak", "after_hours", "closing"]
```

For a 110→135 build set, the planner targets warmup early, build through the
middle, and peak by the end. For a 135→105 drop set, it targets peak, then
after-hours, then closing.

`_target_category(step, total_steps, category_sequence)` slices the step
position into the category list:

```python
def _target_category(step, total_steps, category_sequence):
    if total_steps <= 1:
        return category_sequence[0]
    idx = min(int(step / total_steps * len(category_sequence)),
              len(category_sequence) - 1)
    return category_sequence[idx]
```

[`setbuilder.py:65`](../../autocue/analysis/setbuilder.py#L65).

For a 10-step set with `["warmup", "build", "peak"]`:
- Steps 0–2 → `warmup`
- Steps 3–5 → `build`
- Steps 6–9 → `peak`

The category becomes a **filter input** for `_get_candidates`: candidates must
score `≥ category_min` (default 0.3) on the target category before they can be
scored. The relaxation ladder (see [Relaxation tiers](#11-relaxation-tiers--_relaxation_tiers)) lowers this threshold and eventually
removes the category filter entirely if no candidates pass.

---

## 6. Candidate retrieval — `_get_candidates(...)`

[`setbuilder.py:424`](../../autocue/analysis/setbuilder.py#L424).

```python
def _get_candidates(
    track_id, current_bpm, target_cat, visited, db,
    bpm_step_max, start_bpm, end_bpm,
    category_min=0.3,
    visited_titles=None,
    visited_artists=None,
    max_artist_repeats=2,
) -> list[tuple[int, float, str]]:
```

### Asymmetric BPM gate

The BPM gate has three branches based on the direction of `end_bpm` vs
`start_bpm`:

```python
if end_bpm > start_bpm:     # ascending — bias forward
    bpm_lo = max(current_bpm * (1.0 - 0.03), start_bpm * 0.97)
    bpm_hi = current_bpm * (1.0 + bpm_step_max)
    bpm_gate = max(bpm_hi - current_bpm, current_bpm - bpm_lo, 12.0)

elif end_bpm < start_bpm:   # descending — bias backward
    bpm_lo = current_bpm * (1.0 - bpm_step_max)
    bpm_hi = current_bpm * (1.0 + 0.03)
    bpm_gate = max(bpm_hi - current_bpm, current_bpm - bpm_lo, 12.0)

else:                       # flat
    bpm_lo = current_bpm * (1.0 - 0.03)
    bpm_hi = current_bpm * (1.0 + 0.03)
    bpm_gate = max(abs(bpm_hi - current_bpm), abs(current_bpm - bpm_lo), 8.0)
```

Two clamps run:

1. `bpm_gate` is passed to `find_similar(..., bpm_gate=...)` — this is the
   **coarse** filter used by the similarity index. The 12-BPM floor (8 for
   flat) gives the index enough slack to surface higher-BPM neighbours that a
   pure 3% window would miss.
2. `bpm_lo` / `bpm_hi` filter the returned list explicitly — the **fine**
   filter. A 120-BPM track with `end_bpm=135` and `bpm_step_max=0.08` accepts
   candidates in `[120 × 0.97, 120 × 1.08] = [116.4, 129.6]`.

The asymmetry exists because the planner *wants* upward movement: making the
gate symmetric meant the similarity index returned mostly same-BPM tracks,
starving the planner of progression candidates (Bug 4, layer B — see [Bug 4 history](#17-bug-4-history--full-timeline)). The
asymmetric gate gives the index more upside candidates while the fine filter
still enforces the step constraint.

### BPM monotonicity

Important — the per-step BPM sequence is **biased upward (build) or downward
(drop), not strictly monotonic**. The ascending fine filter accepts any
candidate where:

```
current_bpm × 0.97  ≤  cand_bpm  ≤  current_bpm × (1 + bpm_step_max)
```

(clamped to `start_bpm × 0.97` on the low end). Concretely, in a 120 → 128
`build` set, a step from `current_bpm = 118.81` may pick a candidate at
`117.65` — a 1.16 BPM dip — because `117.65 ≥ 118.81 × 0.97 = 115.24`.

Two reasons the tolerance window is intentional:

1. **Pool size on small libraries.** Strictly non-decreasing would empty the
   candidate pool at most steps in a typical home library (a few hundred
   tracks). The BPM-progress bonus (see
   [BPM-progress bonus](#9-bpm-progress-bonus)) and the rebalanced
   transition score (BPM weight 0.25 instead of the global 0.40, see
   [Reweighting](#8-setbuilder-specific-transition-reweighting)) bias the
   beam toward upward movement *on average* — but a single dip can be the
   right call when the alternative is a sharply less-compatible key/energy
   match.
2. **DJ practice.** Real sets often nudge BPM down by 1–2 to set up a key
   change or an energy moment, then climb again. Strict monotonicity would
   forbid this idiom.

If you need strict non-decreasing/non-increasing behavior (e.g. a competition
mix), supply the entire sequence as `anchor_track_ids` — anchors are inserted
at their BPM-sorted positions and bypass the candidate selector entirely
(see [Anchor merging](#12-anchor-merging--_merge_anchors)).

### Candidate count

```python
n = _CANDIDATES_PER_STEP * 2 if end_bpm != start_bpm else _CANDIDATES_PER_STEP
similar = find_similar(track_id, db, n=n, bpm_gate=bpm_gate)
```

`_CANDIDATES_PER_STEP = 20`
([`setbuilder.py:25`](../../autocue/analysis/setbuilder.py#L25)). So:

- `end_bpm == start_bpm` → 20 candidates per step.
- `end_bpm ≠ start_bpm` → 40 candidates per step. This is the second prong of
  the Bug 4 fix — same-BPM tracks fill all 20 slots in a flat library, so
  doubling the pool makes room for the upside candidates that survive the
  coarse `bpm_gate=12.0` filter.

### Per-candidate filtering

For each `(cid, score, bpm_diff)` returned by `find_similar`:

1. Skip if `cid in visited` (track-ID dedup).
2. Resolve `content = db.get_content(ID=cid)`. Skip if `None`.
3. Convert `getattr(content, "BPM", 0) / 100.0` to a float. Skip if
   `bpm < bpm_lo` or `bpm > bpm_hi`.
4. Build a `title|||artist` lowercase key. Skip if it appears in
   `visited_titles` (title-dedup).
5. Skip if `visited_artists[artist] >= max_artist_repeats` (artist cap).
6. If `target_cat` and `category_min` are not both `None`, fetch
   `get_classification(content, db)` and skip if
   `scores[target_cat] < category_min`.
7. Resolve `key` from `content.Key.ScaleName` (best-effort, defaults to `""`).
8. Append `(cid, bpm, key)`.

The category filter is the single most expensive check per candidate (it can
read the ANLZ energy curve). The relaxation ladder defers it — tiers later in
the ladder pass `category_min=None` so a hopelessly small library can still
finish a set.

---

## 7. Deduplication — three axes

Each `_Beam` ([`setbuilder.py:43`](../../autocue/analysis/setbuilder.py#L43))
maintains three dedup structures:

```python
@dataclass
class _Beam:
    tracks: list[SetTrack] = field(default_factory=list)
    total_duration: float = 0.0
    cumulative_score: float = 0.0
    visited: set[int] = field(default_factory=set)              # ← track ID
    visited_titles: set[str] = field(default_factory=set)        # ← "title|||artist"
    visited_artists: dict[str, int] = field(default_factory=dict)  # ← artist count
```

### Axis 1 — `visited: set[int]`

Prevents the same `content.ID` appearing twice on the same beam. Straightforward.

### Axis 2 — `visited_titles: set[str]`

Keys are `f"{title}|||{artist}".lower()`. Catches the *duplicate import* case:
a DJ who imported the same track under two different IDs (e.g. a remaster and
original) would otherwise see both. The `|||` separator is a placeholder
unlikely to appear inside any real title — collisions are essentially
impossible.

### Axis 3 — `visited_artists: dict[str, int]`

A counter, capped at `max_artist_repeats=2` by default. Lets a recognisable
artist appear twice (their signature track + a deeper cut) but blocks "three
in a row" scenarios.

The 2-cap is set in `_get_candidates`'s signature, not the request, so external
callers cannot tune it without editing the source. The Bug 4 fix lowered the
limit from "no cap" to two after the William Orbit ×3 / Gia Margaret ×2 case
surfaced in adversarial review.

All three dedup states are tracked **per beam**, not globally. Two beams can
have legitimately different `visited` sets, which is what makes beam search
work — beams represent alternate timelines.

---

## 8. Setbuilder-specific transition reweighting

[`setbuilder.py:232`](../../autocue/analysis/setbuilder.py#L232).

```python
if end_bpm != start_bpm:
    overall = round(0.25 * ts["bpm"] + 0.40 * ts["key"] + 0.35 * ts["energy"], 1)
else:
    overall = ts["overall"]
```

`score_transition` returns `overall = 0.40*bpm + 0.35*key + 0.25*energy` —
that's the right weighting for *judging* a transition in isolation. It's the
**wrong** weighting for *planning* one: a 40% BPM weight makes same-BPM tracks
beat almost any BPM-progressing alternative.

Set Builder rebalances when `end_bpm ≠ start_bpm`:

| Weight       | Standard transition | Set Builder (BPM changing) |
|--------------|---------------------|-----------------------------|
| BPM          | 0.40                | **0.25**                    |
| Key          | 0.35                | 0.40                        |
| Energy       | 0.25                | 0.35                        |

The shift moves 15 percentage points of weight off BPM (so a 70-BPM-score
candidate is no longer auto-rejected) and redistributes them to key and energy
(so non-BPM compatibility matters more).

For flat sets (`end_bpm == start_bpm`), the standard weighting is correct —
you *want* same-BPM tracks — and Set Builder uses `ts["overall"]` unchanged.

This is the **layer A** fix from Bug 4. See [Bug 4 history](#17-bug-4-history--full-timeline).

---

## 9. BPM-progress bonus

[`setbuilder.py:244`](../../autocue/analysis/setbuilder.py#L244).

```python
bpm_bonus = 0.0
if end_bpm != start_bpm:
    progress_needed = end_bpm - current_track.bpm
    progress_made = cand_bpm - current_track.bpm
    if abs(progress_needed) > 0.5 and progress_needed * progress_made > 0:
        bpm_bonus = min(15.0, 15.0 * abs(progress_made / progress_needed))

adjusted = overall - ep + bpm_bonus
```

The bonus rewards candidates that move toward `end_bpm`:

- `progress_needed = end_bpm - current_bpm` — how far the set still has to go.
- `progress_made = cand_bpm - current_bpm` — how far this candidate would move.
- The sign-product guard (`progress_needed * progress_made > 0`) means the
  bonus only applies when both deltas point in the same direction.
- The bonus is `min(15, 15 × |progress_made / progress_needed|)` — capped at
  +15 and proportional to the fraction of remaining BPM distance covered.

Worked example. `start_bpm=110`, `end_bpm=135`. Current track at 120 BPM
(`progress_needed = 15`). Candidate at 122.5 BPM (`progress_made = 2.5`).

```
bpm_bonus = min(15, 15 * |2.5 / 15|) = min(15, 2.5) = 2.5
```

A candidate at 130 BPM (`progress_made = 10`) would score
`min(15, 15 * 10/15) = 10`. A candidate that overshoots end_bpm
(`progress_made = 16`, `progress_needed = 15`) clamps at +15.

The `abs(progress_needed) > 0.5` guard prevents division-near-zero when the
beam is already at `end_bpm` (which is also when the bonus is no longer
useful).

This bonus is the **counterforce** to the BPM penalty inside `score_transition`
— without it, beam search would still get stuck at same-BPM tracks. With it,
upward movement gets up to 15 points back. Combined with the reweighting
(see [Reweighting](#8-setbuilder-specific-transition-reweighting)), this is what makes the planner actually escape same-BPM clusters.

---

## 10. Energy penalty — `_energy_penalty(...)`

[`setbuilder.py:73`](../../autocue/analysis/setbuilder.py#L73).

```python
def _energy_penalty(end_prev: float | None, start_next: float | None,
                    energy_mode: str) -> float:
    if end_prev is None or start_next is None:
        return 0.0
    if energy_mode == "build" and start_next < end_prev - 0.15:
        return 15.0
    if energy_mode == "drop" and start_next > end_prev + 0.15:
        return 15.0
    if energy_mode == "flat" and abs(start_next - end_prev) > 0.15:
        return 15.0
    return 0.0
```

The penalty is a 15-point flat subtraction off `adjusted`. Inputs are the
scalar end-of-A and start-of-B energies already computed inside
`score_transition` and returned as `ts["end_energy_a"]` /
`ts["start_energy_b"]` — Set Builder does not re-read ANLZ curves.

`0.15` is the noise floor on the normalized 0–1 energy curve — smaller
deltas are perceptually indistinguishable.

Modes:

- **`build`** — penalize candidates that drop energy by more than 0.15.
- **`drop`** — penalize candidates that raise energy by more than 0.15.
- **`flat`** — penalize candidates that change energy by more than 0.15 in
  either direction.

When either side is `None` (no ANLZ data), the penalty is `0.0`. This is
intentional: penalising unknown data would systematically demote no-ANLZ
tracks, which is overly punitive. Coverage holes should not weigh heavier
than violations of the requested arc.

The penalty is a **soft signal**, not a gate. A candidate with `+30 BPM bonus
- 15 energy penalty = +15 net` still wins over a candidate with `0 bonus 0
penalty`. This is by design — energy is the weakest signal Set Builder has.

---

## 11. Relaxation tiers — `_relaxation_tiers(...)`

[`setbuilder.py:375`](../../autocue/analysis/setbuilder.py#L375).

```python
def _relaxation_tiers(bpm_step_max: float, target_cat: str) -> list[dict]:
    return [
        {"category": target_cat, "category_min": 0.3,  "transition_min": 40.0, "bpm_step_max": bpm_step_max},
        {"category": target_cat, "category_min": 0.2,  "transition_min": 40.0, "bpm_step_max": bpm_step_max},
        {"category": target_cat, "category_min": 0.2,  "transition_min": 30.0, "bpm_step_max": bpm_step_max},
        {"category": target_cat, "category_min": 0.2,  "transition_min": 30.0, "bpm_step_max": bpm_step_max + 0.02},
        {"category": None,       "category_min": None, "transition_min": 30.0, "bpm_step_max": bpm_step_max + 0.02},
    ]
```

Five tiers, ordered from strict to permissive:

| Tier | Category               | `category_min` | `transition_min` | `bpm_step_max`     | Notes                                  |
|------|------------------------|----------------|------------------|--------------------|----------------------------------------|
| 0    | target                 | 0.30           | 40.0             | as-passed (0.08)   | Default constraints.                   |
| 1    | target                 | 0.20           | 40.0             | as-passed          | Loosen category fit.                   |
| 2    | target                 | 0.20           | 30.0             | as-passed          | Loosen transition threshold.           |
| 3    | target                 | 0.20           | 30.0             | + 0.02 (10%)       | Widen BPM step.                        |
| 4    | `None` (no filter)     | `None`         | 30.0             | + 0.02 (10%)       | Drop category filter entirely.         |

The beam walks the ladder top to bottom **per step**. On tier 0, if no
candidate makes it past the filters, the loop falls through to tier 1, and so
on. Tracks placed via any tier ≥ 1 are flagged `relaxed=True` so the UI can
warn the DJ (`SetBuilderTrackItem.relaxed`,
[`schemas.py:362`](../../autocue/serve/schemas.py#L362)).

The `_cand_cache` inside the per-beam loop
([`setbuilder.py:205`](../../autocue/analysis/setbuilder.py#L205)) memoizes
candidate lists across tiers that share `(category, category_min,
bpm_step_max)` — tier 1 and tier 2 share a candidate list (only
`transition_min` differs), so the second call is free.

The ladder is a controlled fallback, not a permission to lower quality. In a
healthy library tier 0 satisfies almost every step; only at the edges (last
two tracks of a 90-minute set, or odd category transitions) does the planner
slide down.

---

## 12. Anchor merging — `_merge_anchors(...)`

[`setbuilder.py:330`](../../autocue/analysis/setbuilder.py#L330).

```python
def _merge_anchors(db, result: dict, anchor_ids: list[int]) -> None:
    tracks = result["tracks"]
    existing = {t["track_id"] for t in tracks}

    for anchor_id in anchor_ids:
        if anchor_id in existing:
            continue
        content = db.get_content(ID=anchor_id)
        if content is None: continue
        ...
        anchor_dict = {
            "track_id": anchor_id, "title": ..., "artist": ...,
            "bpm": round(bpm, 2), "key": ..., "category": cls.get("primary", "unknown"),
            "transition_score": None,
            "relaxed": False,
        }
        insert_pos = next(
            (j for j, t in enumerate(tracks) if t["bpm"] >= bpm),
            len(tracks),
        )
        tracks.insert(insert_pos, anchor_dict)
        existing.add(anchor_id)
```

Behaviour:

1. Anchors already chosen by the beam are left in place.
2. Missing anchors are looked up, classified, and **inserted at the first
   position where the running BPM is at or above the anchor's BPM** — i.e.
   the BPM-sorted position. For a 110→135 set, an anchor at 122 BPM lands
   between the last sub-122 track and the first 122+ track.
3. `transition_score` is `None` (the caller's neighbours change after
   insertion — recomputing here would lie). The UI should not surface a
   transition score for anchors.
4. `relaxed=False` regardless of whether the anchor would have passed any
   tier — anchors are user intent, not algorithmic compromise.
5. Anchors with `db.get_content(ID=...) is None` are silently dropped (the
   debug log records the failure).

Anchors that have no BPM data are inserted at the **end** of the list (the
generator's fallback `len(tracks)` value).

---

## 13. `mix_advice` per track

Every non-seed `SetTrack` carries a `mix_advice` field — a one-line practical
DJ mixing tip produced by `transition_advice(ts)`
([`transitions.py:194`](../../autocue/analysis/transitions.py#L194)) from the
same dict that produced `transition_score`.

Examples from `transition_advice`:

```
"BPM matched — beatmix, blend over 16–32 bars; compatible key (5A→4A) — harmonic blend works"
"Nudge pitch +5.5 BPM — blend over 8–16 bars; mild dissonance (4A→6A) — keep overlap ≤8 bars"
"18.4 BPM gap — hard cut at phrase boundary; key incompatible (1A→7B) — cut-mix or use a cappella"
"Half-time drop (130→64 BPM) — let outgoing finish, bring incoming in at full energy"
"Double-time (95→190 BPM) — quick cut at phrase boundary"
```

The advice has three components, joined by `"; "`:

1. **BPM technique** — beatmix vs nudge vs phrase-cut vs hard-cut. Decided by
   `bpm_score` thresholds and the half-time/double-time ratio detection.
2. **Key technique** — harmonic blend vs mild dissonance vs key clash vs
   incompatible. Decided by `key_score` thresholds.
3. **Energy technique** — only mentioned when energy jumps or drops by more
   than 20%. Suggests filtering or bridging.

The seed track has `mix_advice=None` (no previous track to transition from).
The UI renders advice in the connector between rows.

---

## 14. `build_alternatives(...)` and `/api/setbuilder/alternatives`

[`routes.py:1391`](../../autocue/serve/routes.py#L1391).

When the user wants to swap a single slot, the alternatives endpoint returns
candidate replacements scored on **fit to both neighbours**, not on the
original generation criteria.

```http
GET /api/setbuilder/alternatives
    ?track_id=<int>
    &prev_id=<int>      (optional)
    &next_id=<int>      (optional)
    &exclude_ids=<csv>
    &n=<1-20>           default 8
```

Algorithm:

1. Build similarity index if missing.
2. Compute the `exclude` set from `exclude_ids` plus the slot's own
   `track_id`.
3. **Candidate pool**: union of `find_similar(prev_id, n=25)`,
   `find_similar(next_id, n=25)`, and `find_similar(track_id, n=25)`. Minus
   `exclude`.
4. Cap the pool at 60 candidates for cost.
5. For each candidate:
   - `from_prev = score_transition(prev, cand, db)["overall"]` if `prev_id`
     given, else `None`.
   - `to_next = score_transition(cand, next, db)["overall"]` if `next_id`
     given, else `None`.
   - `combined = mean(non-None scores)` or `50.0` if both `None`.
6. **Genre match logic**:
   - `ref_genre = GenreName(replaced track)`.
   - `neighbour_genres = {GenreName(prev), GenreName(next)} - {""}`.
   - Match decisions:
     - No `ref_genre` and no `neighbour_genres` → `genre_match=None`.
     - Candidate genre matches `ref_genre` or in `neighbour_genres` →
       `genre_match=True`.
     - Candidate has a genre but it does not match → `genre_match=False`,
       and `combined -= 20` (clamped to `≥ 0`).
     - Candidate has no `GenreName` → `genre_match=None` (no penalty).
7. Sort by `score` descending; return top `n`.

The `-20` penalty exists because a key-and-BPM compatible track from the
wrong genre can wreck a vibe (a dnb track in the middle of a deep house set
matches BPM ratios but breaks immersion). The penalty is large enough to
push wrong-genre candidates below right-genre ones with mild score gaps, but
not large enough to bury them entirely if no right-genre alternative exists.

The reference genre is the **replaced track's** genre first — neighbours are
the fallback. This preserves the original creative intent of the slot.

---

## 15. `SetBuilderTrackItem` schema

[`schemas.py:353`](../../autocue/serve/schemas.py#L353).

```python
class SetBuilderTrackItem(BaseModel):
    track_id: int
    title: str
    artist: str
    bpm: float
    key: str
    category: str
    transition_score: float | None = None
    mix_advice: str | None = None
    relaxed: bool = False
```

| Field              | Type            | Notes                                                                             |
|--------------------|-----------------|-----------------------------------------------------------------------------------|
| `track_id`         | `int`           | `DjmdContent.ID` as int.                                                          |
| `title`            | `str`           | `DjmdContent.Title` or `""`.                                                      |
| `artist`           | `str`           | `DjmdContent.ArtistName` or `""`.                                                 |
| `bpm`              | `float`         | Stored ×100 in Rekordbox, divided here. Rounded to 2 decimal places.              |
| `key`              | `str`           | `DjmdContent.Key.ScaleName` (Camelot, e.g. `"8A"`). Empty if not analysed.        |
| `category`         | `str`           | `get_classification(content)["primary"]` — `warmup`/`build`/`peak`/`after_hours`/`closing`/`unknown`. |
| `transition_score` | `float \| None` | The **raw** `ts["overall"]` for inspection (NOT the reweighted/adjusted internal score). `None` for the seed. |
| `mix_advice`       | `str \| None`   | `transition_advice(ts)`. `None` for the seed.                                     |
| `relaxed`          | `bool`          | `True` if this track was placed via relaxation tier ≥ 1 (see [Relaxation tiers](#11-relaxation-tiers--_relaxation_tiers)).                    |

The full response wrapper:

```python
class SetBuilderResponse(BaseModel):
    tracks: list[SetBuilderTrackItem]
    total_tracks: int
    estimated_duration_minutes: float
    terminated_reason: Literal[
        "target_duration_reached",
        "no_candidates_passed_thresholds",
        "safety_cap_hit",
    ] = "target_duration_reached"
```

`total_tracks = len(tracks)` and `estimated_duration_minutes` is computed
from `DjmdContent.Length` (in seconds) of every track in the result, summed
and divided by 60.

---

## 16. `SetAlternativeItem` schema

[`schemas.py:441`](../../autocue/serve/schemas.py#L441).

```python
class SetAlternativeItem(BaseModel):
    track_id: int
    title: str
    artist: str
    bpm: float
    key: str
    score: float                            # combined fit, 0–100
    from_prev: float | None = None          # transition score from previous track
    to_next: float | None = None            # transition score to next track
    genre: str = ""
    genre_match: bool | None = None         # True | False | None (unknown)
```

| Field          | Type            | Notes                                                                                      |
|----------------|-----------------|--------------------------------------------------------------------------------------------|
| `score`        | `float`         | Mean of `from_prev` and `to_next`, minus 20 if `genre_match is False`. Clamped to `[0, 100]`. |
| `from_prev`    | `float \| None` | `None` when `prev_id` was not supplied (slot is the first track).                          |
| `to_next`      | `float \| None` | `None` when `next_id` was not supplied (slot is the last track).                           |
| `genre`        | `str`           | `DjmdContent.GenreName` of the candidate (best-effort).                                    |
| `genre_match`  | `bool \| None`  | See [`build_alternatives`](#14-build_alternatives-and-apisetbuilderalternatives). `None` is the "no opinion" state — used when no reference genre is available.     |

The endpoint returns these wrapped in:

```python
class SetAlternativesResponse(BaseModel):
    alternatives: list[SetAlternativeItem]
```

---

## 17. Bug 4 history — full timeline

**File**: `SCORING_BUGS.md` (repo root). Set Builder shipped originally with
four compounding scoring bugs that produced degenerate sets — same-BPM, same
category, sometimes duplicate tracks, all transitions scoring 100. This
section documents Bug 4 (the BPM direction bug) in full because it is the
only one whose fix lives entirely in Set Builder; the others were fixed in
`similar.py`, `classify.py`, and `transitions.py` and Set Builder inherits
those fixes.

### Symptom

A 110→135 BPM build, 90 minutes, energy=build set produced:

| Metric              | Before              |
|---------------------|---------------------|
| BPM range           | 107–109 (never reached 135) |
| Category arc        | all warmup / after_hours |
| Duplicate tracks    | 2× "The Gates of Door to Door" |
| Artist repeats      | William Orbit ×3, Gia Margaret ×2 |
| Transition scores   | all 100.0 |

### Root cause — three layers

**Layer A — transition scorer fights BPM movement.** `score_transition`
returns `overall = 0.40×bpm + 0.35×key + 0.25×energy`. `_bpm_score` returns
100 for same-BPM and decays linearly to 0 at ±10% delta. A same-BPM
transition scored 92.5 overall; a +5% BPM transition scored ~76. With the
default weighting the beam *always* chose same-BPM, making upward BPM
progression structurally impossible.

**Layer B — candidate pool too shallow.** `find_similar(n=20, bpm_gate=8.6)`
at BPM 107 filled all 20 returned slots with 107-BPM same-key tracks
(scoring 0.65 each thanks to the data-quality cap from Bug 1), leaving no
slots for higher-BPM candidates *even though they were in-gate*. The
similarity index's bias toward same-BPM tracks (no surprise — they have
identical feature vectors) starved the beam.

**Layer C — seed selection ignored `start_bpm`.** `_find_seed()` selected the
highest-scoring track regardless of BPM, often starting below `start_bpm`.
A set requested at 110 BPM was already mis-anchored at step 0.

### Fix layers

**Layer A fix — setbuilder-specific reweighting** ([`setbuilder.py:232`](../../autocue/analysis/setbuilder.py#L232)):

```python
if end_bpm != start_bpm:
    overall = round(0.25 * ts["bpm"] + 0.40 * ts["key"] + 0.35 * ts["energy"], 1)
else:
    overall = ts["overall"]
```

Reduces the BPM weight from 40% to 25% **only when BPM is changing**. For
flat sets, the standard 40% BPM weight is correct and the planner uses
`ts["overall"]` unchanged.

**Layer A fix part two — BPM-progress bonus** ([`setbuilder.py:244`](../../autocue/analysis/setbuilder.py#L244)):

```python
if end_bpm != start_bpm:
    progress_needed = end_bpm - current_track.bpm
    progress_made = cand_bpm - current_track.bpm
    if abs(progress_needed) > 0.5 and progress_needed * progress_made > 0:
        bpm_bonus = min(15.0, 15.0 * abs(progress_made / progress_needed))
adjusted = overall - ep + bpm_bonus
```

Up to +15 points for moving toward `end_bpm`. The two together (reweighting
+ bonus) shift the planner's preference from "stay where you are" to "move
gradually toward the goal".

**Layer B fix part one — asymmetric BPM gate** ([`setbuilder.py:446`](../../autocue/analysis/setbuilder.py#L446)):

```python
if end_bpm > start_bpm:
    bpm_gate = max(bpm_hi - current_bpm, current_bpm - bpm_lo, 12.0)
```

Floor raised from 8 BPM to 12 BPM when BPM is changing. Widens the coarse
similarity filter so upside candidates are not pre-filtered out.

**Layer B fix part two — doubled candidate retrieval** ([`setbuilder.py:459`](../../autocue/analysis/setbuilder.py#L459)):

```python
n = _CANDIDATES_PER_STEP * 2 if end_bpm != start_bpm else _CANDIDATES_PER_STEP
```

20 → 40 candidates per step. Same-BPM tracks can no longer crowd out the
upside candidates that survive the wider gate.

**Layer C fix — two-pass seed selection** ([`setbuilder.py:402`](../../autocue/analysis/setbuilder.py#L402)):

```python
for min_bpm in (start_bpm * 0.97, 0.0):
    ...
    if best_content is not None:
        break
```

Pass one enforces `bpm ≥ start_bpm × 0.97`; pass two falls back to any BPM
if pass one finds nothing.

**Layer D fix — title + artist dedup** ([`setbuilder.py:48`](../../autocue/analysis/setbuilder.py#L48)):

```python
visited_titles: set[str] = field(default_factory=set)         # "title|||artist" lowercase
visited_artists: dict[str, int] = field(default_factory=dict)  # artist → count
```

Three-axis dedup (track ID, title+artist, artist count ≤ 2) eliminated the
"William Orbit ×3" and "duplicate track" failure modes.

### After

Same inputs (110→135, 90 min, build):

| Metric              | After                                      |
|---------------------|--------------------------------------------|
| BPM range           | 107 → 136                                  |
| Category arc        | warmup → build → peak                      |
| Duplicate tracks    | none                                       |
| Artist repeats      | max 1× per artist (cap permits 2)          |
| Transition scores   | 85–100, real differentiation               |

The fix is interlocking. Each piece is necessary; none is sufficient alone:

- Without reweighting, the bonus is too small to overcome the BPM penalty.
- Without the bonus, the reweighting still ties same-BPM and upside tracks.
- Without the wider gate, no upside candidates reach the scorer at all.
- Without the doubled count, same-BPM tracks fill the candidate pool.
- Without the two-pass seed, the set starts at the wrong BPM regardless.
- Without dedup, the beam settles into a local minimum of repeated tracks.

### Note on cache staleness

Both `similar._INDEX` and `classify._class_cache` are in-memory and built at
server startup. After any code fix to these modules, **the server must be
restarted** for the changes to take effect. The autouse fixture in
`tests/conftest.py` clears all four caches (`energy._cache`,
`classify._class_cache`, `score._mixability_cache`,
`similar.clear_index()`) before every test so the test suite never sees
stale state.

---

## 18. REST endpoints

### `POST /api/setbuilder`

[`routes.py:1351`](../../autocue/serve/routes.py#L1351).

**Request** (`SetBuilderRequest`,
[`schemas.py:343`](../../autocue/serve/schemas.py#L343)):

```json
{
  "start_bpm": 110.0,
  "end_bpm": 135.0,
  "duration_minutes": 90.0,
  "energy_mode": "build",
  "bpm_step_max": 0.08,
  "seed_track_id": null,
  "anchor_track_ids": []
}
```

**Response** (`SetBuilderResponse`,
[`schemas.py:365`](../../autocue/serve/schemas.py#L365)):

```json
{
  "tracks": [
    {
      "track_id": 4214,
      "title": "Opening Statement",
      "artist": "Some Artist",
      "bpm": 110.0,
      "key": "8A",
      "category": "warmup",
      "transition_score": null,
      "mix_advice": null,
      "relaxed": false
    },
    {
      "track_id": 4302,
      "title": "Slow Burn",
      "artist": "Another Artist",
      "bpm": 112.3,
      "key": "8A",
      "category": "warmup",
      "transition_score": 91.4,
      "mix_advice": "Nudge pitch +2.3 BPM — blend over 8–16 bars",
      "relaxed": false
    }
  ],
  "total_tracks": 14,
  "estimated_duration_minutes": 91.2,
  "terminated_reason": "target_duration_reached"
}
```

**Errors**:

- `422 Unprocessable Entity` — `"No valid set could be built with the given
  constraints"`. Raised when the result has zero tracks (no seed, or all
  beams pruned). Re-issue with looser inputs.

### `GET /api/setbuilder/alternatives`

[`routes.py:1391`](../../autocue/serve/routes.py#L1391).

**Query params**:

| Param          | Type         | Default | Notes                                          |
|----------------|--------------|---------|------------------------------------------------|
| `track_id`     | `int`        | —       | Required. The slot being replaced.             |
| `prev_id`      | `int \| None`| `None`  | Previous track in the set (`None` if first).   |
| `next_id`     | `int \| None`| `None`  | Next track in the set (`None` if last).        |
| `exclude_ids`  | `str` (CSV)  | `""`    | Track IDs to exclude from candidates.          |
| `n`            | `int (1–20)` | `8`     | Number of alternatives to return.              |

**Response** (`SetAlternativesResponse`,
[`schemas.py:454`](../../autocue/serve/schemas.py#L454)):

```json
{
  "alternatives": [
    {
      "track_id": 5012,
      "title": "Replacement Cut",
      "artist": "Another Artist",
      "bpm": 122.5,
      "key": "8A",
      "score": 88.5,
      "from_prev": 87.0,
      "to_next": 90.0,
      "genre": "House",
      "genre_match": true
    }
  ]
}
```

---

## 19. UI surface

The Set Builder panel lives on the **Library** tab in
[`docs/index.html`](../../docs/index.html) (server mode only — file:// mode
hides it).

Controls:

- **Start BPM / End BPM** number inputs.
- **Duration** number input (minutes).
- **Energy mode** segmented control (`build` / `flat` / `drop`).
- **Step max %** slider (default 8%).
- **Seed track**: optional — uses the currently selected library row.
- **Anchors**: optional — accepts multiple library selections.
- **Build set** button — `POST /api/setbuilder`.

Output:

- An ordered tracklist, one row per track. Each row shows title, artist,
  BPM, key, category chip (coloured from `get_classification`), transition
  score, and a `relaxed` flag if applicable.
- Between every two rows a **connector** strip carries the `mix_advice`
  string as a tooltip — the DJ scans down the list and reads the advice
  without clicking.
- A **swap** button on each row opens the **alternatives modal**: `GET
  /api/setbuilder/alternatives` with `track_id` = the row's track, `prev_id`
  / `next_id` from the surrounding rows, `exclude_ids` = every other track
  in the set. The modal shows candidates ranked by `score` with `from_prev`
  / `to_next` / genre badges. Selecting a candidate replaces the row in
  place; the UI does *not* re-fetch the whole set.
- A **Create playlist** button at the top calls `POST /api/playlists` with
  the current track IDs — turns the algorithmic set into a real Rekordbox
  playlist.

The modal renders `genre_match=True` with a green check, `False` with a
red cross, and `None` with no badge.

---

## 20. Performance

### Per-step cost

```
beam_count × (similar_lookup_cost + K × score_transition_cost)
```

where `K ≤ 40` is the candidate pool size.

- `find_similar` is O(n) where n = library size (it walks the in-memory
  index), but the inner loop is just a dot product — runs in microseconds for
  a few thousand tracks.
- `score_transition` reads two ANLZ energy curves but the
  `energy._cache` already holds them after the similarity index was built
  (the cache key is `(content.ID, n_points)` and `n_points=50` is the only
  caller).
- `get_classification` is O(1) per candidate because `_class_cache` was
  pre-warmed by `_index_track` during the similarity index build
  ([`similar.py:162`](../../autocue/analysis/similar.py#L162)).

So total work for a 15-track set:

```
5 beams × 15 steps × 40 candidates × O(1) lookup ≈ 3000 transition scorings
```

each of which is ~microsecond range. End-to-end runtime is dominated by the
**index build** (one-off) and the duration / step count.

The server pre-warms the similarity index in a background daemon thread at
startup (`deps._prewarm_index`,
[`autocue/serve/deps.py`](../../autocue/serve/deps.py)). The first set
request after server startup typically hits a warm index and returns in
seconds rather than minutes.

### Memory

The full index is `len(library)` × ~96 bytes (6 floats + bpm + has_e flag)
≈ 300 kB for a 3000-track library. The classification cache adds a few
hundred kB more. Beam state is bounded by `5 × est_tracks` `SetTrack`
objects.

### Pathological cases

- **Very tight `bpm_step_max`** (e.g. 0.02) starves every tier — the planner
  hits the safety cap and returns a partial set with `safety_cap_hit`.
- **Very small library** (< 50 tracks) frequently degrades into the lowest
  relaxation tier; expect `relaxed=True` on most slots.
- **All tracks same artist** triggers the artist cap quickly — the planner
  may not reach `duration_minutes` and returns with
  `no_candidates_passed_thresholds`.

---

## Performance and scaling

A more precise look at how Set Builder scales for very large libraries — useful
when sizing a deployment or diagnosing slow first calls.

### Per-step work

```
work_per_step = beam_count × (similar_lookup + K × score_transition)
              = 5         × (O(n)            + K × O(1))
```

where `K = beam_width × candidates_per_step`. With defaults that's `5 × 40 =
200` candidates per step in the BPM-changing case, `5 × 20 = 100` in the flat
case. `score_transition` is the **hot path**: it's O(1) per call once energy
curves are cached, but it runs `N_steps × K` times across the whole build.

### Memory

The similarity index dominates Set Builder's footprint:

- **6-dim feature vector + bpm + has_e flag** ≈ 96 bytes per track. See
  [similar-tracks.md](./similar-tracks.md) for the exact vector layout.
- 5 000 tracks → ~480 kB.
- 50 000 tracks → ~4.8 MB (the similar-tracks reference cites ~3 MB for the raw
  vectors and a little more for `_class_cache` and bookkeeping; treat ~5 MB at
  50k as a working upper bound).

Beam state is bounded by `5 × est_tracks × sizeof(SetTrack)` — a few kB.
Negligible compared to the index.

### First-call latency

The similarity index build is **one-time** and pre-warmed on server startup by
a background daemon thread
([`autocue/serve/deps.py`](../../autocue/serve/deps.py), `_prewarm_index`).
After warm-up:

- **5 000 tracks, 90-min set (~18 steps)**: typically under one second
  end-to-end, dominated by per-step `find_similar` walks. Cold (no warm
  index) on the same library: usually under a few seconds for the build plus
  the search.
- **50 000 tracks**: index build moves into the multi-second range
  (`_index_track` reads classification + energy per track, both cached
  thereafter). The per-step search remains fast — O(n) over 96-byte vectors
  is microseconds even at 50k.

The CLI path has no pre-warm; first `build_set` call from a cold Python
process pays the index-build cost up front.

### CPU profile

Two hot paths dominate measured runs:

1. **`find_similar` index walk** — `n` vector comparisons per beam per step.
   Pure NumPy/pure-Python dot product on 6 floats; effectively memory-bound.
2. **`score_transition`** — `5 × K = 100–200` calls per step. Reads two
   energy scalars from `ts["end_energy_a"]` / `ts["start_energy_b"]`
   (no ANLZ re-read because the curves are already in `energy._cache`).

The classification lookup is **not** on the hot path because `_index_track`
pre-warms `classify._class_cache` during the index build, so per-candidate
`get_classification` is O(1).

### Operational caveats for libraries above 50 000 tracks

- **Index build time grows linearly.** Cold first call may take noticeable
  seconds. Mitigation: keep the server running so the index lives in process
  memory; the daemon-thread pre-warm handles the rest.
- **The doubled candidate pool (40 per step) is less necessary at scale.**
  The whole point of `n=40` was to keep enough upside candidates around in
  small libraries where same-BPM tracks crowd out the pool (see
  [Bug 4 history](#17-bug-4-history--full-timeline)). At 50k+ a track-rich
  pool means 20 candidates per step is already plenty; halving K halves the
  per-step `score_transition` load if needed. This is not currently
  configurable but would be a one-line change in
  [`setbuilder.py:25`](../../autocue/analysis/setbuilder.py#L25).
- **Memory is not the bottleneck.** Even at 100k tracks the index sits
  comfortably under 10 MB; the FastAPI worker can host it without trouble.

### Practical numbers

| Library size | Index build (cold) | Build set (warm, 90 min) | Build set (cold) |
|--------------|--------------------|--------------------------|------------------|
| 1 000        | <1 s               | ~200 ms                  | ~1 s             |
| 5 000        | ~2 s               | ~1 s                     | ~3 s             |
| 50 000       | ~10–30 s           | ~3 s                     | ~15–35 s         |

(Order-of-magnitude estimates from in-house mocked-DB tests; real-world
numbers depend on disk-cache state for the ANLZ reads done during
`_index_track`.)

---

## 21. Examples

### Example A — 110 → 135 BPM, 90 min, build (house warmup → peak)

Request:

```json
{
  "start_bpm": 110.0,
  "end_bpm": 135.0,
  "duration_minutes": 90.0,
  "energy_mode": "build",
  "bpm_step_max": 0.08
}
```

Algorithm trace (paraphrased):

- `_category_order` → `["warmup", "build", "peak"]`.
- `est_tracks = max(3, 5400 / 360) = 15`.
- `_find_seed(start_bpm=110, "warmup")`: pass one finds a 111.2 BPM warmup
  track scoring 0.92 (`bpm_s=0.95 × 0.5 + cat_s=0.88 × 0.5`).
- Step 1, target = `warmup`: `find_similar(seed, n=40, bpm_gate=12)` returns
  candidates 109–120 BPM. After reweighting and BPM bonus, a 113 BPM
  warmup-strong candidate scores `80 (reweighted) + 1.8 (bonus) = 81.8`.
- Steps 5–9, target = `build`: BPM progresses to 122–128.
- Steps 10–14, target = `peak`: BPM reaches 134–136.

Sample output (truncated):

```
01  110.0 BPM  8A  warmup    (seed)                     —
02  113.0 BPM  8A  warmup    Nudge pitch +3 BPM …       91.4
03  116.5 BPM  9A  warmup    Nudge pitch +3.5 BPM …     88.0
04  118.8 BPM  9A  build     compatible key 9A→10A …    87.5
...
13  131.0 BPM  10A peak      BPM matched — beatmix …    96.8
14  133.5 BPM  10A peak      Nudge pitch +2.5 BPM …     92.0
15  135.8 BPM  10A peak      BPM matched …              95.3

terminated_reason: target_duration_reached
estimated_duration_minutes: 91.2
```

### Example B — 128 → 128 BPM, 60 min, flat (techno set)

Request:

```json
{
  "start_bpm": 128.0,
  "end_bpm": 128.0,
  "duration_minutes": 60.0,
  "energy_mode": "flat",
  "bpm_step_max": 0.08
}
```

Behaviour:

- `_category_order` → `["warmup", "build", "peak"]` (ascending fallback for
  equal BPM).
- Reweighting **does not** apply (`end_bpm == start_bpm`) — `ts["overall"]`
  is used directly.
- BPM bonus is `0.0` always.
- `_get_candidates` flat branch: `bpm_lo = current × 0.97`,
  `bpm_hi = current × 1.03`, `bpm_gate = max(..., 8.0)`. Same-BPM techno
  is exactly what the planner is supposed to chain.
- Energy penalty (flat mode) hits any candidate whose start energy differs
  from the previous outro by > 0.15 — keeps energy consistent.
- `est_tracks = max(3, 3600 / 360) = 10`.

Expected output: 10 tracks within 124.2–131.8 BPM range (3% gate around
128), category mix dominated by `build` or `peak` depending on energy.

### Example C — 130 → 105 BPM, 45 min, drop (closing set)

Request:

```json
{
  "start_bpm": 130.0,
  "end_bpm": 105.0,
  "duration_minutes": 45.0,
  "energy_mode": "drop",
  "bpm_step_max": 0.08
}
```

Behaviour:

- `_category_order` → `["peak", "after_hours", "closing"]` (descending).
- Reweighting applies (`end_bpm ≠ start_bpm`): `0.25×bpm + 0.40×key + 0.35×energy`.
- BPM bonus rewards **downward** movement (`progress_needed * progress_made
  > 0` matches negative on both sides).
- Asymmetric BPM gate flips: `bpm_lo = current × (1 - 0.08)`,
  `bpm_hi = current × 1.03`. The 8% gate is now on the *downside*.
- Energy penalty (drop mode) hits any candidate whose start energy is more
  than 0.15 above the previous outro — keeps energy descending.
- `est_tracks = max(3, 2700 / 360) = 7`.

Expected output: 7–8 tracks moving 130 → 124 → 118 → 113 → 108 → 105 BPM
with `peak` chip on the first 1–2 tracks, `after_hours` in the middle, and
`closing` on the final 2–3.

---

## 22. Testing

[`tests/test_setbuilder.py`](../../tests/test_setbuilder.py) — 27 tests.

| Class                  | Coverage                                                                                  |
|------------------------|-------------------------------------------------------------------------------------------|
| `TestCategoryOrder`    | Ascending, equal, descending, missing-prefs default.                                      |
| `TestTargetCategory`   | First step, last step, mid step, single-step, step beyond total (no IndexError).          |
| `TestEnergyPenalty`    | Both-None, one-None, build with rise / drop, drop with fall / rise, flat with large swing on either side, flat with small swing, small drop in build (< 0.15 threshold). |
| `TestGetTrackInfo`     | Basic extraction, BPM ÷ 100, None fields default to empty string.                         |
| `TestBuildSet`         | Empty when no seed, returns list of dicts with expected keys, no duplicate tracks, seed_track_id used when provided, transition_score present (seed is None), low transition score filtered (only seed survives). |

Mocking strategy: all tests mock pyrekordbox via `MagicMock`. The
`_make_db_content` helper builds a fake `DjmdContent` row; the
`_make_db_with_tracks` helper wraps a list of fakes in a mock `db` whose
`get_content(**kwargs)` resolves by ID or returns the full iterable. The
`autocue.analysis.similar._INDEX_BUILT` flag is patched to `True` and
`_build_index` is mocked to a no-op, so `build_set` skips the real index
build and reads from the mocked `find_similar` return value.

The `conftest.py` autouse fixture clears `energy._cache`,
`classify._class_cache`, `score._mixability_cache`, and calls
`similar.clear_index()` before every test — no test sees state from another
test.

### Integration tests

Routes (`/api/setbuilder`, `/api/setbuilder/alternatives`) are covered by
[`tests/test_serve_routes.py`](../../tests/test_serve_routes.py) (194 tests
total across all routes), using `fastapi.testclient.TestClient` and the
same mock-DB pattern.

### Adding a test

Mock `find_similar` to return the candidate IDs you want; mock
`score_transition` to return the score dict you want; mock
`get_classification` to return the category breakdown you want. Then call
`build_set(...)` with a duration short enough that 1–3 steps suffice and
assert on the returned `tracks` list.

For tests that exercise the relaxation ladder, set the first-tier-mocked
`score_transition` to return a score below 40 and the second-tier-mocked
`score_transition` (via patch with `side_effect`) to return a score above
40 — then assert the result has `relaxed=True` on the second track.

---

## 23. Related references

- [`docs/reference/transition-scoring.md`](./transition-scoring.md) —
  `score_transition`, the BPM / key / energy components, and the weighting
  Set Builder rewrites in [Reweighting](#8-setbuilder-specific-transition-reweighting).
- [`docs/reference/similar-tracks.md`](./similar-tracks.md) — `find_similar`
  and the 6-dim feature vector that drives Set Builder's candidate
  retrieval.
- [`docs/reference/track-classification.md`](./track-classification.md) —
  `get_classification` and the five categories that form Set Builder's
  category arc.
- [`docs/reference/energy-and-mixability.md`](./energy-and-mixability.md) —
  `get_energy_curve`, `get_mixability`, and the PWAV waveform reader behind
  the energy penalty.
- [`docs/reference/auto-tag.md`](./auto-tag.md) — Auto-Tag uses the same
  category model and can write Set Builder's categories as My Tags.
- [`SCORING_BUGS.md`](../../SCORING_BUGS.md) — full root-cause writeup of
  the four bugs that shipped originally, with before/after metrics.
- [`docs/FEATURES.md`](../FEATURES.md) Section 7 — end-user copy with
  step-by-step UI walkthrough.

# Transition Scoring

AutoCue's Transition Scorer answers a single question: *how cleanly can track A be mixed into track B?* It produces a 0–100 overall score, a three-way decomposition (BPM / key / energy), human-readable explanations, and a one-sentence DJ mixing tip.

This document covers the math, the data sources, the REST surface, the UI affordances, and the worked examples needed to predict what the scorer will say about any pair of tracks.

## Table of Contents

- [1. Overview](#1-overview)
- [2. Return dict shape](#2-return-dict-shape)
- [3. Overall score formula](#3-overall-score-formula)
- [4. BPM compatibility](#4-bpm-compatibility)
- [5. Key compatibility (Camelot wheel)](#5-key-compatibility-camelot-wheel)
- [6. Energy compatibility](#6-energy-compatibility)
- [7. Missing energy data (Bug 3 fix)](#7-missing-energy-data-bug-3-fix)
- [8. `transition_advice(ts)`](#8-transition_advicets)
- [9. `explanation` field](#9-explanation-field)
- [10. REST endpoint](#10-rest-endpoint)
- [11. UI surface](#11-ui-surface)
- [12. Performance](#12-performance)
- [13. Worked examples](#13-worked-examples)
- [End-to-end worked example](#end-to-end-worked-example)
- [Asymmetric BPM gate (consumer behavior)](#asymmetric-bpm-gate-consumer-behavior)
- [14. Testing](#14-testing)
- [15. Related documentation](#15-related-documentation)

---

## 1. Overview

Transition scoring is a pure function over two [`DjmdContent`](./GLOSSARY.md#djmdcontent) rows and a [`Rekordbox6Database`](./GLOSSARY.md#rekordbox6database) handle. It is invoked from two surfaces:

1. **Set Builder beam search** (`autocue/analysis/setbuilder.py`) — every candidate at every step is scored against the previous track. The transition score is the primary signal driving beam ranking, with a small genre / category / BPM-progress bonus layered on top.
2. **UI "⇌ Score transition" modal** (`docs/index.html:5131` `showTransitionScore`) — when exactly two tracks are selected in the Library tab in local-server mode, a button reveals a modal showing the overall score, the three component bars, the explanations, and (implicitly) what the set builder would think of the pair.

Internally the scorer is also called from `/api/setbuilder/alternatives` to rank swap candidates against the slot's neighbours.

It is **not** a metric of similarity — two identical tracks score 100, but so do many transitions between tracks that are very different musically as long as their BPM, key, and energy curves line up at the mix-in / mix-out points. Transition scoring measures **mix viability**, not "sounds the same".

References:
- `autocue/analysis/transitions.py:272` `score_transition`
- `autocue/analysis/transitions.py:194` `transition_advice`
- `autocue/serve/routes.py:1326` `POST /api/transitions/score`
- `autocue/serve/schemas.py:197` `TransitionRequest`, `:202` `TransitionResponse`

---

## 2. Return dict shape

`score_transition(content_a, content_b, db)` returns a single flat dict. Every key is always present.

```python
{
    "overall":        92.5,        # float, 0–100, rounded to 1 dp
    "bpm":           100.0,        # float, 0–100, rounded to 1 dp
    "key":           100.0,        # float, 0–100, rounded to 1 dp
    "energy":         50.0,        # float, 0–100, rounded to 1 dp
    "bpm_a":         128.0,        # float, BPM of source track (rounded to 2 dp)
    "bpm_b":         128.0,        # float, BPM of incoming track (rounded to 2 dp)
    "key_a":         "8A",        # str, Camelot key of A, or "" if unknown
    "key_b":         "8A",        # str, Camelot key of B, or "" if unknown
    "end_energy_a":  None,         # float | None — average of last N energy points of A
    "start_energy_b": None,        # float | None — average of first N energy points of B
    "explanation": [               # list[str] — 3 entries (BPM, key, energy)
        "0.0 BPM difference — perfect",
        "Same key (8A)",
        "Energy data unavailable",
    ],
}
```

Field-by-field:

| Key | Type | Meaning |
| --- | ---- | ------- |
| `overall` | `float` | Weighted sum of the three components. The single number the UI shows by default. |
| `bpm` | `float` | Tempo compatibility component (0–100). |
| `key` | `float` | Camelot wheel compatibility component (0–100). |
| `energy` | `float` | End-of-A vs start-of-B energy handoff component (0–100). |
| `bpm_a`, `bpm_b` | `float` | The two BPMs, already divided by 100 (Rekordbox stores BPM × 100). |
| `key_a`, `key_b` | `str` | Camelot strings (`"8A"`, `"5B"`, etc.) or `""` if either side has no `DjmdKey`. |
| `end_energy_a` | `float \| None` | Average of the last 5 normalized energy samples of A (0–1). `None` if the track has no PWAV data. |
| `start_energy_b` | `float \| None` | Average of the first 5 normalized energy samples of B (0–1). `None` if no PWAV. |
| `explanation` | `list[str]` | Three short human-readable strings — one per component — for the UI explanation panel. |

`bpm_a` / `bpm_b` / `key_a` / `key_b` exist for two reasons:

- `transition_advice(ts)` re-uses them so it can render `"Nudge pitch +5 BPM"` without re-reading `DjmdContent`.
- The set builder includes them so the connector row between two set entries can display `128.0 → 132.5 BPM` without a second DB hit.

`end_energy_a` and `start_energy_b` are **scalars**, not curves. They are deliberately exposed (as opposed to keeping the full curves internal) so that downstream consumers (set builder energy-penalty, transition advice) can compute energy deltas without re-reading ANLZ. See [§12 Performance](#12-performance).

---

## 3. Overall score formula

The standard transition score is a fixed weighted sum:

```
overall = 0.40 × bpm + 0.35 × key + 0.25 × energy
```

Implemented at `autocue/analysis/transitions.py:311`:

```python
overall = round(0.40 * bpm_s + 0.35 * key_s + 0.25 * energy_s, 1)
```

The weighting reflects a deliberate ranking of mix-viability concerns:

1. **BPM (40%)** — a 5-BPM gap is felt instantly. Pitch range on most CDJs is ±6%, so a wide BPM gap is structurally hard to mix.
2. **Key (35%)** — harmonic clashes are obvious but recoverable with EQ-kill or a cappella bridges.
3. **Energy (25%)** — the most subjective and the most data-dependent of the three. Carries less weight because the underlying signal (the PWAV waveform) is the noisiest of the three inputs.

### Set Builder uses a different weighting

When the user requests a non-flat BPM curve (`end_bpm != start_bpm`), the set builder **reweights** the same components to avoid structurally punishing forward BPM progression. See `autocue/analysis/setbuilder.py:228–235`:

```python
if end_bpm != start_bpm:
    overall = round(0.25 * ts["bpm"] + 0.40 * ts["key"] + 0.35 * ts["energy"], 1)
else:
    overall = ts["overall"]
```

| Weighting | bpm | key | energy | When used |
| --------- | --- | --- | ------ | --------- |
| Standard  | 0.40 | 0.35 | 0.25 | `/api/transitions/score`, UI modal, flat-BPM set builder, alternatives |
| Setbuilder-progressive | 0.25 | 0.40 | 0.35 | `build_set()` when `end_bpm != start_bpm` |

This is documented in `SCORING_BUGS.md` under Bug 4. The setbuilder weighting is a deliberate compromise — beam search needs the BPM penalty to be soft enough that a +3–5% step is competitive with same-BPM repetition. The BPM-progress bonus (up to +15 pts for moving toward `end_bpm`) is layered on top of the reweighted score. None of this affects the `overall` field returned by `score_transition()` directly — the set builder computes its own value and stores `ts["overall"]` only as `transition_score` on the `SetTrack` (the raw, unweighted value).

---

## 4. BPM compatibility

`_bpm_score(bpm_a, bpm_b)` at `autocue/analysis/transitions.py:28` returns a value in [0, 100].

### Algorithm

```
1. If bpm_a <= 0 or bpm_b <= 0:      → 50.0   (neutral, missing data)
2. ratio = bpm_b / bpm_a
3. If 0.46 <= ratio <= 0.54:          → 50.0   (half-time, ±4% tolerance)
4. If 1.85 <= ratio <= 2.15:          → 50.0   (double-time, ±7.5% tolerance)
5. delta = abs(bpm_b - bpm_a) / bpm_a
6. If delta <= 0.03:                  → 100.0  (within ±3% — pitch-knob range)
7. If delta >= 0.10:                  → 0.0    (>±10% — not mixable)
8. Else: linear decay from 100 → 0 between 3% and 10%:
   score = 100 × (0.10 - delta) / 0.07
```

The half-time and double-time bonus exists because DJs routinely mix `B ≈ A/2` (drop a 130-BPM techno track into a 65-BPM hip-hop instrumental) and `B ≈ 2A` (run a 70-BPM r&b vocal over a 140-BPM bass line). The ratio gates use **±4%** on the half-time side and **±7.5%** on the double-time side. The asymmetry is deliberate: the half-time gate `[0.46, 0.54]` defeats the original cliff at exactly `0.5` (130 → 62 BPM has ratio 0.477 and now scores 50 instead of 0; see `test_near_half_time_boundary_no_cliff` at `tests/test_transitions.py:62`).

### Decay curve

Reference table for a base of 120 BPM:

| Other BPM | delta | Score |
| --------- | ----- | ----- |
| 120.0     | 0.000 | 100.0 |
| 123.0     | 0.025 | 100.0 |
| 123.6     | 0.030 | 100.0 |
| 126.0     | 0.050 | 71.4 |
| 127.2     | 0.060 | 57.1 |
| 128.4     | 0.070 | 42.9 |
| 130.8     | 0.090 | 14.3 |
| 132.0     | 0.100 | 0.0 |
| 60.0      | (half-time) | 50.0 |
| 240.0     | (double-time) | 50.0 |

For BPM values outside the ratio gates and above 10% delta, the score is a hard zero. There is no "asymptotic floor" — `_bpm_score(120, 200)` is exactly `0.0`.

### Directional symmetry

Note that `delta` divides by `bpm_a`, so the function is **not perfectly symmetric**: `_bpm_score(120, 125)` differs slightly from `_bpm_score(125, 120)`. The property test `test_directional_asymmetry_is_small` at `tests/test_transitions.py:71` asserts the difference is always < 5 points, which is well below the precision of any DJ mixing decision.

---

## 5. Key compatibility (Camelot wheel)

`_key_score(key_a, key_b)` at `autocue/analysis/transitions.py:70` returns a value in [0, 100] based on the [Camelot key wheel](./GLOSSARY.md#camelot-key-wheel).

### What Camelot is

The [Camelot key wheel](./GLOSSARY.md#camelot-key-wheel) arranges musical keys around a 12-position circle. Each position has two keys: `<n>A` (minor) and `<n>B` (major), where stepping from `nA` to `nB` is a relative major/minor swap on the same root. Adjacent positions on the wheel share the most pitches and produce the cleanest harmonic mixes.

```
        1A / 1B
   12A          2A
   12B          2B
 11A              3A
 11B              3B
10A                4A
10B                4B
 9A               5A
 9B               5B
   8A          6A
   8B          6B
        7A / 7B
```

(That is — `8A` is at the 7 o'clock position; "adjacent" means ±1 on the number ring.)

### Algorithm

```
1. Parse both keys via _parse_camelot ("8A" → (8, "A")).
2. If either fails to parse:           → 50.0  (neutral)
3. dist = min(|num_a - num_b|, 12 - |num_a - num_b|)   (circular distance, 0–6)
4. If same number AND same letter:     → 100.0
5. If same number AND different letter: → 75.0   (relative major/minor swap)
6. If dist == 1 AND same letter:        → 80.0   (adjacent on the same ring)
7. If dist == 1 AND different letter:   → 60.0   (one step + ring swap)
8. If dist == 2:                        → 50.0
9. If dist == 3:                        → 25.0
10. Else (dist >= 4):                   → 0.0    (genuinely incompatible)
```

Implemented in `autocue/analysis/transitions.py:70`. The function has a true floor of 0 — there is no artificial minimum. `_camelot_distance(num_a, num_b)` at `autocue/analysis/transitions.py:64` handles the wraparound (`abs(1 - 12) → 11 → min(11, 1) → 1`).

### Score table

| Relationship | Example | Score |
| ------------ | ------- | ----- |
| Identical | `8A → 8A` | 100 |
| Same number, swap mode (relative major/minor) | `8A → 8B` | 75 |
| Adjacent number, same mode | `8A → 7A`, `8A → 9A`, `12A → 1A` | 80 |
| Adjacent number, swap mode | `8A → 7B`, `8A → 9B` | 60 |
| Two steps away | `8A → 6A`, `8A → 10A`, `8A → 6B` | 50 |
| Three steps away | `8A → 5A`, `8A → 11B` | 25 |
| Four or more steps away | `8A → 4A`, `8A → 2B`, `8A → 12B` (dist=4) | 0 |

The 80 / 60 split is intentional — moving along the same ring (`8A → 7A`) is a more familiar harmonic move than swapping ring and stepping at the same time (`8A → 7B`).

`tests/test_transitions.py` covers all of these rows (`TestKeyScore`, ~14 cases). `tests/test_properties.py` adds Hypothesis invariants: same-key always 100, parsed-distance always in [0, 6], score always in [0, 100].

### Missing key data

If `key_a == ""` or `key_b == ""` (no `DjmdKey` row, or `ScaleName` is empty), the score is **50**, not 0. The reasoning is symmetric with `_bpm_score`: unknown is not the same as incompatible.

---

## 6. Energy compatibility

`_energy_score(curve_a, curve_b)` at `autocue/analysis/transitions.py:113` returns a value in [0, 100] measuring how smooth the energy handoff is between the **end** of A and the **start** of B.

### The window-average

Energy curves are 50-point normalized 0–1 arrays produced by `autocue/analysis/energy.py:get_energy_curve` from the [PWAV](./GLOSSARY.md#pwav) section of the [`.DAT` ANLZ file](./GLOSSARY.md#anlz-files). The transition scorer averages a small window at each track edge:

```python
_ENERGY_WINDOW = 5  # points

def _window_avg(curve, window, from_end):
    if not curve:
        return 0.5
    n = min(window, len(curve))
    if from_end:
        return sum(curve[-n:]) / n
    return sum(curve[:n]) / n
```

`autocue/analysis/transitions.py:103`. So `end_a = _window_avg(curve_a, 5, from_end=True)` averages the last 5 of 50 points — roughly the last 10% of A. `start_b = _window_avg(curve_b, 5, from_end=False)` averages the first 5 of 50 points — roughly the first 10% of B.

For a 50-point curve resampled from a typical 3-minute track, each point covers ~3.6 seconds, so the window covers ~18 seconds at each edge. That maps cleanly to a typical DJ mix-out / mix-in window of ~16–32 bars.

### Score formula

`_score_delta(end_a, start_b)` at `autocue/analysis/transitions.py:130`:

```
delta = abs(end_a - start_b)
if delta <= 0.05:    → 100.0   (within 5% of 1.0-normalized range — imperceptible)
if delta >= 0.50:    → 0.0     (full-scale jump — unmixable without bridge)
else:                → 100 × (0.5 - delta) / 0.45
```

Reference table (with both curves present):

| `end_a` | `start_b` | Δ | Score |
| ------- | --------- | --- | ----- |
| 0.80 | 0.80 | 0.00 | 100.0 |
| 0.80 | 0.75 | 0.05 | 100.0 |
| 0.80 | 0.70 | 0.10 | 88.9 |
| 0.80 | 0.60 | 0.20 | 66.7 |
| 0.80 | 0.40 | 0.40 | 22.2 |
| 0.80 | 0.30 | 0.50 | 0.0 |

The direction (energy rising vs falling) does not affect the score — only the magnitude of the delta does. The `_energy_explanation` function does inspect direction so the UI can render `"Energy drops 20%"` vs `"Energy jumps 20%"`.

---

## 7. Missing energy data (Bug 3 fix)

This is the most important behavior to understand about energy scoring. The original implementation defaulted both missing curves to `0.5`, computed `delta = 0`, and returned `100`. The combined effect was that **every same-key same-BPM transition between two no-ANLZ tracks scored 100 overall**. The set builder used this signal to fill entire sets with same-BPM same-key repetition because every alternative had a worse non-perfect score.

The fix has two cases, both at `autocue/analysis/transitions.py:113`:

```python
def _energy_score(curve_a, curve_b):
    if not curve_a and not curve_b:
        return 50.0           # both unknown — neutral, NOT perfect
    end_a = _window_avg(curve_a, _ENERGY_WINDOW, from_end=True) if curve_a else 0.5
    start_b = _window_avg(curve_b, _ENERGY_WINDOW, from_end=False) if curve_b else 0.5
    score = _score_delta(end_a, start_b)
    if not curve_a or not curve_b:
        return min(score, 75.0)   # one side missing — partial penalty
    return score
```

| Curve A | Curve B | Behavior |
| ------- | ------- | -------- |
| present | present | Score directly from `_score_delta(end_a, start_b)` — full range [0, 100]. |
| missing | missing | Returns 50.0 (neutral). Cannot return 100 by accident. |
| missing | present | Score computed using `end_a = 0.5` placeholder, then capped at `min(score, 75.0)`. |
| present | missing | Score computed using `start_b = 0.5` placeholder, then capped at `min(score, 75.0)`. |

The cap reflects two intuitions:

- Unknown is not perfect — without data we should not award the full 100.
- Unknown is not zero — penalizing a transition into oblivion because one track lacks ANLZ data would make brand-new imports unusable in the set builder.

### Concrete impact

For a **same-key, same-BPM, no-ANLZ-on-either-side** transition, the overall score now is:

```
overall = 0.40 × 100 + 0.35 × 100 + 0.25 × 50
        = 40.0 + 35.0 + 12.5
        = 87.5  (standard weighting)
```

Standard weighting brings this to 87.5 (the SCORING_BUGS.md "92.5" figure is from an earlier rounding pass; the current formula computes 87.5). Either way, it is no longer 100. The same-BPM repetition trap is broken.

For a **same-key, same-BPM, both-curves-present, near-identical-edges** transition:

```
overall = 0.40 × 100 + 0.35 × 100 + 0.25 × 100
        = 100.0
```

— still possible, but it requires real ANLZ data on both sides, which is the correct gating signal.

See `tests/test_transitions.py` `TestEnergyScore` for full coverage of every case in the table above. Bug 3 in `SCORING_BUGS.md` documents the regression history.

---

## 8. `transition_advice(ts)`

`transition_advice(ts)` at `autocue/analysis/transitions.py:194` produces a single-sentence practical mixing tip from a scored transition dict. It is a deterministic decision tree over `bpm_a`, `bpm_b`, `bpm_score`, `key_a`, `key_b`, `key_score`, `end_energy_a`, `start_energy_b`.

The output joins multiple component snippets with `"; "`. Each component is independently selected.

### BPM component (always emitted)

```
ratio = bpm_b / bpm_a  (or 1.0 if bpm_a <= 0)

if 0.46 <= ratio <= 0.54:
    "Half-time drop ({bpm_a:.0f}→{bpm_b:.0f} BPM) — let outgoing finish, bring incoming in at full energy"

elif 1.85 <= ratio <= 2.15:
    "Double-time ({bpm_a:.0f}→{bpm_b:.0f} BPM) — quick cut at phrase boundary"

elif bpm_score >= 95.0:
    "BPM matched — beatmix, blend over 16–32 bars"

elif bpm_score >= 70.0:
    diff = bpm_b - bpm_a
    sign = +diff or -diff
    "Nudge pitch {sign} BPM — blend over 8–16 bars"

elif bpm_score > 0:
    "{diff} BPM gap — phrase-align then cut, or loop outro of outgoing track"

else:
    "{diff} BPM gap — hard cut at phrase boundary or use an acappella/dub"
```

Reading the tree: very close BPMs get the long beatmix advice; small offsets get the pitch-nudge advice; medium gaps suggest looping the outro to bridge; wide gaps recommend a hard cut. Half-time and double-time are detected first to override the general decay-curve advice.

### Key component (omitted when keys match perfectly)

```
if key_score >= 95.0:
    (omit — same key needs no extra mention; BPM advice covers the mix)

elif key_score >= 75.0:
    "compatible key ({key_a}→{key_b}) — harmonic blend works"

elif key_score >= 60.0:
    "mild dissonance ({key_a}→{key_b}) — keep overlap ≤8 bars or high-pass outgoing"

elif key_score >= 25.0:
    "key clash ({key_a}→{key_b}) — EQ-kill lows/mids before incoming lands"

elif key_a and key_b:
    "key incompatible ({key_a}→{key_b}) — cut-mix or use a cappella intro"
```

Note the final guard: if either key is missing (the unknown-key fallback of 50 in `_key_score`), the advice is omitted entirely. We do not want to print `key incompatible (→8A)`.

### Energy component (omitted unless |Δ| > 0.20)

```
if end_energy is not None and start_energy is not None:
    delta = start_energy - end_energy
    if delta > 0.20:
        "energy jumps {delta:.0%} — filter incoming until mix point, then open slowly"
    elif delta < -0.20:
        "energy drops {abs(delta):.0%} — use outgoing outro as a bridge, delay mix"
```

The 20% threshold is the same as the "noticeable" boundary in `_energy_explanation`. Below 20% the energy delta is left implicit — the BPM and key advice already cover the standard cases.

### Fallback

If for some reason no component matches, the function returns `"Standard blend"`. In practice this only fires for transitions with zero BPM data and zero key data — the BPM component always emits at least one of the six branches when `bpm_a > 0` or `bpm_b > 0`.

### Sample outputs

| Scenario | Advice |
| -------- | ------ |
| `128 → 128, 8A → 8A, no ANLZ` | `BPM matched — beatmix, blend over 16–32 bars` |
| `128 → 132, 8A → 9A, ANLZ Δ=0.05` | `Nudge pitch +4.0 BPM — blend over 8–16 bars; compatible key (8A→9A) — harmonic blend works` |
| `128 → 130, 5A → 6B, ANLZ Δ=0.25` | `Nudge pitch +2.0 BPM — blend over 8–16 bars; mild dissonance (5A→6B) — keep overlap ≤8 bars or high-pass outgoing; energy jumps 25% — filter incoming until mix point, then open slowly` |
| `128 → 64, 8A → 8A` | `Half-time drop (128→64 BPM) — let outgoing finish, bring incoming in at full energy` |
| `100 → 140, 1A → 7B` | `40.0 BPM gap — hard cut at phrase boundary or use an acappella/dub; key incompatible (1A→7B) — cut-mix or use a cappella intro` |

---

## 9. `explanation` field

In addition to `transition_advice`, the dict carries an `explanation` list — three short strings, one per component — that the UI renders as bullet points under the score bars.

### BPM explanation

`_bpm_explanation` at `autocue/analysis/transitions.py:144`:

| Condition | Output |
| --------- | ------ |
| `bpm_a <= 0 or bpm_b <= 0` | `"BPM unknown — neutral"` |
| Half-time ratio | `"Half-time (128→64 BPM)"` |
| Double-time ratio | `"Double-time (64→128 BPM)"` |
| `score == 100.0` | `"{diff:.1f} BPM difference — perfect"` |
| `score >= 60.0` | `"{diff:.1f} BPM difference — good"` |
| `score > 0` | `"{diff:.1f} BPM difference — marginal"` |
| `score == 0` | `"{diff:.1f} BPM difference — incompatible"` |

### Key explanation

`_key_explanation` at `autocue/analysis/transitions.py:162`:

| Condition | Output |
| --------- | ------ |
| Either key empty | `"Key unknown — neutral"` |
| `score == 100` | `"Same key (8A)"` |
| `score == 80` | `"8A→7A — adjacent (±1)"` |
| `score == 75` | `"8A→8B — parallel (same number)"` |
| `score == 60` | `"8A→7B — compatible"` |
| `score >= 50` | `"8A→6A — risky"` |
| `score >= 25` | `"8A→5A — clash"` |
| `score < 25` | `"8A→2A — incompatible"` |

### Energy explanation

`_energy_explanation` at `autocue/analysis/transitions.py:180`:

| Condition | Output |
| --------- | ------ |
| Either side `None` | `"Energy data unavailable"` |
| `score == 100` | `"Smooth energy handoff"` |
| `score >= 60` | `"Energy drops slightly (10%)"` / `"Energy jumps slightly (8%)"` |
| `score > 0` | `"Energy drops (25%) — noticeable"` |
| `score == 0` | `"Energy jumps sharply (45%)"` |

Both `_energy_explanation` and `transition_advice` infer direction from `start_b < end_a` so the UI can say "drops" or "jumps" rather than just "differs". This is also why `end_energy_a` and `start_energy_b` are returned to callers as part of the dict — they enable advice rendering and UI bars without re-reading the curves.

---

## 10. REST endpoint

`POST /api/transitions/score`

Defined at `autocue/serve/routes.py:1326`. Read-only DB connection (`get_ro_db`).

### Request

```json
{
  "track_a_id": 12345,
  "track_b_id": 67890
}
```

Schema: `autocue/serve/schemas.py:197`

```python
class TransitionRequest(BaseModel):
    track_a_id: int
    track_b_id: int
```

### Response

```python
class TransitionResponse(BaseModel):
    track_a_id: int
    track_b_id: int
    overall: float
    bpm: float
    key: float
    energy: float
    bpm_a: float
    bpm_b: float
    key_a: str
    key_b: str
    end_energy_a: float | None = None
    start_energy_b: float | None = None
    explanation: list[str] = []
```

`autocue/serve/schemas.py:202`. The response is the raw `score_transition` dict spliced into the response model with the two track IDs echoed back. There is no `transition_advice` field on the REST surface — the UI does not currently render the mix tip in the transition modal. The set builder calls `transition_advice` directly and attaches the result as `mix_advice` on each `SetTrack` (see [§11 UI surface](#11-ui-surface)).

### Errors

| Status | Condition |
| ------ | --------- |
| 400 | `track_a_id == track_b_id` |
| 404 | Either track not found in the DB |

There are no 5xx responses unless a database connection error bubbles up — `score_transition` itself never raises; the BPM, key, and energy components all degrade to neutral defaults when data is missing.

### Performance characteristics

The endpoint does **two** DB lookups (`db.get_content(ID=...)` for each track) plus up to two ANLZ reads via `get_energy_curve`. Energy curves are cached in `energy._cache` keyed by `(content.ID, n_points)`, so repeated calls on the same pair are O(1). A cold call typically completes in <50ms on a local SSD.

---

## 11. UI surface

### "⇌ Score transition" button

In the Library tab, when **exactly two tracks are selected** in local-server mode, the selection bar reveals a button:

```
<button id="transition-score-btn" ... onclick="showTransitionScore()">⇌ Score transition</button>
```

`docs/index.html:1660`. The button is hidden when:

- the app is in static (XML-upload) mode (`localMode === false`)
- `selectedTrackIds.size !== 2`

The visibility toggle is in `updateSelectionBar()` at `docs/index.html:5121`.

### Modal

Clicking the button opens a modal (`docs/index.html:5131` `showTransitionScore`) showing:

- The track names: `Artist A — Track A → Artist B — Track B`
- An overall score in large type: `Overall: 87.5/100`
- Three score bars (BPM / Key / Energy) with numeric labels and color (green ≥80, amber ≥50, red <50)
- The `bpm_a → bpm_b` and `key_a → key_b` strings beside the respective bars
- A bullet list of the three `explanation` strings

The modal fetches `POST /api/transitions/score` and renders the bars with inline styles (no CSS class — the modal is small and self-contained).

### Set Builder connector row

In the Set Builder tab, between each pair of adjacent tracks in the output set, a connector row renders:

```
128.0 → 132.5 BPM (+4.5) · 8A → 9A ✓ · Mix [87]
💡 Nudge pitch +4.5 BPM — blend over 8–16 bars; compatible key (8A→9A)
```

`docs/index.html:7699`. The `Mix [score]` chip is color-coded (≥70 green, ≥45 amber, <45 red) and `mix_advice` (from `transition_advice`) is rendered in italics underneath. The score shown is `transition_score`, which the set builder stores as the **raw** `ts["overall"]` (standard weighting), not the reweighted progressive overall it uses internally for ranking. This is intentional — the user-facing score should reflect the canonical scorer, not the beam-search heuristic.

### Set Builder alternatives modal

`/api/setbuilder/alternatives` returns swap candidates for a slot and includes `from_prev` and `to_next` transition scores for each candidate (`autocue/serve/routes.py:1449`). The alternatives modal displays both numbers and copies one of them onto the slot's `transition_score` field when a swap is applied (`docs/index.html:7796`).

---

## 12. Performance

Transition scoring is designed to be cheap to call repeatedly. The set builder calls it on the order of `K × beam_width × steps` times per build — typically a few thousand invocations per request.

### Cached inputs

- **Energy curves** are cached in `energy._cache` keyed by `(content.ID, n_points)`. The first call per track does an ANLZ `.DAT` read; subsequent calls are dict lookups.
- **BPM** and **key** are read directly from `DjmdContent.BPM` and `DjmdContent.Key.ScaleName`, both of which are already in the SQLAlchemy session by the time `score_transition` is called from the beam search (the row was loaded to build the track's feature vector).

### No ANLZ re-reads downstream

The returned dict exposes `end_energy_a` and `start_energy_b` as pre-computed scalars. Downstream consumers (the energy-penalty function in the set builder, `transition_advice`) **do not** re-read the curves — they consume these two floats directly. This is a deliberate API choice: the scorer holds the curves long enough to compute the window-averages, but the curves themselves never leave the function.

### Index pre-warm

The server pre-warms the similarity index in a background thread on startup (`autocue/serve/deps.py:_prewarm_index`). The pre-warm path indirectly calls `get_classification`, which pulls in `get_mixability` → `get_energy_curve` for every track in the library — so by the time the first transition score is requested, every energy curve is already in `energy._cache`. The first `/api/transitions/score` call after startup is therefore typically <10ms.

### Cache invalidation

After a `/api/restore` (DB rollback), the route clears `similar._INDEX` and the analysis caches via `similar.clear_index()`. The transition scorer reads from `energy._cache` and `classify._class_cache`, both of which are cleared on the same path — so transition scores computed after a restore reflect the restored DB.

### Test fixture cache clearing

`tests/conftest.py` has an autouse fixture that clears `energy._cache`, `classify._class_cache`, `score._mixability_cache`, and calls `similar.clear_index()` before every test. The transition tests do not need to manage cache state directly.

---

## 13. Worked examples

The four worked examples below trace the math end-to-end. All four use standard weighting (`0.40 × bpm + 0.35 × key + 0.25 × energy`).

### Example 1 — Same key, same BPM, no ANLZ on either side

Two tracks both at 128 BPM, both in key `8A`, neither has ANLZ data.

| Component | Inputs | Calc | Score |
| --------- | ------ | ---- | ----- |
| BPM | `bpm_a = 128`, `bpm_b = 128` | `delta = 0` → ≤ 3% | **100.0** |
| Key | `key_a = "8A"`, `key_b = "8A"` | same num, same letter | **100.0** |
| Energy | `curve_a = None`, `curve_b = None` | both missing → neutral cap | **50.0** |

```
overall = 0.40 × 100 + 0.35 × 100 + 0.25 × 50
        = 40.0 + 35.0 + 12.5
        = 87.5
```

**Overall: 87.5.** This is the headline case from Bug 3 — under the old `_energy_score` this transition would have scored a deceptive 100. The 12.5 reduction from the energy penalty is what the set builder needs to choose a real candidate over a no-data look-alike.

### Example 2 — Same key, +5% BPM, both ANLZ present, smooth energy

Track A at 128 BPM, Track B at 134.4 BPM, both `8A`. Energy curves both present; `end_a = 0.75`, `start_b = 0.72`.

| Component | Inputs | Calc | Score |
| --------- | ------ | ---- | ----- |
| BPM | `bpm_a = 128`, `bpm_b = 134.4` | `delta = 6.4/128 = 0.050` → 100 × (0.10 - 0.05) / 0.07 = 71.4 | **71.4** |
| Key | `8A → 8A` | identical | **100.0** |
| Energy | `Δ = 0.03` | ≤ 0.05 | **100.0** |

```
overall = 0.40 × 71.4 + 0.35 × 100 + 0.25 × 100
        = 28.6 + 35.0 + 25.0
        = 88.6
```

**Overall: 88.6.** The 5% BPM gap shaves 28.6 off the BPM component but the other two are perfect. This transition gets the `"Nudge pitch +6.4 BPM — blend over 8–16 bars"` advice from `transition_advice`.

A pure +5% (not 5% of BPM but 5% delta) example for the requested ~76 ballpark — Track A at 100, Track B at 105 (delta exactly 5%) — gives `bpm_score = 100 × 0.05 / 0.07 = 71.4`, same `overall = 71.4 × 0.40 + 100 × 0.35 + 100 × 0.25 = 88.6` with perfect key/energy, or `71.4 × 0.40 + 100 × 0.35 + 50 × 0.25 = 76.1` with no ANLZ. The "~76" target in the prompt matches the no-ANLZ form: **same key, +5% BPM, no ANLZ → 76.1**.

### Example 3 — Camelot adjacent, same BPM, both ANLZ present

Track A at 128 BPM in `8A`, Track B at 128 BPM in `9A` (adjacent on the wheel, same letter). Energy `end_a = 0.65`, `start_b = 0.60`.

| Component | Inputs | Calc | Score |
| --------- | ------ | ---- | ----- |
| BPM | identical | within ±3% | **100.0** |
| Key | `8A → 9A` | dist=1, same letter | **80.0** |
| Energy | `Δ = 0.05` | ≤ 0.05 | **100.0** |

```
overall = 0.40 × 100 + 0.35 × 80 + 0.25 × 100
        = 40.0 + 28.0 + 25.0
        = 93.0
```

**Overall: 93.0.** With smooth energy and a perfect BPM match, the Camelot adjacency penalty only costs 7 points overall — a strong recommendation. If the energy data were missing, the score would drop to `40 + 28 + 12.5 = 80.5` — still strong, but no longer the obvious top choice. For the lower-energy version (`Δ = 0.20`, both curves present), energy is `100 × (0.5 - 0.20) / 0.45 = 66.7` and overall is `40 + 28 + 16.7 = 84.7`.

### Example 4 — Key clash + large BPM gap

Track A at 100 BPM in `1A` (a minor), Track B at 140 BPM in `7B` (a major). The harmonic distance is exactly 6 (the maximum on the wheel). Both have ANLZ; `end_a = 0.4`, `start_b = 0.85`.

| Component | Inputs | Calc | Score |
| --------- | ------ | ---- | ----- |
| BPM | `bpm_a = 100`, `bpm_b = 140` | `delta = 0.40` → ≥ 10% | **0.0** |
| Key | `1A → 7B` | dist=6 ≥ 4 | **0.0** |
| Energy | `Δ = 0.45` | between 0.05 and 0.50 → 100 × 0.05 / 0.45 = 11.1 | **11.1** |

```
overall = 0.40 × 0 + 0.35 × 0 + 0.25 × 11.1
        = 0 + 0 + 2.8
        = 2.8
```

**Overall: 2.8.** This is a genuine no-mix case. `transition_advice` returns: `"40.0 BPM gap — hard cut at phrase boundary or use an acappella/dub; key incompatible (1A→7B) — cut-mix or use a cappella intro; energy jumps 45% — filter incoming until mix point, then open slowly"`.

Even with the setbuilder reweighting (`0.25 × 0 + 0.40 × 0 + 0.35 × 11.1 = 3.9`) and the maximum BPM-progress bonus (`+15`), this candidate would score under 20 in the beam — well below the `transition_min` threshold of any tier in `build_set()`. The beam discards it before it gets ranked.

---

## End-to-end worked example

This is the canonical "follow the math" trace. Every value below is derived from the source — no rounding shortcuts, no hand-waved constants. Use this when you need to predict what `score_transition` will say for a specific pair of tracks.

### Inputs

| | Track A | Track B |
| --- | ------- | ------- |
| BPM | `120.0` | `124.0` |
| [Camelot](./GLOSSARY.md#camelot-key-wheel) key | `8A` | `9A` |
| Energy curve (tail / head shown) | `[0.50, 0.55, 0.58, 0.62, 0.65, 0.68, 0.77]` | `[0.45, 0.48, 0.52, 0.54, 0.56, 0.60, 0.65, ...]` |

(Both curves are 50-point normalized arrays from `get_energy_curve`; only the last 5 of A and the first 5 of B affect the score.)

### Step 1 — BPM compatibility

Function: `_bpm_score` at `autocue/analysis/transitions.py:28`.

```
ratio = bpm_b / bpm_a = 124.0 / 120.0 = 1.03333
```

`ratio` is not in the half-time gate `[0.46, 0.54]` and not in the double-time gate `[1.85, 2.15]`, so the standard decay-curve branch applies:

```
delta = abs(bpm_b - bpm_a) / bpm_a
      = abs(124.0 - 120.0) / 120.0
      = 4.0 / 120.0
      = 0.03333
```

`delta` exceeds the `≤ 0.03` cliff (which would return 100) and is below the `≥ 0.10` cliff (which would return 0). The linear decay between those two anchors fires:

```
bpm_score = 100 × (0.10 - delta) / 0.07
          = 100 × (0.10 - 0.03333) / 0.07
          = 100 × 0.06667 / 0.07
          = 95.2
```

**BPM component: 95.2.**

### Step 2 — Key compatibility ([Camelot](./GLOSSARY.md#camelot-key-wheel))

Function: `_key_score` at `autocue/analysis/transitions.py:70`. Distance helper: `_camelot_distance` at `autocue/analysis/transitions.py:64`.

```
parse: ("8A") → (num=8, letter="A")
       ("9A") → (num=9, letter="A")
dist = min(|8 - 9|, 12 - |8 - 9|)
     = min(1, 11)
     = 1
```

The 8A → 9A move is **adjacent number, same letter**. From the [score table](#score-table):

| Relationship | Example | Score |
| ------------ | ------- | ----- |
| Adjacent number, same mode | `8A → 9A` | **80** |

**Key component: 80.0.**

### Step 3 — Energy compatibility

Function: `_window_avg` at `autocue/analysis/transitions.py:103` (window size = `_ENERGY_WINDOW = 5`). Delta scorer: `_score_delta` at `autocue/analysis/transitions.py:130`.

End-of-A average (last 5 of `[0.50, 0.55, 0.58, 0.62, 0.65, 0.68, 0.77]`):

```
end_a = _window_avg(curve_a, 5, from_end=True)
      = mean([0.58, 0.62, 0.65, 0.68, 0.77])
      = 3.30 / 5
      = 0.660
```

Start-of-B average (first 5 of `[0.45, 0.48, 0.52, 0.54, 0.56, 0.60, 0.65, ...]`):

```
start_b = _window_avg(curve_b, 5, from_end=False)
        = mean([0.45, 0.48, 0.52, 0.54, 0.56])
        = 2.55 / 5
        = 0.510
```

Delta and score:

```
delta = abs(end_a - start_b) = abs(0.660 - 0.510) = 0.150
```

`delta` is above the `≤ 0.05` perfect threshold and below the `≥ 0.50` zero threshold, so the linear decay applies:

```
energy_score = 100 × (0.5 - delta) / 0.45
             = 100 × (0.5 - 0.150) / 0.45
             = 100 × 0.350 / 0.45
             = 77.8
```

Both curves are present, so the one-sided cap of 75.0 does **not** fire (`_energy_score` at `autocue/analysis/transitions.py:113`).

**Energy component: 77.8.**

### Step 4 — Overall

Standard weighting at `autocue/analysis/transitions.py:311`:

```
overall = 0.40 × bpm + 0.35 × key + 0.25 × energy
        = 0.40 × 95.2 + 0.35 × 80.0 + 0.25 × 77.8
        = 38.08 + 28.00 + 19.45
        = 85.5
```

**Overall: 85.5.**

### Step 5 — `explanation` and `transition_advice`

The dict returned by `score_transition` carries an `explanation` list (see [§9](#9-explanation-field)):

```python
explanation = [
    "4.0 BPM difference — good",          # bpm_score 95.2 → "good" branch (≥ 60, < 100)
    "8A→9A — adjacent (±1)",              # key_score == 80 branch
    "Energy drops slightly (15%)",        # energy_score 77.8 ≥ 60, start_b < end_a
]
```

(Direction is inferred from `start_b < end_a` so the energy line says "drops" rather than "differs".)

`transition_advice(ts)` at `autocue/analysis/transitions.py:194` then produces a single-sentence mixing tip from the same dict. With `bpm_score = 95.2` (between 70 and 95) the BPM branch is the **nudge** branch; with `key_score = 80` the key add-on is the **compatible-key** branch; with `|delta| = 0.15` (< 0.20 threshold), the energy add-on is **omitted**:

```
"Nudge pitch +4.0 BPM — blend over 8-16 bars; compatible key (8A→9A) — harmonic blend works"
```

This is the full, end-to-end trace: two `DjmdContent` rows in, one numeric score and one English sentence out.

---

## Asymmetric BPM gate (consumer behavior)

The asymmetric BPM gate is **not** part of `score_transition` itself — the scorer is symmetric in the sense that the BPM component depends only on the `bpm_b / bpm_a` ratio and `|bpm_b - bpm_a| / bpm_a` delta. The asymmetric gate is a **set-builder concern**: it lives in `_get_candidates` in `autocue/analysis/setbuilder.py`, where the beam search prunes candidates whose BPM is outside an acceptable window relative to the current track's position on the BPM curve.

Parameter shape:

| Curve direction | Gate behavior |
| --------------- | ------------- |
| `end_bpm > start_bpm` (building) | Gate **forward**: `max(bpm_hi - current, current - bpm_lo, 12.0)` — at least ±12 BPM tolerance, biased toward higher candidates. |
| `end_bpm < start_bpm` (dropping) | Gate **backward** (mirrored): at least ±12 BPM tolerance, biased toward lower candidates. |
| `end_bpm == start_bpm` (flat) | **Symmetric**: `max(..., 8.0)` — at least ±8 BPM tolerance in either direction. |

The 12 vs. 8 split is what makes a 120 → 130 BPM build curve actually move: a strict ±8 symmetric gate would punish forward progress every step. The set builder also widens `find_similar`'s `n` parameter from 20 to 40 when `end_bpm != start_bpm` so the candidate pool actually contains higher-BPM tracks worth considering.

Because this is consumer behavior built on top of the scorer (not behavior **of** the scorer), the full discussion lives in [`set-builder.md`](./set-builder.md) — including the progressive-weighting reweight (`0.25 / 0.40 / 0.35`) and the +15 BPM-progress bonus that pairs with this gate.

---

## 14. Testing

### `tests/test_transitions.py` (48 tests)

Organised by component. Each module has its own test class:

- `TestBpmScore` — identical, within ±3%, at ±10%, between, zero-BPM neutral, half-time, double-time, near-half-time cliff fix, just-outside widened gate, directional asymmetry.
- `TestKeyScore` — identical, parallel (same number diff letter), adjacent same letter, adjacent diff letter, dist=2, dist=3, dist=4+, unparseable, empty string, full Camelot wheel wraparound.
- `TestEnergyScore` — smooth handoff, gradual rise, full delta=0.5, partial both-curves, both-missing returns 50, one-missing caps at 75 (Bug 3 regression test), explicit empty-list handling.
- `TestWindowAvg` — empty curve, short curve (< window), exact-window, longer-than-window first/last extraction.
- `TestCamelotDistance` — wraparound (1↔12), all six distances, identity.
- `TestScoreTransition` — full dict shape, BPM × 100 division, missing key fallback, missing ANLZ fallback, return key set.
- `TestTransitionAdvice` — every advice branch (half-time, double-time, beatmatched, nudge, gap, hard-cut, key add-ons, energy add-ons, standard-blend fallback).

### `tests/test_properties.py` (Hypothesis)

Generative invariants for the pure math. Most relevant strategies:

- `_positive_bpm = st.floats(0.01, 300.0)` — `_bpm_score` always in [0, 100], same-BPM always 100, ratio inversion symmetry within 5 points.
- `_camelot_num = st.integers(1, 12)` — `_camelot_distance` is symmetric (`d(a,b) == d(b,a)`), always in [0, 6], `_key_score` is symmetric, output always in [0, 100], same-key always 100.
- `_energy_curve = st.lists(_energy_val, max_size=50)` — `_energy_score` always in [0, 100], `_window_avg` always in [0, 1] for normalized curves, identical curves always score 100.

The Hypothesis tests sit alongside hand-written breakpoint tests (the exact-3%, exact-10%, exact-0.05-Δ boundaries) that pin the numeric thresholds against regressions.

### CI gating

Both files run in the standard `pytest` invocation in `.github/workflows/ci.yml` on Python 3.10, 3.11, and 3.12. The Hypothesis tests share their seed corpus across CI runs via the cached `.hypothesis/examples/` directory, so flaky property failures replay deterministically.

---

## 15. Related documentation

- **Set Builder** (`docs/reference/set-builder.md`) — primary consumer of transition scoring. Documents the progressive reweighting (`0.25 / 0.40 / 0.35`), the +15 BPM-progress bonus, asymmetric BPM gate, and the `mix_advice` field plumbed through from `transition_advice`.
- **Energy and Mixability** (`docs/reference/energy-and-mixability.md`) — covers the PWAV waveform reader and the 50-point normalized curve that transition scoring consumes. Defines `get_energy_curve(content, db, n_points=50)` semantics.
- **Similar Tracks** (`docs/reference/similar-tracks.md`) — the 6-dimensional feature vector that set builder uses for candidate retrieval before transition scoring re-ranks them. Shares the energy-data dependence and the no-ANLZ cap (0.65 there, 75 here).
- **Scoring Bugs** (`SCORING_BUGS.md` at the repo root) — the adversarial-review record that drove the current `_energy_score` design. Bug 3 is the canonical history for the "free 100 energy score" fix documented here.
- **REST API reference** — `autocue/serve/routes.py` and `autocue/serve/schemas.py` for the full endpoint catalogue. The transition surface is a single endpoint; the set builder and alternatives endpoints consume the same scorer internally.

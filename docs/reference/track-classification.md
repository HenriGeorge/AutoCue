# Track Classification

> Deterministic, no-ML categorisation of every track in a Rekordbox library
> into five DJ-set roles based on BPM, PWAV energy curves, and a vocal proxy.

`autocue/analysis/classify.py` is the scoring core. It takes a single
`DjmdContent` row plus a live `Rekordbox6Database` handle and returns a dict
of five fuzzy membership scores in `[0.0, 1.0]` plus the argmax (`primary`).
There is no training, no model file, and no network call — the result is a
pure function of three observable inputs:

1. **BPM** (`DjmdContent.BPM`, stored as integer × 100).
2. **PWAV energy curve** (mean + peak) from `get_energy_curve()`
   (see [`energy-and-mixability.md`](./energy-and-mixability.md)).
3. **Vocal proxy** (`bool`) from `get_mixability()`.

---

## 1. Overview

A DJ set has shape. The opening warmup, the build, the peak hour, the
after-hours wind-down, the closing track — each role wants a different kind
of track. AutoCue classifies every track in the library into one of five
roles so the rest of the app can reason about "where in a set" a track fits.

The classification feeds three other surfaces:

| Surface | How it uses the score |
|---|---|
| **Playlist Suggest** (`/api/playlists/suggest`) | Ranks every track in the library (or a chosen playlist) by `scores[category]` for the requested category, then samples weighted by score² so picks are good but not deterministic. |
| **Set Builder** (`/api/setbuilder`) | Beam search uses category as one of the soft constraints when scoring next-track candidates. `get_classification()` is pre-warmed during similarity indexing so per-step lookup is O(1). |
| **Similar tracks** (`/api/tracks/{id}/similar`) | The similarity index in `similar.py` pre-warms `_class_cache` via `_index_track()` so the similar-by-category UI chip works instantly. |
| **Auto-Tag** (`/api/auto-tag`) | When `tag_types` includes `"category"`, `apply_classification_tags()` writes the `primary` category as a Rekordbox My Tag, but **only when the confidence ≥ 0.70** (see `auto_tag.MIN_SCORE`). |
| **UI library view** | Each track card shows the category badge with its colour from `_CATEGORY_COLORS` (`docs/index.html`). |

The five categories are mutually fuzzy. A track sitting at 122 BPM with
moderate energy will score well on both `build` and `after_hours` — the
multi-score output is the point. Downstream callers can either pick the
argmax (`primary`) or rank-filter on a specific category's raw score.

---

## 2. The five categories

The trapezoidal BPM and energy ranges below are the **knees** of the
membership function. The score ramps from 0 to 1 across the outer-zero →
full range and back down across the full → upper-zero range. Tracks that
fall inside the "full" range for both BPM and energy receive the maximum
score (modulated by the vocal weight).

| Category | BPM full range (zero → full → zero) | Energy mean range | Vocal preference | Typical use |
|---|---|---|---|---|
| **warmup** | 75 → 100 → 100 → 130 | -0.1 → 0.12 → 0.12 → 0.55 (low) | Vocals OK (no penalty) | Opening 30–60 min of the night; gentle introduction |
| **build** | 108 → 123 → 123 → 140 | 0.10 → 0.45 → 0.45 → 0.72 (medium) | Slight instrumental preference (×0.85 if vocals) | Mid-set ramp; lifts the floor toward peak BPM |
| **peak** | 116 → 136 → 136 → 158 | uses **energy_peak** 0.40 → 0.60 → 1.0 → 1.01 (high) | Sparse vocals preferred (×0.80 if vocals) | Prime-time main-floor; biggest moments |
| **after_hours** | 88 → 107 → 107 → 132 | 0.05 → 0.32 → 0.32 → 0.62 (low–medium) | Vocals OK (no penalty) | Late-set/after-hours rooms; deeper grooves |
| **closing** | 55 → 88 → 88 → 118 | -0.1 → 0.12 → 0.12 → 0.55 (low) | Vocals OK | Last-track-of-the-night; downtempo or sub-90 BPM |

Notes:

- `peak` is the **only** category that uses `energy_peak` (`max(curve)`)
  instead of `energy_mean`. EDM build-style productions have a low mean
  but a high peak; the peak field is what catches them
  (see `test_classify.py::TestScoreCategory::test_peak_uses_peak_energy` at
  `tests/test_classify.py:55`).
- `closing` deliberately overlaps with `warmup` at the bottom (both want
  low energy and low BPM); the BPM trapezoid shape disambiguates — a 95-BPM
  low-energy track will score `warmup > closing` because 95 sits in the
  warmup plateau but on the falling ramp of `closing`.
- `closing` includes very high BPM (>148) tracks **only via the prompt's
  description** — in code, the closing trapezoid actually caps at BPM 118
  (`_score_category` for `closing` at `classify.py:81-84`). The
  classification system treats super-high-BPM tracks as outside the closing
  zone; they default to whichever active category they best fit (often
  `peak` or `unknown` when nothing matches).

---

## 3. `get_classification(content, db)` return shape

The function always returns a dict (never `None`) — even when ANLZ data is
missing, the BPM alone produces a partial score.

```python
{
    "primary":     "build",                   # str: argmax of scores, or "unknown" if all zero
    "label":       "Build",                   # str: human-friendly display name (_CATEGORY_LABELS)
    "color":       "#fa0",                    # str: hex chip colour (_CATEGORY_COLORS)
    "confidence":  0.842,                     # float: top_score, rounded to 3dp
    "scores": {                               # dict[str, float]: 0.0–1.0 per category
        "warmup":      0.121,
        "build":       0.842,
        "peak":        0.310,
        "after_hours": 0.456,
        "closing":     0.000,
    },
    "bpm":         123.5,                     # float: BPM / 100, rounded to 2dp
    "energy_mean": 0.487,                     # float | None: rounded to 3dp, or None
    "energy_peak": 0.712,                     # float | None: rounded to 3dp, or None
    "vocal_proxy": False,                     # bool: from get_mixability()["vocal_proxy"]
}
```

Source: `autocue/analysis/classify.py:122-132`.

The REST schema (`ClassificationResponse` at `autocue/serve/schemas.py:173`)
mirrors this exactly but adds the calling `track_id`. The `label`, `color`
and `confidence` fields are derived for UI convenience — downstream code
that scores against the dict should use `scores[category]` directly.

---

## 4. The trapezoidal membership function

```python
def _trap(value, lo_zero, lo_full, hi_full, hi_zero):
    """Trapezoidal membership: 1.0 in [lo_full, hi_full], 0.0 outside [lo_zero, hi_zero]."""
    if value <= lo_zero or value >= hi_zero:
        return 0.0
    if lo_full <= value <= hi_full:
        return 1.0
    if value < lo_full:
        return (value - lo_zero) / max(lo_full - lo_zero, 1e-9)
    return (hi_zero - value) / max(hi_zero - hi_full, 1e-9)
```

Source: `autocue/analysis/classify.py:35-43`.

Shape (1.0 plateau in the middle, linear ramps on either side, zero outside):

```
        1.0 ┤        ┌────────────┐
            │       /              \
            │      /                \
        0.5 ┤     /                  \
            │    /                    \
            │   /                      \
        0.0 ┤──┘                        └──────
            └──┬──────┬───────────┬──────┬────
            lo_zero lo_full   hi_full  hi_zero
```

Key invariants verified by `tests/test_properties.py:36` (Hypothesis):

- **Output bounded**: always in `[0.0, 1.0]` regardless of input (`test_output_in_unit_interval`).
- **Plateau is exactly 1.0**: any value in `[lo_full, hi_full]` returns 1.0 (`test_plateau_is_exactly_one`).
- **Monotonic on rising ramp**: `v1 ≤ v2` ⇒ `_trap(v1) ≤ _trap(v2)` when both fall on the ramp up.
- **Monotonic on falling ramp**: `v1 ≤ v2` ⇒ `_trap(v1) ≥ _trap(v2)` when both fall on the ramp down.
- **Continuous at corners**: no jump at `lo_zero`, `lo_full`, `hi_full`, `hi_zero`.
- **Midpoint of ramp is 0.5** (`test_midpoint_rising_ramp_is_half` at `test_properties.py:179`).

The `max(... , 1e-9)` guard prevents division-by-zero if a caller passes
`lo_zero == lo_full` (degenerate trapezoid that collapses to a half-line).

`lo_zero == lo_full` and `hi_zero == hi_full`: in classify.py's actual
calls, the plateau is collapsed in some categories (e.g. warmup's BPM
plateau is 100→100, a triangle peak at 100). This is intentional — the
trapezoid degrades gracefully into a triangular membership when the
plateau collapses.

---

## 5. Per-category formulas

All five formulas live in `_score_category()` at `classify.py:46-86`. Each
combines a BPM trapezoid, an energy trapezoid, and an optional vocal weight.

The common shape is:

```python
score = bpm_s * (eng_s * 0.60 + 0.40) * vocal_f
```

The `(eng_s * 0.60 + 0.40)` term means **energy contributes 60% of the
non-BPM signal, with a 40% floor**: even an energy score of 0 still leaves
40% of the BPM contribution intact. This stops a track that's perfectly in
the BPM zone but slightly off on energy from being scored as zero.

The vocal weight `vocal_f` is 1.0 unless the category cares about vocals.

### 5.1 warmup (`classify.py:58-61`)

```python
bpm_s = _trap(bpm, 75, 100, 100, 130)
eng_s = _trap(energy_mean, -0.1, 0.12, 0.12, 0.55) if energy_mean is not None else 0.5
return bpm_s * (eng_s * 0.60 + 0.40)
```

- BPM triangle peaks at 100 BPM, ramps down to zero at 75 and 130.
- Energy triangle peaks at 0.12 (very low), ramps to zero at -0.1 and 0.55.
- No vocal preference.

### 5.2 build (`classify.py:63-67`)

```python
bpm_s   = _trap(bpm, 108, 123, 123, 140)
eng_s   = _trap(energy_mean, 0.1, 0.45, 0.45, 0.72) if energy_mean is not None else 0.5
vocal_f = 0.85 if vocal_proxy else 1.0
return bpm_s * (eng_s * 0.60 + 0.40) * vocal_f
```

- BPM triangle peaks at 123, the canonical "house build" tempo.
- Energy triangle peaks at 0.45, mid-band.
- Vocals get a 15% penalty (`×0.85`) — build sections favour instrumental
  loops that won't fight the next track's vocal.

### 5.3 peak (`classify.py:69-74`)

```python
epeak  = energy_peak
eng_s  = _trap(epeak, 0.40, 0.60, 1.0, 1.01) if epeak is not None else 0.5
bpm_s  = _trap(bpm, 116, 136, 136, 158)
vocal_f = 0.80 if vocal_proxy else 1.0
return bpm_s * (eng_s * 0.60 + 0.40) * vocal_f
```

- **Only category that reads `energy_peak`**. EDM build/drop tracks can
  have a low mean but a 0.9+ peak; this is the right signal.
- Energy trapezoid plateau is 0.60 → 1.0 (wide plateau — peak tracks span
  a big intensity band).
- `hi_zero = 1.01` (just above 1.0) so the ramp-down never actually
  triggers — a fully-saturated peak track still scores 1.0.
- BPM triangle peaks at 136, ramps to zero at 116 and 158.
- Vocals get a 20% penalty (`×0.80`) — peak tracks want big sub-bass and
  drops, not full vocal hooks fighting the next mix.

### 5.4 after_hours (`classify.py:76-79`)

```python
bpm_s = _trap(bpm, 88, 107, 107, 132)
eng_s = _trap(energy_mean, 0.05, 0.32, 0.32, 0.62) if energy_mean is not None else 0.5
return bpm_s * (eng_s * 0.60 + 0.40)
```

- BPM triangle peaks at 107 — classic deep-house / minimal tempo.
- Energy triangle peaks at 0.32, broader than warmup's 0.12.
- No vocal preference.

### 5.5 closing (`classify.py:81-84`)

```python
bpm_s = _trap(bpm, 55, 88, 88, 118)
eng_s = _trap(energy_mean, -0.1, 0.12, 0.12, 0.55) if energy_mean is not None else 0.5
return bpm_s * (eng_s * 0.60 + 0.40)
```

- BPM triangle peaks at 88, ramps to zero at 55 and 118.
- Same energy band as `warmup`.
- No vocal preference.

---

## 6. Energy fallback (Bug 2 fix)

`SCORING_BUGS.md` documents the original bug and its fix. The relevant code
is the `if energy_mean is not None else 0.5` branch in every formula.

### Before the fix

Earlier versions of `_score_category` did:

```python
neutral_energy = 0.5 if energy_mean is None else energy_mean
eng_s = _trap(neutral_energy, ...)
```

That default of `0.5` fell **inside the full zone** of both `after_hours`
(plateau 0.32 — but the older trapezoid was 0.2–0.50, so 0.5 was on the
upper edge) and `build` (plateau 0.45 — older trapezoid 0.3–0.60, so 0.5
was inside). Result: every BPM-matching unanalyzed track scored
`eng_s = 1.0` ⇒ category score ≈ 100%.

### The fix

```python
eng_s = _trap(energy_mean, -0.1, 0.12, 0.12, 0.55) if energy_mean is not None else 0.5
```

When `energy_mean is None`, the trapezoid is **bypassed** and `eng_s` is
clamped to a literal `0.5`. Combined with the `(eng_s * 0.60 + 0.40)` term,
that gives `(0.5 × 0.60 + 0.40) = 0.70` as the cap on the non-BPM factor.

So for a perfectly in-zone BPM, an unanalyzed track scores **at most 0.70**.
A real high-energy track sitting on the BPM plateau scores higher than any
unanalyzed track ever can, which is what we want: the system trusts ANLZ
data more than the absence of it.

This cap is verified by `tests/test_classify.py:110-131`
(`TestNoEnergyPenalty`):

```python
def test_after_hours_no_energy_capped(self):
    # BPM 110 near after_hours peak (107); no energy → capped at bpm_s * 0.70
    score = _score_category(110.0, None, None, False, "after_hours")
    assert score <= 0.70 + 1e-9

def test_with_energy_can_exceed_70(self):
    # after_hours BPM 110, real energy 0.35 (near peak 0.32) → eng_s ≈ 0.9 → score > 0.70
    score = _score_category(110.0, 0.35, None, False, "after_hours")
    assert score > 0.70
```

### Residual limitation

All unanalyzed tracks in the same BPM range still score **identically** at
~0.70. The cap is an improvement (no more inflated 100% scores) but it is
not differentiation. Playlist Suggest mitigates this by sampling weighted
on `score²` from the top pool — unanalyzed tracks still appear, but they
are no longer guaranteed to dominate the picks.

The fix lives in **every** category formula in `_score_category()`
(`classify.py:60, 65, 71, 78, 83`).

---

## 7. `primary` selection

After scoring all five categories, the primary is the simple argmax:

```python
primary = max(scores, key=lambda k: scores[k]) if any(scores.values()) else "unknown"
```

Source: `classify.py:119`.

Notes:

- Ties are broken by Python's `max()` — which returns the first key found.
  In CPython that's the iteration order of the underlying dict, which here
  matches the insertion order of the `CATEGORIES` tuple: warmup, build,
  peak, after_hours, closing. A tied score therefore prefers warmup over
  build, build over peak, etc. This rarely matters in practice (ties at
  arbitrary float precision are vanishingly rare) but it is deterministic.
- If **all** scores are exactly 0.0 (BPM of 0 or BPM outside every
  category's outer zero), `primary = "unknown"`. The `confidence` field
  becomes `0.0` and the UI renders a grey chip via the `_CATEGORY_COLORS`
  fallback `"#888"`.

---

## 8. Caching

```python
_class_cache: dict = {}  # content.ID → classification dict
```

Source: `classify.py:16`. The cache is module-level, keyed by **`content.ID`**
(integer track ID — not the BPM or any feature combination). One entry per
track. Cache hits return the full dict immediately at `classify.py:97-98`.

### Cache lifecycle

| When | What happens |
|---|---|
| Server starts | Empty cache. |
| First call for a track | `get_classification()` computes scores, stores in `_class_cache`. |
| Subsequent calls for same track | Returns cached dict (O(1)). |
| Similarity index build | `similar._index_track()` pre-warms `_class_cache` for every track so beam search lookup is O(1) (see CLAUDE.md). |
| `/api/classify?force_refresh=true` | `_class_cache.clear()` before streaming (see `routes.py:1260-1261`). |
| `/api/restore` | After DB restore, the restore route clears all analysis caches (see CLAUDE.md). |
| Test setup | The autouse fixture in `tests/conftest.py` clears `_class_cache` before every test. |

### Cache invariants

- The dict stored in the cache is the **exact same object** returned to the
  caller. Callers must not mutate it — doing so would poison every future
  hit. In practice callers either read it once or serialise it to JSON.
- The cache does **not** persist across server restarts. After any code
  change to `classify.py`, the server must be restarted (see SCORING_BUGS.md
  closing note).

---

## 9. REST endpoints

### `GET /api/tracks/{track_id}/classification`

Source: `routes.py:1149-1156`.

Returns a single `ClassificationResponse` (`schemas.py:173-184`) for one
track. 404 if `track_id` is not in the library.

```http
GET /api/tracks/5247/classification HTTP/1.1
```

```json
{
  "track_id": 5247,
  "primary": "build",
  "label": "Build",
  "color": "#fa0",
  "confidence": 0.842,
  "scores": {"warmup": 0.121, "build": 0.842, "peak": 0.310, "after_hours": 0.456, "closing": 0.000},
  "bpm": 123.5,
  "energy_mean": 0.487,
  "energy_peak": 0.712,
  "vocal_proxy": false
}
```

### `GET /api/classify` (SSE)

Source: `routes.py:1249-1301`.

Streams one `ClassificationResponse` JSON event per track in the library
(or in a specified playlist), then a `{"done":true,...}` summary event.

Query parameters:

- `playlist_id: int | None` — scope to a single playlist's tracks.
  Resolves via `DjmdPlaylist`/`DjmdSongPlaylist`; 404 if the playlist
  doesn't exist.
- `force_refresh: bool` (default `false`) — clear `_class_cache` before
  scoring. Use after recomputing PWAV / changing BPM in Rekordbox.

SSE event format:

```
data: {"track_id":5247,"primary":"build","label":"Build",...}\n\n
data: {"track_id":5248,"primary":"peak","label":"Peak",...}\n\n
...
data: {"done":true,"total":3247,"counts":{"warmup":820,"build":1102,"peak":643,"after_hours":511,"closing":171,"unknown":0}}\n\n
```

Per-track exceptions are logged via `logger.exception(...)` and the loop
continues — one bad row never aborts the scan. The summary's `counts` dict
includes `"unknown"` so the UI can render a "could not classify" bucket.

### `POST /api/playlists/suggest` (consumer of classification)

Source: `routes.py:1159-1246`. Documented separately under
[`playlist-suggest.md`](./playlist-suggest.md) — relevant detail here is
that it calls `get_classification(content, db)["scores"].get(category, 0.0)`
for every candidate track, then weighted-samples by `score²` from the top
pool. Seeds bypass the score filter and `exclude_ids`.

---

## 10. UI surface

The web app (`docs/index.html`) consumes classification in three places:

1. **Track card category chip**. Each row in the Library tab shows a small
   pill with `data["label"]` text and `data["color"]` background. The chip
   comes from the per-track GET endpoint, lazily fetched as cards become
   visible (IntersectionObserver). Unanalyzed tracks (`primary == "unknown"`)
   show a grey chip.
2. **Classification panel**. The per-track dialog (when you click the
   chip) shows the full breakdown — a horizontal bar for each category
   labelled with its score 0.00–1.00. The `energy_mean`, `energy_peak`,
   `vocal_proxy` fields are listed below.
3. **Library Health → Classification scan**. The "Classify library" button
   in the Library Health panel uses `/api/classify` (SSE) and updates a
   progress counter + a histogram of category counts as events stream in.
   On completion the summary line shows the five counts.

The Discover tab and Set Builder tab do not display classification
directly — they consume it server-side (as a soft constraint in beam
search) and surface only the resulting picks.

---

## 11. Property tests

`tests/test_properties.py:36-185` covers the trapezoid math with
**Hypothesis** (in the `[dev]` extra). Hypothesis generates ~100 random
inputs per `@given` decorator and shrinks failing cases to a minimal
counter-example.

Key invariants:

| Test | What it asserts |
|---|---|
| `test_output_in_unit_interval` | For any finite inputs forming a well-ordered trapezoid, output is in `[0.0, 1.0]`. |
| `test_plateau_is_exactly_one` | For any value in `[lo_full, hi_full]`, the output is **exactly** 1.0 (not 0.999…). |
| `test_rising_ramp_nondecreasing` | On the rising ramp, `v1 ≤ v2 ⇒ _trap(v1) ≤ _trap(v2)`. |
| `test_falling_ramp_nonincreasing` | On the falling ramp, `v1 ≤ v2 ⇒ _trap(v1) ≥ _trap(v2)`. |
| `test_continuous_at_*` | No jump at any of the four corner points. |

If Hypothesis is missing the property test module fails at collection time
— install via `pip install -e ".[dev]"`.

---

## 12. Examples

Worked examples for five real-world track shapes. All four float scores are
rounded to 3dp. The vocal proxy is set to `False` unless stated.

### Example A — Solid warmup track

- BPM 96, `energy_mean` 0.18, `energy_peak` 0.30, no vocals.
- `warmup`: BPM trapezoid `_trap(96, 75, 100, 100, 130) = (96-75)/25 = 0.84`.
  Energy trapezoid `_trap(0.18, -0.1, 0.12, 0.12, 0.55) = (0.55-0.18)/(0.55-0.12) = 0.86`.
  Score = `0.84 × (0.86 × 0.60 + 0.40) = 0.84 × 0.916 = 0.770`.
- `closing`: BPM `_trap(96, 55, 88, 88, 118) = (118-96)/30 = 0.733`.
  Energy same as warmup = 0.86. Score = `0.733 × 0.916 = 0.671`.
- `after_hours`: BPM = 0.42 (96 is on the rising ramp before 107).
  Energy trapezoid is 0 (0.18 below 0.32 plateau but inside ramp:
  `(0.18-0.05)/(0.32-0.05) = 0.481`). Score ≈ `0.42 × 0.689 = 0.290`.
- `primary = "warmup"`, `confidence = 0.770`.

### Example B — Classic 124-BPM house build

- BPM 124, `energy_mean` 0.52, `energy_peak` 0.68, no vocals.
- `build`: BPM `_trap(124, 108, 123, 123, 140) = (140-124)/17 = 0.94`.
  Energy `_trap(0.52, 0.1, 0.45, 0.45, 0.72) = (0.72-0.52)/(0.72-0.45) = 0.74`.
  Score = `0.94 × (0.74 × 0.60 + 0.40) × 1.0 = 0.94 × 0.844 = 0.793`.
- `peak`: BPM `_trap(124, 116, 136, 136, 158) = 0.40`.
  Energy on `_peak = 0.68`: `_trap(0.68, 0.40, 0.60, 1.0, 1.01) = 1.0`.
  Score = `0.40 × 1.0 = 0.400`.
- `after_hours`: BPM 124 outside plateau (132 is hi_zero), so ramp
  `(132-124)/(132-107) = 0.32`. Energy: 0.52 > 0.32 plateau, on falling
  ramp `(0.62-0.52)/(0.62-0.32) = 0.33`. Score = `0.32 × 0.598 = 0.191`.
- `primary = "build"`, `confidence = 0.793`.

### Example C — Peak-hour banger with vocal

- BPM 132, `energy_mean` 0.55, `energy_peak` 0.92, **vocals present**.
- `peak`: BPM `_trap(132, 116, 136, 136, 158) = (132-116)/(136-116) = 0.80`.
  Energy on peak = 0.92: trapezoid plateau (0.60–1.0) ⇒ `eng_s = 1.0`.
  Vocal weight = 0.80. Score = `0.80 × 1.0 × 0.80 = 0.640`.
- `build`: BPM in plateau (between 123 hi_zero=140) so on falling ramp:
  `(140-132)/(140-123) = 0.47`. Energy 0.55 inside trapezoid plateau →
  `eng_s = 1.0`. Vocal weight = 0.85. Score = `0.47 × 1.0 × 0.85 = 0.400`.
- `primary = "peak"`, `confidence = 0.640`. (Vocal penalty pulled peak
  down from a non-vocal hypothetical 0.800.)

### Example D — Unanalyzed track at 120 BPM (Bug 2 cap in action)

- BPM 120, `energy_mean = None`, `energy_peak = None`, no vocals.
- `build`: BPM `_trap(120, 108, 123, 123, 140) = (120-108)/15 = 0.80`.
  Energy fallback: `eng_s = 0.5` (bypasses the trapezoid). Score =
  `0.80 × (0.5 × 0.60 + 0.40) = 0.80 × 0.70 = 0.560`.
- `peak`: BPM `_trap(120, 116, 136, 136, 158) = (120-116)/20 = 0.20`.
  Energy fallback 0.5. Score = `0.20 × 0.70 = 0.140`.
- `after_hours`: BPM `_trap(120, 88, 107, 107, 132) = (132-120)/(132-107) = 0.48`.
  Energy fallback 0.5. Score = `0.48 × 0.70 = 0.336`.
- `primary = "build"`, `confidence = 0.560`. Note: **without** the Bug 2 fix
  this track would have scored `0.80 × 1.0 = 0.800` for build and similar
  inflated scores across other categories. The 0.70 cap (the `(0.5 × 0.6 +
  0.4)` term) is exactly the "no-energy uncertainty cap" mentioned in
  SCORING_BUGS.md.

### Example E — Sub-90-BPM downtempo closer

- BPM 78, `energy_mean` 0.09, `energy_peak` 0.15, vocals present.
- `closing`: BPM `_trap(78, 55, 88, 88, 118) = (78-55)/(88-55) = 0.697`.
  Energy `_trap(0.09, -0.1, 0.12, 0.12, 0.55) = (0.09-(-0.1))/(0.12-(-0.1)) = 0.864`.
  Score = `0.697 × (0.864 × 0.60 + 0.40) = 0.697 × 0.918 = 0.640`.
- `warmup`: BPM `_trap(78, 75, 100, 100, 130) = (78-75)/(100-75) = 0.12`.
  Score ≈ `0.12 × 0.918 = 0.110`.
- `primary = "closing"`, `confidence = 0.640`. The vocal proxy has no
  effect because `closing` has no vocal weight.

---

## 13. Edge cases

### BPM == 0 (unanalyzed BPM)

Rekordbox sometimes stores `BPM = 0` for tracks that have not been
analyzed. The trapezoid handles this naturally — BPM 0 is below the
`lo_zero` of every active category (warmup's 75 is the lowest), so every
`bpm_s` returns 0.0, every score becomes 0.0, and `primary = "unknown"`.

Verified by `tests/test_classify.py:94-97`:

```python
def test_bpm_zero_gives_zero(self):
    # BPM=0 (unanalyzed) should not score in any active category
    for cat in ("warmup", "build", "peak", "after_hours"):
        assert _score_category(0, 0.5, 0.5, False, cat) == 0.0
```

CLAUDE.md warns more generally: "always check `float(bpm) > 0` before using
BPM in calculations". Classification doesn't need that guard explicitly
because the trapezoid takes care of zeroing the score, but downstream
consumers that read `data["bpm"]` should be aware.

### No energy data (`energy_mean is None`)

Falls into the `else 0.5` branch in every category formula. See section 6.
Max score is ~0.70.

### Both `energy_mean` and `energy_peak` are `None`

Same as above — `peak` uses `energy_peak`, but its `if epeak is not None else 0.5`
branch applies. All five categories cap at ~0.70.

### Category boundaries (e.g. BPM exactly 123)

`_trap` is continuous at corners — the value at `lo_full` is exactly 1.0,
not just "approaches 1.0". A track at exactly 123 BPM will score 1.0 on
the build BPM trapezoid (peak of the triangle, since `lo_full == hi_full ==
123` for build). It will also score on neighbouring categories' ramps:
`peak` at BPM 123 gives `(123-116)/(136-116) = 0.35`. The argmax is
unambiguous.

### Ties between categories

Pure Python `max(dict, key=...)` returns the first occurrence of the
maximum value in iteration order. The `CATEGORIES` tuple
(`"warmup", "build", "peak", "after_hours", "closing"`) defines that order.
Ties at float precision are exceptionally rare in practice but deterministic
when they occur.

### Mixability returns `None`

`get_mixability(content, db)` can return `None` (no phrase data). The code
at `classify.py:111-113` guards this:

```python
mix = get_mixability(content, db)
if mix:
    vocal_proxy = mix["vocal_proxy"]
```

So `vocal_proxy` defaults to `False` when phrase data is missing. This is
the safe choice: the only categories that penalise vocals are `build` and
`peak`, and defaulting to "no vocal" means we don't apply a penalty we
can't justify.

### Negative `lo_zero` for energy trapezoids

`warmup` and `closing` use `lo_zero = -0.1` in their energy trapezoids.
Since `energy_mean` is the average of a 0.0–1.0 normalized curve, it can
never actually be negative — the negative bound just ensures the energy 0.0
floor lies on the rising ramp, not on the zero boundary. A pure-silence
track at `energy_mean = 0.0` gets `(0.0 - (-0.1)) / (0.12 - (-0.1)) = 0.45`
energy score, not zero.

---

## 14. Testing

| File | Lines | What it covers |
|---|---|---|
| `tests/test_classify.py` | 33 tests across `TestTrap`, `TestScoreCategory`, `TestNoEnergyPenalty`, `TestGetClassification` | trapezoid corner / midpoint / ramp tests; per-category scoring sanity (peak uses peak, vocal penalties, BPM=0 = 0); 0.70 cap for unanalyzed tracks; `get_classification` return shape, BPM scaling, primary argmax, vocal-proxy plumbing. |
| `tests/test_properties.py` | Hypothesis property tests for `_trap`, plus `_bpm_score`, `_camelot_distance`, `_key_score`, `_energy_score` from `transitions.py`. The classify section is `TestTrapProperties` at `test_properties.py:36-185`. |
| `tests/conftest.py` | Autouse fixture clears `_class_cache` (plus `energy._cache`, `score._mixability_cache`, `similar.clear_index()`) before every test — see `conftest.py:604-607`. |
| `tests/test_serve_routes.py` | 194 FastAPI TestClient tests including `/api/tracks/{id}/classification`, `/api/classify` SSE, `/api/playlists/suggest` (which calls `get_classification` per track), `/api/auto-tag` with `tag_types=["category"]`. |

The classification cache is cleared between tests, so a unit test never
sees stale state from a previous test's mocked content.

Run all classification tests:

```bash
pytest tests/test_classify.py tests/test_properties.py -v
```

Run just the property tests (slow — Hypothesis generates 100 cases per
`@given`):

```bash
pytest tests/test_properties.py::TestTrapProperties -v
```

---

## 15. Related

- [`energy-and-mixability.md`](./energy-and-mixability.md) —
  `get_energy_curve()` produces the `energy_mean` and `energy_peak` inputs;
  `get_mixability()` produces the `vocal_proxy` boolean.
- [`set-builder.md`](./set-builder.md) — Set Builder beam search uses
  `get_classification()` as a soft constraint and pre-warms the cache
  during similarity indexing.
- [`similar-tracks.md`](./similar-tracks.md) — `similar._index_track()`
  calls `get_classification()` while building the similarity index, so
  per-track classification is already cached when the API serves it.
- [`auto-tag.md`](./auto-tag.md) — `apply_classification_tags()` writes
  the `primary` category as a Rekordbox My Tag when `confidence ≥ 0.70`
  (see `auto_tag.MIN_SCORE`).
- [`playlist-suggest.md`](./playlist-suggest.md) — consumes
  `scores[category]` and seeds.
- [`../FEATURES.md`](../FEATURES.md) — end-user-facing feature list.
- `SCORING_BUGS.md` (repo root) — the adversarial review session that
  produced the 0.70 cap (Bug 2).

---

*Source files referenced:*
*`autocue/analysis/classify.py` · `autocue/serve/routes.py:1149-1301` ·*
*`autocue/serve/schemas.py:173-184` · `tests/test_classify.py` ·*
*`tests/test_properties.py:36-185` · `tests/conftest.py:604-607` ·*
*`SCORING_BUGS.md` (Bug 2)*

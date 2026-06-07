# Energy Curve & Mixability Score

Technical reference for AutoCue's two foundational track-analysis features:

- **Energy curve** — a normalized 0.0–1.0 waveform overview extracted from Rekordbox's `PWAV` analysis tag.
- **Mixability score** — a deterministic 0–100 score blending phrase structure, intro/outro length, vocal proxy, and energy variance.

Both features live in `autocue/analysis/` and are exposed via REST endpoints in `autocue/serve/routes.py`. They feed almost every downstream intelligence feature in AutoCue.

---

## 1. Overview

### Energy curve

`get_energy_curve(content, db, n_points=50)` returns a fixed-length list of 50 floats in the range `[0.0, 1.0]`. Each value represents the average loudness of the corresponding slice of the track. The curve is **derived from Rekordbox's own waveform overview** (the `PWAV` tag inside the `.DAT` ANLZ file), so it only requires that the track has been analysed in Rekordbox — AutoCue does not decode the audio itself.

Downstream consumers:

| Module | How it uses the curve |
| --- | --- |
| `autocue/analysis/score.py` | Energy variance feeds the `energy` component (20%) of the mixability score. |
| `autocue/analysis/classify.py` | `energy_mean` and `energy_peak` drive the trapezoidal membership scoring for `warmup` / `build` / `peak` / `after_hours` / `closing`. |
| `autocue/analysis/similar.py` | `energy_mean` and `energy_variance × 10` are two of the six dimensions in the cosine similarity vector. |
| `autocue/analysis/transitions.py` | The last 5 points (≈track outro) and the first 5 points (≈track intro) become `end_energy_a` / `start_energy_b` for transition scoring. |
| `autocue/analysis/auto_tag.py` | `classify_energy_profile()` produces the `energy_profile` My Tag (`flat` / `build` / `wave` / `drop-then-flat`). The `energy_level` detector reads `energy_mean`. |
| `autocue/analysis/setbuilder.py` | Reads the same classification + similarity scores; never reads the curve directly. |
| `autocue/analysis/comment.py` | Maps `energy_mean` to a 1–10 MIK scale for the comment string. |
| `docs/index.html` | Renders a per-card SVG sparkline and the mini-player canvas waveform (`_drawMiniWaveform`). |

### Mixability score

`get_mixability(content, db)` returns either `None` (no phrase data — track has not been analysed) or a dict with a 0–100 `score` and five sub-components. It answers a single concrete question: **"How easy is it to mix this track in and out?"** That is *not* the same as "is this a good track" — a great vocal banger can score 35 and a dull techno tool can score 90.

Downstream consumers:

| Surface | How it uses the score |
| --- | --- |
| `autocue/serve/routes.py` (`/api/tracks/{id}/mixability`) | One-off lookup for a track card. |
| `docs/index.html` | Per-track mixability chip + tooltip with the component breakdown. |
| `autocue/analysis/setbuilder.py` | Indirectly — the beam search uses `find_similar`, which uses the same primitives. |
| `autocue/analysis/auto_tag.py` (`intro_outro` detector) | Reads `intro_bars` / `outro_bars` from the same primitives. |

---

## 2. Energy curve (PWAV)

### Source of truth: the `.DAT` ANLZ file

Rekordbox writes two analysis files per track:

- `ANLZ0000.DAT` — beat grid (`PQTZ`), waveform overview (`PWAV`), waveform colour preview (`PCOB`).
- `ANLZ0000.EXT` — phrase analysis (`PSSI`), high-resolution colour waveform, song structure.

The energy curve comes from the **`.DAT` file's `PWAV` tag** — **not** the `.EXT` file. This is important:

- `has_phrase` (on the `/api/tracks` row) checks for the `.EXT` file. A track with `has_phrase = False` can still have an energy curve (because the `.DAT` file exists independently).
- Conversely, a track with `has_phrase = True` can still return `None` from `get_energy_curve` if its `.DAT` file is missing or its PWAV tag failed to parse.

### Byte layout

Each `PWAV` entry is a single byte:

```
bit:  7 6 5    4 3 2 1 0
       └─┬─┘   └───┬───┘
       color    amplitude (0–31)
```

AutoCue uses only the amplitude (low 5 bits). The colour nibble is ignored — that information is reserved for the high-res `PWV3` / `PWV5` tags in `.EXT`, which AutoCue does not consume.

See `autocue/analysis/energy.py:15-26`:

```python
def _read_pwav_amplitudes(anlz_dat) -> list[int] | None:
    """Return raw PWAV amplitude values (each 0–31) or None if unavailable."""
    try:
        tag = anlz_dat.get_tag("PWAV")
        if tag is None:
            return None
        entries = tag.content.entries
        if not entries:
            return None
        return [int(e) & 0x1F for e in entries]
    except Exception:
        return None
```

The blanket `except Exception` is deliberate: `pyrekordbox` can raise `ConstError`, `IndexError`, or other low-level errors on unsupported ANLZ format versions. The CLAUDE.md invariant ("wrap ANLZ reads in try/except") is enforced here.

### Normalization, smoothing, downsampling

The pipeline is three stages, all in `get_energy_curve` (`autocue/analysis/energy.py:94-117`):

1. **Normalize** — divide each amplitude by `31.0`, giving values in `[0.0, 1.0]`. The constant `31` is the max value of a 5-bit field, not a tunable parameter — it derives from the PWAV byte format.

2. **Smooth** — 3-point symmetric rolling average (`_smooth_3`). First and last samples are passed through unchanged; interior sample `i` becomes `(curve[i-1] + curve[i] + curve[i+1]) / 3`. This kills the alternating-byte noise common in PWAV data without distorting the overall envelope.

   See `autocue/analysis/energy.py:29-38`:

   ```python
   def _smooth_3(values: list[float]) -> list[float]:
       n = len(values)
       if n < 3:
           return list(values)
       out = [values[0]]
       for i in range(1, n - 1):
           out.append((values[i - 1] + values[i] + values[i + 1]) / 3.0)
       out.append(values[-1])
       return out
   ```

3. **Average-downsample** to exactly `n_points` (default 50). Each output bin is the arithmetic mean of its slice of the smoothed signal. If the source already has `≤ n_points` samples, it is returned untouched — no upsampling, no interpolation.

   See `autocue/analysis/energy.py:41-55`:

   ```python
   def _downsample_avg(values: list[float], n: int) -> list[float]:
       total = len(values)
       if total == 0:
           return []
       if total <= n:
           return list(values)
       result: list[float] = []
       for i in range(n):
           start = int(i * total / n)
           end = int((i + 1) * total / n)
           end = max(end, start + 1)
           chunk = values[start:end]
           result.append(sum(chunk) / len(chunk))
       return result
   ```

   The `end = max(end, start + 1)` guard ensures every bin contains at least one sample, even when `total` is only slightly larger than `n`.

### Why 50 points?

50 is the default `n_points` for every server call. The choice is a compromise:

- High enough to make the `wave` profile detectable (you need enough resolution to see two local maxima).
- Low enough that the JSON payload to the browser is tiny (~250 bytes serialized).
- Round enough that one bin ≈ 2% of the track — easy to reason about ("the energy at 50%" = curve[24..25]).

The pipeline does not assume any particular real-time interval per sample. See section 5 for how to map `position_ms` to a curve index correctly.

---

## 3. `get_energy_curve(content, db, n_points=50)`

### Signature

```python
def get_energy_curve(content, db, n_points: int = 50) -> list[float] | None:
```

- `content` — a `DjmdContent` row (must have `.ID`).
- `db` — a `Rekordbox6Database`. Used for `db.read_anlz_file(content, "DAT")`.
- `n_points` — output length. Defaults to 50.

### Return type

- `list[float]` of length `n_points`, all values in `[0.0, 1.0]`, on success.
- `None` on any failure.

### Failure modes (all return `None`)

| Cause | Detection point |
| --- | --- |
| `.DAT` file missing or unreadable | `db.read_anlz_file(content, "DAT")` returns `None` or raises. |
| ANLZ file has no `PWAV` tag | `anlz_dat.get_tag("PWAV")` returns `None`. |
| `PWAV` tag has empty `entries` | `not entries` short-circuit. |
| Fewer than 2 PWAV entries | `len(raw) >= 2` guard before normalization. |
| `pyrekordbox` raises on unsupported ANLZ version | Outer `try/except Exception` in `get_energy_curve`. |

**Important:** the function never returns `[]` — empty input goes through `_downsample_avg([], n)` which returns `[]`, but the outer guard `if raw and len(raw) >= 2:` skips that branch. So `None` is the only "no data" signal. Always check `if curve is None:` and not `if not curve:` — the latter is fine in practice but conflates "no data" with "all-zero amplitudes" (which is a valid curve).

### Source

```
autocue/analysis/energy.py:94-117
```

---

## 4. Cache keying

### `_cache` is keyed by `(content.ID, n_points)`

```python
# autocue/analysis/energy.py:11-12
_cache: dict[tuple, list[float] | None] = {}
```

The cache key is a **two-tuple**, not just the track ID. This matters because the curve length is part of the result — two calls with different `n_points` produce two different curves, and they must be stored separately. The cache check:

```python
# autocue/analysis/energy.py:100-102
cache_key = (content.ID, n_points)
if cache_key in _cache:
    return _cache[cache_key]
```

### Cache stores `None` too

If the first call returns `None` (no PWAV), that `None` is cached. Subsequent calls return immediately without re-reading the ANLZ file. This is critical for performance — without it, every UI render of an unanalysed track would re-attempt the failed ANLZ read.

Test: `tests/test_energy.py:260-266` (`test_caches_none_result`) verifies that two consecutive calls on a track with no PWAV produce exactly one `db.read_anlz_file` invocation.

### Different `n_points` ⇒ different cache entry

Tested explicitly in `tests/test_energy.py:248-258`:

```python
def test_different_n_points_not_served_from_cache(self):
    # Two calls with different n_points must each parse the file
    db = _make_db([15] * 200)
    content = _make_content(60)
    r1 = get_energy_curve(content, db, n_points=10)
    r2 = get_energy_curve(content, db, n_points=20)
    assert r1 is not None and r2 is not None
    assert len(r1) == 10
    assert len(r2) == 20
    # Both calls must have read the DAT file independently
    assert db.read_anlz_file.call_count == 2
```

### Cache clearing

- `energy.clear_cache()` resets the dict. Called automatically:
  - In every test by the `conftest.py` autouse fixture (see `tests/conftest.py`).
  - In `/api/restore` after a backup is restored, because the stale curve no longer matches the restored ANLZ on disk.
  - In `autocue serve` lifespan teardown.

The CLAUDE.md invariant explicitly calls this out:

> `energy._cache` — keyed by `(content.ID, n_points)` (NOT just the track ID — the curve length is part of the key).

---

## 5. Mapping `position_ms` to a curve index

**It is NOT one float per 150ms column.** This is the single most common mistake when working with the energy curve.

The PWAV format itself uses 150-byte columns (each representing a fixed wall-clock slice of the track), but AutoCue's `get_energy_curve` resamples that to a fixed `n_points` output. The mapping from a `position_ms` to a curve index depends on the **track duration**, not on a constant time-per-bin.

### Correct formula

```python
def position_to_curve_index(position_ms: int, track_length_s: float, curve: list[float]) -> int | None:
    if not curve or track_length_s <= 0:
        return None
    track_ms = track_length_s * 1000
    frac = max(0.0, min(1.0, position_ms / track_ms))
    idx = int(frac * len(curve))
    return min(idx, len(curve) - 1)
```

### What this means in practice

- For a 6-minute (360s) track with a 50-point curve, each bin covers `7.2s` of audio.
- For a 12-minute (720s) extended mix with the same 50-point curve, each bin covers `14.4s`.
- A cue at 90s into the 6-minute track maps to bin `int(90/360 × 50) = 12`. The same 90s cue in the 12-minute track maps to bin `6`.

### Why this surprises people

The raw PWAV header in the `.DAT` file specifies a fixed time-per-column (typically 150ms in Rekordbox 6 / 7 builds). If you read PWAV directly with `pyrekordbox`, you can in fact convert a sample index back to a millisecond offset. But the moment you call `get_energy_curve`, that information is *gone* — it has been re-binned to `n_points`. The resampling destroys the original time index.

The CLAUDE.md invariant for this:

> **PWAV energy curve**: `get_energy_curve(content, db, n_points=50)` returns a fixed-length normalized 0–1 curve (default 50 points), resampled by averaging from the raw PWAV amplitudes in the `.DAT` ANLZ file. It is NOT one-float-per-150ms-column. To map a `position_ms` to an index, scale against the track's duration and `len(curve)` — do not assume 150ms per sample.

---

## 6. Energy profile classification

`classify_energy_profile(curve)` reduces a 50-point curve to one of four labels:

| Label | Heuristic | Typical track |
| --- | --- | --- |
| `flat` | Variance `< 0.05`, or curve length `< 4`. | Minimal techno; ambient warmup tools. |
| `build` | Second-half mean `>` first-half mean `+ 0.05`. | Slow burns; tracks where the drop is near the end. |
| `wave` | Two or more strict local maxima detected. | Multi-drop EDM; arrangements with verse / chorus / verse / chorus pattern. |
| `drop-then-flat` | Default fallback when none of the above hit. | Most classic EDM — early peak, lower second half. |

### Source

```
autocue/analysis/energy.py:58-91
```

```python
def classify_energy_profile(
    curve: list[float],
) -> Literal["flat", "build", "drop-then-flat", "wave"]:
    if len(curve) < 4:
        return "flat"
    n = len(curve)
    mean = sum(curve) / n
    variance = sum((v - mean) ** 2 for v in curve) / n
    if variance < 0.05:
        return "flat"

    # Count local maxima (strict peaks)
    peaks = [
        i for i in range(1, n - 1)
        if curve[i] > curve[i - 1] and curve[i] > curve[i + 1]
    ]
    if len(peaks) >= 2:
        return "wave"

    first_mean = sum(curve[: n // 2]) / (n // 2)
    second_mean = sum(curve[n // 2 :]) / (n - n // 2)

    if second_mean > first_mean + 0.05:
        return "build"

    return "drop-then-flat"
```

### Edge cases

- `curve = []` or `curve = [0.5, 0.9]` → `"flat"` (length guard).
- All values identical → variance 0 → `"flat"`.
- An evenly-rising ramp `[0.0, 0.05, …, 0.95]` of 20 points → `"build"`.
- A two-peak pattern `[0.1, 0.9, 0.1, …, 0.9, 0.1, …]` → `"wave"` (provided variance exceeds 0.05).
- A 5-bar `0.9 → 0.2` cliff that stays flat afterward → `"drop-then-flat"`.

### Consumers

- The `energy_profile` My Tag detector in `autocue/analysis/auto_tag.py`.
- The `energy_profile` field on `EnergyResponse` returned by `/api/tracks/{id}/energy`.
- Tooltips on the track card in `docs/index.html`.

Tests covering all four labels live in `tests/test_energy.py:157-188`.

---

## 7. Mixability score formula

`get_mixability(content, db)` returns a dict with a 0–100 `score` plus a `components` sub-dict that breaks the score down into its five inputs.

### Components

| Component | Weight | Formula | Range |
| --- | --- | --- | --- |
| Intro bars | 25% | `min(intro_bars / 32, 1.0)` | 0–100% |
| Outro bars | 25% | `min(outro_bars / 32, 1.0)` (or `0.5` if `Length` is missing — neutral fallback, *not* zero) | 0–100% |
| Energy consistency | 20% | `1 − min(variance × 5, 1.0)` if curve present, else `0.5` (neutral) | 0–100% |
| Vocal proxy | 15% | `0.3` if any phrase is labelled `VERSE`, else `1.0` | 30% or 100% |
| Phrase structure | 15% | `min(phrase_count / 6.0, 1.0)` | 0–100% |

### Final formula

From `autocue/analysis/score.py:100-106`:

```python
score = max(0.0, min(100.0, (
    intro_score  * 0.25
    + outro_score  * 0.25
    + energy_score * 0.20
    + vocal_score  * 0.15
    + phrase_score * 0.15
) * 100))
```

### Reference constants

```python
# autocue/analysis/score.py:13-17
_INTRO_OUTRO_REFERENCE_BARS = 32      # 32 bars = perfect intro/outro
_VOCAL_SCORE_WITH_VOCALS = 0.3        # vocals get 30% of their weight (penalty)
```

The 32-bar reference is the spec target — well-produced techno and house tracks typically run 16 or 32-bar intros and outros. 16 bars maps to score 50%, 32+ bars to 100%. Going beyond 32 bars does not yield a bonus.

### Reading the intro and outro bars

- **Intro bars** — total bars covered by the **leading run of `INTRO` phrases**, starting from phrase 0. Once a non-INTRO phrase appears, the run terminates. Multiple consecutive INTRO phrases sum.
- **Outro bars** — bars from the start of the *last* `OUTRO` phrase to the track's end (`content.Length × 1000` ms). If `content.Length` is missing or zero, `outro_length_unknown = True` and `outro_score` falls back to `0.5` (neutral) rather than penalising the track.

### Vocal proxy

Naive but effective: `any(lbl == PhraseLabel.VERSE for lbl in phrase_labels)`. A track with any verse phrase is treated as vocal. The 0.3 floor (instead of, say, 0.5) is intentional — DJs typically avoid stacking vocal tracks, so the penalty is stricter than a midpoint.

### Phrase structure

Number of phrases capped at 6 (`min(phrase_count / 6.0, 1.0)`). The reasoning: a track with 6+ phrases gives you 6+ candidate mix points; fewer phrases means fewer options.

### Returns

```python
{
  "score": 78,
  "intro_bars": 32,
  "outro_bars": 16,
  "phrase_count": 5,
  "vocal_proxy": False,
  "energy_variance": 0.0234,
  "outro_length_unknown": False,
  "components": {
    "intro": 100,
    "outro": 50,
    "energy": 88,
    "vocals": 100,
    "structure": 83,
  },
}
```

---

## 8. Genre calibration

The score is not normalized per-genre — the same formula applies to every track. As a result the absolute numbers cluster by genre:

| Genre | Typical mixability range | Why |
| --- | --- | --- |
| Techno (140+ BPM) | 70–80 | Long intros/outros, instrumental, steady energy. |
| House / deep house | 60–70 | Slightly shorter intros, more vocal samples, more variation. |
| Vocal open-format / pop | 30–50 | Short intros, dominant vocal, dramatic dynamics. |
| Drum & bass / breakbeat | 40–60 | High variance, short intros. |
| Ambient / warmup tools | 50–70 | Flat energy (good) but short or non-existent phrase structure (bad). |

These are observations from the score formula, not rules baked into the code. Do not "fix" a vocal pop track scoring 35 — the score correctly reflects that you cannot blend it in over 32 bars.

---

## 9. `get_mixability()` cache

```python
# autocue/analysis/score.py:19-20
_mixability_cache: dict[int, dict] = {}
```

- **Keyed by `content.ID`** (a single int — not a tuple).
- **The cache populates `result` after the full computation** (`score.py:124-125`).
- **`None` results are NOT cached.** Look at `get_mixability` — when `pssi_content is None or pqtz_content is None`, the function returns `None` without inserting into the cache. This is deliberate: a track may gain phrase analysis later (a user re-analyses it in Rekordbox), and we want to pick that up on the next call.
- **The CLAUDE.md invariant calls this out specifically**: `get_mixability` *IS* cached — that changed from an earlier version where it was always recomputed.

### Clearing

The `tests/conftest.py` autouse fixture clears `score._mixability_cache` before every test. In production the cache is cleared on `/api/restore` (alongside `energy._cache`, `classify._class_cache`, and the similarity index).

Test coverage: `tests/test_score.py:186-197` (`test_cache_returns_same_result_second_call`) — the second call deliberately patches `_get_pssi_and_pqtz` to return `(None, None)` and verifies that the cached result is returned anyway (i.e. the cache hit path skipped the patched lookup).

---

## 10. REST endpoints

### `GET /api/tracks/{track_id}/energy`

**Response schema** — `EnergyResponse` in `autocue/serve/schemas.py:146-150`:

```python
class EnergyResponse(BaseModel):
    track_id: int
    energy: list[float] | None = None  # None = PWAV tag unavailable
    n_points: int = 0
    energy_profile: str | None = None  # "flat" | "build" | "drop-then-flat" | "wave"
```

**Implementation** — `autocue/serve/routes.py:1113-1125`:

```python
@router.get("/tracks/{track_id}/energy", response_model=EnergyResponse)
def track_energy(track_id: int, db=Depends(get_ro_db)):
    from ..analysis.energy import classify_energy_profile, get_energy_curve
    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, f"Track {track_id} not found")
    curve = get_energy_curve(content, db)
    return EnergyResponse(
        track_id=track_id,
        energy=curve,
        n_points=len(curve) if curve else 0,
        energy_profile=classify_energy_profile(curve) if curve else None,
    )
```

Notes:

- `n_points` reflects the *actual* length of the returned curve, not the requested `n_points`. The endpoint always uses the `get_energy_curve` default (50).
- When `curve` is `None`, both `energy` and `energy_profile` are `None`. The client must handle this case (see `docs/index.html:5437-5442`).

### `GET /api/tracks/{track_id}/mixability`

**Response schema** — `MixabilityResponse` in `autocue/serve/schemas.py:153-170`:

```python
class MixabilityComponents(BaseModel):
    intro: int
    outro: int
    energy: int
    vocals: int
    structure: int


class MixabilityResponse(BaseModel):
    track_id: int
    score: int | None = None          # None = no phrase data
    intro_bars: int = 0
    outro_bars: int = 0
    phrase_count: int = 0
    vocal_proxy: bool = False
    energy_variance: float | None = None
    outro_length_unknown: bool = False
    components: MixabilityComponents | None = None
```

**Implementation** — `autocue/serve/routes.py:1128-1146`:

```python
@router.get("/tracks/{track_id}/mixability", response_model=MixabilityResponse)
def track_mixability(track_id: int, db=Depends(get_ro_db)):
    from ..analysis.score import get_mixability
    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, f"Track {track_id} not found")
    data = get_mixability(content, db)
    if data is None:
        return MixabilityResponse(track_id=track_id, score=None)
    return MixabilityResponse(
        track_id=track_id,
        score=data["score"],
        intro_bars=data["intro_bars"],
        outro_bars=data["outro_bars"],
        phrase_count=data["phrase_count"],
        vocal_proxy=data["vocal_proxy"],
        energy_variance=data["energy_variance"],
        components=MixabilityComponents(**data["components"]),
    )
```

Note that `outro_length_unknown` is set in the analysis dict but the route does not propagate it to the response model. The client treats `outro_bars = 0` plus `components.outro = 50` as the "unknown" signal.

---

## 11. UI surface

### Per-card SVG sparkline

Each track card on the Library tab fetches `/api/tracks/{id}/energy` lazily (via `IntersectionObserver`) and renders an SVG polyline. `docs/index.html:5430-5459`:

- The polyline coordinates are computed from `pts.map((v, i) => ...)` where `pts` is the 50-point curve from the API.
- The Y axis is inverted (`h - v * h`) so higher energy renders higher on the card.
- A `no waveform` placeholder is shown when `data.energy` is empty or null.

The cached curve is also stored in `_energyCache[trackId]` so the mini player can reuse it without a second fetch:

```javascript
// docs/index.html:5444-5445
const pts = data.energy;
_energyCache[trackId] = pts;  // D4: cache for mini waveform
```

### Mini player canvas waveform

When a track is playing, the bottom-right mini-player shows a 120×22 px canvas with the energy curve as vertical bars, plus a progress overlay and playhead. `docs/index.html:4707-4756`:

- **HiDPI scaling** — `canvas.width = cssW * dpr` so the canvas is physically `120 × devicePixelRatio` pixels wide, then the context is scaled back via `ctx.setTransform(dpr, 0, 0, dpr, 0, 0)`. This avoids the blurry-on-retina problem.
- **Progress fill** — bars before the playhead are rendered in the AutoCue green (`rgba(40,226,20,0.85)`); bars after are dimmed.
- **No-data fallback** — when `curve` is undefined or empty, a simple horizontal progress bar is drawn instead.
- **Playhead** — a single vertical line at `Math.round(W * pct)`, redrawn every RAF frame (see CLAUDE.md note on RAF playhead).
- **Scrub guard** — `if (isScrubbing) return;` at the top prevents the canvas from being repainted while the user is dragging the scrubber range input.

### `_energyCache` JS map

```javascript
// docs/index.html:2592
let _energyCache = {};  // D4: trackId → Float32Array of energy curve
```

Cleared when:

- The library is reloaded (`docs/index.html:2642`).
- The user uploads a new XML (`docs/index.html:6353`).

This is purely a UI-side cache; it has no relationship to the Python `energy._cache`. Clearing one does not clear the other.

---

## 12. Performance characteristics

### First call

- Read `.DAT` file from disk via `pyrekordbox` — a few ms.
- Parse `PWAV` tag — a few ms.
- Normalize, smooth, downsample — microseconds.
- Insert into `_cache`.

Typical first-call latency: **5–20 ms** depending on disk and ANLZ size.

### Subsequent calls

- Dict lookup on `(content.ID, n_points)` — **sub-microsecond**.
- Return the cached list reference (no copy).

### Pre-warming

The similarity index builder (`autocue/analysis/similar.py:_index_track`) calls `get_energy_curve` for every track in the library when it builds the index. Once the index is built (kicked off in a background daemon thread on server startup — see `autocue/serve/deps.py:_prewarm_index`), every track's energy curve is already in `_cache`. The first user-facing fetch of `/api/tracks/{id}/energy` will be a cache hit.

For mixability, `get_mixability` is *not* called by the index builder, so its cache is populated lazily on first request per track. A library-wide call (e.g. `/api/setbuilder` running across many candidates) will warm the cache as a side effect.

### Memory cost

- 50 floats per track × ~3000 tracks ≈ **150k floats ≈ 1.2 MB** for a typical DJ library.
- One mixability dict per track ≈ ~200 bytes × 3000 ≈ **0.6 MB**.

Both caches are bounded by the library size; there is no eviction.

---

## 13. Behavior edge cases

### Track with no ANLZ data at all

- `get_energy_curve` → `None`.
- `get_mixability` → `None` (because `_get_pssi_and_pqtz` also returns `(None, None)`).
- `classify_energy_profile(None)` is *not* a safe call — pass `if curve:` first. The routes already do this.

### Track with PWAV but no PSSI (waveform but no phrase analysis)

- `get_energy_curve` → 50-point curve.
- `get_mixability` → `None` (PSSI required).
- The track shows a sparkline on its card but no mixability score.

### Track with PSSI but no PWAV (phrase analysis but no waveform)

- `get_energy_curve` → `None`.
- `get_mixability` → dict with `score` computed, but the `energy` component falls back to **50%** (neutral) rather than 0. See `score.py:96`:

  ```python
  energy_score = (1.0 - min(variance * 5, 1.0)) if variance is not None else 0.5
  ```

  Test: `tests/test_score.py:112-116` (`test_no_energy_uses_neutral_fallback`) verifies `components["energy"] == 50` in this case.

### Track with `content.Length` missing or zero

- `outro_length_unknown = True`, `outro_score = 0.5`.
- Test: `tests/test_score.py:169-175` (`test_outro_length_unknown_gives_neutral_score`).
- This is intentional — a track with metadata gaps should not be punished as if it had no outro. The neutral fallback keeps it competitive in the set builder.

### Track with no BPM (`bpm = 0`)

- Mixability score itself does not use BPM directly, so it is unaffected.
- Downstream classification, similarity, and transition scoring all guard on `float(bpm) > 0` (CLAUDE.md invariant).
- The mixability score can still be computed.

### All-zero PWAV (silent track)

- `get_energy_curve` → `[0.0, 0.0, …, 0.0]` (50 zeros).
- `classify_energy_profile` → `"flat"` (variance is 0).
- `energy_variance` in mixability → 0.0.
- `energy_score` → 1.0 → `components["energy"] == 100`.

This is a known quirk: a perfectly silent track scores 100 on the energy consistency component. In practice it does not come up — Rekordbox does not generate PWAV for silent tracks; the tag is simply absent.

### Track with only INTRO phrases

- `intro_bars` accumulates correctly (loop continues until a non-INTRO phrase is hit, but there isn't one — every entry is INTRO so the `break` never fires until the loop ends).
- `outro_bars = 0` (no OUTRO phrase).
- `phrase_count = number of INTRO phrases`.
- A real-world example would be a track flagged as one long intro/tool. The score will be moderate — high intro, zero outro, decent structure.

---

## 14. Examples

### A real 50-point energy curve

A typical 6-minute techno track (130 BPM, classic two-build arrangement):

```
[0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.31, 0.35, 0.40,
 0.45, 0.50, 0.55, 0.60, 0.65, 0.68, 0.70, 0.72, 0.73, 0.75,
 0.78, 0.80, 0.82, 0.83, 0.85, 0.85, 0.84, 0.83, 0.82, 0.80,
 0.78, 0.76, 0.73, 0.70, 0.68, 0.65, 0.62, 0.60, 0.55, 0.50,
 0.45, 0.40, 0.35, 0.30, 0.25, 0.22, 0.18, 0.15, 0.12, 0.10]
```

Classification: `classify_energy_profile(curve)` → `"drop-then-flat"`? Actually `"build"` — the second-half mean (0.55) exceeds the first-half mean (0.50). For genuine drop-then-flat, the peak would be in the first half.

### A mixability score breakdown

Track: 6-minute techno, 32-bar intro, 32-bar outro, 5 phrases, instrumental, low energy variance.

| Component | Raw value | Computed |
| --- | --- | --- |
| `intro_bars` | 32 | `intro_score = min(32/32, 1.0) = 1.0` → 100% |
| `outro_bars` | 32 | `outro_score = min(32/32, 1.0) = 1.0` → 100% |
| `energy_variance` | 0.04 | `energy_score = 1.0 - min(0.04 × 5, 1.0) = 0.80` → 80% |
| `vocal_proxy` | `False` | `vocal_score = 1.0` → 100% |
| `phrase_count` | 5 | `phrase_score = min(5/6.0, 1.0) = 0.833` → 83% |

```
score = (1.0×0.25 + 1.0×0.25 + 0.80×0.20 + 1.0×0.15 + 0.833×0.15) × 100
      = (0.25 + 0.25 + 0.16 + 0.15 + 0.125) × 100
      = 0.935 × 100
      = 94
```

Final dict:

```python
{
    "score": 94,
    "intro_bars": 32,
    "outro_bars": 32,
    "phrase_count": 5,
    "vocal_proxy": False,
    "energy_variance": 0.04,
    "outro_length_unknown": False,
    "components": {
        "intro": 100,
        "outro": 100,
        "energy": 80,
        "vocals": 100,
        "structure": 83,
    },
}
```

### A low-scoring vocal pop track

Track: 3-minute vocal pop, 8-bar intro, 4-bar outro, 4 phrases, vocals present, variable energy.

| Component | Raw value | Computed |
| --- | --- | --- |
| `intro_bars` | 8 | `intro_score = 8/32 = 0.25` → 25% |
| `outro_bars` | 4 | `outro_score = 4/32 = 0.125` → 13% |
| `energy_variance` | 0.12 | `energy_score = 1.0 - min(0.12 × 5, 1.0) = 0.40` → 40% |
| `vocal_proxy` | `True` | `vocal_score = 0.3` → 30% |
| `phrase_count` | 4 | `phrase_score = 4/6.0 = 0.667` → 67% |

```
score = (0.25×0.25 + 0.125×0.25 + 0.40×0.20 + 0.30×0.15 + 0.667×0.15) × 100
      = (0.0625 + 0.03125 + 0.08 + 0.045 + 0.10) × 100
      = 0.319 × 100
      = 32
```

A score of 32 correctly reflects that this track is hard to blend — short intro, short outro, vocal, energetic.

---

## 15. Testing

### `tests/test_energy.py` (36 tests)

| Test class | Coverage |
| --- | --- |
| `TestDownsampleAvg` | Length invariants, empty input, single value, output range. |
| `TestReadPwavAmplitudes` | Low-5-bit mask, upper-3-bit ignore, `None` on missing tag, `None` on exception. |
| `TestSmooth3` | Length preserved, endpoints unchanged, interior is 3-sample mean, variance-reduction. |
| `TestClassifyEnergyProfile` | All four labels, short / empty curves return `"flat"`. |
| `TestGetEnergyCurve` | Normalization (31 → 1.0, 0 → 0.0), `n_points` length contract, `None` propagation, **cache keying including `(id, n_points)` tuple**, cache stores `None`, `clear_cache()` forces reparse, exception in ANLZ read → `None`, smoothing visibly applied to output. |

The autouse fixture in `tests/conftest.py` calls `energy_mod.clear_cache()` before every test, so the cache always starts empty.

### `tests/test_score.py` (19 tests)

| Test class | Coverage |
| --- | --- |
| `TestGetMixabilityNoPhraseData` | Returns `None` when PSSI / PQTZ / phrases are missing. |
| `TestGetMixabilityScore` | Required dict keys, score in 0–100, **`None` energy curve → neutral 50% fallback**, flat energy → 100% energy component, **vocal proxy penalty**, vocal floor 30, intro bars computed (32 beats = 8 bars at 500ms/beat), outro bars computed from track end, **`Length = 0` → `outro_length_unknown` + neutral 50%**, **cache: second call returns identical dict even after `_get_pssi_and_pqtz` is patched to return `(None, None)`**, structure cap at 6 phrases. |

### Phrase-fallback contract

`tests/test_score.py:112-116` and `:169-175` together guarantee the two key neutral-fallback behaviours:

1. Missing energy curve → `components.energy = 50`.
2. Missing track length → `components.outro = 50` and `outro_length_unknown = True`.

Both fallbacks are required by the design principle: **metadata absence is not the same as failure**. A track with no `Length` is not worse than a track with a known short outro — it is simply unknown.

---

## 16. Related references

- [`track-classification.md`](./track-classification.md) — how `energy_mean` and `energy_peak` are derived from the curve and used to score `warmup` / `build` / `peak` / `after_hours` / `closing`.
- [`similar-tracks.md`](./similar-tracks.md) — how `energy_mean` and `energy_variance` enter the 6-dim cosine similarity vector, and the data-quality cap when both candidates lack energy data.
- [`transition-scoring.md`](./transition-scoring.md) — how the **last 5 points** of track A and the **first 5 points** of track B become `end_energy_a` and `start_energy_b`, and the neutral 50.0 fallback for missing energy data.
- [`set-builder.md`](./set-builder.md) — how mixability and energy feed the beam search.
- `autocue/analysis/energy.py` — source.
- `autocue/analysis/score.py` — source.
- `autocue/serve/routes.py:1113-1146` — REST endpoints.
- `autocue/serve/schemas.py:146-170` — response schemas.
- `docs/FEATURES.md` — user-facing feature documentation (energy section at line 99, mixability at line 134).
- `CLAUDE.md` — invariants: `energy._cache` keyed by `(content.ID, n_points)`, `get_mixability` IS cached, PWAV `/ 31.0` normalization, `get_energy_curve` returns `None` not `[]`, energy profile labels.

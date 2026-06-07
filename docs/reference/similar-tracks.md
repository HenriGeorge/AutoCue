# Similar Track Discovery — Reference

This document describes AutoCue's similarity engine: a small, in-process
nearest-neighbour search over a six-dimensional feature vector derived from
each track's Camelot key, energy profile, vocal proxy, and BPM. It powers two
user-facing surfaces — the `≈ Similar` button on every track card and the
Set Builder beam search — and is exposed over HTTP at
`GET /api/tracks/{id}/similar`.

Source: [`autocue/analysis/similar.py`](../../autocue/analysis/similar.py)

---

## 1. Overview

The goal of similarity is simple: given a target track, surface the **N most
similar tracks** within a **BPM gate** of ±8 BPM (configurable). Two callers
consume the result:

- **Set Builder** (`autocue/analysis/setbuilder.py:462`) calls
  `find_similar(track_id, db, n=20)` at every beam-search step to retrieve
  candidate next tracks. When `end_bpm != start_bpm` it doubles the candidate
  pool to `n=40` so higher-BPM tracks have a chance to surface inside the same
  BPM gate. (See section 12 for the rationale.)
- **`≈ Similar` button** in the web app (`docs/index.html:5282`,
  `_toggleSimilarPanel`) renders the **top 5** similar tracks in a panel under
  each track card. The panel is populated on first open and cached afterwards.

The similarity engine is purely **read-only** — it never writes to the
Rekordbox database. It uses a single in-process index that is built lazily
on the first call and **pre-warmed in a background thread** at server
startup (`autocue/serve/deps.py:46–78`).

### What it is not

- **Not a transition scorer.** Similarity is a static, symmetric, key+energy
  proximity score. For mixing fitness, prefer
  [`score_transition()`](./transition-scoring.md) — that takes intro/outro
  energy, key compatibility (Camelot wheel), and a directional BPM step into
  account.
- **Not a recommender system.** There is no collaborative-filtering signal,
  no listen-history weighting, and no user-side personalization beyond the
  current track's own features. The index is a deterministic function of
  the library's metadata + ANLZ.
- **Not a clustering algorithm.** No K-means, no DBSCAN. Just cosine
  similarity on a fixed-length vector with a BPM gate.

---

## 2. Feature vector — six dimensions

Every track in the index is represented by a six-element list of floats. The
vector is **L2-normalized** at construction (`_build_vector`,
`autocue/analysis/similar.py:63–82`):

| Index | Name             | Source                              | Range       |
|------:|------------------|-------------------------------------|-------------|
| 0     | `cos(key_angle)` | Camelot key → angle on the wheel    | [-1, 1]     |
| 1     | `sin(key_angle)` | Camelot key → angle on the wheel    | [-1, 1]     |
| 2     | `energy_mean`    | PWAV curve average (0.0 if no ANLZ) | [0, 1]      |
| 3     | `energy_var×10`  | PWAV variance, scaled and capped    | [0, 1]      |
| 4     | `vocal_proxy`    | Has Verse phrases? 0 or 1           | {0.0, 1.0}  |
| 5     | `bpm / 200`      | BPM as a normalized scalar          | [0, 1]      |

The raw build:

```python
# autocue/analysis/similar.py:70-78
v = [
    math.cos(angle),
    math.sin(angle),
    float(energy_mean),
    min(energy_variance * 10.0, 1.0),
    1.0 if vocal_proxy else 0.0,
    min(float(bpm) / 200.0, 1.0),
]
mag = math.sqrt(sum(x * x for x in v))
if mag < 1e-9:
    return [0.0] * len(v)
return [x / mag for x in v]
```

### 2.1 Camelot key → angle (dims 0 + 1)

Camelot notation maps 24 musical keys around a circle: 1A through 12A on the
"minor" ring, 1B through 12B on the "major" ring. AutoCue encodes the key as
a **(cos, sin)** pair on the unit circle so that adjacent keys are close in
feature space and **wrap-around is free** (12A is next to 1A geometrically).

```python
# autocue/analysis/similar.py:39-56
def _camelot_angle(key_str: str) -> float:
    if not key_str:
        return 0.0
    m = _CAMELOT_RE.match(key_str.strip())
    if not m:
        return 0.0
    number = int(m.group(1))             # 1–12
    letter = m.group(2).upper()
    base_angle = 2 * math.pi * (number - 1) / 12.0
    ring_offset = math.pi / 12.0 if letter == 'B' else 0.0
    return base_angle + ring_offset
```

Two design notes:

- **B-ring offset of +π/12 (15°).** A-ring and B-ring keys at the same number
  position (e.g. 8A vs 8B) are *not* equal in real-world mixing terms — they
  are relative major/minor, harmonically compatible but distinct. Encoding
  them with a small geometric offset preserves distinction without spending
  an extra dimension.
- **Missing / unparseable → angle 0.0.** Tracks without a parsed key collapse
  to the same angle. This is the original Bug 1 trigger (see section 15).

### 2.2 energy_mean (dim 2)

The average of the track's normalized PWAV energy curve (typically 50 points).
Computed by `get_energy_curve()` in `autocue/analysis/energy.py`. Tracks with
no ANLZ data — or whose PWAV cannot be read — get **`energy_mean = 0.0`**
(not 0.5; the latter was the original Bug 1 trigger).

### 2.3 energy variance × 10 (dim 3)

Population variance of the same curve, multiplied by 10 and capped at 1.0.
The ×10 factor lifts realistic variances (typically 0.005 – 0.08) into a
range where the dimension actually contributes to cosine similarity.

If `get_mixability(content, db)` returns a non-`None` value, its
`energy_variance` field overrides the locally computed one — keeping the
mixability score, transition scorer, and similarity engine in agreement on
the variance signal.

### 2.4 vocal_proxy (dim 4)

Boolean coerced to 0.0 or 1.0. Read from
`get_mixability(...)["vocal_proxy"]`, which is `True` when any PSSI phrase
on the track is labelled `verse`. This is a coarse "does it have vocals?"
heuristic — Rekordbox does not tag vocals directly.

### 2.5 BPM / 200 (dim 5) — the post-Bug 1 dimension

The BPM is encoded as a 6th dimension so that no-ANLZ tracks at different
BPMs end up at different points on the unit hypersphere after normalization.
Without this dimension, every no-key no-energy track collapsed to the same
unit vector (see section 15).

The divisor `200` is chosen so that virtually all DJ-relevant tempos
(80 – 200 BPM) sit in [0.4, 1.0]; anything above 200 is clipped to 1.0.

---

## 3. L2 normalization + cosine similarity

After the raw 6-element list is built, every vector is **L2-normalized**:

```
v_normalized = v / ‖v‖₂
```

This places every track on the unit hypersphere. The similarity between two
tracks is then a **plain dot product**:

```python
# autocue/analysis/similar.py:85-86
def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
```

Because both inputs are unit vectors, the dot product equals cosine
similarity in `[-1, 1]`. AutoCue clips this to `[0, 1]` to map cleanly onto a
"percent similar" UI badge:

```python
# autocue/analysis/similar.py:217-218
score = _dot(target_vec, vec)
score = max(0.0, min(1.0, score))
```

### Why cosine and not Euclidean?

- **Scale-invariance.** Two tracks with the same key + same energy shape
  should score 1.0 regardless of how the raw energy curve was scaled.
  Normalization gives that for free.
- **Wrap-around safety.** The (cos, sin) key dims work correctly under
  cosine because the angle distance maps directly to dot product magnitude.

### Residual: identical-vector collisions

After normalization, **two tracks with identical raw features have identical
unit vectors and therefore cosine = 1.0**. This is the structural limitation
the data-quality cap (section 5) was added to mitigate — see also section 16.

---

## 4. BPM gate

Before the dot product is even computed, the candidate is filtered by BPM
distance:

```python
# autocue/analysis/similar.py:210-216
for tid, (bpm, vec, has_e) in _INDEX.items():
    if tid == track_id:
        continue
    bpm_diff = abs(bpm - target_bpm)
    if bpm_diff > bpm_gate:
        continue
```

- **Default `bpm_gate = 8.0` BPM.** Matches the "moderate" jump threshold in
  the DJ literature; a 8 BPM gap is at the upper end of what can be
  beat-mixed without dramatic pitch-shifting.
- **Caller override.** Set Builder uses a wider, asymmetric gate (12+ BPM)
  when end_bpm != start_bpm. See section 12.

The gate is **symmetric** at the `similar.py` layer. Asymmetric / directional
gating is the Set Builder's responsibility — `similar.py` deliberately knows
nothing about set direction.

---

## 5. Data-quality cap

Two tracks with no ANLZ data and the same BPM end up with identical feature
vectors (key = 0, energy_mean = 0, variance = 0, vocal_proxy = 0,
bpm/200 = same). After L2 normalization they are the *same point* on the
unit sphere, and their cosine similarity is exactly 1.0. This is meaningless —
the engine knows nothing about either track. The cap exists to keep the UI
honest:

```python
# autocue/analysis/similar.py:219-223
# Data-quality cap: prevents same-BPM no-data tracks from all scoring 100%
if not target_has_e and not has_e:
    score = min(score, 0.65)
elif not target_has_e or not has_e:
    score = min(score, 0.82)
```

| Situation                            | Cap   | Intent                                          |
|--------------------------------------|-------|-------------------------------------------------|
| Neither track has ANLZ energy data   | 0.65  | "We don't really know — partial match at best." |
| One track has ANLZ data, one doesn't | 0.82  | "Asymmetric confidence — likely but not sure."  |
| Both have ANLZ data                  | none  | Trust the score in full.                        |

The cap is applied *before* the BPM distance penalty (section 6), so a same-
BPM no-energy pair lands at exactly 0.65, and a 4-BPM-apart no-energy pair
ends up around `0.65 × (1 - 0.20) = 0.52`.

The `has_e` flag is stored in the index (`_INDEX[track_id] = (bpm, vec, has_e)`)
specifically so this check is O(1) at query time without re-reading ANLZ.

---

## 6. 15 % maximum BPM penalty

After the cap, similarity is scaled down linearly by BPM distance:

```python
# autocue/analysis/similar.py:224-226
bpm_penalty = min(bpm_diff / 20.0, 0.15)
score = round(score * (1.0 - bpm_penalty), 3)
```

- The penalty grows at `bpm_diff / 20` (5% per BPM).
- It saturates at **15 %** once `bpm_diff ≥ 3` — beyond that, the BPM gate
  (max 8 BPM by default) does the rest of the work.
- The cap exists so Set Builder isn't double-penalized: it already applies
  its own BPM-direction scoring in `_score_candidate()` and an explicit
  BPM-progress bonus when `end_bpm != start_bpm` (see SCORING_BUGS.md, Bug 4).

---

## 7. In-process index

The index lives in three module-level globals in `autocue/analysis/similar.py`:

```python
# autocue/analysis/similar.py:93-96
# track_id → (bpm, vector, has_energy)
_INDEX: dict[int, tuple[float, list[float], bool]] = {}
_INDEX_BUILT = False
_INDEX_LOCK = threading.Lock()
```

### 7.1 Build semantics

`_build_index(db)` (`autocue/analysis/similar.py:99–125`) walks every row in
`db.get_content().all()` and calls `_index_track()` for each one. Per-track
exceptions are caught and counted; the first three are logged so a broken
ANLZ doesn't blow up library indexing.

The lock pattern is **acquire-non-blocking, then fall through**:

```python
# autocue/analysis/similar.py:101-103
if not _INDEX_LOCK.acquire(blocking=False):
    with _INDEX_LOCK:  # wait for the in-progress build to finish, then return
        return
```

This guarantees only **one builder runs at a time**. Concurrent callers
(e.g. a `find_similar()` call racing with `deps._prewarm_index`) block on
the lock until the build finishes, then return without redoing the work.

### 7.2 Pre-warm on server startup

`autocue/serve/deps.py:77-79` kicks off a daemon thread to build the index
as soon as the FastAPI app starts:

```python
# autocue/serve/deps.py:77-79
threading.Thread(
    target=_prewarm_index, args=(app.state.ro_db or app.state.db,), daemon=True, name="index-prewarm"
).start()
```

The pre-warmer prefers the **read-only handle** (`app.state.ro_db`) so it
does not conflict with write operations on the main `app.state.db`. It is
strictly best-effort — failures are logged and swallowed so the server
still serves.

### 7.3 First-call build

If a `find_similar()` arrives before the pre-warm finishes, it triggers the
build itself:

```python
# autocue/analysis/similar.py:191-193
global _INDEX_BUILT
if not _INDEX_BUILT:
    _build_index(db)
```

The first call blocks until the index is ready. The UI shows a friendly
fallback message when this happens:

```
The similarity index is still warming up — please wait a few seconds and try again.
```

(`docs/index.html:6420`)

### 7.4 On-demand single-track indexing

If a `find_similar(track_id, ...)` arrives for a track that **isn't** in
the index (e.g. it was added after the pre-warm finished), `similar.py`
will index it on the fly using `db.get_content(ID=track_id)`:

```python
# autocue/analysis/similar.py:195-206
target = _INDEX.get(track_id)
if target is None:
    try:
        content = db.get_content(ID=track_id)
        if content is not None:
            _index_track(content, db)
    except Exception:
        pass
    target = _INDEX.get(track_id)
    if target is None:
        return []
```

This won't help the *candidate* side though — only tracks already in
`_INDEX` can be returned. After a library import the right move is a
`force_rebuild=True` on the next request.

---

## 8. Module index access pattern (critical for setbuilder)

Other modules sometimes need to check whether the similarity index has been
built — `setbuilder.py:122` does this to decide whether to call
`_build_index(db)` explicitly:

```python
# autocue/analysis/setbuilder.py:122-124
if not _similar_mod._INDEX_BUILT:
    _log.info("setbuilder: building similarity index…")
    _build_index(db)
```

**This must be done through a module reference, not a direct import:**

```python
# autocue/analysis/setbuilder.py:17-18
from . import similar as _similar_mod
from .similar import find_similar, _build_index
```

### Why the indirection matters

In Python, `from .similar import _INDEX_BUILT` copies the **current value
of the name** at the time of import (`False`) into the importing module's
namespace. When `similar.py` later flips its own `_INDEX_BUILT = True`,
the imported copy in `setbuilder.py` would still be `False`. Result: every
Set Builder call would re-build the index from scratch.

By contrast, `from . import similar as _similar_mod` binds the **module
object** itself. Attribute access `_similar_mod._INDEX_BUILT` then reads
the *current* value of the attribute every time — exactly what we want.

This is documented in CLAUDE.md (the project's Claude Code guide) so
future contributors don't accidentally introduce a stale copy:

> **similar._INDEX / _INDEX_BUILT**: The similarity index is module-level
> in `similar.py`, guarded by `_INDEX_LOCK`. To check from another module
> (e.g. `setbuilder.py`) whether the index is built, import the module
> (`from . import similar as _similar_mod`) and check
> `_similar_mod._INDEX_BUILT` — do NOT import `_INDEX_BUILT` directly
> (that creates a copy that never updates).

`find_similar` and `_build_index` are also imported directly for
ergonomics; that's safe because they are function objects, not mutable
state.

---

## 9. `clear_index()`

A simple reset:

```python
# autocue/analysis/similar.py:169-173
def clear_index() -> None:
    global _INDEX, _INDEX_BUILT
    with _INDEX_LOCK:
        _INDEX = {}
        _INDEX_BUILT = False
```

It is called from three places:

1. **`tests/conftest.py`** (autouse fixture) — before every test, so the
   similarity state never leaks between tests. The same fixture clears
   `energy._cache`, `classify._class_cache`, and `score._mixability_cache`.
2. **`/api/restore`** (`autocue/serve/routes.py:605-609`) — after restoring
   a DB backup. The restored database may have different tracks/cues/keys,
   so the old vectors are stale by definition. Restore also clears the
   energy, classification, and mixability caches.
3. **`/api/tracks/{id}/similar?force_rebuild=true`** — manual reset from the
   UI (e.g. after a re-analysis pass in Rekordbox while the server was
   running). Route: `autocue/serve/routes.py:1317-1318`.

---

## 10. `_index_track()` — warming side effects

```python
# autocue/analysis/similar.py:128-166
def _index_track(content, db) -> None:
    from .classify import get_classification

    raw_bpm = getattr(content, "BPM", 0) or 0
    bpm = float(raw_bpm) / 100.0
    # ... key, energy_curve, mixability ...
    vector = _build_vector(key_str, energy_mean, energy_variance, vocal_proxy, bpm)
    _INDEX[int(content.ID)] = (bpm, vector, bool(curve))

    # Pre-populate the classification cache so setbuilder beam search is O(1)
    try:
        get_classification(content, db)
    except Exception:
        pass
```

Two pieces worth flagging:

- **BPM is read raw and divided by 100.** `DjmdContent.BPM` is stored as
  an integer scaled by 100 (e.g. `BPM = 12500` for 125.00 BPM). Always
  divide by 100 when reading it.
- **The `_class_cache` warm-up is intentional.** Set Builder calls
  `get_classification()` for every candidate at every beam step. By warming
  the cache during the *same* DB pass that built the index, beam-search
  lookup becomes O(1) instead of a fresh ANLZ + energy + variance compute.
  The exception is swallowed because failing to classify is non-fatal —
  the vector is still useful.

---

## 11. `find_similar()` — full signature

```python
# autocue/analysis/similar.py:180-233
def find_similar(
    track_id: int,
    db,
    n: int = 10,
    bpm_gate: float = 8.0,
) -> list[dict]:
```

(Caller note: Set Builder passes `n=20` / `n=40`. The REST endpoint defaults
to `n=10` but accepts `1..100`.)

| Parameter      | Type   | Default | Meaning                                              |
|----------------|--------|---------|------------------------------------------------------|
| `track_id`     | int    | —       | Target track's `DjmdContent.ID`                      |
| `db`           | Database | —     | `Rekordbox6Database` instance (read-only is enough)  |
| `n`            | int    | `10`    | Max number of results to return                      |
| `bpm_gate`     | float  | `8.0`   | Maximum allowed BPM distance, in absolute BPM        |

### Return shape

A list (length ≤ `n`) of dicts, sorted by descending score:

```python
[
    {"track_id": 1234, "score": 0.87, "bpm_diff": 0.0},
    {"track_id": 5678, "score": 0.84, "bpm_diff": 2.5},
    ...
]
```

- **`score`** — final similarity in `[0, 1]`, after all caps and penalties.
  3-decimal precision.
- **`bpm_diff`** — absolute BPM distance from the target track.
- **Empty list** — returned for unknown target IDs, empty libraries, or
  libraries where no candidate passes the BPM gate.

### What it does *not* return

- No genre, key, title — those are looked up by the caller from
  `db.get_content(ID=…)` when needed. The result is intentionally compact
  because Set Builder iterates it tens of thousands of times.
- No `relaxed` flag (that's a Set Builder concept).
- **No `force_rebuild` parameter at this layer.** Rebuilds are triggered by
  callers via `clear_index()` first; the function then notices
  `_INDEX_BUILT = False` and rebuilds.

---

## 12. Set Builder integration

`autocue/analysis/setbuilder.py` is the heaviest caller. Two specifics
matter:

```python
# autocue/analysis/setbuilder.py:459-462
# Fetch more candidates when building BPM — same-BPM tracks fill slots quickly
n = _CANDIDATES_PER_STEP * 2 if end_bpm != start_bpm else _CANDIDATES_PER_STEP

similar = find_similar(track_id, db, n=n, bpm_gate=bpm_gate)
```

- **`n` doubles when end_bpm ≠ start_bpm.** Without this, the beam search's
  top-20 list at, say, 107 BPM was completely consumed by other 107 BPM
  same-key candidates (which scored 0.65 each thanks to the data-quality
  cap), leaving zero slots for higher-BPM candidates that were inside the
  asymmetric gate. With `n=40`, the higher-BPM candidates show up too,
  and the BPM-progress bonus rewards them.
- **The BPM gate is computed by Set Builder, not by similar.** Set Builder
  passes an asymmetric `bpm_gate` (`autocue/analysis/setbuilder.py:443-457`):
  wider in the direction of `end_bpm`, with a 12 BPM minimum when building
  or dropping. `similar.py` itself only enforces the gate symmetrically as
  given.

---

## 13. REST endpoint

```
GET /api/tracks/{track_id}/similar
    ?n=10                # 1..100, default 10
    &bpm_gate=8.0        # 0.0..50.0, default 8.0
    &force_rebuild=false # default false
```

Route: `autocue/serve/routes.py:1304-1323`.

```python
@router.get("/tracks/{track_id}/similar", response_model=SimilarTracksResponse)
def track_similar(
    track_id: int,
    n: int = Query(10, ge=1, le=100),
    bpm_gate: float = Query(8.0, ge=0.0, le=50.0),
    force_rebuild: bool = False,
    db=Depends(get_ro_db),
):
    from ..analysis.similar import clear_index, find_similar
    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Track not found")
    if force_rebuild:
        clear_index()
    results = find_similar(track_id, db, n=n, bpm_gate=bpm_gate)
    return SimilarTracksResponse(
        track_id=track_id,
        results=[SimilarTrackItem(**r) for r in results],
    )
```

The route uses `get_ro_db` — the read-only DB handle — so a long similarity
build cannot collide with a write request on the main handle.

### Response schema

`autocue/serve/schemas.py:186-194`:

```python
class SimilarTrackItem(BaseModel):
    track_id: int
    score: float          # cosine similarity 0.0–1.0
    bpm_diff: float       # |target_bpm - candidate_bpm|

class SimilarTracksResponse(BaseModel):
    track_id: int
    results: list[SimilarTrackItem]
```

### Status codes

- `200 OK` — index ready, results returned (may be empty).
- `404 Not Found` — the track ID does not exist in the library.
- `503` is *not* returned for a still-warming index; the request will block
  on the build lock and resolve normally. The UI translates a long delay
  + empty response into a "still warming up" toast.

---

## 14. UI surface — the `≈ Similar` button

Every track card in the web app gets a small `≈ Similar` button on the right
edge of its header (`docs/index.html:5830-5845`):

```js
simBtn.className = 'similar-btn';
simBtn.textContent = '≈ Similar';
...
simPanel.className = 'similar-panel';
...
simBtn.addEventListener('click', () => _toggleSimilarPanel(simBtn, simPanel, track.id));
```

Clicking it slides open a panel directly underneath, populated on first
open via `_toggleSimilarPanel` (`docs/index.html:5282-5339`):

```js
const r = await fetch(`/api/tracks/${trackId}/similar?n=5`);
```

The top 5 results are rendered as rows like:

```
87%  ±0     Artist — Song Title
84%  ±2.5   Other Artist — Other Title
```

Behaviour notes:

- **Cached after first open.** `panel.dataset.loaded = '1'` flips after the
  fetch resolves; subsequent clicks just slide-toggle visibility, no refetch.
- **Title+artist deduplication.** Same track present twice in the library
  (e.g. two copies of an album rip) is collapsed in the UI based on
  lowercase `artist|||title`. Keeps the small panel useful.
- **Footnote.** A one-line hint reminds the user that this scores key +
  energy + BPM proximity only — harmonic mixing scoring lives in the
  **Transition** panel, not here.
- **No similar in XML mode.** This feature requires the local server (it
  needs `_INDEX` and ANLZ access). The button is hidden when the app is
  loaded from `file://` without a backing server.

---

## 15. Bug 1 — degenerate cluster history

A long-form post-mortem lives in
[`SCORING_BUGS.md`](../../SCORING_BUGS.md) (Bug 1). The short version:

### Root cause

```
no-key track → _camelot_angle("") → 0.0
              → cos(0)=1.0, sin(0)=0.0
no-ANLZ track → energy_mean defaulted to 0.5
no PSSI → vocal_proxy=0, variance=0
```

The raw vector for *any* track with no key, no ANLZ, no PSSI, and BPM X
was identical:

```
[1.0, 0.0, 0.5, 0.0, 0.0]
```

(The old build had no BPM dimension.) After L2 normalization, every such
track sat at the same point on the unit sphere. Cosine similarity = 1.0 for
all of them. The Similar panel showed "100% match" on a wall of strangers.
The Set Builder filled every same-BPM slot with these phantom matches.

### Fix (all in `autocue/analysis/similar.py`)

1. **6th dimension: BPM.** `bpm / 200.0` so tracks at different tempos no
   longer collide after normalization.
2. **`energy_mean` default 0.5 → 0.0.** A no-data track is at the *origin*
   on this dimension, not in the middle. Removes one source of phantom
   identity.
3. **`has_energy` flag in the index.** Stored alongside the vector and BPM
   so the cap check is O(1).
4. **Data-quality cap.** `score ≤ 0.65` when **neither** track has ANLZ
   energy data; `score ≤ 0.82` when exactly one side has it.
5. **15 % maximum BPM penalty.** Bounds the BPM penalty so Set Builder
   isn't double-punished for moving BPM forward.

After these fixes, the Similar panel for a no-data track shows scores up
to 0.65 — honest, and clearly separable from the 0.9+ that real-data
matches produce.

---

## 16. Residual limitation

The 6-dim BPM addition cannot break a **same-BPM cluster**. After
normalization, two tracks with:

- identical key (or both missing)
- identical (or both missing) energy
- same vocal proxy
- **identical BPM**

still produce identical unit vectors. Cosine = 1.0, then capped to 0.65 if
both are no-data. **All members of a same-BPM same-key cluster score
identically** in the Similar panel.

For the Similar panel this is acceptable — the user just sees a row of
tracks all marked "65%". For Set Builder, this would freeze the beam at a
single BPM. The work-around lives in
[`setbuilder.py`](../../autocue/analysis/setbuilder.py):

- **BPM-progress bonus** — up to +15 pts for a candidate that moves toward
  `end_bpm`.
- **Setbuilder-specific transition reweighting** — when end_bpm ≠ start_bpm,
  the BPM component's weight drops from 0.40 to 0.25.
- **Doubled candidate retrieval** — `n=40` to escape the same-BPM cluster
  by surface area.

Together these let the beam escape clusters even when individual similarity
scores cannot rank them.

---

## 17. Performance

| Library size | `_INDEX` RAM | Pre-warm wall-time      |
|--------------|--------------|-------------------------|
| 3 000 tracks | ~0.2 MB      | ~1–3 s                  |
| 10 000 tracks| ~0.6 MB      | ~3–10 s                 |
| 50 000 tracks| ~3 MB        | ~15–60 s                |

- **Storage per track:** 6 floats × 8 B = 48 B vector, plus `(bpm, has_e)`
  tuple = ~64 B + Python overhead → ~200 B per track in practice.
- **Lookup is O(n)** per query: every call walks every track in the index
  applying the BPM gate, computing a 6-term dot product, applying caps and
  penalties, then sorts. At 3 000 tracks this is sub-millisecond.
- **First call blocks** if the pre-warm hasn't finished. After the pre-warm
  daemon completes, every subsequent query is in the µs–ms range.
- **No GIL release** during query — the entire loop runs in Python. For
  50k+ libraries, consider batching `find_similar` calls inside a single
  request rather than rebuilding context per call.

---

## 18. Worked example

Two tracks, both fully analyzed:

```
Track A:  key=8A, BPM=120, energy_mean=0.55, energy_var=0.012, vocal=True
Track B:  key=8A, BPM=122, energy_mean=0.52, energy_var=0.014, vocal=True
```

### Raw vectors (pre-normalization)

```
A_raw = [cos(7π/6), sin(7π/6), 0.55, 0.12, 1.0, 0.60]
      = [-0.866, -0.500, 0.55, 0.12, 1.0, 0.60]

B_raw = [cos(7π/6), sin(7π/6), 0.52, 0.14, 1.0, 0.61]
      = [-0.866, -0.500, 0.52, 0.14, 1.0, 0.61]
```

(8A → number 8 → base_angle = 2π × 7 / 12 = 7π/6; A-ring → no offset.)

### Magnitudes

```
‖A‖ ≈ √(0.75 + 0.25 + 0.3025 + 0.0144 + 1.0 + 0.36) ≈ √2.6769 ≈ 1.636
‖B‖ ≈ √(0.75 + 0.25 + 0.2704 + 0.0196 + 1.0 + 0.3721) ≈ √2.6621 ≈ 1.632
```

### Normalized

```
A ≈ [-0.529, -0.306, 0.336, 0.073, 0.611, 0.367]
B ≈ [-0.531, -0.306, 0.319, 0.086, 0.613, 0.374]
```

### Dot product

```
A · B ≈ 0.281 + 0.094 + 0.107 + 0.006 + 0.375 + 0.137 ≈ 0.999
```

### Caps / penalties

- Both have ANLZ → no data-quality cap.
- `bpm_diff = 2.0` → penalty = `min(2.0/20.0, 0.15) = 0.10`.
- Final: `0.999 × (1 − 0.10) = 0.899` → rounded to **0.899**.

The UI displays it as **90 %**.

---

## 19. Testing

`tests/test_similar.py` — 28 tests. Highlights:

- `TestCamelotAngle` — A/B ring offset, wrap-around, lowercase, empty/invalid
  input.
- `TestBuildVector` — output length is exactly 6, unit-magnitude post
  normalization, vocal proxy changes the vector, zero-vector safe.
- `TestFindSimilar` — empty library, single track, BPM gate
  inclusion/exclusion, identical tracks → ~1.0, opposite Camelot keys
  → lower than same-key, results sorted descending, `n` limits results,
  unknown track ID → empty list, `bpm_diff` reported, score in `[0, 1]`,
  `clear_index()` is observable.
- `TestDataQualityCap` — both-no-energy capped at 0.65, one-side-no-energy
  capped at 0.82, both-have-energy can exceed 0.82.

The `conftest.py` autouse fixture calls `similar.clear_index()` before every
test, plus clears the dependent caches (`energy._cache`,
`classify._class_cache`, `score._mixability_cache`). Without that, an early
test would warm the index and a later test would see stale state.

For unit tests that need a pre-built index without driving `_build_index()`,
patch `_INDEX` and `_INDEX_BUILT` directly:

```python
with patch(f"{MODULE}._INDEX", custom_index), \
     patch(f"{MODULE}._INDEX_BUILT", True):
    results = find_similar(1, MagicMock(), n=5)
```

This is the pattern used by `TestDataQualityCap`.

---

## 20. Related references

- [`docs/reference/energy-and-mixability.md`](./energy-and-mixability.md) —
  source of `energy_mean`, `energy_variance`, and the `vocal_proxy` flag.
- [`docs/reference/track-classification.md`](./track-classification.md) —
  also consumes `energy_mean`; `_class_cache` is warmed by `_index_track()`.
- [`docs/reference/set-builder.md`](./set-builder.md) — the primary
  consumer; documents the BPM-progress bonus and asymmetric BPM gating
  that work around the residual cluster limitation.
- [`docs/reference/transition-scoring.md`](./transition-scoring.md) —
  the harmonic / energy fitness scorer used for actual mixing decisions.
  Similar scoring is **not** a substitute.
- [`SCORING_BUGS.md`](../../SCORING_BUGS.md) — the four-bug post-mortem
  Bug 1 fixed in this module.

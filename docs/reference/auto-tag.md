# Auto-Tag — Rekordbox My Tags

AutoCue's **Auto-Tag** feature translates the same DJ-oriented analysis used by
the Set Builder, the Comment Enricher, and the Library Health panel into
**Rekordbox "My Tags"**, the colored labels Rekordbox supports as a
faceted filter in the browser sidebar. The result is a library that you can
sort and filter inside Rekordbox itself — "show me every Peak track tagged
Vocal between 125–128 BPM" becomes a click rather than a memory game.

This document covers the full feature: the database tables that hold the
tags, every detector that produces them, the REST endpoints that expose them,
the Discogs auto-tag flow that piggy-backs on the same machinery, the safety
rails (Rekordbox-must-be-closed guard, backups, undo), and how it is exercised
by the test suite.

Source modules:

- `autocue/analysis/auto_tag.py` — all detectors, the `apply_tags()` /
  `apply_classification_tags()` entrypoints, idempotent
  `ensure_tag_by_name()` / `ensure_category_tags()` helpers, the
  `undo_tag_run()` reversal helper, and the two name allowlists
  (`AUTOCUE_TAG_NAMES`, `ALL_AUTOCUE_TAG_NAMES`).
- `autocue/serve/routes.py:1554-1729` — the `POST /api/auto-tag`,
  `POST /api/auto-tag/undo`, `POST /api/auto-tag/discogs` (SSE),
  `POST /api/auto-tag/discogs/test`, and `GET /api/config` endpoints.
- `autocue/serve/schemas.py:376-423` — request/response Pydantic models.
- `tests/test_auto_tag.py` — 36 tests covering create/reuse, every detector,
  undo, and dry-run behaviour.

---

## 1. Overview

### What are Rekordbox "My Tags"?

Rekordbox 6/7 supports user-defined labels called **My Tags**. Each tag has a
name (`Vocal`, `Peak`, `Long Intro`, …), a color hint, and an ordering
sequence number. A track may carry any number of My Tags, and the Rekordbox
browser can filter the library by one or more of them.

My Tags live in two SQLCipher-encrypted tables in `master.db`:

- `DjmdMyTag` — one row per **tag definition** (name + color + sort order).
- `DjmdSongMyTag` — one row per **(track, tag) assignment** (the join table).

### What AutoCue writes

AutoCue ships eight **tag groups**, each containing 2–7 mutually-exclusive
labels. Every group has its own detector that reads the analysis layer
(ANLZ-derived energy curve, phrase grid, classification scores, BPM,
metadata, play count) and decides which label to apply. The eight groups are:

| Group | Detector reads | Possible tags |
|-------|----------------|---------------|
| `category` | `classify.get_classification()` | Warmup, Build, Peak, After Hours, Closing |
| `vocal` | `score.get_mixability().vocal_proxy` | Vocal, Instrumental |
| `energy_level` | `energy.get_energy_curve()` mean | High Energy, Mid Energy, Low Energy |
| `energy_profile` | `energy.classify_energy_profile()` | Build Track, Wave Track, Flat Track, Drop Track |
| `intro_outro` | `score.get_mixability().intro_bars` / `outro_bars` | Long Intro, Short Intro, Long Outro, Short Outro |
| `decade` | `content.ReleaseYear` | 60s, 70s, 80s, 90s, 00s, 10s, 20s |
| `bpm_tier` | `content.BPM` | <120 BPM, 120–124 BPM, 125–128 BPM, 129–135 BPM, 136–144 BPM, >144 BPM |
| `play_history` | `content.DJPlayCount` | Never Played, Rarely Played, Frequently Played |

### Why it's useful

- **Filter the library in Rekordbox without leaving Rekordbox.** Once tags are
  written, the entire faceted-search experience is native — no AutoCue UI
  required during a gig.
- **Compose tags.** "Peak + Vocal + 125–128 BPM + Long Intro" is the kind of
  set-prep query that takes seconds to express but is laborious by ear.
- **Survive XML round-trips.** My Tags persist in `master.db` regardless of
  any XML import/export workflow.
- **Reversible.** Every run returns `undo_data` that `POST /api/auto-tag/undo`
  can use to roll back. There is no "I just tagged 3,000 tracks by accident"
  failure mode.

A separate **Discogs** auto-tag flow (`POST /api/auto-tag/discogs`) uses the
same `DjmdMyTag` plumbing to attach Discogs **style** strings (Deep House,
Techno, Minimal, …) as tags. This is described in
[§12 Discogs auto-tag flow](#12-discogs-auto-tag-flow).

---

## 2. Database tables

Both tables are accessed exclusively through `pyrekordbox.db6.tables`. None of
the SQL is hand-written.

### `DjmdMyTag` — tag definitions

| Column | Type | Notes |
|--------|------|-------|
| `ID` | VARCHAR(255) | Primary key. No auto-generate; AutoCue calls `db.generate_unused_id(DjmdMyTag)` (see `auto_tag.py:58`, `:323`, `:354`). |
| `UUID` | VARCHAR(255) | AutoCue writes `str(uuid4())`. |
| `Name` | VARCHAR(255) | The display string shown in Rekordbox. AutoCue's name allowlists (`AUTOCUE_TAG_NAMES`, `ALL_AUTOCUE_TAG_NAMES`) match against this column. |
| `Attribute` | INTEGER | Color hint, 1–8, mirroring `DjmdColor.SortKey`: 1=Pink, 2=Red, 3=Orange, 4=Yellow, 5=Green, 6=Aqua, 7=Blue, 8=Purple (`auto_tag.py:15-17`). |
| `Seq` | INTEGER | Display order. AutoCue writes monotonically increasing values. |

### `DjmdSongMyTag` — track ↔ tag join

| Column | Type | Notes |
|--------|------|-------|
| `ID` | VARCHAR(255) | Primary key, via `db.generate_unused_id(DjmdSongMyTag)`. |
| `UUID` | VARCHAR(255) | `str(uuid4())`. |
| `MyTagID` | VARCHAR(255) | FK → `DjmdMyTag.ID`. |
| `ContentID` | VARCHAR(255) | FK → `DjmdContent.ID`. Always stringified. |
| `TrackNo` | INTEGER | Position within the tag (defaults to 0 — Rekordbox does not require unique ordering here). |

### Required fields when inserting

The pattern shared by `ensure_category_tags`, `ensure_tags`,
`ensure_tag_by_name`, `apply_classification_tags`, and `apply_tags`:

1. `new_id = str(db.generate_unused_id(<Table>))` — never trust autoincrement.
2. `UUID = str(uuid4())`.
3. For `DjmdMyTag`: set `Name`, `Attribute` (1–8 color hint), `Seq`.
4. For `DjmdSongMyTag`: set `MyTagID`, `ContentID` (stringified), and a
   `TrackNo` (defaulted to 0 in `apply_tags`).
5. `db.session.add(row)`.
6. The detector loop flushes (`db.session.flush()`) once after all tracks are
   processed; the route layer commits (`db.session.commit()`) once at the
   very end.

---

## 3. `AUTOCUE_TAG_NAMES`

```python
# auto_tag.py:27
AUTOCUE_TAG_NAMES = frozenset(v["name"] for v in _CATEGORIES.values())
# => {"Warmup", "Build", "Peak", "After Hours", "Closing"}
```

The **narrow** allowlist. It contains only the five
[category](#category-warmupbuildpeakafter-hoursclosing) tag names. Used by:

- `ensure_category_tags()` to decide whether to reuse an existing
  `DjmdMyTag` row by name (`auto_tag.py:47`).
- `apply_classification_tags()` to scope its "remove existing AutoCue tags
  before adding the new one" behaviour to only the category set
  (`auto_tag.py:97-150`).

This frozen set is what historic single-category runs needed; the broader
`ALL_AUTOCUE_TAG_NAMES` (next section) is its superset.

---

## 4. `ALL_AUTOCUE_TAG_NAMES`

```python
# auto_tag.py:280-284
ALL_AUTOCUE_TAG_NAMES = frozenset(
    cfg["name"]
    for group in _TAG_GROUPS.values()
    for cfg in group.values()
)
```

The **wide** allowlist. It contains every name AutoCue might ever write:
the five categories *plus* Vocal/Instrumental, the three energy levels, the
four energy profiles, the four intro/outro variants, the seven decades, the
six BPM tiers, and the three play-history buckets. Approximately 34 names.

Used by `POST /api/auto-tag/discogs` (`routes.py:1637`) when
`skip_existing=True` — see [§13 `skip_existing`](#13-skip_existing). The
intuition: if a track carries a My Tag whose name is **not** in
`ALL_AUTOCUE_TAG_NAMES`, that tag must have come from somewhere else
(probably a previous Discogs run), so the Discogs auto-tag flow can skip it
to avoid re-querying the Discogs API for a track it has already styled.

This is the only place AutoCue inspects "tags it does not own"; the rest of
the codebase touches tags only through name-keyed reuse via
`ensure_tag_by_name` and `ensure_category_tags`.

---

## 5. `MIN_SCORE = 0.70`

```python
# auto_tag.py:29
MIN_SCORE = 0.70
```

The classification-confidence floor. `apply_classification_tags()` and
`_detect_category()` both consult it (`auto_tag.py:128`, `:380`):

```python
top_score = clf["scores"].get(top_cat, 0.0)
if top_score < MIN_SCORE:
    skipped_low_score += 1
    continue
```

`classify.get_classification(content, db)` returns a dict shaped
`{"primary": <key>, "scores": {warmup: 0.41, build: 0.62, peak: 0.81, …}, …}`
where each score is a 0–1 trapezoidal membership weight. The category tagger
only writes a tag when the **top score is at least 0.70**, which is the
threshold that empirically separates "this is clearly a peak-time tool" from
"this could be either build or peak". Lower-confidence tracks are skipped
rather than mis-tagged.

Two additional gates fire first:

1. **No energy curve** — `get_energy_curve(content, db)` returned `None`
   (track has no PWAV in its `.DAT` ANLZ). Tracked as `skipped_no_anlz`.
2. **`get_classification()` returned `None`** — the analysis layer rejected
   the track entirely. Also counted as `skipped_no_anlz`.

The result: a tagged library only ever contains category labels you can
trust. Tracks the analysis cannot speak confidently about are *not* tagged
(and are surfaced as `skipped_no_anlz` / `skipped_low_score` counters in the
response).

---

## 6. Idempotent helpers

### `ensure_tag_by_name(db, name, attribute=1)`

Used to materialise an arbitrary tag row by name. This is the workhorse for
**Discogs styles** (where the tag names are not known up front — they come
from Discogs at runtime) and for any future dynamic-tag flow.

```python
# auto_tag.py:339-365
def ensure_tag_by_name(db, name: str, attribute: int = 1) -> str:
    """Get or create a single My Tag row by name (used for dynamic tags like Discogs styles).

    Returns the str(ID) of the My Tag row.
    attribute=1 (pink) is used as default color for Discogs style tags.
    """
    from pyrekordbox.db6.tables import DjmdMyTag

    try:
        for t in db.get_my_tag().all():
            if t.Name == name:
                return str(t.ID)
    except Exception:
        pass

    new_id = str(db.generate_unused_id(DjmdMyTag))
    tag = DjmdMyTag(
        ID=new_id,
        UUID=str(uuid4()),
        Name=name,
        Attribute=attribute,
        Seq=0,
    )
    db.session.add(tag)
    db.session.flush()
    _log.info("Created My Tag (dynamic) '%s' (ID=%s)", name, new_id)
    return new_id
```

Behaviour notes:

- **Reuse-by-name is by exact string match.** No normalization, no fuzzy
  collapse. "Deep House" and "deep house" produce two distinct rows. The
  Discogs path therefore relies on Discogs's own canonical style strings.
- **Default `Attribute=1` (Pink).** Discogs style tags all show up pink in
  Rekordbox; the colour is purely cosmetic.
- **Default `Seq=0`.** Dynamic tags sort before the curated ones.
- **`try/except Exception` around the read.** If `db.get_my_tag()` raises
  (rare, but possible on a freshly-restored DB before any tags exist), the
  function falls through to the create branch rather than failing.

### `ensure_category_tags(db) -> dict[str, str]`

The same pattern, specialized for the five category rows. Returns
`{category_key → tag_id}` (e.g. `{"peak": "789", "warmup": "456", …}`)
so `apply_classification_tags` can resolve the destination tag ID without a
second query. Reuses existing rows by name; creates only what is missing.
Implementation at `auto_tag.py:36-71`.

### Why this matters

Both helpers are the linchpin of **idempotency** ([§15](#15-idempotency)).
Without them, every run would create five fresh Peak/Warmup/etc. rows in
`DjmdMyTag`, and the Rekordbox sidebar would balloon. Reuse-by-name keeps
the schema stable across an arbitrary number of re-runs.

---

## 7. `apply_tags()` — the multi-type entrypoint

```python
# auto_tag.py:502-598
def apply_tags(
    db,
    track_ids: list[int],
    tag_types: list[str] | None = None,
    overwrite: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
```

The unified tagger. `tag_types` is a subset of:

```
"category", "vocal", "energy_level", "energy_profile",
"intro_outro", "decade", "bpm_tier", "play_history"
```

When `tag_types=None`, every detector runs. The function:

1. Filters `tag_types` against `_DETECTORS.keys()` — unknown types are
   silently dropped.
2. Calls `ensure_tags(db, active_types)` (`auto_tag.py:298-336`) to
   materialise every `DjmdMyTag` row that the active detectors might write.
   This is the same idempotent reuse-by-name pattern as
   `ensure_category_tags`, scaled to multiple groups.
3. Iterates `track_ids`. For each:
   - Loads the `DjmdContent` row via `db.get_content(ID=track_id)`. Missing
     rows are silently skipped.
   - Runs every active detector and concatenates their returned name lists
     into `names_to_write`.
   - If `names_to_write` is empty, increments `skipped_no_data` and moves on.
   - If `overwrite=True`, walks `db.get_my_tag_songs(ContentID=...)` and
     **deletes any existing assignment whose `MyTagID` is in
     `tag_name_map.values()`** — i.e. only the rows AutoCue itself would
     write. User-created tags and Discogs tags are left alone. Each deleted
     row is appended to `undo_data["removed"]` so undo can re-insert it.
   - Adds a fresh `DjmdSongMyTag` row per detected name; appends each new ID
     to `undo_data["added"]`.
4. After the loop, if any writes happened, calls `db.session.flush()`. The
   route layer (`POST /api/auto-tag`) calls `db.session.commit()` once.

Return shape:

```python
{
    "tagged": int,            # tracks that received ≥1 tag
    "skipped_no_data": int,   # detectors produced no names
    "errors": int,            # exceptions caught during the per-track loop
    "dry_run": bool,
    "undo_data": {            # None when dry_run=True
        "added":   list[str],
        "removed": list[dict],
    },
}
```

The `errors` counter is capped at three log lines (`auto_tag.py:582`) so a
runaway library does not flood the journal — the count is still accurate.

---

## 8. Detectors

Every detector takes `(content, db)` and returns a `list[str]` of tag
**names** to apply. Empty lists mean "no opinion" — the track contributes
nothing to that group. All eight detectors live in `auto_tag.py:372-483`.

### `_detect_category` — Warmup / Build / Peak / After Hours / Closing

```python
# auto_tag.py:372-382
def _detect_category(content, db) -> list[str]:
    curve = get_energy_curve(content, db)
    if not curve:
        return []
    clf = get_classification(content, db)
    if clf is None:
        return []
    top_cat = clf["primary"]
    if clf["scores"].get(top_cat, 0.0) < MIN_SCORE:
        return []
    return [_CATEGORIES[top_cat]["name"]]
```

Three gates: PWAV present → classification produces a primary → top score
≥ `MIN_SCORE` (0.70). The result is the *single* top category, mapped to a
display name via `_CATEGORIES`. See
[§5 `MIN_SCORE`](#5-min_score--070) for the rationale.

### `_detect_vocal` — Vocal / Instrumental

```python
# auto_tag.py:385-389
def _detect_vocal(content, db) -> list[str]:
    mix = get_mixability(content, db)
    if mix is None:
        return []
    return ["Vocal" if mix["vocal_proxy"] else "Instrumental"]
```

Reads `score.get_mixability(content, db)["vocal_proxy"]` — a heuristic
boolean derived from genre, mood tag, and (where available) the
high-frequency content of the energy curve. Always emits one of the two
labels when mixability is available; both labels are mutually exclusive.

### `_detect_energy_level` — High / Mid / Low

```python
# auto_tag.py:392-401
def _detect_energy_level(content, db) -> list[str]:
    curve = get_energy_curve(content, db)
    if not curve:
        return []
    mean = sum(curve) / len(curve)
    if mean >= 0.65:
        return ["High Energy"]
    if mean >= 0.35:
        return ["Mid Energy"]
    return ["Low Energy"]
```

Thresholds 0.65 / 0.35 on the **mean of the normalized 0–1 PWAV curve**.
The constants are tuned so a typical 124-BPM driving house track lands in
"High", a downtempo cut lands in "Low", and most ambient/cinematic material
falls in "Mid".

### `_detect_energy_profile` — Build / Wave / Flat / Drop Track

```python
# auto_tag.py:404-416
def _detect_energy_profile(content, db) -> list[str]:
    curve = get_energy_curve(content, db)
    if not curve:
        return []
    profile = classify_energy_profile(curve)
    name_map = {
        "build":          "Build Track",
        "wave":           "Wave Track",
        "flat":           "Flat Track",
        "drop-then-flat": "Drop Track",
    }
    name = name_map.get(profile)
    return [name] if name else []
```

Delegates to `energy.classify_energy_profile()`, which returns one of
`flat` / `build` / `wave` / `drop-then-flat`. The name map renames them for
display. Profile choices feed directly off the energy curve shape — see
the `energy-and-mixability.md` reference for the trapezoid math.

### `_detect_intro_outro` — Long/Short Intro/Outro

```python
# auto_tag.py:419-434
def _detect_intro_outro(content, db) -> list[str]:
    mix = get_mixability(content, db)
    if mix is None or mix.get("phrase_count", 0) == 0:
        return []
    tags = []
    intro = mix.get("intro_bars", 0)
    outro = mix.get("outro_bars", 0)
    if intro >= LONG_INTRO_BARS:
        tags.append("Long Intro")
    elif 0 < intro <= SHORT_INTRO_BARS:
        tags.append("Short Intro")
    if outro >= LONG_OUTRO_BARS:
        tags.append("Long Outro")
    elif 0 < outro <= SHORT_OUTRO_BARS:
        tags.append("Short Outro")
    return tags
```

Constants (`auto_tag.py:288-291`):

- `LONG_INTRO_BARS = 16`, `SHORT_INTRO_BARS = 4`
- `LONG_OUTRO_BARS = 16`, `SHORT_OUTRO_BARS = 4`

Tracks may receive **both** an intro tag and an outro tag (or neither). The
detector requires the PSSI phrase grid (`phrase_count > 0`) — without
phrases, intro/outro bars cannot be computed and the detector is silent.
Intros/outros in the open interval (4, 16) bars are intentionally untagged.

### `_detect_decade` — 60s / 70s / 80s / 90s / 00s / 10s / 20s

```python
# auto_tag.py:437-450
def _detect_decade(content, db) -> list[str]:
    year = getattr(content, "ReleaseYear", None)
    if not year:
        return []
    try:
        year = int(year)
    except (ValueError, TypeError):
        return []
    if year <= 0:
        return []
    decade_start = (year // 10) * 10
    decade_map = {1960: "60s", 1970: "70s", 1980: "80s", 1990: "90s",
                  2000: "00s", 2010: "10s", 2020: "20s"}
    return [decade_map[decade_start]] if decade_start in decade_map else []
```

Pure metadata read. `DjmdContent.ReleaseYear` is sometimes blank, sometimes
`"0"`, sometimes a stringified four-digit year — all three are gated. Years
outside 1960–2029 are silently skipped (no 30s tag, no 50s tag).

### `_detect_bpm_tier` — six BPM bands

```python
# auto_tag.py:453-468
def _detect_bpm_tier(content, db) -> list[str]:
    bpm_raw = getattr(content, "BPM", None)
    if not bpm_raw:
        return []
    try:
        bpm = float(bpm_raw) / 100.0
    except (ValueError, TypeError):
        return []
    if bpm <= 0:
        return []
    if bpm < 120: return ["<120 BPM"]
    if bpm < 125: return ["120–124 BPM"]
    if bpm < 129: return ["125–128 BPM"]
    if bpm < 136: return ["129–135 BPM"]
    if bpm < 145: return ["136–144 BPM"]
    return [">144 BPM"]
```

Note the `/ 100.0` — `DjmdContent.BPM` is stored as **BPM × 100** (an integer
column). A track at 124.50 BPM is stored as `12450`. The tier boundaries
are inclusive on the lower edge: 124.99 → 120–124, 125.00 → 125–128.

### `_detect_play_history` — Never / Rarely / Frequently Played

```python
# auto_tag.py:471-483
def _detect_play_history(content, db) -> list[str]:
    count_raw = getattr(content, "DJPlayCount", None)
    try:
        count = int(str(count_raw or 0))
    except (ValueError, TypeError):
        count = 0
    if count == 0:
        return ["Never Played"]
    if count <= 5:
        return ["Rarely Played"]
    if count >= 25:
        return ["Frequently Played"]
    return []
```

Three buckets: 0 plays → Never, 1–5 → Rarely, 25+ → Frequently. Tracks with
6–24 plays receive no play-history tag (deliberately — they are neither
buried nor over-played).

### Detector registry

```python
# auto_tag.py:486-495
_DETECTORS = {
    "category":       _detect_category,
    "vocal":          _detect_vocal,
    "energy_level":   _detect_energy_level,
    "energy_profile": _detect_energy_profile,
    "intro_outro":    _detect_intro_outro,
    "decade":         _detect_decade,
    "bpm_tier":       _detect_bpm_tier,
    "play_history":   _detect_play_history,
}
```

Adding a ninth tag group is a four-step change: define the names under
`_TAG_GROUPS`, add the detector function, register it in `_DETECTORS`, and
add a test class to `tests/test_auto_tag.py`.

---

## 9. `apply_classification_tags()` — category-only entrypoint

A focused subset of `apply_tags` that exists for backward compatibility and
for clients that only want the category facet without paying for the full
detector pipeline.

```python
# auto_tag.py:78-184
def apply_classification_tags(
    db,
    track_ids: list[int],
    overwrite: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
```

Differences from `apply_tags`:

- Hard-codes the category detector — no `tag_types` argument.
- Returns a richer skip breakdown: `skipped_no_anlz` (no PWAV / no
  classification) and `skipped_low_score` (score below `MIN_SCORE`).
- The `overwrite` semantics scope to **AutoCue category tags only**: only
  rows whose `MyTagID` is in `set(ensure_category_tags().values())` are
  deleted before insertion. Existing Vocal, Decade, etc. tags are
  untouched.

Both entrypoints share the same `undo_data` shape, so a category-only run
can be undone by the same `POST /api/auto-tag/undo` endpoint that handles
`apply_tags` runs.

---

## 10. `undo_data` and `undo_tag_run`

Every non-dry-run call to `apply_classification_tags` or `apply_tags`
returns a structured undo payload:

```python
undo_data = {
    "removed": [
        # one entry per DjmdSongMyTag row that was deleted before re-insertion
        {"ID": "...", "MyTagID": "...", "ContentID": "...",
         "TrackNo": 0, "UUID": "..."},
        ...
    ],
    "added": [
        # str(ID) of every DjmdSongMyTag row created during the run
        "1234", "1235", ...
    ],
}
```

`undo_tag_run(db, undo_data)` (`auto_tag.py:191-225`) reverses both halves:

1. **Delete every "added" row.** Look up each ID via
   `db.get_my_tag_songs(ID=...)` and delete it.
2. **Re-create every "removed" row.** Construct a fresh `DjmdSongMyTag`
   from the captured fields and add it back. The original UUID is
   preserved when possible (`item.get("UUID") or str(uuid4())`).
3. `db.session.flush()`.

Return shape: `{"removed": <count>, "restored": <count>}`. The route layer
commits.

### Important guarantees

- **Undo is forward-only.** It does not check whether the row still exists
  with the same content; missing rows are skipped with a warning.
- **Undo only touches `DjmdSongMyTag`.** The `DjmdMyTag` definition rows
  created by `ensure_tag_by_name` / `ensure_tags` / `ensure_category_tags`
  are *not* removed by undo. This is intentional — once a tag definition
  exists, repeated tagging runs reuse it, and deleting the definition
  would invalidate other (unrelated) song-tag rows that happened to use
  it. Cleaning up empty tags is a manual Rekordbox action.

The endpoint that consumes this is `POST /api/auto-tag/undo` ([§11.2](#112-post-apiauto-tagundo)).

---

## 11. REST endpoints

### 11.1 `POST /api/auto-tag`

**File**: `routes.py:1554-1574`. **Schema**: `AutoTagRequest` /
`AutoTagResponse` (`schemas.py:376-393`).

**Request body**:

```json
{
    "track_ids":   [12345, 12346, 12347],
    "tag_types":   ["category", "vocal", "energy_level"],
    "overwrite":   true,
    "dry_run":     false
}
```

- `track_ids` — list of `DjmdContent.ID` values (integer in transport,
  stringified for FK use internally).
- `tag_types` — optional; defaults to `["category"]` in the schema. Pass
  any subset of the eight registered names.
- `overwrite` — when `true`, existing AutoCue-owned `DjmdSongMyTag` rows
  for the targeted tag groups are deleted before re-insertion. When
  `false`, new rows are added on top (which may duplicate existing
  assignments).
- `dry_run` — when `true`, no writes happen; the response counters report
  what *would* have been written.

**Response**:

```json
{
    "tagged": 1742,
    "skipped_no_data": 188,
    "errors": 0,
    "dry_run": false,
    "undo_data": { "added": ["..."], "removed": [ {...}, ... ] }
}
```

**409 guard**: `routes.py:1559` checks `rekordbox_is_running()` (via
`db_writer.rekordbox_is_running()`, which probes `psutil` for the
Rekordbox process). When the app is open the SQLCipher database is
exclusively locked, so any write would fail — the route returns
`409 Conflict` with the message `"Rekordbox is running — close it before
applying tags"`. The guard is skipped when `dry_run=true`.

**Transaction**: a single `db.session.commit()` runs at the end of the
route handler (`routes.py:1570`). On any exception, `db.session.rollback()`
fires and the error is rethrown as `500`.

### 11.2 `POST /api/auto-tag/undo`

**File**: `routes.py:1716-1729`. **Schema**: `AutoTagUndoRequest` /
`AutoTagUndoResponse` (`schemas.py:396-402`).

**Request body**:

```json
{
    "undo_data": { "added": ["1234", "1235"], "removed": [...] }
}
```

Pass back the `undo_data` object you received from `POST /api/auto-tag`
or `apply_classification_tags`.

**Response**:

```json
{ "removed": 47, "restored": 12 }
```

The same 409 guard applies — undo is also a write, so Rekordbox must be
closed.

### 11.3 `POST /api/auto-tag/discogs` (SSE)

**File**: `routes.py:1619-1713`. **Schema**: `DiscogsTagRequest` /
`DiscogsTagEvent` (`schemas.py:405-423`).

**Request body**:

```json
{
    "track_ids":     [12345, 12346, ...],
    "token":         "abc...xyz",
    "dry_run":       false,
    "skip_existing": false
}
```

Streams Server-Sent Events. Each per-track event:

```json
{
    "processed": 12,
    "total":     250,
    "track_id":  12345,
    "artist":    "Floating Points",
    "title":     "Bias",
    "styles":    ["Electronic", "Deep House"],
    "tagged":    11
}
```

Skipped tracks emit `"styles": []` and increment `"skipped"` instead.
Errored tracks emit `"error": "<message>"` and increment `"errors"`.

Final event: `{"done": true, "tagged": T, "skipped": S, "errors": E}`.

The 409 guard fires on entry (`routes.py:1632`). The route uses
`StreamingResponse(event_stream(), media_type="text/event-stream")` and
commits **once per track that received tags** (`routes.py:1697`) — a
single failing Discogs lookup does not roll back the whole batch.

### 11.4 `POST /api/auto-tag/discogs/test`

**File**: `routes.py:1599-1616`. Validates a Discogs personal access
token by calling the identity endpoint
(`https://api.discogs.com/oauth/identity`). The request body is a raw
`{"token": "..."}` dict.

**Response on success**: `{"ok": true, "username": "youruser"}`.

**Response on failure**: HTTP 400 with the underlying error message.

This endpoint is what the UI's "Test token" button calls before
unlocking the **Apply tags** button in the Discogs Genre Tags panel.

### 11.5 `GET /api/config` (Discogs token surfacing)

**File**: `routes.py:1577-1596`. Reads `DISCOGS_TOKEN` from the
project-root `.env` (and then the environment, which takes precedence)
so the UI can pre-fill the Discogs panel. The token is the only field
currently returned, but the endpoint is named generically for future
extension.

---

## 12. Discogs auto-tag flow

The Discogs path differs from the curated detectors because the tag set
is **discovered at query time** rather than known up front. For each track:

1. **Skip-existing gate** (if `req.skip_existing=True`). The route
   pre-builds a `{tag_id → tag_name}` map of every `DjmdMyTag` row
   (`routes.py:1645`). For each track, walks `DjmdSongMyTag` rows and
   asks: does this track have any assignment whose tag name is **not** in
   `ALL_AUTOCUE_TAG_NAMES`? If yes, assume it has been Discogs-tagged
   already and skip.
2. **Lookup**. Read `artist = content.ArtistName`,
   `title = content.Title`, call `discogs.search_styles(artist, title, token)`.
   The function returns a `list[str]` of style strings (e.g.
   `["Electronic", "Deep House", "Minimal"]`) or `[]` if Discogs has no
   match.
3. **Materialise tag rows**. For each style:
   - Call `ensure_tag_by_name(db, style)` to reuse or create a `DjmdMyTag`
     row with `Attribute=1` (Pink — the visual convention for Discogs
     styles).
   - Query `DjmdSongMyTag` for an existing `(ContentID, MyTagID)` pair;
     if absent, add a fresh row with a newly-generated ID.
4. **Commit per track** (`routes.py:1697`). One transaction per track —
   not one per batch — so a single 503 from Discogs mid-run does not lose
   prior progress.

### Caching and rate limiting

`autocue.analysis.discogs` provides:

- An in-process **token-bucket rate limiter** at 60 requests/minute (the
  Discogs free-tier limit). Calls block when the bucket is empty.
- A per-process cache (`discogs._cache`) keyed by `(artist.lower(),
  title.lower())`. Repeated calls to the same track in the same session
  hit the cache, not the network.

### Error model

Per-track exceptions are caught at `routes.py:1702-1709`. The session is
rolled back, the error counter increments, an error SSE event is emitted,
and the loop continues. Network failures, JSON parse errors, and
SQLAlchemy IntegrityErrors all fall into the same bucket.

---

## 13. `skip_existing`

`DiscogsTagRequest.skip_existing` (default `False`,
`schemas.py:409`) is the inexpensive way to do an "incremental" Discogs
run on a library that has been partially tagged before. When `True`, the
endpoint:

1. Builds a single `{tag_id_str → tag_name}` map by querying
   `DjmdMyTag` once (`routes.py:1645`).
2. For each track, queries `DjmdSongMyTag` for that
   track's assignments.
3. Asks: is there any assignment whose tag name is **non-empty AND not
   in `ALL_AUTOCUE_TAG_NAMES`**? If yes, the track has at least one tag
   that AutoCue itself never writes — assume it must be a Discogs style
   from a prior run.
4. Skip — increment `skipped`, emit the per-track SSE event with
   `"styles": []`, and continue without calling the Discogs API.

This trades a small amount of false-negative risk (a manually-added user
tag would also cause a skip) for substantial savings on the Discogs rate
budget. Users who want to *re-fetch* styles can run with
`skip_existing=False` to overwrite.

The allowlist used by this check is `ALL_AUTOCUE_TAG_NAMES`, not
`AUTOCUE_TAG_NAMES` — this is the reason the wider frozen set exists.

---

## 14. UI surface

The Auto-Tag UI lives in `docs/index.html`'s **Library** tab.

### Auto-Tag panel

- **Tag-type checkboxes** — one per detector key, mapping 1:1 to
  `AutoTagRequest.tag_types`. The default selection is `category` (matching
  the schema default).
- **Overwrite toggle** — wires to `overwrite`.
- **Dry-run toggle** — wires to `dry_run`. The "Apply tags" CTA stays
  enabled in dry-run mode, but the response is rendered as a preview
  rather than a write confirmation.
- **Apply tags button** — fires `POST /api/auto-tag` against
  `filteredTracks()` (so the panel honours every active library filter:
  search, rating, plays, last-played, My-Tag, phrase-only).
- **Undo button** — stores `undo_data` from the last successful run and
  posts it to `POST /api/auto-tag/undo`. Disabled until a non-dry-run
  response is received.
- **Progress / result toast** — the UI surfaces the `tagged`,
  `skipped_no_data`, and `errors` counters from `AutoTagResponse`.

### Discogs Genre Tags panel

- **Token field** — pre-populated from `GET /api/config` (which reads
  `.env` / environment).
- **Test token** button — fires `POST /api/auto-tag/discogs/test`. A
  successful response enables the Apply button.
- **Skip-existing checkbox** — wires to `DiscogsTagRequest.skip_existing`.
- **Apply** — fires `POST /api/auto-tag/discogs` and consumes the SSE
  stream via the same `_consumeSSE(response, onEvent)` helper used by
  Discover and Download. Each event updates the progress bar and writes a
  one-line summary into the live log.

### 409 handling

When Rekordbox is open, both endpoints return `409` with a JSON body
`{"detail": "Rekordbox is running — close it before applying tags"}`.
The UI checks `r.ok` before reading typed properties (per the
`Fetch error handling in JS` rule in `CLAUDE.md`) and renders the
`detail` string as an error toast.

---

## 15. Idempotency

The whole feature is designed to run twice with no surprises. The two
load-bearing pieces:

1. **`ensure_tag_by_name` / `ensure_tags` / `ensure_category_tags`** all
   reuse `DjmdMyTag` rows by name. A second run finds the existing rows
   and returns their IDs; no new `DjmdMyTag` is ever created with a
   duplicate name.

2. **`overwrite=True` removes AutoCue-owned `DjmdSongMyTag` rows for the
   *active* tag groups before re-inserting.** That means the user can
   re-run with the same `tag_types` list and expect zero net change (if
   the underlying analysis is unchanged) or a clean delta (if it has).

Some edge cases:

- **Running with two non-overlapping `tag_types` lists.** Run 1 writes
  `category`, Run 2 writes `vocal`. Both sets of assignments coexist
  because `overwrite` scopes to the tags whose IDs are in *this run's*
  `tag_name_map`. The category tags from Run 1 are not deleted by Run 2.
- **Running with `overwrite=False`.** Each run appends new assignments
  even when an identical assignment already exists. The user is opting
  out of dedup and will end up with duplicate `DjmdSongMyTag` rows. Use
  with care; the UI defaults to `overwrite=True`.
- **Discogs runs.** The Discogs endpoint queries for an existing
  `(ContentID, MyTagID)` pair before insertion (`routes.py:1685-1689`)
  and skips if one is found. Repeated Discogs runs therefore do not
  duplicate assignments even though they use a different code path.

---

## 16. Safety

### Rekordbox-running guard

`POST /api/auto-tag`, `POST /api/auto-tag/undo`, and
`POST /api/auto-tag/discogs` all consult `db_writer.rekordbox_is_running()`
on entry. A `True` response yields a `409 Conflict` with a fixed message.
The guard is only bypassed for `dry_run=True` on the main auto-tag and
Discogs endpoints.

### Database location

All writes go to `master.db` at the `Rekordbox6Database._db_dir / "master.db"`
path. The path is opened once at startup via the lifespan dependency
(`autocue/serve/deps.py`) — the auto-tag routes do not open their own
connections.

### Backups

The auto-tag endpoints do **not** create their own backup file. The user
is expected to use the backup/restore controls on the Library Health
panel before a large run. The `POST /api/restore` endpoint can restore
any prior backup via `{filename}` (validated to live within the backup
dir).

A future enhancement would be to add an automatic backup before large
`apply_tags` runs (matching the behaviour of `enrich_comments_batch`).

### Commit semantics

- **`POST /api/auto-tag`**: one `db.session.commit()` at the end of the
  route handler. Any exception during the per-track loop is caught
  *inside* `apply_tags` and counted; only a `flush()` failure raises and
  triggers a route-level `rollback()`.
- **`POST /api/auto-tag/undo`**: one commit at the end.
- **`POST /api/auto-tag/discogs`**: **one commit per tagged track**
  (`routes.py:1697`). This is deliberate — the Discogs API can fail
  mid-stream, and per-track commits preserve already-tagged progress.

---

## 17. Examples

### Tagging a single peak-time techno track

Assume `track_id=12345` is a 130 BPM, full-energy, instrumental peak-time
banger from 2022 with phrases and an 8-bar intro / 32-bar outro,
classified `peak` (score 0.84), with `DJPlayCount=30`.

Request:

```json
POST /api/auto-tag
{
    "track_ids": [12345],
    "tag_types": ["category", "vocal", "energy_level",
                  "energy_profile", "intro_outro",
                  "decade", "bpm_tier", "play_history"],
    "overwrite": true,
    "dry_run":   false
}
```

Detector outputs:

| Detector | Result | Reason |
|----------|--------|--------|
| `_detect_category` | `["Peak"]` | top score 0.84 ≥ 0.70 |
| `_detect_vocal` | `["Instrumental"]` | `vocal_proxy` False |
| `_detect_energy_level` | `["High Energy"]` | curve mean ≥ 0.65 |
| `_detect_energy_profile` | `["Wave Track"]` | `classify_energy_profile` → `"wave"` |
| `_detect_intro_outro` | `["Long Outro"]` | intro=8 (no tag, 4<8<16), outro=32 ≥ 16 |
| `_detect_decade` | `["20s"]` | `ReleaseYear=2022` |
| `_detect_bpm_tier` | `["129–135 BPM"]` | 130 ≥ 129, < 136 |
| `_detect_play_history` | `["Frequently Played"]` | 30 ≥ 25 |

After the run the track carries seven new `DjmdSongMyTag` rows and the
response is:

```json
{
    "tagged": 1,
    "skipped_no_data": 0,
    "errors": 0,
    "dry_run": false,
    "undo_data": { "added": ["...", ...], "removed": [] }
}
```

In Rekordbox the track now appears under filters for **Peak**,
**Instrumental**, **High Energy**, **Wave Track**, **Long Outro**, **20s**,
**129–135 BPM**, and **Frequently Played**.

### Skipping a low-confidence track

Same request shape, but `track_id=22222` is a 122 BPM uncertain build/peak
track with category scores `{build: 0.55, peak: 0.62, warmup: 0.40, …}`.

- `_detect_category` returns `[]` (top score 0.62 < 0.70). The track is
  not tagged as Peak — better silent than wrong.
- All other detectors still run and produce their tags.

The track receives ~6 tags instead of ~7; `tagged` is still incremented.

### Undoing the run

```json
POST /api/auto-tag/undo
{ "undo_data": <whatever the previous response returned> }

→ { "removed": 7, "restored": 0 }
```

(Zero rows are "restored" because the original track carried no AutoCue
tags before the run — `removed` was empty in the original `undo_data`.)

---

## 18. Testing

`tests/test_auto_tag.py` contains **36 tests** organised into six classes
(`TestEnsureCategoryTags`, `TestApplyClassificationTags`, `TestUndoTagRun`,
`TestEnsureTags`, `TestApplyTags`, and helpers).

Coverage at a glance:

- **`ensure_category_tags`**:
  - `test_creates_all_five_when_none_exist`
  - `test_reuses_existing_tags`
  - `test_idempotent_all_existing`
  - `test_returns_string_ids` — verifies the `dict[str, str]` contract
  - `test_handles_get_my_tag_exception` — falls through to create branch

- **`apply_classification_tags`** (the legacy entrypoint):
  - High-score happy path; below-`MIN_SCORE` skip path
  - Skip when no energy curve / no classification
  - Dry-run does not write
  - Overwrite deletes existing AutoCue tags; `overwrite=False` keeps them
  - Missing content is skipped without error
  - Error counter increments
  - `flush()` is called after writes
  - Multi-track happy path
  - `undo_data.added` contains stringified IDs

- **`undo_tag_run`**: removes added tags, restores removed tags, handles
  missing rows, handles an empty undo payload, flushes at the end.

- **`ensure_tags`**: creates only the requested types, reuses existing,
  combines multiple types.

- **`apply_tags`** (the unified multi-type entrypoint):
  - Per-type happy paths: `test_vocal_tag_applied`,
    `test_instrumental_tag_applied`, `test_energy_level_high`,
    `test_energy_profile_build`, `test_intro_outro_long_intro`
  - Skip-when-no-data paths for energy and intro/outro
  - `test_multiple_tag_types_combined` — verifies that a single track
    can receive Vocal + High Energy + Long Intro from one call
  - `test_dry_run_does_not_write`
  - `test_skips_missing_content`

The autouse fixture in `conftest.py` clears `energy._cache`,
`classify._class_cache`, `score._mixability_cache`, and the similarity
index before every test, so detectors see fresh analysis state every time.

Run them with:

```bash
pytest tests/test_auto_tag.py -q
```

---

## 19. Related references

- [`track-classification.md`](./track-classification.md) — how the
  `category` detector decides on Warmup/Build/Peak/After Hours/Closing.
  Covers the trapezoidal membership weights, the BPM ranges, and the
  `MIN_SCORE` threshold from the analysis side.
- [`energy-and-mixability.md`](./energy-and-mixability.md) — the
  underlying PWAV → energy curve pipeline and the
  `classify_energy_profile()` rules consumed by `_detect_energy_level`
  and `_detect_energy_profile`.
- [`discogs-and-discovery.md`](./discogs-and-discovery.md) — the
  `search_styles()` client, its caching, the 60 req/min rate limiter,
  and the new-release discovery flow that shares the same token.
- [`comment-enrichment.md`](./comment-enrichment.md) — a parallel
  feature that writes the same analysis output to
  `DjmdContent.Commnt` in MIK-compatible format. Comments are richer
  text but harder to faceted-filter on; My Tags are coarser but
  Rekordbox-native. Use both.

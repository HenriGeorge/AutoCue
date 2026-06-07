# Playlist Suggest & Playlist Creation

Playlist Suggest answers a simple DJ question: *"give me 20 peak-time tracks from
my library."* It returns an unordered bag of category matches — not a sequenced
set — and pairs with a companion endpoint (`POST /api/playlists`) that persists a
chosen list of track IDs as a real Rekordbox playlist.

The two endpoints are independent. Playlist Suggest is a pure read; Playlist
Creation is a guarded write that round-trips through `db_writer`'s "Rekordbox
must be closed" interlock.

## Table of Contents

- [1. Overview](#1-overview)
- [2. The Suggestion Algorithm](#2-the-suggestion-algorithm)
- [3. Seeds (`seed_track_ids`)](#3-seeds-seed_track_ids)
- [4. Seed Scoring](#4-seed-scoring)
- [5. `PlaylistSuggestRequest` Schema](#5-playlistsuggestrequest-schema)
- [6. `PlaylistSuggestResponse` Schema](#6-playlistsuggestresponse-schema)
- [7. `POST /api/playlists/suggest`](#7-post-apiplaylistssuggest)
- [8. Playlist Creation — `POST /api/playlists`](#8-playlist-creation--post-apiplaylists)
- [9. UI Surface — Playlist Suggest Panel](#9-ui-surface--playlist-suggest-panel)
- [10. Weighted Random Rationale — Why `score ** 2`](#10-weighted-random-rationale--why-score--2)
- [11. Why a Pool, Not Top-N](#11-why-a-pool-not-top-n)
- [12. Examples](#12-examples)
- [13. Edge Cases](#13-edge-cases)
- [14. Playlist Suggest vs Set Builder](#14-playlist-suggest-vs-set-builder)
- [15. Testing](#15-testing)
- [16. Related](#16-related)

---

## 1. Overview

Two endpoints, one user flow:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/playlists/suggest` | `POST` | Score every in-scope track against a DJ-set category, return the top `count` after a weighted-random draw. Read-only. |
| `/api/playlists` | `POST` | Create a new top-level Rekordbox playlist ([`DjmdPlaylist`](./GLOSSARY.md#djmdplaylist--djmdsongplaylist) + [`DjmdSongPlaylist`](./GLOSSARY.md#djmdplaylist--djmdsongplaylist) rows) from a list of track IDs. Write. |

The suggestion algorithm filters by the five classification categories defined
in `autocue/analysis/classify.py:14`:

```python
CATEGORIES = ("warmup", "build", "peak", "after_hours", "closing")
```

A category score is a float in `[0.0, 1.0]` produced by
`get_classification(content, db)["scores"][category]`. Playlist Suggest sorts
by that score, weights the top of the distribution by `score**2`, and draws
without replacement.

Contrast with Set Builder (see [`set-builder.md`](./set-builder.md)): Set
Builder produces an **ordered** sequence with transition scoring, anchors, and
BPM progression; Playlist Suggest produces an **unordered** bag. Use Set Builder
when you want a play-ready set; use Playlist Suggest when you want a crate
of category-matched candidates to browse.

---

## 2. The Suggestion Algorithm

Implemented in `autocue/serve/routes.py:1159-1246`. The full pseudo-code:

```text
1. Validate category in CATEGORIES, count in [1, 500].
2. Resolve scope:
     if playlist_id is not None:
       contents = tracks belonging to DjmdPlaylist(playlist_id)
     else:
       contents = db.get_content().all()   # full library
3. For each content in scope:
     cid = int(content.ID)
     if cid in exclude_ids AND cid not in seed_track_ids:
       skip                                 # exclude bypassed by seeds
     try:
       data = get_classification(content, db)
       cat_score = data["scores"].get(category, 0.0)
       if cid in seed_track_ids:
         seed_scored[cid] = cat_score       # remembered; doesn't affect selection
       elif cat_score > 0:
         scored.append((cat_score, cid))    # only positive scores enter the pool
     except Exception:
       if cid in seed_track_ids:
         seed_scored[cid] = 0.0             # seeds survive classify failure
4. seed_items = [(seed_scored[sid] or 0.0, sid) for sid in seed_track_ids]
5. fill_count = max(0, count - len(seed_items))
6. scored.sort(reverse=True)                # by score descending
7. pool_size = min(len(scored), max(fill_count * 3, 60))
8. pool = scored[:pool_size]                # top of the distribution
9. Weighted random draw:
     weights = [s ** 2 for (s, _) in pool]
     seen = {}
     for _ in range(fill_count * 4):        # bounded retry loop
       if len(fill) >= fill_count: break
       pick = random.choices(pool, weights=weights, k=1)[0]
       if pick.track_id not in seen:
         seen.add(pick.track_id)
         fill.append(pick)
     # If the random draw under-fills, top up from the sorted pool:
     if len(fill) < fill_count:
       for item in pool:
         if len(fill) >= fill_count: break
         if item.track_id not in seen:
           fill.append(item)
10. top = seed_items + fill                 # seeds first, in user order
11. Return PlaylistSuggestResponse(category, results=[{track_id, score}])
```

### Key invariants

- **Zero-score tracks never enter the pool.** A track that scores `0.0` for the
  requested category is silently dropped at step 3. This is what guarantees
  the `test_zero_score_tracks_excluded` test passes
  (`tests/test_serve_routes.py:2112`).
- **The exclude check fires before classification.** Tracks excluded *and not
  seeded* are skipped before `get_classification` runs, avoiding wasted work.
- **`get_classification` is cached** via `classify._class_cache` keyed by
  `content.ID`. The first scan of a 3k-track library is expensive; repeat
  scans are near-instant.
- **A bounded retry loop drives the random draw.** The loop runs at most
  `fill_count * 4` times to prevent infinite spin if weights skew so heavily
  that the same handful of tracks are picked over and over. The fallback at
  step 9b guarantees the response always contains `min(fill_count, len(pool))`
  fill items.

---

## 3. Seeds (`seed_track_ids`)

Seeds are tracks the caller wants *pinned* to the front of the response, in the
order supplied.

```python
seed_track_ids: list[int] = []   # default — no seeds
```

Behaviour:

- **Seeds bypass `exclude_ids`.** If a track ID appears in both lists, the seed
  list wins. This lets the UI re-run a suggestion with previously-selected
  tracks pinned even after the user has globally excluded them in another
  panel.
- **Seeds are placed first**, in the exact order the caller supplied them
  (`autocue/serve/routes.py:1210-1212`). The weighted-random fill never
  reorders them.
- **Seeds consume the `count` budget.** If you ask for `count=20` with
  `seed_track_ids=[42, 99]`, you get the two seeds plus up to 18 weighted-random
  fill tracks.
- **Seeds survive a classification crash.** If `get_classification` raises
  for a seed track, it still appears in the response with `score=0.0`
  (`routes.py:1206-1207`). For non-seed tracks, a crash silently drops them.

### Why seeds bypass excludes

The intended flow is:

1. The user calls `/api/playlists/suggest` once.
2. They tick a few tracks they like in the UI result list.
3. They hit *"Use selected as seeds"* — this re-runs the endpoint with the
   ticked track IDs as `seed_track_ids` **and** the rest of the current
   suggestion as `exclude_ids` to avoid re-rolling the same tracks.

Without the seed-bypass, the second call would silently drop the user's picks.

---

## 4. Seed Scoring

The reported score for a seed in the response is whatever
`get_classification(content, db)["scores"][category]` returned at scan time, or
`0.0` if classification raised. The score is informational only — the UI uses
it to render a meter next to each pinned track — but it does **not** affect
seed selection. Seeds are always returned, in the order given, regardless of
their score.

This decouples *"I want this track in the result"* from *"this track is a
great peak-time candidate."* A DJ pinning a personal favourite that scores
`0.32` for peak still sees it included; the meter just shows it's a stretch.

---

## 5. `PlaylistSuggestRequest` Schema

Defined in `autocue/serve/schemas.py:321-326`.

```python
class PlaylistSuggestRequest(BaseModel):
    category: str                       # one of CATEGORIES (warmup/build/peak/after_hours/closing)
    count: int = 20                     # 1..500, validated server-side
    exclude_ids: list[int] = []         # tracks to skip (unless also in seed_track_ids)
    seed_track_ids: list[int] = []      # pre-included; bypass exclude_ids
    playlist_id: int | None = None      # scope to a Rekordbox playlist, or None = full library
```

### Field semantics

| Field | Type | Default | Notes |
|---|---|---|---|
| `category` | `str` | required | Validated against `CATEGORIES`. Invalid → `400 Unknown category 'X'. Valid: [...]`. |
| `count` | `int` | `20` | `count < 1 or count > 500` → `400 count must be between 1 and 500`. |
| `exclude_ids` | `list[int]` | `[]` | Compared against `int(content.ID)`. Coerced to a `set` once for O(1) lookup. |
| `seed_track_ids` | `list[int]` | `[]` | Compared against `int(content.ID)`. **Order is preserved** in the response. |
| `playlist_id` | `int \| None` | `None` | If set, only tracks belonging to that `DjmdPlaylist` are considered. Unknown ID → `404 Playlist N not found`. |

### `playlist_id` scope query

When `playlist_id` is set (`routes.py:1171-1185`), the endpoint:

1. Looks up the `DjmdPlaylist` row by stringified ID (Rekordbox stores IDs as
   `VARCHAR`).
2. Pulls every `DjmdSongPlaylist.ContentID` for that playlist.
3. Loads the corresponding [`DjmdContent`](./GLOSSARY.md#djmdcontent) rows via `IN(valid_ids)`.

This is one of the few places in the codebase where `IN(...)` is the right call
— the playlist membership set is small (tens to a few hundred), unlike the
`/api/tracks` endpoint which scans the full library.

---

## 6. `PlaylistSuggestResponse` Schema

Defined in `autocue/serve/schemas.py:329-336`.

```python
class PlaylistSuggestItem(BaseModel):
    track_id: int
    score: float        # category score 0.0–1.0, rounded to 3 decimals

class PlaylistSuggestResponse(BaseModel):
    category: str       # echo of the requested category
    results: list[PlaylistSuggestItem]
```

### Result ordering

The response `results` list is structured as:

```text
[seed_1, seed_2, ..., seed_N,   # user-supplied order, all seeds
 fill_1, fill_2, ..., fill_M]   # weighted-random draw, unordered
```

The fill portion is **not** sorted by score. The weighted-random draw is the
intended randomisation; sorting after the fact would defeat the variety
property described in [Why a Pool, Not Top-N](#11-why-a-pool-not-top-n).

The total length is `min(len(seed_track_ids) + len(scored_pool), count)`. If the
scope produces fewer category-matching tracks than `count`, the response is
correspondingly shorter — there is no padding.

---

## 7. `POST /api/playlists/suggest`

```http
POST /api/playlists/suggest
Content-Type: application/json

{
  "category": "peak",
  "count": 20,
  "exclude_ids": [],
  "seed_track_ids": [],
  "playlist_id": null
}
```

### Responses

| Status | Body | When |
|---|---|---|
| `200` | `PlaylistSuggestResponse` | Success. |
| `400` | `{"detail": "Unknown category 'X'. Valid: [...]"}` | `category` not in `CATEGORIES`. |
| `400` | `{"detail": "count must be between 1 and 500"}` | `count` out of range. |
| `404` | `{"detail": "Playlist N not found"}` | `playlist_id` does not match any `DjmdPlaylist`. |

### Edge cases

- **Empty library / empty playlist** — `results: []`. No error.
- **Every track excluded** — only seeds are returned (possibly empty).
- **Fewer matches than `count`** — response is truncated to the available pool.
- **All tracks have `score = 0`** — `results: []` (zero-score tracks are filtered).
- **Seed references a deleted track ID** — the `get_content` scan won't find it,
  so `seed_scored[sid]` stays absent, and the response substitutes `0.0` via
  the `seed_scored.get(sid, 0.0)` fallback at `routes.py:1211`. The seed
  *still appears* in the response.

---

## 8. Playlist Creation — `POST /api/playlists`

Persists a list of track IDs as a new top-level Rekordbox playlist. Implemented
in `autocue/serve/routes.py:1494-1549`.

### Request

```python
class CreatePlaylistRequest(BaseModel):
    name: str
    track_ids: list[int]
```

### Response

```python
class CreatePlaylistResponse(BaseModel):
    playlist_id: int
    name: str
    track_count: int
```

### Guards (in order)

1. **`name.strip()` non-empty** — else `400 Playlist name is required`.
2. **`track_ids` non-empty** — else `400 No tracks provided`.
3. **Rekordbox not running** — `db_writer.rekordbox_is_running()` (psutil) must
   return `False`. Else `409 Rekordbox is running — close it before saving
   playlists`. This is the same interlock used by every other write endpoint;
   the SQLCipher database is exclusively locked while Rekordbox is open.

### Write sequence

```python
max_seq = SELECT MAX(Seq) FROM DjmdPlaylist        # or 0
now = datetime.utcnow()

pl_id = db.generate_unused_id(DjmdPlaylist)        # explicit — no auto-PK
playlist = DjmdPlaylist(
    ID=str(pl_id),
    Seq=int(max_seq) + 1,                          # appended at the end of the root list
    Name=name.strip(),
    Attribute=0,                                   # 0 = regular playlist (not a folder)
    ParentID="root",                               # top-level
    UUID=str(uuid4()),
    created_at=now, updated_at=now,
)
db.session.add(playlist)

for track_no, tid in enumerate(track_ids, start=1):
    sp_id = db.generate_unused_id(DjmdSongPlaylist)
    db.session.add(DjmdSongPlaylist(
        ID=str(sp_id),
        PlaylistID=str(pl_id),
        ContentID=str(tid),
        TrackNo=track_no,
        UUID=str(uuid4()),
        created_at=now, updated_at=now,
    ))

db.session.commit()
```

### Field notes (consistent with the rest of the codebase)

- `DjmdPlaylist.ID` is `VARCHAR` — never int.
- `db.generate_unused_id(...)` must be called explicitly; there is no
  auto-increment default.
- `UUID` is a fresh `uuid4()` per row.
- `Attribute = 0` → regular playlist; non-zero values indicate folders or
  smart lists.
- `ParentID = "root"` places the playlist at the top level of the sidebar.
- `Seq` is the global ordering for the parent — the new playlist is appended
  to the end of root.
- `TrackNo` starts at 1 and matches the order of `track_ids` in the request.
  Caller order is preserved.

### Error path

Any exception inside the write block triggers `db.session.rollback()` and a
`500 Failed to create playlist: ...` is returned. The endpoint does not retry.

### Backup behaviour

`POST /api/playlists` does **not** make a DB backup before writing. Backups are
made by destructive endpoints (apply, color, comment enrich); playlist creation
is purely additive — failure rolls back the session and leaves the DB
unchanged. If a write succeeds but the user wants to undo it, they delete the
playlist from Rekordbox's sidebar.

---

## 9. UI Surface — Playlist Suggest Panel

The Playlist Suggest panel lives in `docs/index.html` and is only visible when
the page is served by `autocue serve` (it depends on the server-only
`/api/playlists/*` endpoints).

User-visible controls:

| Control | Behaviour |
|---|---|
| **Category dropdown** | One of `warmup`, `build`, `peak`, `after_hours`, `closing`. |
| **Count input** | Integer 1–500, default 20. Validated client-side; server re-validates. |
| **Source** | "Full library" or the currently filtered playlist (passes `playlist_id`). |
| **Suggest tracks** button | Fires `POST /api/playlists/suggest` with the current form values. |
| **Result list** | One row per `PlaylistSuggestItem`. Each row has a checkbox, the track's title/artist, and a score meter showing `item.score`. Re-rolls clear the list. |
| **Use selected as seeds** button | Re-runs the suggestion with the checked rows' track IDs as `seed_track_ids` and the rest of the current result list as `exclude_ids`. Variety + pinning, in one click. |
| **Create playlist** button | Prompts for a name, then `POST /api/playlists` with the current result list as `track_ids`. The created playlist appears in the playlist dropdown after the next `/api/playlists` refresh. |

The panel does *not* sort, transition-score, or BPM-progress the result — that's
Set Builder's job (see [`set-builder.md`](./set-builder.md)).

---

## 10. Weighted Random Rationale — Why `score ** 2`

The fill loop weights each pool entry by `score ** 2`, not by `score` itself
(`routes.py:1224`).

### The intuition

A *linear* weighting would give a track scoring `0.6` exactly 60% the draw
probability of a track scoring `1.0`. But in classification space, `0.6` is
*meaningfully* worse than `1.0` — the BPM is off by 4–5 from the category
centre, or the energy curve doesn't quite peak. A DJ rolling a peak-time crate
mostly wants tracks near `0.9+`; they tolerate occasional `0.7`s for variety
but rarely want `0.5`s.

Squaring the score widens the gap. A `0.6` track is now drawn at
`0.36 / 1.00 = 36%` the rate of a `1.0` track — much closer to the DJ's
mental model of "this is *meaningfully* worse."

### Numeric example

For a pool of three tracks scoring `[1.0, 0.8, 0.6]`:

| Weighting | Draw probabilities |
|---|---|
| Linear (`s`) | 41.7% / 33.3% / 25.0% |
| Squared (`s ** 2`) | 56.0% / 35.8% / 8.2% |
| Cubed (`s ** 3`) | 64.5% / 33.0% / 2.5% |

Squared keeps a real but diminished long tail. Cubed effectively eliminates the
tail; linear keeps too much of it. Squared is the empirically-comfortable
middle.

---

## 11. Why a Pool, Not Top-N

Pool size: `pool_size = min(len(scored), max(fill_count * 3, 60))`
(`routes.py:1217`).

### The two failure modes this avoids

- **Strictly top-N (no random draw)** — every call returns the same N tracks
  in the same order. A DJ hitting *"Suggest tracks"* three times in a row sees
  no new options. The whole point of the panel is exploration; determinism
  kills that.
- **Uniform random over the whole library** — most tracks score near zero for
  any given category, so most draws would be terrible.

### The pool size formula

`max(fill_count * 3, 60)`:

- The `* 3` multiplier guarantees the random draw has room to actually vary
  the output. With `count=20`, you draw 20 from a pool of 60 — different runs
  will share roughly 80% of picks but reorder/replace ~4 of them. Enough
  variety to feel fresh; not so much that quality drops.
- The `60` floor matters for small `count`. With `count=5`, `fill_count * 3 =
  15` is too narrow — every draw becomes nearly deterministic. The floor of
  60 ensures the random draw has breathing room even for tiny requests.
- The outer `min(len(scored), ...)` is the natural ceiling: never pull from
  more tracks than exist.

The pool always represents the top of the distribution by raw score, so
even the unluckiest random draw still selects from "good" candidates. The
weighting ([Weighted Random Rationale](#10-weighted-random-rationale--why-score--2)) then biases within that top slice.

---

## 12. Examples

### Example A — 20 peak-time tracks from the full library

```http
POST /api/playlists/suggest
Content-Type: application/json

{
  "category": "peak",
  "count": 20
}
```

Response (truncated):

```json
{
  "category": "peak",
  "results": [
    {"track_id": 1283, "score": 0.964},
    {"track_id": 4501, "score": 0.911},
    {"track_id": 2087, "score": 0.887},
    ...
  ]
}
```

### Example B — 30 build tracks with two pinned seeds

```http
POST /api/playlists/suggest
Content-Type: application/json

{
  "category": "build",
  "count": 30,
  "seed_track_ids": [42, 99]
}
```

The response has exactly 30 items: track 42 first, track 99 second (regardless
of their individual `build` scores), followed by 28 weighted-random fill
tracks. If track 42 had been deleted from the library, its row still appears
with `score: 0.0`.

### Example C — 10 warmup tracks scoped to a playlist

```http
POST /api/playlists/suggest
Content-Type: application/json

{
  "category": "warmup",
  "count": 10,
  "playlist_id": 5
}
```

Only tracks belonging to `DjmdPlaylist(ID=5)` are scored. The pool size formula
caps at the playlist's track count, so a 12-track playlist yields a pool of at
most 12 (not 60).

### Example D — Iterating with excludes for "more like these"

The UI's *"Use selected as seeds"* button does this in one click:

```http
POST /api/playlists/suggest
{
  "category": "peak",
  "count": 20,
  "seed_track_ids": [1283, 2087],
  "exclude_ids": [4501, 9, 1156, 2042, 8801, ...]   // the rest of the previous result
}
```

Result: tracks 1283 and 2087 pinned at the top, 18 fresh weighted-random
selections from outside the previous response.

### Example E — Creating a playlist from a suggestion

```http
POST /api/playlists
Content-Type: application/json

{
  "name": "Sat night peak crate",
  "track_ids": [1283, 2087, 4501, 9, 1156, ...]
}
```

Response:

```json
{"playlist_id": 38291, "name": "Sat night peak crate", "track_count": 20}
```

Provided Rekordbox is closed. If it's open: `409 Rekordbox is running — close
it before saving playlists`.

---

## 13. Edge Cases

| Situation | Behaviour |
|---|---|
| `count` larger than available matches | Response is shorter than `count`. No padding, no error. |
| Empty library | `results: []`. |
| Every track excluded *and* no seeds | `results: []`. |
| Every track excluded *with* seeds | `results = [seeds...]` only. |
| Seed ID does not exist in the library | Included in response with `score: 0.0`. The track is missing from `db.get_content()`, so it never lands in `seed_scored`; the fallback at `routes.py:1211` substitutes `0.0`. |
| Seed ID also in `exclude_ids` | Seed wins. Track is included. |
| `get_classification` raises for a non-seed | Track is silently dropped. |
| `get_classification` raises for a seed | Seed included with `score: 0.0`. |
| `playlist_id` references an empty playlist | `results: []`. |
| `playlist_id` references a non-existent playlist | `404 Playlist N not found`. |
| `category` not in `CATEGORIES` | `400 Unknown category 'X'. Valid: [...]`. |
| `count = 0` or `count > 500` | `400 count must be between 1 and 500`. |

For Playlist *Creation*:

| Situation | Behaviour |
|---|---|
| Empty `name` or whitespace-only | `400 Playlist name is required`. |
| Empty `track_ids` | `400 No tracks provided`. |
| Rekordbox open | `409 Rekordbox is running — close it before saving playlists`. |
| Track ID does not exist in `DjmdContent` | Row is still added to `DjmdSongPlaylist` with the dangling `ContentID`. Rekordbox tolerates this and shows the row as missing in the UI. Validate upstream if this matters. |
| Any DB error mid-write | `db.session.rollback()`, then `500 Failed to create playlist: ...`. |

---

## 14. Playlist Suggest vs Set Builder

These two endpoints sound similar and answer different questions.

| Property | Playlist Suggest | Set Builder |
|---|---|---|
| Output | Unordered bag of category matches | Ordered sequence with transitions |
| Optimised for | Crate-browsing, "give me peak tracks" | Play-ready sets, "give me a one-hour build" |
| Transition scoring | None | Yes — every gap scored on BPM/key/energy |
| BPM progression | None | Yes — monotonic toward `end_bpm` with progress bonus |
| Anchors | No — seeds are pinned at the front, not interleaved | Yes — `anchor_track_ids` merged into the result at BPM-sorted positions |
| Randomisation | Weighted random draw on pool of `3× count` | Beam search (width=5) — deterministic given the same seed |
| Mix advice per track | None | Yes — `transition_advice()` per gap |
| Category | Required input (single category) | Implied by BPM range + energy mode |
| Typical input size | `count=20` | `duration_minutes=60` |
| Cost on a 3k-track library | One classify scan (~1–2s cold, <100ms cached) | Beam search over `find_similar(n=20–40)` per step (~3–10s) |

Use Playlist Suggest when you're stocking a crate. Use Set Builder when you're
planning the actual play order. They share `get_classification` and its cache,
so running them back-to-back is cheap.

See [`set-builder.md`](./set-builder.md) for the Set Builder reference.

---

## 15. Testing

Tests live in `tests/test_serve_routes.py`:

- `TestPlaylistSuggest` (around `tests/test_serve_routes.py:2025`):
  - `test_valid_category_returns_200` — happy path, empty DB.
  - `test_response_has_required_fields` — shape of the response.
  - `test_invalid_category_returns_400` — bad `category`.
  - `test_count_too_large_returns_400` — `count > 500`.
  - `test_results_sorted_by_score_descending` — scoring monotonicity within
    the deterministic top-N portion of the response.
  - `test_exclude_ids_omits_tracks` — exclude filter for non-seeds.
  - `test_count_limits_results` — response truncation.
  - `test_zero_score_tracks_excluded` — `BPM=50` (out of every category) yields
    `results: []`.
- `TestWriteGuards.test_create_playlist_blocked_when_rekordbox_running`
  (`tests/test_serve_routes.py:2609`) — confirms the `409` guard.

All tests use `MagicMock`-style DB fixtures and patch
`autocue.analysis.classify.get_energy_curve` / `get_mixability` to avoid hitting
real [ANLZ files](./GLOSSARY.md#anlz-files-and-tags). The autouse `conftest.py` fixture clears the classification
cache before each test.

To add a test for a new edge case, follow the existing pattern: build a fake
content with `_make_content(track_id, bpm_int)`, wrap a list of them in
`_db_with_tracks([...])`, patch the two analysis helpers, and use the
FastAPI `TestClient` from `_make_client(db)`.

---

## 16. Related

- [`track-classification.md`](./track-classification.md) — how
  `get_classification()` produces the category scores Playlist Suggest depends
  on.
- [`set-builder.md`](./set-builder.md) — the ordered-set counterpart; shares
  the classification cache and the "must include this track" idea via
  `anchor_track_ids`.
- [`rest-api.md`](./rest-api.md) — full REST API reference, including the
  `/api/playlists` listing endpoint used to populate the source dropdown.
- [`energy-and-mixability.md`](./energy-and-mixability.md) — the underlying
  ANLZ-driven signals that feed into the category scores.

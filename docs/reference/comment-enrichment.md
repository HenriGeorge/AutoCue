# Comment Enrichment

AutoCue writes DJ-useful metadata directly into the Rekordbox track comment
field so that the information appears on the CDJ display while loading the
track. The format mirrors the convention popularised by
[Mixed In Key](https://mixedinkey.com/) (MIK) so existing muscle memory and
collection-management habits carry over.

```
8A - Energy 7 | Peak | 4 bar intro
```

A single string carries:

- **Key** in Camelot notation (`8A`, `12B`, ...)
- **Energy level** on a 1–10 integer scale (the MIK convention)
- **Category** from AutoCue's track classifier (`Warm Up` / `Build` / `Peak` /
  `After Hours` / `Closing`)
- **Intro length** in bars (rounded to the nearest 4, the standard phrase grid)

This document describes the format, the build/write pipeline, the REST
endpoints, the sentinel pattern that protects user-authored comment text, and
the edge cases you should know about before flipping the feature on.

---

## Table of Contents

1. [Overview](#1-overview)
2. [The MIK-compatible format](#2-the-mik-compatible-format)
3. [`build_comment_string(content, db)`](#3-build_comment_stringcontent-db)
4. [`DjmdContent.Commnt` spelling](#4-djmdcontentcommnt-spelling)
5. [The sentinel block pattern](#5-the-sentinel-block-pattern)
6. [`enrich_comment(content, db, *, overwrite, dry_run)`](#6-enrich_commentcontent-db--overwrite-dry_run)
7. [`enrich_comments_batch(track_ids, db, *, overwrite, dry_run)`](#7-enrich_comments_batchtrack_ids-db--overwrite-dry_run)
8. [REST endpoints](#8-rest-endpoints)
9. [Backup behaviour](#9-backup-behaviour)
10. [Rekordbox-running guard](#10-rekordbox-running-guard)
11. [Energy 1–10 scale, in detail](#11-energy-110-scale-in-detail)
12. [Intro info, in detail](#12-intro-info-in-detail)
13. [UI surface](#13-ui-surface)
14. [Examples](#14-examples)
15. [Edge cases](#15-edge-cases)
16. [Reversing AutoCue comments](#16-reversing-autocue-comments)
17. [Comment length and truncation](#17-comment-length-and-truncation)
18. [Testing](#18-testing)
19. [Related references](#19-related-references)

---

## 1. Overview

### What it does

Comment enrichment populates [`DjmdContent.Commnt`](./GLOSSARY.md#djmdcontent)
(the Rekordbox track comment column — note the spelling) with a
deterministically-formatted string built from analysis results the rest of
AutoCue already produces:

| Component       | Source                                                                          |
| --------------- | ------------------------------------------------------------------------------- |
| Camelot key     | `DjmdContent.Key.ScaleName` (Rekordbox's stored key)                            |
| Energy 1–10     | `get_classification(content, db)["energy_mean"]` rescaled to 1–10               |
| Category        | `get_classification(content, db)["primary"]` mapped via `_CATEGORY_LABELS`      |
| `N bar intro`   | Phrase analysis from `analyzer.analyze_track(content, db)`                      |

No new analysis is performed during enrichment — every input is fetched from
the already-computed classifier and the ANLZ phrase reader.

### Why it matters

The Rekordbox track comment is one of the few text fields surfaced on the CDJ
itself when a track is loaded or hovered. A DJ scrolling through 200 tracks in
a USB folder sees the comment as part of the row label, and the same value is
visible at the moment of cueing the next track on a CDJ-3000 / NXS2 head unit.
A short, dense, predictable comment like `8A - Energy 7 | Peak | 4 bar intro`
removes the mental tax of remembering each track's character in the moment.

The format is also additive — the enrichment string lives inside a sentinel
block that AutoCue can rewrite without losing whatever else the user already
typed into the comment field.

---

## 2. The MIK-compatible format

The full format string is:

```
{key} - Energy {N} | {category} | {N} bar intro
```

Each section is optional and dropped (along with the surrounding ` | `
separator) when no data is available. The pieces are joined with ` | ` (space,
pipe, space):

```python
# autocue/analysis/comment.py:96-113
parts: list[str] = []

# Key + energy block (MIK-compatible prefix)
if key and level is not None:
    parts.append(f"{key} - Energy {level}")
elif key:
    parts.append(key)
elif level is not None:
    parts.append(f"Energy {level}")

if cat_label:
    parts.append(cat_label)

intro = _intro_bars(content, db)
if intro:
    parts.append(f"{intro} bar intro")

return " | ".join(parts)
```

The leading `key + " - Energy " + level` block is what makes the output
MIK-compatible: collections that already filter/sort on a regex like
`^([0-9]{1,2}[AB])\s*-\s*Energy\s+([0-9]+)` (a common Rekordbox / Serato smart
playlist trick) will continue to work after AutoCue takes over.

### Category labels

The classifier emits machine-readable category strings; the enrichment
formatter maps them to title-cased human labels:

```python
# autocue/analysis/comment.py:22-28
_CATEGORY_LABELS = {
    "warmup":      "Warm Up",
    "build":       "Build",
    "peak":        "Peak",
    "after_hours": "After Hours",
    "closing":     "Closing",
}
```

If the classifier returns a category not in this dict (or an empty string), the
category segment is dropped entirely.

### Energy mapping

`energy_mean` from the classifier is a 0–1 float (the smoothed mean of the PWAV
energy curve). It is rescaled to a 1–10 integer:

```python
# autocue/analysis/comment.py:42-46
def _energy_level(energy_mean: float | None) -> int | None:
    if energy_mean is None:
        return None
    return max(1, min(10, round(energy_mean * 9) + 1))
```

- `energy_mean = 0.0` → `1` (the floor; energy `0` is never emitted)
- `energy_mean = 0.5` → `round(4.5) + 1 = 6` (banker's rounding; `round` is half-to-even)
- `energy_mean = 1.0` → `10`

The `1 + round(energy_mean * 9)` shape was chosen so that the 0–1 input is
spread across the full 1–10 output range. Returning `None` (rather than `0`)
when no PWAV data is available causes the energy segment to be dropped, instead
of writing a misleading "Energy 1".

### Intro info

The intro length comes from the phrase analyzer:

```python
# autocue/analysis/comment.py:49-79
def _intro_bars(content, db) -> int | None:
    try:
        from ..analyzer import analyze_track
        from ..models import PhraseLabel
        phrases = analyze_track(content, db)
        if not phrases:
            return None
        # First non-INTRO phrase start = intro end
        non_intro = [p for p in phrases if p.label != PhraseLabel.INTRO]
        if not non_intro:
            return None
        intro_end_ms = min(p.position_ms for p in non_intro)
        if intro_end_ms <= 0:
            return None
        raw_bpm = getattr(content, "BPM", 0) or 0
        bpm = float(raw_bpm) / 100.0
        if bpm <= 0:
            return None
        ms_per_bar = (60_000.0 / bpm) * 4
        bars_raw = intro_end_ms / ms_per_bar
        # Round to nearest 4 bars (standard phrase grid)
        bars = max(4, round(bars_raw / 4) * 4)
        return bars
    except Exception:
        return None
```

Notes:

- `DjmdContent.BPM` is stored as **BPM × 100** (e.g. `12800` → 128.00 BPM). The
  `/ 100.0` divisor restores the real value.
- A bar at 4/4 contains 4 beats; `ms_per_bar = (60000 / bpm) * 4`.
- The result is snapped to the nearest 4-bar boundary (`round(bars_raw / 4)
  * 4`) with a floor of 4, because DJ phrasing is built around 8- and
  16-bar boundaries — `5 bar intro` would be both wrong and unhelpful.
- If the track has no phrases, no non-intro phrases, no BPM, or
  `analyze_track` raises (unsupported ANLZ version, missing file, etc.),
  `_intro_bars` returns `None` and the segment is dropped silently.

---

## 3. `build_comment_string(content, db)`

```python
# autocue/analysis/comment.py:82-113
def build_comment_string(content, db) -> str:
    cls = get_classification(content, db)
    key = _camelot_key(content)
    energy_mean = cls.get("energy_mean")
    level = _energy_level(energy_mean)
    category = cls.get("primary", "")
    cat_label = _CATEGORY_LABELS.get(category, "")
    ...
    return " | ".join(parts)
```

| Parameter | Type                 | Notes                                                            |
| --------- | -------------------- | ---------------------------------------------------------------- |
| `content` | `DjmdContent`        | Pyrekordbox ORM row. Must have `.Key.ScaleName`, `.BPM` as ints. |
| `db`      | `Rekordbox6Database` | Used by the classifier and the phrase analyzer.                  |

Returns a `str` that is either the joined format string or `""` if **every**
component is unavailable. `enrich_comment()` treats `""` as "nothing to do" and
exits without modifying the comment.

The Camelot key is read off the relationship row:

```python
# autocue/analysis/comment.py:31-39
def _camelot_key(content) -> str:
    try:
        k = getattr(content, "Key", None)
        if k:
            return str(getattr(k, "ScaleName", "") or "").strip()
    except Exception:
        pass
    return ""
```

Two important details:

1. `content.Key` is the pyrekordbox ORM relationship row (`DjmdKey`), not the
   key string. `.ScaleName` is the Camelot label (e.g. `"8A"`).
2. The whole thing is wrapped in `try/except` because tracks without analysed
   keys may have `content.Key = None`, and Rekordbox occasionally stores empty
   strings instead of `None`.

---

## 4. `DjmdContent.Commnt` spelling

The track comment column is spelled `Commnt`, **without the second `e`**. This
is a long-standing Rekordbox schema quirk — every other table in the database
uses the correct spelling.

| Table         | Column    | Notes                                                |
| ------------- | --------- | ---------------------------------------------------- |
| `DjmdContent` | `Commnt`  | Track-level comment. The abbreviated form.           |
| `DjmdCue`     | `Comment` | Hot-cue/memory-cue label. Correctly spelled.         |
| `DjmdHotCueBanklistCue` | `Comment` | Same as `DjmdCue`. Correctly spelled.    |

Always use `getattr` with a default so that mocked / partially-populated rows
do not raise `AttributeError`:

```python
# autocue/analysis/comment.py:128
existing = str(getattr(content, "Commnt", "") or "").strip()
```

This pattern appears in `enrich_comment` and in the preview endpoint
(`routes.py:1822`). When writing tests, mock rows must use the abbreviated
spelling — `MagicMock().Commnt = "..."`, **not** `Comment`.

---

## 5. The sentinel block pattern

User-typed text inside the comment field needs to survive enrichment, and
re-running enrichment must not double-append the AutoCue block. Both
requirements are solved with a sentinel:

```python
# autocue/analysis/comment.py:21
_SENTINEL = "/* AutoCue:"
```

The full block written into the comment is:

```
/* AutoCue: 8A - Energy 7 | Peak | 4 bar intro */
```

The opening `/* AutoCue:` (with no closing `*/`) is enough to anchor a
substring search; the closing `*/` is part of the appended block but not part
of the sentinel string itself. This matches the convention Rekordbox uses for
its own "Add My Tag to Comments" feature, so DJs already familiar with the `/*
... */` convention will recognise the block.

### How re-running affects the comment

`enrich_comment` has three paths:

```python
# autocue/analysis/comment.py:134-142
if overwrite or not existing:
    new_comment = enrichment
elif _SENTINEL in existing:
    # Replace the existing AutoCue sentinel block
    sentinel_start = existing.index(_SENTINEL)
    base = existing[:sentinel_start].rstrip()
    new_comment = f"{base} {_SENTINEL} {enrichment} */" if base else f"{_SENTINEL} {enrichment} */"
else:
    new_comment = f"{existing} {_SENTINEL} {enrichment} */"
```

1. **`overwrite=True` or the comment is empty** → write `enrichment` directly.
   User text (if any) is discarded only when the caller explicitly opts in.
2. **The sentinel is already present** → take everything before the sentinel
   (`existing[:sentinel_start]`), strip trailing whitespace, and re-append a
   freshly-built block. This is the idempotent re-run path: enrichment can be
   called any number of times and the comment converges on a single block.
3. **A comment exists but has no sentinel** → append ` /* AutoCue: ... */`
   after the user text, preserving it intact.

The anchor uses `index(_SENTINEL)` (no regex), which is robust to whatever
characters appear inside the AutoCue block. The block is "anchored from the
left" — anything past the first `/* AutoCue:` is considered part of the
sentinel block and replaced on the next run, even if a literal `*/` appears
earlier in the user text.

### When the sentinel appears mid-string

If a user has manually written `before /* AutoCue: stale stuff */ after`, the
next enrichment run will produce:

```
before /* AutoCue: 8A - Energy 7 | Peak | 4 bar intro */
```

i.e. the `after` text is lost because `existing[:sentinel_start]` strips
everything from the first `/* AutoCue:` onwards. This is intentional —
treating that `after` as user text would mean the block could never be
re-replaced cleanly.

---

## 6. `enrich_comment(content, db, *, overwrite, dry_run)`

```python
# autocue/analysis/comment.py:116
def enrich_comment(content, db, *, overwrite: bool = False, dry_run: bool = False) -> str | None:
```

Single-track entrypoint used by the streaming endpoint and the preview
endpoint.

| Argument    | Default | Effect                                                                  |
| ----------- | ------- | ----------------------------------------------------------------------- |
| `overwrite` | `False` | When `True`, ignore existing text and replace the comment in full.      |
| `dry_run`   | `False` | Compute the new comment but do **not** assign it to `content.Commnt`.   |

Returns:

- `None` when there is nothing to do — empty enrichment, or the computed new
  comment is byte-identical to the existing comment.
- The new comment string otherwise.

**Critically, `enrich_comment` does not commit the SQLAlchemy session.** The
caller decides when to commit so that one transaction can cover many tracks
(batch path) or one track at a time (streaming path). See
`autocue/analysis/comment.py:127` ("Does NOT commit — caller must commit the
session.").

---

## 7. `enrich_comments_batch(track_ids, db, *, overwrite, dry_run)`

```python
# autocue/analysis/comment.py:153
def enrich_comments_batch(
    track_ids: list[int],
    db,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
```

Returns:

```python
{
    "enriched": int,       # tracks whose Commnt was changed
    "skipped": int,        # missing tracks or no-op enrichments
    "errors": int,         # exceptions during processing
    "backup_path": str | None,  # path to the master.db backup (None on dry_run / failure)
}
```

Behaviour:

1. **Backup** — when `dry_run=False` and `track_ids` is non-empty, the function
   calls `db_writer.backup_database(db._db_dir / "master.db")` once at the
   top, before any writes. The backup includes WAL/SHM sidecars; see the
   backup-and-restore reference for details. Backup failures are logged at
   `WARNING` and execution continues with `backup_path=None`.
2. **Per-track work** — for each `tid`, the function:
   - fetches `content = db.get_content(ID=tid)`,
   - skips with `skipped += 1` if the row is missing,
   - calls `enrich_comment(content, db, ...)` and increments `enriched` or
     `skipped` based on the return value,
   - catches any exception, logs at `WARNING`, and increments `errors`.
3. **Single commit at end** — if `dry_run=False` and `enriched > 0`,
   `db.session.commit()` is called once. On commit failure the function
   rolls back and raises `RuntimeError`. This is the sync path's
   transaction model: all-or-nothing.

The streaming endpoint deliberately does **not** use this batch helper — it
commits per track so that one failing track does not roll back the whole job.
See [§8.3](#83-post-apienrich-commentsstream--sse-per-track-commit).

---

## 8. REST endpoints

All three live in `autocue/serve/routes.py` and share the
`EnrichCommentsRequest` / `EnrichCommentsResponse` / `CommentPreviewRequest`
schemas (`autocue/serve/schemas.py:462-483`).

### 8.1 `POST /api/enrich-comments` — synchronous batch

`routes.py:1736-1752`

Request body (`EnrichCommentsRequest`):

```json
{
  "track_ids": [101, 102, 103],
  "overwrite": false,
  "dry_run": false
}
```

Response (`EnrichCommentsResponse`):

```json
{
  "enriched": 2,
  "skipped": 1,
  "errors": 0,
  "dry_run": false,
  "backup_path": "/Users/me/Library/.../master.db.20260607-091233.bak"
}
```

Implementation calls `enrich_comments_batch` and wraps the result. On any
exception, `db.session.rollback()` runs and a `500` is returned with the
exception message.

This endpoint is the safest choice for small batches (a single playlist, a
handful of tracks) where you want a clean transaction boundary. For
many-hundred-track jobs prefer the streaming endpoint so that per-track
failures do not abort the whole run.

### 8.2 `POST /api/enrich-comments/preview` — read-only preview

`routes.py:1816-1830`

Request body (`CommentPreviewRequest`):

```json
{ "track_id": 101 }
```

Response (`CommentPreviewResponse`):

```json
{
  "track_id": 101,
  "current_comment": "Banger from Jeff Mills",
  "preview": "Banger from Jeff Mills /* AutoCue: 8A - Energy 7 | Peak | 4 bar intro */"
}
```

The preview endpoint:

- uses `get_ro_db` (read-only dependency) — it cannot accidentally write to the
  database;
- calls `enrich_comment(..., overwrite=False, dry_run=True)` so no write
  occurs;
- falls back to `enrichment` (just the AutoCue block) when `enrich_comment`
  returns `None`, so the UI can still show what *would* be written when the
  comment is already up to date.

This is what the UI calls when the user clicks **Preview**.

### 8.3 `POST /api/enrich-comments/stream` — SSE, per-track commit

`routes.py:1755-1813`

This is the workhorse endpoint. It accepts the same `EnrichCommentsRequest`
body and returns an `text/event-stream` response.

Events look like:

```
data: {"processed": 1, "total": 50, "enriched": 1}

data: {"processed": 2, "total": 50, "enriched": 2}

...

data: {"done": true, "enriched": 47, "skipped": 2, "errors": 1, "backup_path": "/.../master.db.20260607-091233.bak", "dry_run": false}
```

Implementation details that matter for callers:

1. **One backup at the top.** Before iterating, the endpoint calls
   `backup_database(db._db_dir / "master.db")` once (when `dry_run=False` and
   the request has at least one track). Backup failure is swallowed silently
   (`backup_path` stays `None`).
2. **Per-track commit, per-track rollback.** Inside the per-track try block:

   ```python
   # routes.py:1789-1802
   result = enrich_comment(content, db, overwrite=req.overwrite, dry_run=req.dry_run)
   if result is None:
       skipped += 1
   else:
       enriched += 1
       if not req.dry_run:
           try:
               db.session.commit()
           except Exception as commit_exc:
               db.session.rollback()
               errors += 1
               enriched -= 1
               logger.warning("Enrichment commit failed for track %s: %s", tid, commit_exc)
   ```

   A failing commit rolls back **only that track's change** and decrements
   `enriched`. The old "batch commit at the end" pattern is gone — one bad
   track no longer wipes out the whole job. This is the documented contract;
   see CLAUDE.md for the rationale.
3. **SSE plumbing.** The response sets
   `Cache-Control: no-cache` and `X-Accel-Buffering: no`. The latter both
   disables nginx-style proxy buffering and signals AutoCue's own gzip
   middleware (`GZipMiddleware`, `app.py`) to leave the stream alone — Starlette
   skips gzip on `text/event-stream` so events flush immediately.
4. **POST-bodied SSE.** Because the request is a `POST` with a JSON body,
   browser-side `EventSource` cannot be used. The UI consumes the stream via
   `fetch` + `ReadableStream` (see `_consumeSSE` in `docs/index.html`).

---

## 9. Backup behaviour

| Path                              | When is a backup made?                              |
| --------------------------------- | --------------------------------------------------- |
| `POST /api/enrich-comments`       | Once at the top of `enrich_comments_batch`, unless `dry_run`. |
| `POST /api/enrich-comments/stream`| Once at the top of `event_stream()`, unless `dry_run`.        |
| `POST /api/enrich-comments/preview` | Never (read-only).                                |

Backups are written to the standard AutoCue backup directory via
`db_writer.backup_database(...)`, which also copies the `master.db-wal` and
`master.db-shm` sidecars. The backup file path is reported back in the
response (`backup_path`) so the UI can show "Restore" affordances pointing at
this exact file. See the backup-and-restore reference for the path layout and
restore behaviour.

---

## 10. Rekordbox-running guard

All write endpoints check `db_writer.rekordbox_is_running()` and return `409
Conflict` when Rekordbox is open:

```python
# routes.py:1741-1742
if rekordbox_is_running() and not req.dry_run:
    raise HTTPException(409, "Rekordbox is running — close it before enriching comments")
```

`dry_run=True` requests are allowed even when Rekordbox is running, because no
writes occur. This is verified by
`test_enrich_comments_dry_run_allowed_when_rekordbox_running`
(`tests/test_serve_routes.py:2650-2658`).

---

## 11. Energy 1–10 scale, in detail

The energy classifier yields `energy_mean` ∈ [0, 1]. The MIK convention is
1–10 integer. The mapping is:

```python
level = max(1, min(10, round(energy_mean * 9) + 1))
```

Worked examples:

| `energy_mean` | `energy_mean * 9` | `round(...)` | `+ 1` | clamped → `level` |
| -------------:| -----------------:| ------------:| -----:| -----------------:|
| 0.00          | 0.00              | 0            | 1     | **1**             |
| 0.10          | 0.90              | 1            | 2     | **2**             |
| 0.50          | 4.50              | 4            | 5     | **5**             |
| 0.55          | 4.95              | 5            | 6     | **6**             |
| 0.75          | 6.75              | 7            | 8     | **8**             |
| 1.00          | 9.00              | 9            | 10    | **10**            |

Two consequences worth knowing:

- `round` is Python's banker's rounding (half-to-even). `round(4.5) == 4`, not
  `5`. For most tracks this is invisible; for edge cases (`energy_mean` that
  lands exactly on `.5 / 9`) the result depends on the parity of the bucket.
- A track with no PWAV data has `energy_mean = None` from the classifier, and
  `_energy_level` returns `None`. The energy segment is omitted from the
  comment entirely rather than written as `Energy 1`.

---

## 12. Intro info, in detail

The intro segment is built by `_intro_bars` (see [§2](#2-the-mik-compatible-format) for the source).
Pseudocode:

```
intro_end_ms  = first non-INTRO phrase start
ms_per_bar    = (60_000 / bpm) * 4
bars_raw      = intro_end_ms / ms_per_bar
bars          = max(4, round(bars_raw / 4) * 4)
```

| Track                                          | Intro segment       |
| ---------------------------------------------- | ------------------- |
| Phrases: `[INTRO@0, VERSE@30000]`, 120 BPM     | `15 bars → 16 bar intro` |
| Phrases: `[VERSE@0, CHORUS@15000]`, 128 BPM    | `0 bars` → segment dropped (no INTRO) |
| Phrases: `[]` (no phrase data)                 | segment dropped (`None`)            |
| BPM = 0 (corrupt grid)                         | segment dropped (`None`)            |
| `analyze_track` raises (unsupported ANLZ)      | segment dropped (`None`)            |

The floor of 4 bars exists because the snap-to-4-bar can produce `0` for very
short intros (e.g. an 8-beat intro at 128 BPM is just under 2 bars), and "0 bar
intro" is worse than no intro segment at all.

---

## 13. UI surface

The Comment Enrichment panel lives in `docs/index.html` under the **Library**
tab. It exposes:

- A **Preview** button — calls `/api/enrich-comments/preview` for the currently
  selected (or first filtered) track and shows the before / after comment
  side-by-side.
- An **Apply** button — calls `/api/enrich-comments/stream` for every
  `filteredTracks()` track. The streaming response drives a progress bar via
  the shared `_consumeSSE` helper.
- A **Dry-run** checkbox — sends `dry_run: true`. Allowed even when Rekordbox
  is running (no DB writes happen).
- An **Overwrite** checkbox — sends `overwrite: true`. The UI warns explicitly
  that user-authored comment text outside the sentinel block will be lost.

The Apply path always uses the streaming endpoint, never the sync batch — so a
single track that fails to commit does not abort the rest of the job.

---

## 14. Examples

### Example A — track with no prior comment

Inputs: `Commnt = ""`, key `8A`, `energy_mean = 0.72`, category `peak`,
phrases yield a 16-bar intro.

| Step             | `Commnt` value                                  |
| ---------------- | ----------------------------------------------- |
| Before           | `""`                                            |
| After 1st enrich | `8A - Energy 7 | Peak | 16 bar intro`           |
| After 2nd enrich | `8A - Energy 7 | Peak | 16 bar intro` (no-op)   |

The second run is detected by `new_comment == existing` and returns `None`
without touching the row, so `skipped += 1` and no commit happens.

### Example B — track with user-authored comment

Inputs: `Commnt = "Cracking edit — gift from Jeff"`, key `12A`, energy 0.40,
category `build`, 8-bar intro.

| Step                  | `Commnt` value                                                                        |
| --------------------- | ------------------------------------------------------------------------------------- |
| Before                | `Cracking edit — gift from Jeff`                                                      |
| After 1st enrich      | `Cracking edit — gift from Jeff /* AutoCue: 12A - Energy 4 | Build | 8 bar intro */`  |
| After 2nd enrich      | `Cracking edit — gift from Jeff /* AutoCue: 12A - Energy 4 | Build | 8 bar intro */` (no-op) |
| After re-classify (energy now 0.55) | `Cracking edit — gift from Jeff /* AutoCue: 12A - Energy 6 | Build | 8 bar intro */` (sentinel block updated, user text preserved) |
| After **`overwrite=True`** | `12A - Energy 6 | Build | 8 bar intro` (user text gone)                          |

The sentinel-replacement path uses `existing[:sentinel_start].rstrip()` so the
trailing space before `/* AutoCue:` does not accumulate over re-runs.

### Example C — partially-populated track

Inputs: `Commnt = ""`, key `8A`, no PWAV (`energy_mean = None`), category
classified as `peak`, no phrase data.

| Step             | `Commnt` value         |
| ---------------- | ---------------------- |
| Before           | `""`                   |
| After 1st enrich | `8A | Peak`            |

Segments without data are dropped silently; the ` | ` separators are added by
`" | ".join(parts)`, so there are no orphan separators.

---

## 15. Edge cases

| Case                                                     | Behaviour                                                                  |
| -------------------------------------------------------- | -------------------------------------------------------------------------- |
| No key (track not analysed by Rekordbox)                 | Key segment dropped. If energy is known: `Energy 5 | Peak | 8 bar intro`. |
| No energy data (PWAV missing)                            | `level = None`; "Energy N" segment dropped.                                |
| No classification (`primary = ""`)                       | Category segment dropped.                                                  |
| No phrase data / `analyze_track` raises                  | "N bar intro" segment dropped.                                             |
| Every segment unavailable                                | `build_comment_string` returns `""`; `enrich_comment` returns `None`; the comment row is untouched. |
| BPM stored as `0` or `"0"`                               | `_intro_bars` returns `None`. Other segments still computed.               |
| Existing comment has the sentinel mid-string             | Everything from the first `/* AutoCue:` onward is treated as the AutoCue block and replaced. Text **before** the sentinel is preserved; text **after** is discarded. |
| User wrote `/* AutoCue:` themselves                      | AutoCue cannot distinguish this from its own block — the user text matching the sentinel is replaced. The sentinel is documented as a reserved marker. |
| `enrich_comment` produces a byte-identical comment       | Returns `None`; the stream / batch counts the track as `skipped`; no commit happens. |
| `dry_run=True`                                           | Backup is skipped, write is skipped, commit is skipped. Counters still increment based on what *would* happen. |
| `overwrite=True` with empty enrichment                   | `enrichment == ""` short-circuits at the top of `enrich_comment`; returns `None` regardless of `overwrite`. |

---

## 16. Reversing AutoCue comments

There is **no** `undo_comments_run()` helper today, in contrast with the
auto-tag feature's [`undo_tag_run()`](./auto-tag.md#10-undo_data-and-undo_tag_run).
The enrichment endpoints do not record an `undo_data` payload — they only
make a `master.db` backup before writing. That gives you two practical
ways to roll back a run:

### 16.1 Sentinel-based reversal (preserves user text)

Because every AutoCue contribution is wrapped in the
`/* AutoCue: ... */` sentinel block (see [§5](#5-the-sentinel-block-pattern)),
you can strip only AutoCue's contribution from each comment without touching
prior user text. The sentinel string defined in `autocue/analysis/comment.py:21`
is:

```python
_SENTINEL = "/* AutoCue:"
```

The block that ends up in `Commnt` has the shape
`<existing> /* AutoCue: <enrichment> */`, with a single space between the
user text and the opening sentinel. The regex below matches the AutoCue
block (and the single space that precedes it when one exists) without
swallowing any user-typed text:

```python
import re
_AUTOCUE_BLOCK = re.compile(r"\s?/\* AutoCue:[^*]*\*/")
```

> **Caveat.** This regex assumes the AutoCue block contains no literal `*`
> characters between `/* AutoCue:` and the closing `*/`. The current
> enrichment format (`8A - Energy 7 | Peak | 4 bar intro`) never produces a
> `*`, so this is safe for AutoCue-written comments. If a user manually
> embedded a `*` inside the block, the regex would stop early; in that case,
> use the substring-based approach in the Python snippet below instead.

**One-shot Python snippet** — run this against an offline `master.db` copy
(Rekordbox closed) to strip every AutoCue sentinel block library-wide:

```python
# strip_autocue_sentinels.py
from pyrekordbox.db6 import Rekordbox6Database
from autocue.analysis.comment import _SENTINEL

db = Rekordbox6Database()  # opens the default Rekordbox library
changed = 0
for content in db.get_content():
    existing = str(getattr(content, "Commnt", "") or "")
    if _SENTINEL not in existing:
        continue
    start = existing.index(_SENTINEL)
    # Walk back over the single separating space, if present.
    base = existing[:start].rstrip()
    if base != existing:
        content.Commnt = base
        changed += 1
db.session.commit()
print(f"Stripped AutoCue sentinel block from {changed} tracks.")
```

This mirrors the `existing[:sentinel_start].rstrip()` logic used by
`enrich_comment` itself (`autocue/analysis/comment.py:138-142`), so it
agrees byte-for-byte with how AutoCue itself locates the block boundary.

**SQL alternative** (read-only inspection — Rekordbox uses SQLCipher, so a
plain `sqlite3` shell needs the encryption key; in practice the Python
snippet above is the supported path):

```sql
-- Tracks currently carrying an AutoCue sentinel block:
SELECT ID, Title, Commnt
FROM djmdContent
WHERE Commnt LIKE '%/* AutoCue:%';
```

### 16.2 Full-database restore (rolls back everything since the backup)

Every write endpoint (sync batch and SSE stream) makes a
`master.db` backup before its first write — the backup path is reported in
the response as `backup_path`. To roll the whole library back to that
snapshot, call `POST /api/restore` with `{"filename": "<basename>"}`; see
[`backup-and-restore.md`](./backup-and-restore.md) for the path-validation
rules and the WAL/SHM sidecar handling.

**Caveats**:

- This is a **whole-database** rollback. Anything else you changed between
  the backup and the restore — new hot cues, edited grids, modified
  playlists, other AutoCue runs, Rekordbox-side edits — is also reverted.
- Restoring closes and re-opens the database connection on the server side
  and clears in-memory analysis state (see CLAUDE.md's "Restore backup"
  note). You will need to re-run any analysis that hadn't been persisted.

### 16.3 The `overwrite=True` trap

When `enrich_comment` is called with `overwrite=True`, the path at
`autocue/analysis/comment.py:134-135` writes the enrichment string **as the
entire comment**, discarding any prior user text:

```python
if overwrite or not existing:
    new_comment = enrichment
```

There is no sentinel to anchor on after this point — the user's original
text is no longer present in `DjmdContent.Commnt`. The sentinel-based
reversal in [§16.1](#161-sentinel-based-reversal-preserves-user-text) will
leave you with an empty comment (which is correct for tracks that started
empty, and lossy for tracks that didn't).

**The only way to recover the user's original comment after an `overwrite=True`
run is the `master.db` backup made by the endpoint.** The UI exposes the
`overwrite` checkbox with an explicit warning ([§13](#13-ui-surface));
treat it accordingly.

---

## 17. Comment length and truncation

### What the column type is

`DjmdContent.Commnt` is declared as SQL `Text`, not a fixed-length
`VARCHAR(N)`. The pyrekordbox model is:

```python
# pyrekordbox/db6/tables.py — DjmdContent definition
Commnt: Mapped[str] = mapped_column(Text, default=None)
"""The comment of the track."""
```

`Text` in SQLite (Rekordbox's underlying storage, encrypted via SQLCipher)
has **no hard length limit** at the column level — the practical ceiling is
the SQLite per-row limit (`SQLITE_MAX_LENGTH`, default ~10⁹ bytes) and
whatever Rekordbox's UI is willing to render. There is no
`db.session.commit()`-time truncation: SQLite stores whatever bytes the
ORM hands it, and the round-trip is lossless.

### What AutoCue itself writes

A canonical AutoCue enrichment string sits at ~40 ASCII characters:

```
8A - Energy 7 | Peak | 4 bar intro       # 36 chars
12A - Energy 10 | After Hours | 16 bar intro  # 44 chars
```

Wrapped in the sentinel block it grows by 15 characters (`/* AutoCue: ` +
` */`):

```
/* AutoCue: 8A - Energy 7 | Peak | 4 bar intro */    # 51 chars
```

Appended after existing user text, AutoCue adds one separating space plus
the wrapped block — a worst case of ~52 ASCII characters on top of
whatever was already there. That is well inside the practical comfort
range for any track-comment column.

### What happens with very long comments

AutoCue performs **no truncation today**. The relevant write paths in
`autocue/analysis/comment.py` always assign the full computed string to
`content.Commnt`:

```python
# autocue/analysis/comment.py:147-148
if not dry_run:
    content.Commnt = new_comment
```

There is no length check, no clip-to-N, and no warning when the resulting
string is unusually long. Because the column is `Text`, the
`db.session.commit()` call also does not raise — the bytes simply land in
the database.

The implication for unusual inputs:

| Scenario                                                    | What happens today                                                                                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User comment ~50 chars, AutoCue appends ~52 chars           | Written as-is. Roughly 100 ASCII chars in the comment. Comfortable.                                                                                                       |
| User comment 1 KB, AutoCue appends ~52 chars                | Written as-is. ~1 KB comment. Persists fine in SQLite; whether Rekordbox's track-list / CDJ UI renders all of it depends on the surface (CDJ-3000s clip to a few dozen chars). |
| User comment 100 KB, AutoCue appends ~52 chars              | Written as-is. The DB accepts it. Rekordbox import/export, AppleScript bridges, and CDJ rendering have no contract for this; behaviour is undefined.                      |
| Re-running enrichment on a 100 KB user comment              | Sentinel logic still finds `_SENTINEL` and rewrites only the sentinel block; the 100 KB stays put.                                                                        |
| `overwrite=True` against a 100 KB user comment              | Replaced wholesale with the ~40-char AutoCue string. (Same caveat as [§16.3](#163-the-overwritetrue-trap) — the original is only recoverable from the backup.)             |

### Known limitation / recommended future behaviour

The lack of any length cap is a **known limitation** rather than a
deliberate design choice. CDJ head units, Rekordbox's own track-list
columns, and DJ-software bridges (Serato/Mixed In Key) typically render
only the first few dozen characters of the comment, so a 1 KB user
comment with an AutoCue block appended at the end will display the user
text and clip the sentinel before it appears on-screen.

If a future change introduces a comment-length cap, the recommended
shape — and the one that preserves AutoCue's contract that the user's
text is sacred — is:

1. Define a soft cap (e.g. 255 chars, matching most legacy MIK / Serato
   tooling). Make it a `GenerationPrefs`-style setting, not a hardcode.
2. When `existing + sentinel block` would exceed the cap, **truncate
   AutoCue's contribution first** (drop the `N bar intro` segment, then
   the `Category` segment, then the energy block) until the whole row
   fits. The user's text is never trimmed.
3. If the user's text alone already exceeds the cap, log a warning and
   skip enrichment for that track rather than silently lopping off user
   data.

Until that lands, callers writing into already-long comments should
audit the result manually. AutoCue's behaviour today is "write whatever
the format produces; trust the column."

---

## 18. Testing

Relevant tests in `tests/test_serve_routes.py`:

- `test_enrich_comments_blocked_when_rekordbox_running`
  (`tests/test_serve_routes.py:2644-2648`) — asserts the sync endpoint returns
  409 when Rekordbox is running.
- `test_enrich_comments_dry_run_allowed_when_rekordbox_running`
  (`tests/test_serve_routes.py:2650-2658`) — asserts a `dry_run=True` request
  is allowed even while Rekordbox is open, and that `enrich_comments_batch`
  is invoked.

Both tests mock `autocue.db_writer.rekordbox_is_running` and (for the dry-run
case) `autocue.analysis.comment.enrich_comments_batch`, returning a stub dict
of counters.

When writing new tests:

- Mock `DjmdContent` rows with `.Commnt` (note the spelling), `.Key.ScaleName`,
  and `.BPM` (×100). The mocks for the classifier should return a dict shape
  `{"primary": "peak", "energy_mean": 0.7}` to match what
  `get_classification` produces.
- Per-test cache clearing is already handled by the autouse fixture in
  `tests/conftest.py` (it clears `classify._class_cache` and
  `energy._cache`), so successive tests do not see stale classification
  results.
- For end-to-end tests of the streaming endpoint, the SSE response can be
  consumed via `TestClient.stream(...)`. Look at the auto-tag and discover
  tests in the same file for the canonical pattern.

---

## 19. Related references

- `track-classification.md` — provides the `primary` category that becomes the
  human-readable label in the comment.
- `energy-and-mixability.md` — provides `energy_mean`, the input to the 1–10
  energy mapping.
- `auto-tag.md` — the parallel feature that writes the same kind of metadata
  into Rekordbox My Tags instead of the comment field. Both features share the
  classifier and energy infrastructure; pick one or run both.
- `backup-and-restore.md` — details the `backup_database` helper called by the
  sync and streaming endpoints, and how to restore from `backup_path`.
- CLAUDE.md — single-source-of-truth for the `Commnt` spelling rule, the
  sentinel pattern, and the per-track commit contract on the stream endpoint.

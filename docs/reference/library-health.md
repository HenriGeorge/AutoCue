# Library Health Check

The **Cue Quality Checker** is AutoCue's library-wide diagnostic surface. It scores
every track in your Rekordbox library 0–100, flags the issues that drag the score
down, and (when audio is reachable) recommends which generation tier — phrase,
bar, or heuristic — AutoCue should use to fix the track.

Implementation lives at `autocue/analysis/quality.py`. The HTTP surface is two
endpoints in `autocue/serve/routes.py`:

- `GET /api/tracks/{id}/health` — a single track's report.
- `GET /api/health` — an SSE stream that yields one event per track plus a
  summary event when the scan finishes.

A closely related surface — `POST /api/cue-tools-stream` (bulk rename / recolor /
shift / delete-orphan) — is documented in the [**Cue Library Tools**](#10-cue-library-tools--post-apicue-tools-stream)
section below, because the UI ships the two panels together and they share the
same backup-before-write contract.

Terminology note: this doc references several Rekordbox entities
(`DjmdContent`, `DjmdCue`, ANLZ `.EXT` / `.DAT`, `Kind`, `InFrame`, etc.). See
the [Rekordbox glossary](./GLOSSARY.md) for definitions.

---

## Table of Contents

- [1. Overview](#1-overview)
- [2. Health score formula](#2-health-score-formula)
- [3. Fix tiers in detail](#3-fix-tiers-in-detail)
- [4. Pure DB reads — why it matters](#4-pure-db-reads--why-it-matters)
- [5. Duplicate cue detection in detail](#5-duplicate-cue-detection-in-detail)
- [6. Issue codes — full reference](#6-issue-codes--full-reference)
- [Issue severity classification](#issue-severity-classification)
- [7. `GET /api/tracks/{id}/health`](#7-get-apitracksidhealth)
- [8. `GET /api/health` — SSE stream](#8-get-apihealth--sse-stream)
- [Performance profile](#performance-profile)
- [9. UI — Library Health Report](#9-ui--library-health-report)
- [10. Cue Library Tools — `POST /api/cue-tools-stream`](#10-cue-library-tools--post-apicue-tools-stream)
- [11. Performance](#11-performance)
- [12. Edge cases & behavioural notes](#12-edge-cases--behavioural-notes)
- [13. Examples](#13-examples)
- [14. Testing](#14-testing)
- [15. Related reference](#15-related-reference)

---

## 1. Overview

### What it does

For every track that has its audio file on disk:

1. Reads [`DjmdContent`](./GLOSSARY.md#djmdcontent) (analysis presence, BPM)
   and [`DjmdCue`](./GLOSSARY.md#djmdcue) (hot cues and memory cues) directly
   from `master.db`. **No [ANLZ](./GLOSSARY.md#anlz-files-and-tags) parsing.**
2. Computes a deterministic deduction-based score out of 100.
3. Lists every issue that fired, with a severity (`error`, `warning`, `info`).
4. Decides a **fix tier** — `phrase`, `bar`, or `heuristic` — that mirrors the
   generator's fallback ladder (`autocue/generator.py`).

For tracks whose audio is not on disk, the scanner short-circuits: score is
forced to `0`, fix tier is `none`, and no `DjmdCue` query is issued at all
(see `quality.py:74-76`).

### Why it exists

A working Rekordbox library degrades quietly. Files move, analysis fails, hot
cues get overwritten with defaults like *Cue 1 / Cue 2 / Cue 3*. The Cue Quality
Checker turns "my library feels messy" into a sortable list of *exactly*
N tracks with no cues, K tracks with no phrase analysis, and M tracks with
duplicate cue positions — each tagged with the highest-confidence fix AutoCue
can produce for it.

### What "score" means

`score` is an integer 0–100. **It is not a measure of music quality** — it is a
measure of cue-data completeness. A track with zero hot cues, no phrase
analysis, and no beat grid scores 50 even if it is a flawless master. The
intent is mechanical: "what's missing that AutoCue (or the DJ) could fix?"

### What "fix tier" means

`fix_tier` is the **strongest generation strategy AutoCue can apply right now**,
given what's already in `master.db`:

| Tier         | Trigger                                                  | Confidence  |
|--------------|----------------------------------------------------------|-------------|
| `phrase`     | `AnalysisDataPath` is set **and** `BPM > 0`              | Highest     |
| `bar`        | `BPM > 0` but no phrase data                             | Medium      |
| `heuristic`  | No beat grid (and no phrase data)                        | Low         |
| `none`       | Audio file missing from disk                             | n/a         |

See `quality.py:54-61` for the tier function. The UI uses this to split the
"Fix" buttons in two: phrase-quality fixes apply without a confirmation;
bar/heuristic fixes prompt the user first (see [§9 UI — Library Health Report](#9-ui--library-health-report)).

---

## 2. Health score formula

`check_track_health()` starts at `100` and applies these deductions in order
(`quality.py:64-135`):

| Issue code        | Severity | Deduction | Trigger                                                                                       |
|-------------------|----------|----------:|-----------------------------------------------------------------------------------------------|
| `NO_AUDIO_FILE`   | error    | **forces score = 0** | `FolderPath` is empty, or `os.path.exists()` returns false. All other checks skipped. |
| `NO_CUES`         | error    | −30       | Zero `DjmdCue` rows with `Kind ∈ [1..8]`.                                                     |
| `NO_PHRASE`       | info     | −10       | `DjmdContent.AnalysisDataPath` is empty / `None`.                                             |
| `NO_BEATGRID`     | info     | −10       | `DjmdContent.BPM` is `None`, `0`, or fails `float(bpm) > 0`.                                  |
| `DUPLICATE_CUE`   | warning  | −5        | Any two hot cues whose `InFrame` values differ by fewer than 2 frames (≈ 13 ms). At most one penalty per track regardless of count. |
| `UNNAMED_CUES`    | info     | −5        | Any hot cue's `Comment` is empty, whitespace-only, or matches `^Cue\s*\d+$` (case-insensitive). |
| `NO_MEMORY_CUE`   | info     | **0**     | Zero `DjmdCue` rows with `Kind == 0`. Info-only — does **not** affect the score.              |
| `INTERNAL_ERROR`  | error    | forces 0  | The track raised an unhandled exception. Emitted by `check_library_health()` only.            |

The final score is clamped to `[0, 100]` (`quality.py:127`). The hard floor in
practice is `100 − 30 − 10 − 10 − 5 − 5 = 40`, but `NO_AUDIO_FILE` overrides
everything to `0`.

### Worked examples

| Issues fired                                           | Math                | Score |
|--------------------------------------------------------|---------------------|------:|
| None                                                   | 100                 | **100** |
| `NO_MEMORY_CUE` only                                   | 100 − 0             | **100** |
| `NO_PHRASE`                                            | 100 − 10            | **90** |
| `NO_BEATGRID`                                          | 100 − 10            | **90** |
| `DUPLICATE_CUE` (one hot cue named "Drop", another 1 frame away) | 100 − 5    | **95** |
| `UNNAMED_CUES`                                         | 100 − 5             | **95** |
| `NO_PHRASE` + `NO_BEATGRID`                            | 100 − 10 − 10       | **80** |
| `NO_CUES`                                              | 100 − 30            | **70** |
| `NO_CUES` + `NO_PHRASE`                                | 100 − 30 − 10       | **60** |
| `NO_CUES` + `NO_PHRASE` + `NO_BEATGRID`                | 100 − 30 − 10 − 10  | **50** |
| `NO_AUDIO_FILE`                                        | forced              | **0** |
| `INTERNAL_ERROR`                                       | forced              | **0** |

### Why duplicates use `InFrame` directly

Rekordbox stores each cue position as **both** `InMsec` (milliseconds) and
`InFrame` (frames at 150 fps, ≈ 6.67 ms per frame). The quality checker reads
`InFrame` and compares integers — it never round-trips through milliseconds —
because the float-conversion drift can shift adjacent cues across the threshold
in either direction. The constant `_DUPLICATE_FRAMES = 2` lives at
`quality.py:17`.

The intent of `DUPLICATE_CUE` is to catch **double-write bugs**, not to flag
DJs who place two cues close together. Any two cues placed deliberately will
sit well over 2 frames apart; the threshold is tight on purpose.

### Why `NO_PHRASE` only checks `AnalysisDataPath`

`AnalysisDataPath` being non-empty means Rekordbox ran analysis at some point.
The `.EXT` file could in principle have been deleted from disk since, but
checking that would require ANLZ path resolution per track — too slow for a
3,000-track bulk scan. The bias is towards a fast scan. If you suspect ANLZ
files are missing, run AutoCue's normal generation flow; it logs ANLZ read
failures and falls back to bar mode automatically.

---

## 3. Fix tiers in detail

The fix tier is the **highest-confidence cue-generation strategy AutoCue
can run right now**, based on what's in `master.db`:

### `phrase` (highest confidence)

The track has both `AnalysisDataPath` set *and* a positive BPM. AutoCue can
read the ANLZ `PSSI` phrase tag and place cues at the verse / chorus / drop /
outro boundaries Rekordbox already detected. This is the default generator
mode and the only tier where smart-slot ordering (slot A = mix-in, slot B =
first outro) takes effect.

### `bar` (medium confidence)

No phrase analysis, but BPM is present. AutoCue falls back to placing cues
every `bars_interval` bars from `start_bar` (defaults to every 16 bars from
bar 1). Position accuracy depends on the beat grid being correct.

### `heuristic` (low confidence)

No phrase analysis and no BPM. AutoCue divides the track duration into
`max_cues` evenly-spaced slices. Useful as a starting point, but the cues
will not land on bar boundaries — the user will need to nudge them.

### `none`

The audio file is missing from disk. AutoCue cannot run the audio probe or
trust the metadata, so no fix is offered. Listed in the summary as
*"excluded (audio missing from disk)"*.

---

## 4. Pure DB reads — why it matters

`quality.py` deliberately reads only `DjmdContent` and `DjmdCue` plus one
`os.path.exists()` per track. It does **not** call `db.read_anlz_file()`. This
is a load-bearing design decision:

- ANLZ files live next to the audio (or in Rekordbox's analysis cache).
  Parsing one is ~50 ms of disk + protocol decode work.
- A library of 5,000 tracks × 50 ms = ~4 minutes of ANLZ work just to scan,
  every time.
- ANLZ parsing also occasionally throws `ConstError` / `IndexError` on
  unsupported format versions, which would noisy-up the scan results.

So the scanner trades a small amount of accuracy (it doesn't verify that the
`.EXT` file actually exists on disk) for a ~50× speedup on the bulk scan path.
The same library scans in seconds.

The presence of `AnalysisDataPath` (string, non-empty → "Rekordbox analyzed
this") is what the scanner uses as a proxy for phrase data. The `has_phrase`
field on `/api/tracks` *does* do the fast file-existence check via
`db.get_anlz_path(content, "EXT")` — but only for tracks the UI is actively
showing.

---

## 5. Duplicate cue detection in detail

Code (`quality.py:107-113`):

```python
in_frames = sorted(int(c.InFrame or 0) for c in hot_cues)
for i in range(len(in_frames) - 1):
    if in_frames[i + 1] - in_frames[i] < _DUPLICATE_FRAMES:
        issues.append(CueIssue("DUPLICATE_CUE", "warning",
                               "Duplicate cue positions (within ~13ms)"))
        score -= 5
        break  # one penalty regardless of how many duplicates
```

Behavior:

- Sorts all hot cues by `InFrame`, scans adjacent pairs.
- A pair `< 2` frames apart fires. `_DUPLICATE_FRAMES = 2`, frame resolution
  is `1000 / 150 ≈ 6.67 ms`, so the practical threshold is **< 13 ms**.
- `break` after the first match: **one −5 deduction per track**, no matter how
  many duplicate clusters exist. This is intentional — the issue is binary
  ("track has a double-write bug" vs. "doesn't").
- Boundary cases (from `tests/test_quality.py`):
  - Same `InFrame` (0 apart) → duplicate.
  - 1 frame apart (≈ 6.67 ms) → duplicate.
  - **2 frames apart (≈ 13 ms) → NOT a duplicate** (strict `<`).
  - 100 frames apart (≈ 670 ms) → not a duplicate.

`None`-valued `InFrame` is coerced to `0`, which is correct — if a cue row has
no position, it's effectively at the start of the track and any other cue at
position 0 is a real duplicate.

---

## 6. Issue codes — full reference

| Code              | Severity | Score impact | Surfaced where                                          | Meaning                                                                                                  |
|-------------------|----------|-------------:|---------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| `NO_AUDIO_FILE`   | error    | forces 0     | Health chip; *excluded* count in summary                | `DjmdContent.FolderPath` either empty or `os.path.exists()` returned false.                              |
| `NO_CUES`         | error    | −30          | Issue list; powers the "Fix" buttons                    | Track has zero `DjmdCue` rows with `Kind ∈ [1..8]`.                                                      |
| `NO_PHRASE`       | info     | −10          | Issue list; included in `no_phrase` count               | `AnalysisDataPath` empty. Suggested fix: re-analyze in Rekordbox.                                        |
| `NO_BEATGRID`     | info     | −10          | Issue list; included in `no_beatgrid` count             | `BPM` missing or `0`. Suggested fix: re-analyze in Rekordbox.                                            |
| `DUPLICATE_CUE`   | warning  | −5           | Issue list; included in `duplicate_cues` count          | Two hot cues within `< 2` `InFrame` (≈ 13 ms). At most one penalty per track.                            |
| `UNNAMED_CUES`    | info     | −5           | Issue list; included in `unnamed_cues` count            | Any hot cue's `Comment` is empty/whitespace or matches `^Cue\s*\d+$` (case-insensitive).                 |
| `NO_MEMORY_CUE`   | info     | **0**        | Issue list (when shown); included in `no_memory_cue`    | No `DjmdCue` row with `Kind == 0`. CDJ Auto Cue feature won't land at a cue point. Info only.            |
| `INTERNAL_ERROR`  | error    | forces 0     | Score `0`, `fix_tier="none"`                            | Per-track exception caught by `check_library_health()` — the row keeps streaming, the scan doesn't abort. |

---

## Issue severity classification

The matrix below pins every issue code to its trigger, score impact, and how it
propagates into the [fix tier](#3-fix-tiers-in-detail). Severities are taken
from the literal strings passed to `CueIssue(...)` in `quality.py` — they are
**not** declared as a separate enum, they are positional `Literal["error",
"warning", "info"]` arguments to the `CueIssue` dataclass
(`quality.py:22, 26-30`).

| Issue code | Severity | Trigger criterion | Score impact | Fix tier propagation |
|---|---|---|---|---|
| `NO_AUDIO_FILE` | `error` | `_resolve_audio_path(content)` returns `""` **or** `os.path.exists()` returns `False` (`quality.py:71-76`) | Forces `score = 0`, function returns immediately — `DjmdCue` is never queried, no other issue can fire. | Forces `fix_tier = "none"` (`_fix_tier` short-circuits when `audio_exists` is `False`, `quality.py:54-56`). |
| `NO_CUES` | `error` | Zero rows in `DjmdCue` with `1 ≤ Kind ≤ 8` for this `ContentID` (`quality.py:88, 101-103`). | `−30`. | No direct effect — but `NO_CUES` is the issue that the per-tier "Fix" buttons in the UI act on, dispatched to whatever tier `_fix_tier` decided independently. |
| `NO_PHRASE` | `info` | `DjmdContent.AnalysisDataPath` is empty / `None` (`quality.py:82, 94-96`). | `−10`. | Drops `fix_tier` from `phrase` to `bar` (since the `has_phrase and has_beatgrid` branch in `_fix_tier` requires `AnalysisDataPath`). Caps the achievable tier at `bar` (or `heuristic`, if also no beat grid). |
| `NO_BEATGRID` | `info` | `DjmdContent.BPM` is `None`, `0`, falsy, or fails `float(bpm) > 0` (`quality.py:83-84, 97-99`). | `−10`. | Forces `fix_tier = "heuristic"` (when `has_beatgrid` is `False`, `_fix_tier` returns `"heuristic"` regardless of `has_phrase`, `quality.py:57-61`). |
| `DUPLICATE_CUE` | `warning` | Any two hot cues with `InFrame` values differing by `< _DUPLICATE_FRAMES` (= 2, ≈ 13 ms) after sorting (`quality.py:107-113`). At most one penalty per track. | `−5`. | No tier change. |
| `UNNAMED_CUES` | `info` | Any hot cue's `Comment` is empty / whitespace-only **or** matches `^Cue\s*\d+$` (case-insensitive, `_UNNAMED_RE`, `quality.py:20, 116-120`). | `−5`. | No tier change. |
| `NO_MEMORY_CUE` | `info` | Zero rows in `DjmdCue` with `Kind == 0` for this `ContentID` (`quality.py:89, 123-125`). | `0` — info-only, deduction is deliberately omitted. | No tier change. |
| `INTERNAL_ERROR` | `error` | An unhandled exception was raised inside `check_track_health()` while iterating in `check_library_health()` (`quality.py:162-171`). Also emitted by `/api/tracks/{id}/health` when the single-track handler catches an exception (`routes.py:835-843`). | Forces `score = 0` for the affected track. | Forces `fix_tier = "none"`. |

### Severity is implicit, not enforced

The `severity` field is a free-form string typed `Literal["error", "warning",
"info"]` (`quality.py:22, 29`) and is only used downstream by the UI to pick an
icon and color (`#health-section` renderer in `docs/index.html`). There is
**no validation** that a given `code` always uses the same severity — if a
future revision wires up a different severity for `NO_CUES`, the type checker
won't catch it and the UI will just render whatever severity arrives.

**Recommendation for a future revision**: promote the issue code → severity
mapping to a single source of truth. Either (a) replace the loose `code: str`
field with an enum where the severity is a property of the enum member, or
(b) keep `code: str` but pull severities from a module-level
`_SEVERITY_BY_CODE: dict[str, Severity]` so `CueIssue(...)` constructors only
pass the code and the dataclass resolves the severity. The current arrangement
is fine for the eight known codes, but it makes adding a ninth (or renaming
one) a small refactor instead of a one-liner.

---

## 7. `GET /api/tracks/{id}/health`

Synchronous endpoint that returns the report for a single track.

**Path parameter**: `track_id` — integer matching `DjmdContent.ID`.

**Errors**:
- `404` — track not found.
- The endpoint **never throws on internal failures**: it catches `Exception`
  and returns a synthetic `INTERNAL_ERROR` report with `score=0`
  (`routes.py:835-843`).

**Response** (`TrackHealthReport`, `schemas.py:228-234`):

```json
{
  "track_id": 12345,
  "score": 80,
  "issues": [
    { "code": "NO_PHRASE",   "severity": "info", "message": "No phrase analysis — re-analyze in Rekordbox" },
    { "code": "NO_BEATGRID", "severity": "info", "message": "No beat grid — re-analyze in Rekordbox" }
  ],
  "fix_tier": "heuristic",
  "hot_cue_count": 4,
  "memory_cue_count": 0
}
```

### Field-by-field

| Field              | Type                          | Notes                                                                                              |
|--------------------|-------------------------------|----------------------------------------------------------------------------------------------------|
| `track_id`         | `int`                         | `DjmdContent.ID`. `-1` if the row was completely unreadable.                                       |
| `score`            | `int` (0–100)                 | Clamped. `0` means either `NO_AUDIO_FILE` or `INTERNAL_ERROR`.                                     |
| `issues`           | `list[CueIssueSchema]`        | Order matches the deduction order in `check_track_health()`.                                       |
| `issues[].code`    | string                        | See **Issue codes**.                                                                                |
| `issues[].severity`| `"error"` / `"warning"` / `"info"` | Drives icon and color in the UI.                                                              |
| `issues[].message` | string                        | Human-readable; safe to render directly.                                                            |
| `fix_tier`         | `"phrase"` / `"bar"` / `"heuristic"` / `"none"` | Highest-confidence generator strategy available.                                |
| `hot_cue_count`    | `int`                         | Count of `Kind ∈ [1..8]`.                                                                           |
| `memory_cue_count` | `int`                         | Count of `Kind == 0`.                                                                               |

---

## 8. `GET /api/health` — SSE stream

Streams the full-library scan one event at a time, then ends with a summary
event. Designed for large libraries: the client renders results as they arrive
instead of waiting on a multi-second blocking request.

**Method**: `GET` (SSE, so `EventSource` is fine — unlike the POST-bodied
generate-apply stream, which has to use `fetch` + `ReadableStream`).

**Headers**: `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
`X-Accel-Buffering: no` (so the GZip middleware in `serve/app.py` passes the
stream through untouched).

**Query parameters**:

| Parameter      | Type | Default | Effect                                                                          |
|----------------|------|---------|---------------------------------------------------------------------------------|
| `playlist_id`  | int  | `None`  | If supplied, scans only tracks in that playlist (used for incremental rescans). |

If `playlist_id` doesn't match any `DjmdPlaylist`, the endpoint returns `404`
before the stream opens (`routes.py:861-865`).

### Event sequence

1. **Total event** (sent once, before the per-track events):

   ```
   data: {"total": 3241}
   ```

   The scanner pre-counts so the client can render a real progress bar.
   If the count query fails, the field is silently dropped.

2. **One TrackHealthReport per track**, exactly the `/api/tracks/{id}/health`
   response shape (above).

   ```
   data: {"track_id": 12345, "score": 95, "issues": [...], "fix_tier": "phrase", "hot_cue_count": 4, "memory_cue_count": 1}
   ```

3. **Final summary event** (`LibraryHealthSummary`):

   ```
   data: {"done": true, "summary": { ... }}
   ```

### Summary shape (`LibraryHealthSummary`, `schemas.py:237-247`)

```json
{
  "total": 3241,
  "excluded_missing_audio": 47,
  "library_score": 84.6,
  "no_cues": 312,
  "no_phrase": 188,
  "no_beatgrid": 92,
  "duplicate_cues": 4,
  "unnamed_cues": 1183,
  "no_memory_cue": 2810,
  "fix_tier_counts": { "phrase": 2802, "bar": 280, "heuristic": 112, "none": 47 }
}
```

| Field                     | Notes                                                                                             |
|---------------------------|---------------------------------------------------------------------------------------------------|
| `total`                   | All scanned rows, including those that hit `NO_AUDIO_FILE`.                                       |
| `excluded_missing_audio`  | Count of `NO_AUDIO_FILE` short-circuits.                                                          |
| `library_score`           | **Mean of scores for non-missing-audio tracks**, rounded to one decimal. `0.0` if none scannable. |
| `no_cues` … `no_memory_cue` | Per-issue track counts.                                                                         |
| `fix_tier_counts`         | Dict of `{"phrase": N, "bar": N, "heuristic": N, "none": N}`.                                     |

`library_score` deliberately **excludes** missing-audio tracks. If you have
50 broken file paths in a 1,000-track library, the average isn't dragged down
by 50 forced zeros — instead, those 50 are surfaced separately in
`excluded_missing_audio`.

### Per-track exception handling

If any one track raises during analysis, the scan **does not abort**. Inside
`check_library_health()` (`quality.py:162-171`):

```python
for content in contents:
    try:
        yield check_track_health(content, db)
    except Exception as exc:
        yield TrackHealthReport(
            track_id=getattr(content, "ID", -1),
            score=0,
            issues=[CueIssue("INTERNAL_ERROR", "error", str(exc))],
            fix_tier="none",
        )
```

The bad row gets an `INTERNAL_ERROR` report (score `0`, fix tier `none`) and
the loop continues to the next track. The error's `str(exc)` ends up in the
issue's `message`, so debugging stays straightforward without spamming logs.

### `playlist_id` — incremental rescans

After re-analyzing a subset of tracks in Rekordbox (e.g. you just fixed beat
grids on the "House — Friday Set" playlist), pass `?playlist_id=N` to rescan
only that playlist instead of the whole library. The UI hands through the
currently-active playlist automatically:

```js
const url = activePlaylistId
  ? `/api/health?playlist_id=${activePlaylistId}`
  : '/api/health';
```

(`docs/index.html` line ~2747.)

**Caveats**:

- The summary reflects only the scanned subset, not the full library. The
  `library_score` for a 30-track playlist is **not comparable** to the
  library-wide score.
- `DjmdSongPlaylist.PlaylistID` is `VARCHAR(255)`, so the underlying query
  coerces the int to `str` before filtering (`quality.py:156`). Pass the
  integer ID over the wire — the route handles the string coercion.

---

## Performance profile

The Library Health scan is designed to be cheap enough to run on every library
load. The per-track cost is dominated by one `DjmdCue` query and one syscall;
no ANLZ parsing is involved at any point (see also [§4 Pure DB reads — why it
matters](#4-pure-db-reads--why-it-matters)).

### Per-track work

For every track that survives the audio-existence check, `check_track_health()`
does exactly this (`quality.py:64-135`):

| Step | Cost |
|---|---|
| `_resolve_audio_path(content)` — string normalisation only | ~µs |
| `os.path.exists(audio_path)` — one `stat` syscall against the audio file | typically <1 ms on a warm SSD; can spike to tens of ms on a cold cache or network volume |
| `db.query(DjmdCue).filter(DjmdCue.ContentID == track_id).all()` | a few ms — indexed lookup against SQLCipher |
| ~5 attribute reads on the `DjmdContent` row (`AnalysisDataPath`, `BPM`, `FolderPath`, `ID`) | ~µs |
| Score deductions + issue list assembly | ~µs |

For tracks that fail the audio-existence check, the function returns
**immediately** at `quality.py:74-76` — no `DjmdCue` query is issued at all.
That is the cheapest path through the scan.

No analysis-module caches are read or written (`energy._cache`,
`classify._class_cache`, `score._mixability_cache`, `similar._INDEX`). The
health scan is fully decoupled from the rest of the analysis layer.

See the [glossary](./GLOSSARY.md#djmdcontent) for `DjmdContent` / `DjmdCue`
column definitions.

### SSE event rate and payload size

- **One `data:` event per track**, written via `yield f"data: {schema.model_dump_json()}\n\n"`
  (`routes.py:902`). Plus one `{"total": N}` event up front and one final
  `{"done": true, "summary": {...}}` event.
- **Payload per event**: a serialised `TrackHealthReport`. Typical size is
  ~150–400 bytes — `track_id`, `score`, a short `issues[]` (most tracks have
  0–3 entries), `fix_tier`, and the two cue counts. Worst-case (all eight
  codes fire) is still under ~1 KB.
- **No batching**: the stream commits one event per iteration of the
  generator. The Starlette `StreamingResponse` plus the `X-Accel-Buffering: no`
  header in [§8](#8-get-apihealth--sse-stream) lets each event reach the
  client immediately, so the JS UI can update its progress bar in real time.
- **Bypasses GZip**: the `GZipMiddleware(minimum_size=1000)` in
  `serve/app.py` skips `text/event-stream` responses, so events flush as
  generated rather than buffering for a compression window.

### Bottleneck on large libraries

The single biggest factor in scan duration is the
`os.path.exists(audio_path)` call inside `check_track_health()`
(`quality.py:73`). It runs once per track — fast on a warm OS page cache, but
on a cold cache against a library on an external drive or network volume,
those `stat` syscalls dominate the runtime. Everything else — the `DjmdCue`
query, the issue list, the JSON serialisation — is in-process work that
finishes in single-digit milliseconds per track.

### Rough throughput

On a typical SSD with a warm OS cache, the scan moves at roughly **a few
hundred tracks per second** end-to-end (i.e. a 3,000-track library scans in
roughly 5–15 seconds). The number is dominated by the per-track `stat` syscall
plus a single indexed `DjmdCue` lookup; anything that slows those down — cold
cache, external drive, network volume, SQLCipher under load — moves throughput
toward the low end of that range. The CI machines and a developer SSD differ
enough that nailing the number to one figure would be misleading. If you need
a precise figure for your environment, the SSE stream's per-event timestamps
make ad-hoc measurement trivial.

### Memory profile

- **`check_library_health()` is a generator** (`quality.py:138-171`). It
  yields one `TrackHealthReport` at a time and never accumulates the whole
  scan in memory. A 10K-track scan and a 10-track scan use the same per-track
  working set.
- The route handler in `routes.py:867-917` keeps three running collectors:
  `scores: list[int]`, `missing_audio: list[int]`, and two
  `defaultdict(int)`s for issue and tier counts. These are **summary state**
  only — O(tracks) integers total — and are needed to build the final
  `LibraryHealthSummary`. No `TrackHealthReport` objects are retained past
  the iteration that produced them.
- The **JS UI** in `docs/index.html` does accumulate every event into
  `healthData` (keyed by `String(track_id)`) so it can re-render per-track
  chips and group fix-tier buckets after the scan completes. That is the only
  O(tracks) memory growth in the pipeline, and it lives on the client.

### Per-track exception isolation

One bad row never aborts the scan. The `try` / `except Exception` in
`check_library_health()` (`quality.py:162-171`) catches and re-yields a
synthetic `INTERNAL_ERROR` report (score `0`, `fix_tier="none"`), and the
loop continues to the next track. The error's `str(exc)` lands in the issue's
`message` for debugging. The same pattern guards the single-track endpoint
at `routes.py:835-843`.

### Scoping via `?playlist_id=N` for incremental rescans

If you only need to refresh a subset of the library (e.g. you just
re-analyzed one playlist in Rekordbox), pass `?playlist_id=N` to `/api/health`.
The route narrows the underlying `DjmdContent` query via a join on
`DjmdSongPlaylist` (`quality.py:151-158`), the total event reflects only the
scoped count (`routes.py:878-886`), and the summary is computed over the
filtered set. The route also pre-validates the playlist ID and returns `404`
before the stream opens if it does not exist (`routes.py:861-865`). This is
the cheapest way to keep the report current without re-stat'ing every audio
file in the library on every scan.

---

## 9. UI — Library Health Report

The Library Health panel lives in `docs/index.html` (the `#health-section`
element, around line 1849) and only renders when AutoCue is connected to a
running server (`localMode` is `true`). The corresponding JS lives at
~line 2729 (`scanLibraryHealth`).

### What the user sees

1. **A "Scan" button** at the top of the panel. Clicking it streams
   `/api/health` (passing `?playlist_id=N` if a playlist is currently active).
2. **A live progress bar** that updates per event. If the total count was
   delivered, the bar shows real progress (`processed / total`); otherwise it
   creeps asymptotically toward 97%.
3. **A score ring** with a 0–100 number:
   - Green (`hsr-good`) when score ≥ 90.
   - Orange (`hsr-ok`) when 70 ≤ score < 90.
   - Red (`hsr-bad`) when score < 70.
   - Muted "—" when no tracks were scannable.
4. **Per-issue rows**, each with an icon (`✗` for errors, `⚠` for warnings,
   `ℹ` for info), a count, a label, and an optional fix-note ("Re-analyze
   in Rekordbox"). The row for `no_memory_cue` is `infoOnly` — it shows up,
   but doesn't block the "No issues — library looks great" message.
5. **Per-tier fix buttons** ([see below](#per-tier-fix-buttons)).
6. **Health chips on every track card** in the library view — a tiny badge
   next to the track name showing the score, color-coded the same way as the
   ring. Hovering reveals the issue list as a tooltip. The chip render lives
   at `docs/index.html` ~line 5556.

### Per-tier fix buttons

Once the scan finishes, the UI walks `healthData` and groups every track with
a `NO_CUES` issue by `fix_tier`. Two buttons are rendered (one or both may be
absent if the bucket is empty):

| Button                                          | Tracks included                              | Confirmation? |
|-------------------------------------------------|----------------------------------------------|---------------|
| **"Fix phrase-quality tracks (N)"** — primary   | `fix_tier == "phrase"`                       | None          |
| **"Fix remaining (M — bar/heuristic quality)"** — secondary | `fix_tier ∈ {"bar", "heuristic"}` | Required (`confirm()`) |

The split is deliberate: phrase-quality fixes are high confidence (cues land on
real phrase boundaries detected by Rekordbox), so the UI runs them with no
confirmation. Bar / heuristic fixes can land on arbitrary positions — the
user explicitly opts in.

Both buttons call `_applyHealthFix(trackIds, needsConfirm)`, which POSTs to
`/api/generate-apply-stream` with `mode: "auto"` and the current
bars-interval / start-bar / max-cues / memory-cue-mode settings from the
top-of-page controls. After the apply finishes, the panel **automatically
re-scans** by calling `scanLibraryHealth()` again, so the report reflects the
fixes immediately.

### Concurrency guard

A module-level `_healthFixInProgress` flag prevents overlapping fix runs. If
the user clicks both buttons in quick succession, the second one shows a toast
("A fix is already in progress — please wait") instead of issuing a duplicate
POST. The flag is set **after** DOM reads so a null-dereference can't leave it
permanently stuck (`docs/index.html` ~line 2922).

### Cancellation

The scan itself is cancellable: the button toggles to "Cancel" while
streaming, and clicking it calls `abortCtrl.abort()` → `reader.cancel()`. On
abort, the UI shows a toast with how many tracks were scanned and renders the
partial results anyway, so a cancelled scan still updates the per-track chips.

The fix run is **not** cancellable mid-flight (it commits per-track in the
server route, and the UI keeps the Apply button disabled until the stream
finishes).

---

## 10. Cue Library Tools — `POST /api/cue-tools-stream`

Sister feature to Library Health. Bulk-edits cues across whatever set of
tracks the user is looking at in the UI. Defined at `routes.py:930-1110`.

The four operations:

| `operation`       | Effect                                                                                          | Required params                                            |
|-------------------|-------------------------------------------------------------------------------------------------|------------------------------------------------------------|
| `rename`          | Renames every hot cue whose `Comment` is an exact, case-sensitive match for `from_name`.        | `rename: { from_name, to_name }`                           |
| `recolor`         | Sets `ColorTableIndex` per slot. Slot keys are stringified ints `"0".."7"` (slot A is `"0"`).   | `recolor: { slot_colors: { "0": 5, "1": 7, ... } }`        |
| `shift`           | Adds `delta_ms` to every hot cue's `InMsec` / `InFrame`. Updates `OutMsec` / `OutFrame` on loops to preserve length. | `shift: { delta_ms, negative_policy }`                     |
| `delete_orphan`   | Deletes every hot cue whose `Kind > keep_slots`. (Slot A is `Kind=1`, slot H is `Kind=8`.)      | `delete_orphan: { keep_slots: 1..8 }`                      |

Memory cues (`Kind == 0`) are **always excluded** from every operation
(see `routes.py:986-991` — the query filter is `Kind >= 1, Kind <= 8`).

### Request shape (`CueToolsRequest`, `schemas.py:286-301`)

```json
{
  "operation": "rename",
  "track_ids": [101, 102, 103],
  "dry_run": false,
  "rename": { "from_name": "Cue 1", "to_name": "Intro" }
}
```

`@model_validator` enforces that the params block matching `operation` is
present (e.g. `rename` requires `rename`). Missing → `422`.

### Backup behaviour

If `dry_run == false`, the endpoint **takes a `master.db` backup before any
writes** (`routes.py:964-976`). The backup filename is returned in the summary
as `backup_path`. If the backup itself fails, the endpoint returns `500` and
no writes happen. There is **no skipping the backup** — every non-dry-run
goes through it.

If `dry_run == true`, no backup is taken (nothing is being written to back up).

### Dry-run default

The Cue Library Tools panel ships with the **Dry run** checkbox checked
(`docs/index.html` line 1887). The user has to actively uncheck it to commit
changes. This matches the rest of AutoCue: destructive ops default to safe.

### Destructive-op confirmation

Even with dry-run unchecked, the UI **requires explicit confirmation** before
running `shift` or `delete_orphan`:

```js
if (!dryRun && (op === 'delete_orphan' || op === 'shift')) {
  if (!window.confirm(`Apply ${opLabel} to ${total} track(s)? A backup will be created first.`)) return;
}
```

(`docs/index.html` ~line 3044.) `rename` and `recolor` don't prompt — they're
trivially reversible by re-running the tool with swapped values or reverting
from the backup.

### Rekordbox-running guard

If Rekordbox is open and `dry_run == false`, the endpoint returns `409 — close
it before editing cues` (`routes.py:942-943`). Dry-runs are allowed while
Rekordbox is open because they don't write.

### Event stream

Per-track events every 50 tracks (the `BATCH` constant):

```
data: {"processed": 50, "affected": 31, "total": 200}
```

Final summary event:

```
data: {
  "done": true,
  "summary": {
    "operation": "rename",
    "tracks_processed": 200,
    "tracks_affected": 31,
    "cues_changed": 47,
    "cues_skipped": 158,
    "skip_reasons": { "no_match": 158 },
    "dry_run": false,
    "backup_path": "master_2026-06-07_142315.db"
  }
}
```

### Skip reasons

`CueToolsSummary.skip_reasons` uses stable keys:

| Key                  | Set by                                              | Meaning                                                                                |
|----------------------|-----------------------------------------------------|----------------------------------------------------------------------------------------|
| `no_match`           | `rename`, `recolor`                                 | Cue's `Comment` didn't match `from_name`, or its slot wasn't in `slot_colors`.         |
| `would_be_negative`  | `shift` with `negative_policy == "skip"`            | The cue would have moved to a negative position; it was left in place.                 |
| `track_aborted`      | `shift` with `negative_policy == "abort_track"`     | At least one cue on the track would have gone negative; **all** cues on the track were left untouched (preserves internal consistency of the cue set). |
| `beyond_keep_slots`  | `delete_orphan`                                     | Cue was in a slot ≤ `keep_slots` and so was kept (counted as "skipped from deletion"). |

### Shift `negative_policy` options

Defined on `CueShiftParams` (`schemas.py:266-273`):

| Policy             | Behaviour                                                                                                |
|--------------------|----------------------------------------------------------------------------------------------------------|
| `abort_track`      | **Default.** If any cue on the track would go negative, leave the entire track untouched.                |
| `skip`             | Silently drop the cues that would go negative; shift the rest.                                           |
| `clamp_to_zero`    | Place cues that would go negative at `0 ms` instead.                                                     |

`delta_ms == 0` is rejected by a validator (`schemas.py:274-279`) — a
zero-shift is presumed to be a UI mistake.

### Atomicity

The endpoint commits **once per request**, at the end (`routes.py:1087-1088`).
If the SSE stream is cancelled mid-flight (client disconnect raises
`GeneratorExit` → caught by `BaseException`), `db.session.rollback()` fires
and the database is left in its pre-request state — even though many tracks
appear as `processed` in earlier events. This is intentional: the user
expects "cancel" to mean "nothing changed".

If the backup succeeded but the commit was rolled back, the backup file
remains on disk. It's still a valid restore point — it just records the same
state the DB is already in.

---

## 11. Performance

### `has_phrase` and the fast path

The track list endpoint (`/api/tracks`) reports `has_phrase` per track via a
fast file-existence probe: `db.get_anlz_path(content, "EXT")`. This **does
not parse the ANLZ file** — it just resolves the path and checks if it exists
on disk. The check is intentionally separate from the score calculation:

- **`/api/tracks` `has_phrase`**: fast file-existence check on `.EXT`.
- **`/api/health` `NO_PHRASE` issue**: presence of `AnalysisDataPath` string.

These can diverge in pathological cases (ANLZ deleted while the DB still
references it). The intentional bias is towards speed — the health scan must
finish in seconds, not minutes.

### Per-track cost

- 1 `DjmdCue` query (filtered by `ContentID`).
- 1 `os.path.exists()` call against `FolderPath`.
- ~5 attribute reads on the `DjmdContent` row.

No analysis-module caches are consulted (`energy._cache`,
`classify._class_cache`, `score._mixability_cache`, `similar._INDEX`). The
health scan is fully decoupled from the rest of the analysis layer.

### Stream backpressure

The endpoint yields one event per track and doesn't buffer the whole scan in
memory. A 10K-track library scan uses roughly the same memory footprint as a
10-track scan.

---

## 12. Edge cases & behavioural notes

### `FolderPath` stores the **complete file path**, not just the folder

Despite the name, `DjmdContent.FolderPath` holds the full path to the audio
file (`/Users/dj/Music/track.mp3`), **not** just the directory. The
`os.path.exists()` check works on it directly. `_resolve_audio_path()` also
handles two macOS-specific quirks:

- A leading `/:` prefix (some macOS Rekordbox versions add this) is stripped.
- Paths without a leading `/` get one prepended.

See `quality.py:43-51` and the parameterized tests in
`tests/test_quality.py::TestResolveAudioPath`.

### `NO_AUDIO_FILE` short-circuit

When the audio file is missing, the function `return`s immediately
(`quality.py:74-76`). It does **not** query `DjmdCue` for the track —
verified by `tests/test_quality.py::TestNoAudioFile::test_missing_audio_skips_cue_queries`.
This shaves DB calls on bulk scans against libraries with broken paths.

`hot_cue_count` and `memory_cue_count` are left at their dataclass defaults of
`0`. The UI must check the `NO_AUDIO_FILE` issue code before treating those
zeros as meaningful — otherwise a broken-path track looks identical to one
that's simply uncued.

### `DjmdCue` rows with `None` `InFrame` or empty `Comment`

- `InFrame is None` → coerced to `0` for the duplicate check. This is correct:
  two `None`-positioned cues are real duplicates.
- `Comment is None` → coerced to `""`, which triggers `UNNAMED_CUES`.
- `Kind is None` → coerced to `0`, which buckets the row as a memory cue.

The implementation uses `getattr(c, "Comment", None) or ""` and
`int(c.Kind or 0)` everywhere, so the function does not crash on partial rows.

### Memory cues — info only

`NO_MEMORY_CUE` is **intentionally zero score impact** (`quality.py:122-125`).
DJs who don't use the CDJ Auto Cue feature would otherwise get a permanent
−5 deduction on every track. The issue is still emitted (info severity) so
DJs who *do* care can see which tracks lack one.

The UI's "No issues — library looks great" message ignores `no_memory_cue`
(it's tagged `infoOnly: true` in `_renderHealthSummary`).

### Exception inside `/api/tracks/{id}/health`

The single-track endpoint also catches all exceptions and returns an
`INTERNAL_ERROR` synthetic report (`routes.py:837-843`). The client never
gets a 500 from a per-track failure — only from missing-track (404).

### Scan with no playlist filter — what's the `total`?

`db.query(DjmdContent).count()`. If the count query itself fails, the
`total` event is skipped (the UI falls back to its asymptotic progress
approximation) and the scan continues normally.

---

## 13. Examples

### A healthy track

```json
{
  "track_id": 12345,
  "score": 100,
  "issues": [],
  "fix_tier": "phrase",
  "hot_cue_count": 8,
  "memory_cue_count": 1
}
```

### A track Rekordbox has analyzed but never cued

```json
{
  "track_id": 12346,
  "score": 70,
  "issues": [
    { "code": "NO_CUES",        "severity": "error", "message": "No hot cues" },
    { "code": "NO_MEMORY_CUE",  "severity": "info",  "message": "No memory cue — CDJ Auto Cue won't load at a cue point" }
  ],
  "fix_tier": "phrase",
  "hot_cue_count": 0,
  "memory_cue_count": 0
}
```

This is the most common "needs fixing" track. AutoCue's **phrase**-mode
generator can fill all 8 hot cue slots from the phrase analysis with no
user input.

### A track whose audio file moved

```json
{
  "track_id": 12347,
  "score": 0,
  "issues": [
    { "code": "NO_AUDIO_FILE", "severity": "error", "message": "Audio file not found on disk" }
  ],
  "fix_tier": "none",
  "hot_cue_count": 0,
  "memory_cue_count": 0
}
```

`hot_cue_count` is `0` not because the track is uncued — but because the
scanner short-circuited and never asked.

### A pre-CDJ-era import with no analysis

```json
{
  "track_id": 12348,
  "score": 50,
  "issues": [
    { "code": "NO_PHRASE",     "severity": "info",  "message": "No phrase analysis — re-analyze in Rekordbox" },
    { "code": "NO_BEATGRID",   "severity": "info",  "message": "No beat grid — re-analyze in Rekordbox" },
    { "code": "NO_CUES",       "severity": "error", "message": "No hot cues" },
    { "code": "NO_MEMORY_CUE", "severity": "info",  "message": "No memory cue — CDJ Auto Cue won't load at a cue point" }
  ],
  "fix_tier": "heuristic",
  "hot_cue_count": 0,
  "memory_cue_count": 0
}
```

Heuristic-tier fix: AutoCue divides the track into evenly-spaced cues. The
DJ should re-analyze in Rekordbox first if possible, then re-scan.

### A summary after a 3,241-track scan

```json
{
  "done": true,
  "summary": {
    "total": 3241,
    "excluded_missing_audio": 47,
    "library_score": 84.6,
    "no_cues": 312,
    "no_phrase": 188,
    "no_beatgrid": 92,
    "duplicate_cues": 4,
    "unnamed_cues": 1183,
    "no_memory_cue": 2810,
    "fix_tier_counts": {
      "phrase": 2802,
      "bar":    280,
      "heuristic": 112,
      "none":     47
    }
  }
}
```

What the UI does with this:

- Renders **84.6 → 85** in the score ring (orange — between 70 and 90).
- Lists **312 tracks have no hot cues** (red), **47 tracks — audio file
  missing** (red), **4 tracks have duplicate cue positions** (warning),
  **188 tracks have no phrase analysis** (info, with the "Re-analyze in
  Rekordbox" note), etc.
- Renders two fix buttons:
  - **"Fix phrase-quality tracks (≈ 260)"** — primary, no confirm. Counts
    the subset of the 312 `NO_CUES` tracks whose `fix_tier == "phrase"`.
  - **"Fix remaining (≈ 52 — bar/heuristic quality)"** — secondary, with
    confirm.

(The exact split isn't in the summary — the UI computes it client-side by
walking `healthData`, which is keyed by `String(track_id)`.)

---

## 14. Testing

`tests/test_quality.py` has **47 tests**. Grouped:

| Group                       | Tests | What it pins                                                                                      |
|-----------------------------|------:|---------------------------------------------------------------------------------------------------|
| `TestResolveAudioPath`      | 4     | Plain paths pass through; `/:` prefix stripped; missing leading slash added; empty string handled. |
| `TestFixTier`               | 5     | Truth table: `(has_phrase, has_beatgrid, audio_exists)` → tier.                                   |
| `TestNoAudioFile`           | 4     | Forced `score=0`, `fix_tier="none"`, `NO_AUDIO_FILE` present, **`DjmdCue` not queried**.          |
| `TestHealthyTrack`          | 4     | Two named cues + analysis + BPM → `score=100`, no errors, `fix_tier="phrase"`.                    |
| `TestNoCues`                | 3     | `NO_CUES` → −30; severity `error`; issue code present.                                            |
| `TestMissingAnalysis`       | 6     | `NO_PHRASE` → −10; `NO_BEATGRID` → −10; both → −20; fix tier degrades to `bar` then `heuristic`.   |
| `TestDuplicateCue`          | 6     | Same frame, 1 frame, 2 frames boundary (not duplicate), 100 frames; 3 cues at same frame → still one −5. |
| `TestUnnamedCues`           | 7     | Empty, whitespace, `Cue 3`, `cue3` (case-insensitive); `Drop` and `Verse 1` don't trigger.        |
| `TestMemoryCue`             | 3     | `NO_MEMORY_CUE` is info-only; doesn't affect score; memory cue count tracked.                     |
| `TestScoreClamping`         | 2     | Score never below 0 or above 100.                                                                  |
| `TestLibraryHealth`         | 3     | Yields one report per track; per-track exception → `INTERNAL_ERROR`; `playlist_id` filters query. |

The autouse fixture in `tests/conftest.py` clears the energy, classify, score,
and similarity caches before every test — even though the quality module
doesn't use any of them — for consistency with the rest of the analysis
suite.

The conventions used by the tests are also a good reference for adversarial
edge cases:

> *"Duplicate threshold is 10ms — 9ms apart = duplicate, 10ms apart = not."*
>
> *"UNNAMED_CUES catches empty string AND "Cue 3" / "cue3" patterns; NOT
>   "Drop" or "Chorus"."*
>
> *"Memory cue absence is info-only; score must stay unchanged."*
>
> *"Per-track exception in check_library_health must yield error report, not
>   propagate."*

(Inline comments at the top of `tests/test_quality.py`.)

Route-level tests for `/api/tracks/{id}/health`, `/api/health`, and
`/api/cue-tools-stream` are in `tests/test_serve_routes.py` (194 tests
across the full FastAPI surface).

---

## 15. Related reference

- [`GLOSSARY.md`](./GLOSSARY.md) — definitions for `DjmdContent`, `DjmdCue`,
  ANLZ `.EXT` / `.DAT`, `Kind`, `InFrame`, Camelot key wheel, etc. Linked
  throughout this doc.
- `cue-generation.md` — phrase / bar / heuristic generators, which is what the
  per-tier fix buttons actually call (via `/api/generate-apply-stream`).
- `cue-library-tools.md` — full reference for the `cue-tools-stream`
  operations: rename, recolor, shift, delete-orphan.
- `rest-api.md` — full REST and SSE endpoint catalogue.

Source files cited in this doc:

- `autocue/analysis/quality.py` — score formula, fix tier, library generator.
- `autocue/serve/routes.py` — `/api/tracks/{id}/health` (line 828),
  `/api/health` (line 846), `/api/cue-tools-stream` (line 930).
- `autocue/serve/schemas.py` — `CueIssueSchema`, `TrackHealthReport`,
  `LibraryHealthSummary`, `CueToolsRequest`, `CueToolsSummary`.
- `tests/test_quality.py` — behavioural pins.
- `docs/index.html` — `#health-section` (line 1849), `scanLibraryHealth`
  (line 2731), `_renderHealthSummary` (line 2829), `_applyHealthFix`
  (line 2915), `#cue-tools-section` (line 1872), `_runCueTools` (line 3029).

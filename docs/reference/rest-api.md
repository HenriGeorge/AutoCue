# REST API Reference

AutoCue's local server exposes a JSON+SSE REST API at `http://localhost:7432`. This
document covers every endpoint, every request and response schema, status codes,
SSE event formats, middleware, and the behavioural rules baked into the
implementation. Source-of-truth file refs are inline (e.g.
`routes.py:323` → `autocue/serve/routes.py` line 323).

> **Scope.** This is the *reference* doc — exhaustive, schema-first, and stable
> against the code. For end-user feature explanations see the deep-dive docs
> linked under each section ([cue-generation.md](./cue-generation.md),
> [track-classification.md](./track-classification.md), etc.).

---

## 1. Overview

| Property            | Value                                                                |
| ------------------- | -------------------------------------------------------------------- |
| Base URL            | `http://localhost:7432`                                              |
| API prefix          | `/api`                                                               |
| Default port        | `7432` (auto-increments up to `7441` if busy — see `app.py:71`)      |
| Bind address        | `127.0.0.1` (loopback only — never `0.0.0.0`)                        |
| Content type        | `application/json` for sync responses, `text/event-stream` for SSE   |
| Web UI              | Mounted on `/` from `docs/` (`StaticFiles`, `app.py:60`)              |
| Server framework    | FastAPI + uvicorn                                                    |
| Lifespan-managed DB | Yes — `Rekordbox6Database` opened at startup (`deps.py:58`)          |
| Read-only handle    | Yes — second connection with `PRAGMA query_only=ON` (`deps.py:64`)   |

The server is intentionally **local-only**. It writes directly to the user's
Rekordbox `master.db`; exposing it to a network would be a credentials-stealing
hazard. CORS is locked down to `null` (file://), `http://localhost:{port}`, and
`http://127.0.0.1:{port}` (`app.py:45-49`). Do not widen this list.

### Static UI

When `docs/` exists next to the install, the app mounts it on `/`:

```python
# autocue/serve/app.py:59-60
if DOCS_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DOCS_DIR), html=True), name="ui")
```

This is why visiting `http://localhost:7432/` returns `docs/index.html` — the
exact same single-file web app that ships on GitHub Pages, automatically
switched into "local mode" by detecting `/api/status` on load.

### Port auto-detection

If `7432` is taken, the launcher does two things (`app.py:64`):

1. Calls `GET /api/status` against the existing process. If it returns 200,
   "AutoCue is already running" — open the browser and exit.
2. Otherwise, scan ports `7433 … 7441` and bind the first free one.

---

## 2. Authentication

**None.** The server requires no authentication.

- It binds to `127.0.0.1` only.
- CORS only allows `null` and `localhost:{port}` origins.
- Local processes share the user's filesystem permissions, so any process that
  can read the Rekordbox DB can already write to it directly.

Discogs/Discovery endpoints require a **Discogs personal access token** (not an
AutoCue credential). It is supplied per-request in the body or, for SSE
endpoints, as a query param; it can also be sourced from a project-root `.env`
file or the `DISCOGS_TOKEN` environment variable (`routes.py:1885-1900`).

---

## 3. Database connection

Two DB handles live on `app.state`:

| Handle               | Dependency       | Use                                          | PRAGMA                |
| -------------------- | ---------------- | -------------------------------------------- | --------------------- |
| `app.state.db`       | `get_db`         | Write endpoints (`/apply`, `/auto-tag`, …)   | normal RW             |
| `app.state.ro_db`    | `get_ro_db`      | Read endpoints (`/tracks`, `/health`, …)     | `query_only=ON`       |

The read-only handle is created at startup with a SQLAlchemy `connect` event
that issues `PRAGMA query_only=ON` on every new connection
(`deps.py:36-43`). If the RO handle fails to open, `get_ro_db` falls back to
the shared RW handle (`deps.py:28-33`).

Both handles return **503 Service Unavailable** if the DB never opened (e.g.
no Rekordbox installation found, locked file). See `deps.py:13-33`.

### Rekordbox-running guard (409)

Every write endpoint checks `rekordbox_is_running()` (a psutil scan in
`autocue/db_writer.py`) and returns **HTTP 409 Conflict** when Rekordbox is
open. SQLCipher-locks the DB while running, so writing would either fail or
corrupt the library.

The guard fires on:

| Endpoint                              | Guarded                |
| ------------------------------------- | ---------------------- |
| `POST /api/apply`                     | always                 |
| `POST /api/generate-apply`            | always                 |
| `POST /api/generate-apply-stream`     | always                 |
| `POST /api/delete-cues`               | always                 |
| `POST /api/color-tracks`              | only when `dry_run=False` |
| `POST /api/color-tracks-stream`       | only when `dry_run=False` |
| `POST /api/cue-tools-stream`          | only when `dry_run=False` |
| `POST /api/auto-tag`                  | only when `dry_run=False` |
| `POST /api/auto-tag/undo`             | always                 |
| `POST /api/auto-tag/discogs`          | only when `dry_run=False` |
| `POST /api/enrich-comments`           | only when `dry_run=False` |
| `POST /api/enrich-comments/stream`    | only when `dry_run=False` |
| `POST /api/restore`                   | always                 |
| `POST /api/playlists` (create)        | always                 |

`GET` endpoints never check the guard — they share the read-only handle.

---

## 4. SSE conventions

Several endpoints stream Server-Sent Events instead of a single JSON response.

### Wire format

```
data: {"processed": 1, "total": 100, "applied": 1, "skipped": 0}\n\n
data: {"processed": 2, "total": 100, "applied": 2, "skipped": 0}\n\n
...
data: {"done": true, "applied": 100, "skipped": 0, "backup_path": "/..."}\n\n
```

- Content type: `text/event-stream`.
- Every event is a single `data: <json>` line terminated by **two** newlines.
- The last event always has `"done": true` and carries a summary payload.
- Both per-track events and the final event use the same `data: {...}` shape —
  there are no SSE `event:` types in this API.

### Required headers

All SSE endpoints set:

```python
headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
```

`X-Accel-Buffering: no` is the de-facto signal to disable proxy buffering. It
also (combined with the `text/event-stream` content type) causes
`GZipMiddleware` to skip compression — gzip would block partial chunks until a
buffer fills, defeating progressive streaming (`app.py:50`).

### POST-based SSE in JS

Because some endpoints are POST (request body needed), they cannot be consumed
with `EventSource`. The web app uses a shared `_consumeSSE(response, onEvent)`
helper built on `fetch` + `ReadableStream` (`docs/index.html`).

```js
const resp = await fetch("/api/generate-apply-stream", {method: "POST", ...});
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buf = "";
while (true) {
  const {done, value} = await reader.read();
  if (done) break;
  buf += decoder.decode(value, {stream: true});
  for (const line of buf.split("\n\n").slice(0, -1)) {
    if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
  }
  buf = buf.split("\n\n").pop();
}
```

### Client disconnect

`/api/color-tracks-stream` and `/api/cue-tools-stream` wrap the per-track loop
in `try/except BaseException` to catch `GeneratorExit` raised on client
disconnect, then `db.session.rollback()` before re-raising. This guarantees a
disconnected client never leaves the session in a dirty state
(`routes.py:762-764`, `routes.py:1090-1092`).

---

## 5. Status and system endpoints

### GET `/api/status`

System health probe. Used by the UI to detect local-mode and by the launcher
to detect a running instance (`routes.py:74`).

**Response — 200**

```json
{
  "connected": true,
  "rekordbox_version": null,
  "track_count": 3127
}
```

| Field                | Type           | Meaning                                                |
| -------------------- | -------------- | ------------------------------------------------------ |
| `connected`          | bool           | DB handle is non-null                                  |
| `rekordbox_version`  | string \| null | Reserved (always `null` today)                         |
| `track_count`        | int            | `db.get_content().count()` — total `DjmdContent` rows  |

**503** — DB never connected at startup.

---

### GET `/api/config`

Returns non-sensitive client config (`routes.py:1577`). Today, just the
Discogs token sourced from `.env` then `os.environ`.

**Response — 200**

```json
{ "discogs_token": "abc123..." }
```

The `.env` reader is line-oriented; only the first `DISCOGS_TOKEN=` line wins.
`os.environ["DISCOGS_TOKEN"]` overrides the file.

---

### GET `/api/backups`

List `.db` backup files in the AutoCue backup directory, sorted newest first
(`routes.py:526`). The filename embeds a UTC timestamp (`master_YYYYMMDDTHHMMSS.db`),
which is parsed for a clean `created_at`; non-matching names fall back to mtime.

**Response — 200** — `list[BackupItem]`

| Field         | Type    | Meaning                              |
| ------------- | ------- | ------------------------------------ |
| `path`        | string  | Full absolute path                   |
| `filename`    | string  | Base filename                        |
| `size_mb`     | float   | File size, rounded to 2 decimals     |
| `created_at`  | string  | `YYYY-MM-DD HH:MM:SS` (UTC)          |

```json
[
  {"path": "/.../master_20260607T142233.db", "filename": "master_20260607T142233.db", "size_mb": 12.4, "created_at": "2026-06-07 14:22:33"},
  {"path": "/.../master_20260606T090012.db", "filename": "master_20260606T090012.db", "size_mb": 12.3, "created_at": "2026-06-06 09:00:12"}
]
```

Returns `[]` if `BACKUP_DIR` does not exist.

---

### POST `/api/restore`

Restore a backup `master.db` (`routes.py:551`). Validates the filename is a
bare filename (no `/`, `\`, or `..`) and resolves under `BACKUP_DIR` — see
`routes.py:561-565`. Then:

1. Closes the current DB session and disposes its engine.
2. `shutil.copy2` the backup over `master.db`.
3. Copies `-wal` and `-shm` sidecars if present in the backup, otherwise
   removes stale sidecars next to the live DB.
4. Re-opens `Rekordbox6Database` and stores it on `app.state.db`.
5. **Clears every in-process analysis cache** — `energy._cache`,
   `classify._class_cache`, `score._mixability_cache`, and
   `similar.clear_index()`. Stale feature vectors built against the previous
   DB must not survive restore (`routes.py:603-609`).

**Request body** — `RestoreRequest`

| Field      | Type   | Required | Meaning                |
| ---------- | ------ | -------- | ---------------------- |
| `filename` | string | yes      | Bare backup filename   |

**Response — 200** — `RestoreResponse`

```json
{ "restored": true, "message": "Restored from master_20260606T090012.db" }
```

**Status codes**

| Status | When                                                       |
| ------ | ---------------------------------------------------------- |
| 200    | Success                                                    |
| 400    | Path traversal attempt (`/`, `\`, or `..`) or outside dir  |
| 404    | Backup file not found                                      |
| 409    | Rekordbox is running                                       |
| 500    | Copy failed, or reopen failed (db is left null)            |

---

### DELETE `/api/backups/{filename}`

Delete a single backup, plus its `-wal`/`-shm` sidecars if present
(`routes.py:614`). Path traversal blocked by `resolve()` + prefix check.

**Path param** — `filename` (bare name; no separators)

**Response — 200**

```json
{ "deleted": "master_20260606T090012.db" }
```

**Status codes**

| Status | When                                                  |
| ------ | ----------------------------------------------------- |
| 200    | Deleted                                               |
| 400    | Resolved path is outside BACKUP_DIR                   |
| 404    | File does not exist                                   |

The UI's "multi-select delete" calls this endpoint once per checked file then
shows a consolidated toast.

---

## 6. Tracks and library

### GET `/api/tracks`

Return the library, optionally filtered to a playlist and sorted. Designed for
single-shot full-library loads of ~3k–10k tracks; do not paginate on the
client (`routes.py:96`).

**Query params**

| Param         | Type   | Default  | Meaning                                                  |
| ------------- | ------ | -------- | -------------------------------------------------------- |
| `playlist_id` | int    | none     | Restrict to tracks in this Rekordbox playlist ID         |
| `sort_by`     | string | `title`  | `title`, `bpm`, `artist`, `album`, `key`, `rating`, `plays` |
| `sort_order`  | string | `asc`    | `asc` or `desc`                                          |
| `limit`       | int    | `5000`   | Max rows                                                 |
| `offset`      | int    | `0`      | Skip N rows                                              |

Sort details:

- `key` joins `DjmdKey` and sorts by `Seq` (so `1A < 2A < … < 12A`, not
  lexicographic — `routes.py:131`).
- `artist`/`album` join the relevant table and sort `lower(Name)`.
- Default falls through to `lower(DjmdContent.Title)`.

**Response headers** — `X-Total-Count: <total>` for paginated clients.

**Response — 200** — `list[TrackItem]`

```json
[
  {
    "id": 12345,
    "title": "Strobe",
    "artist": "Deadmau5",
    "album": "For Lack of a Better Name",
    "bpm": 128.0,
    "duration": 633.5,
    "has_phrase": true,
    "has_beats": true,
    "existing_hot_cues": 4,
    "key": "8A",
    "rating": 5,
    "play_count": 12,
    "last_played": "2026-05-12 22:31:00",
    "my_tags": ["Peak", "Vocals"],
    "color_name": "Green",
    "genre": "Progressive House",
    "comment": "Energy 8 | Peak | 8 bar intro"
  }
]
```

| Field               | Type      | Meaning                                                                 |
| ------------------- | --------- | ----------------------------------------------------------------------- |
| `id`                | int       | `DjmdContent.ID`                                                        |
| `bpm`               | float     | `DjmdContent.BPM / 100` (Rekordbox stores it ×100)                      |
| `duration`          | float     | `DjmdContent.Length` (seconds)                                          |
| `has_phrase`        | bool      | `AnalysisDataPath` is set (= `.EXT` file written; no `iterdir` scan)    |
| `has_beats`         | bool      | `BPM > 0`                                                               |
| `existing_hot_cues` | int       | Count of `DjmdCue` rows with `Kind ∈ [1, 8]` for this track             |
| `key`               | string    | Camelot string from `DjmdKey.ScaleName`                                 |
| `last_played`       | string \| null | Latest `DjmdHistory.DateCreated` for this track                    |
| `my_tags`           | list[str] | Names of `DjmdMyTag` attached via `DjmdSongMyTag`                       |
| `color_name`        | string    | `DjmdColor.Commnt` for `DjmdContent.ColorID` (or `""` if none)          |
| `genre`             | string    | `DjmdContent.GenreName` association proxy                               |
| `comment`           | string    | `DjmdContent.Commnt` (note the abbreviated spelling)                    |

**Performance note** — history and my-tags are loaded via `db.query(...).all()`
then filtered against `row_ids` in Python, **not** with `IN(row_ids)`. For
full-library loads (~3k rows) `IN` is slower than fetch-all-then-filter against
pyrekordbox's SQLCipher. Do not "optimize" this back — see CLAUDE.md and the
explicit code comment at `routes.py:148`.

Hot cue counts come from a single `GROUP BY` aggregate (`routes.py:188-196`),
not one query per track.

**Errors**

| Status | When                              |
| ------ | --------------------------------- |
| 404    | `?playlist_id=N` does not exist   |

---

### GET `/api/tracks/{track_id}/artwork`

Return the track's embedded artwork as an image response (`routes.py:206`).

**Primary**: `DjmdContent.ImagePath` resolved against `db._db_dir` and
`db._db_dir/share/` for relative paths.

**Fallback**: looks for `cover.jpg`, `folder.jpg`, `artwork.jpg`, `front.jpg`
(plus `.png` variants) in the audio file's parent directory.

**Response — 200** — `FileResponse` with media type inferred from suffix
(`image/jpeg`, `image/png`, `image/gif`).

**404** — Track does not exist, or no artwork found.

---

### GET `/api/tracks/{track_id}/audio`

Stream the track's audio file (`routes.py:244`). Reads `DjmdContent.FolderPath`
(despite the name, this is the full file path) and serves it via
`FileResponse`. Handles macOS `/:`-prefixed paths.

**Response — 200** — `FileResponse` with media type per extension
(mp3/wav/aac/m4a/flac/ogg/aiff).

**Errors**

| Status | When                                                |
| ------ | --------------------------------------------------- |
| 404    | Track not in DB, missing `FolderPath`, or file gone |

This endpoint is used by the in-page mini player. Browsers send range
requests; `FileResponse` handles 206 partial content natively.

---

### GET `/api/tags`

Return My Tags that are **actually applied** to at least one track
(`routes.py:270`). The endpoint filters `DjmdMyTag` against
`distinct(DjmdSongMyTag.MyTagID)` so orphan tags created and never used do
not pollute the UI's tag filter.

**Response — 200**

```json
[ {"id": "1", "name": "Peak"}, {"id": "5", "name": "Vocals"} ]
```

Returns `[]` on any exception (broad fallback for older Rekordbox schemas).

---

### GET `/api/playlists`

Return all Rekordbox playlists with their track counts (`routes.py:80`).

**Response — 200** — `list[PlaylistItem]`

```json
[ {"id": 1234, "name": "House 2026", "track_count": 87} ]
```

Track counts come from a single `GROUP BY` query over `DjmdSongPlaylist`.

---

## 7. Cue generation and application

See [cue-generation.md](./cue-generation.md) for the engine deep dive.

### POST `/api/generate`

Preview cues for one or more tracks **without writing** to the DB
(`routes.py:287`).

**Request** — `GenerateRequest`

| Field               | Type                                  | Default  | Meaning                                       |
| ------------------- | ------------------------------------- | -------- | --------------------------------------------- |
| `track_ids`         | list[int]                             | required | DjmdContent IDs                               |
| `mode`              | `"phrase" \| "bar" \| "auto"`         | `auto`   | Strategy selector                             |
| `bars_interval`     | int                                   | `16`     | Used in bar mode                              |
| `start_bar`         | int                                   | `1`      | First bar to seed cues at                     |
| `max_cues`          | int                                   | `8`      | Cap (slot count A–H)                          |
| `add_memory_cue`    | bool                                  | `false`  | Legacy alias; `memory_cue_mode` wins          |
| `memory_cue_mode`   | `"none" \| "load_only" \| "all"`      | `none`   | Memory cue placement                          |
| `add_fill_cues`     | bool                                  | `false`  | Fill empty slots with bar-interval cues       |

**Response — 200** — `GenerateResponse`

```json
{
  "tracks": [
    {
      "id": 12345,
      "title": "Strobe",
      "cues": [
        {"slot": 0, "label": "INTRO", "position_ms": 0, "is_phrase": true, "name": "Intro", "color_id": 1, "confidence": 1.0, "phrase_bars": 16}
      ],
      "mode_used": "phrase",
      "skipped": false
    }
  ]
}
```

Tracks not found in the DB are silently dropped (no error, no entry in
`tracks`).

---

### POST `/api/apply`

Write previewed cues to the DB (`routes.py:322`). Caller supplies the
`TrackResult` rows from `/generate`. Always makes a DB backup before writing
unless `dry_run=true`.

**Request** — `ApplyRequest`

| Field       | Type                  | Default | Meaning                                  |
| ----------- | --------------------- | ------- | ---------------------------------------- |
| `tracks`    | list[`TrackResult`]   | yes     | What to write                            |
| `overwrite` | bool                  | `false` | Wipe existing cues in slots we will fill |
| `dry_run`   | bool                  | `false` | Skip the actual write + backup           |

**Response — 200** — `ApplyResponse`

```json
{ "applied": 25, "skipped": 3, "dry_run": false, "backup_path": "/.../master_20260607T142233.db" }
```

**Errors**

| Status | When                                       |
| ------ | ------------------------------------------ |
| 409    | Rekordbox is running                       |
| 500    | Backup failed — no writes happen           |

`/api/apply` is rarely used directly by the UI — the combined streaming
endpoint avoids a large JSON round-trip.

---

### POST `/api/generate-apply`

Combined sync version: generates cues and writes them in a single request
(`routes.py:382`). Used by tests and scripts; the UI uses the SSE variant.

**Request** — `GenerateAndApplyRequest` — same fields as `GenerateRequest` plus:

| Field          | Type | Default | Meaning                                                              |
| -------------- | ---- | ------- | -------------------------------------------------------------------- |
| `overwrite`    | bool | `false` | Wipe existing cues in slots we will fill                             |
| `dry_run`      | bool | `false` | Skip writes + backup                                                 |
| `phrase_only`  | bool | `false` | Skip tracks lacking `.EXT` ANLZ (checked via `db.get_anlz_path`)     |

**Response** — same `ApplyResponse` as `/api/apply`.

---

### POST `/api/generate-apply-stream`

SSE version of the combined endpoint — emits per-track progress
(`routes.py:449`). **This is the one the UI uses.**

**Request body** — identical to `/generate-apply` (`GenerateAndApplyRequest`).

**SSE events**

Per track:

```
data: {"processed": 1, "total": 100, "applied": 1, "skipped": 0}
```

Final:

```
data: {"done": true, "applied": 95, "skipped": 5, "backup_path": "/.../master_20260607T142233.db"}
```

**Behaviour**

- Backup happens **before** the stream starts (so `500` is raised before any
  SSE byte if backup fails).
- `db.session.expire_all()` is called every 100 tracks to prevent identity-map
  bloat on multi-thousand-track libraries (`routes.py:492`).
- Skips:
  - Track not found → `skipped += 1`.
  - `phrase_only=true` and no `.EXT` ANLZ → `skipped += 1`.
  - Generator returned no cues → `skipped += 1`.
  - `write_cues_to_db` returned 0 → `skipped += 1`.

**Status codes**

| Status | When                       |
| ------ | -------------------------- |
| 409    | Rekordbox is running       |
| 500    | Backup failed              |

---

### POST `/api/delete-cues`

Delete all hot cues from a list of tracks (`routes.py:631`). Makes a backup
first unless `dry_run`.

**Request** — `DeleteRequest`

| Field        | Type      | Default | Meaning           |
| ------------ | --------- | ------- | ----------------- |
| `track_ids`  | list[int] | yes     | DjmdContent IDs   |
| `dry_run`    | bool      | `false` | Skip writes/backup |

**Response — 200** — `DeleteResponse`

```json
{ "deleted": 42, "tracks_affected": 12, "dry_run": false, "backup_path": "/.../master_20260607T142233.db" }
```

**409** when Rekordbox is running. **500** on backup failure.

---

### POST `/api/color-tracks`

Set each track's color to a slot derived from its BPM (`routes.py:673`). See
the BPM→color mapping in `autocue/db_writer.py:_bpm_to_color_sort_key`.

**Request** — `ColorTracksRequest`

| Field           | Type      | Default | Meaning                                                |
| --------------- | --------- | ------- | ------------------------------------------------------ |
| `track_ids`     | list[int] | yes     | Tracks to color                                        |
| `dry_run`       | bool      | `false` | Skip writes/backup                                     |
| `skip_colored`  | bool      | `false` | Leave tracks that already have a non-empty `ColorID`   |

**Response — 200** — `ColorTracksResponse`

```json
{ "colored": 87, "skipped": 13, "dry_run": false, "backup_path": "/.../master_20260607T142233.db" }
```

**409** when Rekordbox is running **and** `dry_run=false`. **500** on backup.

---

### POST `/api/color-tracks-stream`

SSE version, same request schema (`routes.py:702`). Streams progress every
50 tracks (`BATCH=50`) and at the end. Commits **once** at the end of the
batch via `db.session.commit()` rather than per-track.

**SSE events**

Per batch:

```
data: {"colored": 50, "skipped": 0, "total": 200}
```

Final:

```
data: {"done": true, "colored": 198, "skipped": 2, "total": 200, "backup_path": "/.../master_20260607T142233.db", "dry_run": false}
```

Wrapped in `try/except BaseException` — if the client disconnects mid-stream,
the session is rolled back before the exception propagates
(`routes.py:762-764`).

---

### POST `/api/cue-tools-stream`

Bulk cue edits (rename / recolor / shift / delete_orphan) over the whole hot-cue
set of each track (`routes.py:930`). Always operates on `Kind ∈ [1, 8]` —
memory cues (`Kind=0`) are never touched.

**Request** — `CueToolsRequest`

| Field           | Type                                                 | Default |
| --------------- | ---------------------------------------------------- | ------- |
| `operation`     | `"rename" \| "recolor" \| "shift" \| "delete_orphan"` | required |
| `track_ids`     | list[int]                                            | required |
| `dry_run`       | bool                                                 | `false` |
| `rename`        | `CueRenameParams`                                    | for `rename`        |
| `recolor`       | `CueRecolorParams`                                   | for `recolor`       |
| `shift`         | `CueShiftParams`                                     | for `shift`         |
| `delete_orphan` | `CueDeleteOrphanParams`                              | for `delete_orphan` |

The model validator enforces that the params for the chosen operation are
present (`schemas.py:295-301`).

**`CueRenameParams`** — `{ from_name, to_name }`. Exact, case-sensitive match
against `DjmdCue.Comment`.

**`CueRecolorParams`** — `{ slot_colors: dict[str, int] }`. Maps slot index
strings `"0".."7"` to `ColorTableIndex` `0..8` (0=none, 1=Pink, …, 8=Purple).

**`CueShiftParams`** — `{ delta_ms: int (nonzero), negative_policy: "skip" | "clamp_to_zero" | "abort_track" }`.

- `abort_track` (default): if any cue on the track would go negative, leave
  the whole track untouched.
- `skip`: silently drop the cues that would go negative.
- `clamp_to_zero`: place them at 0 ms.

Shift also updates `OutMsec`/`OutFrame` for loop cues to preserve loop length
(`routes.py:1038-1045`).

**`CueDeleteOrphanParams`** — `{ keep_slots: 1..8 }`. Deletes cues with
`Kind > keep_slots`.

**SSE events** — per BATCH=50:

```
data: {"processed": 50, "affected": 32, "total": 200}
```

Final:

```
data: {"done": true, "summary": {
  "operation": "shift",
  "tracks_processed": 200,
  "tracks_affected": 195,
  "cues_changed": 1432,
  "cues_skipped": 8,
  "skip_reasons": {"would_be_negative": 8},
  "dry_run": false,
  "backup_path": "master_20260607T142233.db"
}}
```

`backup_path` here is **the filename only**, not the full path
(`routes.py:974`). The other backup-emitting endpoints return full paths.

Stable `skip_reasons` keys:

| Key                  | Meaning                                        |
| -------------------- | ---------------------------------------------- |
| `no_match`           | rename / recolor: no slot matched              |
| `would_be_negative`  | shift / `skip` policy                          |
| `track_aborted`      | shift / `abort_track` policy                   |
| `beyond_keep_slots`  | delete_orphan                                  |

**409** when Rekordbox is running and `dry_run=false`. **500** on backup
failure. Empty `track_ids` short-circuits to a synthesized `done` event with
zeros — no backup is taken (`routes.py:945-962`).

---

## 8. Library health

See [library-health.md](./library-health.md) for the scoring rubric.

### GET `/api/tracks/{track_id}/health`

Per-track health report (`routes.py:828`).

**Response — 200** — `TrackHealthReport`

```json
{
  "track_id": 12345,
  "score": 85,
  "issues": [
    {"code": "NO_PHRASE", "severity": "warning", "message": "No phrase analysis"}
  ],
  "fix_tier": "bar",
  "hot_cue_count": 4,
  "memory_cue_count": 1
}
```

| Field              | Type   | Meaning                                                              |
| ------------------ | ------ | -------------------------------------------------------------------- |
| `score`            | int    | 0–100; 0 ⇒ `NO_AUDIO_FILE` (and other checks skipped)                |
| `fix_tier`         | string | `phrase` \| `bar` \| `heuristic` \| `none` — best generator strategy |
| `issues[].code`    | string | `NO_CUES`, `NO_PHRASE`, `NO_BEATGRID`, `DUPLICATE_CUE`, `UNNAMED_CUES`, `NO_MEMORY_CUE`, `NO_AUDIO_FILE`, `INTERNAL_ERROR` |
| `issues[].severity`| string | `error` \| `warning` \| `info`                                       |

Score deductions: `-30 NO_CUES`, `-10 NO_PHRASE`, `-10 NO_BEATGRID`,
`-5 DUPLICATE_CUE`, `-5 UNNAMED_CUES`. `NO_MEMORY_CUE` is info-only.
`NO_AUDIO_FILE` forces `score=0` and skips other checks.

On internal exception, returns score 0 + one `INTERNAL_ERROR` issue rather
than 500 (`routes.py:837-843`).

**404** — Track not found.

---

### GET `/api/health`

SSE stream of the whole library's health (`routes.py:846`). One event per
track, then a summary.

**Query params**

| Param         | Type | Meaning                                |
| ------------- | ---- | -------------------------------------- |
| `playlist_id` | int  | Restrict scan to this playlist's tracks |

**SSE events**

Bootstrap (once, before tracks):

```
data: {"total": 3127}
```

Per track — full `TrackHealthReport`:

```
data: {"track_id": 12345, "score": 85, "issues": [...], "fix_tier": "bar", "hot_cue_count": 4, "memory_cue_count": 1}
```

Final summary:

```
data: {"done": true, "summary": {
  "total": 3120,
  "excluded_missing_audio": 7,
  "library_score": 78.4,
  "no_cues": 23,
  "no_phrase": 412,
  "no_beatgrid": 5,
  "duplicate_cues": 18,
  "unnamed_cues": 200,
  "no_memory_cue": 1080,
  "fix_tier_counts": {"phrase": 2700, "bar": 380, "heuristic": 40, "none": 0}
}}
```

- `library_score` is the **mean of non-`NO_AUDIO_FILE` tracks** — files
  missing on disk do not drag the score down.
- `excluded_missing_audio` reports how many tracks were excluded.
- Per-track exceptions yield `{"score":0,"fix_tier":"none","issues":[{"code":"INTERNAL_ERROR",...}]}`
  so a single bad row never aborts the scan.

**404** when `?playlist_id=N` does not exist.

---

## 9. Analysis (energy / mixability / classification / similar)

### GET `/api/tracks/{track_id}/energy`

PWAV-derived energy curve, resampled to a fixed length (`routes.py:1113`).

**Response — 200** — `EnergyResponse`

```json
{ "track_id": 12345, "energy": [0.12, 0.18, 0.31, ...], "n_points": 50, "energy_profile": "build" }
```

| Field            | Type           | Meaning                                                        |
| ---------------- | -------------- | -------------------------------------------------------------- |
| `energy`         | list[float] \| null | 0–1 normalized curve; `null` if PWAV unavailable          |
| `n_points`       | int            | `len(energy)` (default 50)                                     |
| `energy_profile` | string \| null | `"flat"` \| `"build"` \| `"wave"` \| `"drop-then-flat"`        |

Curve is normalized to 0–1 (raw PWAV / 31.0), 3-point smoothed, then averaged
down to `n_points`. Curve length is part of the cache key in `energy._cache`.

**404** — Track not found.

---

### GET `/api/tracks/{track_id}/mixability`

Mixability score (0–100) and breakdown (`routes.py:1128`). Cached in
`score._mixability_cache`.

**Response — 200** — `MixabilityResponse`

```json
{
  "track_id": 12345,
  "score": 78,
  "intro_bars": 16,
  "outro_bars": 16,
  "phrase_count": 12,
  "vocal_proxy": false,
  "energy_variance": 0.21,
  "outro_length_unknown": false,
  "components": {"intro": 20, "outro": 20, "energy": 18, "vocals": 10, "structure": 10}
}
```

If no phrase data, `score` is `null` and `components` is `null`.

---

### GET `/api/tracks/{track_id}/classification`

Track category (`routes.py:1149`). See
[track-classification.md](./track-classification.md).

**Response — 200** — `ClassificationResponse`

```json
{
  "track_id": 12345,
  "primary": "peak",
  "label": "Peak",
  "color": "#FF4D4F",
  "confidence": 0.82,
  "scores": {"warmup": 0.02, "build": 0.31, "peak": 0.82, "after_hours": 0.05, "closing": 0.0},
  "bpm": 128.0,
  "energy_mean": 0.71,
  "energy_peak": 0.91,
  "vocal_proxy": false
}
```

| Field          | Type         | Meaning                                                                  |
| -------------- | ------------ | ------------------------------------------------------------------------ |
| `primary`      | string       | `warmup` / `build` / `peak` / `after_hours` / `closing` / `unknown`      |
| `confidence`   | float        | Score of the primary category, 0–1                                       |
| `scores`       | dict         | Score per category                                                       |
| `energy_mean`  | float \| null | Mean of the energy curve                                                |

**404** — Track not found.

---

### GET `/api/classify`

SSE: classify every track in the library or a playlist
(`routes.py:1249`).

**Query params**

| Param           | Type | Default | Meaning                                  |
| --------------- | ---- | ------- | ---------------------------------------- |
| `playlist_id`   | int  | none    | Restrict to this playlist                |
| `force_refresh` | bool | `false` | Clear `_class_cache` before scanning     |

**SSE events**

Per track — full `ClassificationResponse`:

```
data: {"track_id": 12345, "primary": "peak", "label": "Peak", "color": "#FF4D4F", "confidence": 0.82, ...}
```

Final:

```
data: {"done": true, "total": 3127, "counts": {"warmup": 320, "build": 540, "peak": 1100, "after_hours": 700, "closing": 80, "unknown": 387}}
```

**404** when `?playlist_id=N` does not exist.

---

### GET `/api/tracks/{track_id}/similar`

K-nearest tracks by cosine similarity over a 6-dim feature vector
(`routes.py:1304`). See [similarity-search.md](./similarity-search.md) if
present, else CLAUDE.md "similar" section.

**Query params**

| Param           | Type   | Default | Range          | Meaning                                       |
| --------------- | ------ | ------- | -------------- | --------------------------------------------- |
| `n`             | int    | `10`    | 1–100          | Result count                                  |
| `bpm_gate`      | float  | `8.0`   | 0–50           | Max BPM delta                                 |
| `force_rebuild` | bool   | `false` |                | Clear and rebuild the index                   |

**Response — 200** — `SimilarTracksResponse`

```json
{
  "track_id": 12345,
  "results": [
    {"track_id": 12346, "score": 0.92, "bpm_diff": 1.0},
    {"track_id": 12347, "score": 0.88, "bpm_diff": 0.5}
  ]
}
```

| Field            | Meaning                                                                       |
| ---------------- | ----------------------------------------------------------------------------- |
| `results[].score`| Cosine similarity 0–1; capped at 0.65 when neither side has ANLZ energy data |
| `bpm_diff`       | `|target_bpm − candidate_bpm|`                                                |

The similarity index is module-level in `similar.py`, guarded by
`_INDEX_LOCK`, and **pre-warmed in a background daemon thread on startup**
(`deps.py:77-79`). The first request after startup may see a cold index for a
few seconds.

**404** — Track not found.

---

## 10. Transitions and set builder

### POST `/api/transitions/score`

Score a single A→B transition (`routes.py:1326`). See
[transitions.md](./transitions.md) if present.

**Request** — `TransitionRequest`

```json
{ "track_a_id": 1, "track_b_id": 2 }
```

**Response — 200** — `TransitionResponse`

```json
{
  "track_a_id": 1,
  "track_b_id": 2,
  "overall": 88.5,
  "bpm": 90.0,
  "key": 100.0,
  "energy": 76.0,
  "bpm_a": 126.0,
  "bpm_b": 128.0,
  "key_a": "8A",
  "key_b": "8A",
  "end_energy_a": 0.71,
  "start_energy_b": 0.55,
  "explanation": ["Perfect key match", "Energy step-down at outro→intro"]
}
```

- `end_energy_a` and `start_energy_b` are **scalars** (or null) — do not
  re-read ANLZ curves; pass them directly to energy-penalty functions.
- Missing energy data: `_energy_score(None, None) = 50.0` (neutral, not 100);
  one side missing caps the energy score at 75. This defeats the old "free
  100" bug where any same-key same-BPM no-ANLZ pair scored overall=100.

**Errors**

| Status | When                                          |
| ------ | --------------------------------------------- |
| 400    | `track_a_id == track_b_id`                    |
| 404    | Either track not in DB                        |

---

### POST `/api/setbuilder`

Beam-search a full DJ set (`routes.py:1351`). See
[set-builder.md](./set-builder.md) for the algorithm.

**Request** — `SetBuilderRequest`

| Field                  | Type                              | Default  | Meaning                                                            |
| ---------------------- | --------------------------------- | -------- | ------------------------------------------------------------------ |
| `start_bpm`            | float                             | `110.0`  | Seed BPM                                                           |
| `end_bpm`              | float                             | `135.0`  | Target final BPM                                                   |
| `duration_minutes`     | float                             | `60.0`   | Target total duration                                              |
| `energy_mode`          | `"build" \| "flat" \| "drop"`     | `build`  | Energy trajectory                                                  |
| `bpm_step_max`         | float                             | `0.08`   | Max BPM step as fraction (8% default)                              |
| `seed_track_id`        | int \| null                       | none     | Override seed selection                                            |
| `anchor_track_ids`     | list[int]                         | `[]`     | Must-include tracks; merged at BPM-sorted positions                |

**Response — 200** — `SetBuilderResponse`

```json
{
  "tracks": [
    {
      "track_id": 100,
      "title": "Warmup", "artist": "X", "bpm": 110.0, "key": "8A",
      "category": "warmup",
      "transition_score": null,
      "mix_advice": null,
      "relaxed": false
    },
    {
      "track_id": 101,
      "title": "Track 2", "artist": "Y", "bpm": 113.0, "key": "8A",
      "category": "build",
      "transition_score": 87.2,
      "mix_advice": "Nudge pitch +3 BPM — blend over 8–16 bars; compatible key.",
      "relaxed": false
    }
  ],
  "total_tracks": 12,
  "estimated_duration_minutes": 62.5,
  "terminated_reason": "target_duration_reached"
}
```

| Field                       | Meaning                                                                |
| --------------------------- | ---------------------------------------------------------------------- |
| `tracks[].relaxed`          | `true` if placed via relaxed constraints fallback                      |
| `terminated_reason`         | `target_duration_reached` \| `no_candidates_passed_thresholds` \| `safety_cap_hit` |

**422** if no valid set could be built with the given constraints.

---

### GET `/api/setbuilder/alternatives`

Replacement candidates for one slot, scored on fit to both neighbours
(`routes.py:1391`).

**Query params**

| Param         | Type   | Default | Meaning                                                       |
| ------------- | ------ | ------- | ------------------------------------------------------------- |
| `track_id`    | int    | yes     | The track being replaced                                      |
| `prev_id`     | int    | none    | Previous track in the set                                     |
| `next_id`     | int    | none    | Next track in the set                                         |
| `exclude_ids` | string | `""`    | Comma-separated track IDs to exclude (e.g. `"5,7,9"`)         |
| `n`           | int    | `8`     | 1–20 — result count                                           |

**Response — 200** — `SetAlternativesResponse`

```json
{
  "alternatives": [
    {
      "track_id": 202, "title": "Alt 1", "artist": "Z", "bpm": 128.0, "key": "8A",
      "score": 86.5,
      "from_prev": 88.0,
      "to_next": 85.0,
      "genre": "Progressive House",
      "genre_match": true
    }
  ]
}
```

| Field         | Meaning                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------ |
| `score`       | Mean of `from_prev` and `to_next`; **−20** if genre mismatches reference and neighbours          |
| `genre_match` | `true` = matches ref/neighbour genre; `false` = mismatch (penalty applied); `null` = unknown     |

Builds the similarity index on first call if not warm (`routes.py:1405`).

---

## 11. Playlists

### POST `/api/playlists/suggest`

Return top tracks for a DJ-set category, sorted by category score
(`routes.py:1159`). Used by the "Suggest playlist" UI.

**Request** — `PlaylistSuggestRequest`

| Field              | Type            | Default | Meaning                                                        |
| ------------------ | --------------- | ------- | -------------------------------------------------------------- |
| `category`         | string          | yes     | `warmup` \| `build` \| `peak` \| `after_hours` \| `closing`    |
| `count`            | int             | `20`    | 1–500                                                          |
| `exclude_ids`      | list[int]       | `[]`    | Tracks to omit                                                 |
| `seed_track_ids`   | list[int]       | `[]`    | Pre-included tracks; **bypass `exclude_ids`**                  |
| `playlist_id`      | int \| null     | none    | Restrict candidate pool to this playlist (else full library)   |

**Behaviour**

- Seeds are placed at the front in user-supplied order.
- The remaining `count − len(seeds)` slots are filled by weighted random draw
  from the top pool (weights = score²). Pool size = `max(fill_count × 3, 60)`.
- Up to `fill_count × 4` draws are attempted; if still short, the pool is
  drained in score order.

**Response — 200** — `PlaylistSuggestResponse`

```json
{
  "category": "peak",
  "results": [
    {"track_id": 100, "score": 0.92},
    {"track_id": 101, "score": 0.89}
  ]
}
```

**Errors**

| Status | When                                |
| ------ | ----------------------------------- |
| 400    | Unknown `category` or bad `count`   |
| 404    | `playlist_id` not found             |

---

### POST `/api/playlists`

Create a Rekordbox playlist (`routes.py:1494`).

**Request** — `CreatePlaylistRequest`

| Field        | Type      | Required | Meaning                |
| ------------ | --------- | -------- | ---------------------- |
| `name`       | string    | yes      | Playlist name          |
| `track_ids`  | list[int] | yes      | Tracks (in order)      |

**Response — 200** — `CreatePlaylistResponse`

```json
{ "playlist_id": 5001, "name": "Tonight's Set", "track_count": 12 }
```

**Errors**

| Status | When                                       |
| ------ | ------------------------------------------ |
| 400    | Name empty or no tracks                    |
| 409    | Rekordbox is running                       |
| 500    | Insert failed (rolled back)                |

Each `DjmdSongPlaylist` row is given an explicit `db.generate_unused_id(...)`,
a fresh UUID, and a sequential `TrackNo`. The new playlist's `Seq` is
`max(Seq) + 1`.

---

## 12. Auto-Tag

See [auto-tag.md](./auto-tag.md) for the My Tag deep dive.

### POST `/api/auto-tag`

Write detected My Tags to a list of tracks (`routes.py:1554`).

**Request** — `AutoTagRequest`

| Field        | Type        | Default        | Meaning                                                                  |
| ------------ | ----------- | -------------- | ------------------------------------------------------------------------ |
| `track_ids`  | list[int]   | yes            | Tracks to tag                                                            |
| `tag_types`  | list[str]   | `["category"]` | Subset of: `category`, `vocal`, `energy_level`, `energy_profile`, `intro_outro`, `decade`, `bpm_tier`, `play_history` |
| `overwrite`  | bool        | `true`         | Replace AutoCue-owned tags on re-run                                     |
| `dry_run`    | bool        | `false`        | Don't write or commit                                                    |

**Response — 200** — `AutoTagResponse`

```json
{
  "tagged": 87,
  "skipped_no_data": 13,
  "errors": 0,
  "dry_run": false,
  "undo_data": { "removed": [...], "added": ["1234", "1235"] }
}
```

The `undo_data` blob is the input for `/api/auto-tag/undo`.

**409** when Rekordbox is running and `dry_run=false`. **500** rolls back the
session and reports the message.

---

### POST `/api/auto-tag/undo`

Reverse a previous tag run (`routes.py:1716`).

**Request** — `AutoTagUndoRequest`

```json
{ "undo_data": { "removed": [...], "added": ["1234", "1235"] } }
```

**Response — 200** — `AutoTagUndoResponse`

```json
{ "removed": 87, "restored": 13 }
```

**409** when Rekordbox is running. **500** rolls back.

---

### POST `/api/auto-tag/discogs/test`

Validate a Discogs personal access token via the Discogs identity endpoint
(`routes.py:1599`).

**Request body** — raw `dict`:

```json
{ "token": "abc123..." }
```

**Response — 200**

```json
{ "ok": true, "username": "yourname" }
```

**400** on missing token or any Discogs failure (message echoed).

---

### POST `/api/auto-tag/discogs`

SSE stream that fetches Discogs Styles per track and writes them as My Tags
(`routes.py:1619`).

**Request** — `DiscogsTagRequest`

| Field           | Type      | Default | Meaning                                                                |
| --------------- | --------- | ------- | ---------------------------------------------------------------------- |
| `track_ids`     | list[int] | yes     | Tracks                                                                 |
| `token`         | string    | yes     | Discogs personal access token                                          |
| `dry_run`       | bool      | `false` | Skip writes                                                            |
| `skip_existing` | bool      | `false` | Skip tracks that already have **non-AutoCue** My Tags (likely Discogs) |

The `skip_existing` allowlist is `auto_tag.ALL_AUTOCUE_TAG_NAMES`.

**SSE events**

Per track (success):

```
data: {"processed": 5, "total": 50, "track_id": 12345, "artist": "Deadmau5", "title": "Strobe", "styles": ["Progressive House", "Trance"], "tagged": 4}
```

Per track (skipped):

```
data: {"processed": 6, "total": 50, "track_id": 12346, "styles": [], "skipped": 2}
```

Per track (error):

```
data: {"processed": 7, "total": 50, "track_id": 12347, "error": "HTTP 429", "errors": 1}
```

Final:

```
data: {"done": true, "tagged": 42, "skipped": 6, "errors": 2}
```

**409** when Rekordbox is running and `dry_run=false`.

Discogs is rate-limited (60 req/min) by an in-process token bucket; results
are cached per process in `discogs._cache`.

---

## 13. Comment enrichment

See [comment-enrichment.md](./comment-enrichment.md) for the MIK-compatible
format.

### POST `/api/enrich-comments`

Sync batch comment writer (`routes.py:1736`).

**Request** — `EnrichCommentsRequest`

| Field        | Type      | Default | Meaning                                |
| ------------ | --------- | ------- | -------------------------------------- |
| `track_ids`  | list[int] | yes     | Tracks                                 |
| `overwrite`  | bool      | `false` | Replace existing user text             |
| `dry_run`    | bool      | `false` | Skip writes/backup                     |

**Response — 200** — `EnrichCommentsResponse`

```json
{ "enriched": 87, "skipped": 13, "errors": 0, "dry_run": false, "backup_path": "/.../master_20260607T142233.db" }
```

**409** when Rekordbox is running and `dry_run=false`. **500** rolls back.

---

### POST `/api/enrich-comments/preview`

Compute what one track's comment would become **without writing**
(`routes.py:1816`).

**Request** — `CommentPreviewRequest`

```json
{ "track_id": 12345 }
```

**Response — 200** — `CommentPreviewResponse`

```json
{
  "track_id": 12345,
  "current_comment": "Big intro vibe",
  "preview": "8A - Energy 7 | Peak | 8 bar intro /* AutoCue: ... */"
}
```

**404** — Track not found.

---

### POST `/api/enrich-comments/stream`

SSE batch enrichment, with **per-track commit** so one failing track no longer
rolls back the whole batch (`routes.py:1755`).

**Request** — same as `/api/enrich-comments`.

**SSE events**

Per track:

```
data: {"processed": 5, "total": 50, "enriched": 4}
```

On commit failure inside the per-track try block, the row is rolled back,
`errors += 1`, and `enriched -= 1` (the count is monotonically truthful).

Final:

```
data: {"done": true, "enriched": 42, "skipped": 5, "errors": 3, "backup_path": "/.../master_20260607T142233.db", "dry_run": false}
```

A single backup is taken once up front before any writes. **409** when
Rekordbox is running and `dry_run=false`.

---

## 14. Discover and Download

See [discovery.md](./discovery.md) and [download.md](./download.md) if present.

### GET `/api/discover`

SSE stream of "new releases from your library's top artists" via Discogs
(`routes.py:1837`).

**Query params**

| Param         | Type   | Default        | Meaning                                                |
| ------------- | ------ | -------------- | ------------------------------------------------------ |
| `since_year`  | int    | last year      | Only releases since this year                          |
| `max_artists` | int    | `25`           | 1–100 — cap on artists queried (Discogs rate-limit safety) |
| `per_artist`  | int    | `5`            | 1–20 — releases per artist                             |
| `token`       | string | env / .env     | Discogs token (overrides env)                          |

If `token` is empty AND `DISCOGS_TOKEN` is not set anywhere, returns 400.

**SSE events**

Progress-only tick (artist with no new releases):

```
data: {"processed": 7, "total": 25, "suggested": 4}
```

Suggestion event — `DiscoverItem`:

```
data: {
  "processed": 8, "total": 25, "suggested": 5,
  "artist": "Deadmau5", "album": "Where's the Drop?", "title": null,
  "year": 2018, "thumb": "https://...", "cover": "https://...",
  "genres": ["Electronic"], "styles": ["Progressive House", "Trance"],
  "formats": ["Vinyl", "LP", "Album"],
  "url": "https://www.discogs.com/release/...",
  "query": "Deadmau5 Where's the Drop?"
}
```

Error tick (preserves stream, no 500):

```
data: {"error": "Discogs API timeout"}
```

Final:

```
data: {"done": true, "total": 25, "suggested": 8}
```

**Behaviour**

- The endpoint reuses the Discogs token bucket from `/api/auto-tag/discogs`.
- Owned albums are filtered out via `library_album_set(db)`.
- `formats` is the Discogs `format` tag list — surfaced for UI chips.

---

### GET `/api/download/config`

Probe whether YouTube downloads are available (`routes.py:1933`).

**Response — 200** — `DownloadConfigResponse`

```json
{
  "available": true,
  "ffmpeg": true,
  "default_dir": "/Users/me/Music/AutoCue",
  "music_folder": "/Users/me/Music/Rekordbox"
}
```

| Field          | Meaning                                                                  |
| -------------- | ------------------------------------------------------------------------ |
| `available`    | `yt_dlp` is importable                                                   |
| `ffmpeg`       | `ffmpeg` is on PATH                                                      |
| `default_dir`  | `AUTOCUE_DOWNLOAD_DIR` env or `~/Music/AutoCue`                          |
| `music_folder` | Detected Rekordbox music root via `os.path.commonpath()` over up to 30 `FolderPath` values, or `null` on failure |

---

### POST `/api/download`

Download one track via yt-dlp; stream progress as SSE (`routes.py:1957`).

**Request** — `DownloadRequest`

| Field          | Type        | Default | Meaning                                                  |
| -------------- | ----------- | ------- | -------------------------------------------------------- |
| `query`        | string      | yes     | YouTube URL or search term (`"artist - title"`)          |
| `dest_dir`     | string \| null | none | Override download directory                              |
| `audio_format` | string      | `mp3`   | Target audio format (passed to `FFmpegExtractAudio`)     |

A bare search term is wrapped as `ytsearch1:<query>`. URLs pass through.

**SSE events**

Per progress tick (from yt-dlp's hook):

```
data: {"processed": 0, "total": 1, "query": "Deadmau5 Strobe", "status": "downloading", "percent": 42.7}
```

Final (success):

```
data: {"done": true, "status": "finished", "path": "/Users/me/Music/AutoCue/Deadmau5 - Strobe.mp3", "downloaded": 1}
```

Final (error):

```
data: {"done": true, "status": "error", "error": "...", "failed": 1}
```

**Status codes**

| Status | When                                                |
| ------ | --------------------------------------------------- |
| 503    | yt-dlp not installed, or ffmpeg not on PATH         |

The download runs in a worker thread; progress events flow through a
`queue.Queue` so the SSE generator can iterate them without blocking the
event loop (`routes.py:1971-2001`).

---

### POST `/api/download/album`

Download multiple tracks sequentially (`routes.py:2011`).

**Request** — `DownloadAlbumRequest`

| Field          | Type                       | Default |
| -------------- | -------------------------- | ------- |
| `tracks`       | list[`DownloadTrackSpec`]  | yes     |
| `dest_dir`     | string \| null             | none    |
| `audio_format` | string                     | `mp3`   |

`DownloadTrackSpec` = `{ query: string, title?: string }`.

**SSE events**

Per track:

```
data: {"processed": 1, "total": 8, "title": "Strobe", "query": "Deadmau5 Strobe", "status": "finished", "path": "/.../Strobe.mp3", "downloaded": 1}
```

```
data: {"processed": 2, "total": 8, "title": "Ghosts 'n' Stuff", "query": "...", "status": "error", "error": "...", "failed": 1}
```

Final:

```
data: {"done": true, "downloaded": 7, "failed": 1, "total": 8}
```

**503** when yt-dlp or ffmpeg missing.

---

## 15. Middleware

### GZipMiddleware

Installed first (`app.py:50`):

```python
app.add_middleware(GZipMiddleware, minimum_size=1000)
```

- Compresses JSON responses ≥1000 bytes.
- **Does not compress SSE streams.** Starlette skips gzip when the response is
  `text/event-stream` (and partial-chunk compression would defeat streaming
  anyway). The `X-Accel-Buffering: no` header is a belt-and-braces signal.

### CORSMiddleware

Installed after GZip (`app.py:51-57`):

```python
allowed_origins = [
    "null",                          # file:// pages
    f"http://localhost:{port}",
    f"http://127.0.0.1:{port}",
]
```

- `allow_credentials=False` — cookies are never read.
- `allow_methods=["*"]`, `allow_headers=["*"]`.
- **Do not widen.** The server writes to `master.db` with the user's own
  permissions; a wide CORS list is one cross-site request away from an attacker
  rewriting the library.

---

## 16. Common error responses

| Status | Body                                  | When                                                     |
| ------ | ------------------------------------- | -------------------------------------------------------- |
| 400    | `{"detail": "..."}`                   | Bad request (invalid filename, unknown category, empty playlist name, `track_a == track_b`, …) |
| 404    | `{"detail": "Track not found"}`       | Track/playlist/backup not in DB                          |
| 409    | `{"detail": "Rekordbox is running — close it before ..."}` | Rekordbox process detected via psutil      |
| 422    | `{"detail": [...]}`                   | Pydantic validation (`CueShiftParams.delta_ms == 0`, etc.) or set-builder constraints unmet |
| 500    | `{"detail": "Backup failed — aborting: ..."}` | Backup failure, DB write failure, internal exception |
| 503    | `{"detail": "yt-dlp is not installed..."}` | yt-dlp / ffmpeg missing (download endpoints)         |
| 503    | `{"detail": "Rekordbox database not connected"}` | DB never opened at lifespan start                 |

**Client tip** — always check `r.ok` before reading typed fields from JSON.
A 409 returns `{"detail": "..."}`; reading `.applied` on it yields `undefined`
and produces misleading toast messages. This is an explicit invariant in the
codebase (see CLAUDE.md "Fetch error handling in JS").

---

## 17. SSE event format — wire-level examples

### `/api/health`

```
data: {"total": 3127}\n\n
data: {"track_id":1,"score":85,"issues":[{"code":"NO_PHRASE","severity":"warning","message":"No phrase analysis"}],"fix_tier":"bar","hot_cue_count":4,"memory_cue_count":1}\n\n
data: {"track_id":2,"score":100,"issues":[],"fix_tier":"phrase","hot_cue_count":8,"memory_cue_count":1}\n\n
...
data: {"done":true,"summary":{"total":3120,"excluded_missing_audio":7,"library_score":78.4,"no_cues":23,"no_phrase":412,"no_beatgrid":5,"duplicate_cues":18,"unnamed_cues":200,"no_memory_cue":1080,"fix_tier_counts":{"phrase":2700,"bar":380,"heuristic":40,"none":0}}}\n\n
```

### `/api/discover`

```
data: {"processed":1,"total":25,"suggested":1,"artist":"Deadmau5","album":"Where's the Drop?","title":null,"year":2018,"thumb":"https://...","cover":"https://...","genres":["Electronic"],"styles":["Progressive House"],"formats":["Vinyl","LP","Album"],"url":"https://www.discogs.com/release/...","query":"Deadmau5 Where's the Drop?"}\n\n
data: {"processed":2,"total":25,"suggested":1}\n\n
...
data: {"done":true,"total":25,"suggested":8}\n\n
```

### `/api/generate-apply-stream`

```
data: {"processed":1,"total":100,"applied":1,"skipped":0}\n\n
data: {"processed":2,"total":100,"applied":2,"skipped":0}\n\n
...
data: {"done":true,"applied":95,"skipped":5,"backup_path":"/Users/me/Library/AutoCue/backups/master_20260607T142233.db"}\n\n
```

### `/api/auto-tag/discogs`

```
data: {"processed":1,"total":3,"track_id":100,"artist":"Deadmau5","title":"Strobe","styles":["Progressive House","Trance"],"tagged":1}\n\n
data: {"processed":2,"total":3,"track_id":101,"styles":[],"skipped":1}\n\n
data: {"processed":3,"total":3,"track_id":102,"error":"HTTP 429 Too Many Requests","errors":1}\n\n
data: {"done":true,"tagged":1,"skipped":1,"errors":1}\n\n
```

---

## 18. Performance notes

| Pattern                                   | Why                                                                            |
| ----------------------------------------- | ------------------------------------------------------------------------------ |
| `/api/tracks` fetches whole history / my-tag tables and filters in Python | For ~3k row libraries, full table scan + Python `if id in row_ids` outperforms SQLAlchemy `IN(row_ids)` against pyrekordbox's SQLCipher. Do **not** re-add `.filter(...ContentID.in_(row_ids))`. |
| `/api/tracks` hot cue counts come from one `GROUP BY` | Avoids one `COUNT(*)` per track.                                                |
| `/api/tracks` `has_phrase` reads `AnalysisDataPath` | ~3764× faster than calling `db.get_anlz_path()` per track (which does `iterdir()`). |
| Similar index pre-warmed in background thread on startup | The first `/api/tracks/{id}/similar` call doesn't pay the full build cost.    |
| `_class_cache`, `_mixability_cache`, `energy._cache` are in-process | Repeated requests for the same track are O(1). All three caches are wiped on `/api/restore`. |
| `db.session.expire_all()` every 100 tracks in `/generate-apply-stream` | Prevents SQLAlchemy identity-map bloat over multi-thousand-track runs.        |
| `/color-tracks-stream` and `/cue-tools-stream` commit once at the end | Many-row commits, one fsync.                                                  |

---

## 19. Testing

Endpoint coverage lives in `tests/test_serve_routes.py` — **194 tests** at
last count. Every endpoint above has at least:

- A happy-path test against a mocked `Rekordbox6Database`.
- A 409 test when the write guard fires (`rekordbox_is_running()` patched).
- Schema validation tests (Pydantic 422 paths).
- Streaming endpoints have at least one test that consumes the SSE body and
  asserts both per-event and final-event payloads.

Tests use `fastapi.testclient.TestClient` and never hit a real Rekordbox DB.
DB-shape helpers (`_make_db()`, `_make_tags_db()`, etc.) build mock content
rows directly.

If you add a new endpoint, add a matching test class that covers the same
three axes (happy path, write guard, schema). The CI workflow runs the full
suite on Python 3.10–3.12.

---

## 20. Related docs

- [Cue generation engine](./cue-generation.md) — `/api/generate`,
  `/api/apply`, `/api/generate-apply*`.
- [Track classification](./track-classification.md) — `/api/tracks/{id}/classification`,
  `/api/classify`.
- [CLI usage](./cli-usage.md) — the `autocue` CLI shares the underlying
  generator and writer with the server.
- [CLAUDE.md](../../CLAUDE.md) — short-form invariants and gotchas that the
  endpoint code assumes. Authoritative for caching, SQL patterns, and the
  Rekordbox-running guard.

For long-form end-user feature docs, see [`docs/FEATURES.md`](../FEATURES.md).

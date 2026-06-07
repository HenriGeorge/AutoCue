# Cue Library Tools

Bulk-edit hot cues across every track in your Rekordbox library — or just the
currently visible subset — in one streamed operation. Use these tools to fix
systematic problems that would be tedious to repair track-by-track: an
inconsistent cue name, a beatgrid offset that propagated into every cue, junk
hot cues left over from a previous tagging convention, or a slot-color scheme
that no longer matches the rest of your library.

This page covers the four bulk operations exposed by the local server (`autocue
serve`), the single SSE endpoint that drives them all, the safety guards built
around destructive writes, and the wire-format details you need to call the
endpoint directly from a script.

> Cue Library Tools are **local-mode only**. The web app at
> [autocue.app](https://autocue.app) cannot reach them — they require a running
> `autocue serve` instance with direct write access to your Rekordbox database.

---

## Overview

The "Cue Library Tools" panel in the Library tab dispatches four kinds of bulk
cue edits through a single SSE endpoint:

| Operation         | What it does                                              | Reversible via UI? |
|-------------------|-----------------------------------------------------------|--------------------|
| `rename`          | Replace exact cue name across all matching cues           | Restore from backup |
| `recolor`         | Set a specific slot's color across all matching tracks    | Restore from backup |
| `shift`           | Move every cue position by ±N milliseconds                | Restore from backup |
| `delete_orphan`   | Remove all cues whose slot index exceeds a threshold      | Restore from backup |

All four share the same calling convention: a request body, a per-track
streaming progress event, a final summary, and a database backup that is taken
exactly once before the first write.

The tools are intentionally narrow — they edit **only** existing rows in the
`DjmdCue` table. They do not generate new cues, change the beatgrid, touch the
ANLZ files on disk, or modify track metadata. Use the cue generation pipeline
(`/api/generate-apply-stream`) when you need new cues; use these tools when you
need to mutate the ones you already have.

### When to reach for each operation

- **Rename**: you used "Cue 1 / Cue 2 / Cue 3" as placeholder names during
  analysis and want to standardise on "Intro / Drop / Outro".
- **Recolor**: you settled on a slot-color convention later (e.g. slot A
  always green, slot B always blue) and need to back-fill the rest of the
  library.
- **Shift**: you discovered a systematic beatgrid offset (typically caused by a
  bad encoder pre-roll) — every cue on every affected track is off by the
  same number of milliseconds.
- **Delete orphan cues**: you ran an automated tagger that pushed cues into
  slots E–H and want them gone, keeping only slots A–D.

---

## The four operations

### Rename

Replace the `DjmdCue.Comment` field — the cue name that shows in Rekordbox —
across every selected track. The match is **exact and case-sensitive**.

Schema (`autocue/serve/schemas.py:254`):

```python
class CueRenameParams(BaseModel):
    from_name: str   # exact, case-sensitive match against DjmdCue.Comment
    to_name: str
```

Per-track logic (`autocue/serve/routes.py:995`):

```python
if operation == "rename":
    p = req.rename
    for cue in hot_cues:
        if (cue.Comment or "") == p.from_name:
            if not dry_run:
                cue.Comment = p.to_name
            changed += 1
        else:
            skipped += 1
            reasons["no_match"] = reasons.get("no_match", 0) + 1
```

A cue with no name (`Comment is None`) is treated as the empty string `""`,
which is the only string that will match `from_name=""`. There is no
substring or regex mode — if you need partial matching, run multiple passes.

### Recolor

Set the cue color for specific **slots** (slot A through slot H), not for cue
names. The request body maps slot index strings (`"0"`–`"7"`) to a Rekordbox
color table index (1–8). Slots absent from the mapping are left untouched.

Schema (`autocue/serve/schemas.py:259`):

```python
class CueRecolorParams(BaseModel):
    # Maps slot index string ("0"–"7") to ColorTableIndex (0=none,1=Pink,
    # 2=Red,3=Orange,4=Yellow,5=Green,6=Aqua,7=Blue,8=Purple).
    slot_colors: dict[str, int]
```

Per-track logic (`autocue/serve/routes.py:1006`):

```python
elif operation == "recolor":
    p = req.recolor
    for cue in hot_cues:
        slot_str = str(cue.Kind - 1)  # Kind=1 → slot 0 (A)
        if slot_str in p.slot_colors:
            if not dry_run:
                cue.ColorTableIndex = p.slot_colors[slot_str]
            changed += 1
        else:
            skipped += 1
            reasons["no_match"] = reasons.get("no_match", 0) + 1
```

The CLAUDE.md invariant **`Kind = slot + 1`** is the reason the conversion
`slot_str = str(cue.Kind - 1)` is correct: a DjmdCue row with `Kind=1` lives
in slot A, `Kind=2` in slot B, and so on through `Kind=8 → slot H`.

The web UI defaults the eight slot-color drop-downs to the AutoCue palette
(`docs/index.html:3005`):

```js
const defaults = [5,7,2,4,1,6,3,8];
// A=Green, B=Blue, C=Red, D=Yellow, E=Pink, F=Aqua, G=Orange, H=Purple
```

Sending `0` for a slot (the "—" entry) **omits that slot from the mapping** so
existing colors are preserved.

### Shift

Move every hot cue's position by `delta_ms` milliseconds. Positive shifts move
cues later in the track; negative shifts move them earlier. This is the right
tool for correcting a systematic beatgrid offset that has already been baked
into the cues.

Schema (`autocue/serve/schemas.py:266`):

```python
class CueShiftParams(BaseModel):
    delta_ms: int  # positive = shift later, negative = shift earlier
    negative_policy: Literal["skip", "clamp_to_zero", "abort_track"] = "abort_track"
```

`delta_ms` is rejected at the schema layer if it equals zero
(`autocue/serve/schemas.py:274`):

```python
@field_validator("delta_ms")
@classmethod
def _nonzero(cls, v: int) -> int:
    if v == 0:
        raise ValueError("delta_ms must not be zero")
    return v
```

The implementation updates **both** the millisecond position
(`DjmdCue.InMsec`) and the frame position (`DjmdCue.InFrame`) in lock-step,
and — for loop cues — extends the same shift to the loop end
(`DjmdCue.OutMsec` / `DjmdCue.OutFrame`) so the loop length is preserved:

```python
elif operation == "shift":
    p = req.shift
    policy = p.negative_policy
    if policy == "abort_track":
        if any(int(cue.InMsec or 0) + p.delta_ms < 0 for cue in hot_cues):
            reasons["track_aborted"] = reasons.get("track_aborted", 0) + len(hot_cues)
            return 0, len(hot_cues), reasons
    for cue in hot_cues:
        original_in_ms = int(cue.InMsec or 0)
        new_ms = original_in_ms + p.delta_ms
        if new_ms < 0:
            if policy == "clamp_to_zero":
                new_ms = 0
            else:  # "skip"
                skipped += 1
                reasons["would_be_negative"] = reasons.get("would_be_negative", 0) + 1
                continue
        if not dry_run:
            cue.InMsec = new_ms
            cue.InFrame = round(new_ms * 150 / 1000)
            out_ms = cue.OutMsec if cue.OutMsec is not None else -1
            if out_ms >= 0:
                effective_shift = new_ms - original_in_ms
                new_out_ms = max(0, out_ms + effective_shift)
                cue.OutMsec = new_out_ms
                cue.OutFrame = round(new_out_ms * 150 / 1000)
        changed += 1
```

The three negative-position policies:

| Policy             | Behavior                                                                       |
|--------------------|---------------------------------------------------------------------------------|
| `abort_track`      | (default) If **any** cue on a track would go negative, leave the whole track untouched and increment `track_aborted` by the number of cues on that track. Preserves internal cue-set consistency. |
| `skip`             | Silently drop the cues that would go negative, shift the rest. Adds to `would_be_negative`. |
| `clamp_to_zero`    | Place would-be-negative cues at `0 ms` instead. Cues become a stacked group at the start. |

`abort_track` is the safe default — if you shift `-500 ms` and one early cue
sits at `200 ms`, you almost certainly want the whole track left alone rather
than silently losing that cue.

### Delete orphan cues

Remove every hot cue whose slot index exceeds a threshold. This is the cleanup
tool for libraries that picked up spurious cues from automated taggers or
older Rekordbox imports.

Schema (`autocue/serve/schemas.py:282`):

```python
class CueDeleteOrphanParams(BaseModel):
    keep_slots: int = Field(..., ge=1, le=8)  # delete hot cues whose Kind > keep_slots
```

Per-track logic (`autocue/serve/routes.py:1048`):

```python
elif operation == "delete_orphan":
    p = req.delete_orphan
    for cue in hot_cues:
        if cue.Kind > p.keep_slots:
            if not dry_run:
                db.session.delete(cue)
            changed += 1
        else:
            skipped += 1
            reasons["beyond_keep_slots"] = reasons.get("beyond_keep_slots", 0) + 1
```

`keep_slots=4` deletes every cue in slots E–H (`Kind=5, 6, 7, 8`) and leaves
slots A–D intact. `keep_slots=8` is a no-op (nothing has `Kind > 8`). The
schema rejects `keep_slots < 1` or `> 8`.

The `skip_reasons` counter for skipped cues is named `beyond_keep_slots` —
slightly counter-intuitively, this counts the cues that were **inside** the
keep range (and therefore skipped from deletion). Cues that were deleted
contribute to `cues_changed`.

---

## The endpoint — `POST /api/cue-tools-stream`

A single SSE endpoint dispatches all four operations based on the request
body's `operation` field. The handler lives in `autocue/serve/routes.py:930`.

### Request body — `CueToolsRequest`

```python
class CueToolsRequest(BaseModel):
    operation: Literal["rename", "recolor", "shift", "delete_orphan"]
    track_ids: list[int]
    dry_run: bool = False
    rename: CueRenameParams | None = None
    recolor: CueRecolorParams | None = None
    shift: CueShiftParams | None = None
    delete_orphan: CueDeleteOrphanParams | None = None

    @model_validator(mode="after")
    def _params_present(self):
        required = {"rename": self.rename, "recolor": self.recolor,
                    "shift": self.shift, "delete_orphan": self.delete_orphan}
        if required[self.operation] is None:
            raise ValueError(f"params for '{self.operation}' must be provided")
        return self
```

(`autocue/serve/schemas.py:286`)

| Field            | Type                    | Notes                                                                                              |
|------------------|-------------------------|----------------------------------------------------------------------------------------------------|
| `operation`      | enum string             | One of `rename`, `recolor`, `shift`, `delete_orphan`.                                              |
| `track_ids`      | `list[int]`             | DjmdContent IDs to scan. The web UI passes the IDs of the currently visible tracks.                |
| `dry_run`        | `bool` (default `false`)| `true` skips the Rekordbox-running check, skips backup, and never writes to the DB.                |
| `rename`         | object \| `null`        | Required when `operation == "rename"`.                                                              |
| `recolor`        | object \| `null`        | Required when `operation == "recolor"`.                                                             |
| `shift`          | object \| `null`        | Required when `operation == "shift"`.                                                               |
| `delete_orphan`  | object \| `null`        | Required when `operation == "delete_orphan"`.                                                       |

The model validator returns HTTP 422 if the params for the chosen operation are
omitted — `test_missing_operation_params_returns_422`
(`tests/test_serve_routes.py:1842`) covers this.

> The schema field defaults `dry_run` to `False`, but the **web UI sets the
> "Dry run" checkbox to checked by default**, making interactive use safe by
> default. Programmatic callers must pass `dry_run: true` explicitly to get
> the same safety.

### Response — SSE stream

The response is a `text/event-stream` with two kinds of events.

#### Progress event (every 50 tracks)

```json
{ "processed": 50, "affected": 12, "total": 3247 }
```

The handler buffers per-track progress and emits one progress event every
`BATCH = 50` tracks (`autocue/serve/routes.py:1084`):

```python
if (i + 1) % BATCH == 0:
    yield f"data: {_json.dumps({'processed': processed, 'affected': affected, 'total': total})}\n\n"
```

`affected` is the number of tracks where at least one cue was changed
(i.e. `changed > 0`). `processed` includes tracks whose `get_content(ID=tid)`
returned `None` — those are silently counted as processed but contribute
nothing to `affected`.

#### Terminal event

```json
{
  "done": true,
  "summary": {
    "operation": "rename",
    "tracks_processed": 3247,
    "tracks_affected": 412,
    "cues_changed": 418,
    "cues_skipped": 12894,
    "skip_reasons": { "no_match": 12894 },
    "dry_run": false,
    "backup_path": "master_20260607T143012.db"
  }
}
```

The terminal event matches `CueToolsSummary` (`autocue/serve/schemas.py:304`):

```python
class CueToolsSummary(BaseModel):
    operation: str
    tracks_processed: int
    tracks_affected: int
    cues_changed: int
    cues_skipped: int
    skip_reasons: dict[str, int] = {}
    # Stable reason keys: "would_be_negative" (shift/skip policy),
    # "no_match" (rename/recolor),
    # "track_aborted" (shift/abort_track policy),
    # "beyond_keep_slots" (delete_orphan)
    dry_run: bool
    backup_path: str | None = None
```

`backup_path` is the **bare filename** of the backup, not an absolute path —
the file lives in `~/.autocue/backups/`. This is asserted by
`test_backup_path_is_filename_only` (`tests/test_serve_routes.py:1925`).

### SSE headers

The response carries the headers Starlette/Nginx need to keep the stream
flowing instead of buffering it:

```
Cache-Control: no-cache
X-Accel-Buffering: no
Content-Type: text/event-stream
```

These pass through the `GZipMiddleware` unchanged — see the GZip note in
CLAUDE.md (gzip is skipped automatically for `text/event-stream` responses).

---

## Safety guards

### Backup is created once, up front

Before processing any tracks, the handler copies `master.db` (and its WAL/SHM
sidecars) to `~/.autocue/backups/master_YYYYMMDDTHHMMSS.db`
(`autocue/db_writer.py:14`):

```python
def backup_database(db_path: Path) -> Path:
    """Copy master.db (and WAL/SHM sidecars) to ~/.autocue/backups/master_TIMESTAMP.db."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = BACKUP_DIR / f"master_{ts}.db"
    shutil.copy2(db_path, dest)
    for suf in ("-wal", "-shm"):
        src = Path(str(db_path) + suf)
        if src.exists():
            shutil.copy2(src, Path(str(dest) + suf))
    logger.info("Backup → %s", dest)
    return dest
```

The handler invokes it once at the top of the request (`autocue/serve/routes.py:965`):

```python
backup_path = None
if not req.dry_run:
    try:
        ...
        backup_path = Path(backup_database(db_path)).name  # filename only
    except Exception as e:
        raise HTTPException(500, f"Backup failed — aborting: {e}")
```

If the backup fails, the handler returns HTTP 500 and **no writes happen**.
That's the point: there is exactly one backup-or-abort moment before the SSE
stream starts.

**Dry runs skip the backup**, by design — a dry run never writes, so a backup
would just be noise. `test_rename_dry_run_no_backup`
(`tests/test_serve_routes.py:1735`) covers this. The empty-`track_ids` short
path also skips the backup (`test_empty_track_ids_no_backup`,
`tests/test_serve_routes.py:1908`).

You can list, restore, and delete backups through the standard backup
endpoints (`/api/backups`, `/api/restore`, `DELETE /api/backups/{filename}`) —
see [backup-and-restore](./backup-and-restore.md).

### Rekordbox-running guard

`Rekordbox6Database` cannot write to `master.db` while Rekordbox itself has
the SQLCipher file open. The handler checks first (`autocue/serve/routes.py:942`):

```python
if rekordbox_is_running() and not req.dry_run:
    raise HTTPException(409, "Rekordbox is running — close it before editing cues")
```

The check uses `psutil` to scan the process list for any process whose name
contains "rekordbox" (case-insensitive) — see `autocue/db_writer.py:28`.

The guard is **skipped on dry runs** (`test_dry_run_skips_rekordbox_check`,
`tests/test_serve_routes.py:1706`). You can safely preview a rename or shift
while Rekordbox is still open; you just can't commit it.

### Dry-run default in the UI

The web UI checkbox **"Dry run (preview only)" is checked by default**
(`docs/index.html:1887`):

```html
<input type="checkbox" id="cue-tools-dry-run" checked>
```

The user has to actively uncheck it before the operation can mutate the
database. This is the same convention the Library Health auto-fix tools use
and is the primary brake against accidental bulk edits.

### Destructive operation confirmation

For the two operations whose effects are not visually obvious in Rekordbox —
**shift** and **delete_orphan** — the UI requires a second `window.confirm()`
before sending the request, on top of the dry-run checkbox
(`docs/index.html:3043`):

```js
// Require confirmation before destructive writes (delete_orphan or shift when not dry-run)
if (!dryRun && (op === 'delete_orphan' || op === 'shift')) {
  const opLabel = op === 'delete_orphan' ? 'delete cues' : 'shift cues';
  if (!window.confirm(`Apply ${opLabel} to ${total} track${total === 1 ? '' : 's'}? A backup will be created first.`)) return;
}
```

`rename` and `recolor` skip the confirm dialog because both effects are
immediately visible in Rekordbox (a wrong color or name is one click to
spot and easy to roll back via the backup), whereas shift and delete can be
silently lost in a wall of unchanged-looking cues.

### Transactional safety

The handler commits **once**, after every track has been processed
(`autocue/serve/routes.py:1087`):

```python
if not dry_run:
    db.session.commit()  # single commit for entire batch
```

If the client disconnects mid-stream (a `GeneratorExit` reaches the generator),
the handler catches the `BaseException`, rolls back the session, and re-raises
so SQLAlchemy doesn't carry dirty rows into the next request
(`autocue/serve/routes.py:1090`):

```python
except BaseException:  # includes GeneratorExit (client disconnect)
    db.session.rollback()
    raise
```

This is why "client cancels the request" doesn't leave the database in a
half-written state — every change either lands together at the final commit
or is rolled back together.

---

## Frame math and DB invariants

Two CLAUDE.md invariants drive the cue-shift math.

### `InFrame = round(position_ms * 150 / 1000)`

Rekordbox stores cue positions in two parallel units inside `DjmdCue`:

- `InMsec` — milliseconds since the start of the track (integer).
- `InFrame` — Rekordbox's internal "frames" unit, 150 frames per second.

When you shift a cue by `delta_ms`, you must update **both** fields:

```python
cue.InMsec  = new_ms
cue.InFrame = round(new_ms * 150 / 1000)
```

If you update one without the other Rekordbox may pick whichever it cached
when scanning the track, and the visible cue position will drift relative to
the audio. The handler is careful to update both fields atomically inside the
same per-cue assignment block.

### Loop cues — `OutFrame` and `OutMsec`

A non-loop hot cue stores `OutMsec = -1` and `OutFrame = -1` — sentinel values
meaning "no out point". A loop cue stores the actual loop-end position in
those fields.

The shift handler walks the same two-field shift on the out point, gated on
the sentinel (`autocue/serve/routes.py:1039`):

```python
out_ms = cue.OutMsec if cue.OutMsec is not None else -1
if out_ms >= 0:
    # Use effective shift to keep loop length intact
    effective_shift = new_ms - original_in_ms
    new_out_ms = max(0, out_ms + effective_shift)
    cue.OutMsec = new_out_ms
    cue.OutFrame = round(new_out_ms * 150 / 1000)
```

This preserves the loop length even when `clamp_to_zero` is the negative
policy and the in-point ended up clamped — the loop end uses the
`effective_shift` (which may be less than `delta_ms` after the clamp), so
the loop never bleeds backward past zero.

`test_shift_preserves_loop_length` (`tests/test_serve_routes.py:1871`) asserts
that a 2-second loop starting at `10000ms` shifted by `+500ms` ends up with
`InMsec=10500`, `OutMsec=12500` — same 2000ms duration.

`test_shift_leaves_sentinel_out_msec_unchanged` (`tests/test_serve_routes.py:1890`)
asserts that `OutMsec=-1` is never re-written to a real value by the shift —
non-loop cues stay non-loop cues.

### Color resolution — `ColorTableIndex`

Cue colors are stored in `DjmdCue.ColorTableIndex` as integers 0–8 (0 = no
color, 1 = Pink, 2 = Red, 3 = Orange, 4 = Yellow, 5 = Green, 6 = Aqua, 7 = Blue,
8 = Purple). These map directly to the sort keys in `DjmdColor`.

> Don't confuse `DjmdCue.ColorTableIndex` (the cue color, an integer) with
> `DjmdContent.ColorID` (the track tint, a VARCHAR(255) FK to `djmdColor.ID`).
> Cue tools only ever touch `ColorTableIndex`. The track-level color is
> handled by the separate `/api/color-tracks` endpoint and resolved through
> the `DjmdColor` SortKey-to-ID mapping documented in CLAUDE.md.

### `Kind = slot + 1`, and memory cues are excluded

`DjmdCue.Kind` is the slot encoding:

| Kind | Slot       | Treated by cue tools? |
|------|------------|------------------------|
| 0    | Memory cue | **No** — excluded by `Kind >= 1` filter |
| 1    | A          | Yes |
| 2    | B          | Yes |
| ...  | ...        | ... |
| 8    | H          | Yes |

The query that loads cues for each track filters `Kind >= 1 AND Kind <= 8`
(`autocue/serve/routes.py:986`):

```python
hot_cues = (
    db.session.query(DjmdCue)
    .filter(DjmdCue.ContentID == content_id,
            DjmdCue.Kind >= 1, DjmdCue.Kind <= 8)
    .all()
)
```

Memory cues (CDJ Auto Cue points, `Kind=0`, `CuePoint.slot = -1` in AutoCue's
own model) are **never touched** by any of the four bulk operations. This is
deliberate: a memory cue is the "starting position" Rekordbox uses when a
track loads on a CDJ, and renaming/recoloring/shifting/deleting it would
break that contract. `test_memory_cues_are_excluded`
(`tests/test_serve_routes.py:1826`) pins this invariant.

If you need to wipe memory cues, use the dedicated `/api/delete-cues` flow,
not the cue tools.

---

## UI surface

The "Cue Library Tools" panel lives at the top of the Library tab and is shown
only when the page detects a local server (`docs/index.html:4033`). The panel
is a single `<section>` with:

- An operation dropdown (`<select id="cue-tools-op">`) with five entries —
  the four operations plus "Auto-classify tracks" (which is a shortcut to the
  Auto-Tag flow, dispatched through the same Run button but routed to a
  different endpoint).
- A "Dry run (preview only)" checkbox, checked by default.
- Four operation-specific parameter rows (`#cue-tools-params-rename`,
  `…-recolor`, `…-shift`, `…-delete-orphan`) — only one is visible at a time;
  the others are hidden via `style.display = 'none'` in
  `_updateCueToolsParams()` (`docs/index.html:3017`).
- A "Run on visible tracks" button. The label flips to "Tag visible tracks"
  when the auto-classify entry is selected.
- A 3px progress bar (`.cue-tools-progress`) that fills as the SSE stream
  emits per-batch events.
- A result panel that renders the final summary.

The Run button collects `track_ids` from `activeTracks()` — the currently
filtered/visible track list — not from `parsedTracks`. This means rename,
recolor, shift, and delete operations are scoped to whatever the search bar
and filter chips have left visible.

The SSE consumer in `_runCueTools()` (`docs/index.html:3082`) reads the
stream via `fetch` + `ReadableStream` (not `EventSource`, because the request
is a POST), buffers partial chunks on `\n\n`, and updates the button label
and progress fill on each batch event. The final `{done:true,summary}` event
populates the result panel and removes the spinner.

---

## Performance

The handler is intentionally simple:

1. Backup the DB (one shutil copy of `master.db` + WAL/SHM sidecars).
2. For each `track_id` in the request, call `db.get_content(ID=tid)` and then
   `db.session.query(DjmdCue).filter(...).all()` to fetch that track's hot
   cues.
3. Mutate them in Python.
4. Yield a progress event every 50 tracks.
5. Commit once at the end.

There is **no `IN (...)` query** that fetches all cues for the whole batch in
one go. The per-track query is the unit of work, and the same pattern repeats
3000 times for a full-library run. On a typical SSD-backed Rekordbox install,
the bottleneck is `db.get_content()` rather than the cue mutation itself.

The batch flush at `BATCH = 50` is a tuning choice — small enough that the
progress bar feels live, large enough that SSE framing overhead doesn't
dominate the total time. The size of each progress payload is constant, so a
larger library does not produce larger events — just more of them.

The per-track exception handler (`autocue/serve/routes.py:1080`) means **one
bad row does not abort the stream**:

```python
try:
    changed, skipped_count, reasons = _process_track(content.ID)
    ...
except Exception as exc:
    logger.error("cue-tools %s failed for track %d: %s", operation, tid, exc)
    processed += 1
```

The failed track contributes nothing to `cues_changed` / `cues_skipped` /
`tracks_affected`, but `processed` still increments so the progress bar keeps
moving.

---

## Edge cases

### Shift to a negative position

Covered by `negative_policy`:

- `abort_track` (default): the entire track is left untouched. All cues on
  that track count toward `skip_reasons["track_aborted"]`. `cues_changed` is
  unaffected for the track.
- `skip`: just the would-be-negative cues are skipped. Other cues on the
  same track still shift. `skip_reasons["would_be_negative"]` increments.
- `clamp_to_zero`: would-be-negative cues are placed at `0 ms`. Multiple
  pre-zero cues collapse to a single stacked group at the start of the track.

`test_shift_skips_cue_that_would_go_negative` (`tests/test_serve_routes.py:1770`)
exercises the `skip` path with a default policy (note: the test sends no
explicit policy, so the schema default `abort_track` applies — but with one
cue on the track, `abort_track` and `skip` produce the same observable
counts).

### Rename with no matches anywhere

The stream still runs to completion, every track is reported as processed,
and the summary returns `cues_changed: 0`, `cues_skipped: <total cues>`,
`skip_reasons: {"no_match": N}`. The backup is still created (assuming
`dry_run=false`) because the handler doesn't know in advance whether any
match exists.

If you want to avoid the unnecessary backup, run the same request first with
`dry_run: true` — the dry-run skips the backup and tells you exactly how
many cues will match.

### `delete_orphan` on tracks with no cues beyond `keep_slots`

The query returns the existing cues, none of them satisfy `Kind > keep_slots`,
and `skip_reasons["beyond_keep_slots"]` increments by the total cue count.
`tracks_affected` stays `0` for that track. `tracks_processed` still
increments — the empty-result case is treated as "successfully scanned and
nothing to do", not as an error.

### Multiple cues with the same name on one track

`rename` matches every cue with `Comment == from_name` independently. If a
track has three cues all called "Cue 1", all three are renamed to `to_name`
and contribute three `cues_changed`. This is intentional — the operation is
defined per-cue, not per-track.

### Empty `track_ids` array

The handler short-circuits before backup or scan and emits a single
terminal event:

```json
{"done": true, "summary": {"operation": "rename", "tracks_processed": 0, ...}}
```

No backup is created. `test_empty_track_ids_no_backup`
(`tests/test_serve_routes.py:1908`) pins this.

### Track ID not in the library

`db.get_content(ID=tid)` returns `None`. The handler increments `processed`
and moves on. The summary reports the track as processed but never affected.

---

## Examples

### Rename "Drop" to "Drop 1" library-wide (dry run first)

```bash
# Preview
curl -N -X POST http://localhost:7432/api/cue-tools-stream \
  -H 'Content-Type: application/json' \
  -d '{
        "operation": "rename",
        "track_ids": [101, 102, 103, 104],
        "dry_run": true,
        "rename": {"from_name": "Drop", "to_name": "Drop 1"}
      }'
```

The stream emits progress every 50 tracks, then a terminal event:

```json
{"done": true, "summary": {
  "operation": "rename",
  "tracks_processed": 4,
  "tracks_affected": 3,
  "cues_changed": 3,
  "cues_skipped": 9,
  "skip_reasons": {"no_match": 9},
  "dry_run": true,
  "backup_path": null
}}
```

Three of the four tracks had a cue named "Drop" — rename it for real by
re-sending the same request with `"dry_run": false`. Make sure Rekordbox is
closed first.

### Shift every cue forward 25 ms (beatgrid correction)

```bash
curl -N -X POST http://localhost:7432/api/cue-tools-stream \
  -H 'Content-Type: application/json' \
  -d '{
        "operation": "shift",
        "track_ids": [101, 102, 103],
        "dry_run": false,
        "shift": {"delta_ms": 25, "negative_policy": "abort_track"}
      }'
```

The handler updates `InMsec` and `InFrame` on every cue, plus `OutMsec` and
`OutFrame` on any loop cues — keeping loop lengths constant. `delta_ms = 25`
is positive, so `negative_policy` is moot; if you were shifting backward
(`delta_ms = -25`), `abort_track` would skip any track whose first cue lives
below `25 ms`.

### Delete cues in slots E–H

```bash
curl -N -X POST http://localhost:7432/api/cue-tools-stream \
  -H 'Content-Type: application/json' \
  -d '{
        "operation": "delete_orphan",
        "track_ids": [101, 102, 103, 104],
        "dry_run": false,
        "delete_orphan": {"keep_slots": 4}
      }'
```

Slots A–D (`Kind 1–4`) are preserved, slots E–H (`Kind 5–8`) are deleted.
Memory cues (`Kind=0`) are not in the query result and stay untouched.

---

## Related

- [Cue generation](./cue-generation.md) — the inverse direction: generating new
  cues from phrase / bar / heuristic analysis (`/api/generate-apply-stream`).
- [Library health](./library-health.md) — the diagnostic pass that surfaces
  candidate tracks for cue tools (no-cues, duplicate cues, unnamed cues).
- [Backup and restore](./backup-and-restore.md) — managing the
  `~/.autocue/backups/` directory created by every non-dry-run cue-tools
  invocation.
- [REST API reference](./rest-api.md) — full list of endpoints, schemas, and
  SSE patterns including the `_consumeSSE` helper used in the web UI.

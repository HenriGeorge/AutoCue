# Backup and Restore

AutoCue's safety net. Every operation that mutates `master.db` is preceded by a
timestamped copy of the database to `~/.autocue/backups/`. Restore is a
one-click operation that swaps the live database back to any prior snapshot and
invalidates every in-process analysis cache so feature vectors built against the
old DB cannot leak into the restored state.

This document covers the on-disk layout, the API surface, the server-side
guarantees, and the failure modes you should know about before you trust a
recovery.

## Table of Contents

- [1. Overview](#1-overview)
- [2. Backup directory](#2-backup-directory)
- [3. Backup file naming](#3-backup-file-naming)
- [4. `backup_database(db_path)`](#4-backup_databasedb_path)
- [5. When backups are made](#5-when-backups-are-made)
- [6. Rekordbox-running guard](#6-rekordbox-running-guard)
- [7. `GET /api/backups`](#7-get-apibackups)
- [8. `POST /api/restore`](#8-post-apirestore)
- [9. `DELETE /api/backups/{filename}`](#9-delete-apibackupsfilename)
- [10. UI surface](#10-ui-surface)
- [11. WAL/SHM handling](#11-walshm-handling)
- [12. Failure modes](#12-failure-modes)
- [13. `autocue serve` startup](#13-autocue-serve-startup)
- [14. Stale analysis state after restore](#14-stale-analysis-state-after-restore)
- [15. Examples](#15-examples)
- [16. Manual recovery](#16-manual-recovery)
- [17. Retention](#17-retention)
- [18. Testing](#18-testing)
- [19. Related](#19-related)

---

## 1. Overview

AutoCue writes directly to Rekordbox's SQLite (SQLCipher) database in local-server
mode (`autocue serve`). Every write is potentially destructive — there is no
undo at the Rekordbox level for:

- Hot cues (`/api/apply`, `/api/generate-apply`, `/api/generate-apply-stream`)
- Hot-cue deletion (`/api/delete-cues`)
- Track colors (`/api/color-tracks`, `/api/color-tracks-stream`)
- Cue Library Tools — rename / recolor / shift / delete-orphan
  (`/api/cue-tools-stream`)
- Track comments (`/api/enrich-comments`, `/api/enrich-comments/stream`)
- My Tags (`/api/auto-tag`, `/api/auto-tag/discogs`)

To make every write reversible, AutoCue runs a **rolling backup strategy**:

1. Before the *first* write inside any of the endpoints above, the live
   `master.db` (plus its WAL/SHM sidecars) is copied to a new timestamped file
   under `~/.autocue/backups/`.
2. The backup path is returned to the caller in the response body
   (`backup_path` field on `ApplyResponse`, `DeleteResponse`,
   `ColorTracksResponse`, `EnrichCommentsResponse`, `CueToolsSummary`, etc.).
3. Backups are **never** auto-deleted. The UI surfaces a Backups panel where
   the user can browse, multi-select, and prune old snapshots.

If the backup itself fails, the write is aborted with `HTTP 500` and **no
mutation happens** — see `serve/routes.py:342-344`, `routes.py:413-415`,
`routes.py:482-484`. AutoCue refuses to write without a backup in place.

`dry_run = true` skips the backup entirely; the schema flag on every write
request bypasses the backup branch (`routes.py:331`, `routes.py:403`,
`routes.py:474`, `routes.py:639`, `routes.py:680`, `routes.py:711`,
`routes.py:965`).

---

## 2. Backup directory

All backups live under a single user-scoped directory:

```
~/.autocue/backups/
```

Declared as the module-level constant `BACKUP_DIR` in `autocue/db_writer.py:10`:

```python
BACKUP_DIR = Path.home() / ".autocue" / "backups"
```

The directory is created **lazily** by `backup_database()` on first write
(`db_writer.py:16`):

```python
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
```

If the user has never run a write operation, the directory does not exist
and `GET /api/backups` returns `[]` without erroring
(`routes.py:531-532`).

`BACKUP_DIR` is patched in tests via
`unittest.mock.patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups")` —
see `tests/test_db_writer.py:60`, `test_serve_routes.py:757`. The path is
imported by name from `db_writer` inside each endpoint so test patches
take effect at call time.

---

## 3. Backup file naming

Each backup is a single SQLite file with a timestamp embedded in the stem:

```
master_YYYYMMDDTHHMMSS.db
```

The timestamp is **local time** (`datetime.now()`), not UTC — see
`db_writer.py:17`:

```python
ts = datetime.now().strftime("%Y%m%dT%H%M%S")
dest = BACKUP_DIR / f"master_{ts}.db"
```

Example: `master_20260607T143012.db` (June 7 2026, 14:30:12 local time).

### Sidecars

Rekordbox runs the SQLCipher database in **WAL journal mode**, so two sidecar
files may live next to `master.db`:

- `master.db-wal` — write-ahead log
- `master.db-shm` — shared memory index for the WAL

`backup_database()` copies both sidecars when present
(`db_writer.py:20-23`):

```python
for suf in ("-wal", "-shm"):
    src = Path(str(db_path) + suf)
    if src.exists():
        shutil.copy2(src, Path(str(dest) + suf))
```

The result is a **self-consistent snapshot** that can be reopened without
relying on uncommitted WAL state in the live file. Sidecars are optional —
if WAL is absent (e.g. checkpointed), only the `.db` file is copied
(verified by `test_db_writer.py::test_ok_when_wal_absent`,
`tests/test_db_writer.py:104-114`).

---

## 4. `backup_database(db_path)`

The entire backup primitive is six lines.

**Signature** (`autocue/db_writer.py:14`):

```python
def backup_database(db_path: Path) -> Path:
    """Copy master.db (and WAL/SHM sidecars) to ~/.autocue/backups/master_TIMESTAMP.db."""
```

**Behaviour**:

- Creates `BACKUP_DIR` if it does not exist.
- Computes a timestamp via `datetime.now().strftime("%Y%m%dT%H%M%S")`.
- Calls `shutil.copy2(db_path, dest)` — preserves mtime and permission bits.
- Iterates `("-wal", "-shm")` and copies each sidecar if it exists.
- Logs `"Backup → <path>"` at `INFO` level.
- **Returns the `Path` object pointing at the new `.db` file.** Tests assert
  the return is a `Path` and the file exists (`test_db_writer.py:78-86`).

**Failure mode**: any `shutil.copy2` exception (permission denied, disk full,
source missing) propagates out. Endpoints wrap the call in `try`/`except` and
re-raise as `HTTPException(500, f"Backup failed — aborting: {e}")` — see
`routes.py:343-344`. The write loop is never reached when the backup throws.

---

## 5. When backups are made

Every endpoint that writes to `master.db` calls `backup_database()` **once**
before the per-track loop. The call site pattern is identical across endpoints:

```python
backup_path = None
if not req.dry_run:
    try:
        db_dir = getattr(db, "_db_dir", None)
        if db_dir is None:
            raise RuntimeError("Cannot locate master.db: …")
        db_path = Path(db_dir) / "master.db"
        if not db_path.exists():
            raise FileNotFoundError(f"master.db not found at {db_path}")
        backup_path = str(backup_database(db_path))
    except Exception as e:
        raise HTTPException(500, f"Backup failed — aborting: {e}")
```

Full list of writers that back up:

| Endpoint | Source | Notes |
| --- | --- | --- |
| `POST /api/apply` | `routes.py:322-379` | One backup per request. |
| `POST /api/generate-apply` | `routes.py:382-446` | Single backup; per-track write loop after. |
| `POST /api/generate-apply-stream` (SSE) | `routes.py:449-523` | Backup made *before* the SSE generator is returned, so a slow client cannot defer the snapshot. |
| `POST /api/delete-cues` | `routes.py:631-670` | Same pattern. |
| `POST /api/color-tracks` | `routes.py:673-699` | Same pattern. |
| `POST /api/color-tracks-stream` (SSE) | `routes.py:702-768` | Same pattern; one commit at end. |
| `POST /api/cue-tools-stream` (SSE) | `routes.py:930-1110` | Backup made for non-empty `track_ids` only; the empty-payload short-circuit at `routes.py:945-962` returns immediately without writing. `backup_path` here is the *filename only* (`Path(...).name`) — see `routes.py:974`. |
| `POST /api/enrich-comments` | (comment-enrichment routes) | One backup per request, made by the route. |
| `POST /api/enrich-comments/stream` (SSE) | (comment-enrichment routes) | Backup made once up front; per-track commit happens inside the loop so a single track failure no longer rolls back the whole batch. |
| `POST /api/auto-tag` | (auto-tag routes) | One backup per request. |
| `POST /api/auto-tag/discogs` (SSE) | (auto-tag routes) | One backup per SSE run. |

**Not backed up** (read-only or no `master.db` writes):

- `GET /api/status`, `GET /api/playlists`, `GET /api/tracks`,
  `GET /api/tracks/{id}/health`, `GET /api/health` (SSE),
  `GET /api/tracks/{id}/energy`, `GET /api/tracks/{id}/mixability`,
  `GET /api/tracks/{id}/classification`, `GET /api/classify`,
  `GET /api/tracks/{id}/similar`, `POST /api/transitions/score`,
  `POST /api/setbuilder`, `GET /api/setbuilder/alternatives`,
  `POST /api/playlists/suggest`, `GET /api/discover`,
  `GET /api/download/config`, `POST /api/download`, `POST /api/download/album`.
- `POST /api/playlists` (create) — adds rows but Rekordbox treats new
  playlists as additive and the operation is reversible from the UI.

`autocue serve` startup does **not** create a backup; the lifespan handler
only opens the DB connection. Backups only happen at the moment of write.

---

## 6. Rekordbox-running guard

`master.db` is SQLCipher-encrypted and exclusively locked while the Rekordbox
desktop app is open. Writes during that window corrupt the database in
practice.

Every write endpoint checks `rekordbox_is_running()` first and refuses with
`HTTP 409` if true (`routes.py:327-328`, `routes.py:389-390`,
`routes.py:460-461`, `routes.py:635-636`, `routes.py:677-678`,
`routes.py:708-709`, `routes.py:942-943`). The guard also applies to restore
(`routes.py:557-558`).

**Implementation** (`autocue/db_writer.py`): two-signal check combining a
process-name probe with a real file-lock attempt on `master.db`.

```python
def rekordbox_is_running(db_path: Path | str | None = None) -> bool:
    """Return True if Rekordbox appears to be running.

    Two signals are combined:

    1. A psutil process-name probe (fast, but a renamed Rekordbox
       build can slip through).
    2. An exclusive fcntl/msvcrt lock attempt on master.db when
       ``db_path`` is supplied (catches renamed builds and races
       where the process started after the name check fired).
    """
    if _process_name_check():
        return True
    if db_path is not None and _db_file_is_locked(db_path):
        return True
    return False
```

- **Process-name probe** (`_process_name_check`): `psutil.process_iter(["name"])`
  matches `"rekordbox"` substring case-insensitively. Catches `rekordbox`,
  `Rekordbox`, and `rekordboxAgent`. Returns `False` on `ImportError`
  (psutil missing — defense for unusual installs) or any other exception
  (fail open — defer to the file-lock check).
- **File-lock probe** (`_db_file_is_locked`): opens `master.db` and tries
  `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on Unix-likes or
  `msvcrt.locking(fd, LK_NBLCK, 1)` on Windows. If the lock attempt
  fails with `BlockingIOError` / `OSError`, somebody else has the file —
  Rekordbox is in. Lock is released immediately when the attempt
  succeeds. Permission denied or a missing file falls back to `False`
  so the process probe remains the safety net.
- **Why two signals?** The process probe is defeated by renamed
  Rekordbox builds (custom forks, dev builds, third-party shells) and by
  the race where Rekordbox opens *between* the probe firing and the
  write hitting SQLite. The file-lock probe closes both gaps.
- **Routes plumbing**: `serve/routes.py:_rb_running(db)` wraps the call
  with `db._db_dir / "master.db"`, so every write endpoint gets the
  lock check automatically. The wrapper imports `db_writer` lazily so
  tests that patch `autocue.db_writer.rekordbox_is_running` still take
  effect.
- **Backwards compatibility**: callers that pass no argument
  (`rekordbox_is_running()`) keep the legacy process-only behaviour.
- Tests stub via `patch("autocue.db_writer.rekordbox_is_running",
  return_value=...)`; the new lock probe is covered separately in
  `tests/test_db_writer.py::TestRekordboxIsRunning::
  test_db_path_lock_check_catches_renamed_process` (locks `master.db`
  with `fcntl`, asserts the helper returns `True` even with an empty
  process iterator).

When the guard fires, the client receives:

```json
HTTP/1.1 409 Conflict
{"detail": "Rekordbox is running — close it before applying cues"}
```

UI behaviour: the JS catches the 409 and shows a toast telling the user to
quit Rekordbox before retrying.

---

## 7. `GET /api/backups`

Lists all backup files in `BACKUP_DIR`, newest first.

**Source**: `serve/routes.py:526-548`.

```python
@router.get("/backups", response_model=list[BackupItem])
def list_backups():
    ...
    if not BACKUP_DIR.exists():
        return []
    items = []
    for p in sorted(BACKUP_DIR.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True):
        stat = p.stat()
        m = re.search(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})", p.stem)
        if m:
            yr, mo, dy, hh, mm, ss = m.groups()
            created_at = f"{yr}-{mo}-{dy} {hh}:{mm}:{ss}"
        else:
            created_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        items.append(BackupItem(...))
    return items
```

**Behaviour**:

- Returns `[]` (HTTP 200) when `BACKUP_DIR` does not exist
  (`routes.py:531-532`; covered by
  `test_serve_routes.py::TestBackups::test_returns_empty_list_when_no_backups`).
- Globs `*.db` only — sidecars (`*.db-wal`, `*.db-shm`) are not surfaced
  individually because they are auxiliary to the `.db` file.
- Sort key is `stat().st_mtime`, newest first (covered by
  `test_returns_backup_files_sorted_newest_first`,
  `test_serve_routes.py:763-776`).
- `created_at` is parsed from the filename pattern
  `YYYYMMDDTHHMMSS` when present (the normal AutoCue-created case). Files
  that lack that pattern fall back to `mtime` formatted the same way.

**Response schema** — `BackupItem` (`serve/schemas.py:94-98`):

```python
class BackupItem(BaseModel):
    path: str          # full filesystem path
    filename: str      # bare filename
    size_mb: float     # rounded to two decimals
    created_at: str    # "YYYY-MM-DD HH:MM:SS"
```

---

## 8. `POST /api/restore`

Replaces the live `master.db` with a named backup, then reopens the DB and
clears every analysis cache.

**Source**: `serve/routes.py:551-611`.

**Request** (`schemas.py:101-103`):

```python
class RestoreRequest(BaseModel):
    filename: str
```

`filename` is a **bare filename** only. The server rejects anything containing
`/`, `\`, or `..`. Validation runs before any filesystem access
(`routes.py:561-562`):

```python
if "/" in req.filename or "\\" in req.filename or ".." in req.filename:
    raise HTTPException(400, "Invalid filename")
backup_path = (BACKUP_DIR / req.filename).resolve()
if not str(backup_path).startswith(str(BACKUP_DIR.resolve())):
    raise HTTPException(400, "Invalid backup path")
```

Both checks are needed. The string check kills the obvious traversal
attempts; the `resolve()` + `startswith` check defeats symlink trickery.

**Restore sequence** (the critical invariant):

1. Reject `409` if `rekordbox_is_running()`.
2. Validate `filename` (no path separators / dot-dot).
3. Resolve `backup_path` and confirm it sits inside `BACKUP_DIR`.
4. `404` if the backup file does not exist.
5. Close the live connection: `db.session.close()` then
   `db._engine.dispose()`. This flushes the SQLite WAL and releases the file
   handles so the `.db` can be overwritten on Windows and macOS without
   "file in use" errors.
6. `shutil.copy2(backup_path, db_path)` overwrites `master.db`.
7. WAL/SHM sidecars are **kept in sync** (`routes.py:584-590`):

   ```python
   for suf in ("-wal", "-shm"):
       src = Path(str(backup_path) + suf)
       dst = Path(str(db_path) + suf)
       if src.exists():
           shutil.copy2(src, dst)
       elif dst.exists():
           dst.unlink()
   ```

   If the backup has a `-wal` file, it overwrites the live one. If the backup
   does **not** have a `-wal` (a clean checkpointed snapshot) and the live DB
   currently has one, that stale `-wal` is **deleted** to prevent it being
   replayed against the restored DB. Same logic for `-shm`.
8. Reopen via `Rekordbox6Database(db_path.parent)` and assign the new
   handle to `request_obj.app.state.db`.
9. **Clear every analysis cache** so feature vectors and classification
   scores from the pre-restore DB do not leak into the restored DB
   (`routes.py:603-609`):

   ```python
   from ..analysis import energy as _energy_mod, classify as _classify_mod, score as _score_mod
   from ..analysis import similar as _similar_mod
   _energy_mod.clear_cache()
   _classify_mod._class_cache.clear()
   _score_mod._mixability_cache.clear()
   _similar_mod.clear_index()
   ```

**Response** (`schemas.py:105-107`):

```python
class RestoreResponse(BaseModel):
    restored: bool
    message: str
```

A successful call returns `{"restored": true, "message": "Restored from <filename>"}`.

**Error map**:

| Status | Trigger | Source |
| --- | --- | --- |
| `400` | `filename` contains `/`, `\`, or `..`; or resolved path escapes `BACKUP_DIR` | `routes.py:561-565` |
| `404` | Backup file does not exist | `routes.py:566-567` |
| `409` | Rekordbox is running | `routes.py:557-558` |
| `500` | `_db_dir` missing on db object; `shutil.copy2` raises; or `Rekordbox6Database()` fails to reopen | `routes.py:569-571`, `:591-592`, `:599-601` |

A `500` from the reopen branch is special: the **file copy succeeded** but the
connection could not be re-established. `app.state.db` is set to `None` so the
next request fails fast rather than using a stale handle.

---

## 9. `DELETE /api/backups/{filename}`

Removes a single backup file plus its WAL/SHM sidecars.

**Source**: `serve/routes.py:614-628`.

```python
@router.delete("/backups/{filename}")
def delete_backup(filename: str):
    backup_path = (BACKUP_DIR / filename).resolve()
    if not str(backup_path).startswith(str(BACKUP_DIR.resolve())):
        raise HTTPException(400, "Invalid backup filename")
    if not backup_path.exists():
        raise HTTPException(404, f"Backup not found: {filename}")
    backup_path.unlink()
    for suf in ("-wal", "-shm"):
        sidecar = Path(str(backup_path) + suf)
        if sidecar.exists():
            sidecar.unlink()
    return {"deleted": filename}
```

Path traversal protection is the resolved-path-`startswith`-`BACKUP_DIR`
check. Unlike `/api/restore`, this endpoint does not pre-screen for `/` or
`..` substrings in the filename — the `resolve() + startswith` check is
sufficient on its own because any traversal resolves outside `BACKUP_DIR` and
gets rejected with `400`.

The Rekordbox-running guard is **not** applied to deletes. Deleting old
backups does not touch `master.db` and is always safe.

The response is a minimal JSON `{"deleted": "<filename>"}`.

---

## 10. UI surface

The Backups panel in `docs/index.html` exposes:

- A scrollable list of `BackupItem` rows: `filename · size_mb · created_at`.
- Per-row **Restore** button → `POST /api/restore` with the bare filename.
- A checkbox on each row, wired through `_populateChecklist()`,
  `_updateSelectionCount()`, and `_checkedBackups()` helpers (see
  CLAUDE.md notes on multi-select backup delete).
- A **Delete selected** button that issues one
  `DELETE /api/backups/{filename}` per checked entry, then shows a
  consolidated toast like `Deleted 3 backups`.

Restore confirmation is mandatory in the UI — the user gets a warning that the
current library state will be replaced.

After a successful restore, the UI reloads `/api/tracks`, `/api/playlists`,
and `/api/tags` to reflect the restored DB. Track cards are re-rendered from
scratch (cached `_cardMap` cleared via the `'tracks'` signal on `AppState`).

---

## 11. WAL/SHM handling

The WAL/SHM dance is the trickiest part of the feature. Both `backup_database`
and the restore handler treat the sidecars as first-class state.

**Backup pattern** (`db_writer.py:20-23`):

```python
for suf in ("-wal", "-shm"):
    src = Path(str(db_path) + suf)
    if src.exists():
        shutil.copy2(src, Path(str(dest) + suf))
```

**Restore pattern** (`routes.py:584-590`):

```python
for suf in ("-wal", "-shm"):
    src = Path(str(backup_path) + suf)
    dst = Path(str(db_path) + suf)
    if src.exists():
        shutil.copy2(src, dst)
    elif dst.exists():
        dst.unlink()
```

The asymmetry matters:

- On **backup**, missing sidecars are silently skipped (the snapshot is
  internally consistent without them).
- On **restore**, missing sidecars are **deleted** from the live location.
  Leaving a stale `-wal` next to the freshly restored `.db` would cause
  SQLite to replay uncommitted writes from a *different* version of the DB on
  next open — silent corruption.

**Delete pattern** (`routes.py:624-627`):

```python
for suf in ("-wal", "-shm"):
    sidecar = Path(str(backup_path) + suf)
    if sidecar.exists():
        sidecar.unlink()
```

Both sidecars are unlinked along with the `.db` file so the backup directory
never accumulates orphaned WAL files. This is verified by
`tests/test_db_writer.py::test_copies_wal_and_shm_if_present`
(`test_db_writer.py:88-102`).

---

## 12. Failure modes

| Symptom | Likely cause | Server response |
| --- | --- | --- |
| `HTTP 500 — Backup failed — aborting: [Errno 13] Permission denied` | `~/.autocue/backups/` not writable by user. | Write aborted. No DB mutation. |
| `HTTP 500 — Backup failed — aborting: master.db not found at <path>` | Rekordbox library directory moved or unset. | Write aborted. |
| `HTTP 500 — Backup failed — aborting: [Errno 28] No space left on device` | Disk full when copying live DB. | Write aborted. |
| `HTTP 409 — Rekordbox is running — close it before <op>` | `psutil` saw a `rekordbox*` process. | All writes and `/api/restore` refused. |
| `HTTP 404 — Backup '<name>' not found` | Filename does not exist in `BACKUP_DIR`. | Restore refused. |
| `HTTP 400 — Invalid filename` | `/`, `\`, or `..` in `req.filename`. | Restore refused. |
| `HTTP 400 — Invalid backup path` | Resolved path escapes `BACKUP_DIR` (symlink, race). | Restore refused. |
| `HTTP 500 — Restore failed: ...` | `shutil.copy2` raised after the engine was disposed. | Database is now inconsistent — manual recovery required (see [Manual recovery](#16-manual-recovery)). |
| `HTTP 500 — Restore succeeded but could not reopen database: ...` | File copy succeeded; `Rekordbox6Database()` failed. | `app.state.db = None`. Next request will fail; restart `autocue serve`. |
| Corrupted backup (truncated, wrong page size) | Disk error or external tampering. | Restore copies the bytes successfully; the reopen step fails with `500`. Try a different backup. |

The restore handler intentionally puts the reopen in a `finally` block so the
DB handle is always *attempted* to be re-bound even if the copy raises mid-way.
This makes it possible to retry restore without restarting the server in most
cases, but step-7 partial failures (where the `.db` copied but one sidecar
did not) leave the directory in a mixed state — recovery from that requires
re-running restore against the same backup or a different one.

---

## 13. `autocue serve` startup

The server does **not** create a backup on startup. The lifespan handler in
`serve/deps.py` only:

1. Opens the `Rekordbox6Database` connection and binds it to `app.state.db`.
2. Spawns the similarity-index pre-warm thread (`_prewarm_index`).

Backups only happen at the moment of write. Running `autocue serve` for hours
without making any cue / tag / color / comment changes produces zero backup
files. This is deliberate — automatic startup backups would create dozens of
near-identical snapshots for users who launch the server frequently without
mutating their library.

---

## 14. Stale analysis state after restore

The single most important post-restore step is **cache invalidation**. AutoCue
keeps several in-process caches keyed by [`DjmdContent`](./GLOSSARY.md#djmdcontent)`.ID`. After a restore,
those keys may map to *different* content rows, or the rows may have
different [ANLZ files](./GLOSSARY.md#anlz-files-and-tags) paths, BPM, key, or energy curves.

If the caches were not cleared:

- `similar.find_similar(track_id, ...)` would return neighbours from the
  pre-restore feature vectors — likely wrong artists, wrong BPM gates.
- `setbuilder.build_set(...)` would walk those stale vectors and produce a
  set built against a library state that no longer exists on disk.
- `get_classification(content, db)` would return scores computed against a
  different ANLZ energy curve.
- `get_mixability(content, db)` would return scores computed against
  pre-restore phrase counts and vocal proxy.

The restore handler clears **all four** caches in one block
(`routes.py:603-609`):

```python
from ..analysis import energy as _energy_mod, classify as _classify_mod, score as _score_mod
from ..analysis import similar as _similar_mod
_energy_mod.clear_cache()
_classify_mod._class_cache.clear()
_score_mod._mixability_cache.clear()
_similar_mod.clear_index()
```

| Cache | Module | Key | Cleared by |
| --- | --- | --- | --- |
| Energy curves | `analysis/energy.py` `_cache` | `(content.ID, n_points)` | `energy.clear_cache()` |
| Classification scores | `analysis/classify.py` `_class_cache` | `content.ID` | `_class_cache.clear()` |
| Mixability scores | `analysis/score.py` `_mixability_cache` | `content.ID` | `_mixability_cache.clear()` |
| Similarity index | `analysis/similar.py` `_INDEX` / `_INDEX_BUILT` | full index | `similar.clear_index()` |

`similar.clear_index()` is thread-safe (`analysis/similar.py:169-173`):

```python
def clear_index() -> None:
    global _INDEX, _INDEX_BUILT
    with _INDEX_LOCK:
        _INDEX = {}
        _INDEX_BUILT = False
```

It acquires `_INDEX_LOCK` so a concurrent `_build_index()` in the pre-warm
thread cannot race the clear. The next call to `find_similar()` rebuilds the
index lazily.

The `tests/conftest.py` autouse fixture also clears these same caches before
every test so tests cannot leak cached state across DB swaps.

---

## 15. Examples

**Sample backup path**:

```
~/.autocue/backups/master_20260607T143012.db
~/.autocue/backups/master_20260607T143012.db-wal
~/.autocue/backups/master_20260607T143012.db-shm
```

**Listing backups** — `GET /api/backups`:

```json
[
  {
    "path": "/Users/dj/.autocue/backups/master_20260607T143012.db",
    "filename": "master_20260607T143012.db",
    "size_mb": 14.27,
    "created_at": "2026-06-07 14:30:12"
  },
  {
    "path": "/Users/dj/.autocue/backups/master_20260601T093015.db",
    "filename": "master_20260601T093015.db",
    "size_mb": 14.11,
    "created_at": "2026-06-01 09:30:15"
  }
]
```

**Restoring a backup** — `POST /api/restore`:

```json
{"filename": "master_20260601T093015.db"}
```

Response:

```json
{"restored": true, "message": "Restored from master_20260601T093015.db"}
```

**Deleting a backup** — `DELETE /api/backups/master_20260601T093015.db`:

```json
{"deleted": "master_20260601T093015.db"}
```

**An apply response carrying the backup path** — `POST /api/generate-apply-stream`
final SSE event:

```
data: {"done": true, "applied": 47, "skipped": 3, "backup_path": "/Users/dj/.autocue/backups/master_20260607T143012.db"}
```

The UI shows this path in the post-apply toast so the user always knows
which snapshot to restore if they regret the operation.

---

## 16. Manual recovery

If the server is unreachable but `~/.autocue/backups/` is intact, you can
recover the database by hand:

1. **Quit Rekordbox completely.** Make sure no `rekordbox*` process is
   running (`ps aux | grep -i rekordbox`).
2. **Locate your Rekordbox library directory.** macOS default:
   `~/Library/Pioneer/rekordbox/`. Windows default:
   `%APPDATA%\Pioneer\rekordbox\`.
3. **Back up the current `master.db` before restoring**, in case the chosen
   snapshot is wrong:
   ```bash
   cp ~/Library/Pioneer/rekordbox/master.db ~/master.db.before-restore
   ```
4. **Pick a backup** from `~/.autocue/backups/` (newest unaffected snapshot,
   typically).
5. **Copy the backup over `master.db`** and any matching sidecars:
   ```bash
   cp ~/.autocue/backups/master_20260601T093015.db \
      ~/Library/Pioneer/rekordbox/master.db
   # If the backup had sidecars:
   cp ~/.autocue/backups/master_20260601T093015.db-wal \
      ~/Library/Pioneer/rekordbox/master.db-wal 2>/dev/null || \
      rm -f ~/Library/Pioneer/rekordbox/master.db-wal
   cp ~/.autocue/backups/master_20260601T093015.db-shm \
      ~/Library/Pioneer/rekordbox/master.db-shm 2>/dev/null || \
      rm -f ~/Library/Pioneer/rekordbox/master.db-shm
   ```
   If the backup has no `-wal`/`-shm`, **delete** any stale sidecars in the
   library directory before reopening Rekordbox — the same rule the API
   restore handler enforces ([WAL/SHM handling](#11-walshm-handling)).
6. **Reopen Rekordbox.** If it complains about an inconsistent database, the
   `-wal`/`-shm` cleanup in step 5 was probably skipped.

---

## 17. Retention

Current behaviour: **backups are never deleted automatically**. Each write
operation appends a new snapshot; the only way to remove one is via
`DELETE /api/backups/{filename}` (or `rm` on disk).

Implications:

- A user who runs `/api/generate-apply-stream` once a day on a 200 MB library
  accumulates ~6 GB / month of backups.
- The Backups panel UI is the primary disposal tool — multi-select and bulk
  delete are intentional design choices to keep cleanup ergonomic.
- The naming scheme (`master_YYYYMMDDTHHMMSS.db`) sorts lexicographically by
  age, so `ls | head` shows oldest first if scripted cleanup is desired.

Future possibilities (not implemented):

- A retention policy setting (e.g. "keep the N most recent + everything from
  the last 7 days").
- A startup hook that deletes backups older than X days.
- An on-disk size cap that auto-prunes oldest snapshots when exceeded.

None of these exist today. The retention story is "the user is in charge."

---

## 18. Testing

Backup and restore have dedicated test classes.

**`tests/test_db_writer.py`** — `TestBackupDatabase`
(`test_db_writer.py:54-114`):

- `test_creates_file_at_backup_dir` — backup file exists at
  `BACKUP_DIR` with original bytes.
- `test_filename_contains_timestamp` — name starts with `master_`,
  suffix `.db`.
- `test_returns_path_object` — return is a `pathlib.Path`.
- `test_copies_wal_and_shm_if_present` — both sidecars are copied with
  correct bytes.
- `test_ok_when_wal_absent` — no sidecars left over when source has none.

`TestRekordboxIsRunning` covers `psutil` mock paths
(`test_db_writer.py:121+`).

**`tests/test_serve_routes.py`** — `TestBackups`
(`test_serve_routes.py:755-788`):

- `test_returns_empty_list_when_no_backups` — `/api/backups` returns `[]`
  when `BACKUP_DIR` does not exist.
- `test_returns_backup_files_sorted_newest_first` — `mtime` sort order.
- `test_backup_item_has_required_fields` — schema completeness.

`TestRestore` (`test_serve_routes.py:791+`):

- `test_returns_409_when_rekordbox_running` — guard enforced.
- `test_path_traversal_blocked` — `../../../etc/passwd` rejected with 400.
- `test_path_traversal_with_slash_blocked` — `subdir/master.db` rejected
  with 400.
- `test_missing_backup_returns_404`.
- `test_successful_restore_returns_restored_true`.
- `test_restore_overwrites_db_file` — bytes match the backup after restore.
- `test_restore_copies_wal_if_present` — WAL sidecar is restored.

Throughout these tests, `BACKUP_DIR` is monkey-patched into `tmp_path` and
`rekordbox_is_running` is stubbed to `False`. The reopen branch is mocked via
`patch("pyrekordbox.Rekordbox6Database")` so the test does not need a real
SQLCipher database.

Each per-write endpoint also has dedicated `backup_path` assertions — see
`test_serve_routes.py:374-375` (`/api/apply`) and `:414` (`/api/generate-apply`)
for examples confirming that `data["backup_path"]` is non-null on success and
`None` on dry-run.

---

## 19. Related

- [`cue-generation.md`](./cue-generation.md) — the primary writer that triggers
  most backups.
- [`comment-enrichment.md`](./comment-enrichment.md) — comment-write endpoint
  backup behaviour and per-track commit pattern.
- [`auto-tag.md`](./auto-tag.md) — My Tag writes that share the same backup
  contract; `undo_data` is a finer-grained undo for that specific endpoint.
- [`cue-library-tools.md`](./cue-library-tools.md) — bulk cue rename / shift /
  recolor / delete-orphan; uses filename-only `backup_path` in the SSE
  summary.
- [`rest-api.md`](./rest-api.md) — full endpoint reference; backup-related
  endpoints listed alongside their request / response schemas.

# Library Duplicates

The **Duplicate Tracks** panel (Library tab, local mode only) finds tracks that
appear more than once in your Rekordbox library, suggests which copy to keep, and
deletes the rest — with a backup-before-write contract and a one-click undo.

Implementation:

- `autocue/analysis/duplicates.py` — the pure grouping + keeper logic (no DB writes).
- `autocue/db_writer.py::delete_tracks` — the destructive cascade.
- `autocue/serve/routes.py` — `GET /api/duplicates` (SSE scan) and
  `POST /api/duplicates/delete` (SSE delete).
- `docs/index.html` — the Library-tab panel + confirm modal.

Terminology note: this doc references Rekordbox entities (`DjmdContent`,
`DjmdCue`, `ContentID`, etc.). See the [Rekordbox glossary](./GLOSSARY.md).

---

## Table of contents

- [1. What counts as a duplicate](#1-what-counts-as-a-duplicate)
- [2. The keeper heuristic](#2-the-keeper-heuristic)
- [3. Scan — `GET /api/duplicates`](#3-scan--get-apiduplicates)
- [4. Delete — `POST /api/duplicates/delete`](#4-delete--post-apiduplicatesdelete)
- [5. Safety contract](#5-safety-contract)
- [6. The FK cascade — all 13 ContentID tables](#6-the-fk-cascade--all-13-contentid-tables)
- [7. Frontend behaviour](#7-frontend-behaviour)
- [8. What is NOT deleted](#8-what-is-not-deleted)
- [9. Testing](#9-testing)

---

## 1. What counts as a duplicate

Tracks are grouped by a normalised key:

```
normalize_key(artist, title, duration) = "<artist>|||<title>|||<duration_bucket>"
```

- **artist** and **title** are lowercased, trimmed, and internal whitespace is
  collapsed — so `"Gia    Margaret"` and `"gia margaret"` match.
- **duration_bucket** = `round(duration / 5)` — 5-second buckets. This is the
  phase-3 discriminator: two imports of the same song whose ID3-tagged lengths
  disagree by ±1–2 s still group, but a **4:12 album cut** and a **6:48 extended
  mix** land in different buckets and are NOT called duplicates.
- The triple-pipe `|||` separator is chosen because it cannot legally appear in
  Rekordbox metadata.

A bucket with **≥2 tracks** is a duplicate group. Buckets are sorted
worst-offender-first (`-copy_count`, then alphabetically).

**Empty-metadata tracks** (no artist AND no title — typically streaming
references) are excluded from grouping; they would otherwise collapse into one
fake bucket. The scan summary reports how many were skipped.

## 2. The keeper heuristic

`pick_keeper(copies)` chooses the copy you most likely want to keep — `max` over
this tuple (largest wins):

| Rank | Field | Why |
|---|---|---|
| 1 | `existing_hot_cues` | **Cue-prep outranks everything.** A freshly-prepped re-import with 8 hand-placed cues beats a heavily-played original that only has Rekordbox auto-cues — the prepped copy is the one you'll reach for next. |
| 2 | `play_count` | Reflects actual DJ use. |
| 3 | `last_played` | Newer reference wins; missing date loses to any real date. |
| 4 | `bitrate` | A 320 kbps re-import beats the 192 kbps original. `0` = unknown, loses. |
| 5 | `-track_id` | Deterministic final tiebreak — lowest ID wins, so a re-scan always picks the same keeper. |

The suggestion is only a default. In the UI, every copy in a group's expanded
detail rows has a **"Keep" radio** — picking a different copy recomputes the
non-keeper set, the delete-button label, and the same-file chips live, with no
re-scan.

## 3. Scan — `GET /api/duplicates`

Read-only SSE stream. No Rekordbox-closed check (never mutates):

```
data: {"total": 3765}
data: {"group": {"artist": "...", "title": "...", "copies": [ {...}, ... ]}}
...
data: {"done": true, "summary": {"groups": 707, "surplus": 863, "scanned": 3765, "skipped_empty": 10}}
```

Each copy carries `track_id`, `bpm`, `key`, `existing_hot_cues`, `play_count`,
`last_played`, `source`, `duration`, `bitrate`, `folder_path`, `file_name`,
`same_path_as_keeper`, and `is_keeper`. The path fields let the frontend recompute
`same_path_as_keeper` against a user-chosen keeper without a second round-trip.

The route bulk-loads hot-cue counts and play history up front (same pattern as
`/api/tracks`) so the per-row projection stays O(N).

## 4. Delete — `POST /api/duplicates/delete`

SSE stream. Request: `{"track_ids": [int], "dry_run": bool}`.

```
data: {"total": 863, "backup_path": "/Users/.../master_2026-06-11T...db"}
data: {"processed": 25, "deleted": 25, "skipped": 0}
...
data: {"done": true, "summary": {"deleted": 863, "skipped": 0, "dry_run": false, "cancelled": false, "backup_path": "..."}}
```

Tracks are deleted in 25-row batches so the stream can emit progress. The
backend honours **client disconnect as cancel**: an asyncio task polls
`request.is_disconnected()` and sets a `threading.Event` that the delete loop
checks per row. Rows committed before the cancel survive; the backup still
restores the pre-session state, so a cancel is always safe.

## 5. Safety contract

- **Rekordbox-closed guard** — 409 when Rekordbox is running (the SQLCipher lock
  would corrupt the DB). Dry-run is exempt.
- **Backup first** — a timestamped copy of `master.db` (+ the Discover sidecar)
  is created before the first delete. Returned in the stream's first event so
  the UI surfaces it and offers undo.
- **Per-session backup window** — deletes arriving **<30 s** apart reuse the
  first backup of the session (the window slides on each hit). So a steady
  stream of per-group clicks produces ONE backup, and restoring it rolls back
  the whole cleanup session — not just the last click.
- **Per-row savepoint** — `begin_nested()` per track; one failed row rolls back
  alone, the rest of the batch survives the top-level commit.
- **Concurrency lock** — a non-blocking lock 409s a second concurrent real
  delete. `get_db` returns a single shared SQLAlchemy session app-wide; the
  ~60 s SSE delete would otherwise let a second browser tab's delete corrupt the
  shared transaction. The lock is released on every SSE exit path, including the
  `GeneratorExit` raised on client disconnect, so it can't leak.
- **Undo** — `POST /api/restore` against the returned `backup_path`. The UI
  shows an inline **"Undo this delete"** banner (30 s) that does exactly this.
  We deliberately do NOT re-INSERT deleted `DjmdContent` rows from a snapshot —
  the table has ~80 columns plus a registry and relationships, and a hand-rolled
  re-insert would silently miss columns; restoring the backup is robust.

## 6. The FK cascade — all 13 ContentID tables

`db_writer.delete_tracks` deletes every child row that references the track via
`ContentID` **before** the `DjmdContent` row, so nothing orphans. The full set
(13 tables) is:

```
DjmdCue            ContentCue          ContentActiveCensor   ContentFile
DjmdActiveCensor   DjmdSongHotCueBanklist   DjmdMixerParam   DjmdSongSampler
DjmdSongHistory    DjmdSongPlaylist    DjmdSongTagList       DjmdSongMyTag
DjmdSongRelatedTracks
```

Phase 2 originally cascaded only 4; the other 9 orphaned silently (SQLite's
default has FK enforcement off, so the delete "succeeds" while leaving dangling
rows that corrupt Rekordbox on next open). The fix (`tests/test_duplicates_
integration.py`) is **schema-pinned**: it introspects `pyrekordbox`'s metadata,
seeds one row in every ContentID-bearing table, deletes, and fails if any child
row survives — so if a future pyrekordbox release adds a new ContentID table,
the test catches it.

## 7. Frontend behaviour

- **Scan** — "Find duplicates" streams group cards (collapsed) + a summary with a
  bulk "Delete all N non-keepers" button.
- **Per-group** — each card has a "Delete N non-keepers" button + a "Show
  details" toggle. Expanded rows show id / duration / BPM / key / cues / plays /
  bitrate / last-played, the **★ keeper** highlight, a **"Keep" radio** per copy,
  and a **same-file vs distinct-file chip** (🗂 = shares the keeper's audio file,
  safe to delete; 📁 = distinct file that will remain on disk).
- **Confirm modal** — mirrors the Discover download-confirm pattern: primary
  disabled 250 ms after open, Cancel focused by default, focus-trap, and ESC
  during an in-flight delete aborts it. An audio-summary line warns how many
  distinct files will remain on disk.
- **Progress** — the modal's Delete consumes the SSE stream into an in-place
  progress bar; Cancel becomes "Cancel delete".
- **Invalidation** — `_onTracksDeleted(ids)` surgically prunes `parsedTracks`,
  `parsedTracksById`, and `healthData` so the Cues + Library tabs never keep
  stale references (no `/api/tracks` refetch → scroll + selection survive).

## 8. What is NOT deleted

The delete removes the `DjmdContent` row and its child rows from the Rekordbox
database. It does **NOT** delete the audio file on disk. The per-row chip flags
which non-keepers point at a distinct file (those leave an orphan on disk) vs
which share the keeper's file (no orphan). Opt-in audio-file deletion is a
deferred phase-4 feature with its own threat model (path-traversal guard,
realpath, library-root allowlist, OS-trash undo).

## 9. Testing

- `tests/test_duplicates.py` — `normalize_key` (incl. duration buckets),
  `pick_keeper` (the reordered heuristic + bitrate tiebreak),
  `find_duplicate_groups`, `to_dict` same-path chip.
- `tests/test_duplicates_integration.py` — **real in-memory SQLite** with the
  pyrekordbox schema: the schema-pinned 13-table cascade guard, unrelated-tracks-
  untouched, dry-run, and the cancel/progress hooks.
- `tests/test_serve_routes.py` — `TestDuplicatesEndpoint` (scan) +
  `TestDuplicatesDeleteEndpoint` (409 guards, backup-window reuse/expiry,
  concurrency 409, SSE event shape).
- `tests/web/duplicates-*.test.js` — keeper-pick mirror, same-path predicate,
  `_onTracksDeleted` surgical prune, confirm-modal interlock, focus-trap cycle.

# Database / pyrekordbox constraints

- **Rekordbox must be closed** before running the CLI or clicking Apply in local mode (DB is SQLCipher-locked while open). `db_writer.rekordbox_is_running(db_path=None)` enforces this via two signals: a `psutil` process-name probe (fast) **plus** an `fcntl`/`msvcrt` exclusive-lock attempt on `master.db` when `db_path` is supplied. The lock check catches renamed Rekordbox builds that slip past the process name and the race window where Rekordbox starts after the process probe fired. `serve/routes.py:_rb_running(db)` wraps the call and forwards `db._db_dir / "master.db"` to enable the lock probe at every write endpoint.

- **pyrekordbox API**: use `Rekordbox6Database` from `pyrekordbox.db6`. The `add_track()` method takes the file path as a positional argument, not a keyword argument.

- **ANLZ parsing**: wrap `db.read_anlz_file()` and `get_tag()` calls in `try/except Exception` — pyrekordbox raises `ConstError` / `IndexError` for unsupported ANLZ format versions and missing tags. Affected tracks are silently skipped.

- **Slot numbering**: `CuePoint.slot` is 0-indexed (0 = A … 7 = H), matching the Rekordbox XML `Num` attribute directly. In `DjmdCue`, the slot is encoded as `Kind = slot + 1` (Kind=0 is a memory cue). No `Num` column exists in the DB table.

- **DB path**: `Rekordbox6Database` stores the directory as `_db_dir`; the database file is always `_db_dir / "master.db"`. There is no `.db_path` attribute.

- **XML import is slot-level additive**: Rekordbox only writes slots present in the imported XML. Slots absent from the XML are left untouched in Rekordbox. The app intentionally only wipes slots it will overwrite.

- **BPM guard**: always check `float(bpm) > 0` before using BPM in calculations. Rekordbox can store BPM as `"0.0"` (truthy string, zero float) which would cause division by zero.

- **Memory cue (slot = -1)**: `CuePoint.slot = -1` → `Kind = 0` in DjmdCue (CDJ Auto Cue position). Memory cues do not consume hot cue slots. The `add_memory_cue` pref in `GenerationPrefs` prepends one before the hot cues; in phrase mode it anchors to the first phrase, otherwise to `max(0, inizio_ms)`.

- **DjmdContent.ColorID**: VARCHAR(255) FK to `djmdColor.ID` — NOT an integer. Always query `DjmdColor` at runtime and resolve `{SortKey: ID}` mapping. SortKey 1–8 corresponds to Pink/Red/Orange/Yellow/Green/Aqua/Blue/Purple.

- **DjmdContent.Commnt**: The track comment column is spelled `Commnt` (not `Comment`). Use `getattr(content, "Commnt", "")`. Genre is an association proxy: `content.GenreName` (not `content.Genre` which is the ORM relationship object). `DjmdCue.Comment` is correctly spelled — only `DjmdContent` uses the abbreviated name.

- **DjmdKey.Seq**: use `Seq` (Integer) for server-side key sort, not `ScaleName` (lexicographic "10A" < "1A" is wrong). Client-side uses `camelotSortKey()` which converts "8A" → numeric order.

- **DjmdCue ID generation**: `DjmdCue.ID` is VARCHAR(255) with no auto-generate default — must call `db.generate_unused_id(DjmdCue)` explicitly when inserting. Also set `UUID=str(uuid4())`, `ContentUUID` from the content row, `InFrame=round(position_ms * 150 / 1000)`, `OutMsec=-1`, and 0 for all other integer fields.

- **Smart slot ordering**: `_apply_smart_slot_order()` in `generator.py` assigns slot A to the first non-Intro phrase chronologically (the DJ mix-in point). Slot B is reserved for the first OUTRO phrase (CDJ prep feature — DJs instinctively reach for B at mix-out). Slots C+ are ordered by `_SMART_PRIORITY` (CHORUS=0, UP=1, OUTRO=2, VERSE=3, DOWN=4, BRIDGE=5, INTRO=6). Only applied in phrase mode.

## Sidecar analysis cache (Performance v1 PRD)

- **Cache file**: `<rekordbox_dir>/autocue_cache.sqlite` (TASK-010). Lives in the SAME directory
  as Rekordbox's `master.db`. Plain SQLite (no SQLCipher) — contains numeric energy curves,
  classification labels, similarity vectors, mixability scores, and a gzipped `TrackItem`
  snapshot. **No audio, no credentials, no Discogs tokens, no SQLCipher key.**
- **Schema** (6 tables): `meta`, `energy_curve`, `classification`, `similarity_vector`,
  `mixability`, `tracks_snapshot`. Per-track rows keyed by `(content.ID, anlz_mtime)` —
  rows invalidate automatically when Rekordbox rewrites the ANLZ `.EXT`/`.DAT` files. Tracks
  with no ANLZ store `anlz_mtime = -1.0` (`MISSING_MTIME`) so the reader skips re-attempting
  until the file appears.
- **Schema version**: `meta.schema_version`. Mismatch on open drops + recreates ALL tables
  (no migrations in v1; cache is regenerable from ANLZ). Bump `SCHEMA_VERSION` in
  `autocue/cache.py` when the layout changes.
- **WAL mode + `check_same_thread=False` + `threading.Lock`** serialise access; readers
  never block on writers.
- **Reset**: `autocue serve --reset-cache` removes only `autocue_cache.sqlite` + `-wal` +
  `-shm`. NEVER `master.db`. Path traversal is impossible because the suffix is constant
  (TASK-020).
- **Invalidation hooks**:
  - `/api/restore` calls `CacheStore.invalidate_all()` (TASK-017) — restored backups may have
    different `DjmdContent` rows.
  - HTTP middleware in `serve/app.py` (TASK-026) clears `app.state.tracks_snapshot` after any
    2xx POST/PUT/DELETE to `/api/*`.
- **Tests** use `CacheStore.open_memory()` (`:memory:` SQLite) so they neither touch the
  filesystem nor depend on a real Rekordbox install.

## Thread-pool concurrency model

- **Shared `ThreadPoolExecutor`** in `autocue/analysis/concurrency.py` (TASK-001). Size:
  `AUTOCUE_POOL_SIZE` env (defaults to `min(8, cpu_count())`). Singleton via `get_pool()`;
  cleaned up on serve lifespan exit via `shutdown_pool()`.
- **Single-writer rule**: the pool runs only READ/COMPUTE work. The SSE generator loop is
  the SINGLE writer for `master.db` — it owns `db.session.commit()`. Pool workers may NEVER
  call `db.session.commit()` directly. `tests/test_concurrency_invariants.py` pins this
  contract via tests that fail if a future PR introduces a parallel write path.
- **Pattern** (every flagged-parallel endpoint follows this shape):
  1. Pool worker reads source data (`db.get_content`, `db.read_anlz_file`, classify, etc.),
     returns `(content_id, computed_data, error?)`.
  2. Generator loop iterates `concurrent.futures.as_completed(...)` (or, for TASK-002, a
     bounded-in-flight `_wait_any` cycle).
  3. Writer-side per-track work runs ON THE GENERATOR'S THREAD — writes, commits, emits SSE.
  4. Per-track exceptions in the worker are forwarded as a third tuple element; the writer
     emits an error event and continues. One bad row never aborts the stream.
- **`AUTOCUE_PARALLEL_*` env flags** (default-off until TASK-008 signoff): the six SSE
  refactors are gated to preserve current behaviour for users until the maintainer runs the
  stress test below.
- **TASK-008 verification** (gated `RUN_ANLZ_STRESS=1`):
  ```
  RUN_ANLZ_STRESS=1 AUTOCUE_DB_PATH=~/Library/Pioneer/rekordbox \
      pytest tests/test_concurrency.py::test_anlz_read_concurrent -v
  ```
  Hammers `db.read_anlz_file()` from 16 threads against a real Rekordbox library. Passing
  is the gating signal to flip the six `AUTOCUE_PARALLEL_*` flags to default-on. If it
  fails, the fallback is a `thread_local_db(db_dir)` helper in
  `autocue/analysis/concurrency.py` giving each worker its own `Rekordbox6Database` instance.

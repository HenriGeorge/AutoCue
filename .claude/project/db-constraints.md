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

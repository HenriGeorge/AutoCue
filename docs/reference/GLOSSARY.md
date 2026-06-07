# Glossary — Rekordbox / Pioneer entities

A reference for the database tables, ANLZ tags, file conventions, and DJ-specific notations that appear across the AutoCue documentation. Use this as a single source of truth when terms feel unfamiliar.

For project-wide invariants and code-location rules, see [`../../CLAUDE.md`](../../CLAUDE.md). For the high-level architecture, see [`../../README.md`](../../README.md).

## Table of Contents

- [Database tables (`DjmdXxx`)](#database-tables-djmdxxx)
  - [`DjmdContent`](#djmdcontent)
  - [`DjmdCue`](#djmdcue)
  - [`DjmdMyTag` + `DjmdSongMyTag`](#djmdmytag--djmdsongmytag)
  - [`DjmdPlaylist` + `DjmdSongPlaylist`](#djmdplaylist--djmdsongplaylist)
  - [`DjmdHistory` + `DjmdSongHistory`](#djmdhistory--djmdsonghistory)
  - [`DjmdColor`](#djmdcolor)
  - [`DjmdKey`](#djmdkey)
  - [`DjmdGenre`](#djmdgenre)
- [ANLZ files and tags](#anlz-files-and-tags)
- [Cue encoding (`Kind`, slot, `InFrame`, `OutMsec`)](#cue-encoding-kind-slot-inframe-outmsec)
- [Camelot key wheel](#camelot-key-wheel)
- [Phrase types and mood values](#phrase-types-and-mood-values)
- [Color SortKey ↔ ColorID resolution](#color-sortkey--colorid-resolution)
- [BPM storage convention](#bpm-storage-convention)
- [DB filenames and SQLCipher locking](#db-filenames-and-sqlcipher-locking)
- [Rekordbox XML import semantics](#rekordbox-xml-import-semantics)
- [`pyrekordbox` glue](#pyrekordbox-glue)

---

## Database tables (`DjmdXxx`)

Rekordbox 7 stores its library in an SQLCipher-encrypted SQLite database called `master.db`. Tables are prefixed `Djmd…`. AutoCue uses [`pyrekordbox`](https://github.com/dylanljones/pyrekordbox) (specifically `Rekordbox6Database` from `pyrekordbox.db6`) to read and write this database.

### `DjmdContent`

The track row. One per audio file imported into Rekordbox.

| Column | Type | Notes |
|---|---|---|
| `ID` | VARCHAR(255) | Stable Rekordbox track ID. Used as foreign key by `DjmdCue`, `DjmdSongMyTag`, etc. |
| `Title` | VARCHAR | Track title. |
| `ArtistName` | association proxy | Resolves to the artist name string. Use this, not `.Artist` (the ORM relationship object). |
| `AlbumName` | association proxy | Album name string. |
| `GenreName` | association proxy | Genre name string. **Not** `Genre` (which is the relationship). |
| `BPM` | int / str | Stored as `BPM × 100`. See [BPM storage convention](#bpm-storage-convention). |
| `Length` | int | Track duration in seconds. |
| `FolderPath` | VARCHAR | **Full** absolute audio file path (not just the folder). |
| `ColorID` | VARCHAR(255) | Foreign key to [`DjmdColor.ID`](#djmdcolor) — **not** an integer SortKey. |
| `Commnt` | VARCHAR | Track comment. **Spelled with no `e`.** See `comment-enrichment.md`. |
| `Rating` | int | 0–5 (stored as 0, 51, 102, 153, 204, 255 in some versions). |
| `DJPlayCount` | int | Number of times played in Rekordbox. |

> ⚠️  Use `getattr(content, "Commnt", "")` — the column is spelled `Commnt`, not `Comment`. Only `DjmdContent` uses the abbreviated name; `DjmdCue.Comment` is correctly spelled.

### `DjmdCue`

A single hot cue, memory cue, or loop point on a track.

| Column | Type | Notes |
|---|---|---|
| `ID` | VARCHAR(255) | No default — must call `db.generate_unused_id(DjmdCue)` when inserting. |
| `UUID` | VARCHAR(255) | Set with `str(uuid4())` on insert. |
| `ContentID` | VARCHAR(255) | FK to `DjmdContent.ID`. |
| `ContentUUID` | VARCHAR(255) | Copy of the parent `DjmdContent.UUID`. |
| `Kind` | int | `0` = memory cue (CDJ Auto Cue), `1`–`8` = hot cue in slot A–H. See [Cue encoding](#cue-encoding-kind-slot-inframe-outmsec). |
| `InFrame` | int | Position in 150ths of a second. `round(position_ms * 150 / 1000)`. |
| `OutMsec` | int | `-1` for non-loop cues; loop end position in milliseconds for loops. |
| `Color` | int | Pioneer color index. |
| `Name` | VARCHAR | Optional cue label ("Drop", "Bar 1", "Outro 2", etc.). |
| `Comment` | VARCHAR | Free-form comment on the cue. Spelled correctly here (unlike `DjmdContent.Commnt`). |

> ⚠️  When inserting a new `DjmdCue` row you must also set `OutFrame = -1` (when not a loop) and zero every other integer column. Missing columns crash on flush.

### `DjmdMyTag` + `DjmdSongMyTag`

Rekordbox's "My Tags" feature — user-defined faceted labels separate from genre. AutoCue writes detector results (category, vocal, energy_level, energy_profile, intro_outro, decade, bpm_tier, play_history) here.

| Table | Columns | Notes |
|---|---|---|
| `DjmdMyTag` | `ID, UUID, Name, Attribute, Seq` | Tag definition. `Attribute` is a color hint 1–8 mirroring `DjmdColor.SortKey`. |
| `DjmdSongMyTag` | `ID, ContentID, MyTagID, Seq` | Join row attaching a tag to a track. |

`autocue/analysis/auto_tag.py:ensure_tag_by_name()` is idempotent: it reuses an existing tag by name rather than creating a duplicate, so re-running auto-tag never clutters the sidebar.

### `DjmdPlaylist` + `DjmdSongPlaylist`

Rekordbox playlists.

| Table | Columns | Notes |
|---|---|---|
| `DjmdPlaylist` | `ID, UUID, Name, ParentID, Seq, Attribute` | `ParentID` enables folder nesting. `Attribute` distinguishes playlists from folders. |
| `DjmdSongPlaylist` | `ID, ContentID, PlaylistID, TrackNo` | `TrackNo` is the 1-based position within the playlist. |

### `DjmdHistory` + `DjmdSongHistory`

Rekordbox play history. AutoCue's "last played" facet filter and the `play_history` auto-tag detector consume these.

| Table | Columns | Notes |
|---|---|---|
| `DjmdHistory` | `ID, DateCreated, Name` | One row per played-tracks session. |
| `DjmdSongHistory` | `ID, ContentID, HistoryID, TrackNo` | One row per play event. |

> ⚠️  `/api/tracks` deliberately fetches all history rows via `db.query(DjmdSongHistory).all()` and filters against `row_ids` in Python — **not** with a SQLAlchemy `.filter(ContentID.in_(row_ids))`. For full-library page loads (~3k rows) the `IN` clause is slower against pyrekordbox's SQLCipher. Do not "optimize" this.

### `DjmdColor`

The Rekordbox track-color palette. There are eight named colors plus an unused 0 slot.

| Column | Type | Notes |
|---|---|---|
| `ID` | VARCHAR(255) | Used as the FK target for `DjmdContent.ColorID`. |
| `SortKey` | int | 1–8. Maps to the named colors below. |
| `Name` | VARCHAR | Color name. |

| `SortKey` | Color | Hex used by AutoCue's BPM legend |
|---|---|---|
| 1 | Pink | `#e4849b` |
| 2 | Red | `#e4384e` |
| 3 | Orange | `#f0801a` |
| 4 | Yellow | `#e4c000` |
| 5 | Green | `#35c26e` |
| 6 | Aqua | `#00b4ba` |
| 7 | Blue | `#2e7de4` |
| 8 | Purple | `#9050e4` |

See [Color SortKey ↔ ColorID resolution](#color-sortkey--colorid-resolution) for how AutoCue translates between the UI sort key and the FK.

### `DjmdKey`

The 24-row key reference table (every Camelot position).

| Column | Type | Notes |
|---|---|---|
| `ID` | VARCHAR(255) | FK target. |
| `ScaleName` | VARCHAR | Camelot notation like `"8A"`, `"5B"`. |
| `Seq` | int | Stable numeric ordering — use this for server-side sort, **not** `ScaleName` (lexicographic `"10A" < "1A"` is wrong). |

The web app uses its own `camelotSortKey()` helper for client-side sort. See [Camelot key wheel](#camelot-key-wheel).

### `DjmdGenre`

Genre lookup table. Accessed in AutoCue via the association proxy `content.GenreName` (string), **not** `content.Genre` (the ORM relationship object).

---

## ANLZ files and tags

Rekordbox stores per-track analysis data in two binary files alongside the audio:

| File | Tags AutoCue uses | Source of |
|---|---|---|
| `.DAT` | `PQTZ`, `PWAV` | Beat grid, energy curve |
| `.EXT` | `PSSI` | Phrase boundaries (Intro / Verse / Chorus / Bridge / Outro / Up / Down) |

The files live in Rekordbox's `share/` directory. Resolve a track's ANLZ path with `db.get_anlz_path(content, "DAT")` or `"EXT"`. Parse with `db.read_anlz_file(path)`.

| ANLZ tag | What it carries | AutoCue consumer |
|---|---|---|
| `PQTZ` | Beat grid — millisecond timestamp per beat, plus the beat number within the bar | `cue-generation.md` (bar mode), `library-health.md` (NO_BEATGRID detection) |
| `PWAV` | Waveform overview — raw amplitude samples (one per ~150 ms in the source, but AutoCue resamples) | `energy-and-mixability.md` |
| `PSSI` | Phrase boundaries with type (Intro / Verse / Chorus / Bridge / Outro / Up / Down) and mood value | `cue-generation.md` (phrase mode), `track-classification.md`, `similar-tracks.md` (vocal proxy) |

> ⚠️  Always wrap `db.read_anlz_file()` and `get_tag()` calls in `try/except Exception`. pyrekordbox raises `ConstError` / `IndexError` for unsupported ANLZ format versions and missing tags. AutoCue silently skips affected tracks rather than failing the whole batch.

---

## Cue encoding (`Kind`, slot, `InFrame`, `OutMsec`)

The relationship between Pioneer's wire-level cue representation and AutoCue's domain model:

| Concept | AutoCue (`CuePoint.slot`) | DB (`DjmdCue.Kind`) | XML (`<POSITION_MARK Num=…>`) |
|---|---|---|---|
| Memory cue (CDJ Auto Cue) | `-1` | `0` | `Num="-1"` |
| Hot cue slot A | `0` | `1` | `Num="0"` |
| Hot cue slot B | `1` | `2` | `Num="1"` |
| … | … | … | … |
| Hot cue slot H | `7` | `8` | `Num="7"` |

Rule: **`Kind = slot + 1`**. There is no `Num` column on `DjmdCue` itself — the XML field is what Rekordbox writes during XML import.

Position fields:

- **`InFrame`** — `round(position_ms * 150 / 1000)`. The unit is 1/150 s.
- **`OutMsec`** — `-1` for non-loop cues. For loops it's the end position in milliseconds.
- **`OutFrame`** — same convention as `InFrame` but for the loop end point. The cue library tools `shift` operation updates both `InFrame` and `OutFrame` for loops.

---

## Camelot key wheel

DJ-friendly notation for harmonic mixing. Each musical key gets a `(number, letter)` pair: `1A` through `12A` for minor keys, `1B` through `12B` for major. Adjacent numbers and the same number with a different letter are harmonically compatible.

```
                          12A / 12B
                  11A / 11B     1A  / 1B
               10A / 10B               2A / 2B
              9A  / 9B                   3A / 3B
               8A / 8B                  4A / 4B
                  7A / 7B          5A / 5B
                          6A / 6B
```

| Move | Camelot delta | Result | Score in `transition-scoring.md` |
|---|---|---|---|
| Same key | none | Perfect harmonic | 100 |
| Same number, different letter | `nA ↔ nB` (relative major/minor) | "Energy boost" mix | 75 |
| Adjacent number, same letter | `nA → (n±1)A` | Up/down a fifth | 80 |
| Two off | `nA → (n±2)A` | Mild dissonance | 50 |
| Anything else | — | Clash | 25 or lower |

> ⚠️  Sort Camelot strings server-side by `DjmdKey.Seq` (integer), **not** `ScaleName` (the lexicographic comparison `"10A" < "1A"` is wrong). The web app uses `camelotSortKey()` for client-side sorting.

---

## Phrase types and mood values

`PSSI` (`.EXT` ANLZ tag) yields a sequence of phrases. Each phrase has:

- **Kind**: the phrase type. AutoCue maps these via `PhraseLabel` in `autocue/models.py`.
- **Mood**: an integer 1–3 indicating low / medium / high energy.

| Rekordbox kind | `PhraseLabel` | Default AutoCue cue name | Default color |
|---|---|---|---|
| Intro | `INTRO` | "Intro" | Green |
| Verse | `VERSE` | "Verse" | Blue |
| Chorus (low or mid mood) | `CHORUS` | "Drop" | Red |
| Chorus (high mood) | `CHORUS` | "Drop" | Red |
| Bridge | `BRIDGE` | "Bridge" | Cyan |
| Outro | `OUTRO` | "Outro" | Orange |
| Up (build-up) | `UP` | "Build" | Pink |
| Down (breakdown) | `DOWN` | "Break" | Purple |

The smart slot ordering rule lives in `autocue/generator.py:_apply_smart_slot_order()`:

- **Slot A** = first non-Intro phrase chronologically (the DJ mix-in point)
- **Slot B** = first OUTRO phrase (DJs reach for B at mix-out)
- **Slots C+** = `_SMART_PRIORITY` order: CHORUS=0, UP=1, OUTRO=2, VERSE=3, DOWN=4, BRIDGE=5, INTRO=6

---

## Color SortKey ↔ ColorID resolution

`DjmdContent.ColorID` is a VARCHAR(255) FK to `DjmdColor.ID` — it is **not** an integer SortKey. To set a track color from AutoCue's BPM-color rule:

```python
from pyrekordbox.db6 import DjmdColor
sortkey_to_color_id = {c.SortKey: c.ID for c in db.query(DjmdColor).all()}
content.ColorID = sortkey_to_color_id[5]  # Green
```

Never hard-code an integer into `ColorID` — different Rekordbox installations may have different `DjmdColor.ID` values.

The cue library tools `recolor` operation uses `DjmdCue.ColorTableIndex` (0–8), which **is** an integer — that's a different column from `DjmdContent.ColorID`. See `cue-library-tools.md`.

---

## BPM storage convention

`DjmdContent.BPM` is stored as the BPM **multiplied by 100**: a track at 128.50 BPM has `BPM = 12850`. AutoCue divides by 100.0 wherever it reads BPM:

```python
bpm = float(getattr(content, "BPM", 0) or 0) / 100.0
if bpm <= 0:
    skip()  # mandatory guard — Rekordbox can store "0.0" (truthy string, zero float)
```

The BPM-zero guard is critical. A track with no BPM analysis can have `BPM = "0.0"` (a truthy string) or `BPM = 0`. Forgetting `bpm > 0` causes a division-by-zero in bar-interval cue math.

---

## DB filenames and SQLCipher locking

The Rekordbox 7 database is `master.db` plus two WAL-mode sidecars: `master.db-wal` and `master.db-shm`. All three must be copied together on backup/restore.

`Rekordbox6Database` stores the parent directory as `_db_dir`. There is no `.db_path` attribute. To get the file path:

```python
db_path = Path(db._db_dir) / "master.db"
```

While Rekordbox is open, the OS holds an exclusive lock on `master.db`. Writing through pyrekordbox fails. AutoCue's `db_writer.rekordbox_is_running()` uses `psutil` to enumerate processes and refuses to write when Rekordbox is detected — see [`backup-and-restore.md`](./backup-and-restore.md) and the `409` responses documented in [`rest-api.md`](./rest-api.md).

---

## Rekordbox XML import semantics

Rekordbox XML import is **slot-level additive**: when Rekordbox reads an imported XML, it only replaces slots that appear in the XML. Slots absent from the XML are left untouched on the existing track.

This is why AutoCue's XML writer intentionally only emits the slots it intends to overwrite — manually placed cues in other slots survive the import unchanged.

The implication for the web app: the "Conflict warning" only triggers when AutoCue would write a slot the user already used. Other manually placed cues are safe.

---

## `pyrekordbox` glue

Common patterns used throughout AutoCue:

```python
from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6 import (
    DjmdContent, DjmdCue, DjmdColor, DjmdKey, DjmdGenre,
    DjmdMyTag, DjmdSongMyTag,
    DjmdPlaylist, DjmdSongPlaylist,
    DjmdHistory, DjmdSongHistory,
)

db = Rekordbox6Database()       # auto-detects master.db on macOS
content = db.get_content(ID=42) # single track
all_tracks = db.get_content().all()

# Insert a new row (Kind / UUID / generated ID required)
cue = DjmdCue(
    ID=db.generate_unused_id(DjmdCue),
    UUID=str(uuid4()),
    ContentID=content.ID,
    ContentUUID=content.UUID,
    Kind=slot + 1,
    InFrame=round(position_ms * 150 / 1000),
    OutMsec=-1,
    OutFrame=-1,
    Color=0, ColorTableIndex=0, Type=0, ActiveLoop=0, Status=0,
    Name="Drop",
)
db.session.add(cue)
db.session.commit()
```

---

## See also

- [`../FEATURES.md`](../FEATURES.md) — end-user feature tour with screenshots.
- [`../../CLAUDE.md`](../../CLAUDE.md) — invariants and contributor notes — the single source of truth for `Commnt` spelling, `Kind = slot + 1`, `ColorID` VARCHAR, etc.
- [`../../README.md`](../../README.md) — install + quick-start.
- [`../../SCORING_BUGS.md`](../../SCORING_BUGS.md) — historical post-mortem for the scoring algorithms.

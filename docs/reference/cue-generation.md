# Hot Cue Generation Engine

This document describes AutoCue's hot cue generation engine end-to-end: how
tracks are inspected, which strategy is chosen, how phrase data is parsed out
of Rekordbox's ANLZ analysis files, how slots are reordered for DJ ergonomics,
and how the result is written to either a Rekordbox XML file or directly to
the `master.db` database.

The engine lives in three primary modules:

| Module                              | Role                                                          |
| ----------------------------------- | ------------------------------------------------------------- |
| `autocue/generator.py`              | Strategy selection, smart slot ordering, memory-cue building. |
| `autocue/analyzer.py`               | [ANLZ](GLOSSARY.md#anlz-files-and-tags) parser ([PSSI](GLOSSARY.md#anlz-files-and-tags) phrases + [PQTZ](GLOSSARY.md#anlz-files-and-tags) beat grid). |
| `autocue/models.py`                 | `PhraseLabel` enum + `CuePoint` dataclass + color tables.     |
| `autocue/writer.py`                 | Rekordbox XML output.                                         |
| `autocue/db_writer.py`              | Direct [`DjmdCue`](GLOSSARY.md#djmdcue) insert with backup + safety checks. |

---

## Table of Contents

1. [Overview](#1-overview)
2. [The Three Strategies](#2-the-three-strategies)
3. [Phrase Analysis](#3-phrase-analysis)
4. [Smart Slot Ordering](#4-smart-slot-ordering)
5. [Bar-Interval Mode](#5-bar-interval-mode)
6. [Heuristic Mode](#6-heuristic-mode)
7. [Memory Cues (Kind=0)](#7-memory-cues-kind0)
8. [Cue Color Mapping](#8-cue-color-mapping)
9. [Cue Naming](#9-cue-naming)
10. [Confidence Levels](#10-confidence-levels)
11. [GenerationPrefs](#11-generationprefs)
12. [Writing to XML vs Writing to DB](#12-writing-to-xml-vs-writing-to-db)
13. [Skipping and Overwriting](#13-skipping-and-overwriting)
14. [Edge Cases](#14-edge-cases)
15. [XML Import Semantics](#15-xml-import-semantics)
16. [Examples](#16-examples)
17. [File Map](#17-file-map)
18. [Testing](#18-testing)
19. [Related References](#19-related-references)

---

## 1. Overview

A Rekordbox **hot cue** is a labelled position inside a track that a DJ can
jump to instantly by pressing a pad on a CDJ or controller. Rekordbox
supports eight hot cue slots per track, labelled A through H. AutoCue
fills these slots automatically so a freshly imported library is immediately
playable on stage.

AutoCue also writes **memory cues** (Rekordbox's "auto cue" markers — a
different concept from hot cues, see [Section 7](#7-memory-cues-kind0)).

The generator inspects each track and picks the **best** of three strategies,
in this priority order:

1. **Phrase analysis** — uses Rekordbox's own song-structure data (intros,
   choruses, breaks, outros) from the `.EXT` ANLZ file. Highest quality.
2. **Bar-interval** — when no phrase data exists but the BPM is known, places
   cues every N bars (default: every 16 bars).
3. **Heuristic** — every 30 seconds. Last-resort placement for tracks
   without any analysis data.

The strategy actually used is reported back to the caller as a string
(`"phrase"`, `"bar"`, or `"heuristic"`) so the UI can show confidence badges.

Each `CuePoint` carries a `confidence` value tagged at generation time
(`1.0`, `0.6`, `0.3`) which surfaces in the UI as **High / Medium / Low**
badges. See [Section 10](#10-confidence-levels).

---

## 2. The Three Strategies

The entry point is `generate_cues_for_track()` at
`autocue/generator.py:183`. It accepts a `DjmdContent` row, a
`Rekordbox6Database`, and an optional `GenerationPrefs`, and returns
`(cues, mode_used)`.

The `prefs.mode` field controls strategy selection:

| `prefs.mode` | Behaviour                                                                                          |
| ------------ | -------------------------------------------------------------------------------------------------- |
| `"auto"`     | Try phrase first; fall back to bar if no ANLZ data; fall back to heuristic if no BPM. **Default.** |
| `"phrase"`   | Phrase-only. Returns `[]` (no fallback) when ANLZ data is unavailable.                             |
| `"bar"`      | Bar-only. Returns `[]` when BPM is missing or `0`.                                                 |

The control flow at `autocue/generator.py:204-226`:

```python
if prefs.mode in ("phrase", "auto"):
    phrase_cues = analyze_track(content, db)
    if phrase_cues:
        cues, mode_used = phrase_cues, "phrase"
    elif prefs.mode == "phrase":
        return [], "phrase"  # no fallback in explicit phrase mode

if not cues:
    bpm_ok = float(getattr(content, "BPM", 0) or 0) / 100 > 0
    if prefs.mode == "bar" and bpm_ok:
        cues, mode_used = _bar_strategy(content, db, prefs)
    elif prefs.mode == "auto":
        if bpm_ok:
            cues, mode_used = _bar_strategy(content, db, prefs)
        else:
            cues, mode_used = _heuristic_strategy(content, db, prefs)
```

Important: **explicit phrase mode never falls back**. This is intentional —
when a user clicks "Phrase mode only" in the UI they want the absence of
phrase data to be visible, not silently masked by bar placement.

---

## 3. Phrase Analysis

Rekordbox stores its song-structure analysis in a `.EXT`
[ANLZ](GLOSSARY.md#anlz-files-and-tags) file alongside each track. The
relevant tag is **[PSSI](GLOSSARY.md#anlz-files-and-tags)** (Phrase
Structure Sensing Information). The beat grid lives in the `.DAT` ANLZ
file under the **[PQTZ](GLOSSARY.md#anlz-files-and-tags)** tag.

### 3.1 PSSI tag

Each PSSI entry has:

- `beat` — 1-indexed beat number where the phrase starts.
- `kind` — integer encoding the phrase type (meaning depends on `mood`).
- `fill` / `beat_fill` — optional flags for sub-phrase fill bars (used by
  `analyze_fills()` for "add fill cues" mode).

`pssi_content.mood` is a track-wide value (1, 2, or 3) selecting which
`kind → label` lookup table is used.

### 3.2 Mood values and phrase types

The `_KIND_MAP` table at `autocue/models.py:17-52`:

| Mood    | `kind` | PhraseLabel | DJ Name (`DJ_NAMES`) |
| ------- | ------ | ----------- | -------------------- |
| 1 High  | 1      | INTRO       | Intro                |
| 1 High  | 2      | UP          | Build                |
| 1 High  | 3      | DOWN        | Break                |
| 1 High  | 5      | CHORUS      | **Drop**             |
| 1 High  | 6      | OUTRO       | Outro                |
| 2 Mid   | 1      | INTRO       | Intro                |
| 2 Mid   | 2–7    | VERSE       | Verse                |
| 2 Mid   | 8      | BRIDGE      | Bridge               |
| 2 Mid   | 9      | CHORUS      | Drop                 |
| 2 Mid   | 10     | OUTRO       | Outro                |
| 3 Low   | 1      | INTRO       | Intro                |
| 3 Low   | 2–7    | VERSE       | Verse                |
| 3 Low   | 8      | BRIDGE      | Bridge               |
| 3 Low   | 9      | CHORUS      | Drop                 |
| 3 Low   | 10     | OUTRO       | Outro                |

Unknown `(mood, kind)` combinations resolve to `PhraseLabel.UNKNOWN` (display
name `""`).

Note the EDM-aware naming in `DJ_NAMES` at `autocue/models.py:62-71`:

- Rekordbox `CHORUS` → "**Drop**" (the high-energy section).
- Rekordbox `UP` → "**Build**" (the riser before the drop).
- Rekordbox `DOWN` → "**Break**" (the breakdown after the drop).

These names get written into the `Comment` field of `DjmdCue` and the `Name`
attribute of the XML `POSITION_MARK`, so they appear on the CDJ display
during a set.

### 3.3 The XOR de-garbling pass

Rekordbox 6 exports its PSSI tag with an XOR mask applied to the body. The
mask sequence is hard-coded at `autocue/analyzer.py:23`:

```python
_PSSI_XOR_MASK = bytearray.fromhex("CB E1 EE FA E5 EE AD EE E9 D2 E9 EB E1 E9 F3 E8 E9 F4 E1")
```

The de-garble logic at `autocue/analyzer.py:68-80` inspects bytes 18–19 of
the PSSI body to read `mood_raw`. If it is **not** in `{1, 2, 3}`, the tag
is assumed garbled and the XOR pass is run with each byte offset by
`len_entries` (with wraparound).

This is needed because pyrekordbox itself does not currently de-garble PSSI.

### 3.4 Resilient tag scanner

`_get_anlz_tags_resilient()` at `autocue/analyzer.py:26-90` walks the ANLZ
file byte-by-byte rather than relying on pyrekordbox's strict `construct`
schemas. This is critical because:

- Rekordbox 7 writes a `PQT2` tag with version `0x02000002`, but
  pyrekordbox 0.4.x expects `0x01000002` and raises a `ConstError`.
- Once one tag fails, pyrekordbox's `AnlzFile.parse` aborts the whole file.
- The resilient scanner skips unparseable tags and keeps going, so PSSI is
  still recovered even when PQT2 is unreadable.

The wrapper `_get_pssi_and_pqtz()` at `autocue/analyzer.py:101-138`
tries pyrekordbox's normal parser first and falls back to the resilient
scanner via `db.get_anlz_path(content, "EXT" | "DAT")` only on exception.

All ANLZ access is wrapped in `try/except Exception` — a malformed ANLZ
silently yields zero phrase cues. The track then falls through to bar mode
in `"auto"`. See [Section 14](#14-edge-cases) for full error handling.

### 3.5 The two-pass phrase selection

`analyze_track()` at `autocue/analyzer.py:141-244` runs a deliberate
two-pass selection to ensure rare labels survive when the track is dominated
by repeating Up/Down phrases:

- **Pass 1**: For each `PhraseLabel` value, take its **first** chronological
  occurrence. This guarantees one Intro, one Drop, one Outro, etc.
- **Pass 2**: Fill remaining slots (up to `MAX_HOT_CUES = 8`) with phrases
  not already taken, in chronological order.
- Combine, sort by position, dedupe same-position entries (defends against
  degenerate PSSI tags with zero-length phrases).
- Assign DJ-friendly names. Labels appearing more than once are numbered:
  `"Drop"`, `"Drop 2"`, `"Drop 3"`.

`phrase_bars` is also computed per cue: the bar count is derived from the
delta in ms to the next PSSI entry, divided by `avg_ms_per_beat * 4`. This
feeds the "8 bar intro" hint displayed in the UI's enriched comment and the
Cue Quality badge.

---

## 4. Smart Slot Ordering

After phrase mode produces a chronologically ordered list of cues, the
generator can **reassign slot numbers** to put the most useful cues on the
"home row" of slots A and B. This is a DJ ergonomics feature, not a data
correctness feature.

`_apply_smart_slot_order()` at `autocue/generator.py:65-121` runs only when:

- `mode_used == "phrase"` (bar/heuristic modes already have natural ordering), and
- `prefs.slot_priority == "smart"` (the default; `"sequential"` preserves
  chronological assignment for legacy compatibility).

### 4.1 The deterministic priority list

The rule set below is what `_apply_smart_slot_order()`
(`autocue/generator.py:65-121`) implements, in the exact order it runs.

1. **Slot A — the mix-in point.** Always the first non-Intro phrase in
   chronological order. Why: a DJ triggering hot cue A wants the moment they
   can start a transition, not the intro buildup. If a track has a chorus at
   00:00 and an intro at 00:08, slot A goes to the chorus.

   The label is annotated with a `(Mix In)` suffix when it isn't already
   named "Intro" (see `autocue/generator.py:89-95`). If the only non-Intro
   phrase is itself named "Intro" (because of mood/kind quirks), no suffix
   is added.

2. **Slot B — the first OUTRO phrase.** Reserved for the start of the
   mix-out window. Why: DJ ergonomics — at mix-out time a DJ's thumb
   instinctively finds slot B as the second-most-used pad. The label is
   prefixed with `(Outro)` if its existing name does not already contain
   "Outro" (`autocue/generator.py:108-114`). If there is no OUTRO phrase in
   the track, slot B falls through to the normal priority queue (the next
   most important phrase by `_SMART_PRIORITY` lands there instead).

3. **Slots C through H — by `_SMART_PRIORITY`.** Remaining phrases are
   sorted by `(priority_tier, position_ms)` and assigned to the next free
   slot. The tier table (`autocue/generator.py:21-30`):

   | Tier | `PhraseLabel` | Default slot if no ties | Why this priority |
   | ---- | ------------- | ----------------------- | ----------------- |
   | 0 | CHORUS  | **Slot C** | The main drop — highest musical importance, the "money shot" of the track. |
   | 1 | UP      | **Slot D** | Build-ups. Often used as a "spinback" or filter-sweep cue. |
   | 2 | OUTRO   | **Slot E** | Second outro candidate if multiple outros exist (slot B took the first). |
   | 3 | VERSE   | **Slot F** | Verses — useful for vocal-line entry points. |
   | 4 | DOWN    | **Slot G** | Breakdowns — the calm before the next drop. |
   | 5 | BRIDGE  | **Slot H** | Bridges — the rarest phrase type in EDM. |
   | 6 | INTRO   | (overflow, deprioritized) | Intros rarely get a hot cue since slot A already captures the mix-in. |
   | 7 | UNKNOWN | (overflow, last)          | Phrases that didn't resolve via `_KIND_MAP`. |

4. **Tie-breaking within a tier — chronological.** When multiple phrases
   share the same priority (e.g. two CHORUS phrases), they are ordered by
   `position_ms` ascending. The sort key at `autocue/generator.py:119`:

   ```python
   remaining.sort(key=lambda c: (_SMART_PRIORITY.get(c.label, 7), c.position_ms))
   ```

   Mood, fill flags, and phrase length do **not** influence the tiebreak —
   only the timestamp does. This is intentional: the chronologically first
   Drop is almost always the "main" Drop a DJ wants on the lowest free slot.

5. **Overflow cap at 8 cues.** After A and B are assigned, only six free
   slots remain (C–H). If the chronologically-sorted, priority-sorted
   `remaining` list has more than six entries, the **last entries are
   simply dropped** — i.e. phrases whose final slot index would be `>= 8`
   never get written. In practice this means low-priority Intros and
   Unknowns fall off first, then later Bridges and Breakdowns. CHORUS
   phrases are essentially never dropped.

6. **All-Intro fallback.** If **every** phrase in the track is labelled
   `INTRO`, there is no candidate for slot A. The function then falls back
   to pure chronological assignment (`autocue/generator.py:84-87`) — slot A
   = first Intro by time, slot B = second, etc. — rather than producing
   zero cues.

7. **Only runs in phrase mode.** Bar mode and heuristic mode do **not**
   apply smart slot ordering — they fill slots A through H sequentially in
   the order the cues were generated. `_apply_smart_slot_order()` is gated
   at the call site by `mode_used == "phrase"` and
   `prefs.slot_priority == "smart"`.

### 4.2 The priority table

From `autocue/generator.py:21-30`:

```python
_SMART_PRIORITY: dict[PhraseLabel, int] = {
    PhraseLabel.CHORUS:  0,  # Drop
    PhraseLabel.UP:      1,  # Build
    PhraseLabel.OUTRO:   2,  # Outro
    PhraseLabel.VERSE:   3,  # Verse
    PhraseLabel.DOWN:    4,  # Break
    PhraseLabel.BRIDGE:  5,  # Bridge
    PhraseLabel.INTRO:   6,  # Intro — least useful as a hot cue
    PhraseLabel.UNKNOWN: 7,
}
```

The Intro is deliberately the **last** priority. Intros are background; what
DJs reach for under stage lights is the next drop. The same logic applies to
unknown-label phrases — they fall to the lowest-numbered slot still free.

### 4.3 Worked example — 6-phrase track

Phrases (chronological): `Intro@0s → Build@16s → Drop@32s → Verse@64s →
Drop@96s → Outro@128s`.

After smart ordering:

| Slot | Label   | Position | Why                                  |
| ---- | ------- | -------- | ------------------------------------ |
| A    | Build   | 16s      | First non-Intro = mix-in point.      |
| B    | Outro   | 128s     | First Outro = mix-out window.        |
| C    | Drop    | 32s      | Priority 0, chronologically first.   |
| D    | Drop 2  | 96s      | Priority 0, chronologically second.  |
| E    | Verse   | 64s      | Priority 3.                          |
| F    | Intro   | 0s       | Priority 6 (lowest non-unknown).     |

### 4.4 Worked example — 12-phrase track (overflow)

A long progressive track with twelve PSSI entries:

```
Intro@0s    →  Build@16s   →  Drop@32s    →  Verse@48s   →
Bridge@64s  →  Drop@80s    →  Break@112s  →  Build@128s  →
Drop@144s   →  Verse@176s  →  Outro@200s  →  Outro@216s
```

Step 1 — Slot A is the first non-Intro chronologically: **Build@16s**.

Step 2 — Slot B is the first OUTRO: **Outro@200s**.

Step 3 — Remaining 10 phrases, sorted by `(priority, position)`:

| Sort key (priority, ms) | Label  | Position |
| ----------------------- | ------ | -------- |
| (0, 32 000)             | Drop   | 32s      |
| (0, 80 000)             | Drop 2 | 80s      |
| (0, 144 000)            | Drop 3 | 144s     |
| (1, 128 000)            | Build  | 128s     |
| (2, 216 000)            | Outro 2| 216s     |
| (3, 48 000)             | Verse  | 48s      |
| (3, 176 000)            | Verse 2| 176s     |
| (4, 112 000)            | Break  | 112s     |
| (5, 64 000)             | Bridge | 64s      |
| (6, 0)                  | Intro  | 0s       |

Step 4 — Take the first six (slots C–H), drop the rest:

| Slot | Label   | Position | Notes                                  |
| ---- | ------- | -------- | -------------------------------------- |
| A    | Build   | 16s      | Mix-in point. Renamed `Build (Mix In)`.|
| B    | Outro   | 200s     | First OUTRO = mix-out window.          |
| C    | Drop    | 32s      | Priority 0.                            |
| D    | Drop 2  | 80s      | Priority 0.                            |
| E    | Drop 3  | 144s     | Priority 0.                            |
| F    | Build 2 | 128s     | Priority 1.                            |
| G    | Outro 2 | 216s     | Priority 2.                            |
| H    | Verse   | 48s      | Priority 3, chronologically first.     |

**Dropped (would have been slot 8+):** the second Verse@176s, Break@112s,
Bridge@64s, and Intro@0s. Notice that every Drop survived — the cap
preferentially preserves high-priority phrases. The intro is the first
casualty, exactly as the priority table intends.

---

## 5. Bar-Interval Mode

When phrase data isn't available but the track has a BPM, `_bar_strategy()`
at `autocue/generator.py:144-165` places cues at fixed bar intervals.

### 5.1 Formula

```
bar_ms = (60000 / bpm) * 4                    # ms in one 4/4 bar
cue[i] = inizio_ms + (start_bar - 1 + i * bars_interval) * bar_ms
```

Defaults: `bars_interval=16`, `start_bar=1`, `inizio_ms=0`, `max_cues=8`.

The loop runs up to `max_cues + 64` iterations to give headroom for negative
`inizio_ms` skips, breaks early when `slot >= max_cues`, skips positions
< 0, and stops when `pos >= duration_ms`.

### 5.2 The BPM guard

The strategy **must** check `bpm > 0` (not just truthy). Rekordbox can store
BPM as the literal string `"0.0"` which is **truthy** in Python but
**zero-valued as a float** — feeding that to the formula would divide by
zero. The check at `autocue/generator.py:146`:

```python
bpm = float(content.BPM) / 100  # DB stores BPM as int×100
if bpm <= 0:
    return [], "bar"
```

The caller also guards: at `autocue/generator.py:216`:

```python
bpm_ok = float(getattr(content, "BPM", 0) or 0) / 100 > 0
```

This is the canonical pattern across the codebase — never trust a truthy
BPM check.

### 5.3 Inizio offset

The first beat of a track rarely sits at `t=0` — there's often digital
silence or a fractional bar before the downbeat. `prefs.inizio_ms` lets you
shift the bar grid forward so "Bar 1" lands on the real downbeat. Bar mode
hot cue names are `"Bar 1"`, `"Bar 17"`, `"Bar 33"`, etc.

### 5.4 BPM storage convention

Rekordbox's `DjmdContent.BPM` is stored as integer **×100** (so
`14000 == 140.00 BPM`). All consumers must divide by 100. The detection in
`detect_capability()` at `autocue/generator.py:124-141` does the same.

---

## 6. Heuristic Mode

`_heuristic_strategy()` at `autocue/generator.py:168-180` is the
last-resort fallback for tracks with no analysis at all (no ANLZ, no BPM).

Cues are placed every 30 seconds starting from `t=0`, named by their
position: `"0:00"`, `"0:30"`, `"1:00"`, `"1:30"`, etc. The strategy stops
when the position exceeds the track length (default 300 s if no
`content.Length`).

Confidence is set to `0.3` — these cues are guesses, not music-aware.

---

## 7. Memory Cues (Kind=0)

Memory cues are a separate Rekordbox concept from hot cues:

- They appear as **white triangles** on the waveform on the CDJ display.
- The CDJ's "Auto Cue" feature jumps to the first memory cue on track load.
- They do **not** consume hot cue slots (A–H).
- In [`DjmdCue`](GLOSSARY.md#djmdcue) they are encoded as
  [`Kind`](GLOSSARY.md#cue-encoding-kind-slot-inframe-outmsec) `= 0`
  (whereas hot cues are `Kind = 1..8`). See
  [Section 12](#12-writing-to-xml-vs-writing-to-db).
- In the Rekordbox XML format they have `Num = -1`.

`CuePoint.slot = -1` represents a memory cue (`autocue/models.py:92-94`).

### 7.1 The three memory cue modes

Controlled by `prefs.memory_cue_mode`:

| Mode          | Cues added                                                  |
| ------------- | ----------------------------------------------------------- |
| `"none"`      | No memory cues.                                             |
| `"load_only"` | One **Load Point** at the first phrase / `inizio_ms`.       |
| `"all"`       | Load Point + Mix In + Mix Out + Warning (phrase mode only). |

The legacy `prefs.add_memory_cue = True` boolean is an alias for
`"load_only"`, resolved by `_resolve_memory_cue_mode()` at
`autocue/generator.py:56-62`.

### 7.2 The full set ("all")

When `memory_cue_mode == "all"` and the strategy was `"phrase"`,
`generate_cues_for_track()` at `autocue/generator.py:274-309` builds:

1. **Load Point** — always. At the earliest hot cue position (phrase mode)
   or `max(0, inizio_ms)` for bar/heuristic modes. Color 0 (white).
2. **Mix In** — the slot-A position (after smart ordering), if it is
   > 500 ms away from the Load Point to avoid duplicate markers. Color 5
   (Green).
3. **Mix Out** — the position of the **last** Outro phrase. Color 3
   (Orange).
4. **Warning** — 16 bars before the end of the track, but **only** if the
   outro is shorter than 8 bars (or there is no Outro at all). This is a
   "wake up — track is ending" prompt. Color 2 (Red). Skipped if it would
   collide with an existing memory cue (< 500 ms apart).

The CDJ orders memory cues by insertion order, so the list is sorted by
position before being prepended to the cue list
(`autocue/generator.py:312-313`).

### 7.3 Confidence inheritance

Memory cue confidence mirrors the source strategy:
`{"phrase": 1.0, "bar": 0.6, "heuristic": 0.3}` (`autocue/generator.py:261`).

---

## 8. Cue Color Mapping

Two color tables live in `autocue/models.py`:

### 8.1 By label (phrase mode)

`LABEL_COLORS` at `autocue/models.py:77-85`:

| PhraseLabel | DJ Name | Color  | DjmdColor ID |
| ----------- | ------- | ------ | ------------ |
| INTRO       | Intro   | Green  | 5            |
| VERSE       | Verse   | Blue   | 7            |
| BRIDGE      | Bridge  | Aqua   | 6            |
| CHORUS      | Drop    | Red    | 2            |
| OUTRO       | Outro   | Orange | 3            |
| UP          | Build   | Pink   | 1            |
| DOWN        | Break   | Purple | 8            |

The mapping is applied at `autocue/generator.py:208`:

```python
c.color_id = LABEL_COLORS.get(c.label.value, 0)
```

The Drop is **red** so it punches visually on the CDJ waveform.

### 8.2 By slot (bar / heuristic / fill modes)

`SLOT_COLORS` at `autocue/models.py:75`:

```python
SLOT_COLORS = [5, 7, 6, 3, 2, 2, 1, 8]  # slots A→H
```

Bar-mode and heuristic-mode cues get their color from this table indexed by
slot number, so slot A is always Green, slot B Blue, etc. Fill cues added
in phrase mode pick from the same table by their final post-smart-ordering
slot (`autocue/generator.py:254-257`).

### 8.3 DjmdColor table

The integer color IDs map to Rekordbox's built-in palette as defined by
`DjmdColor.SortKey` (1 = Pink, 2 = Red, 3 = Orange, 4 = Yellow, 5 = Green,
6 = Aqua, 7 = Blue, 8 = Purple, 0 = no color). When colors are looked up at
runtime (e.g. in `db_writer.color_tracks_by_bpm()`), the actual VARCHAR
`DjmdColor.ID` is resolved via the SortKey because the ID is **not**
guaranteed to be an integer — it can be a UUID string.

---

## 9. Cue Naming

Cue names land in `DjmdCue.Comment` (for DB writes) and the XML `Name`
attribute (for XML writes).

### 9.1 Phrase mode

Names come from `DJ_NAMES[label]`. Labels appearing multiple times are
numbered:

- One Drop in the track → `"Drop"`.
- Three Drops → `"Drop"`, `"Drop 2"`, `"Drop 3"`.

Smart ordering adds `(Mix In)` and `(Outro)` suffixes when slot A and slot B
re-label a non-matching phrase. See `autocue/generator.py:89-95` and
`108-114`.

### 9.2 Bar mode

Names track the bar number: `"Bar 1"`, `"Bar 17"`, `"Bar 33"` for the
default 16-bar interval. The formula at `autocue/generator.py:160`:

```python
bar_number = prefs.start_bar + i * prefs.bars_interval
```

So `start_bar=5, bars_interval=16` produces `"Bar 5"`, `"Bar 21"`, `"Bar
37"`, etc.

### 9.3 Heuristic mode

Names are minutes:seconds formatted: `"0:00"`, `"0:30"`, `"1:00"`, `"1:30"`,
…. See `autocue/generator.py:174`.

### 9.4 Memory cues

Memory cues have fixed names: `"Load Point"`, `"Mix In"`, `"Mix Out"`,
`"Warning"`.

---

## 10. Confidence Levels

Every `CuePoint` carries a `confidence: float` field (default `1.0`). The UI
maps these into three badges:

| Confidence | Badge      | Strategy   | Backing data                          |
| ---------- | ---------- | ---------- | ------------------------------------- |
| 1.0        | **High**   | phrase     | PSSI + PQTZ from ANLZ files.          |
| 0.6        | **Medium** | bar        | BPM-derived bar grid, no phrase data. |
| 0.3        | **Low**    | heuristic  | 30-second intervals, no audio data.   |

The confidence value drives the small ℹ icon in the web UI's track card —
clicking it expands the `_explainCue(cue)` panel which lists *why* AutoCue
placed a cue at that point ("first non-Intro phrase", "16 bars after Bar
1", "30 seconds from track start", etc.). See
`docs/index.html` → `_explainCue`.

---

## 11. GenerationPrefs

The full dataclass at `autocue/generator.py:41-53`:

```python
@dataclass
class GenerationPrefs:
    mode: Literal["phrase", "bar", "auto"] = "auto"
    bars_interval: int = 16
    start_bar: int = 1
    max_cues: int = MAX_HOT_CUES  # = 8
    inizio_ms: int = 0
    add_memory_cue: bool = False  # legacy alias for memory_cue_mode="load_only"
    memory_cue_mode: Literal["none", "load_only", "all"] = "none"
    add_fill_cues: bool = False
    slot_priority: Literal["smart", "sequential"] = "smart"
```

| Field             | Default     | Behaviour                                                                                                |
| ----------------- | ----------- | -------------------------------------------------------------------------------------------------------- |
| `mode`            | `"auto"`    | Strategy selector. See [Section 2](#2-the-three-strategies).                                             |
| `bars_interval`   | `16`        | Spacing between bar-mode cues, in bars.                                                                  |
| `start_bar`       | `1`         | First bar number to place a cue on (1-indexed).                                                          |
| `max_cues`        | `8`         | Hard cap on total hot cues per track. Capped at `MAX_HOT_CUES = 8`.                                      |
| `inizio_ms`       | `0`         | Offset of the first beat in ms; bar mode adds this to every cue position.                                |
| `add_memory_cue`  | `False`     | Legacy alias for `memory_cue_mode="load_only"`. Resolved by `_resolve_memory_cue_mode()`.                |
| `memory_cue_mode` | `"none"`    | `"none"` / `"load_only"` / `"all"`. See [Section 7](#7-memory-cues-kind0).                               |
| `add_fill_cues`   | `False`     | Phrase mode only — add `analyze_fills()` results in slots not used by main phrases. Color via slot index.|
| `slot_priority`   | `"smart"`   | `"smart"` runs `_apply_smart_slot_order()`. `"sequential"` keeps chronological assignment.               |

---

## 12. Writing to XML vs Writing to DB

AutoCue has two output paths. They share the same `CuePoint` list but
encode it very differently.

### 12.1 XML output (`autocue/writer.py`)

`write_xml(tracks, output_path)` writes a Rekordbox XML file to be imported
via `File > Import Library` in Rekordbox.

For each cue, the XML `POSITION_MARK` is added via
`pyrekordbox.rbxml.RekordboxXml.add_track(...).add_mark(...)`:

```python
track.add_mark(
    Name=cue.name or cue.label.value,
    Type="cue",
    Start=cue.position_sec,
    Num=cue.slot,        # -1 for memory, 0..7 for hot cues A..H
)
```

The `Num` attribute maps directly from `CuePoint.slot` with no
transformation: `-1` is memory, `0` is A, `7` is H. **Per-cue color is
deliberately not written** — the Rekordbox XML schema only supports
track-level color, not cue-level. For per-cue colors, use DB-direct mode.

### 12.2 Direct DB write (`autocue/db_writer.py`)

`write_cues_to_db(content, cues, db, ...)` inserts rows into the `DjmdCue`
table on the live `master.db`.

**Slot encoding invariant** (load-bearing across the codebase):

```
DjmdCue.Kind = CuePoint.slot + 1   for hot cues (slot >= 0)
DjmdCue.Kind = 0                   for memory cues (slot == -1)
```

This is computed at `autocue/db_writer.py:223`:

```python
Kind=cue.slot + 1,
```

There is **no `Num` column on `DjmdCue`** — `Kind` is the wire format. The
XML `Num` is independent of the DB `Kind`.

### 12.3 Required DjmdCue fields when inserting

The full insert at `autocue/db_writer.py:206-231`:

```python
db.session.add(
    DjmdCue(
        ID=str(db.generate_unused_id(DjmdCue)),  # NO autogen default — must call this
        ContentID=content.ID,
        ContentUUID=content_uuid,                # mirror DjmdContent.UUID
        UUID=str(uuid4()),                       # fresh UUID per cue
        InMsec=cue.position_ms,
        InFrame=int(round(cue.position_ms * 150.0 / 1000.0)),  # CDJ: 150 sub-frames/sec
        InMpegFrame=0,
        InMpegAbs=0,
        OutMsec=-1,                              # not a loop
        OutFrame=0,
        OutMpegFrame=0,
        OutMpegAbs=0,
        Kind=cue.slot + 1,                       # the slot-to-Kind invariant
        Color=0,
        ColorTableIndex=cue.color_id,
        ActiveLoop=0,
        BeatLoopSize=0,
        CueMicrosec=0,
        Comment=cue.name or cue.label.value,
    )
)
```

Key invariants:

- `ID` is `VARCHAR(255)` with **no auto-generate default** — you must call
  `db.generate_unused_id(DjmdCue)` explicitly.
- [`InFrame`](GLOSSARY.md#cue-encoding-kind-slot-inframe-outmsec)
  `= round(position_ms * 150 / 1000)` because the CDJ stores cues
  at 150 sub-frames per second.
- `OutMsec = -1` marks the cue as a point (not a loop).

### 12.4 Backup, lock, and overwrite safety

Before any write, `db_writer.backup_database()` copies `master.db` (plus
`-wal` and `-shm` sidecars if present) to
`~/.autocue/backups/master_TIMESTAMP.db`.

`db_writer.rekordbox_is_running()` enforces that Rekordbox is closed before
writing — the SQLCipher database is locked while Rekordbox holds it open
and writes would silently fail or corrupt state. The mechanics of that
check are documented in detail in [Section 12.5](#125-db-lock-out-verification).

The DB-write code path uses a SAVEPOINT (`db.session.begin_nested()`) so a
failure inside the cue insert rolls back cleanly. On success both the
nested savepoint and the outer session commit.

### 12.5 DB lock-out verification

The full implementation at `autocue/db_writer.py:28-34`:

```python
def rekordbox_is_running() -> bool:
    """Return True if a Rekordbox process is running."""
    try:
        import psutil
        return any("rekordbox" in p.name().lower() for p in psutil.process_iter(["name"]))
    except ImportError:
        return False
```

**What it does.** It enumerates every running process via
`psutil.process_iter(['name'])`, lowercases each process name, and returns
`True` if the substring `"rekordbox"` appears anywhere in any of them.

- The match is **case-insensitive** (`.name().lower()`).
- The match is **substring-based**, not exact equality. So all of these
  trip it: `rekordbox`, `Rekordbox` (macOS app), `rekordbox.exe` (Windows),
  `rekordbox-helper`, `com.pioneerdj.rekordbox`.
- The check is **one process scan per call**; there is no caching. Each
  write endpoint re-checks immediately before backing up the DB.

**What it does NOT check.**

- It does **not** check for a SQLite/SQLCipher file lock on
  [`master.db`](GLOSSARY.md#db-filenames-and-sqlcipher-locking).
- It does **not** check for the presence of `master.db-wal` or
  `master.db-shm` sidecars.
- It does **not** open the database to attempt a probe write.
- It does **not** check that the running Rekordbox is actually using the
  same `master.db` the user is pointing AutoCue at.

This is purely a process-name probe. It is a fast, conservative heuristic,
not a true database-lock check.

**Failure modes.**

| Scenario | Effect | Honest assessment |
| -------- | ------ | ----------------- |
| User runs an unrelated process named `rekordbox-something` (e.g. a helper script, a renamed binary) | False positive — AutoCue refuses to write. | Annoying but safe. |
| User runs a custom Rekordbox build / wrapper whose process name does not contain `rekordbox` | False negative — AutoCue proceeds to write. The actual DB write may then succeed (no lock contention) or fail at the SQLCipher layer (lock contention). | Data risk if Rekordbox is genuinely holding the DB open. |
| `psutil` is not installed (e.g. minimal install missing the dependency) | The `ImportError` is caught and the function returns `False` — i.e. it **fails open**. AutoCue will attempt the write. | This is by design so that environments without `psutil` are not bricked, but it means the safety net is silently disabled. |
| User starts Rekordbox **after** the check passes but **before** the write completes | The check is point-in-time. No re-check during the write. The write may fail with an obscure SQLCipher error. | Race window. |

**HTTP surface.** Every write endpoint in `autocue/serve/routes.py` calls
`rekordbox_is_running()` at the top of its handler and returns
**HTTP 409 Conflict** with a `{"detail": "Rekordbox is running…"}` body
when it returns `True`. The endpoints that gate on this include
`/api/apply`, `/api/generate-apply`, `/api/generate-apply-stream`,
`/api/delete-cues`, `/api/color-tracks`, `/api/auto-tag`,
`/api/auto-tag/undo`, `/api/enrich-comments`, `/api/enrich-comments/stream`,
and `/api/restore`. Read-only endpoints (`/api/tracks`, `/api/health`,
`/api/classify`, etc.) do **not** call the check — they can safely run
while Rekordbox is open.

**Testing bypass.** The test suite never spawns a real Rekordbox process,
so two patterns short-circuit the check:

- `tests/test_db_writer.py` builds a mocked `db` via `_make_db()` and
  imports `db_writer` directly; the check is bypassed by patching the
  function or by never calling the write functions that gate on it.
- `tests/test_serve_routes.py` patches the symbol at its import site:
  `patch("autocue.db_writer.rekordbox_is_running", return_value=False)`.
  Tests covering the 409 path patch it with `return_value=True`.

---

## 13. Skipping and Overwriting

### 13.1 The `overwrite` flag

`write_cues_to_db()` accepts `overwrite: bool = False`. The default
behaviour is **conservative**:

```python
if not overwrite and has_existing_hot_cues(content, db) > 0:
    return 0  # silently skip — track already has cues
```

`has_existing_hot_cues()` at `autocue/db_writer.py:37-43` counts
`DjmdCue` rows where `Kind > 0` (excluding memory cues).

When `overwrite=True`:

- Existing hot cues whose `Kind` matches one of the new cues' Kinds are
  deleted first (only those Kinds — slots not being written are left
  untouched).
- Memory cues are only overwritten when `overwrite=True` **or** the track
  has no existing memory cues. This prevents silently destroying a DJ's
  manually placed memory cues.

### 13.2 The existing-cues banner

The UI surfaces a count of tracks with existing cues via
`has_existing_hot_cues()` before triggering Apply, so a user is warned how
many tracks would be skipped.

### 13.3 Memory cue protection logic

From `autocue/db_writer.py:185`:

```python
write_memory = bool(mem_cues) and (overwrite or has_existing_memory_cues(content, db) == 0)
```

In other words, memory cues are written when:

- The cue list contains memory cues, AND
- Either `overwrite=True` or the track currently has zero memory cues.

This is a one-way ratchet — once a track has memory cues, they're treated as
DJ-authored data and are protected from accidental overwrite unless you
explicitly opt in.

---

## 14. Edge Cases

### 14.1 BPM = `"0.0"` string

Rekordbox can store `DjmdContent.BPM` as the literal string `"0.0"`. This is
**truthy** in Python (`bool("0.0") == True`) but `float("0.0") == 0.0`.

Every BPM consumer in the codebase must use `float(bpm) > 0`, not
`if bpm`. The pattern at `autocue/generator.py:216`:

```python
bpm_ok = float(getattr(content, "BPM", 0) or 0) / 100 > 0
```

If this guard is omitted, the bar formula divides by zero and crashes.

### 14.2 Missing `Inizio` (first-beat offset)

`prefs.inizio_ms` defaults to `0`. If unset, bar mode places the first cue
at `t=0`, which may be inside digital silence. The CLI's `--inizio` flag and
the web UI's bar offset field let the user supply it manually.

### 14.3 Malformed ANLZ (ConstError / IndexError)

pyrekordbox raises `construct.ConstError` for unsupported ANLZ format
versions (e.g. Rekordbox 7's `PQT2` v2 tag with pyrekordbox 0.4.x) and
`IndexError` for missing tags. Every ANLZ access in `analyzer.py` is
wrapped in `try/except Exception` and falls through silently.

The resilient byte-level scanner (`_get_anlz_tags_resilient`) attempts
recovery on the same file even when pyrekordbox's parse failed — it skips
the broken tag and recovers the good ones.

### 14.4 Tracks with no `Length`

`_heuristic_strategy()` defaults to 300 s when `content.Length` is missing:

```python
dur_ms = int(float(getattr(content, "Length", 300) or 300) * 1000)
```

This is a safety net — the strategy will produce up to 8 cues at 30 s
intervals, capped to whatever the resulting track length suggests.

### 14.5 Degenerate PSSI tags

Some PSSI tags contain multiple entries at the **same beat number**, which
would translate to multiple cues at the same position. `analyze_track()`
dedupes by `seen_ms` at `autocue/analyzer.py:202-209`.

### 14.6 All-Intro tracks

If every PSSI phrase is labelled `INTRO`, smart slot ordering at
`autocue/generator.py:84-87` falls back to chronological assignment rather
than emitting zero cues.

### 14.7 Rekordbox running

`db_writer.rekordbox_is_running()` polls `psutil` for any process whose
name contains "rekordbox". If true, the CLI/server refuse to write —
SQLCipher cannot tolerate two writers. See
[Section 12.5](#125-db-lock-out-verification) for the full mechanism, what
it does **not** check, and the failure modes (false positives, false
negatives, missing `psutil`, race window).

---

## 15. XML Import Semantics

The Rekordbox XML import is **slot-level additive**: Rekordbox only writes
the cue slots that are present in the imported XML file. Slots that are
absent are left untouched in Rekordbox's existing data.

Consequences:

- If you import an XML containing only slot A and slot B, slots C–H in
  Rekordbox are unchanged.
- AutoCue **intentionally only wipes the slots it will overwrite** — the
  `DB-direct` write path deletes only `Kind` values in the set of new cues
  being inserted (`hot_kinds` in `autocue/db_writer.py:181`). Slots not
  present in the new list are preserved.
- This means re-running AutoCue with `max_cues=4` after a previous run
  with `max_cues=8` leaves slots E–H from the earlier run intact. Use
  "Delete cues" (`/api/delete-cues` or CLI equivalent) for a full wipe.

---

## 16. Examples

### Example 1: EDM track with full ANLZ data

`Some Big Track.mp3` — 128 BPM, has PSSI and PQTZ data.

PSSI phrases (chronological): `Intro@0s → Build@16s → Drop@32s → Verse@64s
→ Build@80s → Drop@96s → Outro@128s`.

| Decision         | Result                                                                |
| ---------------- | --------------------------------------------------------------------- |
| Strategy chosen  | `"phrase"` (ANLZ available).                                          |
| Slot A           | Build@16s (first non-Intro). Renamed to `"Build (Mix In)"`.           |
| Slot B           | Outro@128s (first Outro). Named `"Outro"`.                            |
| Slot C           | Drop@32s (priority 0).                                                |
| Slot D           | Drop 2@96s (priority 0, later).                                       |
| Slot E           | Build@80s (priority 1).                                               |
| Slot F           | Verse@64s (priority 3).                                               |
| Slot G           | Intro@0s (priority 6).                                                |
| Confidence       | `1.0` (High).                                                         |

### Example 2: Old track with BPM but no ANLZ

`Disco Classic.mp3` — 118 BPM, `inizio_ms=420`, no analysis files.

| Decision         | Result                                                                |
| ---------------- | --------------------------------------------------------------------- |
| Strategy chosen  | `"bar"` (no ANLZ, BPM valid).                                         |
| Bar duration     | `(60000 / 118) * 4 ≈ 2033.9 ms`.                                      |
| Cue positions    | `420 + 0×2034`, `420 + 16×2034 ≈ 33s`, `+ 32×2034 ≈ 65s`, …           |
| Cue names        | `"Bar 1"`, `"Bar 17"`, `"Bar 33"`, …                                  |
| Slot colors      | Green, Blue, Aqua, Orange, Red, Red, Pink, Purple (from `SLOT_COLORS`).|
| Confidence       | `0.6` (Medium).                                                       |

### Example 3: Voice memo with no analysis

`Studio Idea.m4a` — no BPM, no ANLZ, 180s duration.

| Decision         | Result                                                                |
| ---------------- | --------------------------------------------------------------------- |
| Strategy chosen  | `"heuristic"` (no BPM, no ANLZ).                                      |
| Cue positions    | 0, 30, 60, 90, 120, 150 s (stops past 180s).                          |
| Cue names        | `"0:00"`, `"0:30"`, `"1:00"`, `"1:30"`, `"2:00"`, `"2:30"`.           |
| Confidence       | `0.3` (Low).                                                          |

### Example 4: Phrase mode + memory cues "all"

Same track as Example 1, with `memory_cue_mode="all"`.

Memory cues prepended (Kind=0, do not consume slots A–H):

| Memory cue | Position                          | Color  |
| ---------- | --------------------------------- | ------ |
| Load Point | 0s (earliest hot cue position)    | None   |
| Mix In     | 16s (slot A position)             | Green  |
| Mix Out    | 128s (last OUTRO position)        | Orange |
| Warning    | (skipped — Outro is long enough)  | —      |

Hot cue slots A–H remain as in Example 1.

### Example 5: Phrase mode = explicit, no ANLZ → empty result

User clicks "Phrase mode only" in the UI and runs Apply on
`Disco Classic.mp3` (no ANLZ data). `generate_cues_for_track()` returns
`([], "phrase")` immediately at `autocue/generator.py:211-213` — no bar
fallback, no heuristic fallback. The UI shows "0 cues placed" so the user
knows the track lacks phrase data and can either run Rekordbox analysis
first or switch to auto mode.

---

## 17. File Map

| File                                 | Role                                                                 |
| ------------------------------------ | -------------------------------------------------------------------- |
| `autocue/generator.py`               | `generate_cues_for_track`, `_bar_strategy`, `_heuristic_strategy`, `_apply_smart_slot_order`, `_resolve_memory_cue_mode`, `detect_capability`, `GenerationPrefs`, `_SMART_PRIORITY`. |
| `autocue/analyzer.py`                | `analyze_track`, `analyze_fills`, `analyze_by_id`, `analyze_by_title`, `analyze_all`, `_get_pssi_and_pqtz`, `_get_anlz_tags_resilient` (XOR de-garble + tag-level error recovery). |
| `autocue/models.py`                  | `PhraseLabel`, `CuePoint`, `phrase_label()`, `_KIND_MAP`, `DJ_NAMES`, `LABEL_COLORS`, `SLOT_COLORS`. |
| `autocue/writer.py`                  | `write_xml(tracks, output_path)`. XML `Num` mirrors `slot` directly. |
| `autocue/db_writer.py`               | `write_cues_to_db`, `delete_cues_from_db`, `has_existing_hot_cues`, `has_existing_memory_cues`, `backup_database`, `rekordbox_is_running`, `color_tracks_by_bpm`, `_bpm_to_color_sort_key`. |
| `autocue/cli.py`                     | argparse front-end: `--track`, `--track-id`, `--library`, `--playlist`, `--dry-run`, `--overwrite`, `--inizio`, `serve`. |
| `autocue/serve/routes.py`            | `/api/generate`, `/api/apply`, `/api/generate-apply`, `/api/generate-apply-stream` (SSE). |

---

## 18. Testing

The generation engine is covered by four test modules under `tests/`:

| File                          | Tests | Scope                                                                                            |
| ----------------------------- | ----- | ------------------------------------------------------------------------------------------------ |
| `tests/test_models.py`        | 48    | `PhraseLabel`, `CuePoint`, `phrase_label()` lookup, `DJ_NAMES`/`LABEL_COLORS`/`SLOT_COLORS` tables. |
| `tests/test_analyzer.py`      | 39    | Mocked pyrekordbox ANLZ objects; PSSI parsing, mood/kind mapping, two-pass selection, error recovery. |
| `tests/test_generator.py`     | 79    | Smart slot order (A=mix-in, B=first Outro, C+ by `_SMART_PRIORITY`), confidence values, memory cue modes, fill cues, fallback chain. |
| `tests/test_writer.py`        | 39    | XML output: `Num` field mapping, memory cue (`Num=-1`), missing artist/title.                    |
| `tests/test_db_writer.py`     | 49    | DB-direct: `Kind = slot + 1` invariant, backup creation, overwrite vs skip, color-by-BPM mapping.|

Test conventions:

- Tests **mock pyrekordbox objects** rather than hitting a real database.
- ANLZ tests construct `SimpleNamespace` fakes with `entries`, `mood`,
  `beat`, `kind`, `time` fields — see `tests/test_analyzer.py`.
- Generator tests use `_make_fake_cues()` to avoid in-place slot mutation
  leaking between tests (slot ordering mutates the list).
- The autouse fixture in `tests/conftest.py` clears all analysis caches
  before each test (`energy._cache`, `classify._class_cache`,
  `score._mixability_cache`, and `similar.clear_index()`).

Run the suite:

```bash
pytest tests/test_models.py tests/test_analyzer.py tests/test_generator.py \
       tests/test_writer.py tests/test_db_writer.py
```

---

## 19. Related References

- **CLI usage**: see `docs/reference/cli-usage.md` for the full
  `autocue` command-line interface (`--track`, `--library`, `--playlist`,
  `--dry-run`, `--overwrite`, `serve`).
- **High-level feature docs**: see `docs/FEATURES.md` for end-user-facing
  descriptions of cue placement modes, memory cue modes, and the web UI's
  Preview / Apply flow.
- **Cue Quality Checker**: see `autocue/analysis/quality.py` and
  `docs/reference/library-health.md` — scores tracks 0–100 based on
  presence of hot cues, phrase data, beat grid, duplicates, and naming.
- **Direct-write safety**: see CLAUDE.md "Key constraints" for the full
  list of invariants enforced when writing to `master.db` (slot/Kind
  mapping, required `DjmdCue` fields, the Rekordbox-running guard, the
  `Commnt` vs `Comment` column-name gotcha).
- **Hot cue generation guide (sibling)**: see
  `docs/reference/hot-cue-generation.md` for a higher-level walkthrough
  aimed at first-time AutoCue users.

# P1-GROUND — AUTOLOOPS concrete-design grounding (Serato-first → RB XML). READ-ONLY.

Scope LOCKED: Serato tags first, then Rekordbox XML. Source-cited spec to build a DESIGN from.

**TL;DR for the 4 questions:**
1. Serato LOOP layout: CUE is fully in our code and is a faithful Holzhaus impl; LOOP is **NOT
   in our code** (build/parse handle CUE only) — best-known spec below, with the color/reserved
   middle bytes flagged as **probe-verify**. Framing, start, end, name offset are high-confidence.
2. **YES — Rekordbox stores named loops** exactly like cues: the name lives in
   `DjmdCue.Comment`, which `write_cues_to_db` already sets for every row; XML `add_mark`
   carries `Name + Type="loop" + Start + End`.
3. Naming source EXISTS: analyzer already emits semantic labels (`PhraseLabel` →
   `DJ_NAMES`: Intro/Build/Break/Drop/Outro/…) — no numeric-only ids.
4. Pairing feed = `export_pairs` list of `(content, cues)` → `build_markers2(cues)`; loops must
   ride in that same `cues` list as loop-flagged CuePoints.

---

## (1) SERATO MARKERS2 LOOP TAG FORMAT

### How the CUE entry is serialized today (`serato_writer.py:79-98`) — [EXISTS, exact]
`build_markers2` emits, per hot cue (memory cues slot<0 skipped, `serato_writer.py:83-84`):
```
entry = b"CUE\x00"                      # entry-type name, ASCII, NUL-terminated
      + len(data).to_bytes(4,"big")     # uint32 BE payload length
      + data
data  = b"\x00"                         # 0x00  reserved (1)
      + bytes([cue.slot])               # 0x01  index (uint8, 0-based)
      + position_ms.to_bytes(4,"big")   # 0x02  position (uint32 BE, ms)
      + b"\x00"                          # 0x06  reserved (1)
      + _cue_rgb(cue)                    # 0x07  color RGB (3 bytes)
      + b"\x00\x00"                      # 0x0a  reserved (2)
      + name(utf-8) + b"\x00"            # 0x0c  name, NUL-terminated
```
This is byte-exact to the Holzhaus `CueEntry` struct `>cBIc3s2s` + name (offsets 0x00–0x0c). Our
CUE code being a **faithful implementation of that reference** is the cross-check that makes the
same repo's LoopEntry the correct target.

### LOOP entry — NOT implemented in our code; best-known spec (Holzhaus serato-tags)
`build_markers2` never emits a LOOP entry and `parse_markers2` only decodes `etype=="CUE"`
(`serato_writer.py:147`) — so **the loop layout is not inferable from our source**. From the
reverse-engineered reference (Holzhaus `serato_markers2` `LoopEntry`, struct `>cBII4s4sB?` +
name; cross-checked against the docs table). Framing is generic (the parse loop reads any
NUL-terminated type string, `serato_writer.py:141-145`), so `b"LOOP\x00" + uint32be len + data`:

| Off | Size | Field | Type / value | Confidence |
|----:|-----:|-------|--------------|-----------|
| 0x00 | 1 | reserved | `0x00` | HIGH |
| 0x01 | 1 | index | uint8, 0-based loop number | HIGH |
| 0x02 | 4 | **start position** | uint32 BE, **milliseconds** | HIGH |
| 0x06 | 4 | **end position** | uint32 BE, ms; **`0xFFFFFFFF` = undefined** | HIGH |
| 0x0a | 4 | reserved (`field5`) | 4 bytes, commonly `0xFFFFFFFF` | MED |
| 0x0e | 4 | reserved / color block (`field6`) | 4 bytes | **LOW — probe** |
| 0x12 | 1 | color | uint8 (per struct) *or* part of a 4-byte ARGB starting 0x0e (per docs table) | **LOW — probe** |
| 0x13 | 1 | **locked** | uint8 bool, `0x00` unlocked / `0x01` locked | HIGH |
| 0x14 | var | **name** | UTF-8, NUL-terminated | HIGH (offset fixed at 20) |

**Anchors (high confidence):** type string `LOOP`; positions are **ms, uint32 BE** (same units
& endianness as CUE); `0xFFFFFFFF` end = undefined; name is NUL-terminated UTF-8 at fixed offset
**0x14** (fixed portion = 20 bytes). endianness/units identical to the CUE path we already ship.

**Ambiguous (the 8 bytes 0x0a–0x12):** the two reference forms disagree on how loop **color** is
packed (1-byte at 0x12 vs 4-byte ARGB at 0x0e) and what the two reserved 4-byte fields hold.
Historically Serato loops render in a fixed loop color, so this rarely matters for display, but
we must not corrupt the entry.

**→ Probe-verify before shipping the writer** (cheap, deterministic):
1. In Serato DJ Pro, save 1–2 **named** loops on a throwaway MP3, lock one.
2. Read the raw `GEOB:Serato Markers2` back (extend `parse_markers2` to keep `etype=="LOOP"`
   and hex-dump `data`) → capture the real bytes 0x0a–0x13 for a locked and an unlocked loop.
3. Make our LOOP serializer reproduce those exact reserved/color/locked bytes; assert a
   byte-for-byte round-trip in a unit test (mirrors how CUE is already tested).

### Two known quirks — CONFIRM they apply to loops → YES (both are payload/file-level)
- **Legacy `Markers_` deletion** (`serato_writer.py:15` docstring; deletes at `:267` ID3
  `delall("GEOB:Serato Markers_")`, `:280` FLAC `pop("SERATO_MARKERS")`, `:291` MP4
  `pop(MP4_V1)`). This wipes the **whole legacy tag per file**, independent of entry type — and
  the legacy `Markers_` format can itself carry loops, so deleting it is **more** important for
  loops (a stale legacy loop would otherwise shadow our Markers2 loop). Applies. ✓
- **No `=` base64 padding** (`wrap_outer` `serato_writer.py:104-108`, `.replace("=","A")`;
  FLAC/MP4 `build_envelope` `rstrip("=")` `:120`). Both operate on the base64 of the **entire
  payload**, which will now include LOOP entries → applies unchanged. ✓

---

## (2) REKORDBOX NAMED-LOOP PARITY — **direct answer: YES, RB saves named loops like Serato**

- **Name column = `DjmdCue.Comment`.** `write_cues_to_db` already sets it for every row:
  `Comment=cue.name or cue.label.value` (`db_writer.py:528`). `read_hot_cues` reads it back as
  the cue name (`db_writer.py:176`). A loop row uses the **same** column — so naming a loop is
  zero new plumbing beyond setting `OutMsec/OutFrame/BeatLoopSize` (the sentinels currently
  pinned at `db_writer.py:518-526`).
- **Memory-loop (Kind=0) vs hot-loop (Kind 1–8):** both carry the name in `DjmdCue.Comment`
  identically; `Kind` only selects hot-slot vs memory placement (`db-constraints.md:9`,
  `models.py:92-93`). Hot loops show the name on the hot-cue button; memory loops in the
  memory-cue list. Same name field either way.
- **XML export carries the name:** `RekordboxXml.add_mark(Name, Type="cue", Start, End=None,
  Num=-1)` accepts `Type="loop"` (POSMARK "4"→"loop") + `Name` + `Start` + `End`. Our
  `writer.py:46-51` hardcodes `Type="cue"` with no `End` and already passes
  `Name=cue.name or cue.label.value` → a named loop export is `Type="loop", End=<end_sec>` with
  the name unchanged.
- Caveat (from P0, still open): mirror-first can't see EXISTING RB loops until `read_hot_cues`
  carries `OutMsec` (currently dropped, `db_writer.py:170-178`).

---

## (3) LOOP NAMING SOURCE — semantic phrase labels EXIST (not numeric-only)

- `PhraseLabel` enum: `INTRO, VERSE, BRIDGE, CHORUS, OUTRO, UP, DOWN, UNKNOWN`
  (`models.py:5-13`).
- `phrase_label(mood, kind)` maps PSSI **mood + kind ints → PhraseLabel** (`models.py:16-56`),
  called in the analyzer per phrase (`analyzer.py:182,198`).
- **DJ display names** (`DJ_NAMES`, `models.py:62-71`): `INTRO→"Intro", VERSE→"Verse",
  BRIDGE→"Bridge", CHORUS→"Drop", OUTRO→"Outro", UP→"Build", DOWN→"Break", UNKNOWN→""`.
- `analyze_track` **already names cues** from these labels and disambiguates repeats
  ("Drop 1"/"Drop 2", `analyzer.py:227-239`); the name lands on `CuePoint.name` with the
  semantic `CuePoint.label` alongside (`analyzer.py:240-243`).
- → Auto-loops can be named **"Intro" / "Break" / "Drop" / "Outro"** exactly like the Serato
  screenshot, straight from the existing label pipeline. (Screenshot mapping: "Intro"=INTRO,
  "Break"=DOWN, "Outro"=OUTRO.) Loop LENGTH in bars is already computable via
  `CuePoint.phrase_bars` + beat-grid `bar_ms` (P0 §3).

---

## (4) SERATO WRITE PAIRING — where a generated loop list joins the markers

Feed today (`cli.py:179-205`, just read end-to-end):
```
export_pairs = []                       # list[(content, cues)]
for content, generated, _ in tracks:
    existing = read_hot_cues(content, db)      # mirror-first
    export_pairs.append((content, existing or generated))
summary = write_serato(export_pairs, overwrite=args.overwrite)   # cli.py:205
```
- `write_serato` iterates the pairs, resolves the file path, and calls
  `build_markers2(cues)` → embeds per container (`serato_writer.py:341-379`, build at `:257`).
- `build_markers2` emits **one CUE entry per cue** over `sorted(cues, key=slot)`, skipping
  memory cues slot<0 (`serato_writer.py:82-96`).
- **Join point = the `cues` list in each `(content, cues)` pair, consumed by `build_markers2`.**
  Cleanest wiring: loops ride **inside that same `cues` list** as loop-flagged `CuePoint`s (needs
  the P0 CuePoint loop-end field), and `build_markers2` branches CUE-vs-LOOP on that flag. The
  mirror-first source (`read_hot_cues`) must also carry `OutMsec` so existing RB loops mirror
  into Serato loops rather than degrading to point cues.

---

## Build-order implication (Serato-first, for the DESIGN)
1. Extend `CuePoint` with loop-end/beats (keystone, P0 §4). 2. **Probe a real Serato loop file**
to lock the 8 ambiguous bytes. 3. LOOP branch in `build_markers2` + LOOP decode in
`parse_markers2` (round-trip test). 4. `read_hot_cues` carries `OutMsec` (mirror-first loops).
5. Loop-naming/length policy off phrase labels + `phrase_bars`. 6. Then RB XML: `writer.py`
`Type="loop", End=…`. (DB `write_cues_to_db` loop write is a later increment, out of this scope.)

<!-- P1-GROUND-AUTOLOOPS -->
STATUS: DONE

---

# P6 DOCS-IMPACT — AUTOLOOPS (advisory; NO edits applied). READ-ONLY.

**Shipped surface verified against live code (branch feat/autoloops, 12 commits 784cce4…5d51872).**
One correction to the brief before anything else: **the XML loop path is code-present but NOT
CLI-exposed.** `writer.py:46-55` can emit `<POSITION_MARK Type="loop" End>`, but the CLI wires
`--loops` into the **Serato path only** and prints *"Note: --loops currently writes loops only
with --serato (Rekordbox XML loop export arrives in a later increment)"* (`cli.py:271-275`). Also:
loops are **memory loops (slot=-1, Kind=0)** and are **NOT written to master.db** —
`write_cues_to_db` still pins `OutMsec=-1` (`db_writer.py:538,546`). So the user-facing feature is
**Serato-tag loops only**. Docs must say that, not "loops across Serato+XML".

Verified policy (`analyzer.py:32-105`, `plan_loops`): categories **Intro/Outro (≤16 bars),
Break=DOWN/Build=UP (≤8 bars)** (`_LOOP_CATEGORIES` :32-38); require `phrase_bars >= 4`
(`_MIN_LOOP_PHRASE_BARS` :40); length = largest power-of-2 that fits, clamped before track end;
one loop per section, priority Intro→Outro→Break→Build, **cap 4/track** (`_MAX_LOOPS_PER_TRACK`
:42); Build default-eligible lowest priority (R-NC8); `bar_ms<=0` ⇒ no loops. Tests:
`tests/test_autoloops.py` (58 tests, 18 classes) + `tests/test_autoloops_golden.py` (20, verifier-owned).

| Doc | Verdict | What |
|---|---|---|
| CLAUDE.md — must-know bullet | **[MISSING]** | loops gotcha (Serato-only, memory Kind=0, no DB write, read-back) |
| CLAUDE.md — CLI §1 line 43 | **[STALE]** | `--serato` clause omits `--loops` |
| CLAUDE.md — dev-commands 90-93 | [MISSING] (optional) | a `--loops` example line |
| docs/FEATURES.md — Feature 13b | **[MISSING]** | user-facing auto-loops paragraph (primary home) |
| .claude/project/architecture.md — module map | **[STALE]** | analyzer/serato_writer/writer/db_writer/models loop lines |
| .claude/project/analysis-and-testing.md | **[MISSING]** | loop-policy + tests subsection |
| .claude/project/db-constraints.md | **[MISSING]** | DjmdCue OutMsec/BeatLoopSize loop read-back bullet |
| .claude/project/api-design.md | **[SKIP]** | read_hot_cues is CLI-only; no API loop surface (verified) |
| HANDOFF.md | **[MISSING]** (coordinator-owned P8) | autoloops "shipped" + next-work |

---

### 1. CLAUDE.md — must-know constraints — **[MISSING]** (highest value; "read every session")
**Home:** the "Must-know constraints" bulleted list (after the Serato-related bullets). **Add:**
```md
- **Auto-loops (`--loops`)** are **Serato-only today** and are **memory loops** (`slot=-1`,
  `DjmdCue.Kind=0`) — they ride in the same `(content, cues)` list `build_markers2` serializes
  (`autocue/serato_writer.py`) and are **never written to `master.db`** (`write_cues_to_db` still
  pins `OutMsec=-1`). `writer.py` can emit `<POSITION_MARK Type="loop" End>` but the CLI wires
  `--loops` into `--serato` only (`cli.py` prints a "later increment" note for the XML path).
  Policy in `analyzer.plan_loops`: Intro/Outro (≤16 bars) + Break/Build (≤8), power-of-2, phrase
  ≥4 bars, one per section, cap 4/track. Mirror-first: `read_hot_cues` now carries `OutMsec`
  → `loop_end_ms`, so an existing Rekordbox loop (a slot with `OutMsec>InMsec`) mirrors as a loop.
```

### 2. CLAUDE.md — line 43 (Python CLI description) — **[STALE]**
**Replace** the `--serato` clause tail `…win over generation; `autocue/serato_writer.py`).` **with:**
```md
…win over generation; `autocue/serato_writer.py`). Add `--loops` to also emit named,
bar-length **Serato LOOP** regions at phrase edges (Intro/Outro/Break/Build; `analyzer.plan_loops`).
```

### 2b. CLAUDE.md — dev-commands block (90-93) — [MISSING] (optional, low value)
**Add after line 93:**
```md
autocue --library --serato --loops   # also write named bar-length Serato LOOP regions
```

### 3. docs/FEATURES.md — Feature 13b Serato Export — **[MISSING]** (primary user-facing home)
**Home:** insert a subsection between "### What it does" (ends ~line 660) and "### Safety" (~663),
OR append after the "### Serato caveat" block (~672). **Add:**
```md
### Auto-loops (`--loops`)

Add `--loops` to also write **named, bar-length loop regions** into the Serato tags —
`autocue --library --serato --loops`. AutoCue reads Rekordbox's phrase analysis and places one
loop per section at the phrase downbeat, beat-grid aligned:

- **Intro / Outro** loops up to 16 bars; **Break / Build** loops up to 8 bars (always a
  power-of-two number of bars that fits the phrase; phrases under 4 bars are skipped).
- At most **4 loops per track**, priority Intro → Outro → Break → Build.
- Loops are **memory loops** (they don't consume a hot-cue slot) and are named "Intro",
  "Outro", "Break", "Build".
- **Existing Serato loops are preserved**; a generated loop whose start collides with a cue you
  already have is dropped (mirror-first). Requires a beat grid — tracks without one are skipped.

Loops are written **only with `--serato`** today. Rekordbox XML loop export is a later increment.
Preview with `--loops --serato --dry-run`, which prints each loop's name, time range and bar count.
```

### 4. .claude/project/architecture.md — module map — **[STALE]** (5 lines)
- **`models.py`** (line 8) — append: `CuePoint now carries loop_end_ms/loop_beats + is_loop property (a loop region vs point cue).`
- **`analyzer.py`** (line 8) — append: `plan_loops()/analyze_loops() — pure loop-generation policy (Intro/Outro/Break/Build, power-of-2 bars, memory loops slot=-1) + its ANLZ/beat-grid driver.`
- **`writer.py`** (line 11) — append: `is_loop CuePoints export as <POSITION_MARK Type="loop" Start/End> (add_mark End=loop_end_ms/1000).`
- **`db_writer.py`** (line 12) — append: `read_hot_cues() carries OutMsec→loop_end_ms so an existing Rekordbox loop reads back as a loop CuePoint (mirror-first). write_cues_to_db still writes point cues only (OutMsec=-1).`
- **`serato_writer.py`** (line 13) — append: `build_loop_entry()/parse loop decode — Serato Markers2 LOOP entries (write + decode + preserve existing); generated loops indexed past the max preserved loop index.`

### 5. .claude/project/analysis-and-testing.md — **[MISSING]** (new subsection)
**Home:** after "## Comment enrichment / Cue quality" (before "## Analysis caches"). **Add:**
```md
## Auto-loops (`--loops`, Serato-first)

- **Policy** in `analyzer.plan_loops(phrases, bar_ms, total_ms=None)` — PURE + fully unit-testable
  (no DB). Rules: restrict to INTRO/OUTRO/DOWN(Break)/UP(Build) — never Verse/Drop/Bridge; length =
  largest power-of-2 bars that fits `phrase_bars`, capped 16 (Intro/Outro) / 8 (Break/Build),
  require `phrase_bars >= 4`; start = phrase downbeat; one loop per section (first qualifying
  phrase), priority Intro→Outro→Break→Build, cap 4/track; clamp the loop end before track end
  (terminal Outro's bar length is measured against the track end — auditor #1); `bar_ms<=0` ⇒ [].
  `analyzer.analyze_loops(content, db)` is the thin ANLZ driver (PSSI phrases + PQTZ beat grid →
  `plan_loops`); it warns only on a genuinely missing beat grid (P-10 breadcrumb), stays silent on
  the legit "no eligible phrase".
- **Loops are memory loops** (`slot=-1`, `Kind=0`) merged onto the export list by
  `cli._merge_loops` (existing entry at the same start wins). Serato write/decode/preserve in
  `serato_writer.py`; existing Serato LOOP entries are kept and generated loops indexed past the
  highest preserved loop index (N1).
- **Tests**: `tests/test_autoloops.py` (58 tests: CuePoint loop fields, plan_loops guards/length/
  label-restriction/priority-and-cap, analyze_loops terminal-phrase + breadcrumb, --loops flag,
  _merge_loops, read_hot_cues OutMsec, Serato loop write/decode/preserve + decode-fail breadcrumb,
  XML loop mark, dry-run preview, generated-loop index) + `tests/test_autoloops_golden.py` (20,
  verifier-owned disjoint golden suite).
```

### 6. .claude/project/db-constraints.md — **[MISSING]** (new bullet)
**Home:** after the "DjmdCue ID generation" bullet (line 25). **Add:**
```md
- **Loop read-back (autoloops)**: a saved Rekordbox **loop** is a `DjmdCue` row whose
  `OutMsec > InMsec` (a point cue keeps the `OutMsec=-1` sentinel). `read_hot_cues()` carries
  `OutMsec` (and `BeatLoopSize`) back as `CuePoint.loop_end_ms`/`loop_beats` so existing loops
  mirror into Serato as loops rather than degrading to points. **The write path does NOT emit
  loops yet** — `write_cues_to_db` still writes `OutMsec=-1, BeatLoopSize=0` for every row
  (`db_writer.py:538,546`); auto-loops are written only into Serato tags (`serato_writer.py`),
  never into `master.db`.
```

### 7. .claude/project/api-design.md — **[SKIP]** (verified)
`read_hot_cues` is imported only by `cli.py` (`cli.py:229,236`); **no serve route consumes it** and
no schema exposes a loop field (`CueDetail` = slot/name/pos_sec unchanged). The only `OutMsec` in
`serve/` is the pre-existing cue-tools *shift* preserve path (`routes.py:2436-2441`), untouched by
autoloops. No API surface changed → no edit.

### 8. HANDOFF.md — **[MISSING]** (coordinator-owned at P8 CLOSE)
HANDOFF is scoped to the 2.0 redesign program; autoloops is unrelated net-new work. Recommend the
coordinator add, near "Program status" / next-work:
```md
## Auto-loops (feat/autoloops) — shipped increment 1 (Serato-first)
`--loops` CLI flag writes named bar-length **Serato LOOP** regions at phrase edges
(Intro/Outro/Break/Build; `analyzer.plan_loops`); Serato Markers2 LOOP write+decode+preserve;
`read_hot_cues` carries OutMsec for mirror-first loops; `CuePoint` gains loop_end_ms/loop_beats/
is_loop. Memory loops only (Kind=0); NOT written to master.db. **Next:** wire `--loops` into the
Rekordbox XML path (writer.py capability exists, CLI-gated off); optional server/web-UI surface.
```
(Flag as coordinator-owned — P6 doc edits 1–6 are implementer-applied; HANDOFF is P8.)

<!-- P6-AUTOLOOPS -->
STATUS: DONE

---

# P0-GROUND inc-3 — DB-DIRECT loop write (CLI → master.db). READ-ONLY, no edits.

⚠️ **This is the first AutoCue CLI path that would MUTATE the real Rekordbox DB.** Unlike the XML
path (the DJ reviews + imports; slot-level additive — `db-constraints.md:13`), a DB-direct write is
immediate and irreversible except via backup. Headline verdict below.

> **STATE CHANGE since my P6 pass:** the CLI now ALSO writes loops to XML (`cli.py:283-293`, the
> "later increment" note is gone). Current shipped loop surfaces = **Serato tags + Rekordbox XML**.
> DB-direct is the missing third. (P6 doc edit text that says "Serato-only" is now stale — flag to
> the implementer before it's applied.)

## 🚨 HEADLINE — `write_cues_to_db` is UNSAFE for loop writing on BOTH branches. Do not reuse it.

`write_memory` (db_writer.py:504) forces a lose-lose:
- `overwrite=False` + track has ANY existing Kind=0 row → `write_memory=False` → **the loop is
  silently NOT written** (no delete, but no loop either). Silent no-op.
- `overwrite=True` → **DELETES EVERY Kind=0 row for the track** (`:519-524`) — the DJ's
  hand-placed memory cues AND existing memory loops — and re-inserts only what the caller passed.
  **This is the clobber.**
- Third trap: `overwrite=False` + track has any hot cue → the whole track returns 0 at `:484-486`
  before memory logic is even reached.

**→ [RISK] Calling `write_cues_to_db(…, overwrite=True)` with memory loops WIPES the user's memory
cues.** The safe path is a NEW append-only function (§3, Option B).

---

## (1) `write_cues_to_db` FULL behavior — db_writer.py:469-558 [EXISTS]

`write_cues_to_db(content, cues: list[CuePoint], db, *, overwrite=False, dry_run=False) -> int`

| Step | Line | Behavior |
|---|---|---|
| Track-level skip | 484-486 | `not overwrite and has_existing_hot_cues>0` → **return 0** (skips whole track). `has_existing_hot_cues` counts `Kind > 0` (129-135) → **includes hot loops**. |
| dry_run | 488-490 | logs, **returns 0**, no write |
| empty cues | 492-493 | return 0 |
| split | 498-500 | `mem_cues = slot==-1`; `hot_cue_list = slot>=0`; `hot_kinds = {slot+1}` (never 0) |
| **write_memory gate** | 504 | `bool(mem_cues) and (overwrite or has_existing_memory_cues(...)==0)` — `has_existing_memory_cues` counts **ALL Kind=0** (138-144), i.e. memory cues *and* memory loops |
| hot DELETE | 510-518 | deletes **only the Kind values being written** → slot-level additive, untouched slots survive ✅ |
| **memory DELETE** | 519-524 | `if write_memory:` → `.filter(ContentID==…, Kind==0).delete()` — **deletes ALL Kind=0 rows, not just the ones being rewritten** ❌ |
| INSERT | 525-550 | one `DjmdCue` per cue; `ID=db.generate_unused_id(DjmdCue)` (530), `UUID=uuid4()` (533), `InFrame=round(pos*150/1000)` (527), loop cols pinned to non-loop sentinel (538-546) |
| commit | 551-552 | `begin_nested()` savepoint (509) → `sp.commit()` → `db.session.commit()` |
| returns | 554 | `len(cues_to_write)` (hot + memory-if-written) |
| on error | 555-558 | `db.session.rollback()` + `logger.exception` + **re-raise** |

---

## (2) The LOOP-COLUMN change — exact columns + UNITS (db_writer.py:538-546)

All confirmed against the read side (`read_hot_cues`) and `plan_loops`:

| Column | Current | Loop value | Unit / evidence |
|---|---|---|---|
| `OutMsec` | `-1` (538) | `cue.loop_end_ms` | **milliseconds** — same unit as `InMsec=cue.position_ms` (534). `-1` is the point-cue sentinel; read side treats `OutMsec > InMsec` as a genuine loop (180) |
| `OutFrame` | `0` (539) | `int(round(cue.loop_end_ms * 150.0 / 1000.0))` | **150 sub-frames/sec**, exactly mirroring `in_frame` (527) + `db-constraints.md:25` |
| `OutMpegFrame` | `0` (540) | **leave `0`** | matches `InMpegFrame/InMpegAbs = 0` (536-537) — unused on this path |
| `OutMpegAbs` | `0` (541) | **leave `0`** | ditto |
| `ActiveLoop` | `0` (545) | **leave `0`** | `0` = saved-but-**unarmed** loop (what we want); `1` would mark it the currently-armed loop |
| `BeatLoopSize` | `0` (546) | `cue.loop_beats` | **BEATS — CONFIRMED.** Read side maps `BeatLoopSize → loop_beats` verbatim (184-187), and `plan_loops` sets `loop_beats = loop_bars * 4` (analyzer.py:97) ⇒ bars×4 beats ✅ |
| `Kind` | `cue.slot+1` (542) | `cue.slot+1` → **`0`** for `slot=-1` | memory loop = Kind 0 (`db-constraints.md:9,17`) |
| `Comment` | (548) | `cue.name` | the loop NAME ("Intro"/"Outro"/"Break"/"Build") |
| `ColorTableIndex` | (544) | `cue.color_id` | unchanged |

---

## (3) NO-CLOBBER — the critical safety design

### How Rekordbox distinguishes a memory LOOP from a memory CUE
Both are **`Kind=0`**. The discriminator is **`OutMsec`**: a memory *cue* keeps `OutMsec=-1`
(point); a memory *loop* has `OutMsec > InMsec` (region). Same discriminator `read_hot_cues` already
uses (`db_writer.py:174,180`). **⇒ memory cues and memory loops COEXIST in the same Kind=0 row set**
— a track can hold both. So writing a memory loop **must never DELETE Kind=0**.

### Options weighed

- **Option A — read-merge-rewrite the whole Kind=0 set (`overwrite=True`): ❌ REJECT.**
  Reconstructing existing rows from `CuePoint` is **lossy**: CuePoint carries only
  position/name/color/loop_end/loop_beats — the round-trip drops `CueMicrosec`,
  `InMpegFrame/InMpegAbs`, exact `OutFrame`, `ActiveLoop`, `Color`, `UUID`. Worse, **there is no
  reader for Kind=0 rows at all today** — `read_hot_cues` filters `Kind >= 1` (`:158`);
  `has_existing_memory_cues` only *counts* (138-144). Any bug here = **permanent memory-cue data
  loss on the DJ's real library**. Highest blast radius.

- **Option C — relax the Kind=0 delete inside `write_cues_to_db`: ❌ REJECT.**
  That function is the shared write path for `/api/apply` (routes.py:1020),
  `/api/generate-apply` (:1385) and the SSE stream (:1233,1272), and its memory semantics back the
  existing `memory_cue_mode` (`none`/`load_only`/`all`) feature. Changing it risks regressing a
  shipped feature on the server surface. Leave it untouched.

- **✅ Option B — NEW dedicated append-only `write_loops_to_db()`. RECOMMENDED.**
  **Never issues a DELETE on Kind=0 ⇒ clobber is impossible by construction.**
  ```
  write_loops_to_db(content, cues, db, *, dry_run=False) -> int
    1. loops = [c for c in cues if c.is_loop and c.slot == -1]      # memory loops only
    2. existing = {InMsec of every Kind=0 row for this ContentID}   # one cheap query
       → skip any loop whose start collides (mirror-first — same semantics as
         cli._merge_loops, cli.py:86-96) ⇒ ALSO gives idempotency (re-run adds nothing)
    3. INSERT only the surviving loop rows with the §2 columns (Kind=0, OutMsec/OutFrame/
       BeatLoopSize set), ID=db.generate_unused_id(DjmdCue), UUID=uuid4()
    4. begin_nested() savepoint → sp.commit() → db.session.commit(); on exception
       rollback + logger.exception + raise   (mirror db_writer.py:508-558)
    5. breadcrumb (logger.info) per skipped-for-collision loop — silent-failure lens
  ```
  Properties: **no DELETE anywhere** · existing memory cues untouched · existing memory loops
  untouched · idempotent on re-run · reuses the proven INSERT + savepoint pattern · leaves the
  shared server write path alone.

  *Collision tolerance:* compare `InMsec` with a small window (the codebase precedent for
  "same position" is ±500 ms in `generator.py:235,305` and <2 frames ≈13 ms in
  `analysis-and-testing.md:9`) — DESIGN decision, not a research one; exact-match is the
  conservative default and matches `cli._merge_loops` (exact `position_ms` set).

---

## (4) SAFETY WIRING — backup + Rekordbox-closed guard [EXISTS, reuse verbatim]

The server apply route is the contract to copy (**routes.py:975-997**):
1. **`_rb_running(db)` → HTTP 409** (`:980-981`) — wraps `rekordbox_is_running(db._db_dir/"master.db")`
   (`db_writer.py:107-126`: psutil process probe **+** fcntl/msvcrt exclusive lock; pass `db_path`
   or the lock probe is skipped).
2. **Backup BEFORE any write, skipped only on dry_run** (`:983-997`): resolves
   `Path(db._db_dir) / "master.db"` (`:987-992` — `_db_dir` is the only path attr,
   `db-constraints.md:11`), calls `backup_database(db_path, discover_db_path=…)` (`:995`).
3. **Backup failure ABORTS the write** (`:996-997` → 500 "Backup failed — aborting"). ← replicate
   this exactly; never write without a successful backup.

`backup_database` (db_writer.py:15-48): copies `master.db` + `-wal`/`-shm` sidecars to
`~/.autocue/backups/master_<TS>.db` (+ optional discover sidecar), returns the dest path.

---

## (5) CLI TRIGGER — where `--write-db` hooks in

- **[EXISTS] the CLI already holds a live DB handle**: `db = MasterDatabase(args.db_path) if
  args.db_path else MasterDatabase()` (`cli.py:129`). It can call `backup_database` +
  a loop-write directly — same objects the server passes.
- **[MISSING] the CLI has NO DB-write path today** — it only emits XML (`cli.py:292,295`) or Serato
  tags (`:254`). No `rekordbox_is_running` check, no `backup_database` call anywhere in `cli.py`.
  Both must be added.
- **Arg parser hook**: alongside `--loops` (`cli.py:66-74`) → add `--write-db` (`store_true`, opt-in).
- **Dispatch hook**: `main()` is a chain of terminal branches — dry_run block (`:201-217`, already
  previews loops at `:204-215` and returns) → `if args.serato:` (`:219-277`, ends `return`) → XML
  fallthrough (`:279-297`). **Insert a third mutually-exclusive terminal branch** after the dry_run
  block, before the serato branch, ending in `return`:
  ```
  if args.write_db:
      if rekordbox_is_running(db_path):  -> print error, sys.exit(1)     # mirrors routes.py 980
      backup = backup_database(db_path, ...)  -> on failure: abort       # mirrors routes.py 995-997
      print the backup path (the user's only undo)
      for content, cues, _ in tracks:
          loops = analyze_loops(content, db)
          n += write_loops_to_db(content, loops, db, dry_run=args.dry_run)
      return
  ```
  `--write-db --dry-run` is free: the existing dry_run block (`:201-217`) already returns before any
  branch, so it previews loops and writes nothing ✅.
- **[RISK] `--write-db` should gate on `--loops`** (or imply it) — writing *cues* to the DB is a much
  bigger scope than this increment; keep it loops-only.
- **[RISK] single-writer**: `rekordbox_is_running` detects **Rekordbox**, not a running
  `autocue serve`. A CLI DB write concurrent with a server write would break the single-writer rule
  (`db-constraints.md:56-72`). Cheap mitigation: document it, or probe for the serve port.

---

## (6) TEST PRECEDENT — scratch DB, never the live library [EXISTS]

`tests/test_duplicates_integration.py` is the exact template for TDD-ing a real DB write:
- **Real in-memory SQLite with the full pyrekordbox schema**: `create_engine("sqlite:///:memory:")`
  + `t.Base.metadata.create_all(engine)` + `sessionmaker` (`:47-51`).
- **A `MagicMock` db shim** exposing `.session` and `.get_content(ID=…)` — the thin surface the
  writer expects (`:53-60`). ⚠️ our writer also calls `db.generate_unused_id(DjmdCue)`
  (`db_writer.py:530`) → the fixture must stub it (or the test will silently write `ID=<MagicMock>`).
- **Schema-pinned**: introspects pyrekordbox so a future schema change fails the test (docstring
  `:9-12`) — same trick applies to pinning the loop columns.
- Existing `tests/test_autoloops.py` never opens a real DB (monkeypatches ANLZ via `_fake_anlz`,
  `:577-591`) — keep that discipline.

**The load-bearing test (write this one FIRST — it is the whole safety case):**
seed a `DjmdContent` + **2 pre-existing Kind=0 memory cues** (`OutMsec=-1`) + 1 hot cue →
`write_loops_to_db(...)` → assert **(a) both original Kind=0 memory cues STILL EXIST, byte-identical**
← *the no-clobber assertion*; (b) new loop rows have `Kind=0, OutMsec=loop_end_ms,
OutFrame=round(end*150/1000), BeatLoopSize=loop_beats, Comment=name`; (c) re-running adds **zero**
rows (idempotent); (d) a loop colliding with an existing Kind=0 start is skipped + logged.
Add the mirror-negative: assert `write_cues_to_db(..., overwrite=True)` with memory cues DOES delete
Kind=0 — pinning *why* we don't reuse it.

---

## Tagged inventory
- **[EXISTS, reuse]** `backup_database` (db_writer.py:15-48) · `rekordbox_is_running` (:107-126) ·
  the apply-route guard/backup/abort contract (routes.py:975-997) · INSERT + savepoint pattern
  (db_writer.py:508-558) · CLI live db handle (cli.py:129) · scratch-DB integration-test fixture
  (test_duplicates_integration.py:47-60) · `CuePoint.loop_end_ms/loop_beats/is_loop` · `analyze_loops`.
- **[MISSING, build]** `write_loops_to_db()` (append-only, §3 Option B) · `--write-db` flag +
  its terminal dispatch branch · rb-guard + backup calls in `cli.py` (none exist) · a Kind=0
  reader (only needed if Option A were taken — Option B avoids it).
- **[RISK]** `write_cues_to_db(overwrite=True)` **deletes ALL Kind=0 rows** (:519-524) → memory-cue
  clobber — **do not reuse for loops** · `overwrite=False` silently drops the loop when any Kind=0
  row exists (:504) · DB-direct write has no user review step (unlike XML import) → keep opt-in +
  always print the backup path · CLI write concurrent with `autocue serve` breaks single-writer ·
  MagicMock `generate_unused_id` must be stubbed or IDs corrupt.

**Recommended safe approach: Option B — a new append-only `write_loops_to_db()` that never issues a
DELETE, skips collisions against existing Kind=0 rows, and is fronted by the same
rb-running-guard → backup → abort-on-backup-failure contract the server apply route uses.**

<!-- P0-DBWRITE-AUTOLOOPS -->
STATUS: DONE

---

# P6-v2 — 🛑 STOP. Docs-impact is MOOT. `origin/main` ALREADY SHIPPED `--loops`.

I ran `git fetch` before writing edit text (I should have done this at P0 — see LESSONS). The
result invalidates the task as scoped.

## 🚨 GATE-0 BLOCKER — we are 12 commits BEHIND and have been building a DUPLICATE

```
behind origin/main: 12    ahead: 16    merge-base: 81f4963    origin/main: 6e8b024
```
`origin/main` contains a **shipped, merged, already-bug-fixed auto-loops feature**:

| origin/main commit | What landed |
|---|---|
| `acecec3` (PR #257) | `feat(serato): export saved Rekordbox loops as Serato loop slots` |
| `9def254` (PR #260) | `feat(serato): incremental export via per-track fingerprint state` |
| `6bfe4ea` (PR #261) | **`feat(loops): generate seam-validated mix-in/mix-out loops (--loops)`** |
| `9511d6c` (PR #262) | `fix(loops): calibration bugs — CLI skip filter, unverifiable seams, stale cache` |
| `76a7a85`/`195ac57` | `docs/guides/serato.html` user guide + loops docs |

**main already has:** a `--loops` flag (`cli.py:86`), `autocue/analysis/loops.py`
(`generate_loops`, `seam_similarity` — librosa chroma/MFCC seam validation, `autocue[loops]` extra),
`db_writer.read_loops()` (`:147-176`), `db_writer.write_memory_loops()` (`:486+`),
`serato_writer.build_markers2(cues, loops=...)` **with LOOP entries already** (`:83-117`),
`tests/test_loop_generation.py`, and FEATURES.md docs.

**⇒ Our 16 commits are a parallel, colliding second implementation of the same feature.**
Merging as-is = hard conflicts in `cli.py` (two `--loops` flags), `serato_writer.py`
(incompatible `build_markers2` signatures), `db_writer.py`, and **our FEATURES.md would DELETE
main's shipped loop docs** (the −20/+4 diff on this branch removes main's
`### Loop generation (--loops)` section, the `serato.html` guide link, and the incremental-export
docs — pure regression, purely because our base is stale).

**Every docs edit from my P6 pass — and everything this P6-v2 task asked for — must NOT be
applied.** It documents a surface that conflicts with what main already ships.

---

## 🔴 THE SILVER LINING — main's shipped loop write has a LIVE DATA-LOSS BUG (the exact footgun I found at P0-inc3)

`origin/main:autocue/db_writer.py:486-518`, `write_memory_loops(..., overwrite=False)`:
```python
if not overwrite and has_existing_memory_cues(content, db) > 0:
    return 0                                    # ← silently skips the whole track
...
if overwrite:
    (db.session.query(DjmdCue)
       .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind == 0)
       .delete(synchronize_session=False))      # ← 🔴 BLANKET Kind=0 DELETE
```
Its docstring claims *"manually placed memory data is never destroyed silently"* — **but memory
CUES and memory LOOPS share the `Kind=0` space** (discriminated only by `OutMsec`;
`has_existing_memory_cues` counts BOTH, `:138-142`). So:

- **`autocue --library --loops --overwrite` DELETES every hand-placed memory CUE in the library**
  and replaces them with generated loops. Irreversible except via the backup. **This is shipped on
  `main` today.**
- Without `--overwrite`, a track with any memory cue is **silently skipped** — you can never add a
  loop to a track that has a memory cue. (The same lose-lose I documented at P0-inc3 §1.)

**Our `write_loops_to_db()` (append-only, zero DELETE, collision-skip, idempotent —
`db_writer.py:202-291`) is precisely the FIX for this bug.** That is the single highest-value thing
on this branch.

---

## Recommended RE-SCOPE (re-enter GATE-1 against the REAL gap)

**Step 0 — rebase onto `origin/main`.** Non-negotiable; everything below is against post-rebase main.

| # | Our work | Verdict vs main |
|---|---|---|
| 1 | **`write_loops_to_db()` append-only no-clobber write** | 🟢 **KEEP — reframe as a BUG FIX.** Fixes main's `Kind=0` data-loss + the can't-add-loop dead end. Ship as `fix(loops):`, replacing `write_memory_loops`'s delete path. Our `tests/test_autoloops_db_golden.py` (21 tests: `TestNoClobber`, `TestNoDeleteEverIssued`, `TestMirrorNegativeWriteCuesIsUnsafe`, `TestIdempotency`…) is the regression suite. |
| 2 | **XML loop marks** (`writer.py` `Type="loop" End=`) | 🟢 **KEEP — genuinely new.** main's `writer.py:46-48` is still `Type="cue"` only; main has **no** XML loop export. |
| 3 | **`autocue_serve_is_running()` single-writer probe** + abort-on-backup-failure | 🟢 **KEEP — new hardening.** main's loop-write guards (`cli.py:232-256`) have the rb-running check + a backup, but **no serve probe** and **no try/except around `backup_database`**. |
| 4 | Loop policy `plan_loops` (Intro/Outro/Break/Build, pow-2, cap 4) | 🟡 **PRODUCT DECISION — do not merge blind.** Competes with main's seam-validated mix-in/mix-out (max 2, librosa-verified). main's audio-seam validation is arguably *stronger*; ours has broader section coverage. Pick one, or graft Break/Build onto main's `generate_loops`. **Needs the human.** |
| 5 | Serato LOOP entries (`serato_writer`) | 🔴 **DROP — duplicate.** main ships it (`:83-117`), capped at 8 slots. Bonus: main's `_LOOP_COLOR4 = 0027AAE1` **resolves the exact byte ambiguity I flagged "probe-verify" in P1-GROUND** — question answered by shipped code. |
| 6 | `read_hot_cues` carrying `OutMsec` | 🔴 **DROP — duplicate.** main added a dedicated `read_loops()` (`:147-176`) as the shipped API. Use it. |
| 7 | `CuePoint.loop_end_ms/loop_beats/is_loop` | 🟡 **Only if 2/4 survive.** main models loops as **dicts** (`{start_ms,end_ms,name,locked}`), not CuePoint fields. Keeping our keystone means reconciling two models — justify it or adopt main's dict. |

---

## Docs-impact — the ONLY honest answer right now

**[SKIP — ALL of it, blocked]** Every target (CLAUDE.md must-know + dev-commands, FEATURES.md,
architecture.md, db-constraints.md, analysis-and-testing.md, HANDOFF.md) is **un-writable until the
re-scope lands**, because the feature being documented is not the feature that will ship.

**Do NOT apply from my earlier P6 pass** (all of it is now wrong):
- ❌ the FEATURES.md "Feature 13b auto-loops" text — **main already has a `### Loop generation
  (--loops)` section**; ours would duplicate/contradict it AND our branch currently *deletes* main's.
- ❌ the "Serato-only / XML later increment" framing — wrong twice over (XML shipped here; Serato
  shipped on main).
- ❌ the architecture-map lines naming `analyzer.plan_loops` / our `serato_writer` LOOP branch —
  those modules may not survive the re-scope (rows 4–6 above).
- ❌ the CLAUDE.md `--loops` bullet — main's `--loops` has *different* semantics (mix-in/mix-out,
  max 2, librosa) than ours.

**What SURVIVES as real docs work, post-rebase** (write it against main's feature, not ours):
- ✅ **db-constraints.md — the `Kind=0` invariant.** This is correct and urgently needed *regardless*
  of the re-scope, because it documents a footgun that is **live on main**:
  ```md
  - **`Kind=0` is SHARED by memory cues AND memory loops** — the discriminator is `OutMsec`
    (`-1` = point cue, `> InMsec` = loop region). **NEVER blanket-DELETE `Kind=0`**: a
    `.filter(Kind == 0).delete()` issued to rewrite loops also destroys the DJ's hand-placed
    memory CUES. `has_existing_memory_cues()` counts BOTH, so it cannot be used to gate a
    loop-only write. Loop writes must be **append-only** (insert non-colliding rows; never
    delete). `write_cues_to_db(overwrite=True)` and `write_memory_loops(overwrite=True)` both
    bulk-delete `Kind=0` — do not reuse them for loops.
  ```
- ✅ **CLAUDE.md must-know bullet** — same invariant, one line, pointing at db-constraints.md.
- ✅ Whatever the re-scoped increment actually ships (XML loop marks + the no-clobber fix).

**Also flag to the human:** the in-code `--loops` help text on *our* branch (`cli.py:69-73`) is
itself stale ("Currently written with --serato…") — moot if we rebase, but it shows the branch
drifted from its own docs too.

---

## Verdict
The task as briefed ("re-derive docs-impact for the full final surface") cannot be answered
honestly, because **the "final surface" collides with a feature already merged to `main`**. The
branch needs a **rebase + GATE-1 re-entry** on the real gap (rows 1–3 above, plus a human call on
row 4) before any doc is touched. Escalating rather than producing edit text that would regress
main's documentation.

<!-- P6V2-AUTOLOOPS -->
STATUS-P6V2: BLOCKED — origin/main already ships `--loops` (PR #261/#257/#262); this branch is 12 behind
and duplicates it. Docs cannot be written until the scope is re-cut. Highest-value salvage: our
append-only `write_loops_to_db` FIXES a live `Kind=0` memory-cue data-loss bug in main's
`write_memory_loops(overwrite=True)`. Needs: rebase → GATE-1 re-scope → human decision on the loop
policy (ours vs main's seam-validated).

---

# P0-COLLISION — origin/main vs feat/autoloops (READ-ONLY; nothing checked out, nothing edited)

`behind: 12 · ahead: 25 · merge-base 81f4963 · origin/main 6e8b024`

## (1) WHAT MAIN ACTUALLY DOES

### `autocue/analysis/loops.py` (main) — the generator [pure analysis, no DB write]
`generate_loops(content, db, *, cache=None, audio_check=True, stats=None) -> list[dict]`
returning **0–2** loops as dicts `{start_ms, end_ms, name, kind, bars, confidence}`.

- **Mix In Loop** — only if the track *opens* with an INTRO phrase: the **last 4/8 bars of the
  intro**, ending exactly at the next phrase (`loops.py:62-80`) so releasing the loop drops
  straight into the body.
- **Mix Out Loop** — the **first 4/8 bars of the last OUTRO phrase** (`:83-97`).
- Length rule `_bars(n)`: **8 if the phrase ≥8 bars, else 4 if ≥4, else 0** (`:57-59`).
- `bar_ms` is derived from **BPM** (`60_000/bpm*4`, `:52`) — *not* the beat grid (ours uses the
  beat-grid `avg_ms_per_beat`).
- **Named**: literally `"Mix In Loop"` / `"Mix Out Loop"` (`:76,94`).
- **"Seam-validated"** (`seam_similarity`, `:102-133`) — the quality bar. With **librosa**: load
  **1 s of audio right after the loop START** and **1 s right after the loop END**, take
  chroma(12)+MFCC(13) means, L2-normalise each, concatenate, **cosine similarity**; drop the
  candidate if `< SEAM_THRESHOLD = 0.80` (`:29`). Rationale: what plays across the wrap must
  sound like what plays at the start. If librosa is present but the seam **can't** be computed
  (missing/streaming file, loop at EOF) the candidate is **REJECTED** — *"a clicking loop is worse
  than none"* (`:141-146,171-181`). **Without librosa**: grid/phrase-only, everything accepted,
  `confidence = 0.5` (`:183-187`).
- Verdicts **cached in the sidecar CacheStore** keyed by `anlz_mtime`
  (`get_loop_verdicts`/`put_loop_verdicts`, `:152-160,189-190`); `stats` Counter for calibration.

### Where main WRITES them
- **master.db — YES.** `db_writer.write_memory_loops(content, loops, db, overwrite=, dry_run=)`
  (main `db_writer.py:481-552`) writes **memory loops: `Kind=0`, `OutMsec=end`**, `ActiveLoop=0`.
  Note it writes **`BeatLoopSize=0`** and **`ColorTableIndex=0`** (`:538,540`) — it does *not*
  record the beat length.
- **No `--write-db` flag.** The DB write is **unconditional on `--loops`** (main `cli.py:232-256`):
  rb-running guard → `backup_database` (⚠️ **not** wrapped in try/except) → `write_memory_loops`.
  **No `autocue serve` single-writer probe.**
- **Serato — MIRROR, not independent generation.** `db_writer.read_loops()` (main `:147-176`) reads
  **any** `DjmdCue` row with `OutMsec > InMsec` (hot *and* memory), sorted, **capped at 8**, and
  feeds `write_serato_tags(..., loops=loops)` → `build_markers2(cues, loops)`
  (main `serato_writer.py:83-122`). So generated loops reach Serato **only because they were
  written to the DB first**. Loop layout: `LOOP\0` + u32be len + reserved, index, start(u32be),
  end(u32be), `0xFFFFFFFF`, `_LOOP_COLOR4 = 0027AAE1`, pad, locked, name+NUL — max 8.
  `parse_markers2` **decodes** LOOP too (`:178`).
- **Rekordbox XML — NO.** main `writer.py:45-51` is still `Type="cue"` only. **No loop marks.**
- Plus **Serato incremental export**: per-track fingerprint in `autocue_serato_state.json` skips
  unchanged tracks (main `serato_writer.py:433-436`).

---

## (2) FEATURE-BY-FEATURE MATRIX

| Capability | main | ours | Tag |
|---|---|---|---|
| Loop-gen policy | seam-validated **Mix In/Mix Out**, max **2**, librosa cosine ≥0.80, sidecar-cached, BPM-derived bars | phrase-label **Intro/Outro/Break/Build**, max **4**, power-of-2, beat-grid bars, **no audio check** | **[BOTH-CONFLICT]** |
| `--loops` flag | generates + **always writes to master.db**, mirrors to Serato | generates + merges into Serato/XML; DB only via `--write-db` | **[BOTH-CONFLICT]** same name, different behaviour |
| Serato LOOP **write** | ✅ `build_markers2(cues, loops=)` | ✅ `build_markers2(cues, preserve=)` | **[BOTH-DUPLICATE]** incompatible signatures |
| Serato LOOP **decode** | ✅ (`parse_markers2:178`) | ✅ | **[BOTH-DUPLICATE]** |
| Serato LOOP **PRESERVE** (DJ's *Serato-native* loops survive a rewrite) | ❌ **NO** — full tag rewrite from RB data; `_read_existing` is used only for the fingerprint + JSONL backup. **A loop made in Serato is destroyed on the next `--serato` run.** | ✅ `preserve=` re-emits foreign raw LOOP entries; `_next_loop_index` indexes ours past them | ★ **[OURS-ONLY]** |
| Serato incremental (fingerprint state) | ✅ | ❌ | **[MAIN-ONLY]** |
| RB XML `<POSITION_MARK Type="loop">` | ❌ none | ✅ `writer.py` + CLI XML merge | ★ **[OURS-ONLY]** |
| Direct master.db loop write | ✅ `write_memory_loops` | ✅ `write_loops_to_db` | **[BOTH-DUPLICATE]** (capability) |
| **Kind=0 no-clobber DELETE** | ❌ **blanket delete ×2** | ✅ `_point_cue_filter()` | ★★★ **[OURS-ONLY]** — fixes a LIVE bug |
| **Kind=0 no-conflate COUNT** | ❌ **blanket count** | ✅ | ★★★ **[OURS-ONLY]** |
| `autocue serve` single-writer probe | ❌ | ✅ by process + port | ★ **[OURS-ONLY]** |
| backup abort-on-failure / non-zero exit on partial write | ⚠️ backup taken, no try/except | ✅ | **[OURS-ONLY]** (minor) |
| `BeatLoopSize` recorded | ❌ writes `0` | ✅ `loop_beats` (bars×4) | **[OURS-ONLY]** (small, real) |
| Read loops from DB | `read_loops()` → dicts (hot+mem, cap 8) | `read_hot_cues` → CuePoint w/ `loop_end_ms` | **[BOTH-DUPLICATE]** different models |
| `CuePoint` loop fields | ❌ (loops are dicts) | ✅ `loop_end_ms/loop_beats/is_loop` | **[OURS-ONLY]** (only our stack needs it) |
| Sidecar cache of loop verdicts | ✅ | ❌ | **[MAIN-ONLY]** |
| **Audio seam validation** | ✅ librosa | ❌ | **[MAIN-ONLY]** — genuinely better than ours |

---

## (3) ★ THE Kind=0 DATA-LOSS BUGS — **YES, STILL LIVE ON main, and shipping loops made them WORSE**

Three blanket-`Kind=0` sites on `origin/main:autocue/db_writer.py`:

| # | Site | Code |
|---|---|---|
| A | `has_existing_memory_cues` **:138-144** | `.filter(ContentID==…, Kind == 0).count()` ← **conflates memory CUES + memory LOOPS** |
| B | `write_cues_to_db` **:604-608** | `if write_memory:` → `.filter(ContentID==…, Kind == 0).delete()` ← **blanket DELETE** |
| C | `write_memory_loops` **:513-518** | `if overwrite:` → `.filter(ContentID==…, Kind == 0).delete()` ← **blanket DELETE** |

Memory cues and memory loops **share the `Kind=0` space**; the only discriminator is `OutMsec`
(`-1` = point cue, `> InMsec` = loop). main now ships **two producers into that shared space** —
`write_cues_to_db` (memory CUES, via `/api/apply`, `generate-apply`, `memory_cue_mode`) and
`write_memory_loops` (memory LOOPS, via `--loops`). **They destroy each other:**

- 🔴 **Bug A — loops eat cues.** `autocue --library --loops --overwrite` → (C) deletes **every**
  `Kind=0` row → the DJ's **hand-placed memory CUES are destroyed**, replaced by ≤2 loops.
  Library-wide. Recoverable only from the backup.
- 🔴 **Bug B — cues eat loops (NEW; *caused* by shipping loops).** Apply from the web UI / CLI with
  `memory_cue_mode != none` + overwrite → (B) deletes **every** `Kind=0` row → **main's own
  freshly-generated loops are silently destroyed.** Generate loops, then apply cues → loops gone.
  Nothing in main protects them.
- 🟠 **Bug C — silent suppression, both directions** (from the conflated COUNT at A):
  - track has only LOOPS → `has_existing_memory_cues > 0` → `write_memory=False` → the user's
    **memory cue is silently not written** (default path, no overwrite).
  - track has only memory CUES → `write_memory_loops` returns 0 → the **loop is silently skipped**
    (`"has memory cues/loops — skipped"`, main `cli.py:255`).

**Our fix (already built + reviewed on HEAD):** `_point_cue_filter()` (`db_writer.py:138-152`,
`or_(OutMsec IS NULL, OutMsec <= InMsec)`) shared by the memory-cue COUNT (`:167-168`) **and** the
memory-cue DELETE (`:747-748`), so a cue-rewrite spares loops and a loop-write spares cues; plus
`write_loops_to_db` (`:202-291`) which is **append-only — no `overwrite`, no DELETE at all**.
Commits `6cf2d8f` + `6acc573` (both auditor-rated IMPORTANT 88), golden suite
`tests/test_autoloops_db_golden.py` (21 tests: `TestNoClobber`, `TestNoDeleteEverIssued`,
`TestMirrorNegativeWriteCuesIsUnsafe`, `TestIdempotency`, …).

---

## (4) SALVAGE VERDICT — brutally honest

**~65–70% of our 25 commits is redundant or superseded.** Our loop *generator*, Serato LOOP
write/decode, DB-loop-write capability, and CuePoint loop model all duplicate code already shipped
on main — **and main's generator is better than ours** (real audio seam validation + verdict
caching; we have no audio check at all). We spent the branch rebuilding a feature that existed.

### KEEP — ranked by value (each = a small, standalone PR onto main)
1. **★★★ The `Kind=0` no-clobber fixes** (`_point_cue_filter` + the scoped COUNT/DELETE +
   append-only loop write). **Fixes a live cross-feature data-loss bug that main's own loop feature
   both causes and suffers from.** Zero design conflict — it makes *main's* feature correct. Ship
   first, standalone, with the golden tests. **This alone justifies the branch.**
2. **★★ Serato LOOP preserve.** main destroys a DJ's Serato-native loops on every `--serato` run.
   Re-implement our `preserve=` against main's `build_markers2(cues, loops=)` signature.
3. **★★ Rekordbox XML loop marks.** main has none; clean additive. Port `writer.py` + the CLI XML
   merge onto main's `generate_loops` dicts.
4. **★ Safety hardening for the DB write:** `autocue_serve_is_running()` single-writer probe,
   backup abort-on-failure, non-zero exit on partial write. main lacks all three.
5. **★ `BeatLoopSize = bars × 4`** — main writes `0`. One-liner.

### DROP — pure duplicate / superseded
- `plan_loops`/`analyze_loops` policy — superseded by main's seam-validated generator. *(If the
  human wants Break/Build sections, graft those **categories** onto main's `_phrase_candidates`
  and keep main's seam validation — don't bring our generator.)*
- Our `build_markers2` LOOP writer, `read_hot_cues` OutMsec (main has `read_loops`),
  `CuePoint.loop_end_ms/loop_beats/is_loop` (main models loops as dicts), the `--write-db` flag
  (main writes on `--loops` already — our append-only *writer* survives as a fix to
  `write_memory_loops`; the flag does not).

### CONFLICTS (why a rebase is the wrong move)
Both branches rewrote **the same three files' loop surfaces**: `cli.py` (two `--loops` flows),
`db_writer.py` (two loop writers + the memory helpers), `serato_writer.py` (two incompatible
`build_markers2` signatures) — plus our stale `FEATURES.md` **deletes main's shipped loop docs**.
A rebase yields hairy conflicts *and* leaves two competing loop generators in one CLI.

### RECOMMENDATION
**Abandon `feat/autoloops` as a merge candidate. Cherry-pick 4–5 surgical PRs onto a fresh branch
off `origin/main`,** starting with the `Kind=0` data-loss fix (which should go out on its own,
fast — it is live on main today). Do **not** rebase; do **not** try to land the generator.

<!-- P0-COLLISION-AUTOLOOPS -->
<<<<<<< Updated upstream
=======
STATUS: DONE

---

# P0-GROUND PR#3 — SERATO LOOP PRESERVE, ported onto MAIN (base 6e8b024). READ-ONLY.

## (1) MAIN's `serato_writer.py` — the flow, end-to-end

| Element | main file:line | Behaviour |
|---|---|---|
| `build_markers2(cues, loops=None)` | **:83-122** | Positional 2nd arg. Emits CUE entries from `cues` (slot 0-7), then LOOP entries from `loops` dicts: `for index, loop in enumerate((loops or [])[:8])` — **index = enumerate position, hard cap 8** (`:107`). |
| LOOP byte layout | **:108-120** | `b"LOOP\x00"` + u32be len + data; data = `\x00`(0x00) · index(0x01) · **start u32be ms**(0x02) · **end u32be ms**(0x06) · `\xff\xff\xff\xff`(0x0a) · `_LOOP_COLOR4 = 0027AAE1`(0x0e) · pad `\x00`(0x12) · **locked**(0x13) · name+NUL(0x14). Fixed = 20 B. *(Matches the P1-GROUND spec exactly — main resolved the bytes I'd flagged probe-verify.)* |
| `parse_markers2` | **:148-190** | **YES — already DECODES LOOP at `:178`** (`elif etype == "LOOP" and length >= 21`), extracting `index/start_ms/end_ms/name`. ⚠️ **It does NOT return the raw framed bytes**, and drops `locked`/color. |
| `_read_existing(path)` | **:192-220** | `{tag_name: raw_bytes}` for GEOB_V2/GEOB_V1/FLAC_V2/MP4_V2/… — the raw outer tag. |
| `write_serato_tags(path, cues, comment=None, loops=None)` | **:281-332** | `payload = build_markers2(cues, loops)` (**:291**) → `wrap_outer` → **full tag REPLACEMENT** (`id3.setall("GEOB:Serato Markers2", …)` `:301-306`; FLAC `:315`; MP4 `:325`). Legacy `Markers_` deleted every write (`:300`). |
| `fingerprint(cues, loops, comment)` | **:337-359** | sha1 of DB-sourced cues + loops + comment. **Foreign/file loops are NOT part of it.** |
| incremental state | **:361-370, :436-438, :463** | `autocue_serato_state.json`; skip when `not overwrite and existing and state[track_id] == fp` → `unchanged`, **no write at all**. |
| CLI feed | main `cli.py:268-277` | `read_hot_cues(content, db)` + **`read_loops(content, db)`** (DB loops, hot+memory, `OutMsec > InMsec`, **cap 8**) → 3-tuples `(content, cues, loops)`; `write_serato` unpacks `item[2]` (`:419`). |

## (2) ★ THE BUG — exactly where the DJ's Serato-native loop dies

`write_serato` reads the old tag at **`:435`** (`existing = _read_existing(path)`) and uses it for **only two things**: the fingerprint skip (**:436-438**) and the JSONL backup (**:442-449**). It is **never parsed for LOOP entries and never fed back into the payload**.

**Drop point: main `serato_writer.py:453-454` → `:291`.**
`write_serato_tags(path, cues, comment=…, loops=loops)` → `payload = build_markers2(cues, loops)` — the payload is rebuilt **exclusively** from `cues` (`read_hot_cues`) + `loops` (`read_loops` = **the Rekordbox DB**). The new payload then **replaces** the whole GEOB (`:301-306`). Any LOOP entry that exists in the file but **not** in the DB is simply never re-emitted → **gone**.

**Is it recoverable?** ⚠️ **Correction to my P0-COLLISION wording ("destroyed / silently lost") — be precise:** the prior raw tag payload **IS** appended (base64) to `autocue_serato_backup.jsonl` at **`:442-449`** before every rewrite. So it is recoverable *in principle*, but only by **hand-restoring the entire previous Markers2 tag** — which also reverts AutoCue's cues. There is **no per-loop recovery and no automated restore path**. Practically: the DJ's Serato-native loop is silently dropped from the file, with a hand-only, all-or-nothing escape hatch. **Still a real data-loss bug — just not an unrecoverable one.**

## (3) DESIGN — the minimal port onto main

### 🔑 The load-bearing insight: **dedup is MANDATORY, not politeness**
A naive "preserve every file LOOP entry" **double-counts AutoCue's own loops**: run 1 writes DB loop *X* into the file; run 2 (any rewrite) would preserve the file's *X* **and** re-emit *X* from `read_loops()` → *X* twice, growing every rewrite until the 8-cap. So the preserve set must be **FOREIGN loops only**.

**Discriminator: `start_ms`.** `read_loops` sources `start_ms` from `DjmdCue.InMsec` (int) and `build_markers2` writes/parses it as an exact u32be round-trip — so **exact equality is safe, no tolerance needed**.
- file loop whose `start_ms` **matches a DB loop** → *ours / RB knows it* → **do NOT preserve**; regenerate from `loops` (**DB stays authoritative**, so a re-tuned loop end actually updates).
- file loop with **no matching DB loop** → **foreign (DJ made it in Serato)** → **preserve raw bytes verbatim** (keeps its name, locked flag, colour).

*(This is strictly better than our archived approach, which dropped the **generated** loop on a start collision — that let a stale file loop shadow an updated DB loop forever.)*

### 🐛 Bug found in OUR archived code — do NOT port it as-is
`_next_loop_index()` (`667f244:117-128`) returns `max(preserved_index)+1`, and `_loop_entry` writes `bytes([index & 0xFF])`. **If a preserved DJ loop sits at index 7, a generated loop gets index 8 — outside Serato's 0-7 slot range.** Replace with **lowest-free-slot** assignment:
```python
used = {raw[10] for raw in preserve if raw[:5] == b"LOOP\x00" and len(raw) >= 11}
free = [i for i in range(8) if i not in used]          # handles non-contiguous DJ indices
for slot, loop in zip(free, generated):  emit(slot, loop)
dropped = generated[len(free):]                        # log — never silently truncate
```
This makes the **8-slot cap** fall out naturally and enforces **DJ's loops win** (preserved keep their original slots; generated fill what's left; surplus generated are dropped **with a breadcrumb**).

### Exact diff (4 touch points, ~15 lines; no signature break)
1. **`parse_markers2`** (main **:170-171**) — capture the framed bytes:
   `raw = payload[i:end + 5 + length]` → `entry: dict = {"type": etype, "raw": raw}`. *(+2 lines; port of `667f244:217-220`.)*
2. **NEW `_existing_loop_entries(path) -> list[tuple[int, bytes]]`** — port `667f244:267-296` verbatim: `_read_existing` → decode GEOB_V2/FLAC_V2/MP4_V2 → return `(start_ms, raw)` for `type == "LOOP"`. Best-effort `[]` on any failure; **warn** when a v2 tag is present but decodes to nothing (a rewrite would then silently drop loops).
3. **`build_markers2(cues, loops=None, *, preserve=())`** (main **:83**, loop block **:107-120**) — add the **keyword-only** `preserve` (backward-compatible: every existing caller/test keeps working). Replace `enumerate((loops or [])[:8])` with the lowest-free-slot loop above; append `preserve` raw entries **verbatim** before the `b"\x00"` terminator.
4. **`write_serato_tags`** (main **:281-291**) — resolve preserve from the file, foreign-only:
```python
existing_loops = _existing_loop_entries(path)              # [(start_ms, raw)]
db_starts = {int(l["start_ms"]) for l in (loops or [])}
preserve = [raw for start, raw in existing_loops if start not in db_starts]   # FOREIGN only
payload = build_markers2(cues, loops, preserve=preserve)
```
5. **`fingerprint` — UNCHANGED.** 6. **`write_serato` — UNCHANGED** (preserve is resolved inside `write_serato_tags`, so the orchestrator needs no edit at all).

## (4) RISKS

- **[RISK — CRITICAL, solved by the dedup] Double-count.** `read_loops()` returns DB loops that AutoCue **already wrote into the file** on a previous run. Preserve-everything ⇒ every AutoCue loop duplicated per rewrite. **The foreign-only filter (§3) is mandatory**; without it the port is worse than the bug.
- **[RISK — 8-slot cap] preserved + generated > 8.** Serato has 8 loop slots. **Recommend the DJ's loops win**: preserved keep their slots, generated take the free ones, surplus generated are **dropped with a log line** (never silent).
- **[RISK — our archived code] index overflow past 7** (`_next_loop_index`) — see §3; use lowest-free-slot instead.
- **[RISK — fingerprint, benign] preserved loops aren't in the fingerprint.** Safe, and **deliberately leave it that way**: the skip path performs **no write**, so foreign loops can't be harmed while skipped; and *including* them would flip the fingerprint every time the DJ edits a loop in Serato, forcing a pointless rewrite (mtime churn) that just re-emits what's already there. Only a *rewrite* (RB-side change) needs preserve — which it now has.
- **[RISK — accepted, minor] coincidental start collision.** A DJ loop at the exact same `start_ms` as a RB loop is treated as "ours" → its name/locked flag are overwritten. Rare; the loop stays at the same position. Acceptable.
- **[RISK — legacy tag] `Markers_` is deleted on every write** (main `:300`) **by design** — only Markers2 (v2) loops are preserved. Unchanged behaviour; call it out in the docstring.
- **[EXISTS, reuse]** LOOP decode (`parse_markers2:178`), `_read_existing`, the JSONL backup, the LOOP byte layout + `_LOOP_COLOR4`, the incremental state machinery.
- **[MISSING, build]** raw-byte capture in `parse_markers2` · `_existing_loop_entries` · the `preserve=` param + free-slot indexing · the foreign-only dedup in `write_serato_tags`.

**Tests to add:** foreign loop survives a rewrite (round-trip, byte-identical) · **no double-count** (export twice → still one loop) · DB loop end re-tuned → file updates (not shadowed) · preserved+generated > 8 → DJ's kept, generated dropped **and logged** · preserved loop at index 7 → generated never emits index 8 · non-contiguous preserved indices · undecodable v2 tag → warns, returns `[]`, write still succeeds.

<!-- P0-PR3-SERATO -->
>>>>>>> Stashed changes
STATUS: DONE

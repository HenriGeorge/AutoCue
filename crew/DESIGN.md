# DESIGN — AUTOLOOPS (auto-place named, bar-aligned loops) · Serato-first → Rekordbox XML

> Prior designs (workbench reconcile → PR #245; Review Dock → feat/review-dock) are preserved in
> git history of this file. This file now holds the CURRENT work: **autoloops**.
> Status: **GATE-1 SIGNED OFF by human 2026-07-11** (decisions in §0). Build may proceed.

Single convergence source of truth (GATE-2 parity). Grounded by `crew/researcher.md`
(P0-PREMISE + P1-GROUND, source-cited). Scope **LOCKED by the human**: Serato loops FIRST, then
Rekordbox XML. Direct `master.db` loop write and any web-UI loop surface are **explicitly out of
scope** for this branch (later increments).

## Feature (one sentence)
After the existing phrase-based auto-cue pass, **also emit named, bar-length loop regions at
phrase edges** (e.g. an 8-bar **"Outro"** loop for mix-out, a 4-bar **"Break"** loop) and write
them as **Serato Markers2 LOOP entries** (increment 1) and **Rekordbox XML `<POSITION_MARK
Type="loop">`** (increment 2) — **mirror-first**: never clobber a loop the DJ already made.

---

## 0. DECISIONS — signed off by human 2026-07-11 (GATE-1)

1. **Rekordbox loop placement = memory loops (`Kind=0`)** ✅ — no conflict with the auto-cue hot
   slots A–H. Serato loops have their own loop-slot index (separate from Serato cues) → unaffected.
2. **Loop policy = the grilled policy in §2** ✅ — restrict to INTRO/OUTRO/Break/Build, power-of-2
   bar lengths clamped to the phrase, phrase ≥ 4 bars, prioritize Intro+Outro.
3. **Serato LOOP byte-lock = option (b)** ✅ — build to the reference `LoopEntry` struct now
   (start/end/name/locked/index HIGH-confidence; color = a fixed Serato loop color, reserved =
   `0xFFFFFFFF`), prove a byte-for-byte round-trip in tests, then the **user opens a written file in
   Serato DJ Pro at GATE-2** to confirm loops render + are named; a one-pass byte fix if off.
4. **Opt-in = new `--loops` CLI flag** ✅ — loops generate only when asked (like `--serato`); Serato
   write still requires `--serato`, XML via the existing XML path.

---

## 1. Keystone — `CuePoint` gains a loop end (models.py)
- Add optional loop fields to `CuePoint` (`models.py:88-109`): `loop_end_ms: int | None = None` and
  `loop_beats: int | None = None` (bar length × 4). `is_loop` ⇔ `loop_end_ms is not None`.
- Everything downstream (writer / serato_writer / schemas) consumes `CuePoint`, so this is the one
  shared add that unlocks both surfaces. Non-loop cues keep `loop_end_ms=None` → behave exactly as
  today (regression-safe).

## 2. Loop-generation policy (GRILLED) — "the two loops every DJ wants, plus two creative"
Runs **only when `--loops`** is set, **layered on the existing phrase cue output** (reuses
`phrase_bars` + beat-grid `bar_ms` — seed math already exists, P0 §3). **No new analysis** — reuses
the phrase labels AutoCue already computes from the Rekordbox analysis.

**Which phrases get a loop (by `PhraseLabel`):**

| Phrase label | Loop? | Name | Length = largest power-of-2 bars ≤ phrase | Rationale |
|---|---|---|---|---|
| **INTRO** | ✅ default | "Intro" | 16 → 8 → 4 | mix-IN time (bread-and-butter) |
| **OUTRO** | ✅ default | "Outro" | 16 → 8 → 4 | mix-OUT time (bread-and-butter) |
| **DOWN (Break)** | ✅ default | "Break" | 8 → 4 | creative tension / atmos |
| **UP (Build)** | ⚙️ optional flag | "Build" | 8 → 4 | extend the build |
| VERSE / CHORUS(Drop) / BRIDGE | ❌ never | — | — | full arrangement + vocals loop badly (G1) |

**Rules (from the grill):**
- **Start = the phrase downbeat** (already the cue position, beat-grid-aligned) → seamless loop.
- **Length = largest power-of-2 bars that fits** (`16/8/4`, or `8/4` for Break/Build), **clamped to
  `phrase_bars`** — never a 6-bar loop (G2), never overruns the next phrase.
- **Require `phrase_bars ≥ 4`** — shorter phrases are skipped (too short to loop usefully).
- **Cap ~3–4 loops/track**, **one per section** (no stacking adjacent phrases); priority order
  Intro → Outro → Break → Build (G3).
- **BPM guard** reused (`float(bpm)>0`); **no beat grid ⇒ skip** the loop (no bar alignment).
- **Clamp the loop end before track end** (a last-outro loop must not run into silence).
- **Naming** from `DJ_NAMES` (Intro/Outro/Break/Build), disambiguated like cues ("Break 1/2").
- Loops ride **inside the same `cues` list** the writers already consume (P1-GROUND §4) as
  loop-flagged `CuePoint`s — no parallel plumbing.
- **Future (NOT this cut):** use AutoCue's energy analysis to pick the *most-mixable* break when
  several qualify. Left out to keep the first cut quick & easy.

## 3. Increment 1 — Serato LOOP tags (`autocue/serato_writer.py`)
- **`build_markers2`** (`serato_writer.py:79-98`): branch CUE-vs-LOOP on `cue.is_loop`. LOOP entry
  framing `b"LOOP\x00" + uint32be(len) + data`, `data` per the grounded layout
  (`crew/researcher.md` §1): index (loop number), **start** uint32 BE ms, **end** uint32 BE ms,
  reserved/color/**locked** (option (b): color = fixed Serato loop color, reserved = `0xFFFFFFFF`,
  locked = 0), name UTF-8 + NUL. Positions are ms/uint32 BE — identical units/endianness to the CUE
  path we already ship.
- **`parse_markers2`** (`serato_writer.py:124-156`): decode `etype=="LOOP"` too. **Critical
  (data-loss guard, F1):** today the parser drops LOOP, so a Markers2 rewrite would **wipe the DJ's
  existing Serato loops** — decoding LOOP lets us **preserve** them (mirror-first). Assert a
  byte-for-byte round-trip in tests.
- The two Serato quirks confirmed to apply (P1-GROUND §1): legacy `Markers_` deletion and no `=`
  base64 padding — both operate on the whole payload, so they cover LOOP entries unchanged.

## 4. Mirror-first read-back — `read_hot_cues` carries `OutMsec` (`db_writer.py:170-178`)
- Today `read_hot_cues` reads `InMsec` only and **drops `OutMsec`** → existing Rekordbox loops read
  back as point cues (loop lost). Carry `OutMsec`/`BeatLoopSize` so the mirror source
  (`existing or generated`, `cli.py:186`) surfaces an existing loop as a `CuePoint` with
  `loop_end_ms` → it exports as a loop (Serato + XML), not a degraded point cue.
- `has_existing_hot_cues` already counts loops (Kind>0) → overwrite/skip semantics unchanged.

## 5. Increment 2 — Rekordbox XML (`autocue/writer.py:46-51`)
- `add_mark` already supports it: pass `Type="loop", End=<end_sec>` when `cue.is_loop` (else the
  current `Type="cue"` with no `End`). `Name` already passed (`cue.name or cue.label.value`).
- Named loops export → import into Rekordbox as named memory loops → sync to CDJs. (Answers the
  user's parity question: YES, verified in `crew/researcher.md` §2.)

## 6. Non-goals (explicitly OUT of this branch)
- Direct `master.db` loop write (`write_cues_to_db` 6-line change) — later increment.
- Any web-UI / serve-API loop surface — later (UI has zero loop concept today).
- Active-loop arming (`ActiveLoop=1`) — we write *saved* loops, not an armed loop.
- Energy-ranked break selection — future refinement.

---

## Pressure-test (grill-me) — flaws surfaced & mitigations
- **F1 · Serato loop write wipes existing loops (data loss).** `parse_markers2` drops LOOP →
  rewrite loses the DJ's saved loops. → **Mitigation §3:** decode+preserve LOOP entries; round-trip
  test. (Highest-severity; must land WITH the writer, not after.)
- **F2 · 8 ambiguous reserved/color bytes corrupt the entry.** → **Decision 3(b):** build to
  reference struct with safe defaults + byte-for-byte round-trip test; user verifies in Serato at
  GATE-2, one-pass fix if off. Do NOT claim done on Serato until the user confirms in Serato.
- **G1/F3 · Looping the wrong section (verse/drop w/ vocals) or off-grid.** → §2 restricts to
  INTRO/OUTRO/Break/Build; start = phrase downbeat; clamp to `phrase_bars`; skip when the beat grid
  is unparseable (ANLZ fragility, reuse the existing try/except ladder).
- **G2 · Non-power-of-2 loop length sounds wrong.** → §2 rounds DOWN to the largest power-of-2 bars
  that fits; require `phrase_bars ≥ 4`.
- **F4 · Hot loop clobbers a hot cue** (Rekordbox slot contention). → **Decision 1: memory loops
  (Kind=0)** sidestep it entirely.
- **F5 · BPM=0 / no beat grid → /0 or garbage length.** → reuse the `float(bpm)>0` guard; no grid
  ⇒ no loop.
- **F6 · Mirror-first without OutMsec read-back double-writes or degrades a real loop.** →
  **§4** carries OutMsec before any loop write.
- **F7 · Green tests ≠ Serato accepts it.** Automated proof is a self-consistent round-trip (our
  writer ↔ our parser). **True proof requires Serato DJ Pro** (user-verify at GATE-2). Acceptance
  states this honestly.

---

## Branch
Build on **`feat/autoloops`** (current worktree HEAD == origin/main, clean). Per-increment commits:
(1) CuePoint keystone + policy, (2) Serato LOOP write+decode, (3) read_hot_cues OutMsec,
(4) RB XML loop. PR at finish.

## VERIFY / GATE-2 acceptance
- **STATIC:** `pytest` (+ new loop tests: policy incl. power-of-2/phrase-clamp/label-restrict,
  Serato LOOP encode **byte-for-byte round-trip**, `parse_markers2` LOOP decode/preserve,
  `read_hot_cues` OutMsec, `writer.py` XML loop mark, BPM/grid guards). `npm test` unaffected (no
  web change). Green, counts grow.
- **BEHAVIORAL:** golden-file test — generate loops for a fixture track, assert the Serato Markers2
  bytes and the XML `<POSITION_MARK Type="loop" Start Name End>` match expected; assert an existing
  loop **survives** a rewrite (F1).
- **EXERCISE (real artifact, by profile = CLI):** run `autocue --track … --loops --serato --dry-run`
  and a real write to a **throwaway copy**; read the tag back with our extended parser. **Final
  proof (Decision 3b):** hand the written file to the **user to open in Serato DJ Pro** → named
  loops render at the right positions. For XML: user imports the generated XML into Rekordbox to
  confirm named loops appear. **SHOW the evidence** (hex-diff / CLI stdout / the user's screenshot).
- **SILENT-FAILURE lens:** the ANLZ/PQTZ try/except must not swallow a real parse error into "no
  loop" silently — log a breadcrumb.

## Coverage map → crew/test-designer.md · build log → crew/implementer.md · e2e/golden → crew/test-verifier.md

---

## P2 refinements — resolutions to test-designer open questions (2026-07-11)

The P2 coverage map (`crew/test-designer.md`) surfaced NC-3/NC-4/NC-8. Resolutions (within the
GATE-1-approved design):

- **R-NC8 · Cap + Build (coordinator decision).** **Cap = 4 loops/track.** Build (UP) is **eligible
  by default at lowest priority** — the separate opt-flag is **dropped** (simpler; the cap +
  priority Intro>Outro>Break>Build naturally limits it). So policy §2 "⚙️ optional flag" for Build
  → **✅ default (lowest priority)**. `--loops` remains the single opt-in.
- **R-NC3 · Serato encode golden (coordinator decision, per option-b).** The composed encode golden
  is asserted **deterministically** on AutoCue's own output (a valid REGRESSION anchor of what we
  emit) — NOT `xfail`. BUT it must be **clearly labelled** "AutoCue's encoding; Serato-acceptance of
  the reserved/color bytes `0x0a–0x12` is proven ONLY by the GATE-2 user Serato-verify (F2/F7)."
  High-confidence fields (framing/start/end/name/locked/index) are hard assertions; the F1
  survival test is a parse→rebuild round-trip (needs no frozen hex). No green golden may be read as
  "Serato works" — that claim requires the user's Serato screenshot.
- **R-NC4 · `--loops` × mirror-first (NEEDS USER THUMBS-UP; default = LAYER).** Today
  `cli.py` uses `existing or generated`, so a track with ANY existing hot cue drops generated loops
  wholesale. That contradicts the feature intent ("*after* autocues, *also* set loops"). **Default
  decision: LAYER** — mirror the DJ's existing cues **and loops**, AND add generated loops **only in
  sections that don't already have a loop** (never overwrite an existing loop; mirror-first still
  wins per-loop). i.e. `export = existing_cues+loops  +  generated_loops_in_loopless_sections`.
  *(Alternative = strict mirror: a track with existing cues gets no generated loops. Rejected as it
  defeats the feature for already-cued libraries.)* → flagged to human for a quick confirm; build of
  the encode/decode/policy-selection units is unaffected and proceeds.

---

# INCREMENT 3 — DB-DIRECT loop write (`--write-db`) · approved by human 2026-07-11

Un-defers the §6 non-goal at the user's request (they don't use XML import; they apply via the DB).
Grounded by `crew/researcher.md` P0-DBWRITE (source-cited). **This is the only AutoCue CLI path that
MUTATES the real Rekordbox DB** — safety is the design.

## 🚨 The trap we are designing AROUND (researcher headline)
`write_cues_to_db` is **UNSAFE for loops on BOTH branches** — DO NOT REUSE IT:
- `overwrite=True` → **DELETES EVERY `Kind=0` row** for the track (`db_writer.py:519-524`) — the DJ's
  hand-placed **memory cues** AND existing memory loops. **This is the clobber.**
- `overwrite=False` + any existing `Kind=0` row → `write_memory=False` → the loop is **silently not
  written** (silent no-op).
- `overwrite=False` + any hot cue → whole track returns 0 (`:484-486`).
Memory **cues** and memory **loops** share the `Kind=0` space; the discriminator is `OutMsec`
(`-1` = point cue, `> InMsec` = loop). ⇒ **A loop write must NEVER DELETE `Kind=0`.**

## 1. NEW `write_loops_to_db()` — append-only (db_writer.py) · Option B
**No DELETE anywhere ⇒ clobber is impossible by construction.**
```
write_loops_to_db(content, cues, db, *, dry_run=False) -> int
  1. loops = [c for c in cues if c.is_loop and c.slot == -1]     # memory loops only
  2. existing = {InMsec of every Kind=0 row for this ContentID}  # one cheap query
     → SKIP any loop whose start collides (mirror-first; DJ wins) ⇒ also IDEMPOTENT on re-run
  3. INSERT only surviving loops (§2 columns); ID=db.generate_unused_id(DjmdCue), UUID=uuid4()
  4. begin_nested() savepoint → sp.commit() → db.session.commit(); on error rollback+log+RAISE
  5. logger.info breadcrumb per skipped-for-collision loop (silent-failure lens)
```
- `write_cues_to_db` is **left untouched** (shared server path for /api/apply, /api/generate-apply,
  SSE — backs `memory_cue_mode`; editing it risks regressing the server).
- **Collision tolerance = EXACT `InMsec` match** (conservative; matches `cli._merge_loops`).

## 2. Loop columns + UNITS (confirmed vs read side + plan_loops)
`OutMsec=cue.loop_end_ms` (ms) · `OutFrame=round(loop_end_ms*150/1000)` (150 sub-frames/s, mirrors
`InFrame`) · `OutMpegFrame=OutMpegAbs=0` · **`ActiveLoop=0`** (saved-but-UNARMED) ·
`BeatLoopSize=cue.loop_beats` (**BEATS** = bars×4, confirmed) · `Kind=0` (slot=-1 → memory) ·
`Comment=cue.name` (the loop name).

## 3. Safety wiring — copy the apply-route contract verbatim (routes.py:975-997)
1. **`rekordbox_is_running(db_path)` → abort** (Rekordbox open = SQLCipher lock).
2. **`backup_database(db_path, …)` BEFORE any write** (→ `~/.autocue/backups/master_<TS>.db`).
3. **Backup failure ABORTS the write** (never write without a successful backup).
4. **PRINT the backup path** — it is the user's only undo.
5. **Single-writer probe (NEW, #RISK):** `rekordbox_is_running` does NOT detect a running
   `autocue serve`, which holds a read-write DB handle. Probe for a local serve (port/lock) and
   **refuse to write** if one is up (violates the single-writer rule, db-constraints.md:56-72).

## 4. CLI trigger — `--write-db` (opt-in)
- New `store_true` flag beside `--loops`; **gates on `--loops`** (loops-only scope — writing *cues*
  to the DB is a much larger scope, explicitly NOT in this increment).
- A THIRD mutually-exclusive terminal branch after the dry-run block, before `--serato`.
- `--write-db --dry-run` is free (the dry-run block already returns first and previews loops).

## 5. GATE-2 acceptance (the safety case IS the test)
- **THE LOAD-BEARING TEST (write FIRST):** scratch in-memory SQLite w/ the real pyrekordbox schema
  (`tests/test_duplicates_integration.py:47-60` pattern; must stub `db.generate_unused_id`). Seed a
  DjmdContent + **2 pre-existing Kind=0 memory cues** + 1 hot cue → `write_loops_to_db(...)` → assert:
  **(a) both original memory cues STILL EXIST, byte-identical** ← *the no-clobber assertion*;
  (b) new loop rows carry Kind=0/OutMsec/OutFrame/BeatLoopSize/Comment per §2; (c) **re-run adds ZERO
  rows** (idempotent); (d) a loop colliding with an existing Kind=0 start is skipped + logged.
- **Mirror-negative:** assert `write_cues_to_db(..., overwrite=True)` DOES delete Kind=0 — pinning
  *why* we don't reuse it.
- **NEVER test against the live master.db.** Real-DB verification = a **COPY** of master.db, one
  track (the user's "test with one"), then the user runs it on their live DB (auto-backup first).

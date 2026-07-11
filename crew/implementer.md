# IMPLEMENTER — P3 BUILD · AUTOLOOPS INCREMENT 1 (Serato-first, TDD)

Branch `feat/autoloops`. Built **increment 1 only** (Serato LOOP tags + mirror read-back +
`--loops` flag). The RB XML loop mark (`writer.py`) is **increment 2 — NOT built** (held for the
rolling checkpoint per the task). TDD throughout: failing unit test → minimal code → green →
per-logical-unit commit. My TDD file is `tests/test_autoloops.py` (40 tests); the verifier owns a
DISJOINT golden/round-trip file (#99 — I created NO golden/behavioural file).

## Commits (per logical unit)
| # | Commit | Unit | Files |
|---|--------|------|-------|
| 1 | `784cce4` | Keystone: `CuePoint.loop_end_ms/loop_beats` + `is_loop` | models.py, test_models.py, test_autoloops.py |
| 2 | `e075bce` | Loop policy `plan_loops()` (§2 GRILLED) + `analyze_loops()` wrapper | analyzer.py, test_autoloops.py |
| 3 | `b2c2fc7` | Serato LOOP write + decode + preserve (§3, F1) | serato_writer.py, test_autoloops.py |
| 4 | `e66b6d1` | `read_hot_cues` carries `OutMsec`→`loop_end_ms` (§4, F6) | db_writer.py, test_autoloops.py |
| 5 | `eca754c` | Wire `--loops` CLI flag into the Serato path (§5) | cli.py, test_autoloops.py |

## What changed (files touched — 5 source + 2 test)
- **`autocue/models.py`** — `CuePoint` gains `loop_end_ms: int|None=None`, `loop_beats: int|None=None`,
  `is_loop` property (⇔ `loop_end_ms is not None`). Non-loop cues default to `None` → **behave
  exactly as today** (regression-safe; the CUE serialization path is untouched).
- **`autocue/analyzer.py`** — `plan_loops(phrases, bar_ms, *, total_ms=None, include_build=False)`:
  the **pure §2 policy** (no DB), fully unit-testable. Restricts to INTRO/OUTRO/DOWN(Break) +
  optional UP(Build); length = largest power-of-2 bars that fits (cap 16 Intro/Outro, 8 Break/Build);
  requires `phrase_bars≥4`; one loop per section, priority Intro>Outro>Break>Build, cap 3 (default) /
  4 (build); `bar_ms≤0` (no grid/BPM=0) ⇒ none; clamps the loop end before track end (shrink or skip).
  `analyze_loops(content, db, *, include_build=False)` is the thin ANLZ wrapper (reuses the existing
  PSSI/PQTZ parse + bar-length math) that feeds `plan_loops`.
- **`autocue/serato_writer.py`** — `build_markers2` branches CUE vs LOOP on `is_loop`; LOOP entries get
  their **own 0-based loop index** (memory loops `slot=-1` ARE written — loops carry a loop index, not
  the cue slot); byte layout per `crew/researcher.md §1` with GATE-1 Decision 3(b) option-b defaults
  (`_LOOP_RESERVED=0xFFFFFFFF`, `_SERATO_LOOP_COLOR`, locked=0). `parse_markers2` now **decodes LOOP**
  (was dropped) and carries each entry's `raw` framed bytes. `write_serato_tags` **preserves existing
  file LOOP entries verbatim on rewrite (F1)** via `_existing_loop_entries` + a `preserve=` arg
  (container-aware decode for GEOB / FLAC / MP4); a generated loop colliding with a DJ loop's start is
  dropped (mirror-first).
- **`autocue/db_writer.py`** — `read_hot_cues` carries `OutMsec`/`BeatLoopSize`: any `OutMsec > InMsec`
  (non-loop rows are pinned to the `-1` sentinel) surfaces as a loop `CuePoint` (`loop_end_ms` +
  `loop_beats`). Point cues unchanged (F6 mirror-first).
- **`autocue/cli.py`** — new opt-in `--loops` flag; extracted `_build_parser()` (testability);
  `_merge_loops()` layers generated loops onto the export cue list (drop-on-start-collision). Wired
  into the `--serato` block only; honest note printed if `--loops` used without `--serato` (XML =
  inc 2).

## GATE-2 evidence (RUN → READ → SHOW, all THIS session)
- **STATIC — full pytest suite:** `python -m pytest -q` → **`1576 passed, 7 skipped` (exit 0)** in
  52.4s. (Baseline grew vs prior sessions because I installed the `mutagen` dev extra — see LESSONS —
  so `test_serato_writer.py` (~44) now un-skips — plus my 40 new tests.) No failures, nothing wrongly
  xfailed. `py_compile` clean on all changed sources. (No ruff/mypy configured in pyproject — pytest
  is the project's gate.)
- **My TDD file:** `tests/test_autoloops.py` → **40 passed** (keystone 4 · policy 16 · serato 12 ·
  read_hot_cues 5 · CLI 3).
- **No regressions:** `test_models.py`, `test_analyzer.py`, `test_serato_writer.py`, `test_db_writer.py`
  all green alongside.
- **npm test unaffected:** diff is **Python-only** (5 source + 2 test files) — zero `docs/` / css / js
  changes. Not run (no web surface touched).
- **Byte-for-byte round-trip (SHOWN)** — a generated `Outro` loop (10000→18000 ms):
  ```
  LOOP entry hex : 4c4f4f50 00 0000001a  00 00 00002710 00004650 ffffffff 0027aae1 00 00 4f7574726f 00
                   L O O P \0  len=26     rsv ix start=10000 end=18000 field5   color    c lk "Outro" \0
  decoded        : index=0 start_ms=10000 end_ms=18000 name='Outro' locked=False field5='ffffffff'
  round-trip     : end==loop_end_ms True | start==position_ms True
  F1 preserve    : raw LOOP entry byte-for-byte present in a rebuild → True
  ```
- **F1 preserve end-to-end:** `test_existing_serato_loop_survives_file_rewrite` writes a DJ loop into a
  real MP3, rewrites the Markers2 tag with only a hot cue, and asserts the DJ's `MyLoop` **survives** +
  the new `Intro` cue is present. Green.

## Honest scope / what I did NOT (and cannot) claim
- **Serato-accepts is NOT claimed.** Automated proof is the self-consistent writer↔parser round-trip
  only (F7). The 8 middle bytes (field5/field6/color) are **probe-verify** — `_SERATO_LOOP_COLOR`
  (`0027aae1`) + `_LOOP_RESERVED` (`ffffffff`) are the Decision-3(b) option-b defaults. **The USER
  must open a written file in Serato DJ Pro at GATE-2** to confirm loops render + are named; a one-pass
  byte fix if any are off (constants are named + commented for a cheap swap).
- Dev server **NOT started** (Serato/CLI path — not needed; and one-live-driver discipline #26).
- Increment 2 (RB XML `Type="loop"`) **NOT built** — held for the rolling checkpoint.
- `crew/test-designer.md` was **stale** (still the Review Dock) — no autoloops coverage map existed, so
  I derived TDD directly from DESIGN §1-§5 + researcher §1. Flagging for the coordinator.

## Suggested verifier hand-off
- Golden/behavioural file (DISJOINT from mine): pin the exact Serato Markers2 LOOP bytes for a fixture
  loop + assert an existing loop survives a rewrite (I proved it at unit level; a golden pin is the
  belt-and-braces). Live/real-artifact leg = `autocue --track … --loops --serato --dry-run` then a
  throwaway-copy write read back with the extended parser, and hand the file to the USER for Serato.

P3-AUTOLOOPS-INC1
STATUS: DONE

---

# INC-2 — Rekordbox XML loop marks (`autocue/writer.py` §5)

Commit `7b0fd81`. **One file changed: `autocue/writer.py`** (+ extended `tests/test_autoloops.py`).
INC-1 files untouched.

## Unit
In the mark-writing loop, branch on `cue.is_loop`:
```python
if cue.is_loop:
    track.add_mark(Name=cue.name or cue.label.value, Type="loop",
                   Start=cue.position_sec, End=cue.loop_end_ms / 1000.0, Num=cue.slot)
else:  # unchanged — Type="cue", no End (regression-safe)
    track.add_mark(Name=..., Type="cue", Start=cue.position_sec, Num=cue.slot)
```
**UNITS (the critical bit):** `Start` was already `cue.position_sec` (= `position_ms/1000.0`) — the
XML expects **seconds**, so `End` is `loop_end_ms / 1000.0` (same conversion). Verified
`add_mark(Name, Type="cue", Start, End=None, Num=-1)` in the installed pyrekordbox; `Type="loop"`
serializes to numeric `Type="4"`, `Type="cue"`→`"0"`. `Name` already carries the loop name.

## GATE-2 evidence (fresh this session)
- **Full suite:** `python -m pytest -q` → **1598 passed, 7 skipped**. My INC-2 change is green.
- **INC-2 XML tests:** `tests/test_autoloops.py -k XmlLoopMark` → **5 passed** (Type="4" + seconds
  End + Num=-1; unit-bug guard `End<1000`; non-loop `Type="0"` no-End regression; mixed cue+loop;
  name fallback). My full TDD file: **45 passed** (40 INC-1 + 5 INC-2). `test_writer.py`: **40 passed**
  (no regression).
- **Sample XML (SHOWN)** — one cue + one loop in a track:
  ```
  <POSITION_MARK Name="Intro" Type="0" Start="2.0"  Num="0" />
  <POSITION_MARK Name="Outro" Type="4" Start="10.0" Num="-1" End="18.0" />
  ```
  cue = `Type="0"` no End (unchanged); loop = `Type="4"` with `Start`/`End` in **seconds** (10.0→18.0,
  not 10000→18000).

## ⚠️ FLAG for coordinator — 3 PRE-EXISTING reds in the verifier's file (NOT INC-2, NOT mine)
`tests/test_autoloops_golden.py` (verifier-owned) has **3 failing** tests that fail **at HEAD
`eca754c` too** (proven: stashed my INC-2 diff → same 3 fail) — entirely independent of INC-2:
1. `TestNoBeatGridBreadcrumb::test_unusable_beat_grid_logs_breadcrumb…` — expects `analyze_loops`
   to **log a breadcrumb** on an unusable beat grid (DESIGN §VERIFY silent-failure lens). INC-1
   returns `[]` silently. A real gap, but in **`analyzer.py` (an INC-1 file)** — out of my INC-2
   scope ("change ONLY writer.py; do not re-touch INC-1 files").
2/3. `TestCliLoopsGate::test_with/without_loops…` — the CLI wiring **works** (stdout: "+ 1 loop(s):
   Outro", "Serato export: 1 written"); the test's own assertion iterates the `(content, cues)`
   pair tuples and calls `.is_loop` on a `content`/`list` (`AttributeError`) — a malformed spy check
   (its own comment: "Best-effort seam — reconcile … at P4").
   
I did NOT fix these (scope + they'd touch INC-1 files / the verifier's file). Recommend: coordinator
routes the breadcrumb gap as a small INC-1 follow-up (add a `logger.debug/info` breadcrumb in
`analyze_loops`/`_get_pssi_and_pqtz`'s no-grid path) and the verifier fixes its own CLI-gate
assertion.

## Still NOT claimed
- **Rekordbox-accepts is NOT claimed** — the XML is well-formed and the loop mark carries
  `Type=loop/Start/End/Name`, but the **USER must import the XML into Rekordbox at GATE-2** to confirm
  named memory loops appear. Automated proof is the serialized-attribute assertion only.

P3-AUTOLOOPS-INC2
STATUS: DONE

---

# P4-FIX — consolidated VERIFY-loop fix (systematic-debugging → root cause → fix → re-verify)

6 fixes, TDD (failing test first) + per-logical-unit commits. Touched `analyzer.py`, `cli.py`,
`serato_writer.py` (+ `tests/test_autoloops.py`). **`writer.py` (INC-2) NOT re-touched.** Serato LOOP
byte layout (fields 0x0a–0x12 option-b) **unchanged** — only the loop *index* byte (0x01) logic moved
(FIX 6).

| # | Fix | Root cause → change | Commit | Files |
|---|-----|---------------------|--------|-------|
| 1 | **Outro never fires** (auditor #1) | `_bars()` returned 0 for the terminal phrase (no next) → Outro failed the `≥4` gate. Hoisted `total_ms` above `_bars`; terminal fallback `next_ms=total_ms`. | `cb991ac` | analyzer.py |
| 2 | **R-NC8 Build** (auditor #2) | Build was behind `build_only`/`include_build` (unreachable). Dropped the opt-flag: UP default-eligible lowest priority, `include_build` DELETED from `plan_loops`+`analyze_loops`, explicit `cap=4`. | `8216fc9` | analyzer.py |
| 3 | **P-10 no-grid breadcrumb** (verifier RED) | analyzer had no logging; no-grid returns were silent. Added module logger + `logger.warning("track %s: no usable beat grid — skipping loops")` on both no-grid paths; "no eligible phrase" stays SILENT (distinguishable). | `152f177` | analyzer.py |
| 4 | **C-3 dry-run preview** (verifier) | Loops computed only in the real write branch, after the dry-run return. Preview loop placements (name + start–end + bars) in the dry-run path; still writes nothing. | `ceef102` | cli.py |
| 5 | **N2 decode-fail breadcrumb** (auditor N2) | `_existing_loop_entries` silently `[]` when a present v2 tag decodes to nothing → `--overwrite` could drop DJ loops. Warn on zero-entries (no control-flow change). | `b8244a8` | serato_writer.py |
| 6 | **N1 loop-index** (auditor N1) | Generated loops indexed from `len(preserve)` → collided with a DJ loop in a non-contiguous high slot. Now `max(existing index)+1` via `_next_loop_index`. | `5d51872` | serato_writer.py |

## GATE-2 evidence (RUN → READ → SHOW, this session)
- **Full suite:** `python -m pytest -q` → **1614 passed, 7 skipped (exit 0)** in 28.5s. **Zero fails**
  (verifier expected ~1601; higher because P4-FIX added ~13 new tests). The last suite red (P-10) is
  green.
- **Golden file** `tests/test_autoloops_golden.py` → **20/20** (was 3 red: P-10 + the 2 CLI-gate cases
  — all green now).
- **No INC-2 regression:** `git diff` shows P4-FIX touched only analyzer/cli/serato_writer — **writer.py
  untouched**; INC-2 XML tests (5) + `test_writer.py` (40) all green.
- **Serato option-b bytes UNCHANGED (SHOWN):** LOOP entry `field5(0x0a)=ffffffff`,
  `field6/color(0x0e)=0027aae1`, `color(0x12)=0`, `locked(0x13)=0` — byte-identical to INC-1 (user
  still verifies render in Serato at GATE-2; F2/F7).
- **FIX 1 proof:** a terminal-OUTRO track now yields an "Outro" loop bounded by track end (was never
  emitted — zero prior coverage). **FIX 2 proof:** a lone Build phrase → a "Build" loop by default;
  cap=4. **FIX 3 proof:** unusable/absent grid logs a "grid" WARNING; a valid grid + no eligible
  phrase stays silent.

## Still NOT claimed
- Serato-ACCEPTS (F2/F7) and Rekordbox-import (INC-2) remain **USER GATE-2 steps** — unchanged by this
  fix pass.

P4-FIX-AUTOLOOPS
STATUS: DONE

---

# XML-WIRING-FIX — `autocue --loops` writes 0 loops to the Rekordbox XML

systematic-debugging. **Source changed: `autocue/cli.py` only** (2 commits, TDD failing-test-first).
`writer.py`/`serato_writer.py`/`analyzer.py`/`models.py`/`db_writer.py` untouched.

## Root cause (two layers — the second found by probe)
1. **(reported)** The XML/default write branch never merged loops — `analyze_loops` + `_merge_loops`
   lived only in the `--serato` block, so `write_xml` received loop-free `cues`. `autocue --loops`
   (no `--serato`) → XML with **0** loop marks.
2. **(deeper, probe-confirmed)** Even after wiring, `_merge_loops` dropped any loop colliding with
   **any** cue's start. Generated phrase **cues** and generated **loops** sit at the *same phrase
   downbeats*, so a naive mirror would still write **0 loops** (probe: 3 loops → 0). A memory loop
   (`Num=-1`) and a hot cue (`Num 0-7`) are **different Rekordbox objects that coexist** — the
   collision-drop must apply only to an existing **loop** (mirror-first: a DJ's saved loop still
   wins). I fixed `_merge_loops` accordingly; this also fixes the latent same-downbeat drop on the
   `--serato`-with-generated-cues path. *(Went beyond the literal "mirror the --serato block" because
   mirroring it verbatim would not have fixed the user-visible bug — flagged for coordinator.)*

## Changes (`autocue/cli.py`)
| Commit | Unit |
|--------|------|
| `9ed6c58` | `_merge_loops` collides only against existing **loop** starts (loops coexist with hot cues). |
| `37308e3` | XML branch merges `analyze_loops` + `_merge_loops` before `write_xml`, prints "N named loop(s) added"; **DELETED the stale** "loops only with --serato / later increment" note; dry-run preview now reflects the merged/collision-filtered set (== what is written). |

## The test (end-to-end CLI→XML — had ZERO coverage, why it shipped)
`TestCliXmlLoopWiring` (drives real `write_xml`): `--loops` → a `POSITION_MARK Type="4" Num="-1"
End=…` loop mark is written; **no `--loops` → none** (regression); a cue+loop at the same downbeat
**both** land. Plus `TestMergeLoops` updated to the corrected semantics (coexist with point cue; drop
on existing-loop collision; two generated loops at one start dedupe).

## GATE-2 evidence (RUN → READ → SHOW, this session)
- **Full suite:** `python -m pytest -q` → **1619 passed, 7 skipped (exit 0)**.
- **Scope:** only `cli.py` changed. **No regression:** serato_writer(44) + writer/INC-2(40) +
  db_writer + models = 184 green; golden `test_autoloops_golden.py` 20/20.
- **Real artifact (SHOWN)** — `autocue --loops --output …` on a synthetic Intro/Build/Chorus/Outro
  track (before: 0 loops):
  ```
  Wrote /tmp/xmlwire_demo.xml — 3 named loop(s) added
  cue   Name=Intro  Start= 0.0  End=  -   Num=0
  cue   Name=Build  Start=16.0  End=  -   Num=1
  cue   Name=Drop   Start=32.0  End=  -   Num=2
  cue   Name=Outro  Start=48.0  End=  -   Num=3
  LOOP  Name=Intro  Start= 0.0  End=16.0  Num=-1
  LOOP  Name=Build  Start=16.0  End=32.0  Num=-1
  LOOP  Name=Outro  Start=48.0  End=56.0  Num=-1
  ```
  3 memory loops (Type="4", Num=-1, End in seconds) coexisting with the 4 hot cues at the same
  downbeats. **Bonus:** the `--serato`-with-generated-cues path now writes loops too (was the same
  collision-drop).

## Still NOT claimed / non-goals honoured
- **Rekordbox-ACCEPTS is a USER GATE-2 step** — user re-runs `autocue --loops`, imports the XML, and
  confirms named memory loops appear.
- Direct `master.db` loop write (`write_cues_to_db`) **NOT touched** — remains the deferred non-goal.

XMLWIRE-AUTOLOOPS
<<<<<<< Updated upstream
=======
STATUS: DONE

---

# INC-3 — DB-DIRECT loop write (`--write-db`) · ⚠️ mutates the real Rekordbox DB

TDD, **no-clobber test written FIRST** (it IS the safety case). 2 commits.
Files: `autocue/db_writer.py` (**additive only — 0 deleted lines**), `autocue/cli.py`,
`tests/test_autoloops.py`. **`write_cues_to_db` LEFT UNTOUCHED** (verified: `git diff` shows zero
deletions in db_writer.py) — it stays the shared server path for `/api/apply` + SSE + `memory_cue_mode`.

| Commit | Unit |
|--------|------|
| `70d8a48` | NEW append-only `write_loops_to_db()` + `autocue_serve_is_running()` (db_writer.py) |
| `fb218f3` | `--write-db` CLI branch + full safety contract (cli.py) |

## 1. `write_loops_to_db(content, cues, db, *, dry_run=False) -> int` — append-only
**NO DELETE ANYWHERE ⇒ clobber impossible by construction.** Filters memory loops
(`is_loop and slot == -1`) → queries the existing `Kind=0` `InMsec` set → **SKIPS** any loop whose
start exactly collides (mirror-first: the DJ's entry wins ⇒ also **idempotent**) with a `logger.info`
breadcrumb → INSERTs only survivors: `Kind=0`, `InMsec/InFrame`, `OutMsec=loop_end_ms` (ms),
`OutFrame=round(end*150/1000)`, `OutMpegFrame=OutMpegAbs=0`, **`ActiveLoop=0`** (saved but UNARMED),
`BeatLoopSize=loop_beats` (**BEATS** = bars×4), `Comment=name`, `ID=db.generate_unused_id(DjmdCue)`,
`UUID=uuid4()`; `begin_nested()` savepoint → `sp.commit()` → `db.session.commit()`; on error
rollback + `logger.exception` + **raise**.

## 2. `--write-db` CLI — safety contract (mirrors routes.py:975-997)
Third terminal branch, after the dry-run block, before `--serato`. **Gates on `--loops`** (loops-only;
never writes cues to the DB). Order: **Rekordbox-running → abort** · **NEW `autocue serve` running →
abort** (single-writer: `rekordbox_is_running` does NOT detect the server, which holds its own
read-write handle) · **`backup_database()` BEFORE any write; backup failure ABORTS (nothing written)**
· **PRINT the backup path** (the user's only undo). `--write-db --dry-run` writes **nothing** (the
dry-run block returns first).

## 3. THE NO-CLOBBER PROOF (scratch in-memory SQLite + real pyrekordbox schema — NEVER the live DB)
```
BEFORE — the DJ's 2 hand-placed memory cues  (2 Kind=0 rows)
   cue  In=  5000 Out=    -1 Beats=  0 Active=0 'DJ Memory 1'
   cue  In= 60000 Out=    -1 Beats=  0 Active=0 'DJ Memory 2'

write_loops_to_db -> wrote 2   (3 offered; 'Clash' @5000 collides -> skipped + logged)

AFTER — BOTH DJ memory cues INTACT + 2 new loops coexist  (4 Kind=0 rows)
   cue  In=  5000 Out=    -1 OutFrame=    0 Beats=  0 Active=0 'DJ Memory 1'   ← survived
   LOOP In= 10000 Out= 18000 OutFrame= 2700 Beats= 16 Active=0 'Intro'
   cue  In= 60000 Out=    -1 OutFrame=    0 Beats=  0 Active=0 'DJ Memory 2'   ← survived
   LOOP In= 90000 Out=106000 OutFrame=15900 Beats= 32 Active=0 'Outro'

re-run -> wrote 0  (idempotent; rows unchanged)
```
Units verified: `OutFrame` = 18000×0.15=**2700**, 106000×0.15=**15900**; `BeatLoopSize` = bars×4
(**16**/**32**); `ActiveLoop=0`.

## 4. Tests (all in `tests/test_autoloops.py`; scratch DB only)
- **★ `TestWriteLoopsNoClobber`** — 2 pre-existing memory cues survive **byte-identical**
  (ID/UUID/Kind/InMsec/OutMsec/Comment); hot cue untouched. *(the load-bearing case)*
- `TestWriteLoopsColumns` — every §2 column + unit; only `slot==-1` loops written; `ID` is a real
  int (the `generate_unused_id` stub — else it silently writes `ID=<MagicMock>`).
- `TestWriteLoopsIdempotentAndCollision` — re-run adds **0**; colliding loop skipped **+ logged**;
  dry-run writes nothing; empty is a no-op.
- **`TestMirrorNegativeWhyNotWriteCuesToDb`** — asserts `write_cues_to_db(..., overwrite=True)` **DOES
  delete Kind=0** (both DJ memory cues gone) — pinning *why* we do not reuse it.
- `TestWriteDbCli` (7) — flag gating on `--loops`; abort on Rekordbox; abort on `autocue serve`;
  **backup-failure aborts with NOTHING written**; happy path backs up + prints the path; `--dry-run`
  writes nothing.

## 5. GATE-2 evidence
- **Full suite:** `python -m pytest -q` → **1635 passed, 7 skipped (exit 0)**.
- **`write_cues_to_db` untouched:** 0 deleted lines in db_writer.py; server suites green
  (db_writer + duplicates-integration + serve_routes = 293 passed).
- No test or probe ever touched the live `master.db` (in-memory SQLite only).

## 6. The exact user command (GATE-2 — user runs it)
```bash
# Rekordbox CLOSED, and no `autocue serve` running. Test with ONE track first.
autocue --track "SONG TITLE" --loops --write-db --dry-run   # preview: writes nothing
autocue --track "SONG TITLE" --loops --write-db             # writes; prints the backup path
# → open Rekordbox, confirm named memory loops (Intro/Outro/Break/Build).
# Undo = restore the printed backup from ~/.autocue/backups/master_<TS>.db
autocue --library --loops --write-db                        # then the whole library
```
**Rekordbox-ACCEPTS remains the USER GATE-2 step.** Recommend the user first runs it against a
**COPY** of master.db (`--db-path`) if they want a zero-risk rehearsal.

## 7. Non-goals honoured
- `write_cues_to_db` **not modified**. No web-UI/server loop surface. `ActiveLoop=0` (we write
  *saved* loops, never an armed loop). Loops-only — `--write-db` never writes cues.

P3-AUTOLOOPS-INC3
STATUS: DONE

---

# P4FIX-INC3 — consolidated fix for the DB-direct loop write ⚠️ DANGEROUS SURFACE

TDD, 3 per-logical-unit commits. Files: `autocue/cli.py`, `autocue/db_writer.py`,
`tests/test_autoloops.py`. **No test or probe ever touched the live master.db.**

| Fix | Commit | What |
|---|---|---|
| **F1 + F4 + F5** | `a212aad` | pre-open guards (BL-1 BLOCKER) · correct `db_path` · per-track errors |
| **F2** | `077cdcc` | serve probe by PROCESS + full 7432-7441 range, fail-safe |
| **F3** | `6cf2d8f` | `write_cues_to_db` spares memory LOOPS on the Kind=0 rewrite |

## 🔴 F1 — BL-1 BLOCKER (`--write-db` aborted 3/3 on a real DB)
**Root cause:** `rekordbox_is_running()` probes an **exclusive file lock**. AutoCue had *already*
opened master.db (cli.py) and the analysis queries left SQLAlchemy's autobegin txn holding a SQLite
lock — so the guard **self-detected AutoCue's own handle** and printed a false "Rekordbox is running".
**Fix:** new `_preflight_write_db(args)` runs the `--loops` gate + Rekordbox guard + serve guard
**BEFORE `MasterDatabase(...)` is ever constructed** — which is also the semantically correct place
("Rekordbox must be closed before we even open the DB"). Gated on `args.write_db`; every other CLI
path is unchanged.

### ⚠️ What the ordering test now pins (the anti-mock defence)
**Every unit test MOCKS `rekordbox_is_running` — that is exactly why BL-1 shipped.** A mock can never
reveal a self-lock. So the new test does not assert the guard's *return value*, it asserts the
**call ORDER**, which a mock cannot hide:
`test_rekordbox_guard_runs_BEFORE_the_db_is_opened` → `order.index("rb_guard") < order.index("open_db")`
(and `serve_guard < open_db`, `backup < write`). Any future refactor that moves the guard back after
the DB is opened fails this test immediately — regardless of how the guard is mocked.

**Honest evidence note:** I could **not** reproduce the lock on a plain-SQLite scratch file (a SELECT
there does not take the same lock — the real condition needs SQLCipher/WAL via pyrekordbox). The
authoritative reproduction is the **verifier's real-DB-COPY characterization**. The fix is correct by
construction (a probe on a file we have not opened cannot self-detect) and is pinned by the ordering
test — but the **real proof is the verifier's re-run: `--write-db` must now WRITE, not abort.**

## 🔴 F2 — serve single-writer probe (BL-2 / auditor CRITICAL 95)
Probed **only 7432**, but `serve()` auto-switches to the next 9 free ports and honours `--port` — a
server on 7433-7441 or on `:3004` (what this crew ran) was **invisible**, so the guard silently never
fired. Now: (1) scan the whole **7432-7441** fallback range, (2) scan the **process table** (psutil, a
hard dep) for `autocue serve` / `python -m autocue serve` — catches **any** port. `"serve"` must be its
own argv token, so `autocue --loops --write-db` is not a false positive; the current pid is skipped.
**N2 (fail-open) fixed:** an unresolvable probe now returns **True — refuse the write**, never False.
```
`autocue serve --port 3004` running -> autocue_serve_is_running() = True   (old probe: False ❌)
```

## 🟠 F3 — `write_cues_to_db` spares memory LOOPS (auditor IMPORTANT 88) — SHARED PATH
The blanket `Kind=0` delete destroyed **both** our `--write-db` loops **and the DJ's hand-placed memory
LOOPS**, silently, on every `overwrite=True` apply (`/api/apply`, SSE, CLI `--overwrite`). Memory cues
and memory loops share `Kind=0`; the only discriminator is `OutMsec`. The rewrite now deletes
**point cues only** (`OutMsec <= InMsec`) and spares loops (NULL `OutMsec` → spared, safe direction).
memory_cue_mode semantics intact; hot cues still deleted slot-wise.
**Shared server suites GREEN at exactly 293** (db_writer + duplicates-integration + serve_routes);
verifier golden 21/21 (its DB-5 seeds point cues, still deleted).

## 🟠 F4 — one path for guard + backup + write (auditor 85)
`db_path` was reconstructed as `_db_dir/"master.db"` — **not** the file opened under `--db-path`, so the
**backup targeted the wrong file**, voiding the printed "your ONLY undo" promise. Now
`db_path = Path(args.db_path) if args.db_path else _default_db_path()`; guard, backup and write all
target the **same** file (pinned by `test_guard_and_backup_target_the_db_path_flag`).

## 🟡 F5 — per-track exception handling (auditor N1)
The CLI write loop now catches per track: names the failing track, keeps going, and reprints the
backup path — instead of dumping a raw traceback over already-committed tracks.

## GATE-2 evidence
- **Full suite:** `python -m pytest -q` → **1668 passed, 7 skipped (exit 0)**, 0 failures.
- **Shared path (F3) safe:** server suites **293 passed** (unchanged count); verifier golden **21/21**.
- New tests: ordering pin · db-path targeting · per-track failure · 7 serve-probe cases (fallback
  port, arbitrary `--port` via process, `python -m`, no-false-positive, fail-safe, full range scan) ·
  2 loop-sparing cases.

## ⚠️ NEW finding for the coordinator (found while doing F3 — NOT fixed, out of the given scope)
`has_existing_memory_cues()` counts **all** `Kind=0` rows, including the loops `--write-db` writes. Its
only caller is the `write_memory` gate (`db_writer.py:677`):
`write_memory = bool(mem_cues) and (overwrite or has_existing_memory_cues(...) == 0)`.
⇒ **After a user runs `--write-db`, a later non-overwrite apply silently stops writing their memory
CUES** (our loops make the count non-zero). The gate was designed to protect memory *cues*, so counting
*loops* is semantic drift introduced by INC-3. One-line fix, consistent with F3's discriminator: count
point cues only (`OutMsec <= InMsec`). I did **not** apply it — it is a further semantics change to the
shared server path that neither the auditor nor this task asked for, and I will not widen shared-path
changes unilaterally on a destructive surface. **Coordinator's call.**

## Still NOT claimed
- The real-DB proof is the **verifier's** re-run on a COPY: `--write-db` must now actually WRITE
  (not abort), and the serve guard must fire for a serve on a NON-default port. Rekordbox-accepts
  remains the USER GATE-2 step.

P4FIX-AUTOLOOPS-INC3
STATUS: DONE

---

# P5FIX-INC3 — final cleanup batch (all 5 items)

TDD, 4 per-logical-unit commits. Files: `autocue/db_writer.py`, `autocue/cli.py`,
`tests/test_autoloops.py`. **The append-only `write_loops_to_db` spine is byte-identical** (verified
by diffing the function across the batch); Serato/XML paths untouched. Live master.db never touched.

| # | Commit | Fix |
|---|---|---|
| **1 + 5** | `6acc573` | `has_existing_memory_cues()` counts POINT CUES only + one shared NULL-consistent predicate |
| **2** | `ea188d4` | `--write-db` exits **1** on a partial write |
| **3** | `79b493b` | serve probed BEFORE the lock probe → correct attribution |
| **4** | `7d8683d` | serve process scan tightened (no grep/pytest false positives) |

## 🟠 #1 — the COUNT half of the Kind=0 conflation (IMPORTANT 88)
`has_existing_memory_cues()` counted memory **LOOPS** as memory cues. It gates `write_memory`
(`overwrite or has_existing_memory_cues(...) == 0`), so once `--write-db` had added our `Kind=0`
loops the count was non-zero and a later **default (overwrite=False) apply silently stopped writing
the user's memory CUES** — a regression *introduced by INC-3*.
**Fix:** both halves now share ONE predicate — `_point_cue_filter()` = `OutMsec IS NULL OR OutMsec <=
InMsec` — used by the COUNT **and** F3's DELETE, so they can never drift apart again.
**This folds in #5 (N3):** NULL `OutMsec` is now treated as a point cue in both, matching
`read_hot_cues` (NULL → -1). (Unreachable — `OutMsec` is NOT NULL — but consistent.)
**Test:** a track whose only `Kind=0` row is an INC-3 loop still gets its memory cue written on a
non-overwrite apply (RED before: `assert 0 == 1` — the cue was silently dropped).
**Fixture blind spot fixed too:** `has_existing_*` call `db.query` (not `db.session.query`), so the
MagicMock made `.count() == 0` silently False and the gate untestable — that is *why* this shipped.
The scratch fixture now wires `db.query` to the real session.

## 🟡 #2 — partial write must not look like success (N1)
F5 reported per-track failures loudly but still **exited 0**, so a script saw SUCCESS after a partial
DB write (earlier tracks already committed). Now `sys.exit(1)` when any track failed; a fully
successful write still exits 0. Both exit codes pinned by tests.

## 🟡 #3 — blame the server, not Rekordbox (verifier LOW nit)
A running `autocue serve` holds master.db, so it **also** trips the file-lock probe inside
`rekordbox_is_running()`. Probing Rekordbox first made the CLI say *"Rekordbox is running"* when the
real culprit was our own server — the write was correctly refused, only the message lied. The serve
probe now runs **first** and short-circuits (the generic lock probe is never even reached); its text
is also softened to "the database is locked by another process (Rekordbox is running, or another app
holds master.db open)".

## 🟡 #4 — serve scan false positives (N2)
The scan matched any process with a standalone `serve` token AND `autocue` as a substring anywhere —
so `grep serve autocue/cli.py` or `pytest -k serve autocue` made `--write-db` **refuse**. New
`_is_serve_cmdline()`: the token must be exactly `serve` **and the token before it must end with
`autocue`** (`autocue serve`, `/usr/local/bin/autocue serve`, `python -m autocue serve`).
**The fail-SAFE direction is untouched** — an unresolvable probe still returns True (refuse). 4
parametrized false-positive cases + 4 real-invocation cases.

*(N4 skipped as instructed — `uvicorn autocue.serve.app:…` is not a documented way to run the server.)*

## GATE-2 evidence
- **Full suite:** `python -m pytest -q` → **1681 passed, 7 skipped (exit 0)**, 0 failures.
- **Server suites (the blast radius for #1/#5):** `tests/test_serve*.py` → **234 passed** (unchanged).
- **Spine intact:** `write_loops_to_db` byte-identical across the batch; only `cli.py` +
  `db_writer.py` touched; `serato_writer.py` / `writer.py` untouched.

P5FIX-AUTOLOOPS-INC3
STATUS: DONE

---

# P6-DOCS — ⚠️ PARTIALLY BLOCKED. The task premise is stale; I applied only what is TRUE.

**Commit `277cf8d` (docs only, no source, suite not re-run — no behaviour change).**

## Why most of this task could not be executed honestly
The task said "apply the verbatim edit text from **P6-v2**". **P6-v2 contains no such edit text — it is
a GATE-0 STOP.** It says, verbatim: *"Every docs edit from my P6 pass — and everything this P6-v2 task
asked for — must NOT be applied."* I verified the researcher's claim myself rather than trust it:

```
behind origin/main: 12   ahead: 23
origin/main ALREADY SHIPS:  cli.py --loops (:86) · autocue/analysis/loops.py ·
                            db_writer.read_loops() (:147) · write_memory_loops() (:481)
docs/FEATURES.md on main:   "### Loop generation (--loops)"  (line 675)  ← we don't have it (stale base)
```
So the four remaining doc homes would each document a surface that **collides with shipped `main`**:

| Target | Verdict | Why |
|---|---|---|
| CLAUDE.md dev-commands `--loops`/`--write-db` | ❌ **NOT APPLIED** | main's `--loops` has *different semantics* (seam-validated mix-in/mix-out, max 2, librosa). Documenting ours would become a lie on merge. |
| docs/FEATURES.md auto-loops entry | ❌ **NOT APPLIED** | main already has `### Loop generation (--loops)`. Ours would duplicate/contradict it. |
| architecture.md module map | ❌ **NOT APPLIED** | names `analyzer.plan_loops` / our `serato_writer` LOOP branch — the Serato LOOP work is a **duplicate of main's** and may not survive the re-scope. |
| analysis-and-testing.md loop policy | ❌ **NOT APPLIED** | the policy itself is an open **product decision** (ours vs main's seam-validated) pending the human. |

Documenting any of these now = documenting a lie. The task itself said: *"do not document a lie."*

## ✅ What I DID apply (the one edit that is true regardless of the re-scope)
Both homes get the **`Kind=0` invariant** — correct against our branch **and** against `main`, and it
documents a footgun that is **LIVE ON MAIN TODAY**:

1. **`.claude/project/db-constraints.md`** — new bullet after the memory-cue entry:
   - `Kind=0` is **shared** by memory CUES and memory LOOPS; discriminator = `OutMsec`
     (`-1` = point cue, `> InMsec` = loop).
   - **Never blanket-DELETE `Kind=0`** — a rewrite meant for cues destroys hand-placed **loops**, and
     vice-versa. Both `write_cues_to_db(overwrite=True)` **and main's `write_memory_loops(overwrite=True)`**
     do exactly this. Delete point cues only (`OutMsec IS NULL OR OutMsec <= InMsec`).
   - **Never blanket-COUNT `Kind=0`** to gate a write — it silently skips.
   - Loop writes must be **append-only** (insert non-colliding rows, never delete) — idempotent and
     clobber-proof by construction.
   - DB-write tests: **scratch in-memory SQLite only, never the live `master.db`**; stub
     `generate_unused_id` and wire `db.query`, or a MagicMock yields false greens.
2. **`CLAUDE.md`** must-know bullet — the same invariant in one line, pointing at db-constraints.md.

## 🔴 Verified while writing this (for the coordinator/human)
`origin/main:db_writer.py:481-518` `write_memory_loops()` docstring claims *"manually placed memory
data is never destroyed silently"* — **that is false.** With `overwrite=True` it blanket-deletes every
`Kind=0` row, so **`autocue --library --loops --overwrite` on `main` today wipes every hand-placed
memory CUE in the library.** Without `--overwrite` it silently skips any track that has a memory cue.
Our append-only `write_loops_to_db()` is precisely the fix. **This is the highest-value thing on this
branch and it is a bug fix against main, not a new feature.**

## Also stale (source — NOT touched, per "no source changes")
Our own `cli.py` `--loops` argparse help still says *"Currently written with --serato as Serato LOOP
tags"* — untrue since the XML + DB paths shipped. Needs a one-line fix in the re-scoped increment.

## Recommendation
**Rebase onto `origin/main` and re-enter GATE-1** on the real gap (per researcher's table: KEEP the
append-only no-clobber write + XML loop marks + the serve/backup hardening; DROP the duplicate Serato
LOOP + `read_hot_cues` OutMsec work; the loop *policy* needs a human call). Docs for the feature
surface can only be written after that lands.

## P6-DOCS-FIX — I documented a lie. Corrected. (commit `2245f3a`, docs only)

My own P6 docs commit (`277cf8d`) contained **two** false claims. Caught by the coordinator; verified
against source and fixed. No source change, no suite re-run.

### What was wrong
1. **Hallucinated symbols.** I named **`write_memory_loops(overwrite=True)`** and **`read_loops()`** as
   if they were ours. `grep -rn write_memory_loops autocue/` → **nothing**. Both exist **only on
   `origin/main`** (`db_writer.py:481` / `:147`) — I had read them there while verifying the GATE-0
   rebase claim and carried them into docs that live in *this* tree, where a reader greps and finds
   nothing. **Both references removed.**
2. **Worse — stale pre-fix state.** I wrote that the blanket-`DELETE` and blanket-`COUNT` footguns are
   *current* behaviour. **They are not: I fixed both myself** (F3 → the DELETE; P5-FIX #1 → the COUNT).
   The doc described a codebase that no longer exists.

### What each claim now says (all source-verified)
| Claim | Source proof |
|---|---|
| `Kind=0` shared by memory CUES + LOOPS; discriminator `OutMsec` (**KEPT** — correct & important) | the same test `read_hot_cues` uses, `db_writer.py:174` |
| The blanket-DELETE hazard applied to **`write_cues_to_db`'s memory rewrite ONLY** — and it now deletes **point cues only**, sparing loops | `db_writer.py:685`, via `_point_cue_filter()` |
| The blanket-COUNT half was **`has_existing_memory_cues()`** — now counts **point cues only** | `db_writer.py:154`, same predicate |
| COUNT and DELETE share **one** predicate so they cannot drift apart | `_point_cue_filter()` `db_writer.py:138` |
| **`write_loops_to_db()` is the append-only reference impl** — **no `overwrite` param, ZERO `DELETE`s**, idempotent; never reuse `write_cues_to_db` for loops | `db_writer.py:229`; grep: `0` `.delete(` calls in the function body |

### Grep proof — every symbol named in both docs exists in source
```
write_memory_loops / read_loops  →  REMOVED (0 refs in CLAUDE.md + db-constraints.md) ✓
read_hot_cues ✓ · _point_cue_filter ✓ · has_existing_memory_cues ✓ · write_cues_to_db ✓
write_loops_to_db ✓ · generate_unused_id ✓ · DjmdCue ✓   (all present in autocue/)
line numbers: 138 _point_cue_filter · 154 has_existing_memory_cues · 174 read_hot_cues
              229 write_loops_to_db · 685 write_cues_to_db      (all confirmed by sed)
write_loops_to_db: no `overwrite` param ✓ · 0 `.delete(` calls ✓
```

### Lesson
The GATE-0 verification (reading `origin/main`'s source to confirm the rebase blocker) leaked
main-only symbols into docs describing **our** tree. **Rule: every symbol named in a doc must be
grepped in the tree that doc ships with — reading another ref's source is exactly how a plausible,
non-existent function name gets written down.** And: after fixing a bug, re-read the docs describing
it; I documented the footgun I had already fixed.

*(The origin/main `write_memory_loops` blanket-`Kind=0`-delete data-loss bug is still real and still
worth fixing — but it belongs in the BOARD/handoff to the coordinator, not in this tree's
db-constraints.md, until the rebase lands.)*

P6DOCSFIX-AUTOLOOPS
STATUS: DONE

---

P6DOCS-AUTOLOOPS
STATUS: BLOCKED — 4 of 5 doc homes un-writable (they would document a surface that collides with the
auto-loops feature ALREADY MERGED to origin/main; we are 12 behind). Applied the ONE universally-true
edit: the Kind=0 memory-cue/memory-loop invariant (CLAUDE.md + db-constraints.md, commit 277cf8d).
Needs: rebase + GATE-1 re-scope before the rest can be written.

---

# PR1-KIND0 — fix the LIVE `Kind=0` clobber on main (`fix/loop-kind0-clobber`)

Branch cut fresh from `origin/main` (6e8b024). **Re-implemented against main's code** — no
cherry-pick from the archived `feat/autoloops` (667f244). One commit: **`11a7b13`**.
Staged `autocue/` + `tests/` only — **no `crew/` in the PR**.

## The invariant
Memory CUES and memory LOOPS share `DjmdCue.Kind=0`. The ONLY discriminator is `OutMsec`
(point cue: NULL/-1/`<= InMsec` · loop: `> InMsec`). Every blanket `Kind == 0` COUNT or DELETE
conflates two object classes.

## The 3 sites (+ a 4th I found)
| # | Site (origin/main) | Bug | Fix |
|---|---|---|---|
| 1 | `db_writer.py:142` `has_existing_memory_cues()` | blanket **COUNT** | counts **POINT CUES** only |
| 2 | `db_writer.py:515` `write_memory_loops()` `if overwrite:` | blanket **DELETE** → wiped DJ memory **CUES** | deletes **LOOP rows** only |
| 3 | `db_writer.py:607` `write_cues_to_db()` `if write_memory:` | blanket **DELETE** → wiped generated **LOOPS** | deletes **POINT CUES** only |
| **4** | **`analysis/quality.py:89`** (found by repo-wide grep) | in-Python `Kind == 0` counted loops as memory cues → inflated `memory_cue_count`, wrongly suppressed the "No memory cue" advisory | excludes loops |

**Repo-wide grep result:** every *other* `Kind` filter is `Kind > 0` / `Kind 1..8` (hot cues only)
— `db_writer.py:133,190,392,404,600` · `bench/cue_accuracy.py:198` · `serve/routes.py:580,598,1999,2386`.
**Not conflated; nothing left unfixed.**

## The fix — one symmetric discriminator pair, used everywhere
```python
_point_cue_filter()  # or_(OutMsec.is_(None), OutMsec <= InMsec)
_loop_filter()       # and_(OutMsec.isnot(None), OutMsec > InMsec)
```
Plus **`has_existing_memory_loops()`** (new) — the mirror counter. **Bug C (symmetric silent
suppression) is fixed at the gate, not just the count:** `write_memory_loops` now gates on existing
**LOOPS** (not memory cues), because loops are all it writes or replaces. So a track with only the
DJ's memory cues still gets its loops; a track with only loops still gets its memory cues.
**Intended protection preserved:** the DJ's memory cues are never overwritten without `--overwrite`,
and neither are their saved loops. `memory_cue_mode` semantics unchanged.
Also corrected the now-wrong CLI skip message (`cli.py`: "has memory cues/loops" → "already has saved loops").

## Tests — `tests/test_loop_kind0_clobber.py` (real in-memory SQLite + pyrekordbox schema; NEVER the live DB)
All five proofs **failed against main before the fix** (that failure IS the bug report):
```
AssertionError: DJ memory cue 900 was DELETED — clobber!      ← A
AssertionError: generated loop 910 was DELETED — clobber!     ← B
7 failed, 2 passed   →   9 passed
```
* DJ memory **CUES survive** `write_memory_loops(overwrite=True)` — **byte-identical**
* generated **LOOPS survive** `write_cues_to_db(overwrite=True)` — **byte-identical**
* track with **only loops** still gets its memory cues (COUNT fix)
* track with **only memory cues** still gets its loops (suppression fix)
* **no DELETE constrains `Kind=0` without `OutMsec`** — SQL-listener structural check; catches a
  blanket delete *even when it matches 0 rows in the fixture* (anti-vacuous guard included)
* + both "intended protection preserved" guards (green before AND after — they must not regress)
* `db.generate_unused_id` stubbed and `db.query` wired to the real session — without either, rows
  silently get `ID=<MagicMock>` / `.count() == 0` is silently False and the tests lie.

## Counts (fresh)
* **Full suite: 1559 passed · 8 skipped · 0 failed**
* **Server suites (`tests/test_serve*.py` — the `/api/apply` + SSE + `memory_cue_mode` blast radius): 234 passed**

*(Housekeeping: `tests/test_autoloops_golden.py` was untracked leftover from the archived branch —
it tests `write_loops_to_db`/`plan_loops`, which don't exist on main — and was removed from the
worktree. Preserved in `667f244`.)*

PR1-KIND0-FIX
STATUS: DONE

---

# PR2-GUARD — main's `--loops` DB write self-locked and could NEVER write

Branch `fix/loops-db-write-guard`, cut fresh from `origin/main` (6e8b024).
**Re-implemented against main's code** (consulted the archived `a212aad`, cherry-picked nothing).
One commit: **`ed44ac1`**. Staged `autocue/` + `tests/` only — **no `crew/`**.

## Root cause — a SELF-LOCK
`rekordbox_is_running()` probes an **exclusive file lock** on master.db. But `cli.py:108` opens
`MasterDatabase(...)` first, and the analysis queries leave SQLAlchemy's **autobegin transaction
holding a SQLite lock**. The guard at `cli.py:237` then ran against **AutoCue's own handle**, failed
to take the lock, and reported "Rekordbox is running" → `sys.exit(1)`.

| state | `_db_file_is_locked` |
|---|---|
| nothing open | False |
| DB open, no query | False |
| **DB open, AFTER a query** ← the CLI's state at the guard | **TRUE** |
| after `session.rollback()` | False |

⇒ **main's headline `--loops` DB write never wrote, on any run.**

## The fix (`autocue/cli.py`)
1. **`_preflight_loop_write(args)` runs the guard BEFORE `MasterDatabase(...)` is constructed** — a
   probe on a file we have not opened cannot self-detect. Semantically right too ("Rekordbox must be
   closed before we even open the DB"). Gated on `args.loops and not args.dry_run`, so `--dry-run`
   and the XML/Serato paths are **behaviourally unchanged**.
2. **`db_path` resolved from `--db-path`** (else the Rekordbox config default) instead of
   reconstructing `_db_dir/"master.db"` → the guard, the backup and the write now target the **same
   file**. The old reconstruction could back up a *different* file than the one written, voiding the
   printed "your only undo".
3. **`backup_database()` wrapped** — a failed backup **aborts** with a non-zero exit and **nothing
   written**. The backup path is printed.
4. **Partial write ⇒ non-zero exit.** Per-track errors are caught, failing tracks named, backup path
   reprinted — a script can no longer read success from a partial DB write.
5. `--dry-run` still writes nothing and previews the loops.

## ★ The anti-mock ordering test (why this shipped)
Every existing test **mocks** `rekordbox_is_running` — and **a mock can never reveal a self-lock**.
So the load-bearing test does **not** assert the guard's return value; it asserts the **CALL ORDER**:
```python
assert order.index("rb_guard") < order.index("open_db")   # cannot be hidden by any mock
assert order.index("backup")   < order.index("write")
```
Any future refactor that moves the guard back after the DB opens fails immediately.

## REAL-DB PROOF — a COPY of master.db (the live DB was never touched)
`cp master.db /tmp/ac-scratch/` + symlinked the read-only ANLZ dirs. Rekordbox **NOT** running.
Same track, same copy:
```
main  (unfixed) → EXIT 1   "Error: Rekordbox is running"   ← FALSE. nothing written, ever.
fixed           → EXIT 0   Database backed up to ~/.autocue/backups/master_20260711T070048.db
                           Loops: 2 written · 0 track(s) skipped
```
Rows that actually landed in the COPY (0 `Kind=0` rows before):
```
DjmdCue Kind=0 InMsec= 21504 OutMsec= 36801 OutFrame= 5520 'Mix In Loop'
DjmdCue Kind=0 InMsec=194783 OutMsec=210079 OutFrame=31512 'Mix Out Loop'
```

## Counts
* **Full suite: 1560 passed · 8 skipped · 0 failed**
* New: `tests/test_loops_db_write_guard.py` — **10 passed** (6 were RED against main).

## Noted, NOT in this PR
On the real-DB drive the first track came back `Loops: 0 written · 1 skipped` — main's own
"has memory cues/loops" gate (the conflated `Kind=0` COUNT). That is **PR#1**'s bug
(`fix/loop-kind0-clobber`), not this one; this branch is cut from main so PR#1's fix isn't present.
The two PRs are independent and complementary: **PR#2 lets `--loops` write at all; PR#1 makes what it
writes safe.**

PR2-GUARD-FIX
STATUS: DONE

---

# PR3-SERATO — main destroys the DJ's Serato-native loops on every `--serato` run

Branch `fix/serato-preserve-dj-loops`, cut fresh from `origin/main` (6e8b024).
Re-implemented against main's code per the researcher's P0-PR3-SERATO design (consulted the archive,
cherry-picked nothing). One commit: **`7310610`** — **112 insertions in one source file**.
Staged `autocue/` + `tests/` only — **no `crew/`**.

## The bug
`write_serato` reads the old tag (`:435`) but uses it ONLY for the fingerprint skip and the JSONL
backup — **never parsed for LOOP entries, never fed back into the payload**. `write_serato_tags` then
does a **full tag replacement**, rebuilding the payload exclusively from `cues` (`read_hot_cues`) +
`loops` (`read_loops` = **the Rekordbox DB**). Any LOOP entry in the FILE but not in the DB — a loop
the DJ made **in Serato** — is never re-emitted. **Dropped on every run.**

**Recoverability (stated precisely, not overclaimed):** the prior raw tag *is* appended to
`autocue_serato_backup.jsonl`, so it is recoverable — but only by **hand-restoring the entire previous
Markers2 tag**, which also reverts AutoCue's cues. **No per-loop restore, no automated path.**
Real data loss; **not** unrecoverable.

## 🔑 Dedup is MANDATORY, not politeness
Preserving *every* file LOOP entry would **double-count our own**: run 1 writes DB loop *X* into the
file; run 2 preserves the file's *X* **and** re-emits *X* from `read_loops()` → *X* twice, growing each
rewrite to the 8-cap. **Foreign loops only.** Discriminator = **exact `start_ms`** (safe: sourced from
`DjmdCue.InMsec` (int), written/parsed as an exact u32be round-trip — no tolerance needed):
* start matches a DB loop → **ours** → regenerate from `loops` ⇒ **the DB stays AUTHORITATIVE**, so a
  re-tuned loop end actually updates (a preserved stale file-loop can never shadow it);
* no DB match → **FOREIGN** → re-emit **raw framed bytes verbatim** (name, locked flag, colour kept).

## The fix — 4 touch points, no signature break
1. `parse_markers2` also captures each entry's framed bytes (`raw`).
2. NEW `_existing_loop_entries(path) -> [(start_ms, raw)]` (+ `_envelope_payload` for FLAC/MP4).
   Best-effort `[]`, but **WARNS when a v2 tag is present yet decodes to nothing** — else the rewrite
   silently drops the DJ's loops (silent-failure lens).
3. `build_markers2(cues, loops=None, *, preserve=())` — **keyword-only**, so every existing caller and
   test is untouched. Generated loops take the **lowest free slot**, not `enumerate()` position.
4. `write_serato_tags` computes the foreign-only preserve set. **`write_serato` and `fingerprint()`
   needed no change at all.**

**8-slot cap → THE DJ WINS.** Preserved loops keep their original slots; generated fill what's left;
surplus **generated** loops are dropped **with a breadcrumb** — never a DJ loop to make room for a
generated one. *(Lowest-free-slot also fixes a bug in our ARCHIVED code, which used
`max(preserved)+1` — a preserved loop at index 7 would have emitted index 8, outside Serato's 0-7
range. Not ported.)*

**Fingerprint-skip interaction (checked, and pinned by a test):** foreign loops are deliberately NOT in
the fingerprint. That is **safe because the skip path performs NO WRITE** — a skipped track's file is
never rewritten, so the DJ's loops can't be harmed while skipped. Including them would instead force a
pointless rewrite (mtime churn) every time the DJ touched a loop in Serato.

## Tests — `tests/test_serato_preserve_dj_loops.py` (throwaway files only)
**6 were RED against main** — headline: `AssertionError: the DJ's Serato-native loop was DROPPED`.
They use an **INDEPENDENT byte-walker**, not `parse_markers2`, so they cannot be satisfied by a bug in
the very parser they police.
* DJ loop (custom name, off-policy length, **locked**) survives **byte-identical** — **MP3 and FLAC**
* **no double-count** across 3 repeated rewrites (green before AND after — the guard against a naive
  preserve-everything fix)
* **DB authoritative**: a re-tuned loop end **does** update
* **>8 total** → every DJ loop kept, surplus generated dropped **+ logged**
* preserved at index 7 → generated **never** emits index 8 · non-contiguous preserved indices
* **CUE bytes byte-identical** (regression) · undecodable v2 tag → **warns**, write still succeeds
* skipped (unchanged-fingerprint) export → DJ loop intact and ours not doubled

## Counts
* **Full suite: 1561 passed · 8 skipped · 0 failed**
* **Existing serato suites UNMODIFIED and green:** `test_serato_writer.py` + `test_loop_generation.py`
  → **57 passed, 1 skipped**
* New suite: **11 passed**

PR3-SERATO-FIX
STATUS: DONE

---

# PR4 — single-writer guard (DONE) + BeatLoopSize (BLOCKED ON EVIDENCE)

Branch `fix/loops-single-writer-beatloopsize`, cut fresh from `origin/main` (6e8b024).
Re-implemented against main; cherry-picked nothing. Commit **`c6668aa`**.
Staged `autocue/` + `tests/` only — **no `crew/`**.

## ✅ GAP 1 — `autocue serve` single-writer guard — **DONE**
`--loops` only checked that **Rekordbox** was closed. `autocue serve` holds its own **read-write**
handle on master.db and `rekordbox_is_running()` cannot see it ⇒ two concurrent writers on the
library (db-constraints.md). New `autocue_serve_is_running()` in `db_writer.py`, called from the
`--loops` guard.

Both traps we hit on the archived branch are handled:
* **Port coverage** — serve defaults to 7432 but **auto-falls-back through 7433-7441** and honours
  `--port`, so a server on **:3004** is invisible to a single-port probe. We scan the **whole
  7432-7441 range AND the process table** (psutil, already a dependency) → any port.
* **No false positives** — a naive *"'serve' in cmdline and 'autocue' somewhere"* match fires on
  `grep serve autocue/cli.py` or `pytest -k serve autocue`. The token **immediately before `serve`
  must end with `autocue`**. The **current pid is skipped** (never self-detect).
* **Fail-safe** — an unresolvable probe returns **True (refuse the write)**, never False.

The serve probe is asked **before** the Rekordbox probe: a running server *also* holds the DB file, so
it trips the file-lock check inside `rekordbox_is_running()` and would otherwise be misreported as
"Rekordbox is running".

**PR #264 interaction (trivial):** that PR moves the Rekordbox guard into `_preflight_loop_write()`
(pre-DB-open). This probe is **process/port based — it cannot self-lock** — so it works in either
position. When #264 lands, move the call into that preflight next to the Rekordbox check: a one-line
move, no logic change.

**Tests — `tests/test_serve_single_writer.py`, 16 tests, ALL RED against main:** serve on :3004
detected → write **REFUSED** (no backup, no write) · full 7432-7441 range scanned · `autocue serve` /
`python -m autocue serve` / absolute path detected · grep / pytest / vim / an unrelated `serve` binary
/ the CLI's own sibling process do **not** trip it · current pid never self-detected · unresolvable
probe fails safe.

## 🔴 GAP 2 — `BeatLoopSize` — **BLOCKED ON EVIDENCE. I did not write a guessed value.**

The brief said *"BEATS — confirmed: the read side maps BeatLoopSize→beats verbatim"*. **That
confirmation is circular** and does not hold: the "read side" it refers to is **our own archived
code** — *we* wrote `loop_beats = bars*4` and *we* read it back verbatim. A writer agreeing with its
own reader proves nothing about what **Rekordbox** expects. I tried to verify it properly and **every
avenue is dry**:

| Source | Result |
|---|---|
| pyrekordbox schema | `BeatLoopSize: Integer` — **no unit, no comment, no default** |
| pyrekordbox docs/source | **nothing** — the column is declared and never explained |
| **Real data (the user's library)** | **0 of 30,765 `DjmdCue` rows have `BeatLoopSize != 0`.** The only 2 loop rows in the entire library are the ones *I* wrote in PR#2's test. **No Rekordbox-authored loop exists to compare against.** |
| ANLZ cue structs | store `loop_time` + `loop_enumerator`/`loop_denominator` (**a fraction**) — a different representation; no corroboration |
| **Rekordbox's own XML** | `POSITION_MARK` carries only `Name/Type/Start/End/Num` — **`BeatLoopSize` is not in the format at all** |
| main's code | **no reader** — nothing consumes the column |

**The last row is the important one.** Rekordbox's *own* XML import produces loops with
`BeatLoopSize` unset, and they work — loops are defined by `In`/`Out`. So **`0` is a value Rekordbox
itself produces**, not a corruption, and the premise *"CDJs lose the loop-size metadata"* is not
established. Writing a **guessed non-zero** value is not a no-op: if Rekordbox reads it as a
**quantized beat-loop size**, a wrong unit (bars written where beats are expected, or vice-versa)
could make it **snap or resize the DJ's loop** — strictly worse than the benign `0` it writes today.

This is precisely the standard the brief itself sets for `ColorTableIndex` (*"do NOT change it unless
you can prove the correct value"*). **I applied that same standard to `BeatLoopSize` and stopped.**

### 🔬 The 60-second experiment that settles it
Ask the user to **save ONE loop in Rekordbox** (any track; make it exactly **4 bars**), then read the
row back:
```
BeatLoopSize == 16  ⇒ BEATS  → write bars * 4
BeatLoopSize ==  4  ⇒ BARS   → write bars
BeatLoopSize ==  0  ⇒ Rekordbox does not populate it → GAP 2 is a NON-ISSUE, leave it
```
The one-line change is ready to land the moment that returns.

### `ColorTableIndex=0` (also asked)
`DjmdColor` IDs are **1-8**, so `0` = *no colour* — a valid "unset", exactly like a memory cue with no
colour assigned. I **cannot prove** a better value, so **unchanged**, per instruction.

## Counts
* **Full suite: 1566 passed · 8 skipped · 0 failed**
* New: `tests/test_serve_single_writer.py` — **16 passed** (all 16 RED against main)

PR4-FINAL-FIX
STATUS: DONE (GAP 1 shipped) · GAP 2 **BLOCKED ON EVIDENCE** — the "BEATS confirmed" claim is
circular (our writer ↔ our reader); 0/30,765 real rows carry a non-zero value, Rekordbox's own XML
does not carry the field at all, and no reader exists. Refused to write a guessed value into the
user's live library. One 60-second user experiment (save a 4-bar loop in Rekordbox) settles it.

---

# PR264-FOLD — serve single-writer guard folded INTO PR #264

Branch `fix/loops-db-write-guard` (PR **#264**), commit **`eb96f98`**, **pushed** (PR updated).
Staged `autocue/` + `tests/` only — **no `crew/`**.

## Why fold (both reviewers were right)
PR#264 and the standalone PR#4 branch **rewrite the same `cli.py` block**. #264's hunk **replaces**
PR#4's insertion point and carried no serve guard ⇒ merging both could have **silently dropped the
guard**. And once #264's pre-open preflight landed first, a running serve — which *also* holds the
file lock — would once again be misreported as *"Rekordbox is running"*. One coherent PR removes both
hazards: **the serve guard belongs in the same preflight as the Rekordbox guard.**

## What moved
* `autocue_serve_is_running()` + `_port_is_listening()` + `_is_serve_cmdline()` +
  `_serve_process_is_running()` → `db_writer.py` (ported from the standalone `c6668aa`, reused not
  re-derived).
* `tests/test_serve_single_writer.py` → this branch (**20 tests**, up from 16).
* **Called from INSIDE `_preflight_loop_write()`** — the pre-open guard this PR already adds.

## The ordering (the fold's key property, now pinned by a test)
**Serve is asked BEFORE Rekordbox.** A running server also holds the DB file, so it trips the
file-lock probe inside `rekordbox_is_running()`. Asked second, the user would be told to close
*Rekordbox* when the real culprit is *our own server*. Ask the specific question before the general
one. The serve probe is **process/port based, so it cannot self-lock** — safe pre-open.
`test_serve_is_asked_BEFORE_rekordbox_so_the_message_is_honest` asserts serve short-circuits at
index 0 and the lock probe is **never reached**.

## The auditor's [IMPORTANT 85] `.exe` false negative — FIXED
`endswith("autocue")` **missed a Windows `autocue.exe serve`**. Now matched on the **path stem**
(`Path(prev).stem.lower() == "autocue"`), so `autocue`, `/usr/local/bin/autocue`, `python -m autocue`
**and `autocue.exe`** all match. **A false NEGATIVE is the dangerous direction here** — a missed
server means two writers on the user's library.
*Found while testing:* a Windows cmdline read on a **POSIX** host has no path separators, so `.stem`
returned the whole string. Separators are **normalised** first — the added
`C:\Program Files\AutoCue\autocue.exe` case caught exactly that and now passes.

## Properties kept (both reviewers verified these)
full **7432-7441** range + **process scan** (any `--port`, e.g. `:3004`) · **no false positives**
(`grep serve autocue/cli.py`, `pytest -k serve autocue`, an unrelated `myapp serve`, the CLI's own
pid) · **FAIL-SAFE** (psutil missing / probe raises → **REFUSE**) · **refuses before any backup** —
in fact before the DB is even opened.

## Standalone branch
`fix/loops-single-writer-beatloopsize` was **never pushed** (0 remote refs) and **no PR will be
opened**. Its content now lives here. **BeatLoopSize remains BLOCKED-ON-EVIDENCE and is NOT in this
PR** — the "BEATS confirmed" claim is circular, 0/30,765 real rows carry a non-zero value, and
Rekordbox's own XML doesn't carry the field at all.

## Counts
* **Full suite: 1580 passed · 8 skipped · 0 failed**
* `tests/test_serve_single_writer.py` **20 passed** · `tests/test_loops_db_write_guard.py` **10 passed**
* `git diff origin/main...HEAD` now carries **BOTH** the self-lock / db_path / backup-or-abort /
  exit-code fixes **AND** the serve guard, in **one preflight** (4 files, +656).

PR264-FOLD
>>>>>>> Stashed changes
STATUS: DONE

---

# INC-3 — DB-DIRECT loop write (`--write-db`) · ⚠️ mutates the real Rekordbox DB

TDD, **no-clobber test written FIRST** (it IS the safety case). 2 commits.
Files: `autocue/db_writer.py` (**additive only — 0 deleted lines**), `autocue/cli.py`,
`tests/test_autoloops.py`. **`write_cues_to_db` LEFT UNTOUCHED** (verified: `git diff` shows zero
deletions in db_writer.py) — it stays the shared server path for `/api/apply` + SSE + `memory_cue_mode`.

| Commit | Unit |
|--------|------|
| `70d8a48` | NEW append-only `write_loops_to_db()` + `autocue_serve_is_running()` (db_writer.py) |
| `fb218f3` | `--write-db` CLI branch + full safety contract (cli.py) |

## 1. `write_loops_to_db(content, cues, db, *, dry_run=False) -> int` — append-only
**NO DELETE ANYWHERE ⇒ clobber impossible by construction.** Filters memory loops
(`is_loop and slot == -1`) → queries the existing `Kind=0` `InMsec` set → **SKIPS** any loop whose
start exactly collides (mirror-first: the DJ's entry wins ⇒ also **idempotent**) with a `logger.info`
breadcrumb → INSERTs only survivors: `Kind=0`, `InMsec/InFrame`, `OutMsec=loop_end_ms` (ms),
`OutFrame=round(end*150/1000)`, `OutMpegFrame=OutMpegAbs=0`, **`ActiveLoop=0`** (saved but UNARMED),
`BeatLoopSize=loop_beats` (**BEATS** = bars×4), `Comment=name`, `ID=db.generate_unused_id(DjmdCue)`,
`UUID=uuid4()`; `begin_nested()` savepoint → `sp.commit()` → `db.session.commit()`; on error
rollback + `logger.exception` + **raise**.

## 2. `--write-db` CLI — safety contract (mirrors routes.py:975-997)
Third terminal branch, after the dry-run block, before `--serato`. **Gates on `--loops`** (loops-only;
never writes cues to the DB). Order: **Rekordbox-running → abort** · **NEW `autocue serve` running →
abort** (single-writer: `rekordbox_is_running` does NOT detect the server, which holds its own
read-write handle) · **`backup_database()` BEFORE any write; backup failure ABORTS (nothing written)**
· **PRINT the backup path** (the user's only undo). `--write-db --dry-run` writes **nothing** (the
dry-run block returns first).

## 3. THE NO-CLOBBER PROOF (scratch in-memory SQLite + real pyrekordbox schema — NEVER the live DB)
```
BEFORE — the DJ's 2 hand-placed memory cues  (2 Kind=0 rows)
   cue  In=  5000 Out=    -1 Beats=  0 Active=0 'DJ Memory 1'
   cue  In= 60000 Out=    -1 Beats=  0 Active=0 'DJ Memory 2'

write_loops_to_db -> wrote 2   (3 offered; 'Clash' @5000 collides -> skipped + logged)

AFTER — BOTH DJ memory cues INTACT + 2 new loops coexist  (4 Kind=0 rows)
   cue  In=  5000 Out=    -1 OutFrame=    0 Beats=  0 Active=0 'DJ Memory 1'   ← survived
   LOOP In= 10000 Out= 18000 OutFrame= 2700 Beats= 16 Active=0 'Intro'
   cue  In= 60000 Out=    -1 OutFrame=    0 Beats=  0 Active=0 'DJ Memory 2'   ← survived
   LOOP In= 90000 Out=106000 OutFrame=15900 Beats= 32 Active=0 'Outro'

re-run -> wrote 0  (idempotent; rows unchanged)
```
Units verified: `OutFrame` = 18000×0.15=**2700**, 106000×0.15=**15900**; `BeatLoopSize` = bars×4
(**16**/**32**); `ActiveLoop=0`.

## 4. Tests (all in `tests/test_autoloops.py`; scratch DB only)
- **★ `TestWriteLoopsNoClobber`** — 2 pre-existing memory cues survive **byte-identical**
  (ID/UUID/Kind/InMsec/OutMsec/Comment); hot cue untouched. *(the load-bearing case)*
- `TestWriteLoopsColumns` — every §2 column + unit; only `slot==-1` loops written; `ID` is a real
  int (the `generate_unused_id` stub — else it silently writes `ID=<MagicMock>`).
- `TestWriteLoopsIdempotentAndCollision` — re-run adds **0**; colliding loop skipped **+ logged**;
  dry-run writes nothing; empty is a no-op.
- **`TestMirrorNegativeWhyNotWriteCuesToDb`** — asserts `write_cues_to_db(..., overwrite=True)` **DOES
  delete Kind=0** (both DJ memory cues gone) — pinning *why* we do not reuse it.
- `TestWriteDbCli` (7) — flag gating on `--loops`; abort on Rekordbox; abort on `autocue serve`;
  **backup-failure aborts with NOTHING written**; happy path backs up + prints the path; `--dry-run`
  writes nothing.

## 5. GATE-2 evidence
- **Full suite:** `python -m pytest -q` → **1635 passed, 7 skipped (exit 0)**.
- **`write_cues_to_db` untouched:** 0 deleted lines in db_writer.py; server suites green
  (db_writer + duplicates-integration + serve_routes = 293 passed).
- No test or probe ever touched the live `master.db` (in-memory SQLite only).

## 6. The exact user command (GATE-2 — user runs it)
```bash
# Rekordbox CLOSED, and no `autocue serve` running. Test with ONE track first.
autocue --track "SONG TITLE" --loops --write-db --dry-run   # preview: writes nothing
autocue --track "SONG TITLE" --loops --write-db             # writes; prints the backup path
# → open Rekordbox, confirm named memory loops (Intro/Outro/Break/Build).
# Undo = restore the printed backup from ~/.autocue/backups/master_<TS>.db
autocue --library --loops --write-db                        # then the whole library
```
**Rekordbox-ACCEPTS remains the USER GATE-2 step.** Recommend the user first runs it against a
**COPY** of master.db (`--db-path`) if they want a zero-risk rehearsal.

## 7. Non-goals honoured
- `write_cues_to_db` **not modified**. No web-UI/server loop surface. `ActiveLoop=0` (we write
  *saved* loops, never an armed loop). Loops-only — `--write-db` never writes cues.

P3-AUTOLOOPS-INC3
STATUS: DONE

---

# P4FIX-INC3 — consolidated fix for the DB-direct loop write ⚠️ DANGEROUS SURFACE

TDD, 3 per-logical-unit commits. Files: `autocue/cli.py`, `autocue/db_writer.py`,
`tests/test_autoloops.py`. **No test or probe ever touched the live master.db.**

| Fix | Commit | What |
|---|---|---|
| **F1 + F4 + F5** | `a212aad` | pre-open guards (BL-1 BLOCKER) · correct `db_path` · per-track errors |
| **F2** | `077cdcc` | serve probe by PROCESS + full 7432-7441 range, fail-safe |
| **F3** | `6cf2d8f` | `write_cues_to_db` spares memory LOOPS on the Kind=0 rewrite |

## 🔴 F1 — BL-1 BLOCKER (`--write-db` aborted 3/3 on a real DB)
**Root cause:** `rekordbox_is_running()` probes an **exclusive file lock**. AutoCue had *already*
opened master.db (cli.py) and the analysis queries left SQLAlchemy's autobegin txn holding a SQLite
lock — so the guard **self-detected AutoCue's own handle** and printed a false "Rekordbox is running".
**Fix:** new `_preflight_write_db(args)` runs the `--loops` gate + Rekordbox guard + serve guard
**BEFORE `MasterDatabase(...)` is ever constructed** — which is also the semantically correct place
("Rekordbox must be closed before we even open the DB"). Gated on `args.write_db`; every other CLI
path is unchanged.

### ⚠️ What the ordering test now pins (the anti-mock defence)
**Every unit test MOCKS `rekordbox_is_running` — that is exactly why BL-1 shipped.** A mock can never
reveal a self-lock. So the new test does not assert the guard's *return value*, it asserts the
**call ORDER**, which a mock cannot hide:
`test_rekordbox_guard_runs_BEFORE_the_db_is_opened` → `order.index("rb_guard") < order.index("open_db")`
(and `serve_guard < open_db`, `backup < write`). Any future refactor that moves the guard back after
the DB is opened fails this test immediately — regardless of how the guard is mocked.

**Honest evidence note:** I could **not** reproduce the lock on a plain-SQLite scratch file (a SELECT
there does not take the same lock — the real condition needs SQLCipher/WAL via pyrekordbox). The
authoritative reproduction is the **verifier's real-DB-COPY characterization**. The fix is correct by
construction (a probe on a file we have not opened cannot self-detect) and is pinned by the ordering
test — but the **real proof is the verifier's re-run: `--write-db` must now WRITE, not abort.**

## 🔴 F2 — serve single-writer probe (BL-2 / auditor CRITICAL 95)
Probed **only 7432**, but `serve()` auto-switches to the next 9 free ports and honours `--port` — a
server on 7433-7441 or on `:3004` (what this crew ran) was **invisible**, so the guard silently never
fired. Now: (1) scan the whole **7432-7441** fallback range, (2) scan the **process table** (psutil, a
hard dep) for `autocue serve` / `python -m autocue serve` — catches **any** port. `"serve"` must be its
own argv token, so `autocue --loops --write-db` is not a false positive; the current pid is skipped.
**N2 (fail-open) fixed:** an unresolvable probe now returns **True — refuse the write**, never False.
```
`autocue serve --port 3004` running -> autocue_serve_is_running() = True   (old probe: False ❌)
```

## 🟠 F3 — `write_cues_to_db` spares memory LOOPS (auditor IMPORTANT 88) — SHARED PATH
The blanket `Kind=0` delete destroyed **both** our `--write-db` loops **and the DJ's hand-placed memory
LOOPS**, silently, on every `overwrite=True` apply (`/api/apply`, SSE, CLI `--overwrite`). Memory cues
and memory loops share `Kind=0`; the only discriminator is `OutMsec`. The rewrite now deletes
**point cues only** (`OutMsec <= InMsec`) and spares loops (NULL `OutMsec` → spared, safe direction).
memory_cue_mode semantics intact; hot cues still deleted slot-wise.
**Shared server suites GREEN at exactly 293** (db_writer + duplicates-integration + serve_routes);
verifier golden 21/21 (its DB-5 seeds point cues, still deleted).

## 🟠 F4 — one path for guard + backup + write (auditor 85)
`db_path` was reconstructed as `_db_dir/"master.db"` — **not** the file opened under `--db-path`, so the
**backup targeted the wrong file**, voiding the printed "your ONLY undo" promise. Now
`db_path = Path(args.db_path) if args.db_path else _default_db_path()`; guard, backup and write all
target the **same** file (pinned by `test_guard_and_backup_target_the_db_path_flag`).

## 🟡 F5 — per-track exception handling (auditor N1)
The CLI write loop now catches per track: names the failing track, keeps going, and reprints the
backup path — instead of dumping a raw traceback over already-committed tracks.

## GATE-2 evidence
- **Full suite:** `python -m pytest -q` → **1668 passed, 7 skipped (exit 0)**, 0 failures.
- **Shared path (F3) safe:** server suites **293 passed** (unchanged count); verifier golden **21/21**.
- New tests: ordering pin · db-path targeting · per-track failure · 7 serve-probe cases (fallback
  port, arbitrary `--port` via process, `python -m`, no-false-positive, fail-safe, full range scan) ·
  2 loop-sparing cases.

## ⚠️ NEW finding for the coordinator (found while doing F3 — NOT fixed, out of the given scope)
`has_existing_memory_cues()` counts **all** `Kind=0` rows, including the loops `--write-db` writes. Its
only caller is the `write_memory` gate (`db_writer.py:677`):
`write_memory = bool(mem_cues) and (overwrite or has_existing_memory_cues(...) == 0)`.
⇒ **After a user runs `--write-db`, a later non-overwrite apply silently stops writing their memory
CUES** (our loops make the count non-zero). The gate was designed to protect memory *cues*, so counting
*loops* is semantic drift introduced by INC-3. One-line fix, consistent with F3's discriminator: count
point cues only (`OutMsec <= InMsec`). I did **not** apply it — it is a further semantics change to the
shared server path that neither the auditor nor this task asked for, and I will not widen shared-path
changes unilaterally on a destructive surface. **Coordinator's call.**

## Still NOT claimed
- The real-DB proof is the **verifier's** re-run on a COPY: `--write-db` must now actually WRITE
  (not abort), and the serve guard must fire for a serve on a NON-default port. Rekordbox-accepts
  remains the USER GATE-2 step.

P4FIX-AUTOLOOPS-INC3
STATUS: DONE

---

# P5FIX-INC3 — final cleanup batch (all 5 items)

TDD, 4 per-logical-unit commits. Files: `autocue/db_writer.py`, `autocue/cli.py`,
`tests/test_autoloops.py`. **The append-only `write_loops_to_db` spine is byte-identical** (verified
by diffing the function across the batch); Serato/XML paths untouched. Live master.db never touched.

| # | Commit | Fix |
|---|---|---|
| **1 + 5** | `6acc573` | `has_existing_memory_cues()` counts POINT CUES only + one shared NULL-consistent predicate |
| **2** | `ea188d4` | `--write-db` exits **1** on a partial write |
| **3** | `79b493b` | serve probed BEFORE the lock probe → correct attribution |
| **4** | `7d8683d` | serve process scan tightened (no grep/pytest false positives) |

## 🟠 #1 — the COUNT half of the Kind=0 conflation (IMPORTANT 88)
`has_existing_memory_cues()` counted memory **LOOPS** as memory cues. It gates `write_memory`
(`overwrite or has_existing_memory_cues(...) == 0`), so once `--write-db` had added our `Kind=0`
loops the count was non-zero and a later **default (overwrite=False) apply silently stopped writing
the user's memory CUES** — a regression *introduced by INC-3*.
**Fix:** both halves now share ONE predicate — `_point_cue_filter()` = `OutMsec IS NULL OR OutMsec <=
InMsec` — used by the COUNT **and** F3's DELETE, so they can never drift apart again.
**This folds in #5 (N3):** NULL `OutMsec` is now treated as a point cue in both, matching
`read_hot_cues` (NULL → -1). (Unreachable — `OutMsec` is NOT NULL — but consistent.)
**Test:** a track whose only `Kind=0` row is an INC-3 loop still gets its memory cue written on a
non-overwrite apply (RED before: `assert 0 == 1` — the cue was silently dropped).
**Fixture blind spot fixed too:** `has_existing_*` call `db.query` (not `db.session.query`), so the
MagicMock made `.count() == 0` silently False and the gate untestable — that is *why* this shipped.
The scratch fixture now wires `db.query` to the real session.

## 🟡 #2 — partial write must not look like success (N1)
F5 reported per-track failures loudly but still **exited 0**, so a script saw SUCCESS after a partial
DB write (earlier tracks already committed). Now `sys.exit(1)` when any track failed; a fully
successful write still exits 0. Both exit codes pinned by tests.

## 🟡 #3 — blame the server, not Rekordbox (verifier LOW nit)
A running `autocue serve` holds master.db, so it **also** trips the file-lock probe inside
`rekordbox_is_running()`. Probing Rekordbox first made the CLI say *"Rekordbox is running"* when the
real culprit was our own server — the write was correctly refused, only the message lied. The serve
probe now runs **first** and short-circuits (the generic lock probe is never even reached); its text
is also softened to "the database is locked by another process (Rekordbox is running, or another app
holds master.db open)".

## 🟡 #4 — serve scan false positives (N2)
The scan matched any process with a standalone `serve` token AND `autocue` as a substring anywhere —
so `grep serve autocue/cli.py` or `pytest -k serve autocue` made `--write-db` **refuse**. New
`_is_serve_cmdline()`: the token must be exactly `serve` **and the token before it must end with
`autocue`** (`autocue serve`, `/usr/local/bin/autocue serve`, `python -m autocue serve`).
**The fail-SAFE direction is untouched** — an unresolvable probe still returns True (refuse). 4
parametrized false-positive cases + 4 real-invocation cases.

*(N4 skipped as instructed — `uvicorn autocue.serve.app:…` is not a documented way to run the server.)*

## GATE-2 evidence
- **Full suite:** `python -m pytest -q` → **1681 passed, 7 skipped (exit 0)**, 0 failures.
- **Server suites (the blast radius for #1/#5):** `tests/test_serve*.py` → **234 passed** (unchanged).
- **Spine intact:** `write_loops_to_db` byte-identical across the batch; only `cli.py` +
  `db_writer.py` touched; `serato_writer.py` / `writer.py` untouched.

P5FIX-AUTOLOOPS-INC3
STATUS: DONE

---

# P6-DOCS — ⚠️ PARTIALLY BLOCKED. The task premise is stale; I applied only what is TRUE.

**Commit `277cf8d` (docs only, no source, suite not re-run — no behaviour change).**

## Why most of this task could not be executed honestly
The task said "apply the verbatim edit text from **P6-v2**". **P6-v2 contains no such edit text — it is
a GATE-0 STOP.** It says, verbatim: *"Every docs edit from my P6 pass — and everything this P6-v2 task
asked for — must NOT be applied."* I verified the researcher's claim myself rather than trust it:

```
behind origin/main: 12   ahead: 23
origin/main ALREADY SHIPS:  cli.py --loops (:86) · autocue/analysis/loops.py ·
                            db_writer.read_loops() (:147) · write_memory_loops() (:481)
docs/FEATURES.md on main:   "### Loop generation (--loops)"  (line 675)  ← we don't have it (stale base)
```
So the four remaining doc homes would each document a surface that **collides with shipped `main`**:

| Target | Verdict | Why |
|---|---|---|
| CLAUDE.md dev-commands `--loops`/`--write-db` | ❌ **NOT APPLIED** | main's `--loops` has *different semantics* (seam-validated mix-in/mix-out, max 2, librosa). Documenting ours would become a lie on merge. |
| docs/FEATURES.md auto-loops entry | ❌ **NOT APPLIED** | main already has `### Loop generation (--loops)`. Ours would duplicate/contradict it. |
| architecture.md module map | ❌ **NOT APPLIED** | names `analyzer.plan_loops` / our `serato_writer` LOOP branch — the Serato LOOP work is a **duplicate of main's** and may not survive the re-scope. |
| analysis-and-testing.md loop policy | ❌ **NOT APPLIED** | the policy itself is an open **product decision** (ours vs main's seam-validated) pending the human. |

Documenting any of these now = documenting a lie. The task itself said: *"do not document a lie."*

## ✅ What I DID apply (the one edit that is true regardless of the re-scope)
Both homes get the **`Kind=0` invariant** — correct against our branch **and** against `main`, and it
documents a footgun that is **LIVE ON MAIN TODAY**:

1. **`.claude/project/db-constraints.md`** — new bullet after the memory-cue entry:
   - `Kind=0` is **shared** by memory CUES and memory LOOPS; discriminator = `OutMsec`
     (`-1` = point cue, `> InMsec` = loop).
   - **Never blanket-DELETE `Kind=0`** — a rewrite meant for cues destroys hand-placed **loops**, and
     vice-versa. Both `write_cues_to_db(overwrite=True)` **and main's `write_memory_loops(overwrite=True)`**
     do exactly this. Delete point cues only (`OutMsec IS NULL OR OutMsec <= InMsec`).
   - **Never blanket-COUNT `Kind=0`** to gate a write — it silently skips.
   - Loop writes must be **append-only** (insert non-colliding rows, never delete) — idempotent and
     clobber-proof by construction.
   - DB-write tests: **scratch in-memory SQLite only, never the live `master.db`**; stub
     `generate_unused_id` and wire `db.query`, or a MagicMock yields false greens.
2. **`CLAUDE.md`** must-know bullet — the same invariant in one line, pointing at db-constraints.md.

## 🔴 Verified while writing this (for the coordinator/human)
`origin/main:db_writer.py:481-518` `write_memory_loops()` docstring claims *"manually placed memory
data is never destroyed silently"* — **that is false.** With `overwrite=True` it blanket-deletes every
`Kind=0` row, so **`autocue --library --loops --overwrite` on `main` today wipes every hand-placed
memory CUE in the library.** Without `--overwrite` it silently skips any track that has a memory cue.
Our append-only `write_loops_to_db()` is precisely the fix. **This is the highest-value thing on this
branch and it is a bug fix against main, not a new feature.**

## Also stale (source — NOT touched, per "no source changes")
Our own `cli.py` `--loops` argparse help still says *"Currently written with --serato as Serato LOOP
tags"* — untrue since the XML + DB paths shipped. Needs a one-line fix in the re-scoped increment.

## Recommendation
**Rebase onto `origin/main` and re-enter GATE-1** on the real gap (per researcher's table: KEEP the
append-only no-clobber write + XML loop marks + the serve/backup hardening; DROP the duplicate Serato
LOOP + `read_hot_cues` OutMsec work; the loop *policy* needs a human call). Docs for the feature
surface can only be written after that lands.

## P6-DOCS-FIX — I documented a lie. Corrected. (commit `2245f3a`, docs only)

My own P6 docs commit (`277cf8d`) contained **two** false claims. Caught by the coordinator; verified
against source and fixed. No source change, no suite re-run.

### What was wrong
1. **Hallucinated symbols.** I named **`write_memory_loops(overwrite=True)`** and **`read_loops()`** as
   if they were ours. `grep -rn write_memory_loops autocue/` → **nothing**. Both exist **only on
   `origin/main`** (`db_writer.py:481` / `:147`) — I had read them there while verifying the GATE-0
   rebase claim and carried them into docs that live in *this* tree, where a reader greps and finds
   nothing. **Both references removed.**
2. **Worse — stale pre-fix state.** I wrote that the blanket-`DELETE` and blanket-`COUNT` footguns are
   *current* behaviour. **They are not: I fixed both myself** (F3 → the DELETE; P5-FIX #1 → the COUNT).
   The doc described a codebase that no longer exists.

### What each claim now says (all source-verified)
| Claim | Source proof |
|---|---|
| `Kind=0` shared by memory CUES + LOOPS; discriminator `OutMsec` (**KEPT** — correct & important) | the same test `read_hot_cues` uses, `db_writer.py:174` |
| The blanket-DELETE hazard applied to **`write_cues_to_db`'s memory rewrite ONLY** — and it now deletes **point cues only**, sparing loops | `db_writer.py:685`, via `_point_cue_filter()` |
| The blanket-COUNT half was **`has_existing_memory_cues()`** — now counts **point cues only** | `db_writer.py:154`, same predicate |
| COUNT and DELETE share **one** predicate so they cannot drift apart | `_point_cue_filter()` `db_writer.py:138` |
| **`write_loops_to_db()` is the append-only reference impl** — **no `overwrite` param, ZERO `DELETE`s**, idempotent; never reuse `write_cues_to_db` for loops | `db_writer.py:229`; grep: `0` `.delete(` calls in the function body |

### Grep proof — every symbol named in both docs exists in source
```
write_memory_loops / read_loops  →  REMOVED (0 refs in CLAUDE.md + db-constraints.md) ✓
read_hot_cues ✓ · _point_cue_filter ✓ · has_existing_memory_cues ✓ · write_cues_to_db ✓
write_loops_to_db ✓ · generate_unused_id ✓ · DjmdCue ✓   (all present in autocue/)
line numbers: 138 _point_cue_filter · 154 has_existing_memory_cues · 174 read_hot_cues
              229 write_loops_to_db · 685 write_cues_to_db      (all confirmed by sed)
write_loops_to_db: no `overwrite` param ✓ · 0 `.delete(` calls ✓
```

### Lesson
The GATE-0 verification (reading `origin/main`'s source to confirm the rebase blocker) leaked
main-only symbols into docs describing **our** tree. **Rule: every symbol named in a doc must be
grepped in the tree that doc ships with — reading another ref's source is exactly how a plausible,
non-existent function name gets written down.** And: after fixing a bug, re-read the docs describing
it; I documented the footgun I had already fixed.

*(The origin/main `write_memory_loops` blanket-`Kind=0`-delete data-loss bug is still real and still
worth fixing — but it belongs in the BOARD/handoff to the coordinator, not in this tree's
db-constraints.md, until the rebase lands.)*

P6DOCSFIX-AUTOLOOPS
STATUS: DONE

---

P6DOCS-AUTOLOOPS
STATUS: BLOCKED — 4 of 5 doc homes un-writable (they would document a surface that collides with the
auto-loops feature ALREADY MERGED to origin/main; we are 12 behind). Applied the ONE universally-true
edit: the Kind=0 memory-cue/memory-loop invariant (CLAUDE.md + db-constraints.md, commit 277cf8d).
Needs: rebase + GATE-1 re-scope before the rest can be written.

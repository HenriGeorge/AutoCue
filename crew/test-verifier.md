# Test-verifier result — AUTOLOOPS · INCREMENT 1 (Serato-first)

## P3 AUTHOR — `tests/test_autoloops_golden.py` (DISJOINT #99, verifier-owned)

**AUTHORED, NOT RUN** (per task: implementer INC-1 Serato commit not landed; the
suite runs at P4). Parse-checked only: `python -m py_compile` OK, `pytest
--collect-only` → **20 tests collected**, imports resolve. `git diff --name-only`
touches only `crew/*` + the ONE new file — **zero overlap** with the implementer's
`tests/test_autoloops.py` / `test_serato_writer.py` / `test_db_writer.py`.

### Why most of these FAIL today (by design)
Git shows only the keystone (`784cce4`) + policy (`e075bce`) committed. The Serato
**LOOP encode/decode + `preserve=` + `--loops` CLI are NOT committed yet**, so every
LOOP/CLI case fails until INC-1's Serato commit lands. The three **regression** cases
(R-1..R-3) pin *today's* CUE bytes and should pass immediately — they are the anchor
that INC-1 must not disturb.

### Spec inventory (case IDs → test)

| Test class · method | Case | Asserts |
|---|---|---|
| `TestRegressionCuePathUnchanged.test_single_cue_golden_byte_identical` | **R-1** | `build_markers2([cue])` == the shipped `GOLDEN_PAYLOAD`, byte-exact |
| …`.test_non_loop_payload_carries_no_loop_framing` | **R-2** | an 8-cue payload has **0** `LOOP` framing bytes (non-loop path unchanged) |
| …`.test_eight_cue_roundtrip_unchanged` | **R-3** | 8-cue round-trip still → 8 CUE / 0 LOOP, slots 0..7 |
| `TestCueEntriesUntouchedByLoops.test_cue_entry_bytes_appear_verbatim_in_mixed_payload` | **E-8** | the CUE entry slice appears **byte-for-byte** inside a `[cue, loop]` payload |
| …`.test_cue_fields_survive_alongside_loops` | **E-8** | CUE index/pos/name decode unchanged with a loop riding alongside |
| `TestLoopSurvivesRewrite.test_loop_is_not_dropped_on_decode` | **D-4** | parse `[1 CUE + 1 LOOP]` keeps BOTH — loop count never collapses 1→0 |
| …`.test_composed_payload_survives_parse_then_rebuild` | **D-4** | parse→`build_markers2(preserve=[raw])`→ LOOP bytes identical, still present (**hex-free**) |
| …`.test_foreign_dj_loop_survives_a_fresh_autocue_write` | **D-5** | a DJ's custom-named, off-policy-length loop survives a fresh cue-only write |
| `TestLoopPayloadQuirks.test_no_base64_padding_char_with_loops` | **Q-1** | no `=` in `wrap_outer` **or** `build_envelope` of a LOOP payload |
| …`.test_outer_padded_to_min_470_with_loops` | **Q-3** | LOOP-containing outer NUL-padded ≥470 B |
| …`.test_legacy_markers_tag_deleted_on_loop_write` | **Q-2** | writing a loop tag deletes the legacy `Serato Markers_` (mutagen) |
| `TestPolicyToSeratoIntegration.test_qualifying_phrases_become_loop_entries` | STATES | `plan_loops` Intro+Outro → 2 LOOP entries named Intro/Outro (policy→encode seam) |
| …`.test_no_qualifying_phrase_yields_no_loop_entries` | STATES · *No eligible phrase* | VERSE/CHORUS only → 0 loops → payload cues-only |
| `TestNoBeatGridBreadcrumb.test_unusable_beat_grid_logs_breadcrumb_and_returns_empty` | **P-10** (silent-failure) | unusable grid → `[]` **and a WARNING logged** |
| …`.test_no_eligible_phrase_path_is_silent` | **P-10** | valid grid + no loopable phrase → `[]` **without** a warning (distinguishable) |
| `TestCliLoopsGate.test_loops_flag_is_registered` | **C-1/C-2** | `--loops` shows in `--help` |
| …`.test_without_loops_no_loop_cues_exported` | **C-2** (neg) | no `--loops` ⇒ zero loop CuePoints reach `write_serato` |
| …`.test_with_loops_exports_loop_cues` | **C-2/C-4** | `--loops` ⇒ ≥1 loop CuePoint in the Serato export (best-effort seam) |
| …`.test_dry_run_writes_nothing` | **C-3** | `--loops --serato --dry-run` prints "Dry run — no files written." and never calls `write_serato` |
| …`.test_loops_without_serato_does_not_crash` | **C-5** | `--loops` with no `--serato` → XML path, no crash |

### R-NC3 honoured (Serato-acceptance is NOT claimed by any green here)
Module docstring states plainly: these assert **AutoCue's own encoding** as a
regression anchor; Serato-acceptance of the reserved/colour bytes `0x0a–0x12` is
proven **only** by the GATE-2 user Serato-verify. The D-4/D-5 survival tests are
**hex-free** parse→rebuild round-trips (bytes from our encoder on both sides), so a
later one-pass byte fix cannot make them lie.

### Contract note (P4 reconciliation)
Golden matches the **implementer's actual draft contract** in `tests/test_autoloops.py`
— LOOP parse-dict keys `start_ms`/`end_ms`/`locked`/`raw`, and
`build_markers2(cues, preserve=[raw])` — **not** the coverage-map prose
(`position_ms`/`loop_end_ms`). If the committed contract drifts, reconcile the key
names + the `analyze_loops` CLI monkeypatch target at P4 (the CLI wiring seam is the
most coupled part; the payload survival/regression cases are robust).

---

## DESIGN.md parity notes surfaced during authoring (for the coordinator)

- **⚠ R-NC8 MISMATCH (policy — implementer-owned, flag to coordinator).** DESIGN §"P2
  refinements · R-NC8" resolves **cap = 4** with **Build (UP) eligible by default at
  lowest priority, opt-flag dropped**. The **committed** `plan_loops` still gates Build
  behind `include_build=False` (`analyzer.py:33,44,72-74`) and the implementer's unit
  `test_cap_three_by_default_four_with_build` asserts the **old** "cap 3 default / 4
  with build". So today: Build excluded by default → effective cap 3, and `--loops`
  has no path to set `include_build`. This is a DESIGN↔code parity **MISMATCH**. Not
  mine to fix (policy lives in the implementer's disjoint file); recorded here so the
  VERIFY loop reconciles it. My golden file deliberately does **not** assert the cap
  number (in flux) to avoid a brittle competing policy test.
- **NC-4 (`--loops` × mirror-first) still flagged NEEDS-USER-THUMBS-UP** in DESIGN
  (default = LAYER). My C-2/C-4 CLI cases assume loops layer onto the export; if the
  user picks strict-mirror, `test_with_loops_exports_loop_cues` re-scopes at P4.

---

## GATE-2 REAL-ARTIFACT RUNBOOK (green tests ≠ Serato accepts it — F7)

Automated proof is a self-consistent writer↔parser round-trip. **True proof needs
Serato DJ Pro.** Run these at P4 once INC-1's Serato commit is in; **SHOW the evidence**
(CLI stdout + hex/parser read-back + the user's Serato screenshot).

**Pre-req:** `pip install -e ".[serato]"` (mutagen). Pick a real track TITLE that has
phrase/beat-grid data. **Never touch the user's real files** — copy first.

### 1. Dry-run (writes nothing; lists placements incl. loops)
```bash
autocue --track "SONG TITLE" --loops --serato --dry-run
```
Expect: the placement summary lists loop rows (Intro/Outro/Break…) and the final line
`Dry run — no files written.` Filesystem untouched. (C-3)

### 2. Real write to a THROWAWAY COPY (never the user's library file)
```bash
mkdir -p /tmp/autoloops-gate2
cp "/path/to/the/real/track.mp3" /tmp/autoloops-gate2/track.mp3   # throwaway copy
# Point AutoCue at the copy (or run --track and confirm the resolved path is the copy).
autocue --track "SONG TITLE" --loops --serato          # Rekordbox AND Serato must be CLOSED
```
Expect the summary line: `Serato export: N written · …`.

### 3. Read the tag back with OUR extended parser (round-trip proof)
```bash
python - <<'PY'
from pathlib import Path
from autocue.serato_writer import _read_existing, parse_markers2, GEOB_V2
raw = _read_existing(Path("/tmp/autoloops-gate2/track.mp3"))[GEOB_V2]
for e in parse_markers2(raw):
    print(e["type"], {k: e.get(k) for k in ("index", "start_ms", "end_ms", "name")})
PY
```
Expect ≥1 line with `LOOP` and the expected `start_ms`/`end_ms`/`name`. Capture this
stdout as evidence.

### 4. HAND TO USER — the only proof that counts (F2/F7, Decision 3b)
> Open `/tmp/autoloops-gate2/track.mp3` in **Serato DJ Pro** (Files panel →
> "Rescan ID3 Tags" if the track was already in the library). Confirm the **named
> loops render at the right positions** (e.g. an "Outro" loop over the mix-out) and
> are named. **Screenshot it.** If any loop is missing/mis-coloured/mis-placed → the
> reserved/colour bytes need a one-pass fix (§DESIGN F2); the survival tests stay
> valid regardless.

### 5. INC-2 only — XML import to Rekordbox (named memory loops)
```bash
autocue --track "SONG TITLE" --loops --output /tmp/autoloops-gate2/autocue_import.xml
```
> User: Rekordbox → File → Import Library → select the XML → confirm the tracks show
> **named memory loops** (Intro/Outro/Break) at the right positions. Screenshot.
(INC-2 = `writer.py add_mark(Type="loop", End=…)`, not in this cut.)

---

STATUS: DONE
<!-- P3-AUTOLOOPS-INC1-SPECS -->

---

# P4 — GATE-2 VERIFY (INC-1 committed `81f4963..eca754c`, HEAD `eca754c`)

RUN → READ → SHOW, all this session. INC-1 landed as 5 commits (keystone · policy ·
Serato LOOP write/decode/preserve · read_hot_cues OutMsec · `--loops` CLI). Committed
contract matches my golden's assumptions — `build_markers2(cues, *, preserve=())`,
parse LOOP keys `start_ms`/`end_ms`/`locked`/`raw` — so **no contract-key drift**; the
only golden edit was fixing a spy double-wrap in my own CLI harness.

## 1 · STATIC — full suite (fresh)
```
$ python -m pytest -q
1 failed, 1600 passed, 7 skipped, 4 warnings in 32.13s        (exit 1)
```
The single failure is my P-10 breadcrumb guard (a real code gap, below). All 1600
others green — the implementer's full autoloops unit suite + every pre-existing test.

## 2 · GOLDEN — `tests/test_autoloops_golden.py` (19/20)
```
$ python -m pytest -q tests/test_autoloops_golden.py
1 failed, 19 passed in 0.52s
```
Reconciliation done: my `_spy` recorded pairs one level too deep → fixed
(`spy.extend(list(cs) for _,cs in pairs)`); the CLI itself was correct all along
(captured stdout: `+ 1 loop(s): Outro` · `Serato export: 1 written`).

| Class | Result |
|---|---|
| `TestRegressionCuePathUnchanged` (R-1..3) | ✅ 3/3 — CUE golden byte-identical, non-loop payload loop-free |
| `TestCueEntriesUntouchedByLoops` (E-8) | ✅ 2/2 — CUE bytes verbatim inside a mixed payload |
| `TestLoopSurvivesRewrite` (D-4/D-5) | ✅ 3/3 — **F1**: loop never 1→0, foreign DJ loop preserved |
| `TestLoopPayloadQuirks` (Q-1..3) | ✅ 3/3 — no `=`, ≥470 pad, legacy Markers_ deleted |
| `TestPolicyToSeratoIntegration` | ✅ 2/2 — policy→encode seam |
| `TestCliLoopsGate` (C-2/C-3/C-4/C-5) | ✅ 5/5 — flag registered, gate, dry-run, real-write, no-serato |
| `TestNoBeatGridBreadcrumb` (P-10) | 🔴 1/2 — **no-grid path logs no breadcrumb** (real gap) |

(Cap number NOT asserted — R-NC8 in flux, coordinator reconciling.)

## 3 · REAL-ARTIFACT — dry-run + policy sanity on real tracks (READ-ONLY :3004/DB)
Track picked by querying the running server `GET /api/tracks` (read-only; 2918 `file`
tracks). Drove by `--track-id` (server masks text fields; `analyze_by_id` reads the
decrypted DB directly).

**Dry-run (writes nothing):**
```
$ python -m autocue --track-id 108962923 --loops --serato --dry-run
  … [phrase]  8 phrase cues (Intro/Verse/Chorus…)
Dry run — no files written.
```
✅ dry-run safety confirmed. ⚠ **FINDING (C-3):** the dry-run prints **no loop
placements** — loops are computed only inside the real `--serato` write branch, which
sits *after* the `dry-run` early-return (`cli.py:201-203` before `:232`). test-designer
C-3 expected "prints loop placements". Gap, not a data bug.

**Policy sanity — real Intro/Break at real ms (`analyze_loops` direct):**
```
[track 136122394] bpm≈133 dur≈224s → 2 loops:
   Intro  00:00.44 → 00:07.60  (4 bars)  slot=-1 is_loop=True
   Break  01:11.64 → 01:25.97  (8 bars)  slot=-1 is_loop=True
[track 114993188] bpm≈120 → 1 loop:  Intro 00:00.02→00:08.00 (4 bars)
[track 108962923] 8×Verse/Chorus cues → 0 loops   (G1: never loop vocals/arrangement)
[track 241612968] → 0 loops
```
✅ policy MATCHES DESIGN §2: Intro/Break at phrase downbeats, power-of-2 bars (16 beats
=4 bars / 32 beats =8 bars), Break capped at 8, Verse/Chorus never looped, memory slot=-1.

## 4 · SERATO FILE FOR THE USER (F2 byte-proof — the long pole)
Copied a **real** local MP3 into a throwaway dir (original untouched), wrote 3 named
loops + 1 hot cue via `write_serato_tags`, read straight back with `parse_markers2`:
```
COPIED src=…/Michael Jackson - Beat It - 5 - bass.mp3  →  dst below (3.9 MB)
READ-BACK — 4 entries decoded:
  CUE   idx=1  pos_ms= 30000  name='Drop'
  LOOP  idx=0  start_ms=     0  end_ms=  8000  locked=False  name='Intro'
  LOOP  idx=1  start_ms= 60000  end_ms= 68000  locked=False  name='Break'
  LOOP  idx=2  start_ms=262000  end_ms=270000  locked=False  name='Outro'
```
✅ writer emits valid LOOP entries · parser decodes them · CUE+LOOP coexist · loop index
independent of cue slot · every `end_ms > start_ms`.

**F1 on the real file** — rewrote passing ONLY a hot cue; the DJ's loops survived:
```
BEFORE: 3 loops [Break,Intro,Outro]
AFTER (only a cue passed): 3 loops [Break,Intro,Outro]  +  cue [MixIn]
✅ loop count 3→3 (never 3→0) — mirror-first preserve works end-to-end.
```

### 📂 HAND-TO-USER (F7 — the only proof of Serato-ACCEPTS)
> **File:** `/tmp/autoloops-gate2/beat-it-bass__autoloops-demo.mp3`
> Open it in **Serato DJ Pro** (Files panel → right-click → **Rescan ID3 Tags**) and
> confirm the **named loops render** — *Intro* @0:00–0:08, *Break* @1:00–1:08,
> *Outro* @4:22–4:30 — plus a *Drop* hot cue @0:30. **Screenshot it.** If any loop is
> missing / mis-placed / mis-coloured, the reserved/colour bytes `0x0a–0x12` need the
> one-pass fix (DESIGN F2); the F1 survival + round-trip proofs stay valid regardless.

## 5 · GATE-2 VERDICT — per DESIGN acceptance

| DESIGN item | Verdict | Evidence |
|---|---|---|
| **F1** loop-preserve (highest sev) | ✅ **MATCH** | golden D-4/D-5 green + real-file 3→3 survive |
| **F2** byte-lock — writer emits valid LOOP | ✅ **MATCH (writer)** | round-trip decode of Intro/Break/Outro |
| **F2/F7** Serato **ACCEPTS** the bytes | ⏳ **PENDING USER** | file handed off; screenshot required |
| Round-trip (writer↔parser) | ✅ **MATCH** | §2 + §4 |
| Regression — CUE path unchanged | ✅ **MATCH** | R-1..3 green, 1600 pass |
| Policy §2 (labels/power-of-2/≥4 bars/clamp) | ✅ **MATCH** | real-track Intro/Break §3 |
| **F3 silent-failure breadcrumb** | 🔴 **MISMATCH** | P-10: no-grid path logs nothing |
| Dry-run previews loops (C-3) | ⚠ **PARTIAL** | writes nothing ✓ · previews loops ✗ |

**Verdict: BLOCKED on 1 RED + 1 finding** (core feature GREEN & PROVEN; Serato-ACCEPTS
is a separate expected human gate, F7). Route to implementer:

1. 🔴 **P-10 (must-fix, ~3 lines).** `autocue/analyzer.py` has **no `import logging`
   at all**; `analyze_loops` returns `[]` on an unusable/absent beat grid (`:360`,
   `:372`) and `_get_anlz_tags_resilient` "silently skips" unparseable tags — a genuine
   grid/parse failure is indistinguishable from "no eligible phrase". DESIGN §VERIFY
   requires a breadcrumb. Fix: add a module logger + `logger.warning("track %s: no
   usable beat grid — skipping loops", …)` on those returns. Then P-10 greens (full
   suite → 1601/0).
2. ⚠ **C-3 (should-fix).** Compute+print loop placements in the dry-run path (before
   `cli.py:201`) so `--loops --serato --dry-run` previews Intro/Break/Outro.

*(R-NC8 cap/Build parity mismatch remains flagged from P3 — coordinator owns that
reconciliation; not re-blocked here.)*

STATUS: BLOCKED — P-10 no-grid breadcrumb (DESIGN §VERIFY silent-failure), + C-3 dry-run loop-preview finding; all else GREEN + evidence shown; Serato-ACCEPTS pending user screenshot (F7)
<!-- P4-AUTOLOOPS-INC1 -->

---

# P4-RE — GATE-2 RE-VERIFY after the fix batch (7 commits, HEAD `5d51872`)

Both P4 reds fixed + bonus (INC-2 XML loop marks, R-NC8 Build-default, N1/N2 breadcrumbs,
terminal-Outro fix). RUN→READ→SHOW fresh this session.

## 1 · STATIC — full suite (fresh) → GREEN
```
$ python -m pytest -q
1614 passed, 7 skipped, 4 warnings in 29.89s        (exit 0)
```
Was 1600p/1F at P4 → now **1614p / 0F** (+14 from the fix commits, P-10 now green).

## 2 · GOLDEN — `tests/test_autoloops_golden.py` → 20/20
```
$ python -m pytest -q tests/test_autoloops_golden.py
20 passed in 0.46s        (exit 0)
```
P-10 breadcrumb + the 2 CLI-gate cases all green. `logger.warning("track %s: no usable
beat grid — skipping loops")` added at `analyzer.py:370,383` → the silent-failure path
now logs (message contains "grid" → P-10 asserts pass); no-eligible-phrase stays silent.

## 3 · FIX-1 FUNCTIONAL — the OUTRO loop now FIRES (was never emitted before)
`analyze_loops` on real tracks (READ-ONLY DB). The terminal-phrase fix (`cb991ac`: the
last phrase gets its bar length from track end) + R-NC8 (`8216fc9`: Build default-on):
```
[136122394] bpm≈133 dur≈224s  was [Intro,Break]  →  [Intro, Build, Break, Outro]  ← OUTRO + BUILD fire
        Outro 03:29.44 → 03:43.77  (8 bars)   end ≤ dur ✓
[114993188]  was [Intro]        →  [Intro, Outro]              ← OUTRO fires
[241612968]  was [] (0 loops)   →  [Outro]                     ← OUTRO fires
[85904626]   → [Outro]  (16 bars — Outro cap)     [92168888] → [Intro, Outro]
[108962923]  8×Verse/Chorus     →  []  (still 0 — G1 correct)
```
✅ **Outro observed on 5 tracks**; every Outro end ≤ track duration (clamp holds); Build
now eligible by default (cap = 4, priority Intro>Outro>Break>Build) — R-NC8 RESOLVED.

## 4 · FIX-4 — `--loops --serato --dry-run` now PREVIEWS loops + writes nothing
```
$ python -m autocue --track-id 136122394 --loops --serato --dry-run
  …: loop [Intro] 00:00–00:07 (4 bars)
  …: loop [Build] 00:07–00:14 (4 bars)
  …: loop [Break] 01:11–01:25 (8 bars)
  …: loop [Outro] 03:29–03:43 (8 bars)

Dry run — no files written.
```
✅ all 4 placements previewed AND filesystem untouched. C-3 gap closed.

## 5 · GATE-2 VERDICT — RE-VERIFY

| DESIGN item | P4 | P4-RE |
|---|---|---|
| F1 loop-preserve (highest sev) | ✅ MATCH | ✅ MATCH (unchanged) |
| F2 byte-lock — writer emits valid LOOP + round-trip | ✅ MATCH | ✅ MATCH |
| Regression — CUE path / full suite | ✅ 1600p | ✅ **1614p / 0F** |
| Policy §2 — Intro/Outro/Break/**Build**, power-of-2, ≥4 bars, clamp, cap 4 | ⚠ Outro/Build absent | ✅ **MATCH** (5 tracks fire Outro; Build default) |
| **F3 silent-failure breadcrumb** (P-10) | 🔴 MISMATCH | ✅ **MATCH** (logs "no usable beat grid") |
| Dry-run previews loops (C-3) | ⚠ PARTIAL | ✅ **MATCH** |
| R-NC8 cap/Build parity | ⚠ flagged | ✅ **RESOLVED** (Build default, cap 4) |
| **F2/F7 Serato ACCEPTS the bytes** | ⏳ pending | ⏳ **PENDING USER** (expected human gate) |

**Verdict: GATE-2 GREEN** — every automated acceptance criterion passes (tests + policy
parity: Intro/Build/Break/Outro fire, Verse/Chorus → 0, silent-failure logs, dry-run
previews). The **only** open item is the expected human gate **F7 (Serato ACCEPTS)**:
> 📂 open `/tmp/autoloops-gate2/beat-it-bass__autoloops-demo.mp3` in **Serato DJ Pro**
> (Files panel → Rescan ID3 Tags), confirm the named loops render, **screenshot it**.
This is a human confirmation, not a code red — GATE-2 is otherwise fully passed.

STATUS: DONE — GATE-2 GREEN (pytest 1614p/7s/0f, golden 20/20, Outro+Build fire on real tracks, dry-run previews loops, no-grid breadcrumb logs); only the expected human F7 Serato-accepts screenshot remains
<!-- P4-RE-AUTOLOOPS -->

---

# P4-RE2 — GATE-2 RE-VERIFY #2: the XML-wiring fix (`9ed6c58` + `37308e3`, HEAD `37308e3`)

The user bug: `autocue --loops` (XML path) wrote **0 loop marks** — `_merge_loops` dropped
any memory loop sharing a downbeat with a hot cue (`9ed6c58` root cause), and the XML
branch never merged loops at all (`37308e3`). RUN→READ→SHOW fresh.

## 1 · STATIC — full suite + golden → GREEN
```
$ python -m pytest -q
1619 passed, 7 skipped, 4 warnings in 43.33s      (exit 0)   # was 1614p → +5
$ python -m pytest -q tests/test_autoloops_golden.py
20 passed in 0.58s                                (exit 0)
```

## 2 · END-TO-END XML — the user bug, now fixed
```
$ python -m autocue --track-id 136122394 --loops --output /tmp/xmlwire_verify.xml
Wrote /private/tmp/xmlwire_verify.xml — 4 named loop(s) added
```
Written XML — **4 loop marks `Type="4" Num="-1" … End=…`** coexisting with **8 hot cues
`Type="0"`** (before the fix: 0 loop marks):
```
<POSITION_MARK Name="Intro" Type="0" Start="0.44"   Num="7" />      … 8 cues (Num 0–7)
<POSITION_MARK Name="Intro" Type="4" Start="0.44"   Num="-1" End="7.604"  />
<POSITION_MARK Name="Build" Type="4" Start="7.05"   Num="-1" End="14.214" />
<POSITION_MARK Name="Break" Type="4" Start="71.64"  Num="-1" End="85.968" />
<POSITION_MARK Name="Outro" Type="4" Start="209.44" Num="-1" End="223.768"/>
```
✅ 4 loop(Type=4) + 8 cue(Type=0); every loop has Start+End(sec)+Name+Num=-1 (memory
loop). Loop downbeats align with their hot cues (0.44 / 7.05 / 71.64 / 209.44) — the
coexistence the root-cause `_merge_loops` fix enables.

## 3 · `--serato` REGRESSION — same-downbeat drop, on a THROWAWAY COPY
Local `.m4a` (Boards of Canada — '84 Pontiac Dream, id 119875137) where **all 3 loops
share a downbeat with a hot cue** — the exact latent-drop case. Copied first (original
untouched); replicated the CLI generated-cue path (`generate_cues_for_track` +
`analyze_loops` + `_merge_loops`) → `write_serato_tags` → read back:
```
mode=phrase  generated cues=8  loops=[Build, Break, Outro]
same-downbeat collisions (would-be-dropped pre-fix): [Build, Break, Outro]
after _merge_loops: 3 loop(s) kept
READ-BACK from the .m4a copy: 8 CUE + 3 LOOP
  LOOP idx=0 Build  34500→41595     idx=1 Break 182640→196830     idx=2 Outro 205540→219730
```
✅ all 3 same-downbeat loops survived + 8 cues coexist. Latent drop FIXED in the Serato
path too. Copy: `/tmp/autoloops-gate2/pontiac-dream__serato-regression.m4a`.

## 4 · GATE-2 VERDICT — RE-VERIFY #2

| DESIGN item | P4-RE | P4-RE2 |
|---|---|---|
| Full suite / regression | ✅ 1614p | ✅ **1619p / 0F** |
| Golden (incl. F1/E-8/CLI) | ✅ 20/20 | ✅ 20/20 |
| Policy §2 (Intro/Build/Break/Outro, clamp, cap 4) | ✅ MATCH | ✅ MATCH |
| **INC-2 XML `--loops` writes loop marks** (user bug) | — n/a | ✅ **FIXED** (4× Type=4 + 8 cues) |
| **`_merge_loops` same-downbeat coexist** (root cause) | 🐛 latent | ✅ **FIXED** (XML + Serato) |
| F1 preserve · F2 writer round-trip | ✅ MATCH | ✅ MATCH (unchanged) |
| **F2/F7 Serato ACCEPTS** the bytes | ⏳ pending | ⏳ **PENDING USER** (human gate) |

**Verdict: GATE-2 GREEN** — the user's XML bug is fixed end-to-end (loop marks now
written + coexist with cues), the shared `_merge_loops` root-cause fix is regression-clear
in both the XML and Serato paths, and every automated acceptance criterion passes. The
only open items are the expected **human gates**:
> 📂 **Serato (F7):** open `/tmp/autoloops-gate2/beat-it-bass__autoloops-demo.mp3` (or the
> new `pontiac-dream__serato-regression.m4a`) in Serato DJ Pro → Rescan ID3 Tags →
> confirm named loops render → screenshot.
> 📂 **Rekordbox (INC-2):** import `/tmp/xmlwire_verify.xml` → confirm 4 named memory
> loops (Intro/Build/Break/Outro) appear alongside the hot cues → screenshot.

STATUS: DONE — GATE-2 GREEN (pytest 1619p/7s/0f, golden 20/20; XML now writes 4 Type=4 loop marks coexisting with 8 cues — user bug FIXED; --serato same-downbeat drop regression-clear on a throwaway .m4a); only the expected human F7 Serato-accepts + INC-2 Rekordbox-import screenshots remain
<!-- XMLWIRE-RE-AUTOLOOPS -->

---

# P3 (INC-3) AUTHOR — INDEPENDENT DB-SAFETY SUITE + REAL-DB-COPY RUNBOOK

`--write-db` is the **only AutoCue path that MUTATES the real Rekordbox library**. The
failure mode is *destruction of user data*: memory CUES and memory LOOPS share the
`Kind=0` space (discriminated only by `OutMsec`), so a loop write that DELETEs `Kind=0`
wipes the DJ's hand-placed memory cues.

**AUTHORED, NOT RUN** (implementer INC-3 build in flight; I run at P4).
`python -m py_compile` OK · `pytest --collect-only` → **21 tests collected**.

## File — `tests/test_autoloops_db_golden.py` (verifier-owned, DISJOINT #99)
`git status` touches only my file — zero overlap with `tests/test_autoloops.py` /
`test_db_writer.py` / the implementer's `test_autoloops_dbwrite.py`.

**Why an independent author matters here:** an implementer who forgot the no-DELETE rule
would equally forget to test for it — the clobber test would be written to match the
*code*, not the *requirement*. Every assertion below is derived from `crew/DESIGN.md`
"INCREMENT 3", not from reading `write_loops_to_db`'s implementation.

| Case | Test | Asserts |
|---|---|---|
| **DB-1** | `TestNoClobber` | Seed 2 pre-existing `Kind=0` memory cues + 1 hot cue → write loops → **every original row survives BYTE-IDENTICAL** (all columns compared, minus `created_at`/`updated_at`), loops **ADDED** (2→4 Kind=0) |
| **DB-2** | `TestNoDeleteEverIssued` | SQLAlchemy `before_cursor_execute` listener → **no `DELETE` statement is ever emitted**. *Stronger than DB-1* — catches a DELETE matching 0 rows in the fixture that would clobber in the wild. Includes an anti-vacuous check (the listener really captured INSERTs) |
| **DB-3** | `TestIdempotency` | 2nd run inserts **ZERO** rows, returns 0, row count unchanged |
| **DB-4** | `TestCollisionSkip` | Loop colliding on an existing `Kind=0` `InMsec` → **skipped + breadcrumb**; non-colliding loops in the same call **still written**; the DJ's point cue is **not converted into a loop** (`OutMsec` still -1) |
| **DB-5** | `TestMirrorNegativeWriteCuesIsUnsafe` | **Characterizes the dangerous function**: `write_cues_to_db(overwrite=True)` **DOES delete** `Kind=0`; `overwrite=False` **silently drops** the memory cue. If either flips, the INC-3 no-reuse rationale must be re-derived |
| **DB-6** | `TestRollbackOnFailure` | Failure on the 2nd loop → `pytest.raises` (**error PROPAGATES, not swallowed**), **nothing partially written** (snapshot identical, not even loop #1), error breadcrumb logged |
| **DB-7** | `TestLoopRowColumnsAndUnits` | Schema-pin (loop columns exist) + **the unit traps**: `OutMsec` in **ms** (not sec, not -1) · `OutFrame`=round(end×150/1000) · `BeatLoopSize` in **BEATS** (8-bar loop = 32, not 8) · **`ActiveLoop=0`** (1 would auto-arm the loop on the DJ) · `Kind=0` · `Comment`=name · unique UUID/ID · **ID is not a `<MagicMock>`** |
| **DB-8** | `TestOnlyMemoryLoopsAreWritten` | Mixed [hot cue, memory point cue, memory loop] → **exactly 1 row** (the loop); no `Kind>=1` row; no point-cue row; hot-slot loop not written; `dry_run` writes nothing |
| **DB-9** | `test_db9_refuses_when_rekordbox_is_running` | Refuse · exit≠0 · **0 writes** · **no backup taken** |
| **DB-10a/b/c** | `test_db10a/b/c…` | **backup BEFORE the first write** (call-order proof) · **backup FAILURE ABORTS** → exit≠0, **0 writes** (a swallowed backup error that lets the write proceed is the worst bug on this path) · **backup path PRINTED** (the user's only undo) |
| **DB-11** | `test_db11_refuses_when_autocue_serve_holds_the_db` | `autocue serve` up (single-writer rule) → refuse · **0 writes**, even though `rekordbox_is_running` is False |
| **DB-12** | `test_db12_dry_run_writes_nothing` | `--write-db --dry-run` → **0 writes, 0 backups**, "Dry run" printed |
| **DB-13** | `test_db13_write_db_without_loops_makes_no_db_write` | `--write-db` alone → **0 DB writes** (reject-with-exit or no-op both accepted; a write is not) |

### 🔒 Harness safety (verified by grep, this session)
- **`MasterDatabase` is never called** — it appears only as a `monkeypatch.setattr` target, so no real library can be opened. No `~/Library/Pioneer/…` path anywhere.
- **`db.generate_unused_id` IS stubbed** (the false-green trap: unstubbed it silently writes `ID=<MagicMock>` and the test still passes). DB-7 asserts the ID is a real value.
- In-memory SQLite + the **real pyrekordbox schema**; loop columns introspected from `DjmdCue.__table__` so a schema change fails loudly.

### Expected at P4
DB-1..DB-8 should green once `write_loops_to_db` lands (it exists at `db_writer.py:202`).
**DB-9..DB-13 fail until the CLI `--write-db` flag is wired** (not in `cli.py` at authoring
time) — reconcile the monkeypatch seam targets at P4, same as the INC-1 `analyze_loops` seam.

---

## 🚨 P4 REAL-DB-COPY RUNBOOK (scratch SQLite ≠ SQLCipher master.db)

Automated proof stops at an in-memory SQLite. The real driver is **SQLCipher** and the real
risk is the DJ's actual library. **Verify on a COPY. NEVER run this against the live DB.**

### 1 · Copy the library (never touch the original)
```bash
mkdir -p /tmp/ac-scratch
RB=~/Library/Pioneer/rekordbox
cp "$RB/master.db" /tmp/ac-scratch/master.db
for s in -wal -shm; do [ -f "$RB/master.db$s" ] && cp "$RB/master.db$s" "/tmp/ac-scratch/master.db$s"; done
ls -l /tmp/ac-scratch/
```

### 2 · Snapshot BEFORE — every `Kind=0` row for the target track
```bash
python - <<'PY'
from pyrekordbox import Rekordbox6Database as DB
from pyrekordbox.db6 import DjmdCue
db = DB("/tmp/ac-scratch/master.db")          # the COPY, never the live path
TID = "<TRACK_ID>"
for r in db.session.query(DjmdCue).filter(DjmdCue.ContentID == TID, DjmdCue.Kind == 0).all():
    print(f"BEFORE Kind=0 id={r.ID} InMsec={r.InMsec} OutMsec={r.OutMsec} "
          f"BeatLoopSize={r.BeatLoopSize} ActiveLoop={r.ActiveLoop} Comment={r.Comment!r}")
PY
```

### 3 · Run the loop write on ONE track — against the COPY
```bash
python -m autocue --track-id <TRACK_ID> --loops --write-db --db-path /tmp/ac-scratch/master.db
```
Expect: the **backup path printed** (the user's only undo) then `Wrote N loop(s) to <title>`.

### 4 · Snapshot AFTER + DIFF — the no-clobber proof on REAL data
Re-run the step-2 dump and assert:
- **(a)** every pre-existing `Kind=0` row from step 2 is **still present and byte-identical**
  ← the no-clobber proof on real data;
- **(b)** the new loop rows carry `OutMsec` (ms, > `InMsec`), `BeatLoopSize` (beats),
  `ActiveLoop=0`, `Kind=0`, `Comment` = the loop name;
- **(c)** **re-run step 3 → ZERO new rows** (idempotent on real data).
**SHOW the before/after row diff in the report.**

### 5 · Optional user step
Open the **copy** in Rekordbox → named memory loops appear **and the DJ's memory cues are
still there**.

### 6 · The command the USER runs on their LIVE library (only after the above is green)
```bash
# Rekordbox CLOSED. No `autocue serve` running. AutoCue auto-backs-up FIRST and prints the path.
autocue --track "<TITLE>" --loops --write-db          # start with ONE track
autocue --library --loops --write-db                  # then the whole library
```
Undo = the printed backup: `cp ~/.autocue/backups/master_<TS>.db ~/Library/Pioneer/rekordbox/master.db`
(Rekordbox closed). A verification run must never *need* it — but it is there.

STATUS: DONE
<!-- P3-AUTOLOOPS-INC3-SPECS -->

---

# P4 (INC-3) GATE-2 — DB-DIRECT loop write @`fb218f3`

🚫 **The live `master.db` was NEVER opened for write.** Every real-DB step ran against
`/tmp/autoloops-db/master.db` (a copy). Live mtime unchanged (`Jul 11 02:41`, before this
session). A later probe that would have opened the live DB read-write was correctly
refused by the safety classifier — I did not work around it.

## 1 · STATIC — full suite
```
$ python -m pytest -q
1656 passed, 7 skipped, 4 warnings in 89.24s        (exit 0)
```

## 2 · MY INDEPENDENT SAFETY SUITE — `tests/test_autoloops_db_golden.py` → **21/21**
```
$ python -m pytest -q tests/test_autoloops_db_golden.py
21 passed in 1.36s        (exit 0)
```
DB-1 no-clobber · DB-2 no-DELETE (SQL-listener; **mutation-checked** — the listener does
catch a real DELETE, so the guard is not vacuous) · DB-3 idempotent · DB-4 collision+
breadcrumb · DB-5 mirror-negative · DB-6 rollback/raise/no-partial · DB-7 columns+units ·
DB-8 loops-only · DB-9..13 CLI guards.

**Two harness reconciliations (my bugs, not the code's):**
1. **False RED:** pyrekordbox's ORM marks `InPointSeekInfo`/`OutPointSeekInfo`/`usn`/
   `rb_local_usn` NOT NULL, but the **real DDL** (read from the copy) says
   `InPointSeekInfo VARCHAR(255) DEFAULT NULL` — and **30761 of 30763 real rows are NULL**.
   The shipped `write_cues_to_db` omits them too. A `create_all()` scratch schema is
   therefore *stricter than the user's real DB*. Relaxed exactly those 4 (restored after);
   every column the writer must set stays NOT NULL so an omission still fails loudly.
2. **False RED:** my DB-2 check `"DELETE" in sql.upper()` matched the *column*
   `rb_local_deleted` inside ordinary SELECTs. Tightened to the DML statement form
   (`sql.strip().upper().startswith("DELETE")`) — stricter and correct.

## 3 · ★ REAL-DB-COPY TEST — track `119875137` ('84 Pontiac Dream)
Copy at `/tmp/autoloops-db/master.db` + a **read-only symlink** `share → …/rekordbox/share`
(pyrekordbox resolves ANLZ under `<db_dir>/share/PIONEER/USBANLZ`; without it every track
reported *"no usable beat grid"* — **the P-10 breadcrumb I fought for is what surfaced this**).

Ideal subject: **3 pre-existing `Kind=0` memory cues** to protect + 8 hot cues + 3 eligible
loops — and the **Outro loop @205540 ms collides exactly with the DJ's "Mix Out" memory cue**,
so mirror-first gets exercised on real data.

### (a) BEFORE — 11 DjmdCue rows
```
 id= 87405070 Kind=0 [MEMORY ] InMsec=   110 OutMsec=-1 Comment='Load Point'
 id=245629094 Kind=0 [MEMORY ] InMsec=  6270 OutMsec=-1 Comment='Mix In'
 id=184603805 Kind=0 [MEMORY ] InMsec=205540 OutMsec=-1 Comment='Mix Out'    ← collision target
 id=135590352 Kind=1..8 [HOT ] … 8 hot cues (Drop 1-4, Build, Break, Intro, Outro)
```

### (b) DRY-RUN — previews, writes NOTHING ✅
```
  '84 Pontiac Dream: loop [Build] 00:34–00:41 (4 bars)
  '84 Pontiac Dream: loop [Break] 03:02–03:16 (8 bars)
  '84 Pontiac Dream: loop [Outro] 03:25–03:39 (8 bars)
Dry run — no files written.
  master.db md5 before == after  → ✅ BYTE-IDENTICAL   ·   no backup taken
```

### (c) REAL WRITE via the CLI → **🔴 ABORTED (see BL-1)**
```
Error: Rekordbox is running — close it before writing to the database.
  backups: 6 → 6 (none taken)   ·   md5 unchanged (0 writes)
```
Rekordbox is **NOT** running. This is the blocker — see §5.

### (d) NO-CLOBBER PROOF (driving `write_loops_to_db` directly, backup taken first)
```
Backup -> ~/.autocue/backups/master_20260711T050955.db
write_loops_to_db -> 2 written of 3 eligible (1 skipped on collision)

=== NO-CLOBBER: every pre-existing row byte-identical? ===
   destroyed rows: NONE ✅
   mutated rows  : NONE ✅

=== NEW Kind=0 loop rows ===
 NEW id=100588827 Kind=0 InMsec= 34500 OutMsec= 41595 OutFrame= 6239 BeatLoopSize=16 ActiveLoop=0 Comment='Build'
 NEW id= 89112161 Kind=0 InMsec=182640 OutMsec=196830 OutFrame=29524 BeatLoopSize=32 ActiveLoop=0 Comment='Break'

=== the DJ's 3 memory cues, AFTER ===
 SURVIVED id= 87405070 InMsec=   110 OutMsec=-1 'Load Point'
 SURVIVED id=245629094 InMsec=  6270 OutMsec=-1 'Mix In'
 SURVIVED id=184603805 InMsec=205540 OutMsec=-1 'Mix Out'   ← Outro loop correctly SKIPPED here
```
Units verified on real data: `OutMsec` in **ms** (> `InMsec`) · `OutFrame == round(end×150/1000)`
· `BeatLoopSize` in **BEATS** (16 / 32) · **`ActiveLoop=0`** · `Kind=0` · `Comment` = loop name.

### (e) IDEMPOTENT — re-run
```
RE-RUN: wrote 0 (must be 0) · rows unchanged: True   ✅
```

## 4 · GUARDS (live)
- **`--write-db` without `--loops`** → `Error: --write-db requires --loops…`, **DB byte-identical, 0 writes** ✅
- **serve single-writer probe:** throwaway serves started **against the copy**, then stopped.
  `autocue_serve_is_running()` → **True** with a serve on the default **7432** ✅ …but it probes
  **only 7432** — a serve on `--port 3009` (or the coordinator's `:3004`) returns **False** ⚠ (BL-2).
- The CLI's *serve* abort path is currently **unreachable** — BL-1 fires first (`cli.py:265` before `:275`).

## 5 · VERDICT — 🔴 **BLOCKED**

The **writer is proven safe** (no-clobber on real data, no DELETE, idempotent, correct units,
collision-skip). But the **CLI cannot write at all**:

### 🔴 BL-1 (BLOCKER) — `--write-db` is fully broken on a real DB: the guard self-detects AutoCue
`rekordbox_is_running(db_path)` → `_db_file_is_locked(db_path)` cannot take an exclusive lock
because **AutoCue itself already holds the DB open**. `cli.py:124` opens `MasterDatabase`, the
analysis queries leave SQLAlchemy's autobegin transaction open (holding a SQLite lock), and
**then** `cli.py:265` probes the lock → **"Rekordbox is running"** → `sys.exit(1)`.
Characterization (on the copy, Rekordbox closed):

| state | `_db_file_is_locked` |
|---|---|
| nothing open | `False` ×6 |
| DB opened, no query | `False` ×6 |
| **after a query** (the CLI's state at the guard) | **`True`** |
| after `session.rollback()` | `False` ×6 |

**CLI aborts 3/3 runs.** The user can never write loops. Every unit test *mocks*
`rekordbox_is_running`, so only the real-artifact drive catches this.
**Fix:** run the Rekordbox guard **before** `MasterDatabase(...)` is opened (resolve `db_path`
from `args.db_path`/the default dir first) — semantically the right place ("Rekordbox must be
closed before we even open the DB"). Alternative: `db.session.rollback()`/close immediately
before the probe.

### ⚠ BL-2 (medium) — the serve single-writer guard only probes port 7432
`autocue_serve_is_running(port=AUTOCUE_SERVE_PORT=7432)`. A user running
`autocue serve --port 3004` (exactly what this crew was doing) is **invisible** to the guard →
the single-writer rule can still be violated. **Fix:** detect the serve by process/lockfile, or
probe the DB handle rather than one hard-coded port.

### The command the USER will run — ONLY after BL-1 is fixed
```bash
# Rekordbox CLOSED · no `autocue serve` running · AutoCue auto-backs-up FIRST and prints the path
autocue --track "<TITLE>" --loops --write-db --dry-run   # preview
autocue --track "<TITLE>" --loops --write-db             # start with ONE track
autocue --library --loops --write-db                     # then the whole library
```
Undo = the printed backup: `cp ~/.autocue/backups/master_<TS>.db ~/Library/Pioneer/rekordbox/master.db`
(Rekordbox closed).

STATUS: BLOCKED — BL-1 (`--write-db` self-detects AutoCue's own DB handle → aborts 3/3 with a false "Rekordbox is running"; user can never write) + BL-2 (serve guard probes only port 7432). Writer itself PROVEN safe: real-DB-copy no-clobber (0 destroyed / 0 mutated), collision-skip, idempotent, correct units; suite 1656p/0f; my independent safety suite 21/21.
<!-- P4-AUTOLOOPS-INC3 -->

---

# P4-RE (INC-3) — GATE-2 RE-VERIFY: the consolidated fix @`6cf2d8f`

🚫 Live `master.db` **never opened for write** — mtime `Jul 11 02:41:45`, pre-session. All
real-DB work on `/tmp/autoloops-verify2/master.db` (a fresh copy + a read-only `share`
symlink for the ANLZ tree). I am the authoritative BL-1 proof: it needs SQLCipher/WAL via
pyrekordbox and does not reproduce on plain SQLite.

## 1 · ★ BL-1 — THE MONEY TEST: the CLI now actually WRITES

**BEFORE** (track 119875137, '84 Pontiac Dream) — 11 rows: **3 DJ memory cues** (`Load
Point` @110, `Mix In` @6270, `Mix Out` @205540) + 8 hot cues.

**Dry-run** → previews `[Build] [Break] [Outro]`, `Dry run — no files written.`,
**md5 byte-identical** ✅

**REAL WRITE** — *no false "Rekordbox is running"; it wrote:*
```
Backup: /Users/henrigeorge/.autocue/backups/master_20260711T052859.db
  ^ your ONLY undo — keep it until you've checked the result in Rekordbox.
  '84 Pontiac Dream: wrote 2 loop(s) (1 already had an entry at that start)
Database write: 2 named memory loop(s) added · 1 skipped.
exit=0   ·   backups 7 → 8   ·   md5 85e9ad… → 2b1942… (the write landed)
```

**AFTER — NO-CLOBBER on real data (11 → 13 rows):**
```
pre-existing rows destroyed : NONE ✅
DJ's 3 memory cues mutated  : NONE ✅
   SURVIVED id= 87405070 InMsec=   110 OutMsec=-1 'Load Point'
   SURVIVED id=245629094 InMsec=  6270 OutMsec=-1 'Mix In'
   SURVIVED id=184603805 InMsec=205540 OutMsec=-1 'Mix Out'   ← Outro loop SKIPPED here (mirror-first)

NEW id=196356040 Kind=0 InMsec= 34500 OutMsec= 41595 OutFrame= 6239 BeatLoopSize=16 ActiveLoop=0 'Build'
NEW id=158104644 Kind=0 InMsec=182640 OutMsec=196830 OutFrame=29524 BeatLoopSize=32 ActiveLoop=0 'Break'
```
Units on real data: `OutMsec` ms (> `InMsec`) · `OutFrame == round(end×150/1000)` ·
`BeatLoopSize` in **BEATS** (16/32) · **`ActiveLoop=0`** · `Kind=0` · `Comment` = loop name.

**IDEMPOTENT re-run:** `wrote 0 loop(s) (3 already had an entry at that start)` → **0 added, 3 skipped** ✅

## 2 · BL-2 — serve single-writer guard, LIVE (non-default port)
| scenario | result |
|---|---|
| **no serve** running | **no abort** — no false positive ✅ |
| serve on **:3009** (non-default) holding the *same* copy | **ABORTS, 0 writes** ✅ (message attributed to the Rekordbox lock probe, which fires first — see nit below) |
| serve on **:3009** holding a *different* DB (lock probe passes) | **ABORTS with the serve message** ✅ — `Error: a local `autocue serve` is running and holds the database open… (single-writer rule).` |

`autocue_serve_is_running()` → **True** for a serve on **3009** (was `False` before the fix —
the process-based probe replaced the hard-coded port-7432 check). **BL-2 FIXED.**

> ⚠ **LOW nit (not a blocker):** when the serve holds the *same* file being written, the
> Rekordbox file-lock probe fires first, so the user sees *"Rekordbox is running"* although
> the real holder is an `autocue serve`. The write is correctly refused (0 bytes) — safety is
> intact; only the attribution is misleading. Consider checking the serve probe first, or
> softening the lock-probe message to "the database is locked by another process".

## 3 · F4 — guard / backup / write all target the SAME `--db-path` file
```
printed backup      : ~/.autocue/backups/master_20260711T052859.db
md5(backup)         = 85e9ad1585db87afe27093a657b8c6a3
md5(COPY pre-write) = 85e9ad1585db87afe27093a657b8c6a3   → ✅ byte-exact snapshot OF THE COPY
live master.db mtime: Jul 11 02:41:45  (untouched)
```

## 4 · F3 — a DJ's hand-made memory LOOP now survives `write_cues_to_db(overwrite=True)`
The Kind=0 rewrite now deletes **point cues only** (`OutMsec <= InMsec`); loops are spared.
```
BEFORE Kind=0: ['DJ LOOP', 'DJ Point']
AFTER  Kind=0: ['DJ LOOP', 'New Mem']
  DJ hand-made LOOP survived overwrite=True : True ✅  (InMsec=50000 OutMsec=58000 BeatLoopSize=32 — intact)
  memory POINT cue still rewritten          : True ✅
  new memory cue written                    : True ✅
```
**Server suite (the F3 blast radius — /api/apply, SSE, memory_cue_mode): `234 passed`** ✅
*(Note: my DB-5 mirror-negative still passes and still pins the hazard — `write_cues_to_db`
`overwrite=True` continues to wholesale-delete memory POINT cues, and `overwrite=False` still
silently drops. So the append-only `write_loops_to_db` remains the right call.)*

## 5 · Suites
```
$ python -m pytest -q                              → 1668 passed, 7 skipped, 0 failed  (exit 0)
$ python -m pytest -q tests/test_autoloops_db_golden.py → 21 passed                    (exit 0)
$ python -m pytest -q tests/test_serve*.py         → 234 passed                        (exit 0)
```

## 6 · VERDICT — ✅ **GATE-2 GREEN**

| item | P4 | P4-RE |
|---|---|---|
| **BL-1** `--write-db` writes at all | 🔴 aborted 3/3 (false "Rekordbox is running") | ✅ **WRITES** (backup printed, 2 loops, exit 0) |
| **BL-2** serve guard on a non-default port | 🔴 invisible (:3009 → False) | ✅ **detected + aborts** |
| **F3** DJ memory LOOP vs `overwrite=True` | 🐛 clobbered | ✅ **spared** (server suite 234p) |
| **F4** backup/guard/write target `--db-path` | — | ✅ backup is a byte-exact snapshot of the copy |
| NO-CLOBBER on real data | ✅ (writer, driven directly) | ✅ **via the CLI end-to-end** (0 destroyed / 0 mutated) |
| Collision skip · idempotent · units | ✅ | ✅ |
| Full suite · independent safety suite | 1656p · 21/21 | **1668p · 21/21** |

Both blockers cleared; no reds. One LOW message-attribution nit (§2), safety unaffected.

### The exact command the USER runs on their LIVE library
```bash
# Rekordbox CLOSED · no `autocue serve` running.
# AutoCue takes a backup FIRST and prints the path — that is your only undo.
autocue --track "<TITLE>" --loops --write-db --dry-run   # 1. preview, writes nothing
autocue --track "<TITLE>" --loops --write-db             # 2. start with ONE track
# check it in Rekordbox (named memory loops appear; your memory cues are still there)
autocue --library --loops --write-db                     # 3. then the whole library
```
Undo: `cp ~/.autocue/backups/master_<TS>.db ~/Library/Pioneer/rekordbox/master.db` (Rekordbox closed).

STATUS: DONE — GATE-2 GREEN. BL-1 FIXED (CLI now writes: backup printed, 2 loops added, exit 0), BL-2 FIXED (serve on :3009 detected → aborts, no false positive), F3 FIXED (DJ memory LOOP survives overwrite=True; server suite 234p), F4 verified (backup = byte-exact snapshot of the --db-path copy). Real-DB-COPY no-clobber via the CLI end-to-end: 11→13 rows, 0 destroyed / 0 mutated, collision-skip, idempotent re-run 0. Suites: 1668p/7s/0f · independent safety 21/21. Live master.db never touched. One LOW nit: a serve holding the same file surfaces the "Rekordbox is running" message (write still correctly refused).
<!-- P4RE-AUTOLOOPS-INC3 -->

---

# FINAL — GATE-2 across INC-1 (Serato) + INC-2 (XML) + INC-3 (DB) @`7d8683d`

🚫 Live `master.db` **never opened for write** — mtime `Jul 11 02:41:45`, pre-session. All DB
work on `/tmp/autoloops-*` copies; all audio writes on throwaway copies. No stray serves.

## 1 · Suites
```
$ python -m pytest -q                                    → 1681 passed, 7 skipped, 0 failed (exit 0)
$ python -m pytest -q tests/test_autoloops_db_golden.py  → 21 passed          (independent safety suite)
$ python -m pytest -q tests/test_serve*.py               → 234 passed
$ pytest tests/test_autoloops{,_golden,_db_golden}.py    → 145 passed         (INC-1/2/3)
```
> ⚠ **One transient flake observed, NOT a regression.** The first server-suite-alone run showed
> `TestDownloadAlbumEndpoint::test_downloads_each_track` failing. It **passes alone** and on **2
> consecutive re-runs (234/234)**, and the **full suite is green (0 failed)**. It is a
> download/SSE test with no relationship to the loops diff — a pre-existing flaky test. Flagged,
> not owned.

## 2 · The 4 cleanup fixes — regression-confirmed

### (a) ★ `has_existing_memory_cues` — loops must not gate the memory-cue write (the silent stop)
The regression our own loops introduced: once `--write-db` added `Kind=0` loops, a **default
(`overwrite=False`) apply silently stopped writing the user's memory cues**.
```
track has 2 Kind=0 LOOPS and 0 memory POINT cues
has_existing_memory_cues() = 0        ← loops are NOT memory cues ✅ (was counting them)
after DEFAULT (overwrite=False) apply, Kind=0 = ['Break', 'Build', 'Load Point']
   memory cue WRITTEN despite pre-existing loops : True ✅
   the 2 loops still intact                      : True ✅
```

### (b) `--write-db` exit code
```
full success              → exit 0  ✅   ("Database write: 0 added · 3 skipped")
forced failure (RO DB)    → exit 1  ✅   "1 track(s) FAILED … PARTIAL write; earlier tracks
                                          are already committed. Backup: …"
                                         (+ "rolled back"; DB left intact — 11 rows, all cues survive)
```
A partial DB write can never look like success to a script.

### (c) With a serve running, the CLI blames the **SERVER**, not Rekordbox — and refuses
```
Error: a local `autocue serve` is running and holds the database open.
       Stop the server before using --write-db (single-writer rule).
   DB byte-identical — 0 writes ✅
```
(My P4-RE LOW nit — the misleading "Rekordbox is running" attribution — is **closed**.)

### (d) Serve scan: no false positive, fail-SAFE intact
```
decoy `grep serve autocue/cli.py` running → _serve_process_is_running() = False ✅
REAL `autocue serve --port 3009`         → _serve_process_is_running() = True  ✅
```

## 3 · End-to-end SMOKE — all three increments

**INC-3 (DB, real-DB copy)** — dry-run byte-identical, then:
```
Backup: ~/.autocue/backups/master_20260711T055320.db
  '84 Pontiac Dream: wrote 2 loop(s) (1 already had an entry at that start)   exit=0
AFTER: 13 rows (was 11) · every pre-existing cue survives ✅
  NEW LOOP InMsec= 34500 OutMsec= 41595 OutFrame= 6239 BeatLoopSize=16 ActiveLoop=0 'Build'
  NEW LOOP InMsec=182640 OutMsec=196830 OutFrame=29524 BeatLoopSize=32 ActiveLoop=0 'Break'
idempotent re-run → 0 added · 3 skipped ✅
```

**INC-2 (XML)** — `Wrote … — 4 named loop(s) added`
```
POSITION_MARK: 12 total · loop(Type=4)=4 · cue(Type=0)=8
  LOOP Intro Start=0.44   End=7.604     LOOP Build Start=7.05   End=14.214
  LOOP Break Start=71.64  End=85.968    LOOP Outro Start=209.44 End=223.768
```

**INC-1 (Serato)** — throwaway `.m4a` copy (original never touched)
```
read-back: 8 CUE + 3 LOOP
  LOOP idx=0  34500 → 41595  'Build'
  LOOP idx=1 182640 →196830  'Break'
  LOOP idx=2 205540 →219730  'Outro'
✅ LOOP entries present and coexisting with the cues
```

## 4 · FINAL VERDICT — ✅ **GATE-2 GREEN across all three increments**

| Increment | Automated | Real artifact | Verdict |
|---|---|---|---|
| **INC-1 Serato** | golden 20/20 · F1 preserve · regression | LOOP entries read back from a real `.m4a` copy | ✅ GREEN |
| **INC-2 XML** | writer units | 4 × `Type="4" … End=` coexisting with 8 cues | ✅ GREEN |
| **INC-3 DB** | independent safety suite **21/21** (no-clobber, no-DELETE, rollback, units, guards) | real-DB-copy: 0 destroyed / 0 mutated · collision-skip · idempotent · exit codes · serve + Rekordbox guards | ✅ GREEN |

All blockers from earlier rounds are closed: **BL-1** (`--write-db` self-lock), **BL-2** (serve
guard blind to non-default ports), **F3** (memory LOOP clobbered by `overwrite=True`), **F4**
(db-path targeting), the **P-10 breadcrumb**, **C-3 dry-run preview**, the **XML wiring bug**,
and the **`has_existing_memory_cues` silent stop**. No reds outstanding.

### Remaining human gates (not code reds)
- **F7 · Serato accepts the bytes** — open `/tmp/autoloops-gate2/final__serato-smoke.m4a` (or
  `beat-it-bass__autoloops-demo.mp3`) in **Serato DJ Pro** → Files → Rescan ID3 Tags → confirm the
  named loops render → screenshot. *(The reserved/colour bytes `0x0a–0x12` are only ever proven here.)*
- **Rekordbox import** — import `/tmp/final_verify.xml`, or open a written **copy** of master.db,
  and confirm named memory loops appear with the DJ's own cues intact.

### The command the USER runs on their LIVE library
```bash
# Rekordbox CLOSED · no `autocue serve` running. AutoCue backs up FIRST and prints the path.
autocue --track "<TITLE>" --loops --write-db --dry-run   # preview, writes nothing
autocue --track "<TITLE>" --loops --write-db             # start with ONE track, check it in Rekordbox
autocue --library --loops --write-db                     # then the whole library
```
Undo: `cp ~/.autocue/backups/master_<TS>.db ~/Library/Pioneer/rekordbox/master.db` (Rekordbox closed).

STATUS: DONE — FINAL GATE-2 GREEN across INC-1/INC-2/INC-3. Full suite 1681p/7s/0f; independent DB-safety suite 21/21; server 234p. All 4 cleanup fixes regression-confirmed (memory-cue silent-stop FIXED, --write-db exit 0/1 correct, serve correctly blamed + refuses, serve scan no false-positive yet fail-safe). End-to-end smoke green on a real-DB COPY (2 loops written, 11→13 rows, 0 clobbered, idempotent) + XML 4 loop marks + Serato 3 LOOP entries. Live master.db never touched. One pre-existing flaky test (TestDownloadAlbumEndpoint) flagged, unrelated. Human gates remain: F7 Serato screenshot + Rekordbox import.
<!-- FINAL-AUTOLOOPS -->

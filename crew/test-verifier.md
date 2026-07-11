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
<<<<<<< Updated upstream
=======

---

# PR1 — SALVAGE `fix/loop-kind0-clobber` @`11a7b13` (from origin/main `6e8b024`)

Fixes a **live data-loss bug on main** and touches the **shared server write path**
(`write_cues_to_db` ← /api/apply, generate-apply, SSE, memory_cue_mode) + health
(`quality.py`). 🚫 Live `master.db` never opened for write (mtime `Jul 11 02:41:45`,
pre-session) — all DB work on `/tmp/pr1-copy`, `/tmp/main-repro`, and in-memory SQLite.

## 1 · Full suite
```
$ python -m pytest -q        → 1559 passed, 8 skipped, 0 failed   (exit 0)
```

## 2 · ★ SERVER BLAST RADIUS — any red is a blocker
```
tests/test_serve*.py                                     → 234 passed   ✅
tests/test_db_writer.py + duplicates + duplicates_integration → 85 passed   ✅
health / quality (the quality.py memory_cue_count change) → 70 passed, 1 skipped ✅
tests/test_loop_kind0_clobber.py (the PR's own file)     →  9 passed   ✅
memory_cue_mode coverage (none/load_only/all)            → 34 passed, 1 skipped ✅
```
**No reds anywhere in the blast radius.**

## 3 · ★ THE PR EVIDENCE — bug REPRODUCED on main, FIXED on the branch

Method: `git archive origin/main | tar -x -C /tmp/main-repro` (the user's repo untouched),
dropped the new safety file in, and **proved I was importing main's code**:
```
autocue.db_writer resolved from: /private/tmp/main-repro/autocue/db_writer.py
has _loop_filter (the FIX)?     : False
has has_existing_memory_loops?  : False
```

| | result |
|---|---|
| **origin/main `6e8b024`** | **7 failed, 2 passed** ← the bug reproduces |
| **branch `11a7b13`** | **9 passed** ✅ |

The concrete failures on main:
```
AssertionError: DJ memory cue 900 was DELETED — clobber!
   ← write_memory_loops(overwrite=True) destroys the DJ's hand-placed memory CUES
     (library-wide on `autocue --library --loops --overwrite`)

AssertionError: generated loop 910 was DELETED — clobber!
   ← write_cues_to_db(overwrite=True) destroys saved memory LOOPS
     (ANY Apply / generate-apply with overwrite — the shared server path)

AssertionError: the memory cue was silently suppressed by the loop
AssertionError: the loop was silently suppressed by the DJ's memory cue

AssertionError: BLANKET Kind=0 DELETE — conflates memory cues and loops:
   DELETE FROM "djmdCue" WHERE "ContentID" = ? AND "Kind" = ?        ← no OutMsec discriminator
```
The 2 that **pass on main** are the *intended* protections (`TestIntendedProtectionPreserved`) —
correctly unchanged by the fix.

## 4 · SEMANTICS REGRESSION — we spared LOOPS only; point-cue behaviour is identical
```
A) write_cues_to_db(overwrite=True)   OLD point cue REPLACED (as on main) ✅
                                      NEW point cue written               ✅
                                      saved LOOP SPARED (the fix)         ✅
B) write_memory_loops(overwrite=True) OLD loop REPLACED · NEW loop written ✅
                                      DJ POINT cue SPARED (the fix)        ✅
C) loop write, NO --overwrite, saved loop present → wrote 0, DJ loop intact ✅  (protection preserved)
C2) loop write, NO --overwrite, only POINT cues   → wrote 1                ✅  (no silent suppression)
D) has_existing_memory_cues()=1 · has_existing_memory_loops()=1            ✅  (complementary counters)
```

## 5 · main's OWN loop feature still works — e2e on a real-DB COPY
`generate_loops → write_memory_loops`, track 119875137 (3 DJ point cues, 0 loops):
```
has_existing_memory_cues=3 · has_existing_memory_loops=0
generate_loops -> 1 loop:  'Mix Out Loop'  205540 - 219620 ms
write_memory_loops(overwrite=False) -> wrote 1     ← on MAIN this returns 0 (silently suppressed)

AFTER: 3 POINT cues + 1 LOOP
   POINT InMsec=   110 'Load Point'   POINT InMsec=  6270 'Mix In'
   POINT InMsec=205540 'Mix Out'      LOOP  InMsec=205540 OutMsec=219620 'Mix Out Loop'
   → DJ's 3 point cues INTACT ✅

--overwrite re-run: wrote 1 · POINT cues=3 (still intact) · LOOPS=1 (replaced, not duplicated) ✅
                    ← on MAIN, --overwrite DELETES the 3 DJ point cues
```
Note the loop starts at **the same `InMsec` (205540) as the 'Mix Out' point cue** — a live
illustration of why `OutMsec` must be the discriminator: they are two different rows in the
same Kind=0 space at the same position.

## 6 · VERDICT — ✅ **GATE-2 GREEN. Ship it.**
The fix is correct, the data-loss bug is independently reproduced on main and closed on the
branch, and the shared server write path is fully green (234 + 85 + 70 + 34, no reds). The
intended protections and point-cue semantics are byte-for-byte behaviourally unchanged.

### ⚠ OUT-OF-SCOPE finding — PRE-EXISTING ON MAIN, **not** introduced by this PR
`autocue --track … --loops` (main's DB loop write) **aborts with a false
"Error: Rekordbox is running"** — Rekordbox was NOT running (no processes; both probes False).
Root cause is the same **self-lock** I characterized as BL-1 on `feat/autoloops`:
`cli.py:108` opens `MasterDatabase(...)`, the analysis queries leave SQLAlchemy's autobegin
transaction holding a SQLite lock, and **then** `cli.py:237` calls `rekordbox_is_running(db_file)`
→ the exclusive-lock probe fails → self-detection → `sys.exit(1)`.
**This PR does not touch that guard** (its only `cli.py` change is the skip-message wording), so
it is squarely pre-existing. But it means **main's `--loops` DB write cannot actually write** — it
needs its own fix: run the guard **before** `MasterDatabase(...)` is opened (resolve `db_path`
from `args`/the default first). Recommend a follow-up PR; **not a blocker for PR#1**.

STATUS: DONE — PR#1 GATE-2 GREEN. Bug independently REPRODUCED on origin/main (7 failed: "DJ memory cue 900 was DELETED — clobber!", "generated loop 910 was DELETED — clobber!", silent suppression both ways, blanket Kind=0 DELETE) and FIXED on the branch (9 passed). Server blast radius clean: full 1559p/8s/0f, serve 234p, db_writer+duplicates 85p, health 70p, memory_cue_mode 34p. Point-cue semantics unchanged (overwrite still rewrites them); only LOOPS spared; intended protections intact. main's loop feature e2e on a real-DB COPY: writes 1 loop with the DJ's 3 point cues intact (on main it would be suppressed, and --overwrite would delete them). Live master.db never touched. FLAGGED (out of scope, pre-existing on main): --loops CLI DB write aborts with a false "Rekordbox is running" (BL-1 self-lock; guard runs after MasterDatabase() opens) — needs a follow-up PR.
<!-- PR1-VERIFY -->

---

# PR2 — SALVAGE `fix/loops-db-write-guard` @`ed44ac1` (from origin/main `6e8b024`)

Fixes the **self-lock**: `--loops` could never write to the DB. ⚠️ A mock can never reveal
this — the bug shipped precisely *because* every unit test mocks `rekordbox_is_running`. The
real-DB drive below is the only proof. 🚫 Live `master.db` never opened for write (mtime
`Jul 11 02:41:45`, pre-session); all work on `/tmp/pr2-copy`, `/tmp/pr2-fail`, `/tmp/main2`.

**Preconditions verified at run time:** no `autocue serve` running · **Rekordbox NOT running**
(no matching processes; `_process_name_check() = False`).

## 1 · ★ THE MONEY TEST — bug on MAIN, fix on the branch (same track, same COPY)

Track **136122394** chosen deliberately: it has **0 pre-existing `Kind=0` rows**, so the
*separate* PR#1 suppression bug cannot mask PR#2's write path.

| | command | result |
|---|---|---|
| **origin/main `6e8b024`** | `autocue --track-id 136122394 --loops --db-path <COPY>` | **`exit=1`** · `Error: Rekordbox is running — close it before writing loops.` · **DB byte-identical — wrote NOTHING** |
| **branch `ed44ac1`** | *same command* | **`exit=0`** · backup printed · **`Loops: 1 written · 0 skipped`** |

Rekordbox was **not** running. Main's message is a **false positive against AutoCue's own
handle** — `cli.py:108` opens `MasterDatabase(...)`, the analysis queries leave SQLAlchemy's
autobegin transaction holding a SQLite lock, and *then* the exclusive-lock probe runs.

**The row the branch actually wrote (re-opened from the COPY):**
```
BEFORE: track 136122394 — 0 Kind=0 rows
AFTER : LOOP id=48194376 InMsec=209440 OutMsec=223840 OutFrame=33576 ActiveLoop=0 'Mix Out Loop'
        → a real Kind=0 LOOP row (OutMsec > InMsec) ✅
```
And on a track **with** the DJ's cues (119875137), the branch cleared the guard (backup taken)
and left all **3 point cues intact** — it reported `0 written · 1 skipped`, because
`write_memory_loops` still gates on `has_existing_memory_cues`. **That is the PR#1 bug, not a
PR#2 defect** — the two salvages are complementary and main needs BOTH.

## 2 · BACKUP — targeting + abort-on-failure
```
printed backup       : ~/.autocue/backups/master_20260711T070411.db
md5(backup)          = 85e9ad1585db87afe27093a657b8c6a3
md5(COPY pre-write)  = 85e9ad1585db87afe27093a657b8c6a3
→ ✅ a byte-exact snapshot OF THE --db-path COPY: guard, backup and write all target the
     same file (main reconstructed it from db._db_dir, which can differ from --db-path).
```
**Backup failure (simulated disk-full) → the write is ABORTED:**
```
Error: backup failed — aborting, no loops written: simulated backup failure (disk full)
exit=1  ✅   ·   DB byte-identical → NOTHING written ✅
```

## 3 · EXIT CODES
```
clean run          → exit 0   ("Loops: 1 written · 0 skipped")             ✅
--dry-run          → exit 0 · previews "Mix Out Loop 04:42–04:54 (8 bars)" ·
                     DB byte-identical · NO backup taken                    ✅
failed write (RO)  → exit 1 · "Loop write failed … — rolled back" ·
                     OperationalError surfaced, not swallowed               ✅
```
A partial/failed DB write can never look like success to a script.

## 4 · NO REGRESSION — XML / `--serato` / loop generation
```
diff touches ONLY autocue/cli.py (89+/13-)  ·  writer.py + analysis/ : 0 files changed
XML output        : BYTE-IDENTICAL main vs branch (8 POSITION_MARKs)   ✅
--serato output   : IDENTICAL main vs branch                            ✅
loop generation   : identical — 'Mix Out Loop 04:42–04:54 (8 bars, confidence 0.5)' on both ✅
```
The guard only fires on `args.loops and not args.dry_run`, so the XML/Serato paths never reach it.

## 5 · Suites
```
$ python -m pytest -q                          → 1560 passed, 8 skipped, 0 failed  (exit 0)
$ python -m pytest -q tests/test_loops_db_write_guard.py → 10 passed
$ python -m pytest -q tests/test_serve*.py     → 234 passed
```

## 6 · VERDICT — ✅ **GATE-2 GREEN. Ship it.**
The self-lock is independently reproduced on main (aborts, writes nothing) and closed on the
branch (writes a real loop row, exit 0). Backup-before-write, backup-abort, exit codes, and
dry-run all behave per contract; the backup is provably a snapshot of the file actually written.
XML, Serato and loop generation are byte-for-byte unchanged. No reds.

> **Note for the coordinator — PR#1 and PR#2 are complementary, and main needs BOTH.**
> PR#2 makes `--loops` *able* to write. PR#1 stops it from *clobbering / being suppressed*.
> With only PR#2 merged, a track that already has the DJ's memory cues still gets
> `0 written · 1 skipped` (PR#1's suppression bug). With only PR#1 merged, `--loops` still
> can't write at all (PR#2's self-lock). Merging both gives: writes loops, keeps the DJ's cues.

STATUS: DONE — PR#2 GATE-2 GREEN. Self-lock independently REPRODUCED on origin/main (exit 1, false "Rekordbox is running" with Rekordbox NOT running, DB byte-identical — main can never write loops) and FIXED on the branch (exit 0, backup printed, "Loops: 1 written", a real Kind=0 LOOP row InMsec=209440/OutMsec=223840 written to the COPY). Backup = byte-exact snapshot of the --db-path file actually written; backup failure ABORTS (exit 1, nothing written); exit codes correct (0 clean / 1 partial-failed); --dry-run previews + writes nothing + takes no backup. NO REGRESSION: XML byte-identical, --serato identical, loop generation identical, only cli.py touched. Full suite 1560p/8s/0f, guard tests 10p, serve 234p. Live master.db never touched. NOTE: PR#1 + PR#2 are complementary — main needs BOTH (PR#2 lets it write; PR#1 stops the clobber/suppression).
<!-- PR2-VERIFY -->

---

# PR3 — SALVAGE `fix/serato-preserve-dj-loops` @`7310610` (from origin/main `6e8b024`)

Preserves the DJ's **Serato-native** loops across a `--serato` rewrite. `write_serato_tags`
is a FULL tag replacement, so any LOOP the DJ made in Serato — unknown to the Rekordbox DB —
was silently destroyed on every write.

🚫 **No real library audio file was modified.** Every write went to `/tmp/pr3/*.mp3` copies.
*Provenance check:* the source stem already carried a Serato tag with mtime `02:46:01` — I
verified this is **pre-existing** (all **6 sibling stems** share that identical timestamp = a
bulk `--serato` run by someone else, **17 min before** my first copy-write at `03:03:17`; a
write by me would have stamped only that one file). I only ever passed `/tmp/**` paths to the
writer, and every CLI `--serato` I ran was `--dry-run`.

## 1 · ★ REPRODUCE ON MAIN, PROVE ON OURS
Seeded a throwaway copy with a **Serato-native DJ loop the DB knows nothing about**:
`name='DJ Secret Loop'`, `start=7123`, `end=19457` (off-policy, non-power-of-2), **`locked=0x01`**,
colour `0027aae1`. Then both writers rewrote the tag with the *same* DB loops
(`Mix Out Loop` @60000) — which do **not** include the DJ's loop.

| writer | result |
|---|---|
| **origin/main** (`preserve=` absent) | **1 LOOP** → `['Mix Out Loop']` · **`DJ Secret Loop` WIPED** ❌ |
| **branch `7310610`** | **2 LOOPs** → `['Mix Out Loop', 'DJ Secret Loop']` · **BYTE-IDENTICAL** ✅ |

Byte-identity was asserted on the **raw framed entry** (`raw == dj_raw`), which proves name,
start, end, **locked flag** and **colour** all survived verbatim. The DJ's loop also kept its
original slot (`index=0`) while the generated loop took the next free slot (`index=1`).

## 2 · ★ NO DOUBLE-COUNT (the trap a naive preserve falls into)
Rewrote the same file — which now contains AutoCue's OWN DB-sourced loop — **4 times**:
```
rewrite #1: 2 loop(s) starts=[7123, 60000] duplicate_start=False ✅
rewrite #2: 2 loop(s) starts=[7123, 60000] duplicate_start=False ✅
rewrite #3: 2 loop(s) starts=[7123, 60000] duplicate_start=False ✅
rewrite #4: 2 loop(s) starts=[7123, 60000] duplicate_start=False ✅
→ count stayed N=2 (not 2N, not 4N) · no duplicate start_ms · DJ loop still verbatim
```
The dedup rule (`preserve` = file loops whose `start_ms` ∉ the DB's loop starts) is what makes
this work: a file loop matching a DB start is *ours*, so it is regenerated, not preserved.

## 3 · DB AUTHORITATIVE — a stale file-loop must not shadow the DB
```
before: DB loop end=68000   → re-tuned in the DB to 72000 → rewrite
after : DB loop end=72000   ✅ UPDATED (the stale preserved file-loop did NOT shadow it)
        loop count still 2 · DJ loop still verbatim ✅
```
Same rule, second consequence: because our own loops are regenerated rather than preserved,
the DB stays the source of truth.

## 4 · 8-SLOT CAP → THE DJ WINS
6 foreign loops seeded (slots 0-5), then 5 generated loops written (6 + 5 = 11 > 8):
```
total loops now   : 8 (cap 8)
FOREIGN preserved : 6/6  — and 6/6 BYTE-IDENTICAL ✅
GENERATED kept    : 2  ['Gen 0', 'Gen 1']   (filled the 2 free slots)
GENERATED dropped : 3  (surplus)
WARN> Serato has only 8 loop slots and 6 are held by your own Serato loops
      — dropping 3 generated loop(s): Gen 2, Gen 3, Gen 4
```
✅ The surplus dropped is always **GENERATED, never a DJ loop** — and the drop is warned, not silent.

## 5 · REGRESSION
```
tests/ changed by this PR : ONLY the new tests/test_serato_preserve_dj_loops.py
                            (tests/test_serato_writer.py is UNMODIFIED)
CUE bytes                 : 8-cue payload  main == branch  BYTE-IDENTICAL ✅
                            cues+loops payload main == branch  BYTE-IDENTICAL ✅
preserve=                  : KEYWORD_ONLY — a 3rd positional arg is rejected with TypeError,
                             so every existing positional caller build_markers2(cues, loops) works ✅

tests/test_serato_writer.py         (unmodified) → 57 passed
tests/test_serato_preserve_dj_loops.py           → 11 passed
FULL SUITE                                        → 1561 passed, 8 skipped, 0 failed (exit 0)
```

## 6 · SILENT-FAILURE
A v2 tag **present but undecodable** (garbage payload → 0 entries) fires the WARN and
degrades safely (returns `[]`, the write is never blocked):
```
WARN> undecodable.mp3: the existing Serato GEOB:Serato Markers2 tag could not be decoded
      — any loops you saved in Serato cannot be preserved and will be dropped by this write
```
The DJ's loops can still be lost here (the tag is unreadable), but **never silently**.

## 7 · VERDICT — ✅ **GATE-2 GREEN. Ship it.**
The data-loss is independently reproduced on main (DJ loop wiped) and closed on the branch
(byte-identical survival, incl. the locked flag and colour). The three subtle traps a naive
preserve would hit — **double-count**, **stale file-loop shadowing the DB**, and **a DJ loop
lost to the 8-slot cap** — are each explicitly disproven on real files. CUE bytes are
byte-identical to main, the existing serato suite passes **unmodified**, and `preserve=` is
keyword-only so no existing caller changes. No reds.

STATUS: DONE — PR#3 GATE-2 GREEN. DJ-loop wipe REPRODUCED on origin/main (a Serato-native 'DJ Secret Loop' start=7123 end=19457 locked=0x01, unknown to the DB, is GONE after main's --serato rewrite) and FIXED on the branch (survives BYTE-IDENTICAL — raw framed entry matches, so name/start/end/locked/colour all intact; keeps its slot). NO DOUBLE-COUNT: 4 consecutive rewrites keep N=2, no duplicate start_ms. DB AUTHORITATIVE: a re-tuned DB loop end (68000->72000) updates in the file (no stale shadow). 8-SLOT CAP: 6 foreign + 5 generated -> all 6 DJ loops preserved byte-identical, 2 generated fill the free slots, 3 SURPLUS GENERATED dropped with a WARN (never a DJ loop). REGRESSION: CUE bytes byte-identical to main, tests/test_serato_writer.py UNMODIFIED (57p), preserve= is KEYWORD_ONLY (3rd positional rejected) so all existing callers work; PR tests 11p; full suite 1561p/8s/0f. SILENT-FAILURE: WARN fires when a v2 tag is present but decodes to zero entries. NO real library audio file modified (all writes to /tmp/pr3 copies; the source stem's pre-existing Serato tag verified as someone else's earlier bulk run — all 6 sibling stems share mtime 02:46:01, 17min before my first copy-write).
<!-- PR3-VERIFY -->

---

# PR4 — SALVAGE `fix/loops-single-writer-beatloopsize` @`c6668aa` (from origin/main)

Scope = the **`autocue serve` single-writer guard** only. (BeatLoopSize was correctly BLOCKED
on evidence — out of scope, not verified here.) 🚫 Live `master.db` never opened for write
(mtime `Jul 11 02:41:45`, pre-session); every serve ran with `--db-path /tmp/pr4-copy/master.db`.

## 1 · ★ THE LIVE PROOF — a real serve, three port cases

| serve | detected by | `--loops` result |
|---|---|---|
| **`--port 3004`** (non-default — the exact case a single-port probe misses) | **process scan** (`port 7432 listening? False`) | **REFUSED**, exit 1 |
| **`--port 7432`** (default) | port scan | **REFUSED**, exit 1 |
| **`--port 7435`** (7433-7441 fallback range) | port scan | **REFUSED**, exit 1 |

```
Error: a local `autocue serve` is running and holds the database open.
       Stop the server before writing loops (single-writer rule).
   DB byte-identical — NOTHING written ✅
   backups 20 → 20 — NO backup taken (refused BEFORE the backup) ✅
```
**Serve stopped → the guard RELEASES:** `autocue_serve_is_running() = False`, the serve error no
longer fires, and the DB write path itself proceeds (`write_memory_loops → wrote 1`). The guard
is not the blocker. *(The CLI is then blocked by the pre-existing self-lock that PR#2 fixes —
see the merge note.)* The COPY stayed **byte-identical across all three refusals**.

## 2 · NO FALSE POSITIVES
With all of these running simultaneously, the guard did **not** trip:
```
baseline (nothing)                                  → False
grep serve autocue/cli.py  +  pytest -k serve  +  an unrelated `myapp serve --port 9`
                                                    → False  ✅ NO false positive
the CLI's own process (self-detection, pid excluded) → False  ✅
```
`_is_serve_cmdline` unit-check — the token `serve` must be preceded by a token ending in `autocue`:
```
grep serve autocue/cli.py                 → False ✅     autocue serve                        → True ✅
python -m pytest -k serve autocue         → False ✅     /usr/local/bin/autocue serve --port… → True ✅
myapp serve --port 9                      → False ✅     python -m autocue serve --no-browser → True ✅
```
This matters: a looser "`serve` in cmdline and `autocue` somewhere" match would **refuse a
perfectly legal write** whenever a grep or a test run happened to be open.

## 3 · FAIL-SAFE — an ambiguous probe REFUSES, never allows
```
psutil ImportError   → True  ✅ refuses   WARN> psutil unavailable — … refusing the database write (fail-safe)
process_iter raises  → True  ✅ refuses   WARN> could not probe for a running `autocue serve` — … (fail-safe)
```
Never fail-open: a write it cannot rule out is always refused, and the reason is logged.

## 4 · GUARD ORDER — the message is HONEST
```
cli.py:245   if db_writer.autocue_serve_is_running():     ← asked FIRST
cli.py:250   if rekordbox_is_running(db_file):
```
A running server **also holds the file lock**, so if the Rekordbox probe ran first it would be
misreported as *"Rekordbox is running"*. Proven live: with a serve up the user gets the **serve**
message, not the Rekordbox one. ✅

## 5 · REGRESSION
```
files changed : autocue/cli.py · autocue/db_writer.py · tests/test_serve_single_writer.py
writer.py / analysis/ / serato_writer.py : 0 files changed
XML output        : BYTE-IDENTICAL to main ✅
--serato output   : IDENTICAL to main ✅
loop generation   : unchanged — 'Mix Out Loop 04:42–04:54 (8 bars, confidence 0.5)' ✅
--dry-run         : previews, unchanged ✅

FULL SUITE                          → 1566 passed, 8 skipped, 0 failed (exit 0)
tests/test_serve_single_writer.py   → 16 passed
```

## 6 · VERDICT — ✅ **GATE-2 GREEN. Ship it.**
The single-writer guard refuses on **any** port (default, fallback range, and an arbitrary
`--port 3004` via the process scan), refuses **before** taking a backup or writing a byte, has
**zero false positives** against the realistic look-alikes, **fails safe** on an unresolvable
probe, and reports the cause **honestly** by being asked before the Rekordbox probe. No
regressions. No reds.

> ### ⚠ MERGE NOTE for the coordinator — PR#2 × PR#4 ordering (actionable)
> **PR#2** hoists the Rekordbox probe into `_preflight_loop_write()`, **before**
> `MasterDatabase(...)` opens. **PR#4** inserts the serve check into the *old, post-open* block,
> just before `rekordbox_is_running()`.
> A running serve **also holds the file lock**, so if both land naively, PR#2's preflight
> (Rekordbox) would run **first** and a serve would once again be misreported as *"Rekordbox is
> running"* — re-introducing exactly the dishonesty PR#4 fixes.
> **On merge, hoist `autocue_serve_is_running()` into `_preflight_loop_write()` and ask it
> BEFORE the Rekordbox probe** (the order PR#4 establishes today). The two also touch adjacent
> lines, so expect a textual conflict there — resolve it that way.

STATUS: DONE — PR#4 GATE-2 GREEN. LIVE PROOF: a real `autocue serve` on :3004 (NON-default — caught by the PROCESS scan, port 7432 not listening), on :7432 (default), and on :7435 (7433-7441 fallback) each REFUSED the --loops DB write with the honest single-writer message, exit 1, DB byte-identical, NO backup taken (refused before backup); serve stopped → guard releases (False) and the write path proceeds (write_memory_loops wrote 1). NO FALSE POSITIVES: `grep serve autocue/cli.py`, `pytest -k serve`, an unrelated `myapp serve`, and the CLI's own process all fail to trip it (_is_serve_cmdline requires the token `serve` preceded by a token ending in `autocue`; 6/6 cases correct). FAIL-SAFE: psutil ImportError → True, process_iter raises → True (both WARN; never fail-open). GUARD ORDER: serve asked at cli.py:245 BEFORE rekordbox at :250, so a serve (which also holds the file lock) is reported honestly as a serve. REGRESSION: XML byte-identical, --serato identical, loop generation unchanged, writer/analysis/serato untouched; full suite 1566p/8s/0f; PR tests 16p. Live master.db never touched. ⚠ MERGE NOTE: PR#2 hoists the Rekordbox probe into a pre-open preflight while PR#4's serve check sits in the old post-open block — on merge the serve check MUST be hoisted into _preflight_loop_write() and asked BEFORE the Rekordbox probe, or a running serve is misreported as "Rekordbox is running" again (expect a textual conflict on those adjacent lines).
<!-- PR4-VERIFY -->

---

# PR264-FINAL — `fix/loops-db-write-guard` @`eb96f98` (the FOLD)

`ed44ac1` (self-lock + backup + exit codes) + `eb96f98` (serve single-writer guard), now **ONE
preflight**. The fold is exactly where a guard can be silently dropped — so **every guard was
proven LIVE, not read off the source**. 🚫 Live `master.db` never opened for write (mtime
`Jul 11 02:41:45`, pre-session); every serve and every write targeted `/tmp/pr264*` copies.

## 1 · ★ THE GUARD STILL BITES (real serve, non-default port)
```
serve: python -m autocue serve --port 3004 --no-browser   (a NON-default port)
$ autocue --track-id 136122394 --loops --db-path <COPY>
  exit=1
  Error: a local `autocue serve` is running and holds the database open.
         Stop the server before writing loops (single-writer rule).
  ✅ DB byte-identical — NOTHING written
  ✅ backups 20 → 20 — NO backup taken (refused BEFORE the backup)
  ✅ the message names the SERVER, not "Rekordbox is running"
```

## 2 · ★ THE SELF-LOCK IS STILL FIXED (the regression that matters most)
Serve stopped, Rekordbox closed — the fold did **not** re-break `ed44ac1`:
```
$ autocue --track-id 136122394 --loops --db-path <COPY>
  exit=0
  Database backed up to ~/.autocue/backups/master_20260711T074851.db
    ^ your only undo …
  Loops: 1 written · 0 track(s) skipped

  row actually in the DB:
  LOOP id=12285340 InMsec=209440 OutMsec=223840 OutFrame=33576 ActiveLoop=0 'Mix Out Loop'
```

## 3 · ORDERING — proven empirically, not asserted
With the serve up and **Rekordbox NOT running**:
```
Rekordbox actually running?   NO
_db_file_is_locked(COPY)      True   ← the SERVE holds the lock
rekordbox_is_running(COPY)    True   ← would say "Rekordbox is running" (WRONG)
autocue_serve_is_running()    True   ← the TRUE cause
```
Asked **second**, the user would be told to close an app that isn't even open. The fold asks
**SERVE FIRST**, so the message is honest — confirmed by the live run in §1. ✅

## 4 · `.exe` FALSE-NEGATIVE · NO FALSE POSITIVES · FAIL-SAFE
The matcher now compares the **path stem** (with `\`→`/` normalisation, so a Windows cmdline read
on a POSIX host still matches). A plain `endswith("autocue")` would **MISS `autocue.exe`** — a
false NEGATIVE, the dangerous direction: a real server slips through → two writers on the library.
```
DETECT  autocue serve · /usr/local/bin/autocue serve · python -m autocue serve
        ★ C:\Python\Scripts\autocue.exe serve   ★ autocue.exe serve --port 7432   → True ✅
IGNORE  grep serve autocue/cli.py · pytest -k serve autocue · myapp serve · bare "serve"
        · the CLI itself                                                          → False ✅
                                                                        10/10 correct ✅
live: grep + pytest -k serve + 'myapp serve' + the CLI's own pid all running  → False ✅
FAIL-SAFE: psutil ImportError → True (refuses) · process_iter raises → True (refuses); both WARN.
           Never fail-open. ✅
```

## 5 · BACKUP-ABORT · EXIT CODES · DRY-RUN
```
--dry-run           → exit 0 · previews "Mix Out Loop 04:42–04:54 (8 bars)" ·
                      DB byte-identical · NO backup taken                       ✅
backup failure      → exit 1 · "backup failed — aborting, no loops written" ·
                      DB byte-identical → NOTHING written                        ✅
failed write (RO)   → exit 1 · "Loop write failed … — rolled back" ·
                      OperationalError surfaced, not swallowed                    ✅
clean run           → exit 0                                                      ✅
```

## 6 · REGRESSION
```
files changed : autocue/cli.py · autocue/db_writer.py · 2 test files
writer.py / analysis/ / serato_writer.py : 0 files changed
XML output      : BYTE-IDENTICAL to main ✅
--serato output : IDENTICAL to main      ✅
loop generation : unchanged              ✅

FULL SUITE                                       → 1580 passed, 8 skipped, 0 failed (exit 0)
test_loops_db_write_guard + test_serve_single_writer → 30 passed
```

## 7 · FINAL VERDICT — ✅ **PR #264 GATE-2 GREEN. Ship it.**

| guard | proven live |
|---|---|
| `autocue serve` single-writer (any port, incl. `--port 3004`) | ✅ refuses · exit 1 · 0 bytes · **no backup taken** |
| Rekordbox self-lock (`ed44ac1`) — must still WRITE | ✅ exit 0 · backup · **real loop row in the DB** |
| serve asked BEFORE Rekordbox (honest message) | ✅ empirically justified — the lock probe would misattribute |
| `.exe` detection (false-negative = a missed writer) | ✅ 10/10 matcher cases |
| no false positives (grep / pytest / unrelated / own pid) | ✅ |
| fail-safe (unresolvable probe → refuse) | ✅ never fail-open |
| backup-abort · exit codes · dry-run | ✅ all per contract |
| XML / `--serato` / loop generation | ✅ unchanged |

**Nothing was dropped in the fold.** Both guards live in one pre-open preflight, in the correct
order, and neither can be reached after a backup is taken. No reds.

STATUS: DONE — PR #264 FINAL GATE-2 GREEN. The fold dropped NOTHING: both guards PROVEN LIVE. (1) serve on :3004 (non-default) → --loops REFUSED, exit 1, DB byte-identical, NO backup taken, message names the SERVER. (2) serve stopped + Rekordbox closed → --loops WRITES (exit 0, backup printed, "Loops: 1 written", real row LOOP InMsec=209440 OutMsec=223840 ActiveLoop=0) — the ed44ac1 self-lock fix survived the fold. (3) ORDERING proven empirically: with the serve up, rekordbox_is_running(COPY)=True (the serve holds the lock) though Rekordbox is NOT running — asked second it would misattribute; the fold asks SERVE FIRST. (4) .exe false-negative FIXED via path-stem matching (10/10 matcher cases; autocue.exe serve now DETECTED — a miss would let a real server through); no false positives live (grep/pytest/unrelated/own pid); FAIL-SAFE refuses on ImportError + probe exception (never fail-open). (5) --dry-run writes nothing + no backup; backup failure → exit 1, nothing written; failed write → exit 1, rolled back. (6) REGRESSION: XML byte-identical, --serato identical, loop gen unchanged, writer/analysis/serato untouched; full suite 1580p/8s/0f; guard tests 30p. Live master.db never touched.
<!-- PR264-FINAL -->
>>>>>>> Stashed changes

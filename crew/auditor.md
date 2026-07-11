# AUDITOR — P5 ADVERSARIAL REVIEW · AUTOLOOPS INCREMENT 1

**Diff under review:** `git diff 81f4963..eca754c` (5 INC-1 commits: models.py, analyzer.py,
serato_writer.py, db_writer.py, cli.py + tests/test_autoloops.py + tests/test_models.py).
**Lenses:** /code-review + /security-review + silent-failure-hunter, tried to BREAK the change
(concurrency, malformed input, destructive/irreversible ops, data corruption, swallowed errors).
**Inputs read:** crew/implementer.md · crew/DESIGN.md (F1–F7 + R-NC3/R-NC4/R-NC8) ·
crew/researcher.md §1 (via DESIGN §3) · crew/test-designer.md (coverage map).

Bottom line: **the data-safety spine the coordinator flagged as highest priority is SOUND** — F1
loop-preservation works across ID3/FLAC/MP4, R-NC4 layering is correct, OutMsec read-back is
correct, the Serato LOOP bytes round-trip, and the CUE path is byte-identical with no loops. But two
**headline loops silently never get generated** (Outro + Build), so the feature ships half-working.
→ **NEEDS-FIX** (2 Important; both feature-completeness, not corruption/crash).

---

## Coordinator priority axes — verdicts (all SOUND)

**(1) F1 data-loss — PASS (strengthened).** `write_serato_tags` (serato_writer.py:368-377) reads the
file's existing LOOP entries via `_existing_loop_entries` → `_read_existing` → `_decode_marker_tag`
and re-emits each as `raw` framed bytes verbatim (`build_markers2 preserve=`, serato_writer.py:145-149).
Traced all three containers: GEOB (`parse_markers2` on raw), FLAC and MP4 (`_ENVELOPE` unwrap →
`parse_markers2`) — all decode-then-reframe correctly. Per-entry `raw` is captured even for a LOOP
whose fixed fields don't decode (`length < 20` → dict still has `type`+`raw`, serato_writer.py:201-202),
so preservation is robust to field-layout surprises. A generated loop that collides on **start**
with an existing loop is dropped (serato_writer.py:374-376) — DJ wins. **Bonus:** preservation fires
even on a plain `--serato --overwrite` *without* `--loops`, so the pre-existing silent wipe of DJ
loops on overwrite is now fixed. No path found where a preserved loop is lost or a generated loop
clobbers a DJ loop at the same start.

**(2) Silent-failure (ANLZ/PQTZ) — PASS.** `analyze_loops` (analyzer.py:344) defers to the SHARED
`_get_pssi_and_pqtz` (analyzer.py:198-235), which is not a bare swallow — it catches the parse
exception and **falls back to the resilient scanner** (`_get_anlz_tags_resilient`) before yielding
None. Identical ladder to `analyze_track`/`analyze_fills`, per DESIGN G1/F3 ("reuse the existing
try/except ladder"). `analyze_loops` returning `[]` on `pssi/pqtz is None` or an unusable beat grid
is the legit "no bar alignment ⇒ no loop", not a swallowed error. No new silent ANLZ failure
introduced. No client/db call that returns an error value is ignored on this path.

**(3) R-NC4 layering — PASS.** CLI mirror path (cli.py:220-235): `existing or generated` was the old
wholesale-drop; now `cues = existing` (or `generated`), then `if args.loops: cues = _merge_loops(cues,
loops)`. `_merge_loops` (cli.py:114-124) ADDS generated loops even when the track already has hot cues,
dropping only those whose `position_ms` collides with an existing entry's start — existing wins,
never overwritten. An overlapping-but-different-start generated loop cannot stomp a DJ loop: the DJ
loop is re-emitted verbatim via `preserve`, and both coexist as independent Serato loop slots. R-NC4
"LAYER" default correctly implemented.

**(4) read_hot_cues OutMsec — PASS.** db_writer.py:170-186. `out_ms = int(out_raw) if out_raw is not
None else -1`; `loop_end_ms = out_ms if out_ms > in_ms else None`. Point cue (OutMsec NULL→-1, 0, or
-1 sentinel) → `loop_end_ms=None` → stays `is_loop=False`. Strict `>` means a zero-length
OutMsec==InMsec is a point (correct, no off-by-one). `BeatLoopSize→loop_beats` guarded `>0 else None`.
Sane. (Minor: `loop_beats` unit = raw BeatLoopSize here vs `bars×4` in `plan_loops`, but `loop_beats`
is unused in the write path — no output impact. See N4.)

**(5) Serato bytes — PASS.** Framing `b"LOOP\x00" + uint32be(len(data)) + data` correct
(serato_writer.py:111). Loop index space is independent of the cue slot and starts at `base =
len(preserve)` (serato_writer.py:145-147). Memory loops `slot=-1` ARE written (loops bypass the
`slot<0 → continue` CUE guard). Name UTF-8 + NUL. `_loop_entry` offsets (start@0x02, end@0x06,
field5@0x0a, field6@0x0e, color@0x12, locked@0x13, name@0x14) match `parse_markers2` decode exactly
→ round-trips. No-`=` padding (`wrap_outer`/`build_envelope`) and legacy `Markers_` deletion operate
on the whole payload, so they cover LOOP entries. (8 middle bytes are option-b defaults — Serato
render is the GATE-2 user-verify, per Decision 3b; not an auditor call.)

**(6) Regression — PASS.** With no loops and no preserve, `build_markers2` = `[b"\x01\x01"]` + CUE
entries `sorted(points, key=slot)` + `b"\x00"` — **byte-identical** to the pre-change path (E-8/R-1).
`preserve` default is the immutable `()` (no mutable-default trap). Non-loop CuePoints default
`loop_end_ms=None` → `is_loop=False` → untouched CUE serialization.

---

## FINDINGS (≥80)

### [IMPORTANT · conf 85] Outro loop is essentially NEVER generated — the terminal phrase computes to 0 bars
**autocue/analyzer.py:379-388** (`analyze_loops._bars`) + gate at **analyzer.py:78**
(`ph_bars < _MIN_LOOP_PHRASE_BARS`).

`_bars(idx)` derives a phrase's bar-length purely from the delta to the **next** phrase. For the
**last** PSSI phrase there is no next → `next_ms is None` → `return 0` (line 387). The OUTRO is
almost always the terminal phrase, so it arrives at `plan_loops` with `ph_bars=0`, which fails the
`ph_bars < 4` entry gate (analyzer.py:78) and is skipped. Net: the **"Outro" mix-out loop — one of
the two "bread-and-butter loops every DJ wants" (DESIGN §2, top priority after Intro)** — is never
emitted for a normal track. This is masked because every `plan_loops` unit test feeds an explicit
`phrase_bars` via `_ph(...)`, and `analyze_loops`/`_bars` have **zero test coverage** in this diff
(grep: no `analyze_loops` reference in tests/test_autoloops.py). The identical 0-for-terminal logic
is harmless in `analyze_track._phrase_bars` (line 319-320) because a point cue is placed regardless
of `phrase_bars`; here the gate makes it fatal.

**Failure scenario:** `autocue --track "…" --loops --serato` on any track whose last phrase is the
outro → user gets Intro/Break loops but no Outro loop, silently. Half the headline feature is dead.

**Fix (one move):** give the terminal phrase a real length from the track duration, which
`analyze_loops` already computes (just below, at analyzer.py:397-403). Hoist `total_ms` above the
`plan_input` loop and use it as the terminal fallback:
```python
    # (compute total_ms BEFORE building plan_input)
    def _bars(idx: int) -> int:
        this_ms = phrase_ms_list[idx]
        next_ms = next((phrase_ms_list[j] for j in range(idx + 1, len(phrase_ms_list))
                        if phrase_ms_list[j] is not None), None)
        if next_ms is None:
            next_ms = total_ms          # terminal phrase → bound by track end
        if this_ms is None or next_ms is None:
            return 0
        return max(0, round((next_ms - this_ms) / bar_ms))
```
`_fit_loop_bars` already clamps the chosen length before `total_ms` (analyzer.py "clamp end before
track end"), so an outro measured this way still can't run into trailing silence. Add an
`analyze_loops` test with the outro as the last phrase asserting an "Outro" loop is produced.

---

### [IMPORTANT · conf 85] Build ("Up") loops are unreachable — R-NC8 (drop the opt-flag, default-on lowest priority) was NOT implemented
**autocue/analyzer.py:33** (`(PhraseLabel.UP, "Build", (8, 4), True)`) + **analyzer.py:73**
(`if build_only and not include_build: continue`) + **cli.py:233** (`analyze_loops(content, db)` —
no `include_build`).

DESIGN P2-refinement **R-NC8** (signed off 2026-07-11): *"Build (UP) is eligible by default at
lowest priority — the separate opt-flag is dropped … `--loops` remains the single opt-in."* The
implementation instead kept UP behind `build_only=True` + an `include_build` param defaulting to
`False`, and the CLI never passes it. Result: **Build loops can never be generated** through any
shipped path — directly contradicting the signed-off refinement. The build log
(crew/implementer.md:22-28) shows the implementer built to the *original* §2 "⚙️ optional flag"
wording and missed R-NC8; the test-verifier already flagged this as an R-NC8 parity MISMATCH
(BOARD 02:52:23). Impact is bounded (Build is lowest priority under cap=4, so it only surfaces when
<4 of Intro/Outro/Break qualify), which is why it isn't higher severity — but it's a real divergence
from the approved design and a dead code branch.

**Failure scenario:** a track with a strong Build phrase but no eligible Intro/Outro/Break → user
expects (per R-NC8) a "Build" loop and gets nothing.

**Fix:** drop the `build_only` gate per R-NC8 — make UP a normal (lowest-priority) category:
`(PhraseLabel.UP, "Build", (8, 4), False)` and delete the `include_build` short-circuit (or, if the
coordinator prefers to KEEP the flag as a deliberate re-scope, reconcile DESIGN R-NC8 to match and
say so — the divergence must be resolved one way, not left latent). Confirm intent with coordinator.

---

## SUB-THRESHOLD NOTES (not blocking; recorded for completeness)

- **N1 · conf 60 — loop-index collision with preserved DJ loops.** `build_markers2` indexes
  generated loops from `base = len(preserve)` (serato_writer.py:145), assuming preserved loops
  occupy indices `0..len-1`. If a DJ's loops sit in non-contiguous high slots (e.g. indices 2 and 5,
  `len(preserve)=2`), a generated loop also gets index 2 → two LOOP entries share index 2. Both sets
  of bytes survive in the file (no data loss), but Serato *may* display only one slot. Serato-behaviour
  dependent → GATE-2 user-verify territory (F2/F7). If cheap, index generated loops past
  `max(existing index)+1` instead of `len(preserve)`.

- **N2 · conf 70 — silent whole-tag decode failure wipes DJ loops on `--overwrite`.**
  `_existing_loop_entries` (serato_writer.py:257-260) and `_decode_marker_tag` (serato_writer.py:239-242)
  swallow every exception → `[]` with **no breadcrumb**. If a file genuinely has a Serato loop tag
  our parser can't decode wholesale (base64 corruption / envelope our parser doesn't recognise), an
  `--overwrite` write replaces the whole Markers2 tag with `preserve=[]` → DJ loops lost, silently.
  Real-world trigger is unlikely (a tag WE wrote always round-trips; per-entry `raw` preservation
  covers field-layout drift — see F1 PASS), so it's below the bar — but it is exactly the
  silent-failure the DESIGN SILENT-FAILURE lens warned about. Cheap hardening: log a one-line
  warning when `_read_existing` finds a v2 tag but `_decode_marker_tag` yields zero entries or raises.

- **N3 · info — mirrored RB hot-slot loops now export as Serato LOOP even without `--loops`.**
  `read_hot_cues` (db_writer.py) now always carries `OutMsec`, so a Rekordbox hot-slot saved-loop
  mirrors to a Serato **LOOP** entry (not a point CUE) on a plain `--serato`. This is DESIGN §4 (F6)
  intent (more faithful, not a regression) — noting only that the behaviour change is not gated by
  the opt-in `--loops` flag. Acceptable.

- **N4 · info — `loop_beats` unit inconsistency.** `plan_loops` sets `loop_beats = bars×4`
  (analyzer.py) while `read_hot_cues` sets `loop_beats = BeatLoopSize` (db_writer.py). If Rekordbox
  stores BeatLoopSize in beats-vs-bars differently, these disagree — but `loop_beats` is unused by
  the Serato write path (which uses `loop_end_ms`), so no output impact today. Pin the unit before
  INC-2 (XML) consumes it.

---

## Diff-under-review marker
81f4963..eca754c

P5-AUTOLOOPS-INC1

STATUS: NEEDS-FIX
- [IMPORTANT · conf 85] Outro loop never generated (terminal phrase → 0 bars) — analyzer.py:379-388 + :78
- [IMPORTANT · conf 85] Build loops unreachable — R-NC8 not implemented — analyzer.py:33,:73 + cli.py:233
- Notes N1(60)/N2(70)/N3/N4 below threshold; F1 data-safety + R-NC4 + OutMsec + Serato bytes + CUE regression all SOUND.

---

# P5-RE — RE-REVIEW OF THE P4-FIX DIFF (`git diff 7b0fd81..5d51872`)

Scope: the 6 fix commits ONLY (analyzer.py, cli.py, serato_writer.py + tests/test_autoloops.py).
Confirmed each fix is correct AND introduces no new ≥80 problem; regression-checked the INC-1
data-safety spine. **All clear.**

## Fix-by-fix verdicts

**FIX-1 · Outro terminal-bars (`analyzer.py` `_bars` total_ms fallback) — CORRECT.**
`total_ms` is now computed (analyzer.py:392-399) BEFORE `_bars` is defined/used (my prior suggested
ordering); terminal phrase gets `next_ms = total_ms` (analyzer.py:409-410). Edge cases traced:
- **Single-phrase track:** `_bars(0)` → next generator empty → `next_ms=None` → `total_ms`; if known,
  real bars, else 0 → skip. No crash.
- **total_ms None:** terminal `next_ms=None` → `return 0` → skip (same safe fallback as pre-fix). Only
  non-terminal phrases (real next) still loop. Covered by `test_terminal_phrase_without_duration_still_safe`.
- **total_ms 0:** unreachable — `float(length) > 0` guard leaves it `None` (never 0). (Even if 0,
  `bars=round(neg)→max(0,·)=0→skip`.)
- **phrase starts after total_ms:** `this_ms > total_ms` → `round(negative)` → `max(0,·)=0` → skip. No
  negative/oversized bar count.
- **Clamp still bounds loop end ≤ track end:** `_fit_loop_bars` unchanged — still requires
  `pos_ms + bars*bar_ms ≤ total_ms` (analyzer.py). Even when `round()` inflates `phrase_bars` above the
  true outro length, the clamp rejects the too-long candidate and steps down (16→8→4). Test
  `test_terminal_outro_produces_outro_loop` asserts `32_000 < loop_end_ms ≤ 50_000` (track end). No run
  into silence.

**FIX-2 · R-NC8 (Build default lowest-pri, `include_build` deleted, cap=4) — CORRECT.**
`_LOOP_CATEGORIES` is now 3-tuples with UP present (analyzer.py:29-34); the `build_only`/`include_build`
short-circuit is gone; `include_build` removed from both `plan_loops` and `analyze_loops` signatures.
Grep confirms **zero** `include_build`/`build_only` left in `autocue/`, and both `analyze_loops`
callers (cli.py:208 dry-run, cli.py:247 real path) match the new arity — **no dead branch, no broken
caller.** Cap: `loops = loops[:4]` applied on the priority-ordered list BEFORE the position sort
(analyzer.py:103-104) → keeps highest-priority-first, then timeline-sorts. Because it's one-loop-per-
section across exactly 4 categories, total is inherently ≤4, so Build (4th/lowest) always fits when it
qualifies and the cap is a non-binding safety belt (correct, not a bug). Priority order intact.
Covered: `test_build_eligible_by_default_rnc8`, `test_build_surfaces_when_few_higher_priority_qualify`
({Intro,Build}), `test_cap_four_with_build_default`, `test_never_exceeds_cap_of_four` (≤4).

**FIX-6 · N1 loop-index `max(existing)+1` (`serato_writer.py` `_next_loop_index`) — CORRECT.**
`raw[10]` is the framed loop-index byte (LOOP\0[5] + u32 len[4] + data; data[1]=index=raw[10]);
`len(raw) >= 11` guards the read; empty preserve → `max_idx=-1` → base 0 (identical to the old
`len(preserve)=0`, so no-preserve goldens/round-trip unchanged — `test_no_preserve_indexes_from_zero`
→ [0,1]). Generated loops now index past the HIGHEST preserved index, so a DJ loop in a non-contiguous
high slot can't share an index (`test_generated_index_past_max_existing_no_collision`: DJ0=0, DJ2=2,
Outro=3, all unique). **F1 preserve intact** — preserved raws are still appended byte-for-byte
verbatim (serato_writer.py:166-167), no DJ-loop byte loss. **Serato LOOP byte layout 0x0a–0x12
UNCHANGED** — `_loop_entry` body untouched; only the 0x01 index *value* changed (field5=ffffffff,
field6=0027aae1, color=00, locked=00 all byte-identical, per implementer proof).

**FIX-3/5 · Breadcrumbs (`analyzer.py` + `serato_writer.py`) — CORRECT, logging-only.**
`analyze_loops` warns only on the two grid-missing `return []` paths (analyzer.py:369, :383); the
"no eligible phrase" path stays silent (plan_loops returns [] with no log) — the exact
real-failure-vs-legit distinction DESIGN's silent-failure lens asked for. `_existing_loop_entries`
warns `if not entries` then returns the SAME comprehension value (serato_writer.py:281-297). **No
control-flow change on any path** — a real failure still returns `[]`/preserves-nothing, just logs.
Covered: `test_missing_anlz_logs_breadcrumb`, `test_no_eligible_phrase_is_silent`,
`test_undecodable_v2_tag_warns`, `test_valid_loop_tag_does_not_warn`, `test_no_existing_tag_is_silent`.

**FIX-4 · Dry-run preview (`cli.py`) — CORRECT, no write path.**
The preview lives inside `if args.dry_run:` and runs BEFORE the "Dry run — no files written." print +
`return` (cli.py:202-215). It only calls the read-only `analyze_loops` and `print`s — no
`write_serato`/`write_serato_tags`/`write_xml` reachable. Gated by `if args.loops`. Test
`test_dry_run_lists_loops_and_writes_nothing` spies `write_serato` → `[]` (asserts zero writes);
`test_dry_run_without_loops_flag_no_loop_preview` confirms no preview without `--loops`.

## INC-1 data-safety spine — REGRESSION CHECK (intact)
- **F1** — preserve mechanism unchanged except the improved index base; raws re-emitted verbatim. ✓
- **R-NC4** — `_merge_loops` + the mirror path are NOT in this diff (cli.py change is +14 lines, the
  dry-run block only; the real `--serato` path untouched). ✓
- **OutMsec** — `db_writer.py` is NOT in this diff at all. ✓
- **CUE byte-identity** — `build_markers2` CUE branch unchanged; the only edit is the LOOP `base`
  index, which affects nothing when there are no loops → CUE-only payload byte-identical (E-8/R-1). ✓

## Sub-threshold notes (not blocking)
- **conf 55 — dry-run preview shows RAW `analyze_loops` output, not the merged/collision-filtered
  set.** The real write applies `_merge_loops` (drop start-collision vs existing cues) and the
  file-loop collision filter; the preview applies neither, so on a (rare) start-collision it could
  list a loop that won't actually be written. Preview-vs-reality cosmetic only; no write/data impact.
- **conf ~40 — `_next_loop_index` skips a preserved LOOP raw shorter than 11 bytes** (guard
  `len(raw) >= 11`); a malformed sub-11-byte LOOP could then be missed in the max and collide. A real
  Serato loop is ≥20+name bytes, so this is theoretical; DJ bytes still survive regardless. Noted only.
- **conf ~35 — N2 warning also fires on a legitimately EMPTY-but-present v2 tag** (`if not entries`),
  a benign false-positive log line. Cosmetic.

## Diff-under-review marker
7b0fd81..5d51872

P5-RE-AUTOLOOPS

STATUS: DONE (clear)
- FIX-1 Outro total_ms fallback — CORRECT (single-phrase / None / post-end / clamp≤end all safe)
- FIX-2 R-NC8 Build+cap=4 — CORRECT (no dead branch; Build always surfaces when eligible; ≤4 honored)
- FIX-6 loop-index max+1 — CORRECT (no collision, F1 verbatim-preserve intact, 0x0a-0x12 unchanged)
- FIX-3/5 breadcrumbs — CORRECT (logging-only, no control-flow change)
- FIX-4 dry-run preview — CORRECT (read-only, writes nothing)
- INC-1 spine (F1/R-NC4/OutMsec/CUE byte-identity) — REGRESSION-CLEAN
- No new ≥80 findings; 3 sub-threshold cosmetic notes only. My prior INC-1 findings #1 and #2 both RESOLVED.

---

# P5-RE2 — RE-REVIEW OF THE XML-WIRING-FIX DIFF (`git diff 5d51872..37308e3`)

Scope: 2 commits, `cli.py` only (`_merge_loops` collision-rule change + XML-branch loop wiring +
dry-run preview tweak) + `tests/test_autoloops.py`. **All clear** — the semantic change is *more*
correct than INC-1 for both paths, F1/R-NC4 hold, and the XML path now actually writes loop marks.

## (1) `_merge_loops` new collision rule — CORRECT (and fixes a latent INC-1 bug)
The drop set changed from **all cue starts** to **existing-LOOP starts only**:
`loop_starts = {c.position_ms for c in cues if getattr(c, "is_loop", False)}` (cli.py:90). Traced every
requirement:
- **DJ/existing saved LOOP still wins (F1):** `_merge_loops` does `merged = list(cues)` then only
  **appends** — it never removes an element of `cues`, so an existing loop is never overwritten; a
  generated loop landing on its start is dropped (`in loop_starts`). `test_drops_loop_colliding_with_existing_loop`
  (DJLoop wins). ✓
- **Generated loop COEXISTS with a hot/point cue at the same downbeat:** a point cue has
  `is_loop=False` → not in `loop_starts` → the generated loop is kept. `test_loop_coexists_with_point_cue_at_same_position`
  (→ {Cue, Outro}). This is the XMLWIRE root-cause fix: memory loop (Num=-1) and hot cue (Num 0-7) are
  distinct Rekordbox objects. ✓
- **Two generated loops at one start dedupe:** the first is appended and its start added to
  `loop_starts`, so the second is dropped (`test_two_generated_loops_same_start_deduped` → 1). No dup
  index/entry. ✓
- **Can a generated loop clobber/duplicate an existing loop?** No. No `cues` element is ever removed
  (no clobber); a generated loop at an existing-loop start is dropped (no dup); at a *different* start
  it coexists (distinct object, not a duplicate). ✓
- **Bonus — retroactively fixes INC-1 Serato R-NC4.** Under the OLD rule, a mirrored track's existing
  hot cues sit on phrase downbeats and generated loops sit on the *same* downbeats → every generated
  loop was dropped, so R-NC4 layering silently didn't fire on already-cued libraries. The NEW rule
  (collide only vs existing LOOPs) is exactly R-NC4's "add loops in loopless sections, never overwrite
  an existing loop." Strictly better; F1 intact.

## (2) XML branch wiring — CORRECT
`if args.loops:` now runs `analyze_loops` + `_merge_loops(cues, loops)` per track before `write_xml`
(cli.py:279-289); `else:` keeps the prior loop-free path.
- **Sources GENERATED cues (not the read_hot_cues mirror) — consistent.** The XML path has always
  exported AutoCue's generated cues for *import* into Rekordbox (mirror-first / F1 file-preservation is
  a Serato *file-rewrite* concern; XML has no existing file tag to preserve — Rekordbox handles the
  import merge). Layering generated loops onto generated cues is the right, self-consistent behavior.
  ✓
- **Loops + cues both reach `write_xml`.** `merged` = generated cues + non-colliding generated loops;
  generated cues are point cues (`is_loop=False`) so no generated loop is dropped → all loops land.
  `test_xml_path_writes_loop_marks_with_loops` asserts a real `POSITION_MARK Type="4" Num="-1"
  End=18.0 Name="Outro"`; `test_xml_loop_coexists_with_cue_at_same_downbeat` asserts Type 0 AND 4 both
  present. ✓
- **No double-analyze, no None-guard gap.** dry-run / serato / xml are mutually exclusive early-return
  branches → `analyze_loops` runs once per track per run. `analyze_loops` always returns a **list**
  (never None); `_merge_loops(cues, loops) if loops else cues` short-circuits the empty case; `added +=
  len(merged) - len(cues)` is ≥0 and accurate (merge only appends). ✓
- **Regression guard tested:** `test_xml_path_no_loop_marks_without_flag` — no Type=4 without `--loops`,
  the cue (Type=0) still written. ✓

## (3) Serato path shares `_merge_loops` — NO REGRESSION (improvement)
The Serato write block (`_merge_loops(cues, loops)` on the read_hot_cues mirror) is unchanged in this
diff but inherits the new semantics. Analyzed both mirror cases: (a) existing hot cues, no loops →
generated loops now correctly layer (was broken); (b) existing RB loop at X → generated loop at X
dropped (F1), others kept. Both better; F1 honored. File-level F1 in `write_serato_tags`
(`_existing_loop_entries` preserve + collision drop) is untouched, so the second F1 layer still holds.
No new clobber/duplication path (the DB-mirror-loop vs file-preserved-loop dedup lives in
`write_serato_tags`, unchanged). ✓

## (4) Deleted stale note + "N loops added" print — BENIGN
The "--loops writes only with --serato" note is removed (now false — XML writes loops); replaced by
`Wrote {output} — {added} named loop(s) added` (accurate, `added` counts real net loops). ✓

## Regression — INC-1/INC-2 spine intact
`serato_writer.py`, `db_writer.py`, `writer.py`, `analyzer.py`, `models.py` are **not in this diff** —
INC-1 F1/OutMsec/CUE byte-identity and INC-2 XML loop-mark encoding are untouched; this diff only
*wires* them. The old `_merge_loops` test was **replaced** by three sharper tests (not deleted to hide
a regression), and the XML path gained real end-to-end coverage. ✓

## Sub-threshold notes (not blocking)
- **conf 45 — dry-run preview uses the `generated` base, not the Serato read_hot_cues mirror base.**
  So `--loops --serato --dry-run` on a track that has an *existing RB loop* could preview a loop the
  real Serato write would drop (F1). The XML path matches its preview exactly; only the Serato+existing-
  loop case can diverge, and it's a display-only detail (this diff already improved the preview from raw
  policy output → merged set, resolving my prior P5-RE conf-55 note). No write/data impact.

## Diff-under-review marker
5d51872..37308e3

XMLWIRE-RE-AUTOLOOPS

STATUS: DONE (clear)
- `_merge_loops` collide-vs-existing-LOOP-only — CORRECT (F1 loops win, coexist w/ point cue, dedupe generated); also fixes latent INC-1 Serato R-NC4
- XML branch wiring — CORRECT (generated-cue source consistent; loops+cues both reach write_xml; no double-analyze / None gap; real e2e tests)
- Serato path via shared `_merge_loops` — improvement, F1/R-NC4 intact, no regression
- deleted note + "N loops added" print — benign/accurate
- INC-1/INC-2 spine (serato_writer/db_writer/writer/analyzer/models all outside diff) — REGRESSION-CLEAN
- No new ≥80 findings; 1 sub-threshold display-only note (conf 45)

---

# P5-INC3 — ADVERSARIAL REVIEW · DB-DIRECT LOOP WRITE (`git diff 37308e3..fb218f3`)

⚠️ This diff MUTATES the user's real Rekordbox library. I reviewed it as a destruction exercise.
Scope: `db_writer.py` (+`write_loops_to_db`, `autocue_serve_is_running`), `cli.py` (`--write-db`),
`tests/test_autoloops.py`. **The no-clobber safety case HOLDS and is genuinely well-built.** But the
**single-writer guard is bypassable by the app's own default behaviour** (Critical), and two further
Important issues. → **NEEDS-FIX**.

## The safety case — verdicts

**(1) NO-CLOBBER — PASS (proven by construction).**
- `git diff` shows **ZERO deleted lines** in `db_writer.py` (whole diff = 588 insertions, 0 deletions).
  `write_cues_to_db` is therefore **byte-identical** → the shared `/api/apply` + SSE + `memory_cue_mode`
  path **cannot** regress. ✓
- `write_loops_to_db` (db_writer.py:202-290) contains **no `.delete()`, no `.update()`, no
  `synchronize_session`** (grepped). It only `session.add(...)`s. Existing rows are *read* once (the
  `existing_starts` query) and never mutated. ✓
- **No ORM cascade/orphan risk:** rows are built as `DjmdCue(ContentID=content.ID, …)` — a **scalar FK**,
  not a relationship-collection replacement (`content.Cues = [...]` would orphan/delete children). This
  is the safe pattern. ✓
- Hot cues (Kind 1-8) are never queried, never touched; the input is filtered to
  `c.is_loop and c.slot == -1` (memory loops only). ✓
- Tests back this with a **real SQLite DB**: `test_existing_memory_cues_survive_byte_identical`,
  `test_hot_cue_untouched`, plus a NEGATIVE mirror test
  (`test_write_cues_to_db_overwrite_deletes_memory_cues`) that documents exactly why a separate
  append-only writer was required. Excellent.

**(2) Collision/idempotency — PASS.** `existing_starts` = every existing `Kind==0` `InMsec` for the
track (memory cues **and** memory loops). A generated loop on an occupied start is **skipped + logged**
(db_writer.py:236-243) — it can never overwrite a DJ entry (and there is no UPDATE anywhere to
overwrite *with*). Re-run: the previously-written loop's start is now in `existing_starts` → skipped →
`test_rerun_adds_zero_rows`. ✓ **Clamp-changed end:** a re-run whose loop has the same start but a new
end still collides *on start* → skipped → **no duplicate row** (the old row keeps its end). ✓
**Float/int InMsec:** safe — Python hashes `10000.0 == 10000`, so set membership matches across types. ✓
**Two generated loops at one start:** `existing_starts.add(loop.position_ms)` after each append dedupes
within the batch. ✓

**(3) Transaction safety — PASS.** `begin_nested()` (SAVEPOINT) → adds → `sp.commit()` →
`session.commit()`; on any exception → `db.session.rollback()` → `logger.exception` → **`raise`** (not
swallowed). Identical to the shipped `write_cues_to_db` pattern. A mid-loop failure leaves **nothing**
written for that track (adds are pending until the savepoint releases). No other pending session state
exists in the CLI, so the session-wide commit/rollback is safe here. ✓ *(Across tracks the CLI commits
per-track, so an abort mid-library leaves earlier tracks written — benign: append-only, idempotent
re-run, and the backup path was already printed. See note N1.)*

**(4) Guards — order is CORRECT; one is BYPASSABLE (Finding #1).** Order verified in cli.py:237-296:
`--loops` gate → Rekordbox-running abort → `autocue serve` abort → `db_path.exists()` → **backup-or-abort**
→ print backup path → **only then** the write loop. **No path writes before or despite a failed backup**
(`backup_database` raises on `shutil.copy2` failure; the CLI catches → stderr → `sys.exit(1)`;
`test_backup_failure_aborts_before_any_write`). ✓ `--write-db --dry-run` writes nothing — the dry-run
block `return`s *above* the write-db block. ✓ `--write-db` without `--loops` → exit 1. ✓

**(5) Columns/units — PASS (exact parity + independently confirmed).** Same 19 columns as
`write_cues_to_db`; `InFrame`/`OutFrame` both `int(round(ms * 150.0 / 1000.0))` — the identical
150-sub-frames/sec conversion (db-constraints.md:25). `Kind=0` (memory), `OutMsec=loop_end_ms` (ms),
`ActiveLoop=0` = saved-but-**unarmed** (`1` would arm it — researcher.md:364), **`BeatLoopSize=loop_beats`
= BEATS, CONFIRMED** (researcher.md:365; `plan_loops` sets `loop_beats = bars × 4`) — this resolves my
INC-1 note N4. `ID=str(db.generate_unused_id(DjmdCue))`, `UUID=str(uuid4())`, `ContentUUID` — all per
db-constraints.md:25. Neither writer sets USN/timestamp columns, so there is no bookkeeping gap vs the
shipped path. ✓

**(6) Silent-failure — PASS (within the diff).** Skips are logged (`logger.info` breadcrumb); write
failure logs + **raises**; backup failure aborts loudly; missing `_db_dir` aborts loudly. No swallowed
exception on the write path. ✓

---

## FINDINGS

### [CRITICAL · conf 95] The `autocue serve` single-writer guard probes ONLY port 7432 — the server routinely runs on a different port, so the guard silently fails to fire
**autocue/db_writer.py:302-317** (`autocue_serve_is_running(port=AUTOCUE_SERVE_PORT=7432)`) called at
**autocue/cli.py:274**.

The guard is a TCP probe of `127.0.0.1:7432` only. But `serve()` (**autocue/serve/app.py:87-106**)
**auto-switches to the next free port** when 7432 is busy:
```python
        # Try the next 9 ports before giving up
        for alt in range(port + 1, port + 10):
            if _port_is_free(alt):
                print(f"\n  Port {port} is in use — switching to {alt}")
                port = alt
```
…and `autocue serve --port N` accepts any port (cli.py:119). So a perfectly normal running server on
**7433–7441** — or any explicit `--port` — is **invisible to the guard**, which returns `False`
("not running") and lets `--write-db` proceed. The CLI then writes `master.db` while the server holds
its own read-write handle: **the single-writer rule the guard exists to enforce is violated**, which is
precisely the hazard the function's own docstring says it prevents. This is not hypothetical — *this
crew session itself* started a server on `:3004` (BOARD 02:43:24).

**Failure scenario:** user has `autocue serve` open (it silently landed on 7433 because 7432 was taken,
or they passed `--port`), runs `autocue --loops --write-db` → guard passes → concurrent writers on
master.db → "database is locked" failures, a stale server session, or interleaved writes on the user's
real library. The backup is the only thing standing between this and data loss.
`test_aborts_when_autocue_serve_running` monkeypatches the function to `True`, so it exercises the abort
branch but **cannot** catch this — the defect is in the probe itself.

**Fix (mirror the Rekordbox guard, which is already process-based):** don't trust a single port. Scan
for the process — this catches every port:
```python
def autocue_serve_is_running(port: int = AUTOCUE_SERVE_PORT) -> bool:
    try:                                    # fast path: the default port
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        pass
    try:                                    # any port: is an `autocue serve` process alive?
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if "autocue" in cmd and "serve" in cmd:
                return True
    except Exception:
        pass
    return False
```
Belt-and-braces alternative (more robust, no psutil dependency): have `serve()` write
`~/.autocue/serve.lock` (pid + port) on startup and remove it on exit; `--write-db` refuses when the
lockfile names a live pid. At minimum, probe the whole `7432..7441` fallback range.

---

### [IMPORTANT · conf 88] The loops INC-3 writes are silently bulk-deleted by the (correctly untouched) `write_cues_to_db(overwrite=True)` — i.e. by the web UI's own Apply button
**autocue/db_writer.py:625-631** (inside the untouched `write_cues_to_db`):
```python
        if write_memory:
            (db.session.query(DjmdCue)
                .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind == 0)
                .delete(synchronize_session=False))
```
`write_memory = overwrite or has_existing_memory_cues(...) == 0`. INC-3 stores its loops as **Kind=0**
rows, so **any later `overwrite=True` apply** — `/api/apply`, the SSE generate-apply stream, CLI
`--overwrite` — deletes **every Kind=0 row for that track, including the loops the user just wrote**.
Silently, with no warning.

This is **not a defect in the diff** (leaving `write_cues_to_db` untouched was the right call, and the
implementer's docstring explicitly acknowledges the Kind=0 hazard) — but it is a **system-level data-loss
the feature ships with**, and it will bite: `--write-db` prints "Open Rekordbox to see them", and the
very next Apply-with-overwrite in the web UI erases them. *(Note the same delete already destroys a DJ's
hand-placed memory cues/loops on overwrite — pre-existing, outside this diff, but it is the same root
cause and worth fixing once.)*

**Failure scenario:** `autocue --loops --write-db` → 4 named loops in the library. User opens the web UI,
clicks Apply with overwrite on that track → all 4 loops gone, no message. Recoverable only by re-running
`--write-db` (idempotent) or restoring the backup.

**Fix:** make the memory-**cue** overwrite spare memory-**loop** rows — they are a different object class,
discriminated exactly as `read_hot_cues`/`write_loops_to_db` already discriminate them (`OutMsec > InMsec`):
```python
        if write_memory:
            (db.session.query(DjmdCue)
                .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind == 0,
                        DjmdCue.OutMsec <= DjmdCue.InMsec)      # point memory CUES only — spare loops
                .delete(synchronize_session=False))
```
This touches the shared path, so it needs its own tests (and a coordinator call on whether it lands in
INC-3 or a follow-up INC-4). It must not ship unknown.

---

### [IMPORTANT · conf 85] The guard/backup `db_path` is RECONSTRUCTED, not taken from the DB actually opened — under `--db-path` it can target a different file than the write
**autocue/cli.py:256-263**:
```python
        db_dir = getattr(db, "_db_dir", None)
        ...
        db_path = Path(db_dir) / "master.db"
```
pyrekordbox opens the file passed to `MasterDatabase(args.db_path)` and sets `self._db_dir =
db_path.parent` (it does **not** retain the file path). So re-appending `"master.db"` only round-trips
when the `--db-path` basename *is* `master.db`. With `--db-path /x/copy.db`:
- if `/x/master.db` doesn't exist → `db_path.exists()` aborts (fail-safe, but `--write-db` is simply
  unusable with a non-standard filename);
- if `/x/master.db` **does** exist → the RB lock-probe and the **backup target `/x/master.db` while the
  write goes to `/x/copy.db`** → the file being mutated is **not the file that was backed up**, breaking
  the "Backup: … ^ your ONLY undo" promise printed two lines earlier.

The user's real `master.db` can never be written-unbacked this way (it isn't the write target), so this
is not catastrophic — but it silently voids the backup contract on the most dangerous path.

**Fix (one line):** use the path actually opened.
```python
        db_path = Path(args.db_path) if args.db_path else Path(db_dir) / "master.db"
```

---

## Sub-threshold notes
- **N1 · conf 50 — no per-track exception handling in the CLI write loop** (cli.py:299-309): a failure on
  track 5 propagates out of `main()` as a raw traceback; tracks 1-4 stay committed. Benign (append-only,
  idempotent re-run, backup path already printed) and failing fast on a DB write is defensible — but a
  `try/except` printing "N tracks written, backup at <path>" would be kinder than a stack trace.
- **N2 · conf 40 — `autocue_serve_is_running` `except OSError: return False`** is fail-open; folded into
  Finding #1's fix.
- **N3 · info (pre-existing, outside the diff)** — `_process_name_check` returns `False` on missing psutil
  ("fail open"), backstopped by `_db_file_is_locked` when `db_path` is passed (it is). Not a diff finding.

## Diff-under-review marker
37308e3..fb218f3

P5-AUTOLOOPS-INC3

STATUS: NEEDS-FIX
- [CRITICAL · conf 95] `autocue serve` guard probes only :7432, but serve auto-falls-back to 7433-7441 / `--port` → single-writer guard silently bypassed — db_writer.py:302-317 + cli.py:274 (vs serve/app.py:87-106)
- [IMPORTANT · conf 88] INC-3's Kind=0 loops are silently bulk-deleted by `write_cues_to_db(overwrite=True)` (/api/apply, SSE, CLI --overwrite) — db_writer.py:625-631
- [IMPORTANT · conf 85] guard/backup `db_path` reconstructed as `_db_dir/"master.db"` ≠ the file opened under `--db-path` → backup targets the wrong file — cli.py:256-263
- NO-CLOBBER (zero deletes, scalar FK, write_cues_to_db byte-identical), idempotency, savepoint/rollback/raise, backup-before-write ordering, and all column units (BeatLoopSize=BEATS, ActiveLoop=0, 150 fps frames) — all SOUND.

---

# P5RE-INC3 — RE-REVIEW OF THE INC-3 FIX DIFF (`git diff fb218f3..6cf2d8f`)

⚠️ One of these fixes edits `write_cues_to_db` — the SHARED server write path (`/api/apply`,
`/api/generate-apply`, SSE, `memory_cue_mode`). Reviewed that hardest.
**All 3 of my prior P5-INC3 findings are RESOLVED** (CRITICAL 95 serve guard · IMPORTANT 88
loop-deletion · IMPORTANT 85 db_path), and the BL-1 blocker is fixed with a genuinely load-bearing
pin. But F3 fixed only **half** of the Kind=0 conflation — the sibling *count* function still
conflates loops with memory cues, on the **default** server apply path. → **NEEDS-FIX** (1 Important).

## (1) F3 — `write_cues_to_db` Kind=0 rewrite (the shared path) — CORRECT, but INCOMPLETE

**The delete change itself is sound.** db_writer.py:690-706 now filters
`DjmdCue.Kind == 0, DjmdCue.OutMsec <= DjmdCue.InMsec`:
- **Point cues deleted:** `write_cues_to_db` pins `OutMsec=-1` on *every* row it inserts, and `InMsec >= 0`
  → `-1 <= InMsec` is always TRUE → every point memory cue it ever wrote is still deleted and rewritten.
  **No duplicate memory cues.** ✓
- **Loops spared:** `OutMsec > InMsec` → predicate FALSE → the DJ's hand-placed memory loops and
  `--write-db` loops survive. ✓
- **The "NULL OutMsec is unclassifiable → spared" caveat is moot in practice:** I checked the schema —
  `DjmdCue.OutMsec` is **`nullable=False`** (INTEGER NOT NULL), so no NULL row can exist and the
  `OutMsec <= InMsec` partition is *total*. (Defensive-only; see note N3 for a consistency nit.)
- **Hot-cue behaviour unchanged:** the `Kind.in_(hot_kinds)` delete is untouched — only the `Kind == 0`
  branch changed. Pinned by `test_hot_cues_still_rewritten_slot_wise`. ✓
- **`memory_cue_mode` (none/load_only/all) delete-semantics preserved:** the mode only shapes `mem_cues`
  (the `slot == -1` list). `write_memory = bool(mem_cues) and (...)` → mode `none` ⇒ no `mem_cues` ⇒
  `write_memory=False` ⇒ **the Kind=0 delete never runs at all** (unchanged). Modes `load_only`/`all` ⇒
  point cues rewritten, loops spared. ✓
- **"Can a legit memory-cue rewrite now leave a STALE loop the user expected gone?"** — No.
  `write_cues_to_db` never *writes* a loop row (it hard-pins `OutMsec=-1`), so no row it authored can be
  spared-as-a-loop. The only spared rows are genuine loops (the DJ's, or `--write-db`'s) — exactly the
  intent. A memory-**cue** rewrite is not expected to remove **loops**. ✓
- **293 server tests:** the change only *narrows* a DELETE. Any pre-INC-3 server test asserting memory
  cues are rewritten still passes (their rows carry `OutMsec=-1` → still deleted). The only test that
  could break is one asserting a `Kind=0, OutMsec>InMsec` row gets deleted — i.e. one asserting the old
  loop-destroying bug, which cannot pre-date INC-3. New coverage:
  `TestWriteCuesToDbSparesMemoryLoops` + the renamed negative test. ✓

### → [IMPORTANT · conf 88] The COUNT side of the same Kind=0 conflation is still unfixed — `has_existing_memory_cues` counts LOOPS as memory cues, silently suppressing memory-cue writes on the DEFAULT apply path
**autocue/db_writer.py:138-144** (unchanged by this diff) — used at **db_writer.py:677**:
```python
def has_existing_memory_cues(content, db) -> int:
    return (db.query(DjmdCue)
            .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind == 0)   # ← counts LOOPS too
            .count())
...
write_memory = bool(mem_cues) and (overwrite or has_existing_memory_cues(content, db) == 0)
```
F3 taught the **delete** that a `Kind=0` loop is *not* a memory cue, but the **count** still treats it as
one — the two now use *different* discriminators for the same space. Consequence: once `--write-db`
writes loops, `has_existing_memory_cues()` returns `>0`, so `write_memory` becomes **False** and memory
cues are **silently not written**.

This lands on the **default** server path, not an exotic one: the apply routes pass
`overwrite=req.overwrite` (routes.py:1020, 1105, 1385) and the request models default
**`overwrite: bool = False`** (schemas.py:127, 140, 550). So the `has_existing_memory_cues() == 0` branch
is the one that actually runs.

**Failure scenario:** user runs `autocue --loops --write-db` (3 memory loops written; the track has **no**
memory cues). They then hit Apply in the web UI with `memory_cue_mode='all'` (default `overwrite=False`)
→ `has_existing_memory_cues()` = 3 (the loops) → `write_memory=False` → **no memory cues are written, with
no message**. The guard exists to protect *hand-placed DJ memory cues*; it is now tripped by AutoCue's own
loops, which aren't cues at all. *(Root cause is INC-3's Kind=0 storage, not this diff — but this is the
unfixed half of the exact conflation F3 addresses, and it must not ship half-done.)*

**Fix (symmetric one-liner — makes the count and the delete agree):**
```python
def has_existing_memory_cues(content, db) -> int:
    from pyrekordbox.db6 import DjmdCue
    return (
        db.query(DjmdCue)
        .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind == 0,
                DjmdCue.OutMsec <= DjmdCue.InMsec)   # point memory CUES only — a loop is not a cue
        .count()
    )
```
Add a test: a track with only `--write-db` loops + a non-overwrite apply with `memory_cue_mode='all'`
must still write its memory cues.

## (2) F1 pre-open guards + F4 db_path — CORRECT
- **No path opens the DB before the guard on `--write-db`:** `write_db_path = _preflight_write_db(args) if
  args.write_db else None` (cli.py:208) runs **before** `db = MasterDatabase(...)` (cli.py:~211). The
  `serve` subcommand returns earlier and is unaffected. ✓ This genuinely fixes BL-1 — the lock probe was
  self-detecting AutoCue's own SQLAlchemy autobegin lock.
- **F4 — guard/backup/write now target the SAME file:** `db_path = Path(args.db_path) if args.db_path else
  _default_db_path()`, and `_default_db_path()` mirrors pyrekordbox's own lookup
  (`get_config("rekordbox7") or get_config("rekordbox6")` → `db_path`). I executed it: `get_config` returns
  a real `dict` and resolves to the correct `…/Pioneer/rekordbox/master.db`, matching what
  `MasterDatabase()` opens. The reconstructed `_db_dir/"master.db"` is gone. My prior finding #3
  **RESOLVED**. ✓ A non-dict config → `None` → abort "pass --db-path" (fail-safe). ✓
- **No regression to other CLI paths:** every removed line was inside the `if args.write_db:` block, and the
  preflight is gated on `args.write_db`. ✓
- **The anti-mock ordering test IS load-bearing:** `test_rekordbox_guard_runs_BEFORE_the_db_is_opened`
  records a call-order list and asserts `order.index("rb_guard") < order.index("open_db")` (and the same for
  `serve_guard`). A mock cannot hide an **ordering** assertion — move the guard back after
  `MasterDatabase(...)` and the index comparison fails. ✓ Correctly diagnosed as the pin that would have
  caught BL-1.

## (3) F2 serve probe — CORRECT and fail-SAFE (my CRITICAL is resolved)
- **Port range:** `AUTOCUE_SERVE_PORT_RANGE = range(7432, 7442)` exactly covers `serve()`'s own fallback
  (`for alt in range(port+1, port+10)` → 7433-7441) plus the default. ✓
- **Process scan catches ANY port** (`--port 3004` etc.): matches when `"serve" in cmd` **and**
  `any("autocue" in part)`. ✓ Tests: fallback port, arbitrary-port process, `python -m autocue serve`,
  whole-range scan.
- **False positives — self not tripped:** `"serve" in cmd` is **exact list-element** membership (not
  substring), so `autocue --track "Preserve Me" --loops --write-db` does **not** match; and the current pid
  is skipped explicitly (`proc.pid == me → continue`). Double-protected.
  `test_no_false_positive_on_our_own_write_db_process`. ✓
- **FAIL-SAFE confirmed:** `psutil` ImportError → `return True` (refuse); any probe exception → `return True`
  (refuse). Ambiguous ⇒ **refuse the write, never allow**. ✓ `test_fail_safe_when_the_process_probe_raises`.

## (4) F5 per-track error handling — CORRECT, nothing swallowed
The `try/except` around `analyze_loops` + `write_loops_to_db` (cli.py:338-352) records the failing title,
prints `ERROR — {e}` to **stderr**, continues, and emits a loud closing summary to stderr: `N track(s)
FAILED: … — earlier tracks are already committed. Backup: {backup}`. The failure is **surfaced, not
swallowed** (silent-failure lens: PASS). ✓ **DB stays consistent:** `write_loops_to_db` still rolls back its
own savepoint/session and re-raises on failure, so the failing track writes nothing and the session is clean
and reusable for the next track; earlier tracks are committed (append-only + idempotent re-run). ✓

## (5) Regression — INC-3 no-clobber spine INTACT
The db_writer.py hunks touch only the serve-probe block (~293-317) and `write_cues_to_db`'s delete (~690).
**`write_loops_to_db` (202-290) is not in the diff at all** — still append-only, zero `Kind=0` deletes,
exact-InMsec idempotency, savepoint→commit / rollback→raise, and unchanged column units
(BeatLoopSize=BEATS, ActiveLoop=0, 150 sub-frames/sec). ✓

## Sub-threshold notes
- **N1 · conf 65 — `--write-db` still exits 0 when tracks FAILED.** F5 reports failures loudly but never
  `sys.exit(1)`, so a script/automation sees success on a partial DB write. One-liner: `if failed:
  sys.exit(1)` after the summary.
- **N2 · conf 55 — process-scan false positive (fail-safe direction).** Any process with `serve` as a
  standalone argv token *and* `autocue` as a substring in some arg trips it — e.g. `grep serve autocue/cli.py`
  or `pytest -k serve autocue`. Result: `--write-db` **refuses** (safe, just annoying). Could tighten by
  requiring the token *before* `serve` to end with `autocue`.
- **N3 · conf 45 — count/delete NULL-handling disagreement (cosmetic; cannot fire).** `read_hot_cues` maps
  `OutMsec IS NULL → -1 → point cue`, but the new delete filter *spares* NULL. Unreachable
  (`OutMsec` is NOT NULL), but for internal consistency the filter could be
  `or_(DjmdCue.OutMsec.is_(None), DjmdCue.OutMsec <= DjmdCue.InMsec)`.
- **N4 · conf 50 — process-scan false negative on an undocumented invocation.** `uvicorn
  autocue.serve.app:create_app --port 9999` has no standalone `serve` token → missed by the scan; the
  7432-7441 port probe only covers it if it landed in that range. Not the documented way to run the server.

## Diff-under-review marker
fb218f3..6cf2d8f

P5RE-AUTOLOOPS-INC3

STATUS: NEEDS-FIX
- [IMPORTANT · conf 88] `has_existing_memory_cues` (db_writer.py:138-144) still counts LOOPS as memory cues → on the DEFAULT apply path (`overwrite=False`) a track with `--write-db` loops silently stops getting memory cues. The unfixed COUNT half of the Kind=0 conflation F3 fixed on the DELETE side. Symmetric one-line fix.
- F3 delete-side (spares loops, deletes point cues, no dup cues, hot cues + memory_cue_mode semantics intact, OutMsec NOT NULL ⇒ total partition) — CORRECT
- F1 pre-open guards (guard→open ordering, mock-proof ordering pin) + F4 db_path (guard/backup/write = same file) — CORRECT; prior findings #85/#95 RESOLVED
- F2 serve probe (7432-7441 range + process scan, self skipped, exact-token match, fail-SAFE on any ambiguity) — CORRECT; prior CRITICAL RESOLVED
- F5 per-track errors — CORRECT, nothing swallowed, DB consistent
- INC-3 no-clobber spine (write_loops_to_db untouched) — REGRESSION-CLEAN

---

# FINAL — AUTOLOOPS CLEANUP RE-REVIEW (`git diff 6cf2d8f..7d8683d`) + BRANCH MERGE VERDICT

4 commits (cli.py + db_writer.py). My IMPORTANT(88) and both sub-threshold notes (N1 exit code, N2
false positives) are **RESOLVED**, and the fixes are better than what I asked for. One **new**
false-negative was introduced by the N2 tightening — in the single-writer guard, the exact CRITICAL
class we already closed once. → **NEEDS-FIX** (1 Important, one line).

## (1) ★ `has_existing_memory_cues` — RESOLVED, and now structurally symmetric
The fix went further than my one-liner: it extracts **one shared predicate** used by *both* halves —
```python
def _point_cue_filter():                                    # db_writer.py:138-151
    return or_(DjmdCue.OutMsec.is_(None), DjmdCue.OutMsec <= DjmdCue.InMsec)
```
consumed at **db_writer.py:168 (the COUNT)** and **db_writer.py:748 (the DELETE)** — grepped, those are
its only two consumers. The count and delete can no longer drift apart, which was the root cause. ✓
- **Our loops no longer gate the memory-cue write:** a loop (`OutMsec > InMsec`) fails the predicate →
  not counted → `write_memory` is no longer forced False. ✓
- **The `write_memory` gate still protects genuine memory CUES** (db_writer.py:677): real point cues
  (`OutMsec=-1`) still satisfy the predicate → still counted → a track with hand-placed DJ memory cues
  still refuses a non-overwrite rewrite, exactly as designed. ✓
- **`memory_cue_mode` intact:** `none` ⇒ no `mem_cues` ⇒ `write_memory=False` ⇒ the Kind=0 delete never
  runs at all (unchanged); `load_only`/`all` ⇒ gate on point-cue count only. ✓
- **It also resolved my N3:** NULL `OutMsec` is now treated as a **point cue** in *both* halves
  (`OutMsec.is_(None)`), matching `read_hot_cues` (NULL → -1 → point). The count/delete/read
  classifications are now all consistent. ✓
- **Blast radius / no remaining Kind=0 conflation** — I grepped **every** `Kind` filter in `autocue/`:
  all hot-cue queries are `Kind > 0` / `Kind 1..8` (db_writer 133/185/596/608/731; routes 580/598/1999/2386;
  bench 198) and are **structurally immune** — our loops are `Kind=0`. Only two `Kind==0` consumers remain:
  - **db_writer.py:263** (`write_loops_to_db`'s `existing_starts` occupancy check) — *intentionally* both
    cues and loops ("is this start occupied by ANY memory entry?"), and it only ever **skips** a generated
    loop. Reviewed: correct by design, fails safe. ✓
  - **analysis/quality.py:89** — see note N2 below (cosmetic, read-only).
- **Coverage:** `TestMemoryCueCountIgnoresLoops` — loops not counted · point cues still counted ·
  **`test_memory_cues_still_written_on_default_apply_when_only_loops_exist`** (my exact failure scenario). ✓

## (2) N1 exit code — CORRECT
`sys.exit(1)` fires **only** inside `if failed:` (cli.py:363-372). A clean run falls through to `return`
→ exit 0 (`test_exit_code_zero_when_all_tracks_succeed`). ✓ **No `--dry-run` interaction:** the dry-run
block `return`s far earlier in `main()`, so `--write-db --dry-run` never reaches this code and can never
exit non-zero. ✓ The "left untouched" line was also moved above the failure block — the partial-write
warning now lands last on stderr. ✓

## (3) Serve-probe-first reordering — CORRECT, refusal set unchanged
- **Rekordbox guard still fires:** if no serve is running, `autocue_serve_is_running()` returns False and
  control falls through to `rekordbox_is_running(db_path)`, which still aborts. ✓
- **No write can slip through:** both guards are still evaluated in sequence and each `sys.exit(1)`s on
  True. Reordering two short-circuiting *refusals* cannot widen the accept set — `refuse ⇔ (serve OR
  rekordbox)` is identical either way. Only the *message* changes when both would trip. ✓
- **Rationale is sound:** a running `autocue serve` holds the DB file, so it *also* trips the file-lock
  probe inside `rekordbox_is_running` — the old order refused correctly but **blamed Rekordbox for our own
  server**. Asking the specific question before the general one fixes the lie. The broadened Rekordbox
  message ("Rekordbox is running, or another app holds master.db open") is now honest about what the lock
  probe can actually prove. ✓ (`test_serve_is_blamed_not_rekordbox_when_the_server_holds_the_db`)
- **Fail-SAFE preserved:** the serve probe still returns `True` (refuse) on psutil-missing or any probe
  exception. ✓

## (4) N2 `_is_serve_cmdline` — false positives fixed, but a REAL serve can now be MISSED

**[IMPORTANT · conf 85] `_is_serve_cmdline` misses a Windows `autocue.exe serve` — a false negative in the single-writer guard**
**autocue/db_writer.py:339-355.** The tightened rule requires the token before `serve` to
`.endswith("autocue")`. On Windows the console script is **`autocue.exe`**, which does not. I probed it
live against the real function:
```
  True   ['autocue', 'serve']
  True   ['/usr/local/bin/autocue', 'serve', '--port', '3004']
  True   ['python', '-m', 'autocue', 'serve']
  True   ['/venv/bin/python3', '-m', 'autocue', 'serve', '--port', '9999']
  False  ['C:\\Python\\Scripts\\autocue.exe', 'serve', '--port', '3004']   ← REAL SERVE, MISSED
  False  ['grep', 'serve', 'autocue/cli.py']          ← false positive correctly gone ✓
  False  ['pytest', '-k', 'serve', 'autocue']         ← false positive correctly gone ✓
  False  ['autocue', '--track', 'Preserve Me', ...]   ← self/sibling correctly ignored ✓
```
The parametrized `test_still_detects_real_serve_invocations` pins the four passing forms but **not**
`autocue.exe`, so the gap is untested. **Exposure is narrow but real:** the `7432-7441` port probe runs
first and still catches a Windows serve on the default/auto-fallback range — so the hole is *Windows +
an explicit `--port` outside 7432-7441*, where **both** probes miss and `--write-db` proceeds against a
live server handle (the CRITICAL we already fixed once). macOS/Linux are unaffected (no `.exe`).

**Fix (one line — strips `.exe` natively on each platform, keeps every current match):**
```python
from pathlib import Path
...
        if tok == "serve" and i > 0 and Path(cmd[i - 1]).stem.lower() == "autocue":
            return True
```
(`Path("autocue.exe").stem == "autocue"` on Windows; `Path("/usr/local/bin/autocue").stem == "autocue"`;
`Path("autocue").stem == "autocue"` for the `-m` form — all four current cases still pass, and every
false positive above still rejects.) Add `["C:\\Python\\Scripts\\autocue.exe", "serve"]` to the
`test_still_detects_real_serve_invocations` parametrize list.

## (5) Regression — CLEAN
db_writer.py hunks touch only 135-171 (count/predicate), 336-386 (serve cmdline) and 738-750 (the delete).
**`write_loops_to_db` (202-290) is not in the diff** — append-only, zero `Kind=0` deletes, exact-InMsec
idempotency, savepoint→commit / rollback→raise, units unchanged. ✓ **INC-1 Serato** (`serato_writer.py`)
and **INC-2 XML** (`writer.py`) are not in the diff at all. ✓ cli.py changes are confined to
`_preflight_write_db`'s guard order and the exit-code block — the Serato path, XML path, dry-run and
`_merge_loops` are untouched. ✓

---

## FINAL MERGE-READINESS VERDICT — the whole branch (INC-1 + INC-2 + INC-3 + all fixes)

**Merge-ready once the one-line `_is_serve_cmdline` fix lands.** Everything else audited across five
passes is sound:

| Area | Verdict |
|---|---|
| **INC-1 Serato** — F1 loop-preserve (GEOB/FLAC/MP4, verbatim raw re-emit), LOOP byte layout + round-trip, independent loop-index space, CUE path byte-identical | ✅ SOUND |
| **INC-1 policy** — Outro terminal-bars fix, R-NC8 Build default + cap=4, ANLZ breadcrumbs | ✅ SOUND |
| **INC-2 XML** — `_merge_loops` collide-vs-existing-LOOP-only (loops coexist with point cues, DJ loop wins, generated dedupe), loop marks reach `write_xml` | ✅ SOUND |
| **INC-3 DB-direct** — append-only `write_loops_to_db` (zero deletes, scalar FK, no cascade), exact-InMsec idempotency, savepoint/rollback/**raise**, column units (Kind=0, OutMsec ms, OutFrame 150fps, **BeatLoopSize=BEATS**, ActiveLoop=0 unarmed) | ✅ SOUND |
| **INC-3 guards** — pre-open ordering (mock-proof pin), db_path = the file actually opened, backup-or-abort before any write, `--dry-run` writes nothing, non-zero exit on partial write | ✅ SOUND |
| **Shared server path** — `write_cues_to_db` Kind=0 rewrite now deletes point cues only and spares loops, via **one predicate shared with the count**; `memory_cue_mode`/hot-cue semantics intact | ✅ SOUND |
| **Single-writer guard** — port range 7432-7441 + process scan + fail-SAFE on ambiguity | ⚠️ **1-line gap (Windows `.exe`)** |

**Standing GATE-2 caveats (not defects — carry into the merge note):**
- **Serato-ACCEPTS is still NOT proven** (F2/F7). The 8 reserved/color bytes (`0x0a–0x12`) are option-b
  defaults; automated proof is only our writer↔parser round-trip. **The user must open a written file in
  Serato DJ Pro** to confirm loops render + are named.
- **Rekordbox loop-import parity** (INC-2 XML + INC-3 DB) is likewise a **user-verify** step.
- **`--write-db` mutates the real library.** The printed backup is the only undo — that contract is now
  correctly enforced (guard → backup-or-abort → write, all on the same file).

## Diff-under-review marker
6cf2d8f..7d8683d

FINAL-AUTOLOOPS

STATUS: NEEDS-FIX (1 Important, one line — then merge-ready)
- [IMPORTANT · conf 85] `_is_serve_cmdline` (db_writer.py:339-355) misses a Windows `autocue.exe serve` → single-writer guard bypassed on Windows + a `--port` outside 7432-7441. Live-probed. Fix: `Path(cmd[i-1]).stem.lower() == "autocue"` + add the `.exe` case to the parametrize list.
- ★ `has_existing_memory_cues` IMPORTANT(88) — RESOLVED, and now SYMMETRIC with the delete via one shared `_point_cue_filter()`; no Kind=0 conflation remains on any write path (only quality.py:89, cosmetic — note N2)
- N1 exit code — RESOLVED (non-zero only on real failure; no `--dry-run` interaction)
- Serve-first reordering — CORRECT (refusal set unchanged, Rekordbox guard still fires, fail-SAFE preserved)
- N2 false positives — RESOLVED (grep/pytest/vim/unrelated-`serve` all rejected, live-probed)
- Regression: write_loops_to_db + INC-1 Serato + INC-2 XML all untouched — CLEAN

### Sub-threshold notes
- **N1 · conf 55 — `analysis/quality.py:89`** (`memory_cues = [c for c in all_cues if int(c.Kind or 0) == 0]`)
  still counts `--write-db` loops as memory cues in library-health scoring. Read-only, cosmetic metric skew —
  no write/data impact. Apply `OutMsec <= InMsec` there too if the health number should exclude loops.
- **N2 · conf 40 — fail-safe message overclaims.** When psutil is missing, `autocue_serve_is_running()`
  returns True and the CLI prints "a local `autocue serve` is running" when the truth is "cannot tell".
  Behaviour (refuse) is right; the wording asserts more than it knows.
<<<<<<< Updated upstream
=======

---

# PR1 — SALVAGE PR AUDIT · `fix/loop-kind0-clobber` (11a7b13) → main

⚠️ Edits the SHARED SERVER WRITE PATH on a **live data-loss bug**, straight to `main`. I verified this
one by **execution**, not by reading. **No findings ≥80. MERGE-READY.**

## (1) ★ Discriminator correctness — PROVEN total & disjoint (executed, not argued)
I built a scratch SQLite with the **real pyrekordbox schema**, inserted every `Kind=0` corner state, and
ran the two predicates against it:

| Kind=0 row state | `_point_cue_filter()` | `_loop_filter()` | verdict |
|---|---|---|---|
| `OutMsec` = -1 (sentinel) | True | False | exactly one |
| `OutMsec` = 0 | True | False | exactly one |
| `OutMsec` == `InMsec` (degenerate) | True | False | exactly one |
| `OutMsec` < `InMsec` | True | False | exactly one |
| **`OutMsec` > `InMsec` (LOOP)** | **False** | **True** | exactly one |
| `InMsec`=0 / `OutMsec`=0 · `InMsec`=0 / `OutMsec`=-1 · loop from 0 | ✓ | ✓ | exactly one |

```
TOTAL    (point | loop == all rows): True   missing=[]
DISJOINT (point & loop == empty)   : True
```
**No row falls through BOTH or NEITHER.** ✓ I also probed NULL reachability: `InMsec` and `OutMsec` are
both **`nullable=False`** and the DDL **rejects** a NULL insert on either — so the `OutMsec.is_(None)` /
`.isnot(None)` clauses are dead defensive code. They are also *symmetric* (NULL ⇒ point in one, ⇒ not-loop
in the other), so even a hypothetical NULL lands in exactly one half. The partition is airtight.

**The writers' own output lands correctly:** `write_cues_to_db` inserts `OutMsec=-1` (db_writer.py:700) →
always matched by `_point_cue_filter` → point cues are still deleted+rewritten; `write_memory_loops`
inserts `OutMsec=end` (db_writer.py:601) → always matched by `_loop_filter`. The predicates and the
inserts agree.

## (2) No regression to memory_cue_mode / /api/apply / SSE — CORRECT
- **Point-cue rewrite is byte-for-byte the same behaviour:** every point cue AutoCue ever wrote carries the
  `-1` sentinel → still matched → still deleted and rewritten. The delete was only **narrowed** to exclude
  loops. ✓
- **"Could sparing loops leave a STALE loop the user expected removed?"** — **No.** `write_cues_to_db` never
  *writes* a loop row (it hard-pins `OutMsec=-1`), so no row it authored can be spared-as-a-loop. The only
  spared rows are genuine loops, and a memory-**cue** rewrite is not expected to remove **loops**. Loops
  remain replaceable via the loop path (`write_memory_loops(overwrite=True)` → deletes loop rows only). ✓
- **`write_memory_loops` gating on LOOPS is semantically right** (db_writer.py:564): it still refuses to
  replace the DJ's saved loops without `overwrite` (`test_existing_loops_still_block_a_loop_write_without_overwrite`),
  while a track that merely has the DJ's memory **cues** now correctly gets its loops. And the two writers'
  delete sets are **disjoint**, so a cue-apply and a loop-apply in the same session cannot interfere. ✓
- **The real prize:** `--library --loops --overwrite` no longer blanket-deletes `Kind=0` — it no longer
  destroys the DJ's hand-placed memory cues **library-wide**. That was the live bug.

## (3) `analysis/quality.py:88-101` — CORRECT, and provably harmless to scoring
`_is_memory_loop()` mirrors `_loop_filter` in Python (`OutMsec is not None and OutMsec > InMsec`). Excluding
loops from `memory_cues` is right: a loop is **not** a CDJ Auto-Cue load point. I checked the only two
consumers — `memory_cue_count` (reporting) and the `NO_MEMORY_CUE` advisory, which quality.py itself marks
**"info only, zero score impact"** (line 135). So **no health score changes**; the only user-visible delta is
that a loops-only track now *correctly* raises "No memory cue" instead of having it wrongly suppressed. No
advisory changes wrongly. ✓

## (4) Completeness — independently VERIFIED (I grepped, did not take the implementer's word)
Every `Kind` reference in the repo:
- **All four `Kind == 0` sites now carry a discriminator:** db_writer **176** (cue count → `_point_cue_filter`),
  **195** (loop count → `_loop_filter`), **583** (loop-overwrite DELETE → `_loop_filter`), **682**
  (memory-cue DELETE → `_point_cue_filter`), plus quality.py **101** (in-Python).
- **Every other site is hot-cue-only and structurally immune** (loops are `Kind=0`): db_writer 133/245/447/459,
  serve/routes 580/598/1999/2386/2448, bench/cue_accuracy 198, schemas 351 — all `Kind > 0` / `1..8` /
  `Kind.in_(hot_kinds)`.
**No remaining Kind=0 conflation anywhere — COUNT, DELETE, or in-Python.** ✓

## (5) The tests — REAL PROOFS, and they genuinely reproduce the bug
I ran the PR's test file against a temp worktree at **unfixed `origin/main`**:
```
7 failed, 2 passed          ← on main (bug reproduces)
FAILED  TestMemoryCuesSurviveLoopOverwrite / TestLoopsSurviveCueOverwrite
FAILED  TestNoSilentSuppression (both directions) / TestCounters (both) / TestNoBlanketKind0Delete
```
and on the PR branch: **9 passed**. The 2 that pass on main are `TestIntendedProtectionPreserved` — correct,
those are preservation guards that *must* pass before **and** after. **Not tautologies.** ✓
- **`generate_unused_id` IS properly stubbed** — the exact trap you flagged. `db.generate_unused_id.side_effect`
  returns real incrementing ints, with the comment *"⚠️ Without this the writers insert ID=`<MagicMock>` and
  every assertion lies."* They also stub `db.query.side_effect = session.query` (the `has_existing_*` helpers
  use `db.query`, not `db.session.query` — a bare MagicMock would make `.count() == 0` silently False). Both
  landmines seen and defused. ✓
- **Real in-memory SQLite with the real pyrekordbox schema**; the DDL relaxation is scoped to 4 columns the
  shipped writers omit, restored in a `finally`, and explicitly keeps every writer-required column NOT NULL.
- **`TestNoBlanketKind0Delete` is an excellent regression pin:** it captures the actual emitted SQL via
  `before_cursor_execute` and asserts every `Kind = ?` DELETE on `djmdCue` also constrains `OutMsec` — so a
  future blanket delete fails **even if it matches 0 rows in the fixture**. It carries an anti-vacuous
  assertion (`assert deletes, "the writers must have issued DELETEs at all"`). ✓

## (6) Silent-failure + transaction safety — CLEAN
The changed deletes sit inside the pre-existing `begin_nested()` savepoint → `try/except` →
`db.session.rollback()` → **`raise`**. Structure untouched; nothing swallowed on any write path. The only new
`except` is `quality.py`'s `(TypeError, ValueError) → False` classification fallback — read-only, info-only,
and unreachable given the NOT NULL schema (note N1).

## REGRESSION — full local gate, run this turn
```
tests/test_loop_kind0_clobber.py .........            9 passed
python -m pytest -q          →  1559 passed, 8 skipped, 4 warnings in 81.17s   (exit 0)
```
No regression to the server/apply/SSE suites.

## Sub-threshold notes
- **N1 · conf 30 — `quality.py` `_is_memory_loop` swallows `(TypeError, ValueError)` → treats a malformed row
  as a point cue**, which would *suppress* the `NO_MEMORY_CUE` advisory. Unreachable (NOT NULL schema),
  info-only, zero score impact. Cosmetic.
- **N2 · info — the NULL branches in both predicates are dead code** (DDL rejects NULL `In`/`OutMsec`).
  Harmless and symmetric; keep as defence-in-depth.
- **N3 · info (pre-existing, not a regression)** — there is no verb to *remove* loops outright; `--loops
  --overwrite` replaces them. Same as before the fix.

## MERGE VERDICT
**✅ MERGE-READY.** The discriminator is a proven total+disjoint partition; all 4 conflation sites are fixed
and no fifth exists; the shared server path's point-cue semantics are unchanged while the destructive
blanket delete is gone; the tests demonstrably fail on main and pass here, with both mock landmines defused
and a SQL-level pin against regression; the full 1559-test suite is green. This fixes a **live library-wide
data-loss bug** and should land.

PR1-AUDIT

STATUS: DONE (merge-ready — no findings ≥80)

---

# PR2 — SALVAGE PR AUDIT · `fix/loops-db-write-guard` (ed44ac1) → main

Gates a **destructive DB write**. Verified by execution, incl. running the PR's tests against unfixed
`origin/main`. **No findings ≥80. MERGE-READY.**

## (1) ★ Pre-open guard ordering + db_path identity — CORRECT
**Ordering.** `_preflight_loop_write(args)` runs at **cli.py:158-161**; `MasterDatabase(...)` is
constructed at **cli.py:165**. The guard is strictly *before* the DB is opened — no remaining path opens
it first. On unfixed main the guard sat at **cli.py:237**, long after the open at **108**: that is the
self-lock (SQLAlchemy's autobegin txn holds a SQLite lock, so `_db_file_is_locked()` detected **AutoCue's
own handle** and falsely aborted every run). Root cause correctly identified and fixed.

**The gate is exactly right.** Preflight fires on `args.loops and not args.dry_run`. The write block
(**cli.py:289**) is reachable only when `not dry_run` (`if args.dry_run: … return` at **285-287**) **and**
`args.loops` **and** `loops_by_track`. So the preflight condition is a strict **superset** of the write
block's reachability → `loop_db_path` is guaranteed non-None wherever it is consumed. There is **no**
`backup_database(None)` path and no None-deref. ✓

**All three target the same file** (guard = backup = written):
- **With `--db-path`:** preflight uses `Path(args.db_path)`; `MasterDatabase(args.db_path)` opens the same
  file; the backup takes `loop_db_path`. Identical. ✓
- **Without:** `_default_db_path()` mirrors pyrekordbox's own lookup (`get_config("rekordbox7") or
  get_config("rekordbox6") → db_path`) — exactly what `MasterDatabase()` resolves internally. Identical. ✓
- This kills main's `_db_dir / "master.db"` **reconstruction**, which would back up a *different* file than
  the one written whenever `--db-path` pointed at a non-`master.db` name. Pinned by
  `test_guard_and_backup_target_the_db_path_flag` (uses `copy.db`; asserts `rb_paths == backups ==` the
  flag path) — and that test **fails on main**. ✓

## (2) The guard STILL FIRES on a genuinely-open Rekordbox — no false-negative traded
`rekordbox_is_running()` combines a **psutil process-name probe** *or* an **exclusive file-lock probe**.
Run pre-open, **both signals still work**: psutil sees the Rekordbox process regardless of DB state, and
the lock probe sees *Rekordbox's* lock. The only thing removed is the interference from **our own** handle
(we hold none yet). So the false positive is eliminated **without** weakening detection. ✓
Pinned by `test_aborts_before_opening_the_db_when_rekordbox_runs`: `rb=True` → `SystemExit(1)`,
"rekordbox" on stderr, **`"open_db" not in calls["order"]`** (the DB was never even opened),
`backups == [] and written == []`.
*(One temporal caveat — see note N1: the check now runs at startup rather than immediately before the
write, so a Rekordbox launched **during** a long analysis isn't caught. It fails safe, not silently.)*

## (3) Backup-abort ordering — HARD ABORT, nothing written
`backup = db_writer.backup_database(db_file)` is wrapped in `try/except` → stderr message →
**`sys.exit(1)`** (cli.py:296-302), and sits **strictly before** the write loop (cli.py:308). **No path
writes before, or despite, a failed backup.** Non-zero exit, nothing written. ✓
Pinned by `test_backup_failure_aborts_with_nothing_written`.
*(Accuracy note N2: the test's docstring claims main "let the write proceed anyway, leaving the user with
NO undo" — that is **not true**. On main an exception from `backup_database` propagates and aborts before
the write loop (raw traceback, nothing written). The PR's real gain is a **graceful** abort with a clear
message instead of a traceback. The PR body should not claim a data-loss hole that didn't exist.)*

## (4) The anti-mock ordering test IS load-bearing — not a tautology (PROVEN)
The stub **records the call order** (`calls["order"]` ← `"rb_guard"`, `"open_db"`, `"backup"`, `"write"`),
then asserts:
```python
assert order.index("rb_guard") < order.index("open_db")
assert order.index("backup")   < order.index("write")
```
Mocking `rekordbox_is_running`'s **return value** (what every pre-existing test does) cannot hide an
**ordering** assertion — move the guard back after `MasterDatabase(...)` and `order` becomes
`["open_db", "rb_guard", …]`, failing the comparison regardless of what the mock returns. **Proven
empirically:** I ran the PR's test file against a temp worktree at unfixed `origin/main`:
```
6 failed, 4 passed          ← on main (the bug reproduces)
FAILED  test_rekordbox_guard_runs_BEFORE_MasterDatabase_is_constructed   ← the ordering pin
FAILED  test_loop_write_actually_happens          ← "--loops wrote nothing (the shipped bug)"
FAILED  test_guard_and_backup_target_the_db_path_flag
FAILED  test_aborts_before_opening_the_db_when_rekordbox_runs
FAILED  test_backup_path_is_printed · test_full_success_exits_zero
```
and **10 passed** on the PR branch. The 4 that pass on main are the preservation/regression guards
(non-loop paths, dry-run) plus two contract tests that main satisfies incidentally — correct, they must
pass on both. ✓

## (5) Regression — non-loop paths behaviourally IDENTICAL; nothing swallowed
- **XML / `--serato` (without `--loops`):** the preflight is gated on `args.loops`, so nothing runs at all.
  `test_non_loop_paths_unchanged` asserts `"rb_guard" not in order`. ✓
- **`--dry-run`:** preflight skipped **and** the write block is unreachable (`return` at 287).
  `test_dry_run_previews_loops_and_writes_nothing` asserts `backups == [] and written == []`. ✓
- **`--loops --serato`:** preflight runs — correct, since `--loops` always attempts the DB write (true on
  main too); the Serato block (cli.py:334) still runs afterwards, unchanged. ✓
- **Silent-failure lens: CLEAN.** The new per-track `try/except` does **not** swallow: it records the title,
  prints `ERROR — {e}` to **stderr**, and after the loop emits a loud `PARTIAL write; N loop(s) already
  committed. Backup: …` summary and **`sys.exit(1)`**. `write_memory_loops` itself is unchanged and still
  does `begin_nested()` → `sp.commit()` / `session.commit()`, and on exception `rollback()` →
  `logger.exception()` → **`raise`**. ✓
- **DB consistency on partial write:** the failing track is fully rolled back (savepoint); earlier tracks
  are already committed; the user is told so and pointed at the backup, and the exit code is non-zero
  (`test_partial_write_exits_non_zero`; `test_full_success_exits_zero`). ✓
- **Full local gate, run this turn:** `1560 passed, 8 skipped` (exit 0).

## Sub-threshold notes
- **N1 · conf 70 — TOCTOU window widened (recommended hardening, not a blocker).** The guard now runs at
  startup instead of immediately before the write, so the gap between check and write is the whole analysis
  (minutes on `--library`). A Rekordbox opened *mid-run* is not caught. It fails safe — the write hits a
  locked DB and raises, which the per-track handler reports and exits 1 on (no corruption; SQLite serialises)
  — and main's placement was **non-functional** anyway, so this is strictly better. Cheap close: re-call
  `rekordbox_is_running()` **with no `db_path`** (process-name probe only → *cannot* self-lock) right before
  the backup:
  ```python
  if db_writer.rekordbox_is_running():      # no path ⇒ process probe only, no self-lock
      print("Error: Rekordbox was opened during the run — aborting before the write.", file=sys.stderr)
      sys.exit(1)
  ```
- **N2 · conf 55 — test docstring overclaims main's backup behaviour** (`tests/test_loops_db_write_guard.py`,
  `TestBackupContract`): main did *not* "let the write proceed" on a failed backup — it crashed before the
  write. The real improvement is a graceful abort. Correct the docstring/PR body so the PR doesn't assert a
  data-loss bug that never existed.
- **N3 · info — `--loops` now aborts at startup** if Rekordbox is running / master.db is unlocatable, even
  when the analysis would have produced zero loops (main only checked inside the write block). Stricter and
  fail-fast; consistent with the project's "Rekordbox must be closed before any write" rule. Defensible.
- **N4 · info — `_default_db_path()` returns `None` if `get_config` isn't a dict** → clean "pass --db-path"
  abort (fail-safe).

## MERGE VERDICT
**✅ MERGE-READY.** The guard now runs before the DB is opened (fixing a bug that made `--loops` unable to
write *at all*), it still fires on a genuinely-open Rekordbox with no false-negative traded, guard/backup/
write all target one file (killing the `_db_dir` reconstruction), a failed backup is a hard abort with
nothing written, the ordering pin is genuinely mock-proof and demonstrably fails on main, non-loop paths are
untouched, and nothing is swallowed. Full suite green (1560 passed). Recommend folding in the N1 one-liner
(a process-only re-check before the write) either here or as an immediate follow-up.

PR2-AUDIT

STATUS: DONE (merge-ready — no findings ≥80)

---

# PR3 — SALVAGE PR AUDIT · `fix/serato-preserve-dj-loops` (7310610) → main

Changes the **Serato tag writer** — a bug here corrupts the DJ's audio-file tags. Verified by direct
byte-level probing of the writer/parser and by running the PR's tests against unfixed `origin/main`.
**No findings ≥80. MERGE-READY.**

## (1) ★ The dedup discriminator — exact `start_ms` equality is SAFE, both directions walked

**Provenance is exact.** `read_loops` (db_writer.py:147-176) sets `start_ms = int(row.InMsec)` — a plain
int. `build_markers2` writes it as `loop["start_ms"].to_bytes(4, "big")`; `parse_markers2` reads it back
as `int.from_bytes(data[2:6], "big")`. A **u32be round-trip is lossless** for any 0 ≤ start < 2³², so the
same integer comes back bit-for-bit. **No tolerance is needed** — I confirmed the exact round-trip live.

**Direction B — can OUR OWN loop be misclassified FOREIGN (the double-count trap)?** **No.** A loop we
wrote carries start = the DB's `InMsec`; on rewrite it parses back to that identical int, and
`db_starts = {int(lp["start_ms"]) for lp in loops}` contains it → `start not in db_starts` is False → it
is **not** preserved, it is regenerated from `loops`. So it appears exactly once, and repeated rewrites are
**stable, not growing** — pinned by `test_autocue_loops_not_duplicated_across_repeated_rewrites` and by
`test_db_is_authoritative_a_retuned_loop_end_updates` (a re-tuned end actually updates rather than being
frozen by a stale preserved copy). The trap is closed. ✓

**Direction A — can a DB loop and a genuine DJ loop legitimately SHARE a `start_ms` (→ we drop the DJ's)?**
Only on an *exact-millisecond* collision between two independent analyses (Rekordbox's beat grid vs the
DJ's hand-placed Serato loop). Possible but narrow, and the consequence is bounded: a loop still exists at
that start, but with the DB's end/name instead of the DJ's. This is the design's stated "DB is
authoritative" trade, and the alternative (preserve everything) reintroduces the double-count bug. See
note N2.

## (2) ★ Byte-verbatim preserve — payload stays VALID, indices never collide (PROVEN live)
I built a payload with two DJ loops forced into **non-contiguous** slots 3 and 7 plus two generated loops,
wrapped it, and parsed it back:
```
  parsed back: 2 CUE + 4 LOOP
    idx=0 start=  10000 'Intro'      ← generated, lowest free
    idx=1 start=  30000 'Outro'      ← generated, lowest free
    idx=3 start= 111000 'DJ Loop'    ← preserved, original slot kept
    idx=7 start= 111000 'DJ Loop'    ← preserved, original slot kept
  INDEX DISJOINT (no dupes): True -> [0, 1, 3, 7]
  DJ loops byte-VERBATIM in payload: True
  header 0101: True   terminator 0x00: True
  outer: no '=' padding: True    >=470B pad: True
```
- **Framing/lengths:** each preserved `raw` is the *full framed entry* (`TYPE\0` + u32be len + data, sliced
  as `payload[i:end+5+length]` at serato_writer.py:208), so re-emitting it verbatim keeps the framing
  self-consistent — `parse_markers2` walks the spliced payload without drift. ✓
- **Terminator + header + Serato quirks:** the `\x00` terminator is appended after *all* entries, and
  `wrap_outer` (unchanged) applies the no-`=` base64 dialect and the ≥470-byte NUL pad to the **whole**
  payload, preserved bytes included. ✓
- **Index collision: IMPOSSIBLE by construction.** `used = {raw[10] …}` reads the index byte at framed
  offset 10 (5-byte `LOOP\0` + 4-byte length + `data[1]`) — I verified that offset is correct — and
  `free = [i for i in range(8) if i not in used]`; generated loops are assigned via `zip(free, generated)`.
  Generated indices are therefore **disjoint** from every preserved index, including non-contiguous ones.
  Pinned by `test_non_contiguous_preserved_indices` and
  `test_preserved_loop_at_index_7_never_pushes_a_generated_loop_past_7`. ✓

## (3) 8-cap — surplus GENERATED are dropped, never a DJ loop (PROVEN, incl. foreign > 8)
The preserved loops are emitted **unconditionally** (`for raw in preserve: out.append(raw)`); only
`dropped = generated[len(free):]` is discarded, with a `logger.warning` naming them. I probed the extreme —
**10 foreign loops (more than the 8 slots) + 3 generated**:
```
  foreign=10, generated=3 -> emitted 10 LOOPs
  ALL 10 DJ loops preserved (none dropped): True
  surplus GENERATED dropped (0 of 3 emitted): True
  WARNING: Serato has only 8 loop slots and 8 are held by your own Serato loops
           — dropping 3 generated loop(s): G10, G20, G30
```
`free` collapses to `[]` → zero generated emitted → **the DJ always wins**, and we faithfully re-emit
whatever the file already contained rather than truncating it. ✓

## (4) Regression — no positional breakage; CUE bytes unchanged; fingerprint-skip does NOT mask the fix
- **`preserve` is keyword-only** (`def build_markers2(cues, loops=None, *, preserve=())`). I grepped **every**
  caller in `autocue/` and `tests/`: all pass ≤2 positional args (`build_markers2(cues)`,
  `build_markers2(cues, loops)`, `build_markers2([], loops=loops)`). **No positional-arg breakage
  anywhere.** ✓ Default is the immutable `()` → `list(preserve)` → no mutable-default trap. ✓
- **CUE entry bytes untouched** — the CUE branch is not modified; pinned by
  `test_cue_entries_are_byte_identical_with_a_preserved_loop_alongside`. ✓
- **Fingerprint-skip (`autocue_serato_state.json`) cannot mask a preserved-loop change.** The fingerprint is
  `sha1(cues, loops, comment)` — the DB side. When it matches, the export is **skipped entirely**, so
  `write_serato_tags` is never called, so the file (and the DJ's loops in it) is **never touched** — the
  desired outcome, not a masked one. When it *doesn't* match, we write, and the preserve path re-reads the
  file's loops fresh. Either way the DJ's loop survives and is never doubled. Pinned by
  `TestFingerprintSkipInteraction::test_a_skipped_export_leaves_the_djs_loop_untouched_and_undoubled`. ✓
- **Full suite: `1561 passed, 8 skipped` (exit 0).** ✓

## (5) Silent-failure — the required warning IS there
`_existing_loop_entries` (serato_writer.py:274-308): when a v2 tag **is present but decodes to zero entries**
(base64 corruption, envelope our parser can't read), it emits
`logger.warning("%s: the existing Serato %s tag could not be decoded — any loops you saved in Serato cannot
be preserved and will be dropped by this write")` before returning `[]`. **Exactly the breadcrumb that stops
a rewrite from silently dropping the DJ's loops.** Pinned by
`test_undecodable_v2_tag_warns_and_the_write_still_succeeds` — which **fails on main**. ✓
A tag that decodes to CUE-only entries correctly does *not* warn (nothing to preserve). ✓ The one remaining
un-warned swallow is note N3 (benign).

## The tests are real proofs (run against unfixed `origin/main`)
```
6 failed, 5 passed          ← on main (the destruction reproduces)
FAILED  test_dj_serato_native_loop_survives_byte_identical      ← the headline bug
FAILED  test_foreign_loop_survives_in_flac
FAILED  test_dj_loops_win_and_surplus_generated_are_dropped_with_a_breadcrumb
FAILED  test_preserved_loop_at_index_7_never_pushes_a_generated_loop_past_7
FAILED  test_non_contiguous_preserved_indices
FAILED  test_undecodable_v2_tag_warns_and_the_write_still_succeeds
```
**11 passed** on the PR. The 5 that pass on main are the anti-regression guards (no-double-count,
DB-authoritative, CUE byte-identity, clean-write, fingerprint-skip) — correct: those must hold **before and
after**, and they are what would catch this fix introducing the doubling trap. Not tautologies. ✓

## Sub-threshold notes
- **N1 · conf 70 — stale-orphan accumulation when a DB loop's `InMsec` MOVES.** If Rekordbox re-analysis
  shifts a loop's start (10000 → 10005), the old file loop at 10000 is no longer in `db_starts` → it is
  classified **foreign** and preserved forever, while the new one is written alongside. It does **not**
  double per rewrite (repeated runs are stable — I traced it), but each re-analysis can leave one orphan,
  and once foreign fills the 8 slots, *generated* loops start being dropped (loudly). The conservative
  direction is the safe one (never delete something that might be the DJ's), so this is a limitation, not a
  bug. Cheap true fix: the existing `autocue_serato_state.json` could record the `start_ms` values AutoCue
  wrote, giving real provenance instead of inferring it from a start match.
- **N2 · conf 60 — an exact-ms collision between a genuine DJ Serato loop and a DB loop silently replaces
  the DJ's end/name.** Narrow (two independent analyses landing on the same millisecond), non-destructive
  (a loop still exists at that start), and documented as the intentional "DB is authoritative" trade.
- **N3 · conf 45 — `_existing_loop_entries`' outer `except Exception: return []` (serato_writer.py:286-288)
  does not warn.** Effectively benign: `_read_existing` and the writer use the *same* mutagen constructors,
  so anything that makes the read raise makes the subsequent write raise too (reported by `write_serato`'s
  per-file handler). Add a warning for symmetry with the decode path.
- **N4 · conf 50 — MP4/M4A preserve is untested.** GEOB (MP3) and FLAC are covered; MP4 goes through the
  identical `_envelope_payload` path (`_read_existing` returns the same base64 bytes for both), so it is
  structurally sound — but this is a tag-*writing* surface on the user's audio files, so one MP4 test is
  cheap insurance.

## MERGE VERDICT
**✅ MERGE-READY.** The dedup discriminator is exact and safe in both directions (our own loops can never be
misclassified foreign → the double-count trap is closed; the DJ's loops can never be dropped). Preserved
entries are re-emitted byte-verbatim into a payload that stays structurally valid (framing, terminator,
≥470B pad, no `=` padding), and generated loops take the **lowest free** slots so an index collision is
impossible by construction. Under the 8-slot cap the DJ always wins — even with foreign > 8. No caller
breakage (`preserve` is keyword-only), CUE bytes unchanged, the fingerprint-skip cannot mask the fix, and an
undecodable tag warns instead of silently dropping loops. The tests demonstrably fail on main and pass here;
full suite green (1561 passed). This fixes real destruction of the DJ's Serato loops and should land.

PR3-AUDIT

STATUS: DONE (merge-ready — no findings ≥80)

---

# PR4 — SALVAGE PR AUDIT · `fix/loops-single-writer-beatloopsize` (c6668aa) → main

Adds the `autocue serve` single-writer guard to the `--loops` DB-write path. (BeatLoopSize correctly
BLOCKED on evidence — not in this diff. ✓ right call.) Verified by live-probing the matcher against every
enumerated invocation. **2 Important findings → NEEDS-FIX** (both one-liners). Note up front: the PR is
still **strictly safer than main**, which has *no* serve guard at all.

## (1) ★ FALSE NEGATIVES — one real serve invocation is MISSED (live-probed)
I ran every invocation the brief enumerates through the real `_is_serve_cmdline`:
```
  MUST CATCH (a miss = single-writer guard BYPASSED):
    CAUGHT   autocue serve
    CAUGHT   /abs/path/autocue serve
    CAUGHT   python -m autocue serve
    CAUGHT   autocue serve --port 3004          ← process scan catches ANY port
    CAUGHT   /venv/bin/python3 -m autocue serve --port 9999
    CAUGHT   uv run autocue serve
    *MISS*   C:\Py\Scripts\autocue.exe serve --port 3004     ← WINDOWS
```
Port coverage is correct: `AUTOCUE_SERVE_PORT_RANGE` = `range(7432, 7442)` — exactly `serve()`'s own
auto-fallback (`for alt in range(port+1, port+10)` → 7433-7441) plus the default. A serve on **:3004** is
invisible to the port probe but **caught by the process scan**. ✓

### [IMPORTANT · conf 85] A Windows `autocue.exe serve` is not detected — the single-writer guard is bypassable
**autocue/db_writer.py:157-170** (`_is_serve_cmdline`). The rule requires the token before `serve` to
`.endswith("autocue")`. On Windows the console script is **`autocue.exe`**, which does not. Live-probed:
**MISS**. The `7432-7441` port probe backstops a *default* Windows serve, so the actual hole is
**Windows + an explicit `--port` outside 7432-7441** → both probes miss → `--loops` proceeds while the
server holds its own read-write handle on `master.db`: **two writers on the user's library**, precisely the
hazard this guard exists to prevent. It is also **untested** — `test_real_serve_invocations_are_detected`
parametrizes four forms and omits the `.exe` case.

**Fix (one line — strips `.exe` natively per-platform, keeps every current match, still rejects every FP):**
```python
from pathlib import Path
...
        if tok == "serve" and i > 0 and Path(cmd[i - 1]).stem.lower() == "autocue":
            return True
```
(`Path("autocue.exe").stem == "autocue"` on Windows; `Path("/usr/local/bin/autocue").stem == "autocue"`;
`Path("autocue").stem == "autocue"` for the `-m` form.) Add
`["C:\\Py\\Scripts\\autocue.exe", "serve"]` to the parametrize list.

*(Sub-threshold false negatives: a direct `uvicorn autocue.serve.app:…` has no standalone `serve` token →
missed by the process scan, covered by the port probe only inside 7432-7441 — not the documented way to run
the server (N3). A serve owned by another user would hit `AccessDenied` → `continue` → skipped (N2).)*

## (2) FALSE POSITIVES — all correctly rejected (live-probed)
```
  MUST REJECT (a hit = the user cannot write at all):
    rejected  grep serve autocue/cli.py
    rejected  pytest -k serve autocue
    rejected  autocue --loops --overwrite            ← the CLI's own sibling
    rejected  /Users/x/autocue/.venv/bin/python -m pytest -k serve   ← venv path w/ "autocue"
    rejected  vim autocue/serve/app.py serve
```
`"serve"` must be an **exact argv token** *and* the immediately-preceding token must end with `autocue`, so a
venv path containing `autocue`, a grep, a pytest run, and the CLI's own siblings all miss. The current pid is
additionally skipped (`proc.pid == me → continue`) — double-protected. Pinned by
`TestNoFalsePositives` (5 parametrized cases) + `test_the_current_process_is_never_self_detected`. ✓

## (3) FAIL-SAFE — confirmed REFUSE on every unresolvable probe
- `psutil` **ImportError** → `logger.warning(… refusing … fail-safe)` → **`return True`** (refuse). ✓
- Any exception during `process_iter` → warning → **`return True`** (refuse). ✓
- `_port_is_listening`'s `except OSError: return False` is *not* a fail-open — it only means "that port isn't
  listening", and the process scan runs afterwards as the backstop. ✓
Pinned by `test_an_unresolvable_probe_refuses_the_write` (raises inside `process_iter`, asserts `is True` **and**
that a "fail-safe"/"refus" warning was logged). **Never fail-open on the path that mutates the library.** ✓
- **Message honesty:** partially. See note N1 — the CLI's stderr line *asserts* "a local `autocue serve` is
  running" even when the truth is "cannot tell". The accompanying `logger.warning` ("cannot rule out … refusing")
  does reach stderr via `logging.lastResort`, so the user sees both, but the Error line overclaims.

## (4) ORDERING — serve probed BEFORE Rekordbox; messages correct
`cli.py:239-244` runs `autocue_serve_is_running()` first, then `rekordbox_is_running(db_file)`. Rationale is
right: a running serve **also holds the DB file**, so it would trip the lock probe *inside*
`rekordbox_is_running` and be misreported as "Rekordbox is running" — asking the specific question first
yields the accurate message. Both guards still evaluate (the refusal set is unchanged), and Rekordbox still
fires when no serve is up. ✓ *(N4: the Rekordbox message still says "Rekordbox is running" though the lock
probe can trip on any process holding master.db — cosmetic.)*

## (5) REGRESSION — non-loop paths untouched; nothing swallowed
The cli.py change is entirely inside `if args.loops and loops_by_track:` — XML, `--serato` and `--dry-run` are
byte-for-byte unaffected. ✓ Both `except` blocks in `_serve_process_is_running` **warn and refuse** (they do not
swallow); the per-process `NoSuchProcess/AccessDenied → continue` is the only silent skip (N2). ✓
**Full suite: `1566 passed, 8 skipped` (exit 0).** The PR's own tests: **16 passed**; against unfixed
`origin/main` **all 16 fail** (the guard does not exist there at all) — the gap reproduces. ✓

---

### [IMPORTANT · conf 90] MERGE-INTEGRATION: PR#2 and PR#4 rewrite the SAME cli.py block — merging both can silently DROP this guard
Not a defect in PR#4's code, but it will bite on merge and it loses a **safety guard**, so it is in scope.
- **PR#4** inserts `autocue_serve_is_running()` immediately before `if rekordbox_is_running(db_file):` inside
  the `if args.loops and loops_by_track:` block (main `cli.py` ~231-245).
- **PR#2** (`fix/loops-db-write-guard`) **replaces that entire block** — its hunk is `@@ -230,31 +287,50 @@` —
  moving `rekordbox_is_running` into a new **pre-open** `_preflight_loop_write()`. **PR#2's replacement
  contains no serve guard.**

Whichever lands second conflicts. If the conflict is resolved by taking PR#2's rewrite wholesale (the natural
instinct — it's the larger, "newer" restructuring), **the serve guard vanishes silently** and we are back to a
bypassed single-writer rule.

**Correct integration** (and it's the shape the `feat/autoloops` branch already converged on): the serve probe
must live **inside PR#2's `_preflight_loop_write()`, called BEFORE `rekordbox_is_running(db_path)`** — i.e.
pre-open. This is safe because PR#4's probe is **port + process only, with no file-lock check**, so unlike the
Rekordbox probe it *cannot* self-detect AutoCue's own handle:
```python
def _preflight_loop_write(args) -> Path:
    db_path = Path(args.db_path) if args.db_path else _default_db_path()
    ...
    if db_writer.autocue_serve_is_running():        # ← PR#4's guard, hoisted pre-open
        print("Error: a local `autocue serve` is running …", file=sys.stderr); sys.exit(1)
    if db_writer.rekordbox_is_running(db_path):     # ← PR#2's guard
        ...
```
**Also note: PR#4 alone does not make `--loops` work.** Its serve guard sits in front of
`rekordbox_is_running(db_file)`, which on main is still **post-open** and therefore still self-locks (PR#2's
root cause) — so `--loops` still aborts with a false "Rekordbox is running" and this guard is never exercised
in the real world. **PR#4 is necessary but not sufficient; it must land together with PR#2.**

## Sub-threshold notes
- **N1 · conf 40 — the refusal message overclaims.** On a failed probe the CLI prints "a local `autocue serve`
  **is running**" when the truth is "cannot tell". Behaviour (refuse) is correct; the wording asserts more than
  it knows. Suggest: "could not rule out a running `autocue serve` — refusing the write (single-writer rule)."
- **N2 · conf 45 — per-process `except (NoSuchProcess, AccessDenied): continue`** is a per-process fail-*open*
  inside an otherwise fail-safe scan. A serve owned by another user would be skipped. The user's own serve is
  inspectable, so low risk.
- **N3 · conf 50 — a direct `uvicorn autocue.serve.app:create_app --port 9999`** has no standalone `serve`
  token → missed by the process scan; covered by the port probe only within 7432-7441. Not the documented way
  to start the server.
- **N4 · info — the Rekordbox message still names Rekordbox** though the lock probe can trip on any process
  holding `master.db`. PR#2 broadens this wording; take PR#2's version on integration.

## MERGE VERDICT
**⚠️ NEEDS-FIX — two one-liners, then merge-ready.** The guard is well-built: the port range exactly matches
`serve()`'s fallback, the process scan catches any `--port`, every false-positive case is rejected, and an
unresolvable probe **refuses** rather than allows. But (a) a Windows `autocue.exe serve` is **missed** —
the dangerous direction, in the one guard whose whole job is preventing two writers on the library — and
(b) merging PR#2 and PR#4 as-is risks **silently deleting this guard**. Both are cheap:
1. `Path(cmd[i-1]).stem.lower() == "autocue"` + an `.exe` parametrize case.
2. Land PR#2 first, then rebase PR#4 to call `autocue_serve_is_running()` from inside
   `_preflight_loop_write()` (pre-open, before the Rekordbox probe) — and re-verify the guard survives the merge.

Even unfixed, this PR is **strictly safer than main** (which has no serve guard whatsoever), so if the
coordinator prefers to ship and follow up, do it in that order — but do **not** merge PR#2 after it without
re-checking the guard is still present.

PR4-AUDIT

STATUS: NEEDS-FIX (SUPERSEDED — see PR264-FINAL below; both findings RESOLVED by the fold)
- [IMPORTANT · conf 85] Windows `autocue.exe serve` MISSED → single-writer guard bypassable (db_writer.py:157-170; live-probed; untested in the parametrize list). Fix: `Path(cmd[i-1]).stem.lower() == "autocue"`.
- [IMPORTANT · conf 90] PR#2 and PR#4 rewrite the same cli.py block (PR#2 hunk `@@ -230,31 +287,50 @@` replaces PR#4's insertion point, and carries NO serve guard) → merging both can silently drop the guard. Integrate by hoisting the serve probe into PR#2's `_preflight_loop_write()`, before the Rekordbox probe.
- Port range (7432-7441), any-`--port` process scan, all false-positive rejections, fail-SAFE-on-unresolvable, probe ordering, non-loop regression (full suite 1566 passed) — all SOUND.

---

# PR264-FINAL — PR #264 AFTER THE FOLD · `fix/loops-db-write-guard` (ed44ac1 + eb96f98) → main

Both of my prior IMPORTANT findings are **RESOLVED**, and one of them was fixed *better* than I specified.
**No findings ≥80. MERGE-READY.**

## (1) ★ The merge hazard is ELIMINATED BY CONSTRUCTION — not by convention
My [IMPORTANT 90] was: PR#2 and PR#4 rewrote the *same* cli.py block, PR#2's replacement carried no serve
guard, so resolving the conflict by taking the larger rewrite would **silently delete** the single-writer
guard. The fold removes the hazard at its root:
- There is now **one** `_preflight_loop_write()` (cli.py:35-82) holding **both** guards, in **one** PR. PR#4's
  content is folded into the same function that replaces main's old block — so there is **no second hunk, no
  conflict, and no resolution step in which the guard could be dropped.** The failure mode no longer exists;
  it isn't merely avoided by care. ✓

**And the ordering test is genuinely load-bearing** (defence in depth). `test_both_guards_run_before_the_db_is_opened`
(test_serve_single_writer.py:208-217) asserts on the **recorded call order**:
```python
assert order.index("serve_guard") < order.index("open_db")
assert order.index("rb_guard")    < order.index("open_db")
```
- **Remove the serve guard** → `order.index("serve_guard")` raises **ValueError** → the test FAILS.
- **Reorder it after the open** → the comparison FAILS.
- No amount of mocking the guard's *return value* can hide an assertion on *order*.
Plus `test_serve_is_asked_BEFORE_rekordbox_so_the_message_is_honest` pins `index("serve_guard") == 0` **and**
`"rb_guard" not in order` (the short-circuit). **Proven:** 26 of 30 fail on unfixed `origin/main`. ✓

## (2) ★ The `.exe` fix — RESOLVED, and better than I asked for
**db_writer.py:157-181.** My [IMPORTANT 85] was that `endswith("autocue")` missed the Windows `autocue.exe`.
The fix is `Path(cmd[i-1].replace("\\", "/")).stem.lower() == "autocue"` — and that `replace("\\", "/")`
**normalisation is a genuine improvement I did NOT specify**: my suggested bare `Path(...).stem` would have
still failed when a *Windows* cmdline is read on a POSIX host (`PosixPath("C:\\Py\\autocue.exe").stem` is
`"C:\\Py\\autocue"` → no match). They caught that. Live-probed against the real function:
```
  MUST CATCH (a miss = guard bypassed):
    CAUGHT  autocue serve                      CAUGHT  python -m autocue serve
    CAUGHT  /usr/local/bin/autocue serve       CAUGHT  autocue serve --port 9999
    CAUGHT  C:\Py\Scripts\autocue.exe serve    CAUGHT  /venv/bin/python3 -m autocue serve
    CAUGHT  uv run autocue serve               CAUGHT  C:\X\AUTOCUE.EXE serve   (.lower())
  MUST REJECT (a hit = the user cannot write at all):
    rejected grep serve autocue/cli.py     rejected pytest -k serve autocue
    rejected myapp serve                   rejected serve --port 8080
    rejected /Users/x/autocue/.venv/bin/python -m pytest -k serve
    rejected vim autocue/serve/app.py serve    rejected autocue --library --loops (own sibling)
    rejected /bin/myautocue serve          ← NEW: the exact-stem match also kills a near-miss FP
                                             that the old endswith("autocue") would have WRONGLY hit
```
**8/8 caught, 8/8 rejected.** The stem fix tightened **both** directions. ✓
**Remaining false negatives:** only the two sub-threshold ones (N2/N3) — a serve owned by another user
(`AccessDenied` → skipped) and a direct `uvicorn autocue.serve.app:…` (no standalone `serve` token; covered by
the port probe only inside 7432-7441). Neither is a documented way to run the server.

## (3) ORDERING — correct and honest in every combination; the serve probe cannot self-lock
`_preflight_loop_write` asks **serve first, then Rekordbox** (cli.py:66-79):
| Combination | Behaviour | Verified |
|---|---|---|
| serve only | serve message, exit 1, **DB never opened** (`"open_db" not in order`) | pinned |
| Rekordbox only | serve passes → RB fires → "Rekordbox is running" | pinned |
| **both** | serve asked first and **short-circuits**; asserts `"rekordbox is running" not in stderr` — **no misattribution** | pinned |
| neither | both clear → the write proceeds | pinned |

**Can the serve probe self-lock now that it runs pre-open?** **No.** It is **port + process** based
(`socket.create_connection` over 7432-7441, then a psutil cmdline scan) with **zero file-lock probe** — unlike
`rekordbox_is_running`, it has nothing that could detect our own handle. It also runs before we hold one, and
it skips its own pid. Triple-safe. The docstring states this explicitly. ✓

## (4) The original PR #264 fixes all survive the fold
- **Guard before the DB is opened:** `loop_db_path = _preflight_loop_write(args) if (args.loops and not args.dry_run) else None`
  (cli.py:172-174) runs before `MasterDatabase(...)` (cli.py:178). Pinned by **both** ordering assertions. ✓
- **Guard / backup / write target the SAME file:** `db_path = Path(args.db_path) if args.db_path else _default_db_path()`
  is returned and reused as `db_file = loop_db_path` for the backup, while `MasterDatabase(args.db_path)` opens
  the identical file (`_default_db_path()` mirrors pyrekordbox's own `get_config` lookup). The `_db_dir/"master.db"`
  reconstruction is gone. ✓
- **Backup failure = hard abort, nothing written:** `try/except` → stderr → `sys.exit(1)`, strictly before the
  write loop. ✓
- **Non-zero exit on a partial write:** `if failed: … sys.exit(1)`. ✓
- **`--dry-run` writes nothing:** preflight gated on `not args.dry_run`, and the write block is unreachable
  (dry-run returns earlier). ✓
- **No regression from the fold:** non-loop paths (XML / `--serato` / `--dry-run`) untouched; **full suite
  `1580 passed, 8 skipped` (exit 0)**; PR tests **30 passed**; **26 failed on unfixed main**. ✓

## (5) FAIL-SAFE + silent-failure lens — CLEAN
- `psutil` **ImportError** → `logger.warning(… refusing … fail-safe)` → **`return True`** (refuse).
- Any exception during `process_iter` → warning → **`return True`** (refuse).
- `_port_is_listening`'s `except OSError: return False` is not a fail-open — the process scan backstops it.
- `_default_db_path`'s `except Exception: return None` funnels into a **loud** `sys.exit(1)`
  ("cannot locate master.db — pass --db-path"), never a silent allow.
**No swallowed exception anywhere on the preflight can permit a write.** ✓

## Sub-threshold notes (carry into the merge note, none blocking)
- **N1 · conf 65 — TOCTOU (carried from my PR#2 review).** Both guards run at startup, so a Rekordbox or serve
  started *during* a long `--library` analysis is not caught. It fails safe (the write hits a locked DB → raises
  → per-track handler reports → exit 1); no corruption. Cheap close: re-run the **process-only** probes
  (`autocue_serve_is_running()` and `rekordbox_is_running()` with **no** `db_path`, so neither can self-lock)
  immediately before the backup.
- **N2 · conf 45 —** per-process `except (NoSuchProcess, AccessDenied): continue` is a per-process fail-*open*
  inside an otherwise fail-safe scan.
- **N3 · conf 50 —** a direct `uvicorn autocue.serve.app:create_app --port 9999` has no standalone `serve` token.
- **N4 · conf 40 —** the refusal message asserts "a local `autocue serve` **is running**" even when the probe
  merely could not complete. Behaviour (refuse) is right; the wording overclaims. The accompanying
  `logger.warning` ("cannot rule out … fail-safe") does reach stderr.

## FINAL MERGE VERDICT — PR #264
**✅ MERGE-READY.** The fold makes the single-writer guard structurally undroppable (one preflight, one PR) and
pins it with a mock-proof ordering assertion that fails on removal *or* reorder. The Windows `.exe` false
negative is closed — with a backslash-normalisation refinement beyond what I specified — and the exact-stem
match tightened a latent false positive too: **8/8 real invocations caught, 8/8 impostors rejected.** Both
guards run before the DB is opened (killing the self-lock that made `--loops` unable to write at all), the
serve probe is port/process-only so it cannot self-lock, guard/backup/write target one file, a failed backup
aborts with nothing written, a partial write exits non-zero, and an unresolvable probe **refuses**. Tests
demonstrably fail on main (26/30) and pass here (30/30); full suite green (1580 passed).

**Both of my prior findings — [IMPORTANT 90] merge hazard and [IMPORTANT 85] Windows `.exe` — are RESOLVED.**
Recommend folding the N1 one-liner (a process-only re-check just before the backup) here or as an immediate
follow-up.

PR264-FINAL

STATUS: DONE (merge-ready — no findings ≥80)
>>>>>>> Stashed changes

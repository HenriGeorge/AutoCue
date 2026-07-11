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

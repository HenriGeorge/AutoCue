# Crew lessons — APPEND-ONLY (agent→coordinator continuous-improvement channel, lesson #19).
# On STATUS: DONE, if you hit reusable friction or a gotcha, append ONE line in this format:
#   printf '%s | ROLE | Problem -> Fix -> where-it-applies\n' "$(date +%H:%M:%S)" >> crew/LESSONS.md
# The coordinator aggregates these into docs/lessons.md (/dev-reflect) at P8 CLOSE and flags global
# candidates for ~/Projects/lessons_learned.md. Never overwrite — append below this header.
21:10 | RESEARCHER | Live-schema probe beats doc/code assumptions for "does field X exist" -> python3 -c "from pyrekordbox.db6 import DjmdCue; print([c.name for c in DjmdCue.__table__.columns])" confirmed all 7 loop columns in one shot; same trick read RekordboxXml.add_mark signature via inspect.getsource -> use for any "is capability Y available" data-model question
21:23 | RESEARCHER | Reverse-eng binary format only half-in-code (Serato CUE serialized, LOOP not) -> anchor high-confidence fields from the ORIGINfor spec source (Holzhaus serato-tags via raw.githubusercontent WebFetch) and explicitly tag the fuzzy middle bytes PROBE-VERIFY with a round-trip test recipe, rather than guessing byte offsets; our faithful CUE impl is the cross-check that the same ref repos LoopEntry is correct -> applies to any partial binary-format port
02:51:52 | IMPLEMENTER | autoloops: mutagen (the [serato] dev extra) was NOT installed in the worktree python -> test_serato_writer.py + the F1 file-rewrite preserve test SILENTLY skip -> pip install --break-system-packages "mutagen>=1.46" (homebrew py is PEP-668 externally-managed) -> serato suite un-skips (~44 tests). Applies: any crew touching autocue/serato_writer.py must install mutagen or the serato leg is invisible-green. | crew build
02:52 | TEST-VERIFIER | Authoring a DISJOINT golden against a not-yet-committed contract, the coverage-map PROSE dict-keys (position_ms/loop_end_ms) conflicted with the implementer draft unit tests (start_ms/end_ms/raw + preserve=) -> match the implementer DRAFT test file for REAL key names, not the map prose; keep survival tests hex-free (type+raw only) so contract drift + a later byte-fix cannot make them lie -> applies to any two-author #99 golden/unit split on an in-flight binary format
03:02 | AUDITOR | Loop policy unit-tested only via pure plan_loops with explicit phrase_bars -> the ANLZ wrapper analyze_loops/_bars (which derives bars from next-phrase deltas and zeroes the terminal outro) had ZERO coverage, hiding a headline-feature miss -> when a pure policy fn is fed synthetic inputs in tests, ALSO test the real adapter that builds those inputs | applies to any policy/adapter split (analyzer, ranker, scan_orchestrator)
03:23:17 | IMPLEMENTER | autoloops P4-FIX: a pure policy (plan_loops) with 100% unit coverage still shipped the Outro-never-fires bug because every test fed explicit phrase_bars — the defect lived in the ADAPTER (analyze_loops._bars) that derives those inputs from real data. Fix -> always add at least one test at the adapter/seam level (synthetic ANLZ), not only the pure fn; a green pure-fn suite is necessary NOT sufficient. Applies: any pure-core + thin-adapter split. | crew build
03:28 | RESEARCHER | Docs-impact from a coordinator brief can overstate the shipped surface (brief said "XML loop in writer.py" but cli.py gates --loops to --serato only + prints "later increment") -> ALWAYS trace the flag END-TO-END (writer capability != CLI exposure) before writing user-facing feature docs; grep the flag through cli.py branches, not just the target module -> applies to every P6 feature-doc pass
03:39:45 | IMPLEMENTER | autoloops XMLWIRE: INC-2 unit-tested writer.py loop marks but the CLI XML branch never merged loops -> shipped writing 0 loops; AND _merge_loops collision-dropped loops sharing a downbeat with generated point cues. Fix -> the seam between a unit-verified writer and its ONE caller needs an end-to-end test; "green unit + green writer" != "the command writes loops". Applies: any feature where a tested low-level writer is invoked by a separate wiring branch. | crew build
04:43 | RESEARCHER | Reusing a shared write fn for a NEW row type can be silently destructive: write_cues_to_db(overwrite=True) deletes ALL Kind=0 rows, so writing a memory LOOP through it would wipe the DJ existing memory CUES (they share Kind=0, discriminated only by OutMsec) -> before reusing any write path for a new entity, read its DELETE predicate and ask "what ELSE matches this filter?"; prefer a new append-only fn over relaxing a shared one (server routes depend on it) -> applies to any DB-mutating increment
05:04 | AUDITOR | A guard that probes a single hardcoded port is not a guard — the guarded service auto-fell-back to another port (serve 7433-7441) and --port made it arbitrary, so the safety check silently returned "not running" -> guard on the PROCESS (psutil cmdline) or a pid+port lockfile, never on one port -> applies to every single-writer/mutex/"is X running" check before a destructive op
05:14 | RESEARCHER | I characterized the loop surface as [MISSING] at P0 against the WORKTREE base and never ran git fetch — origin/main had ALREADY shipped the whole feature (12 commits ahead), so the crew built a duplicate for 16 commits -> GATE-0 research MUST start with `git fetch && git rev-list --count HEAD..origin/main` and grep origin/main (git show/git grep origin/main -- path) for the target surface, NOT just the local worktree; a worktree is a snapshot and "does X exist?" is only answerable against the REMOTE default branch -> applies to every P0-PREMISE/greenfield-vs-redesign question
05:27:13 | IMPLEMENTER | INC-3 BL-1: a guard that probes a FILE LOCK self-detected our own DB handle (we opened the DB first) -> false abort, 100%% broken feature, yet every unit test passed because they all MOCK the guard. Fix -> when a guard tests an environmental precondition (lock/port/process), assert its CALL ORDER relative to the thing that would perturb that environment, not just its return value; a mock can never reveal a self-interference bug. Applies: any lock/port/process guard in a CLI that also opens the resource. | crew build
05:33 | AUDITOR | Fixing a shared-space conflation on ONE side leaves the other side lying: write_cues_to_db learned "a Kind=0 loop is not a memory cue" for its DELETE, but has_existing_memory_cues still COUNTED loops as cues -> silently suppressed writes on the default path -> when you introduce a discriminator into a shared row-space, grep EVERY query on that space (count/exists/delete/select) and apply it to all of them in the same commit | applies to any shared-table/soft-type discriminator (Kind=0 cue-vs-loop, status flags, tombstones)
05:49 | AUDITOR | Tightening a guard against FALSE POSITIVES silently opened a FALSE NEGATIVE: constraining the serve match to prev-token.endswith("autocue") fixed grep/pytest noise but stopped matching the Windows autocue.exe console script -> when you narrow a safety predicate, re-enumerate the POSITIVE cases it must still catch (incl. platform variants: .exe, abs path, python -m) and pin each in a parametrized test -> applies to every allow/deny matcher (process scans, path matchers, auth checks)
05:51:51 | IMPLEMENTER | Docs: verifying a GATE-0 rebase claim meant reading origin/main source — and main-only symbols (write_memory_loops, read_loops) leaked into docs describing OUR tree, where grep finds nothing. Fix -> EVERY symbol named in a doc must be grepped in the tree that doc ships with; reading another ref is exactly how a plausible non-existent function name gets written down. Corollary: after fixing a bug, re-read the docs describing it — I documented a footgun I had already fixed. Applies: any docs pass done alongside cross-branch research. | crew build
<<<<<<< Updated upstream
=======

## Re-run GATE-0 BEFORE you finish, not just before you start (autoloops, 2026-07-11)

**What happened.** P0 GATE-0 was run correctly: `git fetch` → `feat/autoloops` was 0 ahead / 0
behind `origin/main`. Clean base, verified. We then built for several hours. A *parallel* effort
merged its own auto-loops feature to main **40–75 minutes after our base check** (PR #257 at 21:44,
PR #261 `--loops` at 22:20, PR #262 at 03:15). We discovered it only at P6 DOCS — when the
implementer refused to write docs that contradicted main. By then: 25 commits ahead, 12 behind, a
colliding `--loops` flag, and ~65–70% of the work duplicated or superseded (main's generator is
*better* — it does real librosa audio seam-validation; ours had none).

**The lesson.** A stale base is not a start-of-session risk — in a repo where other sessions merge
to main, it is a **continuous** one. GATE-0 as a one-shot check is insufficient for any build
longer than ~an hour.

**The rule.**
- Re-run `git fetch origin` + the behind-count **at every phase boundary** on a long build — at
  minimum before P5 REVIEW and again before P7 FINISH. One command; it would have saved this build.
- The check is cheap and the failure is total: you cannot "mostly" recover from having rebuilt a
  shipped feature. **Fetch before you commit to a direction, and fetch again before you ship.**
- Corollary (#97): the *brief* can go stale mid-build too, not just the git base. If a teammate
  reports "this doc/claim collides with reality," treat it as a possible base-drift signal and
  re-run GATE-0 immediately — do not just fix the doc.

**What saved it.** The implementer STOPPED rather than documenting a surface that contradicted
main. Blocking on a contradiction — instead of writing the plausible-looking lie — is what turned a
silent disaster into a recoverable one. That instinct is worth more than the code it refused to write.

**The salvage.** Not all was lost: the branch found a **live data-loss bug on main** — `Kind=0` is
shared by memory cues and memory loops (discriminator `OutMsec`), and main blanket-DELETEs/COUNTs it
in three places, so its own new loop feature destroys the DJ's memory cues *and* gets destroyed by
Apply. Duplicated work still produced a real fix, because we went looking for the invariant instead
of assuming it.
06:50 | AUDITOR | A safety-fix test suite is only worth what it proves: I ran the PR tests against a temp worktree at unfixed origin/main (7 failed = bug reproduces) instead of trusting that they would -> ALWAYS run a bugfix PR test file against the pre-fix commit; a green suite on the fix branch proves nothing about whether the test can fail. Also check the mock landmines (db.generate_unused_id -> MagicMock IDs; db.query vs db.session.query -> .count()==0 silently False) | applies to every bugfix/regression PR review
07:40 | AUDITOR | Two PRs fixing the same file can DELETE each other's safety guard: PR#2 replaces the exact cli.py block PR#4 inserts its single-writer guard into, and PR#2's replacement has no guard -> resolving the conflict by taking the bigger rewrite silently drops it -> when auditing a stack of parallel PRs, diff their HUNK RANGES against each other, not just each PR vs main; a guard that survives review can still die in the merge | applies to any multi-PR salvage/stacked-branch workflow
>>>>>>> Stashed changes

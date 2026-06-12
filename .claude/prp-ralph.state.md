---
iteration: 3
max_iterations: 20
plan_path: ".claude/PRPs/plans/v2-p1-global-layer.plan.md"
input_type: "plan"
started_at: "2026-06-12T02:04:31Z"
phase: "P1 (P0 complete, PR #208)"
---

## P0 — COMPLETE (PR #208, base claude/hopeful-turing-3fe017)
All 9 tasks done. Three-leg green: Vitest 676, pytest 1439, e2e 161 passed +
8 known-baseline (fixed by PR #206), 0 new. Branch feature/v2-p0-foundations.

## P1 — IN PROGRESS (branch feature/v2-p1-global-layer off P0)
- T1 ✅ (0f93c07): /api/status?include_rb=1 opt-in rekordbox_running. pytest +5.
- T2 ✅ (426ba2d): window.ACBridge (4 read-only accessors at end of
  08-set-builder-boot.js) + autocue:health-summary / autocue:local-mode events.
  Vitest 680. NOTE: bridge accessors are closures over the classic-script
  global lexical env — v2 modules MUST read legacy let-state only via ACBridge.
- T3 ✅ (0c04e0c): status sentence — spans→buttons + 'N need cues'/'health
  S/100' derived facts + 30s ?include_rb=1 rb poll. New module status-sentence.js.
  control-inventory.json +6 status buttons (skipSweep). Verified Chrome both
  themes. Vitest 690.
- T4 ✅: fuzzy.js + commands.js (pure, 16 vitest cases). 708.
- T5 ✅ (uncommitted): palette.js + markup + CSS + cmdk-hint-btn. ⌘K/Ctrl+K//
  open, capture-phase key priority, green-wash active item, mono track meta,
  inert "Ask AutoCue (coming soon)" composer seam (P6 AUTOCUE_LLM seam).
  Verified live in Chrome (grouped results, track search, composer seam).
  Vitest 718. e2e: selectors-exist +4, control-inventory +cmdk-hint-btn /
  pal-input ignore-listed, NEW v2-global-layer.spec.ts (4 smoke tests).
  Affected-e2e validation RUNNING (berp260y2). Commit after green.
- T6–T9 PENDING: T6 action-bar relabel, T7 done (in T5), T8 full Chrome audit,
  T9 docs + full three-leg gate + push + PR.

# PRP Ralph Loop State

## Codebase Patterns
- Branch: `feature/v2-p0-foundations` (off claude/hopeful-turing-3fe017, which has PR #207 = aliveness pass)
- docs/index.html structure: `<style>` block lines 13–2290; CDN scripts at 7 + 2291–2292; markup 2293–3733; main `<script>` 3734–14546
- `autocue/serve/app.py:78` already mounts all of docs/ via StaticFiles(html=True) — subdirs served, no backend change needed
- 38 vitest specs read docs/index.html via exactly two readFileSync patterns (see plan Key Facts)
- e2e baseline: 8 pre-existing failures (control-inventory ×2 + per-control-sweep ×6) — accepted; ZERO new failures allowed
- Validation = three-leg stack: `pytest` · `npm test` · `cd tests/e2e && npx playwright test`

## Current Task
Execute PRP plan and iterate until all validations pass.

## Plan Reference
.claude/PRPs/plans/v2-p0-foundations.plan.md

## Instructions
1. Read the plan file
2. Implement all incomplete tasks
3. Run ALL validation commands from the plan
4. If any validation fails: fix and re-validate
5. Update plan file: mark completed tasks, add notes
6. When ALL validations pass: output the completion promise

## Progress Log

## Iteration 1 — 2026-06-12T02:04Z (in progress)

### Completed
- T1+T2 (b8a23d7): loadAppHtml() helper + 3 spec migrations. KEY FINDING: only
  4 of the "38" specs actually readFileSync the HTML — the rest are mirror-copy
  tests referencing it in comments. tracks-sticky-structure (jsdom, markup-only)
  needed no change.
- T3 (0f46bb3): CSS → docs/css/app.css (2,277 lines, verbatim).
- T4+T6+T7 (67516b8): JS → docs/js/app.js (10,811 lines, classic script);
  js/v2/main.js ES-module seam (window.AC2); XML-mode hint in drop zone.
- T8 (b8dfcc1): CLAUDE.md + web-ui.md constraint rewrite; program PRD + plan
  checked in.

### Validation Status
- Vitest: PASS 676/676 (after every task)
- pytest: PASS 1439 (after T4)
- Chrome: zero console errors; CSS tokens + dark theme + runtime objects verified live
- e2e: T4-state full run IN FLIGHT (background task bszkd7scl); note T6/T7
  edits landed mid-run — if oddities, disregard and rely on the T9 final run

### Learnings
- node --check treats docs/js/*.js as ESM (root package.json "type":"module")
  → syntax-check classic scripts via `cp X /tmp/x.cjs && node --check`.
- app.js has 3 duplicate top-level `function _esc` (4547/5354/9564) — legal
  later-wins in classic scripts, SyntaxError if ever converted to a module.
  Consolidate during T5; until then never type=module app.js.
- grep -c returning 0 exits 1 — don't chain `grep -c && npm test`.

### Next Steps
- T5: split app.js at section boundaries; T9 final gate + PR.

---

## Iteration 2 — 2026-06-12T05:xx Z

### Completed
- T5 (commit "split app.js into 8 ordered feature files"): used an acorn
  hazard-analysis agent — split is safe with EXACTLY ONE relocation: the
  detectLocalMode().then(...) block (former 5824-5935) moved to end of
  08-set-builder-boot.js (its callback refs buildSet/psSavePlaylist/
  loadTracksFromServer in segs 05-08). Verified: concat content-set IDENTICAL
  to original (10,077 lines, sorted-diff empty); each part syntax-checks as a
  classic script; Chrome zero console errors; all globals + bare identifiers
  (DiscoverV2/parsedTracks/buildPhraseStrip/_explainCue) resolve cross-file
  via the shared global lexical environment.
- qa-smoke filter-toggle hardening (separate commit): see Learnings.
- Pushed feature/v2-p0-foundations. T9 full e2e RUNNING (bvqxug24o).

### Validation Status
- Vitest 676, pytest 1439 — green after T5.
- T9 full e2e: in flight; EXPECT 8-known-baseline failures (fixed by PR #206,
  not by P0) + 0 NEW + qa-smoke now passing.

### Learnings
- **CRITICAL — don't trust single e2e runs for flaky tests.** The 9th e2e
  failure (qa-smoke "filter toggles") looked like a P0 regression. Bisection
  (monolith pass / HEAD fail / HEAD-minus-module pass) FALSELY implicated the
  v2 module script. Re-running on a QUIET host: HEAD-with-module passes 3/3.
  Root cause = issue #189 residual flake: .check({force:true}) post-click
  state verification reads `checked` mid-re-render-frame under main-thread
  saturation. The "failures" all occurred under CPU contention (concurrent
  e2e suite + post-kill residue). Lesson: confirm flaky-test failures with
  3x isolated runs on a quiet host BEFORE attributing to your change.
- Fixed the flake properly: drive the change handler via evaluate
  (set checked + dispatch 'change') — deterministic, no pointer-frame
  dependency. Passes 3/3 under load.
- **PR #206 already fixes the 8 baseline e2e failures** (branch
  claude/distracted-jemison-2b2c96, MERGEABLE) — from an earlier session.
  A spawned baseline-fixer agent DUPLICATED this (its branch never reached
  origin; orphaned). Don't re-fix the 8; #206 is canonical. Merge #206 → main,
  then P0/P1 inherit green.
- Background-job gotcha: a `while` guard that greps `ps` for "playwright test"
  matches its OWN command line (the `npx playwright test` in the same script)
  → infinite wait. Killed PID 325 after 41 min stuck.

### Next Steps
- T9 result → open P0 PR (base: claude/hopeful-turing-3fe017, the #207
  aliveness branch P0 stacks on). Document: 8 known failures fixed by #206,
  0 new from P0.
- Then P1 (plan ready: v2-p1-global-layer.plan.md). P2 research captured at
  .claude/PRPs/research/v2-p2-workbench-findings.md.

---

# HANDOFF — AutoCue 2.0 redesign (2026-06-14)

Autonomous phased build of the B "Crate Console" redesign. **All planned build
phases P0–P5 are MERGED to `main`** (P6 LLM composer is deferred by design — see
table). The workbench is the default local-mode home and the legacy tab UI is
retired. Decisions locked via socratic grill — see
`.claude/PRPs/prds/autocue-2-program.prd.md` + memory `project_autocue_2_redesign.md`.
Do NOT re-litigate them.

## ⚠️ Workflow rule (new, enforced) — never commit to `main`
A tracked pre-commit hook (`.githooks/pre-commit`, activated via
`git config core.hooksPath .githooks`) **blocks `git commit` on main/master**.
Everything — code, docs, PRDs, plans — lands via: branch → commit → push →
`gh pr create` → `gh pr merge`. No local ff-merges to `main`. See the CLAUDE.md
"Worktree + PR workflow" section. Emergency override: `git commit --no-verify`.
(One-time per fresh clone: `git config core.hooksPath .githooks`.)

## Program status (phase table: `.claude/PRPs/prds/autocue-2-program.prd.md`)
| Phase | State |
|---|---|
| P0 foundations | ✅ merged #208 |
| P1 global layer (status sentence + ⌘K) | ✅ merged #209 |
| P2 workbench-as-home | ✅ merged #211; **default-on** shipped after (c3dcff0) |
| **P3 Duplicates as a place** | ✅ merged **#212** |
| **P4 Nightboard canvas mode** | ✅ merged **#217** (full-bleed set canvas, visualize-only) |
| **P5 Discover into the shell** (retires `#tab-discover`) | ✅ merged **#215** |
| P6 AUTOCUE_LLM composer | 📋 PRD only, deferred by design |

`main` HEAD at handoff: `f4528b6` (post-P4 merge #217). Synced with origin.

## What "the old UI is gone" means precisely
- `#tab-nav` is `display:none` since P2; P5 removed the `#tab-discover` button.
- Navigation = workbench **rail places** (Duplicates `#wb-dupes-place`, Discover
  `#wb-disc-place`) + **⌘K palette** + **crates**. Users never see tabs.
- Residual `#tab-cues`/`#tab-library` buttons remain as **inert hidden markup**;
  the `switchTab(name)` plumbing is **load-bearing** (the Discover place calls
  `switchTab('discover')` to swap the centre pane). Don't delete switchTab.
- Deleting the two dead tab buttons is an optional cosmetic cleanup.

## The "place" pattern (P3 + P5 — copy this for any future place)
A rail place swaps the workbench **centre pane** (not full-bleed):
- `docs/js/v2/workbench/{duplicates,discover}.js` — ES modules; own ONLY the
  rail entry + the swap + lazy first-load. Drive legacy via `window.ACBridge` /
  `window.DiscoverV2`; **never fetch endpoints directly, never import legacy**.
- Swap = toggle `hidden` + a `body.wb-place-*` class ONLY; `#track-list` is NEVER
  detached (Virtualizer/sticky invariants, TASK-033/037); CSS
  `display:none !important` backstop under the body class.
- Places are **mutually exclusive** (Duplicates ↔ Discover): cross-deactivate in
  both `activate()`s + every rail/crate exit (`shell.js`, `rail.js`).
  `_renderCrates` paints no crate `.active` while a place owns the centre
  (`autocue:wb-place-change` event).
- P3 restore = A-layer **status-sentence sheet** (`docs/js/v2/restore-sheet.js`,
  `#status-restore` fact → `#wb-restore-sheet`), fed by `autocue:duplicates-deleted`.
- P5 release detail re-hosts in `#wb-inspector` via a `_mode` ('track'|'release')
  flag on `inspector.js`; `switchTab('discover')` scroll-resets to top on
  grid-return (accepted tradeoff, documented in `discover.js` header).

## P4 Nightboard — SHIPPED (#217, 2026-06-13)
Full-bleed set-builder canvas **mode** (not a rail place): `body.nb-active` hides
rail+grid+inspector (grid HIDDEN, never detached — TASK-033/037), canvas owns the
body, global topbar stays. Entered by the `#nb-open-btn` verb + `open-nightboard`/
`build-set` ⌘K commands; local-mode only. VISUALIZE-only over the FROZEN
`setbuilder.py` + `transitions.py` + REST surface (`/api/setbuilder`,
`/api/transitions/score`, `/api/setbuilder/alternatives`, `/api/tracks/{id}/energy`,
`/api/playlists`) — **zero backend edits; pytest untouched-green = drift proof.**
Modules: `docs/js/v2/nightboard/{mode,set-model,canvas,joint-popover,tray}.js`.
Full module detail lives in the CLAUDE.md "Nightboard (P4)" bullet.
- **How the 3 plan open-questions resolved:** (1) no `transition_advice` REST
  field — popover uses `mix_advice` + the 3 `explanation` strings, no JS port;
  (2) joint bands ≥85/≥70 shipped as presentation constants (`JOINT_BANDS` in
  `canvas.js`) — real-library calibration left as a future tweak; (3) inspector
  reused at mode 'track' via `body.nb-inspecting`, no collision with P5's `_mode`.

## Proven implementer workflow (reuse for P6 / any future slice)
Launch an implementer via the Agent tool with `isolation: "worktree"`, hand it
the plan + any open questions, tell it to **commit per task** (the P3 agent wedged
before committing and nearly lost a day). Arm a **liveness watchdog** (Monitor
polling branch commit count + transcript byte-staleness + PR-open) — this
caught/avoided the wedge on P5 and worked again for P4. On PR open: independent
code-review (code-reviewer agent), **verify any "critical" empirically** before
acting (the P5 reviewer's "critical" was a false positive — the flagged tests
were network-gated skips, not failures), fix real findings on the branch, then
admin-merge.

## Validate / gate
Three-leg stack (run e2e ALONE — #189 contention flake):
```
pytest                                   # 1449 passed, 7 skipped (2026-06-14)
npm test                                 # 882 passed (2026-06-14)
cd tests/e2e && npx playwright test      # run alone
```
No linter is wired (no ruff/eslint); the three-leg stack is the gate.
**Baseline e2e behaviour under full-suite contention (#189 — NOT regressions):**
a contended full run drops up to ~14 tests, ALL in Discover + action-bar; each
passes on an isolation re-run (verified 2026-06-14). The set:
- `v2-discover-shell.spec.ts` — the full 7-test P5 place suite (a–h)
- `discover-v2.spec.ts` — feed-render / inspector-rehost / `?` help / Save (4)
- `per-control-sweep` — `action-bar-clear` + `action-bar-preview` (the 2 that
  want a dedicated triage) + `disc-v2-refresh-btn`
Re-run any failing Discover spec file ALONE (`npx playwright test <file>`) to
confirm green before assuming a regression. P4 `v2-nightboard.spec.ts` is NOT in
this set — it passes in the full run.

## Open items
- [PR #214](https://github.com/HenriGeorge/AutoCue/pull/214) — P4 plan-only PR,
  now **redundant** (the plan merged inside #217) and CONFLICTING; **close it**.
- Stale merged-branch worktrees prunable: `.claude/worktrees/v2-p4-nightboard`
  (#217) + `.claude/worktrees/hopeful-turing-3fe017` (P2, #211) — `git worktree remove`.
- [Issue #187](https://github.com/HenriGeorge/AutoCue/issues/187) — sticky-overlap on the *legacy* Cues tab; only reachable via opt-out (`ac_workbench='0'`) now — effectively moot, **closeable**.
- 2 baseline action-bar e2e failures want a dedicated triage.
- Optional cleanup: delete the inert `#tab-cues`/`#tab-library` buttons.
- Next build work: only **P6** (AUTOCUE_LLM composer) remains, deferred by design.

## Run / verify locally
`python -m autocue serve --port 7434 --no-browser` (use 127.0.0.1, never
localhost — memory rule). Real master.db; read-only browse is safe; NEVER run a
real (non-dry-run) delete or a real Discover scan against it — use route mocks /
a scratch DB copy. To serve a worktree's code, run `python -m autocue serve` from
that worktree (the installed `autocue` resolves to the main repo's `docs/`).

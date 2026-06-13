# HANDOFF — AutoCue 2.0 redesign (2026-06-13)

Autonomous phased build of the B "Crate Console" redesign. **P0–P3 + P5 are
MERGED to `main`.** The workbench is the default local-mode home and the legacy
tab UI is retired. Decisions locked via socratic grill — see
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
| **P5 Discover into the shell** (retires `#tab-discover`) | ✅ merged **#215** |
| P4 Nightboard canvas mode | 📋 plan ready ([PR #214](https://github.com/HenriGeorge/AutoCue/pull/214), unmerged); NOT started |
| P6 AUTOCUE_LLM composer | 📋 PRD only, deferred by design |

`main` HEAD at handoff: `82b7bb9` (post-P5 merge). Synced with origin.

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

## P4 (next building phase) — plan is ready, NOT started
Plan: `.claude/PRPs/plans/v2-p4-nightboard.plan.md` (in [PR #214](https://github.com/HenriGeorge/AutoCue/pull/214), unmerged).
PRD: `.claude/PRPs/prds/v2-p4-nightboard.prd.md`. Branch to use: `feature/v2-p4-nightboard`.
- Nightboard is a full-bleed **MODE** (not a rail place): hides rail+grid+inspector
  via `body.nb-mode`, entered by a verb (toolbar `#nb-open` + ⌘K). 7 tasks T1–T7.
- VISUALIZE-only over the FROZEN `setbuilder.py` + `transitions.py` + their REST
  surface (`/api/setbuilder`, `/api/transitions/score`, `/api/setbuilder/alternatives`,
  `/api/playlists`) — NO backend edits, NO new endpoint. pytest stays green = zero-drift proof.
- 3 OPEN QUESTIONS flagged in the plan to resolve at implementation:
  1. No `transition_advice` REST field → use `mix_advice` + the 3 `explanation`
     strings (don't port the join to JS).
  2. Mockup joint-score thresholds (85/70, `design-D.html:405`) may be
     miscalibrated vs the product's lower real scale — calibrate before locking.
  3. Inspector `_mode` flag: P5 shipped 'track'|'release'; P4's only inspector
     change is an additive `hostId` param on `renderInspector` — re-verify the
     signature on rebase, keep it additive; add a 'tile' mode only if needed.

## How to run P4 (the proven workflow that worked for P5)
Launch an implementer via the Agent tool with `isolation: "worktree"`, hand it
the P4 plan + the 3 open questions, tell it to **commit per task** (the P3 agent
wedged before committing and nearly lost a day). Arm a **liveness watchdog**
(Monitor polling the branch commit count + transcript byte-staleness + PR-open) —
this caught/avoided the wedge on P5. On PR open: independent code-review
(code-reviewer agent), **verify any "critical" empirically** before acting (the
P5 reviewer's "critical" was a false positive — the flagged tests were
network-gated skips, not failures), fix real findings on the branch, then admin-merge.

## Validate / gate
Three-leg stack (run e2e ALONE — #189 contention flake):
```
pytest                                   # 1442 passed, 7 skipped (current)
npm test                                 # 843 passed (current)
cd tests/e2e && npx playwright test      # run alone
```
No linter is wired (no ruff/eslint); the three-leg stack is the gate.
**Known-baseline e2e failures (NOT regressions — fail on `main` independently):**
`per-control-sweep › action-bar-clear` + `action-bar-preview` (want a separate
triage). Discover-v2 `?`/Save + `disc-v2-refresh-btn` are #189-flaky (pass in isolation).

## Open items
- [PR #214](https://github.com/HenriGeorge/AutoCue/pull/214) — P4 plan, unmerged (queued).
- [Issue #187](https://github.com/HenriGeorge/AutoCue/issues/187) — sticky-overlap on the *legacy* Cues tab; only reachable via opt-out (`ac_workbench='0'`) now — effectively moot, **closeable**.
- 2 baseline action-bar e2e failures want a dedicated triage.
- Optional cleanup: delete the inert `#tab-cues`/`#tab-library` buttons.

## Run / verify locally
`python -m autocue serve --port 7434 --no-browser` (use 127.0.0.1, never
localhost — memory rule). Real master.db; read-only browse is safe; NEVER run a
real (non-dry-run) delete or a real Discover scan against it — use route mocks /
a scratch DB copy. To serve a worktree's code, run `python -m autocue serve` from
that worktree (the installed `autocue` resolves to the main repo's `docs/`).

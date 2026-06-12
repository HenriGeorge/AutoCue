# HANDOFF — AutoCue 2.0 redesign (2026-06-12)

Autonomous phased build of the B "Crate Console" redesign. **Resume point: P2 T4
part 2b (thin-row grid compaction).** Decisions locked via socratic grill — see
`.claude/PRPs/prds/autocue-2-program.prd.md` + memory `project_autocue_2_redesign.md`.
Do NOT re-litigate them.

## Branch / PR stack (merge in this order)
- **PR #206** `claude/distracted-jemison-2b2c96` — fixes the 8 known e2e baseline
  failures. MERGE FIRST so all branches inherit a green gate.
- **PR #207** `claude/hopeful-turing-3fe017` — UI aliveness pass.
- **PR #208** `feature/v2-p0-foundations` (base #207) — P0 file split. COMPLETE.
- **PR #209** `feature/v2-p1-global-layer` (base #208) — P1 status sentence + ⌘K
  palette. COMPLETE (full e2e was stopped to unblock P2; re-run the standard
  "8 known, 0 new" before merging — P1 changes were validated by the targeted
  affected-specs run: 26 passed, 1 = #206 baseline).
- **`feature/v2-p2-workbench`** (base P1, **current**) — P2 in progress, 3 commits,
  NOT pushed/PR'd yet. Push + PR when P2 reaches a coherent milestone.

## P2 — workbench (the visible redesign). Plan: `.claude/PRPs/plans/v2-p2-workbench.plan.md`
Research: `.claude/PRPs/research/v2-p2-workbench-findings.md`. Mockups: design-B/E.html.
Flag-gated: `localStorage.ac_workbench='1'` (⌘K → "Toggle workbench (beta)").
Additive — old tabbed UI byte-identical when off.

### Done (committed on feature/v2-p2-workbench)
- **Shell** (0b3bf88): 3-pane fixed-flank layout (rail + document-scrolled centre +
  inspector). Path (a) — Virtualizer/#tracks-sticky/document-scroll UNTOUCHED. Rail
  smart crates (All/No-cues/Phrase-ready/Already-cued) with live counts; clicking
  filters via `_wbCrate` in `filteredTracks` (driven by `ACBridge.setCrate`).
- **Inspector** (af2e34e): row click → right pane re-hosts the legacy builders
  (energy curve, mixability + breakdown, classification, existing cues A–H + times,
  similar). Capture-phase #track-list click pre-empts the card select-toggle.
- **Uniform flat grid** (4956ff4): wb-active skips album-group view → uniform
  virtualized list (verified: virtualized, 0 album headers, all rows 160px).
- `ACBridge` extended with state readers + fn pass-throughs (08-set-builder-boot.js).
- New v2 modules: `docs/js/v2/workbench/{shell,inspector}.js`.

### Remaining P2 (resume here)
- **T4 part 2b — thin-row compaction (NEXT, do carefully — virtualizer surgery)**:
  add `buildWbRow(track)` (~52px, design-B 10-col: checkbox/title-artist/BPM/key/
  energy-mini/mix/class/cues), and in `renderTracks` (06-render.js ~1481) branch on
  `body.wb-active` → use the smaller itemHeight + buildWbRow in `Virtualizer.attach`.
  CARD_HEIGHT_PX (01-core.js:46) is a mutable var but DON'T mutate it globally — pass
  a per-attach itemHeight. Keep data-track-id on the row (inspector click), a
  mix-score-chip[data-track-id] (for _mixObserver lazy load). Gate with a new
  `tests/e2e/v2-workbench-grid.spec.ts` (uniform height, bounded recycling, row→
  inspector). This is the piece that makes the centre match mockup B's density.
- **T5** rail: real playlists (`/api/playlists`), saved filters (localStorage,
  mirror `ac_discover_filters`); intelligence-keyed crate counts deferred (mix/class
  are per-track lazy — note the bulk-source gap).
- **T6** grid-toolbar verbs: relocate auto-tag/comment-enrich/preview-apply; normalize
  `enrichComments` to `activeTracks()`; de-couple option controls from hidden DOM.
- **T7** health ring rail card (`#wb-rail-health`, already in markup) + fix stack
  (relocate `_renderHealthSummary`) + G deterministic lede (template, no LLM) +
  new-import event banner (needs `autocue:tracks-loaded` dispatch — add after
  parsedTracks populated).
- **T8** F proposal/applied stamps + per-track approve ticks on pendingCues→apply
  (gate apply payload to approved∩pending); H review-unlocks-apply on destructive ops.
- **T9** retire the 3 tabs at parity; both-themes audit; e2e (selectors-exist +
  control-inventory for #wb-* ids) + Lighthouse-not-worse; push + PR.
- **P6 (later, user-gated)** AUTOCUE_LLM: the ⌘K composer seam already exists.

## Run / verify
- Server (this worktree's code): `python -m autocue serve --port 7433 --no-browser`
  (the `autocue` wrapper resolves to a different Python — use `python -m`). Serves
  docs/ live, so frontend edits show on browser refresh; Python edits need restart.
  Use 127.0.0.1 (memory rule). Real master.db, read-only browse is safe.
- Three-leg gate: `pytest` · `npm test` (720) · `cd tests/e2e && npx playwright test`.
  RUN e2e ALONE (contention causes the #189-class flake — confirm failures with 3×
  isolated runs before blaming your change).
- New interactive id → add to `tests/e2e/control-inventory.json` or the drift guard
  fails (the #206-baseline guard already fails on pre-existing duplicates ids).

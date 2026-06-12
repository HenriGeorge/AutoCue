# HANDOFF — AutoCue 2.0 redesign (2026-06-12)

## What this is
Autonomous execution of the AutoCue 2.0 redesign. Design exercise (8 mockups +
synthesis) → socratic decision grill → 7 locked decisions → phased build (P0–P6).
**Resume point: P1 task T3.**

## Locked decisions (DO NOT re-litigate — full record in memory + program PRD)
`.claude/PRPs/prds/autocue-2-program.prd.md` and
`~/.claude/projects/-Users-henrigeorge-Projects-AutoCue/memory/project_autocue_2_redesign.md`:
1. Home = **B Crate Console** workbench (rail + grid + inspector); C's health
   ring is a rail card + new-import event banner, not a tab.
2. Maintenance grammar: duplicates = a *place*; cue-tools/auto-tag/comments =
   selection *verbs*; backups = a *sheet*.
3. Full **Nightboard** (D) ships as a real mode.
4. **Multi-file, NO build step**: `docs/index.html` (markup) + `docs/css/app.css`
   + `docs/js/01..08-*.js` (legacy classic, shared globals) +
   `docs/js/v2/*` (ALL new code = ES modules, `window.AC2`/`window.ACBridge`).
5. XML/Pages mode frozen; 2.0 shell = local mode only.
6. Organs: F proposal→applied stamps + per-track ticks; H review-unlocks-apply;
   G deterministic lede (no LLM). Conversational = a future **door** (`AUTOCUE_LLM`,
   P6) — the ⌘K palette is designed as the composer seam, nothing live now.
7. Global A-layer: status sentence + ⌘K palette + ink-pill dock.
v1 = P0–P2. Each phase = own plan + branch + PR, three-leg green per merge.

## Branch / PR state
- `main` (d7de2f6)
- **PR #206** `claude/distracted-jemison-2b2c96` — fixes the 8 known e2e
  baseline failures (control-inventory ×2 + per-control-sweep ×6). MERGEABLE.
  **Merge this FIRST** so all branches inherit a green e2e baseline.
- **PR #207** `claude/hopeful-turing-3fe017` — UI aliveness pass (30 fixes).
- **PR #208** `feature/v2-p0-foundations` (base #207) — **P0 split, COMPLETE.**
- `feature/v2-p1-global-layer` (off P0, **current branch**) — P1 T1+T2 committed,
  NOT pushed yet. Push + PR when P1 is further along.
Merge order: #206 → #207 → #208 → P1 → P2.

## e2e baseline reality
8 deterministic failures on main (fixed by #206). Our gates report "8 known,
0 new" = green-equivalent. **qa-smoke "filter toggles" was a load-flake** (issue
#189), hardened in P0 (commit 5d93818) — do NOT re-investigate; 3/3 on a quiet
host. **Lesson: confirm flaky e2e failures with 3× isolated runs on a quiet host
before attributing to your change** (I wasted time falsely blaming the v2 module
script for this — bisection under CPU contention lies).

## P1 — resume at T3 (plan: `.claude/PRPs/plans/v2-p1-global-layer.plan.md`)
Done: T1 (status `?include_rb=1`), T2 (`window.ACBridge` + events). Remaining:
- **T3 status sentence**: convert `#status-db/count/scan/rb` spans
  (index.html:53-70) to `<button>` (updateAppStatus writes innerHTML, tag-
  agnostic — safe), add hidden `#status-needcues`/`#status-health`; new module
  `docs/js/v2/status-sentence.js` (pure `deriveFacts()` + 30s `?include_rb=1`
  poll feeding the existing `updateAppStatus` which today never gets
  rekordboxRunning). **MUST add the new button/input ids to
  `tests/e2e/control-inventory.json`** or the drift guard fails.
- **T4 palette logic**: `docs/js/v2/fuzzy.js` + `commands.js` (pure, direct
  vitest import). Every command delegates to existing buttons via `.click()`.
- **T5 palette overlay**: `docs/js/v2/palette.js` + minimal markup; ⌘K//,
  capture-phase keydown for strict priority over app.js shortcuts; inert
  "Ask AutoCue (coming soon)" composer seam (document for P6 AUTOCUE_LLM).
- **T6** action-bar relabel only (P2 builds the real dock).
- **T7** e2e: selectors-exist + control-inventory.json + palette smoke spec.
- **T8** Chrome both-themes verification (use 127.0.0.1, NOT localhost).
- **T9** docs + three-leg gate + push + PR.
v2 modules import into `docs/js/v2/main.js`. Read legacy ONLY via window.ACBridge
(tracks/healthSummary/isLocalMode/selectedCount) + the two CustomEvents.

## P2 ready
Plan `.claude/PRPs/plans/v2-p2-workbench.plan.md` + research
`.claude/PRPs/research/v2-p2-workbench-findings.md`. T1 = scroll-architecture
spike (path a: document-scroll + sticky/fixed flanks, Virtualizer untouched).
Biggest task = split buildTrackCard → buildGridRow + renderInspector.

## How to run / validate
- Three-leg gate: `pytest` · `npm test` · `cd tests/e2e && npx playwright test`.
- **Run e2e ALONE** (no concurrent suite) — contention causes the #189-class
  flake. A full run is ~27 min.
- Chrome check (Pages mode): `python3 -m http.server 8802 --bind 127.0.0.1` in
  `docs/`, load `http://127.0.0.1:8802/index.html`. Local-mode features need
  `autocue serve` (sandbox via the e2e harness).
- Mockups for reference: `/var/folders/kg/.../T/design-{A..H,E}.html` (design-A =
  palette/status-sentence canonical; design-B = workbench; design-E = synthesis).

## Open question for the user (non-blocking)
P6 `AUTOCUE_LLM` (conversational door, design F) — needs a Claude API key +
opt-in; design it after P2 ships and real workbench usage informs it.

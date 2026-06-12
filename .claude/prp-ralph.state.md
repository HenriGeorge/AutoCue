---
phase: "P2 — workbench (near v1)"
updated: "2026-06-12"
---

# AutoCue 2.0 — Ralph state

## main (4d3ca3d): P0 + P1 + #206 (e2e) + #210 (design-preview). Gate green.

## P2 — feature/v2-p2-workbench (13 ahead of main, RECONCILED with main)
DONE: 3-pane shell · crates · live inspector · uniform→**dense thin-row grid**
(buildWbRow, 46px, design-Z columns) · top-bar toolbar · **full rail**
(playlists /api/playlists · saved filters localStorage · health-ring card + G
lede) · **F organ** (proposal stamps + approve ticks + approved∩pending apply
gate) · **H organ** (review-unlocks-apply on cue-tools delete/shift via
_confirmDialog reviewRequired) · **polish** (dark mode, no-waveform, health card).
Vitest 762 · pytest 1442 · targeted e2e 32/0 · Chrome-verified both themes vs
real 2,928-track lib · FULL e2e gate running (bsoj799tg).

REMAINING for v1 + beyond:
- Discoverability: a header "Workbench (beta)" toggle (only ⌘K + flag now).
- Retire 3 tabs: DEFERRED — needs Library (duplicates/cue-tools as places/verbs,
  P3) + Discover (P5) reachable in the workbench first; tabs stay for now.
- Open the P2 PR (base main) once the full gate is green.
- adopt-autocue-design-system PRD still OPEN (token reconciliation in
  docs/index.html — NOT done by #210's preview; my P2 CSS uses fallbacks like
  var(--font-mono, var(--mono)) because the app :root lacks the system names).

## Open issues: #187 (sticky overlap, legacy Cues — superseded by P2).
## Plans: v2-p2-workbench active; p0/p1 in completed/.

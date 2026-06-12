---
phase: "P2 — workbench (mid-flight)"
updated: "2026-06-12"
---

# AutoCue 2.0 — Ralph state

## Merged to main (acc1afd): P0 + P1 + #206 (e2e baseline). Gate 180/0 green.
- PRs #206/#207 merged; #208/#209 closed-as-merged.

## P2 — feature/v2-p2-workbench (7 commits ahead of main)
DONE: shell (3-pane, crates) · live inspector · uniform flat grid · top-bar
toolbar · **dense thin-row grid (buildWbRow, 46px, design-Z columns,
empty-state fix)** · mockups vendored (docs/design/mockups/).
Vitest 732 · grid e2e 4-spec green · Chrome-verified vs real 2,928-track lib.

REMAINING P2 (this iteration, via subagents):
- A: rail completion — playlists (/api/playlists) + saved filters (localStorage)
  + health-ring rail card (#wb-rail-health) with G deterministic lede + Fix-it.
- B: F organ — proposal stamps + per-track approve ticks on grid rows;
  gate apply payload to approved∩pending.
THEN: H review-unlocks-apply · retire the 3 tabs at parity · grid polish
(column density, dark-mode, "no waveform" label) · full three-leg gate · PR.

## Plans: v2-p0/v2-p1 → plans/completed/. v2-p2-workbench active.
## Open issues: #187 (sticky overlap, legacy Cues — superseded by P2; commented).

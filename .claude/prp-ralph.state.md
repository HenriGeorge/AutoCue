---
phase: "P3 + P5 merged; P4 plan queued"
updated: "2026-06-13"
---

# AutoCue 2.0 — Ralph state

See **HANDOFF.md** (repo root) for the authoritative current state. Summary:

## main (82b7bb9): P0+P1+P2+P3+P5 merged. Workbench is the default home; tabs retired.
- P2 workbench **default-on** in local mode (c3dcff0, `ac_workbench !== '0'`).
- **P3 #212** — Duplicates as a rail place (centre-pane swap, restore sheet).
- **P5 #215** — Discover as a rail place; `#tab-discover` retired (legacy tab UI gone).
- Governance: tracked pre-commit hook (`.githooks/pre-commit`, `core.hooksPath`)
  **blocks direct commits to main** — everything via branch → PR → `gh pr merge`.

## Next: P4 Nightboard — plan ready, NOT started
- Plan `.claude/PRPs/plans/v2-p4-nightboard.plan.md` (PR #214, unmerged).
- Full-bleed canvas MODE; visualize-only over frozen setbuilder/transitions; 7 tasks.
- 3 open questions flagged in the plan (transition_advice REST gap, joint-score
  threshold calibration, inspector mode-flag rebase). Run via worktree implementer
  + liveness watchdog + per-task commits (the proven P5 workflow).

## Open: PR #214 (P4 plan) · issue #187 (legacy Cues sticky overlap — moot/closeable)
## Known-baseline e2e fails (not regressions): action-bar-clear/preview.
## Gate: pytest 1442 · vitest 843 · e2e run ALONE (#189).

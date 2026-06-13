# AutoCue 2.0 — Program PRD (the redesign umbrella)

## Problem Statement

The AutoCue web app grew feature-by-feature into a three-tab page where the most
powerful surfaces (workbench-grade triage, health, duplicates, set building) are
buried in a junk-drawer Library tab, and the 14.5k-line single-file architecture
taxes every change. A design exercise (8 mockups, A–H + synthesis E) and a
socratic decision grill (2026-06-12) locked a new shape. This PRD records the
program; each phase ships as its own PRD + plan + PR.

## Locked decisions (do not re-litigate; source: design grill 2026-06-12)

1. **Home = B "Crate Console" workbench** — left rail (smart crates + playlists +
   saved filters), dense center grid, right inspector (energy curve, phrase strip
   + A–H ticks, cue reasoning, anchor-transition card, similar tracks).
   C's health ring is a **live rail card** that expands into the fix stack;
   new-import moments surface it as an **event banner**. C is an event, not a place.
2. **Maintenance grammar** — *places* for decisions, *verbs* for operations,
   *sheets* for emergencies: Duplicates = rail place (center-pane view);
   cue tools / auto-tag / comment enrichment = selection verbs in the grid
   toolbar + ⌘K commands; backups/restore = sheet off the status sentence.
3. **Full Nightboard** (D) ships as a real mode: horizontal set timeline, energy-
   curve tiles, scored transition joints with explanation popovers + swap
   alternatives, gravity tray, zone bands, set-wide energy arc.
4. **Architecture: multi-file, no build step.** `docs/index.html` stays the
   entry; CSS and JS split into `docs/css/*` + `docs/js/*`. Legacy code may
   remain classic scripts sharing globals (documented); **all NEW v2 code is
   native ES modules** loaded via `<script type="module">`. No bundler, ever —
   FastAPI/StaticFiles + `python -m http.server` serving must keep working.
5. **XML/Pages mode frozen** — no server detected → the existing simple
   drop-zone flow, unchanged, plus a one-line "Run `autocue serve` for the full
   experience" hint. The 2.0 shell renders in local mode only.
6. **Organ transplants**: F's proposal→applied stamps + per-track approve ticks
   on the preview/apply path; H's review-unlocks-apply consent gradient on
   destructive operations; G's deterministic template-generated lede (no LLM)
   atop the health expansion. **Conversational = a door**: the ⌘K palette is
   designed as a composer seam; a later opt-in `AUTOCUE_LLM` feature (Claude
   API) may route unmatched natural-language input to an assistant. No cloud
   dependency before that phase.
7. **Global A-layer everywhere**: clickable status-sentence header
   (`2,789 tracks · 142 need cues · health 78/100 · ● Rekordbox closed`) +
   ⌘K command palette + single contextual ink-pill action dock.

## Phases (each = own PRD + plan + branch + PR, three-leg stack green per merge)

| Phase | Scope | Status |
|---|---|---|
| P0 | Foundations: file split + test migration + XML freeze | ✅ **MERGED** (main, #208) |
| P1 | Global layer: status sentence + ⌘K palette + composer seam | ✅ **MERGED** (main, #209) |
| P2 | Workbench-as-home (= **v1 milestone**): rail/grid/inspector, F stamps/ticks, H consent, G lede. **Default-on** in local mode (`ac_workbench !== '0'`, c3dcff0); opt-out reverts to the legacy UI | ✅ **MERGED** (main, #211) — default-on shipped post-merge |
| P3 | Duplicates as a place (restyle existing logic into center-pane view) | ✅ **MERGED** (main, #212) |
| P4 | Nightboard canvas mode | 📋 plan ready (PR #214); not started |
| P5 | Discover restyle into the shell + theme audit + aliveness round 2 — **retires `#tab-discover`** | ✅ **MERGED** (main, #215) |
| P6 | `AUTOCUE_LLM` opt-in: palette composer routes to a Claude-API assistant with artifact responses (design after P2 usage) | 📋 PRD only (deferred) |

**Tab retirement:** the legacy tab nav (`#tab-nav`) has been `display:none` since P2; P5 removed the `#tab-discover` button. The workbench (rail places + ⌘K + crates) is the sole navigation in local mode. Residual `#tab-cues`/`#tab-library` buttons remain as inert hidden markup; the `switchTab` plumbing is reused by the workbench to swap the centre pane.

## What we're NOT building

- A build step / framework / npm runtime dependencies for the web app.
- A background watcher daemon (H's Stagehand) — out of scope for this program.
- Pages-mode (no-server) rendering of the 2.0 shell.
- Wholesale ES-module conversion of legacy code (opportunistic only).

## Success metrics

- v1 (post-P2): every daily session starts and ends in the workbench (default-on
  shipped 2026-06-12, c3dcff0); status sentence + ⌘K reachable from anywhere;
  three-leg stack green; both themes verified per phase. *"The old tabs are gone"
  completes at P5, not P2*: P2 made the workbench home, but the Library tab's
  duplicates surface retires with P3 and the Discover tab with P5 — the tabs stay
  until those phases give their features a workbench home.
- Program: all five rules of the design system hold on every new surface;
  mockup-to-product parity judged against `design-E.html` (+ B/C/D for their
  modes); Lighthouse perf not worse than baseline on a 3.7k-track library.

## References

- Mockups: `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/design-{A..H,E}.html`
- Decision memory: `~/.claude/projects/-Users-henrigeorge-Projects-AutoCue/memory/project_autocue_2_redesign.md`
- Aliveness pass: PR #207

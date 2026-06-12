# AutoCue 2.0 — P2 Workbench research findings (read-only code map)

> Captured 2026-06-12 from a codebase-research agent. Feeds the P2 plan author.
> All citations are post-P0-split: `docs/js/app.js` (legacy classic, 10,811 lines),
> `docs/index.html` (markup), `docs/css/app.css`, v2 seam `docs/js/v2/main.js`.

## The central tension (P2's #1 blocker)
`design-B.html` is a **3-pane CSS grid with an INNER-scrolling center** (`#workbench`
`grid-template-columns:224px 1fr 364px`; `#grid-scroll{overflow-y:auto}`; a 10-col table
`.grid-cols`). The current app is a **single 900px document-scrolled column of 160px
cards**. The Virtualizer runs `scrollSource:'window'` (document scroll) specifically to
preserve TASK-037 (sticky `#tracks-sticky` + fixed `#action-bar`) and the
`topOcclusionFn` snap. **Resolve before any feature work.**
- **Recommended path (a):** keep document scroll; make rail (`sticky`/`fixed`) +
  inspector (`sticky`) flank a document-scrolled center — Virtualizer unchanged.
- Path (b): switch Virtualizer to `scrollSource:'container'` (supported, app.js:88-91)
  and re-solve sticky + occlusion for an inner scroller — CLAUDE.md TASK-037 warns
  against this.

## Key reuse map (inspector = re-host existing builders on `focusedId`)
| Inspector element | Builder fn (app.js) | Endpoint | Verdict |
|---|---|---|---|
| Energy curve | `_renderEnergySparkline` 7433-7480 | `/tracks/{id}/energy` | reuse (bigger viewBox) |
| Mixability | `_renderMixabilityChip` 7382-7431 | `/tracks/{id}/mixability` | reuse wholesale |
| Classification | `_renderCategoryChip` 7345-7364 | `/tracks/{id}/classification` | reuse |
| Similar | `_toggleSimilarPanel` 7278-7343 | `/tracks/{id}/similar` | reuse |
| Phrase strip + A–H ticks | `buildPhraseStrip` 375-422 / `_appendPhraseStrip` 7491 | `phraseCueState` | reuse — exactly B's strip |
| Cue reasoning | `_explainCue` 7208-7276 | pure client | reuse |
| Anchor-transition card | `showTransitionScore` 7114-7179 | `POST /transitions/score` | reuse fetch+scoring; rebuild presentation (modal→inline, trigger off `prevFocusedId`) |

## Other findings
- **buildTrackCard (7580-8046) is a monolith** doing row identity AND all inspector
  widgets. Must split → thin `buildGridRow` (fixed-height, ~10 cols) + `renderInspector
  (focusedId)`. FLIP/observer/`_updateTrackCardCues` machinery assumes the full builder.
- **No single-focus state today** — only `selectedTrackIds` (Set, app.js:477) +
  `nowPlayingId`. Need net-new `focusedId`/`prevFocusedId` + grid keyboard nav.
- **Filters → smart crates**: `filteredTracks()` 7011-7052 (returns indices),
  `sortedTracks()` 7185, `activeTracks()` 7057 (write-op target). Structural crates
  (no-cues=`existingHotCues===0` [NEW predicate, data present], phrase-ready=
  `phraseOnlyFilter`, already-cued [NEW predicate]) are cheap client-side.
  **Intelligence-keyed crates (Mix-80, Peak-time) are NOT bulk-available** — mixability/
  classification are per-track lazy fetches. Need a bulk endpoint or "counts fill on
  scroll." Saved filters need new `localStorage` persistence (mirror `ac_discover_filters`).
- **Verbs are easy to relocate** — auto-tag (`autoTagTracks` 1867), comment-enrich
  (`enrichComments` 5471 — ⚠ uses `filteredTracks()` not `activeTracks()`, normalize),
  cue preview/apply all key off the selection already. Friction: they READ options from
  hidden DOM nodes (`#cue-tools-section`, `#comment-enrich-section`) — must move controls
  to popovers or parameterize the functions.
- **Health**: `scanLibraryHealth` 712, `healthData` 540 (per-track), `healthLastSummary`
  541 (`.library_score`,`.total`,`.no_cues`,`.no_phrase`,`.no_beatgrid`,...),
  `_renderHealthSummary` 1459-1548 (ring + issue rows + split fix-tier buttons),
  `_applyHealthFix` 1550. Ring/fix-stack relocate directly. **G lede = net-new template**
  over the summary counts (no LLM). **New-import banner = net-new** (no import detection
  exists; needs track-count/id diff across loads).
- **F stamps/ticks**: `pendingCues` (451) IS the proposal state; F5 bar renders it per
  card (8019-8041). Apply (`applyToRekordbox` 5631) sends `activeTracks()`
  unconditionally — F's per-track approve = new `Set` gating the payload to
  approved+pending. Net-new state, slots onto existing pipeline.
- **v2 interop gap**: the workbench shell (first real v2 module) must read legacy fns
  (`filteredTracks`, `buildTrackCard`, the 6 builders) which are **plain top-level
  functions, NOT on window**. Step 1 must expose them on `window`/`AC2` per the interop
  contract (note: this overlaps the P1 `window.ACBridge` work — extend it).

## P2 sequencing (each step shippable green)
1. Expose legacy globals on window/AC2 (pure plumbing) — extends P1's ACBridge.
2. Resolve scroll architecture (spike + Playwright e2e for sticky+occlusion survival;
   JSDOM can't catch layout — see jsdom-layout-blind-spot memory). Prove path (a).
3. Three-pane shell skeleton (v2 module, local-mode only, additive alongside old UI).
4. Split buildTrackCard → buildGridRow + renderInspector; introduce focusedId + kbd nav.
   **Biggest PR** — gate with fixed-height + virtualization e2e.
5. Left rail: structural crates (client predicates) + playlists (existing /api/playlists
   re-fetch) + saved filters (new localStorage). Defer intelligence-keyed crate counts.
6. Grid-toolbar verbs (relocate auto-tag/enrich/preview-apply; normalize enrichComments
   to activeTracks; de-couple option controls from hidden DOM).
7. Health ring rail card + fix stack + G lede + new-import event banner.
8. F stamps/ticks + H review-unlocks-apply on destructive ops.
9. Retire tabs at parity; both-themes audit; final e2e + Lighthouse-not-worse on large lib.

Steps 1-3 de-risk and merge trivially; 4 is the heavy lift gated by §2; 5-8 are mostly
relocation of existing tested logic.

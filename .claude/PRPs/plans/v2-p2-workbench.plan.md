# Feature: AutoCue 2.0 — P2 Workbench-as-home (the v1 daily-driver milestone)

## Summary

Turn the three-pane **B "Crate Console"** workbench into the home surface of the
local-mode app: a left **rail** (structural smart crates + playlists + saved
filters), a dense document-scrolled **center grid** of fixed-height rows, and a
right **inspector** keyed off a single `focusedId` that re-hosts the six existing
intelligence builders. Then relocate the operation **verbs** (auto-tag, comment
enrich, cue preview/apply) into the grid toolbar + ⌘K, surface health as a **rail
ring card → fix stack + deterministic lede + new-import banner** (C-as-event),
graft F's **proposal/applied stamps + per-track approve ticks** onto the
pendingCues→apply pipeline and H's **review-unlocks-apply** consent gate onto
destructive ops, and finally **retire the three tabs** once the shell reaches
parity. This is the milestone where every daily session starts and ends in the
workbench and the old tabs are gone.

The work is **additive and local-mode-only behind a `workbench` flag** until the
final retirement task — every task merges green on the three-leg stack with the
old tabbed UI still fully working. The **first task is a de-risking spike**:
prove path (a) — rail (sticky/fixed) + inspector (sticky) flank a DOCUMENT-scrolled
center with the Virtualizer untouched (`scrollSource:'window'`), TASK-037 sticky +
TASK-033 fixed-160px invariants preserved — backed by a NEW Playwright e2e
(JSDOM can't catch layout — jsdom-layout-blind-spot rule).

Source PRD: `.claude/PRPs/prds/autocue-2-program.prd.md` (phase P2, locked
decisions 1, 2, 6, 7). Primary code map: `.claude/PRPs/research/v2-p2-workbench-findings.md`.
Canonical interaction sources: `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/design-B.html`
(the 3-pane grid + inspector) and `design-E.html` (Library mode inside the full
shell). v2 module patterns + the `window.ACBridge` read-only bridge come from
`v2-p0-foundations.plan.md` + `v2-p1-global-layer.plan.md`.

## Key facts (verified against this worktree, post-P0 split)

- **P0 landed; P1 has NOT landed here.** Legacy JS is split into ordered classic
  files `docs/js/01-core.js` … `docs/js/08-set-builder-boot.js`; the v2 ES seam is
  `docs/js/v2/main.js` (`<script type="module">`, sets `window.AC2 = {}`). CSS is
  `docs/css/app.css` (2,276 lines); markup `docs/index.html` (1,474 lines).
  **`window.ACBridge` does not exist yet** (P1 introduces it) — T2 below either
  extends it if P1 has merged first, or stands it up if P2 lands first. v2 modules
  are directly importable in Vitest (`tests/web/**/*.test.js`, `type:"module"`);
  legacy logic is source-loaded via `loadAppHtml()` from `tests/web/_source.js`.
- **Virtualizer** (`docs/js/01-core.js`): `attach({container, itemHeight,
  totalCount, renderItem, buffer, scrollSource, onWindowChange, topOcclusionFn})`.
  `scrollSource:'window'` branch (01-core.js:82) computes the visible window from
  document scroll; the `topOcclusionFn` snap (01-core.js:101–120) only fires for
  `scrollSource==='window'`. The track list attaches at **06-render.js:1504** with
  `scrollSource:'window'` (06-render.js:1510) + a `topOcclusionFn` (06-render.js:1517).
  **This call must stay byte-identical through P2** — path (a) flanks it, never
  rewires it.
- **Sticky/fixed chrome**: `#tracks-sticky` (index.html:266, `position:sticky`
  anchored to document scroll, TASK-037), `#track-list` (index.html:396),
  `#action-bar` (index.html:1427, `position:fixed` viewport). CLAUDE.md
  TASK-037 invariant: the scroll source MUST stay document-level under
  virtualization; switching `#track-list` to inner `overflow:auto` breaks both
  sticky bars + the occlusion snap. **Path (a) honors this; path (b) is rejected.**
- **buildTrackCard monolith** at **06-render.js:592** (`buildTrackCard(track,
  cues, willSkip, opts)`) does row identity AND all inspector widgets AND wires
  `_toggleSimilarPanel` (06-render.js:589). `renderTracks` at 06-render.js:1142.
  FLIP/observer/`_updateTrackCardCues` machinery assumes the full builder. Must
  split → thin fixed-height `buildGridRow` + `renderInspector(focusedId)`.
- **Six inspector builders to reuse wholesale** (research §3): `buildPhraseStrip`
  (01-core.js:379, + A–H ticks), `_explainCue` (06-render.js:220, pure client),
  `_toggleSimilarPanel` (06-render.js:290), `_renderCategoryChip` (06-render.js:357),
  `_renderMixabilityChip` (06-render.js:394), `_renderEnergySparkline`
  (06-render.js:445); plus `showTransitionScore` (06-render.js:126, modal→inline,
  trigger off `prevFocusedId`). All are plain top-level functions → exposed via
  bridge in T2.
- **Selection / focus state** (`docs/js/01-core.js`): `selectedTrackIds` (Set,
  01-core.js:481), `nowPlayingId` (01-core.js:522). **No `focusedId` exists** —
  net-new `focusedId`/`prevFocusedId` + grid keyboard nav land in T4.
- **Filter/selection fns** (`docs/js/06-render.js`): `filteredTracks()` (06-render.js:23,
  returns indices), `activeTracks()` (06-render.js:69, the write-op target),
  `sortedTracks()` (06-render.js:197). Structural crate predicates are cheap
  client-side: no-cues = `Number(t.existingHotCues) === 0` (NEW predicate, data
  present from API `existing_hot_cues`), phrase-ready = existing `phraseOnlyFilter`,
  already-cued = `Number(t.existingHotCues) > 0` (NEW). **Intelligence-keyed crates
  (Mix-80, Peak-time) are NOT bulk-available** — mixability/classification are
  per-track lazy fetches → deferred (open question, T5).
- **Playlists**: `GET /api/playlists` exists (`autocue/serve/routes.py:133`,
  `response_model=list[PlaylistItem]`, schema at `autocue/serve/schemas.py:15`).
  Re-fetch on demand for the rail; no backend change needed.
- **Verbs**: `autoTagTracks` (02-local-ops.js:1334), `enrichComments`
  (04-app-chrome.js:7 — ⚠ reads `filteredTracks()` not `activeTracks()`, normalize),
  cue Preview (07-helpers-events.js:598, `#preview-cues-btn` → `activeTracks()` per
  #173), `applyToRekordbox` (04-app-chrome.js:167, sends `activeTracks()`
  unconditionally). Friction: verbs READ options from hidden DOM (`#cue-tools-section`,
  `#comment-enrich-section`) — relocate controls to popovers or parameterize.
- **Health** (`docs/js/02-local-ops.js`): `scanLibraryHealth` (02-local-ops.js:179),
  `healthLastSummary` (02-local-ops.js:8, `.library_score/.total/.no_cues/.no_phrase/
  .no_beatgrid/…`, null until first session scan), `_renderHealthSummary` (02-local-ops.js:926,
  ring + issue rows + split fix-tier buttons), `_applyHealthFix` (02-local-ops.js:1017).
  **G lede = net-new deterministic template** over the summary counts (NO LLM —
  PRD decision 6). **New-import banner = net-new** (no import detection exists; needs
  a track-count/id diff across loads).
- **F stamps/ticks**: `pendingCues` (01-core.js:456) IS the proposal state; the F5
  preview bar renders it per card. `applyToRekordbox` (04-app-chrome.js:167) sends
  `activeTracks()` unconditionally — F's per-track approve = a NEW `Set` gating the
  apply payload to **approved ∩ pending**. Net-new state, slots onto the existing
  pipeline.
- **e2e drift guards**: `tests/e2e/selectors-exist.spec.ts` (REQUIRED_SELECTORS —
  add BEFORE referencing); `tests/e2e/control-inventory.spec.ts` +
  `control-inventory.json` enumerate every id'd button/input/select/textarea from
  the live DOM (forcing hidden sections visible) and fail on any id missing from the
  json. **Every new interactive id (rail crate buttons, grid-toolbar verbs,
  inspector controls, approve ticks) MUST gain a json entry** or the guard fails.
  e2e baseline = the 8 known pre-existing failures; zero new.
- **Design rules** (docs/design/README.md + CLAUDE.md): two themes (test both);
  green = signal only (active crate, focus rings, success — never the CTA);
  primary CTA = ink pill; every measured value (count, score, BPM, key, time,
  path, cue name) = `--font-mono`; pills 999px, data chips 4px, inputs 8px,
  panels 12px, elevated cards 16px (`--radius-xl`); glass blur only on sticky
  chrome in motion; `prefers-reduced-motion` honored; `var(--token)` only, no
  hardcoded hex. **Virtualizer fixed-160px-height (TASK-033) + document-scroll
  sticky (TASK-037) invariants are load-bearing — every task preserves them.**

## Tasks (execute in order; VALIDATE after each; each merges green with the old UI intact)

### T1 — Scroll-architecture spike + Playwright proof (de-risk EVERYTHING first)

Prove research §7.1 path (a) in isolation before any feature work. Build a minimal
3-pane scaffold in `docs/index.html` (behind the `workbench` flag, local-mode only,
**hidden by default** — old tabbed UI untouched and primary): a CSS grid wrapper
`#wb-shell { display:grid; grid-template-columns: 224px 1fr 364px }` whose **center
column hosts the EXISTING `#tracks-sticky` + `#track-list`** (do NOT move the
Virtualizer or its `scrollSource:'window'` call). The rail (`#wb-rail`) is
`position:sticky; top:<header-height>` (or `fixed` — pick whichever keeps the
document the scroll source); the inspector (`#wb-inspector`) is
`position:sticky; top:<header-height>`. Center stays document-scrolled. Adapt B's
visual (design-B.html:88 grid) to path (a): B's inner `#grid-scroll{overflow-y:auto}`
is **replaced** by document scroll; only the rail/inspector are independently
scrollable (`overflow-y:auto` with their own max-height), the center is not.

Constraints to preserve and assert:
- `#tracks-sticky` still sticks to the document scroll (TASK-037) inside the grid.
- `#action-bar` stays `position:fixed` against the viewport.
- The Virtualizer `topOcclusionFn` snap still aligns the next card boundary to the
  sticky bar's bottom edge when the grid is the center column.
- Card height stays the fixed 160px (TASK-033) — no per-card measurement.

NEW e2e `tests/e2e/v2-workbench-scroll.spec.ts` (the JSDOM blind spot is exactly
why this is Playwright): force the `workbench` flag on, then assert at a realistic
viewport (and a 3.7k-track-scale stub if feasible): (a) `#tracks-sticky`'s
`getBoundingClientRect().top` pins to the sticky offset after scrolling the center
(not scrolled off); (b) the rail + inspector remain in view while the center
scrolls (their `top` ≈ constant); (c) no orphan ⚠ row at the sticky boundary
(occlusion snap holds); (d) `#action-bar` `position` computes to `fixed`. Mirror the
virtualizer-snap memory pattern for the boundary assertion. If path (a) cannot hold
all four, STOP and escalate before T2 (do not silently fall to path (b) — TASK-037
warns against it).

Add the new ids to `selectors-exist.spec.ts` (`#wb-shell`, `#wb-rail`,
`#wb-inspector`) and `control-inventory.json` if any are interactive.

VALIDATE: `cd tests/e2e && npx playwright test v2-workbench-scroll` green + the four
assertions pass in BOTH themes; `npm test` + `pytest` unaffected (additive,
flag-gated). Chrome at `http://127.0.0.1:7432` with the flag on — screenshots of the
scaffold scrolling, both themes, to the user.

### T2 — Expose the legacy globals the shell needs (extend/stand up `window.ACBridge`)

The shell reads legacy fns that are plain top-level functions, NOT on window. If P1
has merged, **extend its read-only `window.ACBridge`**; if P2 lands first, stand the
bridge up (same contract as P1 plan T2). Append to the END of the appropriate legacy
file (or P1's bridge block) read-only accessors + function pass-throughs:
- state readers: `tracks()`→`parsedTracks`, `healthSummary()`→`healthLastSummary`,
  `isLocalMode()`→`localMode`, `selectedIds()`→`selectedTrackIds`, `pending()`→`pendingCues`.
- fn pass-throughs the shell needs: `filteredTracks`, `sortedTracks`, `activeTracks`,
  `buildTrackCard`, `renderTracks`, and the **six inspector builders** + `showTransitionScore`
  + `_explainCue` (so renderInspector can call them without poking module internals).
- event hooks (so v2 never polls): keep/add `autocue:local-mode` and
  `autocue:health-summary` dispatches; add `autocue:tracks-loaded` after
  `parsedTracks` is populated (T7's new-import banner consumes this).

Per the interop contract, v2 reads legacy ONLY via `window.ACBridge`/`window.*`;
never bare `parsedTracks`. Vitest `tests/web/v2-bridge-p2.test.js` (source-contract
via `loadAppHtml()`): assert every new accessor + the three event names exist in
source, and a regex sweep of `docs/js/v2/*.js` finds no bare `parsedTracks`/
`healthLastSummary`/`pendingCues`/`selectedTrackIds` outside `ACBridge`.

VALIDATE: `npm test`.

### T3 — Three-pane shell skeleton (v2 module, flag-gated, additive)

`docs/js/v2/workbench/shell.js` (imported by `main.js`): `initWorkbench()` no-ops
until `autocue:local-mode` AND the `workbench` flag (a `localStorage.ac_workbench`
toggle, default off; expose a dev toggle in ⌘K / a header switch). When active,
reveal `#wb-shell` (from T1's scaffold) and hide the legacy `#tab-nav` tabs'
content area; the old tabs stay in the DOM (parity safety) but the workbench owns
the screen. Wire the three regions: rail (T5), grid (T4), inspector (T4). CSS under
a new `/* ── v2: workbench ── */` banner in `docs/css/app.css`, tokens only,
mirroring design-B's spacing/radii (rail crate rows, grid header `.grid-cols`,
inspector card stack at `--radius-xl`). Keep it inert/empty-but-laid-out at this
step (real content arrives T4–T7) so the skeleton merges trivially.

VALIDATE: `npm test` + Chrome (flag on): three panes render at design-B proportions,
both themes, zero console errors; flag off → old UI byte-identical. Screenshots to user.

### T4 — Split buildTrackCard → buildGridRow + renderInspector; focusedId + kbd nav (BIGGEST PR)

`docs/js/v2/workbench/grid-row.js`: `buildGridRow(track, cues, willSkip)` — a thin
**fixed-height** row (the design-B `.grid-cols` 10-column layout: checkbox, slot,
title/artist, BPM, key, energy mini, mix, class, cues, menu), reusing the EXISTING
`buildTrackCard` data plumbing but rendering only row identity (NOT the inspector
widgets). **Preserve the TASK-033 fixed-160px-equivalent row height invariant** —
the Virtualizer computes the window in O(1) from `itemHeight`; rows must be uniform.
Keep the FLIP/observer/`_updateTrackCardCues` machinery working (it assumes the full
builder — adapt it to the row, or keep buildTrackCard as the legacy path and route
the grid through buildGridRow when the flag is on).

`docs/js/v2/workbench/inspector.js`: `renderInspector(focusedId, prevFocusedId)` —
re-hosts the six builders wholesale via the bridge (research §3 table): energy curve
(`_renderEnergySparkline`, bigger viewBox), mixability (`_renderMixabilityChip`),
classification (`_renderCategoryChip`), similar (`_toggleSimilarPanel`), phrase strip
+ A–H ticks (`buildPhraseStrip`), cue reasoning (`_explainCue`); plus the
anchor-transition card (`showTransitionScore` fetch+scoring reused, presentation
rebuilt modal→inline, triggered off `prevFocusedId` as the "from" anchor). Empty
state when `focusedId` is null.

State: net-new `focusedId`/`prevFocusedId` (single-focus; distinct from
`selectedTrackIds` and `nowPlayingId`). Grid keyboard nav: ↑/↓ move focus (clamp),
Enter/click focuses + renders inspector, Space toggles selection, Esc clears focus —
registered scoped to the grid (don't fight the ⌘K capture-phase handler). Mouse:
row click focuses; checkbox toggles selection.

**Gate hard with virtualization e2e.** New e2e `tests/e2e/v2-workbench-grid.spec.ts`:
(a) all visible rows report identical height (fixed-height invariant); (b) scrolling
keeps row count bounded (virtualization still recycles); (c) clicking a row populates
`#wb-inspector` with the energy curve + phrase strip; (d) ↑/↓ moves focus and updates
the inspector. Add row/inspector control ids to `selectors-exist` +
`control-inventory.json`.

VALIDATE: `npm test` (unit-test the pure parts of buildGridRow + the focus-state
reducer as ES modules) + `cd tests/e2e && npx playwright test v2-workbench-grid` +
Chrome: scroll a large library, focus rows, inspector follows; both themes;
screenshots to user. Confirm the legacy tabbed list (flag off) is unchanged.

### T5 — Left rail: structural crates + playlists + saved filters

`docs/js/v2/workbench/rail.js`: render three crate sections (design-B rail):
- **Structural crates** via client predicates over `ACBridge.tracks()`: "No cues"
  (`Number(t.existingHotCues) === 0`), "Phrase-ready" (reuse `phraseOnlyFilter`
  semantics), "Already cued" (`Number(t.existingHotCues) > 0`). Live counts (mono)
  recompute on `AppState.subscribe('tracks')`. Clicking a crate sets the grid's
  active predicate (feeds `filteredTracks()`/`activeTracks()` so verbs target it).
- **Playlists**: fetch `GET /api/playlists` on first rail open; each playlist is a
  crate that filters the grid to its track ids. Cache; refetch on tracks-loaded.
- **Saved filters**: new `localStorage.ac_workbench_crates` persistence (mirror
  `ac_discover_filters`); "Save current filter" captures the active predicate +
  search; saved crates render with a delete affordance.

**Defer intelligence-keyed crate counts** (Mix-80, Peak-time): mixability/
classification are per-track lazy fetches, not bulk-available. Document the
**open question** in the rail module header + the plan's Out-of-scope: either a new
bulk `/api/mixability?ids=` / `/api/classify?ids=` endpoint or a "counts fill on
scroll" lazy strategy — a follow-up phase decides. Render these crates **disabled
with a "—" count** placeholder so the layout matches design-B without lying.

Add crate-button ids to `selectors-exist` + `control-inventory.json` (reads only —
no `safeOnRealDb:false`). Vitest `tests/web/v2-workbench-rail.test.js`: predicate
counts (mixed cued/uncued), saved-filter round-trip through a mocked localStorage,
playlists render from a stubbed fetch.

VALIDATE: `npm test` + Chrome: crates show live mono counts; clicking filters the
grid; save/restore a filter; both themes; screenshots.

### T6 — Grid-toolbar verbs (relocate + normalize + de-couple from hidden DOM)

`docs/js/v2/workbench/toolbar.js`: a selection-scoped verb bar above the grid
(design-B toolbar). Relocate the operation verbs so they target `activeTracks()`:
- **Auto-tag** → `autoTagTracks` (02-local-ops.js:1334);
- **Comment enrich** → `enrichComments` (04-app-chrome.js:7) — **normalize it to
  `activeTracks()`** (it currently reads `filteredTracks()`; change the source so
  the verb targets the selection like every other write op, mirroring the #173 fix);
- **Preview cues** → `#preview-cues-btn.click()` delegation (keeps #173 scoping +
  all guards);
- **Apply** → the existing apply path (T8 gates the payload).

De-couple option controls from hidden DOM: the verbs currently READ params from
`#cue-tools-section` / `#comment-enrich-section`. Move those controls into **toolbar
popovers** OR parameterize the functions to accept an options object (preferred:
parameterize, fall back to popover-hosted controls that the functions read). Keep
delegating to the existing functions so every backup / `_rb_running` 409 / guard
still fires — never re-implement the write path.

Add toolbar verb + popover control ids to `selectors-exist` + `control-inventory.json`
(write verbs need `safeOnRealDb:false`; popover option inputs are safe reads). Vitest
covers the enrichComments source-normalization (assert it now calls `activeTracks()`)
and the options-parameterization. **The legacy `#cue-tools-section` controls stay
working for the flag-off path** until T9.

VALIDATE: `npm test` + Chrome: select a crate, run auto-tag / enrich on the selection
(guards intact, Rekordbox-open still 409s apply); both themes; screenshots.

### T7 — Health ring rail card + fix stack + G lede + new-import event banner

`docs/js/v2/workbench/health-card.js`: relocate `_renderHealthSummary`
(02-local-ops.js:926) ring + `_applyHealthFix` (02-local-ops.js:1017) fix-tier
buttons into a **live rail card** that expands into the fix stack (C-as-event, PRD
decision 1). Read counts via `ACBridge.healthSummary()`; repaint on
`autocue:health-summary`; if null, the card offers "Scan health" → existing
`#health-scan-btn.click()` path.
- **G lede (deterministic, NO LLM)**: a net-new template over the summary counts —
  e.g. `"${no_cues} tracks need cues, ${no_beatgrid} need beatgrids — health
  ${Math.round(library_score)}/100"` — rendered atop the expanded fix stack. Pure
  string template; assert no network/LLM call.
- **New-import banner (net-new)**: detect net-new imports by diffing the track-id
  set across loads (persist the prior id set / count in `localStorage`; on
  `autocue:tracks-loaded` compute the delta). When N net-new tracks appear, surface
  an **event banner** ("N new tracks imported — N need cues") that deep-links the
  "No cues" crate. C is an event, not a place.

Add health-card + banner control ids to `selectors-exist` + `control-inventory.json`.
Vitest `tests/web/v2-workbench-health.test.js`: G-lede template output for several
count combos (pure fn, no LLM), new-import delta logic (prior-set vs current-set),
banner visibility gating.

VALIDATE: `npm test` + Chrome: scan health → ring + lede + fix stack in the rail
card; simulate a new-import delta → banner appears and deep-links the crate; both
themes; screenshots.

### T8 — F proposal/applied stamps + per-track approve ticks; H review-unlocks-apply

`docs/js/v2/workbench/approval.js`: graft F's organ transplant onto the
`pendingCues` (01-core.js:456) → `applyToRekordbox` (04-app-chrome.js:167) pipeline.
- **Proposal/applied stamps**: each grid row / inspector shows the cue state —
  *proposed* (in `pendingCues`) vs *applied* — as a stamp (mono label + state color,
  green = applied/success only).
- **Per-track approve ticks**: a net-new `approvedTrackIds` Set; each proposed track
  gets an approve tick. **Gate the apply payload to `approved ∩ pending`** — modify
  the apply call site so it sends only approved+pending tracks instead of
  `activeTracks()` unconditionally (slot onto the existing pipeline; keep all guards).
  "Approve all" / "Approve selection" bulk affordances.
- **H consent gradient (review-unlocks-apply)**: on destructive ops (apply that
  overwrites existing cues, and any delete-bearing path), require an explicit review
  step before the ink-pill Apply unlocks — disabled until the user has acknowledged
  the proposed changes (the consent gradient from H). Mirror the existing
  confirm-modal / 250ms-primary-disable patterns already used for duplicates delete.

Add approve-tick + stamp + review-gate ids to `selectors-exist` +
`control-inventory.json` (apply/destructive → `safeOnRealDb:false`). Vitest
`tests/web/v2-workbench-approval.test.js`: payload-gating reducer (only approved∩pending
ships), stamp-state derivation, the review-unlock state machine (apply disabled until
acknowledged).

VALIDATE: `npm test` + Chrome: preview cues, approve a subset, apply → only approved
tracks written (Rekordbox-open still 409s); destructive op stays locked until
reviewed; both themes; screenshots.

### T9 — Retire the three tabs at parity; both-themes audit; final gate

Once T3–T8 reach parity, make the workbench the default home in local mode and
**remove the three tabs** (`#tab-nav` cues/library/discover tab content): the
workbench owns the screen; Discover restyle is a later phase (P5) so keep Discover
reachable via ⌘K "Go to" until then, but the **Cues + Library tabs retire** (their
surfaces now live in the workbench rail/grid/inspector + verbs + health card). Flip
the `workbench` flag default to on in local mode (or remove the flag entirely for
local mode); XML/Pages mode is unaffected (PRD decision 5 — shell never renders
there). Remove the now-dead legacy `#cue-tools-section`/`#comment-enrich-section`
hidden-DOM controls superseded by T6 popovers (update `control-inventory.json`
accordingly — drift guard must reconcile in both directions).

Full both-themes audit on every new surface (green = signal-only, ink-pill CTA,
mono-for-data, radii scale, reduced-motion). Final e2e + **Lighthouse-not-worse on a
large (≈3.7k-track) library** vs the pre-P2 baseline (PRD success metric). Update
`.claude/project/web-ui.md` (workbench shell: path-(a) scroll architecture, the
buildGridRow/renderInspector split, focusedId state, rail crates + the deferred
intelligence-crate open question, approval/consent pipeline) and the CLAUDE.md
track-record. Commit AI-asset changes with a `Context:` section. Three-leg gate from
repo root: `pytest` → `npm test` → `cd tests/e2e && npx playwright test`. Open PR
(base: main) titled `feat(web): P2 workbench-as-home — the v1 daily driver (AutoCue 2.0)`.

VALIDATE: three legs green (zero new e2e failures vs the 8-known baseline); Lighthouse
perf ≥ baseline on the large library; both themes verified with screenshots; old tabs
gone; PR open.

## Out of scope

- **Path (b)** (Virtualizer `scrollSource:'container'` + inner scroller) — rejected
  by T1; TASK-037 warns against it. Only fall here if T1 escalation explicitly
  re-decides.
- **Intelligence-keyed crate counts** (Mix-80, Peak-time live counts) — deferred:
  mixability/classification are per-track lazy, not bulk-available. Open question
  (bulk endpoint vs counts-fill-on-scroll) flagged in T5; a later phase resolves.
  Rendered as disabled "—" placeholders for now.
- **Nightboard** (D) — P4. **Duplicates as a place** — P3 (stays the existing
  Library-section logic until then). **Discover restyle into the shell** — P5
  (reachable via ⌘K until then).
- **`AUTOCUE_LLM` / conversational composer** — P6. The G lede is deterministic
  template only; no LLM anywhere in P2.
- **XML/Pages-mode rendering of the shell** — PRD decision 5; the 2.0 shell is
  local-mode only.
- **Wholesale ES-module conversion of legacy code** — opportunistic only; legacy
  classic files stay classic.

## Acceptance criteria

- **Scroll architecture (T1)**: path (a) proven — rail (sticky/fixed) + inspector
  (sticky) flank a document-scrolled center, Virtualizer + its `scrollSource:'window'`
  call + `topOcclusionFn` snap untouched, `#tracks-sticky` sticks + `#action-bar`
  fixed + fixed-160px rows + no orphan ⚠ row, all asserted by a NEW Playwright e2e in
  both themes (JSDOM can't catch this).
- **Shell**: three-pane workbench is the local-mode home; left rail (structural
  crates with live mono counts + playlists + saved filters), dense center grid of
  uniform-height virtualized rows, right inspector keyed off a single `focusedId`
  re-hosting the six existing builders + the anchor-transition card off `prevFocusedId`.
- **buildTrackCard split**: thin fixed-height `buildGridRow` + `renderInspector`;
  TASK-033 + TASK-037 invariants hold; virtualization e2e green; legacy list
  unchanged when the flag is off.
- **Verbs**: auto-tag / comment-enrich / preview / apply relocated to the grid
  toolbar + ⌘K, all targeting `activeTracks()` (enrichComments normalized off
  `filteredTracks()`), option controls de-coupled from hidden DOM, every guard /
  backup / `_rb_running` 409 intact (delegated, never re-implemented).
- **Health as event**: ring + fix stack in a rail card; deterministic G lede (no LLM);
  net-new-import event banner deep-linking the No-cues crate.
- **F + H transplants**: proposal/applied stamps + per-track approve ticks gate the
  apply payload to approved∩pending; review-unlocks-apply consent gate on destructive ops.
- **Retirement**: the three tabs are gone in local mode; workbench is the default;
  every daily session starts and ends in the workbench (the v1 milestone).
- **Discipline**: all new JS = ES modules under `docs/js/v2/` imported by `main.js`;
  legacy edits limited to the T2 bridge/events + the apply-payload gate + the
  enrichComments normalization; CSS appended under one `/* ── v2: workbench ── */`
  banner; no build step, no new runtime deps; green=signal-only + mono-for-data +
  the virtualizer/sticky invariants hold on every new surface; both themes verified
  per task with screenshots.
- **Tests**: every new interactive id is in `control-inventory.json` (drift guard
  reconciles both directions) + `selectors-exist`; new Vitest specs (grid-row,
  focus reducer, rail predicates + saved filters, enrich normalization, G-lede
  template, new-import delta, approval payload gate) green; new Playwright specs
  (scroll survival, grid virtualization/inspector) green; three-leg stack green with
  zero new e2e failures vs the 8-known baseline; Lighthouse perf ≥ baseline on a
  ≈3.7k-track library.

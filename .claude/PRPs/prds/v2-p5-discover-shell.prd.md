# AutoCue 2.0 — P5: Discover in the shell + theme audit + aliveness round 2 (phase PRD)

## Problem statement

The Discover v2 feature is fully built and locked — a three-feeder pipeline
(`autocue/analysis/discover/`), a 28-endpoint `/api/discover/*` REST + SSE
surface, and a complete frontend module (`DiscoverV2` IIFE,
`docs/js/03-download-discover.js:662`). But it lives **entirely inside the
legacy Discover tab** (`#discover-tab-content`, `docs/index.html:1061`), reached
only via `#tab-discover` (`docs/index.html:59`) and `switchTab('discover')`
(`docs/js/04-app-chrome.js:328`). The P2 workbench (the locked default home,
program PRD decision 1) made the Cues track-list the document-scrolled center
and retired the Cues + Library tabs — but **deliberately left Discover behind**,
reachable via ⌘K "Go to" until this phase (P2 plan T9, `v2-p2-workbench.plan.md`:
"Discover restyle is a later phase (P5) so keep Discover reachable via ⌘K…the
Cues + Library tabs retire").

So today the local-mode user lives in the workbench, then context-switches into a
visually-foreign tabbed page to discover music: a different layout grammar
(top-bar tabs vs. rail places), inline-styled cards that predate the design
system (`.disc-v2-card`, `docs/css/app.css:2067` — `box-shadow: 0 4px 12px
rgba(0,0,0,0.08)`, hardcoded rgba, emoji action buttons `💚`/`💤`/`✕` at
`docs/js/03-download-discover.js:1177`), and no shared inspector. **Discover is
the last surface blocking full tab retirement** and the last surface that hasn't
had the design system + aliveness treatment.

This phase restyles the EXISTING Discover behavior into the workbench shell as a
**rail place → center-pane view** (maintenance grammar, decision #2), runs a
both-themes audit across every workbench surface, and does an aliveness round 2
(micro-feedback + transitions). **Behavior is frozen** — feeders, ranker, scan
orchestrator, the 60-request budget cap, the {1w/1m/3m} snooze set, and both
filter rows are restyle-only.

## Goals / Non-goals

**Goals**
- Discover reachable as a **rail place** in the workbench that swaps the center
  pane to the restyled release feed; the right inspector follows the focused
  release (detail re-hosted, not a separate modal page).
- Every Discover surface honors the five design rules in **both themes**
  (currently: inline rgba shadows, emoji buttons, non-token colors).
- A both-themes audit pass over **all** workbench surfaces shipped P2 + this
  phase (rail, grid, inspector, toolbar, health card, Discover).
- Aliveness round 2: micro-feedback on save/dismiss/snooze, scan-progress
  motion, card-enter transitions — all `prefers-reduced-motion`-gated.
- **Tab-retirement parity**: after this phase the `#tab-discover` tab and
  `#discover-tab-content` block are removable; the workbench owns 100% of
  local-mode surfaces.

**Non-goals**
- Any change to Discover *behavior*: feeders, ranker, scan orchestrator, budget
  caps, snooze durations, filter semantics, YouTube preview heuristic, backup
  sidecar, export/import. Restyle only — do NOT contradict `discover-v2.md`.
- New `/api/discover/*` endpoints or schema changes.
- The deferred intelligence-keyed crate counts (P2 T5 open question) — unrelated.
- XML/Pages-mode rendering of Discover (program decision #5 — shell is
  local-mode only; Discover is already local-mode-only via the server).
- `AUTOCUE_LLM` composer (program decision #6, P6).

## Alignment with locked program decisions

(Program PRD: `.claude/PRPs/prds/autocue-2-program.prd.md`)

- **Decision #1 (Home = B workbench)**: Discover becomes a center-pane *view*
  inside the same three-pane shell, not a parallel page. The inspector re-hosts
  the release detail, mirroring how the grid inspector keys off `focusedId`.
- **Decision #2 (maintenance grammar — *places* for decisions)**: Discover is a
  *place* (rail entry → center pane), exactly as Duplicates is in P3. Its
  per-card operations (save/dismiss/snooze) are card-local verbs; scan is a
  toolbar verb; settings is a *sheet* off the place.
- **Decision #4 (multi-file, no build step)**: all new code is native ES modules
  under `docs/js/v2/workbench/`, imported by `docs/js/v2/main.js`. The legacy
  `DiscoverV2` IIFE (`docs/js/03-download-discover.js:662`) stays a classic
  script; v2 reads it via `window.*` / a bridge extension, never imports it.
- **Decision #5 (XML/Pages frozen)**: Discover only ever rendered in local mode
  (the server holds the Discogs token + sidecar); no change here.
- **Decision #7 (global A-layer)**: the ⌘K palette already has a "Go to Discover"
  seam (P2 T9); this phase wires it to open the Discover place instead of the
  legacy tab.

## Current-state inventory (file:line — logic being restyled/ported, NOT rewritten)

**Backend (frozen — read-only dependency)**
- Discover module map: `autocue/analysis/discover/` — `taste.py`,
  `style_graph.py`, `ranker.py`, `scan_orchestrator.py`, `store.py`, `feeders/`
  (`discover-v2.md` §2). `HARD_SCAN_REQUEST_CAP=60`; budgets 20/15/10
  (`discover-v2.md` §8).
- REST + SSE surface, `autocue/serve/routes.py`: feed SSE
  `@router.get("/discover/feed")` (routes.py:4112); `feed/status` (4033);
  `feed/cancel` (4056); `releases/{id}` (4076); `save`/`dismiss`/`snooze`
  (4241/4250/4259) + un- variants (4269/4275/4281); `block-artist`/`-label`
  (4287–4305); `saved`/`dismissed`/`snoozed`/`downloaded` (4313–4333);
  `labels`/`labels/search`/`labels/suggested` + follow/unfollow (4350–4389);
  `state/export`/`import` (4419/4462); `stats` (4525); `token-status` (4001).
  **None change.**
- Snooze set `{1w,1m,3m}` enforced server-side (`discover-v2.md` §10; `30d`→400).

**Frontend Discover (the thing being restyled)**
- `DiscoverV2` IIFE pub/sub container + SSE consumer:
  `docs/js/03-download-discover.js:662` (`runScan` at 753, `_handleSSEChunk` at
  908). Renderers: `_renderDiscoverV2Feed` (1389), the card builder around
  `docs/js/03-download-discover.js:1153` (`card.className = 'disc-v2-card'`),
  `_applyDiscoverV2Filters` pure helper (1242).
- Card markup uses **emoji action buttons** + non-token styling:
  `disc-v2-card-action` save `💚`/snooze `💤`/dismiss `✕`
  (03-download-discover.js:1177–1179).
- Two filter rows (`discover-v2.md` §11): server-side `#disc-v2-filter-bar`
  (index.html:1091 — source chips, sort, year + `#disc-v2-year-custom` at 1112),
  client-side `#disc-v2-filter-bar-2` (index.html:1119 — search, hide-saved,
  hide-dismissed) + `#disc-v2-style-chips` (1130). Persist to
  `localStorage.ac_discover_filters`.
- Scan chrome: `#disc-v2-scan-progress` (index.html:1144) with
  `.disc-v2-spinner`, breakdown, `#disc-v2-scan-progress-fill` (1153);
  `#disc-v2-scan-error-inline` (1138, issue #169 supersede banner);
  `#disc-v2-scan-warnings` (1161); `#disc-v2-empty-state` (1164).
- Detail panel + popovers (per-open scoped keydown, `discover-v2.md` §11):
  `#disc-v2-detail-panel` (index.html:1219) + `#disc-v2-detail-backdrop` (1218),
  `#disc-v2-snooze-pop` (1229), download confirm + keyboard help overlay.
- Settings block `#disc-v2-settings` (index.html:1173): saved/followed/blocked
  lists, label search/suggest, export/import, stats.
- Grid host `#disc-v2-grid.disc-v2-grid` (index.html:1213).
- Card CSS: `.disc-v2-grid`/`.disc-v2-card`/`.disc-v2-card-*`
  (`docs/css/app.css:2062`–2111) — **hardcoded** `rgba(0,0,0,0.08)` shadow,
  `rgba(0,0,0,0.78)` action bg, `transform: translateY(-2px)` hover.
- Keyboard map (`discover-v2.md` §11): `j/k/Enter/s/x/z/Shift+D/?/Esc`.

**Workbench shell (the host)**
- Shell module: `docs/js/v2/workbench/shell.js` — `isWorkbenchOn()`
  (shell.js:18, flag `ac_workbench`), `activate()` (90) reveals `#wb-rail` +
  `#wb-inspector`, calls `switchTab('cues')` (98) to make the track list the
  center, relocates tools, renders crates + rail + inspector.
- Crate model: `CRATES` array (shell.js:28) + `_renderCrates` (37);
  active state via `ACBridge.crate()`/`setCrate()` (shell.js:46/52).
- Rail: `docs/js/v2/workbench/rail.js` — playlists/saved-filters/health groups;
  `_makeRow` (rail.js:89) is the reusable rail-button factory.
- Inspector: `docs/js/v2/workbench/inspector.js` — `renderInspector(trackId)`
  (inspector.js:29) re-hosts legacy builders by building containers + calling
  legacy fns via `window.*`; `clearInspector` empty state.
- Bridge: `window.ACBridge` (`docs/js/08-set-builder-boot.js:919`) — read-only
  state accessors + fn pass-throughs + `setCrate`/`crate` (the one sanctioned
  write path via `AppState.signal('filters')`).
- Module entry: `docs/js/v2/main.js` (imports shell + proposals).
- Center/sticky invariants (CLAUDE.md TASK-033/037): `<main>` (index.html:85),
  `#tracks-sticky` (278), `#track-list` (423), document-level scroll.
- Tabs still present in DOM (parity): `#tab-discover` (index.html:59),
  `#discover-tab-content` (1061), `switchTab` (04-app-chrome.js:297).
- v2 CSS banner: `/* ── v2: workbench (P2) ── */` (`docs/css/app.css:2341`);
  `--wb-rail-w: 224px` / `--wb-insp-w: 360px` (2343).

**Aliveness baseline**
- `prefers-reduced-motion` already honored in 15 CSS blocks
  (`docs/css/app.css`); `tab-entering` enter animation (04-app-chrome.js:303).

## Proposed design

### Where it lives in the workbench (maintenance grammar)

**Discover = a rail PLACE that swaps the center pane** (proposal). The rail gains
a top-level "Discover" entry (a *place*, distinct from the cue-state *crates*).
Clicking it:

1. Hides the cue track-list center (`#tracks-sticky` + `#track-list`) and shows
   the restyled Discover feed in the **same center column** (document-scrolled,
   so TASK-037 holds for the feed grid too).
2. Repaints the rail's lower region with Discover-scoped navigation: source
   chips, sort, year, saved/followed/blocked counts — the existing two filter
   rows re-homed (server-side row → rail or a slim center toolbar; client-side
   search/chips → center toolbar). **Open question OQ1** picks the exact split.
3. The **right inspector** re-hosts the release *detail* (currently
   `#disc-v2-detail-panel`): focusing a release card renders tracklist + YouTube
   preview + actions in `#wb-inspector`, mirroring `renderInspector(trackId)`.

**Scan = a toolbar verb** (grammar: *verbs* for operations) — a "Scan / Refresh"
ink-pill in the center toolbar (delegates to legacy `runScan` via the existing
`#disc-v2-refresh-btn`). **Settings = a sheet** off the place (the existing
`#disc-v2-settings` block, restyled, opened as a sheet not an inline expander).

### Restyle rules applied (both themes, design-system tokens)

- **Cards**: `.disc-v2-card` → token-based — `var(--surface)` bg,
  `var(--border)`, `var(--radius-xl)` (16px elevated card), `var(--shadow-lg)`
  on hover only (rule 5: soft shadows lift on hover); replace hardcoded
  `rgba(0,0,0,*)`. Source/year/label line in `var(--font-mono)` (rule 3 —
  measured data). Title/artist in `var(--font-sans)`.
- **Action buttons**: emoji `💚`/`💤`/`✕` → pill buttons (`var(--radius-pill)`,
  rule 4) with text/icon glyphs; **save-applied state uses `var(--green)`**
  (rule 2: green = success signal only); the scan CTA is the **ink pill**
  (`var(--ink)`/`var(--on-ink)`, never green).
- **Active/focused card**: `var(--green-wash)` + `var(--green-ring)` focus ring
  (rule 2 wash discipline), matching the grid's focused-row treatment.
- **Chips/toggles**: source chips, style chips, hide-saved/dismissed → pills.
- **Scan progress**: token colors; the warning/amber inline uses a token, not
  the `var(--amber, #c98a00)` hardcoded fallback currently inline at
  index.html:1138/1151/1161.

### Aliveness round 2 (all `prefers-reduced-motion`-gated)

- **Micro-feedback**: save/dismiss/snooze produce an immediate optimistic state
  change on the card (the action already mutates `DiscoverV2.state`) with a
  short transition (scale/opacity) — no behavior change, purely visual.
- **Scan progress**: animate `#disc-v2-scan-progress-fill` width + the
  `.disc-v2-spinner` smoothly; card-enter stagger as releases stream in.
- **Place transition**: crossfade center pane when switching crate↔Discover
  (reuse the `tab-entering` pattern, 04-app-chrome.js:303).
- **Workbench-wide audit fixes**: any dead-zone / missing-feedback found by the
  `ui-aliveness-audit` pass on rail/grid/inspector/toolbar/health/Discover.

## Requirements (numbered, testable)

- **R1** — A "Discover" entry renders in the workbench rail as a *place*
  (visually distinct from cue-state crates), present only in local mode + when
  the workbench is active. *Test*: Vitest asserts the rail renders the Discover
  place row; e2e asserts the control id exists in the rail.
- **R2** — Clicking the Discover place hides `#tracks-sticky` + `#track-list`
  and reveals the Discover feed in the center column; clicking a cue crate
  restores the track list. The document remains the scroll source (TASK-037).
  *Test*: Playwright asserts the center swap + that `#action-bar` stays
  `position: fixed` and the page scrolls at document level on the Discover view.
- **R3** — All existing Discover behavior is unchanged: a scan still POSTs/streams
  via `/discover/feed`, the 60-request budget cap holds, snooze still accepts
  only `{1w,1m,3m}`, both filter rows still filter, YouTube preview heuristic
  unchanged. *Test*: existing `pytest tests/test_discover_*` + `tests/web/
  discover-v2-*.test.js` stay green **untouched**; a new Vitest asserts the
  restyled actions still call the same `DiscoverV2` methods.
- **R4** — The release detail renders in `#wb-inspector` (re-hosted), not as a
  standalone full-page modal, when a release is focused in the workbench; Escape
  / focus behavior preserved. *Test*: Playwright focuses a release card →
  inspector shows tracklist; Esc clears.
- **R5** — Every Discover surface (cards, chips, buttons, scan chrome, detail,
  settings sheet) uses design-system tokens — **zero hardcoded hex/rgba** in the
  restyled CSS block. The scan CTA is the ink pill; green appears only on
  save-applied/success/focus. *Test*: a Vitest/grep guard over the new CSS block
  finds no `#`-hex or `rgba(` literals; manual both-themes screenshots.
- **R6** — All Discover measured values (year, BPM where shown, source label,
  release id, label name) render in `var(--font-mono)`. *Test*: audit checklist
  + screenshot.
- **R7** — Aliveness round 2: save/dismiss/snooze micro-feedback, scan-progress
  motion, and place-transition crossfade are present and **fully suppressed
  under `prefers-reduced-motion: reduce`**. *Test*: Playwright with reduced-motion
  emulation asserts no transition/animation runs on those elements.
- **R8** — Both-themes audit: every workbench surface shipped in P2 + P5 passes
  the five-rule checklist in light and dark. *Test*: documented checklist + dual
  screenshots attached to the PR; e2e theme-toggle smoke.
- **R9** — Tab-retirement parity: with Discover in the shell, `#tab-discover` +
  `#discover-tab-content` are removable from the markup, and ⌘K "Go to Discover"
  opens the place. *Test*: e2e asserts the Discover place is reachable from the
  workbench with no legacy tab; `control-inventory.json` reconciles removed +
  added ids in both directions (the P2 drift-guard pattern).
- **R10** — The legacy `DiscoverV2` IIFE and `/api/discover/*` surface are
  delegated to, never re-implemented (every save/scan/snooze still flows through
  the existing functions and endpoints with their guards). *Test*: Vitest asserts
  no v2 module re-implements an SSE consumer or a `fetch('/api/discover/...')`
  that duplicates `DiscoverV2`.

## Architecture & interop

- **New module**: `docs/js/v2/workbench/discover.js`, imported by
  `docs/js/v2/main.js` (the only entry point). Native ES module; no build step,
  no framework, no new runtime deps (decision #4).
- **Interop contract**: v2 reads legacy ONLY via `window.*` / `window.ACBridge`.
  The `DiscoverV2` IIFE is currently a module-scoped `const` in a classic script
  (`03-download-discover.js:662`) — **it must be exposed**. Extend the bridge or
  add `window.DiscoverV2 = DiscoverV2` (proposal — OQ2): the v2 module then reads
  `DiscoverV2.state`, `subscribe`, `runScan`, `save`/`dismiss`/`snooze` through
  it, and triggers the existing renderers rather than re-rendering. Legacy never
  imports v2; v2 exposes any surface on `window.AC2.discover`.
- **Center-pane swap**: the workbench already calls `switchTab('cues')` on
  activate (shell.js:98). The Discover place calls `switchTab('discover')` to
  reuse the existing show/hide of `#discover-tab-content`, OR (cleaner, OQ1)
  moves `#disc-v2-grid` into the center column and toggles it against
  `#tracks-sticky`/`#track-list`. Either way the document stays the scroll source
  and the existing `_renderDiscoverV2Feed` renders into the same grid host —
  **the SSE consumer and grid host are reused, never duplicated**.
- **Inspector reuse**: the detail rendering currently targets
  `#disc-v2-detail-body` (index.html:1223). The v2 module renders the same detail
  content into `#wb-inspector` when in workbench mode (a render-target switch),
  reusing `/discover/releases/{id}` fetch + the YouTube preview path unchanged.
- **CSS**: appended under the existing `/* ── v2: workbench (P2) ── */` banner
  in `docs/css/app.css` (extend, don't fork); restyle the `.disc-v2-*` rules
  to tokens. No new `<style>` block in markup.
- **Bridge additions** (read-only + delegation): `window.ACBridge.discover` or
  `window.DiscoverV2` exposure (OQ2); a `discover:place-open`/`-close` event so
  the rail and inspector coordinate without polling (mirroring P2's
  `autocue:health-summary` event pattern).

## Test plan

**pytest** — no new backend tests required (behavior frozen); the full
`tests/test_discover_*.py` suite (taste/feeders/ranker/orchestrator/store/
endpoints/budget audit per `discover-v2.md` §13) must stay green **untouched** as
the regression guard that restyle changed no behavior.

**Vitest** (`tests/web/`)
- `v2-workbench-discover.test.js` (new): the rail Discover place renders;
  clicking it dispatches the place-open path; restyled card actions still call
  `DiscoverV2.save/dismiss/snooze`; detail renders into the inspector target
  when workbench-active. Source-contract via `loadAppHtml()` for any legacy read.
- A token-purity guard over the restyled CSS block (no hardcoded hex/rgba) — R5.
- Existing `discover-v2-*.test.js` (filters, style-chip prune, integration,
  YouTube carousel, keyboard, settings, etc.) stay green **unmodified** — R3.

**Playwright e2e** (`tests/e2e/`)
- `v2-discover-shell.spec.ts` (new): force workbench flag on; assert (a) the
  Discover rail place exists and opens the center feed; (b) `#tracks-sticky` /
  `#track-list` hide and the feed grid shows; (c) `#action-bar` stays `fixed` +
  document-level scroll on the Discover view (TASK-037); (d) focusing a release
  populates `#wb-inspector`; (e) reduced-motion emulation suppresses the
  micro-feedback/scan transitions (R7); (f) both themes via `theme-toggle`.
  Route/mock `/api/discover/*` as `discover-v2.spec.ts` already does
  (`discover-v2.md` §13).
- `control-inventory.spec.ts` + `selectors-exist.spec.ts`: **new ids** for the
  Discover rail place, center-toolbar scan/refresh verb, re-homed filter
  controls, and inspector detail controls go into `control-inventory.json`
  (write verbs → `safeOnRealDb:false`; reads safe). **Removed ids** for
  `#tab-discover` + the retired `#discover-tab-content` controls are deleted from
  the inventory — the drift guard reconciles **both directions** (R9).

**New control-inventory ids (proposed)**: `wb-rail-discover` (place button),
`wb-disc-scan-btn` (toolbar verb), `wb-disc-search`, `wb-disc-source-*` chips,
`wb-disc-sort`, `wb-disc-year`, `wb-disc-detail-*` inspector controls. Exact set
finalized during implementation; every interactive id MUST land in the inventory
(P2 drift-guard rule).

## Rollout & parity

- **Flag-gated, additive** until parity, exactly like P2: the new Discover place
  only renders when the workbench is active (`isWorkbenchOn()`, shell.js:18) +
  local mode. The legacy `#tab-discover` + `#discover-tab-content` stay in the
  DOM and fully working for the flag-off path through the phase.
- **Parity gate → retirement**: once R1–R8 pass, the **final task removes**
  `#tab-discover` (index.html:59), `#discover-tab-content` (1061), and the
  `switchTab('discover')` wiring (04-app-chrome.js:328), and points ⌘K "Go to
  Discover" at the place. This is the moment **all three legacy tabs are gone**
  — P2 retired Cues + Library; P5 retires Discover. The program's v1 success
  metric ("the old tabs are gone") is fully met only after this phase.
- **Default-on**: RESOLVED (2026-06-12, main c3dcff0) — the workbench is
  default-on in local mode (`isWorkbenchOn()` reads `!== '0'`, opt-out `'0'`).
  P5 inherits that; removing the legacy tab cannot strand a default-off user
  unless they explicitly opted out (the opt-out path must keep Discover
  reachable — see OQ4).
- **XML/Pages unaffected** (decision #5): Discover never rendered there; no
  change to the frozen drop-zone flow.

## Open questions & risks

- **OQ1 — filter-row placement**: do the two existing filter rows
  (`#disc-v2-filter-bar` server-side, `#disc-v2-filter-bar-2` client-side) move
  into the rail, a slim center toolbar, or split (server-side → rail, client-side
  → toolbar)? The maintenance grammar suggests scan/refresh = toolbar verb and
  scope-selection (source/year) could be rail. Decide in the implementation
  plan; behavior (which fires `runScan` vs. client filter) is fixed by
  `discover-v2.md` §11.
- **OQ2 — DiscoverV2 exposure**: extend `window.ACBridge` with a `discover`
  accessor, or expose `window.DiscoverV2` directly? The IIFE is a classic-script
  `const` (03-download-discover.js:662) not on `window` today. Proposal:
  `window.DiscoverV2 = DiscoverV2` appended in the legacy file (one line,
  read-mostly) — least churn, consistent with the IIFE being self-contained.
- **OQ3 — center-pane swap mechanism**: reuse `switchTab('discover')` (shows the
  whole `#discover-tab-content` block, simplest, but that block carries its own
  layout) vs. relocate `#disc-v2-grid` into the workbench center column
  (cleaner shell integration, more DOM surgery). Tied to OQ1.
- **OQ4 — opted-out users at tab retirement**: default-on shipped (main c3dcff0,
  `!== '0'`), so the open question is narrower: a user who explicitly opted out
  (`ac_workbench === '0'`) loses the Discover tab when it retires. Decide: keep
  the legacy tab when opted out, or make Discover reachable from the legacy UI
  some other way, or treat opt-out as legacy-complete (no Discover).
- **Risk — inspector dual-purpose**: the inspector currently keys off a track
  `focusedId` (grid). Hosting release detail means the inspector serves two
  content types. *Mitigation*: a mode flag on the inspector ("track" vs.
  "release"); clear on place switch.
- **Risk — JSDOM layout blind spot** (memory: jsdom-layout-blind-spot): center
  swap + sticky/scroll behavior can ONLY be verified in Playwright, not Vitest —
  the e2e spec is mandatory, not optional.
- **Risk — restyle drift**: changing `.disc-v2-card` CSS could shift the feed
  grid layout. *Mitigation*: token-only edits, both-themes screenshots per
  checkpoint, keep the grid host id stable so existing filter/render tests pass.

## Success metrics

- Discover is reachable as a rail place; the workbench owns 100% of local-mode
  surfaces; **all three legacy tabs removed** (the program v1 metric fully met).
- Zero hardcoded hex/rgba in the restyled Discover CSS; five rules hold on every
  Discover surface in both themes (checklist 0 violations).
- All workbench surfaces (P2 + P5) pass the both-themes audit; dual screenshots
  on the PR.
- Aliveness round 2 present and fully suppressed under `prefers-reduced-motion`.
- Three-leg gate green: `pytest` (Discover suite untouched-green) · `npm test`
  (existing Discover specs untouched-green + new shell specs) · `playwright`
  (new `v2-discover-shell.spec.ts` + reconciled `control-inventory`), zero new
  e2e failures vs. the known baseline; Lighthouse not worse on a ≈3.7k-track
  library.

# Feature: AutoCue 2.0 — P5 Discover restyled into the shell (rail place → centre-pane view) + both-themes audit + aliveness round 2

## Summary

Re-surface the finished **Discover v2** feature — the three-feeder pipeline (`autocue/analysis/discover/`), the `/api/discover/*` REST+SSE surface, and the `DiscoverV2` IIFE in `docs/js/03-download-discover.js` — as a **rail place that opens a centre-pane view** in the P2/P3 workbench shell, mirroring how Duplicates was shipped in P3. **Discover behaviour is frozen**: feeders, ranker, scan orchestrator, the 60-request budget cap, the `{1w/1m/3m}` snooze set, both filter rows, the YouTube-preview heuristic, and the SSE consumer are **re-driven, never re-implemented**. The v2 module owns only the door (rail entry), the centre-pane swap, the restyle, and the detail re-host into `#wb-inspector`; every scan/save/dismiss/snooze flows through the existing `DiscoverV2` functions via a `window.DiscoverV2` exposure. This is the **second of two tab-retirement blockers** — after P5, `#tab-discover` retires and **all three legacy tabs are gone** (the program v1 success metric). Phase closes with a both-themes audit across every workbench surface and an aliveness round 2.

**Source PRD:** `.claude/PRPs/prds/v2-p5-discover-shell.prd.md` (R1–R10).
**Pattern reference:** `.claude/PRPs/plans/v2-p3-duplicates-place.plan.md` (P3 plan) + shipped P3 code.
**Branch:** `feature/v2-p5-discover-shell`, base `main` (post-P3 merge `b75d912`).

## Adopted decisions (resolve the PRD's open questions — do not reopen)

- **OQ3 / OQ1 — centre-pane swap = reuse `switchTab('discover')`, NOT relocate `#disc-v2-grid`.** Verified rationale: `_handleDiscoverKeydown` gates the whole `j/k/Enter/s/x/z/D/?/Esc` map on `#disc-v2-section.offsetParent !== null` (i.e. `#discover-tab-content`'s display state), and `initDiscoverV2` re-parents seven overlay elements to `<body>` so their `position:fixed` resolves against the viewport. Relocating `#disc-v2-grid` into the workbench centre would break both invariants. Reusing `switchTab('discover')` shows `#discover-tab-content` in place; the rail/inspector are `position:fixed` flanks, so the centre column already accommodates an arbitrary tab body. Same `hidden` + body-class contract as P3. Lower-risk per the PRD's "restyle drift" + "JSDOM blind spot" risks.
- **OQ2 — DiscoverV2 exposure = `window.DiscoverV2 = DiscoverV2`** appended at the end of the IIFE, plus an `ACBridge.discover` accessor group for the handful of calls the place makes. The IIFE is a module-scoped `const` today; one-line exposure, read-mostly, consistent with P3's bridge pass-throughs.
- **OQ4 — opted-out users at tab retirement = palette force-activates the workbench** (same as P3). `go-discover`/`find-releases` run `setWorkbench(true)` then click `#wb-disc-place`; the `ac_workbench === '0'` opt-out is a temporary escape hatch, and explicit navigation overrides it.
- **Inspector dual-purpose = a mode flag.** `inspector.js` gains `_mode` ('track' | 'release'); `clearInspector()` resets to 'track'; the Discover place renders release detail via `renderReleaseInspector(releaseKey)` and clears it on place exit; the grid click path early-returns when `_mode === 'release'`.

## Key facts (verified against `main` / `b75d912`)

- **`DiscoverV2` IIFE** — `docs/js/03-download-discover.js:662`, module-scoped `const`, NOT on `window`. Public surface at `:1125`: `state, subscribe, loadInitialState, runScan, cancelScan, save, dismiss, snooze, loadDetail, searchYouTube, followLabel, …, exportState, importState`. `runScan` `:753`; `_handleSSEChunk` `:908`; `save`/`dismiss`/`snooze` `:979`/`:991`/`:1002`.
- **Card builder** — ~`:1153` (`card.className = 'disc-v2-card'`); emoji action buttons `:1176–1179` (`data-act="save"`→`💚`/`✓`, `snooze`→`💤`, `dismiss`→`✕`). Detail-panel emoji actions `:2214–2219`. Settings-saved copy refs `💚` at `:1880`.
- **Renderers** — `_renderDiscoverV2Feed` (`:1389`, grid `#disc-v2-grid` `:1390`), `_applyDiscoverV2Filters` (`:1242`), scan-progress/warnings/onboarding/token-banner/followed/blocked renderers subscribed in `initDiscoverV2` (`:2787–2793`).
- **Detail panel** — `_openDetailPanel(releaseKey)` (`:2007`) → `#disc-v2-detail-body` (`:2010`) inside `#disc-v2-detail-panel` (`:2008`) + `#disc-v2-detail-backdrop` (`:2009`); per-open focus trap `:2025`; `_renderDetailBody(release, detail, status)` `:2022`/`:2036`; `loadDetail(id)` → `/discover/releases/{id}`.
- **Keyboard map** — `_handleDiscoverKeydown` installed `:2964` (`addEventListener('keydown', …, true)`), guarded on `#disc-v2-section` `offsetParent !== null`.
- **Wiring entry** — `initDiscoverV2` (`:2761`); only if `#disc-v2-section` exists; re-parents overlays to `<body>` (`:2771–2784`); wires `#disc-v2-refresh-btn` (`:2796`) + `#disc-v2-grid` delegation (`:2910`).
- **Markup** — `#tab-discover` (`docs/index.html:59`); `#discover-tab-content` (`:1079`, `display:none`); `#disc-v2-section.panel-card` (`:1083`); `#disc-v2-refresh-btn` (`:1092`); filter rows `#disc-v2-filter-bar` (`:1109`) + `#disc-v2-filter-bar-2` (`:1137`) + `#disc-v2-style-chips`; scan progress `#disc-v2-scan-progress` (`:1162`)/`-fill` (`:1171`, hardcoded `var(--amber, #c98a00)` `:1169`); `#disc-v2-empty-state` (`:1182`); `#disc-v2-settings` (`:1191`); `#disc-v2-grid` (`:1231`); `#disc-v2-detail-panel` (`:1237`).
- **Tab wiring** — `switchTab` map `discover: 'discover-tab-content'` (`04-app-chrome.js:295`); tab-button listener `:328`.
- **Discover CSS** — `app.css:2044` `/* Discover v2 */`; `.disc-v2-card` (`:2067`); hardcoded hover `translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.08)` (`:2076`); `.disc-v2-card-action:hover { background: rgba(0,0,0,0.78) }` (`:2110`); `.disc-v2-card-action.saved { background: var(--green) }` (`:2111`); reduced-motion override `:2028`.
- **Workbench shell** — `shell.js activate()` (`:99`) calls `switchTab('cues')` (`:107`); `_renderCrates()` `.active` from `ACBridge.crate()` (`:48`); crate click deactivates active place first (`:60`); `autocue:wb-place-change` repaints (`:119`). Rail `_makeRow` (`:89`); playlist/saved-filter clicks deactivate place first (`:121`/`:153`).
- **Place precedent** — `duplicates.js`: `HIDE_IDS = ['tracks-sticky','track-list','wb-grid-head','wb-inspector']`; `activate()` (`:38`); `deactivate()` (`:62`) reverses + `ACBridge.renderTracks()`. Registered `window.AC2.duplicates` `main.js:40`.
- **Inspector** — `inspector.js renderInspector(trackId)` (`:29`), `clearInspector()` (`:143`), `initInspector()` capture-phase click (`:155`).
- **Bridge** — `window.ACBridge` `08-set-builder-boot.js:921`; P3 added duplicates pass-throughs.
- **Control inventory** — `tab-discover` under `globalControls` (`:30`); `panelControls.discover` array `:478`; `wb-dupes-*` under `globalControls` (`:133`+).
- **e2e mock** — `discover-v2.spec.ts mockDiscoverApi(page, overrides)` (`:47`) routes every `/api/discover/*` + `/api/youtube/search`; feed SSE as raw `event:…\ndata:…\n\n` lines (`:78–90`). New spec imports/copies this helper.
- **Commands** — `commands.js go-discover` runs `_goto('discover')` (`:67`); `find-duplicates`/`go-duplicates` show the force-activate pattern (`:36–47`).
- **Invariants** — centre swap toggles `hidden` + body class only; `#track-list` never detached (TASK-033/037); `#action-bar` `position:fixed`; CSS `display:none !important` backstop at `app.css:2717–2719`. No backend edits. e2e runs ALONE (#189).

## Tasks (execute in order; each merges green on the three-leg stack)

### T1 — Legacy seams: expose DiscoverV2 + ACBridge.discover (additive)
**Files:** `docs/js/03-download-discover.js`, `docs/js/08-set-builder-boot.js` (bridge `:921`), `tests/web/v2-workbench-discover.test.js` (new).
1. End of the `DiscoverV2` IIFE (after `:1133`): `window.DiscoverV2 = DiscoverV2;` — one line, exposes the existing public surface; do not alter the returned object.
2. Append to `window.ACBridge` a `discover` group, all delegating: `discoverRunScan`, `discoverLoadInitialState`, `discoverState: () => (window.DiscoverV2 ? window.DiscoverV2.state : null)`, `discoverLoadDetail: (id) => window.DiscoverV2?.loadDetail(id)`. Keep minimal — every write (save/dismiss/snooze) still goes through the legacy grid delegation + detail-panel buttons.
3. Vitest source-contract: `window.DiscoverV2 = DiscoverV2` + the accessors exist; the IIFE return object at `:1125` is unchanged.

**VALIDATE:** `npm test` (new + existing `discover-v2-*.test.js` untouched-green); `pytest` untouched. No UI change.

### T2 — Place skeleton: rail entry + centre-pane swap via `switchTab('discover')` (inert)
**Files:** `docs/index.html`, `docs/js/v2/workbench/discover.js` (new), `shell.js`, `rail.js`, `main.js`, `app.css`, `selectors-exist.spec.ts`, `control-inventory.json`, `tests/web/v2-workbench-discover.test.js`.
1. Markup: in `#wb-rail`'s "Maintenance" section (next to `#wb-dupes-place`), add `.wb-crate` button `#wb-disc-place` (label "Discover"). Keep `#discover-tab-content` where it is.
2. `docs/js/v2/workbench/discover.js` (export `initDiscoverPlace`/`activate`/`deactivate`/`isActive`; register `window.AC2.discover` in `main.js`), mirroring `duplicates.js`:
   - `HIDE_IDS = ['tracks-sticky','track-list','wb-grid-head']`; inspector hidden separately (un-hidden on release focus, T5/T3).
   - `activate()`: guard `ACBridge.isLocalMode()`; `clearInspector()`; `switchTab('discover')`; set `hidden` on `HIDE_IDS` + `#wb-inspector`; add `body.wb-place-disc`; mark `#wb-disc-place.active`; dispatch `autocue:wb-place-change`; lazy first load via `ACBridge.discoverLoadInitialState()` (idempotent), `_loadedOnce` flag. Never detach `#track-list`; never fetch `/api/discover/*`.
   - `deactivate()`: `switchTab('cues')`; un-hide `HIDE_IDS` + `#wb-inspector`; remove `body.wb-place-disc`; un-mark; `clearInspector()`; `ACBridge.renderTracks()`; dispatch event.
   - Re-clicking the active entry toggles back.
3. Exits + **mutual exclusion** (Duplicates/Discover only one owns the centre): in `shell.js` crate-click (`:58`), `rail.js` playlist (`:119`)/saved-filter (`:151`), add `window.AC2?.discover?.deactivate()` alongside `duplicates.deactivate()`. In `discover.js activate()` first call `duplicates.deactivate()`; in `duplicates.js activate()` add `discover.deactivate()`. In `shell.js deactivate()` also deactivate Discover. `_renderCrates`'s `placeActive` (`:47`) ORs `discover.isActive()`.
4. CSS (mirror `app.css:2714–2719`): `body.wb-place-disc #tracks-sticky, … #track-list, … #wb-grid-head { display:none !important; }`. `#wb-disc-place.active` green-signal (reuse `.wb-crate.active`). Tokens only.
5. Guards: `#wb-disc-place` → `selectors-exist`; `wb-disc-place` (button) → `control-inventory` `globalControls`.
6. Vitest (jsdom): activation toggles + `switchTab('discover')`; deactivation restores + `switchTab('cues')`; mutual exclusion; crate-click deactivates. Stub `window.switchTab`/`ACBridge`.

**VALIDATE:** `npm test`; `pytest`; `playwright test selectors-exist control-inventory` (alone). Chrome `127.0.0.1:7432`: rail Discover → tab body swaps in, cue grid+sticky gone, inspector hidden; crate/Duplicates → cue grid returns, virtualizes/sticks. Both themes, screenshots.

### T3 — Detail re-host into the inspector + mode flag (R4)
**Files:** `inspector.js`, `discover.js`, `03-download-discover.js`, `control-inventory.json`, `selectors-exist.spec.ts`, `tests/web/v2-workbench-discover.test.js`.
1. Inspector mode flag: `let _mode='track'`; `clearInspector()` resets it; `initInspector`'s grid-click (`:158`) early-returns when `_mode==='release'`; add `setInspectorMode(m)` export.
2. `renderReleaseInspector(releaseKey)` (new export): set `_mode='release'`; read release from `window.DiscoverV2.state.cardsByKey.get(releaseKey)`; build header + mono data chips (year/label/release-id/styles, R6) into `#wb-inspector-body`; delegate body/tracklist/YouTube to the legacy `_renderDetailBody` (expose as `window._renderDiscoverRenderDetail`, one-line) + `ACBridge.discoverLoadDetail(id)`. Place never re-fetches `/discover/releases/{id}` outside the bridge.
3. Focus → inspector: in `discover.js`, a delegated `#disc-v2-grid` click/focus listener (active-only) calls `renderReleaseInspector(card.dataset.releaseKey)` + un-hides `#wb-inspector`. Suppress the legacy slide-in: single guard at top of `_openDetailPanel` (`:2007`) — early-`return` + `window.AC2?.discover?.focusRelease(key)` when `window.AC2?.discover?.isActive?.()`.
4. Esc/clear: `deactivate()` calls `clearInspector()` (resets mode). Escape in the inspector clears the focused release; scoped keydown removed on clear.
5. Guards: any new inspector-detail write ids → `control-inventory` `globalControls` (`safeOnRealDb:false`) + `selectors-exist`; if `_renderDetailBody` reused wholesale, its `data-detail-act` buttons stay class-keyed (note in json).
6. Vitest: focus populates `#wb-inspector-body` mode 'release'; `clearInspector()` resets; grid click while 'release' doesn't `renderInspector`; `_openDetailPanel` early-returns when place active (source-contract).

**VALIDATE:** `npm test`; `pytest`. Chrome: place → focus release → inspector shows tracklist+YouTube+actions; Esc clears; save from inspector works (delegates `DiscoverV2.save`). Both themes, screenshots.

### T4 — Restyle Discover to the five rules (presentation-only; both themes)
**Files:** `03-download-discover.js` (emoji→glyph/text in builders only), `app.css` (Discover block `:2044–2160`), `docs/index.html` (strip inline `style=`), `tests/web/v2-workbench-discover.test.js`, `tests/web/design-tokens.test.js` (extend hex-sweep if present).
1. Cards (`:2067–2111`): `var(--surface)`/`var(--border)`/`var(--radius-xl)`; hover lift → `var(--shadow-lg)` (replace `:2076` hardcode); action hover `rgba(0,0,0,0.78)` (`:2110`) → token; active/focus → `var(--green-wash)` + `var(--green-ring)`. Source/year/label/release-id → `var(--font-mono)` (R6); title/artist → `var(--font-sans)`.
2. Action buttons (`:1176–1179` + `:2214–2219`): emoji → text/glyph pill buttons (`var(--radius-pill)`); save-applied → `var(--green)`. Update settings copy `:1880`. Logic untouched — only text/glyph + class; `data-act`/`data-detail-act` + delegation byte-identical.
3. Scan CTA = ink pill (`#disc-v2-refresh-btn`, `class="primary"`): `var(--ink)`/`var(--on-ink)`, never green.
4. Scan progress (`:1162–1171`): strip inline `style=`; amber `:1169` → a real `--amber` token (define in `:root`+`html.dark` if absent — no hex fallback).
5. Chips/toggles → pills; data chips 4px `var(--radius-sm)`.
6. Inline-style cleanup: every `style=` on `docs/index.html:1083–1231` → classes; keep ids stable so `discover-v2-*` tests + the `#disc-v2-section` visibility check pass.
7. Vitest: token-purity over the restyled block + touched `docs/index.html` region — no `#`-hex, no `rgba(` (R5); emoji gone from builders; `data-act` survives.

**VALIDATE:** `npm test`; Chrome both themes — five-rules pass on every Discover surface, screenshots. `playwright test discover-v2` (alone) stays green.

### T5 — Aliveness round 2 + workbench-wide audit (reduced-motion-gated)
**Files:** `app.css`, `discover.js`, `03-download-discover.js` (class-toggle hooks only), `.claude/project/web-ui.md`, `tests/web/v2-workbench-discover.test.js`.
1. Micro-feedback: short scale/opacity transition on the card's optimistic save/dismiss/snooze state-change via CSS class; under `@media (prefers-reduced-motion: no-preference)`.
2. Scan-progress motion: `#disc-v2-scan-progress-fill` width transition + spinner; card-enter stagger (`disc-v2-card-enter` class removed after one frame). Reduced-motion → `animation:none;transition:none`.
3. Place transition: crossfade via the existing `tab-entering` enter animation (`04-app-chrome.js:303`); verify it rides along the place's `switchTab` calls; no new code if so.
4. Run `ui-aliveness-audit` (report mode) over rail/grid/inspector/toolbar/health/Discover; implement dead-zone/missing-feedback fixes, reduced-motion-gated, tokens only.
5. Both-themes audit: five-rule checklist over every P2+P5 surface in light + dark; fix violations; dual screenshots for the PR.
6. Vitest: every aliveness transition/animation lives inside a `prefers-reduced-motion: no-preference` block (source-contract).

**VALIDATE:** `npm test`; Chrome both themes + reduced-motion emulation (no transitions run); screenshots.

### T6 — Tab retirement + Playwright spec + final gate (R9/R10, parity)
**Files:** `docs/index.html`, `04-app-chrome.js`, `commands.js`, `tests/e2e/v2-discover-shell.spec.ts` (new), `control-inventory.json`, `selectors-exist.spec.ts`, `v2-global-layer.spec.ts` (if it asserts `go-discover`), `tests/web/v2-commands.test.js`, `.claude/project/web-ui.md`, `CLAUDE.md`.
1. Remove `#tab-discover` (`docs/index.html:59`) + its click listener (`04-app-chrome.js:328`). KEEP `switchTab` map `discover:'discover-tab-content'` (`:295`) and the content block — the place *uses* `switchTab('discover')`. The tab strip is already `display:none` from P2; removing the Discover button completes the strip's emptiness. Document: the block survives, the tab does not.
2. ⌘K (R9): rewrite `go-discover` (`:67`) + add `find-releases` ("Discover new releases", group "Library"), both `setWorkbench(true); if(!discover.isActive()) _click('wb-disc-place')`. Remove dead `_goto('discover')`.
3. Drift-guard reconcile (both directions): remove `tab-discover` from `globalControls` (`:30`); keep the `panelControls.discover` entries (ids still exist inside the place body); add `wb-disc-place` + any T3 write ids. Update `selectors-exist` (drop `#tab-discover`; add `#wb-disc-place`). Update `v2-global-layer.spec.ts` if it asserts `go-discover` scrolls `#discover-tab-content` → assert `#wb-disc-place` activation.
4. New e2e `tests/e2e/v2-discover-shell.spec.ts` (import/copy `mockDiscoverApi` `:47`; force `ac_workbench` on; route every `/api/discover/*` + `/api/youtube/search`, #189-safe):
   - (a) `#wb-disc-place` opens the centre feed (`#disc-v2-grid` visible);
   - (b) `#tracks-sticky`/`#track-list`/`#wb-grid-head` hidden (R2);
   - (c) `#action-bar` `position==='fixed'` + document scroll (TASK-037);
   - (d) focusing a mocked card populates `#wb-inspector-body`; Esc clears (R4);
   - (e) swap back (crate) → cue grid re-shows + `#tracks-sticky` pins after scroll;
   - (f) reduced-motion emulation → no card/scan animation (R7);
   - (g) both themes via `#theme-toggle`;
   - (h) network capture: the place issues no `/api/discover/*` beyond the legacy `loadInitialState`/`runScan` (R10).
5. Vitest: `find-releases`+`go-discover` resolve, target `wb-disc-place`; `v2-commands.test.js` pins kept ids.
6. Docs: `.claude/project/web-ui.md` "Discover place" paragraph; `CLAUDE.md` Discover-v2 paragraph + workbench-place sentence (don't alter budget/snooze/timeout text). AI-asset commit with `Context:` section.
7. Final three-leg gate from root, e2e ALONE (#189): `pytest` → `npm test` → `playwright test`. Zero new e2e failures vs baseline. Open PR (base `main`): `feat(web): P5 Discover as a place — rail place + centre-pane view + theme audit + aliveness r2 (AutoCue 2.0)`.

**VALIDATE:** three legs green; new spec green both themes; existing `discover-v2.spec.ts` + `discover-v2-*.test.js` + `test_discover_*.py` untouched-green (no-behaviour-change proof); screenshots; PR open.

## Full-suite validation (per merge AND final)
```bash
pytest                                   # Discover suite untouched-green = zero backend drift
npm test                                 # Vitest incl. new v2-workbench-discover.test.js
cd tests/e2e && npx playwright test      # ALONE — #189; new v2-discover-shell.spec.ts + reconciled control-inventory
```

## Risks & mitigations
- **Keyboard scoping / overlay containing-block** — reuse `switchTab('discover')` so `#discover-tab-content`'s display semantics (the `offsetParent` check) are unchanged; never relocate `#disc-v2-grid`. T6 e2e asserts the keyboard map works in the place.
- **Inspector dual-purpose** — `_mode` flag + `initInspector` early-return when 'release'; `clearInspector()` resets on exit. T3 Vitest pins it.
- **Two places sharing the centre** — Discover/Duplicates mutually exclusive; T2 wires cross-deactivation in both `activate()`s + every rail/crate exit; `_renderCrates` ORs both.
- **JSDOM layout blind spot** — centre swap, `#action-bar` fixed, document-scroll, sticky-pin-after-round-trip verifiable ONLY in Playwright; spec (c)/(e) mandatory.
- **Restyle drift** — token-only edits, ids stable, both-themes screenshots per checkpoint; existing `discover-v2-*` tests + `discover-v2.spec.ts` as the layout regression guard (kept green untouched).
- **Enter/detail-panel divergence** — single `isActive()`-keyed guard at top of `_openDetailPanel`; flag-off legacy path still gets the slide-in.
- **Worktree vs main drift** — line numbers verified against `main` `b75d912`; T1 re-verifies symbols on the fresh branch.

## Rollback
Each task is an independent green merge; the branch lands as one PR. Rollback = revert the merge commit: no backend/schema/localStorage-migration changes (only reads of `ac_workbench` + existing `ac_discover_filters`). The legacy `#tab-discover` + tab-button wiring restore wholesale (control-inventory reverts in the same commit). Mid-branch, T6 is the only task removing user-facing surface — reverting T6 alone restores the Discover tab button while leaving the place (additive, flag-gated).

## Out of scope
- Any Discover **behaviour** change: feeders, ranker, scan orchestrator, the 60-request budget cap (artist=20/label=15/novelty=10), `{1w/1m/3m}` snooze set, the 120s timeout, scan-supersede poll, filter semantics, YouTube-preview heuristic, export/import — restyle only; do not contradict `discover-v2.md`.
- New `/api/discover/*` endpoints or schema; any backend edit.
- Retiring the legacy slide-in detail panel for the flag-off path (kept; place re-routes via a guard).
- XML/Pages-mode rendering of Discover (program decision #5 — shell is local-mode only).
- `AUTOCUE_LLM` composer (P6); deferred intelligence-keyed crate counts (P2 T5).
